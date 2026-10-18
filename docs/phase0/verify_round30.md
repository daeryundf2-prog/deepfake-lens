# 독립 검증 라운드 30 결과 (HEAD 0cfbd03) — 새 형식 네 번째 라운드

검증 대상: 비공개 클론 `scratchpad/repo`(`git clone --no-hardlinks`, git 이력 있음), 브랜치 phase0 HEAD 0cfbd03(= origin/phase0 `daeryundf2-prog/deepfake-lens`). 6a3ad1f 이후 코드 변경은 R29-1~R29-6 수정 여섯 개(c6fd8f3·d543820·bdc1438·8cbdeb3·8b778c4·43521d8 — 모두 `webapp_api.py`의 업로드 경로와 `test_axis_product.UploadEntryPointEquivalenceTest`), 나머지는 문서(e3ce6e1·6dfd3fa, `docs/deepfake-lens-service.md`)와 기록(0cfbd03). 로컬 main = dad9730. 수정한 것은 없음.
측정 조건: Docker Desktop(10 CPU, 7.75 GiB), 게이트 컨테이너 모두 `--cpus 4`(GUI 스모크만 `--cpus 3 --shm-size=1g`), 탐침 컨테이너 `--cpus 2`(대용량 업로드 탐침만 `--memory 2500m`), `/repo`는 git 이력이 있는 클론(`.git` 디렉터리). 게이트는 두 줄로 동시에 돌렸음(줄 A: `:latest` 1·2회차 → `:tech2`, 줄 B: `:tech` 1·2회차 → `:gui`; 처음 약 1시간은 탐침 컨테이너 1~2개가 더 있었음 — `:tech` 2회차 단위 테스트 1565초는 그 동시 부하). 성능 측정은 게이트·탐침이 모두 끝나고 다른 컨테이너가 하나도 없을 때(`docker ps` 0개, `perf/containers_start.txt`) 혼자 돌렸음. 탐침 스크립트·로그: `r30-out/probe/`(GUI 탐침은 `r30-out/probe/gui/`), 성능: `r30-out/perf/`, 게이트 로그: `r30-out/gate-*.log`.

## 게이트 결과

| 게이트 | 이미지 | 결과 | 비고 |
| --- | --- | --- | --- |
| `gate.sh full` 1회차 | `deepfake-lens-gate:latest` | **GATE rc=0** (24분, :tech와 동시) | /venv 2136개 OK(skipped=125), /venv-api 2136개 OK(skipped=93), ruff 통과, mypy 162개 파일 문제 없음, coverage 81%, cli_smoke OK(서브명령 45개/기능 검사 26개), 측정 게이트 10/10, 계약 PIN 일치, stress_shutdown 30회 중 실패 0, qa-main 202개 실패 0(통과 19, 수동 4, 1단계 12, 건너뜀(환경) 1) |
| `gate.sh full` 2회차 | `:latest` | **GATE rc=0** (51분 — 탐침과 동시) | /venv 2136 OK(skipped=125), /venv-api 2136 OK(skipped=93) — 1회차와 같음(플레이크 없음) |
| `gate.sh full` 1회차 | `deepfake-lens-gate:tech` | **GATE rc=0** (60분) | /venv 2136 OK(skipped=112), /venv-api 2136 OK(skipped=93), coverage 82%, 나머지 단계 :latest와 같음, qa-main 19/0 |
| `gate.sh full` 2회차 | `:tech` | **GATE rc=0** (53분) | /venv 2136 OK(skipped=112), /venv-api 2136 OK(skipped=93), qa-main 19/0 — 1회차와 같음 |
| `gate.sh full` | `deepfake-lens-gate:tech2`(PerTh 가중치, 8.1(a) 필수) | **GATE rc=0** (44분) | /venv 2136 OK(skipped=109 — :tech보다 3개 적음: PerTh 가중치 테스트), /venv-api 2136 OK(skipped=93), coverage 82%, qa-main 19/0 |
| `gate.sh gui` | `deepfake-lens-gate:gui` | **GATE rc=0** | 스모크 2개 모두 `GUI smoke PASS`(fixtures, /tmp/adv) |
| 기록 검사 | `:latest` (`gate.sh cmd`) | **통과** | `qa_phase0.py --verify-record`: 「docs/CONFORMANCE.md는 6dfd3fa2be7d에서 생성되었고 그 뒤 다른 변경이 없습니다」 rc=0(`PIPESTATUS`); `build_traceability_commits.py --check`: 「추적표 최신: 커밋 484개(dad9730..6dfd3fa), 표 이후 커밋 1개(재생성 커밋만)」 rc=0 (`record-check.log`) |

