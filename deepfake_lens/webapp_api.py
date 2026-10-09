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
from collections import OrderedDict
from dataclasses import replace
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
from .analysis_api import (
    WEB_MAX_FILE_BYTES_CEILING,
    WEB_MAX_SCAN_FILES,
    AnalysisOptions,
    analyze_path,
    analyze_rows,
    load_thresholds,
    provenance,
    scan_folder,
    scan_payload,
)
from .analysis_api import default_engine_profiles as _engine_profiles_in
from .core import SCAN_JSON_SCHEMA_VERSION, BatchScanSummary, DEFAULT_METADATA_BYTES, _scan_item_from_json, is_default_heatmap_output, summarize  # noqa: F401
from .datasets import is_negative_label, is_positive_label
from .reports import write_html_report


# G7: request limits live in analysis_api (AnalysisOptions.from_query);
# re-exported under their historical names.
MAX_SCAN_FILES = WEB_MAX_SCAN_FILES
MAX_FILE_BYTES_CEILING = WEB_MAX_FILE_BYTES_CEILING
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_UPLOAD_FILES = 20

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
    """Engine profiles of the server's models dir (G7: same set as the CLI)."""
    return _engine_profiles_in(Path(root) if root is not None else _models_dir())


def _web_options(params: dict[str, list[str]] | None = None) -> AnalysisOptions:
    """AnalysisOptions for a web request (InvalidOption -> HTTP 400)."""
    return AnalysisOptions.from_query(params or {}, models_dir=_models_dir())



def _load_gui() -> str:
    """Load the GUI HTML file."""
    gui_path = Path(__file__).parent / "gui.html"
    if gui_path.exists():
        return gui_path.read_text(encoding="utf-8")
    return "<h1>GUI 파일을 찾을 수 없습니다</h1>"


def _scan_payload(query: str, *, default_folder: Path | None, should_stop: Callable[[], bool] | None = None) -> dict[str, object]:
    """Handle scan request.

    Raises ReadRootDenied (-> HTTP 403) when the folder is outside the
    operator-registered roots; the caller's folder is never registered (G31).
    """
    folder = _requested_folder(query, default_folder)
    _require_read_root(folder, default_folder)
    # G7: same options object, thresholds and engine set as the CLI. A bad
    # option (non-integer limit, model_path outside the models dir) raises
    # InvalidOption (a ValueError) -> HTTP 400 before any file is read.
    options = _web_options(parse_qs(query))
    try:
        summary, items, thresholds = scan_folder(folder, options, should_stop=should_stop)
        return scan_payload(summary, items, thresholds, options)
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
    # Refuse out-of-root folders (403) and invalid options (400) up front,
    # not inside the job.
    _require_read_root(_requested_folder(query, default_folder), default_folder)
    _web_options(parse_qs(query))
    with _SCAN_JOBS_LOCK:
        _scan_job_evict(time.time())
        if len(_SCAN_JOBS) >= _SCAN_JOB_MAX:
            raise ValueError("실행 중인 검사 작업이 너무 많습니다 — 진행 중인 작업이 끝난 뒤 다시 시도하십시오")
        job_id = secrets.token_hex(8)
        cancel = threading.Event()
        _SCAN_JOBS[job_id] = {"status": "running", "created": time.time(), "cancel": cancel}

    def work() -> None:
        try:
            result = _scan_payload(query, default_folder=default_folder, should_stop=cancel.is_set)
            status = "done"
        except Exception as exc:  # noqa: BLE001 - a worker crash is recorded, not raised into the thread
            # D16: log the traceback and record the failure as coverage +
            # limitation — never just a bare message (fail-closed: the scan
            # did not complete, so nothing in it may read as a conclusion).
            logger.exception("scan job %s failed", job_id)
            reason = failure_reason(exc)
            result = {
                "error": "폴더 검사 작업이 실패했습니다",
                "detail": reason,
                "coverage": [{"check": "scan_job", "status": "failed", "reason": reason}],
                "limitations": [f"검사 작업 실패 — {reason}. 이 작업의 결과는 결론으로 사용할 수 없습니다."],
            }
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
        return {"error": "job 매개변수가 필요합니다"}
    with _SCAN_JOBS_LOCK:
        _scan_job_evict(time.time())
        entry = _SCAN_JOBS.get(job_id)
        if entry is None:
            return {"error": "알 수 없거나 만료된 작업입니다"}
        payload: dict[str, object] = {"job_id": job_id, "status": entry["status"]}
        if entry["status"] != "running":
            payload["result"] = entry.get("result")
        return payload


