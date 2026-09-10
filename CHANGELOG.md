# Changelog

All notable changes to Deepfake Lens. Format: Keep a Changelog; this repo
has not tagged a release yet (version is `0.1.0.dev0`), so entries are
grouped by work pass rather than release.

## 2026-09 — detection-evidence pass

### Added
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
