"""C2PA hard-binding (content hash) exclusion-range audit — P0R-20.

Threat (2026-10-02 disclosure, c2pa-rs up to 0.91.3 unfixed): a C2PA
hard-binding hash assertion (``c2pa.hash.data``, also ``c2pa.hash.boxes`` /
``c2pa.hash.bmff``) lists *exclusion* ranges — byte (or box) ranges that are
left out of the signed hash so the manifest can be embedded without
invalidating its own signature. A malicious signer can make those exclusions
cover the whole file (or everything except the manifest store), so an
"empty" content gets a cryptographically valid signature and a trusting
validator reports valid/trusted. c2pa-rs validates the exclusion math but
does **not** reject an exclusion that empties the asset.

This module is a pure, SDK-free audit of a manifest's hard-binding
assertions. It takes the assertion data (as decoded by the reader, or a
synthetic dict in a unit test) plus the file bytes, locates the manifest
store container in the file, and decides whether the exclusions leave a
trustworthy amount of asset content under the signature. It never imports
``c2pa`` and never raises for a malformed assertion — a parse problem is an
anomaly (fail closed), not a silent pass.

Rules (P0R-20):

(a) exclusions covering >= the whole file, or leaving fewer than
    :data:`MIN_HASHED_CONTENT_BYTES` content bytes under the hash, or
    covering more than :data:`STORE_SLACK_BYTES` bytes outside the manifest
    store container, are untrustworthy;
(b) overlapping, out-of-range or negative exclusions are untrustworthy;
(c) for BMFF, any exclusion xpath other than :data:`BMFF_ALLOWED_XPATHS` is
    untrustworthy;
(d) a manifest with no hard-binding assertion, in a container where one is
    required, is untrustworthy.
"""

from __future__ import annotations

import os
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import BinaryIO

# --- Pinned constants (with sources) ---------------------------------------

# Labels of the C2PA hard-binding (content hash) assertions. C2PA 2.x spec
# §"Hard Bindings": c2pa.hash.data (byte-range data hash, JPEG/PNG/TIFF/PDF/…),
# c2pa.hash.bmff (BMFF/ISO-BMFF: MP4/MOV/HEIF/AVIF), c2pa.hash.boxes (box hash),
# c2pa.hash.collection (collection hash, 2.1+). Versions are matched by prefix
# so c2pa.hash.data.v2 / c2pa.hash.bmff.v3 are covered.
DATA_HASH_PREFIX = "c2pa.hash.data"
BMFF_HASH_PREFIX = "c2pa.hash.bmff"
BOXES_HASH_PREFIX = "c2pa.hash.boxes"
COLLECTION_HASH_PREFIX = "c2pa.hash.collection"
HARD_BINDING_PREFIXES = (DATA_HASH_PREFIX, BMFF_HASH_PREFIX, BOXES_HASH_PREFIX, COLLECTION_HASH_PREFIX)

# A trustworthy data-hash must leave at least this many asset bytes under the
# signature. A whole-file exclusion leaves 0; the disclosed attack leaves a
# handful. 64 bytes is far below any real image's content yet rejects an
# emptied asset. (Documented slack, not a C2PA constant.)
MIN_HASHED_CONTENT_BYTES = 64

# How many excluded bytes may legitimately fall outside the located manifest
# store container — the segment/chunk framing around the store (JPEG APP11
# marker+length+CI+box-instance headers can repeat per 64 KiB segment; PNG
# caBX framing). 64 bytes covers multi-segment JPEG framing while still
# rejecting any exclusion that reaches into real asset content.
STORE_SLACK_BYTES = 64

# BMFF hard-binding exclusion xpaths that C2PA permits. C2PA 2.x spec,
# "BMFF-based Hash" assertion: the C2PA manifest box (a top-level ``uuid`` box
# carrying the C2PA UUID) is excluded, and the file-type box and movie-fragment
# random-access box are allowed as framing/offset-correction exclusions. Any
# other xpath (e.g. ``/mdat`` or a ``/moov/trak`` media box) would drop real
# content from the hash and is untrustworthy. Kept as a pinned allowlist.
BMFF_ALLOWED_XPATHS = frozenset({"/uuid", "/ftyp", "/mfra"})

