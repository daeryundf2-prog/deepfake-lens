# 0단계 작업 인계서 (다른 세션에서 이어 하기)

작성: 2026-10-11, 갱신: 2026-10-12(21차 수정 반영) · 브랜치 `phase0` · 기준 커밋 `main`(dad9730)

## 1. 한 줄 요약

0단계(기반 고정)의 10개 작업 패키지(WP-A~J)는 전부 구현되어 있고, 계획서 7장의 "독립 검증 → 불일치 전부 수정 → 재검증" 루프를 21회 돌렸다. **21차 지적 9건(R21-1~R21-9)은 이 브랜치에서 수정되었다**(각 수정 커밋 제목에 ID가 있다: `git log --oneline --grep 'R21-'`). 다음 단계는 기록 재생성(5장, 라운드 끝의 기록 전용 커밋)이 아직 없다면 그것, 그다음 22차 독립 검증이다. 게이트 0(불일치 0건)은 아직 선언되지 않았다.

이 인계서는 기록 전용 커밋(CONFORMANCE·추적표 재생성) **앞에서** 쓰고, 그 커밋으로 바뀌는 상태(기록이 어느 커밋에서 만들어졌는지, 추적표에 어느 라운드 ID까지 있는지, 커밋 수)는 적지 않는다. 그 상태는 아래 2장의 명령으로 직접 확인한다.

## 2. 지금까지의 진행 위치

| 항목 | 상태 / 확인 방법 |
| --- | --- |
| 2차 계획서(사용자·이상적 상태·갭·QA·완료 판정) | claude.ai 문서 "Deepfake Lens 2차 계획서"(탭 2개: 본문, 0단계 구현 명세). 같은 내용이 이 폴더의 `SPEC.md`에 있음 |
| 0단계 구현 | WP-A~J 완료, `phase0` 브랜치(기준 커밋 dad9730 이후). 커밋 수는 `git rev-list --count dad9730..HEAD` |
| 독립 검증 | 21회. 라운드별 지적사항은 `verify_round*.md` (라운드 2·3은 문서 파일 없이 커밋 메시지·추적표에만 남음). 마지막 문서는 `verify_round21.md`(R20 종료 확인 + R21-1~R21-9) |
| 테스트 | 테스트 수와 건너뜀 수는 숫자로 적지 않는다 — `gate.sh tests`/`full` 출력의 `Ran …`·`OK (skipped=…)` 줄이 기준이다. 건너뜀 수는 측정 조건에 따라 다르다: `/repo`에 git 이력이 있으면(`git clone --no-hardlinks` 사본 — `gate.sh`가 `.git` 디렉터리를 복사) git 이력이 필요한 테스트가 돌고, 없으면(`git archive`로 푼 트리, `.git`이 파일인 worktree) 그 테스트가 건너뛰어져 두 venv 모두 건너뜀이 그만큼 많다(R21-7: 20차 인계서·커밋 본문의 "건너뜀 100/50"은 이력 없는 사본의 값이었고 조건을 적지 않았다). 커버리지는 `gate.sh full`의 coverage 단계(fail_under 63) |
| 적합성 기록 | `docs/CONFORMANCE.md` — 머리말에 생성한 커밋이 적혀 있다. 그 커밋이 마지막 수정 커밋보다 앞이면 아직 재생성 전이다. 확인: `PYTHONPATH=$PWD .venv-api/bin/python scripts/qa_phase0.py --verify-record` |
| 추적성 | `docs/TRACEABILITY-COMMITS.md`, `docs/traceability-commits.json`(검증 ID → 스펙 갭 G1–G34; `ids[]`의 gaps는 그 ID를 고친 커밋 제목의 Gaps에 포함되어야 함). 어느 라운드까지 매핑됐는지는 `ids[]`를 보고, 일관성은 `python scripts/build_traceability_commits.py --check`로 확인 |
| 다음 할 일 | (1) CONFORMANCE 머리말의 생성 커밋이 R21 수정 커밋들보다 앞이면: R21-1~R21-9를 `ids[]`에 추가하고 5장 절차로 기록 재생성(기록 전용 커밋 하나) (2) 22차 독립 검증 |

