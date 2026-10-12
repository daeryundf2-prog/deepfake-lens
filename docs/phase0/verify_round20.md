# 독립 검증 라운드 20 결과 (HEAD 05091f9)

R19-1~R19-13을 새 탐침으로 다시 확인했다. 지적 내용 자체는 모두 닫혔다. 다만 R19-13 수정이 새 회귀(R20-1)를, R19-2 수정이 큰 성능 회귀(R20-2)와 새 결함(R20-3)을 만들었다. R19-13·R19-6·R19-4·R19-8·R19-9·R19-12는 같은 부류의 잔여가 남아 있다(R20-4~R20-10).

- R19-1: 같은 캐시 파일을 /venv → /venv-api → /venv → /venv-api 순서로 썼다. 첫 두 실행은 cached 8(같은 스캔 안의 같은 내용 재사용으로, 2130892도 8), 이어진 두 실행은 77이다. 환경을 바꾸면 재생되지 않는다. 다음 세 경우에도 cached 8이다.
  - PYTHONPATH의 c2pa.py 스텁(ImportError) → 차단 해제(77)
  - librosa 스텁
  - PATH 앞의 가짜 ffmpeg(버전 문자열 다름; 같은 상태로 다시 돌리면 77)
  - ffmpeg 없는 PATH
  - `dependency_tokens()` 27개 중 경로 문자를 담은 토큰은 0개다. /work와 복사본 /w2의 키는 `4bc65f49…`로 같다.
