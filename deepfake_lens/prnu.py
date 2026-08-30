"""PRNU sensor-fingerprint screening (Photo Response Non-Uniformity).

Every imaging sensor impresses a fixed multiplicative noise pattern on its
output. The standard workflow (Chen et al., "Determining the origin of
images using a sensor PRNU", 2008): denoise an image, keep the residual,
average residuals from several images of the same device into a reference
fingerprint, then correlate a query residual against that fingerprint.

This module implements the primitives — residual extraction, fingerprint
accumulation, and zero-mean normalized cross-correlation (NCC). It is a
provenance measurement, not a fake detector: a natural camera image
correlates with its own device; synthetic or heavily processed images will
not correlate with any given reference set.

Simplified local implementation (no wavelet denoising; a Gaussian residual
keeps the dominant PRNU component).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

MIN_REFERENCE_IMAGES = 3
MIN_CORRELATION = 0.10  # empirical screening floor for same-device NCC


@dataclass(frozen=True)
class PrnuEvidenceSignal:
    title: str
    detail: str
    weight: int


@dataclass(frozen=True)
class PrnuAnalysis:
    score: int
    band: str
    band_label: str
    verdict: str
    signals: list[PrnuEvidenceSignal]
    limitations: list[str]
    correlation: float | None
    reference_images: int
    method: str = "prnu-ncc-v1"

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def prnu_residual(gray_2d):
    """Normalized noise residual of a grayscale image (numpy array in/out).

    The convolver's zero-padded borders leave a systematic boundary band in
    the residual that is identical for every image; it is cropped so the NCC
    reflects the sensor pattern instead of the filter's edge behavior.
    """
    import numpy as np

    image = np.asarray(gray_2d, dtype=np.float64)
    if image.ndim != 2 or min(image.shape) < 40:
        raise ValueError("prnu residual needs a 2-D array of at least 40x40")
    sigma = 1.0
    border = 2 * max(1, int(sigma * 3))
    denoised = _gaussian_denoise(image, sigma=sigma)
    residual = (image - denoised)[border:-border, border:-border]
    # PRNU is multiplicative: normalize by the local intensity so dark and
    # bright regions contribute comparably.
    return residual / np.maximum(np.abs(image[border:-border, border:-border]), 1.0)


def _gaussian_denoise(image, *, sigma: float):
    """Separable Gaussian smoothing used as the denoiser F in W = I - F(I)."""
    import numpy as np

    radius = max(1, int(sigma * 3))
    offsets = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-(offsets**2) / (2 * sigma * sigma))
    kernel /= kernel.sum()
    padded_rows = np.apply_along_axis(
        lambda values: np.convolve(values, kernel, mode="same"), 0, image
    )
    return np.apply_along_axis(
        lambda values: np.convolve(values, kernel, mode="same"), 1, padded_rows
    )


def camera_fingerprint(residuals: list) -> "object":
    """Average normalized residuals into a device fingerprint."""
    import numpy as np

    if len(residuals) < MIN_REFERENCE_IMAGES:
        raise ValueError(f"need at least {MIN_REFERENCE_IMAGES} reference residuals")
    return np.mean([np.asarray(residual, dtype=np.float64) for residual in residuals], axis=0)


def normalized_cross_correlation(residual, fingerprint) -> float:
    """Zero-mean NCC in [-1, 1] between a residual and a fingerprint."""
    import numpy as np

    left = np.asarray(residual, dtype=np.float64).ravel()
    right = np.asarray(fingerprint, dtype=np.float64).ravel()
    if left.shape != right.shape or left.size == 0:
        raise ValueError("residual and fingerprint shapes must match and be non-empty")
    left -= left.mean()
    right -= right.mean()
    denominator = np.sqrt((left * left).sum() * (right * right).sum())
    if denominator <= 1e-12:
        return 0.0
    return float((left * right).sum() / denominator)


def analyze_prnu(target_path: Path | str, reference_paths: list[Path | str]) -> PrnuAnalysis:
    """Correlate a target image's residual against a reference fingerprint."""
    target = Path(target_path)
    if not target.is_file():
        return _error_analysis(f"대상 파일이 존재하지 않습니다: {target}")
    references = [Path(path) for path in reference_paths]
    if len(references) < MIN_REFERENCE_IMAGES:
        return _error_analysis(
            f"참조 이미지가 부족합니다 ({len(references)}개, 최소 {MIN_REFERENCE_IMAGES}개). PRNU 지문은 여러 장의 평균이 필요합니다."
        )
    for path in [*references, target]:
        if not path.is_file():
            return _error_analysis(f"파일이 존재하지 않습니다: {path}")

    try:
        import numpy as np
    except ImportError:
        return _error_analysis("numpy가 설치되어 있지 않습니다. pip install numpy로 설치하세요.")

    def residual_for(path: Path):
        image = _load_grayscale(path)
        return prnu_residual(image)

    reference_residuals = []
    for path in references:
        try:
            reference_residuals.append(residual_for(path))
        except Exception as exc:
            return _error_analysis(f"참조 이미지를 읽을 수 없습니다 ({path}): {exc}")

    first_shape = reference_residuals[0].shape
    if any(residual.shape != first_shape for residual in reference_residuals):
        return _error_analysis("참조 이미지들의 해상도가 서로 달라 지문을 누적할 수 없습니다.")

    try:
        target_residual = residual_for(target)
    except Exception as exc:
        return _error_analysis(f"대상 이미지를 읽을 수 없습니다: {exc}")
    if target_residual.shape != first_shape:
        return _error_analysis("대상 이미지와 참조 이미지의 해상도가 다릅니다.")

    fingerprint = camera_fingerprint(reference_residuals)
    correlation = normalized_cross_correlation(target_residual, fingerprint)

    signals: list[PrnuEvidenceSignal] = []
    score = 0
    limitations = [
        "PRNU 상관은 출처 일치 참고 측정값이며, 재압축/리사이즈/렌더링된 이미지에서는 신뢰도가 떨어집니다.",
        "참조 이미지는 동일 장치의 원본 사진 여러 장이어야 합니다.",
    ]
    if correlation >= MIN_CORRELATION:
        verdict = f"대상 이미지가 참조 지문과 상관됩니다 (NCC {correlation:.3f}). 동일 장치 출처 추정입니다."
    else:
        score = 25
        signals.append(
            PrnuEvidenceSignal(
                "센서 지문 불일치",
                f"대상 잔차가 참조 지문과 상관되지 않습니다 (NCC {correlation:.3f}).",
                25,
            )
        )
        verdict = "대상 이미지가 참조 지문과 상관되지 않습니다. 다른 장치/렌더링 출처 가능성을 확인하세요."

    band = "medium" if score >= 25 else "low"
    band_label = "주의" if band == "medium" else "낮음"
    return PrnuAnalysis(
        score=score,
        band=band,
        band_label=band_label,
        verdict=verdict,
        signals=signals,
        limitations=limitations,
        correlation=correlation,
        reference_images=len(references),
    )


def _error_analysis(message: str) -> PrnuAnalysis:
    return PrnuAnalysis(
        score=0,
        band="unknown",
        band_label="판단 어려움",
        verdict=message,
        signals=[],
        limitations=[message],
        correlation=None,
        reference_images=0,
    )


def _load_grayscale(path: Path):
    """Load an image as a grayscale numpy array (PNG native, Pillow optional)."""
    import numpy as np

    data = path.read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        from .pixel import _load_png_raster

        raster, limitation = _load_png_raster(data, max_side=4096)
        if raster is None:
            raise ValueError(limitation or "PNG을 해석할 수 없습니다.")
        rgb = np.asarray(raster.pixels, dtype=np.float64).reshape(raster.height, raster.width, 3)
        return 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
    try:
        from PIL import Image

        with Image.open(path) as image:
            return np.asarray(image.convert("L"), dtype=np.float64)
    except ImportError:
        raise ValueError("PNG가 아닌 이미지는 Pillow가 필요합니다.")
