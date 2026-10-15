# 독립 검증 라운드 26 결과 (HEAD 037ade7)

검증자는 0단계 구현에 참여하지 않았다. 사본은 `scratchpad/r26/repo`(`git clone`, 로컬 `main`=dad9730)이고 원본 저장소 세 곳은 건드리지 않았다. 비교 트리는 `git archive`로 푼 d2239fe(직전 검증 HEAD)와 2130892다. 기준 환경은 Docker 이미지 `deepfake-lens-gate:latest`(8f13a610a203, aarch64, Python 3.13, ffmpeg 7.1, `--cpus 4`)와 `deepfake-lens-gate:gui`(c204cd6603ae)다. macOS 쪽 확인(PATH_MAX 1024, `/tmp` 심볼릭 링크)은 호스트(Darwin 24.6, 기존 `.venv`를 읽기만 하고 `PYTHONDONTWRITEBYTECODE=1`, HOME·TMPDIR은 scratchpad)에서 했다. 탐침은 상대 경로 입력, 매우 긴 경로·이상한 이름, 메인 환경(`/venv`, librosa 있음 — 또는 `/venv`+`/venv-api`를 함께 보는 qa-main식 임시 venv)을 항상 포함했다. HANDOFF §7의 합의된 결정은 다시 지적하지 않았다.

**게이트 0 판정: 아니오.** 수정할 항목은 R26-1~R26-7의 7건이다. 그중 차단 항목은 R26-1 하나다. 이 항목은 R25-6 수정(f2fc514)이 만든 회귀다.

## R25 수정 종료 확인 (새 탐침)

