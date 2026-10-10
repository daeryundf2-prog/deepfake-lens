# 독립 검증 라운드 16 결과 (HEAD 2da7a31) — 전부 수정 (면제 없음)

R15-1,2,4,5,6 종료, 회귀 없음, 플레이크 0(8회), 비루프백 connect 0, stress_shutdown 150/150. 결론에 영향 주는 결함 없음(전부 fail-closed). 남은 항목:

R16-1 (Low, 차단). faceswap_seam·face_track이 관대한 `_detect_faces`를 써서 검출기 오류(로드 불가·핀 거부 cascade, MediaPipe 크래시, cv2.error)를 skipped "얼굴 미검출"/"얼굴이 검출된 프레임이 0개"로 기록. (faceswap_seam.py:116, face_track.py:189, core.py:1303) → 두 검사도 strict 검출 사용, 검출기 오류는 failed + 원인; 테스트(핀 불일치·깨진 매니페스트·cv2.error 주입).
R16-2 (Low, 차단). `DEEPFAKE_LENS_HAAR_CASCADE`가 다른 바이트를 가리키면 조용히 무시되고 번들 cascade 로드(문서는 failed라고 함). (face.py:349-361) → 재정의가 지정되면 그것만 사용, 핀 불일치면 face/rPPG/lipsync/seam/track 검사 failed "미고정 모델: 재정의 cascade sha256 불일치"; 테스트.
R16-3 (Low, 차단). FaceLandmarker 검증 바이트를 파일 stat(path,size,mtime,inode)로만 캐시, SyncNet 파이프라인은 한 번만 생성 → 장기 실행 서버에서 pin-asset 재핀/핀 제거 후에도 옛 바이트로 "ran". (face.py:758-770, lipsync.py) → 캐시 키에 기대 sha256(매니페스트의 현재 핀)과 매니페스트 파일 상태 포함, 매 사용 시 매니페스트 재로드(mtime 기반) 후 핀이 바뀌면 캐시 무효화; 테스트(같은 프로세스에서 재핀·핀 제거).
R16-4 (Low, 차단). `import_mediapipe`가 matplotlib을 끌어와 첫 실행 시 추적되지 않는 `fc-list` 자식 2개 + `~/.cache/matplotlib/fontlist-*.json`; 홈/XDG 캐시 쓰기 불가면 세션 폴더 밖 `$TMPDIR/matplotlib-*` 생성, SIGTERM 시 잔류, 청소 대상 아님; f13fda6 메시지("no child process")와 불일치. (mediapipe_import.py) → matplotlib import 차단(필요 없는 경우) 또는 `MPLCONFIGDIR`을 세션 폴더로 설정 + fontconfig 캐시 미생성 설정; mediapipe import 중 자식 프로세스 0·세션 폴더 밖 파일 0을 테스트(자식 이름 무관하게 전체 프로세스 트리로).
R16-5 (Low, 차단). 골든 픽스처가 표준 라이브러리 전용이라 의존성 기반 출력 변경을 못 잡음(NON_PHOTO_NOTICE 수정 통과); 반대로 골든 변화 없는 세대 증가를 거부("출력은 그대로인데 세대가 3 → 4") → 정당한 증가 차단. (tests/golden_output.py:128-160, scripts/update_golden_output.py) → 골든 픽스처를 의존성 설치 여부별 두 세트(stdlib, full-extras)로 저장하고 현재 환경의 세트를 비교; 세대 증가는 항상 허용(사유 문자열을 golden 파일에 기록 필수); 골든 해시 변경 시 세대 동일이면 실패는 유지; 사용자용 문구 상수(NON_PHOTO_NOTICE 등 result_text·core의 한국어 상수 전부)의 해시도 골든에 포함.
R16-6 (Cosmetic). ffmpeg가 `-nostdin` 없이 운영자 터미널 상속 → 스캔 SIGKILL 후 터미널 에코 꺼진 채 남음 → `-nostdin` + stdin=DEVNULL.
R16-7 (Low). 직계 자식만 추적(프로세스 그룹 없음) → 래퍼 스크립트 ffmpeg(sh→ffmpeg)의 손자가 SIGKILL 후 고아 → 자식을 새 프로세스 그룹으로 시작(`start_new_session=True`), 종료 시 그룹 전체에 신호(killpg), Linux에서 PDEATHSIG는 그룹 리더에 + 서브리퍼(PR_SET_CHILD_SUBREAPER)로 손자 회수; 테스트(래퍼 스크립트 가짜 ffmpeg).
R16-8 (Cosmetic). `pin-asset --models-dir <없음>`이 영어 errno·내부 tmp 파일명 출력 → 한국어, 내부 이름 미노출.
R16-9 (Cosmetic). doctor가 자산 핀 상태(asset_status)를 표시하지 않음 → doctor 표·JSON에 자산별 핀 상태 열 추가.
R16-10 (Cosmetic). HTML이 DEEPFAKE_LENS_TMPDIR가 원인이어도 "시스템 임시 폴더를 쓸 수 없어"; 증거설명서에 임시 폴더 안내 없음 → 원인별 문구, 증거설명서에도 기록.
R16-11 (Cosmetic). test_telemetry가 ptrace 거부 환경에서 skip 대신 fail → ptrace 가능 여부 사전 확인 후 skip(사유 문서화).
R16-12 (Cosmetic). QA-ENV-DEPENDENT-TESTS.md의 "git 작업 트리 아님" 행이 test_qa_sys.py만 나열; 얕은 클론에서 2개 실패 → 문서 갱신, 두 테스트 모두 같은 규칙(얕은 클론은 fail, 문서화).
R16-13 (Cosmetic, 기존). Ctrl-C 시 영어 KeyboardInterrupt traceback → 최상위에서 KeyboardInterrupt를 "중단됨(사용자 요청)" 한국어 + exit 130, 정리 후.
R16-14 (Low, 기존). `vendor-weights --verify --models-dir <없음>`이 "통과" rc 0 → 오류 exit 2.
R16-15 (Cosmetic). with_default_signals.py가 프로그램 없음 시 영어 traceback → 한국어 오류 exit 127.
