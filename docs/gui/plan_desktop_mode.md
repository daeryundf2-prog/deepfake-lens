# 일반 사용자용 「데스크톱 모드」 구현 계획 (DESK)

작성 2026-10-23. 기준 phase0 HEAD 149e4d0(GUI-REV7 포함). 목표: **터미널을 모르는 사람이 아이콘 하나로 켜고, 폴더를 고르고,
감정서를 받는다.** 지금은 서버 시작(명령줄), 폴더 경로 타이핑, PDF 엔진 설치, 서명 키 환경 변수, 종료(Ctrl-C)가 전부 사용자
몫이다. 이 계획은 그 다섯 가지를 없애고, 생길 수 있는 문제를 미리 막는다.

역할: 계획과 서버 핵심(보안이 걸린 부분)은 Fable, GUI·꾸러미·문서 구현은 Opus, 작은 작업(테스트 보강·문구·문서 절)은 Sonnet.

## 0) 설계 원칙 (바꾸지 않는 것)

- 원본 폴더에 쓰지 않는다. 검사는 등록된 읽기 루트 안에서만(G31). 루트는 **사용자가 이 PC의 OS 대화상자로 고른 폴더**로만 늘어난다.
- 서버는 루프백에만 묶인다. 새 API는 모두 `desktop=True`로 시작한 서버에서만 켜지고, 기존 `X-Deepfake-Lens-Client` 헤더 검사를 그대로 받는다
  (다른 사이트의 페이지는 커스텀 헤더를 못 붙이므로 폴더 대화상자·종료를 못 건드린다).
- 보고서는 서버가 보관한 검사 작업으로만 만든다(P0R-6). 서명은 HMAC-SHA256 그대로. **키는 사용자 데이터 폴더의 키 파일**에서 온다(아래 3).
- 모든 사용자 문구는 한국어, 「」는 보간값에만(SourceQuotesTest). 테스트 약화 없음. 게이트 네 개 통과.

## 1) 사용자가 보게 될 형태

### Windows (주 대상)
`DeepfakeLens-win64.zip` 하나. 풀면 폴더 하나:
```
DeepfakeLens-win64\
  Deepfake Lens.cmd          ← 더블클릭: 서버 시작 + 브라우저 자동 열림. 창 제목 "Deepfake Lens — 이 창을 닫으면 프로그램이 꺼집니다"
  문제 확인.cmd              ← doctor 결과를 창에 보여 주고 로그 폴더를 연다
  읽어보세요.txt             ← 3줄 사용법 + 문제 생겼을 때
  python\                    ← python.org 임베더블 3.12.10 x64 (설치 불필요, 관리자 권한 불필요)
  python\Lib\site-packages\  ← deepfake_lens + pymupdf + Pillow + numpy + c2pa-python + py7zr + syhwp/olefile
```
- 설치 없음, 레지스트리·PATH 변경 없음, 관리자 권한 불필요. 지우려면 폴더 삭제.
- 사용자 기록(검토·피드백·서명 키·검사 작업)은 `%APPDATA%\deepfake-lens\`에 남아 꾸러미를 바꿔도 유지된다.

### macOS (개발 검증용, 같은 코드)
`Deepfake Lens.command`(venv 생성 + 실행). 이 Mac에서 끝까지 실행해 본다.

### 화면 흐름 (브라우저)
1. 자동으로 `http://127.0.0.1:<포트>/gui`가 열린다. 상단에 **「이 PC에서 실행 중 · 종료」** 버튼.
2. 검사 화면 맨 위 **「검사할 폴더 고르기」** 큰 버튼 → OS 폴더 대화상자 → 고르면 경로 칸이 채워지고 바로 검사 시작(옵션은 그대로).
   그 아래 "지금 검사할 수 있는 폴더: …" 목록. 기존 경로 칸은 "직접 입력"으로 남긴다.
3. 결과·감정서는 지금과 같다. PDF 버튼은 꾸러미에 pymupdf가 있으므로 항상 켜져 있고, 서명 키는 자동이라 "서명됨"이 기본.
4. 「종료」 → 확인 창 → 서버가 꺼지고 화면에 "종료되었습니다. 이 탭을 닫으십시오." 콘솔 창도 닫힌다.
5. 콘솔 창을 그냥 닫아도 된다(서버 종료). 브라우저 탭만 닫으면 서버는 남는다 → 20분 동안 아무 화면도 열려 있지 않고 검사도 없으면 스스로 꺼진다.

