"""Tests for faceswap boundary seam and multi-cue localized manipulation detector."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

try:
    import cv2
    import numpy as np

    HAVE_CV2 = True
except ImportError:
    np = None  # type: ignore[assignment]
    HAVE_CV2 = False

from deepfake_lens.calibration import (
    MIN_CALIBRATION_SAMPLES,
    THRESHOLD_PROFILE_VERSION,
    ThresholdProfile,
    load_threshold_profile,
    write_threshold_profile,
)
from deepfake_lens.cli import main
from deepfake_lens.core import _deep_image_layers
from deepfake_lens.faceswap_seam import (
    FaceSwapSeamAnalysis,
    SEAM_THRESHOLDS,
    _resolve_thresholds,
    analyze_faceswap_seam,
)


class FaceSwapSeamTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_missing_file_returns_graceful_analysis(self) -> None:
        analysis = analyze_faceswap_seam(self.root / "nonexistent.jpg")
        self.assertEqual(analysis.score, 0)
        self.assertEqual(analysis.band, "unknown")
        self.assertTrue(len(analysis.limitations) > 0)

    @unittest.skipUnless(HAVE_CV2, "opencv required")
    def test_undersized_faces_report_unknown_not_low(self) -> None:
        """Faces below the analysis resolution must not yield a 'low risk' verdict."""
        from unittest.mock import patch

        from deepfake_lens.face import FaceRegion
        import deepfake_lens.faceswap_seam as seam_mod

        img = np.full((300, 300, 3), 128, dtype=np.uint8)
        img_path = self.root / "small_face.png"
        cv2.imwrite(str(img_path), img)

        tiny = FaceRegion(x=10, y=10, width=20, height=20, landmarks=[], confidence=0.9)
        with patch.object(seam_mod, "_detect_faces", return_value=[tiny]):
            analysis = analyze_faceswap_seam(img_path)

        self.assertEqual(analysis.band, "unknown")
        self.assertEqual(analysis.face_count, 1)

    @unittest.skipUnless(HAVE_CV2, "opencv required")
    def test_blank_image_reports_no_faces(self) -> None:
        # Create solid gray image
        blank = np.full((300, 300, 3), 128, dtype=np.uint8)
        blank_path = self.root / "blank.png"
        cv2.imwrite(str(blank_path), blank)

        analysis = analyze_faceswap_seam(blank_path)
        self.assertEqual(analysis.face_count, 0)
        self.assertEqual(analysis.band, "unknown")
        self.assertEqual(analysis.score, 0)

    @unittest.skipUnless(HAVE_CV2, "opencv required")
    def test_synthetic_faceswap_with_simulated_seam(self) -> None:
        # Generate an image with a drawn synthetic face and artificial boundary seam
        img = np.full((400, 400, 3), 100, dtype=np.uint8)
        # Background noise
        noise = np.random.normal(0, 5, (400, 400, 3)).astype(np.int16)
        img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)

        # Draw face ellipse
        center = (200, 180)
        axes = (80, 100)
        cv2.ellipse(img, center, axes, 0, 0, 360, (180, 190, 220), -1)

        # Draw eyes (dark circles)
        cv2.circle(img, (170, 160), 12, (50, 50, 50), -1)
        cv2.circle(img, (230, 160), 12, (50, 50, 50), -1)
        # Pupils / specular highlights
        cv2.circle(img, (172, 158), 3, (255, 255, 255), -1)
        cv2.circle(img, (226, 164), 3, (255, 255, 255), -1)  # Asymmetric highlight

        # Draw nose & mouth
        cv2.line(img, (200, 175), (200, 205), (120, 120, 150), 3)
        cv2.ellipse(img, (200, 230), (25, 10), 0, 0, 180, (80, 80, 180), -1)

        # Introduce high-contrast seam border around ellipse (Poisson artifact)
        cv2.ellipse(img, center, axes, 0, 0, 360, (0, 0, 0), 2)

        test_path = self.root / "synthetic_face.png"
        cv2.imwrite(str(test_path), img)

        analysis = analyze_faceswap_seam(test_path)
        self.assertIsInstance(analysis, FaceSwapSeamAnalysis)
        self.assertIn("score", analysis.to_json())
        self.assertIn("band", analysis.to_json())
        self.assertIn("signals", analysis.to_json())

    @unittest.skipUnless(HAVE_CV2, "opencv required")
    def test_cli_faceswap_seam_json_output(self) -> None:
        img = np.full((200, 200, 3), 150, dtype=np.uint8)
        test_path = self.root / "sample_face.png"
        cv2.imwrite(str(test_path), img)

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["faceswap-seam", str(test_path), "--format", "json"])
        self.assertEqual(code, 0)
        output = json.loads(buf.getvalue())
        self.assertIn("score", output)
        self.assertIn("band", output)
        self.assertIn("verdict", output)

    @unittest.skipUnless(HAVE_CV2, "opencv required")
    def test_core_deep_image_layers_integration(self) -> None:
        img = np.full((200, 200, 3), 150, dtype=np.uint8)
        test_path = self.root / "deep_sample.png"
        cv2.imwrite(str(test_path), img)

        # G12: deep layers now return evidence + per-layer coverage instead
        # of a (signals, limitations) pair.
        layers = _deep_image_layers(test_path)
        self.assertIsInstance(layers.evidence, list)
        self.assertIsInstance(layers.limitations, list)
        self.assertEqual([entry.check for entry in layers.coverage], ["face_manipulation", "inpaint", "faceswap_seam"])
        face_entry = layers.coverage[0]
        # A flat grey image has no face: recorded as a skip with a reason.
        self.assertEqual(face_entry.status.value, "skipped")
        self.assertEqual(face_entry.reason, "얼굴 미검출")


class ThresholdProfileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_roundtrip_and_lookup(self) -> None:
        profile = ThresholdProfile(
            version=THRESHOLD_PROFILE_VERSION,
            values={"faceswap_seam.seam_ratio_high": 4.1},
            samples=200,
            dataset_fingerprint="abc123",
            measured_at="2026-09-28",
        )
        out = self.root / "thresholds.json"
        write_threshold_profile(out, profile)
        loaded = load_threshold_profile(out)
        self.assertIsNotNone(loaded)
        self.assertFalse(loaded.provisional)
        self.assertEqual(loaded.value("faceswap_seam.seam_ratio_high", 3.2), 4.1)
        self.assertEqual(loaded.value("faceswap_seam.chroma_delta", 18.0), 18.0)

    def test_small_sample_profile_is_provisional(self) -> None:
        profile = ThresholdProfile(
            version=THRESHOLD_PROFILE_VERSION, values={}, samples=MIN_CALIBRATION_SAMPLES - 1
        )
        self.assertTrue(profile.provisional)

    def test_low_auroc_profile_is_provisional(self) -> None:
        # A measured corpus whose aggregate score cannot beat chance must not
        # ship as "measured" — the AUROC floor keeps it provisional regardless
        # of sample count.
        profile = ThresholdProfile(
            version=THRESHOLD_PROFILE_VERSION,
            values={},
            samples=MIN_CALIBRATION_SAMPLES + 100,
            metrics={"score_auroc": 0.47},
        )
        self.assertTrue(profile.provisional)
        self.assertIn("score_auroc", profile.provisional_reason or "")

        good = ThresholdProfile(
            version=THRESHOLD_PROFILE_VERSION,
            values={},
            samples=MIN_CALIBRATION_SAMPLES + 100,
            metrics={"score_auroc": 0.9},
        )
        self.assertFalse(good.provisional)
        self.assertIsNone(good.provisional_reason)

    def test_wrong_version_rejected(self) -> None:
        out = self.root / "bad.json"
        out.write_text(json.dumps({"version": "other", "values": {}}), encoding="utf-8")
        self.assertIsNone(load_threshold_profile(out))

    def test_resolve_defaults_and_overrides(self) -> None:
        t, provisional = _resolve_thresholds(None)
        self.assertEqual(t("seam_ratio_high"), 3.2)
        self.assertFalse(provisional)

        t, _ = _resolve_thresholds({"seam_ratio_high": 9.9, "bogus_key": 1})
        self.assertEqual(t("seam_ratio_high"), 9.9)
        self.assertEqual(t("chroma_delta"), SEAM_THRESHOLDS["chroma_delta"])

        profile = ThresholdProfile(
            version=THRESHOLD_PROFILE_VERSION,
            values={"faceswap_seam.chroma_delta": 7.5},
            samples=100,
        )
        t, _ = _resolve_thresholds(profile)
        self.assertEqual(t("chroma_delta"), 7.5)
        self.assertEqual(t("seam_ratio_high"), 3.2)

    @unittest.skipUnless(HAVE_CV2, "opencv required")
    def test_provisional_profile_adds_limitation(self) -> None:
        from unittest.mock import patch

        from deepfake_lens.face import FaceRegion
        import deepfake_lens.faceswap_seam as seam_mod

        face = FaceRegion(x=60, y=60, width=120, height=120, landmarks=[], confidence=0.9)
        img = np.full((300, 300, 3), 128, dtype=np.uint8)
        profile = ThresholdProfile(
            version=THRESHOLD_PROFILE_VERSION, values={}, samples=3
        )
        with patch.object(seam_mod, "_detect_faces", return_value=[face]):
            analysis = analyze_faceswap_seam("ignored.png", image_matrix=img, thresholds=profile)
        self.assertTrue(any("provisional" in lim or "임시" in lim for lim in analysis.limitations))
