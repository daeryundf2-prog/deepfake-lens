# 독립 검증 라운드 27 결과 (HEAD 8178e77) — 새 형식 첫 라운드

검증 대상: 비공개 클론 `scratchpad/r27/repo`, 브랜치 phase0 HEAD 8178e77(코드는 b83ddd8), 로컬 main = dad9730. 수정한 것은 없음.
측정 조건: Docker Desktop(10 CPU, 7.75 GiB), `--cpus 4`, `/repo`는 git 이력이 있는 클론(`.git` 디렉터리). 같은 시간에 다른 세션의 컨테이너(tb-a 약 200%, tb-b 약 100% CPU)가 돌고 있어 CPU 경합이 있었음. 이 검증의 컨테이너는 한 번에 최대 2개.

## 게이트 결과

| 게이트 | 이미지 | 결과 | 비고 |
| --- | --- | --- | --- |
| `gate.sh full` | `deepfake-lens-gate:latest` | **GATE rc=0** (27분 49초, 경합 있음) | /venv 1997개 OK(skipped=117), /venv-api 1997개 OK(skipped=88), ruff 통과, mypy 145개 파일 문제 없음, coverage 80%, cli_smoke OK(서브명령 45개/기능 검사 26개), 측정 게이트 10/10, 계약 PIN 일치, stress_shutdown 30회 중 실패 0, qa-main 202개 테스트 실패 0(통과 19, 수동 4, 1단계 12, 건너뜀(환경) 1) |
| `gate.sh gui` | `deepfake-lens-gate:gui`(`--shm-size=1g`, `--cpus 3`) | **GATE rc=0** | 스모크 2개 모두 `GUI smoke PASS`(fixtures, /tmp/adv) |
| `gate.sh full` | `deepfake-lens-gate:tech` | **이 보고 시점에 미완료** | 18:01에 시작해 핸드백 시점까지 /venv unittest 단계였음(경합). 로그: `r27-out/gate-full-tech.log`. 조정자가 끝까지 돌려 확인해야 함 |
| 기록 검사(`--verify-record`, `build_traceability_commits.py --check`) | — | **실행하지 못함** | 컨테이너 슬롯이 :tech와 성능 측정에 묶여 있었음 |

## 축별 요약

- **판정**: Med 이상 0건. 불변식 테스트(`test_invariants_verdict`, `test_axis_product`, QA-OUT)는 :latest 게이트에서 통과. C2PA 해시 제외범위 공격 픽스처는 게이트 안에서 SDK가 Trusted로 보고하지만 c2pa 검사는 실패하고 판정은 판단 불가로 남음(`ExclusionAttackFixturesTest`). 직접 탐침: CLI = web의 verdict·evidence·coverage가 행마다 같음. `Credit`/`Model`에 든 브랜드 단어는 판단 불가. 컨테이너 박스 파서는 64비트·0·초과 크기와 깊이·개수 상한을 처리하고, 처리하지 못하면 실패/부분 커버리지로 기록하며 결론으로 이어지지 않음. 결정 규칙 7(근거 충돌)과 규칙 2/3/5의 순서도 확인함. 남은 지적 R27-1(Low)은 렌더링 완전성 문제.
- **서명**: Med 이상 0건. 거짓 문구 1건(R27-2). 탐침 결과:
  - 위조한 작업 id와 `../../etc/passwd` 형태: 404
  - `exhibit_paths`의 별칭(`./gen.png`)과 중복: 400
  - 재시작(`STORE.forget`) 뒤 보고서: 200. 작업 파일 권한 0600
  - 작업 기록 변조: 409. 다른 키: 409
  - 키 없이 변조한 작업: 200, 서명 없음으로 변조 내용이 나옴(문서화된 동작)
  - 짧은 키: CLI 종료 코드 2, `web`·`api-serve`는 시작을 거부
  - 검사 뒤 파일 변경: 409, 경로와 사유만 돌려줌
  - 서명 본문: 호스트 경로·`/tmp/` 없음, `generated_at` 있음, `executed_models`는 `[]`(기본 엔진 끔이라 실행된 모델 없음 — 맞는 값)
  - `verify-report`: 정상 보고서 rc=0(검증됨), 변조된 보고서 rc=1
