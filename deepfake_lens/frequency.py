"""Frequency-domain forensics features (numpy-based, optional).

Implements the physical measurements behind the frequency expert of the
pixel ensemble:

- radial power spectrum slope (natural images decay roughly 1/f^2)
- spectral spikes above the radial average (upsampling / checkerboard
  artifacts leave periodic peaks at fixed frequencies)
- neighboring-pixel interpolation consistency (NPR-inspired: resampled
  regions satisfy linear interpolation relations unusually well)
- per-block DCT high-frequency energy share

These are measurements, not trained detectors; thresholds in the pixel
expert are screening heuristics. Simplified local implementations of ideas
from:
- NPR (Neighboring Pixel Relations): https://github.com/chuangchuangtan/NPR-DeepfakeDetection
- F3-Net frequency-aware learning: https://github.com/yyk-wew/F3Net
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


MIN_FREQUENCY_ANALYSIS_SIDE = 32


@dataclass(frozen=True)
class FrequencyFeatures:
    spectrum_slope: float
    spike_count: int
    max_spike_prominence: float
    npr_consistency: float
    dct_highfreq_ratio: float
    dct_block_uniformity: float

    def to_json(self) -> dict[str, float | int]:
        return asdict(self)


def gray2d(gray: "list[list[float]]") -> "object":
    import numpy as np

    return np.asarray(gray, dtype=np.float64)


def frequency_features(gray_2d) -> FrequencyFeatures:
    """Compute all frequency features for a 2-D grayscale array."""
    import numpy as np

    image = np.asarray(gray_2d, dtype=np.float64)
    if min(image.shape) < MIN_FREQUENCY_ANALYSIS_SIDE:
        raise ValueError("image too small for frequency analysis")

    power, freq_radius = _power_spectrum(image)
    slope = _radial_slope(power, freq_radius)
    spike_count, max_prominence = _spectral_spikes(power, freq_radius)
    npr = _npr_consistency(image)
    hf_ratio, block_uniformity = _dct_block_highfreq(image)
    return FrequencyFeatures(
        spectrum_slope=float(slope),
        spike_count=int(spike_count),
        max_spike_prominence=float(max_prominence),
        npr_consistency=float(npr),
        dct_highfreq_ratio=float(hf_ratio),
        dct_block_uniformity=float(block_uniformity),
    )


def _power_spectrum(image) -> tuple["object", "object"]:
    """Windowed 2-D power spectrum with frequency radius per pixel."""
    import numpy as np

    height, width = image.shape
    window_y = np.hanning(height)[:, None]
    window_x = np.hanning(width)[None, :]
    windowed = (image - image.mean()) * window_y * window_x
    spectrum = np.fft.fftshift(np.fft.fft2(windowed))
    power = np.abs(spectrum) ** 2

    center_y, center_x = height // 2, width // 2
    freq_y = np.fft.fftshift(np.fft.fftfreq(height))[:, None] * height
    freq_x = np.fft.fftshift(np.fft.fftfreq(width))[None, :] * width
    radius = np.hypot(freq_y, freq_x) / max(height, width)
    return power, radius


def _radial_slope(power, radius, low: float = 0.05, high: float = 0.6) -> float:
    """Log-log slope of radially averaged power (natural images ~ -2)."""
    import numpy as np

    mask = (radius >= low) & (radius <= high)
    if not mask.any():
        return 0.0
    log_power = np.log10(power[mask] + 1e-12)
    log_freq = np.log10(radius[mask])
    bins = np.linspace(np.log10(low), np.log10(high), 24)
    indices = np.digitize(log_freq, bins)
    xs, ys = [], []
    for bin_index in range(1, len(bins)):
        members = log_power[indices == bin_index]
        if members.size:
            xs.append((bins[bin_index - 1] + bins[bin_index]) / 2)
            ys.append(float(np.mean(members)))
    if len(xs) < 4:
        return 0.0
    xs_array = np.asarray(xs)
    ys_array = np.asarray(ys)
    slope = np.polyfit(xs_array, ys_array, 1)[0]
    return float(slope)


def _spectral_spikes(power, radius, prominence_threshold: float = 8.0) -> tuple[int, float]:
    """Count frequency bins far above the radial log-power average.

    Checkerboard / transposed-convolution artifacts leave isolated peaks at
    fixed frequencies; a smooth natural spectrum stays near its radial mean.
    Prominence uses the median absolute deviation so a single huge peak does
    not inflate its own normalizer (a plain std would self-mask it).
    """
    import numpy as np

    log_power = np.log10(power + 1e-12)
    radii = np.ravel(radius)
    values = np.ravel(log_power)
    max_radius = float(radii.max()) or 1.0
    bins = np.clip((radii / max_radius * 64).astype(int), 0, 63)
    medians = np.zeros(64)
    scales = np.ones(64)
    for bin_index in range(64):
        members = values[bins == bin_index]
        if members.size:
            medians[bin_index] = np.median(members)
            mad = np.median(np.abs(members - medians[bin_index]))
            scales[bin_index] = 1.4826 * mad + 1e-6
    residual = values - medians[bins]
    normalized = residual / scales[bins]
    spike_mask = normalized > prominence_threshold
    spike_count = int(np.count_nonzero(spike_mask))
    max_prominence = float(normalized[spike_mask].max()) if spike_count else 0.0
    return spike_count, max_prominence


def _npr_consistency(image, residual_limit: float = 1.0) -> float:
    """Share of pixels consistent with linear horizontal/vertical
    interpolation of their neighbors (NPR-inspired upsampling evidence)."""
    import numpy as np

    left = image[:, :-2]
    right = image[:, 2:]
    center = image[:, 1:-1]
    horizontal_residual = np.abs(center - 0.5 * (left + right))
    up = image[:-2, :]
    down = image[2:, :]
    middle = image[1:-1, :]
    vertical_residual = np.abs(middle - 0.5 * (up + down))
    consistent = (
        (horizontal_residual < residual_limit).mean() * 0.5
        + (vertical_residual < residual_limit).mean() * 0.5
    )
    return float(consistent)


def _dct_basis(size: int = 8) -> "object":
    """Orthonormal DCT-II basis matrix: basis[k, n] over input index n."""
    import numpy as np

    ns = np.arange(size)[None, :]
    ks = np.arange(size)[:, None]
    basis = np.cos((2 * ns + 1) * ks * np.pi / (2 * size)) * np.sqrt(2.0 / size)
    basis[0, :] = np.sqrt(1.0 / size)
    return basis


def dct_2d(block) -> "object":
    """Orthonormal 2-D DCT-II of a square block (any size)."""
    import numpy as np

    array = np.asarray(block, dtype=np.float64)
    basis = _dct_basis(array.shape[0])
    return basis @ array @ basis.T


def idct_2d(coefficients) -> "object":
    """Inverse of :func:`dct_2d` (basis is orthonormal, so transpose suffices)."""
    import numpy as np

    array = np.asarray(coefficients, dtype=np.float64)
    basis = _dct_basis(array.shape[0])
    return basis.T @ array @ basis


def _dct_block_highfreq(image, block: int = 8) -> tuple[float, float]:
    """Per-block DCT high-frequency AC energy share and its spatial spread.

    Sensor-noised camera images spread energy broadly; smoothed synthetic
    surfaces and block-consistent recompression leave characteristic
    high-frequency patterns. The value is a measurement, not a verdict.
    """
    import numpy as np

    height, width = image.shape
    blocks_y = height // block
    blocks_x = width // block
    if blocks_y < 2 or blocks_x < 2:
        return 0.0, 0.0
    basis = _dct_basis(block)
    high_mask = _highfreq_mask(block)
    ratios = []
    cropped = image[: blocks_y * block, : blocks_x * block]
    tiles = cropped.reshape(blocks_y, block, blocks_x, block).transpose(0, 2, 1, 3).reshape(-1, block, block)
    for tile in tiles:
        coefficients = basis @ tile @ basis.T
        energy = coefficients**2
        total_ac = energy.sum() - energy[0, 0]
        if total_ac <= 1e-12:
            continue
        ratios.append(float(energy[high_mask].sum() / total_ac))
    if not ratios:
        return 0.0, 0.0
    mean_ratio = float(np.mean(ratios))
    uniformity = float(np.std(ratios))
    return mean_ratio, uniformity


def _highfreq_mask(block: int) -> "object":
    import numpy as np

    rows = np.arange(block)[:, None]
    cols = np.arange(block)[None, :]
    # Outer ring of the 8x8 coefficient matrix (highest 1/4 of frequencies).
    return (rows + cols) >= block


def jpeg_double_compression_score(gray, *, block: int = 8, coeff_pos: tuple[int, int] = (1, 2)) -> tuple[float, str]:
    """Estimate double-JPEG-compression likelihood from block-DCT histograms.

    When a JPEG is recompressed at a different quality, one coefficient's
    quantized value distribution develops periodic gaps at the ratio of the
    two quantization steps. We decode to pixels, recompute 8x8 DCT blocks,
    histogram the chosen mid-frequency coefficient, and measure histogram
    periodicity via normalized autocorrelation over lags 2..8.

    Returns (strength 0..1, detail). A measurement, not a verdict: many
    re-saves happen in innocent pipelines (messaging apps, web uploads).
    """
    import numpy as np

    image = np.asarray(gray, dtype=np.float64)
    height, width = image.shape
    blocks_y, blocks_x = height // block, width // block
    if blocks_y < 4 or blocks_x < 4:
        return 0.0, "블록 수가 부족해 이중압축 측정을 건너뜁니다."
    basis = _dct_basis(block)
    cropped = image[: blocks_y * block, : blocks_x * block]
    tiles = cropped.reshape(blocks_y, block, blocks_x, block).transpose(0, 2, 1, 3).reshape(-1, block, block)
    values = []
    row, col = coeff_pos
    for tile in tiles:
        values.append(float((basis @ tile @ basis.T)[row, col]))
    if len(values) < 64:
        return 0.0, "계수 표본이 부족합니다."
    values = np.asarray(values)
    lo, hi = np.percentile(values, 2), np.percentile(values, 98)
    if hi - lo < 1e-6:
        return 0.0, "계수 분포가 한 값에 몰려 있습니다."
    hist, _ = np.histogram(np.clip(values, lo, hi), bins=64)
    hist = hist.astype(np.float64)
    if hist.sum() <= 0:
        return 0.0, "히스토그램이 비어 있습니다."
    hist = hist - hist.mean()
    norm = float((hist * hist).sum())
    if norm <= 0:
        return 0.0, "히스토그램 편차가 없습니다."
    best = 0.0
    for lag in range(2, 9):
        corr = float((hist[:-lag] * hist[lag:]).sum() / norm)
        best = max(best, corr)
    strength = max(0.0, min(1.0, best))
    detail = (
        f"블록 DCT 계수 히스토그램 주기성 강도 {strength:.2f} "
        "(0.5+ 이면 서로 다른 품질로 재압축된 흔적 후보)"
    )
    return strength, detail


def ela_metrics(gray, *, quality: int = 75, block: int = 16) -> tuple[float, float, str]:
    """Error-Level Analysis: resave the image at a fixed JPEG quality and
    measure per-block recompression error.

    Returns (global_mean_error, region_max_error, detail). A region whose
    error is dramatically above the global mean suggests that region was
    composited after the last save; uniformly low error suggests the file
    was already saved near this quality or never JPEG-compressed.
    """
    import numpy as np

    image = np.asarray(gray, dtype=np.float64)
    try:
        import cv2
    except ImportError:
        return 0.0, 0.0, "cv2가 없어 ELA를 건너뜁니다."
    uint8 = np.clip(image, 0, 255).astype(np.uint8)
    ok, encoded = cv2.imencode(".jpg", uint8, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return 0.0, 0.0, "JPEG 재인코딩 실패."
    resaved = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE).astype(np.float64)
    height = min(image.shape[0], resaved.shape[0])
    width = min(image.shape[1], resaved.shape[1])
    diff = np.abs(image[:height, :width] - resaved[:height, :width])
    blocks_y, blocks_x = height // block, width // block
    if blocks_y < 2 or blocks_x < 2:
        return float(diff.mean()), float(diff.max()), "블록 수 부족 — 전역 오차만 보고합니다."
    tiles = diff[: blocks_y * block, : blocks_x * block].reshape(
        blocks_y, block, blocks_x, block
    ).transpose(0, 2, 1, 3).reshape(-1, block, block)
    block_errors = tiles.mean(axis=(1, 2))
    global_mean = float(block_errors.mean())
    region_max = float(block_errors.max())
    detail = (
        f"ELA 전역 오차 {global_mean:.2f}, 최대 영역 오차 {region_max:.2f} "
        f"(최대/전역 비율 {region_max / max(global_mean, 1e-6):.1f}x)"
    )
    return global_mean, region_max, detail
