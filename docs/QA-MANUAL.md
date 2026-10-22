# 0단계 수동 QA 체크리스트

자동화할 수 없는 0단계 QA 시나리오 네 개의 절차와 통과 기준이다. `scripts/qa_phase0.py`는
이 항목들을 적합성 표(`docs/CONFORMANCE.md`)에 "수동"으로 표시하고 이 문서의 절을 로그
경로로 가리킨다. 결과는 각 절 끝의 기록 표에 남긴다(수행자, 날짜, 커밋, 결과, 증거 파일).

명세의 "QA 시나리오 (통과 기준 원문)"에는 이 네 ID의 기준 문장이 없다. 아래 통과 기준은
요구사항(R-IN-4, R-SYS-2, R-SYS-3, R-SYS-5, R-SYS-6)과 0단계 목표에서 끌어낸 것이며,
명세에 원문이 생기면 그 문장으로 바꾼다. 수치 기준이 명세에 없는 항목(QA-SYS-8)은 "기록"만
통과 조건으로 두고 기준값을 정하지 않는다.

## 기록 현황

네 항목 모두 **미실시(기록 없음)**이다 — 공식 수행 기록이 하나도 없으며, 각 절 끝의 기록 표는 빈
양식이다. 적합성 표(`docs/CONFORMANCE.md`)의 "수동"은 통과가 아니라 "이 문서의 절차로 사람이
수행해야 함"을 뜻한다.

| QA ID | 항목 | 기록 상태 |
| --- | --- | --- |
| QA-IN-3 | 네트워크 차단 상태 검사 | 미실시(기록 없음) |
| QA-SYS-4 | 깨끗한 Windows 11 설치 | 미실시(기록 없음) |
| QA-SYS-5 | 500개 파일 검사 중 강제 종료 후 재개 | 미실시(기록 없음) |
| QA-SYS-8 | CPU 전용 처리 시간 | 미실시(기록 없음) |

## 공통 준비

```bash
git clone <repo> deepfake-lens && cd deepfake-lens
git rev-parse HEAD                      # 기록 표의 "커밋"
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -e . numpy Pillow        # 픽스처 생성용(numpy 없으면 사진 대신 텍스트)
python scripts/make_qa_manual_fixtures.py --out qa-manual
```

`qa-manual/formats/`(지원 형식별 샘플 1개씩 — 자동 QA-IN-1과 같은 생성기),
`qa-manual/resume-500/`(500개), `qa-manual/timing/`(타이밍 세트)이 만들어진다. 같은 커밋이면
내용이 바이트 단위로 같다.

결과 JSON 비교는 `scripts/qa_compare_results.py`로 한다(표준 라이브러리만 쓰므로 레포 밖에서도
`python -I qa_compare_results.py a.json b.json`으로 돈다 — 이전의 히어독 스니펫은 PowerShell에서 돌지 않았다, W-4).
항목을 행 경로별로 맞추되 경로의 `\`는 `/`로 바꾸고, `cached`·`path_b64`와 설치 폴더가 든 모델 프로필 경로(파일
이름만 비교)를 빼고 비교한다. 같으면 `동일`(exit 0), 다르면 `다름 N건`과 행마다 다른 키·두 값(exit 1):

```bash
python -I scripts/qa_compare_results.py a.json b.json
```

## QA-IN-3

**네트워크 차단 상태 검사** (R-IN-4)

통과 기준:
1. 네트워크가 완전히 차단된 상태에서 `scan`이 종료 코드 0으로 끝나고 모든 파일에 결과 행이 있다.
2. 검사 중 외부(비루프백) 네트워크 연결 시도가 0건이다.
3. 결과 JSON의 항목이 네트워크가 있는 상태의 같은 검사와 `동일`하다.
4. `doctor`가 traceback 없이 끝나고, 허브 모델 프로필은 "pin 없음/미고정" 또는 실행 불가로 표시될 뿐
   내려받기를 시도하지 않는다.

절차(Linux; 사용자 네임스페이스가 없으면 `docker run --network none -v $PWD:/w -w /w python:3.12 …`):

```bash
# 1) 기준: 네트워크 있음
python -m deepfake_lens scan qa-manual/formats --recursive --deep-signals --pixel deep \
    --json-out online.json --format json > /dev/null
