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

### Measured results — round 2 (diverse-domain training data)

The FFHQ-only checkpoint was retrained after adding 86 diverse real
faces (Wikimedia old/damaged/vintage portraits, face-cropped) plus their
SBI fakes — 62 train / 24 held-out. Effect on the false-positive domain:

| Set | n | AUROC | recall@50 | FPR@50 |
|---|---:|---:|---:|---:|
| FFHQ in-domain, clean | 40 | 0.90 | 0.65 | 0.00 |
| FFHQ in-domain, jpeg75 | 40 | 0.84 | 0.65 | 0.15 |
| FFHQ in-domain, 50% resize | 40 | 0.86 | 0.65 | 0.05 |
| Cross-domain (27 diverse/old portraits, held-out) | 216 | 0.79 | 0.48 | 0.07 |

### Measured results — round 1 (FFHQ-only, superseded)

| Set | n | AUROC | recall@50 | FPR@50 |
|---|---:|---:|---:|---:|
| FFHQ in-domain, clean | 40 | 0.94 | 0.70 | 0.05 |
| FFHQ in-domain, jpeg75 | 40 | 0.91 | 0.75 | 0.20 |
| FFHQ in-domain, 50% resize | 40 | 0.91 | 0.80 | 0.25 |
| Cross-domain portraits (Einstein/Lincoln/Lenna) | 36 | 0.72 | 0.89 | 0.67 |

Round-2 trade-off: cross-domain FPR dropped 0.67 → 0.07 (10x) at the
cost of recall 0.89 → 0.48 — consistent with the precision-over-recall
posture. A conservative score now means 'needs review', not 'probably
real'.

Verdict: **wired with measured limits** — the only working
face-manipulation member. In-domain AUROC ~0.86-0.90 with low FPR;
cross-domain AUROC ~0.79 is usable but recall is conservative. The
profile limitations and `crop_faces` gating carry this; the member is
advisory weight and must not drive a verdict alone.

### Reproduce

    python experiments/train_detector.py --manifest faces.json --sbi         --augment-degradation --arch efficientnet_b0 --out models/
    python experiments/eval_face_manipulation.py --real-dir <faces>         --profile models/sbi-effnet-runtime.json

## Honest limits

- `crop_faces` coverage depends on the detector (Haar + MediaPipe
  FaceMesh fallback); faces both miss are silently skipped.
- The eval set is small; in-domain numbers are FFHQ-like faces only.
- Aged/damaged portraits are a measured false-positive domain for
  off-domain classifiers — keep them in every future eval set.
- Cross-domain AUROC 0.79 with conservative recall means this member
  must not drive a verdict alone; it prioritizes review, nothing more.


## Face-detector swap candidate — yolov8n-face — REJECTED (2026-09-20)

Proposal: replace Haar+MediaPipe with `yolov8n-face-lindevs.onnx`
(12 MB, WIDERFace-trained) to cut detection misses on side/low-light
faces. Measured on the local corpus before wiring:

| Set | n | MediaPipe+Haar | yolov8n-face (conf 0.35) |
|---|---:|---:|---:|
| diverse portraits (train pool) | 62 | 61 | 53 |
| FFHQ val | 30 | 30 | 30 |
| cross-domain held-out | 27 | 25 | 22 |

Verdict: the existing stack already out-detects yolov8n-face on every
set — including the vintage/damaged portraits it was meant to rescue.
Lowering confidence below 0.35 recovers misses but at unmeasured
false-positive cost. Rejected; the detector stays MediaPipe primary +
Haar fallback. Detection-miss remains a documented blind spot
(`crop_faces` skips silently) — the fix is a recall benchmark, not this
model.


## SBI v2 — enriched blending + score recalibration (2026-09-20)

v1 recall dropped to ~0.34-0.48 after diverse-portrait negatives were
added. Retrained with diversified self-blending: polygon/hull masks,
affine misalignment between warped face and base, extra
noise/sharpen distortion paths (experiments/sbi.py).

Held-out eval: 72 real (40 FFHQ-val + 32 diverse/archival) + 85 fake
(v2-recipe SBI positives never seen in training).

| Member | AUROC | FPR@50 | recall@50 | recall @ matched FPR~8% |
|---|---:|---:|---:|---:|
| v1 (deployed) | 0.813 | 0.029 | 0.342 | in-domain 0.41 / xd 0.20 |
| v2 raw | 0.862 | 0.250 | 0.785 | in-domain 0.77 / xd 0.30 |
| v2 + score_bias 35 | 0.862 | 0.044 | 0.405 | (same operating point) |

v2's ROC dominates v1 at every matched-FPR operating point; the raw
score distribution shifted ~16 points up on real faces, so
`score_bias: 35` was added to `_score_from_outputs` and set in the
runtime profile to restore the precision-first operating point.
Promoted as `models/sbi-effnet-b0.pth` (still git-ignored).

Residual caveat: cross-domain ranking stays weak (AUROC ~0.68) — the
member remains advisory-only on aged/scanned portraits.
