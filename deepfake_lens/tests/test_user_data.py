"""R22-3 (round 22): the web GUI's examiner records (reviews.json, feedback.jsonl) never go into the evidence.

They were written to ``~/.deepfake-lens/``: with ``cd ~; deepfake-lens web`` (allowed since R21-10) a saved
review created ``~/.deepfake-lens/reviews.json`` inside the served evidence folder, and with a relative HOME the
file was made in the working folder (``rel/.deepfake-lens/…`` — 2d053dd refused ``HOME=rel web``: a regression).
User decision 2026-10-14: one fixed per-user application-data location; saving is refused (409, Korean) when it
overlaps an examined folder; the old records are migrated once (copied, then marked), never lost.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Any
from unittest import mock

from deepfake_lens import api_server, reviews, user_data, webapp_api

HAVE_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None


def _snapshot(folder: Path) -> dict[str, int]:
    return {str(path.relative_to(folder)): path.lstat().st_mtime_ns for path in [folder, *folder.rglob("*")]}


class _Servers(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        (self.home / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        roots = mock.patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        store = mock.patch.object(reviews, "_DEFAULT_STORE", None)
        store.start()
        self.addCleanup(store.stop)
        clean = {key: value for key, value in os.environ.items()
                 if key not in (user_data.REVIEWS_ENV, user_data.FEEDBACK_ENV, "XDG_DATA_HOME", "APPDATA")}
        env = mock.patch.dict(os.environ, {**clean, "HOME": str(self.home)}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        platform = mock.patch.object(user_data, "_platform", return_value="posix")
        platform.start()
        self.addCleanup(platform.stop)

    def _web(self, folder: Path):
        from deepfake_lens.webapp import CLIENT_HEADER, build_server

        server = build_server("127.0.0.1", 0, default_folder=folder)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def send(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
            data = json.dumps(body).encode("utf-8") if body is not None else None
            request = urllib.request.Request(base + path, data=data, method=method,
                                             headers={CLIENT_HEADER: "qa", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read())

        return send

    def _api(self, folder: Path):
        from fastapi.testclient import TestClient

        client = TestClient(api_server.create_app(default_folder=folder))

        def send(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
            response = client.request(method, path, content=json.dumps(body).encode("utf-8") if body is not None else None,
                                      headers={"host": "localhost", "X-Deepfake-Lens-Client": "qa", "Content-Type": "application/json"})
            return response.status_code, response.json()

        return send

    def _legs(self, folder: Path):
        legs = [("web", self._web(folder))]
        if HAVE_FASTAPI:
            legs.append(("api", self._api(folder)))
        return legs

    @staticmethod
    def _save(send, server: str) -> tuple[int, dict[str, Any]]:
        if server == "web":
            return send("POST", "/api/review", {"artifact_id": "photo.png", "star": True, "note": "검토"})
        return send("PUT", "/api/artifacts/photo.png/review", {"star": True, "note": "검토"})


class ExaminerRecordsNeverInTheEvidenceTest(_Servers):
    def test_a_server_serving_home_refuses_to_save_into_it(self) -> None:
        """cd ~; deepfake-lens web: the default location (~/.local/share/deepfake-lens) is inside the served HOME."""
        before = _snapshot(self.home)
        for leg, send in self._legs(self.home):
            with self.subTest(leg=leg):
                status, body = self._save(send, leg)
                self.assertEqual(status, 409, body)
                text = body.get("error") or body.get("detail")
                self.assertIn("검사 대상 폴더", text)
                self.assertIn("저장하지 않았습니다", text)
                status, body = send("POST", "/api/feedback", {"path": "photo.png", "expected_label": "synthetic"})
                self.assertEqual(status, 409, body)
                status, body = send("GET", "/api/reviews")
                self.assertEqual((status, body["reviews"]), (200, {}))
        self.assertEqual(_snapshot(self.home), before)  # QA-IN-1: nothing written into the evidence

    def test_a_relative_home_saves_nowhere(self) -> None:
        work = self.base / "work"
        work.mkdir()
        before = _snapshot(work)
        cwd = os.getcwd()
        os.chdir(work)
        self.addCleanup(os.chdir, cwd)
        with mock.patch.dict(os.environ, {"HOME": "rel"}):
            for leg, send in self._legs(self.base / "elsewhere"):
                with self.subTest(leg=leg):
                    status, body = self._save(send, leg)
                    self.assertEqual(status, 409, body)
                    self.assertIn("절대 경로가 아닙니다", body.get("error") or body.get("detail"))
                    status, body = send("POST", "/api/feedback", {"path": "a.png", "expected_label": "real"})
                    self.assertEqual(status, 409, body)
        self.assertEqual(_snapshot(work), before)
        self.assertFalse((work / "rel").exists())

    def test_a_relative_override_is_refused(self) -> None:
        with mock.patch.dict(os.environ, {user_data.REVIEWS_ENV: "rel/reviews.json"}):
            with self.assertRaises(user_data.UserDataRefused):
                user_data.record_path(user_data.REVIEWS_FILE, user_data.REVIEWS_ENV)

    def test_the_fixed_location_outside_the_evidence_is_used(self) -> None:
        data = self.base / "data"
        (self.base / "case").mkdir()
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(data)}):
            for leg, send in self._legs(self.base / "case"):
                with self.subTest(leg=leg):
                    status, body = self._save(send, leg)
                    self.assertEqual(status, 200, body)
                    status, body = send("POST", "/api/feedback", {"path": "photo.png", "expected_label": "synthetic"})
                    self.assertEqual(status, 200, body)
                    self.assertEqual(body["feedback_file"], str(data / "deepfake-lens" / "feedback.jsonl"))
            stored = json.loads((data / "deepfake-lens" / "reviews.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["photo.png"]["note"], "검토")
        self.assertFalse((self.home / ".deepfake-lens").exists())

    def test_the_per_os_location(self) -> None:
        with mock.patch.object(user_data, "_platform", return_value="darwin"):
            self.assertEqual(user_data.user_data_dir(), self.home / "Library" / "Application Support" / "deepfake-lens")
        with mock.patch.object(user_data, "_platform", return_value="windows"), \
                mock.patch.dict(os.environ, {"APPDATA": str(self.base / "Roaming")}):
            self.assertEqual(user_data.user_data_dir(), self.base / "Roaming" / "deepfake-lens")
        with mock.patch.object(user_data, "_platform", return_value="windows"), mock.patch.dict(os.environ, {"APPDATA": "rel"}):
            self.assertIsNone(user_data.user_data_dir())
        self.assertEqual(user_data.user_data_dir(), self.home / ".local" / "share" / "deepfake-lens")
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "relative"}):
            self.assertEqual(user_data.user_data_dir(), self.home / ".local" / "share" / "deepfake-lens")


class ExaminerRecordsMigrateOnceTest(_Servers):
    def test_old_records_are_copied_once_and_kept(self) -> None:
        legacy = self.home / ".deepfake-lens"
        legacy.mkdir()
        (legacy / "reviews.json").write_text(json.dumps({"old.png": {"artifact_id": "old.png", "note": "예전 검토"}}), encoding="utf-8")
        (legacy / "feedback.jsonl").write_text('{"path": "old.png", "expected_label": "real"}\n', encoding="utf-8")
        legacy_before = {name: (legacy / name).read_bytes() for name in ("reviews.json", "feedback.jsonl")}
        data = self.base / "data"
        (self.base / "case").mkdir()
        legs = self._legs(self.base / "case")
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(data)}):
            for leg, send in legs:
                with self.subTest(leg=leg):
                    status, body = send("GET", "/api/reviews")
                    self.assertEqual(body["reviews"]["old.png"]["note"], "예전 검토")
                    self.assertEqual(self._save(send, leg)[0], 200)
                    for _ in range(2):
                        status, body = send("POST", "/api/feedback", {"path": "new.png", "expected_label": "synthetic"})
                        self.assertEqual(status, 200, body)
        target = data / "deepfake-lens"
        lines = (target / "feedback.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line)["path"] for line in lines], ["old.png", *(["new.png"] * 2 * len(legs))])
        stored = json.loads((target / "reviews.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(stored), ["old.png", "photo.png"])
        marks = json.loads((target / user_data.MIGRATION_MARKER).read_text(encoding="utf-8"))
        self.assertEqual(sorted(marks), ["feedback.jsonl", "reviews.json"])
        self.assertEqual({name: (legacy / name).read_bytes() for name in legacy_before}, legacy_before)  # never lost

    def test_records_already_in_the_new_place_win_a_merge(self) -> None:
        legacy = self.home / ".deepfake-lens"
        legacy.mkdir()
        (legacy / "reviews.json").write_text(json.dumps({"a": {"note": "old"}, "b": {"note": "old b"}}), encoding="utf-8")
        target = self.base / "data" / "deepfake-lens" / "reviews.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({"a": {"note": "new"}}), encoding="utf-8")
        user_data.migrate_once("reviews.json", target)
        user_data.migrate_once("reviews.json", target)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"a": {"note": "new"}, "b": {"note": "old b"}})


class ExaminerRecordOverridesTest(_Servers):
    """R23-8 (round 23): with DEEPFAKE_LENS_REVIEWS set, ~/.deepfake-lens/reviews.json was merged into the operator's
    file and a marker left in its folder; with DEEPFAKE_LENS_FEEDBACK set nothing was migrated — and the service
    document said both are copied once. Now neither record is migrated into a file an operator named (the default
    location still gets them once: ExaminerRecordsMigrateOnceTest)."""

    def test_no_migration_into_an_operators_file(self) -> None:
        legacy = self.home / ".deepfake-lens"
        legacy.mkdir()
        (legacy / "reviews.json").write_text(json.dumps({"old.png": {"artifact_id": "old.png", "note": "예전 검토"}}), encoding="utf-8")
        (legacy / "feedback.jsonl").write_text('{"path": "old.png", "expected_label": "real"}\n', encoding="utf-8")
        operator = self.base / "operator"
        operator.mkdir()
        (self.base / "case").mkdir()
        overrides = {user_data.REVIEWS_ENV: str(operator / "r.json"), user_data.FEEDBACK_ENV: str(operator / "f.jsonl")}
        with mock.patch.dict(os.environ, overrides):
            for leg, send in self._legs(self.base / "case"):
                with self.subTest(leg=leg):
                    self.assertEqual(self._save(send, leg)[0], 200)
                    self.assertEqual(send("POST", "/api/feedback", {"path": "new.png", "expected_label": "synthetic"})[0], 200)
                    status, body = send("GET", "/api/reviews")
                    self.assertNotIn("old.png", body["reviews"])
        self.assertEqual(sorted(os.listdir(operator)), ["f.jsonl", "r.json"])  # no migration marker
        self.assertEqual(sorted(json.loads((operator / "r.json").read_text(encoding="utf-8"))), ["photo.png"])
        self.assertNotIn("old.png", (operator / "f.jsonl").read_text(encoding="utf-8"))
        self.assertFalse((self.home / ".local").exists())
