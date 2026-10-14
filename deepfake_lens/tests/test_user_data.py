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


class ExaminerRecordWriteFailuresTest(_Servers):
    """R23-7 (round 23): with the record folder not writable (uid 1000, ~/.local/share/deepfake-lens 0500) POST /api/review
    answered 200 "success" and GET /api/reviews showed the entry from the memory cache — nothing on disk, gone after a
    restart (ReviewStore._persist passed over every OSError); POST /api/feedback was a 500 (PermissionError). A write that
    fails is now a Korean error — 409, or 507 when the disk is full — and the cache is put back as it was. (The gate runs
    as root, where mode bits do not stop a write: the folder is made unwritable by a regular file in its place.)"""

    def setUp(self) -> None:
        super().setUp()
        (self.base / "case").mkdir()
        self.data = self.base / "data"
        env = mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.data)})
        env.start()
        self.addCleanup(env.stop)

    def _block(self) -> None:
        self.data.write_text("not a folder", encoding="utf-8")  # mkdir data/deepfake-lens fails, also for root

    def test_an_unwritable_record_folder_is_a_korean_409_on_both_servers(self) -> None:
        self._block()
        for leg, send in self._legs(self.base / "case"):
            with self.subTest(leg=leg):
                status, body = self._save(send, leg)
                self.assertEqual(status, 409, body)
                text = body.get("error") or body.get("detail")
                self.assertIn("저장하지 못했습니다", text)
                self.assertIn("기록은 바뀌지 않았습니다", text)
                status, body = send("GET", "/api/reviews")
                self.assertEqual((status, body["reviews"]), (200, {}))  # not kept in memory
                status, body = send("POST", "/api/feedback", {"path": "photo.png", "expected_label": "synthetic"})
                self.assertEqual(status, 409, body)
                self.assertIn("저장하지 못했습니다", body.get("error") or body.get("detail"))

    def test_a_full_disk_is_507_and_the_previous_records_stay(self) -> None:
        import errno

        from deepfake_lens import output_files

        legs = self._legs(self.base / "case")
        send = legs[0][1]
        self.assertEqual(self._save(send, "web")[0], 200)
        status, _ = send("POST", "/api/feedback", {"path": "photo.png", "expected_label": "synthetic"})
        self.assertEqual(status, 200)
        stored = (self.data / "deepfake-lens" / "reviews.json").read_bytes()
        feedback = self.data / "deepfake-lens" / "feedback.jsonl"
        feedback_before = feedback.read_bytes()

        def full(*_: Any, **__: Any) -> None:
            raise OSError(errno.ENOSPC, "No space left on device")

        real_open = Path.open

        def half_written(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
            handle = real_open(path, mode, *args, **kwargs)
            if path != feedback or "a" not in mode:
                return handle

            class Partial:
                def __enter__(self) -> "Partial":
                    return self

                def __exit__(self, *exc: object) -> None:
                    handle.close()

                def write(self, data: bytes) -> int:
                    handle.write(data[:7])
                    handle.flush()
                    raise OSError(errno.ENOSPC, "No space left on device")

            return Partial()

        for leg, send in legs:
            with self.subTest(leg=leg), mock.patch.object(output_files, "write_output_text", full), \
                    mock.patch.object(Path, "open", half_written):
                status, body = send("POST" if leg == "web" else "PUT", "/api/review" if leg == "web" else "/api/artifacts/photo.png/review",
                                    {"artifact_id": "photo.png", "star": False, "note": "바뀐 검토"})
                self.assertEqual(status, 507, body)
                self.assertIn("디스크 공간이 부족합니다", body.get("error") or body.get("detail"))
                status, body = send("GET", "/api/reviews")
                self.assertEqual(body["reviews"]["photo.png"]["note"], "검토")  # the cache was put back
                status, body = send("POST", "/api/feedback", {"path": "new.png", "expected_label": "real"})
                self.assertEqual(status, 507, body)
        self.assertEqual((self.data / "deepfake-lens" / "reviews.json").read_bytes(), stored)
        self.assertEqual(feedback.read_bytes(), feedback_before)  # the half-written line was cut off again

    def test_an_unreadable_record_file_is_never_overwritten(self) -> None:
        target = self.data / "deepfake-lens" / "reviews.json"
        target.parent.mkdir(parents=True)
        for content in (b"[1, 2]", b"{not json", "검토".encode("utf-16")):
            target.write_bytes(content)
            reviews._DEFAULT_STORE = None
            for leg, send in self._legs(self.base / "case"):
                with self.subTest(leg=leg, content=content[:8]):
                    status, body = self._save(send, leg)
                    self.assertEqual(status, 409, body)
                    self.assertIn("읽지 못해", body.get("error") or body.get("detail"))
            self.assertEqual(target.read_bytes(), content)


class ExaminerRecordRepairedFileTest(_Servers):
    """R24-4 (round 24): after a refusal for an unreadable ``reviews.json`` (R23-7) the examiner repaired the file as the
    409 message asks — and every save was still refused with the old reason, GET /api/reviews still empty, until the
    server restarted (``ReviewStore._load`` kept its cache; the unreadable flag was cleared only when the path changed).
    The file is read again on the next request after a failed read, and whenever it changed on disk."""

    def setUp(self) -> None:
        super().setUp()
        (self.base / "case").mkdir()
        self.data = self.base / "data"
        env = mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.data)})
        env.start()
        self.addCleanup(env.stop)
        self.target = self.data / "deepfake-lens" / "reviews.json"
        self.target.parent.mkdir(parents=True)
        reviews._DEFAULT_STORE = None
        self.addCleanup(setattr, reviews, "_DEFAULT_STORE", None)

    def _kept(self) -> dict[str, Any]:
        return {"b.png": {"artifact_id": "b.png", "star": True, "note": "기존 검토", "analyst_id": "examiner",
                          "verdict": "unreviewed", "marks": [], "created_at": "2026-10-01T00:00:00+00:00",
                          "updated_at": "2026-10-01T00:00:00+00:00"}}

    def _refuse_repair_save(self, send: Any, leg: str, break_file: Any, repair: Any) -> None:
        break_file()
        status, body = self._save(send, leg)
        self.assertEqual(status, 409, body)
        self.assertIn("읽지 못해", body.get("error") or body.get("detail"))
        repair()
        status, body = send("GET", "/api/reviews")
        self.assertEqual((status, sorted(body["reviews"])), (200, ["b.png"]))  # was {} until a restart
        status, body = self._save(send, leg)
        self.assertEqual(status, 200, body)  # was 409 with the old reason until a restart
        stored = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(sorted(stored), ["b.png", "photo.png"])
        self.assertEqual(stored["b.png"], self._kept()["b.png"])  # the repaired file's entries are kept

    def test_a_repaired_file_is_read_again_on_the_same_server(self) -> None:
        def write_kept() -> None:
            self.target.write_text(json.dumps(self._kept(), ensure_ascii=False), encoding="utf-8")

        for leg, send in self._legs(self.base / "case"):
            for content in (b"[1, 2]", b"{not json"):
                with self.subTest(leg=leg, content=content):
                    self._refuse_repair_save(send, leg, lambda: self.target.write_bytes(content), write_kept)
            if os.geteuid() != 0:  # mode bits do not stop root (the gate image)
                write_kept()
                with self.subTest(leg=leg, content="chmod 000"):
                    self._refuse_repair_save(send, leg, lambda: self.target.chmod(0), lambda: self.target.chmod(0o644))

    def test_a_file_changed_on_disk_is_read_again(self) -> None:
        legs = self._legs(self.base / "case")
        for leg, send in legs:
            with self.subTest(leg=leg):
                self.target.write_text(json.dumps(self._kept(), ensure_ascii=False), encoding="utf-8")
                self.assertEqual(sorted(send("GET", "/api/reviews")[1]["reviews"]), ["b.png"])
                self.assertEqual(self._save(send, leg)[0], 200)
                edited = json.loads(self.target.read_text(encoding="utf-8"))
                edited["c.png"] = dict(self._kept()["b.png"], artifact_id="c.png")
                self.target.write_text(json.dumps(edited, ensure_ascii=False), encoding="utf-8")
                self.assertEqual(sorted(send("GET", "/api/reviews")[1]["reviews"]), ["b.png", "c.png", "photo.png"])


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
