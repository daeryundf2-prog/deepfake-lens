# GUI UI/UX·접근성 점검 + 적대적 리뷰 (HEAD 17b3a15, 비게이트) — Windows 후임 테스트 전

작성 2026-10-22. 대상: `deepfake_lens/gui.html`·`gui.css`·`gui.js`·`webapp.py`(HEAD 17b3a15, phase0).
목적: 내일 Windows 11(Edge/Chrome, 한국어 로캘)에서 후임이 손으로 GUI를 시험한다. 시험이 첫 단계에서
깨지거나 잘못된 버그 보고가 나올 곳을 미리 찾는다. 이전 감사(`docs/phase0/ux_audit_ec0d057.md`)와
GUI 리뷰 1~5차(`docs/gui/adversarial_review_*.md`), BACKLOG BL-24~32에 있는 항목은 "잔여"로만 적었다.

## 0) 어떻게 봤나

- 실행: 이 Mac에서 `web --folder <qa-manual>`을 띄우고 Chromium(1366×768, 375×812, 683×384=200% 확대, 어두운 모드,
  Windows 대비 테마 강제 색상 에뮬레이션)으로 검사→사건 대장→감정서→빠른 검사·비교를 돌렸다. 업로드(4파일)·압축 파일·
  빠른 검사(텍스트·파일)·비교(wav/wav, wav/txt)·500개 검사 중단·새로고침까지 스크립트로 확인했다.
- 자동: axe-core 4.10(WCAG 2.0/2.1/2.2 A·AA + best-practice)을 네 뷰포트 × 6화면에 돌렸다. 결과는 한 종류뿐:
  `#detail`(aside, complementary)이 `<main>` 안에 있음(moderate, best-practice). 가로 넘침은 320px·683px에서도 0.
  텍스트 대비는 변수값으로 전부 계산해 AA(4.5:1) 이상이다. 즉 **자동 도구로는 거의 안 잡히고, 아래는 전부 손으로 찾은 것**이다.
- 정적: 세 파일 전체를 읽고 aria·초점·live 영역·innerHTML·상태 CSS를 대조했다.

## 1) 판정

**내일 시험을 막을 만한 것은 GUI 자체보다 Windows 절차 문서(QA-MANUAL QA-SYS-4)에 있다.** 그 절차를 그대로 따르면
서명 키 길이·출력 폴더·실행 정책 세 군데에서 시작도 못 한다(§3 W-1~W-3). GUI는 High 2건(F-1 스킵 링크, F-2 빠른 검사의
압축 파일이 대장을 통째로 바꿈)과 Med 10건이 있고, 모두 반나절 안에 고칠 크기다. 서명되는 산출물이 틀리는 경로는 찾지 못했다.

## 2) GUI 지적 사항

표기: 심각도 / 신규·잔여 / 위치 / 재현. WCAG는 해당할 때만.

### High

**F-1 (High, 신규) 「본문으로 건너뛰기」가 화면을 바꿔 버림** — WCAG 2.4.1·3.2.2
- `gui.html:11` `href="#main"` → `hashchange` → `gui.js:897` `showView(viewFromHash() || (results.length ? 'ledger' : 'scan'))`.
- 직접 확인: #/report에서 해시를 `#main`으로 바꾸자 감정서 화면이 숨고 사건 대장이 떴고 초점은 `sum-h`로 갔다.
  키보드·스크린리더 시험의 첫 Tab→Enter가 작업 화면을 날린다. 해시가 `#main`으로 남아 새로고침해도 돌아오지 않는다.
- 고침: 스킵 링크를 클릭 핸들러(`preventDefault` + `$('main').focus()`)로 바꾸거나 `hashchange`에서 `viewFromHash()`가 null이면 무시.

