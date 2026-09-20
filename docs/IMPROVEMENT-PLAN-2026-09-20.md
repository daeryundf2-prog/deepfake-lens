# 개선 계획 — 측정 기반 차기 로드맵 (2026-09-20)

기준 상태: 536 tests passing, `1723fde` push 완료. 이 문서는 **실측된 실패/공백**만 다룬다.
모든 항목은 "측정 → 문서 → 테스트 → 커밋" 사이클과 실측 게이트(기준 미달 시 기각+기록)를 따른다.

## 현재 실측 상태 요약

| 영역 | 강함 | 약함/공백 (측정값) |
|---|---|---|
| 이미지 | GAN 얼굴(AIDE AUROC 0.95+), SBI faceswap(인도메인 FPR 0.07) | DALL-E 3급 recall ~1/4, CIFAKE급 32×32 전멸, 재압축 시 전 멤버 저하 |
| 얼굴 | 얼굴검출 98%, SBI 크로스도메인 FPR 0.07 | SBI recall 0.48로 하락(정밀도 우선 트레이드오프), reenactment/모핑 탐지기 없음 |
| 오디오 | 구형 TTS(SAPI) 90%+, LibriSpeech 실음성 정확 | edge-tts급 recall 1/7~4/7, 보이스클론 미측정, AASIST 저대역폭 오탐 |
| 텍스트 | 영어 AI문서(fakespot ~0.98), 언어게이트 작동 확인 | 한국어 신경망 커버리지 0(게이트로 en 멤버 전부 제외됨), 단일생성기 분류기 과적합 확인됨 |
| 영상 | 프레임 집계, 플리커 휴리스틱, 립싱크 | AI 생성 영상 전용 탐지기 없음, 재압축/화면녹화 조건 미측정 |
| 포렌식 | C2PA, ELA, 주파수, PRNU, 메타데이터 | — (비신경망 층이 가장 신뢰도 높음) |

---

## Phase A — 평가 인프라 영구화 (최우선, 선행 과제)

지금까지 축적된 라벨 코퍼스(얼굴 724+/크로스도메인 86, 오디오 12+, 한국어 텍스트 398+175+39, 이미지 DALL-E/실사)가
임시 디렉터리에만 존재한다. **코퍼스를 잃으면 측정 기반이 사라진다.**

| 항목 | 내용 | 상태 |
|---|---|---|
| **A1 평가 하네스** | `experiments/eval_all.py` — 라벨 코퍼스 디렉터리 → 전 멤버 AUROC/FPR/recall/커버리지 표. face 모댈리티는 crop_faces 멤버 + SBI 양성 자동합성 | ✅ 완료 — 베이스라인 `experiments/baseline_eval_2026-09-20.json` |
| **A2 코퍼스 재현 스크립트** | `scripts/build_eval_corpus.py` — 위키/TTS/YESNO 재현. 코퍼스는 `eval_corpus/`(581파일, gitignore)에 보존 | ✅ 완료 — `experiments/EVAL_CORPUS.md` |
| **A3 데이터 기반 가중치** | `experiments/suggest_weights.py` — `4·(AUROC−0.5)·(1−FPR@50)` 클램프. AUROC만으론 "전부 플래그" 멤버가 과대평가되어 FPR 페널티 필수임을 실증 | ✅ 완료 — advisory 모드 |

## Phase B — 탐지 성능 개선 (측정된 약점 순)

| 항목 | 내용 | 배경 |
|---|---|---|
| **B1 SBI recall 회복** | 다양 도메인 학습 후 recall 0.89→0.48 하락. SBI 생성 시 더 다양한 블렌딩(다중 랜드마크 마스크, 색상 불일치, 블러 경계) + hard negative 추가로 정밀도 유지하며 recall 회복 시도 | ✅ 완료 — v2 승격: 블렌딩 다양화(폴리곤/아핀) + `score_bias 35` 재보정으로 recall 0.405@FPR0.044, matched-FPR 기준 전 구간 우월 |
| **B2 범용 이미지 생성 탐지 자체학습** | Hemg/deepfake-and-real-images(HF parquet, 720장)로 EfficientNet-B0 이진분류 학습 시도 | ❌ 기각(실측) — holdout AUROC 0.615, FPR 1.0(전 입력 ~64점), DALL-E 랜덤. 데이터셋 라벨 노이즈+560장으로 부족. diffusion 자체생성 경로는 미시도(diffusers 미설치)로 잔여 |
| **B3 오디오 확장 벤치** | edge-tts 전 음성(50+) 스윕으로 voice-dependency 분포 확정 + Coqui XTTS(로컬 보이스클론) 샘플 생성 시도. AASIST 오탐 조건(대역폭별) 프로파일 | ✅ 완료 — 40 fake/12 real: AASIST recall 0.72/FPR 0.08, w2v 0.78/0.00, union 0.93. 점수표 `experiments/audio_sweep_2026-09-20.json`. Coqui는 py3.12 미지원으로 미측정 유지 |
| **B4 영상 재압축 측정** | 화면녹화/재인코딩 시뮬레이션(ffmpeg preset)으로 프레임 집계 탐지율 붕괴 곡선 측정 | ✅ 완료 — 실측: 실사 Lenna 86(오탐), DALL-E 10-19(미스), crf32/화면녹화 시 전멸. aide-frames 프로필 limitation에 기록 |

## Phase C — 파이프라인/운영 개선

| 항목 | 내용 |
|---|---|
| **C1 멤버 불일치 신호 명시화** | AASIST↔wav2vec, AIDE↔Swin 불일치가 이미 측정된 경고 신호 — 앙상블 결과에 "member disagreement" 상위 신호로 노출 |
| **C2 배치 스캔 진행률** | `scan-folder` 장기 실행 시 진행률 콜백/SSE 연동(`/api/check/stream` 인프라 재사용) |
| **C3 프레임트레이스 고도화** | 실패 아티팩트 재시도 버튼, deepfake 점수 분포 요약 패널, HTML 리포트 임베딩 검증 |

## Phase D — 데이터/자격증명 필요 (현재 차단됨 — 명시적 기록)

| 항목 | 차단 사유 | 해제 조건 |
|---|---|---|
| KoDF 한국인 얼굴 SBI 파인튜닝 | AI Hub 승인 필요 | 데이터셋 접근 승인 |
| KsponSpeech 실사 한국어 음성 | AI Hub 승인 필요 | 동일 |
| HyperCLOVA/GPT/Claude 한국어 AI문 코퍼스 | 상용 API 키 필요 | API 자격증명 제공 |
| ElevenLabs/RVC 보이스클론 실측 | 상용 서비스/학습 자원 | API 키 또는 RVC 학습 데이터 |
| Sora급 영상 생성기 탐지 | 공개 체크포인트 부재 | 공개 모델 출시 시 재평가 |
| 텍스트 제공사 귀속(모델별 구분) | 원리적으로 불가에 가까움 | — (범위 제외 유지) |

## 실행 순서 및 게이트

```
A1 → A2 → A3 → B3(저비용 측정) → B1(재학습) → B2(자체학습) → B4 → C1~C3
```

- **모든 신규 멤버/신호는 라벨 코퍼스 실측 게이트 통과 시에만 배선** — 이번 세션에서 이 규율이
  YOLO-face, 위상 휴리스틱, KoELECTRA 3건의 잘못된 배선을 막았다.
- 기각된 후보는 이유와 측정값을 프로필/registry/평가 문서에 기록한다(재시도 비용 방지).
- 점수는 어디까지나 검토 우선순위 — "낮은 점수 = 진짜" 또는 "높은 점수 = 조작" 판정 금지.
