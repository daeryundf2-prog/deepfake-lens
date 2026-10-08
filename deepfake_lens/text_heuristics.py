"""Text-level heuristic signals extracted from ``core``.

Language-ratio, sentence-uniformity, shingle-repetition, and direct
tool-fingerprint helpers feeding ``analyze_text`` — plus source guessing.
"""


from __future__ import annotations

import re
import struct

from .result_types import EvidenceSignal, SourceConfidence, SourceGuess


PERSONAL_ANCHORS = [
    "나",
    "저",
    "우리",
    "오늘",
    "어제",
    "내일",
    "엄마",
    "아빠",
    "친구",
    "학교",
    "회사",
    "집",
    "i",
    "me",
    "my",
    "we",
    "today",
    "yesterday",
    "tomorrow",
]

_TYPOGRAPHIC_PUNCT = "—–‘’“”…″‴·"

_AI_VOCAB_EN = {
    "delve", "delving", "crucial", "crucially", "realm", "tapestry",
    "landscape", "nuanced", "foster", "fostering", "meticulous",
    "meticulously", "testament", "vibrant", "pivotal", "leverage",
    "leveraging", "elevate", "holistic", "embark", "unleash",
    "streamline", "commendable", "intricate", "underscore",
    "underscores", "paramount", "multifaceted", "endeavor", "beacon",
    "navigate", "navigating",
}
_AI_CONNECTORS_EN = {
    "moreover", "furthermore", "additionally", "consequently",
    "nevertheless", "nonetheless", "in conclusion", "importantly",
    "notably", "ultimately", "in summary", "in essence",
}
_AI_CONNECTORS_KO = {
    "결론적으로", "요약하자면", "다음과 같습니다", "중요한 것은",
    "주목할 점", "핵심은", "다양한 측면", "균형 잡힌",
    "종합하면", "살펴보면", "고려해야", "한편으로", "무엇보다",
}


def _hangul_ratio_text(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if "가" <= c <= "힣") / len(letters)


def _frontier_llm_fingerprints(
    trimmed: str, normalized: str, sentences: list[str], words: list[str]
) -> list[EvidenceSignal]:
    """Model-agnostic fingerprints of frontier-LLM writing style — vendor
    tells (delve family vocab, typographic punctuation, hedging scaffold,
    connector-first sentences) that persist across generators. Kept in
    sync with text_advanced's probe family."""
    signals: list[EvidenceSignal] = []
    hangul = _hangul_ratio_text(trimmed)

    if len(trimmed) >= 200:
        typo = sum(trimmed.count(ch) for ch in _TYPOGRAPHIC_PUNCT)
        if typo >= 6 and typo / len(trimmed) > 0.002:
            signals.append(EvidenceSignal(
                "타이포그래픽 구두점",
                f"em-dash/곱따옴표/말줄임 등 비ASCII 구두점이 {typo}개 — 키보드 입력이 아닌 모델 출력 특성입니다.",
                12,
            ))

    if len(words) >= 60:
        if hangul > 0.3:
            hits = sum(normalized.count(p) for p in _AI_CONNECTORS_KO)
            if hits >= 3 and hits / (len(words) / 1000) > 4:
                signals.append(EvidenceSignal(
                    "모델형 연결어 밀도(한국어)",
                    f"모델 생성 한국어에서 과용되는 연결/결론 표현이 {hits}개 발견됩니다.",
                    12,
                ))
        else:
            vocab_hits = sum(words.count(w) for w in _AI_VOCAB_EN)
            conn_hits = sum(normalized.count(c) for c in _AI_CONNECTORS_EN)
            if vocab_hits + conn_hits >= 4 and (vocab_hits + conn_hits) / (len(words) / 1000) > 5:
                signals.append(EvidenceSignal(
                    "LLM 과용 어휘",
                    f"frontier LLM이 과용하는 어휘/접속부사가 {vocab_hits + conn_hits}개 — delve/crucial/moreover 계열 지문입니다.",
                    15,
                ))

    if len(sentences) >= 4:
        pairs = [("한편", "반면"), ("장점이 있", "단점"), ("다만", "고려해야"), ("반대로", "동시에")] if hangul > 0.3 else [
            ("on the one hand", "on the other hand"),
            ("while it is", "it is also"),
            ("however", "it is important to note"),
            ("although", "nevertheless"),
        ]
        hit_pairs = sum(1 for a, b in pairs if a in normalized and b in normalized)
        if hit_pairs >= 2:
            signals.append(EvidenceSignal(
                "양면 균형 헤징 구조",
                f"찬반 균형형 연결 구조가 {hit_pairs}쌍 발견 — 어시스턴트 응답의 전형적 골격입니다.",
                10,
            ))

    if len(sentences) >= 6:
        starters = ("또한", "그러나", "하지만", "따라서", "결론적으로", "먼저", "다음으로", "마지막으로", "한편", "특히", "종합하면", "즉,") if hangul > 0.3 else (
            "however", "moreover", "furthermore", "additionally", "in addition",
            "consequently", "therefore", "thus", "overall", "in conclusion",
            "importantly", "notably", "first", "second", "finally", "ultimately",
        )
        hits = sum(1 for s in sentences if s.lower().lstrip("\"'-–— ").startswith(starters))
        if hits / len(sentences) > 0.3 and hits >= 3:
            signals.append(EvidenceSignal(
                "문두 접속사 균일성",
                f"문장의 {hits / len(sentences):.0%}({hits}개)이 접속사로 시작 — 사람보다 균일한 문두 골격입니다.",
                10,
            ))
    return signals


