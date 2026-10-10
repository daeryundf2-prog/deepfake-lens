# 독립 검증 라운드 10 결과 (HEAD 21717bd) — 전부 수정 (면제 없음)

R9-1…R9-10 종료, 회귀 없음. 남은 항목:

R10-1 (Med, 차단). 파일명이 평문 출력에 원문 그대로 → CLI 표에서 이름에 CR이 있으면 A1111 AI 이미지가 "원본성 근거 있음 … family_photo.png"로 표시(JSON은 조작·생성 근거 있음); ESC 시퀀스가 터미널에 전달; 증거설명서 Markdown에서 이름의 개행이 위조 행 `| **갑 제9호증** | … 원본성 근거 있음 |` 삽입, `|`가 열을 밀어 결론 열 소실. (cli_render.py:176, evidence_statement.py:403/to_markdown) → 모든 평문/Markdown/CSV/HTML/PDF 렌더에서 파일명·경로·에코 문자열의 C0/C1 제어문자(CR, LF, ESC, TAB 포함)와 Markdown 테이블 구분자 `|`를 이스케이프(`\r`,`\n`,`\x1b` 표기, `\|`); 공통 함수 result_text.display_name()으로 통일; 테스트: CR/LF/ESC/`|`/zero-width 문자가 든 이름의 A1111 PNG가 표·MD·CSV·HTML·PDF에서 결론 열을 바꾸지 못함(행 수·결론 열 값 단언).
R10-2 (Low-Med). CSV 수식 주입: `=`,`+`,`-`,`@`,TAB,CR로 시작하는 셀 → 앞에 `'` 붙이기(OWASP CSV injection 대응), 테스트.
R10-3 (Low-Med). docs/TEST-DELETIONS.md가 phase0 히스토리에 없는 해시(리라이트 이전) 인용; 클론에서 QA-SYS-10 삭제 테스트가 skip인데 CONFORMANCE는 통과 표시 → TEST-DELETIONS.md는 커밋 제목(subject)으로 인용하고 해시는 참고로만; 테스트는 제목으로 `git log --grep`해 찾고, 못 찾으면 skip이 아니라 fail; qa_phase0는 QA ID의 테스트가 하나라도 skip이면 "통과"가 아니라 "건너뜀(환경)"으로 표기(전부 pass일 때만 통과).
R10-4 (Low). `feedback --scan-json`이 `::`/`\:` 포함 실제 파일명 라벨을 미매칭(행은 이스케이프, 조인은 원문 비교) → 조인 키를 unescape한 원문 경로 또는 (container, member) 필드로; 테스트.
R10-5 (Low). 예기치 않은 오류의 exit code: verify-report 깊은 중첩 JSON → 1(변조됨 코드) 대신 4; evidence-statement deep.json → 문서는 2인데 1; web/api-serve --port -1/99999 → 1 대신 2; 행이 없는데 "처리 오류 1건 — 각 행의 검사 범위(coverage)에 사유가 기록" 출력 → 각 명령의 예외 처리에서 RecursionError/ValueError 등을 입력 오류로 분류해 문서화된 코드 사용; 행 0건이면 해당 문구 생략; 포트 범위 검증 1–65535.
R10-6 (Low). 단일 파일 업로드(/api/analyze-upload, 비아카이브)의 `t:::c.png`가 이스케이프되지 않아 스키마 문구("never contains '::'") 위반 (webapp_api.py:781, 941) → 이스케이프 적용, 테스트.
R10-7 (Low). 영어 탐지기 미탐: `【fake】`, `verdict→fake` → 전각 괄호·화살표류(→ ⇒ ➜ 【】『』〈〉) 토큰 경계 추가.
R10-8 (Cosmetic, 전부 수정). "올바르지 않은 int 값" → "정수가 아닙니다"; feedback 오류 "2행 … (1행 109열)" 행 번호 일관화; R9-10 메시지·docstring에 CONFORMANCE.md 허용 명시; 링크된 폴더 파일 수가 이미 센 하위 폴더 제외로 4/5 표시 → "(중복 제외)" 표기 또는 전체 수 표시로 명확화; REST 표에 `/gui*`, `/docs`, `/redoc`, `/openapi.json` 추가(또는 비활성화).
R10-9 (테스트 보강). R9-4 메타테스트 정규식 `--.*-(dir|root)|--out.*`이 `--frames`, `--dest` 같은 미래 옵션을 못 잡음 → type=Path이고 쓰기 대상인 옵션은 전부 등록 강제(쓰기 옵션 명시 목록 + 미등록 Path 옵션은 읽기 전용으로 선언해야 통과).