테스트 수 2129 → 2136은 `test_axis_product.UploadEntryPointEquivalenceTest`의 7개. 이 클래스를 6a3ad1f 트리에 넣어 돌리면 0바이트 `/api/check`, 쓸 수 없는 이름의 zip·tar.gz(세 진입점), 여러 파일 `/api/check`, `/api/compare` 개수, 예외 시 실패 행에서 실패·오류가 나고, 0cfbd03에서는 7개 OK(`probe/old_test.log`) — 커밋 본문의 「fails on 6a3ad1f」와 맞음.

## 축별 요약

- **판정**: Med 이상 0건. 거짓 문구 5건(R30-1~R30-5, 모두 Low).
  - R29 회귀 탐침(29차 `probe_upload.py`·`probe_upload_live.py`·`probe_standin.py`를 그대로 다시 돌림): 41가지 이름 × 바이트에서 web `/api/analyze-upload`·web `/api/check`·api-serve `/api/analyze-upload`가 같고, 비교 가능한 35건 모두 폴더 검사 행과 같음(`folder_eq_upload_result: true`), 누출 0, sha256 모두 일치, 24스레드·12요청 동시 업로드 정상, 서버 종료 뒤 `dflens-up-*` 0개. 29차에 다르던 두 곳이 바뀜: 0바이트 `/api/check` → 200 `analyzed`·이름 검사 `ran`(R29-1), 300자·270바이트·NUL 이름의 zip → 세 진입점 모두 200, 구성원 행 `analyzed`·이름 검사 「일치」, 컨테이너 행 `expanded`(R29-2).
  - **등식 표 밖의 입력 부류**(`probe_mp_live.py`, `explore_mp*.py`, GUI 탐침): 등식 표는 모든 파트를 `Content-Type: application/octet-stream`(또는 지정 없음)과 서버식 인코딩(`filename*`·역슬래시 이스케이프)으로만 보낸다. 실제 GUI 클라이언트(Chromium)는 파트의 `Content-Type`을 확장자로 정하고(`.eml` → `message/rfc822`, `.mht`·`.mhtml` → `multipart/related`, `gui/mime.log`) 파일 이름을 WHATWG 규칙(역슬래시는 그대로, `"`는 `%22`)으로 보낸다. 이 두 축에서 R30-1~R30-3이 나옴. 그 밖에 시험한 것 — `filename`과 `filename*` 둘 다(순서 무관하게 `filename*` 우선, 세 진입점 같음), `filename=` 중복(첫 값), 비UTF-8 `filename` 바이트(EUC-KR로 복호, 폴더 행과 같은 규칙), 파일 파트 + 일반 필드 혼합(필드 무시), 이름 없는 파트·빈 `filename=""`(400 「업로드된 파일이 없습니다」 — 사실), `Content-Transfer-Encoding: base64` 파트(복호해 원래 sha256), `text/plain; charset=latin-1` 파트(바이트 그대로), 무작위 바이너리 310개 × `application/octet-stream`·`text/*`·`image/png`·`application/json`(바이트 변화 0), 0바이트 파트 2000개(200, 행 2000개 — 20개 분석 + 1980개 `skipped`, 0.4초), `.ZIP`·`.TAR.GZ` 대문자 확장자(압축으로 처리, 폴더와 같음), 구성원에 `.eml`이 있는 zip(구성원 `unsupported` 행 — 폴더와 같음), 256 MiB 상한 근처 본문(`probe_big.log` — 아래 백로그 후보).
  - TB 층이 결론을 만들지 않음: `probe_tb.py`(:latest)에서 강한 근거 없이 결론이 난 행 0건, workers 1/4 scan JSON 바이트 동일. :tech2에서 `synthetic-perth.wav`는 「등록된 생성기 워터마크 일치(PerTh 1비트 존재 표식 — 결론 근거 아님)」 synthetic/moderate + 검출 신뢰도 1.000, 판단 불가; `synthetic-clean.wav`는 0.004 「검출 안 됨 — 무정보」; workers 1/4 scan JSON 바이트 동일(`probe_tech2_perth2.log`, 29차와 같음). 29차 대비 행 차이는 `narrowband.wav`의 대역폭 5.9→5.8 kHz 하나 — 탐침이 시드 없는 `anoisesrc`로 매번 새로 만드는 파일이라 판정과 무관.
  - 손상 입력(`probe_malformed.py`, 19개): 29차와 같음 — 충돌 없음, 결론 0건, workers 1/4 동일.
  - 기존 판정 회귀: 6a3ad1f 대비 fixtures 72행·적대 픽스처 110행에서 verdict·status·근거 제목 변화 0건(`cmp.latest.log`).
  - 진입점 동일성: `classify --format json` = `scan` 행(TB 폴더 26개 파일, /venv와 /venv-api 각각 차이 0, 증거설명서 결론 = scan 결론, `probe_classify_both.log`). 같은 venv에서 CLI = web(라이브) = api-serve(라이브)의 행이 같음(`probe_cli.log`). workers 1/4는 scan JSON이 바이트 단위로 같음(TB 폴더 :latest·:tech2, 손상 폴더, 증거 폴더 상대/절대 경로, perth 폴더).
