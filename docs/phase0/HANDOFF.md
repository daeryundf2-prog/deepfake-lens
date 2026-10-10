# 0단계 작업 인계서 (다른 세션에서 이어 하기)

작성: 2026-10-11 · 브랜치 `phase0` · 기준 커밋 `main`(dad9730)

## 1. 한 줄 요약

0단계(기반 고정)의 10개 작업 패키지(WP-A~J)는 전부 구현되어 있고, 계획서 7장의 "독립 검증 → 불일치 전부 수정 → 재검증" 루프를 17회 돌렸다. **17차 검증에서 나온 11건(R17-1~R17-11)이 아직 수정되지 않은 상태**에서 인계한다. 게이트 0(불일치 0건)은 아직 선언되지 않았다.

## 2. 지금까지의 진행 위치

| 항목 | 상태 |
| --- | --- |
| 2차 계획서(사용자·이상적 상태·갭·QA·완료 판정) | claude.ai 문서 "Deepfake Lens 2차 계획서"(탭 2개: 본문, 0단계 구현 명세). 같은 내용이 이 폴더의 `SPEC.md`에 있음 |
| 0단계 구현 | WP-A~J 완료, `phase0` 브랜치 223커밋(main 대비) |
| 독립 검증 | 17회. 라운드별 지적사항은 `verify_round*.md` (라운드 2·3은 문서 파일 없이 커밋 메시지·추적표에만 남음) |
| 테스트 | 633 → 1,555개, 커버리지 80% |
| 적합성 기록 | `docs/CONFORMANCE.md`: 19 통과 / 0 실패 / 4 수동 / 12 1단계 / 1 건너뜀(환경) |
| 추적성 | `docs/TRACEABILITY-COMMITS.md`, `docs/traceability-commits.json` (검증 ID → 스펙 갭 G1–G34) |
| 다음 할 일 | `verify_round17.md`의 R17-1~R17-11 수정 → 기록 재생성 → 18차 독립 검증 |

라운드별 추세: 초반(1~5차)은 판정을 틀리게 만드는 치명 결함(검사 실패가 '낮음'으로, 학습 안 된 모델이 한국어 판정, 다른 파일 해시 기록 등), 중반(6~11차)은 경로·인코딩·서명 범위·보고서 렌더링, 후반(12~17차)은 비UTF-8 파일명, 신호 처리, 자식 프로세스 정리 같은 운영 엣지 케이스. 14차 이후 매 회차 회귀 0건, 결론(판정)에 영향 주는 결함 0건, 성능 영향 ±6% 이내.

## 3. 레포를 받는 방법

이 작업은 클라우드 작업 공간에서만 이루어졌고 **GitHub에는 아직 push되지 않았다**(작업 공간의 GitHub 토큰이 유효하지 않음). 함께 전달한 `deepfake-lens-phase0.bundle`로 받는다.

```bash
git clone https://github.com/daeryundf2-prog/deepfake-lens
cd deepfake-lens
git fetch /경로/deepfake-lens-phase0.bundle phase0:phase0
git checkout phase0
git push -u origin phase0      # 원하면 GitHub에 올려 두기
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
- 커밋 메시지 끝 두 줄: `Co-Authored-By: Claude …` + `Claude-Session: …`.
- 면제 없음: 검증자가 "노트/잔여 리스크"로 분류한 것도 수정 대상으로 다음 라운드 문서에 넣었다. 단, 아래 "합의된 결정"은 재지적하지 않는다.
- 테스트 약화 금지. 결함을 고정하던 테스트만 고치고 ID 주석을 남긴다. 테스트 삭제는 `docs/TEST-DELETIONS.md`에 커밋 제목으로 기록.
- 커밋 메시지는 실제 동작을 정확히 적는다(과장 금지 — 16차에서 지적됨).

## 7. 합의된 결정 (검증자에게 재지적하지 말라고 알려 줄 것)

텍스트 보고서는 모든 백슬래시를 두 배로 표시(단사 표시) · QA-SYS-10은 선택 extras 부재 시 "건너뜀(환경)" · `corpus verify`는 라벨 없는 파일에 exit 1 · `corpus split` 비율 정규화 · FastAPI 자동 문서 비활성 · `--port 0` 거부 · Windows/macOS 경로는 가짜 객체·mock으로만 검증 · python-markdown의 리터럴 백슬래시는 단사·무링크면 허용 · 비프로필 자산(face_landmarker.task, syncnet_v2.model, sfd_face.pth)은 운영자가 핀을 넣기 전까지 거부 · Linux 외 SIGKILL 자손 정리는 최선 노력 · 과거 커밋 b914bce…7fefde4의 SIG_IGN 문제는 `docs/KNOWN-HISTORICAL-ISSUES.md`에 문서화 · 같은 mtime/ctime/size/inode의 매니페스트 제자리 수정은 프로세스 내 미감지(문서화된 잔여) · 골든 출력은 mediapipe/torch/deep-signals 출력 미포함(문서화된 잔여) · api-serve의 Ctrl-C는 uvicorn이 처리.

## 8. 루프를 이어 가는 방법

1. `verify_round17.md`의 R17-1~R17-11을 수정한다(Opus 에이전트에게 SPEC.md + 라운드 문서 + 위 게이트·규칙을 주고 위임했었다).
2. 새 ID를 추적표 `ids[]`에 추가하고 기록을 재생성한다(5장).
3. **구현에 참여하지 않은** 새 에이전트에게 독립 검증을 맡긴다. 지금까지 쓴 검증 지시의 골격:
   - 게이트 전부(두 환경, 플레이크 확인을 위해 반복 실행), 얕은 클론 동작, 커밋별 게이트
   - 직전 라운드 항목을 새 탐침으로 종료 확인
   - 직전 기준 커밋 대비 회귀 탐색(픽스처 + 60행 이상 악성 폴더), 결정성(3회·워커 4·이름 변경·캐시 cold/warm·동시 4개), 서명 변조, doctor, 성능(2000파일, 25% 이상 저하 시 지적), 이전에 다루지 않은 영역 40건 이상
   - 새 테스트 감사, WP-A~J·규칙 1~7·QA 시나리오 전수 대조
   - 최종 판정: 불일치 0건이면 "예" + 1단계 잔여 리스크, 아니면 "아니오" + 수정 목록
4. 불일치 0건이 나오면 게이트 0 통과를 선언하고, 계획서 5장의 1단계(다섯 트랙: 이미지·문서 먼저, 음성·영상·텍스트 시차 병렬)로 넘어간다. 1단계 진입 시 결정할 사항(예산·GPU·인력·코퍼스 범위)은 계획서 8장에 있다.

## 9. 이 폴더의 파일

| 파일 | 내용 |
| --- | --- |
| `SPEC.md` | 0단계 구현 명세(목표·공통 규칙·갭 목록·WP-A~J·QA 시나리오 원문) |
| `verify_round1.md`, `verify_round4.md`~`verify_round17.md` | 라운드별 검증 지적사항(17차는 미수정) |
| `HANDOFF.md` | 이 문서 |