**F-2 (High, 신규) 빠른 검사에서 압축 파일을 고르면 폴더 검사 결과가 통째로 교체되고 감정서를 만들 수 없게 됨**
- 안내문 `gui.html:305` "안의 파일이 사건 대장에 행으로 **실립니다**"(추가처럼 읽힘). 실제 `gui.js:2811-2824`는 `lastScanJob = ''`로 서버 작업
  연결을 끊고 `processResults(data)`로 `results`를 교체한다. 확인 창 없음(「화면의 결과 지우기」는 `window.confirm`으로 묻는다, `gui.js:2295`).
- 이후 감정서 화면: "올린 파일만 있어 증거설명서를 만들 수 없습니다 — 폴더를 검사하십시오." 후임이 "결과가 사라졌다"고 보고할 1순위.
- 고침: 확인 창 + 문구 "지금 보이는 검사 결과를 이 압축 파일의 결과로 바꿉니다", 또는 행을 덧붙이는 방식.

### Med

**F-3 (Med, 신규) 폴더 경로 칸이 비동기로 채워져 입력이 섞임 → 403 「허용되지 않은 경로」**
- `gui.js:3008-3014`: `/api/stats` 응답 뒤 `if (!$('folder-path').value) value = default_folder`. 페이지가 뜨자마자 칸을 누르고 치면
  답이 온 순간 값이 들어가고 캐럿 위치에 입력이 끼어든다. 직접 재현: 제출된 값이
  `/private/tmp/claude-501/<내가 친 경로>-Users-…/qa` 꼴이 되어 403. 오류문은 어느 폴더가 허용인지 말하지 않는다(GUI는 `defaultFolder`를 안다).
- Windows 저사양 PC에서 후임이 `C:\사건 자료\증거 1`을 바로 치면 재현된다.
- 고침: 입력 중(`document.activeElement === field` 또는 `value !== ''`)이면 덮지 않기; 403 문구에 허용 폴더를 붙이기.

**F-4 (Med, 신규) 실패한 검사의 오류 상자가 다음 검사가 성공해도 모든 화면 위에 남음**
- `gui.js:160-168` `retireErrors()`는 "이전 검사 · "만 붙이고 지우지 않는다. 직접 확인: 403 한 번 뒤 정상 검사 완료 → 사건 대장·감정서·빠른
  검사 화면 맨 위에 계속 "이전 검사 · 오류 …". 게다가 `#error-region`이 `aria-live="assertive"`라 상자 제목을 바꿀 때마다 옛 오류가 재낭독된다.
- 고침: 새 검사가 성공으로 끝나면 `data-scan` 상자를 제거(또는 접기); 제목 수정은 live 영역 밖에서.

**F-5 (Med, 신규) 한 번 중단한 뒤에는 정상 완료 검사도 버튼이 「부분 결과 보기」**
- `gui.js:1087` 문구를 바꾸고 `progressStart`(1014-1032)·완료 분기(1073-1078)에서 되돌리지 않는다. 사실과 다른 문구.

**F-6 (Med, 신규) 「검사 시작」 이중 제출**
- `gui.js:1324-1371`: `scanBusy`는 `/api/scan` 응답 뒤에야 true. 응답 대기 중 Enter 연타·더블클릭이 두 번째 작업을 만든다. 두 폴링 루프가
  진행 패널을 번갈아 덮어쓴다. 업로드 쪽(`setBusy(true)` 동기)은 안전.

**F-7 (Med, 신규) 「최대 파일 수」 입력 상한 10000 vs 서버 상한 2000, 안내는 "늘리라"**
- `gui.html:95` `max="10000"`, `analysis_api.py:69` `WEB_MAX_SCAN_FILES = 2000`(초과는 조용히 잘림), `gui.js:317` "'최대 파일 수'를 늘려 다시
  검사하십시오"(따를 수 없음). `novalidate`라 0·빈값은 서버 400이 상단에만 뜨고 입력란과 연결(aria-invalid)되지 않는다. 3.3.1.

**F-8 (Med, 신규) Windows 대비 테마(강제 색상)에서 선택·눌림·진행 상태가 사라짐** — 1.4.1·1.4.11
- `gui.css:177-180`은 `.mk-*`만 처리. 현재 행 `gui.css:319`(배경+inset box-shadow), 필터 눌림 `:282`(배경), 검토 별 `:394`, 진행 바 `:243`,
  `.row-changed :551`, 드롭 존 `.over :223`가 전부 배경·그림자뿐.
