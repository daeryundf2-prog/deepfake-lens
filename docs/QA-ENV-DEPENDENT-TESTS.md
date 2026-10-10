# 환경 의존 테스트 (QA-ENV-DEPENDENT-TESTS)

R11-2 (round 11). 선택 패키지·도구·OS 기능·픽스처가 없으면 건너뛰는(skip)
테스트의 목록이다. 건너뛴 테스트는 **삭제가 아니므로** `docs/TEST-DELETIONS.md`가
아니라 이 문서에 둔다. `scripts/qa_phase0.py`는 전체 스위트에 건너뛴 테스트가
하나라도 있으면 QA-SYS-10을 "건너뜀(환경)"으로 기록하고(통과 아님) 건너뛴 테스트와
사유를 `docs/CONFORMANCE.md` 비고에 적는다.

- 모든 건너뛰기 사유는 아래 "건너뛰기 사유" 표에 있어야 한다 —
  `deepfake_lens/tests/qa/test_qa_sys.py`의 메타 테스트가 테스트 소스의
  `@unittest.skip*`, `self.skipTest(...)`, `raise unittest.SkipTest(...)`를 AST로
  읽어 확인한다(사유는 문자열 리터럴·모듈 상수·f-문자열만 허용, f-문자열의
  자리표시자는 `{exc}`처럼 적힌 그대로).
- "환경 의존 기준선 테스트" 표는 기존 633개 기준선
  (`deepfake_lens/tests/qa/test_inventory_baseline.json`의 `pre_phase0`) 중
  건너뛰기 경로가 있는 테스트 전부다. 같은 메타 테스트가 소스에서 다시 계산한
  목록과 이 표가 같은지 확인한다(추가·삭제 모두 감지).
- 모든 테스트를 실행하려면: `pip install -e .[dev,full,provenance,hwp]` +
  QA 사이드 venv(`pip install fastapi httpx uvicorn pymupdf`) + Node.js + git 작업
  트리 + POSIX(root가 아닌 사용자). 신경망 가중치·torch가 필요한 테스트
  (`text_lm`, `speaker`, 수동 모델 테스트)는 0단계 기록 환경에서 건너뛰는 것이
  정상이며, 그 경우 QA-SYS-10은 "건너뜀(환경)"이다.

## 건너뛰기 사유

