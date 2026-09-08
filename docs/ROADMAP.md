# Deepfake Lens Roadmap

Status date: 2026-09-08. This file tracks what has been completed and what
remains, so the next session can pick up without re-deriving context.

## Completed

### Foundation (earlier phases)
- Metadata-first CLI scanner with 36 subcommands (`scan`, `eval`, `benchmark`,
  `fusion`, `calibrate`, `train`, `train-neural-plan`, `models`, `perf`,
  `security`, `release`, `web`, per-modality analyzers, ...).
- Honest-labeling posture throughout: no metadata = `출처 단서 없음`, never
  "human-made"; unverified signals are reference-level only.
- SBI training path (`experiments/sbi.py`, pure numpy), ConvNeXt/EfficientNet
  trainer with best-checkpoint selection + early stopping, ONNX export with
  TorchScript-vs-ONNX verification.
- Android `deepfakeclassifier` module: SAF folder scan, metadata + pixel
  heuristics, ONNX Runtime inference path (weight-0 signal until a validated
  checkpoint exists).
- Verified research registry (`docs/VERIFIED_REGISTRY.md`) + weekly link
  checker with bot-block tolerance.

### 2026-09 review pass (commits c3c484f, a5b8d1a, 65e176d)
- Repaired dead ComfyUI docs URL (404) and added 408/5xx retry to
  `scripts/check_registry_links.py` (Zenodo 504s were failing CI spuriously).
- `export_onnx.py` fails fast with a clear message when the `onnx` package is
  missing (torch's exporter needs it; easy to miss).
- `scripts/build_synthetic_dataset.py`: synthetic ai/real dataset generator
  for pipeline smoke tests.
- End-to-end verified: dataset → manifest → SBI training → ONNX export
  (max abs diff vs TorchScript 5.4e-07) → `models --profile-out` →
  `scan --model-path` integration → `fusion` calibration at target FPR.
- **C2PA fixture restored**: the blanket `*.png` ignore rule had kept
  `fixtures/c2pa-test/signed-c2pa.png` out of the repo, so the three
  SDK-gated tests failed on every fresh clone with c2pa-python installed.
  Added `!fixtures/**/*.png` exception and
  `scripts/build_c2pa_fixture.py` (deterministic regeneration: test CA +
  ES256 signer cert with digitalSignature/emailProtection extensions,
  PKCS#8 key, 32x32 PNG, official-SDK manifest signing; keys stay in a temp
  dir). Verified: 12/12 test_c2pa pass locally and the `forensic` CLI
  reports the untrusted-signer wording.
- **Robustness loop closed**: `scripts/build_robustness_variants.py`
  generates all eight planned transforms (jpeg_q95/q75, resize_75/50,
  center_crop_90, gaussian_blur_light, screenshot, social_recompress)
  under transform-named folders; `eval --robustness` reports per-transform
  metrics. Verified end to end on the synthetic dataset (clean AUROC 0.97
  → 0.5-0.75 degraded, which is the drop the report exists to measure).
- **Weekly link checker fixed for CI**: urllib raises `HTTPError` for
  400+ statuses instead of returning a response, so the bot-block tolerance
  never fired on CI runners (GitHub IPs get 403 from openai.com while
  residential IPs get 200). HTTPError is now classified through the same
  retry/bot-block rules. Verified via a manual workflow dispatch: all five
  CI jobs green, including registry-links.
- `dev` extra added to pyproject (numpy, Pillow, c2pa-python) for
  contributor onboarding: `pip install -e '.[dev]'` enables the
  synthetic-dataset, robustness-variant, and C2PA-fixture scripts plus the
  SDK-gated tests.

### 2026-09-08 external-data pass
- **Label rules now understand benchmark class prefixes**: CNNDetection and
  its derivatives label folders `0_real`/`1_fake`; discovery previously
  exact-matched only `real`/`fake`/... and reported every record in such
  datasets as `unknown`. `_strip_class_prefix` drops the numeric prefix so
  `1_fake` maps to the positive label (unit test added).
- **Empty-scan hint**: non-recursive `scan` over a folder whose direct
  children are subdirectories now prints a `--recursive` hint instead of a
  bare "Scanned 0 files".
- **First real-data measurement (ProGAN test set)**: CNNDetection's
  progan_testset (HF mirror `sywang/CNNDetection`, 8,000 images, 4 classes
  × 200 ProGAN fakes + 200 reals), evaluated at 400 images (cat class):
  `pixel off` AUROC 0.50, `fast` 0.479, `deep` 0.476 — the pixel expert
  ensemble does NOT beat chance on ProGAN. This is the honest baseline the
  Limits section warns about: heuristics detect metadata and heavy
  manipulation traces, not GAN textures; a trained detector (P1/AIDE) is the
  known gap. Full-dataset runs and the Synthbuster download (12.4 GB, in
  progress) are the natural follow-ups.
- **Synthbuster first pass** (40 images per model, dalle2/glide/sdxl): the
  metadata-first design is confirmed on real data — DALL-E 2 images carry
  real OpenAI provenance metadata and score 100 with `DALL-E/OpenAI 추정`
  attribution (mean 87.8); glide 17.7 and sdxl 9.8 show that diffusion images
  without metadata land in low-signal territory, consistent with the
  `출처 단서 없음` posture. Detection strength tracks metadata presence
  exactly as documented, not pixel "magic".

## Pending (in priority order)

### P0 — CI workflow update (blocked on token scope, patch ready)
The current CI never exercises the C2PA SDK tests (they skip without
c2pa-python) and runs deprecated action versions. The full change is saved
as `patches/0001-ci-c2pa-sdk-test-and-actions-bump.patch`.

Applying it requires a git credential with the `workflow` scope; the repo
token currently has only `repo`/`gist`/`read:org` (verified: a normal-file
commit via API succeeds, workflow-file updates are rejected). To apply:

```sh
git apply patches/0001-ci-c2pa-sdk-test-and-actions-bump.patch
git checkout -b ci/c2pa-sdk-test && git commit -am "ci: apply patch"
# push with a workflow-scoped token, or run:
gh auth refresh -h github.com -s workflow   # then push
```

### P1 — Real-data evaluation (the actual product blocker)
First real measurement done (ProGAN 400-image sample: pixel AUROC ~0.48,
see the completed section — heuristics do not beat chance on GAN textures).
The remaining accuracy work:
1. A labeled real-world dataset (e.g. GenImage, Synthbuster — links in
   `docs/deepfake-lightweight-tool-research.md`) with per-source folders.
2. `dataset` manifest + audit, then `eval --pixel deep` for clean AUC/EER,
   and the robustness loop (P0's script) for transform degradation.
3. Cross-dataset evaluation (train on one source mix, eval on another) —
   the only honest generalization claim per VERIFIED_REGISTRY rules.
4. Only after that: consider wiring a pretrained detector (AIDE is the
   registry's first candidate) or training a local checkpoint for the
   Android app. Until then the app keeps the neural score at weight 0.

### P2 — Nice-to-have
- `python-extras` CI job could also run the C2PA fixture regeneration
  script to prove reproducibility on a clean machine (needs openssl, which
  runners have). Blocked on the same workflow-scope credential as P0.
- Registry-links scheduled run is green via manual dispatch (including the
  403 bot-block fix); the weekly cron confirms it autonomously from here.

## Non-goals (stable)
- No cloud calls, no upload, no login — local-only by design.
- No final "this is fake" verdicts — screening signals with next checks.
- No checkpoint publication without dataset license, model license, and a
  calibration report (VERIFIED_REGISTRY adoption process, step 4).
