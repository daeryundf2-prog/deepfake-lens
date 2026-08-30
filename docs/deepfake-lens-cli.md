# Deepfake Lens CLI

`deepfake-lens` is the first local scanning engine for the AI-generated material checker.

It is intentionally CLI-first:

- Faster to validate than a browser UI.
- Better for large folders.
- Reusable later from a local web app or localhost API.
- No upload, login, cloud AI API, or new runtime dependency.

## Usage

```sh
python -m deepfake_lens scan /path/to/folder
python -m deepfake_lens scan /path/to/folder --include-low
python -m deepfake_lens scan /path/to/folder --recursive --max-files 5000
python -m deepfake_lens scan /path/to/folder --json-out report.json --csv-out report.csv
python -m deepfake_lens scan /path/to/folder --pixel fast
python -m deepfake_lens scan /path/to/folder --pixel deep --heatmaps --heatmap-dir heatmaps
python -m deepfake_lens scan /path/to/folder --pixel deep --cache .cache/deepfake-lens.json --workers 4 --html-out report.html --pdf-out report.pdf
python -m deepfake_lens scan /path/to/folder --recursive --dedupe --hash-db .cache/hashes.json --max-file-bytes 25000000
python -m deepfake_lens collect /path/to/dataset-root --out collection-plan.json
python -m deepfake_lens dataset /path/to/dataset --manifest-out dataset-manifest.json --fingerprints --audit-out audit.json --split-out split.json --robustness-out robustness.json
python -m deepfake_lens eval /path/to/dataset --pixel deep --json-out eval.json --html-out benchmark.html --false-positive-out fp.jsonl --false-negative-out fn.jsonl
python -m deepfake_lens eval /path/to/dataset --pixel deep --robustness --json-out robustness-eval.json
python -m deepfake_lens benchmark /path/to/dataset --pixel-modes off,deep --model-path aide-profile.json --json-out matrix.json --md-out matrix.md
python -m deepfake_lens fusion /path/to/dataset --pixel deep --model-path aide-profile.json --target-fpr 0.05 --out fusion-profile.json
python -m deepfake_lens scan /path/to/folder --fusion-profile fusion-profile.json
python -m deepfake_lens benchmark /path/to/dataset --pixel-modes off,fast,deep --fusion-profile fusion-profile.json --json-out matrix.json
python -m deepfake_lens calibrate /path/to/dataset --pixel deep --out calibration.json
python -m deepfake_lens train /path/to/dataset --pixel deep --out portable-model.json
python -m deepfake_lens train-neural-plan /path/to/dataset --out neural-plan.json --output-dir experiments/run-001
python experiments/train_detector.py --manifest dataset-manifest.json --arch convnext_tiny --epochs 10 --out experiments/run-001
python experiments/export_onnx.py --checkpoint experiments/run-001/convnext_tiny.torchscript --out experiments/run-001/convnext_tiny.onnx
python -m deepfake_lens models --json-out detector-registry.json
python -m deepfake_lens models --candidate aide-iclr-2025 --checkpoint models/aide.onnx --profile-out aide-profile.json
python -m deepfake_lens video /path/to/videos --out video-plan.json --frame-root extracted-frames
python -m deepfake_lens perf /path/to/folder --pixel deep --workers 4 --cache .cache/scan.json --hash-db .cache/hashes.json --out perf.json
python -m deepfake_lens security --out security-check.json
python -m deepfake_lens release --out release-check.json
python -m deepfake_lens web --folder /path/to/folder
```

If installed from the package, the entry point is:

```sh
deepfake-lens scan /path/to/folder
```

## Supported Files

- `.png`
- `.jpg`
- `.jpeg`
- `.webp`
- `.txt`
- `.md`

## Detection Strategy

For large-folder speed, the CLI is metadata-first:

- Reads only a bounded prefix of image files by default.
- Extracts PNG `tEXt`, `iTXt`, and `zTXt` chunks.
- Searches image headers for explicit generation metadata.
- Reads only a bounded prefix of text files.
- Sorts high/medium/unknown candidates before low-signal and unsupported files.

Pixel-level analysis is opt-in:

