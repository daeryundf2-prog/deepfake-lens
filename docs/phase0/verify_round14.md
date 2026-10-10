# 독립 검증 라운드 14 결과 (HEAD 232cfbd) — 전부 수정 (면제 없음)

R13-2,3,5,6,7,9 종료, 회귀 없음(성능 ±2%). 남은 항목:

R14-1 (Low-Med, 차단, 회귀). 상속된 SIG_IGN 위에도 정리 핸들러 설치 → `nohup` 스캔이 SIGHUP(터미널 닫힘)을 받거나 무시하도록 지시된 SIGINT를 받으면 살아 있는 스테이징 폴더를 지우고 스캔 계속 → 디코드 중이던 파일이 가짜 failed `audio_features`("System error"). 3/3 재현. (native_path._cleanup_then/install_cleanup_handlers) → 상속 동작이 SIG_IGN이면 핸들러 미설치; 사용자 정의 핸들러가 있으면 체인; 정리는 프로세스 종료가 확정될 때만(핸들러에서 정리 후 원래 동작 재실행, SIG_IGN이면 아무것도 안 함). 테스트: SIG_IGN 상속 자식에 SIGHUP/SIGINT → 스캔 결과가 기준 실행과 동일.
R14-2 (Low, 차단). 스테이징된 심볼릭 링크 unlink 실패 시 `os.chmod`가 링크를 따라가 증거 파일 권한 변경(0444→0600, ctime 변경). Windows에서는 하드링크에 대해 증거 파일 읽기 전용 속성 해제 가능. (_remove_tree, _unlink_staged) → 링크/하드링크에는 절대 chmod하지 않음(`os.chmod(..., follow_symlinks=False)` 지원 시만, 미지원이면 건너뜀); 하드링크는 st_nlink>1 또는 원본 inode와 같으면 권한 변경 금지; chmod는 우리가 만든 복사본(마커 파일로 식별)에만; 테스트: 제거 불가 스테이징 폴더(immutable 흉내: 부모 디렉토리 쓰기 금지)에서 증거 파일 모드·ctime 불변.
R14-3 (Low). 9085e5e가 쓴 캐시 행이 HEAD에서 그대로 재생(같은 도구 버전·같은 키 버전) → 옛 스테이징 이름 부활, ASCII 파일이 다른 파일의 임시 이름 상속. → CACHE_KEY_VERSION 올림(content-v4) + 캐시 로드 시 스테이징 이름 패턴을 가진 행은 무효화; 키에 "출력 형식 세대" 상수 추가해 출력 텍스트 규칙이 바뀌는 커밋마다 올리도록 문서화; 옛 형식 행 재생 테스트.
R14-4 (Cosmetic). 이름 복원기가 스테이징 폴더 모양이면 무엇이든 치환 → 증거 폴더 이름이 `deepfake-lens-native-7-ab`이면 `<네이티브 디코더용 임시 폴더>`로 표시(두 폴더가 같게 보임); 그런 폴더 아래 스캔 루트는 모든 실패 사유에서 `<root>/` 소실(부모 폴더 이름 변경 시 출력 변화). (restore_original_names) → 이번 프로세스가 실제로 만든 세션 폴더(등록된 절대 경로)만, 실제 임시 기반 경로 아래에서만 치환; 문자열 패턴 치환 금지; E1·E65 테스트.
R14-5 (수정). `cv2.CascadeClassifier(path)`가 native 호출 목록에 없음 → Windows 한글 설치 경로에서 Haar 검출기 무음 로드 실패 위험 → native_safe_path 경유(또는 파일 내용을 읽어 `cv2.FileStorage` 메모리 로드), 메타테스트 목록에 추가, 로드 실패는 coverage에 failed로.
R14-6 (수정). 64 MB 초과 컨테이너는 (size, mtime, inode)만 비교(ctime 없음) → 같은 크기 재작성 + `touch -r`을 놓침 → ctime_ns도 비교(가능한 OS), 그리고 추출 후 컨테이너 해시를 추출에 쓴 바이트에서 계산(스트리밍 해시를 추출과 동시에)하거나 64 MB 초과에서도 전후 해시 비교; 테스트.
R14-7 (수정). SIGKILL이 스테이징 외 임시 파일(`tmp*.wav` 등)을 남기고 청소 대상이 아님 → 모든 임시 파일을 세션 스테이징 폴더 안에 만들도록 tempfile 호출 통일(dir=세션 폴더), 그러면 기존 청소로 함께 제거; 메타테스트: 패키지의 tempfile.* 호출이 세션 폴더 dir 인자를 쓰는지 AST 검사.
R14-8 (수정). Windows 분기(`_short_path_name`, `_pid_alive` Windows 경로)가 완전히 mock되어 실행된 적 없음 → ctypes 호출을 얇은 래퍼로 분리하고, 래퍼 자체를 ctypes 시그니처·반환 처리 단위 테스트(ctypes.WinDLL을 가짜 객체로 주입해 인자 타입·버퍼 크기·오류 코드 처리 검증); 하드링크 시 Windows 속성 부작용은 R14-2 규칙(하드링크에 속성 변경 금지)으로 차단됨을 테스트.
