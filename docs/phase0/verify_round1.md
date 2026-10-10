# 독립 검증 라운드 1 결과 (HEAD 2605bf0) — 수정 필요 항목

판정: 게이트 0 통과 불가. 게이트 스크립트와 `scan`/`/api/scan` 경로는 건전함(fail-closed, medium 없음, 결정 규칙, 내용 해시 캐시, 전체 본문 서명, read-root 제한, 핀 강제). 아래 항목이 불일치.

## 반드시 수정 (게이트 0 차단)

D1. 독립 CLI 서브커맨드가 옛 계약(67/35 컷오프, 낮음/주의/높음, 근거 없는 "낮음")을 출력:
  - `audio clip.wav` → "band":"low","낮음","오디오에서 뚜렷한 합성 의심 신호는 적습니다" (모델 미실행)
  - `video-analysis` → low; `text-advanced` → "medium","주의", score 50; `pixel-analysis photo.jpg` → "높음","AI 생성 의심 신호가 강합니다"; `forensic a1111.png` → "낮음"; `inpaint`, `agent` → low; `multimodal`, `explain` → high/medium
  - 위치: audio.py:187-196, 249-256; video_analysis.py:188-197; text_advanced.py:135ff; pixel_analyzer.py:120-128; c2pa.py:178-186; inpaint.py:108-116; multimodal.py:214-222, 411; prnu.py:176; rppg.py:211; face.py:181-190; avatar.py:80-88; threed.py:75-83; realtime.py:64-77; faceswap_seam.py:205-213
  - 요구: 이 서브커맨드들은 (a) analysis_api를 통해 세 결론 계약으로 결과를 내거나, (b) "계층 진단(참고 신호, 미측정)" 출력으로 바꿔 어떤 밴드/결론/"의심 적음" 문구도 내지 않아야 한다. 각 서브모듈의 `band`/`verdict` 필드는 제거하거나 `reference_band`로 이름을 바꾸고 UI/CLI/JSON에서 "결론"으로 표시되지 않게 한다.

D2. api_server의 `/api/analyze/{text,forensic,audio,face}`, `/api/classify`, `/api/multimodal`이 통합 경로를 우회: text → medium/주의 또는 low/낮음; forensic a1111.png → low/낮음(scan은 manipulation_evidence); multimodal → low. (api_server.py:245-326, 702-745)

D3. 웹 서버 `/api/analyze-file`이 `forensic.band "low" 낮음`, 게이트 안 거친 `pixel_analysis.band "high" 높음 58`을 반환. (webapp_api.py:196-240)

D4. `legal-report`가 a1111.png에 근거 0건, "전체 신뢰도: 0.00", "출처 표준 메타데이터가 발견되지 않았습니다", 도구 버전 "2.0"을 출력. scan 결과와 일치해야 함.

D5. 증거설명서가 `item.path`(상대경로)를 현재 작업 디렉토리 기준으로 재해시 → 다른 파일 해시 기록 또는 "해시 불가". scan의 `item.sha256`를 써야 함. (evidence_statement.py:224)

D6. 적합성 표 "통과"가 기준보다 약함:
  - QA-ADV-2: 합성 채팅 스크린샷 10장만(기준: 카톡/웹/문서뷰어 50장). 합성으로라도 세 종류 × ≥17장 = 50장 생성.
  - QA-OUT-3: `_detect_faces_strict`를 mock. 실제 검출기가 돌 수 있어야 함. OpenCV 5.0에 CascadeClassifier가 없음 → 순수 numpy/Pillow 기반 대체 검출기(예: 피부색+타원 휴리스틱) 또는 opencv-contrib/`cv2.FaceDetectorYN`(YuNet onnx, 핀 고정) 검토. 최소한 검출기 없는 환경에서는 "의존성 부재"가 아니라 실제 검출기 실행 결과로 "얼굴 미검출"이 기록되어야 하므로, numpy 기반 경량 검출기를 넣고 합성 얼굴(눈·코·입 타원)에서 검출/미검출을 실제로 확인.
  - QA-OUT-4: 기록된 실행에서 API 레그 skip. qa_phase0.py가 fastapi 없으면 실패하도록(또는 사이드 venv 자동 생성) 하여 기록에 API 레그가 포함되게.
  - QA-IN-1: 43개 형식 중 6개(.7z .doc .hwp .ppt .rar .xls) 미샘플. 최소 바이트 샘플을 합성(.doc/.xls/.ppt는 OLE 헤더만, .hwp는 HWP 5.0 OLE 헤더, .7z/.rar는 py7zr/rarfile 없으면 매직 바이트만)해 "미지원/판단 불가 + 이유"로라도 스캔.

## 수정 필요 (스펙 불일치)

