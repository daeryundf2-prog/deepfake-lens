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
