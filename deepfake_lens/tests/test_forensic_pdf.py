"""Tests for court-admissible forensic PDF report generation."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from deepfake_lens import api_server
from deepfake_lens.core import (
    BatchScanSummary,
    ClassificationResult,
    EvidenceSignal,
    RiskBand,
    ScanItem,
    SourceConfidence,
    SourceGuess,
)
from deepfake_lens.evidence_statement import PDF_REPORT_DEPENDENCY_MESSAGE, PdfDependencyMissing
from deepfake_lens.reports import write_forensic_pdf_report

HAVE_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None
HAVE_PYMUPDF = importlib.util.find_spec("pymupdf") is not None or importlib.util.find_spec("fitz") is not None


class ForensicPdfReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.summary = BatchScanSummary(
            total=2, analyzed=2, high=1, medium=1, unknown=0, low=0,
            unsupported_or_failed=0, capped=False,
        )
        sig1 = EvidenceSignal(title="픽셀 경계 이상", detail="얼굴 윤곽 부자연스러운 고주파 아티팩트", weight=30)
        res1 = ClassificationResult(
            score=88, band=RiskBand.HIGH, band_label="AI 의심", verdict="합성 의심",
            signals=[sig1], limitations=[],
            source_guess=SourceGuess(label="Midjourney v6", confidence=SourceConfidence.HIGH),
            next_checks=[],
        )
        self.item1 = ScanItem(
            path="sample/cctv_frame_01.png", name="cctv_frame_01.png", kind="image",
            status="analyzed", size_bytes=2048, result=res1,
        )

        sig2 = EvidenceSignal(title="스펙트로그램 결손", detail="고주파 차단 패턴", weight=18)
        res2 = ClassificationResult(
            score=42, band=RiskBand.MEDIUM, band_label="주의", verdict="주의 요망",
            signals=[sig2], limitations=[],
            source_guess=SourceGuess(label="RVC v2", confidence=SourceConfidence.MEDIUM),
            next_checks=[],
        )
        self.item2 = ScanItem(
            path="sample/recorded_call.wav", name="recorded_call.wav", kind="audio",
            status="analyzed", size_bytes=4096, result=res2,
        )
        self.items = [self.item1, self.item2]

    def test_without_pymupdf_refuses_in_korean_and_writes_nothing(self) -> None:
        """B8: no Latin-1 English fallback PDF — PdfDependencyMissing (Korean), no file."""
        def _missing() -> object:
            raise ImportError("pymupdf")

        with tempfile.TemporaryDirectory() as tmp, patch("deepfake_lens.pdf_backend.import_pymupdf", _missing):
            out_pdf = Path(tmp) / "forensic_report.pdf"
            with self.assertRaises(PdfDependencyMissing) as ctx:
                write_forensic_pdf_report(out_pdf, self.summary, self.items, exhibit_no="갑 제1호증")
            self.assertEqual(str(ctx.exception), PDF_REPORT_DEPENDENCY_MESSAGE)
            self.assertFalse(out_pdf.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    # B8: pymupdf is the only PDF backend (no Latin-1 fallback) — this case runs in the venv_api / extras job.
    @unittest.skipUnless(HAVE_PYMUPDF, "pymupdf not installed — the venv_api / extras run covers this")
    def test_write_forensic_pdf_generates_valid_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_pdf = Path(tmp) / "forensic_report.pdf"
            write_forensic_pdf_report(
                out_pdf,
                self.summary,
                self.items,
                exhibit_no="갑 제1호증",
            )
            self.assertTrue(out_pdf.is_file())
            raw = out_pdf.read_bytes()
            self.assertTrue(raw.startswith(b"%PDF-"))
            self.assertGreater(len(raw), 1500)
            import pymupdf
            doc = pymupdf.open(str(out_pdf))
            self.assertGreaterEqual(doc.page_count, 1)
            text = doc[0].get_text()
            # N17: no built-in law firm in the header (was "법무법인(유한) 대륜 …").
            self.assertNotIn("대륜", text)
            self.assertIn("디지털포렌식 감정센터", text)
            self.assertIn("갑 제1호증", text)
            self.assertIn("SHA-256", text)

    # B8: pymupdf is the only PDF backend (no Latin-1 fallback) — this case runs in the venv_api / extras job.
    @unittest.skipUnless(HAVE_PYMUPDF, "pymupdf not installed — the venv_api / extras run covers this")
    def test_redact_paths_in_forensic_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_pdf = Path(tmp) / "redacted_report.pdf"
            write_forensic_pdf_report(
                out_pdf,
                self.summary,
                self.items,
                redact_paths=True,
            )
            self.assertTrue(out_pdf.is_file())
            import pymupdf
            doc = pymupdf.open(str(out_pdf))
            text = doc[0].get_text()
            self.assertIn("cctv_frame_01.png", text)
            self.assertNotIn("sample/cctv_frame_01.png", text)


@unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed")
class ForensicPdfApiEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        from collections import OrderedDict

        from fastapi.testclient import TestClient

        from deepfake_lens import webapp_api

        # P1 (round 8): a report request names the scanned folder (scan_root,
        # inside the read roots); the hand-made row is not a file there, so it
        # is listed as unsigned and the PDF still renders.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.scan_root = str(Path(tmp.name).resolve())
        roots: Any = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        self.client = TestClient(api_server.create_app(default_folder=Path(self.scan_root)))

    def _payload(self) -> dict[str, object]:
        # N11: /api/report now checks every row against the scan-result item
        # contract; the hand-written legacy row this used (band/score only,
        # no verdict_code/evidence/coverage) is refused with 400. The row is
        # built through the result contract instead.
        from deepfake_lens.core import ScanItem, SourceGuess, build_classification_result
        from deepfake_lens.result_types import (
            CoverageEntry, CoverageStatus, EvidenceDirection, EvidenceItem, EvidenceKind, EvidenceStrength,
        )

        result = build_classification_result(
            subject="이미지",
            evidence=[EvidenceItem(
                "C2PA 서명: 생성형 AI 출처 선언", "trainedAlgorithmicMedia",
                EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG, "c2pa",
            )],
            coverage=[CoverageEntry("metadata", CoverageStatus.RAN)],
            source_guess=SourceGuess.unknown(),
            limitations=[],
            next_checks=[],
        )
        row = ScanItem("test/photo.jpg", "photo.jpg", "image", "analyzed", 100, result).to_json()
        return {"items": [row], "format": "pdf", "exhibit_no": "갑 제3호증", "scan_root": self.scan_root}

    # B8: pymupdf is the only PDF backend (no Latin-1 fallback) — this case runs in the venv_api / extras job.
    @unittest.skipUnless(HAVE_PYMUPDF, "pymupdf not installed — the venv_api / extras run covers this")
    def test_api_report_pdf_format_query(self) -> None:
        payload = self._payload()
        res = self.client.post(
            "/api/report?format=pdf",
            headers={"host": "localhost", "X-Deepfake-Lens-Client": "gui"},
            json=payload,
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("application/pdf", res.headers.get("content-type", ""))
        self.assertIn("deepfake-lens-forensic-report.pdf", res.headers.get("content-disposition", ""))
        self.assertTrue(res.content.startswith(b"%PDF-"))

    def test_api_report_pdf_without_pymupdf_is_korean_501(self) -> None:
        """B8: api-serve /api/report?format=pdf without pymupdf → 501 + Korean JSON error, no PDF."""
        def _missing() -> object:
            raise ImportError("pymupdf")

        with patch("deepfake_lens.pdf_backend.import_pymupdf", _missing):
            res = self.client.post(
                "/api/report?format=pdf",
                headers={"host": "localhost", "X-Deepfake-Lens-Client": "gui"},
                json=self._payload(),
            )
        self.assertEqual(res.status_code, 501)
        self.assertIn("application/json", res.headers.get("content-type", ""))
        self.assertFalse(res.content.startswith(b"%PDF"))
        self.assertEqual(res.json(), {"error": "PDF 보고서를 만들려면 pymupdf 패키지가 필요합니다(설치: pip install pymupdf)."})
