# 0단계 테스트 삭제·이름 변경 기록 (QA-SYS-10)

QA-SYS-10 통과 기준: "기존 633개 테스트 중 유지 대상 전부 통과. 삭제된 테스트는 삭제
이유가 커밋 메시지에 기록."

- R10-3 (검증 라운드 10): 이 문서는 커밋을 **제목(subject)** 으로 인용한다. 브랜치
  히스토리가 여러 번 다시 쓰여 해시가 바뀌었으므로(예: WP-C는 리라이트 전 `2b39313`,
  지금 `1cb8da8`) 해시는 참고로만 적는다. QA-SYS-10 테스트는 제목으로
  `git log --fixed-strings --grep=<제목> HEAD`를 실행해 HEAD의 조상에서 그 제목의 커밋을
  찾고, 못 찾으면(얕은 클론 포함) 건너뛰지 않고 실패한다.
- 기준선은 `deepfake_lens/tests/qa/test_inventory_baseline.json`에 고정되어 있다:
  0단계 시작 전 커밋 `feat(calibration): first MEASURED thresholds — real faceswap corpus via inswapper`
  (해시 참고: `dad9730`)의 633개, 그리고 WP-C 삭제 직전 커밋
  `fix(text): keywords are lexical evidence only, never points (WP-E)`(WP-A, WP-B, WP-E
  반영; 해시 참고: 리라이트 전 `35cbc8e`, 지금 `7f4ee2a`)의 739개. 집계 규칙은 `tests/qa/traceability.source_inventory`
  (모듈 최상위 클래스에 직접 정의된 `test*` 메서드, AST).
- `tests/qa/test_qa_sys.py`의 QA-SYS-10 테스트가 확인하는 것: 현재 테스트 수가 739개
  아래로 내려가지 않음, 기준선의 모든 테스트가 현재 있거나 아래 표에 있음, 표의 삭제
  항목은 실제로 없고 기준선에 있던 이름임, 삭제 이유가 해당 커밋 메시지에 있음(git 이력이
  있을 때). "유지 대상 전부 통과"는 `scripts/qa_phase0.py`가 전체 스위트를 돌려 함께
  판정한다.
- 비교 단위는 `Class.test_method`이다. 파일만 옮긴 테스트(아래 "파일 이동")는 삭제가 아니다.
  W2(검증 라운드 4)에서 `tests/qa/`는 명세 WP-J가 명명한 4개 파일(`test_qa_in.py`,
  `test_qa_out.py`, `test_qa_adv.py`, `test_qa_sys.py`)로 합쳐졌다 — 클래스 단위 이동이며
  삭제된 테스트는 없다.

조사 방법: `git log --format='%h %s' dad9730..HEAD -i --grep='delet'`는 커밋 8개를 후보로
내지만("No deletions", "deleted checkpoint" 같은 문구 포함), 커밋마다
기준선 인벤토리를 비교하면 테스트가 실제로 사라진 커밋은 WP-C 커밋
`fix(models): pinned, gated model zoo; rejected profiles removed (WP-C)` 하나다(`--grep='Deleted'`도 이 커밋만 맞는다). 커밋별 테스트 수(제목 — 해시는
리라이트 전 → 지금, 참고):

| 커밋 제목 | 해시(참고) | 테스트 수 |
| --- | --- | --- |
| `feat(calibration): first MEASURED thresholds — real faceswap corpus via inswapper` | `dad9730` | 633 |
| `feat(contract): result contract v2 — three verdicts, classified evidence, coverage (WP-A)` | `50486d5` → `01faad0` | 709 |
| `fix(fail-closed): crashed checks are "failed", never clean; lint gate (WP-B)` | `9baa2ee` → `ad76dfd` | 735 |
| `fix(text): keywords are lexical evidence only, never points (WP-E)` | `35cbc8e` → `7f4ee2a` | 739 |
| `fix(models): pinned, gated model zoo; rejected profiles removed (WP-C)` | `2b39313` → `1cb8da8` | 764(10개 사라지고 35개 추가) |
| `merge: WP-F/H unified entry point and ops` | `a6bffc4` → `1f74988` | 896 |

## 삭제

"커밋" 열은 커밋 제목(백틱 안)이고 괄호 안 해시는 참고다(R10-3).