- **서명**: Med 이상 0건. `probe_srv.py` 결과가 29차와 같음(작업 id·진행 표본·키 id 말고 차이 없음) — 서명 행 집합 = 작업 행 집합, 주입 필드 400·`side=defendant` 400·다른 소유자 404·위조 작업 404, 검사 뒤 수정·삭제 → json·html·pdf·evidence 409(재시작 뒤에도), 취소된 작업 보고서 200·`summary.cancelled` true, `verify-report` 정상 rc=0·변조 rc=1, 라이브 web/api-serve JSON 보고서 rc=0. 업로드 행은 서명되지 않고 `excluded_items`에는 경로·표시·사유·`name_source`만 들어가므로 R30-2의 틀린 sha256은 서명 본문에 들어가지 않음(확인함).
- **증거**: Med 이상 0건. 게이트 범위 명령(`scan` w1/w4/상대 경로/`--sign`/`--html-out`, `classify` PDF·PNG, `evidence-statement` JSON·MD·PDF, `verify-report`, `doctor`)과 라이브 `web`·`api-serve`(비동기 검사, scan-status, json·html·pdf·evidence·evidence-preview) 전후로 사건 폴더의 sha256·mtime·권한·파일 수가 같음(`after_cli_unchanged`, `after_servers_unchanged`, `new_files_in_case: []`). 업로드 탐침의 서버 읽기 폴더는 `p.png` 하나 그대로. QA-IN-1은 qa-main에서 통과.

## 지적사항

