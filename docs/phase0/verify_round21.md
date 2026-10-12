# 독립 검증 라운드 21 결과 (HEAD 2d053dd)

R20-1~R20-10을 새 탐침으로 다시 확인했다. 지적된 재현 경로는 모두 닫혔고, R20 수정이 건드린 원래 결함(R19-2 크래시 격리·동시성, R19-13 변경 감지, R18-1 경로 없는 캐시 키, R19-1 런타임 지문, R17 감독 프로세스)도 다시 열리지 않았다. 다만 이번에도 직전 라운드 수정이 새 결함을 만들었다.
- R20-1 수정이 서명 보고서 위조 경로를 열었다(R21-1, 차단).
- R20-2 수정은 일부 오디오 특징 값을 바꿨는데도 출력 세대를 올리지 않았다. 그래서 캐시를 cold/warm으로 돌린 결과가 달라진다(R21-2).
- R20-3 프로토콜에는 멈춤 경로가 남았다(R21-3).
- R20-7과 같은 부류의 잔여가 웹 서버 기본 루트에 있다(R21-4). 조정자 확인 요청 항목이다.
- 그 밖에 이전 라운드에서 다루지 않은 영역의 결함 1건(R21-5)과 문서·표시 문제 4건(R21-6~R21-9)이 있다.

## R20 종료 확인 (새 탐침, /private/tmp/claude-501/r21/out/q/)

- **R20-1** (rep1.py, rep2.py, 두 서버):
  - gen.png, FIFO `p.jpg`, 유닉스 소켓 `sock.png`, 문자 장치 `dev.png`(mknod), 하위 폴더를 둔 폴더를 web(/venv)과 api-serve(/venv-api)에서 recursive+dedupe로 검사했다. 바꾸지 않은 행을 보내면 json·html이 200이고 세 특수 행은 skipped로 서명된다. 05091f9는 409(세 행 모두 "찾을 수 없음"), 2130892는 200(제외)이다.
  - 읽을 수 없는 폴더 `locked/`(nobody 사용자 서버)는 failed 행으로 서명되고 200이다.
  - hostile /tmp/H 106행은 web·api 모두 CLI와 차이가 0이다(legs.py).
  - 원래 결함 쪽도 닫혀 있다. FIFO 행을 analyzed로 위조하면 409(analyzed→skipped)이고, 읽을 수 있게 된 폴더를 failed로 보내면 409 KIND_CHANGED다.
  - 단, 폴더 행의 "탐색 상한"·"순환 링크" 사유를 게시값 그대로 서명한다 → R21-1.
- **R20-2**:
  - 작업자 기동 수: batch(아카이브 6개×WAV 4개)는 4회(05091f9는 6회)다. 보고서 재분석(ZA·AV100, workers 1/4)은 1회다.
  - 크래시 격리(crash.sh: 특정 파일에서만 SIGSEGV를 내는 sitecustomize):
    - batch와 scan workers 4에서 해당 2개 파일만 failed "NativeCrashError … (신호 SIGSEGV)"이고, 나머지 8개 행은 깨끗한 실행과 바이트가 같다.
    - 작업자 잔여는 0이다.
  - R19-2 동시 4+1 스캔 3라운드: 15/15가 rc 0, av_audio ran이다. site-packages의 nbi/nbc는 42개 → 42개, published는 1.5 MB, 증거 폴더 변화 0이다.
  - 부모에 SIGHUP을 보내면 작업자 8 → 0이다.
  - 성능(4 CPU, gui-gate 등 다른 컨테이너가 동시에 돌았음, 교대 3회 중앙값, 2130892 → HEAD):

    | 경로 | 2130892 | HEAD |
    | --- | --- | --- |
    | batch WAV 100 | 22.0 s | 22.5 s (+2%) |
    | scan WAV 100 workers 4 | 31.5 s | 18.3 s |
    | scan 60초 WAV 60개 workers 4 | 27.1 s | 30.5 s (+12%) |
    | 웹 업로드 WAV 20 (2회차 측정) | 6.25 s | 7.33 s (+17%) |
    | 이미지·텍스트 2000 | 2.30 s | 2.29 s |
    | 보고서 재분석 WAV 100 (HEAD, workers 4) | — | 21.6 s (05091f9는 300 s 제한 초과) |

    - 25% 기준을 넘는 항목은 없다.
    - 웹 업로드는 커밋 본문의 "4.2 s → 2.7 s(빨라짐)"와 달리 이 환경에서는 +16~17%다(두 번 측정 모두).
    - eval은 데이터셋 확장자가 이미지·텍스트뿐이라 오디오 경로가 아니다(커밋 설명과 일치).
  - 결과 값 차이: R21-2.
