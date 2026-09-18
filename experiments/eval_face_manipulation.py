"""Face-manipulation detector evaluation harness.

Measures the face-crop ViT member (models/face-manipulation-vit-runtime.json)
on a labeled set:

    python experiments/eval_face_manipulation.py --real-dir <real faces>
        [--fake-dir <known-fake faces>]
        [--profile models/face-manipulation-vit-runtime.json]
        [--variants original,jpeg75,half]
        [--report experiments/face_eval_report.json]

Labeling:
- Every image in --real-dir is label 0. A self-blended (SBI) manipulation is
  synthesized from each real image and scored as label 1 — so coverage is
  measured even without a fake corpus.
- --fake-dir images are label 1 as-is.

Variants apply to every sample: original, jpeg75 (re-encode at q75 —
social-media transcode simulation), half (50% resize).

Metrics come from deepfake_lens.evaluation_metrics: AUROC, EER,
threshold@FPR, plus a coverage rate — crop_faces skips face-free samples,
and skipped-vs-scored is reported rather than silently treated as 0.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from deepfake_lens.evaluation_metrics import auroc, eer, threshold_at_fpr  # noqa: E402
from deepfake_lens.model_adapter import analyze_external_model  # noqa: E402
from experiments.sbi import DISTORTIONS, _apply_distortion, gaussian_blur  # noqa: E402

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def _face_boxes(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Detected face boxes via the package's detector; empty on failure."""
    from deepfake_lens.face import _detect_faces

    return [(r.x, r.y, r.width, r.height) for r in _detect_faces(image)]


