> ⚠ 미검증(n 부족 또는 재현 불가) — 2차 계획 WP-I 참조. 아래 수치는 클래스당 200개 미만이거나 코퍼스가 재현 불가능하여 증거로 사용할 수 없다.

# Deepfake Lens Experiments

These scripts are optional research utilities. They are not required by the
local CLI install.

Expected optional dependencies for real training:

- `torch`
- `torchvision`
- `Pillow`
- `onnx` (required by `export_onnx.py`; the torch ONNX exporter fails
  without it, which is easy to miss because torch itself imports fine)

Recommended flow:

```sh
python -m deepfake_lens dataset data/raw --manifest-out artifacts/manifest.json --audit-out artifacts/audit.json --split-out artifacts/split.json
python experiments/train_detector.py --manifest artifacts/manifest.json --arch convnext_tiny --epochs 10 --out experiments/run-001
python experiments/export_onnx.py --checkpoint experiments/run-001/convnext_tiny.torchscript --out experiments/run-001/convnext_tiny.onnx
python -m deepfake_lens models --candidate aide-iclr-2025 --checkpoint experiments/run-001/convnext_tiny.onnx --profile-out experiments/run-001/onnx-runtime-profile.json
python -m deepfake_lens benchmark data/raw --pixel-modes deep --model-path experiments/run-001/runtime-profile.json --json-out experiments/run-001/benchmark.json
```

`train_detector.py` trains a local binary image detector, writes a TorchScript
checkpoint, state dict, runtime profile, and history metadata. Keep the command
outside the package dependency set so the CLI remains lightweight.

`--sbi` (Self-Blended Images) trains from real-labeled images only: each
record yields an original plus a blended fake synthesized from distortion
pairs (see `experiments/sbi.py`). This avoids needing a fake dataset and
learns blending/resampling artifacts instead of one generator's signature.

`export_onnx.py` takes the input size from `training-metadata.json` beside
the checkpoint and verifies the exported ONNX against TorchScript with
onnxruntime when it is installed.

## Android handoff

The mobile app runs the exported model through ONNX Runtime when the file
is present:

```sh
python experiments/export_onnx.py --checkpoint experiments/run-001/convnext_tiny.torchscript \
  --out deepfakeclassifier/src/main/assets/deepfake-lens.onnx
```

Contract (see `deepfakeclassifier/src/main/assets/README.md` and
`OnnxClassifier.kt`): input `input` [1,3,224,224] NCHW float32,
ImageNet-normalized RGB; output `logits` [1,2], softmax over
`real=0, synthetic-fake=1`. Without the asset the app degrades to
heuristics only. Until a checkpoint is validated on a real benchmark
(cross-dataset AUC/EER report), the neural score is surfaced in the app as
a weight-0 informational signal and never moves the heuristic score.

Do not publish checkpoints without dataset provenance, license notes, and a
calibration/benchmark report.

## 프로필에서 옮긴 측정 메모 (미검증)

> ⚠ 미검증(n 부족 또는 재현 불가) — 2차 계획 WP-I 참조. R4(검증 2차): 아래는 `deepfake_lens/models/*-runtime.json`의
> `limitations`/`notes`에 있던 원문(영어)을 그대로 옮긴 것이다. 프로필 문구는 사용자에게 그대로 표시되므로 한국어로
> 바꾸고, 클래스당 200개 미만·재현 불가 측정의 recall/FPR/AUROC 수치는 프로필에서 지웠다. 수치는 증거로 사용할 수 없다.

### `aasist-runtime.json`

