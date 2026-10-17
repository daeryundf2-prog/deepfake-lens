# 독립 검증 라운드 28 결과 (HEAD 2a08d8e) — 새 형식 두 번째 라운드

검증 대상: 비공개 클론 `scratchpad/r28/repo`(`git clone --no-hardlinks`), 브랜치 phase0 HEAD 2a08d8e(= origin/phase0, 코드 마지막 커밋 3bf3489·e74e2cf 병합), 로컬 main = dad9730. 수정한 것은 없음.
측정 조건: Docker Desktop(10 CPU, 7.75 GiB), 모든 컨테이너 `--cpus 4`(GUI 스모크만 `--cpus 3 --shm-size=1g`), `/repo`는 git 이력이 있는 클론(`.git` 디렉터리). 성능 측정은 다른 컨테이너가 하나도 없을 때(`docker ps` 0개, `perf/containers_start.txt`) 혼자 돌렸음. 게이트는 한 번에 2개(일부 시간대에 탐침 컨테이너 1개 추가)가 함께 돌았음. 탐침 스크립트·로그: `r28-out/probe/`, 성능: `r28-out/perf/`, 게이트 로그: `r28-out/gate-*.log`.

## 게이트 결과

| 게이트 | 이미지 | 결과 | 비고 |
| --- | --- | --- | --- |
| `gate.sh full` 1회차 | `deepfake-lens-gate:latest` | **GATE rc=0** (24분, :tech와 동시) | /venv 2127개 OK(skipped=125), /venv-api 2127개 OK(skipped=93), ruff 통과, mypy 162개 파일 문제 없음, coverage 81%, cli_smoke OK(서브명령 45개/기능 검사 26개), 측정 게이트 10/10, 계약 PIN 일치, stress_shutdown 30회 중 실패 0, qa-main 202개 실패 0(통과 19, 수동 4, 1단계 12, 건너뜀(환경) 1) |
| `gate.sh full` 2회차 | `:latest` | **GATE rc=0** | /venv 2127 OK(skipped=125), /venv-api 2127 OK(skipped=93) — 1회차와 같음(플레이크 없음) |
| `gate.sh full` 1회차 | `deepfake-lens-gate:tech` | **GATE rc=0** (38분) | /venv 2127 OK(skipped=112), /venv-api 2127 OK(skipped=93), coverage 82%, 나머지 단계 :latest와 같음, qa-main 19/0 |
| `gate.sh full` 2회차 | `:tech` | **GATE rc=0** (39분) | /venv 2127 OK(skipped=112), /venv-api 2127 OK(skipped=93), qa-main 19/0 — 1회차와 같음 |
| `gate.sh full` (참고) | `deepfake-lens-gate:tech2`(PerTh 가중치) | **GATE rc=0** (34분) | /venv 2127 OK(skipped=109 — :tech보다 3개 적음: PerTh 가중치 테스트), /venv-api 2127 OK(skipped=93), qa-main 19/0 | HANDOFF 8.1(a)의 대상은 아니지만 TB-2 PerTh 경로는 이 이미지에서만 가중치가 있어 한 번 돌림 |
| `gate.sh gui` | `deepfake-lens-gate:gui` | **GATE rc=0** | 스모크 2개 모두 `GUI smoke PASS`(fixtures, /tmp/adv) |
| 기록 검사 | `:latest` (`gate.sh cmd`) | **통과** | `qa_phase0.py --verify-record`: 「docs/CONFORMANCE.md는 4c7cef59140a에서 생성되었고 그 뒤 다른 변경이 없습니다」 rc=0; `build_traceability_commits.py --check`: 「추적표 최신: 커밋 471개(dad9730..4c7cef5), 표 이후 커밋 1개(재생성 커밋만)」 rc=0 |

## 축별 요약