def _sentence_uniformity_signal(sentences: list[str]) -> EvidenceSignal | None:
    if len(sentences) < 5:
        return None
    lengths = [max(1, len(re.findall(r"[\w']+", sentence, flags=re.UNICODE))) for sentence in sentences]
    average = sum(lengths) / len(lengths)
    variance = sum((length - average) ** 2 for length in lengths) / len(lengths)
    coefficient = (variance**0.5) / max(1.0, average)
    if average >= 18.0 and coefficient < 0.28:
        return EvidenceSignal("문장 길이 균일성", "여러 문장이 비슷한 길이로 이어집니다.", 15)
    if average >= 14.0 and coefficient < 0.38:
        return EvidenceSignal("낮은 문장 변주", "문장 길이 변화가 작습니다.", 9)
    return None


def _repeated_shingle_signal(words: list[str]) -> EvidenceSignal | None:
    if len(words) < 80:
        return None
    shingles = [" ".join(words[index : index + 3]) for index in range(len(words) - 2)]
    repeated = len(shingles) - len(set(shingles))
    ratio = repeated / max(1, len(shingles))
    if ratio >= 0.1:
        return EvidenceSignal("반복 어구", f"3단어 구문 반복률이 {int(ratio * 100)}% 입니다.", 16)
    if ratio >= 0.055:
        return EvidenceSignal("약한 반복 패턴", "비슷한 구문이 여러 번 재사용됩니다.", 8)
    return None


def _generic_text_signal(normalized: str, words: list[str]) -> EvidenceSignal | None:
    if len(words) < 70:
        return None
    has_number_or_date = re.search(r"\d{1,4}([./:-]\d{1,2})?", normalized) is not None
    personal_anchor_count = sum(1 for anchor in PERSONAL_ANCHORS if re.search(rf"\b{re.escape(anchor)}\b", normalized))
    if not has_number_or_date and personal_anchor_count == 0:
        return EvidenceSignal("개인 맥락 부족", "긴 글인데 날짜, 수치, 구체적 경험 단서가 거의 없습니다.", 8)
    return None


def _direct_tool_guess(normalized: str) -> SourceGuess | None:
    rules = [
        ("Midjourney/Niji 추정", ["midjourney", "niji"], "Midjourney/Niji 단서가 메타데이터에 있습니다."),
        ("Flux / Black Forest Labs 추정", ["flux", "black forest labs", "bfl"], "Flux 또는 Black Forest Labs 단서가 메타데이터에 있습니다."),
        ("Stable Diffusion 추정", ["stable diffusion", "stablediffusion", "automatic1111", "a1111", "sd-webui"], "Stable Diffusion 계열 단서가 메타데이터에 있습니다."),
        ("DALL-E/OpenAI 추정", ["dall-e", "dalle", "openai", "chatgpt"], "DALL-E/OpenAI 단서가 메타데이터에 있습니다."),
        ("Google Imagen/Gemini 추정", ["imagen", "gemini", "google ai studio", "nano banana"], "Google Imagen/Gemini 계열 단서가 메타데이터에 있습니다."),
        ("Adobe Firefly 추정", ["adobe firefly", "firefly"], "Adobe Firefly 단서가 메타데이터에 있습니다."),
        ("Ideogram 추정", ["ideogram"], "Ideogram 단서가 메타데이터에 있습니다."),
        ("Runway 추정", ["runway"], "Runway 단서가 메타데이터에 있습니다."),
        ("Leonardo.ai 추정", ["leonardo.ai", "leonardo ai"], "Leonardo.ai 단서가 메타데이터에 있습니다."),
        ("NovelAI 추정", ["novelai", "novel ai"], "NovelAI 단서가 메타데이터에 있습니다."),
        ("Recraft 추정", ["recraft"], "Recraft 단서가 메타데이터에 있습니다."),
        ("Canva AI 추정", ["canva ai", "magic media"], "Canva AI/Magic Media 단서가 메타데이터에 있습니다."),
        ("Grok/xAI 추정", ["grok", "xai"], "Grok/xAI 단서가 메타데이터에 있습니다."),
    ]
    for label, markers, reason in rules:
        if any(re.search(rf"(?:^|\s){re.escape(marker)}(?:\s|$)", normalized) for marker in markers):
            return SourceGuess(label, SourceConfidence.HIGH, [reason])
    return None


