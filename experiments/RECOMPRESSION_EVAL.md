# Recompression / degradation robustness (2026-09-19)

Field-realistic degradations applied to the eval sets — social-media
re-encoding is the common case where detectors silently collapse.

## Image — JPEG quality + resize sweep

Ensemble members: AIDE, UnivFD, CNNDetection, Swin (umm-maybe).

| Input | clean | q75 | q50 | half-size | down-4x-up |
|---|---|---|---|---|---|
| **dalle_0 (AI)** ENS / AIDE | 30 / 90 | 19 / 43 | 15 / 28 | 20 / 45 | 30 / 83 |
| **dalle_1 (AI)** ENS / AIDE / Swin | 43 / 63 / 77 | 48 / 87 / 74 | 28 / 6 / 75 | 34 / 30 / 75 | 32 / 11 / 84 |
| **dalle_3 (AI)** ENS / AIDE | 40 / 91 | 25 / 43 | 16 / 6 | 20 / 19 | 39 / 78 |
| **lenna (real)** ENS / AIDE | 30 / **85 FP** | 11 / 12 | 12 / 13 | 17 / 20 | 23 / 52 |

Findings:

- **AIDE is JPEG-fragile in both directions.** Its fake scores collapse
  under recompression (91→6 on dalle_3 at q50) — but so does its Lenna
  false positive (85→12 at q75). The detector is reacting partly to
  compression artifacts themselves.
- **Swin is the recompression-stable member.** dalle_1 holds 74–84 across
  every degradation — the only member whose catch survives q50.
- **UnivFD contributes noise on this set**: flat ~30–36 on every input
  including real — worth a weight review.
- **CNNDetection is flat 0** — always "real" here, consistent with its
  documented weak diffusion transfer.
- Net effect: a recompressed DALL-E image lands at ensemble 15–28 (low),
  and a recompressed real photo lands at 11–23 (low) — the band order
  survives but the margin narrows sharply.

## Audio — mp3 24 kbps re-encode

| Input | AASIST clean | AASIST 24k | wav2vec clean | wav2vec 24k |
|---|---|---|---|---|
| edge-tts en_guy | 0.189 | **0.718** | 0.604 | 0.198 |
| edge-tts ko_sunhi | 0.685 | 0.747 | 0.858 | 0.835 |
| edge-tts ko_injoon (ko text) | 0.000 | 0.005 | 0.214 | 0.136 |
| real LibriSpeech | 0.019 | 0.028 | 0.103 | 0.082 |

Findings:

- **Heavy re-encoding pushes AASIST scores UP on synthetic audio**
  (0.19→0.72) — compression artifacts read as "spoof-like" to the
  ASVspoof-trained member. Caution: the same mechanism may push real
  re-encoded speech up too; the real sample stayed clean here but only
  one sample was tested.
- wav2vec degrades gracefully downward on 24k encodes.

## Action taken

- Profile limitations updated where the behavior was new information
  (AASIST bandwidth/codec sensitivity already partially recorded).
- This document is the measured basis for the "low score ≠ real"
  warnings — a recompressed synthetic can sit below threshold on every
  member while looking legitimate.
