"""GUI redesign: the server fields the redesigned GUI reads (U-7, U-8).

* ``/api/scan-status`` reports the job's progress — ``done``, ``total``,
  ``current``, ``recent`` and ``elapsed_seconds`` — on both servers (the
  FastAPI route calls the same payload function).
* A running job is never evicted from the registry: it was dropped 15
  minutes after it started, so a long scan's result became unreachable
  while it was still running (the GUI gave up after 30 minutes, U-7).
* ``/api/stats`` names the server's default folder (``default_folder`` and
  its bytes, ``default_folder_b64``) and whether PDF exports can be made
  (``pdf_available``) — the GUI prefills the folder box from it (U-8).
"""

from __future__ import annotations

import importlib.util
import tempfile
import threading
import time
import unittest
from collections import OrderedDict
from pathlib import Path
from unittest.mock import patch

from deepfake_lens import webapp_api
from deepfake_lens.json_text import fs_b64decode, fs_b64encode

HAVE_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None


class ScanStatusProgressTest(unittest.TestCase):
    # Merge of phase0 P0R-6: the job registry is scan_jobs.STORE (was webapp_api._SCAN_JOBS); the progress is held
    # with the running job in the store (set_progress / add_progress_row) — the tests set it there.
    def setUp(self) -> None:
        from deepfake_lens.scan_jobs import JobStore

        self.store = JobStore()
        for target in (patch.object(webapp_api, "STORE", self.store), patch.object(webapp_api, "_READ_ROOTS", OrderedDict())):
            target.start()
            self.addCleanup(target.stop)

    def _finish(self, job_id: str) -> dict[str, object]:
        for _ in range(400):
            state = webapp_api._scan_status_payload(f"job={job_id}")
            if state["status"] != "running":
                return state
            time.sleep(0.05)
        self.fail("job did not finish")

    def _running(self, progress: dict[str, object] | None = None, *, age: float = 0.0) -> str:
        from deepfake_lens.scan_jobs import LOCAL_OWNER, SOURCE_ASYNC

        job_id, _cancel = self.store.start(LOCAL_OWNER, SOURCE_ASYNC)
        with self.store._lock:
            self.store._jobs[job_id]["created"] = time.time() - age
        if progress is not None:
            self.store.set_progress(job_id, progress)
        return job_id

    def test_status_reports_done_total_current_recent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for index in range(7):
                (Path(tmp) / f"f{index}.txt").write_text(f"내용 {index}", encoding="utf-8")
            (Path(tmp) / "x.xyz").write_bytes(b"?")
            started = webapp_api._scan_job_start(f"folder={tmp}&no_default_engine=true", default_folder=Path(tmp))
            state = self._finish(str(started["job_id"]))
        self.assertEqual(state["status"], "done")
        self.assertEqual((state["done"], state["total"]), (8, 8))
        result = state["result"]
        assert isinstance(result, dict)
        names = {item["display_name"] for item in result["items"]}
        self.assertIn(state["current"], names)
        recent = state["recent"]
        assert isinstance(recent, list)
        self.assertEqual(len(recent), webapp_api._SCAN_JOB_RECENT)
        self.assertEqual(recent[0]["name"], state["current"])
        for row in recent:
            self.assertEqual(set(row), {"name", "status", "verdict_code"})
            self.assertIn(row["name"], names)
            self.assertIn(row["verdict_code"], {None, "manipulation_evidence", "authenticity_evidence", "undetermined"})
        self.assertIsInstance(state["elapsed_seconds"], float)
        self.assertGreaterEqual(float(state["elapsed_seconds"]), 0.0)

    def test_running_job_reports_its_progress(self) -> None:
        job = self._running({"done": 2, "total": 9, "current": "b.png", "recent": [{"name": "b.png", "status": "analyzed", "verdict_code": "undetermined"}]}, age=3)
        state = webapp_api._scan_status_payload(f"job={job}")
        self.assertEqual((state["status"], state["done"], state["total"], state["current"]), ("running", 2, 9, "b.png"))
        self.assertNotIn("result", state)
        self.assertGreaterEqual(float(state["elapsed_seconds"]), 3.0)  # type: ignore[arg-type]

    def test_a_job_before_enumeration_has_no_total(self) -> None:
        job = self._running()
        state = webapp_api._scan_status_payload(f"job={job}")
        self.assertEqual((state["done"], state["total"], state["current"], state["recent"]), (0, None, None, []))

    def test_a_running_job_is_never_evicted(self) -> None:
        from deepfake_lens.scan_jobs import ttl_seconds

        job = self._running(age=ttl_seconds() + 60)
        self.assertEqual(webapp_api._scan_status_payload(f"job={job}")["status"], "running")

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed")
    def test_api_serve_status_has_the_same_fields(self) -> None:
        from fastapi.testclient import TestClient

        from deepfake_lens.api_server import create_app

        job = self._running({"done": 1, "total": 4, "current": "a.txt", "recent": []})
        client = TestClient(create_app())
        body = client.get(f"/api/scan-status?job={job}", headers={"host": "localhost", "X-Deepfake-Lens-Client": "gui"}).json()
        self.assertEqual({key: body[key] for key in ("status", "done", "total", "current", "recent")},
                         {"status": "running", "done": 1, "total": 4, "current": "a.txt", "recent": []})


class StatsDefaultFolderTest(unittest.TestCase):
    def test_stats_names_the_default_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stats = webapp_api._stats_payload(Path(tmp))
            resolved = str(Path(tmp).resolve())
        self.assertEqual(stats["default_folder"], resolved)
        self.assertEqual(stats["default_folder_b64"], fs_b64encode(resolved))
        self.assertEqual(fs_b64decode(str(stats["default_folder_b64"])), resolved)
        self.assertIsInstance(stats["pdf_available"], bool)
        for key in ("status", "version", "modules"):
            self.assertIn(key, stats)

    def test_stats_without_a_default_folder(self) -> None:
        stats = webapp_api._stats_payload()
        self.assertIsNone(stats["default_folder"])
        self.assertIsNone(stats["default_folder_b64"])

    def test_web_server_sends_its_folder(self) -> None:
        import json
        import urllib.request

        from deepfake_lens.webapp import build_server

        with tempfile.TemporaryDirectory() as tmp, patch.object(webapp_api, "_READ_ROOTS", OrderedDict()):
            server = build_server("127.0.0.1", 0, default_folder=Path(tmp))
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_address[1]}/api/stats", headers={"X-Deepfake-Lens-Client": "gui"},
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    stats = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
            self.assertEqual(stats["default_folder"], str(Path(tmp).resolve()))

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed")
    def test_api_serve_sends_its_folder(self) -> None:
        from fastapi.testclient import TestClient

        from deepfake_lens.api_server import create_app

        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(default_folder=Path(tmp)))
            stats = client.get("/api/stats", headers={"host": "localhost", "X-Deepfake-Lens-Client": "gui"}).json()
            self.assertEqual(stats["default_folder"], str(Path(tmp).resolve()))
        self.assertIsNone(TestClient(create_app()).get("/api/stats", headers={"host": "localhost", "X-Deepfake-Lens-Client": "gui"}).json()["default_folder"])


if __name__ == "__main__":
    unittest.main()