| 테스트 | 커밋 | 이유(커밋 메시지) | 의도를 이어받는 테스트 |
| --- | --- | --- | --- |
| `CommittedProfilesTest.test_dire_is_documented_placeholder` | `fix(models): pinned, gated model zoo; rejected profiles removed (WP-C)` (WP-C, G2/G9; 해시 참고: `2b39313` → `1cb8da8`) | 단언 대상인 커밋된 프로필(dire-runtime.json)이 삭제됨 | `test_removed_profiles_are_gone_and_documented` |
| `CommittedProfilesTest.test_univfd_profile_records_clip_contract` | `fix(models): pinned, gated model zoo; rejected profiles removed (WP-C)` (해시 참고: `1cb8da8`) | univfd-runtime.json 삭제 | `test_removed_profiles_are_gone_and_documented` |
| `CommittedProfilesTest.test_cnndetection_profile_records_torchvision_contract` | `fix(models): pinned, gated model zoo; rejected profiles removed (WP-C)` (해시 참고: `1cb8da8`) | cnndetection-runtime.json 삭제 | `test_removed_profiles_are_gone_and_documented` |
| `CommittedProfilesTest.test_face_vit_profile_records_hub_contract` | `fix(models): pinned, gated model zoo; rejected profiles removed (WP-C)` (해시 참고: `1cb8da8`) | face-manipulation-vit*.json 삭제 | `test_removed_profiles_are_gone_and_documented` |
| `CommittedProfilesTest.test_rejected_ffpp_profiles_are_disabled` | `fix(models): pinned, gated model zoo; rejected profiles removed (WP-C)` (해시 참고: `1cb8da8`) | faceswap-ffpp*.json 삭제 | `test_every_profile_is_gated_and_carries_an_empty_pin` |
| `CommittedProfilesTest.test_rejected_ffpp_profile_degrades_with_reason` | `fix(models): pinned, gated model zoo; rejected profiles removed (WP-C)` (해시 참고: `1cb8da8`) | faceswap-ffpp*.json 삭제 | `test_gated_profile_degrades_with_reason` |
| `CommittedProfilesTest.test_disabled_face_vit_profile_reports_reason` | `fix(models): pinned, gated model zoo; rejected profiles removed (WP-C)` (해시 참고: `1cb8da8`) | face-manipulation-vit*.json 삭제 | `test_gated_profile_degrades_with_reason` |
| `CommittedProfilesTest.test_qwen_ppl_profile_records_ppl_contract` | `fix(models): pinned, gated model zoo; rejected profiles removed (WP-C)` (해시 참고: `1cb8da8`) | qwen-ppl-runtime.json 삭제 (causal-lm-ppl 퇴화 테스트는 임시 프로필로 이동) | `test_removed_profiles_are_gone_and_documented` |
| `CommittedProfilesTest.test_binoculars_profile_records_contract` | `fix(models): pinned, gated model zoo; rejected profiles removed (WP-C)` (해시 참고: `1cb8da8`) | binoculars-runtime.json 삭제 (binoculars 퇴화 테스트는 임시 프로필로 이동) | `test_removed_profiles_are_gone_and_documented` |

모두 `deepfake_lens/tests/test_model_zoo.py`에 있던 테스트다. 커밋 메시지의 해당 단락:
"Deleted (the committed profiles they asserted no longer exist): …  Their intent is
covered by test_removed_profiles_are_gone_and_documented,
test_gated_profile_degrades_with_reason and
test_every_profile_is_gated_and_carries_an_empty_pin."

## 이름 변경

