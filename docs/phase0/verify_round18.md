# 독립 검증 라운드 18 결과 (HEAD ec0d057)

R17-1~R17-12는 새로 만든 탐침으로 전부 닫혔음을 확인했다. R17-8(b)만 import 초반에 아주 짧은 구간이 남아 있고, 이는 R18-12로 적었다.
- R17-1·2·3: 부모 SIGKILL 시점을 0~0.6 s로 바꿔 가며 360회(일반, setsid 1·3단계, TERM 무시 자손) 돌렸고, 남은 자손은 0이었다. 신호 6종으로 죽은 프로그램은 각각 -9/-11/-13/-15/-2/-10으로 보고된다. setsid로 빠져나간 자손이 있어도 정상 종료·타임아웃은 0.5~1.0 s 안에 끝난다.
- R17-4: 실제 오디오가 있는 mp4에 고장 ffmpeg 5종(exec 실패, 127, SIGKILL, SIGSEGV, exit 1)을 두 venv에서 돌렸다. 전부 failed와 정확한 원인이 기록된다.
- R17-5: tasks 전용 mediapipe 1.1.0에서 doctor는 WARN, face_track은 "의존성 부재: 실측 랜드마크 검출기 없음"으로 기록된다.
- R17-6: 기록되는 원인에 임시 경로가 나오지 않고(스크럽됨), 실행마다 같다.
- R17-7: Haar 재정의를 잘못→수정→해제→잘못→같은 경로에서 바이트 수정→~로 바꾸는 순서로 캐시를 돌렸다. 매번 캐시가 무효화되었다.
- R17-8: batch에 Ctrl-C를 넣으면 0.04 s 만에 종료된다(ed30923은 17 s). web 영상 검사 중 Ctrl-C에는 영어 줄이 0이다.
- R17-9·10·11은 재현 결과가 수정 내용과 맞다.

회귀 탐색 결과는 다음과 같다.
- 성능: 2000파일 스캔의 함수 호출 수가 52,218,615 → 52,219,129(+0.001%)다. 실행 시간은 호스트 부하(load 7~8) 때문에 ±40%로 흔들려 측정값으로 쓸 수 없다. 처리량 저하는 없다고 판단한다(25% 기준 미만).
- 결정성: 3회 실행, 워커 4, 폴더 이름 변경, cold/warm 캐시, 4개 동시 스캔 모두 바이트 단위로 같다(`summary.cached`만 다름).
- 서명: 문자 1개 변경, 필드 추가, 키 ID 제거는 모두 정상적으로 잡힌다. 다만 중복 키 삽입은 통과한다(R18-5).
- 악성 폴더 95개 항목·106행: ed30923과 비교해 결론 변화가 없다. 차이는 의도된 R17-4·R17-5 사유뿐이다.
- QA-IN-1·QA-OUT-4(세 경로)도 다시 통과했다.
- 새 회귀 2건: R18-1(캐시 키에 설치 경로가 들어감), R18-2(`~user` 재정의 시 스캔 전체 중단).

R18-1 (Low, 차단 — 회귀, WP-G "경로 미사용" 위반). R17-7의 `asset_file_tokens()`가 캐시 키에 절대 경로를 넣는다. 들어가는 경로는 OpenCV 데이터 폴더, 패키지 설치 폴더, models 폴더다. 그래서 같은 버전·같은 자산이라도 venv나 설치 위치가 다르면 키가 달라진다.
- 재현(같은 HEAD 코드): /work → `574323ed…`, 사본 /w2 → `73e3b893…`, /venv-api → `40c8730e…`.
- ed30923은 /base, /base2, 두 venv 모두 `a2f37028…`로 같다.
- 위치: model_assets.py:248-284(`_loader_files`, `_file_token`의 `f"{path}:{digest}"`).
- → 토큰은 자산 이름과 역할(opencv/bundled/models/override)과 sha256으로만 만든다. 절대 경로는 넣지 않는다. 설치 위치를 옮긴 테스트를 추가한다.

