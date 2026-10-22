# GUI-REV7 구현 계획 — `ux_ax_review_17b3a15.md`의 수정

작성 2026-10-22. 기준 HEAD 17b3a15(phase0). 리뷰 ID는 `docs/gui/ux_ax_review_17b3a15.md`의 F-n(GUI)·W-n(Windows 절차).
작업 규칙은 HANDOFF §6: Med 이상과 사실과 다른 문구만 고치고, Low·Cosmetic은 BACKLOG로. 커밋 제목은
`fix(gui): GUI-REV7 F-n … (F-n; Gaps: 신규)`, 테스트 약화 금지, 회귀 테스트는 `deepfake_lens/tests/test_gui_*.py` 방식
(소스 단언 + node/JXA로 gui.js 함수 실행).

## 작업 묶음

### WP-A GUI 코드 (gui.js / gui.html / gui.css) — 순서대로, 커밋 하나씩

| 순서 | ID | 고칠 것 | 검증 |
|---|---|---|---|
| 1 | F-1 High | 스킵 링크: `href="#main"` 대신 클릭 핸들러로 `preventDefault` + `$('main').focus()`; `hashchange`에서 `viewFromHash()`가 null이면 화면을 바꾸지 않음(해시 `#main`·빈 해시 모두) | 소스 단언 + 해시 `#main` 뒤 `view-report`가 숨지 않음(Playwright 스모크 1건) |
| 2 | F-2 High | 빠른 검사 `mode === 'files'`: 폴더 결과(`results.length && lastScanJob`)가 있으면 `window.confirm`("지금 보이는 검사 결과 N개를 이 압축 파일의 결과로 바꿉니다. 바꾼 뒤에는 증거설명서를 만들 수 없습니다. 바꿀까요?")로 묻고 취소면 중단; `gui.html:305` 안내문을 "…행으로 **바꿔 싣습니다**(폴더 검사 결과는 지워짐)"로 | 소스 단언(confirm 문자열·hint 문구) |
| 3 | F-3 Med | `/api/stats` 뒤 기본 폴더 채우기: 칸에 초점이 있거나 값이 있으면 덮지 않음; 403 「허용되지 않은 경로」 토스트에 허용 폴더(`defaultFolder`)를 덧붙임 | 소스 단언; 초점 상태에서 stats 응답 모의(함수 분리 `applyDefaultFolder(folder)`) |
| 4 | F-4 Med | 새 검사가 `done`/`cancelled`로 끝나면 `data-scan` 오류 상자 제거; `retireErrors`의 제목 수정이 live 영역을 재낭독하지 않게(상자에 `aria-live` 없음 확인, 제목 수정 대신 `.err-old` 라벨은 `aria-hidden` 텍스트 노드로 추가 또는 region을 `role=region`+개별 상자 `role=alert`로) | 소스 단언 + 함수 실행 |
| 5 | F-5 Med(거짓 문구) | `progressStart`에서 `view-results-btn` 문구를 「결과 보기」로 되돌림 | 소스 단언 |
| 6 | F-6 Med | 제출 핸들러 첫 줄에서 `isBusyButton($('analyze-btn'))`이면 return(또는 `scanBusy = true`를 동기로) | 소스 단언 |
| 7 | F-7 Med | `max-files` `max="2000"`(상수는 서버 `WEB_MAX_SCAN_FILES`와 같게 — `gui_labels`처럼 소스 대조 테스트), 상한 배너 문구를 "서버 상한 2000개 — 폴더를 나누어 검사하십시오"로; 0·빈값은 제출 전 인라인 오류(`aria-invalid`, `aria-describedby`) | 소스 단언 + 서버 상수 대조 테스트 |
| 8 | F-8 Med | `@media (forced-colors: active)`에 `.row[aria-current="true"]`(outline 또는 border-left), `.seg[aria-pressed="true"]`(border 2px + outline), `.rev-star[aria-pressed="true"]`, `.progressbar .fill`(`forced-color-adjust: none; background: Highlight`), `.row.row-changed`, `.drop.over`, `.studio-btn.active` | CSS 소스 단언(선택자 존재) + Playwright `forced_colors="active"` 캡처 1장 |
| 9 | F-9 Med | `export-note`는 텍스트가 바뀔 때만 설정; 사건 입력 중(`input` 이벤트)에는 미리보기 POST를 500 ms 디바운스로 늘리고 live 영역 갱신은 blur/변경 확정 시 | 소스 단언 |
| 10 | F-10 Med | `document`에 `dragover`/`drop` `preventDefault`(드롭 존 밖) | 소스 단언 |
| 11 | F-11 Med | `.seg:focus-visible { outline-offset: -3px }` | CSS 단언 |
| 12 | F-12 Med | 비교 결과: 미측정이면 큰 숫자를 맨 위에 두지 않고 「참고 신호 — 미측정」 라벨을 먼저, 숫자는 "참고 원점수 99/100" 문장 안에만; 결과 본문의 백틱 제거("결론은 폴더 검사를 사용하십시오") | 소스 단언 + 함수 실행(미측정 payload) |
| 13 | F-13 Med(거짓 문구) | `gui.html:264`·`gui.js:2361` 호증 순서 문구를 "결론순(조작·생성 → 원본성 → 판단 불가 → 분석 못 함), 같은 결론 안에서는 경로순"으로 | 소스 단언 |
| 14 | F-14·F-15 (거짓·중복 문구) | 행 사유의 세 겹 중복은 서버 문구라 0단계 판정 대상 — GUI에서는 `reasonText`에서 같은 구절 반복을 접는 표시만(원문은 상세 패널에); 상세 패널 백틱 제거, 열 수 없는 압축 파일 행에 "컨테이너 행은 구성 파일 결과의 요약" 안내 생략 | 함수 실행 |

