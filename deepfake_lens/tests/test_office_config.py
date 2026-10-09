"""N17: report headers carry no built-in law firm; libsndfile and provenance wording.

Round 6: every forensic PDF, evidence statement and web report printed one
specific law firm's name, street address and phone number by default; a
truncated MP3 was reported as "파일이 없거나 일반 파일이 아님"; the forensic
PDF put "측정됨" and "in-sample(참고)" side by side on one line.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Any
from unittest import mock

from deepfake_lens import webapp_api
from deepfake_lens.cli import main
from deepfake_lens.office_config import CONFIG_ENV, DEFAULT_CENTER, load_office_config, office_identity

HAVE_PYMUPDF = importlib.util.find_spec("pymupdf") is not None
# The former built-in identity — none of it may appear in any output.
OLD_IDENTITY = ("대륜", "테헤란로", "역삼빌딩", "780-1128")
REPO_ROOT = Path(__file__).resolve().parents[2]
A1111 = REPO_ROOT / "fixtures" / "benchmark" / "a1111-metadata-marker.png"


def _pdf_text(path_or_bytes: Path | bytes) -> str:
    import pymupdf

    doc = pymupdf.open(stream=path_or_bytes, filetype="pdf") if isinstance(path_or_bytes, bytes) else pymupdf.open(str(path_or_bytes))
    with doc:
        return "\n".join(page.get_text() for page in doc)


class _Isolated(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        self.case = self.base / "case"
        self.case.mkdir()
        (self.case / "a1111.png").write_bytes(A1111.read_bytes())
        (self.case / "memo.txt").write_text("오늘 회의 메모입니다.", encoding="utf-8")
        env = {"HOME": str(self.home), "DEEPFAKE_LENS_LOG_DIR": str(self.base / "logs")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(CONFIG_ENV, None)
        os.environ.pop("DEEPFAKE_LENS_REPORT_KEY", None)

    def write_config(self, **values: str) -> None:
        folder = self.home / ".deepfake-lens"
        folder.mkdir(exist_ok=True)
        (folder / "config.json").write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")

    def scan_outputs(self, *extra: str) -> dict[str, str]:
        out = self.base / f"out{len(list(self.base.glob('out*')))}"
        out.mkdir()
        argv = [
            "scan", str(self.case), "--format", "json",
            "--json-out", str(out / "scan.json"),
            "--html-out", str(out / "report.html"),
            "--evidence-statement-out", str(out / "statement.md"),
            *extra,
        ]
        if HAVE_PYMUPDF:
            argv += [
                "--forensic-pdf-out", str(out / "forensic.pdf"),
                "--pdf-out", str(out / "report.pdf"),
                "--evidence-statement-pdf-out", str(out / "statement.pdf"),
            ]
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(argv), 0)
        texts = {"stdout": stdout.getvalue()}
        for name in ("scan.json", "report.html", "statement.md"):
            texts[name] = (out / name).read_text(encoding="utf-8")
        if HAVE_PYMUPDF:
            for name in ("forensic.pdf", "report.pdf", "statement.pdf"):
                texts[name] = _pdf_text(out / name)
        return texts


class NoBuiltInLawFirmTest(_Isolated):
    """N17: without --law-firm/--contact or a config file, no output names a firm."""

    def test_cli_outputs_have_no_built_in_identity(self) -> None:
        texts = self.scan_outputs()
        for name, text in texts.items():
            for needle in OLD_IDENTITY:
                with self.subTest(output=name, needle=needle):
                    self.assertNotIn(needle, text)
        if HAVE_PYMUPDF:
            self.assertIn(DEFAULT_CENTER, texts["forensic.pdf"])

    def test_evidence_statement_command_has_no_built_in_identity(self) -> None:
        out = self.base / "stmt.md"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["evidence-statement", str(self.case), "--md-out", str(out)]), 0)
        text = out.read_text(encoding="utf-8")
        for needle in OLD_IDENTITY:
            self.assertNotIn(needle, text)

    def test_web_reports_have_no_built_in_identity(self) -> None:
        from deepfake_lens.analysis_api import AnalysisOptions, scan_folder_run, scan_payload
        from deepfake_lens.webapp import build_server

        run = scan_folder_run(self.case, AnalysisOptions())
        rows = scan_payload(run.summary, run.items, run.thresholds, AnalysisOptions())["items"]
        roots: Any = mock.patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        server = build_server("127.0.0.1", 0, default_folder=self.case)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        formats = ["html", "json"] + (["pdf", "evidence"] if HAVE_PYMUPDF else [])
        for fmt in formats:
            with self.subTest(format=fmt):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_address[1]}/api/report?format={fmt}",
                    data=json.dumps({"items": rows}).encode("utf-8"),
                    headers={"X-Deepfake-Lens-Client": "gui", "Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=120) as response:
                    body = response.read()
                text = _pdf_text(body) if body.startswith(b"%PDF") else body.decode("utf-8")
                for needle in OLD_IDENTITY:
                    self.assertNotIn(needle, text)


class OfficeIdentitySourcesTest(_Isolated):
    """N17: the identity comes from the flag, else ~/.deepfake-lens/config.json, else blank."""

    def test_resolution_order(self) -> None:
        self.assertEqual(office_identity().law_firm, "")
        self.assertEqual(office_identity().contact, "")
        self.write_config(law_firm="법무법인 설정값", contact="02-1111-2222")
        self.assertEqual(load_office_config(), {"law_firm": "법무법인 설정값", "contact": "02-1111-2222"})
        self.assertEqual(office_identity().law_firm, "법무법인 설정값")
        self.assertEqual(office_identity("법무법인 플래그", None).law_firm, "법무법인 플래그")
        self.assertEqual(office_identity("법무법인 플래그", None).contact, "02-1111-2222")
        self.assertEqual(office_identity().header, f"법무법인 설정값 {DEFAULT_CENTER}")

    def test_config_file_reaches_the_reports(self) -> None:
        self.write_config(law_firm="법무법인 설정값", contact="02-1111-2222")
        texts = self.scan_outputs()
        self.assertIn("법무법인 설정값", texts["statement.md"])
        self.assertIn("02-1111-2222", texts["statement.md"])
        if HAVE_PYMUPDF:
            for name in ("forensic.pdf", "statement.pdf"):
                self.assertIn("법무법인 설정값", texts[name], name)

    def test_flags_override_the_config(self) -> None:
        self.write_config(law_firm="법무법인 설정값", contact="02-1111-2222")
        texts = self.scan_outputs("--law-firm", "법무법인 플래그", "--contact", "02-3333-4444")
        self.assertIn("법무법인 플래그", texts["statement.md"])
        self.assertIn("02-3333-4444", texts["statement.md"])
        self.assertNotIn("법무법인 설정값", texts["statement.md"])
        if HAVE_PYMUPDF:
            self.assertIn("법무법인 플래그", texts["forensic.pdf"])
            self.assertIn("02-3333-4444", texts["forensic.pdf"])

    def test_unreadable_config_is_blank(self) -> None:
        folder = self.home / ".deepfake-lens"
        folder.mkdir()
        (folder / "config.json").write_text("{not json", encoding="utf-8")
        with self.assertLogs("deepfake_lens.office_config", level="WARNING"):
            self.assertEqual(office_identity().law_firm, "")


class LibsndfileAndProvenanceWordingTest(unittest.TestCase):
    """N17: the truncated-MP3 reason and the in-sample provenance line."""

    def test_libsndfile_open_failure_is_a_decode_failure(self) -> None:
        from deepfake_lens.error_text import english_prose, korean_exception_message

        LibsndfileError = type("LibsndfileError", (RuntimeError,), {})
        message = korean_exception_message(LibsndfileError("Error opening '/x/truncated.mp3': File does not exist or is not a regular file (possibly a pipe?)."))
        self.assertIn("오디오 디코드 실패(파일 손상 또는 미지원 코덱)", message)
        self.assertNotIn("파일이 없거나", message)
        self.assertIsNone(english_prose(message), message)

    def test_in_sample_profile_is_not_called_measured_and_caveat_is_its_own_line(self) -> None:
        from deepfake_lens.calibration import IN_SAMPLE_LABEL
        from deepfake_lens.result_text import IN_SAMPLE_CAVEAT, threshold_provenance_line, threshold_provenance_lines

        payload = {"version": "layer-thresholds-v1", "samples": 173, "dataset_fingerprint": "d77809e5eddfd968aa", "provisional": False, "in_sample": True}
        lines = threshold_provenance_lines(payload)
        self.assertEqual(len(lines), 2)
        self.assertNotIn("측정됨", lines[0])
        self.assertIn(IN_SAMPLE_LABEL, lines[0])
        self.assertEqual(lines[1], f"{IN_SAMPLE_LABEL}: {IN_SAMPLE_CAVEAT}")
        self.assertEqual(threshold_provenance_line(payload), "\n".join(lines))
        measured = threshold_provenance_lines({**payload, "in_sample": False})
        self.assertEqual(len(measured), 1)
        self.assertIn("측정됨", measured[0])

    @unittest.skipUnless(HAVE_PYMUPDF, "pymupdf required for PDF text")
    def test_forensic_pdf_prints_the_caveat_on_a_separate_line(self) -> None:
        from deepfake_lens.calibration import IN_SAMPLE_LABEL
        from deepfake_lens.core import scan_directory
        from deepfake_lens.reports import write_forensic_pdf_report

        payload = {"source": "threshold_profile", "version": "layer-thresholds-v1", "samples": 173, "provisional": False, "in_sample": True}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "memo.txt").write_text("메모입니다.", encoding="utf-8")
            summary, items = scan_directory(root)
            out = root / "forensic.pdf"
            with mock.patch.dict(os.environ, {CONFIG_ENV: str(root / "none.json")}):
                write_forensic_pdf_report(out, summary, items, thresholds=payload)
            lines = _pdf_text(out).splitlines()
        provenance = [line for line in lines if line.startswith("판정 임계값")]
        self.assertEqual(len(provenance), 1, lines)
        self.assertNotIn("측정됨", provenance[0])
        self.assertTrue(any(line.startswith(f"{IN_SAMPLE_LABEL}:") for line in lines), lines)


if __name__ == "__main__":
    unittest.main()
