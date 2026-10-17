"""P0R-6 test helper: a folder scan held as this server process's job, for POST /api/report requests."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote


def scan_job_for(folder: Path | str, default_folder: Path | str | None = None, query: str = "") -> str:
    """Run the web scan of ``folder`` (read roots as the caller configured them) and return its ``scan_job`` id."""
    from deepfake_lens import webapp_api

    payload = webapp_api._scan_payload(
        f"folder={quote(str(folder))}{query}", default_folder=Path(default_folder or folder),
    )
    if "error" in payload or "scan_job" not in payload:
        raise AssertionError(f"scan failed: {payload}")
    return str(payload["scan_job"])
