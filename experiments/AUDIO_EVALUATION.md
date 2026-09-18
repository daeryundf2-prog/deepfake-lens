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