def face_focused_sbi(image: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """SBI-style self-blend with the mask constrained to detected faces.

    A whole-image SBI mask usually lands on background — the crop_faces
    scorer then sees an untouched face and the 'fake' label is a lie. This
    variant draws the soft ellipse mask inside detected face boxes so the
    scored crop actually contains the manipulation, approximating a
    faceswap. Falls back to a whole-image mask when no face is found.
    """
    base_image = np.asarray(image, dtype=np.float64)
    height, width = base_image.shape[:2]
    base = _apply_distortion(DISTORTIONS[int(rng.integers(0, len(DISTORTIONS)))], base_image, rng)
    patch = _apply_distortion(DISTORTIONS[int(rng.integers(0, len(DISTORTIONS)))], base_image, rng)
    if base.shape[:2] != (height, width):
        from experiments.sbi import resize_bilinear

        base = resize_bilinear(base, height, width)
    if patch.shape[:2] != (height, width):
        from experiments.sbi import resize_bilinear

        patch = resize_bilinear(patch, height, width)

    yy, xx = np.mgrid[0:height, 0:width]
    mask = np.zeros((height, width), dtype=np.float64)
    boxes = _face_boxes(np.asarray(image, dtype=np.uint8))
    if not boxes:
        boxes = [(0, 0, width, height)]
    for bx, by, bw, bh in boxes:
        # 1-2 ellipses inside the face box, like SBI's random mask.
        for _ in range(int(rng.integers(1, 3))):
            cy = rng.uniform(by, by + bh)
            cx = rng.uniform(bx, bx + bw)
            ry = rng.uniform(bh * 0.3, bh * 0.9)
            rx = rng.uniform(bw * 0.3, bw * 0.9)
            dist = ((yy - cy) / max(1e-6, ry)) ** 2 + ((xx - cx) / max(1e-6, rx)) ** 2
            mask = np.maximum(mask, (dist <= 1.0).astype(np.float64))
    mask = gaussian_blur(mask, max(1.0, min(height, width) * 0.02))
    blended = base * (1 - mask[..., None]) + patch * mask[..., None]
    return np.clip(blended, 0, 255)


def _variants() -> dict[str, object]:
    import cv2

    def jpeg75(image):
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else image

    def half(image):
        h, w = image.shape[:2]
        return cv2.resize(image, (max(8, w // 2), max(8, h // 2)))

    return {"original": lambda image: image, "jpeg75": jpeg75, "half": half}


def _load_images(directory: Path) -> list[tuple[str, np.ndarray]]:
    import cv2

    out = []
    for path in sorted(directory.rglob("*")):
        if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
            continue
        image = cv2.imread(str(path))
        if image is None:
            continue
        out.append((str(path.relative_to(directory)), image))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--real-dir", type=Path, required=True, help="Directory of real face images (label 0)")
    parser.add_argument("--fake-dir", type=Path, help="Optional directory of known-fake faces (label 1)")
    parser.add_argument("--profile", type=Path, default=REPO_ROOT / "models/face-manipulation-vit-runtime.json")
    parser.add_argument("--variants", default="original,jpeg75,half", help="Comma list of: original,jpeg75,half")
    parser.add_argument("--sbi-per-real", type=int, default=1, help="SBI manipulations synthesized per real image")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--report", type=Path, default=REPO_ROOT / "experiments/face_eval_report.json")
    args = parser.parse_args()

    import cv2

    variant_fns = _variants()
    variant_names = [name.strip() for name in args.variants.split(",") if name.strip()]
    unknown = [name for name in variant_names if name not in variant_fns]
    if unknown:
        parser.error(f"unknown variants: {unknown} (known: {sorted(variant_fns)})")

    real = _load_images(args.real_dir)
    if not real:
        parser.error(f"no readable images under {args.real_dir}")
    fakes = _load_images(args.fake_dir) if args.fake_dir else []

    rng = np.random.default_rng(args.seed)
    # (name, image, label) — label 1 = manipulated/fake.
    samples: list[tuple[str, np.ndarray, int]] = []
    for name, image in real:
        samples.append((name, image, 0))
        for idx in range(max(1, args.sbi_per_real)):
            blended = face_focused_sbi(image, rng)
            samples.append((f"{name}#sbi{idx}", np.asarray(blended, dtype=np.uint8), 1))
    for name, image in fakes:
        samples.append((name, image, 1))

    rows: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="faceeval-") as tmp_dir:
        tmp = Path(tmp_dir)
        for sample_name, image, label in samples:
            for variant in variant_names:
                transformed = variant_fns[variant](image)
                path = tmp / f"s{len(rows)}.png"
                cv2.imwrite(str(path), transformed)
                result = analyze_external_model(path, args.profile)
                rows.append(
                    {
                        "sample": sample_name,
                        "variant": variant,
                        "label": label,
                        "available": bool(result and result.available),
                        "score": result.score if result and result.available else None,
                        "detail": result.detail if result else "no result",
                    }
                )

    def metrics(subset: list[dict[str, object]]) -> dict[str, object]:
        scored = [(float(r["score"]), int(r["label"])) for r in subset if r["available"]]
        skipped = len(subset) - len(scored)
        out: dict[str, object] = {
            "n": len(subset),
            "scored": len(scored),
            "skipped": skipped,
            "coverage": round(len(scored) / len(subset), 4) if subset else 0.0,
        }
        if scored:
            out["auroc"] = auroc(scored)
            out["eer"] = eer(scored)
            out["threshold_fpr1pct"] = threshold_at_fpr(scored, 0.01)
            out["threshold_fpr5pct"] = threshold_at_fpr(scored, 0.05)
            correct = sum(1 for score, label in scored if (score >= 50) == bool(label))
            out["accuracy_at_50"] = round(correct / len(scored), 4)
            out["fpr_at_50"] = round(sum(1 for s, l in scored if s >= 50 and l == 0) / max(1, sum(1 for _, l in scored if l == 0)), 4)
            out["recall_at_50"] = round(sum(1 for s, l in scored if s >= 50 and l == 1) / max(1, sum(1 for _, l in scored if l == 1)), 4)
        return out

    report = {
        "profile": str(args.profile),
        "real_dir": str(args.real_dir),
        "fake_dir": str(args.fake_dir) if args.fake_dir else None,
        "n_real_images": len(real),
        "n_fake_images": len(fakes),
        "sbi_per_real": args.sbi_per_real,
        "overall": metrics(rows),
        "by_variant": {name: metrics([r for r in rows if r["variant"] == name]) for name in variant_names},
        "by_label": {label: metrics([r for r in rows if r["label"] == label]) for label in (0, 1)},
        "rows": rows,
    }
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    overall = report["overall"]
    print(f"samples={overall['n']} scored={overall['scored']} skipped={overall['skipped']} coverage={overall['coverage']}")
    if "auroc" in overall:
        print(f"auroc={overall['auroc']} eer={overall['eer']} acc@50={overall['accuracy_at_50']} fpr@50={overall['fpr_at_50']} recall@50={overall['recall_at_50']}")
    for name, sub in report["by_variant"].items():
        line = f"  {name}: n={sub['n']} coverage={sub['coverage']}"
        if "auroc" in sub:
            line += f" auroc={sub['auroc']} recall@50={sub['recall_at_50']} fpr@50={sub['fpr_at_50']}"
        print(line)
    print(f"report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