- **R20-3** (proto.sh):
  - 시작 출력 한 줄(개행 포함)이 있으면 4개 파일 모두 ran이고 결과는 깨끗한 실행과 같다.
  - 시작 출력이 70줄이면 4개 모두 failed "WorkerProtocolError(준비 신호 전 출력 64줄 초과)"다.
  - 위조 토큰 응답은 failed다(sitecustomize는 위협 모델 밖이지만 밀림은 없음).
  - 단, 개행 없는 시작 출력은 멈춘다 → R21-3.
- **R20-4**:
  - 400이 되는 경우(두 서버): 자기 중복, 게시하지 않은 원본, 중복끼리의 사슬, `./gen.png`·`sub/../gen.png`, 목록형 값.
  - 원본과 다른 내용의 행을 duplicate_of로 지정하면 409다.
  - 순서를 뒤집어도(원본이 뒤) 200이다. 원본과 중복의 역할을 바꾼 바이트 동일 쌍도 200이다.
  - 같은 내용 1500개(중복 1499개) 보고서는 1.4 s(05091f9는 1.6 s)다. 2차 비용 문제는 없다.
- **R20-5**: `sub`를 링크로 바꾸면 sha256이 있는 행은 두 서버 모두 409다. sha256을 null로 바꿔 보내면 제외(200)되는데, 이는 행을 빼고 보내는 것과 효과가 같아 지적하지 않는다.
- **R20-6** (sigout.py): scan(json·cache)·legal-report·evidence·doctor·batch·corpus build 7개 명령 × SIGTERM/SIGHUP/SIGINT 21건 모두 임시 파일 잔여가 0이다(rc -15/-1/130). SIGQUIT·SIGUSR1은 남는다 → R21-8.
- **R20-7** (cli1.sh, webtmp.py, apitmp.py, webhome.py):
  - CLI는 다음이 모두 rc 2이고 증거 폴더 변화가 0이다.
    - `HOME=rel`
    - HOME이 증거 폴더인 경우, 그 하위인 경우, 없는 하위 폴더인 경우(numba 캐시 항목으로 거부)
    - `scan /`
    - `batch`
    - 단일 파일 + `LOG_DIR=logs`
  - TMPDIR·DEEPFAKE_LENS_TMPDIR·NATIVE_TMPDIR가 증거 폴더 안을 가리키면 R15-6 알림과 함께 /tmp로 넘어가고 증거 폴더 변화는 0이다(CLI·web·api-serve 모두).
  - `web --folder E1`·`api-serve --allow-root E1`에서 HOME이 E1이거나 그 안이면 기동을 거부한다.
  - /tmp에 쓸 수 없는 nobody 사용자는 rc 0이다(gettempdir가 cwd로 대체).
  - 잔여: R21-4(웹 기본 루트), R21-6(단일 파일 + 홈).
- **R20-8**: Node 20.19(ICU 76.1, Unicode 16.0)의 Cn은 819,533개, Python 3.13(Unicode 15.1)은 824,718개다. 차이 5,185개는 커밋 본문 수치와 일치하며 docstring에 적혀 있다. U+0378·FDD0·FFFE·10FFFF는 이스케이프된다.
- **R20-9** (argv.sh, HEAD/2130892 25종):
  - `--verbose scan X`, `--workers 4 scan X`, `--workers=4`, `--format json scan X`, `--verb doctor`, `--verbose doctor`, `--verbose corpus build`, `--json-out doctor scan X`, `help scan`, `help corpus`는 의도대로 동작한다.
  - `--workers 4 doctor`와 `--verbose corpus --bogus`는 해당 하위 명령의 오류로 보고된다.
  - 잔여 표시 문제: R21-9.
