#!/usr/bin/env python3
"""Fetch diverse-domain real faces for face-manipulation training.

Downloads public-domain portrait photos from Wikimedia Commons (vintage,
sepia, scanned, daguerreotype, family photos), detects and crops the
largest face, and writes real/SBI-fake pairs split into train and
held-out validation directories.

Why: the FFHQ-only SBI checkpoint measured a cross-domain FPR of ~0.67
on old/damaged portraits. Adding 62 diverse real faces dropped it to
~0.07 (see experiments/FACESWAP_EVALUATION.md). This script reproduces
that dataset expansion.

Usage:
    python scripts/fetch_diverse_faces.py --out <dir>
        [--queries "sepia portrait" ...] [--max-downloads 150]
        [--val-every 4] [--seed 7]

Output layout (<out>):
    diverse_real/       real face crops for training
    diverse_fake/       self-blend fakes synthesized from the same crops
    diverse_val_real/   held-out real crops (every --val-every-th image)
    diverse_val_fake/   held-out SBI fakes

Requires: deepfake_lens.face (mediapipe/cv2 face detection) and
experiments/sbi.py on sys.path — run from the repo root.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "experiments"))

DEFAULT_QUERIES = [
    "portrait 19th century photograph",
    "daguerreotype portrait",
    "old family photograph 1920s",
    "sepia portrait",
    "passport photo vintage",
    "school class photo 1930s",
]
USER_AGENT = "deepfake-lens-eval/1.0 (contact: repo issues)"


def _commons_search(query: str, limit: int = 30) -> list[str]:
    api = (
        "https://commons.wikimedia.org/w/api.php?action=query&generator=search"
        f"&gsrsearch={urllib.parse.quote(query)}&gsrnamespace=6&gsrlimit={limit}"
        "&prop=imageinfo&iiprop=url&iiurlwidth=400&format=json"
    )
    req = urllib.request.Request(api, headers={"User-Agent": USER_AGENT})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read())
    urls = []
    for page in (data.get("query", {}).get("pages", {}) or {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("thumburl") or info.get("url")
        if url and url.lower().split("?")[0].endswith((".jpg", ".jpeg", ".png")):
            urls.append(url)
    return urls


def _largest_face_crop(image):
    """Crop around the largest detected face (+30% margin) or None."""
    import cv2
    import numpy as np

    from deepfake_lens.face import _detect_faces

    array = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    regions = _detect_faces(array)
    if not regions:
        return None
    region = max(regions, key=lambda r: r.width * r.height)
    margin = int(0.3 * max(region.width, region.height))
    crop = image.crop(
        (
            max(0, region.x - margin),
            max(0, region.y - margin),
            min(image.width, region.x + region.width + margin),
            min(image.height, region.y + region.height + margin),
        )
    )
    return crop if crop.width >= 60 else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True, help="output directory for diverse_* folders")
    parser.add_argument("--queries", nargs="*", default=DEFAULT_QUERIES)
    parser.add_argument("--max-downloads", type=int, default=150)
    parser.add_argument("--val-every", type=int, default=4, help="every Nth face goes to validation (default: 4)")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    try:
        import numpy as np
        from PIL import Image
    except ImportError as exc:
        print(f"error: missing dependency: {exc}", file=sys.stderr)
        return 2
    import io

    import sbi as sbi_module

    rng = np.random.default_rng(args.seed)
    for name in ("diverse_real", "diverse_fake", "diverse_val_real", "diverse_val_fake"):
        (args.out / name).mkdir(parents=True, exist_ok=True)

    urls: list[str] = []
    seen: set[str] = set()
    for query in args.queries:
        try:
            for url in _commons_search(query):
                if url not in seen:
                    seen.add(url)
                    urls.append(url)
        except Exception as exc:  # noqa: BLE001 - per-query network failures are non-fatal
            print(f"warn: search '{query}' failed: {exc}", file=sys.stderr)
    print(f"candidate urls: {len(urls)}")

    kept = 0
    for index, url in enumerate(urls[:args.max_downloads]):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            blob = urllib.request.urlopen(req, timeout=20).read()
            if len(blob) < 5000:
                continue
            image = Image.open(io.BytesIO(blob)).convert("RGB")
        except Exception:  # noqa: BLE001 - skip unreadable/undownloadable candidates
            continue
        crop = _largest_face_crop(image)
        if crop is None:
            continue
        array = np.asarray(crop, dtype=np.float64)
        blended, _ = sbi_module.self_blended_image(array, rng)
        blended_image = Image.fromarray(np.clip(blended, 0, 255).astype(np.uint8))
        prefix = "diverse_val" if index % max(1, args.val_every) == 0 else "diverse"
        crop.save(args.out / f"{prefix}_real/{kept:03d}.jpg", quality=92)
        blended_image.save(args.out / f"{prefix}_fake/{kept:03d}.jpg", quality=92)
        kept += 1
        if kept % 10 == 0:
            print(f"  kept {kept} face crops...", flush=True)

    print(f"done: {kept} face crops -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
