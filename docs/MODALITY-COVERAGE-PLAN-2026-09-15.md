# 전체 모달리티 탐지 요소 카탈로그 및 누락 기능 계획 (2026-09-15)

딥페이크가 될 수 있는 모든 대상 — **사진(이미지), 음성/소리(오디오), 영상(비디오), 글(텍스트)** —
에 대해 문헌·업계에서 실제로 사용되는 탐지 요소를 전수 열거하고, 현재 코드베이스의
구현 상태를 대조해 누락 기능의 추가 계획을 세운다.

상태 표기: ✅ 구현+스캔 배선 / 🔶 구현됐으나 스캔 미배선(CLI 전용) 또는 부분 구현 / ❌ 없음

---

## 1. 사진 / 이미지

| 탐지 요소 | 원리 | 상태 |
|---|---|---|
| 메타데이터/EXIF·PNG 청크 | 카메라 기록 부재, 생성기 서명 | ✅ |
| C2PA / Content Credentials | 암호학적 출처 체인 | ✅ (c2pa.py) |
| 픽셀 프리스크린 | 노이즈/블렌딩 휴리스틱 (cv2) | ✅ pixel_analyzer |
| 주파수 영역 포렌식 | 스펙트럼 경사, 업샘플링 피크 | 🔶 frequency.py — 모듈 존재, `analyze_file` 미배선 |
| 뉴럴 생성 탐지기 | AIDE / CNNDetection / UnivFD / DIRE | ✅ 모델 프로필 |
| 얼굴 조작 탐지 | 블렌딩 경계, 반사, 랜드마크 | 🔶 face.py — `face` CLI 전용, 스캔 미포함 |
| PRNU 센서 지문 | 촬상 소자 노이즈 지문 상관 | 🔶 prnu.py — CLI 전용, 스캔 미배선 |
| 인페인팅/부분 변형 | 수정 영역 국소 탐지 | 🔶 inpaint.py — CLI 전용 |
| JPEG 고스트/이중압축 | 재압축 흔적(quality 불일치 영역) | ❌ |
| CFA/디모자이크 아티팩트 | 베이어 패턴 부재 → 비카메라 | ❌ |
| Copy-move 탐지 | 동일 영역 복제 스플라이싱 | ❌ |
| ELA(오류수준 분석) | 재저장 시 압축 불균일 | 🔶 pixel 경로에 유사 신호 일부 — 정식 ELA 없음 |
| 조명/그림자 물리 일관성 | 광원 방향·명암 모순 | ❌ (연구 후보) |
| 눈동자 반사/각막 하이라이트 | GAN 얼굴의 비일관 반사 | ❌ |
| SynthID 픽셀 워터마크 디코딩 | Google 픽셀도메인 워터마크 | ❌ (바이트 스캔 불가 명시됨 — 디코더 없음) |
| 생성기 귀속(generator attribution) | 어떤 모델이 만들었나 | ❌ (벤더 미귀속 원칙 유지 — 신호로만) |
| 확장자 커버리지 | bmp/tiff/gif/heic | ❌ core는 jpg/png/webp만 — GUI accept와 불일치 |

## 2. 음성 / 소리 (오디오)

| 탐지 요소 | 원리 | 상태 |
|---|---|---|
| 스펙트럴 휴리스틱 | 스펙트로그램/에너지 이상 | ✅ audio.py |
| 뉴럴 스푸핑 탐지 | AASIST (ASVspoof2019) | ✅ 프로필 실추론 검증됨 |
| 보코더 아티팩트 | 합성기 특유의 위상/고조파 패턴 | ❌ |
| 위상 스펙트럼 분석 | 자연 음성의 위상 비정형성 | ❌ |
| 무음/호흡 패턴 | TTS는 호흡·마이크로침묵이 부자연 | ❌ |
| 프로소디(억양) 통계 | 피치 곡선 단조로움 | ❌ |
| 배경 잡음 일관성 | 잡음 바닥의 불연속 → 편집/합성 | ❌ |
| 잔향 일관성 (RT60) | 공간 잔향이 장면별 불일치 | ❌ |
| 코덱 이중압축 흔적 | 재인코딩 아티팩트 | ❌ |
| 화자 대조 (두 음성 동일인?) | speaker embedding 비교 | ❌ (선택 기능 후보) |
| 최신 벤치마크 캘리브레이션 | ASVspoof 2021/2024, ITW 데이터 | ❌ (2019 학습 모델 그대로 — 감가 명시) |

