"""Legal-marking context of a file's machine-readable marks (techniques-2026 item 6).

EU AI Act Art. 50 (transparency obligations, applicable from 2026-08-02; the
Commission's code of practice on marking and labelling AI-generated content
expects machine-readable marks — signed metadata and an imperceptible
watermark) and the Korean AI Basic Act (인공지능기본법, in force 2026-01-22;
the MSIT/TTA transparency guideline names visible labels and invisible
marks such as C2PA metadata or watermarks) both ask providers to make
AI-generated output detectable by machine. This module turns the mark checks
the scan actually ran into a statement of fact for the report:

- which machine-readable marks were checked, and for each: found / not
  found / not run (with the coverage reason);
- which detectors exist only as online services (Google SynthID Detector,
  OpenAI's content-credential check, Kling's AI-content detection) — never
  run, because an analysis uses no network;
- the fixed limitation that **absence of a mark is not evidence of
  originality** (marks are stripped by re-encoding or never applied).

It infers nothing: the item is deterministic, neutral and weak, and the
structured ``marking_context`` object carries the same facts for machines.
"""

from __future__ import annotations

from typing import Any

from .result_types import CoverageEntry, CoverageStatus, EvidenceDirection, EvidenceItem, EvidenceKind, EvidenceStrength

MARKING_TITLE = "기계판독 표식 확인 결과(EU 인공지능법 제50조·인공지능기본법 관련)"
MARKING_LAYER = "marking"
ONLINE_CHECK = "online_detection"
ABSENCE_STATEMENT = "표식이 확인되지 않은 것은 원본성 근거가 아닙니다 — 표식은 재인코딩·편집으로 사라지거나 처음부터 적용되지 않았을 수 있습니다."
LEGAL_BASIS = (
    "EU 인공지능법(AI Act) 제50조(2026-08-02 적용)와 인공지능기본법(2026-01-22 시행)은 생성형 AI 결과물에 기계판독 가능한 표식"
    "(서명된 메타데이터·비가시 워터마크 등)을 요구합니다. 아래는 이 파일에서 실제로 확인한 표식의 사실이며 법적 판단이 아닙니다."
)
ONLINE_REASON = "온라인 전용 검출 서비스(검출기·키 비공개) — 분석 중 네트워크를 쓰지 않으므로 실행하지 않음"
# Online-only detectors per modality (new_techniques_2026-10.md §1.6–1.7).
ONLINE_ONLY: dict[str, tuple[str, ...]] = {
    "image": ("구글 SynthID 검출 서비스(이미지)", "OpenAI 콘텐츠 출처 확인 서비스"),
    "video": ("구글 SynthID 검출 서비스(영상)", "Kling 콘텐츠 검출 서비스(kling.ai)"),
    "audio": ("구글 SynthID 검출 서비스(오디오)",),
    "text": ("구글 SynthID 검출 서비스(텍스트 — 운영자 키가 있으면 --synthid-keys로 로컬 검사)",),
}
OUTCOME_LABELS = {"found": "확인됨", "not_found": "확인 안 됨", "skipped": "미실행", "failed": "실패", "ran": "실행"}
C2PA_LABEL = "C2PA 매니페스트(서명된 출처 메타데이터)"


def c2pa_finding(validation: dict[str, object] | None, entry: CoverageEntry) -> dict[str, str]:
    if entry.status != CoverageStatus.RAN:
        return {"check": "c2pa", "algorithm": C2PA_LABEL, "outcome": entry.status.value, "detail": entry.reason}
    if validation and validation.get("present"):
        from .c2pa import c2pa_state_label

        trust = "신뢰 서명자" if validation.get("trusted") else "신뢰되지 않은 서명자"
        return {"check": "c2pa", "algorithm": C2PA_LABEL, "outcome": "found",
                "detail": f"매니페스트 있음(검증 상태 {c2pa_state_label(validation.get('state'))}, {trust})"}
    return {"check": "c2pa", "algorithm": C2PA_LABEL, "outcome": "not_found", "detail": "매니페스트 없음"}


def online_entry(modality: str) -> CoverageEntry:
    return CoverageEntry(ONLINE_CHECK, CoverageStatus.SKIPPED, f"{', '.join(ONLINE_ONLY.get(modality, ()))}: {ONLINE_REASON}")


def build(modality: str, findings: list[dict[str, str]]) -> tuple[dict[str, Any], EvidenceItem]:
    """``(marking_context object, its evidence item)`` from the mark findings of one file."""
    online = [{"name": name, "status": "skipped", "reason": ONLINE_REASON} for name in ONLINE_ONLY.get(modality, ())]
    found = [f for f in findings if f.get("outcome") == "found"]
    context: dict[str, Any] = {
        "legal_basis": LEGAL_BASIS,
        "checked": [dict(f) for f in findings],
        "online_only": online,
        "marks_found": len(found),
        "absence_statement": ABSENCE_STATEMENT,
    }
    parts = []
    for finding in findings:
        detail = str(finding.get("detail") or "")
        label = OUTCOME_LABELS.get(str(finding.get("outcome")), str(finding.get("outcome")))
        parts.append(f"{finding.get('algorithm')} — {label}" + (f"({detail})" if detail else ""))
    text = (
        f"{LEGAL_BASIS} 확인한 표식: " + ("; ".join(parts) if parts else "없음") + ". "
        + (f"온라인 전용(미실행): {', '.join(item['name'] for item in online)}. " if online else "")
        + ABSENCE_STATEMENT
    )
    item = EvidenceItem(MARKING_TITLE, text, EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, MARKING_LAYER)
    return context, item


__all__ = ["ABSENCE_STATEMENT", "MARKING_LAYER", "MARKING_TITLE", "ONLINE_CHECK", "ONLINE_ONLY", "build", "c2pa_finding", "online_entry"]