- 직접 확인(forced-colors 에뮬레이션 캡처): 「판단 불가」 칸을 누른 상태와 3번째 행을 고른 상태가 눈에 전혀 안 보인다. Windows 11 설정→접근성→
  대비 테마를 켜고 보면 그대로다.
- 고침: 해당 상태에 `outline`/`border` 추가, 진행 바 fill에 `forced-color-adjust: none` 또는 border.

**F-9 (Med, 신규) 감정서 화면에서 사건 정보 한 글자마다 `aria-live` 안내가 재낭독되고 미리보기 POST가 나감** — 4.1.3
- `gui.html:274` `#export-note aria-live="polite"`; `gui.js:964-970` `onCaseChange → renderReport()` → `:2423` textContent 재설정(같은 문자열도
  노드 교체는 변경). 입력마다 250 ms 디바운스로 `/api/report?format=evidence-preview`.
- 고침: 값이 바뀔 때만 설정; 입력 중 live 갱신 보류.

**F-10 (Med, 신규) 드롭 존 밖에 파일을 놓으면 브라우저가 파일을 열어 결과가 사라짐**
- `gui.js:1185-1203` `dragover/drop`이 `#drop-zone`에만. 문서 수준 차단 없음. 탐색기에서 조금만 빗나가면 Edge가 `file:///…`로 이동하고
  메모리의 `results`는 사라진다(sessionStorage에는 사건·작업 id만, 새로고침 복구는 작업 id로만 된다).
- 고침: `document.addEventListener('dragover'|'drop', e => e.preventDefault())`.

**F-11 (Med, 신규) 결론 필터 4칸의 키보드 초점 링이 잘림** — 2.4.7·2.4.11
- `gui.css:266-269` `.strip { overflow: hidden }` 안에 `.seg`가 가장자리까지 차서 바깥 outline 3변이 잘린다. 고침: `.seg:focus-visible { outline-offset: -3px }`.

**F-12 (Med, 신규) 비교 결과가 큰 글씨 「99」로 시작 — "숫자 점수는 보정된 경우에만 표시" 원칙과 충돌**
- 직접 확인(wav/wav): 결과 첫 줄이 `99`, 그 아래 "참고 원점수 99/100, 미측정 매핑". 대장 하단 문구(`gui.html:233`)와 다르고, 후임·의뢰인은
  "99% 같은 사람"으로 읽는다. 또 본문에 "결론은 \`scan\`을 사용하십시오"처럼 백틱과 CLI 명령이 그대로 보인다.
- 고침: 숫자를 접고 "참고 신호(미측정)" 라벨을 앞세우기; 백틱 제거.

**F-13 (Med, 신규) "고른 순서대로 호증 번호가 붙습니다"가 사실과 다름**
- `gui.html:264`, `gui.js:2361` "고른 순서로 번호" vs `gui.js:2309-2318` `.sort(byVerdict)`(결론→이름순 고정). 고르는 단위는 그룹 체크박스 4개뿐.
- 고침: "결론순(조작·생성 → 원본성 → 판단 불가 → 분석 못 함), 같은 결론 안에서는 경로순".

### Low

- **F-14 (신규)** 행 사유 문구가 세 겹 중복: "결정 검사 미실행 — C2PA 출처 검증: C2PA SDK가 지원하지 않는 형식: C2PA SDK 오류(NotSupported): 지원하지 않는 형식"
  (bmp·flv·mkv·opus·webm·hwp·doc·ppt·xls 행). 후임은 이것을 오류로 보고한다. 한 겹("C2PA 미지원 형식")이면 충분. BL-25와 같은 계열.
- **F-15 (신규)** 상세 패널에 마크다운 백틱이 그대로: "(\`pip install deepfake-lens[archive]\`)". 열 수 없는 7z 행에 "컨테이너 행은 구성 파일 결과의 요약입니다…"
  안내가 붙어 모순.
