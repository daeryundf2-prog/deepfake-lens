"""Local AI-generated material screening CLI."""

from .core import (
    BatchScanSummary,
    ClassificationResult,
    EvidenceSignal,
    ScanItem,
    SourceGuess,
    analyze_file,
    analyze_text,
    scan_directory,
)
from .benchmark import run_benchmark
from .collection import build_collection_plan
from .datasets import DatasetAudit, DatasetRecord, DatasetSummary, audit_dataset, discover_dataset, plan_dataset_splits
from .evaluate import calibrate_dataset, evaluate_dataset, evaluate_robustness_dataset, train_portable_baseline
from .fusion import FusionProfile, apply_fusion_to_items, apply_fusion_to_result, calibrate_fusion_profile, load_fusion_profile
from .model_registry import DetectorCandidate, build_runtime_profile, list_detector_candidates
from .perf import run_performance_check
from .pixel import PixelAnalysis, PixelExpertResult
from .security import build_security_check
from .training import build_neural_training_plan
from .audio import AudioAnalysis, analyze_audio
from .face import FaceAnalysis, analyze_faces
from .video import build_video_frame_plan, discover_videos
from .video_analysis import VideoTemporalAnalysis, analyze_video_temporal
from .inpaint import InpaintAnalysis, analyze_inpainting
from .text_advanced import TextAdvancedAnalysis, analyze_text_advanced
from .c2pa import MetadataForensicAnalysis, analyze_metadata_forensic
from .classifier import ClassificationResult as ToolClassificationResult, classify_metadata
from .multimodal import MultimodalAnalysis, analyze_multimodal
from .realtime import RealtimeDetector, RealtimeState, create_realtime_detector
from .evidence import EvidenceChain, ForensicReport, create_evidence_chain, generate_forensic_report
from .xai import XAIExplanation, explain_classification, format_explanation_text
from .batch import BatchProcessor, BatchJob
from .ai_agent import AgentAnalysis, analyze_agent_content
from .threed import ThreeDAnalysis, analyze_3d_content
from .avatar import AvatarAnalysis, analyze_avatar

__all__ = [
    "AudioAnalysis",
    "BatchScanSummary",
    "FaceAnalysis",
    "VideoTemporalAnalysis",
    "InpaintAnalysis",
    "TextAdvancedAnalysis",
    "MetadataForensicAnalysis",
    "ToolClassificationResult",
    "MultimodalAnalysis",
    "RealtimeDetector",
    "AgentAnalysis",
    "ThreeDAnalysis",
    "AvatarAnalysis",
    "EvidenceChain",
    "ForensicReport",
    "XAIExplanation",
    "BatchProcessor",
    "BatchJob",
    "ClassificationResult",
    "DatasetAudit",
    "DatasetRecord",
    "DatasetSummary",
    "DetectorCandidate",
    "EvidenceSignal",
    "FusionProfile",
    "PixelAnalysis",
    "PixelExpertResult",
    "ScanItem",
    "SourceGuess",
    "analyze_audio",
    "analyze_faces",
    "analyze_file",
    "analyze_text",
    "analyze_video_temporal",
    "analyze_inpainting",
    "analyze_text_advanced",
    "analyze_metadata_forensic",
    "classify_metadata",
    "analyze_multimodal",
    "create_realtime_detector",
    "create_evidence_chain",
    "generate_forensic_report",
    "explain_classification",
    "format_explanation_text",
    "analyze_agent_content",
    "analyze_3d_content",
    "analyze_avatar",
    "audit_dataset",
    "build_collection_plan",
    "build_neural_training_plan",
    "build_runtime_profile",
    "build_security_check",
    "build_video_frame_plan",
    "calibrate_dataset",
    "calibrate_fusion_profile",
    "discover_dataset",
    "discover_videos",
    "evaluate_dataset",
    "evaluate_robustness_dataset",
    "apply_fusion_to_items",
    "apply_fusion_to_result",
    "list_detector_candidates",
    "load_fusion_profile",
    "plan_dataset_splits",
    "run_benchmark",
    "run_performance_check",
    "scan_directory",
    "train_portable_baseline",
]
