"""R15-1 / R16-6 / R16-7: a SIGTERM / SIGHUP leaves nothing; a SIGKILL takes the ffmpeg children — and grandchildren — with it.

The verifier's race harness (SIGTERM as soon as a session folder holds a
``.wav``) left the folder in 20 of 40 runs under load and 6 of 20 idle:
workers kept staging names and temp files during the cleanup, the cleanup
reset the session folder so a worker made a new one, and the ``ffmpeg``
children (``-y``) re-created ``tmp*.wav`` and outlived the scan — after
SIGKILL too.

The end-to-end tests here are deterministic: the child process forces the
dangerous state with synchronisation, not timing — a fixed number of
workers each *inside* a tracked child that keeps rewriting its output in
the session folder (an ``ffmpeg -y`` stand-in, and real ``ffmpeg`` where it
is installed), plus threads that keep staging names and making temp files —
and only then reports READY; the signal is sent after READY. Each case is
repeated. ``scripts/stress_shutdown.py`` repeats the real CLI scan (100 runs
by default, 20 in CI).
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from deepfake_lens import native_path, shutdown
from deepfake_lens.checks import CheckSkipped, run_check
from deepfake_lens.result_types import CoverageStatus

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parent
CHILD_TIMEOUT_SECONDS = 120
# Repetitions of each deterministic end-to-end case.
REPEATS = 5
# Workers held inside a running child when the signal is sent (the scan's --workers 2).
WORKERS = 2
# How long a reaped/killed child may take to disappear from /proc.
GONE_WITHIN_SECONDS = 5.0
# After the process ended: how long the folder must stay absent (an orphaned
# ``ffmpeg -y`` used to re-create its output within milliseconds).
STAYS_GONE_SECONDS = 0.5

# How long a thread keeps filling the folder during a cleanup (well inside
# native_path.CLEANUP_ATTEMPTS x CLEANUP_RETRY_SECONDS = 1 s).
REFILL_SECONDS = 0.3

# An ``ffmpeg -y`` stand-in: rewrites its output in the session folder until killed.
REWRITER = "import sys, time\nwhile True:\n    with open(sys.argv[1], 'wb') as h:\n        h.write(b'RIFF' * 256)\n    time.sleep(0.001)\n"

CHILD_SCRIPT = textwrap.dedent(
    """
    import json, os, sys, tempfile, threading, time
    from pathlib import Path
    from deepfake_lens import native_path, shutdown
    from deepfake_lens.native_path import native_safe_path

    base, workers, kind = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    REWRITER = sys.argv[4]
    native_path.install_cleanup_handlers()  # what the CLI does at start
    source = base / "증거.mp4"
    source.write_bytes(b"x" * 64)
    outs = []
    lock = threading.Lock()

    def child_argv(out):
        if kind == "ffmpeg":
            return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi",
                    "-i", "sine=frequency=440", "-flush_packets", "1", "-y", out]
        return [sys.executable, "-c", REWRITER, out]

    def worker():
        with native_safe_path(source):
            fd, out = tempfile.mkstemp(suffix=".wav", dir=native_path.scratch_dir())
            os.close(fd)
            with lock:
                outs.append(out)
            try:
                shutdown.run_child(child_argv(out), capture_output=True)
            except shutdown.ShuttingDown:
                pass

    def hammer():
        # The scan's other workers: keep staging names and making temp files.
        while True:
            try:
                with native_safe_path(source):
                    fd, out = tempfile.mkstemp(suffix=".wav", dir=native_path.scratch_dir())
                    os.close(fd)
            except (shutdown.ShuttingDown, OSError):
                time.sleep(0.0005)

    for _ in range(workers):
        threading.Thread(target=worker, daemon=True).start()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        with lock:
            ready = len(outs) == workers and all(os.path.getsize(o) > 0 for o in outs)
        if ready and len(shutdown.running_children()) == workers:
            break
        time.sleep(0.005)
    else:
        sys.exit("children never ran")
    for _ in range(2):
        threading.Thread(target=hammer, daemon=True).start()
    time.sleep(0.05)  # the hammers are staging when the signal lands
    print(json.dumps({"folder": native_path.session_dir(), "children": shutdown.running_children()}), flush=True)
    while True:
        time.sleep(0.05)
    """
)

KILLED_PARENT_SCRIPT = textwrap.dedent(
    """
    import json, sys, tempfile, threading, time
    from deepfake_lens import native_path, shutdown

    REWRITER = sys.argv[1]
    out = tempfile.mkstemp(suffix=".wav", dir=native_path.scratch_dir())[1]
    threading.Thread(target=shutdown.run_child, args=([sys.executable, "-c", REWRITER, out],), daemon=True).start()
    while not shutdown.running_children():
        time.sleep(0.005)
    print(json.dumps({"children": shutdown.running_children(), "out": out}), flush=True)
    while True:
        time.sleep(0.05)
    """
)

def child_env(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update({"HOME": str(home), "DEEPFAKE_LENS_LOG_DIR": str(home / "logs"), "PYTHONPATH": str(REPO) + os.pathsep + env.get("PYTHONPATH", "")})
    env.pop("DEEPFAKE_LENS_REPORT_KEY", None)
    return env


def process_alive(pid: int) -> bool:
    """True while ``pid`` runs (a zombie, or no such process, is not alive)."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            state = handle.read().rsplit(b")", 1)[-1].split()[0]
        return state not in (b"Z", b"X")
    except OSError:
        if os.path.isdir("/proc/self"):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def wait_gone(pids: list[int], seconds: float = GONE_WITHIN_SECONDS) -> list[int]:
    deadline = time.monotonic() + seconds
    alive = [pid for pid in pids if process_alive(pid)]
    while alive and time.monotonic() < deadline:
        time.sleep(0.02)
        alive = [pid for pid in alive if process_alive(pid)]
    return alive


