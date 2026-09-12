"""Tests for local server hardening (webapp + api-serve guard).

Contract doc: docs/deepfake-lens-service.md — loopback binds, token auth on
/api/*, Host allowlist, and scan limits are all pinned here.
"""

from __future__ import annotations

import importlib.util
import inspect
import unittest

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


class ApiServeTokenGateTest(unittest.TestCase):
    """The CLI must refuse non-localhost API binds without a token."""

    def test_non_local_host_without_token_is_rejected(self) -> None:
        from deepfake_lens import cli

        with self.assertRaises(SystemExit) as ctx:
            cli.main(["api-serve", "--host", "0.0.0.0", "--port", "0"])
        self.assertEqual(ctx.exception.code, 2)


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


if __name__ == "__main__":
    unittest.main()
