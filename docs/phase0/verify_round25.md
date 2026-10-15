# 독립 검증 라운드 25 결과 (HEAD d2239fe)

검증자는 0단계 구현에 참여하지 않았다. 사본은 `scratchpad/r25/repo`(`git clone`, 로컬 `main`=dad9730)이고 원본 저장소 세 곳은 건드리지 않았다. 비교 트리는 `git archive`로 푼 fbcb53c(직전 검증 HEAD)와 2130892다. 기준 환경은 Docker 이미지 `deepfake-lens-gate:latest`(8f13a610a203, aarch64, Python 3.13, ffmpeg 7.1, `--cpus 4`)와 `deepfake-lens-gate:gui`(c204cd6603ae)다. 탐침은 상대 경로 입력, 매우 긴 경로·이상한 이름, 메인 환경(`/venv`, librosa 있음 — 또는 `/venv`+`/venv-api`를 함께 보는 qa-main식 임시 venv)을 항상 포함했다. HANDOFF §7의 합의된 결정은 다시 지적하지 않았다.

**게이트 0 판정: 아니오.** 수정할 항목은 R25-1~R25-8의 8건이다. 그중 차단 항목은 R25-1 하나다. 이 항목은 R24-1 수정이 만든 회귀다.

## R24 수정 종료 확인 (새 탐침)