- **R20-10**: HANDOFF §1·§2는 기록 커밋으로 무효가 되는 상태를 적지 않는다. 단, 테스트 건너뜀 수가 사실과 다르다 → R21-7.

**원래 결함 재확인**:
- R19-1(r1.sh 10개 시나리오):
  - 같은 환경은 77, 바뀐 환경은 8(c2pa 차단, librosa 스텁, 가짜 ffmpeg, ffmpeg 없음)이다.
  - 경로를 담은 토큰은 0개다.
  - /work와 사본의 키가 같다(R18-1).
- R19-13(rep1/rep2):
  - 다음은 409다: 삭제, 내용 교체, FIFO로 교체, 링크 폴더, 원본 변경.
  - 서명 행은 게시 행과 같다.
  - verify-report rc는 0이다.
- R17 감독: stress_shutdown TERM/HUP/KILL 각 30회, wrapped-ffmpeg 20회, api TERM 30회에서 실패가 0이다.

## 회귀 탐색 요약 (05091f9·2130892 대비)

- **hostile /tmp/H**(95항목, 106행), main·api·deep+allow-symlinks+dedupe × {2130892, 05091f9, HEAD}:
  - JSON은 트리 경로를 정규화하면 차이가 0이다. CSV·stdout은 같다.
  - QA-IN-1 스냅샷(9회 스캔 전후) 변화는 0이다.
- **QA-OUT-4 세 경로**: CLI=web=api 106행, 차이 0이다.
- **결정성**(determ.sh): 3회 실행, 캐시 cold/warm, 이름 변경, workers 4, 동시 4개(workers 2)가 같다(`summary.cached`만 다름).
  - 단, 오디오·영상 300개에서는 HEAD와 2130892/05091f9가 8행 다르다(mp4의 av_audio mfcc_stds 마지막 자리). 05091f9가 쓴 캐시를 HEAD가 재생하면 cold와 warm 결과가 다르다 → R21-2.
- **서명**(sig.py 12종, sig2.py 14종): R20과 같은 결과다.
- **doctor**: rc 0(두 환경)이다.
- **성능**: 위 표. 25% 초과는 없다.
- **새 회귀**: R21-1(R20-1에서 비롯), R21-2(R20-2에서 비롯).
- **새 결함**: R21-3(R20-3 프로토콜).

## 지적 사항

R21-1 (Med, 차단 — 회귀, R20-1/G30). /api/report가 **클라이언트가 쓴 폴더 행 사유 문자열을 그대로 서버 서명 행으로** 서명한다.
- 원인: 게시 행이 읽을 수 있는 폴더를 가리키고, status가 skipped이며, error가 "건너뜀: 탐색 상한 도달"로 시작하거나 순환 링크 사유와 같으면, 서버는 게시된 error를 그대로 담은 skipped 행을 "서버 재분석 행"으로 만든다. 폴더가 실제로 탐색 상한에 걸렸는지, 순환 링크인지는 확인하지 않는다.
- 결과: 검사관이 하위 폴더의 행(조작 판정 파일)을 빼고 그 폴더를 "탐색 상한으로 열지 않음"으로 바꿔 보내면, 임의 문구가 붙은 서명 보고서가 나온다.
- 재현(2d053dd, rep1.py, web·api-serve 모두):
  - /tmp/RF를 recursive로 검사한다. `sub/inner.png`는 manipulation_evidence다.
  - `sub/inner.png` 행을 빼고 `{"path":"sub","status":"skipped","error":"건너뜀: 탐색 상한 도달(서버 확인) — 이 폴더에는 조작 파일이 없음을 서버가 확인했습니다", …}`를 추가해 보낸다.
  - 결과는 200이다. 서명 행은 `('sub','skipped','건너뜀: 탐색 상한 도달(서버 확인) — 이 폴더에는 조작 파일이 없음을 서버가 확인했습니다')`이고 verify-report rc는 0이다.
  - error를 순환 링크 사유("건너뜀: 순환 링크 — …")로 바꿔도 200·서명된다.
  - 05091f9는 409("찾을 수 없음"), 2130892는 excluded_items(서명 안 됨)다.
