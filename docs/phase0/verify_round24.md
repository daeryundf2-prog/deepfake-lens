# 독립 검증 라운드 24 결과 (HEAD fbcb53c)

검증자는 0단계 구현에 참여하지 않았다. 사본은 `scratchpad/r24/repo`(`git clone`, 로컬 `main`=dad9730)이고 원본 저장소 세 곳은 건드리지 않았다. 비교 트리는 `git archive`로 푼 85ad114(직전 검증 HEAD)와 2130892다. 기준 환경은 Docker 이미지 `deepfake-lens-gate:latest`(8f13a610a203, aarch64, Python 3.13.16, ffmpeg 7.1.5, Node 20.19.2, `--cpus 4`)이다. 탐침은 상대 경로 입력과 메인 환경(librosa 있음 — `/venv`, 또는 `/venv`+`/venv-api`를 함께 보는 qa-main식 임시 venv)을 항상 포함했다. HANDOFF §7의 합의된 결정은 다시 지적하지 않았다.

**게이트 0 판정: 아니오.** 수정할 항목은 R24-1~R24-11의 11건이다. 그중 차단 항목은 R24-1 하나다.

## R23 수정 종료 확인 (새 탐침)

| ID | 결과 | 새 탐침 |
| --- | --- | --- |
| R23-1 | 종료(잔여 2건 → R24-2, R24-3) | `/venv`에서 WAV·`a:b.wav`·`concat:x.wav`·`-dash.wav`·한글·비UTF-8·손상 WAV·mp4 10개를 `scan <abs>`, `scan case`, `cd case; scan .`, 심볼릭 링크 cwd에서 `scan ..`로 돌렸다. HEAD는 네 경우 모두 `audio_features` `ran`이고 행이 절대 경로 실행과 같다(85ad114: 상대 실행 6개 `failed`). 단일 파일 명령 8종(classify/audio/compare/video-analysis …)도 같다. 출력에 절대 경로가 새지 않았다(작업자 오류는 `_caller_form`으로 호출자 형식, 스캔은 `<root>`). 비UTF-8 cwd·한글 cwd·삭제된 cwd(절대 입력)도 정상이다. hostile 폴더에서도 `cd base; scan case`와 `cd case; scan .`가 절대 실행과 바이트 동일하다. 30 WAV `--workers 4` 상대=절대, `batch case`, `web --folder case`, 서버 `analyze-file`/`check`도 `ran`이다. **열린 구멍**: cwd 기준 `x:` 접두 이름의 프로세스 내 cv2(R24-2), PATH_MAX를 넘는 cwd(R24-3, 새 회귀). |
| R23-2 | 종료 | 프로젝트 폴더 안 실제 `.venv`(sys.prefix=proj/.venv)로 15개 명령(classify/evidence/evidence-statement/audio/compare/explain/legal-report/forensic/multimodal/video-analysis/classify mp4/절대 경로/`batch .`/`scan .`/`../proj/a.wav`)을 돌렸다. `.venv/var` 생성은 0건이다(85ad114: 2→50개). `classify sub/c.wav`(분석 폴더가 .venv 밖)는 환경 캐시를 정상 사용한다. 단일 파일 명령 시간은 2130892와 같은 수준이다(아래 성능 표). |
| R23-3 | 종료(인접 결함 R24-1) | 두 서버에서 취소 작업 + summary 생략/`{}`/`cancelled:false`/`"no"`는 모두 서버 값(288·291개 취소)으로 표시되고 「없음」이 없다. 다른 폴더의 작업은 409, 정상 종료 작업 + 게시 `cancelled:true`는 `unverified`, 취소 작업에서 행 2개를 빼면 `not_in_report 2`, 재귀 취소 작업도 맞다. **그러나 취소된 검사 폴더에 압축 파일이 있으면 보고서가 아예 만들어지지 않는다(R24-1).** |
| R23-4 | 종료(잔여 1건 → R24-7) | 히브리어·이모지·태국어 이름과 그 이름의 리터럴 `\uXXXX` 이름 쌍, 결합 문자, U+202E, 130자 히브리어 이름을 웹 `pdf`·`evidence`, CLI `evidence-statement --pdf-out`으로 그렸다. 이름은 모두 구별되고 NUL이 없으며, 페이지 밖 글자 범위는 0개다. 줄바꿈은 이스케이프 문자열 기준으로 측정된다. 사건 필드는 단사가 아니다(R24-7). |
| R23-5 | 종료 | 두 서버에서 walk 목록: `bundle.zip::gen.png`, 중첩 `outer.zip::inner.zip::deep/gen.png`, `top.png`, 3000개 구성원 zip의 한 행을 각각 빼면 `not_in_report 1`이다. 컨테이너만 게시하면 구성원 수만큼 센다(10/999/1000(상한)). 완료된 `scan_job` 목록에서 구성원 1개를 빼면 1, 한 zip의 구성원 4개를 모두 빼면 4다. 1009행 보고서 재분석은 2.4–2.9 s다. |
| R23-6 | 종료(mock 수준, 합의 사항) | 순수 함수 10종: 기본 ProgramData 상속(창작자 본인 True, 다른 계정 False), SHARED_PARENT_SDDL(본인 True, Administrators 소유 True), Everyone (IO) 항목 True(자식은 보호 ACL), Authenticated Users DELETE_CHILD False, 자식 폴더 CO 허용·다른 계정 (IO) 쓰기 거부·다른 소유자 거부. 모두 커밋 설명과 맞다. |
| R23-7 | 종료(잔여 1건 → R24-4) | uid 1000, 두 서버: 0500 폴더에서 review·feedback 409(한국어), GET은 빈 목록, 삭제 실패 시 항목 보존, `DEEPFAKE_LENS_FEEDBACK=/dev/full`은 507. 읽지 못한 기존 파일은 덮어쓰지 않는다. 다만 파일을 고친 뒤에도 거부가 재시작 전까지 계속된다(R24-4). |
| R23-8 | 종료 | 두 서버: 재정의 둘 다 설정 시 운영자 폴더에 `f.jsonl`·`r.json`만 있고 기본 위치는 만들어지지 않는다. 재정의 없음은 둘 다 한 번 이전된다(피드백 1+2줄, 표지). 피드백만 재정의 시 리뷰만 기본 위치로 이전된다. |
| R23-9 | 종료 | 두 서버에서 `model_path`/`fusion_profile` 5000자, `a/`×2100, 255자, 256자 모두 `/api/scan`과 보고서 options에서 400이다. |
| R23-10 | 부분 확인(네트워크) | `docker build --no-cache -t deepfake-lens-gate:r24rebuild`는 기반 이미지 레지스트리 메타데이터 조회에서 39분간 진행하지 못해 중단했다(23차와 같은 증상). 대신 수정 세션이 만든 `:rebuild`(212636a4b4f0)와 `:latest`를 비교했다. Python 버전, `/venv`·`/venv-api` `pip freeze`, `dpkg-query -W`, ffmpeg·node 버전 417줄이 동일하고 잠금 파일과도 일치한다(순서만 다름). |
| R23-11 | 종료(표현 잔여 → R24-11) | 17종: `help vendor-weights pin`/`pin pin` 0+알림, `help help` 0, `help help help`·`help vendor-weights bogus`·`help scan extra` 2, `help corpus build` 0. `help <명령> -h`는 2다(R24-11). |
| R23-12 | 종료 | c89cb3a·494c18d·ec4b17a 본문에 정정이 있다. 이번 라운드의 새 부정확 기술은 R24-8, R24-9. |
| R23-13 | 종료(잔여 → R24-8) | 두 서버: `async=maybe/t`, `recursive=maybe`, `dedupe=2`, `heatmaps=nope` 400, `async=TRUE`, `recursive=%20yes%20`, `recursive=` 200. JSON options: `true`/`1`/`"TRUE"`/`null` 200, `"maybe"`/`2`/`[]`/`{}`/`0.0`/`"None"` 400. `/api/scan/stream`은 같은 규칙이 아니다(R24-8). |
| 23차 잔여(오디오 QA) | 종료(문서 잔여 → R24-9, 테스트 잔여 → R24-10) | `gate.sh qa-main`: 201개 테스트, 0 실패, 19/0/4/12/1. 로그에서 두 상대 경로 오디오 QA 테스트가 `passed`이고 librosa가 import된다. 쓰기는 `/tmp` 밖에 `$HOME/.cache/deepfake-lens`(로그, numba published 캐시)와 표준 라이브러리 `__pycache__` 107개가 있다(R24-9). |