| ID | 결과 | 새 탐침 |
| --- | --- | --- |
| R24-1 | **본래 증상은 종료. 수정이 새 구멍을 냈다 → R25-1** | 두 서버에서 PNG 150개(900px)와 `zz0.zip`이 있는 폴더를 비동기 검사하고 0.5 s 뒤 취소했다. 컨테이너 행은 `cancelled`, 「압축 파일: 판단 불가 — 검사 취소 — 구성 파일 2개 미분석(부분 결과).」이다. `/api/report`는 그대로 보내면 200(`files_cancelled` 140/141, listing `scan_job` 0, 「사용자 취소」 제외 행)이다. 압축 파일을 디스크에서 바꾸면 409다. PNG 행을 `cancelled`로 바꾸면 400(NOT_IN_JOB)이다. `summary.cancelled:false`를 보내면 200이고 서버 값을 쓴다. 부분 분석 컨테이너(구성원 16개 분석, 65개 미분석)는 `cancelled`이고 「압축 파일: 조작·생성 근거 있음 — … 검사 취소 — 구성 파일 65개 미분석」이다. OUTPUT_FORMAT_GENERATION 10: fbcb53c(9)가 채운 캐시를 HEAD가 0행만 적중한다. 같은 세대에서 상대 경로 2회차는 10/10 적중하고 콜드 결과와 같다. **그러나 비취소 검사의 행이나 컨테이너의 구성원을 `cancelled`로 서명 보고서에서 뺄 수 있다(R25-1).** |
| R24-2 | 종료(잔여 → R25-4, 기존 결함 R25-5·R25-6·R25-7) | 18가지 이름을 썼다: `c:v.mp4`, `c:a.wav`, `c:a.m4a`, `50%.mp4`, `f%d.mp4`, `f%03d.mp4`, `x?y.mp4`, `a#b.mp4`, `file:z.mp4`, `pipe:0.mp4`, `data:v.mp4`, `subfile,x.mp4`, `-i.mp4`, `%2e.mp4`, `한글:v.mp4` 등. 각 이름을 `scan <abs>`, `cd p; scan case`, `cd case; scan .`, 폴더 `ev:2026`의 상대·절대로 돌렸다. HEAD는 영상 검사 결과가 모두 절대 경로 실행과 같다. fbcb53c·2130892는 `ev:2026` 상대 실행에서 영상 13개가 `video_analysis failed`, `cd case; scan .`에서 `x:` 이름 4개가 실패한다. 단일 파일 `classify`도 상대·절대·깊은 cwd가 같다(`pipe:0.mp4`, `c:v.mp4`는 fbcb53c에서 실패). |
| R24-3 | 종료(비ASCII 잔여 → R25-4) | 깊이 22 × 200자(`pwd` 4435바이트)에서 `scan case --workers 4`를 돌렸다. WAV·mp4·`x:`·`%` 이름 모두 절대 경로 실행과 같다(fbcb53c: 오디오 4개 `LibsndfileError`, 영상 15개 `av_audio` `FfmpegError 220`). 심볼릭 링크 cwd(논리 10바이트, 물리 4435바이트)에서도 `audio_features` `ran`이다. 같은 깊은 cwd에서 `classify` 6개를 공유 HOME으로 동시에 돌려도 6/6 `ran`이다. `cd case; scan .`(깊은 cwd)도 같다. `web --folder case`(깊은 cwd, 상대)는 오디오·영상 모두 `ran`이다. **`한글:v.mp4`는 깊은 cwd에서만 `video_analysis failed`이다(R25-4).** |
| R24-4 | 종료 | uid 1000, 두 서버에서 7가지 경우를 확인했다. chmod 000 → 409 → chmod 644 → 200(기존 b.png 보존). `[]` → 409 → `{}` → 200. 쓰다 만 JSON → GET `{}`·POST 409 → 외부에서 완성 → GET에 외부 항목이 보이고 POST 200(외부 항목 보존). 같은 크기·같은 mtime 외부 수정(sed + `touch -d`) → GET에 반영(ctime). 디스크에서 삭제 → GET `{}`, POST는 새 파일을 만든다. 파일 자리에 폴더 → 409(「폴더입니다」). 증거 폴더로 가는 심볼릭 링크 → 409. |
| R24-5 | 종료(인접 기존 결함 → R25-2) | uid 1000에서 `classify`를 상대·절대 png로 돌리면 rc 0이고 판단 불가, `tool_candidates` `failed`(「생성 도구 표지 대조 실패 — 파일을 읽지 못했습니다(PermissionError…)」)다. 텍스트 형식도 rc 0이다. api `/api/classify`(wav·mp4)는 200이다. FIFO·폴더·없는 파일은 rc 2이고 각각 「일반 파일이 아닙니다」, 「폴더입니다」, 「찾을 수 없습니다」다. **읽을 수 없는 wav/mp4/pdf는 「분석 중 파일 변경」이라는 거짓 사유를 낸다(R25-2).** |
| R24-6 | 종료 | `gate.sh gui`(gui 이미지, `--shm-size=1g`): fixtures와 `/tmp/adv` 모두 `GUI smoke PASS`이고 GATE rc=0, 20 s다. 단계마다 pipefail이 걸려 있다. |
| R24-7 | **부분(잔여 → R25-3)** | 8개 사건 필드 × 3개 값(리터럴 `\u05e2 corp`, `가\u3000나`, `x\|y`)을 CLI 증거설명서 PDF와 `scan`의 forensic/pdf/증거설명서 PDF로 그렸다. 머리글·사건·원고·피고·제출처·서명란·호증·대표전화는 `display_name`을 거친다. **증거설명서 표의 「작성자 및 일자」 칸만 법무법인을 그대로 그린다(R25-3).** |
| R24-8 | 종료 | api `/api/scan/stream`: `recursive=t`·`maybe`·`true&maybe`는 400(한국어)이다. `TRUE`, ` yes `, 빈 값, `1`, `0`, `no`는 200이다. `max_files`가 정수가 아니면 422이고, 이는 커밋과 서비스 문서 E37에 적힌 대로다. |
| R24-9 | 종료 | `gate.sh qa-main`을 `/marker` 이후로 실행했다. 202개 테스트, 0 실패, 19/0/4/12/1이다. 새로 쓰인 파일은 `/tmp/qa-main`(1.2 MB, HOME 포함)과 `/tmp/qa-main-venv`(952 KB)다. 그 밖에는 gate.sh 머리 부분의 `git config --global`이 쓴 `/root/.gitconfig`뿐이다. 이 줄은 모든 모드에 공통이고 컨테이너 안에서만 돈다. `__pycache__`는 없다. |
| R24-10 | 종료 | `test_audio_worker`를 포함한 10개 모듈(390 테스트)을 `/venv`·`/venv-api`·qa-main식 임시 venv에서 각 3회 돌렸다. 9회 모두 OK다(임시 venv skipped=2). |
| R24-11 | 종료(잔여 → R25-8) | `help scan -h/--help`, `help vendor-weights -h`, `help vendor-weights pin -h`, `help scan -h -h`, `help corpus build -h`는 0이다. `help scan -h extra`, `help scan --he`, `help scan -hh`는 2다(타당). **`help help -h`, `help -h -h`는 2다(R25-8).** |

원래 결함도 다시 확인했다.

- R18-3 부분 보고서와 R23-3 취소 계수: 위 R24-1 탐침에서 서버 값(140/141/65)이 쓰였다. 「없음」은 나오지 않았다(R25-1의 위조 경로는 제외).
- R19-13: 스캔 뒤 파일이 사라지면 409다.
- R20-1: hostile 폴더의 보고서는 200이고 서명된다. verify-report rc 0, 변조 40/40 탐지(세 트리 모두).
- R20-4/5: 위 탐침 범위에서 다시 열리지 않았다.
- R21-1/5 불변식 퍼징: 서버당 300건, 그중 210건은 거부(409/400)였다. 서명 출력에 게시 문자열이 새어 나온 경우는 0이다.
- R22-1 listing: 취소 작업에서는 `scan_job`, 평면 검사에서는 `walk`다.
- R23-1 상대 경로: hostile 폴더에서 `cd base; scan case`와 `cd case; scan .`가 절대 경로 실행과 정규화 바이트까지 같다.
- R23-5 구성원 생략: 걷기 목록에서 `bundle.zip::gen.png`를 빼면 `not_in_report 1`이다. 압축 파일 전체를 빼도 1이다. 단 R25-1 경로는 제외다.
- R19-2 크래시 격리: WAV 24개 중 작업자 2회 SIGKILL → 2개 `NativeCrashError`, 22개 `ran`, rc 0이다.
- R22-5 차단기: 0.6 s에 모두 `WorkerStartError`다.
- R18-1/R19-1 캐시 키: QA-IN-4에서 해시가 바뀌고 새 해시가 맞으며 적중 0이다. 세대 9→10 무효화와 상대 경로 재사용(10/10)도 확인했다.
- R17 감독: `stress_shutdown --runs 30`에서 실패 0이다. CLI에 SIGINT를 보내면 rc 130이고 출력 없이 정리 문구가 나온다.