R18-2 (Low, 차단 — 회귀). `DEEPFAKE_LENS_HAAR_CASCADE='~nouser/h.xml'`처럼 존재하지 않는 사용자 이름을 쓰면 `Path.expanduser`가 RuntimeError를 낸다.
- `scan --cache`는 rc 1로 끝나고 출력이 없다. 스캔 전체가 중단되며, 얼굴 검사를 하지 않는 스캔도 마찬가지다. ed30923은 rc 0이었다.
- 캐시 없이 돌리면 얼굴 검사가 "RuntimeError: 라이브러리 오류(RuntimeError)"로 기록된다. 이전에는 "재정의 cascade 파일이 없습니다"였다.
- doctor도 rc 1 "예기치 않은 오류"로 끝난다. FACE_LANDMARKER 변수도 같다.
- 위치: model_assets.py:331 `asset_override`, scan_cache.py:801.
- → expanduser가 실패하면 재정의 값을 그대로 경로로 쓰거나, 한국어로 "재정의 경로를 해석할 수 없음"을 낸다. 캐시 키 계산은 예외를 삼켜 토큰으로 만든다. 테스트를 추가한다.

R18-3 (Low-Med, 차단). GUI·웹 스캔을 취소하면 남은 파일이 조용히 빠진다(조정자 지적 1, 재현함).
- 재현: `_scan_payload(…, should_stop=5회 후 True)`를 300파일 폴더에 돌렸다. 결과는 summary.total 3, unrecorded_files.total_files 1이다. 나머지 297개는 행도 미기록 사유도 없다.
- 작업 상태는 "done"으로 남고 cancelled 표시가 없다(webapp_api.py:279). GUI는 "분석 완료 — 3개 파일"을 띄우고, 내보낸 보고서는 완결된 폴더 보고서처럼 보인다.
- 원인: core.py:858 `if should_stop(): break`.
- 위반 근거: X1(라운드 7)의 "분석되지 않은 파일 누락 금지/기록되지 않은 파일 집계" 규칙과 "어떤 검사를 왜 못 했는지 기록" 목표.
- → 취소 뒤 남은 파일은 unrecorded_files에 "취소됨" 사유와 개수로 넣는다. summary에 `cancelled: true`, 작업 상태는 "cancelled"로 둔다. GUI·보고서에 "취소된 부분 결과"를 표시한다. 테스트를 추가한다.

R18-4 (Low). 메타데이터를 앞 4 MB(`--metadata-bytes`)까지만 읽는데, 이를 표시하지 않고 coverage에 `metadata: ran`으로 기록한다.
- 재현: 6.7 MB PNG의 IDAT 뒤에 A1111 `parameters` tEXt를 넣었다. 생성 도구 근거가 누락되고 결론은 판단 불가인데, metadata는 ran이다.
- `--metadata-bytes 10`을 줘도 ran이다.
- 위치: image_metadata.py:115 — 잘린 앞부분을 "정상"으로 간주한다.
- → PNG는 청크 헤더로 IDAT를 건너뛰며 끝까지 텍스트 청크를 찾는다. 그게 어렵다면 앞부분이 잘렸을 때 limitation과 coverage 사유를 남긴다("메타데이터 앞 N MB만 확인"). 테스트를 추가한다.

R18-5 (Low). 서명된 보고서에 중복 키를 넣어도 검증을 통과한다.
- 재현: 첫 항목(a1111.png, manipulation) 앞에 `"verdict_code": "authenticity_evidence",`를 삽입했다. 결과는 "검증됨" rc 0이다.
- JSON은 마지막 값을 쓰지만, 사람이나 "먼저 나온 키"를 쓰는 파서는 위조된 결론을 보게 된다.
- 위치: signing.py:177/203, verify-report 로더.
- → `object_pairs_hook`으로 중복 키를 감지해 "변조됨"(또는 해석 불가 4)으로 처리한다. QA-SYS-6 테스트에 이 사례를 추가한다.

R18-6 (Low). 2억 화소 사진(갤럭시 S23/S24 Ultra 200MP, 16320×12240)에서 EXIF를 읽지 못한다.
- 결과: `metadata` failed "EXIF 판독 실패: DecompressionBombError: 라이브러리 오류(DecompressionBombError)", `image_class`도 failed다.
- 원인: Pillow의 픽셀 수 상한(약 1.79억)이 헤더만 읽는 단계에도 걸린다. 카메라 EXIF 근거가 사라지고, 사유는 번역되지 않은 상태다.
- 재현: SOF 크기를 패치한 JPEG(Make=samsung).
- → EXIF·크기 읽기는 픽셀 디코드와 분리해 폭탄 검사 없이 수행한다. 상한은 실제 디코드에만 적용하고, 사유는 한국어("픽셀 수 상한 초과 — 디코드 생략")로 쓴다.

