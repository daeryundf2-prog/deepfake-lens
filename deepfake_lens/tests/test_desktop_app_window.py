from __future__ import annotations

import ntpath
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deepfake_lens import desktop
from deepfake_lens.cli_parser import build_parser

URL = "http://127.0.0.1:8765/gui"


def _exists_in(paths: list[str]):
    return lambda path: path in paths


class FindAppBrowserTest(unittest.TestCase):
    def test_windows_order_edge_before_chrome_and_program_files_x86_first(self) -> None:
        env = {"ProgramFiles(x86)": "C:\\PF86", "ProgramFiles": "C:\\PF", "LOCALAPPDATA": "C:\\LAD"}
        edge = r"Microsoft\Edge\Application\msedge.exe"
        chrome = r"Google\Chrome\Application\chrome.exe"
        order = [
            ntpath.join("C:\\PF86", edge),
            ntpath.join("C:\\PF", edge),
            ntpath.join("C:\\LAD", edge),
            ntpath.join("C:\\PF86", chrome),
            ntpath.join("C:\\PF", chrome),
            ntpath.join("C:\\LAD", chrome),
        ]
        seen: list[str] = []

        def exists(path: str) -> bool:
            seen.append(path)
            return path == order[-1]

        found = desktop.find_app_browser(platform="win32", environ=env, exists=exists, which=lambda name: None)
        self.assertEqual(found, order[-1])
        self.assertEqual(seen, order)

    def test_windows_first_existing_candidate_wins(self) -> None:
        env = {"ProgramFiles": "C:\\PF", "LOCALAPPDATA": "C:\\LAD"}
        want = ntpath.join("C:\\LAD", r"Microsoft\Edge\Application\msedge.exe")
        found = desktop.find_app_browser(platform="win32", environ=env, exists=_exists_in([want]), which=lambda name: None)
        self.assertEqual(found, want)

    def test_macos_order_system_apps_then_user_applications(self) -> None:
        env = {"HOME": "/Users/t"}
        order = [
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Users/t/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Users/t/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
        seen: list[str] = []

        def exists(path: str) -> bool:
            seen.append(path)
            return False

        self.assertIsNone(desktop.find_app_browser(platform="darwin", environ=env, exists=exists, which=lambda name: None))
        self.assertEqual(seen, order)

    def test_macos_chrome_in_system_applications_wins_over_user_edge(self) -> None:
        env = {"HOME": "/Users/t"}
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        user_edge = "/Users/t/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
        found = desktop.find_app_browser(platform="darwin", environ=env, exists=_exists_in([user_edge, chrome]), which=lambda name: None)
        self.assertEqual(found, chrome)

    def test_other_platform_uses_which_in_order(self) -> None:
        names: list[str] = []

        def which(name: str) -> str | None:
            names.append(name)
            return "/usr/bin/google-chrome-stable" if name == "google-chrome-stable" else None

        found = desktop.find_app_browser(platform="linux", environ={}, exists=lambda path: True, which=which)
        self.assertEqual(found, "/usr/bin/google-chrome-stable")
        self.assertEqual(names, ["microsoft-edge", "microsoft-edge-stable", "google-chrome", "google-chrome-stable"])

    def test_none_when_nothing_is_installed(self) -> None:
        self.assertIsNone(desktop.find_app_browser(platform="linux", environ={}, exists=lambda p: False, which=lambda n: None))


class OpenAppWindowTest(unittest.TestCase):
    def test_popen_argv_and_stdio_are_exact(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def popen(argv: list[str], **kwargs: object) -> object:
            calls.append((argv, kwargs))
            return object()

        ok = desktop.open_app_window(URL, profile_dir=Path("/p/app-window"), executable="/bin/chrome", popen=popen)
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)
        argv, kwargs = calls[0]
        self.assertEqual(argv, [
            "/bin/chrome",
            f"--app={URL}",
            "--user-data-dir=/p/app-window",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1440,900",
        ])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertIs(kwargs["close_fds"], True)
        if os.name == "nt":
            self.assertEqual(kwargs["creationflags"], 0x00000008 | 0x00000200)
        else:
            self.assertIs(kwargs["start_new_session"], True)
            self.assertNotIn("creationflags", kwargs)

    def test_oserror_returns_false(self) -> None:
        def popen(argv: list[str], **kwargs: object) -> object:
            raise OSError("cannot start")

        self.assertFalse(desktop.open_app_window(URL, profile_dir=Path("/p"), executable="/bin/chrome", popen=popen))


class OpenGuiTest(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "deepfake-lens"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_app_window_used_with_its_own_profile_and_browser_tab_not_called(self) -> None:
        with mock.patch.object(desktop, "user_data_dir", return_value=self.data), \
                mock.patch.object(desktop, "find_app_browser", return_value="/bin/chrome"), \
                mock.patch.object(desktop, "open_app_window", return_value=True) as app, \
                mock.patch.object(desktop, "open_browser", return_value=True) as browser:
            self.assertTrue(desktop.open_gui(URL, browser_tab=False))
        browser.assert_not_called()
        app.assert_called_once_with(URL, profile_dir=self.data / "app-window", executable="/bin/chrome")
        self.assertTrue((self.data / "app-window").is_dir())

    def test_app_window_failure_falls_back_to_open_browser(self) -> None:
        with mock.patch.object(desktop, "user_data_dir", return_value=self.data), \
                mock.patch.object(desktop, "find_app_browser", return_value="/bin/chrome"), \
                mock.patch.object(desktop, "open_app_window", return_value=False), \
                mock.patch.object(desktop, "open_browser", return_value=True) as browser:
            self.assertTrue(desktop.open_gui(URL, browser_tab=False))
        browser.assert_called_once_with(URL)

    def test_browser_tab_skips_app_window(self) -> None:
        with mock.patch.object(desktop, "user_data_dir", return_value=self.data), \
                mock.patch.object(desktop, "find_app_browser") as finder, \
                mock.patch.object(desktop, "open_browser", return_value=True) as browser:
            self.assertTrue(desktop.open_gui(URL, browser_tab=True))
        finder.assert_not_called()
        browser.assert_called_once_with(URL)
        self.assertFalse((self.data / "app-window").exists())

    def test_no_profile_folder_skips_app_window(self) -> None:
        with mock.patch.object(desktop, "user_data_dir", return_value=None), \
                mock.patch.object(desktop, "find_app_browser") as finder, \
                mock.patch.object(desktop, "open_browser", return_value=True) as browser:
            self.assertTrue(desktop.open_gui(URL, browser_tab=False))
        finder.assert_not_called()
        browser.assert_called_once_with(URL)

    def test_no_executable_falls_back_to_open_browser(self) -> None:
        with mock.patch.object(desktop, "user_data_dir", return_value=self.data), \
                mock.patch.object(desktop, "find_app_browser", return_value=None), \
                mock.patch.object(desktop, "open_browser", return_value=True) as browser:
            self.assertTrue(desktop.open_gui(URL, browser_tab=False))
        browser.assert_called_once_with(URL)


class BrowserTabFlagTest(unittest.TestCase):
    def test_desktop_parses_browser_tab(self) -> None:
        parser, _commands = build_parser()
        self.assertTrue(parser.parse_args(["desktop", "--browser-tab"]).browser_tab)
        self.assertFalse(parser.parse_args(["desktop"]).browser_tab)


if __name__ == "__main__":
    unittest.main()