## 회귀 탐색 요약 (vs fbcb53c, 2130892)

- **hostile 폴더**: 52개 항목, 행 53(재귀 164). uid 1000으로 실행했다.
  - 세 트리의 정규화 결과 217행이 모두 같다.
  - 결정성: workers 1 = 4. 상대(`cd base; scan case`, `cd case; scan .`) = 절대.
  - CLI = web = api-serve.
  - QA-IN-1: 증거 변화 0. QA-IN-2: 이름을 바꿔도 같다.
  - 서명 변조 40/40 탐지. doctor rc 0.
- **새 회귀 1건**: R25-1. fbcb53c는 같은 요청을 409로 거부하고, 2130892는 모든 행을 서명한다.
- **기존 결함**: 그 밖의 R25-2·R25-4·R25-5·R25-6·R25-7은 fbcb53c·2130892에서도 같다. R25-3·R25-8은 R24 수정의 잔여다.
- **성능**: 측정 조건은 다음과 같다.
  - gate 이미지, `--cpus 4`. 측정 시작·끝에 실행 중인 컨테이너는 내 `r25-p` 하나였다(`out/perf-others-*.txt`).
  - 데이터는 컨테이너 `/tmp`에 둔다. 측정마다 새 HOME을 썼다(표시한 항목 제외). 트리 사이에 `/venv/var`를 지웠다.
  - 순서는 2130892 → HEAD → fbcb53c → HEAD다(`out/perf.log`).
  - 단위는 초다. HEAD는 두 패스의 값이다.

| 경로 | 2130892 | fbcb53c | HEAD (패스 1 / 2) | HEAD/2130892 |
| --- | --- | --- | --- | --- |
| scan 이미지 2000 w1 | 5.71 | 5.78 | 5.72 / 5.73 | 0 % |
| scan 이미지 2000 w4 | 3.52 | 3.63 | 3.52 / 3.66 | 0~+4 % |
| scan WAV 300 w4 (1회차/2회차) | 25.95/27.02 | 11.97/6.95 | 11.73/6.78 / 12.30/7.02 | -53 %/-74 % |
| scan WAV 300 w1 | 33.86 | 18.50 | 18.47 / 18.51 | -45 % |
| scan WAV 300 w4 상대 경로 | 26.92 | 6.72 | 6.58 / 7.32 | -73~-76 % |
| classify WAV 1개, 같은 HOME (×4 평균) | 1.60 | 1.60 | 1.60 / 1.64 | 0~+2 % |
| batch WAV 20 w4 (×3 평균) | 4.71 | 2.40 | 2.36 / 2.41 | -49 % |
| 웹 업로드 WAV 20, 새 서버·새 HOME (×3) | 5.00 | 3.33 | 3.33 / 3.36 | -33 % |
| 웹 업로드 WAV 20, 같은 서버 (×3) | 3.93 | 3.33 | 3.31 / 3.33 | -16 % |
| 보고서 재분석 WAV 300 (×2) | 32.89 | 19.03 | 18.27 / 18.38 | -44 % |

25 % 기준을 넘는 저하는 없다. HEAD와 fbcb53c의 차이도 측정 잡음 범위(±5 %) 안이다.

## 지적 사항

### R25-1 (Med, 차단, 회귀 — 3d48593 R24-1) `cancelled` 상태로 서명 보고서에서 파일·구성원을 뺄 수 있고, 보고서는 「기록되지 않은 파일: 없음」이라고 서명한다

**재현 A — 일반 파일** (두 서버, 비취소 동기 검사 `case/` = `bundle.zip`(gen.png + plain.png), `evidence_gen.png`(A1111 메타데이터 → 조작·생성 근거 있음), `ok.png`, `ok2.png`)

```
POST /api/report {scan_root, summary:{cancelled:true},
                  items: 검사 결과 그대로, 단 evidence_gen.png 행만 kind "archive", status "cancelled"}
→ 200, 서명됨(verify-report rc 0)
  excluded_items: evidence_gen.png 「사용자 취소 — 검사가 취소되어 이 압축 파일의 구성 파일을 모두 분석하지 않았으므로 …」
  unrecorded listing {source: walk, not_in_report: 0}
  HTML/JSON/evidence/pdf: 「기록되지 않은 파일: 없음 — 분석 대상의 모든 파일에 분석 결과가 있습니다」
  요약: 조작·생성 근거 있음 2건(정직한 보고서는 3건)
```