## 3. 영상 (비디오)

| 탐지 요소 | 원리 | 상태 |
|---|---|---|
| 시간축 일관성 | 밝기/콘트라스트/블러/에지 변동 | 🔶 video_analysis.py — CLI 전용 |
| 프레임별 이미지 탐지 | 샘플 프레임을 이미지 탐지기로 | 🔶 aide-frames-runtime — CLI 전용 |
| rPPG 맥박 신호 | 얼굴 미세 색변화 → 심박 유무 | 🔶 rppg.py — CLI 전용 |
| 얼굴 워핑/깜빡임 | 프레임 간 얼굴 경계 불안정 | 🔶 temporal 신호 일부 |
| **`analyze_file` 비디오 분기** | 스캔/업로드/통합검사가 영상을 인식 | ❌ **mp4 등이 "unsupported"로 떨어짐** |
| 오디오 트랙 추출+분석 | 영상 속 음성을 오디오 파이프라인으로 | ❌ |
| 립싱크 (입모양-음성 동기) | SyncNet 계열 A/V 오프셋 | ❌ |
| C2PA 비디오 출처 | 영상 컨테이너 출처 기록 | 🔶 바이트 스캔은 되나 영상 전용 manifest 검증 미흡 |
| 인코딩/컨테이너 메타데이터 | encoder 문자열, GOP 구조 | ❌ |
| 프레임 ELA/압축 일관성 | GOP 경계 재압축 흔적 | ❌ |
| 아바타/디지털휴먼 탐지 | 렌더링/립싱크 아티팩트 | 🔶 avatar.py — CLI 전용 |
| 실시간 스트림 | 프레임 단위 이동평균 스코어링 | 🔶 realtime.py — CLI 전용 |

## 4. 글 (텍스트)

| 탐지 요소 | 원리 | 상태 |
|---|---|---|
| 통계/지문 휴리스틱 | TTR, burstiness, LLM 과용어휘, 타이포그래피 | ✅ core + text_advanced |
| Causal-LM 퍼플렉시티 | 참조 LM 기준 토큰 NLL | ✅ qwen-ppl-runtime (한국어 유효) |
| Binoculars 이중 LM | performer/observer NLL 비율 | ✅ (실험적, weight 0.25) |
| 분류기 멤버 | Fakespot / OpenAI detector | ✅ |
| 한국어 인식 레이어 | 어절 토큰화, 한국어 밴드 | ✅ (부분 — 임계 미보정) |
| **Office 문서 파이프라인** | docx/pdf/hwp/xlsx 텍스트 추출 | ❌ **.txt/.md만 지원** — 실사용 문서 대부분 누락 |
| 문서 포렌식 메타데이터 | PDF producer, docx 작성자/수정이력 | ❌ |
| LLM 워터마크 디코딩 | KGW green-list 토큰 분포 검정 | ❌ (학술 구현 가능) |
| 필체/저자 귀속 (stylometry) | 작성자 지문 비교 | ❌ |
| Fast-DetectGPT | 조건부 곡률 | ❌ (측정상 이득 불명확 — 보류 결정 유지) |
| 라벨 캘리브레이션 | 보류 코퍼스 기준 임계값 확정 | 🔶 시드 코퍼스만 존재 |

## 5. 공통 / 융합

