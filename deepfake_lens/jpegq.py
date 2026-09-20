"""JPEG quality-factor estimation from embedded quantization tables.

Reads the luminance DQT via Pillow and finds the libjpeg quality value
(1-100) whose scaled standard table best matches — the same table family
most encoders (libjpeg, libjpeg-turbo, most phone cameras) emit.

Used to gate ensemble members: detectors measured fragile under
recompression declare ``degraded_weight`` so a low-QF input cannot lean
on a member whose signal is destroyed below it.
"""

from __future__ import annotations

from pathlib import Path

# Standard JPEG luminance quantization table, natural (row-major) order.
_LUMA_BASE = [
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77,
    24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103, 99,
]

# Zigzag traversal order of the 8x8 matrix — DQT tables are stored zigzag.
_ZIGZAG = [
    0, 1, 8, 16, 9, 2, 3, 10,
    17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63,
]

_LUMA_BASE_ZZ = [_LUMA_BASE[i] for i in _ZIGZAG]


def _scaled_table(quality: int) -> list[int]:
    quality = max(1, min(100, quality))
    scale = 5000 // quality if quality < 50 else 200 - quality * 2
    return [max(1, min(255, (base * scale + 50) // 100)) for base in _LUMA_BASE_ZZ]


def estimate_jpeg_quality(path: Path | str) -> float | None:
    """Estimate the libjpeg quality factor (1-100) of a JPEG file.

    Returns None for non-JPEG inputs or files without a luminance DQT —
    callers must treat None as 'unknown', not 'high quality'.
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as image:
            tables = getattr(image, "quantization", None)
    except Exception:  # noqa: BLE001 - unreadable/non-JPEG files have no tables
        return None
    if not tables:
        return None
    # Table 0 is luminance for baseline JPEGs; fall back to the first table.
    table = tables.get(0) or next(iter(tables.values()), None)
    if not table or len(table) != 64:
        return None
    observed = [int(v) for v in table]
    best_q, best_err = 0, float("inf")
    for q in range(1, 101):
        ref = _scaled_table(q)
        err = sum((a - b) ** 2 for a, b in zip(observed, ref))
        if err < best_err:
            best_q, best_err = q, err
    # Tables that are far off every libjpeg scale (custom encoders) are
    # reported anyway — the estimate is approximate, not exact.
    return float(best_q)


def image_min_side(path: Path | str) -> int | None:
    """Smaller dimension of an image, or None when unreadable."""
    try:
        from PIL import Image

        with Image.open(path) as image:
            return min(image.size)
    except Exception:  # noqa: BLE001
        return None
