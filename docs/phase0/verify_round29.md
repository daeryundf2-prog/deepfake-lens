# 독립 검증 라운드 29 결과 (HEAD 6a3ad1f) — 새 형식 세 번째 라운드

검증 대상: 비공개 클론 `scratchpad/r29/repo`(`git clone --no-hardlinks`), 브랜치 phase0 HEAD 6a3ad1f(= origin/phase0 `daeryundf2-prog/deepfake-lens`, 코드 변경은 cb7283d R28-1 수정 하나, 나머지는 문서 c3ff620·bd798fb와 기록 6a3ad1f), 로컬 main = dad9730. 수정한 것은 없음.
측정 조건: Docker Desktop(10 CPU, 7.75 GiB), 모든 컨테이너 `--cpus 4`(GUI 스모크만 `--cpus 3 --shm-size=1g`, 탐침 컨테이너는 `--cpus 2`), `/repo`는 git 이력이 있는 클론(`.git` 디렉터리). 게이트는 두 줄로 동시에 돌렸음(줄 A: `:latest` 1·2회차 → `:tech2`, 줄 B: `:tech` 1·2회차 → `:gui`; 일부 시간대에 탐침 컨테이너 1개 추가). 성능 측정은 게이트·탐침이 모두 끝나고 다른 컨테이너가 하나도 없을 때(`docker ps` 0개, `perf/containers_start.txt`) 혼자 돌렸음. 탐침 스크립트·로그: `r29-out/probe/`, 성능: `r29-out/perf/`, 게이트 로그: `r29-out/gate-*.log`.

## 게이트 결과

| 게이트 | 이미지 | 결과 | 비고 |
| --- | --- | --- | --- |
| `gate.sh full` 1회차 | `deepfake-lens-gate:latest` | **GATE rc=0** (25분, :tech와 동시) | /venv 2129개 OK(skipped=125), /venv-api 2129개 OK(skipped=93), ruff 통과, mypy 162개 파일 문제 없음, coverage 81%, cli_smoke OK(서브명령 45개/기능 검사 26개), 측정 게이트 10/10, 계약 PIN 일치, stress_shutdown 30회 중 실패 0, qa-main 202개 실패 0(통과 19, 수동 4, 1단계 12, 건너뜀(환경) 1) |
| `gate.sh full` 2회차 | `:latest` | **GATE rc=0** (23분) | /venv 2129 OK(skipped=125), /venv-api 2129 OK(skipped=93) — 1회차와 같음(플레이크 없음) |
| `gate.sh full` 1회차 | `deepfake-lens-gate:tech` | **GATE rc=0** (40분) | /venv 2129 OK(skipped=112), /venv-api 2129 OK(skipped=93), coverage 82%, 나머지 단계 :latest와 같음, qa-main 19/0 |
| `gate.sh full` 2회차 | `:tech` | **GATE rc=0** (42분) | /venv 2129 OK(skipped=112), /venv-api 2129 OK(skipped=93), qa-main 19/0 — 1회차와 같음 |
| `gate.sh full` | `deepfake-lens-gate:tech2`(PerTh 가중치, 8.1(a) 필수 — R28-3 반영) | **GATE rc=0** (43분) | /venv 2129 OK(skipped=109 — :tech보다 3개 적음: PerTh 가중치 테스트), /venv-api 2129 OK(skipped=93), coverage 82%, qa-main 19/0 |
| `gate.sh gui` | `deepfake-lens-gate:gui` | **GATE rc=0** | 스모크 2개 모두 `GUI smoke PASS`(fixtures, /tmp/adv) |
| 기록 검사 | `:latest` (`gate.sh cmd`) | **통과** | `qa_phase0.py --verify-record`: 「docs/CONFORMANCE.md는 bd798fb0aaab에서 생성되었고 그 뒤 다른 변경이 없습니다」 rc=0(이번에는 `PIPESTATUS`로 파이프 앞 명령의 종료 코드를 읽음); `build_traceability_commits.py --check`: 「추적표 최신: 커밋 475개(dad9730..bd798fb), 표 이후 커밋 1개(재생성 커밋만)」 rc=0 |