# Container kinds we can locate a store in (so rule (d) can require a binding).
CONTAINER_PNG = "png"
CONTAINER_JPEG = "jpeg"
CONTAINER_BMFF = "bmff"
CONTAINER_UNKNOWN = "unknown"

BINDING_ANOMALY = "C2PA 해시 제외범위 이상"
_TAIL = "서명 유효 여부와 무관하게 출처 근거로 쓰지 않습니다"


@dataclass(frozen=True)
class HardBindingVerdict:
    """Outcome of auditing a manifest's hard-binding assertion(s)."""

    ok: bool
    kind: str  # "data" | "bmff" | "boxes" | "collection" | "missing" | "parse" | "none"
    reason: str  # Korean; "" when ok
    file_size: int = 0
    excluded_bytes: int = 0
    excluded_outside_store: int = 0
    hashed_bytes: int = 0
    store_located: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "ok": self.ok, "kind": self.kind, "reason": self.reason,
            "file_size": self.file_size, "excluded_bytes": self.excluded_bytes,
            "excluded_outside_store": self.excluded_outside_store,
            "hashed_bytes": self.hashed_bytes, "store_located": self.store_located,
        }


def _ok(kind: str, **metrics: object) -> HardBindingVerdict:
    return HardBindingVerdict(True, kind, "", **metrics)  # type: ignore[arg-type]


def _bad(kind: str, reason: str, **metrics: object) -> HardBindingVerdict:
    return HardBindingVerdict(False, kind, reason, **metrics)  # type: ignore[arg-type]


# --- Range math ------------------------------------------------------------

def _parse_exclusions(raw: object) -> list[tuple[int, int]]:
    """``[(start, length), …]`` from an exclusions list. Raises ValueError on a
    malformed entry (missing/negative/non-int start or length) — fail closed."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("exclusions가 목록이 아님")
    out: list[tuple[int, int]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise ValueError("제외 항목이 사전이 아님")
        start, length = entry.get("start"), entry.get("length")
        if not isinstance(start, int) or isinstance(start, bool) or not isinstance(length, int) or isinstance(length, bool):
            raise ValueError("제외 항목의 start/length가 정수가 아님")
        if start < 0 or length < 0:
            raise ValueError("제외 항목의 start/length가 음수")
        out.append((start, length))
    return out


def _merge(ranges: list[tuple[int, int]]) -> tuple[list[tuple[int, int]], bool]:
    """Merge to ``[(start, end), …]``; second value is True if any two ranges overlapped."""
    spans = sorted((s, s + ln) for s, ln in ranges)
    merged: list[tuple[int, int]] = []
    overlap = False
    for s, e in spans:
        if merged and s < merged[-1][1]:
            overlap = True
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        elif merged and s == merged[-1][1]:
            merged[-1] = (merged[-1][0], e)  # adjacent, not an overlap
        else:
            merged.append((s, e))
    return merged, overlap


def _bytes_outside(spans: list[tuple[int, int]], store: list[tuple[int, int]]) -> int:
    """Total length of ``spans`` lying outside every ``store`` range."""
    store_merged, _ = _merge([(s, e - s) for s, e in store]) if store else ([], False)
    outside = 0
    for s, e in spans:
        pos = s
        for cs, ce in store_merged:
            if ce <= pos or cs >= e:
                continue
            if cs > pos:
                outside += cs - pos
            pos = max(pos, ce)
            if pos >= e:
                break
        if pos < e:
            outside += e - pos
    return outside


def evaluate_data_hash(
    exclusions: object, *, file_size: int, store_ranges: list[tuple[int, int]], store_located: bool,
) -> HardBindingVerdict:
    """Audit a ``c2pa.hash.data`` assertion's byte exclusions (rules a, b)."""
    try:
        parsed = _parse_exclusions(exclusions)
    except ValueError as exc:
        return _bad("parse", f"{BINDING_ANOMALY} — 하드 바인딩 assertion 해석 실패({exc}) — {_TAIL}",
                    file_size=file_size, store_located=store_located)
    merged, overlap = _merge(parsed)
    out_of_range = any(e > file_size for _, e in merged)
    if overlap or out_of_range:
        detail = "제외 구간이 겹침" if overlap else f"제외 구간이 파일 크기({file_size})를 벗어남"
        return _bad("data", f"{BINDING_ANOMALY} — {detail} — {_TAIL}",
                    file_size=file_size, store_located=store_located)
    clipped = [(s, min(e, file_size)) for s, e in merged]
    excluded = sum(e - s for s, e in clipped)
    hashed = file_size - excluded
    outside = _bytes_outside(clipped, store_ranges) if store_located else 0
    metrics = dict(file_size=file_size, excluded_bytes=excluded, excluded_outside_store=outside,
                   hashed_bytes=hashed, store_located=store_located)
    if hashed < MIN_HASHED_CONTENT_BYTES:
        return _bad("data", f"{BINDING_ANOMALY} — 서명 대상에서 파일 내용 {file_size}바이트 중 {excluded}바이트가 제외됨"
                    f"(매니페스트 저장소 밖 제외 {outside}바이트) — {_TAIL}", **metrics)
    if store_located and outside > STORE_SLACK_BYTES:
        return _bad("data", f"{BINDING_ANOMALY} — 서명 대상에서 파일 내용 {file_size}바이트 중 {excluded}바이트가 제외됨"
                    f"(매니페스트 저장소 밖 제외 {outside}바이트) — {_TAIL}", **metrics)
    return _ok("data", **metrics)


