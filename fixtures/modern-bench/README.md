# modern-bench — modern-generator benchmark layout spec

A **specification** for an in-domain (≥224 px) evaluation set covering
contemporary generators. Unlike `fixtures/benchmark/` (committed synthetic
smoke fixtures), this directory is a layout + manifest spec: **no generated
media is committed** — licenses for Synthbuster, RAISE, and generator outputs
do not all permit redistribution. Populate it locally with
`scripts/fetch_benchmark.py` and a manifest built from
`manifest.template.json`.

## Why this exists

Measured results so far are ProGAN (128–256 px) and CIFAKE (32×32). Neither
answers the question that matters for a screening tool: *how does the score
behave on a 1024×1024 FLUX.1 or SD3.5 image, or on a real camera photo from
2024?* This spec fixes the layout so anyone who assembles the data gets
comparable `per_source` numbers from `deepfake-lens eval`.

## Layout

```
modern-bench/                     # dataset root (e.g. public_datasets/modern-bench)
├── 0_real/                       # camera/authentic images
│   ├── raise-1k/                 # RAISE-1k raw conversions
│   ├── synthbuster-real/         # Synthbuster's real split (Flickr originals)
│   └── <source>/                 # any other labeled camera source
├── 1_fake/                       # generated images, one folder per generator
│   ├── flux1-dev/
│   ├── flux1-schnell/
│   ├── sd35-large/
│   ├── sd3-medium/
│   ├── midjourney-v6/            # only if ToS allows local eval copies
│   ├── dalle3/
│   ├── firefly-v3/
│   └── wan21/                    # video model — use extracted frames
└── manifest.json                 # fetch_benchmark.py manifest (see template)
```

The subfolder under the label folder **is the generator/source name** —
`deepfake_lens.datasets._source_for` reads it as `record.source`, and
`deepfake_lens.evaluate` reports `per_source` AUROC/accuracy from it. Name
folders `<generator>-<version>` (e.g. `sd35-large`, not `sd`) so per-source
numbers stay attributable.

## Requirements per entry

- **Resolution**: shortest edge ≥ 224 px. The modern-detector failure mode we
  are measuring is at real-world resolution; 32×32 proxies are not allowed.
- **Labels**: only `0_real` / `1_fake` top-level folders (numeric prefix
  convention is stripped by the labeler). No `unknown` files.
- **Provenance**: every `1_fake/<generator>/` folder needs a `SOURCE.md`
  noting generator version, parameter notes (steps/guidance) if known,
  acquisition date, and license/terms. See `SOURCE.template.md`.
- **Balance target**: ≥200 files per generator and ≥400 real images total,
  so per-source AUROC is meaningful (±0.03–0.05 at typical prevalence).
- **Splits**: keep a single flat set and let
  `deepfake-lens dataset-splits` plan deterministic train/val/test — do not
  hand-pick.

## Populating

```sh
# 1. Copy manifest.template.json to manifest.json, fill in url + sha256 per file.
python scripts/fetch_benchmark.py --manifest manifest.json --dest public_datasets/modern-bench

# 2. Audit the assembled set.
deepfake-lens dataset public_datasets/modern-bench

# 3. Evaluate — per_source in the JSON output is the per-generator breakdown.
deepfake-lens eval public_datasets/modern-bench --model-path models/aide-runtime.json
```

`fetch_benchmark.py` verifies per-file sha256, refuses path traversal in
`dest`, and cross-checks the label-folder convention after download.

## Candidate sources (check terms before use)

| Source | Real/Fake | Notes |
|---|---|---|
| Synthbuster (zenodo.org/records/10066048) | both | 9 generators incl. Firefly, Midjourney-v5, SDXL; cited in `scripts/fetch_benchmark.py` |
| RAISE-1k | real | raw-camera benchmark; manual license form — do not automate |
| Self-generated FLUX.1/SD3.5 | fake | cleanest licensing; record params in SOURCE.md |
| Wan2.1 frames | fake | video model — extract frames, treat as image set |

## Honesty notes

- Per-source AUROC/EER are screening metrics on this set's distribution —
  they say nothing about generators not represented here.
- Mixed-provenance "real" folders (web-scraped photos of unknown origin)
  contaminate the negative class; prefer camera-verified sources.
