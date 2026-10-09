"""Court Evidence Statement (증거설명서) Builder for Electronic Litigation (ECFS).

Standardized evidence explanation document conforming to Supreme Court Electronic
Litigation standards. Formulates a structured 4-column exhibit schedule:
1. 호증 (Exhibit Number, e.g. 갑 제1호증)
2. 서증명 (Evidence Name & Media Metadata)
3. 작성자 및 일자 (Forensic Center Signer & Date)
4. 입증취지 및 위법성 구성요건 (Purpose of Proof & Statutory Article Mapping)
   - 성폭력범죄의 처벌 등에 관한 특례법 제14조의2 (허위영상물 등의 반포등)
   - 정보통신망 이용촉진 및 정보보호 등에 관한 법률 제70조 (명예훼손)
   - 형법 제347조 (사기 - 신원 사칭)
   - Cryptographic SHA-256 evidence integrity verification

Signing (G30): like the scan reports, the statement is signed as a whole —
``signed_statement_body`` HMAC-signs every field of ``to_json()`` plus
``report_type``, ``tool_version``, ``model_pins`` and ``signature_note``
(key from ``--key-file`` or DEEPFAKE_LENS_REPORT_KEY). The JSON output is
that signed body; the Markdown and PDF renderings print the signature, the
key id and the signed body's SHA-256 so a paper copy can be tied to its
signed JSON, or state "서명 없음" when no key is configured.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .core import ScanItem
from .office_config import DEFAULT_CENTER, office_identity
from .result_text import (
    ARCHIVE_MEMBER_SEPARATOR,
    HASH_UNAVAILABLE_ACCESS,
    HASH_UNAVAILABLE_MEMBER,
    HASH_UNAVAILABLE_SYMLINK,
    TEXT_LEGAL_LIMITATION,
    coverage_gaps,
    display_name,
    escape_controls,
    evidence_groups,
    is_symlink_row,
    markdown_cell,
    row_label,
    threshold_provenance_lines,
    UNRECORDED_SECTION_TITLE,
    UnrecordedFiles,
    unrecorded_files,
)
from .result_types import VERDICT_LABELS, CoverageStatus, EvidenceDirection, EvidenceKind, EvidenceStrength, Grade, Verdict, is_verdict_row, status_label
from .signing import resolve_report_key, sign_report
from .json_text import json_dumps

# Marks a signed body as a 증거설명서 so it can never be mistaken for (or
# verified as) a scan report body.
EVIDENCE_STATEMENT_REPORT_TYPE = "evidence-statement"
SIGNATURE_SECTION_TITLE = "### [보고서 서명]"


@dataclass(frozen=True)
class EvidenceStatementEntry:
    exhibit_no: str
    document_name: str
    author_date: str
    purpose_of_proof: str
    sha256: str
    score: int
    # The three-verdict label (VERDICT_LABELS) — named verdict_label, not
    # band_label, since phase-0 statements carry no band (D1).
    verdict_label: str
    file_path: str
    statutes: list[str]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceStatement:
    case_no: str
    case_name: str
    plaintiff: str
    defendant: str
    court: str
    entries: list[EvidenceStatementEntry]
    created_at: str
    # N17: no built-in office identity — blank unless the operator sets it
    # (--law-firm/--contact, the request, or ~/.deepfake-lens/config.json).
    law_firm: str = ""
    contact: str = ""
    center: str = DEFAULT_CENTER
    provenance_note: str = ""
    # The fixed legal limitation, set when any entry is a text result.
    reference_note: str = ""
    # X1: "기록되지 않은 파일" — files of the scanned folder without an
    # analysis result (cap, flat scan, symlinks, duplicates, unsupported),
    # UnrecordedFiles.to_json(); signed with the rest of the statement.
    unrecorded_files: dict[str, Any] = field(default_factory=dict)
    # X2: web statements — posted rows the server could not re-analyze
    # (path, marker, reason; never the client's result). Empty for the CLI.
    excluded_items: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    def unrecorded_lines(self) -> list[str]:
        """The "기록되지 않은 파일" section body (headline + reasons)."""
        unrecorded = UnrecordedFiles.from_json(self.unrecorded_files) or UnrecordedFiles({})
        return unrecorded.lines()

    def to_markdown(self) -> str:
        # R10-1: every table cell goes through markdown_cell (line breaks as
        # <br>, controls escaped, "|" as "\|") and every other line through
        # escape_controls — a file name or field value can neither end a
        # table row (a forged "| **갑 제9호증** | … |" row) nor add a column.
        one_line = escape_controls
        lines = [
            "# 증  거  설  명  서",
            "",
            f"**사    건**: {one_line(self.case_no)} {one_line(self.case_name)}",
            f"**원    고 (고소인)**: {one_line(self.plaintiff)}",
            f"**피    고 (피의자)**: {one_line(self.defendant)}",
            "",
            "위 사건에 관하여 원고(고소인)의 대리인은 그 주장사실을 입증하기 위하여 다음과 같이 증거방법을 제출합니다.",
            "",
            "## 다        음",
            "",
            "| 호증 | 서증(증거)의 명칭 | 작성자 및 일자 | 입증취지 및 관련 법조 |",
            "| :--- | :--- | :--- | :--- |",
        ]
        for entry in self.entries:
            lines.append(
                f"| **{markdown_cell(entry.exhibit_no)}** | {markdown_cell(entry.document_name)} | "
                f"{markdown_cell(entry.author_date)} | {markdown_cell(entry.purpose_of_proof)} |"
            )

        hashed = sum(1 for e in self.entries if e.sha256)
        if self.entries and hashed == len(self.entries):
            integrity_line = "모든 제출 서증은 원본 해시(SHA-256)가 산출·기재되어 채증 시점의 동일성 확인이 가능합니다."
        elif hashed:
            integrity_line = f"제출 서증 중 {hashed}/{len(self.entries)}건의 원본 해시(SHA-256)가 산출되었으며, 해시 불가 항목은 별도 표기되어 별도 확인이 필요합니다."
        else:
            integrity_line = "원본 파일에 접근할 수 없어 해시 산출이 불가하였습니다. 동일성 확인은 별도 절차로 진행하여야 합니다."
        lines.extend([
            "",
            "### [증거 무결성 고지 (증거 관리 연속성)]",
            integrity_line,
            "본 문서는 자동 분석 도구의 결과를 요약한 것으로, 결론(조작·생성 근거 있음/원본성 근거 있음/판단 불가)은 유죄·불법성에 대한 법적 판단이 아닙니다.",
            "",
        ])
        unrecorded = self.unrecorded_lines()
        lines.extend([f"### [{UNRECORDED_SECTION_TITLE}]", one_line(unrecorded[0]), *(f"- {one_line(line)}" for line in unrecorded[1:]), ""])
        if self.reference_note:
            lines.extend(["### [텍스트 분석 한계]", self.reference_note, ""])
        if self.provenance_note:
            lines.extend(["### [분석 프로비넌스]", *(one_line(line) for line in self.provenance_note.split("\n")), ""])
        lines.extend([
            f"**제출일자**: {one_line(self.created_at)}",
            f"**원고(고소인) 소송대리인**: {one_line(f'{self.law_firm} {self.center}'.strip())}",
            f"**대표전화**: {one_line(self.contact)}",
            f"**제출처**: **{one_line(self.court)}**",
        ])
        return "\n".join(lines)


# N2: why a row has no SHA-256, printed in the hash line. The constants
# live in result_text (shared with the forensic PDF, S2) and are re-exported
# here under their historical names (imported above).


class _SymlinkRefused(Exception):
    """The evidence path (or a folder on it) is a symbolic link (N2)."""


def _no_symlink_on_path(path: Path, scan_root: Path | None) -> None:
    """Raise _SymlinkRefused if ``path`` or a folder between it and ``scan_root`` is a link."""
    parts = [path]
    if scan_root is not None:
        try:
            relative: Path | None = path.relative_to(scan_root)
        except ValueError:
            relative = None
        if relative is not None:
            parts.extend(scan_root / Path(*relative.parts[:depth]) for depth in range(1, len(relative.parts)))
    for part in parts:
        try:
            mode = os.lstat(part).st_mode
        except OSError:
            continue
        if stat.S_ISLNK(mode):
            raise _SymlinkRefused(str(part))


def _compute_sha256(path: Path | str, scan_root: Path | str | None = None) -> str | None:
    """Full streaming hash of the evidence file, or None if unavailable.

    A path string is never hashed as a substitute for content — an absent
    hash must be rendered as 'unavailable', not as a digest-shaped value.
    N2: a symbolic link is never followed — not the file itself, not a
    folder between it and ``scan_root`` — and the open uses O_NOFOLLOW so
    a link swapped in after the check is refused too (:class:`_SymlinkRefused`).
    """
    p = Path(path)
    _no_symlink_on_path(p, Path(scan_root) if scan_root is not None else None)
    try:
        info = os.lstat(p)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    h = hashlib.sha256()
    try:
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise _SymlinkRefused(str(p)) from exc
        return None
    try:
        with os.fdopen(fd, "rb") as f:
            while chunk := f.read(64 * 1024):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _item_hash(item: ScanItem, scan_root: Path | str | None) -> tuple[str | None, str]:
    """``(sha256, unavailable reason)`` for one statement row (D5, N2).

    The scan already hashed the bytes it analyzed (``item.sha256``, WP-G),
    so that digest is used as is — re-reading the file later could hash a
    different file or a changed one. Only a row without a recorded digest
    (a single-file analysis, an old JSON) is hashed here, and then a
    relative ``item.path`` is resolved against ``scan_root`` (the scanned
    folder), never against the process working directory. A relative path
    with no ``scan_root`` and archive-member paths (``a.zip::x``) are not
    hashable. A symlink row (the scan never followed it) and any path that
    is or passes through a symbolic link yield
    :data:`HASH_UNAVAILABLE_SYMLINK` — the link is never followed (N2).
    """
    if item.sha256:
        return item.sha256, ""
    if ARCHIVE_MEMBER_SEPARATOR in item.path:
        return None, HASH_UNAVAILABLE_MEMBER
    if is_symlink_row(item.status, item.error):
        return None, HASH_UNAVAILABLE_SYMLINK
    path = Path(item.path)
    root = Path(scan_root) if scan_root is not None else None
    if not path.is_absolute():
        if root is None:
            return None, HASH_UNAVAILABLE_ACCESS
        path = root / path
    try:
        digest = _compute_sha256(path, root)
    except _SymlinkRefused:
        return None, HASH_UNAVAILABLE_SYMLINK
    return digest, "" if digest else HASH_UNAVAILABLE_ACCESS


def _item_sha256(item: ScanItem, scan_root: Path | str | None) -> str | None:
    """The row's SHA-256 or None (see :func:`_item_hash`)."""
    return _item_hash(item, scan_root)[0]