- **증거**: Med 이상 0건. 스캔, 보고서(json·html·pdf·evidence), 작업 보관 전후로 사건 폴더의 sha256·mtime·파일 수가 같음. 사건 폴더 안에 작업 파일 없음. 보관 위치가 증거 안이면 메모리에만 보관(테스트로 확인). QA-IN-1은 qa-main에서 통과.

## 지적사항

### R27-2 — 웹 보고서의 「재분석 옵션(서버가 이 옵션으로 모든 행을 다시 분석)」은 P0R-6 이후 사실이 아님
- 심각도 Low, 축 서명(서명 본문 `analysis_options`의 렌더링), **사용자에게 보이는 거짓 문구: 예** → 이번 라운드 수정 대상.
- 재현: 폴더를 `/api/scan`으로 검사한 뒤 `/api/report`에 `{"scan_job": id}`를 보내 html·pdf·evidence를 받으면, 세 형식 모두 머리말에 「재분석 옵션(서버가 이 옵션으로 모든 행을 다시 분석): 픽셀 off · …」이 찍힘(`r27-out/live_probe.log`: `html_reanalysis_label`, `pdf_reanalysis_label`, `evidence_reanalysis_label` 모두 true). P0R-6 이후 보고서는 행을 다시 분석하지 않고 검사 작업의 행을 검사 때 그대로 서명함(`scan_cache.py:92`의 주석도 그렇게 말함). 이 옵션은 검사 작업이 실행된 옵션이다. 계약 문서는 "the label is kept from R19-3"(docs/deepfake-lens-json-contract.md:518)라고 라벨을 그대로 둔 것을 인정함.
- 위치: `deepfake_lens/reports.py:185` `ANALYSIS_OPTIONS_LABEL`, 렌더링 위치 `reports.py:350`(HTML), `:585`(forensic PDF), `evidence_statement.py:723`.
- → 라벨을 「검사 옵션(서버가 이 검사 작업을 실행한 옵션)」 같은 문구로 바꾸고, 「다시 분석」이 없음을 확인하는 테스트로 `ReportRecordsAnalysisOptionsTest`의 `"재분석 옵션"` 단언을 교체. 같은 계열의 낡은 주석·문서(`reports.py:94,104,183,329`, `webapp_api.py:389-392`, `gui.js:3`)도 함께 고칠 것.

### R27-1 — CLI 표 렌더링에 「이 결론이 틀릴 수 있는 이유」가 없고, `scan` 표에는 자기 선언 표시도 없음
- 심각도 Low, 축 판정, 거짓 문구 아님(문구 누락) → BACKLOG.
- 재현: A1111 parameters PNG를 `scan --include-low`로 검사하면 행이 「조작·생성 근거 있음 … # [재현 가능한 기록 근거] 생성 도구 메타데이터」로만 나옴. 「심거나 지울 수 있음」도, 이유 목록도 없음. `classify --format table`은 결론 문장에 표시가 있지만 이유 목록은 없음. JSON·HTML·forensic PDF·증거설명서·GUI에는 둘 다 있음(`live_probe.log`).
- 위치: `cli_render.py:161` `table_row_text`, `cli_standalone.py:214` `format_analysis_result`.
- → `format_analysis_result`에 `conclusion_caveats` 블록을 넣고, `scan` 표 머리말에 자기 선언 결론 개수 한 줄을 넣는 방안. P0R-12가 명시한 범위(결론 문장과 증거설명서)는 지켜졌으므로 Low.

### R27-3 — JSON 계약 문서에 P0R-6 이전 설명이 남아 있음
- 심각도 Low, 축 없음(문서), 거짓 문구: 화면에 나오는 문구는 아님 → BACKLOG.
- docs/deepfake-lens-json-contract.md:55(목록 출처 `walk`/`unknown`, 게시 행 재분석), :168-171(「a report whose re-analysis breaks it is 409」, 게시 member 행 400). `result_types.py:563-592`의 `walk` 출처 주석도 같음.

### R27-4 — 키 없이 운영하면 작업 기록 파일을 고친 내용이 (서명 없는) 보고서에 그대로 나옴
- 심각도 Low, 축 서명, 거짓 문구 아님. 보고서는 `signature: null`과 「서명 없음」 메모를 달고, 이 동작은 scan_jobs 모듈 문서에 적혀 있음 → BACKLOG(운영 안내에 한 줄 추가 후보).