| 테스트 | 커밋 | 이유 | 새 이름 |
| --- | --- | --- | --- |
| `Rule4CalibratedStatisticalTest.test_threshold_for_other_calibration_falls_back_to_default` | G8 (검증 라운드 5) | 결함을 고정하던 테스트: 프로필 임계값이 없는 calibration_id가 0.5 기본값으로 규칙 4를 발동한다고 기대했다(기본값 제거). 같은 입력에서 판단 불가를 기대하도록 고치고 이름을 바꿨다. 커밋 메시지에 옛 이름 기재 | `Rule4CalibratedStatisticalTest.test_threshold_for_other_calibration_does_not_apply` |
| `ScanPayloadValidationTest.test_max_files_floors_at_one` | Y8 (검증 라운드 7) | 결함을 고정하던 테스트: `/api/scan?max_files=0`이 1로 올려져 검사가 돈다고 기대했다. 1 미만은 이제 400(InvalidOption)이므로 거부를 확인하도록 고치고 이름을 바꿨다. 커밋 메시지에 옛 이름 기재 | `ScanPayloadValidationTest.test_zero_or_negative_limits_are_refused` |
| `JsonOutParentCreationTest.test_scan_json_out_creates_missing_parents` | Z5 (검증 라운드 8) | 결함을 고정하던 테스트: `scan --json-out`이 없는 상위 폴더를 만들어 저장한다고 기대했다. 없는 출력 폴더는 이제 검사 전 사용 오류(exit 2, `오류: 출력 폴더가 없습니다: …`, 폴더를 만들지 않음)이므로 거부와 기존 폴더 저장을 확인하도록 고치고 이름을 바꿨다. 커밋 메시지에 옛 이름 기재 | `JsonOutParentCreationTest.test_scan_json_out_refuses_missing_parents` |
| `VideoFramesRuntimeTest.test_committed_aide_frames_profile_matches_video_modality` | `fix(models): pinned, gated model zoo; rejected profiles removed (WP-C)` (해시 참고: `1cb8da8`) | aide-frames-runtime.json 삭제. 커밋 메시지: "frames-profile contract retargeted to the remaining video-frames profiles" | `VideoFramesRuntimeTest.test_committed_frames_profiles_match_video_modality` |
| `Rule2DeterministicSyntheticTest.test_beats_strong_authenticity` | `fix(decision): P0R-10 rule 2 no longer outvotes strong authenticity — strong reproducible-record evidence in both directions is a conflict, undetermined (P0R-10; Gaps: 신규)` | 없어진 설계를 고정하던 테스트(사용자 결정 2026-10-17): 강한 생성 근거가 강한 원본성 근거를 이긴다고 기대했다. 둘이 함께 있으면 이제 근거 충돌로 판단 불가다 | `Rule2DeterministicSyntheticTest.test_strong_authenticity_makes_it_a_conflict` |
| `MetadataEvidenceMappingTest.test_header_byte_string_is_only_moderate` | `fix(metadata): P0R-11 a generator brand word is moderate and counts only in generator-specific fields (Software, parameters, prompt/workflow, xmp:CreatorTool) — never exif.Model, LensModel, Credit or dc:creator (P0R-11; Gaps: 신규)` | 없어진 설계를 고정하던 테스트(사용자 결정 2026-10-17): 헤더 바이트 문자열의 생성기 이름을 보통 강도의 생성 근거로 기대했다. 생성 도구 필드가 아닌 곳의 이름은 이제 중립 사실이다 | `MetadataEvidenceMappingTest.test_header_byte_string_is_only_a_fact` |

## 파일 이동 (삭제 아님)

| 테스트 파일 | 커밋 | 새 위치 |
| --- | --- | --- |
| `deepfake_lens/tests/test_qa_adv3_keywords.py` (`HumanTextsAboutAiTest`, 4개) | WP-J | `deepfake_lens/tests/qa/test_qa_adv3.py` |
| `deepfake_lens/tests/qa/test_qa_adv3.py` (`HumanTextsAboutAiTest`, 클래스·테스트·docstring 그대로) | W2 (검증 라운드 4) | `deepfake_lens/tests/qa/test_qa_adv.py` |
| `deepfake_lens/tests/qa/test_qa_in_robustness.py` (`QaIn5DamagedInputsTest`, 클래스·테스트·docstring 그대로) | W2 (검증 라운드 4) | `deepfake_lens/tests/qa/test_qa_in.py` |
| `deepfake_lens/tests/qa/test_qa_sys_doctor.py` (`QaSys3DoctorMatchesScanTest`, 클래스·테스트·docstring 그대로) | W2 (검증 라운드 4) | `deepfake_lens/tests/qa/test_qa_sys.py` |
| `deepfake_lens/tests/qa/test_qa_sys_gate.py` (`MeasurementGateTest`, 클래스·테스트·docstring 그대로) | W2 (검증 라운드 4) | `deepfake_lens/tests/qa/test_qa_sys.py` |
| `deepfake_lens/tests/qa/test_qa_sys_integrity.py` (`QaSys6SignatureCoversWholeReportTest`, `QaSys7ReadRootConfinementTest`, `QaSys7ReadRootUnitTest`, 클래스·테스트·docstring 그대로) | W2 (검증 라운드 4) | `deepfake_lens/tests/qa/test_qa_sys.py` |
| `deepfake_lens/tests/qa/test_qa_traceability.py` (`TraceabilityTest`, `HarnessLogicTest`, 클래스·테스트·docstring 그대로) | W2 (검증 라운드 4) | `deepfake_lens/tests/qa/test_qa_sys.py` |

## P0R-6 재설계로 없어진 테스트 (기준선 밖 — 0단계 중에 생긴 테스트)