## 2) 서버 (Fable 구현) — API 계약

`run_server(..., desktop: bool = False)` → `build_server(..., desktop=desktop)`. `desktop=False`면 아래 경로는 전부 404(기존 동작 불변).

| 경로 | 조건 | 동작 |
|---|---|---|
| `GET /api/stats` | 항상 | 기존 필드 + `desktop: bool`, `roots: [str]`(등록된 읽기 루트, 표시용 텍스트), `signing: {"available": bool, "source": "env" \| "file" \| null, "key_id": str \| null}`, `idle_exit_minutes: int \| null` |
| `GET /api/roots` | desktop | `{"roots": [{"path": str, "path_b64": str}]}` |
| `POST /api/pick-folder` | desktop, 루프백, 본문 없음 | 이 PC에 OS 폴더 대화상자를 띄운다(동시에 하나만 — 이미 떠 있으면 409 `{"error": "폴더 선택 창이 이미 열려 있습니다"}`). 사용자가 고르면 읽기 루트로 등록하고 `{"folder": str, "folder_b64": str}`; 취소하면 `{"folder": null}`; 대화상자를 띄울 수 없으면(헤드리스, 도구 없음) 501 `{"error": "이 환경에서는 폴더 선택 창을 열 수 없습니다 — 경로를 직접 입력하십시오"}` |
| `POST /api/shutdown` | desktop, 루프백 | 검사 작업이 실행 중이면 409 `{"error": "검사가 진행 중입니다 — 중단한 뒤 종료하십시오", "job_id": …}`; 아니면 `{"status": "shutting_down"}`을 보낸 뒤 서버를 멈춘다 |
| `POST /api/heartbeat` | desktop | GUI가 30초마다 보낸다. 마지막 수신 시각을 기록(유휴 종료용). `{"status":"ok"}` |

대화상자 구현(`deepfake_lens/desktop_dialog.py`): tkinter를 쓰지 않는다(임베더블 Python에 없음, macOS는 메인 스레드 제약).
- Windows: `powershell -NoProfile -STA -Command` + `System.Windows.Forms.FolderBrowserDialog`(`ShowNewFolderButton=false`, `UseDescriptionForTitle`, TopMost 소유 창) → 선택 경로를 UTF-8로 stdout. 실행 정책과 무관(-Command). 60초 상한 없음(사용자가 고르는 동안 대기), 프로세스는 요청 스레드가 기다린다.
- macOS: `osascript -e 'POSIX path of (choose folder with prompt "검사할 폴더를 고르십시오")'`.
- Linux: `zenity --file-selection --directory`, 없으면 `kdialog`, 없으면 501.
- 반환 경로는 `_require_read_root`와 같은 `expand_user(...).resolve()`로 정규화한 뒤 `_register_read_root`. 루트 상한(64) 도달 시 가장 오래된 것을 내보내는 기존 규칙은 데스크톱 모드에서 **현재 검사 작업의 루트는 내보내지 않도록** 보호한다(아니면 진행 중 검사의 미리보기가 403).
- 대화상자가 브라우저 뒤에 숨는 문제: Windows는 TopMost 소유 창, macOS는 `osascript`가 앞으로 온다. GUI는 요청 중 "폴더 선택 창이 떠 있습니다 — 보이지 않으면 작업 표시줄을 확인하십시오"를 보인다.

