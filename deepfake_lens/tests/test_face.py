"""Tests for the face manipulation detection module."""

from __future__ import annotations

import unittest
from pathlib import Path

from deepfake_lens.face import (
    FaceAnalysis,
    FaceRegion,
    analyze_faces,
    _classify_manipulation_type,
    _calculate_confidence,
    _estimate_landmarks,
    _face_landmarks,
)


def _has_cv2() -> bool:
    try:
        import cv2  # noqa: F401

        return True
    except ImportError:
        return False


def _has_mediapipe() -> bool:
    try:
        import mediapipe  # noqa: F401

        return True
    except ImportError:
        return False


class FaceAnalysisTest(unittest.TestCase):
    """Test cases for face analysis functions."""

    def test_nonexistent_file_returns_error(self) -> None:
        """Analysis of nonexistent file should return error analysis."""
        result = analyze_faces(Path("/nonexistent/image.jpg"))
        self.assertEqual(result.score, 0)
        self.assertEqual(result.band, "unknown")
        self.assertIn("존재하지 않습니다", result.verdict)

    def test_unsupported_format_returns_error(self) -> None:
        """Analysis of unsupported format should return error analysis."""
        tmp_path = Path("/tmp") / "test.txt"
        tmp_path.write_bytes(b"not image")
        result = analyze_faces(tmp_path)
        self.assertEqual(result.score, 0)
        self.assertIn("지원하지 않는", result.verdict)
        tmp_path.unlink(missing_ok=True)

    def test_analysis_returns_dataclass(self) -> None:
        """Analysis should return a FaceAnalysis dataclass."""
        result = analyze_faces(Path("nonexistent.jpg"))
        self.assertIsInstance(result, FaceAnalysis)

    def test_to_json_returns_dict(self) -> None:
        """to_json should return a dictionary."""
        result = analyze_faces(Path("nonexistent.jpg"))
        data = result.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("score", data)
        self.assertIn("band", data)
        self.assertIn("verdict", data)
        self.assertIn("face_count", data)
        self.assertIn("manipulation_type", data)

    def test_dead_geometry_checks_are_gone(self) -> None:
        """Landmark-derived checks were provably dead code (synthesized
        landmarks made every relation a constant) and must not return."""
        import deepfake_lens.face as face_module

        self.assertFalse(hasattr(face_module, "_landmark_consistency"))
        self.assertFalse(hasattr(face_module, "_symmetry_analysis"))

    @unittest.skipUnless(_has_cv2(), "opencv not installed")
    def test_circular_hue_same_red_family_does_not_fire(self) -> None:
        """Face hue 5 vs surround hue 175 is the same red family on the
        OpenCV hue circle and must not read as a 170-unit mismatch."""
        import cv2
        import numpy as np

        from deepfake_lens.face import FaceRegion, _color_temperature

        image = np.zeros((120, 120, 3), dtype=np.uint8)
        image[:, :, 2] = 200  # reddish background (BGR)
        face = FaceRegion(x=10, y=10, width=60, height=60, landmarks=[], confidence=0.9)
        result = _color_temperature(face, image)
        self.assertIsNone(result)

    def test_classify_manipulation_type_swap(self) -> None:
        """Boundary blending signals should classify as face_swap."""
        from deepfake_lens.face import FaceEvidenceSignal

        signals = [FaceEvidenceSignal("경계 블렌딩 의심", "test", 18)]
        result = _classify_manipulation_type(signals)
        self.assertEqual(result, "face_swap")

    def test_classify_manipulation_type_reenactment(self) -> None:
        """Reflection signals should classify as reenactment."""
        from deepfake_lens.face import FaceEvidenceSignal

        signals = [FaceEvidenceSignal("반사 패턴 불일치", "test", 15)]
        result = _classify_manipulation_type(signals)
        self.assertEqual(result, "reenactment")

    def test_classify_manipulation_type_unknown(self) -> None:
        """No signals should classify as unknown."""
        result = _classify_manipulation_type([])
        self.assertEqual(result, "unknown")

    def test_calculate_confidence_high(self) -> None:
        """High score with multiple signals should be high confidence."""
        result = _calculate_confidence(80, 1, 3)
        self.assertEqual(result, "high")

    def test_calculate_confidence_medium(self) -> None:
        """Medium score should be medium confidence."""
        result = _calculate_confidence(50, 1, 1)
        self.assertEqual(result, "medium")

    def test_calculate_confidence_low(self) -> None:
        """Low score should be low confidence."""
        result = _calculate_confidence(20, 1, 0)
        self.assertEqual(result, "low")

    def test_assumed_landmarks_are_box_constants(self) -> None:
        """The assumed eye/nose/mouth anchors are fixed box fractions; they
        anchor eye-region sampling only and imply nothing about geometry."""
        landmarks = _estimate_landmarks(100, 100, 200, 200)
        self.assertEqual(landmarks, [(170, 170), (230, 170), (200, 210), (200, 250)])

    def test_landmarks_source_defaults_to_box_estimate(self) -> None:
        """A FaceRegion without an explicit source must not masquerade as
        measured geometry."""
        region = FaceRegion(x=0, y=0, width=10, height=10, landmarks=[], confidence=0.9)
        self.assertEqual(region.landmarks_source, "box-ratio-estimate")

    @unittest.skipIf(_has_mediapipe(), "mediapipe installed — fallback path not exercised")
    def test_face_landmarks_falls_back_to_labelled_box_estimate(self) -> None:
        """Without mediapipe, _face_landmarks must still return anchors but
        label them as box-ratio estimates, never as measured landmarks."""
        import numpy as np

        image = np.zeros((80, 80, 3), dtype=np.uint8)
        landmarks, source = _face_landmarks(image, 10, 10, 40, 40)
        self.assertEqual(source, "box-ratio-estimate")
        self.assertEqual(landmarks, _estimate_landmarks(10, 10, 40, 40))

    @unittest.skipIf(_has_mediapipe(), "mediapipe installed")
    def test_mediapipe_landmarks_none_without_package(self) -> None:
        """The MediaPipe path must degrade to None when the extra is absent."""
        import numpy as np

        from deepfake_lens.face import _mediapipe_landmarks

        image = np.zeros((80, 80, 3), dtype=np.uint8)
        self.assertIsNone(_mediapipe_landmarks(image, 10, 10, 40, 40))


if __name__ == "__main__":
    unittest.main()
