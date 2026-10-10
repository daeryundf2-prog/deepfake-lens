# 독립 검증 라운드 9 결과 (HEAD 187dbd7) — 전부 수정 (면제 없음)

회귀 없음. P2, P3(재귀), P6, P9, P10, P12, P14 종료. 남은 항목:

R9-1 (Med-low, P7). `tri:::c.png`가 `tri\:\::c.png`로 이스케이프되어 여전히 `::` 포함 → 홀수 연속 콜론 3개 이상에서 충돌. /api/report가 제외, GUI 미리보기 없음, 미기록 파일 집계가 멤버로 취급. (result_text.escape_row_path, row_identity) → 이스케이프를 문자 단위로(`:` → `\:` 전부, 또는 `::` 분리자를 쓰지 않는 별도 필드 기반 식별로 전환해 표시 문자열만 `::`), 왕복 테스트에 `:::`, `::::`, `\:`, `\\::` 포함.
R9-2 (Med, P5/P3). `--allow-symlinks` 평면 스캔의 하위 폴더 파일 수 집계가 심볼릭 링크 폴더(`/`, `..`, `.`, 자기 자신 포함)를 따라감 → 146800개, 실행마다 변동, 50개 링크가 50번 집계. (scan_cache.subfolder_file_counts) → 집계도 (device, inode) 방문 집합·상위/순환 링크 제외·동일 폴더 1회·상한 적용; 테스트: 평면+allow-symlinks+링크 매트릭스, 결정성.
R9-3 (Low-med, P8). `/api/scan/stream?directory=`에 없는 폴더/파일, `/api/check/stream`에 file_path·text 둘 다 없음 → 200 + SSE error 이벤트 → 스트림 시작 전 검증해 404/400 JSON; 오류 표 행·테스트 추가(스트림 엔드포인트의 사전 검증 전부).
R9-4 (Low, P11). `video --frame-root <folder>/frames --extract`, `--output-dir`가 검사 폴더 안 생성 허용 → cli_inputs.OUTPUT_FOLDER_ATTRS에 모든 출력 폴더 옵션 등록(파서 메타테스트로 `*-dir`/`*-root`/`--out*` 전부 등록 강제).
R9-5 (Low, P4). feedback labels 파일에 BOM/잘린 JSONL → 0건으로 조용히 읽고 exit 0 → BOM 제거 후 파싱, 잘린 줄은 "오류: 피드백 파일 n행을 해석할 수 없습니다" exit 2.
R9-6 (Low, P13). 영어 탐지기 미탐: `verdict=fake`, `result:fake`, `결과=fake`, `fake-image`, `deep-fake`, `AI-generated`, `AIGenerated`, `ai_generated`, `fakes`, `faked`, `#fake`, `authentic입니다` → `=`,`:`,`#` 토큰 분리, 영어 어간 처리(복수/과거형 s/ed/ing 제거 후 사전 조회), 한글 접미 분리를 모든 결론 단어에 적용.
R9-7 (Low). `--install BUNDLE_DIR` 영어; 위치 인수 플레이스홀더(`folder`, `file`, `file_a`, `report`) 영어; 에코된 입력의 개행 미이스케이프 → 위치 인수 metavar 한국어(`<폴더>`, `<파일>`, `<파일A>`, `<보고서>`), `--install <묶음 폴더>`, 에코 시 개행·제어문자 `\n` 표기로 이스케이프.
R9-8 (Low). 서비스 문서: `/api/review-marks`가 문서화됐으나 404; "업로드 엔드포인트 없음" 문장이 틀림(api-serve에 /api/analyze-upload 있음); `/api/analyze/text?text=` 빈 텍스트가 200 → 문서 정정, 빈 텍스트 400 + 표 행, 문서의 모든 엔드포인트가 실제 존재하는지 메타테스트.
R9-9 (Low, P1). 서명 본문·렌더 보고서에 scan_root 미기록 → 서명 본문에 `scan_root`(read root 기준 상대 경로) 기록, 보고서 헤더에 표시.
R9-10 (잔여 리스크). build_traceability_commits --check가 표 이후 커밋 수와 무관하게 통과 → 표 이후 커밋이 "재생성 커밋 1개(추적성 파일만 변경)"를 초과하면 실패.
