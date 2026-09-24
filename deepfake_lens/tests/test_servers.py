"""Tests for local server hardening (webapp + api-serve guard).

Contract doc: docs/deepfake-lens-service.md — loopback binds, token auth on
/api/*, Host allowlist, and scan limits are all pinned here.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from deepfake_lens import api_server
from deepfake_lens.webapp import MAX_FILE_BYTES_CEILING, MAX_SCAN_FILES, _scan_payload, host_name

HAVE_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None


class HostNameTest(unittest.TestCase):
    """host_name must parse Host headers so the rebinding guard can check them."""

    def test_localhost_with_port(self) -> None:
        self.assertEqual(host_name("localhost:8765"), "localhost")

    def test_ipv4_with_port(self) -> None:
        self.assertEqual(host_name("127.0.0.1:9000"), "127.0.0.1")

    def test_ipv6_bracketed_with_port(self) -> None:
        self.assertEqual(host_name("[::1]:8765"), "::1")

    def test_bare_hostname(self) -> None:
        self.assertEqual(host_name("localhost"), "localhost")

    def test_rebound_hostname_is_not_local(self) -> None:
        self.assertNotIn(host_name("attacker.example.com"), {"127.0.0.1", "localhost", "::1"})


class ScanPayloadValidationTest(unittest.TestCase):
    """_scan_payload must reject non-integer limits and clamp unbounded values."""

    def _capture_scan_kwargs(self, query: str) -> dict[str, object]:
        from deepfake_lens import webapp

        captured: dict[str, object] = {}

        def fake_scan_directory(folder, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("sentinel-stop")

        original = webapp.scan_directory
        webapp.scan_directory = fake_scan_directory
        try:
            with self.assertRaises(RuntimeError):
                _scan_payload(query, default_folder=None)
        finally:
            webapp.scan_directory = original
        return captured

    def test_invalid_max_files_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            _scan_payload("max_files=abc", default_folder=None)

    def test_invalid_max_file_bytes_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            _scan_payload("max_file_bytes=abc", default_folder=None)

    def test_max_files_is_clamped_to_ceiling(self) -> None:
        captured = self._capture_scan_kwargs("max_files=999999&folder=.")
        self.assertEqual(captured["max_files"], MAX_SCAN_FILES)

    def test_max_files_floors_at_one(self) -> None:
        captured = self._capture_scan_kwargs("max_files=0&folder=.")
        self.assertEqual(captured["max_files"], 1)

    def test_max_file_bytes_is_clamped_to_ceiling(self) -> None:
        captured = self._capture_scan_kwargs("max_file_bytes=99999999999999&folder=.")
        self.assertEqual(captured["max_file_bytes"], MAX_FILE_BYTES_CEILING)

    def _capture_scan_with_profiles(self, query: str, profiles: list[Path]) -> dict[str, object]:
        from deepfake_lens import webapp

        original_profiles = webapp.default_engine_profiles
        webapp.default_engine_profiles = lambda root=None: profiles
        try:
            return self._capture_scan_kwargs(query)
        finally:
            webapp.default_engine_profiles = original_profiles

    def test_default_engine_profiles_applied_when_model_path_absent(self) -> None:
        profiles = [Path("/tmp/profile-a.json"), Path("/tmp/profile-b.json")]
        captured = self._capture_scan_with_profiles("folder=.", profiles)
        self.assertEqual(captured["model_path"], profiles)

    def test_no_default_engine_disables_profiles(self) -> None:
        captured = self._capture_scan_with_profiles("folder=.&no_default_engine=true", [Path("/tmp/p.json")])
        self.assertIsNone(captured["model_path"])

    def test_explicit_model_path_wins_over_defaults(self) -> None:
        captured = self._capture_scan_with_profiles("folder=.&model_path=/tmp/explicit.json", [Path("/tmp/p.json")])
        self.assertEqual(captured["model_path"], Path("/tmp/explicit.json"))


class AnalyzeUploadPayloadTest(unittest.TestCase):
    """POST /api/analyze-upload must parse multipart bodies and analyze each file."""

    def _multipart(self, *files: tuple[str, bytes]) -> tuple[str, bytes]:
        boundary = "----dfltestboundary"
        chunks: list[bytes] = []
        for name, data in files:
            chunks.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="files"; filename="{name}"\r\n'
                    "Content-Type: application/octet-stream\r\n\r\n"
                ).encode()
                + data
                + b"\r\n"
            )
        chunks.append(f"--{boundary}--\r\n".encode())
        return f"multipart/form-data; boundary={boundary}", b"".join(chunks)

    def test_rejects_non_multipart(self) -> None:
        from deepfake_lens.webapp import _analyze_upload_payload

        result = _analyze_upload_payload("text/plain", b"hello")
        self.assertIn("error", result)

    def test_multipart_files_are_analyzed(self) -> None:
        from deepfake_lens import webapp
        from deepfake_lens.core import ScanItem

        def fake_analyze(path, **kwargs):
            return ScanItem(str(path), Path(str(path)).name, "text", "analyzed", 4, result=None)

        original = webapp.analyze_file
        webapp.analyze_file = fake_analyze
        try:
            content_type, body = self._multipart(("a.txt", b"abc"), ("b.txt", b"def"))
            result = webapp._analyze_upload_payload(content_type, body)
        finally:
            webapp.analyze_file = original

        self.assertEqual(result["summary"]["total"], 2)
        self.assertEqual(result["summary"]["analyzed"], 2)
        self.assertEqual({item["name"] for item in result["items"]}, {"a.txt", "b.txt"})

    def test_empty_upload_reports_error(self) -> None:
        from deepfake_lens.webapp import _analyze_upload_payload

        boundary = "----empty"
        content_type = f"multipart/form-data; boundary={boundary}"
        body = f"--{boundary}--\r\n".encode()
        result = _analyze_upload_payload(content_type, body)
        self.assertIn("error", result)


class CheckPayloadTest(unittest.TestCase):
    """POST /api/check runs every applicable layer on one input."""

    def _multipart(self, name: str, data: bytes) -> tuple[str, bytes]:
        boundary = "----dflcheckboundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        return f"multipart/form-data; boundary={boundary}", body

    def _fake_analyze(self, path, **kwargs):
        from deepfake_lens.core import ScanItem
        return ScanItem(str(path), Path(str(path)).name, "text", "analyzed", 4, result=None)

    def test_text_check_runs_all_text_layers(self) -> None:
        from deepfake_lens import webapp

        original = webapp.analyze_file
        webapp.analyze_file = self._fake_analyze
        try:
            result = webapp._check_text_payload("인공지능 기술은 빠르게 발전하고 있습니다. " * 5)
        finally:
            webapp.analyze_file = original

        self.assertEqual(result["mode"], "text")
        self.assertIn("item", result)
        self.assertIn("advanced", result)
        self.assertIn("signals", result["advanced"])

    def test_text_check_rejects_too_short(self) -> None:
        from deepfake_lens.webapp import _check_text_payload

        self.assertIn("error", _check_text_payload("짧음"))

    def test_file_check_runs_scan_and_forensic(self) -> None:
        from deepfake_lens import webapp

        original = webapp.analyze_file
        webapp.analyze_file = self._fake_analyze
        try:
            content_type, body = self._multipart("note.txt", b"hello world, this is a test document")
            result = webapp._check_file_payload(content_type, body)
        finally:
            webapp.analyze_file = original

        self.assertEqual(result["mode"], "file")
        self.assertEqual(result["item"]["name"], "note.txt")
        self.assertIsNotNone(result["advanced"])

    def test_file_check_rejects_non_multipart(self) -> None:
        from deepfake_lens.webapp import _check_file_payload

        self.assertIn("error", _check_file_payload("text/plain", b"x"))


class FeedbackPayloadTest(unittest.TestCase):
    """POST /api/feedback must append JSONL rows the feedback CLI can load."""

    def _with_feedback_path(self, fn):
        import tempfile
        from deepfake_lens import webapp

        with tempfile.TemporaryDirectory() as tmp:
            original = os.environ.get("DEEPFAKE_LENS_FEEDBACK")
            os.environ["DEEPFAKE_LENS_FEEDBACK"] = str(Path(tmp) / "fb.jsonl")
            try:
                return fn(webapp)
            finally:
                if original is None:
                    os.environ.pop("DEEPFAKE_LENS_FEEDBACK")
                else:
                    os.environ["DEEPFAKE_LENS_FEEDBACK"] = original

    def test_valid_label_appends_jsonl(self) -> None:
        from deepfake_lens.feedback import load_feedback

        def run(webapp):
            payload = webapp._feedback_payload(json.dumps({
                "path": "/tmp/x.png", "expected_label": "synthetic",
                "result": {"score": 42},
            }).encode())
            self.assertTrue(payload["ok"])
            entries = load_feedback(payload["feedback_file"])
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].expected_label, "synthetic")
            self.assertEqual(entries[0].embedded_result, {"score": 42})

        self._with_feedback_path(run)

    def test_unknown_label_rejected(self) -> None:
        def run(webapp):
            payload = webapp._feedback_payload(json.dumps({
                "path": "/tmp/x.png", "expected_label": "maybe",
            }).encode())
            self.assertIn("error", payload)

        self._with_feedback_path(run)

    def test_missing_path_rejected(self) -> None:
        def run(webapp):
            payload = webapp._feedback_payload(json.dumps({"expected_label": "real"}).encode())
            self.assertIn("error", payload)

        self._with_feedback_path(run)


class ClientHeaderGateTest(unittest.TestCase):
    """api_request_allowed: token path vs the loopback client-header gate.

    The client header is the CSRF defense on tokenless loopback binds:
    browsers cannot attach custom headers to cross-origin "simple" requests,
    so requiring one forces a preflight the server never answers.
    """

    def _headers(self, mapping: dict[str, str]) -> dict[str, str]:
        return mapping

    def test_no_token_requires_client_header(self) -> None:
        from deepfake_lens.webapp import CLIENT_HEADER, api_request_allowed

        self.assertFalse(api_request_allowed(self._headers({}), token=None))
        self.assertFalse(api_request_allowed(self._headers({CLIENT_HEADER: ""}), token=None))
        self.assertTrue(api_request_allowed(self._headers({CLIENT_HEADER: "gui"}), token=None))
        self.assertTrue(api_request_allowed(self._headers({CLIENT_HEADER: "curl-script"}), token=None))

    def test_token_ignores_client_header(self) -> None:
        from deepfake_lens.webapp import CLIENT_HEADER, api_request_allowed

        self.assertFalse(api_request_allowed(self._headers({CLIENT_HEADER: "gui"}), token="s3cret"))
        self.assertTrue(
            api_request_allowed(self._headers({"X-Deepfake-Lens-Token": "s3cret"}), token="s3cret")
        )


class LiveServerClientHeaderTest(unittest.TestCase):
    """End-to-end: the running web server must 401 /api/* requests that lack
    the client header on a tokenless loopback bind."""

    def _start_server(self):
        import socket
        import threading
        import urllib.request
        from deepfake_lens import webapp

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        thread = threading.Thread(
            target=webapp.run_server,
            kwargs={"host": "127.0.0.1", "port": port},
            daemon=True,
        )
        thread.start()
        url = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                urllib.request.urlopen(url + "/", timeout=1)
                break
            except OSError:
                threading.Event().wait(0.05)
        return url

    def test_api_requires_client_header_without_token(self) -> None:
        import urllib.error
        import urllib.request
        from deepfake_lens.webapp import CLIENT_HEADER

        url = self._start_server()
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(url + "/api/stats", timeout=5)
        self.assertEqual(ctx.exception.code, 401)

        request = urllib.request.Request(url + "/api/stats", headers={CLIENT_HEADER: "gui"})
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["status"], "ok")

    def test_gui_shell_still_served_without_header(self) -> None:
        import urllib.request

        url = self._start_server()
        with urllib.request.urlopen(url + "/", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"<", response.read(64))

    def test_feedback_write_rejected_without_client_header(self) -> None:
        import urllib.error
        import urllib.request

        url = self._start_server()
        body = json.dumps({"path": "x.png", "expected_label": "synthetic"}).encode()
        request = urllib.request.Request(
            url + "/api/feedback",
            data=body,
            headers={"Content-Type": "text/plain"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(ctx.exception.code, 401)


class AsyncScanJobTest(unittest.TestCase):
    """The async=1 scan job API: start returns a job id, status polls to done."""

    def setUp(self) -> None:
        from deepfake_lens import webapp

        registry = patch.object(webapp, "_SCAN_JOBS", {})
        registry.start()
        self.addCleanup(registry.stop)

    def test_job_lifecycle(self) -> None:
        import tempfile
        from deepfake_lens import webapp

        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "a.txt"
            fixture.write_text("hello world", encoding="utf-8")
            started = webapp._scan_job_start(f"folder={tmp}&no_default_engine=true", default_folder=None)
            self.assertEqual(started["status"], "running")
            job_id = started["job_id"]

            result = None
            for _ in range(200):
                state = webapp._scan_status_payload(f"job={job_id}")
                if state["status"] != "running":
                    result = state["result"]
                    break
                time.sleep(0.05)
            self.assertIsNotNone(result, "job did not finish")
            self.assertEqual(state["status"], "done", result)
            self.assertEqual(result["summary"]["total"], 1)

            # A second poll returns the stored result, and unknown ids are errors.
            again = webapp._scan_status_payload(f"job={job_id}")
            self.assertEqual(again["status"], "done")
            self.assertIn("error", webapp._scan_status_payload("job=deadbeef"))

    def test_missing_job_parameter_is_error(self) -> None:
        from deepfake_lens import webapp

        self.assertIn("error", webapp._scan_status_payload(""))

    def test_cancel_payload(self) -> None:
        import threading
        from deepfake_lens import webapp

        self.assertIn("error", webapp._scan_cancel_payload(""))
        self.assertIn("error", webapp._scan_cancel_payload("job=deadbeef"))

        cancel = threading.Event()
        with webapp._SCAN_JOBS_LOCK:
            webapp._SCAN_JOBS["job1"] = {"status": "running", "created": time.time(), "cancel": cancel}
        out = webapp._scan_cancel_payload("job=job1")
        self.assertTrue(out["cancelled"])
        self.assertTrue(cancel.is_set())

        # A finished job reports that there is nothing left to cancel.
        with webapp._SCAN_JOBS_LOCK:
            webapp._SCAN_JOBS["job2"] = {"status": "done", "created": time.time(), "cancel": threading.Event()}
        out = webapp._scan_cancel_payload("job=job2")
        self.assertFalse(out["cancelled"])
        self.assertEqual(out["status"], "done")

    def test_cancel_stops_scan_early(self) -> None:
        """should_stop must short-circuit scan_directory between items."""
        import tempfile
        from deepfake_lens.core import scan_directory

        with tempfile.TemporaryDirectory() as tmp:
            for i in range(5):
                (Path(tmp) / f"f{i}.txt").write_text(f"content {i}", encoding="utf-8")
            stop_calls = []

            def stop() -> bool:
                stop_calls.append(1)
                return len(stop_calls) > 2

            summary, items = scan_directory(tmp, should_stop=stop)
            self.assertLess(len(items), 5)

    def test_job_cap_refuses_overflow(self) -> None:
        from deepfake_lens import webapp

        with webapp._SCAN_JOBS_LOCK:
            for i in range(webapp._SCAN_JOB_MAX):
                webapp._SCAN_JOBS[f"fake{i}"] = {"status": "running", "created": time.time()}
        with self.assertRaises(ValueError):
            webapp._scan_job_start("folder=.&no_default_engine=true", default_folder=None)


class ApiServeTokenGateTest(unittest.TestCase):
    """The CLI must refuse non-localhost API binds without a token."""

    def test_non_local_host_without_token_is_rejected(self) -> None:
        from deepfake_lens import cli

        with self.assertRaises(SystemExit) as ctx:
            cli.main(["api-serve", "--host", "0.0.0.0", "--port", "0"])
        self.assertEqual(ctx.exception.code, 2)

    def test_web_allow_lan_without_token_is_rejected(self) -> None:
        from deepfake_lens import cli

        with self.assertRaises(SystemExit) as ctx:
            cli.main(["web", "--allow-lan", "--host", "0.0.0.0", "--port", "0"])
        self.assertEqual(ctx.exception.code, 2)

    def test_run_server_refuses_allow_lan_without_token(self) -> None:
        from deepfake_lens import webapp

        with self.assertRaises(ValueError):
            webapp.run_server("0.0.0.0", 0, allow_lan=True)


class ServiceContractTest(unittest.TestCase):
    """Pin the documented service contract: loopback defaults and guards."""

    def test_servers_default_to_loopback(self) -> None:
        from deepfake_lens import webapp

        self.assertEqual(inspect.signature(api_server.run_server).parameters["host"].default, "127.0.0.1")
        self.assertEqual(inspect.signature(api_server.create_app).parameters["host"].default, "127.0.0.1")
        self.assertEqual(inspect.signature(webapp.run_server).parameters["host"].default, "127.0.0.1")

    def test_api_server_host_name_parsing(self) -> None:
        self.assertEqual(api_server.host_name("localhost:8765"), "localhost")
        self.assertEqual(api_server.host_name("[::1]:8765"), "::1")
        self.assertNotIn(api_server.host_name("attacker.example.com"), api_server.LOCAL_HOSTS)

    def test_webapp_refuses_non_loopback_without_allow_lan(self) -> None:
        from deepfake_lens import webapp

        with self.assertRaises(ValueError):
            webapp.run_server("0.0.0.0", 0)

    def test_create_app_import_guard(self) -> None:
        if importlib.util.find_spec("fastapi") is not None:
            self.skipTest("fastapi installed; the missing-dep branch does not apply")
        with self.assertRaises(ImportError):
            api_server.create_app()


@unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed")
class ApiServiceContractTest(unittest.TestCase):
    """HTTP-level contract for the FastAPI screening service."""

    def _client(self, **kwargs):
        from fastapi.testclient import TestClient

        return TestClient(api_server.create_app(**kwargs))

    def test_health_endpoint_shape(self) -> None:
        client = self._client()
        response = client.get("/api/health", headers={"host": "localhost"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "healthy"})

    def test_token_required_when_configured(self) -> None:
        client = self._client(token="s3cret")
        self.assertEqual(client.get("/api/health").status_code, 401)
        self.assertEqual(client.get("/api/health", headers={"x-api-token": "s3cret"}).status_code, 200)

    def test_host_allowlist_without_token(self) -> None:
        client = self._client()
        self.assertEqual(client.get("/api/health", headers={"host": "attacker.example.com"}).status_code, 403)
        self.assertEqual(client.get("/api/health", headers={"host": "localhost:8765"}).status_code, 200)

    def test_root_is_unauthenticated(self) -> None:
        client = self._client(token="s3cret")
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("version", response.json())

    _SSE_HEADERS = {"host": "localhost", api_server.CLIENT_HEADER: "test"}

    def _collect_sse(self, response) -> dict[str, list[dict]]:
        events: dict[str, list[dict]] = {}
        current: str | None = None
        for raw in "".join(response.iter_text()).splitlines():
            if raw.startswith("event:"):
                current = raw.split(":", 1)[1].strip()
            elif raw.startswith("data:") and current:
                events.setdefault(current, []).append(json.loads(raw[5:].strip()))
        return events

    def test_check_stream_emits_progress_then_result(self) -> None:
        client = self._client()
        text = "인공지능 기술은 빠르게 발전하고 있습니다. " * 5
        with client.stream(
            "POST",
            "/api/check/stream",
            params={"text": text},
            headers=self._SSE_HEADERS,
        ) as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"].split(";")[0], "text/event-stream")
            events = self._collect_sse(response)
        self.assertIn("job", events)
        self.assertIn("progress", events)
        self.assertIn("result", events)
        self.assertEqual(events["result"][0]["mode"], "text")
        self.assertIn("item", events["result"][0])
        self.assertIn("advanced", events["result"][0])

    def test_check_stream_requires_input(self) -> None:
        client = self._client()
        with client.stream(
            "POST", "/api/check/stream", headers=self._SSE_HEADERS
        ) as response:
            events = self._collect_sse(response)
        self.assertIn("error", events)

    def test_cancel_unknown_job_is_404(self) -> None:
        client = self._client()
        response = client.post(
            "/api/jobs/deadbeef/cancel", headers=self._SSE_HEADERS
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(client.get("/api/jobs/deadbeef", headers=self._SSE_HEADERS).status_code, 404)

    def test_scan_stream_emits_progress_then_result(self) -> None:
        import tempfile
        from pathlib import Path

        client = self._client()
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.txt").write_text("안녕하세요 테스트 문서입니다.", encoding="utf-8")
            Path(tmp, "b.txt").write_text("다른 파일입니다.", encoding="utf-8")
            with client.stream(
                "POST",
                "/api/scan/stream",
                params={"directory": tmp},
                headers=self._SSE_HEADERS,
            ) as response:
                self.assertEqual(response.status_code, 200)
                events = self._collect_sse(response)
        self.assertIn("job", events)
        self.assertIn("progress", events)
        self.assertIn("result", events)
        result = events["result"][0]
        self.assertEqual(result["mode"], "scan")
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["items"]), 2)

    def test_scan_stream_requires_directory(self) -> None:
        client = self._client()
        with client.stream(
            "POST",
            "/api/scan/stream",
            params={"directory": "C:/nonexistent-dir-xyz"},
            headers=self._SSE_HEADERS,
        ) as response:
            events = self._collect_sse(response)
        self.assertIn("error", events)


if __name__ == "__main__":
    unittest.main()


class ComparePayloadTest(unittest.TestCase):
    """POST /api/compare pairs two uploaded files by kind."""

    def _two_files(self, a_name: str, a_data: bytes, b_name: str, b_data: bytes) -> tuple[str, bytes]:
        boundary = "----dflcmpboundary"
        def part(name: str, data: bytes) -> bytes:
            return (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode() + data + b"\r\n"
        body = part(a_name, a_data) + part(b_name, b_data) + f"--{boundary}--\r\n".encode()
        return f"multipart/form-data; boundary={boundary}", body

    def test_text_pair_returns_stylometry(self) -> None:
        from deepfake_lens.webapp import _compare_payload

        text = "인공지능 기술은 빠르게 발전하고 있으며 다양한 산업에 적용된다. 또한 윤리 문제가 함께 논의된다. " * 8
        content_type, body = self._two_files("a.txt", text.encode(), "b.txt", text.encode())
        result = _compare_payload(content_type, body)
        self.assertEqual(result.get("kind"), "stylometry")
        self.assertIn("score", result)

    def test_single_file_rejected(self) -> None:
        from deepfake_lens.webapp import _compare_payload

        boundary = "----dflcmpboundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="a.txt"\r\n'
            "Content-Type: application/octet-stream\r\n\r\nhello\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        result = _compare_payload(f"multipart/form-data; boundary={boundary}", body)
        self.assertIn("error", result)


class PreviewPayloadTest(unittest.TestCase):
    """GET /api/preview serves media under the scanned root only."""

    def test_media_served_within_root(self) -> None:
        import tempfile
        from deepfake_lens.webapp import _preview_payload

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.png"
            p.write_bytes(b"\x89PNG\r\n\x1a\nfake")
            status, body, _, mime = _preview_payload(f"path={p}&root={d}")
            self.assertEqual(status, 200)
            self.assertEqual(mime, "image/png")
            self.assertEqual(body[:4], b"\x89PNG")

    def test_outside_root_forbidden(self) -> None:
        import tempfile
        from deepfake_lens.webapp import _preview_payload

        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            p = Path(other) / "a.png"
            p.write_bytes(b"x")
            status, _, msg, _ = _preview_payload(f"path={p}&root={d}")
            self.assertEqual(status, 403)

    def test_non_media_forbidden(self) -> None:
        import tempfile
        from deepfake_lens.webapp import _preview_payload

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.txt"
            p.write_text("hi")
            status, _, _, _ = _preview_payload(f"path={p}&root={d}")
            self.assertEqual(status, 403)