- **판정**: Med 이상 0건. 거짓 문구 1건(R28-1, Low).
  - TB 층(TB-1~TB-8)이 결론을 만들지 않음: TB 사실 33행 탐침(`probe_tb.py`, :latest와 :tech2)에서 강한 근거 없이 결론이 난 행 0건(`verdict_without_strong_evidence: []`). TB 항목은 모두 neutral/weak이고 예외는 Google AI 라벨(synthetic/moderate, self_declared)과 PerTh 등록 일치(synthetic/moderate)뿐 — 둘 다 단독으로는 판단 불가(`google_credit.png`, `google_made.png`, `synthetic-perth.wav`). Google 라벨 + IPTC DigitalSourceType(강함, 자기 선언)은 조작·생성 근거 있음이며 결정 근거는 DigitalSourceType 쪽 — 라벨 문구 「원본성 결론만 막고 조작·생성 결론은 내리지 않습니다(같은 파일의 디지털 출처 유형 필드는 따로 기록)」와 맞음.
  - 자기 선언 표시: Google AI 라벨, 삼성 SEFT 항목(AI 후보 포함), OS 「Screenshot」 주석, PDF 작성 도구 이력, OOXML 수정 메타데이터 모두 `self_declared: true`. 화면 크기 일치·PDF 증분 저장·텍스트 표식은 구조 사실이라 표시 없음(합의된 규칙 364abfc와 같음). 서명 본문에서 `self_declared`를 지우면 `verify-report` rc=1.
  - 원본성 결론과 TB: 신뢰된 카메라 C2PA 파일을 스크린샷 이름(`Screenshot_20240102-010203.png`)으로 바꾸어도 원본성 근거 있음(중립 사실만 추가)이고 재촬영 한계 문구(CAMERA_C2PA_LIMITATION)와 경로 한계 문구가 붙음. 신뢰된 화면 캡처 매니페스트는 판단 불가.
  - 손상 입력(`probe_malformed.py`, 19개 — SEFT 개수 0xFFFFFFFF·음수 오프셋·길이 초과·깨진 JSON, 잘린 JPEG·DQT, `%%EOF` 20만 개, 잘못된 /ByteRange, 끝나지 않은 /Producer 문자열, PNG 바이트의 `.pdf`, 압축 폭탄 settings.xml(16MB 상한 → 건너뜀), 가짜 zip `.docx`, 태그 문자 50만 개, 잘린 Opus·AMR): 충돌 없음, 결론 0건, 실패는 `failed`(Opus `audio_channel_trace`) 또는 사유 있는 `skipped`; workers 1/4 동일.
  - 기존 판정 회귀: 8178e77 대비 fixtures 72행·적대 픽스처 110행에서 verdict·status 변화 0건(`cmp.latest.log`). 적대 픽스처에 새로 붙은 근거는 「기기 화면 해상도와 같은 크기」(17행)뿐.
  - 진입점 동일성: 같은 venv에서 CLI = web(`_scan_payload`, 라이브 `web` 서버) = api-serve(라이브, `/venv-api` CLI와 비교)의 status·verdict·evidence·coverage가 행마다 같음. workers 1/4는 scan JSON이 바이트 단위로 같음(TB 폴더, :latest·:tech2; 손상 폴더; 증거 폴더 상대/절대 경로).
- **서명**: Med 이상 0건. 탐침(`probe_srv.py`, `probe_cli.py`) 결과:
  - 서명 행 집합 = 작업 행 집합(경로 집합 같음, 행 내용 차이 0). 사건 필드 표식은 JSON 서명 본문에 없음. 호스트 경로·`/tmp/` 없음. 같은 작업으로 두 번 만든 JSON은 `generated_at`·`signature`만 다름.
  - 주입: `items`·`summary`·`analysis_options` 필드 400, `side=defendant` 400, 다른 소유자의 작업 404, 위조 작업 id 404. 업로드 행에 심은 이름은 `excluded_items`(name_source `client`)에만 있고 행·판정은 서명되지 않음.
  - 409 changed_rows: 검사 뒤 파일 수정·삭제 → json·html·pdf·evidence 모두 409, `changed_rows`의 키는 `path`·`reason`뿐(「검사 뒤 내용이 바뀌었습니다」, 「검사 뒤 파일이 없어졌거나 읽을 수 없습니다」). 재시작(`STORE.forget`) 뒤에도 409.
  - 취소된 작업: 보고서 200, `summary.cancelled` true, 1행.
  - R27-2 회귀: html·pdf·evidence·evidence-preview 모두 「검사 옵션(서버가 이 검사 작업을 실행한 옵션)」, 「재분석 옵션」·「다시 분석」 없음.
  - `verify-report`: 정상 rc=0, 변조 rc=1. 라이브 web/api-serve의 JSON 보고서도 rc=0.
