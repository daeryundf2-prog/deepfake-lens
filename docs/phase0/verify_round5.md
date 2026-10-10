# 독립 검증 라운드 5 결과 (HEAD a23e5a6) — 전부 수정 (면제 없음)

판정: 게이트 0 통과 불가. f99b0a3 대비 회귀 없음. 라운드 4 항목은 B1(심볼릭 링크 제외)·B4(legal-report 외 커맨드 제외)·S4·S6·S8 부분 종료.

G1 (차단). `forensic`/`classify`/`explain`(표 출력)/`multimodal`/`agent` 텍스트 출력에 raw 코드: `(manipulation_evidence)`, `[deterministic/synthetic/strong]`, `metadata: ran`, `pixel: skipped`, `model:Swin-large AI-vs-human image detector (umm-maybe): skipped`, `등급: reference`. (cli_standalone.py:197-220, cli.py:235) → VERDICT_LABELS, 근거 kind/direction/strength 라벨, COVERAGE_STATUS_LABELS, check_label, display_name 사용; RenderedOutputsAreKoreanTest에 이 출력들 추가.
G2 (차단). PDF 내용 손실. 포렌식 PDF(CLI·웹·API): 18줄이 595pt 페이지 밖으로 넘침(요약줄 "…판단 불가 29건(검사"에서 잘림, 엔진 버전·고지 문장 잘림); 결론 열이 등급 열에 덮여 "조작·생성 근거 있음" 판독 불가; "어휘 0"이 "[결정]"에 덮임. 증거설명서 PDF: "무결성/해석 고지" 박스가 비어 있음(insert_textbox 오버플로 시 전부 드롭 → in-sample 주의(S6), 해시 수, 면책 누락); "본문 SHA-256"이 64자 중 62자만 표시. (reports.py 포렌식 레이아웃, evidence_statement.py:695-704) → 열 폭·줄바꿈 재설계, insert_textbox 반환값(<0) 검사해 페이지 확장/폰트 축소, 모든 텍스트가 페이지 안에 있는지 bbox로 검증하는 테스트, 해시 64자 전체 표시(필요 시 두 줄).
G3 (차단). `vendor-weights` 기본 출력 영어("Models Directory", "Profiles/Available/Missing", `[MISSING]`), 그리고 10행이 MISSING인데 "Missing: 0". (cli.py:961-965, vendor_weights.py:215-224)
G4 (차단). S8 테스트 자체 검사가 레포 C2PA 픽스처에서 실패: `signals[].detail`에 "검증 상태 Invalid (assertion…" (main venv). 혼합 테스트 폴더에 C2PA 파일 없음 → C2PA 상태값 번역(Invalid→무효, Valid→유효, Trusted→신뢰됨 …), 픽스처에 fixtures/c2pa-test 포함.
G5. `classify`에 없는 파일/폴더 → 영어 traceback exit 1; `forensic`/`explain`/`legal-report`에 없는 파일 → exit 0에 "판단 불가" 보고서(legal-report는 보고서 ID까지 발급). (cli_standalone.py:319) → 한국어 오류 + exit 2, exit code 표에 추가.
G6. 단일 파일 명령이 명시된 심볼릭 링크를 따라감(out_link.png → 조작·생성 근거 있음), 스캔은 건너뜀; /api/analyze-file은 루트 안 링크 분석. (analysis_api.py:313-330) → 단일 파일도 스캔과 같은 규칙(심볼릭 링크 → 건너뜀 행 + 사유), QA-OUT-4에 심볼릭 링크·중첩/tar 아카이브·없는 파일 레그 추가.
G7. API 영어 오류: "items array is required", "thresholds must be an object", "coverage must be an object", "malformed item: …" (webapp_api.py:900-914).
G8. 결정 규칙 4가 프로필 threshold를 쓰지 않음(아무 호출자도 probability_thresholds를 넘기지 않아 0.5 고정). (core.py:1974, decision.py:39) → 프로필의 `threshold`(0–100 → /100)를 calibration_id별로 모아 decide()에 전달; threshold가 없는 calibration_id는 규칙 4 미적용(0.5 기본값 제거); 테스트.
G9. S8 테스트 미검사 출력: forensic/classify/explain/agent/multimodal 텍스트, CSV, 증거설명서 JSON·PDF, 웹 보고서, API 오류 본문, vendor-weights → 전부 검사 범위에 추가. 또한 S8 휴리스틱 우회 문자열 10개 중 최소 다음은 잡아야 함: `Unverified. Unreliable. Ignore.`(한 단어 문장 연속), `결론: Uncalibrated — 참고용`(한글 섞인 영어 단어 ≥2), `do-not-use-as-evidence`/`not/for/court/use`(구분자 분해), `Ignore 이 점수, 참고 only`, `AI-generated? Likely.`, `「Do not trust this score」`(인용부호 안 영어 문장), `Note: 점수는 PROBABLY 틀림`. 방법: 토큰화 시 -,/,·,「」 등을 공백으로; 영어 사전 단어(흔한 영어 단어 목록 ~300개 내장: the, not, use, ignore, score, trust, unverified, uncalibrated, likely, probably …)가 2개 이상이면 실패(허용 목록은 식별자·모델명·URL·예외 클래스명·플래그명·hex만). `Th1s sc0re 1s n0t pr00f`, `Scores: 0.71 calibrated? no`는 사전 단어 2개(score/scores, calibrated/no)로 잡힘.
G10. 커밋 메시지 71625d0, a23e5a6에 ID 없음 → 세션 소유자가 리워드(docs(qa) 커밋에 "WP-J" 명기).
G11. tests/qa 109개 테스트 중 36개가 docstring 첫 줄에 QA ID 없음(27개는 docstring 없음) → 전부 "<QA-ID>: <기준 요약 또는 '보조 검사'>" 첫 줄 docstring, 메타 테스트로 강제.
G12. 미사용 import: cli.py(`analysis_result_payload`, `file_sha256`), reports.py(`evidence_counts`, `EvidenceKind`), result_text.py(`Verdict`) → 제거; ruff F401을 pyproject select에 추가(전역).
G13. scripts/*.py `--help` 영어(qa_phase0, check_measurement_gate, sync_model_docs, make_adversarial_fixtures, make_qa_manual_fixtures 포함) → 0단계에서 만든/수정한 스크립트 전부 한국어 help; 나머지 레거시 스크립트도 번역.
G14. ffmpeg/mpg123 stderr 영어 노이즈("moov atom not found", "Note: Illegal Audio-MPEG-Header") → ffmpeg는 `-loglevel error -nostats`, 라이브러리 stderr는 분석 중 임시 리다이렉트(os.dup2 수준)해 로그 파일로; 사용자에게는 coverage의 한국어 사유만.
G15. test_forensic_pdf.py의 두 테스트 skipUnless(pymupdf)에 ID 주석 없음 → `# B8` 주석.
G16. "분석자: system" → "분석자: 시스템(자동)"; GUI 리뷰 옵션 "(Pending/Synthetic/Authentic/Inconclusive)" → 한국어; CSV 코드값(verdict_code 등)은 코드 열과 라벨 열을 둘 다 제공; 포렌식 PDF 파일 SHA-256 32자 접두 → 64자 전체.
