"""Document structure facts (TB-6; threat catalogue M-IMG-06, M-DOC-01).

Structure a document file carries about its own editing history — never a
verdict, never a statement about the content:

- **PDF** (ISO 32000-1 §7.5.6 "Incremental Updates", §7.5.8 cross-reference
  streams, §12.8.1 signature ``/ByteRange``, Annex F linearization): the number
  of saved revisions (``%%EOF`` markers; a linearized file's first-page section
  is not a revision), cross-reference tables / streams and ``/Prev`` links, the
  bytes after the end of every signature's ``/ByteRange`` (a save after
  signing), bytes after the last ``%%EOF``, every ``/Producer`` / ``/Creator``
  literal and the XMP ``pdf:Producer`` / ``xmp:CreatorTool`` /
  ``stEvt:softwareAgent`` values in file order (the tool history), and an
  embedded file declared ``application/c2pa`` (C2PA's PDF embedding; the
  ``c2pa`` check validates it). Objects inside compressed object streams
  (``/ObjStm``) are not read — said in the reason.
- **DOCX/XLSX/PPTX** (ECMA-376): ``docProps/core.xml`` revision and times,
  ``docProps/app.xml`` Application / AppVersion / TotalTime / Template, the
  number of revision-save identifiers (``w:rsid`` in ``word/settings.xml`` —
  Word adds one per editing session saved), tracked insertions/deletions and
  comments.
- HWP / legacy OLE (.doc/.xls/.ppt) are skipped with the reason: no reader in
  this build verified against real samples.

All of it is written by the producing software and can be edited by anyone:
each finding is a deterministic, neutral, weak **fact** ("자기 신고"), shown
in corner brackets verbatim. A document with a long history is not forged; a document
without one is not original.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .check_skipped import CheckSkipped
from .result_types import CoverageEntry, CoverageStatus, EvidenceDirection, EvidenceItem, EvidenceKind, EvidenceStrength

DOCUMENT_STRUCTURE_CHECK = "document_structure"
DOCUMENT_STRUCTURE_LAYER = "document_structure"
MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_VALUES = 8
MAX_VALUE_CHARS = 120
OOXML_EXTENSIONS = frozenset({".docx", ".xlsx", ".pptx"})
UNREAD_EXTENSIONS = {
    ".hwp": "HWP 구조 검사는 이 버전에서 하지 않음(실제 표본으로 검증한 판독기 없음)",
    ".doc": "구형 OLE 문서 구조 검사는 이 버전에서 하지 않음(실제 표본으로 검증한 판독기 없음)",
    ".xls": "구형 OLE 문서 구조 검사는 이 버전에서 하지 않음(실제 표본으로 검증한 판독기 없음)",
    ".ppt": "구형 OLE 문서 구조 검사는 이 버전에서 하지 않음(실제 표본으로 검증한 판독기 없음)",
}
SELF_DECLARED = "문서 소프트웨어가 기록한 값(자기 신고)이며 누구나 고칠 수 있어 위조·원본 여부의 근거가 아니라 구조 사실입니다."
STRUCTURE_LIMITATION = (
    "문서 구조 사실(증분 저장 횟수·작성 도구 이력·수정 메타데이터)은 편집 이력의 흔적일 뿐 위조나 원본성의 근거가 아닙니다 — "
    "정상 문서도 여러 번 저장되며, 이력은 다시 저장하면 사라집니다."
)


@dataclass
class PdfFacts:
    size: int = 0
    eof_markers: int = 0
    linearized: bool = False
    xref_tables: int = 0
    xref_streams: int = 0
    prev_links: int = 0
    object_streams: bool = False
    signatures: list[tuple[int, int]] = field(default_factory=list)  # (signed end, bytes after it)
    trailing_after_eof: int = 0
    tools: list[tuple[str, str]] = field(default_factory=list)  # (field, value) in file order
    c2pa_embedded: bool = False

    @property
    def changed_tool_fields(self) -> list[str]:
        """Tool fields (Producer, Creator, XMP history) holding two or more different values across the file."""
        groups: dict[str, set[str]] = {}
        for name, value in self.tools:
            groups.setdefault(TOOL_FIELD_GROUPS.get(name, name), set()).add(value)
        return sorted(group for group, values in groups.items() if len(values) >= 2)

    @property
    def revisions(self) -> int:
        if self.eof_markers == 0:
            return 0
        return self.eof_markers - 1 if self.linearized and self.eof_markers >= 2 else self.eof_markers


# Info-dictionary keys and their XMP equivalents (XMP Specification Part 2, Adobe PDF namespace).
TOOL_FIELD_GROUPS = {"Producer": "Producer", "XMP pdf:Producer": "Producer", "Creator": "Creator", "XMP xmp:CreatorTool": "Creator",
                     "XMP stEvt:softwareAgent": "XMP 편집 이력(stEvt:softwareAgent)"}
_EOF = re.compile(rb"%%EOF")
_XREF_TABLE = re.compile(rb"(?:^|[\r\n])xref\s")
_XREF_STREAM = re.compile(rb"/Type\s*/XRef\b")
_PREV = re.compile(rb"/Prev\s+\d+")
_BYTERANGE = re.compile(rb"/ByteRange\s*\[\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\]")
_INFO_LITERAL = re.compile(rb"/(Producer|Creator)\s*\(((?:\\.|[^\\)])*)\)", re.S)
_INFO_HEX = re.compile(rb"/(Producer|Creator)\s*<([0-9A-Fa-f\s]*)>")
_XMP_VALUES = re.compile(
    rb"<(pdf:Producer|xmp:CreatorTool)>([^<]{1,512})</\1>|(xmp:CreatorTool|pdf:Producer|stEvt:softwareAgent)=\"([^\"]{1,512})\"",
)
_C2PA_EMBEDDED = re.compile(rb"/Subtype\s*/application#2Fc2pa\b|/Subtype\s*/application/c2pa\b")
_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}


def _pdf_literal(raw: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(raw):
        ch = raw[i:i + 1]
        if ch == b"\\" and i + 1 < len(raw):
            nxt = raw[i + 1:i + 2]
            if nxt in _ESCAPES:
                out += _ESCAPES[nxt]
                i += 2
                continue
            octal = re.match(rb"[0-7]{1,3}", raw[i + 1:i + 4])
            if octal:
                out.append(int(octal.group(0), 8) & 0xFF)
                i += 1 + len(octal.group(0))
                continue
            i += 1
            continue
        out += ch
        i += 1
    return bytes(out)


def _pdf_text(data: bytes) -> str:
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    return data.decode("latin-1")


def _clean(value: str) -> str:
    text = "".join(ch if ch.isprintable() else " " for ch in value).strip()
    return text[:MAX_VALUE_CHARS] + ("…" if len(text) > MAX_VALUE_CHARS else "")


def pdf_facts(data: bytes) -> PdfFacts:
    facts = PdfFacts(size=len(data))
    eofs = [m.end() for m in _EOF.finditer(data)]
    facts.eof_markers = len(eofs)
    facts.linearized = b"/Linearized" in data[:2048]
    facts.xref_tables = len(_XREF_TABLE.findall(data))
    facts.xref_streams = len(_XREF_STREAM.findall(data))
    facts.prev_links = len(_PREV.findall(data))
    facts.object_streams = b"/ObjStm" in data
    if eofs:
        facts.trailing_after_eof = len(data[eofs[-1]:].strip(b"\r\n\t \x00"))
    seen: set[tuple[int, int, int, int]] = set()
    for match in _BYTERANGE.finditer(data):
        a, b, c, d = (int(x) for x in match.groups())
        if (a, b, c, d) in seen:
            continue
        seen.add((a, b, c, d))
        end = c + d
        facts.signatures.append((end, max(0, len(data) - end)))
    tools: list[tuple[int, str, str]] = []
    for match in _INFO_LITERAL.finditer(data):
        tools.append((match.start(), match.group(1).decode(), _pdf_text(_pdf_literal(match.group(2)))))
    for match in _INFO_HEX.finditer(data):
        hexdigits = re.sub(rb"\s", b"", match.group(2))
        if len(hexdigits) % 2:
            hexdigits += b"0"
        try:
            tools.append((match.start(), match.group(1).decode(), _pdf_text(bytes.fromhex(hexdigits.decode()))))
        except ValueError:
            continue
    for match in _XMP_VALUES.finditer(data):
        name = (match.group(1) or match.group(3)).decode()
        raw = match.group(2) or match.group(4)
        tools.append((match.start(), f"XMP {name}", raw.decode("utf-8", errors="replace")))
    ordered: list[tuple[str, str]] = []
    for _, name, value in sorted(tools, key=lambda item: item[0]):
        cleaned = _clean(value)
        if cleaned and (name, cleaned) not in ordered:
            ordered.append((name, cleaned))
    facts.tools = ordered[:32]
    facts.c2pa_embedded = bool(_C2PA_EMBEDDED.search(data))
    return facts


def pdf_evidence(facts: PdfFacts) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    revisions = facts.revisions
    xref = f"교차 참조 표 {facts.xref_tables}개·교차 참조 스트림 {facts.xref_streams}개, /Prev 연결 {facts.prev_links}개"
    if revisions >= 2:
        items.append(EvidenceItem(
            "PDF 증분 저장 구조",
            f"PDF가 저장 개정(revision) {revisions}개로 이루어져 있습니다 — 최초 저장 뒤 증분 저장 {revisions - 1}회"
            f"({xref}{', 선형화 파일의 첫 페이지 구획은 개정에서 제외' if facts.linearized else ''}). "
            "증분 저장은 양식 입력·주석·서명·편집 프로그램의 일반 저장에서도 생기는 정상 구조이며, 어떤 내용이 바뀌었는지는 판단하지 않습니다.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, DOCUMENT_STRUCTURE_LAYER,
        ))
    for index, (end, after) in enumerate(facts.signatures[:4], start=1):
        if after > 0:
            items.append(EvidenceItem(
                "PDF 전자서명 범위 뒤의 후속 저장",
                f"서명 {index}의 /ByteRange가 파일의 {end:,}바이트까지만 덮고, 그 뒤에 {after:,}바이트가 더 있습니다 — 서명 이후 증분 저장이 있었다는 구조 사실입니다. "
                "장기 검증 정보 추가·추가 서명 같은 정상 절차도 같은 구조를 만들므로, 서명 뒤 바이트가 내용 변경인지는 판단하지 않습니다(서명 검증은 하지 않음).",
                EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, DOCUMENT_STRUCTURE_LAYER,
            ))
    if facts.trailing_after_eof:
        items.append(EvidenceItem(
            "PDF 끝 표식 뒤의 덧붙은 바이트",
            f"마지막 %%EOF 뒤에 공백이 아닌 바이트 {facts.trailing_after_eof:,}개가 있습니다 — PDF 판독기가 읽지 않는 덧붙은 데이터라는 구조 사실입니다.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, DOCUMENT_STRUCTURE_LAYER,
        ))
    if facts.changed_tool_fields:
        shown = "; ".join(f"{name} 「{value}」" for name, value in facts.tools[:MAX_VALUES])
        items.append(EvidenceItem(
            "PDF 작성 도구 기록 이력",
            f"같은 항목({', '.join(facts.changed_tool_fields)})에 서로 다른 작성·생성 도구 값이 기록되어 있습니다(파일 순서): {shown}"
            + (f" 외 {len(facts.tools) - MAX_VALUES}개" if len(facts.tools) > MAX_VALUES else "") + f". {SELF_DECLARED}",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, DOCUMENT_STRUCTURE_LAYER,
        ))
    if facts.c2pa_embedded:
        items.append(EvidenceItem(
            "PDF 내장 C2PA 매니페스트 저장소 표기",
            "PDF에 형식이 application/c2pa로 표기된 내장 파일이 있습니다(C2PA의 PDF 임베딩 방식). 서명·해시 검증 결과는 C2PA 출처 검증 항목이 따로 기록합니다.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, DOCUMENT_STRUCTURE_LAYER,
        ))
    return items


def _pdf_reason(facts: PdfFacts) -> str:
    if facts.eof_markers == 0:
        return "PDF 끝 표식(%%EOF) 없음 — 잘린 파일이거나 PDF 구조가 아님, 개정 수를 세지 않음"
    parts = [f"개정 {facts.revisions}개", f"서명 {len(facts.signatures)}개"]
    if any(after for _, after in facts.signatures):
        parts.append("서명 뒤 후속 저장 있음")
    parts.append(f"도구 값 {len(dict.fromkeys(v for _, v in facts.tools))}가지")
    if facts.c2pa_embedded:
        parts.append("C2PA 내장 파일 표기 있음")
    if facts.object_streams:
        parts.append("압축 객체 스트림 안의 값은 읽지 않음")
    return "PDF 구조: " + ", ".join(parts)


# --- OOXML ------------------------------------------------------------------

_CORE_FIELDS = {"cp:revision": "수정 번호(revision)", "dcterms:created": "생성 시각", "dcterms:modified": "수정 시각", "cp:lastPrinted": "마지막 인쇄 시각"}
_APP_FIELDS = {"Application": "작성 애플리케이션", "AppVersion": "애플리케이션 버전", "TotalTime": "총 편집 시간(분)", "Template": "서식 파일"}


@dataclass
class OoxmlFacts:
    fields: list[tuple[str, str]] = field(default_factory=list)
    rsids: int | None = None
    insertions: int = 0
    deletions: int = 0
    comments: int = 0
    created: str = ""
    modified: str = ""


def _xml_value(xml: str, tag: str) -> str:
    match = re.search(rf"<{re.escape(tag)}(?:\s[^>]*)?>([^<]*)</{re.escape(tag)}>", xml)
    return _clean(match.group(1)) if match else ""


def _member(zf: zipfile.ZipFile, name: str, cap: int = 16 * 1024 * 1024) -> str | None:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > cap:
        raise CheckSkipped(f"측정 범위 밖: 구성 파일 {name}이(가) {cap // (1024 * 1024)}MB보다 큼")
    return zf.read(info).decode("utf-8", errors="replace")


def ooxml_facts(path: Path) -> OoxmlFacts:
    facts = OoxmlFacts()
    with zipfile.ZipFile(path) as zf:
        core = _member(zf, "docProps/core.xml") or ""
        app = _member(zf, "docProps/app.xml") or ""
        for tag, label in _CORE_FIELDS.items():
            value = _xml_value(core, tag)
            if value:
                facts.fields.append((label, value))
        facts.created, facts.modified = _xml_value(core, "dcterms:created"), _xml_value(core, "dcterms:modified")
        for tag, label in _APP_FIELDS.items():
            value = _xml_value(app, tag)
            if value:
                facts.fields.append((label, value))
        settings = _member(zf, "word/settings.xml")
        if settings is not None:
            facts.rsids = len(re.findall(r"<w:rsid\s", settings))
        document = _member(zf, "word/document.xml")
        if document is not None:
            facts.insertions = len(re.findall(r"<w:ins\s", document))
            facts.deletions = len(re.findall(r"<w:del\s", document))
        comments = _member(zf, "word/comments.xml")
        if comments is not None:
            facts.comments = len(re.findall(r"<w:comment\s", comments))
    return facts


def ooxml_evidence(facts: OoxmlFacts) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    parts = [f"{label} 「{value}」" for label, value in facts.fields]
    if facts.rsids is not None:
        parts.append(f"편집 세션 식별자(w:rsid) {facts.rsids}개")
    if facts.insertions or facts.deletions:
        parts.append(f"변경 내용 추적 삽입 {facts.insertions}개·삭제 {facts.deletions}개")
    if facts.comments:
        parts.append(f"메모 {facts.comments}개")
    if parts:
        items.append(EvidenceItem(
            "문서 수정 메타데이터(자기 신고)",
            f"문서 패키지가 기록한 편집 이력: {'; '.join(parts)}. {SELF_DECLARED}",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, DOCUMENT_STRUCTURE_LAYER,
        ))
    if facts.created and facts.modified and facts.created[:19] > facts.modified[:19] and len(facts.created) >= 19 and len(facts.modified) >= 19:
        items.append(EvidenceItem(
            "문서 생성 시각이 수정 시각보다 늦음",
            f"docProps/core.xml의 생성 시각 「{facts.created}」이(가) 수정 시각 「{facts.modified}」보다 늦습니다 — 기록이 서로 맞지 않는다는 사실이며, "
            "복사·변환 도구가 흔히 만드는 상태라 위조 근거가 아닙니다.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, DOCUMENT_STRUCTURE_LAYER,
        ))
    return items


@dataclass
class StructureLayers:
    evidence: list[EvidenceItem] = field(default_factory=list)
    coverage: list[CoverageEntry] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


def _structure(path: Path) -> tuple[list[EvidenceItem], str]:
    extension = path.suffix.lower()
    if extension in UNREAD_EXTENSIONS:
        raise CheckSkipped(UNREAD_EXTENSIONS[extension])
    if extension == ".pdf":
        size = path.stat().st_size
        if size > MAX_PDF_BYTES:
            raise CheckSkipped(f"측정 범위 밖: 파일 크기 {size:,}바이트(상한 {MAX_PDF_BYTES:,}바이트)")
        with path.open("rb") as handle:
            data = handle.read(MAX_PDF_BYTES + 1)
        if not data.startswith(b"%PDF-") and b"%PDF-" not in data[:1024]:
            raise CheckSkipped("PDF 머리(%PDF-)가 없음 — PDF 구조로 읽지 않음")
        facts = pdf_facts(data)
        return pdf_evidence(facts), _pdf_reason(facts)
    if extension in OOXML_EXTENSIONS:
        try:
            facts_x = ooxml_facts(path)
        except zipfile.BadZipFile as exc:
            raise CheckSkipped(f"문서 압축 구조를 읽을 수 없음({type(exc).__name__}) — 구조 검사 생략") from exc
        summary = ", ".join(f"{label} 「{value}」" for label, value in facts_x.fields if label in ("수정 번호(revision)", "작성 애플리케이션")) or "수정 메타데이터 없음"
        if facts_x.rsids is not None:
            summary += f", w:rsid {facts_x.rsids}개"
        return ooxml_evidence(facts_x), f"문서 패키지 구조: {summary}"
    raise CheckSkipped("문서 구조 검사 대상 형식이 아님")


def document_structure_layers(path: Path) -> StructureLayers:
    """The ``document_structure`` check of one document row (facts only)."""
    from .checks import run_check

    layers = StructureLayers()
    outcome, entry = run_check(DOCUMENT_STRUCTURE_CHECK, lambda: _structure(path))
    if outcome is not None:
        evidence, reason = outcome
        entry = CoverageEntry(DOCUMENT_STRUCTURE_CHECK, CoverageStatus.RAN, reason)
        layers.evidence.extend(evidence)
        layers.limitations.append(STRUCTURE_LIMITATION)
    layers.coverage.append(entry)
    return layers


__all__ = [
    "DOCUMENT_STRUCTURE_CHECK",
    "OoxmlFacts",
    "PdfFacts",
    "document_structure_layers",
    "ooxml_evidence",
    "ooxml_facts",
    "pdf_evidence",
    "pdf_facts",
]