def evaluate_bmff_hash(exclusions: object, *, file_size: int = 0) -> HardBindingVerdict:
    """Audit a ``c2pa.hash.bmff`` assertion's box-xpath exclusions (rule c)."""
    if not isinstance(exclusions, Sequence) or isinstance(exclusions, (str, bytes)):
        return _bad("parse", f"{BINDING_ANOMALY} — BMFF 하드 바인딩 assertion 해석 실패(exclusions가 목록이 아님) — {_TAIL}",
                    file_size=file_size)
    disallowed: list[str] = []
    for entry in exclusions:
        xpath = entry.get("xpath") if isinstance(entry, Mapping) else None
        if not isinstance(xpath, str) or not xpath:
            return _bad("parse", f"{BINDING_ANOMALY} — BMFF 제외 항목에 xpath가 없음 — {_TAIL}", file_size=file_size)
        top = "/" + xpath.lstrip("/").split("/", 1)[0]
        if top not in BMFF_ALLOWED_XPATHS:
            disallowed.append(xpath)
    if disallowed:
        return _bad("bmff", f"{BINDING_ANOMALY} — 허용되지 않은 BMFF 제외 경로({', '.join(disallowed)}) — "
                    f"허용 경로: {', '.join(sorted(BMFF_ALLOWED_XPATHS))} — {_TAIL}", file_size=file_size)
    return _ok("bmff", file_size=file_size)


# --- Store container location ----------------------------------------------
#
# The file is read through a seekable handle — chunk / segment headers only, never the whole file (a C2PA-signed
# video can be gigabytes): a PNG is walked chunk header by chunk header, a JPEG segment by segment up to its first
# start-of-scan.

# A walk that has not found the store within this many chunks / segments gives up (the store is then not located
# and only the generic byte rules apply — never a silent pass).
MAX_CONTAINER_WALK = 100_000


def locate_png_store(handle: BinaryIO, file_size: int) -> list[tuple[int, int]]:
    """Byte ranges of the PNG ``caBX`` chunk(s) (length+type+data+CRC) that hold the C2PA store."""
    handle.seek(0)
    if handle.read(8) != b"\x89PNG\r\n\x1a\n":
        return []
    ranges: list[tuple[int, int]] = []
    off = 8
    for _ in range(MAX_CONTAINER_WALK):
        if off + 8 > file_size:
            break
        handle.seek(off)
        header = handle.read(8)
        if len(header) < 8:
            break
        length, = struct.unpack(">I", header[:4])
        chunk_type = header[4:8]
        end = off + 12 + length
        if end > file_size:
            break
        if chunk_type == b"caBX":
            ranges.append((off, end))
        off = end
        if chunk_type == b"IEND":
            break
    return ranges


def locate_jpeg_store(handle: BinaryIO, file_size: int) -> list[tuple[int, int]]:
    """Byte ranges of the JPEG APP11 (0xFFEB) segments that carry the JUMBF C2PA store."""
    handle.seek(0)
    if handle.read(2) != b"\xff\xd8":
        return []
    ranges: list[tuple[int, int]] = []
    off = 2
    for _ in range(MAX_CONTAINER_WALK):
        if off + 4 > file_size:
            break
        handle.seek(off)
        header = handle.read(4)
        if len(header) < 4 or header[0] != 0xFF:
            break
        marker = header[1]
        if marker in (0xD8, 0xD9):
            off += 2
            continue
        if marker == 0xDA:  # start of scan — entropy data follows, no more segments to walk
            break
        length, = struct.unpack(">H", header[2:4])
        seg_end = off + 2 + length
        if seg_end > file_size:
            break
        if marker == 0xEB:
            ranges.append((off, seg_end))
        off = seg_end
    return ranges