- `scan_job`이 없는 id(`nonexistent-job`)여도 같다.
- 결과를 `null`로 보내도 같다.
- fbcb53c는 같은 요청을 409(「검사 뒤 바뀐 파일」)로 거부한다. 2130892는 6행을 모두 서명한다.

**재현 B — 압축 파일 구성원**

- 같은 폴더에서 `bundle.zip` 컨테이너 행을 `cancelled`로 바꾸고 구성원 행 두 개를 뺐다. `summary.cancelled:true`를 보냈다.
- 결과는 200이다. 서명된 행은 3개이고 listing은 0이다.
- `bundle.zip::gen.png`(조작·생성 근거)의 흔적이 서명 본문에 없다.
- R23-5가 막은 「구성원 생략 → 없음」이 그대로 다시 열린다.

**재현 C — 실제로 취소된 작업**

- `big.zip`(조작·생성 PNG 1개 + 900px PNG 80개)을 검사하다 0.8 s에 취소했다. 구성원 16개(조작·생성 포함)가 분석되었고 65개는 미분석이다.
- 작업 결과에서 분석된 구성원 행 16개를 빼고 `scan_job`과 함께 보냈다.
- 결과는 200이다. 서명된 행은 0, 조작·생성 0건이다.
- listing은 「확인 불가 — 검사 폴더가 없는 보고서 — 업로드 결과만 있음」이다. 사실과 다른 사유다.
- 다른 일반 파일이 함께 서명되면 listing은 `scan_job`이고 `not_in_report`가 0이 된다(코드상 `missing_members = unsigned_members ∩ job_members`, `unsigned_members`는 비어 있음).
- 반대로 구성원 행을 하나라도 남기면 재분석이 일어나 정상적으로 센다(gen 구성원만 빼면 1).

**원인**

- webapp_api.py:1754-1769: `cancelled` 컨테이너 행만 있고 구성원 행이 없으면 재분석하지 않는다. 커밋 본문도 "A container posted without member rows is not re-analyzed at all"이라고 적는다. 그래서 `unsigned_members`가 생기지 않는다.
- webapp_api.py:1870 `_posted_cancelled_row_error`: 컨테이너인지를 **게시된** `item.kind`로만 판단한다. 서버가 그 파일을 압축 파일로 보는지는 확인하지 않는다.
- webapp_api.py:2315: `cancelled_tops`를 listing 계산에서 빼므로 그 경로는 「없음」으로 셈된다.
- `scan_job` 없이 `summary.cancelled:true`만 보내도 `cancelled` 행을 받는다(설계상 "unverified").

**왜 차단인가**

- 행은 「서명 제외(클라이언트 제공 결과)」 절에 보이지만 서명되지 않는다. 그런데 서명된 요약과 「기록되지 않은 파일: 없음」은 거짓이다.
- 제외 사유(「압축 파일의 구성 파일…」)도 PNG에 대해 거짓이다.
- 이 보고서는 서명 검증을 통과한다.
- 공통 규칙의 "서명 출력은 게시 문자열이 아니라 서버가 다시 만든 사실만"(R21-1/5, R23-5)을 어긴다.
- 테스트(`test_a_cancelled_container_row_needs_a_cancelled_report`)는 `summary.cancelled`만 있는 경우를 200으로 기대한다. 위조 `kind`나 구성원 생략은 다루지 않는다.

→ 컨테이너 여부는 서버가 찾은 파일로 정한다(압축 파일 판별 또는 서버 `scan_job` 행). `cancelled` 컨테이너도 구성원 목록은 서버가 정한다. `scan_job`이 있으면 작업 행에서, 없으면 압축 목록을 열거한다. 서명되지 않은 구성원은 `not_in_report`(또는 `files_cancelled`)로 센다. `scan_job` 없이 `summary.cancelled`만 있는 보고서에서는 `cancelled` 행을 「확인 불가」로 세거나 거부한다. 회귀 테스트: 위 A·B·C → 409/400이거나 `not_in_report ≥ 1`이어야 하고, 「없음」이 아니어야 한다.

### R25-2 (Low, 기존 결함) 읽을 수 없는 오디오·영상·PDF 파일이 「분석 중 파일 변경 — … 크기·수정 시각·inode 또는 내용 해시가 다릅니다」로 기록된다

**재현** (uid 1000, `chmod 0`)

```
scan case / classify case/locked.wav / 웹 /api/scan
  l.wav, l.mp4, l.pdf → status "analyzed", 「l.wav: 판단 불가 — 분석 중 파일 변경 — 판단 불가: 분석 전후로 크기·수정 시각·inode 또는 내용 해시가 다릅니다 (…)」
                        coverage file_integrity failed(같은 문구), audio_features failed(LibsndfileError)
  l.png, l.txt        → status "failed", 「PermissionError: [Errno 13] 접근 권한이 없습니다」
```

