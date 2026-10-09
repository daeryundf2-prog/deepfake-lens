"""Tests for the C2PA forensics module.

The byte-scan fallback assertions pin the HONEST behavior: marker strings
are reference-level hints, never manifest validation, and SynthID is never
"detected" from bytes (it is a pixel-domain watermark). The SDK tests run
only when c2pa-python is installed (pip install 'deepfake-lens[provenance]').
"""

from __future__ import annotations

import json
import tempfile
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
        # D1: band/verdict renamed to reference_band/reference_note.
        self.assertEqual(result.reference_band, "unavailable")
        self.assertIn("존재하지 않습니다", result.reference_note)

    def test_empty_file_returns_error(self) -> None:
        """Analysis of empty file should return error analysis."""
        tmp_path = Path(tempfile.gettempdir()) / "empty.txt"
        tmp_path.write_bytes(b"")
        result = analyze_metadata_forensic(tmp_path)
        self.assertEqual(result.score, 0)
        self.assertIn("비어 있습니다", result.reference_note)
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
        # D1: no old-contract band/verdict keys; reference_* instead.
        self.assertIn("reference_band", data)
        self.assertIn("reference_note", data)
        for legacy in ("band", "band_label", "verdict"):
            self.assertNotIn(legacy, data)
        self.assertIn("has_c2pa", data)
        self.assertIn("has_synthid", data)
        self.assertIn("has_watermark", data)

    def test_c2pa_marker_is_reference_level_hint(self) -> None:
        """Without the SDK, a byte-marker match is a hint, not manifest
        validation: the signal wording must say so and its weight stays low.
        With the SDK installed the result is authoritative — a bare marker
        string is not a manifest, so has_c2pa stays False."""
        tmp_path = Path(tempfile.gettempdir()) / "test_c2pa_marker.jpg"
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
        tmp_path = Path(tempfile.gettempdir()) / "test_synthid.jpg"
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
        tmp_path = Path(tempfile.gettempdir()) / "test_watermark.jpg"
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

    @unittest.skipUnless(_has_c2pa_sdk(), "c2pa-python not installed")
    def test_video_container_parses_for_manifest(self) -> None:
        """MP4 is a supported C2PA container — the SDK must parse it and
        report 'no manifest' (not None, which would mean it couldn't run)."""
        import struct
        import tempfile

        # Minimal valid-ish mp4: ftyp box + free box — enough for the SDK
        # to open the container and find no JUMBF manifest.
        ftyp = bytes(24)[:4] + b"ftypmp42" + bytes(8) + b"mp42isom"
        with tempfile.TemporaryDirectory() as tmp:
            mp4 = Path(tmp) / "clip.mp4"
            mp4.write_bytes(ftyp + (8).to_bytes(4, "big") + b"free")
            summary = validate_c2pa_manifest(mp4)
        self.assertIsNotNone(summary)
        self.assertFalse(summary["present"])


