"""Tests for the duplicate-path consolidation (docs/consolidation-notes.md).

Two contracts pinned here:

* ``pixel_analyzer.analyze_pixels`` is formally the fast *pre-screen* tier
  (``analysis_tier="pre-screen"``) and must never be confused with the
  scan-pipeline ensemble in ``pixel.py`` (``analysis_tier="ensemble"``).
* ``enhanced_forensics.analyze_forensic`` delegates provenance scanning to
  ``c2pa.analyze_metadata_forensic``, so a raw ``b"c2pa"`` substring can no
  longer outscore an SDK-verified manifest (the old local scan gave it
  0.9).
"""

from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from dataclasses import asdict
from pathlib import Path

from deepfake_lens.enhanced_forensics import analyze_forensic
from deepfake_lens.pixel import analyze_image_pixels
from deepfake_lens.pixel_analyzer import QuickPixelAnalysis, analyze_pixels


def _has_cv2() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401

        return True
    except ImportError:
        return False


def _has_c2pa_sdk() -> bool:
    try:
        import c2pa  # noqa: F401

        return True
    except ImportError:
        return False


def _chunk(kind: bytes, payload: bytes) -> bytes:
    import binascii

    checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return len(payload).to_bytes(4, "big") + kind + payload + checksum.to_bytes(4, "big")


def _write_rgb_png(path: Path, width: int, height: int, pixel_at) -> None:
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row.extend(pixel_at(x, y))
        rows.append(bytes(row))
    compressed = zlib.compress(b"".join(rows))
    ihdr = _chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + ihdr + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b""))


def _jpeg_with_app1_exif() -> bytes:
    exif_payload = b"Exif\x00\x00" + b"\x00" * 24
    app1 = b"\xff\xe1" + struct.pack(">H", len(exif_payload) + 2) + exif_payload
    return b"\xff\xd8" + app1 + b"\xff\xd9"


class PreScreenTierTest(unittest.TestCase):
    """pixel_analyzer is the labelled pre-screen tier; pixel.py is the
    ensemble tier. The labels are result metadata, not scoring changes."""

    def test_error_result_is_labelled_pre_screen(self) -> None:
        result = analyze_pixels(Path("/nonexistent/image.png"))
        self.assertIsInstance(result, QuickPixelAnalysis)
        self.assertEqual(result.analysis_tier, "pre-screen")
        self.assertEqual(result.band, "unknown")

    def test_pre_screen_label_survives_json(self) -> None:
        result = analyze_pixels(Path("/nonexistent/image.png"))
        self.assertEqual(result.to_json()["analysis_tier"], "pre-screen")

    @unittest.skipUnless(_has_cv2(), "opencv/numpy not installed")
    def test_real_image_result_is_labelled_pre_screen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.png"
            _write_rgb_png(path, 32, 32, lambda x, y: (200, 200, 200) if (x + y) % 2 else (40, 40, 40))
            result = analyze_pixels(path)
            self.assertEqual(result.analysis_tier, "pre-screen")
            self.assertTrue(any("pre-screen" in line for line in result.limitations))

    def test_ensemble_result_is_labelled_ensemble(self) -> None:
        """The canonical pixel path reports the ensemble tier — and the
        pixel.py field round-trips through scan JSON deserialization."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tile.png"
            _write_rgb_png(path, 32, 32, lambda x, y: (220, 220, 220) if (x // 8 + y // 8) % 2 == 0 else (30, 30, 30))
            result = analyze_image_pixels(path, mode="fast")
            self.assertTrue(result.available)
            self.assertEqual(result.analysis_tier, "ensemble")
            self.assertEqual(asdict(result)["analysis_tier"], "ensemble")

    def test_ensemble_tier_round_trips_through_scan_json(self) -> None:
        from deepfake_lens.core import _pixel_analysis_from_json

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tile.png"
            _write_rgb_png(path, 32, 32, lambda x, y: (200, 200, 200))
            result = analyze_image_pixels(path, mode="fast")
            restored = _pixel_analysis_from_json(asdict(result))
            self.assertEqual(restored.analysis_tier, "ensemble")

    def test_cached_results_without_tier_default_to_ensemble(self) -> None:
        """Cache entries written before the field existed still load."""
        from deepfake_lens.core import _pixel_analysis_from_json

        restored = _pixel_analysis_from_json({"mode": "fast", "available": True, "score": 12})
        self.assertEqual(restored.analysis_tier, "ensemble")


class ForensicDelegationTest(unittest.TestCase):
    """analyze_forensic must inherit the c2pa.py calibration: verified
    manifest > unverified manifest > bare byte string."""

    def test_raw_c2pa_substring_is_hint_not_manifest(self) -> None:
        """A bare b"c2pa" byte match must never reach 0.9 — the conflict the
        consolidation removed. Without the SDK it is a labelled 0.3 string
        hint; with the SDK the manifest is authoritatively absent."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "marker.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64 + b"c2pa" + b"\x00" * 64)
            report = analyze_forensic(path)
            c2pa_evidence = [e for e in report.evidences if e.evidence_type.startswith("c2pa")]
            if _has_c2pa_sdk():
                self.assertFalse(c2pa_evidence)
                return
            self.assertTrue(c2pa_evidence)
            self.assertEqual(c2pa_evidence[0].evidence_type, "c2pa_marker")
            self.assertLessEqual(c2pa_evidence[0].confidence, 0.3)
            self.assertNotIn("검증되었습니다", c2pa_evidence[0].description)

    def test_tool_string_is_provenance_marker_not_watermark(self) -> None:
        """Tool-identifying strings are attribution hints (<=0.4), not the
        old 0.7 'watermark' finding."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tool.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64 + b"Adobe Firefly" + b"\x00" * 64)
            report = analyze_forensic(path)
            watermark_evidence = [e for e in report.evidences if e.evidence_type == "watermark"]
            self.assertFalse(watermark_evidence)
            marker_evidence = [e for e in report.evidences if e.evidence_type == "provenance_marker"]
            self.assertTrue(marker_evidence)
            self.assertLessEqual(marker_evidence[0].confidence, 0.4)
            self.assertIn("아닙니다", marker_evidence[0].description)

    def test_jpeg_app1_exif_still_reported(self) -> None:
        """The delegated path still surfaces EXIF metadata evidence."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "exif.jpg"
            path.write_bytes(_jpeg_with_app1_exif())
            report = analyze_forensic(path)
            exif_evidence = [e for e in report.evidences if e.evidence_type == "exif_metadata"]
            self.assertTrue(exif_evidence)
            self.assertEqual(exif_evidence[0].confidence, 0.5)

    def test_structure_evidence_preserved(self) -> None:
        """File-structure findings (not part of the provenance scan) stay."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tiny.bin"
            path.write_bytes(b"tiny")
            report = analyze_forensic(path)
            self.assertTrue(any(e.evidence_type == "file_size" for e in report.evidences))

    def test_report_packaging_unchanged(self) -> None:
        """The packaging role — hash, report ID, checksum, legal text — is
        untouched by the delegation."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("forensic sample", encoding="utf-8")
            report = analyze_forensic(path)
            self.assertTrue(report.report_id.startswith("FR-"))
            self.assertTrue(report.integrity_checksum)
            self.assertEqual(len(report.file_hash), 64)
            text = report.generate_legal_text()
            self.assertIn("포렌식 분석 보고서", text)
            self.assertIn("무결성 체크섬", text)


if __name__ == "__main__":
    unittest.main()
