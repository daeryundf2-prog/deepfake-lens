# 0단계 수동 QA 체크리스트

자동화할 수 없는 0단계 QA 시나리오 네 개의 절차와 통과 기준이다. `scripts/qa_phase0.py`는
이 항목들을 적합성 표(`docs/CONFORMANCE.md`)에 "수동"으로 표시하고 이 문서의 절을 로그
경로로 가리킨다. 결과는 각 절 끝의 기록 표에 남긴다(수행자, 날짜, 커밋, 결과, 증거 파일).

명세의 "QA 시나리오 (통과 기준 원문)"에는 이 네 ID의 기준 문장이 없다. 아래 통과 기준은
요구사항(R-IN-4, R-SYS-2, R-SYS-3, R-SYS-5, R-SYS-6)과 0단계 목표에서 끌어낸 것이며,
명세에 원문이 생기면 그 문장으로 바꾼다. 수치 기준이 명세에 없는 항목(QA-SYS-8)은 "기록"만
통과 조건으로 두고 기준값을 정하지 않는다.

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

결과 JSON 비교에 쓰는 스니펫(타임스탬프·절대경로를 빼고 항목별 결과를 비교, 같으면 `동일` 출력):

```bash
python - a.json b.json <<'EOF'
import json, sys
def items(path):
    data = json.load(open(path, encoding="utf-8"))
    return {item["path"]: {k: v for k, v in item.items() if k not in {"cached"}} for item in data["items"]}
a, b = (items(p) for p in sys.argv[1:3])
diff = sorted(p for p in a.keys() | b.keys() if a.get(p) != b.get(p))
print("동일" if not diff else f"다름 {len(diff)}건: {diff[:10]}")
EOF
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
# 3) 비교: 공통 준비의 비교 스니펫에 online.json offline.json 을 넘긴다 → "동일"
```

Windows 11(관리자 PowerShell):

```powershell
$py = (Get-Command python).Source
New-NetFirewallRule -DisplayName dfl-qa-in-3 -Direction Outbound -Program $py -Action Block
python -m deepfake_lens scan qa-manual\formats --recursive --json-out offline.json --format json > $null; "exit=$LASTEXITCODE"
python -m deepfake_lens doctor; "exit=$LASTEXITCODE"
Remove-NetFirewallRule -DisplayName dfl-qa-in-3
```

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
   깨지지 않는다.
3. 한글과 공백이 들어간 경로(`C:\사건 자료\증거 1`)를 검사할 수 있다.
4. `api-serve`는 fastapi/uvicorn이 없으면 한국어 설치 안내와 종료 코드 2로 끝난다(traceback 없음).
5. `qa-manual/formats` 결과가 Linux에서 같은 커밋으로 만든 결과와 `동일`하다.

절차:

```powershell
# 빌드 머신(이 저장소): 휠과 픽스처를 만든다
python -m pip wheel . --no-deps -w dist
python scripts/make_qa_manual_fixtures.py --out qa-manual
python -m deepfake_lens scan qa-manual\formats --recursive --json-out linux.json --format json   # Linux에서 실행해 복사

# 깨끗한 Windows 11: python.org의 Python 3.12 x64 설치("Add python.exe to PATH" 체크)
py -3.12 -m venv C:\dfl\venv
C:\dfl\venv\Scripts\activate
pip install C:\dfl\dist\deepfake_lens-0.1.0-py3-none-any.whl
deepfake-lens --help
deepfake-lens doctor; "exit=$LASTEXITCODE"
New-Item -ItemType Directory "C:\사건 자료\증거 1" | Out-Null
Copy-Item C:\dfl\qa-manual\formats\* "C:\사건 자료\증거 1\"
deepfake-lens scan "C:\사건 자료\증거 1" --recursive --json-out C:\dfl\out\windows.json --html-out C:\dfl\out\scan.html --evidence-statement-out C:\dfl\out\statement.md; "exit=$LASTEXITCODE"
$env:DEEPFAKE_LENS_REPORT_KEY = "qa-sys-4-key"
deepfake-lens evidence-statement C:\dfl\out\windows.json --json-out C:\dfl\out\statement.json --format table
deepfake-lens api-serve; "exit=$LASTEXITCODE"        # 2 + 한국어 안내
deepfake-lens web --folder "C:\사건 자료\증거 1"      # 브라우저로 http://127.0.0.1:8765/gui 열고 폴더 검사 1회
cmd /c "chcp 949 & C:\dfl\venv\Scripts\deepfake-lens.exe doctor"   # cmd 기본 코드 페이지에서 한글 확인
```

비교는 공통 준비의 스니펫으로 `linux.json`과 `windows.json`을 비교한다(경로 구분자만 다른 경우는
항목 경로를 `/`로 바꿔 비교).

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
# 비교: 공통 준비의 스니펫으로 fresh.json 과 resumed.json
find resume-500 -type f | wc -l                                   # 여전히 500
```

Windows: `Start-Process`로 실행한 뒤 `Stop-Process -Id <pid> -Force`(= `taskkill /F`)로 끊는다.
`sleep 8`은 이 저장소 기준 머신에서 약 440개를 처리한 시점이다(2026-10, 가중치 없음); 느린
머신에서는 캐시 항목이 20 이상이 될 때까지 늘린다.

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

| 수행자 | 날짜 | 커밋 | CPU / 코어 / RAM | pixel off s | pixel deep s | 사진 1장 s | WAV 1개 s | 텍스트 1개 s | 직전 대비 | 결과 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| | | | | | | | | | | |
