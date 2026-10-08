from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .benchmark import run_benchmark, write_benchmark, write_benchmark_markdown
from .collection import write_collection_plan
from .core import DEFAULT_MAX_FILES, RiskBand, ScanItem, _thresholds_json, scan_directory, scan_to_json, scan_to_json_text, summarize
from .calibration import MIN_CALIBRATION_SAMPLES, load_threshold_profile
from .cli_parser import build_parser
from .cli_render import (
    _file_text,
    _has_subdirectories,
    _is_priority_row,
    _load_thresholds_arg,
    _maybe_sign,
    _parse_csv,
    _parse_split_ratios,
    _pixel_score_text,
    _pixel_top_experts,
    _print_table,
    _write_csv,
    _write_json_out,
)
from .datasets import write_audit, write_manifest, write_robustness_plan, write_split_plan
from .evaluate import calibrate_dataset, evaluate_dataset, evaluate_robustness_dataset, train_portable_baseline, write_cases_jsonl, write_json_report
from .feedback import build_feedback_report, load_feedback, observations_from_scan_payload, observations_live
from .fusion import FusionProfile, apply_fusion_to_items, calibrate_fusion_profile, load_fusion_profile, write_fusion_profile
from .model_registry import list_detector_candidates, write_detector_registry, write_runtime_profile
from .perf import run_performance_check, write_performance_check
from .release import write_release_checklist
from .reports import write_eval_html_report, write_forensic_pdf_report, write_html_report, write_pdf_report
from .pixel import DEFAULT_PIXEL_MAX_SIDE, SUPPORTED_PIXEL_MODES
from .security import write_security_check
from .signing import resolve_report_key, sign_report
from .training import write_neural_training_plan
from .audio import analyze_audio, AudioAnalysis
from .face import analyze_faces, FaceAnalysis
from .video import extract_video_frames, write_video_frame_plan
from .video_analysis import analyze_video_temporal, VideoTemporalAnalysis
from .inpaint import analyze_inpainting, InpaintAnalysis
from .text_advanced import analyze_text_advanced, TextAdvancedAnalysis
from .watermark import detect_kgw_watermark
from .c2pa import analyze_metadata_forensic, MetadataForensicAnalysis
from .classifier import classify_metadata, classify_text_content, ClassificationResult as ToolClassificationResult
from .multimodal import analyze_av_sync, analyze_multimodal, MultimodalAnalysis
from .realtime import RealtimeDetector, create_realtime_detector
from .rppg import analyze_rppg, RppgAnalysis
from .prnu import analyze_prnu, PrnuAnalysis
from .evidence import create_evidence_chain, generate_forensic_report
from .api_server import run_server as run_api_server
from .batch import BatchProcessor
from .xai import explain_classification, format_explanation_text
from .ai_agent import analyze_agent_content, AgentAnalysis
from .threed import analyze_3d_content, ThreeDAnalysis
from .avatar import analyze_avatar, AvatarAnalysis
from .pixel_analyzer import analyze_pixels
from .rule_classifier import RuleClassifier
from .enhanced_forensics import analyze_forensic
from .webapp import run_server
from .faceswap_seam import analyze_faceswap_seam
from .evidence_statement import (
    build_evidence_statement,
    write_evidence_statement_markdown,
    write_evidence_statement_pdf,
)
from .vendor_weights import (
    default_models_dir,
    fetch_weights,
    bundle_offline_weights,
    inspect_model_manifest,
    install_bundle,
    pin_profile,
    verify_offline_integrity,
    weights_coverage,
)


COMMANDS = {"doctor", "scan", "collect", "dataset", "eval", "benchmark", "fusion", "calibrate", "feedback", "train", "train-neural-plan", "models", "video", "video-analysis", "audio", "face", "faceswap-seam", "evidence-statement", "vendor-weights", "inpaint", "text-advanced", "compare", "watermark", "forensic", "classify", "multimodal", "realtime", "rppg", "prnu", "evidence", "api-serve", "batch", "explain", "agent", "3d", "avatar", "pixel-analysis", "ml-classify", "legal-report", "perf", "security", "release", "web", "-h", "--help"}

def _pkg_profile(name: str) -> str:
    # Absolute path into the resolved models dir — works from the source
    # tree and from an installed wheel alike.
    return str(default_models_dir() / name)


