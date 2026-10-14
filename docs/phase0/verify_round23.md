# 독립 검증 라운드 23 결과 (HEAD 85ad114)

검증자는 0단계 구현에 참여하지 않았다. 사본은 `scratchpad/r23/repo`(`git clone`, 로컬 `main`=dad9730)이고 원본 저장소는 건드리지 않았다. 기준 환경은 Docker 이미지 `deepfake-lens-gate`(aarch64, Python 3.13.16, ffmpeg 7.1.5, Node 20.19.2, `--cpus 4`)이다. HANDOFF §7의 합의된 결정은 다시 지적하지 않았다.

**게이트 0 판정: 아니오.** 수정할 항목은 R23-1~R23-13의 13건이다. 그중 차단 항목은 R23-1, R23-2, R23-3, R23-4의 넷이다.

## R22 수정 종료 확인 (새 탐침)

| ID | 결과 | 새 탐침 |
| --- | --- | --- |
| R22-1 | 부분 종료 | 상한(`max_files` 1/3), 평면 스캔, 행 누락, `max_files` 0/-1/abc/3.5/true(400), 10^21(통과), 가짜/사전형 `scan_job`(walk로 대체), 스캔 뒤 추가된 파일(not_in_report 1), 5만 파일 폴더 walk(0.8–1.0 s, 20f73bf 0.01 s) 모두 서버 값으로 서명된다. **열린 구멍 둘**: 취소된 검사 작업(R23-3)과 압축 파일 구성원 행 누락(R23-5). |
| R22-2 | 실제 Windows에서는 미종료(정적 분석) | 순수 함수 `dacl_parent_safe`에 기본 `C:\ProgramData`가 상속하는 DACL을 넣으면 False가 나온다(R23-6). mock 테스트는 통과한다. |
| R22-3 | 종료(잔여 2건) | 비root로 10개 시나리오를 시험했다(HOME 하위 폴더 제공, `cd ~; web`=409, XDG 밖, 상대 HOME=409, 상대 재정의=409, 증거 안 재정의=409, 데이터 폴더가 증거로 가는 심볼릭 링크=409, 이전 2회 멱등(피드백 줄 중복 없음, 원본 불변), 재정의와 이전, 쓰기 불가 폴더). 증거 폴더 변화는 모든 경우 0이다. 잔여는 R23-7, R23-8. |
| R22-4 | 종료 | **실제 macOS APFS**(대소문자 무시·정규화 무시)에서 시험했다. `GEN.png`, `Gen.PNG`, NFD 「한글」, `SUB/inner.png`, `sub/INNER.png`, `BUNDLE.ZIP::…`은 400이다. 구성원 이름 대소문자 변형은 409, `scan_root` 대소문자 변형은 403, 정당한 행은 200이다. 큰 폴더에서도 폴더별 목록 캐시로 비용이 작다. |
| R22-5 | 종료 | 실제 `scan`(WAV 6개, 준비 한도 0.01 s 패치)에서 작업자 2개가 기동 실패하고 나머지 4개는 즉시 차단기 사유로 `failed`가 된다. 전부 `undetermined`, 0.4 s다. |
| R22-6 | 종료 | NUL은 두 서버의 보고서 행, scan_root, heatmap, analyze-file, preview, heatmap, scan에서 400이다. 경로형 매개변수 25여 개에 잡값 14종(NUL, 고립 서로게이트, `\x7f`, U+202E, `~nouser`, `/proc/self/environ`, 5000자 …)을 넣었을 때 5xx는 2건뿐이다(R23-9, 기존). |
| R22-7 | 종료 | `/venv`(pymupdf 없음)에서 `format=evidence`와 `pdf`는 0.01 s에 501이다(재분석 전). html은 200이다. |
| R22-8 | 종료 | 이름 12종 시험: `../`, 절대 경로, `a/./b`, 256자, `a//b`, `\\srv\x`는 400이다. 255자, 한글 255자, 개행, U+202E, `C:\x.png`(POSIX 서버)는 200이고 `name_source: "client"`로 서명된다(R22-6 합의 범위). |
| R22-9 | 성능은 종료, **새 결함** | 성능은 아래 표 참고. 증거 폴더 안 venv(`scan`)는 쓰기 0, 읽기 전용 venv(uid 1000)는 정상, 0777로 미리 심은 루트와 심볼릭 링크 루트는 사용하지 않는다. 동시 4개 게시도 결과가 같고 published는 하나, 잔여물은 0이다. **그러나 분석 파일의 폴더 안 venv에는 쓴다(R23-2).** |
| R22-10 | 종료(잔여 표현 1건) | 12종 시험. `help vendor-weights pin`이 오해를 부르는 오류를 낸다(R23-11). |
| R22-11 | 종료 | HANDOFF §1·§2·§9는 사실과 맞는다. |

