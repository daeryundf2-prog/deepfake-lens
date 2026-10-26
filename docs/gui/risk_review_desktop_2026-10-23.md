# 데스크톱 모드·꾸러미 위험 검토 (커뮤니티·이슈 트래커·코드 대조) — 2026-10-23

대상: phase0 0b4c468 (`deepfake-lens desktop`, `DeepfakeLens-win64.zip`). 방법: 조사 에이전트 넷이 Microsoft 문서, CPython/PyMuPDF/py7zr/rarfile/Edge 이슈,
SigmaHQ 규칙, 국내 포럼(kldp·클리앙·보안 FAQ), 유사 프로젝트(SD-WebUI·ComfyUI·Streamlit·Jupyter)의 이슈를 뒤지고, 빌드된 꾸러미의
바이너리(objdump import 표)와 코드를 대조했다. 각 항목의 **판정**: 확인(코드·문서·실측으로 확인) / 개연(근거는 있으나 Windows 실측 전) / 추정.
Windows에서는 아직 한 번도 돌리지 않았다 — 이 문서의 "실측 필요" 항목이 QA-SYS-4 꾸러미 경로에서 확인할 목록이다.

## A. 내일 시험을 그 자리에서 막는 것 (Critical·High) — 전부 이번 커밋에서 고침