def _looks_like_a1111(normalized: str) -> bool:
    has_prompt_block = "negative prompt" in normalized or "png.parameters" in normalized
    hits = sum(1 for marker in ["steps:", "sampler:", "cfg scale", "seed:", "model hash", "model:"] if marker in normalized)
    return has_prompt_block and hits >= 2


def _looks_like_comfyui(normalized: str) -> bool:
    has_workflow = "png.workflow" in normalized or "png.prompt" in normalized or '"workflow"' in normalized or "comfyui" in normalized
    hits = sum(1 for marker in ["ksampler", "checkpointloadersimple", "loraloader", '"class_type"', '"inputs"', '"widgets_values"'] if marker in normalized)
    return has_workflow and hits >= 1


def _contains_generation_fields(normalized: str) -> bool:
    fields = ["prompt", "negative prompt", "seed", "cfg", "sampler", "model hash", "model_name", "lora", "checkpoint"]
    return sum(1 for field in fields if field in normalized) >= 2


# D11: every text source hint below comes from words in the text itself —
# lexical evidence that a person writing *about* ChatGPT or AI produces just
# as well. It is shown as "참고: …" with confidence UNKNOWN and never as a
# medium/high attribution.
TEXT_SOURCE_REFERENCE_NOTE = "원문 어휘에서 나온 참고 단서이며, 작성 도구나 작성자를 판별한 결과가 아닙니다."


def guess_text_source(normalized_text: str, ai_identity_hits: int) -> SourceGuess:
    if "chatgpt" in normalized_text or "openai" in normalized_text:
        return _text_reference_guess("참고: 원문에 ChatGPT/OpenAI 언급", "원문에 ChatGPT 또는 OpenAI가 언급되었습니다.")
    if "claude" in normalized_text or "anthropic" in normalized_text:
        return _text_reference_guess("참고: 원문에 Claude/Anthropic 언급", "원문에 Claude 또는 Anthropic이 언급되었습니다.")
    if "gemini" in normalized_text or "bard" in normalized_text:
        return _text_reference_guess("참고: 원문에 Gemini/Bard 언급", "원문에 Gemini 또는 Bard가 언급되었습니다.")
    if ai_identity_hits:
        return _text_reference_guess("참고: AI 어시스턴트 문체 유사", "AI 또는 언어 모델을 언급하는 문구가 있습니다.")
    return SourceGuess.unknown()


def _text_reference_guess(label: str, reason: str) -> SourceGuess:
    return SourceGuess(label, SourceConfidence.UNKNOWN, [reason, TEXT_SOURCE_REFERENCE_NOTE])


def _technical_document_density(text: str, lines: list[str]) -> float:
    """Share of lines that are technical-document structure, not prose.

    Code fences, tables, markdown headers, inline-code-only lines and HTML
    tags make a document look unlike natural prose — they inflate list,
    uniformity, and perplexity signals for structural reasons unrelated
    to who wrote it. Returns 0-1.
    """
    if not lines:
        return 0.0
    structural = 0
    in_fence = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            structural += 1
            continue
        if in_fence:
            structural += 1
            continue
        if (
            stripped.startswith(("#", "|", ">", "- [", "* ["))
            or re.match(r"^</?[a-zA-Z][^>]*>$", stripped)
            or (stripped.count("`") >= 2)
        ):
            structural += 1
    return structural / len(lines)
