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

## Video-container recompression (2026-09-20, aide-frames member)

Synthetic test: still frames → 4 s mp4 → crf32 re-encode and
screen-capture sim (downscale + re-encode). AIDE per-frame scores:

| Video | original | crf32 | screen-recap |
|---|---|---|---|
| real_lenna | **86 FP** | 9 | 11 |
| fake_dalle0 | 16 miss | 18 | 10 |
| fake_dalle1 | 19 miss | 12 | 11 |

The frame-aggregated member inherits the image member's failure modes
verbatim: the known Lenna false-positive fires in video too (86), DALL-E
frames are missed, and recompression collapses every score toward zero.
**Per-frame video scoring adds nothing beyond the image modality's own
weaknesses on this corpus — treat video scores as noise when the
underlying still would mislead the image members.**

## Screen-capture simulation (2026-09-23, sim_screen_capture.py)

Realistic screen-recording degradations: display-grid resampling moiré,
refresh-rate brightness banding, optional perspective skew, then
JPEG q80 (images) / H.264 crf30 (video).

### Image members under screen-capture degradation

| Input | swin | community-forensics | sd-turbo-det |
|---|---|---|---|
| dalle_0 screen | 5 miss | 0 miss | **100 catch** |
| dalle_1 screen | 87 catch | 0 miss | **82 catch** |
| hemg_real screen | 5 | 0 | 0 |
| lenna screen | 6 | 0 | 1 |

- **sd-turbo-det survives screen-capture** on its diffusion class —
  the class-balanced degradation augmentation paid off where CF-ViT
  (no comparable augmentation in its export) collapses to 0.
- Zero false positives on the real screens.

### sd-turbo-det under direct JPEG sweep (diffusion class only)

| Condition | dalle scores | real |
|---|---|---|
| clean | 100, 84, 100 | all ≤1 |
| JPEG q50 | 100, 79, 100 | all ≤1 |
| JPEG q30 | 100, 84, 100 | all ≤1 |

The earlier "q50 AUROC 0.51 collapse" was driven by the Hemg
face-manipulation class (which this member never detected); on its
actual diffusion class the member is already compression-robust.

### V3 compression-invariant retrain — REJECTED

Retrained on the same corpus as convnext_tiny with heavier class-balanced
degradation (JPEG q30-90, downscale 0.3-0.7, blur, noise). Result:
perfectly compression-invariant (identical scores clean/q50/q30) but
non-discriminating — real mean ~40 vs fake mean ~45, FPR collapses.
Invariance without separation is worthless; v2 stays.

## Video face-track under screen-capture (face_track module)

| Video | faces found | score |
|---|---|---|
| talk_plos real screen | 16/16 | 0 |
| talk_wikitongues real screen | 65/65 | 0 |
| swap_jitter screen | **0-7** | unavailable |

- Real tracks survive moiré/banding with no false positive.
- **The swapped video loses face detection entirely under screen
  degradation** — the track signal degrades to *unavailable*, not to a
  false "clean". Screen-recording is an evasion vector for
  track-based detection: absence of a track verdict is not evidence of
  authenticity.