원래 결함도 다시 확인했다.

- R19-13: 스캔 뒤 파일 이동은 409.
- R20-1: FIFO·순환 행이 서버 사유로 서명된다(hostile).
- R20-4/5: 위 탐침 범위에서 다시 열리지 않았다.
- R21-1/5 불변식: 게시 문자열 퍼징 150회 × 2서버 × json/html에서 서명 출력에 나타난 경우 0.
- R19-2 크래시 격리: 스캔 중 작업자를 두 번 SIGKILL하면 그 2개 파일만 `NativeCrashError`이고 22개는 `ran`, 프로세스 rc 0.
- R18-1/R19-1 캐시 키: QA-IN-4(마지막 바이트 변경 + `touch -r`)에서 재분석되고 새 해시가 맞다.
- R17 감독: `stress_shutdown --runs 30`을 2회 돌려 실패 0.

## 회귀 탐색 요약 (vs 20f73bf, 2130892)

- **hostile 폴더**: 52개 항목, 행 53(재귀 164). 비UTF-8 이름, 개행·백슬래시·`-`로 시작하는 이름, 240자, NFD, 이모지, 빈·잘린·위장 파일, 깨진 wav/mp4/docx, 텍스트, PDF, zip slip, 3중 중첩 zip, 50 MB zip 폭탄, 심볼릭 tar, 외부·순환·끊긴·자기 링크, FIFO, 숨김 파일, 권한 없는 파일, 중복, 하위 폴더를 넣었다. 비root 사용자로 실행했다.
  - 세 트리의 정규화 결과 217행이 모두 동일하다.
  - 결정성: workers 1과 4가 같고, 이름 변경 후에도 바이트 동일.
  - CLI = web = api-serve.
  - QA-IN-1: CLI·web·api·보고서 전후 증거 변화 0.
  - 서명 변조 40필드 모두 탐지(40/40). verify-report rc 0. doctor rc 0.
- **새 회귀 1건(R23-1)**: 상대 경로로 주어진 오디오의 `audio_features`가 2d053dd부터 항상 `failed`다. 지금까지의 검증은 모두 절대 경로를 썼기 때문에 드러나지 않았다.
- **성능**: 아래 표. gate 이미지, `--cpus 4`, 각 트리를 단독 컨테이너에서 순차 실행했다. 단, 다른 세션의 `gui-gate` 컨테이너가 같은 호스트에서 돌고 있었다. 새 HOME 조건이다. HEAD는 root라 `/venv/var`에 환경 캐시를 만든다. 2130892는 site-packages 캐시를 쓴다. 값은 초이고, 여러 번 잰 항목은 중앙값이다.