테스트 수 2127 → 2129는 cb7283d의 `test_axis_product.UploadPathNameDependentCheckTest` 2개. 이 두 테스트를 2a08d8e 트리에 넣어 돌리면 실패(failures=2, errors=2), 6a3ad1f에서는 OK — 커밋 본문의 「Both fail on 2a08d8e」와 맞음(`probe/old_test.log`).

## 축별 요약

- **판정**: Med 이상 0건. 거짓 문구 1건(R29-1, Low).
  - R28-1 회귀 탐침(`probe_upload.py`, 41가지 이름 × 바이트, `probe_upload.log`): 단일 업로드 `/api/analyze-upload`(web 함수)·`/api/check`(web)·api-serve `/api/analyze-upload`(TestClient)의 `file_name_pattern` coverage·이름 근거·경로 한계가 세 경로 모두 같고, 같은 바이트를 그 이름(구분자가 있으면 base name)으로 둔 폴더를 web `_scan_payload`로 검사한 행과 `result` 전체가 같음(비교한 34건 모두 `folder_eq_upload_result: true`). 시험한 이름: 등록 형식 6종(KakaoTalk, Android 스크린샷, WhatsApp PTT `.opus`, Android 화면 녹화 `.mp4`, macOS 한국어 스크린샷 NFC/NFD), `../`·`../../../../etc/`·`/etc/` 접두, `C:\Users\me\…`(filename*로 보낸 역슬래시), `a\b.png`, 끝 `/`, `.`·`..`·`/`, NUL, 300자, 정확히 255바이트, 한글 90자(270바이트), `CON.png`·`aux`, `x.png.ivy.json`·`x.png.model.json`(사이드카 이름), `upload.png`(대체 이름과 같은 이름), `dflens-up-abc.png`, `t:::c.png`, 줄바꿈, 끝 공백, 대문자 확장자, 30자 확장자, 비UTF-8 바이트(surrogate escape), 내용과 다른 확장자(PNG를 `.jpg`·`.mp4`·`.txt`로, JPEG를 `.png`로), 잘린 PNG·MP4, 0바이트, 깨진 Opus.
    - 만들 수 없는 이름(NUL, 300자, 270바이트)은 세 경로 모두 `skipped` 「업로드 파일의 원래 이름을 이 시스템에서 파일 이름으로 쓸 수 없어 원래 이름으로 검사하지 않음」, 이름 근거·경로 한계 없음, 페이로드 최상위(`coverage`·`summary`)에도 이름 검사가 돌았다는 표시 없음(`probe_standin.log`). `.`·`..`·`/`는 대체 이름 `upload`(확장자 없음)로 `unsupported` — 확장자가 없는 이름의 폴더 행과 같은 처리.
    - 행의 `name`·`path`·`display_name`·`path_b64`(복호)는 모두 클라이언트 이름 그대로(`t:::c.png`는 `t\:\:\:c.png` 이스케이프). 페이로드 어디에도 `dflens-up-`(클라이언트가 그 이름을 보낸 경우 제외)·`/tmp/`·`deepfake-lens-native`·작업 폴더 경로 없음. sha256은 모두 보낸 바이트의 것.
    - 정리: `analyze_path`가 예외를 내도 `dflens-up-*` 폴더는 지워짐(예외 시점의 경로 부모가 뒤에 없음), 실패 행은 「RuntimeError: 라이브러리 오류(RuntimeError) — 상세는 로그 참조」(경로 없음). 41건·24스레드 뒤 세션 폴더에 `dflens-up-*` 0개(남은 것은 기존 `numba-*` — R22-9 설계).
    - 동시성: 같은 이름·다른 바이트를 24스레드(함수)·12요청(라이브 web, 라이브 api-serve)·8요청(라이브 web `/api/check`)으로 동시에 올려도 행마다 자기 바이트의 sha256, 이름 검사 `ran`(일치), 누출 없음. 한 요청 안의 같은 이름 두 파일도 각자의 sha256. 서버 종료 뒤 `TMPDIR` 아래 잔여 0개(`probe_upload_live.log`).
  - TB 층이 결론을 만들지 않음: `probe_tb.py`(:latest, :tech2)에서 강한 근거 없이 결론이 난 행 0건(`verdict_without_strong_evidence: []`). :tech2에서 `synthetic-perth.wav`는 「등록된 생성기 워터마크 일치(PerTh 1비트 존재 표식 — 결론 근거 아님)」 synthetic/moderate + 검출 신뢰도 1.00, 판단 불가; `synthetic-clean.wav`는 0.004 「검출 안 됨 — 무정보」; workers 1/4 scan JSON 바이트 동일(`probe_tech2_perth.log`).
  - 손상 입력(`probe_malformed.py`, 19개): 28차와 같음 — 충돌 없음, 결론 0건, 실패는 `failed` 또는 사유 있는 `skipped`, workers 1/4 동일.
  - 기존 판정 회귀: 2a08d8e 대비 fixtures 72행·적대 픽스처 110행에서 verdict·status·근거 제목 변화 0건(`cmp.latest.log`).
  - 진입점 동일성: `classify --format json` = `scan` 행(TB 폴더 26개 파일, /venv와 /venv-api 각각, verdict·evidence(자기 선언 표시 포함)·coverage·limitations 차이 0, `probe_classify.log`). 같은 venv에서 CLI = web(라이브) = api-serve(라이브, `/venv-api` CLI와 비교)의 행이 같음(`probe_cli.latest.log`). workers 1/4는 scan JSON이 바이트 단위로 같음(TB 폴더 :latest·:tech2, 손상 폴더, 증거 폴더 상대/절대 경로, perth 폴더). `probe_tb`의 `cli_vs_web_diff`에 나오는 PDF 3건은 28차와 같은 탐침 환경 차이(웹 함수 쪽에만 `/venv-api`의 pymupdf가 보임)로, 같은 venv 비교에서는 0건.