- 파일은 바뀌지 않았다. 증거가 변조되었다고 읽힐 수 있는 거짓 사유다(R24-1이 지적한 「바뀐 파일」과 같은 종류다).
- HEAD, fbcb53c, 2130892가 같다.
- 위치: core.py:855-858. `post_digest = _file_fingerprint(path)`가 읽기 실패로 비면 `not post_digest`를 "변경"으로 처리한다.
- → 사전·사후 지문을 모두 읽지 못하고 상태(`_file_state`)가 같으면 읽기 실패로 기록한다(`failed`, PermissionError 사유). R24-5와 같은 회귀 테스트(uid 1000의 wav/mp4/pdf)를 둔다.

### R25-3 (Cosmetic, R24-7 잔여) 증거설명서 PDF의 「작성자 및 일자」 칸은 법무법인을 그대로 그린다

- `evidence-statement a.png --pdf-out s.pdf --law-firm "\u05e2 corp"`(리터럴)와 `--law-firm "ע corp"`를 비교했다.
- 머리글·서명란은 `\\u05e2 corp`와 `\u05e2 corp`로 구별된다. 그러나 표의 작성자 칸은 둘 다 `\u05e2 corp`로 찍힌다.
- `x|y`는 다른 곳에서 `x\|y`로 찍히지만 이 칸에서는 `x|y`다. `scan --evidence-statement-pdf-out`도 같다.
- 위치: evidence_statement.py:407(`author_date = f"{law_firm}\n{now_date}"`), 698(`Cell(2, entry.author_date …)`).
- 799ee6a 본문의 "law firm (header and signoff)"과 "the first prints \u05e2corp only"는 이 칸에서 사실이 아니다.
- 덧붙임: `display_name`은 PDF 사건 필드에서도 Markdown용 `|`→`\|`와 NFD 한글·NBSP·U+3000 이스케이프를 적용한다. 예를 들어 macOS에서 복사한 NFD 「한국」이 원고란에 `\u1112\u1161\u11ab…`으로 찍힌다. 구별에는 맞지만 법원 제출 서식에서는 읽을 수 없다. 이 점은 문서에 적거나 정규화(NFC)를 먼저 하는 편이 낫다.
- → 작성자 칸에도 `display_name(law_firm)`을 쓴다. 테스트에 이 칸을 넣는다. 커밋 본문을 정정한다.

### R25-4 (Low, R24-3 잔여 — 기존 결함) PATH_MAX보다 깊은 cwd에서 비ASCII 상대 이름의 영상은 분석되지 않는다

- 깊이 22 × 200자 cwd에서 `scan case`를 돌리면 `한글:v.mp4`가 `video_analysis failed`이고 「비디오 열기 오류: 비ASCII 이름의 파일을 열 수 없습니다(…)」다. `av_audio`는 skipped다.
- 같은 파일을 절대 경로나 얕은 cwd에서 돌리면 `ran`이다.
- 2130892·fbcb53c도 같다.
- 원인: native_path `_stage`가 비ASCII 이름을 스테이징할 때 `os.path.abspath`(4096바이트 초과)로 링크·복사하다 ENAMETOOLONG을 받는다. R24-3의 `./` 대체 경로는 ASCII 이름에만 적용된다.
- → 스테이징도 cwd 기준 상대 이름(`./…`)이나 `chdir_any`로 원본을 연다. 회귀 테스트(깊은 cwd + 비ASCII 이름)를 둔다.

### R25-5 (Low, 기존 결함 — 문서·환경) 지원 형식으로 문서화된 `.m4a`·`.aac`·`.wma`의 오디오 특징 검사가 고정 환경에서 항상 실패한다

- `/venv`(게이트 메인 환경)에서 `classify a.m4a|a.aac|a.wma`를 돌리면 `audio_features failed`이고 「LibsndfileError: 오디오 파일을 열 수 없습니다(형식 인식 불가)」다. 결론은 판단 불가다.
- wav/mp3/flac/ogg/opus는 `ran`이다.
- `audioread`가 잠금 파일에 없어서 librosa의 대체 디코더가 없다. 2130892도 같다.
- `docs/deepfake-lens-cli.md:91`과 `audio.py:23 SUPPORTED_AUDIO_EXTENSIONS`는 이 형식을 지원 오디오로 적는다.
- R24-2의 코드 주석과 커밋 본문("librosa's audioread fallback runs ffmpeg")은 이 환경에 없는 경로를 근거로 든다.
- fail-closed는 지켜진다(검사 실패 + 판단 불가).
- → `_extract_features`에서 libsndfile이 못 읽는 형식은 ffmpeg로 WAV 변환한다(audio.py:998과 같은 방식). 아니면 문서에 "m4a/aac/wma는 오디오 특징 검사 불가"를 적는다.

### R25-6 (Low, 기존 결함) 압축 파일 구성원 이름에 `:`가 있으면 「절대 경로 멤버(대상 폴더 밖 쓰기 시도)」로 거부된다

