"""Invisible Unicode marks in text (TB-1; threat catalogue M-TXT-03, M-TXT-01).

Text can carry machine-readable marks that a reader never sees:

- **Unicode Tags** (U+E0000–U+E007F, The Unicode Standard §23.9 "Tag
  Characters"): U+E0020–U+E007E mirror printable ASCII, so a run of them is a
  hidden ASCII string ("ASCII smuggling"). The one legitimate use left is the
  emoji tag sequence (UTS #51 ED-14a: U+1F3F4 WAVING BLACK FLAG + tag letters/
  digits + U+E007F CANCEL TAG, e.g. the flag of England) — counted, never a mark.
- **Variation selectors used as data**: VS1–VS16 (U+FE00–U+FE0F) and VS17–VS256
  (U+E0100–U+E01EF). A legitimate variation sequence is one base character +
  one selector (StandardizedVariants.txt uses VS1–VS7, emoji presentation
  VS15/VS16; ideographic variation sequences, UTS #37 / IVD 2025-07-14, use only
  U+E0100–U+E011F). Two or more selectors in a row, or one selector of
  U+E0120–U+E01EF (never in a registered sequence), are not a variation
  sequence; they are read as bytes with the public mapping VS1–VS16 →
  0x00–0x0F, VS17–VS256 → 0x10–0xFF (C2PA 2.3 Appendix A.7.3 / 2.4 A.8.3,
  Encypher's encypher-ai and Paul Butler's 2025 "smuggling" write-up).
- **C2PA text manifests** (C2PA 2.3 Appendix A.7 = 2.4 Appendix A.8): a
  ``C2PATextManifestWrapper`` — magic ``C2PATXT\0``, version (uint8), manifest
  length (uint32, big-endian as c2pa-rs and the MIT ``c2pa-text`` reference),
  JUMBF bytes — written as one variation-selector run after a U+FEFF; and
  (2.4 Appendix A.9, structured text such as Markdown) a block between
  ``-----BEGIN C2PA MANIFEST-----`` and ``-----END C2PA MANIFEST-----``.
- **Zero-width characters** (U+200B, U+200C, U+200D, U+2060, U+FEFF after the
  first character, U+180E, U+2061–U+2064): counted; a run of
  :data:`ZERO_WIDTH_RUN_MIN` or more in a row (a binary-encoding shape) is named.
  A ZWJ between two pictographs (an emoji ZWJ sequence) is not counted.

Semantics (phase-0 rules): every finding is a **fact** — a deterministic,
neutral, weak item. A mark says nothing about who wrote the text: anyone can
insert or strip one (copy-paste, editors and IMEs drop or add them), and text
results are reference-grade anyway (decision rule 1). The absence of a mark is
no information. A C2PA text-manifest wrapper is reported as present but its
signature is **not** validated: the bundled SDK (c2pa-python 0.38 / c2pa-rs
0.91) does not read text embeddings, so the ``c2pa`` check of a text file stays
skipped with that reason.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .media_marks import MarkFinding, MarkLayers
from .result_types import CoverageEntry, CoverageStatus, EvidenceDirection, EvidenceItem, EvidenceKind, EvidenceStrength

TEXT_MARKS_CHECK = "text_invisible_marks"
TEXT_MARKS_LAYER = "text_marks"
TEXT_MARKS_ALGORITHM = "비가시 유니코드 표식(태그 문자·변형 선택자 데이터열·제로폭 문자)"
C2PA_TEXT_ALGORITHM = "C2PA 텍스트 매니페스트(비정형 텍스트 래퍼·구조화 텍스트 블록, C2PA 2.3/2.4)"
C2PA_TEXT_CHECK = "c2pa_text_wrapper"

TAG_FIRST, TAG_LAST = 0xE0000, 0xE007F
TAG_ASCII_FIRST, TAG_ASCII_LAST = 0xE0020, 0xE007E
CANCEL_TAG = 0xE007F
BLACK_FLAG = 0x1F3F4
VS_BMP_FIRST, VS_BMP_LAST = 0xFE00, 0xFE0F
VS_SUP_FIRST, VS_SUP_LAST = 0xE0100, 0xE01EF
ZERO_WIDTH = frozenset({0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x180E, 0x2061, 0x2062, 0x2063, 0x2064})
ZWJ = 0x200D
ZERO_WIDTH_RUN_MIN = 4
# C2PA 2.3 §A.7 "Embedding manifests into unstructured text": the wrapper bytes
# start with this magic (see docs/MACHINE-READABLE-MARKS.md §8 for the source).
C2PA_TEXT_MAGIC = b"C2PATXT\x00"
C2PA_TEXT_MAGIC_SHOWN = "C2PATXT\\0"
C2PA_STRUCTURED_BEGIN = "-----BEGIN C2PA MANIFEST-----"
C2PA_STRUCTURED_END = "-----END C2PA MANIFEST-----"
IVS_LAST = 0xE011F  # last selector used by registered ideographic variation sequences
MAX_SHOWN = 120
MAX_RUNS = 16

NO_MARK = "비가시 유니코드 표식 없음 — 무정보(표식은 복사·붙여넣기·편집기로 쉽게 사라짐)"
MARK_CAVEAT = (
    "비가시 문자는 누구나 넣거나 지울 수 있고 복사·붙여넣기·편집기·입력기에서 생기거나 사라지므로 작성 주체나 AI 생성 여부의 근거가 아니라 "
    "파일에 들어 있다는 사실입니다."
)
TEXT_MARK_LIMITATION = (
    "비가시 유니코드 표식이 없다는 것은 사람이 썼다는 근거가 아닙니다 — 텍스트 워터마크·표식은 복사·붙여넣기와 편집으로 쉽게 사라집니다."
)


@dataclass
class TextMarkScan:
    """What :func:`scan_text` found (all counts are over the analyzed text)."""

    chars: int = 0
    tag_runs: list[tuple[int, str]] = field(default_factory=list)  # (offset, decoded)
    tag_chars: int = 0
    emoji_tag_sequences: int = 0
    vs_runs: list[tuple[int, bytes]] = field(default_factory=list)  # (offset, bytes)
    single_selectors: int = 0
    zero_width: dict[int, int] = field(default_factory=dict)
    zero_width_runs: list[tuple[int, int]] = field(default_factory=list)  # (offset, length)
    emoji_zwj: int = 0
    structured_blocks: list[int] = field(default_factory=list)  # offsets of C2PA 2.4 A.9 blocks

    @property
    def zero_width_total(self) -> int:
        return sum(self.zero_width.values())

    @property
    def c2pa_wrappers(self) -> list[tuple[int, bytes]]:
        return [(offset, data) for offset, data in self.vs_runs if data.startswith(C2PA_TEXT_MAGIC)]

    @property
    def found(self) -> bool:
        return bool(self.tag_runs or self.vs_runs or self.zero_width_total or self.structured_blocks)


def _is_vs(cp: int) -> bool:
    return VS_BMP_FIRST <= cp <= VS_BMP_LAST or VS_SUP_FIRST <= cp <= VS_SUP_LAST


def vs_byte(cp: int) -> int:
    """VS1–VS16 → 0x00–0x0F, VS17–VS256 → 0x10–0xFF."""
    return cp - VS_BMP_FIRST if cp <= VS_BMP_LAST else cp - VS_SUP_FIRST + 16


def _pictographic(ch: str) -> bool:
    cp = ord(ch)
    return unicodedata.category(ch) == "So" or 0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF


# R27-5: the characters scan_text's branches start on — tag characters, variation selectors (both blocks) and the
# ZERO_WIDTH set (U+FEFF among them); a per-character Python loop over the rest cost a text scan more than the
# text analysis itself.
_ACTED_ON = re.compile(
    "[" + "".join(
        f"\\U{low:08x}-\\U{high:08x}" for low, high in ((TAG_FIRST, TAG_LAST), (VS_BMP_FIRST, VS_BMP_LAST), (VS_SUP_FIRST, VS_SUP_LAST))
    ) + "".join(f"\\U{cp:08x}" for cp in sorted(ZERO_WIDTH)) + "]"
)


def scan_text(text: str) -> TextMarkScan:
    """Find the marks in ``text`` (pure, deterministic)."""
    out = TextMarkScan(chars=len(text))
    i, n = 0, len(text)
    while i < n:
        # R27-5: jump to the next character any branch below acts on — every other character only did "i += 1".
        found = _ACTED_ON.search(text, i)
        if found is None:
            break
        i = found.start()
        cp = ord(text[i])
        if TAG_FIRST <= cp <= TAG_LAST:
            j = i
            while j < n and TAG_FIRST <= ord(text[j]) <= TAG_LAST:
                j += 1
            run = text[i:j]
            body = [ord(c) for c in run]
            flag = (
                i > 0 and ord(text[i - 1]) == BLACK_FLAG and body[-1] == CANCEL_TAG and len(body) >= 2
                and all(0xE0030 <= c <= 0xE0039 or 0xE0061 <= c <= 0xE007A for c in body[:-1])
            )
            if flag:
                out.emoji_tag_sequences += 1
            else:
                out.tag_chars += len(body)
                if len(out.tag_runs) < MAX_RUNS:
                    decoded = "".join(
                        chr(c - TAG_FIRST) if TAG_ASCII_FIRST <= c <= TAG_ASCII_LAST else f"<U+{c:05X}>" for c in body
                    )
                    out.tag_runs.append((i, decoded))
            i = j
            continue
        if _is_vs(cp):
            j = i
            while j < n and _is_vs(ord(text[j])):
                j += 1
            if j - i >= 2 or cp > IVS_LAST:
                if len(out.vs_runs) < MAX_RUNS:
                    out.vs_runs.append((i, bytes(vs_byte(ord(c)) for c in text[i:j])))
            else:
                out.single_selectors += 1
            i = j
            continue
        if cp == 0xFEFF and i + 1 < n and _is_vs(ord(text[i + 1])):
            i += 1  # the C2PA wrapper's U+FEFF prefix (A.7.4.1) — part of the wrapper, not a zero-width mark
            continue
        if cp in ZERO_WIDTH and not (cp == 0xFEFF and i == 0):
            j = i
            while j < n and ord(text[j]) in ZERO_WIDTH:
                j += 1
            length = j - i
            if (
                length == 1 and cp == ZWJ and 0 < i < n - 1
                and (_pictographic(text[i - 1]) or _is_vs(ord(text[i - 1]))) and _pictographic(text[i + 1])
            ):
                out.emoji_zwj += 1
            else:
                for c in text[i:j]:
                    out.zero_width[ord(c)] = out.zero_width.get(ord(c), 0) + 1
                if length >= ZERO_WIDTH_RUN_MIN and len(out.zero_width_runs) < MAX_RUNS:
                    out.zero_width_runs.append((i, length))
            i = j
            continue
        i += 1
    start = text.find(C2PA_STRUCTURED_BEGIN)
    while start != -1 and len(out.structured_blocks) < MAX_RUNS:
        if text.find(C2PA_STRUCTURED_END, start) != -1:
            out.structured_blocks.append(start)
        start = text.find(C2PA_STRUCTURED_BEGIN, start + 1)
    return out


def _shown_bytes(data: bytes) -> str:
    head = data[:48].hex()
    text = ""
    try:
        decoded = data.decode("utf-8")
        if decoded and all(ch.isprintable() for ch in decoded):
            text = f", UTF-8 해석 「{decoded[:MAX_SHOWN]}」" + ("…" if len(decoded) > MAX_SHOWN else "")
    except UnicodeDecodeError:
        pass
    return f"{len(data)}바이트, 앞부분 16진수 {head}" + ("…" if len(data) > 48 else "") + text


def _c2pa_wrapper_text(data: bytes) -> str:
    rest = data[len(C2PA_TEXT_MAGIC):]
    version = rest[0] if rest else None
    length = int.from_bytes(rest[1:5], "big") if len(rest) >= 5 else None
    parts = [f"래퍼 {len(data)}바이트"]
    if version is not None:
        parts.append(f"버전 {version}")
    if length is not None:
        inside = max(0, len(rest) - 5)
        if length == inside:
            parts.append(f"선언 매니페스트 길이 {length}바이트(래퍼 안 바이트 수와 일치)")
        elif length < inside:
            parts.append(f"선언 매니페스트 길이 {length}바이트(뒤에 채움 선택자 {inside - length}개)")
        else:
            parts.append(f"선언 매니페스트 길이 {length}바이트(래퍼 안 바이트는 {inside}개뿐 — 잘린 래퍼)")
    return ", ".join(parts)


def text_mark_evidence(scan: TextMarkScan, *, source: str) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    if scan.tag_runs:
        shown = "; ".join(f"위치 {offset}: 「{decoded[:MAX_SHOWN]}」" + ("…" if len(decoded) > MAX_SHOWN else "") for offset, decoded in scan.tag_runs[:4])
        items.append(EvidenceItem(
            "비가시 유니코드 표식: 태그 문자열",
            f"{source}에 유니코드 태그 문자(U+E0000–U+E007F) {scan.tag_chars}개가 이모지 깃발 시퀀스 밖에서 {len(scan.tag_runs)}개 연속열로 들어 있습니다. "
            f"태그 문자는 ASCII를 화면에 보이지 않게 옮기므로 그대로 풀어 적습니다: {shown}. {MARK_CAVEAT}",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, TEXT_MARKS_LAYER,
        ))
    wrappers = scan.c2pa_wrappers
    for offset, data in wrappers[:4]:
        items.append(EvidenceItem(
            "C2PA 텍스트 매니페스트 래퍼 발견(서명 미검증)",
            f"{source}의 위치 {offset}에 변형 선택자로 부호화된 C2PA 비정형 텍스트 래퍼(C2PA 2.3 부록 A.7, 머리 「{C2PA_TEXT_MAGIC_SHOWN}」)가 있습니다: {_c2pa_wrapper_text(data)}. "
            "번들 SDK(c2pa-python 0.38 / c2pa-rs 0.91)는 텍스트 임베딩을 판독하지 않아 서명·해시를 검증하지 않았습니다 — "
            "검증 전의 매니페스트는 출처 근거가 아닌 존재 사실입니다.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, TEXT_MARKS_LAYER,
        ))
    for offset in scan.structured_blocks[:4]:
        items.append(EvidenceItem(
            "C2PA 구조화 텍스트 매니페스트 블록 발견(서명 미검증)",
            f"{source}의 위치 {offset}에 C2PA 2.4 구조화 텍스트 매니페스트 블록(「{C2PA_STRUCTURED_BEGIN}」…「{C2PA_STRUCTURED_END}」)이 있습니다. "
            "번들 SDK는 이 임베딩을 판독하지 않아 서명·해시를 검증하지 않았고, 블록이 가리키는 원격 매니페스트도 조회하지 않습니다(분석 중 네트워크 금지) — 존재 사실입니다.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, TEXT_MARKS_LAYER,
        ))
    others = [(offset, data) for offset, data in scan.vs_runs if not data.startswith(C2PA_TEXT_MAGIC)]
    if others:
        shown = "; ".join(f"위치 {offset}: {_shown_bytes(data)}" for offset, data in others[:4])
        items.append(EvidenceItem(
            "비가시 유니코드 표식: 데이터로 쓰인 변형 선택자",
            f"{source}에 변형 선택자가 2개 이상 연속된 열이 {len(others)}개 있습니다(정상 변형 시퀀스는 기준 문자 하나에 선택자 하나). "
            f"공개 대응(VS1–VS16 → 0x00–0x0F, VS17–VS256 → 0x10–0xFF)으로 읽은 바이트: {shown}. {MARK_CAVEAT}",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, TEXT_MARKS_LAYER,
        ))
    if scan.zero_width_total:
        counts = ", ".join(f"U+{cp:04X} {count}개" for cp, count in sorted(scan.zero_width.items()))
        runs = (
            f" 그중 {ZERO_WIDTH_RUN_MIN}개 이상 연속된 열 {len(scan.zero_width_runs)}개(가장 긴 열 {max(length for _, length in scan.zero_width_runs)}자, "
            f"첫 위치 {scan.zero_width_runs[0][0]}) — 이진 부호화 형태입니다."
            if scan.zero_width_runs else ""
        )
        items.append(EvidenceItem(
            "비가시 유니코드 표식: 제로폭 문자",
            f"{source}에 제로폭 문자가 있습니다: {counts}(이모지 결합용 ZWJ {scan.emoji_zwj}개는 제외).{runs} {MARK_CAVEAT}",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, TEXT_MARKS_LAYER,
        ))
    return items


def _summary(scan: TextMarkScan) -> str:
    parts = []
    if scan.tag_runs:
        parts.append(f"태그 문자 {scan.tag_chars}개")
    if scan.c2pa_wrappers:
        parts.append(f"C2PA 텍스트 래퍼 {len(scan.c2pa_wrappers)}개(서명 미검증)")
    if scan.structured_blocks:
        parts.append(f"C2PA 구조화 텍스트 블록 {len(scan.structured_blocks)}개(서명 미검증)")
    if len(scan.vs_runs) > len(scan.c2pa_wrappers):
        parts.append(f"변형 선택자 데이터열 {len(scan.vs_runs) - len(scan.c2pa_wrappers)}개")
    if scan.zero_width_total:
        parts.append(f"제로폭 문자 {scan.zero_width_total}개")
    return ", ".join(parts)


def text_mark_layers(text: str | None, *, source: str, unavailable: str | None = None) -> MarkLayers:
    """The ``text_invisible_marks`` check of one text/document row.

    ``text`` is the text the row analyzed (a .txt/.md prefix or a document's
    extracted body); ``unavailable`` is the reason no text exists (extraction
    skipped/failed) — the check is then skipped with it.
    """
    layers = MarkLayers()
    if unavailable is not None or not text:
        reason = unavailable or "분석할 본문 없음"
        layers.coverage.append(CoverageEntry(TEXT_MARKS_CHECK, CoverageStatus.SKIPPED, reason))
        layers.findings.append(MarkFinding(TEXT_MARKS_CHECK, TEXT_MARKS_ALGORITHM, "skipped", reason))
        return layers
    scan = scan_text(text)
    legit = []
    if scan.emoji_tag_sequences:
        legit.append(f"이모지 깃발 태그 시퀀스 {scan.emoji_tag_sequences}개")
    if scan.single_selectors:
        legit.append(f"단일 변형 선택자 {scan.single_selectors}개")
    if scan.emoji_zwj:
        legit.append(f"이모지 ZWJ {scan.emoji_zwj}개")
    note = f"(정상 용법으로 보고 제외: {', '.join(legit)})" if legit else ""
    span = f"{source} {scan.chars:,}자 검사"
    if scan.found:
        summary = _summary(scan)
        layers.coverage.append(CoverageEntry(TEXT_MARKS_CHECK, CoverageStatus.RAN, f"{span} — 발견: {summary}{note}"))
        layers.findings.append(MarkFinding(TEXT_MARKS_CHECK, TEXT_MARKS_ALGORITHM, "found", summary))
        layers.evidence.extend(text_mark_evidence(scan, source=source))
    else:
        layers.coverage.append(CoverageEntry(TEXT_MARKS_CHECK, CoverageStatus.RAN, f"{span} — {NO_MARK}{note}"))
        layers.findings.append(MarkFinding(TEXT_MARKS_CHECK, TEXT_MARKS_ALGORITHM, "not_found", NO_MARK))
    if scan.c2pa_wrappers or scan.structured_blocks:
        layers.findings.append(MarkFinding(
            C2PA_TEXT_CHECK, C2PA_TEXT_ALGORITHM, "found",
            f"래퍼 {len(scan.c2pa_wrappers)}개·구조화 블록 {len(scan.structured_blocks)}개 — 서명 검증 미실행(번들 SDK가 텍스트 임베딩 미지원)",
        ))
    layers.limitations.append(TEXT_MARK_LIMITATION)
    return layers


__all__ = [
    "C2PA_TEXT_MAGIC",
    "TEXT_MARKS_CHECK",
    "TEXT_MARK_LIMITATION",
    "TextMarkScan",
    "scan_text",
    "text_mark_evidence",
    "text_mark_layers",
    "vs_byte",
]