- **서명**: Med 이상 0건. `probe_srv.py` 결과 28차와 같음 — 서명 행 집합 = 작업 행 집합, 사건 필드·호스트 경로·`/tmp/` 없음, 주입 필드 400·`side=defendant` 400·다른 소유자 404·위조 작업 404, 업로드 행 이름은 `excluded_items`(name_source `client`)에만, 검사 뒤 수정·삭제 → json·html·pdf·evidence 409(`changed_rows` 키 `path`·`reason`뿐), 재시작 뒤에도 409, 취소된 작업 보고서 200·`summary.cancelled` true, `verify-report` 정상 rc=0·`self_declared` 변조 rc=1, 라이브 web/api-serve JSON 보고서 rc=0. R28-1은 업로드 행만 바꾸며 업로드 행은 서명되지 않음(그대로).
- **증거**: Med 이상 0건. 게이트 범위 명령(`scan` w1/w4/상대 경로/`--sign`/`--html-out`, `classify` PDF·PNG, `evidence-statement` JSON·MD·PDF, `verify-report`, `doctor`)과 라이브 `web`·`api-serve`(비동기 검사, scan-status, json·html·pdf·evidence·evidence-preview) 전후로 사건 폴더의 sha256·mtime·권한·파일 수가 같음(읽기 전용 파일 포함). 업로드 탐침에서 서버 읽기 폴더(`/tmp/r29ul`)의 파일은 `p.png` 하나 그대로. QA-IN-1은 qa-main에서 통과.

## 지적사항