`deepfake-lens desktop` 명령(`cli_parser`·`cli.py`, 게이트 명령 목록 `GATE_COMMANDS`에 추가):
- 옵션: `--folder`(선택), `--port`(기본 8765; 사용 중이면 8766…8785 중 빈 포트), `--no-browser`, `--idle-exit-minutes`(기본 20, 0이면 끄기), `--models-dir`.
- 순서: (1) 서명 키 — `DEEPFAKE_LENS_REPORT_KEY`가 있으면 그대로; 없으면 `user_data_dir()/report-key`를 읽고, 없으면 `secrets.token_hex(32)`(64자)로 만든다(POSIX 0600, Windows는 `%APPDATA%` 상속 ACL — 소유자 전용). 사용자 데이터 폴더가 없으면(상대 HOME 등) 키 없이 "서명 없음"으로 계속하고 콘솔에 한 줄. 만든 키는 **프로세스 환경 변수로만** 넣는다(`os.environ[REPORT_KEY_ENV]`), 자식 프로세스에 그대로 상속되는 것은 기존 web과 같다. (2) 포트 선택 — 이미 이 도구의 서버가 그 포트에 떠 있으면(`/api/stats`에 `status: ok`와 `version`) 새로 띄우지 않고 그 주소로 브라우저만 연다("이미 실행 중"). (3) `build_server(desktop=True)` (4) 서버 스레드 시작 뒤 `webbrowser.open(url)`; 실패하면 URL을 크게 출력. (5) 콘솔에 한국어 안내(주소, 종료 방법, 로그 파일 경로). (6) 유휴 감시 스레드: 마지막 heartbeat/요청 뒤 N분, 검사 작업 없음 → 콘솔에 "N분 동안 사용이 없어 종료합니다" 후 shutdown. (7) Ctrl-C·창 닫기(SIGBREAK/SIGTERM)는 기존 종료 처리(exit 130).
- 콘솔 출력 인코딩: 기존 UTF-8 reconfigure. `.cmd`는 `chcp 65001 >nul`.

## 3) 서명 키 파일 (Fable)

- `signing.py`: `desktop_key_file() -> Path | None`(= `user_data_dir()/report-key`), `load_or_create_desktop_key() -> tuple[bytes | None, str]`(source: "env"/"file"/"created"/"none"). 키 파일은 hex 64자 + 줄바꿈. 읽을 수 없거나 32바이트 미만이면 **덮어쓰지 않고** 오류로 중단(exit 2, "키 파일이 손상되었습니다: <경로> — 지우면 새로 만듭니다"): 이전 감정서의 검증 가능성을 지키기 위해서다.
- `verify-report`는 `--key-file %APPDATA%\deepfake-lens\report-key`로 검증한다(문서). 같은 PC·같은 계정에서만 검증되는 키라는 점을 감정서 확인 목록과 README에 적는다.
- 서버 문서(`docs/deepfake-lens-service.md`)에 "데스크톱 모드" 절 추가.

## 4) GUI (Opus A)

- 상단 바: `desktop`이면 「이 PC에서 실행 중」 표시와 **「종료」** 버튼. 누르면 `confirm` → `/api/shutdown` → 성공 시 화면 전체를 "종료되었습니다. 이 탭을 닫으십시오." 안내로 바꾼다(모든 버튼 비활성). 409면 그 사유를 오류 영역에.
- 검사 화면: 폴더 경로 칸 위에 **「검사할 폴더 고르기」**(btn-primary, 데스크톱에서만 보임) → `/api/pick-folder` → 성공 시 경로 칸 채움 + `#scan-form` submit. 요청 중에는 버튼 busy + 안내문. 취소면 아무 변화 없음. 501이면 안내문을 경로 칸 힌트에.
  "지금 검사할 수 있는 폴더" 목록(`/api/stats.roots`)을 경로 칸 힌트 자리에(최대 5개, 나머지는 "외 n개"). 403 문구는 이 목록으로.
- 업로드 블록: 제목 「파일 올려서 검사(서명 제외 — 감정서 불가)」, 버튼 「올릴 파일 고르기」「올릴 폴더 고르기」. 데스크톱이면 이 블록은 `<details>`로 접어 둔다.
- 연결 감시: 데스크톱이면 30초마다 `/api/heartbeat`; 실패가 2번 이어지면 상단 오류 영역에 "서버와 연결이 끊겼습니다 — 바탕 화면의 Deepfake Lens를 다시 실행하십시오"(지속, data-scan 아님). 다시 성공하면 제거.
- 상태 줄: 버전 옆에 "PDF 가능/불가", "서명 자동/서명 없음"을 작은 글씨로. 감정서 확인 목록에 서명 키 출처 한 줄("서명 키: 이 PC의 사용자 폴더 — 다른 PC에서는 검증되지 않음").
- 첫 화면 빈 상태: 결과도 등록 폴더도 없으면 검사 화면 상단에 3단계 안내("1 폴더 고르기 → 2 결과 확인 → 3 감정서").
- 테스트: `test_gui_desktop.py`(소스 단언 + node), `gui_smoke.py`에 데스크톱 단계(서버를 `desktop=True`로 띄우고 pick-folder는 `DEEPFAKE_LENS_PICK_FOLDER_STUB=<경로>` 환경 변수로 대화상자를 대신한다 — 서버 쪽에 스텁 훅을 둔다).

