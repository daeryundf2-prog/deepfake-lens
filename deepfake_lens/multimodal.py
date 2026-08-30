"""Multimodal analysis module.

Combines signals from image, text, audio, and video analysis
to provide a unified assessment of content authenticity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class MultimodalEvidenceSignal:
    title: str
    detail: str
    weight: int
    source_modality: str


@dataclass(frozen=True)
class MultimodalAnalysis:
    score: int
    band: str
    band_label: str
    verdict: str
    signals: list[MultimodalEvidenceSignal]
    limitations: list[str]
    modalities_used: list[str]
    consistency_score: float
    overall_ai_probability: float

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def analyze_multimodal(
    image_score: int | None = None,
    text_score: int | None = None,
    audio_score: int | None = None,
    video_score: int | None = None,
    image_source_guess: str | None = None,
    text_source_guess: str | None = None,
    audio_source_guess: str | None = None,
    video_source_guess: str | None = None,
) -> MultimodalAnalysis:
    """Combine signals from multiple modalities into a unified analysis."""
    signals: list[MultimodalEvidenceSignal] = []
    limitations: list[str] = []
    modalities_used: list[str] = []

    scores: list[tuple[int, str]] = []
    source_guesses: list[tuple[str, str]] = []

    if image_score is not None:
        scores.append((image_score, "image"))
        modalities_used.append("image")
        if image_score >= 67:
            signals.append(MultimodalEvidenceSignal(
                "이미지 강한 의심",
                f"이미지 분석 점수({image_score})가 높습니다.",
                30,
                "image",
            ))
        elif image_score >= 35:
            signals.append(MultimodalEvidenceSignal(
                "이미지 의심",
                f"이미지 분석 점수({image_score})가 중간입니다.",
                15,
                "image",
            ))
        if image_source_guess and image_source_guess != "unknown":
            source_guesses.append((image_source_guess, "image"))

    if text_score is not None:
        scores.append((text_score, "text"))
        modalities_used.append("text")
        if text_score >= 67:
            signals.append(MultimodalEvidenceSignal(
                "텍스트 강한 의심",
                f"텍스트 분석 점수({text_score})가 높습니다.",
                25,
                "text",
            ))
        elif text_score >= 35:
            signals.append(MultimodalEvidenceSignal(
                "텍스트 의심",
                f"텍스트 분석 점수({text_score})가 중간입니다.",
                12,
                "text",
            ))
        if text_source_guess and text_source_guess != "unknown":
            source_guesses.append((text_source_guess, "text"))

    if audio_score is not None:
        scores.append((audio_score, "audio"))
        modalities_used.append("audio")
        if audio_score >= 67:
            signals.append(MultimodalEvidenceSignal(
                "오디오 강한 의심",
                f"오디오 분석 점수({audio_score})가 높습니다.",
                28,
                "audio",
            ))
        elif audio_score >= 35:
            signals.append(MultimodalEvidenceSignal(
                "오디오 의심",
                f"오디오 분석 점수({audio_score})가 중간입니다.",
                14,
                "audio",
            ))
        if audio_source_guess and audio_source_guess != "unknown":
            source_guesses.append((audio_source_guess, "audio"))

    if video_score is not None:
        scores.append((video_score, "video"))
        modalities_used.append("video")
        if video_score >= 67:
            signals.append(MultimodalEvidenceSignal(
                "비디오 강한 의심",
                f"비디오 분석 점수({video_score})가 높습니다.",
                26,
                "video",
            ))
        elif video_score >= 35:
            signals.append(MultimodalEvidenceSignal(
                "비디오 의심",
                f"비디오 분석 점수({video_score})가 중간입니다.",
                13,
                "video",
            ))
        if video_source_guess and video_source_guess != "unknown":
            source_guesses.append((video_source_guess, "video"))

    # Calculate consistency score
    consistency_score = _calculate_consistency(scores, source_guesses)

    # Check for cross-modality inconsistencies
    inconsistency_signal = _check_inconsistency(scores, source_guesses)
    if inconsistency_signal:
        signals.append(inconsistency_signal)

    # Limitations
    if len(modalities_used) < 2:
        limitations.append("단일 모달리티만 분석되어 멀티모달 비교가 불가합니다.")
    limitations.append("멀티모달 분석은 각 모달리티 분석의 종합이며, 개별 분석의 정확도에 의존합니다.")

    # Calculate overall score: the mean modality score, plus the weights of
    # any cross-modal disagreement signals. Consistency is reported as a
    # diagnostic and must not raise suspicion by itself — agreement between
    # clean modalities is not evidence of AI generation.
    cross_modal_weight = sum(
        signal.weight for signal in signals if signal.source_modality == "cross-modal"
    )
    if scores:
        base_score = sum(s for s, _ in scores) / len(scores)
        score = min(100, int(round(base_score + cross_modal_weight)))
    else:
        score = 0

    if score >= 67:
        band = "high"
        band_label = "높음"
        verdict = "멀티모달 분석에서 AI 생성 의심 신호가 강합니다."
    elif score >= 35:
        band = "medium"
        band_label = "주의"
        verdict = "멀티모달 분석에서 몇 가지 의심 신호가 보여 추가 확인이 필요합니다."
    else:
        band = "low"
        band_label = "낮음"
        verdict = "멀티모달 분석에서 뚜렷한 AI 생성 의심 신호는 적습니다."

    overall_ai_probability = min(1.0, score / 100.0)

    return MultimodalAnalysis(
        score=score,
        band=band,
        band_label=band_label,
        verdict=verdict,
        signals=signals,
        limitations=limitations,
        modalities_used=modalities_used,
        consistency_score=consistency_score,
        overall_ai_probability=overall_ai_probability,
    )


def _calculate_consistency(
    scores: list[tuple[int, str]],
    source_guesses: list[tuple[str, str]],
) -> float:
    """Calculate consistency score across modalities."""
    if len(scores) < 2:
        return 0.5  # Neutral for single modality

    # Check if scores are consistent (all high or all low)
    score_values = [s for s, _ in scores]
    mean_score = sum(score_values) / len(score_values)
    variance = sum((s - mean_score) ** 2 for s in score_values) / len(score_values)
    std_dev = variance ** 0.5

    # Low variance = high consistency
    consistency = max(0.0, 1.0 - (std_dev / 50.0))

    # Bonus if source guesses agree
    if len(source_guesses) >= 2:
        unique_sources = set(s for s, _ in source_guesses)
        if len(unique_sources) == 1:
            consistency = min(1.0, consistency + 0.2)

    return consistency


def _check_inconsistency(
    scores: list[tuple[int, str]],
    source_guesses: list[tuple[str, str]],
) -> MultimodalEvidenceSignal | None:
    """Check for cross-modality inconsistencies."""
    if len(scores) < 2:
        return None

    score_values = [s for s, _ in scores]
    modalities = [m for _, m in scores]

    # Check for large score differences
    max_score = max(score_values)
    min_score = min(score_values)

    if max_score - min_score > 40:
        high_modality = modalities[score_values.index(max_score)]
        low_modality = modalities[score_values.index(min_score)]
        return MultimodalEvidenceSignal(
            "멀티모달 불일치",
            f"{high_modality}({max_score})와 {low_modality}({min_score}) 점수 차이가 큽니다.",
            20,
            "cross-modal",
        )

    # Check for source guess inconsistencies
    if len(source_guesses) >= 2:
        unique_sources = set(s for s, _ in source_guesses)
        if len(unique_sources) > 1:
            return MultimodalEvidenceSignal(
                "출처 불일치",
                f"다양한 모달리티에서 다른 출처가 추정됩니다: {', '.join(unique_sources)}",
                15,
                "cross-modal",
            )

    return None
