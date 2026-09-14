"""Advanced text analysis module for AI-generated text detection.

Provides deeper analysis of text using statistical measures like
bigram entropy, burstiness, vocabulary diversity, and n-gram patterns.

Note: the entropy measure below is a bigram distribution statistic, NOT
language-model perplexity. No LLM backbone is bundled, so nothing in this
module claims or approximates PPL.
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

    hangul = _hangul_ratio(trimmed)
    words = _tokenize_words(trimmed, hangul)
    sentences = [s.strip() for s in re.split(r"[.!?。！？\n]+", trimmed) if len(s.strip()) >= 5]

    signals: list[TextAdvancedEvidenceSignal] = []
    limitations: list[str] = []

    # Bigram-entropy analysis (a distribution statistic, not LM perplexity)
    bigram_entropy_signal = _bigram_entropy_analysis(words)
    if bigram_entropy_signal:
        signals.append(bigram_entropy_signal)

    # Burstiness analysis
    burstiness_signal = _burstiness_analysis(sentences)
    if burstiness_signal:
        signals.append(burstiness_signal)

    # Vocabulary diversity
    diversity_signal = _vocabulary_diversity(words, hangul)
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

    # Markdown/structure fingerprint — agent-style outputs carry heavy
    # list/header/bold markup even after plain-text flattening
    markdown_signal = _markdown_density(trimmed)
    if markdown_signal:
        signals.append(markdown_signal)

    # Frontier-LLM fingerprints (language/model agnostic families)
    for probe in (_typographic_punctuation_signal, _ai_vocabulary_signal,
                  _hedging_balance_signal, _connector_starter_signal):
        probe_signal = probe(trimmed, sentences, words, hangul)
        if probe_signal:
            signals.append(probe_signal)

    # Limitations
    if len(words) < 50:
        limitations.append("텍스트가 너무 짧아 신뢰할 수 있는 분석이 어렵습니다.")
    if len(sentences) < 3:
        limitations.append("문장 수가 적어 문장 수준 분석이 제한적입니다.")
    if hangul > 0.3:
        limitations.append(
            "한국어 텍스트입니다 — 어휘 통계 임계값이 영어 기준으로 보정되어 있어 "
            "교착어 특성상 TTR/hapax/MTLD 신호가 발동하지 않을 수 있습니다."
        )
    if len(words) >= 20:
        limitations.append("빅그램 엔트로피는 언어모델 퍼플렉시티가 아닌 분포 통계이므로 PPL 수준의 근거로 해석하면 안 됩니다.")
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


def _hangul_ratio(text: str) -> float:
    """Fraction of letters that are Hangul syllables/jamo (0-1)."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    hangul = sum(1 for c in letters if "가" <= c <= "힣" or "ᄀ" <= c <= "ᇿ")
    return hangul / len(letters)


# Common Korean particles/endings (longest-first so multi-char endings
# strip before their suffixes). A heuristic eojeol normalizer, not a
# morpheme analyzer — enough to stop 조사/어미 from inflating token counts.
_KO_PARTICLES = sorted(
    "으로부터 에서는 에게는 한테는 으로써 으로서 이라고 라고 하고 이며 이고 에서 "
    "으로 로 에 에게 한테 께 의 을 를 이 가 은 는 도 만 부터 까지 처럼 보다 조차 "
    "마저 밖에 이나 나 와 과 대한 대해 위해 위한 통해 따라 같은 등 니다 습니다 "
    "습니다 했다 한다 되는 됩니다 입니다 였다 았다 었다 겠다 고 다".split(),
    key=len, reverse=True,
)


def _strip_ko_particles(token: str) -> str:
    for particle in _KO_PARTICLES:
        if token.endswith(particle) and len(token) > len(particle) + 1:
            return token[: -len(particle)]
    return token


def _tokenize_words(text: str, hangul_ratio: float) -> list[str]:
    """Word tokens; for Hangul-heavy text also strip one trailing particle
    so agglutinative forms do not inflate the vocabulary stats."""
    words = re.findall(r"[\w']+", text.lower())
    if hangul_ratio > 0.3:
        words = [_strip_ko_particles(w) for w in words]
    return words


