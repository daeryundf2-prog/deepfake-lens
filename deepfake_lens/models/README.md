> ⚠ 미검증(n 부족 또는 재현 불가) — 2차 계획 WP-I 참조. 아래 수치는 클래스당 200개 미만이거나 코퍼스가 재현 불가능하여 증거로 사용할 수 없다.

# Model zoo

Runtime profiles for published synthetic-media detectors (image, audio, text
and per-frame video). Profiles are committed; **weights never are** (license +
size — `models/*.pth` and friends are gitignored). A scan treats every
profile as optional: a missing checkpoint or a missing optional dependency is
recorded as a skipped check with the fetch hint in `limitations`, never a
crash.

## Phase-0 status

- **Every profile is `supported: false`.** None has passed the measurement
  gate (≥200 samples per class, AUROC 95% CI lower bound ≥ 0.85 — WP-I), so
  the adapter skips them with the profile's `reason`. `measured_on` is the
  slot WP-I fills with the corpus id, manifest hash and CI.
- **No weight loads without a pin** (G9/G10). Each profile carries a `pin`
  object: `{"sha256": "<64 hex>"}` for a local checkpoint (re-hashed on every
  load) or `{"revision": "<40 hex commit>"}` for a Hugging Face hub model
  (passed as `revision=` to every `from_pretrained`). An empty pin is
  recorded as a failed check `미고정 프로필`; a mismatch as
  `무결성 불일치: …`. Either makes the verdict `판단 불가`.
- Fill a pin with `deepfake-lens vendor-weights pin <profile>` (local file:
  sha256 of the checkpoint; hub model: the current commit via
  `huggingface_hub`, or `--revision <sha>` offline).
- Profiles removed in phase 0 and the measurements behind each removal are
  listed in `docs/MODEL-REJECTIONS.md`.

The table below is generated from the profiles by
`scripts/sync_model_docs.py`; CI fails when it is stale.

<!-- BEGIN GENERATED: profiles (scripts/sync_model_docs.py — do not edit by hand) -->
| 프로필 | 표시 이름 | 검출기(식별자 name) | 모달리티 | 런타임 | 가중치 | pin | supported | measured_on | 표본 내 보정(G28) |
|---|---|---|---|---|---|---|---|---|---|
| `aasist-runtime.json` | AASIST 음성 위조 탐지기(ASVspoof2019-LA) | AASIST (Interspeech 2022) ASVspoof2019-LA anti-spoofing | audio | `aasist` | 로컬 `aasist.pth` | 미고정 (sha256 비어 있음) | false | 없음 | — |
| `ai-image-swin-runtime.json` | Swin-large 생성 이미지 탐지기(umm-maybe) | Swin-large AI-vs-human image detector (umm-maybe) | image | `hf-image-classifier` | 허브 `umm-maybe/AI-image-detector` | 미고정 (revision 비어 있음) | false | 없음 | — |
| `aide-runtime.json` | AIDE 생성 이미지 탐지기(ICLR 2025, progan_train) | AIDE (ICLR 2025) progan_train | image | `aide` | 로컬 `aide_progan_train.pth` | 미고정 (sha256 비어 있음) | false | 없음 | — |
| `community-forensics-frames-runtime.json` | CommunityForensics ViT-S/384 영상 프레임 생성 탐지기 | CommunityForensics ViT-S/384 per-frame (video-frames runtime) | video | `video-frames → onnx` | 로컬 `checkpoints/community-forensics/generative_detector.onnx` | 미고정 (sha256 비어 있음) | false | 없음 | — |
| `community-forensics-vit-runtime.json` | CommunityForensics ViT-S/384 생성 이미지 탐지기 | CommunityForensics ViT-S/384 (OpenSight) general AI-image detector | image | `onnx` | 로컬 `checkpoints/community-forensics/generative_detector.onnx` | 미고정 (sha256 비어 있음) | false | 없음 | — |
| `fakespot-detector-runtime.json` | Fakespot AI 텍스트 탐지기(roberta-base) | Fakespot AI text detector (roberta-base) | text | `hf-text-classifier` | 허브 `fakespot-ai/roberta-base-ai-text-detection-v1` | 미고정 (revision 비어 있음) | false | 없음 | — |
| `sbi-effnet-runtime.json` | SBI EfficientNet-B0 얼굴 조작 탐지기(로컬 학습 v2) | SBI-trained EfficientNet-B0 v2 (local, enriched self-blend: polygon/affine masks + diverse portraits) | image | `torchvision` | 로컬 `sbi-effnet-b0.pth` | 미고정 (sha256 비어 있음) | false | 없음 | 예 (score_bias=35) |
| `sbi-frames-runtime.json` | SBI EfficientNet-B0 영상 얼굴 프레임 탐지기 | SBI EfficientNet-B0 on per-frame face crops (video-frames runtime) | video | `video-frames → torchvision` | 로컬 `sbi-effnet-b0.pth` | 미고정 (sha256 비어 있음) | false | 없음 | 예 (score_bias=35) |
| `sd-turbo-det-runtime.json` | SD-Turbo 생성 이미지 탐지기(로컬 EfficientNet-B0) | Local EfficientNet-B0 trained on SD-Turbo fakes vs Hemg reals | image | `torchscript` | 로컬 `sd-turbo-det-b0.torchscript` | 미고정 (sha256 비어 있음) | false | 없음 | — |
| `wav2vec-deepfake-audio-runtime.json` | Wav2Vec2-XLSR 딥페이크 음성 분류기(In-the-Wild) | Wav2Vec2-XLSR deepfake audio classifier (Gustking, In-the-Wild) | audio | `hf-audio-classifier` | 허브 `Gustking/wav2vec2-large-xlsr-deepfake-audio-classification` | 미고정 (revision 비어 있음) | false | 없음 | — |
<!-- END GENERATED: profiles -->

