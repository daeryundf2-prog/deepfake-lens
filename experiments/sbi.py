"""Self-Blended Image (SBI) generation for training manipulation detectors.

Simplified local implementation of the SBI idea (Shiohara & Yamasaki,
"Detecting Deepfakes with Self-Blended Images", CVPR 2022,
https://github.com/mapooon/SelfBlendedImages — link verified 2026-08-30):
fakes are synthesized from REAL images only, by blending two differently
distorted copies of the same image with a random mask. A detector trained
this way learns blending/resampling artifacts instead of overfitting to one
generator, and needs no fake data at all.

Pure numpy; the distortion pool uses bilinear resampling, Gaussian blur,
color jitter, and a DCT-quantization JPEG simulation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deepfake_lens.frequency import dct_2d, idct_2d  # noqa: E402


JPEG_LUMA_TABLE = [
    [16, 11, 10, 16, 24, 40, 51, 61],
    [12, 12, 14, 19, 26, 58, 60, 55],
    [14, 13, 16, 24, 40, 57, 69, 56],
    [14, 17, 22, 29, 51, 87, 80, 62],
    [18, 22, 37, 56, 68, 109, 103, 77],
    [24, 35, 55, 64, 81, 104, 113, 92],
    [49, 64, 78, 87, 103, 121, 120, 101],
    [72, 92, 95, 98, 112, 100, 103, 99],
]


def resize_bilinear(image: np.ndarray, out_height: int, out_width: int) -> np.ndarray:
    """Pure-numpy bilinear resize of an HxW(xC) array."""
    image = np.asarray(image, dtype=np.float64)
    squeezed = image.ndim == 2
    plane = image[:, :, None] if squeezed else image
    height, width = plane.shape[:2]
    ys = np.linspace(0, height - 1, out_height)
    xs = np.linspace(0, width - 1, out_width)
    y0 = np.clip(np.floor(ys).astype(int), 0, height - 1)
    x0 = np.clip(np.floor(xs).astype(int), 0, width - 1)
    y1 = np.minimum(y0 + 1, height - 1)
    x1 = np.minimum(x0 + 1, width - 1)
    wy = (ys - y0)[:, None, None]
    wx = (xs - x0)[None, :, None]
    top = plane[y0][:, x0] * (1 - wx) + plane[y0][:, x1] * wx
    bottom = plane[y1][:, x0] * (1 - wx) + plane[y1][:, x1] * wx
    result = top * (1 - wy) + bottom * wy
    return result[:, :, 0] if squeezed else result


def gaussian_blur(image: np.ndarray, sigma: float) -> np.ndarray:
    """Separable Gaussian blur for HxW(xC) arrays."""
    if sigma <= 0:
        return image
    radius = max(1, int(sigma * 3))
    offsets = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-(offsets**2) / (2 * sigma * sigma))
    kernel /= kernel.sum()
    padded = np.apply_along_axis(
        lambda values: np.convolve(values, kernel, mode="same"), 0, image
    )
    return np.apply_along_axis(
        lambda values: np.convolve(values, kernel, mode="same"), 1, padded
    )


def jpeg_simulate(image: np.ndarray, quality: int, block: int = 8) -> np.ndarray:
    """Block-DCT quantization with the JPEG luma table at a quality level.

    This reproduces the frequency-domain effect of JPEG compression (the
    high-frequency coefficients are dropped) without a codec dependency.
    """
    height, width = image.shape[:2]
    channels = image.shape[2] if image.ndim == 3 else 1
    plane = image if image.ndim == 3 else image[:, :, None]
    scale = 5000.0 / quality if quality < 50 else 200.0 - 2.0 * quality
    quantizer = np.ceil(np.asarray(JPEG_LUMA_TABLE, dtype=np.float64) * scale / 100.0)
    quantizer[quantizer < 1] = 1

    blocks_y = height // block
    blocks_x = width // block
    cropped = plane[: blocks_y * block, : blocks_x * block]
    tiles = cropped.reshape(blocks_y, block, blocks_x, block, channels).transpose(0, 2, 1, 3, 4)
    tiles = tiles.reshape(-1, block, block, channels)
    for index in range(tiles.shape[0]):
        for channel in range(channels):
            coefficients = dct_2d(tiles[index, :, :, channel])
            quantized = np.round(coefficients / quantizer) * quantizer
            tiles[index, :, :, channel] = idct_2d(quantized)
    rebuilt = tiles.reshape(blocks_y, blocks_x, block, block, channels).transpose(0, 2, 1, 3, 4)
    result = rebuilt.reshape(blocks_y * block, blocks_x * block, channels)
    if image.ndim == 2:
        return np.clip(result[:, :, 0], 0, 255)
    return np.clip(result, 0, 255)


def random_blending_mask(rng: np.random.Generator, height: int, width: int) -> np.ndarray:
    """Random soft mask: 1-3 random ellipses on a [0, 1] plane."""
    yy, xx = np.mgrid[0:height, 0:width]
    mask = np.zeros((height, width), dtype=np.float64)
    for _ in range(int(rng.integers(1, 4))):
        center_y = rng.uniform(0, height)
        center_x = rng.uniform(0, width)
        radius_y = rng.uniform(height * 0.15, height * 0.6)
        radius_x = rng.uniform(width * 0.15, width * 0.6)
        distance = ((yy - center_y) / max(1e-6, radius_y)) ** 2 + ((xx - center_x) / max(1e-6, radius_x)) ** 2
        mask = np.maximum(mask, (distance <= 1.0).astype(np.float64))
    # Soften the boundary so blending edges carry a gradient, like SBI's
    # random-mask blending.
    return gaussian_blur(mask, max(1.0, min(height, width) * 0.02))


def polygon_blending_mask(rng: np.random.Generator, height: int, width: int) -> np.ndarray:
    """Convex-polygon mask — mimics the landmark-hull shape real face swaps use.

    Random points near the face region, convex-hulled and filled, then
    blurred with a sigma that is sometimes near-zero (real swaps can have
    hard boundaries after codec noise).
    """
    try:
        import cv2
    except ImportError:
        return random_blending_mask(rng, height, width)
    n = int(rng.integers(5, 9))
    cx, cy = rng.uniform(0.3, 0.7, size=2)
    angles = np.sort(rng.uniform(0, 2 * np.pi, size=n))
    radii = rng.uniform(0.18, 0.45, size=n)
    pts = np.stack(
        [cx * width + radii * width * np.cos(angles),
         cy * height + radii * height * np.sin(angles)], axis=1)
    hull = cv2.convexHull(pts.astype(np.float32)).astype(np.int32)
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillConvexPoly(mask, hull, 1)
    sigma = rng.uniform(0.5, min(height, width) * 0.03)
    return gaussian_blur(mask.astype(np.float64), sigma)


def _color_jitter(image: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    gains = 1.0 + rng.uniform(-0.12, 0.12, size=(3,))
    return np.clip(image * gains, 0, 255)


DISTORTIONS = ("resize", "blur", "jpeg", "color", "affine", "noise", "sharpen")


def _apply_distortion(name: str, image: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    height, width = image.shape[:2]
    if name == "resize":
        factor = rng.uniform(0.5, 0.85)
        small = resize_bilinear(image, max(8, int(height * factor)), max(8, int(width * factor)))
        return resize_bilinear(small, height, width)
    if name == "blur":
        return gaussian_blur(image, rng.uniform(1.0, 3.0))
    if name == "jpeg":
        return jpeg_simulate(image, int(rng.integers(30, 75)))
    if name == "color":
        return _color_jitter(image, rng)
    if name == "affine":
        # Small rotation/scale/translation mismatch — the signature of a
        # warped-in face patch. Uses cv2 when available, else falls back to
        # a resize jitter.
        try:
            import cv2
        except ImportError:
            return _apply_distortion("resize", image, rng)
        angle = rng.uniform(-12, 12)
        scale = rng.uniform(0.9, 1.1)
        tx = rng.uniform(-0.08, 0.08) * width
        ty = rng.uniform(-0.08, 0.08) * height
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, scale)
        matrix[:, 2] += [tx, ty]
        warp = cv2.warpAffine(
            np.asarray(image, dtype=np.float64), matrix, (width, height),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        return np.clip(warp, 0, 255)
    if name == "noise":
        sigma = rng.uniform(2.0, 8.0)
        return np.clip(image + rng.normal(0, sigma, image.shape), 0, 255)
    if name == "sharpen":
        blurred = gaussian_blur(image, rng.uniform(0.8, 1.6))
        amount = rng.uniform(0.4, 1.2)
        return np.clip(image + amount * (image - blurred), 0, 255)
    raise ValueError(f"unknown distortion: {name}")


def self_blended_image(image: np.ndarray, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Blend two differently distorted copies of a real image.

    Returns (blended, mask) where mask=1 marks the blended (fake) region.
    """
    base_image = np.asarray(image, dtype=np.float64)
    height, width = base_image.shape[:2]
    first = DISTORTIONS[int(rng.integers(0, len(DISTORTIONS)))]
    second = DISTORTIONS[int(rng.integers(0, len(DISTORTIONS)))]
    base = _apply_distortion(first, base_image, rng)
    patch = _apply_distortion(second, base_image, rng)
    # jpeg_simulate crops to 8x8 block multiples — restore base dims so the
    # blend broadcast always lines up.
    if base.shape[:2] != (height, width):
        base = resize_bilinear(base, height, width)
    if patch.shape[:2] != (height, width):
        patch = resize_bilinear(patch, height, width)
    mask = (polygon_blending_mask(rng, height, width)
            if rng.random() < 0.5
            else random_blending_mask(rng, height, width))
    blended = base * (1 - mask[..., None]) + patch * mask[..., None]
    return np.clip(blended, 0, 255), mask
