from __future__ import annotations

import importlib.util
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_registry_links", REPO_ROOT / "scripts" / "check_registry_links.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RegistryLinkCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = _load_checker()

    def test_backtick_wrapped_url_is_not_captured_with_backtick(self) -> None:
        found = self.checker.URL_PATTERN.findall("send to `https://example.org/a`.")
        self.assertEqual(found, ["https://example.org/a"])

    def test_loopback_and_placeholder_urls_are_skipped(self) -> None:
        for url in (
            "http://localhost:8765",
            "http://127.0.0.1:8000/x",
            "https://drive.google.com/uc?id=<FILE_ID",
        ):
            self.assertTrue(self.checker.is_skipped(url), url)
        self.assertFalse(self.checker.is_skipped("https://github.com/WisconsinAIVision/UniversalFakeDetect"))

    def test_persistent_timeout_is_a_warning_not_a_failure(self) -> None:
        with mock.patch.object(self.checker.urllib.request, "urlopen", side_effect=TimeoutError("slow")), \
                mock.patch.object(self.checker.time, "sleep"):
            ok, detail = self.checker.check("https://slow.example.org/")
        self.assertTrue(ok)
        self.assertIn("warning", detail)

    def test_http_404_still_fails(self) -> None:
        err = urllib.error.HTTPError("https://x.example.org/", 404, "nf", {}, None)
        with mock.patch.object(self.checker.urllib.request, "urlopen", side_effect=err):
            ok, detail = self.checker.check("https://x.example.org/")
        self.assertFalse(ok)
        self.assertEqual(detail, "HTTP 404")


if __name__ == "__main__":
    unittest.main()