| 경로 | 2130892 | 20f73bf | HEAD | HEAD/2130892 |
| --- | --- | --- | --- | --- |
| scan 이미지 2000 w1 | 9.53 | 6.91 | 6.66 | -30 % |
| scan 이미지 2000 w4 | 6.63 | 4.35 | 4.30 | -35 % |
| scan WAV 300 w4 (1회차/2회차) | 37.0/32.8 | 15.5/17.1 | 22.4/11.4 | -39 %/-65 % |
| scan WAV 300 w1 | 38.5 | 25.9 | 20.1 | -48 % |
| batch WAV 20 w4 (×3) | 6.19 | 11.39 | 3.99 | -36 % |
| 웹 업로드 WAV 20, 새 서버·새 HOME (×3) | 5.63 | 7.81 | 4.41 | -22 % |
| 웹 업로드 WAV 20, 같은 서버 (×3) | 4.12 | 4.42 | 4.50 | +9 % |
| 보고서 재분석 WAV 300 (×2) | 33.7 | 25.6 | 23.3 | -31 % |

25 % 기준을 넘는 항목은 없다. 다만 HEAD의 WAV 300 첫 회(22.4 s)는 20f73bf 첫 회(15.5 s)보다 느렸다. 한 번만 잰 값이고 다른 컨테이너가 함께 돌고 있었으므로 참고로만 적는다.

## 지적 사항

### R23-1 (Med, 차단, 회귀 — de59997 R19-2) 상대 경로로 주어진 오디오는 모든 오디오 검사가 실패한다

**재현**

```
cd /tmp/proj && mkdir case && <wav 2개>
deepfake-lens scan case --format json        # audio_features: failed ×2
deepfake-lens classify case/a.wav           # failed
cd case && deepfake-lens scan .             # failed
deepfake-lens web --folder case → /api/scan # failed
```

- 사유는 `LibsndfileError: 오디오 파일을 열 수 없습니다`, 로그는 `Error opening 'a.wav': System error.`이다. 같은 파일을 절대 경로로 주면 `ran`이다.
- 2130892는 네 경우 모두 `ran`이다. 2d053dd, 20f73bf, HEAD는 모두 `failed`다.
- fail-closed라서 결론은 `undetermined`로 남는다. 그러나 가장 기본적인 호출(`scan case`, `cd case; scan .`, 상대 `--folder`)에서 오디오 검사가 전혀 돌지 않는다. 같은 파일을 절대 경로 웹 요청으로 검사한 결과와도 다르다(QA-OUT-4, "CLI/GUI/API가 같은 파일에 같은 결과").

**위치**

- deepfake_lens/audio_worker.py:744 — 요청 `{"path": native_path}`가 받은 경로 그대로 간다.
- audio_worker.py:441 — 작업자의 `cwd=self.cache_dir`이다. 상대 경로가 캐시 폴더 기준으로 풀린다.

→ `extract_features`(또는 호출부)에서 경로를 `os.path.abspath`로 바꿔 작업자에 보낸다. 상대 경로 `scan`·`classify`·`batch`·`web --folder`로 회귀 테스트를 두고, 2130892와 같은 `ran`인지 확인한다.

### R23-2 (Med, 차단, 회귀 — b161bb2 R22-9) 분석 파일의 폴더 안에 있는 venv에 numba 캐시를 쓴다 (QA-IN-1)

**재현**

```
/tmp/proj/{.venv, a.wav}
cd /tmp/proj; .venv/bin/python -m deepfake_lens classify a.wav
```

- 실행 후 `/tmp/proj/.venv/var/deepfake-lens/numba/<key>/published`가 생긴다. 즉 분석 파일의 폴더(증거)에 새 파일이 생긴다.
- R20-7/R21-6 규칙은 단일 파일 명령에서 분석 파일의 폴더를 검사 대상으로 본다. 흔한 배치(프로젝트 폴더에 `.venv`와 시료가 함께 있는 경우)에서 일어난다.
- `scan <폴더>`는 폴더가 `_EXAMINED_FOLDERS`에 들어가므로 쓰지 않는다.

**위치**

- audio_worker.py:211-214 — `environment_cache_root`가 `_inside_examined`만 본다.
- cli_inputs.py:746 — `set_examined_folders([str(folder) for folder in folders])`에는 분석 파일의 폴더(`examined`의 `path.parent`)가 빠진다.
- b161bb2 본문의 "skipped inside an examined folder"는 이 경우에 맞지 않는다.

