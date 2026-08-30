from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .benchmark import run_benchmark, write_benchmark, write_benchmark_markdown
from .collection import write_collection_plan
from .core import DEFAULT_MAX_FILES, RiskBand, ScanItem, scan_directory, scan_to_json_text, summarize
from .datasets import write_audit, write_manifest, write_robustness_plan, write_split_plan
from .evaluate import calibrate_dataset, evaluate_dataset, evaluate_robustness_dataset, train_portable_baseline, write_cases_jsonl, write_json_report
from .fusion import FusionProfile, apply_fusion_to_items, calibrate_fusion_profile, load_fusion_profile, write_fusion_profile
from .model_registry import list_detector_candidates, write_detector_registry, write_runtime_profile
from .perf import run_performance_check, write_performance_check
from .release import write_release_checklist
from .reports import write_eval_html_report, write_html_report, write_pdf_report
from .pixel import DEFAULT_PIXEL_MAX_SIDE, SUPPORTED_PIXEL_MODES
from .security import write_security_check
from .training import write_neural_training_plan
from .audio import analyze_audio, AudioAnalysis
from .face import analyze_faces, FaceAnalysis
from .video import extract_video_frames, write_video_frame_plan
from .video_analysis import analyze_video_temporal, VideoTemporalAnalysis
from .inpaint import analyze_inpainting, InpaintAnalysis
from .text_advanced import analyze_text_advanced, TextAdvancedAnalysis
from .c2pa import analyze_metadata_forensic, MetadataForensicAnalysis
from .classifier import classify_metadata, classify_text_content, ClassificationResult as ToolClassificationResult
from .multimodal import analyze_multimodal, MultimodalAnalysis
from .realtime import RealtimeDetector, create_realtime_detector
from .evidence import create_evidence_chain, generate_forensic_report
from .api_server import run_server as run_api_server
from .batch import BatchProcessor
from .xai import explain_classification, format_explanation_text
from .ai_agent import analyze_agent_content, AgentAnalysis
from .threed import analyze_3d_content, ThreeDAnalysis
from .avatar import analyze_avatar, AvatarAnalysis
from .pixel_analyzer import analyze_pixels
from .ml_classifier import SimpleClassifier
from .enhanced_forensics import analyze_forensic
from .webapp import run_server


