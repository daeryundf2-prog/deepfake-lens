> ⚠ 미검증(n 부족 또는 재현 불가) — 2차 계획 WP-I 참조. 아래 수치는 클래스당 200개 미만이거나 코퍼스가 재현 불가능하여 증거로 사용할 수 없다.

# Labeled evaluation corpus — layout, provenance, regeneration

The corpus lives at `eval_corpus/` (gitignored — content is derived data,
only the build scripts are committed). It backs `experiments/eval_all.py`
and `experiments/suggest_weights.py`.

## Layout and provenance

| Path | n | Source | License/status |
|---|---|---|---|
| `text/real/ko_wiki_*.txt` | 398 | Korean Wikipedia paragraphs (wikimedia/wikipedia 20231101.ko, streamed) | CC BY-SA; derived excerpts, not redistributed |
| `text/fake/ko_qwen05_*.txt` | ~88 | Local Qwen2.5-0.5B-Instruct, 8 prompt styles × 25 topics | locally generated |
| `text/fake/ko_qwen15_*.txt` | ~29 | Local Qwen2.5-1.5B base, prompt-completion | locally generated; cross-generator holdout |
| `audio/real/*.wav/flac` | ~7 | torchaudio YESNO (8 kHz telephone speech) + LibriSpeech sample | public domain / CC BY 4.0 |
| `audio/fake/*.mp3/wav` | ~9 | edge-tts (ko/en voices) + Windows SAPI | locally generated |
| `image/real/` | 26 | lenna + 25 held-out samples from Hemg/deepfake-and-real-images (HF, real class) | dataset license per HF page |
| `image/fake/` | 29 | 4 DALL-E samples from Wikimedia Commons + 25 held-out Hemg fakes | AI-generated; Hemg provenance per HF page |
| `face/real/` | 54 | Wikimedia vintage/diverse portraits + xdomain crops (via `scripts/fetch_diverse_faces.py`) | public domain portraits |

Face positives are **synthesized at eval time** — `eval_all.py` runs SBI
self-blends on `face/real/` so the fake class never goes stale relative to
the training recipe.

## Regenerate

```bash
python scripts/build_eval_corpus.py --out eval_corpus --parts text,audio
python scripts/fetch_diverse_faces.py --out eval_corpus/face_src  # then copy crops into face/real
```

Human/real sides need network; AI/TTS sides need local CPU inference
(Qwen ~1 GB download once) plus network for edge-tts.

## Run the evaluation

```bash
# everything
python experiments/eval_all.py --corpus eval_corpus --report report.json --md report.md

# one modality
python experiments/eval_all.py --corpus eval_corpus --modality audio

# measurement-derived weight suggestions (review before writing)
python experiments/suggest_weights.py report.json
python experiments/suggest_weights.py report.json --write   # updates profiles
```

## Caveats baked into interpretation

- `text/fake` is dominated by **one generator family (Qwen)** — a detector
  that aces this corpus may still miss other generators. The KoELECTRA
  experiment measured exactly this failure (holdout AUROC 1.0, cross-gen
  recall 0.10). Treat high corpus scores as necessary-not-sufficient.
- `image/fake` now mixes DALL-E (4) and Hemg parquet fakes (25) — the Hemg
  generator mix is undocumented on the HF page, so treat image metrics as
  cross-generator-agnostic rather than per-generator.
- `face/` positives are SBI self-blends — coverage of Deepfakes/
  FaceShifter-class pipelines is inferred, not measured.
- Small n means wide confidence intervals; AUROC differences <0.1 on
  n<100 are noise-level.

## Per-layer threshold provenance (layer-thresholds-v1)

Heuristic decision cutoffs (`faceswap_seam.*`, `face_track.*`) are module
defaults that a `ThresholdProfile` JSON can override at scan/eval time
(`--thresholds`). A profile is **provisional** while `samples <
MIN_CALIBRATION_SAMPLES` (20) and detectors must surface that status in
their `limitations` — a provisional profile is a hypothesis, not a
measurement.

Minimum corpus requirements before shipping a non-provisional profile:

| Layer | Keys | Min samples | Required coverage |
|---|---|---:|---|
| `faceswap_seam` | seam_ratio_high/low, noise_ratio_low/high, chroma_delta, corneal_asymmetry_px, score_high/medium | 40 labeled (>=20 real faces incl. aged/damaged portraits + >=20 swaps) | jpeg75 + 50%-resize variants; aged/damaged portrait FPR is a known failure domain — keep them in every eval set |
| `face_track` | drift_mean_*, jitter_*, area_delta, score_* | 20 labeled videos (>=10 real talking-head, >=10 swaps incl. smooth single-identity pastes — the measured evasion case) | landmark-aligned deepfakes evade re-detection; include at least one |
| score (global) | CalibrationProfile / ScoreCalibrator | 20 samples, >=5 per class | existing `calibrate` machinery |

Writing a profile: `calibration.write_threshold_profile` — include
`samples`, `dataset_fingerprint`, `measured_at`, and the `metrics` block
(AUROC/FPR at the chosen operating point). `cli --thresholds` warns to
stderr when the profile is provisional or fails to load.

