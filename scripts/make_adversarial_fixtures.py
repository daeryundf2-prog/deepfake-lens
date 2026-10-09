#!/usr/bin/env python3
"""Generate the adversarial non-photo image set (phase 0, WP-D: G3/G13/G17).

QA-ADV-1/QA-ADV-2 need images that are *not photographs* — the inputs on
which the old pixel heuristic produced "medium" for nothing (G3) and on
which no generation detector has been measured (G13). Every image is
synthesized here with numpy + Pillow from a fixed seed, so the output is
byte-for-byte deterministic for a given ``seed``.

Layout (``NN.png`` = 00..; 10 images per class, except the QA-ADV-2
screenshots: 17 chat + 17 web page + 16 document viewer = 50)::

    fixtures/adversarial/gradient/          linear / radial / diagonal gradients
    fixtures/adversarial/noise/             uniform white noise (RGB and gray)
    fixtures/adversarial/flat/              one solid colour per image
    fixtures/adversarial/checkerboard/      two-colour checkerboards, varied cells
    fixtures/adversarial/blurred_noise/     white noise under a Gaussian blur
    fixtures/adversarial/screenshot/        phone chat screenshots (1080x2400 / 1170x2532), 17
    fixtures/adversarial/screenshot_web/    desktop browser screenshots (monitor sizes), 17
    fixtures/adversarial/screenshot_viewer/ desktop PDF/HWP viewer screenshots, 16
    fixtures/adversarial/document_scan/     white page, grey text lines, scanner noise

The set is NOT committed (``fixtures/adversarial/`` is in .gitignore): tests
call :func:`generate_all` into a temporary directory.

Usage::

    python scripts/make_adversarial_fixtures.py               # write fixtures/adversarial/
    python scripts/make_adversarial_fixtures.py --out DIR --seed 0
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageFilter

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "fixtures" / "adversarial"
IMAGES_PER_CLASS = 10

# Pattern classes are QA-ADV-1; screenshots QA-ADV-2 (chat "screenshot",
# web page "screenshot_web", document viewer "screenshot_viewer"); document
# scans are an extra non-photo class the gate must also catch.
PATTERN_CLASSES = ("gradient", "noise", "flat", "checkerboard", "blurred_noise")
SCREENSHOT_CLASSES = ("screenshot", "screenshot_web", "screenshot_viewer")
DOCUMENT_CLASSES = ("document_scan",)
# New classes are appended so the per-image seed streams (keyed by the
# class's position here) of the older classes do not change.
ALL_CLASSES = PATTERN_CLASSES + ("screenshot",) + DOCUMENT_CLASSES + ("screenshot_web", "screenshot_viewer")
# QA-ADV-2: 50 screenshots = 17 chat + 17 web page + 16 document viewer.
CLASS_COUNTS = {"screenshot": 17, "screenshot_web": 17, "screenshot_viewer": 16}
# Desktop screens (w, h) for the web-page and viewer screenshots — all in
# image_class.SCREEN_RESOLUTIONS.
WEB_SCREEN_SIZES = ((1920, 1080), (1366, 768), (2560, 1440), (1440, 900), (1536, 864))
VIEWER_SCREEN_SIZES = ((1920, 1080), (1680, 1050), (2560, 1600), (1280, 800), (1920, 1200))

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


# Desktop palettes: browser tab strips, site accents, viewer title bars and
# canvases (light/dark themes of common browsers and PDF/HWP viewers).
_BROWSER_CHROME = ((222, 225, 230), (32, 33, 36), (240, 240, 244), (53, 54, 58), (205, 220, 245))
_SITE_ACCENTS = ((3, 199, 90), (26, 115, 232), (33, 37, 41), (255, 87, 34), (94, 53, 177))
_VIEWER_TITLE = ((32, 33, 36), (240, 240, 240), (45, 45, 48), (0, 120, 212))
_VIEWER_BACKGROUND = ((82, 86, 89), (230, 230, 230), (64, 64, 64), (200, 205, 210))

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


def _word_lines(
    canvas: np.ndarray, rng: np.random.Generator, x0: int, y0: int, x1: int, y1: int,
    color: tuple[int, int, int], *, line_h: int = 14, gap: int = 12,
) -> None:
    """Rendered-text stand-in: rows of word-sized solid blocks (screen text
    is anti-aliased glyphs on exact pixel rows; blocks keep its exact
    horizontal runs)."""
    y = y0
    while y + line_h <= y1:
        if rng.random() < 0.1:  # paragraph break
            y += line_h + gap
            continue
        x = x0
        end = x1 - (int(rng.integers(0, max(1, (x1 - x0) // 2))) if rng.random() < 0.25 else 0)
        while x < end:
            word = int(rng.integers(18, 110))
            canvas[y:y + line_h, x:min(x + word, end)] = color
            x += word + int(rng.integers(6, 14))
        y += line_h + gap


def _chrome_band(width: int, height: int) -> int:
    # Covers the classifier's status-bar band (2.5 % of the long side) with
    # a few pixels to spare — a browser tab strip / window title bar —
    # rounded up to the 16-px JPEG MCU so a re-encode keeps the band solid.
    return (int(round(max(width, height) * 0.025)) + 8 + 15) // 16 * 16


def web_screenshot(rng: np.random.Generator, index: int) -> Image.Image:
    """Desktop browser screenshot: tab strip, address bar, site header,
    article text, image blocks and sidebar cards (QA-ADV-2 "웹페이지")."""
    width, height = WEB_SCREEN_SIZES[index % len(WEB_SCREEN_SIZES)]
    canvas = np.full((height, width, 3), 255, dtype=np.uint8)
    band = _chrome_band(width, height)
    canvas[:band] = _BROWSER_CHROME[index % len(_BROWSER_CHROME)]
    canvas[band:band + 52] = (241, 243, 244)  # toolbar
    canvas[band:band + 4, 12:252] = (255, 255, 255)  # active tab's lower edge
    canvas[band + 10:band + 42, 140:width - 220] = (255, 255, 255)  # address field
    _word_lines(canvas, rng, 170, band + 20, min(width - 260, 900), band + 34, (60, 64, 67), line_h=12)
    canvas[band + 52:band + 53] = (218, 220, 224)
    top = band + 53
    accent = _SITE_ACCENTS[index % len(_SITE_ACCENTS)]
    canvas[top:top + 72] = accent  # site header
    for slot in range(5):
        x = 240 + slot * 150
        if x + 110 < width:
            canvas[top + 30:top + 44, x:x + int(rng.integers(60, 110))] = (255, 255, 255)
    content_top = top + 72 + 32
    sidebar_x = int(width * 0.7)
    left = int(width * 0.08)
    # Article: title, hero image, paragraphs.
    canvas[content_top:content_top + 30, left:left + int(rng.integers(300, 600))] = (32, 33, 36)
    hero_top = content_top + 56
    hero_h = int(height * 0.25)
    hero = rng.uniform(60, 200, size=3)
    canvas[hero_top:hero_top + hero_h, left:sidebar_x - 40] = hero.astype(np.uint8)
    _word_lines(canvas, rng, left, hero_top + hero_h + 30, sidebar_x - 40, height - 40, (32, 33, 36))
    # Sidebar cards with hairline borders.
    y = content_top
    while y + 160 < height - 30:
        card_h = int(rng.integers(120, 220))
        if y + card_h > height - 30:
            break
        canvas[y:y + card_h, sidebar_x:width - 40] = (248, 249, 250)
        canvas[y, sidebar_x:width - 40] = (218, 220, 224)
        canvas[y + card_h - 1, sidebar_x:width - 40] = (218, 220, 224)
        canvas[y:y + card_h, sidebar_x] = (218, 220, 224)
        canvas[y:y + card_h, width - 41] = (218, 220, 224)
        _word_lines(canvas, rng, sidebar_x + 16, y + 16, width - 56, y + card_h - 12, (95, 99, 104), line_h=11, gap=9)
        y += card_h + 24
    return Image.fromarray(canvas, "RGB")


def viewer_screenshot(rng: np.random.Generator, index: int) -> Image.Image:
    """Desktop document-viewer screenshot (PDF/HWP viewer): title bar,
    toolbar, thumbnail pane, a white page of text on a grey canvas,
    scrollbar (QA-ADV-2 "문서 뷰어")."""
    width, height = VIEWER_SCREEN_SIZES[index % len(VIEWER_SCREEN_SIZES)]
    canvas = np.full((height, width, 3), _VIEWER_BACKGROUND[index % len(_VIEWER_BACKGROUND)], dtype=np.uint8)
    band = _chrome_band(width, height)
    canvas[:band] = _VIEWER_TITLE[index % len(_VIEWER_TITLE)]
    canvas[band:band + 48] = (50, 54, 57)  # toolbar
    for slot in range(int(rng.integers(8, 14))):
        x = 16 + slot * 40
        canvas[band + 12:band + 36, x:x + 24] = (138, 144, 150)  # toolbar icons
    pane_w = 220
    canvas[band + 48:, :pane_w] = (60, 64, 67)  # thumbnail pane
    y = band + 72
    while y + 200 < height:
        canvas[y:y + 180, 40:180] = (255, 255, 255)
        _word_lines(canvas, rng, 52, y + 14, 168, y + 170, (150, 150, 150), line_h=4, gap=5)
        y += 210
    page_w = int(min(width - pane_w - 120, height * 0.75))
    page_x = pane_w + (width - pane_w - page_w) // 2
    page_top = band + 48 + 28
    canvas[page_top:, page_x:page_x + page_w] = (255, 255, 255)
    canvas[page_top:, page_x + page_w:page_x + page_w + 4] = (40, 42, 44)  # page shadow
    margin = int(page_w * 0.11)
    _word_lines(canvas, rng, page_x + margin, page_top + margin, page_x + page_w - margin, height - 20, (34, 34, 34), line_h=13, gap=11)
    canvas[band + 48:, width - 14:] = (70, 74, 77)  # scrollbar track
    thumb = int(rng.integers(band + 60, max(band + 61, height - 260)))
    canvas[thumb:thumb + 200, width - 12:width - 2] = (154, 160, 166)
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
    "screenshot_web": web_screenshot,
    "screenshot_viewer": viewer_screenshot,
}


_STORED_CLASSES = frozenset({"noise", "blurred_noise", "document_scan"})


def generate_class(cls: str, out_dir: Path | str, seed: int = 0, count: int | None = None) -> list[Path]:
    target = Path(out_dir) / cls
    target.mkdir(parents=True, exist_ok=True)
    paths = []
    for index in range(CLASS_COUNTS.get(cls, IMAGES_PER_CLASS) if count is None else count):
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
    _repo = Path(__file__).resolve().parents[1]
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help
    parser = KoreanArgumentParser(description="사진이 아닌 적대적 이미지 세트(그라데이션·노이즈·단색·체커보드·블러·스크린샷·문서 스캔)를 만듭니다(0단계 WP-D: G3/G13/G17).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"출력 폴더(기본: {DEFAULT_OUT})")
    parser.add_argument("--seed", type=int, default=0, help="난수 시드(기본: 0)")
    args = parser.parse_args(argv)
    written = generate_all(args.out, seed=args.seed)
    total = sum(len(paths) for paths in written.values())
    print(f"wrote {total} images under {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
