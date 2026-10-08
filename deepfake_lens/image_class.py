"""Photo / non-photo gate (phase 0, WP-D: G13).

Generation and manipulation detectors in this tool are trained and (to the
extent they are) measured on *photographs*. On noise, gradients, flat
colour, screenshots, UI graphics or document scans their output has no
measured meaning, and the old pixel heuristic produced a "medium" band on
exactly those inputs (G3). :func:`classify_image` sorts an image into one
of six kinds with fixed, deterministic rules so the scan pipeline can skip
the detectors — with the reason "사진 아님: <kind>" — instead of reporting
a number that means nothing.

Kinds (evaluated in this order; the first rule that matches wins):

1. ``too_small``      long side < :data:`TOO_SMALL_LONG_SIDE_PX`.
2. ``screenshot``     dimensions in :data:`SCREEN_RESOLUTIONS` *and* a solid
                      status-bar band at the top *and* many exact
                      axis-aligned step edges.
3. ``document_scan``  bimodal luminance (paper + ink), > 60 % near-white,
                      and text-line structure in the row projection profile.
4. ``pattern``        < 64 unique colours, or noise-residual variance
                      outside the photo range, or lag-1 row/column
                      autocorrelation > 0.999 (gradient) / < 0.05 (white
                      noise), or a stationary Gaussian field (blurred
                      noise: uniform local contrast + Gaussian residual).
5. ``graphic``        low unique-colour ratio and > 40 % flat-colour area.
6. ``photo``          everything else.

Screenshot and document rules run before ``pattern`` because a synthetic
UI capture or a clean scan has few colours and would otherwise be called a
pattern; all three are "not a photo", the order only picks the most
informative name.

This is a *gate*, not a detector: it never produces evidence for or
against manipulation. Its result is recorded as a deterministic, neutral,
weak evidence item ("이미지 유형: …") and in ``coverage``. The thresholds
are first-principles values (cited per constant) checked against the
synthetic adversarial set (scripts/make_adversarial_fixtures.py) and a
photo-like positive control; they have not been measured on a real-photo
corpus yet (phase 1, G15) — a misclassified photo is a *skipped* detector,
never a conclusion.

Only numpy and Pillow are required (opencv is not used). Without them the
check is recorded as skipped "의존성 부재: numpy" and the detectors run as
before — the gate only *blocks* when it positively identifies a non-photo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Union

try:  # numpy/Pillow are optional extras; the gate is skipped without them.
    import numpy as np
except ImportError:  # pragma: no cover - exercised only in the no-extras CI job
    np = None  # type: ignore[assignment]

# A path, a binary file object, or an RGB / gray array.
ImageSource = Union[Path, str, IO[bytes], "np.ndarray"]

# --- kinds ------------------------------------------------------------------

PHOTO = "photo"
GRAPHIC = "graphic"
SCREENSHOT = "screenshot"
PATTERN = "pattern"
DOCUMENT_SCAN = "document_scan"
TOO_SMALL = "too_small"
IMAGE_KINDS = (PHOTO, GRAPHIC, SCREENSHOT, PATTERN, DOCUMENT_SCAN, TOO_SMALL)

KIND_LABELS = {
    PHOTO: "사진",
    GRAPHIC: "그래픽",
    SCREENSHOT: "스크린샷",
    PATTERN: "패턴(노이즈·그라데이션·단색)",
    DOCUMENT_SCAN: "문서 스캔",
    TOO_SMALL: "저해상도",
}

# --- size gate (single source of truth for "측정 범위 밖: 해상도") -------------

# Phase-0 spec floor (WP-B/WP-D). Every bundled image detector takes
# >=224 px input and the model_adapter note records chance-level AUROC on
# 32x32 thumbnails; below 128 px there is no measured operating range.
MEASURABLE_MIN_SIDE_PX = 128
# too_small uses the *long* side (spec WP-D): an image whose largest
# dimension is under the floor carries too little content to classify.
TOO_SMALL_LONG_SIDE_PX = MEASURABLE_MIN_SIDE_PX
RESOLUTION_SKIP_PREFIX = "측정 범위 밖: 해상도"
NON_PHOTO_SKIP_PREFIX = "사진 아님"

# --- analysis raster -----------------------------------------------------

# Statistics are computed on a box-reduced copy (JPEG: DCT-domain draft
# decode, then Image.reduce) whose long side is at most this many pixels:
# enough to keep sensor-noise texture (a 4x box average of a 4000 px photo
# still has visible grain) while bounding cost to ~1 MP. Screen-sized
# images are also kept at full resolution for the screenshot rule.
ANALYSIS_MAX_SIDE_PX = 1024
# Rec. 601 luma weights (ITU-R BT.601), the same as Pillow's "L" mode.
LUMA_WEIGHTS = (0.299, 0.587, 0.114)

# --- pattern rules ---------------------------------------------------------

# Spec WP-D. A photograph has tens of thousands of distinct colours even
# after JPEG; fewer than 64 means a palette image (flat fill, checkerboard,
# posterised gradient).
PATTERN_MAX_UNIQUE_COLORS = 64
# Variance (DN^2, 8-bit luma) of the residual after a 3x3 box mean. Floor:
# 8-bit quantisation alone gives 1/12 DN^2 ≈ 0.083 per pixel, and any
# camera adds read/shot noise of at least ~0.5 DN after processing, i.e.
# residual variance ≳ 0.25 — below that the image is noise-free synthesis
# (gradient, blurred field). Ceiling: residual of uniform white noise is
# 8/9 · 255²/12 ≈ 4800; a photograph's residual is dominated by edges and
# texture and stays far below ~1500 even for foliage at full resolution.
PHOTO_RESIDUAL_VAR_MIN = 0.25
PHOTO_RESIDUAL_VAR_MAX = 1500.0
# Spec WP-D. Lag-1 autocorrelation of natural images is typically 0.85-0.98
# (Kretzmer 1952; Simoncelli & Olshausen 2001). > 0.999 in *both*
# directions is a gradient; < 0.05 in both is white noise.
GRADIENT_MIN_AUTOCORR = 0.999
WHITE_NOISE_MAX_AUTOCORR = 0.05
# Stationary Gaussian field (noise blurred at any radius): photographs are
# strongly non-stationary — local contrast differs between sky, skin and
# foliage (local-contrast distributions of natural scenes are broad, Frazor
# & Geisler 2006) — and their band-pass coefficients are heavy-tailed
# (kurtosis well above the Gaussian 3; Field 1987, Simoncelli & Olshausen
# 2001). A field whose block-wise contrast barely varies (coefficient of
# variation over an 8x8 block grid < 0.2) AND whose 3x3 residual is
# Gaussian or lighter-tailed (kurtosis < 3.5) is a synthetic noise
# texture. Both conditions are required so a frame-filling natural texture
# (sand, foliage) with heavy-tailed edges stays a photo.
STATIONARY_MAX_CONTRAST_CV = 0.2
GAUSSIAN_FIELD_MAX_KURTOSIS = 3.5
LOCAL_CONTRAST_GRID = 8  # 64 blocks: >= 16x16 px each at the 128 px floor

# --- graphic rules ---------------------------------------------------------

# Spec WP-D: "고유 색 비율 낮고 대면적 단색 영역 > 40%". A ~1 MP photo has
# unique colours on 10-40 % of its pixels (sklearn's china/flower samples:
# 23-35 %; the synthetic photo control: >= 8 %). A flat-fill graphic sits
# well under 1 %, and JPEG ringing around its edges lifts that to a few
# percent — 5 % keeps JPEG'd graphics while staying under every photo seen.
GRAPHIC_MAX_UNIQUE_RATIO = 0.05
# A pixel is "flat" when it equals its right and lower neighbours exactly;
# sensor noise makes that rare in photos except in clipped highlights.
GRAPHIC_MIN_FLAT_FRACTION = 0.40

# --- document-scan rules ---------------------------------------------------

# Paper is near-white after scanner normalisation; ink is dark. 200/160 DN
# leave a valley in between for anti-aliased glyph edges.
DOC_NEAR_WHITE_LUMA = 200
DOC_INK_LUMA = 160
# Spec WP-D: > 60 % near-white.
DOC_MIN_WHITE_FRACTION = 0.60
# At least 2 % ink (a sparse page still has a few lines), and the mid-tone
# valley must hold less mass than the ink mode (bimodality).
DOC_MIN_INK_FRACTION = 0.02
# Text lines: rows whose ink fraction exceeds DOC_INK_ROW_FRACTION form
# lines; a page has >= DOC_MIN_TEXT_LINES such runs separated by blank
# rows covering >= DOC_MIN_BLANK_ROW_FRACTION of the height (horizontal
# projection profile, the classic OCR line-segmentation feature).
DOC_INK_ROW_FRACTION = 0.01
# Fewer than five lines cannot be told apart from a few dark shapes.
DOC_MIN_TEXT_LINES = 5
DOC_MIN_BLANK_ROW_FRACTION = 0.20
# Body text at 9-12 pt is 0.5-1.5 % of an A4 page height per line; 3 %
# admits headings and low-resolution scans but rejects blobs and shapes.
DOC_MAX_LINE_HEIGHT_FRACTION = 0.03

# --- screenshot rules ------------------------------------------------------

# Common screen sizes in px (phones: iPhone 8 → 15 Pro Max, Galaxy S/A/Note,
# Pixel; tablets: iPad; monitors/laptops: VESA/HD/QHD/4K/Retina). Matched
# in either orientation.
SCREEN_RESOLUTIONS = frozenset({
    # phones
    (640, 1136), (750, 1334), (828, 1792), (1080, 1920), (1125, 2436),
    (1170, 2532), (1179, 2556), (1242, 2208), (1242, 2688), (1284, 2778),
    (1290, 2796), (720, 1280), (720, 1520), (720, 1600), (1080, 2160),
    (1080, 2220), (1080, 2280), (1080, 2340), (1080, 2400), (1440, 2560),
    (1440, 2960), (1440, 3040), (1440, 3088), (1440, 3120), (1440, 3200),
    (1344, 2992), (1280, 2856),
    # tablets
    (1536, 2048), (1620, 2160), (1640, 2360), (1668, 2224), (1668, 2388),
    (2048, 2732), (1600, 2560), (1800, 2880),
    # monitors / laptops (landscape listed as (w, h); orientation-free match)
    (1024, 768), (1280, 720), (1280, 800), (1280, 1024), (1366, 768),
    (1440, 900), (1536, 864), (1600, 900), (1680, 1050), (1920, 1080),
    (1920, 1200), (2560, 1080), (2560, 1440), (2560, 1600), (2880, 1800),
    (3024, 1964), (3440, 1440), (3456, 2234), (3840, 2160),
})
# Status bar: Android 24 dp (66-100 px at xxhdpi) / iOS 44-59 pt (132-177
# px at 3x). The top band checked is this fraction of the long side (2.5 %
# = 60 px at 2400), inside every status bar listed above.
STATUS_BAR_FRACTION = 0.025
# Share of the band that must be one exact colour (icons and the clock
# cover well under 15 % of a status bar).
STATUS_BAR_MIN_SOLID_FRACTION = 0.85
# Exact step edges: a boundary between two adjacent pixels whose max
# channel difference is >= EDGE_STEP_MIN and which continues along the
# boundary for >= EDGE_RUN_MIN_PX pixels (a UI rectangle side). 12 DN is
# the contrast between white and the lightest UI greys (#F0F0F0 ≈ 15).
EDGE_STEP_MIN = 12
EDGE_FLAT_TOLERANCE = 3
EDGE_RUN_MIN_PX = 32
# Fraction of pixels on such runs (horizontal + vertical). A single 300 px
# wide bubble on a 1080x2400 screen contributes ~0.05 %; a chat page has
# dozens. Photos at screen size essentially never reach it (noise breaks
# 32-px exact runs).
SCREENSHOT_MIN_EDGE_FRACTION = 0.003

DIRECTION_NEUTRAL_NOTE = "이미지 유형 판별은 조작·생성 여부에 대한 근거가 아니며, 생성 탐지 검사를 적용할지 정하는 데만 쓰입니다."


@dataclass(frozen=True)
class ImageClass:
    kind: str
    reasons: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def is_photo(self) -> bool:
        return self.kind == PHOTO

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    def skip_reason(self) -> str:
        """Coverage reason for a detector skipped because of this class."""
        if self.kind == TOO_SMALL:
            width, height = self.stats.get("width", 0), self.stats.get("height", 0)
            return resolution_skip_reason(int(width), int(height))
        return f"{NON_PHOTO_SKIP_PREFIX}: {self.kind}"


def resolution_skip_reason(width: int, height: int) -> str:
    return f"{RESOLUTION_SKIP_PREFIX} {width}x{height} (최소 변 {MEASURABLE_MIN_SIDE_PX}px 미만)"


def resolution_out_of_range(dimensions: tuple[int, int] | None) -> str | None:
    """Skip reason when a detector's input is below the measurable size."""
    if not dimensions:
        return None
    width, height = dimensions
    if min(width, height) < MEASURABLE_MIN_SIDE_PX:
        return resolution_skip_reason(width, height)
    return None