| # | 문제 | 판정 | 근거 | 조치 |
|---|---|---|---|---|
| A1 | **깨끗한 PC에서 PDF 감정서가 안 됨.** PyMuPDF의 `_mupdf.pyd`·`mupdfcpp64.dll`·`_extra.pyd`가 `MSVCP140.dll`을 import하는데 임베더블 Python에도 꾸러미에도 없다. 개발 PC에는 VC++ 재배포가 깔려 있어 안 드러난다. 게다가 14.44 툴셋이라 시스템의 옛 msvcp140(<14.40)이면 `std::mutex` 크래시(STL 17.10 변경) | 확인(objdump) | python.org "embedded distribution does not include the Microsoft C Runtime"; PyMuPDF 설치 문서·issue #2678; MS STL 17.10 노트 | 꾸러미에 `msvcp140*.dll`(msvc-runtime 14.51 휠에서 추출, 앱 로컬 배포 허용)을 `python\` 옆에 넣고, 빌더가 그 존재를 검사 |
| A2 | **두 번 실행하면 같은 포트에 서버가 둘.** Windows의 `SO_REUSEADDR`는 이미 LISTEN 중인 포트에도 bind를 허용한다. `port_is_free`가 항상 True → "이미 실행 중" 감지에 못 이름. `HTTPServer.allow_reuse_address=1`이라 서버도 겹쳐 뜬다. 요청이 두 프로세스로 흩어져 404·403이 무작위로 난다 | 확인(MS 문서 표, bpo-2550) | learn.microsoft.com SO_REUSEADDR/SO_EXCLUSIVEADDRUSE; dev.to "Python's HTTPServer will bind a port already in use on Windows" | Windows에서는 탐침·서버 모두 `SO_EXCLUSIVEADDRUSE`; `this_tool_listens`를 먼저 확인; bind 실패(OSError)면 다음 포트 |
| A3 | **폴더 선택 창의 실패가 전부 "취소"로 처리됨.** AppLocker/WDAC의 ConstrainedLanguage 모드에서 `New-Object System.Windows.Forms.Form`이 거부되거나 EDR이 PowerShell을 죽이면 exit≠0 → 코드는 None(취소) → 화면에 아무것도 안 나온다. 501 "직접 입력" 안내에 못 이름 | 확인(코드) + 개연(정책 환경) | MS Q&A CLM "Cannot create type"; forums.powershell.org Add-Type in CLM; SigmaHQ non-interactive PowerShell 규칙 | 스크립트가 `OK:<경로>`/`CANCEL`을 명시 출력하고 언어 모드를 먼저 검사(`exit 3`); 그 밖의 종료 코드는 501 + stderr를 로그에; BOM 제거; `UTF8Encoding $false` |
| A4 | **절전에서 깨면 서버가 바로 꺼짐.** 3.12의 Windows `time.monotonic()`은 GetTickCount64라 절전 시간이 포함된다 → 깨어난 첫 1초 루프에서 `idle ≥ 20분` | 확인(PEP 418, gh-88494) | | 루프 틱 간격이 60초 넘게 뛰면 절전 복귀로 보고 `touch()`; GUI는 `visibilitychange`/`focus`/`pageshow`에 즉시 heartbeat |
| A5 | **너무 넓은 폴더(사용자 폴더·`C:\`·AppData·꾸러미 폴더)를 고르면** 등록 루트가 도구 폴더(로그·히트맵·작업 보관·키·%TEMP%)를 품게 되어 (a) 검사 작업 보관이 꺼지고 (b) 이미 보관된 작업도 못 읽어 감정서가 404 (c) 검토 저장 409 (d) 증거 폴더 안에 쓰는 원칙 위반 (e) 2000개 상한이 브라우저 캐시로 찬다. `--folder`의 relocate 검사가 대화상자 경로에는 없다 | 확인(코드: `register_user_folder`에 검사 없음, `store_folder`는 모든 루트와 비교) | | `register_user_folder`가 홈·드라이브 루트·사용자 데이터 폴더·도구 캐시·임시 폴더·`sys.prefix`를 품는 폴더를 400으로 거부("너무 넓은 폴더입니다 — 증거 파일이 든 폴더를 고르십시오") |
| A6 | **.cmd의 `if errorlevel 1`은 음수 종료 코드(네이티브 크래시 0xC0000005·DLL 없음 0xC0000135)를 못 잡아 창이 바로 닫힘**; Ctrl-C(130)는 반대로 "오류로 끝났습니다"로 보임 | 확인(cmd 문서) | | `if not "%errorlevel%"=="0"`로 바꾸고 130은 "사용자가 중단했습니다" |
| A7 | **첫 실행이 느린데(백신이 .pyd 102개 검사 + .pyc 컴파일) 창이 비어 있어 두 번 누른다**(A2의 방아쇠) | 확인(꾸러미에 `__pycache__` 0개) | | .cmd에 "시작 중… 처음에는 1분" echo; 빌더가 `compileall`로 미리 컴파일(REV2-D6부터 `checked-hash` — 고친 `.py`가 옛 바이트코드에 가려지지 않음; REV2-D5부터 `-s`/`-p`로 빌드 경로 제거) |
| A8 | **회사 프록시가 켜진 PC에서 "이미 실행 중" 감지가 프록시로 감** (urllib는 레지스트리 프록시를 읽고 `<local>` 예외는 127.0.0.1에 안 맞음) | 확인(stdlib) | | `ProxyHandler({})` — 0b4c468에서 이미 고침 |

## B. 오해·데이터 손실·원칙 위반 (Med) — 이번에 고친 것(✔)과 문서·백로그로 보낸 것(→)

| # | 문제 | 판정 | 조치 |
|---|---|---|---|
| B1 | 콘솔 창을 닫으면(CTRL_CLOSE_EVENT) Python 시그널 처리기가 돌지 않아 `%TEMP%`의 한글 파일 스테이징 사본이 남고, 진행 중 작업은 보관되지 않는다. P3의 "작업 기록은 남음"은 진행 중 작업에는 틀림 | 확인(bpo msg335056) | ✔ `SetConsoleCtrlHandler`로 동기 정리(`shutdown_session`) · 「읽어보세요」 문구 수정 |
| B2 | 콘솔 QuickEdit: 창을 클릭하면 선택 모드가 되어 stderr 쓰기가 막힌 스레드가 멈춤 | 확인(microsoft/terminal #34) | ✔ 시작 때 `SetConsoleMode`로 QuickEdit 끄기 + 안내 |
| B3 | 탭 절전(Edge 2시간, 효율성 모드 5분, 정책 30초)·Chrome 메모리 절약으로 heartbeat가 끊겨 20분 뒤 서버가 꺼짐. 화면 결과는 JS 메모리뿐이라 재시작하면 사라짐 | 확인(Edge 지원 문서) | ✔ GUI: 보이는 순간 즉시 heartbeat · 「읽어보세요」에 Edge 절전 예외·Chrome 「항상 활성 상태로 유지할 사이트」 안내(REV2-D12) → 결과가 있으면 유휴 종료를 늘리는 것과 "최근 검사 열기"(`/api/jobs`)는 백로그 |
| B4 | 재실행·새 탭이면 결과·사건 정보가 없음(sessionStorage는 탭마다; 포트가 바뀌면 localStorage도). 보관된 작업으로 감정서를 만들려면 같은 폴더를 다시 골라야 하는데 안내가 없음 | 확인(코드) | → 백로그(`/api/jobs` + "최근 검사 열기"), ✔ 403 문구에 "같은 폴더를 다시 고르십시오" |
| B5 | 폴더 창이 늦게 뜨거나(PowerShell 콜드 스타트 수 초) 브라우저 뒤·다른 모니터에 숨음. 소유 창이 `ShowInTaskbar=false`라 "작업 표시줄을 확인" 안내가 틀림. 10분 넘게 응답 없으면 버튼이 영원히 busy | 개연 | ✔ 문구 "Alt+Tab으로 '폴더 찾아보기' 창을 찾으십시오", `ShowInTaskbar` 기본값, `run_child(timeout=600)` |
| B6 | PowerShell 5.1의 폴더 창은 구형 트리 창(주소 붙여넣기 칸 없음, 제목 안 바뀜, 150% 배율에서 흐림) | 확인(MS 문서 UseDescriptionForTitle) | ✔ 「읽어보세요」에 "'폴더 찾아보기' 트리 창입니다. 정상입니다"(REV2-D12); 장기: ctypes `IFileOpenDialog` |
| B7 | 네트워크 드라이브(Z:)를 고르면 `resolve()` 때문에 `\\서버\공유` 경로로 표시·기록됨; 관리자 권한 실행 시 매핑 드라이브가 안 보임 | 확인(CPython #82174) | ✔ 「읽어보세요」 한 줄(`\\서버\공유` 형태로 표시, REV2-D12); 표시는 그대로(보고서의 경로 일관성이 우선) |
| B8 | OneDrive 폴더(바탕 화면·문서가 KFM으로 옮겨진 경우)를 고르면 온라인 전용 파일을 내려받으며 검사하거나 362 오류로 건너뜀 | 확인(MS Q&A, elevenforum) | ✔ pick-folder 응답에 OneDrive 경고 문구 · 「읽어보세요」 |
| B9 | 서명 키가 `%APPDATA%`(Roaming)라 AD 로밍 프로필·폴더 리디렉션이면 다른 PC로 동기화된다 → "이 PC에서만 검증"이 틀림. `0o600`은 Windows에서 무의미하고 ACL은 상속(Administrators 포함). 두 인스턴스 동시 생성 경합이면 빈 파일을 "손상"으로 읽음 | 확인(코드) | ✔ 생성 직후 `windows_acl.make_owner_only`(있을 때), 끝까지 쓴 임시 파일을 `os.link`로 배타적으로 놓음(하드 링크가 없는 파일 시스템에서만 `os.replace` — 동시 첫 시작 경합 가능, `docs/deepfake-lens-service.md` 「서명 키 파일」), 문구 "이 키 파일을 가진 계정에서만 검증" → 위치 변경(LOCALAPPDATA)은 R22-3 결정과 얽혀 백로그 |
| B10 | 꾸러미에 opencv·ffmpeg·librosa·rarfile이 없어 동영상·음성·RAR 검사가 제한되는데 화면은 "PDF 가능·서명 자동"만 보이고 안내는 `pip install`을 가리킴(꾸러미엔 pip가 없음) | 확인 | ✔ 꾸러미 실행이면(`._pth` 존재) pip 안내 대신 "꾸러미에 포함되지 않은 기능" 문구 → ffmpeg(LGPL)·opencv-headless 동봉은 백로그(크기·라이선스 검토) |
| B11 | 폴더 대신 **파일**을 .cmd에 끌어 놓으면 argparse 영어 오류; 실행 중에 끌어 놓으면 조용히 무시; `&`가 든 경로 | 확인(코드) | ✔ 파일이면 `%~dp1`(그 폴더) 사용, 실행 중이면 "화면에서 고르십시오" 안내 · `&`·`,`·`;`·`=`가 있고 공백이 없는 경로는 탐색기가 따옴표 없이 넘겨 cmd가 인수를 쪼개므로 끌어 놓기가 안 된다 — 「읽어보세요」에 "끌어 놓기가 안 되면(경로에 & , ; = 가 있고 공백이 없으면) 화면에서 고르십시오"(REV2-D8) |
| B12 | 네트워크 공유(UNC)에서 실행하면 `cd /d`가 실패해 "python 폴더가 없습니다"로 잘못 안내 | 확인(cmd) | ✔ `pushd` |
| B13 | "바탕 화면의 Deepfake Lens를 다시 실행" — 바탕 화면에 바로가기가 없다 | 확인 | ✔ "압축을 푼 폴더의 「Deepfake Lens.cmd」" |
| B14 | Ctrl-C 종료 순서: serve 스레드 종료 전에 `server_close()` → Windows에서 WinError 10038 영어 traceback 가능 | 개연(실측 필요) | ✔ `shutdown()` → `join()` → `server_close()` 순서. KeyboardInterrupt는 서버를 멈춘 뒤 다시 올려 CLI의 중단 경로로 간다: `중단됨(사용자 요청)`(stderr)과 종료 코드 130(0이 아님 — REV2 F7 정정). 꾸러미 `.cmd`에서는 그 뒤 cmd의 「일괄 작업을 끝내시겠습니까 (Y/N)?」 |
| B15 | 종료·유휴 종료가 진행 중인 **동기** 요청(PDF 생성, 업로드 분석)을 보지 않음 | 확인 | → 백로그(처리 중 요청 수 계수) |
| B16 | 64개 루트 상한에서 "다시 시작하십시오" → 메모리 결과 상실 | 확인 | → 백로그(가장 오래된 미사용 루트부터 내보내기) |
| B17 | 접근성: 「종료」 이름 모호, 취소 때 낭독 없음, `window.confirm`이 "추가 대화상자 차단"이면 종료가 소리 없이 무시 | 확인 | ✔ 「프로그램 종료」, 취소 낭독 → confirm을 `<dialog>`로 바꾸는 것은 백로그 |
| B18 | Smart App Control(Win11)·MotW: 탐색기 압축 풀기는 Zone.Identifier를 전파, SAC가 켜진 PC는 .cmd를 무시 단추 없이 차단하고 서명 없는 .pyd도 막을 수 있음 | 확인(textslashplain 2026-04, KiCad 포럼) | ✔ 「읽어보세요」에 "풀기 전에 zip 차단 해제", "SAC면 이 PC에서는 못 씀 — 담당자에게" · 빌드 산출물 VirusTotal 점검은 사람 몫 |
| B19 | AppLocker 기본 규칙(%WINDIR%·%PROGRAMFILES%만 허용)·"명령 프롬프트 사용 금지" GPO면 시작 불가 | 개연 | ✔ 「읽어보세요」 한 줄(IT 담당자에게 실행 허용 요청) |
| B20 | 백신(V3·알약·Defender)의 행위 차단(python→숨김 PowerShell), 격리 | 개연(알약 오진 사례) | ✔ A3가 고쳐지면 원인이 화면에 보임 · 「읽어보세요」에 V3 예외 경로 |
| B21 | numpy 2.5가 x86-64-v2(SSE4.2) 기준선 → 2009년 이전 CPU·기능 가린 VM에서 import 실패 | 확인(바이너리 문자열) | ✔ 「읽어보세요」 지원 사양 |
| B22 | 빌드 산출물에 Mac 경로 노출(`bin/` 셰뱅, `direct_url.json`) | 확인 | ✔ 빌더가 삭제 |
| B23 | `chcp 65001`이 한국어 conhost의 글꼴을 한글 없는 글꼴로 바꿔 □로 보일 수 있음 | 추정(kldp) | → QA-SYS-4 실측 항목 |
| B24 | 기본 브라우저가 IE(LTSC)면 빈 화면; `webbrowser.open`은 `os.startfile`이라 `new=2`는 무시 | 개연 | ✔ 콘솔 문구 "Edge/Chrome 주소창에 붙여 넣으십시오" |
| B25 | 한글 IME 조합 중 Enter가 조합 확정과 제출을 함께 일으킴(사건명 칸에서 마지막 글자 손실·이중 제출) | 확인(W3C 논의) | ✔ `isComposing`/keyCode 229면 무시 |

## C. 증거 파일·보고서 내용 (0단계 판정 축 — 이번엔 문구·백로그, 코드는 검증 라운드에서)

| # | 문제 | 판정 | 조치 |
|---|---|---|---|
| C1 | **PDF의 한글 글꼴 실측(PyMuPDF 1.28.2 'korea' = Droid Sans Fallback):** 완성형 11,172자 전부 있음. 그러나 조합형 자모(U+1100–11FF)는 255/256 없음 → **macOS에서 만든 NFD 파일명은 PDF에서 통째로 빈칸**(잉크 픽셀 0 실측). 확장 A 한자 6,580/6,582 없음, ✔✗ 없음, 굵은 글꼴 없음 | 확인(실측) | → **0단계 수정 후보(High)**: 표시 문자열 NFC 정규화 + `has_glyph`로 빠진 문자를 각주에 기록("표시할 수 없는 문자 n개(U+XXXX)") — 조용한 빈칸은 법정 문서에서 허용 불가. BACKLOG BL-61 |
| C2 | 한국 Windows에서 만든 ZIP(UTF-8 플래그 없음)의 파일명이 cp437로 풀려 `║╕░φ` 모양으로 보고서에 실림 | 확인(zipfile 문서) | → BL-62: 플래그 없으면 cp437→cp949 재해석 시도 + "파일명 인코딩 추정: CP949" 명기 |
| C3 | ALZ/EGG(알집)·BCJ2 7z·Deflate64·RAR5(bsdtar 미지원, unrar 없음)는 못 연다 | 확인(py7zr·rarfile 문서) | → BL-63: 형식별 한국어 안내("반디집으로 푼 뒤 폴더 검사", 원본 압축 파일 해시 행은 남김) |
| C4 | iPhone HEIC는 Pillow만으로 못 연다(pillow-heif 없음) → "cannot identify" | 확인 | → BL-64: "HEIC — 지원 안 함(원본 해시만 기록)" 명시; pillow-heif 동봉 검토 |
| C5 | 삼성 모션포토(JPEG 뒤 MP4 트레일러)를 "파일 끝 이후 추가 데이터"로 오인할 수 있음 | 개연 | → BL-65: `SEFH`/`MotionPhoto_Data` 감지 → "정상 기기 기능" 라벨 |
| C6 | HWP: 암호 문서·HWP 3.x 미지원, 배포용 문서는 느림(cryptography 있으면 90배 빠름 — 꾸러미엔 있음) | 확인(syhwp) | → BL-66: 예외별 한국어 상태 |
| C7 | 카카오톡 전송본은 EXIF가 제거됨 → "EXIF 없음"을 조작 근거로 쓰지 않도록 문구 | 개연 | → BL-67: "메신저 전송본일 수 있음 — 원본 확보 권장" |
| C8 | 문서보안 DRM(파수·소프트캠프·마크애니) 파일은 python이 암호문을 읽음 → 손상 판정 | 추정 | ✔ 「읽어보세요」 한 줄 → BL-68: 매직 바이트 불일치 시 "암호화/DRM 가능성" |
| C9 | 경로 260자 초과(깊은 중첩 압축) — `LongPathsEnabled`에 달림 | 개연 | → BL-69 |
| C10 | %TEMP%가 8.3 짧은 이름(`홍길~1`)이면 루트 비교가 어긋날 수 있음 | 개연(bpo-32907) | → BL-70(모든 비교를 `resolve()` 뒤에 — 대부분 이미 그렇다) |

## D. 문제없음으로 확인한 것

Windows 방화벽은 127.0.0.1 bind에 묻지 않는다 · Chromium은 localhost/127.0.0.1을 프록시 없이 직접 연결 · LNA/PNA·HTTPS 우선·HSTS는 IP 출처에 걸리지 않는다 · 127.0.0.1의 blob 다운로드는 "안전하지 않은 다운로드" 대상이 아님(Edge 127+ 정책 DownloadRestrictions만 예외) · `._pth`와 `-m` 해석·`importlib.metadata` 동작 · zip 한글 이름에 UTF-8 플래그 있음 · 가장 긴 경로 ≈170자 · `.cmd`는 BOM 없는 UTF-8 CRLF이고 `chcp`가 한글 줄보다 앞 · c2pa_c.dll은 시스템 DLL과 `VCRUNTIME140.dll`만 import(REV2-D13 정정 — vcruntime은 `python\`에 있고, REV2-D7부터 msvcp140과 같은 14.51) · cryptography는 OpenSSL 정적 링크 · numpy는 자체 msvcp140 동봉 · `-NonInteractive`는 WinForms 창을 막지 않음 · 실행 정책은 `-Command`에 적용 안 됨 · PowerShell 5.1 Console은 UTF8 preamble을 쓰지 않음(referencesource) — 그래도 BOM 제거를 넣음.

## E. QA-SYS-4 꾸러미 경로에 추가할 실측 항목

아이콘 두 번 누르기(두 번째는 "이미 실행 중") · 사용자 폴더·`C:\`·바탕 화면 고르기(거부 문구) · 한글·공백 폴더를 대화상자로 고른 뒤 경로 칸 값 확인 · 콘솔 창 닫기 뒤 `%TEMP%\deepfake-lens-*` 잔재 · 절전 20분 뒤 복귀 · 프록시 켠 PC에서 재실행 · 폴더/파일 끌어 놓기 · Win10 conhost에서 한글 표시 · Edge 효율성 모드에서 30분 방치 · Ctrl-C 종료 메시지 · PDF 감정서 생성(VC++ 재배포 없는 PC).
