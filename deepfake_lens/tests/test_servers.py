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
from collections import OrderedDict
from pathlib import Path
from typing import Any
from unittest.mock import patch

from deepfake_lens import api_server
from deepfake_lens import webapp_api
from deepfake_lens.json_text import PATH_B64_INVALID, fs_b64encode
from deepfake_lens.webapp import host_name
from deepfake_lens.webapp_api import MAX_FILE_BYTES_CEILING, MAX_SCAN_FILES, _scan_payload

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


class ReportRequestErrorsAreKoreanTest(unittest.TestCase):
    """G7 (round 5): /api/report request errors were English ("items array is required",
    "thresholds must be an object", "coverage must be an object", "malformed item: …")."""

    CASES: tuple[tuple[object, str], ...] = (
        ({}, "보고서에 넣을 검사 결과 항목(items 배열)이 필요합니다"),
        ({"items": []}, "보고서에 넣을 검사 결과 항목(items 배열)이 필요합니다"),
        # N11: a non-object row is refused by number (was silently skipped,
        # leaving "items required" — or a report about the rows that remained).
        ({"items": ["x"]}, "검사 결과 항목 1번을 해석할 수 없습니다: 항목은 JSON 객체여야 합니다"),
        ({"items": [{"path": "a.png"}], "thresholds": "x"}, "thresholds 값은 JSON 객체여야 합니다"),
        ({"items": [{"path": "a.png"}], "coverage": []}, "coverage 값은 JSON 객체여야 합니다"),
        ([1, 2], "보고서 요청 본문은 JSON 객체여야 합니다"),
    )

    def _check(self, body: dict[str, object] | bytes, expected: str | None = None) -> str:
        from deepfake_lens.error_text import english_prose

        result = webapp_api._report_payload(body if isinstance(body, bytes) else json.dumps(body).encode("utf-8"))
        assert isinstance(result, dict), result
        message = str(result["error"])
        if expected is not None:
            self.assertEqual(message, expected)
        self.assertIsNone(english_prose(message), message)
        return message

    def test_report_request_errors(self) -> None:
        for body, expected in self.CASES:
            with self.subTest(body=body):
                self._check(json.dumps(body).encode("utf-8"), expected)
        self._check(b"{not json", "JSON 본문을 해석할 수 없습니다")

    def test_malformed_item_names_the_item_in_korean(self) -> None:
        message = self._check({"items": [{"path": "a.png", "result": {"verdict_code": "bogus"}}]})
        self.assertTrue(message.startswith("검사 결과 항목 1번을 해석할 수 없습니다: "), message)
        self.assertNotIn("malformed", message)

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed")
    def test_api_serve_report_errors(self) -> None:
        from fastapi.testclient import TestClient

        client = TestClient(api_server.create_app())
        for body, expected in self.CASES[:5]:
            with self.subTest(body=body):
                response = client.post("/api/report", headers={"host": "localhost", "X-Deepfake-Lens-Client": "gui"}, json=body)
                self.assertEqual(response.json().get("error") or response.json().get("detail"), expected, response.text)


