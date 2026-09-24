# CHARTER — deepfake-lens

## Role
Screening plane — 딥페이크 선별 연구 프로토타입 (ML Research Prototype).

## Do
- 로컬 이미지/영상 딥페이크 스크리닝 보조
- 모델 해시·revision·라이선스 기록

## Don't
- checkpoint를 `weights_only=False`/`torch.load` 기본값으로 로드 금지 — safetensors 또는 `weights_only=True`만
- C2PA SDK 오류를 "manifest absent"로 변환 금지 — `valid/absent/invalid/unavailable` 4값 유지
- test split을 validation에 포함 금지 — group/content/subject 단위 고정 분리
- 서버 모드에서 외부/미등록 checkpoint 금지
- 일반 목적 딥페이크 판정기로 포지셔닝 금지 — screening 보조 도구

## Contracts
- Consumes: rapid/frametrace 산출물 (lazy-evidence-case-v1)
- Produces: 스크리닝 결과 (model-assisted, limitations 필수)
- Vendored: `contracts/` (lazy-contracts, hash-pinned)

## Claims allowed
`model-assisted` 전용 — 점수는 항상 limitations와 함께. 독립 평가 없이 정확도 주장 금지.
