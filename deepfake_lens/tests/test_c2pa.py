"""Tests for the C2PA forensics module."""

from __future__ import annotations

import unittest
import unittest
from pathlib import Path

from deepfake_lens.c2pa import (
    MetadataForensicAnalysis,
    ProvenanceRecord,
    analyze_metadata_forensic,
)


class MetadataForensicAnalysisTest(unittest.TestCase):
    """Test cases for metadata forensic analysis functions."""

    def test_nonexistent_file_returns_error(self) -> None:
        """Analysis of nonexistent file should return error analysis."""
        result = analyze_metadata_forensic(Path("/nonexistent/file.jpg"))
        self.assertEqual(result.score, 0)
        self.assertEqual(result.band, "unknown")
        self.assertIn("존재하지 않습니다", result.verdict)

    def test_empty_file_returns_error(self) -> None:
        """Analysis of empty file should return error analysis."""
        tmp_path = Path("/tmp") / "empty.txt"
        tmp_path.write_bytes(b"")
        result = analyze_metadata_forensic(tmp_path)
        self.assertEqual(result.score, 0)
        self.assertIn("비어 있습니다", result.verdict)
        tmp_path.unlink(missing_ok=True)

    def test_analysis_returns_dataclass(self) -> None:
        """Analysis should return a MetadataForensicAnalysis dataclass."""
        result = analyze_metadata_forensic(Path("nonexistent.jpg"))
        self.assertIsInstance(result, MetadataForensicAnalysis)

    def test_to_json_returns_dict(self) -> None:
        """to_json should return a dictionary."""
        result = analyze_metadata_forensic(Path("nonexistent.jpg"))
        data = result.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("score", data)
        self.assertIn("band", data)
        self.assertIn("verdict", data)
        self.assertIn("has_c2pa", data)
        self.assertIn("has_synthid", data)
        self.assertIn("has_watermark", data)

    def test_c2pa_detection(self) -> None:
        """Files with C2PA marker should be detected."""
        tmp_path = Path("/tmp") / "test_c2pa.jpg"
        tmp_path.write_bytes(b"\xff\xd8" + b"\x00" * 100 + b"c2pa" + b"\x00" * 100)
        result = analyze_metadata_forensic(tmp_path)
        self.assertTrue(result.has_c2pa)
        self.assertGreater(result.score, 0)
        tmp_path.unlink(missing_ok=True)

    def test_synthid_detection(self) -> None:
        """Files with SynthID marker should be detected."""
        tmp_path = Path("/tmp") / "test_synthid.jpg"
        # Use mostly printable context around marker for better detection
        context = b"This is a SynthID watermark from Google for AI generated content verification and provenance tracking. " + b"\x00" * 20
        tmp_path.write_bytes(b"\xff\xd8" + b"\x00" * 50 + context)
        result = analyze_metadata_forensic(tmp_path)
        self.assertTrue(result.has_synthid)
        self.assertGreater(result.score, 0)
        tmp_path.unlink(missing_ok=True)

    def test_watermark_detection(self) -> None:
        """Files with watermark marker should be detected."""
        tmp_path = Path("/tmp") / "test_watermark.jpg"
        tmp_path.write_bytes(b"\xff\xd8" + b"\x00" * 100 + b"Adobe Firefly" + b"\x00" * 100)
        result = analyze_metadata_forensic(tmp_path)
        self.assertTrue(result.has_watermark)
        self.assertGreater(result.score, 0)
        tmp_path.unlink(missing_ok=True)

    def test_provenance_record_dataclass(self) -> None:
        """ProvenanceRecord should be a valid dataclass."""
        record = ProvenanceRecord(
            standard="C2PA",
            provider="CAI",
            signed=True,
            claim_url="https://example.com",
            details={"key": "value"},
        )
        self.assertEqual(record.standard, "C2PA")
        self.assertEqual(record.provider, "CAI")
        self.assertTrue(record.signed)
        self.assertEqual(record.claim_url, "https://example.com")

    def test_provenance_record_to_json(self) -> None:
        """ProvenanceRecord to_json should return a dictionary."""
        record = ProvenanceRecord(
            standard="C2PA",
            provider="CAI",
            signed=True,
            claim_url=None,
            details={},
        )
        data = record.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("standard", data)
        self.assertIn("provider", data)
        self.assertIn("signed", data)


if __name__ == "__main__":
    unittest.main()