| ID | 결과 | 새 탐침 |
| --- | --- | --- |
| R25-1 | **종료.** 단 R25-6 수정이 같은 종류의 새 구멍을 냈다 → R26-1. 스트림 검사의 문서·문구 → R26-5 | 25차 재현 A~G4를 두 서버에서 다시 돌렸다. 위조 `cancelled` 컨테이너(구성원 유지·생략), 끝난 비취소 작업, 없는 작업 id, PNG를 `archive`/`cancelled`로 위조(결과 `null` 포함), `summary.cancelled` 없음은 모두 400이다. 실제 취소 작업은 그대로 보내면 200(`files_cancelled` 138/137 서버 값, listing `scan_job`, `not_in_report` 0)이고, 압축 파일을 바꾸면 409, PNG 행을 위조하면 400, `summary.cancelled:false`여도 서버 값이다. 구성원 행 `cancelled`는 400(NOT_CONTAINER)이다. 작업 만료(TTL을 0.5 s로 줄임) 뒤 같은 보고서는 400이고 문구에 「만료」가 있다. 만료 뒤 취소 컨테이너 행을 빼고 보내면 200, `cancelled:false`, listing `walk`, `not_in_report` 162(최상위 120 + 미분석 구성원 42)로 정직하다. 업로드 행을 폴더 파일과 같은 이름으로 섞어도 폴더 파일은 `not_in_report` 1로 셈된다. 같은 내용 압축 파일 2개(dedupe) + 취소 3회도 정직하다. 위조 `container`/`member` 9종은 400/409다(`member_index`만 바꾸면 200, 서명 행은 서버 것). R21-1/5 불변식 퍼징 600건: 거부 420, 게시 문자열 누출 0. |
| R25-2 | **종료.** 단 새 사유 경로가 R25-7의 경로 표기 규칙을 비켜 간다 → R26-2 | uid 1000, 13개 형식(wav, mp3, m4a, wma, mp4, mov, pdf, png, jpg, txt, docx, zip, tar)을 `chmod 0`으로 두고 `scan <abs>`, `scan case`, `cd case; scan .`, `classify`(상대·절대), 웹 `/api/scan`을 돌렸다. 모두 `failed` + 「PermissionError: [Errno 13] 접근 권한이 없습니다」(압축 파일은 「압축 해제 실패(PermissionError…)」)이고 「분석 중 파일 변경」은 0건이다(d2239fe는 10건). 분석 중 실제로 바뀐 파일 8가지(덧붙임, mtime 복원, 분석 중 chmod 0, 같은 크기 재작성 + chmod 0, 읽을 수 없는 파일로 교체, 처음부터 읽을 수 없는 파일의 덧붙임·재작성·교체)는 HEAD·d2239fe 모두 「분석 중 파일 변경」이다. 실제 변경이 「읽기 실패」로 잘못 기록되는 경우는 찾지 못했다. |
| R25-3 | **종료(문구 잔여 → R26-6)** | 5개 값(리터럴 `\u05e2 corp`, `ע corp`, 파이프가 든 값, 백슬래시+파이프가 든 값, NFD 「한국」) × 8개 사건 필드를 `scan --pdf-out --evidence-statement-pdf-out`, CLI `evidence-statement --pdf-out`, 웹 `format=pdf`로 그렸다. 「작성자 및 일자」 칸을 포함한 사건 필드는 모두 구별된다. 파이프는 입력 그대로(이스케이프 없이) 찍히고, 리터럴 백슬래시는 두 배로 찍힌다. NFD는 이스케이프로 남는다(문서화됨). **그러나 같은 PDF에서 파일 이름과 검사 폴더 이름의 파이프는 Markdown 이스케이프(백슬래시+파이프)로 찍힌다(R26-6).** |
| R25-4 | **Linux 종료. macOS 잔여 → R26-3** | Linux: 깊이 22 × 200자(cwd 4442바이트)에서 `scan case --workers 2`를 돌렸다. `한글:v.mp4`, `c:v.mp4`, `한글 녹음.m4a`, `t.m4a`가 모두 `ran`이고, `multimodal --av-sync case/한글:v.mp4`도 `ran`이다. macOS: cwd 1540바이트에서 `한글:v.mp4`, `한글 녹음.m4a`, `한글.wav`의 `scan case`가 절대 경로 실행과 같고, `classify`·`--av-sync`도 `ran`이다. 스테이징 잔여물은 0이다. **그러나 macOS에서 2 GiB가 넘는 비ASCII 이름 영상은 깊은 cwd에서만 실패하고, 사유도 사실과 다르다(R26-3).** |
| R25-5 | 종료 | `/venv`에서 m4a, aac, wma, 영상만 든 m4a, 음성+영상 m4a, 120초 m4a, 깨진 m4a, 빈 aac, `c:t.m4a`, `한글 녹음.m4a`, `pipe:0.wma`, 음성 트랙 2개 mkv를 상대·절대로 `classify`했다. 정상 파일은 모두 `audio_features ran`이다. 영상만 든 m4a는 「FfmpegError: … '<root>/video_only.m4a' — 오디오 스트림 없음」, 깨진 m4a는 「… ffmpeg 실패(종료 코드 183)」, 빈 파일은 「파일이 비어 있습니다」다. 101 MB m4a는 크기 상한으로 먼저 거부된다. PATH에 ffmpeg가 없으면 `skipped` 「의존성 부재: ffmpeg」다. 웹 `/api/scan`, `/api/report` 재분석, `/api/analyze-upload`(한글 이름), api `/api/classify`도 `ran`이다. ffmpeg 시간 초과(주입)는 `failed` 「TimeoutExpired…」이고 임시 WAV가 남지 않는다. 증거 폴더 변화 0(QA-IN-1). |
| R25-6 | **본래 증상은 종료. 수정이 새 구멍을 냈다 → R26-1. 같은 종류의 잔여 → R26-4** | zip·tar에 26가지 이름을 넣었다. `c:v.png`, `2026:10:15.png`, `a:b:c.png`, `dir:x/y.png`, `x/C:/y.png`, `:lead.png`, `trail:.png`, `..:x.png`, `CON:.png`, `nul:stream.png`는 분석된다. `/abs.png`, `//srv/share/a.png`, `\\srv\share\b.png`, `C:/x.png`, `C:\y.png`, `c:`, `../e.png`, `a/../../e2.png` 등은 각자의 사유로 거부된다. 추출 폴더 밖이나 `/`에 쓰인 파일은 0이다(zip-slip 차단 유지). 폴더의 `c:v.png`와 압축 파일 안의 `c:v.png`는 sha256과 결론이 같다. **그러나 `inner.zip::b.png`처럼 `::`가 든 구성원 이름이 이제 받아들여져, 중첩 압축 파일 구성원과 식별자가 겹친다(R26-1).** |
| R25-7 | **부분(잔여 → R26-2)** | 깨진 wav/m4a/pdf/mp4, `c:x.wav`, `it's.wav`, `q"d.wav`, `<root>.wav`, `a b.wav`, `sub/bad.wav`, `xsub/bad.wav`, 비UTF-8 이름(`\xff\xfe.wav`, `\xc1bad.m4a`)을 절대, `scan case`, `cd case; scan .`, `scan sub/..`, 심볼릭 링크 cwd(상대·`.`·절대), macOS `/tmp`(→`/private/tmp`) 아래에서 돌렸다. 디코더 사유는 모두 `<root>/…`로 같고 절대 경로 누출은 0이다. `classify` 상대·절대·링크 cwd도 같다. **그러나 읽을 수 없는 파일(R25-2의 새 경로)과 압축 해제 실패는 `cd case; scan .`와 `--workers 4`에서 `lock.wav`(접두 없음)로 쓰인다. NBSP 같은 repr 이스케이프 문자가 든 이름도 `cd case; scan .`에서 접두가 빠진다(R26-2).** |
| R25-8 | **종료(인접 잔여 → R26-7)** | `help`, `help -h`, `help help`, `help help -h`, `help -h -h`, `help --help --help`, `help help --help`, `-h help`, `help scan -h -h`, `help corpus build -h`, `help vendor-weights pin -h`는 0이다. `help scan bogus -h`, `help bogus -h`, `help -h scan`은 2다(타당). **`help help scan`과 `help help help`는 2이고 「허용되지 않은 값 'help'」라는 사실과 다른 문구를 낸다(R26-7).** |
| R25-9 | 종료 | `multimodal --av-sync`를 음성 있는 mp4, 음성 없는 mp4, 음성 트랙 2개 mkv, `c:v.mp4`, `한글:v.mp4`, `-i.mp4`, `%d.mp4`에 상대·절대로 돌렸다. 음성이 있으면 `av_sync_motion ran`·`av_sync_audio ran`이고 점수가 나온다. 음성이 없으면 `skipped` 「오디오 트랙 없음(…)」, PATH에 ffmpeg가 없으면 `skipped` 「의존성 부재: ffmpeg」, 주입한 시간 초과·librosa 예외는 `failed`와 실제 사유다. 10분 영상은 2.4 s다. 진행 중 SIGINT는 rc 130이고 임시 WAV가 남지 않는다. SIGKILL 뒤에는 26 MB 임시 WAV가 세션 폴더에 남는다. 이 폴더는 1시간(`STALE_MIN_AGE_SECONDS`) 뒤 다음 실행이 지운다(설계대로). `.m4a`는 「지원되지 않는 형식」 rc 2다(문서화된 영상 확장자 목록). |

원래 결함도 다시 확인했다.

- R18-3 부분 보고서와 R23-3 취소 계수: 위 R25-1 탐침에서 서버 값(138/137/162)이 쓰였다.
- R19-13: 스캔 뒤 파일을 지우면 409다.
- R20-1: hostile 폴더의 보고서는 200이고 서명된다. verify-report rc 0, 변조 40/40 탐지(세 트리 모두).
- R20-4/5: 위조 `duplicate_of`·식별자 탐침과 불변식 퍼징 범위에서 다시 열리지 않았다.
- R21-1/5 불변식 퍼징: 서버당 300건, 그중 210건은 거부(409/400)였다. 서명 출력에 게시 문자열이 새어 나온 경우는 0이다.
- R22-1 listing: 취소 작업에서는 `scan_job`, 평면 검사와 만료 뒤에는 `walk`다.
- R23-1 상대 경로: hostile 폴더에서 `cd base; scan case`와 `cd case; scan .`가 절대 경로 실행과 같다(행 키 비교, 세 트리).
- R23-5 구성원 생략: `bundle.zip::gen.png`를 빼면 `not_in_report 1`, 압축 파일 전체를 빼도 1이다. 단 R26-1 경로는 제외다.
- R24-1: 위 R25-1 행 참고.
- R19-2 크래시 격리: WAV 24개 중 이 검사의 작업자만 골라 2회 SIGKILL → 2개 `NativeCrashError`, 22개 `ran`, rc 0이다.
- R22-5 차단기: 0.6 s에 모두 `WorkerStartError`다.
- R18-1/R19-1 캐시 키: QA-IN-4에서 해시가 바뀌고 새 해시가 맞으며 적중 0이다. d2239fe(세대 10)가 채운 캐시를 HEAD(세대 12)는 0/10만 적중한다. HEAD 상대 경로 2회차는 10/10 적중하고 콜드 결과와 같다.
- R17 감독: `stress_shutdown --runs 30`에서 실패 0이다. CLI에 SIGINT를 보내면 json·table 모두 rc 130이고 출력 없이 정리 문구가 나오며 자식 프로세스가 남지 않는다.

