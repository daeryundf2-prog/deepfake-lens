# Changelog

All notable changes to Deepfake Lens. Format: Keep a Changelog; this repo
has not tagged a release yet (version is `0.1.0.dev0`), so entries are
grouped by work pass rather than release.

## 2026-09 — AASIST audio runtime + modality-aware adapter

### Added
- `models/aasist-runtime.json` — committed runtime profile for AASIST
  (Interspeech 2022, the standard audio anti-spoofing baseline): 16 kHz
  mono, 64 600-sample window, spoof-probability score semantics; weights
  stay out of git.
- `scripts/run_aasist.py` — faithful minimal reimplementation (sinc
  convolution frontend, residual blocks, spectro-temporal graph attention
  + pooling) with a stdlib WAV loader; loads the official `AASIST.pth`
  strict state-dict.
- `scripts/fetch_aasist.py` — checkpoint fetcher (direct raw-GitHub URL,
  ~1.3 MB) with sha256 verification, license notice, and `--force` guard.
- `model_adapter` modality filtering: profiles declare `modality`
  (`image`/`audio`) and only run on matching files; the `aasist` runtime
  dispatches to `scripts/run_aasist.py`. Profiles that exist but do not
  match the file's modality are skipped silently (no `model_analysis`
  entry), same as having no profile.
- Audio scan plumbing: `analyze_audio` accepts `model_path`, scan items of
  `kind: audio` carry `model_analysis`, and `summary.external_model_active`
  counts them identically to images. `scan`/`audio`/`api-serve`
  auto-discover `models/aasist-runtime.json`.
- Registry entry `aasist-2022`; docs updated (`models/README.md`, JSON
  contract, CLI reference).

## 2026-09 — AIDE default-engine plumbing

### Added
- `models/aide-runtime.json` — committed runtime profile for the AIDE
  (ICLR 2025) `progan_train` checkpoint; weights stay out of git.
- `scripts/fetch_aide.py` — checkpoint fetcher with sha256 verification,
  Google Drive interstitial handling, license notice, and `--force`
  overwrite guard.
- `model_adapter` runtime `"aide"`: lazily loads `scripts/run_aide.py`
  (optional torch/timm stack), caches the model across files, and returns
  `available=False` with a reason when the checkpoint or dependencies are
  missing. Runtime profiles may now carry a `limitations` list that merges
  into results.
- CLI auto-discovery: `scan`/`eval`/`fusion` use `models/aide-runtime.json`
  as the default `--model-path` when present; `--no-default-engine` opts out.
- `summary.external_model_active` count in scan JSON so the external-engine
  signal stays distinct from the pixel ensemble.

## 2026-09 — detection-evidence pass

### Added
- **Full Synthbuster sweep**: 720 images (9 generators x 40 vs 360 camera
  reals) — AUROC 0.951 at 5% target FPR with the ProGAN-trained AIDE
  checkpoint; the strongest cross-generator evidence to date
  (`experiments/AIDE_EVALUATION.md`).
- **AIDE ONNX export path** (`run_aide.py --export-onnx`): fused graph
  exported with external-data weight split (3.57 GB); ONNX runtime
  reproduces torch probabilities to 4 decimals on real images — the bridge
  to `--model-path` runtime profiles and a future mobile build.
- AIDE (ICLR 2025) pretrained-detector adapter, verified bit-exact against
  the official code:
  - `scripts/run_aide.py` — faithful reimplementation (DCT band-selection,
    SRM-HPF ResNets, timm ConvNeXt-XXL trunk with AIDE head surgery), plus
    single-image inference and ONNX export scaffolding.
  - `scripts/eval_aide.py` — labeled-folder evaluation (accuracy, AUROC,
    EER, threshold at target FPR) on top of the shared discovery rules.
  - `experiments/aide_srm_kernels.py` — AIDE's 30 SRM HPF kernels vendored
    verbatim.
  - `experiments/AIDE_EVALUATION.md` — measured results: ProGAN cat 400
    AUROC 1.000, unseen airplane category 1.000, Synthbuster cross-domain
    (dalle2/glide) 1.000 with the official progan_train.pth checkpoint.
- `scripts/build_robustness_variants.py` — generates all eight planned
  robustness transforms (JPEG q95/q75, resize, crop, blur, screenshot,
  social recompression) so `eval --robustness` works end to end.
- `scripts/build_synthetic_dataset.py` — synthetic ai/real dataset builder
  for pipeline smoke tests.
- `scripts/build_c2pa_fixture.py` — deterministic regeneration of the C2PA
  test fixture (test CA + ES256 signer, SDK-signed manifest).
- `docs/ROADMAP.md` — scorecard (overall 8.0/10), completed-work log, and
  the prioritized remaining path.
- `dev` extra (numpy, Pillow, c2pa-python) for contributor onboarding.
- Patches directory with `0001-ci-c2pa-sdk-test-and-actions-bump.patch`
  (CI update preserved for a workflow-scoped credential).

### Fixed
- Dataset discovery now understands benchmark class-prefix folders:
  `0_real`/`1_fake` and multi-digit/zero-padded variants (`07_real`,
  `10_fake`) instead of reporting every record as unknown.
- Non-recursive `scan` over a folder whose direct children are
  subdirectories prints a `--recursive` hint instead of a bare
  "Scanned 0 files".
- C2PA fixture was missing from every fresh clone (blanket `*.png` ignore
  rule); restored and excepted in `.gitignore`.
- Link checker: retries transient 5xx and classifies HTTPError 403/429 as
  bot-blocked — CI runners get 403 from hosts that serve 200 locally.
- Dead ComfyUI documentation URL replaced with the current one.
- `experiments/export_onnx.py` fails fast when the `onnx` package is
  missing instead of a confusing torch exporter traceback.

### Measured (detection honesty)
- Heuristic pixel ensemble: ProGAN AUROC 0.43-0.48 (below chance) — the
  documented Limits warning, now with numbers.
- Metadata-first screening on real data: DALL-E 2 provenance images score
  100 with correct tool attribution (Synthbuster); metadata-free diffusion
  images land in low-signal territory as documented.
- Pretrained AIDE closes the pixel gap on all measured sets (AUROC 1.000);
  see `experiments/AIDE_EVALUATION.md` for caveats.

## Earlier passes (pre-changelog)

See `git log`: v2.0 expansion cleanups (fabricated-metric removal, server
hardening, honest-labeling fixes), Phase 2 real frequency forensics + CHROM
rPPG + PRNU + SBI training, C2PA SDK validation path, Android ONNX Runtime
module, CI workflow, and the initial repository split from mobile-forensics.
