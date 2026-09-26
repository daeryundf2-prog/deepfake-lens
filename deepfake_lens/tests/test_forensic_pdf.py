"""Tests for court-admissible forensic PDF report generation."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

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

            if HAVE_PYMUPDF:
                import pymupdf
                doc = pymupdf.open(str(out_pdf))
                self.assertGreaterEqual(doc.page_count, 1)
                text = doc[0].get_text()
                self.assertIn("대륜", text)
                self.assertIn("갑 제1호증", text)
                self.assertIn("SHA-256", text)

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
            if HAVE_PYMUPDF:
                import pymupdf
                doc = pymupdf.open(str(out_pdf))
                text = doc[0].get_text()
                self.assertIn("cctv_frame_01.png", text)
                self.assertNotIn("sample/cctv_frame_01.png", text)


@unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed")
class ForensicPdfApiEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi.testclient import TestClient
        self.client = TestClient(api_server.create_app())

    def test_api_report_pdf_format_query(self) -> None:
        payload = {
            "items": [
                {
                    "path": "test/photo.jpg",
                    "name": "photo.jpg",
                    "kind": "image",
                    "status": "analyzed",
                    "size_bytes": 100,
                    "result": {
                        "score": 90,
                        "band": "high",
                        "band_label": "AI 의심",
                        "verdict": "합성 의심",
                        "signals": [{"title": "노이즈 결손", "detail": "인공 생성 패턴", "weight": 25}],
                        "limitations": [],
                        "source_guess": {"label": "SDXL", "confidence": "high"},
                        "next_checks": [],
                    },
                }
            ],
            "format": "pdf",
            "exhibit_no": "갑 제3호증",
        }
        res = self.client.post(
            "/api/report?format=pdf",
            headers={"host": "localhost", "X-Deepfake-Lens-Client": "gui"},
            json=payload,
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("application/pdf", res.headers.get("content-type", ""))
        self.assertIn("deepfake-lens-forensic-report.pdf", res.headers.get("content-disposition", ""))
        self.assertTrue(res.content.startswith(b"%PDF-"))
