# 독립 검증 라운드 19 결과 (HEAD 2130892)

R18-1~R18-19(a~f 포함)는 새로 만든 탐침으로 각 항목의 지적 내용 자체는 닫혔음을 확인했다. 다만 R18-1 수정이 새 회귀(R19-1)를 드러냈고, R18-15·R18-17·R18-19(f)는 같은 부류의 잔여가 남아 있다(R19-6·R19-10·R19-9).
- R18-1: /work, 사본 /w2, /venv, /venv-api 네 조합의 캐시 키 토큰 sha256이 모두 `008915a0…`로 같고 경로 문자열이 0개다. 그러나 경로가 빠지면서 다른 venv의 캐시 행이 그대로 재생된다(R19-1).
- R18-2: 7개 환경 변수(HAAR, FACE_LANDMARKER, LOG_DIR, HEATMAP_DIR, CONFIG, MODELS_DIR, FEEDBACK)를 `~nouser/x`로 줘도 `scan --cache`·doctor가 rc 0이다. 다만 리터럴 상대 경로로 바뀐 로그·히트맵 폴더가 cwd(증거 폴더일 수 있음)에 생기고(R19-4), CLI 인수·웹 파라미터의 `~nouser`는 그대로 남았다(R19-5).
- R18-3: 312행 폴더(zip 구성 파일 10개, 중복 포함)를 5·299번째에서 취소하고 workers 4, dedupe로도 돌렸다. 매번 `행 + files_cancelled = 312`가 맞고, 작업 상태는 cancelled다. 클라이언트가 보낸 `files_cancelled`는 상한 없이 서명 보고서에 들어간다(R19-3에 포함).
- R18-4: 6.7 MB급 PNG의 IDAT 뒤 tEXt, 270,000개 청크 PNG, 길이 0xFFFFFFF0 청크, IDAT 뒤 WebP XMP는 모두 기대대로 동작한다. APNG는 잔여가 있다(R19-7).
- R18-5: 이스케이프된 중복 키(`verdict\u005fcode`, 밑줄을 \u 이스케이프로 쓴 같은 키), `signature` 중복, 중첩 객체 중복은 모두 duplicate-key(rc 1)다. 재들여쓰기, ASCII 이스케이프 값, `-0`은 검증됨이고, `1E0`·NaN 필드·값 변경은 변조됨, BOM·뒤 데이터는 rc 4다.
- R18-6: 16320×12240 PNG(tEXt A1111)와 JPEG(XMP CreatorTool)는 metadata ran이고 결론은 조작·생성 근거 있음이다. image_class 사유는 한국어("픽셀 수 상한 초과 — 디코드 생략(199756800화소, …)").
- R18-7: api venv에서 zero/nomoov/garbage/av_trunc.mp4의 rppg는 failed "VideoDecodeError: 영상을 디코딩할 수 없습니다(열기 실패)"다.
- R18-8: gui.js의 키가 `v2|root:<scan_root>|sha256:<hash>`이고, 새 테스트는 부모 커밋에서 실패한다.
- R18-9: 새 테스트 3건이 부모 커밋에서 실패하고 HEAD에서 통과한다. 컨테이너에서 core_pattern을 바꿀 수 없어 실측 재현은 하지 않았다.
- R18-10: `/proc/self/environ`, `/proc/self/mounts`, `/sys/…/enabled` 링크는 특수 파일 시스템으로 건너뛴다. `/dev/stdin`은 일반 파일 아님, 깨진 링크는 링크 사유로 처리된다. `/proc/self/root/etc/hostname`은 실제 경로로 해석되어 분석된다(정상). 단일 파일 명령(classify, legal-report)도 sha256 null, skipped다.
- R18-11: 지적된 6종은 이스케이프된다. U+2800은 남았다(R19-8).
- R18-12: 새 테스트로만 확인했다(실측 SIGINT 반복은 하지 않음). __init__의 첫 문장이 가드다.
- R18-13: HAAR 재정의가 없으면 [재정의없음]이고, FACE_LANDMARKER가 폴더를 가리키면 mediapipe 행이 WARN이며, /venv opencv 5 행은 WARN이다.
- R18-14: ids[]의 R18-1~19는 커밋 Gaps와 일치하고 `--check` rc 0이다.
- R18-15: scan/legal-report/doctor/classify/vendor-weights의 `/dev/full`, `doctor > /dev/full`, `| head`, `>&-`는 기대대로다. 다른 명령에는 잔여가 있다(R19-6).
- R18-16: 증거설명서 사건명이 "(사건명 입력)"이다. 죄명 후보 자동 기재는 남았다(R19-11).
- R18-17: model_analysis는 "없음 — 미실행(사유)"로 표시된다. 계층 진단은 여전히 "원점수 0"이다(R19-10).
- R18-18: 새 테스트가 부모 커밋에서 5건 실패한다. diagnostic 키는 {signals, limitations}다.
- R18-19 (a): ffmpeg exit 1, SIGKILL, 미설치 모두 rc 1과 한국어 사유를 낸다.
- R18-19 (b): DEEPFAKE_LENS_MODELS_DIR가 없는 폴더면 "모델 폴더가 없습니다 (DEEPFAKE_LENS_MODELS_DIR)", 파일이면 "아니라 파일입니다"다.
- R18-19 (c): BOM이 있는 config는 허용되고, config 경로가 폴더면 rc 2다.
- R18-19 (d): cp949 zip의 "똠방각하"·"증거"가 정상 표시된다.
- R18-19 (e): 깊이 1 클론은 "얕은 복제 … git fetch --deepen=1"(rc 1)을, 깊이 2 클론은 rc 0을 낸다.
- R18-19 (f): `api-serve --folder`가 api-serve 사용법으로 보고된다. 중첩 하위 명령은 잔여가 있다(R19-9).

