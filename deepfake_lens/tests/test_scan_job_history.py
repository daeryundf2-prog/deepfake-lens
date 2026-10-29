import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from deepfake_lens import scan_jobs
from deepfake_lens.scan_jobs import HISTORY_LIMIT, STORE_ENV, JobStore, new_job_id

KEY = "0123456789abcdef0123456789abcdef0123456789abcdef"
OWNER = "token:alice"
OTHER = "token:bob"
DAY = 86400
ITEM_KEYS = {"job_id", "status", "source", "created", "finished", "scan_root", "folder_name", "files", "summary", "persisted"}


def _result(scan_root: str, items: int = 1) -> dict[str, Any]:
    return {"scan_root": scan_root, "items": [{"name": f"f{i}"} for i in range(items)], "summary": {"total": items}}


class ScanJobHistoryTest(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.root = Path()
        self.folder = Path()
        self.store = JobStore()

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.folder = self.root / "jobs"
        env = mock.patch.dict(os.environ, {STORE_ENV: str(self.folder), "DEEPFAKE_LENS_REPORT_KEY": KEY})
        env.start()
        self.addCleanup(env.stop)
        self.store = JobStore()

    def persist(self, job_id: str, finished: float, *, owner: str = OWNER, result: dict[str, Any] | None = None) -> None:
        with mock.patch.object(scan_jobs.time, "time", return_value=finished):
            self.store.start(owner, scan_jobs.SOURCE_SYNC, job_id=job_id)
            note = self.store.finish(job_id, "done", result or _result(f"/scans/{job_id}"), evidence=())
        self.assertIsNone(note)
        self.store.forget()

    def test_lists_newest_first(self) -> None:
        base = time.time() - 3600
        ids = [new_job_id() for _ in range(3)]
        for offset, job_id in zip((0, 100, 200), ids):
            self.persist(job_id, base + offset)
        listed = self.store.list_finished(OWNER)
        self.assertEqual([item["job_id"] for item in listed], [ids[2], ids[1], ids[0]])
        self.assertTrue(all(item["persisted"] for item in listed))

    def test_owner_filter(self) -> None:
        mine, theirs = new_job_id(), new_job_id()
        self.persist(mine, time.time() - 60, owner=OWNER)
        self.persist(theirs, time.time() - 60, owner=OTHER)
        self.assertEqual([item["job_id"] for item in self.store.list_finished(OWNER)], [mine])

    def test_expired_record_skipped(self) -> None:
        job_id = new_job_id()
        self.persist(job_id, time.time() - 8 * DAY)
        self.assertEqual(self.store.list_finished(OWNER), [])

    def test_tampered_mac_skipped(self) -> None:
        job_id = new_job_id()
        self.persist(job_id, time.time() - 60)
        path = self.folder / f"{job_id}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["result"]["summary"]["total"] = 999
        path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertLogs(scan_jobs.logger, level="WARNING"):
            self.assertEqual(self.store.list_finished(OWNER), [])

    def test_symlink_record_skipped(self) -> None:
        job_id = new_job_id()
        self.persist(job_id, time.time() - 60)
        path = self.folder / f"{job_id}.json"
        moved = self.root / "elsewhere.json"
        os.rename(path, moved)
        os.symlink(moved, path)
        self.assertEqual(self.store.list_finished(OWNER), [])

    def test_unreadable_record_skipped_with_warning(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        job_id = new_job_id()
        (self.folder / f"{job_id}.json").write_text("{not json", encoding="utf-8")
        with self.assertLogs(scan_jobs.logger, level="WARNING"):
            self.assertEqual(self.store.list_finished(OWNER), [])

    def test_item_shape_and_summary(self) -> None:
        job_id = new_job_id()
        self.persist(job_id, time.time() - 60, result=_result("/scans/case-a", items=2))
        (item,) = self.store.list_finished(OWNER)
        self.assertEqual(set(item), ITEM_KEYS)
        self.assertEqual(item["folder_name"], "case-a")
        self.assertEqual(item["scan_root"], "/scans/case-a")
        self.assertEqual(item["files"], 2)
        self.assertEqual(item["summary"], {"total": 2})
        self.assertEqual(item["status"], "done")

    def test_delete_own_record(self) -> None:
        job_id = new_job_id()
        self.persist(job_id, time.time() - 60)
        path = self.folder / f"{job_id}.json"
        self.assertTrue(self.store.delete(job_id, OWNER))
        self.assertFalse(path.exists())
        self.assertEqual(self.store.list_finished(OWNER), [])
        self.assertFalse(self.store.delete(job_id, OWNER))

    def test_delete_other_owner_refused(self) -> None:
        job_id = new_job_id()
        self.persist(job_id, time.time() - 60, owner=OWNER)
        self.assertFalse(self.store.delete(job_id, OTHER))
        self.assertTrue((self.folder / f"{job_id}.json").exists())
        self.assertEqual([item["job_id"] for item in self.store.list_finished(OWNER)], [job_id])

    def test_in_memory_only_job_listed_not_persisted(self) -> None:
        job_id = new_job_id()
        with mock.patch.dict(os.environ, {STORE_ENV: "relative-jobs"}):
            self.store.start(OWNER, scan_jobs.SOURCE_ASYNC, job_id=job_id)
            note = self.store.finish(job_id, "done", _result("/scans/memo"), evidence=())
            self.assertIsNotNone(note)
            listed = self.store.list_finished(OWNER)
        self.assertEqual([item["job_id"] for item in listed], [job_id])
        self.assertFalse(listed[0]["persisted"])

    def test_running_job_not_deleted(self) -> None:
        job_id = new_job_id()
        self.store.start(OWNER, scan_jobs.SOURCE_ASYNC, job_id=job_id)
        self.assertFalse(self.store.delete(job_id, OWNER))
        self.assertEqual(self.store.running_async_jobs(), [job_id])
        self.assertIsNone(self.store.finish(job_id, "done", _result("/scans/live"), evidence=()))
        self.assertEqual([item["job_id"] for item in self.store.list_finished(OWNER)], [job_id])

    def test_list_capped_at_history_limit(self) -> None:
        base = time.time() - 3600
        newest = ""
        for index in range(HISTORY_LIMIT + 5):
            newest = new_job_id()
            self.persist(newest, base + index)
        listed = self.store.list_finished(OWNER)
        self.assertEqual(len(listed), HISTORY_LIMIT)
        self.assertEqual(listed[0]["job_id"], newest)


if __name__ == "__main__":
    unittest.main()
