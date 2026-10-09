# 검증 ID → 스펙 갭 ID 추적표 (Y13, P12)

<!-- 생성 파일: scripts/build_traceability_commits.py가 docs/traceability-commits.json과 git 히스토리에서 만든다. 손으로 고치지 말 것 — 매핑은 JSON의 ids[]를 고친 뒤 스크립트를 다시 실행한다. -->

Y13/P12: 검증 결함 ID(라운드 1–9)를 0단계 스펙의 갭 ID(G1–G34) 또는 신규(스펙 외) 사유에 매핑하고, 현재 phase0 히스토리의 커밋마다 제목의 ID와 'Gaps:' 줄을 적는다. 라운드 3과 라운드 6의 N은 서로 다른 집합이므로 N3-x / N6-x로, 라운드 5의 G1–G16은 스펙의 G1–G34와 충돌하므로 V5-G1…V5-G16으로 표기한다. 매핑(ids[], wp_gaps, unused_ids, notes)은 손으로 관리하는 데이터이고, commits[]와 ids[].commits는 scripts/build_traceability_commits.py가 git 히스토리에서 다시 만든다(제목으로 이전 항목과 매칭).

범위: `dad9730..3e256fc`(병합 커밋 제외, 커밋 141개). 이 표는 **표를 재생성한 커밋의 부모까지**를 덮는다 — 재생성 커밋 자신의 해시는 표에 없다(자기 해시를 담을 수 없음). CI(`python scripts/build_traceability_commits.py --check`)가 같은 범위를 히스토리에서 다시 만들어 커밋된 표와 비교한다.

요약: ID 133개 — 스펙 갭에 매핑 78개(그중 신규 사유 병기 9개), 신규(스펙 외)만 55개.

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
| R9-10 | 9 | build_traceability_commits --check가 표 이후 커밋 수와 무관하게 통과 → 재생성 커밋 1개(추적표 파일만)를 초과하면 실패 | — | 신규(스펙 외): 커밋 추적성(공통 규칙 1) — 갭 목록 밖 | d2036db, b88ef92, 3e256fc |

커밋 제목에 쓰이지 않은 ID:

- R10: 커밋 제목에 쓰이지 않음 — 라운드 2 문서가 없어 매핑 불가(커밋 메시지(라운드 2 문서 없음))
- R11: 커밋 제목에 쓰이지 않음 — 라운드 2 문서가 없어 매핑 불가(커밋 메시지(라운드 2 문서 없음))
- R12: 커밋 제목에 쓰이지 않음 — 라운드 2 문서가 없어 매핑 불가(커밋 메시지(라운드 2 문서 없음))
- R13: 커밋 제목에 쓰이지 않음 — 라운드 2 문서가 없어 매핑 불가(커밋 메시지(라운드 2 문서 없음))
- R14: 커밋 제목에 쓰이지 않음 — 라운드 2 문서가 없어 매핑 불가(커밋 메시지(라운드 2 문서 없음))
- R15: 커밋 제목에 쓰이지 않음 — 라운드 2 문서가 없어 매핑 불가(커밋 메시지(라운드 2 문서 없음))
- R17: 커밋 제목에 쓰이지 않음 — 라운드 2 문서가 없어 매핑 불가(커밋 메시지(라운드 2 문서 없음))

근거: `verify_round1/4/5/6/7/8/9.md`(라운드 2·3은 문서가 없어 해당 커밋 메시지), 스펙 WP 머리글의 갭 목록.

라운드 3의 N은 커밋 제목에 N1–N8만 쓰였다(N3-1…N3-8). 라운드 6의 N3(=V5-G10, 커밋 리워드)은 세션 소유자 작업이라 커밋 제목에 없다.

Z3 → G7 근거: R-OUT-6(진입점이 같은 입력을 같게 다룸) — CLI 진입점이 `Path()`로 끝 구분자를 지워, OS가 ENOTDIR로 거부하는 경로(`photo.png/`)를 다른 파일 경로처럼 분석했다.

P12: 라운드 8 검증 때 이 표의 해시 115개가 전부 히스토리 리라이트 이전 값이었다 — 이제 표는 현재 히스토리에서 제목으로 매칭해 재생성되고(`pre_rewrite_subject`에 리라이트 전 제목), CI가 `--check`로 최신 여부를 확인한다.

P14: Gaps를 제목 괄호에만 적은 커밋(예: `(Z1-Z5; Gaps: G7, 신규)`)은 규칙상 허용 — 스크립트가 본문의 `Gaps:` 줄을 먼저, 없으면 제목을 읽는다.

## 커밋별 `Gaps:` 줄

`Gaps 출처`: 본문의 `Gaps:` 줄(body) 또는 제목 괄호 안의 `Gaps:`(subject, P14 — 규칙상 허용).

| 커밋 | 라운드 | 제목의 ID | Gaps 줄 | Gaps 출처 | 제목 |
| --- | --- | --- | --- | --- | --- |
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