회귀 탐색 결과는 다음과 같다.
- 성능: 2000파일 cProfile 호출 수가 51,873,742 → 53,308,281(+2.8%)이다.
  - 증가분은 거의 전부 R18-11 display_name(genexpr 838,802회 등)이고, 자체 시간 합은 약 0.2 s(전체 약 20 s의 1% 미만)다.
  - 교대 5회 CPU 중앙값은 18.77 s → 22.12 s(+17.8%)지만, 같은 시간에 다른 컨테이너 2개가 돌아 값이 흔들렸다(HEAD 최솟값 17.2 s < 기준 최솟값 18.4 s).
  - 처리량 저하는 25% 기준 미만으로 판단한다.
- 결정성: 3회 실행, 캐시 cold/warm, 폴더 이름 변경, 워커 4, 4개 동시 스캔 모두 바이트 단위로 같다(`summary.cached`만 다름).
- 서명: 위 R18-5 항목대로 정상이다.
- 악성 폴더 /tmp/H(95 항목, 106행)를 ec0d057과 HEAD에서 main·api·deep-signals+allow-symlinks로 비교했다. 차이는 의도된 R18-3(summary 필드, 사유 목록), R18-6(huge_dims·bomb_partial 메타데이터 ran), R18-11(NFD 이름 표시)뿐이다.
- QA-OUT-4 세 경로(CLI/web/api-serve) 106행이 동일하다. QA-IN-1 스냅샷(deep·heatmap·cache·allow-symlinks·증거설명서)도 변화 0이다.
- 새 회귀 1건: R19-1(다른 venv의 캐시 재생, R18-1에서 비롯).
- 기존 결함 1건(ec0d057에도 있음): R19-2(numba 캐시 경합 뒤 프로세스 SIGSEGV).

R19-1 (Med, 차단 — 회귀, G11/G1). 스캔 캐시 키에 실행 환경(선택 의존성 유무·버전)이 없다. R18-1이 venv 경로를 키에서 빼자 다른 환경의 결과 행이 그대로 재생된다.
- 재현(HEAD, 같은 캐시 파일):
  1. `/venv/bin/python -m deepfake_lens scan /tmp/H --recursive --cache /tmp/xc.json`
  2. 같은 명령을 `/venv-api/bin/python`(c2pa·librosa 없음)으로 실행 → `summary.cached 77`.
  3. api venv에서 캐시 없이 실행한 결과와 비교한다.