def _scan_cancel_payload(query: str) -> dict[str, object]:
    params = parse_qs(query)
    job_id = params.get("job", [""])[0].strip()
    if not job_id:
        return {"error": "job 매개변수가 필요합니다"}
    with _SCAN_JOBS_LOCK:
        entry = _SCAN_JOBS.get(job_id)
        if entry is None:
            return {"error": "알 수 없거나 만료된 작업입니다"}
        if entry["status"] != "running":
            return {"job_id": job_id, "status": entry["status"], "cancelled": False}
        cancel = entry.get("cancel")
        if isinstance(cancel, threading.Event):
            cancel.set()
        return {"job_id": job_id, "status": "cancelling", "cancelled": True}


def _analyze_file_payload(query: str) -> dict[str, object]:
    """Single-file analysis (``/api/analyze-file``).

    D3: the conclusion is the scan result (``analysis_api.analyze_rows``,
    three verdicts) — the same path, photo gate and thresholds as /api/scan;
    an archive is expanded like the folder scan (B1) and ``rows`` lists the
    member and container rows.
    The provenance-metadata scan and the tool-marker match ride along as
    layer diagnostics (reference, no band). No pixel pre-screen runs here:
    scan's own pixel layer is gated by the photo classifier and recorded in
    coverage.
    """
    from .cli_standalone import analysis_result_for_path, tool_attribution
    from .layer_diagnostic import to_layer_diagnostic

    params = parse_qs(query)
    file_path = params.get("file", [""])[0]

    if not file_path:
        return {"error": "파일 경로가 없습니다"}
    # G31: confined to the operator roots even when none are registered
    # (then only the server's default folder) — raises ReadRootDenied (403).
    path = _require_read_root(Path(file_path))
    if not path.exists():
        return {"error": f"파일이 존재하지 않습니다: {file_path}"}

    options = _web_options()
    thresholds = load_thresholds(options)
    try:
        response, _ = analysis_result_for_path(path, options, command="analyze-file", thresholds=thresholds)
    except Exception as exc:
        logger.exception("file analysis failed")
        # Detail stays in `detail` so the GUI shows a clean headline instead
        # of a raw exception sentence; the type/message aid local debugging.
        return {"error": "파일 분석 중 오류가 발생했습니다", "detail": failure_reason(exc)}
    response["file"] = str(path)
    layers: dict[str, Any] = {}
    try:
        from .c2pa import analyze_metadata_forensic

        layers["provenance_metadata"] = to_layer_diagnostic(
            "provenance_metadata", analyze_metadata_forensic(path).to_json(), layer_label="출처 메타데이터 계층"
        )
    except Exception as exc:
        logger.exception("provenance layer failed for %s", path)
        _layer_error(response, "provenance_metadata", exc)
    try:
        layers["tool_candidates"] = to_layer_diagnostic(
            "tool_attribution", tool_attribution(path).to_json(), layer_label="생성 도구 표지 대조"
        )
    except Exception as exc:
        logger.exception("tool attribution layer failed for %s", path)
        _layer_error(response, "tool_candidates", exc)
    response["layer_diagnostics"] = layers
    response.update(_provenance(options, thresholds))
    return response


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


# Operator-registered read roots (G31). Every endpoint that reads a path the
# caller names — /api/scan, /api/analyze-file, /api/heatmap, /api/preview
# and the report hashing/heatmap embedding — is confined to these. Only
# server setup registers them (``configure_read_roots`` from run_server:
# ``--folder`` and each ``--allow-root``); a request can never add one. With
# none registered, the server's own default folder (``--folder`` or the
# working directory it was started in) is the only root — never "anything".
# The `root` query parameter is kept for backward compatibility but can only
# narrow, never widen, the scope.
_READ_ROOTS_LOCK = threading.Lock()
_READ_ROOTS: OrderedDict[Path, None] = OrderedDict()
_READ_ROOTS_MAX = 64
READ_ROOT_DENIED_MESSAGE = "허용되지 않은 경로"


