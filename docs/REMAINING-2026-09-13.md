# Deepfake Lens — Remaining Items (2026-09-13)

> Status after the full implementation push: AASIST audio runtime,
> modality-aware adapter, model zoo (AIDE/UnivFD/CNNDetection/DIRE
> profiles), isotonic calibration, examiner feedback loop, HMAC report
> signing, CIFAKE cross-domain measurement, Android ONNX inference on
> emulator. Everything below is blocked on external assets or decisions —
> no code item remains.

## Verified this cycle

| Item | Evidence |
|---|---|
| Full suite | 394 tests OK (14 skipped on absent optional deps) — CI green on `c91742b` |
| AIDE real inference | 3.3 GB `progan_train.pth` fetched + run; fake=0.57 on ai-like fixture, 0.08 on real-like |
| AASIST real inference | `aasist.pth` fetched + run; spoof=1.0 on a synthetic WAV; scan reports `external_model_active: 1` |
| UnivFD real inference | official `fc_weights.pth` + CLIP ViT-L/14 via HF; live scores produced |
| CNNDetection real inference | blur_jpg checkpoint loads; nested `model`/`net` wrappers unwrapped (`7af3087`) |
| transformers 5.x compat | `BaseModelOutputWithPooling` unwrapped for clip-linear (`b5f33f8`) |
| Android instrumented | emulator AVD `dfl_test` — `connectedDebugAndroidTest` BUILD SUCCESSFUL, real ONNX inference executed on-device |
| CIFAKE measured bound | 100-image labeled eval across 3 runtimes; AUROC 0.47–0.55 (near chance at 32×32) — recorded in `experiments/AIDE_EVALUATION.md` |
| fuzz/degradation | numpy-absent and checkpoint-absent paths verified graceful (`model_analysis.available=false`) |

## Remaining — external-blocked only

| # | Item | Blocker | Unblock condition |
|---|---|---|---|
| 1 | Calibrated AUROC at target resolution | only 32×32 and ProGAN-domain data measured so far | labeled dataset at ≥224px in-domain (Synthbuster full set + RAISE-1k per roadmap P1) |
| 2 | Lip-sync / video-temporal detector | no committed model | integrate a Wav2Lip-detector or temporal artifact model (new runtime + weights) |
| 3 | Text-modality neural detector | heuristic only | LLM-backbone detector profile (e.g. Binoculars-style perplexity model) |
| 4 | Android physical-device run | emulator only on this host | real device + committed ONNX asset (int8 AIDE or smaller) |
| 5 | `v0.1.0` tag + signed release | version still `0.1.0.dev0`; signing-key decision pending | tag + `RAPIDTRIAGE_SIGNING_KEY`-equivalent key management decision |
| 6 | Modern-generator benchmark coverage | 2025-26 generators (FLUX.1, SD3.5, Wan2.1…) not in fixtures | self-collected samples per roadmap P3 (`fixtures/modern-bench/` spec) |
| 7 | UnivFD full checkpoint family | only `fc_weights.pth` fetched | fetch remaining UnivFD heads if needed |

## Score trajectory

`7.0 → ~7.5` after this cycle (real-inference proof + measured bounds + audio
runtime + modality dispatch). The remaining ~1.5 requires items 1–3 —
labeled in-domain data and genuinely missing modality detectors — which are
external/model-integration work, not code gaps.
