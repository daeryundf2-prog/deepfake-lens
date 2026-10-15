# Third-party model licenses

Deepfake Lens code is MIT-licensed. Model **weights are never committed** —
each runtime profile in this directory fetches its checkpoint from upstream
and the upstream license applies to the weights, not the MIT license of this
repository. Verify each license before redistributing or commercializing.

The profile table is generated from each profile's `source_url` and
`license` fields by `scripts/sync_model_docs.py` (CI fails when it is stale).

<!-- BEGIN GENERATED: profiles (scripts/sync_model_docs.py — do not edit by hand) -->
| 프로필 | 업스트림 | 라이선스 |
|---|---|---|
| `aasist-runtime.json` | https://github.com/clovaai/aasist | MIT |
| `ai-image-swin-runtime.json` | https://huggingface.co/umm-maybe/AI-image-detector | cc-by-4.0 |
| `aide-runtime.json` | https://github.com/shilinyan99/AIDE | MIT |
| `community-forensics-frames-runtime.json` | https://huggingface.co/Red-had1911/deepfake-detector-onnx (generative_detector.onnx — Community Forensics ViT-S/384, arXiv:2411.04125, MIT) | MIT |
| `community-forensics-vit-runtime.json` | https://huggingface.co/Red-had1911/deepfake-detector-onnx (generative_detector.onnx — direct export of the original timm checkpoint model_v11_ViT_384_base_ckpt.pt; Community Forensics, Park & Owens U-Michigan, arXiv:2411.04125, MIT) | MIT |
| `fakespot-detector-runtime.json` | https://huggingface.co/fakespot-ai/roberta-base-ai-text-detection-v1 | Apache-2.0 |
| `sbi-effnet-runtime.json` | local training: experiments/train_detector.py --sbi --augment-degradation with enriched blending (polygon hull masks, affine misalignment) on FFHQ parquet + Wikimedia diverse portraits | 로컬 학습 체크포인트(업스트림 가중치 없음) — 학습 데이터 약관 적용, source_url 참조 |
| `sbi-frames-runtime.json` | local training: experiments/train_detector.py on self-blend corpus (see experiments/FACESWAP_EVALUATION.md); frame wrapper uses the committed sbi-effnet-runtime.json inner config | 로컬 학습 체크포인트(업스트림 가중치 없음) — 학습 데이터 약관 적용, source_url 참조 |
| `sd-turbo-det-runtime.json` | local training: experiments/train_detector.py --augment-degradation on experiments/gen_sdturbo_corpus.py output (280 SD-Turbo fakes) + Hemg/deepfake-and-real-images real class | 로컬 학습 체크포인트(업스트림 가중치 없음) — 학습 데이터 약관 적용, source_url 참조 |
| `wav2vec-deepfake-audio-runtime.json` | https://huggingface.co/Gustking/wav2vec2-large-xlsr-deepfake-audio-classification | apache-2.0 |
<!-- END GENERATED: profiles -->

## Assets that are not runtime profiles

| Asset | Upstream | License | Verified |
|---|---|---|---|
| `syncnet_v2.model` + `sfd_face.pth` (`lipsync.py`) | https://www.robots.ox.ac.uk/~vgg/software/lipsync/ (joonson/syncnet_python) | **Research/non-commercial** (VGG hosting terms) — fetch via `scripts/fetch_syncnet.py`, do not redistribute | 2026-09 |
| `speechbrain/spkrec-ecapa-voxceleb` (`audio.py`) | https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb | Apache-2.0 | model card (2026-09) |

Profiles removed in phase 0 are listed in `docs/MODEL-REJECTIONS.md`.

Every asset in this section and the next is registered in `assets.json`
(R15-3: name, sha256, source, license) and loaded only when its bytes match
the pin.

## Bundled detector assets

- `haarcascade_frontalface_default.xml` — OpenCV frontal-face cascade
  (OpenCV, BSD 3-Clause). Vendored because OpenCV 5.x no longer ships the
  cascade XML with `cv2.data`; used by `face._detect_faces` before the
  MediaPipe fallback.

## C2PA trust list (techniques-2026)

- `c2pa-trust-list.pem` — the C2PA conformance trust list (30 trust anchors,
  list issue date 2026-08-05, next update 2027-08-05), copied byte for byte
  from https://raw.githubusercontent.com/c2pa-org/conformance-public/main/trust-list/C2PA-TRUST-LIST.pem
  (upstream commit `70ec46e16962b81e70795fcb50a05e0128329a20`, retrieved
  2026-10-15), sha256
  `75cacc98b79ecac33713c7ecfb58d4a0ef383f3c1f886e7409f9e37e8664aea5`.
  License: **CC BY 4.0** — © Coalition for Content Provenance and
  Authenticity (c2pa-org/conformance-public `LICENSE`); unmodified.
  `c2pa-trust.json` records the same facts; `deepfake_lens/c2pa.py` checks
  the file's sha256 against it before every use and fails the `c2pa` check
  when they differ. An operator adds their own anchors with
  `DEEPFAKE_LENS_C2PA_TRUST_ANCHORS=<PEM 파일>` (recorded in every C2PA
  item and coverage reason). To update: replace both files in one commit,
  raise `scan_cache.OUTPUT_FORMAT_GENERATION` and record the golden output.

## Digital source type vocabulary and SDXL watermark message (techniques-2026)

- `deepfake_lens/digital_source_type.py` restates the IPTC NewsCodes
  "Digital Source Type" vocabulary (release 2024-10-23, 20 concepts,
  https://cv.iptc.org/newscodes/digitalsourcetype/) — © IPTC, **CC BY 4.0**;
  codes and English labels are IPTC's, the Korean labels are ours.
- `deepfake_lens/wm_imwatermark.py` reimplements the `dwtDct` *decoder* of
  ShieldMnt/invisible-watermark 0.2.0 (`imwatermark/maxDct.py`, **MIT**) with
  numpy + OpenCV; no code is copied. `watermark-registry.json` records the
  SDXL 48-bit message published in huggingface/diffusers
  (`pipelines/stable_diffusion_xl/watermark.py`, Apache-2.0) and
  Stability-AI/generative-models (`sgm/inference/helpers.py`, MIT).