Profiles declare a `modality` (`image`/`audio`/`text`/`video`); a scanned file
only runs profiles matching its own modality, so the image detectors, the
audio members and the text classifier never trip over each other in a mixed
directory scan.

## Multi-model runs

`--model-path` accepts a directory, a list, or a profile-set JSON
(`type: deepfake-lens-profile-set-v1` with a `profiles` array of paths
relative to the set file). With more than one profile every member runs and
the result reports:

- `models[]` — per-model `available`/`score`/`confidence`/`detail`; each
  member also gets its own `model:<name>` coverage entry
- aggregate `score` — the `ensemble_weight`-weighted mean of the members
  that contributed (each profile may declare `ensemble_weight`, default 1.0)
- agreement — `detail` reports the spread of the contributing members;
  disagreement (>20 points) drops confidence to `low` and adds a limitation.
  A member excluded by the language gate (`trained_languages` without `ko`
  on Korean-dominant text) is reported as skipped and takes no part in the
  aggregate, the spread or the agreement.

Example:

```bash
deepfake-lens scan folder/ --model-path models/   # every profile in models/
```

Default scans auto-discover `aide-runtime.json` for images and
`aasist-runtime.json` + `wav2vec-deepfake-audio-runtime.json` for audio
(`--no-default-engine` opts out). There is no default text model. In phase 0
the defaults are `supported: false`, so they are recorded as skipped.

## Other assets (not runtime profiles)

- `face_landmarker.task` — MediaPipe Tasks-API model for `face.py`'s
  measured-landmark path on tasks-only mediapipe builds (>=0.10.30 / 1.x).
  Fetch with `scripts/fetch_facelandmarker.py` or point
  `DEEPFAKE_LENS_FACE_LANDMARKER` at a local copy. Absent → the legacy
  FaceMesh extra or the labelled box-ratio estimate.
- `syncnet_v2.model` + `sfd_face.pth` — pretrained SyncNet (LRS2) lip-sync
  model and its S3FD face detector for `lipsync.py`'s `--deep-signals`
  video layer. Fetch both with `scripts/fetch_syncnet.py` (Oxford VGG
  hosting, research-only license — do not redistribute). Absent → the
  zero-asset envelope/mouth-correlation heuristic.

## Honesty notes

- Scores are **prioritization signals, not truth labels** — an uncalibrated
  model score never decides the verdict (see
  `docs/deepfake-lens-json-contract.md`, decision rule 4).
- Each profile's `limitations` field is surfaced on every result it touches;
  read them before citing a score.
