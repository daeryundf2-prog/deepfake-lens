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


def copy_move_score(gray, *, block: int = 16, stride: int = 4, min_offset: int = 20, mad_threshold: float = 4.0) -> tuple[float, str]:
    """Block-matching copy-move detection: duplicated tiles recurring at a
    non-local offset.

    Copy-move forgery clones a region within the same image. Tiles are
    bucketed by a coarse (mean, std, low-frequency DCT) key, then each
    candidate pair is *verified* by mean absolute difference on a
    box-blurred tile — the bucket alone collides on noisy content, and
    verification happens on smoothed pixels because a clone whose offset
    is not a multiple of the sampling stride lands up to a few pixels
    off-grid, which pixel-exact comparison would miss on textured content.
    Rotation/scale-invariant clones are out of scope.

    Returns (duplicated_ratio, detail) — the fraction of tiles participating
    in a verified non-local duplicate.
    """
    import numpy as np

    image = np.asarray(gray, dtype=np.float64)
    height, width = image.shape
    if height < block * 3 or width < block * 3:
        return 0.0, "이미지가 작아 copy-move 분석을 건너뜁니다."
    # Box-blur once so sub-stride offsets (a clone at a non-multiple-of-
    # stride shift) still verify on smoothed content.
    pad = np.pad(image, 1, mode="edge")
    blurred = (
        pad[:-2, :-2] + pad[:-2, 1:-1] + pad[:-2, 2:]
        + pad[1:-1, :-2] + pad[1:-1, 1:-1] + pad[1:-1, 2:]
        + pad[2:, :-2] + pad[2:, 1:-1] + pad[2:, 2:]
    ) / 9.0
    basis = _dct_basis(8)
    fingerprints: dict[tuple, list[tuple[int, int]]] = {}
    total = 0
    for y in range(0, height - block + 1, stride):
        for x in range(0, width - block + 1, stride):
            tile = blurred[y : y + block, x : x + block]
            # Flat tiles carry no clone evidence — a uniform sky or wall
            # matches everywhere, so exclude them (clones OF flat regions
            # are undetectable anyway; an honest coverage boundary).
            if tile.std() < 6.0:
                total += 1
                continue
            sub = tile[:8, :8]
            coeffs = basis.T @ sub @ basis
            key = (
                int(round(tile.mean() / 8.0)),
                int(round(tile.std() / 8.0)),
                int(round(coeffs[0, 1] / 16.0)),
                int(round(coeffs[1, 0] / 16.0)),
                int(round(coeffs[1, 1] / 16.0)),
            )
            fingerprints.setdefault(key, []).append((y, x))
            total += 1
    # A genuine clone copies a connected region at ONE displacement, so its
    # verified pairs pile onto a single offset vector AND stay spatially
    # compact (two localized areas). Repetitive textures match too, but
    # their duplicates spread across the whole image — track tiles per
    # offset so both tests can apply.
    offset_tiles: dict[tuple[int, int], set[tuple[int, int]]] = {}
    for positions in fingerprints.values():
        if len(positions) < 2 or len(positions) > max(8, int(total * 0.05)):
            continue
        for i, (y1, x1) in enumerate(positions):
            for y2, x2 in positions[i + 1 :]:
                dy, dx = y2 - y1, x2 - x1
                if abs(dy) < min_offset and abs(dx) < min_offset:
                    continue
                tile_a = blurred[y1 : y1 + block, x1 : x1 + block]
                tile_b = blurred[y2 : y2 + block, x2 : x2 + block]
                if float(np.abs(tile_a - tile_b).mean()) < mad_threshold:
                    bucket = offset_tiles.setdefault((dy, dx), set())
                    bucket.add((y1, x1))
                    bucket.add((y2, x2))
    # Score every offset, not just the most frequent: an image-wide texture
    # family can out-pair a real clone, so pick the strongest offset that
    # ALSO stays spatially compact (<60% of the frame — the texture
    # family's tiles spread everywhere and fail this test).
    top_pairs = 0
    top_coverage = 1.0
    for tiles in offset_tiles.values():
        pairs = len(tiles) // 2
        if pairs < 6 or pairs <= top_pairs:
            continue
        cell = 8
        cells: set[tuple[int, int]] = set()
        for ty, tx in tiles:
            for cy in range(ty // cell, (ty + block) // cell):
                for cx in range(tx // cell, (tx + block) // cell):
                    cells.add((cy, cx))
        coverage = len(cells) * cell * cell / float(height * width)
        if coverage < 0.6:
            top_pairs, top_coverage = pairs, coverage
    ratio = top_pairs / max(total, 1) if top_pairs >= 6 else 0.0
    detail = (
        f"copy-move 후보: 단일 오프셋에서 {top_pairs}쌍의 검증된 타일 중복 "
        f"(이미지의 {top_coverage:.0%} 영역에 국한)이 관측됩니다."
        if ratio > 0
        else "비국소 중복 타일이 관측되지 않았습니다."
    )
    return ratio, detail


def copy_move_keypoint_score(gray, *, min_matches: int = 4, min_inliers: int = 2) -> tuple[float, str]:
    """Rotation/scale-robust copy-move detection via keypoint matching.

    The block-matching detector only catches unrotated, unscaled clones.
    This keypoint path (SIFT → ORB fallback) matches each descriptor against
    the rest of the image, clusters matches by displacement to exclude
    self/neighbour matches, then verifies each cluster with a partial-affine
    RANSAC fit — a genuine rotated/scaled clone yields many matches under
    ONE geometric transform; incidental texture matches fail the fit.

    Returns (score 0-1, detail) where score is the inlier fraction of the
    best transform — the fraction of that cluster's matches consistent
    with a single geometric copy.
    """
    import cv2
    import numpy as np

    image = np.asarray(gray, dtype=np.float64)
    uint8 = np.clip(image, 0, 255).astype(np.uint8)
    detector = cv2.SIFT_create() if hasattr(cv2, "SIFT_create") else cv2.ORB_create(nfeatures=1500)
    keypoints, descriptors = detector.detectAndCompute(uint8, None)
    if descriptors is None or len(keypoints) < 12:
        return 0.0, "키포인트 부족 — copy-move 키포인트 분석을 건너뜁니다."

    norm = cv2.NORM_L2 if descriptors.dtype != np.uint8 else cv2.NORM_HAMMING
    matcher = cv2.BFMatcher(norm)
    knn = matcher.knnMatch(descriptors, descriptors, k=3)
    pts = np.float32([kp.pt for kp in keypoints])
    # Copy-move clones violate Lowe's ratio test (the second-best match is
    # the clone itself, distance ~0), so use an adaptive absolute threshold:
    # clone matches sit at near-zero descriptor distance while incidental
    # self-matches cluster around the median.
    candidates: list[tuple[float, int, int]] = []
    for group in knn:
        others = [m for m in group if m.queryIdx != m.trainIdx]
        if not others:
            continue
        m1 = others[0]
        if abs(m1.queryIdx - m1.trainIdx) < 3:
            continue  # neighbouring keypoints trivially match
        candidates.append((float(m1.distance), m1.queryIdx, m1.trainIdx))
    if not candidates:
        return 0.0, "교차 키포인트 매칭이 없습니다 — 복제 후보가 없습니다."
    median_dist = float(np.median([d for d, _, _ in candidates]))
    cap = max(1.0, 0.5 * median_dist)
    good = sorted({(q, t) for d, q, t in candidates if d <= cap})
    if len(good) < min_matches:
        return 0.0, f"교차 키포인트 매칭 {len(good)}개 — 복제 후보가 없습니다."

    # A rotated/scaled clone does NOT share one displacement — it shares
    # one similarity transform. Fit partial-affine RANSAC over all
    # non-local matches iteratively: each coherent clone yields a transform
    # with many inliers, incidental matches scatter and produce none.
    remaining = [
        (q, t) for q, t in good
        if abs(float(pts[t][0] - pts[q][0])) >= 16 or abs(float(pts[t][1] - pts[q][1])) >= 16
    ]
    # With few matches RANSAC can latch onto a degenerate transform, so
    # vote instead: every pair of matches proposes one similarity
    # transform. Mirror matches (clone A→B plus B→A) mix into spurious
    # hypotheses, so hypotheses are evaluated in vote order and each must
    # also survive photometric verification — the first that does wins.
    src_all = np.float32([pts[q] for q, _ in remaining])
    dst_all = np.float32([pts[t] for _, t in remaining])
    n = len(remaining)
    hypotheses: list[tuple[int, np.ndarray, np.ndarray]] = []
    for i in range(n):
        for j in range(i + 1, n):
            v1 = src_all[j] - src_all[i]
            v2 = dst_all[j] - dst_all[i]
            len1 = float(np.hypot(*v1))
            if len1 < 6:
                continue
            scale = float(np.hypot(*v2)) / len1
            if not 0.2 <= scale <= 5.0:
                continue
            cos_d = float(np.dot(v1, v2)) / (len1 * np.hypot(*v2))
            sin_d = float(v1[0] * v2[1] - v1[1] * v2[0]) / (len1 * np.hypot(*v2))
            a, b = scale * cos_d, scale * sin_d
            tx = dst_all[i][0] - (a * src_all[i][0] - b * src_all[i][1])
            ty = dst_all[i][1] - (b * src_all[i][0] + a * src_all[i][1])
            pred = np.stack(
                [a * src_all[:, 0] - b * src_all[:, 1] + tx,
                 b * src_all[:, 0] + a * src_all[:, 1] + ty], axis=1
            )
            inliers = np.hypot(*(pred - dst_all).T) <= 4.0
            count = int(inliers.sum())
            # Two consistent matches already define the transform exactly;
            # the photometric check below is what rejects accidents, so
            # keep every hypothesis with at least a pair of supporters.
            if count >= 2:
                hypotheses.append(
                    (count, np.array([[a, -b, tx], [b, a, ty]]), src_all[inliers])
                )
    best_count = 0
    hypotheses.sort(key=lambda h: -h[0])
    tested: list[np.ndarray] = []
    for count, matrix, src_inliers in hypotheses[:40]:
        if any(np.allclose(matrix, prev, atol=2.0) for prev in tested):
            continue
        tested.append(matrix)
        if len(tested) > 12:
            break
        spread = float(src_inliers.std(axis=0).max())
        if spread > 0.45 * max(uint8.shape):
            continue
        if _clone_patch_matches(uint8, matrix, src_inliers):
            best_count = count
            break
    if best_count < min_inliers:
        return 0.0, f"기하 검증을 통과한 매칭 클러스터가 없습니다(최다 {best_count}개 인라이어)."
    score = min(1.0, best_count / 40.0)
    detail = (
        f"copy-move(키포인트) 후보: {best_count}개 매칭이 단일 유사 변환에 "
        f"수렴합니다 — 회전/스케일 복제 후보."
    )
    return score, detail


def _clone_patch_matches(image, matrix, src_points, *, pad: int = 8) -> bool:
    """Photometric verification of a fitted clone transform.

    Warps the source keypoints' bounding region by the similarity transform
    and compares it against the destination pixels. A genuine copy-move
    clone reproduces the patch nearly exactly (mean absolute difference
    low); spurious geometric fits land on unrelated pixels.
    """
    import cv2
    import numpy as np

    x0, y0 = np.floor(src_points.min(axis=0)).astype(int) - pad
    x1, y1 = np.ceil(src_points.max(axis=0)).astype(int) + pad
    h, w = image.shape
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, w), min(y1, h)
    if x1 - x0 < 12 or y1 - y0 < 12:
        return False
    # Reject glyph-scale repeats (logos, characters, UI icons): a forensic
    # clone region should cover a meaningful fraction of the frame.
    if (x1 - x0) * (y1 - y0) < 0.005 * w * h:
        return False
    # matrix maps src points→dst points; warpAffine takes the same
    # forward src→dst transform for the image, placing the source patch
    # at its destination for pixel comparison.
    warped = cv2.warpAffine(image, matrix.astype(np.float64), (w, h))
    dst_pts = cv2.transform(src_points.reshape(-1, 1, 2), matrix).reshape(-1, 2)
    dx0, dy0 = np.floor(dst_pts.min(axis=0)).astype(int) - pad
    dx1, dy1 = np.ceil(dst_pts.max(axis=0)).astype(int) + pad
    dx0, dy0 = max(dx0, 0), max(dy0, 0)
    dx1, dy1 = min(dx1, w), min(dy1, h)
    if dx1 - dx0 < 12 or dy1 - dy0 < 12:
        return False
    region_warp = warped[dy0:dy1, dx0:dx1]
    region_real = image[dy0:dy1, dx0:dx1]
    mad = float(np.abs(region_warp.astype(float) - region_real.astype(float)).mean())
    # Rotated/scaled clones carry interpolation error, so the bound is
    # relative: a clone's error is far below the region's own contrast.
    return mad < max(12.0, 0.35 * float(region_real.std()))