| 요소 | 상태 |
|---|---|
| 통합 검사 엔드포인트 | ✅ `/api/check` (텍스트+단일 파일) |
| 멀티모달 융합 | ✅ multimodal.py (수동 점수 입력 방식) |
| **자동 멀티모달** | ❌ 영상에서 얼굴+음성+프레임을 자동 추출해 각 파이프라인 실행 후 융합 — 없음 |
| 엔진 가용성 표시 | 🔶 멤버별 available은 보고됨, GUI 통합 카드에는 요약만 |
| 업로드 용량/형식 화이트리스트 | 🔶 256MB 상한 있음, 형식별 세부 캡 없음 |

---

## 구현 계획 (우선순위 순)

### Phase V1 — 비디오를 스캔에 배선 (최우선, 구조적 구멍)
영상이 현재 통합 파이프라인에서 완전히 빠져 있다.
1. `core.analyze_file`에 `SUPPORTED_VIDEO_EXTENSIONS` 분기 추가:
   `analyze_video_temporal` + 프레임 샘플 이미지 탐지(aide-frames) + `model_analysis` 융합.
2. `video-frames` 런타임 결과를 `model_analysis`로 끌어올려 `external_model_active` 집계에 포함.
3. 오디오 트랙 추출(ffmpeg, 선택) → `analyze_audio` 재귀 호출 → `av_analysis` 섹션.
4. GUI accept 목록에 mp4 등 추가 + `/api/check`가 영상을 인식.

### Phase V2 — 텍스트 문서 파이프라인
실사용 문서 대부분이 .txt/.md가 아니다.
1. `_extract_document_text(path)`: pdf(pymupdf), docx(python-docx/zip-xml), hwp(olefile/선택), xlsx → 텍스트 추출 계층. 의존성 없으면 graceful skip.
2. `SUPPORTED_TEXT_EXTENSIONS`를 "텍스트 추출 가능 문서"로 확장.
3. 문서 포렌식 신호: PDF Producer/Creator 문자열, docx `docProps`(author, lastModifiedBy, revision) → `forensic` 섹션에 기록.

### Phase V3 — 스캔 미배선 모듈 배선
이미 구현된 탐지기가 스캔에서 안 도는 문제.
1. 이미지: `face.analyze_faces` + `inpaint` + `frequency` 신호를 이미지 분기에 통합(선택/옵션 플래그).
2. 영상: `rppg`(프레임 샘플 있을 때) + `avatar` 신호를 비디오 분기에 통합.
3. 각각 기본 off + `--deep` 플래그로 활성화 — 런타임 비용 보호.

### Phase V4 — 오디오 물리 신호군
1. 무음/호흡 패턴 (energy-gated pause 분포).
2. 배경 잡음 바닥 불연속 탐지.
3. 프로소디 단조도 (피치 곡선 분산 — librosa/parselmouth 선택).
4. 보코더 고조파 규칙성 휴리스틱.
전부 휴리스틱 신호 레이어 — 신경망 없이 구현 가능.

### Phase V5 — 이미지 포렌식 보강
1. JPEG 이중압축/고스트 (DCT 계수 히스토그램).
2. 정식 ELA 신호.
3. 확장자 커버리지: bmp/tiff/gif를 core 분기에 추가(GUI accept와 정합).

### Phase V6 — 연구/선택 후보 (비용 대비 검증 후)
- 립싱크 SyncNet류 A/V 오프셋 — 모델 자산 필요.
- LLM 텍스트 워터마크(KGW) — 생성기 측 시드 필요, 오픈 생성기에만 유효.
- SynthID 픽셀 디코더 — Google 비공개, 대체 구현 불확실.
- 화자 대조 / stylometry 저자 대조 — 참조 샘플 필요한 2-input 기능.
- 조명/그림자 물리 일관성 — 정확도 연구 단계.

### 검증 규칙 (기존 계약 유지)
- 모든 신규 신호는 weight가 명시된 `EvidenceSignal` — 점수는 여전히 우선순위 신호.
- 모달리티별 라벨 코퍼스 추가 전까지 새 신호는 "측정 전(provisional)" 표기.
- 선택 의존성(ffmpeg, pymupdf, mediapipe) 부재 시 해당 레이어만 unavailable로 표기.
- Windows 재현 가능성 필수 (delete=False 패턴 준수).
