# Image detector evaluation — commercial-generator member (2026-09)

Purpose: the known recall gap is commercial generators (dalle3/Midjourney
scored 25–50% in earlier runs). Candidate: `umm-maybe/AI-image-detector`
(Swin-large, ~355k HF downloads — the most-downloaded public AI-image
classifier). Same screening rule as the audio and face rounds: measure
before wiring.

## Test set

| Sample | Class | Notes |
|---|---|---|
| dalle_0.png | AI | Wikimedia "DALL-E sample.png" |
| dalle_1.jpg | AI | Wikimedia "DALL-E 2 artificial intelligence digital image" |
| dalle_2.jpg | AI | Wikimedia "DALL-E radish.jpg" |
| dalle_3.jpg | AI | Wikimedia "DALL-E 2 robot hand drawing" |
| lenna.png | real | standard Lenna test photo (scanned magazine photo) |

## Results — candidate vs incumbents

| Input | AIDE | UnivFD | CNNDet | umm-maybe Swin |
|---|---|---|---|---|
| dalle_0.png | 90 | — | — | 1.3 |
| dalle_1.jpg | 63 | — | — | 76.5 |
| dalle_2.jpg | 6 | — | — | 0.4 |
| dalle_3.jpg | 91 | — | — | 35.4 |
| lenna.png (real) | **85 FP** | 31 | 0 | 3.4 |

Ensemble today (aide+univfd+cnndetection): lenna 39, dalle_0 40, dalle_2 12.

## Conclusions

- **AIDE is stronger on DALL-E than previously recorded** — 3/4 samples at
  63–91. The earlier "25–50% recall" estimate was pessimistic for this set.
- **AIDE false-positive on real Lenna (85)** is a new measured finding:
  heavily processed/scanned real photos can score high. The Swin member
  corrects exactly this failure (3.4) — its main value is real-image
  calibration, not recall.
- **The commercial-generator recall gap is not closed** — dalle_2/dalle_3
  are missed by every wired member and by the candidate. No public
  checkpoint measured here solves it; that limitation stays documented.
- **Decision:** wired as `models/ai-image-swin-runtime.json` (supported,
  opt-in — models-dir scans pick it up; not in the CLI default list). The
  marginal ensemble gain is modest: it dilutes strong AIDE hits
  (dalle_0: 40→~31) while correcting real-image FPs (lenna: 39→~30).