- `m.zip`에 다음 7개 구성원을 넣었다: `c:v.png`, `2026:10:15.png`, `ab:c.png`, `dir:x/y.png`, `c:/abs.png`, `C:\win.png`, `ok.png`.
- 결과: `ok.png`만 분석되고 6개는 거부된다. 「거부 6개(예: c:v.png — 절대 경로 멤버(대상 폴더 밖 쓰기 시도))」다. tar도 같다.
- 2130892도 같다.
- R24-2가 "드물지 않다"고 한 macOS 날짜 이름(Finder의 `/` → POSIX `:`)은 폴더에서는 이제 분석된다. 그러나 같은 파일을 압축하면 분석되지 않는다.
- `c:v.png`에는 zip-slip을 뜻하는 사유가 붙는다. 검사자가 압축 파일을 공격용으로 오인할 수 있다.
- 추출 대상 이름은 이미 색인 기반(`_member_target(dest, index, rel)`)이라 원래 이름이 경로가 되지 않는다.
- 위치: archives.py:160-166(`normalized[1] == ":"` → 절대 경로), 278-281.
- → POSIX에서는 `:` 이름을 거부하지 않는다(대상은 색인 이름). 또는 사유를 「Windows에서 쓸 수 없는 이름(':')」으로 바로잡는다. `x:` 한 글자 접두를 절대 경로로 부르지 않는다.

### R25-7 (Cosmetic, 기존 결함 — 결정성) 디코드 실패 사유의 경로 표기가 호출 방식에 따라 다르다

- `scan /abs/case`와 `scan case`의 행 사유는 「LibsndfileError: 오디오 파일을 열 수 없습니다(형식 인식 불가): '<root>/a.m4a'」다.
- `cd case; scan .`의 같은 행 사유는 「… : 'a.m4a'」다.
- `c:a.m4a`도 같다. 같은 파일·같은 폴더인데 행 텍스트가 다르다(QA-OUT-4, 결정성).
- HEAD, fbcb53c, 2130892가 같다.
- 위치: 작업자 오류를 호출자 형식으로 되돌리는 `_caller_form`과 `<root>` 치환. `.` 루트에서는 접두가 없어 치환되지 않는다.
- → 행 사유의 경로는 루트 기준 표기로 통일한다(`<root>/a.m4a`).

### R25-8 (Cosmetic, R24-11 잔여) `help help -h`와 `help -h -h`가 exit 2다

- `help help`는 0이다. 그런데 `help help -h`는 「deepfake-lens: 오류: 인수 <명령>: 허용되지 않은 값 'help'」, exit 2다. 메시지도 사실과 다르다(`help`는 명령이다).
- `help -h -h`는 「허용되지 않은 값 '-h'」, exit 2다.
- 위치: cli.py:600-613. `-h` 제거가 `topic`을 정한 뒤에만 일어나고, `help` 주제 분기 앞에서는 처리되지 않는다.
- → 주제를 정하기 전에 끝의 `-h`/`--help`를 떼거나, `help help -h`를 `help help`와 같게 처리한다.

## 잔여 1단계 리스크

- 웹 보고서의 무결성은 여전히 "게시된 행 중 서버가 다시 만들 수 있는 것만 서명"에 기대고 있다. `cancelled`처럼 게시값으로 재분석을 건너뛰는 예외가 생기면 R23-5·R25-1과 같은 생략 구멍이 다시 생긴다. 예외 경로마다 "서버가 정한 목록과의 차집합"을 세는 공통 검사를 두는 편이 안전하다.
- 적합성 기록(CONFORMANCE)은 여전히 API venv에서 만들어지고, librosa·soundfile·c2pa가 없다고 적힌다. qa-main은 별도로 통과한다.
- Windows 경로·ACL은 mock으로만 검증된다(합의 사항). `child_path`의 Windows 분기에는 PATH_MAX(MAX_PATH 260) 처리가 없다.
- 오디오 형식 지원(R25-5)은 환경 잠금 파일에 좌우된다. 형식별 실제 디코드 경로를 QA 시나리오가 다루지 않는다.
- PDF는 CJK 글꼴 하나와 `display_name` 이스케이프에 의존한다. 다국어·NFD 사건 필드는 구별되지만 읽기 어렵다.
- 게이트 이미지 재빌드는 이번에도 네트워크 의존이라 시도하지 않았다(24차와 같음).
- 이전 라운드 잔여(제자리 매니페스트 수정 미감지, 골든 출력의 mediapipe/torch 미포함, 비Linux SIGKILL 자손 정리 최선 노력 등)는 유지된다.

## 게이트 0 판정: 아니오

수정 목록: R25-1(차단), R25-2, R25-3, R25-4, R25-5, R25-6, R25-7, R25-8.

## 부록

### 게이트·기록

