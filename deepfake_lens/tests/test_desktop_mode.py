"""DESK (2026-10-23): the desktop mode — `deepfake-lens desktop`, its key file, port choice, folder dialog and routes.

The plain ``web`` server must not change: its /api/stats says ``desktop: false`` and the desktop routes are 404 there.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from collections import OrderedDict

from deepfake_lens import desktop, webapp_api
from deepfake_lens.signing import REPORT_KEY_ENV, ReportKeyError
from deepfake_lens.webapp import CLIENT_HEADER, CLIENT_HEADER_VALUE, build_server


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class SigningKeyFileTest(unittest.TestCase):
    """The key is made once in the user data folder and never overwritten."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch("deepfake_lens.user_data.user_data_dir", return_value=self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(REPORT_KEY_ENV, None)

    def test_env_wins_and_no_file_is_made(self) -> None:
        os.environ[REPORT_KEY_ENV] = "x" * 40
        self.assertEqual(desktop.prepare_signing_key(), ("env", None))
        self.assertFalse((self.folder / desktop.KEY_FILE_NAME).exists())

    def test_a_key_is_created_once_then_read_back(self) -> None:
        source, path = desktop.prepare_signing_key()
        self.assertEqual((source, path), ("created", self.folder / desktop.KEY_FILE_NAME))
        key = os.environ[REPORT_KEY_ENV]
        self.assertEqual(len(key), 64)
        self.assertEqual(path.read_text(encoding="utf-8"), key + "\n")
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        os.environ.pop(REPORT_KEY_ENV)
        self.assertEqual(desktop.prepare_signing_key(), ("file", path))
        self.assertEqual(os.environ[REPORT_KEY_ENV], key)  # the same key: earlier reports still verify

    def test_a_damaged_key_file_stops_the_start_and_is_kept(self) -> None:
        path = self.folder / desktop.KEY_FILE_NAME
        path.write_text("short\n", encoding="utf-8")
        with self.assertRaises(ReportKeyError) as ctx:
            desktop.prepare_signing_key()
        self.assertIn("손상", str(ctx.exception))
        self.assertEqual(path.read_text(encoding="utf-8"), "short\n")
        self.assertNotIn(REPORT_KEY_ENV, os.environ)

    def test_no_user_data_folder_means_unsigned_not_a_crash(self) -> None:
        with mock.patch("deepfake_lens.user_data.user_data_dir", return_value=None):
            self.assertEqual(desktop.prepare_signing_key(), ("none", None))
        self.assertNotIn(REPORT_KEY_ENV, os.environ)


class PortAndIdleTest(unittest.TestCase):
    def test_choose_port_skips_a_busy_port(self) -> None:
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen(1)
            port = busy.getsockname()[1]
            with mock.patch.object(desktop, "this_tool_listens", return_value=False):
                chosen, running = desktop.choose_port(port, 3)
        self.assertNotEqual(chosen, port)
        self.assertFalse(running)

    def test_choose_port_reports_this_tool_already_running(self) -> None:
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen(1)
            port = busy.getsockname()[1]
            with mock.patch.object(desktop, "this_tool_listens", return_value=True):
                self.assertEqual(desktop.choose_port(port, 3), (port, True))

    def test_idle_exit_waits_for_a_scan_and_the_timeout(self) -> None:
        state = desktop.DesktopState(idle_exit_minutes=1)
        now = state.last_activity
        self.assertFalse(state.should_exit(scanning=False, now=now + 59))
        self.assertTrue(state.should_exit(scanning=False, now=now + 61))
        self.assertFalse(state.should_exit(scanning=True, now=now + 61))
        state.touch()
        self.assertFalse(state.should_exit(scanning=False, now=state.last_activity + 1))
        self.assertFalse(desktop.DesktopState(idle_exit_minutes=0).should_exit(scanning=False, now=now + 10 ** 6))