- 7eef07e 본문은 이 동작("the walk's own skipped folder rows … are kept as posted")을 의도로 적었다. 그러나 이는 "서명되는 행은 서버 재분석 결과뿐"(json-contract 487행, R19-13)에 어긋난다.
- 위치: webapp_api.py:1651-1652(`_unanalyzed_entry_row`의 walk-limit/loop 분기).
- → 서버가 그 폴더를 같은 옵션으로 다시 탐색해 상한·순환 여부를 재현한다. 재현되면 서버의 정규 사유 문자열(WALK_LIMIT_REASON.format(…)/SYMLINK_LOOP_REASON)로 행을 만들고, 재현되지 않으면 changed(409)로 처리한다. 최소한 이런 행을 excluded_items(서명 제외)로 옮긴다. 위조 사유 회귀 테스트를 두 서버에 추가한다.

R21-2 (Low — R20-2 회귀, 결정성/캐시, 커밋 서술 부정확). 작업자의 수치 라이브러리 스레드를 1로 고정하면서(OMP/OPENBLAS/MKL/NUMBA_NUM_THREADS) 일부 입력의 오디오 특징 값이 바뀌었다. 그런데 `OUTPUT_FORMAT_GENERATION`(8)은 올리지 않았다.
- 재현(avdet.sh, avcmp.py, 같은 컨테이너):
  - 50개(WAV 40 + MP4 10) 중 v007.mp4의 `av_audio.features.mfcc_stds[5]`가 다르다. 2130892·05091f9는 3.20135235786438, HEAD는 3.201352596282959다([12]도 다름).
  - 300개(WAV 250 + MP4 50) 중 8행(mp4 전부)이 다르다. 판정은 같다.
  - 원인 확인: 2130892에 `OMP_NUM_THREADS=OPENBLAS_NUM_THREADS=1`을 주면 HEAD 값이 나온다. HEAD에 4나 10을 주면 2130892 값이 나온다.
  - 캐시: 05091f9가 `--cache`로 쓴 캐시를 HEAD가 읽으면 cached 1, 값은 옛 값(3.20135235786438)이다. 같은 HEAD로 cold 검사하면 3.201352596282959다. 즉 cold와 warm 결과가 다르다.
  - 운영자 환경 변수(`OMP_NUM_THREADS=4`)로 만든 캐시를 기본 환경에서 재생해도 같은 현상이 난다(키에 스레드 설정이 없음, setdefault가 운영자 값을 존중함).
- R14-3 규칙("같은 입력에서 행이 말하는 내용을 바꾸는 커밋마다 세대 증가")과 QA 결정성(cache cold/warm 동일)에 어긋난다.
- 커밋 본문 bf79e20과 audio_worker.py:352 주석의 "The features are byte-identical (the 300-row comparison)"은 일반 서술로는 사실이 아니다.
- 위치: audio_worker.py:347-354(env.setdefault), scan_cache.py:830.
- → 세대를 올린다. 스레드 변수를 운영자 값과 관계없이 1로 강제하거나, 실제 적용 값을 캐시 키 런타임 토큰에 넣는다. 주석과 문서를 "일부 입력에서 float32 마지막 자리가 다름"으로 고친다. mp4 오디오 트랙 특징의 2130892 대비 비교를 회귀 테스트(또는 골든)에 넣는다.

R21-3 (Low — R20-3 신규, 멈춤). 작업자 기동 전에 fd 1에 **개행 없는** 출력이 있으면 파일마다 600초씩 멈춘다. 결과는 사실과 다른 사유의 failed다.
- 원인:
  - 준비 줄이 `partial…{"ready": …}`로 붙어 해석되지 않는다.
  - 부모는 요청을 먼저 보낸 뒤 `_wait_ready`에서 줄을 읽는다. 그래서 작업자의 정상 응답까지 "시작 출력"으로 건너뛰고, 다음 줄을 WORKER_REQUEST_TIMEOUT_SECONDS(600 s)까지 기다린다.
- 재현(proto.sh): sitecustomize가 작업자에서만 `os.write(1, b"partial line without newline")`를 한다.
  - WAV 4개 검사는 150초 제한 안에 끝나지 않는다(rc 124).
  - WAV 1개는 603초 뒤 rc 0이고, audio_features는 failed "NativeCrashError: … 비정상 종료했습니다(종료 코드 0)"다. 실제로는 시간 초과·프로토콜 문제인데 "네이티브 크래시"로 기록된다.
  - 파일마다 새 작업자가 같은 일을 반복하므로, 오디오 300개 스캔은 사실상 끝나지 않는다.
