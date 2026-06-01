"""Tests for the face manipulation detection module."""

from __future__ import annotations

import unittest
from pathlib import Path

from deepfake_lens.face import (
    FaceAnalysis,
    FaceRegion,
    analyze_faces,
    _landmark_consistency,
    _symmetry_analysis,
    _classify_manipulation_type,
    _calculate_confidence,
)


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

    def test_landmark_consistency_asymmetric(self) -> None:
        """Asymmetric landmarks should generate signal."""
        face = FaceRegion(
            x=100, y=100, width=200, height=200,
            landmarks=[(130, 135), (170, 135), (185, 165), (150, 190)],  # Nose far right
            confidence=0.9,
        )
        signal = _landmark_consistency(face)
        self.assertIsNotNone(signal)
        self.assertIn("랜드마크", signal.title)

    def test_landmark_consistency_normal(self) -> None:
        """Normal landmarks should not generate signal."""
        face = FaceRegion(
            x=100, y=100, width=200, height=200,
            landmarks=[(130, 135), (170, 135), (150, 165), (150, 190)],  # Centered, good distance
            confidence=0.9,
        )
        signal = _landmark_consistency(face)
        self.assertIsNone(signal)

    def test_symmetry_analysis_asymmetric(self) -> None:
        """Asymmetric eyes should generate signal."""
        face = FaceRegion(
            x=100, y=100, width=200, height=200,
            landmarks=[(110, 135), (180, 135)],  # Very asymmetric
            confidence=0.9,
        )
        signal = _symmetry_analysis(face)
        self.assertIsNotNone(signal)
        self.assertIn("대칭", signal.title)

    def test_symmetry_analysis_symmetric(self) -> None:
        """Symmetric eyes should not generate signal."""
        face = FaceRegion(
            x=100, y=100, width=200, height=200,
            landmarks=[(145, 135), (155, 135)],  # Symmetric
            confidence=0.9,
        )
        signal = _symmetry_analysis(face)
        self.assertIsNone(signal)

    def test_classify_manipulation_type_swap(self) -> None:
        """Landmark signals should classify as face_swap."""
        from deepfake_lens.face import FaceEvidenceSignal
        signals = [FaceEvidenceSignal("랜드마크 비대칭", "test", 20)]
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


if __name__ == "__main__":
    unittest.main()
