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
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .core import BatchScanSummary, ScanItem


@dataclass(frozen=True)
class EvidenceStatementEntry:
    exhibit_no: str
    document_name: str
    author_date: str
    purpose_of_proof: str
    sha256: str
    score: int
    band_label: str
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
            "### [증거 무결성 고지 (Chain of Custody)]",
            integrity_line,
            "본 문서는 자동 스크리닝 도구의 결과를 요약한 것으로, 탐지 점수는 유죄·불법성에 대한 법적 판단이 아닙니다.",
            "",
        ])
        if self.provenance_note:
            lines.extend(["### [분석 프로비넌스]", self.provenance_note, ""])
        lines.extend([
            f"**제출일자**: {self.created_at}",
            f"**원고(고소인) 소송대리인**: {self.law_firm} {self.center}",
            f"**대표전화**: {self.contact}",
            f"**제출처**: **{self.court}**",
        ])
        return "\n".join(lines)


def _compute_sha256(path: Path | str) -> str | None:
    """Full streaming hash of the evidence file, or None if unavailable.

    A path string is never hashed as a substitute for content — an absent
    hash must be rendered as 'unavailable', not as a digest-shaped value.
    """
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    try:
        with p.open("rb") as f:
            while chunk := f.read(64 * 1024):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _determine_statutes(score: int, signals: list[Any], item_kind: str, band: str) -> list[str]:
    """Statutes a legal reviewer may consider — only when the screen flags risk.

    A screening score is not proof of a crime. Statutes are listed as
    '검토 참고' candidates only for high/medium bands, and never asserted
    for low/unknown/failed items.
    """
    if band not in ("high", "medium"):
        return []
    statutes = []
    sig_titles = [s.title for s in signals] if signals else []

    is_faceswap = any("얼굴" in t or "안면" in t or "스왑" in t or "턱선" in t for t in sig_titles)
    is_video = item_kind == "video"

    if band == "high":
        if is_faceswap or is_video:
            statutes.append("성폭력범죄의 처벌 등에 관한 특례법 제14조의2 (허위영상물 등의 반포등) — 검토 참고")
            statutes.append("형법 제347조 (사기 - 신원도용 및 기망) — 검토 참고")
        statutes.append("정보통신망 이용촉진 및 정보보호 등에 관한 법률 제70조 (벌칙 - 명예훼손) — 검토 참고")
    else:
        statutes.append("관련 법조 적용 가능성 검토 필요 — 스크리닝 점수만으로 범죄 구성요건 단정 불가")

    return statutes


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
) -> EvidenceStatement:
    """Build an EvidenceStatement from analyzed scan items."""
    entries: list[EvidenceStatementEntry] = []
    now_date = datetime.now().strftime("%Y. %m. %d.")

    for idx, item in enumerate(items, start=1):
        res = item.result
        score = res.score if res else 0
        band_value = res.band.value if res else "unknown"
        band = res.band_label if res else (item.status or "판단 불가")
        signals = res.signals if res else []
        file_sha256 = _compute_sha256(item.path)

        exhibit_no = f"{exhibit_prefix}{idx}호증"
        doc_name = f"디지털 증거 파일 ({Path(item.path).name}) 및 AI 스크리닝 데이터"
        author_date = f"{law_firm}\n{now_date}"

        statutes = _determine_statutes(score, signals, item.kind, band_value)

        # The tool screens and prioritizes; it does not prove crimes.
        # Purpose language must match the actual analytic outcome.
        if band_value == "high":
            purpose_head = "AI 합성 의심 신호가 검출되어 우선 정밀 감정 대상으로 분류된 증거물임을 소명함."
        elif band_value == "medium":
            purpose_head = "일부 합성 관련 신호가 관찰되어 추가 검토가 필요한 증거물임을 소명함."
        elif band_value == "low":
            purpose_head = "AI 스크리닝에서 유의한 합성 신호가 확인되지 않은 증거물임을 소명함."
        else:
            purpose_head = "분석 불가 또는 판단 유보 상태의 증거물로, 별도 검증이 필요함을 소명함."

        top_signals = [f"• {s.title} ({s.detail[:55]}...)" for s in signals[:2]]
        sig_text = "\n".join(top_signals) if top_signals else "• 인공지능 생성/합성 흔적 정밀 검사"

        statute_text = "\n".join([f"• {st}" for st in statutes]) if statutes else "• 관련 법조: 해당 없음 (스크리닝 결과상 단정 불가)"

        hash_text = f"• 원본 SHA-256: {file_sha256}" if file_sha256 else "• 원본 SHA-256: 해시 불가 — 원본 파일 접근 실패 (동일성 확인 요망)"

        purpose = (
            f"[스크리닝 위험도: {band} ({score}점) — 자동 스크리닝 결과로서 유죄·불법성의 직접 증거가 아님]\n"
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
                band_label=band,
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
            f"모델 가중치: {wa}/{wc} 탑재" + (" — 신경망 엔진 미실행, 휴리스틱 전용 결과" if wa == 0 else "")
        )
    if isinstance(thresholds, dict):
        src = thresholds.get("source", "")
        if src == "builtin_defaults":
            prov_lines.append("판정 임계값: 내장 기본값 — 미측정 잠정값(프로비저널)")
        elif src:
            n = thresholds.get("samples", "?")
            fp = thresholds.get("dataset_fingerprint") or ""
            prov_lines.append(f"판정 임계값: 측정 프로파일 {src} (표본 {n}건" + (f", 지문 {fp[:12]}" if fp else "") + ")")
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
    )