## 회귀 탐색 요약 (vs d2239fe, 2130892)

- **hostile 폴더**: 52개 항목, 행 53(재귀 164). uid 1000으로 실행했다.
  - 상대(`cd base; scan case`, `cd case; scan .`) = 절대(행 키).
  - CLI = web = api-serve.
  - QA-IN-1: 증거 변화 0. QA-IN-2: 이름을 바꿔도 같다.
  - 서명 변조 40/40 탐지(세 트리). doctor rc 0.
  - **결정성: workers 1 ≠ workers 4(정규화 바이트)가 HEAD에서만 나왔다.** 차이는 `noperm.png` 한 행의 사유(`<root>/noperm.png` vs `noperm.png`)다. 2회 반복해도 같다. d2239fe·2130892는 이 폴더에서 같았다(R26-2).
- **새 회귀**: R26-1(f2fc514). R26-2의 범위 확대(50ef8e8).
- **기존 결함·잔여**: R26-3(R25-4의 비Linux 분기), R26-4(R25-6과 같은 종류 — `~`·예약 장치명), R26-5(문서·문구), R26-6(R25-3 잔여), R26-7(R25-8 인접).
- **성능**: 측정 조건은 다음과 같다.
  - gate 이미지, `--cpus 4`. 측정 시작·끝에 실행 중인 다른 컨테이너는 없었다(내 `r26-perf` 하나, `out/perf-others-*.txt`).
  - 데이터는 컨테이너 `/tmp`에 둔다. 측정마다 새 HOME을 썼다(표시한 항목 제외). 트리 사이에 `/venv/var`를 지웠다.
  - 순서는 2130892 → HEAD → d2239fe → HEAD다(`out/perf.log`).
  - 단위는 초다. HEAD는 두 패스의 값이다.

| 경로 | 2130892 | d2239fe | HEAD (패스 1 / 2) | HEAD/2130892 |
| --- | --- | --- | --- | --- |
| scan 이미지 2000 w1 | 5.74 | 5.71 | 5.71 / 5.74 | 0 % |
| scan 이미지 2000 w4 | 3.80 | 3.67 | 3.82 / 3.70 | -3~+1 % |
| scan WAV 300 w4 (1회차/2회차) | 31.48/27.49 | 16.28/9.01 | 12.83/7.42 / 13.75/8.01 | -56~-59 %/-71~-73 % |
| scan WAV 300 w1 | 33.98 | 18.58 | 18.82 / 18.56 | -45 % |
| scan WAV 300 w4 상대 경로 | 27.13 | 6.90 | 8.44 / 7.42 | -69~-73 % |
| classify WAV 1개, 같은 HOME (×4 평균) | 1.71 | 1.68 | 1.74 / 1.67 | -2~+2 % |
| batch WAV 20 w4 (×3 평균) | 4.83 | 2.87 | 2.96 / 2.43 | -39~-50 % |
| 웹 업로드 WAV 20, 새 서버·새 HOME (×3) | 4.97 | 3.36 | 3.43 / 3.35 | -31~-33 % |
| 웹 업로드 WAV 20, 같은 서버 (×3) | 3.96 | 3.33 | 3.41 / 3.37 | -14~-15 % |
| 보고서 재분석 WAV 300 (×2) | 33.12 | 18.29 | 18.48 / 18.26 | -44~-45 % |

25 % 기준을 넘는 저하는 없다. HEAD와 d2239fe의 차이는 WAV 300 상대 경로 패스 1(+22 %)을 빼면 ±8 % 안이다. 그 값도 패스 2에서는 +8 %라 측정 잡음으로 본다.

## 지적 사항

### R26-1 (Med, 차단, 회귀 — f2fc514 R25-6) 구성원 이름의 `::`가 중첩 압축 파일 구성원과 같은 식별자를 만든다. 정직한 보고서는 400이 되고, 조작 근거 구성원을 빼면 「없음」이 서명된다

**재현** (`z.zip` = `inner.zip`(안에 A1111 메타데이터 PNG `b.png`) + 리터럴 이름 `inner.zip::b.png`(평범한 PNG) + `fake.zip::x.png` + `:lead.png`)

```
scan case --format json   (HEAD)
  'z.zip::inner.zip::b.png'  container z.zip  member 'inner.zip::b.png'  manipulation_evidence
  'z.zip::inner.zip::b.png'  container z.zip  member 'inner.zip::b.png'  undetermined     ← 같은 path·같은 식별자
  'z.zip::fake.zip::x.png'   … 존재하지 않는 중첩 압축 파일처럼 보이는 행
웹 /api/scan → /api/report (검사 결과 그대로)
  → 400 「검사 결과 항목 「z.zip::inner.zip::b.png」가 두 번 게시되었습니다」
웹 /api/report (조작 근거가 있는 쪽 행만 뺌)
  → 200, 서명됨. 서명 행 'z.zip::inner.zip::b.png' undetermined
     listing {source: walk, not_in_report: 0} → 「기록되지 않은 파일: 없음」
     manipulation_evidence 1 (정직한 보고서는 2 — d2239fe는 같은 요청에 not_in_report 1)
```

