# 알려진 과거 이력 문제 (KNOWN-HISTORICAL-ISSUES)

R15-4 (검증 라운드 15). 브랜치 이력은 다시 쓰지 않는다. 이력 속 커밋이 **특정 실행 환경에서**
자기 테스트를 통과하지 못했던 경우를 여기에 기록한다 — 그 커밋을 꺼내 다시 검증하는 사람이
같은 실패를 결함 회귀로 오인하지 않도록, 조건과 해결 커밋을 함께 적는다. 테스트 삭제·이름
변경은 이 문서가 아니라 `docs/TEST-DELETIONS.md`에 둔다(QA-SYS-10).

커밋은 `docs/TEST-DELETIONS.md`와 같이 **제목(subject)** 으로 인용하고 해시는 참고로만 적는다.

## H-1. SIGINT가 무시(SIG_IGN)된 채 상속된 환경에서 SIGINT 정리 테스트 실패

| 항목 | 내용 |
| --- | --- |
| 영향 커밋(해시 참고) | `fix(native): R14-1 an inherited SIG_IGN stays ignored — a nohup scan survives SIGHUP/SIGINT with its staging folder; only a default action gets the cleanup handler (R14-1; Gaps: G34, G1, 신규)` (`b914bce`) |
| | `fix(native): R14-2 removing a staged name never changes the evidence file — no chmod through a link, no hard links; R14-8 the Windows ctypes calls run against a fake WinDLL (R14-2, R14-8; Gaps: G30, G1, 신규)` (`0a9f42b`) |
| | `fix(cache): R14-3 a cache row written before R13-1 is never replayed — key content-v4 with an output-format generation, rows carrying staging text dropped on load; R14-4 only this process's own staging folders and names are restored (R14-3, R14-4; Gaps: G11, G32, 신규)` (`fd4bc0a`) |
| | `fix(native): R14-5 the Haar cascade is opened through native_safe_path — a cascade that does not load is a failed face / rPPG / lip-sync check, never "얼굴 미검출" (R14-5; Gaps: G12, G1, 신규)` (`7fefde4`) |
| 실패 테스트 | `SessionFolderCleanupTest.test_sigint_removes_the_folder_and_still_raises_keyboard_interrupt` (`deepfake_lens/tests/test_native_path.py`) — ERROR(자식 프로세스 대기 시간 초과) |
| 조건 | 테스트를 실행한 프로세스가 SIGINT를 **SIG_IGN으로 상속**한 경우: 작업 제어가 없는 셸의 백그라운드 작업(`cmd &`), `nohup`/일부 CI 러너·서브셸 등. 대화형 터미널(SIGINT 기본 동작)에서는 통과한다. |
| 원인 | R14-1부터 무시된 신호는 무시된 채로 둔다(정리 처리기를 설치하지 않음 — `nohup` 스캔 보호). 테스트의 자식 프로세스는 실행자의 SIGINT 처분(SIG_IGN)을 그대로 물려받아 신호를 무시하고 잠든 채 시간 초과가 났다. 제품 동작의 결함이 아니라 테스트가 자식의 신호 처분을 고정하지 않은 것이 원인이다. |
| 재현 | 검증 라운드 15: 각 커밋에서 `python -c "import signal,os,sys; signal.signal(signal.SIGINT, signal.SIG_IGN); os.execv(sys.executable, [sys.executable,'-m','unittest','deepfake_lens.tests.test_native_path.SessionFolderCleanupTest'])"` → `FAILED (errors=1)`; 같은 명령에서 `SIG_DFL` → `OK`. |
| 해결 커밋(해시 참고) | `test(native): R14-1 follow-up — the SIGINT cleanup test's child starts with SIGINT/SIGTERM at their default (R14-1; Gaps: G34, 신규)` (`cf1fe06`) — 자식이 SIGINT/SIGTERM을 기본값으로 리셋한 뒤 실행. |
| 재발 방지 | R15-4: 테스트 하네스의 자식 프로세스는 항상 SIGINT/SIGTERM/SIGHUP 기본 동작으로 시작한다 — 테스트 패키지(`deepfake_lens/tests/__init__.py`)와 `scripts/qa_phase0.py`가 `deepfake_lens.shutdown.children_start_with_default_signals()`로 물려받은 SIG_IGN을 "아무것도 하지 않는 처리기"로 바꿔(자신은 계속 무시, exec된 자식은 기본 동작) 두고, CI의 단위 테스트·QA 단계는 `scripts/with_default_signals.py -- …`로 실행하며, 신호를 보내는 테스트(`test_native_path.py`, `test_shutdown.py`)와 `scripts/stress_shutdown.py`는 자식을 `shutdown.with_signals`/`with_default_signals`(exec 트램펄린)로 시작한다. R15-5: 이 설정은 `preexec_fn`(스레드가 있는 프로세스에서 fork 안전하지 않음) 대신 exec 트램펄린으로 한다(ruff PLW1509). |

이 커밋들을 다시 검증할 때는 SIGINT 기본 동작으로 실행하거나(`scripts/with_default_signals.py --
python -m unittest …`), 위 실패를 이 항목으로 판정한다.