사용자 결정 2026-10-17: `/api/report`는 클라이언트가 게시한 행을 서버 재분석과 맞추지 않고, 서버가 보관한 검사 작업(`scan_job`)의 행만 서명한다. 아래 테스트는 **없어진 설계(게시 행의 재분석·대조·검증)**를 고정하던 것이라 함께 지웠다(커밋 제목: `feat(report): P0R-6 /api/report signs a server-held scan job only — …`). 기준선(QA-SYS-10) 테스트는 없다 — 그래서 표가 아니라 목록으로 적는다(QA-SYS-10 검사는 표의 행만 기준선 이름으로 확인한다). 의도가 남는 것은 `deepfake_lens/tests/test_servers.py`의 새 클래스가 새 API로 이어받는다: 게시 행 대신 `scan_job` — 서명 행 = 작업 행(`ReportSignsTheJobsRowsTest`, `ReportSignedSetIsTheServersInvariantTest`), 검사 뒤 변경 409(`ReportRefusesChangedRowsTest`, `ReportRefusesAFolderLinkedAfterTheScanTest`), 특수 항목(`ReportSignsSpecialEntriesAsScannedTest`), 취소·미기록 계수(`ReportSignedSetIsTheServersInvariantTest.test_r24_1_…`, `ReportUnrecordedFilesAreTheServersTest`), 클라이언트 문구 불변식(`ReportSignsNoPostedTextInvariantTest`), 구성원 사슬 단사(`ReportArchiveMemberChainIsInjectiveTest`), 업로드 이름(`ReportUploadRowNamesTest`), 작업 보관·소유·만료(`ScanJobsAreServerHeldTest`). 게시 행·별칭·게시된 중복 관계·게시된 폴더 사유·게시된 취소 계수를 다루던 테스트는 그 입력이 요청 필드가 아니게 되어(400 「알 수 없는 필드」) 이어받을 대상이 없다.

