"""Persistent Review Marks & Examiner Audit Store for Deepfake Lens.

Provides persistent storage for examiner review marks, notes, verdicts, and
bounding-box/point annotations across scan sessions. Prevents examiner marks
from being lost or isolated in client-side browser localStorage.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .json_text import json_dumps
from .user_data import (
    REVIEWS_ENV,
    REVIEWS_FILE,
    USER_DATA_READ_FAILED,
    UserDataRefused,
    UserDataWriteFailed,
    check_outside,
    migrate_default,
    record_path,
    write_failed,
)


class ReviewStore:
    """Thread-safe, file-backed review marks storage.

    R22-3 (round 22): without an explicit ``storage_path`` the file is the
    fixed per-user ``reviews.json`` of :mod:`user_data` (``DEEPFAKE_LENS_REVIEWS``
    overrides it), resolved at each call; it was ``~/.deepfake-lens/reviews.json``,
    inside the evidence when the web server served HOME and in the working
    folder with a relative HOME. ``evidence`` (the folders the server
    examines): a file inside one is never written (:class:`user_data.UserDataRefused`)
    nor read. Records in ``~/.deepfake-lens/reviews.json`` are migrated once.

    R23-7 (round 23): a save or delete that cannot reach the disk raises
    :class:`user_data.UserDataWriteFailed` (HTTP 409, or 507 when the disk is
    full) and the in-memory cache is put back as it was — it used to answer
    "success" and keep the entry in memory only. An existing file that cannot
    be read (permissions, not a JSON object) is never overwritten by a save:
    the save is refused too. R23-8: migration only into the default location
    (not into ``DEEPFAKE_LENS_REVIEWS``' file), like the feedback record.
    """

    def __init__(self, storage_path: Path | str | None = None) -> None:
        self._explicit = Path(storage_path) if storage_path else None
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] | None = None
        self._cache_path: Path | None = None
        self._unreadable: str | None = None  # R23-7: why the existing file could not be read

    @property
    def storage_path(self) -> Path:
        """The file (explicit, else the per-user location); raises UserDataRefused when there is none."""
        return self._explicit if self._explicit is not None else record_path(REVIEWS_FILE, REVIEWS_ENV)

    def _path(self, evidence: list[Path] | tuple[Path, ...]) -> Path:
        path = self.storage_path
        check_outside(path, evidence, REVIEWS_ENV)
        if self._explicit is None:
            migrate_default(REVIEWS_FILE, REVIEWS_ENV, path)  # R23-8: never into an operator's file
        if path != self._cache_path:
            self._cache, self._cache_path, self._unreadable = None, path, None
        return path

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._cache is not None:
            return self._cache
        assert self._cache_path is not None
        if not self._cache_path.is_file():
            self._cache = {}
            return self._cache
        from .error_text import read_error_ko

        self._unreadable = None
        try:
            content = self._cache_path.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict):
                self._cache = data
            else:
                self._cache = {}
                self._unreadable = "JSON 객체가 아닙니다"
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self._cache = {}
            self._unreadable = read_error_ko(exc)
        return self._cache

    def _writable(self) -> None:
        """R23-7: refuse a save that would overwrite an existing file this store could not read."""
        if self._unreadable is not None:
            raise UserDataWriteFailed(USER_DATA_READ_FAILED.format(path=self._cache_path, reason=self._unreadable, env=REVIEWS_ENV))

    def _persist(self) -> None:
        if self._cache is None or self._cache_path is None:
            return
        from .output_files import write_output_text

        try:
            # R19-6 (round 19): the shared writer — a unique temp name, removed on any exception.
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            write_output_text(self._cache_path, json_dumps(self._cache, ensure_ascii=False, indent=2))
        except OSError as exc:
            # R23-7: was `pass` — "success" with nothing on disk
            raise write_failed(self._cache_path, exc, REVIEWS_ENV) from exc

    def _apply(self, store: dict[str, dict[str, Any]], key: str, entry: dict[str, Any] | None) -> None:
        """Set (or, with None, remove) ``store[key]`` and persist; on a failed write put the entry back as it was."""
        had, previous = key in store, store.get(key)
        if entry is None:
            store.pop(key, None)
        else:
            store[key] = entry
        try:
            self._persist()
        except UserDataWriteFailed:
            if had:
                store[key] = previous  # type: ignore[assignment]
            else:
                store.pop(key, None)
            raise

    def get_review(self, artifact_id: str, evidence: list[Path] | tuple[Path, ...] = ()) -> dict[str, Any] | None:
        """Fetch review entry for artifact_id (or None if unreviewed — R22-3: or when the file has no place outside the evidence)."""
        key = str(artifact_id).strip()
        with self._lock:
            try:
                self._path(evidence)
            except UserDataRefused:
                return None
            store = self._load()
            return store.get(key)

    def save_review(self, artifact_id: str, data: dict[str, Any], evidence: list[Path] | tuple[Path, ...] = ()) -> dict[str, Any]:
        """Save or update review entry for artifact_id (R22-3: raises UserDataRefused instead of writing into the evidence)."""
        key = str(artifact_id).strip()
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._path(evidence)
            store = self._load()
            self._writable()
            existing = store.get(key, {})
            entry = {
                "artifact_id": key,
                "star": bool(data.get("star", existing.get("star", False))),
                "note": str(data.get("note", existing.get("note", ""))),
                "analyst_id": str(data.get("analyst_id", existing.get("analyst_id", "examiner"))),
                "verdict": str(data.get("verdict", existing.get("verdict", "unreviewed"))),
                "marks": list(data.get("marks", existing.get("marks", []))),
                "created_at": existing.get("created_at", now),
                "updated_at": now,
            }
            # If everything is blank / unchecked, optionally retain or prune
            blank = not entry["star"] and not entry["note"] and not entry["marks"] and entry["verdict"] == "unreviewed"
            self._apply(store, key, None if blank else entry)
            return entry

    def delete_review(self, artifact_id: str, evidence: list[Path] | tuple[Path, ...] = ()) -> bool:
        """Delete review entry for artifact_id (R22-3: raises UserDataRefused like save_review)."""
        key = str(artifact_id).strip()
        with self._lock:
            self._path(evidence)
            store = self._load()
            if key in store:
                self._writable()
                self._apply(store, key, None)
                return True
            return False

    def list_reviews(self, evidence: list[Path] | tuple[Path, ...] = ()) -> dict[str, dict[str, Any]]:
        """Return a copy of all stored reviews (R22-3: none when the file has no place outside the evidence)."""
        with self._lock:
            try:
                self._path(evidence)
            except UserDataRefused:
                return {}
            return dict(self._load())


# Global default store singleton
_DEFAULT_STORE: ReviewStore | None = None
_STORE_LOCK = threading.Lock()


def get_default_review_store(storage_path: Path | str | None = None) -> ReviewStore:
    global _DEFAULT_STORE
    with _STORE_LOCK:
        if _DEFAULT_STORE is None or storage_path is not None:
            _DEFAULT_STORE = ReviewStore(storage_path)
        return _DEFAULT_STORE
