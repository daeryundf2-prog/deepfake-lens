"""Tests for the multimodal analysis module."""

from __future__ import annotations

import unittest

from deepfake_lens.multimodal import (
    MultimodalAnalysis,
    MultimodalEvidenceSignal,
    analyze_multimodal,
)


class MultimodalAnalysisTest(unittest.TestCase):
    """Test cases for multimodal analysis functions."""

    def test_analyze_multimodal_returns_result(self) -> None:
        """analyze_multimodal should return a MultimodalAnalysis."""
        result = analyze_multimodal(image_score=80, text_score=70)
        self.assertIsInstance(result, MultimodalAnalysis)

    def test_empty_modalities_returns_zero(self) -> None:
        """No modalities should return zero score."""
        result = analyze_multimodal()
        self.assertEqual(result.score, 0)
        self.assertEqual(result.modalities_used, [])

    def test_single_modality_returns_neutral(self) -> None:
        """Single modality should return neutral consistency."""
        result = analyze_multimodal(image_score=50)
        self.assertEqual(len(result.modalities_used), 1)
        self.assertEqual(result.consistency_score, 0.5)

    def test_consistent_high_scores(self) -> None:
        """Consistent high scores should increase score."""
        result = analyze_multimodal(image_score=80, text_score=75, audio_score=78)
        self.assertGreater(result.score, 75)
        self.assertGreater(result.consistency_score, 0.8)

    def test_consistent_low_scores(self) -> None:
        """Consistent low scores should keep score low."""
        result = analyze_multimodal(image_score=20, text_score=25, audio_score=22)
        self.assertLess(result.score, 35)
        self.assertGreater(result.consistency_score, 0.8)

    def test_inconsistent_scores_detected(self) -> None:
        """Inconsistent scores should be detected."""
        result = analyze_multimodal(image_score=90, text_score=10)
        self.assertGreater(len(result.signals), 0)
        # Should have inconsistency signal
        inconsistency_signals = [s for s in result.signals if "불일치" in s.title]
        self.assertGreater(len(inconsistency_signals), 0)

    def test_source_guess_consistency(self) -> None:
        """Matching source guesses should increase consistency."""
        result = analyze_multimodal(
            image_score=70,
            text_score=65,
            image_source_guess="DALL-E",
            text_source_guess="DALL-E",
        )
        self.assertGreater(result.consistency_score, 0.8)

    def test_source_guess_inconsistency(self) -> None:
        """Different source guesses should be detected."""
        result = analyze_multimodal(
            image_score=70,
            text_score=65,
            image_source_guess="DALL-E",
            text_source_guess="Midjourney",
        )
        inconsistency_signals = [s for s in result.signals if "출처 불일치" in s.title]
        self.assertGreater(len(inconsistency_signals), 0)

    def test_ai_probability_range(self) -> None:
        """AI probability should be between 0 and 1."""
        result = analyze_multimodal(image_score=80, text_score=70)
        self.assertGreaterEqual(result.overall_ai_probability, 0.0)
        self.assertLessEqual(result.overall_ai_probability, 1.0)

    def test_high_score_returns_high_band(self) -> None:
        """High scores should return high band."""
        result = analyze_multimodal(image_score=90, text_score=85, audio_score=88)
        self.assertEqual(result.band, "high")
        self.assertEqual(result.band_label, "높음")

    def test_low_score_returns_low_band(self) -> None:
        """Low scores should return low band."""
        result = analyze_multimodal(image_score=10, text_score=15, audio_score=12)
        self.assertEqual(result.band, "low")
        self.assertEqual(result.band_label, "낮음")

    def test_to_json_returns_dict(self) -> None:
        """to_json should return a dictionary."""
        result = analyze_multimodal(image_score=50, text_score=60)
        data = result.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("score", data)
        self.assertIn("band", data)
        self.assertIn("verdict", data)
        self.assertIn("modalities_used", data)
        self.assertIn("consistency_score", data)

    def test_multimodal_evidence_signal(self) -> None:
        """MultimodalEvidenceSignal should be a valid dataclass."""
        signal = MultimodalEvidenceSignal(
            title="Test",
            detail="Test detail",
            weight=10,
            source_modality="image",
        )
        self.assertEqual(signal.title, "Test")
        self.assertEqual(signal.source_modality, "image")


if __name__ == "__main__":
    unittest.main()