원래 결함도 다시 확인했다.

- R19-13: 스캔 뒤 파일 이동은 409.
- R20-1: hostile 폴더의 FIFO·순환·권한 없는 행이 서버 사유로 서명된다(200, verify rc 0).
- R20-4/5: 위 탐침 범위에서 다시 열리지 않았다.
- R21-1/5 불변식: 게시 문자열(행·summary의 문자열 388곳 중 4곳씩, `없음`·U+202E·HTML·결론 라벨 섞음) 150회 × 2서버 × json/html, 600건 중 서명 출력에 나타난 경우 0(그중 420건은 409/400 거부).
- R22-1: 상한(`max_files 3`)·평면·취소 작업 목록이 서버 값이다.
- R19-2 크래시 격리: 24개 WAV 스캔 중 작업자 2회 SIGKILL → 2개 `NativeCrashError`, 22개 `ran`, rc 0.
- R18-1/R19-1 캐시 키: QA-IN-4(마지막 바이트 + `touch -r`) 재분석, 새 해시 정확. 상대(`scan case --cache cache.json`) → 절대 → `cd case; scan . --cache ../cache.json` 순서로 31/31 캐시 적중이고 결과가 같다.
- R17 감독: `stress_shutdown --runs 30` 실패 0. R22-5 실제 차단기(준비 한도 0.01 s) 0.4 s에 전부 `failed`/`undetermined`.