R18-7 (Low). rPPG가 열 수 없는 영상(0바이트, 손상 mp4, moov 없음, 잘린 mp4)을 skipped "얼굴 영역을 충분히 추적하지 못했습니다 (획득 프레임 0, 최소 30)"로 기록한다. 같은 영상에서 face_track은 "영상을 디코딩할 수 없습니다" failed다. R16-1과 같은 종류의 결함이다.
- 위치: rppg.py:364 `if not capture.isOpened(): return [], [], 0.0, 0.0`.
- → 열기 실패나 읽기 실패는 예외로 올려 failed와 원인을 기록한다. 테스트를 추가한다.

R18-8 (Low). GUI 검토 표시(★, 메모, 검토자 판정)가 상대 경로로 키가 잡히고 전역 저장소에 저장된다(조정자 지적 4, 코드로 확인).
- 키: `itemKey = item.path||item.name`(gui.js:28). 저장소: `~/.deepfake-lens/reviews.json`(reviews.py:17)과 localStorage.
- 결과: 다른 사건의 같은 이름 파일(IMG_0001.jpg 등)에 앞 사건의 메모와 "검토됨" 표시가 붙는다. 이 값은 GUI JSON·CSV 내보내기에 그대로 들어간다(gui.js:1306-1320).
- G11("내용 해시 기반, 경로 미사용")의 취지에 어긋난다.
- → 키를 sha256과 스캔 루트(또는 사건) 식별자로 바꾸고 기존 키는 이전한다.

R18-9 (Low). 감독 프로세스가 프로그램의 시그널 종료를 그대로 재현하면서 Python 코어 덤프를 남긴다.
- 재현: `ulimit -c unlimited` + core_pattern=core 환경에서, 코어를 끈 프로그램(`ulimit -c 0; kill -SEGV $$`)을 run_child로 실행했다. 작업 폴더(cwd)에 `core`가 생긴다.
- cwd가 증거 폴더라면 폴더 안에 새 파일이 생기므로 QA-IN-1 취지에 어긋난다.
- 위치: shutdown.py:136-141 `die()`.
- → die 전에 `resource.setrlimit(RLIMIT_CORE,(0,0))` 또는 `prctl(PR_SET_DUMPABLE,0)`을 호출한다. 자식의 cwd는 세션 폴더로 둔다.

R18-10 (Low). `--allow-symlinks`가 `/proc/self/environ` 같은 procfs 링크를 따라가 스캐너 자신의 환경을 분석한다.
- 이 환경에는 DEEPFAKE_LENS_REPORT_KEY가 들어 있을 수 있고, 그 해시가 기록된다.
- 행에는 `size_bytes 0`인데 sha256은 내용이 있는 값이 찍혀 서로 맞지 않는다.
- → 링크 대상이 procfs·sysfs이거나 stat 크기 0인데 내용이 있으면 "건너뜀: 특수 파일 시스템"으로 처리한다.

R18-11 (Cosmetic). 표시 이름이 단사가 아니다(R11-4·R12-5 취지).
- NFC `한.jpg`와 NFD `한.jpg`가 같은 표시 이름이 된다.
- NBSP(U+00A0), U+3000, U+3164(한글 채움 문자), U+115F, U+FE0F가 이스케이프되지 않아 `ab.jpg`, `a b.jpg` 같은 이름과 눈으로 구분되지 않는다.
- → display_name이 NFC가 아닌 시퀀스, U+0020이 아닌 Zs, Default_Ignorable 코드포인트를 `\uXXXX`로 표기한다.

R18-12 (Cosmetic). R17-8(b) 잔여: import 가드 설치 전 구간에서 Ctrl-C를 받으면 영어 traceback이 나온다.
- 측정: 0.01 s 간격으로 80회씩 돌렸을 때 module 형태 1회, 콘솔 스크립트 형태 3회 발생했다. 부하가 있으면 0.2 s 시점에도 발생한다.
- 그중 한 건은 interrupt_guard.py:22 `import threading` 안에서 일어났다. 가드가 텔레메트리 import와 threading import 뒤에 설치되기 때문이다(__init__.py:6-13).
- ee2818f 메시지("a Ctrl-C during the package import is a Korean line")는 약간 과장이다.
- → 가드를 `__init__` 맨 처음에 설치하고 threading import 없이(ValueError로 판정) 구현한다. 메시지와 문서에 "Python 시작 구간은 제외"라고 적는다.