class ScanPayloadValidationTest(unittest.TestCase):
    """_scan_payload must reject non-integer limits and clamp unbounded values."""

    def _capture_scan_kwargs(self, query: str) -> dict[str, object]:
        # G7: /api/scan reaches core.scan_directory only through
        # analysis_api.scan_folder, so that is where the call is captured.
        from deepfake_lens import analysis_api

        captured: dict[str, object] = {}

        def fake_scan_directory(folder, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("sentinel-stop")

        with patch.object(analysis_api, "scan_directory", fake_scan_directory):
            with self.assertRaises(RuntimeError):
                _scan_payload(query, default_folder=None)
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

    def test_zero_or_negative_limits_are_refused(self) -> None:
        """Y8 (round 7): max_files=-3 / 0 was clamped to 1 and the scan ran; now 400 (InvalidOption)."""
        from deepfake_lens.analysis_api import InvalidOption

        for query, message in (
            ("max_files=0&folder=.", "max_files는 1 이상이어야 합니다"),
            ("max_files=-3&folder=.", "max_files는 1 이상이어야 합니다"),
            ("max_file_bytes=-5&folder=.", "max_file_bytes는 1 이상이어야 합니다"),
            ("max_file_bytes=0&folder=.", "max_file_bytes는 1 이상이어야 합니다"),
        ):
            with self.subTest(query=query), self.assertRaises(InvalidOption) as ctx:
                _scan_payload(query, default_folder=None)
            self.assertEqual(str(ctx.exception), message)

    def test_max_file_bytes_is_clamped_to_ceiling(self) -> None:
        captured = self._capture_scan_kwargs("max_file_bytes=99999999999999&folder=.")
        self.assertEqual(captured["max_file_bytes"], MAX_FILE_BYTES_CEILING)

    def _capture_scan_with_profiles(self, query: str, profiles: list[Path]) -> dict[str, object]:
        from deepfake_lens import analysis_api

        with patch.object(analysis_api, "default_engine_profiles", lambda root=None: profiles):
            return self._capture_scan_kwargs(query)

    def test_default_engine_profiles_applied_when_model_path_absent(self) -> None:
        profiles = [Path("/tmp/profile-a.json"), Path("/tmp/profile-b.json")]
        captured = self._capture_scan_with_profiles("folder=.", profiles)
        self.assertEqual(captured["model_path"], profiles)

    def test_no_default_engine_disables_profiles(self) -> None:
        captured = self._capture_scan_with_profiles("folder=.&no_default_engine=true", [Path("/tmp/p.json")])
        self.assertIsNone(captured["model_path"])

    def test_explicit_model_path_wins_over_defaults(self) -> None:
        # G7: model_path names a profile file inside the server's models dir.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp).resolve()
            (models / "explicit.json").write_text("{}", encoding="utf-8")
            with patch.object(webapp_api, "_MODELS_DIR", models):
                captured = self._capture_scan_with_profiles("folder=.&model_path=explicit.json", [Path("/tmp/p.json")])
        self.assertEqual(captured["model_path"], models / "explicit.json")

    def test_model_and_fusion_paths_outside_models_dir_rejected(self) -> None:
        """G7: a request can never point the server at another file (400)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp).resolve() / "models"
            models.mkdir()
            (Path(tmp) / "outside.json").write_text("{}", encoding="utf-8")
            with patch.object(webapp_api, "_MODELS_DIR", models):
                for value in ("/etc/passwd", "../outside.json", "sub/x.json", "..", "C:\\x.json", "missing.json"):
                    for field in ("model_path", "fusion_profile"):
                        with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                            _scan_payload(f"folder=.&{field}=" + __import__("urllib.parse").parse.quote(value), default_folder=None)


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
        from deepfake_lens.webapp_api import _analyze_upload_payload

        result = _analyze_upload_payload("text/plain", b"hello")
        self.assertIn("error", result)

    def test_multipart_files_are_analyzed(self) -> None:
        from deepfake_lens import webapp_api
        from deepfake_lens.core import ScanItem

        def fake_analyze(path, **kwargs):
            from deepfake_lens.result_types import ClassificationResult, RiskBand, SourceGuess
            return ScanItem(str(path), Path(str(path)).name, "text", "analyzed", 4,
                            result=ClassificationResult(score=1, band=RiskBand.LOW, band_label="낮음",
                                                        verdict="", signals=[], limitations=[],
                                                        source_guess=SourceGuess.unknown(""), next_checks=[]))

        # G7: uploads are analyzed through analysis_api.analyze_path.
        from deepfake_lens import analysis_api

        with patch.object(analysis_api, "analyze_file", fake_analyze):
            content_type, body = self._multipart(("a.txt", b"abc"), ("b.txt", b"def"))
            result = webapp_api._analyze_upload_payload(content_type, body)

        self.assertEqual(result["summary"]["total"], 2)
        self.assertEqual(result["summary"]["analyzed"], 2)
        self.assertEqual({item["name"] for item in result["items"]}, {"a.txt", "b.txt"})

    def test_r10_6_single_file_upload_row_path_never_holds_double_colon(self) -> None:
        """R10-6: a non-archive upload named "t:::c.png" kept "::" in its row path
        (contract: a real file's path "never contains '::'"); it is escaped like
        every real file's row (R9-1) and ``name`` keeps the name sent."""
        from deepfake_lens import webapp_api
        from deepfake_lens.result_text import escape_row_path, unescape_row_path

        data = (Path(__file__).resolve().parents[2] / "fixtures" / "benchmark" / "a1111-metadata-marker.png").read_bytes()
        # (A "\\" in a multipart filename is a quoting escape, so names here have none.)
        names = ("t:::c.png", "x::y.png", "::::.png", "plain.png")
        content_type, body = self._multipart(*((name, data) for name in names))
        result = webapp_api._analyze_upload_payload(content_type, body)
        items = result["items"]
        self.assertEqual([item["name"] for item in items], list(names))
        self.assertEqual([item["path"] for item in items], [escape_row_path(name) for name in names])
        for item in items:
            self.assertNotIn("::", item["path"])
            self.assertEqual(unescape_row_path(item["path"]), item["name"])
        # /api/check (single file) — the same row path.
        boundary = "----r106"
        check_body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"t:::c.png\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        checked = webapp_api._check_file_payload(f"multipart/form-data; boundary={boundary}", check_body)
        self.assertEqual((checked["item"]["path"], checked["item"]["name"]), ("t\\:\\:\\:c.png", "t:::c.png"))
        # A failed upload row is escaped too.
        with patch.object(webapp_api, "analyze_path", side_effect=RuntimeError("boom")):
            content_type, body = self._multipart(("t:::c.png", data))
            failed = webapp_api._analyze_upload_payload(content_type, body)["items"]
        self.assertEqual([(item["path"], item["status"]) for item in failed], [("t\\:\\:\\:c.png", "failed")])

    def test_empty_upload_reports_error(self) -> None:
        from deepfake_lens.webapp_api import _analyze_upload_payload

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
        from deepfake_lens import webapp_api

        from deepfake_lens import analysis_api

        with patch.object(analysis_api, "analyze_file", self._fake_analyze):
            result = webapp_api._check_text_payload("인공지능 기술은 빠르게 발전하고 있습니다. " * 5)

        self.assertEqual(result["mode"], "text")
        self.assertIn("item", result)
        self.assertIn("advanced", result)
        # D1: the text-statistics layer is a layer diagnostic (raw numbers,
        # fixed notice, no band); its signals sit under "diagnostic".
        self.assertEqual(result["advanced"]["kind"], "layer_diagnostic")
        self.assertIn("signals", result["advanced"]["diagnostic"])
        self.assertNotIn("band", result["advanced"])

    def test_text_check_rejects_too_short(self) -> None:
        from deepfake_lens.webapp_api import _check_text_payload

        self.assertIn("error", _check_text_payload("짧음"))

    def test_file_check_runs_scan_and_forensic(self) -> None:
        from deepfake_lens import webapp_api

        from deepfake_lens import analysis_api

        with patch.object(analysis_api, "analyze_file", self._fake_analyze):
            content_type, body = self._multipart("note.txt", b"hello world, this is a test document")
            result = webapp_api._check_file_payload(content_type, body)

        self.assertEqual(result["mode"], "file")
        self.assertEqual(result["item"]["name"], "note.txt")
        self.assertIsNotNone(result["advanced"])

    def test_file_check_rejects_non_multipart(self) -> None:
        from deepfake_lens.webapp_api import _check_file_payload

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
            payload = webapp_api._feedback_payload(json.dumps({
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
            payload = webapp_api._feedback_payload(json.dumps({
                "path": "/tmp/x.png", "expected_label": "maybe",
            }).encode())
            self.assertIn("error", payload)

        self._with_feedback_path(run)

    def test_missing_path_rejected(self) -> None:
        def run(webapp):
            payload = webapp_api._feedback_payload(json.dumps({"expected_label": "real"}).encode())
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

    def test_token_accepts_api_token_alias(self) -> None:
        """Both servers accept either token header so one credential works."""
        from deepfake_lens.webapp import api_request_allowed

        self.assertTrue(api_request_allowed(self._headers({"X-API-Token": "s3cret"}), token="s3cret"))
        self.assertFalse(api_request_allowed(self._headers({"X-API-Token": "wrong"}), token="s3cret"))


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

    def test_gui_assets_and_csp_headers(self) -> None:
        """Extracted gui.css/gui.js are served and the shell carries the CSP."""
        import urllib.request

        url = self._start_server()
        with urllib.request.urlopen(url + "/", timeout=5) as response:
            self.assertEqual(response.status, 200)
            csp = response.headers.get("Content-Security-Policy", "")
            self.assertIn("script-src 'self'", csp)
            self.assertIn("style-src 'self'", csp)
            self.assertNotIn("unsafe-inline", csp)
            # S5: wav/mp3/mp4 previews are blob: object URLs in <audio>/<video>.
            self.assertIn("media-src 'self' blob:", csp)
            self.assertIn("img-src 'self' blob:", csp)
            html = response.read()
            self.assertIn(b'href="/gui.css"', html)
            self.assertIn(b'src="/gui.js"', html)
            self.assertNotIn(b"<script>", html)
            self.assertNotIn(b"<style>", html)

        with urllib.request.urlopen(url + "/gui.css", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "text/css")
            self.assertIn(b":root", response.read())

        with urllib.request.urlopen(url + "/gui.js", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("javascript", response.headers.get_content_type())
            self.assertGreater(len(response.read()), 1000)

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
        from deepfake_lens import webapp_api

        registry = patch.object(webapp_api, "_SCAN_JOBS", {})
        registry.start()
        self.addCleanup(registry.stop)
        roots = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)

    def test_job_lifecycle(self) -> None:
        import tempfile
        from deepfake_lens import webapp_api

        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "a.txt"
            fixture.write_text("hello world", encoding="utf-8")
            # G31: a scan may only target the server's own roots — here the
            # default folder (nothing registered), as `web --folder <tmp>`.
            started = webapp_api._scan_job_start(f"folder={tmp}&no_default_engine=true", default_folder=Path(tmp))
            self.assertEqual(started["status"], "running")
            job_id = started["job_id"]

            result = None
            for _ in range(200):
                state = webapp_api._scan_status_payload(f"job={job_id}")
                if state["status"] != "running":
                    result = state["result"]
                    break
                time.sleep(0.05)
            self.assertIsNotNone(result, "job did not finish")
            self.assertEqual(state["status"], "done", result)
            self.assertEqual(result["summary"]["total"], 1)

            # A second poll returns the stored result, and unknown ids are errors.
            again = webapp_api._scan_status_payload(f"job={job_id}")
            self.assertEqual(again["status"], "done")
            self.assertIn("error", webapp_api._scan_status_payload("job=deadbeef"))

    def test_missing_job_parameter_is_error(self) -> None:
        from deepfake_lens import webapp_api

        self.assertIn("error", webapp_api._scan_status_payload(""))

    def test_cancel_payload(self) -> None:
        import threading
        from deepfake_lens import webapp_api

        self.assertIn("error", webapp_api._scan_cancel_payload(""))
        self.assertIn("error", webapp_api._scan_cancel_payload("job=deadbeef"))

        cancel = threading.Event()
        with webapp_api._SCAN_JOBS_LOCK:
            webapp_api._SCAN_JOBS["job1"] = {"status": "running", "created": time.time(), "cancel": cancel}
        out = webapp_api._scan_cancel_payload("job=job1")
        self.assertTrue(out["cancelled"])
        self.assertTrue(cancel.is_set())

        # A finished job reports that there is nothing left to cancel.
        with webapp_api._SCAN_JOBS_LOCK:
            webapp_api._SCAN_JOBS["job2"] = {"status": "done", "created": time.time(), "cancel": threading.Event()}
        out = webapp_api._scan_cancel_payload("job=job2")
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
        from deepfake_lens import webapp_api

        with webapp_api._SCAN_JOBS_LOCK:
            for i in range(webapp_api._SCAN_JOB_MAX):
                webapp_api._SCAN_JOBS[f"fake{i}"] = {"status": "running", "created": time.time()}
        with self.assertRaises(ValueError):
            webapp_api._scan_job_start("folder=.&no_default_engine=true", default_folder=None)


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
        self.assertEqual(client.get("/api/health", headers={"x-deepfake-lens-token": "s3cret"}).status_code, 200)

    def test_host_allowlist_without_token(self) -> None:
        client = self._client()
        self.assertEqual(client.get("/api/health", headers={"host": "attacker.example.com"}).status_code, 403)
        self.assertEqual(client.get("/api/health", headers={"host": "localhost:8765"}).status_code, 200)

    def test_root_is_unauthenticated(self) -> None:
        client = self._client(token="s3cret")
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("version", response.json())

    def test_gui_serves_html(self) -> None:
        client = self._client(token="s3cret")
        res_gui = client.get("/gui")
        self.assertEqual(res_gui.status_code, 200)
        self.assertIn("<html", res_gui.text.lower())
        # S5: the same CSP as the stdlib server, media-src blob: included.
        from deepfake_lens.webapp import GUI_CSP

        self.assertEqual(res_gui.headers.get("content-security-policy"), GUI_CSP)
        self.assertIn("media-src 'self' blob:", GUI_CSP)

    def test_unified_api_stats(self) -> None:
        client = self._client(token="s3cret")
        res = client.get("/api/stats", headers={"x-deepfake-lens-token": "s3cret"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("status", data)


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
        # R9-3 (round 9): this expected an SSE "error" event inside a 200 stream
        # (encoded the defect); the request is refused with 400 before the stream.
        client = self._client()
        response = client.post("/api/check/stream", headers=self._SSE_HEADERS)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers["content-type"].split(";")[0], "application/json")
        self.assertEqual(response.json()["detail"], api_server.TEXT_OR_FILE_REQUIRED)

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

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.txt").write_text("안녕하세요 테스트 문서입니다.", encoding="utf-8")
            Path(tmp, "b.txt").write_text("다른 파일입니다.", encoding="utf-8")
            # G31: /api/scan/stream is confined to the read roots like
            # /api/scan; the scanned dir is the server's default folder
            # (no roots registered in this test).
            client = self._client(default_folder=Path(tmp))
            with patch.object(webapp_api, "_READ_ROOTS", OrderedDict()), client.stream(
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
        # R9-3 (round 9): this expected an SSE "error" event inside a 200 stream
        # (encoded the defect); every bad folder is refused before the stream.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "file.txt").write_text("파일", encoding="utf-8")
            client = self._client(default_folder=root)
            with patch.object(webapp_api, "_READ_ROOTS", OrderedDict()):
                for directory, status in (("", 400), (str(root / "gone"), 400), (str(root / "file.txt"), 400),
                                          ("C:/nonexistent-dir-xyz", 403)):
                    with self.subTest(directory=directory):
                        response = client.post("/api/scan/stream", params={"directory": directory}, headers=self._SSE_HEADERS)
                        self.assertEqual(response.status_code, status, response.text[:200])
                        self.assertEqual(response.headers["content-type"].split(";")[0], "application/json")
                        self.assertNotIn("event:", response.text)


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
        from deepfake_lens.webapp_api import _compare_payload

        text = "인공지능 기술은 빠르게 발전하고 있으며 다양한 산업에 적용된다. 또한 윤리 문제가 함께 논의된다. " * 8
        content_type, body = self._two_files("a.txt", text.encode(), "b.txt", text.encode())
        result = _compare_payload(content_type, body)
        # D1: similarity is a layer diagnostic — no same/different band.
        self.assertEqual(result.get("kind"), "layer_diagnostic")
        self.assertEqual(result["diagnostic"]["kind"], "stylometry")
        self.assertIn("raw_score", result)
        self.assertNotIn("band", result)
        self.assertNotIn("band", result["diagnostic"])

    def test_single_file_rejected(self) -> None:
        from deepfake_lens.webapp_api import _compare_payload

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
    """GET /api/preview serves media under a server-registered root only."""

    def setUp(self) -> None:
        from deepfake_lens import webapp_api

        # G31: the registry is an insertion-ordered OrderedDict (oldest
        # registration evicted first), no longer a set.
        registry = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        registry.start()
        self.addCleanup(registry.stop)

    def test_media_served_within_root(self) -> None:
        import tempfile
        from deepfake_lens.webapp_api import _preview_payload, _register_read_root

        with tempfile.TemporaryDirectory() as d:
            _register_read_root(Path(d))
            p = Path(d) / "a.png"
            p.write_bytes(b"\x89PNG\r\n\x1a\nfake")
            status, body, _, mime = _preview_payload(f"path={p}&root={d}")
            self.assertEqual(status, 200)
            self.assertEqual(mime, "image/png")
            self.assertEqual(body[:4], b"\x89PNG")

    def test_outside_root_forbidden(self) -> None:
        import tempfile
        from deepfake_lens.webapp_api import _preview_payload, _register_read_root

        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            _register_read_root(Path(d))
            p = Path(other) / "a.png"
            p.write_bytes(b"x")
            status, _, msg, _ = _preview_payload(f"path={p}&root={d}")
            self.assertEqual(status, 403)

    def test_non_media_forbidden(self) -> None:
        import tempfile
        from deepfake_lens.webapp_api import _preview_payload, _register_read_root

        with tempfile.TemporaryDirectory() as d:
            _register_read_root(Path(d))
            p = Path(d) / "a.txt"
            p.write_text("hi")
            status, body, code, _ = _preview_payload(f"path={p}&root={d}")
            # P8 (round 8): a non-media file inside the roots is refused as a bad
            # request (400, as the error table documents), not as a forbidden
            # path (403 stays for paths outside the roots — the test above).
            self.assertEqual((status, code), (400, "not-media"))
            self.assertIn("미리보기를 지원하지 않는 형식입니다: .txt", body.decode("utf-8"))
            self.assertNotIn(b"hi", body)

    def test_caller_supplied_root_cannot_widen_scope(self) -> None:
        """A forged root= must not grant access outside registered roots."""
        import tempfile
        from deepfake_lens.webapp_api import _preview_payload, _register_read_root

        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            _register_read_root(Path(d))
            p = Path(other) / "a.png"
            p.write_bytes(b"x")
            # Caller claims the parent dir as root — before the fix this
            # passed _is_within and served any file on the host.
            status, _, _, _ = _preview_payload(f"path={p}&root={other}")
            self.assertEqual(status, 403)

    def test_unregistered_root_rejected(self) -> None:
        import tempfile
        from deepfake_lens.webapp import _preview_payload

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.png"
            p.write_bytes(b"x")
            status, _, _, _ = _preview_payload(f"path={p}&root={d}")
            self.assertEqual(status, 403)

    def test_stale_root_argument_rejected(self) -> None:
        """path inside a registered root but root= pointing elsewhere -> 403."""
        import tempfile
        from deepfake_lens.webapp_api import _preview_payload, _register_read_root

        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            _register_read_root(Path(d))
            _register_read_root(Path(other))
            p = Path(d) / "a.png"
            p.write_bytes(b"x")
            status, _, _, _ = _preview_payload(f"path={p}&root={other}")
            self.assertEqual(status, 403)


class SummaryParityTest(unittest.TestCase):
    """core.summarize() and webapp_api._summarize_records() must agree —
    the same rows seen through CLI, upload, or report must report the same
    counts, or a corrupt archive could look analyzed on one surface and
    failed on another."""

    def _rows(self):
        return [
            {"path": "ok.png", "status": "analyzed",
             "result": {"band": "low", "verdict_code": "authenticity_evidence", "score": 3}},
            {"path": "sus.png", "status": "analyzed",
             "result": {"band": "high", "verdict_code": "manipulation_evidence", "score": 90, "model_analysis": {"available": True}}},
            {"path": "bad.zip", "status": "unknown", "kind": "archive",
             "result": {"band": "unknown", "score": 0}},
            {"path": "gone.bin", "status": "failed", "error": "unreadable"},
            {"path": "dup.png", "status": "duplicate"},
            {"path": "big.iso", "status": "skipped"},
        ]

    def test_web_summary_matches_core_contract(self):
        from deepfake_lens.webapp_api import _summarize_records
        summary = _summarize_records(self._rows(), "test")
        # R5: an archive container row with a result is counted by its
        # verdict (undetermined here) like the CLI table and GUI show it;
        # only the failed row lands in unsupported_or_failed.
        self.assertEqual(summary["total"], 6)
        self.assertEqual(summary["analyzed"], 3)
        # D16: verdict counts only — the legacy band keys are not serialized.
        self.assertEqual(summary["manipulation_evidence"], 1)
        self.assertEqual(summary["authenticity_evidence"], 1)
        self.assertEqual(summary["undetermined"], 1)
        for legacy in ("high", "medium", "low", "unknown"):
            self.assertNotIn(legacy, summary)
        self.assertEqual(summary["unsupported_or_failed"], 1)
        self.assertEqual(summary["duplicates"], 1)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["external_model_active"], 1)

    def _cr(self, score: int, band) -> "object":
        from deepfake_lens.result_types import ClassificationResult, SourceGuess
        return ClassificationResult(score=score, band=band,
                                    band_label=str(band), verdict="",
                                    signals=[], limitations=[],
                                    source_guess=SourceGuess.unknown(""),
                                    next_checks=[])

    def test_core_counts_same_scan_items(self):
        from deepfake_lens.core import summarize
        from deepfake_lens.result_types import RiskBand, ScanItem
        items = [
            ScanItem("ok.png", "ok.png", "image", "analyzed", 10,
                     self._cr(3, RiskBand.LOW)),
            ScanItem("sus.png", "sus.png", "image", "analyzed", 10,
                     self._cr(90, RiskBand.HIGH)),
            ScanItem("bad.zip", "bad.zip", "archive", "unknown", 10,
                     self._cr(0, RiskBand.UNKNOWN)),
            ScanItem("gone.bin", "gone.bin", "unknown", "failed", 0, error="unreadable"),
            ScanItem("dup.png", "dup.png", "image", "duplicate", 10),
            ScanItem("big.iso", "big.iso", "unknown", "skipped", 10),
        ]
        summary = summarize(items, capped=False)
        # R5: the archive container row is counted by its verdict/band.
        self.assertEqual(summary.analyzed, 3)
        self.assertEqual(summary.high, 1)
        self.assertEqual(summary.low, 1)
        self.assertEqual(summary.unknown, 1)
        self.assertEqual(summary.unsupported_or_failed, 1)
        self.assertEqual(summary.duplicates, 1)
        self.assertEqual(summary.skipped, 1)

    def test_serialized_rows_parity(self):
        """The web helper consumes ScanItem.to_json() rows — feed it exactly
        that and compare against core counts on the same items."""
        from deepfake_lens.core import summarize
        from deepfake_lens.result_types import RiskBand, ScanItem
        from deepfake_lens.webapp_api import _summarize_records
        items = [
            ScanItem("ok.png", "ok.png", "image", "analyzed", 10,
                     self._cr(3, RiskBand.LOW)),
            ScanItem("arc.zip", "arc.zip", "archive", "expanded", 10,
                     self._cr(50, RiskBand.MEDIUM)),
            ScanItem("arc.zip::a.png", "a.png", "image", "analyzed", 5,
                     self._cr(50, RiskBand.MEDIUM)),
        ]
        core = summarize(items, capped=False)
        web = _summarize_records([i.to_json() for i in items], "test")
        self.assertEqual(web["analyzed"], core.analyzed)
        self.assertEqual(web["undetermined"], core.undetermined)  # D16: verdict keys only
        self.assertEqual(web["manipulation_evidence"], core.manipulation_evidence)
        self.assertEqual(web["unsupported_or_failed"], core.unsupported_or_failed)


class ApiServeMissingDependenciesTest(unittest.TestCase):
    """G29: `api-serve` without fastapi/uvicorn exits 2 with a Korean hint, no traceback."""

    def test_api_serve_exits_2_with_install_hint(self) -> None:
        import contextlib
        import io

        from deepfake_lens import cli

        err = io.StringIO()
        with patch.object(api_server, "missing_server_dependencies", return_value=["fastapi", "uvicorn"]), \
                contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            # R10-5: was "--port 0" — the port is now checked first (1–65535,
            # exit 2 before anything else), so a valid port reaches the
            # dependency check this test is about.
            cli.main(["api-serve", "--port", "8765"])
        self.assertEqual(ctx.exception.code, 2)
        message = err.getvalue()
        self.assertIn("pip install fastapi uvicorn", message)
        self.assertIn("설치", message)
        self.assertNotIn("Traceback", message)

    def test_missing_dependencies_reflect_environment(self) -> None:
        expected = [name for name in api_server.SERVER_DEPENDENCIES if importlib.util.find_spec(name) is None]
        self.assertEqual(api_server.missing_server_dependencies(), expected)


@unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed")
class ApiServerHardeningTest(unittest.TestCase):
    """G8/G34 on the FastAPI server: preview/heatmap tuples, nosniff, client
    header on scan GETs, job cap (429), request paths confined (400)."""

    _LOCAL = {"host": "localhost"}

    def setUp(self) -> None:
        import tempfile

        from fastapi.testclient import TestClient

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        roots = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        webapp_api.configure_read_roots(self.root)
        self.client = TestClient(api_server.create_app(default_folder=self.root))

    def _gui(self) -> dict[str, str]:
        return {**self._LOCAL, api_server.CLIENT_HEADER: "gui"}

    def test_scan_get_requires_client_header(self) -> None:
        for path in ("/api/scan", "/api/scan-cancel?job=x", "/api/scan-status?job=x"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, headers=self._LOCAL).status_code, 401)
        ok = self.client.get("/api/scan", params={"folder": str(self.root), "no_default_engine": "true"}, headers=self._gui())
        self.assertEqual(ok.status_code, 200)

    def test_preview_unpacks_status_data_message_mime(self) -> None:
        png = self.root / "a.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        res = self.client.get("/api/preview", params={"path": str(png)}, headers=self._gui())
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "image/png")
        self.assertEqual(res.headers["x-content-type-options"], "nosniff")
        self.assertEqual(res.content, png.read_bytes())
        denied = self.client.get("/api/preview", params={"path": "/etc/hostname"}, headers=self._gui())
        self.assertEqual(denied.status_code, 403)
        self.assertTrue(denied.headers["content-type"].startswith("text/plain"))
        self.assertEqual(denied.headers["x-content-type-options"], "nosniff")

    def test_heatmap_media_type_is_png_or_text(self) -> None:
        denied = self.client.get("/api/heatmap", params={"path": "/etc/x.png"}, headers=self._gui())
        self.assertEqual(denied.status_code, 403)
        self.assertTrue(denied.headers["content-type"].startswith("text/plain"))
        self.assertEqual(denied.headers["x-content-type-options"], "nosniff")

    def test_job_registry_cap_returns_429(self) -> None:
        with patch.object(api_server, "MAX_JOBS", 0):
            res = self.client.post("/api/check/stream", params={"text": "테스트 문장입니다. " * 4}, headers=self._gui())
        self.assertEqual(res.status_code, 429)

    def test_model_path_outside_models_dir_is_400(self) -> None:
        res = self.client.get("/api/scan", params={"folder": str(self.root), "model_path": "/etc/passwd"}, headers=self._gui())
        self.assertEqual(res.status_code, 400)


SECRET_BYTES = b"G31-API-SERVER-SECRET-do-not-read"


class ApiServerConfinementUnitTest(unittest.TestCase):
    """G31: api_server.confine_request_path — the check every file-reading
    API endpoint runs. Stdlib only (runs without fastapi)."""

    def setUp(self) -> None:
        import tempfile

        roots = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        self.root, self.outside = base / "case", base / "elsewhere"
        self.root.mkdir()
        self.outside.mkdir()
        (self.root / "memo.txt").write_text("사건 메모", encoding="utf-8")
        (self.outside / "secret.txt").write_bytes(SECRET_BYTES)
        webapp_api.configure_read_roots(self.root)

    def test_inside_root_resolves(self) -> None:
        self.assertEqual(api_server.confine_request_path(str(self.root / "memo.txt")), self.root / "memo.txt")
        self.assertEqual(api_server.confine_request_path(str(self.root)), self.root)

    def test_outside_traversal_and_symlink_are_denied(self) -> None:
        link = self.root / "link.txt"
        try:
            link.symlink_to(self.outside / "secret.txt")
        except OSError:
            link = self.outside / "secret.txt"  # no symlink privilege (Windows)
        for text in (str(self.outside / "secret.txt"), f"{self.root}/../elsewhere/secret.txt", "/", str(link)):
            with self.subTest(path=text), self.assertRaises(webapp_api.ReadRootDenied):
                api_server.confine_request_path(text)

    def test_default_folder_only_when_nothing_registered(self) -> None:
        webapp_api._READ_ROOTS.clear()
        self.assertEqual(api_server.confine_request_path(str(self.root / "memo.txt"), self.root), self.root / "memo.txt")
        with self.assertRaises(webapp_api.ReadRootDenied):
            api_server.confine_request_path(str(self.outside / "secret.txt"), self.root)


@unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed")
class ApiServerFilePathConfinementTest(ApiServerConfinementUnitTest):
    """G31 on the FastAPI server: every endpoint that takes ``file_path`` /
    ``directory`` is confined to the registered read roots like /api/scan —
    403 {"error": "허용되지 않은 경로"} outside, no file content echoed."""

    _HEADERS = {"host": "localhost", api_server.CLIENT_HEADER: "test"}

    def setUp(self) -> None:
        super().setUp()
        from fastapi.testclient import TestClient

        self.client = TestClient(api_server.create_app(default_folder=self.root))

    def _post(self, path: str, params: dict[str, str]) -> Any:
        return self.client.post(path, params=params, headers=self._HEADERS)

    def _requests(self, target: Path) -> list[tuple[str, dict[str, str]]]:
        file_endpoints = ["/api/analyze/image", "/api/analyze/audio", "/api/analyze/face", "/api/analyze/forensic", "/api/classify", "/api/check", "/api/check/stream"]
        requests = [(endpoint, {"file_path": str(target)}) for endpoint in file_endpoints]
        requests.append(("/api/scan/stream", {"directory": str(target.parent)}))
        requests.append(("/api/compare", {"file_path_a": str(self.root / "memo.txt"), "file_path_b": str(target)}))
        requests.append(("/api/compare", {"file_path_a": str(target), "file_path_b": str(self.root / "memo.txt")}))
        return requests

    def test_every_file_endpoint_refuses_paths_outside_the_roots(self) -> None:
        for endpoint, params in self._requests(self.outside / "secret.txt"):
            with self.subTest(endpoint=endpoint, params=params):
                res = self._post(endpoint, params)
                self.assertEqual(res.status_code, 403, res.text[:300])
                self.assertEqual(res.json(), {"error": "허용되지 않은 경로"})
                self.assertNotIn(SECRET_BYTES, res.content)

    def test_traversal_out_of_the_root_is_refused(self) -> None:
        sneaky = Path(f"{self.root}/../elsewhere/secret.txt")
        for endpoint, params in self._requests(sneaky):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self._post(endpoint, params).status_code, 403)

    def test_refused_request_starts_no_job(self) -> None:
        # Confinement runs before job registration: with a zero job cap a
        # refused path is 403 (not 429), so no job slot was ever taken.
        with patch.object(api_server, "MAX_JOBS", 0):
            self.assertEqual(self._post("/api/scan/stream", {"directory": str(self.outside)}).status_code, 403)
            self.assertEqual(self._post("/api/check/stream", {"file_path": str(self.outside / "secret.txt")}).status_code, 403)

    def test_inside_the_root_still_works(self) -> None:
        inside = self.root / "memo.txt"
        for endpoint in ("/api/analyze/image", "/api/check", "/api/classify"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self._post(endpoint, {"file_path": str(inside)}).status_code, 200)
        res = self._post("/api/scan/stream", {"directory": str(self.root)})
        self.assertEqual(res.status_code, 200)
        self.assertIn("event: result", res.text)
        self.assertIn("memo.txt", res.text)


HAVE_PYMUPDF = importlib.util.find_spec("pymupdf") is not None


def _write_png(path: Path, seed: int) -> Path:
    """A small valid RGB PNG (stdlib only) whose bytes depend on ``seed``."""
    import struct
    import zlib

    width = height = 32

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + bytes(((x * 7 + y * 3 + seed) % 256) for x in range(width) for _ in range(3)) for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return path


class _ReportLegsFixture(unittest.TestCase):
    """Scan fixture and the two server legs (stdlib web, api-serve) for /api/report tests."""

    HEADERS = {"X-Deepfake-Lens-Client": "gui", "Content-Type": "application/json"}

    def setUp(self) -> None:
        import contextlib
        import io
        import tempfile
        import zipfile

        from deepfake_lens.cli import main as cli_main

        roots = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name).resolve() / "case"
        self.folder.mkdir()
        _write_png(self.folder / "target.png", seed=1)
        os.symlink("target.png", self.folder / "in_link.png")
        with zipfile.ZipFile(self.folder / "bundle.zip", "w") as archive:
            archive.writestr("inner/member.png", _write_png(Path(self._tmp.name) / "m.png", seed=2).read_bytes())
            archive.writestr("notes.txt", "메모입니다.")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli_main(["scan", str(self.folder), "--format", "json"]), 0)
        self.scan = json.loads(out.getvalue())
        self.rows = {row["path"]: row for row in self.scan["items"]}
        import hashlib

        self.target_sha = hashlib.sha256((self.folder / "target.png").read_bytes()).hexdigest()
        self.member_sha = hashlib.sha256(_write_png(Path(self._tmp.name) / "m2.png", seed=2).read_bytes()).hexdigest()
        # The scan itself: link row unhashed, member rows hashed.
        self.assertIsNone(self.rows["in_link.png"].get("sha256"))
        self.assertEqual(self.rows["target.png"]["sha256"], self.target_sha)
        self.assertEqual(self.rows["bundle.zip::inner/member.png"]["sha256"], self.member_sha)
        self.assertTrue(self.rows["bundle.zip::notes.txt"]["sha256"])
        # R19-13 (round 19): the rows are posted as the scan wrote them. This fixture posted junk digests
        # ("0" * 64) to show they are never trusted — the server signed its own digests silently; a posted
        # row that differs from the server's re-analysis is now refused (409, ReportRefusesChangedRowsTest).
        self.posted = [dict(row) for row in self.scan["items"]]

    # -- legs ---------------------------------------------------------------

    def _stdlib_leg(self):
        import threading
        import urllib.error
        import urllib.request

        from deepfake_lens.webapp import build_server

        server = build_server("127.0.0.1", 0, default_folder=self.folder)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        def post(path: str, body: bytes) -> tuple[int, str, bytes]:
            request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, headers=self.HEADERS, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    return response.status, response.headers.get("Content-Type", ""), response.read()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.headers.get("Content-Type", ""), exc.read()

        return post

    def _fastapi_leg(self):
        from fastapi.testclient import TestClient

        client = TestClient(api_server.create_app(default_folder=self.folder))

        def post(path: str, body: bytes) -> tuple[int, str, bytes]:
            response = client.post(path, content=body, headers={"host": "localhost", **self.HEADERS})
            return response.status_code, response.headers.get("content-type", ""), response.content

        return post

    def _legs(self):
        legs = [("web", self._stdlib_leg)]
        if HAVE_FASTAPI:
            legs.append(("api", self._fastapi_leg))
        return legs

    def _body(self, **extra: object) -> bytes:
        return self._json({"items": self.posted, **extra})

    def _json(self, payload: dict[str, Any]) -> bytes:
        """P1 (round 8): a report request names the scanned folder (the scan's scan_root)."""
        return json.dumps({"scan_root": str(self.folder), **payload}).encode("utf-8")


class ReportEvidenceHashesBothServersTest(_ReportLegsFixture):
    """N2/N9/N10/N11: /api/report on the stdlib web server and on api-serve.

    N2: the report re-hashed rows with ``resolve()``, so a symbolic-link row
    (``in_link.png -> target.png``) got its target's digest in the signed
    body and both PDFs, while the scan recorded null / "해시 불가(심볼릭
    링크)". N10: archive members were "해시 불가(압축 파일 구성원…)" on the
    web PDFs while the CLI PDF showed the member digest. N9: the stdlib
    server ignored the body's ``format`` and sent PDF bytes as text/html.
    N11: malformed requests were 200 + ``{"error"}`` and ``{"items":
    [{"path": 3}]}`` rendered an HTML report.
    """


    # -- N2/N10: signed body ------------------------------------------------

    def test_signed_body_keeps_symlink_unhashed_and_hashes_members(self) -> None:
        for name, make in self._legs():
            with self.subTest(leg=name):
                post = make()
                status, content_type, raw = post("/api/report?format=json", self._body())
                self.assertEqual(status, 200, raw[:300])
                self.assertIn("application/json", content_type)
                signed = {row["path"]: row for row in json.loads(raw)["items"]}
                self.assertIsNone(signed["in_link.png"]["sha256"])  # N2: not target.png's digest
                self.assertEqual(signed["target.png"]["sha256"], self.target_sha)
                self.assertEqual(signed["bundle.zip::inner/member.png"]["sha256"], self.member_sha)  # N10
                self.assertEqual(signed["bundle.zip::notes.txt"]["sha256"], self.rows["bundle.zip::notes.txt"]["sha256"])
                self.assertEqual(signed["bundle.zip"]["sha256"], self.rows["bundle.zip"]["sha256"])
                self.assertNotIn("0" * 64, raw.decode("utf-8"))  # posted digests are recomputed

    def test_symlink_component_on_the_path_is_not_followed(self) -> None:
        """A row path through a linked folder (``linked/target.png``) is not hashed either.

        X2: nor re-analyzed — the row is left out of the signed body and listed
        under ``excluded_items`` with the reason."""
        os.symlink(self.folder, self.folder / "linked")
        # R20-5 (round 20): this row carried the scan's sha256 of target.png — a row the scan hashed that is now
        # behind a linked folder is a changed row (409, ReportRefusesAFolderLinkedAfterTheScanTest). A row the scan
        # did not hash keeps N2's rule checked here: excluded, the link never followed or hashed.
        row = dict(self.rows["target.png"], path="linked/target.png", name="target.png", sha256=None)
        for name, make in self._legs():
            with self.subTest(leg=name):
                status, _, raw = make()("/api/report?format=json", self._json({"items": [row]}))
                self.assertEqual(status, 200, raw[:300])
                payload = json.loads(raw)
                self.assertEqual(payload["items"], [])
                self.assertEqual(payload["excluded_items"], [{
                    "path": "linked/target.png", "marker": webapp_api.UNSIGNED_CLIENT_ROW_MARKER,
                    "reason": webapp_api.REPORT_ROW_LINK_ON_PATH,
                }])
                self.assertNotIn(self.target_sha, raw.decode("utf-8"))
        hashed = dict(row, sha256=self.target_sha)
        for name, make in self._legs():
            with self.subTest(leg=name, posted="hashed"):
                status, _, raw = make()("/api/report?format=json", self._json({"items": [hashed]}))
                self.assertEqual(status, 409, raw[:300])
                [entry] = json.loads(raw)["changed_rows"]
                self.assertIsNone(entry["server_sha256"])  # still never hashed through the link

    # -- N2/N10/N9: both PDFs ----------------------------------------------

    @unittest.skipUnless(HAVE_PYMUPDF, "pymupdf required for PDF text")
    def test_both_pdfs_show_symlink_unhashed_and_member_digests(self) -> None:
        import pymupdf

        from deepfake_lens.result_text import HASH_UNAVAILABLE_SYMLINK

        for name, make in self._legs():
            post = make()
            for fmt in ("pdf", "evidence"):
                with self.subTest(leg=name, format=fmt):
                    # N9: the format travels in the JSON body only.
                    status, content_type, raw = post("/api/report", self._body(format=fmt))
                    self.assertEqual(status, 200, raw[:300])
                    self.assertEqual(content_type, "application/pdf")
                    self.assertTrue(raw.startswith(b"%PDF-"))
                    with pymupdf.open(stream=raw, filetype="pdf") as doc:
                        text = "\n".join(page.get_text() for page in doc)
                    flat = "".join(text.split())
                    # N2: the target's digest appears once (its own row), never for the link.
                    self.assertEqual(flat.count(self.target_sha), 1, text)
                    self.assertIn("".join(HASH_UNAVAILABLE_SYMLINK.split())[:20], flat)
                    # N10: the member digest is printed; no member row is "해시 불가(압축 파일 구성원".
                    self.assertIn(self.member_sha, flat)
                    self.assertNotIn("해시불가(압축파일구성원", flat)
                    self.assertNotIn("0" * 64, flat)

    # -- N9: format in the body, HTML default ---------------------------------

    def test_format_from_body_sets_content_type(self) -> None:
        for name, make in self._legs():
            post = make()
            with self.subTest(leg=name):
                status, content_type, raw = post("/api/report", self._body())
                self.assertEqual(status, 200)
                self.assertIn("text/html", content_type)
                status, content_type, raw = post("/api/report", self._body(format="json"))
                self.assertEqual(status, 200)
                self.assertIn("application/json", content_type)
                self.assertIn("items", json.loads(raw))
                # ?format= wins over the body
                status, content_type, _ = post("/api/report?format=html", self._body(format="json"))
                self.assertIn("text/html", content_type)

    # -- N11: malformed requests are 400 + Korean --------------------------------

    def test_malformed_requests_are_400_in_korean(self) -> None:
        from deepfake_lens.error_text import english_prose

        bad: list[tuple[bytes, str]] = [
            (b"{not json", "JSON 본문을 해석할 수 없습니다"),
            (b"[1, 2]", "보고서 요청 본문은 JSON 객체여야 합니다"),
            (b"{}", "보고서에 넣을 검사 결과 항목(items 배열)이 필요합니다"),
            (b'{"items": []}', "보고서에 넣을 검사 결과 항목(items 배열)이 필요합니다"),
            (b'{"items": [{"path": 3}]}', "검사 결과 항목 1번을 해석할 수 없습니다: "),
            (json.dumps({"items": [dict(self.posted[0], path=3)]}).encode(), "검사 결과 항목 1번을 해석할 수 없습니다: `path` 값은 문자열이어야 합니다"),
            (json.dumps({"items": ["x"]}).encode(), "검사 결과 항목 1번을 해석할 수 없습니다: 항목은 JSON 객체여야 합니다"),
            (json.dumps({"items": [dict(self.posted[0], size_bytes="9")]}).encode(), "`size_bytes` 값은 정수이어야 합니다"),
            (json.dumps({"items": [dict(self.posted[0], sha256="abc")]}).encode(), "`sha256` 값은 64자리"),
            (json.dumps({"items": self.posted, "format": "docx"}).encode(), "지원되지 않는 보고서 형식입니다: 「docx」"),
            (json.dumps({"items": self.posted, "format": 3}).encode(), "`format` 값은 문자열이어야 합니다"),
            (json.dumps({"items": self.posted, "thresholds": 3}).encode(), "thresholds 값은 JSON 객체여야 합니다"),
        ]
        broken_result = dict(self.posted[0])
        broken_result["result"] = dict(broken_result["result"] or {}, verdict_code="bogus")
        bad.append((json.dumps({"items": [broken_result]}).encode(), "`result.verdict_code` 값이 허용 목록"))
        for name, make in self._legs():
            post = make()
            for body, expected in bad:
                with self.subTest(leg=name, body=body[:60]):
                    status, content_type, raw = post("/api/report", body)
                    self.assertEqual(status, 400, raw[:300])
                    self.assertIn("application/json", content_type)
                    error = json.loads(raw)["error"]
                    self.assertIn(expected, error)
                    self.assertIsNone(english_prose(error), error)
                    self.assertNotIn(b"<html", raw)


class ReportSignsOnlyServerResultsTest(_ReportLegsFixture):
    """X2 (round 7): /api/report signed whatever conclusion and evidence the
    client posted — target.png's row with midjourney.png's verdict and
    evidence came back signed, with the real SHA-256, and verify-report said
    "검증됨". Every row is now re-analyzed on the server; only server results
    are signed, and a row that cannot be re-analyzed is excluded and marked."""

    def setUp(self) -> None:
        super().setUp()
        from deepfake_lens.cli import main as cli_main
        from deepfake_lens.tests.qa.test_qa_sys import _write_generator_png

        import contextlib
        import io

        generated = self.folder / "generated.png"
        _write_generator_png(generated)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli_main(["scan", str(self.folder), "--format", "json"]), 0)
        self.rows = {row["path"]: row for row in json.loads(out.getvalue())["items"]}
        self.assertEqual(self.rows["generated.png"]["result"]["verdict_code"], "manipulation_evidence")
        self.assertNotEqual(self.rows["target.png"]["result"]["verdict_code"], "manipulation_evidence")

    def _forged(self) -> dict[str, Any]:
        import copy

        row = copy.deepcopy(self.rows["target.png"])
        row["result"] = copy.deepcopy(self.rows["generated.png"]["result"])
        return row

    def test_forged_row_is_replaced_by_the_server_result(self) -> None:
        """R19-13 (round 19): this test expected the forged row to be signed silently with the server's verdict
        (200). A posted row whose verdict differs from the server's re-analysis is now refused — 409 with the
        difference — and nothing the client said is signed either way; the scan's own row is signed as the
        server's result."""
        from deepfake_lens.signing import REPORT_KEY_ENV, verify_report

        key = "x2-server-key-0123456789abcdef"
        forged = self._json({"items": [self._forged()], "format": "json"})
        genuine = self._json({"items": [self.rows["target.png"]], "format": "json"})
        for name, make in self._legs():
            with self.subTest(leg=name), patch.dict(os.environ, {REPORT_KEY_ENV: key}):
                status, _, raw = make()("/api/report", forged)
                self.assertEqual(status, 409, raw[:300])
                refused = json.loads(raw)
                self.assertEqual(refused["error"], webapp_api.REPORT_ROWS_CHANGED)
                [changed] = refused["changed_rows"]
                self.assertEqual(changed["path"], "target.png")
                self.assertEqual(changed["posted_verdict"], "manipulation_evidence")
                self.assertEqual(changed["server_verdict"], self.rows["target.png"]["result"]["verdict_code"])
                self.assertNotIn("signature", refused)
                status, _, raw = make()("/api/report", genuine)
                self.assertEqual(status, 200, raw[:300])
                signed = json.loads(raw)
                [row] = signed["items"]
                self.assertEqual(row["path"], "target.png")
                self.assertEqual(row["sha256"], self.target_sha)
                # The server's own verdict and evidence for target.png.
                self.assertEqual(row["result"]["verdict_code"], self.rows["target.png"]["result"]["verdict_code"])
                self.assertEqual(
                    [item["title"] for item in row["result"]["evidence"]],
                    [item["title"] for item in self.rows["target.png"]["result"]["evidence"]],
                )
                self.assertEqual(signed["excluded_items"], [])
                self.assertTrue(verify_report(signed, key.encode()).verified)

    def test_rows_the_server_cannot_reanalyze_are_excluded_and_marked(self) -> None:
        """R19-13 (round 19): a row whose file is gone ("gone.png", not found under the scan folder) was moved to
        excluded_items and the rest signed (200). A file missing since the scan is a changed row: every format
        is refused with 409 and the row's reason. (A row the server never re-analyzes — a linked folder on the
        path, an upload — is still excluded and marked: test_symlink_component_on_the_path_is_not_followed,
        the upload tests.)"""
        import copy

        ghost = copy.deepcopy(self.rows["generated.png"])
        ghost.update(path="gone.png", name="gone.png")
        rows = [self.rows["target.png"], ghost]
        for name, make in self._legs():
            post = make()
            # pdf without pymupdf: 501 first (B8); R22-7: the evidence statement too (it re-analyzed, then 500)
            for fmt in ("json", "html", *(("evidence", "pdf") if HAVE_PYMUPDF else ())):
                with self.subTest(leg=name, format=fmt):
                    status, content_type, raw = post("/api/report", self._json({"items": rows, "format": fmt}))
                    self.assertEqual(status, 409, raw[:300])
                    self.assertIn("application/json", content_type)
                    [changed] = json.loads(raw)["changed_rows"]
                    self.assertEqual(changed["path"], "gone.png")
                    self.assertEqual(changed["reason"], webapp_api.REPORT_ROW_NOT_FOUND)
                    self.assertEqual((changed["posted_verdict"], changed["server_verdict"]), ("manipulation_evidence", None))

    def test_report_options_are_validated(self) -> None:
        for name, make in self._legs():
            post = make()
            for options, expected in (("deep", "options 값은 JSON 객체여야 합니다"), ({"pixel": "turbo"}, "pixel은 ")):
                with self.subTest(leg=name, options=options):
                    status, _, raw = post("/api/report", self._json({"items": [self._forged()], "options": options}))
                    self.assertEqual(status, 400, raw[:300])
                    self.assertIn(expected, json.loads(raw)["error"])


