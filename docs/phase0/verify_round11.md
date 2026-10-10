# 독립 검증 라운드 11 결과 (HEAD b600a58) — 전부 수정 (면제 없음)

R10 항목 종료(R10-5 서버 측 제외), 회귀 없음. 남은 항목:

R11-1 (Med, 차단). UTF-8이 아닌 파일명(리눅스의 CP949 `증거사진.png`, lone surrogate)이 스캔 전체를 깨뜨림: CLI --json-out exit 2 "입력을 처리할 수 없습니다(UnicodeEncodeError)" + 0바이트 JSON, HTML 미작성; 웹 /api/scan 400에 영어 인코딩 오류; API 500. (cli_render._write_json_out, HTML 임베드, 두 서버의 JSON 인코더) → 모든 경로 문자열은 `os.fsdecode` 결과를 `surrogateescape`로 직렬화하거나 display_name이 lone surrogate를 `\udcXX`로 이스케이프; JSON/HTML/CSV/PDF/서버 응답 전부 surrogate-safe; 해당 행은 정상 분석되고 다른 행에 영향 없음; 한국어 메시지; 실제 non-UTF-8 파일명(os.fsencode로 생성)으로 CLI·웹·API 테스트.
R11-2 (Low-Med). QA-SYS-10이 full-suite의 skip을 무시(API venv 기록에서 baseline 633개 중 14개 skip, 양쪽 venv에서 6개 skip)하고 통과 표시 → qa_outcomes FULL_SUITE 분기에서 skip 집계: skip이 있으면 "건너뜀(환경)"으로 표기하고 skip된 테스트 목록·사유를 CONFORMANCE.md 비고에 기재; 환경 의존 테스트(torch/c2pa/librosa/pymupdf/hwp 픽스처/SynthID)는 TEST-DELETIONS.md가 아닌 docs/QA-ENV-DEPENDENT-TESTS.md에 목록·사유·필요 extras 기재.
R11-3 (Low). POST /api/report, /api/feedback에 깊은 중첩 JSON → 두 서버 500(RecursionError 미포착) (webapp_api.py:1014, 1121, 1462) → RecursionError 포착 → 400 "JSON 중첩이 너무 깊습니다"; 두 서버 테스트.
R11-4 (Low). display_name의 `\|` 이스케이프가 1:1 아님(`bs\|p`와 `bs\\|p` 동일 표시; 리터럴 `\n`과 실제 LF 동일) → 백슬래시 자체를 `\\`로 이스케이프해 단사(injective) 보장; GUI JS 동일; 속성 테스트(무작위 문자열 왕복·충돌 없음).
R11-5 (Low). 영어 탐지기: 기호에 붙은 단어 미탐(`fake✓`, `▶fake◀`, `fake的`, `판정★real`, `✔real`, `fake⚠`, `결론●real`) → 유니코드 카테고리 S*/P* 전부를 토큰 경계로; CJK 한자 접미도 분리.
R11-6 (Low). 이름에 `<!--<script>`가 임베드 JSON `<script>` 안에 원문으로 남음(`</`만 이스케이프) → `<`, `>`, `&`, U+2028/2029를 `<` 등으로 이스케이프(JSON 안전 임베드 표준).
R11-7 (Low). 증거설명서 Markdown에 이름의 Markdown 링크/이미지 문법(`![t](https://…)`) 미중화 → markdown_cell에서 `[`, `]`, `(`, `)`, `!`, `<`, `` ` ``, `*`, `_`, `#` 이스케이프.
R11-8 (Cosmetic). "상세는 로그 참조 — 상세는 로그 파일" 중복 → 한 번만.
R11-9 (Cosmetic). 테스트 소스의 raw RLO/zero-width 문자(ruff PLE2502/2515) → 이스케이프 시퀀스로 작성.
R11-10 (Low). GUI 근거 제목·상세가 displayName을 거치지 않음(gui.js:361) → 모든 사용자 표시 문자열(제목·상세·사유·limitations)에 displayName 적용; HTML 보고서와 동일.
R11-11 (잔여 리스크, 수정). `str` 타입 경로 옵션이 R10-9 메타테스트를 우회 → 옵션 이름/도움말에 "경로/폴더/파일/dir/path"가 있거나 default가 Path인 str 옵션도 검사 대상.
R11-12 (잔여 리스크, 수정). 웹 서버가 모든 미지 경로(/docs 포함)에 GUI를 서빙 → 알려진 경로(`/`, `/gui*`, `/api/*`) 외는 404 한국어 JSON; 라우트 메타테스트 갱신.
R11-13 (잔여 리스크, 수정). CSV 전각 `＝` 미보호 → 전각 `＝＋－＠`도 보호.
R11-14 (수정). JSON stdout에 raw bidi/C1 유지(설계) → JSON 행에 `display_name` 필드 추가(이스케이프본), 원문 `name`은 유지; 계약 문서화.
