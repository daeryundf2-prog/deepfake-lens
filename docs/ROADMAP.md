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
Everything measurable without real data is done. The remaining accuracy
claims require:
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
  runners have).
- Registry-links scheduled run is green locally; the weekly cron will
  confirm on its own now that transient 5xx retries exist.
- Consider a `dev` extra collecting the tooling dependencies
  (numpy, Pillow, c2pa-python) for contributor onboarding.

## Non-goals (stable)
- No cloud calls, no upload, no login — local-only by design.
- No final "this is fake" verdicts — screening signals with next checks.
- No checkpoint publication without dataset license, model license, and a
  calibration report (VERIFIED_REGISTRY adoption process, step 4).