- d2239fe·2130892는 `:`가 든 구성원을 거부하므로 식별자가 겹치지 않는다. 같은 요청에 `not_in_report 1`이다.
- 원인 1: archives.py:292-310 `_safe_member_name`이 POSIX에서 `::`가 든 이름을 받아들인다. 그런데 중첩 압축 파일 구성원의 이름은 `inner.zip::b.png` 형식의 연결 이름(archives.py:93-99, 119 `member_name`)이다. 두 이름 공간이 겹친다. R12-3의 `claim`(같은 이름 → `#2`)도 이 경우를 보지 않는다.
- 원인 2: webapp_api.py:1811 `rows[row_member] = …`가 구성원 이름을 키로 하므로 뒤의 행이 앞의 행을 덮는다. 그래서 서버 재분석이 조작 근거 행을 「서명되지 않은 구성원」으로 세지 못한다. webapp_api.py:2024의 「두 번 게시」 검사는 정직한 게시를 거부한다.
- 불변식 테스트는 이 구조를 놓친다. `ReportSignedSetIsTheServersInvariantTest` 픽스처에 위 압축 파일 하나(`col.zip`)만 더하면 7개 테스트 중 12개 하위 검사가 실패한다. 정직한 게시가 모두 400이다(`out/n19.log`).
- 표시 문제도 있다. `fake.zip::x.png`처럼 실제로 없는 중첩 압축 파일을 가리키는 행이 HTML·PDF·JSON에 그대로 나온다.

**왜 차단인가**: R23-5·R25-1과 같은 종류다. 서명 보고서가 조작 근거가 있는 구성원 하나를 빼고도 「기록되지 않은 파일: 없음」을 서명한다. 서명된 요약도 거짓이다(2 → 1). 이 보고서는 서명 검증을 통과한다. 반대로 그런 압축 파일이 있는 폴더는 정직한 보고서를 만들 수 없다(GUI도 400).

→ 구성원 행의 식별자를 단사로 만든다. 방법은 둘 중 하나다. (1) 구성원 이름의 각 구성 요소에서 `::`를 이스케이프한다(`escape_row_path`와 같은 규칙). (2) `::`가 든 구성원 이름을 사유와 함께 거부하거나 `claim`으로 `#2`를 붙인다. 회귀 테스트: 위 압축 파일 → 서로 다른 식별자 2개, 정직한 보고서 200, 어느 한 행을 빼도 `not_in_report ≥ 1`. 이 압축 파일을 `ReportSignedSetIsTheServersInvariantTest` 픽스처에 넣는다.

### R26-2 (Low, R25-7 잔여 — R25-2로 범위 확대) 읽을 수 없는 파일의 사유 경로가 호출 방식과 작업자 수에 따라 다르다

**재현** (uid 1000, `chmod 0`)

```
scan /abs/case            → 「PermissionError: [Errno 13] 접근 권한이 없습니다: <root>/lock.wav」
scan case                 → 같음
cd case; scan .           → 「… 접근 권한이 없습니다: lock.wav」   (13개 형식 모두, zip/tar는 「압축 해제 실패(… l.zip)」)
scan /abs/case --workers 4 → 「… 접근 권한이 없습니다: lock.png」 / 「… lock.wav」
classify k.wav / ./k.wav   → 「… k.wav」,  classify /abs/k.wav → 「… <root>/k.wav」
cd case; scan .  (이름 「한글<NBSP>x.wav」, 깨진 WAV) → 「… '한글\xa0x.wav'」, 절대 실행은 「'<root>/한글\xa0x.wav'」
```

- hostile 폴더의 결정성(workers 1 = 4, 정규화 바이트)이 HEAD에서만 깨진다(`noperm.png` 행). macOS에서도 `cd case; scan .`가 같다.
- d2239fe에서도 png·jpg·txt는 같은 현상이 있었다(작은 폴더의 `--workers 4`). 그러나 50ef8e8이 모든 형식의 읽기 실패를 같은 경로로 보내면서 범위가 13개 형식 전부와 압축 파일로 넓어졌다.
- 원인 1: core.py:858-864 `_unreadable_row(item, unreadable)`와 core.py:1063 `failure_reason(exc)`은 `analyze_file`의 `path_scrub_root`/`path_scrub_file`(core.py:1132) 밖에서 실행된다. 스캔 수준의 `path_scrub_root`(core.py:407)는 호출 스레드에만 있고 작업자 스레드에는 없다.
- 원인 2: error_text.py:152의 맨이름 치환은 repr 그대로의 이름만 찾는다. 그래서 `\xa0`·`\t`처럼 repr가 이스케이프하는 문자가 든 이름은 치환되지 않는다.
- 209ceb2 본문의 "every scan, single file, web and report re-analysis path goes through it"은 50ef8e8의 새 경로에서는 사실이 아니다.
- → 읽기 실패 행의 사유도 같은 scrub 문맥 안에서 만든다. 스캔 수준 문맥은 작업자 스레드로 전달한다(`contextvars.copy_context`). 맨이름 치환은 repr 이스케이프 형식도 함께 등록한다. 회귀 테스트: 읽을 수 없는 wav/png/zip과 NBSP 이름의 깨진 wav를 `abs`·`case`·`.`·`--workers 4`로 돌려 사유가 모두 같은지 본다.

### R26-3 (Low, R25-4 잔여 — 비Linux) macOS에서 PATH_MAX보다 깊은 cwd의 2 GiB 초과 비ASCII 이름 영상은 분석되지 않고, 사유도 사실과 다르다

**재현** (macOS 호스트, cwd 1139바이트, 희소 파일)

```
scan b  (b/한글 큰.mp4, 2.2 GB, --max-file-bytes 4 GiB)
  → video_analysis failed 「… 네이티브 디코더용 임시 사본 상한 초과 — 비ASCII 이름의 파일에 심볼릭 링크를
     만들 수 없어 사본이 필요하지만 크기(2306867200바이트)가 사본 상한(2147483648바이트)을 넘어 …」, av_audio skipped
같은 파일을 얕은 폴더에서 절대 경로로: ran / ran
같은 깊은 cwd의 2.2 GB ASCII 이름(ascii big.mp4): ran / ran
같은 깊은 cwd의 600 MB 비ASCII 이름: ran(호출마다 사본을 만든다)
```

