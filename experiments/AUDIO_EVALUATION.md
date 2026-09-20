# Audio deepfake detector evaluation (2026-09)

Purpose: the only wired audio member was AASIST, trained on ASVspoof2019-LA
(2019-era attacks). Two gaps to close: (1) no measured recall at all, and
(2) known fragility on out-of-domain real speech. Candidates were screened
locally before wiring — model-card metrics are not trusted (see
FACESWAP_EVALUATION.md for the same lesson).

## Test set

| Sample | Class | Notes |
|---|---|---|
| real_extra.flac | real | LibriSpeech studio-quality speech (HF asr_dummy mirror) |
| real_yesno.wav | real | YESNO corpus, 8 kHz telephone-quality speech, ~62 s |
| fake_sapi.wav | fake | Windows SAPI TTS (pyttsx3), older formant-style synthesis |
| fake_edge.mp3 | fake | edge-tts en-US-AriaNeural — modern neural TTS service |

## Candidate — MelodyMachine/Deepfake-audio-detection-V2 — REJECTED

wav2vec2-base fine-tune, apache-2.0, ~9.7k HF downloads.

| Input | P(fake) |
|---|---|
| real_yesno.wav | 1.000 |
| fake_sapi.wav | 0.000 |

Signal completely inverted on this test set — real speech scores 100% fake
and synthetic speech scores 100% real. id2label order was checked; the
inversion is the model's own output, not a label-mapping bug. Not wired.

## Candidate — Gustking/wav2vec2-large-xlsr-deepfake-audio-classification — WIRED

Wav2Vec2-XLSR-large fine-tune on the In-the-Wild audio deepfake dataset,
apache-2.0, ~31k HF downloads → `models/wav2vec-deepfake-audio-runtime.json`
on the new `hf-audio-classifier` runtime (score_label `"fake"` resolves the
index from id2label, so label order can never silently flip the score).

| Input | P(fake) | Correct? |
|---|---|---|
| real_extra.flac (real) | 0.103 | yes |
| real_yesno.wav (real) | 0.070 | yes |
| fake_sapi.wav (fake) | 0.917 | yes |
| fake_edge.mp3 (fake) | 0.179 | **no — missed** |

## Incumbent — AASIST (same files, for comparison)

| Input | P(spoof) | Correct? |
|---|---|---|
| real_extra.flac (real) | 0.019 | yes |
| real_yesno.wav (real) | 0.975 | **no — false positive** |
| fake_sapi.wav (fake) | 1.000 | yes |
| fake_edge.mp3 (fake) | 0.729 | yes |

## Conclusions

- **The two members are complementary.** AASIST catches the modern neural
  TTS that Gustking misses (0.73 vs 0.18); Gustking is far better calibrated
  on low-bandwidth real speech where AASIST false-positives (0.07 vs 0.98 on
  8 kHz YESNO).
- **Ensemble effect (edge-tts):** AASIST 73 + wav2vec 18 → ensemble 46,
  medium band — the sample is flagged for review without a false "high"
  verdict, and member disagreement (55-point spread) is itself a signal.
- **Neither is sufficient alone.** A real recording below studio quality can
  trip AASIST; a post-2019 neural TTS can slip past Gustking. Scores remain
  advisory — both profiles carry these measurements in `limitations`.
- Coverage of voice-clone services (ElevenLabs et al.) is still unmeasured —
  edge-tts is the only modern generator in this set.

## Generator-level recall sweep (2026-09-19)

Expanded the fake set to 7 edge-tts voices (en-US/GB + ko-KR, English and
Korean text) — the modern neural-TTS family. Threshold reference ~0.5.

