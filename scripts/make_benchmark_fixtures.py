#!/usr/bin/env python3
"""Generate the license-clean benchmark fixtures in fixtures/benchmark/.

All images are synthesized here by this script — no downloaded or
third-party media, so there are no licensing or provenance questions.
The PNGs are written by a pure-stdlib encoder (struct + zlib only), and
the pixel generators are seeded so the bytes are fully deterministic:
re-running this script must reproduce the committed files bit-for-bit.

What gets generated (all small enough to live in git):

  - real-like-texture.png   96x96  value-noise + film-grain texture that
                                   stands in for a camera capture. Carries
                                   no generator metadata; expected to land
                                   in the low-signal bands.
  - ai-like-gradient.png    512x512 smooth synthetic gradient. Also has no
                                   metadata, but the 512x512 canvas is the
                                   classic generator square resolution and
                                   exercises that heuristic signal.
  - a1111-metadata-marker.png  64x64 gradient carrying a PNG tEXt
                                   "parameters" chunk with an
                                   AUTOMATIC1111-style parameter string —
                                   the metadata path that must score HIGH.

These fixtures feed tests/test_benchmark_e2e.py, which is a
smoke/regression harness for the scan pipeline — NOT a calibrated
accuracy benchmark. Do not read detection quality into its scores.

Usage:
    python scripts/make_benchmark_fixtures.py            # write fixtures
    python scripts/make_benchmark_fixtures.py --check    # verify committed bytes
    python scripts/make_benchmark_fixtures.py --out DIR  # write elsewhere
"""

from __future__ import annotations

import argparse
import math
import random
import struct
import sys
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "fixtures" / "benchmark"

# Fixed seeds — the committed fixture bytes are pinned to these.
SEED_REAL_TEXTURE = 20260912
SEED_GRADIENT = 20260913

A1111_PARAMETERS = (
    "a majestic wolf howling at a neon moon, intricate fur detail, cinematic lighting\n"
    "Negative prompt: blurry, lowres, deformed, watermark\n"
    "Steps: 28, Sampler: Euler a, CFG scale: 7.0, Seed: 31415926, "
    "Size: 512x512, Model hash: c6bbc36e32, Model: sd-v1-5, Clip skip: 2"
)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _filtered_rows(width: int, height: int, pixels: bytes) -> bytes:
    """Per-row adaptive filtering (None/Sub/Up), the standard
    minimum-sum-of-absolute-differences heuristic. Keeps the smooth
    gradients tiny without hurting the noisy texture."""
    stride = width * 3
    bpp = 3
    prev = bytes(stride)
    out = bytearray()
    for y in range(height):
        row = pixels[y * stride : (y + 1) * stride]
        sub = bytes((row[i] - (row[i - bpp] if i >= bpp else 0)) & 0xFF for i in range(stride))
        up = bytes((row[i] - prev[i]) & 0xFF for i in range(stride))

        def score(candidate: bytes) -> int:
            return sum(abs(b - 256 if b > 127 else b) for b in candidate)

        choice = min(((score(row), 0, row), (score(sub), 1, sub), (score(up), 2, up)), key=lambda c: c[0])
        out.append(choice[1])
        out.extend(choice[2])
        prev = row
    return bytes(out)


def encode_png(width: int, height: int, pixels: bytes, text_chunks: dict[str, str] | None = None) -> bytes:
    """Encode an 8-bit RGB PNG. ``pixels`` is row-major RGB bytes."""
    if len(pixels) != width * height * 3:
        raise ValueError(f"expected {width * height * 3} pixel bytes, got {len(pixels)}")
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = _filtered_rows(width, height, pixels)
    data = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
    for key, value in (text_chunks or {}).items():
        data += _chunk(b"tEXt", key.encode("latin-1") + b"\x00" + value.encode("latin-1"))
    return data + _chunk(b"IDAT", zlib.compress(rows, 9)) + _chunk(b"IEND", b"")


