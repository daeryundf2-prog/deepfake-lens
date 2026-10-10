# Deepfake Lens — 0단계(phase0) 구현 명세

리포지토리: /home/claude/repos/deepfake-lens (브랜치 phase0). 파이썬 venv: /tmp/claude-0/-home-claude/c59f2345-4825-53b3-86de-7341aca55351/scratchpad/venv (opencv, numpy, Pillow, librosa, scikit-learn, c2pa, coverage, ruff, mypy 설치됨. torch/transformers/mediapipe/fastapi 없음 — 모델 경로는 가짜 프로필 + monkeypatch로 테스트).

최종 사용자: 법무법인 디지털포렌식 조사관. 파일/폴더 단위 의뢰를 받아 "AI 생성/조작인가"에 답하고 감정서를 쓴다. 도구의 가치는 "맞히는 확률"이 아니라 "틀렸을 때 왜 틀렸는지 설명할 수 있고, 모를 때 모른다고 말하는가"에 있다.

## 0단계의 목표 (게이트 0)
- 검사 실패/예외가 절대 "낮음/깨끗함"으로 귀결되지 않는다 (fail-closed).
- 결론은 세 가지뿐: 조작·생성 근거 있음 / 원본성 근거 있음 / 판단 불가. 숫자 점수는 보조. `medium` 밴드가 생성되는 경로 0.
- 근거는 결정적(메타데이터, C2PA, 재압축 지문) vs 통계적(모델) vs 어휘적(키워드)으로 구분되어 표시되고, 어휘적 근거는 결론을 바꾸지 못한다.
- 파일마다 어떤 검사를 했고 어떤 검사를 왜 못 했는지(coverage)가 기록된다.
- 모델 가중치는 sha256/revision 핀 없이는 로드되지 않는다. 측정 게이트(클래스당 200, AUROC CI 하한 0.85)를 못 넘은 프로필은 supported:false.
- 사진이 아닌 이미지(노이즈, 그라데이션, 스크린샷, 그래픽, 문서 스캔)는 생성 탐지 비적용.
- CLI/GUI/API가 같은 파일에 같은 결과.
- 캐시는 내용 해시 기반. 서명은 본문 전체를 덮는다. read-root는 운영자만 등록.

## 공통 규칙
1. 브랜치 phase0, WP마다 커밋 1개 이상, 커밋 메시지에 닫는 갭 ID(G1–G34)를 적는다. 커밋 메시지 끝에 다음 두 줄을 붙인다:
   Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
   Claude-Session: https://claude.ai/code/session_01LBvAKB1o93ygmqLC2ashQM
2. 기존 테스트는 유지한다. 결함을 고정하고 있는 테스트(예: 노이즈 이미지가 medium이기를 기대)만 고치고, 고칠 때 주석에 갭 ID를 남긴다. 테스트 삭제는 커밋 메시지에 이유를 적는다.
3. 출력 문자열은 한국어, 코드 식별자는 영어. 매직 넘버는 상수로 올리고 주석에 출처를 적는다.
4. `python -m unittest discover deepfake_lens/tests`, `ruff check deepfake_lens`, `mypy`(pyproject 목록), `coverage report`(fail_under 유지)가 모두 통과한 상태로만 커밋한다. 기존에 1개 실패하는 test_archives.py:166(파일 순서 의존)은 WP-G에서 정렬로 고쳐진다; 그 전까지는 그 1건만 허용.
5. 신경망 가중치 없는 환경에서 모든 새 테스트가 돌아야 한다.
6. 새 필드는 `docs/deepfake-lens-json-contract.md`와 `contracts/`에 같은 커밋에서 반영한다.
7. 테스트 실행: `cd <repo> && <venv>/bin/python -m unittest discover deepfake_lens/tests`. 린트: `<venv>/bin/ruff check deepfake_lens`, `<venv>/bin/mypy` (pyproject 설정 사용).

