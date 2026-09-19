# Coverage Roadmap — 2026-09-19

Status after the P4 member round (`76ff1f5`). This roadmap only lists work
whose value is measurable; every item names the artifact that proves it
worked. Measured current state is in `experiments/*_EVALUATION.md` and
`docs/CAPABILITY-REVIEW-AND-GAP-RESEARCH-2026-09-15.md`.

## Current coverage snapshot (measured)

| Threat | Today | Evidence |
|---|---|---|
| GAN image synthesis | strong | AIDE AUROC ~1.0 |
| Diffusion image (older) | medium | AIDE 55–85% |
| Commercial image gen (DALL-E 3/MJ) | weak | 3/4 caught on local sample |
| Faceswap / reenactment | **none** | both candidates measured-rejected |
| Lip-sync mismatch | works | SyncNet offset verified |
| Old TTS (SAPI/formant) | strong | AASIST 1.0 / wav2vec 0.92 |
| Modern neural TTS (edge-tts) | partial | AASIST 0.73 only |
| Voice clone (ElevenLabs/RVC) | **unmeasured** | no samples tested |
| Text (formal English) | ~0.98 | fakespot |
| Text (Korean/short/humanized) | heuristic only | recall ~0.17 |
| AI video gen (Sora/Kling/etc.) | none | no public detector |
| Provenance (C2PA/SynthID/PRNU/metadata) | strong | verified paths |

## Phase 1 — close the measurement gaps (low cost, this/next session)

### 1A. Voice-clone recall measurement
- Generate samples: edge-tts variants (done: 1 sample), free RVC/so-vits
  inference on a few source clips, ElevenLabs free tier if reachable.
- Score every sample through AASIST + wav2vec members.
- Deliverable: `experiments/AUDIO_EVALUATION.md` gains a per-generator
  recall table; profile `limitations` updated with real numbers.
- Success: every shipped audio member has a measured recall figure per
  generator family; unknown domains are explicitly marked unmeasured.

### 1B. Recompression robustness curve
- Take the existing eval corpora (faceswap faces, DALL-E set, audio set).
- Apply the field-realistic degradations: JPEG q75/q60, 0.5× resize,
  screenshot re-encode, mp3 96kbps re-encode, social-media-style crop.
- Score each member before/after; produce an AUROC/degradation curve.
- Deliverable: `experiments/RECOMPRESSION_EVAL.md` + band thresholds
  re-checked against degraded inputs.
- Success: documented per-member recall collapse point; ensemble band
  thresholds still honest under degraded inputs.

## Phase 2 — fill the biggest detection gap (faceswap)

### 2A. SBI self-trained face-manipulation detector
- `experiments/sbi.py` already builds self-blended training pairs; train
  EfficientNet-B0 (torchvision runtime already supports `.classifier`)
  on local real faces + SBI pairs. CPU-feasible at b0 scale.
- Validate on held-out SBI pairs AND the face-crop inference path
  (`crop_faces`) that was built for the rejected candidates.
- If local training proves too weak: try remaining public candidates
  (SBI official weights, MAT/forgery detectors on HF) through the same
  measured-screening gate before wiring.
- Deliverable: `models/face-manipulation-sbi-runtime.json` (supported only
  if AUROC clears ~0.8 on the held-out set) + eval numbers in
  `experiments/FACESWAP_EVALUATION.md`.
- Success: a real-vs-manipulated face discriminates at AUROC ≥0.8 on
  unseen manipulations; otherwise stays `supported:false` with numbers.

### 2B. Reenactment / morph candidates (research)
- Survey Face2Face/FOMM/morph detectors on HF; only proceed to wiring if
  a checkpoint passes the same local screening.
- Deliverable: registry entries with status notes even if all rejected.

## Phase 3 — forensic-layer additions (no model needed)

### 3A. Edit-localization heuristics
- ELA (error-level analysis), double-JPEG-compression detection,
  resampling-noise map — pure numpy/PIL, no weights.
- Surface as an `image` signal with a per-region heat hint; feeds the
  review-priority score, never a verdict.
- Deliverable: `deepfake_lens/ela.py` + scan signal + tests on spliced
  vs uniform-JPEG fixtures.

### 3B. Video temporal-consistency heuristic
- Frame-to-frame score jitter of existing image members, optical-flow
  magnitude outliers, audio-video timestamp skew — heuristic tier for
  AI-video/reenactment where no public model exists.
- Deliverable: `video_analysis` additions reporting temporal-disagreement
  signal, marked uncalibrated.

## Explicitly out of scope (documented, not silently missing)

- Provider attribution for text (Codex/Claude/Gemini/Grok/Kimi cannot be
  reliably separated from generic stylometry — recorded in
  TEXT_DETECTION_EVAL.md).
- A "Sora detector" — no public measured checkpoint exists; the temporal
  heuristic (3B) is the honest interim.
- Korean text recall — needs a Korean-anchored corpus/member; no public
  model measured well so far.

## Order of execution

1A → 1B (measurement, cheap) → 2A (SBI training attempt) → 3A → 2B → 3B.
Each phase ends with tests green, eval docs updated, and a commit — same
discipline as the previous rounds.