- `--pixel off` keeps the default metadata-first scanner.
- `--pixel fast` adds local pixel, spectral/statistical, reconstruction, retrieval, compositing, fuzzy-fusion, and external-baseline adapter experts.
- `--pixel deep` also adds SAFE-style local manipulation/localization.
- `--heatmaps` with `--pixel deep` writes small PNG heatmaps for localization review.
- PNG pixels are decoded with the built-in reader. JPEG/WebP pixel analysis works when Pillow is available, without making Pillow a required dependency.
- Ivy-xDetector can be used as an external baseline by placing a sidecar next to the image, for example `image.png.ivy.json` or `image.ivy.json`, with `score`, `fake_score`, `probability`, or `label`.
- `--model-path` can point to a JSON score profile, a sidecar profile, or an optional ONNX/TorchScript runtime profile. ONNX Runtime, PyTorch, Pillow, and NumPy remain optional local dependencies rather than mandatory install requirements.

The recent-research layer is represented in the JSON report as named experts:

1. `difference_in_difference_reconstruction`
2. `spark_il_spectral_retrieval`
3. `low_correlation_fractal_signal`
4. `alpha_blending_compositing`
5. `safe_pixel_localization`
6. `vrag_dfd_local_retrieval`
7. `reveal_evidence_chain`
8. `agentfox_explainable_summary`
9. `fuzzy_decision_tree_fusion`
10. `ivy_xdetector_adapter`

## Source Guessing

High-confidence source guesses require explicit metadata or known workflow fields:

- Stable Diffusion / A1111
- ComfyUI
- Midjourney / Niji
- DALL-E / OpenAI
- Adobe Firefly
- Runway
- Leonardo.ai
- NovelAI

Text source guesses are only made when the text directly names a tool or contains assistant self-reference.

## Implementation Map

The next-stage plan is implemented as local-first commands and adapters:

1. Dataset preparation: `collect` writes a real/AI source collection plan; `dataset` discovers `ai`, `edited`, and `real` folder labels, writes a manifest, can add SHA-256 fingerprints, writes an audit, plans deterministic splits, and emits a robustness transform plan.
2. Evaluation runner: `eval` reports threshold, accuracy, precision, recall, false-positive rate, AUROC, confusion counts, false-positive/false-negative case files, benchmark HTML, source-attribution coverage, and per-source metrics. `benchmark` compares multiple pixel modes and model profiles in one matrix.
3. Calibration and fusion: `calibrate` writes a versioned threshold profile targeting a requested false-positive rate. `fusion` calibrates a local metadata/pixel/external-model/source score profile, and `scan`, `eval`, and `benchmark` accept `--fusion-profile`.
4. Pretrained detector adapter: `--model-path` accepts JSON score profiles, sidecar profiles, direct `.onnx`/`.pt`/`.pth`/`.torchscript` paths, and JSON runtime profiles for optional ONNX/TorchScript inference. `models --profile-out` scaffolds candidate profiles such as AIDE.
5. Training baseline: `train` creates a portable threshold model from local scores until verified neural checkpoints are available. `train-neural-plan` writes the ConvNeXt/ONNX training handoff plan, and optional scripts in `experiments/` run a real PyTorch image experiment when `torch`, `torchvision`, and `Pillow` are installed.
6. Patch/localization: `--pixel deep --heatmaps` writes SAFE-style PNG localization heatmaps; HTML reports embed small heatmap previews.
7. Source attribution: JSON separates `ai_score`, `source_guess`, and `source_attribution_label`. Metadata rules now include Flux, Ideogram, Imagen/Gemini, Recraft, Canva AI, and Grok/xAI in addition to earlier sources.
8. Large scans and performance: `--cache`, `--workers`, `--dedupe`, `--hash-db`, `--max-file-bytes`, and `--progress` support resumable parallel scans with duplicate and oversize handling. `perf` writes a throughput/cache/duplicate-rate report for local tuning.
9. Local web app: `web` starts a localhost-only UI/API with escaped table rendering, optional recursive/dedupe scans, model/fusion profile fields, and heatmap preview serving constrained to the scanned folder.
10. Reports: `--html-out` and `--pdf-out` write review artifacts with optional `--redact-paths`; HTML reports embed heatmaps when available. The simple PDF is Latin-1 only — when Korean text is present it prints an explicit notice recommending the HTML report.
11. Evaluation output includes AUROC and EER (threshold-swept equal error rate) alongside confusion counts, and `eval` reports per-split metrics when the dataset declares splits.
12. Security/privacy: `security` writes a local-only guardrail report. Network calls are not used by scan/eval/train, symlink following is opt-in, oversize files can be skipped, report paths can be redacted, and the web server binds to localhost unless `--allow-lan` is passed.
13. Release prep: `release` writes a readiness checklist and `.github/workflows/deepfake-lens.yml` runs compile, unit tests, registry smoke, and CLI help checks.

