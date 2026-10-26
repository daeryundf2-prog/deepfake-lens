from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deepfake_lens import case_store
from deepfake_lens.case_store import CASES_ENV, CaseInputInvalid, delete_case, prune_cases, read_all, read_case, write_case
from deepfake_lens.user_data import UserDataRefused

JOB = "0123456789ab"
OTHER = "fedcba987654"


def _cases_file() -> Path:
    return Path(os.environ[CASES_ENV])


class CaseStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        patch = mock.patch.dict(os.environ, {CASES_ENV: str(Path(temp_dir.name) / "cases.json")})
        patch.start()
        self.addCleanup(patch.stop)

    def test_round_trip(self) -> None:
        saved = write_case(JOB, {"case_no": " 2026가단1 ", "plaintiff": "김철수", "side": "plaintiff", "court": ""})
        self.assertEqual(saved, {"case_no": "2026가단1", "plaintiff": "김철수", "side": "plaintiff"})
        self.assertEqual(read_case(JOB), saved)
        self.assertEqual(read_all(), {JOB: saved})
        self.assertEqual(read_case(OTHER), {})

    def test_missing_file_reads_empty(self) -> None:
        self.assertFalse(_cases_file().exists())
        self.assertEqual(read_all(), {})

    def test_unknown_key_refused(self) -> None:
        with self.assertRaisesRegex(CaseInputInvalid, "알 수 없는 사건 항목"):
            write_case(JOB, {"x": "y"})
        self.assertFalse(_cases_file().exists())

    def test_too_long_value_refused(self) -> None:
        with self.assertRaisesRegex(CaseInputInvalid, "200자"):
            write_case(JOB, {"case_name": "가" * 201})
        write_case(JOB, {"case_name": "가" * 200})
        self.assertEqual(len(read_case(JOB)["case_name"]), 200)

    def test_bad_job_id_refused(self) -> None:
        for bad in ("../etc", "XYZ", "0123", ""):
            with self.subTest(job_id=bad), self.assertRaises(CaseInputInvalid):
                write_case(bad, {"case_no": "1"})
        with self.assertRaises(CaseInputInvalid):
            read_case("../etc")

    def test_side_validation(self) -> None:
        for side in ("plaintiff", "defendant", ""):
            with self.subTest(side=side):
                write_case(JOB, {"side": side})
        with self.assertRaisesRegex(CaseInputInvalid, "side"):
            write_case(JOB, {"side": "judge"})

    def test_empty_values_delete_entry(self) -> None:
        write_case(JOB, {"case_no": "1"})
        write_case(OTHER, {"case_no": "2"})
        self.assertEqual(write_case(JOB, {"case_no": "  ", "court": ""}), {})
        self.assertEqual(read_all(), {OTHER: {"case_no": "2"}})
        self.assertTrue(delete_case(OTHER))
        self.assertFalse(delete_case(OTHER))
        self.assertEqual(read_all(), {})

    def test_inside_evidence_refused(self) -> None:
        evidence = [_cases_file().parent]
        with self.assertRaises(UserDataRefused):
            write_case(JOB, {"case_no": "1"}, evidence)
        with self.assertRaises(UserDataRefused):
            read_all(evidence)
        with self.assertRaises(UserDataRefused):
            delete_case(JOB, evidence)
        self.assertFalse(_cases_file().exists())

    def test_corrupt_file_error(self) -> None:
        _cases_file().write_text("{not json", encoding="utf-8")
        with self.assertRaisesRegex(UserDataRefused, "읽지 못해"):
            read_all()
        with self.assertRaisesRegex(UserDataRefused, "읽지 못해"):
            write_case(JOB, {"case_no": "1"})
        self.assertEqual(_cases_file().read_text(encoding="utf-8"), "{not json")

    def test_non_object_file_error(self) -> None:
        _cases_file().write_text("[1, 2]", encoding="utf-8")
        with self.assertRaisesRegex(UserDataRefused, "JSON 객체"):
            read_all()

    def test_written_file_is_private(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX file modes only")
        write_case(JOB, {"case_no": "1"})
        self.assertEqual(_cases_file().stat().st_mode & 0o777, 0o600)

    def test_prune_drops_entries_not_kept(self) -> None:
        write_case(JOB, {"case_no": "1"})
        write_case(OTHER, {"case_no": "2"})
        self.assertEqual(prune_cases([JOB]), 1)
        self.assertEqual(read_all(), {JOB: {"case_no": "1"}})

    def test_prune_without_stale_entries_does_not_write(self) -> None:
        write_case(JOB, {"case_no": "1"})
        with mock.patch("deepfake_lens.case_store._persist") as persist:
            self.assertEqual(prune_cases([JOB, OTHER]), 0)
        persist.assert_not_called()
        self.assertEqual(read_all(), {JOB: {"case_no": "1"}})

    def test_prune_missing_file_creates_nothing(self) -> None:
        self.assertEqual(prune_cases([]), 0)
        self.assertFalse(_cases_file().exists())

    def test_module_exports_contract(self) -> None:
        self.assertEqual(case_store.CASES_FILE, "cases.json")
        self.assertEqual(case_store.MAX_VALUE_CHARS, 200)
        self.assertEqual(
            case_store.CASE_KEYS,
            ("case_no", "case_name", "plaintiff", "defendant", "court", "law_firm", "center", "contact", "side"),
        )


if __name__ == "__main__":
    unittest.main()
