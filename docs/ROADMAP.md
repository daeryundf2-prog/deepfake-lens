# Deepfake Lens Roadmap

Status date: 2026-09-11. Rewritten after the full external audit (all four
modalities vs the 2025-26 generation landscape) and the completed AIDE
integration. Every gap below was verified against the code, not assumed.

## Where we actually are (measured, not claimed)

| Modality | Screening capability | Measured evidence | Neural detection |
|---|---|---|---|
| Image (metadata/provenance) | strong | DALL-E 2 provenance caught at 100 w/ attribution; C2PA SDK validation honest (untrusted ≠ valid) | n/a — this IS the design |
| Image (pixels, heuristics) | weak, documented | ProGAN AUROC 0.43-0.48 (below chance) | — |
| Image (pixels, AIDE adapter) | **strong** | ProGAN cat 400 = 1.000, airplane = 1.000, **full Synthbuster sweep 720 (9 generators) = 0.951**; ONNX parity verified (4-decimal prob match) | **done** (`scripts/run_aide.py`) |
| Video | heuristic only | Haar-cascade 1-box rPPG; landmark "estimator" is box-ratio arithmetic (`face.py:185` — self-documented as NOT measured) | none |
| Audio | heuristic only | librosa thresholds (jitter/shimmer/formants); no vocoder bispectrum, no AASIST/RawNet | none |
| Text | heuristic only | bigram-entropy pseudo-perplexity; no LLM backbone, no Binoculars | none |
| Cross-modal | scalar average only | no lip-sync phase analysis, no semantic consistency check | none |

Overall scorecard: **8.0/10 for the image path**; video/audio/text remain
honest-but-heuristic (4-5/10 each as *screeners* — they say "참고용" and mean
it). The audit's P0-P3 findings map to real code and are folded in below.

## Priority order (top = next session's first task)

### P0 — Ship the CI workflow patch (2 minutes of user action)
`gh auth refresh -h github.com -s workflow` in a terminal, approve in the
browser, then `git apply patches/0001-... && git push`. Unblocks: C2PA SDK
tests in CI, action version bumps. (Blocked only on the user's browser;
verified three separate push routes all require the scope.)

### P1 — Make AIDE the default pixel engine (product-critical)
The adapter exists and measures AUROC 1.0, but it is a *script*, not the
product. Close the loop:
1. **Bundle path**: `deepfake-lens scan --model-path <profile>` already
   accepts ONNX runtime profiles; wire a `models/aide-runtime.json` profile
   + a documented "download checkpoint once" step (checkpoint is 3.3 GB and
   license-restricted — do NOT commit weights; provide a fetch script with
   checksum).
2. **Android**: export a quantized AIDE (fp16/int8) that fits mobile
   budgets, validate parity vs the fp32 torch path, then move the neural
   score off weight-0 (`AndroidFileAnalysis.kt:194`) per the app's own
   contract — only after a cross-generator report exists.
3. **Full-benchmark run**: the 720-image Synthbuster sweep is DONE
   (AUROC 0.951, report updated). Next: per-model breakdown
   (`eval_aide.py --per-source` flag), then the full 9k set vs RAISE-1k
   reals (manual license form — never automate). Publish per-model
   AUROC/EER in `experiments/AIDE_EVALUATION.md` with the
   VERIFIED_REGISTRY caveats.

### P2 — Replace fake heuristics with real measurements (audit finding)
The audit correctly flags where the codebase *simulates* capability:
1. **face.py `_estimate_landmarks`**: DONE — MediaPipe FaceMesh path added
   behind the `face_mediapipe` optional extra; the box-ratio fallback is
   kept but every `FaceRegion` now carries `landmarks_source`
   ("mediapipe-facemesh" vs "box-ratio-estimate") so downstream output can
   no longer pass estimates off as measured landmarks.
2. **rppg.py single-box CHROM**: upgrade to multi-ROI (forehead + both
   cheeks) phase-coherence à la FakeCatcher; report per-ROI agreement, not
   one pulse.
3. **text_advanced.py pseudo-perplexity**: bigram entropy is not
   perplexity. Either rename the signal honestly ("repetition entropy") or
   add a small local LLM for real PPL/Binoculars as an optional extra.
4. **multimodal.py**: scalar average admits to being a summary. Add an
   audio-visual lip-sync phase check (phoneme closure vs formant timing) as
   the first true cross-modal signal.

### P3 — Modern-benchmark coverage (audit P2)
Current data: ProGAN (2019 GAN), Synthbuster (2023 diffusion). Missing
2025-26 generators the audit names (Wan2.1, HunyuanVideo, FLUX.1, SD3.5,
F5-TTS/CosyVoice audio, DeepSeek-style text). Plan: a
`fixtures/modern-bench/` layout spec + eval commands users can point at
self-collected samples; no redistribution of generated content — document
per-set license provenance only.

### P4 — Release hygiene
- Tag `v0.1.0` (CHANGELOG.md exists; P0 patch is the last CI blocker).
- Coverage tooling (`coverage.py` over the unit suite) + a `perf` floor
  assertion in `scripts/cli_smoke_test.py`.
- Korean/English verdict-string review (audit notes mixed registers).

### Explicitly out of scope (unchanged)
- Cloud calls, uploads, logins — local-only.
- Final "this is fake" verdicts — screening + next checks only.
- Publishing model weights without license + calibration report
  (VERIFIED_REGISTRY adoption process step 4).
- Legal-evidence output beyond what exists (`legal-report` stays a
  summary with checksums; court-grade packaging is a separate product per
  the audit's P3 — noted, not planned here).

## Completed log (condensed)

- **2026-09-11**: full Synthbuster sweep (9 generators, 720 images,
  AUROC 0.951); AIDE ONNX export verified (external-data split at 3.57GB,
  probs identical to torch on real images); ROADMAP rewritten against the
  four-modality external audit (every audit claim verified in code first:
  fake-landmark estimator `face.py:185`, single-box rPPG, pseudo-
  perplexity, scalar multimodal average — all real, all now P2 items).
- **2026-09-09**: AIDE reimplementation (timm trunk after discovering a
  hand-rolled ConvNeXt diverges by 14.1), official-checkpoint parity, three
  AUROC-1.0 evaluations, evaluation report + scorecard 8.0, CHANGELOG.
- **2026-09-09**: multi-digit class-prefix label fix (`07_real`, `10_fake`).
- **2026-09-08**: real-data passes — ProGAN/Synthbuster measurements,
  benchmark-label fix, robustness-variant generator, C2PA fixture
  restoration + reproducible builder, dev extra, link-checker fixes.
- Earlier: 36-command CLI, SBI training, ONNX handoff, Android module,
  verified registry, CI, honest-labeling posture throughout.
