"""R18-15 (round 18): output files are written whole, and output failures are Korean lines with an exit code.

The verifier saw ``scan … | head`` end with "예기치 않은 오류(BrokenPipeError)"
(exit 1), ``--json-out /dev/full`` fail after the whole scan with "예기치
않은 오류(OSError)", a closed standard output (``>&-``) lose the JSON with
exit 0, and ``--json-out`` written in place (an interruption left the old
report truncated).
"""

from __future__ import annotations

import errno
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deepfake_lens import output_files
from deepfake_lens.output_files import BROKEN_PIPE_EXIT, OutputWriteError, write_output_text

REPO = Path(__file__).resolve().parents[2]
TIMEOUT = 300


class WholeFileTest(unittest.TestCase):
    def test_a_failed_write_leaves_the_old_report_and_no_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "report.json")
            target.write_text('{"old": true}\n', encoding="utf-8")
            with mock.patch.object(output_files.os, "fsync", side_effect=OSError(errno.ENOSPC, "No space left on device")):
                with self.assertRaises(OutputWriteError) as caught:
                    write_output_text(target, '{"new": true}\n')
            self.assertEqual(target.read_text(encoding="utf-8"), '{"old": true}\n')
            self.assertEqual(sorted(os.listdir(tmp)), ["report.json"])
        self.assertEqual(str(caught.exception), f"출력 파일을 쓸 수 없습니다: {target} (디스크 공간이 부족합니다(오류 번호 28))")

    def test_a_link_is_followed_and_a_new_file_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp, "real.json")
            real.write_text("old", encoding="utf-8")
            link = Path(tmp, "link.json")
            link.symlink_to(real)
            write_output_text(link, "new")
            self.assertTrue(link.is_symlink())
            self.assertEqual(real.read_text(encoding="utf-8"), "new")
            write_output_text(Path(tmp, "sub", "fresh.json"), "x")
            self.assertEqual(Path(tmp, "sub", "fresh.json").read_text(encoding="utf-8"), "x")


def _cli(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=str(REPO))
    return subprocess.run([sys.executable, "-m", "deepfake_lens", *args], text=True, env=env, timeout=TIMEOUT, **kwargs)  # type: ignore[call-overload]


@unittest.skipUnless(sys.platform.startswith("linux") and Path("/dev/full").exists(), "Linux /dev/full needed")
class CliOutputFailureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name, "case")
        self.folder.mkdir()
        (self.folder / "note.txt").write_text("메모", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_json_out_on_a_full_disk(self) -> None:
        done = _cli(["scan", str(self.folder), "--json-out", "/dev/full", "--format", "json"], capture_output=True)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("오류: 출력 파일을 쓸 수 없습니다: /dev/full (디스크 공간이 부족합니다(오류 번호 28))", done.stderr)
        self.assertNotIn("예기치 않은 오류", done.stderr)
        self.assertTrue(Path("/dev/full").is_char_device())  # written in place, never renamed over

    def test_standard_output_on_a_full_disk(self) -> None:
        with open("/dev/full", "w") as full:
            done = _cli(["scan", str(self.folder), "--format", "json"], stdout=full, stderr=subprocess.PIPE)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("오류: 결과를 표준 출력에 쓸 수 없습니다(디스크 공간이 부족합니다(오류 번호 28))", done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        self.assertNotIn("Exception ignored", done.stderr)

    def test_a_reader_that_left_is_a_quiet_141(self) -> None:
        read_end, write_end = os.pipe()
        os.close(read_end)  # `| head` that has already exited
        try:
            done = _cli(["scan", str(self.folder), "--format", "json"], stdout=write_end, stderr=subprocess.PIPE)
        finally:
            os.close(write_end)
        self.assertEqual(done.returncode, BROKEN_PIPE_EXIT, done.stderr)
        for word in ("오류", "Traceback", "Exception ignored", "BrokenPipe"):
            self.assertNotIn(word, done.stderr)  # only the usual notes (engine profiles, thresholds)

    def test_a_closed_standard_output_is_an_error(self) -> None:
        env = dict(os.environ, PYTHONPATH=str(REPO))
        done = subprocess.run(
            ["sh", "-c", 'exec "$0" -m deepfake_lens scan "$1" --format json >&-', sys.executable, str(self.folder)],
            capture_output=False, stderr=subprocess.PIPE, text=True, env=env, timeout=TIMEOUT,
        )
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("오류: 표준 출력이 닫혀 있어 결과를 출력할 수 없습니다", done.stderr)


if __name__ == "__main__":
    unittest.main()