## 회귀 탐색 요약 (vs 85ad114, 2130892)

- **hostile 폴더**: 52개 항목, 행 53(재귀 164). 비root(uid 1000)로 실행했고 내용은 23차와 같다.
  - 세 트리의 정규화 결과 217행이 모두 동일하다.
  - 결정성: workers 1 = 4, 이름 변경 후 바이트 동일, 상대(`cd base; scan case`, `cd case; scan .`) = 절대(HEAD·2130892; 85ad114는 상대에서 tone.wav가 달랐다).
  - CLI = web = api-serve.
  - QA-IN-1: CLI·web·api·보고서 전후 증거 변화 0.
  - 서명 변조 40/40 탐지. verify-report rc 0. doctor rc 0.
- **새 회귀 2건**:
  - R24-1: R19-13 이후. 2130892에서는 200이었다.
  - R24-3: R23-1 수정이 만든 회귀. 85ad114에서는 영상 음성 트랙이 `ran`이었다.
- **성능**: gate 이미지, `--cpus 4`, 내 컨테이너 1개만 실행했고 다른 세션의 컨테이너는 시작·종료 시점에 없었다. 데이터는 컨테이너 `/tmp`에 있다. 매 측정마다 새 HOME을 쓰고(표시한 항목 제외), 트리 사이에 `/venv/var`를 지웠다. 첫 번째 4트리 실행은 원인을 모르는 호스트 부하로 HEAD 1회차와 85ad114 값이 2–3배 흔들려서 버렸다(`out/perf.log`). 아래는 두 번째 실행(`out/perf2.log`, 순서 2130892 → HEAD → 85ad114 → HEAD)의 값이다. 단위는 초이고, HEAD는 두 패스의 값이다.

| 경로 | 2130892 | 85ad114 | HEAD (패스 1 / 2) | HEAD/2130892 |
| --- | --- | --- | --- | --- |
| scan 이미지 2000 w1 | 5.71 | 5.72 | 6.09 / 5.72 | +0~7 % |
| scan 이미지 2000 w4 | 3.54 | 3.67 | 3.61 / 3.67 | +2~4 % |
| scan WAV 300 w4 (1회차/2회차) | 31.96/26.90 | 12.04/7.15 | 11.94/6.88 / 12.22/7.06 | -62 %/-74 % |
| scan WAV 300 w1 | 33.71 | 18.56 | 18.59 / 18.82 | -45 % |
| scan WAV 300 w4 **상대 경로** | 26.58 | 1.60(전부 failed) | 6.70 / 6.75 | -75 % |
| classify WAV 1개, 같은 HOME (×4) | 1.69 | 1.68 | 1.68 / 1.70 | 0 % |
| batch WAV 20 w4 (×3) | 4.70 | 2.53 | 2.47 / 2.48 | -47 % |
| 웹 업로드 WAV 20, 새 서버·새 HOME (×3) | 4.87 | 3.37 | 3.35 / 3.37 | -31 % |
| 웹 업로드 WAV 20, 같은 서버 (×3) | 3.96 | 3.35 | 3.35 / 3.34 | -15 % |
| 보고서 재분석 WAV 300 (×2) | 32.99 | 18.27 | 18.37 / 18.38 | -44 % |

25 % 기준을 넘는 저하는 없다. 프로젝트 `.venv` 배치에서 새 HOME으로 단일 파일 명령을 돌리면 환경 캐시를 쓰지 않으므로 매번 6–7 s다(R23-2의 의도된 대가). 같은 HOME에서는 1.7 s다.

## 지적 사항

### R24-1 (Med, 차단, 회귀 — R19-13 이후) 취소된 웹 검사의 폴더에 압축 파일이 있으면 보고서를 만들 수 없고, "검사 뒤 바뀐 파일이 있습니다"라는 거짓 사유로 409가 난다

**재현** (두 서버 공통 경로, 웹 서버로 확인)