라운드별 추세: 초반(1~5차)은 판정을 틀리게 만드는 치명 결함(검사 실패가 '낮음'으로, 학습 안 된 모델이 한국어 판정, 다른 파일 해시 기록 등), 중반(6~11차)은 경로·인코딩·서명 범위·보고서 렌더링, 후반(12~17차)은 비UTF-8 파일명, 신호 처리, 자식 프로세스 정리 같은 운영 엣지 케이스. 14~18차는 회차마다 회귀 0건이었고, 19차에서 R18-1 수정이 드러낸 회귀 1건(R19-1)과 기존 결함 1건(R19-2: numba 디스크 캐시 경합 뒤 SIGSEGV)이 차단 항목으로 나왔다. 20차의 차단 항목 둘도 직전 수정의 회귀였다: R20-1(R19-13의 보고서 재분석이 FIFO·소켓·읽기 불가 폴더를 "찾을 수 없음"으로 세어 그런 폴더의 보고서가 전부 409)과 R20-2(R19-2의 오디오 작업자가 스캔 밖에서는 파일마다 새로 떠서 batch·업로드가 7~9배 느려짐). 그래서 6장에 "회귀 방지" 규칙을 더했다. 21차에서도 직전 수정이 결함을 만들었다: R21-1(차단 — R20-1이 게시된 폴더 행의 사유 문자열을 서버 행으로 서명), R21-2(R20-2가 작업자 스레드 수를 바꾸고도 출력 세대를 올리지 않음), R21-3(R20-3 프로토콜이 개행 없는 시작 출력에서 파일마다 600초 멈춤). `/api/report`의 결함 여섯(R19-13, R20-1, R20-4, R20-5, R21-1, R21-5)은 모두 게시 행과 서버 결과를 맞추는 규칙에서 나왔으므로, 21차 수정은 "게시 행은 식별 목록일 뿐 서명되는 글자는 전부 서버 것"을 불변식으로 두고 시험한다(6장).

## 3. 레포를 받는 방법

브랜치 `phase0`은 GitHub(`https://github.com/daeryundf2-prog/deepfake-lens`, `origin`)에 올라가 있고 `origin/main`은 기준 커밋 dad9730이다. 수정 세션은 push하지 않으므로 GitHub의 `phase0`이 로컬보다 뒤일 수 있다 — 받는 쪽에서 `git log origin/phase0..phase0`(로컬에서) 또는 전달받은 번들과 비교해 확인한다.

```bash
git clone https://github.com/daeryundf2-prog/deepfake-lens
cd deepfake-lens
git checkout phase0
# GitHub보다 앞선 커밋을 번들로 받았다면:
git fetch /경로/deepfake-lens-phase0.bundle phase0 && git merge --ff-only FETCH_HEAD
```

## 4. 환경 준비 (두 가지 가상환경)

검증은 항상 두 환경에서 돌렸다.

```bash
# (A) 메인 환경: 픽셀·오디오·ML extras
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,pixel,audio,ml]" ruff mypy

# (B) API 환경: 서버·PDF 경로 검증용
python3 -m venv .venv-api && .venv-api/bin/pip install fastapi httpx uvicorn pymupdf linkify-it-py markdown-it-py
# 실행 시: PYTHONPATH=$PWD .venv-api/bin/python ...
```

torch/transformers/mediapipe는 설치하지 않은 상태가 기준이다(신경망 가중치 없이 모든 테스트가 돌아야 함). 이 때문에 QA-SYS-10은 "건너뜀(환경)"으로 기록되며 목록은 `docs/QA-ENV-DEPENDENT-TESTS.md`에 있다.

### 기준 게이트 이미지 (Docker `deepfake-lens-gate`)

18·19차 검증과 수정은 로컬 Docker 이미지 `deepfake-lens-gate`를 기준 환경으로 썼다.

- Linux aarch64, Python 3.13, ffmpeg 7.1, Node.js 20.
- `/venv`(메인): numpy, scipy, Pillow, opencv-python 5, scikit-learn, librosa + numba + soundfile, c2pa-python, jsonschema, coverage, ruff, mypy.
- `/venv-api`(API): fastapi, httpx, uvicorn, pymupdf, markdown-it-py + linkify-it-py, mediapipe, opencv-python/opencv-contrib-python 5, numpy, scipy, Pillow, scikit-learn(librosa·c2pa 없음).
- 실행기 `gate.sh`(호스트에서 읽기 전용으로 마운트)는 `/repo`의 작업 트리를 컨테이너의 `/work`로 복사한 뒤(`.venv*` 제외, `.git`은 복사) 단계를 `bash -o pipefail`로 실행한다. 사용법: `gate.sh tests`(두 venv 단위 테스트), `gate.sh lint`(ruff, mypy), `gate.sh full`(5장의 게이트 전부), `gate.sh cmd "<명령>"`.

```bash
docker run --rm --init --name <고유이름> --cpus 4 \
  -v <작업 트리>:/repo:ro -v <gate.sh>:/gate.sh:ro deepfake-lens-gate /gate.sh full
```