| Sample | AASIST P(spoof) | wav2vec P(fake) | Read |
|---|---|---|---|
| real_extra.flac (real) | 0.019 | 0.103 | both correct |
| real_yesno.wav (real, 8 kHz) | **0.928 FP** | 0.068 | AASIST trips on telephone bandwidth |
| fake_sapi.wav (SAPI TTS) | 1.000 | 0.917 | both catch |
| tts_en_aria (edge-tts) | 0.088 | 0.253 | both miss |
| tts_en_guy (edge-tts) | 0.189 | 0.604 | wav2vec catches |
| tts_gb_sonia (edge-tts) | 0.385 | 0.620 | wav2vec catches |
| tts_ko_injoon, English text | 0.018 | 0.713 | wav2vec catches |
| tts_ko_injoon, Korean text | 0.000 | 0.214 | both miss |
| tts_ko_sunhi, English text | 0.685 | 0.858 | both catch |
| tts_ko_sunhi, Korean text | 0.019 | 0.435 | wav2vec partial |

**edge-tts recall: AASIST ~1/7, wav2vec ~4/7 at 0.5.** The first batch
suggested a Korean-language collapse (ko_injoon 0.71 -> 0.21), but a
second batch on different Korean text scored 0.57-0.91 — the drop is
sample/text-dependent, not a systematic Korean gap (see extended sweep
below).

Voice-clone services (ElevenLabs, RVC, so-vits-svc) remain **unmeasured** —
no checkpoint or API access locally; that gap stays explicitly open.

## Rejected candidate: phase-discontinuity heuristic (2026-09)

Hypothesis: neural-vocoder frame boundaries inflate wrapped Δ²φ in upper
STFT bins. Measured at the pipeline rate (16 kHz), median |wrapped Δ²φ|:

| Sample | Value |
|---|---|
| real_yesno (8 kHz speech) | 1.19 |
| real_flac (studio speech) | 0.80 |
| edge_en / edge_ko | 0.77 / 0.74 |
| fake_sapi | 0.72 |

The direction is **inverted** vs the hypothesis — vocoders produce
*smoother* phase than vocal cords — and clean studio speech (0.80)
overlaps the synthetic range (0.72-0.77). Margin ~0.05, sensitive to
sample rate and codec. **Rejected as a scoring signal**; the metric is
kept in `AudioFeatures.phase_discontinuity` as a recorded diagnostic
only.

## Korean-voice extended sweep (2026-09, second batch)

Same voices, different Korean text — result reverses the earlier
"Korean-language collapse" claim:

| Sample | AASIST | wav2vec |
|---|---|---|
| HyunsuMultilingual (Korean) | 0.09 miss | **0.91** catch |
| InJoonNeural (Korean) | 0.02 miss | **0.67** catch |
| SunHiNeural (Korean) | **0.94** catch | **0.73** catch |
| edge_ko (Korean) | **0.81** catch | **0.57** catch |

wav2vec recall on Korean text: 4/4 in this batch vs 0/2 in the first —
the collapse is **sample/text-dependent, not a systematic Korean gap**.
AASIST remains voice-dependent (2/4 missed). No threshold recalibration
is warranted; the ensemble disagreement itself remains the review signal.

## Extended voice sweep (2026-09-20, 39 edge-tts voices + 12 real)

Supersedes the earlier 7-voice estimate — the small sample understated
both members.

| Member | recall@50 (40 fake) | FPR@50 (12 real) | mean fake | mean real |
|---|---|---|---|---|
| AASIST | **0.72** | 0.08 | 65 | 10 |
| wav2vec XLSR | **0.78** | 0.00 | 69 | 10 |
| union (either ≥50) | **0.93** | 0.08 | — | — |
| ensemble mean ≥50 | 0.75 | — | — | — |

Per-language wav2vec recall: ko 3/3, en **11/19 (weakest)**, ja/zh/de/fr/
es/it/pt/nl ~1.00, ru 1/2. AASIST is complementary — catches most en
voices w2v misses. Both-missed set is only 3 English voices
(AnaNeural, BrandonNeural, LibbyNeural).

AASIST's single FP remains the 8 kHz telephone-bandwidth YESNO clip
(score 98) — low-bandwidth real speech stays its known weakness.

## Voice-clone coverage attempt (2026-09-20)

Coqui TTS (XTTS) cannot install on this environment (no distribution for
Python 3.12); RVC/so-vits need trained voice models; ElevenLabs needs an
API key. True voice-clone coverage remains **unmeasured** — edge-tts
neural TTS is the newest locally reproducible generator.
