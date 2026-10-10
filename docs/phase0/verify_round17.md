# 독립 검증 라운드 17 결과 (HEAD ad02d2b) — 미수정 상태로 인계 (전부 수정 대상)

R16-1(영상 프레임 멤버 사유 제외)·R16-2~6·R16-8~12·R16-14·R16-15 종료. 회귀 없음(성능 ±2%, 결정성·서명 정상). 남은 항목:

R17-1 (Med, 차단). 감독(supervisor) 프로세스 경쟁: 워커 스레드가 만든 자식은 부모 SIGKILL 시 PR_SET_PDEATHSIG를 두 번 받음(생성 스레드 종료 + 프로세스 종료, ~1 ms 간격, 7/40). 두 번째 SIGTERM이 `except Stop` 이후·SIG_IGN 설치 전에 오면 감독자가 잡히지 않은 Stop으로 죽고 프로그램의 자식이 init에 입양되어 계속 실행(12분+). 게이트에서 플레이크로 관측(test_shutdown.WrapperGrandchildTest.test_sigkill_of_the_parent_ends_the_grandchild). (shutdown.py CHILD_SUPERVISOR) → on_stop이 raise 전에 SIG_IGN 설치 또는 정지 신호 블록(sigprocmask); 두 번 신호 결정적 테스트.
R17-2 (Low, 회귀 vs 2da7a31). SIGKILL(OOM 등)로 죽은 프로그램 처리 중 감독자가 `signal.signal(-code, SIG_DFL)`에서 OSError EINVAL로 크래시 → run_child가 1 + 영어 traceback 반환; `video --extract`가 returncode 1 + 영어 traceback 기록(이전 -9). → SIGKILL/SIGSTOP은 signal() 호출 없이 동일 신호로 자신을 kill하거나 128+signum/음수 반환 매핑; 테스트.
R17-3 (Low). setsid 탈출: 두 단계 아래 다른 세션의 자손(wrapper → `setsid sh -c 'sleep & wait'`)이 정상 종료·SIGTERM·SIGKILL 후에도 생존, 정상 종료 시 캡처 파이프를 잡아 스캔이 4분+ 멈춤; 타임아웃 후 `proc.communicate()`가 무제한 대기 가능. → 입양된 자손이 없을 때까지 신호·회수 반복(서브리퍼), kill 후 communicate에 제한 시간.
R17-4 (Low). 실행 불가 ffmpeg(exec 실패 127, 공유 라이브러리 누락, SIGKILL)가 오디오 있는 영상에 av_audio skipped "오디오 트랙 없음", lipsync skipped "오디오 트랙이 없거나 추출에 실패"로 기록. (video_analysis.py:240-247, lipsync._audio_envelope) → "스트림 없음"이 아닌 ffmpeg 오류는 failed + 원인(예외 클래스/종료 코드).
R17-5 (Low). mediapipe ≥0.10.30(solutions 없음) + face_landmarker.task 없음: Haar가 매 프레임 얼굴을 찾는데 face_track이 skipped "얼굴이 검출된 프레임이 0개"; `face_detector_unavailable_reason(require_landmarks=True)`가 None; doctor는 mediapipe OK "FaceMesh 얼굴 검출(대체 경로)"; DEEPFAKE_LENS_FACE_LANDMARKER가 없는 파일이면 조용히 무시. (face.py:429-448, face_track.py:192-205, doctor.py:66) → 실측 랜드마크 경로 불가 시 "의존성 부재: 실측 랜드마크 검출기 없음", doctor 행 정정, 없는 재정의 파일은 failed.
R17-6 (Low). video-frames 멤버 검출기 오류의 coverage 사유가 일반 문구("프레임 8개를 디코딩했지만 내부 런타임이 점수를 내지 못했습니다"), 원인은 models[].detail에만. (model_adapter.py:926-936) → 내부 원인·예외 클래스를 coverage 사유로.
R17-7 (Low). 스캔 캐시 키가 핀만 포함, 실제 사용 자산 파일·재정의는 미포함 → 잘못된 HAAR 재정의로 캐시된 "failed … 불일치"가 재정의 수정 후에도 재생(반대도). (scan_cache.py:784-797, model_assets.asset_pin_tokens) → 키에 실효 자산 경로·재정의 환경 변수·해당 파일 sha256 포함, 또는 환경 원인 실패는 캐시하지 않음.
R17-8 (Cosmetic). R16-13 미완: (a) web 요청이 영상 분석 중 Ctrl-C → 영어 "analysis failed: …" + ShuttingDown traceback이 한국어 줄보다 먼저(정리 전에 로깅 복원) (b) `deepfake_lens/__init__` import 중(처음 ~0.35초) Ctrl-C → 영어 traceback·SIGINT 사망(콘솔 스크립트 동일) (c) `batch`가 한국어 줄 출력 후 ~36초 계속 실행. → 로깅 복원은 정리 후, import 구간 가드(엔트리포인트에서 import 전에 SIGINT 핸들러 설치), batch 즉시 중단.
R17-9 (Cosmetic). doctor와 로더 불일치: `DEEPFAKE_LENS_HAAR_CASCADE=~/x.xml`을 doctor는 ~ 확장해 ok, 스캔은 "재정의 cascade 파일이 없습니다"; 깨진/BOM assets.json이면 doctor 자산 섹션은 비고 모든 얼굴 검사는 "미고정 모델" → 두 경로가 같은 해석 함수 사용, doctor가 매니페스트 오류 표시.
R17-10 (Cosmetic). `vendor-weights --verify --models-dir <비어 있거나 잘못된 기존 폴더>`가 "통과 … 프로필 0개" rc 0 → 프로필 0개면 오류 exit 2.
R17-11 (Cosmetic). 0ce6d1b가 Ctrl-C 처리에 G13(사진 게이트) 인용 — 추적표 매핑 정정(G34/신규); DEEPFAKE_LENS_TMPDIR가 파일을 가리킬 때 "(폴더가 없음)" → "(폴더가 아니라 파일임)".

잔여 1단계 리스크: full-extras 골든은 CI에서 비교되지 않음(CI가 opencv-python-headless 설치) → CI 환경용 골든 세트 추가 검토; SyncNet은 mock으로만 검증; PDEATHSIG는 다른 커널·macOS·Windows에서 미검증; 캐시 행이 키 밖 환경 상태에 의존(R17-7로 일부 해소).