### R29-1 — GUI 빠른 검사(`/api/check`)에 0바이트 파일을 올리면 「업로드된 파일이 없습니다」
- 심각도 Low, 축 판정(실패 사유 — 결론에는 영향 없음), **사용자에게 보이는 거짓 문구: 예**(HANDOFF 6장의 「실제와 다른 실패 사유」) → 이번 라운드 수정 대상.
- 재현(`probe_upload_live.py`, `probe_upload_live.log`의 `zero/api/check`): 라이브 `web`에 `Content-Disposition: form-data; name="file"; filename*=UTF-8''KakaoTalk_20240101_123456789.png`, 본문 0바이트로 `POST /api/check` → 400 `{"error": "업로드된 파일이 없습니다"}`. GUI는 빠른 검사의 파일 선택(`gui.js:2766-2786`, `apiJson('/api/check')`)에서 이 문구를 「오류: 업로드된 파일이 없습니다」로 보인다. 파일은 올라갔다. 같은 요청을 `/api/analyze-upload`(web·api-serve)로 보내면 200, 행 `analyzed`·판단 불가에 이름 검사 `ran`(일치)이고, 같은 0바이트 파일을 폴더 검사해도 `analyzed` 행이 나온다(`probe_upload.log`의 `zero_png`).
- 원인: `_check_file_payload`(`webapp_api.py:1255-1258`, 조건은 `:1256`)가 첫 파트를 `_part_filename(p) and p.get_payload(decode=True)`로 고르므로 내용이 빈 파트(`b""`)는 「파일 없음」이 된다. `/api/analyze-upload`는 `payload is None`만 건너뛴다(`:1123`). 2a08d8e 이전부터 있던 동작이고 cb7283d가 만든 것은 아니지만, 28차가 막은 「업로드 경로마다 같은 파일에 다른 결과」의 같은 부류다.
- → 파트 선택을 `_part_filename(p) and _part_bytes(p) is not None`로 바꿔 0바이트 파일도 `/api/analyze-upload`·폴더 검사와 같은 행으로 분석하거나, 분석하지 않을 거라면 사실대로 「빈 파일(0바이트) — 빠른 검사로 분석하지 않음」. 회귀 테스트: 0바이트 파일의 `/api/check` 행 = `/api/analyze-upload` 행 = 폴더 검사 행(`test_axis_product.UploadPathNameDependentCheckTest`에 「0바이트」 축을 더하는 것을 제안 — 8.3의 축 곱 「업로드 경로 × 내용 크기 0」).

### R29-2 — 이 시스템에서 파일 이름으로 쓸 수 없는 이름의 압축 파일 업로드는 통째로 실패하고, web `/api/check`에서는 500
- 심각도 Low, 축 판정(업로드 경로), 거짓 문구 아님 → 백로그 후보.
- 재현(`probe_upload_live.log`의 `arc[…]`): 구성원 `in/KakaoTalk_20240101_123456789.png` 하나인 zip을 이름 `"A"*300 + ".zip"`, `"가"*90 + ".zip"`(270바이트 — Windows NTFS에서는 쓸 수 있는 이름), `"nul\x00x.zip"`로 올리면 `/api/analyze-upload`(web·api-serve)는 행 하나 `failed` 「OSError: [Errno 36] 경로가 너무 깁니다: <이름>」 또는 「ValueError: 라이브러리 오류(ValueError) — 상세는 로그 참조」, web `/api/check`는 500 「서버 내부 오류가 발생했습니다 — 상세는 서버 로그를 확인하십시오」. 구성원은 분석되지 않는다. 같은 이름의 단일 파일은 R28-1 이후 대체 이름으로 분석되고 이름 검사만 `skipped`다.
- 원인: `_archive_upload_items`(`webapp_api.py:1012-1016`)는 base name으로만 쓰고, cb7283d의 대체 이름 처리는 `_upload_copy`에만 있다. `_check_file_payload`의 압축 분기(`:1268-1269`)에는 예외 처리가 없다. 문구는 사실이라 거짓 문구는 아님(NUL 사례의 「라이브러리 오류」는 정확하지 않지만 「상세는 로그 참조」).
- → 압축 업로드도 `_upload_copy`처럼 base name을 먼저, 실패하면 `upload.zip`으로 쓰고 컨테이너 행 이름은 클라이언트 이름; `/api/check` 압축 분기의 예외를 실패 행으로.

