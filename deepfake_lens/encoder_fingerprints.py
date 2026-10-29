"""Encoder fingerprints: which encoder table / re-encode format a file's own bytes equal (facts only).

Two comparisons against the pinned table ``models/encoder-fingerprints.json``
(every entry with its source):

- JPEG: the file's DQT tables (read by :func:`platform_traces.parse_jpeg`,
  natural order, the table each component uses) compared exactly with the
  IJG libjpeg standard tables (ITU-T T.81 Annex K) scaled for every quality
  1-100, with and without the baseline clamp — "the last encoder that saved
  this file used the IJG standard table at quality N", or "no IJG quality
  gives these tables" with the nearest quality and how many values differ.
- Video: the first H.264/HEVC track's coded size and profile plus the
  container's encoder field compared with messenger re-encode formats
  measured in published work (KakaoTalk normal/high quality).

What a match means is worded in :mod:`deepfake_lens.evidence_rules`
(``jpeg_table_evidence`` / ``messenger_video_evidence``): never
manipulation, generation or authenticity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .platform_traces import JpegStructure

TABLE_PATH = Path(__file__).resolve().parent / "models" / "encoder-fingerprints.json"
TABLE_SCHEMA = "encoder-fingerprints-v1"
JPEG_SCAN_BYTES = 4 * 1024 * 1024
IJG_QUALITIES = range(1, 101)


class TableError(RuntimeError):
    """The encoder-fingerprint table cannot be read (Korean)."""


@dataclass(frozen=True)
class Table:
    version: str
    sha256: str
    data: dict[str, Any]

    @property
    def label(self) -> str:
        return f"인코더 지문 표 {self.version}(sha256 {self.sha256[:12]}…)"


_TABLES: dict[tuple[str, int, int], Table] = {}


def load_table(path: Path | None = None) -> Table:
    target = path or TABLE_PATH
    try:
        info = target.stat()
        key = (str(target), info.st_mtime_ns, info.st_size)
        cached = _TABLES.get(key)
        if cached is not None:
            return cached
        raw = target.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise TableError(f"인코더 지문 표를 읽을 수 없습니다: {type(exc).__name__}") from exc
    if not isinstance(data, dict) or data.get("schema") != TABLE_SCHEMA:
        raise TableError(f"인코더 지문 표 형식이 {TABLE_SCHEMA}가 아닙니다")
    table = Table(str(data.get("version", "")), hashlib.sha256(raw).hexdigest(), data)
    _TABLES[key] = table
    return table


def table_cache_tokens() -> list[str]:
    """Scan-cache token of the table (a new table version re-analyzes); never raises."""
    try:
        table = load_table()
    except TableError as exc:
        return [f"encoder-fingerprints:error:{exc}"]
    return [f"encoder-fingerprints:{table.version}:{table.sha256}"]


# --- JPEG: IJG standard tables ---------------------------------------------------------


def ijg_scaled(base: list[int], quality: int, *, force_baseline: bool = True) -> list[int]:
    """IJG jcparam.c: jpeg_quality_scaling + jpeg_add_quant_table for one base table (natural order)."""
    quality = max(1, min(100, quality))
    scale = 5000 // quality if quality < 50 else 200 - quality * 2
    top = 255 if force_baseline else 32767
    return [max(1, min(top, (value * scale + 50) // 100)) for value in base]


@dataclass(frozen=True)
class IjgComparison:
    qualities: tuple[int, ...]  # every IJG quality whose tables equal the file's (empty: none)
    nearest: int  # the quality with the fewest differing values
    differing: int  # values differing at ``nearest`` (0 when matched)
    compared: int  # 64 (luma only) or 128 (luma and chroma)

    @property
    def matched(self) -> bool:
        return bool(self.qualities)


def compare_ijg(luma: list[int], chroma: list[int] | None, table: Table) -> IjgComparison:
    standard = table.data.get("jpeg_ijg_standard") or {}
    base_luma: list[int] = [int(v) for v in standard.get("luma") or ()]
    base_chroma: list[int] = [int(v) for v in standard.get("chroma") or ()]
    if len(base_luma) != 64 or len(base_chroma) != 64:
        raise TableError("인코더 지문 표의 IJG 표준 표가 64개 값이 아닙니다")
    observed = luma + (chroma or [])
    qualities: list[int] = []
    nearest, best = 0, (len(observed) + 1, 0)
    for quality in IJG_QUALITIES:
        for force_baseline in (True, False):
            expected = ijg_scaled(base_luma, quality, force_baseline=force_baseline)
            if chroma is not None:
                expected += ijg_scaled(base_chroma, quality, force_baseline=force_baseline)
            differing = sum(1 for a, b in zip(observed, expected) if a != b)
            if differing == 0 and quality not in qualities:
                qualities.append(quality)
            score = (differing, sum(abs(a - b) for a, b in zip(observed, expected)))
            if score < best:
                nearest, best = quality, score
    return IjgComparison(tuple(qualities), nearest, best[0], len(observed))


def jpeg_tables(structure: "JpegStructure") -> tuple[list[int], list[int] | None]:
    """(luma, chroma) the first two components use; chroma None for a grayscale JPEG."""
    luma = structure.table_for(0) or structure.tables[min(structure.tables)]
    chroma = structure.table_for(1) if len(structure.components) >= 3 else None
    return luma, chroma


def jpeg_ijg_comparison(path: Path, table: Table) -> IjgComparison:
    """Raises CheckSkipped (not a JPEG / no DQT), OSError, TableError."""
    from .platform_traces import parse_jpeg
    from .versioned_cache import read_prefix

    luma, chroma = jpeg_tables(parse_jpeg(read_prefix(path, JPEG_SCAN_BYTES)))
    return compare_ijg(luma, chroma, table)


# --- Video: messenger re-encode formats ----------------------------------------------------


def messenger_video_matches(
    codec: str, profile: str | None, width: int, height: int, encoders: list[str], table: Table,
) -> list[dict[str, Any]]:
    """Entries whose codec, profile, coded size (either orientation) and encoder string all equal the file's."""
    found = {value.strip() for value in encoders}
    out = []
    for entry in table.data.get("messenger_video") or ():
        if not isinstance(entry, dict) or entry.get("codec") != codec or profile not in (entry.get("profiles") or ()):
            continue
        sizes = {tuple(size) for size in entry.get("sizes") or ()}
        if (width, height) not in sizes and (height, width) not in sizes:
            continue
        if str(entry.get("encoder") or "") in found:
            out.append(entry)
    return out


__all__ = [
    "IjgComparison",
    "TABLE_PATH",
    "Table",
    "TableError",
    "compare_ijg",
    "ijg_scaled",
    "jpeg_ijg_comparison",
    "jpeg_tables",
    "load_table",
    "messenger_video_matches",
    "table_cache_tokens",
]