- **증거**: Med 이상 0건. 게이트 범위 명령(`scan` w1/w4/상대 경로/`--sign`/`--html-out`, `classify` PDF·PNG, `evidence-statement` JSON·MD·PDF, `verify-report`, `doctor`)과 라이브 `web`·`api-serve`(비동기 검사, scan-status 폴링, json·html·pdf·evidence·evidence-preview 보고서) 전후로 사건 폴더의 sha256·mtime·권한·파일 수가 같음(읽기 전용 파일 포함). 사건 폴더 안 작업 파일 0개, 스크래치 폴더 잔여 0개. QA-IN-1은 qa-main에서 통과.

## 지적사항

### R28-1 — 업로드한 파일의 「파일 이름 형식」 검사가 임시 파일 이름으로 돌아, 형식에 맞는 이름도 「일치 없음」으로 기록됨
- 심각도 Low, 축 판정(행의 coverage·evidence·limitations — 결론에는 영향 없음, 중립·약 사실), **사용자에게 보이는 거짓 문구: 예** → 이번 라운드 수정 대상.
- 재현(`probe_tb.py`, `probe_tb.latest.log`의 `upload_rows`): `KakaoTalk_20240101_123456789.png`, `Screenshot_20240101-123456.png`, `PTT-20240101-WA0001.opus`를 `/api/analyze-upload`(web·api-serve 공통 `_analyze_upload_payload`)로 하나씩 올리면 세 행 모두 `file_name_pattern` ran 「등록된 이름 형식 9개와 일치 없음 — 무정보」. 같은 바이트를 폴더 검사하거나 zip에 넣어 올리면 「이름 형식 일치: 카카오톡에서 저장한 파일 이름 형식(…)」과 「파일 이름 형식: …」 근거, 경로 한계 문구(「메신저·SNS·화면 캡처·통화 경로의 흔적이 관찰되었습니다 — …」)가 붙음. GUI의 상세 「검사 범위」는 이 행에 「실행: … 파일 이름 형식(메신저·스크린샷·녹음)」으로 표시해 사용자가 올린 이름에 검사가 돈 것처럼 보임.
- 원인: 단일 업로드는 `tempfile.NamedTemporaryFile(suffix=suffix)`(`webapp_api.py:1081`, 빠른 검사 `/api/check`의 `_check_file_payload`도 `:1229`)로 `tmpXXXX.png`에 써서 분석하고, `platform_traces._matching_patterns`(`platform_traces.py:265`)는 `path.name`을 본다. 압축 업로드(`_archive_upload_items`)는 원래 이름으로 쓰므로 맞게 나옴. 검사 결과는 사용자의 파일 이름에 대해 사실이 아니다(하지 않은 검사를 「일치 없음」으로 씀).
- → 업로드 행의 파일 이름 검사를 클라이언트가 보낸 이름(`filename`의 base name)으로 돌리거나(압축 업로드처럼 임시 폴더에 원래 이름으로 쓰는 방법), 그럴 수 없으면 `skipped` 「업로드 임시 파일 — 원래 이름으로 검사하지 않음」으로 기록. 원래 결함의 회귀 테스트: 같은 이름·바이트의 폴더 행, 단일 업로드 행, 압축 업로드 행의 `file_name_pattern`·근거·한계가 같음. 축 곱 추가 제안: 「업로드 경로 × 이름에 의존하는 검사」를 `test_axis_product`(또는 QA-OUT-4)의 축으로.

