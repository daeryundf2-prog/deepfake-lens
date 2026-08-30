"""Tests for Phase 4 honesty fixes: measured evidence integrity, checksum
(not "digital signature") naming, PDF Latin-1 notice, EER metric."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from deepfake_lens.calibration import eer
from deepfake_lens.evidence import create_evidence_chain
from deepfake_lens.enhanced_forensics import analyze_forensic
from deepfake_lens.reports import write_pdf_report


class EvidenceIntegrityTest(unittest.TestCase):
    """integrity_verified must be measured by re-hashing, not hardcoded."""

    def test_chain_for_existing_file_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_bytes(b"evidence payload")
            chain = create_evidence_chain(path, results={"score": 10})
            self.assertTrue(chain.integrity_verified)

    def test_chain_for_missing_file_reports_unverified(self) -> None:
        chain = create_evidence_chain("/nonexistent/evidence.bin", results={})
        self.assertFalse(chain.integrity_verified)


class ChecksumNamingTest(unittest.TestCase):
    """The unkeyed SHA-256 is an integrity checksum, not a signature."""

    def test_legal_text_names_checksum_not_signature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("forensic sample", encoding="utf-8")
            report = analyze_forensic(path)
            text = report.generate_legal_text()
            self.assertIn("무결성 체크섬", text)
            self.assertIn("전자서명 아님", text)
            self.assertNotIn("디지털 서명", text)

    def test_report_json_uses_checksum_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("forensic sample", encoding="utf-8")
            report = analyze_forensic(path)
            data = report.to_json()
            self.assertIn("integrity_checksum", data)
            self.assertNotIn("digital_signature", data)
            self.assertTrue(report.integrity_checksum)


class PdfLatin1NoticeTest(unittest.TestCase):
    def _summary(self):
        from deepfake_lens.core import BatchScanSummary

        return BatchScanSummary(
            total=1, analyzed=1, high=0, medium=1, unknown=0, low=0,
            unsupported_or_failed=0, capped=False,
        )

    def _item(self, path: Path):
        from deepfake_lens.core import ScanItem, analyze_text

        result = analyze_text("as an AI language model I conclude")
        return ScanItem(path=str(path), name=path.name, kind="text", status="analyzed", size_bytes=10, result=result)

    def test_korean_content_gets_latin1_notice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "sample.txt"
            sample.write_text("hello", encoding="utf-8")
            out = Path(tmp) / "report.pdf"
            write_pdf_report(out, self._summary(), [self._item(sample)])
            raw = out.read_bytes()
            self.assertIn(b"NOTE: this simple PDF is Latin-1 only", raw)

    def test_ascii_only_content_has_no_notice(self) -> None:
        from deepfake_lens.core import ScanItem

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.pdf"
            write_pdf_report(out, self._summary(), [])
            raw = out.read_bytes()
            self.assertNotIn(b"NOTE: this simple PDF", raw)


class EerTest(unittest.TestCase):
    def test_separable_scores_have_near_zero_eer(self) -> None:
        scores = [(5, False), (95, True)] * 10
        value = eer(scores)
        self.assertIsNotNone(value)
        self.assertLessEqual(value, 0.05)

    def test_fully_overlapping_scores_have_high_eer(self) -> None:
        scores = [(50, False), (50, True)] * 10
        value = eer(scores)
        self.assertGreaterEqual(value, 0.4)
        self.assertLessEqual(value, 0.6)

    def test_single_class_returns_none(self) -> None:
        self.assertIsNone(eer([(50, True), (60, True)]))
        self.assertIsNone(eer([]))


if __name__ == "__main__":
    unittest.main()
