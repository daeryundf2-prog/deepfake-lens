from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


@dataclass(frozen=True)
class VideoPlanItem:
    path: str
    size_bytes: int
    frame_dir: str
    ffmpeg_command: list[str]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def discover_videos(root: Path | str, *, recursive: bool = True) -> list[Path]:
    root_path = Path(root)
    iterator: Iterable[Path] = root_path.rglob("*") if recursive else root_path.iterdir()
    return [path for path in iterator if path.is_file() and path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS]


def build_video_frame_plan(
    root: Path | str,
    *,
    output_root: Path | str,
    recursive: bool = True,
    sample_every_seconds: float = 2.0,
) -> dict[str, object]:
    root_path = Path(root)
    output_path = Path(output_root)
    items: list[VideoPlanItem] = []
    for video_path in discover_videos(root_path, recursive=recursive):
        frame_dir = output_path / _safe_frame_dir(root_path, video_path)
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-vf",
            f"fps=1/{max(0.1, sample_every_seconds)}",
            str(frame_dir / "frame-%06d.png"),
        ]
        try:
            size = video_path.stat().st_size
        except OSError:
            size = 0
        items.append(VideoPlanItem(str(video_path), size, str(frame_dir), command))
    return {
        "version": "video-frame-plan-v1",
        "root": str(root_path),
        "output_root": str(output_path),
        "sample_every_seconds": sample_every_seconds,
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
        "count": len(items),
        "items": [item.to_json() for item in items],
        "next_scan": f"python -m deepfake_lens scan {output_path} --recursive --pixel deep --heatmaps",
    }


def write_video_frame_plan(root: Path | str, output_path: Path | str, *, frame_root: Path | str, recursive: bool = True, sample_every_seconds: float = 2.0) -> dict[str, object]:
    plan = build_video_frame_plan(root, output_root=frame_root, recursive=recursive, sample_every_seconds=sample_every_seconds)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return plan


def extract_video_frames(plan: dict[str, object], *, limit: int | None = None) -> dict[str, object]:
    if shutil.which("ffmpeg") is None:
        return {"version": "video-extract-results-v1", "results": [], "error": "ffmpeg is not installed"}
    items = plan.get("items", []) if isinstance(plan.get("items"), list) else []
    results = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        command = [str(part) for part in item.get("ffmpeg_command", [])] if isinstance(item.get("ffmpeg_command"), list) else []
        frame_dir = Path(str(item.get("frame_dir", "")))
        if not command:
            continue
        frame_dir.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        results.append(
            {
                "path": item.get("path", ""),
                "returncode": completed.returncode,
                "stderr": completed.stderr[-2000:],
            }
        )
    return {"version": "video-extract-results-v1", "results": results}


def _safe_frame_dir(root: Path, video_path: Path) -> str:
    try:
        relative = video_path.relative_to(root)
    except ValueError:
        relative = Path(video_path.name)
    return "__".join(part.replace("/", "_").replace("\\", "_") for part in relative.with_suffix("").parts)