DEFAULT_ENGINE_PROFILE = _pkg_profile("aide-runtime.json")
DEFAULT_AUDIO_ENGINE_PROFILE = _pkg_profile("aasist-runtime.json")
# G2: no text model is a default member. The former default (GPT-2-era
# OpenAI detector) and the Korean "detector" (an MLM without a classifier
# head) were removed — see docs/MODEL-REJECTIONS.md.
DEFAULT_TEXT_ENGINE_PROFILE: str | None = None


def default_model_path(root: Path | None = None) -> Path | None:
    """Bundled default-engine profile (models/aide-runtime.json), when present.

    The profile is committed but the checkpoint it points at is not, so the
    adapter degrades gracefully until scripts/fetch_aide.py is run. Returns
    None when the profile is absent so callers keep heuristic-only behavior.
    """
    candidate = (Path(root) / Path(DEFAULT_ENGINE_PROFILE).name) if root is not None else Path(DEFAULT_ENGINE_PROFILE)
    return candidate if candidate.is_file() else None


def default_audio_model_path(root: Path | None = None) -> Path | None:
    """Bundled default audio profile (models/aasist-runtime.json), when present.

    Same contract as default_model_path: the profile is committed, the
    AASIST checkpoint is not (scripts/fetch_aasist.py), and absence returns
    None so audio analysis stays heuristic-only.
    """
    candidate = (Path(root) / Path(DEFAULT_AUDIO_ENGINE_PROFILE).name) if root is not None else Path(DEFAULT_AUDIO_ENGINE_PROFILE)
    return candidate if candidate.is_file() else None


# Additional bundled audio members run alongside AASIST when present. The
# wav2vec XLSR classifier complements AASIST (better calibrated on real
# speech, weaker on out-of-domain TTS); its hub weights download on demand.
DEFAULT_AUDIO_AUX_PROFILES = (_pkg_profile("wav2vec-deepfake-audio-runtime.json"),)


def default_audio_model_paths(root: Path | None = None) -> list[Path]:
    """All bundled audio profiles that exist (AASIST + aux members)."""
    if root is not None:
        return [c for rel in (DEFAULT_AUDIO_ENGINE_PROFILE, *DEFAULT_AUDIO_AUX_PROFILES) if (c := Path(root) / Path(rel).name).is_file()]
    return [Path(rel) for rel in (DEFAULT_AUDIO_ENGINE_PROFILE, *DEFAULT_AUDIO_AUX_PROFILES) if Path(rel).is_file()]


def default_text_model_path(root: Path | None = None) -> Path | None:
    """Bundled default text profile, when one is configured.

    ``DEFAULT_TEXT_ENGINE_PROFILE`` is None since phase 0 (G2), so this
    returns None and text analysis stays lexical/reference-only.
    """
    if DEFAULT_TEXT_ENGINE_PROFILE is None:
        return None
    candidate = (Path(root) / Path(DEFAULT_TEXT_ENGINE_PROFILE).name) if root is not None else Path(DEFAULT_TEXT_ENGINE_PROFILE)
    return candidate if candidate.is_file() else None


