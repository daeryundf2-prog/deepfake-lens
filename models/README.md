# Model zoo

Runtime profiles for published synthetic-media detectors (image, audio, and text).
Profiles are committed; **weights never are** (license + size —
`models/*.pth` and friends are gitignored). A scan treats every profile as
optional: missing checkpoints or missing optional dependencies degrade to
`available: false` entries with the fetch hint in `limitations`, never a
crash.

| Profile | Detector | Runtime | Status |
|---|---|---|---|
| `aide-runtime.json` | AIDE (ICLR 2025) `progan_train` | `aide` (torch reimplementation in `scripts/run_aide.py`) | wired — run `scripts/fetch_aide.py` |
| `univfd-runtime.json` | UnivFD (CVPR 2023) CLIP ViT-L/14 linear probe | `clip-linear` (torch + transformers) | wired — fetch `pretrained_weights/fc_weights.pth` from the [UnivFD repo](https://github.com/WisconsinAIVision/UniversalFakeDetect) to `models/univfd_fc.pth`; the CLIP backbone downloads from HF on first use (~1.7 GB) |
| `cnndetection-runtime.json` | CNNDetection (CVPR 2020) ResNet-50 blur+jpg | `torchvision` (torch + torchvision) | wired — download `blur_jpg_prob0.5.pth` via the CNNDetection repo's `weights/download_weights.sh` (Dropbox) to `models/` |
| `dire-runtime.json` | DIRE (ICCV 2023) diffusion reconstruction | — | `supported: false` placeholder (needs ADM diffusion pipeline) |
| `aasist-runtime.json` | AASIST (Interspeech 2022) audio anti-spoofing | `aasist` (torch reimplementation in `scripts/run_aasist.py`) | wired — run `scripts/fetch_aasist.py` |
| `openai-detector-runtime.json` | OpenAI GPT-2 output detector (RoBERTa-base) | `hf-text-classifier` (torch + transformers) | wired — fetched from HF hub on first use |
| `fakespot-detector-runtime.json` | Fakespot AI text detector (RoBERTa-base, modern-LLM training data) | `hf-text-classifier` (torch + transformers) | wired — fetched from HF hub on first use |
| `qwen-ppl-runtime.json` | Qwen2.5-0.5B reference-LM perplexity screen (generator-agnostic, multilingual incl. Korean) | `causal-lm-ppl` (torch + transformers) | wired — fetched from HF hub on first use (~1 GB); `hub_model` may point at a local snapshot for offline use |
| `aide-frames-runtime.json` | AIDE per-frame video screen | `video-frames` (cv2 + nested image profile) | wired — needs the AIDE checkpoint + opencv; **frame-level only, not temporal/lip-sync** |

Profiles declare a `modality` (`image`/`audio`/`text`/`video`); a scanned file
only runs profiles matching its own modality, so the image detectors, AASIST,
and the text classifiers never
trip over each other in a mixed directory scan.

## Multi-model runs

`--model-path` accepts a directory, a list, or a profile-set JSON
(`type: deepfake-lens-profile-set-v1` with a `profiles` array of paths
relative to the set file). With more than one profile every member runs and
the result reports:

- `models[]` — per-model `available`/`score`/`confidence`/`detail`
- aggregate `score` — the `ensemble_weight`-weighted mean of available
  member scores (each profile may declare `ensemble_weight`, default 1.0;
  measured-reliability weights, e.g. openai-detector 0.25 / fakespot 0.5 /
  qwen-ppl 1.0)
- agreement — `detail` reports the member score spread; disagreement
  (>20 points) drops confidence to `low` and adds a limitation

Example:

```bash
deepfake-lens scan folder/ --model-path models/   # every profile in models/
```

Default scans auto-discover the bundled verified engines —
`aide-runtime.json` for images, `aasist-runtime.json` for audio, and
`openai-detector-runtime.json` for text
(`--no-default-engine` opts out). The wider zoo stays opt-in because the
placeholder members add latency without scores until their checkpoints are
fetched.

## Other assets (not runtime profiles)

- `face_landmarker.task` — MediaPipe Tasks-API model for `face.py`'s
  measured-landmark path on tasks-only mediapipe builds (>=0.10.30 / 1.x).
  Fetch with `scripts/fetch_facelandmarker.py` or point
  `DEEPFAKE_LENS_FACE_LANDMARKER` at a local copy. Absent → the legacy
  FaceMesh extra or the labelled box-ratio estimate.

## Honesty notes

- Scores are **prioritization signals, not truth labels** — calibration
  against your own data (`deepfake-lens eval` / `calibrate`) is required
  before trusting thresholds.
- Each profile's `limitations` field is surfaced on every result it touches;
  read them before citing a score.