class ReportRefusesChangedRowsTest(_ReportLegsFixture):
    """R19-13 (round 19): /api/report re-analyzed every posted row and signed the re-analysis without comparing
    it with the row. A file replaced after the scan (generator metadata gone), an archive member replaced, a
    file deleted, or a dedupe duplicate re-analyzed as a full row was signed with another verdict — 200, and
    verify-report said 검증됨. Now any difference in sha256, status or verdict_code, or a row whose file can no
    longer be re-analyzed, refuses every signed format with 409 and ``changed_rows``."""

    # R22-7 (round 22): without pymupdf the evidence statement is 501 before any re-analysis, like pdf (B8).
    FORMATS = ("json", "html", *(("evidence", "pdf") if HAVE_PYMUPDF else ()))

    def _scan(self, *extra: str) -> dict[str, dict[str, Any]]:
        import contextlib
        import io

        from deepfake_lens.cli import main as cli_main

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli_main(["scan", str(self.folder), "--format", "json", *extra]), 0)
        return {row["path"]: row for row in json.loads(out.getvalue())["items"]}

    def _assert_refused(self, rows: list[dict[str, Any]], path: str) -> list[dict[str, Any]]:
        changed_rows: list[dict[str, Any]] = []
        for name, make in self._legs():
            post = make()
            for fmt in self.FORMATS:
                with self.subTest(leg=name, format=fmt):
                    status, content_type, raw = post("/api/report", self._json({"items": rows, "format": fmt}))
                    self.assertEqual(status, 409, raw[:300])
                    self.assertIn("application/json", content_type)
                    refused = json.loads(raw)
                    self.assertEqual(refused["error"], webapp_api.REPORT_ROWS_CHANGED)
                    self.assertIn(path, [entry["path"] for entry in refused["changed_rows"]])
                    for entry in refused["changed_rows"]:
                        self.assertEqual(
                            set(entry) - {"reason"},
                            {"path", "posted_sha256", "server_sha256", "posted_status", "server_status", "posted_verdict", "server_verdict"},
                        )
                    changed_rows = refused["changed_rows"]
        return changed_rows

    def test_a_file_replaced_after_the_scan_is_409_in_every_format(self) -> None:
        from deepfake_lens.tests.qa.test_qa_sys import _write_generator_png

        generated = self.folder / "generated.png"
        _write_generator_png(generated)
        rows = self._scan()
        self.assertEqual(rows["generated.png"]["result"]["verdict_code"], "manipulation_evidence")
        _write_png(generated, seed=7)  # a plain image now: no generator metadata
        changed = self._assert_refused([rows["target.png"], rows["generated.png"]], "generated.png")
        [entry] = changed
        self.assertEqual(entry["posted_sha256"], rows["generated.png"]["sha256"])
        self.assertNotEqual(entry["server_sha256"], entry["posted_sha256"])
        self.assertEqual(entry["posted_verdict"], "manipulation_evidence")
        self.assertNotEqual(entry["server_verdict"], "manipulation_evidence")

    def test_an_archive_member_replaced_after_the_scan_is_409(self) -> None:
        import zipfile

        rows = self._scan()
        with zipfile.ZipFile(self.folder / "bundle.zip", "w") as archive:
            archive.writestr("inner/member.png", _write_png(Path(self._tmp.name) / "other.png", seed=9).read_bytes())
            archive.writestr("notes.txt", "메모입니다.")
        changed = self._assert_refused(list(rows.values()), "bundle.zip::inner/member.png")
        member = next(entry for entry in changed if entry["path"] == "bundle.zip::inner/member.png")
        self.assertEqual(member["posted_sha256"], self.member_sha)
        self.assertNotEqual(member["server_sha256"], self.member_sha)

    def test_a_file_deleted_after_the_scan_is_409(self) -> None:
        rows = self._scan()
        (self.folder / "target.png").unlink()
        changed = self._assert_refused([rows["target.png"], rows["bundle.zip::notes.txt"]], "target.png")
        [entry] = changed
        self.assertEqual(entry["reason"], webapp_api.REPORT_ROW_NOT_FOUND)
        self.assertIsNone(entry["server_status"])

    def test_a_dedupe_duplicate_stays_a_duplicate_and_its_change_is_409(self) -> None:
        (self.folder / "zcopy.png").write_bytes((self.folder / "target.png").read_bytes())
        rows = self._scan("--dedupe")
        duplicate = rows["zcopy.png"]
        self.assertEqual((duplicate["status"], duplicate["duplicate_of"]), ("duplicate", "target.png"))
        posted = [rows["target.png"], duplicate]
        for name, make in self._legs():
            with self.subTest(leg=name):
                status, _, raw = make()("/api/report", self._json({"items": posted, "format": "json"}))
                self.assertEqual(status, 200, raw[:300])
                signed = {row["path"]: row for row in json.loads(raw)["items"]}
                self.assertEqual(signed["zcopy.png"]["status"], "duplicate")  # J-2: not a full analysis of the copy
                self.assertEqual(signed["zcopy.png"]["duplicate_of"], "target.png")
                self.assertIsNone(signed["zcopy.png"]["result"])
                self.assertEqual(signed["zcopy.png"]["sha256"], self.target_sha)
        _write_png(self.folder / "zcopy.png", seed=5)  # the copy is no longer a duplicate
        [entry] = self._assert_refused(posted, "zcopy.png")
        self.assertEqual((entry["posted_status"], entry["server_status"]), ("duplicate", "analyzed"))

    def test_junk_posted_digests_are_409(self) -> None:
        rows = self._scan()
        junk = [dict(row, sha256="0" * 64) if row.get("sha256") else dict(row) for row in rows.values()]
        changed = self._assert_refused(junk, "target.png")
        self.assertEqual({entry["posted_sha256"] for entry in changed}, {"0" * 64})

    def test_unchanged_rows_are_signed_as_before(self) -> None:
        from deepfake_lens.signing import REPORT_KEY_ENV, verify_report

        rows = self._scan()
        key = "r19-13-key-0123456789abcdef"
        for name, make in self._legs():
            post = make()
            with self.subTest(leg=name), patch.dict(os.environ, {REPORT_KEY_ENV: key}):
                status, _, first = post("/api/report", self._json({"items": list(rows.values()), "format": "json"}))
                self.assertEqual(status, 200, first[:300])
                status, _, second = post("/api/report", self._json({"items": list(rows.values()), "format": "json"}))
                self.assertEqual(first, second)
                signed = json.loads(first)
                self.assertNotIn("changed_rows", signed)
                self.assertTrue(verify_report(signed, key.encode()).verified)
                self.assertEqual({row["path"]: row["sha256"] for row in signed["items"]}, {path: row.get("sha256") for path, row in rows.items()})


class ReportSignsSpecialEntriesAsScannedTest(_ReportLegsFixture):
    """R20-1 (round 20): R19-13 counted every posted row the server could not "find" as changed. A FIFO, a socket
    or an unreadable subfolder — listed by the scan as skipped / failed, never analyzed — was "not found" (the
    report only located regular files and links), so every report of such a folder was 409 in every format on
    both servers (2130892: 200). The server now classifies the entry the way the scan's walk does and compares
    like with like; a present entry is never "not found". The R19-13 refusals still hold (a file replaced by a
    FIFO after the scan is 409)."""

    FORMATS = ReportRefusesChangedRowsTest.FORMATS
    _scan = ReportRefusesChangedRowsTest._scan
    _assert_refused = ReportRefusesChangedRowsTest._assert_refused

    def _special_entries(self) -> list[str]:
        import socket

        os.mkfifo(self.folder / "pipe.jpg")
        names = ["pipe.jpg"]
        sock = socket.socket(socket.AF_UNIX)
        self.addCleanup(sock.close)
        try:
            sock.bind(str(self.folder / "sock.png"))
            names.append("sock.png")
        except OSError:  # a temp path longer than sun_path (macOS: 104 bytes)
            pass
        return names

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO")
    def test_a_fifo_and_a_socket_are_signed_as_the_scan_listed_them(self) -> None:
        names = self._special_entries()
        rows = self._scan()
        for name in names:
            self.assertEqual(rows[name]["status"], "skipped")
        posted = list(rows.values())
        for leg, make in self._legs():
            post = make()
            for fmt in self.FORMATS:
                with self.subTest(leg=leg, format=fmt):
                    status, _, raw = post("/api/report", self._json({"items": posted, "format": fmt}))
                    self.assertNotEqual(status, 409, raw[:400])
                    if fmt in ("json", "html") or HAVE_PYMUPDF:
                        self.assertEqual(status, 200, raw[:300])
            status, _, raw = post("/api/report", self._json({"items": posted, "format": "json"}))
            signed = {row["path"]: row for row in json.loads(raw)["items"]}
            for name in names:
                self.assertEqual((signed[name]["status"], signed[name]["error"]), (rows[name]["status"], rows[name]["error"]))

    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() != 0, "root ignores folder modes; POSIX modes")
    def test_an_unreadable_subfolder_is_signed_as_the_scans_failed_row(self) -> None:
        locked = self.folder / "locked"
        locked.mkdir()
        _write_png(locked / "in.png", seed=3)
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        rows = self._scan("--recursive")
        self.assertEqual(rows["locked"]["status"], "failed")
        for leg, make in self._legs():
            with self.subTest(leg=leg):
                status, _, raw = make()("/api/report", self._json({"items": list(rows.values()), "format": "json"}))
                self.assertEqual(status, 200, raw[:400])
                signed = {row["path"]: row for row in json.loads(raw)["items"]}
                self.assertEqual(signed["locked"]["status"], "failed")
        locked.chmod(0o755)  # readable now: the folder row no longer matches what is there
        [entry] = self._assert_refused(list(rows.values()), "locked")
        self.assertEqual(entry["reason"], webapp_api.REPORT_ROW_KIND_CHANGED)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO")
    def test_a_file_replaced_by_a_fifo_after_the_scan_is_still_409(self) -> None:
        """The R19-13 defect stays closed: an analyzed row whose file is now a FIFO differs (analyzed → skipped)."""
        rows = self._scan()
        (self.folder / "target.png").unlink()
        os.mkfifo(self.folder / "target.png")
        [entry] = self._assert_refused([rows["target.png"], rows["bundle.zip::notes.txt"]], "target.png")
        self.assertEqual((entry["posted_status"], entry["server_status"]), ("analyzed", "skipped"))
        self.assertNotIn("reason", entry)

    def test_a_missing_file_is_still_not_found(self) -> None:
        rows = self._scan()
        (self.folder / "target.png").unlink()
        [entry] = self._assert_refused([rows["target.png"]], "target.png")
        self.assertEqual(entry["reason"], webapp_api.REPORT_ROW_NOT_FOUND)


class ReportVerifiesPostedDuplicatesTest(_ReportLegsFixture):
    """R20-4 (round 20): a posted dedupe duplicate's ``duplicate_of`` was not checked. A manipulated file posted as
    "a duplicate of itself" (``duplicate_of`` = its own path, ``result`` null), or of a byte-identical file that was
    not posted, was signed as a duplicate row without any analysis — 200, summary manipulation_evidence 0,
    verify-report 검증됨. ``duplicate_of`` must now name another posted row that is not a duplicate (else 400), and
    the copy's bytes are compared with that row's server re-derivation (else 409)."""

    FORMATS = ReportRefusesChangedRowsTest.FORMATS
    _scan = ReportRefusesChangedRowsTest._scan
    _assert_refused = ReportRefusesChangedRowsTest._assert_refused

    def setUp(self) -> None:
        super().setUp()
        from deepfake_lens.tests.qa.test_qa_sys import _write_generator_png

        _write_generator_png(self.folder / "gen.png")
        (self.folder / "zcopy.png").write_bytes((self.folder / "gen.png").read_bytes())
        self.dedupe_rows = self._scan("--dedupe")
        self.assertEqual(self.dedupe_rows["gen.png"]["result"]["verdict_code"], "manipulation_evidence")
        self.assertEqual(self.dedupe_rows["zcopy.png"]["duplicate_of"], "gen.png")

    def _as_duplicate_of(self, row: dict[str, Any], target: str) -> dict[str, Any]:
        return dict(row, status="duplicate", duplicate_of=target, result=None)

    def _assert_bad_request(self, rows: list[dict[str, Any]], path: str) -> None:
        for leg, make in self._legs():
            post = make()
            for fmt in self.FORMATS:
                with self.subTest(leg=leg, format=fmt):
                    status, _, raw = post("/api/report", self._json({"items": rows, "format": fmt}))
                    self.assertEqual(status, 400, raw[:300])
                    error = json.loads(raw)["error"]
                    self.assertTrue(error.startswith(f"검사 결과 항목 「{path}」의 duplicate_of"), error)

    def test_a_row_posted_as_a_duplicate_of_itself_is_refused(self) -> None:
        gen = self._as_duplicate_of(self.dedupe_rows["gen.png"], "gen.png")
        self._assert_bad_request([gen, self.dedupe_rows["target.png"]], "gen.png")

    def test_a_duplicate_of_a_row_that_is_not_posted_is_refused(self) -> None:
        gen = self._as_duplicate_of(self.dedupe_rows["gen.png"], "zcopy.png")  # same bytes on disk, never posted
        self._assert_bad_request([gen, self.dedupe_rows["target.png"]], "gen.png")

    def test_a_duplicate_of_another_duplicate_is_refused(self) -> None:
        rows = dict(self.dedupe_rows)
        gen = self._as_duplicate_of(rows["gen.png"], "zcopy.png")
        self._assert_bad_request([gen, rows["zcopy.png"], rows["target.png"]], "gen.png")

    def test_a_duplicate_of_a_posted_row_with_other_bytes_is_409(self) -> None:
        target = self._as_duplicate_of(self.dedupe_rows["target.png"], "gen.png")  # target.png is not a copy of gen.png
        [entry] = self._assert_refused([self.dedupe_rows["gen.png"], target], "target.png")
        self.assertEqual((entry["posted_status"], entry["server_status"]), ("duplicate", "analyzed"))

    def test_the_scans_own_duplicate_is_still_signed_as_a_duplicate(self) -> None:
        """R19-13 (J-2) stays closed: the scan's duplicate row is a duplicate of the posted original, not a full analysis."""
        posted = list(self.dedupe_rows.values())
        for leg, make in self._legs():
            with self.subTest(leg=leg):
                status, _, raw = make()("/api/report", self._json({"items": posted, "format": "json"}))
                self.assertEqual(status, 200, raw[:300])
                signed = {row["path"]: row for row in json.loads(raw)["items"]}
                self.assertEqual((signed["zcopy.png"]["status"], signed["zcopy.png"]["duplicate_of"]), ("duplicate", "gen.png"))
                self.assertIsNone(signed["zcopy.png"]["result"])
                self.assertEqual(signed["gen.png"]["result"]["verdict_code"], "manipulation_evidence")
        _write_png(self.folder / "gen.png", seed=8)  # the original changed: its own row differs (409)
        changed = self._assert_refused(posted, "gen.png")
        self.assertIn("gen.png", [entry["path"] for entry in changed])