class C2paReaderErrorTest(unittest.TestCase):
    """D8: a reader exception is ``unavailable`` + a failed check, never ``absent``."""

    @unittest.skipUnless(_has_c2pa_sdk(), "c2pa-python not installed")
    def test_no_manifest_is_absent(self) -> None:
        summary = validate_c2pa_manifest(Path(__file__).resolve().parents[2] / "fixtures" / "benchmark" / "a1111-metadata-marker.png")
        assert summary is not None
        self.assertEqual(summary["status"], "absent")
        self.assertFalse(summary["present"])

    @unittest.skipUnless(_has_c2pa_sdk(), "c2pa-python not installed")
    def test_reader_exception_is_unavailable(self) -> None:
        import tempfile

        signed = (C2PA_FIXTURES / "signed-c2pa.png").read_bytes()
        jumbf = signed.index(b"caBX")
        with tempfile.TemporaryDirectory() as tmp:
            cases = {
                "empty.png": b"",
                "cut-manifest.png": signed[: jumbf + 64],
            }
            for name, data in cases.items():
                with self.subTest(name=name):
                    path = Path(tmp) / name
                    path.write_bytes(data)
                    summary = validate_c2pa_manifest(path)
                    assert summary is not None
                    self.assertEqual(summary["status"], "unavailable", summary)
                    self.assertFalse(summary["present"])
                    # B6: the class part is the exception class, or "C2PA SDK 오류(<kind>)"
                    # for the SDK's private _C2pa<Kind> classes.
                    self.assertRegex(str(summary["error"]), r"^(?:\w+|C2PA SDK 오류\(\w+\)): ")

    @unittest.skipUnless(_has_c2pa_sdk(), "c2pa-python not installed")
    def test_unexpected_reader_exception_is_unavailable_and_check_failed(self) -> None:
        from unittest import mock

        from deepfake_lens.core import analyze_file
        from deepfake_lens.result_types import CoverageStatus, Verdict

        fixture = Path(__file__).resolve().parents[2] / "fixtures" / "benchmark" / "real-like-texture.png"
        with mock.patch("c2pa.Reader", side_effect=RuntimeError("jumbf parser crashed")):
            summary = validate_c2pa_manifest(fixture)
            with self.assertLogs("deepfake_lens.checks", level="ERROR"):
                item = analyze_file(fixture)
        assert summary is not None and item.result is not None
        self.assertEqual(summary["status"], "unavailable")
        # B6: an untranslated English library message never reaches the reason.
        self.assertEqual(summary["error"], "RuntimeError: 라이브러리 오류(RuntimeError) — 상세는 로그 참조")
        entry = next(c for c in item.result.coverage if c.check == "c2pa")
        self.assertEqual(entry.status, CoverageStatus.FAILED)
        self.assertIn("RuntimeError", entry.reason)
        self.assertEqual(item.result.verdict_code, Verdict.UNDETERMINED)
        # An unread C2PA block is not "no metadata" (D16).
        self.assertNotIn("메타데이터 부재", [e.title for e in item.result.evidence])

    @unittest.skipUnless(_has_c2pa_sdk(), "c2pa-python not installed")
    def test_c2pa_png_is_not_reported_as_missing_metadata(self) -> None:
        from deepfake_lens.core import analyze_file

        item = analyze_file(C2PA_FIXTURES / "signed-c2pa.png")
        assert item.result is not None
        titles = [e.title for e in item.result.evidence]
        self.assertIn("C2PA 매니페스트 존재(검증 미완료)", titles)
        self.assertNotIn("메타데이터 부재", titles)


