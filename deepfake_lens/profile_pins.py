"""Model-profile pin inventory for cache keys and report signatures (G11, G30).

A runtime profile pins its weights with a ``pin`` object — ``{"sha256": …}``
for a local checkpoint, ``{"revision": …}`` for a hub model (phase 0, WP-C).
Two consumers need the same inventory:

- the scan cache keys every entry on it, so a cached verdict computed under
  different (or unpinned) weights is never replayed;
- signed reports carry it as ``model_pins`` so the signature covers which
  weights the tool was configured with.

A profile without a pin, or with only empty pin values, is reported as
unpinned (``pin: null`` / token ``unpinned:<name>``) — never silently
treated as pinned.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Union

PIN_FIELDS = ("sha256", "revision")
PROFILE_GLOB = "*-runtime.json"

ModelPathArg = Union[Path, str, list[Union[Path, str]], tuple[Union[Path, str], ...], None]


def _profile_name(path: Path) -> str:
    return path.name[: -len(".json")] if path.name.endswith(".json") else path.name


def _model_path_entries(model_path: ModelPathArg) -> list[Path]:
    if model_path is None:
        return []
    if isinstance(model_path, (list, tuple)):
        return [Path(entry) for entry in model_path if entry]
    return [Path(model_path)] if str(model_path) else []


def _profile_files(models_dir: Path | str | None, model_path: ModelPathArg) -> list[Path]:
    """Profiles in the models dir plus any profile/dir named by ``model_path``."""
    files: list[Path] = []
    if models_dir is None:
        from .vendor_weights import default_models_dir

        models_dir = default_models_dir()
    base = Path(models_dir)
    if base.is_dir():
        files.extend(sorted(base.glob(PROFILE_GLOB), key=str))
    for entry in _model_path_entries(model_path):
        if entry.is_dir():
            files.extend(sorted(entry.glob(PROFILE_GLOB), key=str))
        elif entry.suffix.lower() == ".json" and entry.is_file():
            files.append(entry)
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in files:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def _pin_of(profile: object) -> dict[str, str] | None:
    """The non-empty pin fields of a profile, or None when it is unpinned."""
    if not isinstance(profile, dict):
        return None
    pin = profile.get("pin")
    if not isinstance(pin, dict):
        return None
    values = {field: str(pin[field]).strip() for field in PIN_FIELDS if isinstance(pin.get(field), str) and str(pin[field]).strip()}
    return values or None


def profile_pins(models_dir: Path | str | None = None, model_path: ModelPathArg = None) -> list[dict[str, object]]:
    """``[{"profile": <name>, "pin": {...} | None}]`` sorted by profile name.

    Path-free on purpose: the inventory must be identical for the same
    weights wherever the models directory lives.
    """
    entries: dict[tuple[str, str], dict[str, object]] = {}
    for path in _profile_files(models_dir, model_path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            data = None
        name = _profile_name(path)
        pin = _pin_of(data)
        entries[(name, json.dumps(pin, sort_keys=True))] = {"profile": name, "pin": pin}
    return [entries[key] for key in sorted(entries)]


def pin_tokens(pins: Iterable[dict[str, object]]) -> list[str]:
    """Sorted string tokens — ``<name>:sha256=<hex>`` or ``unpinned:<name>``."""
    tokens: list[str] = []
    for entry in pins:
        name = str(entry.get("profile", ""))
        pin = entry.get("pin")
        if isinstance(pin, dict) and pin:
            tokens.append(name + ":" + ",".join(f"{key}={pin[key]}" for key in sorted(pin)))
        else:
            tokens.append(f"unpinned:{name}")
    return sorted(tokens)


def model_path_digest(model_path: ModelPathArg) -> str:
    """Content digest of the profiles ``model_path`` selects — no paths.

    Replaces the resolved-path marker the cache key used to carry: moving a
    models directory must not invalidate the cache, editing a profile must.
    """
    digests: list[str] = []
    for entry in _model_path_entries(model_path):
        targets = sorted(entry.glob("*.json"), key=str) if entry.is_dir() else [entry]
        for target in targets:
            try:
                digests.append(hashlib.sha256(target.read_bytes()).hexdigest())
            except OSError:
                digests.append(f"missing:{target.name}")
    return hashlib.sha256("\n".join(sorted(digests)).encode("utf-8")).hexdigest() if digests else ""
