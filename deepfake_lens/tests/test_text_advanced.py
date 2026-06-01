"""Tests for the advanced text analysis module."""

from __future__ import annotations

import unittest

from deepfake_lens.text_advanced import (
    TextAdvancedAnalysis,
    analyze_text_advanced,
)


class TextAdvancedAnalysisTest(unittest.TestCase):
    """Test cases for advanced text analysis functions."""

    def test_empty_text_returns_unknown(self) -> None:
        """Empty text should return unknown analysis."""
        result = analyze_text_advanced("")
        self.assertEqual(result.score, 0)
        self.assertEqual(result.band, "unknown")
        self.assertIn("비어 있습니다", result.verdict)

    def test_short_text_returns_low(self) -> None:
        """Short text should return low score."""
        result = analyze_text_advanced("Hello world")
        self.assertEqual(result.score, 0)
        self.assertEqual(result.band, "low")

    def test_analysis_returns_dataclass(self) -> None:
        """Analysis should return a TextAdvancedAnalysis dataclass."""
        result = analyze_text_advanced("This is a test sentence with enough words to analyze properly.")
        self.assertIsInstance(result, TextAdvancedAnalysis)

    def test_to_json_returns_dict(self) -> None:
        """to_json should return a dictionary."""
        result = analyze_text_advanced("This is a test sentence with enough words to analyze properly.")
        data = result.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("score", data)
        self.assertIn("band", data)
        self.assertIn("verdict", data)
        self.assertIn("ai_probability", data)
        self.assertIn("style_profile", data)

    def test_ai_probability_range(self) -> None:
        """AI probability should be between 0 and 1."""
        result = analyze_text_advanced("This is a test sentence with enough words to analyze properly.")
        self.assertGreaterEqual(result.ai_probability, 0.0)
        self.assertLessEqual(result.ai_probability, 1.0)

    def test_style_profile_valid(self) -> None:
        """Style profile should be a valid string."""
        result = analyze_text_advanced("This is a test sentence with enough words to analyze properly.")
        valid_profiles = ["short", "academic", "news", "blog", "conversational", "general"]
        self.assertIn(result.style_profile, valid_profiles)

    def test_transitional_text_detected(self) -> None:
        """Text with many transition words should be detected."""
        text = """
        결론적으로, 이 연구는 중요한 결과를 보여줍니다.
        요약하자면, 주요 발견사항은 다음과 같습니다.
        먼저, 첫 번째 결과를 살펴보면 중요한 것은 매우 명확합니다.
        다음으로, 두 번째 결과는 다음과 같습니다.
        마지막으로, 세 번째 결과는 중요하게도 매우 의미가 있습니다.
        따라서 결론적으로 이 연구는 가치가 있습니다.
        """ * 5
        result = analyze_text_advanced(text)
        self.assertGreater(result.score, 0)

    def test_repetitive_text_detected(self) -> None:
        """Repetitive text should be detected."""
        text = " ".join(["이것은 반복되는 문장입니다."] * 30)
        result = analyze_text_advanced(text)
        self.assertGreater(result.score, 0)

    def test_uniform_sentence_length_detected(self) -> None:
        """Text with uniform sentence lengths should be detected."""
        sentences = [" ".join(["단어"] * 10) + "."] * 20
        text = " ".join(sentences)
        result = analyze_text_advanced(text)
        self.assertGreater(result.score, 0)


if __name__ == "__main__":
    unittest.main()