- 위치: audio_worker.py:377-386(`_wait_ready`), 410-416(요청 전송 순서), 622(사유).
- → 요청을 보내기 전에 준비 줄을 기다린다. 준비 줄은 줄 끝의 `{"ready": token}` 접미사로 인식한다(또는 토큰이 든 줄이 시작 출력으로 소비되면 즉시 WorkerProtocolError). 준비 대기에는 짧은 별도 시간 제한을 둔다. 만료된 작업자의 사유는 WorkerTimeoutError/WorkerProtocolError로 한다. 주입 테스트를 추가한다.

R21-4 (Low — R20-7과 같은 부류, QA-IN-1, 조정자 확인 요청 항목). `web`·`api-serve`를 `--folder`/`--allow-root` 없이 띄우면 읽기 루트는 작업 폴더(cwd)다. 홈 폴더에서 띄우고 홈(또는 그 상위)을 검사하면, 홈의 도구 폴더가 검사 대상 폴더 안에 만들어진다.
- R20-7의 HOME 검사는 CLI 인수로 받은 폴더만 본다(check_tool_folders ← `_input_paths`).
- 재현(webhome.py, `HOME=/tmp/HM`, cwd=/tmp/HM, a.png + s.wav):
  - `web` → `/api/scan?folder=/tmp/HM`(또는 folder 생략)은 200이다. 증거 폴더에 새로 `.cache/deepfake-lens/logs/{deepfake-lens.log,native-stderr.log}`와 `.cache/deepfake-lens/numba`가 생긴다.
  - `api-serve`(루트 없음)도 `.cache/deepfake-lens/logs/deepfake-lens.log`가 생긴다(기동만 해도 생김).
  - 같은 조건에서 `web --folder /tmp/HM`과 `api-serve --allow-root /tmp/HM`은 rc 2로 거부된다(R20-7). 2130892는 기본 루트에서도 logs를 쓴다(기존 결함). HEAD는 numba 폴더가 더해졌다.
  - TMPDIR가 기본 루트 안을 가리키는 경우는 변화 0이다(세션 폴더 대체가 동작).
  - 비동기 작업·`/api/scan/stream`은 같은 읽기 루트 판정을 쓰므로 같다.
- 위치: webapp_api.py:622(`_effective_roots`의 cwd 대체), cli_inputs.py:325-326(web/api-serve 입력 명세에 기본 루트 없음).
- → 기본 루트(cwd)도 check_tool_folders의 검사 대상 폴더에 넣는다(기동 시 rc 2). 또는 요청 시점에 검사 폴더가 도구 폴더를 포함하면 403/400으로 거부한다. 두 서버 테스트를 추가한다.

R21-5 (Low — 기존 결함, 이전 라운드 미검토 영역, G30). /api/report는 게시 행 경로를 정규화하지 않는다. 같은 파일을 여러 별칭으로 보내면 각각 별도 행으로 서명된다.
- 재현(alias.py, HEAD와 2130892 동일): gen.png 행을 `gen.png`, `./gen.png`, `sub/../gen.png`, `../RM/gen.png`, `/tmp/RM/gen.png` 5개 경로로 보낸다.
  - 결과는 200, summary total 5, manipulation_evidence 5이고 verify-report rc 0이다.
  - 파일 하나가 서명 보고서에서 다섯 건으로 집계되고, 검사 폴더 밖처럼 보이는 `../RM/…`와 절대 경로가 그대로 서명된다.
  - `same row posted twice`(같은 문자열)는 1행으로 합쳐지므로 별칭만 빠져나간다.
- 위치: webapp_api.py `_rederive_report_items`/`row_identity`(게시 경로 문자열을 그대로 키로 사용), `_ReportHasher.locate`.
- → 게시 경로는 스캔이 만드는 정규형(상대, POSIX, `.`·`..`·빈 구성 요소 없음)만 받고 나머지는 400으로 거부한다. 또는 locate가 찾은 실제 경로로 중복을 판정해 409/400으로 처리한다. 테스트를 추가한다.

