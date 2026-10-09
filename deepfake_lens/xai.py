"""Explainable AI (XAI) module for deepfake detection.

Provides human-readable breakdowns of unmeasured heuristic scores:
which signals contributed and how much. It never states a conclusion —
conclusions come only from the scan's decision rules (D1).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from .layer_diagnostic import REFERENCE_BAND, raw_score_note


@dataclass(frozen=True)
class FeatureImportance:
    feature_name: str
    importance_score: float
    direction: str
    explanation: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class XAIExplanation:
    """Human-readable breakdown of an unmeasured heuristic score (D1).

    Only describes which signals made up ``overall_score``: no 67/35 band,
    no "AI 생성 가능성 높음/자연스러운 콘텐츠" decision text, no
    high/medium/low confidence. ``reference_band`` follows
    :mod:`deepfake_lens.layer_diagnostic`; conclusions come only from the
    scan's decision rules (decision.py).
    """

    overall_score: int
    reference_band: str
    reference_note: str
    signal_count: int
    summary: str
    feature_importances: list[FeatureImportance]
    decision_path: list[str]
    limitations: list[str]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def explain_classification(
    score: int,
    signals: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> XAIExplanation:
    """Describe the signals behind a raw heuristic score (no conclusion)."""
    feature_importances = []
    limitations = []

    # Analyze signals for feature importance
    for signal in signals:
        if isinstance(signal, dict):
            title = signal.get("title", "")
            weight = signal.get("weight", 0)
            detail = signal.get("detail", "")

            importance = FeatureImportance(
                feature_name=title,
                importance_score=weight / 100.0,
                direction="positive" if weight > 0 else "negative",
                explanation=detail,
            )
            feature_importances.append(importance)

    # Sort by importance
    feature_importances.sort(key=lambda x: x.importance_score, reverse=True)
    top = feature_importances[:3]

    decision_path = [
        f"참고 원점수 {score}/100 — 신호 가중치의 합이며 측정·보정되지 않았습니다",
        f"신호 {len(feature_importances)}개 집계"
        + (f" (상위: {', '.join(f.feature_name for f in top)})" if top else ""),
        "결론은 scan의 결정 규칙(결정적 근거·보정된 확률)에서만 나옵니다",
    ]
    summary = f"참고 원점수 {score}/100, 신호 {len(feature_importances)}개."
    if top:
        summary += f" 가중치 상위 신호: {', '.join(f.feature_name for f in top)}."

    # Limitations
    limitations.append("이 설명은 측정되지 않은 휴리스틱 신호의 구성을 보여 줄 뿐 결론이 아닙니다.")
    limitations.append("확정적 판별이 아닌 선별 도구로 활용해야 합니다.")
    if not metadata:
        limitations.append("메타데이터가 없어 분석이 제한적일 수 있습니다.")

    return XAIExplanation(
        overall_score=score,
        reference_band=REFERENCE_BAND,
        reference_note=raw_score_note("설명 대상", score),
        signal_count=len(feature_importances),
        summary=summary,
        feature_importances=feature_importances[:10],  # Top 10
        decision_path=decision_path,
        limitations=limitations,
    )


def explain_audio_classification(
    score: int,
    signals: list[dict[str, Any]],
) -> XAIExplanation:
    """Generate explanation for audio classification."""
    return explain_classification(score, signals)


def explain_face_classification(
    score: int,
    signals: list[dict[str, Any]],
    face_count: int = 0,
    manipulation_type: str = "unknown",
) -> XAIExplanation:
    """Generate explanation for face classification."""
    from .face import manipulation_type_label

    explanation = explain_classification(score, signals)
    
    # Add face-specific context
    if face_count > 0:
        explanation = XAIExplanation(
            overall_score=explanation.overall_score,
            reference_band=explanation.reference_band,
            reference_note=explanation.reference_note,
            signal_count=explanation.signal_count,
            summary=explanation.summary + f" {face_count}개의 얼굴이 감지되었습니다.",
            feature_importances=explanation.feature_importances,
            decision_path=explanation.decision_path + [f"얼굴 조작 유형 추정(휴리스틱): {manipulation_type_label(manipulation_type)}"],
            limitations=explanation.limitations,
        )
    
    return explanation


def explain_video_classification(
    score: int,
    signals: list[dict[str, Any]],
    frame_count: int = 0,
    duration: float = 0.0,
) -> XAIExplanation:
    """Generate explanation for video classification."""
    explanation = explain_classification(score, signals)
    
    # Add video-specific context
    if frame_count > 0:
        explanation = XAIExplanation(
            overall_score=explanation.overall_score,
            reference_band=explanation.reference_band,
            reference_note=explanation.reference_note,
            signal_count=explanation.signal_count,
            summary=explanation.summary + f" {frame_count}개 프레임 분석 (재생시간: {duration:.1f}초).",
            feature_importances=explanation.feature_importances,
            decision_path=explanation.decision_path,
            limitations=explanation.limitations,
        )
    
    return explanation


def format_explanation_text(explanation: XAIExplanation) -> str:
    """Format explanation as human-readable text."""
    lines = [
        "=== 참고 신호 구성(결론 아님) ===",
        f"참고 원점수(미측정): {explanation.overall_score}/100",
        f"신호 수: {explanation.signal_count}",
        f"참고: {explanation.reference_note}",
        "",
        "=== 요약 ===",
        f"{explanation.summary}",
        "",
        "=== 주요 요인 ===",
    ]
    
    for i, feature in enumerate(explanation.feature_importances[:5], 1):
        lines.append(f"{i}. {feature.feature_name} (중요도: {feature.importance_score:.2f})")
        lines.append(f"   {feature.explanation}")
    
    lines.append("")
    lines.append("=== 집계 경로 ===")
    for step in explanation.decision_path:
        lines.append(f"  - {step}")
    
    lines.append("")
    lines.append("=== 제한 사항 ===")
    for limitation in explanation.limitations:
        lines.append(f"  - {limitation}")
    
    return "\n".join(lines)
