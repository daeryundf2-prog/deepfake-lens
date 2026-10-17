"""Device AI-edit labels (TB-3; threat catalogue S-GALAXY-AI, S-GOOGLE-PHOTOS).

Phones write what their gallery / AI editor did into the file. Only field names
and values with a citable source are read (sources in ``models/device-labels.json``):

- **Samsung SEFT trailer** (bytes after a JPEG's EOI or a PNG's IEND ending in
  ``SEFT``; layout from ExifTool ``lib/Image/ExifTool/Samsung.pm`` 1.62
  ``ProcessSamsung`` — little-endian ``SEFH`` directory of (type, negative
  offset, size) entries, each block ``[type][name length][name][data]``). The
  entry names are listed as a fact. Two structures are AI-related *candidates*
  whose meaning no public source documents: ``PhotoEditor_Re_Edit_Data`` JSON
  keys ``isScaleAI`` / ``isAIFilterReEditOnly`` and ``PEg_Info``
  (``genImageVersion``, ``connectorType``). They are shown as "의미 미확인";
  no entry name for Galaxy AI Generative Edit is documented (ExifTool has none),
  so none is claimed. A Samsung C2PA manifest is the ``c2pa`` check's (the
  existing C2PA rules).
- **Google Photos / Gemini credit labels**: the XMP ``photoshop:Credit`` values
  "Edited with Google AI" (Google Photos AI edits — Magic Editor, Magic Eraser,
  Zoom Enhance), "Made with Google AI" (Gemini; IPTC 2024-10-28) and "Made by
  Google AI" (as reported for Pixel Studio). The IPTC digital source type
  written with them is already read by the XMP rules (``xmp_evidence``,
  unchanged here).

Semantics: unsigned device labels are **self-declarations**. A Google AI label
is a synthetic-direction item of at most moderate strength (it blocks an
authenticity conclusion, never concludes); Samsung trailer names are neutral
facts. Absence of a label says nothing (labels are dropped by re-saving and
by messengers).
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .result_types import CoverageEntry, CoverageStatus, EvidenceDirection, EvidenceItem, EvidenceKind, EvidenceStrength

DEVICE_LABEL_CHECK = "device_ai_label"
DEVICE_LAYER = "device_label"
TABLE_PATH = Path(__file__).resolve().parent / "models" / "device-labels.json"
TABLE_SCHEMA = "device-labels-v1"
MAX_TRAILER_BLOCK = 0x10000  # ExifTool's bound on the SEFT directory length
MAX_ENTRIES = 64
MAX_DATA = 1024 * 1024
MAX_NAMES = 16


@dataclass
class SeftEntry:
    type_id: int
    name: str
    json_keys: dict[str, Any] = field(default_factory=dict)


def _printable(raw: bytes) -> str:
    text = raw.decode("ascii", errors="replace")
    return "".join(ch if ch.isprintable() and ch != "�" else "?" for ch in text)[:64]


def read_seft(path: Path, *, wanted: frozenset[str] = frozenset()) -> list[SeftEntry] | None:
    """The SEFT directory entries of ``path`` (None when there is no SEFT trailer at the end of the file).

    ``wanted``: entry names whose data is parsed as JSON (top-level scalar keys kept).
    """
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        if size < 16:
            return None
        handle.seek(size - 8)
        tail = handle.read(8)
        if tail[4:] != b"SEFT":
            return None
        length = struct.unpack("<I", tail[:4])[0]
        if not 12 <= length < MAX_TRAILER_BLOCK or length + 8 > size:
            return None
        dir_pos = size - 8 - length
        handle.seek(dir_pos)
        directory = handle.read(length)
        if not directory.startswith(b"SEFH"):
            return None
        count = struct.unpack_from("<I", directory, 8)[0]
        if 12 + 12 * count > len(directory) or count > MAX_ENTRIES:
            return None
        entries: list[SeftEntry] = []
        for index in range(count):
            base = 12 + 12 * index
            type_id, noff, block = struct.unpack_from("<HII", directory, base + 2)
            if noff > dir_pos or block > noff or block < 8:
                break
            handle.seek(dir_pos - noff)
            head = handle.read(min(block, 8 + 256))
            name_len = struct.unpack_from("<I", head, 4)[0]
            if name_len + 8 > block or name_len > 256:
                break
            name = _printable(head[8:8 + name_len])
            entry = SeftEntry(type_id, name)
            if name in wanted and block - 8 - name_len <= MAX_DATA:
                handle.seek(dir_pos - noff + 8 + name_len)
                data = handle.read(block - 8 - name_len)
                try:
                    parsed = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict):
                    entry.json_keys = {k: v for k, v in parsed.items() if isinstance(k, str) and isinstance(v, (str, int, float, bool))}
            entries.append(entry)
        return entries


@dataclass(frozen=True)
class LabelTable:
    version: str
    sha256: str
    data: dict[str, Any]

    @property
    def label(self) -> str:
        return f"기기 라벨 표 {self.version}(sha256 {self.sha256[:12]}…)"


def load_table() -> LabelTable:
    raw = TABLE_PATH.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or data.get("schema") != TABLE_SCHEMA:
        raise ValueError(f"기기 라벨 표 형식이 {TABLE_SCHEMA}가 아닙니다")
    return LabelTable(str(data.get("version", "")), hashlib.sha256(raw).hexdigest(), data)


def table_cache_tokens() -> list[str]:
    try:
        table = load_table()
    except (OSError, ValueError) as exc:
        return [f"device-labels:error:{type(exc).__name__}"]
    return [f"device-labels:{table.version}:{table.sha256}"]


@dataclass
class DeviceLayers:
    evidence: list[EvidenceItem] = field(default_factory=list)
    coverage: list[CoverageEntry] = field(default_factory=list)


def _evaluate(path: Path, metadata: Mapping[str, str]) -> tuple[list[EvidenceItem], str]:
    table = load_table()
    samsung = table.data.get("samsung_seft", {})
    candidates = {c["entry"]: c for c in samsung.get("ai_candidates", []) if isinstance(c, dict) and "entry" in c}
    known = {k["entry"]: k["meaning"] for k in samsung.get("known_entries", []) if isinstance(k, dict)}
    items: list[EvidenceItem] = []
    parts: list[str] = []
    entries = read_seft(path, wanted=frozenset(candidates))
    if entries is None:
        parts.append("삼성 SEFT 트레일러 없음")
    else:
        names = list(dict.fromkeys(entry.name for entry in entries))
        parts.append(f"삼성 SEFT 트레일러 항목 {len(entries)}개")
        shown = ", ".join(f"「{name}」" + (f"({known[name]})" if name in known else "") for name in names[:MAX_NAMES])
        items.append(EvidenceItem(
            "삼성 기기 편집·촬영 기록(SEFT 트레일러)",
            f"파일 끝에 삼성 갤러리·카메라가 쓰는 SEFT 트레일러가 있고 항목 이름은 {shown}"
            + (f" 외 {len(names) - MAX_NAMES}개" if len(names) > MAX_NAMES else "") + "입니다. "
            "삼성 기기(또는 그 형식을 흉내 낸 프로그램)가 저장했다는 구조 사실이며, 항목의 존재가 AI 편집을 뜻하지는 않습니다"
            "(갤럭시 AI 생성형 편집 전용 항목 이름은 공개 자료에 없음). " + str(table.data.get("self_declared_note", "")) + f" 기준: {table.label}.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, DEVICE_LAYER, self_declared=True,
        ))
        for entry in entries:
            candidate = candidates.get(entry.name)
            if candidate is None:
                continue
            keys = [str(k) for k in candidate.get("keys", [])]
            values = {k: entry.json_keys[k] for k in keys if k in entry.json_keys}
            if not values and candidate.get("require_keys", True):
                continue
            listed = ", ".join(f"{k}=「{v}」" for k, v in values.items()) or "값 없음"
            parts.append(f"AI 관련 후보 「{entry.name}」")
            items.append(EvidenceItem(
                "삼성 편집 기록의 AI 관련 후보 필드(의미 미확인)",
                f"SEFT 항목 「{entry.name}」에 {listed}이(가) 있습니다. 이름이 AI 처리를 시사하지만 의미를 확인한 공개 자료가 없어 "
                "AI 편집 근거로 쓰지 않고 사실로만 적습니다.",
                EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, DEVICE_LAYER, self_declared=True,
            ))
    credit = metadata.get("xmp.Credit", "").strip()
    labels = {str(c["value"]).casefold(): c for c in table.data.get("credit_labels", []) if isinstance(c, dict) and "value" in c}
    found = labels.get(credit.casefold()) if credit else None
    if found is not None:
        parts.append(f"XMP 라벨 「{credit}」")
        items.append(EvidenceItem(
            str(found.get("title")),
            f"XMP photoshop:Credit=「{credit}」 — {found.get('meaning')}(신뢰 수준: {found.get('confidence')}). 서명 없는 기기·앱의 자기 신고이므로 "
            "원본성 결론만 막고 조작·생성 결론은 내리지 않습니다(같은 파일의 디지털 출처 유형 필드는 따로 기록).",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.MODERATE, DEVICE_LAYER, self_declared=True,
        ))
    else:
        parts.append("Google AI 라벨 없음")
    return items, "; ".join(parts) + f"({table.label})"


def device_label_layers(path: Path, metadata: Mapping[str, str]) -> DeviceLayers:
    from .checks import run_check

    layers = DeviceLayers()
    outcome, entry = run_check(DEVICE_LABEL_CHECK, lambda: _evaluate(path, metadata))
    if outcome is not None:
        items, reason = outcome
        layers.evidence.extend(items)
        entry = CoverageEntry(DEVICE_LABEL_CHECK, CoverageStatus.RAN, reason)
    layers.coverage.append(entry)
    return layers


__all__ = ["DEVICE_LABEL_CHECK", "SeftEntry", "device_label_layers", "load_table", "read_seft", "table_cache_tokens"]