def _container_of(head: bytes) -> str:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return CONTAINER_PNG
    if head[:2] == b"\xff\xd8":
        return CONTAINER_JPEG
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return CONTAINER_BMFF
    return CONTAINER_UNKNOWN


# --- Top-level audit -------------------------------------------------------

def find_hard_bindings(assertion_store: object) -> dict[str, object]:
    """``{label: data}`` for every hard-binding assertion in a reader's assertion store.

    ``assertion_store`` is the ``detailed_json`` active-manifest ``assertion_store``
    mapping (label -> data), or any mapping of the same shape (unit tests)."""
    found: dict[str, object] = {}
    if not isinstance(assertion_store, Mapping):
        return found
    for label, data in assertion_store.items():
        text = str(label)
        if any(text.startswith(prefix) for prefix in HARD_BINDING_PREFIXES):
            found[text] = data
    return found


def audit_assertion_store(assertion_store: object, path: str | os.PathLike[str]) -> HardBindingVerdict:
    """Audit the hard-binding assertion(s) of an active manifest against the file bytes.

    Fail-closed: a missing binding (in a container that requires one), a
    malformed assertion, or any rule violation returns ``ok=False`` with a
    Korean reason. A container we cannot parse is audited with the generic
    byte rules only (no store-location rule, no required-binding rule).
    """
    try:
        with open(path, "rb") as handle:
            return _audit(assertion_store, handle, os.fstat(handle.fileno()).st_size)
    except OSError as exc:
        return _bad("parse", f"{BINDING_ANOMALY} — 하드 바인딩 확인을 위해 파일을 읽지 못함({type(exc).__name__}) — {_TAIL}")


def _audit(assertion_store: object, handle: BinaryIO, file_size: int) -> HardBindingVerdict:
    handle.seek(0)
    container = _container_of(handle.read(12))
    bindings = find_hard_bindings(assertion_store)
    if not bindings:
        if container in (CONTAINER_PNG, CONTAINER_JPEG, CONTAINER_BMFF):
            return _bad("missing", f"C2PA 하드 바인딩(콘텐츠 해시) assertion 없음 — 서명이 파일 내용에 묶여 있지 않습니다 — {_TAIL}",
                        file_size=file_size)
        # A container whose required binding we cannot name (WAV, MP3, PDF, …): absence is not asserted.
        return _ok("none", file_size=file_size)

    data_label = next((lbl for lbl in bindings if lbl.startswith(DATA_HASH_PREFIX)), None)
    if data_label is not None:
        assertion = bindings[data_label]
        if not isinstance(assertion, Mapping):
            return _bad("parse", f"{BINDING_ANOMALY} — 하드 바인딩 assertion이 사전이 아님 — {_TAIL}", file_size=file_size)
        if container == CONTAINER_PNG:
            store, located = locate_png_store(handle, file_size), True
        elif container == CONTAINER_JPEG:
            store, located = locate_jpeg_store(handle, file_size), True
        else:
            store, located = [], False
        if located and not store:
            located = False  # store chunk/segment not found — apply generic rules only
        return evaluate_data_hash(assertion.get("exclusions"), file_size=file_size,
                                  store_ranges=store, store_located=located)

    bmff_label = next((lbl for lbl in bindings if lbl.startswith(BMFF_HASH_PREFIX)), None)
    if bmff_label is not None:
        assertion = bindings[bmff_label]
        if not isinstance(assertion, Mapping):
            return _bad("parse", f"{BINDING_ANOMALY} — BMFF 하드 바인딩 assertion이 사전이 아님 — {_TAIL}", file_size=file_size)
        return evaluate_bmff_hash(assertion.get("exclusions"), file_size=file_size)

    # boxes/collection hash present: no byte/xpath exclusion attack modelled here;
    # the binding exists, so it is not the "missing" case. Treated as acceptable.
    return _ok("boxes", file_size=file_size)