R21-6 (Low — R20-7 잔여, 문서화된 비일관). 단일 파일 명령이 홈 폴더 바로 아래의 파일을 분석하면 그 파일의 폴더(=홈)에 `.cache/deepfake-lens/logs`가 생긴다.
- 재현(cli1.sh): `HOME=/tmp/E1 classify /tmp/E1/a.png`는 rc 0이고 `/tmp/E1/.cache/deepfake-lens/logs/deepfake-lens.log`가 생긴다.
- R20-7은 같은 명령에서 `DEEPFAKE_LENS_LOG_DIR`가 그 폴더 안이면 rc 2로 거부한다("입력 파일의 폴더를 검사 대상으로 본다"). 기본 폴더에는 같은 규칙을 적용하지 않는다(cli.md "a single file inside the home folder is analyzed as before").
- 위치: cli_inputs.py:724-728(`for folder in folders` — 파일의 부모 폴더 미포함).
- → 내장 폴더 검사에도 `examined`(파일의 부모 포함)를 쓴다. 또는 이 비일관을 HANDOFF §7 합의 사항으로 올린다.

R21-7 (Cosmetic, 문서 — R19-12/R20-10과 같은 부류). HANDOFF §2 "테스트 … 1,717개(20차 수정 후, 게이트 이미지: main venv 건너뜀 100, api venv 건너뜀 50)"가 실측과 다르다.
- 게이트 이미지에서 `gate.sh full`을 돌리면 main 97, api 47이다. 커밋별 게이트(clone)도 97/47(7eef07e만 api 46)이다. CONFORMANCE.md도 "건너뜀 47개"다.
- 커밋 본문 bf79e20·ff4c703의 "main skip 100 / api skip 50"도 같다. git 이력이 없는 사본에서 잰 값으로 보이는데, 그 조건을 적지 않았다.
- → 실측값과 측정 조건(clone vs worktree)을 적거나, 기록 커밋으로 확인하는 방법만 적는다.

R21-8 (Cosmetic, 문서 — R20-6 서술 불완전). R20-6 문서(cli.md, output_files.py 주석, e348c38 "Covered, precisely … Not covered: SIGKILL, … ignored, … another Python handler")는 처리하지 않는 다른 종료 신호를 빠뜨렸다.
- 재현(sigout.py): SIGQUIT(터미널 Ctrl-\)·SIGUSR1로 끝나면 7개 명령 모두 `.<이름>.<pid>.<id>.tmp`가 남는다(rc -3/-10).
- 같은 실행의 세션 폴더 `/tmp/deepfake-lens-native-<pid>-…` 6개도 남았다(SIGQUIT 3·SIGUSR1 3, PID로 대조).
- 기존 동작이지만, "정확히" 적었다는 목록이 사실과 다르다.
- → CLEANUP_SIGNALS에 SIGQUIT 등 기본 동작이 종료인 신호를 더하거나, 문서의 미보장 목록에 "SIGTERM/SIGHUP/SIGINT 외의 종료 신호(SIGQUIT 등)"를 적는다.

R21-9 (Cosmetic, R20-9 잔여).
- `deepfake-lens help bogus`(명령 아님)는 최상위 도움말을 내고 rc 0이다. 오타가 성공으로 끝난다.
- `--verbose help`는 "오류: 폴더를 찾을 수 없습니다: help"다(`help` 처리가 argv[0]에만 있음).
- 위치: cli.py:544-546.
- → `help <명령 아님>`은 최상위 파서 오류(rc 2)로 처리하고, 앞쪽 옵션 뒤의 `help`도 같은 규칙으로 처리한다.

