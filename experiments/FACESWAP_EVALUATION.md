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

Neither community checkpoint survived local measurement. Direction 1
below was executed (see Candidate C); the rest remain open:

1. ~~Local fine-tune~~ — done: `train_detector.py --sbi
   --augment-degradation`, evaluated with `eval_face_manipulation.py`,
   wired as `models/sbi-effnet-runtime.json` (narrow domain — see
   Candidate C).
2. **Gated/restricted checkpoints** — e.g.
   `HrutikAdsare/deepfake-detector-faceforensics` (HF gated, 401) —
   requires operator-authenticated access before evaluation.
3. **Cross-domain KoDF model** — a checkpoint verified on Korean faces,
   since deployment data is likely Korean; would also address the
   measured cross-domain FPR of Candidate C.

## Candidate C — local SBI-trained EfficientNet-B0 — WIRED (narrow domain)

After both public checkpoints failed, a local detector was trained with
`experiments/train_detector.py --sbi` on 360 FFHQ faces + 360 face-region
self-blend fakes (pure NumPy `sbi.py` pipeline, no external fake corpus).

### Failure found and fixed during measurement

The first checkpoint scored AUROC 0.97 on clean held-out SBI pairs but
**FPR 1.0 under JPEG q75 recompression** — the model had learned
'JPEG artifacts = fake' because `jpeg_simulate` is one of the blend
distortions and real training images were never recompressed. Fix:
`--augment-degradation` now applies random JPEG/resize to BOTH classes;
retrained model no longer collapses.

### Measured results (eval_face_manipulation.py, models/sbi-effnet-runtime.json)

| Set | n | AUROC | recall@50 | FPR@50 |
|---|---:|---:|---:|---:|
| FFHQ in-domain, clean | 40 | 0.94 | 0.70 | 0.05 |
| FFHQ in-domain, jpeg75 | 40 | 0.91 | 0.75 | 0.20 |
| FFHQ in-domain, 50% resize | 40 | 0.91 | 0.80 | 0.25 |
| Cross-domain portraits (Einstein/Lincoln/Lenna) | 36 | 0.72 | 0.89 | 0.67 |

Verdict: **wired with narrow-domain limitations** — the only working
face-manipulation member. Passes the ≥0.8 gate on its training domain
with degradation robustness, but old/scanned/sepia portraits remain a
measured false-positive domain (FPR ~0.67). The profile limitations and
`crop_faces` gating carry this. Scores on non-FFHQ-like faces should be
treated as unreliable; the member is advisory weight.

### Reproduce

    python experiments/train_detector.py --manifest faces.json --sbi         --augment-degradation --arch efficientnet_b0 --out models/
    python experiments/eval_face_manipulation.py --real-dir <faces>         --profile models/sbi-effnet-runtime.json

## Honest limits

- `crop_faces` coverage depends on the detector (Haar + MediaPipe
  FaceMesh fallback); faces both miss are silently skipped.
- The eval set is small; in-domain numbers are FFHQ-like faces only.
- Aged/damaged portraits are a measured false-positive domain for
  off-domain classifiers — keep them in every future eval set.
- Cross-domain AUROC 0.72 means this member must not drive a verdict
  alone; it prioritizes review, nothing more.