- macOS의 PATH_MAX는 1024바이트다. 한글은 글자당 3바이트이므로 한글 폴더 이름이 깊이 쌓인 증거 폴더에서 흔히 닿는다.
- 원인: native_path.py:1205-1225 `_reachable_name`은 Linux에서만 `/proc/<pid>/cwd/…`를 쓰고, 그 밖에서는 상대 이름을 돌려준다. 그러면 `_stage`가 심볼릭 링크를 만들지 않고 사본으로 간다(native_path.py:1188-1196). 2 GiB(`NATIVE_COPY_MAX_BYTES`)를 넘으면 거부된다. 거부 사유 「심볼릭 링크를 만들 수 없어」는 사실과 다르다. 링크를 만들지 않기로 한 것이다.
- → macOS·BSD에서는 열린 파일 디스크립터(`/dev/fd/N`, 자식에게는 `pass_fds`)나 `chdir_any`로 만든 짧은 상대 이름으로 링크를 만든다. 아니면 적어도 사유를 사실대로 적고 문서에 남긴다. 회귀 테스트: 깊은 cwd + 비ASCII + 상한 초과 크기(희소 파일, 상한을 패치해 작게)를 macOS 분기(mock)로 돌린다.

### R26-4 (Low, R25-6과 같은 종류 — 기존 결함) `~`로 시작하는 이름과 Windows 예약 장치명은 POSIX에서도 압축 파일 안에서만 거부된다

**재현**

```
office.zip = ~$보고서.docx, ~WRL0001.tmp, ~x.png, con.png, …
→ 「~$보고서.docx: 홈 폴더 표기('~')로 시작하는 멤버(대상 폴더 밖 쓰기 시도로 취급)」
   「con.png: 허용되지 않는 이름(Windows 예약 장치명)」 — 추출·분석하지 않음
같은 파일들을 폴더에 두면 모두 분석된다.
```

- `~$…`는 Office가 문서를 열 때 만드는 소유자 파일이고, `~WRL….tmp`도 Word 임시 파일이다. 업무 폴더를 통째로 압축한 증거에 흔하다.
- POSIX에서 `open()`은 `~`를 펼치지 않는다. 추출 대상도 색인 이름(`_member_target`)이다. 그래서 이 이름들은 폴더 밖 쓰기와 무관하다.
- R25-6은 `:`에 대해 바로 이 이유("POSIX에서는 보통 이름, 대상은 색인 폴더")로 고쳤다. 그러나 `~`와 예약 장치명은 그대로 두었다. 폴더 검사와 압축 파일 검사가 다르다(규칙 "같은 결과").
- 위치: archives.py:186, 302(`name[0] == "~"`), 310(`_WINDOWS_RESERVED`, 모든 OS).
- → `:`와 같이 Windows에서만 거부한다(`_colon_names_unsafe` 같은 판정). 사유에서도 「대상 폴더 밖 쓰기 시도로 취급」을 뺀다. 회귀 테스트: 위 4개 이름이 POSIX에서 분석되고 Windows(패치)에서 사유와 함께 거부되는지 본다.

### R26-5 (Cosmetic, 문서·문구 — R25-1 인접) 취소된 `/api/scan/stream`의 `partial`은 보고서로 만들 수 없는데, 400 문구가 사실과 다르고 문서에도 없다. 서비스 문서에 R18-3 이전 설명이 남아 있다

**재현** (api-serve 실제 프로세스, `a0.zip`(PNG 80개) + PNG 40개, 1 s 뒤 `/api/jobs/{id}/cancel`)

```
cancelled 이벤트: done 23, files_cancelled 98, partial.items 23행(a0.zip 'cancelled')
POST /api/report {partial 그대로}                → 400
POST /api/report {partial 그대로, scan_job: 스트림 job_id} → 400
  「… scan_job이 이 서버가 이 폴더의 검사를 취소로 끝낸 검사 작업이 아닙니다(없음·만료·다른 폴더·완료된 작업) …」
POST /api/report {취소 컨테이너 행을 뺀 partial}   → 200, cancelled false, not_in_report 98
```

- 스트림 작업은 이 서버가 이 폴더의 검사를 취소로 끝낸 작업이다. 그러나 `_SCAN_JOBS`(비동기 `/api/scan` 작업)에만 있는 작업을 찾으므로 400이 된다. 문구의 괄호 목록 어디에도 해당하지 않는다.
- 서비스 문서의 `/api/scan/stream` 행은 `partial`을 "the `/api/scan` payload of the rows done"이라고만 적는다. 압축 파일이 걸린 취소 스트림의 `partial`은 `/api/report`에 보낼 수 없다는 사실은 적지 않는다.
- 같은 문서 207행(`/api/scan-cancel` — "returns partial results as `done`")과 249행("partial results still come back as `done`")은 206행·R18-3과 모순된다. 실제로는 `status: cancelled`다(이번 탐침에서도 확인).
- 위치: webapp_api.py:1527 `REPORT_ROW_CANCELLED_NOT_CANCELLED_SCAN`, `_finished_scan_job`(webapp_api.py:2252), docs/deepfake-lens-service.md:138, 207, 249.
- → 스트림 작업의 결과도 `_SCAN_JOBS`처럼 서버에 보관해 `scan_job`으로 받는다. 아니면 문구와 문서에 "스트림 검사의 부분 결과는 압축 파일이 취소되었으면 보고서로 만들 수 없음 — 비동기 검사(`async=1`)를 쓰십시오"를 적는다. 207·249행은 `cancelled`로 고친다.

### R26-6 (Cosmetic, R25-3 잔여 — 커밋 메시지) PDF 안에서 같은 문자 `|`가 사건 필드에서는 `x|y`, 파일·폴더 이름에서는 `x\|y`로 찍힌다

- `scan case_x|y --pdf-out f.pdf --evidence-statement-pdf-out e.pdf --law-firm "x|y" --case-no "x|y"`:
  - f.pdf: 머리글 `x|y 디지털포렌식 감정센터`, 파일 행 `fx\|y.png`.
  - e.pdf: 사건 `x|y`, 증거 설명 `(fx\|y.png)`.
- 웹 `format=pdf`의 검사 폴더 줄은 「검사 폴더(…): case_x\|y \\u05e2」다. 사건 필드는 `x|y`다.
- c700e85 제목은 "the scan folder name and every case field print through one injective PDF form; "|" prints as typed"라고 적는다. 그러나 검사 폴더 이름은 PDF 형식(`pdf_field_text`)이 아니라 Markdown 형식(`display_name`, reports.py:200-205)을 거친다. 본문은 `display_name`이라고 적어 제목과 다르다.
- 커밋 본문의 근거("A PDF has no table syntax")라면 PDF의 파일·폴더 이름도 `|`를 그대로 찍어야 한다. 단사성은 어느 쪽이든 유지된다.
- → PDF 렌더러(reports `_render_forensic_pdf`, evidence_statement `_render_statement_pdf`, `scan_root_line`의 PDF 호출)의 이름도 `pdf_field_text`로 통일한다. 아니면 제목을 정정하고 두 형식을 쓰는 이유를 문서에 적는다.

