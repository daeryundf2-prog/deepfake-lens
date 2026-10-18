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

    # P0R-6: the request is a server-held scan job; rows, thresholds and coverage are no longer fields.
    CASES: tuple[tuple[object, str], ...] = (
        ({}, "보고서에 넣을 검사 작업(scan_job)이 필요합니다 — 폴더를 먼저 검사하고 그 응답의 scan_job 값을 보내십시오(업로드 결과만 넣으려면 uploads)"),
        ({"scan_job": 3}, "scan_job 값은 문자열이어야 합니다"),
        ({"uploads": ["x"]}, "uploads는 업로드 검사 결과 행(객체)의 배열이어야 합니다"),
        ({"scan_job": "0" * 32, "exhibit_paths": "a.png"}, "exhibit_paths는 검사 작업의 행 경로(문자열)의 배열이어야 합니다"),
        ({"uploads": [{"source": "upload", "path": "../a.png", "status": "failed"}]}, "업로드 항목 1번의 이름이 업로드 파일 이름 형식이 아닙니다(상대 경로, 255자 이하, 빈·`.`·`..` 구성 요소 불가)"),
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

    def test_an_unknown_field_is_named_in_korean(self) -> None:
        message = self._check({"items": [{"path": "a.png"}]})
        self.assertTrue(message.startswith("보고서 요청에 알 수 없는 필드가 있습니다: items — "), message)

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

    def test_true_false_options_take_known_values_only(self) -> None:
        """R23-13 (round 23): `recursive=maybe` was read as false (and a report signed "아니오"); every true/false option
        now takes true/false, 1/0, yes/no, on/off (any case, surrounding spaces) or empty — anything else is InvalidOption."""
        from deepfake_lens.analysis_api import AnalysisOptions, InvalidOption, parse_flag

        for value, expected in (("1", True), ("TRUE", True), (" yes ", True), ("On", True),
                                ("0", False), ("false", False), ("No", False), ("off", False), ("", False)):
            with self.subTest(value=value):
                self.assertIs(parse_flag(value, "recursive"), expected)
                self.assertIs(AnalysisOptions.from_query({"recursive": [value]}, models_dir=None).recursive, expected)
        for key in ("recursive", "dedupe", "heatmaps", "deep_signals", "no_default_engine"):
            for value in ("maybe", "2", "-1", "truee", "None"):
                with self.subTest(key=key, value=value), self.assertRaises(InvalidOption) as ctx:
                    AnalysisOptions.from_query({key: [value]}, models_dir=None)
                self.assertIn(f"{key}은(는) 참·거짓 값이어야 합니다", str(ctx.exception))
        self.assertFalse(AnalysisOptions.from_query({}, models_dir=None).recursive)  # absent: the default

    def test_a_model_name_the_file_system_cannot_look_up_is_invalid(self) -> None:
        """R23-9 (round 23): a 5000-character model_path / fusion_profile raised OSError(ENAMETOOLONG) — HTTP 500."""
        import tempfile

        from deepfake_lens.analysis_api import AnalysisOptions, InvalidOption

        with tempfile.TemporaryDirectory() as models:
            for key in ("model_path", "fusion_profile"):
                for name in ("m" * 5000, "m" * 256 + ".json"):
                    with self.subTest(key=key, length=len(name)), self.assertRaises(InvalidOption) as ctx:
                        AnalysisOptions.from_query({key: [name]}, models_dir=Path(models))
                    self.assertIn(f"{key}을(를) 모델 디렉터리에서 찾을 수 없습니다", str(ctx.exception))
                    self.assertLess(len(str(ctx.exception)), 400)  # the name is not echoed in full

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
        from deepfake_lens.scan_jobs import JobStore

        # P0R-6: the job registry is scan_jobs.STORE (was webapp_api._SCAN_JOBS)
        self.store = JobStore()
        registry = patch.object(webapp_api, "STORE", self.store)
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

        job1, cancel = self.store.start("local", "async")
        self.assertIsInstance(cancel, threading.Event)
        out = webapp_api._scan_cancel_payload(f"job={job1}")
        self.assertTrue(out["cancelled"])
        self.assertTrue(cancel.is_set())

        # A finished job reports that there is nothing left to cancel.
        job2, _ = self.store.start("local", "async")
        self.store.finish(job2, "done", {"items": []})
        out = webapp_api._scan_cancel_payload(f"job={job2}")
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

        from deepfake_lens.scan_jobs import MAX_RUNNING

        for _ in range(MAX_RUNNING):
            self.store.start("local", "async")
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

    def test_stream_recursive_takes_the_true_false_values_of_every_option(self) -> None:
        """R24-8 (round 24): ``recursive`` of /api/scan/stream was a pydantic bool — ``t``/``y``/``n`` were 200 and an unknown
        value 422, while every other true/false option (R23-13) is 400 outside true/false, 1/0, yes/no, on/off, empty."""
        (self.root / "deeper").mkdir(exist_ok=True)
        (self.root / "deeper" / "inner-note.txt").write_text("하위 폴더의 메모입니다.", encoding="utf-8")
        for value, recursive in (("TRUE", True), (" yes ", True), ("1", True), ("on", True), ("", False), ("off", False), ("No", False)):
            with self.subTest(value=value):
                res = self._post("/api/scan/stream", {"directory": str(self.root), "recursive": value})
                self.assertEqual(res.status_code, 200, res.text[:300])
                self.assertEqual("inner-note.txt" in res.text, recursive)
        for value in ("t", "f", "y", "n", "maybe", "2", "None"):
            with self.subTest(value=value):
                res = self._post("/api/scan/stream", {"directory": str(self.root), "recursive": value})
                self.assertEqual(res.status_code, 400, res.text[:300])
                self.assertIn("recursive은(는) 참·거짓 값이어야 합니다", res.json()["detail"])


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


# R26-1 (round 26): an archive whose member names look like the "::" chains of nested archive members. R25-6 made
# ":" an ordinary member name character on POSIX, so the literal member "inner.zip::b.png" and the member "b.png" of
# the nested "inner.zip" had one identity (container, member). Every member must be its own row (injective chain).
COLLIDING_MEMBER_NAMES = (
    "inner.zip::b.png", "inner.zip::c.zip::d.png", "a.zip::b.zip::c.png", "fake.zip::x.png", ":lead.png",
    "t:.png", "q\\:z.png", "\uD55C.png", "\u1112\u1161\u11AB.png",
)


def _write_colliding_names_zip(path: Path, scratch: Path, generator_png: bytes | None = None) -> Path:
    """R26-1: ``path`` — a zip whose literal member names collide with the chains of its nested members.

    ``inner.zip`` holds ``b.png`` (``generator_png`` when given: the member with manipulation evidence) and
    ``c.zip`` (``d.png``); ``a.zip`` holds the literal ``b.zip::c.png`` and ``b.zip`` (``c.png``); the top level
    holds every name of :data:`COLLIDING_MEMBER_NAMES` — the same chains written literally, a leading / trailing
    ":", a "\\" (stored as a folder separator), the NFC and the NFD "한".
    """
    import io
    import zipfile

    def png(seed: int) -> bytes:
        return _write_png(scratch / f"collide-{seed}.png", seed=seed).read_bytes()

    def zipped(entries: dict[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        return buffer.getvalue()

    inner = zipped({"b.png": generator_png or png(60), "c.zip": zipped({"d.png": png(61)})})
    outer_a = zipped({"b.zip::c.png": png(62), "b.zip": zipped({"c.png": png(63)})})
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("inner.zip", inner)
        archive.writestr("a.zip", outer_a)
        for seed, name in enumerate(COLLIDING_MEMBER_NAMES, start=70):
            archive.writestr(name, png(seed))
    return path


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
            ("POST", "/api/report", b"{}", "application/json", 400, "검사 작업(scan_job)이 필요합니다"),
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
        # P0R-6: jobs of this server (owner "local"), of another token, outside the roots, running and failed
        job = row["scan_job"]
        foreign_job = webapp_api.new_job_id()
        webapp_api.STORE.record_finished(foreign_job, "token:other", "sync", "done", dict(row))
        outside_job = webapp_api.new_job_id()
        webapp_api.STORE.record_finished(outside_job, "local", "sync", "done", {**row, webapp_api.CONTEXT_KEY: {"scan_root": str(self.outside)}})
        running_job, _cancel = webapp_api.STORE.start("local", "async")
        failed_job = webapp_api.new_job_id()
        webapp_api.STORE.record_finished(failed_job, "local", "async", "error", {"error": "x"})

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
        # R29-5: several files on the single-file quick check; R29-6: a third file on the comparison.
        two_files, two_type = self._multipart(("file", "photo.png", png), ("file", "memo.txt", b"memo"))
        triple, triple_type = self._multipart(("a", "a.txt", b"one"), ("b", "b.txt", b"two"), ("c", "c.txt", b"three"))
        files = [
            ("POST", "/api/analyze/image?file_path={}"), ("POST", "/api/analyze/audio?file_path={}"),
            ("POST", "/api/analyze/face?file_path={}"), ("POST", "/api/analyze/forensic?file_path={}"),
            ("POST", "/api/classify?file_path={}"), ("POST", "/api/check?file_path={}"),
            ("POST", "/api/check/stream?file_path={}"),
            ("POST", "/api/compare?file_path_a={}&file_path_b=" + q(f / "memo.txt")),
        ]
        jobs = {f"fake{i}": {"status": "running", "created": time.time(), "owner": "local", "source": "async", "cancel": __import__("threading").Event()} for i in range(40)}

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
            + [case(s, "GET", f"/api/scan?folder={q(f)}&pixel=bogus", 400, "pixel") for s in both]
            # R23-13 (round 23): an unknown true/false value was read as false (signed "아니오" in a report).
            + [case(s, "GET", f"/api/scan?folder={q(f)}&{key}=maybe", 400, f"{key}은(는) 참·거짓 값이어야 합니다")
               for s in both for key in ("recursive", "dedupe", "heatmaps", "deep_signals", "no_default_engine", "async")]
            # R24-8 (round 24): /api/scan/stream's recursive was a pydantic bool — "t" was 200 and "maybe" a 422.
            + [case("api", "POST", f"/api/scan/stream?directory={q(f)}&recursive={value}", 400, "recursive은(는) 참·거짓 값이어야 합니다")
               for value in ("maybe", "t", "y", "n", "2")]
            # R23-9 (round 23): a name longer than the file system allows was a 500 (ENAMETOOLONG from is_file()).
            + [case(s, "GET", f"/api/scan?folder={q(f)}&{key}={'m' * 5000}", 400, f"{key}을(를) 모델 디렉터리에서 찾을 수 없습니다")
               for s in both for key in ("model_path", "fusion_profile")],
            "E6": [case(s, "GET", "/api/scan?max_files=-3", 400, "max_files는 1 이상이어야 합니다") for s in both]
            + [case(s, "GET", "/api/scan?max_file_bytes=0", 400, "max_file_bytes는 1 이상이어야 합니다") for s in both]
            + [case("api", "POST", f"/api/scan/stream?directory={q(f)}&max_files=0", 400, "max_files는 1 이상이어야 합니다")],
            "E7": [case(s, "GET", f"/api/scan?folder={q(f / 'gone')}&no_default_engine=true", 400, "폴더를 찾을 수 없습니다: ") for s in both]
            + [case(s, "GET", f"/api/scan?folder={q(f / 'memo.txt')}&no_default_engine=true", 400, "폴더가 아니라 파일입니다") for s in both],
            "E8": [case(s, "GET", f"/api/scan?folder={q(self.outside)}", 403, "허용되지 않은 경로") for s in both]
            + [case(s, "GET", f"/api/scan?folder_b64={fs_b64encode(str(self.outside))}", 403, "허용되지 않은 경로") for s in both],  # R13-3
            "E9": [case(s, "GET", f"/api/scan?folder={q(f)}&async=1", 400, "실행 중인 검사 작업이 너무 많습니다",
                        patches=(patch.dict(webapp_api.STORE._jobs, jobs),)) for s in both],  # P0R-6: the job store
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
                (two_files, two_type, webapp_api.CHECK_ONE_FILE.format(count=2)),
            )],
            "E22": [case("web", "POST", "/api/check", 413, "요청 본문이 상한", upload, upload_type, patches=(patch.object(webapp_module, "MAX_UPLOAD_BYTES", 16),))],
            # (text > 256 KB cannot travel in a test client's query string; the
            # limit is the same constant check as the web server's E21.)
            "E23": [case("api", "POST", "/api/check", 400, "file_path 또는 text가 필요합니다"),
                    case("api", "POST", "/api/check?text=", 400, "file_path 또는 text가 필요합니다")],
            "E24": [case("web", "POST", "/api/compare", 400, text, body, ctype) for body, ctype, text in (
                (b"x", "text/plain", "multipart/form-data 본문이 필요합니다"),
                (upload, upload_type, webapp_api.COMPARE_TWO_FILES.format(count=1)),
                (triple, triple_type, webapp_api.COMPARE_TWO_FILES.format(count=3)),
                (pair, pair_type, "지원되는 쌍이 아닙니다(.txt, .wav)"),
                (cut_pair, pair_type, "업로드 본문이 잘렸습니다"),
            )],
            "E25": [case("web", "POST", "/api/compare", 500, "비교 분석 중 오류가 발생했습니다", pair, pair_type,
                         patches=(patch.object(webapp_module, "_compare_payload", raise_),))],
            "E26": [case("api", "POST", f"/api/compare?file_path_a={q(f / 'memo.txt')}&file_path_b={q(f / 'tone.wav')}", 400, "지원되는 쌍이 아닙니다(.txt, .wav)"),
                    case("api", "POST", f"/api/compare?file_path_a={q(f / 'memo.txt')}&file_path_b={q(f / 'memo.txt')}", 500, "주입된 실패",
                         patches=(patch("deepfake_lens.core.compare_files", raise_),))],
            # P0R-6: the request names a server-held scan job; rows, options and the scan folder are no longer fields.
            "E27": [case(s, "POST", "/api/report", 400, text, body, "application/json") for s in both for body, text in (
                (b"{oops", "JSON 본문을 해석할 수 없습니다"),
                # R11-3 (round 11): a too-deep body was a 500 (RecursionError).
                (deep, "JSON 중첩이 너무 깊습니다"),
                (b'{"uploads": [{"path": "a", "x": ' + b"[" * DEEP + b"]" * DEEP + b"}]}", "JSON 중첩이 너무 깊습니다"),
                (b"[1]", "보고서 요청 본문은 JSON 객체여야 합니다"),
                (report({"items": [memo_row], "scan_root": scan_root}), "보고서 요청에 알 수 없는 필드가 있습니다: items, scan_root"),
                (report({"scan_job": job, "options": {"recursive": "maybe"}}), "보고서 요청에 알 수 없는 필드가 있습니다: options"),
                (report({}), webapp_api.REPORT_SCAN_JOB_REQUIRED),
                (report({"scan_job": 3}), webapp_api.REPORT_SCAN_JOB_NOT_STRING),
                (report({"scan_job": job, "exhibit_paths": "memo.txt"}), webapp_api.REPORT_EXHIBIT_PATHS_INVALID),
                (report({"scan_job": job, "exhibit_paths": []}), webapp_api.REPORT_EXHIBIT_PATHS_EMPTY),
                (report({"scan_job": job, "exhibit_paths": ["memo.txt", "memo.txt"]}), "exhibit_paths에 같은 행이 두 번 있습니다"),
                (report({"scan_job": job, "exhibit_paths": ["./memo.txt"]}), "exhibit_paths 중 1개는 이 검사 작업의 행이 아닙니다"),
                (report({"scan_job": job, "side": "defendant"}), "제출 측(side) 「defendant」은(는) 이 서버가 지원하지 않습니다"),
                (report({"scan_job": job, "case_no": 3}), "사건 필드 case_no 값은 문자열이어야 합니다"),
                (report({"uploads": "x"}), webapp_api.REPORT_UPLOADS_INVALID),
                (report({"uploads": [{"source": "upload", "status": "failed", "path": "../x.png"}]}), "업로드 항목 1번의 이름이 업로드 파일 이름 형식이 아닙니다"),
            )],
            "E28": [case(s, "POST", "/api/report", 403, "허용되지 않은 경로", report({"uploads": [{"source": "upload", "path": "memo.txt", "status": "failed", "heatmap_path": str(self.outside / "secret.png")}]}), "application/json") for s in both]
            + [case(s, "POST", "/api/report", 403, "허용되지 않은 경로", report({"uploads": [dict(memo_row, source="upload", result=dict(memo_row["result"], pixel_analysis={"heatmap_path": str(self.outside / "secret.png")}))]}), "application/json") for s in both],
            "E29": [case(s, "POST", "/api/report", 404, webapp_api.REPORT_SCAN_JOB_UNKNOWN, report({"scan_job": value}), "application/json")
                    for s in both for value in ("0" * 32, "deadbeef", foreign_job)],
            "E30": [case(s, "POST", "/api/report", 403, "허용되지 않은 경로", report({"scan_job": outside_job}), "application/json") for s in both],
            "E31": [case(s, "POST", "/api/report?format=pdf", 501, "PDF 보고서를 만들려면 pymupdf 패키지가 필요합니다", report({"scan_job": job}), "application/json",
                         patches=(patch("deepfake_lens.pdf_backend.import_pymupdf", no_pymupdf),)) for s in both]
            # R22-7 (round 22): the evidence statement without pymupdf is the same 501, before the job is read
            + [case(s, "POST", f"/api/report?format={fmt}", 501, "PDF 보고서를 만들려면 pymupdf 패키지가 필요합니다", report({"scan_job": job}), "application/json",
                    patches=(patch("deepfake_lens.pdf_backend.import_pymupdf", no_pymupdf), patch.object(webapp_api, "_changed_job_rows", raise_)))
               for s in both for fmt in ("evidence", "evidence-statement")]
            + [case(s, "POST", "/api/report?format=evidence", 500, "증거설명서 PDF 생성 실패", report({"scan_job": job}), "application/json",
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
            # R23-7 (round 23): an examiner record that cannot be written (here: its folder would be under a regular file) —
            # a review answered 200 "success" with nothing on disk, feedback was a 500.
            "E50": [case(s, "POST", "/api/feedback", 409, "저장하지 못했습니다", json.dumps({"path": "memo.txt", "expected_label": "real"}).encode(),
                         "application/json", patches=(patch.dict(os.environ, {"DEEPFAKE_LENS_FEEDBACK": str(self.outside / "secret.png" / "d" / "fb.jsonl")}),))
                    for s in both]
            + [case(s, "POST", "/api/review", 409, "저장하지 못했습니다", json.dumps({"artifact_id": "memo.txt", "star": True}).encode(),
                    "application/json", patches=(patch.dict(os.environ, {"DEEPFAKE_LENS_REVIEWS": str(self.outside / "secret.png" / "d" / "r.json")}),
                                                 patch("deepfake_lens.reviews._DEFAULT_STORE", None))) for s in both]
            + [case("api", "PUT", "/api/artifacts/memo.txt/review", 409, "저장하지 못했습니다", json.dumps({"star": True}).encode(),
                    "application/json", patches=(patch.dict(os.environ, {"DEEPFAKE_LENS_REVIEWS": str(self.outside / "secret.png" / "d" / "r.json")}),
                                                 patch("deepfake_lens.reviews._DEFAULT_STORE", None)))],
            # R22-6 (round 22): a NUL in a path value was a 500 (PathValuesWithNulAre400Test sends every endpoint).
            "E48": [case(s, "GET", f"/api/analyze-file?file={q(f / 'a')}%00", 400, "NUL 문자") for s in both]
            + [case(s, "POST", "/api/report", 400, "NUL 문자", report({"uploads": [{"source": "upload", "status": "failed", "path": "a\x00.png"}]}), "application/json") for s in both]
            + [case("api", "POST", f"/api/classify?file_path={q(f / 'a')}%00", 400, "NUL 문자")],
            # R19-13 (round 19): a posted row the server's re-analysis contradicts (here its digest) — every format.
            # R19-13 / P0R-6: a file the job hashed changed after the scan — every format; a running or failed job.
            "E47": [case(s, "POST", f"/api/report?format={fmt}", 409, webapp_api.REPORT_ROWS_CHANGED, report({"scan_job": job}), "application/json",
                         patches=(patch.object(webapp_api._JobFiles, "change", lambda *_: webapp_api.REPORT_ROW_CHANGED_CONTENT),))
                    for s in both for fmt in ("json", "html", *(("evidence",) if HAVE_PYMUPDF else ()))]  # R22-7
            + [case(s, "POST", "/api/report", 409, webapp_api.REPORT_SCAN_JOB_NOT_FINISHED, report({"scan_job": running_job}), "application/json") for s in both]
            + [case(s, "POST", "/api/report", 409, webapp_api.REPORT_SCAN_JOB_FAILED, report({"scan_job": failed_job}), "application/json") for s in both],
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
            # P0R-6: the only path-like values a report request still carries are those of upload rows
            body: dict[str, Any] = {"uploads": [dict(row, source="upload", **changes.pop("row", {}))], "format": "json"}
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
            report(row={"path": "memo\x00.txt", "name": "memo\x00.txt"}),
            report(row={"heatmap_path": str(f / "h\x00.png")}),
            report(row={"duplicate_of": "a\x00", "status": "duplicate"}),
            json.dumps({"uploads": [{"source": "upload", "path": "a\x00.png", "name": "a\x00.png", "status": "failed"}], "format": "json"}).encode(),
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


# --- POST /api/report: a server-held scan job (P0R-6, user decision 2026-10-17) -------------------------------------
#
# The report used to take the rows the client posted and reconcile them with a server re-analysis; the tests of that
# design (posted rows, aliases, posted duplicates, posted folder reasons, posted cancel counts …) went with it
# (docs/TEST-DELETIONS.md, "P0R-6"). The intents they protected are kept below against the new request — a scan job
# id, case fields, format, side and an exhibit selection: the signed rows are the job's (R25-1, R26-1), the
# accounting is the job's (R18-3, R22-1, R23-3, R23-5, R24-1), a file changed after the scan refuses the report
# (R19-13, R20-5), special entries are signed as scanned (R20-1), and no client text reaches a signed artifact
# (R21-1) except the case fields and upload names (R22-8).


def _artifact_text(raw: bytes, fmt: str) -> str:
    """The visible text of a report artifact (JSON as text, HTML as is, a PDF's page text)."""
    if fmt in ("pdf", "evidence", "evidence-statement"):
        import pymupdf

        with pymupdf.open(stream=raw, filetype="pdf") as doc:
            return "\n".join(page.get_text() for page in doc)
    return raw.decode("utf-8", "replace")


class _ReportLegsFixture(unittest.TestCase):
    """A case folder, its scan as a server-held job, and the two server legs (stdlib web, api-serve)."""

    HEADERS = {"X-Deepfake-Lens-Client": "gui", "Content-Type": "application/json"}
    FORMATS = ("json", "html", *(("pdf", "evidence") if HAVE_PYMUPDF else ()))

    def setUp(self) -> None:
        import hashlib
        import tempfile
        import zipfile

        from deepfake_lens.scan_jobs import JobStore

        roots = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        self.store = JobStore()
        store = patch.object(webapp_api, "STORE", self.store)
        store.start()
        self.addCleanup(store.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        jobs = patch.dict(os.environ, {"DEEPFAKE_LENS_SCAN_JOBS": str(self.base / "jobs")})
        jobs.start()
        self.addCleanup(jobs.stop)
        self.folder = self.base / "case"
        self.folder.mkdir()
        _write_png(self.folder / "target.png", seed=1)
        os.symlink("target.png", self.folder / "in_link.png")
        with zipfile.ZipFile(self.folder / "bundle.zip", "w") as archive:
            archive.writestr("inner/member.png", _write_png(self.base / "m.png", seed=2).read_bytes())
            archive.writestr("notes.txt", "메모입니다.")
        self.extra_setup()
        self.job, self.scan = self._scan_job()
        self.rows = {row["path"]: row for row in self.scan["items"]}
        self.target_sha = hashlib.sha256((self.folder / "target.png").read_bytes()).hexdigest()
        self.member_sha = hashlib.sha256((self.base / "m.png").read_bytes()).hexdigest()

    def extra_setup(self) -> None:
        """Files a subclass adds before the fixture's scan."""

    def _scan_job(self, query: str = "", *, should_stop: Any = None, owner: str = "local", folder: Path | None = None) -> tuple[str, dict[str, Any]]:
        from urllib.parse import quote

        payload = webapp_api._scan_payload(
            f"folder={quote(str(folder or self.folder))}&no_default_engine=true{query}", default_folder=self.folder,
            should_stop=should_stop, owner=owner,
        )
        self.assertNotIn("error", payload)
        self.assertNotIn("scan_job_note", payload)
        return str(payload["scan_job"]), payload

    # -- legs -----------------------------------------------------------------------------------------------------

    def _stdlib_leg(self, token: str | None = None):
        import threading
        import urllib.error
        import urllib.request

        from deepfake_lens.webapp import build_server

        server = build_server("127.0.0.1", 0, default_folder=self.folder, token=token)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        headers = dict(self.HEADERS, **({"X-Deepfake-Lens-Token": token} if token else {}))

        def post(path: str, body: bytes) -> tuple[int, str, bytes]:
            request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    return response.status, response.headers.get("Content-Type", ""), response.read()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.headers.get("Content-Type", ""), exc.read()

        return post

    def _fastapi_leg(self, token: str | None = None):
        from fastapi.testclient import TestClient

        client = TestClient(api_server.create_app(default_folder=self.folder, token=token))
        headers = {"host": "localhost", **self.HEADERS, **({"X-API-Token": token} if token else {})}

        def post(path: str, body: bytes) -> tuple[int, str, bytes]:
            response = client.post(path, content=body, headers=headers)
            return response.status_code, response.headers.get("content-type", ""), response.content

        return post

    def _legs(self):
        legs = [("web", self._stdlib_leg)]
        if HAVE_FASTAPI:
            legs.append(("api", self._fastapi_leg))
        return legs

    def _body(self, **extra: object) -> bytes:
        return self._json({"scan_job": self.job, **extra})

    @staticmethod
    def _json(payload: dict[str, Any]) -> bytes:
        return json.dumps(payload).encode("utf-8")

    def _signed(self, body: bytes | None = None) -> list[dict[str, Any]]:
        """The signed JSON body from every leg (asserting 200)."""
        out = []
        for leg, make in self._legs():
            status, _, raw = make()("/api/report?format=json", body or self._body())
            self.assertEqual(status, 200, (leg, raw[:400]))
            out.append(json.loads(raw))
        return out

    def _assert_refused(self, path: str, reason: str, body: bytes | None = None) -> None:
        for leg, make in self._legs():
            post = make()
            for fmt in self.FORMATS:
                with self.subTest(leg=leg, format=fmt):
                    status, _, raw = post(f"/api/report?format={fmt}", body or self._body())
                    self.assertEqual(status, webapp_api.REPORT_ROWS_CHANGED_STATUS, raw[:400])
                    payload = json.loads(raw)
                    self.assertEqual(payload["error"], webapp_api.REPORT_ROWS_CHANGED)
                    self.assertIn({"path": path, "reason": reason}, payload["changed_rows"])
                    # review §2.4: no server digest, status or verdict is echoed
                    for entry in payload["changed_rows"]:
                        self.assertEqual(set(entry), {"path", "reason"})
                    self.assertNotIn(self.target_sha, raw.decode("utf-8"))


def _row_view(row: dict[str, Any]) -> tuple[Any, ...]:
    result = row.get("result") or {}
    return (row.get("path"), row.get("container"), row.get("member"), row.get("status"), row.get("error"), row.get("sha256"),
            result.get("verdict_code"), json.dumps(result.get("evidence"), sort_keys=True), json.dumps(result.get("coverage"), sort_keys=True))


class ReportEvidenceHashesBothServersTest(_ReportLegsFixture):
    """N2/N9/N10/N11 on both servers: a link row stays unhashed, archive members carry their digests, the format comes
    from ?format= or the body, malformed requests are Korean 400s (P0R-6: the request is a scan job, not rows)."""

    def test_signed_body_keeps_symlink_unhashed_and_hashes_members(self) -> None:
        for body in self._signed():
            signed = {row["path"]: row for row in body["items"]}
            self.assertIsNone(signed["in_link.png"]["sha256"])  # N2: not target.png's digest
            self.assertEqual(signed["target.png"]["sha256"], self.target_sha)
            self.assertEqual(signed["bundle.zip::inner/member.png"]["sha256"], self.member_sha)  # N10
            self.assertEqual(signed["bundle.zip"]["sha256"], self.rows["bundle.zip"]["sha256"])

    @unittest.skipUnless(HAVE_PYMUPDF, "pymupdf required for PDF text")
    def test_both_pdfs_show_symlink_unhashed_and_member_digests(self) -> None:
        from deepfake_lens.result_text import HASH_UNAVAILABLE_SYMLINK

        for name, make in self._legs():
            post = make()
            for fmt in ("pdf", "evidence"):
                with self.subTest(leg=name, format=fmt):
                    status, content_type, raw = post("/api/report", self._body(format=fmt))  # N9: format in the body
                    self.assertEqual(status, 200, raw[:300])
                    self.assertEqual(content_type, "application/pdf")
                    flat = "".join(_artifact_text(raw, fmt).split())
                    self.assertEqual(flat.count(self.target_sha), 1)
                    self.assertIn("".join(HASH_UNAVAILABLE_SYMLINK.split())[:20], flat)
                    self.assertIn(self.member_sha, flat)
                    self.assertNotIn("해시불가(압축파일구성원", flat)

    def test_format_from_body_sets_content_type(self) -> None:
        for name, make in self._legs():
            post = make()
            with self.subTest(leg=name):
                status, content_type, _ = post("/api/report", self._body())
                self.assertEqual((status, "text/html" in content_type), (200, True))
                status, content_type, raw = post("/api/report", self._body(format="json"))
                self.assertEqual(status, 200)
                self.assertIn("application/json", content_type)
                self.assertIn("items", json.loads(raw))
                status, content_type, _ = post("/api/report?format=html", self._body(format="json"))  # ?format= wins
                self.assertIn("text/html", content_type)

    def test_malformed_requests_are_400_in_korean(self) -> None:
        from deepfake_lens.error_text import english_prose

        bad: list[tuple[bytes, str]] = [
            (b"{not json", "JSON 본문을 해석할 수 없습니다"),
            (b"[1, 2]", "보고서 요청 본문은 JSON 객체여야 합니다"),
            (b"{}", webapp_api.REPORT_SCAN_JOB_REQUIRED),
            (self._json({"items": list(self.rows.values())}), "보고서 요청에 알 수 없는 필드가 있습니다: items"),
            (self._body(format="docx"), "지원되지 않는 보고서 형식입니다: 「docx」"),
            (self._body(format=3), "`format` 값은 문자열이어야 합니다"),
            (self._body(thresholds={}), "보고서 요청에 알 수 없는 필드가 있습니다: thresholds"),
            (self._json({"scan_job": ["x"]}), webapp_api.REPORT_SCAN_JOB_NOT_STRING),
        ]
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


class ReportSignsTheJobsRowsTest(_ReportLegsFixture):
    """X2/R21-1/R25-1 by construction: the request carries no rows, so the signed rows are the job's rows, as scanned."""

    def test_the_signed_rows_are_the_jobs_rows_in_scan_order(self) -> None:
        expected = [_row_view(row) for row in self.scan["items"]]
        for body in self._signed():
            self.assertEqual([_row_view(row) for row in body["items"]], expected)
            self.assertEqual(body["excluded_items"], [])
            self.assertEqual(body["unrecorded_files"]["listing"], {"source": "scan_job", "not_in_report": 0})

    def test_rows_options_summaries_and_folders_are_not_request_fields(self) -> None:
        forged = dict(self.rows["target.png"], result=dict(self.rows["target.png"]["result"], verdict_code="manipulation_evidence"))
        for field, value in (("items", [forged]), ("options", {"pixel": "deep"}), ("scan_root", str(self.folder)),
                             ("summary", {"cancelled": True, "files_cancelled": 10**18}), ("coverage", {}), ("thresholds", {})):
            for leg, make in self._legs():
                with self.subTest(field=field, leg=leg):
                    status, _, raw = make()("/api/report?format=json", self._body(**{field: value}))
                    self.assertEqual(status, 400, raw[:300])
                    self.assertIn(f"알 수 없는 필드가 있습니다: {field}", json.loads(raw)["error"])

    def test_a_report_runs_no_analysis(self) -> None:
        """P0R-6 (and R20-2's cost): a report signs the job's rows — it analyzes no file (it re-hashes only)."""
        def refuse(*_: Any, **__: Any) -> Any:
            raise AssertionError("a report must not analyze a file")

        with patch("deepfake_lens.analysis_api.scan_file_run", refuse), patch("deepfake_lens.core.analyze_file", refuse):
            for leg, make in self._legs():
                for fmt in self.FORMATS:
                    with self.subTest(leg=leg, format=fmt):
                        self.assertEqual(make()(f"/api/report?format={fmt}", self._body())[0], 200)

    def test_the_scans_own_duplicate_is_signed_as_a_duplicate(self) -> None:
        """J-2/R20-4: a dedupe duplicate is the scan's duplicate row (no analysis), never a re-analysis of the copy."""
        import shutil

        shutil.copyfile(self.folder / "target.png", self.folder / "copy.png")
        job, scan = self._scan_job("&dedupe=true")
        rows = {row["path"]: row for row in scan["items"]}
        duplicate = next(row for row in rows.values() if row["status"] == "duplicate")
        for body in self._signed(self._json({"scan_job": job})):
            signed = {row["path"]: row for row in body["items"]}
            self.assertEqual(_row_view(signed[duplicate["path"]]), _row_view(duplicate))
        (self.folder / duplicate["path"]).write_bytes(b"changed")
        self._assert_refused(duplicate["path"], webapp_api.REPORT_ROW_CHANGED_CONTENT, self._json({"scan_job": job}))

    def test_exhibit_paths_choose_and_order_the_statements_exhibits_only(self) -> None:
        from deepfake_lens.core import _scan_item_from_json
        from deepfake_lens.evidence_statement import NOT_AN_EXHIBIT, build_evidence_statement

        order = ["bundle.zip::notes.txt", "target.png"]
        for body in self._signed(self._body(exhibit_paths=order)):
            self.assertEqual(body["exhibit_paths"], order)
            self.assertEqual(len(body["items"]), len(self.scan["items"]))  # every row is still signed
        items = [_scan_item_from_json(row) for row in self.scan["items"]]
        statement = build_evidence_statement(items, exhibit_paths=order)
        self.assertEqual([entry.file_path for entry in statement.entries][:2], order)
        self.assertEqual([entry.exhibit_no for entry in statement.entries][:2], ["갑 제1호증", "갑 제2호증"])
        self.assertEqual({entry.exhibit_no for entry in statement.entries[2:]}, {NOT_AN_EXHIBIT})
        self.assertEqual(len(statement.entries), len(items))
        if HAVE_PYMUPDF:
            for leg, make in self._legs():
                with self.subTest(leg=leg):
                    status, _, raw = make()("/api/report?format=evidence", self._body(exhibit_paths=order))
                    self.assertEqual(status, 200, raw[:300])
                    self.assertIn("서증외", "".join(_artifact_text(raw, "evidence").split()))
        # an alias of a row path is not a row of the job (R21-5/R22-4/R26-1 aliases): 400
        for alias in ("./target.png", "TARGET.png", "bundle.zip::inner\\/member.png", str(self.folder / "target.png")):
            for leg, make in self._legs():
                with self.subTest(alias=alias, leg=leg):
                    status, _, raw = make()("/api/report?format=json", self._body(exhibit_paths=[alias]))
                    self.assertEqual(status, 400, raw[:300])


class ReportRefusesChangedRowsTest(_ReportLegsFixture):
    """R19-13 (round 19) on the job: every file the scan hashed is re-hashed on disk; any change refuses every format
    with 409 and ``changed_rows`` [{path, reason}] — never the server's digests (review §2.4)."""

    def test_unchanged_rows_are_signed(self) -> None:
        for leg, make in self._legs():
            post = make()
            for fmt in self.FORMATS:
                with self.subTest(leg=leg, format=fmt):
                    self.assertEqual(post(f"/api/report?format={fmt}", self._body())[0], 200)

    def test_a_file_replaced_after_the_scan_is_409_in_every_format(self) -> None:
        _write_png(self.folder / "target.png", seed=9)
        self._assert_refused("target.png", webapp_api.REPORT_ROW_CHANGED_CONTENT)

    def test_an_archive_replaced_after_the_scan_is_409(self) -> None:
        import zipfile

        with zipfile.ZipFile(self.folder / "bundle.zip", "w") as archive:
            archive.writestr("inner/member.png", b"other bytes")
            archive.writestr("notes.txt", "메모입니다.")
        self._assert_refused("bundle.zip", webapp_api.REPORT_ROW_CHANGED_CONTENT)

    def test_a_member_of_an_unhashed_container_is_rehashed_from_a_fresh_extraction(self) -> None:
        import zipfile

        from deepfake_lens.core import _scan_item_from_json

        items = [_scan_item_from_json(row) for row in self.scan["items"] if row["path"] != "bundle.zip"]
        files = webapp_api._JobFiles(self.folder)
        self.addCleanup(files.close)
        self.assertEqual(webapp_api._changed_job_rows(items, files), [])
        with zipfile.ZipFile(self.folder / "bundle.zip", "w") as archive:
            archive.writestr("inner/member.png", b"other bytes")
            archive.writestr("notes.txt", "메모입니다.")
        files = webapp_api._JobFiles(self.folder)
        self.addCleanup(files.close)
        self.assertIn({"path": "bundle.zip::inner/member.png", "reason": webapp_api.REPORT_ROW_CHANGED_MEMBER},
                      webapp_api._changed_job_rows(items, files))

    def test_a_file_deleted_after_the_scan_is_409(self) -> None:
        (self.folder / "target.png").unlink()
        self._assert_refused("target.png", webapp_api.REPORT_ROW_CHANGED_GONE)

    def test_a_file_replaced_by_a_link_is_409(self) -> None:
        (self.folder / "target.png").rename(self.folder / "moved.png")
        os.symlink("moved.png", self.folder / "target.png")
        self._assert_refused("target.png", webapp_api.REPORT_ROW_CHANGED_LINK)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO")
    def test_a_file_replaced_by_a_fifo_is_409(self) -> None:
        (self.folder / "target.png").unlink()
        os.mkfifo(self.folder / "target.png")
        self._assert_refused("target.png", webapp_api.REPORT_ROW_CHANGED_GONE)


class ReportRefusesAFolderLinkedAfterTheScanTest(_ReportLegsFixture):
    """R20-5 (round 20): a hashed row whose folder is replaced by a link after the scan is a changed row (409)."""

    def extra_setup(self) -> None:
        (self.folder / "sub").mkdir()
        _write_png(self.folder / "sub" / "deep.png", seed=7)

    def test_a_hashed_row_behind_a_folder_linked_after_the_scan_is_409(self) -> None:
        job, _ = self._scan_job("&recursive=true")
        (self.folder / "sub").rename(self.base / "elsewhere")
        os.symlink(self.base / "elsewhere", self.folder / "sub")
        self._assert_refused("sub/deep.png", webapp_api.REPORT_ROW_CHANGED_LINK, self._json({"scan_job": job}))


class ReportSignsSpecialEntriesAsScannedTest(_ReportLegsFixture):
    """R20-1 (round 20): a FIFO, a socket or an unreadable subfolder — listed by the scan as skipped / failed — is signed
    as the scan listed it (it was "not found" and every report of such a folder was 409)."""

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO")
    def test_a_fifo_is_signed_as_the_scan_listed_it(self) -> None:
        os.mkfifo(self.folder / "pipe.jpg")
        job, scan = self._scan_job()
        row = next(item for item in scan["items"] if item["path"] == "pipe.jpg")
        self.assertEqual(row["status"], "skipped")
        for body in self._signed(self._json({"scan_job": job})):
            signed = {item["path"]: item for item in body["items"]}
            self.assertEqual(_row_view(signed["pipe.jpg"]), _row_view(row))

    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() != 0, "root ignores folder modes; POSIX modes")
    def test_an_unreadable_subfolder_is_signed_as_the_scans_failed_row(self) -> None:
        locked = self.folder / "locked"
        locked.mkdir()
        _write_png(locked / "in.png", seed=3)
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        job, scan = self._scan_job("&recursive=true")
        self.assertEqual(next(row for row in scan["items"] if row["path"] == "locked")["status"], "failed")
        for body in self._signed(self._json({"scan_job": job})):
            self.assertEqual({row["path"]: row["status"] for row in body["items"]}["locked"], "failed")


class ReportSignsNoPostedTextInvariantTest(_ReportLegsFixture):
    """R21-1 invariant (kept, P0R-6): no text a client sends reaches a signed artifact, except the case fields and
    upload names (HANDOFF §6). With the job as the only row source this is nearly structural: a marker in any field
    the request does not accept is a 400 and reaches no artifact; a marker in a case field is printed only there; a
    marker in an upload row is printed only as that row's unsigned name."""

    MARK = "클라이언트문구표식"

    def test_no_posted_text_reaches_a_signed_artifact(self) -> None:
        rows = list(self.rows.values())
        hostile = [
            {"items": [dict(row, error=self.MARK, result=dict(row.get("result") or {}, verdict=self.MARK)) for row in rows]},
            {"summary": {"note": self.MARK}}, {"options": {"model_path": self.MARK}}, {"scan_root": self.MARK},
            {self.MARK: 1}, {"thresholds": {"version": self.MARK}},
        ]
        for extra in hostile:
            for leg, make in self._legs():
                for fmt in self.FORMATS:
                    with self.subTest(field=next(iter(extra)), leg=leg, format=fmt):
                        status, _, raw = make()(f"/api/report?format={fmt}", self._body(**extra))
                        self.assertEqual(status, 400, raw[:200])
                        self.assertEqual(set(json.loads(raw)), {"error"})  # a refusal, no artifact
        upload = {"source": "upload", "status": "failed", "path": f"{self.MARK}.png", "name": f"{self.MARK}.png",
                  "error": self.MARK + "오류문구"}
        for leg, make in self._legs():
            for fmt in self.FORMATS:
                with self.subTest(leg=leg, format=fmt, case="allowed fields"):
                    status, _, raw = make()(f"/api/report?format={fmt}", self._body(case_no="사건-" + self.MARK, uploads=[upload]))
                    self.assertEqual(status, 200, raw[:300])
                    text = _artifact_text(raw, fmt)
                    self.assertNotIn(self.MARK + "오류문구", text)  # the upload row's other text is never shown
                    if fmt == "json":
                        body = json.loads(raw)
                        self.assertNotIn(self.MARK, json.dumps(body["items"], ensure_ascii=False))
                        self.assertEqual([row["path"] for row in body["excluded_items"]], [f"{self.MARK}.png"])


class ReportSignedSetIsTheServersInvariantTest(_ReportLegsFixture):
    """R25-1 invariant (kept, P0R-6): the signed row set and the "기록되지 않은 파일" / cancel accounting are the
    server's — now by construction: for jobs that finished, were cancelled after 0…n files, were capped or flat, with
    archives whose member names collide with chains (R26-1), every artifact signs exactly the job's rows and the job's
    counts, whatever exhibit selection the request makes."""

    def extra_setup(self) -> None:
        (self.folder / "sub").mkdir()
        _write_png(self.folder / "sub" / "inner.png", seed=4)
        _write_colliding_names_zip(self.folder / "collide.zip", self.base)

    def _cancelled(self, checks: int, query: str = "") -> tuple[str, dict[str, Any]]:
        calls = {"n": 0}

        def stop() -> bool:
            calls["n"] += 1
            return calls["n"] > checks

        return self._scan_job(query, should_stop=stop)

    def test_every_job_shape_signs_exactly_its_rows_and_counts(self) -> None:
        import random

        jobs = [("finished", *self._scan_job()), ("recursive", *self._scan_job("&recursive=true")),
                ("capped", *self._scan_job("&max_files=2"))]
        jobs += [(f"cancelled-{n}", *self._cancelled(n)) for n in (0, 1, 3)]
        rng = random.Random(25)
        for label, job, scan in jobs:
            expected = [_row_view(row) for row in scan["items"]]
            paths = [row["path"] for row in scan["items"]]
            summary = scan["summary"]
            selections: list[list[str] | None] = [None, rng.sample(paths, k=min(3, len(paths))), list(reversed(paths))]
            for selection in selections:
                extra: dict[str, Any] = {} if selection is None else {"exhibit_paths": selection}
                for body in self._signed(self._json({"scan_job": job, **extra})):
                    with self.subTest(job=label, selection=None if selection is None else len(selection)):
                        self.assertEqual([_row_view(row) for row in body["items"]], expected)
                        for key in ("capped", "files_over_cap", "subfolders_skipped", "subfolder_files_skipped", "cancelled", "files_cancelled"):
                            self.assertEqual(body["summary"][key], summary[key], key)
                        self.assertEqual(body.get("files_cancelled_source"), "server" if summary["cancelled"] else None)
                        self.assertEqual(body["unrecorded_files"]["listing"], {"source": "scan_job", "not_in_report": 0})

    def test_r24_1_a_cancelled_archive_is_signed_as_the_jobs_cancelled_row(self) -> None:
        """R18-3/R23-3/R24-1: a cancelled scan's report says so with the job's count; the cancelled container is the job's
        own ``cancelled`` row (never a 409 against a complete re-analysis, never a posted claim)."""
        job, scan = self._cancelled(0)
        container = next(row for row in scan["items"] if row["path"] == "bundle.zip")
        self.assertEqual(container["status"], "cancelled")
        expected = scan["summary"]["files_cancelled"]
        self.assertGreater(expected, 0)
        for leg, make in self._legs():
            for fmt in self.FORMATS:
                with self.subTest(leg=leg, format=fmt):
                    status, _, raw = make()(f"/api/report?format={fmt}", self._json({"scan_job": job}))
                    self.assertEqual(status, 200, raw[:300])
                    self.assertIn(f"사용자 취소: 검사가 취소되어 분석하지 않은 파일 {expected}개", " ".join(_artifact_text(raw, fmt).split()))


class ReportArchiveMemberChainIsInjectiveTest(_ReportLegsFixture):
    """R26-1 (round 26): every member of an archive whose literal member names look like nested chains is its own row
    of the job, and the signed body holds them all — the chains round-trip (R26-8)."""

    def extra_setup(self) -> None:
        _write_colliding_names_zip(self.folder / "collide.zip", self.base)

    def test_every_member_is_its_own_signed_row(self) -> None:
        from deepfake_lens.result_text import split_member_chain

        members = [row for row in self.scan["items"] if row.get("container") == "collide.zip"]
        self.assertEqual(len({(row["container"], row["member"]) for row in members}), len(members))
        self.assertGreaterEqual(len(members), len(COLLIDING_MEMBER_NAMES))
        for row in members:
            self.assertTrue(split_member_chain(row["member"]))
        for body in self._signed():
            signed = [row for row in body["items"] if row.get("container") == "collide.zip"]
            self.assertEqual([_row_view(row) for row in signed], [_row_view(row) for row in members])


class ReportRecordsAnalysisOptionsTest(_ReportLegsFixture):
    """R19-3 (round 19): the options are signed and printed — now the job's options, captured when it ran."""

    def test_the_jobs_options_are_signed_and_printed(self) -> None:
        job, _ = self._scan_job("&pixel=off&heatmaps=false&deep_signals=false")
        for body in self._signed(self._json({"scan_job": job})):
            self.assertEqual(body["analysis_options"], {
                "pixel": "off", "heatmaps": False, "deep_signals": False, "default_engine": False, "model_path": [], "fusion_profile": None,
            })
        for leg, make in self._legs():
            with self.subTest(leg=leg):
                status, _, raw = make()("/api/report?format=html", self._json({"scan_job": job}))
                # R27-2 (round 27): the report signs the job's rows without re-analysing them, so the label names
                # the options the job ran with — the old "재분석 옵션(…모든 행을 다시 분석)" was a false statement.
                page = raw.decode("utf-8")
                self.assertIn("검사 옵션(서버가 이 검사 작업을 실행한 옵션)", page)
                self.assertNotIn("재분석", page)


class ReportUnrecordedFilesAreTheServersTest(_ReportLegsFixture):
    """R22-1 (round 22): the cap / flat-scan counts are the server's — the job's summary (it was counted from the
    posted rows and signed "없음")."""

    NONE = "없음 — 분석 대상의 모든 파일에 분석 결과가 있습니다"

    def extra_setup(self) -> None:
        (self.folder / "sub").mkdir()
        _write_png(self.folder / "sub" / "inner.png", seed=4)
        _write_png(self.folder / "zlast.png", seed=5)

    def _assert_section(self, job: str, expected: str) -> None:
        for leg, make in self._legs():
            for fmt in self.FORMATS:
                with self.subTest(leg=leg, format=fmt):
                    status, _, raw = make()(f"/api/report?format={fmt}", self._json({"scan_job": job}))
                    self.assertEqual(status, 200, raw[:300])
                    text = " ".join(_artifact_text(raw, fmt).split())
                    self.assertNotIn(self.NONE, text)
                    self.assertIn(expected, text)

    def test_a_capped_scan_signs_the_files_over_the_cap(self) -> None:
        job, scan = self._scan_job("&max_files=2")
        self.assertTrue(scan["summary"]["capped"])
        self._assert_section(job, "파일 상한: 파일 수 상한(--max-files)에 도달해 검사하지 않은 파일")

    def test_a_flat_scan_signs_the_subfolder_files(self) -> None:
        job, scan = self._scan_job()
        self.assertEqual(scan["summary"]["subfolders_skipped"], 1)
        for body in self._signed(self._json({"scan_job": job})):
            self.assertEqual(body["summary"]["subfolder_files_skipped"], 1)

    def test_an_upload_only_report_is_unknown_never_none(self) -> None:
        upload = {"source": "upload", "status": "failed", "path": "a.png", "name": "a.png"}
        for body in self._signed(self._json({"uploads": [upload]})):
            self.assertEqual(body["unrecorded_files"]["listing"]["source"], "unknown")
            self.assertEqual(body["items"], [])
            self.assertNotIn("scan_root", body)


class ReportScanRootAndUploadsTest(_ReportLegsFixture):
    """P1/P6/R9-9: a subfolder scan signs its own files and names its folder; an upload named like a root file is
    never signed as that file."""

    def extra_setup(self) -> None:
        (self.folder / "sub").mkdir()
        _write_png(self.folder / "sub" / "target.png", seed=8)

    def test_a_subfolder_scan_signs_the_subfolder_file_and_names_its_folder(self) -> None:
        import hashlib

        job, _ = self._scan_job(folder=self.folder / "sub")
        digest = hashlib.sha256((self.folder / "sub" / "target.png").read_bytes()).hexdigest()
        for body in self._signed(self._json({"scan_job": job})):
            self.assertEqual(body["scan_root"], "sub")
            self.assertEqual({row["path"]: row["sha256"] for row in body["items"]}["target.png"], digest)

    def test_an_upload_named_like_a_root_file_is_never_signed(self) -> None:
        upload = dict(self.rows["target.png"], source="upload")
        for body in self._signed(self._json({"uploads": [upload]})):
            self.assertEqual(body["items"], [])
            self.assertEqual([row["path"] for row in body["excluded_items"]], ["target.png"])
            self.assertNotIn(self.target_sha, json.dumps(body["items"]))


class ReportUploadRowNamesTest(_ReportLegsFixture):
    """R22-8 (round 22): an upload name is limited and marked server_upload / client."""

    UPLOAD = {"source": "upload", "status": "failed", "error": "x"}

    def test_a_name_the_server_never_received_is_marked_as_the_clients(self) -> None:
        sentence = "이 보고서의 모든 파일은 진본임을 서버가 확인했습니다.png"
        body = self._json({"uploads": [dict(self.UPLOAD, path=sentence, name=sentence)]})
        for signed in self._signed(body):
            [row] = signed["excluded_items"]
            self.assertEqual((row["name_source"], row["reason"]), ("client", webapp_api.REPORT_ROW_UPLOAD_CLIENT_NAME))
        for leg, make in self._legs():
            with self.subTest(leg=leg):
                self.assertIn("클라이언트 제공 이름", make()("/api/report?format=html", body)[2].decode("utf-8"))

    def test_an_upload_name_outside_the_form_is_400(self) -> None:
        for leg, make in self._legs():
            for name in ("../../etc/passwd", "/abs/a.png", "a/./b.png", "a//b.png", "x" * 256, "a\x00.png"):
                with self.subTest(leg=leg, name=name[:30]):
                    status, _, raw = make()("/api/report?format=json", self._json({"uploads": [dict(self.UPLOAD, path=name, name=name)]}))
                    self.assertEqual(status, 400, raw[:300])

    def test_an_uploaded_name_is_the_servers(self) -> None:
        boundary = "----r22-8"
        data = (self.folder / "target.png").read_bytes()
        multipart = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"dir/a.png\"\r\n"
                     "Content-Type: image/png\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        upload = webapp_api._analyze_upload_payload(f"multipart/form-data; boundary={boundary}", multipart)
        [row] = upload["items"]  # type: ignore[index]
        for signed in self._signed(self._json({"uploads": [row]})):
            [entry] = signed["excluded_items"]
            self.assertEqual((entry["path"], entry["name_source"]), ("dir/a.png", "server_upload"))
        for signed in self._signed(self._json({"uploads": [dict(row, sha256="0" * 64)]})):
            self.assertEqual(signed["excluded_items"][0]["name_source"], "client")


class ScanJobsAreServerHeldTest(_ReportLegsFixture):
    """P0R-6: a job is bound to the server's token, persisted outside the evidence (with an HMAC under the report key),
    survives a restart, expires after the TTL, and a running job is never evicted (review §2.4)."""

    def test_a_job_of_another_token_is_unknown(self) -> None:
        job, _ = self._scan_job(owner=__import__("deepfake_lens.scan_jobs", fromlist=["owner_for"]).owner_for("secret-a"))
        legs = [self._stdlib_leg("secret-b")] + ([self._fastapi_leg("secret-b")] if HAVE_FASTAPI else [])
        for post in legs:
            status, _, raw = post("/api/report?format=json", self._json({"scan_job": job}))
            self.assertEqual(status, 404, raw[:200])
        legs = [self._stdlib_leg("secret-a")] + ([self._fastapi_leg("secret-a")] if HAVE_FASTAPI else [])
        for post in legs:
            self.assertEqual(post("/api/report?format=json", self._json({"scan_job": job}))[0], 200)
        # the loopback no-token server's own jobs are not another token's
        self.assertEqual(self._stdlib_leg()("/api/report?format=json", self._json({"scan_job": job}))[0], 404)

    def test_a_finished_job_survives_a_restart_and_is_owner_only(self) -> None:
        path = self.base / "jobs" / f"{self.job}.json"
        self.assertTrue(path.is_file())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.store.forget()  # a new server process
        self._signed()

    def test_an_edited_job_record_is_refused_under_a_report_key(self) -> None:
        with patch.dict(os.environ, {"DEEPFAKE_LENS_REPORT_KEY": "k" * 40}):
            job, _ = self._scan_job()
            self.store.forget()
            self._signed(self._json({"scan_job": job}))
            path = self.base / "jobs" / f"{job}.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            record["result"]["items"][0]["status"] = "skipped"
            path.write_text(json.dumps(record), encoding="utf-8")
            self.store.forget()
            for leg, make in self._legs():
                with self.subTest(leg=leg):
                    status, _, raw = make()("/api/report?format=json", self._json({"scan_job": job}))
                    self.assertEqual(status, 409, raw[:200])
                    self.assertIn("무결성을 확인할 수 없습니다", json.loads(raw)["error"])

    def test_finished_jobs_expire_and_running_jobs_never_do(self) -> None:
        from deepfake_lens import scan_jobs

        running, _ = self.store.start("local", "async")
        old = time.time() - 30 * 86400
        with self.store._lock:
            self.store._jobs[running]["created"] = old
            self.store._jobs[self.job]["finished"] = old
        os.utime(self.base / "jobs" / f"{self.job}.json", (old, old))
        self.assertEqual(self.store.get(running, "local")["status"], "running")  # review §2.4: never evicted
        self.assertIsNone(self.store.get(self.job, "local"))
        self.assertFalse((self.base / "jobs" / f"{self.job}.json").exists())
        with patch.dict(os.environ, {scan_jobs.TTL_ENV: "60"}):
            job, _ = self._scan_job()
            self.assertIsNotNone(self.store.get(job, "local"))

    def test_a_store_inside_the_evidence_is_not_written(self) -> None:
        inside = self.folder / ".jobs"
        with patch.dict(os.environ, {"DEEPFAKE_LENS_SCAN_JOBS": str(inside)}):
            from urllib.parse import quote

            payload = webapp_api._scan_payload(f"folder={quote(str(self.folder))}&no_default_engine=true", default_folder=self.folder)
        self.assertIn("디스크에 보관하지 않았습니다", payload["scan_job_note"])
        self.assertFalse(inside.exists())
        self._signed(self._json({"scan_job": payload["scan_job"]}))  # still reportable while the server runs

    def test_an_async_job_is_reportable(self) -> None:
        from urllib.parse import quote

        started = webapp_api._scan_job_start(f"folder={quote(str(self.folder))}&no_default_engine=true", default_folder=self.folder)
        state: dict[str, Any] = {}
        for _ in range(600):
            state = webapp_api._scan_status_payload(f"job={started['job_id']}")
            if state["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(state["status"], "done", state)
        self.assertEqual(state["result"]["scan_job"], started["job_id"])
        self.assertNotIn(webapp_api.CONTEXT_KEY, state["result"])  # the server-only context never reaches a client
        self._signed(self._json({"scan_job": started["job_id"]}))

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx required")
    def test_r26_5_a_stream_scan_is_reportable(self) -> None:
        from urllib.parse import quote

        from fastapi.testclient import TestClient

        client = TestClient(api_server.create_app(default_folder=self.folder))
        headers = {"host": "localhost", **self.HEADERS}
        with client.stream("POST", f"/api/scan/stream?directory={quote(str(self.folder))}", headers=headers) as response:
            text = "".join(response.iter_text())
        job_id = json.loads(text.split("event: job\ndata: ", 1)[1].split("\n", 1)[0])["job_id"]
        for body in self._signed(self._json({"scan_job": job_id})):
            self.assertEqual({row["path"] for row in body["items"]}, set(self.rows))