- `ReportEvidenceHashesBothServersTest.test_symlink_component_on_the_path_is_not_followed`
- `ReportSignsOnlyServerResultsTest.test_forged_row_is_replaced_by_the_server_result`
- `ReportSignsOnlyServerResultsTest.test_rows_the_server_cannot_reanalyze_are_excluded_and_marked`
- `ReportSignsOnlyServerResultsTest.test_report_options_are_validated`
- `ReportRefusesChangedRowsTest.test_an_archive_member_replaced_after_the_scan_is_409`
- `ReportRefusesChangedRowsTest.test_a_dedupe_duplicate_stays_a_duplicate_and_its_change_is_409`
- `ReportRefusesChangedRowsTest.test_junk_posted_digests_are_409`
- `ReportRefusesChangedRowsTest.test_unchanged_rows_are_signed_as_before`
- `ReportSignsSpecialEntriesAsScannedTest.test_a_fifo_and_a_socket_are_signed_as_the_scan_listed_them`
- `ReportSignsSpecialEntriesAsScannedTest.test_a_file_replaced_by_a_fifo_after_the_scan_is_still_409`
- `ReportSignsSpecialEntriesAsScannedTest.test_a_missing_file_is_still_not_found`
- `ReportVerifiesPostedDuplicatesTest.test_a_row_posted_as_a_duplicate_of_itself_is_refused`
- `ReportVerifiesPostedDuplicatesTest.test_a_duplicate_of_a_row_that_is_not_posted_is_refused`
- `ReportVerifiesPostedDuplicatesTest.test_a_duplicate_of_another_duplicate_is_refused`
- `ReportVerifiesPostedDuplicatesTest.test_a_duplicate_of_a_posted_row_with_other_bytes_is_409`
- `ReportVerifiesPostedDuplicatesTest.test_the_scans_own_duplicate_is_still_signed_as_a_duplicate`
- `ReportRefusesAFolderLinkedAfterTheScanTest.test_a_row_the_scan_did_not_hash_is_still_only_excluded`
- `ReportSignsNoPostedFolderReasonTest.test_a_forged_walk_limit_row_is_excluded_and_its_text_is_in_no_artifact`
- `ReportSignsNoPostedFolderReasonTest.test_a_forged_loop_row_of_a_folder_that_is_no_loop_is_409`
- `ReportSignsNoPostedFolderReasonTest.test_a_loop_the_server_finds_is_its_own_row`
- `ReportSignsNoPostedFolderReasonTest.test_an_unreadable_folder_row_still_signs_the_servers_failed_row`
- `ReportSignsNoPostedFolderReasonTest.test_a_duplicate_row_carries_the_scans_reason`
- `ReportSignsNoPostedTextInvariantTest.test_no_posted_row_text_reaches_a_signed_artifact`
- `ReportRowPathsAreTheScansOwnTest.test_an_alias_of_a_scan_row_path_is_400`
- `ReportRowPathsAreTheScansOwnTest.test_an_escaped_alias_is_400`
- `ReportRowPathsAreTheScansOwnTest.test_a_row_posted_twice_is_400`
- `ReportRowPathsAreTheScansOwnTest.test_a_tilde_in_a_row_path_is_a_name_in_the_scan_folder`
- `ReportRowPathsAreTheScansOwnTest.test_the_scans_own_rows_are_still_signed`
- `ReportRecordsAnalysisOptionsTest.test_the_options_are_signed_and_printed`
- `ReportRecordsAnalysisOptionsTest.test_a_posted_cancel_count_is_not_signed_unchecked`
- `ReportScanRootAndUploadsTest.test_subfolder_scan_report_signs_the_subfolder_file`
- `ReportScanRootAndUploadsTest.test_r9_9_scan_root_is_signed_and_shown`
- `ReportScanRootAndUploadsTest.test_scan_root_is_required_absolute_existing_and_inside_the_roots`
- `ReportScanRootAndUploadsTest.test_upload_named_like_a_root_file_is_never_signed`
- `ReportArchiveMemberIdentityTest.test_every_row_is_reported_as_its_own_file`
- `ReportUnrecordedFilesAreTheServersTest.test_rows_left_out_of_the_post_are_counted`
- `ReportUnrecordedFilesAreTheServersTest.test_the_servers_scan_job_is_the_listing`
- `ReportUnrecordedFilesAreTheServersTest.test_archive_member_rows_left_out_of_the_post_are_counted`
- `ReportUnrecordedFilesAreTheServersTest.test_a_cancelled_scan_with_an_archive_is_reported`
- `ReportUnrecordedFilesAreTheServersTest.test_a_cancelled_recursive_scan_with_an_archive_in_a_subfolder_is_reported`
- `ReportUnrecordedFilesAreTheServersTest.test_a_cancelled_container_that_really_changed_is_still_refused`
- `ReportUnrecordedFilesAreTheServersTest.test_a_cancelled_container_row_needs_a_cancelled_report`
- `ReportUnrecordedFilesAreTheServersTest.test_a_cancelled_scan_job_is_cancelled_without_the_posted_summary`
- `ReportUnrecordedFilesAreTheServersTest.test_a_cancelled_job_of_another_folder_or_a_finished_job_changes_nothing`
- `ReportUnrecordedFilesAreTheServersTest.test_signed_rows_outside_the_servers_listing_are_unknown`
- `ReportUnrecordedFilesAreTheServersTest.test_a_walk_limit_is_unknown`
- `ReportUnrecordedFilesAreTheServersTest.test_upload_rows_only_are_unknown`
- `ReportUnrecordedFilesAreTheServersTest.test_regressions_of_the_report_rules_stay_closed`
- `ReportSignedSetIsTheServersInvariantTest.test_fuzzed_structure_of_a_never_cancelled_scan`
- `ReportSignedSetIsTheServersInvariantTest.test_fuzzed_structure_of_a_cancelled_job`
- `ReportSignedSetIsTheServersInvariantTest.test_fuzzed_structure_of_a_finished_job`
- `ReportSignedSetIsTheServersInvariantTest.test_r25_1_a_file_posted_as_a_cancelled_archive_is_refused`
- `ReportSignedSetIsTheServersInvariantTest.test_r25_1_an_archive_posted_cancelled_without_its_members_is_refused_or_counted`
- `ReportSignedSetIsTheServersInvariantTest.test_r25_1_a_real_cancelled_job_with_its_analyzed_members_left_out`
- `ReportSignedSetIsTheServersInvariantTest.test_original_cases_stay_closed`
- `ReportArchiveMemberChainIsInjectiveTest.test_every_member_is_its_own_row_and_its_chain_round_trips`
- `ReportArchiveMemberChainIsInjectiveTest.test_the_honest_report_is_signed_and_a_left_out_member_is_counted`
- `ReportArchiveMemberChainIsInjectiveTest.test_a_server_whose_identities_collide_refuses_the_report`
- `ReportArchiveMemberChainIsInjectiveTest.test_a_member_chain_in_another_form_is_400`
- `ReportRowAliasesByFileIdentityTest.test_a_path_under_a_bind_mount_loop_is_400`
- `ReportRowAliasesByFileIdentityTest.test_a_path_spelled_unlike_the_listing_is_400`
- `ReportRowAliasesByFileIdentityTest.test_a_gone_file_is_still_409_and_hard_links_are_rows`
