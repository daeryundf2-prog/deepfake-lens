# 검증 ID → 스펙 갭 ID 추적표 (Y13)

검증 라운드 1–7의 결함 ID(커밋 제목에 쓰인 것)와 라운드 8의 Z1–Z5를 0단계 스펙의 갭 ID(G1–G34)에 매핑한다.
스펙 갭에 해당하지 않는 항목은 `신규(스펙 외): <사유>`로 적는다. 라운드 3과 라운드 6의 N은 서로 다른 집합이므로
`N3-x` / `N6-x`로, 라운드 5의 G1–G16은 스펙의 G1–G34와 충돌하므로 `V5-G1…V5-G16`으로 표기한다.
근거: `verify_round1/4/5/6/7.md`(라운드 2·3은 문서가 없어 해당 커밋 메시지), 스펙 WP 머리글의 갭 목록.
기계 판독용 같은 매핑과 커밋별 `Gaps:` 줄은 [`docs/traceability-commits.json`](traceability-commits.json)에 있다
(세션 소유자의 커밋 메시지 일괄 리워드용 — 이 표는 히스토리를 바꾸지 않는다).

요약: ID 109개 — 스펙 갭에 매핑 62개(그중 일부 신규 사유 병기 8개), 신규(스펙 외)만 47개. (라운드 8의 Z1–Z5 포함: Z3 → G7, 나머지 신규.)

## WP → 갭 (스펙 머리글)

| WP | 갭 |
| --- | --- |
| WP-A | G5, G6, G12, G24 |
| WP-B | G1 |
| WP-C | G2, G9, G10, G33 |
| WP-D | G3, G13, G17 |
| WP-E | G4 |
| WP-F | G7, G8 |
| WP-G | G11, G30, G31, G32 |
| WP-H | G29, G34 |
| WP-I | G26, G27, G28 |
| WP-J | — (QA 하네스·적합성 표) |

## 검증 ID → 갭

