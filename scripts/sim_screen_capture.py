"""Simulate screen-recording / camera-of-screen degradations for eval.

Real screen captures differ from flat re-encodes: pixel-grid resampling
(moiré), brightness banding from refresh-rate beat, optional perspective
skew when a camera films the display, then a final lossy encode.

Usage:
    python scripts/sim_screen_capture.py --input img.png --output out.jpg --kind image
    python scripts/sim_screen_capture.py --input clip.mp4 --output out.mp4 --kind video
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path


def degrade_frame(img, *, moire: bool, banding: bool, perspective: bool, rng):
    """Apply screen-capture degradations to one BGR numpy frame."""
    import cv2
    import numpy as np

    h, w = img.shape[:2]
    out = img.astype(np.float32)

    if perspective:
        # Slight keystone as if filmed off-axis.
        dx, dy = w * 0.02, h * 0.02
        src_pts = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        dst_pts = np.float32([
            [dx, dy * 0.4], [w - dx * 0.6, 0],
            [w, h - dy], [dx * 0.3, h],
        ])
        m = cv2.getPerspectiveTransform(src_pts, dst_pts)
        out = cv2.warpPerspective(out, m, (w, h), borderMode=cv2.BORDER_REPLICATE)

    if moire:
        # Down to a display pixel grid then back up — resampling moiré.
        grid = rng.choice([0.5, 0.6, 0.7])
        small = cv2.resize(out, (int(w * grid), int(h * grid)), interpolation=cv2.INTER_LINEAR)
        out = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)

    if banding:
        # Slow vertical brightness wave — refresh-rate beat.
        rows = np.arange(h, dtype=np.float32)
        wave = 1.0 + 0.05 * np.sin(2 * np.pi * rows / rng.choice([11, 17, 23]) + rng.uniform(0, 3))
        out = out * wave[:, None, None]

    return np.clip(out, 0, 255).astype("uint8")


def degrade_image(src: Path, dst: Path, *, seed: int) -> None:
    import random

    import cv2

    rng = random.Random(seed)
    img = cv2.imread(str(src))
    if img is None:
        raise SystemExit(f"cannot read {src}")
    out = degrade_frame(img, moire=True, banding=rng.random() < 0.5,
                        perspective=rng.random() < 0.3, rng=rng)
    cv2.imwrite(str(dst), out, [cv2.IMWRITE_JPEG_QUALITY, 80])


def degrade_video(src: Path, dst: Path, *, seed: int) -> None:
    import random

    import cv2

    rng = random.Random(seed)
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
        silent = Path(tf.name)
    vw = cv2.VideoWriter(str(silent), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    banding = rng.random() < 0.6
    persp = rng.random() < 0.3
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        vw.write(degrade_frame(frame, moire=True, banding=banding,
                               perspective=persp, rng=rng))
    cap.release()
    vw.release()
    # Re-encode with audio passthrough + H.264 (crf 30 ≈ social re-upload).
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-i", str(src),
         "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-crf", "30",
         "-c:a", "aac", "-shortest", str(dst)],
        check=True,
    )
    silent.unlink(missing_ok=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--kind", choices=["image", "video"], required=True)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    if args.kind == "image":
        degrade_image(args.input, args.output, seed=args.seed)
    else:
        degrade_video(args.input, args.output, seed=args.seed)
    print(f"wrote {args.output}")
