"""R22-3 (round 22): where the web GUI's examiner records live — one fixed per-user location, never the evidence.

The examiner's review marks (``reviews.json``, ``/api/review``) and feedback
labels (``feedback.jsonl``, ``/api/feedback``) were written to
``~/.deepfake-lens/``. With ``cd ~; deepfake-lens web`` (R21-10 lets that
run) HOME is the served evidence folder, so a saved review created
``~/.deepfake-lens/reviews.json`` inside it; with a relative HOME the file
went to ``<working folder>/rel/.deepfake-lens/`` — the evidence again
(2d053dd refused ``HOME=rel web`` with exit 2, so this was a regression),
and the start-up notice "증거 폴더에는 아무것도 쓰지 않습니다" was false.

User decision 2026-10-14: the records live in one fixed per-user
application-data folder (:func:`user_data_dir`):

- macOS ``~/Library/Application Support/deepfake-lens``;
- Windows ``%APPDATA%\\deepfake-lens``;
- elsewhere ``$XDG_DATA_HOME/deepfake-lens`` when that variable is absolute,
  else ``~/.local/share/deepfake-lens``;

only from an absolute HOME / APPDATA (a relative one names no place —
saving is refused, never resolved against the working folder). The
operator's ``DEEPFAKE_LENS_REVIEWS`` / ``DEEPFAKE_LENS_FEEDBACK`` (absolute
file paths; a relative one is refused) replace the file. When the file is
inside a folder the server examines (a read root, the default folder, a
folder the command examines), saving is refused with
:data:`USER_DATA_INSIDE_EVIDENCE` (HTTP 409) — nothing is written into the
evidence — and reading returns nothing from there.

Records under ``~/.deepfake-lens`` are migrated once (:func:`migrate_once`):
copied (a review merged by artifact, feedback lines appended) into the new
file, then a marker in the new folder records it; the old files are left
as they were, so nothing is lost and nothing is copied twice.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

USER_DATA_DIR_NAME = "deepfake-lens"
REVIEWS_FILE = "reviews.json"
FEEDBACK_FILE = "feedback.jsonl"
REVIEWS_ENV = "DEEPFAKE_LENS_REVIEWS"
FEEDBACK_ENV = "DEEPFAKE_LENS_FEEDBACK"
# The folder the records used to live in (under HOME), and the marker of the one-time migration.
LEGACY_DIR_NAME = ".deepfake-lens"
MIGRATION_MARKER = "migrated-from-home.json"

USER_DATA_NO_PLACE = (
    "검토·피드백 기록을 저장할 위치를 정할 수 없습니다(HOME 또는 APPDATA가 절대 경로가 아닙니다: 「{home}」) — "
    "저장하지 않았습니다(작업 폴더에 쓰지 않습니다). {env} 환경 변수에 절대 경로를 지정하십시오"
)
USER_DATA_ENV_RELATIVE = "환경 변수 {env}의 값이 절대 경로가 아닙니다: 「{value}」 — 저장하지 않았습니다(작업 폴더에 쓰지 않습니다)"
USER_DATA_INSIDE_EVIDENCE = (
    "검토·피드백 기록 파일 {path}이(가) 검사 대상 폴더({folder}) 안에 있어 저장하지 않았습니다 — 증거 폴더에는 쓰지 않습니다. "
    "{env} 환경 변수로 검사 대상 밖의 절대 경로를 지정하십시오"
)


class UserDataRefused(RuntimeError):
    """R22-3: the examiner-record file has no place outside the evidence (saving refused, HTTP 409)."""


def _home() -> Path | None:
    try:
        home = Path.home()
    except (RuntimeError, KeyError, OSError):
        return None
    return home if home.is_absolute() else None


def _platform() -> str:
    """"darwin", "windows" or "posix" (a function so tests can pick the platform)."""
    if sys.platform == "darwin":
        return "darwin"
    return "windows" if os.name == "nt" else "posix"


def user_data_dir() -> Path | None:
    """The fixed per-user application-data folder of the examiner records, or None when no absolute base exists."""
    platform = _platform()
    if platform == "darwin":
        home = _home()
        return home / "Library" / "Application Support" / USER_DATA_DIR_NAME if home is not None else None
    if platform == "windows":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / USER_DATA_DIR_NAME if appdata and os.path.isabs(appdata) else None
    xdg = os.environ.get("XDG_DATA_HOME", "")
    if xdg and os.path.isabs(xdg):
        return Path(xdg) / USER_DATA_DIR_NAME
    home = _home()
    return home / ".local" / "share" / USER_DATA_DIR_NAME if home is not None else None


def record_path(name: str, env: str) -> Path:
    """The file of one record kind (``reviews.json`` / ``feedback.jsonl``); raises :class:`UserDataRefused` when there is none."""
    override = os.environ.get(env, "")
    if override:
        if not os.path.isabs(override):
            raise UserDataRefused(USER_DATA_ENV_RELATIVE.format(env=env, value=override[:200]))
        return Path(override)
    folder = user_data_dir()
    if folder is None:
        home = os.environ.get("APPDATA" if _platform() == "windows" else "HOME", "")
        raise UserDataRefused(USER_DATA_NO_PLACE.format(home=home[:200], env=env))
    return folder / name


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def check_outside(path: Path, evidence: list[Path] | tuple[Path, ...], env: str) -> None:
    """Raise :class:`UserDataRefused` when ``path`` (or the folder that would hold it) is inside an examined folder."""
    from .native_path import examined_folders

    real = os.path.realpath(path)
    for folder in [*(os.path.realpath(item) for item in evidence), *examined_folders()]:
        if _inside(real, folder) or _inside(os.path.realpath(path.parent), folder):
            raise UserDataRefused(USER_DATA_INSIDE_EVIDENCE.format(path=path, folder=folder, env=env))


def legacy_path(name: str) -> Path | None:
    """``~/.deepfake-lens/<name>`` (where the records used to be), when HOME is absolute."""
    home = _home()
    return home / LEGACY_DIR_NAME / name if home is not None else None


def migrate_once(name: str, target: Path) -> None:
    """Copy ``~/.deepfake-lens/<name>`` into ``target`` once, then mark it in the target folder (never lost, never twice).

    reviews.json: entries merged by artifact id (one already in ``target``
    wins); feedback.jsonl: the old lines appended. The old file is not
    changed. A marker ``migrated-from-home.json`` beside ``target`` records
    the source, the time and the size; a failure leaves no marker (tried
    again next time).
    """
    from .output_files import write_output_text

    legacy = legacy_path(name)
    if legacy is None or not legacy.is_file():
        return
    try:
        if os.path.realpath(legacy) == os.path.realpath(target):
            return
    except OSError:
        return
    marker = target.parent / MIGRATION_MARKER
    try:
        marks = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {}
    except (OSError, ValueError):
        marks = {}
    if not isinstance(marks, dict):
        marks = {}
    if name in marks:
        return
    try:
        data = legacy.read_bytes()
        target.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".jsonl"):
            existing = target.read_bytes() if target.is_file() else b""
            joined = existing + (b"" if not existing or existing.endswith(b"\n") else b"\n") + data
            write_output_text(target, joined.decode("utf-8", "replace"))
        else:
            old = json.loads(data.decode("utf-8"))
            new = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
            merged = {**(old if isinstance(old, dict) else {}), **(new if isinstance(new, dict) else {})}
            write_output_text(target, json.dumps(merged, ensure_ascii=False, indent=2))
        marks[name] = {"from": str(legacy), "to": str(target), "bytes": len(data),
                       "at": datetime.now(timezone.utc).isoformat()}
        write_output_text(marker, json.dumps(marks, ensure_ascii=False, indent=2))
    except (OSError, ValueError, UnicodeError):
        logger.warning("examiner records in %s could not be migrated to %s", legacy, target, exc_info=True)