- limitations: Checkpoint is not committed; run scripts/fetch_aasist.py to download AASIST.pth (~1.3 MB) into models/aasist.pth.
- limitations: AASIST code and weights are MIT-licensed (NAVER Corp.) but the model was trained on ASVspoof2019-LA, whose dataset terms are research-oriented; treat the checkpoint as research-use, not a redistributed asset of this repo.
- limitations: Trained on ASVspoof2019 LA attacks (TTS/VC): generalization to newer neural codecs and commercial voice-clone services is not guaranteed — a low score is not evidence of real audio.
- limitations: Measured locally (2026-09): edge-tts 7-voice recall ~1/7 at 0.5 (voice-dependent), and false-positives on low-bandwidth real speech (8 kHz YESNO scored 0.93 spoof). Pairs with the wav2vec member which is calibrated better on real speech but weak on some TTS voices.
- limitations: Only the first ~30s is scored, in ~4s windows whose logits are averaged; very long files are not fully covered.
- limitations: Scores are a prioritization signal, not a truth label.
- limitations: Expanded sweep (40 edge-tts voices / 12 real controls, 2026-09-20): fake recall@50 0.72, real FPR@50 0.08. Combined with wav2vec, union recall reaches 0.93; both missed only 3 English voices.
- notes: Runtime 'aasist' delegates to scripts/run_aasist.py, a faithful reimplementation of clovaai/aasist models/AASIST.py (sinc-conv front-end + 6 residual blocks + spectro-temporal graph attention); attribute names match the official state dict.
- notes: Audio is decoded to mono at sample_rate, then scored in non-overlapping evenly-spaced windows of window_samples (64600 ~= 4.04s, the upstream evaluation cut); windows beyond the file are skipped and short files are loop-padded exactly like upstream pad().
- notes: Model logits are (spoof=0, bonafide=1) — upstream's own eval reads index 1 as the bonafide score, so score_index 0 with softmax yields the spoof/fake probability on a 0-100 scale.
- notes: The checkpoint path is relative to this profile's directory (models/); keep it out of git — it is covered by .gitignore.

### `ai-image-swin-runtime.json`

