# 0단계 작업 인계서 (다른 세션에서 이어 하기)

작성: 2026-10-11, 갱신: 2026-10-17(0단계 재구성 P0R 반영) · 브랜치 `phase0` · 기준 커밋 `main`(dad9730)

## 1. 한 줄 요약

0단계(기반 고정)의 10개 작업 패키지(WP-A~J)는 전부 구현되어 있고, "독립 검증 → 수정 → 재검증" 루프를 26회 돌렸다(26차 지적 R26-1~R26-10은 수정됨). 2026-10-17 사용자 결정 세 가지와 전체 리뷰(`fable_review.md`)·위협 조사(`threat_catalog_2026-10-17.md`)에 따라 **0단계를 재구성했다(P0R — 커밋 제목의 ID: `git log --oneline --grep 'P0R-'`)**: (1) 기법 브랜치 `techniques-2026` 병합(C2PA 2.4 판독기·신뢰 목록·워터마크 디코더·법정 표식 문맥), (2) **위험 기반 종료**(8.1장 — 판정·서명·증거 세 축의 불변식 테스트 + 직전 독립 검증 두 번에서 세 축 Med 이상 0건; Low·Cosmetic은 `BACKLOG.md`, 사실과 다른 문구는 심각도와 무관하게 막음), (3) **범위 동결**(게이트 범위 명령 9개 — 나머지는 「실험·비게이트」, Android 모듈은 `extras/android/`), (4) **`/api/report`는 서버가 보관한 검사 작업(`scan_job`)으로만** 만든다(6장), (5) 정직성·무결성 수정(자기 선언 메타데이터 표시와 「이 결론이 틀릴 수 있는 이유」, 「재현 가능한 기록 근거」 개명, 규칙 2 근거 충돌, 브랜드 단어 약화, 컨테이너 메타데이터 계층, C2PA 해시 제외범위 공격 차단과 동결된 ITL 분리, 서명 본문의 생성 시각·실행 모델·키 길이 하한). 다음 단계는 기록 재생성(5장)과 **새 형식(8.3장)의 27차 독립 검증**이다. 게이트 0은 아직 선언되지 않았다(8.1장의 (b) — 새 형식의 독립 검증 두 번이 필요하다).

이 인계서는 기록 전용 커밋(CONFORMANCE·추적표 재생성) **앞에서** 쓰고, 그 커밋으로 바뀌는 상태(기록이 어느 커밋에서 만들어졌는지, 추적표에 어느 라운드 ID까지 있는지, 커밋 수)는 적지 않는다. 그 상태는 아래 2장의 명령으로 직접 확인한다.

## 2. 지금까지의 진행 위치

| 항목 | 상태 / 확인 방법 |
| --- | --- |
| 2차 계획서(사용자·이상적 상태·갭·QA·완료 판정) | claude.ai 문서 "Deepfake Lens 2차 계획서"(탭 2개: 본문, 0단계 구현 명세). 같은 내용이 이 폴더의 `SPEC.md`에 있음 |
| 0단계 구현 | WP-A~J 완료, `phase0` 브랜치(기준 커밋 dad9730 이후). 커밋 수는 `git rev-list --count dad9730..HEAD` |
| 독립 검증 | 26회(옛 형식). 27차부터 8.3장의 새 형식(세 축의 적대적 탐침 + 직전 수정의 회귀 탐침, 라운드당 Med 이상만 수정). 라운드별 지적사항은 `verify_round*.md` (라운드 2·3은 문서 파일 없이 커밋 메시지·추적표에만 남음). 마지막 문서는 `verify_round26.md`(R25 종료 확인 + R26-1~R26-7; 검증자 원문 — 끝의 R26-8~R26-10은 조정자가 검증자의 잔여 리스크에서 승격한 항목). `verify_round22.md`는 검증자 원문이 `/private/tmp`와 함께 사라져 최종 보고를 옮긴 것이다(머리말 참고). R21-10은 21차 문서에 없고 사용자 결정으로 더한 항목이다(커밋 c43bc11) |
| 테스트 | 테스트 수와 건너뜀 수는 숫자로 적지 않는다 — `gate.sh tests`/`full` 출력의 `Ran …`·`OK (skipped=…)` 줄이 기준이다. 건너뜀 수는 측정 조건에 따라 다르다: `/repo`에 git 이력이 있으면(`git clone --no-hardlinks` 사본 — `gate.sh`가 `.git` 디렉터리를 복사) git 이력이 필요한 테스트가 돌고, 없으면(`git archive`로 푼 트리, `.git`이 파일인 worktree) 그 테스트가 건너뛰어져 두 venv 모두 건너뜀이 그만큼 많다(R21-7: 20차 인계서·커밋 본문의 "건너뜀 100/50"은 이력 없는 사본의 값이었고 조건을 적지 않았다). 커버리지는 `gate.sh full`의 coverage 단계(fail_under 63) |
| 적합성 기록 | `docs/CONFORMANCE.md` — 머리말에 생성한 커밋이 적혀 있다. 그 커밋이 마지막 수정 커밋보다 앞이면 아직 재생성 전이다. 확인: `PYTHONPATH=$PWD .venv-api/bin/python scripts/qa_phase0.py --verify-record` |
| 추적성 | `docs/TRACEABILITY-COMMITS.md`, `docs/traceability-commits.json`(검증 ID → 스펙 갭 G1–G34; `ids[]`의 gaps는 그 ID를 고친 커밋 제목의 Gaps에 포함되어야 함). 어느 라운드까지 매핑됐는지는 `ids[]`를 보고, 일관성은 `python scripts/build_traceability_commits.py --check`로 확인 |
| 다음 할 일 | (1) 기록 재생성(5장): P0R-0~P0R-23과 R26-1~R26-10을 추적표 `ids[]`에 넣고 CONFORMANCE를 다시 만든다(기록 전용 커밋 하나 — 조정자가 한다) (2) 27차 독립 검증 — 8.3장 형식. 성능 비교의 기준 커밋은 2130892와 28db773(재구성 직전), 측정 조건(이미지 태그·HOME·단독 실행 여부)을 함께 적는다. 반드시 포함: 새 `/api/report`(작업 id 위조·만료·다른 토큰·재시작 뒤·작업 기록 변조·검사 뒤 파일 변경·`exhibit_paths` 별칭), 자기 선언 근거와 「이 결론이 틀릴 수 있는 이유」가 모든 결론 렌더링에 있는지, C2PA 해시 제외범위 공격 변형(JPEG·BMFF·다른 구간 배치), 컨테이너 메타데이터의 거대·손상 박스, 짧은 서명 키, `test_axis_product`의 축 밖 조합, `gate.sh gui`. (3) `BACKLOG.md`의 BL-1(Ed25519)은 1단계 시작 시 |

