# 0단계 적합성 표 (CONFORMANCE)

<!-- scripts/qa_phase0.py가 생성 — 손으로 고치지 말 것 -->

- 검증 커밋: `f7b9cf7c709b3c655f4c1a0ed6d95533fe7d7b03` — 변경 없는 작업 트리에서 실행. 이 표는 이 커밋의 직계 자식 커밋에 단독으로 담긴다 (`python scripts/qa_phase0.py --verify-record`로 확인)
- 생성 일시(UTC): 2026-10-09T06:04:37Z
- 도구 버전: deepfake-lens 0.1.0
- 환경: Python 3.13.16 / Linux-6.18.44-fc-v80-x86_64-with-glibc2.39 / ffmpeg 있음
- 설치된 선택 패키지: numpy, PIL, cv2, scipy, sklearn, fastapi, httpx, uvicorn, mediapipe, pymupdf, fitz
- 없는 선택 패키지(해당 테스트는 건너뜀): librosa, soundfile, c2pa, torch, transformers, speechbrain, py7zr, rarfile
- 실행 범위: 전체 단위 테스트 스위트 — 1200개 실행, 실패 0개, 건너뜀 30개
- 신경망 가중치: 없음(모든 프로필 supported:false, 모델 경로는 가짜 프로필 + monkeypatch로 검증)

**요약: 20 통과 / 0 실패 / 4 수동 / 12 1단계**

집계 단위: 고유 QA ID 1건씩. QA ID가 없는 1단계 요구사항(R-IMG-1, R-VID-*, R-AUD-*, R-DOC-*)과 0단계 범위 밖 ID(QA-MOD-*, QA-ADV-4–6)는 각각 1건으로 1단계에 센다. 아래 표는 요구사항×QA ID 쌍마다 한 행이라 같은 QA ID가 여러 행에 나올 수 있다.

## 요구사항 → 갭 → QA

