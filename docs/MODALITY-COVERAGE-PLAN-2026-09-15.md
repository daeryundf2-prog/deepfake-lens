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

## 구현 계획 — 세부 작업 분해

각 작업은 (파일 → 변경 내용 → 테스트 → 수용 기준) 순서로 기술한다.
모든 단계는 독립 커밋 가능 크기로 쪼갠다.

---

### Phase V1 — 비디오 스캔 배선 (구조적 구멍)

**V1-1. `core.analyze_file` 비디오 분기**
- `deepfake_lens/core.py` → `SUPPORTED_VIDEO_EXTENSIONS` 임포트 후 audio 분기 뒤에
  video 분기 추가: `analyze_video_temporal(path, model_path=model_path)` 호출 →
  `_video_result()` 어댑터(`_audio_result`와 동형)로 `ClassificationResult` 변환.
- 어댑터 규칙: temporal score/band/signals/limitations를 그대로 이관하고
  `model_analysis`는 `VideoTemporalAnalysis.model_analysis` 그대로 실어
  `external_model_active` 집계에 포함. `verdict` 앞에 `[영상]` 프리픽스 불필요
  (kind 필드가 구분).
- 테스트: `test_core.py`(또는 신규 `test_video_scan.py`)에 cv2 모킹한 가짜 mp4
  분기 테스트 2건 — (a) 확장자 디스패치 확인, (b) cv2 없을 때 status=failed가
  아니라 limitation으로 degrade되는지.
- 수용: `python -m deepfake_lens scan <mp4폴더>`가 "지원 형식 아님" 대신
  kind=video 결과를 반환.

**V1-2. `webapp`/GUI 영상 인식**
- `gui.html` → drop-zone 안내문과 `input.accept`에 `.mp4,.mov,.webm,.mkv,.avi` 추가.
- `webapp.py` → 별도 수정 불필요(업로드는 확장자 무관하게 `analyze_file` 위임).
- 테스트: 기존 upload 테스트가 mp4 파일명으로도 동작하는지 1건 추가.
- 수용: GUI에 영상 파일 드롭 → 결과 행에 kind=video.

**V1-3. 영상 오디오 트랙 교차 분석**
- `video_analysis.py` → `analyze_video_temporal(..., analyze_audio_track: bool=False)` 옵션:
  ffmpeg 있을 때 `ffmpeg -i in.mp4 -vn -ac 1 -ar 16000 tmp.wav` 추출 →
  `audio.analyze_audio(tmp, model_path)` → 결과를 `av_audio` 필드로 첨부.
  ffmpeg/추출 실패 시 limitation "오디오 트랙 분석 불가(ffmpeg 없음)"만 추가.
- `core.py` 비디오 분기에서 `analyze_audio_track=True`는 `--deep`/opt-in으로 게이트.
- 테스트: ffmpeg 모킹 + analyze_audio 스텁으로 av_audio 첨부 여부 1건.
- 수용: 음성 포함 영상에서 음성 점수가 영상 결과와 함께 보고됨.

**V1-4. `/api/check` 영상 경로**
- webapp `_check_file_payload` → kind==video일 때 forensic에 더해
  `item.result.model_analysis`가 이미 있으므로 추가 작업 없음 — 응답에
  `item.kind`가 "video"로 오는지만 테스트.
- 수용: mp4 업로드 → `{mode:file, item.kind:video}`.

### Phase V2 — 문서 파이프라인

**V2-1. `documents.py` 신규 모듈**
- `extract_document_text(path) -> tuple[str, dict]` 반환 (본문, 메타데이터 dict).
- 확장자 매핑: `.pdf`(pymupdf → fallback pdfminer), `.docx`(zipfile+`word/document.xml`
  정규식 스트립 — 의존성 제로 경로), `.hwp`(olefile 있을 때만), `.xlsx/.pptx`(zip-xml).
- 추출 불가/의존성 없음 → `("", {"extractor": "unavailable:<dep>"})` — 예외 아님.
- 크기 캡: 파일 ≤64MB, 추출 텍스트 ≤1MB(절단 표시 limitation).
- 테스트: zip으로 만든 최소 docx + 텍스트 없는 pdf → graceful 2~3건.

**V2-2. `core.analyze_file` 문서 분기**
- `SUPPORTED_TEXT_EXTENSIONS`에 문서 확장자 추가하되 `_read_prefix` 대신
  `documents.extract_document_text` 사용. 추출 실패 시 status=analyzed +
  limitation "텍스트 추출 불가" (failed 아님 — 파일은 유효).
