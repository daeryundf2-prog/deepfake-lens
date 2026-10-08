# 제거된 모델 프로필 기록 (Model rejections)

0단계(WP-C, G2/G33)에서 `deepfake_lens/models/`에서 삭제한 런타임 프로필과
삭제 근거가 된 측정 기록이다. 각 줄은 해당 프로필의 `reason`/`limitations`/
`notes` 또는 `experiments/*.md`의 측정 기록을 옮긴 것이며, 수치는 당시
소규모 로컬 측정(n 부족, 재현 불가)이라 **미검증** 상태다. 후보를 다시
들이려면 WP-I 측정 게이트(클래스당 200건, AUROC 95% CI 하한 0.85)를
통과한 `measured_on` 기록과 `pin`이 있는 새 프로필로 추가한다.

| 삭제된 프로필 | 모델 | 삭제 사유 / 측정 기록 (한 줄) |
|---|---|---|
| `korean-roberta-text-detector-runtime.json` | klue/roberta-base | G2: 분류 헤드가 없는 MLM 체크포인트(klue/roberta-base)를 가리켜 `from_pretrained`가 무작위 초기화 헤드를 붙였고, 가중치 1.2의 유일한 한국어 멤버라 한국어 글에서 무작위 점수가 HIGH를 만들 수 있었음 — 측정 기록 없음. |
| `cnndetection-runtime.json` | CNNDetection (CVPR 2020) ResNet-50 blur+jpg | 2026-09 로컬 평가에서 알려진 AI 이미지를 포함한 모든 입력에 ~0점(항상 "real"); CIFAKE 32px AUROC 0.476 (experiments/RECOMPRESSION_EVAL.md, AIDE_EVALUATION.md). |
| `univfd-runtime.json` | UnivFD (CVPR 2023) CLIP ViT-L/14 선형 프로브 | 2026-09 로컬 평가에서 실사 포함 모든 입력에 30–36점으로 평탄 — 판별력 없음; CIFAKE 32px AUROC 0.468 (RECOMPRESSION_EVAL.md, AIDE_EVALUATION.md). |
| `qwen-ppl-runtime.json` | Qwen2.5-0.5B 퍼플렉서티 스크린 | 30건 시드 코퍼스(2026-09-16)에서 사람 글 PPL 8–19가 AI 글 7–19와 겹쳐 어떤 기준점으로도 분리 불가; 한국어 사람 글 FPR 0.97 (TEXT_DETECTION_EVAL.md). |
| `binoculars-runtime.json` | Binoculars (Qwen2.5-0.5B/1.5B) | 2026-09-16 시드 코퍼스에서 다듬어진 사람 글이 전부 100점(비율 0.71–0.84가 AI 0.67–0.79와 겹침) — 문체 정제도를 추적할 뿐 AI 여부가 아님; 한국어 사람 글 FPR 1.00 (TEXT_DETECTION_EVAL.md). |
| `openai-detector-runtime.json` | OpenAI GPT-2 output detector (roberta-base) | GPT-2(2019) 출력으로 학습 — 최신 LLM 글에서 성능 저하를 모델 카드가 명시; 한국어 사람 글 FPR 0.57 (TEXT_DETECTION_EVAL.md). 기본 텍스트 엔진이었으나 `DEFAULT_TEXT_ENGINE_PROFILE = None`으로 해제. |
| `aide-frames-runtime.json` | AIDE 프레임 단위 (video-frames) | 2026-09-20 합성 정지 프레임 영상에서 이미지 멤버의 실패를 그대로 상속: 실사 86점(오탐), DALL-E 프레임 10–19점(미탐), crf32/화면 재촬영 재압축 시 전 점수 붕괴 (RECOMPRESSION_EVAL.md). |
| `umm-maybe-detector-runtime.json` | umm-maybe/AI-image-detector | G33: 같은 가중치가 `ai-image-swin-runtime.json`(supported:true)과 이 프로필(supported:false)로 중복 존재. 2026-09-21 측정에서 id2label {0:artificial,1:human} 기준 P(artificial)이 DALL-E와 실사 모두 0.01–0.26 — 어느 라벨 해석으로도 분리 실패. 남은 `ai-image-swin` 프로필도 supported:false. |
| `melodymachine-w2v2-runtime.json` | MelodyMachine/Deepfake-audio-detection-V2 | 2026-09-21 측정: ONNX 내보내기는 edge-TTS 가짜와 LibriSpeech 실음성 모두 한 클래스(~1.0)로 포화, HF 경로는 실음성이 TTS보다 높은 가짜 점수(yesno 73 vs edge_ko 50) — 반전/사용 불가 (AUDIO_EVALUATION.md). |
| `dire-runtime.json` | DIRE (ICCV 2023) | 점수를 내지 않는 문서용 자리표시자: ADM 확산 모델과 이미지별 역변환 파이프라인이 필요하고 단일 검출 가중치가 없음 — 런타임 미구현. |
| `genconvit-face-runtime.json` | GenConViT ED (ONNX) | 2026-09-21 eval_corpus/face(SBI 자기혼합 vs 다양한 실사 초상, 98건)에서 AUROC 0.49, FPR@50 0.42 — 무작위 분리 + 실사 얼굴 다수 오탐 (FACESWAP_EVALUATION.md). |
| `faceswap-ffpp-runtime.json` | Xicor9/efficientnet-b0-ffpp-c23 | 실사 Lenna 얼굴 96% 가짜, SBI 조작본은 더 낮은 86% — 신호 반전; 모델 카드 전처리(Resize+ToTensor) 일치 후에도 지속. 업스트림이 법집행·포렌식 용도 사용 금지 명시 (FACESWAP_EVALUATION.md). |
| `faceswap-ffpp-frames-runtime.json` | 위 체크포인트의 프레임 단위 래퍼 | 내부 체크포인트가 위 측정으로 기각되어 함께 삭제. 체크포인트 다운로드 스크립트 `scripts/fetch_faceswap.py`도 삭제. |
| `face-manipulation-vit-runtime.json` | dima806/deepfake_vs_real_image_detection | 얼굴 중심 SBI 45건(초상 5 × 조작 × 3변형)에서 AUROC ~0.51, 노년 초상이 99–100% 가짜로 나와 FPR@50 ~0.47 — 학습 과제(완전 생성 얼굴)가 페이스스왑과 다름 (FACESWAP_EVALUATION.md). |
| `face-manipulation-vit-frames-runtime.json` | 위 모델의 프레임 단위 래퍼 | 내부 모델이 위 측정으로 기각되어 함께 삭제. |