```
case/ = PNG 150개(900px) + zz0.zip, zz1.zip(각 PNG 3개)
/api/scan?async=1&folder=case&workers=1 → 0.6 s 뒤 /api/scan-cancel
작업: cancelled, 행 14, files_cancelled 144
  zz0.zip / zz1.zip 행: status "unknown", 「압축 파일: 판단 불가 — 아카이브에서 분석 가능한 구성 파일이 없습니다.」
POST /api/report {items, scan_root, scan_job}           → 409 「검사 뒤 바뀐 파일이 있습니다 — 다시 검사하십시오」
                 (scan_job 없음 / summary.cancelled true) → 409
changed_rows: posted_sha256 == server_sha256, posted_status "unknown", server_status "expanded"
```

- 아무 파일도 바뀌지 않았다. 그런데도 검사자에게 증거가 바뀌었다고 알린다. 증거 무결성 사유로 읽힐 수 있는 거짓 메시지다.
- R18-3(취소된 검사의 부분 보고서)와 R23-3(취소 작업 계수)의 흐름이 압축 파일이 하나라도 있는 폴더에서는 전혀 쓸 수 없다. 재귀 취소(하위 폴더의 zip)도 같다.
- 2130892는 같은 요청이 200이다. 85ad114와 HEAD는 409다.
- 취소 작업의 컨테이너 행 문구 「아카이브에서 분석 가능한 구성 파일이 없습니다」도 사실과 다르다. 구성 파일은 있고 취소되었을 뿐이다. 이 문구는 작업 결과 JSON과 GUI에 그대로 나온다.
- 상한(`max_files` 1/2/3/10)으로 잘린 스캔의 컨테이너는 `expanded`이고 200이다. 즉 취소 경로만의 문제다.

**위치**

- core.py:702 — 구성원이 하나도 분석되지 않으면 이유와 관계없이 `"unknown"`이고 「분석 가능한 구성 파일이 없습니다」다.
- webapp_api.py:1975 `_row_signature`(sha256, status, verdict)와 1782 `_rederive_report_items`의 비교 — 서버는 컨테이너를 끝까지 재분석해 `expanded`를 만든다.

→ 취소로 구성원이 분석되지 않은 컨테이너는 상태와 문구를 따로 둔다(예: `cancelled`, 「검사 취소 — 구성 파일 N개 미분석」). 보고서 재분석은 그런 행을 "바뀜"이 아니라 취소 행으로 다룬다(서명하지 않고 `files_cancelled`로 센다). 회귀 테스트: 취소 작업 + zip(평면·재귀) → 200, 「사용자 취소」, 409 아님.

### R24-2 (Low, 기존 결함 — R23-1 범위 밖) cwd 기준 상대 이름이 `x:`로 시작하면 프로세스 내 cv2/FFmpeg가 프로토콜로 읽어 영상 검사가 실패한다

**재현**

```
cd case; deepfake-lens classify c:v.mp4    → video_analysis failed 「비디오를 열 수 없습니다」, av_audio skipped
cd case; deepfake-lens classify ./c:v.mp4  → 같음(pathlib이 ./를 지운다)
cd /tmp/p; deepfake-lens scan ev:2026      → clip.mp4 같음
deepfake-lens scan /tmp/p/ev:2026          → ran / ran
web(cd case), /api/scan · analyze-file?file=c:v.mp4, api /api/check → ran / ran
```

- 2130892, 85ad114, HEAD가 모두 같다. 기존 결함이다.
- 같은 파일이 호출 방식에 따라 다른 결과를 낸다(QA-OUT-4, "CLI/GUI/API가 같은 파일에 같은 결과"). macOS Finder에서 `/`가 들어간 폴더 이름(예: 날짜 「2026/10/15」)은 POSIX에서 `2026:10:15`가 되므로 드문 경우가 아니다.
- R23-1의 `child_path` 문서와 테스트(`test_ffmpeg_gets_a_relative_name_with_a_colon_as_a_file`)는 자식 프로세스만 다룬다. 같은 문제를 가진 프로세스 내 호출은 남아 있다.

**위치**: video_analysis.py:113, 323, face_track.py:123, rppg.py:387, lipsync.py:213, model_adapter.py:970, multimodal.py:471, 513(librosa → audioread → ffmpeg). 모두 `native_safe_path(...)`를 `for_child` 없이 부른다.

→ cv2/librosa(FFmpeg 백엔드)로 가는 이름도 절대 경로로 만든다(`child_path`, 또는 `./` 접두). 상대 `x:` 이름의 classify·scan 회귀 테스트를 QA-OUT-4에 둔다.

### R24-3 (Low, 회귀 — 5f3990f R23-1) cwd가 PATH_MAX보다 깊으면 상대 입력의 오디오·영상 음성 검사가 실패한다