- **F-16 (신규)** 검색 상자 placeholder가 유일한 가시 라벨인데 Chromium 기본색(라이트 ≈2.6:1, 다크 ≈2.9:1). `gui.css`에 `::placeholder` 없음. 1.4.3.
- **F-17 (신규)** CSV·빠른 검사 낭독의 결론 열이 「미지원·실패」(`gui.js:537-539`) — 화면은 「분석 못 함」, 중복 행도 "실패"로 적힘. CSV 파일명 열이 경로가
  아니라 이름(`gui.js:2287`)이라 하위 폴더의 동명 파일을 구별 못 함.
- **F-18 (신규)** 내보내기 메뉴의 「결과 JSON」·「결과 CSV」 뒤 초점이 body로 떨어짐(`gui.js:2260-2262` `closeMenu()`); 확인 목록의 「이 폴더의 사건이 맞음」도
  `renderChecks`가 버튼을 지워 초점 유실(`gui.js:2327`). 2.4.3.
- **F-19 (신규)** 데스크톱 폭에서 행 Enter가 오른쪽 패널만 바꾸고 알림·초점 이동 없음(`gui.js:1619`); 거르기·정렬 뒤 남은 개수가 낭독되지 않음(`gui.js:1664`). 4.1.3.
- **F-20 (신규)** 「검사 범위 n/N」의 실패 여부가 빨간색뿐(`gui.css:327`), 열 머리글도 없어 후임에게 "4/15"가 무슨 뜻인지 안 보인다(힌트는 상단 한 줄뿐). 1.4.1.
- **F-21 (신규)** 비교 슬롯 버튼 이름이 파일명으로 바뀌어 A/B 구분이 사라짐(`gui.js:2941`); 비교·빠른 검사 실행 중 상태(`#cmp-status`·`#qc-status`)가 live가
  아니고 수십 초 무응답; 빠른 검사 파일 경로에서는 「워터마크 키」가 조용히 무시됨(`gui.js:2776-2778`).
- **F-22 (신규)** 업로드 묶음(20개 단위) 중 하나가 실패하면 앞 묶음 결과도 버림(`gui.js:1416-1442`); 칩 용량이 "0K"·"307200K"(`gui.js:1224`); 클라이언트
  사전 용량 검사 없음(256 MiB 상한).
- **F-23 (신규)** 토큰 모달: 틀린 토큰에도 "틀렸다" 없이 같은 모달 재표시(`gui.js:229-235`).
- **F-24 (신규)** 검토 메모·판정이 localStorage에 평문 누적(`gui.js:36-57`, 키에 검사 폴더 절대경로 포함)되고 GUI에서 지울 수단 없음. 공용 PC 시험 뒤 남는다.
- **F-25 (신규)** 용어 불일치: 토스트 "원장"(화면은 사건 대장/파일 대장), "뉴럴 모델"/"신경망 탐지기", "화자 모델"; 빠른 검사 텍스트 결과의 파일명 자리가
  `pasted-text`; 포렌식 스튜디오 버튼이 `.active`만(aria-pressed 없음)이고 라벨 "히트맵 (Grad-CAM/ELA)"는 픽셀 휴리스틱을 과장.
- **F-26 (신규)** 증거설명서 미리보기·PDF의 행마다 내부 문구가 그대로 실림: "외부 모델(AASIST …) 미실행: 모델 실행 불가: … 0단계: 측정 게이트(클래스당 200건,
  AUROC 95% CI 하한 0.85) 미충족 — 2차 계획 측정 단계 전까지 비활성.;" — 법원 제출 문서의 가독성 문제(서버 서식, 0단계 판정 대상).
- **F-27 (신규)** `#detail`(aside)이 `<main>` 안에 있어 landmark 중첩(axe moderate). `<main>` 밖으로 빼거나 `role="region"`+이름.

### Cosmetic

