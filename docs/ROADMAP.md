# Deepfake Lens Roadmap

Status date: 2026-09-09 (full review pass complete). This file records the
scorecard, the completed work, and the prioritized remaining path.

## Scorecard

| Area | Score | Basis |
|---|---|---|
| Code quality & hygiene | 9/10 | stdlib-first with optional extras that fail safe; honest labels everywhere; no dead code found; 228 tests |
| Testing infrastructure | 8/10 | 228 unit tests, CLI smoke (36 subcommands), CI matrix 3.11/3.12/extras/android; deduct for no coverage tooling and no perf regression test |
| Reproducibility | 9/10 | new-clone verified end to end (install -> tests -> fixture regeneration -> scripts); SBI/ONNX export deterministic with seed |
| CI/CD | 7/10 | 4 jobs green + weekly link check; the C2PA SDK path is still not exercised in CI (blocked on workflow scope) |
| Documentation | 8/10 | CLI reference, dataset workflow, limits, verified registry, roadmap; deduct for scattered regeneration instructions (now consolidated) |
| **Detection effectiveness (metadata)** | **8/10** | real Synthbuster data: DALL-E 2 provenance caught at score 100 with correct attribution (mean 87.8); C2PA SDK validation honest |
| **Detection effectiveness (pixels)** | **2/10** | measured: ProGAN AUROC 0.43-0.48 (below chance); diffusion images without metadata score 10-18. This is the honest, known gap |
| Security posture | 9/10 | local-only, no network calls in scan/eval/train, localhost-bound servers, opt-in symlinks, bounded reads, redaction options |
| Release readiness | 6/10 | release checklist exists but version is still 0.1.0.dev0; no tagged release; no changelog; Android app is a module without a release artifact |

**Overall: 7.3/10** — as a *screening* tool it is honest, tested, and
reproducible; the pixel-detection gap (2/10) is measured, documented, and
must not be hidden: the tool finds what metadata reveals and admits what it
cannot see.

## Completed (chronological, newest first)

### 2026-09-09 final sweep (`2e9cd3c`, this commit)
- Multi-digit and zero-padded class-prefix folders (`07_real`, `10_fake`)
  now resolve labels; the first fix handled only single digits. Unit test
  added; ProGAN balanced-sample eval re-verified (AUROC consistency).

### 2026-09-08/09 external-data pass (`a0ecb92`, `2e9cd3c`)
- First real-data measurements recorded (see scorecard): ProGAN test set
  via HF mirror, Synthbuster 3-model sample. Metadata-first detection
  confirmed on real DALL-E 2 provenance; pixel gap quantified.
- `0_real`/`1_fake` benchmark label folders recognized (CNNDetection
  convention); empty non-recursive scans now print a `--recursive` hint.

### 2026-09-08 reproducibility & tooling (`83bdf11`, `e4158bd`, `65e176d`)
- `dev` extra (numpy/Pillow/c2pa-python) — one install enables every
  script and the SDK-gated tests.
- Robustness loop closed: `build_robustness_variants.py` generates all 8
  planned transforms; `eval --robustness` reports per-transform metrics.
- New-clone reproducibility verified from scratch.

### 2026-09-07 review pass (`a5b8d1a`, `9a150c1`, `c3c484f`)
- C2PA fixture restored (was lost to the blanket `*.png` ignore) with a
  deterministic regeneration script; `.gitignore` exception added; the 3
  SDK-gated tests now actually run when c2pa-python is present.
- Link checker: transient 5xx retries + HTTPError bot-block classification
  (CI runners get 403 from hosts that serve 200 locally).
- ONNX export guard (missing `onnx` package), synthetic dataset builder,
  dead ComfyUI doc link replaced.

### Earlier foundations
- 36-subcommand CLI, SBI training path, ConvNeXt trainer with
  best-checkpoint/early-stopping/cosine LR, ONNX export with
  TorchScript-parity verification (5.4e-07), Android ONNX Runtime path
  (weight-0 until a validated checkpoint), verified research registry.

## Remaining (priority order)

### P0 — unblock CI coverage of the C2PA SDK path
One-time action: run `gh auth refresh -h github.com -s workflow` in a
terminal and approve in the browser, then:
`git apply patches/0001-ci-c2pa-sdk-test-and-actions-bump.patch && git push`.
The patch (reviewed, `git apply --check` clean) also bumps the deprecated
action versions. Everything else in CI is already green.

### P1 — close the pixel-detection gap (the product's real frontier)
The measurements are done and conclusive; the fix is a trained detector:
1. Wire a pretrained detector as the first neural adapter — AIDE
   (github.com/shilinyan99/AIDE, checkpoints on Hugging Face) is the
   registry's designated first candidate; `--model-path` already accepts
   ONNX runtime profiles, so this is integration + validation work, not new
   plumbing.
2. Validate on the data already downloaded: ProGAN test set (8k images,
   local), Synthbuster (9k, local, 12.4 GB zip ready). Report clean and
   `--robustness` AUROC/EER per source. No accuracy claim without this.
3. Cross-dataset protocol (train-side sources vs held-out sources) per the
   VERIFIED_REGISTRY adoption process.
4. If AIDE holds up, re-run `train_detector.py --sbi` as the local fallback
   and only then consider moving the Android neural score off weight 0.
   Real comparators (RAISE-1k for Synthbuster) need a manual license-form
   request and must not be automated.

### P2 — release hygiene
- Tag `v0.1.0` once P0 lands (version is still `0.1.0.dev0`); add a
  CHANGELOG from the git history (the log messages are already
  release-note quality).
- Add coverage tooling and a perf regression assertion for scan throughput
  (the `perf` report exists; a pass/fail bound does not).
- Consider a `dev` CI job running the fixture-regeneration script on a
  clean runner to keep reproducibility from drifting.

### P3 — later / optional
- CLIDE-style zero-shot direction for unseen generators (registry
  research entry).
- Localization review of Korean verdict strings against the English docs
  terminology.

## Non-goals (stable)
- No cloud calls, no upload, no login — local-only by design.
- No final "this is fake" verdicts — screening signals with next checks.
- No checkpoint publication without dataset license, model license, and a
  calibration report (VERIFIED_REGISTRY adoption process, step 4).