- 측정 조건: git 이력이 있는 사본, 로컬 main=dad9730. 시작 시점에 다른 세션 컨테이너는 없었다.
- `gate.sh full`은 rc 0이고 17분 53초(1072 s) 걸렸다. 탐침 컨테이너 1개가 동시에 돌았다.
  - /venv: Ran 1832, OK(skipped=102). /venv-api: Ran 1832, OK(skipped=55).
  - ruff, mypy(131 파일) 통과. coverage 80 %.
  - cli_smoke 45/26, 측정 게이트 10/0, sync_model_docs, verify_contracts(3+2), stress 30/0.
  - qa-main: 202 테스트, 0 실패, 19/0/4/12/1.
- `gate.sh gui`는 rc 0이다(두 단계 PASS, 20 s).
- `qa_phase0.py --verify-record`: "7b99aa7fa8da에서 생성되었고 그 뒤 다른 변경이 없습니다". `build_traceability_commits.py --check`: 비병합 커밋 338개(dad9730..7b99aa7), 표 이후 1개(재생성 커밋만). R24-1~R24-11이 `ids[]`에 있다.
- CONFORMANCE의 1832/55는 게이트 실측과 같다.
- 커밋별 게이트(`git archive` 트리, 이력 없음): 코드가 바뀐 11개 커밋(3d48593..4c2dfc0) 모두 두 venv 단위 테스트, ruff, mypy가 OK다(1821→1832, skip 102→105 / 57→58). 문서 커밋 6e3bd1e·7b99aa7·d2239fe는 코드가 이웃 커밋과 같다.
- 각 수정 커밋의 변경된 테스트 모듈을 부모 소스에서 실행한 결과(임시 venv)는 아래와 같다.

  | 커밋 | 결과 |
  | --- | --- |
  | 3d48593 | F3 E4 |
  | 77b1345 | F2 |
  | a9d7eaa | F4 E1 |
  | 33f8d8c | F6 |
  | 7abbfaa | F3 |
  | 799ee6a | F4 |
  | 97227a0 | F14 |
  | a73da37 | F1 |

  07df093·4c2dfc0은 부모에서 통과한다. 07df093은 게이트 스크립트 수정이고, 4c2dfc0은 테스트 자체 수정이다. 테스트 약화·삭제는 없다.
- 반복 실행: 10개 모듈(390 테스트)을 /venv·/venv-api·임시 venv에서 각 3회 돌렸다. 9/9 OK이고 플레이크는 0이다.
- 얕은 클론(depth 1, /venv-api): 1832개 중 문서화된 2개 테스트(QaSys10 deletion reasons 9, test_shutdown history 5)만 실패한다(하위 테스트 14). QA-ENV-DEPENDENT-TESTS와 일치한다.

### 커밋 메시지 정확성

- 3d48593: "a row of status cancelled must be a top-level archive container row"라고 적었다. 그러나 실제로는 게시된 `kind`만 본다(R25-1).
- 799ee6a: 작성자 칸이 빠졌다(R25-3).
- 77b1345: audioread 대체 경로를 근거로 들지만 이 환경에는 그 경로가 없다(R25-5).
- 나머지 8개 커밋 본문은 탐침 결과와 맞다.

### 테스트 감사 보충

- R24-1 테스트는 위조 `kind`, 구성원 생략, 부분 분석 컨테이너의 구성원 생략을 다루지 않는다(R25-1).
- R24-7 테스트는 증거설명서 표의 작성자 칸을 보지 않는다(R25-3).
- R24-3 테스트는 깊은 cwd의 비ASCII 이름을 다루지 않는다(R25-4).
- R24-5 테스트는 png만 다룬다. wav/mp4/pdf의 읽기 실패 사유는 보지 않는다(R25-2).

### WP·규칙·QA 대조

- WP-A~J는 구현되어 있고, WP-J의 두 게이트(full, gui)가 통과한다.
- 공통 규칙 1~7:
  - fail-closed는 지켜진다. 다만 R25-2의 사유는 거짓이다.
  - 서명 범위 규칙은 R25-1이 어긴다.
  - "CLI/GUI/API 같은 결과"는 R25-7(행 텍스트)과 R25-6(폴더 vs 압축 파일)에서 어긋난다.
- QA 시나리오:
  - QA-IN-1/2/4: 통과.
  - QA-OUT-1/2/3/5/6, ADV-1/2/3, SYS-1/2/3/6/7/9: 기존 테스트와 이번 탐침 범위에서 통과.
  - QA-OUT-4: 이름의 `:`·`%`·`?`·`#`, 상대·깊은 cwd에서 통과. 비ASCII + 깊은 cwd(R25-4)와 `.` 루트 사유 텍스트(R25-7)는 예외다.
  - QA-SYS-6: 변조 40/40 탐지. 다만 R25-1의 위조 보고서도 검증을 통과한다(서명이 거짓 요약을 보증한다).
- CONFORMANCE의 "QA-OUT-4 통과"는 하네스 범위에서 맞다.
- HANDOFF 사실 대조:
  - §1·§2: 24회, R24-1~R24-11 수정은 맞다.
  - §4: GUI 스모크 안내대로 통과한다.
  - §5: qa-main은 `/tmp` 아래에만 쓴다. `/root/.gitconfig`는 gate.sh 공통 머리 부분이 쓴다.
  - §9: 다음 할 일은 맞다.

