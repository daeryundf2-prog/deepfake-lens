"""Remote photoplethysmography (rPPG) screening from face video.

Implements the CHROM method (de Haan & Jeanne, 2013): face-region RGB means
over time are projected onto chrominance signals whose ratio isolates the
blood-volume pulse from motion artifacts. A plausible, stable cardiac peak
in the 0.7-4 Hz band is evidence of a living, camera-captured face; the
absence of any recoverable pulse is a weak suspicion signal only - lighting
and compression can erase it.

The face box is additionally split into a 3x3 grid of sub-ROIs and a
CHROM pulse trace is recovered per ROI. Following FakeCatcher (Ciftci et
al., 2020), a real face's pulse is spatially coherent, so the circular
mean resultant length of the per-ROI phases at the shared peak frequency
is reported as ``phase_coherence``; strong decorrelation under a present
global pulse is a weak suspicion signal, not a verdict.

Measurement only: this is not a trained deepfake detector.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

PULSE_LOW_HZ = 0.7
PULSE_HIGH_HZ = 4.0
MIN_SECONDS = 8.0
MIN_FACE_FRAMES = 30
ROI_GRID = 3
MIN_PHASE_COHERENCE = 0.5
INCOHERENT_ROI_WEIGHT = 20


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
    # Number of spatial sub-ROIs the pulse was recovered from (1 = legacy
    # single-box path). phase_coherence is the circular mean resultant
    # length of per-ROI pulse phases at the shared peak frequency: ~1.0
    # coherent (real-face-like), ~0 decorrelated. None when not evaluated.
    roi_count: int = 1
    phase_coherence: float | None = None

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def analyze_rppg(path: Path | str, *, max_frames: int = 600) -> RppgAnalysis:
    """Run the full video -> face ROI grid -> CHROM + coherence pipeline."""
    video_path = Path(path)
    if not video_path.is_file():
        return _error_analysis(f"파일이 존재하지 않습니다: {video_path}")

    try:
        import cv2  # noqa: F401
    except ImportError:
        return _error_analysis("opencv가 설치되어 있지 않습니다. pip install opencv-python으로 설치하세요.")

    aggregate, roi_samples, fps, duration = _face_roi_samples(video_path, max_frames=max_frames)
    if len(aggregate) < MIN_FACE_FRAMES:
        return _error_analysis(
            f"얼굴 영역을 충분히 추적하지 못했습니다 (획득 프레임 {len(aggregate)}, 최소 {MIN_FACE_FRAMES})."
        )

    series = roi_samples if len(roi_samples) >= 2 else [aggregate]
    return _analyze_rppg(series, fps=fps, duration_seconds=duration, face_frames=len(aggregate))


def rppg_from_rgb_samples(
    samples: list[tuple[float, float, float]],
    *,
    fps: float,
    duration_seconds: float | None = None,
) -> RppgAnalysis:
    """CHROM pulse estimation from a per-frame mean-RGB time series.

    Single-ROI entry point kept for backward compatibility; it reports
    ``roi_count=1`` and no ``phase_coherence``.
    """
    return _analyze_rppg(
        [samples], fps=fps, duration_seconds=duration_seconds, face_frames=len(samples)
    )


def rppg_from_roi_samples(
    roi_samples: list[list[tuple[float, float, float]]],
    *,
    fps: float,
    duration_seconds: float | None = None,
) -> RppgAnalysis:
    """Multi-ROI CHROM pulse estimation plus inter-ROI phase coherence.

    ``roi_samples`` is a list of per-ROI mean-RGB time series (e.g. grid
    cells of the face box). The aggregate pulse is the element-wise mean
    across ROIs; coherence is the circular mean resultant length of the
    per-ROI pulse phases at the aggregate peak frequency.
    """
    valid = [series for series in roi_samples if len(series) >= MIN_FACE_FRAMES]
    if not valid:
        return _error_analysis(
            f"충분한 길이의 ROI 시계열이 없습니다 (최소 {MIN_FACE_FRAMES} 프레임)."
        )
    face_frames = min(len(series) for series in valid)
    return _analyze_rppg(
        valid, fps=fps, duration_seconds=duration_seconds, face_frames=face_frames
    )


def _analyze_rppg(
    roi_series: list[list[tuple[float, float, float]]],
    *,
    fps: float,
    duration_seconds: float | None,
    face_frames: int,
) -> RppgAnalysis:
    limitations = ["rPPG는 생체 신호 존재 여부의 참고 측정값이며, 신호 부재가 곧 합성 판정이 아닙니다."]
    if duration_seconds is None:
        duration_seconds = face_frames / fps if fps > 0 else 0.0
    if fps <= 0:
        return _error_analysis("프레임 속도를 알 수 없어 rPPG 분석이 불가합니다.")
    if duration_seconds < MIN_SECONDS:
        return _error_analysis(f"영상이 너무 짧습니다 ({duration_seconds:.1f}초, 최소 {MIN_SECONDS:.0f}초).")

    # Aggregate pulse: element-wise mean across ROIs (== the single series
    # itself on the legacy single-ROI path).
    length = min(len(series) for series in roi_series)
    aggregate = [
        tuple(sum(series[i][c] for series in roi_series) / len(roi_series) for c in range(3))
        for i in range(length)
    ]

    pulse = _chrom_pulse_signal(aggregate, fps)
    if pulse is None:
        return _error_analysis("신호 분산이 부족해 펄스를 추정할 수 없습니다.")
    peak = _pulse_peak(pulse, fps)
    if peak is None:
        return _error_analysis("신호 분산이 부족해 펄스를 추정할 수 없습니다.")
    bpm, snr, peak_hz = peak
    pulse_ok = 45.0 <= bpm <= 200.0 and snr >= 8.0

    # FakeCatcher-style spatial coherence: only meaningful while a real
    # global pulse exists; on noise the per-ROI phases are uniformly random
    # and the number would only masquerade as a measurement.
    phase_coherence: float | None = None
    if len(roi_series) >= 2 and pulse_ok:
        roi_pulses = [_chrom_pulse_signal(series[:length], fps) for series in roi_series]
        phase_coherence = _phase_coherence(
            [p for p in roi_pulses if p is not None], fps, peak_hz
        )

    signals: list[RppgEvidenceSignal] = []
    score = 0

    # Empirical floor: the maximum of ~100 spectral-noise bins in the band
    # averages around 5-6x the mean bin power, so only peaks clearly above
    # that are treated as a cardiac pulse.
    if pulse_ok:
        verdict = "안정적인 심박 펄스가 검출되어 촬영 기반 실물 영상일 가능성이 있습니다."
        limitations.append("rPPG 펄스는 워터마크/조작 여부와 무관하게 촬영 원본성의 참고 신호입니다.")
        if phase_coherence is not None:
            limitations.append(
                f"위상 일관성({phase_coherence:.2f})은 ROI별 펄스가 동일 생체 신호에서 오는지의 참고 지표입니다."
            )
            if phase_coherence < MIN_PHASE_COHERENCE:
                score += INCOHERENT_ROI_WEIGHT
                signals.append(
                    RppgEvidenceSignal(
                        "ROI 펄스 위상 불일치",
                        f"전역 펄스는 존재하나 ROI 간 위상 일관성({phase_coherence:.2f})이 낮아 "
                        "영역별 펄스가 하나의 생체 신호에서 오지 않을 수 있습니다.",
                        INCOHERENT_ROI_WEIGHT,
                    )
                )
                verdict = (
                    "심박 펄스는 검출되었으나 얼굴 영역 간 위상이 불일치합니다. "
                    "조명 불균일·압축 영향을 배제할 수 없어 추가 확인이 필요합니다."
                )
        elif len(roi_series) >= 2:
            limitations.append("ROI별 펄스 분산이 부족해 위상 일관성은 미평가입니다.")
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
        if len(roi_series) >= 2:
            limitations.append("전역 펄스가 없어 ROI 간 위상 일관성은 의미가 없어 미평가입니다.")

    band = "medium" if score >= 25 else "low"
    band_label = "주의" if band == "medium" else "낮음"
    return RppgAnalysis(
        score=score,
        band=band,
        band_label=band_label,
        verdict=verdict,
        signals=signals,
        limitations=limitations,
        face_frames=face_frames,
        duration_seconds=duration_seconds,
        estimated_bpm=bpm,
        peak_snr=snr,
        method="chrom-multiroi-v1" if len(roi_series) >= 2 else "chrom-v1",
        roi_count=len(roi_series),
        phase_coherence=phase_coherence,
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


def _chrom_pulse_signal(samples: list[tuple[float, float, float]], fps: float):
    """Band-filtered CHROM pulse trace, or None when the signal carries no
    usable variance."""
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
    return x_band - alpha * y_band


def _pulse_peak(pulse, fps: float) -> tuple[float, float, float] | None:
    """Return (bpm, snr, peak_hz) for the dominant in-band peak of a
    band-filtered pulse trace."""
    try:
        import numpy as np
    except ImportError:
        return None

    frequencies = np.fft.rfftfreq(len(pulse), d=1.0 / fps)
    band = (frequencies >= PULSE_LOW_HZ) & (frequencies <= PULSE_HIGH_HZ)
    if not band.any():
        return None

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
    return bpm, snr, float(frequencies[peak_index])


def _phase_coherence(pulses, fps: float, peak_hz: float) -> float | None:
    """Circular mean resultant length of per-ROI pulse phases at the shared
    peak frequency: ~1.0 means the ROIs pulse in lockstep (real-face-like),
    ~0 means decorrelated phases. Needs at least two usable ROI traces."""
    try:
        import numpy as np
    except ImportError:
        return None

    phases = []
    for pulse in pulses:
        frequencies = np.fft.rfftfreq(len(pulse), d=1.0 / fps)
        peak_index = int(np.argmin(np.abs(frequencies - peak_hz)))
        coefficient = np.fft.rfft(pulse)[peak_index]
        if abs(coefficient) <= 1e-9:
            continue
        phases.append(float(np.angle(coefficient)))

    if len(phases) < 2:
        return None
    return float(abs(np.mean(np.exp(1j * np.asarray(phases)))))


def _face_roi_samples(
    video_path: Path, *, max_frames: int
) -> tuple[list[tuple[float, float, float]], list[list[tuple[float, float, float]]], float, float]:
    """Sample per analysed frame the whole-face mean RGB plus a 3x3 grid of
    sub-ROI means.

    Returns (aggregate_series, roi_series, fps, duration) where
    roi_series has one series per grid cell. Small faces still produce a
    grid — the cells are noisier, which the per-ROI degenerate-signal
    guards absorb — so every sampled frame stays aligned.
    """
    import cv2
    import numpy as np

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return [], [], 0.0, 0.0
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        aggregate: list[tuple[float, float, float]] = []
        roi_frames: list[list[tuple[float, float, float]]] = []
        frame_index = 0
        stride = max(1, int(fps * 0.2)) if fps > 0 else 1  # ~5 samples/second
        while frame_index < total and len(aggregate) < max_frames:
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
                    aggregate.append((mean_r, mean_g, mean_b))
                    roi_frames.append(_grid_cell_means(roi, grid=ROI_GRID))
            frame_index += stride
        # Transpose per-frame cell lists into per-cell series.
        roi_series = [list(series) for series in zip(*roi_frames)] if roi_frames else []
        duration = total / fps if fps > 0 else 0.0
        return aggregate, roi_series, fps, duration
    finally:
        capture.release()


def _grid_cell_means(roi, *, grid: int) -> list[tuple[float, float, float]]:
    """Mean RGB per cell of a grid x grid split of the face ROI."""
    import cv2

    height, width = roi.shape[:2]
    cells: list[tuple[float, float, float]] = []
    for row in range(grid):
        for col in range(grid):
            y1, y2 = row * height // grid, (row + 1) * height // grid
            x1, x2 = col * width // grid, (col + 1) * width // grid
            cell = roi[y1:y2, x1:x2]
            if cell.size == 0:
                cells.append((0.0, 0.0, 0.0))
                continue
            mean_b, mean_g, mean_r = (float(channel) for channel in cv2.mean(cell)[:3])
            cells.append((mean_r, mean_g, mean_b))
    return cells
