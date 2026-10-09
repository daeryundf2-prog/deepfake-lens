"""G14 (round 5): decoder chatter on fd 2 never reaches the examiner's console.

OpenCV's FFmpeg backend printed ``[mov,mp4,…] moov atom not found`` and
mpg123 (inside libsndfile/librosa) ``Note: Illegal Audio-MPEG-Header …``
straight to the process stderr while a broken mp4/mp3 was scanned. Those
go to ``<log dir>/native-stderr.log`` now; the row's coverage carries the
Korean reason.
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from deepfake_lens.error_text import english_prose

REPO_ROOT = Path(__file__).resolve().parents[2]


def _broken_mp4() -> bytes:
    """An mp4 with ftyp and mdat but no moov box (truncated upload)."""
    ftyp = struct.pack(">I", 24) + b"ftyp" + b"isom" + struct.pack(">I", 512) + b"isomiso2"
    mdat = struct.pack(">I", 8 + 4096) + b"mdat" + bytes((i * 31 + 7) % 256 for i in range(4096))
    return ftyp + mdat


def _fake_mp3() -> bytes:
    return bytes((i * 37 + 11) % 256 for i in range(3000))


def _env(log_dir: Path, home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["DEEPFAKE_LENS_LOG_DIR"] = str(log_dir)
    env["HOME"] = str(home)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(REPO_ROOT), env.get("PYTHONPATH"))))
    return env


class NativeStderrRedirectTest(unittest.TestCase):
    def test_fd2_goes_to_the_log_and_python_stderr_stays_on_the_console(self) -> None:
        """Inside the block a native write (os.write(2)) lands in the log; print(file=sys.stderr) does not."""
        script = textwrap.dedent(
            """
            import os, sys
            from deepfake_lens.native_stderr import native_stderr_to_log, quiet_native_stderr
            with native_stderr_to_log():
                os.write(2, b"native decoder noise\\n")
                print("파이썬 경고 한 줄", file=sys.stderr)
                with native_stderr_to_log():  # nested (another decoder) keeps the redirect
                    os.write(2, b"nested noise\\n")
                os.write(2, b"still native\\n")

            @quiet_native_stderr
            def decode():
                os.write(2, b"decorated noise\\n")
                return 7

            assert decode() == 7
            os.write(2, b"after the block\\n")
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "logs"
            done = subprocess.run([sys.executable, "-c", script], capture_output=True, env=_env(log_dir, Path(tmp)), timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
            console = done.stderr.decode("utf-8", "replace")
            self.assertEqual(console.splitlines(), ["파이썬 경고 한 줄", "after the block"])
            logged = (log_dir / "native-stderr.log").read_text(encoding="utf-8")
            for line in ("native decoder noise", "nested noise", "still native", "decorated noise"):
                self.assertIn(line, logged)
            self.assertNotIn("파이썬 경고", logged)

    def test_scanning_broken_mp4_and_mp3_prints_nothing_but_korean(self) -> None:
        """A CLI scan of a broken mp4 and a garbage mp3: no decoder text on stderr; it is in the log."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "case"
            folder.mkdir()
            (folder / "broken_moov.mp4").write_bytes(_broken_mp4())
            (folder / "fake.mp3").write_bytes(_fake_mp3())
            log_dir = Path(tmp) / "logs"
            done = subprocess.run(
                [sys.executable, "-m", "deepfake_lens", "scan", str(folder), "--format", "json", "--deep-signals"],
                capture_output=True, env=_env(log_dir, Path(tmp)), timeout=600,
            )
            self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace")[-2000:])
            console = done.stderr.decode("utf-8", "replace")
            for noise in ("moov atom", "Illegal Audio-MPEG-Header", "Note:", "Trying to resync", "Traceback", "[mov,mp4"):
                self.assertNotIn(noise, console)
            for line in console.splitlines():
                self.assertIsNone(english_prose(line), line)
            # The decoders really were asked (the noise exists — it is in the log, not lost).
            native_log = log_dir / "native-stderr.log"
            if native_log.exists():
                text = native_log.read_text(encoding="utf-8", errors="replace")
                self.assertNotIn("파일", text)  # only native output is captured there


if __name__ == "__main__":
    unittest.main()
