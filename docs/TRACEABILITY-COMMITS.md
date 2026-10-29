# 검증 ID → 스펙 갭 ID 추적표 (Y13, P12)

<!-- 생성 파일: scripts/build_traceability_commits.py가 docs/traceability-commits.json과 git 히스토리에서 만든다. 손으로 고치지 말 것 — 매핑은 JSON의 ids[]를 고친 뒤 스크립트를 다시 실행한다. -->

Y13/P12: 검증 결함 ID(라운드 1–11)를 0단계 스펙의 갭 ID(G1–G34) 또는 신규(스펙 외) 사유에 매핑하고, 현재 phase0 히스토리의 커밋마다 제목의 ID와 'Gaps:' 줄을 적는다. 라운드 3과 라운드 6의 N은 서로 다른 집합이므로 N3-x / N6-x로, 라운드 5의 G1–G16은 스펙의 G1–G34와 충돌하므로 V5-G1…V5-G16으로 표기한다. 매핑(ids[], wp_gaps, unused_ids, notes)은 손으로 관리하는 데이터이고, commits[]와 ids[].commits는 scripts/build_traceability_commits.py가 git 히스토리에서 다시 만든다(제목으로 이전 항목과 매칭).

범위: `84dcb09..f56b467`(병합 커밋 제외, 커밋 781개). 이 표는 **표를 재생성한 커밋의 부모까지**를 덮는다 — 재생성 커밋 자신의 해시는 표에 없다(자기 해시를 담을 수 없음). CI(`python scripts/build_traceability_commits.py --check`)가 같은 범위를 히스토리에서 다시 만들어 커밋된 표와 비교한다.