**재현**: 깊이 22 × 200자 폴더(`pwd` 4436바이트) 안에서 `scan case`를 실행한다.

| 트리 | a.wav `audio_features` | clip.mp4 `av_audio` |
| --- | --- | --- |
| HEAD | failed (LibsndfileError) | failed (`FfmpegError: ffmpeg 실패(종료 코드 220)`) |
| 85ad114 | failed (R23-1) | ran |
| 2130892 | ran | ran |

- `child_path`가 `os.getcwd()`와 이어 붙인 이름이 4096바이트를 넘는다. 자식은 ENAMETOOLONG을 받는다.
- 그 전에는 ffmpeg가 cwd를 상속해 상대 이름으로 열었다.
- 드문 배치지만 매우 긴 경로는 이번 라운드의 명시적 회귀 점검 항목이다.

**위치**: native_path.py `child_path`(1202-1220).

→ 절대 이름이 한도를 넘으면 자식에 `cwd`를 넘기고 상대 이름(`./` 접두)을 쓴다. 아니면 짧은 이름으로 스테이징한다. 오디오 작업자는 요청에 cwd를 함께 보내거나 스테이징한다. 회귀 테스트를 둔다.

### R24-4 (Low, R23-7 잔여) 기존 검토 파일을 고쳐도 서버를 다시 시작하기 전까지 저장 거부가 계속된다

**재현** (uid 1000, 두 서버)

```
reviews.json(항목 b.png)을 chmod 000 → POST /api/review 409 「…읽지 못해(접근 권한이 없습니다) 저장하지 않았습니다 — … 파일을 확인하거나 …」
chmod 644                         → POST /api/review 여전히 409(같은 사유), GET /api/reviews {}(파일에는 b.png)
reviews.json이 [] → 409 ; '{}'로 고침 → 여전히 409 「JSON 객체가 아닙니다」
```

- 안내문은 파일을 확인하라고 하지만, 확인하고 고쳐도 해결되지 않는다. 재시작해야만 풀린다.
- 읽기 쪽도 빈 캐시를 계속 보여 준다.

**위치**: reviews.py:70-95. `_load`는 `_cache`가 있으면 다시 읽지 않는다. `_unreadable`은 경로가 바뀔 때만 초기화된다.

→ `_unreadable`이 설정된 상태(또는 파일의 mtime·크기가 바뀐 경우)에는 다음 요청에서 다시 읽는다. 회귀 테스트: 거부 → 파일 수리 → 같은 서버에서 200, 기존 항목 보존.

### R24-5 (Low, 기존 결함) `classify <읽을 수 없는 파일>`이 「처리 중 예기치 않은 오류(PermissionError)」로 끝나고 결과를 내지 않는다

**재현**: uid 1000, `chmod 0 case/locked.png`(또는 .wav).

- `classify case/locked.png`(상대·절대 모두)는 rc 1이고 JSON이 없다. stderr는 「오류: 처리 중 예기치 않은 오류가 발생했습니다(PermissionError)」다.
- 같은 파일에 `forensic`, `explain`, `evidence`, `legal-report`는 rc 0이고 실패를 기록한다. `scan`은 행 「PermissionError: 접근 권한이 없습니다」를 낸다.
- HEAD, 85ad114, 2130892가 같다.
- fail-closed 규칙(검사 실패는 검사 범위 `failed` + 판단 불가)에서 classify만 벗어난다.

**위치**: cli_standalone.py:347 `tool_attribution`. `Path(path).open("rb")`에 예외 처리가 없다. 호출은 cli.py:1069.

→ 읽기 실패를 도구 표지 계층의 `failed` 진단으로 기록하고 결론(이미 `analysis_result_for_path`가 만든 것)을 출력한다. 회귀 테스트를 둔다.

### R24-6 (Low, WP-J / HANDOFF §4) `gate.sh gui`는 레포가 정의한 GUI 이미지에서 통과할 수 없다

**재현**: `docker run … deepfake-lens-gate:gui /gate.sh gui`

- 결과: 두 단계 모두 `BrowserType.launch: Chromium distribution 'chrome' is not found at /opt/google/chrome/chrome`, GATE rc=1.
- 원인 1: scripts/gui_smoke.py:61이 `channel="chrome"`(Google Chrome)을 띄운다. Dockerfile.gui는 Playwright 자체 Chromium(chromium-1243)만 설치한다. 이미지의 `/venv-gui`는 requirements-gui.lock과 동일함을 확인했다.
- 원인 2: `channel`을 빼고 번들 Chromium으로 돌려도 gui_smoke.py:95 `#stat-total > 0`에서 실패한다. `fixtures`와 `/tmp/adv`의 최상위에는 파일이 없다(「하위 폴더 5개(안의 파일 47개)는 검사하지 않았습니다」). 그래서 평면 검사 결과가 0행이다.
- HANDOFF §4는 이 명령을 GUI 스모크 게이트로 안내한다.