- **F-28** 모노 글꼴 스택(`gui.css:33`)에 한글 글꼴이 없어 Windows에서 Consolas+맑은 고딕 혼합 렌더링(사건줄 메타, "/ – 파일", 해시 줄).
- **F-29** `<h1>` 이름이 "1 / 2사건 정보"(공백 없음, `gui.html:41`); 결과 없을 때 `aria-labelledby="sum-h"`가 빈 h1을 가리킴.
- **F-30** 진행 바 초기 `aria-valuemax="0"`에 valuetext 없음(`gui.html:143`).
- **F-31** 어두운 모드에서 "분석 완료 — 51개 파일" 토스트가 상세 패널 오른쪽을 4초 가림(BL-32 잔여, 캡처로 재확인).

### 잔여(이전 보고, 이 HEAD에도 그대로)
U-13(신경망 탐지기 기본 ON인데 0종 실행), BL-27(검사 실패 뒤 이전 결과 안내 없음), BL-31("검사 중 시작" 낭독), BL-32(토스트 가림), BL-25(사유 문구 순서).

### 이상 없음으로 확인한 것
innerHTML 조립 전부 이스케이프; `<dialog>` 초점 트랩·Escape·취소; 대장 roving tabindex(↑↓/Home/End, J/K 토글); 내보내기 메뉴 Escape 닫힘; 토스트
role=status, 오류는 지속 영역; `prefers-reduced-motion`; 드롭 존 키보드 대안; 해시 전환 시 사건 입력값 보존; 새로고침 뒤 작업 복구(sessionStorage job id);
중단 흐름(배너·"부분 결과" 문구·감정서 확인 목록 "주의" 모두 일치); 320px·200% 확대에서 가로 넘침 없음; 텍스트 대비 전부 AA 이상; 44px 터치 크기.

## 3) Windows 시험 절차(QA-MANUAL QA-SYS-4)의 문제 — 내일 당장 막히는 것

- **W-1 (High)** 절차의 서명 키 `qa-sys-4-key`(12바이트)는 `signing.py:110` 최소 32바이트 미달 → `web`·`api-serve`·`evidence-statement`가 exit 2
  (`cli.py:659-664`). 32자 이상 키로 바꾸거나 키 없이 진행.