# Corner brackets around a metadata value copied verbatim from the file.
_QUOTE_OPEN, _QUOTE_CLOSE = "\u300c", "\u300d"


def _clip(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` chars without leaving a quoted metadata value open."""
    if len(text) <= limit:
        return text
    clipped = text[:limit] + "…"
    if clipped.count(_QUOTE_OPEN) > clipped.count(_QUOTE_CLOSE):
        clipped += _QUOTE_CLOSE
    return clipped


def _determine_statutes(score: int, signals: list[Any], item_kind: str, band: str) -> list[str]:
    """Statutes a legal reviewer may consider — only for a manipulation verdict.

    ``band`` here is the verdict-derived band ("high" == manipulation
    evidence on an evidence-grade result). A screening number is not proof
    of a crime, so statutes are listed as '검토 참고' candidates only, and
    never for undetermined, authenticity, reference-grade or failed items.
    """
    if band != "high":
        return []
    statutes = []
    sig_titles = [s.title for s in signals] if signals else []

    is_faceswap = any("얼굴" in t or "안면" in t or "스왑" in t or "턱선" in t for t in sig_titles)
    is_video = item_kind == "video"

    if is_faceswap or is_video:
        statutes.append("성폭력범죄의 처벌 등에 관한 특례법 제14조의2 (허위영상물 등의 반포등) — 검토 참고")
        statutes.append("형법 제347조 (사기 - 신원도용 및 기망) — 검토 참고")
    statutes.append("정보통신망 이용촉진 및 정보보호 등에 관한 법률 제70조 (벌칙 - 명예훼손) — 검토 참고")
    return statutes


def _member_references(item: ScanItem, exhibits: dict[str, str], items: list[ScanItem]) -> str:
    """Exhibit references to an archive container's member rows (S1).

    Names the rows that exist in this statement ("갑 제3호증(evil.zip::inner/a.png)"),
    manipulation-verdict members first, so the cross-reference always
    points at a real row label.
    """
    prefix = item.path + ARCHIVE_MEMBER_SEPARATOR
    members = [member for member in items if member.path.startswith(prefix)]
    members.sort(key=lambda member: 0 if member.result is not None and member.result.verdict_code == Verdict.MANIPULATION_EVIDENCE else 1)
    refs = [f"{exhibits[member.path]}({row_label(member.path)})" for member in members if member.path in exhibits]
    if not refs:
        return f"구성원 행은 이 증거설명서에 없습니다 — 검사 JSON의 '{row_label(item.path)}{ARCHIVE_MEMBER_SEPARATOR}' 행 참조"
    shown = ", ".join(refs[:3]) + (f" 외 {len(refs) - 3}건" if len(refs) > 3 else "")
    return f"구성원별 근거는 {shown} 참조"


def _purpose_head(item: ScanItem, member_refs: str = "") -> str:
    res = item.result
    if res is None or not is_verdict_row(item.status, True):
        # N2: skipped/unsupported/failed/duplicate rows carry no conclusion.
        return f"자동 분석 결론이 없는 증거물({status_label(item.status or 'failed')})로, 별도 검증이 필요함을 소명함."
    if res.grade == Grade.REFERENCE:
        return f"{TEXT_LEGAL_LIMITATION} 본 증거물에 대한 자동 분석 결과는 결론이 아닌 참고 정보임을 소명함."
    if res.verdict_code == Verdict.MANIPULATION_EVIDENCE and item.kind == "archive":
        # N5: a container row concludes from its members, not from its own bytes.
        rollup = display_name(next((e.detail for e in res.evidence if e.layer == "archive"), ""))
        return (
            f"압축 파일 구성원 중 결정적 근거에 의해 조작·생성 근거가 확인된 파일이 있는 증거물임을 소명함"
            f"(구성원 결론 집계: {rollup}; {member_refs or '구성원 행 참조'})."
        )
    if res.verdict_code == Verdict.MANIPULATION_EVIDENCE:
        basis = next(
            (e.title for e in res.evidence if e.kind == EvidenceKind.DETERMINISTIC and e.direction == EvidenceDirection.SYNTHETIC and e.strength == EvidenceStrength.STRONG),
            "결정적 근거",
        )
        return f"결정적 근거({display_name(basis)})에 의해 조작·생성 근거가 확인된 증거물임을 소명함."
    if res.verdict_code == Verdict.AUTHENTICITY_EVIDENCE:
        basis = next(
            (e.title for e in res.evidence if e.direction == EvidenceDirection.AUTHENTIC and e.strength == EvidenceStrength.STRONG),
            "결정적 근거",
        )
        return f"결정적 근거({display_name(basis)})에 의해 원본성 근거가 확인된 증거물임을 소명함."
    failed = [entry for entry in res.coverage if entry.status == CoverageStatus.FAILED]
    if failed:
        return f"검사 실패({', '.join(display_name(entry.describe()) for entry in failed)})로 판단 불가 상태이며, 별도 검증이 필요함을 소명함."
    return "결론을 뒷받침할 결정적 근거가 없어 판단 불가 상태이며(원본이라는 뜻이 아님), 별도 검증이 필요함을 소명함."


def build_evidence_statement(
    items: list[ScanItem],
    *,
    case_no: str = "(사건번호 입력)",
    case_name: str = "성폭력처벌법위반(허위영상물편집등) 및 정보통신망법위반",
    plaintiff: str = "(의뢰사 상호명 입력) 귀하",
    defendant: str = "(피고/피의자 성명 입력)",
    court: str = "○○지방법원 귀중",
    exhibit_prefix: str = "갑 제",
    law_firm: str | None = None,
    contact: str | None = None,
    center: str = DEFAULT_CENTER,
    thresholds: object | None = None,
    coverage: dict[str, object] | None = None,
    scan_root: Path | str | None = None,
    summary: object | None = None,
    excluded_items: list[dict[str, object]] | None = None,
) -> EvidenceStatement:
    """Build an EvidenceStatement from analyzed scan items.

    ``summary`` (a BatchScanSummary or the scan JSON's summary dict) adds
    the enumeration-level "기록되지 않은 파일" reasons — files over the
    --max-files cap, subfolders a flat scan did not enter (X1); the
    row-level ones are counted from ``items``.

    ``scan_root`` is the folder the items were scanned from; it is only
    used to locate a file whose row carries no ``sha256`` (see
    :func:`_item_sha256`). ``law_firm`` / ``contact`` left as None come from
    the operator config (``office_config``) and are blank without one (N17).
    """
    office = office_identity(law_firm, contact, center=center)
    law_firm, contact, center = office.law_firm, office.contact, office.center
    entries: list[EvidenceStatementEntry] = []
    now_date = datetime.now().strftime("%Y. %m. %d.")
    # S1: exhibit number of every row, so a container can cite its members.
    exhibits = {item.path: f"{exhibit_prefix}{idx}호증" for idx, item in enumerate(items, start=1)}

    for idx, item in enumerate(items, start=1):
        res = item.result
        score = res.score if res else 0
        # Statutes key off the verdict, not the stored band: a v1 record
        # (no verdict) or a reference-grade text result never lists any.
        band_value = (
            "high"
            if res is not None and res.verdict_code == Verdict.MANIPULATION_EVIDENCE and res.grade == Grade.EVIDENCE
            else "unknown"
        )
        # N2: a row without a verdict (skipped/unsupported/failed/duplicate)
        # is labelled by its Korean status, never by the raw status code.
        verdict_row = res is not None and is_verdict_row(item.status, True)
        band = VERDICT_LABELS[res.verdict_code] if res is not None and verdict_row else status_label(item.status or "failed")
        signals = res.signals if res else []
        file_sha256, hash_unavailable = _item_hash(item, scan_root)

        exhibit_no = f"{exhibit_prefix}{idx}호증"
        # S1: an archive member is named "<container>::<member>", never its bare file name.
        doc_name = f"디지털 증거 파일 ({row_label(item.path)}) 및 AI 스크리닝 데이터"
        author_date = f"{law_firm}\n{now_date}" if law_firm else now_date

        statutes = _determine_statutes(score, signals, item.kind, band_value)

        # The tool screens; it does not prove crimes. Purpose language
        # follows the verdict and names the evidence kind behind it.
        member_refs = _member_references(item, exhibits, items) if item.kind == "archive" else ""
        purpose_head = _purpose_head(item, member_refs)
        evidence_lines: list[str] = []
        if res is not None:
            for kind_label, lines in evidence_groups(res):
                evidence_lines.append(f"• {kind_label}: " + "; ".join(display_name(_clip(line, 60)) for line in lines[:2]) + (" 외" if len(lines) > 2 else ""))
            gaps = coverage_gaps(res)
            if gaps:
                evidence_lines.append("• 검사 범위: " + "; ".join(display_name(entry.describe()) for entry in gaps[:3]) + (f" 외 {len(gaps) - 3}건" if len(gaps) > 3 else ""))
        sig_text = "\n".join(evidence_lines) if evidence_lines else "• 근거 항목 없음"

        statute_text = "\n".join([f"• {st}" for st in statutes]) if statutes else "• 관련 법조: 해당 없음 (결정적 근거에 의한 조작·생성 결론이 없음)"

        hash_text = f"• 원본 SHA-256: {file_sha256}" if file_sha256 else f"• 원본 SHA-256: {hash_unavailable or HASH_UNAVAILABLE_ACCESS}"

        if verdict_row and res is not None:
            head_line = f"[자동 분석 결론: {band} / 등급: {res.grade_label} — 유죄·불법성의 직접 증거가 아님]"
        else:
            reason = display_name((item.error or "").strip()) or "사유 기록 없음"
            head_line = f"상태: {band} — {reason}"
        purpose = (
            f"{head_line}\n"
            f"{purpose_head}\n"
            f"{sig_text}\n"
            f"{statute_text}\n"
            f"{hash_text}"
        )

        entries.append(
            EvidenceStatementEntry(
                exhibit_no=exhibit_no,
                document_name=doc_name,
                author_date=author_date,
                purpose_of_proof=purpose,
                sha256=file_sha256 or "",
                score=score,
                verdict_label=band,
                file_path=item.path,
                statutes=statutes,
            )
        )

    prov_lines: list[str] = []
    if coverage is not None:
        _wa_raw, _wc_raw = coverage.get("weights_available", 0), coverage.get("weights_total", 0)
        wa = _wa_raw if isinstance(_wa_raw, int) else 0
        wc = _wc_raw if isinstance(_wc_raw, int) else 0
        prov_lines.append(
            f"모델 가중치: {wa}/{wc} 탑재" + (" — 신경망 미탑재(측정 게이트 미충족) — 결정적 근거만 반영" if wa == 0 else "")
        )
    if thresholds is not None:
        # S6: the same line as the CLI header / HTML / forensic PDF,
        # including the in-sample caveat (G28).
        prov_lines.extend(threshold_provenance_lines(thresholds))  # N17: caveat on its own line
    provenance_note = "\n".join(prov_lines)

    return EvidenceStatement(
        case_no=case_no,
        case_name=case_name,
        plaintiff=plaintiff,
        defendant=defendant,
        court=court,
        entries=entries,
        created_at=now_date,
        law_firm=law_firm,
        contact=contact,
        center=center,
        provenance_note=provenance_note,
        reference_note=TEXT_LEGAL_LIMITATION if any(i.result is not None and i.result.grade == Grade.REFERENCE for i in items) else "",
        unrecorded_files=unrecorded_files(list(items), summary).to_json(),
        excluded_items=[dict(entry) for entry in excluded_items or []],
    )


def signed_statement_body(
    statement: EvidenceStatement,
    key: bytes | str | None = None,
    *,
    model_pins: list[dict[str, object]] | None = None,
    scan_root: str | None = None,
) -> dict[str, object]:
    """The statement as a signed report body (G30).

    ``key`` None means DEEPFAKE_LENS_REPORT_KEY; with no key at all the body
    carries ``signature: null`` and the "서명 없음" note (never silently
    unsigned). Verify with ``signing.verify_report``. ``scan_root`` (web
    statements, R9-9): the scanned folder relative to its read root, signed
    and printed under the case box.
    """
    body: dict[str, object] = {"report_type": EVIDENCE_STATEMENT_REPORT_TYPE, **statement.to_json()}
    if scan_root is not None:
        body["scan_root"] = scan_root
    return sign_report(body, key if key is not None else resolve_report_key(), model_pins=model_pins)


def statement_signature_lines(signed: dict[str, object]) -> list[str]:
    """Korean signature lines for the Markdown/PDF renderings."""
    from .reports import signature_lines_ko

    return signature_lines_ko(signed)


def _signed(statement: EvidenceStatement, signed: dict[str, object] | None, key: bytes | str | None) -> dict[str, object]:
    return signed if signed is not None else signed_statement_body(statement, key)


def write_evidence_statement_json(
    path: Path | str,
    statement: EvidenceStatement,
    *,
    signed: dict[str, object] | None = None,
    key: bytes | str | None = None,
) -> dict[str, object]:
    """Write the signed statement body as JSON; returns it."""
    body = _signed(statement, signed, key)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json_dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return body


def write_evidence_statement_markdown(
    path: Path | str,
    statement: EvidenceStatement,
    *,
    signed: dict[str, object] | None = None,
    key: bytes | str | None = None,
) -> dict[str, object]:
    """Markdown statement followed by the signature section (G30)."""
    body = _signed(statement, signed, key)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [statement.to_markdown(), "", SIGNATURE_SECTION_TITLE, *statement_signature_lines(body), ""]
    p.write_text("\n".join(lines), encoding="utf-8")
    return body


# R6: the PDF renderer's optional dependency and the message shown when it
# is missing (CLI exit 2, web JSON error) — never an English traceback.
PDF_DEPENDENCY = "pymupdf"
STATEMENT_NOTICE_TITLE = "무결성/해석 고지"
PDF_DEPENDENCY_MESSAGE = (
    "PDF 증거설명서를 만들려면 pymupdf 패키지가 필요합니다(설치: `pip install pymupdf`). "
    "Markdown(.md) 또는 JSON(.json) 증거설명서는 pymupdf 없이 만들 수 있습니다."
)
# B8: the same refusal for the scan PDF reports (--pdf-out, --forensic-pdf-out).
PDF_REPORT_DEPENDENCY_MESSAGE = (
    "PDF 보고서를 만들려면 pymupdf 패키지가 필요합니다(설치: `pip install pymupdf`). "
    "HTML(--html-out), JSON(--json-out), CSV(--csv-out) 보고서는 pymupdf 없이 만들 수 있습니다."
)


class PdfDependencyMissing(RuntimeError):
    """pymupdf (or its legacy ``fitz`` name) is not installed (R6, B8)."""

    def __init__(self, message: str = PDF_DEPENDENCY_MESSAGE) -> None:
        super().__init__(message)


def _import_pymupdf() -> Any:
    from .pdf_backend import import_pymupdf

    try:
        return import_pymupdf()  # N4: no fitz deprecation warning on stdout
    except ImportError as exc:
        raise PdfDependencyMissing() from exc


def pdf_backend_available() -> bool:
    """True when write_evidence_statement_pdf can run (checked before a scan starts)."""
    try:
        _import_pymupdf()
    except PdfDependencyMissing:
        return False
    return True


def write_evidence_statement_pdf(
    path: Path | str,
    statement: EvidenceStatement,
    *,
    signed: dict[str, object] | None = None,
    key: bytes | str | None = None,
    unsigned_rows: list[tuple[ScanItem, str]] | None = None,
) -> dict[str, object]:
    """Render court-admissible Evidence Statement PDF using PyMuPDF with Korean fonts.

    The last block prints the signature lines of the signed statement body
    (G30); returns that body. ``unsigned_rows`` (web, X2) are posted rows the
    server could not re-analyze, printed in a "서명 제외(클라이언트 제공
    결과)" box — they are not exhibits and not in the signed body."""
    body = _signed(statement, signed, key)
    pymupdf = _import_pymupdf()

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    data = _render_statement_pdf(pymupdf, statement, body, unsigned_rows=unsigned_rows)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(output)
    return body


# Evidence-statement PDF table (G2): relative column widths of
# 호증 | 서증(증거)의 명칭 | 작성자 및 일자 | 입증취지 및 위법성 요건 대조.
STATEMENT_TABLE_HEADERS = ("호증", "서증(증거)의 명칭", "작성자 및 일자", "입증취지 및 위법성 요건 대조")
STATEMENT_TABLE_WEIGHTS = (12.5, 22.0, 18.5, 47.0)
STATEMENT_ROW_MIN_HEIGHT = 30.0


def _render_statement_pdf(
    pymupdf: Any, statement: EvidenceStatement, body: dict[str, object],
    *, unsigned_rows: list[tuple[ScanItem, str]] | None = None,
) -> bytes:
    """Lay out the evidence statement with :class:`pdf_layout.PdfLayout` (G2).

    Every cell is wrapped to its measured column and a row that does not fit
    continues on the next page — no purpose text is cut or replaced by
    "…(이후 내용 생략…)", and the 무결성/해석 고지 box is as tall as its text
    (the old fixed box was left empty when the text overflowed it).
    """
    from .pdf_layout import Cell, PdfLayout
    from .reports import scan_root_line

    def page_header(layout: PdfLayout) -> None:
        firm_header = f"{statement.law_firm} {statement.center}".strip()
        layout.text(layout.left, layout.right, firm_header, 9.0, (0.15, 0.25, 0.45), gap=0.5)
        contact_line = f"대한민국 법원 전자소송(ECFS) 표준 서식  |  대표전화: {statement.contact}" if statement.contact else "대한민국 법원 전자소송(ECFS) 표준 서식"
        layout.text(layout.left, layout.right, contact_line, 7.5, (0.5, 0.5, 0.5), gap=2.0)
        layout.page.draw_line(pymupdf.Point(layout.left, layout.y), pymupdf.Point(layout.right, layout.y), color=(0.85, 0.88, 0.92), width=0.8)
        layout.y += 6.0

    layout = PdfLayout(pymupdf, header=page_header)
    layout.new_page()
    layout.text(layout.left, layout.right, "증  거  설  명  서", 18.0, (0.08, 0.15, 0.32), align=1, gap=6.0)
    layout.boxed_text("", [
        (f"사        건    {statement.case_no}  {statement.case_name}", 9.5, (0.1, 0.1, 0.1)),
        (f"원고(고소인)    {statement.plaintiff}", 9.5, (0.1, 0.1, 0.1)),
        (f"피고(피의자)    {statement.defendant}", 9.5, (0.1, 0.1, 0.1)),
        ("위 사건에 관하여 원고(고소인)의 소송대리인은 주장사실을 입증하기 위해 아래와 같이 증거방법을 제출합니다.", 8.2, (0.3, 0.3, 0.3)),
        *([(root_line, 8.2, (0.3, 0.3, 0.3))] if (root_line := scan_root_line(body)) else []),  # R9-9
    ], border=(0.82, 0.86, 0.92), fill=(0.97, 0.98, 0.99))
    layout.text(layout.left, layout.right, "다        음", 11.0, (0.1, 0.1, 0.1), align=1, gap=4.0)

    columns = layout.columns(STATEMENT_TABLE_WEIGHTS)
    header_color = (0.12, 0.2, 0.35)

    def table_header(current: PdfLayout) -> None:
        current.draw_row(
            columns,
            [Cell(index, label, 8.5, header_color) for index, label in enumerate(STATEMENT_TABLE_HEADERS)],
            fill=(0.9, 0.93, 0.97), rule=(0.75, 0.8, 0.88),
        )

    layout.ensure_space(80.0)
    table_header(layout)
    for idx, entry in enumerate(statement.entries):
        layout.draw_row(
            columns,
            [
                Cell(0, entry.exhibit_no, 8.5, (0.1, 0.15, 0.3)),
                Cell(1, entry.document_name or " ", 7.5, (0.2, 0.2, 0.2)),
                Cell(2, entry.author_date or " ", 7.5, (0.3, 0.3, 0.3)),
                Cell(3, entry.purpose_of_proof or " ", 6.8, (0.15, 0.15, 0.15)),
            ],
            fill=(0.97, 0.98, 0.99) if idx % 2 == 1 else None,
            rule=(0.88, 0.9, 0.93),
            min_height=STATEMENT_ROW_MIN_HEIGHT,
            on_new_page=table_header,
        )
    layout.y += 8.0

    # Provenance + integrity disclosure (screening caveat goes on every copy).
    hashed = sum(1 for e in statement.entries if e.sha256)
    notice = [
        (f"원본 해시 산출: {hashed}/{len(statement.entries)}건.", 6.8, (0.4, 0.4, 0.4)),
        ("본 문서의 결론은 자동 분석 결과로 유죄·불법성의 직접 증거가 아니며, 정밀 감정은 별도로 수행되어야 합니다.", 6.8, (0.4, 0.4, 0.4)),
    ]
    if statement.reference_note:
        notice.append((statement.reference_note, 6.8, (0.4, 0.4, 0.4)))
    for line in statement.provenance_note.split("\n"):
        if line.strip():
            notice.append((line.strip(), 6.8, (0.4, 0.4, 0.4)))
    layout.boxed_text(STATEMENT_NOTICE_TITLE, notice)
    # X1: the "기록되지 않은 파일" section — count and reasons, always present.
    layout.boxed_text(UNRECORDED_SECTION_TITLE, [
        (line, 7.5 if index == 0 else 6.8, (0.1, 0.2, 0.4) if index == 0 else (0.35, 0.35, 0.35))
        for index, line in enumerate(statement.unrecorded_lines())
    ])
    if unsigned_rows:
        from .reports import UNSIGNED_ROWS_NOTE, UNSIGNED_ROWS_TITLE, unsigned_row_lines

        layout.boxed_text(UNSIGNED_ROWS_TITLE, [
            (UNSIGNED_ROWS_NOTE, 7.0, (0.55, 0.3, 0.0)),
            *((line, 6.8, (0.35, 0.35, 0.35)) for line in unsigned_row_lines(unsigned_rows)),
        ])

    # Signoff block, kept together on one page.
    signoff = [
        (statement.created_at, 10.0, (0.1, 0.1, 0.1)),
        (f"원고(고소인) 소송대리인  {statement.law_firm}".rstrip(), 10.5, (0.08, 0.15, 0.32)),
        ("담당변호사 : ○ ○ ○,  ○ ○ ○", 9.5, (0.2, 0.2, 0.2)),
        ("디지털포렌식센터 수석감정관 : ○ ○ ○  (인)", 9.5, (0.2, 0.2, 0.2)),
    ]
    # The signoff, the court line and the G30 signature lines (or the
    # explicit "서명 없음" lines) stay together on one page.
    signature = [(line, 6.5, (0.35, 0.35, 0.35)) for line in statement_signature_lines(body)]
    court = [(statement.court, 12.0, (0.05, 0.05, 0.05))]
    needed = sum(
        layout.block_height(layout.wrap(text, layout.content_width, size), size) + 3.0
        for text, size, _ in (*signoff, *court, *signature)
    )
    layout.ensure_space(needed + 20.0)
    layout.y += 8.0
    for text, size, color in signoff:
        layout.text(layout.left, layout.right, text, size, color, align=1, gap=3.0)
    layout.y += 6.0
    for text, size, color in court:
        layout.text(layout.left, layout.right, text, size, color, gap=6.0)
    for text, size, color in signature:
        layout.text(layout.left, layout.right, text, size, color, gap=0.5)

    layout.footer(lambda page_no, total: f"- {page_no} / {total} -")
    return layout.to_bytes()