→ 분석 파일의 폴더도 `_EXAMINED_FOLDERS`에 넣는다. 아니면 환경 루트를 `check_tool_folders`의 `examined` 목록과 대조한다. 회귀 테스트로 `classify`·`evidence`·`evidence-statement`·`batch`(파일)를 같은 배치에서 시험한다.

### R23-3 (Med, 차단, R22-1 잔여) 취소된 검사 작업을 `scan_job`으로 보내고 `summary.cancelled`를 빼면 "없음 — 모든 파일에 분석 결과가 있습니다"를 서명한다

**재현**: PNG 300개 폴더, `/api/scan?async=1`을 0.3 s 뒤 `/api/scan-cancel`한다. 작업은 `cancelled`, 행 6, `files_cancelled` 294다.

- `/api/report {items: 6행, scan_job: <id>, scan_root}`(summary 생략)는 200이다. 서명 본문은 다음과 같다.
  - `unrecorded_files.listing = {"source": "scan_job", "not_in_report": 0}`, `total_files 0`
  - `summary.cancelled false`
  - HTML에 「없음 — 분석 대상의 모든 파일에 분석 결과가 있습니다」
- 같은 요청에 `summary.cancelled: true`를 넣으면 「294개 — 검사 취소」로 정확하다. 서버는 작업이 취소된 사실과 개수를 알고 있지만, 클라이언트가 그 사실을 빼면 완전성을 서명한다(R22-1이 막으려던 "클라이언트 생략에서 누락 없음을 추론"과 같은 부류).

**위치**

- webapp_api.py:2129-2147 — `_report_listing`이 작업 결과의 `items`를 폴더 전체 목록으로 쓰고, 작업 상태 `cancelled`와 `files_cancelled`를 보지 않는다.
- webapp_api.py:2389 — 취소 표시는 게시된 summary에서만 켜진다.

→ `scan_job`의 상태가 `cancelled`이면 서버 값(`files_cancelled`)으로 취소를 표시한다. 아니면 그 작업을 목록으로 쓰지 않고 walk 또는 「확인 불가」로 처리한다. 테스트에 "취소된 작업 + summary 생략"을 추가한다.

### R23-4 (Med, 차단, 기존 결함 — 조정자 후보 확인) PDF 출력에서 글꼴에 없는 문자가 빈 글리프가 되어 서로 다른 파일 이름이 똑같이 찍힌다

**재현**: 폴더에 `note עברית.txt`, `note עבריה.txt`, `سجل.txt`, `ไทย.txt`, `हिन्दी.txt`, `emoji😀.txt`, `emoji😁.txt`, `한글.txt`, `日本語.txt`를 둔다.

- 웹 `/api/report format=pdf`(포렌식 PDF)와 `format=evidence`(증거설명서 PDF), CLI `evidence-statement <파일> --pdf-out`이 모두 해당한다.
- pymupdf로 텍스트를 추출하면 다음과 같다.
  - 두 히브리어 이름이 모두 `note \x00\x00\x00\x00\x00.txt`
  - 두 이모지 이름이 모두 `emoji\x00.txt`
  - 아랍어와 태국어 이름이 둘 다 `\x00\x00\x00.txt`
- 한글·일본어·ASCII 이름은 정확하다. HTML·JSON은 정확하다.
- 2130892도 같다(기존 결함).
- 서명된 증거설명서에서 두 증거 파일을 구별할 수 없다. 이름을 읽을 수 없게 되므로 R10-1/R11-4/R18-11의 "두 다른 이름은 결코 같아 보이지 않는다(단사)" 규칙을 PDF 경로가 어긴다.

**위치**: pdf_layout.py:110 — `pymupdf.Font("cjk")`(Droid Sans Fallback) 하나만 쓰고 글리프 유무를 확인하지 않는다(pdf_layout.py:211/239의 `insert_textbox`).