def _markdown_density(text: str) -> TextAdvancedEvidenceSignal | None:
    """Agent-style structure fingerprint: markdown headers, bullets,
    numbered lists, bold, and code fences surviving plain-text flattening."""
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 4:
        return None
    structured = sum(
        1
        for l in lines
        if re.match(r"^\s*(#{1,6}\s|[-*•]\s|\d+[.)]\s|>\s)", l) or l.strip() == "```" or l.strip().startswith("```")
    )
    density = structured / len(lines)
    bold_markers = len(re.findall(r"\*\*[^*]+\*\*", text))
    if density > 0.4 or (density > 0.25 and bold_markers >= 3):
        return TextAdvancedEvidenceSignal(
            "구조화 마크업 밀도",
            f"마크다운형 목록/헤더 구조 비율({density:.2f})이 높아 에이전트형 출력과 유사합니다.",
            14,
        )
    return None


def bigram_entropy(words: list[str]) -> float:
    """Shannon entropy (bits) of the word-bigram distribution.

    This is a distributional statistic over the observed bigrams — it is
    NOT language-model perplexity and must not be reported as such.
    """
    if len(words) < 2:
        return 0.0

    bigrams = [(words[i], words[i + 1]) for i in range(len(words) - 1)]
    bigram_counts = Counter(bigrams)
    total_bigrams = len(bigrams)

    entropy = 0.0
    for count in bigram_counts.values():
        prob = count / total_bigrams
        entropy -= prob * math.log2(prob)
    return entropy


def _bigram_entropy_analysis(words: list[str]) -> TextAdvancedEvidenceSignal | None:
    """Flag extreme bigram-entropy regimes (predictable vs abnormal text)."""
    if len(words) < 20:
        return None

    entropy = bigram_entropy(words)

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


# Typographic (non-ASCII) punctuation frontier LLMs emit by default while
# humans type ASCII fallbacks: em/en dashes, curly quotes, ellipsis.
_TYPOGRAPHIC_PUNCT = "—–‘’“”…″‴·"

# Documented frontier-LLM favored vocabulary (the "delve" family) — words
# whose frequency spiked in published corpora after LLM adoption.
_AI_VOCAB_EN = {
    "delve", "delving", "crucial", "crucially", "realm", "tapestry",
    "landscape", "nuanced", "foster", "fostering", "meticulous",
    "meticulously", "testament", "vibrant", "pivotal", "leverage",
    "leveraging", "elevate", "holistic", "embark", "unleash",
    "streamline", "commendable", "intricate", "underscore",
    "underscores", "paramount", "multifaceted", "endeavor", "beacon",
    "navigate", "navigating", "landscapes", "rich tapestry",
}
# Conjunctive adverbs frontier LLMs overuse as sentence glue.
_AI_CONNECTORS_EN = {
    "moreover", "furthermore", "additionally", "consequently",
    "nevertheless", "nonetheless", "in conclusion", "importantly",
    "notably", "ultimately", "in summary", "in essence",
}
# Korean equivalents: formal connective/conclusive phrases typical of
# model-generated Korean.
_AI_CONNECTORS_KO = {
    "결론적으로", "요약하자면", "다음과 같습니다", "중요한 것은",
    "주목할 점", "핵심은", "다양한 측면", "균형 잡힌",
    "종합하면", "살펴보면", "고려해야", "한편으로", "무엇보다",
}


def _typographic_punctuation_signal(
    text: str, sentences: list[str], words: list[str], hangul_ratio: float
) -> TextAdvancedEvidenceSignal | None:
    """Humans type ASCII quotes/dashes; frontier LLMs emit typographic
    Unicode (—, ", ", ', …). Density of those characters is a cheap,
    model-agnostic fingerprint."""
    if len(text) < 200:
        return None
    typo = sum(text.count(ch) for ch in _TYPOGRAPHIC_PUNCT)
    density = typo / len(text)
    # Humans occasionally paste smart quotes; require real density.
    if typo >= 6 and density > 0.002:
        return TextAdvancedEvidenceSignal(
            "타이포그래픽 구두점",
            f"em-dash/곱따옴표/말줄임 등 비ASCII 구두점이 {typo}개({density:.3f}/문자) — 키보드 입력이 아닌 모델 출력 특성입니다.",
            12,
        )
    return None


