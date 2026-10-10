# 독립 검증 라운드 7 결과 (HEAD 26e3b6f) — 전부 수정 (면제 없음)

회귀 없음, N1–N17 종료. 남은 항목:

## 반드시 수정
X1. 분석되지 않은 파일이 조용히 누락. `evidence-statement <folder>`가 100개 파일로 하드코딩되고 비재귀(105개 폴더 → 100건 작성, exit 0, 안내 없음). HTML 보고서·포렌식 PDF·증거설명서 PDF가 파일 상한 도달·하위 폴더 건너뜀을 전혀 표시하지 않음(콘솔 표와 JSON summary만 표시). (cli.py:876, reports) → evidence-statement 폴더 입력은 scan과 같은 옵션(--max-files 기본값 동일, --recursive) 사용; 상한 도달/하위 폴더 건너뜀/심볼릭 링크 건너뜀/중복 등 "기록되지 않은 파일" 수와 사유를 모든 렌더 출력(HTML, 두 PDF, 증거설명서 MD/JSON, CSV 요약, legal-report)에 명시적 섹션으로 표시. 테스트: 105개 파일 폴더 + 하위 폴더.
X2. `/api/report`가 클라이언트가 보낸 결론·근거를 그대로 서명(target.png 행에 midjourney.png의 verdict/evidence를 붙여 POST → 서명됨, 실제 SHA-256 포함, verify-report "검증됨"). (webapp_api._report_payload) → 서버가 각 항목을 read-root 안에서 재분석(analysis_api)해 결론·근거·coverage를 서버 결과로 교체한 뒤 서명; 재분석 불가(파일 없음/루트 밖)면 해당 행은 "서명 제외(클라이언트 제공)"로 표시하고 서명 본문에서 제외하거나 보고서 전체를 미서명 처리. 테스트: 위조 행 POST → 서명 본문의 verdict가 서버 재분석 결과와 같음(위조 verdict 아님), 또는 미서명 표시.
X3. `--allow-symlinks`에서 dangling/self-loop 링크가 행 없이 사라지고 카운트에도 없음(total 5→3); 링크된 폴더가 "(심볼릭 링크 허용 안 함)"으로 표기 → dangling/loop 링크는 "건너뜀: 깨진 심볼릭 링크/순환 링크" 행, 링크된 폴더는 allow-symlinks 시 따라가되 사유 문구 정정.
X4. 두 서버 모두 `GET /api/analyze-file`(file 없음), `/api/scan-cancel`(job 없음), `/api/scan-status`(미지 job)에 200 + {"error"} → 400/404 + 한국어 오류; 모든 /api/* 엔드포인트의 오류 상태 코드를 표로 문서화하고 테스트.

## 잔여 항목 (전부 수정)
Y1. `evidence-statement x.json`에 items 없음 → 빈 증거설명서 exit 0 → "오류: 검사 JSON에 items가 없습니다" exit 2.
Y2. `--thresholds`가 읽을 수 없는 파일 → 경고 후 기본값 exit 0 → 오류 exit 2(없는 파일과 동일).
Y3. `scan ""` → 현재 디렉토리 스캔 → 오류 exit 2.
Y4. `--cache`가 기존 JSON 파일을 조용히 덮어씀 → 캐시 파일 형식 검증(헤더 "deepfake-lens-cache-v1"), 아니면 오류.
Y5. OpenCV 네이티브 stderr 영어(`grfmt_png …`)가 --deep-signals·face/inpaint/ml-classify/faceswap-seam에서 출력 → G14 리다이렉트를 cv2 호출 전체에 적용.
Y6. `ml-classify`가 손상 이미지에 exit 1 + stdout JSON 오류 → 한국어 stderr 오류 exit 2.
Y7. `--html-out`이 폴더를 가리키면 일반 오류 + 오해 소지 "처리 오류 1건" → "오류: 출력 경로가 폴더입니다" exit 2, 스캔 전에 검사.
Y8. 웹 스캔이 max_files=-3 허용 → 400.
Y9. 중첩 멤버 이름 `x.zip.unpacked/…`가 미문서화, 거부 사유의 `a.zip::b.zip` 체인과 불일치 → 전부 `a.zip::b.zip::c.png` 체인으로 통일; JSON 계약 문서화.
Y10. HTML 보고서에 행별 해시 없음 → 행별 SHA-256 열 추가.
Y11. 영어 탐지기 우회(`proBABLY_fAKE`, 전각 라틴, 공백 없는 접합, 키릴 동형문자) → 대소문자 정규화 후 snake 분해, NFKC 정규화, 동형문자 매핑, 공백 없는 접합은 사전 단어 최장일치 분할(≥2 단어면 실패).
Y12. test_cli_inputs 34.7 s > 20 s → 서브프로세스 대신 in-process 호출 또는 병렬화로 20 s 미만.
Y13. 추적성: 비머지 커밋 101개 중 63개가 검증 ID만 인용하고 G1–G34 갭 ID 없음; 라운드 5 커밋의 "G1–G16"이 스펙 G1–G34와 충돌 → docs/TRACEABILITY-COMMITS.md에 검증 ID(D/R/N/B/S/W/X/Y, 라운드5 G) → 스펙 갭 ID(G1–G34) 또는 "신규(스펙 외)" 매핑표 작성(각 라운드 문서 기준, 라운드 5의 G는 V5-G로 표기). 세션 소유자가 이 표로 커밋 메시지에 "Gaps: …" 줄을 일괄 추가하고 라운드 5 라벨을 V5-G로 리워드.
