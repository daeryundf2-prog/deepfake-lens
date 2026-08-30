"""Remote photoplethysmography (rPPG) screening from face video.

Implements the CHROM method (de Haan & Jeanne, 2013): face-region RGB means
over time are projected onto chrominance signals whose ratio isolates the
blood-volume pulse from motion artifacts. A plausible, stable cardiac peak
in the 0.7-4 Hz band is evidence of a living, camera-captured face; the
absence of any recoverable pulse is a weak suspicion signal only - lighting
and compression can erase it.

Measurement only: this is not a trained deepfake detector.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

PULSE_LOW_HZ = 0.7
PULSE_HIGH_HZ = 4.0
MIN_SECONDS = 8.0
MIN_FACE_FRAMES = 30


@dataclass(frozen=True)
class RppgEvidenceSignal:
    title: str
    detail: str
    weight: int


@dataclass(frozen=True)
class RppgAnalysis:
    score: int
    band: str
    band_label: str
    verdict: str
    signals: list[RppgEvidenceSignal]
    limitations: list[str]
    face_frames: int
    duration_seconds: float
    estimated_bpm: float | None
    peak_snr: float | None
    method: str = "chrom-v1"

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def analyze_rppg(path: Path | str, *, max_frames: int = 600) -> RppgAnalysis:
    """Run the full video -> face ROI -> CHROM pipeline."""
    video_path = Path(path)
    if not video_path.is_file():
        return _error_analysis(f"파일이 존재하지 않습니다: {video_path}")

    try:
        import cv2  # noqa: F401
    except ImportError:
        return _error_analysis("opencv가 설치되어 있지 않습니다. pip install opencv-python으로 설치하세요.")

    samples, fps, duration = _face_rgb_samples(video_path, max_frames=max_frames)
    if len(samples) < MIN_FACE_FRAMES:
        return _error_analysis(
            f"얼굴 영역을 충분히 추적하지 못했습니다 (획득 프레임 {len(samples)}, 최소 {MIN_FACE_FRAMES})."
        )

    return rppg_from_rgb_samples(samples, fps=fps, duration_seconds=duration)


def rppg_from_rgb_samples(
    samples: list[tuple[float, float, float]],
    *,
    fps: float,
    duration_seconds: float | None = None,
) -> RppgAnalysis:
    """CHROM pulse estimation from a per-frame mean-RGB time series."""
    limitations = ["rPPG는 생체 신호 존재 여부의 참고 측정값이며, 신호 부재가 곧 합성 판정이 아닙니다."]
    if duration_seconds is None:
        duration_seconds = len(samples) / fps if fps > 0 else 0.0
    if fps <= 0:
        return _error_analysis("프레임 속도를 알 수 없어 rPPG 분석이 불가합니다.")
    if duration_seconds < MIN_SECONDS:
        return _error_analysis(f"영상이 너무 짧습니다 ({duration_seconds:.1f}초, 최소 {MIN_SECONDS:.0f}초).")

    result = _chrom_pulse(samples, fps)
    if result is None:
        return _error_analysis("신호 분산이 부족해 펄스를 추정할 수 없습니다.")
    bpm, snr = result
    signals: list[RppgEvidenceSignal] = []
    score = 0

    # Empirical floor: the maximum of ~100 spectral-noise bins in the band
    # averages around 5-6x the mean bin power, so only peaks clearly above
    # that are treated as a cardiac pulse.
    if 45.0 <= bpm <= 200.0 and snr >= 8.0:
        verdict = "안정적인 심박 펄스가 검출되어 촬영 기반 실물 영상일 가능성이 있습니다."
        limitations.append("rPPG 펄스는 워터마크/조작 여부와 무관하게 촬영 원본성의 참고 신호입니다.")
    else:
        score = 25
        signals.append(
            RppgEvidenceSignal(
                "생체 펄스 신호 부재",
                f"심박 대역(0.7-4Hz)에서 신뢰할 펄스를 찾지 못했습니다 (SNR {snr:.1f}).",
                25,
            )
        )
        verdict = "얼굴 영역에서 생체 펄스가 회복되지 않았습니다. 조명/압축 영향을 배제할 수 없어 추가 확인이 필요합니다."
        limitations.append("강한 압축, 어두운 조명, 큰 움직임은 펄스를 지울 수 있습니다.")

    band = "medium" if score >= 25 else "low"
    band_label = "주의" if band == "medium" else "낮음"
    return RppgAnalysis(
        score=score,
        band=band,
        band_label=band_label,
        verdict=verdict,
        signals=signals,
        limitations=limitations,
        face_frames=len(samples),
        duration_seconds=duration_seconds,
        estimated_bpm=bpm,
        peak_snr=snr,
    )


def _error_analysis(message: str) -> RppgAnalysis:
    return RppgAnalysis(
        score=0,
        band="unknown",
        band_label="판단 어려움",
        verdict=message,
        signals=[],
        limitations=[message],
        face_frames=0,
        duration_seconds=0.0,
        estimated_bpm=None,
        peak_snr=None,
    )


def _chrom_pulse(samples: list[tuple[float, float, float]], fps: float) -> tuple[float, float] | None:
    """Return (bpm, snr) for the dominant pulse peak, or None when the
    signal carries no usable band energy."""
    try:
        import numpy as np
    except ImportError:
        return None

    data = np.asarray(samples, dtype=np.float64)
    if data.ndim != 2 or data.shape[0] < 32:
        return None

    red, green, blue = data[:, 0], data[:, 1], data[:, 2]
    x_chrom = 3.0 * red - 2.0 * green
    y_chrom = 1.5 * red + green - 1.5 * blue

    def normalize(series):
        centered = series - series.mean()
        std = series.std()
        return centered / std if std > 1e-9 else centered

    x_norm, y_norm = normalize(x_chrom), normalize(y_chrom)

    spectrum_x = np.fft.rfft(x_norm)
    spectrum_y = np.fft.rfft(y_norm)
    frequencies = np.fft.rfftfreq(len(x_norm), d=1.0 / fps)
    band = (frequencies >= PULSE_LOW_HZ) & (frequencies <= PULSE_HIGH_HZ)
    if not band.any():
        return None

    band_x = spectrum_x.copy()
    band_y = spectrum_y.copy()
    band_x[~band] = 0.0
    band_y[~band] = 0.0
    x_band = np.fft.irfft(band_x, n=len(x_norm))
    y_band = np.fft.irfft(band_y, n=len(y_norm))

    std_x = x_band.std()
    std_y = y_band.std()
    if std_x <= 1e-9 or std_y <= 1e-9:
        return None
    alpha = std_x / std_y
    pulse = x_band - alpha * y_band

    power = np.abs(np.fft.rfft(pulse)) ** 2
    band_power = power.copy()
    band_power[~band] = 0.0
    peak_index = int(band_power.argmax())
    peak_power = float(band_power[peak_index])
    if peak_power <= 0.0:
        return None

    inner = (frequencies >= frequencies[peak_index] - 0.2) & (frequencies <= frequencies[peak_index] + 0.2)
    noise_mask = band & ~inner
    noise_power = float(power[noise_mask].mean()) if noise_mask.any() else 0.0
    # No competing in-band energy at all: the peak stands alone.
    snr = peak_power / noise_power if noise_power > 0 else 99.0
    bpm = float(frequencies[peak_index]) * 60.0
    return bpm, snr


def _face_rgb_samples(video_path: Path, *, max_frames: int) -> tuple[list[tuple[float, float, float]], float, float]:
    """Sample mean face-ROI RGB per analysed frame."""
    import cv2
    import numpy as np

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return [], 0.0, 0.0
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        samples: list[tuple[float, float, float]] = []
        frame_index = 0
        stride = max(1, int(fps * 0.2)) if fps > 0 else 1  # ~5 samples/second
        while frame_index < total and len(samples) < max_frames:
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, 1.1, 4)
            if len(faces):
                x, y, w, h = max(faces, key=lambda face: face[2] * face[3])
                roi = frame[max(0, y) : y + h, max(0, x) : x + w]
                if roi.size:
                    mean_b, mean_g, mean_r = (float(channel) for channel in cv2.mean(roi)[:3])
                    samples.append((mean_r, mean_g, mean_b))
            frame_index += stride
        duration = total / fps if fps > 0 else 0.0
        return samples, fps, duration
    finally:
        capture.release()