→ PDF로 그리기 전에 글꼴의 `has_glyph`로 각 코드 포인트를 확인한다. 없는 글자는 `display_name`과 같은 규칙의 `\uXXXX`/`\UXXXXXXXX` 이스케이프로 바꾼다(단사 유지). 포렌식 PDF·증거설명서·법률 보고서 PDF 모두에 회귀 테스트를 둔다.

### R23-5 (Low, R22-1 잔여) 압축 파일 구성원 행을 빼고 보내도 "없음"을 서명한다

**재현**: `bundle.zip`에 `gen.png`(A1111 메타데이터, 조작 근거)와 `plain.png`를 넣는다. 스캔 행 4개 중 `bundle.zip::gen.png`만 빼고 보낸다.

- 200이고, 서명 행 3개, `listing {"source":"walk","not_in_report":0}`, 「없음 — 분석 대상의 모든 파일에 분석 결과가 있습니다」이다.
- 컨테이너 행의 롤업은 manipulation_evidence로 남는다. 그러나 근거가 있는 구성원 행 자체는 보고서에 없는데도 완전성이 서명된다.

**위치**: webapp_api.py:2135/2160-2176 — 목록과 `signed_tops`가 최상위 경로만 비교한다.

→ 서버가 재분석한 컨테이너의 구성원 목록과 서명된 구성원 행을 비교해 빠진 구성원을 `not_in_report`로 센다. 아니면 구성원 행이 빠진 컨테이너는 400으로 거부한다.

### R23-6 (Low, R22-2 잔여, 정적 분석) 기본 ProgramData 상속 DACL에서 `dacl_parent_safe`가 False라 Windows 이동이 실제로는 일어나지 않는다

- 근거: `%ProgramData%\deepfake-lens`는 `base.mkdir(parents=True)`가 기본 모드로 만든다. 그래서 `C:\ProgramData`의 `CREATOR OWNER:(OI)(CI)(IO)(F)`(S-1-3-0, GENERIC_ALL, INHERIT_ONLY 0x08)를 상속한다.
- `dacl_parent_safe`는 INHERIT_ONLY 항목(자식에게만 적용되고 이 폴더에는 적용되지 않음)도 검사하므로 이 항목 때문에 False가 된다.
- 순수 함수 탐침: 실제 상속 ACE 목록이면 False, CREATOR OWNER(IO) 항목을 빼면 True(`out/q/p8_winacl.py`).
- 단위 테스트의 "default ProgramData inheritance" 고정값(test_tool_folders.py:516-519)에는 이 항목이 없다.
- 결과적으로 `classify %USERPROFILE%\x.jpg`와 `cd ~; web`은 실제 Windows에서 여전히 exit 2로 예상된다.
- 다른 계정이 먼저 상위 폴더를 만들었으면 두 번째 사용자는 이 후보를 쓸 수 없다. 이 점은 문서화가 필요하다.

**위치**: windows_acl.py `dacl_parent_safe`/`dacl_owner_only`(INHERIT_ONLY 미고려), native_path.py:743.

→ INHERIT_ONLY 항목은 그 폴더의 권한 판단에서 제외한다. 상위 폴더를 직접 만들 때는 보호된 ACL을 준다. 테스트 고정값에 실제 `icacls C:\ProgramData` 상속 결과를 넣는다.

### R23-7 (Low, 기존 결함 + R22-3 경로) 검토 저장이 실제로 쓰지 못해도 "success"를 응답하고, 피드백은 500이다

- 재현(uid 1000): `~/.local/share/deepfake-lens`를 0500으로 둔다.
  - `POST /api/review`는 200 `success`이고, `GET /api/reviews`에도 메모리 캐시로 보인다. 디스크에는 아무 파일도 없어 재시작하면 사라진다.
  - `POST /api/feedback`은 500이다(로그 `PermissionError`).
