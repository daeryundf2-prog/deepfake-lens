"""Multimodal analysis module.

Combines signals from image, text, audio, and video analysis
to provide a unified assessment of content authenticity.

Also provides a real audio/visual sync check: the audio amplitude
envelope (librosa) is cross-correlated with the visual motion envelope
(frame-difference energy via opencv). A correlated-but-shifted pairing
beyond the desync threshold is a suspicion signal — a classic dubbed or
re-timed audio artifact. All heavy deps stay optional extras.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


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


# A/V sync thresholds
AV_SYNC_MAX_LAG_SECONDS = 1.0
AV_SYNC_DESYNC_SECONDS = 0.3
AV_SYNC_MIN_CORRELATION = 0.15
AV_SYNC_DESYNC_WEIGHT = 18
AV_SYNC_MIN_SECONDS = 3.0
AV_SYNC_MIN_SAMPLES = 16


@dataclass(frozen=True)
class AvSyncAnalysis:
    score: int
    band: str
    band_label: str
    verdict: str
    signals: list[MultimodalEvidenceSignal]
    limitations: list[str]
    # Positive offset = the audio envelope is delayed relative to visual
    # motion. None when no reliable cross-correlation peak exists.
    offset_seconds: float | None
    peak_correlation: float | None
    audio_seconds: float
    video_seconds: float
    method: str = "envelope-xcorr-v1"

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
    av_sync: AvSyncAnalysis | None = None,
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

    # A/V sync result, when provided, is a true cross-modal measurement:
    # its suspicion signals enter the score via the cross-modal weight.
    if av_sync is not None:
        modalities_used.append("av-sync")
        for sync_signal in av_sync.signals:
            signals.append(
                MultimodalEvidenceSignal(
                    sync_signal.title,
                    sync_signal.detail,
                    sync_signal.weight,
                    "cross-modal",
                )
            )
        limitations.extend(av_sync.limitations)

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


def analyze_av_sync(
    path: Path | str, *, max_lag_seconds: float = AV_SYNC_MAX_LAG_SECONDS
) -> AvSyncAnalysis:
    """Extract audio and motion envelopes from a video file and measure
    their cross-correlation offset.

    Requires the optional ``audio`` (librosa) and ``video`` (opencv)
    extras; degrades to an error analysis when either is absent or the
    file has no usable audio/video stream.
    """
    video_path = Path(path)
    if not video_path.is_file():
        return _av_sync_error(f"파일이 존재하지 않습니다: {video_path}")

    try:
        import cv2  # noqa: F401
    except ImportError:
        return _av_sync_error("opencv가 설치되어 있지 않습니다. pip install opencv-python으로 설치하세요.")
    try:
        import librosa  # noqa: F401
    except ImportError:
        return _av_sync_error("librosa가 설치되어 있지 않습니다. pip install librosa로 설치하세요.")

    motion_env, motion_rate, video_seconds = _motion_envelope(video_path)
    if motion_env is None:
        return _av_sync_error("비디오 프레임을 읽을 수 없어 모션 신호를 추출하지 못했습니다.")

    audio_env, audio_rate, audio_seconds = _audio_envelope(video_path)
    if audio_env is None:
        return _av_sync_error("오디오 스트림을 읽을 수 없어 A/V 싱크를 측정할 수 없습니다.")

    return av_sync_from_envelopes(
        audio_env,
        motion_env,
        audio_rate=audio_rate,
        motion_rate=motion_rate,
        max_lag_seconds=max_lag_seconds,
        audio_seconds=audio_seconds,
        video_seconds=video_seconds,
    )


def av_sync_from_envelopes(
    audio_env: Sequence[float],
    motion_env: Sequence[float],
    *,
    audio_rate: float,
    motion_rate: float,
    max_lag_seconds: float = AV_SYNC_MAX_LAG_SECONDS,
    audio_seconds: float | None = None,
    video_seconds: float | None = None,
) -> AvSyncAnalysis:
    """Pure envelope-level A/V sync measurement.

    Both envelopes are resampled to the coarser rate and z-normalized; the
    normalized cross-correlation peak over +/- ``max_lag_seconds`` gives
    the offset. Positive offset = audio delayed relative to motion. A
    correlated pairing shifted beyond ``AV_SYNC_DESYNC_SECONDS`` raises a
    suspicion signal; weak correlation degrades to a limitation, never a
    score.
    """
    limitations = [
        "A/V 싱크는 오디오 에너지-모션 에너지 상관 기반 참고 측정값이며, 립싱크 수준의 정밀 측정이 아닙니다."
    ]
    if audio_seconds is None:
        audio_seconds = len(audio_env) / audio_rate if audio_rate > 0 else 0.0
    if video_seconds is None:
        video_seconds = len(motion_env) / motion_rate if motion_rate > 0 else 0.0

    measure = _av_sync_measure(
        audio_env, motion_env, audio_rate, motion_rate, max_lag_seconds
    )
    if measure is None:
        return _av_sync_error(
            "엔벨로프가 너무 짧거나 분산이 없어 A/V 싱크를 측정할 수 없습니다.",
            audio_seconds=audio_seconds,
            video_seconds=video_seconds,
        )

    offset_seconds = measure["offset_seconds"]
    peak_correlation = measure["peak_correlation"]
    signals: list[MultimodalEvidenceSignal] = []
    score = 0

    if peak_correlation < AV_SYNC_MIN_CORRELATION:
        verdict = "오디오-비디오 에너지 상관이 낮아 싱크 일치 여부를 판별할 수 없습니다."
        limitations.append(
            "무음 구간·정지 장면·배경음 위주 오디오는 본래 상관이 낮습니다. 낮은 상관 자체는 의심 신호가 아닙니다."
        )
    elif abs(offset_seconds) > AV_SYNC_DESYNC_SECONDS:
        score = AV_SYNC_DESYNC_WEIGHT
        signals.append(
            MultimodalEvidenceSignal(
                "A/V 싱크 오프셋 의심",
                f"오디오-모션 상관 피크가 {offset_seconds:+.2f}초에서 발생 "
                f"(상관 {peak_correlation:.2f}). 더빙·재타이밍된 오디오 가능성이 있습니다.",
                AV_SYNC_DESYNC_WEIGHT,
                "cross-modal",
            )
        )
        verdict = "오디오와 화면 움직임이 상관되지만 유의미한 시간 오프셋이 있습니다."
        limitations.append("프레임레이트 추정 오차와 인코딩 지연이 소규모 오프셋을 만들 수 있습니다.")
    else:
        verdict = "오디오-모션 에너지가 정상 범위에서 동기화되어 있습니다."

    band = "medium" if score >= 25 else "low"
    band_label = "주의" if band == "medium" else "낮음"
    return AvSyncAnalysis(
        score=score,
        band=band,
        band_label=band_label,
        verdict=verdict,
        signals=signals,
        limitations=limitations,
        offset_seconds=offset_seconds,
        peak_correlation=peak_correlation,
        audio_seconds=audio_seconds,
        video_seconds=video_seconds,
    )


def _av_sync_error(
    message: str, *, audio_seconds: float = 0.0, video_seconds: float = 0.0
) -> AvSyncAnalysis:
    return AvSyncAnalysis(
        score=0,
        band="unknown",
        band_label="판단 어려움",
        verdict=message,
        signals=[],
        limitations=[message],
        offset_seconds=None,
        peak_correlation=None,
        audio_seconds=audio_seconds,
        video_seconds=video_seconds,
    )


def _av_sync_measure(
    audio_env: Sequence[float],
    motion_env: Sequence[float],
    audio_rate: float,
    motion_rate: float,
    max_lag_seconds: float,
) -> dict[str, float] | None:
    """Normalized cross-correlation between the two envelopes.

    Returns {"offset_seconds", "peak_correlation"} or None when the inputs
    cannot support a measurement (too short, zero variance, numpy absent).
    """
    try:
        import numpy as np
    except ImportError:
        return None

    audio = np.asarray(list(audio_env), dtype=np.float64)
    motion = np.asarray(list(motion_env), dtype=np.float64)
    if audio_rate <= 0 or motion_rate <= 0:
        return None

    rate = min(audio_rate, motion_rate)
    audio_r = _resample_linear(audio, audio_rate, rate)
    motion_r = _resample_linear(motion, motion_rate, rate)
    length = min(len(audio_r), len(motion_r))
    audio_r = audio_r[:length] - audio_r[:length].mean()
    motion_r = motion_r[:length] - motion_r[:length].mean()

    if length < AV_SYNC_MIN_SAMPLES:
        return None
    if length / rate < AV_SYNC_MIN_SECONDS:
        return None
    if audio_r.std() <= 1e-9 or motion_r.std() <= 1e-9:
        return None

    max_lag = int(max_lag_seconds * rate)
    best_lag = 0
    best_corr = 0.0
    # corr(lag) = <a(t), m(t - lag)>: a positive lag means the audio
    # feature arrives later than the matching motion feature.
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            a_seg = audio_r[lag:]
            m_seg = motion_r[: length - lag]
        else:
            a_seg = audio_r[: length + lag]
            m_seg = motion_r[-lag:]
        if len(a_seg) < AV_SYNC_MIN_SAMPLES:
            continue
        denom = float(np.linalg.norm(a_seg) * np.linalg.norm(m_seg))
        if denom <= 1e-9:
            continue
        corr = float(np.dot(a_seg, m_seg) / denom)
        # Only positive correlation counts as alignment; an anti-
        # correlated envelope is not a sync match.
        if corr > best_corr:
            best_corr = corr
            best_lag = lag

    return {
        "offset_seconds": best_lag / rate,
        "peak_correlation": best_corr,
    }


def _resample_linear(series, from_rate: float, to_rate: float):
    """Linear-interpolation resampling; identity when rates already match."""
    import numpy as np

    if abs(from_rate - to_rate) < 1e-9 or len(series) == 0:
        return np.asarray(series, dtype=np.float64)
    duration = len(series) / from_rate
    target_len = max(1, int(round(duration * to_rate)))
    positions = np.linspace(0.0, len(series) - 1, num=target_len)
    return np.interp(positions, np.arange(len(series)), series)


def _motion_envelope(video_path: Path):
    """Per-frame mean absolute frame-difference energy via opencv.

    Returns (envelope, rate_hz, video_seconds) or (None, 0, 0) on failure.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None, 0.0, 0.0

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return None, 0.0, 0.0
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        envelope: list[float] = []
        previous = None
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float64)
            if previous is not None:
                envelope.append(float(np.abs(gray - previous).mean()))
            previous = gray
        video_seconds = total / fps if fps > 0 else 0.0
        if not envelope:
            return None, 0.0, video_seconds
        return envelope, fps, video_seconds
    finally:
        capture.release()


def _audio_envelope(video_path: Path):
    """RMS amplitude envelope of the video's audio track via librosa.

    Returns (envelope, rate_hz, audio_seconds) or (None, 0, 0) on failure.
    """
    try:
        import librosa
    except ImportError:
        return None, 0.0, 0.0

    sample_rate = 22050
    hop = 512
    try:
        y, sr = librosa.load(str(video_path), sr=sample_rate, mono=True)
    except Exception:
        return None, 0.0, 0.0
    if y is None or len(y) < hop:
        return None, 0.0, 0.0
    envelope = librosa.feature.rms(y=y, hop_length=hop)[0]
    audio_seconds = len(y) / sr
    if len(envelope) == 0:
        return None, 0.0, audio_seconds
    return [float(v) for v in envelope], sr / hop, audio_seconds
