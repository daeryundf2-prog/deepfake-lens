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
from .video import build_video_frame_plan, discover_videos

__all__ = [
    "BatchScanSummary",
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
    "analyze_file",
    "analyze_text",
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