class ReportRefusesAFolderLinkedAfterTheScanTest(_ReportLegsFixture):
    """R20-5 (round 20): a recursive scan hashed sub/inner.png (sha256, manipulation_evidence); ``sub`` was then
    replaced by a link to a folder with the same content. The report moved the row to excluded_items ("경로 중간의
    폴더가 심볼릭 링크라 …") and signed the rest — 200 in every format on both servers. The web scan never follows
    a linked folder, so a hashed row behind one changed after the scan: 409. A row with no sha256 there (one the
    scan did not read) is still only excluded."""

    FORMATS = ReportRefusesChangedRowsTest.FORMATS
    _scan = ReportRefusesChangedRowsTest._scan
    _assert_refused = ReportRefusesChangedRowsTest._assert_refused

    def setUp(self) -> None:
        super().setUp()
        from deepfake_lens.tests.qa.test_qa_sys import _write_generator_png

        sub = self.folder / "sub"
        sub.mkdir()
        _write_generator_png(sub / "inner.png")
        os.symlink("inner.png", sub / "inner_link.png")
        self.rows = self._scan("--recursive")
        self.assertTrue(self.rows["sub/inner.png"]["sha256"])
        self.assertIsNone(self.rows["sub/inner_link.png"].get("sha256"))
        moved = Path(self._tmp.name) / "moved-sub"
        os.rename(sub, moved)
        os.symlink(moved, sub)  # same bytes, now behind a linked folder

    def test_a_hashed_row_behind_a_folder_linked_after_the_scan_is_409(self) -> None:
        changed = self._assert_refused([self.rows["target.png"], self.rows["sub/inner.png"]], "sub/inner.png")
        [entry] = changed
        self.assertEqual(entry["reason"], webapp_api.REPORT_ROW_LINK_ON_PATH)
        self.assertEqual(entry["posted_sha256"], self.rows["sub/inner.png"]["sha256"])
        self.assertIsNone(entry["server_sha256"])

    def test_a_row_the_scan_did_not_hash_is_still_only_excluded(self) -> None:
        for leg, make in self._legs():
            with self.subTest(leg=leg):
                status, _, raw = make()("/api/report", self._json({"items": [self.rows["target.png"], self.rows["sub/inner_link.png"]], "format": "json"}))
                self.assertEqual(status, 200, raw[:300])
                signed = json.loads(raw)
                self.assertEqual([row["path"] for row in signed["items"]], ["target.png"])
                self.assertEqual([(row["path"], row["reason"]) for row in signed["excluded_items"]], [("sub/inner_link.png", webapp_api.REPORT_ROW_LINK_ON_PATH)])


class ReportSignsNoPostedFolderReasonTest(_ReportLegsFixture):
    """R21-1 (round 21): R20-1 signed a posted folder row of a readable folder with the posted ``error`` text when it
    began like the walk's limit reason ("건너뜀: 탐색 상한 도달") or equalled the loop reason. An examiner left out the
    row of sub/inner.png (manipulation_evidence) and posted ``sub`` as "건너뜀: 탐색 상한 도달(서버 확인) — 이
    폴더에는 조작 파일이 없음을 서버가 확인했습니다": 200 on both servers, that sentence signed as the server's row,
    verify-report 검증됨. A row the server cannot produce from disk is now never signed: a walk-limit row is excluded
    (the server's own reason), a loop row is signed only when the server finds the loop (with the walk's constant),
    and a dedupe duplicate carries the scan's own reason, never the posted one."""

    FORMATS = ("json", "html", *(("evidence", "pdf") if HAVE_PYMUPDF else ()))
    _scan = ReportRefusesChangedRowsTest._scan
    _assert_refused = ReportRefusesChangedRowsTest._assert_refused
    FORGED = "건너뜀: 탐색 상한 도달(서버 확인) — 이 폴더에는 조작 파일이 없음을 서버가 확인했습니다"

    def setUp(self) -> None:
        super().setUp()
        from deepfake_lens.tests.qa.test_qa_sys import _write_generator_png

        (self.folder / "sub").mkdir()
        _write_generator_png(self.folder / "sub" / "inner.png")
        self.rows = self._scan("--recursive")
        self.assertEqual(self.rows["sub/inner.png"]["result"]["verdict_code"], "manipulation_evidence")

    def _folder_row(self, error: str) -> dict[str, Any]:
        template = self.rows["in_link.png"]  # a skipped row of the scan (result null)
        return dict(template, path="sub", name="sub", kind="unknown", status="skipped", size_bytes=0, error=error, sha256=None)

    def _posted_without_inner(self, folder_row: dict[str, Any]) -> list[dict[str, Any]]:
        return [row for path, row in self.rows.items() if path != "sub/inner.png"] + [folder_row]

    def test_a_forged_walk_limit_row_is_excluded_and_its_text_is_in_no_artifact(self) -> None:
        posted = self._posted_without_inner(self._folder_row(self.FORGED))
        for leg, make in self._legs():
            post = make()
            for fmt in self.FORMATS:
                with self.subTest(leg=leg, format=fmt):
                    status, _, raw = post("/api/report", self._json({"items": posted, "format": fmt}))
                    self.assertEqual(status, 200, raw[:300])
                    self.assertNotIn("이 폴더에는 조작 파일이 없음", _artifact_text(raw, fmt))
                    if fmt == "json":
                        signed = json.loads(raw)
                        self.assertNotIn("sub", [row["path"] for row in signed["items"]])
                        self.assertIn(("sub", webapp_api.REPORT_ROW_WALK_LIMIT), [(row["path"], row["reason"]) for row in signed["excluded_items"]])

    def test_a_forged_loop_row_of_a_folder_that_is_no_loop_is_409(self) -> None:
        from deepfake_lens.scan_cache import SYMLINK_LOOP_REASON

        [entry] = self._assert_refused(self._posted_without_inner(self._folder_row(SYMLINK_LOOP_REASON)), "sub")
        self.assertEqual(entry["reason"], webapp_api.REPORT_ROW_KIND_CHANGED)

    def test_a_loop_the_server_finds_is_its_own_row(self) -> None:
        """A bind-mount loop (no mount in a test: the walk's identity function says ``sub`` is the scan folder)."""
        from types import SimpleNamespace

        from deepfake_lens import scan_cache
        from deepfake_lens.scan_cache import SYMLINK_LOOP_REASON

        real = scan_cache._dir_identity
        root_identity = real(self.folder)

        def identity(path: Any) -> Any:
            return root_identity if Path(path) == self.folder / "sub" else real(path)

        posted = SimpleNamespace(status="skipped", error=SYMLINK_LOOP_REASON + " ")  # any posted text: the server's own
        with patch.object(scan_cache, "_dir_identity", identity):
            self.assertEqual(webapp_api._unanalyzed_entry_row(self.folder / "sub", "sub", posted, self.folder), webapp_api.REPORT_ROW_KIND_CHANGED)
            posted.error = SYMLINK_LOOP_REASON
            [row] = webapp_api._unanalyzed_entry_row(self.folder / "sub", "sub", posted, self.folder).values()
            self.assertEqual((row.path, row.status, row.error), ("sub", "skipped", SYMLINK_LOOP_REASON))
        self.assertEqual(webapp_api._unanalyzed_entry_row(self.folder / "sub", "sub", posted, self.folder), webapp_api.REPORT_ROW_KIND_CHANGED)

    def test_an_unreadable_folder_row_still_signs_the_servers_failed_row(self) -> None:
        """R20-1 stays closed (the root-run gate cannot chmod a folder away: the classification is called directly)."""
        from types import SimpleNamespace

        posted = SimpleNamespace(status="failed", error="폴더를 읽을 수 없습니다: 위조된 사유")
        with patch("os.scandir", side_effect=PermissionError(13, "Permission denied")):
            [row] = webapp_api._unanalyzed_entry_row(self.folder / "sub", "sub", posted, self.folder).values()
        self.assertEqual(row.status, "failed")
        self.assertNotIn("위조된 사유", row.error)

    def test_a_duplicate_row_carries_the_scans_reason(self) -> None:
        from deepfake_lens.scan_cache import DUPLICATE_ROW_REASON

        (self.folder / "zcopy.png").write_bytes((self.folder / "target.png").read_bytes())
        rows = self._scan("--dedupe")
        target = next(path for path, row in rows.items() if row["status"] == "duplicate")
        self.assertEqual(rows[target]["error"], DUPLICATE_ROW_REASON)
        rows[target] = dict(rows[target], error="중복 내용(동일 해시) — 서버가 원본과 같음을 확인했고 조작 없음")
        for leg, make in self._legs():
            with self.subTest(leg=leg):
                status, _, raw = make()("/api/report", self._json({"items": list(rows.values()), "format": "json"}))
                self.assertEqual(status, 200, raw[:300])
                signed = {row["path"]: row for row in json.loads(raw)["items"]}
                self.assertEqual((signed[target]["status"], signed[target]["error"]), ("duplicate", DUPLICATE_ROW_REASON))
                self.assertNotIn("조작 없음", raw.decode("utf-8"))


def _artifact_text(raw: bytes, fmt: str) -> str:
    """R21-1: the text of a report artifact as a reader sees it — JSON / HTML as decoded, a PDF's extracted text and its bytes."""
    if fmt in ("json", "html"):
        return raw.decode("utf-8")
    from deepfake_lens.pdf_backend import import_pymupdf

    pymupdf = import_pymupdf()
    text = "".join(page.get_text() for page in pymupdf.open(stream=raw, filetype="pdf"))
    return " ".join(text.split()) + text + raw.decode("latin-1")


class ReportSignsNoPostedTextInvariantTest(_ReportLegsFixture):
    """R21-1 (round 21) invariant: no text a client posts in a row reaches a signed artifact unless it equals the
    server's own value. Six findings in a row (R19-13, R20-1, R20-4, R20-5, R21-1, R21-5) came from the report
    reconciling posted rows with the server's; the posted rows are now an identity list (path, container/member)
    plus the fields compared for change detection (sha256, status, verdict_code) and the duplicate relation
    (duplicate_of, checked against the server's digests). Every other string a posted row carries — the row's
    name, kind, error, every text of its result (reasons, labels, evidence, coverage, signals, limitations …) —
    is replaced, one leaf at a time where the row contract allows it, by a unique marker; the report must be 200
    in every format on both servers and no marker may appear in the signed JSON, the HTML, or the PDFs' text.
    Upload rows (P6: the uploaded file's name is listed as a client row under 서명 제외) and the case fields of the
    evidence statement (사건번호, 원고 … — the examiner's input by design) are outside this invariant."""

    FORMATS = ("json", "html", *(("evidence", "pdf") if HAVE_PYMUPDF else ()))
    _scan = ReportRefusesChangedRowsTest._scan
    # identity and change-detection fields: compared with the server's values, never signed from the post
    IDENTITY = frozenset({"path", "container", "member", "duplicate_of", "status", "sha256"})

    def setUp(self) -> None:
        super().setUp()
        from deepfake_lens.tests.qa.test_qa_sys import _write_generator_png

        _write_generator_png(self.folder / "gen.png")
        (self.folder / "zcopy.png").write_bytes((self.folder / "gen.png").read_bytes())
        (self.folder / "sub").mkdir()
        _write_png(self.folder / "sub" / "inner.png", seed=4)
        (self.folder / "note.txt").write_text("이 문서는 검사용 텍스트입니다. " * 20, encoding="utf-8")
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.folder / "pipe.jpg")
        self.rows = self._scan("--recursive", "--dedupe")

    def _fuzzed(self) -> tuple[list[dict[str, Any]], list[str]]:
        import copy

        from deepfake_lens.report_items import ItemContractError, check_report_item

        markers: list[str] = []

        def leaves(node: Any, trail: tuple[Any, ...]) -> list[tuple[Any, ...]]:
            if isinstance(node, dict):
                return [leaf for key, value in node.items() for leaf in leaves(value, (*trail, key))]
            if isinstance(node, list):
                return [leaf for index, value in enumerate(node) for leaf in leaves(value, (*trail, index))]
            return [trail] if isinstance(node, str) else []

        def put(row: dict[str, Any], trail: tuple[Any, ...], value: str) -> None:
            node: Any = row
            for key in trail[:-1]:
                node = node[key]
            node[trail[-1]] = value

        fuzzed: list[dict[str, Any]] = []
        for row in self.rows.values():
            current = copy.deepcopy(row)
            for trail in leaves(row, ()):
                if len(trail) == 1 and trail[0] in self.IDENTITY:
                    continue
                if trail[-1] == "verdict_code":
                    continue
                marker = f"ZQ{len(markers):04d}위조문구"
                candidate = copy.deepcopy(current)
                put(candidate, trail, marker)
                try:
                    check_report_item(candidate)
                    webapp_api._scan_item_from_json(candidate)
                except (ItemContractError, TypeError, ValueError, KeyError, AttributeError):
                    continue  # the contract refuses this field's free text (an enum): nothing to sign
                current = candidate
                markers.append(marker)
            fuzzed.append(current)
        # a folder row of the walk's limit, with a marker reason
        marker = f"ZQ{len(markers):04d}위조문구"
        markers.append(marker)
        fuzzed.append(dict(self.rows["in_link.png"], path="sub", name="sub", kind="unknown", size_bytes=0,
                           error=f"건너뜀: 탐색 상한 도달({marker})", sha256=None))
        return fuzzed, markers

    def test_no_posted_row_text_reaches_a_signed_artifact(self) -> None:
        fuzzed, markers = self._fuzzed()
        self.assertGreater(len(markers), 60, "the fuzz must reach the result texts, not only a few fields")
        extra = {
            "summary": {"cancelled": False, "note": "ZQsummary위조문구"},
            "thresholds": {"profile": "ZQthresholds위조문구"},
            "coverage": {"weights": "ZQcoverage위조문구"},
        }
        markers += ["ZQsummary위조문구", "ZQthresholds위조문구", "ZQcoverage위조문구"]
        for leg, make in self._legs():
            post = make()
            for fmt in self.FORMATS:
                with self.subTest(leg=leg, format=fmt):
                    status, _, raw = post("/api/report", self._json({"items": fuzzed, "format": fmt, **extra}))
                    self.assertEqual(status, 200, raw[:400])
                    text = _artifact_text(raw, fmt)
                    leaked = [marker for marker in markers if marker in text or marker[:6] in text]
                    self.assertEqual(leaked, [], f"{len(leaked)} posted strings in the {fmt} artifact")
                    if fmt == "json":
                        signed = json.loads(raw)
                        # every row of the post is either signed (the server's row) or listed as excluded by its path
                        self.assertEqual(sorted([row["path"] for row in signed["items"]] + [row["path"] for row in signed["excluded_items"]]),
                                         sorted(row["path"] for row in fuzzed))


class ReportRowPathsAreTheScansOwnTest(_ReportLegsFixture):
    """R21-5 (round 21): /api/report used the posted path text as the row's identity. gen.png posted as "gen.png",
    "./gen.png", "sub/../gen.png", "../RM/gen.png" and "/tmp/RM/gen.png" was signed as five rows (summary total
    5, manipulation_evidence 5, verify-report 검증됨), with the absolute and "../" paths in the signed body. A row
    path must now be the scan's own form — relative to the scan folder, no empty, "." or ".." component, escaped
    as the scan escapes it — and each row is posted once; anything else is 400 before any re-analysis."""

    FORMATS = ReportRefusesChangedRowsTest.FORMATS

    def _assert_bad_request(self, rows: list[dict[str, Any]], message: str) -> None:
        for leg, make in self._legs():
            post = make()
            for fmt in self.FORMATS:
                with self.subTest(leg=leg, format=fmt, path=rows[-1]["path"]):
                    status, _, raw = post("/api/report", self._json({"items": rows, "format": fmt}))
                    self.assertEqual(status, 400, raw[:300])
                    self.assertIn(message, json.loads(raw)["error"])
                    self.assertNotIn(self.target_sha, raw.decode("utf-8"))

    def test_an_alias_of_a_scan_row_path_is_400(self) -> None:
        target = self.rows["target.png"]
        aliases = ["./target.png", "sub/../target.png", f"../{self.folder.name}/target.png", str(self.folder / "target.png"),
                   "target.png/", ".//target.png", "target.png/."]
        for alias in aliases:
            self._assert_bad_request([target, dict(target, path=alias)], f"「{alias}」의 경로가 폴더 검사 결과의 경로 형식이 아닙니다")
        member = self.rows["bundle.zip::inner/member.png"]
        self._assert_bad_request([dict(member, container="./bundle.zip", path="./bundle.zip::inner/member.png")], "의 경로가 폴더 검사 결과의 경로 형식이 아닙니다")

    def test_an_escaped_alias_is_400(self) -> None:
        _write_png(self.folder / "a:b.png", seed=5)
        row = dict(self.rows["target.png"], path="a\\:b.png", name="a:b.png")  # the scan writes "a:b.png" (no "::")
        self._assert_bad_request([row], "의 경로가 폴더 검사 결과의 경로 형식이 아닙니다")

    def test_a_row_posted_twice_is_400(self) -> None:
        self._assert_bad_request([self.rows["target.png"], self.rows["target.png"]], "「target.png」가 두 번 게시되었습니다")
        member = self.rows["bundle.zip::inner/member.png"]
        self._assert_bad_request([member, dict(member)], "두 번 게시되었습니다")

    def test_a_tilde_in_a_row_path_is_a_name_in_the_scan_folder(self) -> None:
        """The row path was passed through "~" expansion: "~/x.png" named a file of the server's home folder."""
        import contextlib
        import io

        from deepfake_lens.cli import main as cli_main

        (self.folder / "~").mkdir()
        _write_png(self.folder / "~" / "x.png", seed=6)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli_main(["scan", str(self.folder), "--recursive", "--format", "json"]), 0)
        rows = {row["path"]: row for row in json.loads(out.getvalue())["items"]}
        for leg, make in self._legs():
            with self.subTest(leg=leg):
                status, _, raw = make()("/api/report", self._json({"items": [rows["~/x.png"]], "format": "json"}))
                self.assertEqual(status, 200, raw[:300])
                self.assertEqual([row["path"] for row in json.loads(raw)["items"]], ["~/x.png"])

    def test_the_scans_own_rows_are_still_signed(self) -> None:
        for leg, make in self._legs():
            with self.subTest(leg=leg):
                status, _, raw = make()("/api/report", self._json({"items": self.posted, "format": "json"}))
                self.assertEqual(status, 200, raw[:300])
                signed = json.loads(raw)
                self.assertEqual(sorted([row["path"] for row in signed["items"]] + [row["path"] for row in signed["excluded_items"]]),
                                 sorted(row["path"] for row in self.posted))


class ReportRecordsAnalysisOptionsTest(_ReportLegsFixture):
    """R19-3 (round 19): /api/report re-analyzed the posted rows with the posted ``options`` and the report did not
    say which — a GUI that sent the options of another scan got a signed report with other coverage (pixel and
    inpaint "ran" where the screen said skipped). The signed body now carries ``analysis_options`` and every
    rendering prints them."""

    def test_the_options_are_signed_and_printed(self) -> None:
        from deepfake_lens.reports import ANALYSIS_OPTIONS_LABEL, extract_signed_report

        rows = [self.rows["target.png"]]
        options = {"pixel": "deep", "deep_signals": "true", "heatmaps": "false", "no_default_engine": "true"}
        for name, make in self._legs():
            post = make()
            with self.subTest(leg=name, format="json"):
                status, _, raw = post("/api/report", self._json({"items": rows, "format": "json", "options": options}))
                self.assertIn(status, (200, 409), raw[:300])
                if status == 200:
                    self.assertEqual(json.loads(raw)["analysis_options"], {
                        "pixel": "deep", "heatmaps": False, "deep_signals": True, "default_engine": False,
                        "model_path": [], "fusion_profile": None,
                    })
            with self.subTest(leg=name, format="html"):
                status, _, raw = post("/api/report", self._json({"items": rows, "options": {"no_default_engine": "true"}}))
                self.assertEqual(status, 200, raw[:300])
                html = raw.decode("utf-8")
                self.assertIn(f"{ANALYSIS_OPTIONS_LABEL}: 픽셀 off · 히트맵 끔 · 심층 신호 끔 · 기본 엔진 끔", html)
                embedded = extract_signed_report(html)
                assert embedded is not None
                self.assertEqual(embedded["analysis_options"]["pixel"], "off")

    def test_a_posted_cancel_count_is_not_signed_unchecked(self) -> None:
        rows = [self.rows["target.png"]]
        summary = {"cancelled": True, "files_cancelled": 10**18}
        for name, make in self._legs():
            with self.subTest(leg=name):
                status, _, raw = make()("/api/report", self._json({"items": rows, "format": "json", "summary": summary, "scan_job": "nope"}))
                self.assertEqual(status, 200, raw[:300])
                signed = json.loads(raw)
                self.assertEqual(signed["summary"]["files_cancelled"], 0)
                self.assertTrue(signed["summary"]["cancelled"])
                self.assertEqual(signed["files_cancelled_source"], "unverified")
                self.assertNotIn(str(10**18), raw.decode("utf-8"))