R18-13 (Cosmetic). doctor와 실제 검사 결과가 어긋난다(R17-5·R17-9와 같은 종류).
- (a) `DEEPFAKE_LENS_HAAR_CASCADE`가 없는 파일이나 폴더를 가리키면 doctor는 "[파일없음] … 이 자산을 쓰는 경로는 실행되지 않음"이라고 하지만, 실제로는 face·seam·rPPG·lipsync가 failed다.
- (b) `DEEPFAKE_LENS_FACE_LANDMARKER`가 없는 파일이면 mediapipe 행이 OK인데, 얼굴이 검출되면 검사가 failed다.
- (c) /venv(opencv 5.0, CascadeClassifier 없음)에서 opencv 행은 "OK — 얼굴 검출…"인데 rPPG와 lipsync는 "의존성 부재: cv2.CascadeClassifier".
- 위치: doctor.py:60, model_assets.asset_status.
- → 재정의 대상이 없으면 "검사 실패(failed)"로 표시하고, mediapipe와 opencv 행은 실제 경로 가용성으로 warn을 띄운다.

R18-14 (Cosmetic). 추적표 매핑이 커밋 제목과 다르다.
- R16-8은 ids[]에서 G13(사진 게이트)이다. 커밋 42f0344 제목은 G29다. R17-11이 R16-13에서 고친 것과 같은 오류다.
- 그 밖에 ids[]와 제목이 다른 항목: R16-12(G27↔G26), R15-6(G12↔G34), R16-10(G30↔G34), R13-6(G12↔G30), R15-3/R16-2/R16-3(G10은 profile revision과 무관).
- build_traceability_commits --check가 이 불일치를 검사하지 않는다.
- → 매핑을 정정하고, ids[]와 제목의 갭 차이가 있으면 note가 없는 한 --check가 실패하게 한다.

R18-15 (Cosmetic). 출력 쓰기 오류가 "예기치 않은 오류"로 보고된다.
- `scan … | head` → "예기치 않은 오류(BrokenPipeError)" rc 1.
- `--json-out /dev/full`(ENOSPC) → 전체 스캔 뒤 "예기치 않은 오류(OSError)" rc 1.
- stdout을 닫은 경우(`>&-`) → JSON이 소실되는데 rc 0.
- `--json-out`은 원자적으로 쓰지 않는다(cli_render.py:75-79). 중단되면 이전 보고서가 잘려 사라진다.
- → BrokenPipe는 조용히 141로 끝낸다. ENOSPC 등은 한국어 "출력 파일을 쓸 수 없습니다: 디스크 공간 부족"으로 알린다. stdout이 없으면 오류로 처리한다. 출력은 임시 파일에 쓰고 rename한다.

R18-16 (Cosmetic). 증거설명서 사건명 기본값이 특정 죄명이다(조정자 지적 3, 재현함).
- 기본값: "성폭력처벌법위반(허위영상물편집등) 및 정보통신망법위반"(cli_parser.py:285/597, evidence_statement.py:366, webapp_api.py:1775).
- 사건번호·원고·피고는 "(… 입력)" 자리표시인데 사건명만 실제 죄명이 들어간다. N17(기본 신원 제거)과 같은 원칙으로 보면 근거 없는 기재다.
- → "(사건명 입력)" 자리표시로 바꾼다.
- 판단 불가 파일을 호증으로 나열하는 것 자체는 행마다 정직한 결론과 "별도 검증 필요"가 적히므로 위반이 아니다.

R18-17 (Cosmetic). GUI가 실행되지 않은 모델에 "외부 모델 원점수(미보정, 결론 불참여) — 0"을 표시한다(조정자 지적 2, 코드와 데이터로 확인).
- 데이터: `model_analysis {available:false, score:0}`. 위치: gui.js:723, 1492.
- 측정되지 않은 0이 측정값처럼 보인다. G1과 QA-OUT-5("확률 없음") 취지에 어긋난다.
- → `available === false`이면 "없음 — 미실행(사유)"로 표시한다.

R18-18 (Cosmetic). `agent` 계층 진단 JSON이 등급과 분류를 낸다(조정자 지적 5, 재현함).
- 재현: 평범한 사람 글("operator", " done" 포함)에 `diagnostic.confidence: "high"`, `raw_score 90`, `agent_type: "openai_agent"`가 나온다.
- 다른 계층 진단(3d, avatar, text-advanced, ml-classify)에는 confidence가 없다. 문서는 "no band, no verdict"라고 한다.
- 위치: ai_agent.py:87, 210. 부분 문자열 매칭도 원인이다(:39, :130).
- → confidence와 agent_type 분류를 빼고 원신호만 둔다. 단어 경계로 매칭한다.

