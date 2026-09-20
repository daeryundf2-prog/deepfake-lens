"""Generate a local SD-Turbo fake-image corpus for detector training.

Rationale: the Hemg parquet self-training attempt (2026-09-20) collapsed —
labels were noisy and 560 images could not separate the classes (holdout
AUROC 0.615, FPR 1.0). This script generates a *known-provenance* fake
class locally with SD-Turbo (4-step turbo distilled SD 2.1) so labels are
exact by construction. Pair the output with licensed real photos as the
negative class, then evaluate on a *different* generator's outputs
(Hemg holdout, DALL-E) — the gate is cross-generator AUROC >= 0.7.

Requires: pip install diffusers accelerate  (torch already needed)
Model: stabilityai/sd-turbo (~1.3 GB, downloaded on first run)
Cost: ~20 s/image on CPU at 512x512x4 steps — 280 images ~= 95 min.

Usage:
    python experiments/gen_sdturbo_corpus.py --out DIR/fake [--count 280] [--start 0]
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

SUBJECTS = [
    "a street market with fruit stalls", "a mountain lake at sunrise",
    "a busy train station platform", "a bowl of ramen on a wooden table",
    "a golden retriever in a park", "an old brick building facade",
    "a desk with laptop and coffee", "a forest path in autumn",
    "a beach with umbrellas", "a bicycle leaning on a wall",
    "a plate of pancakes with berries", "a city skyline at night",
    "a cat sleeping on a sofa", "a rural road through fields",
    "a bookshelf in a library", "a hot air balloon over valleys",
    "a rainy window with city lights", "a farmer's field with tractor",
    "a bowl of salad on a counter", "a lighthouse on a rocky coast",
    "a classroom with desks", "a bakery display case",
    "a subway car interior", "a garden with tulips",
    "a snowy cabin in woods", "a fisherman on a pier",
    "a street musician with guitar", "a parking lot at dusk",
    "a kitchen with hanging pots", "a waterfall in a jungle",
    "a museum gallery hall", "a rooftop garden", "a highway overpass",
    "a carnival ferris wheel", "a vineyard in summer",
    "a snowy mountain trail", "a cafe interior with plants",
    "a dog park with several dogs", "a bridge over a river",
    "a farmer market flower stall",
]
STYLES = [
    "a photo of", "a photograph of", "an iphone photo of",
    "a dslr photo of", "a candid photo of", "a high quality photo of", "",
]
LIGHT = [
    "", " in soft light", " at golden hour", " under overcast sky",
    " in harsh midday sun", " at night",
]


def build_prompts(count: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    prompts = []
    while len(prompts) < count:
        p = f"{rng.choice(STYLES)} {rng.choice(SUBJECTS)}{rng.choice(LIGHT)}".strip()
        prompts.append(p)
    return prompts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--count", type=int, default=280)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--seed", type=int, default=20260920)
    ap.add_argument("--steps", type=int, default=4)
    args = ap.parse_args(argv)

    import torch  # noqa: F401 - imported for side effect ordering clarity
    from diffusers import AutoPipelineForText2Image

    args.out.mkdir(parents=True, exist_ok=True)
    prompts = build_prompts(args.count, args.seed)
    pipe = AutoPipelineForText2Image.from_pretrained(
        "stabilityai/sd-turbo", torch_dtype=torch.float32
    )
    pipe.set_progress_bar_config(disable=True)
    for i in range(args.start, min(args.count, len(prompts))):
        out = args.out / f"sd_{i:04d}.jpg"
        if out.exists():
            continue
        img = pipe(
            prompts[i],
            num_inference_steps=args.steps,
            guidance_scale=0.0,
            height=512,
            width=512,
        ).images[0]
        img.save(out, quality=95)
        if i % 10 == 0:
            print(f"{i}/{args.count}", flush=True)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