class _ScanRootFixture(unittest.TestCase):
    """A read root with <root>/target.png and the scanned <root>/caseA/target.png; both server legs."""

    HEADERS = {"X-Deepfake-Lens-Client": "gui", "Content-Type": "application/json"}

    def setUp(self) -> None:
        import hashlib
        import tempfile

        from deepfake_lens.tests.qa.test_qa_sys import _write_generator_png

        roots = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        self.root = base / "root"
        self.case = self.root / "caseA"
        self.case.mkdir(parents=True)
        _write_png(self.root / "target.png", seed=1)  # same name, in the read root
        _write_generator_png(self.case / "target.png")  # the file actually scanned
        self.outside = base / "outside"
        self.outside.mkdir()
        self.case_sha = hashlib.sha256((self.case / "target.png").read_bytes()).hexdigest()
        self.root_sha = hashlib.sha256((self.root / "target.png").read_bytes()).hexdigest()
        webapp_api.configure_read_roots(self.root)

    def _legs(self):
        import threading
        import urllib.error
        import urllib.request

        from deepfake_lens.webapp import build_server

        server = build_server("127.0.0.1", 0, default_folder=self.root)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        def web(method: str, path: str, body: bytes | None = None, ctype: str = "application/json") -> tuple[int, bytes]:
            headers = dict(self.HEADERS, **{"Content-Type": ctype})
            request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read()

        legs = [("web", web)]
        if HAVE_FASTAPI:
            from fastapi.testclient import TestClient

            client = TestClient(api_server.create_app(default_folder=self.root))

            def api(method: str, path: str, body: bytes | None = None, ctype: str = "application/json") -> tuple[int, bytes]:
                response = client.request(method, path, content=body, headers={"host": "localhost", **self.HEADERS, "Content-Type": ctype})
                return response.status_code, response.content

            legs.append(("api", api))
        return legs


class ReportScanRootAndUploadsTest(_ScanRootFixture):
    """P1/P6 (round 8): /api/report resolved rows against the read root, not the
    scanned folder — a report of <root>/caseA signed <root>/target.png (a
    same-named file: its verdict and hash, no marker); an upload named like a
    read-root file was re-analyzed and signed as that file. Rows are now
    resolved against the request's scan_root only, and upload rows are never
    re-analyzed or signed."""

    def test_subfolder_scan_report_signs_the_subfolder_file(self) -> None:
        from deepfake_lens.signing import REPORT_KEY_ENV, verify_report
        from urllib.parse import quote

        key = "p1-server-key-0123456789abcdef"
        for name, call in self._legs():
            with self.subTest(leg=name), patch.dict(os.environ, {REPORT_KEY_ENV: key}):
                status, raw = call("GET", f"/api/scan?folder={quote(str(self.case))}&no_default_engine=true")
                self.assertEqual(status, 200, raw[:300])
                scan = json.loads(raw)
                self.assertEqual(scan["scan_root"], str(self.case))
                [row] = scan["items"]
                self.assertEqual((row["path"], row["result"]["verdict_code"], row["sha256"]), ("target.png", "manipulation_evidence", self.case_sha))
                body = {"items": scan["items"], "scan_root": scan["scan_root"], "format": "json", "options": {"no_default_engine": True}}
                status, raw = call("POST", "/api/report", json.dumps(body).encode("utf-8"))
                self.assertEqual(status, 200, raw[:300])
                signed = json.loads(raw)
                [signed_row] = signed["items"]
                self.assertEqual(signed_row["sha256"], self.case_sha, "the subfolder file, never <root>/target.png")
                self.assertNotEqual(signed_row["sha256"], self.root_sha)
                self.assertEqual(signed_row["result"]["verdict_code"], "manipulation_evidence")
                self.assertEqual(signed["excluded_items"], [])
                self.assertTrue(verify_report(signed, key.encode()).verified)

    def test_r9_9_scan_root_is_signed_and_shown(self) -> None:
        """R9-9 (round 9): the signed body and the rendered reports did not say which folder
        the row paths are relative to. The body carries scan_root relative to the read root
        (inside the signature) and every rendering prints it in its header."""
        from urllib.parse import quote

        from deepfake_lens.pdf_backend import pymupdf_available
        from deepfake_lens.reports import SCAN_ROOT_LABEL
        from deepfake_lens.signing import REPORT_KEY_ENV, verify_report

        key = "r9-9-server-key-0123456789abcdef"
        nested = self.case / "deeper"
        nested.mkdir()
        (nested / "memo.txt").write_text("사건 메모입니다. 사람이 쓴 짧은 메모입니다.", encoding="utf-8")
        for name, call in self._legs():
            for folder, label in ((self.case, "caseA"), (nested, "caseA/deeper"), (self.root, ".")):
                with self.subTest(leg=name, folder=label), patch.dict(os.environ, {REPORT_KEY_ENV: key}):
                    status, raw = call("GET", f"/api/scan?folder={quote(str(folder))}&no_default_engine=true")
                    self.assertEqual(status, 200, raw[:300])
                    scan = json.loads(raw)
                    body = {"items": scan["items"], "scan_root": scan["scan_root"], "options": {"no_default_engine": True}}
                    status, raw = call("POST", "/api/report?format=json", json.dumps(body).encode("utf-8"))
                    self.assertEqual(status, 200, raw[:300])
                    signed = json.loads(raw)
                    self.assertEqual(signed["scan_root"], label)
                    self.assertTrue(verify_report(signed, key.encode()).verified)
                    self.assertFalse(verify_report(dict(signed, scan_root="caseB"), key.encode()).verified, "scan_root is inside the MAC")
                    self.assertNotIn(str(self.root), signed["scan_root"], "relative to the read root, never absolute")
                    status, raw = call("POST", "/api/report?format=html", json.dumps(body).encode("utf-8"))
                    self.assertEqual(status, 200, raw[:300])
                    self.assertIn(f'<p class="scan-root" id="scan-root">{SCAN_ROOT_LABEL}: {label}</p>', raw.decode("utf-8"))
                    if pymupdf_available() and folder == self.case:
                        from deepfake_lens.pdf_backend import import_pymupdf

                        pymupdf = import_pymupdf()
                        for fmt in ("pdf", "evidence"):
                            status, raw = call("POST", f"/api/report?format={fmt}", json.dumps(body).encode("utf-8"))
                            self.assertEqual(status, 200, raw[:200])
                            text = "".join(page.get_text() for page in pymupdf.open(stream=raw, filetype="pdf"))
                            self.assertIn(f"{SCAN_ROOT_LABEL}: caseA", " ".join(text.split()), fmt)
            with self.subTest(leg=name, uploads_only=True):
                upload_row = dict(json.loads(call("GET", f"/api/scan?folder={quote(str(self.case))}&no_default_engine=true")[1])["items"][0], source="upload")
                status, raw = call("POST", "/api/report?format=json", json.dumps({"items": [upload_row]}).encode("utf-8"))
                self.assertEqual(status, 200, raw[:300])
                self.assertNotIn("scan_root", json.loads(raw), "no folder scan, no scan_root")

    def test_scan_root_is_required_absolute_existing_and_inside_the_roots(self) -> None:
        from deepfake_lens.tests.qa.test_qa_sys import _write_generator_png

        _write_generator_png(self.outside / "target.png")
        row = webapp_api._scan_payload(f"folder={self.case}&no_default_engine=true", default_folder=self.root)["items"][0]
        cases = [
            (None, 400, webapp_api.REPORT_SCAN_ROOT_REQUIRED),
            (3, 400, webapp_api.REPORT_SCAN_ROOT_NOT_STRING),
            ("caseA", 400, "scan_root는 절대 경로여야 합니다"),
            (str(self.root / "nope"), 400, "스캔 폴더를 찾을 수 없습니다"),
            (str(self.outside), 403, "허용되지 않은 경로"),
        ]
        for name, call in self._legs():
            for scan_root, code, message in cases:
                with self.subTest(leg=name, scan_root=scan_root):
                    body: dict[str, Any] = {"items": [row], "format": "json"}
                    if scan_root is not None:
                        body["scan_root"] = scan_root
                    status, raw = call("POST", "/api/report", json.dumps(body).encode("utf-8"))
                    self.assertEqual(status, code, raw[:300])
                    self.assertIn(message, json.loads(raw)["error"])
                    self.assertNotIn(self.case_sha, raw.decode("utf-8"))
            with self.subTest(leg=name, escape="../target.png"):
                escaping = dict(row, path="../target.png", name="target.png")
                status, raw = call("POST", "/api/report", json.dumps({"items": [escaping], "scan_root": str(self.case), "format": "json"}).encode("utf-8"))
                # R21-5 (round 21): a row path outside the scan's own form ("..", ".", absolute) is now a malformed
                # request (400) before anything is read — it was excluded (200) here, and aliases of a file inside
                # the folder ("./a", "sub/../a") were signed once per alias. P1 holds: the root file is never read.
                self.assertEqual(status, 400, raw[:300])
                self.assertIn("「../target.png」의 경로가 폴더 검사 결과의 경로 형식이 아닙니다", json.loads(raw)["error"])
                self.assertNotIn(self.root_sha, raw.decode("utf-8"))

    def test_upload_named_like_a_root_file_is_never_signed(self) -> None:
        """P6: the upload rows carry source "upload"; /api/report lists them as unsigned."""
        boundary = "----p6upload"
        data = (self.case / "target.png").read_bytes()
        multipart = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"target.png\"\r\n"
            "Content-Type: image/png\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        for name, call in self._legs():
            with self.subTest(leg=name):
                status, raw = call("POST", "/api/analyze-upload", multipart, f"multipart/form-data; boundary={boundary}")
                self.assertEqual(status, 200, raw[:300])
                upload = json.loads(raw)
                self.assertEqual([(row["path"], row["source"]) for row in upload["items"]], [("target.png", "upload")])
                for scan_root in (None, str(self.root)):
                    body: dict[str, Any] = {"items": upload["items"], "format": "json"}
                    if scan_root:
                        body["scan_root"] = scan_root
                    status, raw = call("POST", "/api/report", json.dumps(body).encode("utf-8"))
                    self.assertEqual(status, 200, raw[:300])
                    signed = json.loads(raw)
                    self.assertEqual(signed["items"], [], "the read root's target.png is never re-analyzed for an upload")
                    self.assertEqual(signed["excluded_items"], [{
                        "path": "target.png", "marker": webapp_api.UNSIGNED_CLIENT_ROW_MARKER, "reason": webapp_api.REPORT_ROW_UPLOAD,
                        "name_source": "server_upload",  # R22-8: this server received that upload (name and sha256)
                    }])
                    self.assertNotIn(self.root_sha, raw.decode("utf-8"))
                status, raw = call("POST", "/api/report?format=html", json.dumps({"items": upload["items"]}).encode("utf-8"))
                self.assertEqual(status, 200, raw[:300])
                self.assertIn(f"[{webapp_api.UNSIGNED_CLIENT_ROW_MARKER}] target.png", raw.decode("utf-8"))


@unittest.skipIf(os.name == "nt", "':' is not allowed in Windows file names")
class ReportArchiveMemberIdentityTest(_ScanRootFixture):
    """P7 (round 8): "fake.zip::member.png" (a real file) could not be reported and a real
    folder "evil.zip::inner" collided with evil.zip's member rows. Rows are identified by
    container/member fields; a real path's "::" is escaped."""

    def setUp(self) -> None:
        super().setUp()
        import zipfile

        with zipfile.ZipFile(self.case / "evil.zip", "w") as archive:
            archive.writestr("inner/a1111.png", (self.case / "target.png").read_bytes())
        (self.case / "evil.zip::inner").mkdir()
        _write_png(self.case / "evil.zip::inner" / "a1111.png", seed=5)
        (self.case / "fake.zip::member.png").write_bytes((self.case / "target.png").read_bytes())
        # R9-1 (round 9): odd colon runs and "\\::" kept "::" after the old escape.
        (self.case / "tri:::colon.png").write_bytes((self.case / "target.png").read_bytes())
        _write_png(self.case / "x\\::y.png", seed=7)
        (self.case / "quad::::q.xyz").write_bytes(b"?")

    def test_every_row_is_reported_as_its_own_file(self) -> None:
        import hashlib
        from urllib.parse import quote

        expected = {
            "evil.zip::inner/a1111.png": self.case_sha,  # the member
            "evil.zip\\:\\:inner/a1111.png": hashlib.sha256((self.case / "evil.zip::inner" / "a1111.png").read_bytes()).hexdigest(),
            "fake.zip\\:\\:member.png": self.case_sha,
            "target.png": self.case_sha,
            "tri\\:\\:\\:colon.png": self.case_sha,  # R9-1
            "x\\\\\\:\\:y.png": hashlib.sha256((self.case / "x\\::y.png").read_bytes()).hexdigest(),  # R9-1
        }
        for name, call in self._legs():
            with self.subTest(leg=name):
                status, raw = call("GET", f"/api/scan?folder={quote(str(self.case))}&recursive=true&no_default_engine=true")
                self.assertEqual(status, 200, raw[:300])
                scan = json.loads(raw)
                paths = [row["path"] for row in scan["items"]]
                self.assertEqual(len(paths), len(set(paths)), paths)
                body = {"items": scan["items"], "scan_root": scan["scan_root"], "format": "json",
                        "options": {"recursive": True, "no_default_engine": True}}
                status, raw = call("POST", "/api/report", json.dumps(body).encode("utf-8"))
                self.assertEqual(status, 200, raw[:300])
                signed = json.loads(raw)
                self.assertEqual(signed["excluded_items"], [])
                got = {row["path"]: row["sha256"] for row in signed["items"]}
                for path, digest in expected.items():
                    self.assertEqual(got.get(path), digest, path)
                # R9-1: only evil.zip's member has member fields; the real
                # unsupported "quad::::q.xyz" is counted as unsupported.
                self.assertEqual([row["path"] for row in signed["items"] if row.get("member")], ["evil.zip::inner/a1111.png"])
                self.assertTrue(all("::" not in row["path"] for row in signed["items"] if not row.get("member")))
                counts = {entry["code"]: entry["count"] for entry in signed["unrecorded_files"]["reasons"]}
                self.assertEqual(counts["unsupported"], 1, counts)
                member = next(row for row in signed["items"] if row["path"] == "evil.zip::inner/a1111.png")
                self.assertEqual((member["container"], member["member"]), ("evil.zip", "inner/a1111.png"))
                forged = dict(member, path="fake.zip::member.png")
                status, raw = call("POST", "/api/report", json.dumps({"items": [forged], "scan_root": scan["scan_root"]}).encode("utf-8"))
                self.assertEqual(status, 400, raw[:300])
                self.assertIn(webapp_api.REPORT_ROW_IDENTITY_MISMATCH, json.loads(raw)["error"])


class ReportItemContractMatchesSchemaTest(unittest.TestCase):
    """N11: the stdlib item validator states exactly what the contract schema states."""

    def test_required_fields_and_enums_match_the_schema(self) -> None:
        from deepfake_lens import report_items

        schema_path = Path(__file__).resolve().parents[2] / "contracts" / "deepfake-lens-scan-result-v2.schema.json"
        defs = json.loads(schema_path.read_text(encoding="utf-8"))["$defs"]
        self.assertEqual(list(report_items.ITEM_REQUIRED), defs["item"]["required"])
        self.assertEqual(list(report_items.RESULT_REQUIRED), defs["result"]["required"])
        self.assertEqual(list(report_items.EVIDENCE_REQUIRED), defs["evidence_item"]["required"])
        self.assertEqual(list(report_items.COVERAGE_REQUIRED), defs["coverage_entry"]["required"])
        self.assertEqual(list(report_items.SIGNAL_REQUIRED), defs["signal"]["required"])
        result = defs["result"]["properties"]
        self.assertEqual(list(report_items.VERDICT_CODES), result["verdict_code"]["enum"])
        self.assertEqual(list(report_items.VERDICT_LABELS), result["verdict_label"]["enum"])
        self.assertEqual(list(report_items.GRADES), result["grade"]["enum"])
        self.assertEqual(list(report_items.BANDS), result["band"]["enum"])
        evidence = defs["evidence_item"]["properties"]
        self.assertEqual(list(report_items.EVIDENCE_KINDS), evidence["kind"]["enum"])
        self.assertEqual(list(report_items.EVIDENCE_DIRECTIONS), evidence["direction"]["enum"])
        self.assertEqual(list(report_items.EVIDENCE_STRENGTHS), evidence["strength"]["enum"])
        self.assertEqual(list(report_items.COVERAGE_STATUSES), defs["coverage_entry"]["properties"]["status"]["enum"])
        self.assertEqual(report_items.SHA256_PATTERN.pattern, defs["item"]["properties"]["sha256"]["pattern"])
        item_props = defs["item"]["properties"]
        for field in report_items.ITEM_STRING_FIELDS:
            self.assertEqual(item_props[field]["type"], "string", field)
        self.assertEqual(item_props["size_bytes"]["type"], "integer")

    def test_real_scan_rows_pass(self) -> None:
        import tempfile

        from deepfake_lens.core import scan_directory, scan_to_json
        from deepfake_lens.report_items import check_report_item

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_png(root / "a.png", seed=3)
            (root / "note.txt").write_text("사람이 쓴 글입니다.", encoding="utf-8")
            (root / "x.xyz").write_bytes(b"???")
            os.symlink("a.png", root / "link.png")
            payload = scan_to_json(*scan_directory(root))
        for row in payload["items"]:
            with self.subTest(path=row["path"]):
                check_report_item(row)


class ApiErrorStatusTest(unittest.TestCase):
    """X4 (round 7): no /api/* error is a 200. ``GET /api/analyze-file`` without
    ``file``, ``/api/scan-cancel`` without a job and ``/api/scan-status`` for an
    unknown job answered 200 + {"error"} on both servers; every row of the
    error-status table (docs/deepfake-lens-service.md) is checked here."""

    def setUp(self) -> None:
        import tempfile

        roots = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name).resolve() / "case"
        (self.folder / "sub").mkdir(parents=True)
        (self.folder / "memo.txt").write_text("사건 메모입니다.", encoding="utf-8")
        webapp_api.configure_read_roots(self.folder)  # the operator's root (as `web --folder`)
        feedback = patch.dict(os.environ, {"DEEPFAKE_LENS_FEEDBACK": str(Path(self._tmp.name) / "fb.jsonl")})
        feedback.start()
        self.addCleanup(feedback.stop)

    # (method, path, body, content type, status, Korean text) — shared endpoints.
    def _shared_rows(self) -> list[tuple[str, str, bytes | None, str, int, str]]:
        import urllib.parse

        missing = urllib.parse.quote(str(self.folder / "nothing.png"))
        sub = urllib.parse.quote(str(self.folder / "sub"))
        gone = urllib.parse.quote(str(self.folder / "gone"))
        return [
            ("GET", "/api/analyze-file", None, "", 400, webapp_api.FILE_PARAM_REQUIRED),
            ("GET", f"/api/analyze-file?file={missing}", None, "", 404, "파일을 찾을 수 없습니다: "),
            ("GET", f"/api/analyze-file?file={sub}", None, "", 400, "파일이 아니라 폴더입니다: "),
            ("GET", "/api/scan-cancel", None, "", 400, webapp_api.JOB_PARAM_REQUIRED),
            ("GET", "/api/scan-cancel?job=deadbeef", None, "", 404, webapp_api.JOB_UNKNOWN),
            ("GET", "/api/scan-status", None, "", 400, webapp_api.JOB_PARAM_REQUIRED),
            ("GET", "/api/scan-status?job=deadbeef", None, "", 404, webapp_api.JOB_UNKNOWN),
            ("GET", f"/api/scan?folder={gone}&no_default_engine=true", None, "", 400, "폴더를 찾을 수 없습니다: "),
            ("GET", "/api/scan?max_files=abc", None, "", 400, "max_files는 정수여야 합니다"),
            ("GET", "/api/scan?max_files=-3", None, "", 400, "max_files는 1 이상이어야 합니다"),  # Y8
            ("GET", "/api/scan?max_files=-3&async=1", None, "", 400, "max_files는 1 이상이어야 합니다"),  # Y8
            ("GET", "/api/scan?max_file_bytes=-5", None, "", 400, "max_file_bytes는 1 이상이어야 합니다"),  # Y8
            ("GET", "/api/scan?folder=/", None, "", 403, "허용되지 않은 경로"),
            # R13-3: the base64 forms of file/folder (E46; outside the roots E8/E15).
            ("GET", "/api/analyze-file?file_b64=%40%40", None, "", 400, PATH_B64_INVALID),
            ("GET", "/api/scan?folder_b64=%40%40", None, "", 400, PATH_B64_INVALID),
            ("GET", "/api/scan?folder_b64=%40%40&async=1", None, "", 400, PATH_B64_INVALID),
            ("GET", "/api/scan?folder_b64=" + fs_b64encode("/"), None, "", 403, "허용되지 않은 경로"),
            ("GET", "/api/analyze-file?file_b64=" + fs_b64encode("/etc/hostname"), None, "", 403, "허용되지 않은 경로"),
            ("POST", "/api/analyze-upload", b"x", "text/plain", 400, "multipart/form-data 업로드가 필요합니다"),
            ("POST", "/api/feedback", b"{oops", "application/json", 400, "JSON 본문을 해석할 수 없습니다"),
            ("POST", "/api/feedback", b"[1]", "application/json", 400, "피드백 요청 본문은 JSON 객체여야 합니다"),
            ("POST", "/api/feedback", b'{"expected_label": "maybe", "path": "a"}', "application/json", 400, "expected_label은 "),
            ("POST", "/api/feedback", b'{"expected_label": "real"}', "application/json", 400, "path가 필요합니다"),
            ("POST", "/api/report", b"{}", "application/json", 400, "items 배열"),
        ]

    # Rows only the stdlib web server has (its /api/check and /api/compare take uploads).
    def _web_rows(self) -> list[tuple[str, str, bytes | None, str, int, str]]:
        return [
            ("POST", "/api/check", b"{oops", "application/json", 400, "JSON 본문을 해석할 수 없습니다"),
            ("POST", "/api/check", b"[1]", "application/json", 400, "요청 본문은 JSON 객체여야 합니다"),
            ("POST", "/api/check", '{"text": "짧음"}'.encode("utf-8"), "application/json", 400, "분석할 텍스트가 너무 짧습니다"),
            ("POST", "/api/check", b"x", "text/plain", 400, "multipart/form-data 또는 application/json 본문이 필요합니다"),
            ("POST", "/api/compare", b"x", "text/plain", 400, "multipart/form-data 본문이 필요합니다"),
            ("GET", "/api/review", None, "", 400, "path 또는 artifact_id가 필요합니다"),
            ("GET", "/api/nothing-here", None, "", 404, "찾을 수 없는 경로입니다"),
        ]

    def _check(self, leg: str, rows: list[tuple[str, str, bytes | None, str, int, str]], send: Any) -> None:
        from deepfake_lens.error_text import english_prose

        for method, path, body, ctype, expected, text in rows:
            with self.subTest(leg=leg, method=method, path=path[:60], body=(body or b"")[:30]):
                status, payload = send(method, path, body, ctype)
                self.assertEqual(status, expected, payload)
                error = payload.get("error") if isinstance(payload, dict) else None
                self.assertIsInstance(error, str, payload)
                self.assertIn(text, error)
                self.assertIsNone(english_prose(error), error)

    def test_stdlib_web_server(self) -> None:
        import threading
        import urllib.error
        import urllib.request

        from deepfake_lens.webapp import CLIENT_HEADER, build_server

        server = build_server("127.0.0.1", 0, default_folder=self.folder)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def send(method: str, path: str, body: bytes | None, ctype: str) -> tuple[int, Any]:
            headers = {CLIENT_HEADER: "qa"}
            if ctype:
                headers["Content-Type"] = ctype
            request = urllib.request.Request(base + path, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    return response.status, json.loads(response.read() or b"null")
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read() or b"null")

        self._check("web", self._shared_rows() + self._web_rows(), send)

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx required")
    def test_api_server_shared_endpoints(self) -> None:
        from fastapi.testclient import TestClient

        client = TestClient(api_server.create_app(default_folder=self.folder))

        def send(method: str, path: str, body: bytes | None, ctype: str) -> tuple[int, Any]:
            headers = {"host": "localhost", "X-Deepfake-Lens-Client": "qa"}
            if ctype:
                headers["Content-Type"] = ctype
            response = client.request(method, path, content=body, headers=headers)
            payload = response.json()
            if isinstance(payload, dict) and "error" not in payload and "detail" in payload:
                payload = {"error": payload["detail"]}
            return response.status_code, payload

        self._check("api", self._shared_rows(), send)
        # Y8: the stream scan's own max_files query value.
        status, payload = send("POST", f"/api/scan/stream?directory={self.folder}&max_files=-1", None, "")
        self.assertEqual((status, payload), (400, {"error": "max_files는 1 이상이어야 합니다"}))

    def test_success_is_still_200(self) -> None:
        """The control: a valid request of an endpoint that had a 200 error still answers 200."""
        import urllib.parse

        payload = webapp_api._analyze_file_payload(urllib.parse.urlencode({"file": str(self.folder / "memo.txt")}))
        self.assertEqual(webapp_api.api_status(payload), 200)
        self.assertEqual(webapp_api.api_status(webapp_api._scan_status_payload("job=deadbeef")), 404)