def write_evidence_statement_markdown(path: Path | str, statement: EvidenceStatement) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(statement.to_markdown(), encoding="utf-8")


def write_evidence_statement_pdf(path: Path | str, statement: EvidenceStatement) -> None:
    """Render court-admissible Evidence Statement PDF using PyMuPDF with Korean fonts."""
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            raise RuntimeError(
                "pymupdf is required for PDF evidence statements; "
                "install it or use the Markdown output path instead."
            )

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open()
    font_ko = "korea"
    font_en = "helv"

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

    # Measure each row's required height against column 4 (the widest content).
    # insert_textbox returns the spare height — negative when the text would
    # overflow — so needed = rect_height - spare holds for both signs.
    measure = pymupdf.open()
    probe_page = measure.new_page(width=page_w, height=page_h)
    row_heights: list[float] = []
    for entry in statement.entries:
        probe = pymupdf.Rect(0, 0, col4_w, 2000)
        spare = probe_page.insert_textbox(probe, entry.purpose_of_proof, fontname=font_ko, fontsize=6.8)
        needed = probe.height - spare
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
        page.insert_textbox(doc_rect, entry.document_name, fontname=font_ko, fontsize=7.5, color=(0.2, 0.2, 0.2))

        # Col 3: Author and Date
        auth_rect = pymupdf.Rect(margin_l + 180, y + 6, margin_l + 280, y + row_h - 4)
        page.insert_textbox(auth_rect, entry.author_date, fontname=font_ko, fontsize=7.5, color=(0.3, 0.3, 0.3))

        # Col 4: Purpose of proof & legal mapping
        purpose_rect = pymupdf.Rect(margin_l + 285, y + 4, margin_r - 5, y + row_h - 4)
        # Compact single-line summary with statutes
        page.insert_textbox(purpose_rect, entry.purpose_of_proof, fontname=font_ko, fontsize=6.8, color=(0.15, 0.15, 0.15))

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
         "본 문서의 위험도는 자동 스크리닝 결과로 유죄·불법성의 직접 증거가 아니며, 정밀 감정은 별도로 수행되어야 합니다. "
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

    tmp = output.with_suffix(output.suffix + ".tmp")
    doc.save(str(tmp))
    doc.close()
    tmp.replace(output)