# 2) 네트워크 없음 + 연결 시도 추적
unshare --net --map-root-user strace -f -e trace=connect -o connect.log \
    python -m deepfake_lens scan qa-manual/formats --recursive --deep-signals --pixel deep \
    --json-out offline.json --format json > /dev/null; echo "exit=$?"
grep connect connect.log | grep -v 'AF_UNIX' | grep -c 'AF_INET'     # 0이어야 함
unshare --net --map-root-user python -m deepfake_lens doctor; echo "exit=$?"
# 3) 비교: 공통 준비의 비교 스크립트에 online.json offline.json 을 넘긴다 → "동일"
```

Windows 11(관리자 PowerShell):

```powershell
$py = (Get-Command python).Source
New-NetFirewallRule -DisplayName dfl-qa-in-3 -Direction Outbound -Program $py -Action Block
python -m deepfake_lens scan qa-manual\formats --recursive --json-out offline.json --format json > $null; "exit=$LASTEXITCODE"
python -m deepfake_lens doctor; "exit=$LASTEXITCODE"
Remove-NetFirewallRule -DisplayName dfl-qa-in-3
```

기록 상태: **미실시(기록 없음)** — 아래 표는 비어 있는 기록 양식이다. 수행하면 행을 채우고
이 줄을 `기록 상태: 실시(<날짜>, <커밋>)`로 바꾼다. 아래 "사전 점검"은 공식 기록이 아니다.

| 수행자 | 날짜 | 커밋 | 환경 | exit | 비루프백 connect 수 | 비교 | 결과 | 증거 파일 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| | | | | | | | | |

사전 점검(공식 기록 아님): 2026-10-09 WP-J 작성 중 Linux 컨테이너(Python 3.13, torch·가중치 없음)에서
위 Linux 절차를 그대로 실행 — exit 0, connect 시스템 호출 0건, online/offline 비교 "동일".

## QA-SYS-4

**깨끗한 Windows 11 설치** (R-SYS-2, R-SYS-6)

통과 기준:
1. Python과 이 도구 외에 아무것도 설치되지 않은 Windows 11(Windows Sandbox 또는 새 VM)에서
   설치·실행 중 traceback이 한 번도 나오지 않는다.
2. `doctor`, `scan`, `web`, `evidence-statement`가 동작하고, 한글 출력이 PowerShell과 cmd 모두에서
   깨지지 않는다(화면 캡처로 판단 — 아래 "한글 판단").
3. 한글과 공백이 들어간 경로(`C:\사건 자료\증거 1`)를 검사할 수 있다.
4. `api-serve`는 fastapi/uvicorn이 없으면 한국어 설치 안내와 종료 코드 2로 끝난다(traceback 없음).
5. `qa-manual/formats` 결과가 빌드 머신에서 같은 커밋·**같은 설치 상태**(휠만 설치, 선택 패키지 없음,
   PATH에 ffmpeg 없음)로 만든 기준 결과 `reference-wheel.json`과 `동일`하다. numpy·Pillow·ffmpeg·pymupdf가
   있으면 검사 범위(coverage)의 사유가 달라지므로, extras가 깔린 개발 환경의 결과와는 비교하지 않는다
   (GUI-REV7 W-4 — 같은 픽스처를 extras 환경에서 검사하면 51행 중 31행이 다르다).

PDF 증거설명서는 통과 기준이 아니다. pymupdf는 어떤 extra에도 들어 있지 않아(게이트 lock에만 있음)
휠만 설치한 Windows에서는 PDF를 만들 수 없고, GUI의 PDF 버튼이 비활성인 것이 정상이다. PDF는 비교를
마친 뒤 아래 "선택: PDF"에서 따로 확인한다(W-5).

### 꾸러미 경로 (먼저 이 경로로 시험 — 꾸러미의 첫 실제 Windows 실행)

`DeepfakeLens-win64.zip`(Python과 의존 패키지가 들어 있는 인계 꾸러미, `scripts/build_desktop_bundle.py --platform windows`로 만듦)을
깨끗한 Windows 11에서 시험한다. 이 경로가 꾸러미를 실제 Windows에서 처음 돌려 보는 시험이다. 검사할 `qa-manual/formats`는 USB의
`qa-manual.zip`을 `C:\dfl\qa-manual`에 풀어 쓴다(아래 venv 절차 1)의 `Expand-Archive`와 같다). 통과 기준 1·2·3은 그대로 적용한다.

1. `DeepfakeLens-win64.zip`을 `C:\DeepfakeLens`에 푼다(바탕 화면·문서·OneDrive는 쓰지 않는다). 풀면 `DeepfakeLens-win64\` 안에
   `Deepfake Lens.cmd`, `문제 확인.cmd`, `읽어보세요.txt`, `VERSION.txt`, `python\`이 있어야 한다.
2. `Deepfake Lens.cmd`를 더블클릭한다. SmartScreen이 막으면 「추가 정보」 → 「실행」을 누른다(또는 zip의 속성에서 「차단 해제」,
   PowerShell에서는 `Unblock-File`). 콘솔 창의 제목은 「Deepfake Lens — 이 창을 닫으면 프로그램이 꺼집니다」다.
3. 콘솔에 다음 줄이 나와야 한다(traceback 없음):
   - `Deepfake Lens 데스크톱 모드`와 `화면 주소: http://127.0.0.1:8765/gui`(포트는 8765부터 첫 빈 포트)
   - 종료 방법 줄: 이 창을 닫거나 Ctrl-C, 화면의 「종료」 단추
   - 서명 키 줄: 처음에는 `보고서 서명 키를 새로 만들었습니다: C:\Users\<이름>\AppData\Roaming\deepfake-lens\report-key (이 PC의 이 계정에서만 검증됩니다)`
   - 로그 파일 줄: `문제가 생기면 이 로그 파일을 보내 주십시오: …`
   - 유휴 종료 줄: `20분 동안 화면이 열려 있지 않고 검사도 없으면 스스로 꺼집니다.`