→ 스크립트가 번들 Chromium을 쓰게 하고(또는 Dockerfile.gui에 Chrome을 설치), 최상위에 파일이 있는 폴더를 쓰거나 '하위 폴더' 옵션을 켜게 한다. 게이트가 실제로 통과하는지 확인한 뒤 HANDOFF §4에 적는다.

### R24-7 (Cosmetic, R23-4 잔여) 증거설명서 PDF의 사건 필드는 이스케이프가 단사가 아니다

- `evidence-statement a.txt --pdf-out … --plaintiff "ע corp"`와 `--plaintiff "\u05e2 corp"`(리터럴 백슬래시)의 PDF가 모두 「원고(고소인)    \u05e2 corp」로 찍힌다.
- 파일 이름은 `display_name`을 거치므로 구별되지만(R23-4 확인), 사건 필드는 원문을 그대로 `drawable`에 넘긴다. 그래서 백슬래시가 두 배가 되지 않는다.
- 같은 증거설명서의 Markdown/JSON에서는 둘이 다르다.
- 위치: evidence_statement.py:661-662(그리고 같은 표의 다른 사건 필드).
- → 사건 필드도 PDF에서 백슬래시를 두 배로 하거나 `display_name`과 같은 규칙을 적용한다. a30a455 본문의 단사 설명을 "파일 이름에 한해"로 정정한다.

### R24-8 (Cosmetic, R23-13 잔여) `/api/scan/stream`의 `recursive`는 R23-13 규칙과 다르다

- api-serve `POST /api/scan/stream?recursive=t`는 200이다(pydantic이 `t/f/y/n` 등도 받는다). 같은 서버의 `/api/scan?async=t`는 400이다.
- 알 수 없는 값(`maybe`)은 400이 아니라 422다.
- 617130d 제목 "a true/false option with an unknown value is a 400 on both servers"와 본문 "an unknown value was already refused"가 이 경로에서는 정확하지 않다.
- 위치: api_server.py:880(`recursive: bool`).
- → `str`로 받아 `parse_flag`를 쓴다. 아니면 문서에 예외를 적는다.

### R24-9 (Cosmetic, WP-J 문서) "qa-main은 /tmp에만 쓴다"는 사실이 아니다

- 494daf0 본문("this run writes only under /tmp"), gate.sh의 QA_MAIN 주석("writes to /tmp/qa-main only"), HANDOFF §5("출력은 `/tmp/qa-main`에만 쓰고")가 이렇게 적는다.
- 실제로는 `$HOME/.cache/deepfake-lens/logs/{deepfake-lens.log,native-stderr.log}`와 `numba/<key>/published`(25개 파일)를 만든다. 임시 venv는 표준 라이브러리 `__pycache__`도 107개 만든다.
- 컨테이너에서는 사라지지만, 개발 머신에서 같은 명령을 돌리면 사용자의 도구 캐시가 바뀐다.
- → 하네스 실행에 임시 HOME을 주거나, 문서를 사실대로 고친다.

### R24-10 (Cosmetic, 테스트 이식성) R23-2 종단 테스트가 HANDOFF의 qa-main식 임시 venv에서 실패한다

- `test_audio_worker.EnvironmentCacheOutsideAnalyzedFileFoldersTest.test_a_project_venv_next_to_the_sample_gets_nothing`는 `/venv`+`/venv-api` `.pth` 임시 venv에서 3/3회 실패한다(`'skipped' != 'ran'`, published 0 != 1).
- 테스트가 만든 프로젝트 venv의 `.pth`는 실행 중인 인터프리터의 purelib만 가리킨다. 임시 venv의 purelib에는 `.pth`만 있다(중첩 `.pth`는 처리되지 않음). 그래서 librosa가 없다고 판단해 skip된다.
- `/venv`, `/venv-api`에서는 통과한다(각 3회). 제품 결함은 아니다.
- 위치: test_audio_worker.py:242.
- → `sys.path`의 site 디렉터리를 모두 넣거나 `site.getsitepackages()`와 `.pth` 내용을 따라간다.

### R24-11 (Cosmetic, R23-11 잔여) `help <명령> -h`가 exit 2다

- `help scan -h`, `help vendor-weights -h`는 「deepfake-lens scan: 오류: 알 수 없는 인수: -h」(exit 2)다. `-h`는 그 명령의 정당한 옵션이고, 사용자는 도움말을 원한 것이 분명하다.
- 위치: cli.py `_help_topic`(`word.startswith("-")` → error).
- → `-h`/`--help`는 그 명령의 도움말로 처리한다.

