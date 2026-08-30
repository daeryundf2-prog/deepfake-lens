"""Audio deepfake detection module.

Detects AI-generated, cloned, and synthesized audio using spectral analysis,
acoustic feature extraction, and heuristic scoring.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus"}
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_SEGMENT_SECONDS = 30
MAX_AUDIO_BYTES = 100 * 1024 * 1024  # 100 MB


@dataclass(frozen=True)
class AudioEvidenceSignal:
    title: str
    detail: str
    weight: int


@dataclass(frozen=True)
class AudioFeatures:
    sample_rate: int
    duration_seconds: float
    rms_energy: float
    zero_crossing_rate: float
    spectral_centroid: float
    spectral_bandwidth: float
    spectral_rolloff: float
    spectral_flatness: float
    pitch_mean: float
    pitch_std: float
    formant_frequencies: list[float]
    mfcc_means: list[float]
    mfcc_stds: list[float]
    tempo: float
    onset_rate: float
    jitter: float = 0.0
    shimmer: float = 0.0


@dataclass(frozen=True)
class AudioAnalysis:
    score: int
    band: str
    band_label: str
    verdict: str
    signals: list[AudioEvidenceSignal]
    limitations: list[str]
    source_guess: str
    features: AudioFeatures | None = None
    model_name: str = "local-audio-heuristic-v1"

    def to_json(self) -> dict[str, object]:
        data = asdict(self)
        return data


def analyze_audio(
    path: Path | str,
    *,
    segment_seconds: int = DEFAULT_SEGMENT_SECONDS,
) -> AudioAnalysis:
    """Analyze an audio file for signs of AI generation or voice cloning."""
    audio_path = Path(path)
    if not audio_path.is_file():
        return _error_analysis(f"파일이 존재하지 않습니다: {audio_path}")

    extension = audio_path.suffix.lower()
    if extension not in SUPPORTED_AUDIO_EXTENSIONS:
        return _error_analysis(f"지원하지 않는 오디오 형식입니다: {extension}")

    try:
        file_size = audio_path.stat().st_size
    except OSError as exc:
        return _error_analysis(f"파일 정보를 읽을 수 없습니다: {exc}")

    if file_size > MAX_AUDIO_BYTES:
        return _error_analysis(f"파일이 너무 큽니다: {file_size} bytes (최대 {MAX_AUDIO_BYTES})")

    if file_size == 0:
        return _error_analysis("파일이 비어 있습니다.")

    features = _extract_features(audio_path, segment_seconds=segment_seconds)
    if features is None:
        return _error_analysis("오디오 특징을 추출할 수 없습니다. librosa가 설치되어 있는지 확인하세요.")

    signals: list[AudioEvidenceSignal] = []
    limitations: list[str] = []

    # Pitch analysis
    pitch_signal = _pitch_analysis(features)
    if pitch_signal:
        signals.append(pitch_signal)

    # Spectral analysis
    spectral_signal = _spectral_analysis(features)
    if spectral_signal:
        signals.append(spectral_signal)

    # Fluency analysis
    fluency_signal = _fluency_analysis(features)
    if fluency_signal:
        signals.append(fluency_signal)

    # MFCC analysis
    mfcc_signal = _mfcc_analysis(features)
    if mfcc_signal:
        signals.append(mfcc_signal)

    # Energy consistency
    energy_signal = _energy_analysis(features)
    if energy_signal:
        signals.append(energy_signal)

    # Spectral flatness (noise analysis)
    noise_signal = _noise_analysis(features)
    if noise_signal:
        signals.append(noise_signal)

    # Voice-quality regularity (jitter/shimmer)
    regularity_signal = _regularity_analysis(features)
    if regularity_signal:
        signals.append(regularity_signal)

    # Source guess
    source_guess = _guess_audio_source(features)

    # Limitations
    if features.duration_seconds < 3.0:
        limitations.append("오디오가 너무 짧아 안정적인 분석이 어렵습니다.")
    if features.duration_seconds > 300:
        limitations.append("매우 긴 오디오는 구간별 분석이 필요합니다.")
    limitations.append("로컬 휴리스틱 기반 선별 결과이며, 확정적 판별이 아닙니다.")

    score = min(100, sum(signal.weight for signal in signals))

    if score >= 67:
        band = "high"
        band_label = "높음"
        verdict = "오디오에서 AI 생성/합성 의심 신호가 강합니다."
    elif score >= 35:
        band = "medium"
        band_label = "주의"
        verdict = "오디오에서 몇 가지 의심 신호가 보여 추가 확인이 필요합니다."
    else:
        band = "low"
        band_label = "낮음"
        verdict = "오디오에서 뚜렷한 합성 의심 신호는 적습니다."

    return AudioAnalysis(
        score=score,
        band=band,
        band_label=band_label,
        verdict=verdict,
        signals=signals,
        limitations=limitations,
        source_guess=source_guess,
        features=features,
    )


def _error_analysis(message: str) -> AudioAnalysis:
    return AudioAnalysis(
        score=0,
        band="unknown",
        band_label="판단 어려움",
        verdict=message,
        signals=[],
        limitations=[message],
        source_guess="unknown",
    )


def _extract_features(path: Path, *, segment_seconds: int) -> AudioFeatures | None:
    """Extract acoustic features from audio file using librosa."""
    try:
        import librosa
        import numpy as np
    except ImportError:
        return None

    try:
        y, sr = librosa.load(str(path), sr=DEFAULT_SAMPLE_RATE, duration=segment_seconds)
    except Exception:
        return None

    if len(y) == 0:
        return None

    duration = len(y) / sr

    # RMS energy
    rms = librosa.feature.rms(y=y)[0]
    rms_energy = float(np.mean(rms))

    # Zero crossing rate
    zcr = librosa.feature.zero_crossing_rate(y)[0]
    zero_crossing_rate = float(np.mean(zcr))

    # Spectral features
    spectral_centroid = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
    spectral_bandwidth = float(np.mean(librosa.feature.spectral_bandwidth(y=y, sr=sr)))
    spectral_rolloff = float(np.mean(librosa.feature.spectral_rolloff(y=y, sr=sr)))
    spectral_flatness = float(np.mean(librosa.feature.spectral_flatness(y=y)))

    # Pitch analysis
    pitches, magnitudes = librosa.piptrack(y=y, sr=sr)
    pitch_values = []
    for t in range(pitches.shape[1]):
        idx = magnitudes[:, t].argmax()
        if magnitudes[idx, t] > 0 and pitches[idx, t] > 0:
            pitch_values.append(float(pitches[idx, t]))
    pitch_mean = float(np.mean(pitch_values)) if pitch_values else 0.0
    pitch_std = float(np.std(pitch_values)) if pitch_values else 0.0
    jitter = _relative_successive_variation(pitch_values)
    shimmer = _relative_successive_variation([float(value) for value in rms])

    # Formant estimation (simplified)
    formants = _estimate_formants(y, sr)

    # MFCC
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    mfcc_means = [float(np.mean(mfcc[i])) for i in range(13)]
    mfcc_stds = [float(np.std(mfcc[i])) for i in range(13)]

    # Tempo and onset
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    tempo_val = float(tempo) if isinstance(tempo, (int, float)) else float(tempo[0]) if len(tempo) > 0 else 0.0
    onset_frames = librosa.onset.onset_detect(y=y, sr=sr)
    onset_rate = len(onset_frames) / max(0.001, duration)

    return AudioFeatures(
        sample_rate=sr,
        duration_seconds=duration,
        rms_energy=rms_energy,
        zero_crossing_rate=zero_crossing_rate,
        spectral_centroid=spectral_centroid,
        spectral_bandwidth=spectral_bandwidth,
        spectral_rolloff=spectral_rolloff,
        spectral_flatness=spectral_flatness,
        pitch_mean=pitch_mean,
        pitch_std=pitch_std,
        formant_frequencies=formants,
        mfcc_means=mfcc_means,
        mfcc_stds=mfcc_stds,
        tempo=tempo_val,
        onset_rate=onset_rate,
        jitter=jitter,
        shimmer=shimmer,
    )


def _relative_successive_variation(values: list[float]) -> float:
    """Mean absolute successive difference over the mean (jitter/shimmer)."""
    if len(values) < 3:
        return 0.0
    mean_value = sum(abs(value) for value in values) / len(values)
    if mean_value <= 1e-9:
        return 0.0
    variation = sum(abs(values[i] - values[i - 1]) for i in range(1, len(values))) / (len(values) - 1)
    return variation / mean_value


def _estimate_formants(y, sr: int) -> list[float]:
    """Estimate formant frequencies using LPC analysis.

    Solves the autocorrelation normal equations R a = r, where R is the
    Toeplitz matrix of r[0..p-1] and r = r[1..p]; the polynomial
    A(z) = 1 - a1 z^-1 - ... then has the vocal-tract resonances as roots.
    """
    try:
        import numpy as np
        import scipy.linalg
    except ImportError:
        return []

    try:
        # Pre-emphasis
        pre_emphasized = np.append(y[0], y[1:] - 0.97 * y[:-1])

        # Framing
        frame_length = int(0.025 * sr)
        frame_step = int(0.010 * sr)
        order = min(10, frame_length - 1)
        if frame_length <= order:
            return []

        frames = [
            pre_emphasized[i : i + frame_length]
            for i in range(0, len(pre_emphasized) - frame_length, frame_step)
        ]
        if not frames:
            return []

        # Window and LPC
        windowed = np.array(frames) * np.hamming(frame_length)
        formant_list = []
        for frame in windowed[:5]:  # Use first 5 frames
            try:
                corr = np.correlate(frame, frame, mode="full")[frame_length - 1 :]
                r0 = corr[: order + 1]
                if r0[0] <= 0:
                    continue
                a = scipy.linalg.solve_toeplitz(r0[:order], r0[1:])
                poly = np.concatenate(([1.0], -a))
                roots = np.roots(poly)
                roots = roots[np.imag(roots) >= 0]
                formant_freqs = sorted(abs(np.angle(roots)) * (sr / (2 * np.pi)))
                formant_list.append([f for f in formant_freqs if 90 < f < 5000][:4])
            except Exception:
                continue

        if not formant_list:
            return []
        # Frames can yield different numbers of in-band roots; average each
        # formant index over the frames that produced it instead of calling
        # np.mean on a ragged list.
        max_formants = max(len(freqs) for freqs in formant_list)
        averaged = []
        for i in range(max_formants):
            values = [freqs[i] for freqs in formant_list if len(freqs) > i]
            averaged.append(float(np.mean(values)))
        return averaged
    except Exception:
        return []


def _pitch_analysis(features: AudioFeatures) -> AudioEvidenceSignal | None:
    """Analyze pitch patterns for signs of synthesis."""
    if features.pitch_mean == 0 and features.pitch_std == 0:
        return None

    # Unnaturally stable pitch (low std)
    if features.pitch_std < 2.0 and features.duration_seconds > 5.0:
        return AudioEvidenceSignal(
            "비자연적 피치 안정성",
            f"피치 표준편차가 {features.pitch_std:.1f}Hz로 매우 낮아 합성일 수 있습니다.",
            25,
        )

    # Unnaturally high pitch variability (cloning artifacts)
    if features.pitch_std > 80.0:
        return AudioEvidenceSignal(
            "비정상적 피치 변동",
            f"피치 표준편차가 {features.pitch_std:.1f}Hz로 비정상적으로 높습니다.",
            20,
        )

    # Suspicious pitch range
    if features.pitch_mean > 400 and features.pitch_std < 5:
        return AudioEvidenceSignal(
            "합성 의심 피치 패턴",
            f"높은 평균 피치({features.pitch_mean:.0f}Hz)와 안정적인 변동이 동시에 나타납니다.",
            18,
        )

    return None


def _spectral_analysis(features: AudioFeatures) -> AudioEvidenceSignal | None:
    """Analyze spectral characteristics for synthesis artifacts."""
    # Unnaturally smooth spectrum (low bandwidth)
    if features.spectral_bandwidth < 1000 and features.spectral_centroid > 2000:
        return AudioEvidenceSignal(
            "매끄러운 스펙트럼",
            f"스펙트럼 대역폭({features.spectral_bandwidth:.0f})이 좁고 중심 주파수({features.spectral_centroid:.0f})가 높아 합성 의심.",
            22,
        )

    # Unnaturally flat spectrum (noise-like)
    if features.spectral_flatness > 0.8:
        return AudioEvidenceSignal(
            "플랫 스펙트럼",
            f"스펙트럼 평탄도({features.spectral_flatness:.2f})가 높아 노이즈 패턴이 감지됩니다.",
            15,
        )

    # Very high rolloff (missing high frequencies)
    if features.spectral_rolloff < 3000 and features.spectral_centroid > 1500:
        return AudioEvidenceSignal(
            "고주파 결핍",
            f"스펙트럼 로loff({features.spectral_rolloff:.0f}Hz)가 낮아 고주파 대역이 부족합니다.",
            12,
        )

    return None


def _fluency_analysis(features: AudioFeatures) -> AudioEvidenceSignal | None:
    """Analyze speech fluency patterns."""
    # Unnaturally consistent onset rate
    if features.onset_rate > 15 and features.tempo > 140:
        return AudioEvidenceSignal(
            "비자연적 발화 속도",
            f"온셋 레이트({features.onset_rate:.1f})와 템포({features.tempo:.0f})가 높아 빠르고 균일한 발화입니다.",
            18,
        )

    # Too regular rhythm: onset rate close to the beat rate in Hz.
    # tempo is BPM, so convert to beats per second before comparing with the
    # onset rate (onsets per second).
    if features.tempo > 0 and features.onset_rate > 0:
        beat_rate_hz = features.tempo / 60.0
        ratio = features.onset_rate / beat_rate_hz
        if 0.8 < ratio < 1.2 and features.duration_seconds > 10:
            return AudioEvidenceSignal(
                "균일한 리듬 패턴",
                "발화 리듬이 비정상적으로 균일합니다.",
                10,
            )

    return None


def _mfcc_analysis(features: AudioFeatures) -> AudioEvidenceSignal | None:
    """Analyze MFCC patterns for synthesis signatures."""
    if not features.mfcc_stds:
        return None

    # Unnaturally stable MFCCs (low variance across frames)
    avg_std = sum(features.mfcc_stds) / len(features.mfcc_stds)
    if avg_std < 1.0 and features.duration_seconds > 5.0:
        return AudioEvidenceSignal(
            "안정적 음향 특성",
            f"MFCC 평균 표준편차({avg_std:.2f})가 낮아 음향 특성이 비정상적으로 안정적입니다.",
            20,
        )

    # High MFCC variance (cloning artifacts)
    if avg_std > 15.0:
        return AudioEvidenceSignal(
            "음향 특성 불안정",
            f"MFCC 평균 표준편차({avg_std:.2f})가 높아 클로닝 아티팩트 가능성이 있습니다.",
            15,
        )

    return None


def _energy_analysis(features: AudioFeatures) -> AudioEvidenceSignal | None:
    """Analyze energy patterns."""
    # Very low energy (possible synthetic silence)
    if features.rms_energy < 0.001 and features.duration_seconds > 5:
        return AudioEvidenceSignal(
            "낮은 에너지 레벨",
            "RMS 에너지가 매우 낮아 합성된 무음일 수 있습니다.",
            10,
        )

    # Unnaturally consistent energy
    if features.zero_crossing_rate < 0.02 and features.rms_energy > 0.01:
        return AudioEvidenceSignal(
            "일정한 에너지 패턴",
            "제로크로싱 비율이 낮고 에너지가 일정합니다.",
            8,
        )

    return None


def _noise_analysis(features: AudioFeatures) -> AudioEvidenceSignal | None:
    """Analyze noise floor characteristics."""
    # High spectral flatness indicates noise
    if features.spectral_flatness > 0.6:
        return AudioEvidenceSignal(
            "높은 노이즈 플로어",
            f"스펙트럼 평탄도({features.spectral_flatness:.2f})가 높아 노이즈 레벨이 높습니다.",
            12,
        )

    # Unusually low noise (possible denoising/synthesis)
    if features.spectral_flatness < 0.01 and features.spectral_centroid > 2000:
        return AudioEvidenceSignal(
            "비정상적으로 깨끗한 오디오",
            "스펙트럼 평탄도가 매우 낮아 인위적으로 정제된 오디오일 수 있습니다.",
            10,
        )

    return None


def _regularity_analysis(features: AudioFeatures) -> AudioEvidenceSignal | None:
    """Voice-quality regularity metrics (jitter/shimmer).

    Natural voiced speech carries measurable pitch and amplitude micro-
    variation (jitter ~1-2%, shimmer ~3-5%); strongly regular series are a
    weak synthesis hint. Estimator noise means the thresholds are loose and
    the weight stays low.
    """
    if features.duration_seconds < 5.0:
        return None
    if 0 < features.jitter < 0.004 and features.pitch_mean > 0:
        return AudioEvidenceSignal(
            "비정상적으로 규칙적인 피치 미세변동",
            f"피치 지터({features.jitter * 100:.2f}%)가 자연 발화 범위보다 낮습니다.",
            12,
        )
    if 0 < features.shimmer < 0.01:
        return AudioEvidenceSignal(
            "균일한 진폭 변동",
            f"진폭 쉬머({features.shimmer * 100:.2f}%)가 비정상적으로 균일합니다.",
            8,
        )
    return None


def _guess_audio_source(features: AudioFeatures) -> str:
    """Guess the likely source based on audio characteristics."""
    if features.spectral_flatness > 0.7:
        return "합성/노이즈 의심"
    if features.pitch_std < 3 and features.duration_seconds > 5:
        return "TTS/음성 합성 의심"
    if features.spectral_bandwidth < 800:
        return "저품질 압축 또는 합성"
    return "unknown"
