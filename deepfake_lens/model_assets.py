"""Model assets that are not runtime profiles — pinned in ``models/assets.json`` (R15-3, round 15).

The phase-0 goal "모델 가중치는 sha256/revision 핀 없이는 로드되지 않는다"
held for the runtime profiles (``model_pins``) but not for the assets the
face and lip-sync layers load by path: the MediaPipe FaceLandmarker
``.task`` (``DEEPFAKE_LENS_FACE_LANDMARKER`` or ``models/``; any file was
handed to ``create_from_options`` and a failure swallowed), the SyncNet
weights ``syncnet_v2.model`` + ``sfd_face.pth`` (third-party ``torch.load``)
and the Haar cascade (``DEEPFAKE_LENS_HAAR_CASCADE`` could name any file).

Every such asset is registered in the asset manifest (schema
``model-assets-v1``: name, expected ``sha256``, ``source``, ``license``,
``used_by``). Before a load its bytes are read once and hashed:

- not registered, or registered with an empty ``sha256`` → refused,
  :class:`AssetPinError` "미고정 모델: <자산>";
- a digest other than the registered one → refused, "미고정 모델: <자산> —
  sha256 불일치 …";

and the loader is handed *the bytes that were verified* — a buffer
(FaceLandmarker ``model_asset_buffer``) or a private copy in the session
folder (Haar XML, SyncNet weights) — never the original path, so the file
cannot be swapped between the check and the load. The refusal reaches the
check that needed the asset, which is recorded ``failed``.

Where the manifest lives: ``<models dir>/assets.json`` when the effective
models directory has one (an operator's ``DEEPFAKE_LENS_MODELS_DIR``),
else the packaged ``deepfake_lens/models/assets.json``. ``deepfake-lens
vendor-weights pin-asset <자산>`` records the digest of a local copy the
operator trusts (in the models directory's manifest). The packaged
manifest pins the bundled Haar cascade; the downloadable assets ship with an
empty ``sha256`` (no official digest is published) — they load only after
an operator pins them.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .native_path import scratch_dir

ASSET_MANIFEST_NAME = "assets.json"
ASSET_MANIFEST_SCHEMA = "model-assets-v1"
PACKAGED_MANIFEST = Path(__file__).resolve().parent / "models" / ASSET_MANIFEST_NAME
# R15-3: the coverage reason prefix of a refused asset (phase-0 instruction wording).
UNPINNED_ASSET_PREFIX = "미고정 모델"
_HEX = frozenset("0123456789abcdef")
_CHUNK = 1 << 20

# Asset names (manifest keys) used by the loaders.
HAAR_FRONTALFACE = "haarcascade_frontalface_default.xml"
FACE_LANDMARKER = "face_landmarker.task"
SYNCNET_WEIGHTS = "syncnet_v2.model"
S3FD_WEIGHTS = "sfd_face.pth"


class AssetPinError(RuntimeError):
    """R15-3: a model asset that is not pinned, or whose bytes do not match its pin (``str()`` is Korean)."""

    # R16-2: "기대 <12 hex>…, 실제 <12 hex>…" of a digest mismatch, None when the asset is not pinned.
    mismatch: str | None = None

    def __init__(self, asset: str, detail: str = "", *, mismatch: str | None = None) -> None:
        self.asset = asset
        self.mismatch = mismatch
        super().__init__(f"{UNPINNED_ASSET_PREFIX}: {asset}" + (f" — {detail}" if detail else ""))


def _mismatch_error(asset: str, expected: str, actual: str) -> AssetPinError:
    mismatch = f"기대 {expected[:12]}…, 실제 {actual[:12]}…"
    return AssetPinError(asset, f"sha256 불일치({mismatch})", mismatch=mismatch)


def sha256_mismatch_detail(exc: AssetPinError) -> str | None:
    """R16-2: the "기대 …, 실제 …" part of a digest-mismatch refusal, None for an unpinned asset."""
    return exc.mismatch


def _models_dir() -> Path:
    from .vendor_weights import default_models_dir

    return default_models_dir()


def manifest_path(models_dir: Path | str | None = None) -> Path:
    """The manifest that governs ``models_dir`` (default: the effective models directory)."""
    folder = Path(models_dir) if models_dir is not None else _models_dir()
    candidate = folder / ASSET_MANIFEST_NAME
    return candidate if candidate.is_file() else PACKAGED_MANIFEST


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def manifest_state(path: Path | str | None = None) -> tuple[str, int, int, int, int]:
    """R16-3: ``(path, mtime_ns, ctime_ns, size, inode)`` of the governing manifest (-1s when it cannot be read).

    Part of every cache key of verified asset bytes, and what
    :func:`load_manifest` re-reads the manifest on.
    """
    target = Path(path) if path is not None else manifest_path()
    try:
        info = target.stat()
    except OSError:
        return (str(target), -1, -1, -1, -1)
    return (str(target), info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_ino)


# R16-3: the parsed manifest per path, with the file state it was parsed from
# (R17-9: and why it pins nothing, None when it parsed).
_MANIFESTS: dict[str, tuple[tuple[str, int, int, int, int], dict[str, dict[str, Any]], str | None]] = {}


def load_manifest(path: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """``{name: entry}`` of the manifest at ``path`` (default :func:`manifest_path`).

    A manifest that cannot be read or has another schema pins nothing —
    every asset is then refused (fail-closed), never loaded unverified.
    R16-3 (round 16): consulted on every use and re-parsed whenever the
    file's state (:func:`manifest_state`) changed — a re-pin or a removed
    pin takes effect in a running process.
    """
    return _loaded(path)[0]


def manifest_error(path: Path | str | None = None) -> str | None:
    """R17-9 (round 17): why the governing manifest pins nothing (Korean), None when it parsed.

    doctor shows it — a broken manifest used to leave doctor's asset section
    empty while every face check said "미고정 모델".
    """
    return _loaded(path)[1]


def _loaded(path: Path | str | None) -> tuple[dict[str, dict[str, Any]], str | None]:
    target = Path(path) if path is not None else manifest_path()
    state = manifest_state(target)
    cached = _MANIFESTS.get(str(target))
    if cached is not None and cached[0] == state and state[1] != -1:
        return cached[1], cached[2]
    entries, error = _parse_manifest(target)
    _MANIFESTS[str(target)] = (state, entries, error)
    return entries, error


def _parse_manifest(target: Path) -> tuple[dict[str, dict[str, Any]], str | None]:
    try:
        # R17-9: a UTF-8 BOM (Windows Notepad) is accepted — scan, doctor and pin-asset read alike.
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        from .error_text import read_error_ko

        return {}, f"자산 매니페스트를 읽을 수 없습니다: {read_error_ko(exc)}"
    except (ValueError, UnicodeError) as exc:
        from .error_text import read_error_ko

        return {}, f"자산 매니페스트가 올바른 JSON이 아닙니다: {read_error_ko(exc)}"
    if not isinstance(payload, dict) or payload.get("schema") != ASSET_MANIFEST_SCHEMA:
        return {}, f"자산 매니페스트 형식이 {ASSET_MANIFEST_SCHEMA}가 아닙니다"
    assets = payload.get("assets")
    if not isinstance(assets, list):
        return {}, "자산 매니페스트에 assets 목록이 없습니다"
    out: dict[str, dict[str, Any]] = {}
    for entry in assets:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            out[entry["name"]] = entry
    return out, None


def expected_sha256(asset: str, manifest: dict[str, dict[str, Any]] | None = None) -> str | None:
    """The pinned digest of ``asset``, or None when it is not registered or not pinned."""
    entries = load_manifest() if manifest is None else manifest
    value = (entries.get(asset) or {}).get("sha256")
    return value.lower() if isinstance(value, str) and _is_sha256(value.lower()) else None


def check_bytes(asset: str, data: bytes, manifest: dict[str, dict[str, Any]] | None = None, *, expected: str | None = None) -> None:
    """Raise :class:`AssetPinError` unless ``data`` is exactly the pinned ``asset``.

    ``expected`` (R16-3): check against this digest — the pin a caller keyed
    its cache on — instead of reading the manifest again.
    """
    if expected is None:
        expected = expected_sha256(asset, manifest)
    if expected is None:
        raise AssetPinError(asset)
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise _mismatch_error(asset, expected, actual)


def verified_bytes(asset: str, path: Path | str, *, expected: str | None = None) -> bytes:
    """The bytes of ``path`` after they were checked against the pin of ``asset`` (read once; ``expected`` see :func:`check_bytes`)."""
    with open(path, "rb") as handle:
        data = handle.read()
    check_bytes(asset, data, expected=expected)
    return data


def verified_copy(asset: str, path: Path | str, *, expected: str | None = None) -> str:
    """A private copy of ``path`` in the session folder holding exactly the verified bytes.

    For loaders that only take a file name (OpenCV, third-party
    ``torch.load``). The caller removes it when the loader is done (or the
    session cleanup does). The copy keeps the original's extension.
    """
    if expected is None:
        expected = expected_sha256(asset)
    if expected is None:
        raise AssetPinError(asset)
    digest = hashlib.sha256()
    suffix = Path(os.fspath(path)).suffix if Path(os.fspath(path)).suffix.isascii() else ""
    fd, copy = tempfile.mkstemp(prefix="dfl-asset-", suffix=suffix, dir=scratch_dir())
    try:
        with open(path, "rb") as source, os.fdopen(fd, "wb") as target:
            for chunk in iter(lambda: source.read(_CHUNK), b""):
                digest.update(chunk)
                target.write(chunk)
        actual = digest.hexdigest()
        if actual != expected:
            raise _mismatch_error(asset, expected, actual)
    except BaseException:
        Path(copy).unlink(missing_ok=True)
        raise
    return copy


def asset_pin_tokens(models_dir: Path | str | None = None) -> list[str]:
    """``name:sha256`` / ``unpinned:name`` for every registered asset (scan-cache context, R15-3)."""
    entries = load_manifest(manifest_path(models_dir))
    tokens = []
    for name in sorted(entries):
        digest = expected_sha256(name, entries)
        tokens.append(f"asset:{name}:{digest}" if digest else f"asset-unpinned:{name}")
    return tokens


# R17-7: sha256 of asset files keyed on their stat — each file hashed once per change, not per scan.
_FILE_DIGESTS: dict[tuple[str, int, int, int, int], str] = {}


def _loader_files(asset: str) -> list[Path]:
    """R17-7: the files a loader may read for ``asset`` without an override, in its order.

    Haar: OpenCV's own copy, then the bundled one (``face.haar_cascade_candidates``);
    the others: ``<effective models dir>/<asset>`` (FaceLandmarker, SyncNet).
    """
    if asset == HAAR_FRONTALFACE:
        files: list[Path] = []
        try:
            import cv2

            data_dir = getattr(getattr(cv2, "data", None), "haarcascades", "") or ""
            if data_dir:
                files.append(Path(data_dir + HAAR_FRONTALFACE))
        except ImportError:
            pass
        files.append(PACKAGED_MANIFEST.parent / HAAR_FRONTALFACE)
        return files
    return [_models_dir() / asset]


def _file_token(path: Path) -> str:
    try:
        info = path.stat()
    except OSError:
        return f"{path}:absent"
    if not path.is_file():
        return f"{path}:not-a-file"
    key = (str(path), info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino)
    digest = _FILE_DIGESTS.get(key)
    if digest is None:
        try:
            digest = _file_sha256(path)
        except OSError:
            return f"{path}:unreadable"
        _FILE_DIGESTS[key] = digest
    return f"{path}:{digest}"


def asset_file_tokens() -> list[str]:
    """R17-7 (round 17): what the loaders would read for every known asset — part of the scan-cache key.

    The key held the pins only, so a failed check caused by the files
    themselves (a ``DEEPFAKE_LENS_HAAR_CASCADE`` with other bytes: "미고정
    모델: 재정의 cascade sha256 불일치") was replayed after the override was
    fixed, and the reverse. Each token names the override variable's value
    (as :func:`asset_override` reads it) or the loader's default files, with
    each file's sha256 (``absent`` when there is none).
    """
    tokens: list[str] = []
    for asset in KNOWN_ASSETS:
        override = asset_override(asset)
        if override is not None:
            tokens.append(f"asset-file:{asset}:override:{_file_token(override)}")
        else:
            tokens.append(f"asset-file:{asset}:" + "|".join(_file_token(path) for path in _loader_files(asset)))
    return tokens


# R16-9: the environment variable that replaces an asset's file (what the loaders read).
ASSET_OVERRIDE_ENV = {HAAR_FRONTALFACE: "DEEPFAKE_LENS_HAAR_CASCADE", FACE_LANDMARKER: "DEEPFAKE_LENS_FACE_LANDMARKER"}
# R16-9: asset_status "state" values (R17-9: and the manifest row's).
ASSET_OK = "ok"
ASSET_UNPINNED = "unpinned"
ASSET_MISMATCH = "mismatch"
ASSET_ABSENT = "absent"
ASSET_MANIFEST_ERROR = "manifest_error"
# R17-9: the assets the loaders know — listed by doctor even when the manifest cannot be read.
KNOWN_ASSETS = (HAAR_FRONTALFACE, FACE_LANDMARKER, SYNCNET_WEIGHTS, S3FD_WEIGHTS)


def asset_override(asset: str) -> Path | None:
    """R17-9 (round 17): the file an override variable names for ``asset`` — None when unset.

    The one reading of ``DEEPFAKE_LENS_HAAR_CASCADE`` /
    ``DEEPFAKE_LENS_FACE_LANDMARKER`` for the loaders, doctor and the scan
    cache: surrounding blanks stripped, ``~`` expanded (doctor expanded it
    and said ok while the scan said "재정의 cascade 파일이 없습니다").
    """
    env = ASSET_OVERRIDE_ENV.get(asset)
    if env is None:
        return None
    value = os.environ.get(env, "").strip()
    return Path(value).expanduser() if value else None


def asset_status(models_dir: Path | str | None = None) -> list[dict[str, object]]:
    """Per registered asset: pinned, present, digest matches — and (R16-9) its pin state for doctor.

    The file checked is the one the loader reads: the override variable's
    file when it is set (``DEEPFAKE_LENS_HAAR_CASCADE`` /
    ``DEEPFAKE_LENS_FACE_LANDMARKER``), else ``<models dir>/<asset>`` (the
    bundled cascade for the Haar XML). ``state``: ``ok`` (pinned, present,
    digest matches), ``mismatch`` (pinned, other bytes — refused),
    ``unpinned`` (no pin — refused if present), ``absent`` (pinned, no file);
    ``detail`` says it in Korean.
    """
    folder = Path(models_dir) if models_dir is not None else _models_dir()
    governing = manifest_path(folder)
    entries = load_manifest(governing)
    rows: list[dict[str, object]] = []
    error = manifest_error(governing)
    names = sorted(entries)
    if error is not None:
        # R17-9 (round 17): the section was empty for a broken manifest.
        rows.append({
            "asset": ASSET_MANIFEST_NAME, "pinned": False, "present": governing.is_file(), "sha256_ok": None, "path": str(governing),
            "override": None, "state": ASSET_MANIFEST_ERROR,
            "detail": f"{error} — 모든 자산이 핀 없음으로 로드 거부됨(쓰는 검사는 failed: 미고정 모델)", "used_by": "",
        })
        names = sorted(KNOWN_ASSETS)
    for name in names:
        expected = expected_sha256(name, entries)
        override_path = asset_override(name)
        override = str(override_path) if override_path is not None else ""
        candidate = override_path if override_path is not None else folder / name
        if not override and not candidate.is_file() and name == HAAR_FRONTALFACE:
            candidate = PACKAGED_MANIFEST.parent / name
        present = candidate.is_file()
        matches: bool | None = None
        if present and expected:
            matches = _file_sha256(candidate) == expected
        if expected is None:
            state = ASSET_UNPINNED
            detail = (
                "핀 없음 — 파일이 있지만 로드 거부(쓰는 검사는 failed: 미고정 모델); `vendor-weights pin-asset`으로 고정"
                if present else "핀 없음, 파일 없음 — 내려받아 `vendor-weights pin-asset`으로 고정하기 전에는 쓰지 않음"
            )
        elif not present:
            state = ASSET_ABSENT
            detail = "고정됨, 파일 없음 — 이 자산을 쓰는 경로는 실행되지 않음"
        elif matches:
            state = ASSET_OK
            detail = f"고정됨, sha256 일치({expected[:12]}…)"
        else:
            state = ASSET_MISMATCH
            detail = f"고정됨, sha256 불일치 — 로드 거부(쓰는 검사는 failed: 미고정 모델; 기대 {expected[:12]}…)"
        rows.append({
            "asset": name, "pinned": expected is not None, "present": present, "sha256_ok": matches, "path": str(candidate),
            "override": ASSET_OVERRIDE_ENV.get(name) if override else None, "state": state, "detail": detail,
            "used_by": str((entries.get(name) or {}).get("used_by") or ""),
        })
    return rows


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pin_asset(asset: str, file: Path | str | None = None, models_dir: Path | str | None = None) -> dict[str, object]:
    """``vendor-weights pin-asset``: record the digest of a local copy of ``asset`` in the models directory's manifest.

    The asset must be registered (in the governing manifest) — a name the
    loaders do not know is refused. The manifest written is
    ``<models dir>/assets.json`` (copied from the packaged one first when the
    directory has none).
    """
    folder = Path(models_dir) if models_dir is not None else _models_dir()
    if not folder.is_dir():
        # R16-8 (round 16): said "[Errno 2] No such file or directory: '<폴더>/assets.json.tmp'".
        raise ValueError(f"모델 폴더가 없습니다: {folder}" if not folder.exists() else f"모델 폴더가 아니라 파일입니다: {folder}")
    governing = manifest_path(folder)
    try:
        payload = json.loads(governing.read_text(encoding="utf-8-sig"))  # R17-9: BOM accepted as by the loaders
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError(f"자산 매니페스트를 읽을 수 없습니다: {governing}") from exc
    entries = payload.get("assets") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or payload.get("schema") != ASSET_MANIFEST_SCHEMA:
        raise ValueError(f"자산 매니페스트 형식이 {ASSET_MANIFEST_SCHEMA}가 아닙니다: {governing}")
    entry = next((item for item in entries if isinstance(item, dict) and item.get("name") == asset), None)
    if entry is None:
        known = ", ".join(sorted(str(item.get("name")) for item in entries if isinstance(item, dict)))
        raise ValueError(f"등록되지 않은 자산입니다: {asset} (등록된 자산: {known})")
    source = Path(file) if file is not None else folder / asset
    if not source.is_file():
        raise ValueError(f"자산 파일이 없습니다: {source}")
    digest = _file_sha256(source)
    entry["sha256"] = digest
    target = folder / ASSET_MANIFEST_NAME
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = ""
    try:
        fd, tmp = tempfile.mkstemp(prefix=".assets-", suffix=".json", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, target)
    except OSError as exc:
        if tmp:
            Path(tmp).unlink(missing_ok=True)
        # R16-8: Korean, and the manifest's name — never the internal temp file's.
        from .error_text import read_error_ko

        raise ValueError(f"자산 매니페스트를 쓸 수 없습니다: {target} — {read_error_ko(exc)}") from exc
    return {"asset": asset, "sha256": digest, "file": str(source), "manifest": str(target)}


__all__ = [
    "ASSET_ABSENT",
    "ASSET_MANIFEST_NAME",
    "ASSET_MISMATCH",
    "ASSET_OK",
    "ASSET_OVERRIDE_ENV",
    "ASSET_UNPINNED",
    "ASSET_MANIFEST_SCHEMA",
    "ASSET_MANIFEST_ERROR",
    "KNOWN_ASSETS",
    "asset_file_tokens",
    "asset_override",
    "manifest_error",
    "AssetPinError",
    "FACE_LANDMARKER",
    "HAAR_FRONTALFACE",
    "PACKAGED_MANIFEST",
    "S3FD_WEIGHTS",
    "SYNCNET_WEIGHTS",
    "UNPINNED_ASSET_PREFIX",
    "asset_pin_tokens",
    "asset_status",
    "check_bytes",
    "expected_sha256",
    "load_manifest",
    "manifest_path",
    "manifest_state",
    "pin_asset",
    "sha256_mismatch_detail",
    "verified_bytes",
    "verified_copy",
]