### R28-2 — BL-23의 문서 성능 수치가 작업자 1에서 실제보다 작게 적혀 있음
- 심각도 Low, 축 없음(성능 기록), 거짓 문구: 화면 문구 아님(BACKLOG 기록) → 백로그 후보(BL-23 수치 갱신).
- 측정(아래 표, 5회 중앙값, 단독 실행): doc600 workers 1 +46%(0.46→0.67 s), workers 4 +53%(0.60→0.92 s). BL-23은 +33%/+53%. 3622dc6(TB 병합 전) 대비 HEAD는 +14%/+14%로, 늘어난 비용은 techniques-2026의 PDF C2PA 판독(3622dc6에 이미 +28%/+35%)과 TB-1/TB-6 문서 층이다 — 둘 다 「새로 추가한 검사」라 §8 예외에 해당하고 기존 경로 회귀는 아님. 다만 예외의 조건(형식별 측정을 기록)은 TB 층 몫(+14%)을 따로 적지 않았다.

### R28-3 — HANDOFF에 `:tech2` 게이트 이미지가 없음
- 심각도 Low, 축 없음(문서), 거짓 문구 아님 → 백로그 후보.
- `scripts/gate/Dockerfile.tech2`(TB-2 PerTh 가중치)는 레포에 있고 techniques-2026b 수정 세션은 `:tech2`로도 게이트를 돌렸으나, HANDOFF 4장(이미지 목록)·5장·8.1(a)·8.3에는 `:latest`·`:tech`뿐이다. `:tech`에는 PerTh 가중치가 없어 그 디코더 경로는 8.1(a) 게이트에서 실행되지 않는다(이번 라운드 `:tech2`에서 `synthetic-perth.wav` 검출 1.000·moderate, 판단 불가 확인).

### R28-4 — Opus 인코더 문자열 일치 항목에 자기 선언 표시가 없음
- 심각도 Low(결론 영향 없음, 중립·약), 축 판정, 거짓 문구 아님(상세에 「문자열은 누구나 쓸 수 있습니다」) → 백로그 후보.
- `platform_traces.audio_channel_layers`의 「메신저 음성 인코더 문자열과 일치」는 OpusTags에 서명 없이 적힌 문자열인데 `self_declared`가 없다. 364abfc가 정한 「서명 없는 TB 기기·서비스 라벨은 self_declared」와 어긋남(파일 이름 형식은 라벨이 아니라 이름이라 별개).

### R28-5 — 비동기 검사의 상태가 `done`이 된 뒤 작업 파일이 잠깐 늦게 써짐
- 심각도 Cosmetic, 축 서명(작업 보관), 거짓 문구 아님 → 백로그 후보(관찰).
- `JobStore.finish`는 메모리의 상태를 `done`으로 바꾼 뒤 잠금 밖에서 파일을 쓴다. `/api/scan-status`가 `done`을 돌려준 직후 작업 파일이 아직 없는 순간이 탐침에서 관찰됨(`probe_srv.py`가 파일을 기다리도록 고쳐야 했음). 보고서는 메모리의 작업으로 만들므로 결과는 같고, 그 짧은 사이에 서버가 재시작될 때만 작업이 사라진다.

