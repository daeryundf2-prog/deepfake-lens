# 독립 검증 라운드 6 결과 (HEAD 0a91234) — 전부 수정 (면제 없음)

회귀 없음. 아래 전부 수정한다(노트 항목 포함).

N1 (차단, =G8). evidence_rules.py:472가 probability >= 0.5일 때만 direction=SYNTHETIC → 프로필 threshold가 0.5 미만이면 규칙 4가 절대 발화 안 함(p=0.40/threshold 30 → undetermined, 스펙은 manipulation). → 보정된 통계 항목의 direction은 threshold 기준(p >= threshold → synthetic)으로 결정하거나, 규칙 4가 calibrated 항목에 대해 direction을 무시. p<0.5, threshold<p인 end-to-end 테스트 추가.
N2 (차단). /api/report(두 서버)가 resolve()로 해시 재계산 → 심볼릭 링크 대상의 해시가 서명 본문·포렌식 PDF·증거설명서 PDF에 기록(in_link.png → target.png 해시). CLI/스캔은 null/"해시 불가(심볼릭 링크)". (reports.py:227-257 `_evidence_sha256`, webapp_api.py:960-970 `_resolve_item_path`) → is_symlink()면 해시하지 않고 스캔 행 값(null) 유지; 웹·API 서버 양쪽의 서명 본문과 두 PDF에서 심볼릭 링크 행을 단언하는 테스트.
N3 (차단, =G10). 커밋 71625d0 메시지에 ID 없음 → 세션 소유자가 리워드.
N4 (차단). `text-advanced <없는 경로|폴더>`, `3d --file <없음>`이 영어 traceback exit 1 → 한국어 오류 exit 2; 파일을 받는 모든 계층 명령(audio, face, video-analysis, inpaint, pixel-analysis, rppg, prnu, evidence, ml-classify, watermark, compare, faceswap-seam, batch, eval 등 N7 목록)도 같은 규칙: 없는 경로 → "오류: 파일을 찾을 수 없습니다: …" exit 2, 폴더/파일 종류 불일치 → 해당 메시지 exit 2, 미지원 형식 → "오류: 지원되지 않는 형식입니다: …" exit 2. 빈 출력으로 exit 0 금지. docs exit-code 표에 전부 기재. 테스트: 모든 서브커맨드 × {없는 경로, 폴더 대신 파일, 파일 대신 폴더} 매트릭스.
N5. HTTP 기본 오류 본문 영어: FastAPI `{"detail":"Not Found"}`, `"Method Not Allowed"`, stdlib 501 `"Unsupported method ('PUT')"` → 두 서버에 한국어 JSON 오류 핸들러(404/405/422/500/501); G9 테스트 요청 목록에 미지 라우트·잘못된 메서드 추가.
N6. `--cache`가 바이트 동일·이름/확장자 다른 파일에 다른 파일의 행을 재사용(zero.png가 empty.jpg의 행을 받고 limitation에 '<root>/empty.jpg' 텍스트 포함; 캐시된 e.jpg는 c2pa:failed, 비캐시는 skipped). (scan_cache.py:222-277) → 캐시 키에 확장자/kind 포함 + 캐시 행 복원 시 경로 의존 텍스트 재생성(또는 경로 의존 텍스트를 캐시 전에 제거); c2pa failed/skipped 불일치 원인 조사·수정; 테스트.
N7. 없는 경로 처리 불일치(N4에 통합).
N8. 스캔 표 유형 열에 raw `unsupported` → 한국어 라벨.
N9. stdlib /api/report가 JSON 본문의 `format`을 무시해 PDF 바이트를 text/html·.html로 반환 → FastAPI와 동일하게 본문 format 존중; 테스트.
N10. 웹/API 포렌식 PDF가 아카이브 멤버에 "해시 불가(압축 파일 구성원…)"(38/45), CLI PDF는 멤버 해시 표시(40/45) → 웹/API도 스캔 행의 멤버 sha256을 사용(스캔 행에 이미 있음).
N11. FastAPI /api/report가 잘못된 입력에 200 + {"error"}, `{"items":[{"path":3}]}`에 HTML 보고서 렌더 → 400 + 한국어 오류, 항목 스키마 검증(contracts 스키마로).
N12. 영어 탐지기 우회: 한국어 조사 접합(`trustworthy하지`, `evidence로`, `reliable한`), 단어 내 하이픈(`non-cal-ib-rat-ed`), camelCase/snake_case 문장(`doNotUseAsEvidence`) → 토큰에서 한글 접미 분리, camelCase/snake_case 분해, 하이픈 제거 후 사전 조회 추가.
N13. 사용자용 사유에 식별자: `(failed:zip:BadZipFile)`, `(allow_symlinks=false)`, `WP-I` → 한국어 설명으로("압축 파일 손상(BadZipFile)", "심볼릭 링크 허용 안 함", "2차 계획 측정 단계").
N14. MAX_NESTED_DEPTH=2 → 3단계 이상 중첩은 판단 불가 → 깊이를 예산(중첩 50개)에 맞춰 상향(예: 8)하고 depth 초과 사유를 유지; 테스트.
N15. `scan_folder`가 (summary, items, thresholds) 반환, 스펙은 (summary, items) → 스펙대로 2-튜플; thresholds는 `options.thresholds`(load_thresholds가 options에 채움) 또는 별도 `scan_folder_with_thresholds`로. 호출자 전부 수정.
N16. tests/qa 보조 테스트 docstring이 "<ID>: 보조 검사 — …" → 스펙대로 모든 테스트 첫 줄에 "<QA-ID>: <통과 기준 원문>"(보조 테스트도 해당 QA의 기준 원문을 그대로 적고 둘째 줄에 무엇을 보조 검사하는지).
N17. libsndfile 사유 "(파일이 없거나 일반 파일이 아님)"이 존재하는 잘린 mp3에 표시 → "오디오 디코드 실패(파일 손상 또는 미지원 코덱)"; 포렌식 PDF "측정됨"과 "in-sample(참고)"이 같은 줄 → 분리; 모든 PDF/웹 출력 헤더가 특정 법무법인 이름·주소·전화를 기본값으로 → 기본값 제거(빈칸/설정값), `--law-firm/--contact` 또는 설정 파일로만.