### R26-7 (Cosmetic, R25-8 인접) `help help scan`, `help help help`가 「허용되지 않은 값 'help'」를 낸다

- `help scan scan` → 「deepfake-lens scan: 오류: 알 수 없는 인수: scan」(정확하다).
- `help help scan` → 「deepfake-lens: 오류: 인수 <명령>: 허용되지 않은 값 'help' (선택 가능: 3d, agent, …)」, exit 2. 선택 목록에 `help`가 없어 문구가 사실과 다르다(25차 R25-8이 지적한 문구와 같다).
- 위치: cli.py:600-614. `help` 주제에 남은 단어가 있으면 최상위 parser.error로 간다.
- → `help help <단어>`는 「알 수 없는 인수: <단어>」로 처리한다. 아니면 선택 목록에 `help`를 넣는다.

## 잔여 1단계 리스크

- 웹 보고서의 무결성은 "서버가 다시 만든 행 집합 = 서명 집합"에 기대고 있다. 이번에는 서버 자신의 행 식별자가 단사가 아니어서 구멍이 생겼다(R26-1). 식별자 단사성(같은 `(container, member)`가 서버 쪽에서 두 번 나오지 않음)을 스캔 단계의 불변식으로 검사하는 편이 안전하다.
- `--av-sync`는 영상 전체 음성 트랙을 22 050 Hz 16비트 모노 임시 WAV로 풀고 librosa가 float32로 읽는다. 2시간 영상이면 임시 파일 약 317 MB, 메모리 약 635 MB다. 상한·분할이 없다. 음성 트랙이 여럿이면 첫 트랙만 본다(음성 트랙 2개 mkv: 첫 트랙이 무음이면 「엔벨로프가 너무 짧거나 분산이 없어」, `av_sync_audio ran`).
- ffmpeg 시간 초과 사유는 「TimeoutExpired: 라이브러리 오류(TimeoutExpired)」로, 무엇이 몇 초에 끊겼는지 말하지 않는다.
- SIGKILL로 죽은 실행의 임시 WAV는 1시간 동안 남는다(설계).
- 적합성 기록(CONFORMANCE)은 여전히 API venv에서 만들어지고, librosa·soundfile·c2pa가 없다고 적힌다. qa-main은 별도로 통과한다.
- Windows 경로·ACL은 mock으로만 검증된다(합의 사항). `/proc`가 없는 OS의 깊은 cwd 처리(R26-3)는 이번에 macOS에서만 확인했다.
- 게이트 이미지 재빌드는 이번에도 네트워크 의존이라 시도하지 않았다.
- 이전 라운드 잔여(제자리 매니페스트 수정 미감지, 골든 출력의 mediapipe/torch 미포함, 비Linux SIGKILL 자손 정리 최선 노력 등)는 유지된다.

## 게이트 0 판정: 아니오

수정 목록: R26-1(차단), R26-2, R26-3, R26-4, R26-5, R26-6, R26-7.

## 부록

### 게이트·기록

- 측정 조건: git 이력이 있는 사본, 로컬 main=dad9730. 시작 시점에 다른 세션 컨테이너는 없었다.
- `gate.sh full`은 rc 0이고 20분 52초 걸렸다. 탐침 컨테이너 1개가 동시에 돌았다.
  - /venv: Ran 1854, OK(skipped=103). /venv-api: Ran 1854, OK(skipped=60).
  - ruff, mypy(131 파일) 통과. coverage 80 %.
  - cli_smoke 45/26, 측정 게이트 10/0, sync_model_docs, verify_contracts(3+2), stress 30/0.
  - qa-main: 202 테스트, 0 실패, 19/0/4/12/1.
- `gate.sh gui`는 rc 0이다(두 단계 PASS, 18 s).
- qa-main 단독 실행(`/marker` 이후): 새로 쓰인 것은 `/tmp/qa-main`(1.7 MB), `/tmp/qa-main-venv`(1.5 MB), `/work`, gate.sh 머리 부분의 `/root/.gitconfig`뿐이다. `/tmp/pp`는 이미지에 원래 있다. `__pycache__`는 없다.
- `qa_phase0.py --verify-record`: "e98b10b5fe2d에서 생성되었고 그 뒤 다른 변경이 없습니다". `build_traceability_commits.py --check`: 커밋 352개(dad9730..e98b10b), 표 이후 1개(재생성 커밋만). R25-1~R25-9가 `ids[]`에 있고 각 커밋·갭이 맞다.
- CONFORMANCE의 1854/60은 게이트 실측(/venv-api)과 같다.
- 커밋별 게이트(`git archive` 트리, 이력 없음): 코드가 바뀐 9개 커밋(c464ac3..8483bf4) 모두 두 venv 단위 테스트, ruff, mypy가 OK다(1839→1854, skip 105→106 / 58→63). 문서 커밋 bf484c4·e359405·c5b155c·e98b10b·037ade7은 문서 파일만 바꾼다.
- 각 수정 커밋의 변경된 테스트 모듈을 부모 소스에서 실행한 결과(임시 venv)는 아래와 같다. 모든 수정 커밋의 새 테스트가 부모에서 실패한다.

  | 커밋 | 결과 |
  | --- | --- |
  | c464ac3 (R25-1) | F30 |
  | 50ef8e8 (R25-2) | F1 |
  | c700e85 (R25-3) | F3 E1 |
  | 2d10ee0 (R25-4) | F1 E1 |
  | dfc5889 (R25-5) | F5 |
  | 209ceb2 (R25-7) | F1 E1 |
  | f2fc514 (R25-6) | F2 E2 |
  | fcf56fe (R25-8) | F4 |
  | 8483bf4 (R25-9) | E3 |

