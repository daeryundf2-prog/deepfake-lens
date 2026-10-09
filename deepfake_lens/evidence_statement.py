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
import json
import os
import stat
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .core import BatchScanSummary, ScanItem
from .result_text import (
    ARCHIVE_MEMBER_SEPARATOR,
    HASH_UNAVAILABLE_ACCESS,
    HASH_UNAVAILABLE_MEMBER,
    HASH_UNAVAILABLE_SYMLINK,
    TEXT_LEGAL_LIMITATION,
    coverage_gaps,
    evidence_groups,
    is_symlink_row,
    row_label,
    threshold_provenance_line,
)
from .result_types import VERDICT_LABELS, CoverageStatus, EvidenceDirection, EvidenceKind, EvidenceStrength, Grade, Verdict, is_verdict_row, status_label
from .signing import resolve_report_key, sign_report

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
    law_firm: str = "법무법인(유한) 대륜"
    contact: str = "02-780-1128"
    center: str = "디지털포렌식 감정센터"
    provenance_note: str = ""
    # The fixed legal limitation, set when any entry is a text result.
    reference_note: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [
            "# 증  거  설  명  서",
            "",
            f"**사    건**: {self.case_no} {self.case_name}",
            f"**원    고 (고소인)**: {self.plaintiff}",
            f"**피    고 (피의자)**: {self.defendant}",
            "",
            "위 사건에 관하여 원고(고소인)의 대리인은 그 주장사실을 입증하기 위하여 다음과 같이 증거방법을 제출합니다.",
            "",
            "## 다        음",
            "",
            "| 호증 | 서증(증거)의 명칭 | 작성자 및 일자 | 입증취지 및 관련 법조 |",
            "| :--- | :--- | :--- | :--- |",
        ]
        for entry in self.entries:
            safe_purpose = entry.purpose_of_proof.replace("\n", "<br>")
            lines.append(
                f"| **{entry.exhibit_no}** | {entry.document_name} | {entry.author_date} | {safe_purpose} |"
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
        if self.reference_note:
            lines.extend(["### [텍스트 분석 한계]", self.reference_note, ""])
        if self.provenance_note:
            lines.extend(["### [분석 프로비넌스]", self.provenance_note, ""])
        lines.extend([
            f"**제출일자**: {self.created_at}",
            f"**원고(고소인) 소송대리인**: {self.law_firm} {self.center}",
            f"**대표전화**: {self.contact}",
            f"**제출처**: **{self.court}**",
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
        rollup = next((e.detail for e in res.evidence if e.layer == "archive"), "")
        return (
            f"압축 파일 구성원 중 결정적 근거에 의해 조작·생성 근거가 확인된 파일이 있는 증거물임을 소명함"
            f"(구성원 결론 집계: {rollup}; {member_refs or '구성원 행 참조'})."
        )
    if res.verdict_code == Verdict.MANIPULATION_EVIDENCE:
        basis = next(
            (e.title for e in res.evidence if e.kind == EvidenceKind.DETERMINISTIC and e.direction == EvidenceDirection.SYNTHETIC and e.strength == EvidenceStrength.STRONG),
            "결정적 근거",
        )
        return f"결정적 근거({basis})에 의해 조작·생성 근거가 확인된 증거물임을 소명함."
    if res.verdict_code == Verdict.AUTHENTICITY_EVIDENCE:
        basis = next(
            (e.title for e in res.evidence if e.direction == EvidenceDirection.AUTHENTIC and e.strength == EvidenceStrength.STRONG),
            "결정적 근거",
        )
        return f"결정적 근거({basis})에 의해 원본성 근거가 확인된 증거물임을 소명함."
    failed = [entry for entry in res.coverage if entry.status == CoverageStatus.FAILED]
    if failed:
        return f"검사 실패({', '.join(entry.describe() for entry in failed)})로 판단 불가 상태이며, 별도 검증이 필요함을 소명함."
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
    law_firm: str = "법무법인(유한) 대륜",
    contact: str = "02-780-1128",
    center: str = "디지털포렌식 감정센터",
    thresholds: object | None = None,
    coverage: dict[str, object] | None = None,
    scan_root: Path | str | None = None,
) -> EvidenceStatement:
    """Build an EvidenceStatement from analyzed scan items.

    ``scan_root`` is the folder the items were scanned from; it is only
    used to locate a file whose row carries no ``sha256`` (see
    :func:`_item_sha256`).
    """
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
        author_date = f"{law_firm}\n{now_date}"

        statutes = _determine_statutes(score, signals, item.kind, band_value)

        # The tool screens; it does not prove crimes. Purpose language
        # follows the verdict and names the evidence kind behind it.
        member_refs = _member_references(item, exhibits, items) if item.kind == "archive" else ""
        purpose_head = _purpose_head(item, member_refs)
        evidence_lines: list[str] = []
        if res is not None:
            for kind_label, lines in evidence_groups(res):
                evidence_lines.append(f"• {kind_label}: " + "; ".join(_clip(line, 60) for line in lines[:2]) + (" 외" if len(lines) > 2 else ""))
            gaps = coverage_gaps(res)
            if gaps:
                evidence_lines.append("• 검사 범위: " + "; ".join(entry.describe() for entry in gaps[:3]) + (f" 외 {len(gaps) - 3}건" if len(gaps) > 3 else ""))
        sig_text = "\n".join(evidence_lines) if evidence_lines else "• 근거 항목 없음"

        statute_text = "\n".join([f"• {st}" for st in statutes]) if statutes else "• 관련 법조: 해당 없음 (결정적 근거에 의한 조작·생성 결론이 없음)"

        hash_text = f"• 원본 SHA-256: {file_sha256}" if file_sha256 else f"• 원본 SHA-256: {hash_unavailable or HASH_UNAVAILABLE_ACCESS}"

        if verdict_row and res is not None:
            head_line = f"[자동 분석 결론: {band} / 등급: {res.grade_label} — 유죄·불법성의 직접 증거가 아님]"
        else:
            reason = (item.error or "").strip() or "사유 기록 없음"
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
        prov_lines.append(threshold_provenance_line(thresholds))
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
    )


def signed_statement_body(
    statement: EvidenceStatement,
    key: bytes | str | None = None,
    *,
    model_pins: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """The statement as a signed report body (G30).

    ``key`` None means DEEPFAKE_LENS_REPORT_KEY; with no key at all the body
    carries ``signature: null`` and the "서명 없음" note (never silently
    unsigned). Verify with ``signing.verify_report``.
    """
    body: dict[str, object] = {"report_type": EVIDENCE_STATEMENT_REPORT_TYPE, **statement.to_json()}
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
    p.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
) -> dict[str, object]:
    """Render court-admissible Evidence Statement PDF using PyMuPDF with Korean fonts.

    The last block prints the signature lines of the signed statement body
    (G30); returns that body."""
    body = _signed(statement, signed, key)
    pymupdf = _import_pymupdf()

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open()
    font_ko = "korea"  # B3: every label is drawn with the CJK font

    page_w, page_h = 595.0, 842.0
    margin_l, margin_r = 45.0, 550.0

    def create_page() -> Any:
        page = doc.new_page(width=page_w, height=page_h)
        # Header banner
        firm_header = f"{statement.law_firm} {statement.center}".strip()
        page.insert_text(pymupdf.Point(margin_l, 35), firm_header, fontname=font_ko, fontsize=9, color=(0.15, 0.25, 0.45))
        contact_line = f"대한민국 법원 전자소송(ECFS) 표준 서식  |  대표전화: {statement.contact}" if statement.contact else "대한민국 법원 전자소송(ECFS) 표준 서식"
        page.insert_text(pymupdf.Point(margin_l, 46), contact_line, fontname=font_ko, fontsize=7.5, color=(0.5, 0.5, 0.5))
        page.draw_line(pymupdf.Point(margin_l, 52), pymupdf.Point(margin_r, 52), color=(0.85, 0.88, 0.92), width=0.8)
        return page

    page = create_page()

    # Document Title
    page.insert_text(pymupdf.Point(215, 80), "증  거  설  명  서", fontname=font_ko, fontsize=18, color=(0.08, 0.15, 0.32))

    # Case info block
    box_rect = pymupdf.Rect(margin_l, 98, margin_r, 168)
    page.draw_rect(box_rect, color=(0.82, 0.86, 0.92), fill=(0.97, 0.98, 0.99))

    page.insert_text(pymupdf.Point(margin_l + 12, 115), f"사        건    {statement.case_no}  {statement.case_name}", fontname=font_ko, fontsize=9.5, color=(0.1, 0.1, 0.1))
    page.insert_text(pymupdf.Point(margin_l + 12, 131), f"원고(고소인)    {statement.plaintiff}", fontname=font_ko, fontsize=9.5, color=(0.1, 0.1, 0.1))
    page.insert_text(pymupdf.Point(margin_l + 12, 147), f"피고(피의자)    {statement.defendant}", fontname=font_ko, fontsize=9.5, color=(0.1, 0.1, 0.1))
    page.insert_text(pymupdf.Point(margin_l + 12, 161), "위 사건에 관하여 원고(고소인)의 소송대리인은 주장사실을 입증하기 위해 아래와 같이 증거방법을 제출합니다.", fontname=font_ko, fontsize=8.2, color=(0.3, 0.3, 0.3))

    page.insert_text(pymupdf.Point(265, 185), "다        음", fontname=font_ko, fontsize=11, color=(0.1, 0.1, 0.1))

    hdr_h = 20.0
    col4_w = (margin_r - 5) - (margin_l + 285)

    def draw_table_header(y_top: float) -> None:
        page.draw_rect(pymupdf.Rect(margin_l, y_top, margin_r, y_top + hdr_h), color=(0.75, 0.8, 0.88), fill=(0.9, 0.93, 0.97))
        page.insert_text(pymupdf.Point(margin_l + 8, y_top + 14), "호증", fontname=font_ko, fontsize=8.5, color=(0.12, 0.2, 0.35))
        page.insert_text(pymupdf.Point(margin_l + 55, y_top + 14), "서증(증거)의 명칭", fontname=font_ko, fontsize=8.5, color=(0.12, 0.2, 0.35))
        page.insert_text(pymupdf.Point(margin_l + 180, y_top + 14), "작성자 및 일자", fontname=font_ko, fontsize=8.5, color=(0.12, 0.2, 0.35))
        page.insert_text(pymupdf.Point(margin_l + 285, y_top + 14), "입증취지 및 위법성 요건 대조", fontname=font_ko, fontsize=8.5, color=(0.12, 0.2, 0.35))

    # Measure each row's required height across ALL free-text cells —
    # insert_textbox returns the spare height, negative on overflow, so
    # needed = rect_height - spare. Measuring only the widest column would
    # let a long document name silently clip.
    col2_w, col3_w = 120.0, 100.0
    measure = pymupdf.open()
    probe_page = measure.new_page(width=page_w, height=page_h)
    row_heights: list[float] = []
    fitted_purpose: list[str] = []
    fitted_doc: list[str] = []
    fitted_author: list[str] = []
    for entry in statement.entries:
        def _needed(width: float, text: str, size: float) -> float:
            probe = pymupdf.Rect(0, 0, width, 2000)
            return probe.height - probe_page.insert_textbox(probe, text or " ", fontname=font_ko, fontsize=size)

        def _fit(width: float, text: str, size: float, max_h: float) -> str:
            """Trim text so it renders inside max_h — with an explicit
            ellipsis marker instead of silent clipping (a clipped purpose
            statement would hide legal mapping from the filing)."""
            text = text or " "
            if _needed(width, text, size) <= max_h:
                return text
            marker = " …(이후 내용 생략 — 원문은 감정 데이터 참조)"
            lo, hi = 0, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if _needed(width, text[:mid].rstrip() + marker, size) <= max_h:
                    lo = mid
                else:
                    hi = mid - 1
            return text[:lo].rstrip() + marker

        fitted_purpose.append(_fit(col4_w, entry.purpose_of_proof, 6.8, 606.0))
        fitted_doc.append(_fit(col2_w, entry.document_name, 7.5, 606.0))
        fitted_author.append(_fit(col3_w, entry.author_date, 7.5, 606.0))
        needed = max(
            _needed(col4_w, fitted_purpose[-1], 6.8),
            _needed(col2_w, fitted_doc[-1], 7.5),
            _needed(col3_w, fitted_author[-1], 7.5),
        )
        row_heights.append(min(max(58.0, needed + 10.0), 620.0))
    measure.close()

    # Table Header
    y = 196.0
    draw_table_header(y)
    y += hdr_h

    # Table rows
    for idx, entry in enumerate(statement.entries):
        row_h = row_heights[idx]
        if y + row_h > 720:
            page = create_page()
            y = 70.0
            draw_table_header(y)
            y += hdr_h

        # Alternating background
        if idx % 2 == 1:
            page.draw_rect(pymupdf.Rect(margin_l, y, margin_r, y + row_h), color=(0.97, 0.98, 0.99), fill=(0.97, 0.98, 0.99))
        page.draw_line(pymupdf.Point(margin_l, y + row_h), pymupdf.Point(margin_r, y + row_h), color=(0.88, 0.9, 0.93), width=0.5)

        # Col 1: Exhibit No
        page.insert_text(pymupdf.Point(margin_l + 8, y + 20), entry.exhibit_no, fontname=font_ko, fontsize=8.5, color=(0.1, 0.15, 0.3))

        # Col 2: Document Name
        doc_rect = pymupdf.Rect(margin_l + 55, y + 6, margin_l + 175, y + row_h - 4)
        page.insert_textbox(doc_rect, fitted_doc[idx], fontname=font_ko, fontsize=7.5, color=(0.2, 0.2, 0.2))

        # Col 3: Author and Date
        auth_rect = pymupdf.Rect(margin_l + 180, y + 6, margin_l + 280, y + row_h - 4)
        page.insert_textbox(auth_rect, fitted_author[idx], fontname=font_ko, fontsize=7.5, color=(0.3, 0.3, 0.3))

        # Col 4: Purpose of proof & legal mapping
        purpose_rect = pymupdf.Rect(margin_l + 285, y + 4, margin_r - 5, y + row_h - 4)
        # Compact single-line summary with statutes
        page.insert_textbox(purpose_rect, fitted_purpose[idx], fontname=font_ko, fontsize=6.8, color=(0.15, 0.15, 0.15))

        y += row_h

    # Provenance + integrity disclosure (screening caveat goes on every copy)
    hashed = sum(1 for e in statement.entries if e.sha256)
    if y + 130 > 760:
        page = create_page()
        y = 70.0
    disc_box = pymupdf.Rect(margin_l, y, margin_r, y + 55)
    page.draw_rect(disc_box, color=(0.85, 0.88, 0.92), fill=(0.98, 0.98, 0.99))
    page.insert_text(pymupdf.Point(margin_l + 8, y + 14), "무결성/해석 고지", fontname=font_ko, fontsize=8, color=(0.35, 0.35, 0.35))
    page.insert_textbox(
        pymupdf.Rect(margin_l + 8, y + 18, margin_r - 8, y + 52),
        (f"원본 해시 산출: {hashed}/{len(statement.entries)}건. "
         "본 문서의 결론은 자동 분석 결과로 유죄·불법성의 직접 증거가 아니며, 정밀 감정은 별도로 수행되어야 합니다. "
         + (statement.reference_note + " " if statement.reference_note else "")
         + statement.provenance_note.replace("\n", " / ")),
        fontname=font_ko, fontsize=6.8, color=(0.4, 0.4, 0.4),
    )
    y += 65.0

    # Signoff Block
    if y + 90 > 760:
        page = create_page()
        y = 70.0
    else:
        y += 20.0

    page.insert_text(pymupdf.Point(235, y + 15), statement.created_at, fontname=font_ko, fontsize=10, color=(0.1, 0.1, 0.1))
    page.insert_text(pymupdf.Point(180, y + 35), f"원고(고소인) 소송대리인  {statement.law_firm}", fontname=font_ko, fontsize=10.5, color=(0.08, 0.15, 0.32))
    page.insert_text(pymupdf.Point(195, y + 50), "담당변호사 : ○ ○ ○,  ○ ○ ○", fontname=font_ko, fontsize=9.5, color=(0.2, 0.2, 0.2))
    page.insert_text(pymupdf.Point(185, y + 63), "디지털포렌식센터 수석감정관 : ○ ○ ○  (인)", fontname=font_ko, fontsize=9.5, color=(0.2, 0.2, 0.2))

    page.insert_text(pymupdf.Point(margin_l, y + 85), statement.court, fontname=font_ko, fontsize=12, color=(0.05, 0.05, 0.05))

    # G30: signature block (or the explicit "서명 없음" lines).
    sig_lines = statement_signature_lines(body)
    sig_y = y + 105.0
    if sig_y + 9.0 * len(sig_lines) > 800:
        page = create_page()
        sig_y = 70.0
    for line in sig_lines:
        page.insert_text(pymupdf.Point(margin_l, sig_y), line, fontname=font_ko, fontsize=6.5, color=(0.35, 0.35, 0.35))
        sig_y += 9.0

    tmp = output.with_suffix(output.suffix + ".tmp")
    doc.save(str(tmp))
    doc.close()
    tmp.replace(output)
    return body