## BACKLOG 추가 (후보)
- R28-2 — BL-23 문서 성능 수치 갱신(작업자 1 +46%; 3622dc6 대비 TB 문서 층 +14%를 형식별 비용으로 기록) (28차, Low, 성능)
- R28-3 — HANDOFF 4·5·8장에 `:tech2`(PerTh 가중치) 이미지와 그 게이트 역할을 적거나, 8.1(a) 대상에 넣을지 결정 (28차, Low, 문서)
- R28-4 — Opus 인코더 문자열 일치 항목에 `self_declared` (28차, Low, 판정)
- R28-5 — scan-status `done`과 작업 파일 저장 사이의 짧은 틈 (28차, Cosmetic, 서명)
- 참고(지적 아님): TB-8의 SD 2 등록 메시지는 32비트로, 정확 일치가 SDXL(48비트)과 같은 강한 합성 근거다. 레지스트리가 스스로 「무작위 이미지에서 2 × 2^-32」라고 적었다 — BL-22(SDXL 강도 재검토)에 FLUX.1(46비트)·SD 1(136비트)·SD 2(32비트)를 함께 넣을 것.
- 참고(지적 아님): 텍스트·문서 행의 증거설명서 입증취지는 보통 강도 자기 선언(Google AI 라벨)을 「재현 가능한 기록 근거:」 목록에 두면서 「결론을 뒷받침할 재현 가능한 기록 근거가 없어」라고 쓴다. 「결론을 뒷받침할」이 한정하므로 거짓은 아님.

## R27 회귀 종료 확인
- **R27-2(0fc5c71)**: 라벨이 HTML·forensic PDF·증거설명서 PDF·GUI 미리보기(`analysis_options_line`) 모두 「검사 옵션(서버가 이 검사 작업을 실행한 옵션)」. 패키지에 「재분석 옵션」 없음. 남은 「재분석」·「다시 분석」 문구는 업로드 행(「재분석·서명하지 않습니다」), GUI의 「파일을 다시 분석하지 않습니다」, 비게이트 `feedback` 도움말뿐 — 모두 사실. json-contract 문서의 옛 설명은 BL-20 그대로(악화 없음).
- **R27-5(3622dc6, 4420e63, 96b24f4)**: 커밋 본문의 다른 호출자(신뢰 번들 캐시, 스레드별 SDK Context, 의존성 부재 기억, cv2 위치 확인, 표 캐시, 접두부·UserComment·PNG 픽셀 넘겨받기, `to_json` 필드 생략, `display_name`·오류 번역 메모)를 회귀 탐침: 운영자 신뢰 앵커 파일을 한 프로세스 안에서 다른 CA로 바꾸면 원본성 → 판단 불가, 되돌리면 원본성, 환경 변수를 다른 경로로 바꾸면 판단 불가, 없애면 판단 불가(`probe_cache.log`). workers 1/4·진입점 사이 바이트 동일(위). 같은 바이트의 서로 다른 이름 파일(`KakaoTalk_…`, `Screenshot_…`, NFD 한글 스크린샷 이름)이 같은 검사에서 각자의 이름 형식 행을 받음(캐시 키 `name:` 토큰). 성능은 아래.
- **techniques-2026b(42b0ded)**: 판정 축 탐침과 같음. NFC/NFD 이름(폴더·zip 구성원) 모두 macOS 한국어 스크린샷 형식에 일치.
- **성능 예외 규칙(c4e00c4)**: 아래 성능 절에서 적용.
- **gui-redesign(e74e2cf)**: scan-status의 `done`·`total`·`current`·`recent`·`elapsed_seconds`는 메모리에만 있고 작업 파일(0600, HMAC 있음)·서명 본문에 없음(`job_file_has_progress: false`, `signed_has_progress_keys: false`). 다른 소유자의 scan-status 404. evidence-preview: 같은 요청 검증(추가 필드 400, 모르는 서증 400, 위조 작업·다른 소유자 404), 호스트 경로·`/tmp/` 없음, 임시 파일을 만들지 않음, 변경 감지 없음(파일이 바뀐 뒤 200 — 문서화된 설계, 내보내기는 409). `/api/stats`의 `default_folder`·`pdf_available`는 서버 자신의 값. 보고서 흐름은 여전히 작업 행만 서명. GUI 스모크 통과.
- R27-1·R27-3·R27-4는 BL-19~BL-21 그대로(악화 없음).