라운드별 추세: 초반(1~5차)은 판정을 틀리게 만드는 치명 결함(검사 실패가 '낮음'으로, 학습 안 된 모델이 한국어 판정, 다른 파일 해시 기록 등), 중반(6~11차)은 경로·인코딩·서명 범위·보고서 렌더링, 후반(12~17차)은 비UTF-8 파일명, 신호 처리, 자식 프로세스 정리 같은 운영 엣지 케이스. 14~18차는 회차마다 회귀 0건이었고, 19차에서 R18-1 수정이 드러낸 회귀 1건(R19-1)과 기존 결함 1건(R19-2: numba 디스크 캐시 경합 뒤 SIGSEGV)이 차단 항목으로 나왔다. 20차의 차단 항목 둘도 직전 수정의 회귀였다: R20-1(R19-13의 보고서 재분석이 FIFO·소켓·읽기 불가 폴더를 "찾을 수 없음"으로 세어 그런 폴더의 보고서가 전부 409)과 R20-2(R19-2의 오디오 작업자가 스캔 밖에서는 파일마다 새로 떠서 batch·업로드가 7~9배 느려짐). 그래서 6장에 "회귀 방지" 규칙을 더했다. 21차에서도 직전 수정이 결함을 만들었다: R21-1(차단 — R20-1이 게시된 폴더 행의 사유 문자열을 서버 행으로 서명), R21-2(R20-2가 작업자 스레드 수를 바꾸고도 출력 세대를 올리지 않음), R21-3(R20-3 프로토콜이 개행 없는 시작 출력에서 파일마다 600초 멈춤). `/api/report`의 결함 여섯(R19-13, R20-1, R20-4, R20-5, R21-1, R21-5)은 모두 게시 행과 서버 결과를 맞추는 규칙에서 나왔으므로, 21차 수정은 "게시 행은 식별 목록일 뿐 서명되는 글자는 전부 서버 것"을 불변식으로 두고 시험한다(6장). 22차의 차단 항목 R22-1은 회귀가 아닌 기존 결함이었다: 웹 보고서의 "기록되지 않은 파일"이 게시 행에서만 세어져 상한·평면 스캔도 "없음"으로 서명되었다 — 이제 서버의 검사 작업 기록이나 서버 자신의 폴더 탐색으로 세고, 알 수 없으면 "확인 불가"로 서명한다. R22-3(검토·피드백 기록이 증거 폴더에 생김)은 R21-10이 `cd ~; web`을 허용하면서 생긴 회귀였다. 23차의 차단 항목 넷 중 둘은 회귀였다: R23-1(R19-2부터 상대 경로 오디오가 작업자의 작업 폴더 기준으로 풀려 항상 실패 — 지금까지의 검증이 모두 절대 경로를 써서 4라운드 동안 숨었다)과 R23-2(R22-9의 환경별 numba 캐시가 분석 파일 옆 `.venv`에 씀). 나머지 둘은 기존 결함이었다: R23-3(취소된 검사 작업 + 요약 생략으로 "없음" 서명)과 R23-4(PDF 글꼴에 없는 문자가 빈 글리프가 되어 서로 다른 이름이 같게 찍힘). 23차 수정은 게이트 이미지를 레포만으로 재현되게 고정했고(R23-10), 오디오 경로를 메인 환경에서도 도는 `qa-main` 단계를 게이트에 더했다(5장). 24차의 차단 항목 R24-1은 R19-13 이후의 회귀였다: 압축 파일이 있는 폴더의 웹 검사를 취소하면 구성 파일이 시작되지 않은 컨테이너 행이 `unknown`(「분석 가능한 구성 파일이 없습니다」)이 되고, 서버 재분석은 완전한 `expanded`를 만들어 모든 보고서가 "검사 뒤 바뀐 파일" 409로 거부되었다 — 이제 그런 컨테이너는 `cancelled` 상태와 자기 문구를 갖고 보고서는 취소 행으로 제외한다(아카이브 자체의 sha256은 여전히 비교). 나머지: R24-3은 R23-1 수정의 회귀(PATH_MAX보다 깊은 작업 폴더에서 절대 이름 실패), R24-2·R24-5는 오래된 결함(프로세스 안 FFmpeg 판독기에 상대 `x:` 이름, 읽을 수 없는 파일의 `classify`), R24-4·R24-7·R24-8·R24-11은 23차 수정의 잔여(수리된 `reviews.json`, PDF 사건 필드의 단사성, `/api/scan/stream`의 `recursive`, `help <명령> -h`), R24-6·R24-9·R24-10은 게이트 도구(구 GUI 스모크가 gui 이미지에서 통과 불가, qa-main의 `/tmp` 밖 쓰기, 임시 venv에서 실패하던 R23-2 테스트)였다. 25차의 차단 항목 R25-1도 직전 수정의 회귀였다: R24-1이 게시된 `kind`·`status: "cancelled"`·`summary.cancelled`로 행을 "사용자 취소"로 제외하고 구성원 행 없는 컨테이너를 재분석하지 않아, 조작 근거가 있는 PNG나 압축 파일의 구성원을 서명 보고서에서 빼고도 「기록되지 않은 파일: 없음」이 서명되었다 — 이제 취소 여부는 서버가 가진 검사 작업 기록으로만 정하고 게시된 컨테이너는 늘 재분석하며, 게시 구조 퍼징 불변식 테스트(`test_servers.ReportSignedSetIsTheServersInvariantTest`)가 이를 지킨다(6장). 나머지: R25-2·R25-4·R25-5·R25-6·R25-7은 기존 결함(읽을 수 없는 오디오·영상·PDF의 거짓 「분석 중 파일 변경」, 깊은 작업 폴더의 비ASCII 이름, librosa 1.0에 audioread 경로가 없어 m4a·aac·wma가 늘 실패 — 이제 ffmpeg로 디코딩, `:` 압축 구성원 이름을 zip-slip으로 분류, `.` 루트의 사유 경로 표기), R25-3·R25-8은 24차 수정의 잔여(증거설명서 「작성자 및 일자」 칸, `help help -h`)였다. 조정자 추가 R25-9(R25-5 수정 중 발견)는 같은 원인의 기존 결함이다: `multimodal --av-sync`가 영상을 `librosa.load`로 열어 측정이 한 번도 실행되지 않았다 — 이제 같은 ffmpeg 경로로 음성 트랙을 디코딩하고 못 하면 실제 사유를 `coverage`·한계에 적는다. 출력 형식 세대는 12다(R25-5, R25-7). 26차의 차단 항목 R26-1도 직전 수정의 회귀였다: R25-6이 POSIX에서 `:`를 구성원 이름의 보통 문자로 받아들이자, 리터럴 이름 `inner.zip::b.png`인 구성원과 중첩 압축 파일 `inner.zip` 안의 `b.png`가 같은 행 식별자(`container`, `member`)를 가졌다 — 서버 재분석이 둘 중 하나만 남겨 정직한 보고서는 400, 조작 근거가 있는 쪽을 뺀 보고서는 「기록되지 않은 파일: 없음」으로 서명되었다. 이제 구성원 사슬은 압축 단계마다 경로를 이스케이프해(`::`·`\`가 있거나 `:`로 시작·끝나는 단계만 `\\`·`\:`로) 구성상 단사이고, 검사 결과는 반환 전에 모든 행 식별자가 유일하고 (경로, 구성원 사슬)로 되돌아가는지 확인해 어기면 결과를 내지 않는다(R26-8, 6장). 나머지: R26-2는 R25-7 잔여가 R25-2로 넓어진 것(읽기 실패·압축 해제 실패 사유가 `.` 실행과 `--workers 4`에서 `<root>/` 없이 쓰임 — 작업자 스레드가 스캔의 경로 정리 문맥을 물려받지 않았다), R26-3·R26-4는 R25-4·R25-6과 같은 종류의 기존 결함(macOS 깊은 작업 폴더의 2 GiB 초과 비ASCII 영상과 거짓 사유, `~$보고서.docx`·`con.png` 압축 구성원 거부), R26-5~R26-7은 문서·문구(취소된 스트림의 부분 결과 — 이제 스트림도 서버가 가진 검사 작업 기록, PDF의 `|` 표기 둘, `help help scan`)였다. 조정자가 승격한 R26-9·R26-10은 `--av-sync`의 무제한 디코딩과 첫 음성 트랙만 보는 문제, ffmpeg 시간 초과 사유였다. 출력 형식 세대는 17이다(R26-1, R26-8, R26-2, R26-4, R26-10).

**0단계 재구성(P0R, 2026-10-17).** 26차까지의 진단(전체 리뷰 3.2): 지적의 대부분이 0단계 가치와 무관한 표면(경로 표기·도움말·구성원 이름)에서 나왔고, `/api/report` 계열 지적 12건이 게시 행을 서버 재분석과 맞추는 약 900줄에서 나왔으며, "불일치 0건"은 측정할 수 없는 종료 조건이었다. 재구성은 그 구조를 없앴다: 보고서는 서버가 보관한 검사 작업만 서명하고(P0R-6 — 게시 행 조정 장치 삭제, 작업은 토큰에 묶이고 증거 밖에 보관·HMAC, 실행 중인 작업은 퇴거하지 않음), 종료는 세 축의 불변식(P0R-1~3: hypothesis 속성 테스트, 경로 축 곱 표)과 독립 검증 두 번으로 정하며, 게이트 범위를 9개 명령으로 동결했다(P0R-4, P0R-5). 정직성 수정: 브랜드 단어는 생성 도구 필드에서만 보통 근거(P0R-11), 자기 선언 메타데이터 근거와 「이 결론이 틀릴 수 있는 이유」(P0R-12), 「재현 가능한 기록 근거」 개명과 판단 불가 행의 등급 라벨 제거(P0R-13), 규칙 2 근거 충돌(P0R-10), 하지 않은 영상 메타데이터 검사를 주장하던 문구 제거와 컨테이너·비트스트림 메타데이터 계층(P0R-22/23), C2PA 해시 제외범위 공격(SDK가 Trusted로 보고하는 두 구간 형태)을 c2pa 검사 실패로(P0R-20), 동결된 ITL 분리와 폐기 미확인 명시(P0R-21), 서명 본문의 생성 시각·실행 모델·`temp_folder` 제거(P0R-14), 32바이트 미만 서명 키 거부(P0R-15). 출력 형식 세대는 24다(병합 18, P0R-22/23 19, P0R-20/21 20, P0R-10 21, P0R-11 22, P0R-12 23, P0R-13 24).

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
python3 -m venv .venv-api && .venv-api/bin/pip install fastapi httpx uvicorn pymupdf linkify-it-py markdown-it-py hypothesis
# 실행 시: PYTHONPATH=$PWD .venv-api/bin/python ...
```

torch/transformers/mediapipe는 설치하지 않은 상태가 기준이다(신경망 가중치 없이 모든 테스트가 돌아야 함). 이 때문에 QA-SYS-10은 "건너뜀(환경)"으로 기록되며 목록은 `docs/QA-ENV-DEPENDENT-TESTS.md`에 있다.

### 기준 게이트 이미지 (Docker `deepfake-lens-gate`)

18차부터의 검증과 수정은 로컬 Docker 이미지 `deepfake-lens-gate`를 기준 환경으로 썼다. 이미지 정의와 실행기는 레포 안에 있다(22차부터 — 그 전에는 레포 밖 임시 폴더에 있어 호스트 재시작으로 사라졌다).

- `scripts/gate/Dockerfile` — 기준 이미지: `docker build -t deepfake-lens-gate -f scripts/gate/Dockerfile scripts/gate`
- `scripts/gate/Dockerfile.gui` — GUI 스모크 이미지(기준 이미지 + `/venv-gui`의 pymupdf·Playwright Chromium): `docker build -t deepfake-lens-gate:gui -f scripts/gate/Dockerfile.gui scripts/gate`
- `scripts/gate/Dockerfile.tech` — 기법 이미지(techniques-2026 병합, P0R-0): `:latest` 위에 워터마크 디코더 스택(torch CPU·onnxruntime·audioseal·invisible-watermark, `requirements-tech*.lock`)과 핀된 디코더 가중치(`/opt/deepfake-lens-weights`): `docker build -t deepfake-lens-gate:tech -f scripts/gate/Dockerfile.tech scripts/gate`. `:latest`를 바꾸면 `:tech`도 다시 빌드한다(`FROM deepfake-lens-gate:latest`).
- `scripts/gate/Dockerfile.tech2` — PerTh 가중치 이미지(techniques-2026b TB-2, 28차 R28-3): `:tech` 위에 Resemble AI PerTh 암묵 디코더 가중치(`perth-implicit-250000.pth.tar`, MIT 휠 resemble-perth 1.0.1에서 꺼내 휠·파일 sha256 확인, `models/assets.json`에 핀)만 더한다 — 파이썬 패키지는 더하지 않는다: `docker build -t deepfake-lens-gate:tech2 -f scripts/gate/Dockerfile.tech2 scripts/gate`. **PerTh 가중치가 있는 이미지는 `:tech2`뿐이다** — `:tech`에서는 PerTh 디코더 경로(`wm_perth`, `synthetic-perth.wav` 검출)가 가중치 없음으로 건너뛰어진다. `:tech`를 바꾸면 `:tech2`도 다시 빌드한다(`FROM deepfake-lens-gate:tech`).
- 필수 게이트 이미지는 `:latest`, `:tech`, `:tech2`(각각 `gate.sh full`)와 `:gui`(`gate.sh gui`) 넷이다(8.1장의 (a)).
- `scripts/gate/gate.sh` — 실행기(bash, 단계마다 `pipefail`). Python 파일이 아니므로 ruff·mypy·`scripts/*.py` 도움말 테스트의 대상이 아니다.
- `scripts/gate/requirements-main.lock`, `requirements-api.lock`, `requirements-gui.lock` — 각 venv의 `pip freeze`(의존성 포함 전부, 정확한 버전). Dockerfile이 `pip install --no-deps -r`로 설치하므로 빌드 시점에 아무것도 새로 해석하지 않는다.

**재현성(23차 R23-10).** 기준 이미지는 이 폴더의 파일만으로 다시 만들어진다. 기반 이미지는 digest로 고정했다(`python:3.13.16-slim-trixie@sha256:70729b46…` — 그 arm64 층 4개가 2026-10-11 기준 이미지의 처음 4개 층과 같다). Debian 패키지는 `snapshot.debian.org`의 `DEBIAN_SNAPSHOT`(기본 20261011T000000Z) 상태에서 받고, 명시한 패키지(ffmpeg 7.1.5, git, procps, util-linux, nodejs 20.19.2)는 버전도 고정했다. pip 패키지는 위 잠금 파일의 버전 그대로다(pip 26.2.1). 22차까지의 Dockerfile은 아무것도 고정하지 않아 다시 빌드하면 그날의 `python:3.13-slim`과 PyPI 최신판이 들어왔다. 기준 이미지 이력의 `COPY pyproject.toml /tmp/pp/` 층은 레포 밖 임시 Dockerfile의 흔적이고 이미지 내용에는 영향이 없다(재빌드 이미지에는 없다). 확인 방법: 새 태그로 빌드(`docker build -t deepfake-lens-gate:rebuild -f scripts/gate/Dockerfile scripts/gate`)한 뒤 두 이미지에서 `/venv/bin/pip freeze`, `/venv-api/bin/pip freeze`, `python -V`, `dpkg-query -W`를 비교한다 — 23차 수정에서 모두 같았다. 잠금 파일은 이미지를 의도적으로 바꿀 때만 다시 만들고(`pip freeze`), 그 이유를 커밋에 적는다. 같은 태그를 덮어쓰기 전에 새 태그로 빌드해 비교한다.

- Linux aarch64, Python 3.13, ffmpeg 7.1, Node.js 20.
- `/venv`(메인): numpy, scipy, Pillow, opencv-python 5, scikit-learn, librosa + numba + soundfile, c2pa-python, jsonschema, coverage, ruff, mypy, hypothesis(P0R-2).
- `/venv-api`(API): fastapi, httpx, uvicorn, pymupdf, markdown-it-py + linkify-it-py, mediapipe, opencv-python/opencv-contrib-python 5, numpy, scipy, Pillow, scikit-learn, hypothesis(P0R-2)(librosa·c2pa 없음).
- **이미지 변경 이력(P0R-2, 2026-10-17)**: 판정 불변식 속성 테스트를 위해 두 잠금 파일에 `hypothesis==6.170.1`과 그 의존성 `sortedcontainers==2.4.0`을 더했다. 절차는 위와 같다 — 새 태그 `deepfake-lens-gate:p0r-hyp`로 빌드해 기존 `:latest`와 `/venv`·`/venv-api`의 `pip freeze`(차이: 그 두 줄뿐), `dpkg-query -W`(같음), `python -V`(3.13.16)를 비교한 뒤 `:latest`를 그 이미지로 옮겼다. 이전 기준 이미지는 `:base` 태그로 남아 있다(2026-10-11 빌드). `:tech`(`Dockerfile.tech`, `FROM deepfake-lens-gate:latest`)는 새 `:latest` 위에서 다시 빌드했고(`:tech-p0r`로 빌드·비교 후 `:tech`로), `:gui`는 다시 빌드하지 않았다 — GUI 스모크(`gate.sh gui`)는 `/venv-gui`만 쓰고 단위 테스트를 돌리지 않는다.
- 실행기 `scripts/gate/gate.sh`(호스트에서 `/gate.sh`로 읽기 전용 마운트)는 `/repo`의 작업 트리를 컨테이너의 `/work`로 복사한 뒤(`.venv*` 제외, `.git`은 복사) 단계를 `bash -o pipefail`로 실행한다. 사용법: `gate.sh tests`(두 venv 단위 테스트), `gate.sh lint`(ruff, mypy), `gate.sh full`(5장의 게이트 전부), `gate.sh qa-main`(메인 venv 패키지로 QA 하네스, 23차), `gate.sh cmd "<명령>"`.

```bash
# 레포 루트에서
docker run --rm --init --name <고유이름> --cpus 4 \
  -v "$PWD":/repo:ro -v "$PWD/scripts/gate/gate.sh":/gate.sh:ro deepfake-lens-gate /gate.sh full
# GUI 스모크(Playwright): gui 이미지, 공유 메모리 1 GB
docker run --rm --init --name <고유이름> --cpus 3 --shm-size=1g \
  -v "$PWD":/repo:ro -v "$PWD/scripts/gate/gate.sh":/gate.sh:ro deepfake-lens-gate:gui /gate.sh gui
```

- GUI 스모크(`gate.sh gui`, 24차 R24-6): `scripts/gui_smoke.py`(구 GUI `deepfake_lens/gui.*`의 스모크)는 Google Chrome(`channel="chrome"`)을 먼저 찾고, 없으면 Playwright 자체 Chromium(gui 이미지의 chromium-1243 — Linux arm64에는 Chrome이 없다)을 쓴다. 게이트가 검사하는 두 폴더(`fixtures`, `make_adversarial_fixtures.py`의 `/tmp/adv`)는 최상위에 파일이 없으므로 스모크는 '하위 폴더' 옵션을 켜고 검사한다(끄면 0행이라 `#stat-total > 0`에서 실패). 23차까지는 두 이유로 이 게이트가 레포의 gui 이미지에서 통과할 수 없었다. 통과 기준은 두 단계 모두 `GUI smoke PASS`와 `GATE rc=0`이다.
- git 이력이 필요한 검사(`--verify-record`, `build_traceability_commits.py --check`, 삭제 이유·역사 문서 테스트)는 git worktree(`.git`이 파일)로는 안 된다. `git clone --no-hardlinks`한 사본을 `/repo`로 마운트하고, 그 사본의 로컬 `main`을 `dad9730`으로 맞춘다(`git branch -f main dad9730`). 소유자가 달라 git이 거부하면 `-e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*'`를 준다.

## 5. 게이트 (커밋마다 전부 통과해야 함)

기준 환경에서는 `scripts/gate/gate.sh full`(4장의 `docker run`)이 아래 단계를 그대로 실행한다. 게이트 이미지는 `:latest`, `:tech`, `:tech2`(PerTh 가중치가 있는 유일한 이미지, R28-3)에서 각각 `gate.sh full`, `:gui`에서 `gate.sh gui`다(4장). 단계별 출력(`Ran …`, `OK`, `!!! FAILED`)을 읽고, 마지막 `GATE rc=`만 보지 않는다.

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
# QA 하네스의 오디오 경로(23차 잔여 리스크): /venv(librosa) + /venv-api(fastapi·pymupdf)를 함께 보는 임시 venv에서 --qa-only
gate.sh qa-main                                          # full에 포함, /tmp 아래에만 씀(기록 아님, R24-9)
```

`qa-main` 단계(23차): 적합성 기록(`docs/CONFORMANCE.md`)은 지금처럼 API 환경(librosa 없음)에서 만든다. 그래서 QA-IN-1·QA-OUT-4의 오디오 경로는 기록 실행에서 한 번도 돌지 않았고, 상대 경로 오디오 회귀(R23-1)가 4라운드 동안 드러나지 않았다. `gate.sh full`은 이제 마지막 단계로 하네스를 `--qa-only`로 한 번 더 돌린다 — `/venv`의 패키지를 먼저, `/venv-api`의 패키지를 뒤에 보는 임시 venv(`.pth`)라 librosa와 fastapi가 모두 있다. 기록은 바꾸지 않는다. 쓰는 곳(24차 R24-9 — 23차 문서는 "`/tmp/qa-main`에만 쓴다"고 했으나 실제로는 실행하는 사용자의 `~/.cache/deepfake-lens`(로그, numba published 캐시)와 표준 라이브러리·저장소의 `__pycache__`도 썼다): 하네스는 `HOME`·`XDG_CACHE_HOME`·`XDG_DATA_HOME`·`XDG_CONFIG_HOME`을 `/tmp/qa-main/home` 아래로 두고 `PYTHONDONTWRITEBYTECODE=1`로 돈다. 그래서 `/tmp/qa-main`(표, 로그, 그 HOME의 도구 폴더 `.cache/deepfake-lens`), `/tmp/qa-main-venv`(임시 venv와 그 환경의 numba 캐시, R22-9), 테스트의 임시 폴더(`/tmp/tmp*`, `/tmp/deepfake-lens-<uid>`)에만 쓴다. 24차 수정에서 컨테이너의 `/tmp`·`/proc`·`/sys`·`/dev`·`/work` 밖에 새로 생긴 파일이 0개임을 `find -newer`로 확인했다. 개발 머신에서 같은 명령을 돌려도 사용자의 도구 캐시는 바뀌지 않는다. QA 테스트 중 `QaOut4SameResultEverywhereTest.test_audio_folder_relative_cli_paths_match_the_servers`와 `QaIn1ReadOnlyEvidenceTest.test_relative_audio_commands_from_a_project_folder_leave_it_untouched`는 환경과 무관하게 돌며(librosa가 없으면 양쪽 모두 `skipped`, 있으면 `ran`을 요구), 상대 경로·프로젝트 `.venv` 배치를 시험한다.

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
- 위험 기반 분류(사용자 결정 2026-10-17, P0R-1 — 이전의 "면제 없음" 규칙을 대체한다): 검증 지적은 심각도(Critical·High·Med·Low·Cosmetic)와 축(판정·서명·증거, 8장의 게이트 0 기준)으로 분류한다. **Med 이상과 사용자에게 보이는 사실과 다른 문구**(범위는 8.1 (b) — 사용자 결정 2026-10-19; 심각도와 무관 — 하지 않은 검사를 "찾지 못함"으로 쓰는 문구, 실제와 다른 실패 사유 등)만 다음 라운드의 수정 대상이다. Low·Cosmetic은 `docs/phase0/BACKLOG.md`에 ID·한 줄 요약·출처 라운드와 함께 옮기고 게이트를 막지 않는다. 라운드당 수정은 Med 이상(+ 거짓 문구)으로 제한한다. 아래 "합의된 결정"은 재지적하지 않는다.
- 게이트 범위(사용자 결정 2026-10-17, P0R-4): 게이트 0 검증 대상 명령은 `scan`, `classify`, `verify-report`, `evidence-statement`, `doctor`, `web`, `api-serve`, `corpus build|split|verify`, `vendor-weights pin`(`pin-asset` 포함)이다(`cli_parser.GATE_COMMANDS`). 나머지 CLI 명령은 도움말에 「실험·비게이트」로 표시되며 게이트 0 검증 범위 밖이다 — 그 테스트는 남아 있고 게이트 실행에서 계속 돌지만, 그 명령의 경로·문구·도움말 지적은 Low로 분류해 백로그로 보낸다(거짓 문구는 예외 없이 수정 대상).
- 테스트 약화 금지. 결함을 고정하던 테스트만 고치고 ID 주석을 남긴다. 테스트 삭제는 `docs/TEST-DELETIONS.md`에 커밋 제목으로 기록.
- 커밋 메시지는 실제 동작을 정확히 적는다(과장 금지 — 16차에서 지적됨, 20차 R20-6에서도 "SIGTERM의 SystemExit"라는 사실과 다른 설명이 지적됨).
- 회귀 방지(20차에서 추가): 코드를 바꿀 때 그 코드를 부르는 다른 경로를 모두 적고 함께 시험한다. 고치는 결함의 원래 결함(직전 라운드가 막은 것)을 고정하는 회귀 테스트를 남긴다. 오디오 작업자처럼 비용이 큰 경로를 바꾸면 batch·웹 업로드·보고서 재분석·eval·스캔(workers 1/4)·이미지 전용 스캔을 직전 기준 커밋과 비교해 커밋 본문에 적는다(목표: 25% 이내).
- 인계서에는 기록 전용 커밋이 바꾸는 상태를 적지 않는다(1장 참고, 20차 R20-10). 측정 조건에 따라 달라지는 값(테스트·건너뜀 수, 성능)은 조건과 함께 적거나 확인 방법만 적는다(21차 R21-7).
- 서명 보고서 불변식(21차 R21-1, P0R-6에서 구조로 바꿈 — 사용자 결정 2026-10-17): `/api/report`는 **서버가 보관한 검사 작업(`scan_job`)으로만** 만든다. 요청은 작업 id, 사건 필드, 형식, `side`, 서증 선택(`exhibit_paths` — 작업의 행 경로만, 순서 지정), 업로드 행(`uploads`)만 받고 다른 필드는 400이다. 서명되는 행 집합은 작업의 행 집합, 옵션·임계값·모델 출처·미기록/취소 계수도 작업의 것이며, 변경 감지는 검사 때 해시한 파일을 디스크에서 다시 해시해 다르면 409(경로와 사유만 — 서버 값은 돌려주지 않음)다. 작업은 서버 토큰에 묶이고(다른 토큰의 작업은 404), 끝나면 증거 밖 도구 폴더(`DEEPFAKE_LENS_SCAN_JOBS` 또는 사용자 데이터 폴더의 `scan-jobs`)에 보관되며(보고서 키가 있으면 HMAC), `DEEPFAKE_LENS_SCAN_JOB_TTL_DAYS`(기본 7일) 뒤 만료되고, 실행 중인 작업은 퇴거되지 않는다(`deepfake_lens/scan_jobs.py`). 게시 행을 맞추던 장치(`_posted_*_error`, `_rederive_report_items`, `_with_posted_cancel`, 폴더 재탐색 목록)는 지웠다. 불변식 테스트 두 개(`test_servers.ReportSignsNoPostedTextInvariantTest`, `test_servers.ReportSignedSetIsTheServersInvariantTest`)는 남아 있고 거의 자명해졌다 — 보고서 규칙을 바꿀 때 약화하지 말 것. 예외 두 가지는 그대로다(22차 R22-8): (1) 업로드 행의 이름 — 업로드 파일은 서버가 다시 만들 수 없으므로 `uploads`의 이름이 `excluded_items[].path`에 들어간다(상대 경로·255자 이하·빈/`.`/`..` 구성 요소 없음·NUL 없음, 어기면 400; 이 서버가 그 이름과 sha256으로 업로드 행을 만든 적이 있으면 `name_source: "server_upload"`, 아니면 `"client"`와 사유 「업로드 파일(클라이언트 제공 이름 — …)」). (2) 증거설명서의 사건 필드. 행 식별자의 단사성(26차 R26-1·R26-8)은 검사 결과 쪽의 규칙으로 그대로다: 구성원 사슬은 이스케이프로 구성상 단사이고, 검사 결과는 `result_text.check_row_identities`를 통과해야 반환된다. 이름을 새로 받아들이는 변경(압축 구성원 이름 규칙, 폴더 탐색, 업로드 이름 등)은 그 이름과 겹쳐 보이는 이름(구분자를 담은 이름, 중첩 압축 파일, NFC/NFD 변형, 이스케이프 문자 자체)을 `ReportSignedSetIsTheServersInvariantTest`·`ReportArchiveMemberChainIsInjectiveTest`의 픽스처에 함께 넣는다.

## 7. 합의된 결정 (검증자에게 재지적하지 말라고 알려 줄 것)

텍스트 보고서는 모든 백슬래시를 두 배로 표시(단사 표시) · QA-SYS-10은 선택 extras 부재 시 "건너뜀(환경)" · `corpus verify`는 라벨 없는 파일에 exit 1 · `corpus split` 비율 정규화 · FastAPI 자동 문서 비활성 · `--port 0` 거부 · Windows/macOS 경로는 가짜 객체·mock으로만 검증 · python-markdown의 리터럴 백슬래시는 단사·무링크면 허용 · 비프로필 자산(face_landmarker.task, syncnet_v2.model, sfd_face.pth)은 운영자가 핀을 넣기 전까지 거부 · Linux 외 SIGKILL 자손 정리는 최선 노력 · 과거 커밋 b914bce…7fefde4의 SIG_IGN 문제는 `docs/KNOWN-HISTORICAL-ISSUES.md`에 문서화 · 같은 mtime/ctime/size/inode의 매니페스트 제자리 수정은 프로세스 내 미감지(문서화된 잔여) · 골든 출력은 mediapipe/torch/deep-signals 출력 미포함(문서화된 잔여) · api-serve의 Ctrl-C는 uvicorn이 처리 · 도구 폴더 위치(사용자 결정 2026-10-12, R21-10): HOME에서 정해지는 도구 폴더(`~/.cache/deepfake-lens`의 로그·히트맵·numba 캐시)가 검사 대상 폴더·분석 파일의 폴더·서버 기본 루트와 겹치거나 HOME이 상대 경로이면 거부하지 않고 증거 밖의 첫 후보(OS 캐시 폴더, `$XDG_CACHE_HOME`, Windows의 `%ProgramData%\deepfake-lens\<사용자>`, 시스템 임시 폴더의 사용자 폴더)로 옮기고 알림 한 줄을 낸다 — 후보가 없을 때만 종료 코드 2. Windows 후보(사용자 결정 2026-10-14, R22-2): `%LOCALAPPDATA%`·`%TEMP%`가 모두 프로필 안이라 프로필 밖 `%ProgramData%\deepfake-lens\<사용자>`를 소유자 전용 ACL(SYSTEM·Administrators·소유자)로 만들어 Mac/Linux처럼 옮긴다 — 다른 후보와 같은 규칙(이 사용자 소유, 다른 계정이 쓰기·삭제·권한 변경 불가, 링크·정션 아님, 상위 폴더에서 다른 계정이 지우거나 바꿀 수 없음)으로 확인하며 검증은 mock으로만 한다(23차 R23-6: 상속 전용 `(IO)` 항목은 상위 폴더 판단에서 빼고, 이 도구가 만드는 상위 폴더에는 보호된 ACL을 준다 — 잔여: 다른 계정이 상위 폴더를 먼저 만들었으면 그 계정 소유라 이 사용자에게는 이 후보를 쓰지 않는다). 운영자가 환경 변수로 지정한 폴더(`DEEPFAKE_LENS_LOG_DIR`·`DEEPFAKE_LENS_HEATMAP_DIR`)가 증거 안이면 계속 종료 코드 2. · 검토·피드백 기록 위치(사용자 결정 2026-10-14, R22-3): 웹 GUI의 `reviews.json`·`feedback.jsonl`은 증거와 무관한 사용자별 고정 위치(macOS `~/Library/Application Support/deepfake-lens`, Linux `$XDG_DATA_HOME/deepfake-lens`(절대 경로일 때) 또는 `~/.local/share/deepfake-lens`, Windows `%APPDATA%\deepfake-lens`)에 둔다 — 그 위치가 서버의 검사 대상 폴더와 겹치거나 정할 수 없으면(상대 HOME 등) 저장을 거부(HTTP 409, 한국어 사유)하고 증거에 쓰지 않는다. 기존 `~/.deepfake-lens`의 기록은 한 번 복사한 뒤 새 폴더에 표시를 남기며 원본은 그대로 둔다. · **2026-10-17 사용자 결정(P0R)**: 게이트 0은 위험 기반 종료(8.1장)로 선언하며 Low·Cosmetic 지적은 `BACKLOG.md`로 간다(사실과 다른 문구는 예외) · 게이트 범위는 `scan`, `classify`, `verify-report`, `evidence-statement`, `doctor`, `web`, `api-serve`, `corpus`, `vendor-weights pin`이며 다른 명령의 경로·문구·도움말 지적은 Low(6장) · Android 모듈은 `extras/android/`(패키지·게이트 밖) · `/api/report`는 서버가 보관한 검사 작업으로만 만들고 요청의 다른 필드는 400(게시 행·옵션·요약을 받지 않는 것은 설계) — 업로드 행은 서명 제외 클라이언트 행(6장 예외)으로 남긴다 · `side`는 이 브랜치에서 `plaintiff`만(다른 값 400 — 제출 측 기능은 GUI 브랜치에서 들여올 예정) · 서명은 HMAC-SHA256으로 남고 비대칭 서명(Ed25519)은 1단계 시작 시(BACKLOG BL-1); 생성 시각은 서명자의 시계(RFC 3161은 BL-2) · 동결된 ITL에만 연결된 C2PA 서명은 보통 근거(결론 불가), 인증서 폐기는 오프라인으로 확인하지 않음(문구로 명시) · 강한 원본성 근거와 강한 생성 근거가 함께 있으면 판단 불가(규칙 2의 예외, `decision.CONFLICT_RULE`) · 생성 도구 이름 하나는 보통 근거이고 `exif.Model`·`LensModel`·`Credit`·`dc:creator`의 이름은 근거가 아님 · 절대 이름이 PATH_MAX보다 긴 폴더는 쓸 수 없는 이름이라 거부(종료 코드 2, `test_axis_product`)

## 8. 루프를 이어 가는 방법과 게이트 0 선언 기준

### 8.1 게이트 0 선언 기준 (사용자 결정 2026-10-17: 위험 기반 종료, P0R-1)

이전 기준("독립 검증의 불일치 0건")은 탐침 영역을 매 라운드 새로 여는 한 도달할 수 없고, 도달해도 결함이 없다는 뜻이 아니었다(전체 리뷰 `fable_review.md` 3.2). 게이트 0은 다음 두 조건이 **모두** 참일 때 선언한다.

(a) 세 축의 불변식 테스트가 있고 게이트(`gate.sh full`, `:latest`·`:tech`·`:tech2` — `:tech2`는 PerTh 가중치가 있는 유일한 이미지라 TB-2 디코더 경로가 여기서만 돈다(28차 R28-3); `gate.sh gui`, `:gui`)에서 통과한다.

| 축 | 불변식 | 테스트 |
| --- | --- | --- |
| 판정 | (i) 어떤 검사 예외도 판단 불가 밖으로 나가지 않는다 (ii) 어휘적 근거·참고 신호·보정되지 않은 통계 근거는 결론을 바꾸지 못한다 (iii) 결론이 나면 결과 JSON에 그 결론을 만든 강한 재현 가능한 기록 근거(결정적 근거) 또는 측정된(보정 id·측정 코퍼스·임계값) 통계 근거가 있다 (iv) CLI·web·api의 verdict·evidence·coverage가 바이트 단위로 같다 | `test_invariants_verdict`(hypothesis 속성 테스트 — 무작위 근거·커버리지·등급 조합), `test_axis_product`(경로 축 곱), QA-OUT-1/2/4 |
| 서명 | (i) 서명 본문에 클라이언트가 보낸 문자열이 없다(사건 필드·업로드 이름 예외는 6장) (ii) 서명된 행 집합 = 서버가 보관한 검사 작업의 행 집합 | `test_servers.ReportSignsNoPostedTextInvariantTest`, `test_servers.ReportSignedSetIsTheServersInvariantTest` |
| 증거 | 검사 전후 증거 폴더의 sha256·mtime·파일 수가 같다 | QA-IN-1(`test_qa_in`) |

(b) **직전 두 번의 독립 검증 라운드**가 위 세 축에서 Med 이상 지적을 0건 냈다. Low·Cosmetic은 `BACKLOG.md`로 가며 막지 않는다. 단, 사용자에게 보이는 사실과 다른 문구는 심각도와 무관하게 막는다(그 라운드는 (b)를 충족하지 않는다).

   **막는 거짓 문구의 범위(사용자 결정 2026-10-19, 27~31차 결과 — 다섯 라운드 연속 Med 이상 0건, 거짓 문구 1·1·1·5·4건 중 다수가 브라우저·GUI가 보내지 않는 손으로 만든 API 요청의 거절 사유):** 다음에 나오는 거짓 문구는 심각도와 무관하게 라운드를 막는다 — ① 결론·판정 문구, 근거·한계·커버리지(실행/건너뜀/실패와 그 사유), ② 해시·파일 이름·파일 수 등 증거 식별 정보, ③ 서명된 보고서·증거설명서·`verify-report`, ④ GUI 화면(알림·오류 문구 포함)과 GUI·브라우저가 실제로 보내는 요청에 대한 응답. **브라우저·GUI가 보내지 않는 형식의 API 요청**(손으로 만든 multipart 경계·줄 끝·헤더 대소문자·문자셋, 잘못된 JSON 등)에 대한 **거절 사유 문구**만 틀린 경우는 Low로 분류해 `BACKLOG.md`로 보내고 라운드를 막지 않는다. 단 그런 요청이 500·서버 정지·증거 폴더 쓰기·잘못된 판정이나 서명으로 이어지면 원래 심각도대로 분류한다.

선언하면 SPEC "0단계의 목표"의 각 항목과 위 표의 테스트 이름·게이트 로그(측정 조건 포함)를 한 문서(`docs/phase0/GATE0.md`)에 적고, 계획서 5장의 1단계(다섯 트랙: 이미지·문서 먼저, 음성·영상·텍스트 시차 병렬)로 넘어간다. 1단계 진입 시 결정할 사항(예산·GPU·인력·코퍼스 범위)은 계획서 8장에 있다.

### 8.2 한 라운드의 순서

한 라운드는 다음 순서의 커밋으로 끝난다. 각 단계가 무엇을 바꾸는지 섞지 않는다.

1. 검증 문서 커밋: 가장 최근 `verify_roundN.md`.
2. 지적별 수정 커밋 — **Med 이상과 거짓 문구만**(6장). Low·Cosmetic은 수정하지 않고 `BACKLOG.md`에 옮기는 커밋 하나로 처리한다. Opus 에이전트에게 SPEC.md + 라운드 문서 + 이 인계서를 주고 위임해 왔다.
3. 인계서 갱신 커밋: 이 문서. 기록 전용 커밋으로 바뀌는 상태는 적지 않는다.
4. 기록 전용 커밋: 새 ID를 추적표 `ids[]`에 추가하고 5장 절차로 CONFORMANCE·추적표를 재생성한다(세 파일만).
5. **구현에 참여하지 않은** 새 에이전트에게 독립 검증을 맡긴다. 8.3의 형식을 따른다.
6. 8.1의 두 조건이 충족되면 게이트 0을 선언한다.

### 8.3 검증 라운드 지시 형식 (P0R-1에서 바꿈)

이전 형식의 "이전에 다루지 않은 영역 40건 이상"은 폐지한다 — 탐침 영역을 열 때마다 Low가 무한히 나오고, 그것이 라운드마다 지적 수가 7~13 사이에서 진동한 원인이었다. 새 지시의 골격:

- **게이트**: `gate.sh full`을 `:latest`와 `:tech`에서(플레이크 확인을 위해 2회)와 `:tech2`(PerTh 가중치, R28-3)에서, `gate.sh gui`(`:gui`). 측정 조건(이미지 태그·git 이력 유무·`--cpus`·동시 실행 여부)을 결과와 함께 적는다.
- **세 축의 적대적 탐침**(범위는 6장의 게이트 범위 명령만):
  - 판정: 예외를 일으키는 입력(손상·잘림·권한·거대 크기·0바이트·위장 확장자)이 결론으로 새는지, 어휘·참고·미보정 통계 신호만으로 결론이 나는지, 자기 선언 메타데이터의 심기·지우기·모순(원본성 서명 + 생성 선언)이 어떻게 판정되는지, 세 진입점의 결과가 바이트 동일한지.
  - 서명: 보고서 요청에 클라이언트 문자열을 심어 서명 본문에 들어가는지, 서명 행 집합이 서버 작업과 다르게 만들어지는지(작업 id 위조·만료·다른 토큰·검사 뒤 파일 변경·취소된 작업), 서명 본문 변조가 `verify-report`에서 잡히는지.
  - 증거: 모든 게이트 범위 명령과 서버 경로가 증거 폴더에 쓰지 않는지(sha256·mtime·파일 수).
- **회귀 탐침**: 직전 라운드의 수정마다 그 수정이 닿는 다른 호출 경로를 새 탐침으로 확인한다(수정 커밋 본문의 "다른 호출자" 목록이 출발점).
- **축 곱**: `test_axis_product`가 고정한 축(상대/절대/`.` 경로 × 깊은 작업 폴더 × 비ASCII 이름 × workers 1/4 × 폴더/압축, 그리고 28차 R28-1의 업로드 경로(폴더 검사·압축 업로드·단일 업로드·`/api/check` 업로드) × 이름에 의존하는 검사, 29차의 업로드 진입점 동치 표 `UploadEntryPointEquivalenceTest`(web·api-serve `/api/analyze-upload`, web `/api/check`, web `/api/compare` × 0바이트·잘림·위장 확장자·텍스트·WAV·zip·중첩 zip·손상 zip·tar.gz·쓸 수 없는 이름의 압축 파일·경로 순회·Windows 경로·`.`/`..`/`/`·NUL·긴 이름·NFD·surrogate·여러 파일·같은 이름 두 파일·파일 이름 없는 파트·파일 수 상한, 30차의 파트 `Content-Type`(`application/octet-stream`·`message/rfc822`·`multipart/related`·`multipart/mixed`·`text/plain; charset=utf-8`·없음) × 브라우저 인코딩 이름(역슬래시 그대로, `"`→`%22`·CR/LF→`%0D`/`%0A`, 비UTF-8 바이트) × boundary 없음·빈 값·불일치·잘림, `/api/compare`의 측정 불가 쌍(0바이트·오디오 아닌 WAV·빈 텍스트 → 수치 없음), 31차의 줄 끝(CRLF·LF·CR·여는 줄만 CRLF·LF 헤더·LF 닫는 경계·LF/CRLF 프리앰블, 그리고 그 본문의 잘림) × 미디어 타입 대소문자(`Multipart/Form-Data`·`Boundary=`) × `filename*` 대체(빈 값·`undefined`·모르는 코덱 → `filename` 또는 UTF-8) × 파트 수 상한(1000개 — 이름 없는 파트·폼 필드 포함, 넘으면 행 없이 400) — 각 행이 같은 바이트를 클라이언트의 base name으로 둔 폴더 검사 행과 같고, 그 이름을 만들 수 없으면 대체 이름 `upload<확장자>`의 폴더 행 + 이름 검사 skipped, 500 없음)) 밖의 조합이 의심되면 그 조합을 표에 추가하는 것을 지적으로 낸다. 파일 바이트를 받는 새 진입점이나 업로드 입력 부류를 더하는 변경은 이 표에 그 진입점·부류를 함께 넣는다(29차).
- **성능**: 직전 검증 커밋과 2130892 대비 scan(이미지 2000개, 오디오·영상 300개, workers 1/4)과 보고서 경로; 25% 이상 저하는 Med.
  - 예외(사용자 결정 2026-10-17): 새로 추가한 검사(새 근거 층·새 판독기) 자체의 비용은 25% 예산에서 뺀다. 단 그 비용을 형식별로 측정해 커밋 본문과 `BACKLOG.md`에 적어야 한다. 기존 경로가 느려진 회귀는 여전히 25% 예산으로 막는다. 첫 적용: techniques-2026b 병합 뒤 문서 600개 +46%/+53%(작업자 1/4, 28차 측정 — 27차 기록의 +33%는 작업자 1에서 작았다, R28-2) — PDF마다 C2PA SDK 판독(3622dc6에서 이미 +28%/+35%)과 TB 문서 층(3622dc6 대비 약 +14%)(BL-23).
- **분류**: 지적마다 심각도와 축, 그리고 사용자에게 보이는 거짓 문구인지 여부. Low·Cosmetic은 "백로그 후보"로 따로 적는다.
- **최종 판정**: 세 축에서 Med 이상 0건(8.1 (b)의 범위에 드는 거짓 문구 0건 포함 — 비브라우저 API 요청의 거절 사유만 틀린 것은 Low 백로그 후보)이면 "예", 아니면 "아니오" + Med 이상 수정 목록. 백로그 후보 목록은 판정과 별도.

## 9. 이 폴더의 파일

| 파일 | 내용 |
| --- | --- |
| `SPEC.md` | 0단계 구현 명세(목표·공통 규칙·갭 목록·WP-A~J·QA 시나리오 원문) |
| `verify_round1.md`, `verify_round4.md`~`verify_round17.md` | 라운드별 검증 지적사항(모두 수정됨) |
| `verify_round18.md` | 18차 검증: R17 종료 확인, R18-1~R18-19(수정됨) |
| `verify_round19.md` | 19차 검증: R18 종료 확인, R19-1~R19-12와 조정자 추가 R19-13(수정됨) |
| `verify_round20.md` | 20차 검증: R19 종료 확인, R20-1~R20-10(수정됨) |
| `verify_round21.md` | 21차 검증: R20 종료 확인, R21-1~R21-9(수정됨; 사용자 결정 R21-10도 21차 수정에서 함께 닫힘) |
| `verify_round22.md` | 22차 검증: R21 종료 확인, R22-1~R22-11(수정됨). 원문 소실 — 검증자 최종 보고를 옮긴 것 |
| `verify_round23.md` | 23차 검증: R22 종료 확인, R23-1~R23-13(수정됨) |
| `verify_round24.md` | 24차 검증: R23 종료 확인, R24-1~R24-11(수정됨) |
| `verify_round25.md` | 25차 검증: R24 종료 확인, R25-1~R25-8과 조정자 추가 R25-9(수정됨) |
| `verify_round26.md` | 26차 검증: R25 종료 확인, R26-1~R26-7과 조정자 추가 R26-8~R26-10(잔여 리스크에서 승격; 이 브랜치에서 수정됨) |
| `verify_round27.md` | 27차 검증(새 형식 첫 라운드): R27-1~R27-5(R27-2·R27-5 수정됨, 나머지는 BACKLOG BL-19~BL-22) |
| `verify_round28.md` | 28차 검증(새 형식 두 번째 라운드): R27 종료 확인, R28-1(거짓 문구 — 수정 대상), R28-2~R28-5(BACKLOG 후보) |
| `verify_round29.md` | 29차 검증(새 형식 세 번째 라운드): R28 종료 확인, R29-1(거짓 문구 — 수정됨), R29-2(BACKLOG 후보 — 이 라운드에서 함께 수정됨). 수정 라운드의 업로드 경로 전수 점검에서 R29-3~R29-6을 찾아 수정 |
| `verify_round30.md` | 30차 검증(새 형식 네 번째 라운드): R29 종료 확인, R30-1~R30-5(모두 Low, 사용자에게 보이는 거짓 문구 — 수정됨: R30-1·R30-2·R30-3·R30-5는 업로드 본문 파서 교체 `form_data.py`, R30-4는 비교의 측정 불가 표시), 백로그 후보 3개(서비스 문서 E20·500 문장 수정, BL-35 처리됨, BL-36) |
| `verify_round31.md` | 31차 검증(새 형식 다섯 번째 라운드): R30 종료 확인, R31-1·R31-2·R31-3·R31-5(모두 Low, 사용자에게 보이는 거짓 문구 — 수정됨: R31-1~R31-3은 `form_data.py`의 줄 끝·미디어 타입 대소문자·빈 `filename*`, R31-5는 GUI 빠른 검사의 압축 파일 알림), R31-4(500 — 함께 수정됨). 백로그 후보 4개는 수정 라운드에서 ID를 주어 수정: R31-6(파트 수 상한 1000), R31-7(화자 비교의 길이·에너지 하한), R31-8(문체 비교 하한은 보이는 글자만), R31-9(따옴표 없는 이름의 맨 LF — R31-1의 줄 끝으로); 남은 부분은 BL-37(직류 WAV)·BL-38(맨 LF 줄 끝의 모호성) |
| `ux_audit_ec0d057.md` | 18차와 함께 한 UI/UX/AX 감사(ec0d057 기준) |
| `BACKLOG.md` | 게이트를 막지 않는 항목(Low·Cosmetic·비게이트 명령의 지적, 1단계로 연기한 결정 — BL-1 Ed25519 등). 형식은 문서 머리말 |
| `HANDOFF.md` | 이 문서 |
