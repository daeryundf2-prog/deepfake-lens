"""R16-13 / R16-15 (round 16): an interrupted CLI and a harness command that cannot start end in Korean, without a traceback.

R16-13: Ctrl-C (SIGINT with Python's default handler → KeyboardInterrupt)
ended ``deepfake-lens`` with an English ``KeyboardInterrupt`` traceback. Now
the session folder and the tracked children are cleaned up first, then
"중단됨(사용자 요청)" goes to stderr and the exit code is 130.

R16-15: ``scripts/with_default_signals.py`` with a program that does not
exist printed an English ``FileNotFoundError`` traceback (exit 1); now a
Korean error and the shell's 127 (126 for a file that cannot be executed).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from deepfake_lens import cli
from deepfake_lens.shutdown import with_default_signals

REPO = Path(__file__).resolve().parents[2]
CHILD_TIMEOUT_SECONDS = 120
GONE_WITHIN_SECONDS = 5.0

# A CLI run that is inside a scan when the test presses Ctrl-C: a session
# folder with a temp file and a tracked child exist, then the scan waits.
INTERRUPTED_SCAN = textwrap.dedent(
    """
    import json, sys, tempfile, threading, time
    from deepfake_lens import cli, native_path, shutdown

    def worker():
        try:
            shutdown.run_child([sys.executable, "-c", "import time; time.sleep(60)"])
        except shutdown.ShuttingDown:
            pass  # what a scan check records as skipped "종료 중"

    def slow_scan(*args, **kwargs):
        tempfile.mkstemp(suffix=".wav", dir=native_path.scratch_dir())
        threading.Thread(target=worker, daemon=True).start()
        while not shutdown.running_children():
            time.sleep(0.005)
        print(json.dumps({"folder": native_path.session_dir(), "children": shutdown.running_children()}), flush=True)
        time.sleep(60)

    cli.scan_folder_run = slow_scan
    raise SystemExit(cli.main(["scan", sys.argv[1], "--format", "json"]))
    """
)


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            return handle.read().rsplit(b")", 1)[-1].split()[0] not in (b"Z", b"X")
    except OSError:
        return False


class InterruptedCliTest(unittest.TestCase):
    def test_keyboard_interrupt_is_a_korean_line_and_exit_130_after_the_cleanup(self) -> None:
        previous = signal.getsignal(signal.SIGINT)
        self.addCleanup(signal.signal, signal.SIGINT, previous)
        calls: list[str] = []
        err = io.StringIO()
        with mock.patch.object(cli, "_main", side_effect=KeyboardInterrupt), mock.patch(
            "deepfake_lens.native_path.shutdown_session", side_effect=lambda: calls.append("cleanup") or True
        ), contextlib.redirect_stderr(err):
            code = cli.main(["scan", "."])
        self.assertEqual(code, cli.INTERRUPTED_EXIT)
        self.assertEqual(code, 130)
        self.assertEqual(calls, ["cleanup"])
        self.assertTrue(err.getvalue().startswith(cli.INTERRUPTED_MESSAGE), err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        self.assertNotIn("KeyboardInterrupt", err.getvalue())

    def test_python_dash_m_uses_the_same_wording(self) -> None:
        source = (REPO / "deepfake_lens" / "__main__.py").read_text(encoding="utf-8")
        self.assertIn(json.dumps(cli.INTERRUPTED_MESSAGE, ensure_ascii=False), source)
        self.assertIn(f"SystemExit({cli.INTERRUPTED_EXIT})", source)

    @unittest.skipUnless(sys.platform.startswith("linux"), "POSIX signals")
    def test_ctrl_c_during_a_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            case = root / "case"
            case.mkdir()
            (case / "메모.txt").write_text("회의록", encoding="utf-8")
            base = root / "tmp"
            base.mkdir()
            env = {
                **os.environ, "TMPDIR": str(base), "HOME": str(root / "home"), "DEEPFAKE_LENS_LOG_DIR": str(root / "logs"),
                "PYTHONPATH": str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", ""),
            }
            env.pop("DEEPFAKE_LENS_TMPDIR", None)
            env.pop("DEEPFAKE_LENS_NATIVE_TMPDIR", None)
            proc = subprocess.Popen(with_default_signals([sys.executable, "-c", INTERRUPTED_SCAN, str(case)]), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            try:
                assert proc.stdout is not None and proc.stderr is not None
                line = proc.stdout.readline().decode("utf-8")
                if not line:
                    self.fail(f"child ended early: {proc.wait(timeout=CHILD_TIMEOUT_SECONDS)} {proc.stderr.read()[-800:]!r}")
                ready = json.loads(line)
                proc.send_signal(signal.SIGINT)  # what Ctrl-C sends to the foreground process
                code = proc.wait(timeout=CHILD_TIMEOUT_SECONDS)
                stderr = proc.stderr.read().decode("utf-8", "replace")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                for stream in (proc.stdout, proc.stderr):
                    if stream is not None:
                        stream.close()
            self.assertEqual(code, 130, stderr[-800:])
            self.assertIn(cli.INTERRUPTED_MESSAGE, stderr)
            self.assertNotIn("Traceback", stderr)
            self.assertNotIn("KeyboardInterrupt", stderr)
            self.assertFalse(os.path.exists(ready["folder"]))  # cleaned up before the message
            deadline = time.monotonic() + GONE_WITHIN_SECONDS
            while any(_alive(pid) for pid in ready["children"]) and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual([pid for pid in ready["children"] if _alive(pid)], [])
            self.assertEqual(os.listdir(base), [])


# R17-8 (b): the console script, interrupted while the package is importing —
# a meta-path hook presses Ctrl-C (SIGINT) as deepfake_lens.core is looked up.
IMPORT_INTERRUPTED = textwrap.dedent(
    """
    import importlib.abc, os, signal, sys, time

    class PressCtrlC(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            if name == "deepfake_lens.core":
                os.kill(os.getpid(), signal.SIGINT)
                time.sleep(30)
            return None

    sys.meta_path.insert(0, PressCtrlC())
    from deepfake_lens.cli import main
    sys.exit(main())
    """
)

# R17-8 (a): a command interrupted while another thread (a web request) is
# analysing — that thread fails because of the cleanup and logs it.
INTERRUPTED_WITH_A_REQUEST = textwrap.dedent(
    """
    import logging, sys, threading, time
    from deepfake_lens import cli, shutdown

    def request():
        while not shutdown.active():
            time.sleep(0.005)
        try:
            raise shutdown.ShuttingDown()
        except shutdown.ShuttingDown:
            logging.getLogger("deepfake_lens.core").exception("analysis failed: %s", "영상.mp4")

    def command(*args, **kwargs):
        threading.Thread(target=request).start()
        raise KeyboardInterrupt

    cli._run_command = command
    sys.exit(cli.main(["doctor"]))
    """
)


@unittest.skipIf(os.name == "nt", "POSIX signals")
class InterruptOrderTest(unittest.TestCase):
    """R17-8 (round 17): R16-13 left three gaps — an English log line and traceback before the Korean line
    (logging restored before the cleanup), a traceback for a Ctrl-C during the package import, and a
    ``batch`` that ran on through its queue after the Korean line."""

    def _env(self, root: Path) -> dict[str, str]:
        env = {
            **os.environ, "HOME": str(root / "home"), "DEEPFAKE_LENS_LOG_DIR": str(root / "logs"),
            "PYTHONPATH": str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        }
        return env

    def test_the_cleanup_runs_before_the_log_handlers_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            done = subprocess.run(
                with_default_signals([sys.executable, "-c", INTERRUPTED_WITH_A_REQUEST]),
                capture_output=True, text=True, env=self._env(root), timeout=CHILD_TIMEOUT_SECONDS,
            )
            log = (root / "logs").rglob("*.log")
            logged = "".join(path.read_text(encoding="utf-8", errors="replace") for path in log)
        self.assertEqual(done.returncode, 130, done.stderr[-800:])
        self.assertEqual(done.stderr.strip(), f"{cli.INTERRUPTED_MESSAGE} — 임시 파일과 자식 프로세스를 정리하고 종료합니다.")
        self.assertIn("analysis failed", logged)  # it went to the log file

    def test_ctrl_c_while_the_package_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            program = root / "deepfake-lens"  # the console script's name
            program.write_text(IMPORT_INTERRUPTED, encoding="utf-8")
            done = subprocess.run(
                with_default_signals([sys.executable, str(program), "doctor"]),
                capture_output=True, text=True, env=self._env(root), timeout=CHILD_TIMEOUT_SECONDS,
            )
        self.assertEqual(done.returncode, 130, done.stderr[-800:])
        self.assertEqual(done.stderr.strip(), cli.INTERRUPTED_MESSAGE)
        self.assertNotIn("Traceback", done.stderr)

    def test_the_guard_is_only_for_the_cli_and_is_released_by_main(self) -> None:
        from deepfake_lens import interrupt_guard

        with mock.patch.object(sys, "argv", ["python -m unittest"]):
            self.assertFalse(interrupt_guard.install())
        previous = signal.getsignal(signal.SIGINT)
        self.addCleanup(signal.signal, signal.SIGINT, previous)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        with mock.patch.object(sys, "argv", ["/usr/bin/deepfake-lens"]):
            self.assertTrue(interrupt_guard.install())
        interrupt_guard.release()
        self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        with mock.patch.object(sys, "argv", ["deepfake-lens"]):
            self.assertFalse(interrupt_guard.install())  # R14-1: an inherited SIG_IGN stays

    def test_an_interrupted_batch_starts_no_further_file(self) -> None:
        import threading

        from deepfake_lens.batch import BatchProcessor

        started: list[str] = []
        gate = threading.Event()

        def process(path: Path) -> dict[str, object]:
            started.append(path.name)
            if path.name == "0":
                raise KeyboardInterrupt  # Ctrl-C reaches the batch
            gate.wait(0.5)
            return {}

        files = [Path(str(index)) for index in range(10)]
        began = time.monotonic()
        with self.assertRaises(KeyboardInterrupt):
            BatchProcessor(max_workers=1).process_batch(files, process)
        elapsed = time.monotonic() - began
        gate.set()
        time.sleep(0.1)
        self.assertLess(elapsed, 0.45)  # it used to wait for the whole queue (≈ 4.5 s here)
        self.assertLessEqual(len(started), 2)  # file 0 and at most the one already picked up


class WithDefaultSignalsScriptTest(unittest.TestCase):
    def _run(self, *command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(REPO / "scripts" / "with_default_signals.py"), "--", *command],
            capture_output=True, text=True, timeout=CHILD_TIMEOUT_SECONDS,
        )

    def test_a_missing_program_is_a_korean_error_exit_127(self) -> None:
        done = self._run("deepfake-lens-no-such-program")
        self.assertEqual(done.returncode, 127)
        self.assertIn("오류: 실행할 명령을 찾을 수 없습니다: 'deepfake-lens-no-such-program'", done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        self.assertNotIn("FileNotFoundError", done.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX signals")
    def test_a_file_that_cannot_be_executed_is_exit_126_and_a_command_keeps_its_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "plain.txt"
            plain.write_text("not a program", encoding="utf-8")
            done = self._run(str(plain))
        self.assertEqual(done.returncode, 126)
        self.assertIn("오류: 명령을 실행할 권한이 없습니다", done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        self.assertEqual(self._run(sys.executable, "-c", "raise SystemExit(5)").returncode, 5)


if __name__ == "__main__":
    unittest.main()