4. 브라우저가 저절로 열리고 `/gui`가 보인다. 「검사할 폴더 고르기」를 누르면 Windows 폴더 선택 창이 뜬다. `C:\dfl\qa-manual\formats`를
   고르면 폴더 칸이 채워진다. 검사를 시작해 51행 표가 나오고, 감정서 만들기에서 HTML·서명된 JSON·**PDF 버튼이 활성**인지 확인한다
   (꾸러미에는 pymupdf가 들어 있어 이 경로에서는 PDF가 만들어진다).
5. 화면의 「종료」를 누른다. 콘솔에 `Deepfake Lens를 종료했습니다.`가 나오고 콘솔 창이 저절로 닫힌다.
6. 선택: `문제 확인.cmd`를 더블클릭하면 `doctor` 결과와 로그 폴더(`%USERPROFILE%\.cache\deepfake-lens\logs`)가 나오고 키를 누르면 닫힌다.
   서명한 감정서는 `deepfake-lens verify-report --key-file %APPDATA%\deepfake-lens\report-key`로 이 PC에서만 검증된다.

추가 실측 항목(위험 검토 E — `docs/gui/risk_review_desktop_2026-10-23.md`; Windows에서 한 번도 돌리지 않은 부분이라 이 경로에서 확인한다):

1. 아이콘 두 번 누르기 → 두 번째 창에 「Deepfake Lens가 이미 실행 중입니다」가 뜨고 닫히며, 브라우저는 첫 서버의 화면을 연다.
2. 사용자 폴더·`C:\`·바탕 화면 고르기 → 「너무 넓은 폴더입니다」 안내(400)가 뜨고 폴더가 등록되지 않는다.
3. 한글·공백이 든 폴더(`C:\사건 자료\증거 1`)를 대화상자로 고르기 → 폴더 칸의 값이 고른 경로와 같다.
4. 콘솔 창 닫기 → `%TEMP%\deepfake-lens-*` 폴더가 남지 않는다.
5. 절전 20분 뒤 복귀 → 서버가 켜져 있고 화면이 계속 응답한다.
6. 프록시를 켠 PC에서 다시 실행 → 「이미 실행 중」 감지가 프록시를 거치지 않고 정상 동작한다.
7. 폴더·파일을 `Deepfake Lens.cmd`에 끌어 놓기 → 폴더면 그 폴더가, 파일이면 그 파일이 든 폴더가 열리고 영어 오류가 나오지 않는다.
8. Win10 conhost에서 한글 표시 → 콘솔의 한글이 □로 깨지지 않는다(`chcp 65001`의 글꼴 변경 확인).
9. Edge 효율성 모드에서 30분 방치 → 서버가 꺼지지 않거나, 꺼지면 「읽어보세요」에 적힌 Edge 절전 예외 안내와 일치한다.
10. Ctrl-C 종료 → 「사용자가 중단했습니다」 등 한국어 안내만 나오고 traceback(WinError 10038 포함)이 없다.
11. PDF 감정서 생성(VC++ 재배포가 없는 PC) → PDF가 열린다(꾸러미의 `msvcp140*.dll`로 `_mupdf.pyd`가 적재된다).

기록 표(위)에는 어느 경로로 시험했는지(꾸러미 / venv)를 함께 적는다. 꾸러미가 열리지 않거나 위와 다르면 화면 캡처와 로그 파일을 남기고
아래 venv 절차로 같은 통과 기준을 확인한다.

### 대체 경로: venv 절차 (꾸러미가 안 될 때 — 아래는 이전 그대로)

### 빌드 머신에서 준비할 것

`scripts/make_qa_manual_fixtures.py`는 테스트 패키지가 휠에서 빠져 있어 Windows에서 돌릴 수 없다 —
픽스처와 기준 결과는 빌드 머신(macOS 또는 Linux)에서 만들어 USB로 넘긴다.

```bash
git rev-parse HEAD                                        # 기록 표의 "커밋"
python -m pip wheel . --no-deps -w dist                   # dist/deepfake_lens-0.1.0-py3-none-any.whl
python scripts/make_qa_manual_fixtures.py --out qa-manual
# macOS: Finder 압축·zip은 __MACOSX·._* 를 넣어 행 수가 늘어난다(W-12). 반드시 ditto로:
ditto -c -k --norsrc --noextattr qa-manual qa-manual.zip  # Linux: (cd qa-manual && zip -qr ../qa-manual.zip .)
# 기준 결과: 휠만 깐 새 venv, PATH에서 ffmpeg가 있는 폴더를 뺀다(Windows 쪽과 같은 설치 상태)
python3 -m venv /tmp/wheelvenv && /tmp/wheelvenv/bin/pip install dist/deepfake_lens-0.1.0-py3-none-any.whl
PATH=/tmp/wheelvenv/bin:/usr/bin:/bin sh -c 'command -v ffmpeg' # 아무것도 안 나와야 함(Linux에서 /usr/bin/ffmpeg가 있으면 ffmpeg 없는 컨테이너에서)
PATH=/tmp/wheelvenv/bin:/usr/bin:/bin deepfake-lens scan qa-manual/formats --recursive \
    --json-out reference-wheel.json --format json > /dev/null; echo "exit=$?"     # 0
