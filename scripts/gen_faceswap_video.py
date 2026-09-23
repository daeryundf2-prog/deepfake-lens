"""Generate synthetic face-swap test videos for face_track evaluation.

Pastes a source face (StyleGAN/generated face image) over the detected
face box of each frame of a real clip. Two variants:

- ``jitter``: per-frame random source face + sub-pixel offset noise —
  mimics cheap per-frame swap tools.
- ``smooth``: single source face, box-aligned — the harder case where
  identity is constant but geometry is pasted.

For evaluation only; output goes to eval_corpus/video/fake/.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def paste_swap(frame_bgr, src_bgr, face_box, jitter_px: int, rng):
    import cv2

    x, y, w, h = face_box
    if w <= 4 or h <= 4:
        return frame_bgr
    import numpy as np

    src = cv2.resize(src_bgr, (w, h), interpolation=cv2.INTER_AREA)
    jx = rng.randint(-jitter_px, jitter_px)
    jy = rng.randint(-jitter_px, jitter_px)
    mask = np.zeros((h, w), "uint8")
    cv2.ellipse(mask, (w // 2, h // 2), (int(w * 0.42), int(h * 0.45)), 0, 0, 360, 255, -1)
    mask = cv2.GaussianBlur(mask, (9, 9), 0)
    dst_y = slice(max(0, y + jy), min(frame_bgr.shape[0], y + jy + h))
    dst_x = slice(max(0, x + jx), min(frame_bgr.shape[1], x + jx + w))
    sy = slice(dst_y.start - (y + jy), dst_y.stop - (y + jy))
    sx = slice(dst_x.start - (x + jx), dst_x.stop - (x + jx))
    roi = frame_bgr[dst_y, dst_x]
    m = (mask[sy, sx, None] / 255.0)
    frame_bgr[dst_y, dst_x] = (src[sy, sx] * m + roi * (1 - m)).astype("uint8")
    return frame_bgr


def swap_video(src_video: Path, faces_dir: Path, out: Path, mode: str, rng) -> int:
    import cv2
    from deepfake_lens.face import _detect_faces

    sources = []
    for p in sorted(faces_dir.glob("*.jpg"))[:12]:
        img = cv2.imread(str(p))
        if img is not None:
            sources.append(img)
    if not sources:
        raise SystemExit(f"no source faces in {faces_dir}")

    cap = cv2.VideoCapture(str(src_video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    vw = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    smooth_src = rng.choice(sources)
    n = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        faces = _detect_faces(rgb)
        if faces:
            f = max(faces, key=lambda r: r.width * r.height)
            src = smooth_src if mode == "smooth" else rng.choice(sources)
            jitter = 0 if mode == "smooth" else 3
            frame = paste_swap(frame, src, (f.x, f.y, f.width, f.height), jitter, rng)
        vw.write(frame)
        n += 1
    cap.release()
    vw.release()
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--faces", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=["jitter", "smooth"], default="jitter")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    n = swap_video(
        Path(args.video), Path(args.faces), Path(args.out),
        args.mode, random.Random(args.seed),
    )
    print(f"wrote {args.out}: {n} frames")
