# PerTh fixtures (TB-2)

- `synthetic-clean.wav` — a synthetic voiced signal (24 kHz, 4 s, 16-bit mono: 24 harmonics of a 140 ± 30 Hz glottal
  pitch with slow amplitude modulation, plus noise; seed 20261017), made for this test — no recorded speech.
- `synthetic-perth.wav` — the same signal after `perth.PerthImplicitWatermarker().apply_watermark(sig, sample_rate=24000)`
  (resemble-perth 1.0.1, MIT — the call Chatterbox makes on every output), written as 16-bit PCM.
- `reference.json` — the package's own `get_watermark(round=False)` confidence for both files (torch 2.11 + torchaudio 2.11,
  in a throwaway environment: no torchaudio build exists for the gate's torch 2.14).

Built with the reference script recorded in the TB-2 commit message; the same run compared `wm_perth` with the package on
240 LibriSpeech dev-clean utterances (originals and watermarked copies): largest difference 3.0e-7.