끝에: `python -m unittest discover -s deepfake_lens/tests -p 'test_gui*.py'`, `ruff check`, `mypy`(변경 Python 있을 때),
`scripts/gui_smoke.py`를 로컬 Playwright로 한 번. Docker 게이트(`gate.sh gui`, `gate.sh full :latest`)는 조정자가 한 번에 돌린다.

### WP-B 문서 (QA-MANUAL, BACKLOG) — WP-A와 다른 파일, 병렬

- `docs/QA-MANUAL.md` QA-SYS-4: W-1 키 32자 이상, W-2 `New-Item C:\dfl\out`, W-3 실행 정책/`Activate.ps1`, W-4 기준 JSON 생성 환경 + `compare.py` 파일 제공(히어독 제거), W-5 PDF 단계 분리(pymupdf wheel), W-6 Python 3.12.10 설치 파일, W-7 끝 역슬래시, W-8 리다이렉트 금지, W-9 로그 경로·버그 보고 양식, W-10 GUI 조작법(「폴더 고르기」는 업로드), W-13 정상 화면 예시. 리뷰 §5 runbook을 절차 블록으로 교체.
- `docs/phase0/BACKLOG.md`: Low·Cosmetic(F-16~F-31, W-11~W-12, F-26·F-27)을 BL-39부터 "GUI 적대적 리뷰 7차에서 넘긴 항목" 절로.
- `scripts/qa_compare_results.py`(신규, 비게이트): `cached`·`path_b64` 제외, `\`→`/` 치환, 다른 행의 키·값 출력. `--help` 테스트 관례(`scripts/*.py` 도움말 테스트) 확인.
- 커밋: `docs(qa): GUI-REV7 W-1..W-10 …`, `docs(phase0): GUI-REV7 backlog …`.

### WP-C 인계 꾸러미 (레포 밖 `~/Desktop/deepfake-lens-windows-handover/`)

1. `python -m pip wheel . --no-deps -w dist`
2. `scripts/make_qa_manual_fixtures.py --out qa-manual` → `ditto -c -k --norsrc --noextattr qa-manual qa-manual.zip`
3. 기준 JSON 두 벌: `reference-wheel.json`(numpy·Pillow·ffmpeg 없는 새 venv, PATH에서 ffmpeg 제거) / `reference-extras.json`(.venv)
4. `pip download pymupdf numpy Pillow --platform win_amd64 --python-version 3.12 --only-binary=:all: -d win-wheels`
5. `compare.py`(= scripts/qa_compare_results.py 복사), runbook.md(§5), 기대 화면 텍스트(doctor·scan·evidence-statement·api-serve·web 출력)
6. Python 3.12.10 amd64 설치 파일은 사람이 내려받아 넣는다(이 세션은 외부 다운로드를 하지 않음).

## 순서와 소유

- WP-A: Opus 에이전트 1(메인 체크아웃, phase0에 커밋).
- WP-B + WP-C: Opus 에이전트 2(별도 worktree, 커밋은 조정자가 phase0에 cherry-pick).
- 조정자: 끝나면 Docker 게이트(`:gui` gui, `:latest` full — 한 번에 하나), 리뷰 문서 F-n 상태 갱신, 인계서 "다음 할 일"에 GUI-REV7 추가.
- 32차 검증 재개는 이 커밋들 위에서 한다(메모리 `deepfake-lens-round32-resume`).
