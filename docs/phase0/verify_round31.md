# 독립 검증 라운드 31 결과 (HEAD c29cbb5) — 새 형식 다섯 번째 라운드

검증 대상: 비공개 클론 `scratchpad/r31`(`git clone --no-hardlinks`, git 이력 있음), 브랜치 phase0 HEAD c29cbb5(= origin/phase0 `daeryundf2-prog/deepfake-lens`). 0cfbd03 이후 코드 변경은 R30 수정 둘 — 210cf09(새 multipart/form-data 파서 `deepfake_lens/form_data.py`, 세 업로드 경로 web·api-serve `/api/analyze-upload`, web `/api/check`, web `/api/compare`가 모두 이 파서를 씀, `email.parser.BytesParser` 제거)와 548a168(`compare_speakers`·`compare_texts`가 측정하지 않은 쌍에 `None` 점수·거리, GUI 「없음 — 측정하지 않음」) — 그리고 문서(5bed165·454fd84)와 기록(c29cbb5). 로컬 main = dad9730. 수정한 것은 없음.
측정 조건: Docker Desktop(10 CPU, 7.75 GiB), 게이트 컨테이너 모두 `--cpus 4`(GUI 스모크만 `--cpus 3 --shm-size=1g`), 탐침 컨테이너 `--cpus 1~2`(메모리 탐침은 `--memory 1800m`/`2500m`/`3g`), GUI 탐침은 `:gui`에서 `--cpus 3 --shm-size=1g`. `/repo`는 git 이력이 있는 클론(`.git` 디렉터리). 게이트는 두 줄로 동시에 돌렸음(줄 A: `:latest` 1·2회차 → `:tech2`, 줄 B: `:tech` 1·2회차 → `:gui`; 처음 약 1시간 반은 탐침 컨테이너 1~2개가 더 있었음). 성능 측정은 게이트·탐침이 모두 끝나고 다른 컨테이너가 하나도 없을 때(`docker ps` 0개, `perf/containers_start.txt`) 혼자 돌렸음. 탐침 스크립트·로그: `r31-out/probe/`(GUI 탐침은 `r31-out/probe/gui/`), 성능: `r31-out/perf/`, 게이트 로그: `r31-out/gate-*.log`. 탐침의 코드 트리는 `git archive`로 푼 `trees/c29cbb5`·`trees/0cfbd03`·`trees/2130892`(읽기 전용 마운트).

## 게이트 결과

| 게이트 | 이미지 | 결과 | 비고 |
| --- | --- | --- | --- |
| `gate.sh full` 1회차 | `deepfake-lens-gate:latest` | **GATE rc=0** (29분, :tech·탐침과 동시) | /venv 2142개 OK(skipped=125), /venv-api 2142개 OK(skipped=94), ruff 통과, mypy 163개 파일 문제 없음, coverage 81%, cli_smoke OK(서브명령 45개/기능 검사 26개), 측정 게이트 10/10, 계약 PIN 일치, stress_shutdown 30회 중 실패 0, qa-main 202개 실패 0(통과 19, 수동 4, 1단계 12, 건너뜀(환경) 1) |
| `gate.sh full` 2회차 | `:latest` | **GATE rc=0** (25분) | /venv 2142 OK(skipped=125), /venv-api 2142 OK(skipped=94) — 1회차와 같음(플레이크 없음) |
| `gate.sh full` 1회차 | `deepfake-lens-gate:tech` | **GATE rc=0** (45분) | /venv 2142 OK(skipped=112), /venv-api 2142 OK(skipped=94), coverage 82%, 나머지 단계 :latest와 같음, qa-main 19/0 |
| `gate.sh full` 2회차 | `:tech` | **GATE rc=0** (41분) | /venv 2142 OK(skipped=112), /venv-api 2142 OK(skipped=94), qa-main 19/0 — 1회차와 같음 |
| `gate.sh full` | `deepfake-lens-gate:tech2`(PerTh 가중치, 8.1(a) 필수) | **GATE rc=0** (41분) | /venv 2142 OK(skipped=109 — :tech보다 3개 적음: PerTh 가중치 테스트), /venv-api 2142 OK(skipped=94), coverage 82%, qa-main 19/0 |
| `gate.sh gui` | `deepfake-lens-gate:gui` | **GATE rc=0** | 스모크 2개 모두 `GUI smoke PASS`(fixtures, /tmp/adv) |
| 기록 검사 | `:latest` (`gate.sh cmd`) | **통과** | `qa_phase0.py --verify-record`: 「docs/CONFORMANCE.md는 454fd848fd3a에서 생성되었고 그 뒤 다른 변경이 없습니다」 rc=0; `build_traceability_commits.py --check`: 「추적표 최신: 커밋 490개(dad9730..454fd84), 표 이후 커밋 1개(재생성 커밋만)」 rc=0 (`record-check.log`) |