- 테스트 약화·삭제는 없다. 바뀐 기대값 3곳(`test_a_cancelled_container_row_needs_a_cancelled_report`의 200→400 두 줄, `file.txt:ads`, `audio.py` 자식 호출 2→3)은 모두 결함을 고정하던 값이고 ID 주석이 있다.
- 반복 실행: 13개 모듈(493 테스트)을 /venv·/venv-api·임시 venv에서 각 3회 돌렸다. 9/9 OK이고 플레이크는 0이다.
- 얕은 클론(depth 1, /venv-api): 1854개 중 문서화된 2개 테스트(QaSys10 deletion reasons 9, test_shutdown history 5)만 실패한다(하위 테스트 14). QA-ENV-DEPENDENT-TESTS와 일치한다.

### 커밋 메시지 정확성

- f2fc514: "On POSIX "c:v.png" is an ordinary name"은 맞다. 그러나 `::`가 든 이름이 중첩 구성원 이름과 겹친다는 점을 다루지 않았다(R26-1).
- 209ceb2: "every scan, single file, web and report re-analysis path goes through it"은 50ef8e8의 읽기 실패 경로에서 사실이 아니다(R26-2).
- c700e85: 제목의 "the scan folder name … print through one injective PDF form; "|" prints as typed"는 사실이 아니다. 검사 폴더 이름은 `display_name`을 거쳐 `\|`로 찍힌다(R26-6).
- 2d10ee0: 본문은 비Linux 분기를 정확히 적었다("elsewhere the relative name itself … the staged copy is made from it"). 다만 2 GiB 상한과의 상호작용(R26-3)은 적지 않았다.
- 나머지(c464ac3, 50ef8e8, dfc5889, fcf56fe, 8483bf4) 본문은 탐침 결과와 맞다. dfc5889의 "audioread를 넣어도 바뀌지 않는다"는 검증하지 않았다(이미지 재빌드 없음).

### 테스트 감사 보충

- `ReportSignedSetIsTheServersInvariantTest`의 강도:
  - 퍼징 대상은 `kind`·`status`·`duplicate_of`·`result`·행 생략·`summary.*`·`scan_job`이다.
  - `container`/`member` 필드, 업로드 행, `options`는 퍼징하지 않는다(별도 탐침에서 각각 400/409/정직).
  - 결정적 약점은 픽스처의 이름 공간이다. 서버 쪽 식별자가 겹치는 압축 파일이 없다. 그런 압축 파일 하나를 넣으면 12개 하위 검사가 실패한다(R26-1).
- R25-2 테스트(`UnreadableMediaRowTest`)는 사유의 경로 표기를 보지 않는다. 실행 형식도 절대 경로뿐이고 `--workers 4`를 쓰지 않는다(R26-2).
- R25-7 테스트는 깨진 파일만 다루고 읽을 수 없는 파일과 repr 이스케이프 이름을 다루지 않는다(R26-2).
- R25-4 테스트는 Linux 경로만 실제로 돈다. 비Linux 분기의 사본 상한은 다루지 않는다(R26-3).
- R25-6 테스트는 `::`가 든 이름과 `~`·예약 장치명을 다루지 않는다(R26-1, R26-4).

### WP·규칙·QA 대조

- WP-A~J는 구현되어 있고, WP-J의 두 게이트(full, gui)가 통과한다.
- 공통 규칙 1~7:
  - fail-closed는 지켜진다(읽기 실패·디코드 실패·의존성 부재 모두 판단 불가 + 실제 사유).
  - 서명 범위 규칙과 §6의 새 규칙("서명·계수를 바꾸는 행 상태·종류는 서버가 가진 상태로만")은 R26-1이 어긴다. 서버가 가진 상태 자체(구성원 식별자)가 단사가 아니다.
  - "CLI/GUI/API 같은 결과"는 R26-2(행 사유 텍스트, workers)와 R26-4(폴더 vs 압축 파일)에서 어긋난다.
- QA 시나리오:
  - QA-IN-1/2/4: 통과(m4a·av-sync 임시 WAV 포함).
  - QA-OUT-1/2/3/5/6, ADV-1/2/3, SYS-1/2/3/6/7/9: 기존 테스트와 이번 탐침 범위에서 통과.
  - QA-OUT-4: R26-2(`.` 루트·작업자 수에 따른 사유 텍스트)는 예외다.
  - QA-SYS-6: 변조 40/40 탐지. 다만 R26-1의 생략 보고서도 검증을 통과한다.
- CONFORMANCE의 "QA-OUT-4 통과"는 하네스 범위에서 맞다.
- HANDOFF 사실 대조:
  - §1·§2: 25회, R25-1~R25-8 + R25-9 수정, 출력 형식 세대 12는 맞다(scan_cache.py `OUTPUT_FORMAT_GENERATION = 12`).
  - §4: GUI 스모크 안내대로 통과한다.
  - §5: qa-main은 `/tmp` 아래에만 쓴다(`/root/.gitconfig`는 gate.sh 공통 머리 부분).
  - §6: 새 규칙(서버 상태 + 불변식 테스트)은 문서에 있다. R26-1은 그 불변식 테스트의 픽스처 범위 밖이다.
  - §9: 파일 목록이 맞다.

### 탐침 목록 (이전에 다루지 않은 영역, 46종)