class DocumentedEndpointsExistTest(unittest.TestCase):
    """R9-8 (round 9): the service document listed `/api/review-marks` (404 on the server)
    and said api-serve had no upload endpoint (it has /api/analyze-upload). Every endpoint
    of the REST table is a route of api-serve, every endpoint of the web GUI table a route
    of the web server — and every /api/ route of either server is documented."""

    DOC = Path(__file__).resolve().parents[2] / "docs" / "deepfake-lens-service.md"

    def _tables(self) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
        """((method, path) of the REST table, (method, path) of the web GUI table)."""
        import re

        text = self.DOC.read_text(encoding="utf-8")
        rest = text[text.index("## REST API endpoints"):text.index("## Web GUI endpoints")]
        web = text[text.index("## Web GUI endpoints"):text.index("## Error status codes")]
        rest_rows: set[tuple[str, str]] = set()
        for line in rest.splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) == 4 and re.fullmatch(r"(GET|POST|PUT|DELETE)", cells[0]):
                for path in re.findall(r"`(/[^`]*)`", cells[1]):
                    rest_rows.add((cells[0], path))
        web_rows: set[tuple[str, str]] = set()
        for line in web.splitlines():
            cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
            # R11-12: the GUI paths (/, /gui, /gui.css, /gui.js) are rows of the table too.
            match = re.fullmatch(r"(?:(GET|POST|PUT) )?`(/[^`]*)`", cells[0]) if len(cells) == 3 else None
            if match:
                web_rows.add((match.group(1) or "GET", match.group(2)))
        return rest_rows, web_rows

    def test_rest_table_matches_api_serve_routes(self) -> None:
        if not HAVE_FASTAPI:
            self.skipTest("fastapi/httpx not installed")
        import re

        rest_rows, _ = self._tables()
        self.assertGreaterEqual(len(rest_rows), 30, rest_rows)
        routes = {
            (method, re.sub(r"\{(\w+)(?::path)?\}", r"{\1}", route.path))
            for route in api_server.create_app().routes
            for method in (getattr(route, "methods", None) or ())
            if method != "HEAD"
        }
        missing = sorted(row for row in rest_rows if row not in routes)
        self.assertEqual(missing, [], "documented but not a route of api-serve")
        # R10-8: was limited to /api/ routes — /gui, /gui.css, /gui.js and
        # FastAPI's /docs, /docs/oauth2-redirect, /redoc, /openapi.json were
        # routes the REST table did not list. Every route is documented now
        # and the automatic docs are disabled.
        undocumented = sorted(row for row in routes if row not in rest_rows)
        self.assertEqual(undocumented, [], "a route of api-serve missing from the REST table")
        self.assertIn(("POST", "/api/analyze-upload"), rest_rows)
        self.assertTrue({("GET", "/gui"), ("GET", "/gui.css"), ("GET", "/gui.js")} <= rest_rows)
        from fastapi.testclient import TestClient

        client = TestClient(api_server.create_app())
        for path in ("/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json().get("detail"), "찾을 수 없는 경로입니다")

    def test_web_table_matches_web_server_routes(self) -> None:
        import json as _json
        import tempfile
        import threading
        import urllib.error
        import urllib.request

        from deepfake_lens.webapp import CLIENT_HEADER, build_server

        _, web_rows = self._tables()
        self.assertGreaterEqual(len(web_rows), 15, web_rows)
        roots = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())  # the server registers its folder
        roots.start()
        self.addCleanup(roots.stop)
        with tempfile.TemporaryDirectory() as tmp:
            server = build_server("127.0.0.1", 0, default_folder=Path(tmp))
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            base = f"http://127.0.0.1:{server.server_address[1]}"

            def status_and_text(method: str, path: str) -> tuple[int, str]:
                data = b"{}" if method != "GET" else None
                headers = {CLIENT_HEADER: "qa", "Content-Type": "application/json"}
                request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
                try:
                    with urllib.request.urlopen(request, timeout=60) as response:
                        return response.status, response.read().decode("utf-8", "replace")
                except urllib.error.HTTPError as exc:
                    return exc.code, exc.read().decode("utf-8", "replace")

            env = patch.dict(os.environ, {"DEEPFAKE_LENS_REVIEWS": str(Path(tmp) / "r.json"),
                                          "DEEPFAKE_LENS_FEEDBACK": str(Path(tmp) / "f.jsonl")})
            with env:
                for method, path in sorted(web_rows):
                    with self.subTest(method=method, path=path):
                        status, text = status_and_text(method, path)
                        try:
                            body = _json.loads(text)
                            error = body.get("error") if isinstance(body, dict) else None
                        except ValueError:
                            error = text
                        self.assertFalse(status == 404 and error == "찾을 수 없는 경로입니다", (status, text[:200]))
                # the route the document used to list is not one
                status, text = status_and_text("GET", "/api/review-marks")
                self.assertEqual((status, _json.loads(text)["error"]), (404, "찾을 수 없는 경로입니다"))
        for method, path in (("GET", "/api/reviews"), ("GET", "/api/review"), ("POST", "/api/review"),
                             ("POST", "/api/check"), ("POST", "/api/compare")):
            self.assertIn((method, path), web_rows)
        # and every /api/ route the web server dispatches is in the table
        import inspect
        import re

        from deepfake_lens import webapp

        source = inspect.getsource(webapp)
        get_part = source[source.index("def do_GET"):source.index("def do_POST")]
        post_part = source[source.index("def do_POST"):]
        dispatched = {("GET", path) for path in re.findall(r'parsed\.path == "(/api/[^"]+)"', get_part)}
        dispatched |= {("POST", path) for path in re.findall(r'parsed\.path == "(/api/[^"]+)"', post_part)}
        self.assertGreaterEqual(len(dispatched), 15)
        # R11-12: and the only GET paths outside /api/ are the GUI shell and its assets.
        dispatched |= {("GET", path) for path in (*webapp.GUI_SHELL_PATHS, *webapp.GUI_STATIC_ASSETS)}
        self.assertEqual(sorted(dispatched - web_rows), [], "a web-server route missing from the web GUI table")
        self.assertEqual(sorted(web_rows - dispatched), [], "a documented web route the server does not dispatch")

    def test_web_server_unknown_paths_are_korean_404(self) -> None:
        """R11-12 (round 11): every path outside /api/ served the GUI HTML (/docs,
        /favicon.ico, …). Only /, /gui, /gui.css and /gui.js are served; any other path
        is 404 {"error": "찾을 수 없는 경로입니다"} — the API server's GUI paths."""
        import json as _json
        import tempfile
        import threading
        import urllib.error
        import urllib.request

        from deepfake_lens.webapp import build_server

        roots = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        with tempfile.TemporaryDirectory() as tmp:
            server = build_server("127.0.0.1", 0, default_folder=Path(tmp))
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            base = f"http://127.0.0.1:{server.server_address[1]}"

            def get(path: str) -> tuple[int, str, str]:
                try:
                    with urllib.request.urlopen(base + path, timeout=60) as response:
                        return response.status, response.headers.get("Content-Type", ""), response.read().decode("utf-8", "replace")
                except urllib.error.HTTPError as exc:
                    return exc.code, exc.headers.get("Content-Type", ""), exc.read().decode("utf-8", "replace")

            for path in ("/", "/gui", "/?folder=x"):
                with self.subTest(path=path):
                    status, ctype, body = get(path)
                    self.assertEqual(status, 200)
                    self.assertIn("text/html", ctype)
                    self.assertIn("<html", body.lower())
            for path, ctype_part in (("/gui.css", "text/css"), ("/gui.js", "javascript")):
                with self.subTest(path=path):
                    status, ctype, _ = get(path)
                    self.assertEqual((status, ctype_part in ctype), (200, True))
            for path in ("/docs", "/redoc", "/openapi.json", "/favicon.ico", "/gui2", "/gui/x", "/index.html",
                         "/etc/passwd", "/..%2f..%2fetc%2fpasswd", "/api", "/apix/scan"):
                with self.subTest(path=path):
                    status, ctype, body = get(path)
                    self.assertEqual(status, 404, body[:200])
                    self.assertIn("application/json", ctype)
                    self.assertEqual(_json.loads(body), {"error": "찾을 수 없는 경로입니다"})
        from deepfake_lens import webapp

        if HAVE_FASTAPI:
            # The same GUI paths as api-serve.
            api_gui = {route.path for route in api_server.create_app().routes if not route.path.startswith("/api/")}
            self.assertEqual(api_gui, set(webapp.GUI_SHELL_PATHS) | set(webapp.GUI_STATIC_ASSETS))


class TrailingSlashParityTest(unittest.TestCase):
    """R12-11 (round 12): api-serve redirected "/gui/" to "/gui" (Starlette's
    redirect_slashes, 307) while the stdlib web server answered 404. A path with
    a trailing slash is not a route on either server: both answer 404 (Korean
    body), never a redirect."""

    PATHS = ("/gui/", "/gui.css/", "/gui.js/", "/api/scan/", "/api/scan-status/", "/api/reviews/", "/api/version/")

    def _web_statuses(self) -> dict[str, tuple[int, str]]:
        import tempfile
        import threading
        import urllib.error
        import urllib.request

        from deepfake_lens.webapp import build_server

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args: object, **kwargs: object) -> None:
                return None

        roots = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        out: dict[str, tuple[int, str]] = {}
        with tempfile.TemporaryDirectory() as tmp:
            server = build_server("127.0.0.1", 0, default_folder=Path(tmp))
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
            try:
                opener = urllib.request.build_opener(NoRedirect)
                for path in self.PATHS:
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{server.server_address[1]}{path}", headers={"X-Deepfake-Lens-Client": "gui"}
                    )
                    try:
                        with opener.open(request, timeout=60) as response:
                            out[path] = (response.status, response.read().decode("utf-8", "replace"))
                    except urllib.error.HTTPError as exc:
                        out[path] = (exc.code, exc.read().decode("utf-8", "replace"))
            finally:
                server.shutdown()
                server.server_close()
        return out

    def test_web_server(self) -> None:
        for path, (status, body) in self._web_statuses().items():
            with self.subTest(path=path):
                self.assertEqual(status, 404, body[:200])
                self.assertIn("찾을 수 없는 경로입니다", body)

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx not installed")
    def test_api_server_matches_the_web_server(self) -> None:
        from fastapi.testclient import TestClient

        client = TestClient(api_server.create_app(), raise_server_exceptions=False)
        web = self._web_statuses()
        for path in self.PATHS:
            with self.subTest(path=path):
                response = client.get(path, headers={"host": "localhost", "X-Deepfake-Lens-Client": "gui"}, follow_redirects=False)
                self.assertEqual(response.status_code, 404, (path, response.headers.get("location"), response.text[:200]))
                self.assertNotIn("location", {key.lower() for key in response.headers})
                self.assertIn("찾을 수 없는 경로입니다", response.text)
                self.assertEqual(response.status_code, web[path][0])
        # The paths without the slash are still served by both.
        for path in ("/gui", "/gui.css", "/gui.js"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path, headers={"host": "localhost"}).status_code, 200)


