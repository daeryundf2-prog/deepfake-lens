"""Inpainting and partial manipulation detection module.

Detects image regions that have been modified using inpainting,
object removal, or partial editing techniques.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class InpaintEvidenceSignal:
    title: str
    detail: str
    weight: int


@dataclass(frozen=True)
class InpaintRegion:
    x: int
    y: int
    width: int
    height: int
    confidence: float
    reason: str


@dataclass(frozen=True)
class InpaintAnalysis:
    score: int
    band: str
    band_label: str
    verdict: str
    signals: list[InpaintEvidenceSignal]
    limitations: list[str]
    regions_detected: int

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def analyze_inpainting(
    path: Path | str,
) -> InpaintAnalysis:
    """Analyze an image for inpainting or partial manipulation signs."""
    image_path = Path(path)
    if not image_path.is_file():
        return _error_analysis(f"파일이 존재하지 않습니다: {image_path}")

    extension = image_path.suffix.lower()
    if extension not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}:
        return _error_analysis(f"지원하지 않는 이미지 형식입니다: {extension}")

    try:
        import cv2
        import numpy as np
    except ImportError:
        return _error_analysis("opencv가 설치되어 있지 않습니다. pip install opencv-python로 설치하세요.")

    try:
        image = cv2.imread(str(image_path))
        if image is None:
            return _error_analysis("이미지를 읽을 수 없습니다.")
    except Exception as exc:
        return _error_analysis(f"이미지 읽기 오류: {exc}")

    signals: list[InpaintEvidenceSignal] = []
    limitations: list[str] = []
    regions: list[InpaintRegion] = []

    # JPEG artifact inconsistency
    jpeg_signal = _jpeg_artifact_analysis(image)
    if jpeg_signal:
        signals.append(jpeg_signal)

    # Noise pattern analysis
    noise_signal = _noise_pattern_analysis(image)
    if noise_signal:
        signals.append(noise_signal)

    # Edge continuity
    edge_signal = _edge_continuity_analysis(image)
    if edge_signal:
        signals.append(edge_signal)

    # Color gradient continuity
    color_signal = _color_gradient_analysis(image)
    if color_signal:
        signals.append(color_signal)

    # Patch-based analysis
    patch_signal, patch_regions = _patch_analysis(image)
    if patch_signal:
        signals.append(patch_signal)
        regions.extend(patch_regions)

    # Limitations
    limitations.append("로컬 휴리스틱 기반 선별 결과이며, 확정적 판별이 아닙니다.")

    score = min(100, sum(signal.weight for signal in signals))

    if score >= 67:
        band = "high"
        band_label = "높음"
        verdict = "이미지에서 인페인팅/부분 조작 의심 신호가 강합니다."
    elif score >= 35:
        band = "medium"
        band_label = "주의"
        verdict = "이미지에서 몇 가지 의심 신호가 보여 추가 확인이 필요합니다."
    else:
        band = "low"
        band_label = "낮음"
        verdict = "이미지에서 뚜렷한 인페인팅 의심 신호는 적습니다."

    return InpaintAnalysis(
        score=score,
        band=band,
        band_label=band_label,
        verdict=verdict,
        signals=signals,
        limitations=limitations,
        regions_detected=len(regions),
    )


def _error_analysis(message: str) -> InpaintAnalysis:
    return InpaintAnalysis(
        score=0,
        band="unknown",
        band_label="판단 어려움",
        verdict=message,
        signals=[],
        limitations=[message],
        regions_detected=0,
    )


def _jpeg_artifact_analysis(image) -> InpaintEvidenceSignal | None:
    """Analyze JPEG compression artifacts for inconsistencies."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Divide into blocks and analyze DCT coefficients
    block_size = 8
    h, w = gray.shape
    h_blocks = h // block_size
    w_blocks = w // block_size

    if h_blocks < 2 or w_blocks < 2:
        return None

    # Calculate block-wise statistics
    block_means = []
    block_stds = []

    for i in range(h_blocks):
        for j in range(w_blocks):
            block = gray[i*block_size:(i+1)*block_size, j*block_size:(j+1)*block_size]
            block_means.append(float(np.mean(block)))
            block_stds.append(float(np.std(block)))

    if not block_means:
        return None

    # Analyze variance of block statistics
    mean_variance = np.var(block_means)
    std_variance = np.var(block_stds)

    # Unnaturally uniform blocks (possible inpainting)
    if mean_variance < 10 and std_variance < 5:
        return InpaintEvidenceSignal(
            "균일한 블록 패턴",
            f"8x8 블록 통계가 비정상적으로 균일합니다 (평균 분산: {mean_variance:.2f}).",
            20,
        )

    return None