## BACKLOG 추가
- R27-1 — CLI `scan`/`classify` 표에 결론 이유 목록이 없고 `scan` 표에는 자기 선언 표시도 없음 (27차, Low, 판정)
- R27-3 — json-contract 문서와 `result_types` 주석의 P0R-6 이전(게시 행 재분석·walk 목록) 설명 (27차, Low, 없음)
- R27-4 — 보고서 키가 없을 때 작업 기록에 MAC이 없어, 고친 기록이 서명 없는 보고서에 나옴(문서화됨) (27차, Low, 서명)
- 참고(지적 아님): SDXL 48비트 정확 일치는 강한 합성 근거이고, 상세 문구는 흉내 낼 수 있다고 스스로 밝힘(:tech 전용). 1단계에서 보통 강도로 내릴지 검토할 만함.

## R26 회귀 종료 확인
- R26-1…R26-10과 그 원래 결함(R19-13, R20-1, R21-1/5, R22-1, R23-1/3/5, R24-1, R25-1)의 회귀 테스트가 모두 스위트에 있고 :latest 게이트의 두 venv에서 통과(test_archives, test_axis_product, test_native_path, test_scan_cancel, test_servers, test_pdf_layout, test_cli_korean, test_multimodal, test_error_text, test_standalone_contract, qa/test_qa_in, qa/test_qa_out 등).
- 직접 탐침: 변경 감지(R19-13)는 409와 경로·사유만 돌려줌. 행 식별자는 별칭이 다른 행과 섞이지 않음.
- R26-3(macOS 깊은 작업 폴더)·R26-9/10(av-sync 상한, ffmpeg 시간 초과)은 호스트에서 직접 탐침하지 않았음. Linux 게이트 테스트만 해당.
- R26-7 도움말은 cli_smoke 게이트로 확인.

## 성능 (scan, `--no-default-engine`, 픽셀 off, `:latest`, `--cpus 4`, 경합 있음)
작은 폴더 2회 교대 실행(`r27-out/perf.log`; 64×64 PNG 300개, 1초 WAV 40개, 혼합 170개):

| 폴더 | workers | 28db773 (2회) | HEAD (2회) |
| --- | --- | --- | --- |
| image300 | 1 | 1.08 / 2.32 s | 2.04 / 1.94 s |
| image300 | 4 | 2.04 / 1.65 s | 2.88 / 2.78 s |
| wav40 | 1 | 6.84 / 14.35 s | 7.40 / 6.62 s |
| wav40 | 4 | 5.71 / 4.68 s | 5.69 / 5.34 s |
| mixed170 | 1 | 7.39 / 7.89 s | 6.75 / 5.42 s |
| mixed170 | 4 | 6.32 / 6.48 s | 5.85 / 6.38 s |

- 오디오와 혼합 폴더: 저하 없음(잡음 범위 안).
- 이미지 전용, workers 4: HEAD가 약 1초(약 50%) 느림. 다만 전체가 2~3초라 프로세스 시작과 import 비용이 대부분이고, 경합 때문에 잡음이 큼.
- 2000개 이미지와 빈 폴더(시작 비용 분리) 측정(2130892·28db773·HEAD 교대, `perf2.sh`)은 핸드백 시점에 2130892 첫 회만 끝났음(빈 폴더 0.95/0.43 s, image2000 w1 2.58 s). 결과는 `r27-out/perf2.log`에 계속 쌓임.
- **성능 판정 보류**: 25% 예산(§8.3에서 Med)을 넘는지는 perf2 결과로 정해야 함. 이미지 전용 workers=4에서 넘을 가능성이 있음. 넘는다면 축은 「없음」이고, 8.1(b)의 세 축 기준과는 별개로 Med 수정 대상이 됨.

## 최종 판정
게이트 0 판정: **깨끗하지 않음** — 거짓 문구 1건(R27-2). 세 축의 Med 이상은 0건.
수정 목록: R27-2(「재분석 옵션 … 다시 분석」 라벨). 미완료 확인 항목: `:tech` full 게이트의 결과, 기록 검사, perf2(2000개 이미지) 결과 — 이미지 전용 workers=4 저하가 25%를 넘으면 Med(축 없음)를 하나 더함.

