# 0단계 백로그 (게이트를 막지 않는 항목)

사용자 결정 2026-10-17(위험 기반 종료, HANDOFF.md 6장·8.1): 독립 검증의 Low·Cosmetic 지적과 비게이트 명령의 지적은 여기에 옮기고 게이트 0을 막지 않는다. **사용자에게 보이는 사실과 다른 문구는 여기에 올 수 없다** — 심각도와 무관하게 다음 라운드의 수정 대상이다.

항목 형식: `ID — 한 줄 요약 (출처, 심각도, 축)`. 처리하면 줄을 지우지 말고 끝에 `→ 처리: <커밋 제목>`을 붙인다.

## 1단계 시작 시 처리(결정됨)

- BL-1 — 보고서 서명을 HMAC-SHA256(대칭 키)에서 비대칭 서명(Ed25519 등, 공개 키 id를 본문에 기록)으로 바꾼다. 대칭 키로는 검증할 수 있는 사람이 위조도 할 수 있다(전체 리뷰 4-1). 0단계는 키 길이 하한(P0R-35)과 HMAC의 한계 문서화까지만 한다. (fable_review §4-1, High, 서명 — 사용자 결정 2026-10-17: 1단계 시작 시 연기)
- BL-2 — 서명 본문 생성 시각을 RFC 3161 타임스탬프 기관의 서명으로 보증한다(0단계는 본문에 생성 시각만 기록 — 자기 주장 시각). (fable_review §4-2, Med, 서명)
- BL-3 — HTML·PDF 산출물 자체에 대한 파일 서명(PDF 디지털 서명). 지금은 서명 JSON을 내장하고 본문 SHA-256을 찍을 뿐 보이는 텍스트 수정은 탐지되지 않는다 — "증거는 JSON, PDF는 열람용"을 문서에 적어 두었다. (fable_review §4-3, Med, 서명)

## 구조·유지보수 (Low)

- BL-4 — `api_server.create_app`(약 970줄)·`cli._run_command`(약 820줄) 분할; 단일 파일 검사 계층 시퀀스(api_server·webapp_api 세 곳)를 `analysis_api`로 통합. (fable_review §2.1/§2.2·§7-11, Low)
- BL-5 — git 이력 의존 단위 테스트(`tests/git_history.py`, QA-SYS-10 삭제 사유, 역사 문서)를 CI 스크립트로 옮겨 얕은 클론·worktree의 건너뜀 차이를 없앤다. (fable_review §2.3·§7-12, Low)
- BL-6 — 한국어 문구 단언을 상수 참조로 바꿔 문구 변경 비용을 낮춘다. (fable_review §7-12, Low)
- BL-7 — `image_metadata.SourceConfidence.MEDIUM`·`core` MEDIUM 집계(항상 0) 잔재 정리. (fable_review §2.4, Cosmetic)
- BL-8 — `experiments/`의 미검증 평가 스크립트·문서 정리(1단계 측정 게이트 경로로 통합 또는 삭제). (fable_review §2.4·§6, Low)
- BL-9 — 커버리지 `fail_under` 63 → 실제(약 80) 근처로 상향. (fable_review §2.3, Low)

## 보안·무결성 잔여 (Low, 문서화됨)

- BL-10 — 히트맵 루트 예외(`core.is_default_heatmap_output`): 읽기 루트 밖이라도 도구 히트맵 폴더의 `*.heatmap.png`는 읽힌다. (fable_review §4-7, Low, 증거)
- BL-11 — 재분석·해시·탐색 사이의 TOCTOU는 최선 노력이다(문서화). (fable_review §4-8, Low, 서명)