| 검증 ID | 라운드 | 내용 | 스펙 갭 | 신규(스펙 외) | 커밋 |
| --- | --- | --- | --- | --- | --- |
| D1 | 1 | 독립 CLI 서브커맨드가 옛 밴드(낮음/주의/높음) 출력 → 세 결론 계약 또는 계층 진단 | G5, G6, G7, G24 |  | b13c677, 60fd411, 075e586 |
| D2 | 1 | api_server /api/analyze/*·classify·multimodal이 통합 경로 우회 | G7 |  | b13c677 |
| D3 | 1 | /api/analyze-file이 forensic 밴드·게이트 없는 픽셀 점수 반환 | G7, G13, G17 |  | b13c677 |
| D4 | 1 | legal-report가 scan 결과와 불일치(근거 0건) | G7 |  | b13c677 |
| D5 | 1 | 증거설명서가 cwd 기준 재해시 → scan의 sha256 사용 | G30 |  | ac1157c, 5d21e88 |
| D6 | 1 | 적합성 표 통과 기준 약함(QA-ADV-2·OUT-3·OUT-4·IN-1) | G7, G12, G13 |  | 2644e0d, 4a9ba73, 5d4761d |
| D7 | 1 | JPEG EXIF·XMP 미파싱(카메라 EXIF·XMP 생성 도구 근거) | G6 |  | 64c6b14 |
| D8 | 1 | C2PA 리더 예외가 absent/ran으로 기록 | G1 |  | 58f4a0d |
| D9 | 1 | zip 폭탄 행·거부 멤버 coverage·컨테이너 sha256 누락 | G12, G30, G34 |  | 07ab16c, ea376a8 |
| D10 | 1 | 폴더 안 심볼릭 링크가 보고서에서 사라짐 | G12 |  | 07ab16c |
| D11 | 1 | 텍스트 source_guess가 결론처럼 보임 | G4, G24 |  | 79ea0d3 |
| D12 | 1 | pixel score/confidence 필드·CSV 열이 결론처럼 보임 | G17 |  | d764fce |
| D13 | 1 | 보정 없는 심층 신호(inpaint)가 evidence에 들어가 규칙 5를 막음 | G17 |  | bece7ff, 60fd411 |
| D14 | 1 | 서명 검증 CLI(verify-report) 없음 | G30 |  | b13c677 |
| D15 | 1 | OpenCV 5에 CascadeClassifier 없음 → 얼굴 검출 불가, GIF는 skipped | G12 |  | 2644e0d |
| D16 | 1 | 기타: summary 밴드 키, 영어 오류, 삼킨 예외, 측정 게이트 위조 매니페스트 등 | G1, G5, G28 |  | 64c6b14, 1902fd4, 5d4761d, cc2581e, 075e586 |
| R1 | 2 | /api/scan/stream·아카이브 /api/check가 폴더 스캔 본체를 사용 | G7 |  | ca0018f, 81e4173 |
| R2 | 2 | 영상의 오디오 트랙 분석이 자체 av_audio coverage 항목 | G12 |  | db4bac4 |
| R3 | 2 | 문서 creator/application 메타데이터 출처 추정은 참고 전용 | G24 |  | c9a0573 |
| R4 | 2 | 0단계에서 추가된 영어 사용자 문구 한국어화; 프로필의 미검증 수치 제거 | G26 | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 91e8532, d34cab5, a4cbbc2 |
| R5 | 2 | 아카이브 컨테이너 행을 결론별로 집계(요약 일치) | G7 |  | ca0018f |
| R6 | 2 | pymupdf 없을 때 PDF 증거설명서는 한국어 안내 exit 2(traceback 아님) | G29 |  | f13f3f3 |
| R7 | 2 | 얼굴 미검출은 '얼굴 미검출', 미분석은 '해당 없음' | G12 |  | 0898fb9 |
| R8 | 2 | C2PA SDK 실패는 'C2PA 판독 불가(사유)' — 'C2PA 없음' 아님 | G1 |  | 99c6f29 |
| R9 | 2 | 카메라 EXIF 일관성 근거는 디코드된 이미지에만 | G6 |  | d45cfdb, 81e4173 |
| R16 | 2 | '점수는 검토 우선순위' 문구 제거; CSV 보정점수/결론 열; --include-low 의미 | G5 |  | 3edb8e4, 81e4173, b0a24df |
| N3-1 | 3 | coverage 사유·오류에 파일 시스템 경로 없음 | — | 신규(스펙 외): 보고서·coverage 사유에 파일 시스템 경로 노출 — 갭 목록 밖(정보 노출) | 35d3751 |
| N3-2 | 3 | 해시할 때 심볼릭 링크를 따라가지 않음; 결론 없는 행의 상태 줄 | G30 |  | 539b429 |
| N3-3 | 3 | 스캔 스키마가 draft 2020-12에서 null result 허용 | — | 신규(스펙 외): 계약 스키마 정확성(공통 규칙 6) — 갭 목록 밖 | a486c27 |
| N3-4 | 3 | PyMuPDF import가 stdout에 출력하지 않음 | — | 신규(스펙 외): PyMuPDF import가 stdout 오염(JSON 출력 깨짐) — 갭 목록 밖 | a486c27 |
| N3-5 | 3 | 아카이브 컨테이너 행에 집계 근거 항목 | G12 |  | 214f4a5 |
| N3-6 | 3 | C2PA 해시 불일치는 'C2PA 무결성 불일치'(원본성 근거 아님) | G6 |  | 214f4a5 |
| N3-7 | 3 | 한국어 상태·help·401; 빈 키 파일은 오류(서명 없이 진행하지 않음) | G30 | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | b0a24df |
| N3-8 | 3 | 건너뛴 하위 폴더 수·취소 집계·로그 — 조용한 누락 없음 | G12 |  | b0a24df |
| B1 | 4 | 단일 파일 진입점도 폴더 스캔처럼 아카이브를 펼침 | G7 |  | e6a3a38 |
| B2 | 4 | HTML 보고서 영어 문구 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 6a41827 |
| B3 | 4 | HTML·포렌식 PDF의 raw skipped 상태, PDF 문서 번호 폰트 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 6a41827 |
| B4 | 4 | legal-report 텍스트의 raw 코드·영어 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 6a41827 |
| B5 | 4 | GUI 출처 추정·archive_member 라벨 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 4f688aa |
| B6 | 4 | C2PA SDK 영어 오류 메시지 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 4923e3e |
| B7 | 4 | doctor JSON 출력 오염·영어 표 | G29 | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | bdf4da1 |
| B8 | 4 | --pdf-out 영어 PDF → 한국어 PDF 또는 exit 2; --help 한국어 | G29 | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | eb3354f, bf6c3ae, bda6e5d, 5b32de4, 58bd8f3 |
| S1 | 4 | 아카이브 멤버를 'evil.zip::a1111.png' 전체 이름으로 | — | 신규(스펙 외): 보고서의 압축 멤버 전체 이름 표기 — 갭 목록 밖 | 6a41827 |
| S2 | 4 | 포렌식 PDF 심볼릭 링크 해시 줄 문구 | G30 |  | 6a41827 |
| S3 | 4 | --redact-paths가 models[].profile 설치 경로도 숨김 | — | 신규(스펙 외): --redact-paths가 도구 설치 경로를 남김(정보 노출) — 갭 목록 밖 | 8b5e542 |
| S4 | 4 | 없는 폴더/파일 입력 사유 표시, exit 2 문서화 | G29 |  | c6541d9 |
| S5 | 4 | GUI CSP가 blob: 미디어 허용 | — | 신규(스펙 외): GUI CSP media-src blob: 누락(미리보기 실패) — 갭 목록 밖 | 3beadcf |
| S6 | 4 | 증거설명서 프로비넌스에 in-sample 주의 문구 | G28 |  | 6a41827 |
| S7 | 4 | 오디오 모델 limitation 중복, 비활성 프로필 안내 제외 | G28 |  | 40fdbd1 |
| S8 | 4 | test_korean_output 영어 탐지 우회 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 1479d4a, 75a53e9 |
| S9 | 4 | 테스트 미사용 import 2건 | — | 신규(스펙 외): 미사용 import(공통 규칙 4 린트) — 갭 목록 밖 | 3fb3bc8 |
| W1 | 4 | _iter_files 전역 경로 문자열 정렬 | G32 |  | 4e14e9c |
| W2 | 4 | QA 테스트를 스펙이 명명한 4개 파일로 통합 | — | 신규(스펙 외): WP-J 파일 배치(스펙 문자 그대로) — 갭 목록 밖 | 7d1af4c |
| W3 | 4 | 커밋 트레일러 일괄 수정(세션 소유자 작업) | — | 신규(스펙 외): 커밋 트레일러(공통 규칙 1) — 갭 목록 밖 | 커밋 제목에 없음 |
| V5-G1 | 5 | forensic/classify/explain/multimodal/agent 텍스트 출력의 raw 코드 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | fe247ec |
| V5-G2 | 5 | PDF 내용 손실: 페이지 밖 텍스트, 겹침, 해시 잘림 | G30 | 신규(스펙 외): PDF 레이아웃(페이지 밖 텍스트·겹침) — 갭 목록 밖 | 5880cfe |
| V5-G3 | 5 | vendor-weights 영어 출력, MISSING 개수 불일치 | G9 | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 6e2847d |
| V5-G4 | 5 | C2PA 상태값 영어(Invalid 등) | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 6e2847d |
| V5-G5 | 5 | 없는 파일/폴더 → 한국어 오류 exit 2 | — | 신규(스펙 외): 없는 입력 파일에 traceback/빈 보고서(CLI 사용 오류) — 갭 목록 밖 | 091bfeb |
| V5-G6 | 5 | 단일 파일 명령이 명시된 심볼릭 링크를 따라감 — 스캔과 같은 규칙 | G7 |  | 091bfeb |
| V5-G7 | 5 | API 영어 오류 본문 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 49e62ca |
| V5-G8 | 5 | 결정 규칙 4가 프로필 threshold를 쓰지 않음(0.5 고정) | G5 |  | 6e96fa2, 69b4ace |
| V5-G9 | 5 | 영어 탐지기 강화(사전 규칙)와 출력 검사 범위 확대 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 75a53e9, 1b24ae7 |
| V5-G10 | 5 | 커밋 71625d0, a23e5a6 메시지에 ID 없음(세션 소유자 리워드) | — | 신규(스펙 외): 커밋 메시지 ID 누락(공통 규칙 1) — 갭 목록 밖 | 커밋 제목에 없음 |
| V5-G11 | 5 | tests/qa docstring 첫 줄에 QA ID | — | 신규(스펙 외): WP-J QA docstring 규칙 — 갭 목록 밖 | cdce861 |
| V5-G12 | 5 | 미사용 import 제거, ruff F401 전역 | — | 신규(스펙 외): 미사용 import·ruff F401(공통 규칙 4) — 갭 목록 밖 | f6c51d9 |
| V5-G13 | 5 | scripts/*.py --help 영어 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | e52731b |
| V5-G14 | 5 | ffmpeg/mpg123 stderr를 로그로 | — | 신규(스펙 외): 디코더 stderr 영어 노이즈 — 갭 목록 밖(스펙 G14 '메신저 재압축 지문'과 무관) | b4914b5 |
| V5-G15 | 5 | test_forensic_pdf skipUnless에 B8 주석 | — | 신규(스펙 외): skipUnless에 ID 주석(공통 규칙 2) — 갭 목록 밖 | 58bd8f3 |
| V5-G16 | 5 | 분석자 '시스템(자동)', GUI 리뷰 옵션 한국어, CSV 라벨 열, PDF 64자 해시 | G30 | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 5880cfe, 8211673 |
| N6-1 | 6 | (=V5-G8) 보정된 통계 근거의 direction이 프로필 threshold를 따름 | G5 |  | 69b4ace |
| N6-2 | 6 | /api/report가 심볼릭 링크 대상 해시를 서명 본문·PDF에 기록 | G30, G31 |  | 55072e2 |
| N6-3 | 6 | (=V5-G10) 커밋 71625d0 리워드(세션 소유자) | — | 신규(스펙 외): 커밋 메시지 ID 누락(공통 규칙 1) — 갭 목록 밖 | 커밋 제목에 없음 |
| N6-4 | 6 | 모든 서브커맨드 입력 경로 검사 — 한국어 오류 exit 2 | — | 신규(스펙 외): CLI 입력 경로 오류 처리(사용 오류 exit 2) — 갭 목록 밖 | 846b0e3 |
| N6-5 | 6 | 프레임워크 HTTP 오류 본문 영어(404/405/422/500/501) | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 923878d |
| N6-6 | 6 | 캐시 키에 확장자, 복원 행의 경로 텍스트 재생성 | G11 |  | 040ca3c |
| N6-7 | 6 | 없는 경로 처리 불일치(N4에 통합) | — | 신규(스펙 외): CLI 입력 경로 오류 처리(N6-4에 통합) — 갭 목록 밖 | 846b0e3 |
| N6-8 | 6 | 스캔 표 유형 열의 raw unsupported | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 53d5b21 |
| N6-9 | 6 | stdlib /api/report가 본문 format 무시(서버 간 불일치) | G7 |  | 55072e2 |
| N6-10 | 6 | 웹/API 포렌식 PDF의 아카이브 멤버 해시 | G7, G30 |  | 55072e2 |
| N6-11 | 6 | FastAPI /api/report 잘못된 입력에 200, 항목 스키마 검증 | — | 신규(스펙 외): /api/report 요청 검증(400 + 항목 계약) — 갭 목록 밖 | 55072e2 |
| N6-12 | 6 | 영어 탐지기 우회(조사 접합, 하이픈, camel/snake 문장) | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | accb8e0 |
| N6-13 | 6 | 사용자용 사유의 식별자(failed:zip:…, allow_symlinks=false, WP-I) | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 53d5b21 |
| N6-14 | 6 | 중첩 아카이브 깊이 2 → 8(예산 안) | G34 |  | 195b190 |
| N6-15 | 6 | scan_folder가 스펙대로 (summary, items) 반환(WP-F) | G7 |  | 47a42a9 |
| N6-16 | 6 | tests/qa docstring 첫 줄 '<QA-ID>: <통과 기준 원문>' | — | 신규(스펙 외): WP-J QA docstring 원문 규칙 — 갭 목록 밖 | e368828 |
| N6-17 | 6 | 법무법인 기본값 제거, libsndfile 문구, in-sample 주의 별도 줄 | G28 | 신규(스펙 외): 내장 법무법인 기본값 제거·libsndfile 문구 — 갭 목록 밖 | 52148b2 |
| X1 | 7 | 기록되지 않은 파일이 조용히 누락; evidence-statement 폴더 100개 하드코딩 | G7, G12 |  | c9728a0 |
| X2 | 7 | /api/report가 클라이언트가 보낸 결론·근거를 그대로 서명 | G30, G31 |  | feaf5bb |
| X3 | 7 | --allow-symlinks에서 깨진/순환 링크가 행 없이 사라짐 | G32 |  | 0c5099b |
| X4 | 7 | /api/* 오류가 200 + {error} | G34 |  | c4078da |
| Y1 | 7 | evidence-statement x.json에 items 없음 → exit 2 | — | 신규(스펙 외): CLI 입력 검증(빈 증거설명서 exit 0) — 갭 목록 밖 | e1376eb |
| Y2 | 7 | --thresholds가 읽을 수 없는 파일이면 기본값으로 진행 | G7 |  | e1376eb |
| Y3 | 7 | scan "" → 현재 디렉토리 스캔 | — | 신규(스펙 외): CLI 입력 검증(빈 경로가 현재 폴더) — 갭 목록 밖 | e1376eb |
| Y4 | 7 | --cache가 기존 JSON 파일을 조용히 덮어씀 | G11 |  | 1cd269d |
| Y5 | 7 | OpenCV grfmt_png 영어가 콘솔에 출력 | — | 신규(스펙 외): OpenCV 네이티브 stderr 영어 노이즈 — 갭 목록 밖 | 4756a40 |
| Y6 | 7 | ml-classify 손상 이미지 → 한국어 stderr 오류 exit 2 | — | 신규(스펙 외): CLI 오류 처리(stdout JSON 오류 exit 1) — 갭 목록 밖 | 4756a40 |
| Y7 | 7 | --html-out이 폴더 → 스캔 전 오류 exit 2 | — | 신규(스펙 외): CLI 출력 경로 검증 — 갭 목록 밖 | e1376eb |
| Y8 | 7 | 웹 스캔이 max_files=-3 허용 | G34 |  | 5f0d361 |
| Y9 | 7 | 중첩 멤버 이름 x.zip.unpacked/… → a.zip::b.zip::c.png 체인 | G34 |  | ec46722 |
| Y10 | 7 | HTML 보고서에 행별 SHA-256 없음 | G30 |  | c1c959f |
| Y11 | 7 | 영어 탐지기 우회(대소문자 혼합 snake, 전각, 공백 없는 접합, 동형문자) | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 46f25c9 |
| Y12 | 7 | test_cli_inputs를 in-process로 20 s 미만 | — | 신규(스펙 외): 테스트 실행 시간(test_cli_inputs 34.7 s) — 갭 목록 밖 | 36f5864 |
| Y13 | 7 | 커밋 메시지 갭 ID 누락 → 이 매핑표 | — | 신규(스펙 외): 커밋 추적성(공통 규칙 1) — 갭 목록 밖 | 이 표를 추가하는 커밋 |
| Z1 | 8 | realtime --scores abc → 사용 오류 exit 2(종전 exit 1·처리 오류 1건) | — | 신규(스펙 외): CLI 옵션 값 검증(사용 오류 exit 2) — 갭 목록 밖 | Z1–Z5 커밋 |
| Z2 | 8 | vendor-weights pin <없는 프로필> → 오류: 파일을 찾을 수 없습니다 exit 2(종전 exit 1) | — | 신규(스펙 외): CLI 입력 경로 오류 처리(사용 오류 exit 2) — 갭 목록 밖 | Z1–Z5 커밋 |
| Z3 | 8 | forensic <파일>/ (끝 구분자) → 폴더가 아니라 파일입니다 exit 2, 모든 명령의 require_input_path에서 정규화 | G7 |  | Z1–Z5 커밋 |
| Z4 | 8 | verify-report 깨진·객체 아닌 JSON → stderr 오류: 보고서 JSON을 해석할 수 없습니다 exit 4(종전 stdout '읽을 수 없음') | — | 신규(스펙 외): CLI 오류 처리(stdout 대신 stderr, 문서화된 exit 4) — 갭 목록 밖 | Z1–Z5 커밋 |
| Z5 | 8 | --*-out 출력이 없는 폴더 안 → 검사 전 오류: 출력 폴더가 없습니다 exit 2, 폴더를 만들지 않음 | — | 신규(스펙 외): CLI 출력 경로 검증(Y7 연장) — 갭 목록 밖 | Z1–Z5 커밋 |

커밋 제목에 쓰이지 않은 라운드 2 ID: R10, R11, R12, R13, R14, R15, R17 — 라운드 2 문서가 없어 내용 확인 불가(매핑하지 않음).
라운드 3의 N은 커밋 제목에 N1–N8만 쓰였다(N3-1…N3-8). 라운드 6의 N3(=V5-G10, 커밋 리워드)은 세션 소유자 작업이라 커밋 제목에 없다.
이 표를 추가한 Y13 커밋 자신은 커밋별 목록에 없다(`Gaps: 신규(스펙 외)`).
Z1–Z5 행을 추가한 커밋(제목 `fix(cli): Z1-Z5 … (Z1-Z5; Gaps: G7, 신규)`)도 자기 참조라 커밋별 목록에 없다(`Gaps: G7, 신규(스펙 외)`). Z3 → G7 근거: R-OUT-6(진입점이 같은 입력을 같게 다룸) — CLI 진입점이 `Path()`로 끝 구분자를 지워, OS가 ENOTDIR로 거부하는 경로(`photo.png/`)를 다른 파일 경로처럼 분석했다.

## 커밋별 `Gaps:` 줄

| 커밋 | 라운드 | 제목의 ID | Gaps 줄 | 라벨 변경 제목(V5-G) |
| --- | --- | --- | --- | --- |
| 1697cb0 | wp | WP-A | Gaps: G5, G6, G12, G24 |  |
| cbd5cbf | wp | WP-B | Gaps: G1 |  |
| 34f31a9 | wp | WP-E | Gaps: G4 |  |
| 1155e99 | wp | WP-C | Gaps: G2, G9, G10, G33 |  |
| 75fbde3 | wp | WP-G | Gaps: G11, G30, G31, G32 |  |
| b538ea1 | wp | WP-I | Gaps: G26, G27, G28 |  |
| adad380 | wp | WP-I | Gaps: G26, G27, G28 |  |
| 46a0f2c | wp | WP-F | Gaps: G7, G8 |  |
| e6c488b | wp | WP-H | Gaps: G29, G34 |  |
| cf72674 | wp | WP-D | Gaps: G3, G13, G17 |  |
| 0433d27 | wp | — | Gaps: G31 |  |
| 27584e5 | wp | — | Gaps: G1 |  |
| 8ac52f6 | wp | — | Gaps: G31 |  |
| ec81b94 | wp | — | Gaps: G10 |  |
| b464893 | wp | — | Gaps: G27 |  |
| 8dda303 | wp | — | Gaps: G30 |  |
| 2429cbb | wp | — | Gaps: G28 |  |
| 96ad811 | wp | WP-J | Gaps: G1, G12, G26, G28; 신규(스펙 외) |  |
| de7f9e6 | wp | WP-J | Gaps: 신규(스펙 외) |  |
| ac1157c | 1 | D5 | Gaps: G30 |  |
| 64c6b14 | 1 | D7, D16 | Gaps: G1, G5, G6, G28 |  |
| 58f4a0d | 1 | D8 | Gaps: G1 |  |
| 07ab16c | 1 | D9, D10 | Gaps: G12, G30, G34 |  |
| 79ea0d3 | 1 | D11 | Gaps: G4, G24 |  |
| d764fce | 1 | D12 | Gaps: G17 |  |
| b13c677 | 1 | D1, D2, D3, D4, D14 | Gaps: G5, G6, G7, G13, G17, G24, G30 |  |
| bece7ff | 1 | D13 | Gaps: G17 |  |
| 1902fd4 | 1 | D16 | Gaps: G1, G5, G28 |  |
| 2644e0d | 1 | D15, D6 | Gaps: G7, G12, G13 |  |
| 4a9ba73 | 1 | D6 | Gaps: G7, G12, G13 |  |
| 5d4761d | 1 | D6, D16 | Gaps: G1, G5, G7, G12, G13, G28 |  |
| cc2581e | 1 | D16 | Gaps: G1, G5, G28 |  |
| 60fd411 | 1 | D13, D1 | Gaps: G5, G6, G7, G17, G24 |  |
| 5d21e88 | 1 | D5 | Gaps: G30 |  |
| ea376a8 | 1 | D9 | Gaps: G12, G30, G34 |  |
| 075e586 | 1 | D1, D16 | Gaps: G1, G5, G6, G7, G24, G28 |  |
| dbbef6b | 1 | WP-J | Gaps: 신규(스펙 외) |  |
| ca0018f | 2 | R1, R5 | Gaps: G7 |  |
| db4bac4 | 2 | R2 | Gaps: G12 |  |
| c9a0573 | 2 | R3 | Gaps: G24 |  |
| f13f3f3 | 2 | R6 | Gaps: G29 |  |
| 0898fb9 | 2 | R7 | Gaps: G12 |  |
| 99c6f29 | 2 | R8 | Gaps: G1 |  |
| d45cfdb | 2 | R9 | Gaps: G6 |  |
| 3edb8e4 | 2 | R16 | Gaps: G5 |  |
| 91e8532 | 2 | R4 | Gaps: G26; 신규(스펙 외) |  |
| 81e4173 | 2 | R1, R9, R16, WP-J | Gaps: G5, G6, G7; 신규(스펙 외) |  |
| 35d3751 | 3 | N3-1 | Gaps: 신규(스펙 외) |  |
| 539b429 | 3 | N3-2 | Gaps: G30 |  |
| a486c27 | 3 | N3-3, N3-4 | Gaps: 신규(스펙 외) |  |
| 214f4a5 | 3 | N3-5, N3-6 | Gaps: G6, G12 |  |
| b0a24df | 3 | N3-7, N3-8, R16 | Gaps: G5, G12, G30; 신규(스펙 외) |  |
| d34cab5 | 3 | R4 | Gaps: G26; 신규(스펙 외) |  |
| 0964219 | 3 | — | — (ID 없음) |  |
| 328e75f | 3 | WP-J | Gaps: 신규(스펙 외) |  |
| 3fb3bc8 | 4 | S9 | Gaps: 신규(스펙 외) |  |
| 4e14e9c | 4 | W1 | Gaps: G32 |  |
| 4923e3e | 4 | B6 | Gaps: 신규(스펙 외) |  |
| 7d1af4c | 4 | W2, WP-J | Gaps: 신규(스펙 외) |  |
| 6a41827 | 4 | B2, B3, B4, S1, S2, S6 | Gaps: G28, G30; 신규(스펙 외) |  |
| 4f688aa | 4 | B5 | Gaps: 신규(스펙 외) |  |
| e6a3a38 | 4 | B1 | Gaps: G7 |  |
| 8b5e542 | 4 | S3 | Gaps: 신규(스펙 외) |  |
| bdf4da1 | 4 | B7 | Gaps: G29; 신규(스펙 외) |  |
| c6541d9 | 4 | S4 | Gaps: G29 |  |
| 3beadcf | 4 | S5 | Gaps: 신규(스펙 외) |  |
| 40fdbd1 | 4 | S7 | Gaps: G28 |  |
| eb3354f | 4 | B8 | Gaps: G29; 신규(스펙 외) |  |
| bf6c3ae | 4 | B8 | Gaps: G29; 신규(스펙 외) |  |
| 1479d4a | 4 | S8 | Gaps: 신규(스펙 외) |  |
| bda6e5d | 4 | B8 | Gaps: G29; 신규(스펙 외) |  |
| 5b32de4 | 4 | B8 | Gaps: G29; 신규(스펙 외) |  |
| a4cbbc2 | 4 | R4 | Gaps: G26; 신규(스펙 외) |  |
| 9f63b80 | 4 | WP-J | Gaps: 신규(스펙 외) |  |
| f6c51d9 | 5 | V5-G12 | Gaps: 신규(스펙 외) | chore(lint): V5-G12 ruff F401 enabled, every unused import in the package removed |
| 58bd8f3 | 5 | V5-G15, B8 | Gaps: G29; 신규(스펙 외) | test(pdf): V5-G15 B8 ID comment on every skipUnless(pymupdf) case in test_forensic_pdf |
| fe247ec | 5 | V5-G1 | Gaps: 신규(스펙 외) | fix(cli): V5-G1 standalone text outputs print Korean labels, never JSON codes |
| 6e2847d | 5 | V5-G3, V5-G4 | Gaps: G9; 신규(스펙 외) | fix(vendor-weights, c2pa): V5-G3 Korean weights table with consistent counts; V5-G4 C2PA state in Korean |
| 091bfeb | 5 | V5-G5, V5-G6 | Gaps: G7; 신규(스펙 외) | fix(cli, api): V5-G5 missing file/folder is a Korean usage error; V5-G6 a named symlink is the scan's skipped row |
| 49e62ca | 5 | V5-G7 | Gaps: 신규(스펙 외) | fix(api): V5-G7 /api/report request errors in Korean |
| 6e96fa2 | 5 | V5-G8 | Gaps: G5 | fix(decision): V5-G8 rule 4 uses the loaded profiles' thresholds; no 0.5 default |
| b4914b5 | 5 | V5-G14 | Gaps: 신규(스펙 외) | fix(decoders): V5-G14 ffmpeg and native decoder chatter stay off the console |
| 5880cfe | 5 | V5-G2, V5-G16 | Gaps: G30; 신규(스펙 외) | fix(pdf): V5-G2 measured PDF layout — no text off the page, no overlap, full hashes (V5-G16 PDF hash) |
| 8211673 | 5 | V5-G16 | Gaps: G30; 신규(스펙 외) | fix(labels): V5-G16 analyst "시스템(자동)", Korean GUI review options, CSV label columns |
| 75a53e9 | 5 | V5-G9, S8 | Gaps: 신규(스펙 외) | fix(korean): V5-G9 stronger English detector (dictionary rule) and S8 coverage of every output |
| e52731b | 5 | V5-G13 | Gaps: 신규(스펙 외) | fix(scripts): V5-G13 every scripts/*.py answers --help in Korean |
| cdce861 | 5 | V5-G11 | Gaps: 신규(스펙 외) | test(qa): V5-G11 every tests/qa docstring starts with its QA ID; meta-test enforces it |
| 1b24ae7 | 5 | V5-G9 | Gaps: 신규(스펙 외) | fix(korean): V5-G9 follow-up — an argparse choice set is an identifier, words in braces are not |
| daf0143 | 5 | WP-J | Gaps: 신규(스펙 외) |  |
| 69b4ace | 6 | N6-1, V5-G8 | Gaps: G5 | fix(decision): N1 (=V5-G8) calibrated model direction follows the profile threshold, not 0.5 |
| 55072e2 | 6 | N6-2, N6-9, N6-10, N6-11 | Gaps: G7, G30, G31; 신규(스펙 외) |  |
| 923878d | 6 | N6-5 | Gaps: 신규(스펙 외) |  |
| 47a42a9 | 6 | N6-15 | Gaps: G7 |  |
| 846b0e3 | 6 | N6-4, N6-7 | Gaps: 신규(스펙 외) |  |
| 040ca3c | 6 | N6-6 | Gaps: G11 |  |
| 53d5b21 | 6 | N6-8, N6-13 | Gaps: 신규(스펙 외) |  |
| 195b190 | 6 | N6-14 | Gaps: G34 |  |
| accb8e0 | 6 | N6-12 | Gaps: 신규(스펙 외) |  |
| 52148b2 | 6 | N6-17 | Gaps: G28; 신규(스펙 외) |  |
| e368828 | 6 | N6-16 | Gaps: 신규(스펙 외) |  |
| 26e3b6f | 6 | WP-J | Gaps: 신규(스펙 외) |  |
| 36f5864 | 7 | Y12 | Gaps: 신규(스펙 외) |  |
| 0c5099b | 7 | X3 | Gaps: G32 |  |
| c9728a0 | 7 | X1 | Gaps: G7, G12 |  |
| feaf5bb | 7 | X2 | Gaps: G30, G31 |  |
| c4078da | 7 | X4 | Gaps: G34 |  |
| e1376eb | 7 | Y1, Y2, Y3, Y7 | Gaps: G7; 신규(스펙 외) |  |
| 1cd269d | 7 | Y4 | Gaps: G11 |  |
| 4756a40 | 7 | Y5, Y6 | Gaps: 신규(스펙 외) |  |
| 5f0d361 | 7 | Y8 | Gaps: G34 |  |
| ec46722 | 7 | Y9 | Gaps: G34 |  |
| c1c959f | 7 | Y10 | Gaps: G30 |  |
| 46f25c9 | 7 | Y11 | Gaps: 신규(스펙 외) |  |