cp scripts/qa_compare_results.py compare.py
# 선택 단계(PDF)용 Windows 휠 — 게이트 lock과 같은 판
python -m pip download pymupdf==1.28.2 --platform win_amd64 --python-version 3.12 --only-binary=:all: -d win-wheels
```

USB에 넣을 것(Windows에서 `C:\dfl\`에 그대로 복사): `python-3.12.10-amd64.exe`,
`dist\deepfake_lens-0.1.0-py3-none-any.whl`, `qa-manual.zip`, `reference-wheel.json`, `compare.py`,
`win-wheels\`(선택). Python은 3.12.10을 쓴다 — python.org 첫 화면의 최신판(3.14)은 검증하지 않았고
(`requires-python >=3.11`, CI는 3.11/3.12), 3.12의 Windows 설치 파일은 3.12.10이 마지막이다(W-6):
https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe

### 깨끗한 Windows 11에서 (후임용 runbook)

Python 설치: `python-3.12.10-amd64.exe` 실행 → 첫 화면에서 "Add python.exe to PATH"를 체크 →
"Install Now" → 마지막 화면의 "Disable path length limit"를 누른다. 그다음 PowerShell(관리자 아님)을 열고
아래를 한 줄씩 실행한다. 각 줄 오른쪽 주석이 정상 결과다.

```powershell
# 0) 이 창에서만 스크립트 실행 허용(W-3). 창을 새로 열면 다시 실행하십시오.
#    정책을 바꾸고 싶지 않으면 Activate.ps1 대신 C:\dfl\venv\Scripts\python.exe -m deepfake_lens … 로 실행하거나,
#    cmd에서 C:\dfl\venv\Scripts\activate.bat 을 쓰십시오.
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned -Force
# 1) 출력 폴더와 증거 폴더를 먼저 만듭니다(W-2 — 이 도구는 출력 폴더를 만들지 않습니다:
#    없으면 "오류: 출력 폴더가 없습니다: …"와 exit 2). 바탕 화면·문서(OneDrive 리디렉션)는 쓰지 마십시오(W-11).
New-Item -ItemType Directory -Force C:\dfl\out, "C:\사건 자료\증거 1" | Out-Null
py -3.12 -m venv C:\dfl\venv
C:\dfl\venv\Scripts\Activate.ps1                                  # 프롬프트 앞에 (venv)
python --version                                                  # Python 3.12.10
python -m pip install C:\dfl\dist\deepfake_lens-0.1.0-py3-none-any.whl   # 휠만 설치(의존 패키지 없음)
deepfake-lens --help; "exit=$LASTEXITCODE"                        # 사용법 화면, exit=0
deepfake-lens doctor; "exit=$LASTEXITCODE"                        # exit=0 (아래 "정상 화면" 참고)
Expand-Archive C:\dfl\qa-manual.zip -DestinationPath C:\dfl\qa-manual
# 2) 경로를 따옴표로 감쌀 때 끝에 \ 를 붙이지 마십시오(W-7): "C:\사건 자료\증거 1\" 처럼 Tab 완성이 붙이는 끝의 \ 는
#    PowerShell 5.1이 프로그램에 넘길 때 닫는 따옴표를 먹어 "폴더를 찾을 수 없습니다 …1\"" 가 됩니다.
Copy-Item C:\dfl\qa-manual\formats\* "C:\사건 자료\증거 1"
deepfake-lens scan "C:\사건 자료\증거 1" --recursive --json-out C:\dfl\out\windows.json --html-out C:\dfl\out\scan.html --evidence-statement-out C:\dfl\out\statement.md; "exit=$LASTEXITCODE"
#    exit=0, 51행 표, C:\dfl\out 에 windows.json·scan.html·statement.md 세 파일
# 3) 기준과 비교(통과 기준 5). pymupdf 등을 설치하기 전에 하십시오.
python -I C:\dfl\compare.py C:\dfl\reference-wheel.json C:\dfl\out\windows.json; "exit=$LASTEXITCODE"   # 동일, exit=0
# 4) 서명 키는 32바이트 이상이어야 합니다(W-1 — 짧으면 evidence-statement·web·api-serve가 exit 2).
#    GUID 두 개를 이어 64자 키를 만듭니다. 이 창을 닫으면 사라집니다.
$env:DEEPFAKE_LENS_REPORT_KEY = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
deepfake-lens evidence-statement C:\dfl\out\windows.json --json-out C:\dfl\out\statement.json --format table; "exit=$LASTEXITCODE"
#    증거 목록 51건, 끝에 "서명 JSON 저장 완료: C:\dfl\out\statement.json", "서명: HMAC-SHA256 hmac-sha256-v1:…", exit=0
deepfake-lens api-serve; "exit=$LASTEXITCODE"
#    "오류: API 서버에 필요한 패키지가 설치되어 있지 않습니다: fastapi, uvicorn" + 설치 안내 2줄, exit=2, traceback 없음
# 5) GUI(W-10). 이 창은 서버가 차지하므로 그대로 두십시오.
deepfake-lens web --folder "C:\사건 자료\증거 1"
#    "Deepfake Lens GUI: http://127.0.0.1:8765" 와 "Windows에서 접속: http://localhost:8765" 두 줄이 나오면 정상.
#    web은 요청·오류를 이 창에 찍지 않습니다 — 오류는 로그 파일(아래 "버그 보고")과 브라우저 F12를 보십시오.
#    Edge/Chrome에서 http://127.0.0.1:8765/gui 를 엽니다.
#    "2 / 2 검사할 폴더"의 "폴더 경로" 상자에 C:\사건 자료\증거 1 이 저절로 채워질 때까지 기다린 뒤(채워지기 전에
#    상자를 누르고 치면 글자가 섞일 수 있음) 상자를 누르고 Enter(또는 「검사 시작」)를 누르십시오.
#    다른 폴더를 검사하려면 경로를 직접 치고 Enter — 끝에 \ 를 붙이지 말고, --folder 안의 폴더만 됩니다.
#    「파일 올려서 검사(서명 제외 — 감정서 불가)」의 「올릴 폴더 고르기」·「올릴 파일 고르기」는 폴더 검사가 아니라 업로드입니다(최대 20개씩,
#    서명 제외, 증거설명서를 만들 수 없음). 이번 시험에서는 누르지 마십시오.
#    정상: 진행 표시 → 사건 대장에 51행(모두 「판단 불가」) → 감정서 만들기에서 HTML·서명된 JSON 내려받기.
#    PDF 버튼 비활성은 pymupdf가 없어서이며 정상입니다.
#    종료: 이 창에서 Ctrl-C → "중단됨(사용자 요청) — 임시 파일과 자식 프로세스를 정리하고 종료합니다." (exit 130이 정상)
"exit=$LASTEXITCODE"                                              # 130
# 6) cmd 기본 코드 페이지(949)에서 한글 확인(통과 기준 2). 화면 캡처로만 판단하십시오.
cmd /c "chcp 949 & C:\dfl\venv\Scripts\deepfake-lens.exe doctor"
```

한글 판단(W-8): 콘솔 화면에서는 한글이 정상으로 보여야 한다. 출력을 `> 파일`·`Out-File`·`| Tee-Object`로
받으면 PowerShell 5.1이 cp949로 다시 인코딩해 한글이 깨질 수 있는데, 이는 이 도구의 결함이 아니다 —
한글 깨짐은 **화면 캡처로만** 판단하고 리다이렉트한 파일로 판단하지 않는다(파일이 필요하면 도구의
`--json-out`·`--html-out` 등 출력 옵션으로 받는다).

정상 화면(W-13, 빌드 머신에서 휠만 깐 venv·PATH에 ffmpeg 없음으로 캡처한 출력 — 인계 꾸러미의
`expected-output\`에 전문이 있다. 개수나 문구가 다르면 그 화면을 캡처해 보고한다):
- `doctor`(exit 0): 첫 줄 "실행 가능: 0/10 프로필 — …", 이어서 "!! 실행 가능한 모델 프로필 없음 — …" 경고,
  모델 프로필 10개가 모두 `[ WARN]`(pin·의존성·체크포인트 `MISS`, "실행 가능: 아니오 (supported:false — 측정
  게이트 미충족)"), 의존성 표에 `[ MISS]` 다수(torch·numpy·Pillow·PyMuPDF·fastapi 등), 외부 도구 "ffmpeg: PATH에
  없음", 마지막 줄 "요약: 실행 가능 0/10, 누락 18건, 경고 11건". 전부 정상이며 오류가 아니다.
- `scan`(exit 0): 표 위에 "!! 신경망 미탑재(측정 게이트 미충족) — 재현 가능한 기록 근거만 반영 !!",
  "!! 판정 임계값: in-sample(참고) — … !!", "총 51건(압축 파일 10건 포함) — 조작·생성 근거 있음 0건, 원본성 근거
  있음 0건, 판단 불가 51건(검사 실패 포함 0건), 미지원/분석 실패 0건, …". 표준 오류에 두 줄 —
  "기본 엔진 프로필(C:\dfl\venv\Lib\site-packages\deepfake_lens\models): [...]"과 "경고: 임계값 프로필 …
  thresholds.json은(는) in-sample(참고)입니다 — …" — 이 나오는 것도 정상이다(오류 아님).
- `evidence-statement`(exit 0): "=== (사건명 입력) 증거설명서 ===", 증거 목록 51건(호증마다 SHA-256),
  "서명 JSON 저장 완료: …", "서명: HMAC-SHA256 hmac-sha256-v1:…". 키가 32바이트 미만이면 "오류: 보고서 서명 키가
  너무 짧습니다(12바이트) — …"와 exit 2(W-1).
- `api-serve`: "오류: API 서버에 필요한 패키지가 설치되어 있지 않습니다: fastapi, uvicorn", "설치: `pip install
  fastapi uvicorn`", "설치 없이 쓰려면 내장 웹 서버를 사용하세요: deepfake-lens web", exit 2.

### 선택: PDF (통과 기준 아님, 비교를 마친 뒤)

```powershell
python -m pip install --no-index --find-links C:\dfl\win-wheels pymupdf==1.28.2
deepfake-lens doctor                                              # "[ OK  ] PyMuPDF: v1.28.2 — …", 요약 "누락 17건"
deepfake-lens evidence-statement C:\dfl\out\windows.json --pdf-out C:\dfl\out\statement.pdf --format table; "exit=$LASTEXITCODE"
#    끝에 "PDF 저장 완료: C:\dfl\out\statement.pdf", exit=0. pymupdf 없이 실행하면 "오류: PDF 증거설명서를 만들려면
#    pymupdf 패키지가 필요합니다(…)"와 exit 2.
#    web을 다시 띄우면 감정서 화면의 PDF 버튼이 활성입니다.
```

pymupdf를 설치한 뒤에는 `sample.pdf` 행의 검사 범위가 달라져 기준 결과와 `다름 1건`이 되는 것이 정상이다
(그래서 비교를 먼저 한다).

### 버그 보고(W-9)

보고마다 다음을 넣는다: ① 실행한 명령 그대로 ② `exit=` 값 ③ 화면 캡처 ④ 로그 파일 ⑤ `C:\dfl\out\*`
⑥ GUI 오류는 F12 → Network에서 실패한 요청의 응답 본문(`{"error": …}`) ⑦ `python --version`과 `winver`.

로그 파일은 명령마다 같은 파일에 덧붙는다: 기본은 `C:\Users\<사용자>\.cache\deepfake-lens\logs\deepfake-lens.log`
(`DEEPFAKE_LENS_LOG_DIR`를 지정했으면 그 폴더). 검사 대상 폴더나 `--folder`가 이 기본 위치를 품으면(예:
`C:\Users\<사용자>` 자체를 검사) 실행할 때 "알림: 도구 폴더(로그·히트맵·오디오 numba 캐시)의 기본 위치 … 이번 실행은
<위치>에 만듭니다 …" 한 줄이 나오고, 로그는 그 `<위치>\logs\deepfake-lens.log`에 있다 — Windows에서는 보통
`%LOCALAPPDATA%\deepfake-lens`가 프로필 안이라 `C:\ProgramData\deepfake-lens\<사용자>\logs\`가 된다.

비교(통과 기준 5)는 `compare.py`(= `scripts/qa_compare_results.py`)로 한다. 행 경로의 `\`를 `/`로 바꿔 맞추고
`cached`·`path_b64`를 빼며, 설치 폴더가 들어 있는 모델 프로필 경로는 파일 이름만 비교한다. `다름 N건`이면 다른 행마다
키와 두 값이 나오므로 그 출력 전체를 보고에 붙인다.

기록 상태: **미실시(기록 없음)** — 아래 표는 비어 있는 기록 양식이다. 수행하면 행을 채우고
이 줄을 `기록 상태: 실시(<날짜>, <커밋>)`로 바꾼다. 아래 "사전 점검"은 공식 기록이 아니다.

| 수행자 | 날짜 | 커밋 | Windows 빌드 | Python | 단계별 exit | 한글 표시 | 비교 | 결과 | 증거 파일 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| | | | | | | | | | |

## QA-SYS-5

**500개 파일 검사 중 강제 종료 후 재개** (R-SYS-3)

통과 기준:
1. 강제 종료(SIGKILL / `taskkill /F`) 뒤에도 캐시 파일이 유효한 JSON이다(손상 0).
2. 같은 명령으로 다시 실행하면 종료 코드 0으로 끝나고 `summary.cached`가 0보다 크다
   (캐시는 20개마다 기록된다 — `core._CACHE_FLUSH_EVERY`).
3. 재개 결과의 500개 항목이 중단 없이 검사한 결과와 `동일`하다(요약의 `cached` 수만 다르다).
4. 증거 폴더에 새 파일이 생기지 않는다(QA-IN-1과 같은 조건).

절차(Linux):

```bash
cd qa-manual
find resume-500 -type f | wc -l                                   # 500
rm -f cache.json
( python -m deepfake_lens scan resume-500 --cache cache.json --json-out interrupted.json --format json > /dev/null & \
  pid=$!; sleep 8; kill -9 $pid; wait $pid; echo "exit=$?" )        # 137 = SIGKILL
