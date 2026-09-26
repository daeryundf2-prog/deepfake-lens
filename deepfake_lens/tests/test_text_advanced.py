"""Tests for the advanced text analysis module."""

from __future__ import annotations

import unittest

from deepfake_lens.text_advanced import (
    TextAdvancedAnalysis,
    analyze_text_advanced,
    bigram_entropy,
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

    def test_hangul_text_flags_english_calibrated_thresholds(self) -> None:
        """Korean text must carry the English-calibration limitation: the
        lexical thresholds cannot fire on agglutinative token streams, so
        a Korean score without that warning would mislead."""
        korean = "인공지능 기술은 최근 몇 년간 콘텐츠 제작 분야에서 혁신적인 변화를 이끌어왔습니다. 대규모 언어 모델은 방대한 학습 데이터를 바탕으로 다양한 주제에 걸쳐 일관성 있고 맥락에 적합한 텍스트를 생성할 수 있습니다."
        english = "Artificial intelligence has transformed content creation in recent years across many domains and applications worldwide."
        ko = analyze_text_advanced(korean)
        en = analyze_text_advanced(english)
        self.assertTrue(any("한국어" in lim for lim in ko.limitations))
        self.assertFalse(any("한국어" in lim for lim in en.limitations))

    def test_bigram_entropy_known_distribution(self) -> None:
        """Two equiprobable bigrams must yield exactly 1 bit of entropy."""
        self.assertAlmostEqual(bigram_entropy(["a", "b", "a", "b", "a"]), 1.0)
        # A single repeated bigram has zero entropy.
        self.assertAlmostEqual(bigram_entropy(["x", "x", "x", "x"]), 0.0)
        self.assertAlmostEqual(bigram_entropy(["only"]), 0.0)

    def test_no_perplexity_claims(self) -> None:
        """The module measures bigram entropy; the old pseudo-perplexity
        name must not survive anywhere."""
        import deepfake_lens.text_advanced as text_module

        self.assertFalse(hasattr(text_module, "_perplexity_analysis"))
        self.assertTrue(hasattr(text_module, "bigram_entropy"))

    def test_limitations_disclaim_perplexity(self) -> None:
        """Outputs must state that bigram entropy is not LM perplexity."""
        text = " ".join(f"word{i} follows" for i in range(40))
        result = analyze_text_advanced(text)
        self.assertTrue(
            any("퍼플렉시티가 아닌" in line for line in result.limitations)
        )

    def test_korean_translationese_probe(self) -> None:
        """Korean text with multiple translationese markers should trigger the translationese signal."""
        text = (
            "이러한 문제점들을 해결하기 위한 구체적인 방안들을 살펴보겠습니다. "
            "새로운 정책에 의해 사회적 제도가 변경되어집니다. "
            "그것은 국가 경제의 발전의 방향에 중요한 영향을 미칩니다. "
            "다양한 요소들을 고려하고 여러 데이터들을 분석하는 과정을 거쳤습니다."
        )
        res = analyze_text_advanced(text)
        signal_titles = [s.title for s in res.signals]
        self.assertIn("한국어 번역투 및 직역 문체", signal_titles)

    def test_korean_ai_endings_probe(self) -> None:
        """Korean text with uniform prescriptive endings should trigger the endings signal."""
        text = (
            "첫째로 보안 수칙을 준수하는 것이 중요합니다. "
            "정기적인 업데이트를 통해 취약점을 방지할 수 있습니다. "
            "공식 문서를 반드시 확인해 보시기 바랍니다. "
            "비밀번호를 안전하게 관리하는 것을 기억하세요."
        )
        res = analyze_text_advanced(text)
        signal_titles = [s.title for s in res.signals]
        self.assertIn("한국어 AI 정형 종결어미", signal_titles)

    def test_korean_ai_slop_probe(self) -> None:
        """Korean text with LLM buzzwords should trigger the slop signal."""
        text = (
            "이번 기술 혁신은 업계에서 주목할 만한 성과로 평가받고 있습니다. "
            "인공지능의 도입은 미래 산업의 중추적인 역할을 담당하게 될 것입니다. "
            "새로운 지평을 열어가는 과정에서 지속 가능한 발전 모델을 구축하고 있습니다."
        )
        res = analyze_text_advanced(text)
        signal_titles = [s.title for s in res.signals]
        self.assertIn("한국어 모델 상투어(Slop) 감지", signal_titles)


if __name__ == "__main__":
    unittest.main()