# --- loading ---------------------------------------------------------------


def _load_rgb(source: ImageSource) -> tuple[np.ndarray, tuple[int, int], np.ndarray]:
    """Return (analysis RGB uint8, original (w, h), full-res RGB or empty).

    The full-resolution array is only materialised for screen-sized images
    (the screenshot rule needs exact pixel boundaries).
    """
    if isinstance(source, np.ndarray):
        array = source
        if array.ndim == 2:
            array = np.repeat(array[..., None], 3, axis=2)
        if array.ndim != 3 or array.shape[2] < 3:
            raise ValueError(f"unsupported array shape {array.shape}")
        array = array[..., :3]
        if array.dtype != np.uint8:
            array = np.clip(np.round(array.astype(np.float64)), 0, 255).astype(np.uint8)
        from PIL import Image

        image = Image.fromarray(np.ascontiguousarray(array), "RGB")
    else:
        from PIL import Image

        with Image.open(source) as opened:
            width, height = opened.size
            long_side = max(width, height)
            if opened.format == "JPEG" and long_side > ANALYSIS_MAX_SIDE_PX and not _is_screen_size(width, height):
                # DCT-domain downscale (1/2, 1/4, 1/8) during decoding: a box-like
                # average like Image.reduce, at a fraction of the decode cost.
                scale = ANALYSIS_MAX_SIDE_PX / long_side
                opened.draft("RGB", (max(1, int(width * scale)), max(1, int(height * scale))))
            opened.load()
            image = _to_rgb(opened)
        full = np.asarray(image, dtype=np.uint8) if _is_screen_size(width, height) else np.zeros((0, 0, 3), np.uint8)
        factor = -(-max(image.size) // ANALYSIS_MAX_SIDE_PX)
        reduced = image.reduce(factor) if factor > 1 else image
        return np.asarray(reduced, dtype=np.uint8), (width, height), full
    width, height = image.size
    full = np.asarray(image, dtype=np.uint8) if _is_screen_size(width, height) else np.zeros((0, 0, 3), np.uint8)
    factor = -(-max(width, height) // ANALYSIS_MAX_SIDE_PX)
    reduced = image.reduce(factor) if factor > 1 else image
    return np.asarray(reduced, dtype=np.uint8), (width, height), full


def _to_rgb(image: Any) -> Any:
    from PIL import Image

    if image.mode in {"I;16", "I;16B", "I;16L", "I"}:
        array = np.asarray(image, dtype=np.float64)
        peak = max(1.0, float(array.max()))
        return Image.fromarray((array * (255.0 / peak)).astype(np.uint8), "L").convert("RGB")
    if image.mode in {"RGBA", "LA", "PA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    return image.convert("RGB")


def _is_screen_size(width: int, height: int) -> bool:
    return (width, height) in SCREEN_RESOLUTIONS or (height, width) in SCREEN_RESOLUTIONS


# --- statistics ------------------------------------------------------------


def _luma(rgb: np.ndarray) -> np.ndarray:
    weights: np.ndarray = np.asarray(LUMA_WEIGHTS, dtype=np.float64)
    return rgb[..., :3].astype(np.float64) @ weights


def _packed(rgb: np.ndarray) -> np.ndarray:
    return (rgb[..., 0].astype(np.uint32) << 16) | (rgb[..., 1].astype(np.uint32) << 8) | rgb[..., 2].astype(np.uint32)


def _unique_colors(rgb: np.ndarray) -> int:
    values: np.ndarray = np.sort(_packed(rgb), axis=None)
    if values.size == 0:
        return 0
    return int(np.count_nonzero(values[1:] != values[:-1]) + 1)


def _flat_fraction(rgb: np.ndarray) -> float:
    if rgb.shape[0] < 2 or rgb.shape[1] < 2:
        return 1.0
    core = rgb[:-1, :-1]
    right = np.all(core == rgb[:-1, 1:], axis=2)
    down = np.all(core == rgb[1:, :-1], axis=2)
    return float(np.mean(right & down))


def _box_residual(luma: np.ndarray) -> np.ndarray:
    """Luma minus its 3x3 box mean (interior pixels), flattened."""
    if luma.shape[0] < 3 or luma.shape[1] < 3:
        return np.zeros(0)
    box = np.zeros_like(luma[1:-1, 1:-1])
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            box += luma[dy:dy + luma.shape[0] - 2, dx:dx + luma.shape[1] - 2]
    return (luma[1:-1, 1:-1] - box / 9.0).ravel()


def _residual_moments(luma: np.ndarray) -> tuple[float, float]:
    """(variance, kurtosis) of the 3x3 box residual; kurtosis 0 if flat."""
    residual = _box_residual(luma)
    if residual.size == 0:
        return 0.0, 0.0
    centered = residual - residual.mean()
    squared = centered * centered
    variance = float(squared.mean())
    if variance == 0.0:
        return 0.0, 0.0
    return variance, float((squared * squared).mean() / (variance * variance))


def _lag1_autocorr(a: np.ndarray, b: np.ndarray) -> float:
    a = a.ravel() - a.mean()
    b = b.ravel() - b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    if denom == 0.0:
        return 1.0  # constant along this axis: perfectly predictable
    return float((a * b).sum() / denom)


def _text_line_profile(luma: np.ndarray) -> tuple[int, float, float]:
    """(line count, blank-row fraction, median line height / image height)."""
    ink_rows = (luma < DOC_INK_LUMA).mean(axis=1) > DOC_INK_ROW_FRACTION
    padded = np.concatenate(([False], ink_rows, [False])).astype(np.int8)
    edges = np.diff(padded)
    heights = np.flatnonzero(edges == -1) - np.flatnonzero(edges == 1)
    blank = float(1.0 - ink_rows.mean())
    median_height = float(np.median(heights)) / max(1, luma.shape[0]) if heights.size else 0.0
    return int(heights.size), blank, median_height


def _status_bar_solid_fraction(full: np.ndarray) -> float:
    height = full.shape[0]
    band_px = max(1, int(round(max(full.shape[:2]) * STATUS_BAR_FRACTION)))
    band = full[:min(height, band_px)]
    values: np.ndarray = np.sort(_packed(band), axis=None)
    if values.size == 0:
        return 0.0
    boundaries: np.ndarray = np.flatnonzero(values[1:] != values[:-1]) + 1
    runs = np.diff(np.concatenate(([0], boundaries, [values.size])))
    return float(runs.max() / values.size)


def _axis_step_runs(step: np.ndarray) -> int:
    """Pixels on runs of >= EDGE_RUN_MIN_PX consecutive True along axis 1."""
    if step.size == 0:
        return 0
    padded = np.zeros((step.shape[0], step.shape[1] + 2), dtype=np.int8)
    padded[:, 1:-1] = step
    diff = np.diff(padded, axis=1)
    starts = np.argwhere(diff == 1)
    ends = np.argwhere(diff == -1)
    lengths = ends[:, 1] - starts[:, 1]
    return int(lengths[lengths >= EDGE_RUN_MIN_PX].sum())


def _max_abs_diff(channels: list[np.ndarray], a: tuple[slice, slice], b: tuple[slice, slice]) -> np.ndarray:
    out = np.abs(channels[0][a] - channels[0][b])
    for channel in channels[1:]:
        np.maximum(out, np.abs(channel[a] - channel[b]), out=out)
    return out


def _exact_steps(channels: list[np.ndarray]) -> np.ndarray:
    """Boundaries between rows y and y+1 that are exact UI edges at column x.

    A step of >= EDGE_STEP_MIN across the boundary, with both sides flat
    along the boundary (each side differs from its right-hand neighbour by
    <= EDGE_FLAT_TOLERANCE in every channel). Output shape (H-1, W-1).
    """
    top, bottom = slice(None, -1), slice(1, None)
    left, right = slice(None, -1), slice(1, None)
    across = _max_abs_diff(channels, (bottom, left), (top, left)) >= EDGE_STEP_MIN
    across &= _max_abs_diff(channels, (top, right), (top, left)) <= EDGE_FLAT_TOLERANCE
    across &= _max_abs_diff(channels, (bottom, right), (bottom, left)) <= EDGE_FLAT_TOLERANCE
    return np.asarray(across)


def _exact_edge_fraction(full: np.ndarray) -> float:
    channels = [np.ascontiguousarray(full[..., index], dtype=np.int16) for index in range(3)]
    transposed = [np.ascontiguousarray(channel.T) for channel in channels]
    on_runs = _axis_step_runs(_exact_steps(channels)) + _axis_step_runs(_exact_steps(transposed))
    return on_runs / float(full.shape[0] * full.shape[1])


def _local_contrast_cv(luma: np.ndarray) -> float:
    """Coefficient of variation of block-wise luma std (non-stationarity)."""
    rows, cols = luma.shape
    grid = LOCAL_CONTRAST_GRID
    if rows < grid * 4 or cols < grid * 4:
        return 1.0
    bh, bw = rows // grid, cols // grid
    blocks = luma[: bh * grid, : bw * grid].reshape(grid, bh, grid, bw).swapaxes(1, 2).reshape(grid * grid, bh * bw)
    stds = blocks.std(axis=1)
    mean = float(stds.mean())
    if mean == 0.0:
        return 0.0
    return float(stds.std() / mean)


def _residual_kurtosis(luma: np.ndarray) -> float:
    if luma.shape[0] < 3 or luma.shape[1] < 3:
        return 0.0
    box = np.zeros_like(luma[1:-1, 1:-1])
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            box += luma[dy:dy + luma.shape[0] - 2, dx:dx + luma.shape[1] - 2]
    residual = (luma[1:-1, 1:-1] - box / 9.0).ravel()
    residual = residual - residual.mean()
    var = float((residual * residual).mean())
    if var == 0.0:
        return 0.0
    return float((residual ** 4).mean() / (var * var))


def image_statistics(source: ImageSource) -> dict[str, Any]:
    rgb, (width, height), full = _load_rgb(source)
    luma = _luma(rgb)
    pixels = int(rgb.shape[0] * rgb.shape[1])
    unique = _unique_colors(rgb)
    variance, kurtosis = _residual_moments(luma)
    stats: dict[str, Any] = {
        "width": width,
        "height": height,
        "analysis_size": [int(rgb.shape[1]), int(rgb.shape[0])],
        "unique_colors": unique,
        "unique_ratio": round(unique / max(1, pixels), 6),
        "flat_fraction": round(_flat_fraction(rgb), 6),
        "residual_variance": round(variance, 4),
        "autocorr_h": round(_lag1_autocorr(luma[:, :-1], luma[:, 1:]), 6) if luma.shape[1] > 1 else 1.0,
        "autocorr_v": round(_lag1_autocorr(luma[:-1], luma[1:]), 6) if luma.shape[0] > 1 else 1.0,
        "local_contrast_cv": round(_local_contrast_cv(luma), 4),
        "residual_kurtosis": round(kurtosis, 4),
        "near_white_fraction": round(float((luma >= DOC_NEAR_WHITE_LUMA).mean()), 6),
        "ink_fraction": round(float((luma < DOC_INK_LUMA).mean()), 6),
        "screen_resolution": _is_screen_size(width, height),
    }
    stats["midtone_fraction"] = round(1.0 - stats["near_white_fraction"] - stats["ink_fraction"], 6)
    lines, blank, line_height = _text_line_profile(luma)
    stats["text_lines"] = lines
    stats["median_line_height_fraction"] = round(line_height, 6)
    stats["blank_row_fraction"] = round(blank, 6)
    if full.size:
        stats["status_bar_solid_fraction"] = round(_status_bar_solid_fraction(full), 6)
        stats["exact_edge_fraction"] = round(_exact_edge_fraction(full), 6)
    return stats


# --- rules -----------------------------------------------------------------


def _screenshot_reasons(stats: dict[str, Any]) -> list[str]:
    if not stats.get("screen_resolution"):
        return []
    solid = float(stats.get("status_bar_solid_fraction", 0.0))
    edges = float(stats.get("exact_edge_fraction", 0.0))
    if solid < STATUS_BAR_MIN_SOLID_FRACTION or edges < SCREENSHOT_MIN_EDGE_FRACTION:
        return []
    return [
        f"화면 해상도 {stats['width']}x{stats['height']}",
        f"상단 상태바 단색 비율 {solid:.0%}",
        f"정확한 수평·수직 경계 비율 {edges:.2%}",
    ]


def _document_reasons(stats: dict[str, Any]) -> list[str]:
    white = float(stats["near_white_fraction"])
    ink = float(stats["ink_fraction"])
    if white <= DOC_MIN_WHITE_FRACTION or ink < DOC_MIN_INK_FRACTION:
        return []
    if float(stats["midtone_fraction"]) >= ink:
        return []
    if int(stats["text_lines"]) < DOC_MIN_TEXT_LINES or float(stats["blank_row_fraction"]) < DOC_MIN_BLANK_ROW_FRACTION:
        return []
    if float(stats["median_line_height_fraction"]) > DOC_MAX_LINE_HEIGHT_FRACTION:
        return []
    return [
        f"밝기 양봉 분포(흰 바탕 {white:.0%}, 잉크 {ink:.0%})",
        f"텍스트 줄 구조 {stats['text_lines']}줄",
    ]


def _pattern_reasons(stats: dict[str, Any]) -> list[str]:
    reasons = []
    if int(stats["unique_colors"]) < PATTERN_MAX_UNIQUE_COLORS:
        reasons.append(f"고유 색 {stats['unique_colors']}개(< {PATTERN_MAX_UNIQUE_COLORS})")
    residual = float(stats["residual_variance"])
    if residual < PHOTO_RESIDUAL_VAR_MIN:
        reasons.append(f"노이즈 잔차 분산 {residual:.3g}(사진 범위 {PHOTO_RESIDUAL_VAR_MIN} 미만 — 잡음 없는 합성 표면)")
    elif residual > PHOTO_RESIDUAL_VAR_MAX:
        reasons.append(f"노이즈 잔차 분산 {residual:.4g}(사진 범위 {PHOTO_RESIDUAL_VAR_MAX:g} 초과 — 백색 잡음 수준)")
    ac_h, ac_v = float(stats["autocorr_h"]), float(stats["autocorr_v"])
    if min(ac_h, ac_v) > GRADIENT_MIN_AUTOCORR:
        reasons.append(f"행/열 자기상관 {ac_h:.4f}/{ac_v:.4f}(> {GRADIENT_MIN_AUTOCORR} — 그라데이션)")
    elif max(abs(ac_h), abs(ac_v)) < WHITE_NOISE_MAX_AUTOCORR:
        reasons.append(f"행/열 자기상관 {ac_h:.3f}/{ac_v:.3f}(< {WHITE_NOISE_MAX_AUTOCORR} — 백색 잡음)")
    cv, kurtosis = float(stats["local_contrast_cv"]), float(stats["residual_kurtosis"])
    if cv < STATIONARY_MAX_CONTRAST_CV and kurtosis < GAUSSIAN_FIELD_MAX_KURTOSIS:
        reasons.append(f"균질한 가우스 잡음장(국소 대비 변동계수 {cv:.2f}, 잔차 첨도 {kurtosis:.2f})")
    return reasons


def _graphic_reasons(stats: dict[str, Any]) -> list[str]:
    ratio = float(stats["unique_ratio"])
    flat = float(stats["flat_fraction"])
    if ratio < GRAPHIC_MAX_UNIQUE_RATIO and flat > GRAPHIC_MIN_FLAT_FRACTION:
        return [f"고유 색 비율 {ratio:.2%}", f"단색 영역 {flat:.0%}"]
    return []


def classify_stats(stats: dict[str, Any]) -> ImageClass:
    """Apply the rules to precomputed :func:`image_statistics` output."""
    width, height = int(stats["width"]), int(stats["height"])
    if max(width, height) < TOO_SMALL_LONG_SIDE_PX:
        return ImageClass(TOO_SMALL, [f"긴 변 {max(width, height)}px < {TOO_SMALL_LONG_SIDE_PX}px"], stats)
    for kind, rule in ((SCREENSHOT, _screenshot_reasons), (DOCUMENT_SCAN, _document_reasons), (PATTERN, _pattern_reasons), (GRAPHIC, _graphic_reasons)):
        reasons = rule(stats)
        if reasons:
            return ImageClass(kind, reasons, stats)
    return ImageClass(PHOTO, ["비사진 규칙에 해당하지 않음"], stats)


def too_small_class(width: int, height: int) -> ImageClass | None:
    """``too_small`` from dimensions alone (no decoding, no numpy)."""
    if max(width, height) < TOO_SMALL_LONG_SIDE_PX:
        return ImageClass(
            TOO_SMALL,
            [f"긴 변 {max(width, height)}px < {TOO_SMALL_LONG_SIDE_PX}px"],
            {"width": width, "height": height},
        )
    return None


def classify_image(source: ImageSource, *, dimensions: tuple[int, int] | None = None) -> ImageClass:
    """Classify an image file (path or binary file object) or an RGB/gray array.

    Deterministic. ``dimensions`` (e.g. from the file header) lets a
    ``too_small`` image be classified without decoding it. Raises
    ``ModuleNotFoundError`` without numpy/Pillow (recorded as a skipped
    check) and ``OSError``/``SyntaxError``/``ValueError`` when the file
    cannot be decoded (recorded as a failed check).
    """
    if dimensions:
        small = too_small_class(*dimensions)
        if small is not None:
            return small
    if np is None:
        raise ModuleNotFoundError("numpy", name="numpy")
    if not isinstance(source, np.ndarray):
        from PIL import Image

        with Image.open(source) as probe:
            width, height = probe.size
        if hasattr(source, "seek"):
            source.seek(0)
        small = too_small_class(width, height)
        if small is not None:
            return small
    return classify_stats(image_statistics(source))