class _IsolatedSession:
    """A private session folder under ``base`` and a fresh "종료 중" flag (the test process's own are left alone)."""

    def __init__(self, base: Path) -> None:
        self.patches: list[Any] = [
            mock.patch.object(native_path, "_SESSION_DIR", None),
            mock.patch.object(native_path, "_SCRATCH_FALLBACK", None),
            mock.patch.object(native_path, "_SESSION_DIRS", []),
            mock.patch.object(native_path, "_base_candidates", return_value=[str(base)]),
            mock.patch.object(native_path, "install_cleanup_handlers"),
            mock.patch.object(shutdown, "_SHUTTING_DOWN", False),
        ]

    def __enter__(self) -> None:
        for patch in self.patches:
            patch.start()

    def __exit__(self, *exc: Any) -> None:
        for patch in reversed(self.patches):
            patch.stop()


class ShuttingDownRefusesTest(unittest.TestCase):
    """R15-1: once the flag is set no folder, staged name or child is made — the check is skipped "종료 중"."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.source = self.base / "증거.mp4"
        self.source.write_bytes(b"x" * 64)

    def test_every_creating_step_is_refused_and_recorded_as_skipped(self) -> None:
        with _IsolatedSession(self.base):
            folder = native_path.session_dir()
            with mock.patch.object(shutdown, "_SHUTTING_DOWN", True):
                for name, step in (
                    ("session_dir", native_path.session_dir),
                    ("scratch_dir", native_path.scratch_dir),
                    ("stage", lambda: native_path.native_safe_path(self.source).__enter__()),
                    ("run_child", lambda: shutdown.run_child([sys.executable, "-c", "pass"])),
                ):
                    with self.subTest(step=name):
                        with self.assertRaises(shutdown.ShuttingDown) as caught:
                            step()
                        self.assertIsInstance(caught.exception, CheckSkipped)
                        value, entry = run_check(name, step)
                        self.assertIsNone(value)
                        self.assertEqual((entry.status, entry.reason), (CoverageStatus.SKIPPED, "종료 중"))
                self.assertEqual(os.listdir(folder), [])  # nothing was staged
                self.assertEqual(shutdown.running_children(), [])
                # The final cleanup does not reset the folder: nothing may make a new one.
                self.assertTrue(native_path.cleanup_session())
                self.assertEqual(native_path._SESSION_DIR, folder)
                with self.assertRaises(shutdown.ShuttingDown):
                    native_path.session_dir()
                self.assertEqual([n for n in os.listdir(self.base) if n.startswith(native_path.SESSION_PREFIX)], [])

    def test_no_cache_is_written_while_ending(self) -> None:
        from deepfake_lens.scan_cache import _write_scan_cache

        target = self.base / "cache.json"
        with mock.patch.object(shutdown, "_SHUTTING_DOWN", True):
            _write_scan_cache(target, {"k": {"x": 1}})
        self.assertFalse(target.exists())  # a row refused "종료 중" is never replayed
        with mock.patch.object(shutdown, "_SHUTTING_DOWN", False):
            _write_scan_cache(target, {"k": {"x": 1}})
        self.assertTrue(target.exists())

    def test_a_folder_refilled_during_the_cleanup_is_removed_until_gone(self) -> None:
        with _IsolatedSession(self.base):
            folder = native_path.session_dir()
            stop = threading.Event()

            def refill() -> None:
                # A step that started before the flag and finishes on its own
                # (the stagers are refused after the flag; children are killed
                # before the removal) — the retries outlast it.
                index = 0
                until = time.monotonic() + REFILL_SECONDS
                while not stop.is_set() and time.monotonic() < until:
                    try:
                        (Path(folder) / f"tmp{index}.wav").write_bytes(b"x")
                    except OSError:
                        return  # the folder is gone: nothing can be made in it any more
                    index += 1

            writer = threading.Thread(target=refill)
            writer.start()
            time.sleep(0.02)
            try:
                self.assertTrue(native_path.cleanup_session())
            finally:
                stop.set()
                writer.join()
            self.assertFalse(os.path.lexists(folder))

    def test_an_unremovable_folder_ends_the_retries_and_reports_it(self) -> None:
        with _IsolatedSession(self.base):
            folder = native_path.session_dir()
            calls: list[str] = []
            with mock.patch.object(native_path, "_remove_tree", side_effect=calls.append):
                started = time.monotonic()
                self.assertFalse(native_path.cleanup_session())
                elapsed = time.monotonic() - started
            self.assertEqual(len(calls), native_path.CLEANUP_ATTEMPTS)
            self.assertLess(elapsed, native_path.CLEANUP_ATTEMPTS * native_path.CLEANUP_RETRY_SECONDS + 5)
            self.assertTrue(native_path.cleanup_session())
            self.assertFalse(os.path.lexists(folder))

    def test_begin_stops_a_child_that_ignores_sigterm(self) -> None:
        stubborn = "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint('up', flush=True)\ntime.sleep(60)\n"
        with _IsolatedSession(self.base):
            outcome: list[BaseException | None] = []

            def run() -> None:
                try:
                    shutdown.run_child([sys.executable, "-c", stubborn], capture_output=True)
                    outcome.append(None)
                except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
                    outcome.append(exc)

            worker = threading.Thread(target=run)
            worker.start()
            deadline = time.monotonic() + CHILD_TIMEOUT_SECONDS
            while not shutdown.running_children() and time.monotonic() < deadline:
                time.sleep(0.005)
            pids = shutdown.running_children()
            self.assertEqual(len(pids), 1)
            time.sleep(0.3)  # the child has set SIG_IGN
            with mock.patch.object(shutdown, "CHILD_TERM_GRACE_SECONDS", 0.3):
                started = time.monotonic()
                shutdown.begin()
                elapsed = time.monotonic() - started
            worker.join(timeout=CHILD_TIMEOUT_SECONDS)
            self.assertEqual(wait_gone(pids), [])
            self.assertLess(elapsed, 0.3 + shutdown.CHILD_KILL_GRACE_SECONDS + 1.0)
            self.assertEqual(len(outcome), 1)
            self.assertIsInstance(outcome[0], shutdown.ShuttingDown)  # not a quiet killed result
            self.assertEqual(shutdown.running_children(), [])

    def test_run_child_matches_subprocess_run(self) -> None:
        done = shutdown.run_child([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"], capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (3, "out\n", "err\n"))
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            shutdown.run_child([sys.executable, "-c", "raise SystemExit(4)"], check=True)
        self.assertEqual(caught.exception.returncode, 4)
        with self.assertRaises(subprocess.TimeoutExpired):
            shutdown.run_child([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
        with self.assertRaises(FileNotFoundError):
            shutdown.run_child(["deepfake-lens-no-such-program"])
        self.assertEqual(shutdown.running_children(), [])

    @unittest.skipUnless(sys.platform.startswith("linux"), "PR_SET_PDEATHSIG is Linux")
    def test_linux_children_carry_the_parent_death_signal(self) -> None:
        # R16-7 (round 16): this test asserted the R15-1 design — the program
        # exec-ed in place as our direct child with PR_SET_PDEATHSIG SIGKILL,
        # which left a wrapper's grandchild running after a SIGKILL. Now the
        # program runs under the supervisor (our direct child, session and
        # group leader, subreaper, PDEATHSIG SIGTERM) in a process group of
        # its own, bound to the supervisor with PDEATHSIG SIGKILL.
        probe = textwrap.dedent(
            """
            import ctypes, json, os, signal
            libc = ctypes.CDLL(None)
            def prctl_get(option):
                value = ctypes.c_int()
                libc.prctl(option, ctypes.byref(value), 0, 0, 0)
                return value.value
            supervisor = os.getppid()
            sup_status = dict(line.split(":\\t", 1) for line in open(f"/proc/{supervisor}/status").read().splitlines() if ":\\t" in line)
            print(json.dumps({
                "pid": os.getpid(), "pgid": os.getpgid(0), "pdeathsig": prctl_get(2), "supervisor": supervisor, "supervisor_ppid": int(sup_status["PPid"]),
                "supervisor_sid": os.getsid(supervisor), "supervisor_pgid": os.getpgid(supervisor),
            }))
            """
        )
        done = shutdown.run_child([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
        fields = json.loads(done.stdout)
        self.assertEqual(fields["supervisor_ppid"], os.getpid())  # the supervisor is our direct child
        self.assertEqual((fields["supervisor_sid"], fields["supervisor_pgid"]), (fields["supervisor"], fields["supervisor"]))  # new session
        self.assertEqual(fields["pgid"], fields["pid"])  # the program leads a group of its own …
        self.assertNotEqual(fields["pgid"], os.getpgid(0))  # … not ours
        self.assertEqual(fields["pdeathsig"], int(signal.SIGKILL))  # bound to the supervisor
        # SIGPIPE back at its default in the program (grep: a Python program ignores it itself at start-up).
        status = shutdown.run_child(["grep", "-E", "^SigIgn:", "/proc/self/status"], capture_output=True, text=True, check=True)
        self.assertEqual(int(status.stdout.split(":\t")[1], 16) & (1 << (signal.SIGPIPE - 1)), 0)
        self.assertIn("PR_SET_CHILD_SUBREAPER", shutdown.__doc__ or "")
        self.assertIn(f"prctl({shutdown._PR_SET_CHILD_SUBREAPER}, 1)", shutdown.CHILD_SUPERVISOR)
        self.assertIn(f"prctl({shutdown._PR_SET_PDEATHSIG}, int(signal.SIGTERM))", shutdown.CHILD_SUPERVISOR)

    def test_a_child_never_gets_the_terminal_as_stdin(self) -> None:
        """R16-6: stdin is /dev/null unless given; every ffmpeg call carries -nostdin."""
        from deepfake_lens.native_stderr import FFMPEG_QUIET_ARGS

        done = shutdown.run_child([sys.executable, "-c", "import os, sys; print(os.path.realpath('/proc/self/fd/0') if os.path.exists('/proc/self/fd/0') else sys.stdin.isatty())"], capture_output=True, text=True, check=True)
        self.assertIn(done.stdout.strip(), ("/dev/null", "False"))
        self.assertIn("-nostdin", FFMPEG_QUIET_ARGS)


@unittest.skipIf(os.name == "nt", "POSIX signals")
class SignalLeavesNothingTest(unittest.TestCase):
    """R15-1: SIGTERM / SIGHUP with workers inside running children and stagers busy — nothing is left, every child is gone."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def _run(self, signum: int, kind: str, tag: str) -> None:
        base = self.root / tag
        base.mkdir()
        env = {**child_env(self.root / f"home_{tag}"), native_path.NATIVE_TMP_ENV: str(base), "TMPDIR": str(base)}
        argv = shutdown.with_default_signals([sys.executable, "-c", CHILD_SCRIPT, str(base), str(WORKERS), kind, REWRITER])
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(base))
        try:
            assert proc.stdout is not None and proc.stderr is not None
            line = proc.stdout.readline().decode("utf-8")
            if not line:
                self.fail(f"child ended before READY: {proc.wait(timeout=CHILD_TIMEOUT_SECONDS)} {proc.stderr.read()[-800:]!r}")
            ready = json.loads(line)
            self.assertEqual(len(ready["children"]), WORKERS)
            self.assertTrue(all(process_alive(pid) for pid in ready["children"]))
            proc.send_signal(signum)
            code = proc.wait(timeout=CHILD_TIMEOUT_SECONDS)
            stderr = proc.stderr.read().decode("utf-8", "replace")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
        self.assertEqual(code, -signum, stderr[-800:])  # the default action still ended it
        self.assertEqual(wait_gone(ready["children"]), [])
        self.assertEqual([n for n in os.listdir(base) if n.startswith(native_path.SESSION_PREFIX)], [], stderr[-800:])
        time.sleep(STAYS_GONE_SECONDS)
        self.assertEqual(sorted(os.listdir(base)), ["증거.mp4"])  # nothing re-created afterwards

    def test_sigterm_and_sighup_with_rewriting_children(self) -> None:
        for signum in (signal.SIGTERM, signal.SIGHUP):
            for repeat in range(REPEATS):
                with self.subTest(signal=signum.name, repeat=repeat):
                    self._run(signum, "rewriter", f"{signum.name}_{repeat}")

    def test_sigterm_with_real_ffmpeg_children(self) -> None:
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg 없음")
        for repeat in range(REPEATS):
            with self.subTest(repeat=repeat):
                self._run(signal.SIGTERM, "ffmpeg", f"ffmpeg_{repeat}")


