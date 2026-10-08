"""Phase-0 fix 1, D16: verdict-only summary keys, 결론순 GUI sort, the
"신경망 미탑재" header, Korean user-facing errors, the api-serve client-header
rule for /api/stats, the api-serve help text, and the logged/recorded
web scan-job failure."""

from __future__ import annotations

import contextlib
import io
import json
import logging
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path
from typing import Any
from unittest.mock import patch

from deepfake_lens.cli import main
from deepfake_lens.tests.test_standalone_contract import A1111, HAVE_FASTAPI, REPO_ROOT, run_json, write_wav


class WebScanJobFailureTest(unittest.TestCase):
    def test_scan_job_failure_is_logged_and_recorded(self) -> None:
        """D16: the worker logs the traceback and records coverage + limitation."""
        import threading

        from deepfake_lens import webapp_api

        done = threading.Event()

        def boom(*_args: object, **_kwargs: object) -> dict[str, object]:
            raise RuntimeError("disk vanished")

        with tempfile.TemporaryDirectory() as tmp, patch.object(webapp_api, "_READ_ROOTS", OrderedDict()), \
                patch.object(webapp_api, "_scan_payload", boom), \
                self.assertLogs("deepfake_lens.webapp_api", level=logging.ERROR) as logs:
            webapp_api.configure_read_roots(Path(tmp))
            job = webapp_api._scan_job_start(f"folder={tmp}", default_folder=Path(tmp))
            for _ in range(200):
                status = webapp_api._scan_status_payload(f"job={job['job_id']}")
                if status["status"] != "running":
                    done.set()
                    break
                threading.Event().wait(0.01)
        self.assertTrue(done.is_set())
        self.assertEqual(status["status"], "error")
        result: Any = status["result"]
        self.assertEqual(result["coverage"][0]["status"], "failed")
        self.assertIn("RuntimeError", result["coverage"][0]["reason"])
        self.assertTrue(result["limitations"])
        self.assertTrue(any("Traceback" in line or "disk vanished" in line for line in logs.output))


class SummaryAndGuiTest(unittest.TestCase):
    """D16: verdict-only summary keys, 결론순 sort, no 휴리스틱 전용 header."""

    def test_scan_summary_json_has_verdict_keys_only(self) -> None:
        _, payload = run_json(["scan", str(A1111.parent), "--format", "json"])
        summary = payload["summary"]
        for key in ("manipulation_evidence", "authenticity_evidence", "undetermined"):
            self.assertIn(key, summary)
        for legacy in ("high", "medium", "low", "unknown"):
            self.assertNotIn(legacy, summary)
        self.assertEqual(summary["manipulation_evidence"], 1)

    def test_gui_sort_and_headers(self) -> None:
        package = REPO_ROOT / "deepfake_lens"
        html = (package / "gui.html").read_text(encoding="utf-8")
        js = (package / "gui.js").read_text(encoding="utf-8")
        self.assertNotIn("위험도순", html)
        self.assertNotIn('value="score"', html)
        self.assertIn('<option value="verdict">결론순</option>', html)
        self.assertNotIn("휴리스틱 전용", js)
        self.assertIn("신경망 미탑재(측정 게이트 미충족) — 결정적 근거만 반영", js)
        self.assertIn("let sortMode = 'verdict';", js)

    def test_api_serve_help_has_no_folder_option(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            main(["api-serve", "--help"])
        self.assertNotIn("--folder", out.getvalue())

    def test_oversize_skip_reason_is_korean(self) -> None:
        from deepfake_lens.core import scan_directory

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "big.txt").write_text("x" * 64, encoding="utf-8")
            _, items = scan_directory(tmp, max_file_bytes=8)
        self.assertIn("초과", items[0].error or "")
        self.assertNotIn("exceeds", items[0].error or "")


class WebServerErrorLanguageTest(unittest.TestCase):
    """D16: the built-in server's error bodies are Korean JSON."""

    def test_unknown_api_route_returns_korean_json(self) -> None:
        import socket
        import threading
        import urllib.error
        import urllib.request

        from deepfake_lens import webapp

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        threading.Thread(target=webapp.run_server, kwargs={"host": "127.0.0.1", "port": port}, daemon=True).start()
        url = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                urllib.request.urlopen(url + "/", timeout=1)
                break
            except OSError:
                threading.Event().wait(0.05)
        request = urllib.request.Request(url + "/api/does-not-exist", headers={webapp.CLIENT_HEADER: "gui"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(ctx.exception.code, 404)
        body = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertEqual(body["error"], "찾을 수 없는 경로입니다")

@unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed")
class ApiServeHeaderAndStreamTest(unittest.TestCase):
    """D16: /api/stats needs the client header; /api/scan/stream counts verdicts."""

    HEADERS = {"host": "localhost", "X-Deepfake-Lens-Client": "test"}

    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        from deepfake_lens import api_server, webapp_api

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / A1111.name).write_bytes(A1111.read_bytes())
        write_wav(self.root / "clip.wav")
        self._roots: Any = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        self._roots.start()
        webapp_api.configure_read_roots(self.root)
        self.client = TestClient(api_server.create_app(default_folder=self.root))

    def tearDown(self) -> None:
        self._roots.stop()
        self._tmp.cleanup()

    def test_stats_requires_client_header(self) -> None:
        self.assertEqual(self.client.get("/api/stats", headers={"host": "localhost"}).status_code, 401)
        self.assertEqual(self.client.get("/api/stats", headers=self.HEADERS).status_code, 200)
        self.assertEqual(self.client.get("/api/health", headers={"host": "localhost"}).status_code, 200)

    def test_scan_stream_counts_are_verdict_keys(self) -> None:
        with self.client.stream("POST", "/api/scan/stream", params={"directory": str(self.root)}, headers=self.HEADERS) as response:
            body = "".join(response.iter_text())
        result_line = next(line for line in body.splitlines() if line.startswith("data:") and '"mode": "scan"' in line)
        result = json.loads(result_line[len("data:"):])
        for legacy in ("high", "medium", "low", "unknown"):
            self.assertNotIn(legacy, result["counts"])
        self.assertEqual(result["counts"]["manipulation_evidence"], 1)
        for item in result["items"]:
            self.assertNotIn("band", item)


if __name__ == "__main__":
    unittest.main()
