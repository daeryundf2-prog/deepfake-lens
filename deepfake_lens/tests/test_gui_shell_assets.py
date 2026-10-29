from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

from deepfake_lens import webapp
from deepfake_lens.error_text import english_prose

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parent
EXPECTED = (
    "gui-shell.css",
    "gui-settings.css",
    "gui-onboarding.css",
    "gui-shell.js",
    "gui-history.js",
    "gui-settings.js",
    "gui-onboarding.js",
)
MEDIA = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
LEADING_COMMENTS = re.compile(r"\A(?:\s*(?://[^\n]*|/\*.*?\*/))*\s*", re.DOTALL)
TEXT_CONTENT = re.compile(r"textContent\s*=\s*(['\"`])(.*?)\1", re.DOTALL)


def _gui_files(suffix: str) -> list[str]:
    return sorted(path.name for path in PACKAGE.glob(f"gui-*{suffix}"))


class ShellAssetsTest(unittest.TestCase):
    def test_the_seven_shell_files_exist(self) -> None:
        self.assertEqual(sorted(_gui_files(".css") + _gui_files(".js")), sorted(EXPECTED))

    def test_every_shell_script_is_one_strict_iife(self) -> None:
        for name in _gui_files(".js"):
            with self.subTest(name=name):
                text = (PACKAGE / name).read_text(encoding="utf-8")
                body = LEADING_COMMENTS.sub("", text, count=1)
                self.assertTrue(body.startswith("(function () {"), name)
                self.assertIn("'use strict'", text)

    def test_every_shell_asset_is_served_with_its_media_type(self) -> None:
        for name in _gui_files(".css") + _gui_files(".js"):
            with self.subTest(name=name):
                self.assertEqual(
                    webapp.GUI_STATIC_ASSETS["/" + name],
                    (name, MEDIA[Path(name).suffix]),
                )

    def test_every_shell_asset_is_in_package_data(self) -> None:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        package_data = pyproject["tool"]["setuptools"]["package-data"]["deepfake_lens"]
        for name in _gui_files(".css") + _gui_files(".js"):
            with self.subTest(name=name):
                self.assertIn(name, package_data)

    def test_textcontent_literals_in_shell_scripts_are_korean(self) -> None:
        for name in _gui_files(".js"):
            text = (PACKAGE / name).read_text(encoding="utf-8")
            for match in TEXT_CONTENT.finditer(text):
                literal = match.group(2)
                with self.subTest(name=name, literal=literal):
                    self.assertFalse(english_prose(literal), literal)


if __name__ == "__main__":
    unittest.main()