def _ai_vocabulary_signal(
    text: str, sentences: list[str], words: list[str], hangul_ratio: float
) -> TextAdvancedEvidenceSignal | None:
    """Frontier-LLM favored vocabulary + connector-adverb density."""
    if len(words) < 60:
        return None
    normalized = text.lower()
    if hangul_ratio > 0.3:
        hits = sum(normalized.count(p) for p in _AI_CONNECTORS_KO)
        per_k = hits / (len(words) / 1000)
        if hits >= 3 and per_k > 4:
            return TextAdvancedEvidenceSignal(
                "모델형 연결어 밀도(한국어)",
                f"모델 생성 한국어에서 과용되는 연결/결론 표현이 {hits}개({per_k:.1f}/1000어) 발견됩니다.",
                12,
            )
        return None
    vocab_hits = sum(words.count(w) for w in _AI_VOCAB_EN)
    conn_hits = sum(normalized.count(c) for c in _AI_CONNECTORS_EN)
    per_k = (vocab_hits + conn_hits) / (len(words) / 1000)
    if vocab_hits + conn_hits >= 4 and per_k > 5:
        return TextAdvancedEvidenceSignal(
            "LLM 과용 어휘",
            f"frontier LLM이 과용하는 어휘/접속부사가 {vocab_hits + conn_hits}개({per_k:.1f}/1000어) — delve/crucial/moreover 계열 지문입니다.",
            15,
        )
    return None


def _hedging_balance_signal(
    text: str, sentences: list[str], words: list[str], hangul_ratio: float
) -> TextAdvancedEvidenceSignal | None:
    """Both-sides hedging scaffold typical of assistant-style answers."""
    if len(sentences) < 4:
        return None
    normalized = text.lower()
    en_pairs = [
        ("on the one hand", "on the other hand"),
        ("while it is", "it is also"),
        ("however", "it is important to note"),
        ("although", "nevertheless"),
    ]
    ko_pairs = [
        ("한편", "반면"),
        ("장점이 있", "단점"),
        ("다만", "고려해야"),
        ("반대로", "동시에"),
    ]
    pairs = ko_pairs if hangul_ratio > 0.3 else en_pairs
    hit_pairs = sum(1 for a, b in pairs if a in normalized and b in normalized)
    if hit_pairs >= 2:
        return TextAdvancedEvidenceSignal(
            "양면 균형 헤징 구조",
            f"찬반 균형형 연결 구조가 {hit_pairs}쌍 발견 — 어시스턴트 응답의 전형적 골격입니다.",
            10,
        )
    return None


def _connector_starter_signal(
    text: str, sentences: list[str], words: list[str], hangul_ratio: float
) -> TextAdvancedEvidenceSignal | None:
    """Sentence-initial connector uniformity — humans vary openers,
    frontier models default to connector-first scaffolding."""
    if len(sentences) < 6:
        return None
    en_starters = (
        "however", "moreover", "furthermore", "additionally", "in addition",
        "consequently", "therefore", "thus", "overall", "in conclusion",
        "importantly", "notably", "first", "second", "finally", "ultimately",
    )
    ko_starters = (
        "또한", "그러나", "하지만", "따라서", "결론적으로", "먼저",
        "다음으로", "마지막으로", "한편", "특히", "종합하면", "즉,",
    )
    starters = ko_starters if hangul_ratio > 0.3 else en_starters
    hits = sum(
        1
        for s in sentences
        if s.lower().lstrip('"\'-–— ').startswith(starters)
    )
    rate = hits / len(sentences)
    if rate > 0.3 and hits >= 3:
        return TextAdvancedEvidenceSignal(
            "문두 접속사 균일성",
            f"문장의 {rate:.0%}({hits}개)이 접속사로 시작 — 사람보다 균일한 문두 골격입니다.",
            10,
        )
    return None


