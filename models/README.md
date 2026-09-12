# Model zoo

Runtime profiles for published synthetic-media detectors (image and audio).
Profiles are committed; **weights never are** (license + size —
`models/*.pth` and friends are gitignored). A scan treats every profile as
optional: missing checkpoints or missing optional dependencies degrade to
`available: false` entries with the fetch hint in `limitations`, never a
crash.

| Profile | Detector | Runtime | Status |
|---|---|---|---|
| `aide-runtime.json` | AIDE (ICLR 2025) `progan_train` | `aide` (torch reimplementation in `scripts/run_aide.py`) | wired — run `scripts/fetch_aide.py` |
| `univfd-runtime.json` | UnivFD (CVPR 2023) CLIP ViT-L/14 linear probe | `clip-linear` (torch + transformers) | wired — drop the fc head at `models/univfd_fc.pth` |
| `cnndetection-runtime.json` | CNNDetection (CVPR 2020) ResNet-50 blur+jpg | `torchvision` (torch + torchvision) | wired — drop `blur_jpg_prob.pth` in `models/` |
| `dire-runtime.json` | DIRE (ICCV 2023) diffusion reconstruction | — | `supported: false` placeholder (needs ADM diffusion pipeline) |
| `aasist-runtime.json` | AASIST (Interspeech 2022) audio anti-spoofing | `aasist` (torch reimplementation in `scripts/run_aasist.py`) | wired — run `scripts/fetch_aasist.py` |

Profiles declare a `modality` (`image`/`audio`); a scanned file only runs
profiles matching its own modality, so the image detectors and AASIST never
trip over each other in a mixed directory scan.

## Multi-model runs

`--model-path` accepts a directory, a list, or a profile-set JSON
(`type: deepfake-lens-profile-set-v1` with a `profiles` array of paths
relative to the set file). With more than one profile every member runs and
the result reports:

- `models[]` — per-model `available`/`score`/`confidence`/`detail`
- aggregate `score` — the mean of available member scores
- agreement — `detail` reports the member score spread; disagreement
  (>20 points) drops confidence to `low` and adds a limitation

Example:

```bash
deepfake-lens scan folder/ --model-path models/   # every profile in models/
```

Default scans auto-discover the bundled verified engines —
`aide-runtime.json` for images and `aasist-runtime.json` for audio
(`--no-default-engine` opts out). The wider zoo stays opt-in because the
placeholder members add latency without scores until their checkpoints are
fetched.

## Honesty notes

- Scores are **prioritization signals, not truth labels** — calibration
  against your own data (`deepfake-lens eval` / `calibrate`) is required
  before trusting thresholds.
- Each profile's `limitations` field is surfaced on every result it touches;
  read them before citing a score.