python -c "import json; d=json.load(open('cache.json')); print('캐시 항목', len(d['items']))"
python -m deepfake_lens scan resume-500 --cache cache.json --json-out resumed.json --format json > /dev/null; echo "exit=$?"
python -c "import json; print(json.load(open('resumed.json'))['summary'])"   # cached > 0, total 500
python -m deepfake_lens scan resume-500 --json-out fresh.json --format json > /dev/null
# 비교: 공통 준비의 비교 스크립트로 fresh.json 과 resumed.json
find resume-500 -type f | wc -l                                   # 여전히 500
```

Windows: `Start-Process`로 실행한 뒤 `Stop-Process -Id <pid> -Force`(= `taskkill /F`)로 끊는다.
`sleep 8`은 이 저장소 기준 머신에서 약 440개를 처리한 시점이다(2026-10, 가중치 없음); 느린
머신에서는 캐시 항목이 20 이상이 될 때까지 늘린다.

기록 상태: **미실시(기록 없음)** — 아래 표는 비어 있는 기록 양식이다. 수행하면 행을 채우고
이 줄을 `기록 상태: 실시(<날짜>, <커밋>)`로 바꾼다. 아래 "사전 점검"은 공식 기록이 아니다.

| 수행자 | 날짜 | 커밋 | 종료 시점(초) | 종료 후 캐시 항목 | 재개 exit | 재개 cached | 비교 | 결과 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| | | | | | | | | |

사전 점검(공식 기록 아님): 2026-10-09 WP-J 작성 중 Linux 컨테이너에서 `--no-default-engine`으로
실행 — 8초에 SIGKILL(exit 137), 캐시 항목 440개(내용 키라 같은 WAV는 한 항목), 재개 exit 0,
`cached` 480, 중단 없는 검사와 비교 "동일".

## QA-SYS-8

**CPU 전용 처리 시간** (R-SYS-5)

통과 기준:
1. GPU가 없는(또는 `CUDA_VISIBLE_DEVICES=""`) 머신에서 타이밍 세트 검사가 오류 없이 끝난다.
2. 아래 표에 머신 사양과 측정값(`elapsed_seconds`, `files_per_second`, 파일 종류별 1개당 시간)을 기록한다.
3. 같은 머신에서 직전 기록보다 처리량이 20 % 넘게 떨어지면 원인을 기록하기 전까지 실패로 본다.
   첫 기록이 기준선이 된다. (명세에 절대 수치 기준이 없으므로 절대값으로는 판정하지 않는다.)

절차:

```bash
lscpu | grep -E 'Model name|^CPU\(s\)'; free -g | head -2            # Windows: Get-CimInstance Win32_Processor
export CUDA_VISIBLE_DEVICES=""
for mode in off deep; do
  python -m deepfake_lens perf qa-manual/timing --pixel $mode --out perf-$mode.json
  python -c "import json; d=json.load(open('perf-$mode.json')); print('$mode', round(d['elapsed_seconds'],1), 's', round(d['files_per_second'],2), 'files/s')"
done
# 종류별: 사진 100장(1024x768), WAV 20개(10 s), 텍스트 30개를 각각 따로 측정
mkdir -p t/photo t/wav t/text
cp qa-manual/timing/*.png t/photo/; cp qa-manual/timing/*.wav t/wav/; cp qa-manual/timing/*.txt t/text/
for kind in photo wav text; do python -m deepfake_lens perf t/$kind --pixel deep --out perf-$kind.json; done
```

기록 상태: **미실시(기록 없음)** — 아래 표는 비어 있는 기록 양식이다. 수행하면 행을 채우고
이 줄을 `기록 상태: 실시(<날짜>, <커밋>)`로 바꾼다. 아래 "사전 점검"은 공식 기록이 아니다.

| 수행자 | 날짜 | 커밋 | CPU / 코어 / RAM | pixel off s | pixel deep s | 사진 1장 s | WAV 1개 s | 텍스트 1개 s | 직전 대비 | 결과 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| | | | | | | | | | | |