## 5) 꾸러미 빌더와 실행 파일 (Opus B)

`scripts/build_desktop_bundle.py --platform windows --out dist/DeepfakeLens-win64` (비게이트, `--help` 한국어):
1. python.org 임베더블 zip 내려받기(URL·sha256 고정: `python-3.12.10-embed-amd64.zip`), 풀기.
2. `python312._pth`에 `Lib\site-packages`와 `import site` 추가(임베더블은 기본으로 site를 끈다 — 이게 가장 흔한 함정).
3. `pip install --target python\Lib\site-packages --platform win_amd64 --python-version 3.12 --only-binary=:all: --no-deps` 로 휠 설치: 이 레포 휠(`pip wheel . --no-deps`) + `pymupdf==1.28.2 numpy==2.5.4 Pillow==12.3.0 c2pa-python py7zr syhwp olefile`(의존성은 명시 목록으로 — `--no-deps`라 빠뜨리면 ImportError; 빌더가 끝에 **레코드 검사**: 각 패키지의 `Requires-Dist`를 읽어 목록에 없는 필수 의존성이 있으면 실패).
4. 실행 파일 생성: `Deepfake Lens.cmd`(아래), `문제 확인.cmd`, `읽어보세요.txt`, `VERSION.txt`(커밋 SHA).
5. zip으로 묶기(`zipfile`, 경로 구분자 `/`, 한글 이름 UTF-8 플래그).
6. 검사: 폴더 안 `python.exe` 존재, `._pth` 내용, site-packages에 `deepfake_lens/gui.js`와 `pymupdf`, 총 크기 출력. **Windows에서 실행은 못 하므로** 이 Mac에서 할 수 있는 검증은 여기까지 — 사람이 Windows에서 「Deepfake Lens.cmd」를 한 번 눌러 보는 것이 첫 시험이다(QA-SYS-4에 단계 추가).

`Deepfake Lens.cmd`:
```bat
@echo off
chcp 65001 >nul
title Deepfake Lens — 이 창을 닫으면 프로그램이 꺼집니다
cd /d "%~dp0"
if not exist "python\python.exe" ( echo python 폴더가 없습니다. 압축을 전부 풀었는지 확인하십시오. & pause & exit /b 2 )
"python\python.exe" -X utf8 -m deepfake_lens desktop %*
if errorlevel 1 ( echo. & echo 프로그램이 오류로 끝났습니다. 위 내용을 캡처해 보내 주십시오. & pause )
```
- `-X utf8`: 콘솔·파일 기본 인코딩 UTF-8. `%*`로 `--folder` 전달 가능(폴더를 이 .cmd에 끌어 놓으면 그 폴더로 시작: `%1`이 폴더면 `--folder "%~1"`).
- SmartScreen: zip을 내려받으면 Mark-of-the-Web이 붙어 `.cmd` 실행 때 경고. 「읽어보세요.txt」에 "추가 정보 → 실행" 또는 zip 속성의 "차단 해제"를 적는다. USB로 복사하면 대부분 안 붙는다.
- 꾸러미 경로에 한글·공백: `cd /d "%~dp0"`와 따옴표로 처리. OneDrive 아래에 풀면 느리고 placeholder 문제 → 「읽어보세요」에 "C:\DeepfakeLens 처럼 로컬 폴더에".

macOS `Deepfake Lens.command`: `python3 -m venv .venv`(처음 한 번, 네트워크로 휠 설치 — 또는 미리 만든 venv) → `desktop`. 이 Mac에서 실제로 실행해 흐름을 검증한다.

## 6) 생길 수 있는 문제와 대책 (전부 구현·문서에 반영)