## Corpus acquisition plan (threshold de-provisionalization)

The current corpus cannot de-provisionalize the seam/track thresholds:
`face/fake` positives are SBI self-blends generated at eval time, so the
fake side is not a measured faceswap population. Target corpora:

| Layer | Corpus | Why | Access |
|---|---|---|---|
| `faceswap_seam` | FaceForensics++ (FaceSwap/Deepfakes subsets) | canonical faceswap seam artifact benchmark | research agreement, per-user approval — cannot be committed or auto-fetched |
| `faceswap_seam` | KoDF | Korean-domain distribution match for the actual user base | KODF institutional application (ko df dataset, data.kosa.or.kr) |
| `faceswap_seam` | Celeb-DF v2 | harder, boundary-refined swaps | research agreement |
| `face_track` | DFDC preview / KoDF video subset | temporal tracks with per-video labels | Kaggle/institutional agreement |
| `audio` | ASVspoof 2019/2021 LA | same benchmark AASIST reports against | public download |

None of these can be committed to the repo (license) — the corpus stays
gitignored under `eval_corpus/` like today, and the measurement script
writes its sha256 fingerprint into the profile so "which corpus produced
these numbers" stays answerable without redistributing data.

## Measuring seam thresholds

```bash
python experiments/eval_seam_thresholds.py \
    --real-dir eval_corpus/face/real --fake-dir eval_corpus/face/ffpp \
    --profile-out models/thresholds.faceswap_seam.json \
    --report experiments/seam_eval_report.json
```

`eval_seam_thresholds.py` scores each sample with the production
`analyze_faceswap_seam`, records all four raw metrics, fits cutoffs at
the target real-class FPR (`--fpr-high` 1%, `--fpr-medium` 10%), and
writes a `layer-thresholds-v1` profile with the corpus fingerprint and
per-metric AUROC. Metrics that cannot be fit keep the module defaults
and are marked `fell_back_to_default` in the report — the profile never
implies a measurement that did not happen.

Cross-domain check before shipping: fit on one corpus, evaluate the
profile on a held-out second corpus (in-domain numbers alone are a known
overfit — the SBI candidate measured 0.84 in-domain vs 0.79 cross).

## Measurement log

### 2026-10-05 — SBI self-blend corpus (Wikimedia portraits)

- Corpus: `scripts/fetch_diverse_faces.py` → 66 real / 66 fake (SBI
  self-blends) + 21/21 held-out val; fingerprint
  `446515c21e6dec28a2f9086c97d71ad6a2d5582338fcf351e22b35e584eb89ce`
- Result: `analyze_faceswap_seam` aggregate score **AUROC 0.470**,
  EER 0.542 on 123 scored samples — below the 0.55 validation floor.
  The heuristic seam metrics do **not** discriminate SBI self-blends on
  this diverse-face population; fitted cutoffs were rejected and the
  shipped `models/thresholds.json` stays provisional with empty values.
- Implication: de-provisionalizing `faceswap_seam` requires a real
  faceswap corpus (FF++/KoDF per the table above) — SBI self-blends do
  not produce the boundary artifacts the layer looks for.
- Also fixed: OpenCV 5.x no longer ships `haarcascade_frontalface_default.xml`
  under `cv2.data`; `face._detect_faces` raised `cv2.error` on every frame.
  The cascade is now vendored under `deepfake_lens/models/` (BSD-3, see
  models/NOTICE.md) and detection is crash-safe when cascades are absent.

### 2026-10-05 — inswapper_128 real-faceswap corpus (de-provisionalized)

- Corpus: same Wikimedia portrait fetch with `--save-full` → 90 real
  frames; `scripts/make_faceswap_corpus.py` ran inswapper_128 (buffalo_l
  embeddings) pair-wise across identities → 88 swapped frames. Fingerprint
  `d77809e5eddfd968afb1336099a09588e50e9d307448e2db292a634509d35aca`
- Result: `faceswap_seam` aggregate **AUROC 0.654** (train 0.633 /
  held-out val 0.714 — signal replicates, not a fit artifact), EER 0.42,
  coverage 0.97 on 173 scored samples
- Per-metric: `noise_discrepancy_ratio` dominates (AUROC 0.87 both
  splits) — the real faceswap boundary noise is what this layer actually
  detects; `seam_ratio` modest (0.61), `chroma`/`corneal` near chance
- `models/thresholds.json` now ships **MEASURED** (provisional=false)
  with fitted cutoffs — the first de-provisionalized layer
- Honest caveat: fakes are all inswapper_128 output — thresholds
  measure detectability of *this* generator's artifacts. GAN-native
  (FaceFusion-style) or video-level (DFL) fakes may shift the optimum;
  the corpus fingerprint records exactly which distribution produced
  these numbers.
- SBI contrast: self-blends scored 0.47 — confirms SBI does not produce
  the boundary artifacts this layer keys on; SBI alone could never have
  validated it.