## 잔여 1단계 리스크

- 오디오 QA는 이제 qa-main으로 메인 환경에서 돈다. 그러나 적합성 기록(CONFORMANCE)은 여전히 API venv에서 만들어지고, librosa·soundfile·c2pa가 없다고 적힌다.
- Windows 경로·ACL은 mock으로만 검증된다(합의 사항). R23-6의 SDDL은 실제 Windows에서 확인되지 않았다.
- PDF는 CJK 글꼴 하나와 이스케이프 규칙에 의존한다. 다국어 증거명은 PDF에서 읽기 어려운 코드로 나온다(구별은 됨).
- 게이트 이미지 재빌드는 레지스트리·snapshot.debian.org 접근이 필요하다. 이번 라운드에서는 네트워크 문제로 독립 재빌드를 하지 못했다.
- 단일 파일 명령에서 세션 임시 폴더는 분석 파일의 폴더를 제외 대상으로 보지 않는다(설계상 잔여, 실행 후 삭제).
- 이전 라운드 잔여(제자리 매니페스트 수정 미감지, 골든 출력의 mediapipe/torch 미포함, 비Linux SIGKILL 자손 정리 최선 노력 등)는 유지된다.

## 게이트 0 판정: 아니오

수정 목록: R24-1(차단), R24-2, R24-3, R24-4, R24-5, R24-6, R24-7, R24-8, R24-9, R24-10, R24-11.

## 부록

### 게이트·기록

- 측정 조건: git 이력이 있는 `--no-hardlinks` 사본, 로컬 main=dad9730. 다른 세션 컨테이너는 없었다.
- `gate.sh full`은 rc 0이고 16분 25초 걸렸다.
  - /venv: Ran 1813, OK(skipped=99). /venv-api: Ran 1813, OK(skipped=54).
  - ruff, mypy(131 파일) 통과. coverage 80 %.
  - cli_smoke 45/26, 측정 게이트 10/0, sync_model_docs, verify_contracts(3+2), stress 30/0.
  - qa-main: 201 테스트, 0 실패, 19/0/4/12/1.
- `gate.sh gui`는 rc 1이다(R24-6).
- `qa_phase0.py --verify-record`: "bbe353221965에서 생성되었고 그 뒤 다른 변경이 없습니다". `build_traceability_commits.py --check`: 비병합 커밋 324개(dad9730..bbe3532), 표 이후 1개(재생성 커밋만). R23-1~R23-13이 `ids[]`에 있다.
- 커밋별 게이트(`git archive` 트리, 이력 없음 조건): 0fa5ac8..fbcb53c의 17개 커밋 모두 두 venv 단위 테스트, ruff, mypy가 OK다(1784→1813, skip 100→102 / 51→57).
- 각 수정 커밋의 변경된 테스트 모듈(qa 포함)을 부모 소스에서 실행하면, 감사는 librosa+fastapi 임시 venv에서 했다.

  | 커밋 | 결과 |
  | --- | --- |
  | 5f3990f | F5 E4 |
  | 38911f0 | F11 |
  | c89cb3a | F28 |
  | a30a455 | F7 E1 |
  | 494c18d | F19 |
  | ec4b17a | E11 |
  | 899c104 | F3 |
  | 190694d | F13 |
  | ec1229a | F6 E2 |
  | 617130d | F14 E1 |
  | 5b51712 | F1 |

  494daf0의 qa 테스트는 부모(5b51712, 이미 수정됨)에서 통과한다. 본문은 0fa5ac8 기준 실패라고 적었고 이는 맞다. 테스트 약화·삭제는 없다(TEST-DELETIONS 변경 없음).
- 반복 실행: test_audio_worker, test_servers, test_user_data, test_pdf_layout, test_tool_folders, test_native_path, qa test_qa_in/out(349개)을 /venv·/venv-api에서 각 3회 돌려 실패 0이다. 임시 venv에서는 R24-10의 1개가 3/3 실패했다(환경 원인).
- 얕은 클론(depth 1, /venv-api): 문서화된 2개 테스트(QaSys10 deletion reasons, test_shutdown history)만 실패한다(하위 테스트 14). QA-ENV-DEPENDENT-TESTS R16-12와 일치한다.

### 테스트 감사 보충

- R23-1 테스트(`ChildPathTest`)는 자식 프로세스만 검사하고 프로세스 내 cv2 호출의 `x:` 이름은 다루지 않는다(R24-2). 매우 긴 cwd도 없다(R24-3).
- R23-3·R23-5 테스트에는 압축 파일이 있는 취소 작업이 없다(R24-1).
- R23-7 테스트에는 "거부 → 파일 수리 → 재시도"가 없다(R24-4).
- R23-13 테스트는 `/api/scan/stream`을 다루지 않는다(R24-8).