요약: ID 354개 — 스펙 갭에 매핑 232개(그중 신규 사유 병기 80개), 신규(스펙 외)만 122개.

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
| D1 | 1 | 독립 CLI 서브커맨드가 옛 밴드(낮음/주의/높음) 출력 → 세 결론 계약 또는 계층 진단 | G5, G6, G7, G24 |  | 15af9b7, 63fa8c8, 7d1c689 |
| D2 | 1 | api_server /api/analyze/*·classify·multimodal이 통합 경로 우회 | G7 |  | 15af9b7 |
| D3 | 1 | /api/analyze-file이 forensic 밴드·게이트 없는 픽셀 점수 반환 | G7, G13, G17 |  | 15af9b7 |
| D4 | 1 | legal-report가 scan 결과와 불일치(근거 0건) | G7 |  | 15af9b7 |
| D5 | 1 | 증거설명서가 cwd 기준 재해시 → scan의 sha256 사용 | G30 |  | eba5d27, e957307 |
| D6 | 1 | 적합성 표 통과 기준 약함(QA-ADV-2·OUT-3·OUT-4·IN-1) | G7, G12, G13 |  | 877dbba, 8be2b56, 14aec3c |
| D7 | 1 | JPEG EXIF·XMP 미파싱(카메라 EXIF·XMP 생성 도구 근거) | G6 |  | 2b0a4f7 |
| D8 | 1 | C2PA 리더 예외가 absent/ran으로 기록 | G1 |  | b0ffd0e |
| D9 | 1 | zip 폭탄 행·거부 멤버 coverage·컨테이너 sha256 누락 | G12, G30, G34 |  | 34801a8, 8723fd5 |
| D10 | 1 | 폴더 안 심볼릭 링크가 보고서에서 사라짐 | G12 |  | 34801a8 |
| D11 | 1 | 텍스트 source_guess가 결론처럼 보임 | G4, G24 |  | 8292380 |
| D12 | 1 | pixel score/confidence 필드·CSV 열이 결론처럼 보임 | G17 |  | 3e8dcc5 |
| D13 | 1 | 보정 없는 심층 신호(inpaint)가 evidence에 들어가 규칙 5를 막음 | G17 |  | 12f9c90, 63fa8c8 |
| D14 | 1 | 서명 검증 CLI(verify-report) 없음 | G30 |  | 15af9b7 |
| D15 | 1 | OpenCV 5에 CascadeClassifier 없음 → 얼굴 검출 불가, GIF는 skipped | G12 |  | 877dbba |
| D16 | 1 | 기타: summary 밴드 키, 영어 오류, 삼킨 예외, 측정 게이트 위조 매니페스트 등 | G1, G5, G28 |  | 2b0a4f7, 9742076, 14aec3c, 6ed3e3a, 7d1c689 |
| R1 | 2 | /api/scan/stream·아카이브 /api/check가 폴더 스캔 본체를 사용 | G7 |  | f357390, 1496970 |
| R2 | 2 | 영상의 오디오 트랙 분석이 자체 av_audio coverage 항목 | G12 |  | b6eea4e |
| R3 | 2 | 문서 creator/application 메타데이터 출처 추정은 참고 전용 | G24 |  | 83e86fb |
| R4 | 2 | 0단계에서 추가된 영어 사용자 문구 한국어화; 프로필의 미검증 수치 제거 | G26 | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 6bf9a93, c923521, 574483a |
| R5 | 2 | 아카이브 컨테이너 행을 결론별로 집계(요약 일치) | G7 |  | f357390 |
| R6 | 2 | pymupdf 없을 때 PDF 증거설명서는 한국어 안내 exit 2(traceback 아님) | G29 |  | 534d894 |
| R7 | 2 | 얼굴 미검출은 '얼굴 미검출', 미분석은 '해당 없음' | G12 |  | ac1582a |
| R8 | 2 | C2PA SDK 실패는 'C2PA 판독 불가(사유)' — 'C2PA 없음' 아님 | G1 |  | cd0f62e |
| R9 | 2 | 카메라 EXIF 일관성 근거는 디코드된 이미지에만 | G6 |  | 77afe5c, 1496970 |
| R16 | 2 | '점수는 검토 우선순위' 문구 제거; CSV 보정점수/결론 열; --include-low 의미 | G5 |  | 358029a, 1496970, 2e053a5 |
| N3-1 | 3 | coverage 사유·오류에 파일 시스템 경로 없음 | — | 신규(스펙 외): 보고서·coverage 사유에 파일 시스템 경로 노출 — 갭 목록 밖(정보 노출) | 802a8d2 |
| N3-2 | 3 | 해시할 때 심볼릭 링크를 따라가지 않음; 결론 없는 행의 상태 줄 | G30 |  | a6f3c25 |
| N3-3 | 3 | 스캔 스키마가 draft 2020-12에서 null result 허용 | — | 신규(스펙 외): 계약 스키마 정확성(공통 규칙 6) — 갭 목록 밖 | 37ad939 |
| N3-4 | 3 | PyMuPDF import가 stdout에 출력하지 않음 | — | 신규(스펙 외): PyMuPDF import가 stdout 오염(JSON 출력 깨짐) — 갭 목록 밖 | 37ad939 |
| N3-5 | 3 | 아카이브 컨테이너 행에 집계 근거 항목 | G12 |  | a25b0a8 |
| N3-6 | 3 | C2PA 해시 불일치는 'C2PA 무결성 불일치'(원본성 근거 아님) | G6 |  | a25b0a8 |
| N3-7 | 3 | 한국어 상태·help·401; 빈 키 파일은 오류(서명 없이 진행하지 않음) | G30 | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 2e053a5 |
| N3-8 | 3 | 건너뛴 하위 폴더 수·취소 집계·로그 — 조용한 누락 없음 | G12 |  | 2e053a5 |
| B1 | 4 | 단일 파일 진입점도 폴더 스캔처럼 아카이브를 펼침 | G7 |  | 7015bf0 |
| B2 | 4 | HTML 보고서 영어 문구 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 89c2cdc |
| B3 | 4 | HTML·포렌식 PDF의 raw skipped 상태, PDF 문서 번호 폰트 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 89c2cdc |
| B4 | 4 | legal-report 텍스트의 raw 코드·영어 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 89c2cdc |
| B5 | 4 | GUI 출처 추정·archive_member 라벨 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 24437b1 |
| B6 | 4 | C2PA SDK 영어 오류 메시지 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | fd6f100 |
| B7 | 4 | doctor JSON 출력 오염·영어 표 | G29 | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 746a4d2 |
| B8 | 4 | --pdf-out 영어 PDF → 한국어 PDF 또는 exit 2; --help 한국어 | G29 | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | a3bf814, 20252d2, 2462701, b6fc45f, 5729d48 |
| S1 | 4 | 아카이브 멤버를 'evil.zip::a1111.png' 전체 이름으로 | — | 신규(스펙 외): 보고서의 압축 멤버 전체 이름 표기 — 갭 목록 밖 | 89c2cdc |
| S2 | 4 | 포렌식 PDF 심볼릭 링크 해시 줄 문구 | G30 |  | 89c2cdc |
| S3 | 4 | --redact-paths가 models[].profile 설치 경로도 숨김 | — | 신규(스펙 외): --redact-paths가 도구 설치 경로를 남김(정보 노출) — 갭 목록 밖 | 0a3e142 |
| S4 | 4 | 없는 폴더/파일 입력 사유 표시, exit 2 문서화 | G29 |  | 4334d26 |
| S5 | 4 | GUI CSP가 blob: 미디어 허용 | — | 신규(스펙 외): GUI CSP media-src blob: 누락(미리보기 실패) — 갭 목록 밖 | 0f754f2 |
| S6 | 4 | 증거설명서 프로비넌스에 in-sample 주의 문구 | G28 |  | 89c2cdc |
| S7 | 4 | 오디오 모델 limitation 중복, 비활성 프로필 안내 제외 | G28 |  | fec9fb6 |
| S8 | 4 | test_korean_output 영어 탐지 우회 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 901a5c8, bfddf75 |
| S9 | 4 | 테스트 미사용 import 2건 | — | 신규(스펙 외): 미사용 import(공통 규칙 4 린트) — 갭 목록 밖 | dc2ddfa |
| W1 | 4 | _iter_files 전역 경로 문자열 정렬 | G32 |  | 0d8019f |
| W2 | 4 | QA 테스트를 스펙이 명명한 4개 파일로 통합 | — | 신규(스펙 외): WP-J 파일 배치(스펙 문자 그대로) — 갭 목록 밖 | 3debaac |
| W3 | 4 | 커밋 트레일러 일괄 수정(세션 소유자 작업) | — | 신규(스펙 외): 커밋 트레일러(공통 규칙 1) — 갭 목록 밖 | — |
| V5-G1 | 5 | forensic/classify/explain/multimodal/agent 텍스트 출력의 raw 코드 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 5e2c6b5 |
| V5-G2 | 5 | PDF 내용 손실: 페이지 밖 텍스트, 겹침, 해시 잘림 | G30 | 신규(스펙 외): PDF 레이아웃(페이지 밖 텍스트·겹침) — 갭 목록 밖 | b630e34 |
| V5-G3 | 5 | vendor-weights 영어 출력, MISSING 개수 불일치 | G9 | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 0f38230 |
| V5-G4 | 5 | C2PA 상태값 영어(Invalid 등) | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 0f38230 |
| V5-G5 | 5 | 없는 파일/폴더 → 한국어 오류 exit 2 | — | 신규(스펙 외): 없는 입력 파일에 traceback/빈 보고서(CLI 사용 오류) — 갭 목록 밖 | 3e29c43 |
| V5-G6 | 5 | 단일 파일 명령이 명시된 심볼릭 링크를 따라감 — 스캔과 같은 규칙 | G7 |  | 3e29c43 |
| V5-G7 | 5 | API 영어 오류 본문 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 1a5ffaf |
| V5-G8 | 5 | 결정 규칙 4가 프로필 threshold를 쓰지 않음(0.5 고정) | G5 |  | ad5b496, 07dae02 |
| V5-G9 | 5 | 영어 탐지기 강화(사전 규칙)와 출력 검사 범위 확대 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | bfddf75, 811fc5d |
| V5-G10 | 5 | 커밋 71625d0, a23e5a6 메시지에 ID 없음(세션 소유자 리워드) | — | 신규(스펙 외): 커밋 메시지 ID 누락(공통 규칙 1) — 갭 목록 밖 | — |
| V5-G11 | 5 | tests/qa docstring 첫 줄에 QA ID | — | 신규(스펙 외): WP-J QA docstring 규칙 — 갭 목록 밖 | 5297d58 |
| V5-G12 | 5 | 미사용 import 제거, ruff F401 전역 | — | 신규(스펙 외): 미사용 import·ruff F401(공통 규칙 4) — 갭 목록 밖 | 417d478 |
| V5-G13 | 5 | scripts/*.py --help 영어 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | d01addb |
| V5-G14 | 5 | ffmpeg/mpg123 stderr를 로그로 | — | 신규(스펙 외): 디코더 stderr 영어 노이즈 — 갭 목록 밖(스펙 G14 '메신저 재압축 지문'과 무관) | 073a992 |
| V5-G15 | 5 | test_forensic_pdf skipUnless에 B8 주석 | — | 신규(스펙 외): skipUnless에 ID 주석(공통 규칙 2) — 갭 목록 밖 | 5729d48 |
| V5-G16 | 5 | 분석자 '시스템(자동)', GUI 리뷰 옵션 한국어, CSV 라벨 열, PDF 64자 해시 | G30 | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | b630e34, fb336ca |
| N6-1 | 6 | (=V5-G8) 보정된 통계 근거의 direction이 프로필 threshold를 따름 | G5 |  | 07dae02 |
| N6-2 | 6 | /api/report가 심볼릭 링크 대상 해시를 서명 본문·PDF에 기록 | G30, G31 |  | d3c68ee |
| N6-3 | 6 | (=V5-G10) 커밋 71625d0 리워드(세션 소유자) | — | 신규(스펙 외): 커밋 메시지 ID 누락(공통 규칙 1) — 갭 목록 밖 | — |
| N6-4 | 6 | 모든 서브커맨드 입력 경로 검사 — 한국어 오류 exit 2 | — | 신규(스펙 외): CLI 입력 경로 오류 처리(사용 오류 exit 2) — 갭 목록 밖 | 70701c0 |
| N6-5 | 6 | 프레임워크 HTTP 오류 본문 영어(404/405/422/500/501) | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | e8ce32d |
| N6-6 | 6 | 캐시 키에 확장자, 복원 행의 경로 텍스트 재생성 | G11 |  | ba3d40a |
| N6-7 | 6 | 없는 경로 처리 불일치(N4에 통합) | — | 신규(스펙 외): CLI 입력 경로 오류 처리(N6-4에 통합) — 갭 목록 밖 | 70701c0 |
| N6-8 | 6 | 스캔 표 유형 열의 raw unsupported | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 3d7afd9 |
| N6-9 | 6 | stdlib /api/report가 본문 format 무시(서버 간 불일치) | G7 |  | d3c68ee |
| N6-10 | 6 | 웹/API 포렌식 PDF의 아카이브 멤버 해시 | G7, G30 |  | d3c68ee |
| N6-11 | 6 | FastAPI /api/report 잘못된 입력에 200, 항목 스키마 검증 | — | 신규(스펙 외): /api/report 요청 검증(400 + 항목 계약) — 갭 목록 밖 | d3c68ee |
| N6-12 | 6 | 영어 탐지기 우회(조사 접합, 하이픈, camel/snake 문장) | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | c7eff10 |
| N6-13 | 6 | 사용자용 사유의 식별자(failed:zip:…, allow_symlinks=false, WP-I) | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 3d7afd9 |
| N6-14 | 6 | 중첩 아카이브 깊이 2 → 8(예산 안) | G34 |  | 1a552af |
| N6-15 | 6 | scan_folder가 스펙대로 (summary, items) 반환(WP-F) | G7 |  | b1c94dc |
| N6-16 | 6 | tests/qa docstring 첫 줄 '<QA-ID>: <통과 기준 원문>' | — | 신규(스펙 외): WP-J QA docstring 원문 규칙 — 갭 목록 밖 | 1ba2941 |
| N6-17 | 6 | 법무법인 기본값 제거, libsndfile 문구, in-sample 주의 별도 줄 | G28 | 신규(스펙 외): 내장 법무법인 기본값 제거·libsndfile 문구 — 갭 목록 밖 | c5d05fb |
| X1 | 7 | 기록되지 않은 파일이 조용히 누락; evidence-statement 폴더 100개 하드코딩 | G7, G12 |  | 2ccd85c |
| X2 | 7 | /api/report가 클라이언트가 보낸 결론·근거를 그대로 서명 | G30, G31 |  | 757c4bf |
| X3 | 7 | --allow-symlinks에서 깨진/순환 링크가 행 없이 사라짐 | G32 |  | b0ac672 |
| X4 | 7 | /api/* 오류가 200 + {error} | G34 |  | 70e8188 |
| Y1 | 7 | evidence-statement x.json에 items 없음 → exit 2 | — | 신규(스펙 외): CLI 입력 검증(빈 증거설명서 exit 0) — 갭 목록 밖 | 79df8a9 |
| Y2 | 7 | --thresholds가 읽을 수 없는 파일이면 기본값으로 진행 | G7 |  | 79df8a9 |
| Y3 | 7 | scan "" → 현재 디렉토리 스캔 | — | 신규(스펙 외): CLI 입력 검증(빈 경로가 현재 폴더) — 갭 목록 밖 | 79df8a9 |
| Y4 | 7 | --cache가 기존 JSON 파일을 조용히 덮어씀 | G11 |  | 0418e4d |
| Y5 | 7 | OpenCV grfmt_png 영어가 콘솔에 출력 | — | 신규(스펙 외): OpenCV 네이티브 stderr 영어 노이즈 — 갭 목록 밖 | bee165f |
| Y6 | 7 | ml-classify 손상 이미지 → 한국어 stderr 오류 exit 2 | — | 신규(스펙 외): CLI 오류 처리(stdout JSON 오류 exit 1) — 갭 목록 밖 | bee165f |
| Y7 | 7 | --html-out이 폴더 → 스캔 전 오류 exit 2 | — | 신규(스펙 외): CLI 출력 경로 검증 — 갭 목록 밖 | 79df8a9 |
| Y8 | 7 | 웹 스캔이 max_files=-3 허용 | G34 |  | 9575ac4 |
| Y9 | 7 | 중첩 멤버 이름 x.zip.unpacked/… → a.zip::b.zip::c.png 체인 | G34 |  | a349ee2 |
| Y10 | 7 | HTML 보고서에 행별 SHA-256 없음 | G30 |  | 3b593ff |
| Y11 | 7 | 영어 탐지기 우회(대소문자 혼합 snake, 전각, 공백 없는 접합, 동형문자) | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 6508a4a |
| Y12 | 7 | test_cli_inputs를 in-process로 20 s 미만 | — | 신규(스펙 외): 테스트 실행 시간(test_cli_inputs 34.7 s) — 갭 목록 밖 | 67c953b |
| Y13 | 7 | 커밋 메시지 갭 ID 누락 → 이 매핑표 | — | 신규(스펙 외): 커밋 추적성(공통 규칙 1) — 갭 목록 밖 | 382fb18 |
| Z1 | 8 | realtime --scores abc → 사용 오류 exit 2(종전 exit 1·처리 오류 1건) | — | 신규(스펙 외): CLI 옵션 값 검증(사용 오류 exit 2) — 갭 목록 밖 | 12af9d0 |
| Z2 | 8 | vendor-weights pin <없는 프로필> → 오류: 파일을 찾을 수 없습니다 exit 2(종전 exit 1) | — | 신규(스펙 외): CLI 입력 경로 오류 처리(사용 오류 exit 2) — 갭 목록 밖 | 12af9d0, 1b8f2b1 |
| Z3 | 8 | forensic <파일>/ (끝 구분자) → 폴더가 아니라 파일입니다 exit 2, 모든 명령의 require_input_path에서 정규화 | G7 |  | 12af9d0 |
| Z4 | 8 | verify-report 깨진·객체 아닌 JSON → stderr 오류: 보고서 JSON을 해석할 수 없습니다 exit 4(종전 stdout '읽을 수 없음') | — | 신규(스펙 외): CLI 오류 처리(stdout 대신 stderr, 문서화된 exit 4) — 갭 목록 밖 | 12af9d0 |
| Z5 | 8 | --*-out 출력이 없는 폴더 안 → 검사 전 오류: 출력 폴더가 없습니다 exit 2, 폴더를 만들지 않음 | — | 신규(스펙 외): CLI 출력 경로 검증(Y7 연장) — 갭 목록 밖 | 12af9d0 |
| P1 | 8 | /api/report가 행을 스캔 폴더가 아니라 read root 기준으로 해석 → 하위 폴더 스캔 보고서가 루트의 동명 파일을 서명 | G30, G31 |  | 6e5fe69 |
| P2 | 8 | 캐시 이름 변경 치환이 파일명을 정규식 템플릿으로 사용 → 백슬래시 파일명에서 warm --cache 스캔 exit 1 | G11 |  | fb1b52b |
| P3 | 8 | --recursive --allow-symlinks가 디렉토리 링크를 무한 추적(루트 상위·상호 링크) | G32, G34 |  | 62bd96e |
| P4 | 8 | 비UTF-8 --thresholds 등 파일 읽기 진입점의 UnicodeDecodeError traceback → 한국어 exit 2 | G7 |  | edafb9a |
| P5 | 8 | 거부된 아카이브 멤버·건너뛴 하위 폴더 안 파일이 '기록되지 않은 파일'에 미집계 | G12, G34 |  | 00bd8a5 |
| P6 | 8 | 업로드 파일명이 read root 파일명과 같으면 루트 파일로 재분석되어 서명 | G30, G31 |  | 6e5fe69 |
| P7 | 8 | 실제 경로의 '::'가 아카이브 멤버 경로와 충돌 → 행 식별자 container/member 필드, 표시 '::' 이스케이프 | G30, G34 |  | 9d89129 |
| P8 | 8 | api-serve 고유 엔드포인트가 없는 파일에 200/500, 비객체 JSON 본문 500, 비미디어 preview 403 | G8, G34 |  | aa0afa7 |
| P9 | 8 | Y1/Z4 오류에 영어 예외 상세(JSONDecodeError 메시지) | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | edafb9a |
| P10 | 8 | 읽기 전용 출력 폴더가 스캔 후에야 실패('처리 오류 N건'으로 오해) | — | 신규(스펙 외): CLI 출력 경로 검증(Y7/Z5 연장) — 갭 목록 밖 | 4ce5a7e |
| P11 | 8 | --json-out 등이 검사 대상 폴더 안·입력 JSON을 가리킬 수 있음 | G31 |  | 4ce5a7e |
| P12 | 8 | Y13 추적성 표의 해시가 전부 리라이트 이전 → 현재 히스토리에서 재생성하는 스크립트와 CI --check | — | 신규(스펙 외): 커밋 추적성(공통 규칙 1) — 갭 목록 밖 | 3baee5c, c19738e, d64923b |
| P13 | 8 | 영어 탐지기 우회: ProbablyFake, FakeImageDetected, probably_fake, AUTHENTIC — 결론 단어 단독 등장 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | e63c0c4 |
| P14 | 8 | Gaps를 제목에만 적은 커밋 — 규칙상 허용, 추적표 스크립트가 제목·본문 모두 인식 | — | 신규(스펙 외): 커밋 추적성(공통 규칙 1) — 갭 목록 밖 | 3baee5c |
| R9-1 | 9 | 실제 경로 'tri:::c.png'의 이스케이프가 '::'를 남김 → 행 식별자는 container/member 필드만, 실제 경로는 ':'·'\' 문자 단위 이스케이프 | G30, G34 |  | 614d1cb |
| R9-2 | 9 | --allow-symlinks 평면 스캔의 하위 폴더 파일 수가 '/'·'..'·'.'·자기 자신 링크를 따라가고 같은 폴더를 링크 수만큼 집계 → P3 워커 규칙 | G12, G32, G34 |  | cb8dd1d |
| R9-3 | 9 | /api/scan/stream·/api/check/stream이 잘못된 입력에 200 + SSE error 이벤트 → 스트림 시작 전 400/403 JSON | G8, G34 |  | aa2b705 |
| R9-4 | 9 | video --frame-root·train-neural-plan --output-dir 등 출력 폴더 옵션이 검사 폴더 안 생성 허용 → 전부 등록, 파서 메타테스트 | G31 |  | 63ea0a1 |
| R9-5 | 9 | 피드백 라벨 파일의 BOM·잘린 JSONL 줄을 0건으로 읽고 종료 코드 0 → BOM 제거, 잘린 줄 n행 오류 exit 2 | G7 |  | 63ea0a1 |
| R9-6 | 9 | 영어 탐지기 미탐: verdict=fake, result:fake, #fake, fakes/faked, authentic입니다, fake-image, AI-generated 등 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | 4b2708a |
| R9-7 | 9 | --install BUNDLE_DIR·위치 인수 folder/file/report 등 영어 플레이스홀더, 에코된 입력의 개행 미이스케이프 | — | 신규(스펙 외): 공통 규칙 3(출력 문자열은 한국어) 위반 — 갭 목록 밖 | ada58f7 |
| R9-8 | 9 | 서비스 문서의 /api/review-marks(404)·'업로드 엔드포인트 없음' 오기, 빈 /api/analyze/text 200 → 문서 정정, 400, 엔드포인트 메타테스트 | G8 | 신규(스펙 외): 서비스 문서 정합성 — 갭 목록 밖 | 4ef3b62 |
| R9-9 | 9 | 서명 본문·렌더 보고서에 scan_root 미기록 → 읽기 루트 기준 상대 경로를 서명 본문에 기록, 보고서 헤더에 표시 | G30, G31 |  | 6919550 |
| R9-10 | 9 | build_traceability_commits --check가 표 이후 커밋 수와 무관하게 통과 → 재생성 커밋 1개(추적표 파일만)를 초과하면 실패 | — | 신규(스펙 외): 커밋 추적성(공통 규칙 1) — 갭 목록 밖 | d2036db, b88ef92, 3e256fc, eefacd1, fc1a568, 21717bd |
| R10-1 | 10 | 파일명이 평문 출력에 원문 그대로(CR·ESC·LF·'\|') → 모든 렌더에서 result_text.display_name로 이스케이프 | G6, G30 | 신규(스펙 외): 보고서 렌더링 무결성 — 갭 목록 밖 | fde64d0 |
| R10-2 | 10 | CSV 수식 주입(=,+,-,@,TAB,CR) → 앞에 ' 붙이기 | G30 | 신규(스펙 외): CSV 수식 주입 — 갭 목록 밖 | fde64d0 |
| R10-3 | 10 | TEST-DELETIONS.md가 리라이트 이전 해시 인용·skip을 통과로 표시 → 커밋 제목으로 인용, skip이면 건너뜀(환경) | — | 신규(스펙 외): QA 기록 정확성 — 갭 목록 밖 | e601adf, b600a58 |
| R10-4 | 10 | feedback --scan-json이 '::'/'\:' 포함 실제 파일명 라벨 미매칭 → 원문 경로·(container, member)로 조인 | G30, G34 |  | e1a9598 |
| R10-5 | 10 | 예기치 않은 오류의 종료 코드(verify-report 깊은 JSON 1, --port 범위) → 문서화된 입력 오류 코드 | G29 |  | 40e7ec6 |
| R10-6 | 10 | 단일 파일 업로드 행 경로의 '::' 미이스케이프 → 실제 파일과 같이 이스케이프 | G30, G34 |  | 768a3f3 |
| R10-7 | 10 | 영어 탐지기 미탐(【fake】, verdict→fake) → 화살표·전각 괄호를 토큰 경계로 | — | 신규(스펙 외): 출력 한국어 규칙(공통 규칙 3) — 갭 목록 밖 | e9b881f |
| R10-8 | 10 | 한국어 int/float 메시지, feedback 행 번호, R9-10 메시지의 CONFORMANCE.md, 중복 제외 표기, api-serve 라우트 문서화 | G8, G12 | 신규(스펙 외): 문서·메시지 정합성 — 갭 목록 밖 | 7d3c508 |
| R10-9 | 10 | R9-4 메타테스트가 이름 패턴만 검사 → Path 형 인수는 전부 쓰기 대상 등록 또는 읽기 전용 선언 | G31 |  | 81e94ea |
| R11-1 | 11 | UTF-8이 아닌 파일명(PEP 383 surrogate)이 스캔 전체를 깨뜨림(CLI exit 2·빈 JSON, 웹 400 영어, API 500) → 모든 JSON을 surrogate-safe(\udcXX)로, 행은 정상 분석 | G30 | 신규(스펙 외): 비 UTF-8 파일명 처리 — 갭 목록 밖 | 3b12bd1 |
| R11-2 | 11 | QA-SYS-10이 전체 스위트의 skip을 무시하고 통과 표시 → skip이 있으면 건너뜀(환경)·목록 기재, 환경 의존 테스트 문서화 | — | 신규(스펙 외): QA 기록 정확성 — 갭 목록 밖 | d10e95f, 77a5463 |
| R11-3 | 11 | /api/report, /api/feedback 깊은 중첩 JSON → 500 → 400 'JSON 중첩이 너무 깊습니다' | G34 | 신규(스펙 외): 요청 본문 처리 — 갭 목록 밖 | b661b3f |
| R11-4 | 11 | display_name의 '\\|' 이스케이프가 단사가 아님 → 모든 백슬래시를 '\\'로 | G30 | 신규(스펙 외): 보고서 렌더링 무결성 — 갭 목록 밖 | e402a18 |
| R11-5 | 11 | 영어 탐지기 미탐(fake✓, ▶fake◀, fake的 …) → 비 ASCII S*/P*·CJK 한자를 토큰 경계로 | — | 신규(스펙 외): 출력 한국어 규칙(공통 규칙 3) — 갭 목록 밖 | 8388572 |
| R11-6 | 11 | HTML 보고서 임베드 JSON에 '<!--<script>' 원문 → < > & U+2028/2029 이스케이프 | G30 | 신규(스펙 외): 보고서 렌더링 무결성 — 갭 목록 밖 | e402a18 |
| R11-7 | 11 | 증거설명서 Markdown에 이름의 링크/이미지 문법 미중화 → Markdown 활성 문자 이스케이프 | G30 | 신규(스펙 외): 보고서 렌더링 무결성 — 갭 목록 밖 | 4c2e7b1 |
| R11-8 | 11 | '상세는 로그 참조 — 상세는 로그 파일' 중복 → 한 번만 | G29 |  | 51eac89 |
| R11-9 | 11 | 테스트 소스의 raw RLO/zero-width 문자 → 이스케이프 시퀀스, ruff PLE2502/2515 활성화 | — | 신규(스펙 외): 소스 위생 — 갭 목록 밖 | e402a18 |
| R11-10 | 11 | GUI 근거 제목·상세 등이 displayName을 거치지 않음 → 모든 결과 문자열에 적용(HTML 보고서와 동일) | G30 | 신규(스펙 외): 보고서 렌더링 무결성 — 갭 목록 밖 | 4c2e7b1 |
| R11-11 | 11 | str 형 경로 옵션이 R10-9 메타테스트를 우회 → 이름·도움말·기본값으로 경로 인수 판별 | G31 |  | 5161a8b |
| R11-12 | 11 | 웹 서버가 모든 미지 경로에 GUI 제공 → '/', '/gui*', '/api/*' 외 404 한국어 JSON | G31 | 신규(스펙 외): 서비스 표면 정합성 — 갭 목록 밖 | 40a862a |
| R11-13 | 11 | CSV 전각 ＝＋－＠ 미보호 → NFKC 기준 수식 문자 보호 | G30 | 신규(스펙 외): CSV 수식 주입 — 갭 목록 밖 | 4c2e7b1 |
| R11-14 | 11 | JSON stdout에 raw bidi/C1 유지 → 행에 display_name(이스케이프본) 추가, name 원문 유지 | G30 | 신규(스펙 외): JSON 계약 — 갭 목록 밖 | 8292a74 |
| R12-1 | 12 | UTF-8이 아닌 이름의 영상이 cv2.VideoCapture segfault로 스캔 전체·웹 서버를 죽임 → native_safe_path가 ASCII 이름으로 스테이징 | G1 | 신규(스펙 외): 비 UTF-8 파일명의 네이티브 디코더 크래시 — 갭 목록 밖 | d8e05ea |
| R12-2 | 12 | non-UTF-8 이름의 오디오·PDF 검사 실패 → 같은 내용 ASCII 사본과 동일 분석 | G1 | 신규(스펙 외): 비 UTF-8 파일명 — 갭 목록 밖 | d8e05ea |
| R12-3 | 12 | zip/tar 중복 멤버 경로가 서로 덮어써 sha256·근거 소실 → 항목별 고유 추출, #2 접미, member_index | G34, G30 |  | d710000 |
| R12-4 | 12 | GUI 미리보기/히트맵이 non-UTF-8 행에서 영어 'URI malformed' → path_b64/root_b64 | G8, G31 | 신규(스펙 외): 비 UTF-8 경로 주소 지정 — 갭 목록 밖 | 22ab09b |
| R12-5 | 12 | 증거설명서 MD 렌더에서 서로 다른 이름이 같게 보임 → \, & 이스케이프 | G30 | 신규(스펙 외): 표시 단사성 — 갭 목록 밖 | 0ac910e |
| R12-6 | 12 | GUI 비교 슬롯이 파일명을 원문 표시 → displayName, 메타테스트 확장 | G30 | 신규(스펙 외): 표시 이스케이프 — 갭 목록 밖 | 0a482cb |
| R12-7 | 12 | 스캔 중 변경된 파일이 다른 바이트의 sha256으로 기록 → 전후 상태 비교, 변경 시 판단 불가 | G11, G30 |  | 6457bcf |
| R12-8 | 12 | Windows식 relpath가 corpus verify 포함 검사 통과 → 양쪽 경로 규칙으로 거부 | G27 | 신규(스펙 외): 경로 탈출 — 갭 목록 밖 | 041fb7f |
| R12-9 | 12 | 보고서 타임스탬프가 시간대 없는 로컬 시각 → ISO 8601 + UTC 오프셋 | G30 | 신규(스펙 외): 타임스탬프 형식 — 갭 목록 밖 | 5b002a4 |
| R12-10 | 12 | non-UTF-8 multipart 파일명이 U+FFFD로 손실 → 원시 바이트 보존 | G30 | 신규(스펙 외): 업로드 파일명 — 갭 목록 밖 | 63cf82e |
| R12-11 | 12 | 후행 슬래시 /gui/ 동작이 두 서버에서 다름 → 둘 다 404 | G31 | 신규(스펙 외): 라우트 일관성 — 갭 목록 밖 | db73702 |
| R12-12 | 12 | 영어 탐지기가 TAG 문자 뒤 단어 미탐 → Cf 문자 제거 | — | 신규(스펙 외): 테스트 도구(영어 탐지기) — 갭 목록 밖 | 68035c3 |
| R13-1 | 13 | 스테이징 임시 이름이 오디오 디코드 실패 사유에 누출 → 실행 간 JSON 차이·ASCII 사본과 불일치·캐시 오염 → staged→원래 경로 역매핑 | G11, G32 | 신규(스펙 외): 비 ASCII 파일명 스테이징 — 갭 목록 밖 | 9ee4bf8, 130dddd |
| R13-2 | 13 | 압축 파일이 추출 중 재작성되면 컨테이너 sha256이 추출 바이트와 다름 → 전후 비교, 판단 불가·해시 없음 | G11, G30, G34 |  | 476e412 |
| R13-3 | 13 | non-UTF-8 경로를 /api/analyze-file·/api/scan으로 지정 불가 → file_b64·folder_b64 | G8, G31 | 신규(스펙 외): 비 UTF-8 경로 주소 지정 — 갭 목록 밖 | 6a4aded |
| R13-4 | 13 | SIGTERM·강제 종료 시 스테이징 폴더 잔류 → 신호 처리 정리 + 오래된 폴더 청소 | — | 신규(스펙 외): 임시 파일 정리 — 갭 목록 밖 | faf6b5d |
| R13-5 | 13 | corpus verify가 non-UTF-8 relpath를 r?.jpg로 출력 → display_name | G27 | 신규(스펙 외): 표시 단사성 — 갭 목록 밖 | a9721b2 |
| R13-6 | 13 | 중첩 중복 멤버 행에 중복 사유 없음 → 멤버 행 coverage에 기록 | G34, G30 |  | a4f9b08 |
| R13-7 | 13 | cv2.imwrite가 한글 임시 경로에서 실패(Windows 위험) → imencode + Python 쓰기, 메타테스트 확장 | G1 | 신규(스펙 외): Windows 한글 사용자 경로 — 갭 목록 밖 | 0f22403 |
| R13-8 | 13 | Windows에서 한글 이름 파일 전량 복사 → 하드링크 → 8.3 짧은 이름 → 복사(상한) | — | 신규(스펙 외): Windows 스테이징 성능 — 갭 목록 밖 | faf6b5d |
| R13-9 | 13 | 렌더된 Markdown에서 GFM 자동 링크 → . : @ / 이스케이프 | G30 | 신규(스펙 외): Markdown 자동 링크 — 갭 목록 밖 | ec8e4da |
| R14-1 | 14 | 상속된 SIG_IGN 위에도 정리 핸들러 설치 → nohup 스캔이 SIGHUP에 스테이징 폴더 삭제·가짜 실패 → SIG_IGN 유지 | G1, G34 | 신규(스펙 외): 신호 처리 — 갭 목록 밖 | b914bce, cf1fe06 |
| R14-2 | 14 | 스테이징 정리 시 링크를 따라 chmod해 증거 파일 권한 변경 → 링크·하드링크 chmod 금지 | G30 | 신규(스펙 외): 증거 무변경 — 갭 목록 밖 | 0a9f42b |
| R14-3 | 14 | R13-1 이전 캐시 행이 재생되어 옛 스테이징 이름 부활 → content-v4 키, 출력 형식 세대, 오염 행 무효화 | G11 |  | fd4bc0a |
| R14-4 | 14 | 이름 복원기가 스테이징 폴더 모양 문자열을 무엇이든 치환 → 이 프로세스의 등록 폴더만 | G32 | 신규(스펙 외): 표시 단사성 — 갭 목록 밖 | fd4bc0a |
| R14-5 | 14 | CascadeClassifier가 native_safe_path 미경유(Windows 한글 경로 위험) → 경유, 로드 실패는 failed | G1, G12 | 신규(스펙 외): Windows 한글 경로 — 갭 목록 밖 | 7fefde4 |
| R14-6 | 14 | 64 MB 초과 컨테이너가 같은 크기 재작성+touch -r을 놓침 → ctime_ns 비교, 모든 크기 추출 전 해시 | G11, G30 |  | 045566a |
| R14-7 | 14 | SIGKILL 시 스테이징 외 임시 파일 잔류 → 모든 임시 파일을 세션 폴더에 | G34, G1 | 신규(스펙 외): 임시 파일 정리 — 갭 목록 밖 | b0f5f81 |
| R14-8 | 14 | Windows ctypes 분기가 실행된 적 없음 → 가짜 WinDLL로 시그니처·반환 처리 검증 | G1 | 신규(스펙 외): Windows 경로 — 갭 목록 밖 | 0a9f42b |
| R15-1 | 15 | SIGTERM/SIGHUP 정리 경쟁(워커가 정리 중 생성, 새 세션 폴더, ffmpeg 자식 미종료) → 종료 플래그, 자식 종료·PDEATHSIG, 재시도 | G1, G34 | 신규(스펙 외): 신호 처리 — 갭 목록 밖 | 15c88af |
| R15-2 | 15 | onnxruntime import 시 텔레메트리 연결 시도·deviceid 생성 → 패키지 최상위에서 텔레메트리 차단 변수 | — | 신규(스펙 외): 오프라인 보장(R-IN-4) | c5115ae |
| R15-3 | 15 | FaceLandmarker·SyncNet·Haar 재정의가 핀 없이 로드 → models/assets.json 자산 핀, 미고정은 거부 | G9, G1 |  | 8f06324 |
| R15-4 | 15 | 과거 커밋이 SIGINT 무시 상속 환경에서 자체 테스트 실패 → 이력 문서화, 테스트 자식 신호 기본값 | G34 | 신규(스펙 외): 테스트 환경 — 갭 목록 밖 | 0b44e2d |
| R15-5 | 15 | 스레드 테스트 프로세스의 fork-unsafe preexec_fn → exec 트램펄린, PLW1509 | — | 신규(스펙 외): 테스트 안전성 — 갭 목록 밖 | a1b44d1 |
| R15-6 | 15 | 비ASCII TMPDIR 시 조용한 대체 → 한국어 안내·temp_folder 기록·DEEPFAKE_LENS_TMPDIR | G34 | 신규(스펙 외): 임시 폴더 — 갭 목록 밖 | 56c817d |
| R15-7 | 15 | OUTPUT_FORMAT_GENERATION 수동 관리 → 골든 출력 해시 메타테스트 | G11 |  | 64f4dac |
| R15-8 | 15 | mediapipe→sounddevice가 gcc/ld를 띄워 TMPDIR에 임시 파일 → sounddevice 차단 | G34 | 신규(스펙 외): 임시 파일 — 갭 목록 밖 | f13fda6 |
| R16-1 | 16 | faceswap_seam·face_track이 검출기 오류를 '얼굴 미검출'로 기록 → strict 검출, 오류는 failed | G12, G1 |  | 9d7f118 |
| R16-2 | 16 | Haar 재정의 cascade가 다른 바이트면 조용히 무시 → 재정의만 사용, 불일치는 failed | G9, G1 |  | 9d7f118 |
| R16-3 | 16 | 장기 실행 서버에서 재핀·핀 제거 후 옛 자산 바이트 사용 → 핀 키 캐시 | G9 |  | 11cf522 |
| R16-4 | 16 | mediapipe import가 matplotlib으로 fc-list 자식·세션 밖 파일 생성 → matplotlib 스텁 | G34 | 신규(스펙 외): 임시 파일·자식 프로세스 — 갭 목록 밖 | b0c0285 |
| R16-5 | 16 | 골든 출력이 의존성 기반 출력 변경을 못 잡고 정당한 세대 증가를 막음 → stdlib/full 두 세트, 한국어 상수 해시, 사유 기록 증가 허용 | G11 |  | ce487f2 |
| R16-6 | 16 | ffmpeg가 운영자 터미널 상속 → -nostdin, stdin=DEVNULL | — | 신규(스펙 외): 터미널 상태 — 갭 목록 밖 | d9e178b |
| R16-7 | 16 | 직계 자식만 추적해 래퍼 손자 고아 → 프로세스 그룹·서브리퍼 감독 프로세스 | G34 | 신규(스펙 외): 자식 프로세스 정리 — 갭 목록 밖 | d9e178b |
| R16-8 | 16 | pin-asset 오류에 영어 errno·내부 tmp 이름 → 한국어 | G29 |  | 42f0344 |
| R16-9 | 16 | doctor가 자산 핀 상태 미표시 → 표·JSON에 자산 핀 상태 | G29, G9 |  | fcde16a |
| R16-10 | 16 | 임시 폴더 문구가 원인 무관·증거설명서 누락 → 원인별 문구, 증거설명서 기록 | G34 | 신규(스펙 외): 임시 폴더 — 갭 목록 밖 | ee091ac |
| R16-11 | 16 | ptrace 거부 환경에서 텔레메트리 테스트 실패 → 분리·skip | — | 신규(스펙 외): 테스트 환경 — 갭 목록 밖 | 1653be9 |
| R16-12 | 16 | git 이력 규칙 문서 불일치 → 공유 규칙·문서 갱신 | G26 |  | 1653be9 |
| R16-13 | 16 | Ctrl-C 영어 traceback → 정리 후 '중단됨' exit 130 | G34 |  | 0ce6d1b |
| R16-14 | 16 | vendor-weights --verify 없는 폴더에 통과 rc 0 → exit 2 | G29 |  | 42f0344 |
| R16-15 | 16 | with_default_signals.py 영어 traceback → 한국어 exit 127/126 | — | 신규(스펙 외): 테스트 도구 — 갭 목록 밖 | 0ce6d1b |
| R17-1 | 17 | 감독 프로세스 두 번째 PDEATHSIG 경쟁 → 프로그램 자식 고아화 | G34 |  | 27bc793 |
| R17-2 | 17 | SIGKILL된 프로그램 처리 중 감독자 EINVAL 크래시 → -9 보고 | G34 |  | 27bc793 |
| R17-3 | 17 | setsid로 다른 세션에 간 자손 생존·캡처 파이프 정체 → 반복 신호·회수, 제한 시간 읽기 | G34 |  | 27bc793 |
| R17-4 | 17 | 실행 불가 ffmpeg를 '오디오 트랙 없음'으로 기록 → failed + 종료 코드/신호 | G1, G12 |  | 8cd1a33 |
| R17-5 | 17 | 실측 랜드마크 검출기 부재 시 face_track 사유·doctor 행·없는 재정의 파일 | G12, G29, G1 |  | c1dee0a |
| R17-6 | 17 | video-frames 멤버 오류의 coverage 사유가 일반 문구 → 내부 원인·예외 클래스 | G1, G12 |  | 498558d |
| R17-7 | 17 | 스캔 캐시 키가 핀만 포함 → 실효 자산 파일·재정의·sha256 포함 | G11, G9 |  | cbf9752 |
| R17-8 | 17 | Ctrl-C: 로깅 복원 순서, import 중 인터럽트, batch 즉시 중단 | G34 |  | ee2818f |
| R17-9 | 17 | doctor와 로더의 재정의·매니페스트 해석 불일치(~ 확장, 깨진/BOM assets.json) | G29, G9 |  | 111b302 |
| R17-10 | 17 | vendor-weights --verify 프로필 0개 폴더 통과 rc 0 → exit 2 | G29 |  | 98672a1 |
| R17-11 | 17 | 0ce6d1b 추적표 매핑 G13→G34 정정, TMPDIR가 파일일 때 문구 | G34 | 신규(스펙 외): 추적표 매핑 정정 — 갭 목록 밖 | 3fe35fb |
| R17-12 | 17 | 매니페스트 제자리 재작성 테스트가 파일 시각 해상도에 의존(1 ms 커널에서 실패) | — | 신규(스펙 외): 테스트 견고성 — 갭 목록 밖 | 8f205bd |
| R18-1 | 18 | R17-7 캐시 키에 설치·venv 절대 경로 포함(회귀) → 이름·역할·sha256만 | G11, G9 |  | b42f5c1 |
| R18-2 | 18 | ~알수없는사용자 재정의 변수가 스캔 전체를 죽임(회귀) | G9, G29, G11 |  | 41c3aef |
| R18-3 | 18 | 취소된 검사의 남은 파일이 기록 없이 사라짐 → 사용자 취소 집계·부분 결과 표시 | G12, G7 |  | a3ac2aa |
| R18-4 | 18 | 메타데이터 앞 4 MB만 읽고 coverage ran 표시 | G12, G1 |  | 2b2c112 |
| R18-5 | 18 | 서명된 보고서의 중복 키가 검증 통과 | G30 |  | 1c124aa |
| R18-6 | 18 | 2억 화소 사진 EXIF 판독 실패(픽셀 폭탄 상한) | G12, G1 |  | 46e06da |
| R18-7 | 18 | 열 수 없는 영상을 rPPG가 얼굴 추적 부족으로 기록 | G1, G12 |  | e498775 |
| R18-8 | 18 | GUI 검토 표시가 상대 경로 키·전역 저장소로 사건 간 섞임 | G11 |  | 259f7e3 |
| R18-9 | 18 | 감독 프로세스가 코어 덤프를 cwd에 남김 | G34 |  | 80875cc |
| R18-10 | 18 | --allow-symlinks가 /proc 의사 파일을 분석 | G31 | 신규(스펙 외): 갭 목록 밖 항목 포함 | 3d46a51 |
| R18-11 | 18 | 표시 이름 비단사(NFC/NFD, 비ASCII 공백, 보이지 않는 문자) | G30 | 신규(스펙 외): 표시 이름 단사성 — 갭 목록 밖 | 15d789a |
| R18-12 | 18 | R17-8 잔여: import 가드 설치 전 Ctrl-C 영어 traceback | G34 |  | fe963eb |
| R18-13 | 18 | doctor와 실제 검사 결과 불일치(재정의 없음, CascadeClassifier 없음) | G29 |  | 12348ef |
| R18-14 | 18 | 추적표 ids[] 매핑이 커밋 제목과 다름 + --check 검사 추가 | G26, G27 |  | 1b5dbdd |
| R18-15 | 18 | 출력 쓰기 오류가 예기치 않은 오류로 보고, 비원자적 쓰기 | G34 | 신규(스펙 외): 출력 견고성 — 갭 목록 밖 | 9067673 |
| R18-16 | 18 | 증거설명서 사건명 기본값이 특정 죄명 | — | 신규(스펙 외): 증거설명서 기본값 — 갭 목록 밖 | 91ee0d1 |
| R18-17 | 18 | GUI가 실행 안 된 모델에 원점수 0 표시 | G1 |  | 9ecd4ed |
| R18-18 | 18 | agent 계층 진단이 confidence·agent_type 등급 출력 | G4, G24 |  | 48a07ce |
| R18-19 | 18 | 잡다한 표시 불일치 (a)~(f) | G26, G29, G1 | 신규(스펙 외): 사용자 표시 — 갭 목록 밖 | a155537, 804ce5c, afd7036, 3df21ce, c9e1f81, bc99d8a |
| R19-1 | 19 | the scan-cache key carries the runtime — which optional modules are found, their versions  | G11, G1 |  | b829570 |
| R19-2 | 19 | librosa feature extraction runs in a supervised worker process and numba compiles into a p | G1 | 신규(스펙 외): 갭 목록 밖 항목 포함 | de59997 |
| R19-3 | 19 | the GUI's report options change with the rows on screen, exports are disabled and refused  | G7, G30 |  | b7b27c5 |
| R19-4 | 19 | DEEPFAKE_LENS_LOG_DIR and DEEPFAKE_LENS_HEATMAP_DIR are absolute and checked like output p | G31 | 신규(스펙 외): 갭 목록 밖 항목 포함 | 2525982 |
| R19-5 | 19 | every path argument and web request parameter takes an unknown "~user" literally — no Engl | G34 | 신규(스펙 외): 갭 목록 밖 항목 포함 | 767f7d8 |
| R19-6 | 19 | every remaining output writer goes through the whole-file writer, whose temp file is remov | G34 | 신규(스펙 외): 갭 목록 밖 항목 포함 | 47d80c9 |
| R19-7 | 19 | an APNG's frame data (fdAT) is skipped by seeking like IDAT — not read as a metadata chunk | G12, G1 |  | c3f9594 |
| R19-8 | 19 | display_name escapes U+2800 BRAILLE PATTERN BLANK and private-use characters — "a⠀b.jpg" n | G30 | 신규(스펙 외): 갭 목록 밖 항목 포함 | 8ffd1cf |
| R19-9 | 19 | an unknown argument of a nested subcommand is reported by that subcommand's parser, and an | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | d70f460 |
| R19-10 | 19 | a layer diagnostic or comparison without a score shows "없음" — not "원점수 0" | G1 |  | bc3eea1 |
| R19-11 | 19 | the evidence statement names no charge or statute — a manipulation row leaves "관련 법조" to t | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | f6726cd |
| R19-12 | 19 | HANDOFF.md describes the current state — R17/R18 fixed, R19-1…R19-13 fixed on this branch, | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 9142684 |
| R19-13 | 19 | /api/report refuses with 409 and changed_rows when its re-analysis of a posted row differs | G30, G7 |  | 15185a0 |
| R20-1 | 20 | a FIFO, socket or unreadable subfolder in the scanned folder is re-derived as the scan's o | G30 |  | 7eef07e |
| R20-2 | 20 | every multi-file command and request runs its audio files through one worker per thread, a | G1 | 신규(스펙 외): 갭 목록 밖 항목 포함 | bf79e20 |
| R20-3 | 20 | the audio worker protocol is framed — a ready line, the worker's token and the request id  | G1 | 신규(스펙 외): 갭 목록 밖 항목 포함 | ff4c703 |
| R20-4 | 20 | a posted duplicate row must name another posted, non-duplicate row as duplicate_of, and it | G30 |  | b516bea |
| R20-5 | 20 | a row the scan hashed that is now behind a linked folder is a changed row — 409, not moved | G30 |  | c48f766 |
| R20-6 | 20 | a SIGTERM or SIGHUP during an output write removes the temp file — the CLI's cleanup handl | G34 | 신규(스펙 외): 갭 목록 밖 항목 포함 | e348c38 |
| R20-7 | 20 | tool folders decided by HOME or TMPDIR are never inside a scanned folder, and a single-fil | G31 | 신규(스펙 외): 갭 목록 밖 항목 포함 | 1ec725c |
| R20-8 | 20 | display_name escapes unassigned code points and noncharacters (category Cn) in Python and  | G30 | 신규(스펙 외): 갭 목록 밖 항목 포함 | 5ab8d28 |
| R20-9 | 20 | known options before a command name go after it — `--verbose scan X`, `--verbose doctor`,  | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 7543812 |
| R20-10 | 20 | HANDOFF.md describes the round loop and where to read the record state instead of stating  | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 2b79825 |
| R21-1 | 21 | no text of a posted row is signed — a walk-limit folder row is excluded, a loop row is sig | G30 |  | 4eaecc8 |
| R21-2 | 21 | the audio worker always runs its numeric libraries on one thread, the scan process's threa | G1, G11 |  | 00318fb |
| R21-3 | 21 | a new audio worker's ready marker is found in its byte stream before any request is sent,  | G1 | 신규(스펙 외): 갭 목록 밖 항목 포함 | e427a87 |
| R21-4 | 21 | web and api-serve without --folder / --allow-root check their working folder like a named  | G31 | 신규(스펙 외): 갭 목록 밖 항목 포함 | b4decf3 |
| R21-5 | 21 | a posted row path must be the scan's own relative form and each row is posted once — "./a. | G30 |  | fd8c3be |
| R21-6 | 21 | a single-file command's file folder is examined for the built-in tool folders too — HOME=/ | G31 | 신규(스펙 외): 갭 목록 밖 항목 포함 | 27bd4c5 |
| R21-7 | 21 | HANDOFF.md states no test or skip counts — it says where to read them and why the skip cou | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 3831b93 |
| R21-8 | 21 | the docs and comments say that only SIGTERM, SIGHUP and SIGINT (SIGBREAK) clean up an outp | G34 | 신규(스펙 외): 갭 목록 밖 항목 포함 | 517f163 |
| R21-9 | 21 | `help <not a command>` is the top-level error with exit 2 and `--verbose help` is help — a | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 8285bb0 |
| R21-10 | 21 | HOME-derived tool folders that would be inside an examined folder are relocated outside ev | G31 | 신규(스펙 외): 갭 목록 밖 항목 포함 | c43bc11 |
| R22-1 | 22 | a web report's "기록되지 않은 파일" counts are the server's own — its scan job of the folder, else | G7, G12, G30 |  | d9bf1b2 |
| R22-2 | 22 | on Windows the tool folders move to %ProgramData%\deepfake-lens\<user>, made owner-only an | G31 | 신규(스펙 외): 갭 목록 밖 항목 포함 | 8746ecf |
| R22-3 | 22 | the web GUI's reviews and feedback live in one fixed per-user application-data folder, nev | G31 | 신규(스펙 외): 갭 목록 밖 항목 포함 | e52ac97 |
| R22-4 | 22 | a posted row path is the scan's own listing — a path through a bind-mount loop folder or s | G30 |  | 958fedf |
| R22-5 | 22 | a scan stops starting audio workers after two start failures in a row — later files fail a | G1 | 신규(스펙 외): 갭 목록 밖 항목 포함 | dbf9095 |
| R22-6 | 22 | a NUL in any path value is a 400 on both servers — report rows / scan_root / heatmap_path, | G31 |  | ac0f00d |
| R22-7 | 22 | the evidence statement without pymupdf is the documented 501 before any re-analysis — it r | G30 |  | a1778a5 |
| R22-8 | 22 | an upload row's name in the signed body is limited and marked — relative, ≤ 255 characters | G30 |  | d021d3c |
| R22-9 | 22 | the published numba cache is also kept per Python environment — a new HOME is seeded from  | G1 | 신규(스펙 외): 갭 목록 밖 항목 포함 | b161bb2 |
| R22-10 | 22 | words after `help <command>` must name its subcommands — `help corpus build` is that subco | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | cb40945 |
| R22-11 | 22 | HANDOFF.md §1/§2/§9 state round 21 with R21-10 and round 22 — 22 rounds, R22-1…R22-11 fixe | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 7756d40 |
| R23-1 | 23 | a relative audio path is made absolute before it crosses into a child process — scan case, | G1 |  | 5f3990f, 6ec2974 |
| R23-2 | 23 | the per-environment numba cache is never made inside the folder of a file a single-file co | G1, G31 |  | 38911f0 |
| R23-3 | 23 | a scan job the server finished "cancelled" marks the web report cancelled with the job's o | G7, G12, G30 |  | c89cb3a |
| R23-4 | 23 | every PDF prints a character its font has no glyph for as its \uNNNN escape — Hebrew, Arab | G2, G30 |  | a30a455 |
| R23-5 | 23 | archive members the server's own re-analysis of a signed container yields without a signed | G7, G12, G30 |  | 494c18d |
| R23-6 | 23 | inherit-only ACL entries no longer make the default %ProgramData% parent unsafe, and a par | G31 | 신규(스펙 외): 갭 목록 밖 항목 포함 | ec4b17a |
| R23-7 | 23 | an examiner record that cannot be written is a Korean 409 (507 when the disk is full) with | G31 | 신규(스펙 외): 갭 목록 밖 항목 포함 | 190694d |
| R23-8 | 23 | neither examiner record is migrated into a file an operator named — DEEPFAKE_LENS_REVIEWS  | G31 | 신규(스펙 외): 갭 목록 밖 항목 포함 | 899c104 |
| R23-9 | 23 | a model_path / fusion_profile name the file system cannot look up is a 400 — a 5000-charac | G31 |  | ec1229a |
| R23-10 | 23 | the gate image is reproducible from the repository — base image pinned by digest, Debian p | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 3a2e1c4 |
| R23-11 | 23 | `help vendor-weights pin` shows the vendor-weights help with a note — pin is a value of it | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 5b51712 |
| R23-12 | 23 | the per-environment numba cache is never made inside the folder of a file a single-file co | G1, G31, G7, G12, G30 | 신규(스펙 외): 갭 목록 밖 항목 포함 | 38911f0, c89cb3a, 494c18d, ec4b17a |
| R23-13 | 23 | a true/false option with an unknown value is a 400 on both servers — recursive=maybe was r | G7 | 신규(스펙 외): 갭 목록 밖 항목 포함 | 617130d |
| R24-1 | 24 | a container a cancel left unanalyzed is the status "cancelled" with its own conclusion, an | G12, G7, G30 |  | 3d48593 |
| R24-2 | 24 | the FFmpeg readers inside the process get an absolute name too — cv2.VideoCapture and libr | G7 | 신규(스펙 외): 갭 목록 밖 항목 포함 | 77b1345 |
| R24-3 | 24 | a name too long to make absolute stays "./"-relative and the audio worker is told the work | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | a9d7eaa |
| R24-4 | 24 | reviews.json is read again after a failed read or a change on disk — a repaired file kept  | G31 | 신규(스펙 외): 갭 목록 밖 항목 포함 | 33f8d8c |
| R24-5 | 24 | classify of an unreadable file prints its conclusion with the tool-attribution layer faile | G12, G1 |  | 7abbfaa |
| R24-6 | 24 | the old GUI's smoke passes in the repo's gui image — Playwright's Chromium when Google Chr | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | d3333df |
| R24-7 | 24 | the PDFs draw the case fields through display_name — a literal "\u05e2 corp" printed exact | G2, G30 |  | 799ee6a |
| R24-8 | 24 | /api/scan/stream reads recursive with parse_flag — the R23-13 true/false values, 400 for a | G7 | 신규(스펙 외): 갭 목록 밖 항목 포함 | 97227a0 |
| R24-9 | 24 | qa-main runs with a HOME and XDG folders under /tmp/qa-main and without writing bytecode — | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 07df093 |
| R24-10 | 24 | the R23-2 end-to-end test gives its project venv every site folder this interpreter import | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 4c2dfc0 |
| R24-11 | 24 | `help <command> -h` / `--help` is that command's help with exit 0 | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | a73da37 |
| R25-1 | 25 | whether a report row was left unanalyzed by a cancel is the server's scan job's knowledge  | G12, G7, G30 |  | c464ac3 |
| R25-2 | 25 | an unreadable WAV, MP4, PDF or archive is the failed row with its real read error — it was | G12 |  | 50ef8e8 |
| R25-3 | 25 | the evidence statement's 「작성자 및 일자」 column, the scan folder name and every case field prin | G2, G30 |  | c700e85 |
| R25-4 | 25 | a non-ASCII name in a working folder deeper than PATH_MAX is staged through a name the ker | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 2d10ee0 |
| R25-5 | 25 | .m4a, .aac and .wma are decoded by ffmpeg before the audio features — librosa 1.0 has no a | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | dfc5889 |
| R25-6 | 25 | a member name with ":" is refused only in a real absolute, drive, UNC or traversal form —  | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | f2fc514 |
| R25-7 | 25 | a reason that names the analyzed file is written "<root>/…" however the scan was started — | G7 |  | 209ceb2 |
| R25-8 | 25 | `help help -h` and `help -h -h` are the top-level help with exit 0 | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | fcf56fe |
| R25-9 | 25 | multimodal --av-sync decodes the video's audio track with ffmpeg and records why the measu | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 8483bf4 |
| R26-1 | 26 | R26-1 an archive member's chain escapes each level's path, so a literal member "inner.zip: | G30 |  | e48262b, 28db773 |
| R26-10 | 26 | R26-10 an ffmpeg step stopped at its time limit names the step, the file and the limit — i | G12 |  | 2594bb9 |
| R26-2 | 26 | R26-2 every reason of a row names a file "<root>/…" from the absolute folder, the relative | G7 |  | 7c74bd7 |
| R26-3 | 26 | R26-3 on macOS / BSD a UTF-8 non-ASCII name in a working folder deeper than PATH_MAX is ha | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 21a84a5 |
| R26-4 | 26 | R26-4 archive members named "~$보고서.docx", "~WRL0001.tmp" or — on POSIX — "con.png" are ana | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | d2306f5 |
| R26-5 | 26 | R26-5 a finished /api/scan/stream is a server-held scan job — a report of a cancelled stre | G30 |  | 61c036a |
| R26-6 | 26 | R26-6 one PDF prints "\|" one way — file names, member paths, the scan folder name and reas | G2 |  | 89cc44e |
| R26-7 | 26 | R26-7 `help help <topic>` is the help of <topic> and `help help help` the top-level help,  | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | a95ba10 |
| R26-8 | 26 | R26-8 every scan result's row identities must be unique and round-trip, or the scan gives  | G30 |  | 36f0436 |
| R26-9 | 26 | R26-9 multimodal --av-sync decodes only what the energy envelope needs — 8 kHz mono of the | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | ffb347f |
| R27-2 | 27 | R27-2 the report labels the options as the scan job's own — 「검사 옵션(서버가 이 검사 작업을 실행한 옵션)」 — | G30 |  | 0fc5c71 |
| R27-5 | 27 | R27-5 image scans and HTML reports back within 25% of 2130892 — the C2PA trust bundles are | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 3622dc6, 4420e63, 96b24f4, c4e00c4 |
| R28-1 | 28 | R28-1 a single upload is analyzed under the client's own base name in a fresh temp folder, so the | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | cb7283d |
| R28-2 | 28 | R28-2 BL-23 carries the round-28 document measurement (+46%/+53% vs 2130892) | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | bd798fb |
| R28-3 | 28 | R28-3 :tech2 (the only image with the PerTh weight) is a required gate image | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | bd798fb |
| R28-4 | 28 | R28-4 Opus encoder-string match item not marked self_declared goes to BACKLOG as BL-33 | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | bd798fb |
| R28-5 | 28 | R28-5 scan-status done before the job file is written goes to BACKLOG as BL-34 | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | bd798fb |
| R29-1 | 29 | R29-1 the web quick check (/api/check) analyzes a 0-byte uploaded file like /api/analyze-upload and  | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | c6fd8f3, 6dfd3fa |
| R29-2 | 29 | R29-2 an archive upload whose client name cannot be a file name here is analyzed under the stand-in  | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | d543820, 6dfd3fa |
| R29-3 | 29 | R29-3 an upload is routed as an archive or a single file by the client's base name (x.zip\ gives the | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | bdc1438, 6dfd3fa |
| R29-4 | 29 | R29-4 the upload cap counts uploaded files, not rows; every file over it is its own skipped row | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 8cbdeb3, 6dfd3fa |
| R29-5 | 29 | R29-5 the web quick check refuses more than one file part instead of silently dropping the others | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 8b778c4, 6dfd3fa |
| R29-6 | 29 | R29-6 /api/compare counts a 0-byte file, takes exactly two files and never answers 500 for an odd cl | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 43521d8, 6dfd3fa |
| R30-1 | 30 | R30-1 a part a browser sends as message/* (.eml) is a file on every upload path — it vanished (「업로드된 | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 210cf09, 5bed165, 454fd84 |
| R30-2 | 30 | R30-2 a part a browser sends as multipart/* (.mht) is analyzed as the bytes sent — it was re-seriali | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 210cf09, 5bed165, 454fd84 |
| R30-3 | 30 | R30-3 a backslash in a browser-sent file name is kept (a\b.png was ab.png; a non-KakaoTalk name read | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 210cf09, 5bed165, 454fd84 |
| R30-4 | 30 | R30-4 a comparison that measured nothing shows no number (0-byte or non-audio WAV, empty text) — it  | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 548a168, 454fd84 |
| R30-5 | 30 | R30-5 no/empty boundary and a boundary the body never uses get their own 400 — they said 「업로드 본문이 잘렸 | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 210cf09, 5bed165, 454fd84 |
| R31-1 | 31 | R31-1 a multipart body whose lines end in LF or CR (LF-only, mixed, LF preamble) is parsed as 0cfbd0 | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 1286f70, 94ca8ea, 8514c9d |
| R31-2 | 31 | R31-2 the media type is case-insensitive (Multipart/Form-Data was 「multipart/form-data 업로드가 필요합니다」) | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 1286f70, 8514c9d |
| R31-3 | 31 | R31-3 an empty filename* falls back to filename (a file sent read 「업로드된 파일이 없습니다」) | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 1286f70, 8514c9d |
| R31-4 | 31 | R31-4 a filename* charset that raises (undefined) is skipped like an unknown one — every upload path | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 1286f70, 8514c9d |
| R31-5 | 31 | R31-5 the GUI quick check's archive toast counts the members analyzed and names an archive that did  | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 3127f14, 8514c9d |
| R31-6 | 31 | R31-6 an upload request has at most 1000 parts (nameless ones count), refused before any row is made | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 1286f70, 8514c9d |
| R31-7 | 31 | R31-7 a speaker comparison below 1 s or mean RMS 1e-4 shows no number (a 4-sample WAV scored 47) | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | e3e7c0f, 94ca8ea, 8514c9d |
| R31-8 | 31 | R31-8 the stylometry floor counts visible characters only (100 zero-width spaces were 「표본 100자」) | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | e3e7c0f, 8514c9d |
| R31-9 | 31 | R31-9 a bare LF ends an unquoted header value (filename=a.png<LF>X-Evil: 1 named a file with a line  | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | 1286f70, 8514c9d |
| R31 | 31 | R31 gate 0's blocking false-statement scope: verdicts, evidence identity, signed outputs, GUI; non-browser API refusal reasons are Low | — | 신규(스펙 외): 갭 목록 밖 항목 포함 | c15ed6e |

커밋 제목에 쓰이지 않은 ID:

- R10: 커밋 제목에 쓰이지 않음 — 라운드 2 문서가 없어 매핑 불가(커밋 메시지(라운드 2 문서 없음))
- R11: 커밋 제목에 쓰이지 않음 — 라운드 2 문서가 없어 매핑 불가(커밋 메시지(라운드 2 문서 없음))
- R12: 커밋 제목에 쓰이지 않음 — 라운드 2 문서가 없어 매핑 불가(커밋 메시지(라운드 2 문서 없음))
- R13: 커밋 제목에 쓰이지 않음 — 라운드 2 문서가 없어 매핑 불가(커밋 메시지(라운드 2 문서 없음))
- R14: 커밋 제목에 쓰이지 않음 — 라운드 2 문서가 없어 매핑 불가(커밋 메시지(라운드 2 문서 없음))
- R15: 커밋 제목에 쓰이지 않음 — 라운드 2 문서가 없어 매핑 불가(커밋 메시지(라운드 2 문서 없음))
- R17: 커밋 제목에 쓰이지 않음 — 라운드 2 문서가 없어 매핑 불가(커밋 메시지(라운드 2 문서 없음))

근거: `verify_round1/4/5/6/7/8/9/10/11.md`(라운드 2·3은 문서가 없어 해당 커밋 메시지), 스펙 WP 머리글의 갭 목록.

라운드 3의 N은 커밋 제목에 N1–N8만 쓰였다(N3-1…N3-8). 라운드 6의 N3(=V5-G10, 커밋 리워드)은 세션 소유자 작업이라 커밋 제목에 없다.

Z3 → G7 근거: R-OUT-6(진입점이 같은 입력을 같게 다룸) — CLI 진입점이 `Path()`로 끝 구분자를 지워, OS가 ENOTDIR로 거부하는 경로(`photo.png/`)를 다른 파일 경로처럼 분석했다.

P12: 라운드 8 검증 때 이 표의 해시 115개가 전부 히스토리 리라이트 이전 값이었다 — 이제 표는 현재 히스토리에서 제목으로 매칭해 재생성되고(`pre_rewrite_subject`에 리라이트 전 제목), CI가 `--check`로 최신 여부를 확인한다.

P14: Gaps를 제목 괄호에만 적은 커밋(예: `(Z1-Z5; Gaps: G7, 신규)`)은 규칙상 허용 — 스크립트가 본문의 `Gaps:` 줄을 먼저, 없으면 제목을 읽는다.

R17-11: 0ce6d1b의 제목은 R16-13(Ctrl-C → 정리 후 '중단됨' exit 130)에 G13(사진/비사진 게이트)을 인용했다 — 잘못된 인용이다. R16-13의 매핑은 G34(R-SYS-3: 종료·자원 정리)로 정정했다. commits[]의 `gaps`/`gaps_line`은 커밋 제목을 그대로 옮긴 값이라(히스토리는 고치지 않음) 0ce6d1b 행에는 G13이 남는다 — 기준은 ids[]의 매핑.

R18-14: ids[]의 갭 매핑을 커밋 제목·본문의 `Gaps:`에 맞췄다 — R13-2(+G34), R13-6(G12→G30), R15-2(G9 제거, 신규만), R15-3(G10→G1), R15-4(+G34), R15-6(G12→G34), R16-2(G10→G1), R16-3(G10 제거 — 프로필 revision 핀과 무관), R16-8(G13→G29), R16-9(+G9), R16-10(G30→G34), R16-12(G27→G26). `--check`는 이제 ids[]의 갭이 그 ID를 인용한 커밋의 Gaps에 없거나, 그 ID만 다룬 커밋이 인용한 갭이 ids[]에 없으면 실패한다(`gaps_note`로 이유를 적은 항목은 제외).

R31: c15ed6e의 제목 ID `R31`은 라운드 2의 R1–R16(R10–R15 미사용)과 다른 것으로, 27~31차 결과에 따른 게이트 0 거짓 문구 범위 결정(HANDOFF §8.1 (b), 사용자 결정 2026-10-19)이다 — 검증 지적 ID가 아니라 라운드 31의 결정 커밋을 가리킨다.

## 커밋별 `Gaps:` 줄

`Gaps 출처`: 본문의 `Gaps:` 줄(body) 또는 제목 괄호 안의 `Gaps:`(subject, P14 — 규칙상 허용).

| 커밋 | 라운드 | 제목의 ID | Gaps 줄 | Gaps 출처 | 제목 |
| --- | --- | --- | --- | --- | --- |
| caa5276 |  | — | 없음 | — | fix(cli): restore CLI by removing nonexistent generate_legal_report import |
| 409d342 |  | — | 없음 | — | refactor: remove fabricated metrics and simulated discovery features |
| 8395d47 |  | — | 없음 | — | security: harden local web/API servers against drive-by and rebinding |
| bd81807 |  | — | 없음 | — | refactor: remove dead modules and unreachable code |
| 6e06354 |  | — | 없음 | — | fix: correct provably wrong detection math (audio, multimodal, fusion, face, video) |
| 08f2cf4 |  | — | 없음 | — | fix: split-aware calibration, honest FPR fallback, excluded unanalyzed files |
| dceb8a0 |  | — | 없음 | — | fix: bound resource use, validate ffmpeg argv, de-duplicate PixelAnalysis |
| be817d7 |  | — | 없음 | — | fix(scripts): substitute @OUT@ placeholder correctly in smoke checks |
| 356f2db |  | — | 없음 | — | feat: real frequency forensics expert and CHROM rPPG screening (Phase 2) |
| 07cc64b |  | — | 없음 | — | feat: SBI training path, PRNU sensor-fingerprint screening, voice regularity metrics |
| 62308a2 |  | — | 없음 | — | feat: real C2PA manifest validation via official SDK; honest byte-scan demotion |
| a152b02 |  | — | 없음 | — | fix: measured evidence integrity, checksum naming, PDF notice, EER metric |
| 94bc788 |  | — | 없음 | — | feat(android): ONNX Runtime inference path; chore: verified-link registry |
| 20a20b4 |  | — | 없음 | — | ci: add workflow for tests, CLI smoke, Android unit tests, link checks |
| 3b6d4f5 |  | — | 없음 | — | fix: keep stdlib-only CI green (guarded sbi import, lazy numpy, FloatBuffer read) |
| d944693 |  | — | 없음 | — | fix: pass explicit input size in preprocessing tests; guard numpy in CI |
| 6aee0ef |  | — | 없음 | — | fix(experiments): export TorchScript checkpoints through the legacy ONNX exporter |
| f91a3a6 |  | — | 없음 | — | feat: Phase A hygiene — word-boundary tool matching, forensic loader issues, json-out parents, docs coverage, extras CI job |
| 27bfd4e |  | — | 없음 | — | feat(experiments): best-checkpoint selection, early stopping, LR schedule |
| c3c484f |  | — | 없음 | — | fix: repair 404 ComfyUI docs link, retry transient 5xx in link checker, guard onnx export dependency |
| a5b8d1a |  | — | 없음 | — | fix: restore lost C2PA fixture and make it reproducible |
| 65e176d |  | — | 없음 | — | feat: close the robustness-evaluation loop with a variant generator |
| e4158bd |  | — | 없음 | — | docs: roadmap + preserved CI workflow patch (#8) |
| 83bdf11 |  | — | 없음 | — | chore: dev extra for contributor onboarding; roadmap status update |
| a0ecb92 |  | — | 없음 | — | fix: recognize benchmark 0_real/1_fake label folders; first real-data eval |
| 2e9cd3c |  | — | 없음 | — | docs: record Synthbuster first-pass results in the roadmap |
| 0e502a9 |  | — | 없음 | — | fix: multi-digit class-prefix label folders; full scorecard and rewritten roadmap |
| c2182f1 |  | — | 없음 | — | feat: AIDE pretrained-detector adapter, verified bit-exact, AUROC 1.0 on measured sets |
| e468866 |  | — | 없음 | — | feat: full Synthbuster sweep (AUROC 0.951) + AIDE ONNX parity; roadmap rewritten against the four-modality audit |
| 44279b4 |  | — | 없음 | — | chore: add MIT license |
| e582a70 |  | — | 없음 | — | ci: enable c2pa sdk tests and bump actions to v5/v6 |
| 703a761 |  | — | 없음 | — | fix: add timeout to video extraction subprocess |
| 39d9d66 |  | — | 없음 | — | docs: note consolidation plan for duplicate analysis paths |
| 074cb06 |  | — | 없음 | — | feat: aide runtime profile, fetch script, and default-engine wiring |
| 91fe404 |  | — | 없음 | — | feat: label face landmarks by source, add optional MediaPipe FaceMesh path |
| f04ec13 |  | — | 없음 | — | refactor: rename pseudo-perplexity to bigram_entropy in text_advanced |
| 0b79e46 |  | — | 없음 | — | refactor: rename ml_classifier to rule_classifier |
| 7550cf9 |  | — | 없음 | — | feat: multi-ROI rPPG with FakeCatcher-style phase coherence |
| 02f147d |  | — | 없음 | — | feat: real A/V sync check via envelope cross-correlation in multimodal |
| 06d4c78 |  | — | 없음 | — | test: add visibility-only coverage config and dev-extra entry |
| 2a9f3a0 |  | — | 없음 | — | test: synthetic benchmark fixtures + scan pipeline e2e smoke harness |
| e69ab84 |  | — | 없음 | — | test: ONNX contract unit tests + instrumented graceful-null skeleton |
| fd88785 |  | — | 없음 | — | feat: verify MediaPipe FaceMesh path, pin extra to legacy-API builds |
| 94dcf8a |  | — | 없음 | — | feat: isotonic score-calibration layer with honest insufficient-data fallback |
| 883041a |  | — | 없음 | — | feat: examiner feedback loop with advisory fusion-weight suggestions |
| 91bce6d |  | — | 없음 | — | refactor: consolidate duplicate analysis paths per consolidation-notes |
| 264c056 |  | — | 없음 | — | fix: disclose pixel ensemble is below-chance without external model |
| 5736785 |  | — | 없음 | — | feat: HMAC-SHA256 report signing with honest unsigned fallback |
| 3a1c96a |  | — | 없음 | — | feat: model-zoo profiles — clip-linear/torchvision runtimes, placeholders, multi-profile fusion |
| 86b5903 |  | — | 없음 | — | feat: checksummed benchmark fetcher + in-tree fixture E2E as CI step |
| b20569f |  | — | 없음 | — | chore: gate coverage at fail_under=65 (measured floor ~67%) |
| af02193 |  | — | 없음 | — | docs: screening-service contract + loopback defaults pinned in tests |
| 3da983e |  | — | 없음 | — | fix(ci): guard numpy-dependent face tests and normalize weights in integer units |
| 479a737 |  | — | 없음 | — | test: make face-landmark tests numpy-free and pin exact weight normalization |
| b5f33f8 |  | — | 없음 | — | fix(models): unwrap BaseModelOutputWithPooling for clip-linear on transformers 5.x |
| 7af3087 |  | — | 없음 | — | fix(models): unwrap nested 'model'/'net' state-dict wrappers for torchvision runtime |
| 9cba68f |  | — | 없음 | — | chore: gitignore the bundled Android model asset |
| c91742b |  | — | 없음 | — | feat(models): add AASIST audio runtime with modality-aware profile dispatch |
| e67fd3d |  | — | 없음 | — | docs: record CIFAKE cross-domain bound (3 runtimes, AUROC ~0.5 at 32x32) + remaining-items roadmap |
| 9aa6977 |  | — | 없음 | — | feat(models): hf-text-classifier runtime + OpenAI detector profile (text modality) |
| f387cc0 |  | — | 없음 | — | docs: mark text detector done in remaining-items doc |
| 4a9a6a0 |  | — | 없음 | — | feat(web): honesty layer in GUI + default neural profiles + working upload |
| a433862 |  | — | 없음 | — | feat(web): token auth for LAN binds + elapsed progress + windowed results |
| 9331d18 |  | — | 없음 | — | Let the web GUI pick folders directly and cover keyboard/screen-reader use |
| f98eec4 |  | — | 없음 | — | Add examiner ground-truth feedback loop to the web GUI |
| ce6effa |  | — | 없음 | — | Close the web analysis->documentation loop and record model licenses |
| 45e6866 |  | — | 없음 | — | Complete v0.1.0 plan: Windows fixes, CSRF gate, video-frames runtime, FaceLandmarker, modern text profile |
| d200d04 |  | — | 없음 | — | Post-release: async web scans, verified model assets, doc accuracy |
| bf3e59b |  | — | 없음 | — | Add full-archive Synthbuster benchmark and evaluation script |
| f8c2bbc |  | — | 없음 | — | Emulator-validated Android ONNX: swap int8 asset for working fp32 export |
| 21e1492 |  | — | 없음 | — | Record measured text-detection boundaries |
| a01ad43 |  | — | 없음 | — | Extend text-detection eval with Antigravity-style probe |
| c3d8177 |  | — | 없음 | — | Flag English-calibrated lexical thresholds on Hangul text |
| aaed4ed |  | — | 없음 | — | Add causal-LM perplexity text detector + Korean-aware layer + labeled seed corpus |
| 68dfdcb |  | — | 없음 | — | Add binoculars two-LM runtime + expand labeled text corpus to n=30 |
| 091e0de |  | — | 없음 | — | Add frontier-LLM fingerprint signals + LM-level burstiness |
| a9401d6 |  | — | 없음 | — | Add unified /api/check endpoint + GUI quick-check card |
| 62d2103 |  | — | 없음 | — | Add all-modality detection coverage plan |
| f814960 |  | V1, V2 | 없음 | — | Wire video into scan path + BMP/GIF/TIFF image support (V1-1, V1-2, V5-1) |
| 4823f76 |  | V1, V2, V3 | 없음 | — | Add document pipeline + video audio-track analysis (V1-3, V2) |
| 409ada7 |  | V3 | 없음 | — | Wire isolated detectors into scan via --deep-signals opt-in (V3) |
| 20b1845 |  | — | 없음 | — | Add V4 audio physical signals: pause, noise floor, harmonic stability |
| 14ea1fb |  | — | 없음 | — | Add JPEG double-compression + ELA forensics to pixel expert (V5-2, V5-3) |
| 67175e1 |  | — | 없음 | — | Add V6 comparison/sync probes: speaker, stylometry, lipsync, KGW watermark |
| 99becb7 |  | — | 없음 | — | Update --deep-signals help to include lip-sync layer |
| 9a6e28c |  | — | 없음 | — | Expose comparison/watermark probes + copy-move detection + doc metadata |
| 5567d07 |  | — | 없음 | — | Add capability audit plan + gap research with implementation paths |
| f1f1a2e |  | — | 없음 | — | Add HWP/HWPX text extraction via syhwp (gap-research item B-7) |
| 887944f |  | — | 없음 | — | Add rotation/scale-robust copy-move via keypoint transform voting (B-4) |
| 19a3965 |  | — | 없음 | — | Close gap-research items: Korean gate, ECAPA speaker, C2PA, tech-doc gate, SyncNet path |
| 18fdb4f |  | — | 없음 | — | Add lighting-direction consistency check and SynthID watermark path |
| 42948e9 |  | — | 없음 | — | Cap short-text scores at 49 with disclosure exemption |
| 25e3876 |  | — | 없음 | — | Exclude English-only members on Korean text; tune short-text cap |
| e42cb57 |  | — | 없음 | — | Downweight qwen-ppl after measured anchor calibration failure |
| a8285f7 |  | — | 없음 | — | Effectively disable binoculars member after measured inversion |
| df7d021 |  | — | 없음 | — | Mark PPL-based text members as English-anchored |
| d9856ef |  | — | 없음 | — | Record final calibrated eval: ko FPR 0, conservative posture |
| 918f539 |  | — | 없음 | — | Fix document/member mismatch and lipsync field semantics |
| 40c6db3 |  | — | 없음 | — | Document calibrated member weights and SyncNet asset license |
| bd657ea |  | — | 없음 | — | Keep SyncNet pipeline noise off stdout for JSON consumers |
| 5fa35f2 |  | — | 없음 | — | Redesign web GUI as iOS-style card viewer with compare and heatmap |
| f8adc01 |  | — | 없음 | — | Expand archives into per-member scan rows (zip/tar, optional 7z/rar) |
| 80864a9 |  | — | 없음 | — | Add onboarding guide and score-interpretation help to GUI |
| 0a109ec |  | — | 없음 | — | Add media preview and signal-strength grouping to result viewer |
| a139ddc |  | — | 없음 | — | Fix CI failures and harden tests against missing optional deps |
| 84a4eb2 |  | — | 없음 | — | Fix evaluation metrics and add shared metrics module |
| 36f53ad |  | — | 없음 | — | Add stabilization and quality improvement roadmap |
| 166b0b8 |  | — | 없음 | — | Wire face-manipulation ViT detector; reject failed FF++ checkpoint |
| 82c1e80 |  | — | 없음 | — | Reject face ViT on labeled eval; add face-crop machinery and eval harness |
| 76ff1f5 |  | — | 없음 | — | Wire wav2vec audio member and Swin image member after measured screening |
| 876d318 |  | — | 없음 | — | Add coverage roadmap: measurement gaps -> faceswap -> forensic layers |
| 3706de2 |  | — | 없음 | — | Roadmap: measured coverage expansion — SBI faceswap detector, ELA, video flicker, robustness evals |
| dc0e0b5 |  | — | 없음 | — | Fix measured failure modes: SBI cross-domain FPR 0.67->0.07, downweight flat members |
| 236d8ce |  | — | 없음 | — | Add doctor CLI, JPEG-QF degraded weighting, SSE check stream, Android SBI model |
| 1723fde |  | — | 없음 | — | Korean corpus eval: gate verified, KoELECTRA classifier rejected on cross-gen |
| c7882a0 |  | — | 없음 | — | Add next-phase improvement plan grounded in measured gaps |
| 46f4784 |  | — | 없음 | — | Phase A: persistent eval harness + corpus build script + derived weights |
| 7784852 |  | — | 없음 | — | Phase A done: eval harness verified, corpus preserved, FPR-penalized weights |
| 855d1f7 |  | — | 없음 | — | Promote SBI v2 checkpoint with score_bias recalibration |
| d549ccb |  | — | 없음 | — | Record Phase B measurements and expanded image baseline |
| 3939915 |  | — | 없음 | — | Add SSE batch scan endpoint with per-file progress and cancellation |
| 277a753 |  | — | 없음 | — | Add SD-Turbo corpus generator for the B2 self-training retry |
| f58ae55 |  | — | 없음 | — | Pin eval-harness regressions: FPR-penalized weights, unavailable != zero |
| 244747a |  | — | 없음 | — | B2 retry: wire SD-Turbo-trained image detector as advisory member |
| f207742 |  | — | 없음 | — | B-sweep: add measured ONNX/video model candidates, reject 4 of 6 |
| 3b531f3 |  | — | 없음 | — | V-phase: verify SyncNet, add face-track temporal probe, measure screen-capture evasion |
| 0634e98 |  | — | 없음 | — | chore: vendor lazy-contracts, add charter, align license metadata to MIT |
| f07e28c |  | — | 없음 | — | fix(security): harden checkpoint loading, C2PA status, and data splits |
| ce3f389 |  | — | 없음 | — | UI trust pass: canonical band counts, result filter, scan cancel |
| 2236ed4 |  | — | 없음 | — | Harden file-serving roots, add lint+dep-audit gates (LUMOS findings applied) |
| d6bc422 |  | — | 없음 | — | GUI polish pass: refined visual system, review marks, keyboard nav |
| 574dd79 |  | — | 없음 | — | Fix image reads failing on non-ASCII paths |
| 7c31fa9 |  | — | 없음 | — | Bound model caches, unify token headers, harden scan resilience |
| e485d7a |  | — | 없음 | — | Persist examiner review marks server-side |
| 6275ccf |  | — | 없음 | — | feat(core): harden face detection, doctor diagnostics, and LRU neural cache |
| 04e4c85 |  | — | 없음 | — | feat(reviews): add thread-safe review store and REST persistence endpoints |
| 86011cb |  | — | 없음 | — | feat(gui): introduce forensic split comparison studio and accessible token dialog |
| 39c3a0b |  | — | 없음 | — | feat(text): add Korean AI text detector profile and stylometry heuristics |
| e4ba23b |  | — | 없음 | — | feat(reports): add court-admissible forensic PDF export with ECFS exhibit stamp |
| 5652006 |  | — | 없음 | — | feat(forensics): add localized faceswap boundary seam and blending artifact detector |
| 8cecd3f |  | — | 없음 | — | feat(legal): add ECFS electronic litigation evidence statement generator and PyMuPDF PDF builder |
| 270c260 |  | — | 없음 | — | feat(offline): add air-gapped forensic lab model weight bundler and integrity verifier |
| 9d1f896 |  | — | 없음 | — | feat(cli,gui): wire faceswap-seam, evidence-statement, vendor-weights into CLI and web GUI |
| da24f11 |  | — | 없음 | — | Merge origin/main: unify examiner review persistence behind ReviewStore |
| adf4b43 |  | — | 없음 | — | fix: render evidence PDF purpose column, honest unknown verdicts, air-gap verify semantics |
| e31ed54 |  | — | 없음 | — | feat(calibration): provenance-backed layer threshold profiles |
| f49e0cb |  | — | 없음 | — | refactor(webapp): split monolithic gui.html into gui.css + gui.js under CSP |
| 2574630 |  | — | 없음 | — | feat(offline): sha256-verified weight fetch, coverage count, degraded-mode banner |
| 98c198c |  | — | 없음 | — | refactor(core,cli): split monoliths into focused modules; wire thresholds, coverage, mypy CI |
| 7bfe60a |  | — | 없음 | — | docs(calibration): corpus acquisition plan + seam-threshold measurement pipeline |
| 88a16f7 |  | — | 없음 | — | refactor(model_adapter): extract runtime executors into model_runtimes.py |
| eb12b09 |  | — | 없음 | — | refactor(gui): migrate inline styles to CSS classes; drop style-src 'unsafe-inline' |
| 3198cf7 |  | — | 없음 | — | refactor(typing): extend scoped mypy gate to 12 modules |
| 8d61e29 |  | — | 없음 | — | fix(ci): guard optional-dependency tests; skip editable package in dep audit |
| 53321d4 |  | — | 없음 | — | refactor(core,pixel): extract metadata, text-heuristic, and raster layers |
| 6b36f0b |  | — | 없음 | — | refactor(cli): split argument parser construction into cli_parser.py |
| d52ee04 |  | — | 없음 | — | refactor(webapp): move /api/* payload handlers into webapp_api.py |
| e7b4d97 |  | — | 없음 | — | feat(calibration): surface threshold provenance in scan output and reports |
| e7004ee |  | — | 없음 | — | ci: hard-gate dependency audit; extend mypy scope to 16 modules |
| 94066a1 |  | — | 없음 | — | test+ci: cv2 skip guards, contract drift gate, coverage floor, mypy 61 modules |
| d74b180 |  | — | 없음 | — | refactor(models): ship model profiles as package data |
| ae1a496 |  | — | 없음 | — | feat(offline): honest vendor-weights lifecycle + pinned checkpoint loading |
| de1b60d |  | — | 없음 | — | fix(forensics): court artifacts never overstate integrity or certainty |
| 7e7e925 |  | — | 없음 | — | fix(robustness): per-file failures can't kill a batch; archives fail-closed |
| cc9afbd |  | — | 없음 | — | feat(web): provenance everywhere + honest archive/upload errors + GUI UX |
| 8c84871 |  | — | 없음 | — | feat(cli): provenance in scan/CSV output + models-dir wiring + doctor fixes |
| cce0de2 |  | — | 없음 | — | fix(face): vendored Haar cascade + crash-safe detection on OpenCV 5.x |
| 52e2faa |  | — | 없음 | — | feat(calibration): AUROC quality gate + first measured thresholds.json |
| 8ff3353 |  | — | 없음 | — | fix(model_adapter): load only *-runtime.json from profile directories |
| 3567144 |  | — | 없음 | — | fix(scan): summary parity — container and failed rows never count as analyzed |
| 53518ae |  | — | 없음 | — | fix(evidence): measure every table cell for row height + 60-item stress test |
| 1dd9b6f |  | — | 없음 | — | test(gui): headless Chrome smoke harness + NN badge requires live weights |
| dad9730 |  | — | 없음 | — | feat(calibration): first MEASURED thresholds — real faceswap corpus via inswapper |
| 01faad0 | wp | WP-A | Gaps: G5, G6, G12, G24 | body | feat(contract): result contract v2 — three verdicts, classified evidence, coverage (WP-A) |
| ad76dfd | wp | WP-B | Gaps: G1 | body | fix(fail-closed): crashed checks are "failed", never clean; lint gate (WP-B) |
| 7f4ee2a | wp | WP-E | Gaps: G4 | body | fix(text): keywords are lexical evidence only, never points (WP-E) |
| 1cb8da8 | wp | WP-C | Gaps: G2, G9, G10, G33 | body | fix(models): pinned, gated model zoo; rejected profiles removed (WP-C) |
| 0200815 | wp | WP-G | Gaps: G11, G30, G31, G32 | body | fix(integrity): content-keyed cache, full-body signatures, operator-only read roots (WP-G) |
| c0d5651 | wp | WP-I | Gaps: G26, G27, G28 | body | feat(measurement): bootstrap CIs, corpus manifests, measurement gate (WP-I: G26, G27, G28) |
| df16423 | wp | WP-I | Gaps: G26, G27, G28 | body | docs: mark unverified performance numbers (WP-I: G26, G27) |
| 0024418 | wp | WP-F | Gaps: G7, G8 | body | feat(entry): one analysis entry point for CLI, GUI and API (WP-F: G7, G8) |
| 40c37bd | wp | WP-H | Gaps: G29, G34 | body | fix(ops): doctor readiness columns, API job limits, archive budget (WP-H: G29, G34) |
| 6d67940 | wp | WP-D | Gaps: G3, G13, G17 | body | WP-D: photo/non-photo gate, pixel fusion without floors (G3, G13, G17) |
| a1c9068 | wp | — | Gaps: G31 | body | fix(heatmaps): never write into the evidence folder (G31, R-IN-1; found by QA-IN-1) |
| 42818e6 | wp | — | Gaps: G1 | body | fix(avatar): accept Path — the avatar check crashed on every video (G1) |
| 00a4886 | wp | — | Gaps: G31 | body | fix(api): confine every file_path/directory endpoint to the read roots (G31) |
| 7ec9844 | wp | — | Gaps: G10 | body | fix(hub): pin every remaining hub download to an explicit commit (G10) |
| 782f13b | wp | — | Gaps: G27 | body | fix(scripts): fetch_* write to default_models_dir(), not repo-root models/ (G27) |
| a60b43f | wp | — | Gaps: G30 | body | fix(signing): sign the evidence statement (증거설명서) like the other reports (G30) |
| adcbff3 | wp | — | Gaps: G28 | body | fix(models): mark the SBI score_bias as fitted in-sample (G28) |
| 09e546e | wp | WP-J | Gaps: G1, G12, G26, G28; 신규(스펙 외) | body | feat(qa): phase-0 QA harness, traceability table and conformance gate (WP-J: G26, G28, G1, G12) |
| d4deb58 | wp | WP-J | Gaps: 신규(스펙 외) | body | docs(qa): phase-0 conformance table from scripts/qa_phase0.py (WP-J) |
| eba5d27 | 1 | D5 | Gaps: G30 | body | fix(evidence-statement): record the scan's sha256, never re-hash against the cwd (D5) |
| 2b0a4f7 | 1 | D7, D16 | Gaps: G1, G5, G6, G28 | body | feat(metadata): parse JPEG/PNG EXIF and XMP; failed reads are failures, not absence (D7, D16) |
| b0ffd0e | 1 | D8 | Gaps: G1 | body | fix(c2pa): reader exceptions are "unavailable" and a failed check, never "absent" (D8) |
| 34801a8 | 1 | D9, D10 | Gaps: G12, G30, G34 | body | fix(archives,scan): record every refused archive member and every symlink (D9, D10) |
| 8292380 | 1 | D11 | Gaps: G4, G24 | body | fix(text): lexical source hints are "참고" with confidence unknown (D11) |
| 3e8dcc5 | 1 | D12 | Gaps: G17 | body | fix(pixel): raw_score / reference_confidence "참고" instead of score / confidence (D12) |
| 15af9b7 | 1 | D1, D2, D3, D4, D14 | Gaps: G5, G6, G7, G13, G17, G24, G30 | body | fix(cli,api): standalone commands speak the three-verdict / layer-diagnostic contract; legal-report from scan; verify-report (D1, D2, D3, D4, D14) |
| 12f9c90 | 1 | D13 | Gaps: G17 | body | fix(deep-layers): uncalibrated deep signals are reference_signals, not evidence (D13) |
| 9742076 | 1 | D16 | Gaps: G1, G5, G28 | body | fix(ui,api): verdict-only summary keys, 결론순 sort, Korean errors, /api/stats header, api-serve help, logged scan-job failure (D16) |
| 877dbba | 1 | D15, D6 | Gaps: G7, G12, G13 | body | feat(face): weight-free face detector; QA-OUT-3 runs the real detector; GIF face check skipped (D15, D6/QA-OUT-3) |
| 8be2b56 | 1 | D6 | Gaps: G7, G12, G13 | body | test(qa): QA-ADV-2 on 50 screenshots of three kinds; QA-IN-1 scans all 43 formats (D6) |
| 14aec3c | 1 | D6, D16 | Gaps: G1, G5, G7, G12, G13, G28 | body | test(qa): QA-SYS-1 verdict names the integrity failure; QA-IN-5 peak disk + specific reasons; QA-OUT-5 labeled structural; harness needs fastapi and a clean tree (D6, D16) |
| 6ed3e3a | 1 | D16 | Gaps: G1, G5, G28 | body | fix(documents,gate): keep the extractor's exception class; measured_on must name a real matching manifest (D16) |
| 63fa8c8 | 1 | D13, D1 | Gaps: G5, G6, G7, G17, G24 | body | test(core): D13 fixture uses renamed reference_band/reference_note after D1 merge |
| e957307 | 1 | D5 | Gaps: G30 | body | fix(cli): evidence statements resolve sha256-less rows against the scan folder (D5) |
| 8723fd5 | 1 | D9 | Gaps: G12, G30, G34 | body | fix(web): uploaded archives record refused members like the folder scan (D9) |
| 7d1c689 | 1 | D1, D16 | Gaps: G1, G5, G6, G7, G24, G28 | body | fix(layers): rename the last band/verdict fields to reference_band/reference_note; no-legacy-band regression test (D1, D16) |
| ae403d4 | 1 | WP-J | Gaps: 신규(스펙 외) | body | docs(qa): regenerate CONFORMANCE.md on 94d0bcf (verification round 1 leftovers, WP-J) |
| f357390 | 2 | R1, R5 | Gaps: G7 | body | fix(api): /api/scan/stream and archive /api/check run the folder scan; container rows counted by verdict (R1, R5) |
| b6eea4e | 2 | R2 | Gaps: G12 | body | fix(video): the audio-track analysis is its own av_audio coverage entry (R2) |
| 83e86fb | 2 | R3 | Gaps: G24 | body | fix(documents): source guess from creator/application metadata is reference-only (R3) |
| 534d894 | 2 | R6 | Gaps: G29 | body | fix(cli): PDF evidence statement without pymupdf is a Korean exit-2 message, not a traceback (R6) |
| ac1582a | 2 | R7 | Gaps: G12 | body | fix(face): no detected face reads "얼굴 미검출", no analysis "해당 없음" — never "none" (R7) |
| cd0f62e | 2 | R8 | Gaps: G1 | body | fix(c2pa): an SDK failure reads "C2PA 판독 불가(<reason>)" in the provenance diagnostic, never "C2PA 없음" (R8) |
| 77afe5c | 2 | R9 | Gaps: G6 | body | fix(evidence): camera-EXIF consistency only on an image that decoded (R9) |
| 358029a | 2 | R16 | Gaps: G5 | body | fix(ui): no "scores are review priority" wording; CSV 보정점수/결론 columns; --include-low means authenticity rows (R16) |
| 6bf9a93 | 2 | R4 | Gaps: G26; 신규(스펙 외) | body | fix(i18n): Korean for user-facing English added in phase 0; profiles lose unverified figures (R4) |
| 1496970 | 2 | R1, R9, R16, WP-J | Gaps: G5, G6, G7; 신규(스펙 외) | body | docs(qa): regenerate CONFORMANCE.md on 9be8cf2 (verification round 2: R1-R9, R16, WP-J) |
| 802a8d2 | 3 | N3-1 | Gaps: 신규(스펙 외) | body | fix(paths): coverage reasons and errors never carry a file-system path (N1) |
| a6f3c25 | 3 | N3-2 | Gaps: G30 | body | fix(statement): never follow a symlink when hashing; status line for rows without a verdict (N2) |
| 37ad939 | 3 | N3-3, N3-4 | Gaps: 신규(스펙 외) | body | fix(contract,pdf): scan schema accepts null results under draft 2020-12; PyMuPDF import never prints to stdout (N3, N4) |
| a25b0a8 | 3 | N3-5, N3-6 | Gaps: G6, G12 | body | fix(evidence): archive container rows carry their roll-up item; C2PA hash mismatch is "C2PA 무결성 불일치" (N5, N6) |
| 2e053a5 | 3 | N3-7, N3-8, R16 | Gaps: G5, G12, G30; 신규(스펙 외) | body | fix(cli,api): Korean statuses, help and 401s; empty key file, skipped subfolders, cancel counts, logs; no "우선순위" banners (N7, N8, R16) |
| c923521 | 3 | R4 | Gaps: G26; 신규(스펙 외) | body | fix(i18n,tests): Korean model display names; strict Korean-output test (R4 leftover) |
| f9d80bd | 3 | — | Gaps: G7, G8 | body | test(qa): QA-OUT-4 compares every leg raw on the hostile folder (QA-OUT-4; Gaps: G7, G8) |
| 93fbaa3 | 3 | WP-J | Gaps: 신규(스펙 외) | body | docs(qa): CONFORMANCE.md regenerated on a785d7a (WP-J) |
| dc2ddfa | 4 | S9 | Gaps: 신규(스펙 외) | body | test: S9 drop the unused `time` and `io` test imports |
| 0d8019f | 4 | W1 | Gaps: G32 | body | fix(scan): W1 one global sort of the walk by POSIX relative path (G32) |
| fd6f100 | 4 | B6 | Gaps: 신규(스펙 외) | body | fix(i18n): B6 c2pa SDK messages in Korean, untranslated library text never shown |
| 3debaac | 4 | W2, WP-J | Gaps: 신규(스펙 외) | body | test(qa): W2 tests/qa is exactly the four WP-J area files; manual QA marked 미실시 |
| 89c2cdc | 4 | B2, B3, B4, S1, S2, S6 | Gaps: G28, G30; 신규(스펙 외) | body | fix(i18n): B2 B3 B4 S1 S2 S6 Korean report labels, full archive-member names |
| 24437b1 | 4 | B5 | Gaps: 신규(스펙 외) | body | fix(gui): B5 Korean source-guess confidence, archive_member label, drift test |
| 7015bf0 | 4 | B1 | Gaps: G7 | body | fix(analysis): B1 single-file entry points expand archives exactly like scan (G7) |
| 0a3e142 | 4 | S3 | Gaps: 신규(스펙 외) | body | fix(reports): S3 --redact-paths also hides the install path (models[].profile) |
| 746a4d2 | 4 | B7 | Gaps: G29; 신규(스펙 외) | body | fix(doctor): B7 PyMuPDF via pdf_backend, JSON-clean stdout, Korean table |
| 4334d26 | 4 | S4 | Gaps: G29 | body | fix(cli): S4 scan names why a folder cannot be scanned; exit-code table |
| 0f754f2 | 4 | S5 | Gaps: 신규(스펙 외) | body | fix(gui): S5 CSP allows blob: media so wav/mp4 previews play |
| fec9fb6 | 4 | S7 | Gaps: G28 | body | fix(audio): S7 model limitations listed once; gated-off profiles add none |
| a3bf814 | 4 | B8 | Gaps: G29; 신규(스펙 외) | body | fix(cli): B8 Korean --pdf-out (or exit 2), every --help string in Korean |
| 20252d2 | 4 | B8 | Gaps: G29; 신규(스펙 외) | body | test(qa): QA-SYS-6 web PDF without pymupdf may be a Korean error (B8 handoff) |
| 901a5c8 | 4 | S8 | Gaps: 신규(스펙 외) | body | test(i18n): S8 sentence-level English detector over every rendered output |
| 2462701 | 4 | B8 | Gaps: G29; 신규(스펙 외) | body | fix(reports): B8 web/API PDF without pymupdf is a Korean 501 error, Latin-1 fallback deleted |
| b6fc45f | 4 | B8 | Gaps: G29; 신규(스펙 외) | body | chore(mypy): type-check the test files added with the B8 round-4 merges |
| 574483a | 4 | R4 | Gaps: G26; 신규(스펙 외) | body | fix(signing): unsigned note is Korean only (R4 leftover) |
| fe597d4 | 4 | WP-J | Gaps: 신규(스펙 외) | body | docs(qa): CONFORMANCE.md regenerated on 61f90d7 (WP-J) |
| 417d478 | 5 | V5-G12 | Gaps: 신규(스펙 외) | body | chore(lint): V5-G12 ruff F401 enabled, every unused import in the package removed |
| 5729d48 | 5 | V5-G15, B8 | Gaps: G29; 신규(스펙 외) | body | test(pdf): V5-G15 B8 ID comment on every skipUnless(pymupdf) case in test_forensic_pdf |
| 5e2c6b5 | 5 | V5-G1 | Gaps: 신규(스펙 외) | body | fix(cli): V5-G1 standalone text outputs print Korean labels, never JSON codes |
| 0f38230 | 5 | V5-G3, V5-G4 | Gaps: G9; 신규(스펙 외) | body | fix(vendor-weights, c2pa): V5-G3 Korean weights table with consistent counts; V5-G4 C2PA state in Korean |
| 3e29c43 | 5 | V5-G5, V5-G6 | Gaps: G7; 신규(스펙 외) | body | fix(cli, api): V5-G5 missing file/folder is a Korean usage error; V5-G6 a named symlink is the scan's skipped row |
| 1a5ffaf | 5 | V5-G7 | Gaps: 신규(스펙 외) | body | fix(api): V5-G7 /api/report request errors in Korean |
| ad5b496 | 5 | V5-G8 | Gaps: G5 | body | fix(decision): V5-G8 rule 4 uses the loaded profiles' thresholds; no 0.5 default |
| 073a992 | 5 | V5-G14 | Gaps: 신규(스펙 외) | body | fix(decoders): V5-G14 ffmpeg and native decoder chatter stay off the console |
| b630e34 | 5 | V5-G2, V5-G16 | Gaps: G30; 신규(스펙 외) | body | fix(pdf): V5-G2 measured PDF layout — no text off the page, no overlap, full hashes (V5-G16 PDF hash) |
| fb336ca | 5 | V5-G16 | Gaps: G30; 신규(스펙 외) | body | fix(labels): V5-G16 analyst "시스템(자동)", Korean GUI review options, CSV label columns |
| bfddf75 | 5 | V5-G9, S8 | Gaps: 신규(스펙 외) | body | fix(korean): V5-G9 stronger English detector (dictionary rule) and S8 coverage of every output |
| d01addb | 5 | V5-G13 | Gaps: 신규(스펙 외) | body | fix(scripts): V5-G13 every scripts/*.py answers --help in Korean |
| 5297d58 | 5 | V5-G11 | Gaps: 신규(스펙 외) | body | test(qa): V5-G11 every tests/qa docstring starts with its QA ID; meta-test enforces it |
| 811fc5d | 5 | V5-G9 | Gaps: 신규(스펙 외) | body | fix(korean): V5-G9 follow-up — an argparse choice set is an identifier, words in braces are not |
| 2038358 | 5 | WP-J | Gaps: 신규(스펙 외) | body | docs(qa): CONFORMANCE.md regenerated on f7b9cf7 (WP-J) |
| 07dae02 | 6 | N6-1, V5-G8 | Gaps: G5 | body | fix(decision): N1 (=V5-G8) calibrated model direction follows the profile threshold, not 0.5 |
| d3c68ee | 6 | N6-2, N6-9, N6-10, N6-11 | Gaps: G7, G30, G31; 신규(스펙 외) | body | fix(report): N2 N9 N10 N11 /api/report — no symlink hashing, member digests, body format, 400 + item contract |
| e8ce32d | 6 | N6-5 | Gaps: 신규(스펙 외) | body | fix(servers): N5 Korean JSON for framework HTTP errors (404/405/422/500/501) on both servers |
| b1c94dc | 6 | N6-15 | Gaps: G7 | body | refactor(api): N15 scan_folder/scan_file return (summary, items); ScanRun carries thresholds |
| 70701c0 | 6 | N6-4, N6-7 | Gaps: 신규(스펙 외) | body | fix(cli): N4 N7 every subcommand checks its input paths — Korean "오류: …", exit 2 |
| ba3d40a | 6 | N6-6 | Gaps: G11 | body | fix(cache): N6 cache key carries the extension; restored rows get their own path text |
| 3d7afd9 | 6 | N6-8, N6-13 | Gaps: 신규(스펙 외) | body | fix(korean): N8 N13 Korean row-kind labels; no identifiers in user-facing reasons |
| 1a552af | 6 | N6-14 | Gaps: G34 | body | fix(archives): N14 nested archive depth 2 -> 8 (inside the 50-nested budget) |
| c7eff10 | 6 | N6-12 | Gaps: 신규(스펙 외) | body | fix(korean): N12 English detector — glued Korean particles, in-word hyphens, code-shaped sentences |
| c5d05fb | 6 | N6-17 | Gaps: G28; 신규(스펙 외) | body | fix(reports): N17 no built-in law firm; libsndfile decode wording; in-sample caveat on its own line |
| 1ba2941 | 6 | N6-16 | Gaps: 신규(스펙 외) | body | test(qa): N16 every tests/qa docstring's first line is "<QA-ID>: <criterion verbatim>" |
| e0358c4 | 6 | WP-J | Gaps: 신규(스펙 외) | body | docs(qa): CONFORMANCE.md regenerated on e368828 (WP-J) |
| 67c953b | 7 | Y12 | Gaps: 신규(스펙 외) | subject | test(cli): Y12 input-path matrix runs in process — 30 s -> 3 s (Y12; Gaps: 신규) |
| b0ac672 | 7 | X3 | Gaps: G32 | subject | fix(scan): X3 allowed symlinks never vanish — broken/circular link rows, linked folders followed (X3; Gaps: G32) |
| 2ccd85c | 7 | X1 | Gaps: G7, G12 | subject | fix(reports): X1 "기록되지 않은 파일" section in every output; evidence-statement scans with scan's options (X1; Gaps: G7, G12) |
| 757c4bf | 7 | X2 | Gaps: G30, G31 | subject | fix(report): X2 /api/report re-analyzes every posted row on the server and signs only server results (X2; Gaps: G30, G31) |
| 70e8188 | 7 | X4 | Gaps: G34 | subject | fix(servers): X4 no /api/* error is a 200 — 400/404/500 + Korean on both servers; status table documented (X4; Gaps: G34) |
| 79df8a9 | 7 | Y1, Y2, Y3, Y7 | Gaps: G7; 신규(스펙 외) | subject | fix(cli): Y1 Y2 Y3 Y7 usage errors before any work — JSON without items, unreadable --thresholds, empty path, output path is a folder (Y1, Y2, Y3, Y7; Gaps: G7, 신규) |
| 0418e4d | 7 | Y4 | Gaps: G11 | subject | fix(cache): Y4 --cache never overwrites a file that is not a scan cache (Y4; Gaps: G11) |
| bee165f | 7 | Y5, Y6 | Gaps: 신규(스펙 외) | subject | fix(decoders): Y5 Y6 OpenCV decode chatter goes to the log; ml-classify corrupt image is a Korean stderr error, exit 2 (Y5, Y6; Gaps: 신규) |
| 9575ac4 | 7 | Y8 | Gaps: G34 | subject | fix(servers): Y8 web scan limits below 1 are 400, not clamped to 1 (Y8; Gaps: G34) |
| a349ee2 | 7 | Y9 | Gaps: G34 | subject | fix(archives): Y9 nested members are named by the "::" chain, like the refusal reasons (Y9; Gaps: G34) |
| 3b593ff | 7 | Y10 | Gaps: G30 | subject | fix(reports): Y10 HTML report has a per-row SHA-256 column (Y10; Gaps: G30) |
| 6508a4a | 7 | Y11 | Gaps: 신규(스펙 외) | subject | fix(korean): Y11 English detector reads case-mangled, fullwidth, glued and look-alike spellings (Y11; Gaps: 신규) |
| 382fb18 | 7 | Y13 | Gaps: 신규(스펙 외) | subject | docs(qa): Y13 verification-ID -> spec-gap mapping table and JSON for the commit-message rewrite (Y13; Gaps: 신규) |
| 12af9d0 | 8 | Z1, Z2, Z3, Z4, Z5 | Gaps: G7; 신규(스펙 외) | subject | fix(cli): Z1-Z5 usage errors for realtime/vendor-weights/trailing slash/verify-report/output folders (Z1-Z5; Gaps: G7, 신규) |
| 53251a3 | 8 | WP-J | Gaps: G26, G27, G28 | body | docs(qa): CONFORMANCE.md regenerated on 12af9d0 (WP-J) |
| 3baee5c | 8 | P12, P14 | Gaps: 신규(스펙 외) | body | docs(qa): P12 traceability table regenerated from the current history by script; CI --check (P12, P14; Gaps: 신규) |
| fb1b52b | 8 | P2 | Gaps: G11 | body | fix(cache): P2 a cached row's file name is inserted literally, never as a regex template (P2; Gaps: G11) |
| 62bd96e | 8 | P3 | Gaps: G32, G34 | body | fix(scan): P3 symlinked folders to the root's parents or to each other end the walk; walk limits (P3; Gaps: G32, G34) |
| edafb9a | 8 | P4, P9 | Gaps: G7; 신규(스펙 외) | body | fix(cli): P4 P9 every file input reports encoding/read/JSON errors in Korean before any work, exit 2 (P4, P9; Gaps: G7, 신규) |
| 4ce5a7e | 8 | P10, P11 | Gaps: G31; 신규(스펙 외) | body | fix(cli): P10 P11 outputs checked before any work — writable folder, never inside the examined folder or on an input (P10, P11; Gaps: G31, 신규) |
| e63c0c4 | 8 | P13 | Gaps: 신규(스펙 외) | body | fix(korean): P13 conclusion words fail the English detector alone or inside code tokens (P13; Gaps: 신규) |
| 00bd8a5 | 8 | P5 | Gaps: G12, G34 | body | fix(reports): P5 refused archive members, files inside skipped subfolders and failed rows are "기록되지 않은 파일" (P5; Gaps: G12, G34) |
| 6e5fe69 | 8 | P1, P6 | Gaps: G30, G31 | body | fix(report): P1 P6 /api/report resolves rows against the scan's scan_root only; upload rows are never re-analyzed or signed (P1, P6; Gaps: G30, G31) |
| 9d89129 | 8 | P7 | Gaps: G30, G34 | body | fix(rows): P7 archive members are identified by container/member fields; "::" in a real path is escaped (P7; Gaps: G30, G34) |
| aa0afa7 | 8 | P8 | Gaps: G8, G34 | body | fix(servers): P8 api-serve file endpoints 404/400 for missing files and folders; non-object review bodies, non-media preview, cut-off uploads; every error-table row tested (P8; Gaps: G8, G34) |
| c19738e | 8 | P12 | Gaps: 신규(스펙 외) | body | docs(qa): P12 traceability table regenerated on aa0afa7 — covers up to the parent of this commit (P12; Gaps: 신규) |
| 1b8f2b1 | 8 | Z2 | Gaps: 신규(스펙 외) | subject | fix(cli): Korean usage placeholders and quoted echoed input in usage errors (Z2 leftover, edge8; Gaps: 신규) |
| d64923b | 8 | P12 | Gaps: 신규(스펙 외) | subject | docs(qa): traceability table regenerated (P12; Gaps: 신규) |
| 187dbd7 | 8 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md regenerated on d64923b (WP-J; Gaps: G26, G27, G28) |
| 614d1cb | 9 | R9-1 | Gaps: G30, G34 | subject | fix(rows): R9-1 row identity is the container/member fields only; a real path escapes every ':' and '\' (R9-1; Gaps: G30, G34) |
| cb8dd1d | 9 | R9-2 | Gaps: G12, G32, G34 | subject | fix(scan): R9-2 flat --allow-symlinks subfolder count uses the P3 walker rules — each folder once, no ancestor links, deterministic (R9-2; Gaps: G12, G32, G34) |
| aa2b705 | 9 | R9-3 | Gaps: G8, G34 | subject | fix(api): R9-3 stream endpoints validate every input before the stream starts — 400/403 JSON, never 200 + an SSE error (R9-3; Gaps: G8, G34) |
| 63ea0a1 | 9 | R9-4, R9-5 | Gaps: G31, G7 | subject | fix(cli): R9-4 every output-folder option is kept out of the examined folder; R9-5 feedback labels with a BOM or a cut-off line (R9-4, R9-5; Gaps: G31, G7) |
| 4b2708a | 9 | R9-6 | Gaps: 신규(스펙 외) | subject | fix(text): R9-6 the English detector reads key=value, colon and hash pieces, inflections, Korean endings and compounds of conclusion words (R9-6; Gaps: 신규) |
| ada58f7 | 9 | R9-7 | Gaps: 신규(스펙 외) | subject | fix(cli): R9-7 Korean positional placeholders and --install <묶음 폴더>; echoed input shows newlines and control characters as escapes (R9-7; Gaps: 신규) |
| 4ef3b62 | 9 | R9-8 | Gaps: G8; 신규(스펙 외) | subject | docs(service): R9-8 the endpoint tables list exactly the routes each server has; empty /api/analyze/text is 400 (R9-8; Gaps: G8, 신규) |
| 6919550 | 9 | R9-9 | Gaps: G30, G31 | subject | fix(report): R9-9 the signed web report records scan_root (relative to its read root) and every rendering prints it in the header (R9-9; Gaps: G30, G31) |
| d2036db | 9 | R9-10 | Gaps: 신규(스펙 외) | subject | fix(qa): R9-10 build_traceability_commits --check fails when anything but one table-only regeneration commit follows the table (R9-10; Gaps: 신규) |
| b88ef92 | 9 | R9-10 | Gaps: 신규(스펙 외) | subject | docs(qa): traceability table regenerated with the round-9 IDs — covers up to the parent of this commit (R9-10; Gaps: 신규) |
| 3e256fc | 9 | R9-10 | Gaps: 신규(스펙 외) | subject | fix(qa): generated records may be co-committed; unused imports in scripts (R9-10; Gaps: 신규) |
| eefacd1 | 9 | WP-J, R9-10 | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on 3e256fc (WP-J, R9-10; Gaps: G26, G27, G28) |
| fc1a568 | 9 | R9-10 | Gaps: 신규(스펙 외) | subject | fix(scripts): keep the sweep re-export tests import; noqa the availability probe (R9-10 follow-up; Gaps: 신규) |
| 21717bd | 9 | WP-J, R9-10 | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on fc1a568 (WP-J, R9-10; Gaps: G26, G27, G28) |
| fde64d0 | 10 | R10-1, R10-2 | Gaps: G6, G30; 신규(스펙 외) | subject | fix(reports): R10-1 file names shown through result_text.display_name in every rendering; R10-2 CSV formula cells guarded (R10-1, R10-2; Gaps: G6, G30, 신규) |
| e601adf | 10 | R10-3 | Gaps: 신규(스펙 외) | subject | fix(qa): R10-3 deleted tests cite their commit by subject; QA-SYS-10 fails when it is not in HEAD's history; a skipped test makes a QA ID 건너뜀(환경) (R10-3; Gaps: 신규) |
| e1a9598 | 10 | R10-4 | Gaps: G30, G34 | subject | fix(feedback): R10-4 labels join scan rows on the real path, not the escaped display path (R10-4; Gaps: G30, G34) |
| 40e7ec6 | 10 | R10-5 | Gaps: G29 | subject | fix(cli): R10-5 unusable inputs exit with the documented code; --port checked 1-65535; no per-row error note without rows (R10-5; Gaps: G29) |
| 768a3f3 | 10 | R10-6 | Gaps: G30, G34 | subject | fix(web): R10-6 single-file upload rows escape "::" in their path like every real file (R10-6; Gaps: G30, G34) |
| e9b881f | 10 | R10-7 | Gaps: 신규(스펙 외) | subject | fix(text): R10-7 arrows and CJK/angle brackets are token boundaries of the English detector (R10-7; Gaps: 신규) |
| 7d3c508 | 10 | R10-8 | Gaps: G8, G12; 신규(스펙 외) | subject | fix(cli,docs): R10-8 Korean int/float messages, feedback positions in file lines, CONFORMANCE.md named in the R9-10 check, "(이미 센 폴더 N개 중복 제외)", every api-serve route documented (R10-8; Gaps: G8, G12, 신규) |
| 81e94ea | 10 | R10-9 | Gaps: G31 | subject | test(cli): R10-9 every Path-typed argument is a registered write target or declared read-only (R10-9; Gaps: G31) |
| b600a58 | 10 | WP-J, R10-3 | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on 81e94ea (WP-J, R10-3; Gaps: G26, G27, G28) |
| 3b12bd1 | 11 | R11-1 | Gaps: G30; 신규(스펙 외) | subject | fix(io): R11-1 non-UTF-8 file names (PEP 383 surrogates) never break a scan — surrogate-safe JSON everywhere (R11-1; Gaps: G30, 신규) |
| d10e95f | 11 | R11-2 | Gaps: 신규(스펙 외) | subject | fix(qa): R11-2 QA-SYS-10 counts the full suite's skips as 건너뜀(환경) and lists them; environment-dependent tests documented in docs/QA-ENV-DEPENDENT-TESTS.md (R11-2; Gaps: 신규) |
| b661b3f | 11 | R11-3 | Gaps: G34; 신규(스펙 외) | subject | fix(web,api): R11-3 a request body nested past the JSON recursion limit is a 400 "JSON 중첩이 너무 깊습니다" on both servers, never a 500 (R11-3; Gaps: G34, 신규) |
| e402a18 | 11 | R11-4, R11-6, R11-9 | Gaps: G30; 신규(스펙 외) | subject | fix(reports): R11-4 display_name is injective (every "\" shown "\\"); R11-6 report JSON embedded in <script> escapes < > & U+2028 U+2029; R11-9 no raw invisible characters in source (R11-4, R11-6, R11-9; Gaps: G30, 신규) |
| 8388572 | 11 | R11-5 | Gaps: 신규(스펙 외) | subject | fix(text): R11-5 every non-ASCII symbol/punctuation character, CJK ideograph and kana is a token boundary of the English detector (R11-5; Gaps: 신규) |
| 51eac89 | 11 | R11-8 | Gaps: G29 | subject | fix(cli): R11-8 an input error names the log once — "(라이브러리 오류(X)) — 상세는 로그 파일 …" (R11-8; Gaps: G29) |
| 5161a8b | 11 | R11-11 | Gaps: G31 | subject | test(cli): R11-11 str-typed path options are classified by the R10-9 meta-test too (R11-11; Gaps: G31) |
| 40a862a | 11 | R11-12 | Gaps: G31; 신규(스펙 외) | subject | fix(web): R11-12 the web server answers 404 "찾을 수 없는 경로입니다" for every path that is not /, /gui, /gui.css, /gui.js or /api/* (R11-12; Gaps: G31, 신규) |
| 8292a74 | 11 | R11-14 | Gaps: G30; 신규(스펙 외) | subject | feat(json): R11-14 every JSON row carries display_name — the escaped name — beside the raw name (R11-14; Gaps: G30, 신규) |
| 4c2e7b1 | 11 | R11-7, R11-10, R11-13 | Gaps: G30; 신규(스펙 외) | subject | fix(reports,gui): R11-7 Markdown-active characters escaped in the evidence statement; R11-10 every GUI result string through displayName; R11-13 fullwidth CSV formula prefixes guarded (R11-7, R11-10, R11-13; Gaps: G30, 신규) |
| 77a5463 | 11 | WP-J, R11-2 | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on 4c2e7b1 (WP-J, R11-2; Gaps: G26, G27, G28) |
| d8e05ea | 12 | R12-1, R12-2 | Gaps: G1; 신규(스펙 외) | subject | fix(native): R12-1 native decoders never see a non-ASCII file name — native_safe_path stages an ASCII name; R12-2 audio and PDF of a non-UTF-8 name analysed like their ASCII copy (R12-1, R12-2; Gaps: G1, 신규) |
| d710000 | 12 | R12-3 | Gaps: G34, G30 | subject | fix(archives): R12-3 archive entries naming the same path never overwrite each other — one index-numbered folder per entry, later duplicates "<path>#2" with the reason, member_index in the row identity (R12-3; Gaps: G34, G30) |
| 22ab09b | 12 | R12-4 | Gaps: G8, G31; 신규(스펙 외) | subject | fix(gui,web): R12-4 previews and heatmaps are requested by file-system bytes — rows carry path_b64, scans scan_root_b64, both servers accept path_b64/root_b64; Korean loading errors (R12-4; Gaps: G8, G31, 신규) |
| 0ac910e | 12 | R12-5 | Gaps: G30; 신규(스펙 외) | subject | fix(reports): R12-5 the evidence statement's Markdown renders exactly the shown name — backslashes doubled, "&" as "&amp;", edge whitespace as character references (R12-5; Gaps: G30, 신규) |
| 0a482cb | 12 | R12-6 | Gaps: G30; 신규(스펙 외) | subject | fix(gui): R12-6 the compare slot and the feedback status show names through displayName; the R11-10 check covers textContent/innerText/value and template strings (R12-6; Gaps: G30, 신규) |
| 6457bcf | 12 | R12-7 | Gaps: G11, G30 | subject | fix(scan): R12-7 a file rewritten while it is analyzed is 판단 불가 with no hash — state compared before/after, hash taken before and after (R12-7; Gaps: G11, G30) |
| 041fb7f | 12 | R12-8 | Gaps: G27; 신규(스펙 외) | subject | fix(corpus): R12-8 corpus verify refuses relpaths that leave the corpus under either path flavour — drive, root, UNC, ".." with "\" (R12-8; Gaps: G27, 신규) |
| 5b002a4 | 12 | R12-9 | Gaps: G30; 신규(스펙 외) | subject | fix(reports): R12-9 every report timestamp is ISO 8601 with the UTC offset — legal report, PDF "감정 일시", forensic and evidence records, vendor report, batch jobs (R12-9; Gaps: G30, 신규) |
| 63cf82e | 12 | R12-10 | Gaps: G30; 신규(스펙 외) | subject | fix(web): R12-10 multipart file names keep their bytes — raw Content-Disposition decoded as RFC 5987, UTF-8, CP949, else surrogate escapes (R12-10; Gaps: G30, 신규) |
| db73702 | 12 | R12-11 | Gaps: G31; 신규(스펙 외) | subject | fix(api): R12-11 a trailing slash is 404 on both servers — api-serve no longer redirects /gui/ to /gui (R12-11; Gaps: G31, 신규) |
| 68035c3 | 12 | R12-12 | Gaps: 신규(스펙 외) | subject | fix(text): R12-12 the English detector drops every format character (Cf) — TAG characters no longer hide a word (R12-12; Gaps: 신규) |
| 9085e5e | 12 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on 68035c3, round-12 IDs mapped (WP-J; Gaps: G26, G27, G28) |
| 9ee4bf8 | 13 | R13-1 | Gaps: G32, G11; 신규(스펙 외) | subject | fix(native): R13-1 a staged ASCII name never reaches a reason — native_safe_path maps it back to the original path; the cache rewrite is one pass (R13-1; Gaps: G32, G11, 신규) |
| 476e412 | 13 | R13-2 | Gaps: G11, G30, G34 | subject | fix(scan): R13-2 an archive rewritten while its members are extracted is 판단 불가 with no hash — container and every member row (R13-2; Gaps: G11, G30, G34) |
| 6a4aded | 13 | R13-3 | Gaps: G31, G8; 신규(스펙 외) | subject | feat(web,api): R13-3 /api/analyze-file takes file_b64 and /api/scan folder_b64 — a non-UTF-8 path is addressable on both servers (R13-3; Gaps: G31, G8, 신규) |
| faf6b5d | 13 | R13-4, R13-8 | Gaps: G34, G1; 신규(스펙 외) | subject | fix(native): R13-4 the staging folder is removed on SIGTERM/SIGINT and stale ones are swept; R13-8 Windows stages by hard link, then the 8.3 short name, then a capped copy (R13-4, R13-8; Gaps: G34, G1, 신규) |
| a9721b2 | 13 | R13-5 | Gaps: G27; 신규(스펙 외) | subject | fix(corpus): R13-5 corpus verify names a relpath through display_name — two non-UTF-8 names no longer both print "r?.jpg" (R13-5; Gaps: G27, 신규) |
| a4f9b08 | 13 | R13-6 | Gaps: G34, G30 | subject | fix(archives): R13-6 a duplicate-name reason is the member row's own coverage entry — nested members of a renamed inner archive carry it too (R13-6; Gaps: G34, G30) |
| 0f22403 | 13 | R13-7 | Gaps: G1; 신규(스펙 외) | subject | fix(native): R13-7 face crops and video frames are written with imencode + Python — a Korean or non-UTF-8 temp folder works; the meta-test resolves cv2 aliases and covers sub-packages (R13-7; Gaps: G1, 신규) |
| ec8e4da | 13 | R13-9 | Gaps: G30; 신규(스펙 외) | subject | fix(reports): R13-9 Markdown escapes ".", ":", "@" and "/" — no GFM/linkify autolink (www., http(s)://, ftp://, //host, e-mail, bare domain) forms from a name (R13-9; Gaps: G30, 신규) |
| 130dddd | 13 | R13-1 | Gaps: G32, G11; 신규(스펙 외) | subject | fix(native): R13-1 follow-up — a quoted non-UTF-8 name in a reason reads as in its row (repr()'s "\udcc1" escapes undone), the cache rewrite gives the same form (R13-1; Gaps: G32, G11, 신규) |
| 232cfbd | 13 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on 130dddd, round-13 IDs mapped (WP-J; Gaps: G26, G27, G28) |
| b914bce | 14 | R14-1 | Gaps: G34, G1; 신규(스펙 외) | subject | fix(native): R14-1 an inherited SIG_IGN stays ignored — a nohup scan survives SIGHUP/SIGINT with its staging folder; only a default action gets the cleanup handler (R14-1; Gaps: G34, G1, 신규) |
| 0a9f42b | 14 | R14-2, R14-8 | Gaps: G30, G1; 신규(스펙 외) | subject | fix(native): R14-2 removing a staged name never changes the evidence file — no chmod through a link, no hard links; R14-8 the Windows ctypes calls run against a fake WinDLL (R14-2, R14-8; Gaps: G30, G1, 신규) |
| fd4bc0a | 14 | R14-3, R14-4 | Gaps: G11, G32; 신규(스펙 외) | subject | fix(cache): R14-3 a cache row written before R13-1 is never replayed — key content-v4 with an output-format generation, rows carrying staging text dropped on load; R14-4 only this process's own staging folders and names are restored (R14-3, R14-4; Gaps: G11, G32, 신규) |
| 7fefde4 | 14 | R14-5 | Gaps: G12, G1; 신규(스펙 외) | subject | fix(native): R14-5 the Haar cascade is opened through native_safe_path — a cascade that does not load is a failed face / rPPG / lip-sync check, never "얼굴 미검출" (R14-5; Gaps: G12, G1, 신규) |
| cf1fe06 | 14 | R14-1 | Gaps: G34; 신규(스펙 외) | subject | test(native): R14-1 follow-up — the SIGINT cleanup test's child starts with SIGINT/SIGTERM at their default (R14-1; Gaps: G34, 신규) |
| 045566a | 14 | R14-6 | Gaps: G11, G30 | subject | fix(scan): R14-6 a same-size rewrite with touch -r is caught above 64 MiB — the state carries ctime_ns (POSIX) and an archive is hashed before extraction at any size (R14-6; Gaps: G11, G30) |
| b0f5f81 | 14 | R14-7 | Gaps: G34, G1; 신규(스펙 외) | subject | fix(native): R14-7 every temp file is made in the session folder — a killed scan leaves nothing the cleanup and the next run's sweep do not remove (R14-7; Gaps: G34, G1, 신규) |
| 815a108 | 14 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on b0f5f81, round-14 IDs mapped (WP-J; Gaps: G26, G27, G28) |
| 15c88af | 15 | R15-1 | Gaps: G34, G1; 신규(스펙 외) | subject | fix(native): R15-1 a SIGTERM/SIGHUP cleanup is final — "종료 중" flag, ffmpeg children stopped, folders removed until gone; SIGKILL takes the children along (R15-1; Gaps: G34, G1, 신규) |
| 8f06324 | 15 | R15-3 | Gaps: G9, G1; 신규(스펙 외) | subject | fix(models): R15-3 no model asset loads without a sha256 pin — models/assets.json pins FaceLandmarker, SyncNet and the Haar cascade; refused assets fail the check "미고정 모델" (R15-3; Gaps: G9, G1, 신규) |
| c5115ae | 15 | R15-2 | Gaps: 신규(스펙 외) | subject | fix(privacy): R15-2 third-party telemetry is off before any dependency loads — ORT_DISABLE_TELEMETRY and the other opt-outs set first in deepfake_lens/__init__.py (R15-2; Gaps: 신규) |
| 0b44e2d | 15 | R15-4 | Gaps: G34; 신규(스펙 외) | subject | docs(qa): R15-4 the SIGINT-ignored test failures of the R14-1…R14-5 commits are recorded; the harness always starts children with SIGINT/SIGTERM/SIGHUP at their default (R15-4; Gaps: G34, 신규) |
| a1b44d1 | 15 | R15-5 | Gaps: 신규(스펙 외) | subject | test(native): R15-5 no preexec_fn in the threaded test process — signal dispositions set in an exec trampoline; ruff PLW1509 on (R15-5; Gaps: 신규) |
| 56c817d | 15 | R15-6 | Gaps: G34; 신규(스펙 외) | subject | fix(native): R15-6 a temp folder that cannot be used is reported — Korean notice on stderr once, temp_folder in the scan result and report, DEEPFAKE_LENS_TMPDIR chooses it (R15-6; Gaps: G34, 신규) |
| 64f4dac | 15 | R15-7 | Gaps: G11; 신규(스펙 외) | subject | test(cache): R15-7 a golden-output meta-test ties OUTPUT_FORMAT_GENERATION to the scan output — a changed golden under the same generation fails (R15-7; Gaps: G11, 신규) |
| f13fda6 | 15 | R15-8 | Gaps: G34; 신규(스펙 외) | subject | fix(face): R15-8 MediaPipe is imported without sounddevice — no ldconfig/gcc/ld children and no cc*/tmp* files in TMPDIR (R15-8; Gaps: G34, 신규) |
| 2da7a31 | 15 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on f13fda6, round-15 IDs mapped (WP-J; Gaps: G26, G27, G28) |
| ce487f2 | 16 | R16-5 | Gaps: G11 | subject | test(cache): R16-5 the golden record adds a full-extras scan and a hash of the Korean constants of result_text/core; a raised generation is accepted when recorded with a reason (R16-5; Gaps: G11) |
| 9d7f118 | 16 | R16-1, R16-2 | Gaps: G1, G9, G12 | subject | fix(face): R16-1/R16-2 every face layer detects strictly — a refused, unloadable or crashing detector is failed with the cause; a DEEPFAKE_LENS_HAAR_CASCADE override is the only cascade tried (R16-1, R16-2; Gaps: G1, G9, G12) |
| 11cf522 | 16 | R16-3 | Gaps: G9 | subject | fix(assets): R16-3 the in-process caches of verified asset bytes are keyed on the current pin — a re-pin, a removed pin or a swapped weight file takes effect without a restart (R16-3; Gaps: G9) |
| b0c0285 | 16 | R16-4 | Gaps: G34 | subject | fix(face): R16-4 MediaPipe is imported with matplotlib served as an inert stub — no fc-list children and no font-cache file; the test now checks the whole process tree and every file (R16-4; Gaps: G34) |
| d9e178b | 16 | R16-6, R16-7 | Gaps: G34 | subject | fix(native): R16-6/R16-7 children get /dev/null as stdin (ffmpeg -nostdin) and run in their own session under a Linux supervisor — stop, timeout and a SIGKILLed scan end the whole process tree, grandchildren included (R16-6, R16-7; Gaps: G34) |
| 42f0344 | 16 | R16-8, R16-14 | Gaps: G29 | subject | fix(cli): R16-8/R16-14 vendor-weights refuses a --models-dir that is not a folder (Korean, exit 2) and pin-asset reports a manifest write error in Korean without the temp file name (R16-8, R16-14; Gaps: G29) |
| fcde16a | 16 | R16-9 | Gaps: G29, G9 | subject | feat(doctor): R16-9 doctor shows every model asset's pin state — table section and JSON model_assets[] (R16-9; Gaps: G29, G9) |
| ee091ac | 16 | R16-10 | Gaps: G34 | subject | fix(native): R16-10 the temp-folder fallback names who chose the unusable folder (HTML, scan JSON) and the evidence statement records it (R16-10; Gaps: G34) |
| 1653be9 | 16 | R16-11, R16-12 | Gaps: G26; 신규(스펙 외) | subject | test(qa): R16-11 the telemetry strace leg skips where ptrace is denied; R16-12 the two commit-history tests share one rule and one failure message, documented (R16-11, R16-12; Gaps: G26, 신규) |
| 0ce6d1b | 16 | R16-13, R16-15 | Gaps: G13, G34 | subject | fix(cli): R16-13 Ctrl-C ends the CLI with "중단됨(사용자 요청)" and exit 130 after the cleanup; R16-15 with_default_signals.py reports a command that cannot start in Korean, exit 127/126 (R16-13, R16-15; Gaps: G13, G34) |
| ad02d2b | 16 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on 0ce6d1b, round-16 IDs mapped (WP-J; Gaps: G26, G27, G28) |
| 76955b9 | 16 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): handoff — spec, round 1-17 verification findings and resume guide (WP-J; Gaps: 신규) |
| ed30923 | 16 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on 76955b9 (WP-J; Gaps: G26, G27, G28) |
| 8f205bd | 17 | R17-12 | Gaps: 신규(스펙 외) | subject | test(assets): R17-12 the re-pin tests move the manifest's mtime forward when the file-system clock did not — no dependence on timestamp granularity (R17-12; Gaps: 신규) |
| 27bc793 | 17 | R17-1, R17-2, R17-3 | Gaps: G34 | subject | fix(native): R17-1/R17-2/R17-3 the child supervisor blocks its stop signals, reports a SIGKILLed program as -9 and signals and reaps until no descendant is left (R17-1, R17-2, R17-3; Gaps: G34) |
| 8cd1a33 | 17 | R17-4 | Gaps: G1, G12 | subject | fix(video): R17-4 an ffmpeg that cannot run or dies fails av_audio and lipsync with the exit code or signal — only ffmpeg's "no stream" error is "no audio track" (R17-4; Gaps: G1, G12) |
| c1dee0a | 17 | R17-5 | Gaps: G12, G29, G1 | subject | fix(face): R17-5 face_track is skipped "의존성 부재: 실측 랜드마크 검출기 없음" when neither FaceMesh nor a FaceLandmarker asset exists; doctor warns on the mediapipe row; a FACE_LANDMARKER override naming no file fails the check (R17-5; Gaps: G12, G29, G1) |
| 498558d | 17 | R17-6 | Gaps: G1, G12 | subject | fix(models): R17-6 a video-frames member that scored no frame carries the inner runtime's cause (exception class included) in its detail and so in its coverage reason (R17-6; Gaps: G1, G12) |
| 111b302 | 17 | R17-9 | Gaps: G29, G9 | subject | fix(assets): R17-9 doctor and the loaders read an asset override and the manifest the same way — "~" expanded everywhere, a broken assets.json shown in doctor, a UTF-8 BOM accepted (R17-9; Gaps: G29, G9) |
| cbf9752 | 17 | R17-7 | Gaps: G11, G9 | subject | fix(cache): R17-7 the scan-cache key holds the model-asset files the loaders would read — override variables and each file's sha256 — not only the pins (R17-7; Gaps: G11, G9) |
| ee2818f | 17 | R17-8 | Gaps: G34 | subject | fix(cli): R17-8 an interrupt cleans up before the log handlers are removed, a Ctrl-C during the package import is a Korean line and exit 130, an interrupted batch starts no further file (R17-8; Gaps: G34) |
| 98672a1 | 17 | R17-10 | Gaps: G29 | subject | fix(cli): R17-10 vendor-weights --verify of a folder without runtime profiles is an error, exit 2 — not "통과 … 프로필 0개" (R17-10; Gaps: G29) |
| 3fe35fb | 17 | R17-11 | Gaps: G34; 신규(스펙 외) | subject | fix(native): R17-11 a DEEPFAKE_LENS_TMPDIR that names a file is reported "(폴더가 아니라 파일임)"; the traceability mapping of R16-13 is corrected from G13 to G34 (R17-11; Gaps: G34, 신규) |
| ec0d057 | 17 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on 3fe35fb, round-17 IDs mapped (WP-J; Gaps: G26, G27, G28) |
| 16d1df8 | 17 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): round-18 verification findings and UI/UX/AX audit (WP-J; Gaps: 신규) |
| b42f5c1 | 18 | R18-1 | Gaps: G11, G9 | subject | fix(cache): R18-1 the asset tokens of the scan-cache key hold the asset name, the file's role and its sha256 — no install, venv or models-folder path (R18-1; Gaps: G11, G9) |
| 41c3aef | 18 | R18-2 | Gaps: G9, G29, G11 | subject | fix(assets): R18-2 an override variable "~nouser/…" is read as the literal path — no RuntimeError stops scan --cache or doctor; the cache key never raises (R18-2; Gaps: G9, G29, G11) |
| a3ac2aa | 18 | R18-3 | Gaps: G12, G7 | subject | fix(scan): R18-3 a cancelled scan counts the files it never started — summary.cancelled / files_cancelled, unrecorded reason "사용자 취소", job status "cancelled", a partial-result note in the summary line, the unrecorded section and the GUI (R18-3; Gaps: G12, G7) |
| 2b2c112 | 18 | R18-4 | Gaps: G12, G1 | subject | fix(metadata): R18-4 a PNG's chunks and a WebP's XMP chunk are read through the whole file; a metadata read limited to a prefix is "ran" with the reason "메타데이터 앞 N … 만 확인" (R18-4; Gaps: G12, G1) |
| 1c124aa | 18 | R18-5 | Gaps: G30 | subject | fix(signing): R18-5 a signed report whose text repeats a key inside one JSON object is 변조됨 — verify-report exit 1 (R18-5; Gaps: G30) |
| 46e06da | 18 | R18-6 | Gaps: G12, G1 | subject | fix(metadata): R18-6 the EXIF of an image over Pillow's pixel-count limit (200 MP camera photos) is read from the file structure; the refused decode says "픽셀 수 상한 초과 — 디코드 생략" (R18-6; Gaps: G12, G1) |
| e498775 | 18 | R18-7 | Gaps: G1, G12 | subject | fix(rppg): R18-7 a video rPPG cannot open or decode fails the rppg check with "영상을 디코딩할 수 없습니다" — not skipped "획득 프레임 0" (R18-7; Gaps: G1, G12) |
| 259f7e3 | 18 | R18-8 | Gaps: G11 | subject | fix(gui): R18-8 a review mark is keyed by the file's sha256 inside its scanned folder ("v2\|root:<scan_root>\|sha256:<hash>") — not by the row path in one global store (R18-8; Gaps: G11) |
| 80875cc | 18 | R18-9 | Gaps: G34 | subject | fix(native): R18-9 the child supervisor sets RLIMIT_CORE 0 (inherited by the program) and clears its dumpable flag before it re-raises the program's signal — no core file in the working directory (R18-9; Gaps: G34) |
| 3d46a51 | 18 | R18-10 | Gaps: G31; 신규(스펙 외) | subject | fix(scan): R18-10 a file on /proc or /sys, or one with stat size 0 but content, is a skipped row "건너뜀: 특수 파일 시스템" — --allow-symlinks no longer analyzes /proc/self/environ (R18-10; Gaps: G31, 신규) |
| 15d789a | 18 | R18-11 | Gaps: G30; 신규(스펙 외) | subject | fix(report): R18-11 display_name escapes a run that is not NFC, a space other than U+0020, a default-ignorable code point and a mark after an escape — names that render alike are shown differently (R18-11; Gaps: G30, 신규) |
| fe963eb | 18 | R18-12 | Gaps: G34 | subject | fix(cli): R18-12 the import guard is the first statement of the package and imports only os, signal and sys; the Python start-up, the console-script stub's own imports and finalization stay uncovered (R18-12; Gaps: G34) |
| 12348ef | 18 | R18-13 | Gaps: G29 | subject | fix(doctor): R18-13 a missing asset override is "재정의없음 — 검사는 failed", and the mediapipe / opencv rows warn when the checks that need them cannot run as the row said (R18-13; Gaps: G29) |
| 1b5dbdd | 18 | R18-14 | Gaps: G26, G27 | subject | fix(qa): R18-14 the ids[] gap mappings follow their commits' Gaps (R16-8 G13→G29 and eleven more) and --check fails on a mapping that differs unless it has a gaps_note (R18-14; Gaps: G26, G27) |
| 9067673 | 18 | R18-15 | Gaps: G34; 신규(스펙 외) | subject | fix(cli): R18-15 output files are written whole (temp file + rename) and output failures are Korean lines — a closed pipe is a quiet 141, a full disk "출력 파일을 쓸 수 없습니다", a closed stdout exit 2 (R18-15; Gaps: G34, 신규) |
| 91ee0d1 | 18 | R18-16 | Gaps: 신규(스펙 외) | subject | fix(report): R18-16 the evidence statement's default case name is the placeholder "(사건명 입력)" — not the offence "성폭력처벌법위반(허위영상물편집등) 및 정보통신망법위반" (R18-16; Gaps: 신규) |
| 9ecd4ed | 18 | R18-17 | Gaps: G1 | subject | fix(gui): R18-17 a model that did not run shows "없음 — 미실행(<사유>)" in the GUI — not "외부 모델 원점수(미보정, 결론 불참여) — 0" (R18-17; Gaps: G1) |
| 48a07ce | 18 | R18-18 | Gaps: G4, G24 | subject | fix(layers): R18-18 the agent layer diagnostic carries raw signals only — no confidence, no agent_type — and its markers match whole words (R18-18; Gaps: G4, G24) |
| a155537 | 18 | R18-19 | Gaps: G1; 신규(스펙 외) | subject | fix(cli): R18-19a `video --extract` reports failed frame extractions on stderr and exits 1 — it exited 0 without a word when every ffmpeg run failed (R18-19; Gaps: G1, 신규) |
| 804ce5c | 18 | R18-19 | Gaps: G29 | subject | fix(cli): R18-19b vendor-weights names where its models folder came from (--models-dir, DEEPFAKE_LENS_MODELS_DIR) and refuses a missing folder or a file named by the variable like one named by --models-dir (R18-19; Gaps: G29) |
| afd7036 | 18 | R18-19 | Gaps: 신규(스펙 외) | subject | fix(cli): R18-19c the office config file may start with a UTF-8 BOM like assets.json, and a folder at the config path is a usage error instead of being ignored (R18-19; Gaps: 신규) |
| 3df21ce | 18 | R18-19 | Gaps: 신규(스펙 외) | subject | fix(archives): R18-19d zip member names without the UTF-8 flag are decoded as cp949 — euc-kr left the cp949 extension syllables ("똠방각하") as cp437 mojibake (R18-19; Gaps: 신규) |
| c9e1f81 | 18 | R18-19 | Gaps: G26 | subject | fix(qa): R18-19e `qa_phase0.py --verify-record` in a shallow clone says the parent commit is not fetched — not "다시 생성하세요" (R18-19; Gaps: G26) |
| bc99d8a | 18 | R18-19 | Gaps: 신규(스펙 외) | subject | fix(cli): R18-19f an argument a subcommand does not take is reported by that subcommand's parser with its own usage — not the top-level usage (R18-19; Gaps: 신규) |
| 8675d90 | 18 | — | Gaps: 신규(스펙 외) | subject | feat(api): GUI redesign — /api/scan-status reports done/total/current/recent/elapsed, a running scan job is never evicted, /api/stats names the default folder and PDF availability (GUI; Gaps: 신규) |
| dd4c860 | 18 | — | Gaps: 신규(스펙 외) | subject | feat(gui): redesign the local GUI as a court-docket ledger — conclusion headline, verdict strip, file ledger + evidence panel, scan/progress screen, evidence-statement screen (GUI; Gaps: 신규) |
| f127421 | 18 | — | Gaps: 신규(스펙 외) | subject | test(gui): gui_smoke drives the redesigned GUI — prefilled folder + Enter, focus on the headline, strip/ledger agreement, evidence-first order, panel order, ledger keys, report gating, 375px reflow, no off-host requests; docs describe the screens (GUI; Gaps: 신규) |
| b95b46e | 18 | — | Gaps: 신규(스펙 외) | subject | test(gui): ledger reasons, grouped skipped-check reasons, model members listed once, environment notice from the rows (node), and the accessibility/offline invariants of the page (GUI; Gaps: 신규) |
| e9ad16b | 18 | — | Gaps: 신규(스펙 외) | subject | test(gui): gui_smoke waits with function predicates (string predicates are eval, refused by the GUI's CSP) and ignores only the browser's own /favicon.ico probe (GUI; Gaps: 신규) |
| 39e11d9 | 18 | — | Gaps: 신규(스펙 외) | subject | fix(gui): a long ledger path keeps its file name whole — the folder part is muted and cut first (GUI; Gaps: 신규) |
| 2130892 | 18 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on bc99d8a, round-18 IDs mapped (WP-J; Gaps: G26, G27, G28) |
| bad0163 | 18 | — | Gaps: 신규(스펙 외) | subject | docs(gui): adversarial review of gui-redesign 39e11d9 (GUI-REV; Gaps: 신규) |
| abe01a5 | 18 | — | Gaps: G30; 신규(스펙 외) | subject | fix(gui): G-1 the exhibit choice no longer drops rows from signed artifacts — every row is posted, the choice only numbers the evidence statement's exhibits and the rest go to its signed appendix (GUI-REV G-1; Gaps: G30, 신규) |
| a9cecf8 | 18 | — | Gaps: 신규(스펙 외) | subject | fix(gui): G-2 G-3 a 판단 불가 row that carries record evidence says "단서만 있음", never "근거 없음"; the headline splits 판단 불가 by why; a decisive check that did not run leads the row reason (GUI-REV G-2, G-3; Gaps: 신규) |
| 39719a0 | 18 | — | Gaps: 신규(스펙 외) | subject | fix(gui): G-4 errors belong to the scan that raised them — a new scan or upload marks the earlier ones "이전 검사" (GUI-REV G-4; Gaps: 신규) |
| 6e36242 | 18 | — | Gaps: 신규(스펙 외) | subject | fix(gui): G-5 while a new scan runs the ledger and report say the rows are the previous scan's, exports wait, and the new options attach only to the new results (GUI-REV G-5; Gaps: 신규) |
| 959f80d | 18 | — | Gaps: 신규(스펙 외) | subject | fix(gui): G-6 a scan reconnected after a reload is shown on the 검사 screen, and a result handled on another screen leaves focus on a visible element (GUI-REV G-6; Gaps: 신규) |
| 4079d09 | 18 | — | Gaps: 신규(스펙 외) | subject | fix(gui): G-7 a pasted-text quick check leads with the legal limitation and its reference signals, and shows no C2PA failure (GUI-REV G-7; Gaps: 신규) |
| b759c29 | 18 | — | Gaps: 신규(스펙 외) | subject | fix(gui): G-8 a review mark shows on every row that shares its content key, not only the row it was set on (GUI-REV G-8; Gaps: 신규) |
| 8686bc1 | 18 | — | Gaps: 신규(스펙 외) | subject | fix(gui): G-9 의뢰 측 is chosen (no default, 갑/을호증, required for the evidence statement), the export note names what can be made, the preview's limits come from the scan, clearing asks first (GUI-REV G-9; Gaps: 신규) |
| bc41c38 | 18 | — | Gaps: 신규(스펙 외) | subject | fix(gui): G-10 the rendered evidence-statement PDF prints the appendix rows and hashes; gui_smoke checks every export payload and the downloaded signed JSON against the ledger (GUI-REV G-10; Gaps: 신규) |
| 4fb7d25 | 18 | — | Gaps: G26 | subject | fix(gui): G-10 the new GUI tests' skip reasons are rows of QA-ENV-DEPENDENT-TESTS (the PDF test uses the documented "pymupdf not installed") (GUI-REV G-10; Gaps: G26) |
| ca9327e | 18 | — | Gaps: 신규(스펙 외) | subject | docs(gui): round-2 adversarial review of gui-redesign 4fb7d25 (GUI-REV2; Gaps: 신규) |
| 244f906 | 18 | — | Gaps: G30; 신규(스펙 외) | subject | fix(gui): H-1 the evidence statement numbers its exhibits in the order the preview shows — exhibit_paths is an ordered list, uploads are never preview exhibits, format=evidence-json exposes the signed statement (GUI-REV2 H-1; Gaps: G30, 신규) |
| 8153d39 | 18 | — | Gaps: G30; 신규(스펙 외) | subject | fix(gui): H-7 the server refuses an evidence statement with no exhibit, a repeated row or a path that is not one of its signed rows — 400 with the unknown paths (GUI-REV2 H-7; Gaps: G30, 신규) |
| b147166 | 18 | — | Gaps: 신규(스펙 외) | subject | fix(gui): H-3 the defendant side is worded consistently — 변호인 in every rendering, no statute list, the client placeholder in the defendant box, 을 in the forensic PDF's exhibit box (GUI-REV2 H-3; Gaps: 신규) |
| 2014f66 | 18 | — | Gaps: 신규(스펙 외) | subject | fix(gui): H-6 a cue is never hidden behind a failure or the reference grade; an unverified C2PA record is its own kind; cue rows get cue wording (GUI-REV2 H-6; Gaps: 신규) |
| b43878b | 18 | — | Gaps: G30; 신규(스펙 외) | subject | fix(gui): H-4 an appendix row keeps why it is undetermined — the cue, the unverified record or the failed checks — and the checklist warns which appendix rows carry them (GUI-REV2 H-4; Gaps: G30, 신규) |
| f8967fb | 18 | — | Gaps: 신규(스펙 외) | subject | fix(gui): H-2 the evidence-statement preview is drawn from the server's own template — the PDF's case box, exhibit rows, notice / 기록되지 않은 파일 / appendix boxes and signoff, nothing else (GUI-REV2 H-2; Gaps: 신규) |
| 6191ddb | 18 | — | Gaps: 신규(스펙 외) | subject | fix(gui): H-5 a finished scan opens the ledger only from the 검사 screen — elsewhere the band says "검사가 끝났습니다 — 결과 보기" and focus stays put (GUI-REV2 H-5; Gaps: 신규) |
| d9bb01a | 18 | — | Gaps: 신규(스펙 외) | subject | fix(gui): H-8 case fields kept for the tab belong to the folder they were entered for — another folder asks for a confirmation before the statement or forensic PDF (GUI-REV2 H-8; Gaps: 신규) |
| 8893eb2 | 18 | — | Gaps: 신규(스펙 외) | subject | fix(gui): H-9 an upload stages each batch's provenance and commits it with its results — a failed upload leaves the shown results' provenance alone (GUI-REV2 H-9; Gaps: 신규) |
| 6294acf | 18 | — | Gaps: 신규(스펙 외) | subject | fix(gui): H-10 only a scan's own errors are marked "이전 검사" when the next scan starts (GUI-REV2 H-10; Gaps: 신규) |
| 3fb9056 | 18 | — | Gaps: 신규(스펙 외) | subject | fix(gui): H-2 type the preview's empty excluded-row list (mypy) (GUI-REV2 H-2; Gaps: 신규) |
| a5aca29 | 18 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): round-19 verification findings (WP-J; Gaps: 신규) |
| b829570 | 19 | R19-1 | Gaps: G11, G1 | subject | fix(cache): R19-1 the scan-cache key carries the runtime — which optional modules are found, their versions and module-file hashes, cv2/CascadeClassifier and ffmpeg — never a path (R19-1; Gaps: G11, G1) |
| 9a343d0 | 19 | — | Gaps: 신규(스펙 외) | subject | docs(gui): round-3 adversarial review of gui-redesign 3fb9056 (GUI-REV3; Gaps: 신규) |
| 840e68e | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): J-3 the preview prints the statement's line breaks and its already-escaped file names as the PDF does — no literal "\n", no doubled backslash (GUI-REV3 J-3; Gaps: 신규) |
| 667377d | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): J-4 editing a case field or the side no longer rebinds the case to the current folder; each upload is its own scope (GUI-REV3 J-4; Gaps: 신규) |
| 90dba52 | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): J-5 a cancelled scan finishing on another screen keeps the examiner's focus and says "검사가 취소되었습니다 — 부분 결과 보기" (GUI-REV3 J-5; Gaps: 신규) |
| 304f87c | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): J-6 before a side is chosen the preview marks "(의뢰 측 미선택)" instead of drawing the 원고·갑 form, and the forensic PDF waits for the side (GUI-REV3 J-6; Gaps: 신규) |
| 56151f7 | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): J-7 a refused evidence export names its rows — chosen rows the server could not re-analyze (with the reason) apart from paths it does not know (GUI-REV3 J-7; Gaps: 신규) |
| a92b68f | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): J-2 a dedupe duplicate shows its original's conclusion, and the checklist names duplicates of a manipulated file that go to the appendix (GUI-REV3 J-2; Gaps: 신규) |
| acbb293 | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): J-8 uploaded files are said to be unable to become exhibits, and they are left out of the group and appendix counts (GUI-REV3 J-8; Gaps: 신규) |
| 037a596 | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): J-9 one 판단 불가 order everywhere and the cue/failed overlap is said, so the headline adds up to the report's "검사가 실패한 파일 n개" (GUI-REV3 J-9; Gaps: 신규) |
| 5142c15 | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): J-10 the unsupported-format refusal lists every format, the checklist names the empty boxes apart, the defendant box placeholder drops "귀하" (GUI-REV3 J-10; Gaps: 신규) |
| de59997 | 19 | R19-2 | Gaps: G1; 신규(스펙 외) | subject | fix(audio): R19-2 librosa feature extraction runs in a supervised worker process and numba compiles into a private cache — a native crash fails audio_features instead of killing the scan, and concurrent scans share no numba cache files (R19-2; Gaps: G1, 신규) |
| 66293b1 | 19 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): round-19 finding R19-13 added by the coordinator — /api/report signs a re-analysis that differs from the posted rows (WP-J; Gaps: 신규) |
| 15185a0 | 19 | R19-13 | Gaps: G30, G7 | subject | fix(report): R19-13 /api/report refuses with 409 and changed_rows when its re-analysis of a posted row differs in sha256, status or verdict, or the row's file is gone — it signed the re-analysis silently (R19-13; Gaps: G30, G7) |
| b7b27c5 | 19 | R19-3 | Gaps: G7, G30 | subject | fix(gui): R19-3 the GUI's report options change with the rows on screen, exports are disabled and refused during a scan, the signed web report records the options it re-analyzed with, and a posted cancel count is signed only when the server's job confirms it (R19-3; Gaps: G7, G30) |
| 767f7d8 | 19 | R19-5 | Gaps: G34; 신규(스펙 외) | subject | fix(cli): R19-5 every path argument and web request parameter takes an unknown "~user" literally — no English RuntimeError traceback, no HTTP 500 (R19-5; Gaps: G34, 신규) |
| 2525982 | 19 | R19-4 | Gaps: G31; 신규(스펙 외) | subject | fix(cli): R19-4 DEEPFAKE_LENS_LOG_DIR and DEEPFAKE_LENS_HEATMAP_DIR are absolute and checked like output paths — inside a scanned folder or an unknown "~user" is exit 2 before any work (R19-4; Gaps: G31, 신규) |
| 47d80c9 | 19 | R19-6 | Gaps: G34; 신규(스펙 외) | subject | fix(output): R19-6 every remaining output writer goes through the whole-file writer, whose temp file is removed on any exception, and the docs list exactly what is covered (R19-6; Gaps: G34, 신규) |
| bc3eea1 | 19 | R19-10 | Gaps: G1 | subject | fix(gui): R19-10 a layer diagnostic or comparison without a score shows "없음" — not "원점수 0" (R19-10; Gaps: G1) |
| f6726cd | 19 | R19-11 | Gaps: 신규(스펙 외) | subject | fix(report): R19-11 the evidence statement names no charge or statute — a manipulation row leaves "관련 법조" to the examiner (R19-11; Gaps: 신규) |
| d70f460 | 19 | R19-9 | Gaps: 신규(스펙 외) | subject | fix(cli): R19-9 an unknown argument of a nested subcommand is reported by that subcommand's parser, and an unknown option before a command by the top-level parser (R19-9; Gaps: 신규) |
| c3f9594 | 19 | R19-7 | Gaps: G12, G1 | subject | fix(metadata): R19-7 an APNG's frame data (fdAT) is skipped by seeking like IDAT — not read as a metadata chunk against --metadata-bytes (R19-7; Gaps: G12, G1) |
| 8ffd1cf | 19 | R19-8 | Gaps: G30; 신규(스펙 외) | subject | fix(report): R19-8 display_name escapes U+2800 BRAILLE PATTERN BLANK and private-use characters — "a⠀b.jpg" no longer looks like "a b.jpg" (R19-8; Gaps: G30, 신규) |
| 9142684 | 19 | R19-12 | Gaps: 신규(스펙 외) | subject | docs(phase0): R19-12 HANDOFF.md describes the current state — R17/R18 fixed, R19-1…R19-13 fixed on this branch, 1,684 tests, the round-18/19 documents and the Docker gate image (R19-12; Gaps: 신규) |
| 05091f9 | 19 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on 9142684, round-19 IDs mapped (WP-J; Gaps: G26, G27, G28) |
| d613df3 | 19 | — | Gaps: G30, G7 | subject | fix(gui): R19-13 in the GUI — a 409 export lists the changed files with both verdicts, marks their ledger rows "검사 뒤 바뀜", offers 다시 검사; the preview says changed files refuse the export (GUI-REV3 J-1, J-2; Gaps: G30, G7) |
| 2c9ed70 | 19 | — | Gaps: 신규(스펙 외) | subject | docs(gui): round-4 adversarial review of gui-redesign d613df3 (GUI-REV4; Gaps: 신규) |
| db636a4 | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): K-4 a 409 says what changed — content only (same verdict, other SHA-256), the original of an unchanged duplicate, or a file the server could not re-analyze (GUI-REV4 K-4; Gaps: 신규) |
| 9ceadce | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): K-1 an export response is bound to the results it was asked for — a late one is ignored, a refusal is replaced by the next and removed with new results (GUI-REV4 K-1; Gaps: 신규) |
| 725a8ff | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): K-3 다시 검사 leaves the focus on 검사 시작 (GUI-REV4 K-3; Gaps: 신규) |
| 5b396f4 | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): K-2 a 409 is said next to the export buttons (role=alert, in view) with the list and 다시 검사; the checklist requires the rescan, signed formats wait for it, the preview marks the changed rows (GUI-REV4 K-2; Gaps: 신규) |
| b0d6e77 | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): K-5 the preview's export note is accurate about rows behind a folder that became a link, and a signed JSON export names the scan rows the server excluded (GUI-REV4 K-5; Gaps: 신규) |
| 930f5db | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): K-6 a row with a conclusion and a failed check is not called 판단 불가, and its decisive evidence is no "단서" (GUI-REV4 K-6; Gaps: 신규) |
| 70dc9ce | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): K-7 the export note glues no particle to a format name ("서명된 JSON를"), the service doc's J-7 example matches R19-13 (GUI-REV4 K-7; Gaps: 신규) |
| fa413b3 | 19 | — | Gaps: 신규(스펙 외) | subject | docs(gui): round-5 adversarial review of gui-redesign 70dc9ce (GUI-REV5; Gaps: 신규) |
| c8721b3 | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): L-1 clearing the results also clears the changed-file alert and checklist item — one reset for every path that replaces the results (GUI-REV5 L-1; Gaps: 신규) |
| cb96e6d | 19 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): round-20 verification findings (WP-J; Gaps: 신규) |
| 1b80cc9 | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): Info (a) a changed row with the same SHA-256 and verdict but another status names the status change, not "판단 불가 → 판단 불가" (GUI-REV5 Info a; Gaps: 신규) |
| ae46ac3 | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): L-2 (server) a successful /api/report names the rows it lists as 서명 제외 in X-Deepfake-Lens-Excluded-Count / -Rows headers, for every format (GUI-REV5 L-2; Gaps: 신규) |
| 915d227 | 19 | — | Gaps: 신규(스펙 외) | subject | fix(gui): L-2 L-3 every export failure is said once next to the export buttons, 서명 제외 rows get their own lasting 주의 marker after any format, and an earlier-started download landing after a 409 keeps the changed files (GUI-REV5 L-2, L-3, Info b, Info c; Gaps: 신규) |
| 7eef07e | 20 | R20-1 | Gaps: G30 | subject | fix(report): R20-1 a FIFO, socket or unreadable subfolder in the scanned folder is re-derived as the scan's own skipped / failed row — not "not found", so its report is no longer 409 (R20-1; Gaps: G30) |
| bf79e20 | 20 | R20-2 | Gaps: G1; 신규(스펙 외) | subject | fix(audio): R20-2 every multi-file command and request runs its audio files through one worker per thread, and a worker's numeric libraries use one thread — batch, upload and report re-analysis no longer start a worker per file (R20-2; Gaps: G1, 신규) |
| ff4c703 | 20 | R20-3 | Gaps: G1; 신규(스펙 외) | subject | fix(audio): R20-3 the audio worker protocol is framed — a ready line, the worker's token and the request id in every reply; any other reply stops the worker and fails that file's check, never shifting features to the next file (R20-3; Gaps: G1, 신규) |
| b516bea | 20 | R20-4 | Gaps: G30 | subject | fix(report): R20-4 a posted duplicate row must name another posted, non-duplicate row as duplicate_of, and its bytes are compared with that row's server re-derivation — a self-duplicate or an unposted original is 400 (R20-4; Gaps: G30) |
| c48f766 | 20 | R20-5 | Gaps: G30 | subject | fix(report): R20-5 a row the scan hashed that is now behind a linked folder is a changed row — 409, not moved to excluded_items while the rest is signed (R20-5; Gaps: G30) |
| 37c0d8f | 20 | — | Gaps: 신규(스펙 외) | subject | test(gui): gui_smoke falls back to Playwright's Chromium when Google Chrome is not installed (GUI; Gaps: 신규) |
| e348c38 | 20 | R20-6 | Gaps: G34; 신규(스펙 외) | subject | fix(output): R20-6 a SIGTERM or SIGHUP during an output write removes the temp file — the CLI's cleanup handler removes the output temp files being written, and the comments and docs say exactly what is covered (R20-6; Gaps: G34, 신규) |
| a23a09e | 20 | — | Gaps: 신규(스펙 외) | subject | fix(gui): the A4 preview settles when there is nothing to draw — no results or no chosen exhibit says so ("고른 서증이 없습니다 — …") and is marked ready (GUI; Gaps: 신규) |
| 1ec725c | 20 | R20-7 | Gaps: G31; 신규(스펙 외) | subject | fix(cli): R20-7 tool folders decided by HOME or TMPDIR are never inside a scanned folder, and a single-file command checks its file's folder for the log and heatmap variables — exit 2, or the next temp folder with the notice (R20-7; Gaps: G31, 신규) |
| 5ab8d28 | 20 | R20-8 | Gaps: G30; 신규(스펙 외) | subject | fix(report): R20-8 display_name escapes unassigned code points and noncharacters (category Cn) in Python and in gui.js — names with U+0378, U+FDD0 or U+FFFE are no longer shown as they are (R20-8; Gaps: G30, 신규) |
| 7543812 | 20 | R20-9 | Gaps: 신규(스펙 외) | subject | fix(cli): R20-9 known options before a command name go after it — `--verbose scan X`, `--verbose doctor`, `--verbose corpus build …` and `help scan` are read by that command, not as a scan of a folder named like the command (R20-9; Gaps: 신규) |
| 2b79825 | 20 | R20-10 | Gaps: 신규(스펙 외) | subject | docs(phase0): R20-10 HANDOFF.md describes the round loop and where to read the record state instead of stating it — nothing in it is made false by the round's records-only commit (R20-10; Gaps: 신규) |
| 2d053dd | 20 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on 2b79825, round-20 IDs mapped (WP-J; Gaps: G26, G27, G28) |
| 232c8d2 | 20 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): round-21 verification findings (WP-J; Gaps: 신규) |
| 4eaecc8 | 21 | R21-1 | Gaps: G30 | subject | fix(report): R21-1 no text of a posted row is signed — a walk-limit folder row is excluded, a loop row is signed only when the server finds the loop, a duplicate row carries the scan's own reason (R21-1; Gaps: G30) |
| fd8c3be | 21 | R21-5 | Gaps: G30 | subject | fix(report): R21-5 a posted row path must be the scan's own relative form and each row is posted once — "./a.png", "sub/../a.png", "../<folder>/a.png" and absolute aliases are 400, not signed as more rows of the same file (R21-5; Gaps: G30) |
| e427a87 | 21 | R21-3 | Gaps: G1; 신규(스펙 외) | subject | fix(audio): R21-3 a new audio worker's ready marker is found in its byte stream before any request is sent, within 60 s — output without a newline no longer hangs each file for 600 s, and a worker that never gets ready is a WorkerStartError, not a NativeCrashError (R21-3; Gaps: G1, 신규) |
| 00318fb | 21 | R21-2 | Gaps: G1, G11 | subject | fix(audio): R21-2 the audio worker always runs its numeric libraries on one thread, the scan process's thread variables are in the cache key, and the output-format generation is 9 — cold and warm scans give the same features (R21-2; Gaps: G1, G11) |
| 27bd4c5 | 21 | R21-6 | Gaps: G31; 신규(스펙 외) | subject | fix(cli): R21-6 a single-file command's file folder is examined for the built-in tool folders too — HOME=/E1 classify /E1/a.png is exit 2 instead of writing /E1/.cache/deepfake-lens/logs (R21-6; Gaps: G31, 신규) |
| b4decf3 | 21 | R21-4 | Gaps: G31; 신규(스펙 외) | subject | fix(cli): R21-4 web and api-serve without --folder / --allow-root check their working folder like a named read root — started in the home folder they are exit 2 instead of writing .cache/deepfake-lens into the folder they serve (R21-4; Gaps: G31, 신규) |
| 8285bb0 | 21 | R21-9 | Gaps: 신규(스펙 외) | subject | fix(cli): R21-9 `help <not a command>` is the top-level error with exit 2 and `--verbose help` is help — a typo no longer ends as a success, and help is no longer a scan of a folder named "help" (R21-9; Gaps: 신규) |
| 517f163 | 21 | R21-8 | Gaps: G34; 신규(스펙 외) | subject | fix(output): R21-8 the docs and comments say that only SIGTERM, SIGHUP and SIGINT (SIGBREAK) clean up an output write — SIGQUIT, SIGUSR1 and every other terminating signal leave the temp file and the session folder (R21-8; Gaps: G34, 신규) |
| 3831b93 | 21 | R21-7 | Gaps: 신규(스펙 외) | subject | docs(phase0): R21-7 HANDOFF.md states no test or skip counts — it says where to read them and why the skip count depends on whether the tree has git history; round 21 recorded (R21-7; Gaps: 신규) |
| c43bc11 | 21 | R21-10 | Gaps: G31; 신규(스펙 외) | subject | fix(cli): R21-10 HOME-derived tool folders that would be inside an examined folder are relocated outside every evidence path with one notice — classify ~/x.jpg, cd ~; deepfake-lens web, a HOME inside or a relative HOME run instead of exit 2 (R21-10; Gaps: G31, 신규) |
| 20f73bf | 21 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on c43bc11, round-21 IDs mapped (WP-J; Gaps: G26, G27, G28) |
| 94871f4 | 21 | WP-J | Gaps: 신규(스펙 외) | subject | build(gate): reference gate image and runner live in the repo — scripts/gate/{Dockerfile,Dockerfile.gui,gate.sh} (WP-J; Gaps: 신규) |
| 18a471b | 21 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): round-22 verification findings (WP-J; Gaps: 신규) |
| d9bf1b2 | 22 | R22-1 | Gaps: G7, G12, G30 | subject | fix(report): R22-1 a web report's "기록되지 않은 파일" counts are the server's own — its scan job of the folder, else its own walk of scan_root, else "확인 불가"; a capped, flat or partly posted scan no longer signs "없음" (R22-1; Gaps: G7, G12, G30) |
| a1778a5 | 22 | R22-7 | Gaps: G30 | subject | fix(report): R22-7 the evidence statement without pymupdf is the documented 501 before any re-analysis — it re-analyzed every row and then answered 500 (R22-7; Gaps: G30) |
| ac0f00d | 22 | R22-6 | Gaps: G31 | subject | fix(server): R22-6 a NUL in any path value is a 400 on both servers — report rows / scan_root / heatmap_path, scan, analyze-file, heatmap, preview and api-serve's file endpoints were a 500 (R22-6; Gaps: G31) |
| cb40945 | 22 | R22-10 | Gaps: 신규(스펙 외) | subject | fix(cli): R22-10 words after `help <command>` must name its subcommands — `help corpus build` is that subcommand's help and `help scan extra` is exit 2, not the scan help with exit 0 (R22-10; Gaps: 신규) |
| 958fedf | 22 | R22-4 | Gaps: G30 | subject | fix(report): R22-4 a posted row path is the scan's own listing — a path through a bind-mount loop folder or spelled unlike the listing (letter case, Unicode form) is 400, not one more signed row of the same file (R22-4; Gaps: G30) |
| dbf9095 | 22 | R22-5 | Gaps: G1; 신규(스펙 외) | subject | fix(audio): R22-5 a scan stops starting audio workers after two start failures in a row — later files fail at once with the breaker's reason instead of waiting 60 s each (R22-5; Gaps: G1, 신규) |
| 8746ecf | 22 | R22-2 | Gaps: G31; 신규(스펙 외) | subject | fix(cli): R22-2 on Windows the tool folders move to %ProgramData%\deepfake-lens\<user>, made owner-only and verified like the other candidates — classify %USERPROFILE%\x.jpg and cd ~; web relocate instead of exit 2 (R22-2; Gaps: G31, 신규) |
| e52ac97 | 22 | R22-3 | Gaps: G31; 신규(스펙 외) | subject | fix(server): R22-3 the web GUI's reviews and feedback live in one fixed per-user application-data folder, never in the evidence — saving inside an examined folder or with no absolute home is refused (409), ~/.deepfake-lens records migrate once (R22-3; Gaps: G31, 신규) |
| d021d3c | 22 | R22-8 | Gaps: G30 | subject | fix(report): R22-8 an upload row's name in the signed body is limited and marked — relative, ≤ 255 characters, no "." / ".." / empty component (else 400), name_source "server_upload" only for a name and sha256 this server produced, "client" otherwise (R22-8; Gaps: G30) |
| 022feb1 | 22 | WP-J | Gaps: 신규(스펙 외) | subject | build(gate): gate.sh reads the unittest log as text even when a test's log output holds a NUL byte (WP-J; Gaps: 신규) |
| b161bb2 | 22 | R22-9 | Gaps: G1; 신규(스펙 외) | subject | fix(audio): R22-9 the published numba cache is also kept per Python environment — a new HOME is seeded from it instead of compiling librosa's functions in its first worker; web upload of 20 WAV files 3.15 s against 4.85 s at 2130892 (R22-9; Gaps: G1, 신규) |
| 7756d40 | 22 | R22-11 | Gaps: 신규(스펙 외) | subject | docs(phase0): R22-11 HANDOFF.md §1/§2/§9 state round 21 with R21-10 and round 22 — 22 rounds, R22-1…R22-11 fixed on this branch, the next steps — without anything the records-only commit changes (R22-11; Gaps: 신규) |
| 85ad114 | 22 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on 7756d40, round-22 IDs mapped (WP-J; Gaps: G26, G27, G28) |
| 5801573 | 22 | — | Gaps: 신규(스펙 외) | subject | test(gui): the exhibit-selection tests that mock the PDF writer or check before rendering report the PDF engine present — R22-7's 501 comes first without pymupdf (GUI; Gaps: 신규) |
| 0fa5ac8 | 22 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): round-23 verification findings (WP-J; Gaps: 신규) |
| 5f3990f | 23 | R23-1 | Gaps: G1 | subject | fix(audio): R23-1 a relative audio path is made absolute before it crosses into a child process — scan case, cd case; scan ., classify case/a.wav, batch case and web --folder case run audio_features again, equal to the absolute run (R23-1; Gaps: G1) |
| 6ec2974 | 23 | R23-1 | Gaps: G1 | subject | docs(cli): R23-1 the audio worker is sent the file's absolute path and ffmpeg children absolute input names (R23-1; Gaps: G1) |
| 38911f0 | 23 | R23-2, R23-12 | Gaps: G1, G31 | subject | fix(audio): R23-2 the per-environment numba cache is never made inside the folder of a file a single-file command analyzes — classify / evidence / audio a.wav with the project's .venv next to it wrote .venv/var/deepfake-lens (R23-2, R23-12; Gaps: G1, G31) |
| 3a2e1c4 | 23 | R23-10 | Gaps: 신규(스펙 외) | subject | build(gate): R23-10 the gate image is reproducible from the repository — base image pinned by digest, Debian packages from a snapshot.debian.org state, every pip package at the reference image's exact version from committed lock files (R23-10; Gaps: 신규) |
| c89cb3a | 23 | R23-3, R23-12 | Gaps: G7, G12, G30 | subject | fix(report): R23-3 a scan job the server finished "cancelled" marks the web report cancelled with the job's own count whatever the posted summary says — omitting summary.cancelled no longer signs "없음" (R23-3, R23-12; Gaps: G7, G12, G30) |
| a30a455 | 23 | R23-4 | Gaps: G2, G30 | subject | fix(report): R23-4 every PDF prints a character its font has no glyph for as its \uNNNN escape — Hebrew, Arabic, Thai, Devanagari and emoji names were empty glyphs and different evidence files printed identically (R23-4; Gaps: G2, G30) |
| 494c18d | 23 | R23-5, R23-12 | Gaps: G7, G12, G30 | subject | fix(report): R23-5 archive members the server's own re-analysis of a signed container yields without a signed row are counted as "보고서에 행 없음" — a post that left out bundle.zip::gen.png no longer signs "없음" (R23-5, R23-12; Gaps: G7, G12, G30) |
| ec4b17a | 23 | R23-6, R23-12 | Gaps: G31; 신규(스펙 외) | subject | fix(cli): R23-6 inherit-only ACL entries no longer make the default %ProgramData% parent unsafe, and a parent this tool makes gets a protected ACL — the Windows relocation of R22-2 can happen on a real default install (R23-6, R23-12; Gaps: G31, 신규) |
| 899c104 | 23 | R23-8 | Gaps: G31; 신규(스펙 외) | subject | fix(server): R23-8 neither examiner record is migrated into a file an operator named — DEEPFAKE_LENS_REVIEWS got ~/.deepfake-lens/reviews.json merged in while DEEPFAKE_LENS_FEEDBACK got nothing; the service document said both are copied once (R23-8; Gaps: G31, 신규) |
| 190694d | 23 | R23-7 | Gaps: G31; 신규(스펙 외) | subject | fix(server): R23-7 an examiner record that cannot be written is a Korean 409 (507 when the disk is full) with nothing changed — a review answered 200 "success" from memory with nothing on disk, feedback was a 500 (R23-7; Gaps: G31, 신규) |
| ec1229a | 23 | R23-9 | Gaps: G31 | subject | fix(server): R23-9 a model_path / fusion_profile name the file system cannot look up is a 400 — a 5000-character name was a 500 (ENAMETOOLONG) on both servers, in /api/scan and a report's options (R23-9; Gaps: G31) |
| 617130d | 23 | R23-13 | Gaps: G7; 신규(스펙 외) | subject | fix(server): R23-13 a true/false option with an unknown value is a 400 on both servers — recursive=maybe was read as false and a report signed "아니오" (R23-13; Gaps: G7, 신규) |
| 5b51712 | 23 | R23-11 | Gaps: 신규(스펙 외) | subject | fix(cli): R23-11 `help vendor-weights pin` shows the vendor-weights help with a note — pin is a value of its positional argument, not a subcommand — and `help help` is the top-level help (R23-11; Gaps: 신규) |
| 494daf0 | 23 | WP-J | Gaps: G26; 신규(스펙 외) | subject | test(qa): the QA harness also runs with the audio stack — gate.sh full ends with a --qa-only run in a side venv that sees /venv then /venv-api, and QA-IN-1 / QA-OUT-4 gain relative-path audio cases (WP-J; Gaps: G26, 신규) |
| bbe3532 | 23 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): HANDOFF.md §1/§2/§9 state round 23 — 23 rounds, R23-1…R23-13 fixed on this branch, the round-23 trend and the next steps — without anything the records-only commit changes (WP-J; Gaps: 신규) |
| fbcb53c | 23 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on bbe3532, round-23 IDs mapped (WP-J; Gaps: G26, G27, G28) |
| 6e3bd1e | 23 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): round-24 verification findings (WP-J; Gaps: 신규) |
| 3d48593 | 24 | R24-1 | Gaps: G12, G7, G30 | subject | fix(report): R24-1 a container a cancel left unanalyzed is the status "cancelled" with its own conclusion, and /api/report excludes it as cancelled instead of refusing the report as changed (R24-1; Gaps: G12, G7, G30) |
| 77b1345 | 24 | R24-2 | Gaps: G7; 신규(스펙 외) | subject | fix(native): R24-2 the FFmpeg readers inside the process get an absolute name too — cv2.VideoCapture and librosa.load read a relative "c:v.mp4" as the protocol "c" (R24-2; Gaps: G7, 신규) |
| a9d7eaa | 24 | R24-3 | Gaps: 신규(스펙 외) | subject | fix(native): R24-3 a name too long to make absolute stays "./"-relative and the audio worker is told the working folder — R23-1's absolute names failed in a working folder deeper than PATH_MAX (R24-3; Gaps: 신규) |
| 33f8d8c | 24 | R24-4 | Gaps: G31; 신규(스펙 외) | subject | fix(server): R24-4 reviews.json is read again after a failed read or a change on disk — a repaired file kept being refused until the server restarted (R24-4; Gaps: G31, 신규) |
| 7abbfaa | 24 | R24-5 | Gaps: G12, G1 | subject | fix(cli): R24-5 classify of an unreadable file prints its conclusion with the tool-attribution layer failed — it ended with 「처리 중 예기치 않은 오류(PermissionError)」 and exit 1 (R24-5; Gaps: G12, G1) |
| d3333df | 24 | R24-6 | Gaps: 신규(스펙 외) | subject | fix(gate): R24-6 the old GUI's smoke passes in the repo's gui image — Playwright's Chromium when Google Chrome is absent, and the scan includes subfolders (R24-6; Gaps: 신규) |
| 799ee6a | 24 | R24-7 | Gaps: G2, G30 | subject | fix(report): R24-7 the PDFs draw the case fields through display_name — a literal "\u05e2 corp" printed exactly like "ע corp" (R24-7; Gaps: G2, G30) |
| 97227a0 | 24 | R24-8 | Gaps: G7; 신규(스펙 외) | subject | fix(server): R24-8 /api/scan/stream reads recursive with parse_flag — the R23-13 true/false values, 400 for any other (R24-8; Gaps: G7, 신규) |
| a73da37 | 24 | R24-11 | Gaps: 신규(스펙 외) | subject | fix(cli): R24-11 `help <command> -h` / `--help` is that command's help with exit 0 (R24-11; Gaps: 신규) |
| 07df093 | 24 | R24-9 | Gaps: 신규(스펙 외) | subject | fix(gate): R24-9 qa-main runs with a HOME and XDG folders under /tmp/qa-main and without writing bytecode — it wrote the caller's ~/.cache/deepfake-lens and __pycache__ outside /tmp (R24-9; Gaps: 신규) |
| 4c2dfc0 | 24 | R24-10 | Gaps: 신규(스펙 외) | subject | test(audio): R24-10 the R23-2 end-to-end test gives its project venv every site folder this interpreter imports from — it failed in HANDOFF §5's qa-main side venv (R24-10; Gaps: 신규) |
| 7b99aa7 | 24 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): HANDOFF.md §1/§2/§9 state round 24 — 24 rounds, R24-1…R24-11 fixed on this branch, the round-24 trend and the next steps — without anything the records-only commit changes (WP-J; Gaps: 신규) |
| d2239fe | 24 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on 7b99aa7, round-24 IDs mapped (WP-J; Gaps: G26, G27, G28) |
| bf484c4 | 24 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): round-25 verification findings (WP-J; Gaps: 신규) |
| c464ac3 | 25 | R25-1 | Gaps: G12, G7, G30 | subject | fix(report): R25-1 whether a report row was left unanalyzed by a cancel is the server's scan job's knowledge only — never the posted kind, summary.cancelled or missing member rows (R25-1; Gaps: G12, G7, G30) |
| 50ef8e8 | 25 | R25-2 | Gaps: G12 | subject | fix(core): R25-2 an unreadable WAV, MP4, PDF or archive is the failed row with its real read error — it was 「분석 중 파일 변경」 (R25-2; Gaps: G12) |
| c700e85 | 25 | R25-3 | Gaps: G2, G30 | subject | fix(report): R25-3 the evidence statement's 「작성자 및 일자」 column, the scan folder name and every case field print through one injective PDF form; "\|" prints as typed (R25-3; Gaps: G2, G30) |
| 2d10ee0 | 25 | R25-4 | Gaps: 신규(스펙 외) | subject | fix(native): R25-4 a non-ASCII name in a working folder deeper than PATH_MAX is staged through a name the kernel accepts — its video check failed (R25-4; Gaps: 신규) |
| dfc5889 | 25 | R25-5 | Gaps: 신규(스펙 외) | subject | fix(audio): R25-5 .m4a, .aac and .wma are decoded by ffmpeg before the audio features — librosa 1.0 has no audioread fallback, so they always failed (R25-5; Gaps: 신규) |
| 209ceb2 | 25 | R25-7 | Gaps: G7 | subject | fix(core): R25-7 a reason that names the analyzed file is written "<root>/…" however the scan was started — `cd case; scan .` wrote 'a.wav' (R25-7; Gaps: G7) |
| f2fc514 | 25 | R25-6 | Gaps: 신규(스펙 외) | subject | fix(archive): R25-6 a member name with ":" is refused only in a real absolute, drive, UNC or traversal form — "c:v.png" was called a zip-slip absolute-path member (R25-6; Gaps: 신규) |
| fcf56fe | 25 | R25-8 | Gaps: 신규(스펙 외) | subject | fix(cli): R25-8 `help help -h` and `help -h -h` are the top-level help with exit 0 (R25-8; Gaps: 신규) |
| e359405 | 25 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): HANDOFF.md §1/§2/§9 state round 25 — 25 rounds, R25-1…R25-8 fixed on this branch, the round-25 trend and the next steps; §6 adds the server-held-state rule for row statuses and kinds — without anything the records-only commit changes (WP-J; Gaps: 신규) |
| c5b155c | 25 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): round-25 coordinator-added finding R25-9 — multimodal --av-sync cannot decode a video's audio with librosa 1.0 (WP-J; Gaps: 신규) |
| 8483bf4 | 25 | R25-9 | Gaps: 신규(스펙 외) | subject | fix(audio): R25-9 multimodal --av-sync decodes the video's audio track with ffmpeg and records why the measurement did not run — librosa 1.0 cannot open a video, so it never ran (R25-9; Gaps: 신규) |
| e98b10b | 25 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): HANDOFF.md §1/§2/§9 add the coordinator-added R25-9 to round 25 — fixed on this branch, in the records' ids[] list to add, a round-26 probe — without anything the records-only commit changes (WP-J; Gaps: 신규) |
| 037ade7 | 25 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on e98b10b, round-25 IDs mapped (WP-J; Gaps: G26, G27, G28) |
| da79ae3 | 25 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): round-26 verification findings (WP-J; Gaps: 신규) |
| 3fdb743 | 25 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): round-26 coordinator-promoted residual risks R26-8, R26-9, R26-10 — row-identifier injectivity at scan stage, bounded multi-track av-sync decoding, ffmpeg timeout reasons (WP-J; Gaps: 신규) |
| e48262b | 26 | R26-1 | Gaps: G30 | subject | fix(archive): R26-1 an archive member's chain escapes each level's path, so a literal member "inner.zip::b.png" is never the chain of b.png inside the nested inner.zip — row identity is injective by construction (R26-1; Gaps: G30) |
| 36f0436 | 26 | R26-8 | Gaps: G30 | subject | fix(core): R26-8 every scan result's row identities must be unique and round-trip, or the scan gives no result — checked in core, the CLI, both servers' scans and the report re-analysis (R26-8; Gaps: G30) |
| 7c74bd7 | 26 | R26-2 | Gaps: G7 | subject | fix(scan): R26-2 every reason of a row names a file "<root>/…" from the absolute folder, the relative folder and ".", with 1 or 4 workers — read failures, archive extraction failures, unreadable folders, repr()-escaped names and names the audio worker staged (R26-2; Gaps: G7) |
| d2306f5 | 26 | R26-4 | Gaps: 신규(스펙 외) | subject | fix(archive): R26-4 archive members named "~$보고서.docx", "~WRL0001.tmp" or — on POSIX — "con.png" are analyzed like the same files in a folder; a device name is refused on Windows only, a leading "~" nowhere (R26-4; Gaps: 신규) |
| a95ba10 | 26 | R26-7 | Gaps: 신규(스펙 외) | subject | fix(cli): R26-7 `help help <topic>` is the help of <topic> and `help help help` the top-level help, exit 0 — they were 「허용되지 않은 값 'help'」 with a choice list that has no "help" (R26-7; Gaps: 신규) |
| 89cc44e | 26 | R26-6 | Gaps: G2 | subject | fix(report): R26-6 one PDF prints "\|" one way — file names, member paths, the scan folder name and reasons print it as typed like the case fields; a literal "\\|" still prints with its backslash doubled (R26-6; Gaps: G2) |
| 61c036a | 26 | R26-5 | Gaps: G30 | subject | fix(api): R26-5 a finished /api/scan/stream is a server-held scan job — a report of a cancelled stream's partial result names it by scan_job and is signed with the server's cancel count; the 400 names both job kinds, and the service doc's stale "partial results as done" lines are corrected (R26-5; Gaps: G30) |
| 41bf3f3 | 26 | — | Gaps: 신규(스펙 외) | subject | feat(c2pa): techniques-2026 items 1–2 — the C2PA reader verifies offline against the bundled C2PA trust list and reads the 2.2–2.4 facts; digital source types use the full IPTC vocabulary (techniques-2026 #1–#2; Gaps: 신규) |
| 21a84a5 | 26 | R26-3 | Gaps: 신규(스펙 외) | subject | fix(native): R26-3 on macOS / BSD a UTF-8 non-ASCII name in a working folder deeper than PATH_MAX is handed to the decoders as it is, like an ASCII name — a video over 2 GiB failed with the false reason 「심볼릭 링크를 만들 수 없어」 (R26-3; Gaps: 신규) |
| 2594bb9 | 26 | R26-10 | Gaps: G12 | subject | fix(native): R26-10 an ffmpeg step stopped at its time limit names the step, the file and the limit — it was 「TimeoutExpired: 라이브러리 오류(TimeoutExpired)」 (R26-10; Gaps: G12) |
| 783874e | 26 | — | Gaps: 신규(스펙 외) | subject | feat(watermark): techniques-2026 item 3 — every image is checked for the SDXL pipelines' fixed 48-bit imwatermark message; only an exact match is evidence (techniques-2026 #3; Gaps: 신규) |
| f90a999 | 26 | — | Gaps: 신규(스펙 외) | subject | feat(watermark): techniques-2026 item 4 — Adobe TrustMark Q/P/C decoding; a packet is a fact, and it reaches a conclusion only through a validated C2PA soft binding (techniques-2026 #4; Gaps: 신규) |
| 4ed9ced | 26 | — | Gaps: 신규(스펙 외) | subject | feat(watermark): techniques-2026 item 5 — Meta VideoSeal (images, video) and AudioSeal (audio, a video's audio track) behind the optional torch extra; a generator is named only by a registered message (techniques-2026 #5; Gaps: 신규) |
| 0298c97 | 26 | — | Gaps: 신규(스펙 외) | subject | feat(report): techniques-2026 item 6 — every media and document row states which machine-readable marks were checked (EU AI Act Art. 50 / 인공지능기본법 context), never inferring originality from their absence (techniques-2026 #6; Gaps: 신규) |
| ffb347f | 26 | R26-9 | Gaps: 신규(스펙 외) | subject | fix(audio): R26-9 multimodal --av-sync decodes only what the energy envelope needs — 8 kHz mono of the first 600 s, the span said when the video is longer — and measures every audio track up to 4, reporting the best-correlated one and naming each (R26-9; Gaps: 신규) |
| ba72145 | 26 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): HANDOFF.md §1/§2/§9 state round 26 — 26 rounds, R26-1…R26-7 and the coordinator-promoted R26-8…R26-10 fixed on this branch, the round-26 trend and the next steps; §6 adds that row identifiers must be injective by construction and that a name-acceptance change brings collision fixtures into the report invariant tests — without anything the records-only commit changes (WP-J; Gaps: 신규) |
| 28db773 | 26 | R26-1 | Gaps: G30 | subject | test(report): R26-1 the report invariant fixtures also hold a top-level name with the escape character itself — "col\::x.png", whose row is the R9-1 escape "col\\\:\:x.png" (R26-1; Gaps: G30) |
| d5aea33 | 26 | — | Gaps: 신규(스펙 외) | subject | docs(phase0): P0R-1 gate 0 is declared on a risk basis — invariant tests on three axes plus two clean independent rounds; Low/Cosmetic go to BACKLOG.md, a user-visible false statement still blocks; new verification-round format (P0R-1; Gaps: 신규) |
| e31fd55 | 26 | — | Gaps: 신규(스펙 외) | subject | feat(cli): P0R-4 gate-0 scope freeze — every command outside scan/classify/verify-report/evidence-statement/doctor/web/api-serve/corpus/vendor-weights pin is marked 「실험·비게이트」 in its help line and --help description (P0R-4; Gaps: 신규) |
| af5cac5 | 26 | — | Gaps: 신규(스펙 외) | subject | chore(android): P0R-5 the Android module deepfakeclassifier and its Gradle wrapper move unchanged to extras/android — outside the Python package and the phase-0 gate (P0R-5; Gaps: 신규) |
| aa8d1a4 | 26 | — | Gaps: 신규(스펙 외) | subject | test(invariants): P0R-2 hypothesis property tests of the verdict invariants — conclusions always have their supporting item, lexical never changes a verdict, uncalibrated statistics only withhold, a failed check never concludes outside rule 2, and an injected exception in any check never escapes the scan (P0R-2; Gaps: 신규) |
| 313e701 | 26 | — | Gaps: 신규(스펙 외) | subject | test(paths): P0R-3 one axis-product table — absolute / relative / "." × shallow / deeper-than-PATH_MAX working folder × ASCII / non-ASCII case folder × --workers 1 / 4 × folder / zip — every combination must give the same rows (P0R-3; Gaps: 신규) |
| b589e4d | 26 | — | Gaps: 신규(스펙 외) | subject | feat(text): TB-1 invisible Unicode marks in text and documents — tag strings, variation-selector data, zero-width characters and C2PA 2.3/2.4 text manifests are recorded as facts, never a verdict (TB-1; Gaps: 신규) |
| 5a50808 | 26 | — | Gaps: 신규(스펙 외) | subject | feat(documents): TB-6 document structure facts — PDF revisions, saves after a signature's /ByteRange, tool history and C2PA embedding; OOXML revision, editing time, rsid count and tracked changes, all self-declared facts (TB-6; Gaps: 신규) |
| 82cb31c | 26 | — | Gaps: 신규(스펙 외) | subject | fix(text): TB-1 corner brackets in the package source wrap only interpolated values — the C2PA text magic is a constant, and a docstring no longer quotes 「…」 (TB-1; Gaps: 신규) |
| 66389ae | 26 | — | Gaps: G30, G31 | subject | feat(report): P0R-6 /api/report signs a server-held scan job only — the request names scan_job (+ case fields, format, side, an ordered exhibit_paths selection, unsigned uploads); jobs are bound to the server token, persisted outside the evidence, never evicted while running (P0R-6; Gaps: G30, G31) |
| 1ab27cf | 26 | — | Gaps: 신규(스펙 외) | subject | feat(platform): TB-4 platform and messenger traces — sourced JPEG recompression tables, messenger / screenshot / recording file-name formats, AMR and Opus headers and the effective audio bandwidth are facts that only add the "metadata may have been stripped" limitation (TB-4; Gaps: 신규) |
| 267b463 | 26 | — | Gaps: 신규(스펙 외) | subject | fix(platform): TB-4 the scan-cache key carries the file-name formats a name matches, not the name — a renamed copy still replays its row (QA-IN-4), and the new tests' skip reasons are documented (TB-4; Gaps: 신규) |
| 66389ae | 26 | — | Gaps: 신규(스펙 외) | subject | feat(watermark): TB-2 Resemble AI PerTh implicit watermark (Chatterbox's default) on audio and video audio tracks — presence is a fact, the registered Chatterbox entry is a moderate 1-bit item that never concludes (TB-2; Gaps: 신규) |
| 53c5aa7 | 26 | — | Gaps: 신규(스펙 외) | subject | feat(device): TB-3 device AI-edit labels — the Samsung SEFT trailer's entry names are facts (AI-related candidates marked unverified) and Google AI credit labels are moderate self-declarations (TB-3; Gaps: 신규) |
| 020917e | 26 | — | Gaps: 신규(스펙 외) | subject | feat(metadata): P0R-22 container/bitstream metadata layer (threat G2) — MP4/MOV boxes, H.264/HEVC SEI user data, ID3v2, RIFF INFO, FLAC comments and the GB 45438 AIGC label as facts, a registered generator declaration as a self-declared item; P0R-23 the video row no longer claims a container check that did not exist (P0R-22, P0R-23; Gaps: 신규) |
| f1b3b54 | 26 | — | Gaps: 신규(스펙 외) | subject | fix(c2pa): P0R-20 a C2PA hard binding whose hash exclusions drop asset content fails the c2pa check whatever the SDK says (the 2026-10-02 two-range attack the SDK reports Trusted); P0R-21 the frozen Interim Trust List is a separate bundle, an ITL-only signer is labelled and moderate, revocation is stated as unchecked (P0R-20, P0R-21; Gaps: 신규) |
| c5358a1 | 26 | — | Gaps: 신규(스펙 외) | subject | fix(decision): P0R-10 rule 2 no longer outvotes strong authenticity — strong reproducible-record evidence in both directions is a conflict, undetermined (P0R-10; Gaps: 신규) |
| 28f8fc3 | 26 | — | Gaps: 신규(스펙 외) | subject | fix(metadata): P0R-11 a generator brand word is moderate and counts only in generator-specific fields (Software, parameters, prompt/workflow, xmp:CreatorTool) — never exif.Model, LensModel, Credit or dc:creator (P0R-11; Gaps: 신규) |
| 6f1bf13 | 26 | — | Gaps: 신규(스펙 외) | subject | feat(result): P0R-12 every manipulation or authenticity conclusion says why it could be wrong (result.conclusion_caveats, 「이 결론이 틀릴 수 있는 이유」), and one resting on self-declared metadata says 「자기 선언 메타데이터 — 심거나 지울 수 있음」 in the verdict and the evidence statement (P0R-12; Gaps: 신규) |
| 8334b47 | 26 | — | Gaps: 신규(스펙 외) | subject | fix(wording): P0R-13 「결정적 근거」 is 「재현 가능한 기록 근거」 in the UI, CLI, reports and the GUI CSV header, and no 「감정 근거로 사용 가능」 label is shown next to 판단 불가 (P0R-13; Gaps: 신규) |
| 25c7680 | 26 | — | Gaps: 신규(스펙 외) | subject | feat(screen): TB-5 screenshot facts — the iOS/macOS "Screenshot" user comment and native phone/tablet screen sizes are neutral facts, and a trusted camera C2PA manifest adds the recapture limitation (TB-5; Gaps: 신규) |
| 8b48c5b | 26 | — | Gaps: 신규(스펙 외) | subject | feat(generators): TB-7 a versioned, sourced generator / service string table — the phase-0 markers move unchanged into models/generator-strings.json with sourced additions, and a C2PA signer is named as a neutral fact; no strength rule changes (TB-7; Gaps: 신규) |
| 6315188 | 26 | — | Gaps: G30 | subject | fix(signing): P0R-14 every signed body carries its generation time and the model profiles that ran (executed_models: profile, pin, threshold, calibration id) and never the host temp folder; P0R-15 signing refuses a key shorter than 32 bytes (verifying does not) (P0R-14, P0R-15; Gaps: G30) |
| 3852f12 | 26 | — | Gaps: 신규(스펙 외) | subject | test(output): P0R-0 the "no recorded generation was rewritten" check reads the first-parent history — the merged techniques-2026 branch recorded its own generations 13–17 (P0R-0; Gaps: 신규) |
| fdf6e54 | 26 | — | Gaps: 신규(스펙 외) | subject | docs(phase0): P0R-24 HANDOFF.md §1/§2/§4/§7/§9 describe the restructured phase 0 — the five P0R parts, the next steps (records, a round-27 verification in the new format and what it must probe), the :tech gate image, the 2026-10-17 user decisions as agreed decisions, BACKLOG.md in the file list (P0R-24; Gaps: 신규) |
| 6663a59 | 26 | — | Gaps: 신규(스펙 외) | subject | docs(phase0): P0R-25 BACKLOG.md lists what the restructure left open — exclusion-attack fixtures beyond PNG, the ITL allow list, an offline CRL, the report's side, job-store fallbacks, the axis table's cost, host-only test differences (P0R-25; Gaps: 신규) |
| 94a7858 | 26 | — | Gaps: 신규(스펙 외) | subject | test(fixtures): P0R-26 the new invariant, axis-table and caveat tests write their PNGs without Pillow (tests/png_fixture.write_png) — they ran only where Pillow is installed (P0R-26; Gaps: 신규) |
| 6a2eec5 | 26 | — | Gaps: 신규(스펙 외) | subject | feat(watermark): TB-8 the open pipelines' default fixed watermarks verified in source — FLUX.1 reference (46 bits) and the SD 1 / SD 2 official scripts join SDXL in the registry; FLUX.2, FLUX.3 and the other pipelines: 확인: 기본 워터마크 없음 (TB-8; Gaps: 신규) |
| b83ddd8 | 26 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase1): legal-review bundle C0 — every legal question for phase-1 corpora, licences, service terms and report wording in one document, to be reviewed once (user decision 2026-10-17) (WP-J; Gaps: 신규) |
| 7d7c78a | 26 | — | Gaps: 신규(스펙 외) | subject | fix(watermark): TB-2 PerTh skips tone-dominated signals outside its measured (speech) domain — a 440 Hz sine scored 0.99 — and its texts avoid English runs (TB-2; Gaps: 신규) |
| 9f51400 | 26 | — | Gaps: 신규(스펙 외) | subject | fix(platform): TB-4 table texts shown in rows carry no English runs — platform meanings, device-label meanings, generator rule labels and C2PA service names reworded, screen-size model lists quoted; a test keeps the TB tables Korean (TB-3, TB-4, TB-5, TB-7; Gaps: 신규) |
| 4d505cf | 26 | — | Gaps: 신규(스펙 외) | subject | perf(platform): TB-4 the bandwidth measurement reads WAV/FLAC/OGG in-process with soundfile instead of spawning ffmpeg — an audio scan was 30% slower than at 0298c97, now 7% (TB-4; Gaps: 신규) |
| 8178e77 | 26 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on b83ddd8, round-26 IDs mapped; techniques-2026 merge and phase-0 restructure (P0R) included (WP-J; Gaps: G26, G27, G28) |
| f7dbe43 | 26 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): round-27 verification findings — first round in the risk-based format: no Med+ on the verdict/signature/evidence axes, one false statement (R27-2) and one performance Med (R27-5) to fix (WP-J; Gaps: 신규) |
| 0fc5c71 | 27 | R27-2 | Gaps: G30 | subject | fix(report): R27-2 the report labels the options as the scan job's own — 「검사 옵션(서버가 이 검사 작업을 실행한 옵션)」 — not 「재분석 옵션(서버가 이 옵션으로 모든 행을 다시 분석)」, which stopped being true when P0R-6 made reports sign the job's rows without re-analysis; R27-1/3/4 go to BACKLOG (R27-2; Gaps: G30) |
| d46266d | 27 | — | Gaps: 신규(스펙 외) | subject | fix(gui): the GUI's check labels include the merged watermark, online-only and container-metadata checks, and the scan-status progress is typed for mypy (GUI; Gaps: 신규) |
| 3622dc6 | 27 | R27-5 | Gaps: 신규(스펙 외) | subject | perf(scan): R27-5 image scans and HTML reports back within 25% of 2130892 — the C2PA trust bundles are read and sha256-checked once per file version (not three times per file), the SDK Context is built once per thread, absent watermark dependencies are remembered, cv2 is located rather than imported below the SDXL size floor, and three hot pure-Python loops (header-text markers, error translation, display_name) take an exact fast path; scan JSON byte-identical (R27-5; Gaps: 신규) |
| 4420e63 | 27 | R27-5 | Gaps: 신규(스펙 외) | subject | perf(scan): R27-5 the techniques-2026b layers keep image scans within 25% of 2130892 — device-label and screen-trait tables loaded once per file version, the metadata layer's prefix, EXIF UserComment and decoded PNG pixels handed to the layers that re-read them, absent torch/soundfile remembered, and a row's to_json no longer converts the fields it replaces; output byte-identical (R27-5; Gaps: 신규) |
| 96b24f4 | 27 | R27-5 | Gaps: 신규(스펙 외) | subject | perf(text): R27-5 text_marks.scan_text jumps to the characters it acts on (tag characters, variation selectors, the zero-width set) instead of walking every character in Python; result identical (R27-5; Gaps: 신규) |
| c4e00c4 | 27 | R27-5 | Gaps: 신규(스펙 외) | subject | docs(phase0): R27-5 the performance budget excludes the measured cost of a newly added check (user decision 2026-10-17); documents at +33%/+53% from the C2PA SDK reading PDFs go to BACKLOG as BL-23 (R27-5; Gaps: 신규) |
| 7243344 | 27 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV6 M-1 F-1 F-2 a conclusion resting on self-declared metadata says so in the row, the panel heading and the headline; no text says the server re-analyzes the files (P0R-6 re-hashes the job's files); upload rows only give the real reason the evidence statement cannot be made (GUI-REV6 M-1, F-1, F-2; Gaps: 신규) |
| 1ce2b05 | 27 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on c4e00c4, round-27 IDs mapped; techniques-2026b merge included (WP-J; Gaps: G26, G27, G28) |
| 3bf3489 | 27 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV6 M-1 the deciding-evidence rule compares the direction by its Korean label and the row reason joins the self-declared mark without a template — test_korean_output and test_display_names held the GUI to both rules (GUI-REV6 M-1; Gaps: 신규) |
| 4c7cef5 | 27 | — | Gaps: 신규(스펙 외) | subject | docs(phase0): GUI review 6 Low/Cosmetic items go to BACKLOG as BL-24..BL-32 (GUI6; Gaps: 신규) |
| 2a08d8e | 27 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on 4c7cef5; gui-redesign merge (review rounds 1–6) and GUI6 backlog included, no new IDs (WP-J; Gaps: G26, G27, G28) |
| c3ff620 | 27 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): round-28 verification findings — second round in the risk-based format: no Med+ on the verdict/signature/evidence axes, one false statement (R28-1) to fix, R28-2..R28-5 backlog candidates (WP-J; Gaps: 신규) |
| cb7283d | 28 | R28-1 | Gaps: 신규(스펙 외) | subject | fix(upload): R28-1 a single upload is analyzed under the client's own base name in a fresh temp folder, so the file-name format check (and every other check that reads the name) checks the uploaded name like a folder scan or zip upload does, instead of reporting 「등록된 이름 형식 9개와 일치 없음 — 무정보」 for the server's temp file name (R28-1; Gaps: 신규) |
| bd798fb | 28 | R28-2, R28-3, R28-4, R28-5 | Gaps: 신규(스펙 외) | subject | docs(phase0): R28-2..R28-5 round-28 backlog and handoff — :tech2 (the only image with the PerTh weight) is a required gate image beside :latest/:tech/:gui (R28-3), BL-23 carries the round-28 document measurement (R28-2), BL-33 Opus encoder string not self_declared (R28-4), BL-34 scan-status done before the job file is written (R28-5), BL-22 adds the FLUX.1/SD 1/SD 2 registered messages (R28-2, R28-3, R28-4, R28-5; Gaps: 신규) |
| 6a3ad1f | 28 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on bd798fb; round-28 IDs R28-1..R28-5 mapped (R28-1 fixed, R28-2..R28-5 to HANDOFF/BACKLOG) (WP-J; Gaps: G26, G27, G28) |
| e3ce6e1 | 28 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): round-29 verification findings — third round in the risk-based format: no Med+ on the verdict/signature/evidence axes, one false statement (R29-1) to fix, R29-2 backlog candidate (WP-J; Gaps: 신규) |
| c6fd8f3 | 29 | R29-1 | Gaps: 신규(스펙 외) | subject | fix(upload): R29-1 the web quick check (/api/check) analyzes a 0-byte uploaded file like /api/analyze-upload and a folder scan do, instead of refusing it with 「업로드된 파일이 없습니다」 (400) — every upload path selects file parts by one rule (a file name and a payload, empty or not) (R29-1; Gaps: 신규) |
| d543820 | 29 | R29-2 | Gaps: 신규(스펙 외) | subject | fix(upload): R29-2 an archive upload whose client name cannot be a file name here (300 characters, 270 UTF-8 bytes on ext4, NUL) is analyzed under the stand-in upload.zip like a single upload, its members as rows under the client's name, instead of one failed row 「OSError: … 경로가 너무 깁니다」 on /api/analyze-upload and a 500 on the web /api/check; an analysis that raises on /api/check is the same failed row as on /api/analyze-upload, never a 500 (R29-2; Gaps: 신규) |
| bdc1438 | 29 | R29-3 | Gaps: 신규(스펙 외) | subject | fix(upload): R29-3 an upload is routed as an archive or a single file by the name its bytes are written under (the client's base name), so "x.zip\" gives the folder scan's member rows and container row instead of only the container row (R29-3; Gaps: 신규) |
| 8cbdeb3 | 29 | R29-4 | Gaps: 신규(스펙 외) | subject | fix(upload): R29-4 the upload cap (MAX_UPLOAD_FILES = 20) counts uploaded files, not rows, and every file over it is its own skipped row 「파일 수 상한(20개) 초과 — 이 요청의 파일 N개 중 앞의 20개만 분석함」, instead of an archive's member rows using up the cap so that the next file of a two-file upload was reported 「파일 수 상한(20) 초과」 and later files vanished (R29-4; Gaps: 신규) |
| 8b778c4 | 29 | R29-5 | Gaps: 신규(스펙 외) | subject | fix(upload): R29-5 the web quick check (/api/check) refuses a request with more than one file part — 400 「빠른 검사는 파일 1개만 받습니다(받은 파일 N개) — …」 — instead of analyzing the first file and silently dropping the others (R29-5; Gaps: 신규) |
| 43521d8 | 29 | R29-6 | Gaps: 신규(스펙 외) | subject | fix(upload): R29-6 the web comparison (/api/compare) counts a 0-byte file as a file and takes exactly two (else 400 「비교할 파일 2개가 필요합니다(받은 파일 N개)」), and writes each file like every upload so a client name this system cannot create no longer fails with a 500 — it said 「비교할 파일 2개가 필요합니다」 for two files when one was empty and silently ignored a third (R29-6; Gaps: 신규) |
| 6dfd3fa | 29 | R29-1, R29-2, R29-3, R29-4, R29-5, R29-6 | Gaps: 신규(스펙 외) | subject | docs(phase0): R29-1..R29-6 round-29 handoff — the axis-product line of §8.3 names the upload entry-point equivalence table (UploadEntryPointEquivalenceTest) and asks a change adding an upload entry point or input class to add it there; §9 marks R29-1/R29-2 fixed and R29-3..R29-6 found by the sweep (R29-1, R29-2, R29-3, R29-4, R29-5, R29-6; Gaps: 신규) |
| 0cfbd03 | 29 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on 6dfd3fa; round-29 IDs R29-1..R29-6 mapped (R29-1, R29-2 fixed from verify_round29.md; R29-3..R29-6 found and fixed by the upload entry-point sweep) (WP-J; Gaps: G26, G27, G28) |
| aad5112 | 29 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): round-30 verification findings — fourth round in the risk-based format: no Med+ on the verdict/signature/evidence axes, five false statements (R30-1..R30-5, all Low) to fix, three backlog candidates (WP-J; Gaps: 신규) |
| 210cf09 | 30 | R30-1, R30-2, R30-3, R30-5 | Gaps: 신규(스펙 외) | subject | fix(upload): R30-1 R30-2 R30-3 R30-5 every upload path parses multipart/form-data with one strict parser (form_data.py) instead of the e-mail parser — a part's bytes are taken verbatim whatever its Content-Type (a browser's .eml as message/rfc822 no longer vanishes, a .mht as multipart/related is no longer re-serialized under a wrong sha256), a quoted filename keeps its backslashes as browsers send them (a\b.png was ab.png, a non-KakaoTalk name read 「이름 형식 일치」), and no/empty boundary or a boundary the body never uses get their own 400 instead of 「업로드 본문이 잘렸습니다」 (R30-1, R30-2, R30-3, R30-5; Gaps: 신규) |
| 548a168 | 30 | R30-4 | Gaps: 신규(스펙 외) | subject | fix(compare): R30-4 a comparison that measured nothing shows no number — a side whose audio features cannot be extracted (a 0-byte WAV, a PNG named .wav, no librosa) or that has under 8 characters of text gives raw_score null, distance null and reference_band "unavailable" with the reason naming the side (파일 A/파일 B/두 파일 모두), and the GUI shows 「없음」 and 「거리 없음 — 측정하지 않음」 — it showed 「0」 and 「거리 1」 for audio and a distance computed from an empty text (R30-4; Gaps: 신규) |
| 5bed165 | 30 | R30-1, R30-2, R30-3, R30-5 | Gaps: 신규(스펙 외) | subject | docs(phase0): round-30 backlog and service doc — the upload paragraph says an analysis that raises is a `failed` row and only a failure outside a file's analysis is a 500 (E20 says the same; both said "analysis raised → 500"), BACKLOG gains 「30차 검증에서 넘긴 항목」: BL-35 the ~9.5x upload peak memory, closed by the R30 parser (582/594 MB for a 256 MiB body, was 2489/2482 MB), BL-36 a literal %22 in a name now reads as '"' (R30-1, R30-2, R30-3, R30-5; Gaps: 신규) |
| 454fd84 | 30 | R30-1, R30-2, R30-3, R30-4, R30-5 | Gaps: 신규(스펙 외) | subject | docs(phase0): R30-1..R30-5 round-30 handoff — the axis-product line of §8.3 lists the equivalence table's round-30 axes (part Content-Type × browser-encoded names × boundary refusals, and the unmeasured /api/compare pairs); §9 marks R30-1..R30-5 fixed (R30-1, R30-2, R30-3, R30-4, R30-5; Gaps: 신규) |
| c29cbb5 | 30 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on 454fd84; round-30 IDs R30-1..R30-5 mapped (R30-1, R30-2, R30-3, R30-5 fixed by the multipart/form-data parser; R30-4 by the unmeasured comparison) (WP-J; Gaps: G26, G27, G28) |
| cce03f2 | 30 | WP-J | Gaps: 신규(스펙 외) | subject | docs(phase0): round-31 verification findings — fifth round in the risk-based format: no Med+ on the verdict/signature/evidence axes, four false statements (R31-1, R31-2, R31-3, R31-5, all Low) to fix, R31-4 and four backlog candidates (WP-J; Gaps: 신규) |
| 1286f70 | 31 | R31-1, R31-2, R31-3, R31-4, R31-6, R31-9 | Gaps: 신규(스펙 외) | subject | fix(upload): R31-1 R31-2 R31-3 R31-4 R31-6 R31-9 the multipart/form-data parser takes a bare LF or CR as a line end as the e-mail parser did (an LF-only, mixed or LF-preamble body was 「boundary와 다릅니다」/「잘렸습니다」/「업로드된 파일이 없습니다」), reads the media type and `Boundary=` case-insensitively (`Multipart/Form-Data` was 「multipart/form-data 업로드가 필요합니다」), lets an empty filename* fall back to filename, skips a filename* charset that raises (`undefined` was a 500), caps a request at 1000 parts before any row, and ends an unquoted name at a bare LF (R31-1, R31-2, R31-3, R31-4, R31-6, R31-9; Gaps: 신규) |
| 3127f14 | 31 | R31-5 | Gaps: 신규(스펙 외) | subject | fix(gui): R31-5 the quick check's archive toast counts the members it analyzed — 「압축 해제 — 구성 파일 N개 분석됨」 from member rows whose status is analyzed (plus 「M개 분석 못 함」), 「압축 파일을 열지 못했습니다」 when the archive check failed or was skipped, 「압축 파일에서 분석한 구성 파일이 없습니다」 when it opened with nothing analyzed — it said 「압축 해제 — 1개 파일 분석됨」 for a corrupt or empty zip and counted the container row (R31-5; Gaps: 신규) |
| e3e7c0f | 31 | R31-7, R31-8 | Gaps: 신규(스펙 외) | subject | fix(compare): R31-7 R31-8 a comparison below its floor shows no number — a side shorter than 1 s or with mean frame RMS under 1e-4 (silence) gives raw_score null, distance null and 「비교 하한에 못 미쳐」 naming the side and its length or RMS (a 4-sample WAV scored 47, silence against silence 100 with distance 0), and the 8-character text floor counts visible characters only (100 zero-width spaces or NULs were 「표본 100자」 and compared) (R31-7, R31-8; Gaps: 신규) |
| 94ca8ea | 31 | R31-1, R31-7 | Gaps: 신규(스펙 외) | subject | docs(phase0): R31-7 R31-1 round-31 backlog — BL-37 the speaker-comparison floor leaves a DC-offset WAV compared (RMS above the floor; a zero-crossing floor would skip real recordings with a large offset), BL-38 the bare-LF line end's ambiguities (a full `\n--boundary` inside a CRLF body's data now delimits; a CR ending a part of an LF-delimited body is read as the delimiter's) (R31-1, R31-7; Gaps: 신규) |
| 8514c9d | 31 | R31-1, R31-2, R31-3, R31-4, R31-5, R31-6, R31-7, R31-8, R31-9 | Gaps: 신규(스펙 외) | subject | docs(phase0): R31-1..R31-9 round-31 handoff — the axis-product line of §8.3 lists the equivalence table's round-31 axes (line endings and their truncations × media-type case × filename* fallbacks × the 1000-part cap); §9 marks R31-1..R31-5 fixed and names R31-6..R31-9, the backlog candidates fixed this round, and BL-37/BL-38 (R31-1, R31-2, R31-3, R31-4, R31-5, R31-6, R31-7, R31-8, R31-9; Gaps: 신규) |
| 8202f4e | 31 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on 8514c9d; round-31 IDs R31-1..R31-9 mapped (R31-1, R31-2, R31-3, R31-4, R31-6, R31-9 fixed by the multipart/form-data parser; R31-5 by the quick-check archive toast; R31-7, R31-8 by the comparison floors) (WP-J; Gaps: G26, G27, G28) |
| c15ed6e | 31 | R31 | Gaps: 신규(스펙 외) | subject | docs(phase0): R31 gate 0's false-statement rule covers verdicts, evidence identity, signed outputs and the GUI (and the requests browsers and the GUI send); a wrong refusal reason for a hand-made non-browser API request is Low and goes to BACKLOG unless it causes a 500, a stop, an evidence write or a wrong verdict or signature (user decision 2026-10-19) (R31; Gaps: 신규) |
| 17b3a15 | 31 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on c15ed6e; gate-0 false-statement scope decision (HANDOFF §8.1 b) included — its subject ID R31 (round 31's decision, not a round-2 R-ID) mapped in ids[] with a note, no new verification IDs (WP-J; Gaps: G26, G27, G28) |
| 1cb1e82 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV7 F-1 the skip link focuses <main> without changing the hash, and a hash that names no screen leaves the screen as it is (F-1; Gaps: 신규) |
| ce39b12 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV7 F-2 a quick check of an archive asks before its rows replace a folder scan's rows, and the hint says they are replaced (F-2; Gaps: 신규) |
| cd3066a | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV7 F-3 the server's folder fills the folder box only when it is empty and not focused, and a 「허용되지 않은 경로」 refusal names that folder (F-3; Gaps: 신규) |
| 0bafe83 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV7 F-4 an earlier scan's error box is removed once a new scan's results are shown, and marking it 이전 검사 no longer rewrites its title in the live region (F-4; Gaps: 신규) |
| 39675ec | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV7 F-5 every new scan names the results button 「결과 보기」 again after a cancelled scan named it 「부분 결과 보기」 (F-5; Gaps: 신규) |
| 9d397a7 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV7 F-6 the scan form ignores a second submit while 「검사 시작」 is marked busy, before /api/scan has answered (F-6; Gaps: 신규) |
| 1b0e6f7 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV7 F-7 「최대 파일 수」 allows 1..2000 like the server, a value outside that is said next to the box before the scan, and the over-cap banner advice depends on the value used (F-7; Gaps: 신규) |
| 3148a8a | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV7 F-8 in forced colors (Windows contrast themes) the current row, pressed filter and star, progress fill, changed row, drop target and active studio mode get a border, outline or underline (F-8; Gaps: 신규) |
| a7941b2 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV7 F-9 typing a case field no longer rewrites the live export note, which is written only when its text changes, and waits 500 ms before the preview request (F-9; Gaps: 신규) |
| d27efb0 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV7 F-10 a file dragged over or dropped outside the drop zone is refused by the page instead of being opened by the browser (F-10; Gaps: 신규) |
| 03eabde | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV7 F-11 the verdict filter cells draw their keyboard focus ring inside the cell, where .strip does not clip it (F-11; Gaps: 신규) |
| 33f8193 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV7 F-12 an unmeasured comparison leads with 「참고 신호 — 미측정」 and gives its raw score only in a sentence, and layer notices show 「결론은 폴더 검사를 사용하십시오」 without backticks (F-12; Gaps: 신규) |
| 09072b7 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV7 F-13 the report screen says the exhibits are numbered in the ledger's 결론순, not "in the order chosen" (F-13; Gaps: 신규) |
| cd8c1b6 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV7 F-14 F-15 the ledger row folds a check reason's repeated phrases (display only), the detail panel shows install commands without backticks, and an archive that was not opened has no "컨테이너 행은 … 요약" note (F-14, F-15; Gaps: 신규) |
| ad64921 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(scripts): GUI-REV7 W-4 qa_compare_results.py (W-4; Gaps: 신규) |
| 06bf90e | 31 | — | Gaps: 신규(스펙 외) | subject | docs(qa): GUI-REV7 W-1..W-10 W-13 QA-SYS-4 procedure rewritten as a runnable Windows runbook — 64-char key, output folder made first, process-scope execution policy, wheel-only reference JSON compared with qa_compare_results.py, PDF as a separate pymupdf step (W-1, W-2, W-3, W-4, W-5, W-6, W-7, W-8, W-9, W-10, W-13; Gaps: 신규) |
| 9ed7e70 | 31 | — | Gaps: 신규(스펙 외) | subject | docs(phase0): GUI-REV7 backlog BL-39..BL-56 — GUI review 7's Low and Cosmetic findings F-16..F-31 and Windows-procedure items W-11, W-12 (F-16, F-17, F-18, F-19, F-20, F-21, F-22, F-23, F-24, F-25, F-26, F-27, F-28, F-29, F-30, F-31, W-11, W-12; Gaps: 신규) |
| 83be3d1 | 31 | — | Gaps: 신규(스펙 외) | subject | docs(gui): GUI-REV7 UI/UX·접근성 점검과 적대적 리뷰 of 17b3a15 (F-1..F-31, W-1..W-13) and its implementation plan (GUI-REV7; Gaps: 신규) |
| d020ba2 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV7 F-9 leaving a case text field writes only the export note — a full render on change rebuilt the checklist under a click, which lost 「이 폴더의 사건이 맞음」 in gate.sh gui (F-9; Gaps: 신규) |
| e40d579 | 31 | — | Gaps: 신규(스펙 외) | subject | docs(phase0): GUI-REV7 backlog BL-57..BL-60 — four GUI edge cases found while implementing F-2, F-9 and F-15 (GUI-REV7; Gaps: 신규) |
| 149e4d0 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV7 corner brackets in four gui.js comments and the outside-drop toast become plain quotes — test_korean_output.SourceQuotesTest allows 「」 only around interpolated values (GUI-REV7; Gaps: 신규) |
| 6201e47 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(desktop): DESK-1 `deepfake-lens desktop` — the web app for a double-click user: a signing key file made once in the user data folder, a free port (or only the browser when this tool already runs), the browser opened, and desktop-only routes /api/roots, /api/pick-folder (OS folder dialog registers a read root), /api/shutdown (409 while a scan runs), /api/heartbeat (idle exit) (DESK-1; Gaps: 신규) |
| 574ccea | 31 | — | Gaps: 신규(스펙 외) | subject | test(desktop): DESK-1 the dialog helper command lines per platform, and mypy-clean annotations in test_desktop_mode (DESK-1; Gaps: 신규) |
| 6e7bc17 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(desktop): DESK-1 the folder-dialog helper runs as a tracked child (shutdown.run_child) and the key-file write is listed as the one O_EXCL writer — the child and writer meta-tests name desktop.py (DESK-1; Gaps: 신규) |
| c664013 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(scripts): DESK-8 build_desktop_bundle.py — `--platform windows` makes DeepfakeLens-win64/ and its zip from python.org's embeddable CPython 3.12.10 (sha256 pinned) plus a pinned, dependency-checked wheel set; `--platform macos` makes DeepfakeLens-mac/ with an offline first-run venv (DESK-8; Gaps: 신규) |
| 595bbd9 | 31 | — | Gaps: 신규(스펙 외) | subject | docs: DESK-9 desktop mode — service doc 「데스크톱 모드」, README 「일반 사용자(설치 없이)」, QA-SYS-4 bundle path, HANDOFF §2/§9 (DESK-9; Gaps: 신규) |
| 35f35a5 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): GUI-REV7 the outside-drop toast quotes its block name with double quotes — 149e4d0 put ASCII apostrophes inside a single-quoted string, so gui.js did not parse and the GUI was dead (GUI-REV7; Gaps: 신규) |
| 679904e | 31 | — | Gaps: 신규(스펙 외) | subject | feat(gui): DESK-2 desktop mode top bar — "이 PC에서 실행 중" and 종료: a confirm, POST /api/shutdown, then a page that says "종료되었습니다. 이 탭을 닫으십시오." (role=alert) with every control disabled; a 409 refusal goes to the error region with the server's text (DESK-2; Gaps: 신규) |
| 9c2ecd3 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(gui): DESK-3 "검사할 폴더 고르기" above the folder box (desktop only) — POST /api/pick-folder opens the OS dialog, the chosen folder fills the box and the scan starts; "지금 검사할 수 있는 폴더" (stats roots, five shown and "외 n개") under the box, refreshed after a pick; the 허용되지 않은 경로 refusal names those folders (DESK-3; Gaps: 신규) |
| e290b3f | 31 | — | Gaps: 신규(스펙 외) | subject | feat(gui): DESK-4 the upload block is titled "파일 올려서 검사(서명 제외 — 감정서 불가)" with "올릴 파일 고르기" / "올릴 폴더 고르기", and is a disclosure folded in the desktop mode (DESK-4; Gaps: 신규) |
| f95fccc | 31 | — | Gaps: 신규(스펙 외) | subject | feat(gui): DESK-5 heartbeat every 30 s in the desktop mode — two failures in a row put a lasting "서버와 연결이 끊겼습니다 — 바탕 화면의 Deepfake Lens를 다시 실행하십시오" box in the error region (not a scan's box), the next success removes it; stopped after 종료 (DESK-5; Gaps: 신규) |
| b8d7847 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(gui): DESK-6 "PDF 가능/불가" and "서명 자동/서명 없음" chips next to the version, and one checklist line on the signing key's source — "서명 키: 이 PC의 사용자 폴더(report-key) — 다른 PC에서는 검증되지 않음" for the key file, 환경 변수 for env, 주의 without a key (DESK-6; Gaps: 신규) |
| af3ffee | 31 | — | Gaps: 신규(스펙 외) | subject | feat(gui): DESK-7 three first-use steps at the top of the scan screen while there are no results — 1 폴더 고르기, 2 결과 확인, 3 감정서 (DESK-7; Gaps: 신규) |
| 1b5c2ed | 31 | — | Gaps: 신규(스펙 외) | subject | test(gui): DESK-2..7 gui_smoke desktop step — `deepfake-lens desktop` on --port + 1 with the folder dialog stubbed: dialog 501/cancel/chosen -> scan -> roots list and the refusal naming it, the key-source checklist line, the lost-connection box, 종료 refused (409) then the shutdown page (DESK-2; Gaps: 신규) |
| 02bd8cb | 31 | — | Gaps: 신규(스펙 외) | subject | test(desktop): DESK-1 the documented-endpoints test reads the 데스크톱 모드 table apart — its four routes are 404 on the plain web server and 200 on a desktop server; the shutdown test uses a job store of its own; QA-SYS-4 names the DESK-4 upload labels (DESK-1; Gaps: 신규) |
| 6fdada4 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(desktop): DESK-1 the registries and meta-tests know the desktop command — its --folder is a declared read-only input (cli_inputs, SERVER_COMMANDS), desktop.py is an allowed loopback-only network file (security self-check) and an exempt run_child site (the dialog helper has no file argument), `desktop` is an excluded standalone command, and the gui.js parse test's skip reason is documented (DESK-1; Gaps: 신규) |
| 2faf11a | 31 | — | Gaps: 신규(스펙 외) | subject | docs(cli): DESK-1 the command reference lists `desktop` (the cli smoke's documented-commands check) (DESK-1; Gaps: 신규) |
| 0b4c468 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(desktop): DESK-1 the running-instance probe of /api/stats on 127.0.0.1 ignores HTTP_PROXY and the registry proxy (urllib asked the proxy about the loopback address) (DESK-1; Gaps: 신규) |
| 4ca6387 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(desktop): RISK-A2 RISK-A3 RISK-A4 RISK-A5 A6-prep B1 B2 B5 B8 B9 B14 — Windows exclusive port binding and running-instance check first; the folder dialog reports OK/CANCEL/policy explicitly, times out after 10 minutes and never reads a failure as a cancel; a home, drive-root, AppData, temp or bundle folder is refused (400 너무 넓은 폴더); the key file is made atomically with an owner-only DACL on Windows; QuickEdit off and a close-window cleanup handler; a sleep/wake gap resets the idle clock; shutdown joins the server before closing the socket (RISK; Gaps: 신규) |
| 0a4b4dc | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): RISK-A4 RISK-B3 the desktop page sends a heartbeat the moment it is seen again — visibilitychange (visible), focus and pageshow, besides the 30 s timer; one request in flight as before (RISK-A4, RISK-B3; Gaps: 신규) |
| 8dbfd4f | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): RISK-B4 RISK-B5 RISK-B13 RISK-B17 a 403 for a scan already made says to pick the same folder again; the dialog's waiting text names Alt+Tab, the OneDrive note and a 400 refusal stay under the path box; the launcher is named in the extracted folder; 「프로그램 종료」, a cancel said, a blocked confirm explained (RISK-B4, RISK-B5, RISK-B13, RISK-B17; Gaps: 신규) |
| 1aa63d2 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): RISK-B25 an Enter that ends a Korean IME composition (isComposing, or keyCode 229) no longer submits the scan form or the token form (RISK-B25; Gaps: 신규) |
| 4b0fd13 | 31 | — | Gaps: 신규(스펙 외) | subject | docs: RISK review follow-ups — backlog BL-61..BL-70, QA-SYS-4 measured checks, service doc desktop contract, HANDOFF note (RISK; Gaps: 신규) |
| 721300a | 31 | — | Gaps: 신규(스펙 외) | subject | docs(qa): RISK-B17 QA-SYS-4 names the button 「프로그램 종료」 (RISK; Gaps: 신규) |
| 1a8cd3c | 31 | — | Gaps: 신규(스펙 외) | subject | fix(bundle): RISK-A1 the Windows bundle carries msvcp140*.dll from the pinned msvc-runtime 14.51.36231 wheel and the build checks every .pyd/.dll import table (RISK-A1; Gaps: 신규) |
| 4700dcb | 31 | — | Gaps: 신규(스펙 외) | subject | fix(bundle): RISK-A6 RISK-A7 RISK-B11 RISK-B12 the launcher compares the exit code as a string, says it is starting, passes only a quoted folder and uses pushd; the build precompiles with 3.12 and drops bin/ and direct_url.json; the readme covers the risk review (RISK-A6 RISK-A7 RISK-B11 RISK-B12 RISK-B3 RISK-B8 RISK-B13 RISK-B18 RISK-B19 RISK-B20 RISK-B21 RISK-B22 RISK-C8; Gaps: 신규) |
| 686278a | 31 | — | Gaps: 신규(스펙 외) | subject | feat(bundle): RISK-B10 bundle_info — in the Windows bundle the PDF and 7z/rar pip advice says the feature is not included instead of `pip install` (RISK-B10; Gaps: 신규) |
| c38f8db | 31 | — | Gaps: 신규(스펙 외) | subject | fix(bundle): RISK-A7 precompile skips olefile/olefile2.py, olefile 0.47's Python 2 module that does not compile on Python 3 (RISK-A7; Gaps: 신규) |
| 2adcc7b | 31 | — | Gaps: 신규(스펙 외) | subject | docs(qa): RISK the built-bundle test's skip reason is documented (QA-SYS-10 skip-reason table) (RISK; Gaps: 신규) |
| 2e6557b | 31 | — | Gaps: 신규(스펙 외) | subject | docs(phase0): DESK user decision 2026-10-23 — `desktop` is a gate command, the desktop-only routes exist only on a desktop loopback server, read roots grow only by start-up arguments or the OS folder dialog (DESK; Gaps: 신규) |
| bc663a0 | 31 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on 2e6557b; GUI-REV7 (F-1..F-31, W-1..W-13), DESK-1..DESK-9 and the RISK review commits are in the table (their IDs are not round IDs, so ids[] is unchanged); 19 QA scenarios pass in the gate image's API environment (WP-J; Gaps: G26, G27, G28) |
| a59303c | 31 | — | Gaps: 신규(스펙 외) | subject | fix(desktop): REV2-F1..F8 second review — a desktop server reads nothing until a folder is registered (no working-folder root), the running-instance probe accepts only this user's desktop server (desktop:true + instance_user) with a 5 s timeout and re-asks for 10 s after a bind failure, the PDF import is warmed before serving, the free-port probe also connects (a wildcard listener is not free), a broken-link key file is refused instead of recursing, the bundle folder inside a chosen folder is a note not a refusal, the job-store and log folders count as tool folders, zenity output keeps a trailing space, QuickEdit is restored at exit, a bind error is Korean, "already running" without a browser exits 3 (REV2; Gaps: 신규) |
| c1caf13 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): REV2-D1 D2 second hands-on review — 「검사할 폴더 고르기」 stands at the top of the scan screen (under the first-use steps, before the case fields), and in the desktop mode a request that cannot reach the server puts the lost-connection box up at once with the CONNECTION_LOST words; 프로그램 종료 on a dead server says 「이미 종료되었습니다」 (REV2; Gaps: 신규) |
| d3529be | 31 | — | Gaps: 신규(스펙 외) | subject | fix(bundle): REV2-D1 D5 D6 D7 D9 D13 second review — the launchers refuse only archive-tool temp copies (\AppData\Local\Temp\, \Windows\Temp\, Rar$, \7zO, \7zE; C:\Temp\ is a normal place to unpack) and no_python says to extract first, .pyc files are compiled checked-hash with the build folder stripped (-s/-p) and the build fails if one holds the build machine's home path, the embeddable's vcruntime140*.dll 14.42 are replaced by the msvc-runtime wheel's 14.51 and the build prints and checks every runtime DLL's version, exit 3 (already running, no browser) pauses without an error line, .exp/.d files are pruned, ext-ms-* is no longer a system-DLL prefix, the console code page is restored on every exit; 읽어보세요 D2 D3 D8 D10 D11 D12 (REV2; Gaps: 신규) |
| 31580a6 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(pdf): REV2-D4 second review — pymupdf_available caches its answer and logs a failed import once at WARNING with the traceback, and in the bundle (which ships pymupdf and has no pip) the 501 PDF error and the evidence-PDF failure hint ask for the 「문제 확인.cmd」 output instead of a pip command; outside the bundle both texts are byte-identical (REV2; Gaps: 신규) |
| d04dbfd | 31 | — | Gaps: 신규(스펙 외) | subject | docs: REV2-D2 D3 D8 D10 D11 D12 F7 second review — QA-SYS-4's bundle path quotes run_desktop's console lines exactly, verifies signed reports with the bundle's python from cmd (PowerShell form too) on "the account that holds the key file", expects exit 3 and Ctrl-C's 중단됨(사용자 요청) + Y/N; the service doc describes the key file's temp-file + os.link creation (os.replace only without hard links, where a simultaneous first start can race), instance_user, exit 3 and Ctrl-C's exit 130; the risk review's B3 B6 B7 B9 B11 B14 A7 and D lines follow (REV2; Gaps: 신규) |
| e9c4d17 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(pdf): REV2-D4 the CLI's two PDF refusals use the bundle's engine-failed wording too (the bundle ships pymupdf, so 'not included' was false there) (REV2; Gaps: 신규) |
| 619c90b | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): REV2-D3 D4 D5 second hands-on review — the heartbeat's recovery reads /api/stats again (roots, chips), a scan-start 403 in the desktop mode reads the roots first and says "pick the same folder again" for a folder this page saw registered, a scan-start failure is scrolled into view and summarised under the path box, and the pick's text is said once (under the path box) (REV2; Gaps: 신규) |
| fccf5ad | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): REV2-D6 D7 D8 second hands-on review — the shutdown page is the main landmark and names itself in the tab, the 409 refusal of 프로그램 종료 goes when the scan ends, the recovery is announced in #toasts, and the note under the path box follows the box's value (a cancelled pick keeps it) (REV2; Gaps: 신규) |
| 10efc79 | 31 | — | Gaps: 신규(스펙 외) | subject | docs(qa): REV2 the async-run skip reason of test_gui_desktop is documented (QA-SYS-10 table) (REV2; Gaps: 신규) |
| f3543ad | 31 | WP-J | Gaps: G26, G27, G28 | subject | docs(qa): CONFORMANCE.md and commit traceability regenerated on 10efc79 — REV2 second-review commits (server, bundle, GUI, docs) in the table; 19 QA scenarios pass in the gate image's API environment (WP-J; Gaps: G26, G27, G28) |
| 2bd2217 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(scan-jobs): OMO-2 the job store lists an owner's finished jobs newest first (memory and verified disk records, 7-day TTL) and deletes a finished one — a running job is never deleted (OMO-2; Gaps: 신규) |
| 8ef36aa | 31 | — | Gaps: 신규(스펙 외) | subject | feat(case-store): OMO-3 case fields are kept per scan job in cases.json (0600) in the user-data folder, validated, never inside an examined folder, and pruned with their jobs (OMO-3; Gaps: 신규) |
| a5e4824 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(desktop): OMO-6 the desktop mode opens the GUI in an Edge/Chrome app window with its own profile, the browser tab stays the fallback and --browser-tab (OMO-6; Gaps: 신규) |
| 2a79381 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(case-store): OMO-3 the side refusal quotes the field name as an interpolated value — SourceQuotesTest held 「」 to interpolated values only (OMO-3; Gaps: 신규) |
| b9e9026 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(gui): OMO-1 the app shell's seven assets are served by both servers, packaged and listed in the service doc's endpoint tables, and gui.html carries the sidebar, settings, delete-confirm and welcome skeletons (OMO-1; Gaps: 신규) |
| b8f89eb | 31 | — | Gaps: 신규(스펙 외) | subject | feat(gui): OMO-5 window.DFL — a finished job loads into the ledger through runScanJob's quiet mode (no progress panel, toasts or dfl:scan-* events), and the scan lifecycle is announced as dfl:* events (OMO-5; Gaps: 신규) |
| efca051 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(gui): OMO-7 the app shell — a left sidebar with 새 검사 and 설정, a drawer under 1181 px, Ctrl/Cmd+N and Ctrl/Cmd+, and the page keeps its old layout when the shell script is missing (OMO-7; Gaps: 신규) |
| 573a4e7 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(gui): OMO-9 설정 — theme 시스템/밝게/어둡게, scan defaults, shortcuts and the record folder, key and retention, kept across restarts (OMO-9; Gaps: 신규) |
| d990124 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(gui): OMO-10 a first-run welcome panel with the three steps and one way in, hidden once a scan exists, reopened from 설정 (OMO-10; Gaps: 신규) |
| 5a82db6 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(gui): OMO-11 the long caveat explanations fold behind 자세한 설명 while every warning's headline stays in view; the text is unchanged (OMO-11; Gaps: 신규) |
| 512f5a5 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(server): OMO-4 /api/history, /api/history-delete and /api/job-case on both servers — owner as /api/scan-status, a running job is never deleted, unverifiable records are counted in the note, case entries are pruned with their jobs; both endpoint tables list them (OMO-4; Gaps: 신규) |
| 45c5c42 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(gui): OMO-8 the sidebar lists the finished scans, reopens one with its case fields (an item without saved fields clears them), saves the case with the scan, deletes after a confirmation and shows a running scan, also after a reload (OMO-8; Gaps: 신규) |
| caa9ce2 | 31 | — | Gaps: 신규(스펙 외) | subject | docs: OMO-12 README, the CLI and service docs and the changelog describe the app window, --browser-tab, the sidebar history, the case store and where the GUI keeps its settings (OMO-12; Gaps: 신규) |
| 0a58c0b | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): OMO-11 the closing one-line caveat stays in view and the 자세한 설명 targets keep their 44 px hit area inside the line, so the folded banner is never taller than before (143 px at 1280 px, 432 -> 225 px at 390 px) (OMO-11; Gaps: 신규) |
| 379e8b5 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): OMO-10 the welcome panel waits for the history's answer (1.5 s at most) so a returning examiner never sees it flash before the list arrives (OMO-10; Gaps: 신규) |
| e8979ab | 31 | — | Gaps: 신규(스펙 외) | subject | docs(qa): OMO-3 the cases.json file-mode test's skip reason is documented (QA-SYS-10 skip-reason table) (OMO-3; Gaps: 신규) |
| c594e92 | 31 | — | Gaps: 신규(스펙 외) | subject | docs(qa): OMO commit traceability regenerated on e8979ab — the OMO-1..OMO-12 app-shell commits (server, desktop, GUI, docs) in the table (OMO; Gaps: 신규) |
| 10a7b67 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(bundle): OMO-1 the Windows bundle check requires the app shell's scripts and stylesheet too (gui-shell/history/settings/onboarding.js, gui-shell.css) — a bundle without them would open the old layout silently; a fresh build passes (OMO-1; Gaps: 신규) |
| b2fbf19 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(scan-jobs): OMO-4 the skipped-record count of the history comes back per call (stats) instead of a store attribute two concurrent requests could overwrite (OMO-4; Gaps: 신규) |
| 4ec5f54 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(bundle): OMO-1 the bundle check also requires gui-settings.css and gui-onboarding.css — without them the settings dialog and the welcome panel open unstyled (OMO-1; Gaps: 신규) |
| 61c9416 | 31 | — | Gaps: 신규(스펙 외) | subject | docs(qa): OMO commit traceability regenerated on 4ec5f54 — the bundle-check fixes after the gate review (10a7b67, 4ec5f54) and the per-call history skip count (b2fbf19) in the table (OMO; Gaps: 신규) |
| fc4b63d | 31 | — | Gaps: 신규(스펙 외) | subject | feat(gui): OMO-13 the UI font Pretendard Variable (OFL-1.1) ships in the package and both servers serve it at /fonts/PretendardVariable.woff2 — no web-font request leaves the machine; the bundle check requires it (OMO-13; Gaps: 신규) |
| 3e912e5 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(gui): OMO-13 modern redesign — Pretendard, zinc neutrals with one indigo accent, sticky translucent top bar, segmented tabs, verdict summary cards, rounded ledger, refined sidebar, dialogs and welcome card; theme tokens defined once in gui.css; the offline test now pins every CSS url() to the bundled /fonts/ (OMO-13; Gaps: 신규) |
| 408637b | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): OMO-8 a multi-file drop keeps every file (entries and the file list are read before the first await, the drop's data is gone after dispatch) and the running sidebar item names the case only when it belongs to the scanned folder (dfl:scan-started caseForScan) — found by the all-features QA (OMO-8; Gaps: 신규) |
| d314742 | 31 | — | Gaps: 신규(스펙 외) | subject | docs(qa): OMO commit traceability regenerated on 408637b — the redesign (Pretendard asset fc4b63d, design 3e912e5) and the QA fixes (408637b) in the table (OMO; Gaps: 신규) |
| 224258a | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): OMO-14 the examiner's review — screens say what a check could not do and what that means (no CLI flags, env var, 의존성 부재 or in-sample), the welcome and the summary say what the tool finds and that 판단 불가 is not 원본, the finish toast is short and centred, Korean is out of monospace, a compact phone top bar, the empty report form, 6/17 explained; reports and verdicts unchanged (OMO-14; Gaps: 신규) |
| e4e2727 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(gui): OMO-14 the review-fix wording quotes fixed words with "…" — 「」 wraps only interpolated values (test_corner_brackets_only_wrap_interpolated_values), and the folder hint names the desktop mode without the English product name (OMO-14; Gaps: 신규) |
| 29fd00f | 31 | — | Gaps: 신규(스펙 외) | subject | feat(gui): OMO-15 판단 불가일 때 더 확인하는 방법 — ask for the original file, the generators' own checks (SynthID detector, OpenAI verify) as an outside procedure with consent and recorded apart from the conclusion, and reference signals are clues only (OMO-15; Gaps: 신규) |
| da4e9b0 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(evidence): OMO-16 an ITL-only C2PA signature that declares generative AI (valid, hash match) is strong synthetic evidence with the caveat 임시 신뢰 목록 서명 — 공식 신뢰 목록 밖 (owner decision; capture declarations stay moderate, P0R-21); NovelAI, InvokeAI, Fooocus, SwarmUI and Midjourney generator records are recognised as strong self-declared records, and an rdf:resource DigitalSourceType is no longer dropped (OMO-16; Gaps: 신규) |
| aa366d2 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(bundle): OMO-17 the watermark and TrustMark checks run in the Windows bundle — the SD/SDXL/FLUX.1 decoder's BGR->YUV step is numpy (bit-identical to OpenCV over all 2^24 colours), onnxruntime 1.31.1 ships, and the TrustMark Q/P/C weights are fetched at build time, checked against assets.json sha256 and bundled; the bundle check requires them (zip 80.6 -> 206.9 MB) (OMO-17; Gaps: 신규) |
| dacaa0a | 31 | — | Gaps: 신규(스펙 외) | subject | feat(evidence): OMO-18 explainable re-save and path facts (neutral, weak, never a conclusion) — a JPEG's quantization tables matched exactly against the IJG standard tables (ITU-T T.81 K.1/K.2, libjpeg-turbo scaling), a camera-make record next to IJG tables as a re-save cue, and a video whose codec/profile/coded size/encoder match a KakaoTalk transfer copy (arXiv 2304.03848) as 메신저 재전송 흔적 with a request for the original (OMO-18; Gaps: 신규) |
| 72d71c1 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(models): OMO-19 unmeasured detectors run as 참고 신호 only — a gated profile marked reference_signal runs when its pinned checkpoint is present and adds 미측정 모델 원점수(참고 신호) to reference_signals; verdicts, evidence and coverage stay those of the skipped profile; Community Forensics ViT-S/384 (MIT, exported from OwensLab@6076002 with ImageNet normalisation, 440->384 crop) and AASIST (MIT, clovaai@a04c986) pinned with fetch scripts; the measurement gate is unchanged (OMO-19; Gaps: 신규) |
| 1259b3c | 31 | — | Gaps: 신규(스펙 외) | subject | feat(bundle): OMO-19 the Windows bundle ships the Community Forensics ViT-S/384 reference-signal checkpoint (exported by scripts/fetch_community_forensics.py into <cache>/exported, checked against the profile's pin.sha256, verified in the bundle check) so 참고 신호 runs on the examiner's PC with the bundled onnxruntime; AASIST stays out (needs torch) (OMO-19; Gaps: 신규) |
| 0147e49 | 31 | — | Gaps: 신규(스펙 외) | subject | docs(qa): OMO commit traceability regenerated on 1259b3c — the review fixes (OMO-14), external-check guidance (OMO-15), ITL and generator records (OMO-16), bundled watermark detectors (OMO-17), JPEG/messenger facts (OMO-18) and reference signals (OMO-19) in the table (OMO; Gaps: 신규) |
| da712f8 | 31 | — | Gaps: 신규(스펙 외) | subject | docs(qa): OMO-17 the skip-reason table follows the cv2-free watermark tests — 'numpy and Pillow required' and 'opencv required for the reference conversion' are listed, the retired 'numpy, opencv and Pillow required' row is gone (QA-SYS-10 test_every_skip_reason_is_documented) (OMO-17; Gaps: 신규) |
| 12ac178 | 31 | — | Gaps: 신규(스펙 외) | subject | docs(qa): OMO commit traceability regenerated on da712f8 — the skip-reason table fix in the table (OMO; Gaps: 신규) |
| 8020c88 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(evidence): OMO-20 narrow-domain measurement and likelihood ratios — `measure` turns a detector's scores on one corpus variant (e.g. kakao) into a measured_on block (threshold fitted on val at a target FPR; AUROC, FPR and recall on test with an origin-cluster bootstrap 95% CI so re-encoded copies of one picture are not independent samples) and runs the unchanged measurement gate; a logistic score->LR calibration fitted on val is validated on test (Cllr < 1, Cllr_min by PAV, Tippett, sample-size LR bounds, >=200 per class), and a validated LR profile named by a reference-signal model profile adds 「증거 강도(우도비)」 with the ENFSI 2015 verbal scale to its reference row; verdicts and decision rules unchanged (OMO-20; Gaps: 신규) |
| ae80f83 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(evidence): OMO-20 what the real-data run caught — Cllr_min pooled each tie group as it arrived (481 ties at score 0 glued higher scores to a lower block; 0.743 vs 0.652 from a group-first PAV), so PAV now builds one block per score first; the LR is a 0-100 table clipped to monotone val tail-ratio bounds (+1 per class, tails with >= 10 samples) instead of a sample-size cap that let an AUROC-0.87 detector reach LR 281; `measure --model-path` reads items from the corpus root (root_hint) and refuses paths outside it; a single-profile --model-path scan finds its lr_profile through the model path; `measure --scores-out` keeps the per-item scores so a measurement can be redone with --scores (OMO-20; Gaps: 신규) |
| 20d89b0 | 31 | — | Gaps: 신규(스펙 외) | subject | docs(evidence): OMO-20 the examiner's procedure for a KakaoTalk corpus — folder layout with same-name transfer copies, corpus build/split, `measure`, reading the gate and the LR profile, and a worked public-data example (OpenFake core test, Community Forensics: originals AUROC 0.875 [0.853, 0.895] pass, SNS-like re-encode [0.803, 0.852] fail, LRs validated but at most 20 after re-encoding); the verbal scale reads "…가설을 …지지" and fixed words in OMO-20 sources are quoted with "…" (test_corner_brackets_only_wrap_interpolated_values) (OMO-20; Gaps: 신규) |
| 8abad84 | 31 | — | Gaps: 신규(스펙 외) | subject | refactor(evidence): OMO-20 `measure` checks the unchanged gate without a temp file — measurement_gate.check_profile reads the file and profile_problems checks an already-read profile (same rules), and --scores-out is written whole through output_files (test_every_tempfile_call_uses_the_session_folder, test_no_other_module_writes_a_file) (OMO-20; Gaps: 신규) |
| 2348297 | 31 | — | Gaps: 신규(스펙 외) | subject | docs(qa): OMO commit traceability regenerated on 8abad84 — narrow-domain measurement and likelihood ratios (OMO-20) in the table (OMO; Gaps: 신규) |
| e9757f3 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(evidence): OMO-20 a damaged LR profile can no longer fail an analysis — the table must be 101 finite log10 values within ±12, scope and validation must be objects, list fields of the wrong type read as empty, and an lr_profile name outside the models folder is ignored, so a bad file next to a model profile leaves only the raw-score reference row (gate review blocker, test_a_damaged_lr_profile_leaves_the_raw_score_row_alone); `measure` exits 2 on unreadable or non-JSON input instead of a traceback; the docstring and the procedure describe the tail-ratio bound (OMO-20; Gaps: 신규) |
| 0e15c77 | 31 | — | Gaps: 신규(스펙 외) | subject | fix(evidence): OMO-20 LR profile numbers must be JSON numbers within range — strings, booleans, over-large integers and non-finite values are refused, and an unreadable LR or model profile (bad UTF-8, bad JSON, a 5000-digit literal, deep nesting) leaves only the raw-score reference row (gate re-review blockers; test_an_unreadable_lr_file_leaves_the_raw_score_row_alone) (OMO-20; Gaps: 신규) |
| 1c599af | 31 | — | Gaps: 신규(스펙 외) | subject | docs(qa): OMO commit traceability regenerated on 0e15c77 — the OMO-20 gate-review fixes in the table (OMO; Gaps: 신규) |
| e1c6fbf | 31 | — | Gaps: 신규(스펙 외) | subject | feat(measure): OMO-20 review follow-ups — per-generator results with the weakest generator, --clusters resamples a person/speaker (real and AI together), --dropped-scores puts audit-dropped AI candidates back and gates on that interval, --audit-pos/--audit-neg raise the required CI low (Clopper-Pearson) and cap the LR table (test_hard_ai_dropped_by_the_audit_is_put_back_and_gated) (OMO-20; Gaps: 신규) |
| 3641ef4 | 31 | — | Gaps: 신규(스펙 외) | subject | docs(qa): OMO commit traceability regenerated on e1c6fbf — the OMO-20 review follow-ups (per-generator results, person/speaker clusters, audit drops, label-audit bounds) in the table (OMO; Gaps: 신규) |
| f56b467 | 31 | — | Gaps: 신규(스펙 외) | subject | feat(gui): OMO-21 design review — the scan screen leads with the folder and 검사 시작 (case fields fold below, one primary per view), an empty ledger opens the latest saved scan, the four tabs share one style, the evidence panel folds neutral records and the long limitation list, the report picks a format and exports with one 내보내기 button (blocking checks first, the rest folded), neutral status chips explain themselves, a six-step type scale and radius tokens, drawn radios and checkboxes that read unchecked in dark mode, a compact phone header and a folder path that shows its end; verdicts and reports unchanged (OMO-21; Gaps: 신규) |