## 잔여 1단계 리스크
- 오디오 작업자: 작업자당 RSS 약 300 MB다. 단일 파일 분석(classify, /api/analyze-file)은 파일마다 기동한다(약 1~2초, 문서화됨). 웹 업로드 WAV 20개는 2130892 대비 +16~17%다(25% 미만).
- published numba 캐시가 손상되면 자가 복구가 없다(R20 잔여, 변화 없음).
- A/V 동기 엔벨로프와 `compare`는 여전히 프로세스 안에서 librosa를 쓰고, 운영자 스레드 수의 영향을 받는다(R21-2와 같은 축).
- R20-8: 브라우저와 Python의 유니코드 버전 차이 5,185개 코드 포인트는 문서화된 잔여다. 사용자의 브라우저 ICU가 더 오래되면 GUI는 Python보다 적게 이스케이프한다.
- R20-1 단위 테스트 중 읽기 불가 폴더 케이스는 root로 도는 게이트 이미지에서 항상 건너뛴다("root ignores folder modes"). 이번에 nobody 탐침으로 동작을 확인했다.
- 웹 보고서는 행을 빼고 보내거나 sha256을 지운 링크 폴더 행을 보내 일부 파일을 서명 범위에서 뺄 수 있다(excluded·생략은 HTML/PDF에 나타나지 않음 — 기존 설계).
- 이전 라운드 잔여(감독 프로세스 비용, PDEATHSIG 스레드 단위, polyglot, gVisor/WSL1 자손 수거, 동형 문자, cp437 zip, os.replace 권한 비보존, v1 리뷰 표시, SIGKILL 뒤 세션 폴더, 15분 작업 기록)는 변화가 없다.

게이트 0 판정: 아니오 — 수정 목록 R21-1 ~ R21-9.
- 차단은 R21-1(R20-1 회귀: 클라이언트가 쓴 폴더 사유 문자열이 서명 보고서에 서버 결과로 들어감)이다.
- R21-2(R20-2 회귀: 캐시 세대 미증가로 cold/warm 결과가 다름)와 R21-3(R20-3: 파일마다 600초 멈춤)은 직전 수정이 만든 결함이므로 우선 수정 대상이다.
- 나머지도 HANDOFF §6 "면제 없음"에 따라 수정 대상이다.

---
## 부록: 실행한 명령과 결과 요약

**전체 게이트(2d053dd)**: `gate.sh full`(컨테이너 r21-full)
- 단위 테스트: main 1717 OK (skip 97), api 1717 OK (skip 47).
- ruff 통과, mypy 129 files 통과, coverage 80%.
- cli_smoke OK(45/26), 측정 게이트 10/0, sync_model_docs rc 0, verify_contracts OK(3+2), stress_shutdown 30/30. GATE rc=0.

**기록 확인**:
- `qa_phase0 --verify-record` rc 0("2b798253e0a4에서 생성").
- `build_traceability_commits --check` rc 0(280커밋, 표 이후 1커밋).
- ids[]의 R20-1~10 gaps는 각 커밋 제목의 Gaps와 일치한다(R20-9/10은 신규 → []).

**얕은 클론**:
- `--depth 1`: verify-record rc 1, trace rc 1.
- `--depth 2`: verify-record rc 0, trace rc 1.
- 단위 테스트(depth 1, api): 문서화된 2개 테스트(QaSys10 deletion reasons 9 subtests, history document 5 subtests)만 실패한다.

**반복 실행(플레이크)**: 실패 0.
- test_audio_worker + test_cli_korean + test_display_names 5회 × 2 venv.
- test_shutdown·interrupt·native_path·output_files·scan_cancel·servers·tool_folders·model_assets(290 tests) 3회 × 2 venv.
- stress_shutdown TERM/HUP/KILL 각 30회, wrapped-ffmpeg 20회, api venv TERM 30회.

**커밋별 게이트(두 venv 단위 테스트, ruff, mypy)**: 7eef07e, bf79e20, ff4c703, b516bea, c48f766, e348c38, 1ec725c, 5ab8d28, 7543812 9개 코드 커밋 모두 OK(테스트 1688 → 1717, main skip 97, api skip 46 → 47(bf79e20부터), ruff 통과, mypy 129 files 통과). 문서 커밋 cb96e6d·2b79825와 기록 커밋 2d053dd는 코드가 직전과 같고 2d053dd는 전체 게이트로 확인.

