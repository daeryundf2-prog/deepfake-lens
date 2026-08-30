"""Tests for the C2PA forensics module.

The byte-scan fallback assertions pin the HONEST behavior: marker strings
are reference-level hints, never manifest validation, and SynthID is never
"detected" from bytes (it is a pixel-domain watermark). The SDK tests run
only when c2pa-python is installed (pip install 'deepfake-lens[provenance]').
"""

from __future__ import annotations

import unittest
from pathlib import Path

from deepfake_lens.c2pa import (
    MetadataForensicAnalysis,
    ProvenanceRecord,
    analyze_metadata_forensic,
    validate_c2pa_manifest,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
C2PA_FIXTURES = REPO_ROOT / "fixtures" / "c2pa-test"


def _has_c2pa_sdk() -> bool:
    try:
        import c2pa  # noqa: F401

        return True
    except ImportError:
        return False


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

    def test_c2pa_marker_is_reference_level_hint(self) -> None:
        """Without the SDK, a byte-marker match is a hint, not manifest
        validation: the signal wording must say so and its weight stays low.
        With the SDK installed the result is authoritative — a bare marker
        string is not a manifest, so has_c2pa stays False."""
        tmp_path = Path("/tmp") / "test_c2pa_marker.jpg"
        tmp_path.write_bytes(b"\xff\xd8" + b"\x00" * 100 + b"c2pa" + b"\x00" * 100)
        try:
            result = analyze_metadata_forensic(tmp_path)
            if _has_c2pa_sdk():
                self.assertFalse(result.has_c2pa)
                return
            self.assertTrue(result.has_c2pa)
            self.assertGreater(result.score, 0)
            self.assertLessEqual(result.score, 10)  # reference weight only
            c2pa_signals = [s for s in result.signals if "C2PA" in s.title]
            self.assertTrue(c2pa_signals)
            self.assertIn("문자열", c2pa_signals[0].title)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_google_metadata_never_claims_synthid(self) -> None:
        """Google tool strings are attribution hints; SynthID (a pixel-domain
        watermark) must never be reported as detected from bytes."""
        tmp_path = Path("/tmp") / "test_synthid.jpg"
        context = b"This is a SynthID watermark from Google for AI generated content verification and provenance tracking. " + b"\x00" * 20
        tmp_path.write_bytes(b"\xff\xd8" + b"\x00" * 50 + context)
        try:
            result = analyze_metadata_forensic(tmp_path)
            self.assertFalse(result.has_synthid)
            self.assertTrue(any("SynthID" in line for line in result.limitations))
            google_records = [r for r in result.provenance_records if r.provider == "Google"]
            self.assertTrue(google_records)
            self.assertEqual(google_records[0].standard, "ToolMetadata")
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_watermark_marker_is_tool_attribution(self) -> None:
        """Tool strings in metadata are attribution hints, not cryptographic
        watermark verification."""
        tmp_path = Path("/tmp") / "test_watermark.jpg"
        tmp_path.write_bytes(b"\xff\xd8" + b"\x00" * 100 + b"Adobe Firefly" + b"\x00" * 100)
        try:
            result = analyze_metadata_forensic(tmp_path)
            self.assertTrue(result.has_watermark)
            self.assertLessEqual(result.score, 25)
            tool_signals = [s for s in result.signals if "식별 문자열" in s.title]
            self.assertTrue(tool_signals)
        finally:
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


class C2paSdkValidationTest(unittest.TestCase):
    """Real manifest validation via the official SDK (needs the extra).

    The vendored fixture is signed by a test-only CA that is NOT in the
    SDK's default trust store, so the deterministic, meaningful behavior to
    pin is: the manifest is read and reported faithfully, and an untrusted
    signer is surfaced as an incomplete validation — never as success.
    (Adding the vendored CA to a deployment trust store is an operator
    concern; see fixtures/c2pa-test/README.md.)
    """

    @unittest.skipUnless(_has_c2pa_sdk(), "c2pa-python not installed")
    def test_signed_fixture_manifest_is_read_and_reported(self) -> None:
        summary = validate_c2pa_manifest(C2PA_FIXTURES / "signed-c2pa.png")
        self.assertIsNotNone(summary)
        self.assertTrue(summary["present"])
        self.assertIn(str(summary["state"]).lower(), {"valid", "validwithwarnings", "invalid", "untrusted"})
        self.assertTrue(summary["signature"])
        self.assertEqual(summary["signature"]["common_name"], "DeepfakeLensTestSigner")
        self.assertIn("signingCredential.untrusted", summary["failure_codes"])
        self.assertFalse(summary["trusted"])

        analysis = analyze_metadata_forensic(C2PA_FIXTURES / "signed-c2pa.png")
        self.assertTrue(analysis.has_c2pa)
        validated = [s for s in analysis.signals if s.title == "C2PA 매니페스트 검증됨"]
        incomplete = [s for s in analysis.signals if s.title == "C2PA 매니페스트 검증 미완료"]
        # Untrusted signer: must NOT claim full validation.
        self.assertFalse(validated)
        self.assertTrue(incomplete)
        provenance = [r for r in analysis.provenance_records if r.standard == "C2PA"]
        self.assertTrue(provenance and provenance[0].details["state"])

    @unittest.skipUnless(_has_c2pa_sdk(), "c2pa-python not installed")
    def test_unsigned_image_reports_absent_manifest(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "plain.png"
            plain.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
            summary = validate_c2pa_manifest(plain)
            self.assertIsNotNone(summary)
            self.assertFalse(summary["present"])

    @unittest.skipUnless(_has_c2pa_sdk(), "c2pa-python not installed")
    def test_untrusted_signer_is_reported_not_fabricated(self) -> None:
        """Without the vendored CA in the trust store the SDK state is not
        'valid' and the analysis must say so instead of claiming success."""
        analysis = analyze_metadata_forensic(C2PA_FIXTURES / "signed-c2pa.png")
        self.assertTrue(analysis.has_c2pa)
        incomplete = [s for s in analysis.signals if s.title == "C2PA 매니페스트 검증 미완료"]
        self.assertTrue(incomplete)


if __name__ == "__main__":
    unittest.main()
