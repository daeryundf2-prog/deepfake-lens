"""Build a tiny synthetic labeled-image dataset for pipeline smoke tests.

Real images are smooth numpy gradients (camera-like); AI images get a
checkerboard upsample pattern that the pixel experts are designed to flag.
The point is exercising the dataset -> manifest -> SBI training -> ONNX
export path end to end, not benchmark quality.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np


def make_real(index: int, size: int = 96) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size] / size
    base = 40 + 90 * (0.4 * xx + 0.3 * yy + 0.3 * math.sin(index))
    gradient = base[:, :, None] * np.ones((size, size, 3))
    gradient[:, :, 1] *= 0.9 + 0.1 * yy
    gradient[:, :, 2] *= 0.8 + 0.2 * xx
    noise = np.random.default_rng(1000 + index).normal(0, 2.5, gradient.shape)
    return np.clip(gradient + noise, 0, 255)


def make_ai(index: int, size: int = 96) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size] / size
    gradient = (60 + 120 * (0.5 * xx + 0.5 * yy))[:, :, None] * np.ones((size, size, 3))
    small = gradient[::2, ::2]
    upsampled = np.repeat(np.repeat(small, 2, axis=0), 2, axis=1)
    upsampled = upsampled[:size, :size]
    # checkerboard texture typical of upsampling artifacts
    checker = np.indices((size, size)).sum(axis=0) % 2
    upsampled[:, :, 0] += 12 * checker
    return np.clip(upsampled, 0, 255)


def save_png(path: Path, array: np.ndarray) -> None:
    from PIL import Image

    Image.fromarray(array.astype(np.uint8)).save(path, format="PNG")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--per-split", type=int, default=8)
    args = parser.parse_args()

    root = args.out
    rng = np.random.default_rng(7)
    for split in ("train", "val", "test"):
        for label in ("ai", "real"):
            folder = root / split / label
            folder.mkdir(parents=True, exist_ok=True)
            for i in range(args.per_split):
                array = make_real(f"{split}-{label}-{i}".__hash__() & 0xFFFF) if label == "real" else make_ai(abs(hash((split, label, i))) & 0xFFFF)
                save_png(folder / f"{label}-{i:03d}.png", array)
    print(json.dumps({"root": str(root), "per_split": args.per_split, "labels": ["ai", "real"], "splits": ["train", "val", "test"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