테스트 수 2136 → 2142는 R30 회귀 테스트 6개(`test_axis_product` 3개, `test_gui_model_score` 1개, `test_v6_probes` 2개). /venv-api의 건너뜀 93 → 94는 새 `SpeakerComparisonTest.test_a_side_without_audio_features_has_no_score_or_distance`(librosa 없는 환경에서 클래스째 건너뜀) — 정상. 변경된 기존 테스트 `test_short_text_adds_limitation`은 새 8자 하한 때문에 입력을 9자 이상으로 바꿨고 같은 단언(400자 한계 문구)에 `distance is not None`이 더해졌다 — 약화 아님.

## 새 파서 `form_data.py`의 퍼징 (이번 라운드의 중점)

- **임의 입력**(`probe_fd.py` B, hypothesis 60,000예, 토큰 사전 — CRLF·LF·CR·`--BB`·`\r\n--BB--`·빈 줄·`Content-Disposition`·`filename*`·`%22`·NUL 등 — × Content-Type 11종(따옴표·중복·빈 boundary·`BOUNDARY=` 대문자·닫히지 않은 따옴표·비라틴 문자·NUL)): `FormDataError` 밖의 예외 0건, 파트 바이트가 본문의 부분 문자열이 아닌 경우 0건, 50 ms 넘는 호출 0건(`probe_fd.log`).
- **정상 본문 왕복**(C, 브라우저식 본문 5,000개 — 0~5개 파트, 일반 필드 섞음, 이름에 `\`·`"`·CR·LF·`%`·`%22`·`%0D`·한글·이모지·결합 문자·`;`·`=`·`'`·`/`, 데이터 0~70,000바이트 무작위 + 경계 접두어·`\r\n--`·빈 줄 삽입, 파트 Content-Type 7종): 파일 수·이름·바이트 불일치 0건.
- **Node 20 undici(WHATWG fetch의 multipart/form-data 파서)와의 차분**(`fd_diff.py`, `undici_parse.js`, 400개 + 경계 사례 13개): 406/413 일치. 다른 7건은 모두 undici가 더 엄격하거나 설계상 다른 경우 — 본문 안의 `\r\n--boundaryX`(우리: 데이터, undici: 거부), 경계 뒤 공백, `filename*`(undici는 무시하고 거부), 닫히지 않은 따옴표, CP949 이름(우리: `증거.png`, undici: U+FFFD), 데이터 안의 `\r\n--boundary--TA`, 그리고 R31-1의 `preamble\n--BB`(아래). 이름·바이트가 서로 다르게 나온 정상 본문은 없음.
- **잘림**(D, 정상 본문 400개 × 닫는 경계 앞의 모든 자른 위치 약 27,000개): 전부 400 「업로드 본문이 잘렸습니다(닫는 경계 없음)」 — 잘린 본문이 200으로 분석된 경우 0건.
- **시간 증가**(F, 1·4·16 MiB × 18가지 모양 — 가짜 경계 `\r\n--BBX` 반복, 닫히지 않은 본문, 가짜 경계 프리앰블, 거대한 헤더 줄(`;`·`a=b`·`=`·`""` 반복), 헤더 줄 수만 개, 접힌 줄, 작은 파트 수백만 개, `-`·`--B`·CRLF·`\r\n--` 홍수, 빈 줄 없는 헤더 + 경계 반복): 모두 선형(4배 크기에 약 4배 시간, 16 MiB 최대 4.9초 — 파트 수백만 개). 이차 시간 입력 없음. 0cfbd03의 `BytesParser`는 이름 없는 파트 32 MiB에 237초(16 MiB 67초 — 초선형)였으므로 크게 나아짐(`mem_parts.log`).
- **메모리**(`mem_parts.py`, 한 측정 한 프로세스): 이름 없는 파트(`\r\n--BB\r\n\r\n` 반복) 32 MiB → 최대 RSS 408 MB(0cfbd03: 1190 MB), 파일 파트 64만 개(33 MiB) → 185 MB(0cfbd03: 568 MB). 256 MiB 상한 근처 단일 파일 업로드(`probe_big.py`, `--memory 2500m`): web 582 MB·api-serve 594 MB, 0.6/1.0초, 행 `analyzed`·sha256 일치 — BL-35의 「처리」 기록과 같음. 상한 초과는 web 413(본문 읽기 전 끊음 — 클라이언트 broken pipe), api-serve 413(30차와 같음).
- **파일 이름 문자셋**(A, 코덱 이름 455개 × 바이트 225개를 `decode_filename_bytes`와 `filename*=<charset>''…`로): `undefined` 코덱 하나만 `UnicodeError`를 던짐 → R31-4(500).
- **라이브 경계 사례**(`probe_mp31_live.py`, web·api-serve): 파트 `Content-Type`(`message/rfc822`·`multipart/related`·`multipart/mixed`)와 무관하게 sha256 = 보낸 바이트(R30-1·R30-2 닫힘), 역슬래시 이름 그대로(R30-3), `%0D%0A`·`%22` 복호(BL-36), `Content-Transfer-Encoding: base64` 파트는 base64 글자 그대로 분석(문서에 적힌 동작), 따옴표 없는 `filename=a.png\nX-Evil: 1`(헤더 줄 안의 맨 LF)은 이름 `a.png\nX-Evil: 1`의 `unsupported` 행(500 없음, 표시는 이스케이프됨), `boundary="…"` 따옴표 값 정상. 줄 끝이 CRLF가 아닌 본문 → R31-1, 대문자 미디어 타입 → R31-2, 빈 `filename*` → R31-3, `filename*=undefined''` → R31-4. 서버 종료 뒤 `dflens-up-*` 0개.
- **이름 → 서명 경로**(`probe_upname_report.py`): 새 파서가 만드는 이름(CR LF, LF, `"`, `\`, 탭)의 업로드 행을 `/api/report`의 `uploads`로 보내면 모두 `name_source: "server_upload"`, json·html·pdf·evidence 200, HTML에 날 CRLF 없음, `verify-report` rc=0.

## 축별 요약

- **판정**: Med 이상 0건. 사용자에게 보이는 거짓 문구 4건(R31-1·R31-2·R31-3·R31-5, 모두 Low), 500 1건(R31-4, Low, 거짓 문구 아님).
  - R30 회귀 탐침(30차 `probe_mp_live.py`, `explore_mp*.py`, `explore_bs.py`, `explore_cmp.py`, `explore_unk.py`와 GUI 탐침 `gui_probe.py`·`gui_probe2.py`·`gui_bs.py`·`gui_names.py`·`mime.py`를 그대로 다시 돌림 — 아래 「R30 회귀 종료 확인」).
  - 29차·30차의 업로드 탐침(`probe_upload.py`, `probe_upload_live.py`, `probe_standin.py`): 로그가 30차와 같음(임시 폴더 이름만 다름) — 41가지 이름 × 바이트에서 세 진입점과 폴더 검사 행이 같고, 누출 0, 동시 업로드 정상.
  - TB 층이 결론을 만들지 않음: `probe_tb.py`(:latest) 강한 근거 없이 결론이 난 행 0건, workers 1/4 scan JSON 바이트 동일, api = web. :tech2에서 `synthetic-perth.wav`·`synthetic-clean.wav`의 행과 workers 1/4 동일성이 30차와 같음(`probe_tech2_perth.log`, `probe_tech2_perth2.log` — 30차 로그와 숫자 말고 차이 없음).
  - 손상 입력(`probe_malformed.py`, 19개): 30차와 같음 — 충돌 없음, 결론 0건, workers 1/4 동일.
  - 기존 판정 회귀: 0cfbd03 대비 fixtures 72행·적대 픽스처 110행에서 verdict·status·근거 제목 변화 0건(`cmp.latest.log`).
  - 진입점 동일성: `classify --format json` = `scan` 행(`probe_classify.log`·`probe_classify_both.log`), CLI = web(라이브) = api-serve(라이브)(`probe_cli.log`), workers 1/4 scan JSON 바이트 동일 — 30차와 같음.
  - 비교(R30-4 회귀 탐침, `probe_mp31_live.py`의 `compare[…]`): 0바이트·PNG를 `.wav`로·빈/공백/7자 텍스트 → `raw_score: null`, `distance: null`, `unavailable`, 문구가 어느 쪽인지 맞게 말함(파일 A/파일 B/두 파일 모두). 8자 텍스트부터 비교함. 무음·직류 WAV는 특징이 추출되어 수치가 나옴(아래 백로그 후보).
- **서명**: Med 이상 0건. `probe_srv.py` 결과가 30차와 같음(진행 표본의 파일 이름 말고 차이 없음) — 서명 행 집합 = 작업 행 집합, 주입 필드 400·`side=defendant` 400·다른 소유자 404·위조 작업 404, 검사 뒤 수정·삭제 → json·html·pdf·evidence 409(재시작 뒤에도), 취소된 작업 보고서 200·`summary.cancelled` true, `verify-report` 정상 rc=0·변조 rc=1, 라이브 web/api-serve JSON 보고서 rc=0. 새 파서의 이름(CR LF·`"`·`\`)을 담은 업로드 행도 서명 본문에는 `excluded_items`의 경로·`name_source`·사유만(위).
- **증거**: Med 이상 0건. 게이트 범위 명령(`scan` w1/w4/상대 경로/`--sign`/`--html-out`, `classify`, `evidence-statement` JSON·MD·PDF, `verify-report`, `doctor`)과 라이브 `web`·`api-serve` 전후로 사건 폴더의 sha256·mtime·권한·파일 수가 같음(`probe_cli.log`: `after_cli_unchanged`·`after_servers_unchanged` true, `new_files_in_case: []`; `probe_tb.log`: `evidence_unchanged` true; `probe_srv.log`: `evidence_unchanged_before_change` true). `probe_preview`·`probe_cache` 30차와 같음. QA-IN-1은 qa-main에서 통과.

## 지적사항

### R31-1 — 줄 끝이 CRLF가 아닌 multipart 본문에 사실과 다른 거부 사유: 「본문의 경계가 Content-Type의 boundary와 다릅니다」(같은 boundary), 「업로드 본문이 잘렸습니다」(잘리지 않음), 「업로드된 파일이 없습니다」(파일을 보냄) — 0cfbd03은 같은 본문을 200으로 분석했다(회귀)
- 심각도 Low, 축 판정(업로드 실패 사유 — 결론에는 영향 없음), **사용자에게 보이는 거짓 문구: 예**(API 전용 — 브라우저·curl·requests·undici는 CRLF로 보냄; R30-5와 같은 부류) → 수정 대상. 직전 수정(210cf09)의 회귀.
- 재현(라이브, `probe_mp31_live.log`, web `/api/analyze-upload`·`/api/check`·`/api/compare`, api-serve `/api/analyze-upload` 모두 같음; boundary는 `----WebKitFormBoundaryR30xyz`로 Content-Type과 본문이 같다):
  - `lf_only`(정상 본문의 CRLF를 모두 LF로): 400 「본문의 경계가 Content-Type의 boundary와 다릅니다 — 같은 boundary로 다시 보내십시오」. `compare[wav+lf_body]`도 같음. 같은 boundary로 다시 보내도 같다.
  - `crlf_open_lf_rest`(여는 경계 줄만 CRLF, 나머지 LF), `cr_lf_mixed_close_lf`(닫는 경계만 LF): 400 「업로드 본문이 잘렸습니다(닫는 경계 없음) — 파일을 다시 보내십시오」 — 본문은 잘리지 않았다.
  - `preamble_lf`(`preamble\n` 뒤에 정상 본문): 400 「업로드된 파일이 없습니다」 — 파일 파트 하나를 보냈다. 파서가 LF 뒤의 여는 경계를 건너뛰고 닫는 경계 `\r\n--B--`를 여는 경계로 써서 파트 0개(`probe_fd.log` `E_edge.preamble_lf`: `OK []`).
  - 같은 부류(단위, `probe_fd.log` `E_edge`): CR만(`cr_only`) → 「boundary와 다릅니다」; 프리앰블 뒤 여는 경계 중간에서 잘린 본문(`preamble\r\n--B`) → 「boundary와 다릅니다」(잘림인데).
- 0cfbd03 대비(`old_vs_new.log`, 같은 본문을 두 트리의 업로드 파트 선택에): 0cfbd03은 `lf_only`·`lf_only_ct`·`crlf_open_lf_rest`·`cr_only`·`preamble_lf` 모두 `[('a.png', b'DATA')]`(200 분석), c29cbb5는 위 400들.
- 원인: `parse_form_data`(`form_data.py:171-212`)는 구분자를 `b"\r\n--" + boundary`로만 찾고(`:174`, `:194`), 여는 경계도 본문 맨 앞 또는 CRLF 뒤만 본다(`:177`, `:183`). 경계를 하나도 못 찾으면(`found is None`) 본문이 경계의 접두어일 때만 「잘림」이고 나머지는 전부 `MULTIPART_BOUNDARY_MISMATCH`(`:184-187`) — boundary 문자열 자체는 본문에 있는데 줄 끝만 다른 경우를 구분하지 않는다. 여는 경계를 찾은 뒤 다음 구분자가 없으면 무조건 `MULTIPART_INCOMPLETE`(`:195-196`).
- → 둘 중 하나: (a) 0cfbd03처럼 맨 LF(와 CR)도 줄 끝으로 받아 경계를 `(\r\n|\n)--boundary`로 찾기(파트 바이트는 그대로 — 데이터 안의 LF는 건드리지 않음), 또는 (b) RFC 2046의 CRLF만 받되, `--boundary`가 본문의 줄 시작(맨 앞 또는 LF·CR 뒤)에 있는데 CRLF 구분자가 없으면 별도의 400 「본문의 줄 끝이 CRLF가 아닙니다 — multipart/form-data 본문은 CRLF로 줄을 나눕니다」를 내고, 여는 경계 앞 프리앰블이 맨 LF로 끝나는 경우도 같은 사유로 거부(파일 파트가 있는데 「업로드된 파일이 없습니다」 금지). 잘림 판정은 「여는 경계가 있고 닫는 경계가 없는데 본문 안에 맨 LF 경계도 없음」일 때만. 회귀 테스트: 등식 표 `test_boundary_refusals_say_what_is_wrong`에 「줄 끝」 축(LF만·CR만·여는 줄만 CRLF·닫는 줄만 LF·LF 프리앰블·프리앰블 뒤 잘림) × 세 진입점 + 비교 — 각 사례가 200(같은 바이트의 폴더 행) 또는 그 원인을 말하는 400.

### R31-2 — 대문자 미디어 타입 `Multipart/Form-Data`에 「multipart/form-data 업로드가 필요합니다」
- 심각도 Low, 축 판정(업로드 실패 사유), **사용자에게 보이는 거짓 문구: 예**(API 전용 — 미디어 타입의 type/subtype은 대소문자를 구분하지 않는다, RFC 9110 §8.3.1; 브라우저는 소문자로 보냄). 0cfbd03에도 있던 기존 결함(R30 수정이 고친 함수 바로 위의 줄).
- 재현(`probe_mp31_live.log`의 `ct[Multipart/Form-Data; bou]`, `ct[MULTIPART/FORM-DATA; bou]`): 정상 본문 + `Content-Type: Multipart/Form-Data; boundary=…` → web `/api/analyze-upload` 400 「multipart/form-data 업로드가 필요합니다」, web `/api/check` 400 「multipart/form-data 또는 application/json 본문이 필요합니다」, api-serve 400 「multipart/form-data 업로드가 필요합니다」. 같은 본문을 소문자로 보내면 200. 파서 자체는 대소문자와 무관하게 파싱한다(`probe_fd.log` `E_content_types.ct_upper_type`).
- 원인: `_analyze_upload_payload`·`_check_file_payload`·`_compare_payload`(`webapp_api.py:1122`, `:1238`, `:1310`)의 `"multipart/form-data" not in content_type`(대소문자 구분 부분 문자열).
- → `content_type.split(";", 1)[0].strip().lower() == "multipart/form-data"`(그리고 `/api/check`의 JSON 분기도 같은 방식). 회귀 테스트: 등식 표의 boundary 거부 시험에 대문자 type 행(200).

### R31-3 — 비어 있는 `filename*`가 `filename="a.png"`를 이겨, 파일 파트를 보냈는데 「업로드된 파일이 없습니다」
- 심각도 Low, 축 판정(업로드 실패 사유), **사용자에게 보이는 거짓 문구: 예**(API 전용·손으로 만든 헤더 — 브라우저는 `filename*`를 보내지 않음). 0cfbd03에도 있던 동작(`old_vs_new.log` `filename_star_empty`: 두 트리 모두 파일 0개).
- 재현(`probe_mp31_live.log` `filename_star_empty_plus_plain`, web 두 경로·api-serve): `Content-Disposition: form-data; name="files"; filename*=utf-8''; filename="a.png"` + PNG 바이트 → 400 「업로드된 파일이 없습니다」.
- 원인: `_filename`(`form_data.py:117-129`)은 형식만 맞으면 `filename*`의 값을 그대로 돌려주고(빈 문자열 포함), `file_parts`(`:215-217`)는 빈 이름의 파트를 파일이 아닌 것으로 뺀다.
- → `filename*`가 빈 이름으로 복호되면 `filename`으로 넘어가기(RFC 6266 §4.3의 취지: `filename*`를 처리할 수 없으면 `filename`). 둘 다 비면 지금처럼 파일 아님. 회귀 테스트: 등식 표의 이름 축에 이 헤더.

### R31-4 — `filename*=undefined''a.png`(파이썬의 `undefined` 코덱)로 모든 업로드 경로가 500
- 심각도 Low, 축 판정(500 — 결론·서명·증거에는 영향 없음), 사용자에게 보이는 거짓 문구: 아니오(500 문구 「업로드 분석 중 오류가 발생했습니다」/「서버 내부 오류가 발생했습니다」는 사실) → **백로그 후보**. R12-10(`_decode_filename_bytes`) 이후 있던 기존 결함이고 새 파서로 그대로 옮겨졌다(`old_vs_new.log`: 두 트리 모두 `UnicodeError: undefined encoding`).
- 재현(`probe_mp31_live.log` `filename_star_undefined`): web `/api/analyze-upload` 500 「업로드 분석 중 오류가 발생했습니다」, web `/api/check` 500 「서버 내부 오류가 발생했습니다 — 상세는 서버 로그를 확인하십시오」, api-serve 500. 코덱 455개 중 이것 하나뿐(`probe_fd.log` A).
- 원인: `decode_filename_bytes`(`form_data.py:69-76`)가 `(LookupError, UnicodeDecodeError)`만 잡는데 `undefined` 코덱은 `UnicodeError`(그 상위 클래스)를 던진다.
- → `except (LookupError, UnicodeError)`(또는 `ValueError`), 그리고 선언 문자셋은 텍스트 인코딩 화이트리스트(`codecs.lookup(...)._is_text_encoding`)로 제한.

### R31-5 — GUI 빠른 검사에 압축 파일을 넣으면 알림이 「압축 해제 — N개 파일 분석됨」: 열 수 없는(손상·빈) 압축 파일에도 「압축 해제 — 1개 파일 분석됨」, N은 컨테이너 행과 미지원 구성 파일까지 센다
- 심각도 Low, 축 판정(GUI 표시 — 원장 행과 결론은 맞음), **사용자에게 보이는 거짓 문구: 예**(압축을 풀지 않았고 아무 파일도 분석하지 않았는데 「압축 해제 — 1개 파일 분석됨」). 0cfbd03에도 있던 기존 결함(`gui_unk.0cfbd03.log` — 같은 행).
- 재현(`gui/gui_zip_toast.log`, Playwright Chromium in `:gui`, 빠른 검사 `#qc-file`): PNG 바이트를 `corrupt.zip`으로 → 응답 행 `corrupt.zip` 하나(`unknown`), 알림 「압축 해제 — 1개 파일 분석됨」, 원장으로 이동. 빈 zip → 같은 알림. 구성 파일 2개 zip → 「압축 해제 — 3개 파일 분석됨」(구성 파일 2 + 컨테이너 행 1). PNG + `.eml` 구성 zip → 「3개 파일 분석됨」(분석된 구성 파일은 1개, `.eml`은 `unsupported`). 이 경우 `#qc-out`은 비워진다(`gui/gui_unk.c29cbb5.log`: `shown: ""`) — 결과는 원장에만.
- 원인: `runQuickCheck`(`gui.js:2792-2805`)가 `data.mode === 'files'`이면 `toast(\`압축 해제 — ${(data.items || []).length}개 파일 분석됨\`)` — 행 수를 그대로 「분석된 파일 수」로 쓰고, 컨테이너 행의 상태(`unknown`·`expanded`)를 보지 않는다.
- → 구성 파일 행(`member`가 있는 행) 가운데 `status === 'analyzed'`인 수를 세고, 컨테이너 행이 `expanded`가 아니면(열 수 없음·빈 압축 파일) 「압축 파일을 열지 못했습니다 — 원장에서 사유를 보십시오」. 회귀 테스트: `test_gui_*`(문구 생성 함수) 또는 GUI 스모크에 손상 zip 빠른 검사.

## BACKLOG 추가 (후보)
- R31-4 — `filename*=undefined''…`로 세 업로드 경로 500(위). (31차, Low, 판정·견고성)
- 작은 파트가 아주 많은 업로드 — 새 파서는 선형이지만 파트마다 객체와 행을 만든다. 파일 파트 28만 개(16 MiB 본문) → web 200, 행 279,620개(20개 분석 + 나머지 `skipped`), 응답 JSON 81 MB, 5.0초, 서버 최대 RSS 476 MB(api-serve 8.4초, 494 MB). 256 MiB로 늘리면 약 450만 행·1.3 GB JSON·수 GB RSS로 추정. 이름 없는 파트 2,400만 개(64 MiB) → 400 「업로드된 파일이 없습니다」(사실)까지 21초(api 27초), 최대 RSS 약 1.05 GB(`probe_mp31_live.log` `tiny_file_parts_*`·`nameless_parts_*`). 0cfbd03의 `BytesParser`는 훨씬 느렸다(같은 모양 32 MiB에 237초). 로컬 토큰이 있는 클라이언트만 가능. 파트 수 상한(예: 1,000개 넘으면 400)과 상한을 넘은 파일을 행 하나로 묶기를 제안. (31차, Low, 성능·견고성)
- 화자 비교의 하한 — 4샘플(0.0초) WAV와 사인파 → 「코사인 거리 0.1582 (참고 원점수 47/100 …; 샘플 길이 0.0초/1.0초)」, 무음과 무음 → 100/100·거리 0. 한계에 「5초 미만이라 불안정」이 붙고 참고 신호라 결론과는 무관하지만, 텍스트에는 8자 하한을 두었으면서 오디오에는 길이·에너지 하한이 없다(`compare[tiny+sine]`, `compare[silent+silent]`). (31차, Low, 판정·참고 계층)
- 문체 비교의 하한이 `strip()` 뒤 글자 수라 폭 없는 공백(U+200B) 100개나 NUL 100개도 「표본 100자」로 비교된다(원점수 11, 거리 0.31 — `compare[zw+txt]`, `compare[nul_txt+txt]`). (31차, Cosmetic, 판정·참고 계층)
- 따옴표 없는 `filename=` 값 안의 맨 LF(헤더 줄은 CRLF로만 나눔)는 이름에 들어간다: `filename=a.png\nX-Evil: 1` → 행 이름 `a.png\nX-Evil: 1`(`unsupported`, 표시는 이스케이프, 서명에는 업로드 이름 예외 규칙 그대로). 손으로 만든 헤더에서만. (31차, Cosmetic, 판정)
- 참고(지적 아님): 손상 압축 파일 PDF 비교 「한쪽 파일의 텍스트 추출에 실패했습니다.」(0바이트·PNG를 `.pdf`로)는 사실. RFC 2388식 중첩 `multipart/mixed` 파일 파트는 0cfbd03과 같이 「업로드된 파일이 없습니다」(RFC 7578 §4.3이 폐지한 형식). `Content-Transfer-Encoding: base64` 파트를 복호하지 않는 것은 서비스 문서에 적힌 대로이고 sha256도 보낸 바이트의 것이다.

## R30 회귀 종료 확인
- **R30-1(210cf09)**: `message/rfc822` 파트가 세 진입점에서 행이 됨 — 혼자면 200 `unsupported` 행(sha256 = 보낸 바이트), PNG와 함께면 행 2개, PNG 바이트를 `message/rfc822`로 보내도 `analyzed`(`probe_mp_live.log`). 25개(PNG 22 + `.eml` 3) → 행 25개, 상한 사유 「…파일 25개 중 앞의 20개만 분석함」, `summary.total` 25. GUI(`gui/gui_probe.log`, `gui_probe2.log`): `emlonly/` 폴더 올리기 → 200 `unsupported` 행(30차 400), `mixed/` → 행 3개(30차 2개), 빠른 검사 `.eml` → 「분석 못 함 — 미지원 형식」(30차 「검사 실패: 업로드된 파일이 없습니다」), 비교 `a.wav`+`mail.eml` → 「지원되는 쌍이 아닙니다(.wav, .eml)」(30차 「받은 파일 1개」). `explore_mp2.log`: 무작위 310개 × 파트 타입 11종에서 탈락 0·바이트 변화 0. 원래 결함 재현 안 됨.
- **R30-2(210cf09)**: `.mht`(`multipart/related`) 행 sha256 `dc9091cdee65…` = 디스크 파일(30차 `a421b89531bd…`, `gui/gui_probe.log`), PNG를 `multipart/mixed`로 → sha256 `a90cf4fe5067…`(보낸 바이트, `probe_mp_live.log`), `explore_mp3.log` 입력 = 출력.
- **R30-3(210cf09)**: GUI 빠른 검사 `a\b.png` → 행 `a\b.png`(30차 `ab.png`), `KakaoTalk_20240101_123456789\x.png` → 그 이름(`gui/gui_bs.log`); 실제 이름 `KakaoTalk_2024010\1_123456789.png` → 업로드·폴더 모두 「등록된 이름 형식 9개와 일치 없음 — 무정보」(`explore_bs.log`, 30차 업로드는 「일치」). `case.zip\`(브라우저 헤더 `filename="case.zip\"`) → 라이브 API에서 구성 파일 행 + 컨테이너 `expanded`(`probe_mp31_live.log` `zip_trailing_backslash`), GUI는 PNG 바이트라 `unknown`(같은 바이트의 폴더 행과 같음). `%22` → `"`(BL-36 그대로, `gui_names.log`). 폴더 선택의 역슬래시 → `/` 변환은 브라우저 동작(30차 참고 그대로).
- **R30-4(548a168)**: 위 「판정」의 비교 항목 — 0바이트·비오디오·빈 텍스트에 수치 없음, GUI(`gui/gui_probe2.log`의 `compare_wav0`·`compare_png_as_wav`)는 큰 숫자 자리에 「없음」과 「파일 B에서 오디오 특징을 추출하지 못해 …」(30차 「0」·「거리 1」; 거리 칸은 `gui.js:2977`의 「없음 — 측정하지 않음」). 다른 호출자(api-serve `/api/compare`, CLI `compare`(비게이트))는 같은 `compare_files` → `compare_layer` 경로다(라이브로는 web만 시험).
- **R30-5(210cf09)**: boundary 없음·빈 값 → 「Content-Type에 multipart boundary가 없습니다」, 다른 값 → 「본문의 경계가 Content-Type의 boundary와 다릅니다」(web 두 경로·api-serve, `probe_mp_live.log`), 잘림 → 「잘렸습니다」(잘린 위치 약 27,000개 전부). 같은 함수의 남은 구멍은 줄 끝(R31-1)과 미디어 타입 대소문자(R31-2).
- 커밋 본문의 다른 호출자(보고서 `uploads`의 이름, api-serve 경로 검사, 붙여 넣은 텍스트): `probe_srv`·`probe_cli`·`probe_tb`·`probe_upname_report` 결과 정상, 30차와 같음.
- BL-35 처리 확인(`probe_big.log`): web 582 MB·api-serve 594 MB(30차 2461/2449 MB), 0.6/1.0초, 행 `analyzed`·sha256 일치. BL-36: 그대로(재지적 안 함).
- R29-1~R29-6, R28-1~R28-5, R27-1·R27-3·R27-4(BL-19~BL-21), BL-22·BL-23: 그대로(악화 없음).

## 성능 (scan, 픽셀 off, `--no-default-engine`, `--max-files 5000`, `:latest`, `--cpus 4`, 다른 컨테이너 없음)
명령 전체 벽시계 시간(프로세스 시작 포함)의 중앙값. 1차: 전체 말뭉치 3회 교대(`perf/perf.log`·`perf/perf_table.md`), 2차: 이미지·문서·보고서 5회 교대(`perf/perf2.log`·`perf/perf2_table.md`), 3차: 2차의 image2000 workers 4가 +26%로 경계를 넘어 image2000만 9회 교대로 다시 잼(`perf/perf3.log`·`perf/perf3_table.md`). 세 트리(2130892·0cfbd03·c29cbb5)를 같은 컨테이너에서 번갈아 돌림. 말뭉치는 28~30차와 같은 `mkcorp.py`.

| 폴더 | workers | 2130892 | 0cfbd03 (30차) | HEAD c29cbb5 | HEAD / 2130892 | HEAD / 0cfbd03 |
| --- | --- | --- | --- | --- | --- | --- |
| 빈 폴더 | 1 | 0.22 / 0.24 s | 0.20 / 0.24 s | 0.20 / 0.20 s | 잡음 | 잡음 |
| image2000 | 1 | 1.71 / 1.79 / 1.68 s | 1.82 / 1.90 / 1.99 s | 1.88 / 1.90 / 1.92 s | +10% / +6% / **+14%** | +3% / 0% / −4% |
| image2000 | 4 | 2.53 / 2.56 / 2.76 s | 3.12 / 3.15 / 3.30 s | 3.09 / 3.23 / 3.28 s | +22% / +26% / **+19%** | −1% / +3% / −1% |
| wav300 | 1 | 19.64 s | 7.38 s | 7.40 s | −62% | 0% |
| wav300 | 4 | 22.50 s | 3.47 s | 3.50 s | −84% | +1% |
| mp4300 | 1 | 45.60 s | 25.90 s | 26.03 s | −43% | +1% |
| mp4300 | 4 | 28.77 s | 9.28 s | 10.11 s | −65% | +9% (잡음 — 아래) |
| doc600 | 1 | 0.45 / 0.45 s | 0.68 / 0.69 s | 0.69 / 0.69 s | +53% / +53% (예외) | +1% / 0% |
| doc600 | 4 | 0.61 / 0.60 s | 0.92 / 0.94 s | 0.94 / 0.93 s | +54% / +55% (예외) | +2% / −1% |
| html300 (보고서 경로) | 1 | 0.81 / 0.86 s | 0.59 / 0.61 s | 0.59 / 0.60 s | −27% / −30% | 0% / −2% |

(「a / b / c」는 1차 3회 / 2차 5회 / 3차 9회 중앙값.)
- 0cfbd03..c29cbb5의 코드 변경은 업로드 파서와 비교 함수뿐이라 scan 경로의 코드는 같다. HEAD / 0cfbd03은 mp4300 workers 4의 +9%(3회: 9.55/10.11/10.81 s 대 9.20/9.28/9.88 s — 한 회차씩 흔들림)를 빼면 모두 ±4% 안.
- 이미지: 2130892 대비 2차 5회의 workers 4가 +26%였으나 그 HEAD 값에 4.03/4.12 s 두 회가 섞였고(다른 트리도 3.99 s 한 회), 9회 교대 재측정은 +19%(workers 1 +14%). 0cfbd03도 같은 측정에서 +20%(3.30 / 2.76)로 HEAD와 같다 — 28~30차의 +21~23%와 같은 수준이고 이번 변경과 무관. 25% 예산 안으로 판단.
- 오디오·영상: 2130892보다 빠름.
- 문서: +53%/+55%는 §8 예외(BL-23 — PDF C2PA 판독과 TB 문서 층). 0cfbd03 대비 변화 없음.
- 보고서 경로(html300): 2130892보다 27~30% 빠르고 0cfbd03과 같음.
- 업로드 경로(이번 변경): 256 MiB 근처 단일 업로드 web 6.2 → 0.6초, api-serve 8.0 → 1.0초(`probe_big.log`, 30차 대비); 작은 파트 많은 본문도 0cfbd03보다 빠름(위 퍼징 절).
- 성능 Med: 0건.

## 최종 판정
게이트 0 판정: **아니오** — 사용자에게 보이는 거짓 문구 4건(R31-1·R31-2·R31-3·R31-5, 모두 Low, 판정 축). 세 축의 Med 이상은 0건, 성능 Med도 0건(위 표), 게이트 6개(`:latest`×2, `:tech`×2, `:tech2`, `:gui`)와 기록 검사 모두 통과.
수정 목록:
- R31-1 — 줄 끝이 CRLF가 아닌 본문(LF만·CR만·혼합·LF 프리앰블)에 「boundary와 다릅니다」·「잘렸습니다」·「업로드된 파일이 없습니다」 — 210cf09의 회귀(0cfbd03은 200으로 분석).
- R31-2 — `Multipart/Form-Data`(대문자)에 「multipart/form-data 업로드가 필요합니다」(기존 결함).
- R31-3 — 빈 `filename*`가 `filename`을 이겨 「업로드된 파일이 없습니다」(기존 결함).
- R31-5 — GUI 빠른 검사의 압축 파일 알림 「압축 해제 — N개 파일 분석됨」이 열지 못한 압축 파일에도 나오고 N이 컨테이너·미지원 행을 셈(기존 결함).
(R31-1·R31-2·R31-3은 등식 표의 boundary·이름 축에 「줄 끝」·「미디어 타입 대소문자」·「빈 `filename*`」 행을 더하는 것을 함께 제안한다(8.3 축 곱).)
백로그 후보(판정과 별도): R31-4(`undefined` 코덱 500), 작은 파트 수백만 개의 행·메모리 폭증, 화자 비교의 길이·에너지 하한, 문체 비교 하한이 보이지 않는 글자를 셈, 따옴표 없는 이름 안의 맨 LF.