- git 이력이 필요한 검사(`--verify-record`, `build_traceability_commits.py --check`, 삭제 이유·역사 문서 테스트)는 git worktree(`.git`이 파일)로는 안 된다. `git clone --no-hardlinks`한 사본을 `/repo`로 마운트하고, 그 사본의 로컬 `main`을 `dad9730`으로 맞춘다(`git branch -f main dad9730`). 소유자가 달라 git이 거부하면 `-e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*'`를 준다.

## 5. 게이트 (커밋마다 전부 통과해야 함)

```bash
python -m unittest discover deepfake_lens/tests          # 두 환경 모두
ruff check deepfake_lens scripts experiments
mypy
coverage run -m unittest discover deepfake_lens/tests && coverage report   # fail_under 63
python scripts/cli_smoke_test.py
python scripts/check_measurement_gate.py
python scripts/sync_model_docs.py --check
python scripts/verify_contracts.py                       # 스키마 바뀌면 contracts/PIN.json 재핀
python scripts/stress_shutdown.py --runs 30              # 신호·자식 프로세스 관련 변경 시
```

기록 재생성(라운드 끝마다, 마지막 커밋으로):

```bash
# 1) 깨끗한 트리에서 QA 하네스(API 환경) → docs/CONFORMANCE.md
PYTHONPATH=$PWD .venv-api/bin/python scripts/qa_phase0.py
# 2) 새 라운드 ID를 docs/traceability-commits.json의 ids[]에 추가한 뒤
python scripts/build_traceability_commits.py
# 3) 세 생성 파일만 한 커밋으로
git add docs/CONFORMANCE.md docs/traceability-commits.json docs/TRACEABILITY-COMMITS.md
# 4) 확인
PYTHONPATH=$PWD .venv-api/bin/python scripts/qa_phase0.py --verify-record
python scripts/build_traceability_commits.py --check
```

## 6. 작업 규칙 (지금까지 지켜 온 것)

- 커밋 제목에 검증 ID와 스펙 갭 ID: `fix(...): R17-1 ... (R17-1; Gaps: G34)`. 스펙 밖 항목은 `Gaps: 신규`.
- 커밋 메시지 끝 한 줄: `Co-Authored-By: Claude …`(17차까지는 `Claude-Session: …` 줄도 있었다).
- 추적표 `ids[]`의 `gaps`는 그 ID를 고친 커밋 제목의 `Gaps:`에 들어 있어야 한다(`build_traceability_commits.py --check`가 확인; 다르면 `gaps_note`가 필요).
- 면제 없음: 검증자가 "노트/잔여 리스크"로 분류한 것도 수정 대상으로 다음 라운드 문서에 넣었다. 단, 아래 "합의된 결정"은 재지적하지 않는다.
- 테스트 약화 금지. 결함을 고정하던 테스트만 고치고 ID 주석을 남긴다. 테스트 삭제는 `docs/TEST-DELETIONS.md`에 커밋 제목으로 기록.
- 커밋 메시지는 실제 동작을 정확히 적는다(과장 금지 — 16차에서 지적됨, 20차 R20-6에서도 "SIGTERM의 SystemExit"라는 사실과 다른 설명이 지적됨).
- 회귀 방지(20차에서 추가): 코드를 바꿀 때 그 코드를 부르는 다른 경로를 모두 적고 함께 시험한다. 고치는 결함의 원래 결함(직전 라운드가 막은 것)을 고정하는 회귀 테스트를 남긴다. 오디오 작업자처럼 비용이 큰 경로를 바꾸면 batch·웹 업로드·보고서 재분석·eval·스캔(workers 1/4)·이미지 전용 스캔을 직전 기준 커밋과 비교해 커밋 본문에 적는다(목표: 25% 이내).
- 인계서에는 기록 전용 커밋이 바꾸는 상태를 적지 않는다(1장 참고, 20차 R20-10). 측정 조건에 따라 달라지는 값(테스트·건너뜀 수, 성능)은 조건과 함께 적거나 확인 방법만 적는다(21차 R21-7).
- 서명 보고서 불변식(21차 R21-1): `/api/report`에 게시된 행은 식별(정규 상대 경로, container/member)·변경 감지(sha256, status, verdict_code)·중복 관계(duplicate_of)로만 쓴다. 서명되는 행·사유·라벨은 전부 서버가 디스크에서 만든 것이어야 하고, 서버가 스스로 만들 수 없는 행(예: 탐색 상한 폴더 행)은 서명하지 않는다. `test_servers.ReportSignsNoPostedTextInvariantTest`가 이를 지킨다 — 보고서 규칙을 바꿀 때 이 테스트를 약화하지 말 것.

