"""Persistent Review Marks & Examiner Audit Store for Deepfake Lens.

Provides persistent storage for examiner review marks, notes, verdicts, and
bounding-box/point annotations across scan sessions. Prevents examiner marks
from being lost or isolated in client-side browser localStorage.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_REVIEW_PATH = Path.home() / ".deepfake-lens" / "reviews.json"


class ReviewStore:
    """Thread-safe, file-backed review marks storage."""

    def __init__(self, storage_path: Path | str | None = None) -> None:
        self.storage_path = Path(storage_path) if storage_path else DEFAULT_REVIEW_PATH
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._cache is not None:
            return self._cache
        if not self.storage_path.is_file():
            self._cache = {}
            return self._cache
        try:
            content = self.storage_path.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict):
                self._cache = data
            else:
                self._cache = {}
        except (OSError, json.JSONDecodeError):
            self._cache = {}
        return self._cache

    def _persist(self) -> None:
        if self._cache is None:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.storage_path.with_suffix(".tmp")
        try:
            temp_path.write_text(
                json.dumps(self._cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(self.storage_path)
        except OSError:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    def get_review(self, artifact_id: str) -> dict[str, Any] | None:
        """Fetch review entry for artifact_id (or None if unreviewed)."""
        key = str(artifact_id).strip()
        with self._lock:
            store = self._load()
            return store.get(key)

    def save_review(self, artifact_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Save or update review entry for artifact_id."""
        key = str(artifact_id).strip()
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            store = self._load()
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
            if not entry["star"] and not entry["note"] and not entry["marks"] and entry["verdict"] == "unreviewed":
                store.pop(key, None)
            else:
                store[key] = entry
            self._persist()
            return entry

    def delete_review(self, artifact_id: str) -> bool:
        """Delete review entry for artifact_id."""
        key = str(artifact_id).strip()
        with self._lock:
            store = self._load()
            if key in store:
                del store[key]
                self._persist()
                return True
            return False

    def list_reviews(self) -> dict[str, dict[str, Any]]:
        """Return a copy of all stored reviews."""
        with self._lock:
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
