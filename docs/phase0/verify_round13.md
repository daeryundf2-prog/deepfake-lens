# 독립 검증 라운드 13 결과 (HEAD 9085e5e) — 전부 수정 (면제 없음)

R12-3…R12-6, R12-8…R12-12 종료. 성능 저하 없음(+1~5%). 남은 항목:

R13-1 (Med, 차단, 회귀). R12-1/2 스테이징 임시 이름(예: '000035-df0fda9674ca.m4a')이 soundfile이 비ASCII 이름 오디오를 열지 못할 때 실패 사유에 누출 — 한국어 UTF-8 이름("녹음 1.m4a")도 해당(디코드 불가 m4a, 손상 wav/flac). coverage reason·limitations·CSV·HTML·보고서에 무작위 이름 → (a) 실행마다 JSON 다름(QA-IN-2 위반), (b) ASCII 사본과 행이 다름(R12-2 위반), (c) 내용 키 캐시로 ASCII `tone.m4a`가 다른 파일의 임시 이름을 물려받음. (audio.py _extract_features → failure_reason, error_text.scrub_paths, native_path.py) → native_safe_path가 (staged→original) 매핑을 등록하고 모든 실패 사유·예외 메시지 생성 경로(failure_reason, scrub_paths, run_check)가 staged 경로/이름을 원래 `<root>/…` 표기로 되돌림; 스테이징 디렉토리 이름 패턴이 어떤 출력에도 나타나지 않음을 단언하는 전역 테스트(스캔 JSON·CSV·HTML·PDF·증거설명서 전체에서 staging 접두 grep); 한국어·non-UTF-8 이름의 디코드 불가/미지원/손상 오디오·영상·PDF·이미지 e2e: ASCII 사본과 행 동일, 실행 간 동일, warm cache 동일.
R13-2 (Low, 차단). 압축 파일이 멤버 추출 중 재작성되면 컨테이너 sha256이 추출된 바이트와 다름(컨테이너 sha ZB, 멤버는 ZA에서) → 컨테이너도 추출 전후 상태(size, mtime_ns, ino)·해시 비교, 불일치 시 컨테이너와 그 멤버 행 전부 판단 불가 + 해시 없음 + "분석 중 파일 변경" 사유; 테스트(추출 함수 래핑으로 중간 재작성).
R13-3 (Low). /api/analyze-file?file=, /api/scan?folder=가 non-UTF-8 경로를 지정 불가 → `file_b64`, `folder_b64` 파라미터(read-root 검사 동일), 오류 표·문서·테스트.
R13-4 (Cosmetic). SIGTERM/강제 종료 시 프로세스별 스테이징 폴더가 TMPDIR에 남음 → SIGTERM/SIGINT 핸들러에서 정리 + 시작 시 오래된(소유 PID가 죽은) 스테이징 폴더 청소; 테스트.
R13-5 (Cosmetic). `corpus verify`가 non-UTF-8 relpath를 `r?.jpg`로 출력(비단사) → display_name 사용.
R13-6 (Cosmetic). 중첩 중복 멤버 행(`inner.zip#2::x.png`)에 "중복 멤버 이름" 사유가 행 자체에 없음 → 해당 멤버 행 coverage에도 사유 기록.
R13-7 (Windows 위험, 수정). 얼굴 크롭·영상 프레임을 `cv2.imwrite`로 임시 폴더에 쓰는데, 한국어 Windows 사용자 이름(임시 경로에 한글) 아래에서 실패 → 모든 cv2.imwrite/cv2.imread/VideoWriter 경로도 native_safe_path 또는 `cv2.imencode` + Python 쓰기 / `np.fromfile` + `cv2.imdecode`로; 임시 루트 자체가 비ASCII일 때(TMPDIR=한글 폴더) 테스트. 메타테스트가 `import cv2 as cv` 별칭과 하위 패키지 파일도 검사하도록 확장.
R13-8 (Windows 성능 위험, 수정). Windows(심볼릭 링크 없음, 증거가 다른 드라이브)에서 모든 한글 이름 파일이 최대 2 GB까지 전부 복사됨 → 스테이징 순서: (1) 하드링크(같은 볼륨), (2) Windows 8.3 짧은 이름(GetShortPathNameW, 있으면 복사 불필요), (3) 복사; 복사가 필요한 크기가 상한 초과면 "판단 불가: 네이티브 디코더용 임시 사본 상한 초과" 기록; 순서를 단위 테스트(Windows API는 mock).
R13-9 (Low). 렌더된 Markdown에서 GFM 자동 링크(`www.`, `http://`, `https://`, 이메일)가 이름에서 링크화될 수 있음 → markdown_cell에서 `:`/`.`/`@` 주변을 이스케이프하거나 `<`/`>` 대신 zero-width 없이 `\` 이스케이프로 자동링크 무력화; markdown-it linkify 플러그인(설치 가능하면)으로 테스트.