- 위치: reviews.py:69-79 — `_persist`가 `except OSError: pass`. webapp_api.py:1292-1294 — `mkdir`·`open`이 예외 처리 없이 실행된다.
- → 저장 실패를 한국어 사유와 함께 오류(409 또는 507)로 응답하고, 캐시를 되돌린다.

### R23-8 (Low, R22-3) 재정의 변수가 있을 때 이전 동작이 두 기록에서 다르고, 문서와도 다르다

- `DEEPFAKE_LENS_REVIEWS`가 지정되면 `~/.deepfake-lens/reviews.json`이 운영자 파일에 병합되고, 운영자 폴더에 `migrated-from-home.json` 표지가 생긴다. reviews.py:45-46은 `self._explicit is None`만 확인한다.
- `DEEPFAKE_LENS_FEEDBACK`이 지정되면 이전하지 않는다(webapp_api.py:1290 `if not os.environ.get(FEEDBACK_ENV)`).
- 탐침 결과: 운영자 폴더 `['f.jsonl', 'migrated-from-home.json', 'r.json']`, r.json 키 `old.png`, `a.png`, f.jsonl은 1줄.
- service.md:143/212/219는 두 기록 모두 "copied once"라고 적는다.
- → 두 기록을 같은 규칙으로 맞춘다(재정의 시 둘 다 이전하지 않거나 둘 다 이전). 문서도 고친다.

### R23-9 (Low, 기존 결함) 긴 `model_path`/`fusion_profile` 값이 두 서버에서 500이다

- `/api/scan?folder=…&model_path=<5000자>`에서 `OSError: [Errno 36] File name too long`이 난다(analysis_api.py:625 `candidate.is_file()`, 00244184). `fusion_profile`도 같다.
- → `_models_dir_file`에서 `OSError`를 `InvalidOption`(400)으로 바꾼다.

### R23-10 (Low, WP-J/HANDOFF §4) 레포의 게이트 이미지 정의로는 기준 이미지를 재현할 수 없다

- `scripts/gate/Dockerfile`은 `python:3.13-slim`과 모든 pip 패키지(numpy, opencv, librosa/numba, ruff, mypy, mediapipe …)의 버전을 고정하지 않고, 잠금 파일도 없다.
- 기준 이미지(2026-10-11 빌드)는 numpy 2.5.4, opencv 5.0.0.93, librosa 1.0.0, numba 0.68.0, ruff 0.17.0, mypy 2.4.0, mediapipe 1.1.0, Python 3.13.16 등이다. 다시 빌드하면 다른 버전이 들어와 lint, 오디오 결과, 건너뜀 수가 달라질 수 있다.
- 이미지 이력에는 Dockerfile에 없는 `COPY pyproject.toml /tmp/pp/` 층이 있다.
- 이 라운드에서 재빌드를 시도했지만 레지스트리 메타데이터 조회가 끝나지 않아 실패했다.
- → 버전을 고정한 Dockerfile(또는 `pip freeze` 잠금 파일)과 기반 이미지 digest를 레포에 둔다. HANDOFF §4에 그 파일로 재현된다고 적는다.

### R23-11 (Cosmetic, R22-10 잔여) `help vendor-weights pin`이 「알 수 없는 인수: pin」이다

- `pin`은 `vendor-weights`의 정당한 위치 인수 값이다(하위 파서가 아니다). `help help`는 「허용되지 않은 값 'help'」이다.
- 위치: cli.py:448(`_help_topic`).
- → 하위 파서가 아닌 위치 인수 값은 "그 명령의 도움말을 보십시오"로 안내하거나 그 명령의 도움말을 보여 준다. `help help`는 최상위 도움말을 보여 준다.

### R23-12 (Cosmetic) 커밋 메시지가 실제 동작과 다르다

- b161bb2: "skipped inside an examined folder" — 분석 파일의 폴더는 제외되지 않는다(R23-2).
- d9bf1b2: "a capped, flat or partly posted scan no longer signs '없음'" — 취소 작업과 구성원 생략에서는 여전히 서명한다(R23-3, R23-5).
- 8746ecf: "classify %USERPROFILE%\x.jpg and cd ~; web relocate instead of exit 2" — 기본 ACL에서는 이동하지 않는다(R23-6).