## 성능 (scan, 픽셀 off, 기본 엔진 켬, `--max-files 5000`, `:latest`, `--cpus 4`, 다른 컨테이너 없음)
명령 전체 벽시계 시간(프로세스 시작 포함), 3회(1차 측정) / 5회(2차 측정) 교대 실행의 중앙값. 원자료: `perf/perf.log`(2130892·8178e77·HEAD, 3회), `perf/perf2.log`(2130892·3622dc6·HEAD, 5회). 말뭉치: 64×64 PNG 2000개(1/3에 A1111 parameters), 1초 16 kHz WAV 300개, 1초 64×64 H.264+AAC MP4 300개, PDF 300개 + DOCX 300개, html300(PNG 300개 scan + `--html-out`), 빈 폴더.

| 폴더 | workers | 2130892 | 8178e77 (27차) | 3622dc6 (TB 병합 전) | HEAD 2a08d8e | HEAD / 2130892 |
| --- | --- | --- | --- | --- | --- | --- |
| 빈 폴더 | 1 | 0.22 / 0.23 s | 0.20 s | 0.20 s | 0.21 / 0.21 s | 같음 |
| image2000 | 1 | 1.66 / 1.62 s | 3.06 s | 1.84 s | 1.80 / 1.76 s | **+8~9%** |
| image2000 | 4 | 2.45 / 2.44 s | 4.34 s | 2.96 s | 3.05 / 3.01 s | **+23~24%** |
| wav300 | 1 | 18.98 s | 7.30 s | — | 7.21 s | −62% |
| wav300 | 4 | 20.20 s | 3.18 s | — | 3.33 s | −84% |
| mp4300 | 1 | 45.33 s | 25.44 s | — | 25.46 s | −44% |
| mp4300 | 4 | 27.96 s | 9.25 s | — | 9.54 s | −66% |
| doc600 | 1 | 0.47 / 0.46 s | 0.85 s | 0.59 s | 0.69 / 0.67 s | +46~47% (예외) |
| doc600 | 4 | 0.60 / 0.60 s | 1.08 s | 0.81 s | 0.90 / 0.92 s | +50~53% (예외) |
| html300 (보고서 경로) | 1 | 0.80 / 0.83 s | 1.89 s | 0.58 s | 0.56 / 0.56 s | −30~33% |

- R27-5 해소: image2000은 27차의 +99%/+67%에서 +8%/+23%로 25% 예산 안(벽시계 기준). 시작 비용(빈 폴더 약 0.21 s)을 빼면 workers 4가 약 +27%로 경계선이다 — 그중 TB 층 몫은 3622dc6 대비 +2%(workers 4)·−4%(workers 1)로 잡음 범위이고, 나머지는 3622dc6에서 이미 받아들인 기존 경로 비용이다. HANDOFF의 기준(벽시계)으로는 Med 아님.
- 오디오·영상: 2130892보다 빠름(2130892는 BLAS 스레드 과다로 CPU 시간이 벽시계의 4배), 8178e77 대비 ±5% 안.
- 보고서 경로(html300): 27차 대비 −70%, 2130892보다도 빠름.
- 문서: +46%/+53%는 §8 예외(새 검사 — techniques-2026의 PDF C2PA 판독과 TB-1/TB-6 문서 층). BL-23의 작업자 1 수치(+33%)는 실제보다 작다(R28-2).
- 2130892의 wav300 첫 실행은 numba 캐시 준비로 24 s(warmup, 표에서 제외).

## 최종 판정
게이트 0 판정: **아니오** — 사용자에게 보이는 거짓 문구 1건(R28-1, Low, 판정 축). 세 축의 Med 이상은 0건, 성능 Med도 0건.
수정 목록: R28-1(단일 업로드 행의 「파일 이름 형식」 검사가 임시 파일 이름으로 돌아 「일치 없음」으로 기록됨).
백로그 후보(판정과 별도): R28-2, R28-3, R28-4, R28-5.