def _clamp8(value: float) -> int:
    return max(0, min(255, int(round(value))))


def real_like_pixels(size: int, seed: int) -> bytes:
    """Coarse value noise bilinearly upsampled, plus fine grain.

    Produces a textured, camera-ish surface rather than flat white noise —
    flat noise is itself an anomaly, so this is a fairer "real" stand-in.
    """
    rng = random.Random(seed)
    grid_n = 13  # coarse lattice, then interpolate to `size`
    grid = [[rng.random() for _ in range(grid_n)] for _ in range(grid_n)]

    out = bytearray()
    for y in range(size):
        gy = y * (grid_n - 1) / (size - 1)
        y0 = int(gy)
        y1 = min(y0 + 1, grid_n - 1)
        wy = gy - y0
        for x in range(size):
            gx = x * (grid_n - 1) / (size - 1)
            x0 = int(gx)
            x1 = min(x0 + 1, grid_n - 1)
            wx = gx - x0
            top = grid[y0][x0] * (1 - wx) + grid[y0][x1] * wx
            bottom = grid[y1][x0] * (1 - wx) + grid[y1][x1] * wx
            base = top * (1 - wy) + bottom * wy
            # per-channel grain, drawn in a fixed order for determinism
            out.append(_clamp8((base + rng.uniform(-0.07, 0.07)) * 255))
            out.append(_clamp8((base + rng.uniform(-0.07, 0.07)) * 255))
            out.append(_clamp8((base + rng.uniform(-0.07, 0.07)) * 255))
    return bytes(out)


def ai_like_gradient_pixels(size: int, seed: int) -> bytes:
    """Perfectly smooth synthetic gradient — the textureless look the
    metadata-free AI stand-in needs. Seeded only for future variation."""
    rng = random.Random(seed)  # noqa: F841 - kept for stable future tweaks
    out = bytearray()
    center = (size - 1) / 2
    for y in range(size):
        for x in range(size):
            radial = math.hypot(x - center, y - center) / (center * math.sqrt(2))
            r = 255 * (x / (size - 1))
            g = 255 * (y / (size - 1))
            b = 255 * (1 - radial)
            out.extend((_clamp8(r), _clamp8(g), _clamp8(b)))
    return bytes(out)


def build_fixtures() -> dict[str, bytes]:
    """Return {filename: png_bytes} for every fixture in the set."""
    return {
        "real-like-texture.png": encode_png(96, 96, real_like_pixels(96, SEED_REAL_TEXTURE)),
        "ai-like-gradient.png": encode_png(512, 512, ai_like_gradient_pixels(512, SEED_GRADIENT)),
        "a1111-metadata-marker.png": encode_png(
            64,
            64,
            ai_like_gradient_pixels(64, SEED_GRADIENT),
            text_chunks={"parameters": A1111_PARAMETERS},
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=FIXTURE_DIR, help=f"output directory (default: {FIXTURE_DIR})")
    parser.add_argument("--check", action="store_true", help="verify committed fixtures match regenerated bytes")
    args = parser.parse_args(argv)

    fixtures = build_fixtures()

    if args.check:
        mismatched = []
        for name, expected in sorted(fixtures.items()):
            path = args.out / name
            actual = path.read_bytes() if path.is_file() else None
            status = "ok" if actual == expected else ("missing" if actual is None else "differs")
            print(f"  {status:8} {name} ({len(expected)} bytes)")
            if actual != expected:
                mismatched.append(name)
        if mismatched:
            print(f"check FAILED: {mismatched} — rerun scripts/make_benchmark_fixtures.py", file=sys.stderr)
            return 1
        print("check OK: all fixtures reproduce byte-for-byte")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, payload in sorted(fixtures.items()):
        target = args.out / name
        target.write_bytes(payload)
        total += len(payload)
        print(f"  wrote {target} ({len(payload)} bytes)")
    print(f"done: {len(fixtures)} fixtures, {total} bytes total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