- 문서 메타데이터(author, producer, created/modified)를 `analyze_text` 결과의
  `source_guess.reasons`에 보강 입력.
- 수용: .docx 드롭 → 텍스트 점수 + 작성자 메타데이터가 출처 추정에 반영.

**V2-3. 문서 포렌식**
- `documents.py`의 메타데이터 dict를 `c2pa.analyze_metadata_forensic`이 문서형도
  처리하도록 확장: PDF `/Producer`, docx `docProps/core.xml` author/revision을
  `ProvenanceRecord`로 기록.
- 테스트: docx fixture에서 author 레코드 추출 1건.

### Phase V3 — 고립 탐지기 배선 (opt-in deep 모드)

**V3-1. 이미지 deep 신호**
- `core.analyze_file(..., deep: bool=False)` 추가. deep일 때:
  `face.analyze_faces`(mediapipe 없으면 랜드마크 근사로 degrade),
  `inpaint.detect_inpainting`, `frequency` 신호를 `EvidenceSignal`로 변환해
  `analyze_image_metadata` 결과 signals에 병합.
- GUI: pixel-mode select에 `deep` 옵션 이미 존재 — deep 선택 시 이 플래그도 전달.
- 테스트: deep=False 기본 동작 불변 1건 + deep=True 신호 병합 1건(모킹).

**V3-2. 비디오 deep 신호**
- deep일 때 `rppg.analyze_rppg`(프레임 샘플 재사용), `avatar.analyze_avatar`를
  비디오 분기에 병합. 각각 독립 try/except + limitation degrade.
- 수용: `scan --deep`이 얼굴/맥박/인페인팅 신호를 결과에 포함.

### Phase V4 — 오디오 물리 신호 (audio.py 확장)

- V4-1 무음/호흡: RMS 에너지 게이트로 pause 구간 분포 → "pause 히스토그램 균일도" 신호 (weight ≤15).
- V4-2 잡음 바닥: 저에너지 프레임의 스펙트럴 바닥 일관성 → 불연속 지점 수 신호.
- V4-3 프로소디: `librosa.yin` 피치 곡선 분산/범위 (librosa 없으면 스킵).
- V4-4 고조파 규칙성: harmonic/percussive 분리 후 고조파 잔차 균일도.
- 각각 EvidenceSignal + 테스트 1건씩; 전부 provisional 표기.

### Phase V5 — 이미지 포렌식

- V5-1 확장자: `.bmp/.tiff/.tif/.gif`를 `SUPPORTED_IMAGE_EXTENSIONS`에 추가
  (PIL/cv2 읽기 확인, GIF는 첫 프레임). GUI accept와 정합.
- V5-2 JPEG 이중압축: `frequency.py`에 DCT 계수 히스토그램 주기성 신호 추가
  (JPEG만, 재저장 오탐 limitation 명시).
- V5-3 정식 ELA: 지정 품질 재저장 후 오차 맵의 지역 분산 → pixel 신호.

### Phase V6 — 연구 후보 (게이트: 별도 검증 후 착수)
립싱크 SyncNet, KGW 텍스트 워터마크, SynthID 디코더, 화자/저자 대조 —
각각 선행 조건(모델 자산/참조 샘플) 충족 시 개별 착수. 이 문서에 체크리스트로만 유지.

---

## 실행 순서 및 의존관계

```text
V1-1 → V1-2 ─┐
             ├→ V1-3 → V1-4      (비디오 완결)
V2-1 → V2-2 → V2-3               (문서 완결, V1과 독립 병행 가능)
V3-1, V3-2                       (V1-1 이후 — deep 플래그 경로 재사용)
V4-* , V5-*                      (독립, 언제든)
V6                               (연구 게이트)
```

권장 커밋 순서: **V1-1 → V1-2 → V2-1+V2-2 → V1-3 → V3-1 → V4-1** —
"모든 것을 체크할 수 있는 형태"의 체감 개선이 가장 큰 순서.

## 검증 규칙 (기존 계약 유지)
- 모든 신규 신호는 weight가 명시된 `EvidenceSignal` — 점수는 여전히 우선순위 신호.
- 모달리티별 라벨 코퍼스 추가 전까지 새 신호는 "측정 전(provisional)" 표기.
- 선택 의존성(ffmpeg, pymupdf, mediapipe, librosa) 부재 시 해당 레이어만 unavailable로 표기.
- Windows 재현 가능성 필수 (임시파일 delete=False 패턴 준수).
- 각 Phase 완료 시 해당 모달리티의 실측 프로브 결과를 `experiments/`에 기록.