| 사유(소스 원문) | 테스트가 실행되는 환경(필요 extras·도구) | 파일 |
| --- | --- | --- |
| `':' is not allowed in Windows file names` | Windows가 아닌 OS(POSIX) | test_archives.py, test_servers.py |
| `OpenCV mp4v 인코더 없음` | `pixel`/`video` extra(opencv-python — mp4v 인코더 포함 빌드) | test_native_path.py |
| `POSIX signals` | Windows가 아닌 OS(POSIX — 자식 프로세스에 SIGTERM/SIGINT/SIGHUP을 보내 기본 동작과 스테이징 폴더 정리 확인) | test_native_path.py, test_shutdown.py |
| `PR_SET_PDEATHSIG is Linux` | Linux(`prctl(PR_SET_PDEATHSIG)` — 부모가 SIGKILL로 죽으면 ffmpeg 등 자식 프로세스도 함께 종료, R15-1) | test_shutdown.py |
| `Pillow + numpy needed for the fixture` | `dev` extra(Pillow, numpy) | test_report_labels_ko.py |
| `Pillow + numpy required to write EXIF/XMP fixtures` | `dev` extra(Pillow, numpy) | test_image_metadata_exif.py |
| `Pillow not installed` | `dev` extra(Pillow) | test_cli_operations.py, test_error_text.py, test_json_contract.py, test_model_zoo.py |
| `Pillow writes the GIF` | `dev` extra(Pillow — GIF 픽스처 작성) | test_qa_out.py |
| `Qwen2.5-0.5B tokenizer not cached` | `text_lm` extra(torch, transformers) + Qwen2.5-0.5B 토크나이저 HF 캐시 | test_v6_probes.py |
| `TZ needs time.tzset (POSIX)` | POSIX(`time.tzset` — 자식 프로세스의 `TZ` 환경 변수로 시간대 전환) | test_report_time.py |
| `backslash is a path separator on Windows` | Windows가 아닌 OS(POSIX) | test_qa_in.py |
| `c2pa-python and fixture required` | `provenance`/`dev` extra(c2pa-python) + `scripts/build_c2pa_fixture.py` 픽스처 | test_result_contract.py |
| `c2pa-python not installed` | `provenance`/`dev` extra(c2pa-python) | test_c2pa.py |
| `c2pa-python required for the SDK path` | `provenance`/`dev` extra(c2pa-python) | test_fail_closed.py |
| `cannot create symlinks: {exc}` | 심볼릭 링크를 만들 수 있는 OS·권한(POSIX, Windows 개발자 모드) | test_cli_inputs.py, test_evidence_statement.py |
| `checkpoint is present; auto-discovery would run real inference` | 체크포인트가 없는 환경에서만 실행(가중치를 받은 환경에서는 해당 없음) | test_aide_engine.py |
| `checkpoint is present; unavailable-path assertion does not apply` | 체크포인트가 없는 환경에서만 실행(가중치를 받은 환경에서는 해당 없음) | test_aasist_engine.py, test_aide_engine.py |
| `ctime is the creation time on Windows` | Windows가 아닌 OS(POSIX — `st_ctime_ns`가 inode 변경 시각이라 `touch -r`로 되돌릴 수 없음) | test_file_changed.py |
| `fastapi + httpx not installed` | fastapi, httpx(QA 사이드 venv: `pip install fastapi httpx uvicorn`) | test_cli_operations.py, test_d16_ui_api.py, test_forensic_pdf.py, test_korean_output.py, test_native_path.py, test_non_utf8_names.py, test_path_b64.py, test_reviews.py, test_servers.py, test_standalone_contract.py |
| `fastapi + httpx not installed — API-server leg of QA-OUT-4` | fastapi, httpx(QA 사이드 venv) | test_qa_out.py |
| `fastapi + httpx not installed — stream payload` | fastapi, httpx(QA 사이드 venv) + Pillow | test_json_contract.py |
| `fastapi + httpx not installed — streaming API` | fastapi, httpx(QA 사이드 venv) | test_cli_operations.py |
| `fastapi and pymupdf required` | fastapi, httpx, pymupdf(QA 사이드 venv) | test_evidence_statement.py |
| `fastapi installed; the missing-dep branch does not apply` | fastapi가 없는 환경에서만 실행(CI 기본 venv) | test_servers.py |
| `fastapi/httpx not installed` | fastapi, httpx(QA 사이드 venv) | test_servers.py |
| `fastapi/httpx required` | fastapi, httpx(QA 사이드 venv) | test_servers.py |
| `ffmpeg 없음` | `ffmpeg` 실행 파일(PATH) — 음성 트랙이 있는 시험 영상을 만들고 추출 임시 파일을 관찰; 실제 ffmpeg 자식이 실행 중일 때 종료 신호(R15-1) | test_native_path.py, test_shutdown.py |
| `ffmpeg가 시험 영상을 만들지 못함` | `ffmpeg`(lavfi 입력, mpeg4·aac 인코더 포함 빌드) | test_native_path.py |
| `full-extras 골든은 다른 환경에서 기록됨: {difference}` | 골든 기록(`deepfake_lens/tests/golden_output.json`)의 `full_extras_environment`와 같은 선택 모듈·버전 구성(R16-5 — 0단계 기록 환경은 주 venv: `dev`+`full` extras). 다른 구성에서는 full-extras 골든 비교만 건너뛰고 stdlib 골든·문구 상수 해시는 항상 비교 | test_output_generation.py |
| `full-extras 모듈 없음: {missing}` | `dev`+`full` extras(numpy, opencv, Pillow, scipy, librosa, scikit-learn, c2pa — R16-5 full-extras 골든이 비사진 안내 문구를 포함하는지 확인) | test_output_generation.py |
| `git binary required` | git 실행 파일과 작업 트리(얕은 복제·압축본 아님) | test_traceability_commits.py |
| `git not available` | git 실행 파일 | test_qa_sys.py |
| `golden output recorded on POSIX (path and OS error wording)` | Windows가 아닌 OS(POSIX — 골든 스캔 출력은 POSIX 경로·OS 오류 문구 기준으로 기록, R15-7/R16-5) | test_output_generation.py |
| `jsonschema not installed (dev extra) — _check_object covers the stdlib job` | `dev` extra(jsonschema) | test_json_contract.py |
| `librosa not installed` | `audio`/`full` extra(librosa) | test_audio.py, test_v6_probes.py, test_video_analysis.py |
| `markdown-it-py + linkify-it-py not installed (QA side venv)` | markdown-it-py + linkify-it-py(QA 사이드 venv: `pip install markdown-it-py linkify-it-py`) — GFM식 자동 링크(linkify) 렌더 확인 | test_display_names.py |
| `markdown-it-py not installed (QA side venv)` | markdown-it-py(QA 사이드 venv: `pip install markdown-it-py`) | test_display_names.py |
| `mediapipe installed` | mediapipe가 없는 환경에서만 실행(대체 경로 검사) | test_face.py |
| `mediapipe installed — fallback path not exercised` | mediapipe가 없는 환경에서만 실행(대체 경로 검사) | test_face.py |
| `mediapipe not installed` | `face_mediapipe` 또는 `face_tasks` extra(mediapipe) | test_face.py |
| `node + playwright + Chromium not installed (headless GUI check)` | Node.js + `playwright` npm 패키지 + Chromium(`npx playwright install chromium`) — 헤드리스 GUI 확인 | test_path_b64.py |
| `node is not installed` | Node.js(`node`) — GUI 스크립트 검사 | test_archives.py, test_path_b64.py |
| `node required to run gui.js helpers` | Node.js(`node`) — GUI 스크립트 검사 | test_display_names.py |
| `not a git work tree` | git 작업 트리(얕은 복제·압축본 아님) | test_qa_sys.py |
| `numpy + Pillow needed for the mixed fixture` | `dev` extra(numpy, Pillow) | test_korean_output.py |
| `numpy + opencv required (scene generator, face layer)` | `pixel`/`face` extra(opencv-python, numpy) | test_qa_out.py |
| `numpy needed for the photo-like fixture` | `dev` 또는 `pixel` extra(numpy) | test_korean_output.py |
| `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) | test_aasist_engine.py, test_face_track.py, test_multimodal.py, test_phase2_detection.py, test_phase2_frequency.py, test_v6_probes.py |
| `numpy not installed (photo-like fixture generator)` | `dev` 또는 `pixel` extra(numpy) | test_qa_out.py, test_qa_sys.py |
| `numpy not installed (scene generator)` | `dev` 또는 `pixel` extra(numpy) | test_qa_out.py |
| `numpy unavailable: {exc}` | `dev` 또는 `pixel` extra(numpy) | test_decision.py |
| `numpy/Pillow not installed` | `dev` extra(numpy, Pillow) | test_consolidation.py |
| `numpy/Pillow 미설치` | `dev` extra(numpy, Pillow) | test_image_class.py |
| `numpy/Pillow 미설치 — 사진/비사진 게이트 비활성` | `dev` extra(numpy, Pillow) | test_qa_adv.py |
| `numpy/opencv not installed` | `pixel` extra(opencv-python, numpy) | test_v6_probes.py |
| `numpy/scipy not installed` | `audio`/`full` extra(numpy, scipy) | test_phase1_math.py |
| `official checkpoint not fetched` | `scripts/fetch_aasist.py` 등으로 받은 공식 체크포인트(망분리 감정실에서는 vendor-weights 묶음) | test_aasist_engine.py |
| `onnxruntime not installed` | onnxruntime(선택 런타임 — 모델 프로필의 ONNX 실행; 텔레메트리 차단 확인, R15-2) | test_telemetry.py |
| `opencv not installed` | `pixel`/`video`/`face` extra(opencv-python) | test_face.py, test_model_zoo.py, test_native_path.py |
| `opencv required` | `pixel`/`video`/`face` extra(opencv-python) | test_faceswap_seam.py, test_fail_closed.py |
| `opencv/numpy not installed` | `pixel` extra(opencv-python, numpy) | test_consolidation.py |
| `opencv/numpy required` | `pixel` extra(opencv-python, numpy) | test_standalone_contract.py |
| `photo fixture unavailable: {exc}` | `dev` extra(numpy, Pillow) + `ml` extra(scikit-learn — 실사진 샘플) | test_decision.py |
| `pymupdf (and numpy for the fixture) — the venv_api / extras run covers this` | pymupdf + numpy(QA 사이드 venv) | test_pdf_layout.py |
| `pymupdf not installed` | pymupdf(QA 사이드 venv: `pip install pymupdf`) | test_cli_korean.py, test_evidence_statement.py, test_korean_output.py, test_report_labels_ko.py |
| `pymupdf not installed — the venv_api / extras run covers this` | pymupdf(QA 사이드 venv) | test_forensic_pdf.py, test_pdf_backend.py |
| `pymupdf required for PDF generation` | pymupdf(QA 사이드 venv) | test_display_names.py, test_evidence_statement.py, test_non_utf8_names.py, test_report_time.py |
| `pymupdf required for PDF text` | pymupdf(QA 사이드 venv) | test_office_config.py, test_servers.py |
| `pymupdf — the venv_api / extras run covers this` | pymupdf(QA 사이드 venv) | test_pdf_layout.py |
| `python-markdown not installed (QA side venv)` | python-markdown(QA 사이드 venv: `pip install markdown`) | test_display_names.py |
| `root ignores folder modes; POSIX modes` | root가 아닌 사용자로 실행하는 POSIX | test_cli_inputs.py |
| `sbi/numpy not available` | `dev` 또는 `pixel` extra(numpy) | test_phase2_detection.py |
| `scikit-learn 미설치 — 실사진 샘플 없음` | `ml`/`full` extra(scikit-learn) | test_qa_adv.py |
| `set DEEPFAKE_LENS_MODEL_TESTS=1` | 환경 변수 `DEEPFAKE_LENS_MODEL_TESTS=1` + 실제 가중치(수동 모델 테스트) | test_v6_probes.py |
| `set DEEPFAKE_LENS_TEST_HWP to a real .hwp file` | `hwp` extra(syhwp, olefile) + 환경 변수 `DEEPFAKE_LENS_TEST_HWP`(실제 .hwp 픽스처) | test_documents.py |
| `speechbrain/soundfile not installed` | `speaker` extra(speechbrain, soundfile, torch, torchaudio) | test_v6_probes.py |
| `strace not installed` | `strace` 실행 파일(Linux) — MediaPipe 가져오기 중 자손 프로세스의 execve를 이름과 무관하게 셈(R16-4; audit hook·RUSAGE_CHILDREN 검사는 strace 없이도 실행) | test_mediapipe_import.py |
| `strace를 쓸 수 없음(ptrace 거부)` | ptrace가 허용된 환경(컨테이너의 seccomp·Yama 설정) — R16-4 strace 검사만 건너뜀 | test_mediapipe_import.py |
| `syhwp not installed` | `hwp` extra(syhwp, olefile) | test_documents.py |
| `symbolic links to folders need privileges on Windows` | Windows가 아닌 OS(POSIX) | test_unrecorded_files.py |
| `symlinks not available` | 심볼릭 링크를 만들 수 있는 OS·권한 | test_corpus_manifest.py, test_evidence_statement.py |
| `symlinks not permitted on this platform` | 심볼릭 링크를 만들 수 있는 OS·권한 | test_archives.py |
| `the inpaint check probes for opencv` | `pixel`/`face` extra(opencv-python) | test_core.py |
| `torch is installed; unavailable-path assertion does not apply` | torch가 없는 환경에서만 실행 | test_aasist_engine.py |
| `torch not installed` | `text_lm` extra(torch) | test_aasist_engine.py |
| `torch/torchvision not installed` | torch, torchvision(모델 런타임) | test_model_zoo.py |
| `transformers installed; unavailable-path assertion does not apply` | transformers가 없는 환경에서만 실행 | test_text_detector.py |
| `transformers/torch not installed` | `text_lm` extra(torch, transformers) | test_v6_probes.py |
| `without torch the script exits at the dependency check first` | `text_lm` extra(torch) | test_aasist_engine.py |
| `` {FIXTURE_DIR} missing — run `python scripts/make_benchmark_fixtures.py` `` | `scripts/make_benchmark_fixtures.py`로 만든 픽스처 | test_benchmark_e2e.py |
| `대조군: 이 onnxruntime은 텔레메트리 파일을 만들지 않음` | onnxruntime이 가져올 때 텔레메트리 장치 ID를 쓰는 환경(1.2x Linux 휠, R15-2) — 그렇지 않은 버전에서는 대조군이 성립하지 않아 건너뜀 | test_telemetry.py |
| `대조군: 이 환경의 mediapipe 가져오기는 자식 프로세스도 파일도 만들지 않음` | 그냥 `import mediapipe`가 자식 프로세스(sounddevice→ctypes.util.find_library의 ldconfig/gcc/ld, matplotlib 글꼴 캐시의 fc-list)나 파일(`~/.cache/matplotlib` 등)을 만드는 환경(Linux, R15-8/R16-4) — 그렇지 않은 환경에서는 대조군이 성립하지 않아 건너뜀 | test_mediapipe_import.py |
| `파일 시스템이 UTF-8이 아닌 파일 이름을 허용하지 않음(Windows·macOS)` | UTF-8이 아닌 바이트 파일 이름을 허용하는 파일 시스템(리눅스 ext4·tmpfs 등) | test_corpus_manifest.py, test_native_path.py, test_non_utf8_names.py, test_path_b64.py |

## 환경 의존 기준선 테스트

기존 633개 기준선에 속하는 테스트(정의한 클래스 기준) 중 건너뛰기 경로가 있는 것.
첫 열은 실행되는 클래스 기준 `클래스.메서드`이다.

| 테스트 | 건너뛰기 사유 | 필요 extras·도구 |
| --- | --- | --- |
| `AasistRunnerScriptTest.test_main_reports_missing_checkpoint` | `without torch the script exits at the dependency check first` | `text_lm` extra(torch) |
| `AasistRunnerScriptTest.test_main_reports_unavailable_without_torch` | `torch is installed; unavailable-path assertion does not apply` | torch가 없는 환경에서만 실행 |
| `AasistRunnerScriptTest.test_pad_mirrors_upstream` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `AasistRunnerScriptTest.test_waveform_loader_decodes_pcm_wav` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `AasistRuntimeProfileTest.test_missing_checkpoint_is_graceful` | `checkpoint is present; unavailable-path assertion does not apply` | 체크포인트가 없는 환경에서만 실행(가중치를 받은 환경에서는 해당 없음) |
| `AasistTorchInferenceTest.test_official_checkpoint_scores_synthetic_tone` | `official checkpoint not fetched`; `torch not installed` | `scripts/fetch_aasist.py` 등으로 받은 공식 체크포인트(망분리 감정실에서는 vendor-weights 묶음); `text_lm` extra(torch) |
| `AasistTorchInferenceTest.test_random_init_checkpoint_produces_bounded_score` | `torch not installed` | `text_lm` extra(torch) |
| `AideRuntimeProfileTest.test_missing_checkpoint_is_graceful` | `checkpoint is present; unavailable-path assertion does not apply` | 체크포인트가 없는 환경에서만 실행(가중치를 받은 환경에서는 해당 없음) |
| `ApiServiceContractTest.test_cancel_unknown_job_is_404` | `fastapi + httpx not installed` | fastapi, httpx(QA 사이드 venv: `pip install fastapi httpx uvicorn`) |
| `ApiServiceContractTest.test_check_stream_emits_progress_then_result` | `fastapi + httpx not installed` | fastapi, httpx(QA 사이드 venv: `pip install fastapi httpx uvicorn`) |
| `ApiServiceContractTest.test_check_stream_requires_input` | `fastapi + httpx not installed` | fastapi, httpx(QA 사이드 venv: `pip install fastapi httpx uvicorn`) |
| `ApiServiceContractTest.test_gui_serves_html` | `fastapi + httpx not installed` | fastapi, httpx(QA 사이드 venv: `pip install fastapi httpx uvicorn`) |
| `ApiServiceContractTest.test_health_endpoint_shape` | `fastapi + httpx not installed` | fastapi, httpx(QA 사이드 venv: `pip install fastapi httpx uvicorn`) |
| `ApiServiceContractTest.test_host_allowlist_without_token` | `fastapi + httpx not installed` | fastapi, httpx(QA 사이드 venv: `pip install fastapi httpx uvicorn`) |
| `ApiServiceContractTest.test_root_is_unauthenticated` | `fastapi + httpx not installed` | fastapi, httpx(QA 사이드 venv: `pip install fastapi httpx uvicorn`) |
| `ApiServiceContractTest.test_scan_stream_emits_progress_then_result` | `fastapi + httpx not installed` | fastapi, httpx(QA 사이드 venv: `pip install fastapi httpx uvicorn`) |
| `ApiServiceContractTest.test_scan_stream_requires_directory` | `fastapi + httpx not installed` | fastapi, httpx(QA 사이드 venv: `pip install fastapi httpx uvicorn`) |
| `ApiServiceContractTest.test_token_required_when_configured` | `fastapi + httpx not installed` | fastapi, httpx(QA 사이드 venv: `pip install fastapi httpx uvicorn`) |
| `ApiServiceContractTest.test_unified_api_stats` | `fastapi + httpx not installed` | fastapi, httpx(QA 사이드 venv: `pip install fastapi httpx uvicorn`) |
| `AudioSuccessPathTest.test_pure_tone_pitch_is_detected` | `librosa not installed` | `audio`/`full` extra(librosa) |
| `AudioSuccessPathTest.test_tone_wav_produces_features` | `librosa not installed` | `audio`/`full` extra(librosa) |
| `AvSyncTest.test_aligned_envelopes_report_small_offset` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `AvSyncTest.test_avsync_feeds_multimodal_as_cross_modal` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `AvSyncTest.test_delayed_audio_flags_desync` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `AvSyncTest.test_uncorrelated_envelopes_do_not_score` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `BenchmarkE2ETest.test_marker_outranks_unmarked_images` | `` {FIXTURE_DIR} missing — run `python scripts/make_benchmark_fixtures.py` `` | `scripts/make_benchmark_fixtures.py`로 만든 픽스처 |
| `BenchmarkE2ETest.test_metadata_marker_scores_high` | `` {FIXTURE_DIR} missing — run `python scripts/make_benchmark_fixtures.py` `` | `scripts/make_benchmark_fixtures.py`로 만든 픽스처 |
| `BenchmarkE2ETest.test_pipeline_completes_with_no_failures` | `` {FIXTURE_DIR} missing — run `python scripts/make_benchmark_fixtures.py` `` | `scripts/make_benchmark_fixtures.py`로 만든 픽스처 |
| `BenchmarkE2ETest.test_score_report_written` | `` {FIXTURE_DIR} missing — run `python scripts/make_benchmark_fixtures.py` `` | `scripts/make_benchmark_fixtures.py`로 만든 픽스처 |
| `C2paSdkValidationTest.test_signed_fixture_manifest_is_read_and_reported` | `c2pa-python not installed` | `provenance`/`dev` extra(c2pa-python) |
| `C2paSdkValidationTest.test_unsigned_image_reports_absent_manifest` | `c2pa-python not installed` | `provenance`/`dev` extra(c2pa-python) |
| `C2paSdkValidationTest.test_untrusted_signer_is_reported_not_fabricated` | `c2pa-python not installed` | `provenance`/`dev` extra(c2pa-python) |
| `C2paSdkValidationTest.test_video_container_parses_for_manifest` | `c2pa-python not installed` | `provenance`/`dev` extra(c2pa-python) |
| `ChromPulseTest.test_pulseless_noise_reports_missing_pulse` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `ChromPulseTest.test_synthetic_pulse_detected_near_72_bpm` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `CompressionForensicsTest.test_double_compression_returns_measurement` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `CompressionForensicsTest.test_ela_metrics_on_uniform_surface` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `CompressionForensicsTest.test_small_image_degrades` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `CopyMoveKeypointTest.test_clean_texture_not_flagged` | `numpy/opencv not installed` | `pixel` extra(opencv-python, numpy) |
| `CopyMoveKeypointTest.test_random_noise_not_flagged` | `numpy/opencv not installed` | `pixel` extra(opencv-python, numpy) |
| `CopyMoveKeypointTest.test_scaled_clone_flagged` | `numpy/opencv not installed` | `pixel` extra(opencv-python, numpy) |
| `CopyMoveKeypointTest.test_translated_clone_flagged` | `numpy/opencv not installed` | `pixel` extra(opencv-python, numpy) |
| `CopyMoveTest.test_clean_noise_not_flagged` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `CopyMoveTest.test_forged_region_flagged` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `CopyMoveTest.test_repetitive_texture_suppressed` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `DefaultEngineDiscoveryTest.test_scan_auto_discovers_profile_and_degrades` | `checkpoint is present; auto-discovery would run real inference` | 체크포인트가 없는 환경에서만 실행(가중치를 받은 환경에서는 해당 없음) |
| `ElaExpertTest.test_spliced_region_scores_above_control` | `numpy/Pillow not installed` | `dev` extra(numpy, Pillow) |
| `EvidenceStatementTest.test_api_report_evidence_statement_format` | `fastapi and pymupdf required` | fastapi, httpx, pymupdf(QA 사이드 venv) |
| `EvidenceStatementTest.test_cli_evidence_statement_command` | `pymupdf required for PDF generation` | pymupdf(QA 사이드 venv) |
| `EvidenceStatementTest.test_pdf_generation` | `pymupdf required for PDF generation` | pymupdf(QA 사이드 venv) |
| `EvidenceStatementTest.test_pdf_pagination_stress_60_entries` | `pymupdf not installed` | pymupdf(QA 사이드 venv: `pip install pymupdf`) |
| `EvidenceStatementTest.test_pdf_purpose_column_renders_content` | `pymupdf required for PDF generation` | pymupdf(QA 사이드 venv) |
| `FaceAnalysisTest.test_bundled_cascade_is_packaged` | `opencv not installed` | `pixel`/`video`/`face` extra(opencv-python) |
| `FaceAnalysisTest.test_circular_hue_same_red_family_does_not_fire` | `opencv not installed` | `pixel`/`video`/`face` extra(opencv-python) |
| `FaceAnalysisTest.test_detect_faces_survives_broken_cascade` | `opencv not installed` | `pixel`/`video`/`face` extra(opencv-python) |
| `FaceAnalysisTest.test_face_landmarks_falls_back_to_labelled_box_estimate` | `mediapipe installed — fallback path not exercised` | mediapipe가 없는 환경에서만 실행(대체 경로 검사) |
| `FaceAnalysisTest.test_mediapipe_landmarks_degrades_cleanly_on_blank_crop` | `mediapipe not installed` | `face_mediapipe` 또는 `face_tasks` extra(mediapipe) |
| `FaceAnalysisTest.test_mediapipe_landmarks_none_without_package` | `mediapipe installed` | mediapipe가 없는 환경에서만 실행(대체 경로 검사) |
| `FaceAnalysisTest.test_unicode_path_image_is_readable` | `opencv not installed` | `pixel`/`video`/`face` extra(opencv-python) |
| `FaceSwapSeamTest.test_blank_image_reports_no_faces` | `opencv required` | `pixel`/`video`/`face` extra(opencv-python) |
| `FaceSwapSeamTest.test_cli_faceswap_seam_json_output` | `opencv required` | `pixel`/`video`/`face` extra(opencv-python) |
| `FaceSwapSeamTest.test_core_deep_image_layers_integration` | `opencv required` | `pixel`/`video`/`face` extra(opencv-python) |
| `FaceSwapSeamTest.test_synthetic_faceswap_with_simulated_seam` | `opencv required` | `pixel`/`video`/`face` extra(opencv-python) |
| `FaceSwapSeamTest.test_undersized_faces_report_unknown_not_low` | `opencv required` | `pixel`/`video`/`face` extra(opencv-python) |
| `ForensicPdfApiEndpointTest.test_api_report_pdf_format_query` | `fastapi + httpx not installed`; `pymupdf not installed — the venv_api / extras run covers this` | fastapi, httpx(QA 사이드 venv: `pip install fastapi httpx uvicorn`); pymupdf(QA 사이드 venv) |
| `ForensicPdfReportTest.test_redact_paths_in_forensic_pdf` | `pymupdf not installed — the venv_api / extras run covers this` | pymupdf(QA 사이드 venv) |
| `ForensicPdfReportTest.test_write_forensic_pdf_generates_valid_pdf` | `pymupdf not installed — the venv_api / extras run covers this` | pymupdf(QA 사이드 venv) |
| `FormantEstimationTest.test_silence_returns_empty` | `numpy/scipy not installed` | `audio`/`full` extra(numpy, scipy) |
| `FormantEstimationTest.test_synthetic_tones_yield_formants` | `numpy/scipy not installed` | `audio`/`full` extra(numpy, scipy) |
| `FrequencyExpertIntegrationTest.test_expert_present_in_ensemble` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `FrequencyExpertIntegrationTest.test_expert_unavailable_without_numpy` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `HwpExtractionTest.test_real_hwp_extracts_when_fixture_present` | `set DEEPFAKE_LENS_TEST_HWP to a real .hwp file`; `syhwp not installed` | `hwp` extra(syhwp, olefile); `hwp` extra(syhwp, olefile) + 환경 변수 `DEEPFAKE_LENS_TEST_HWP`(실제 .hwp 픽스처) |
| `MetricMathTest.test_box_smoothness_detects_jump` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `MetricMathTest.test_box_smoothness_stable` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `MetricMathTest.test_consecutive_cosine_identical` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `MetricMathTest.test_consecutive_cosine_orthogonal` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `MetricMathTest.test_landmark_jitter_normalized_by_box` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `MetricMathTest.test_landmark_jitter_zero_when_static` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `MetricMathTest.test_score_bounds` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `MultiProfileAggregationTest.test_degraded_weight_applies_on_low_quality_jpeg` | `Pillow not installed` | `dev` extra(Pillow) |
| `MultiProfileAggregationTest.test_low_resolution_flagged_as_unreliable` | `Pillow not installed` | `dev` extra(Pillow) |
| `MultiRoiRppgTest.test_coherent_rois_report_high_phase_coherence` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `MultiRoiRppgTest.test_incoherent_rois_flag_low_phase_coherence` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `MultiRoiRppgTest.test_missing_pulse_skips_coherence_measurement` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `MultiRoiRppgTest.test_single_roi_path_keeps_compat_fields` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `PreScreenTierTest.test_real_image_result_is_labelled_pre_screen` | `opencv/numpy not installed` | `pixel` extra(opencv-python, numpy) |
| `PrnuTest.test_fewer_than_three_references_is_rejected` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `PrnuTest.test_same_camera_correlates_other_camera_does_not` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `ReviewApiEndpointTest.test_get_and_post_review_query_param` | `fastapi + httpx not installed` | fastapi, httpx(QA 사이드 venv: `pip install fastapi httpx uvicorn`) |
| `ReviewApiEndpointTest.test_list_all_reviews_endpoint` | `fastapi + httpx not installed` | fastapi, httpx(QA 사이드 venv: `pip install fastapi httpx uvicorn`) |
| `ReviewApiEndpointTest.test_put_and_get_artifact_review` | `fastapi + httpx not installed` | fastapi, httpx(QA 사이드 venv: `pip install fastapi httpx uvicorn`) |
| `SbiDistortionTest.test_jpeg_simulation_quality_ordering` | `sbi/numpy not available` | `dev` 또는 `pixel` extra(numpy) |
| `SbiDistortionTest.test_resize_bilinear_interpolates_linear_ramp` | `sbi/numpy not available` | `dev` 또는 `pixel` extra(numpy) |
| `SbiDistortionTest.test_self_blended_image_is_deterministic_and_masked` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `ServiceContractTest.test_create_app_import_guard` | `fastapi installed; the missing-dep branch does not apply` | fastapi가 없는 환경에서만 실행(CI 기본 venv) |
| `SpeakerComparisonTest.test_ecapa_self_comparison_when_available` | `librosa not installed`; `set DEEPFAKE_LENS_MODEL_TESTS=1`; `speechbrain/soundfile not installed` | `audio`/`full` extra(librosa); `speaker` extra(speechbrain, soundfile, torch, torchaudio); 환경 변수 `DEEPFAKE_LENS_MODEL_TESTS=1` + 실제 가중치(수동 모델 테스트) |
| `SpeakerComparisonTest.test_identical_audio_is_same_speaker` | `librosa not installed` | `audio`/`full` extra(librosa) |
| `SpeakerComparisonTest.test_missing_file_is_graceful` | `librosa not installed` | `audio`/`full` extra(librosa) |
| `SpeakerComparisonTest.test_very_different_audio_scores_lower` | `librosa not installed` | `audio`/`full` extra(librosa) |
| `SpectrumSlopeTest.test_bilinear_upsample_raises_npr_consistency` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `SpectrumSlopeTest.test_checkerboard_injection_creates_spectral_spikes` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `SpectrumSlopeTest.test_dct_highfreq_zero_for_constant_image` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `SpectrumSlopeTest.test_natural_image_slope_near_two` | `numpy not installed` | `dev` 또는 `pixel`/`full` extra(numpy) |
| `SynthIDWatermarkTest.test_wrong_key_reports_no_signal` | `Qwen2.5-0.5B tokenizer not cached`; `set DEEPFAKE_LENS_MODEL_TESTS=1`; `transformers/torch not installed` | `text_lm` extra(torch, transformers); `text_lm` extra(torch, transformers) + Qwen2.5-0.5B 토크나이저 HF 캐시; 환경 변수 `DEEPFAKE_LENS_MODEL_TESTS=1` + 실제 가중치(수동 모델 테스트) |
| `TextDetectorProfileTest.test_missing_transformers_degrades_gracefully` | `transformers installed; unavailable-path assertion does not apply` | transformers가 없는 환경에서만 실행 |
| `ThresholdProfileTest.test_provisional_profile_adds_limitation` | `opencv required` | `pixel`/`video`/`face` extra(opencv-python) |
| `TorchvisionHeadTest.test_efficientnet_classifier_head_rewired_and_loads` | `torch/torchvision not installed` | torch, torchvision(모델 런타임) |
| `TorchvisionHeadTest.test_resnet_fc_head_still_rewired` | `torch/torchvision not installed` | torch, torchvision(모델 런타임) |
| `VideoFramesRuntimeTest.test_frames_scored_through_inner_profile` | `opencv not installed` | `pixel`/`video`/`face` extra(opencv-python) |
