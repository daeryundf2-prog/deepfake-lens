#!/usr/bin/env python3
"""Generate the adversarial non-photo image set (phase 0, WP-D: G3/G13/G17).

QA-ADV-1/QA-ADV-2 need images that are *not photographs* — the inputs on
which the old pixel heuristic produced "medium" for nothing (G3) and on
which no generation detector has been measured (G13). Every image is
synthesized here with numpy + Pillow from a fixed seed, so the output is
byte-for-byte deterministic for a given ``seed``.

Layout (10 images per class, ``NN.png`` = 00..09)::

    fixtures/adversarial/gradient/        linear / radial / diagonal gradients
    fixtures/adversarial/noise/           uniform white noise (RGB and gray)
    fixtures/adversarial/flat/            one solid colour per image
    fixtures/adversarial/checkerboard/    two-colour checkerboards, varied cells
    fixtures/adversarial/blurred_noise/   white noise under a Gaussian blur
    fixtures/adversarial/screenshot/      phone chat screenshots (1080x2400 / 1170x2532)
    fixtures/adversarial/document_scan/   white page, grey text lines, scanner noise

The set is NOT committed (``fixtures/adversarial/`` is in .gitignore): tests
call :func:`generate_all` into a temporary directory.

Usage::

    python scripts/make_adversarial_fixtures.py               # write fixtures/adversarial/
    python scripts/make_adversarial_fixtures.py --out DIR --seed 0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageFilter

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "fixtures" / "adversarial"
IMAGES_PER_CLASS = 10

# Pattern classes are QA-ADV-1; screenshots QA-ADV-2; document scans are an
# extra non-photo class the gate must also catch.
PATTERN_CLASSES = ("gradient", "noise", "flat", "checkerboard", "blurred_noise")
SCREENSHOT_CLASSES = ("screenshot",)
DOCUMENT_CLASSES = ("document_scan",)
ALL_CLASSES = PATTERN_CLASSES + SCREENSHOT_CLASSES + DOCUMENT_CLASSES

# Sizes the pattern classes cycle through: square generator sizes and common
# camera aspect ratios, all above the 128 px "too_small" floor.
PATTERN_SIZES = ((512, 512), (768, 512), (640, 480), (1024, 768), (600, 800))
# Phone screens the synthetic screenshots use (portrait, px).
SCREENSHOT_SIZES = ((1080, 2400), (1170, 2532))
SCREENSHOT_STATUS_BAR_PX = 100
# A4 at 150 dpi — a typical office-scanner output size (not a screen size).
DOCUMENT_SIZE = (1240, 1754)


def _rng(seed: int, cls: str, index: int) -> np.random.Generator:
    # Stable per-image stream: independent of generation order.
    return np.random.default_rng([seed, ALL_CLASSES.index(cls), index])


def _size(index: int) -> tuple[int, int]:
    return PATTERN_SIZES[index % len(PATTERN_SIZES)]


def gradient(rng: np.random.Generator, index: int) -> Image.Image:
    width, height = _size(index)
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float64)
    style = index % 3
    if style == 0:
        t = xs / (width - 1)
    elif style == 1:
        t = (xs + ys) / (width + height - 2)
    else:
        cy, cx = rng.uniform(0.2, 0.8) * height, rng.uniform(0.2, 0.8) * width
        t = np.hypot(xs - cx, ys - cy)
        t = t / t.max()
    start = rng.uniform(0, 255, size=3)
    end = rng.uniform(0, 255, size=3)
    rgb = start[None, None, :] * (1 - t[..., None]) + end[None, None, :] * t[..., None]
    return Image.fromarray(np.clip(np.round(rgb), 0, 255).astype(np.uint8), "RGB")


def noise(rng: np.random.Generator, index: int) -> Image.Image:
    width, height = _size(index)
    if index % 2:
        gray = rng.integers(0, 256, size=(height, width), dtype=np.uint8)
        return Image.fromarray(np.repeat(gray[..., None], 3, axis=2), "RGB")
    return Image.fromarray(rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8), "RGB")


def flat(rng: np.random.Generator, index: int) -> Image.Image:
    width, height = _size(index)
    color = tuple(int(c) for c in rng.integers(0, 256, size=3))
    return Image.new("RGB", (width, height), color)


def checkerboard(rng: np.random.Generator, index: int) -> Image.Image:
    width, height = _size(index)
    cell = (1, 2, 4, 8, 16, 32, 3, 12, 24, 64)[index]
    ys, xs = np.mgrid[0:height, 0:width]
    mask = ((xs // cell) + (ys // cell)) % 2 == 0
    a = rng.integers(0, 256, size=3, dtype=np.uint8)
    b = rng.integers(0, 256, size=3, dtype=np.uint8)
    rgb = np.where(mask[..., None], a[None, None, :], b[None, None, :]).astype(np.uint8)
    return Image.fromarray(rgb, "RGB")


def blurred_noise(rng: np.random.Generator, index: int) -> Image.Image:
    width, height = _size(index)
    radius = (1.5, 2.0, 3.0, 4.0, 6.0)[index % 5]
    base = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
    if index >= 5:
        base = np.repeat(base[..., :1], 3, axis=2)
    return Image.fromarray(base, "RGB").filter(ImageFilter.GaussianBlur(radius))


# Chat-app palette: status-bar colours, bubble fills and the page background.
_STATUS_COLORS = ((255, 255, 255), (186, 206, 224), (0, 0, 0), (245, 245, 245), (52, 120, 246))
_BUBBLE_MINE = ((254, 229, 0), (52, 120, 246), (220, 248, 198))
_BUBBLE_OTHER = ((255, 255, 255), (233, 233, 235), (240, 240, 240))


def _text_strokes(canvas: np.ndarray, rng: np.random.Generator, x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]) -> None:
    """Black glyph-like strokes: short bars on text lines inside a box."""
    line_h = 34
    y = y0 + 18
    while y + line_h <= y1 - 12:
        x = x0 + 22
        line_end = x1 - 22 - int(rng.integers(0, max(1, (x1 - x0) // 3)))
        while x < line_end:
            glyph_w = int(rng.integers(6, 22))
            if x + glyph_w > line_end:
                break
            stroke = int(rng.integers(2, 5))
            top = y + int(rng.integers(0, 8))
            canvas[top:y + 24, x:x + stroke] = color          # vertical stem
            canvas[y + 22:y + 24 + stroke, x:x + glyph_w] = color  # baseline bar
            if rng.random() < 0.5:
                canvas[y + 8:y + 8 + stroke, x:x + glyph_w] = color
            x += glyph_w + int(rng.integers(3, 16))
        y += line_h + 8


def screenshot(rng: np.random.Generator, index: int) -> Image.Image:
    width, height = SCREENSHOT_SIZES[index % len(SCREENSHOT_SIZES)]
    canvas = np.full((height, width, 3), 255, dtype=np.uint8)  # white page
    canvas[:SCREENSHOT_STATUS_BAR_PX] = _STATUS_COLORS[index % len(_STATUS_COLORS)]
    # Header bar under the status bar, with a hairline separator.
    canvas[SCREENSHOT_STATUS_BAR_PX:SCREENSHOT_STATUS_BAR_PX + 120] = (248, 248, 248)
    canvas[SCREENSHOT_STATUS_BAR_PX + 120:SCREENSHOT_STATUS_BAR_PX + 122] = (200, 200, 200)
    _text_strokes(canvas, rng, 140, SCREENSHOT_STATUS_BAR_PX + 30, width // 2, SCREENSHOT_STATUS_BAR_PX + 100, (0, 0, 0))
    y = SCREENSHOT_STATUS_BAR_PX + 160
    bottom = height - 180
    while y < bottom - 120:
        mine = bool(rng.random() < 0.5)
        bubble_w = int(rng.integers(width // 4, int(width * 0.7)))
        bubble_h = int(rng.integers(90, 320))
        if y + bubble_h > bottom:
            break
        x0 = width - 40 - bubble_w if mine else 160
        fill = (_BUBBLE_MINE if mine else _BUBBLE_OTHER)[index % 3]
        if not mine:
            canvas[y:y + 100, 40:140] = (160, 170, 190)  # square avatar
        canvas[y:y + bubble_h, x0:x0 + bubble_w] = fill
        text_color = (255, 255, 255) if fill == (52, 120, 246) else (0, 0, 0)
        _text_strokes(canvas, rng, x0, y, x0 + bubble_w, y + bubble_h, text_color)
        y += bubble_h + int(rng.integers(30, 90))
    # Input bar at the bottom.
    canvas[height - 160:] = (250, 250, 250)
    canvas[height - 160:height - 158] = (210, 210, 210)
    canvas[height - 130:height - 50, 40:width - 160] = (255, 255, 255)
    return Image.fromarray(canvas, "RGB")


def document_scan(rng: np.random.Generator, index: int) -> Image.Image:
    width, height = DOCUMENT_SIZE
    page = np.full((height, width), 246.0)
    margin = int(rng.integers(110, 170))
    line_h = int(rng.integers(18, 26))
    gap = int(rng.integers(14, 24))
    y = margin
    while y + line_h < height - margin:
        if rng.random() < 0.12:  # paragraph break
            y += line_h + gap
            continue
        x = margin
        end = width - margin - (int(rng.integers(0, width // 2)) if rng.random() < 0.2 else 0)
        while x < end:
            word = int(rng.integers(20, 90))
            page[y:y + line_h, x:min(x + word, end)] = rng.uniform(70, 120)
            x += word + int(rng.integers(8, 16))
        y += line_h + gap
    page += rng.normal(0.0, 3.0, size=page.shape)  # scanner sensor noise
    image = Image.fromarray(np.clip(np.round(page), 0, 255).astype(np.uint8), "L")
    return image.filter(ImageFilter.GaussianBlur(0.6)).convert("RGB")


def photo_like(seed: int = 0, size: tuple[int, int] = (768, 576), quality: int = 90) -> bytes:
    """Positive control for the photo gate: a synthetic "natural" image.

    Natural photographs have a power spectrum falling roughly as 1/f^2
    (van der Schaaf & van Hateren 1996), occluding objects with soft
    (optically blurred) edges, sensor noise, and JPEG compression. This
    builds exactly that: 1/f-shaped colour field + a few soft-edged
    ellipses + Gaussian sensor noise (sigma 2 DN) + JPEG at ``quality``.
    Returns the JPEG bytes. Not part of the adversarial set; tests use it
    to prove the non-photo gate does not swallow photo-like content.
    """
    import io

    width, height = size
    rng = np.random.default_rng([seed, 99])
    fy = np.fft.fftfreq(height)[:, None]
    fx = np.fft.rfftfreq(width)[None, :]
    radius = np.hypot(fx, fy)
    radius[0, 0] = 1.0
    channels = []
    base = rng.normal(size=(height, width // 2 + 1)) + 1j * rng.normal(size=(height, width // 2 + 1))
    for tint in (1.0, 0.9, 0.75):
        jitter = rng.normal(size=base.shape) * 0.25
        spectrum = (base + jitter) / radius  # amplitude 1/f -> power 1/f^2
        spectrum[0, 0] = 0
        field = np.fft.irfft2(spectrum, s=(height, width))
        field = (field - field.mean()) / (field.std() + 1e-9)
        channels.append(128 + 38 * field * tint)
    rgb = np.stack(channels, axis=2)
    ys, xs = np.mgrid[0:height, 0:width]
    for _ in range(6):
        cy, cx = rng.uniform(0, height), rng.uniform(0, width)
        ry, rx = rng.uniform(height * 0.08, height * 0.3), rng.uniform(width * 0.08, width * 0.3)
        dist = np.hypot((ys - cy) / ry, (xs - cx) / rx)
        alpha = np.clip((1.0 - dist) * 25.0, 0.0, 1.0)[..., None]  # ~2-4 px soft edge
        color = rng.uniform(40, 220, size=3)
        shade = 1.0 + 0.15 * (ys - cy)[..., None] / ry  # simple lighting falloff
        rgb = rgb * (1 - alpha) + color[None, None, :] * shade * alpha
    rgb += rng.normal(0.0, 2.0, size=rgb.shape)  # sensor noise
    image = Image.fromarray(np.clip(np.round(rgb), 0, 255).astype(np.uint8), "RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


GENERATORS: dict[str, Callable[[np.random.Generator, int], Image.Image]] = {
    "gradient": gradient,
    "noise": noise,
    "flat": flat,
    "checkerboard": checkerboard,
    "blurred_noise": blurred_noise,
    "screenshot": screenshot,
    "document_scan": document_scan,
}


_STORED_CLASSES = frozenset({"noise", "blurred_noise", "document_scan"})


def generate_class(cls: str, out_dir: Path | str, seed: int = 0, count: int = IMAGES_PER_CLASS) -> list[Path]:
    target = Path(out_dir) / cls
    target.mkdir(parents=True, exist_ok=True)
    paths = []
    for index in range(count):
        path = target / f"{index:02d}.png"
        # Lossless and byte-deterministic either way; noise barely compresses,
        # so those classes are stored (level 0) to keep generation fast.
        level = 0 if cls in _STORED_CLASSES else 1
        GENERATORS[cls](_rng(seed, cls, index), index).save(path, format="PNG", compress_level=level)
        paths.append(path)
    return paths


def generate_all(out_dir: Path | str, seed: int = 0, classes: tuple[str, ...] = ALL_CLASSES) -> dict[str, list[Path]]:
    """Write every class into ``out_dir/<class>/NN.png``; return the paths."""
    return {cls: generate_class(cls, out_dir, seed) for cls in classes}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output directory (default: {DEFAULT_OUT})")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    written = generate_all(args.out, seed=args.seed)
    total = sum(len(paths) for paths in written.values())
    print(f"wrote {total} images under {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