### WP·규칙·QA 대조

- WP-A~J는 구현되어 있다. 단 WP-J의 GUI 스모크 게이트가 통과할 수 없다(R24-6).
- 공통 규칙 1~7: fail-closed는 classify의 읽기 실패에서 어긋난다(R24-5). 게이트 0 목표 "CLI/GUI/API 같은 결과"는 R24-2가 어긴다.
- QA 시나리오:
  - QA-IN-1: 통과(프로젝트 .venv, 상대 경로, hostile).
  - QA-IN-2/4/5, OUT-1/2/3/5/6, ADV-1/2/3, SYS-1/2/3/6/7/9: 기존 테스트와 이번 탐침 범위에서 통과(변조 40/40, 403/400/409, 손상 파일 hostile).
  - QA-OUT-4: 절대·상대 경로 오디오는 통과하지만 상대 `x:` 이름 영상에서 실패(R24-2).
- CONFORMANCE의 "QA-OUT-4 통과"는 하네스 범위(서버는 절대 경로, 오디오 폴더 상대 경로)에서만 맞다.
- HANDOFF 사실 대조:
  - §1·§2·§4(재현성: latest = rebuild 동일)는 맞다.
  - §5의 qa-main 쓰기 범위 기술은 틀리다(R24-9).
  - §4의 GUI 스모크 안내대로는 게이트가 통과하지 않는다(R24-6).

### 탐침 목록 (이전에 다루지 않은 영역, 52종)

1. ~10. 상대 경로 오디오·영상 이름 10종(`a:b.wav`, `concat:x.wav`, `-dash.wav`, 한글, 비UTF-8, 손상, mp4, `c:v.mp4`) × 4가지 호출 × 3트리
11. 심볼릭 링크 cwd에서 `scan ..`
12. `x:` 상대 단일 파일(classify, `./`)
13. `x:` 상대 폴더
14. 서버 3종에서 `x:` 이름
15. 비UTF-8 cwd
16. 한글 cwd
17. PATH_MAX 초과 cwd
18. 삭제된 cwd
19. 30 WAV w4 상대=절대
20. 상대/절대/점 교차 캐시
21. 상대 compare(비UTF-8 이름)
22. uid 1000 읽기 불가 파일 classify 등 5개 명령
23. 프로젝트 .venv 15개 명령
24. 단일 파일 시간(3트리)
25. 취소 작업 summary 변형 6종 × 2서버
26. 실행 중 작업 id
27. 작업에 없는 행 추가
28. 취소 작업 부분집합
29. 정상 종료 작업 + `cancelled`
30. 취소 + 압축 파일(평면·재귀) × 3트리
31. 상한 + 압축 파일
32. 구성원 생략 walk 6종 × 2서버
33. 컨테이너만 게시(10/999/1001/3000개)
34. `scan_job` 구성원 생략
35. R23-13 query 9종 × 2서버
36. R23-13 JSON options 10종 × 2서버
37. `/api/scan/stream`
38. R23-9 5종 × 2경로 × 2서버
39. PDF 이스케이프 단사 쌍 × 4 PDF 경로
40. PDF 페이지 밖 글자
41. 사건 필드 단사
42. 저장소 오류 8종 × 2서버(uid 1000)
43. `/dev/full` 507
44. 이전 3종
45. DACL 순수 함수 10종
46. help 17종
47. qa-main 쓰기 범위
48. qa-main 상대 오디오 테스트 실행 여부
49. 이미지 freeze/dpkg 비교
50. GUI 스모크(chrome / 번들 Chromium)
51. 불변식 퍼징 600건
52. hostile 3트리(상대 호출 포함)

### 컨테이너와 파일

- 사용한 컨테이너: r24-full, r24-p, r24-h, r24-pc, r24-qa, r24-gui, r24-sh, r24-fl, r24-img, r24-perf. 동시에 최대 2개였고, 성능 측정 때는 1개만 실행했다. 모두 `--rm`으로 삭제했다.
- 독립 재빌드 시도(`deepfake-lens-gate:r24rebuild`)는 메타데이터 단계에서 중단되어 태그가 만들어지지 않았다.
- 탐침 스크립트와 로그는 `scratchpad/r24/out/`(q/*.py, *.sh, gate_full.log, percommit.log, perf.log, perf2.log, r*_*.log, img-*.txt, qa-main-run/)에 있고, `~/Desktop/deepfake-lens-verify-archive/r24-out/`에 복사했다.
- 원본 저장소 세 곳은 변경하지 않았다.
