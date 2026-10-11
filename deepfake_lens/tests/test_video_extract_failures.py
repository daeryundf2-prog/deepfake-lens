"""R18-19 (a) (round 18): `video --extract` whose every extraction fails says so and exits 1.

With an ffmpeg that is killed (-9) or cannot run (127) every extraction
failed, the command exited 0 and printed nothing about it — the failures
were only inside the --out JSON.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.name == "posix", "POSIX signals")
class VideoExtractFailuresTest(unittest.TestCase):
    def _run(self, ffmpeg_script: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools = root / "bin"
            tools.mkdir()
            fake = tools / "ffmpeg"
            fake.write_text("#!/bin/sh\n" + ffmpeg_script + "\n", encoding="utf-8")
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            videos = root / "videos"
            videos.mkdir()
            (videos / "a.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
            (videos / "b.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x01" * 64)
            env = dict(os.environ, PYTHONPATH=str(REPO), PATH=f"{tools}{os.pathsep}{os.environ.get('PATH', '')}")
            return subprocess.run(
                [sys.executable, "-m", "deepfake_lens", "video", str(videos), "--out", str(root / "plan.json"), "--frame-root", str(root / "frames"), "--extract"],
                capture_output=True, text=True, env=env, timeout=300,
            )

    def test_an_ffmpeg_killed_by_a_signal(self) -> None:
        done = self._run("kill -KILL $$")
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("오류: 프레임 추출 실패 2건", done.stderr)
        self.assertIn("ffmpeg가 신호 9로 종료됨", done.stderr)
        self.assertIn('"failed": 2', done.stdout)

    def test_an_ffmpeg_that_cannot_run(self) -> None:
        done = self._run("exit 127")
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("ffmpeg를 실행할 수 없음(종료 코드 127)", done.stderr)

    def test_a_working_ffmpeg_is_exit_0(self) -> None:
        done = self._run("exit 0")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('"extracted": 2', done.stdout)
        self.assertNotIn("오류", done.stderr)


if __name__ == "__main__":
    unittest.main()