- R19-2:
  - numba 캐시를 지운 상태에서 av.mp4 폴더를 4개 동시에 검사하고 이어서 단독으로 1회 검사했다. 이를 3라운드 반복해 15회 모두 rc 0, av_audio ran이다.
  - site-packages의 *.nbi/*.nbc는 42개 → 42개, 증거 폴더 변화 0, published 1개(1.5 MB), staging 잔여 0이다.
  - 작업자에 SIGSEGV·SIGABRT·exit 0·exit 3을 주입했다. 넷 다 스캔 rc 0이고 audio_features는 failed "NativeCrashError … (신호 SIGSEGV)" 등이며 작업자 잔여는 0이다.
  - 부모 스캔(60개 WAV, workers 4)에 SIGTERM·SIGKILL·SIGINT·SIGHUP을 보냈다. 작업자 4개(감독 포함 8 프로세스)가 2초 안에 모두 사라졌다. SIGKILL 뒤에는 세션 폴더(작업자 numba 복사본 포함 7.1 MB)가 남는데, 이는 기존 설계(다음 실행의 오래된 세션 정리)대로다.
  - 운영자가 지정한 NUMBA_CACHE_DIR이 증거 폴더 안을 가리켜도 증거 폴더는 바뀌지 않았다. HOME 미설정, 읽기 전용 HOME(nobody 사용자)에서도 rc 0, ran이다.
  - 웹 analyze-file 뒤 작업자 잔여는 0이다.
- R19-3:
  - 실제 비동기 작업을 4초 뒤 취소했다(333행, files_cancelled 67).
  - 자기 작업 ID를 보내면 67/"server"이고, 10**18을 게시해도 67이다. 작업 ID가 없거나 모르는 ID면 0/"unverified"다.
  - analysis_options는 서명 본문에 들어간다. GUI 쪽 수정은 노드 테스트로만 확인했다.
- R19-4: 다음은 모두 rc 2(작업 전)이다.
  - `cd /tmp/T1; DEEPFAKE_LENS_LOG_DIR=logs scan .`
  - `~nouser/l`
  - `/tmp/T1/../T1/x`
  - 증거 폴더로 가는 심볼릭 링크 경유
  - `HEATMAP_DIR=hm --heatmaps`
  - 웹(`web --folder /tmp/W1`, cwd 안쪽)도 기동 거부다.
  - 잔여: R20-7.
- R19-5: CLI 인수 17종(리터럴 `~nouser` 폴더 포함)에서 영어 traceback 0이다. 웹 10종은 403/404/200(리터럴 폴더 등록 시)이고 500은 0이다.
- R19-6:
  - 쓰기 도중(fsync) SIGINT를 주면 rc 130이고 임시 파일 잔여 0이다.
  - SIGTERM/SIGHUP을 주면 임시 파일이 남는다(R20-6).
- R19-7, R19-8, R19-11:
  - R19-7: hostile 비교에서 변화 0, APNG 테스트 통과.
  - R19-8: U+2800, Co(E000·F0000·100000), 기본 무시 문자(미할당 포함) 30여 종이 이스케이프된다. Cn은 잔여(R20-8).
  - R19-11: 패키지 어디에도 법조 자동 기재가 없다(GUI·HTML·PDF 포함).
- R19-9: `corpus build … --bogus`, `corpus --bogus build`, `--bogus scan X`는 올바른 파서가 보고한다. 알려진 scan 옵션이 명령 앞에 오는 경우는 잔여다(R20-9).
- R19-10: gui.js의 계층 진단 두 곳과 상세 카드가 null이면 "없음"이다.
- R19-12: HANDOFF §4의 게이트 이미지 설명은 실측과 일치한다(ffmpeg 7.1.5, node 20.19, 각 venv 패키지). 277커밋(9142684 기준), 테스트 1,684개, 커버리지 80%도 맞다. 그러나 HEAD(05091f9)에서는 §1·§2가 다시 사실과 다르다(R20-10).
- R19-13 (두 서버, json/html/pdf/evidence): 다음은 모두 409이고 changed_rows 형식이 맞다.
  - 내용 교체, 삭제, 압축 구성 파일 교체·삭제
  - 중복 사본 변경, 원본 변경
  - 파일을 심볼릭 링크·FIFO로 교체
  - 게시 sha256 null·status 위조
  - 바뀌지 않은 행은 json·html 바이트가 같고 verify-report가 검증됨이며, 게시 행과 서명 행의 필드 차이는 0이다.
  - 그러나 정상 폴더에서 거짓 409(R20-1)가 나고, 중복 행 검증(R20-4)과 링크 폴더 교체(R20-5, 조정자 후보)가 남았다.

회귀 탐색 결과는 다음과 같다.
- 악성 폴더 /tmp/H(95 항목, 106행)를 2130892와 HEAD에서 main·api·deep-signals+allow-symlinks로 비교했다.
  - JSON 차이는 모델 프로필 경로(/base vs /work)뿐이다. CSV·stdout은 동일하고, HTML은 본문 SHA-256 줄만 다르다.
  - QA-OUT-4 세 경로(CLI/web/api-serve) 106행 차이 0, QA-IN-1 스냅샷(deep·heatmap·cache·allow-symlinks·증거설명서) 변화 0이다.
- 결정성: 3회 실행, 캐시 cold/warm, 이름 변경, 워커 4, 4개 동시 스캔이 모두 같다(`summary.cached`만 다름).
- 서명: 기존 12종과 새 14종 모두 기대대로다(R20 부록).
- 성능:
  - 이미지 2000개: cProfile 호출 수 53,308,363 → 53,308,405(+42). 교대 3회 기준 wall +0.4%, CPU +0.9%다.
  - 오디오·영상 300개(WAV 250 + MP4 50), 각 3회 교대 측정을 두 번 했다.
    - 1차(부하 높음, load 3.6→9.8, 다른 컨테이너 동시 실행): workers 1에서 wall +7.1%·CPU +5.2%, workers 4에서 wall +18.6%·CPU +29.9%.
    - 2차(내 컨테이너 하나만 실행): workers 1에서 wall +1.6%·CPU +1.6%, workers 4에서 wall +10.3%·CPU +15.5%.
    - 스캔 자체는 25% 기준 아래다.
  - 스캔 밖 경로(batch, 업로드, eval/benchmark)는 7~9배 느려졌다(R20-2).
- 새 회귀: R20-1(R19-13에서 비롯), R20-2(R19-2에서 비롯). 새 결함: R20-3(R19-2 작업자 프로토콜).

R20-1 (Med, 차단 — 회귀, R19-13/G30). 검사 대상 폴더에 FIFO·소켓이나 읽을 수 없는 하위 폴더가 있으면 /api/report가 모든 형식에서 409를 낸다. 두 서버 모두 같다. 파일은 바뀌지 않았다.
- 재현(05091f9):
  - `/tmp/FF`에 gen.png(A1111), FIFO `p.jpg`, 유닉스 소켓 `sock.png`를 둔다.
  - `web --folder /tmp/FF` → /api/scan(3행; p.jpg·sock.png는 skipped) → 같은 행을 /api/report로 보낸다.
  - json·html 모두 409이고, changed_rows는 `[p.jpg skipped→None "읽기 루트 안에서 파일을 찾을 수 없어 서버가 재분석하지 못했습니다", sock.png …]`다.
  - 2130892는 200이다(두 행 excluded, gen.png 서명).
- 읽을 수 없는 하위 폴더(`locked/`, chmod 000, nobody 사용자 서버)도 같다. 스캔 행은 `locked` failed이고 보고서는 409(2130892는 200)다.
- /tmp/H(hostile 106행)는 pipe.jpg·pipe.txt 때문에 web·api, dedupe 유무와 관계없이 4회 모두 409다. 이런 폴더에서는 서명 보고서를 하나도 만들 수 없다.
- GUI는 "바뀐 파일: p.jpg"를 보여 주므로, 검사관에게 증거 폴더를 고치라고 유도한다.
- 위치:
  - webapp_api._ReportHasher.locate(webapp_api.py:1434-1460): 일반 파일과 링크만 받고, 나머지는 REPORT_ROW_NOT_FOUND로 처리한다.
  - `_CHANGED_AFTER_SCAN_REASONS`(webapp_api.py:1287)가 NOT_FOUND를 "변경"으로 센다.
- → locate는 lstat이 되는 항목이면 경로를 돌려준다. 재분석은 스캔과 같은 skipped/failed 행을 만들고(같으면 통과), "찾을 수 없음"은 lstat 실패에만 쓴다. FIFO·소켓·읽기 불가 폴더 행을 포함한 두 서버 테스트를 추가한다.

R20-2 (Med, 차단 — 성능 회귀 >25%, R19-2). 오디오 특징 추출 작업자가 스캔 범위(worker_scope) 밖에서는 파일마다 새로 뜬다. 한 번 뜰 때 약 2초(published 시드 기준; 시드 없으면 약 7초)이고 RSS는 약 300 MB다. 요청 자체는 0.12초다.
- 측정(같은 컨테이너, 2130892 → 05091f9, 각 2회):

  | 경로 | 2130892 | 05091f9 |
  | --- | --- | --- |
  | `batch /tmp/AV100 --output …`(WAV 100개) | 11.8 s / 11.7 s | 87.3 s / 84.0 s (약 7.3배) |
  | 웹 `/api/analyze-upload` WAV 30개 | 3.2 s / 3.4 s | 29.6 s / 32.5 s (약 9배) |

- api-serve 업로드는 /venv-api에 librosa가 없어 차이가 없다.
- 같은 경로를 쓰는 곳: evaluate.evaluate_dataset·evaluate_robustness(파일마다 analyze_file) → `eval`, `benchmark`, calibrate. 측정 게이트(클래스당 200건)의 오디오 평가 비용이 파일당 2초 늘어난다.
- 문서(cli.md "a single-file analysis starts one per file")에는 적혀 있지만, 여러 파일을 처리하는 명령에도 적용된다.
- 스캔 자체는 workers 4에서 wall +10~19%·CPU +15~30%(부하에 따라 다름)이고, workers 8이면 작업자 RSS 합계가 2.1 GB다.
- 위치:
  - audio_worker.extract_features: scope가 None이면 `_start()`와 `_forget()`을 요청마다 한다.
  - cli.py:1011(batch), webapp_api._analyze_upload_payload(webapp_api.py:943-990), api_server.py:561·748(업로드), evaluate.py:106·342.
- → 이 반복문들을 `audio_worker.worker_scope()`로 감싼다(스레드 풀 작업은 `scope.bind`). 또는 프로세스 수준에서 유휴 시간에 은퇴하는 작업자 풀을 둔다. batch/업로드/eval 처리량 회귀 테스트(호출 수 또는 작업자 기동 횟수)를 추가한다.

R20-3 (Med, R19-2 신규 결함 — fail-closed 위반). 작업자 프로토콜이 한 번 어긋나면 그 스레드에서 이후 모든 파일이 **직전 파일의 오디오 특징**을 받는다. 경고는 없다.
- 작업자는 serve()에서 fd 1을 fd 2로 돌리기 전에 인터프리터를 기동한다. 그 사이 fd 1에 한 줄이라도 쓰이면(sitecustomize·.pth·네이티브 초기화 출력) 부모의 `json.loads`가 실패한다. 그런데도 작업자를 버리지 않는다.
- 재현:
  - PYTHONPATH에 sitecustomize.py를 둔다(작업자에서만 `os.write(1, b"hello\n")`).
  - 200/900/3000/6000 Hz 사인파 WAV 4개를 담은 /tmp/DS3을 검사한다(rc 0).
  - s0은 audio_features failed "JSONDecodeError …"다.
  - s1(900 Hz)의 근거는 "비정상적 고조파 안정성 0.042"(s0의 값)다.
  - s2(3000 Hz)의 근거는 "높은 평균 피치(900Hz)"다.
  - s3(6000 Hz)의 근거는 "높은 평균 피치(3000Hz) … 중심 주파수(3002)"다.
  - 오류가 없는 실행과 비교하면 한 칸씩 밀려 있다.
- 위치: audio_worker._Worker.ask(`reply = json.loads(line)`에 예외 처리 없음, 작업자 유지), serve(요청 식별자 없음).
- → 해석할 수 없는 응답은 작업자 폐기(_forget) + 해당 검사 failed로 처리한다. 요청 ID(또는 경로)를 응답에 되돌려 받아 대조하고, 기동 직후 준비 신호 줄로 동기화한다. 주입 테스트를 추가한다.

R20-4 (Low, R19-13(J-2) 잔여, G30). 게시된 중복 행의 `duplicate_of`를 검증하지 않는다. 조작 파일을 "자기 자신의 중복"이나 "게시하지 않은 파일의 중복"으로 보내면, 분석 없는 duplicate 행으로 서명된다.
- 재현(두 서버 모두 200):
  - gen.png(A1111, manipulation_evidence) 행을 `status: duplicate, duplicate_of: "gen.png", result: null`로 바꿔 plain.png 행과 함께 POST한다.
  - 결과는 200이다. 서명 행은 `('gen.png','duplicate','gen.png',None)`, summary는 duplicates 1·manipulation_evidence 0이고 verify-report rc 0이다.
  - `duplicate_of: "zcopy.png"`(게시하지 않음)도 200이다.
- 위치: webapp_api._as_duplicate, _rederive_report_items.
- → duplicate_of ≠ path이고, 대상 행이 같은 요청에 게시되어 있으며(앞선 순서), 대상 자체는 duplicate가 아니어야 한다. 아니면 409(또는 400)로 거부한다.

R20-5 (Low, R19-13 잔여 — 조정자 후보 재현됨, 두 서버). 스캔 뒤 행 경로 중간의 폴더를 심볼릭 링크로 바꾸면, 그 행은 409가 아니라 조용히 excluded_items로 옮겨지고 200으로 서명된다.
- 재현: /tmp/R13을 recursive 검사한다(sub/inner.png는 sha256이 있고 manipulation_evidence). 그 뒤 `sub`를 같은 내용 폴더로 가는 링크로 바꾸고 같은 행을 보낸다.
- web(/venv, /venv-api)과 api-serve 모두 json·html·pdf·evidence가 200이다. excluded는 `sub/inner.png — 경로 중간의 폴더가 심볼릭 링크라 따라가지 않았습니다 …`다.
- 웹 스캔은 링크 폴더를 따라가지 않으므로, 스캔 때 sha256이 있던 행이 보고 시점에 LINK_ON_PATH가 되는 경우는 스캔 뒤 변경뿐이다.
- R19-13의 요구("다시 만들 수 없는 행이 있으면 모든 서명 형식을 409")와 docs/deepfake-lens-json-contract.md:487의 취지에 맞지 않는다. service.md가 이를 "still only excluded"로 문서화한 것은 구현자의 선택일 뿐, HANDOFF §7의 합의 사항이 아니다.
- HTML·PDF의 "서명 제외" 절에는 남으므로 완전히 숨겨지지는 않는다. 그래서 Low로 분류한다.
- → 게시 행에 sha256이 있고 서버가 REPORT_ROW_LINK_ON_PATH를 내면 changed로 세어 409를 낸다(업로드·스캔 폴더 밖 행은 지금처럼 제외).

R20-6 (Low, R19-6 잔여 — 커밋 메시지·주석 부정확). 출력 파일을 쓰는 중에 SIGTERM이나 SIGHUP을 받으면 출력 폴더에 `.<이름>.<pid>.<id>.tmp`가 남는다.
- 재현: os.fsync를 5초 지연하는 sitecustomize 아래에서 fsync 도중 SIGTERM을 보낸다(Popen 직접 실행).
  - `scan --json-out` → `.r.json.7895.6f70af04.tmp`
  - `legal-report --output` → `.l.pdf.*.tmp`
  - `evidence --output` → `.e.json.*.tmp`
  - 반환값은 rc -15다. SIGHUP도 같다(rc 129). SIGINT는 rc 130이고 잔여가 없다.
- output_files.py:84-86 주석과 47d80c9 메시지는 "SIGTERM's SystemExit"도 정리한다고 하지만, 종료 처리기는 os._exit(128+signum) 또는 기본 동작 재발생으로 끝나서 except가 실행되지 않는다.
- 위치: output_files.write_output_bytes, shutdown.py:159.
- → 쓰는 중인 출력 임시 파일 경로를 종료 정리 목록에 등록하고(세션 폴더 정리와 같은 경로), 쓰기가 끝나면 해제한다. SIGTERM 주입 테스트를 추가한다.

R20-7 (Low, R19-4와 같은 부류, QA-IN-1). HOME이나 TMPDIR에서 정해지는 도구 폴더가 검사 대상 폴더 안에 생긴다.
- `cd /tmp/E1; HOME=rel deepfake-lens scan /tmp/E1` → `/tmp/E1/rel/.cache/deepfake-lens/logs/{deepfake-lens.log,native-stderr.log}`가 생긴다(rc 0, 2130892도 같음).
  - R19-2 코드는 numba 캐시에 대해서는 상대 HOME을 거부한다("a relative HOME would put the cache into the working (evidence) folder"). 로그·히트맵 기본 폴더(cli_logging.builtin_log_dir, core.builtin_heatmap_root)에는 이 처리가 없다.
- `TMPDIR=/tmp/E1/tmpd`(증거 폴더 안의 기존 폴더)이면 검사 중 세션 폴더(스테이징 파일, numba 복사본)가 증거 폴더 안에 만들어졌다가 지워지고, 폴더 mtime이 바뀐다. DEEPFAKE_LENS_NATIVE_TMP는 같은 경우를 막는다(변화 0).
- 단일 파일 명령(`classify`·`legal-report`·`evidence` case/a.jpg)은 cwd가 증거 폴더이고 `DEEPFAKE_LENS_LOG_DIR=logs`이면 `case/logs/`를 만든다(rc 0). check_tool_folders는 "명령이 읽는 폴더"만 보고, 입력 파일의 폴더는 보지 않는다.
- → 기본 로그·히트맵 폴더도 상대 HOME이면 쓰지 않는다(또는 절대화한 뒤 P11 검사). 세션 폴더 기준 폴더가 검사 대상 폴더 안이면 다음 후보로 넘긴다. 단일 파일 명령은 입력 파일의 부모 폴더를 검사 대상 폴더로 본다(적어도 LOG_DIR 검사에는).

R20-8 (Cosmetic, R19-8 잔여). display_name이 Cn(미할당·비문자) 코드 포인트를 이스케이프하지 않는다. R19-8이 Co를 넣은 근거("표준 글리프 없음")가 그대로 적용되는데도 그렇다.
- `a\u0378b_….jpg`, `a\uFDD0b…`, `a\uFFFEb…`(실제 문자를 넣은 파일 이름)가 그대로 표시된다. 반면 기본 무시 범위의 미할당(U+2065, U+E0080 등)은 이스케이프된다.
- Python unicodedata와 브라우저 \p{…}의 유니코드 버전이 다르면 두 렌더링의 판정도 갈린다.
- 위치: result_text._invisible_or_space, gui.js invisibleOrSpace.
- → Cn을 이스케이프 대상에 넣고(U+FFFD는 그대로 둬도 됨), parity 테스트에 추가한다.

R20-9 (Cosmetic, R19-9 잔여). 명령 앞에 오는 **알려진** scan 옵션은 아직 잘못 보고된다. 2130892도 같다.
- `--verbose scan /tmp/T1` → "deepfake-lens scan: 오류: 알 수 없는 인수: /tmp/T1"(폴더를 모르는 인수라고 함)
- `--verbose doctor` → "오류: 폴더를 찾을 수 없습니다: doctor"
- `--verbose corpus build …` → scan 오류
- `help scan` → "알 수 없는 인수: scan"
- cli.md는 "`--verbose` (any command)"라고 쓴다.
- 위치: cli.py:497-503(`_misplaced_option`은 모르는 옵션만 거른다).
- → argv에 명령 이름이 있고 그 앞이 옵션뿐이면, 최상위 파서가 "옵션은 명령 뒤에" 오류를 내거나 옵션을 명령 뒤로 옮긴다.

R20-10 (Cosmetic, 문서 — R19-12 재발). HEAD(05091f9)의 HANDOFF.md가 사실과 다르다.
- §1 "기록 재생성(CONFORMANCE·추적표)과 20차 독립 검증이 남아 있다"
- §2 적합성 기록 "bc99d8a에서 생성 … 19차 수정 뒤에는 아직 재생성하지 않았다"
- §2 추적성 "R19-1~13은 아직 ids[]에 없음"
- §2 다음 할 일 "ids[]에 추가하고 기록 재생성"
- 실제로는 05091f9가 9142684에서 CONFORMANCE를 재생성했고, ids[]에 R19-1~13이 있다(--check rc 0). 커밋 수도 HEAD 기준 278이다.
- → 기록 커밋 뒤에 HANDOFF를 갱신하는 단계를 루프에 넣는다. 또는 인계서에 "이 커밋 다음의 기록 커밋에서 재생성됨"처럼 기록 커밋으로 무효가 되지 않는 표현을 쓴다.

## 잔여 1단계 리스크
- 오디오 작업자 비용: 작업자당 RSS 약 300 MB이고, workers 8이면 2.1 GB다. 스캔 CPU는 workers 4에서 +15~30%다(호스트 부하에 따라 다름; QA-SYS-8은 조용한 장비에서 다시 측정해야 함).
- published numba 캐시가 손상되면(.nbc/.nbi 임의 바이트로 실험) 모든 스캔의 audio_features가 "UnpicklingError"로 failed가 되고, `~/.cache/deepfake-lens/numba`를 지울 때까지 계속된다. 실패로 닫히기는 하지만 자가 복구는 없다.
- A/V 동기 엔벨로프(multimodal)와 `compare`는 여전히 같은 프로세스에서 librosa를 쓴다(문서화됨, 크래시 격리 없음).
- SIGKILL 뒤 세션 폴더(numba 복사본 포함 수 MB)는 오래된 세션 정리 기준 시간이 지날 때까지 남는다.
- R19-1 키는 목록 밖 모듈(soxr, audioread, llvmlite 등)과 같은 버전 안의 하위 모듈 수정을 보지 못한다(문서에 일부 명시).
- 홈 폴더 자체를 검사하면 ~/.cache/deepfake-lens/{logs,numba}가 그 안에 쓰인다(로그는 문서화됨, numba는 문서에 없음).
- 포렌식 PDF·증거설명서 PDF는 같은 행이라도 요청마다 바이트가 다르다(생성 시각). JSON·HTML은 같다.
- R19-3: 작업 기록은 15분 뒤 사라진다. 그 뒤 내보낸 취소 보고서는 "unverified 0"이 된다(문서화됨).
- 이전 라운드 잔여(감독 프로세스 비용, PDEATHSIG 스레드 단위, polyglot, gVisor/WSL1 자손 수거, 동형 문자, cp437 zip, os.replace 권한 비보존, v1 리뷰 표시)는 변화가 없다.

게이트 0 판정: 아니오 — 수정 목록 R20-1 ~ R20-10. 차단은 R20-1(R19-13 회귀: 특수 파일·읽기 불가 폴더가 있는 폴더는 서명 보고서 불가)과 R20-2(R19-2 성능 회귀: batch·업로드·eval 7~9배)다. R20-3은 차단은 아니지만 증거가 다른 파일로 옮겨 붙는 결함이므로 우선 수정 대상이다. 나머지도 HANDOFF §6 "면제 없음"에 따라 수정 대상이다.

---
## 부록: 실행한 명령과 결과 요약

**전체 게이트(05091f9)**: `gate.sh full`(컨테이너 r20-full)
- 단위 테스트: main 1684 OK (skip 96), api 1684 OK (skip 45).
- ruff 통과, mypy 129 files 통과, coverage 80%.
- cli_smoke OK(45/26), 측정 게이트 10/0, sync_model_docs rc 0, verify_contracts OK(3+2), stress_shutdown 30/30. GATE rc=0.

**기록 확인**: `qa_phase0 --verify-record` rc 0("91426844c63f에서 생성"). `build_traceability_commits --check` rc 0(268커밋, 표 이후 1커밋). ids[]의 R19-1~13 gaps는 각 커밋 제목의 Gaps와 일치한다(R19-9/11/12는 신규 → []).

**얕은 클론**:
- `--depth 1`: verify-record rc 1(얕은 복제 안내), trace rc 1.
- `--depth 2`: verify-record rc 0, trace rc 1(base 없음).
- 단위 테스트(depth 1 main/api, depth 2 main): 문서화된 2개 테스트(QaSys10 deletion reasons 9 subtests, history document 5 subtests)만 실패.

**반복 실행(플레이크)**: 실패 0.
- test_audio_worker 5회(R19-2 동시성 테스트 포함).
- test_shutdown·interrupt·native_path·output_files·scan_cancel·servers·tool_folders·model_assets(272 tests) 3회 × 2 venv.
- stress_shutdown TERM/HUP/KILL 각 30회, wrapped-ffmpeg 20회, api venv TERM 30회.
- 전체 스위트는 게이트 1회 + 얕은 클론 3회 + 커밋별(8ffd1cf) 1회(× 2 venv).

**커밋별 게이트(두 venv 단위 테스트, ruff, mypy)**: b829570, de59997, 15185a0, b7b27c5, 767f7d8, 2525982, 47d80c9, bc3eea1, f6726cd, d70f460, c3f9594, 8ffd1cf 12개 코드 커밋이 전부 OK다.
- main skip 96, api skip 43 → 45(de59997부터, test_audio_worker).
- 테스트 수 1647 → 1684.
- ruff 통과, mypy 128 → 129 files 통과.
- 문서만 바꾼 커밋(a5aca29, 66293b1, 9142684)과 기록 커밋 05091f9는 코드가 직전 커밋과 같다. 05091f9는 전체 게이트로 확인했다.

**수정 없이 새 테스트 실행(부모 커밋 소스 + 해당 커밋의 테스트 파일, api venv)**: 테스트를 바꾼 12개 커밋 모두 실패했다.
- b829570 3F/3E, de59997 import 오류, 15185a0 55F/7E, b7b27c5 9F/4E, 767f7d8 3F/4E, 2525982 2E, 47d80c9 7F/1E, bc3eea1 1F/1E, f6726cd import 오류, d70f460 2F, c3f9594 1F, 8ffd1cf 1F.
- 테스트 약화는 없다. 바뀐 기존 단언은 다음과 같다. 모두 결함을 고정하던 것이거나 메타 테스트 허용 목록이며, ID 주석이 있다. 삭제 0건, TEST-DELETIONS.md 변경 없음.
  - test_native_path NATIVE_CALL_EXEMPT, test_shutdown run_child 사용자 목록: R19-1(커밋 메시지 Tests 절에는 언급 없음)
  - test_servers 3건, test_forensic_pdf 1건: R19-13
  - test_scan_cancel: R19-3
  - test_model_assets: R19-4
  - test_evidence_statement 2건: R19-11
  - test_display_names 알파벳 확장: R19-8
- 커밋 메시지는 47d80c9(SIGTERM 정리 주장 → R20-6)를 빼면 동작과 맞는다.

**R19 종료 탐침(/private/tmp/claude-501/r20/out/p/)**:
- r1.sh(R19-1, 10개 시나리오)
- r2.sh/r2b.sh/r2c.sh(R19-2: 동시 4×3, 크래시 4종, 부모 신호 4종)
- r3job.py(R19-3, 4종)
- r45.sh/r4b.sh/r4web.py(R19-4/5, 40여 종)
- r6.sh/r6b.sh/r6c.py(R19-6, 신호 × 명령 18종)
- r8.py/r8b.py(R19-8, 41종)
- r9.sh/r9b.sh(R19-9, 26종)
- r13.py/r13b.py/r13h.py/fifo.py/unread.py(R19-13, 두 서버 × 4형식 × 25여 종)

**회귀·결정성·성능**:
- hostile.py + hscan.sh: /tmp/H × {2130892, HEAD} × {main, api, deep+allow-symlinks}.
- determ.sh: 동일.
- legs.py: CLI=web=api 106행, main venv에서 CLI=web.
- snap.py: QA-IN-1 변화 0.
- sig.py(12) + sig2.py(14): 중복 키(문자 \u 이스케이프), 1.0/큰 정수/외로운 서로게이트/Infinity/NFD/대문자 서명은 변조됨이다. 키 순서 변경·CRLF는 검증됨, UTF-16·잘못된 UTF-8·빈 파일·배열·깊은 중첩은 rc 4다.
- doctor(3 환경 변수 조합, api json).
- calls.py, perf3.py, perf4.sh(batch), perf5.py(업로드), wcost.py(작업자 기동 비용).

**새 영역 탐침(40건 이상)**:
- 작업자 프로토콜 비동기화(R20-3).
- 작업자 RSS·workers 8.
- 운영자 NUMBA_CACHE_DIR이 증거 폴더 안을 가리키는 경우.
- HOME 미설정·상대·읽기 전용(nobody).
- published 캐시 .nbc/.nbi 손상 4회.
- staging 잔여.
- librosa 소스 mtime 변경(재설치 모사, 비용 변화 없음).
- 웹 analyze-file 작업자 잔여.
- compare가 site-packages에 쓰지 않음.
- deep-signals av, 비ASCII 오디오 이름.
- TMPDIR·NATIVE_TMP가 증거 폴더 안을 가리키는 경우.
- LOG_DIR·HEATMAP_DIR가 파일인 경우.
- 단일 파일 명령의 LOG_DIR.
- 웹 서버의 LOG_DIR/HEATMAP_DIR.
- R19-13:
  - 자기 참조 중복, 게시하지 않은 대상의 중복, 하위 폴더→링크, 파일→링크/FIFO, chmod, mtime만 변경.
  - 위조 필드 8종(sha256 null/대문자, score, verdict, status, 중복 대상 바꿈, 대상 밖 경로, 행 2회 게시).
  - hostile 106행 보고서(거짓 409 발견), FIFO/소켓/읽기 불가 폴더(2130892 대비).
- R19-3 실제 작업 취소 4종.
- SIGTERM/SIGHUP/SIGINT 출력 쓰기 중단(명령 4종).
- 서명 파서 차이 14종.
- 명령 앞 옵션 7종.
- Cn/비문자/기본 무시 미할당 코드 포인트 표시.

**정리**: 직접 띄운 컨테이너(r20-full, r20-probe, r20-pc)만 썼고, 끝난 뒤 모두 종료·삭제했다. 다른 검증자의 gui-gate, guirev4-gate는 건드리지 않았다. 클론의 작업 트리는 변경하지 않았다(탐침 사본·픽스처는 컨테이너 안 /work, /base, /tmp에서만 만들었고 컨테이너와 함께 삭제됨). 탐침 스크립트는 /private/tmp/claude-501/r20/out/p/, 로그는 /private/tmp/claude-501/r20/out/*.log에 있다.
