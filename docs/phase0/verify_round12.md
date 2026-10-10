# 독립 검증 라운드 12 결과 (HEAD 77a5463) — 전부 수정 (면제 없음)

R11-2…R11-14 종료, 회귀 없음. 남은 항목:

R12-1 (High, 차단). UTF-8이 아닌 이름의 영상(mp4/mov…)에서 `cv2.VideoCapture`가 segfault → CLI 스캔 exit 139, 출력 없음, 다른 행 유실; 웹 서버 프로세스 사망; --workers와 b600a58에서도 동일. (video_analysis.py:110,312, face_track.py:120, lipsync.py:193, model_adapter.py:963, multimodal.py:467, rppg.py:360) → 서로게이트를 포함한 경로(또는 비ASCII 경로 전반)는 OpenCV에 절대 넘기지 않는다: 세션 임시 디렉토리에 ASCII 이름 하드링크(실패 시 읽기 전용 복사, 크기 상한 내) 생성 후 분석, 끝나면 삭제; 공통 헬퍼 `native_safe_path()` 컨텍스트 매니저로 모든 cv2/ffmpeg/네이티브 호출 지점 통일; 메타테스트: 패키지 내 `cv2.VideoCapture(`/`cv2.imread(`/`subprocess` ffmpeg 호출이 전부 헬퍼를 거치는지 AST로 검사. CLI·웹·API에서 실제 non-UTF-8 이름 mp4 테스트(다른 행 정상, 프로세스 생존).
R12-2 (Med). non-UTF-8 이름의 오디오(librosa/soundfile)·PDF(pymupdf)가 검사 실패 → 같은 헬퍼 또는 파일 객체/스트림(`soundfile.read(fileobj)`, `fitz.open(stream=bytes)`)으로; 결과가 같은 내용의 ASCII 이름 사본과 동일함을 테스트.
R12-3 (Med, 차단). zip/tar 중복 멤버 경로가 추출 시 서로 덮어씀(동일 이름 2회, `x/y.png`+`x\y.png`, `./p.png`+`p.png`, 대소문자 무시 FS의 `A.png`/`a.png`) → 두 행이 마지막 멤버의 sha256·결론을 공유, A1111 멤버 근거 소실. (archives.py _extract_zip/_extract_tar, _dest_for) → 멤버마다 고유 추출 대상(멤버 인덱스 기반 내부 이름), 표시 경로는 원래 이름 유지하되 중복은 `#2` 접미로 구분하고 "중복 멤버 이름" 사유 기록; 대소문자 충돌도 구분; 행 식별자(container, member, member_index) 유일; 테스트.
R12-4 (Low). GUI 미리보기/히트맵/스튜디오가 non-UTF-8 행에서 "미리보기 실패: URI malformed"(영어); 어떤 URL 인코딩으로도 /api/preview가 파일에 도달 못함 → 행에 `path_b64`(os.fsencode 바이트의 urlsafe base64) 필드 추가, preview/heatmap API가 `path_b64` 파라미터 수용(read-root 검사 동일), GUI는 항상 path_b64 사용; 오류 문구 한국어; 두 서버 테스트 + 헤드리스 브라우저(playwright 있으면) 확인.
R12-5 (Low). 증거설명서 .md 렌더 시 서로 다른 이름이 같게 보임(실제 LF vs 리터럴 `\n`, `&amp;` vs `&`, `&lt;` vs `<`, ZWSP vs 리터럴 `\u200b`) → Markdown 텍스트에서 `\`와 `&`도 이스케이프(`\\`, `&amp;`); markdown-it(있으면) 또는 python-markdown으로 렌더한 HTML 텍스트가 단사임을 속성 테스트.
R12-6 (Low). GUI 비교 슬롯이 선택 파일명을 textContent로 원문 표시(gui.js:1515) → displayName; R11-10 메타테스트를 textContent/innerText/value 대입·템플릿 문자열까지 확장.
R12-7 (Low, 수정). 스캔 중 파일이 바뀌면 분석한 바이트와 다른 sha256이 기록될 수 있음 → 분석 전후 (size, mtime_ns, inode) 비교 + 해시는 분석에 쓴 바이트에서 계산(한 번 읽은 버퍼/임시 복사본을 해시와 분석에 공용) 또는 변경 감지 시 "분석 중 파일 변경 — 판단 불가" failed coverage; 테스트(분석 함수 monkeypatch로 중간 변경 시뮬레이션).
R12-8 (Low, 수정). Windows에서 `C:\x`/`\x` relpath가 corpus verify 포함 검사 통과 → PureWindowsPath·PurePosixPath 양쪽으로 절대/드라이브/UNC/`..` 검사; 테스트.
R12-9 (Cosmetic). legal-report generated_at/분석 일시가 시간대 없는 로컬 시각 → ISO 8601 + 오프셋(+09:00 등), 모든 보고서 타임스탬프 동일 규칙.
R12-10 (Cosmetic). non-UTF-8 multipart 파일명이 U+FFFD로 → 원시 바이트를 latin-1로 받아 가능하면 UTF-8/CP949 디코드, 불가하면 서로게이트 이스케이프로 보존하고 display_name 표기.
R12-11 (Cosmetic). api-serve가 `/gui/`를 `/gui`로 리다이렉트, 웹 서버는 404 → 두 서버 동일 동작(둘 다 404 또는 둘 다 리다이렉트), 라우트 메타테스트에 후행 슬래시 케이스.
R12-12 (Cosmetic). 영어 탐지기가 TAG 문자(U+E0000–E007F) 뒤 단어 미탐 → Cf 카테고리 제거 후 검사.