- 결과: coverage 59개 상태와 61개 사유가 다르다. 예를 들어 c2pa가 설치되지 않았는데 c2pa가 `ran`이고, `checks_failed`는 18 대 19다. verdict 문구 9건이 다르다(corrupt.pdf "검사 실패: 문서 텍스트 추출" 누락, fake.mp3 "검사 실패(오디오 특징 추출)" 등).
- ec0d057에서는 같은 절차로 `cached 0`이다(키에 cv2 데이터 경로·venv 경로가 들어 있었음).
- 같은 venv 안에서 의존성을 설치하거나 제거한 경우는 ec0d057과 HEAD 모두 재생된다. 재현: PYTHONPATH에 `c2pa.py`(ImportError)를 두고 캐시를 만든 뒤, 차단을 풀고 재검사하면 c2pa "skipped: 의존성 부재"가 그대로 재생된다(`cached 2`). 캐시 없이 돌리면 `ran`이다.
- 위치: scan_cache.py:720-750(키 구성 — 옵션·sha256·핀·자산 토큰·prov뿐, 의존성 정보 없음), model_assets.asset_file_tokens.
- → 키에 검사별 의존성 지문을 넣는다. 예: c2pa, librosa, soundfile, cv2(+CascadeClassifier 여부), mediapipe, pymupdf, torch/transformers의 import 가능 여부와 버전, ffmpeg 유무·버전. venv를 옮긴 테스트와 의존성을 차단한 테스트를 추가한다.