class ReadRootDenied(PermissionError):
    """The requested path is outside every operator-registered read root.

    HTTP layers turn this into status 403 with ``read_root_denied_body()`` —
    a fixed message that echoes no file content.
    """


def read_root_denied_body() -> dict[str, object]:
    return {"error": READ_ROOT_DENIED_MESSAGE}


def _register_read_root(folder: Path) -> None:
    """Register an operator read root. Call only from server setup."""
    resolved = folder.expanduser().resolve()
    with _READ_ROOTS_LOCK:
        if resolved in _READ_ROOTS:
            _READ_ROOTS.move_to_end(resolved)
            return
        while len(_READ_ROOTS) >= _READ_ROOTS_MAX:
            _READ_ROOTS.popitem(last=False)  # evict the oldest registration
        _READ_ROOTS[resolved] = None


def configure_read_roots(default_folder: Path | None, allow_roots: list[Path] | tuple[Path, ...] | None = None) -> None:
    """Server-setup hook: register ``--folder`` and every ``--allow-root``."""
    if default_folder is not None:
        _register_read_root(Path(default_folder))
    for root in allow_roots or ():
        _register_read_root(Path(root))


def _effective_roots(default_folder: Path | None = None) -> list[Path]:
    """Registered roots in registration order, or the default folder alone."""
    with _READ_ROOTS_LOCK:
        roots = list(_READ_ROOTS)
    if roots:
        return roots
    return [Path(default_folder or ".").expanduser().resolve()]


def _require_read_root(path: Path, default_folder: Path | None = None) -> Path:
    """Resolve ``path`` and raise ReadRootDenied unless it is inside a root."""
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise ReadRootDenied(READ_ROOT_DENIED_MESSAGE) from exc
    if not any(_is_within(resolved, root) for root in _effective_roots(default_folder)):
        raise ReadRootDenied(READ_ROOT_DENIED_MESSAGE)
    return resolved


def _requested_folder(query: str, default_folder: Path | None) -> Path:
    params = parse_qs(query)
    return Path(params.get("folder", [str(default_folder or ".")])[0]).expanduser()


def _read_root_allows(path: Path, root_value: str = "", default_folder: Path | None = None) -> bool:
    """True when `path` sits under an operator-registered root.

    The caller-supplied root is also checked when present so a stale or
    mismatched root argument cannot broaden access beyond the registered
    set — both conditions must hold.
    """
    roots = _effective_roots(default_folder)
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
        return 400, "경로가 없습니다".encode("utf-8"), "missing"
    path = Path(path_value).expanduser().resolve()
    # Heatmaps live in the tool-owned output root (never in the evidence
    # folder, R-IN-1); those are served without a read root.
    if path.suffix.lower() != ".png" or not (_read_root_allows(path, root_value) or is_default_heatmap_output(path)):
        return 403, b"forbidden", "forbidden"
    try:
        data = path.read_bytes()
    except OSError:
        return 404, "파일을 찾을 수 없습니다".encode("utf-8"), "not-found"
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
        return 400, "경로가 없습니다".encode("utf-8"), "missing", ""
    path = Path(path_value).expanduser().resolve()
    mime = _PREVIEW_MIME.get(path.suffix.lower())
    if mime is None or not _read_root_allows(path, root_value):
        return 403, b"forbidden", "forbidden", ""
    try:
        if path.stat().st_size > MAX_PREVIEW_BYTES:
            return 413, b"too large", "too-large", ""
        data = path.read_bytes()
    except OSError:
        return 404, "파일을 찾을 수 없습니다".encode("utf-8"), "not-found", ""
    return 200, data, "", mime