class C2paUnreadableDiagnosticTest(unittest.TestCase):
    """R8: when the SDK fails (scan coverage ``c2pa failed``) the provenance
    diagnostic says "C2PA 판독 불가(<reason>)" with c2pa_status
    ``unavailable`` — never "C2PA 없음" or "출처 표준 메타데이터가 발견되지
    않았습니다"."""

    def _assert_unreadable(self, analysis: object, reason: str) -> None:
        payload = analysis.to_json()  # type: ignore[attr-defined]
        text = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(payload["c2pa_status"], "unavailable")
        self.assertEqual(payload["c2pa_error"], reason)
        self.assertIn(f"C2PA 판독 불가({reason})", payload["reference_note"])
        self.assertIn(f"C2PA 판독 불가({reason})", [signal["title"] for signal in payload["signals"]])
        self.assertTrue(any(lim.startswith(f"C2PA 판독 불가({reason})") for lim in payload["limitations"]))
        self.assertNotIn("C2PA 없음", text)
        self.assertNotIn("출처 표준 메타데이터가 발견되지 않았습니다", text)

    def test_sdk_reader_failure_is_unreadable_not_absent(self) -> None:
        from unittest import mock

        fixture = REPO_ROOT / "fixtures" / "benchmark" / "real-like-texture.png"
        reason = "RuntimeError: jumbf parser crashed"
        failed = {"present": False, "status": "unavailable", "error": reason, "error_kind": "reader_error"}
        with mock.patch("deepfake_lens.c2pa.validate_c2pa_manifest", return_value=failed):
            analysis = analyze_metadata_forensic(fixture)
        self._assert_unreadable(analysis, reason)

    def test_validation_failure_after_open_is_unreadable(self) -> None:
        from unittest import mock

        fixture = REPO_ROOT / "fixtures" / "benchmark" / "real-like-texture.png"
        reason = "ValueError: validation crashed"
        failed = {"present": True, "status": "unavailable", "error": reason, "error_kind": "validation_error"}
        with mock.patch("deepfake_lens.c2pa.validate_c2pa_manifest", return_value=failed):
            analysis = analyze_metadata_forensic(fixture)
        self._assert_unreadable(analysis, reason)
        self.assertNotIn("C2PA 매니페스트 검증 미완료", [signal.title for signal in analysis.signals])

    def test_absent_manifest_still_reads_absent(self) -> None:
        from unittest import mock

        fixture = REPO_ROOT / "fixtures" / "benchmark" / "real-like-texture.png"
        with mock.patch("deepfake_lens.c2pa.validate_c2pa_manifest", return_value={"present": False, "status": "absent", "error": ""}):
            analysis = analyze_metadata_forensic(fixture)
        self.assertEqual(analysis.c2pa_status, "absent")
        self.assertIn("C2PA 없음", analysis.reference_note)

    @unittest.skipUnless(_has_c2pa_sdk(), "c2pa-python not installed")
    def test_truncated_manifest_scan_and_diagnostic_agree(self) -> None:
        """Real SDK on a manifest cut mid-JUMBF: scan coverage c2pa failed and
        the forensic diagnostic says 판독 불가 with the same SDK error."""
        import tempfile

        from deepfake_lens.core import analyze_file
        from deepfake_lens.result_types import CoverageStatus

        signed = (C2PA_FIXTURES / "signed-c2pa.png").read_bytes()
        jumbf = signed.index(b"caBX")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cut-manifest.png"
            path.write_bytes(signed[: jumbf + 64])
            item = analyze_file(path)
            analysis = analyze_metadata_forensic(path)
        assert item.result is not None
        entry = next(c for c in item.result.coverage if c.check == "c2pa")
        self.assertEqual(entry.status, CoverageStatus.FAILED)
        self._assert_unreadable(analysis, analysis.c2pa_error)
        self.assertIn(analysis.c2pa_error, entry.reason)


if __name__ == "__main__":
    unittest.main()


