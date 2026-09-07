#!/usr/bin/env python3
"""Generate the robustness-transform variants for a labeled dataset.

`deepfake_lens/datasets.py` writes a robustness PLAN (a no-dependency
manifest of variants to generate "with an external image tool"); this script
is that tool. It reads the plan (or the dataset folder directly) and emits
transformed copies under folders named exactly as the plan's transform keys
(`jpeg_q95`, `resize_75`, ...) so `eval --robustness` can measure the score
drop per transform.

Transforms:
  jpeg_q95 / jpeg_q75   real JPEG re-encode (Pillow) at quality 95 / 75
  resize_75 / resize_50 bilinear downscale to 75% / 50% (then re-save PNG)
  center_crop_90        central 90% crop
  gaussian_blur_light   sigma = 1% of the short side
  screenshot            50% downscale + Q75 JPEG + up-scale back (mimics a
                        platform screenshot's resolution loss + re-encode)
  social_recompress    two successive Q75 JPEG rounds (mimics social-media
                        upload/recompress chains that strip signals)

Pure numpy for the geometry (experiments/sbi.py's bilinear resize is
reused), Pillow only for image IO and JPEG encoding. Both stay optional
dependencies of the package; this script requires them explicitly.

Usage:
    python scripts/build_robustness_variants.py --root data/raw --out data/robust
    python scripts/build_robustness_variants.py --plan robustness-plan.json --out data/robust
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from deepfake_lens.datasets import ROBUSTNESS_TRANSFORMS  # noqa: E402

try:
    import numpy as np
    from PIL import Image
except ImportError as exc:
    print(f"error: this script needs numpy and Pillow: {exc}", file=sys.stderr)
    raise SystemExit(2) from exc

from experiments.sbi import resize_bilinear  # noqa: E402


def _load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.float64)


def _save_png(array: np.ndarray, path: Path) -> None:
    Image.fromarray(np.clip(array, 0, 255).astype("uint8")).save(path, format="PNG")


def _save_jpeg(array: np.ndarray, path: Path, quality: int) -> None:
    Image.fromarray(np.clip(array, 0, 255).astype("uint8")).save(path, format="JPEG", quality=quality)


def apply_transform(name: str, image: np.ndarray) -> tuple[np.ndarray, str]:
    """Return (transformed array, output suffix). Suffix is .png or .jpg."""
    height, width = image.shape[:2]
    if name == "jpeg_q95":
        return image, ".jpg"
    if name == "jpeg_q75":
        return image, ".jpg"
    if name == "resize_75":
        return resize_bilinear(image, max(4, int(height * 0.75)), max(4, int(width * 0.75))), ".png"
    if name == "resize_50":
        return resize_bilinear(image, max(4, height // 2), max(4, width // 2)), ".png"
    if name == "center_crop_90":
        crop_h, crop_w = max(4, int(height * 0.9)), max(4, int(width * 0.9))
        y0, x0 = (height - crop_h) // 2, (width - crop_w) // 2
        return image[y0 : y0 + crop_h, x0 : x0 + crop_w], ".png"
    if name == "gaussian_blur_light":
        from experiments.sbi import gaussian_blur

        return gaussian_blur(image, max(0.75, min(height, width) * 0.01)), ".png"
    if name == "screenshot":
        small = resize_bilinear(image, max(4, height // 2), max(4, width // 2))
        # screenshot: platform re-encode at Q75, then the viewer sees the
        # lower-resolution version
        return small, ".jpg"
    if name == "social_recompress":
        # two successive Q75 JPEG rounds: upload -> CDN -> download chain
        return image, ".jpg"
    raise ValueError(f"unknown transform: {name}")


JPEG_QUALITY = {"jpeg_q95": 95, "jpeg_q75": 75, "screenshot": 75, "social_recompress": 75}


def save_variant(name: str, image: np.ndarray, target: Path) -> None:
    suffix = ".jpg" if name in JPEG_QUALITY else ".png"
    if name == "social_recompress":
        buffer = Path(str(target) + ".tmp.jpg")
        _save_jpeg(image, buffer, 75)
        _save_jpeg(_load_rgb(buffer), target, 75)
        buffer.unlink(missing_ok=True)
        return
    if suffix == ".jpg":
        _save_jpeg(image, target, JPEG_QUALITY[name])
    else:
        _save_png(image, target)


def records_from_plan(plan_path: Path) -> list[dict[str, str]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    return [
        {"path": str(item["path"]), "label": str(item["label"])}
        for item in plan.get("items", [])
        if isinstance(item, dict) and item.get("path") and item.get("label") != "unknown"
    ]


def records_from_folder(root: Path) -> list[dict[str, str]]:
    from deepfake_lens.datasets import discover_dataset

    _, records = discover_dataset(root)
    return [
        {"path": record.path, "label": record.label}
        for record in records
        if record.label != "unknown"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate robustness-transform variants of a labeled dataset.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--root", type=Path, help="labeled dataset folder (ai/real paths)")
    source.add_argument("--plan", type=Path, help="robustness-plan.json from `dataset --robustness-out`")
    parser.add_argument("--out", type=Path, required=True, help="output root for transformed variants")
    parser.add_argument(
        "--transforms",
        default=",".join(ROBUSTNESS_TRANSFORMS),
        help=f"comma-separated transforms (default: all of {ROBUSTNESS_TRANSFORMS})",
    )
    args = parser.parse_args(argv)

    transforms = [name.strip() for name in args.transforms.split(",") if name.strip()]
    for name in transforms:
        if name not in ROBUSTNESS_TRANSFORMS:
            print(f"error: unknown transform '{name}' (known: {', '.join(ROBUSTNESS_TRANSFORMS)})", file=sys.stderr)
            return 2

    records = records_from_plan(args.plan) if args.plan else records_from_folder(args.root)
    if not records:
        print("error: no labeled records found (need ai/real folder labels)", file=sys.stderr)
        return 2

    written = 0
    skipped = 0
    for record in records:
        source_path = Path(record["path"])
        label = record["label"]
        if not source_path.exists():
            skipped += 1
            continue
        try:
            image = _load_rgb(source_path)
        except Exception as exc:  # noqa: BLE001 - a corrupt file must not stop the run
            print(f"warning: could not read {source_path}: {exc}", file=sys.stderr)
            skipped += 1
            continue
        for name in transforms:
            try:
                variant, _suffix = apply_transform(name, image)
                target = args.out / name / source_path.parent.name / f"{source_path.stem}{'.jpg' if name in JPEG_QUALITY else '.png'}"
                target.parent.mkdir(parents=True, exist_ok=True)
                save_variant(name, variant, target)
                written += 1
            except Exception as exc:  # noqa: BLE001 - keep going, report at the end
                print(f"warning: {name} failed on {source_path}: {exc}", file=sys.stderr)
                skipped += 1

    print(
        json.dumps(
            {
                "records": len(records),
                "written": written,
                "skipped": skipped,
                "transforms": transforms,
                "out": str(args.out),
                "next": f"python -m deepfake_lens eval {args.out} --pixel deep --robustness",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
