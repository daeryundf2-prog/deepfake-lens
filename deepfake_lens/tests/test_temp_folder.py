"""R15-6 (round 15): a temp folder that cannot be used is reported, never silently replaced.

With a non-ASCII TMPDIR (a Korean Windows user name) every temp file of a
run — a 2 GB archive extraction included — went to ``/tmp``
(``C:\\Windows\\Temp``) without a word. Now a Korean notice goes to stderr
once, the scan result records the fallback (``temp_folder``), and
``DEEPFAKE_LENS_TMPDIR`` names the folder explicitly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deepfake_lens import native_path

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parent
CHILD_TIMEOUT_SECONDS = 300
NOTICE_HEAD = "알림: 임시 폴더 "


def _env(root: Path, **extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in (native_path.TMP_ENV, native_path.NATIVE_TMP_ENV, "DEEPFAKE_LENS_REPORT_KEY")}
    env.update({"HOME": str(root / "home"), "DEEPFAKE_LENS_LOG_DIR": str(root / "logs"), "PYTHONPATH": str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", "")})
    env.update(extra)
    return env


class TempFolderFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.case = self.root / "case"
        self.case.mkdir()
        (self.case / "증거 메모.txt").write_text("회의록 초안입니다. 사건 기록.", encoding="utf-8")
        import zipfile

        with zipfile.ZipFile(self.case / "묶음.zip", "w") as archive:  # extracted in the session folder: a temp file is made
            archive.writestr("안쪽.txt", "압축 안의 메모")
        self.korean_tmp = self.root / "임시 폴더"
        self.korean_tmp.mkdir()
        self.ascii_tmp = self.root / "ascii-tmp"
        self.ascii_tmp.mkdir()

    def _scan(self, env: dict[str, str]) -> tuple[dict, str]:
        done = subprocess.run(
            [sys.executable, "-m", "deepfake_lens", "scan", str(self.case), "--include-low", "--format", "json"],
            capture_output=True, env=env, timeout=CHILD_TIMEOUT_SECONDS, cwd=str(self.root),
        )
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        return json.loads(done.stdout.decode("utf-8")), done.stderr.decode("utf-8", "replace")

    def test_a_non_ascii_temp_folder_is_reported_once_and_recorded(self) -> None:
        # The Korean TMPDIR is skipped (native decoders need ASCII); the next
        # candidate here is DEEPFAKE_LENS_NATIVE_TMPDIR (so /tmp is not touched).
        env = _env(self.root, TMPDIR=str(self.korean_tmp), **{native_path.TMP_ENV: str(self.korean_tmp), native_path.NATIVE_TMP_ENV: str(self.ascii_tmp)})
        payload, stderr = self._scan(env)
        notices = [line for line in stderr.splitlines() if line.startswith(NOTICE_HEAD)]
        self.assertEqual(len(notices), 1, stderr[-800:])
        self.assertIn("경로에 ASCII가 아닌 문자가 있음", notices[0])
        self.assertIn(str(self.ascii_tmp), notices[0])
        self.assertIn("DEEPFAKE_LENS_TMPDIR", notices[0])
        self.assertEqual(
            payload["temp_folder"],
            {
                "fallback": True, "base": str(self.ascii_tmp), "reason": "경로에 ASCII가 아닌 문자가 있음", "override_env": "DEEPFAKE_LENS_TMPDIR",
                # R16-10: who named the unusable folder, and which folder.
                "requested": str(self.korean_tmp), "requested_by": "DEEPFAKE_LENS_TMPDIR",
            },
        )
        self.assertEqual(os.listdir(self.ascii_tmp), [])  # the session folder was used there and removed
        self.assertEqual(os.listdir(self.korean_tmp), [])

    def test_a_tmpdir_that_is_a_file_is_named_as_one(self) -> None:
        """R17-11 (round 17): DEEPFAKE_LENS_TMPDIR naming a file was reported "(폴더가 없음)"."""
        a_file = self.root / "tmp-file"
        a_file.write_text("x", encoding="utf-8")
        env = _env(self.root, TMPDIR=str(self.ascii_tmp), **{native_path.TMP_ENV: str(a_file)})
        payload, stderr = self._scan(env)
        self.assertEqual(payload["temp_folder"]["reason"], "폴더가 아니라 파일임")
        self.assertEqual(payload["temp_folder"]["requested"], str(a_file))
        notices = [line for line in stderr.splitlines() if line.startswith(NOTICE_HEAD)]
        self.assertEqual(len(notices), 1, stderr[-800:])
        self.assertIn("(폴더가 아니라 파일임)", notices[0])
        self.assertNotIn("폴더가 없음", notices[0])

    def test_deepfake_lens_tmpdir_is_used_without_a_notice(self) -> None:
        chosen = self.root / "chosen"
        chosen.mkdir()
        env = _env(self.root, TMPDIR=str(self.korean_tmp), **{native_path.TMP_ENV: str(chosen)})
        script = (
            "import json, os, sys\n"
            "from deepfake_lens import native_path\n"
            "folder = native_path.scratch_dir()\n"
            "print(json.dumps({'parent': os.path.dirname(folder), 'location': native_path.temp_location()}))\n"
        )
        done = subprocess.run([sys.executable, "-c", script], capture_output=True, env=env, timeout=CHILD_TIMEOUT_SECONDS)
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        seen = json.loads(done.stdout)
        self.assertEqual(seen, {"parent": str(chosen), "location": {"fallback": False}})
        self.assertNotIn(NOTICE_HEAD, done.stderr.decode("utf-8", "replace"))
        payload, stderr = self._scan(env)
        self.assertEqual(payload["temp_folder"], {"fallback": False})
        self.assertNotIn(NOTICE_HEAD, stderr)

    def test_the_html_report_shows_the_fallback(self) -> None:
        out = self.root / "report.html"
        env = _env(self.root, TMPDIR=str(self.korean_tmp), **{native_path.TMP_ENV: str(self.korean_tmp), native_path.NATIVE_TMP_ENV: str(self.ascii_tmp)})
        done = subprocess.run(
            [sys.executable, "-m", "deepfake_lens", "scan", str(self.case), "--include-low", "--html-out", str(out)],
            capture_output=True, env=env, timeout=CHILD_TIMEOUT_SECONDS, cwd=str(self.root),
        )
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        html = out.read_text(encoding="utf-8")
        self.assertIn('id="temp-folder"', html)
        # R16-10 (round 16): DEEPFAKE_LENS_TMPDIR named the unusable folder here —
        # this test used to expect "시스템 임시 폴더를 쓸 수 없어" (the defect).
        self.assertIn("임시 폴더: 환경 변수 DEEPFAKE_LENS_TMPDIR에 지정된 폴더를 쓸 수 없어(경로에 ASCII가 아닌 문자가 있음)", html)
        self.assertNotIn("시스템 임시 폴더", html)

    def test_the_wording_names_who_chose_the_folder(self) -> None:
        """R16-10: the system temp folder, DEEPFAKE_LENS_TMPDIR or DEEPFAKE_LENS_NATIVE_TMPDIR."""
        from deepfake_lens.reports import temp_folder_line

        base = {"fallback": True, "base": "/tmp", "reason": "폴더가 없음", "override_env": "DEEPFAKE_LENS_TMPDIR"}
        self.assertIn("시스템 임시 폴더를 쓸 수 없어(폴더가 없음)", temp_folder_line({**base, "requested_by": "system"}))
        self.assertIn("시스템 임시 폴더를 쓸 수 없어", temp_folder_line(base))  # an R15-6 record
        self.assertIn("환경 변수 DEEPFAKE_LENS_NATIVE_TMPDIR에 지정된 폴더를 쓸 수 없어", temp_folder_line({**base, "requested_by": "DEEPFAKE_LENS_NATIVE_TMPDIR"}))
        self.assertEqual(temp_folder_line({"fallback": False}), "")
        with mock.patch.dict(os.environ, {native_path.TMP_ENV: str(self.korean_tmp)}), mock.patch.object(native_path, "_SESSION_DIR", None), mock.patch.object(
            native_path, "_base_candidates", return_value=[str(self.korean_tmp), str(self.ascii_tmp)]
        ):
            self.assertEqual(native_path.temp_location()["requested_by"], native_path.TMP_ENV)
        with mock.patch.object(native_path, "_SESSION_DIR", None), mock.patch.object(
            native_path, "_base_candidates", return_value=[str(self.root / "없음"), str(self.ascii_tmp)]
        ), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(native_path.TMP_ENV, None)
            os.environ.pop(native_path.NATIVE_TMP_ENV, None)
            self.assertEqual(native_path.temp_location()["requested_by"], "system")

    def test_the_evidence_statement_records_the_fallback(self) -> None:
        """R16-10: the evidence statement had no temp-folder line; now its JSON, Markdown (and PDF notice) carry it."""
        out = self.root / "statement.json"
        md = self.root / "statement.md"
        env = _env(self.root, TMPDIR=str(self.korean_tmp), **{native_path.TMP_ENV: str(self.korean_tmp), native_path.NATIVE_TMP_ENV: str(self.ascii_tmp)})
        done = subprocess.run(
            [sys.executable, "-m", "deepfake_lens", "evidence-statement", str(self.case), "--json-out", str(out), "--md-out", str(md)],
            capture_output=True, env=env, timeout=CHILD_TIMEOUT_SECONDS, cwd=str(self.root),
        )
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        body = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(body["temp_folder"]["requested_by"], "DEEPFAKE_LENS_TMPDIR")
        self.assertEqual(body["temp_folder"]["base"], str(self.ascii_tmp))
        line = "임시 폴더: 환경 변수 DEEPFAKE_LENS_TMPDIR에 지정된 폴더를 쓸 수 없어(경로에 ASCII가 아닌 문자가 있음)"
        self.assertIn(line, body["provenance_note"])  # inside the signed body
        from deepfake_lens.result_text import markdown_text

        self.assertIn(markdown_text(line), md.read_text(encoding="utf-8"))  # the 분석 프로비넌스 section

    def test_the_dry_run_answer_matches_what_session_dir_does(self) -> None:
        with mock.patch.object(native_path, "_SESSION_DIR", None), mock.patch.object(native_path, "_SESSION_DIRS", []), mock.patch.object(
            native_path, "_TEMP_LOCATIONS", {}
        ), mock.patch.object(native_path, "_TEMP_NOTICE_SHOWN", True), mock.patch.object(
            native_path, "_base_candidates", return_value=[str(self.korean_tmp), str(self.root / "missing"), str(self.ascii_tmp)]
        ), mock.patch.object(native_path, "install_cleanup_handlers"):
            planned = native_path.temp_location()
            folder = native_path.session_dir()
            try:
                self.assertEqual(os.path.dirname(folder), str(self.ascii_tmp))
                self.assertEqual(native_path.temp_location(), planned)
                self.assertEqual(planned["base"], str(self.ascii_tmp))
            finally:
                native_path.cleanup_session()


if __name__ == "__main__":
    unittest.main()