class FolderDialogStubTest(unittest.TestCase):
    def test_the_stub_replaces_the_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {desktop.PICK_FOLDER_STUB_ENV: tmp}):
                self.assertEqual(desktop.pick_folder(), Path(tmp))
            with mock.patch.dict(os.environ, {desktop.PICK_FOLDER_STUB_ENV: ""}):
                self.assertIsNone(desktop.pick_folder())
            with mock.patch.dict(os.environ, {desktop.PICK_FOLDER_STUB_ENV: "501"}):
                with self.assertRaises(desktop.DialogUnavailable):
                    desktop.pick_folder()

    def test_no_helper_is_unavailable_not_a_crash(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(desktop.PICK_FOLDER_STUB_ENV, None)
            with mock.patch.object(desktop, "_dialog_command", return_value=None):
                with self.assertRaises(desktop.DialogUnavailable):
                    desktop.pick_folder()

    def test_one_dialog_at_a_time(self) -> None:
        state = desktop.DesktopState()
        state.dialog_lock.acquire()
        try:
            with self.assertRaises(desktop.DialogBusy):
                state.pick_folder_once()
        finally:
            state.dialog_lock.release()


class _ServerMixin:
    """A live loopback server (plain or desktop) on a free port; requests carry the client header."""

    state: desktop.DesktopState | None = None

    def start(self, folder: Path, state: desktop.DesktopState | None) -> str:
        roots = mock.patch.object(webapp_api, "_READ_ROOTS", OrderedDict())  # the registry is per process
        roots.start()
        self.addCleanup(roots.stop)  # type: ignore[attr-defined]
        port = _free_port()
        self.server = build_server("127.0.0.1", port, default_folder=folder, desktop=state)
        if state is not None:
            state.shutdown = self.server.shutdown
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)  # type: ignore[attr-defined]
        return f"http://127.0.0.1:{port}"

    def _stop(self) -> None:
        desktop.STATE = None
        if self.thread.is_alive():
            self.server.shutdown()
        self.server.server_close()

    def call(self, url: str, method: str = "GET", header: bool = True) -> tuple[int, dict]:
        request = urllib.request.Request(url, method=method, headers={CLIENT_HEADER: CLIENT_HEADER_VALUE} if header else {})
        try:
            with urllib.request.urlopen(request, timeout=10) as answer:
                return answer.status, json.loads(answer.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


class PlainServerUnchangedTest(_ServerMixin, unittest.TestCase):
    def test_desktop_routes_are_404_and_stats_say_desktop_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = self.start(Path(tmp), None)
            status, stats = self.call(base + "/api/stats")
            self.assertEqual(status, 200)
            self.assertFalse(stats["desktop"])
            self.assertIsNone(stats["idle_exit_minutes"])
            self.assertEqual(stats["roots"], [str(Path(tmp).resolve())])
            self.assertIn("signing", stats)
            for path, method in (("/api/roots", "GET"), ("/api/pick-folder", "POST"), ("/api/shutdown", "POST"), ("/api/heartbeat", "POST")):
                with self.subTest(path=path):
                    self.assertEqual(self.call(base + path, method)[0], 404)

    def test_desktop_refuses_lan_and_token(self) -> None:
        with self.assertRaises(ValueError):
            build_server("127.0.0.1", _free_port(), allow_lan=True, token="t" * 40, desktop=desktop.DesktopState())
        with self.assertRaises(ValueError):
            build_server("127.0.0.1", _free_port(), token="t" * 40, desktop=desktop.DesktopState())
        desktop.STATE = None


class DesktopServerTest(_ServerMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name) / "evidence"
        self.folder.mkdir()
        self.other = Path(self.tmp.name) / "other"
        self.other.mkdir()
        self.state = desktop.DesktopState(idle_exit_minutes=7, key_source="file", key_file=Path("/k"))
        self.base = self.start(self.folder, self.state)

    def test_stats_roots_heartbeat(self) -> None:
        status, stats = self.call(self.base + "/api/stats")
        self.assertEqual(status, 200)
        self.assertTrue(stats["desktop"])
        self.assertEqual(stats["idle_exit_minutes"], 7)
        self.assertEqual(stats["roots"], [str(self.folder.resolve())])
        before = self.state.last_activity
        time.sleep(0.01)
        self.assertEqual(self.call(self.base + "/api/heartbeat", "POST"), (200, {"status": "ok"}))
        self.assertGreater(self.state.last_activity, before)  # a heartbeat is activity for the idle exit
        status, roots = self.call(self.base + "/api/roots")
        self.assertEqual([r["path"] for r in roots["roots"]], [str(self.folder.resolve())])

    def test_pick_folder_registers_the_chosen_folder_and_keeps_the_earlier_root(self) -> None:
        with mock.patch.dict(os.environ, {desktop.PICK_FOLDER_STUB_ENV: str(self.other)}):
            status, body = self.call(self.base + "/api/pick-folder", "POST")
        self.assertEqual(status, 200)
        self.assertEqual(body["folder"], str(self.other.resolve()))
        self.assertTrue(body["folder_b64"])
        _, roots = self.call(self.base + "/api/roots")
        self.assertEqual([r["path"] for r in roots["roots"]], [str(self.folder.resolve()), str(self.other.resolve())])
        # the new root is usable: a scan of it is not a 403
        status, _ = self.call(self.base + "/api/scan?folder=" + urllib.request.quote(str(self.other)) + "&async=0")
        self.assertEqual(status, 200)

    def test_pick_folder_cancel_unavailable_and_not_a_folder(self) -> None:
        with mock.patch.dict(os.environ, {desktop.PICK_FOLDER_STUB_ENV: ""}):
            self.assertEqual(self.call(self.base + "/api/pick-folder", "POST"), (200, {"folder": None, "folder_b64": None}))
        with mock.patch.dict(os.environ, {desktop.PICK_FOLDER_STUB_ENV: "501"}):
            status, body = self.call(self.base + "/api/pick-folder", "POST")
        self.assertEqual((status, body["error"]), (501, desktop.DIALOG_UNAVAILABLE))
        missing = Path(self.tmp.name) / "nope"
        with mock.patch.dict(os.environ, {desktop.PICK_FOLDER_STUB_ENV: str(missing)}):
            status, body = self.call(self.base + "/api/pick-folder", "POST")
        self.assertEqual(status, 400)
        self.assertIn("폴더가 아닙니다", body["error"])
        _, roots = self.call(self.base + "/api/roots")
        self.assertEqual(len(roots["roots"]), 1)  # nothing was registered by the refused calls

    def test_pick_folder_is_busy_while_a_dialog_is_open(self) -> None:
        self.state.dialog_lock.acquire()
        try:
            status, body = self.call(self.base + "/api/pick-folder", "POST")
        finally:
            self.state.dialog_lock.release()
        self.assertEqual((status, body["error"]), (409, desktop.DIALOG_BUSY))

    def test_routes_need_the_client_header(self) -> None:
        for path in ("/api/pick-folder", "/api/shutdown", "/api/heartbeat"):
            with self.subTest(path=path):
                self.assertEqual(self.call(self.base + path, "POST", header=False)[0], 401)

    def test_shutdown_refuses_while_a_scan_runs_then_stops_the_server(self) -> None:
        from deepfake_lens.scan_jobs import SOURCE_ASYNC, STORE

        job_id, _cancel = STORE.start("local", SOURCE_ASYNC)
        try:
            status, body = self.call(self.base + "/api/shutdown", "POST")
            self.assertEqual(status, 409)
            self.assertEqual(body, {"error": desktop.SHUTDOWN_WHILE_SCANNING, "job_id": job_id})
            self.assertTrue(self.thread.is_alive())
        finally:
            STORE.finish(job_id, "cancelled", {"items": [], "summary": {}})
        self.assertEqual(self.call(self.base + "/api/shutdown", "POST"), (200, {"status": "shutting_down"}))
        self.thread.join(10)
        self.assertFalse(self.thread.is_alive())
        self.assertTrue(self.state.shutting_down)


class CommandSurfaceTest(unittest.TestCase):
    def test_desktop_is_a_gate_command_with_korean_help(self) -> None:
        from deepfake_lens.cli_parser import GATE_COMMANDS, NON_GATE_MARK, NON_GATE_NOTE, build_parser

        self.assertIn("desktop", GATE_COMMANDS)
        _parser, commands = build_parser()
        description = commands["desktop"].description or ""
        self.assertNotIn(NON_GATE_NOTE, description)
        self.assertNotIn(NON_GATE_MARK, description)
        self.assertIn("데스크톱", description)