## BACKLOG 추가 (후보)
- R29-2 — 쓸 수 없는 이름의 압축 업로드: 행 전체 실패(`/api/analyze-upload`)·500(web `/api/check`), 단일 파일의 대체 이름 처리와 다름 (29차, Low, 판정)
- 참고(지적 아님): 28차 문서의 성능 절은 조건을 「기본 엔진 켬」으로 적었으나 탐침 `run1.py`는 `--no-default-engine`으로 돌렸다(28차·29차 같음 — 아래 표의 조건은 실제 플래그로 적음). 제품 문구가 아니라 검증 기록의 정정.
- 참고(지적 아님): 역슬래시가 든 클라이언트 이름(`C:\Users\me\KakaoTalk_….png`)은 단일·압축 업로드 모두 마지막 `\` 뒤를 이름으로 검사해 「카카오톡」 형식에 일치한다. Linux 폴더에 그 이름 그대로 있는 파일은 일치하지 않는다. 업로드 이름은 클라이언트 OS의 경로로 보는 것이 맞아 보이며, 두 업로드 경로가 같으므로 지적하지 않음.

## R28 회귀 종료 확인
- **R28-1(cb7283d)**: 위 판정 축의 회귀 탐침 — 커밋 본문의 「Other callers checked」 목록(web/api-serve `/api/analyze-upload`, web `/api/check` 파일 업로드와 그 forensic 층, zip 업로드(기준), 붙여 넣은 텍스트 경로, api-serve `file_path` 경로·batch·classify·evidence-statement, `/api/compare`, `/api/report`·evidence·evidence-preview)에 더해 경로 순회·역슬래시·NUL·긴 이름·NFC/NFD·Windows 예약 이름·사이드카와 겹치는 이름·대체 이름과 같은 이름·내용과 다른 확장자·손상·0바이트·예외 시 정리·같은 이름 동시 업로드·라이브 서버 HTTP 경로를 확인. 원래 결함(세 이름이 「일치 없음」)은 재현되지 않음. 놓친 호출자: 압축 업로드의 만들 수 없는 이름(R29-2, 커밋 범위 밖의 기존 경로). 0바이트 `/api/check`(R29-1)는 같은 함수의 다른 기존 분기.
- **R28-2(BL-23)**: 29차 측정도 doc600 +41%/+55%(작업자 1/4, 5회 중앙값)로 BL-23의 +46%/+53%와 잡음 범위에서 같음. 2a08d8e 대비 −3%/+2%.
- **R28-3**: HANDOFF 4·5·8.1(a)·8.3에 `:tech2`가 필수 게이트 이미지로 들어감 — 이번 라운드 그대로 돌림(통과).
- **R28-4(BL-33)·R28-5(BL-34)**: 백로그 그대로(악화 없음 — `probe_srv`는 여전히 작업 파일을 기다려야 함).
- R27-1·R27-3·R27-4(BL-19~BL-21), BL-22: 그대로.

## 성능 (scan, 픽셀 off, `--no-default-engine`, `--max-files 5000`, `:latest`, `--cpus 4`, 다른 컨테이너 없음)
명령 전체 벽시계 시간(프로세스 시작 포함), 3회(1차, 전체 말뭉치) / 5회(2차, 이미지·문서·보고서) 교대 실행의 중앙값. 원자료: `perf/perf.log`(2130892·2a08d8e·HEAD, 3회), `perf/perf2.log`(같은 세 트리, 5회). 말뭉치는 28차와 같은 `mkcorp.py`: 64×64 PNG 2000개(1/3에 A1111 parameters), 1초 16 kHz WAV 300개, 1초 64×64 H.264+AAC MP4 300개, PDF 300개 + DOCX 300개, html300(PNG 300개 scan + `--html-out`), 빈 폴더.

| 폴더 | workers | 2130892 | 2a08d8e (28차) | HEAD 6a3ad1f | HEAD / 2130892 | HEAD / 2a08d8e |
| --- | --- | --- | --- | --- | --- | --- |
| 빈 폴더 | 1 | 0.22 / 0.21 s | 0.19 / 0.21 s | 0.23 / 0.23 s | +5~10% (0.01~0.02 s) | 잡음 |
| image2000 | 1 | 1.66 / 1.63 s | 1.82 / 1.76 s | 1.88 / 1.75 s | +13% / **+7%** | +3% / −1% |
| image2000 | 4 | 2.49 / 2.45 s | 3.03 / 3.01 s | 3.20 / 3.02 s | +29% / **+23%** | +6% / 0% |
| wav300 | 1 | 18.88 s | 7.25 s | 7.34 s | −61% | +1% |
| wav300 | 4 | 20.58 s | 3.55 s | 3.36 s | −84% | −5% |
| mp4300 | 1 | 45.30 s | 25.39 s | 25.16 s | −44% | −1% |
| mp4300 | 4 | 28.62 s | 10.35 s | 12.13 s | −58% | +17% (잡음) |
| doc600 | 1 | 0.46 / 0.46 s | 0.69 / 0.67 s | 0.74 / 0.65 s | +61% / +41% (예외) | +7% / −3% |
| doc600 | 4 | 0.59 / 0.60 s | 0.93 / 0.91 s | 0.90 / 0.93 s | +53% / +55% (예외) | −3% / +2% |
| html300 (보고서 경로) | 1 | 0.82 / 0.81 s | 0.58 / 0.56 s | 0.57 / 0.57 s | −30% | −2% / +2% |

(「a / b」는 1차 3회 / 2차 5회 중앙값.)
- 2a08d8e..6a3ad1f의 코드 변경은 업로드 함수(`webapp_api._upload_copy`)뿐이라 scan 경로는 같다. 1차 측정의 2회차가 트리 셋 모두에서 느렸고(예: image2000 w4 4.09 s, mp4300 w4 15.36 s), 1차의 image2000 w4 +29%와 mp4300 w4 +17%는 그 잡음이다 — 5회 측정에서 image2000은 +7%/+23%(28차 +8~9%/+23~24%와 같음)로 25% 예산 안(벽시계 기준). 시작 비용을 뺀 workers 4 경계선 상황은 28차 설명 그대로.
- 오디오·영상: 2130892보다 빠름, 2a08d8e 대비 ±5% 안(mp4300 w4 제외 — 위 잡음, 3회 값 12.13/13.79/10.96 vs 10.35/15.36/9.65).
- 문서: +41~61%/+53~55%는 §8 예외(BL-23 — 새 PDF C2PA 판독과 TB 문서 층). 2a08d8e 대비 변화 없음.
- 보고서 경로(html300): 2130892보다 30% 빠르고 2a08d8e와 같음.

## 최종 판정
게이트 0 판정: **아니오** — 사용자에게 보이는 거짓 문구 1건(R29-1, Low, 판정 축). 세 축의 Med 이상은 0건, 성능 Med도 0건, 게이트 6개(`:latest`×2, `:tech`×2, `:tech2`, `:gui`)와 기록 검사 모두 통과.
수정 목록: R29-1(GUI 빠른 검사 `/api/check`가 0바이트 파일을 「업로드된 파일이 없습니다」로 거부 — 다른 업로드 경로·폴더 검사는 분석함).
백로그 후보(판정과 별도): R29-2.