## 부록 (핸드백 뒤 도착): perf2 원자료
```
### run1 base2130892 18:09:19
PERF empty workers=1 rc=0 0.95s
PERF empty workers=1 rc=0 0.43s
PERF image2000 workers=1 rc=0 2.58s
PERF image2000 workers=1 rc=0 3.11s
PERF image2000 workers=4 rc=0 5.03s
PERF image2000 workers=4 rc=0 4.53s
ROWS 1000
### run1 base28db773 18:09:41
PERF empty workers=1 rc=0 0.91s
PERF empty workers=1 rc=0 0.47s
PERF image2000 workers=1 rc=0 2.72s
PERF image2000 workers=1 rc=0 2.59s
PERF image2000 workers=4 rc=0 5.08s
PERF image2000 workers=4 rc=0 5.24s
ROWS 1000
### run1 repo 18:10:04
PERF empty workers=1 rc=0 0.92s
PERF empty workers=1 rc=0 0.34s
PERF image2000 workers=1 rc=0 4.77s
PERF image2000 workers=1 rc=0 5.19s
PERF image2000 workers=4 rc=0 7.51s
PERF image2000 workers=4 rc=0 7.18s
ROWS 1000
### run2 base2130892 18:10:37
PERF empty workers=1 rc=0 0.91s
PERF empty workers=1 rc=0 0.42s
PERF image2000 workers=1 rc=0 2.37s
PERF image2000 workers=1 rc=0 2.49s
PERF image2000 workers=4 rc=0 4.32s
PERF image2000 workers=4 rc=0 4.57s
ROWS 1000
### run2 base28db773 18:10:56
PERF empty workers=1 rc=0 0.90s
PERF empty workers=1 rc=0 0.38s
PERF image2000 workers=1 rc=0 2.66s
PERF image2000 workers=1 rc=0 2.58s
PERF image2000 workers=4 rc=0 3.87s
PERF image2000 workers=4 rc=0 3.88s
ROWS 1000
### run2 repo 18:11:15
PERF empty workers=1 rc=0 1.08s
PERF empty workers=1 rc=0 0.66s
PERF image2000 workers=1 rc=0 5.63s
PERF image2000 workers=1 rc=0 5.41s
PERF image2000 workers=4 rc=0 7.92s
PERF image2000 workers=4 rc=0 8.09s
ROWS 1000
PERFDONE
```

## 부록 결론 — R27-5 (핸드백 뒤 확정, perf2)
- 조건: `:latest`, `--cpus 4`, 경합 있음. 64×64 PNG 2000개 폴더(기본 `--max-files` 상한 때문에 1000행 — 세 트리 모두 같음). 2회씩 교대 실행.
- 빈 폴더(시작 비용): 세 트리 모두 약 0.4~1.0 s로 같음.

| 트리 | workers=1 (4회 평균) | workers=4 (4회 평균) |
| --- | --- | --- |
| 2130892 | 2.64 s | 4.61 s |
| 28db773 | 2.64 s | 4.52 s |
| HEAD | 5.25 s (**+99%**) | 7.68 s (**+67~70%**) |

- 시작 비용을 빼면 이미지 1개당 비용이 약 2.2배다.
- **R27-5 — Med(§8.3: 25% 이상 저하), 축 없음, 거짓 문구 아님 → 수정 대상.** P0R 재구성과 techniques-2026 병합 뒤 이미지 전용 스캔이 두 기준 커밋보다 workers 1에서 약 2배, workers 4에서 약 1.7배 느리다. 측정은 `--no-default-engine`·픽셀 off라 모델 비용이 아니다. 늘어난 파일당 계층 중 하나가 원인으로 보인다: 컨테이너 메타데이터, C2PA 신뢰 목록, 표식 문맥, 워터마크(:latest에서는 건너뜀) 검사 준비. 프로파일링으로 확인할 것.
- 최종 판정 갱신: **깨끗하지 않음** — 수정 목록 R27-2(거짓 문구), R27-5(성능 Med, 축 없음). 세 축의 Med 이상은 여전히 0건.