### R30-1 — 브라우저가 `message/*`로 보내는 파일(.eml)은 모든 업로드 경로에서 사라진다: 혼자면 「업로드된 파일이 없습니다」, 다른 파일과 함께면 행 없이 빠지고, 비교는 「받은 파일 1개」
- 심각도 Low, 축 판정(업로드 경로 — 결론에는 영향 없음, 그 파일은 미지원 형식), **사용자에게 보이는 거짓 문구: 예**(「실제와 다른 실패 사유」, 사실과 다른 파일 수) → 이번 라운드 수정 대상.
- 재현 1(GUI, `gui/gui_probe.log`, `gui/gui_probe2.log`, Playwright Chromium in `:gui`): 사건 폴더 `emlonly/`에 `mail.eml` 하나를 두고 GUI 「폴더 올리기」(`#dir-picker`) → 「검사」. 브라우저가 보낸 파트는 `Content-Disposition: form-data; name="files"; filename="emlonly/mail.eml"` + `Content-Type: message/rfc822` → 400 `{"error": "업로드된 파일이 없습니다"}`, 화면 알림 「오류 분석 중 오류: 업로드된 파일이 없습니다」. 파일은 올라갔다. 같은 파일을 빠른 검사(`#qc-file`)에 고르면 「검사 실패: 업로드된 파일이 없습니다」.
- 재현 2(같은 로그): 폴더 `mixed/`(`a.png`, `mail.eml`, `page.mht`)를 올리면 파일 칩 3개, 응답 행은 `a.png`·`page.mht` 2개 — `mail.eml`은 행 없이 빠진다(29차 R29-4가 막은 「파일이 조용히 사라짐」과 같은 부류). 같은 폴더를 폴더 검사하면 `mail.eml`은 `unsupported` 행 「지원 형식이 아닙니다.」로 나온다(`explore_mp.log`의 `eml_octet` — 같은 바이트를 `application/octet-stream`으로 보내도 그 행). 20개씩 나눠 보내는 GUI(`gui.js:1414-1421`)에서 한 묶음이 모두 .eml이면 그 400으로 업로드 전체가 멈추고 앞 묶음의 결과도 버려진다(`gui.js:1421`의 `return`).
- 재현 3(비교, `gui/gui_probe2.log`의 `compare_eml`): 파일 A `a.wav`, 파일 B `mail.eml` → 「비교 실패: 비교할 파일 2개가 필요합니다(받은 파일 1개)」. 두 파일을 보냈다(R29-6이 넣은 개수 문구가 이 경우 사실과 다름).
- 재현 4(API, `probe_mp_live.log`, web·api-serve 모두): 파일 25개(PNG 22개 + `message/rfc822` 파트 3개) → 행 22개, 넘친 행의 사유 「파일 수 상한(20개) 초과 — 이 요청의 파일 22개 중 앞의 20개만 분석함」(25개를 보냄), `summary.total` 22. PNG 바이트를 `Content-Type: message/rfc822`로 보내도 400 「업로드된 파일이 없습니다」.
- 원인: 업로드 본문을 `email.parser.BytesParser`로 파싱하므로 파트의 `Content-Type`이 `message/*`이면 파서가 그 본문을 하위 메시지로 파싱하고 `part.get_payload(decode=True)`가 `None`을 돌려준다(무작위 바이트 310개 × `message/rfc822`·`message/global`·`message/partial`에서 310개 모두 탈락, `explore_mp2.log`). `_upload_file_parts`(`webapp_api.py:1166-1180`)는 `_part_bytes(part) is None`인 파트를 파일이 아닌 것으로 건너뛴다(`_part_bytes` `:944-951`). 29차 이전부터 있던 동작(6a3ad1f `_analyze_upload_payload`도 `payload is None`이면 건너뜀)이며, R29 수정이 모든 업로드 경로를 이 한 함수로 모았으므로 지금은 세 진입점과 비교에 똑같이 나타난다.
- → 파트 본문을 파트의 `Content-Type`과 무관하게 받은 바이트 그대로 꺼낸다(R30-2와 같은 수정). 예: 바깥 multipart만 경계로 직접 나누고 각 파트의 헤더만 `email` 파서로 읽기(RFC 7578은 form-data 파트의 `Content-Transfer-Encoding`을 쓰지 않는다 — 지금 받아 주는 base64 파트를 유지할지는 별도 결정), 또는 파싱 전에 각 파트의 `Content-Type`을 `application/octet-stream`으로 바꾸고 원래 값은 무시. 회귀 테스트: `UploadEntryPointEquivalenceTest`에 「파트 Content-Type」 축(브라우저가 붙이는 값 — `application/octet-stream`, `message/rfc822`, `multipart/related`, `multipart/mixed`, `text/plain; charset=…`) — 각 행이 같은 바이트의 폴더 검사 행이고 sha256이 보낸 바이트의 것, 파일 수 문구(상한·`/api/check`·`/api/compare`)가 보낸 파일 파트 수.

### R30-2 — 브라우저가 `multipart/*`로 보내는 파일(.mht·.mhtml)은 바이트가 바뀐 채 분석되어, 행의 sha256이 그 파일의 해시가 아니다
- 심각도 Low, 축 판정(업로드 경로 — 분석 대상 바이트와 표시 해시), **사용자에게 보이는 거짓 문구: 예**(GUI 상세의 「sha256: …」가 올린 파일의 해시가 아님) → 이번 라운드 수정 대상.
- 재현(GUI, `gui/gui_probe.log`): 디스크의 `page.mht`(sha256 `dc9091cdee65…`)를 GUI 폴더 올리기·빠른 검사로 보내면 Chromium이 `Content-Type: multipart/related`를 붙이고, 행은 `unsupported`이지만 `sha256`은 `a421b89531bd…`. 같은 파일을 폴더 검사하면 `dc9091cdee65…`. GUI 상세 패널은 행의 `sha256`을 그대로 보이고(`gui.js:1814`), 검토 표시도 이 해시에 붙는다(`gui.js:51-52`).
- 재현(API, `probe_mp_live.log`의 `png_as_multipart_mixed`, web·api-serve 모두): PNG(sha256 `a90cf4fe5067…`)를 `Content-Type: multipart/mixed` 파트로 보내면 행은 `analyzed`·sha256 `ec2bddaeced7…` — 판정이 보낸 파일이 아닌 바이트에서 나온다. 무작위 바이트 310개 × `multipart/related`·`multipart/mixed`에서 310개 모두 바이트가 바뀜(2바이트 늘어남, `explore_mp2.log`, `explore_mp3.log`).
- 원인: R30-1과 같음 — `BytesParser`가 `multipart/*` 파트를 하위 multipart로 파싱한 뒤 `get_payload(decode=True)`가 다시 직렬화한 바이트를 돌려준다. 서명 축에는 영향 없음(`excluded_items`에 sha256이 없음).
- → R30-1과 같은 수정. 회귀 테스트도 같은 축(sha256 = 보낸 바이트의 sha256).