D7. JPEG EXIF와 XMP를 전혀 파싱하지 않음. 스펙 WP-A 표: "XMP 생성 도구 필드(DigitalSourceType=trainedAlgorithmicMedia 등)" = deterministic/synthetic/strong, "카메라 EXIF 일관(기종·렌즈·촬영시각·GPS 정합, 재압축 흔적 없음)" = deterministic/authentic/moderate. (image_metadata.py:27-51, evidence_rules.py:77-114) Pillow의 getexif/XMP(`Image.getxmp()` 또는 APP1 파싱)로 구현. 생성 도구 키워드(Flux, Midjourney, DALL-E, Imagen, Stable Diffusion, Firefly, Gemini, "AI Generated") 목록은 상수로.

D8. C2PA 리더가 예외 시 {"present": False, "status": "absent"} 반환, coverage는 c2pa "ran". CHARTER 위반(4값 유지). 예외 → coverage failed + status "unavailable". (c2pa.py:241-244)

D9. zip 폭탄 행: status unknown, sha256 None, coverage archive ran, 이유 "분석 가능한 구성 파일이 없습니다"뿐. 건너뛴 `../x.png`, 절대경로 멤버는 coverage 항목 없음. 아카이브 컨테이너 행에 sha256 없음(서명 본문이 아카이브 증거를 바인딩하지 못함). (core.py:295-325) → 컨테이너 행에 sha256 기록, 멤버별 거부 사유를 coverage/limitations에 기록, 예산 소진 사유 명시.

D10. 폴더 안 심볼릭 링크 파일이 보고서에서 사라짐(행 없음, skipped 카운트도 없음). (scan_cache.py:43, 61) → "건너뜀: 심볼릭 링크" 행으로 기록.

D11. 사람 한국어 에세이에 `source_guess` = "AI 어시스턴트 문체 추정", confidence medium (text_heuristics.py:221). lexical 신호가 결론처럼 보임 → 텍스트의 source_guess는 "참고" 표시이거나 제거.

D12. JSON에 `pixel_analysis.score`(38–78), `confidence:"medium"`; CSV에 `pixel_score`/`pixel_confidence` 열이 설명 없이 존재. → JSON은 `reference_signals` 아래로 이동하거나 필드명에 `raw_`/`reference_` 접두, CSV 열 이름을 `참고_픽셀_원점수`로, "medium" confidence 문자열은 "참고" 표기.

D13. inpaint 근거 제목 "인페인팅/부분 변형 탐지", direction synthetic인데 detail은 "뚜렷한 인페인팅 의심 신호는 적습니다 (원점수 15/100)". 깨끗한 사진에도 나타나고 규칙 5에서 authenticity를 막음. (core.py:715-720, evidence_rules.py:201) → 심층 신호는 보정 없으면 reference_signals로만; evidence에 넣지 않는다(스펙 표: "결정 불참여"이므로 아예 evidence에서 제외하는 것이 맞음).

D14. 서명 검증 CLI 없음 → `deepfake-lens verify-report <json> [--key-file]` 추가.

D15. OpenCV 5.0.0에 CascadeClassifier 없음 → 얼굴 검출 불가 (D6의 QA-OUT-3와 함께 해결). GIF 입력은 face_manipulation `failed` "지원하지 않는 이미지 형식" → `skipped`로.

D16. 기타: CONFORMANCE 헤더 커밋이 HEAD의 부모; 영어 오류 메시지("file exceeds --max-file-bytes"); 요약 JSON에 medium/high/low 키; gui.html:98 "위험도순", "결론순" 정렬이 score(항상 0) 기준; 테이블 헤더 "휴리스틱 전용"; 잘린 파일·C2PA PNG에 "메타데이터 부재"; api-serve `/api/stats`가 헤더 없이 200(웹 서버는 401); api-serve help가 존재하지 않는 `--folder` 언급; webapp_api.py:146 예외 traceback 없이 삼킴; documents.py:71 pymupdf 실패 시 예외 클래스 누락; 측정 게이트가 all-zero manifest sha 위조 `measured_on`을 통과시킴(64-hex 검사 외에 코퍼스 매니페스트 파일 존재·해시 일치 검증 추가).

## 약한 테스트 (표 3)
- QA-SYS-1: verdict 문구가 "판단 불가 — 검사 실패(외부 모델)"; "무결성"은 coverage에만. → verdict 문구에 "모델 무결성 실패" 포함.
- QA-IN-5: 예산을 4 MiB로 패치, 피크가 아닌 추출 후 1회 측정, "이유"가 비어있지 않은 문자열이면 통과, 대조군이 어차피 undetermined. → 피크 디스크 사용량을 폴링 스레드로 측정, 이유 문자열이 구체 사유(잘림/빈 파일/예산 소진/…) 중 하나인지 검사, 대조군에 a1111 PNG(manipulation_evidence) 포함.
- QA-OUT-5: 합성 EvidenceItem 통과 검사(0단계에선 공허). 그대로 두되 표에 "구조 검사"로 명시.
