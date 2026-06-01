"""Advanced text analysis module for AI-generated text detection.

Provides deeper analysis of text using statistical measures like
perplexity, burstiness, vocabulary diversity, and n-gram patterns.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class TextAdvancedEvidenceSignal:
    title: str
    detail: str
    weight: int


@dataclass(frozen=True)
class TextAdvancedAnalysis:
    score: int
    band: str
    band_label: str
    verdict: str
    signals: list[TextAdvancedEvidenceSignal]
    limitations: list[str]
    ai_probability: float
    style_profile: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def analyze_text_advanced(text: str) -> TextAdvancedAnalysis:
    """Perform advanced text analysis for AI generation detection."""
    trimmed = text.strip()
    if not trimmed:
        return TextAdvancedAnalysis(
            score=0,
            band="unknown",
            band_label="판단 어려움",
            verdict="분석할 텍스트가 비어 있습니다.",
            signals=[],
            limitations=["텍스트가 비어 있습니다."],
            ai_probability=0.0,
            style_profile="empty",
        )

    words = re.findall(r"[\w']+", trimmed.lower())
    sentences = [s.strip() for s in re.split(r"[.!?。！？\n]+", trimmed) if len(s.strip()) >= 5]

    signals: list[TextAdvancedEvidenceSignal] = []
    limitations: list[str] = []

    # Perplexity analysis
    perplexity_signal = _perplexity_analysis(words)
    if perplexity_signal:
        signals.append(perplexity_signal)

    # Burstiness analysis
    burstiness_signal = _burstiness_analysis(sentences)
    if burstiness_signal:
        signals.append(burstiness_signal)

    # Vocabulary diversity
    diversity_signal = _vocabulary_diversity(words)
    if diversity_signal:
        signals.append(diversity_signal)

    # Sentence length consistency
    sentence_signal = _sentence_length_consistency(sentences)
    if sentence_signal:
        signals.append(sentence_signal)

    # N-gram repetition
    ngram_signal = _ngram_repetition(words)
    if ngram_signal:
        signals.append(ngram_signal)

    # Transition word density
    transition_signal = _transition_word_density(trimmed.lower())
    if transition_signal:
        signals.append(transition_signal)

    # Personal anchor density
    personal_signal = _personal_anchor_density(words)
    if personal_signal:
        signals.append(personal_signal)

    # Limitations
    if len(words) < 50:
        limitations.append("텍스트가 너무 짧아 신뢰할 수 있는 분석이 어렵습니다.")
    if len(sentences) < 3:
        limitations.append("문장 수가 적어 문장 수준 분석이 제한적입니다.")
    limitations.append("통계적 휴리스틱 기반 선별 결과이며, 확정적 판별이 아닙니다.")

    score = min(100, sum(signal.weight for signal in signals))

    if score >= 67:
        band = "high"
        band_label = "높음"
        verdict = "텍스트에서 AI 생성 의심 신호가 강합니다."
    elif score >= 35:
        band = "medium"
        band_label = "주의"
        verdict = "텍스트에서 몇 가지 의심 신호가 보여 추가 확인이 필요합니다."
    else:
        band = "low"
        band_label = "낮음"
        verdict = "텍스트에서 뚜렷한 AI 생성 의심 신호는 적습니다."

    ai_probability = min(1.0, score / 100.0)
    style_profile = _classify_style_profile(words, trimmed)

    return TextAdvancedAnalysis(
        score=score,
        band=band,
        band_label=band_label,
        verdict=verdict,
        signals=signals,
        limitations=limitations,
        ai_probability=ai_probability,
        style_profile=style_profile,
    )


def _perplexity_analysis(words: list[str]) -> TextAdvancedEvidenceSignal | None:
    """Analyze word-level perplexity (simplified)."""
    if len(words) < 20:
        return None

    # Calculate bigram entropy as a proxy for perplexity
    bigrams = [(words[i], words[i+1]) for i in range(len(words) - 1)]
    bigram_counts = Counter(bigrams)
    total_bigrams = len(bigrams)

    entropy = 0.0
    for count in bigram_counts.values():
        prob = count / total_bigrams
        entropy -= prob * math.log2(prob)

    # AI text tends to have lower entropy (more predictable)
    if entropy < 4.0 and len(words) > 100:
        return TextAdvancedEvidenceSignal(
            "낮은 엔트로피",
            f"빅그램 엔트로피({entropy:.2f})가 낮아 예측 가능한 텍스트입니다.",
            20,
        )

    # Very high entropy (possible random/obfuscated)
    if entropy > 8.0:
        return TextAdvancedEvidenceSignal(
            "높은 엔트로피",
            f"빅그램 엔트로피({entropy:.2f})가 높아 비정상적입니다.",
            15,
        )

    return None


def _burstiness_analysis(sentences: list[str]) -> TextAdvancedEvidenceSignal | None:
    """Analyze burstiness (variation in sentence lengths)."""
    if len(sentences) < 5:
        return None

    lengths = [len(s.split()) for s in sentences]
    mean_length = sum(lengths) / len(lengths)
    variance = sum((l - mean_length) ** 2 for l in lengths) / len(lengths)
    std_dev = math.sqrt(variance)

    # Coefficient of variation
    cv = std_dev / max(1.0, mean_length)

    # AI text tends to have low burstiness (uniform sentence lengths)
    if cv < 0.3 and len(sentences) > 10:
        return TextAdvancedEvidenceSignal(
            "낮은 버스티니스",
            f"문장 길이 변이계수({cv:.2f})가 낮아 균일한 문체입니다.",
            18,
        )

    return None


def _vocabulary_diversity(words: list[str]) -> TextAdvancedEvidenceSignal | None:
    """Analyze vocabulary diversity metrics."""
    if len(words) < 50:
        return None

    # Type-Token Ratio (TTR)
    unique_words = set(words)
    ttr = len(unique_words) / len(words)

    # Hapax Legomena ratio (words appearing exactly once)
    word_counts = Counter(words)
    hapax_count = sum(1 for count in word_counts.values() if count == 1)
    hapax_ratio = hapax_count / len(words)

    # MTLD (simplified)
    mtld = _calculate_mtld(words)

    # AI text often has moderate vocabulary diversity
    if 0.4 < ttr < 0.6 and hapax_ratio < 0.5 and mtld < 50:
        return TextAdvancedEvidenceSignal(
            "보통 어휘 다양성",
            f"TTR({ttr:.2f}), MTLD({mtld:.1f})가 보통 수준으로 AI 생성 텍스트와 유사합니다.",
            12,
        )

    return None


def _calculate_mtld(words: list[str]) -> float:
    """Calculate simplified MTLD (Measure of Textual Lexical Diversity)."""
    if not words:
        return 0.0

    factor_count = 0
    factor_length = 0
    current_factor_words = set()

    for word in words:
        current_factor_words.add(word)
        factor_length += 1

        ttr = len(current_factor_words) / factor_length
        if ttr <= 0.72:  # Threshold
            factor_count += 1
            current_factor_words = set()
            factor_length = 0

    if factor_length > 0:
        # Partial factor
        ttr = len(current_factor_words) / factor_length
        factor_count += (1 - ttr) / (1 - 0.72)

    if factor_count == 0:
        return float(len(words))

    return len(words) / factor_count


def _sentence_length_consistency(sentences: list[str]) -> TextAdvancedEvidenceSignal | None:
    """Analyze sentence length consistency."""
    if len(sentences) < 5:
        return None

    lengths = [len(s.split()) for s in sentences]

    # Check for alternating patterns
    if len(lengths) >= 6:
        # Short-long-short-long pattern
        alternating_count = 0
        for i in range(2, len(lengths)):
            if (lengths[i] - lengths[i-1]) * (lengths[i-1] - lengths[i-2]) < 0:
                alternating_count += 1

        alternating_ratio = alternating_count / (len(lengths) - 2)
        if alternating_ratio > 0.85:
            return TextAdvancedEvidenceSignal(
                "교대 문장 패턴",
                f"문장 길이가 교대로 변화하는 패턴이 감지됩니다 ({alternating_ratio:.2f}).",
                10,
            )

    return None


def _ngram_repetition(words: list[str]) -> TextAdvancedEvidenceSignal | None:
    """Analyze n-gram repetition patterns."""
    if len(words) < 30:
        return None

    # Check trigram repetition
    trigrams = [" ".join(words[i:i+3]) for i in range(len(words) - 2)]
    trigram_counts = Counter(trigrams)

    repeated_trigrams = sum(1 for count in trigram_counts.values() if count > 1)
    repetition_ratio = repeated_trigrams / max(1, len(trigram_counts))

    if repetition_ratio > 0.3 and len(words) > 100:
        return TextAdvancedEvidenceSignal(
            "반복 3-그램",
            f"3-그램 반복률({repetition_ratio:.2f})이 높습니다.",
            15,
        )

    return None


def _transition_word_density(text: str) -> TextAdvancedEvidenceSignal | None:
    """Analyze density of transition words."""
    transition_words = [
        "그러나", "하지만", "또한", "게다가", "결론적으로", "요약하자면",
        "먼저", "다음으로", "마지막으로", "중요하게도", "주목할 점은",
        "however", "moreover", "furthermore", "additionally", "consequently",
        "therefore", "thus", "hence", "in conclusion", "to summarize",
        "firstly", "secondly", "finally", "importantly", "notably",
    ]

    words = text.split()
    if len(words) < 30:
        return None

    transition_count = sum(1 for word in words if word in transition_words)
    density = transition_count / len(words)

    if density > 0.05:
        return TextAdvancedEvidenceSignal(
            "높은 연결 표현 밀도",
            f"연결 표현 비율({density:.3f})이 높아 정형화된 문체입니다.",
            12,
        )

    return None


def _personal_anchor_density(words: list[str]) -> TextAdvancedEvidenceSignal | None:
    """Analyze density of personal anchors."""
    personal_anchors = [
        "나", "저", "우리", "오늘", "어제", "내일", "엄마", "아빠", "친구",
        "학교", "회사", "집", "i", "me", "my", "we", "today", "yesterday",
        "tomorrow", "myself", "personally",
    ]

    if len(words) < 30:
        return None

    anchor_count = sum(1 for word in words if word in personal_anchors)
    density = anchor_count / len(words)

    # Very low personal anchor density (possible AI) - only for very long texts
    if density < 0.005 and len(words) > 200:
        return TextAdvancedEvidenceSignal(
            "낮은 개인 표현 밀도",
            f"개인적 표현 비율({density:.4f})이 매우 낮습니다.",
            8,
        )
    return None


def _classify_style_profile(words: list[str], text: str) -> str:
    """Classify the text style profile."""
    if len(words) < 20:
        return "short"

    # Check for academic style
    academic_markers = ["연구", "분석", "결과", "방법", "이론", "연구", "hypothesis", "methodology", "analysis"]
    academic_count = sum(1 for marker in academic_markers if marker in text.lower())
    if academic_count >= 3:
        return "academic"

    # Check for news style
    news_markers = ["보도", "기사", "발표", "성명", "입장", "report", "announced", "stated"]
    news_count = sum(1 for marker in news_markers if marker in text.lower())
    if news_count >= 2:
        return "news"

    # Check for blog style
    blog_markers = ["블로그", "포스트", "공유", "경험", "팁", "blog", "post", "share", "experience"]
    blog_count = sum(1 for marker in blog_markers if marker in text.lower())
    if blog_count >= 2:
        return "blog"

    # Check for conversational style
    conversational_markers = ["그래서", "그러면", "어때", "맞지", "so", "well", "you know", "like"]
    conversational_count = sum(1 for marker in conversational_markers if marker in text.lower())
    if conversational_count >= 3:
        return "conversational"

    return "general"
