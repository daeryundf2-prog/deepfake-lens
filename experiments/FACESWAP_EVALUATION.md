# Face-manipulation detector evaluation (2026-09)

Purpose: close the biggest measured coverage gap — the classic "deepfake"
family (faceswap/reenactment composites of real pixels), which the generator
detectors (AIDE/UnivFD/CNNDetection) structurally miss.

## Candidate A — Xicor9/efficientnet-b0-ffpp-c23 — REJECTED

Community EfficientNet-B0 fine-tune on FaceForensics++ C23
(`models/faceswap-ffpp-runtime.json`, now `supported:false`).

| Input | P(fake) |
|---|---|
| lenna-face.png (real crop) | 0.962 |
| lenna-face-sbi.png (SBI self-blend) | 0.866 |
| lenna.png (real, full frame) | 0.948 |
| lenna-sbi.png (SBI, full frame) | 0.701 |

Verdict: the signal is inverted/unusable — a real face scores 96% fake and the
manipulated copy scores *lower* than the original. Possible causes (wrong
preprocessing assumptions, checkpoint quality, domain mismatch) were partially
ruled out by matching the model card's stated preprocessing (Resize+ToTensor,
no normalization) — the failure persists. Not shipped.

## Candidate B — dima806/deepfake_vs_real_image_detection — REJECTED

ViT image classifier, apache-2.0, ~330 MB HF snapshot
(`models/face-manipulation-vit-*.json`, now `supported:false`).

Smoke test (3 images) looked correct — lenna-face real 0.992,
lenna-face-sbi fake 0.783 — so it was wired as a `crop_faces` member.
The labeled eval then killed it.

### Labeled eval (experiments/eval_face_manipulation.py)

Set: 5 public-domain portraits (lenna, einstein, lincoln, mona lisa,
chaplin) × face-focused SBI manipulations × {original, jpeg75, half-size}
= 45 samples, 45 scored (coverage 100%).

| Metric | Value |
|---|---|
| AUROC | **0.51 — chance** |
| EER | 0.40 |
| Accuracy@50 | 0.58 |
| FPR@50 | **0.47** |
| Recall@50 | 0.60 |

Per-variant AUROC: original 0.50, jpeg75 0.52, half 0.50.

### Why it fails

- **False positives are domain-driven**: real aged portraits score fake —
  einstein 100, lincoln 99 — while lenna/mona/chaplin score real. The model
  reads "aged/sepia/damaged photograph" as synthetic.
- **SBI recall is weak because the trained task is different**: real-vs-
  fully-AI-generated faces, not pixel-level manipulation of real photos.
  A self-blended real face is still a real face to it.

Verdict: rejected for face-manipulation screening. The profile machinery
(`hf-image-classifier` runtime, `crop_faces` gating, per-crop aggregation,
MediaPipe detection fallback) stays — the next candidate slots into the
same profile shape and must pass this harness before `supported` flips.

## Open gap — faceswap coverage

Neither community checkpoint survived local measurement. Remaining
directions, in order of tractability:

1. **Local fine-tune** — the training path already exists
   (`experiments/train_detector.py`, `sbi.py` augmentations); train on
   FF++/KoDF-style data or SBI-augmented local corpora, then validate
   with `eval_face_manipulation.py` before wiring.
2. **Gated/restricted checkpoints** — e.g.
   `HrutikAdsare/deepfake-detector-faceforensics` (HF gated, 401) —
   requires operator-authenticated access before evaluation.
3. **Cross-domain KoDF model** — a checkpoint verified on Korean faces,
   since deployment data is likely Korean.

## Honest limits

- `crop_faces` coverage depends on the detector (Haar + MediaPipe
  FaceMesh fallback); faces both miss are silently skipped.
- The eval set is small (5 identities); a passing candidate needs a
  larger labeled set before its weight rises above advisory.
- Aged/damaged portraits are a measured false-positive domain for
  off-domain classifiers — keep them in every future eval set.