COMMANDS = {"scan", "collect", "dataset", "eval", "benchmark", "fusion", "calibrate", "train", "train-neural-plan", "models", "video", "video-analysis", "audio", "face", "inpaint", "text-advanced", "forensic", "classify", "multimodal", "realtime", "evidence", "api-serve", "batch", "explain", "agent", "3d", "avatar", "pixel-analysis", "ml-classify", "legal-report", "perf", "security", "release", "web", "-h", "--help"}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS:
        argv.insert(0, "scan")

    parser = argparse.ArgumentParser(prog="deepfake-lens", description="Local AI-generated image/text folder scanner.")
    subparsers = parser.add_subparsers(dest="command")
    scan_parser = subparsers.add_parser("scan", help="scan a folder")
    scan_parser.add_argument("folder", type=Path, help="folder to scan")
    scan_parser.add_argument("--recursive", action="store_true", help="scan recursively instead of direct children only")
    scan_parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES, help=f"maximum files to inspect (default: {DEFAULT_MAX_FILES})")
    scan_parser.add_argument("--include-low", action="store_true", help="print low-signal and unsupported rows in the table")
    scan_parser.add_argument("--format", choices=["table", "json"], default="table", help="stdout format")
    scan_parser.add_argument("--json-out", type=Path, help="write full JSON report")
    scan_parser.add_argument("--csv-out", type=Path, help="write compact CSV report")
    scan_parser.add_argument("--text-bytes", type=int, default=64 * 1024, help="maximum text bytes read from each text file")
    scan_parser.add_argument("--metadata-bytes", type=int, default=4 * 1024 * 1024, help="maximum leading bytes read from each image file")
    scan_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="off", help="run local pixel-level experts for images")
    scan_parser.add_argument("--pixel-max-side", type=int, default=DEFAULT_PIXEL_MAX_SIDE, help=f"maximum sampled side for pixel analysis (default: {DEFAULT_PIXEL_MAX_SIDE})")
    scan_parser.add_argument("--heatmaps", action="store_true", help="write PNG heatmaps for deep pixel localization")
    scan_parser.add_argument("--heatmap-dir", type=Path, help="directory for heatmaps (default: folder/deepfake_lens_heatmaps)")
    scan_parser.add_argument("--model-path", type=Path, help="optional external model profile or neural checkpoint path")
    scan_parser.add_argument("--fusion-profile", type=Path, help="optional score-fusion profile")
    scan_parser.add_argument("--cache", type=Path, help="JSON cache for resumable large-folder scans")
    scan_parser.add_argument("--workers", type=int, default=1, help="parallel file workers for large folders")
    scan_parser.add_argument("--dedupe", action="store_true", help="hash files and mark duplicate content")
    scan_parser.add_argument("--hash-db", type=Path, help="persist duplicate hashes across incremental scans")
    scan_parser.add_argument("--max-file-bytes", type=int, help="skip files larger than this size")
    scan_parser.add_argument("--allow-symlinks", action="store_true", help="follow symlinked files")
    scan_parser.add_argument("--progress", action="store_true", help="print coarse progress messages")
    scan_parser.add_argument("--html-out", type=Path, help="write HTML report")
    scan_parser.add_argument("--pdf-out", type=Path, help="write simple PDF report")
    scan_parser.add_argument("--redact-paths", action="store_true", help="redact paths in HTML/PDF reports")

    collect_parser = subparsers.add_parser("collect", help="write a dataset collection plan")
    collect_parser.add_argument("folder", type=Path)
    collect_parser.add_argument("--out", type=Path, required=True)
    collect_parser.add_argument("--minimum-per-source", type=int)

    dataset_parser = subparsers.add_parser("dataset", help="discover a labeled dataset and write a manifest")
    dataset_parser.add_argument("folder", type=Path)
    dataset_parser.add_argument("--manifest-out", type=Path, required=True)
    dataset_parser.add_argument("--fingerprints", action="store_true", help="include SHA-256 fingerprints in the manifest")
    dataset_parser.add_argument("--audit-out", type=Path, help="write dataset audit JSON")
    dataset_parser.add_argument("--split-out", type=Path, help="write deterministic train/val/test split plan")
    dataset_parser.add_argument("--split-ratios", default="0.8,0.1,0.1", help="train,val,test split ratios")
    dataset_parser.add_argument("--split-seed", default="deepfake-lens-v1")
    dataset_parser.add_argument("--robustness-out", type=Path, help="write robustness transform plan")
    dataset_parser.add_argument("--no-recursive", action="store_true")

    eval_parser = subparsers.add_parser("eval", help="evaluate a labeled dataset")
    eval_parser.add_argument("folder", type=Path)
    eval_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="deep")
    eval_parser.add_argument("--pixel-max-side", type=int, default=DEFAULT_PIXEL_MAX_SIDE)
    eval_parser.add_argument("--calibration", type=Path)
    eval_parser.add_argument("--model-path", type=Path)
    eval_parser.add_argument("--fusion-profile", type=Path)
    eval_parser.add_argument("--max-files", type=int)
    eval_parser.add_argument("--json-out", type=Path)
    eval_parser.add_argument("--html-out", type=Path)
    eval_parser.add_argument("--false-positive-out", type=Path)
    eval_parser.add_argument("--false-negative-out", type=Path)
    eval_parser.add_argument("--robustness", action="store_true", help="also summarize transform-named robustness folders")
    eval_parser.add_argument("--redact-paths", action="store_true")

    benchmark_parser = subparsers.add_parser("benchmark", help="run a matrix benchmark across pixel modes and model profiles")
    benchmark_parser.add_argument("folder", type=Path)
    benchmark_parser.add_argument("--pixel-modes", default="off,deep", help="comma-separated pixel modes")
    benchmark_parser.add_argument("--model-path", type=Path, action="append", default=[])
    benchmark_parser.add_argument("--fusion-profile", type=Path)
    benchmark_parser.add_argument("--robustness", action="store_true")
    benchmark_parser.add_argument("--max-files", type=int)
    benchmark_parser.add_argument("--json-out", type=Path, required=True)
    benchmark_parser.add_argument("--md-out", type=Path)

    fusion_parser = subparsers.add_parser("fusion", help="calibrate a metadata/pixel/model/source fusion profile")
    fusion_parser.add_argument("folder", type=Path)
    fusion_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="deep")
    fusion_parser.add_argument("--model-path", type=Path)
    fusion_parser.add_argument("--target-fpr", type=float, default=0.05)
    fusion_parser.add_argument("--max-files", type=int)
    fusion_parser.add_argument("--out", type=Path, required=True)

    calibrate_parser = subparsers.add_parser("calibrate", help="fit a score threshold from a labeled dataset")
    calibrate_parser.add_argument("folder", type=Path)
    calibrate_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="deep")
    calibrate_parser.add_argument("--pixel-max-side", type=int, default=DEFAULT_PIXEL_MAX_SIDE)
    calibrate_parser.add_argument("--target-fpr", type=float, default=0.05)
    calibrate_parser.add_argument("--max-files", type=int)
    calibrate_parser.add_argument("--out", type=Path, required=True)

    train_parser = subparsers.add_parser("train", help="train a portable threshold baseline from a labeled dataset")
    train_parser.add_argument("folder", type=Path)
    train_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="deep")
    train_parser.add_argument("--pixel-max-side", type=int, default=DEFAULT_PIXEL_MAX_SIDE)
    train_parser.add_argument("--target-fpr", type=float, default=0.05)
    train_parser.add_argument("--max-files", type=int)
    train_parser.add_argument("--out", type=Path, required=True)

    models_parser = subparsers.add_parser("models", help="list researched detector integration candidates")
    models_parser.add_argument("--focus", help="filter by task, key, name, or adapter target")
    models_parser.add_argument("--json-out", type=Path)
    models_parser.add_argument("--profile-out", type=Path, help="write a runtime profile for a candidate checkpoint")
    models_parser.add_argument("--candidate", default="aide-iclr-2025")
    models_parser.add_argument("--checkpoint", type=Path)
    models_parser.add_argument("--runtime", choices=["onnx", "torchscript"])
    models_parser.add_argument("--input-size", type=int, default=224)
    models_parser.add_argument("--score-index", type=int, default=1)

    neural_parser = subparsers.add_parser("train-neural-plan", help="write a neural training and ONNX export plan")
    neural_parser.add_argument("folder", type=Path)
    neural_parser.add_argument("--out", type=Path, required=True)
    neural_parser.add_argument("--output-dir", type=Path, required=True)
    neural_parser.add_argument("--architecture", default="convnext_tiny")
    neural_parser.add_argument("--image-size", type=int, default=224)
    neural_parser.add_argument("--epochs", type=int, default=10)

    video_parser = subparsers.add_parser("video", help="plan or run video frame extraction for image scanning")
    video_parser.add_argument("folder", type=Path)
    video_parser.add_argument("--out", type=Path, required=True)
    video_parser.add_argument("--frame-root", type=Path, required=True)
    video_parser.add_argument("--sample-every", type=float, default=2.0)
    video_parser.add_argument("--no-recursive", action="store_true")
    video_parser.add_argument("--extract", action="store_true", help="run ffmpeg commands after writing the plan")
    video_parser.add_argument("--extract-limit", type=int)

    audio_parser = subparsers.add_parser("audio", help="analyze audio files for AI generation or voice cloning")
    audio_parser.add_argument("file", type=Path, help="audio file to analyze")
    audio_parser.add_argument("--segment-seconds", type=int, default=30, help="maximum seconds to analyze (default: 30)")
    audio_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    audio_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    face_parser = subparsers.add_parser("face", help="analyze images for face manipulation")
    face_parser.add_argument("file", type=Path, help="image file to analyze")
    face_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    face_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    video_analysis_parser = subparsers.add_parser("video-analysis", help="analyze video temporal consistency")
    video_analysis_parser.add_argument("file", type=Path, help="video file to analyze")
    video_analysis_parser.add_argument("--frame-rate", type=float, default=1.0, help="frame sample rate (default: 1.0 fps)")
    video_analysis_parser.add_argument("--max-frames", type=int, default=100, help="maximum frames to analyze (default: 100)")
    video_analysis_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    video_analysis_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    inpaint_parser = subparsers.add_parser("inpaint", help="analyze images for inpainting or partial manipulation")
    inpaint_parser.add_argument("file", type=Path, help="image file to analyze")
    inpaint_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    inpaint_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    text_advanced_parser = subparsers.add_parser("text-advanced", help="advanced text analysis for AI generation detection")
    text_advanced_parser.add_argument("file", type=Path, help="text file to analyze")
    text_advanced_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    text_advanced_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    forensic_parser = subparsers.add_parser("forensic", help="analyze metadata for C2PA, SynthID, and provenance signals")
    forensic_parser.add_argument("file", type=Path, help="file to analyze")
    forensic_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    forensic_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    classify_parser = subparsers.add_parser("classify", help="classify which AI tool generated the content")
    classify_parser.add_argument("file", type=Path, help="file to analyze")
    classify_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    classify_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    multimodal_parser = subparsers.add_parser("multimodal", help="combine multiple modality analyses into unified assessment")
    multimodal_parser.add_argument("--image-score", type=int, help="image analysis score")
    multimodal_parser.add_argument("--text-score", type=int, help="text analysis score")
    multimodal_parser.add_argument("--audio-score", type=int, help="audio analysis score")
    multimodal_parser.add_argument("--video-score", type=int, help="video analysis score")
    multimodal_parser.add_argument("--image-source", type=str, help="image source guess")
    multimodal_parser.add_argument("--text-source", type=str, help="text source guess")
    multimodal_parser.add_argument("--audio-source", type=str, help="audio source guess")
    multimodal_parser.add_argument("--video-source", type=str, help="video source guess")
    multimodal_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    multimodal_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    realtime_parser = subparsers.add_parser("realtime", help="run realtime deepfake detection")
    realtime_parser.add_argument("--window-size", type=int, default=30, help="moving average window size (default: 30)")
    realtime_parser.add_argument("--alert-threshold", type=int, default=67, help="alert threshold (default: 67)")
    realtime_parser.add_argument("--warning-threshold", type=int, default=35, help="warning threshold (default: 35)")
    realtime_parser.add_argument("--scores", type=str, help="comma-separated scores to process (for testing)")
    realtime_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    realtime_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    evidence_parser = subparsers.add_parser("evidence", help="create forensic evidence chain")
    evidence_parser.add_argument("file", type=Path, help="file to create evidence for")
    evidence_parser.add_argument("--analyst-id", type=str, default="system", help="analyst identifier")
    evidence_parser.add_argument("--output", type=Path, help="output evidence file")

    api_parser = subparsers.add_parser("api-serve", help="start REST API server")
    api_parser.add_argument("--host", type=str, default="127.0.0.1", help="host to bind")
    api_parser.add_argument("--port", type=int, default=8765, help="port to listen on")
    api_parser.add_argument("--token", type=str, help="require an X-API-Token header on /api routes (mandatory for non-localhost hosts)")

    batch_parser = subparsers.add_parser("batch", help="process files in batch")
    batch_parser.add_argument("folder", type=Path, help="folder to process")
    batch_parser.add_argument("--workers", type=int, default=4, help="number of workers")
    batch_parser.add_argument("--output", type=Path, help="output results file")

    explain_parser = subparsers.add_parser("explain", help="explain classification decision")
    explain_parser.add_argument("--score", type=int, required=True, help="classification score")
    explain_parser.add_argument("--signals", type=str, help="JSON signals array")
    explain_parser.add_argument("--format", choices=["text", "json"], default="text", help="output format")

    agent_parser = subparsers.add_parser("agent", help="analyze content for AI agent generation")
    agent_parser.add_argument("--text", type=str, help="text to analyze")
    agent_parser.add_argument("--file", type=Path, help="file to analyze")
    agent_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    agent_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    threed_parser = subparsers.add_parser("3d", help="analyze content for 3D AI generation")
    threed_parser.add_argument("--file", type=Path, help="file to analyze")
    threed_parser.add_argument("--text", type=str, help="text to analyze")
    threed_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    threed_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    avatar_parser = subparsers.add_parser("avatar", help="analyze content for AI avatar generation")
    avatar_parser.add_argument("--file", type=Path, help="file to analyze")
    avatar_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    avatar_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    pixel_parser = subparsers.add_parser("pixel-analysis", help="analyze image pixels for AI generation")
    pixel_parser.add_argument("file", type=Path, help="image file to analyze")
    pixel_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    pixel_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    ml_parser = subparsers.add_parser("ml-classify", help="classify image using ML features")
    ml_parser.add_argument("file", type=Path, help="image file to analyze")
    ml_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    ml_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    legal_parser = subparsers.add_parser("legal-report", help="generate legal forensic report")
    legal_parser.add_argument("file", type=Path, help="file to analyze")
    legal_parser.add_argument("--output", type=Path, help="output report file")

    perf_parser = subparsers.add_parser("perf", help="measure scan throughput and cache/hash behavior")
    perf_parser.add_argument("folder", type=Path)
    perf_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="off")
    perf_parser.add_argument("--workers", type=int, default=1)
    perf_parser.add_argument("--cache", type=Path)
    perf_parser.add_argument("--hash-db", type=Path)
    perf_parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    perf_parser.add_argument("--no-recursive", action="store_true")
    perf_parser.add_argument("--out", type=Path, required=True)

    release_parser = subparsers.add_parser("release", help="write a release readiness checklist")
    release_parser.add_argument("--out", type=Path, required=True)

    security_parser = subparsers.add_parser("security", help="write a local-only security guardrail report")
    security_parser.add_argument("--out", type=Path, required=True)

    web_parser = subparsers.add_parser("web", help="start the local web app")
    web_parser.add_argument("--folder", type=Path)
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8765)
    web_parser.add_argument("--allow-lan", action="store_true")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "collect":
        payload = write_collection_plan(args.folder, args.out, minimum_per_source=args.minimum_per_source)
        print(json.dumps({"out": str(args.out), "targets": len(payload["targets"])}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "dataset":
        recursive = not args.no_recursive
        summary, _ = write_manifest(args.folder, args.manifest_out, recursive=recursive, include_fingerprints=args.fingerprints)
        if args.audit_out:
            write_audit(args.folder, args.audit_out, recursive=recursive)
        if args.split_out:
            try:
                train_ratio, val_ratio, test_ratio = _parse_split_ratios(args.split_ratios)
            except argparse.ArgumentTypeError as exc:
                dataset_parser.error(str(exc))
            write_split_plan(
                args.folder,
                args.split_out,
                recursive=recursive,
                train_ratio=train_ratio,
                val_ratio=val_ratio,
                test_ratio=test_ratio,
                seed=args.split_seed,
            )
        if args.robustness_out:
            write_robustness_plan(args.folder, args.robustness_out, recursive=recursive)
        print(json.dumps(summary.to_json(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "eval":
        fusion_profile = load_fusion_profile(args.fusion_profile)
        evaluator = evaluate_robustness_dataset if args.robustness else evaluate_dataset
        payload = evaluator(
            args.folder,
            pixel_mode=args.pixel,
            pixel_max_side=args.pixel_max_side,
            calibration_path=args.calibration,
            model_path=args.model_path,
            fusion_profile=fusion_profile,
            max_files=args.max_files,
        )
        if args.json_out:
            write_json_report(args.json_out, payload)
        if args.html_out:
            write_eval_html_report(args.html_out, payload, redact_paths=args.redact_paths)
        cases = payload.get("case_summary", {}) if isinstance(payload.get("case_summary"), dict) else {}
        if args.false_positive_out:
            rows = cases.get("false_positives", []) if isinstance(cases.get("false_positives"), list) else []
            write_cases_jsonl(args.false_positive_out, rows)
        if args.false_negative_out:
            rows = cases.get("false_negatives", []) if isinstance(cases.get("false_negatives"), list) else []
            write_cases_jsonl(args.false_negative_out, rows)
        print(json.dumps(payload["metrics"], ensure_ascii=False, indent=2))
        return 0
    if args.command == "benchmark":
        pixel_modes = _parse_csv(args.pixel_modes)
        fusion_profile = load_fusion_profile(args.fusion_profile)
        payload = run_benchmark(
            args.folder,
            pixel_modes=pixel_modes,
            model_paths=[None, *args.model_path],
            fusion_profile=fusion_profile,
            robustness=args.robustness,
            max_files=args.max_files,
        )
        write_benchmark(args.json_out, payload)
        if args.md_out:
            write_benchmark_markdown(args.md_out, payload)
        print(json.dumps({"out": str(args.json_out), "rows": len(payload["rows"]), "best": payload["best"]}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "fusion":
        payload = calibrate_fusion_profile(
            args.folder,
            pixel_mode=args.pixel,
            model_path=args.model_path,
            target_false_positive_rate=args.target_fpr,
            max_files=args.max_files,
        )
        profile_payload = payload["profile"] if isinstance(payload.get("profile"), dict) else {}
        write_fusion_profile(
            args.out,
            FusionProfile(
                version=str(profile_payload.get("version", "fusion-profile-v1")),
                weights={str(key): float(value) for key, value in dict(profile_payload.get("weights", {})).items()},
                threshold=int(profile_payload.get("threshold", 67) or 67),
                unknown_below=int(profile_payload.get("unknown_below", 8) or 8),
            ),
        )
        print(json.dumps({"out": str(args.out), "threshold": payload["profile"]["threshold"], "metrics": payload["metrics"]}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "calibrate":
        payload = calibrate_dataset(
            args.folder,
            pixel_mode=args.pixel,
            pixel_max_side=args.pixel_max_side,
            target_false_positive_rate=args.target_fpr,
            max_files=args.max_files,
        )
        write_json_report(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == "train":
        payload = train_portable_baseline(
            args.folder,
            pixel_mode=args.pixel,
            pixel_max_side=args.pixel_max_side,
            target_false_positive_rate=args.target_fpr,
            max_files=args.max_files,
        )
        write_json_report(args.out, payload)
        print(json.dumps({"out": str(args.out), "threshold": payload["threshold"], "metrics": payload.get("metrics", {})}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "models":
        payload = list_detector_candidates(focus=args.focus)
        if args.json_out:
            write_detector_registry(args.json_out, focus=args.focus)
        if args.profile_out:
            if not args.checkpoint:
                models_parser.error("--profile-out requires --checkpoint")
            profile = write_runtime_profile(
                args.profile_out,
                args.candidate,
                args.checkpoint,
                runtime=args.runtime,
                input_size=args.input_size,
                score_index=args.score_index,
            )
            payload["runtime_profile"] = profile
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == "train-neural-plan":
        payload = write_neural_training_plan(
            args.folder,
            args.out,
            output_dir=args.output_dir,
            architecture=args.architecture,
            image_size=args.image_size,
            epochs=args.epochs,
        )
        print(json.dumps({"out": str(args.out), "checkpoint": payload["artifacts"]["checkpoint"]}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "video":
        payload = write_video_frame_plan(
            args.folder,
            args.out,
            frame_root=args.frame_root,
            recursive=not args.no_recursive,
            sample_every_seconds=args.sample_every,
        )
        if args.extract:
            payload["extraction"] = extract_video_frames(payload, limit=args.extract_limit)
            args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"count": payload["count"], "ffmpeg_available": payload["ffmpeg_available"], "out": str(args.out)}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "audio":
        analysis = analyze_audio(args.file, segment_seconds=args.segment_seconds)
        if args.json_out:
            args.json_out.write_text(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.format == "json":
            print(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2))
        else:
            print(f"Score: {analysis.score} ({analysis.band_label})")
            print(f"Verdict: {analysis.verdict}")
            print(f"Source: {analysis.source_guess}")
            if analysis.signals:
                print("Signals:")
                for signal in analysis.signals:
                    print(f"  - [{signal.weight}] {signal.title}: {signal.detail}")
        return 0
    if args.command == "face":
        analysis = analyze_faces(args.file)
        if args.json_out:
            args.json_out.write_text(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.format == "json":
            print(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2))
        else:
            print(f"Score: {analysis.score} ({analysis.band_label})")
            print(f"Verdict: {analysis.verdict}")
            print(f"Faces: {analysis.face_count}, Type: {analysis.manipulation_type}")
            if analysis.signals:
                print("Signals:")
                for signal in analysis.signals:
                    print(f"  - [{signal.weight}] {signal.title}: {signal.detail}")
        return 0
    if args.command == "video-analysis":
        analysis = analyze_video_temporal(args.file, frame_sample_rate=args.frame_rate, max_frames=args.max_frames)
        if args.json_out:
            args.json_out.write_text(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.format == "json":
            print(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2))
        else:
            print(f"Score: {analysis.score} ({analysis.band_label})")
            print(f"Verdict: {analysis.verdict}")
            print(f"Frames: {analysis.frame_count}, Duration: {analysis.duration_seconds:.1f}s, FPS: {analysis.fps:.1f}")
            if analysis.signals:
                print("Signals:")
                for signal in analysis.signals:
                    print(f"  - [{signal.weight}] {signal.title}: {signal.detail}")
        return 0
    if args.command == "inpaint":
        analysis = analyze_inpainting(args.file)
        if args.json_out:
            args.json_out.write_text(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.format == "json":
            print(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2))
        else:
            print(f"Score: {analysis.score} ({analysis.band_label})")
            print(f"Verdict: {analysis.verdict}")
            print(f"Regions detected: {analysis.regions_detected}")
            if analysis.signals:
                print("Signals:")
                for signal in analysis.signals:
                    print(f"  - [{signal.weight}] {signal.title}: {signal.detail}")
        return 0
    if args.command == "text-advanced":
        text = args.file.read_text(encoding="utf-8", errors="replace")
        analysis = analyze_text_advanced(text)
        if args.json_out:
            args.json_out.write_text(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.format == "json":
            print(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2))
        else:
            print(f"Score: {analysis.score} ({analysis.band_label})")
            print(f"Verdict: {analysis.verdict}")
            print(f"AI Probability: {analysis.ai_probability:.2%}")
            print(f"Style: {analysis.style_profile}")
            if analysis.signals:
                print("Signals:")
                for signal in analysis.signals:
                    print(f"  - [{signal.weight}] {signal.title}: {signal.detail}")
        return 0
    if args.command == "forensic":
        analysis = analyze_metadata_forensic(args.file)
        if args.json_out:
            args.json_out.write_text(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.format == "json":
            print(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2))
        else:
            print(f"Score: {analysis.score} ({analysis.band_label})")
            print(f"Verdict: {analysis.verdict}")
            print(f"C2PA: {analysis.has_c2pa}, SynthID: {analysis.has_synthid}, Watermark: {analysis.has_watermark}")
            if analysis.signals:
                print("Signals:")
                for signal in analysis.signals:
                    print(f"  - [{signal.weight}] {signal.title}: {signal.detail}")
        return 0
    if args.command == "classify":
        # Read file and try to extract metadata or text for classification
        try:
            import struct
            data = args.file.read_bytes()
            metadata = {}
            result = None
            
            # Check if it's a text file
            text_extensions = {'.txt', '.md', '.py', '.js', '.json', '.csv', '.log'}
            if args.file.suffix.lower() in text_extensions:
                # Text file - classify text content
                text_content = data.decode('utf-8', errors='ignore')
                result = classify_text_content(text_content)
            # Simple metadata extraction from PNG/JPEG
            elif data[:8] == b"\x89PNG\r\n\x1a\n":
                # PNG - extract text chunks
                offset = 8
                while offset + 8 <= len(data):
                    length = struct.unpack(">I", data[offset:offset+4])[0]
                    chunk_type = data[offset+4:offset+8]
                    if chunk_type in (b"tEXt", b"iTXt"):
                        chunk_data = data[offset+8:offset+8+length]
                        if b"\x00" in chunk_data:
                            key, value = chunk_data.split(b"\x00", 1)
                            metadata[key.decode("latin-1", errors="ignore")] = value.decode("utf-8", errors="ignore")
                    offset += 12 + length
                    if chunk_type == b"IEND":
                        break
                result = classify_metadata(metadata)
            elif data[:2] == b"\xff\xd8":
                # JPEG - simple marker scan
                metadata["format"] = "jpeg"
                metadata["size"] = str(len(data))
                result = classify_metadata(metadata)
            else:
                # Unknown format - try metadata
                result = classify_metadata(metadata)
            
            if args.json_out:
                args.json_out.write_text(json.dumps(result.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if args.format == "json":
                print(json.dumps(result.to_json(), ensure_ascii=False, indent=2))
            else:
                print(f"Category: {result.category}")
                print(f"Confidence: {result.confidence}")
                if result.primary_match:
                    print(f"Primary: {result.primary_match.name} ({result.primary_match.provider})")
                if result.matches:
                    print("Matches:")
                    for match in result.matches:
                        print(f"  - [{match.confidence:.2f}] {match.name} ({match.provider}): {', '.join(match.evidence)}")
        except Exception as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        return 0
    if args.command == "multimodal":
        analysis = analyze_multimodal(
            image_score=args.image_score,
            text_score=args.text_score,
            audio_score=args.audio_score,
            video_score=args.video_score,
            image_source_guess=args.image_source,
            text_source_guess=args.text_source,
            audio_source_guess=args.audio_source,
            video_source_guess=args.video_source,
        )
        if args.json_out:
            args.json_out.write_text(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.format == "json":
            print(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2))
        else:
            print(f"Score: {analysis.score} ({analysis.band_label})")
            print(f"Verdict: {analysis.verdict}")
            print(f"Modalities: {', '.join(analysis.modalities_used)}")
            print(f"Consistency: {analysis.consistency_score:.2f}")
            print(f"AI Probability: {analysis.overall_ai_probability:.2%}")
            if analysis.signals:
                print("Signals:")
                for signal in analysis.signals:
                    print(f"  - [{signal.weight}] {signal.title}: {signal.detail} ({signal.source_modality})")
        return 0
    if args.command == "realtime":
        detector = create_realtime_detector(
            window_size=args.window_size,
            alert_threshold=args.alert_threshold,
            warning_threshold=args.warning_threshold,
        )
        
        # Process scores if provided (for testing)
        if args.scores:
            scores = [int(part) for part in args.scores.split(",") if part.strip()]
            for score in scores:
                state = detector.process_frame(score)
        else:
            # Demo mode with sample scores
            demo_scores = [20, 25, 30, 80, 85, 90, 25, 30, 20]
            for score in demo_scores:
                state = detector.process_frame(score)
        
        summary = detector.get_summary()
        
        if args.json_out:
            output = {"state": state.to_json(), "summary": summary}
            args.json_out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        
        if args.format == "json":
            print(json.dumps({"state": state.to_json(), "summary": summary}, ensure_ascii=False, indent=2))
        else:
            print(f"Current Score: {state.current_score}")
            print(f"Average Score: {state.average_score:.1f}")
            print(f"Band: {state.band_label}")
            print(f"Frames Processed: {state.frame_count}")
            print(f"Alerts: {len(state.alerts)}")
            if state.alerts:
                print("Recent Alerts:")
                for alert in state.alerts[-3:]:
                    print(f"  - [{alert.band}] {alert.message}")
        
        return 0
    if args.command == "evidence":
        chain = create_evidence_chain(
            args.file,
            results={"analyst_id": args.analyst_id},
            analyst_id=args.analyst_id,
        )
        if args.output:
            from .evidence import save_evidence_chains
            save_evidence_chains([chain], args.output)
            print(json.dumps({"output": str(args.output), "hash": chain.file_hash}, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(chain.to_json(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "api-serve":
        from .api_server import LOCAL_HOSTS
        if args.host not in LOCAL_HOSTS and not args.token:
            api_parser.error("--token is required when binding a non-localhost host; the API reads local files on request")
        run_api_server(host=args.host, port=args.port, token=args.token)
        return 0
    if args.command == "batch":
        processor = BatchProcessor(max_workers=args.workers)
        files = [path for path in args.folder.glob("*") if path.is_file()]
        from .core import analyze_file
        job = processor.process_batch(files, lambda f: analyze_file(f).to_json())
        if args.output:
            from .batch import save_batch_results
            save_batch_results(job, args.output)
            print(json.dumps({"output": str(args.output), "job_id": job.job_id, "processed": job.processed_files}, ensure_ascii=False, indent=2))
        else:
            summary = processor.get_summary(job.job_id)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.command == "explain":
        signals = []
        if args.signals:
            try:
                signals = json.loads(args.signals)
            except json.JSONDecodeError:
                pass
        explanation = explain_classification(args.score, signals)
        if args.format == "json":
            print(json.dumps(explanation.to_json(), ensure_ascii=False, indent=2))
        else:
            print(format_explanation_text(explanation))
        return 0
    if args.command == "agent":
        text = args.text
        if args.file:
            text = args.file.read_text(encoding="utf-8", errors="replace")
        analysis = analyze_agent_content(text=text)
        if args.json_out:
            args.json_out.write_text(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.format == "json":
            print(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2))
        else:
            print(f"Score: {analysis.score} ({analysis.band_label})")
            print(f"Verdict: {analysis.verdict}")
            print(f"Agent Type: {analysis.agent_type}")
            if analysis.signals:
                print("Signals:")
                for signal in analysis.signals:
                    print(f"  - [{signal.weight}] {signal.title}: {signal.detail}")
        return 0
    if args.command == "3d":
        text = args.text
        if args.file:
            text = args.file.read_text(encoding="utf-8", errors="replace")
        analysis = analyze_3d_content(text=text)
        if args.json_out:
            args.json_out.write_text(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.format == "json":
            print(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2))
        else:
            print(f"Score: {analysis.score} ({analysis.band_label})")
            print(f"Verdict: {analysis.verdict}")
            print(f"Content Type: {analysis.content_type}")
            if analysis.signals:
                print("Signals:")
                for signal in analysis.signals:
                    print(f"  - [{signal.weight}] {signal.title}: {signal.detail}")
        return 0
    if args.command == "avatar":
        analysis = analyze_avatar(file_path=str(args.file) if args.file else None)
        if args.json_out:
            args.json_out.write_text(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.format == "json":
            print(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2))
        else:
            print(f"Score: {analysis.score} ({analysis.band_label})")
            print(f"Verdict: {analysis.verdict}")
            print(f"Avatar Type: {analysis.avatar_type}")
            if analysis.signals:
                print("Signals:")
                for signal in analysis.signals:
                    print(f"  - [{signal.weight}] {signal.title}: {signal.detail}")
        return 0
    if args.command == "pixel-analysis":
        analysis = analyze_pixels(args.file)
        if args.json_out:
            args.json_out.write_text(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.format == "json":
            print(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2))
        else:
            print(f"Score: {analysis.score} ({analysis.band_label})")
            print(f"Verdict: {analysis.verdict}")
            if analysis.signals:
                print("Signals:")
                for signal in analysis.signals:
                    print(f"  - [{signal.weight}] {signal.title}: {signal.detail}")
        return 0
    if args.command == "ml-classify":
        # Extract features and classify
        try:
            import cv2
            import numpy as np
            image = cv2.imread(str(args.file))
            if image is None:
                print(json.dumps({"error": "이미지를 읽을 수 없습니다"}, ensure_ascii=False, indent=2))
                return 1
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            # Simple feature extraction
            features = {
                "mean": float(np.mean(gray)),
                "std": float(np.std(gray)),
                "texture_variance": float(np.var(cv2.Laplacian(gray, cv2.CV_64F))),
            }
            classifier = SimpleClassifier()
            result = classifier.predict(features)
            if args.json_out:
                args.json_out.write_text(json.dumps(result.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if args.format == "json":
                print(json.dumps(result.to_json(), ensure_ascii=False, indent=2))
            else:
                print(f"Prediction: {result.prediction}")
                print(f"Confidence: {result.confidence:.2f}")
                print(f"AI Probability: {result.probability_ai:.2%}")
        except ImportError:
            print(json.dumps({"error": "opencv/numpy가 설치되어 있지 않습니다"}, ensure_ascii=False, indent=2))
            return 1
        return 0
    if args.command == "legal-report":
        report = analyze_forensic(args.file)
        legal_text = report.generate_legal_text()
        if args.output:
            args.output.write_text(legal_text, encoding="utf-8")
            print(json.dumps({"output": str(args.output), "report_id": report.report_id}, ensure_ascii=False, indent=2))
        else:
            print(legal_text)
        return 0
    if args.command == "perf":
        payload = run_performance_check(
            args.folder,
            recursive=not args.no_recursive,
            pixel_mode=args.pixel,
            workers=args.workers,
            cache_path=args.cache,
            hash_db_path=args.hash_db,
            max_files=args.max_files,
        )
        write_performance_check(args.out, payload)
        print(json.dumps({"out": str(args.out), "files_per_second": payload["files_per_second"], "summary": payload["summary"]}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "release":
        payload = write_release_checklist(Path.cwd(), args.out)
        print(json.dumps({"out": str(args.out), "entrypoint_present": payload["entrypoint_present"]}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "security":
        payload = write_security_check(Path.cwd(), args.out)
        print(json.dumps({"out": str(args.out), "passed": payload["passed"]}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "web":
        run_server(args.host, args.port, default_folder=args.folder, allow_lan=args.allow_lan)
        return 0

    if args.max_files < 1:
        scan_parser.error("--max-files must be at least 1")
    if args.text_bytes < 1:
        scan_parser.error("--text-bytes must be at least 1")
    if args.metadata_bytes < 1:
        scan_parser.error("--metadata-bytes must be at least 1")
    if args.pixel_max_side < 16:
        scan_parser.error("--pixel-max-side must be at least 16")
    if args.workers < 1:
        scan_parser.error("--workers must be at least 1")
    if args.max_file_bytes is not None and args.max_file_bytes < 1:
        scan_parser.error("--max-file-bytes must be at least 1")
    if args.heatmaps and args.pixel != "deep":
        scan_parser.error("--heatmaps requires --pixel deep")
    if args.model_path and not args.model_path.exists():
        scan_parser.error("--model-path does not exist")

    try:
        if args.progress:
            print(f"Analyzing {args.folder} with workers={args.workers}, pixel={args.pixel}...", file=sys.stderr)
        summary, items = scan_directory(
            args.folder,
            recursive=args.recursive,
            max_files=args.max_files,
            text_bytes=args.text_bytes,
            metadata_bytes=args.metadata_bytes,
            pixel_mode=args.pixel,
            pixel_max_side=args.pixel_max_side,
            heatmaps=args.heatmaps,
            heatmap_dir=args.heatmap_dir,
            model_path=args.model_path,
            cache_path=args.cache,
            workers=args.workers,
            max_file_bytes=args.max_file_bytes,
            allow_symlinks=args.allow_symlinks,
            dedupe=args.dedupe,
            hash_db_path=args.hash_db,
        )
        fusion_profile = load_fusion_profile(args.fusion_profile)
        if fusion_profile:
            items = apply_fusion_to_items(items, fusion_profile)
            summary = summarize(items, capped=summary.capped, cached=summary.cached)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.progress:
        print(f"Done: analyzed={summary.analyzed}, cached={summary.cached}, total={summary.total}", file=sys.stderr)

    if args.json_out:
        args.json_out.write_text(scan_to_json_text(summary, items) + "\n", encoding="utf-8")
    if args.csv_out:
        _write_csv(args.csv_out, items)
    if args.html_out:
        write_html_report(args.html_out, summary, items, redact_paths=args.redact_paths)
    if args.pdf_out:
        write_pdf_report(args.pdf_out, summary, items, redact_paths=args.redact_paths)

    if args.format == "json":
        print(scan_to_json_text(summary, items))
    else:
        _print_table(summary, items, include_low=args.include_low)
    return 0


def _print_table(summary, items: list[ScanItem], *, include_low: bool) -> None:
    cap_note = " (cap reached)" if summary.capped else ""
    print(
        f"Scanned {summary.total} files{cap_note}: "
        f"high={summary.high}, medium={summary.medium}, unknown={summary.unknown}, "
        f"low={summary.low}, unsupported/failed={summary.unsupported_or_failed}, "
        f"duplicates={summary.duplicates}, skipped={summary.skipped}, cached={summary.cached}"
    )
    print("참고용 선별 결과입니다. 메타데이터가 없으면 '출처 단서 없음'으로 남깁니다.")
    print()
    print(f"{'risk':<12} {'score':>5} {'pixel':>5} {'source':<28} {'kind':<6} file")
    print("-" * 100)
    visible = [item for item in items if include_low or _is_priority_row(item)]
    if not visible:
        print("우선 검토할 후보가 없습니다. --include-low 로 전체 행을 볼 수 있습니다.")
        return
    for item in visible:
        if item.result:
            risk = item.result.band_label
            score = str(item.result.score)
            pixel = _pixel_score_text(item)
            source = item.result.source_guess.label[:27]
            reason = item.result.signals[0].title if item.result.signals else "강한 의심 신호 없음"
        else:
            risk = item.status
            score = "-"
            pixel = "-"
            source = "-"
            reason = item.error or ""
        print(f"{risk:<12} {score:>5} {pixel:>5} {source:<28} {item.kind:<6} {item.path}  # {reason}")


def _is_priority_row(item: ScanItem) -> bool:
    if item.status != "analyzed" or not item.result:
        return False
    return item.result.band in {RiskBand.HIGH, RiskBand.MEDIUM, RiskBand.UNKNOWN}


def _write_csv(path: Path, items: list[ScanItem]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "path",
                "kind",
                "status",
                "score",
                "risk",
                "pixel_score",
                "pixel_confidence",
                "pixel_model",
                "pixel_fusion",
                "pixel_top_experts",
                "external_model",
                "external_model_score",
                "heatmap_path",
                "source",
                "source_confidence",
                "top_signal",
                "error",
            ]
        )
        for item in items:
            result = item.result
            pixel = result.pixel_analysis if result else None
            model = result.model_analysis if result else None
            writer.writerow(
                [
                    item.path,
                    item.kind,
                    item.status,
                    result.score if result else "",
                    result.band_label if result else "",
                    pixel.score if pixel and pixel.available else "",
                    pixel.confidence if pixel and pixel.available else "",
                    pixel.model if pixel and pixel.available else "",
                    pixel.fusion if pixel and pixel.available else "",
                    _pixel_top_experts(pixel) if pixel and pixel.available else "",
                    model.model if model and model.available else "",
                    model.score if model and model.available else "",
                    pixel.heatmap_path if pixel and pixel.heatmap_path else "",
                    result.source_guess.label if result else "",
                    result.source_guess.confidence.value if result else "",
                    result.signals[0].title if result and result.signals else "",
                    item.error or "",
                ]
            )


def _pixel_score_text(item: ScanItem) -> str:
    pixel = item.result.pixel_analysis if item.result else None
    if not pixel:
        return "-"
    if not pixel.available:
        return "n/a"
    return str(pixel.score)


def _pixel_top_experts(pixel) -> str:
    active = [expert for expert in pixel.experts if expert.available and expert.score >= 45]
    return ";".join(expert.name for expert in sorted(active, key=lambda expert: expert.score, reverse=True)[:5])


def _parse_split_ratios(value: str) -> tuple[float, float, float]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("--split-ratios must be train,val,test")
    try:
        train, val, test = (float(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--split-ratios values must be numbers") from exc
    if train < 0 or val < 0 or test < 0 or train + val + test <= 0:
        raise argparse.ArgumentTypeError("--split-ratios must be non-negative and sum to more than zero")
    return train, val, test


def _parse_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
