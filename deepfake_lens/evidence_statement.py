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

        lines.extend([
            "",
            "### [증거 무결성 검증 (Chain of Custody)]",
            "모든 제출 서증은 무결성 훼손 방지를 위해 디지털포렌식 포구 절차를 준수하여 수집되었으며, 원본 해시(SHA-256) 대조를 통해 동일성이 보장됩니다.",
            "",
            f"**제출일자**: {self.created_at}",
            f"**원고(고소인) 소송대리인**: {self.law_firm} {self.center}",
            f"**대표전화**: {self.contact}",
            f"**제출처**: **{self.court}**",
        ])
        return "\n".join(lines)


def _compute_sha256(path: Path | str) -> str:
    p = Path(path)
    if not p.is_file():
        return "N/A (가상 또는 미생성 파일)"
    h = hashlib.sha256()
    try:
        with p.open("rb") as f:
            while chunk := f.read(64 * 1024):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return "N/A (파일 읽기 실패)"


def _determine_statutes(score: int, signals: list[Any], item_kind: str) -> list[str]:
    statutes = []
    sig_titles = [s.title for s in signals] if signals else []

    is_faceswap = any("얼굴" in t or "안면" in t or "스왑" in t or "턱선" in t for t in sig_titles)
    is_video = item_kind == "video"

    if score >= 50:
        if is_faceswap or is_video:
            statutes.append("성폭력범죄의 처벌 등에 관한 특례법 제14조의2 (허위영상물 등의 반포등)")
            statutes.append("형법 제347조 (사기 - 신원도용 및 기망)")
        statutes.append("정보통신망 이용촉진 및 정보보호 등에 관한 법률 제70조 (벌칙 - 명예훼손)")
    else:
        statutes.append("정보통신망 이용촉진 및 정보보호 등에 관한 법률 제70조 (비방 목적 정보유통)")

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
) -> EvidenceStatement:
    """Build an EvidenceStatement from analyzed scan items."""
    entries: list[EvidenceStatementEntry] = []
    now_date = datetime.now().strftime("%Y. %m. %d.")

    for idx, item in enumerate(items, start=1):
        res = item.result
        score = res.score if res else 0
        band = res.band_label if res else (item.status or "판단 불가")
        signals = res.signals if res else []
        file_sha256 = _compute_sha256(item.path)

        exhibit_no = f"{exhibit_prefix}{idx}호증"
        doc_name = f"디지털 증거 파일 ({Path(item.path).name}) 및 AI 감정 데이터"
        author_date = f"{law_firm}\n{now_date}"

        statutes = _determine_statutes(score, signals, item.kind)

        top_signals = [f"• {s.title} ({s.detail[:55]}...)" for s in signals[:2]]
        sig_text = "\n".join(top_signals) if top_signals else "• 인공지능 생성/합성 흔적 정밀 검사"

        statute_text = "\n".join([f"• 관련 법조: {st}" for st in statutes])

        purpose = (
            f"[합성 위험도: {band} ({score}점)]\n"
            f"피고인(피의자)이 피해자의 동의 없이 인공지능 딥페이크 기술을 이용하여 제작·배포한 불법 합성물임을 입증함.\n"
            f"{sig_text}\n"
            f"{statute_text}\n"
            f"• 원본 SHA-256: {file_sha256[:20]}..."
        )

        entries.append(
            EvidenceStatementEntry(
                exhibit_no=exhibit_no,
                document_name=doc_name,
                author_date=author_date,
                purpose_of_proof=purpose,
                sha256=file_sha256,
                score=score,
                band_label=band,
                file_path=item.path,
                statutes=statutes,
            )
        )

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
        page.insert_text(pymupdf.Point(margin_l, 35), "법무법인(유한) 대륜 디지털포렌식 감정센터", fontname=font_ko, fontsize=9, color=(0.15, 0.25, 0.45))
        page.insert_text(pymupdf.Point(margin_l, 46), "대한민국 법원 전자소송(ECFS) 표준 서식  |  대표전화: 02-780-1128", fontname=font_ko, fontsize=7.5, color=(0.5, 0.5, 0.5))
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

    doc.save(str(output))
    doc.close()
