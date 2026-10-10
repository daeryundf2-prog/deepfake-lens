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