def _tampered_fixture(where: str) -> bytes:
    """signed-c2pa.png with bytes changed after signing.

    ``asset``: one byte of pixel data in the IDAT chunk after the caBX
    manifest (CRC recomputed, so the PNG stays well-formed) — C2PA reports
    ``assertion.dataHash.mismatch``. ``assertion``: 64 bytes inverted in the
    middle of the manifest's embedded assertion data — ``assertion.hashedURI.mismatch``.
    """
    import struct
    import zlib

    data = bytearray((C2PA_FIXTURES / "signed-c2pa.png").read_bytes())
    chunks: dict[bytes, tuple[int, int]] = {}
    offset = 8
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset:offset + 4])
        chunks.setdefault(bytes(data[offset + 4:offset + 8]), (offset, length))
        offset += 12 + length
    if where == "asset":
        start, length = chunks[b"IDAT"]
        data[start + 8 + length // 2] ^= 0xFF
        crc = zlib.crc32(bytes(data[start + 4:start + 8 + length])) & 0xFFFFFFFF
        data[start + 8 + length:start + 12 + length] = struct.pack(">I", crc)
    else:
        start, length = chunks[b"caBX"]
        middle = start + 8 + length // 2
        for index in range(middle, middle + 64):
            data[index] ^= 0xFF
    return bytes(data)


class C2paIntegrityMismatchTest(unittest.TestCase):
    """N6: a valid-looking manifest over changed bytes is "C2PA 무결성 불일치"
    (매니페스트 해시 불일치 — 서명 이후 내용이 변경됨): deterministic, neutral,
    strong — never the untrusted-signer wording."""

    UNTRUSTED_WORDING = ("신뢰 저장소에 없는 서명자", "검증 미완료")

    def _check(self, where: str, code: str) -> None:
        from deepfake_lens.core import analyze_file
        from deepfake_lens.result_types import EvidenceDirection, EvidenceKind, EvidenceStrength, Verdict

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"tampered-{where}.png"
            path.write_bytes(_tampered_fixture(where))
            validation = validate_c2pa_manifest(path)
            assert validation is not None
            self.assertIn(code, validation.get("failure_codes") or [])
            item = analyze_file(path)
            forensic = analyze_metadata_forensic(path).to_json()
        assert item.result is not None
        c2pa_items = [e for e in item.result.evidence if e.layer == "c2pa"]
        self.assertEqual([e.title for e in c2pa_items], ["C2PA 무결성 불일치"])
        integrity = c2pa_items[0]
        self.assertTrue(integrity.detail.startswith("매니페스트 해시 불일치 — 서명 이후 내용이 변경됨"), integrity.detail)
        self.assertIn(code, integrity.detail)
        self.assertEqual(
            (integrity.kind, integrity.direction, integrity.strength),
            (EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.STRONG),
        )
        self.assertEqual(item.result.verdict_code, Verdict.UNDETERMINED)
        text = json.dumps(item.result.to_json(), ensure_ascii=False)
        for wording in self.UNTRUSTED_WORDING:
            self.assertNotIn(wording, text)
        forensic_text = json.dumps(forensic, ensure_ascii=False)
        self.assertIn("C2PA 무결성 불일치", forensic_text)
        self.assertIn("매니페스트 해시 불일치 — 서명 이후 내용이 변경됨", forensic_text)
        self.assertNotIn("신뢰 저장소에 없는 서명자", forensic_text)

    @unittest.skipUnless(_has_c2pa_sdk(), "c2pa-python not installed")
    def test_asset_bytes_changed_after_signing(self) -> None:
        self._check("asset", "assertion.dataHash.mismatch")

    @unittest.skipUnless(_has_c2pa_sdk(), "c2pa-python not installed")
    def test_assertion_bytes_changed_after_signing(self) -> None:
        self._check("assertion", "assertion.hashedURI.mismatch")

    def test_rule_on_synthetic_validation_records(self) -> None:
        """No SDK needed: the evidence rule on recorded validation dicts."""
        from deepfake_lens.evidence_rules import c2pa_evidence
        from deepfake_lens.result_types import EvidenceDirection, EvidenceStrength

        base = {
            "present": True, "status": "invalid", "state": "Invalid", "trusted": False,
            "signature": {"common_name": "Some Signer"},
            "success_codes": ["claimSignature.validated"],
        }
        tampered = c2pa_evidence({**base, "failure_codes": ["signingCredential.untrusted", "assertion.hashedURI.mismatch"]})
        self.assertEqual([(e.title, e.direction, e.strength) for e in tampered], [("C2PA 무결성 불일치", EvidenceDirection.NEUTRAL, EvidenceStrength.STRONG)])
        declared = c2pa_evidence({**base, "failure_codes": ["assertion.dataHash.mismatch"], "digital_source_types": ["http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"]})
        self.assertEqual(declared[0].title, "C2PA 무결성 불일치")
        self.assertEqual(declared[1].strength, EvidenceStrength.MODERATE)  # declaration kept, never strong
        untrusted = c2pa_evidence({**base, "failure_codes": ["signingCredential.untrusted"]})
        self.assertEqual(untrusted[0].title, "C2PA 매니페스트 존재(검증 미완료)")