def _vocabulary_diversity(words: list[str], hangul_ratio: float = 0.0) -> TextAdvancedEvidenceSignal | None:
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

    if hangul_ratio > 0.3:
        # Agglutinative Korean sits far above the English bands even after
        # particle stripping; provisional Korean band until Phase-D
        # calibration on a labeled corpus.
        if ttr > 0.8 and hapax_ratio > 0.8:
            return TextAdvancedEvidenceSignal(
                "높은 어휘 다양성(한국어)",
                f"한국어 TTR({ttr:.2f}), hapax({hapax_ratio:.2f})이 높아 정제된 문체입니다.",
                10,
            )
        return None

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


@dataclass(frozen=True)
class StylometryComparison:
    """Two-input authorship comparison — same-author likelihood.

    Compares a coarse stylometric vector (sentence shape, lexical
    diversity, punctuation habits, connective use). This is a screening
    distance, not forensic authorship attribution — short or translated
    texts make it unreliable.
    """

    same_author_score: int  # 0-100
    distance: float
    band: str
    verdict: str
    limitations: list[str]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def _stylometry_vector(text: str) -> list[float]:
    """Author fingerprint vector: sentence shape + lexical + punctuation."""
    import math

    trimmed = text.strip()
    if not trimmed:
        return [0.0] * 9
    sentences = [s for s in re.split(r"[.!?。！？\n]+", trimmed) if s.strip()]
    hangul = _hangul_ratio(trimmed)
    words = _tokenize_words(trimmed, hangul)
    n = len(words) or 1
    sent_lens = [len(_tokenize_words(s, hangul)) for s in sentences] or [0]
    mean_len = sum(sent_lens) / len(sent_lens)
    var_len = sum((x - mean_len) ** 2 for x in sent_lens) / len(sent_lens)
    punct = sum(1 for ch in trimmed if ch in ",;:'\"—–-()")
    connectors = sum(1 for w in words if w in {"moreover", "furthermore", "however", "therefore", "또한", "그러나", "따라서", "한편"})
    unique = len(set(words))
    return [
        mean_len / 40.0,
        math.sqrt(var_len) / max(mean_len, 1.0),
        unique / n,
        len([w for w in set(words) if words.count(w) == 1]) / n,
        punct / max(len(trimmed), 1) * 100,
        connectors / n * 100,
        len(sentences) / max(len(trimmed), 1) * 500,
        hangul,
        bigram_entropy(words) / 20.0,
    ]


def compare_texts(text_a: str, text_b: str) -> StylometryComparison:
    """Compare two texts for same-author likelihood."""
    limitations = [
        "문체 벡터 거리 측정이며 포렌식 저자 귀속이 아닙니다.",
        "짧거나 번역된 텍스트에서는 거리가 불안정합니다.",
    ]
    vec_a = _stylometry_vector(text_a)
    vec_b = _stylometry_vector(text_b)
    # Mean relative difference per dimension — cosine on mostly-positive
    # vectors under-separates same-language pairs, so scale each feature's
    # gap by its magnitude instead.
    distance = sum(abs(a - b) / (abs(a) + abs(b) + 0.02) for a, b in zip(vec_a, vec_b)) / len(vec_a)
    score = max(0, min(100, int((1.0 - distance / 0.35) * 100)))
    if score >= 67:
        band = "same"
        verdict = "문체 특징이 가까워 동일 작성자/도구일 가능성이 높습니다."
    elif score >= 35:
        band = "unclear"
        verdict = "문체 유사성이 중간 영역입니다 — 더 긴 표본이 필요합니다."
    else:
        band = "different"
        verdict = "문체 특징이 멀어 다른 작성자/도구일 가능성이 높습니다."
    if len(text_a) < 400 or len(text_b) < 400:
        limitations.append("한쪽 표본이 400자 미만이라 문체 비교가 불안정합니다.")
    return StylometryComparison(score, distance, band, verdict, limitations)