class ErrorTableEveryRowTest(unittest.TestCase):
    """P8 (round 8): every row of the error-status table (docs/deepfake-lens-service.md,
    "Error status codes") is sent to every server it names. api-serve's own file
    endpoints answered a missing file with 200 "success" (classify: 500, compare: a
    misleading 400), review routes a JSON array with 500 and a non-media preview with
    403; the table said otherwise and no test covered those rows."""

    DOC = Path(__file__).resolve().parents[2] / "docs" / "deepfake-lens-service.md"

    def setUp(self) -> None:
        import tempfile
        import wave

        roots = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        self.folder = base / "case"
        (self.folder / "sub").mkdir(parents=True)
        (self.folder / "memo.txt").write_text("사건 메모입니다. 사람이 쓴 짧은 메모입니다.", encoding="utf-8")
        _write_png(self.folder / "photo.png", seed=3)
        with wave.open(str(self.folder / "tone.wav"), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b"\x00\x10" * 8000)
        self.outside = base / "outside"
        self.outside.mkdir()
        _write_png(self.outside / "secret.png", seed=9)
        webapp_api.configure_read_roots(self.folder)
        feedback = patch.dict(os.environ, {"DEEPFAKE_LENS_FEEDBACK": str(base / "fb.jsonl"), "DEEPFAKE_LENS_REVIEWS": str(base / "reviews.json")})
        feedback.start()
        self.addCleanup(feedback.stop)

    # -- the table ------------------------------------------------------------

    def _table(self) -> dict[str, tuple[str, set[int]]]:
        """Row ID -> (endpoint/server cell, statuses) from the documented table."""
        import re

        text = self.DOC.read_text(encoding="utf-8")
        section = text[text.index("## Error status codes"):text.index("## Honesty contract")]
        rows: dict[str, tuple[str, set[int]]] = {}
        for line in section.splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) == 5 and re.fullmatch(r"E\d+", cells[0]):
                rows[cells[0]] = (cells[1], {int(code) for code in re.findall(r"\d{3}", cells[3])})
        return rows

    @staticmethod
    def _servers(cell: str) -> set[str]:
        found = set()
        if "(both)" in cell or "(web / api)" in cell:
            found |= {"web", "api"}
        if "(web)" in cell:
            found.add("web")
        if "(api)" in cell:
            found.add("api")
        return found

    # -- the requests ------------------------------------------------------------

    def _multipart(self, *parts: tuple[str, str, bytes], close: bool = True) -> tuple[bytes, str]:
        boundary = "----p8table"
        body = b"".join(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{name}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n"
            for field, name, data in parts
        )
        if close:
            body += f"--{boundary}--\r\n".encode()
        return body, f"multipart/form-data; boundary={boundary}"

    def _cases(self) -> dict[str, list[dict[str, Any]]]:
        from urllib.parse import quote

        import deepfake_lens.webapp as webapp_module

        f = self.folder
        q = lambda path: quote(str(path))  # noqa: E731
        row = webapp_api._scan_payload(f"folder={q(f)}&no_default_engine=true", default_folder=f)
        memo_row = next(item for item in row["items"] if item["path"] == "memo.txt")
        scan_root = str(f)

        def raise_(*_: Any, **__: Any) -> Any:
            raise RuntimeError("주입된 실패")

        def no_pymupdf() -> Any:
            raise ImportError("pymupdf")

        def report(body: dict[str, Any]) -> bytes:
            return json.dumps(body).encode("utf-8")

        png = (f / "photo.png").read_bytes()
        upload, upload_type = self._multipart(("files", "photo.png", png))
        cut, cut_type = self._multipart(("files", "photo.png", png), close=False)
        pair, pair_type = self._multipart(("a", "a.txt", "글 하나입니다. 충분히 긴 문장입니다.".encode()), ("b", "b.wav", (f / "tone.wav").read_bytes()))
        cut_pair, _ = self._multipart(("a", "a.txt", b"one"), ("b", "b.txt", b"two"), close=False)
        files = [
            ("POST", "/api/analyze/image?file_path={}"), ("POST", "/api/analyze/audio?file_path={}"),
            ("POST", "/api/analyze/face?file_path={}"), ("POST", "/api/analyze/forensic?file_path={}"),
            ("POST", "/api/classify?file_path={}"), ("POST", "/api/check?file_path={}"),
            ("POST", "/api/check/stream?file_path={}"),
            ("POST", "/api/compare?file_path_a={}&file_path_b=" + q(f / "memo.txt")),
        ]
        jobs = {f"fake{i}": {"status": "running", "created": time.time(), "cancel": __import__("threading").Event()} for i in range(40)}

        def case(server: str, method: str, path: str, status: int, text: str, body: bytes | None = None, ctype: str = "",
                 patches: tuple[Any, ...] = (), headers: dict[str, str] | None = None) -> dict[str, Any]:
            return {"server": server, "method": method, "path": path, "status": status, "text": text, "body": body,
                    "ctype": ctype, "patches": patches, "headers": headers}

        both = ("web", "api")
        # R11-3: a body nested past the JSON parser's recursion limit.
        DEEP = 50_000
        deep = b'{"items": ' + b"[" * DEEP + b"]" * DEEP + b"}"
        return {
            "E1": [case(s, "GET", "/api/stats", 401, "헤더", headers={}) for s in both],
            "E2": [case(s, "GET", "/api/nothing-here", 404, "찾을 수 없는 경로입니다") for s in both],
            "E3": [case("web", "DELETE", "/api/scan", 501, "지원하지 않는 요청 메서드입니다"),
                   case("api", "DELETE", "/api/scan", 405, "허용되지 않는 요청 메서드입니다")],
            "E4": [case("web", "GET", "/api/stats", 500, "서버 내부 오류가 발생했습니다", patches=(patch.object(webapp_module, "_stats_payload", raise_),)),
                   case("api", "GET", "/api/stats", 500, "서버 내부 오류가 발생했습니다", patches=(patch.object(webapp_api, "_stats_payload", raise_),))],
            "E5": [case(s, "GET", "/api/scan?max_files=abc", 400, "max_files는 정수여야 합니다") for s in both]
            + [case(s, "GET", f"/api/scan?folder={q(f)}&pixel=bogus", 400, "pixel") for s in both],
            "E6": [case(s, "GET", "/api/scan?max_files=-3", 400, "max_files는 1 이상이어야 합니다") for s in both]
            + [case(s, "GET", "/api/scan?max_file_bytes=0", 400, "max_file_bytes는 1 이상이어야 합니다") for s in both]
            + [case("api", "POST", f"/api/scan/stream?directory={q(f)}&max_files=0", 400, "max_files는 1 이상이어야 합니다")],
            "E7": [case(s, "GET", f"/api/scan?folder={q(f / 'gone')}&no_default_engine=true", 400, "폴더를 찾을 수 없습니다: ") for s in both]
            + [case(s, "GET", f"/api/scan?folder={q(f / 'memo.txt')}&no_default_engine=true", 400, "폴더가 아니라 파일입니다") for s in both],
            "E8": [case(s, "GET", f"/api/scan?folder={q(self.outside)}", 403, "허용되지 않은 경로") for s in both]
            + [case(s, "GET", f"/api/scan?folder_b64={fs_b64encode(str(self.outside))}", 403, "허용되지 않은 경로") for s in both],  # R13-3
            "E9": [case(s, "GET", f"/api/scan?folder={q(f)}&async=1", 400, "실행 중인 검사 작업이 너무 많습니다",
                        patches=(patch.dict(webapp_api._SCAN_JOBS, jobs),)) for s in both],
            "E10": [case(s, "GET", path, 400, webapp_api.JOB_PARAM_REQUIRED) for s in both for path in ("/api/scan-status", "/api/scan-cancel")],
            "E11": [case(s, "GET", path + "?job=deadbeef", 404, webapp_api.JOB_UNKNOWN) for s in both for path in ("/api/scan-status", "/api/scan-cancel")],
            "E12": [case(s, "GET", "/api/analyze-file", 400, webapp_api.FILE_PARAM_REQUIRED) for s in both],
            "E13": [case(s, "GET", f"/api/analyze-file?file={q(f / 'nope.png')}", 404, "파일을 찾을 수 없습니다: ") for s in both],
            "E14": [case(s, "GET", f"/api/analyze-file?file={q(f / 'sub')}", 400, "파일이 아니라 폴더입니다: ") for s in both],
            "E15": [case(s, "GET", f"/api/analyze-file?file={q(self.outside / 'secret.png')}", 403, "허용되지 않은 경로") for s in both]
            + [case(s, "GET", f"/api/analyze-file?file_b64={fs_b64encode(str(self.outside / 'secret.png'))}", 403, "허용되지 않은 경로") for s in both],  # R13-3
            "E16": [case(s, "GET", f"/api/analyze-file?file={q(f / 'memo.txt')}", 500, "파일 분석 중 오류가 발생했습니다",
                         patches=(patch("deepfake_lens.cli_standalone.analysis_result_for_path", raise_),)) for s in both],
            "E17": [case(s, "GET", f"/api/{kind}?path={q(self.outside / 'secret.png')}&root={q(f)}", 403, "허용되지 않은 경로") for s in both for kind in ("heatmap", "preview")]
            + [case(s, "GET", f"/api/preview?path={q(f / 'nope.png')}&root={q(f)}", 404, "파일을 찾을 수 없습니다") for s in both]
            + [case(s, "GET", f"/api/preview?path={q(f / 'memo.txt')}&root={q(f)}", 400, "미리보기를 지원하지 않는 형식입니다: .txt") for s in both],
            "E18": [case(s, "POST", "/api/analyze-upload", 400, "multipart/form-data 업로드가 필요합니다", b"x", "text/plain") for s in both]
            + [case(s, "POST", "/api/analyze-upload", 400, "업로드 본문이 잘렸습니다", cut, cut_type) for s in both]
            + [case(s, "POST", "/api/analyze-upload", 400, "업로드된 파일이 없습니다", b"------p8table--\r\n", upload_type) for s in both],
            "E19": [case("web", "POST", "/api/analyze-upload", 413, "업로드 크기가 상한", upload, upload_type, patches=(patch.object(webapp_module, "MAX_UPLOAD_BYTES", 16),)),
                    case("api", "POST", "/api/analyze-upload", 413, "업로드 크기가 상한", upload, upload_type, patches=(patch.object(webapp_api, "MAX_UPLOAD_BYTES", 16),))],
            "E20": [case("web", "POST", "/api/analyze-upload", 500, "업로드 분석 중 오류가 발생했습니다", upload, upload_type,
                         patches=(patch.object(webapp_module, "_analyze_upload_payload", raise_),))],
            "E21": [case("web", "POST", "/api/check", 400, text, body, ctype) for body, ctype, text in (
                (b"{oops", "application/json", "JSON 본문을 해석할 수 없습니다"),
                (b"[1]", "application/json", "요청 본문은 JSON 객체여야 합니다"),
                ('{"text": "짧음"}'.encode(), "application/json", "분석할 텍스트가 너무 짧습니다"),
                (b"x", "text/plain", "multipart/form-data 또는 application/json 본문이 필요합니다"),
                (cut, cut_type, "업로드 본문이 잘렸습니다"),
            )],
            "E22": [case("web", "POST", "/api/check", 413, "요청 본문이 상한", upload, upload_type, patches=(patch.object(webapp_module, "MAX_UPLOAD_BYTES", 16),))],
            # (text > 256 KB cannot travel in a test client's query string; the
            # limit is the same constant check as the web server's E21.)
            "E23": [case("api", "POST", "/api/check", 400, "file_path 또는 text가 필요합니다"),
                    case("api", "POST", "/api/check?text=", 400, "file_path 또는 text가 필요합니다")],
            "E24": [case("web", "POST", "/api/compare", 400, text, body, ctype) for body, ctype, text in (
                (b"x", "text/plain", "multipart/form-data 본문이 필요합니다"),
                (upload, upload_type, "비교할 파일 2개가 필요합니다"),
                (pair, pair_type, "지원되는 쌍이 아닙니다(.txt, .wav)"),
                (cut_pair, pair_type, "업로드 본문이 잘렸습니다"),
            )],
            "E25": [case("web", "POST", "/api/compare", 500, "비교 분석 중 오류가 발생했습니다", pair, pair_type,
                         patches=(patch.object(webapp_module, "_compare_payload", raise_),))],
            "E26": [case("api", "POST", f"/api/compare?file_path_a={q(f / 'memo.txt')}&file_path_b={q(f / 'tone.wav')}", 400, "지원되는 쌍이 아닙니다(.txt, .wav)"),
                    case("api", "POST", f"/api/compare?file_path_a={q(f / 'memo.txt')}&file_path_b={q(f / 'memo.txt')}", 500, "주입된 실패",
                         patches=(patch("deepfake_lens.core.compare_files", raise_),))],
            "E27": [case(s, "POST", "/api/report", 400, text, body, "application/json") for s in both for body, text in (
                (b"{oops", "JSON 본문을 해석할 수 없습니다"),
                # R11-3 (round 11): a too-deep body was a 500 (RecursionError).
                (deep, "JSON 중첩이 너무 깊습니다"),
                (b'{"items": [{"path": "a", "x": ' + b"[" * DEEP + b"]" * DEEP + b"}]}", "JSON 중첩이 너무 깊습니다"),
                (b"[1]", "보고서 요청 본문은 JSON 객체여야 합니다"),
                (report({"items": [dict(memo_row, path="x.zip::y", container="x.zip", member="z")], "scan_root": scan_root}), "path가 container::member와 일치하지 않습니다"),
                # R20-4 (round 20): a duplicate of itself was signed with no analysis.
                (report({"items": [dict(memo_row, status="duplicate", duplicate_of="memo.txt", result=None)], "scan_root": scan_root}), "검사 결과 항목 「memo.txt」의 duplicate_of"),
                (report({"items": [memo_row], "options": "deep", "scan_root": scan_root}), "options 값은 JSON 객체여야 합니다"),
            )],
            "E28": [case(s, "POST", "/api/report", 403, "허용되지 않은 경로", report({"items": [{"path": "memo.txt", "heatmap_path": str(self.outside / "secret.png")}]}), "application/json") for s in both]
            + [case(s, "POST", "/api/report", 403, "허용되지 않은 경로", report({"items": [dict(memo_row, result=dict(memo_row["result"], pixel_analysis={"heatmap_path": str(self.outside / "secret.png")}))]}), "application/json") for s in both],
            "E29": [case(s, "POST", "/api/report", 400, text, report(body), "application/json") for s in both for body, text in (
                ({"items": [memo_row]}, webapp_api.REPORT_SCAN_ROOT_REQUIRED),
                ({"items": [memo_row], "scan_root": 3}, webapp_api.REPORT_SCAN_ROOT_NOT_STRING),
                ({"items": [memo_row], "scan_root": "case"}, "scan_root는 절대 경로여야 합니다"),
                ({"items": [memo_row], "scan_root": str(f / "gone")}, "스캔 폴더를 찾을 수 없습니다"),
            )],
            "E30": [case(s, "POST", "/api/report", 403, "허용되지 않은 경로", report({"items": [memo_row], "scan_root": str(self.outside)}), "application/json") for s in both],
            "E31": [case(s, "POST", "/api/report?format=pdf", 501, "PDF 보고서를 만들려면 pymupdf 패키지가 필요합니다", report({"items": [memo_row], "scan_root": scan_root}), "application/json",
                         patches=(patch("deepfake_lens.pdf_backend.import_pymupdf", no_pymupdf),)) for s in both]
            # R22-7 (round 22): the evidence statement without pymupdf is the same 501, before any re-analysis (was a 500 after it)
            + [case(s, "POST", f"/api/report?format={fmt}", 501, "PDF 보고서를 만들려면 pymupdf 패키지가 필요합니다", report({"items": [memo_row], "scan_root": scan_root}), "application/json",
                    patches=(patch("deepfake_lens.pdf_backend.import_pymupdf", no_pymupdf), patch("deepfake_lens.analysis_api.scan_file_run", raise_)))
               for s in both for fmt in ("evidence", "evidence-statement")]
            + [case(s, "POST", "/api/report?format=evidence", 500, "증거설명서 PDF 생성 실패", report({"items": [memo_row], "scan_root": scan_root}), "application/json",
                    # R22-7: a renderer is present (the engine check passes) and the statement PDF fails — 500
                    patches=(patch("deepfake_lens.pdf_backend.pymupdf_available", lambda: True),
                             patch("deepfake_lens.evidence_statement.write_evidence_statement_pdf", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("렌더러 없음"))))) for s in both],
            "E32": [case(s, "POST", "/api/feedback", 400, text, body, "application/json") for s in both for body, text in (
                (b"{oops", "JSON 본문을 해석할 수 없습니다"), (b"[1]", "피드백 요청 본문은 JSON 객체여야 합니다"),
                (deep, "JSON 중첩이 너무 깊습니다"),  # R11-3: was a 500
                (b'{"expected_label": "maybe", "path": "a"}', "expected_label은 "), (b'{"expected_label": "real"}', "path가 필요합니다"),
            )],
            "E33": [case("web", "GET", "/api/review", 400, "path 또는 artifact_id가 필요합니다")]
            + [case("web", "POST", "/api/review", 400, text, body, "application/json") for body, text in (
                # R11-3: was "JSON을 해석할 수 없습니다" — every server now says
                # "JSON 본문을 해석할 수 없습니다" (one parser, load_json_body).
                (b"{oops", "JSON 본문을 해석할 수 없습니다"), (b"[1]", "검토 요청 본문은 JSON 객체여야 합니다"),
                (deep, "JSON 중첩이 너무 깊습니다"),
                (b"{}", "artifact_id가 필요합니다"), (b'{"artifact_id": [1]}', "artifact_id 값은 문자열이어야 합니다"),
            )]
            + [case("api", "GET", "/api/review", 400, "path 또는 artifact_id 쿼리 매개변수가 필요합니다")]
            + [case("api", "POST", "/api/review", 400, text, body, "application/json") for body, text in (
                (b"{oops", "JSON 본문을 해석할 수 없습니다"), (b"[1]", "검토 요청 본문은 JSON 객체여야 합니다"),
                (deep, "JSON 중첩이 너무 깊습니다"),
                (b"{}", "본문에 artifact_id가 없습니다"), (b'{"artifact_id": [1]}', "artifact_id 값은 문자열이어야 합니다"),
            )]
            + [case("api", "PUT", "/api/artifacts/x/review", 400, text, body, "application/json") for body, text in (
                (b"{oops", "JSON 본문을 해석할 수 없습니다"), (b"[1]", "검토 요청 본문은 JSON 객체여야 합니다"),
                (deep, "JSON 중첩이 너무 깊습니다"),
            )],
            "E34": [case("api", "POST", path.format(q(f / "memo.txt")), 500, "주입된 실패",
                         patches=(patch("deepfake_lens.cli_standalone.analysis_result_for_path", raise_), patch("deepfake_lens.analysis_api.analyze_file", raise_)))
                    for _, path in files[:6] if "/face" not in path],
            "E35": [case("api", "GET", "/api/jobs/zzz", 404, "알 수 없거나 이미 끝난 작업입니다"),
                    case("api", "POST", "/api/jobs/zzz/cancel", 404, "알 수 없거나 이미 끝난 작업입니다")],
            "E36": [case("api", "POST", f"/api/{path}", 429, "실행 중인 작업이 너무 많습니다", patches=(patch.object(api_server, "MAX_JOBS", 0),))
                    for path in (f"check/stream?file_path={q(f / 'memo.txt')}", f"scan/stream?directory={q(f)}")],
            "E37": [case("api", "POST", "/api/analyze/image", 422, "요청 매개변수 오류"),
                    case("api", "POST", "/api/multimodal?image_score=abc", 422, "요청 매개변수 오류")],
            "E38": [case("api", method, path.format(q(f / "nope.png")), 404, "파일을 찾을 수 없습니다: ") for method, path in files],
            "E39": [case("api", method, path.format(q(f / "sub")), 400, "파일이 아니라 폴더입니다: ") for method, path in files],
            "E40": [case("api", method, path.format(q(self.outside / "secret.png")), 403, "허용되지 않은 경로") for method, path in files],
            "E41": [case("api", "POST", f"/api/multimodal?file_path={q(f / 'nope.png')}", 400, "알 수 없는 매개변수입니다: file_path"),
                    case("api", "POST", "/api/multimodal?image_score=10&bogus=1", 400, "알 수 없는 매개변수입니다: bogus")],
            # R9-3 (round 9): the stream endpoints answered these with 200 + an SSE error event.
            "E42": [case("api", "POST", "/api/scan/stream?directory=", 400, api_server.DIRECTORY_REQUIRED),
                    case("api", "POST", f"/api/scan/stream?directory={q(f / 'gone')}", 400, "폴더를 찾을 수 없습니다: "),
                    case("api", "POST", f"/api/scan/stream?directory={q(f / 'memo.txt')}", 400, "폴더가 아니라 파일입니다")],
            "E43": [case("api", "POST", f"/api/scan/stream?directory={q(self.outside)}", 403, "허용되지 않은 경로"),
                    case("api", "POST", f"/api/scan/stream?directory={q(self.outside / 'gone')}", 403, "허용되지 않은 경로")],
            "E44": [case("api", "POST", "/api/check/stream", 400, api_server.TEXT_OR_FILE_REQUIRED),
                    case("api", "POST", "/api/check/stream?text=%20%20&file_path=", 400, api_server.TEXT_OR_FILE_REQUIRED),
                    case("api", "POST", "/api/check/stream?text=" + q("가" * 10), 400, api_server.TEXT_TOO_LARGE,
                         patches=(patch.object(api_server, "MAX_TEXT_CHARS", 4),))],
            # R9-8 (round 9): an empty text was analyzed (200).
            "E45": [case("api", "POST", "/api/analyze/text?text=", 400, api_server.TEXT_EMPTY),
                    case("api", "POST", "/api/analyze/text?text=%20%0A%20", 400, api_server.TEXT_EMPTY)],
            # R12-4 / R13-3: a malformed base64 path parameter.
            "E46": [case(s, "GET", path, 400, PATH_B64_INVALID) for s in both for path in (
                "/api/analyze-file?file_b64=%40%40", "/api/scan?folder_b64=%40%40", "/api/scan?async=1&folder_b64=AA%3D%3D",
                f"/api/preview?path_b64=%40%40&root={q(f)}", "/api/heatmap?path_b64=x.png&root_b64=%40%40",
            )],
            # R22-3 (round 22): an examiner record inside the evidence (here: the overrides named inside the root) is refused.
            "E49": [case(s, "POST", "/api/feedback", 409, "저장하지 않았습니다", json.dumps({"path": "memo.txt", "expected_label": "real"}).encode(),
                         "application/json", patches=(patch.dict(os.environ, {"DEEPFAKE_LENS_FEEDBACK": str(f / "fb.jsonl")}),)) for s in both]
            + [case(s, "POST", "/api/review", 409, "저장하지 않았습니다", json.dumps({"artifact_id": "memo.txt", "star": True}).encode(),
                    "application/json", patches=(patch.dict(os.environ, {"DEEPFAKE_LENS_REVIEWS": str(f / "reviews.json")}),
                                                 patch("deepfake_lens.reviews._DEFAULT_STORE", None))) for s in both]
            + [case("api", "PUT", "/api/artifacts/memo.txt/review", 409, "저장하지 않았습니다", json.dumps({"star": True}).encode(),
                    "application/json", patches=(patch.dict(os.environ, {"DEEPFAKE_LENS_REVIEWS": str(f / "reviews.json")}),
                                                 patch("deepfake_lens.reviews._DEFAULT_STORE", None)))],
            # R22-6 (round 22): a NUL in a path value was a 500 (PathValuesWithNulAre400Test sends every endpoint).
            "E48": [case(s, "GET", f"/api/analyze-file?file={q(f / 'a')}%00", 400, "NUL 문자") for s in both]
            + [case(s, "POST", "/api/report", 400, "NUL 문자", report({"items": [memo_row], "scan_root": scan_root + "\x00"}), "application/json") for s in both]
            + [case("api", "POST", f"/api/classify?file_path={q(f / 'a')}%00", 400, "NUL 문자")],
            # R19-13 (round 19): a posted row the server's re-analysis contradicts (here its digest) — every format.
            "E47": [case(s, "POST", f"/api/report?format={fmt}", 409, webapp_api.REPORT_ROWS_CHANGED,
                         report({"items": [dict(memo_row, sha256="0" * 64)], "scan_root": scan_root, "options": {"no_default_engine": "true"}}),
                         "application/json") for s in both for fmt in ("json", "html", *(("evidence",) if HAVE_PYMUPDF else ()))],  # R22-7
        }

    # -- the servers ---------------------------------------------------------------

    def _web(self):
        import threading
        import urllib.error
        import urllib.request

        from deepfake_lens.webapp import CLIENT_HEADER, build_server

        server = build_server("127.0.0.1", 0, default_folder=self.folder)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def send(method: str, path: str, body: bytes | None, ctype: str, headers: dict[str, str] | None) -> tuple[int, bytes]:
            sent = {CLIENT_HEADER: "qa"} if headers is None else dict(headers)
            if ctype:
                sent["Content-Type"] = ctype
            request = urllib.request.Request(base + path, data=body, headers=sent, method=method)
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read()

        return send

    def _api(self):
        from fastapi.testclient import TestClient

        client = TestClient(api_server.create_app(default_folder=self.folder), raise_server_exceptions=False)

        def send(method: str, path: str, body: bytes | None, ctype: str, headers: dict[str, str] | None) -> tuple[int, bytes]:
            sent = {"host": "localhost", **({"X-Deepfake-Lens-Client": "qa"} if headers is None else headers)}
            if ctype:
                sent["Content-Type"] = ctype
            response = client.request(method, path, content=body, headers=sent)
            return response.status_code, response.content

        return send

    @staticmethod
    def _text(raw: bytes) -> str:
        try:
            payload = json.loads(raw)
        except ValueError:
            return raw.decode("utf-8", "replace")
        if isinstance(payload, dict):
            return str(payload.get("error") or payload.get("detail") or payload.get("message") or "")
        return ""

    def test_every_table_row_has_cases_for_its_servers(self) -> None:
        table = self._table()
        cases = self._cases()
        self.assertEqual(sorted(table, key=lambda k: int(k[1:])), sorted(cases, key=lambda k: int(k[1:])), "one case list per table row")
        for row_id, (cell, statuses) in table.items():
            with self.subTest(row=row_id):
                self.assertEqual({c["server"] for c in cases[row_id]}, self._servers(cell), cell)
                self.assertTrue({c["status"] for c in cases[row_id]} <= statuses, (statuses, cell))

    def test_r9_3_stream_refusals_never_open_a_stream(self) -> None:
        """R9-3 (round 9): a refused stream request is a JSON error, never a 200 event stream;
        the web server has no stream endpoint (E2: 404 for the same requests)."""
        cases = [c for row in ("E6", "E36", "E38", "E39", "E40", "E42", "E43", "E44") for c in self._cases()[row] if "/stream" in c["path"]]
        self.assertGreaterEqual(len(cases), 12)
        senders = {"web": self._web()}
        if HAVE_FASTAPI:
            senders["api"] = self._api()
        import contextlib

        for c in cases:
            for name, send in senders.items():
                with self.subTest(server=name, path=c["path"][:80]):
                    with contextlib.ExitStack() as stack:
                        for patcher in c["patches"]:
                            stack.enter_context(patcher)
                        status, raw = send(c["method"], c["path"], c["body"], c["ctype"], c["headers"])
                    self.assertNotIn(b"event:", raw)
                    self.assertIsInstance(json.loads(raw), dict)
                    self.assertEqual(status, c["status"] if name == "api" else 404, raw[:200])

    def test_every_row_on_its_servers(self) -> None:
        import contextlib

        from deepfake_lens.error_text import english_prose

        senders = {"web": self._web()}
        if HAVE_FASTAPI:
            senders["api"] = self._api()
        for row_id, cases in self._cases().items():
            for c in cases:
                send = senders.get(c["server"])
                if send is None:
                    continue
                with self.subTest(row=row_id, server=c["server"], method=c["method"], path=c["path"][:70], body=(c["body"] or b"")[:24]):
                    with contextlib.ExitStack() as stack:
                        for patcher in c["patches"]:
                            stack.enter_context(patcher)
                        status, raw = send(c["method"], c["path"], c["body"], c["ctype"], c["headers"])
                    text = self._text(raw)
                    self.assertEqual(status, c["status"], raw[:300])
                    self.assertIn(c["text"], text, raw[:300])
                    self.assertIsNone(english_prose(text.replace(str(self.folder), "").replace(str(self.outside), "")), text)