- **W-2 (High)** `C:\dfl\out`을 만들지 않아 `scan --json-out …`이 "출력 폴더가 없습니다"로 exit 2(`cli_inputs.py:89`). `New-Item C:\dfl\out` 추가.
- **W-3 (High)** PowerShell 5.1 기본 실행 정책에서 `Activate.ps1`이 막힘. `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 또는 `venv\Scripts\python.exe -m deepfake_lens …`로 통일.
- **W-4 (High)** 통과 기준 5 "Linux와 동일"은 지금 절차로는 불가: wheel만 깐 Windows에는 numpy/Pillow/ffmpeg가 없어 coverage 사유가 달라진다. 기준 JSON을
  같은 설치 상태로 만들고, 비교 스크립트에 `\`→`/` 치환과 `path_b64` 제외를 넣어 파일로 제공(히어독은 PowerShell에서 안 됨).
- **W-5 (High)** PDF는 깨끗한 Windows에서 못 만든다: pymupdf가 어떤 extra에도 없다(게이트 lock에만). GUI는 PDF 버튼 비활성·501. 이 Mac도 `pdf_available=false`라
  **PDF 경로는 이번 리뷰에서 확인하지 못했다.** `pymupdf` win_amd64 wheel을 USB로 주고 별도 단계로.
- **W-6 (Med)** python.org 첫 화면은 3.14; 절차의 `py -3.12`는 3.12.10 설치 파일을 따로 줘야 한다. 3.13/3.14 미검증(`requires-python >=3.11`, CI 3.11/3.12).
- **W-7 (Med)** 탭 완성이 만드는 끝 역슬래시(`"C:\사건 자료\증거 1\"`)가 PS 5.1에서 따옴표를 먹어 "폴더를 찾을 수 없습니다 …1\"". `web --folder`도 같음.
- **W-8 (Med)** 한글은 콘솔에선 정상(UTF-8 reconfigure)이나 `> file`·`Out-File`로 받으면 cp949로 깨져 **오보고** 가능. 캡처로만 판단하게 지시.
- **W-9 (Med)** `web`은 요청·오류를 터미널에 안 찍는다(`webapp.py:499`). 로그는 `C:\Users\<사용자>\.cache\deepfake-lens\logs\deepfake-lens.log`
  (`--folder`가 프로필 안이면 `C:\ProgramData\deepfake-lens\<사용자>\logs\`). 버그 보고 양식에 이 파일을 넣어야 한다.
- **W-10 (Med)** GUI 「폴더 고르기」는 폴더 검사가 아니라 업로드(최대 20개, 서명 제외, 증거설명서 불가). 후임은 이 버튼을 누른다. 폴더 검사는 "폴더 경로"
  상자(`--folder`가 미리 들어감) + Enter라고 명시.
- **W-11 (Low)** OneDrive 리디렉션 경로(바탕 화면·문서)는 미검증 영역(HANDOFF §"Windows 경로는 mock으로만"). `C:\사건 자료\…`처럼 프로필 밖 경로만.
- **W-12 (Low)** Mac에서 zip/Finder 압축은 `__MACOSX`·`._*`를 넣어 행 수가 늘어난다. `ditto -c -k --norsrc --noextattr` 사용.
- **W-13 (Low)** `doctor`가 "실행 가능: 0/10", MISS 다수로 보이고 scan마다 stderr 경고 2줄이 나오는 것이 정상임을 기대 화면으로 박아 둘 것.
- Windows CI가 보장하는 범위: wheel-only·3.12·단위 테스트만. nt에서 41개 skip, GUI 브라우저 스모크·ffmpeg·numpy·pymupdf는 Linux뿐. **Windows+실브라우저+GUI 조합의 기록은 없다.**

## 4) 오늘 Mac에서 준비할 것

1. `python -m pip wheel . --no-deps -w dist` (현재 `dist/` 없음).
2. 기준 JSON 두 벌: wheel만(ffmpeg PATH 제거) / extras 포함. `scripts/make_qa_manual_fixtures.py`는 테스트 패키지가 wheel에서 빠져 Windows에서 못 돌린다 — 여기서 만들어 `ditto`로 압축.
3. `compare.py` 파일(구분자 치환·`cached`·`path_b64` 제외·다른 행 출력).
4. Python 3.12.10 amd64 설치 파일, pymupdf/numpy/Pillow win_amd64 wheel(`pip download … --platform win_amd64 --python-version 3.12 --only-binary=:all:`).
5. 기대 화면 캡처: doctor 표, scan 표+stderr 2줄, evidence-statement 표, api-serve exit 2 문구, web 시작 2줄, GUI 사건 대장.
6. QA-MANUAL.md QA-SYS-4 블록 수정(W-1~W-5, W-7) + 초보자용 runbook(§5).

## 5) 후임용 Windows runbook (QA-MANUAL에 넣을 초안)

```powershell
# USB: python-3.12.10-amd64.exe, deepfake_lens-0.1.0-py3-none-any.whl, qa-manual.zip, reference.json, compare.py, (선택) pymupdf wheel
# 1) Python 설치: "Add python.exe to PATH" 체크, 마지막 화면 "Disable path length limit"
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force
[Console]::OutputEncoding = [Text.Encoding]::UTF8; $OutputEncoding = [Text.Encoding]::UTF8
New-Item -ItemType Directory C:\dfl\out, "C:\사건 자료\증거 1" -Force | Out-Null
py -3.12 -m venv C:\dfl\venv
C:\dfl\venv\Scripts\Activate.ps1
pip install C:\dfl\dist\deepfake_lens-0.1.0-py3-none-any.whl
deepfake-lens --help; "exit=$LASTEXITCODE"                        # 0
deepfake-lens doctor; "exit=$LASTEXITCODE"                        # 0. "실행 가능: 0/10"·MISS 다수가 정상
Expand-Archive C:\dfl\qa-manual.zip C:\dfl\
Copy-Item C:\dfl\qa-manual\formats\* "C:\사건 자료\증거 1\"
deepfake-lens scan "C:\사건 자료\증거 1" --recursive --json-out C:\dfl\out\windows.json --html-out C:\dfl\out\scan.html --evidence-statement-out C:\dfl\out\statement.md; "exit=$LASTEXITCODE"
#   성공: exit 0, 표 출력, stderr에 "기본 엔진 프로필…"·"경고: 임계값 프로필 … in-sample" 두 줄(오류 아님), 파일 3개
$env:DEEPFAKE_LENS_REPORT_KEY = "qa-sys-4-key-0123456789abcdef0123456789abcdef"   # 32자 이상
deepfake-lens evidence-statement C:\dfl\out\windows.json --json-out C:\dfl\out\statement.json --format table; "exit=$LASTEXITCODE"
deepfake-lens api-serve; "exit=$LASTEXITCODE"                     # 2 + "pip install fastapi uvicorn" 한국어 안내, traceback 없음
deepfake-lens web --folder "C:\사건 자료\증거 1"                  # 끝에 \ 붙이지 말 것. 이 창은 그대로 두고:
#   Edge/Chrome에서 http://127.0.0.1:8765/gui → "폴더 경로" 상자에 경로가 이미 있음 → Enter
#   "폴더 고르기" 버튼은 업로드용(서명 제외) — 이번 시험에서는 누르지 말 것
#   성공: 진행 표시 → 사건 대장에 행들 → 감정서 만들기에서 HTML·서명된 JSON 내려받기(PDF 버튼 비활성은 pymupdf 없으면 정상)
#   종료: Ctrl-C → "중단됨(사용자 요청)…", exit 130이 정상
cmd /c "chcp 949 & C:\dfl\venv\Scripts\deepfake-lens.exe doctor"  # 한글 깨짐은 화면 캡처로만 판단(파일로 리다이렉트 금지)
python C:\dfl\compare.py C:\dfl\reference.json C:\dfl\out\windows.json   # "동일"
```

버그 보고에 넣을 것: ① 실행한 명령 그대로 ② `exit=` 값 ③ 화면 캡처 ④ 로그 파일(§3 W-9) ⑤ `C:\dfl\out\*` ⑥ GUI 오류는 F12 → Network의 `{"error": …}` 본문
⑦ `python --version`, `winver`.

## 6) 시험 전에 먼저 고칠 10가지

1. F-1 스킵 링크 2. F-2 빠른 검사 압축 파일 교체(확인 창+문구) 3. W-1~W-3 절차 수정(키 길이·출력 폴더·실행 정책) 4. F-3 폴더 칸 비동기 덮어쓰기+403 문구
5. F-4 오류 상자 잔존 6. F-5 「부분 결과 보기」 잔존 7. F-8 대비 테마 상태 표시 8. F-13 호증 순서 문구 9. F-6 이중 제출 10. F-10 문서 수준 drop 차단.

## 부록 — 실행·확인한 것
- `web --folder <scratch>/qa --port 8765 --verbose` (pdf_available=false). 폴더 검사 51행(formats), 업로드 4파일→6행, 압축 구성원 행, 500개 중단(428행/72 미검사,
  배너·확인 목록·낭독 일치), 새로고침 복구, 빠른 검사(텍스트·jpg), 비교(wav/wav=99, wav/txt=400 안내), 내보내기 메뉴·필터 칸·env 상세, 서명된 JSON 내려받기.
- axe-core 4.10 × {1366 밝음, 1366 어둠, 320, 683(200%)} × {scan, scan-running, ledger, ledger-detail, report, tools}: 위반 1종(F-27).
- forced-colors 에뮬레이션 캡처 3장(scan·ledger·report), 어두운 모드·375px·200% 캡처.
- 정적 판독: gui.html 전체, gui.css 전체, gui.js 전체, webapp.py, webapp_api.py 읽기 루트·상한·stats; cli.py, signing.py, cli_inputs.py, pyproject, CI yml.