R19-2 (Med, 차단 — 기존 결함, ec0d057에도 있음, QA-IN-5 "프로세스 생존"·fail-closed). 메인 venv(librosa+numba)에서 오디오 트랙이 있는 영상을 동시에 여러 개 검사하면 librosa의 numba 디스크 캐시(site-packages/librosa/__pycache__/*.nbi·nbc)가 깨진다. 그 뒤로는 그 영상을 검사하는 모든 스캔이 SIGSEGV로 죽는다(rc 139, stdout 0바이트, 보고서 없음). 캐시 파일을 지울 때까지 계속된다.
- 재현(게이트 이미지):
  1. `find /venv -name '*.nb[ic]' -delete`
  2. `scan /tmp/A2`(av.mp4 1개)를 4개 동시에 실행한다.
  3. 이어서 단독 `scan /tmp/A2`를 실행한다. 3회 중 2회 rc 139였다.
- 이번 라운드 처음에도 shallow 클론 단위 테스트와 동시에 돌던 악성 폴더 스캔이 이렇게 죽었다. ec0d057(base_d)과 HEAD(work_m, work_d)가 모두 139였다. 캐시를 지우면 다시 rc 0이다.
- faulthandler 위치: librosa/beat.py:545 `__beat_tracker` → numba gufunc. 호출 경로는 audio.py:350 `_extract_features` ← video_analysis.py:258 `audio_track_check`.
- 결정성 탐침의 "4개 동시 스캔"과 GUI·CLI 병행 사용은 현장에서도 흔한 조합이다.
- → librosa 특징 추출을 run_child(감독 자식 프로세스)로 옮긴다. 그러면 네이티브 충돌이 해당 검사의 failed가 된다. 아니면 최소한 NUMBA_CACHE_DIR를 프로세스별·버전별 도구 소유 폴더(세션 폴더)로 지정해 공유 캐시 경합을 없앤다. 동시 실행 회귀 테스트를 추가한다.

R19-3 (Low, 조정자 후보 재현됨 — G7/QA-OUT-4 취지). GUI가 지금 화면의 결과를 다른 검사 옵션으로 재분석해 서명 보고서로 내보낼 수 있다.
- gui.js:603: `lastScanOptions`는 폴더 검사를 제출할 때 바로 덮어쓴다. 이전 결과(`results`, `lastScanRoot`, `lastProvenance`)는 검사가 끝나야 바뀐다(gui.js:631, 694).
- 새 검사가 오류로 끝나면(gui.js:612·624·628의 `return`) 옛 행과 새 옵션이 그대로 남는다. 업로드 검사는 `lastScanOptions`를 바꾸지 않는다.
- 내보내기 버튼(exp-report 등)은 SCAN_INPUTS(gui.js:497-499)에 없어 검사 중에도 눌린다. 클릭 핸들러(gui.js:1408)도 scanBusy를 보지 않는다.
- `reportPayload`(gui.js:715-721)는 옛 행 + 새 옵션을 POST한다. 서버는 그 옵션으로 행을 재분석한다(webapp_api.py:1743-1755). 보고서에는 사용한 옵션이 기록되지 않는다.
- 재현(서버 경로): /tmp/G를 `deep_signals=false&pixel=off`로 검사한 행을 `{"deep_signals":"true","pixel":"deep","heatmaps":"true"}`로 /api/report(json)에 보냈다. 화면에서 skipped였던 normal.jpg의 pixel·inpaint와 av.mp4의 avatar가 서명 보고서에서는 `ran`이다(p/guiopts.py).
- 같은 요청 경로에서 `summary.files_cancelled: 10**18`을 보내면 그대로 서명 본문에 들어간다(`_with_posted_cancel`, webapp_api.py:322-329).
- → `lastScanOptions`는 결과를 반영할 때(`lastScanRoot`와 함께) 설정한다. 검사 중에는 내보내기를 비활성화한다. 보고서 본문에 재분석 옵션을 기록한다. 게시된 `files_cancelled`는 상한(예: 서버가 아는 폴더 파일 수)을 두거나 "클라이언트 제공"으로 표시한다.

R19-4 (Low, QA-IN-1 "폴더에 새 파일 0개"). 환경 변수로 지정한 로그·히트맵 폴더가 검사 대상 폴더 안에 만들어진다.
- `cd /tmp/T1; DEEPFAKE_LENS_LOG_DIR='~nouser/l' deepfake-lens scan .` → `/tmp/T1/~nouser/l/deepfake-lens.log`가 생긴다. R18-2의 리터럴 대체가 cwd 기준 상대 경로가 된 결과다.
- `DEEPFAKE_LENS_LOG_DIR=logs` → `/tmp/T1/logs/…`.
- `DEEPFAKE_LENS_HEATMAP_DIR=/tmp/E/hm scan /tmp/E --pixel deep --heatmaps` → `/tmp/E/hm/<hash>/normal.jpg.heatmap.png`.
- 세 경우 모두 rc 0이고 경고가 없다. CLI 출력 인수(`--json-out`, `--frame-root` 등)는 P11 검사로 거부되지만 이 두 변수는 검사 밖이다. `core.default_heatmap_root` 문서에는 "Never inside the evidence"라고 적혀 있다.
- 위치: cli_logging.py:30-34, core.py:2263-2267, cli_inputs.check_output_targets.
- → 두 폴더를 절대 경로로 해석해 P11 검사에 넣는다(안이면 rc 2 또는 기본 폴더로 대체하고 알림). 알 수 없는 `~user`는 리터럴 상대 경로 대신 "경로를 해석할 수 없음" 오류로 처리한다.

R19-5 (Low, "영어 traceback 금지"·QA-SYS-7). R18-2의 `~nouser` 처리가 환경 변수에만 적용되어 인수와 요청 파라미터는 그대로다.
- `scan /tmp/T1 --cache '~nouser/c.json'`, `--hash-db '~nouser/h.json'` → 영어 `RuntimeError: Could not determine home directory.` traceback, rc 1(ec0d057도 같음).
- cwd에 리터럴 `~nouser` 폴더가 있으면 `scan '~nouser/x'`도 같은 traceback이다(check_command_inputs가 try 밖, cli.py:516).
- 웹: `/api/scan?folder=~nouser/x`, `/api/analyze-file?file=~nouser/a.jpg`, `/api/preview?path=~nouser/a.jpg` → HTTP 500 "서버 내부 오류". 등록되지 않은 경로이므로 403이어야 한다.
- 위치: cli_inputs.py:552-554·589, webapp_api.py:375·618·667 등의 `Path(...).expanduser()`.
- → 모든 사용자 입력 경로에 `native_path.expand_user`를 쓰거나 RuntimeError를 입력 오류(rc 2, 웹 400/403)로 바꾼다. 테스트를 추가한다.

R19-6 (Low, R18-15 미완 — 문서 과장). cli.md는 "Report and JSON output files (`--json-out`, `--html-out`, `--csv-out`, `--output`, PDFs, evidence statements, benchmark/eval reports)"가 통째로 쓰이고 실패하면 "출력 파일을 쓸 수 없습니다"라고 하지만 실제는 다르다.
- 여전히 "예기치 않은 오류(OSError)", rc 1인 명령: `benchmark --md-out /dev/full`, `corpus build --out /dev/full`, `dataset --manifest-out /dev/full`, `batch <폴더> --output /dev/full`.
- 원자적 쓰기가 아닌 곳: benchmark.py:100, corpus_manifest.py:232, datasets.py:93/137/178/213, batch.py:182, evaluate.write_cases_jsonl(evaluate.py:297-302 — eval `--false-positive-out`/`--false-negative-out`), calibration/fusion/training/perf/release/security의 `--out`.
- output_files.write_output_bytes(:84-92)는 `except OSError`만 임시 파일을 지운다. 쓰는 중에 Ctrl-C(KeyboardInterrupt)나 SIGTERM의 SystemExit가 오면 `.r.json.<pid>.<id>.tmp`가 출력 폴더에 남는다(fsync에서 KeyboardInterrupt를 주입해 재현).
- → 남은 쓰기 지점도 write_output_text를 쓰게 하고, 임시 파일 정리는 `BaseException`에 대해 한다. 문서는 실제 범위에 맞춘다.

R19-7 (Cosmetic, R18-4 잔여). APNG의 프레임 데이터(fdAT)를 메타데이터 청크로 취급한다.
- 4프레임 1400×1400 APNG(23.5 MB, 메타데이터 없음)의 coverage: metadata ran "메타데이터 청크를 4 MB까지만 읽음(--metadata-bytes) — 이후 fdAT 청크(65540바이트)를 읽지 않음".
- 빠진 메타데이터가 없는데 부분 판독으로 표시하고, 4 MB의 이미지 데이터를 메타데이터로 읽어 파싱한다. 남은 예산보다 큰 tEXt가 프레임 뒤에 있으면 놓친다.
- 위치: image_metadata._walk_png `elif kind != b"IDAT"`.
- → fdAT를 IDAT처럼 seek으로 건너뛴다.

R19-8 (Cosmetic, R18-11 잔여). U+2800(BRAILLE PATTERN BLANK, 범주 So)이 이스케이프되지 않는다.
- `a⠀b.jpg`가 `a b.jpg`와 같아 보인다. 사설 영역 문자(U+F0000 등, Co)도 그대로 표시된다.
- 위치: result_text._invisible_or_space(gui.js displayName도 같음).
- → U+2800과 Co 범주를 이스케이프 대상에 넣고 parity 테스트에 추가한다.

R19-9 (Cosmetic, R18-19(f) 잔여).
- 중첩 하위 명령의 잘못된 인수(`corpus build /tmp/ds --out x --bogus`)는 `corpus` 사용법("사용법: deepfake-lens corpus … {build,split,verify}")으로 보고된다.
- 하위 명령 앞의 최상위 잘못된 옵션(`--bogus scan X`)은 "deepfake-lens scan: 오류"로 scan 탓이 된다.
- 위치: cli.py:487-491.
- → parse_known_args가 남긴 인수를 마지막으로 선택된 (하위의 하위) 파서가 보고하게 하고, 명령 앞 인수는 최상위 파서가 보고하게 한다.

R19-10 (Cosmetic, R18-17과 같은 부류). gui.js:1581, 1589의 계층 진단 제목이 `a.raw_score`/`fr.raw_score`가 null(점수 없음)일 때 "원점수 0"이라고 쓴다. 측정되지 않은 0이 측정값처럼 보인다. 같은 파일의 워터마크 행은 "없음"이다.
- → null이면 "없음"으로 표시한다.

R19-11 (Cosmetic, R18-16과 같은 원칙). 증거설명서가 내용 근거 없이 죄명·법조 후보를 자동으로 적는다(evidence_statement.py:290-310).
- 조작 결론인 모든 호증에 "정보통신망법 제70조(명예훼손) — 검토 참고"가 붙는다. A1111 메타데이터가 있는 고양이 그림 a1111.png에도 들어간다.
- 결론이 조작인 영상이거나 근거 제목에 "얼굴"이 들어가면 "성폭력처벌법 제14조의2(허위영상물 등의 반포등)"와 "형법 제347조(사기)"가 붙는다.
- 파일 종류와 문자열만으로 정하는 것이며, R18-16에서 사건명 기본값을 뺀 것과 같은 이유로 근거 없는 기재다.
- → 기본은 비워 두고, 검사관이 선택해서 넣게 한다.

R19-12 (Cosmetic, 문서). docs/phase0/HANDOFF.md가 현재 상태와 다르다. §1·§2는 "17차 검증에서 나온 11건이 아직 수정되지 않은 상태", 테스트 1,555개, "다음 할 일: R17 수정"이라고 쓰고, §9는 verify_round17까지를 나열한다. 실제로는 R17·R18이 수정되었고 테스트는 1,643개, verify_round18.md가 있다.
- → 라운드 끝마다 §1·§2·§9를 갱신한다.

## 잔여 1단계 리스크
- 성능은 이번에도 호스트 부하(다른 검증 컨테이너 동시 실행) 때문에 실행 시간으로 판정할 수 없었다. 호출 수 +2.8%의 원인은 display_name이다. QA-SYS-8은 조용한 장비에서 측정해야 한다.
- R18-8: 이전(v1) 리뷰 표시는 이전되지 않고 저장소에 남아 보이지 않는다. 키 범위가 스캔 루트 경로라서 증거 폴더 이름을 바꾸면 표시가 사라진다(설계상 선택이지만 사용자 안내가 필요).
- 동형 문자(키릴 а 등)와 NFKC 호환 문자는 display_name이 구분하지 않는다(시각적 구분은 1단계 보고서 설계 과제).
- cp949를 우선 해독하므로, UTF-8 플래그 없이 cp437(서유럽 Windows) 이름을 가진 zip은 다르게 표시될 수 있다.
- 출력 파일을 os.replace로 교체하면 기존 파일의 권한·소유자·하드 링크가 유지되지 않는다.
- 이전 라운드 잔여(감독 프로세스 비용, PDEATHSIG 스레드 단위, polyglot 파일, gVisor/WSL1 자손 수거)는 변화가 없다.

게이트 0 판정: 아니오 — 수정 목록 R19-1 ~ R19-12. 차단은 R19-1(회귀)과 R19-2(프로세스 사망)이다. 나머지도 HANDOFF §6 "면제 없음" 원칙에 따라 수정 대상이다.

---
## 부록: 실행한 명령과 결과 요약

**전체 게이트(2130892)**: `gate.sh full`
- 단위 테스트: main 1643 OK (skip 96), api 1643 OK (skip 43).
- ruff 통과, mypy 128 files 통과, coverage 80%.
- cli_smoke OK(45/26), 측정 게이트 10/0, sync_model_docs 통과, verify_contracts OK(3+2), stress_shutdown 30/30. GATE rc=0.

**기록 확인**: `qa_phase0 --verify-record` rc 0("bc99d8a74ee8에서 생성"). `build_traceability_commits --check` rc 0(252커밋, 표 이후 1커밋).

**얕은 클론**:
- `--depth 1`: verify-record rc 1("얕은 복제 … git fetch --deepen=1"), traceability rc 1("얕은 클론 — fetch-depth: 0 필요").
- `--depth 2`: verify-record rc 0, traceability rc 1(base 없음).
- 단위 테스트(depth 1의 main/api, depth 2의 main): 문서화된 2개 테스트(QaSys10 deletion reasons, history document, 14 subtests)만 실패.

**반복 실행(플레이크)**: 실패 0.
- test_shutdown + test_interrupt + test_native_path + test_output_files + test_scan_cancel(105 tests) 5회 × 2 venv.
- stress_shutdown TERM/HUP/KILL 각 30회, wrapped-ffmpeg 20회, api venv TERM 30회.
- 전체 스위트 추가 2회 × 2 venv(게이트 포함 총 3회 × 2).

**커밋별 게이트(두 venv 단위 테스트, ruff, mypy)**: 16d1df8부터 bc99d8a까지 25개 커밋 전부 OK(main skip 94→96, api skip 43, 테스트 1578→1643, ruff·mypy 통과). 2130892는 기록 파일만 바꾼 커밋이고 전체 게이트로 확인했다. 컨테이너 2개(r19-pc 순방향, r19-pc2 역방향)로 나눠 돌렸다.

**수정 없이 새 테스트 실행(부모 커밋 소스 + 해당 커밋의 테스트 파일)**: 테스트를 바꾼 24개 커밋 모두 실패했다.
- b42f5c1 3F, 41c3aef 1F/7E, a3ac2aa import 오류, 2b2c112 6F/3E, 1c124aa 1F, 46e06da 1F/1E, e498775 3F/4E, 259f7e3 2E, 80875cc 3F, 3d46a51 2F/1E, 15d789a 2F, fe963eb 3F, 12348ef 1F/2E, 1b5dbdd 1F/1E, 9067673 1F/1E, 91ee0d1 1E, 9ecd4ed 2E, 48a07ce 5F, a155537 3F, 804ce5c 1F, afd7036 1F, 3df21ce 1F, c9e1f81 1F, bc99d8a 2F.
- 테스트 약화는 없다. 바뀐 기존 단언 4건(test_model_assets의 키 4개 기대 R18-1, test_native_path 허용 목록 R18-15, test_phase6_new agent_type R18-18, test_telemetry 첫 import R18-12)은 모두 결함을 고정하던 것이며 ID 주석이 있다. 삭제 0건, TEST-DELETIONS.md 변경 없음.
- 커밋 메시지는 R18-15(문서 범위 과장 → R19-6)를 빼면 동작과 맞는다. 추적표 매핑은 R18-1~19 전부 커밋 Gaps와 일치한다.

**R18 종료 탐침(p/ 아래)**:
- tok.py: 4개 설치·venv 조합.
- tilde.sh: 변수 7종 + 인수 8종.
- cancel2.py: 5개 시나리오.
- apng.py, misc2.py: PNG·WebP 청크 5종.
- big2.py: 2억 화소 PNG/JPEG.
- sig.py: 서명 12종.
- web1.py, web2.py: ~ 경로, 게시된 취소 주장 4종.
- 그 밖에 /proc·/sys 링크 6종, display_name 17종, doctor 6종, video --extract 3종, vendor-weights 4종, config 3종, cp949 zip, 중첩 사용법 4종.

**회귀·결정성·성능**:
- hostile.py(/tmp/H 95항목·106행) × {ec0d057, HEAD} × {main, api, deep+allow-symlinks}: 의도된 차이만 있다.
- determ.sh: 동일.
- legs.py: CLI=web=api 106행.
- snap.py: QA-IN-1 변화 0.
- calls.py/pdiff.py, perf2.py: 위 수치.

**새 영역 탐침(40건 이상)**:
- 캐시: venv 간 캐시 재생, 의존성 차단 후 재생, thresholds 내용 변경 시 재사용(행에 임계값 정보가 없어 영향 없음).
- numba 동시성: 4×3회 + wav 2회.
- GUI·서버 옵션 재분석, 게시된 files_cancelled 4종.
- 로그·히트맵 폴더 위치 3종, LOG_DIR가 파일인 경우.
- ~nouser: CLI·웹 11종.
- /dev/full 14종, 쓰기 중 KeyboardInterrupt, 쓰기 불가 폴더의 기존 파일.
- 증거 폴더 안 심볼릭 링크 json-out, 증거 파일을 json-out으로 지정, frame-root를 증거 폴더 안으로 지정.
- `| head`와 --json-out 병용, 닫힌 stdout에서 --help·web.
- APNG 2종, 청크 범람, 거대 길이 청크, 잘린 WebP XMP.
- 2억 화소 + 생성 메타데이터 2종.
- U+2800·PUA·동형 문자.
- 중첩·최상위 잘못된 인수.
- 증거설명서 법조 자동 기재.
- GUI 계층 진단 원점수 표시.
- HANDOFF 상태 문서.

**정리**: 직접 띄운 컨테이너(r19-full, r19-probe, r19-pc, r19-pc2)는 모두 종료·삭제했다. 다른 검증자의 guirev2-gate는 건드리지 않았다. 클론의 작업 트리는 변경하지 않았다(탐침 사본은 컨테이너 /work, /base에서만 만들었고 /tmp/F2000·/tmp/H 등은 컨테이너와 함께 삭제됨). 탐침 스크립트는 /private/tmp/claude-501/r19/out/p/, 로그는 /private/tmp/claude-501/r19/out/*.log에 있다.

---
## 조정자 추가 — GUI 적대적 리뷰 J-1/J-2에서 확인

R19-13 (Med-High, 차단; G30/G11 — 기존 결함, phase0 기준 커밋에도 있음). `/api/report`(stdlib 웹 서버 `webapp_api._report_payload`와 FastAPI `api_server` 경로 모두)는 게시된 행을 `_rederive_report_items`로 다시 분석해 **다시 분석한 결과**에 서명하지만, 그 결과를 게시된 행과 비교하지 않는다.
- 검사 뒤 파일이 바뀌면(내용 교체, 압축 파일 구성 파일 교체, 파일 삭제) 서명된 JSON/HTML/포렌식 PDF/증거설명서가 화면과 다른 결론을 담는다. 예: 화면 "manipulation_evidence" → 서명 본문 "undetermined". 응답은 200이고 `verify-report`는 검증됨이다.
- 재현: 생성 메타데이터(PNG tEXt parameters, DALL-E EXIF 등)가 있는 파일을 검사한 뒤 그 파일을 평범한 이미지로 덮어쓰고, 같은 행을 `/api/report?format=json`으로 보낸다 → 200, 다른 결론.
- 전시물이 아닌 파일을 검사 뒤 지우면 그 행이 조용히 `excluded_items`로 옮겨진다.
- J-2: dedupe 검사에서 `status: "duplicate"`로 게시된 행이 전체 분석으로 다시 만들어진다(상태·결론이 다름) — `duplicate_of`가 유지되지 않는다.
- → 다시 만든 행을 게시된 행과 sha256(압축 구성 파일은 구성 파일 해시)·status·verdict_code로 비교한다. 하나라도 다르거나 다시 만들 수 없는 행이 있으면 모든 서명 형식을 409로 거부하고 `changed_rows`([{path, posted_sha256, server_sha256, posted_status, server_status, posted_verdict, server_verdict}])와 한국어 메시지("검사 뒤 바뀐 파일이 있습니다 — 다시 검사하십시오")를 돌려준다. 중복 행의 의미를 유지한다. 두 서버가 같게 동작하고, GUI는 409 메시지와 바뀐 경로를 보여 준다.
