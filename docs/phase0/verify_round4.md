# 독립 검증 라운드 4 결과 (HEAD f99b0a3) — 수정 필요 항목 전부

판정: 게이트 0 통과 불가. 게이트 스크립트 전부 녹색, 라운드 3 항목은 닫혔으나 아래가 남음. "타협 없이" 규칙: 아래 전부 수정(면제 없음).

## 차단 항목
B1. 같은 파일, 다른 결과. 폴더 스캔·GUI /api/check·API는 evil.zip을 조작·생성 근거 있음으로 보고하는데, 단일 파일 CLI(`forensic`, `classify`, `explain`, `legal-report`, `evidence-statement <file>`, `analysis_api.analyze_path`)는 "단일 파일 분석에서는 압축 내부를 펼치지 않습니다"로 판단 불가. (core.py:827) → 단일 파일 진입점도 아카이브를 폴더 스캔과 동일하게 펼쳐 동일 행·동일 결론. QA-OUT-4에 단일 파일 CLI 레그 추가.
B2. HTML 보고서 영어: "Decision thresholds: … in-sample (reference only)…", "heatmap" 열 헤더 (reports.py:230, :232, 텍스트 :35-53).
B3. HTML·포렌식 PDF에 raw `skipped` 상태 (reports.py:646, :481); 포렌식 PDF "문서 번호"가 라틴 폰트로 그려져 "·· ··"로 렌더 (reports.py:412).
B4. `legal-report` 텍스트에 `[deterministic/synthetic/strong]`, `ran/skipped/failed`, raw 모델 id, "bytes".
B5. GUI "출처 추정 (HIGH)/(UNKNOWN)", "archive_member 미실행" (gui.js:635, :242-249 — result_types.py:288의 라벨표에 있는 항목이 gui.js 라벨표에 없음).
B6. coverage reason "C2PA 판독 실패: _C2paOther: Other: could not create valid JUMBF for claim"이 JSON·표·HTML·GUI·증거설명서에 도달 → error_text 번역표에 추가.
B7. `doctor --format json` stdout이 PyMuPDF 설치 시 JSON 아님(doctor.py:64가 :385에서 fitz를 직접 import → 경고가 stdout) → pdf_backend.import_pymupdf 경유; doctor 표의 영어 모델명·영어 섹션 헤더 → 한국어 display_name·헤더.
B8. `--pdf-out` 단순 PDF가 전부 영어(Latin-1 한계) → pymupdf 없으면 R6처럼 한국어 안내 후 exit 2(영어 PDF를 만들지 않음), 있으면 한국어 PDF. 레거시 서브커맨드 37개의 `--help` 영어 → 전부 한국어.

## 수정 항목
S1. 아카이브 멤버가 증거설명서·HTML·포렌식 PDF에서 basename("a1111.png")로만 표기되어 컨테이너 정보 소실, "evil.zip::경로 행 참조" 상호참조가 가리키는 행 없음 → "evil.zip::a1111.png" 전체 표기.
S2. 포렌식 PDF 심볼릭 링크 해시 줄 "원본 파일 접근 실패" → 증거설명서와 같은 "해시 불가(심볼릭 링크 — 링크를 따라가지 않음)" (reports.py:501).
S3. `--redact-paths`가 `models[].profile`의 도구 설치 경로를 남김 → 프로필은 파일명만.
S4. 존재하지 않는 폴더/파일 입력 시 `오류: <path>`만 출력, 이유 없음; exit 2 미문서화 → "오류: 폴더를 찾을 수 없습니다: <path>" 등 사유 포함, docs/deepfake-lens-cli.md에 exit code 표.
S5. GUI CSP에 `media-src blob:` 없어 wav/mp4 미리보기 거부 (webapp.py:456, api_server.py:233).
S6. 증거설명서 provenance에 in-sample 주의 문구 누락(CLI·HTML·GUI·포렌식 PDF에는 있음).
S7. 오디오 행에 모델 limitation이 2번씩 중복, 비활성 모델의 다운로드 안내 포함 → 중복 제거, 비활성(supported:false) 모델의 다운로드 안내는 limitations에서 제외.
S8. test_korean_output 휴리스틱 우회 가능: "Uncalibrated score. Treat cautiously. Not proof.", "UNVERIFIED SCORE, ignore it", "Don't trust it! Isn't proof!", "Caught 1/4 DALL-E fakes, missed 3/4" 통과 → 문장 단위(마침표/느낌표로 분리) 검사, 축약형(Don't/Isn't) 단어 인정, 숫자/슬래시 토큰 제거 후 검사; 그리고 렌더 출력(HTML 텍스트, 포렌식 PDF 텍스트, legal-report, doctor 표, gui.js 라벨표, CLI 표)까지 검사 범위 확장.
S9. 테스트 미사용 import 2건(test_cli_operations.py `time`, test_json_contract.py `io`).

## 스펙 문자 그대로의 편차 (면제 없이 수정)
W1. WP-G: `_iter_files`가 디렉토리 레벨별 정렬(파일 먼저, 하위 폴더 나중) → 전체 경로 문자열 전역 정렬.
W2. WP-J: QA 테스트가 9개 파일에 분산 → 스펙이 명명한 4개 파일(test_qa_in.py, test_qa_out.py, test_qa_adv.py, test_qa_sys.py)로 통합(클래스 단위로 합치기; traceability.json·qa_phase0.py 갱신). QA-MANUAL.md 기록표는 "미실시" 상태를 명시.
W3. 규칙 1: 커밋 트레일러 — 세션 소유자가 git filter-branch로 일괄 수정(에이전트 작업 아님).
