"""Request-payload handlers for the built-in web server.

``webapp.run_server`` owns the HTTP handler and auth; this module owns the
/api/* payload construction, upload parsing, scan-job registry, and the
read-root guard so each file stays a single readable layer.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import tempfile
import threading
import time
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs

from .checks import failure_reason
from .vendor_weights import default_models_dir

logger = logging.getLogger(__name__)


def _layer_error(payload: dict[str, Any], layer: str, exc: BaseException) -> None:
    """Record a failed auxiliary layer in the response instead of hiding it."""
    errors = payload.setdefault("layer_errors", [])
    errors.append({"layer": layer, "status": "failed", "reason": failure_reason(exc)})
from .core import SCAN_JSON_SCHEMA_VERSION, BatchScanSummary, DEFAULT_METADATA_BYTES, _scan_item_from_json, analyze_file, scan_directory, scan_to_json, summarize
from .datasets import is_negative_label, is_positive_label
from .fusion import apply_fusion_to_items, load_fusion_profile
from .reports import write_html_report


MAX_SCAN_FILES = 2000
MAX_FILE_BYTES_CEILING = 1024 * 1024 * 1024
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_UPLOAD_FILES = 20
DEFAULT_PROFILE_NAMES = (
    "aide-runtime.json",
    "aasist-runtime.json",
    "wav2vec-deepfake-audio-runtime.json",
    "openai-detector-runtime.json",
)

# Server-level models directory override — set by run_server(--models-dir)
# or DEEPFAKE_LENS_MODELS_DIR so every scan/check/coverage call resolves the
# same weight set an administrator provisioned.
_MODELS_DIR: Path | None = None


def set_models_dir(path: Path | None) -> None:
    global _MODELS_DIR
    _MODELS_DIR = Path(path).expanduser().resolve() if path else None


def _models_dir() -> Path | None:
    return _MODELS_DIR or default_models_dir()


def default_engine_profiles(root: Path | None = None) -> list[Path]:
    """Bundled default-engine profiles that exist on disk.

    Mirrors the CLI defaults (image/audio/text) so the web scan uses the
    neural adapters automatically when profiles are committed. Missing
    profiles are skipped and each adapter degrades gracefully when its
    checkpoint is absent.
    """
    base = Path(root) if root is not None else _models_dir()
    if base is None:
        return []
    return [base / name for name in DEFAULT_PROFILE_NAMES if (base / name).is_file()]



def _load_gui() -> str:
    """Load the GUI HTML file."""
    gui_path = Path(__file__).parent / "gui.html"
    if gui_path.exists():
        return gui_path.read_text(encoding="utf-8")
    return "<h1>GUI 파일을 찾을 수 없습니다</h1>"


def _scan_payload(query: str, *, default_folder: Path | None, should_stop: Callable[[], bool] | None = None) -> dict[str, object]:
    """Handle scan request."""
    params = parse_qs(query)
    folder = Path(params.get("folder", [str(default_folder or ".")])[0]).expanduser()
    _register_read_root(folder)
    pixel = params.get("pixel", ["off"])[0]
    recursive = params.get("recursive", ["false"])[0].lower() in {"1", "true", "yes"}
    try:
        max_files = int(params.get("max_files", ["500"])[0])
    except ValueError as exc:
        raise ValueError("max_files must be an integer") from exc
    max_files = max(1, min(max_files, MAX_SCAN_FILES))
    max_file_bytes_raw = params.get("max_file_bytes", [None])[0]
    max_file_bytes: int | None = None
    if max_file_bytes_raw is not None:
        try:
            max_file_bytes = min(int(max_file_bytes_raw), MAX_FILE_BYTES_CEILING)
        except ValueError as exc:
            raise ValueError("max_file_bytes must be an integer") from exc
    dedupe = params.get("dedupe", ["false"])[0].lower() in {"1", "true", "yes"}
    heatmaps = params.get("heatmaps", ["false"])[0].lower() in {"1", "true", "yes"}
    deep_signals = params.get("deep_signals", ["false"])[0].lower() in {"1", "true", "yes"}
    model_path_raw = params.get("model_path", [""])[0]
    no_default_engine = params.get("no_default_engine", ["false"])[0].lower() in {"1", "true", "yes"}
    model_path: Path | list[Path] | None
    if model_path_raw.strip():
        model_path = _optional_path(model_path_raw)
    elif no_default_engine:
        model_path = None
    else:
        model_path = default_engine_profiles() or None
    fusion_profile = load_fusion_profile(_optional_path(params.get("fusion_profile", [""])[0]))
    
    try:
        summary, items = scan_directory(
            folder,
            recursive=recursive,
            max_files=max_files,
            pixel_mode=pixel,
            heatmaps=heatmaps and pixel == "deep",
            max_file_bytes=max_file_bytes,
            dedupe=dedupe,
            model_path=model_path,
            deep_signals=deep_signals,
            should_stop=should_stop,
        )
        if fusion_profile:
            items = apply_fusion_to_items(items, fusion_profile)
            summary = summarize(items, capped=summary.capped, cached=summary.cached)
        return scan_to_json(summary, items)
    except (OSError, ValueError) as exc:
        return {"error": str(exc)}


# In-memory scan-job registry for /api/scan?async=1. ThreadingHTTPServer
# already runs each request on its own thread, so the job model exists for
# progress polling and to keep long scans from holding a client connection —
# not for concurrency. Entries expire; nothing is persisted.
_SCAN_JOBS: dict[str, dict[str, Any]] = {}
_SCAN_JOBS_LOCK = threading.Lock()
_SCAN_JOB_TTL_SECONDS = 15 * 60
_SCAN_JOB_MAX = 32


def _scan_job_evict(now: float) -> None:
    """Drop finished jobs past the TTL (called with the lock held)."""
    for key in [key for key, entry in _SCAN_JOBS.items() if now - entry.get("finished", entry["created"]) > _SCAN_JOB_TTL_SECONDS]:
        _SCAN_JOBS.pop(key, None)


def _scan_job_start(query: str, *, default_folder: Path | None) -> dict[str, object]:
    """Start a background scan job; poll /api/scan-status?job=<id>."""
    with _SCAN_JOBS_LOCK:
        _scan_job_evict(time.time())
        if len(_SCAN_JOBS) >= _SCAN_JOB_MAX:
            raise ValueError("too many scan jobs in flight; retry after a running job finishes")
        job_id = secrets.token_hex(8)
        cancel = threading.Event()
        _SCAN_JOBS[job_id] = {"status": "running", "created": time.time(), "cancel": cancel}

    def work() -> None:
        try:
            result = _scan_payload(query, default_folder=default_folder, should_stop=cancel.is_set)
            status = "done"
        except Exception as exc:  # noqa: BLE001 - a worker crash must not kill the job silently
            result = {"error": str(exc)}
            status = "error"
        with _SCAN_JOBS_LOCK:
            entry = _SCAN_JOBS.get(job_id)
            if entry is not None:
                entry.update(status=status, result=result, finished=time.time())

    threading.Thread(target=work, daemon=True, name=f"scan-job-{job_id}").start()
    return {"job_id": job_id, "status": "running"}


def _scan_status_payload(query: str) -> dict[str, object]:
    params = parse_qs(query)
    job_id = params.get("job", [""])[0].strip()
    if not job_id:
        return {"error": "missing job parameter"}
    with _SCAN_JOBS_LOCK:
        _scan_job_evict(time.time())
        entry = _SCAN_JOBS.get(job_id)
        if entry is None:
            return {"error": "unknown or expired job"}
        payload: dict[str, object] = {"job_id": job_id, "status": entry["status"]}
        if entry["status"] != "running":
            payload["result"] = entry.get("result")
        return payload


def _scan_cancel_payload(query: str) -> dict[str, object]:
    params = parse_qs(query)
    job_id = params.get("job", [""])[0].strip()
    if not job_id:
        return {"error": "missing job parameter"}
    with _SCAN_JOBS_LOCK:
        entry = _SCAN_JOBS.get(job_id)
        if entry is None:
            return {"error": "unknown or expired job"}
        if entry["status"] != "running":
            return {"job_id": job_id, "status": entry["status"], "cancelled": False}
        cancel = entry.get("cancel")
        if isinstance(cancel, threading.Event):
            cancel.set()
        return {"job_id": job_id, "status": "cancelling", "cancelled": True}


def _analyze_file_payload(query: str) -> dict[str, object]:
    """Handle single file analysis request."""
    params = parse_qs(query)
    file_path = params.get("file", [""])[0]
    
    if not file_path:
        return {"error": "파일 경로가 없습니다"}
    
    try:
        from .classifier import classify_metadata
        from .c2pa import analyze_metadata_forensic
        from .pixel_analyzer import analyze_pixels
        
        path = Path(file_path).expanduser()
        with _READ_ROOTS_LOCK:
            roots_registered = bool(_READ_ROOTS)
        if roots_registered and not _read_root_allows(path):
            return {"error": "허용되지 않은 경로입니다 — 먼저 해당 폴더를 스캔/등록하세요.", "detail": "path not under a registered read root"}
        if not path.exists():
            return {"error": f"파일이 존재하지 않습니다: {file_path}"}
        
        # Extract metadata
        metadata = _extract_metadata(path)
        
        # Classify
        classification = classify_metadata(metadata)
        
        # Forensic analysis
        forensic = analyze_metadata_forensic(path)
        
        # Pixel analysis (if image)
        pixel_result = None
        response: dict[str, Any] = {}
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}:
            try:
                pixel_result = analyze_pixels(path)
            except Exception as exc:
                logger.exception("pixel layer failed for %s", path)
                _layer_error(response, "pixel_analysis", exc)

        response.update({
            "file": str(path),
            "classification": classification.to_json(),
            "forensic": forensic.to_json(),
            "pixel_analysis": pixel_result.to_json() if pixel_result else None,
        })
        return response
    except Exception as exc:
        logger.exception("file analysis failed")
        # Detail stays in `detail` so the GUI shows a clean headline instead
        # of a raw exception sentence; the type/message aid local debugging.
        return {"error": "파일 분석 중 오류가 발생했습니다", "detail": f"{type(exc).__name__}: {exc}"}


def _stats_payload() -> dict[str, object]:
    """Handle stats request with real package facts."""
    import importlib.metadata

    try:
        version = importlib.metadata.version("deepfake-lens")
    except importlib.metadata.PackageNotFoundError:
        version = "dev"
    package_dir = Path(__file__).parent
    module_count = sum(1 for entry in package_dir.glob("*.py") if not entry.name.startswith("_"))
    return {
        "status": "ok",
        "version": version,
        "modules": module_count,
    }


def _extract_metadata(path: Path) -> dict[str, str]:
    """Extract metadata from file.

    Reads at most ``DEFAULT_METADATA_BYTES`` — the PNG text/iTXt chunks this
    parses live in the header region anyway, and an unbounded read of a
    user-supplied path is a memory-exhaustion vector.
    """
    import struct

    metadata: dict[str, str] = {}
    try:
        if path.stat().st_size > DEFAULT_METADATA_BYTES:
            return metadata
        data = path.read_bytes()
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            offset = 8
            while offset + 8 <= len(data):
                length = struct.unpack(">I", data[offset:offset+4])[0]
                chunk_type = data[offset+4:offset+8]
                if chunk_type in (b"tEXt", b"iTXt"):
                    chunk_data = data[offset+8:offset+8+length]
                    if b"\x00" in chunk_data:
                        key, value = chunk_data.split(b"\x00", 1)
                        metadata[key.decode("latin-1", errors="ignore")] = value.decode("utf-8", errors="ignore")
                offset += 12 + length
                if chunk_type == b"IEND":
                    break
    except (OSError, struct.error, ValueError) as exc:
        # Truncated/corrupt header: keep the chunks parsed so far, but log
        # it — this helper only feeds the legacy /api/analyze classifier.
        logger.warning("metadata extraction stopped early for %s: %s", path, failure_reason(exc))
    return metadata


def _optional_path(value: str) -> Path | None:
    value = value.strip()
    return Path(value).expanduser() if value else None


# Server-side read roots for /api/heatmap and /api/preview. The `root`
# parameter is kept for backward compatibility but is no longer trusted:
# a path is only served when it lives under a directory the server itself
# registered via /api/scan (or an explicit allow-root registration), so a
# caller cannot widen the read scope by passing root=C:\.
_READ_ROOTS_LOCK = threading.Lock()
_READ_ROOTS: set[Path] = set()
_READ_ROOTS_MAX = 64


def _register_read_root(folder: Path) -> None:
    resolved = folder.expanduser().resolve()
    with _READ_ROOTS_LOCK:
        if resolved in _READ_ROOTS:
            return
        if len(_READ_ROOTS) >= _READ_ROOTS_MAX:
            _READ_ROOTS.pop()
        _READ_ROOTS.add(resolved)


def _read_root_allows(path: Path, root_value: str = "") -> bool:
    """True when `path` sits under a server-registered root.

    The caller-supplied root is also checked when present so a stale or
    mismatched root argument cannot broaden access beyond the registered
    set — both conditions must hold.
    """
    with _READ_ROOTS_LOCK:
        roots = set(_READ_ROOTS)
    if root_value:
        try:
            root = Path(root_value).expanduser().resolve()
        except OSError:
            return False
        if not _is_within(path, root):
            return False
    return any(_is_within(path, root) for root in roots)


def _heatmap_payload(query: str) -> tuple[int, bytes, str]:
    params = parse_qs(query)
    path_value = params.get("path", [""])[0]
    root_value = params.get("root", [""])[0]
    if not path_value:
        return 400, b"missing path", "missing"
    path = Path(path_value).expanduser().resolve()
    if path.suffix.lower() != ".png" or not _read_root_allows(path, root_value):
        return 403, b"forbidden", "forbidden"
    try:
        data = path.read_bytes()
    except OSError:
        return 404, b"not found", "not-found"
    return 200, data, ""


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# Media the result viewer may inline-preview. Deliberately excludes
# HTML/SVG and documents — preview is for media inspection, never for
# rendering active content inside the app.
_PREVIEW_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".m4v": "video/mp4",
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    ".ogg": "audio/ogg", ".flac": "audio/flac", ".aac": "audio/aac",
    ".opus": "audio/opus",
}
MAX_PREVIEW_BYTES = 128 * 1024 * 1024


def _preview_payload(query: str) -> tuple[int, bytes, str, str]:
    """Serve a scanned media file for inline preview in the result viewer.

    Same trust model as /api/heatmap: the file must live under a
    server-registered scan root (see _read_root_allows), must be a known
    media type, and is served with nosniff so it can only render as media.
    """
    params = parse_qs(query)
    path_value = params.get("path", [""])[0]
    root_value = params.get("root", [""])[0]
    if not path_value:
        return 400, b"missing path", "missing", ""
    path = Path(path_value).expanduser().resolve()
    mime = _PREVIEW_MIME.get(path.suffix.lower())
    if mime is None or not _read_root_allows(path, root_value):
        return 403, b"forbidden", "forbidden", ""
    try:
        if path.stat().st_size > MAX_PREVIEW_BYTES:
            return 413, b"too large", "too-large", ""
        data = path.read_bytes()
    except OSError:
        return 404, b"not found", "not-found", ""
    return 200, data, "", mime


def _provenance() -> dict[str, object]:
    """Coverage + threshold provenance attached to every scan-shaped payload.

    A response that omits these lets a heuristic-only run read as a full
    neural one downstream (GUI banner, /api/report, exported JSON).
    """
    from .vendor_weights import weights_coverage
    cov: dict[str, object]
    try:
        cov = dict(weights_coverage(_models_dir()))
    except Exception as exc:
        logger.exception("weights coverage unavailable")
        cov = {"error": failure_reason(exc)}
    return {"coverage": cov, "thresholds": {"source": "builtin_defaults", "provisional": True}}


def _model_active(item: dict[str, object]) -> bool:
    result = item.get("result")
    if not isinstance(result, dict):
        return False
    ma = result.get("model_analysis")
    return isinstance(ma, dict) and bool(ma.get("available"))


def _part_bytes(part) -> bytes | None:
    """Normalize a multipart payload to bytes (email API may return str)."""
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8", errors="replace")
    return None


def _summarize_records(items: list[dict[str, object]], source: str) -> dict[str, object]:
    """Mirror core.summarize() — identical inputs must yield identical counts
    across CLI JSON, web uploads, and report headers. A row counts as
    analyzed only when it has status "analyzed" AND a result; failed,
    unsupported, and unknown-container rows land in unsupported_or_failed.
    """
    def _status(item: dict[str, object]) -> str:
        return str(item.get("status") or ("failed" if item.get("error") else "analyzed"))

    def _band(item: dict[str, object]) -> str:
        result = item.get("result")
        return str(result.get("band")) if isinstance(result, dict) else ""

    def _verdict(item: dict[str, object]) -> str:
        result = item.get("result")
        return str(result.get("verdict_code") or "undetermined") if isinstance(result, dict) else ""

    def _has_failed_check(item: dict[str, object]) -> bool:
        result = item.get("result")
        coverage = result.get("coverage") if isinstance(result, dict) else None
        return isinstance(coverage, list) and any(isinstance(e, dict) and e.get("status") == "failed" for e in coverage)

    analyzed = [i for i in items if _status(i) == "analyzed" and isinstance(i.get("result"), dict)]
    high = sum(1 for item in analyzed if _band(item) == "high")
    medium = sum(1 for item in analyzed if _band(item) == "medium")
    low = sum(1 for item in analyzed if _band(item) == "low")
    return {
        "manipulation_evidence": sum(1 for item in analyzed if _verdict(item) == "manipulation_evidence"),
        "authenticity_evidence": sum(1 for item in analyzed if _verdict(item) == "authenticity_evidence"),
        "undetermined": sum(1 for item in analyzed if _verdict(item) == "undetermined"),
        "checks_failed": sum(1 for item in analyzed if _has_failed_check(item)),
        "total": len(items),
        "analyzed": len(analyzed),
        "high": high,
        "medium": medium,
        "low": low,
        "unknown": len(analyzed) - high - medium - low,
        "unsupported_or_failed": sum(1 for i in items if _status(i) not in {"analyzed", "duplicate", "skipped"}),
        "duplicates": sum(1 for i in items if _status(i) == "duplicate"),
        "skipped": sum(1 for i in items if _status(i) == "skipped"),
        "external_model_active": sum(1 for item in analyzed if _model_active(item)),
        "source": source,
    }


def _archive_upload_items(filename: str, suffix: str, payload: bytes) -> list[dict[str, object]]:
    """Extract an uploaded archive to a temp dir and analyze each member.

    Members are reported as ``archive.zip::inner/path.png`` rows; the
    archive bytes and extracted tree are deleted before returning.
    """
    from .archives import extract_archive

    tmp_name = ""
    dest = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(payload)
            tmp_name = tmp.name
        # resolve: mkdtemp may return a symlinked path (/var→/private/var on
        # macOS); members come back resolved, so relative_to needs the real path.
        dest = str(Path(tempfile.mkdtemp(prefix="dflens-up-")).resolve())
        extraction = extract_archive(tmp_name, dest)
        items: list[dict[str, object]] = []
        for member in extraction.members:
            rel = member.relative_to(dest).as_posix()
            display = f"{filename}::{rel}"
            item = analyze_file(member, display=display, model_path=default_engine_profiles() or None)
            record = item.to_json()
            record["path"] = display
            record["name"] = display
            items.append(record)
        if extraction.warnings or extraction.skipped:
            # A container whose members could not all be analyzed is never a
            # clean LOW — skipped/warned members mean unseen evidence.
            # This branch only runs for partial extraction, so the
            # container is always undetermined (contract v2, never "low").
            reason = f"구성 파일 {extraction.skipped}개 스킵, 경고 {len(extraction.warnings)}건 — 일부 구성을 분석하지 못했습니다"
            items.append({
                "name": filename, "path": filename, "kind": "archive",
                "status": "unknown",
                "size_bytes": len(payload),
                "result": {
                    "score": 0, "band": "unknown", "band_label": "판단 불가",
                    "verdict": f"압축 파일: 판단 불가 — {len(extraction.members)}개 분석, {extraction.skipped}개 스킵",
                    "verdict_code": "undetermined", "verdict_label": "판단 불가",
                    "grade": "evidence", "grade_label": "감정 근거로 사용 가능",
                    "evidence": [],
                    "coverage": [{"check": "archive", "status": "skipped", "reason": reason}],
                    "reference_signals": [],
                    "probability": None, "probability_ci": None, "score_is_calibrated": False,
                    "signals": [{"title": "압축 컨테이너", "detail": f"구성 {len(extraction.members)}개", "weight": 0}],
                    "limitations": extraction.warnings,
                    "next_checks": [],
                },
            })
        if not items:
            items.append({
                "name": filename, "path": filename, "kind": "archive", "status": "failed",
                "error": "; ".join(extraction.warnings) or "해제된 파일이 없습니다",
            })
        return items
    finally:
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)
        if dest:
            shutil.rmtree(dest, ignore_errors=True)


def _analyze_upload_payload(content_type: str, body: bytes) -> dict[str, object]:
    """Analyze files uploaded via multipart/form-data.

    Each part is written to a temporary file, analyzed with the default
    engine profiles, then deleted. Archives are extracted and each member
    analyzed as its own row. The server never persists uploads.
    """
    if "multipart/form-data" not in content_type:
        return {"error": "multipart/form-data upload required"}
    from .archives import is_archive

    message = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    items: list[dict[str, object]] = []
    for part in message.iter_parts():
        filename = part.get_filename()
        payload = _part_bytes(part)
        if not filename or payload is None:
            continue
        if len(items) >= MAX_UPLOAD_FILES:
            items.append({"name": filename, "error": f"파일 수 상한({MAX_UPLOAD_FILES}) 초과"})
            break
        suffix = Path(filename).suffix[:16]
        try:
            if is_archive(filename):
                items.extend(_archive_upload_items(filename, suffix, payload))
                continue
            # delete=False: Windows cannot reopen a delete=True temp file.
            tmp_name = ""
            try:
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(payload)
                    tmp_name = tmp.name
                item = analyze_file(tmp_name, model_path=default_engine_profiles() or None)
            finally:
                if tmp_name:
                    Path(tmp_name).unlink(missing_ok=True)
            record = item.to_json()
            record["path"] = filename
            record["name"] = filename
            items.append(record)
        except Exception as exc:
            # Per-upload failure is a failed row (status "failed"), never a
            # silently missing file.
            logger.exception("upload analysis failed: %s", filename)
            items.append({"name": filename, "path": filename, "status": "failed", "error": failure_reason(exc)})
    if not items:
        return {"error": "업로드된 파일이 없습니다"}
    return {
        "schema_version": SCAN_JSON_SCHEMA_VERSION,
        "summary": _summarize_records(items, "upload"),
        "items": items,
        **_provenance(),
    }


def _check_text_payload(text: str, *, watermark_secret: str | None = None, watermark_gamma: float = 0.25) -> dict[str, object]:
    """Unified text check: core scan heuristics + neural member ensemble
    + fingerprint probes in one payload.

    The text is written to a temp .txt so the full file pipeline (including
    the causal-LM/classifier members) runs exactly as it would on a saved
    document; the temp file is deleted immediately. When ``watermark_secret``
    is supplied, a KGW green-list test runs under that key.
    """
    trimmed = text.strip()
    if len(trimmed) < 8:
        return {"error": "분석할 텍스트가 너무 짧습니다 (8자 이상)."}
    if len(trimmed) > 256 * 1024:
        return {"error": "텍스트가 256KB를 초과합니다."}
    models_dir = _models_dir()
    model_path = models_dir if models_dir and models_dir.is_dir() else (default_engine_profiles() or None)
    # delete=False: Windows cannot reopen a delete=True NamedTemporaryFile,
    # so the analyzers below would hit Permission denied.
    tmp_name = ""
    layer_errors: dict[str, Any] = {}
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as tmp:
            tmp.write(trimmed)
            tmp_name = tmp.name
        item = analyze_file(tmp_name, model_path=model_path)
        try:
            from .c2pa import analyze_metadata_forensic
            forensic = analyze_metadata_forensic(Path(tmp_name)).to_json()
        except Exception as exc:
            logger.exception("forensic layer failed")
            forensic = None
            _layer_error(layer_errors, "forensic", exc)
    finally:
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)
    from .text_advanced import analyze_text_advanced
    advanced = analyze_text_advanced(trimmed)
    watermark = None
    if watermark_secret:
        try:
            from .watermark import detect_kgw_watermark
            watermark = detect_kgw_watermark(trimmed, secret=watermark_secret, gamma=watermark_gamma).to_json()
        except Exception as exc:
            logger.exception("watermark layer failed")
            watermark = {"available": False, "verdict": "워터마크 검사 실패", "error": failure_reason(exc)}
            _layer_error(layer_errors, "watermark", exc)
    record = item.to_json()
    record["name"] = "pasted-text"
    record["path"] = "pasted-text"
    return {
        "schema_version": SCAN_JSON_SCHEMA_VERSION,
        "mode": "text",
        "item": record,
        "advanced": advanced.to_json(),
        "forensic": forensic,
        "watermark": watermark,
        **layer_errors,
        **_provenance(),
    }


def _check_file_payload(content_type: str, body: bytes) -> dict[str, object]:
    """Unified single-file check: full scan + forensics + text probes."""
    if "multipart/form-data" not in content_type:
        return {"error": "multipart/form-data 또는 application/json 본문이 필요합니다"}
    message = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    part = next(
        (p for p in message.iter_parts() if p.get_filename() and p.get_payload(decode=True)),
        None,
    )
    if part is None:
        return {"error": "업로드된 파일이 없습니다"}
    filename = part.get_filename() or "upload"
    payload = _part_bytes(part) or b""
    suffix = Path(filename).suffix[:16]
    from .archives import is_archive
    if is_archive(filename):
        items = _archive_upload_items(filename, suffix, payload)
        return {
            "schema_version": SCAN_JSON_SCHEMA_VERSION,
            "mode": "files",
            "summary": _summarize_records(items, "upload"),
            "items": items,
            **_provenance(),
        }
    models_dir = _models_dir()
    model_path = models_dir if models_dir and models_dir.is_dir() else (default_engine_profiles() or None)
    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(payload)
            tmp_name = tmp.name
        tmp_path = Path(tmp_name)
        item = analyze_file(tmp_path, model_path=model_path)
        record = item.to_json()
        forensic = None
        layer_errors: dict[str, Any] = {}
        try:
            from .c2pa import analyze_metadata_forensic
            forensic = analyze_metadata_forensic(tmp_path).to_json()
        except Exception as exc:
            logger.exception("forensic layer failed")
            _layer_error(layer_errors, "forensic", exc)
        advanced = None
        if item.kind == "text":
            try:
                from .text_advanced import analyze_text_advanced
                advanced = analyze_text_advanced(payload.decode("utf-8", errors="replace")).to_json()
            except Exception as exc:
                logger.exception("text-advanced layer failed")
                _layer_error(layer_errors, "advanced", exc)
    finally:
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)
    record["name"] = filename
    record["path"] = filename
    return {
        "schema_version": SCAN_JSON_SCHEMA_VERSION,
        "mode": "file",
        "item": record,
        "advanced": advanced,
        "forensic": forensic,
        **layer_errors,
        **_provenance(),
    }


def _compare_payload(content_type: str, body: bytes) -> dict[str, object]:
    """Two-file comparison: speaker distance for audio pairs, stylometry
    for text/document pairs. Both parts must carry filenames."""
    if "multipart/form-data" not in content_type:
        return {"error": "multipart/form-data 본문이 필요합니다"}
    message = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    parts = [
        p for p in message.iter_parts() if p.get_filename() and _part_bytes(p)
    ]
    if len(parts) < 2:
        return {"error": "비교할 파일 2개가 필요합니다"}
    tmp_paths: list[Path] = []
    try:
        for part in parts[:2]:
            suffix = Path(part.get_filename() or "upload").suffix[:16]
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(_part_bytes(part) or b"")
                tmp_paths.append(Path(tmp.name))
        from .core import compare_files
        result = compare_files(tmp_paths[0], tmp_paths[1])
        if isinstance(result, dict) and not result.get("error"):
            result.update(_provenance())
        return result
    finally:
        for tmp_path in tmp_paths:
            tmp_path.unlink(missing_ok=True)


def _feedback_path() -> Path:
    """Where web-UI examiner labels accumulate; overridable for tests."""
    override = os.environ.get("DEEPFAKE_LENS_FEEDBACK")
    if override:
        return Path(override)
    return Path.home() / ".deepfake-lens" / "feedback.jsonl"


def _feedback_payload(body: bytes) -> dict[str, object]:
    """Record one examiner verdict from the web UI.

    Appends a ``{path, expected_label, notes, embedded_result}`` JSONL row the
    ``feedback`` CLI command can consume directly — ``embedded_result`` carries
    the scan result so labeled rows need no rescan.
    """
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"error": "invalid JSON body"}
    label = str(data.get("expected_label", "") or "").strip().lower()
    if not (is_positive_label(label) or is_negative_label(label)):
        return {"error": "expected_label must be a recognized label (e.g. synthetic, real)"}
    path = str(data.get("path") or data.get("name") or "").strip()
    if not path:
        return {"error": "path is required"}
    entry: dict[str, object] = {
        "path": path,
        "expected_label": label,
        "notes": str(data.get("notes", "") or ""),
    }
    result = data.get("result")
    if isinstance(result, dict):
        # load_feedback reads the embedded scan result from the "result" key.
        entry["result"] = result
    feedback_file = _feedback_path()
    feedback_file.parent.mkdir(parents=True, exist_ok=True)
    with feedback_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"ok": True, "feedback_file": str(feedback_file)}


def _report_payload(body: bytes, format_override: str | None = None) -> bytes | dict[str, object]:
    """Render the HTML or court-admissible forensic PDF report for web-scan results.

    Accepts the items array the GUI holds (scan/upload payload rows), rebuilds
    ScanItem objects through the same cache deserializer used on disk, and
    returns the same report the CLI's --html-out produces — so web and CLI
    artifacts are identical. Posted ``thresholds``/``coverage`` provenance and
    case metadata are preserved into the artifact; a report that drops them
    would let an uncalibrated scan masquerade as a measured one.
    """
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"error": "invalid JSON body"}
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return {"error": "items array is required"}

    thresholds = data.get("thresholds")
    if thresholds is not None and not isinstance(thresholds, dict):
        return {"error": "thresholds must be an object"}
    coverage = data.get("coverage")
    if coverage is not None and not isinstance(coverage, dict):
        return {"error": "coverage must be an object"}

    try:
        items = [_scan_item_from_json(row) for row in raw_items if isinstance(row, dict)]
    except (TypeError, ValueError) as exc:
        return {"error": f"malformed item: {exc}"}
    if not items:
        return {"error": "items array is required"}

    # Report hashing reads item.path from disk — confine those reads to the
    # registered scan roots so a crafted payload cannot probe host files.
    # Scan rows carry relative paths, so they are resolved against the
    # registered roots first; an unresolved or escaping path stays unread.
    read_roots_registered = bool(_READ_ROOTS)
    def _resolve_item_path(path_text: str) -> Path | None:
        p = Path(path_text).expanduser()
        candidates = [p] if p.is_absolute() else [root / p for root in _READ_ROOTS]
        for cand in candidates:
            try:
                resolved = cand.resolve()
            except OSError:
                continue
            if resolved.is_file() and any(_is_within(resolved, r) for r in _READ_ROOTS):
                return resolved
        return None

    def _path_allowed(path_text: str) -> bool:
        if not read_roots_registered:
            return True  # standalone GUI-less use — same trust as the CLI
        return _resolve_item_path(path_text) is not None

    # Same counting rule as the CLI (core.summarize) so web and CLI report
    # headers agree on every verdict count.
    summary = summarize(items, capped=False)
    req_format = (format_override or data.get("format") or "html").lower()
    suffix = ".pdf" if req_format in ("pdf", "evidence", "evidence-statement") else ".html"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        if req_format in ("evidence", "evidence-statement"):
            from .evidence_statement import build_evidence_statement, write_evidence_statement_pdf
            try:
                stmt = build_evidence_statement(
                    items,
                    case_no=str(data.get("case_no") or "(사건번호 입력)"),
                    case_name=str(data.get("case_name") or "성폭력처벌법위반(허위영상물편집등) 및 정보통신망법위반"),
                    plaintiff=str(data.get("plaintiff") or "(의뢰사 상호명 입력) 귀하"),
                    defendant=str(data.get("defendant") or "(피고/피의자 성명 입력)"),
                    court=str(data.get("court") or "○○지방법원 귀중"),
                    law_firm=str(data.get("law_firm") or "법무법인(유한) 대륜"),
                    contact=str(data.get("contact") or ""),
                    center=str(data.get("center") or "디지털포렌식 감정센터"),
                    thresholds=thresholds,
                    coverage=coverage,
                )
                write_evidence_statement_pdf(tmp_path, stmt)
            except RuntimeError as exc:
                return {"error": f"증거설명서 PDF 생성 실패: {exc}", "hint": "pip install 'deepfake-lens[forensic]' 후 재시도하거나 Markdown 출력을 사용하세요."}
        elif req_format == "pdf":
            from .reports import write_forensic_pdf_report
            exhibit_no = str(data.get("exhibit_no") or "갑 제        호증")
            write_forensic_pdf_report(
                tmp_path, summary, items,
                exhibit_no=exhibit_no,
                thresholds=thresholds,
                coverage=coverage,
                resolve_path=_resolve_item_path if read_roots_registered else None,
                allow_path=_path_allowed,
            )
        else:
            write_html_report(tmp_path, summary, items, thresholds=thresholds)
        return tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