class ReportUnrecordedFilesAreTheServersTest(_ReportLegsFixture):
    """R22-1 (round 22): the "기록되지 않은 파일" section of a web report was counted from the posted rows only
    (``summarize(items, capped=False)``). A scan capped at max_files, a flat scan with files in a subfolder, or a
    post that left rows out was signed ``total_files 0`` / ``cap_reached false`` and every rendering (HTML, forensic
    PDF, evidence statement) said "없음 — 분석 대상의 모든 파일에 분석 결과가 있습니다" — the normal GUI flow, no
    attacker. The counts are now the server's own (its scan job of the folder, else its own walk of the scan folder
    with the report's options) and, when it cannot know, "확인 불가" — never "없음"."""

    FORMATS = ("json", "html", *(("evidence", "pdf") if HAVE_PYMUPDF else ()))
    NONE = "없음 — 분석 대상의 모든 파일에 분석 결과가 있습니다"
    _scan = ReportRefusesChangedRowsTest._scan

    def setUp(self) -> None:
        super().setUp()
        registry = patch.object(webapp_api, "_SCAN_JOBS", {})
        registry.start()
        self.addCleanup(registry.stop)
        (self.folder / "sub").mkdir()
        _write_png(self.folder / "sub" / "inner.png", seed=4)
        _write_png(self.folder / "zlast.png", seed=5)

    def _report(self, rows: list[dict[str, Any]], **extra: object) -> dict[tuple[str, str], tuple[int, str, dict[str, Any] | None]]:
        out: dict[tuple[str, str], tuple[int, str, dict[str, Any] | None]] = {}
        for leg, make in self._legs():
            post = make()
            for fmt in self.FORMATS:
                status, _, raw = post("/api/report", self._json({"items": rows, "format": fmt, **extra}))
                text = _artifact_text(raw, fmt) if status == 200 else raw.decode("utf-8", "replace")
                out[(leg, fmt)] = (status, text, json.loads(raw) if fmt == "json" and status == 200 else None)
        return out

    def _assert_section(self, results: dict[tuple[str, str], tuple[int, str, dict[str, Any] | None]], expected: str) -> None:
        for (leg, fmt), (status, text, _) in results.items():
            with self.subTest(leg=leg, format=fmt):
                self.assertEqual(status, 200, text[:400])
                self.assertNotIn(self.NONE, text)
                self.assertIn(expected, " ".join(text.split()))

    def _signed(self, results: dict[tuple[str, str], tuple[int, str, dict[str, Any] | None]]) -> list[dict[str, Any]]:
        return [body for (_, fmt), (_, _, body) in results.items() if fmt == "json" and body is not None]

    def test_a_capped_scan_signs_the_files_over_the_cap(self) -> None:
        rows = self._scan("--max-files", "2")  # bundle.zip, target.png analyzed; zlast.png over the cap
        self.assertNotIn("zlast.png", rows)
        results = self._report(list(rows.values()), options={"max_files": "2"})
        self._assert_section(results, "파일 상한: 파일 수 상한(--max-files)에 도달해 검사하지 않은 파일 1개")
        for body in self._signed(results):
            unrecorded = body["unrecorded_files"]
            self.assertTrue(unrecorded["cap_reached"])
            self.assertEqual(body["summary"]["files_over_cap"], 1)
            self.assertEqual(unrecorded["listing"], {"source": "walk", "not_in_report": 0})

    def test_a_flat_scan_signs_the_subfolder_files(self) -> None:
        rows = self._scan()
        results = self._report(list(rows.values()))
        self._assert_section(results, "검사하지 않은 하위 폴더 안의 파일 1개")
        for body in self._signed(results):
            self.assertEqual(body["summary"]["subfolders_skipped"], 1)
            self.assertEqual(body["unrecorded_files"]["subfolders"], [{"path": "sub", "files": 1, "complete": True}])

    def test_rows_left_out_of_the_post_are_counted(self) -> None:
        rows = self._scan()
        posted = [row for path, row in rows.items() if path != "zlast.png"]
        results = self._report(posted)
        self._assert_section(results, "보고서에 행 없음: 서버가 검사 폴더에서 찾은 파일 중 이 보고서에 서명된 행이 없는 파일 1개")
        for body in self._signed(results):
            self.assertEqual(body["unrecorded_files"]["listing"]["not_in_report"], 1)
            self.assertIn({"code": "not_in_report", "label": "보고서에 행 없음", "count": 1}, body["unrecorded_files"]["reasons"])

    def test_the_servers_scan_job_is_the_listing(self) -> None:
        started = webapp_api._scan_job_start(f"folder={self.folder}&no_default_engine=true&max_files=2", default_folder=self.folder)
        state: dict[str, Any] = {}
        for _ in range(600):
            state = webapp_api._scan_status_payload(f"job={started['job_id']}")
            if state["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(state["status"], "done", state)
        result = state["result"]
        # posted without options: the job (max_files 2, flat) — not the default walk — gives the counts
        results = self._report(list(result["items"]), scan_job=started["job_id"], options={"no_default_engine": "true"})
        self._assert_section(results, "파일 상한: 파일 수 상한(--max-files)에 도달해 검사하지 않은 파일 1개")
        for body in self._signed(results):
            self.assertEqual(body["unrecorded_files"]["listing"], {"source": "scan_job", "not_in_report": 0})
            self.assertEqual(body["summary"]["subfolders_skipped"], 1)

    def _cancelled_job(self) -> tuple[str, dict[str, Any]]:
        """A scan job of the folder that the server finished "cancelled" after its first file (as /api/scan-cancel does)."""
        calls = {"n": 0}

        def stop() -> bool:
            calls["n"] += 1
            return calls["n"] > 1

        result = webapp_api._scan_payload(f"folder={self.folder}&no_default_engine=true", default_folder=self.folder, should_stop=stop)
        summary = result["summary"]
        assert isinstance(summary, dict)
        self.assertTrue(summary["cancelled"])
        self.assertGreater(summary["files_cancelled"], 0)
        webapp_api._SCAN_JOBS["job-cancelled"] = {"status": "cancelled", "created": time.time(), "finished": time.time(), "result": result}
        return "job-cancelled", result

    def test_a_cancelled_scan_job_is_cancelled_without_the_posted_summary(self) -> None:
        """R23-3 (round 23): a cancelled job posted as ``scan_job`` without ``summary.cancelled`` signed its rows as the
        whole folder — listing ``{"source": "scan_job", "not_in_report": 0}``, ``summary.cancelled false`` and "없음 —
        분석 대상의 모든 파일에 분석 결과가 있습니다" in every format, although the server knew the files it never analyzed.
        The job's own state decides: cancelled, its count, source "server" — whatever the post's summary says."""
        job_id, result = self._cancelled_job()
        expected = int(result["summary"]["files_cancelled"])  # type: ignore[index]
        rows = list(result["items"])  # type: ignore[arg-type]
        for posted in ({}, {"summary": {"cancelled": False}}, {"summary": {}}):
            results = self._report(rows, scan_job=job_id, options={"no_default_engine": "true"}, **posted)
            with self.subTest(posted=posted):
                self._assert_section(results, f"사용자 취소: 검사가 취소되어 분석하지 않은 파일 {expected}개")
                for body in self._signed(results):
                    self.assertTrue(body["summary"]["cancelled"])
                    self.assertEqual(body["summary"]["files_cancelled"], expected)
                    self.assertEqual(body["files_cancelled_source"], "server")
                    self.assertIn({"code": "cancelled", "label": "사용자 취소", "count": expected}, body["unrecorded_files"]["reasons"])
                    self.assertEqual(body["unrecorded_files"]["listing"], {"source": "scan_job", "not_in_report": 0})
        # the post that said "cancelled" (R18-3/R19-3) signs the same body
        said = self._signed(self._report(rows, scan_job=job_id, options={"no_default_engine": "true"}, summary={"cancelled": True}))
        omitted = self._signed(self._report(rows, scan_job=job_id, options={"no_default_engine": "true"}))
        strip = {"generated_at", "signature", "signed_at", "report_id"}
        self.assertEqual([{k: v for k, v in body.items() if k not in strip} for body in said],
                         [{k: v for k, v in body.items() if k not in strip} for body in omitted])

    def test_a_cancelled_job_of_another_folder_or_a_finished_job_changes_nothing(self) -> None:
        """R19-3/R22-1 kept: only the server's own cancelled job of the same scan folder marks a report cancelled."""
        job_id, result = self._cancelled_job()
        rows = list(result["items"])  # type: ignore[arg-type]
        other = dict(webapp_api._SCAN_JOBS[job_id], result=dict(result, scan_root=str(self.folder / "sub")))
        webapp_api._SCAN_JOBS["job-other-folder"] = other
        for body in self._signed(self._report(rows, scan_job="job-other-folder", options={"no_default_engine": "true"})):
            self.assertFalse(body["summary"]["cancelled"])
            self.assertNotIn("files_cancelled_source", body)
            self.assertEqual(body["unrecorded_files"]["listing"]["source"], "walk")
            self.assertGreater(body["unrecorded_files"]["listing"]["not_in_report"], 0)  # the walk finds the files never analyzed
        # a posted "cancelled" without such a job: the caveat, count unverified (R19-3)
        for body in self._signed(self._report(rows, scan_job="nope", summary={"cancelled": True}, options={"no_default_engine": "true"})):
            self.assertEqual((body["summary"]["cancelled"], body["files_cancelled_source"]), (True, "unverified"))

    def test_signed_rows_outside_the_servers_listing_are_unknown(self) -> None:
        rows = self._scan("--recursive")  # sub/inner.png posted; the report's walk is flat (no options)
        results = self._report(list(rows.values()))
        self._assert_section(results, "기록되지 않은 파일: 확인 불가 — 서버가 검사 폴더의 파일 목록을 확인하지 못했습니다(서명된 행 1개가")
        for body in self._signed(results):
            self.assertEqual(body["unrecorded_files"]["listing"]["source"], "unknown")
            self.assertFalse(body["unrecorded_files"]["cap_reached"])
            self.assertEqual(body["summary"]["subfolders_skipped"], 0)
        # the same scan posted with its options: the walk agrees and nothing is unrecorded
        for body in self._signed(self._report(list(rows.values()), options={"recursive": "true"})):
            self.assertEqual(body["unrecorded_files"]["listing"], {"source": "walk", "not_in_report": 0})
            self.assertEqual(body["unrecorded_files"]["total_files"], 1)  # the link row in_link.png only

    def test_a_walk_limit_is_unknown(self) -> None:
        from deepfake_lens import scan_cache

        rows = self._scan("--recursive")
        with patch.object(scan_cache, "MAX_WALK_DIRS", 1):
            results = self._report([row for path, row in rows.items() if not path.startswith("sub")], options={"recursive": "true"})
        self._assert_section(results, "확인 불가 — 서버가 검사 폴더의 파일 목록을 확인하지 못했습니다(폴더 탐색 상한에 도달)")

    def test_upload_rows_only_are_unknown(self) -> None:
        upload = {"source": "upload", "path": "a.png", "name": "a.png", "status": "failed", "error": "x"}
        for leg, make in self._legs():
            status, _, raw = make()("/api/report", json.dumps({"items": [upload], "format": "json"}).encode("utf-8"))
            with self.subTest(leg=leg):
                self.assertEqual(status, 200, raw[:300])
                unrecorded = json.loads(raw)["unrecorded_files"]
                self.assertEqual(unrecorded["listing"]["source"], "unknown")
                self.assertNotIn(self.NONE, unrecorded["lines"][0])

    def test_regressions_of_the_report_rules_stay_closed(self) -> None:
        """R19-13 (a changed row: 409), R20-1 (a FIFO row re-derived as the scan's own row: 200) and R21-1 (a posted
        walk-limit folder row is excluded, never signed) keep their behaviour with the server listing."""
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.folder / "pipe.jpg")
        rows = self._scan()
        results = self._report(list(rows.values()))
        for (leg, fmt), (status, text, _) in results.items():
            with self.subTest(leg=leg, format=fmt, case="unchanged"):
                self.assertEqual(status, 200, text[:300])
        for body in self._signed(results):
            self.assertEqual(body["unrecorded_files"]["listing"], {"source": "walk", "not_in_report": 0})
        walk_row = dict(rows["in_link.png"], path="sub", name="sub", kind="unknown", size_bytes=0,
                        error="건너뜀: 탐색 상한 도달(폴더 1개) — 이 폴더 이하는 열지 않았습니다", sha256=None)
        for body in self._signed(self._report([*rows.values(), walk_row])):
            self.assertIn("sub", [row["path"] for row in body["excluded_items"]])
        _write_png(self.folder / "target.png", seed=9)
        for (leg, fmt), (status, text, _) in self._report(list(rows.values())).items():
            with self.subTest(leg=leg, format=fmt, case="changed"):
                self.assertEqual(status, 409, text[:300])


class PathValuesWithNulAre400Test(unittest.TestCase):
    """R22-6 (round 22): a path value holding a NUL character reached the file system — ValueError("embedded null
    byte") — and both servers answered HTTP 500: report rows / scan_root / heatmap_path, analyze-file, heatmap,
    preview, scan, and api-serve's check, compare, classify, analyze/*, the streams. A NUL can never be part of a
    file-system path: every such value is a request error (400). Control characters other than NUL are legal in
    POSIX names (R11-9 hostile names) and keep working."""

    setUp = ErrorTableEveryRowTest.setUp
    _web = ErrorTableEveryRowTest._web
    _api = ErrorTableEveryRowTest._api
    _multipart = ErrorTableEveryRowTest._multipart

    def _nul_requests(self) -> list[tuple[str, str, str, bytes | None, str]]:
        import base64
        from urllib.parse import quote

        f = self.folder
        nul = quote(str(f / "a\x00b.png"))
        b64 = base64.urlsafe_b64encode(os.fsencode(str(f / "a\x00b.png"))).decode()
        memo = webapp_api._scan_payload(f"folder={quote(str(f))}&no_default_engine=true", default_folder=f)
        row = next(item for item in memo["items"] if item["path"] == "memo.txt")

        def report(**changes: object) -> bytes:
            body: dict[str, Any] = {"items": [dict(row, **changes.pop("row", {}))], "scan_root": str(f), "format": "json"}
            body.update(changes)
            return json.dumps(body).encode("utf-8")

        both = ("web", "api")
        requests = [(s, "GET", path, None, "") for s in both for path in (
            f"/api/scan?folder={nul}", f"/api/scan?async=1&folder={nul}", f"/api/scan?folder_b64={b64}",
            f"/api/analyze-file?file={nul}", f"/api/analyze-file?file_b64={b64}",
            f"/api/heatmap?path={nul}", f"/api/heatmap?path=x.png&root={nul}", f"/api/heatmap?path_b64={b64}",
            f"/api/preview?path={nul}", f"/api/preview?path={quote(str(f / 'photo.png'))}&root={nul}", f"/api/preview?path_b64={b64}",
        )]
        requests += [(s, "POST", "/api/report", body, "application/json") for s in both for body in (
            report(scan_root=str(f) + "\x00"),
            report(row={"path": "memo\x00.txt", "name": "memo\x00.txt"}),
            report(row={"heatmap_path": str(f / "h\x00.png")}),
            report(row={"duplicate_of": "a\x00", "status": "duplicate"}),
            json.dumps({"items": [{"source": "upload", "path": "a\x00.png", "name": "a\x00.png", "status": "failed"}], "format": "json"}).encode(),
        )]
        requests += [("api", "POST", path.format(nul), None, "") for path in (
            "/api/analyze/image?file_path={}", "/api/analyze/audio?file_path={}", "/api/analyze/face?file_path={}",
            "/api/analyze/forensic?file_path={}", "/api/classify?file_path={}", "/api/check?file_path={}",
            "/api/check/stream?file_path={}", "/api/scan/stream?directory={}",
            "/api/compare?file_path_a={}&file_path_b=" + quote(str(f / "memo.txt")),
            "/api/compare?file_path_b={}&file_path_a=" + quote(str(f / "memo.txt")),
        )]
        return requests

    def test_every_nul_path_value_is_400(self) -> None:
        senders = {"web": self._web()}
        if HAVE_FASTAPI:
            senders["api"] = self._api()
        for server, method, path, body, ctype in self._nul_requests():
            send = senders.get(server)
            if send is None:
                continue
            with self.subTest(server=server, method=method, path=path[:90], body=(body or b"")[-60:]):
                status, raw = send(method, path, body, ctype, None)
                self.assertEqual(status, 400, raw[:300])
                text = raw.decode("utf-8", "replace")
                # a *_b64 value's NUL is already refused by the decoder (R12-4: PATH_B64_INVALID)
                self.assertTrue("NUL" in text or ("_b64=" in path and PATH_B64_INVALID in text), text[:300])

    def test_an_upload_name_with_nul_is_not_a_500(self) -> None:
        senders = {"web": self._web()}
        if HAVE_FASTAPI:
            senders["api"] = self._api()
        png = (self.folder / "photo.png").read_bytes()
        for server, send in senders.items():
            for name in ("a\x00.png", "b\x00.zip"):
                body, ctype = self._multipart(("files", name, png))
                with self.subTest(server=server, name=name):
                    status, raw = send("POST", "/api/analyze-upload", body, ctype, None)
                    self.assertIn(status, (200, 400), raw[:300])

    def test_control_characters_other_than_nul_still_work(self) -> None:
        """R11-9 names (\\r, \\n, \\t, ESC) are file names a scan writes: analyze-file and preview still read them."""
        from urllib.parse import quote

        name = self.folder / "c\r\n\t\x1bd.png"
        _write_png(name, seed=6)
        senders = {"web": self._web()}
        if HAVE_FASTAPI:
            senders["api"] = self._api()
        for server, send in senders.items():
            with self.subTest(server=server):
                self.assertEqual(send("GET", f"/api/analyze-file?file={quote(str(name))}", None, "", None)[0], 200)
                self.assertEqual(send("GET", f"/api/preview?path={quote(str(name))}", None, "", None)[0], 200)


class ReportRowAliasesByFileIdentityTest(_ReportLegsFixture):
    """R22-4 (round 22): R21-5 refused aliases by the path *text* only. "sub/loop/gen.png" through a bind-mount loop
    folder (sub/loop = the scan folder again) named gen.png a second time and was signed as one more row (total 4,
    manipulation_evidence 2, verify-report rc 0), and on a case-insensitive file system "GEN.png" does the same.
    A path through a folder with the identity (st_dev, st_ino) of the scan folder or a folder between it and the
    entry — which the walk never enters — and a path whose components are not the listed names are now 400."""

    FORMATS = ReportRefusesChangedRowsTest.FORMATS

    def setUp(self) -> None:
        super().setUp()
        (self.folder / "sub" / "loop").mkdir(parents=True)
        (self.folder / "sub" / "loop" / "target.png").write_bytes((self.folder / "target.png").read_bytes())

    def _post_all(self, rows: list[dict[str, Any]]) -> list[tuple[str, str, int, dict[str, Any]]]:
        out = []
        for leg, make in self._legs():
            post = make()
            for fmt in self.FORMATS:
                status, _, raw = post("/api/report", self._json({"items": rows, "format": fmt}))
                out.append((leg, fmt, status, json.loads(raw) if "json" in fmt or status != 200 else {}))
        return out

    def test_a_path_under_a_bind_mount_loop_is_400(self) -> None:
        from deepfake_lens import scan_cache

        real = scan_cache._dir_identity
        loop = self.folder / "sub" / "loop"

        def identity(path: Any) -> Any:
            return real(self.folder) if Path(path) == loop else real(path)

        target = self.rows["target.png"]
        with patch.object(scan_cache, "_dir_identity", identity):
            for leg, fmt, status, body in self._post_all([target, dict(target, path="sub/loop/target.png", name="target.png")]):
                with self.subTest(leg=leg, format=fmt):
                    self.assertEqual(status, 400, body)
                    self.assertIn("「sub/loop/target.png」는 순환 폴더", body["error"])
        # without the loop the same path is an ordinary subfolder row of a recursive scan: signed
        for leg, fmt, status, body in self._post_all([target, dict(target, path="sub/loop/target.png", name="target.png")]):
            if fmt == "json":
                with self.subTest(leg=leg, case="no loop"):
                    self.assertEqual(status, 200, body)

    def test_a_path_spelled_unlike_the_listing_is_400(self) -> None:
        import unicodedata

        real_lstat = os.lstat
        (self.folder / unicodedata.normalize("NFC", "한.png")).write_bytes((self.folder / "target.png").read_bytes())
        aliases = {"TARGET.png": "target.png", unicodedata.normalize("NFD", "한.png"): unicodedata.normalize("NFC", "한.png")}

        def lstat(path: Any, *args: Any, **kwargs: Any) -> Any:
            # a case-insensitive, normalizing file system (macOS, Windows): another spelling names the same file
            text = os.fspath(path)
            for alias, name in aliases.items():
                text = text.replace(alias, name) if isinstance(text, str) else text
            return real_lstat(text, *args, **kwargs)

        target = self.rows["target.png"]
        for alias in aliases:
            with patch.object(webapp_api.os, "lstat", lstat):
                results = self._post_all([target, dict(target, path=alias, name=alias)])
            for leg, fmt, status, body in results:
                with self.subTest(leg=leg, format=fmt, alias=alias):
                    self.assertEqual(status, 400, body)
                    self.assertIn("의 경로가 폴더 목록의 이름과 다릅니다", body["error"])

    def test_a_gone_file_is_still_409_and_hard_links_are_rows(self) -> None:
        """R19-13 stays: a row whose file is gone is 409 (not 400). Hard links are distinct listed names — the scan
        writes a row for each — and are signed."""
        target = self.rows["target.png"]
        os.link(self.folder / "target.png", self.folder / "hard.png")
        rows = [target, dict(target, path="hard.png", name="hard.png")]
        for leg, fmt, status, body in self._post_all(rows):
            if fmt == "json":
                with self.subTest(leg=leg, case="hard link"):
                    self.assertEqual(status, 200, body)
                    self.assertEqual(sorted(row["path"] for row in body["items"]), ["hard.png", "target.png"])
        for leg, fmt, status, body in self._post_all([target, dict(target, path="gone.png", name="gone.png")]):
            with self.subTest(leg=leg, format=fmt, case="gone"):
                self.assertEqual(status, 409, body)


class ReportUploadRowNamesTest(_ReportLegsFixture):
    """R22-8 (round 22): an upload row's path went into the signed body's excluded_items with no check — any
    sentence, "../", NUL — and the R21-1 invariant left upload rows out without HANDOFF saying so. An upload name
    is now limited (relative, at most 255 characters, no empty / "." / ".." component, no NUL) and marked: one this
    server produced (path and sha256) is name_source "server_upload", any other "client" with a reason that says
    the name is the client's."""

    UPLOAD = {"source": "upload", "status": "failed", "error": "x"}

    def test_a_name_the_server_never_received_is_marked_as_the_clients(self) -> None:
        sentence = "이 보고서의 모든 파일은 진본임을 서버가 확인했습니다.png"
        for leg, make in self._legs():
            with self.subTest(leg=leg):
                status, _, raw = make()("/api/report", json.dumps({"items": [dict(self.UPLOAD, path=sentence, name=sentence)], "format": "json"}).encode())
                self.assertEqual(status, 200, raw[:300])
                [row] = json.loads(raw)["excluded_items"]
                self.assertEqual((row["name_source"], row["reason"]), ("client", webapp_api.REPORT_ROW_UPLOAD_CLIENT_NAME))
                status, _, raw = make()("/api/report?format=html", json.dumps({"items": [dict(self.UPLOAD, path=sentence, name=sentence)]}).encode())
                self.assertIn("클라이언트 제공 이름", raw.decode("utf-8"))

    def test_an_upload_name_outside_the_form_is_400(self) -> None:
        for leg, make in self._legs():
            for name in ("../../etc/passwd", "/abs/a.png", "a/./b.png", "a//b.png", "x" * 256, "a\x00.png"):
                with self.subTest(leg=leg, name=name[:30]):
                    status, _, raw = make()("/api/report", json.dumps({"items": [dict(self.UPLOAD, path=name, name=name)], "format": "json"}).encode())
                    self.assertEqual(status, 400, raw[:300])

    def test_an_uploaded_name_is_the_servers(self) -> None:
        boundary = "----r22-8"
        data = (self.folder / "target.png").read_bytes()
        multipart = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"dir/a.png\"\r\n"
                     "Content-Type: image/png\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        upload = webapp_api._analyze_upload_payload(f"multipart/form-data; boundary={boundary}", multipart)
        [row] = upload["items"]  # type: ignore[index]
        for leg, make in self._legs():
            with self.subTest(leg=leg):
                status, _, raw = make()("/api/report", json.dumps({"items": [row], "format": "json"}).encode())
                [entry] = json.loads(raw)["excluded_items"]
                self.assertEqual((entry["path"], entry["name_source"]), ("dir/a.png", "server_upload"))
                # the same name with another digest is only the client's
                status, _, raw = make()("/api/report", json.dumps({"items": [dict(row, sha256="0" * 64)], "format": "json"}).encode())
                self.assertEqual(json.loads(raw)["excluded_items"][0]["name_source"], "client")