- limitations: Hub weights are not committed; transformers downloads ~870 MB on first use (set hub_model to a local snapshot directory for offline runs).
- limitations: Measured locally (2026-09) on 4 DALL-E samples + Lenna: caught 1 of 4 (artificial 0.77), missed 3 (0.01/0.00/0.35); correctly scored real Lenna human 0.97.
- limitations: Complements AIDE mainly as real-image calibration (AIDE scored real Lenna 85% fake; this model 3%) — it does NOT close the commercial-generator recall gap; DALL-E-family recall stays weak.
- limitations: AutoTrain fine-tune without a published eval protocol; per-generator recall is unverified beyond the local samples above.
- limitations: Not in the CLI default member list — it is picked up automatically by scans pointed at the models/ directory (web app, api_server, --model-path models).
- limitations: Scores are a prioritization signal, not a truth label.
- limitations: G33: the same hub weights (umm-maybe/AI-image-detector) were re-measured on 2026-09-21 and showed no separation under either label reading (see docs/MODEL-REJECTIONS.md). The two measurements conflict, so the profile stays disabled until WP-I re-measures it.
- notes: SwinForImageClassification (Swin-large) fine-tuned on AI-vs-human image sets, cc-by-4.0, ~355k HF downloads.
- notes: score_label 'artificial' resolves the class index from id2label ({0: artificial, 1: human}) at runtime — label order cannot silently invert the score.
- notes: Kept opt-in for default runs: the measured marginal gain over the AIDE default is modest (it dilutes strong AIDE hits on some generators while correcting AIDE's false positives on processed real photos) — see experiments/IMAGE_EVALUATION.md.

### `aide-runtime.json`

- limitations: Checkpoint is not committed; run scripts/fetch_aide.py to download progan_train.pth (~3.3 GB, research license).
- limitations: Measured locally (2026-09): JPEG recompression collapses fake scores (DALL-E 91->6 at q50) and real-image false positives alike (Lenna 85->12 at q75) — below q75 this member is auto down-weighted via 'degraded_weight'. See experiments/RECOMPRESSION_EVAL.md.
- limitations: AIDE scores are a prioritization signal, not a truth label; see experiments/AIDE_EVALUATION.md for measured AUROC and cross-generator caveats.
- notes: Runtime 'aide' delegates to scripts/run_aide.py, which bakes AIDE's DCT band-selection preprocessing into the model class; input_size/mean/std are recorded for reference rather than consumed by the generic image preprocessor.
- notes: Model logits are (real=0, fake=1); score_index 1 with softmax yields the fake probability on a 0-100 scale.
- notes: The checkpoint path is relative to this profile's directory (models/); keep it out of git — it is covered by .gitignore.

### `community-forensics-frames-runtime.json`

- limitations: Frame-level generated-image detection, not a temporal/lip-sync/faceswap model — fully AI-generated video (Sora-class) frames may flag; face-swapped real video will NOT (that class needs crop_faces members on extracted faces).
- limitations: Requires the ONNX checkpoint (not committed) and opencv for frame decoding.
- limitations: Inherits the image member's measured profile: precision high, recall generator-limited; heavy recompression collapses scores.
- limitations: Scores are a prioritization signal, not a truth label.

### `community-forensics-vit-runtime.json`

- limitations: Checkpoint is not committed; download generative_detector.onnx from Red-had1911/deepfake-detector-onnx.
- limitations: Trained on 2.7M samples across ~4,800 generators (Community Forensics, 2024).
- limitations: Measured locally (2026-09-21, mixed 55-sample corpus): AUROC 0.56, FPR@50 0.04, recall@50 0.14 — precision is strong (reals read ~0.00-0.06) but recall is generator-limited: DALL-E mean 0.55, SD-Turbo recall 0.00, Hemg face-manipulation fakes read ~0.04 (that class belongs to crop_faces members).
- limitations: Same-repo faceswap_detector.onnx was also evaluated on face crops and rejected — no separation between real faces and SBI blends (real 0.01-1.0, fake 0.02-0.99 overlapping).
- limitations: The onnx-community auto-converted export (model.onnx, 2-logit) produces flat ~0.4-0.7 scores on every input — DO NOT use it; this profile uses the direct single-logit export.
- limitations: Card preprocessing is resize-440 + center-crop-384; the adapter applies a direct 384 resize (small domain shift).
- limitations: Scores are a prioritization signal, not a truth label.
- notes: Output (N,1) logit; sigmoid = P(AI-generated) on 0-100 scale.

### `fakespot-detector-runtime.json`

- limitations: Trained on newer LLM outputs than the GPT-2-era OpenAI detector, but still an English-centric classifier — re-validate on target-domain samples before trusting thresholds.
- limitations: The model is fetched from Hugging Face on first use (~500 MB into the HF cache); set 'hub_model' to a local snapshot directory to run fully offline.
- limitations: Prioritization signal, not a truth label — short text and non-English text remain unreliable.
- notes: Label order for this checkpoint is index 0 = Human, index 1 = AI — verified against the model's config.json id2label (opposite of the OpenAI detector, which is 0=Fake).

### `sbi-effnet-runtime.json`

- limitations: Checkpoint is not committed; reproduce with experiments/train_detector.py --sbi --augment-degradation --arch efficientnet_b0.
- limitations: score_bias=35 recalibrates the shifted score distribution measured on 147 held-out crops (v2 raw scores run ~16 points hotter on real faces than v1).
- limitations: Measured at bias 35, threshold 50 (2026-09-20 eval, 72 real + 85 fake): overall FPR 0.044 / recall 0.405; in-domain FPR 0.000 / recall 0.405; cross-domain FPR 0.080 / recall 0.300 — dominates v1 (FPR 0.029 / recall 0.342) at every operating point.
- limitations: ROC-level (bias-free): in-domain AUROC 0.96, cross-domain AUROC ~0.68. Cross-domain ranking still weak — treat member as advisory on aged/scanned/damaged portraits.
- limitations: crop_faces=true: face-free images are skipped (unavailable) rather than scored.
- limitations: Generic non-portrait corpus (Hemg holdout, 2026-09-20): crop_faces scored 43/55 images and FPR@50 hit 0.50 — outside face-portrait domains this member's ranking is noise; advisory only.
- notes: Model logits are (real=0, fake=1); score_index 1 with softmax yields the fake probability on a 0-100 scale.
- notes: Trained on 362 real faces (300 FFHQ + 62 diverse portraits) + 362 self-blend fakes with both-class JPEG/resize degradation augmentation; combined val AUROC 0.88.
- notes: Training on FFHQ only produced a cross-domain FPR of ~0.67 on old/damaged portraits; adding 62 diverse real faces dropped it to ~0.07 — see FACESWAP_EVALUATION.md.

### `sbi-frames-runtime.json`

- limitations: This is the measured faceswap path for video: faces are detected per sampled frame (MediaPipe + Haar fallback) and scored by the SBI member — the member with AUROC ~0.87 on held-out face manipulation.
- limitations: Frames without detectable faces are skipped, not scored as authentic.
- limitations: Not a temporal model — per-frame face scores are averaged; lip-sync/inter-frame drift is not modeled.
- limitations: Heavy compression/low-resolution faces degrade detection (documented in experiments/FACESWAP_EVALUATION.md).
- limitations: Checkpoint is not committed; reproduce via scripts + experiments/train_detector.py.
- limitations: Scores are a prioritization signal, not a truth label.

### `sd-turbo-det-runtime.json`

- limitations: Checkpoint is not committed; reproduce with experiments/gen_sdturbo_corpus.py + train_detector.py.
- limitations: Single-generator training (SD-Turbo 4-step) — measured cross-generator transfer: DALL-E AUROC 0.99 / recall 0.75 / FPR 0.00 (4 samples, small set).
- limitations: In-domain holdout AUROC 0.999 / recall 1.00 / FPR 0.075 (80 held-out).
- limitations: Measured failure: face-manipulation fakes (Hemg 'fake' class, StyleGAN/faceswap-style 256px portraits) score as real — AUROC 0.44, recall 0.00. That class is covered by the sbi-effnet member (crop_faces); do not treat a low score here as evidence of authenticity.
- limitations: JPEG q50 recompression collapses fake scores toward 0 (measured: recall 0.00 on q50 Hemg) — compressed media reads low regardless.
- limitations: Scores are a prioritization signal, not a truth label.
- notes: Model logits are (real=0, ai=1); score_index 1 with softmax yields the fake probability on a 0-100 scale.
- notes: ensemble_weight 0.6 keeps it advisory until a larger cross-generator corpus validates the DALL-E transfer signal.

### `wav2vec-deepfake-audio-runtime.json`

- limitations: Hub weights are not committed; transformers downloads ~1.2 GB on first use (set hub_model to a local snapshot directory for offline runs).
- limitations: Fine-tuned on the 'In-the-Wild' audio deepfake dataset — coverage of generators outside that dataset is unverified.
- limitations: Measured locally (2026-09): catches older synthesizer TTS (SAPI fake 91.7%) and is well-calibrated on real speech (LibriSpeech real 89.7%, YESNO 8 kHz real 93.0%).
- limitations: Edge-tts sweeps (2026-09): recall ~4/7 at 0.5 — voice- and text-dependent (en_guy 0.60 caught; en_aria 0.25 missed). Korean-text batch A scored 0.21-0.44 but batch B scored 0.57-0.91 — the variance is sample-dependent, not a systematic language gap. A low score is not evidence of real audio.
- limitations: Only the first ~15 s is scored; longer files are truncated.
- limitations: Scores are a prioritization signal, not a truth label.
- limitations: Expanded sweep (40 edge-tts voices / 12 real controls, 2026-09-20): fake recall@50 0.78, real FPR@50 0.00 — best-calibrated member on real speech in the measured set; union with AASIST reaches 0.93 recall.
- notes: Wav2Vec2ForSequenceClassification fine-tuned on In-the-Wild (real vs generated speech collected in the wild), Apache-2.0.
- notes: score_label 'fake' resolves the class index from id2label at runtime ({0: 'real', 1: 'fake'} in this checkpoint) — label order cannot silently invert the score.
- notes: Pairs with the AASIST member as complementary coverage: AASIST catches generators outside In-the-Wild but false-positives on low-bandwidth real speech (8 kHz telephone audio scored 97.5% spoof); this model is better calibrated on real speech but misses newer TTS. Disagreement between the two is itself a review signal.
- notes: Empirical comparison vs the rejected MelodyMachine/Deepfake-audio-detection-V2 candidate: that checkpoint returned inverted scores on local samples (real speech -> fake 100%), so it is not wired.
