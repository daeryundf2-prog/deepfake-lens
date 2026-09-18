# Deepfake Lens — 안정화·품질 개선 로드맵

> 기준: `84a4eb2` (2026-09-18)  
> 목표: CI 녹색, 평가 신뢰성, 배포 준비, 모델 다운로드 안정성

---

## Phase 1 — 테스트 안정화 ✅

| 항목 | 위치 | 상태 |
|------|------|------|
| 선택 의존성 테스트 격리 | `test_phase2_frequency.py`, `test_v6_probes.py` | ✅ |
| Windows 임시 경로 정규화 | `core.py:239-254` | ✅ |
| 비동기 테스트 경쟁 상태 해결 | `test_servers.py:366` | ✅ |
| 모델 자동 다운로드 차단 | `test_signing.py`, `test_phase_a_hygiene.py`, `test_text_detector.py`, `test_v6_probes.py` | ✅ |
| CI 오프라인 환경변수 | `.github/workflows/deepfake-lens.yml:12` | ✅ |

---

## Phase 2 — 평가 지표 ✅

| 항목 | 위치 | 상태 |
|------|------|------|
| EER 교차점 보간 | `evaluation_metrics.py:48-60` | ✅ |
| AUROC 동점 처리 | `evaluation_metrics.py:27` | ✅ |
| 목표 FPR 임계값 정확성 | `evaluation_metrics.py:63-76` | ✅ |
| 빈 입력/단일 클래스 null 처리 | `evaluation_metrics.py:16-24` | ✅ |
| 실험 스크립트 중복 제거 | `scripts/eval_aide.py`, `experiments/eval_text_detect.py` | ✅ |

---

## Phase 3 — 과거 보고서 정정 ⏳

| 항목 | 파일 | 작업 |
|------|------|------|
| AIDE AUROC/EER 재계산 | `experiments/AIDE_EVALUATION.md` | 원시 점수 파일 확인 후 재계산 또는 미검증 표시 |
| 텍스트 EER 재계산 | `experiments/text_eval_report.json` | 단일 클래스 케이스 포함 |
| 휴리스틱 EER 재계산 | `experiments/text_eval_heuristic.json` | 위와 동일 |

---

## Phase 4 — 결과 규격 통일 ⏳

| 항목 | 위치 | 작업 |
|------|------|------|
| CLI·GUI·API 점수 필드 이름 정리 | `core.py`, `webapp.py`, `api_server.py` | `score`, `confidence`, `verdict` 표준화 |
| 비가용 결과 상태 규격 | 전체 | `null` + `reason` 필드 일관성 |
| JSON 출력 스키마 문서화 | `README.md` | 출력 필드 명세 추가 |

---

## Phase 5 — 패키징·배포 ⏳

| 항목 | 파일 | 작업 |
|------|------|------|
| wheel 빌드 검증 | `pyproject.toml` | `pip wheel .` 성공 확인 |
| 모델 경로 분리 | `models/` | 기본 모델 번들링 vs 옵셔널 다운로드 명확화 |
| 엔트리포인트 검증 | `pyproject.toml [project.scripts]` | CLI 명령 동작 확인 |
| 의존성 그룹 정리 | `pyproject.toml` | base / optional 분리 확인 |

---

## Phase 6 — 모델 진단 ⏳

| 항목 | 위치 | 작업 |
|------|------|------|
| CLI doctor 명령 | 신규 CLI 서브커맨드 | 모델 파일 체크섬·경로·의존성 점검 |
| GUI 진단 탭 | `gui/` | 모델 상태·버전·오류 표시 |
| 체크섬 불일치 안내 | `model_adapter.py` | graceful degradation + 사용자 안내 |

---

## Phase 7 — GUI/작업 관리 ⏳

| 항목 | 위치 | 작업 |
|------|------|------|
| 스캔 취소 지원 | `webapp.py`, `gui/` | asyncio.Task 취소 |
| 실시간 진행률 | SSE/WebSocket | 파일별 처리 상태 스트리밍 |
| 부분 결과 반환 | `webapp.py` | 완료된 파일 먼저 반환 |

---

## Phase 8 — Android 안정화 ⏳

| 항목 | 위치 | 작업 |
|------|------|------|
| ViewModel 상태 관리 | `android/` | Activity·Fragment 수명주기 분리 |
| 범위 표시 개선 | `android/` | 전체 폴더·단일 파일 선택 UI |
| 기기별 호환성 검증 | `android/` | API 26+ 테스트 매트릭스 |
| 대용량 파일 메모리 관리 | `android/` | 스트리밍 처리 |

---

## Phase 9 — 릴리스 준비 ⏳

| 항목 | 위치 | 작업 |
|------|------|------|
| CHANGELOG.md 작성 | 루트 | v0.2.0 변경사항 정리 |
| 릴리스 노트 | GitHub Release | 주요 개선사항· Breaking changes |
| 릴리스 태그 | Git | `v0.2.0` 태그 생성 |
| 배포 자동화 확인 | CI/CD | wheel 배포 파이프라인 동작 |

---

## 진행 우선순위

```
Phase 3 (보고서 정정) → Phase 4 (규격 통일) → Phase 5 (패키징)
→ Phase 6 (진단) → Phase 7 (GUI) → Phase 8 (Android) → Phase 9 (릴리스)
```

Phase 3-5는 품질·신뢰성에 직결되어 가장 먼저 진행해야 합니다.