| 요구사항 ID | 갭 ID | QA ID | 결과 | 로그 경로 |
| --- | --- | --- | --- | --- |
| R-IN-1 | G31 | QA-IN-1 | 통과 | `build/qa-logs/QA-IN-1.log` |
| R-IN-1 | G31 | QA-SYS-7 | 통과 | `build/qa-logs/QA-SYS-7.log` |
| R-IN-2 | — | QA-IN-1 | 통과 | `build/qa-logs/QA-IN-1.log` |
| R-IN-3 | G32 | QA-IN-2 | 통과 | `build/qa-logs/QA-IN-2.log` |
| R-IN-4 | — | QA-IN-3 | 수동 | `docs/QA-MANUAL.md#qa-in-3` |
| R-OUT-1 | G5, G6 | QA-OUT-1 | 통과 | `build/qa-logs/QA-OUT-1.log` |
| R-OUT-2 | G5 | QA-OUT-1 | 통과 | `build/qa-logs/QA-OUT-1.log` |
| R-OUT-2 | G5 | QA-OUT-5 (구조 검사(0단계에 보정 모델 없음)) | 통과 | `build/qa-logs/QA-OUT-5.log` |
| R-OUT-3 | G12 | QA-OUT-3 | 통과 | `build/qa-logs/QA-OUT-3.log` |
| R-OUT-4 | G1 | QA-OUT-2 | 통과 | `build/qa-logs/QA-OUT-2.log` |
| R-OUT-5 | G5, G28 | QA-OUT-5 (구조 검사(0단계에 보정 모델 없음)) | 통과 | `build/qa-logs/QA-OUT-5.log` |
| R-OUT-6 | G7, G8 | QA-OUT-4 | 통과 | `build/qa-logs/QA-OUT-4.log` |
| R-IMG-1 | G14 | — | 1단계 | — (메신저 재압축 지문) |
| R-IMG-2 | G3, G13 | QA-ADV-1 | 통과 | `build/qa-logs/QA-ADV-1.log` |
| R-IMG-2 | G3, G13 | QA-ADV-2 | 통과 | `build/qa-logs/QA-ADV-2.log` |
| R-IMG-3 | G16 | QA-IMG-2 | 1단계 | — |
| R-IMG-4 | G15 | QA-IMG-1 | 1단계 | — |
| R-IMG-4 | G15 | QA-IMG-3 | 1단계 | — |
| R-IMG-5 | G17 | QA-ADV-1 | 통과 | `build/qa-logs/QA-ADV-1.log` |
| R-VID-* | G18 | — | 1단계 | — (영상 코퍼스·측정) |
| R-AUD-* | G19, G20, G21 | — | 1단계 | — (음성 코퍼스·측정, 편집 흔적) |
| R-DOC-* | G22, G23 | — | 1단계 | — (문서 타임라인/이력, PDF 서명) |
| R-TXT-1 | G24 | QA-OUT-6 | 통과 | `build/qa-logs/QA-OUT-6.log` |
| R-TXT-2 | G4 | QA-ADV-3 | 통과 | `build/qa-logs/QA-ADV-3.log` |
| R-TXT-3 | G2, G25 | QA-SYS-9 (게이트) | 통과 | `build/qa-logs/QA-SYS-9.log` |
| R-TXT-3 | G2, G25 | QA-TXT-1 | 1단계 | — |
| R-SYS-1 | G9, G10 | QA-SYS-1 | 통과 | `build/qa-logs/QA-SYS-1.log` |
| R-SYS-1 | G9, G10 | QA-SYS-2 | 통과 | `build/qa-logs/QA-SYS-2.log` |
| R-SYS-2 | G29 | QA-SYS-3 | 통과 | `build/qa-logs/QA-SYS-3.log` |
| R-SYS-2 | G29 | QA-SYS-4 | 수동 | `docs/QA-MANUAL.md#qa-sys-4` |
| R-SYS-3 | G11, G34 | QA-IN-4 | 통과 | `build/qa-logs/QA-IN-4.log` |
| R-SYS-3 | G11, G34 | QA-IN-5 | 통과 | `build/qa-logs/QA-IN-5.log` |
| R-SYS-3 | G11, G34 | QA-SYS-5 | 수동 | `docs/QA-MANUAL.md#qa-sys-5` |
| R-SYS-4 | G30 | QA-SYS-6 | 통과 | `build/qa-logs/QA-SYS-6.log` |
| R-SYS-5 | — | QA-SYS-8 | 수동 | `docs/QA-MANUAL.md#qa-sys-8` |
| R-SYS-6 | — | QA-SYS-4 | 수동 | `docs/QA-MANUAL.md#qa-sys-4` |
| R-QA-1 | G26, G27 | QA-SYS-9 | 통과 | `build/qa-logs/QA-SYS-9.log` |
| R-QA-2 | G26 | QA-SYS-9 | 통과 | `build/qa-logs/QA-SYS-9.log` |
| R-QA-3 | G28 | QA-SYS-9 | 통과 | `build/qa-logs/QA-SYS-9.log` |
| R-QA-3 | G28 | QA-SYS-10 | 통과 | `build/qa-logs/QA-SYS-10.log` |
| R-QA-4 | G3, G4, G13 | QA-ADV-1 | 통과 | `build/qa-logs/QA-ADV-1.log` |
| R-QA-4 | G3, G4, G13 | QA-ADV-2 | 통과 | `build/qa-logs/QA-ADV-2.log` |
| R-QA-4 | G3, G4, G13 | QA-ADV-3 | 통과 | `build/qa-logs/QA-ADV-3.log` |
| — | — | QA-MOD-* | 1단계 | — (0단계 범위 밖) |
| — | — | QA-ADV-4 | 1단계 | — (0단계 범위 밖) |
| — | — | QA-ADV-5 | 1단계 | — (0단계 범위 밖) |
| — | — | QA-ADV-6 | 1단계 | — (0단계 범위 밖) |

## 자동 QA 상세

정본 테스트 = docstring 첫 줄이 "<QA ID>: <통과 기준 원문>"인 테스트(QA ID마다 정확히 1개). QA ID의 결과는 정본 테스트와 같은 QA ID로 태그된 모든 테스트의 결과를 합친 것이다.