R18-19 (Cosmetic). 잡다한 사용자 표시 불일치:
- (a) `video --extract`가 모든 추출에 실패해도(-9, 127) rc 0이고 안내가 없다.
- (b) `vendor-weights --verify`에서 폴더를 `DEEPFAKE_LENS_MODELS_DIR`로 줬는데도 메시지에 "(--models-dir)"가 붙는다. 없는 폴더나 파일이어도 "프로필이 하나도 없어"라고 한다(--models-dir로 줄 때는 "모델 폴더가 없습니다").
- (c) config.json의 BOM은 rc 2 오류인데 assets.json의 BOM은 허용한다. config 경로가 폴더면 조용히 무시한다.
- (d) zip 멤버 이름을 euc-kr로 해독해 CP949 확장 음절(예: "똠방각하")이 cp437 모지바케가 된다(archives.py:295 → cp949).
- (e) `--depth 1` 얕은 클론에서 `qa_phase0.py --verify-record`가 "다시 생성하세요"라고 한다. traceability --check는 "얕은 클론"이라고 정확히 안내한다.
- (f) `api-serve --folder`(잘못된 인수)에 하위 명령이 아니라 최상위 사용법을 보여 준다.

## 잔여 1단계 리스크
- 성능 측정 호스트의 부하가 커서 실행 시간 비교를 쓸 수 없었다. 호출 수는 같다. 1단계 QA-SYS-8은 조용한 기준 장비에서 측정해야 한다.
- `/proc/<pid>/task/*/children`이 없는 커널(gVisor, WSL1)에서는 R17-3의 자손 수거가 동작하지 않는다(PDEATHSIG와 신호만 남음).
- run_child를 부를 때마다 Python 감독 프로세스가 하나씩(약 30–50 ms) 더 뜬다. 1단계에서 영상이 많아지면 비용이 된다.
- PDEATHSIG가 스레드 단위라, 앞으로 자식을 띄운 스레드가 먼저 끝나는 구조(비동기 작업자)를 도입하면 자식이 정지된다.
- 다형(polyglot) 파일(jpg 뒤에 zip을 붙인 것)의 숨은 내용은 검사하지 않는다(1단계 컨테이너 분석).
- 같은 mtime/ctime으로 자산 파일을 제자리 수정하면 같은 프로세스 안에서는 다이제스트 메모가 갱신되지 않는다(합의된 매니페스트 잔여와 같은 부류).

게이트 0 판정: 아니오 — 수정 목록 R18-1 ~ R18-19. 차단은 R18-1(회귀), R18-2(회귀), R18-3이다. 나머지도 HANDOFF §6 "면제 없음" 원칙에 따라 수정 대상이다.

---
## 부록: 실행한 명령과 결과 요약

**전체 게이트(ec0d057)**: `gate.sh full`
- 단위 테스트: main 1578 OK (skip 98), api 1578 OK (skip 47).
- ruff, mypy(128 files) 통과. coverage 80%.
- cli_smoke OK, 측정 게이트 10/0, sync_model_docs 통과, verify_contracts OK, stress_shutdown 30/30. GATE rc=0.

**기록 확인**: `qa_phase0 --verify-record` rc 0. `build_traceability_commits --check` rc 0(226커밋).

**반복 실행(플레이크)**: 한 컨테이너에서 실패 0.
- 전체 스위트 3회 × 2 venv.
- test_shutdown + test_interrupt + test_native_path 6회 × 2 venv(86 tests).
- stress_shutdown: TERM/HUP/KILL 각 50회, 래퍼 ffmpeg 각 30회, api venv TERM 50회.

**얕은 클론(`--depth 1`)**:
- 단위 테스트: 문서화된 2개 테스트만 실패(QaSys10 deletion reasons, history document — QA-ENV-DEPENDENT-TESTS.md R16-12).
- verify-record rc 1(메시지 문제 → R18-19e), traceability rc 1("얕은 클론" 안내). `--depth 2`에서는 verify-record rc 0.

**커밋별 게이트(두 venv 단위 테스트, ruff, mypy)**:
- 8f205bd, 27bc793, 8cd1a33, c1dee0a, 498558d, 111b302, cbf9752, ee2818f, 98672a1, 3fe35fb, ec0d057: 전부 OK.
- 기준 ed30923: R17-12 테스트와 R17-1 플레이크로 실패(main 5, api 3) — 이번 라운드에서 고친 결함과 일치.

