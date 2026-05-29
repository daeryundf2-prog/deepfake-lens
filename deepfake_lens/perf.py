from __future__ import annotations

import json
import time
from pathlib import Path

from .core import scan_directory


def run_performance_check(
    folder: Path | str,
    *,
    recursive: bool = True,
    pixel_mode: str = "off",
    workers: int = 1,
    cache_path: Path | None = None,
    hash_db_path: Path | None = None,
    max_files: int = 1000,
) -> dict[str, object]:
    start = time.perf_counter()
    summary, _ = scan_directory(
        folder,
        recursive=recursive,
        pixel_mode=pixel_mode,
        workers=workers,
        cache_path=cache_path,
        hash_db_path=hash_db_path,
        dedupe=hash_db_path is not None,
        max_files=max_files,
    )
    elapsed = max(0.000001, time.perf_counter() - start)
    return {
        "version": "performance-check-v1",
        "folder": str(Path(folder)),
        "recursive": recursive,
        "pixel_mode": pixel_mode,
        "workers": workers,
        "max_files": max_files,
        "elapsed_seconds": elapsed,
        "files_per_second": summary.total / elapsed,
        "summary": summary.to_json(),
        "cache_hit_rate": summary.cached / max(1, summary.total),
        "duplicate_rate": summary.duplicates / max(1, summary.total),
    }


def write_performance_check(path: Path | str, payload: dict[str, object]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