def _vendor_weights_pin(args: argparse.Namespace) -> int:
    """``vendor-weights pin <profile>``: write the profile's weight pin (G9)."""
    if not args.profile:
        print("error: 'vendor-weights pin' needs a profile name or path", file=sys.stderr)
        return 2
    try:
        result = pin_profile(args.profile, args.models_dir, revision=args.revision)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: 프로필 고정 실패 — {exc}", file=sys.stderr)
        return 1
    if result["status"] == "needs-manual":
        print(result["instructions"], file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to a legacy code page (e.g. cp949) that cannot
    # encode Korean text or em-dashes; reconfiguring to UTF-8 keeps print()
    # from crashing there. StringIO-style test doubles lack reconfigure.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS:
        argv.insert(0, "scan")

    parser, cmd_parsers = build_parser()

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
                cmd_parsers["dataset"].error(str(exc))
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
            model_path=args.model_path or (None if args.no_default_engine else default_model_path()),
            fusion_profile=fusion_profile,
            max_files=args.max_files,
            thresholds=_load_thresholds_arg(args),
        )
        if args.json_out:
            write_json_report(args.json_out, _maybe_sign(payload, sign=args.sign, key_file=args.key_file))
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
        write_benchmark(args.json_out, _maybe_sign(payload, sign=args.sign, key_file=args.key_file))
        if args.md_out:
            write_benchmark_markdown(args.md_out, payload)
        print(json.dumps({"out": str(args.json_out), "rows": len(payload["rows"]), "best": payload["best"]}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "fusion":
        payload = calibrate_fusion_profile(
            args.folder,
            pixel_mode=args.pixel,
            model_path=args.model_path or (None if args.no_default_engine else default_model_path()),
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
            include_score_mapping=args.mapping_out is not None,
            thresholds=_load_thresholds_arg(args),
        )
        write_json_report(args.out, payload)
        if args.mapping_out:
            write_json_report(args.mapping_out, payload["score_calibration"])
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == "feedback":
        entries = load_feedback(args.labels)
        if args.scan_json:
            try:
                scan_payload = json.loads(args.scan_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"error: cannot read scan JSON: {exc}", file=sys.stderr)
                return 2
            observations, unmatched = observations_from_scan_payload(scan_payload, entries)
        else:
            observations, unmatched = observations_live(
                entries,
                pixel_mode=args.pixel,
                pixel_max_side=args.pixel_max_side,
                model_path=args.model_path or (None if args.no_default_engine else default_model_path()),
            )
        report = build_feedback_report(entries, observations, unmatched, base_profile=load_fusion_profile(args.fusion_profile))
        if args.json_out:
            write_json_report(args.json_out, report)
        if args.profile_out and isinstance(report.get("suggested_profile"), dict):
            suggested = report["suggested_profile"]
            write_fusion_profile(
                args.profile_out,
                FusionProfile(
                    version=str(suggested.get("version", "fusion-profile-v1")),
                    weights={str(key): float(value) for key, value in dict(suggested.get("weights", {})).items()},
                    threshold=int(suggested.get("threshold", 67) or 67),
                    unknown_below=int(suggested.get("unknown_below", 8) or 8),
                ),
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
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
                cmd_parsers["models"].error("--profile-out requires --checkpoint")
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
        audio_model_path = args.model_path or (None if args.no_default_engine else default_audio_model_paths() or None)
        analysis = analyze_audio(args.file, segment_seconds=args.segment_seconds, model_path=audio_model_path)
        if args.json_out:
            _write_json_out(args.json_out, json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n")
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
            _write_json_out(args.json_out, json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n")
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
        analysis = analyze_video_temporal(
            args.file,
            frame_sample_rate=args.frame_rate,
            max_frames=args.max_frames,
            model_path=list(args.model_path) if args.model_path else None,
        )
        if args.json_out:
            _write_json_out(args.json_out, json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n")
        if args.format == "json":
            print(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2))
        else:
            print(f"Score: {analysis.score} ({analysis.band_label})")
            print(f"Verdict: {analysis.verdict}")
            print(f"Frames: {analysis.frame_count}, Duration: {analysis.duration_seconds:.1f}s, FPS: {analysis.fps:.1f}")
            if analysis.model_analysis is not None:
                state = f"score={analysis.model_analysis.score}" if analysis.model_analysis.available else "unavailable"
                print(f"Model: {analysis.model_analysis.model} ({state}) — {analysis.model_analysis.detail}")
            if analysis.signals:
                print("Signals:")
                for signal in analysis.signals:
                    print(f"  - [{signal.weight}] {signal.title}: {signal.detail}")
        return 0
    if args.command == "inpaint":
        analysis = analyze_inpainting(args.file)
        if args.json_out:
            _write_json_out(args.json_out, json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n")
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
            _write_json_out(args.json_out, json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n")
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
    if args.command == "compare":
        from .core import compare_files
        result = compare_files(args.file_a, args.file_b)
        if args.format == "json":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if "error" in result:
                print(f"Error: {result['error']}")
                return 1
            print(f"Kind: {result['kind']}")
            print(f"Score: {result['score']} ({result['band']})")
            print(f"Verdict: {result['verdict']}")
        return 0
    if args.command == "watermark":
        text = _file_text(args.file)
        if text is None:
            print(json.dumps({"error": "텍스트 추출 불가 — 지원되지 않는 형식입니다."}, ensure_ascii=False))
            return 1
        if args.synthid_keys:
            from .watermark import detect_synthid_watermark

            keys = [int(part.strip()) for part in args.synthid_keys.split(",") if part.strip()]
            result = detect_synthid_watermark(text, keys=keys, tokenizer_model=args.tokenizer, tokenizer_revision=args.tokenizer_revision)
        else:
            if not args.secret:
                print(json.dumps({"error": "--secret(KGW) 또는 --synthid-keys(SynthID) 중 하나가 필요합니다."}, ensure_ascii=False))
                return 1
            result = detect_kgw_watermark(text, secret=args.secret, tokenizer_model=args.tokenizer, tokenizer_revision=args.tokenizer_revision, gamma=args.gamma)
        if args.format == "json":
            print(json.dumps(result.to_json(), ensure_ascii=False, indent=2))
        else:
            print(f"Score: {result.score} — {result.verdict}")
            print(f"z={result.z_score}, green={result.green_fraction}, tokens={result.token_count}")
        return 0
    if args.command == "forensic":
        analysis = analyze_metadata_forensic(args.file)
        if args.json_out:
            _write_json_out(args.json_out, json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n")
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
                _write_json_out(args.json_out, json.dumps(result.to_json(), ensure_ascii=False, indent=2) + "\n")
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
        av_sync_result = analyze_av_sync(args.av_sync) if args.av_sync else None
        analysis = analyze_multimodal(
            image_score=args.image_score,
            text_score=args.text_score,
            audio_score=args.audio_score,
            video_score=args.video_score,
            image_source_guess=args.image_source,
            text_source_guess=args.text_source,
            audio_source_guess=args.audio_source,
            video_source_guess=args.video_source,
            av_sync=av_sync_result,
        )
        if args.json_out:
            _write_json_out(args.json_out, json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n")
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
            _write_json_out(args.json_out, json.dumps(output, ensure_ascii=False, indent=2) + "\n")
        
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
    if args.command == "rppg":
        analysis = analyze_rppg(args.file, max_frames=args.max_frames)
        if args.json_out:
            _write_json_out(args.json_out, json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n")
        if args.format == "json":
            print(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2))
        else:
            bpm = f"{analysis.estimated_bpm:.0f}" if analysis.estimated_bpm else "-"
            snr = f"{analysis.peak_snr:.1f}" if analysis.peak_snr is not None else "-"
            print(f"Score: {analysis.score} ({analysis.band_label})")
            print(f"Verdict: {analysis.verdict}")
            print(f"Pulse: {bpm} bpm, SNR: {snr}, Face frames: {analysis.face_frames}")
            if analysis.signals:
                print("Signals:")
                for signal in analysis.signals:
                    print(f"  - [{signal.weight}] {signal.title}: {signal.detail}")
        return 0
    if args.command == "prnu":
        analysis = analyze_prnu(args.file, args.reference)
        if args.json_out:
            _write_json_out(args.json_out, json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n")
        if args.format == "json":
            print(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2))
        else:
            correlation = f"{analysis.correlation:.3f}" if analysis.correlation is not None else "-"
            print(f"Score: {analysis.score} ({analysis.band_label})")
            print(f"Verdict: {analysis.verdict}")
            print(f"NCC: {correlation}, References: {analysis.reference_images}")
            if analysis.signals:
                print("Signals:")
                for signal in analysis.signals:
                    print(f"  - [{signal.weight}] {signal.title}: {signal.detail}")
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
            cmd_parsers["api-serve"].error("--token is required when binding a non-localhost host; the API reads local files on request")
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
            _write_json_out(args.json_out, json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n")
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
            _write_json_out(args.json_out, json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n")
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
            _write_json_out(args.json_out, json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n")
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
            _write_json_out(args.json_out, json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n")
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
            from .face import _imread_unicode
            image = _imread_unicode(args.file)
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
            classifier = RuleClassifier()
            result = classifier.predict(features)
            if args.json_out:
                _write_json_out(args.json_out, json.dumps(result.to_json(), ensure_ascii=False, indent=2) + "\n")
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
        if args.allow_lan and not args.token:
            cmd_parsers["web"].error("--token is required with --allow-lan; the API reads and analyzes local files on request")
        run_server(args.host, args.port, default_folder=args.folder, allow_lan=args.allow_lan, token=args.token, models_dir=getattr(args, "models_dir", None))
        return 0
    if args.command == "doctor":
        from .doctor import format_report, run_diagnostics

        report = run_diagnostics()
        if args.json_out:
            _write_json_out(args.json_out, json.dumps(report.to_json(), ensure_ascii=False, indent=2) + "\n")
        if args.format == "json":
            print(json.dumps(report.to_json(), ensure_ascii=False, indent=2))
        else:
            print(format_report(report))
        return 0
    if args.command == "faceswap-seam":
        analysis = analyze_faceswap_seam(args.file, thresholds=_load_thresholds_arg(args))
        if args.json_out:
            _write_json_out(args.json_out, json.dumps(analysis.to_json(), ensure_ascii=False, indent=2) + "\n")
        if args.format == "json":
            print(json.dumps(analysis.to_json(), ensure_ascii=False, indent=2))
        else:
            print(f"Score: {analysis.score} ({analysis.band_label})")
            print(f"Verdict: {analysis.verdict}")
            print(f"Faces: {analysis.face_count}")
            if analysis.boundary_residual is not None:
                print(f"Boundary Seam Laplacian Residual: {analysis.boundary_residual:.2f}")
            if analysis.noise_discrepancy_ratio is not None:
                print(f"Noise Variance Ratio: {analysis.noise_discrepancy_ratio:.2f}")
            if analysis.chrominance_delta is not None:
                print(f"Chin-Neck Chroma Delta: {analysis.chrominance_delta:.1f}")
            if analysis.corneal_asymmetry is not None:
                print(f"Corneal Highlight Asymmetry: {analysis.corneal_asymmetry:.1f}px")
            if analysis.signals:
                print("Signals:")
                for sig in analysis.signals:
                    print(f"  - [{sig.weight}] {sig.title}: {sig.detail}")
        return 0
    if args.command == "evidence-statement":
        target = Path(args.target)
        items: list[ScanItem] = []
        if target.is_file() and target.suffix.lower() == ".json":
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
                from .core import _scan_item_from_json
                raw_items = data.get("items", [])
                items = [_scan_item_from_json(row) for row in raw_items if isinstance(row, dict)]
            except Exception as exc:
                print(f"error: cannot parse scan JSON: {exc}", file=sys.stderr)
                return 2
        elif target.is_dir():
            _, items = scan_directory(target, max_files=100)
        elif target.is_file():
            from .core import analyze_file
            item = analyze_file(target)
            items = [item]
        else:
            print(f"error: target does not exist: {target}", file=sys.stderr)
            return 2

        statement = build_evidence_statement(
            items,
            case_no=args.case_no,
            case_name=args.case_name,
            plaintiff=args.plaintiff,
            defendant=args.defendant,
            court=args.court,
            law_firm=args.law_firm,
            contact=args.contact,
            center=args.center,
            coverage=weights_coverage(None),
        )
        if args.md_out:
            write_evidence_statement_markdown(args.md_out, statement)
        if args.pdf_out:
            write_evidence_statement_pdf(args.pdf_out, statement)

        if args.format == "json":
            print(json.dumps(statement.to_json(), ensure_ascii=False, indent=2))
        elif args.format == "markdown":
            print(statement.to_markdown())
        else:
            print(f"=== {statement.case_name} 증거설명서 ===")
            print(f"사건번호: {statement.case_no}")
            print(f"원고(고소인): {statement.plaintiff}")
            print(f"피고(피의자): {statement.defendant}")
            print(f"증거 목록 ({len(statement.entries)}건):")
            for entry in statement.entries:
                print(f"  - [{entry.exhibit_no}] {entry.document_name} ({entry.band_label}, {entry.score}점)")
                print(f"    SHA-256: {entry.sha256}" if entry.sha256 else "    SHA-256: 해시 불가 — 원본 접근 실패")
            if args.pdf_out:
                print(f"PDF 저장 완료: {args.pdf_out}")
            if args.md_out:
                print(f"Markdown 저장 완료: {args.md_out}")
        return 0
    if args.command == "vendor-weights":
        if args.action == "pin":
            return _vendor_weights_pin(args)
        _modes =[bool(args.fetch), bool(args.verify), bool(args.bundle_to), bool(args.install), bool(args.manifest_out)]
        if sum(_modes) > 1:
            print("error: vendor-weights flags are mutually exclusive — choose one of --fetch/--verify/--bundle-to/--install/--manifest-out", file=sys.stderr)
            return 2
        if args.install:
            target = args.to or args.models_dir
            if target is None:
                print("error: --install needs a target — pass --to DIR or --models-dir DIR (or set DEEPFAKE_LENS_MODELS_DIR)", file=sys.stderr)
                return 2
            res = install_bundle(args.install, target)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0 if res["status"] == "installed" else 1
        if args.fetch:
            fetch_res = fetch_weights(args.models_dir, offline=args.offline)
            print(json.dumps(fetch_res, ensure_ascii=False, indent=2))
            return 0 if fetch_res["status"] in {"ok", "skipped"} else 1
        if args.offline and not args.verify and not args.bundle_to:
            print("error: --offline only makes sense with --fetch/--verify/--bundle-to", file=sys.stderr)
            return 2
        if args.bundle_to:
            manifest_file = bundle_offline_weights(
                args.bundle_to,
                models_dir=args.models_dir,
                copy_weights=args.copy_weights,
                force=args.force,
            )
            print(json.dumps({"bundle_dir": str(args.bundle_to), "manifest": str(manifest_file)}, ensure_ascii=False, indent=2))
            return 0
        if args.verify:
            verify_res = verify_offline_integrity(args.models_dir)
            if args.format == "json":
                print(json.dumps(verify_res, ensure_ascii=False, indent=2))
            else:
                st = verify_res["status"]
                status_str = "PASS" if st == "pass" else ("PASS (unverified weights present)" if st == "pass-unverified" else "FAIL")
                print(f"Offline Model Integrity: {status_str}")
                print(f"Profiles: {verify_res['total_profiles']}, Local checkpoints: {verify_res['local_checkpoints']}, Verified: {verify_res['verified']}, Size: {verify_res['total_size_mb']} MB")
                if verify_res["unverified"]:
                    print(f"Unverified (no declared hash): {', '.join(verify_res['unverified'])}")
                if verify_res["mismatches"]:
                    print("Mismatched Checkpoints:")
                    for m in verify_res["mismatches"]:
                        print(f"  - {m['name']}: {m['checkpoint_relpath']}")
                if verify_res["missing"]:
                    print(f"Missing Checkpoints: {', '.join(verify_res['missing'])}")
                if verify_res.get("hub_resolved"):
                    print(f"Hub-resolved (no local weight): {', '.join(verify_res['hub_resolved'])}")
                if verify_res.get("unsupported"):
                    print(f"Unsupported profiles (not counted): {', '.join(verify_res['unsupported'])}")
            return 0 if verify_res["status"] in ("pass", "pass-unverified") else 1

        manifest = inspect_model_manifest(args.models_dir)
        if args.manifest_out:
            args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
            args.manifest_out.write_text(json.dumps(manifest.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.format == "json":
            print(json.dumps(manifest.to_json(), ensure_ascii=False, indent=2))
        elif args.format == "markdown":
            print(manifest.to_markdown())
        else:
            print(f"Models Directory: {manifest.models_dir}")
            print(f"Profiles: {manifest.total_profiles}, Available: {manifest.available_weights}, Missing: {manifest.missing_weights}, Total: {manifest.total_bytes / (1024 * 1024):.1f} MB")
            for e in manifest.entries:
                chk_mark = "OK" if e.exists else "MISSING"
                print(f"  [{chk_mark:<7}] {e.name:<24} ({e.modality:<5}) {e.checkpoint_relpath}")
        return 0

    if args.max_files < 1:
        cmd_parsers["scan"].error("--max-files must be at least 1")
    if args.text_bytes < 1:
        cmd_parsers["scan"].error("--text-bytes must be at least 1")
    if args.metadata_bytes < 1:
        cmd_parsers["scan"].error("--metadata-bytes must be at least 1")
    if args.pixel_max_side < 16:
        cmd_parsers["scan"].error("--pixel-max-side must be at least 16")
    if args.workers < 1:
        cmd_parsers["scan"].error("--workers must be at least 1")
    if args.max_file_bytes is not None and args.max_file_bytes < 1:
        cmd_parsers["scan"].error("--max-file-bytes must be at least 1")
    if args.heatmaps and args.pixel != "deep":
        cmd_parsers["scan"].error("--heatmaps requires --pixel deep")
    if args.model_path and not args.model_path.exists():
        cmd_parsers["scan"].error("--model-path does not exist")
    # Default engine profiles cover both modalities: the adapter filters by
    # modality, so images run the image profiles and audio files run the
    # audio ones. An explicit --model-path replaces both defaults.
    if args.model_path:
        model_path: Path | list[Path] | None = args.model_path
    elif args.no_default_engine:
        model_path = None
    else:
        model_path = [path for path in (default_model_path(), default_text_model_path()) if path is not None] + default_audio_model_paths() or None
    if model_path and args.model_path is None:
        print(f"default engine profiles: {model_path}", file=sys.stderr)
    thresholds = _load_thresholds_arg(args)

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
            model_path=model_path,
            cache_path=args.cache,
            workers=args.workers,
            max_file_bytes=args.max_file_bytes,
            allow_symlinks=args.allow_symlinks,
            dedupe=args.dedupe,
            hash_db_path=args.hash_db,
            deep_signals=args.deep_signals,
            thresholds=thresholds,
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

    scan_coverage = weights_coverage(getattr(args, "models_dir", None))
    if args.json_out:
        scan_payload = _maybe_sign(scan_to_json(summary, items, thresholds=thresholds, models_dir=getattr(args, 'models_dir', None)), sign=args.sign, key_file=args.key_file)
        _write_json_out(args.json_out, json.dumps(scan_payload, ensure_ascii=False, indent=2) + "\n")
    if args.csv_out:
        _write_csv(args.csv_out, items, coverage=weights_coverage(getattr(args, "models_dir", None)), thresholds=thresholds)
    if args.html_out:
        write_html_report(args.html_out, summary, items, redact_paths=args.redact_paths, thresholds=thresholds)
    if args.pdf_out:
        write_pdf_report(args.pdf_out, summary, items, redact_paths=args.redact_paths, thresholds=thresholds)
    if getattr(args, "forensic_pdf_out", None):
        write_forensic_pdf_report(
            args.forensic_pdf_out,
            summary,
            items,
            redact_paths=args.redact_paths,
            exhibit_no=getattr(args, "exhibit_no", "갑 제        호증"),
            thresholds=thresholds,
            coverage=scan_coverage,
        )
    if getattr(args, "evidence_statement_out", None) or getattr(args, "evidence_statement_pdf_out", None):
        stmt = build_evidence_statement(
            items,
            case_no=getattr(args, "case_no", "(사건번호 입력)"),
            case_name=getattr(args, "case_name", "성폭력처벌법위반(허위영상물편집등) 및 정보통신망법위반"),
            plaintiff=getattr(args, "plaintiff", "(의뢰사 상호명 입력) 귀하"),
            defendant=getattr(args, "defendant", "(피고/피의자 성명 입력)"),
            court=getattr(args, "court", "○○지방법원 귀중"),
            law_firm=getattr(args, "law_firm", "법무법인(유한) 대륜"),
            contact=getattr(args, "contact", "02-780-1128"),
            center=getattr(args, "center", "디지털포렌식 감정센터"),
            thresholds=_thresholds_json(thresholds),
            coverage=scan_coverage,
        )
        if getattr(args, "evidence_statement_out", None):
            out_p = Path(args.evidence_statement_out)
            if out_p.suffix.lower() == ".pdf":
                write_evidence_statement_pdf(out_p, stmt)
            else:
                write_evidence_statement_markdown(out_p, stmt)
        if getattr(args, "evidence_statement_pdf_out", None):
            write_evidence_statement_pdf(Path(args.evidence_statement_pdf_out), stmt)

    if args.format == "json":
        print(scan_to_json_text(summary, items, thresholds=thresholds, models_dir=getattr(args, 'models_dir', None)))
    else:
        _print_table(summary, items, include_low=args.include_low, coverage=scan_coverage, thresholds=thresholds)
        if not args.recursive and summary.total == 0 and _has_subdirectories(args.folder):
            print()
            print(f"힌트: '{args.folder}'의 직접 자식에는 파일이 없고 하위 폴더가 있습니다. --recursive 를 추가해 보세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