**수정 없이 새 테스트 실행(소스만 부모 커밋으로 되돌림)**: 모든 fix 커밋의 새 테스트와 수정 테스트가 실패했다.
- 27bc793: 4F/2E, 8cd1a33: 4F/1E, c1dee0a: 2F/3E, 498558d: 1F, 111b302: 1F/2E, cbf9752: 2F, ee2818f: 3F, 98672a1: 4F, 3fe35fb: 1F.
- 8f205bd는 테스트만 바꾼 커밋이다.
- 테스트 약화는 없다. 결함을 고정하던 테스트 2건(test_face의 override 기대, AvAudioCoverage의 "no audio" 기대)은 R17-4/5 주석을 달고 바뀌었다. 삭제 0건.
- 커밋 메시지는 R18-12(ee2818f 과장)와 R18-14(매핑)를 빼면 동작과 맞는다.

**R17 종료 탐침**:
- killparent.py: 4가지 모드, 지연 0–0.6 s·0–0.12 s, 스레드/메인 생성, 총 360회 → 누수 0.
- rc.py: 신호 6종, 127, 정지된 자손, 늦은 fork, 타임아웃 → 정상.
- ff/run.sh: 고장 ffmpeg 5종 × 2 venv × 2 영상.
- cache7.sh: 재정의 6단계.
- r179*.sh: BOM, 깨진 JSON, 스키마, 폴더, UTF-16 매니페스트, ~, 공백, ~nouser, 폴더 재정의.
- vendor-weights: 빈 폴더, 프로필 아닌 JSON, 깨진 프로필, 1개, 없음, 파일, 환경 변수.
- TMPDIR가 파일인 경우.
- sigint*.py: 0.01 s 간격 Ctrl-C 160회 × 명령 2종.
- batchint.py: 2000파일 batch.
- webint.py: 영상 8개 web 스캔 중 SIGINT 5시점.

**회귀·결정성·성능**:
- 악성 폴더 /tmp/H(95 항목, 106행: 이상한 이름, 비UTF-8, 심볼릭 링크, FIFO, 잘림·손상, 0바이트, 거대 크기, 폭탄, 아카이브 11종): ed30923과 HEAD를 main·api·deep-signals 조합으로 비교했다. 차이는 R17-4/5 사유뿐.
- 결정성: 3회, 워커 4, 이름 변경, cold/warm, 동시 4개 → 동일.
- QA-IN-1 스냅샷(deep, heatmap, cache, allow-symlinks): 변화 0.
- 세 경로(CLI/web/api-serve) 106행이 동일.
- 성능: 2000파일 cProfile 호출 수 52,218,615 vs 52,219,129. 실행 시간은 호스트 부하로 측정 불가.
- 주의: 앞서 돌린 perf.sh는 컨테이너 WORKDIR(/repo)의 HEAD 코드를 import해 무효였고, 결과에 반영하지 않았다.

**새 영역 탐침(40건 이상)**:
- 감독 코어 덤프, procfs·dev·socket·FIFO 링크.
- 서명: 중복 키, BOM, 뒤따르는 데이터, 키 공백.
- 표시 이름 19종(NFC/NFD, bidi, 채움 문자).
- 아카이브: zip 심볼릭·FIFO 멤버, 내부 `..`, 폴더-파일 충돌, 희소 tar, 심볼릭 폴더 tar, 6000멤버, CP949 이름, gz/bz2 단일 파일, 다형 파일.
- IDAT 뒤 tEXt, 200MP·100MP 헤더, -X dev 경고, config 6종, evidence 출력 위치.
- 깨진 파이프, /dev/full, 닫힌 stdout, pin-asset BOM, 대문자 핀, video --extract 실패.
- 취소된 스캔, GUI 원점수, 증거설명서 기본값, 리뷰 키, agent 등급(조정자 지적 5건).
- doctor opencv 행, rPPG 디코드 실패, 캐시 키의 경로 의존성.

**정리**: 직접 띄운 컨테이너(r18probe, r18pc1/2, r18flake, r18audit)는 모두 종료·삭제했다. 클론 /private/tmp/claude-501/r18/repo는 수정하지 않았다(git worktree 메타데이터는 컨테이너 사본에서만 생성). 탐침 스크립트는 /private/tmp/claude-501/r18/out/p/, 로그는 /private/tmp/claude-501/r18/out/*.log에 있다.