## Dataset And Benchmark Workflow

Folder labels are inferred from path segments:

- Positive: `ai`, `fake`, `synthetic`, `generated`, `edited`, `deepfake`
- Negative: `real`, `human`, `camera`, `authentic`, `original`
- Splits: `train`, `val`, `valid`, `validation`, `test`

Recommended sequence:

```sh
python -m deepfake_lens collect data/raw --out artifacts/collection-plan.json
python -m deepfake_lens dataset data/raw --manifest-out artifacts/manifest.json --fingerprints --audit-out artifacts/audit.json --split-out artifacts/split.json --robustness-out artifacts/robustness-plan.json
python -m deepfake_lens eval data/raw --pixel deep --json-out artifacts/eval.json --html-out artifacts/eval.html --false-positive-out artifacts/fp.jsonl --false-negative-out artifacts/fn.jsonl
python -m deepfake_lens calibrate data/raw --pixel deep --target-fpr 0.05 --out artifacts/calibration.json
python -m deepfake_lens train data/raw --pixel deep --target-fpr 0.05 --out artifacts/portable-threshold.model.json
python -m deepfake_lens benchmark data/raw --pixel-modes off,fast,deep --json-out artifacts/benchmark.json --md-out artifacts/benchmark.md
python -m deepfake_lens fusion data/raw --pixel deep --model-path artifacts/aide-profile.json --target-fpr 0.05 --out artifacts/fusion-profile.json
python -m deepfake_lens benchmark data/raw --pixel-modes off,fast,deep --fusion-profile artifacts/fusion-profile.json --json-out artifacts/benchmark-fused.json --md-out artifacts/benchmark-fused.md
python -m deepfake_lens perf data/raw --pixel deep --workers 4 --cache artifacts/scan-cache.json --hash-db artifacts/hash-db.json --out artifacts/perf.json
```

The robustness plan is a no-dependency manifest of variants to generate externally: JPEG quality changes, resizing, crops, light blur, screenshots, and social-media recompression. Put generated variants under folders named by transform and run `eval --robustness`.

For command smoke checks without a real benchmark, use the tiny layout fixture:

```sh
python -m deepfake_lens dataset fixtures/deepfake-lens-sample --manifest-out /tmp/dfl-manifest.json --audit-out /tmp/dfl-audit.json --split-out /tmp/dfl-split.json
python -m deepfake_lens eval fixtures/deepfake-lens-sample --pixel off --json-out /tmp/dfl-eval.json
python -m deepfake_lens perf fixtures/deepfake-lens-sample --out /tmp/dfl-perf.json
```

## Model Registry

`models` prints detector candidates and integration notes. It can also write a local runtime profile:

```sh
python -m deepfake_lens models --candidate aide-iclr-2025 --checkpoint models/aide.onnx --profile-out artifacts/aide-profile.json
python -m deepfake_lens scan samples --model-path artifacts/aide-profile.json
```

The current registry is research-backed and intentionally separates benchmarks from reusable checkpoints:

- [NTIRE 2026 Robust AI-Generated Image Detection in the Wild](https://arxiv.org/abs/2604.11487): robustness benchmark and challenge report for transformed real-world images.
- [AIDE ICLR 2025](https://github.com/shilinyan99/AIDE): public code/checkpoints candidate for first pretrained image detector integration.
- [CLIDE WACV 2026](https://rbetser.github.io/CLIDE/): zero-shot CLIP-likelihood direction for unseen generators.
- [Dual-Path AI-Generated Image Detection](https://github.com/ljppp117/Dual-Path-AI-Generated-Image-Detection): patch/global detector candidate for local artifact heatmaps.
- [DIFC-Net 2026](https://www.mdpi.com/1424-8220/26/8/2389): diffusion-intrinsic feature research candidate.
- [Out-of-box benchmark 2026](https://researchtrend.ai/papers/2602.07814): model selection reference covering many open-source detector variants.

## Video Workflow

Video support is intentionally frame-first:

```sh
python -m deepfake_lens video cases/videos --out artifacts/video-plan.json --frame-root artifacts/frames
python -m deepfake_lens video cases/videos --out artifacts/video-plan.json --frame-root artifacts/frames --extract
python -m deepfake_lens scan artifacts/frames --recursive --pixel deep --heatmaps
```

`--extract` requires local `ffmpeg`; planning does not.

## Neural Training Handoff

`train-neural-plan` writes a concrete training/export checklist for an external PyTorch experiment:

```sh
python -m deepfake_lens train-neural-plan data/raw --out artifacts/neural-plan.json --output-dir experiments/run-001 --architecture convnext_tiny --epochs 10
```

The plan records dataset counts, expected artifacts, ONNX export location, benchmark command, and guardrails. The optional experiment scripts provide the runnable local handoff:

```sh
python experiments/train_detector.py --manifest artifacts/manifest.json --arch convnext_tiny --epochs 10 --out experiments/run-001
python experiments/export_onnx.py --checkpoint experiments/run-001/convnext_tiny.torchscript --out experiments/run-001/convnext_tiny.onnx
python -m deepfake_lens scan samples --model-path experiments/run-001/runtime-profile.json --fusion-profile artifacts/fusion-profile.json
```

`train_detector.py` writes a TorchScript checkpoint, state dict, runtime profile, and training metadata when optional PyTorch dependencies are present. It intentionally stays outside the package dependency set.

## Frequency, Biometric And Provenance Screening (Phase 2)

- `pixel` ensemble `frequency_forensics` expert (requires numpy): real FFT/DCT measurements replacing the former shift-difference pseudo-spectral expert — radial power-spectrum slope (natural images decay ~1/f^2), robust spectral-spike detection above the radial average (upsampling/checkerboard artifacts), NPR-inspired neighboring-pixel interpolation consistency, and per-block DCT high-frequency energy share. Feature computation lives in `deepfake_lens/frequency.py`.
- `rppg <video>` (requires opencv; numpy for the pulse math): CHROM remote photoplethysmography — face-ROI RGB means are projected to chrominance signals and band-passed to 0.7-4 Hz. A stable cardiac peak (SNR >= 8, 45-200 bpm) is evidence of a camera-captured live face; its absence raises a weak 25-weight suspicion signal only. Compression, poor lighting, and motion can erase the pulse, so a missing pulse is never a verdict on its own.
- `prnu <target> --reference a.png --reference b.png --reference c.png` (requires numpy): sensor-fingerprint (PRNU) provenance screening — residuals of 3+ same-device reference images are averaged into a fingerprint and the target's residual is correlated against it (zero-mean NCC after border-cropped Gaussian denoising). NCC >= 0.10 reads as same-device origin; a mismatch raises a weak suspicion signal. Re-compression, resizing, and rendering degrade the fingerprint, so a mismatch is a lead, not a verdict.
- C2PA manifest validation (`forensic` command; optional `provenance` extra = c2pa-python): when the official SDK is installed, `forensic` validates real C2PA manifests — validation state, signer identity, and per-claim success/failure codes — instead of guessing from byte strings. A state other than `valid` usually means the signer is not in the trust store (reported as "검증 미완료"), not proof of tampering. Without the SDK, marker strings are reported as reference-level hints only. Byte scanning can never detect SynthID (a pixel-domain watermark); Google tool strings in metadata are attribution hints, and the analysis says so.

## SBI Training (experiments)

`python experiments/train_detector.py --manifest artifacts/manifest.json --sbi ...` trains with Self-Blended Images (Shiohara & Yamasaki, CVPR 2022): fakes are synthesized from the real-labeled images only, by blending two differently distorted copies (bilinear resampling, Gaussian blur, DCT-quantization JPEG simulation, color jitter) under a random soft mask. A detector trained this way learns blending/resampling artifacts rather than one generator's signature and needs no fake data. `experiments/sbi.py` is pure numpy; training itself still requires torch/torchvision.

## Limits

This is a screening tool, not a truth engine.

- Missing metadata means `출처 단서 없음`, not human-made.
- Pixel-level scores are local heuristic ensemble scores, not calibrated probabilities from a trained foundation model.
- Research-named experts are local implementations or adapters inspired by those approaches. They are not claimed to reproduce original paper weights or benchmark scores.
- The `train` command currently produces a portable threshold model, not a deep neural checkpoint. Neural checkpoint training is available only through optional `experiments/` scripts.
- Exact model/checkpoint attribution is only possible when metadata contains those details.