## 1차 리뷰에서 확인된 갭 (요약)
- G1 R-OUT-4: 모델 예외 시 score 0/available False(model_adapter.py:564); 메타데이터만 있으면 LOW(core.py:948,1070); 모든 예외를 "선택 의존성 부재"로 기록(core.py:580-655).
- G2 R-TXT-3: korean-roberta 프로필이 klue/roberta-base(분류 헤드 없는 MLM)를 가리키고 가중치 1.2, 한국어 유일 멤버 → 무작위 헤드가 HIGH를 만든다.
- G3 R-IMG-2: pixel.py:728 fusion이 max(66,…) 바닥값으로 노이즈/그라데이션/블러를 전부 35/medium으로.
- G4 R-TXT-2: "language model"/"언어 모델" 한 번에 +35(core.py:838), 짧은 글 캡 면제.
- G5 R-OUT-1/5: 점수 = 손으로 정한 가중치 합(core.py:1063-1076). fusion.py 가중치 상수. 보정 매핑 없음.
- G6 R-OUT-1: 양성 결론("원본성 근거 있음") 없음.
- G7 R-OUT-6: GUI는 thresholds= 미전달(webapp_api.py:113), CLI는 measured 프로필 자동 로드(cli_render.py:38-60).
- G8 R-OUT-6: api_server.py:619-623 preview 튜플 오언패킹.
- G9 R-SYS-1: 27개 프로필에 sha256/revision 핀 0개; vendor_weights.py:401-428 해시 미선언 시 검증 없이 fetched; file:// 허용.
- G10 R-SYS-1: model_runtimes.py from_pretrained 5곳 revision 없음.
- G11 R-SYS-3: scan_cache.py:158-196 캐시 키가 경로+크기+mtime.
- G12 R-OUT-3: 얼굴 미검출 시 검사가 조용히 건너뛰어짐. 커버리지 구조 없음.
- G13 R-IMG-2: 사진/비사진 게이트 없음.
- G14 R-IMG-1: 메신저 재압축 지문 없음 (1단계).
- G15–G16, G18–G20, G25: 코퍼스/측정 부재 (1단계).
- G17 R-IMG-5: 픽셀 휴리스틱(AUROC 0.43–0.48)이 결론에 영향.
- G21–G23: 음성 편집 흔적, 문서 타임라인/이력, PDF 서명 (1단계).
- G24 R-TXT-1: 텍스트 결론이 이미지와 같은 밴드로 나옴.
- G26–G27: 신뢰구간 0건, 코퍼스 재현 불가, experiments/*.py가 존재하지 않는 REPO_ROOT/models 참조(eval_all.py:58, eval_text_detect.py:83, suggest_weights.py:60).
- G28 R-QA-3: thresholds.json/sbi score_bias가 평가 데이터에 in-sample로 맞춰짐; 측정 게이트 없음.
- G29 R-SYS-2: doctor가 torch 없는데 HF 모델 OK 표시; api-serve가 uvicorn 없으면 traceback.
- G30 R-SYS-4: signature_note 서명 범위 밖(signing.py:22,106); 웹 보고서 미서명; 모델 해시 미포함.
- G31 R-IN-1: /api/scan?folder= 임의 경로 read-root 등록(webapp_api.py:82); /api/report heatmap_path 임의 PNG 읽기(reports.py:447-456).
- G32 R-IN-3: 파일 순회 순서 OS 의존(scan_cache.py _iter_files).
- G33: umm-maybe 가중치가 supported true/false 두 프로필로 존재; model_adapter.py:388-389 scores 덮어쓰기 버그.
- G34 R-SYS-3: 중첩 아카이브 폭 제한 없음(archives.py:347-353); api_server _JOBS 무제한; async 핸들러에 동기 코드.

## WP-A 결과 계약 교체 (G5, G6, G12, G24)

변경 파일: result_types.py, 새 파일 decision.py, core.py, serialization.py, cli_render.py, reports.py, evidence_statement.py, gui.js, gui.html, webapp_api.py, api_server.py, docs/deepfake-lens-json-contract.md, 테스트.

스키마 (result_types.py):
- `Verdict` enum: manipulation_evidence(조작·생성 근거 있음), authenticity_evidence(원본성 근거 있음), undetermined(판단 불가).
- `Grade` enum: evidence(감정 근거로 사용 가능), reference(참고만). 텍스트는 항상 reference.
- `EvidenceItem`: title, detail, kind(deterministic/statistical/lexical), direction(synthetic/authentic/neutral), strength(strong/moderate/weak), layer(metadata/c2pa/recompression/face/model/pixel/text 등), probability: float|None, probability_ci: tuple[float,float]|None, calibration_id: str|None, measured_on: str|None.
- `CoverageEntry`: check(검사 이름), status(ran/skipped/failed), reason(skipped/failed 시 필수; failed는 예외 클래스명 포함).
- `ClassificationResult` 추가 필드: verdict_code: Verdict, grade: Grade, evidence: list[EvidenceItem], coverage: list[CoverageEntry], probability: float|None, probability_ci, score_is_calibrated: bool, reference_signals: list[EvidenceSignal](미측정 휴리스틱, 결론에 불참여).
- 호환: band는 verdict에서 파생 — manipulation→high, authenticity→low, undetermined→unknown. medium은 더 이상 생성하지 않는다(열거형 값은 읽기 호환용으로 유지). score는 probability가 있을 때만 round(p*100), 없으면 0이고 score_is_calibrated=false. signals 필드는 유지하되 evidence에서 파생(제목/detail/weight=strength 매핑)해 과거 소비자가 깨지지 않게 한다.

결정 규칙 (decision.py, 순수 함수 `decide(evidence, coverage, grade, thresholds=None) -> Verdict`, 순서대로 평가):
1. grade == reference → undetermined.
2. deterministic+synthetic+strong 근거가 하나라도 있으면 → manipulation_evidence (coverage 실패가 있어도 유지; 실패는 coverage에 남는다).
3. coverage에 failed가 하나라도 있으면 → undetermined.
4. statistical 근거 중 calibration_id와 measured_on이 있고 probability >= 프로필 threshold인 것이 있으면 → manipulation_evidence. (0단계에는 보정된 모델이 없어 도달 불가. 합성 입력으로 테스트.)
5. deterministic+authentic+strong 근거가 있고, synthetic 방향 근거가 lexical을 제외하고 하나도 없으면 → authenticity_evidence.
6. 그 외 → undetermined.

기존 신호의 분류 (core.py에서 EvidenceItem으로 변환):
| 기존 신호 | kind | direction | strength |
| C2PA 매니페스트 유효 + AI 도구 서명 | deterministic | synthetic | strong |
| 생성 도구 메타데이터(A1111 params, PNG tEXt 생성기 태그, XMP 생성 도구 필드) | deterministic | synthetic | strong |
| C2PA 매니페스트 유효 + 카메라 서명, 해시 일치 | deterministic | authentic | strong |
| 카메라 EXIF 일관(기종·렌즈·촬영시각·GPS 정합, 재압축 흔적 없음) | deterministic | authentic | moderate |
| 메신저 재압축 흔적 | deterministic | neutral | moderate |
| 정사각 해상도, 메타데이터 부재 | deterministic | neutral | weak |
| 외부 모델 점수 | statistical | synthetic | probability로 표현, 보정 없으면 calibration_id=None |
| 픽셀 앙상블, 주파수 휴리스틱 | — | — | reference_signals로 이동, evidence 아님 |
| AI 정체성 문구, 템플릿 접속사, 문체 통계 | lexical | synthetic | weak |
| 심층 신호(얼굴 심, inpaint, 추적) | statistical | synthetic | probability 없음, calibration_id=None → 결정 불참여 |

텍스트: analyze_text는 grade=reference, verdict 문자열 "참고: …", limitations 맨 앞에 고정 문구 "텍스트 생성 여부 판별은 2026년 현재 증거능력이 없으며 참고 정보입니다."

테스트: test_decision.py에 규칙 6개 × 경계 조합 최소 24케이스. test_json_contract.py에 새 필드 왕복. scripts/gui_smoke.py에 verdict/evidence/coverage 렌더링 확인 추가.
완료 기준: QA-OUT-1, QA-OUT-3, QA-OUT-6 통과. medium 밴드가 생성되는 경로 0.

## WP-B Fail-closed (G1)

변경 파일: core.py, model_adapter.py, audio.py, video_analysis.py, face.py, documents.py, 새 파일 checks.py.
- checks.py에 `run_check(name: str, fn: Callable[[], T]) -> tuple[T|None, CoverageEntry]`. ImportError/ModuleNotFoundError → skipped, reason "의존성 부재: <module>". 그 외 예외 → failed, reason f"{type(exc).__name__}: {str(exc)[:200]}". 로그에 traceback 기록.
- core.py의 _deep_image_layers, _deep_video_layers, 모델 호출, 얼굴 검출, C2PA, 오디오, 문서 파서를 전부 run_check로 감싼다. `except Exception: pass` 패턴은 core.py, model_adapter.py, webapp_api.py, api_server.py에서 0개(ruff BLE001, S110을 이 네 파일에 한해 활성화 — pyproject per-file select).
- 얼굴 검출 0건 → CoverageEntry("face_manipulation", "skipped", "얼굴 미검출").
- 해상도 < 128 px → 모델 검사 skipped, reason "측정 범위 밖: 해상도".
테스트: monkeypatch로 각 검사에 RuntimeError 주입 → coverage failed + verdict undetermined. ImportError 주입 → skipped. QA-OUT-2, QA-OUT-3.

## WP-C 모델 동물원 정리 (G2, G9, G10, G33)

삭제: korean-roberta-text-detector-runtime.json, cnndetection-runtime.json, univfd-runtime.json, qwen-ppl-runtime.json, binoculars-runtime.json, openai-detector-runtime.json, aide-frames-runtime.json, umm-maybe-detector-runtime.json, melodymachine-w2v2-runtime.json, dire-runtime.json, genconvit-face-runtime.json, faceswap-ffpp*.json, face-manipulation-vit*.json. 삭제된 프로필의 측정 기록은 docs/MODEL-REJECTIONS.md에 한 줄씩 옮긴다.
남는 프로필: aide, ai-image-swin, community-forensics-vit(+frames), sd-turbo-det, sbi-effnet, sbi-frames, aasist, wav2vec-deepfake-audio, fakespot-detector. 전부 supported: false로 바꾼다(WP-I 게이트 미충족). 각 프로필에 `pin` 객체 필수: 로컬 체크포인트는 {"sha256": ""}, HF 허브는 {"revision": ""}. 값은 비워 두되 필드는 존재.
로더 (model_adapter.py): pin이 비어 있거나 불일치하면 로드 거부, coverage failed, reason "미고정 프로필" 또는 "무결성 불일치". _PROFILE_SHA_OK 제거, 매 로드마다 검증. model_runtimes.py의 모든 from_pretrained(hub_model)에 revision=pin["revision"] 전달. vendor_weights.py: https만 허용, 선언된 sha256 없으면 fetched로 표시하지 않음(다운로드는 하되 "unverified"로).
CLI: `deepfake-lens vendor-weights pin <profile>`: 로컬 파일의 sha256을 계산해 프로필에 기록. 허브 모델은 huggingface_hub로 현재 commit sha를 조회해 기록(온라인 필요, 명시적 명령에서만).
버그: _aggregate_profile_results 388행 중복 대입 삭제, 테스트로 언어 게이트 제외 멤버가 spread에 불참여함을 확인. DEFAULT_TEXT_ENGINE_PROFILE = None.
문서 동기화: scripts/sync_model_docs.py가 프로필에서 models/README.md, NOTICE.md, model_registry.py의 표를 생성. CI에서 생성물과 커밋물 diff가 있으면 실패.
테스트: QA-SYS-1, QA-SYS-2(가짜 체크포인트 파일로), 언어 게이트 회귀.

## WP-D 사진/비사진 게이트와 픽셀 휴리스틱 분리 (G3, G13, G17)

새 파일 image_class.py: `classify_image(path) -> ImageClass` with kind in {photo, graphic, screenshot, pattern, document_scan, too_small} + reasons. 규칙은 결정적이고 상수는 모듈 상단에 출처 주석과 함께:
- too_small: 긴 변 < 128 px.
- pattern: 고유 색 수 < 64 또는 노이즈 잔차 분산이 사진 범위 밖(극단적으로 낮거나 높음) 또는 행/열 자기상관 > 0.999(그라데이션) 또는 < 0.05(백색 노이즈).
- screenshot: 상단 상태바 높이 단색 띠, 화면 해상도 표(일반 폰/모니터 해상도 목록), 대량의 정확한 수평·수직 엣지.
- graphic: 고유 색 비율 낮고 대면적 단색 영역 > 40%.
- document_scan: 밝기 히스토그램 양봉, 백색 비율 > 60%, 텍스트성 엣지.
- 그 외 photo.
core.analyze_file는 photo가 아니면 모델·픽셀·얼굴 검사를 skipped "사진 아님: <kind>"로 기록하고 메타데이터·C2PA만 수행한다.
픽셀 (pixel.py): _fuzzy_decision_tree_fusion의 max(66, …) 등 바닥값 전부 제거, 가중평균만 반환. 결과는 reference_signals로만 들어가고 _pixel_evidence_signal은 삭제.
픽스처: scripts/make_adversarial_fixtures.py가 fixtures/adversarial/에 그라데이션 10, 노이즈 10, 단색 10, 체커보드 10, 블러 노이즈 10, 합성 스크린샷 10, 문서 스캔 합성 10을 시드 고정으로 생성(커밋하지 않고 테스트에서 생성).
테스트: QA-ADV-1, QA-ADV-2. fixtures/benchmark/real-like-texture.png은 photo 또는 pattern 중 무엇으로 분류되든 verdict는 undetermined.

## WP-E 키워드 단독 승격 제거 (G4)
core.analyze_text의 정체성 문구, 템플릿 접속사, 문체 통계 신호를 전부 EvidenceItem(kind=lexical, strength=weak)로. score_cap_exempt_titles 제거. 테스트: QA-ADV-3 — "언어 모델"이 들어간 사람 글 30건(픽스처에 추가)이 전부 undetermined/reference.

## WP-F 진입점 통일 (G7, G8)
새 파일 analysis_api.py: AnalysisOptions(dataclass: pixel_mode, deep_signals, thresholds_path, models_dir, fusion_profile, max_files, recursive, cache) + analyze_path(path, options) -> ScanItem + scan_folder(folder, options, should_stop=None) -> (summary, items). thresholds는 여기서 한 번 로드(기본: 패키지 models/thresholds.json). cli.py, webapp_api._scan_payload, api_server는 이 두 함수만 호출한다. model_path/fusion_profile 쿼리 파라미터는 models_dir 안의 파일명만 허용.
api_server.py 619행 preview 언패킹을 (status, data, message, mime)로 고치고 X-Content-Type-Options: nosniff 추가. GET /api/scan, /api/scan-cancel에도 클라이언트 헤더 요구.
테스트: QA-OUT-4 — 같은 폴더를 세 경로로 돌려 items[].result의 verdict/evidence/coverage/thresholds 출처 동일 확인(fastapi 없으면 그 부분만 skip).

## WP-G 무결성 (G11, G30, G31, G32)
- scan_cache.py: 키 = sha256(파일) + 옵션 해시 + 도구 버전 + 프로필 pin 해시 목록. 경로·mtime 미사용. _iter_files는 경로 문자열 정렬.
- signing.py: canonical 본문에 signature_note, tool_version, model_pins, 모든 item의 sha256 포함. 제외 필드는 signature와 signature_key_id뿐. verify는 key_id 불일치를 별도 사유로 보고. 웹 보고서도 DEEPFAKE_LENS_REPORT_KEY가 있으면 서명.
- webapp_api.py: _register_read_root는 run_server의 --folder와 --allow-root 인자에서만 호출. 요청의 folder가 등록 범위 밖이면 403. reports._heatmap_img는 resolve_path를 받아 범위 밖 경로를 무시.
- security.py의 문자열 grep 검사를 실제 동작 테스트(QA-SYS-7)로 교체.
테스트: QA-IN-2, QA-IN-4, QA-SYS-6, QA-SYS-7.

## WP-H 운영 버그 (G29, G34)
- doctor.py: 프로필별로 "pin 있음/없음", "런타임 의존성 import 가능", "체크포인트 존재+해시 일치" 세 열. "실행 가능" 요약은 세 열이 전부 OK인 것만 센다.
- api_server.py: uvicorn/fastapi 없으면 안내 메시지 후 exit 2. _JOBS 상한 32, 클라이언트 단절 시 cancel 설정, _JOBS.get 가드, 동기 분석은 run_in_threadpool.
- archives.py: 재귀 전체에 누적 예산(총 바이트 2 GB, 총 멤버 5000, 중첩 아카이브 50) 전달. 7z/rar 심볼릭 멤버 거부. docstring의 filter="data" 언급 수정.
- model_cache.py, _READ_ROOTS 접근에 lock.
테스트: QA-SYS-3, QA-IN-5(중첩 zip 100개 케이스 포함).

## WP-I 측정 게이트와 코퍼스 기반 (G26, G27, G28)
- experiments/*.py의 REPO_ROOT / "models"를 deepfake_lens.cli.default_models_dir()로 교체.
- 새 파일 corpus_manifest.py: 스키마 corpus-manifest-v1 — corpus_id, created, items[]: {id, relpath, sha256, modality, label(real|synthetic|edited), generator, variant(original|kakao|telegram|instagram|jpeg_q50 …), split(train|val|test), source_note}, manifest_sha256. CLI `deepfake-lens corpus build <dir> --out manifest.json`, `corpus split --seed --ratio 60/20/20 --group-by <field>`(같은 원본에서 파생된 변형은 같은 split), `corpus verify`.
- evaluation_metrics.py: bootstrap_ci(scores, labels, metric, n=2000, seed) -> (lo, hi). evaluate.py의 모든 AUROC/recall/FPR 출력에 CI 동반.
- 프로필 필드 measured_on: {corpus_id, manifest_sha256, split: "test", n_pos, n_neg, auroc, auroc_ci, fpr_at_threshold, recall_at_threshold, measured_at}. scripts/check_measurement_gate.py: supported: true인데 measured_on이 없거나 n_pos < 200 or n_neg < 200이거나 auroc_ci[0] < 0.85(텍스트는 제외, 별도 규칙)이면 실패. CI 작업으로 추가.
- docs/STATUS-AND-IMPROVEMENTS.md, CHANGELOG.md, experiments/*.md의 성능 수치 위에 "미검증(n 부족 또는 재현 불가) — 2차 계획 WP-I 참조" 배너 삽입. AIDE_EVALUATION.md의 EER 0.000 행은 "재계산 필요"로 표시.
테스트: QA-SYS-9(가짜 프로필 추가 시 게이트 스크립트 실패), 매니페스트 왕복과 split 그룹 불변.

## WP-J QA 하네스와 적합성 표
- deepfake_lens/tests/qa/ 아래 QA ID를 파일명으로: test_qa_in.py(IN-1,2,4,5), test_qa_out.py(OUT-1–6), test_qa_adv.py(ADV-1,2,3), test_qa_sys.py(SYS-1,2,3,6,7,9,10). 가중치 없이 돌아야 한다. 각 테스트 docstring 첫 줄은 QA ID와 통과 기준 원문.
- scripts/qa_phase0.py: 위 테스트를 돌리고 docs/CONFORMANCE.md를 생성: 요구사항 ID | 갭 ID | QA ID | 결과 | 로그 경로. 0단계 범위 밖(QA-MOD, QA-ADV-4–6, QA-SYS-4,5,8)은 "1단계" 또는 "수동"으로 표시.
- QA-SYS-4(Windows 설치), QA-SYS-5(재개), QA-SYS-8(성능)는 수동 체크리스트로 docs/QA-MANUAL.md에.

## QA 시나리오 (통과 기준 원문)
QA-IN-1: 지원 형식 전부의 샘플 1개씩을 읽기 전용 폴더에 두고 전체 검사 → 모든 파일의 검사 전후 SHA-256 동일, mtime 불변, 폴더에 새 파일 0개.
QA-IN-2: 같은 폴더를 3회 검사(중간에 프로세스 재시작, 폴더 이름 변경) → 타임스탬프·절대경로 필드를 제외한 JSON이 바이트 단위로 동일. 파일 순서 동일.
QA-IN-4: 마지막 바이트만 바꾼 동일 크기 파일을 같은 경로에 넣고 touch -r로 mtime 복원 후 재검사 → 캐시 미사용, 새로 분석, 해시가 다르게 기록.
QA-IN-5: 손상 파일 20종(잘린 JPEG, 깨진 mp4 moov, 빈 파일, 확장자 위장, zip 폭탄, 중첩 zip 100개) → 프로세스 생존, 각 파일이 "판단 불가 + 이유" 또는 "미지원". 디스크 사용 상한 초과 없음. 다른 파일 결과에 영향 없음.
QA-OUT-1: 신경망 가중치를 제거한 상태에서 사진 100장 검사 → 결론이 "판단 불가" 또는 결정적 근거에 의한 결론뿐. "낮음/깨끗함"이 통계적 근거 없이 나오는 건 0개.
QA-OUT-2: 모델 추론 함수에 예외를 강제 주입(monkeypatch)하고 검사 → 커버리지에 "실패: <예외 유형>" 기록, 결론 "판단 불가". "의존성 부재"로 표기되지 않음.
QA-OUT-3: 얼굴 없는 사진, 측면 얼굴, 저조도 얼굴 각 20장 → 얼굴 미검출 시 커버리지에 "얼굴 검사 미실행: 얼굴 미검출" 기록. 얼굴 조작 결론이 "없음"으로 나오지 않음.
QA-OUT-4: 같은 폴더를 CLI, GUI(/api/scan), API 서버로 각각 검사 → 세 결과의 결론·근거·확률·임계값 출처가 동일.
QA-OUT-5: 모델 확률이 표시된 모든 결과 → 각 확률에 보정 코퍼스 ID, 측정 조건, 95% CI가 붙어 있음. 측정 범위 밖 입력(64 px 이하)은 "범위 밖"으로 표시되고 확률 없음.
QA-OUT-6: 텍스트 파일 50개 검사 → 모든 결론 등급이 "참고", 보고서에 법적 한계 문구 존재.
QA-ADV-1: 그라데이션, 랜덤 노이즈, 단색, 체커보드, 블러 노이즈 각 10장 → 전부 "사진 아님 — 생성 탐지 비적용". 의심 판정 0.
QA-ADV-2: 스크린샷 50장(카톡 대화, 웹페이지, 문서 뷰어) → 스크린샷으로 분류, 생성 탐지 미적용, 의심 판정 0.
QA-ADV-3: AI에 대해 쓴 사람 글 30건("언어 모델", "as an AI" 포함) → 키워드는 근거 목록에 나타나되 결론은 "참고 — 근거 부족".
QA-SYS-1: 프로필의 sha256을 한 글자 바꾸고 검사 → 모델 로드 거부, 결론 "판단 불가: 모델 무결성 실패".
QA-SYS-2: 가중치 파일을 다른 파일로 바꾸고 검사 → 동일.
QA-SYS-3: 필수 모델 하나를 삭제한 환경에서 doctor 실행 → 해당 모델 MISS, "실행 가능" 요약이 실제 검사 결과의 coverage와 일치.
QA-SYS-6: 보고서 JSON의 임의 필드(결론, 근거, note, 모델 해시) 한 글자 변경 후 검증 → 모든 경우 "변조됨".
QA-SYS-7: /api/scan?folder=/ 등 등록되지 않은 경로로 요청. heatmap_path를 외부 파일로 지정한 report 요청 → 모두 403, 응답에 파일 내용 0바이트.
QA-SYS-9: 측정 기록 없는 프로필을 models/에 추가하고 PR → CI 실패.
QA-SYS-10: 기존 633개 테스트 중 유지 대상 전부 통과. 삭제된 테스트는 삭제 이유가 커밋 메시지에 기록.