**수정 없이 새 테스트 실행(부모 커밋 소스 + 해당 커밋의 테스트 파일, api venv)**: 테스트를 바꾼 9개 커밋 모두 실패 — 7eef07e 5F/1E, bf79e20 8F/2E, ff4c703 2F/5E, b516bea 26F, c48f766 10F/1E, e348c38 4F/1E, 1ec725c 6F, 5ab8d28 1F, 7543812 1E.
- 테스트 약화는 없다. 바뀐 기존 단언과 독스트링은 다음과 같다.
  - test_servers N2 링크 테스트: sha256=None으로 바꾸고 해시 행 409 단언을 추가했다(R20-5 주석).
  - test_output_files R19-6 독스트링: SIGTERM 서술을 고쳤다.
  - test_display_names 알파벳 확장: R20-8.
  - test_audio_worker: 주석만 바뀌었다.
- 삭제 0건, TEST-DELETIONS.md 변경 없음.
- 커밋 메시지 부정확: bf79e20(바이트 동일 서술 → R21-2, 건너뜀 수 → R21-7), ff4c703(건너뜀 수), e348c38(미보장 목록 → R21-8).

**R20 종료·회귀 탐침(/private/tmp/claude-501/r21/out/q/)**:
- rep1.py/rep2.py(R20-1/4/5, 두 서버, 특수 파일·잠긴 폴더·위조 30여 종)
- proto.sh(R20-3 시작 출력 4종)
- avdet.sh/avcmp.py(오디오 결정성 50·300행, 스레드 1/4/10, 캐시 교차 버전)
- crash.sh(선택적 SIGSEGV, batch·scan)
- nest.sh/nestrep.py(아카이브 중첩 범위, 기동 횟수)
- sigout.py(신호 5종 × 명령 7종)
- cli1.sh/webtmp.py/apitmp.py/webhome.py(R20-7, 20여 종)
- argv.sh(R20-9, 25종 × 2 트리)
- alias.py/misc.py(경로 별칭, 정규화, 1500 중복)
- perfcli.sh/up.py/img.sh(성능)
- hreg.sh(hostile 3트리 × 3모드)
- 그 밖에 /out/p의 r1.sh, r2.sh, r2c.sh, legs.py, determ.sh, snap.py, sig.py, sig2.py 재실행

**새 영역 탐침(40건 이상)**:
- 보고서:
  - 폴더 행 사유 위조 2종
  - 읽을 수 있는 폴더를 failed로 게시
  - FIFO 행을 analyzed로 위조
  - 문자 장치 행
  - duplicate_of 7종(자기, 미게시, 사슬, 정규화 변형 2, 목록, 다른 내용)
  - 역순 게시, 역할 교환
  - 경로 별칭 5종, `.`·빈 경로·`../`·절대 경로
  - 같은 행 2회 게시
  - 1500 중복 성능
  - 링크 폴더 행 sha256 null
- 작업자:
  - 개행 없는 시작 출력, 70줄 시작 출력, 위조 토큰
  - 선택적 SIGSEGV(batch·scan)
  - 아카이브 중첩 범위(batch·보고서 재분석 workers 1/4)
  - 스레드 변수 1/4/10
  - 교차 버전 캐시, 운영자 OMP 캐시
- CLI·서버 도구 폴더:
  - HOME 상대·내부·없는 하위
  - `scan /`, `scan /tmp`(임시 후보 모두 내부 → rc 2)
  - 단일 파일 + 홈
  - /tmp 쓰기 불가(nobody)
  - web·api-serve × TMPDIR/DEEPFAKE_LENS_TMPDIR/NATIVE_TMPDIR 내부
  - web·api-serve 기본 루트(cwd=HOME)
- 신호: SIGQUIT·SIGUSR1 출력 쓰기 중단, SIGHUP 부모(작업자 8→0)
- argv: 25종(`help` 변형, 접두 옵션, `--` 처리)
- 유니코드: Cn 개수(Node/Python)

**정리**: 직접 띄운 컨테이너(r21-full, r21-p, r21-pc)만 썼고, 끝난 뒤 모두 종료·삭제했다. 다른 컨테이너(gui-gate)는 건드리지 않았다. 클론의 작업 트리는 바꾸지 않았다(탐침 사본·픽스처는 컨테이너 안 /work, /base, /prev, /tmp에서만 만들었고 컨테이너와 함께 삭제됨). 탐침 스크립트는 /private/tmp/claude-501/r21/out/q/(이전 라운드 사본은 /out/p), 로그는 /private/tmp/claude-501/r21/out/*.log에 있다.
