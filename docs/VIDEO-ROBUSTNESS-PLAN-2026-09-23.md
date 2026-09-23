# Video / lip-sync / recompression robustness plan (2026-09-23)

Request: before real photo/video evidence arrives, close the four named
gaps — (1) a real video detector, (2) a learned lip-sync model,
(3) recompression robustness, (4) screen-recording robustness.

Discipline: measure first, gate, reject — same rule that prevented three
bad wirings earlier. Every member stays advisory; a low score is never
evidence of authenticity.

## Current state (verified)

- `video_analysis.py` — optical-flow/blur/flicker heuristics exist.
- `lipsync.py` — SyncNet path stubbed; `syncnet-python 0.2.2` IS
  installed and `models/syncnet_v2.model` + `sfd_face.pth` exist —
  but the path has never been verified end-to-end.
- `rppg.py` — CHROM pulse + phase coherence exists.
- `video-frames` runtime — per-frame image scores, explicitly not
  temporal.
- Recompression eval exists (RECOMPRESSION_EVAL.md) — JPEG q50 destroys
  the sd-turbo fingerprint; screen-recording sim exists only as a
  flat ffmpeg re-encode.

## Gap → plan

### V1 — Verify the learned SyncNet lip-sync path

- Generate a talking-head-ish test video (face image + TTS audio, or a
  real clip if available) and run `analyze_lipsync`.
- Create a misaligned negative by shifting audio +400 ms — check the
  confidence/offset actually moves.
- If syncnet-python fails on this stack, record the failure and keep
  the heuristic path as fallback (it already is).
- Gate: SyncNet must produce different offset/confidence on aligned vs
  shifted audio, else mark the path unverified.

### V2 — Face-track temporal consistency (`face_track.py`)

What per-frame scoring cannot see: a face swap that is *internally*
consistent per frame still drifts — identity embedding, landmark
geometry, and box size jitter differently than a real tracked face.

- Sample frames with cv2; detect face per frame (existing mediapipe
  landmarker); build a track.
- Features: consecutive-frame embedding cosine distance distribution
  (mean/std/max jump), landmark position jitter (normalized by box
  size), face-box area/position smoothness.
- Negative corpus: build fake videos by compositing a *different*
  source face per frame (or per segment) onto a real clip — the
  swap boundary should produce measurable drift/jitter.
- Positive corpus: real talking-head clips (generate from face images
  + motion? Prefer real short clips — camera self-video if available;
  else document the positive-corpus gap).
- Gate: report AUROC on the synthetic corpus; wire as
  `video-track` signal only if it separates; else keep measurement-only.

### V3 — Compression-invariant retraining (sd-turbo-det v2)

B2 measured: JPEG q50 destroys the learned fingerprint (AUROC 0.51).

- Retrain the EfficientNet-B0 detector on the existing SD-Turbo corpus
  with degradation augmentation applied to BOTH classes equally
  (JPEG q40-95, resize, mild blur) — the augmentation must be
  class-balanced so the model cannot learn "compressed = real".
- Measure: clean holdout AUROC, q50 holdout AUROC, real-photo FPR.
- Gate: adopt only if q50 AUROC improves without clean-set regression.

### V4 — Screen-recording simulation + measured degradation

Current "screen" sim is a flat re-encode. Real screen capture adds:
moiré/resampling, perspective skew (if filmed), refresh-rate
brightness pulsing, downscale-upscale.

- Build `scripts/sim_screen_capture.py`: resize to a display grid and
  back (resampling moiré), optional small perspective warp, brightness
  banding, then H.264 encode.
- Run the image members + video-frames over the simulated corpus;
  record which members survive.
- Output goes into RECOMPRESSION_EVAL.md; no weight changes unless a
  member shows a stable measured advantage.

## Explicit non-goals

- No claim of definitive authenticity; all signals advisory.
- No gated datasets (FaceForensics++/KoDF) — credential-blocked.
- No commitment of new weight files (checkpoints stay ignored).

## Results (executed 2026-09-23)

- **V1 SyncNet — DONE, verified.** syncnet-python runs on CPU; recovers
  an injected +400 ms shift exactly (-10f @25fps, conf 5.1→4.2,
  score 0→40). Fixed fallback: S3FD track-miss now drops to the
  envelope heuristic instead of returning unavailable (heuristic then
  flagged the shift at score 25 on the WIKITONGUES clip).
- **V2 face_track — DONE, partial.** `deepfake_lens/face_track.py`:
  embedding drift + landmark jitter + box smoothness. Jitter-mode swaps
  score 90 vs real 0 (10x separation on all metrics). **Evasion found
  and documented**: smooth single-identity pastes evade re-detection
  entirely (0 faces → unavailable, not clean).
- **V3 recompression retrain — REJECTED.** Heavier class-balanced
  degradation produced a compression-invariant but non-discriminating
  model (real ~40 ≈ fake ~45). Existing sd-turbo-det was re-measured
  as already robust on its class (dalle q30: 100/84/100, real FPR 0) —
  the earlier "collapse" was the out-of-class Hemg face-manipulation
  set.
- **V4 screen-recording — DONE.** `scripts/sim_screen_capture.py`
  (moiré + banding + perspective + H.264). sd-turbo-det survives
  (dalle 100/82); CF-ViT collapses to 0; face_track stays clean on
  real but the swapped video's faces become undetectable →
  unavailable, confirming screen-recording as an evasion vector.