### R30-3 — 브라우저가 보낸 역슬래시가 든 파일 이름을 서버가 이스케이프로 풀어, 이름이 바뀌고 이름 검사가 사실과 다른 「일치」를 낸다
- 심각도 Low, 축 판정(이름에 의존하는 검사 — R28-1의 반대 방향), **사용자에게 보이는 거짓 문구: 예**(실제 이름은 형식과 맞지 않는데 「이름 형식 일치」; 행 이름이 그 파일의 이름이 아님) → 이번 라운드 수정 대상. 역슬래시가 든 파일 이름은 macOS·Linux에서만 가능하고 드물다.
- 재현(GUI, `gui/gui_bs.log`): 빠른 검사에 이름이 `a\b.png`인 파일 → Chromium이 보낸 헤더 `filename="a\b.png"`(WHATWG multipart/form-data 인코딩: `"`·CR·LF만 `%22`·`%0D`·`%0A`로 바꾸고 역슬래시는 그대로) → 행 이름 `ab.png`, 화면 「ab.png」. 이름 `KakaoTalk_20240101_123456789\x.png` → 행 `KakaoTalk_20240101_123456789x.png`. 이름 `case.zip\`(R29-3의 사례) → 헤더 `filename="case.zip\"` → 행 이름 `"case.zip\"`(따옴표 포함), `unsupported` — R29-3 커밋이 말하는 「폴더 검사의 구성원 행과 컨테이너 행」이 GUI에서는 나오지 않는다.
- 재현(사실과 다른 판정 문구, `explore_bs.log` — Chromium이 보내는 그대로의 헤더를 `_check_file_payload`에): 실제 이름 `KakaoTalk_2024010\1_123456789.png`(형식과 맞지 않음) → 업로드 행 이름 `KakaoTalk_20240101_123456789.png`, `file_name_pattern` `ran` 「이름 형식 일치: 카카오톡에서 저장한 파일 이름 형식…」. 같은 이름의 파일을 폴더 검사하면 「등록된 이름 형식 9개와 일치 없음 — 무정보」.
- 원인: `_part_filename`(`webapp_api.py:920-942`)이 따옴표 안의 값을 RFC 822식 quoted-pair로 보고 `re.sub(rb"\\(.)", rb"\1", …)`(`:938`)로 역슬래시를 지운다(`_FILENAME` 정규식 `:907`도 `\"`를 값 안의 따옴표로 본다). 브라우저는 역슬래시를 이스케이프하지 않으므로 이 해석은 GUI가 보내는 이름과 맞지 않는다. 등식 표는 역슬래시 이름을 `filename*`로만 보내 이 인코딩을 시험하지 않는다.
- → 따옴표 값은 첫 `"`에서 끝내고 역슬래시를 글자 그대로 둔다(WHATWG 인코딩 — 브라우저는 값 안에 날 `"`를 넣지 않음). 그 뒤 R29의 base name 규칙(마지막 `/`·`\` 뒤)을 적용할지, 브라우저 이름에는 `\`를 구분자로 보지 않을지는 함께 결정(29차 참고 사항). 회귀 테스트: 등식 표에 「브라우저 인코딩 이름」 축(`filename="a\b.png"`, `filename="x.zip\"`, `filename="say %22hi%22.png"` — Chromium이 보내는 바이트 그대로).

### R30-4 — 비교에서 한쪽 오디오의 특징을 추출하지 못하면(0바이트 WAV, 오디오가 아닌 내용) GUI가 「화자 유사도 0」과 「거리 1」을 보인다
- 심각도 Low, 축 판정(참고 계층 표시 — 결론 아님), **사용자에게 보이는 거짓 문구: 예**(측정하지 않은 값을 측정값처럼 표시 — R19-10 「점수가 없으면 "없음", 측정처럼 보이는 0 금지」와 같은 부류) → 이번 라운드 수정 대상.
- 재현(GUI, `gui/gui_probe2.log`의 `compare_wav0`, `compare_png_as_wav`): 비교 화면에서 A `a.wav`(1초 사인파), B 0바이트 `b.wav` → 200, 화면 큰 숫자 「0」, 「화자 유사도 (음성) · mfcc」, 「측정값 거리 1」, 그 아래 「특징 추출 실패 — librosa 또는 오디오 형식을 확인하세요.」·「한쪽 파일의 특징 추출에 실패했습니다.」. B에 PNG 바이트를 `.wav` 이름으로 넣어도 같음. API 응답은 `raw_score: 0`, `reference_band: "unavailable"`, `diagnostic.distance: 1.0`(`explore_cmp.log`).
- 원인: `audio.compare_speakers`(`audio.py:993`)가 추출 실패 시 `SpeakerComparison(0, 1.0, UNAVAILABLE_BAND, …)` — 점수 0과 거리 1.0을 값으로 넣고, GUI(`gui.js:2966`, `:2976`)는 `raw_score`가 `null`이 아니면 숫자로, `distance`가 있으면 거리로 그린다. R29-6 이전에는 0바이트 파일이 비교에서 탈락해(「비교할 파일 2개가 필요합니다」) 이 화면이 0바이트로는 나오지 않았고, 오디오가 아닌 내용의 `.wav`로는 이전부터 나왔다. 텍스트 쪽 변형: 0바이트 `a.txt`와 글 200자 → 「문체 벡터 평균 상대 차이 0.3841 (참고 원점수 0/100 …; 표본 0자/200자)」 — 빈 표본에서 거리를 계산해 보인다(`explore_cmp.log`의 `txt0+txt`).
- → 추출 실패(또는 표본 0자)면 점수·거리를 `None`으로 두어 GUI가 「없음」을 보이게 하거나(R19-10 경로), `/api/compare`가 사실대로 400 「한쪽 파일에서 화자 특징을 추출할 수 없어 비교하지 않았습니다」. 회귀 테스트: 등식 표의 `/api/compare` 시험에 「0바이트·오디오 아닌 내용·빈 텍스트」 → 응답에 수치가 없음.

### R30-5 — Content-Type에 boundary가 없거나 다른 요청에 「업로드 본문이 잘렸습니다(닫는 경계 없음)」
- 심각도 Low, 축 판정(실패 사유), **사용자에게 보이는 거짓 문구: 예**(API 전용 — 브라우저는 항상 맞는 boundary를 보냄) → 수정 대상(문구만).
- 재현(`probe_mp_live.log`의 `ct[…]`, web `/api/analyze-upload`·`/api/check`, api-serve `/api/analyze-upload`): 온전한 본문에 `Content-Type: multipart/form-data`(boundary 없음), `…; boundary=`(빈 값), `…; boundary=wrong`(본문과 다른 값) → 모두 400 「업로드 본문이 잘렸습니다(닫는 경계 없음) — 파일을 다시 보내십시오」. 본문은 잘리지 않았고, 다시 보내도 같다.
- 원인: `_multipart_incomplete`(`webapp_api.py:195-204`)가 boundary 매개변수가 없을 때도 `True`(잘림)를 돌려주고, 선언한 boundary가 본문에 없을 때와 닫는 경계만 없을 때를 구분하지 않는다.
- → boundary 매개변수가 없거나 빈 값이면 400 「Content-Type에 multipart boundary가 없습니다」, 선언한 boundary가 본문에 한 번도 없으면 「본문의 경계가 Content-Type의 boundary와 다릅니다」, 여는 경계는 있고 닫는 경계만 없을 때만 지금 문구.

## BACKLOG 추가 (후보)
- 상한 근처 업로드의 메모리 — 256 MiB 상한보다 4 KiB 작은 본문 하나에 서버 최대 RSS가 web 2461 MB, api-serve 2449 MB(본문의 약 9.5배; `--memory 2500m` 컨테이너에서 겨우 통과, 행은 `analyzed`·sha256 일치, `probe_big.log`). `rfile.read` 사본 + `BytesParser`의 surrogateescape 문자열 사본 때문으로 보인다. 동시에 두 개면 8 GB 기계에서도 OOM 가능. 상한 초과(256 MiB + 1)는 web이 본문을 읽기 전에 413으로 끊고(클라이언트는 broken pipe), api-serve는 413. (30차, Low, 성능·견고성) — R30-1의 직접 분할 수정이 같이 줄일 수 있음.
- `docs/deepfake-lens-service.md:168` 「Analysis failures surface as HTTP 500 with the exception text.」는 바로 위 R29 문단(「an analysis that raises is one `failed` row」)과 어긋나고, 표의 E20(「analysis raised → 500」)도 지금은 파트 분석 예외가 실패 행이 된 뒤의 남은 경로(파싱 단계 예외)만 해당한다. (30차, Low, 문서)
- 파일 이름의 `"`는 브라우저가 `%22`로 보내므로 행 이름이 `say %22hi%22.png`로 보인다(`gui/gui_bs.log`) — 서버는 글자 그대로의 `%22`와 구별할 수 없다(WHATWG가 인정하는 모호성). GUI가 원래 이름을 별도 필드(예: JSON 이름 목록)로 함께 보내면 R30-3과 함께 없앨 수 있다. (30차, Cosmetic, 판정)
- 참고(지적 아님): Chromium의 폴더 선택(`webkitdirectory`)은 `webkitRelativePath`에서 역슬래시를 `/`로 바꿔 보낸다(`a\b.png` → `up/a/b.png`, `x.zip\` → `up/x.zip/`, `gui/gui_names.log`) — 브라우저 동작이라 서버가 고칠 수 없음. 열 수 없는 압축 파일 행의 status `unknown`은 폴더 검사와 같음(`explore_unk.log`).

## R29 회귀 종료 확인
- **R29-1(c6fd8f3)**: 라이브 web `/api/check` 0바이트 → 200 `analyzed`·이름 검사 `ran`(`probe_upload_live.log`의 `zero/api/check`), GUI 빠른 검사의 0바이트 `KakaoTalk_….png` → 「판단 불가 — 검사 실패」와 실패한 검사 5개(폴더 검사 행과 같음, `gui/gui_probe2.log`). 원래 결함 재현 안 됨. 같은 함수의 다른 탈락 경로(파트 `Content-Type`)는 R30-1.
- **R29-2(d543820)**: 300자·270바이트·NUL 이름의 zip → web `/api/analyze-upload`·`/api/check`, api-serve `/api/analyze-upload` 모두 200, 구성원 행 이름 검사 「일치」(라이브, `probe_upload_live.log`). GUI 빠른 검사에서 `"A"*300+".zip"`, `"가"*90+".zip"` → 구성원 행 `analyzed`·컨테이너 `expanded`(`gui/gui_probe2.log`). 500 없음.
- **R29-3(bdc1438)**: `filename*`로 보낸 `x.zip\`은 압축으로 처리(등식 표 OK). 브라우저가 보내는 `filename="case.zip\"`에서는 아님 → R30-3.
- **R29-4(8cbdeb3)**: 25개 파일(모두 `application/octet-stream`/`image/png`) → 20개 분석 + 넘친 파일마다 `skipped` 행, 사유의 개수 맞음; 0바이트 파트 2000개 → 행 2000개(0.4초). `message/*` 파트가 섞이면 개수가 틀림 → R30-1. GUI는 20개씩 나눠 보내므로(`gui.js:1414`) GUI에서 이 `skipped` 행은 나오지 않는다.
- **R29-5(8b778c4)**: 파일 파트 2000개 `/api/check` → 400 「빠른 검사는 파일 1개만 받습니다(받은 파일 2000개) — …」(0.5초). GUI 빠른 검사는 파일 하나만 보내므로(`gui.js:2776-2777`) GUI에서는 나오지 않는다 — 문구는 사실.
- **R29-6(43521d8)**: 3개 → 400 「…(받은 파일 3개)」, 0바이트 WAV는 파일로 셈, 300자 이름 → 200(500 없음), 같은 이름 두 개 → 200, zip+zip·확장자 없음 → 기존 400 「지원되는 쌍이 아닙니다(…)」(사실), 같은 파일 두 개 → 100. 0바이트가 셈에 들면서 GUI에서 R30-4 화면이 0바이트로 나오게 됨; `.eml` 비교의 「받은 파일 1개」는 R30-1.
- 커밋 본문의 다른 호출자(보고서 `uploads`, api-serve 경로 검사, 붙여 넣은 텍스트): `probe_srv`·`probe_cli`·`probe_tb` 결과가 29차와 같음.
- R28-1~R28-5, R27-1·R27-3·R27-4(BL-19~BL-21), BL-22·BL-23: 그대로(악화 없음).

## 성능 (scan, 픽셀 off, `--no-default-engine`, `--max-files 5000`, `:latest`, `--cpus 4`, 다른 컨테이너 없음)
명령 전체 벽시계 시간(프로세스 시작 포함), 3회(1차, 전체 말뭉치) / 5회(2차, 이미지·문서·보고서) 교대 실행의 중앙값. 원자료: `perf/perf.log`·`perf/perf_table.md`(2130892·6a3ad1f·0cfbd03, 3회), `perf/perf2.log`·`perf/perf2_table.md`(같은 세 트리, 5회). 말뭉치는 28·29차와 같은 `mkcorp.py`.

| 폴더 | workers | 2130892 | 6a3ad1f (29차) | HEAD 0cfbd03 | HEAD / 2130892 | HEAD / 6a3ad1f |
| --- | --- | --- | --- | --- | --- | --- |
| 빈 폴더 | 1 | 0.19 / 0.20 s | 0.23 / 0.20 s | 0.23 / 0.23 s | +0.03~0.04 s | 잡음 |
| image2000 | 1 | 1.65 / 1.67 s | 1.88 / 1.78 s | 1.88 / 1.80 s | +14% / **+8%** | 0% / +1% |
| image2000 | 4 | 2.53 / 2.50 s | 3.06 / 3.08 s | 3.05 / 3.07 s | +21% / **+23%** | 0% / 0% |
| wav300 | 1 | 19.11 s | 7.36 s | 7.35 s | −62% | 0% |
| wav300 | 4 | 21.40 s | 3.41 s | 3.42 s | −84% | 0% |
| mp4300 | 1 | 45.41 s | 25.67 s | 25.72 s | −43% | 0% |
| mp4300 | 4 | 30.11 s | 9.14 s | 9.28 s | −69% | +2% |
| doc600 | 1 | 0.50 / 0.48 s | 0.70 / 0.66 s | 0.69 / 0.66 s | +38% / +38% (예외) | −1% / 0% |
| doc600 | 4 | 0.58 / 0.60 s | 0.90 / 0.93 s | 0.92 / 0.93 s | +59% / +55% (예외) | +2% / 0% |
| html300 (보고서 경로) | 1 | 0.83 / 0.81 s | 0.56 / 0.59 s | 0.59 / 0.59 s | −29% / −27% | +5% / 0% |

(「a / b」는 1차 3회 / 2차 5회 중앙값.)
- 6a3ad1f..0cfbd03의 코드 변경은 업로드 함수뿐이라 scan 경로는 같고, 측정도 모든 폴더에서 ±5% 안.
- 이미지: 2130892 대비 +8~23%로 25% 예산 안(28·29차와 같은 수준, workers 4의 시작 비용 경계선 상황은 28차 설명 그대로).
- 오디오·영상: 2130892보다 빠름.
- 문서: +38%/+55~59%는 §8 예외(BL-23 — PDF C2PA 판독과 TB 문서 층). 6a3ad1f 대비 변화 없음.
- 보고서 경로(html300): 2130892보다 27~29% 빠르고 6a3ad1f와 같음.
- 성능 Med: 0건.

## 최종 판정
게이트 0 판정: **아니오** — 사용자에게 보이는 거짓 문구 5건(R30-1~R30-5, 모두 Low, 판정 축). 세 축의 Med 이상은 0건, 성능 Med도 0건, 게이트 6개(`:latest`×2, `:tech`×2, `:tech2`, `:gui`)와 기록 검사 모두 통과.
수정 목록:
- R30-1 — `message/*` 파트(브라우저의 .eml)가 모든 업로드 경로에서 탈락: 「업로드된 파일이 없습니다」, 행 없이 빠짐, 「받은 파일 1개」, 상한 문구의 파일 수.
- R30-2 — `multipart/*` 파트(브라우저의 .mht·.mhtml)의 바이트가 바뀌어 행 sha256과 분석 대상이 올린 파일과 다름.
- R30-3 — 브라우저가 보낸 역슬래시 이름을 이스케이프로 풀어 이름이 바뀌고 「이름 형식 일치」가 사실과 다를 수 있음(R29-3도 GUI에서는 닿지 않음).
- R30-4 — 비교에서 특징 추출 실패(0바이트·비오디오)·빈 텍스트에 「0」·「거리 1」·「0/100」을 측정값처럼 표시.
- R30-5 — boundary 없음·불일치에 「업로드 본문이 잘렸습니다」(API 전용).
(R30-1·R30-2는 한 수정 — 파트 바이트를 Content-Type과 무관하게 그대로 꺼내기 — 으로 닫히며, 등식 표에 「파트 Content-Type」·「브라우저 인코딩 이름」 두 축을 더하는 것을 함께 제안한다(8.3 축 곱).)
백로그 후보(판정과 별도): 상한 근처 업로드 메모리(약 9.5배), 서비스 문서의 500 문장, `%22` 이름 표시.