| # | 문제 | 대책 |
|---|---|---|
| P1 | 8765 포트 사용 중 | 8765~8785에서 빈 포트 선택; 이미 이 도구가 떠 있으면 브라우저만 연다 |
| P2 | 브라우저가 안 열림(기본 브라우저 없음, 정책) | `webbrowser` 실패 시 콘솔에 주소를 크게 출력하고 "복사해서 주소창에 붙여 넣으십시오" |
| P3 | 사용자가 콘솔 창을 닫음 | = 종료. 진행 중 검사는 사라진다(작업 기록은 남음). 창 제목과 「읽어보세요」에 명시 |
| P4 | 브라우저 탭만 닫고 서버가 남음 | heartbeat 기반 유휴 종료(기본 20분, 검사 중엔 안 함) + 다음 실행 때 "이미 실행 중" 감지 |
| P5 | SmartScreen/백신이 .cmd 차단 | 「읽어보세요」 안내; `.cmd`는 서명하지 않음(서명 인증서 없음) — 문서에 한계로 |
| P6 | 임베더블 Python이 site-packages를 못 봄 | `._pth` 수정 + 빌더 검사 + 첫 실행 자동 점검(`desktop`이 `import pymupdf` 등을 시도해 "PDF 가능" 표시) |
| P7 | 폴더 대화상자가 브라우저 뒤에 숨음 | TopMost 소유 창(Windows), GUI 안내문 |
| P8 | PowerShell 실행 정책 | `-Command`는 정책과 무관(스크립트 파일이 아님). 그래도 실패하면 501 → 직접 입력 안내 |
| P9 | 폴더에 권한 없는 파일·잠긴 파일(`~$`) | 기존 행 처리(읽기 실패 행). 문서화 |
| P10 | 한글·공백·NFD 경로, UNC, 긴 경로 | 기존 경로 처리 유지; 대화상자 반환값은 `resolve()`. UNC(네트워크 드라이브)는 느림 경고만 |
| P11 | 2000개 상한 | GUI F-7 안내 그대로("폴더를 나누어") |
| P12 | 서명 키 파일 손상·삭제 | 손상: 중단+안내, 삭제: 새 키(이전 감정서는 그 키로 검증 불가 — 확인 목록에 키 id 표시) |
| P13 | `%APPDATA%` 없음/상대 HOME | 서명 없음으로 계속 + 콘솔 안내(기존 UserDataRefused 규칙과 같음) |
| P14 | 두 번 더블클릭 | P1의 "이미 실행 중" |
| P15 | 절전·화면 잠금 중 검사 | 서버는 계속; 브라우저가 heartbeat를 못 보내도 검사 중엔 유휴 종료 안 함 |
| P16 | 32비트·ARM Windows | 지원 안 함(x64만) — 「읽어보세요」 첫 줄. `desktop`은 `platform.machine()`이 x64가 아니면 경고 |
| P17 | 로그가 어디 있는지 모름 | 콘솔 첫 화면과 「문제 확인.cmd」가 경로를 보여 주고 폴더를 연다(`explorer`) |
| P18 | 디스크 부족 | 검사 작업 보관·로그 실패는 기존 UserDataWriteFailed 경로(409/507) — GUI 오류 영역 |
| P19 | 사용자가 꾸러미를 OneDrive·네트워크 드라이브에 풀음 | 「읽어보세요」; `desktop`이 실행 경로가 OneDrive 아래면 경고 한 줄 |
| P20 | 접근성 | 새 버튼·안내는 GUI-REV7 규칙(라벨·live·초점) 그대로; 종료 뒤 화면은 `role=alert` |
| P21 | 보안: 다른 사이트가 `/api/pick-folder`·`/api/shutdown`을 부름 | 커스텀 헤더 필수(프리플라이트 미응답) + Host 검사 + 루프백 전용; `--allow-lan`과 `desktop`은 함께 쓸 수 없음(인자 오류) |
| P22 | 대화상자 프로세스가 안 끝남 | 요청 스레드가 기다리되, 두 번째 요청은 409; 종료 시 자식 프로세스 kill |

## 7) 순서

1. Fable: 2·3(서버·키·desktop 명령) + 단위 테스트(`test_desktop_mode.py`: stats 필드, 404 when not desktop, pick-folder 스텁·409·501, shutdown 409/200, 키 파일 생성·손상, 포트 선택, 유휴 종료 판단 함수). 커밋 `feat(desktop): DESK-1 …`.
2. Opus A(GUI)와 Opus B(꾸러미·실행 파일·문서) 병렬. 각자 Sonnet 워커에게 테스트·문서 절을 맡긴다.
3. 조정자: macOS `.command`로 끝까지 실행, 게이트 4개, 인계 꾸러미에 `DeepfakeLens-win64.zip` 추가, QA-SYS-4에 "꾸러미 경로" 단계 추가.