→ 다음 수정 커밋 본문에서 정정한다(6장 규칙).

### R23-13 (Cosmetic, 기존 결함) 참·거짓 옵션에 알 수 없는 값을 주면 조용히 거짓이 된다

- `/api/scan`과 보고서 `options`에서 `recursive=maybe`가 거부되지 않고 "아니오"로 서명된다(analysis_api.py:597 `_flag`).
- 반면 `max_files=abc`는 400이다. 같은 요청 안에서 숫자 옵션과 처리가 다르다.
- → 알려진 참·거짓 값 외에는 400.

## 잔여 1단계 리스크

- 기록(CONFORMANCE)은 librosa·soundfile·c2pa가 없는 API venv에서 생성된다. 오디오 QA 경로는 적합성 표에서 한 번도 실행되지 않는다. 오디오 회귀(R23-1)는 이 때문에 4라운드 동안 드러나지 않았다. 1단계 음성 트랙 전에 메인 venv로도 QA 하네스를 돌리는 것이 좋다.
- Windows 경로(R22-2 ACL, 경로 정규화)는 mock으로만 검증된다(합의 사항). 그래서 R23-6 같은 실제 OS 차이가 남는다. 1단계에서 실제 Windows CI를 권한다.
- PDF 글꼴이 CJK 한 종류뿐이다(R23-4). 다국어 증거명이 1단계 코퍼스에서 늘어난다.
- 단일 파일 명령에서 세션 임시 폴더는 분석 파일의 폴더를 제외 대상으로 보지 않는다(실행 후 삭제됨, 설계상 잔여).
- 이전 라운드 잔여(합의된 결정 목록: 제자리 매니페스트 수정 미감지, 골든 출력의 mediapipe/torch 미포함, 비Linux SIGKILL 자손 정리 최선 노력 등)는 유지된다.

## 게이트 0 판정: 아니오

수정 목록: R23-1(차단), R23-2(차단), R23-3(차단), R23-4(차단), R23-5, R23-6, R23-7, R23-8, R23-9, R23-10, R23-11, R23-12, R23-13.

## 부록

### 게이트·기록

- 측정 조건: git 이력이 있는 `--no-hardlinks` 사본, 로컬 main=dad9730.
- `gate.sh full`은 rc 0이다.
  - /venv: Ran 1784, OK(skipped=97). /venv-api: Ran 1784, OK(skipped=48).
  - ruff, mypy(131 파일) 통과. coverage 80 %.
  - cli_smoke 45/26, 측정 게이트 10/0, sync_model_docs, verify_contracts, stress 30/0.
- `qa_phase0.py --verify-record`: "7756d4013712에서 생성되었고 그 뒤 다른 변경이 없습니다". `build_traceability_commits.py --check`: 커밋 307개, 표 이후 1개(재생성 커밋만). R22-1~R22-11의 ids[]와 gaps는 커밋 제목과 일치한다.
- 커밋별 게이트(`git archive` 트리, 이력 없음): 20f73bf..85ad114의 15개 커밋은 두 venv 단위 테스트, ruff, mypy가 모두 OK다(테스트 수 1743→1784, skip 100/51은 이력 없는 트리 조건).
- 각 수정 커밋의 변경된 테스트 모듈을 부모 커밋 소스에서 실행하면 모두 실패한다.

  | 커밋 | 실패 | 오류 |
  | --- | --- | --- |
  | d9bf1b2 | 50 | 9 |
  | a1778a5 | 4 | 0 |
  | ac0f00d | 40 | 0 |
  | cb40945 | 4 | 0 |
  | 958fedf | 24 | 0 |
  | dbf9095 | 0 | 2 |
  | 8746ecf | 0 | 10 |
  | e52ac97 | 6 | 1 |
  | d021d3c | 12 | 4 |
  | b161bb2 | 0 | 2 |

  감사는 API venv에서 했으므로 오디오 테스트 일부는 건너뛰었다. 테스트 약화·삭제는 없다(TEST-DELETIONS 변경 없음).
