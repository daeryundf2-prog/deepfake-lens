"""Per-generator screen-rate benchmark on the Synthbuster dataset.

Downloads: fetch the ~11.8 GB archive from Zenodo
(https://zenodo.org/records/10066460, Synthbuster, Bammey 2023) into
public_datasets/synthbuster.zip and extract it so that
public_datasets/synthbuster/<generator>/*.png exists.

This scores --per-gen images per generator through the AIDE runtime profile
and reports the mean score plus the fraction above 50. Synthbuster contains
only generated images — there is no real class, so this measures per-source
screen rates, NOT accuracy/AUROC. Pair with a licensed real set (e.g.
RAISE-1k) under fixtures/modern-bench/ for a calibrated metric.

Usage:
    python experiments/eval_synthbuster.py [--per-gen 100] [--profile models/aide-runtime.json]
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepfake_lens.model_adapter import analyze_external_model

DATASET = Path("public_datasets/synthbuster")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-gen", type=int, default=100)
    ap.add_argument("--profile", default="models/aide-runtime.json")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--root", default=str(DATASET))
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"dataset not found at {root} — see module docstring for fetch steps")
        return 2

    gens = sorted(p for p in root.iterdir() if p.is_dir())
    print(f"profile={args.profile} per_gen={args.per_gen} generators={len(gens)}\n")
    print(f"{'generator':<22} {'n':>5} {'mean':>6} {'>50':>6}")
    for gen in gens:
        images = sorted(gen.glob("*.png"))
        random.Random(args.seed).shuffle(images)
        images = images[: args.per_gen]
        scores = []
        for i, img in enumerate(images, 1):
            a = analyze_external_model(img, args.profile, modality="image")
            if a.available and a.score is not None:
                scores.append(a.score)
            if i % 10 == 0:
                print(f"  {gen.name}: {i}/{len(images)}", flush=True)
        if not scores:
            print(f"{gen.name:<22} {0:>5} {'—':>6} {'—':>6}  (model unavailable)")
            continue
        mean = sum(scores) / len(scores)
        rate = sum(1 for s in scores if s > 50) / len(scores)
        print(f"{gen.name:<22} {len(scores):>5} {mean:>6.1f} {rate:>6.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