def _provenance(options: AnalysisOptions | None = None, thresholds: Any = None) -> dict[str, object]:
    """Coverage + threshold provenance attached to every scan-shaped payload.

    A response that omits these lets a heuristic-only run read as a full
    neural one downstream (GUI banner, /api/report, exported JSON). G7: the
    thresholds reported are the ones the analysis actually used.
    """
    opts = options or _web_options()
    try:
        return provenance(opts, thresholds)
    except Exception as exc:
        logger.exception("weights coverage unavailable")
        from .core import _thresholds_json
        return {"coverage": {"error": failure_reason(exc)}, "thresholds": _thresholds_json(thresholds)}


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
    across CLI JSON, web uploads, and report headers. A row with a result
    is counted by its verdict unless its status is failed/unsupported/
    duplicate/skipped (``result_types.is_verdict_row``) — archive container
    rows included (R5); everything else lands in unsupported_or_failed,
    duplicates or skipped.
    """
    from .result_types import is_verdict_row

    def _status(item: dict[str, object]) -> str:
        return str(item.get("status") or ("failed" if item.get("error") else "analyzed"))

    def _verdict(item: dict[str, object]) -> str:
        result = item.get("result")
        return str(result.get("verdict_code") or "undetermined") if isinstance(result, dict) else ""

    def _has_failed_check(item: dict[str, object]) -> bool:
        result = item.get("result")
        coverage = result.get("coverage") if isinstance(result, dict) else None
        return isinstance(coverage, list) and any(isinstance(e, dict) and e.get("status") == "failed" for e in coverage)

    analyzed = [i for i in items if is_verdict_row(_status(i), isinstance(i.get("result"), dict))]
    verdict_rows = {id(i) for i in analyzed}
    # D16: verdict counts only — same keys as BatchScanSummary.to_json().
    return {
        "manipulation_evidence": sum(1 for item in analyzed if _verdict(item) == "manipulation_evidence"),
        "authenticity_evidence": sum(1 for item in analyzed if _verdict(item) == "authenticity_evidence"),
        "undetermined": sum(1 for item in analyzed if _verdict(item) == "undetermined"),
        "checks_failed": sum(1 for item in analyzed if _has_failed_check(item)),
        "container_rows": sum(1 for item in analyzed if item.get("kind") == "archive"),
        "total": len(items),
        "analyzed": len(analyzed),
        "unsupported_or_failed": sum(1 for i in items if id(i) not in verdict_rows and _status(i) not in {"duplicate", "skipped"}),
        "duplicates": sum(1 for i in items if _status(i) == "duplicate"),
        "skipped": sum(1 for i in items if _status(i) == "skipped"),
        "external_model_active": sum(1 for item in analyzed if _model_active(item)),
        "source": source,
    }


def _archive_upload_items(
    filename: str, suffix: str, payload: bytes, *, options: AnalysisOptions, thresholds: Any,
) -> list[dict[str, object]]:
    """Analyze an uploaded archive exactly as a folder scan analyzes it (B1).

    The bytes are written under their own file name into a fresh temp
    folder and handed to ``analysis_api.analyze_rows`` — the folder
    scanner's own body — so the rows are the scan's rows: members as
    ``archive.zip::inner/path.png``, the container row with every refused
    member (traversal, absolute path, link, "압축 예산 초과(...)" bomb/budget
    limits) as a skipped ``archive_member`` coverage entry plus a
    limitation, an unopenable archive as a failed ``archive`` check, a
    missing extractor as a skipped one, and the uploaded archive's SHA-256.
    The temp folder (archive and extracted tree) is deleted before
    returning. ``suffix`` is kept for the call sites; the name carries it.
    """
    del suffix
    base = Path(filename.replace("\\", "/")).name or "upload.zip"
    folder = Path(tempfile.mkdtemp(prefix="dflens-up-")).resolve()
    try:
        target = folder / base
        target.write_bytes(payload)
        records: list[dict[str, object]] = []
        for row in analyze_rows(target, options, thresholds=thresholds):
            record = row.to_json()
            if base != filename:
                # Report the name the client sent (a path in the archive
                # name is kept as given, like the pre-B1 upload rows).
                for key in ("path", "name"):
                    value = str(record.get(key) or "")
                    if value == base or value.startswith(base + "::"):
                        record[key] = filename + value[len(base):]
            records.append(record)
        return records
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _analyze_upload_payload(content_type: str, body: bytes) -> dict[str, object]:
    """Analyze files uploaded via multipart/form-data.

    Each part is written to a temporary file, analyzed with the default
    engine profiles, then deleted. Archives are extracted and each member
    analyzed as its own row. The server never persists uploads.
    """
    if "multipart/form-data" not in content_type:
        return {"error": "multipart/form-data 업로드가 필요합니다"}
    from .archives import is_archive

    message = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    options = _web_options()
    thresholds = load_thresholds(options)
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
                items.extend(_archive_upload_items(filename, suffix, payload, options=options, thresholds=thresholds))
                continue
            # delete=False: Windows cannot reopen a delete=True temp file.
            tmp_name = ""
            try:
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(payload)
                    tmp_name = tmp.name
                item = analyze_path(tmp_name, options, thresholds=thresholds)
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
        **_provenance(options, thresholds),
    }


def _provenance_layer(raw: dict[str, Any]) -> dict[str, Any]:
    """c2pa.analyze_metadata_forensic output as a layer diagnostic (D1/D3)."""
    from .layer_diagnostic import to_layer_diagnostic

    return to_layer_diagnostic("provenance_metadata", raw, layer_label="출처 메타데이터 계층")


def _text_statistics_layer(raw: dict[str, Any]) -> dict[str, Any]:
    """text_advanced output as a layer diagnostic (D1)."""
    from .layer_diagnostic import to_layer_diagnostic

    return to_layer_diagnostic("text_statistics", raw, layer_label="텍스트 문체 통계 계층")


def compare_layer(raw: dict[str, Any]) -> dict[str, Any]:
    """core.compare_files output as a layer diagnostic (D1)."""
    from .layer_diagnostic import to_layer_diagnostic

    return to_layer_diagnostic("compare", raw, layer_label="두 파일 유사도 계층")


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
    options = _web_options()
    thresholds = load_thresholds(options)
    # delete=False: Windows cannot reopen a delete=True NamedTemporaryFile,
    # so the analyzers below would hit Permission denied.
    tmp_name = ""
    layer_errors: dict[str, Any] = {}
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as tmp:
            tmp.write(trimmed)
            tmp_name = tmp.name
        item = analyze_path(tmp_name, options, thresholds=thresholds)
        try:
            from .c2pa import analyze_metadata_forensic
            forensic = _provenance_layer(analyze_metadata_forensic(Path(tmp_name)).to_json())
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
            watermark = {"available": False, "reference_band": "unavailable", "reference_note": "워터마크 검사 실패", "error": failure_reason(exc)}
            _layer_error(layer_errors, "watermark", exc)
    record = item.to_json()
    record["name"] = "pasted-text"
    record["path"] = "pasted-text"
    return {
        "schema_version": SCAN_JSON_SCHEMA_VERSION,
        "mode": "text",
        "item": record,
        "advanced": _text_statistics_layer(advanced.to_json()),
        "forensic": forensic,
        "watermark": watermark,
        **layer_errors,
        **_provenance(options, thresholds),
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
    options = _web_options()
    thresholds = load_thresholds(options)
    if is_archive(filename):
        items = _archive_upload_items(filename, suffix, payload, options=options, thresholds=thresholds)
        return {
            "schema_version": SCAN_JSON_SCHEMA_VERSION,
            "mode": "files",
            "summary": _summarize_records(items, "upload"),
            "items": items,
            **_provenance(options, thresholds),
        }
    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(payload)
            tmp_name = tmp.name
        tmp_path = Path(tmp_name)
        item = analyze_path(tmp_path, options, thresholds=thresholds)
        record = item.to_json()
        forensic = None
        layer_errors: dict[str, Any] = {}
        try:
            from .c2pa import analyze_metadata_forensic
            forensic = _provenance_layer(analyze_metadata_forensic(tmp_path).to_json())
        except Exception as exc:
            logger.exception("forensic layer failed")
            _layer_error(layer_errors, "forensic", exc)
        advanced = None
        if item.kind == "text":
            try:
                from .text_advanced import analyze_text_advanced
                advanced = _text_statistics_layer(analyze_text_advanced(payload.decode("utf-8", errors="replace")).to_json())
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
        **_provenance(options, thresholds),
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
            # D1: similarity is an unmeasured reference number — the
            # former same/unclear/different band is not returned.
            options = _web_options()
            diag = compare_layer(result)
            diag.update(_provenance(options, load_thresholds(options)))
            return diag
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
        return {"error": "JSON 본문을 해석할 수 없습니다"}
    label = str(data.get("expected_label", "") or "").strip().lower()
    if not (is_positive_label(label) or is_negative_label(label)):
        return {"error": "expected_label은 인식 가능한 라벨이어야 합니다 (예: synthetic, real)"}
    path = str(data.get("path") or data.get("name") or "").strip()
    if not path:
        return {"error": "path가 필요합니다"}
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


def _report_payload(body: bytes, format_override: str | None = None, *, default_folder: Path | None = None) -> bytes | dict[str, object]:
    """Render the HTML or court-admissible forensic PDF report for web-scan results.

    ``format=json`` returns the signed report body itself. Raises
    ReadRootDenied (-> 403) when a posted heatmap_path is outside the roots.

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
        return {"error": "JSON 본문을 해석할 수 없습니다"}
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

    # G31: every disk read this report makes — evidence hashing and heatmap
    # embedding — is confined to the operator read roots (or, with none
    # registered, the server's default folder). Scan rows carry relative
    # paths, so they are resolved against the roots; an unresolved or
    # escaping path stays unread.
    roots = _effective_roots(default_folder)

    def _resolve_item_path(path_text: str) -> Path | None:
        p = Path(path_text).expanduser()
        candidates = [p] if p.is_absolute() else [root / p for root in roots]
        for cand in candidates:
            try:
                resolved = cand.resolve()
            except OSError:
                continue
            if resolved.is_file() and any(_is_within(resolved, r) for r in roots):
                return resolved
        return None

    def _path_allowed(path_text: str) -> bool:
        return _resolve_item_path(path_text) is not None

    def _heatmap_allowed(path_text: str) -> bool:
        try:
            resolved = Path(path_text).expanduser().resolve()
        except OSError:
            return False
        return any(_is_within(resolved, r) for r in roots) or is_default_heatmap_output(resolved)

    # A posted heatmap_path outside the roots is a probe for host files:
    # refuse the whole report (403) instead of rendering around it.
    for item in items:
        pixel = item.result.pixel_analysis if item.result is not None else None
        heatmap_path = getattr(pixel, "heatmap_path", None)
        if heatmap_path and not _heatmap_allowed(str(heatmap_path)):
            raise ReadRootDenied(READ_ROOT_DENIED_MESSAGE)

    # Client-posted sha256 values are never trusted: each row's hash is
    # recomputed from the evidence file under the roots (None when it cannot
    # be read), once, and reused by every renderer and by the signature.
    from .reports import _evidence_sha256, signed_report_body

    items = [replace(item, sha256=_evidence_sha256(item.path, _path_allowed, _resolve_item_path)) for item in items]

    # Same counting rule as the CLI (core.summarize) so web and CLI report
    # headers agree on every verdict count.
    summary = summarize(items, capped=False)
    req_format = (format_override or data.get("format") or "html").lower()
    # G30: web reports are signed when DEEPFAKE_LENS_REPORT_KEY is set and
    # say "서명 없음" otherwise; the pins are those of the server's models dir.
    from .profile_pins import profile_pins

    signed = signed_report_body(
        summary, items, thresholds=thresholds, coverage=coverage,
        report_format=req_format, model_pins=profile_pins(_models_dir()),
    )
    if req_format == "json":
        return signed
    suffix = ".pdf" if req_format in ("pdf", "evidence", "evidence-statement") else ".html"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        if req_format in ("evidence", "evidence-statement"):
            from .evidence_statement import build_evidence_statement, signed_statement_body, write_evidence_statement_pdf
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
                # G30: signed like the other web reports (server key + pins).
                write_evidence_statement_pdf(tmp_path, stmt, signed=signed_statement_body(stmt, model_pins=profile_pins(_models_dir())))
            except RuntimeError as exc:
                return {"error": f"증거설명서 PDF 생성 실패: {exc}", "hint": "`pip install 'deepfake-lens[forensic]'` 후 재시도하거나 Markdown 출력을 사용하세요."}
        elif req_format == "pdf":
            from .reports import write_forensic_pdf_report
            exhibit_no = str(data.get("exhibit_no") or "갑 제        호증")
            write_forensic_pdf_report(
                tmp_path, summary, items,
                exhibit_no=exhibit_no,
                thresholds=thresholds,
                coverage=coverage,
                resolve_path=_resolve_item_path,
                allow_path=_path_allowed,
                signed_report=signed,
            )
        else:
            write_html_report(tmp_path, summary, items, thresholds=thresholds, allow_path=_heatmap_allowed, signed_report=signed)
        return tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