| QA ID | 정본 테스트 | 태그된 테스트(실패/건너뜀/전체) | 결과 | 비고 |
| --- | --- | --- | --- | --- |
| QA-IN-1 | `deepfake_lens.tests.qa.test_qa_in.QaIn1ReadOnlyEvidenceTest.test_full_scan_leaves_read_only_folder_untouched` | 0/0/2 | 통과 | — |
| QA-IN-2 | `deepfake_lens.tests.qa.test_qa_in.QaIn2DeterministicRescanTest.test_three_scans_across_restart_and_rename_are_byte_identical` | 0/0/3 | 통과 | — |
| QA-IN-4 | `deepfake_lens.tests.qa.test_qa_in.QaIn4ContentKeyedCacheTest.test_same_size_edit_with_restored_mtime_is_reanalyzed` | 0/0/6 | 통과 | — |
| QA-IN-5 | `deepfake_lens.tests.qa.test_qa_in.QaIn5DamagedInputsTest.test_each_damaged_input_is_undetermined_unsupported_or_failed_with_reason` | 0/0/7 | 통과 | — |
| QA-OUT-1 | `deepfake_lens.tests.qa.test_qa_out.QaOut1NoWeightsTest.test_hundred_photos_without_weights` | 0/0/2 | 통과 | — |
| QA-OUT-2 | `deepfake_lens.tests.qa.test_qa_out.QaOut2InferenceExceptionTest.test_injected_inference_exceptions_are_failed_and_undetermined` | 0/0/7 | 통과 | — |
| QA-OUT-3 | `deepfake_lens.tests.qa.test_qa_out.QaOut3NoFaceTest.test_no_face_profile_and_low_light_record_face_check_not_run` | 0/0/4 | 통과 | — |
| QA-OUT-4 | `deepfake_lens.tests.qa.test_qa_out.QaOut4SameResultEverywhereTest.test_cli_gui_api_identical_on_benchmark_fixtures` | 0/0/8 | 통과 | — |
| QA-OUT-5 | `deepfake_lens.tests.qa.test_qa_out.QaOut5ProbabilityProvenanceTest.test_synthetic_calibrated_probability_carries_provenance` | 0/0/4 | 통과 | — |
| QA-OUT-6 | `deepfake_lens.tests.qa.test_qa_out.QaOut6TextIsReferenceTest.test_text_corpora_are_reference_grade` | 0/0/1 | 통과 | — |
| QA-ADV-1 | `deepfake_lens.tests.qa.test_qa_adv.AdversarialGateTest.test_qa_adv_1_pattern_images_are_not_photos` | 0/0/7 | 통과 | — |
| QA-ADV-2 | `deepfake_lens.tests.qa.test_qa_adv.AdversarialGateTest.test_qa_adv_2_screenshots_are_classified_and_gated` | 0/0/1 | 통과 | — |
| QA-ADV-3 | `deepfake_lens.tests.qa.test_qa_adv.HumanTextsAboutAiTest.test_keyword_is_listed_but_never_concludes` | 0/0/5 | 통과 | — |
| QA-SYS-1 | `deepfake_lens.tests.qa.test_qa_sys.QaSys1PinTamperTest.test_sha256_changed_by_one_char_refuses_load` | 0/0/4 | 통과 | — |
| QA-SYS-2 | `deepfake_lens.tests.qa.test_qa_sys.QaSys2CheckpointSwapTest.test_checkpoint_replaced_by_another_file_refuses_load` | 0/0/4 | 통과 | — |
| QA-SYS-3 | `deepfake_lens.tests.qa.test_qa_sys.QaSys3DoctorMatchesScanTest.test_deleted_model_is_miss_and_runnable_summary_matches_scan` | 0/0/5 | 통과 | — |
| QA-SYS-6 | `deepfake_lens.tests.qa.test_qa_sys.QaSys6SignatureCoversWholeReportTest.test_named_fields_are_inside_the_signature` | 0/0/9 | 통과 | — |
| QA-SYS-7 | `deepfake_lens.tests.qa.test_qa_sys.QaSys7ReadRootConfinementTest.test_unregistered_scan_and_outside_heatmap_report_are_403_with_no_bytes` | 0/0/14 | 통과 | — |
| QA-SYS-9 | `deepfake_lens.tests.qa.test_qa_sys.MeasurementGateTest.test_qa_sys_9_unmeasured_profile_fails_ci` | 0/0/8 | 통과 | — |
| QA-SYS-10 | `deepfake_lens.tests.qa.test_qa_sys.QaSys10TestInventoryTest.test_inventory_floor_and_documented_deletions` | 0/0/17 | 통과 | 전체 스위트 1200개 실행, 실패 0건 |

## 수동·1단계

- QA-IN-3 (네트워크 차단 상태 검사): 수동 — 체크리스트 `docs/QA-MANUAL.md#qa-in-3`
- QA-SYS-4 (깨끗한 Windows 11 설치): 수동 — 체크리스트 `docs/QA-MANUAL.md#qa-sys-4`
- QA-SYS-5 (500개 파일 검사 중 강제 종료 후 재개): 수동 — 체크리스트 `docs/QA-MANUAL.md#qa-sys-5`
- QA-SYS-8 (CPU 전용 처리 시간): 수동 — 체크리스트 `docs/QA-MANUAL.md#qa-sys-8`
- QA-IMG-1 (R-IMG-4 측정(1단계 코퍼스, G15)): 1단계
- QA-IMG-2 (R-IMG-3 측정(1단계 코퍼스, G16)): 1단계
- QA-IMG-3 (R-IMG-4 측정(1단계 코퍼스, G15)): 1단계
- QA-TXT-1 (R-TXT-3 텍스트 탐지 측정(1단계 코퍼스, G25)): 1단계
- QA-MOD-*, QA-ADV-4, QA-ADV-5, QA-ADV-6: 1단계 — 명세 WP-J: 0단계 범위 밖. 요구사항 대응표에는 없다.
- G33: umm-maybe 가중치 이중 프로필 + _aggregate_profile_results 덮어쓰기 버그 — QA 시나리오 없음, 회귀 테스트 tests/test_model_zoo.py의 test_gated_member_takes_no_part_in_spread_or_agreement, test_all_members_gated_is_not_available로 고정
