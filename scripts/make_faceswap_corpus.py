#!/usr/bin/env python3
"""Generate a real faceswap corpus with inswapper_128 (InsightFace).

Unlike the SBI self-blend corpus (same face blended onto itself), this
performs actual identity swaps between different portraits, producing the
boundary artifacts the faceswap_seam layer is designed to detect.

Usage:

    python scripts/make_faceswap_corpus.py \
        --real-dir /tmp/dfl-faces2/full/diverse_real \
        --out /tmp/dfl-faces2/inswapper_fake \
        --swapper /tmp/dfl-swap/inswapper_128.onnx

Each target image keeps its own composition; the swapped face comes from a
different portrait (cycled with --offset so no image is swapped with
itself). Output pairs line up 1:1 with the input real frames — use the
same directory as --real-dir in eval_seam_thresholds.py.

Requires: insightface, onnxruntime, cv2, and the inswapper_128.onnx
checkpoint (InsightFace, non-commercial research license). Face detection
and embeddings come from insightface's buffalo_l pack, auto-downloaded
under --models-root.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    _repo = Path(__file__).resolve().parents[1]
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help
    parser = KoreanArgumentParser(description="실제 인물 사진에 insightface inswapper로 얼굴을 바꿔 페이스스왑 코퍼스를 만듭니다.")
    parser.add_argument("--real-dir", type=Path, required=True, help="실제 인물 사진 폴더")
    parser.add_argument("--out", type=Path, required=True, help="얼굴을 바꾼 이미지를 쓸 폴더")
    parser.add_argument("--swapper", type=Path, required=True, help="inswapper_128.onnx 파일 경로")
    parser.add_argument("--models-root", type=Path, default=Path("/tmp/dfl-swap"), help="insightface 모델 폴더(buffalo_l)")
    parser.add_argument("--offset", type=int, default=17, help="원본 얼굴 인덱스 오프셋(개수와 서로소)")
    parser.add_argument("--max-images", type=int, default=0, help="입력 수 상한(0 = 전부)")
    args = parser.parse_args()

    try:
        import cv2
        import numpy as np  # noqa: F401 — availability probe
        from insightface.app import FaceAnalysis
        from insightface.model_zoo import get_model
    except ImportError as exc:
        print(f"error: missing dependency: {exc}", file=sys.stderr)
        return 2

    paths = sorted(p for p in args.real_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    if args.max_images:
        paths = paths[: args.max_images]
    if len(paths) < 2:
        print("error: need at least 2 real images to swap between", file=sys.stderr)
        return 2

    app = FaceAnalysis(name="buffalo_l", root=str(args.models_root))
    app.prepare(ctx_id=-1, det_size=(320, 320))
    swapper = get_model(str(args.swapper), providers=["CPUExecutionProvider"])

    # Extract the dominant face embedding for every portrait once.
    embeddings = []
    images = []
    for path in paths:
        img = cv2.imread(str(path))
        if img is None:
            continue
        faces = app.get(img)
        if not faces:
            continue
        face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        images.append(img)
        embeddings.append(face)
    print(f"faces embedded: {len(images)} / {len(paths)}")
    if len(images) < 2:
        print("error: fewer than 2 detectable faces", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    written = 0
    for i, (img, target_face) in enumerate(zip(images, embeddings)):
        source_face = embeddings[(i + args.offset) % len(embeddings)]
        if source_face is target_face and len(embeddings) > 1:
            source_face = embeddings[(i + args.offset + 1) % len(embeddings)]
        try:
            swapped = swapper.get(img, target_face, source_face, paste_back=True)
        except Exception as exc:  # noqa: BLE001 - one bad frame must not kill the batch
            print(f"warn: swap failed for index {i}: {exc}", file=sys.stderr)
            continue
        out_path = args.out / f"{written:03d}.jpg"
        cv2.imwrite(str(out_path), swapped, [cv2.IMWRITE_JPEG_QUALITY, 92])
        written += 1
        if written % 10 == 0:
            print(f"  wrote {written} swaps...", flush=True)
    print(f"done: {written} swapped images -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