1. 구성원 이름 `inner.zip::b.png`와 중첩 구성원의 식별자 충돌(zip, HEAD·d2239fe)
2. 같은 폴더의 정직한 보고서(400)
3. 조작 근거 구성원 행만 뺀 보고서(200, 「없음」)
4. 존재하지 않는 중첩 이름 `fake.zip::x.png`, `:lead.png`, `a::`
5. 불변식 테스트 픽스처에 충돌 압축 파일 추가(12개 실패)
6. 25차 위조 A~G4 재실행 × 2서버
7. 구성원 행 `cancelled`(경로 기준 재작성)
8. 비취소 검사 + `summary.cancelled` 게시
9. 업로드 행과 폴더 행 이름 겹침
10. 취소 작업 만료(TTL 축소) 뒤 보고서
11. 만료 뒤 취소 컨테이너 행을 뺀 보고서
12. 실제 api-serve 스트림 취소 `partial` → 보고서(작업 id 없음·스트림 id·컨테이너 제외)
13. dedupe + 같은 내용 압축 파일 2개 + 취소 3회
14. 위조 `container`/`member`/`member_index` 9종
15. 읽을 수 없는 파일 13형식 × abs/rel/dot × 2트리 + classify + 웹
16. 분석 중 실제 변경 8가지 × 2트리(실제 변경 ↔ 읽기 실패 오분류 탐색)
17. 사유 경로 표기 5가지 호출(심볼릭 링크 cwd 포함)
18. classify 절대·상대·`./`·링크 cwd
19. workers 1 vs 4 결정성(hostile, 작은 폴더, 웹, 보고서)
20. NBSP·비UTF-8 이름의 디코더 사유
21. 특수 이름 8종의 scrub 치환 범위(`<root>.wav`, 따옴표, `sub/`·`xsub/`)
22. av-sync 9개 입력 × abs/rel
23. av-sync PATH에 ffmpeg 없음
24. ffmpeg 디코딩 오디오 12개 입력 × abs/rel
25. m4a PATH에 ffmpeg 없음
26. 깊은 cwd(4442바이트) scan(workers 2)과 av-sync
27. QA-IN-1(m4a·av-sync 후 증거 폴더)
28. av-sync 중 SIGKILL 뒤 임시 WAV와 다음 실행
29. av-sync 중 SIGINT
30. 10분 영상 av-sync
31. ffmpeg 시간 초과·librosa 예외 주입과 임시 WAV 정리
32. 101 MB m4a(크기 상한 우선)
33. m4a 웹 검사·보고서 재분석·업로드(한글 이름)·api classify
34. macOS 깊은 cwd(1540바이트) 비ASCII 영상·m4a·wav·av-sync
35. macOS `/tmp` 심볼릭 링크 아래 사유 표기
36. macOS 깊은 cwd 2.2 GB·600 MB 비ASCII·ASCII 영상(희소 파일)
37. PDF 사건 필드 5값 × 3 PDF
38. `|`가 든 폴더·파일 이름의 CLI·웹 PDF
39. zip/tar 구성원 이름 26종(zip-slip 포함)
40. Office `~$`·`~WRL`·예약 장치명 구성원
41. `:` 이름의 폴더 vs 압축 파일 일치(+`--redact-paths`)
42. help 18종
43. stream `recursive`·`max_files`, `/api/scan` 숫자 매개변수
44. hostile 3트리(결정성·상대·web/api·변조·doctor)
45. 원래 결함 재확인(QA-IN-4, 차단기, R19-2 SIGKILL, R19-13, R23-5, 캐시 세대 10→12, SIGINT)
46. qa-main 쓰기 범위, 얕은 클론, 3 venv × 3회 반복 실행

### 컨테이너와 파일

- 사용한 컨테이너: r26-full, r26-p, r26-gui, r26-sh, r26-fl, r26-qa, r26-perf, r26-v. 동시에 최대 2개였고, 성능 측정 때는 r26-perf 하나만 실행했다. r26-p는 직접 멈췄고 나머지는 `--rm`이다.
- 탐침 스크립트와 로그는 `scratchpad/r26/out/`에 있다(q/n*.py·m*.py, gate_full.log, gate_gui.log, percommit.log, perf.log, flakes.log, shallow.log, qa_main_scope.log, n*.log, m*.log, r6_hostile.log, r9_invariant.log, r10_originals.log, c1_cancel_abuse.log, r26norm-*.json). 같은 내용을 `~/Desktop/deepfake-lens-verify-archive/r26-out/`에 복사했다.
- 원본 저장소 세 곳은 변경하지 않았다.

## 조정자 추가 — 잔여 리스크에서 승격

인계서 6장의 "면제 없음" 규칙에 따라 위 「잔여 1단계 리스크」 중 이 브랜치에서 고칠 수 있는 세 항목을 수정 대상으로 올린다.

### R26-8 (Low, 잔여 리스크 승격 — R26-1 인접) 행 식별자의 단사성을 스캔 단계에서 검사하지 않는다

- 웹 보고서의 무결성은 "서버가 다시 만든 행 집합 = 서명 집합"에 기댄다. 그런데 서버 자신의 행 식별자(`(container, member)`와 `::` 연결 이름)가 겹치는지는 어디에서도 검사하지 않았고, R26-1은 그 틈에서 나왔다.
- → 스캔 결과의 모든 행 식별자가 서로 다르고, 식별자 → (경로, 구성원 사슬) → 식별자로 되돌아오는지를 코어에서 확인한다. 어기면 결과를 내지 않고 실패한다(fail closed). 단위 테스트와 보고서 불변식 테스트(`ReportSignedSetIsTheServersInvariantTest`, `ReportSignsNoPostedTextInvariantTest`) 픽스처에 겹쳐 보이는 이름(`inner.zip::b.png`, 중첩 `a.zip` 안의 `b.zip::c.png`, NFC/NFD 변형, 이스케이프 문자 자체가 든 이름)을 넣는다.

### R26-9 (Low, 잔여 리스크 승격 — R25-9 인접) `--av-sync`가 음성 트랙 전체를 풀고 첫 트랙만 본다

- 영상 전체 음성 트랙을 22 050 Hz 16비트 모노 임시 WAV로 풀고 librosa가 float32로 읽는다. 2시간 영상이면 임시 파일 약 317 MB, 메모리 약 635 MB이고 상한·분할이 없다.
- 음성 트랙이 여럿이면 첫 트랙만 본다. 첫 트랙이 무음이면 「엔벨로프가 너무 짧거나 분산이 없어」로 끝나고 다른 트랙은 보지 않으며, 그 사실을 적지 않는다.
- → 엔벨로프가 필요로 하는 만큼만(낮은 표본율 모노, 상한 길이) 디코딩하고 상한을 한계에 적는다. 음성 트랙이 여럿이면 어느 트랙을 왜 골랐는지(또는 각 트랙을 본 결과)를 적는다.

### R26-10 (Cosmetic, 잔여 리스크 승격) ffmpeg 시간 초과 사유가 무엇이 몇 초에 끊겼는지 말하지 않는다

- 사유가 「TimeoutExpired: 라이브러리 오류(TimeoutExpired)」뿐이다.
- → 시간 초과 사유에 작업(오디오 디코딩·영상 음성 트랙 추출 등), 파일(`<root>/…` 표기), 경과 시간과 상한을 적는다.