### 탐침 목록 (이전에 다루지 않은 영역, 43종)

1. 위조 `cancelled` 컨테이너(구성원 유지) × 2서버
2. 위조 + 구성원 생략
3. 위조 + 끝난 비취소 작업(400)
4. 위조 + 없는 작업 id(200)
5. PNG를 `archive`/`cancelled`로 위조
6. 같은 위조, `result: null`
7. `summary.cancelled` 없음(400)
8. 구성원 행 `cancelled`
9. 실제 취소 + zip 그대로
10. 실제 취소 + 압축 파일 교체(409)
11. 실제 취소 + PNG 행 위조(400)
12. 실제 취소 + `summary.cancelled:false`
13. 부분 분석 컨테이너의 분석된 구성원 생략
14. gen 구성원만 생략
15. 취소 작업 JSON → `evidence-statement`
16. 위조 보고서 3트리 비교
17. 위조 보고서 verify-report와 html/evidence/pdf
18. 18개 이상한 이름 × abs/rel/dot/`ev:2026` × 3트리
19. 깊은 cwd `scan --workers 4`
20. classify 6개 이름 × rel/abs/깊은 cwd
21. 오디오 형식 8종 × 2트리
22. 심볼릭 링크 cwd(물리 경로 > PATH_MAX)
23. 깊은 cwd 동시 classify 6개(공유 HOME)
24. 깊은 cwd `web --folder case`
25. zip/tar 구성원 `:` 이름 9종
26. reviews 7가지 × 2서버(uid 1000)
27. 읽기 불가 png/wav classify·forensic·explain(상대·절대)
28. FIFO·폴더·없는 파일 classify
29. help 16종
30. 읽기 불가 wav/mp4/pdf/png/txt 웹 scan과 api classify × 3트리
31. PDF 사건 필드 8개 × 3값
32. 이상한 값 8종의 PDF/MD/JSON 대조
33. scan의 forensic/pdf/증거설명서 PDF
34. stream `recursive` 9종 + `max_files` 6종
35. `/api/scan` `max_files`·알 수 없는 매개변수
36. 캐시 세대 9→10과 상대 경로 재사용
37. CLI SIGINT(json/table)
38. qa-main 쓰기 범위
39. GUI 게이트
40. 얕은 클론
41. 3 venv × 3회 반복 실행
42. hostile 3트리(결정성·상대·web/api·변조·doctor)
43. 원래 결함 재확인(QA-IN-4, 차단기, R23-5, R19-13, R19-2, 불변식 600건)

### 컨테이너와 파일

- 사용한 컨테이너: r25-full, r25-p, r25-gui, r25-qa, r25-pc. 동시에 최대 2개였고, 성능 측정 때는 r25-p 하나만 실행했다. 모두 `--rm`이다.
- 탐침 스크립트와 로그는 `scratchpad/r25/out/`에 있다(q/*.py·*.sh, gate_full.log, gate_gui.log, percommit.log, perf.log, flakes.log, shallow.log, qa_main_scope.log, c*_*.log, r6_hostile.log, r10_originals.log, r25norm-*.json). 같은 내용을 `~/Desktop/deepfake-lens-verify-archive/r25-out/`에 복사했다.
- 원본 저장소 세 곳은 변경하지 않았다.

## 조정자 추가 — R25-5 수정 중 발견

### R25-9 (Low, 기존 결함) `multimodal --av-sync`가 영상의 오디오를 읽지 못하고 거짓 사유를 낸다

- `multimodal.analyze_av_sync`의 `_audio_envelope`는 영상 파일을 `librosa.load`로 직접 연다. 잠금 파일의 librosa 1.0.0은 audioread 대체 경로가 없고 soundfile(libsndfile)만 쓰므로 mp4 같은 영상 컨테이너는 항상 `LibsndfileError: Format not recognised`다.
- 예외는 삼켜지고 결과는 「오디오 스트림을 읽을 수 없어 A/V 싱크를 측정할 수 없습니다.」다. 음성 트랙이 있는 mp4와 음성 트랙이 없는 mp4가 같은 문구를 낸다(게이트 이미지에서 확인). 측정은 한 번도 실행되지 않고, 원인(디코더 없음)은 어디에도 기록되지 않는다.
- → R25-5의 ffmpeg → 임시 WAV 경로(for_child, PATH_MAX 처리 포함)로 영상의 오디오 트랙을 디코딩한다. 디코딩할 수 없으면 실제 사유(의존성 부재: ffmpeg / 오디오 트랙 없음 / ffmpeg 실패)를 검사 범위와 한계에 적는다. 다른 `librosa.load` 호출도 같은 문제가 있는지 감사한다. 회귀 테스트: 음성 트랙이 있는 mp4와 없는 mp4.
