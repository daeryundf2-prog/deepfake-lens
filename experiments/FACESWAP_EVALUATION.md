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

## Candidate B — dima806/deepfake_vs_real_image_detection — WIRED

ViT image classifier, apache-2.0, ~330 MB HF snapshot
(`models/face-manipulation-vit-runtime.json` +
`face-manipulation-vit-frames-runtime.json`).

| Input | Top label | Score |
|---|---|---|
| lenna-face.png (real crop) | Real | 0.992 |
| lenna-face-sbi.png (SBI self-blend) | Fake | 0.783 |
| lenna.png (real, full frame) | Real | 0.996 |

Verdict: correct direction and separation on the local sanity set. Wired as a
`requires_face` ensemble member — frames/images without a detected face are
skipped rather than scored.

## Honest limits

- Three local images is a smoke test, not a benchmark. Per-generator recall,
  cross-domain accuracy (KoDF/Celeb-DF style), and JPEG/resize robustness are
  still unmeasured — the member stays advisory.
- `requires_face` depends on the Haar/MediaPipe gate; faces it misses are
  silently skipped (coverage loss, not false positives).
- Rejected-checkpoint lesson: upstream model-card metrics did not transfer —
  every new face-manipulation weight must pass a labeled local set before its
  `supported` flag flips.