- 반복 실행: test_audio_worker, test_user_data, test_tool_folders(66)와 test_servers(159)를 각 5회 돌려 실패 0. 종료 스트레스 30회를 추가로 돌려 실패 0.
- 얕은 클론(depth 1): 문서화된 2개 테스트(QaSys10 deletion reasons, test_shutdown history)만 실패한다(하위 테스트 14). QA-ENV-DEPENDENT-TESTS.md R16-12와 일치한다.

### 테스트 감사 보충

- R22-1 테스트에는 취소된 작업과 구성원 생략 사례가 없다(R23-3, R23-5).
- R22-2의 DACL 고정값에는 CREATOR OWNER(IO)가 없다(R23-6).
- R22-9 테스트는 `_EXAMINED_FOLDERS`만 시험하고 분석 파일의 폴더는 시험하지 않는다(R23-2).
- 상대 경로 오디오 테스트가 없다(R23-1).

### WP·규칙·QA 대조

- WP-A~J는 구현되어 있다.
- 공통 규칙 1~7은 위반을 찾지 못했다. 단 게이트 0 목표 "CLI/GUI/API가 같은 파일에 같은 결과"는 R23-1이 어긴다.
- QA 시나리오 통과 여부는 아래와 같다.
  - QA-IN-1: R23-2 경우에서 실패.
  - QA-OUT-4: 상대 경로 CLI와 웹 비교에서 실패(R23-1).
  - 그 외 QA-IN-2/4/5, OUT-1/2/3/5/6, ADV-1/2/3, SYS-1/2/3/6/7/9: 기존 테스트와 이번 탐침(hostile, 변조 40/40, 403/400) 범위에서 통과.
- CONFORMANCE의 "QA-IN-1 통과", "QA-OUT-4 통과"는 하네스 범위(API venv, 절대 경로)에서만 맞다.

### 탐침 목록 (이전에 다루지 않은 영역, 47종)

1. 취소 작업 조작
2. `max_files` 경계값 7종
3. `recursive` 잡값
4. 가짜·사전형 `scan_job`
5. TOCTOU(스캔 뒤 파일 추가)
6. 5만 파일 walk 비용
7. 구성원 생략
8. 스캔 뒤 파일 이동
9. ~15. APFS 별칭 7종
16. ~25. 저장소 10종
26. ~31. 환경 numba 캐시 6종
32. 상대 경로 오디오(scan, classify, `cd; scan .`, `web --folder`) × 3트리
33. GET NUL
34. 제어 문자
35. 긴 이름 500
36. 잡값 퍼징(14값 × 25+ 매개변수 × 2서버)
37. 서로게이트
38. 업로드 이름 12종
39. 불변식 퍼징 150회
40. help 12종
41. Windows DACL
42. 실제 차단기
43. 501 시간
44. QA-IN-4
45. 권한 없는 파일 행
46. hostile 3트리 교차
47. 변조 40

추가로 PDF 글리프 9개 문자 체계 × 3개 PDF 경로 × 2트리를 시험했다.

### 컨테이너와 파일

- 사용한 컨테이너: r23-full, r23-p, r23-perf, r23-h, r23-pc, r23-pc2. 동시에 최대 2개(성능 측정 때는 내 컨테이너 1개만)였고 모두 삭제했다.
- 탐침 스크립트와 로그: `scratchpad/r23/out/`(q/*.py, *.sh, perf-*.log, p10.log, percommit*.log, image-versions.txt).
- 원본 저장소 세 곳은 변경하지 않았다. 사본의 worktree `wt-20f73bf`, `wt-2130892`, `wt-2d053dd`는 scratchpad 안에 있다.
