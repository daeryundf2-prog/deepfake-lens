import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from collections import OrderedDict
from typing import Any
from unittest.mock import patch

from deepfake_lens import scan_jobs, webapp_api
from deepfake_lens.case_store import read_all as read_cases
from deepfake_lens.webapp import CLIENT_HEADER, build_server
from deepfake_lens.webapp_api import (
    HISTORY_SKIPPED_NOTE,
    ApiError,
    _history_delete_payload,
    _history_payload,
    _job_case_get_payload,
    _job_case_post_payload,
    api_status,
)

REPORT_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef"
RESULT = {"items": [{}, {}], "summary": {}, "scan_root": "/x/folder"}


def body(value: object) -> bytes:
    return json.dumps(value).encode("utf-8")


def listed_ids() -> list[str]:
    payload: dict[str, Any] = _history_payload()
    return [item["job_id"] for item in payload["items"]]


class _HistoryCase(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.root = Path()
        self.jobs = Path()
        self.cases = Path()

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.jobs = self.root / "jobs"
        self.cases = self.root / "cases.json"
        env = patch.dict(
            os.environ,
            {
                "DEEPFAKE_LENS_SCAN_JOBS": str(self.jobs),
                "DEEPFAKE_LENS_CASES": str(self.cases),
                "DEEPFAKE_LENS_REPORT_KEY": REPORT_KEY,
            },
        )
        env.start()
        self.addCleanup(env.stop)
        scan_jobs.STORE.forget()
        self.addCleanup(scan_jobs.STORE.forget)

    def finished(self, owner: str = scan_jobs.LOCAL_OWNER) -> str:
        job_id = scan_jobs.new_job_id()
        scan_jobs.STORE.record_finished(job_id, owner, "sync", "done", RESULT)
        return job_id

    def record_file(self, job_id: str) -> Path:
        return self.jobs / f"{job_id}.json"


class HistoryPayloadTest(_HistoryCase):
    def test_history_lists_newest_first_with_case_label(self) -> None:
        older = self.finished()
        newer = self.finished()
        saved = _job_case_post_payload(body({"job": older, "case": {"case_no": "2024-1", "case_name": "alpha"}}))
        self.assertEqual(api_status(saved), 200)

        payload: dict[str, Any] = _history_payload()

        items = payload["items"]
        self.assertEqual({item["job_id"] for item in items}, {older, newer})
        created = [item["created"] for item in items]
        self.assertEqual(created, sorted(created, reverse=True))
        by_id = {item["job_id"]: item for item in items}
        self.assertEqual(by_id[older]["case"], {"case_no": "2024-1", "case_name": "alpha"})
        self.assertEqual(by_id[newer]["case"], {"case_no": "", "case_name": ""})
        self.assertTrue(by_id[older]["persisted"])

    def test_job_case_round_trip_returns_saved_fields(self) -> None:
        job = self.finished()
        fields = {"case_no": "2024-2", "case_name": "beta", "side": "plaintiff"}

        saved = _job_case_post_payload(body({"job": job, "case": fields}))
        got = _job_case_get_payload(f"job={job}")

        self.assertEqual(api_status(saved), 200)
        self.assertEqual(api_status(got), 200)
        self.assertEqual(got, {"status": "success", "job": job, "case": fields})

    def test_unknown_case_key_is_400_and_nothing_is_written(self) -> None:
        job = self.finished()

        result = _job_case_post_payload(body({"job": job, "case": {"nope": "x"}}))

        self.assertIsInstance(result, ApiError)
        self.assertEqual(api_status(result), 400)
        self.assertFalse(self.cases.exists())

    def test_non_object_case_is_400(self) -> None:
        job = self.finished()

        result = _job_case_post_payload(body({"job": job, "case": "not-an-object"}))

        self.assertEqual(api_status(result), 400)

    def test_bad_job_id_is_400(self) -> None:
        with self.subTest("delete with traversal id"):
            self.assertEqual(api_status(_history_delete_payload(body({"job": "../etc"}))), 400)
        with self.subTest("delete without job"):
            self.assertEqual(api_status(_history_delete_payload(body({}))), 400)
        with self.subTest("case get with malformed id"):
            self.assertEqual(api_status(_job_case_get_payload("job=zz")), 400)

    def test_unknown_job_is_404(self) -> None:
        job = scan_jobs.new_job_id()

        self.assertEqual(api_status(_job_case_get_payload(f"job={job}")), 404)
        self.assertEqual(api_status(_job_case_post_payload(body({"job": job, "case": {}}))), 404)
        self.assertEqual(api_status(_history_delete_payload(body({"job": job}))), 404)

    def test_another_owners_job_is_404(self) -> None:
        other = scan_jobs.owner_for("another-token")
        job = self.finished(owner=other)

        self.assertEqual(api_status(_job_case_get_payload(f"job={job}")), 404)
        self.assertEqual(api_status(_job_case_post_payload(body({"job": job, "case": {"case_no": "1"}}))), 404)
        self.assertEqual(api_status(_history_delete_payload(body({"job": job}))), 404)
        self.assertTrue(self.record_file(job).exists())
        self.assertNotIn(job, listed_ids())

    def test_history_delete_removes_record_and_case(self) -> None:
        job = self.finished()
        _job_case_post_payload(body({"job": job, "case": {"case_no": "2024-3"}}))
        self.assertTrue(self.record_file(job).exists())

        result = _history_delete_payload(body({"job": job}))

        self.assertEqual(result, {"status": "success", "job": job, "deleted": True})
        self.assertFalse(self.record_file(job).exists())
        self.assertNotIn(job, read_cases())
        self.assertNotIn(job, listed_ids())

    def test_running_job_delete_is_409_and_job_still_finishes_and_lists(self) -> None:
        job, _cancel = scan_jobs.STORE.start(scan_jobs.LOCAL_OWNER, "async")

        result = _history_delete_payload(body({"job": job}))

        self.assertEqual(api_status(result), 409)
        self.assertEqual(listed_ids(), [])
        scan_jobs.STORE.finish(job, "done", RESULT)
        self.assertEqual(listed_ids(), [job])
        self.assertTrue(self.record_file(job).exists())

    def test_non_object_body_is_400(self) -> None:
        for raw in (b"[1]", b"not json"):
            with self.subTest(raw=raw):
                self.assertEqual(api_status(_history_delete_payload(raw)), 400)
                self.assertEqual(api_status(_job_case_post_payload(raw)), 400)

    def test_history_note_mentions_skipped_tampered_record(self) -> None:
        job = self.finished()
        path = self.record_file(job)
        record = json.loads(path.read_text(encoding="utf-8"))
        record["mac"] = "0" * 64
        path.write_text(json.dumps(record), encoding="utf-8")
        scan_jobs.STORE.forget()

        payload: dict[str, Any] = _history_payload()

        self.assertEqual(payload["items"], [])
        self.assertIn(HISTORY_SKIPPED_NOTE.format(count=1), payload["note"] or "")


class HistoryServerTest(_HistoryCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.evidence = Path()
        self.base = ""

    def setUp(self) -> None:
        super().setUp()
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        roots = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())  # the server registers its folder
        roots.start()
        self.addCleanup(roots.stop)
        server = build_server("127.0.0.1", 0, default_folder=self.evidence)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_address[1]}"

    def send(self, method: str, path: str, payload: object = None, *, client: bool = True) -> tuple[int, Any]:
        headers = {"Content-Type": "application/json"}
        if client:
            headers[CLIENT_HEADER] = "qa"
        data = None if payload is None else body(payload)
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    def test_history_without_client_header_is_401(self) -> None:
        status, _data = self.send("GET", "/api/history", client=False)

        self.assertEqual(status, 401)

    def test_history_with_client_header_returns_items(self) -> None:
        job = self.finished()

        status, data = self.send("GET", "/api/history")

        self.assertEqual(status, 200)
        self.assertEqual([item["job_id"] for item in data["items"]], [job])
        self.assertIn("retention_days", data)

    def test_job_case_post_then_get_round_trip_over_http(self) -> None:
        job = self.finished()
        fields = {"case_no": "2024-4", "case_name": "gamma"}

        post_status, posted = self.send("POST", "/api/job-case", {"job": job, "case": fields})
        get_status, got = self.send("GET", f"/api/job-case?job={job}")

        self.assertEqual(post_status, 200)
        self.assertEqual(posted, {"status": "success", "job": job, "case": fields})
        self.assertEqual(get_status, 200)
        self.assertEqual(got, {"status": "success", "job": job, "case": fields})


if __name__ == "__main__":
    unittest.main()
