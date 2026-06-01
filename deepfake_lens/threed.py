"""3D content generation detection module.

Detects 3D content created using AI generation tools
like NeRF, Gaussian Splatting, and text-to-3D models.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ThreeDEvidenceSignal:
    title: str
    detail: str
    weight: int


@dataclass(frozen=True)
class ThreeDAnalysis:
    score: int
    band: str
    band_label: str
    verdict: str
    signals: list[ThreeDEvidenceSignal]
    limitations: list[str]
    content_type: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)


# 3D generation tool markers
THREED_MARKERS = {
    "nerf": ["nerf", "neural radiance field", "instant-ngp"],
    "gaussian_splatting": ["gaussian splatting", "3dgs", "gaussian splat"],
    "meshy": ["meshy", "meshy ai"],
    "tripo": ["tripo ai", "tripo3d"],
    "luma_3d": ["luma 3d", "luma genie", "luma ai 3d"],
    "nvidia_3d": ["nvidia 3d", "nvidia odin"],
    "openai_3d": ["shap-e", "point-e", "openai 3d"],
    "stability_3d": ["stability 3d", "stability ai 3d"],
}


def analyze_3d_content(
    file_path: str | None = None,
    metadata: dict[str, str] | None = None,
    text: str | None = None,
) -> ThreeDAnalysis:
    """Analyze content for 3D AI generation signs."""
    signals: list[ThreeDEvidenceSignal] = []
    limitations: list[str] = []
    
    if file_path:
        file_signals = _analyze_file_extension(file_path)
        signals.extend(file_signals)
    
    if metadata:
        metadata_signals = _analyze_metadata(metadata)
        signals.extend(metadata_signals)
    
    if text:
        text_signals = _analyze_text(text)
        signals.extend(text_signals)
    
    # Limitations
    if not any([file_path, metadata, text]):
        limitations.append("분석할 콘텐츠가 제공되지 않았습니다.")
    limitations.append("3D 생성 탐지는 아직 초기 단계입니다.")
    
    score = min(100, sum(signal.weight for signal in signals))
    
    if score >= 67:
        band = "high"
        band_label = "높음"
        verdict = "3D AI 생성 콘텐츠일 가능성이 높습니다."
    elif score >= 35:
        band = "medium"
        band_label = "주의"
        verdict = "3D AI 생성 가능성이 일부 감지됩니다."
    else:
        band = "low"
        band_label = "낮음"
        verdict = "3D AI 생성 신호가 거의 없습니다."
    
    content_type = _classify_content_type(signals)
    
    return ThreeDAnalysis(
        score=score,
        band=band,
        band_label=band_label,
        verdict=verdict,
        signals=signals,
        limitations=limitations,
        content_type=content_type,
    )


def _analyze_file_extension(file_path: str) -> list[ThreeDEvidenceSignal]:
    """Analyze file extension for 3D content indicators."""
    signals = []
    
    threed_extensions = {
        ".glb": "GLTF Binary",
        ".gltf": "GLTF",
        ".obj": "Wavefront OBJ",
        ".fbx": "FBX",
        ".ply": "PLY (Point Cloud)",
        ".pcd": "Point Cloud Data",
        ".npy": "NumPy Array (3D data)",
        ".npz": "NumPy Archive (3D data)",
    }
    
    for ext, description in threed_extensions.items():
        if file_path.lower().endswith(ext):
            signals.append(ThreeDEvidenceSignal(
                "3D 파일 형식",
                f"{description} 형식의 파일입니다.",
                10,
            ))
            break
    
    return signals


def _analyze_metadata(metadata: dict[str, str]) -> list[ThreeDEvidenceSignal]:
    """Analyze metadata for 3D generation markers."""
    signals = []
    
    for tool_name, markers in THREED_MARKERS.items():
        for key, value in metadata.items():
            combined = f"{key} {value}".lower()
            for marker in markers:
                if marker.lower() in combined:
                    signals.append(ThreeDEvidenceSignal(
                        f"{tool_name} 마커 발견",
                        f"메타데이터에서 '{marker}'가 감지되었습니다.",
                        25,
                    ))
                    break
    
    return signals


def _analyze_text(text: str) -> list[ThreeDEvidenceSignal]:
    """Analyze text for 3D generation markers."""
    signals = []
    normalized = text.lower()
    
    for tool_name, markers in THREED_MARKERS.items():
        for marker in markers:
            if marker.lower() in normalized:
                signals.append(ThreeDEvidenceSignal(
                    f"{tool_name} 텍스트 마커",
                    f"텍스트에서 '{marker}'가 감지되었습니다.",
                    20,
                ))
                break
    
    return signals


def _classify_content_type(signals: list[ThreeDEvidenceSignal]) -> str:
    """Classify the type of 3D content."""
    signal_titles = {s.title for s in signals}
    
    if any("nerf" in title.lower() for title in signal_titles):
        return "nerf"
    if any("gaussian" in title.lower() for title in signal_titles):
        return "gaussian_splatting"
    if any("mesh" in title.lower() for title in signal_titles):
        return "mesh_generation"
    if any("point" in title.lower() for title in signal_titles):
        return "point_cloud"
    
    return "unknown"
