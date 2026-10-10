# 독립 검증 라운드 8 결과 (HEAD 53251a3) — 전부 수정 (면제 없음)

## 반드시 수정
P1 (High, =X2-R1). /api/report가 행을 "스캔한 폴더"가 아니라 read root 기준으로 해석 → `<root>/caseA` 스캔 후 보고서는 `<root>/target.png`(동명 파일)의 결론·해시를 서명(표시 없음); 동명 파일 없으면 전부 제외. GUI는 lastScanRoot를 보내지 않음. (webapp_api._rederive_report_items / _ReportHasher.locate) → 보고서 요청에 스캔 폴더(scan_root)를 필수로 포함(GUI/스트림 응답의 scan_root를 그대로 전달), read root 안으로 제한, 행 경로를 scan_root 기준으로만 해석. 테스트: 루트에 동명 파일이 있는 하위 폴더 스캔 → 서명 본문이 하위 폴더 파일의 결론·해시.
P2 (High, =CACHE-1). scan_cache.py:526 `name_pattern.sub(new_name, …)`가 파일명을 정규식 치환 템플릿으로 사용 → 이름에 `\`가 있는 파일이 이름 변경/내용 동일 파일과 만나면 warm --cache 스캔이 re.PatternError로 exit 1, 출력 없음 → 치환을 함수로(lambda m: new_name); 백슬래시·`$`·`\1` 포함 파일명으로 이름 변경+중복 테스트.
P3 (Med, 회귀 =X3-R1). `--recursive --allow-symlinks`가 디렉토리 링크를 무한 추적(루트 상위 `/`로의 링크 포함; 2개 항목 폴더가 170 s 넘게 미종료; 26e3b6f는 0 s). → (device, inode) 방문 집합, 루트 상위로 향하는 링크는 순환으로 간주해 "건너뜀: 순환/상위 링크" 행, 탐색 상한(--max-files + 디렉토리 수 상한, 시간 상한) 적용; 테스트에 `/`로의 링크와 상호 링크, 타임아웃 단언.
P4 (Med, =Y2-R1). 비UTF-8 `--thresholds` 파일 → UnicodeDecodeError traceback exit 1 → "오류: 임계값 파일을 읽을 수 없습니다(인코딩): …" exit 2; 바이너리 파일 테스트. 같은 클래스: 모든 파일 읽기 진입점(--thresholds, --fusion-profile, --model-path, 캐시, 검사 JSON, 설정 파일)에서 UnicodeDecodeError/JSONDecodeError/OSError → 한국어 exit 2.
P5 (Med, =X1-R1). 거부된 아카이브 멤버(폭탄/이탈/링크/깊이 초과)가 "기록되지 않은 파일"에 미집계(legal-report가 bomb.zip에 "없음 — 모든 파일에 분석 결과가 있습니다"); 평면 스캔이 건너뛴 하위 폴더를 폴더 수로만 세고 그 안의 파일 수는 미표시 → 거부 멤버 수·사유 집계, 하위 폴더 내부 파일 수(하위 폴더별 재귀 카운트, 심볼릭 링크 제외) 표시; "없음"은 실제 0일 때만.
P6 (Med, =X2-R2). 업로드 파일명이 read root의 파일명과 같으면 루트 파일로 재분석되어 서명됨 → 업로드 행에 `source: "upload"` 표시 후 항상 서명 제외; 테스트.
P7 (Med-low, =NAME-1). 실제 경로의 `::`가 아카이브 멤버 경로와 충돌(`evil.zip::inner` 폴더 → 중복 행; `fake.zip::member.png` 보고 불가) → 행 식별자에 별도 필드(`container`, `member`)를 쓰고 표시 문자열의 `::`는 표시 전용; 실제 경로에 `::`가 있으면 이스케이프(`\:\:`)해 충돌 불가; 테스트.
P8 (Med-low, =X4-R1). api-serve 고유 엔드포인트(/api/analyze/*, /api/multimodal, /api/check)가 없는 파일/폴더에 200 "success"; /api/classify 500; /api/compare 없는 파일에 오해 소지 400; 리뷰/PUT에 JSON 배열 본문 500; 비미디어 preview가 403(문서는 400) → 전부 404/400 한국어, 오류 표 갱신, 표의 모든 행 테스트(api-serve 고유 엔드포인트 포함, 비객체 JSON 본문 포함).

## 낮음 (전부 수정)
P9. Y1/Z4 오류에 영어 예외 상세(예: JSONDecodeError 메시지) → error_text 번역표로 한국어화(위치 정보는 숫자만).
P10. 읽기 전용 출력 폴더가 스캔 후에야 실패하고 "처리 오류 N건"으로 오해 → 스캔 전 쓰기 가능성 검사(os.access + 임시 파일 생성) exit 2.
P11. `--json-out` 등이 스캔 대상 폴더 안이나 입력 JSON을 가리킬 수 있음 → 출력 경로가 입력 폴더 내부면 "오류: 출력 경로가 검사 대상 폴더 안에 있습니다" exit 2; 입력 JSON 덮어쓰기 금지.
P12. Y13 추적성 표의 해시 115개 전부 리라이트 이전 → 현재 phase0 히스토리에서 제목으로 매칭해 해시 재생성(스크립트 scripts/build_traceability_commits.py, CI --check).
P13. 영어 탐지기 우회: `ProbablyFake`, `FakeImageDetected`, `probably_fake`, `AUTHENTIC` → camelCase 분해는 이미 있으나 사전 단어 1개 + 'fake/authentic/detected' 같은 결론 단어는 단독으로도 실패; 결론 단어 목록(fake, real, authentic, synthetic, detected, generated, manipulated, deepfake, genuine, likely, probably, suspicious, clean, safe) 단독 등장 시 실패.
P14. 최근 커밋 2개가 Gaps를 제목에만 적음 → 규칙상 허용(제목에 ID). 변경 불필요이나 build_traceability_commits가 제목·본문 모두 인식.