## 7. 합의된 결정 (검증자에게 재지적하지 말라고 알려 줄 것)

텍스트 보고서는 모든 백슬래시를 두 배로 표시(단사 표시) · QA-SYS-10은 선택 extras 부재 시 "건너뜀(환경)" · `corpus verify`는 라벨 없는 파일에 exit 1 · `corpus split` 비율 정규화 · FastAPI 자동 문서 비활성 · `--port 0` 거부 · Windows/macOS 경로는 가짜 객체·mock으로만 검증 · python-markdown의 리터럴 백슬래시는 단사·무링크면 허용 · 비프로필 자산(face_landmarker.task, syncnet_v2.model, sfd_face.pth)은 운영자가 핀을 넣기 전까지 거부 · Linux 외 SIGKILL 자손 정리는 최선 노력 · 과거 커밋 b914bce…7fefde4의 SIG_IGN 문제는 `docs/KNOWN-HISTORICAL-ISSUES.md`에 문서화 · 같은 mtime/ctime/size/inode의 매니페스트 제자리 수정은 프로세스 내 미감지(문서화된 잔여) · 골든 출력은 mediapipe/torch/deep-signals 출력 미포함(문서화된 잔여) · api-serve의 Ctrl-C는 uvicorn이 처리.

## 8. 루프를 이어 가는 방법

한 라운드는 다음 순서의 커밋으로 끝난다. 각 단계가 무엇을 바꾸는지 섞지 않는다.

1. 검증 문서 커밋: 가장 최근 `verify_roundN.md`.
2. 지적별 수정 커밋(지적 하나에 하나 이상, 6장 규칙). Opus 에이전트에게 SPEC.md + 라운드 문서 + 이 인계서를 주고 위임해 왔다.
3. 인계서 갱신 커밋: 이 문서. 기록 전용 커밋으로 바뀌는 상태는 적지 않는다.
4. 기록 전용 커밋: 새 ID를 추적표 `ids[]`에 추가하고 5장 절차로 CONFORMANCE·추적표를 재생성한다(세 파일만).
5. **구현에 참여하지 않은** 새 에이전트에게 독립 검증을 맡긴다. 지금까지 쓴 검증 지시의 골격:
   - 게이트 전부(두 환경, 플레이크 확인을 위해 반복 실행), 얕은 클론 동작, 커밋별 게이트
   - 직전 라운드 항목을 새 탐침으로 종료 확인
   - 직전 기준 커밋 대비 회귀 탐색(픽스처 + 60행 이상 악성 폴더), 결정성(3회·워커 4·이름 변경·캐시 cold/warm·동시 4개), 서명 변조, doctor, 성능(2000파일, 오디오·영상 300개, batch·업로드·eval 경로; 25% 이상 저하 시 지적), 이전에 다루지 않은 영역 40건 이상
   - 새 테스트 감사, WP-A~J·규칙 1~7·QA 시나리오 전수 대조
   - 최종 판정: 불일치 0건이면 "예" + 1단계 잔여 리스크, 아니면 "아니오" + 수정 목록
6. 불일치 0건이 나오면 게이트 0 통과를 선언하고, 계획서 5장의 1단계(다섯 트랙: 이미지·문서 먼저, 음성·영상·텍스트 시차 병렬)로 넘어간다. 1단계 진입 시 결정할 사항(예산·GPU·인력·코퍼스 범위)은 계획서 8장에 있다.

## 9. 이 폴더의 파일

| 파일 | 내용 |
| --- | --- |
| `SPEC.md` | 0단계 구현 명세(목표·공통 규칙·갭 목록·WP-A~J·QA 시나리오 원문) |
| `verify_round1.md`, `verify_round4.md`~`verify_round17.md` | 라운드별 검증 지적사항(모두 수정됨) |
| `verify_round18.md` | 18차 검증: R17 종료 확인, R18-1~R18-19(수정됨) |
| `verify_round19.md` | 19차 검증: R18 종료 확인, R19-1~R19-12와 조정자 추가 R19-13(수정됨) |
| `verify_round20.md` | 20차 검증: R19 종료 확인, R20-1~R20-10(수정됨) |
| `verify_round21.md` | 21차 검증: R20 종료 확인, R21-1~R21-9(이 브랜치에서 수정됨) |
| `ux_audit_ec0d057.md` | 18차와 함께 한 UI/UX/AX 감사(ec0d057 기준) |
| `HANDOFF.md` | 이 문서 |