@unittest.skipUnless(sys.platform.startswith("linux"), "PR_SET_PDEATHSIG is Linux")
class KilledParentTakesItsChildrenTest(unittest.TestCase):
    """R15-1: SIGKILL runs no handler at all — the children die with the parent (PR_SET_PDEATHSIG)."""

    def test_sigkill_of_the_scan_ends_its_children(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            env = {**child_env(base / "home"), native_path.NATIVE_TMP_ENV: str(base), "TMPDIR": str(base)}
            proc = subprocess.Popen([sys.executable, "-c", KILLED_PARENT_SCRIPT, REWRITER], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            try:
                assert proc.stdout is not None
                line = proc.stdout.readline().decode("utf-8")
                if not line:
                    proc.wait(timeout=CHILD_TIMEOUT_SECONDS)
                    self.fail(f"child ended before READY: {proc.stderr.read()[-800:] if proc.stderr else b''!r}")
                ready = json.loads(line)
                self.assertTrue(all(process_alive(pid) for pid in ready["children"]))
                proc.kill()
                proc.wait(timeout=CHILD_TIMEOUT_SECONDS)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                for stream in (proc.stdout, proc.stderr):
                    if stream is not None:
                        stream.close()
            self.assertEqual(wait_gone(ready["children"]), [])
            out = Path(ready["out"])
            before = out.stat().st_mtime_ns if out.exists() else None
            time.sleep(STAYS_GONE_SECONDS)
            self.assertEqual(out.stat().st_mtime_ns if out.exists() else None, before)  # nobody rewrites it


# R16-7: a wrapper "ffmpeg" (sh, not exec) whose own child does the work — the
# grandchild the R15-1 design left running when the wrapper was killed.
WRAPPER_FFMPEG = '#!/bin/sh\n"$DFL_PYTHON" -c "$DFL_REWRITER" "$1"\necho wrapper-done\n'
# R16-7: runs a wrapper child, prints the grandchild's pid, then waits (SIGKILLed by the test).
WRAPPED_PARENT_SCRIPT = textwrap.dedent(
    """
    import json, os, sys, threading, time
    from deepfake_lens import shutdown

    out = sys.argv[1]

    def descendants(pid):
        found, stack = [], [pid]
        while stack:
            current = stack.pop()
            try:
                tasks = os.listdir(f"/proc/{current}/task")
            except OSError:
                continue
            for task in tasks:
                try:
                    children = [int(c) for c in open(f"/proc/{current}/task/{task}/children").read().split()]
                except OSError:
                    continue
                found += children
                stack += children
        return found

    threading.Thread(target=shutdown.run_child, args=(["ffmpeg", out],), kwargs={"capture_output": True}, daemon=True).start()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        workers = [pid for pid in descendants(os.getpid()) if b"DFL_REWRITER_MARK" in open(f"/proc/{pid}/cmdline", "rb").read()]
        if workers and os.path.exists(out) and os.path.getsize(out):
            break
        time.sleep(0.01)
    else:
        sys.exit("grandchild never ran")
    print(json.dumps({"grandchildren": workers, "children": shutdown.running_children()}), flush=True)
    while True:
        time.sleep(0.05)
    """
)


@unittest.skipUnless(sys.platform.startswith("linux"), "PR_SET_PDEATHSIG is Linux")
class WrapperGrandchildTest(unittest.TestCase):
    """R16-7 (round 16): a wrapper script's grandchild ends with the scan — SIGKILL, shutdown, timeout."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.mark = f"{os.getpid()}-{self.id()}"  # this test's grandchildren only (suites may run side by side)
        bindir = self.root / "bin"
        bindir.mkdir()
        wrapper = bindir / "ffmpeg"
        wrapper.write_text(WRAPPER_FFMPEG, encoding="utf-8")
        wrapper.chmod(0o755)
        self.env = {
            **child_env(self.root / "home"),
            "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", ""),
            "DFL_PYTHON": sys.executable,
            "DFL_REWRITER": f"# DFL_REWRITER_MARK {self.mark}\n" + REWRITER,
        }
        self.out = self.root / "out.wav"

    def _assert_stays_unwritten(self) -> None:
        before = self.out.stat().st_mtime_ns if self.out.exists() else None
        time.sleep(STAYS_GONE_SECONDS)
        self.assertEqual(self.out.stat().st_mtime_ns if self.out.exists() else None, before)  # nobody rewrites it

    def test_sigkill_of_the_parent_ends_the_grandchild(self) -> None:
        for repeat in range(REPEATS):
            with self.subTest(repeat=repeat):
                self.out.unlink(missing_ok=True)
                proc = subprocess.Popen([sys.executable, "-c", WRAPPED_PARENT_SCRIPT, str(self.out)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
                try:
                    assert proc.stdout is not None
                    line = proc.stdout.readline().decode("utf-8")
                    if not line:
                        proc.wait(timeout=CHILD_TIMEOUT_SECONDS)
                        self.fail(f"child ended before READY: {proc.stderr.read()[-800:] if proc.stderr else b''!r}")
                    ready = json.loads(line)
                    self.assertTrue(ready["grandchildren"] and all(process_alive(pid) for pid in ready["grandchildren"]))
                    proc.kill()
                    proc.wait(timeout=CHILD_TIMEOUT_SECONDS)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait()
                    for stream in (proc.stdout, proc.stderr):
                        if stream is not None:
                            stream.close()
                self.assertEqual(wait_gone(ready["children"] + ready["grandchildren"]), [])
                self._assert_stays_unwritten()

    def _start_in_thread(self, **kwargs: Any) -> tuple[threading.Thread, list[BaseException | None]]:
        outcome: list[BaseException | None] = []

        def run() -> None:
            try:
                shutdown.run_child(["ffmpeg", str(self.out)], capture_output=True, **kwargs)
                outcome.append(None)
            except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
                outcome.append(exc)

        with mock.patch.dict(os.environ, self.env):
            worker = threading.Thread(target=run)
            worker.start()
            deadline = time.monotonic() + CHILD_TIMEOUT_SECONDS
            while not (self.out.exists() and self.out.stat().st_size) and time.monotonic() < deadline:
                time.sleep(0.01)
        return worker, outcome

    def _grandchildren(self) -> list[int]:
        found: list[int] = []
        for pid in os.listdir("/proc"):
            if pid.isdigit():
                try:
                    if f"DFL_REWRITER_MARK {self.mark}".encode() in Path(f"/proc/{pid}/cmdline").read_bytes():
                        found.append(int(pid))
                except OSError:
                    continue
        return [pid for pid in found if process_alive(pid)]

    def test_shutdown_stops_the_whole_group(self) -> None:
        with _IsolatedSession(self.root):
            worker, outcome = self._start_in_thread()
            grandchildren = self._grandchildren()
            self.assertTrue(grandchildren)
            shutdown.begin()
            worker.join(timeout=CHILD_TIMEOUT_SECONDS)
            self.assertEqual(wait_gone(grandchildren), [])
            self.assertIsInstance(outcome[0], shutdown.ShuttingDown)
            self._assert_stays_unwritten()

    def test_a_timeout_stops_the_whole_group(self) -> None:
        with mock.patch.dict(os.environ, self.env):
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                shutdown.run_child(["ffmpeg", str(self.out)], capture_output=True, timeout=1.0)
            elapsed = time.monotonic() - started
        self.assertEqual(self._grandchildren(), [])
        self.assertLess(elapsed, 1.0 + shutdown.CHILD_TERM_GRACE_SECONDS + shutdown.CHILD_KILL_GRACE_SECONDS + 2.0)
        self._assert_stays_unwritten()

    def test_a_normal_end_reports_the_status_and_leaves_nothing(self) -> None:
        script = "sleep 30 &\necho started\nexit 5\n"  # leaves a background job behind
        done = shutdown.run_child(["sh", "-c", script], capture_output=True, text=True, timeout=CHILD_TIMEOUT_SECONDS)
        self.assertEqual((done.returncode, done.stdout), (5, "started\n"))
        killed = shutdown.run_child(["sh", "-c", "kill -TERM $$"], capture_output=True)
        self.assertEqual(killed.returncode, -signal.SIGTERM)  # a signal death is reported as one


@unittest.skipUnless(sys.platform.startswith("linux"), "PR_SET_PDEATHSIG is Linux")
class TerminalSurvivesTest(unittest.TestCase):
    """R16-6 (round 16): a child that would put its stdin terminal in raw mode cannot — the terminal keeps echo."""

    def test_the_terminal_keeps_echo(self) -> None:
        import pty
        import termios

        child_code = textwrap.dedent(
            """
            import sys
            from deepfake_lens import shutdown
            raw = "import sys, tty\\ntry:\\n    tty.setraw(0)\\nexcept Exception:\\n    sys.exit(9)\\n"
            done = shutdown.run_child([sys.executable, "-c", raw])
            print("RC", done.returncode, flush=True)
            """
        )
        pid, fd = pty.fork()
        if pid == 0:  # pragma: no cover - the forked child
            try:
                os.environ.update(child_env(Path(tempfile.gettempdir())))
                os.execv(sys.executable, [sys.executable, "-c", child_code])
            finally:
                os._exit(127)
        output = b""
        try:
            deadline = time.monotonic() + CHILD_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
                if b"RC" in output and output.endswith(b"\n"):
                    break
            lflag = termios.tcgetattr(fd)[3]
        finally:
            os.waitpid(pid, 0)
            os.close(fd)
        self.assertIn(b"RC 9", output)  # stdin was not a terminal: setraw failed
        self.assertEqual(lflag & (termios.ECHO | termios.ICANON), termios.ECHO | termios.ICANON)


# R15-1: every child process of the package is started (and so tracked) by
# shutdown.run_child — the only module allowed to call subprocess.
SPAWNING_CALLS = {
    ("subprocess", "run"), ("subprocess", "Popen"), ("subprocess", "call"), ("subprocess", "check_call"),
    ("subprocess", "check_output"), ("subprocess", "getoutput"), ("subprocess", "getstatusoutput"),
    ("os", "system"), ("os", "popen"), ("os", "posix_spawn"), ("os", "posix_spawnp"), ("os", "fork"), ("os", "forkpty"),
    ("os", "spawnl"), ("os", "spawnle"), ("os", "spawnlp"), ("os", "spawnlpe"), ("os", "spawnv"), ("os", "spawnve"),
    ("os", "spawnvp"), ("os", "spawnvpe"), ("os", "execv"), ("os", "execve"), ("os", "execvp"), ("os", "execl"),
    ("os", "startfile"),
}
SPAWN_ALLOWED = {"shutdown.py"}


def spawning_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """``(line, "module.name")`` of every process-spawning call, aliases (``import subprocess as sp``, ``from os import system``) resolved."""
    modules: dict[str, str] = {}
    names: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ("subprocess", "os"):
                    modules[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in ("subprocess", "os"):
            for alias in node.names:
                if (node.module, alias.name) in SPAWNING_CALLS:
                    names[alias.asname or alias.name] = (node.module, alias.name)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in modules:
            key = (modules[func.value.id], func.attr)
        elif isinstance(func, ast.Name) and func.id in names:
            key = names[func.id]
        else:
            continue
        if key in SPAWNING_CALLS:
            found.append((node.lineno, ".".join(key)))
    return found


class EveryChildIsTrackedMetaTest(unittest.TestCase):
    def test_no_module_but_shutdown_spawns_a_process(self) -> None:
        offenders: list[str] = []
        for source in sorted(PACKAGE.rglob("*.py")):
            relative = source.relative_to(PACKAGE)
            if "tests" in relative.parts or relative.as_posix() in SPAWN_ALLOWED:
                continue
            for line, call in spawning_calls(ast.parse(source.read_text(encoding="utf-8"))):
                offenders.append(f"{relative.as_posix()}:{line} {call}")
        self.assertEqual(offenders, [])
        users = [p.name for p in sorted(PACKAGE.glob("*.py")) if "run_child(" in p.read_text(encoding="utf-8") and p.name != "shutdown.py"]
        self.assertEqual(sorted(users), ["audio.py", "lipsync.py", "video.py", "video_analysis.py"])  # non-vacuous

    def test_the_visitor_flags_aliases(self) -> None:
        tree = ast.parse(
            "import subprocess as sp\nfrom os import system as s\nimport os\n"
            "sp.run(['x'])\ns('x')\nos.posix_spawn('x', ['x'], {})\nos.path.join('a')\n"
        )
        self.assertEqual(spawning_calls(tree), [(4, "subprocess.run"), (5, "os.system"), (6, "os.posix_spawn")])


@unittest.skipIf(os.name == "nt", "POSIX signals")
class HarnessChildrenStartWithDefaultSignalsTest(unittest.TestCase):
    """R15-4 (round 15): a harness that inherited SIGINT/SIGHUP/SIGTERM as ignored starts its children with the default.

    The R14-1 … R14-5 commits failed their own SIGINT test when the suite ran
    with SIGINT ignored (a background job): the child inherited SIG_IGN.
    """

    PROBE = (
        "import json, signal\n"
        "def shown(handler):\n"
        "    return 'SIG_IGN' if handler == signal.SIG_IGN else 'SIG_DFL' if handler == signal.SIG_DFL else getattr(handler, '__name__', repr(handler))\n"
        "print(json.dumps({name: shown(signal.getsignal(getattr(signal, name))) for name in ('SIGINT', 'SIGTERM', 'SIGHUP')}))\n"
    )

    def _child_dispositions(self, harness: str) -> dict[str, str]:
        ignored = {"SIGINT": "SIG_IGN", "SIGTERM": "SIG_IGN", "SIGHUP": "SIG_IGN"}
        script = harness + "\nimport subprocess, sys\n" + f"sys.stdout.write(subprocess.run([sys.executable, '-c', {self.PROBE!r}], capture_output=True, text=True).stdout)\n"
        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run(
                shutdown.with_signals(ignored, [sys.executable, "-c", script]), capture_output=True, text=True, env=child_env(Path(tmp)), timeout=CHILD_TIMEOUT_SECONDS
            )
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_without_the_harness_the_ignored_signals_are_inherited(self) -> None:
        seen = self._child_dispositions("pass")
        self.assertEqual(seen, {"SIGINT": "SIG_IGN", "SIGTERM": "SIG_IGN", "SIGHUP": "SIG_IGN"})  # the R14 failure condition

    def test_the_test_package_resets_them_for_children(self) -> None:
        seen = self._child_dispositions("import deepfake_lens.tests")
        # SIGINT at its default: a Python child installs its KeyboardInterrupt handler.
        self.assertEqual(seen, {"SIGINT": "default_int_handler", "SIGTERM": "SIG_DFL", "SIGHUP": "SIG_DFL"})

    def test_the_harness_itself_still_ignores_them(self) -> None:
        script = (
            "import os, signal, time\nimport deepfake_lens.tests\n"
            "os.kill(os.getpid(), signal.SIGHUP); os.kill(os.getpid(), signal.SIGINT); os.kill(os.getpid(), signal.SIGTERM)\n"
            "time.sleep(0.2); print('alive')\n"
        )
        ignored = {"SIGINT": "SIG_IGN", "SIGTERM": "SIG_IGN", "SIGHUP": "SIG_IGN"}
        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run(shutdown.with_signals(ignored, [sys.executable, "-c", script]), capture_output=True, text=True, env=child_env(Path(tmp)), timeout=CHILD_TIMEOUT_SECONDS)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "alive"), done.stderr[-800:])

    def test_qa_harness_and_ci_reset_them(self) -> None:
        qa = ast.parse((REPO / "scripts" / "qa_phase0.py").read_text(encoding="utf-8"))
        main = next(node for node in qa.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        calls = [node.func.id for node in ast.walk(main) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
        self.assertIn("children_start_with_default_signals", calls)
        workflow = (REPO / ".github" / "workflows" / "deepfake-lens.yml").read_text(encoding="utf-8")
        runs = [line.strip() for line in workflow.splitlines() if "unittest discover" in line or "qa_phase0.py --log-dir" in line]
        self.assertTrue(runs)
        self.assertEqual([line for line in runs if "with_default_signals.py --" not in line], [])

    def test_the_history_document_names_real_commits(self) -> None:
        import re
        import shutil as _shutil

        text = (REPO / "docs" / "KNOWN-HISTORICAL-ISSUES.md").read_text(encoding="utf-8")
        subjects = re.findall(r"`((?:fix|test)\([^`]*\(R14-[^`]*\))`", text)
        self.assertEqual(len(subjects), 5)  # four affected commits and the fix
        git = _shutil.which("git")
        if git is None:
            self.skipTest("git not available")
        inside = subprocess.run([git, "rev-parse", "--is-inside-work-tree"], cwd=REPO, capture_output=True, text=True, check=False)
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            self.skipTest("not a git work tree")
        for subject in subjects:
            with self.subTest(subject=subject[:60]):
                found = subprocess.run([git, "log", "HEAD", "--fixed-strings", f"--grep={subject}", "--format=%s"], cwd=REPO, capture_output=True, text=True, check=False)
                self.assertIn(subject, found.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