def _noise_pattern_analysis(image) -> InpaintEvidenceSignal | None:
    """Analyze noise patterns for inconsistencies."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Estimate noise using Laplacian
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    noise_map = np.abs(laplacian)

    # Divide into regions and compare noise levels
    h, w = noise_map.shape
    region_size = max(32, min(h, w) // 4)

    noise_levels = []
    for y in range(0, h - region_size, region_size):
        for x in range(0, w - region_size, region_size):
            region = noise_map[y:y+region_size, x:x+region_size]
            noise_levels.append(float(np.mean(region)))

    if len(noise_levels) < 4:
        return None

    # Check for inconsistent noise levels
    mean_noise = np.mean(noise_levels)
    std_noise = np.std(noise_levels)

    if mean_noise > 0:
        cv_noise = std_noise / mean_noise
        if cv_noise > 0.5:
            return InpaintEvidenceSignal(
                "비일관적 노이즈 패턴",
                f"지역별 노이즈 수준 변이계수({cv_noise:.2f})가 높습니다.",
                18,
            )

    return None


def _edge_continuity_analysis(image) -> InpaintEvidenceSignal | None:
    """Analyze edge continuity for discontinuities."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)

    # Check for edge discontinuities using Hough transform
    lines = cv2.HoughLinesP(edges, 1, np.pi/180, threshold=50, minLineLength=30, maxLineGap=10)

    if lines is None or len(lines) < 5:
        return None

    # Analyze line orientations
    orientations = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        angle = math.atan2(y2 - y1, x2 - x1)
        orientations.append(angle)

    # Check for unusual orientation distribution
    if len(orientations) > 10:
        # Group orientations into bins
        bins = [0] * 8
        for angle in orientations:
            bin_idx = int((angle + math.pi) / (2 * math.pi) * 8) % 8
            bins[bin_idx] += 1

        # Check for concentrated orientations (possible artificial edges)
        max_bin = max(bins)
        total = sum(bins)
        if max_bin / total > 0.6:
            return InpaintEvidenceSignal(
                "비자연적 엣지 방향",
                f"엣지 방향이 한쪽으로 편중되어 있습니다 (최대 비율: {max_bin/total:.2f}).",
                15,
            )

    return None


def _color_gradient_analysis(image) -> InpaintEvidenceSignal | None:
    """Analyze color gradient continuity."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    # Convert to HSV for better color analysis
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    h_channel = hsv[:, :, 0].astype(float)

    # Calculate gradient magnitude
    grad_x = cv2.Sobel(h_channel, cv2.CV_64F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(h_channel, cv2.CV_64F, 0, 1, ksize=3)
    gradient_magnitude = np.sqrt(grad_x**2 + grad_y**2)

    # Analyze gradient distribution
    mean_grad = np.mean(gradient_magnitude)
    std_grad = np.std(gradient_magnitude)

    # Unnaturally smooth gradient (possible inpainting)
    if std_grad < 1.0 and mean_grad > 5:
        return InpaintEvidenceSignal(
            "매끄러운 색상 그라디언트",
            f"색상 그라디언트가 비정상적으로 매끄럽습니다 (표준편차: {std_grad:.2f}).",
            12,
        )

    return None


def _patch_analysis(image) -> tuple[InpaintEvidenceSignal | None, list[InpaintRegion]]:
    """Analyze image patches for inconsistencies."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None, []

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    # Divide into patches
    patch_size = max(32, min(h, w) // 8)
    regions = []

    for y in range(0, h - patch_size, patch_size // 2):
        for x in range(0, w - patch_size, patch_size // 2):
            patch = gray[y:y+patch_size, x:x+patch_size]

            # Calculate patch statistics
            patch_mean = float(np.mean(patch))
            patch_std = float(np.std(patch))

            # Check for patches with unusual statistics
            if patch_std < 5 and patch_mean > 100:
                regions.append(InpaintRegion(
                    x=x, y=y, width=patch_size, height=patch_size,
                    confidence=0.6,
                    reason="낮은 노이즈와 높은 밝기",
                ))

    if len(regions) > 5:
        return InpaintEvidenceSignal(
            "의심스러운 패치 영역",
            f"이미지에서 {len(regions)}개의 의심스러운 패치가 감지되었습니다.",
            15,
        ), regions

    return None, regions
