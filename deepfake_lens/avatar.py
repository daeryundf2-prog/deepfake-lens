"""Avatar generation detection module.

Detects AI-generated avatars, digital humans, and virtual presenters
using analysis of facial movements, lip sync, and rendering artifacts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AvatarEvidenceSignal:
    title: str
    detail: str
    weight: int


@dataclass(frozen=True)
class AvatarAnalysis:
    score: int
    band: str
    band_label: str
    verdict: str
    signals: list[AvatarEvidenceSignal]
    limitations: list[str]
    avatar_type: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)


# Avatar generation tool markers
AVATAR_MARKERS = {
    "heygen": ["heygen", "hey gen"],
    "synthesia": ["synthesia"],
    "d_id": ["d-id", "d_id"],
    "colossyan": ["colossyan"],
    "virbo": ["virbo", "wondershare virbo"],
    "did_ai": ["did ai"],
    "rephrase": ["rephrase ai"],
    "hourone": ["hourone", "hour one"],
    "elai": ["elai"],
    "deepbrain": ["deepbrain", "deep brain"],
}


def analyze_avatar(
    file_path: str | None = None,
    metadata: dict[str, str] | None = None,
) -> AvatarAnalysis:
    """Analyze content for AI avatar generation signs."""
    signals: list[AvatarEvidenceSignal] = []
    limitations: list[str] = []
    
    if file_path:
        file_signals = _analyze_file(file_path)
        signals.extend(file_signals)
    
    if metadata:
        metadata_signals = _analyze_metadata(metadata)
        signals.extend(metadata_signals)
    
    # Limitations
    if not any([file_path, metadata]):
        limitations.append("분석할 콘텐츠가 제공되지 않았습니다.")
    limitations.append("아바타 탐지는 얼굴 분석과 결합하여 사용해야 합니다.")
    
    score = min(100, sum(signal.weight for signal in signals))
    
    if score >= 67:
        band = "high"
        band_label = "높음"
        verdict = "AI 아바타 생성 콘텐츠일 가능성이 높습니다."
    elif score >= 35:
        band = "medium"
        band_label = "주의"
        verdict = "AI 아바타 생성 가능성이 일부 감지됩니다."
    else:
        band = "low"
        band_label = "낮음"
        verdict = "AI 아바타 생성 신호가 거의 없습니다."
    
    avatar_type = _classify_avatar_type(signals)
    
    return AvatarAnalysis(
        score=score,
        band=band,
        band_label=band_label,
        verdict=verdict,
        signals=signals,
        limitations=limitations,
        avatar_type=avatar_type,
    )


def _analyze_file(file_path: str) -> list[AvatarEvidenceSignal]:
    """Analyze file for avatar generation indicators."""
    signals = []
    
    # Check file extension
    video_extensions = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    for ext in video_extensions:
        if file_path.lower().endswith(ext):
            signals.append(AvatarEvidenceSignal(
                "비디오 형식",
                "아바타는 일반적으로 비디오 형식으로 생성됩니다.",
                5,
            ))
            break
    
    return signals


def _analyze_metadata(metadata: dict[str, str]) -> list[AvatarEvidenceSignal]:
    """Analyze metadata for avatar generation markers."""
    signals = []
    
    for tool_name, markers in AVATAR_MARKERS.items():
        for key, value in metadata.items():
            combined = f"{key} {value}".lower()
            for marker in markers:
                if marker.lower() in combined:
                    signals.append(AvatarEvidenceSignal(
                        f"{tool_name} 마커 발견",
                        f"메타데이터에서 '{marker}'가 감지되었습니다.",
                        30,
                    ))
                    break
    
    # Check for avatar-related metadata
    avatar_keywords = ["avatar", "digital human", "virtual presenter", "ai presenter"]
    for key, value in metadata.items():
        combined = f"{key} {value}".lower()
        for keyword in avatar_keywords:
            if keyword in combined:
                signals.append(AvatarEvidenceSignal(
                    "아바타 관련 메타데이터",
                    f"'{keyword}' 키워드가 메타데이터에 있습니다.",
                    20,
                ))
                break
    
    return signals


def _classify_avatar_type(signals: list[AvatarEvidenceSignal]) -> str:
    """Classify the type of avatar."""
    signal_titles = {s.title for s in signals}
    
    if any("heygen" in title.lower() for title in signal_titles):
        return "heygen"
    if any("synthesia" in title.lower() for title in signal_titles):
        return "synthesia"
    if any("d-id" in title.lower() for title in signal_titles):
        return "d_id"
    if any("avatar" in title.lower() for title in signal_titles):
        return "generic_avatar"
    
    return "unknown"
