"""Explainable AI (XAI) module for deepfake detection.

Provides human-readable explanations for detection decisions,
feature importance, and decision rationale.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


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
    overall_score: int
    band: str
    confidence: str
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
    """Generate human-readable explanation for a classification result."""
    feature_importances = []
    decision_path = []
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
    
    # Generate decision path
    if score >= 67:
        decision_path.append("높은 점수 임계값(67+) 도달")
        decision_path.append("여러 의심 신호가 동시에 활성화됨")
        decision_path.append("AI 생성 가능성 높음으로 판단")
    elif score >= 35:
        decision_path.append("중간 점수 임계값(35-66) 범위")
        decision_path.append("일부 의심 신호가 감지됨")
        decision_path.append("추가 확인 권장")
    else:
        decision_path.append("낮은 점수 임계값(35 미만)")
        decision_path.append("뚜렷한 의심 신호 부족")
        decision_path.append("자연스러운 콘텐츠로 판단")
    
    # Generate summary
    if score >= 67:
        summary = f"이 콘텐츠는 {score}점으로 높은 AI 생성 의심 점수를 받았습니다. "
        if feature_importances:
            top_features = feature_importances[:3]
            summary += f"주요 요인: {', '.join(f.feature_name for f in top_features)}."
    elif score >= 35:
        summary = f"이 콘텐츠는 {score}점으로 중간 수준의 의심 신호를 보입니다. "
        summary += "추가 검토가 필요합니다."
    else:
        summary = f"이 콘텐츠는 {score}점으로 낮은 의심 점수를 받았습니다. "
        summary += "자연스러운 콘텐츠로 보입니다."
    
    # Determine confidence
    if len(feature_importances) >= 3 and score >= 67:
        confidence = "high"
    elif len(feature_importances) >= 1 and score >= 35:
        confidence = "medium"
    else:
        confidence = "low"
    
    # Limitations
    limitations.append("이 설명은 휴리스틱 기반 분석 결과에 기반합니다.")
    limitations.append("확정적 판별이 아닌 선별 도구로 활용해야 합니다.")
    if not metadata:
        limitations.append("메타데이터가 없어 분석이 제한적일 수 있습니다.")
    
    # Determine band
    if score >= 67:
        band = "high"
    elif score >= 35:
        band = "medium"
    else:
        band = "low"
    
    return XAIExplanation(
        overall_score=score,
        band=band,
        confidence=confidence,
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
    explanation = explain_classification(score, signals)
    
    # Add face-specific context
    if face_count > 0:
        explanation = XAIExplanation(
            overall_score=explanation.overall_score,
            band=explanation.band,
            confidence=explanation.confidence,
            summary=explanation.summary + f" {face_count}개의 얼굴이 감지되었습니다.",
            feature_importances=explanation.feature_importances,
            decision_path=explanation.decision_path + [f"얼굴 조작 유형: {manipulation_type}"],
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
            band=explanation.band,
            confidence=explanation.confidence,
            summary=explanation.summary + f" {frame_count}개 프레임 분석 (재생시간: {duration:.1f}초).",
            feature_importances=explanation.feature_importances,
            decision_path=explanation.decision_path,
            limitations=explanation.limitations,
        )
    
    return explanation


def format_explanation_text(explanation: XAIExplanation) -> str:
    """Format explanation as human-readable text."""
    lines = [
        f"=== 분석 결과 ===",
        f"점수: {explanation.overall_score} ({explanation.band})",
        f"신뢰도: {explanation.confidence}",
        f"",
        f"=== 요약 ===",
        f"{explanation.summary}",
        f"",
        f"=== 주요 요인 ===",
    ]
    
    for i, feature in enumerate(explanation.feature_importances[:5], 1):
        lines.append(f"{i}. {feature.feature_name} (중요도: {feature.importance_score:.2f})")
        lines.append(f"   {feature.explanation}")
    
    lines.append("")
    lines.append("=== 결정 경로 ===")
    for step in explanation.decision_path:
        lines.append(f"  - {step}")
    
    lines.append("")
    lines.append("=== 제한 사항 ===")
    for limitation in explanation.limitations:
        lines.append(f"  - {limitation}")
    
    return "\n".join(lines)
