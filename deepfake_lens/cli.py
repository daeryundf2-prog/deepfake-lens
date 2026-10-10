from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .benchmark import run_benchmark, write_benchmark, write_benchmark_markdown
from .collection import write_collection_plan
from .core import ARCHIVE_ROLLUP_RULE, check_scan_folder, ScanItem, _thresholds_json
from .analysis_api import AnalysisOptions, analyze_path, analyze_rows, is_symlink_path, load_thresholds, primary_row, scan_folder_run, thresholds_warning_printer
from .analysis_api import scan_payload as analysis_scan_payload
from .cli_inputs import UsageError
from .cli_parser import build_parser, escape_echo
from .serialization import redact_install_paths
from .cli_render import (
    _file_text,
    _load_thresholds_arg,
    _maybe_sign,
    _parse_csv,
    _parse_split_ratios,
    _print_table,
    _write_csv,
    _write_json_out,
)
from .datasets import write_audit, write_manifest, write_robustness_plan, write_split_plan
from .evaluate import calibrate_dataset, evaluate_dataset, evaluate_robustness_dataset, train_portable_baseline, write_cases_jsonl, write_json_report
from .feedback import build_feedback_report, load_feedback, observations_from_scan_payload, observations_live
from .fusion import FusionProfile, calibrate_fusion_profile, load_fusion_profile, write_fusion_profile
from .model_registry import list_detector_candidates, write_detector_registry, write_runtime_profile
from .perf import run_performance_check, write_performance_check
from .release import write_release_checklist
from .reports import write_eval_html_report, write_forensic_pdf_report, write_html_report, write_pdf_report
from .security import write_security_check
from .signing import ReportKeyError, load_key_file, resolve_report_key
from .cli_logging import configure_cli_logging
from .error_text import LIBRARY_ERROR_FALLBACK, read_error_ko
from .training import write_neural_training_plan
from .audio import analyze_audio
from .face import analyze_faces
from .video import extract_video_frames, write_video_frame_plan
from .video_analysis import analyze_video_temporal
from .inpaint import analyze_inpainting
from .text_advanced import analyze_text_advanced
from .watermark import detect_kgw_watermark
from .c2pa import analyze_metadata_forensic
from .multimodal import analyze_av_sync, analyze_multimodal
from .realtime import create_realtime_detector
from .rppg import analyze_rppg
from .prnu import analyze_prnu
from .evidence import create_evidence_chain
from .api_server import run_server as run_api_server
from .batch import BatchProcessor
from .ai_agent import analyze_agent_content
from .threed import analyze_3d_content
from .avatar import analyze_avatar
from .rule_classifier import RuleClassifier
from .layer_diagnostic import ANALYSIS_RESULT_KIND, to_layer_diagnostic
from .cli_standalone import (
    ANALYSIS_RESULT_NOTICE,
    analysis_result_for_path,
    analyze_text_payload,
    combined_verdict,
    emit,
    emit_layer,
    format_analysis_result,
    gated_pixel_layer,
    member_rows_text,
    symlink_layer,
    tool_attribution,
)
from .result_types import VERDICT_LABELS, Verdict
from .webapp import run_server
from .faceswap_seam import analyze_faceswap_seam
from .evidence_statement import (
    build_evidence_statement,
    signed_statement_body,
    write_evidence_statement_json,
    write_evidence_statement_markdown,
    PDF_REPORT_DEPENDENCY_MESSAGE,
    PdfDependencyMissing,
    pdf_backend_available,
    write_evidence_statement_pdf,
)
from .vendor_weights import (
    default_models_dir,
    fetch_weights,
    bundle_offline_weights,
    inspect_model_manifest,
    install_bundle,
    pin_profile,
    ProfileNotFoundError,
    resolve_profile_path,
    verify_offline_integrity,
    weights_coverage,
)
from .json_text import json_dumps


COMMANDS = {"doctor", "scan", "verify-report", "corpus", "collect", "dataset", "eval", "benchmark", "fusion", "calibrate", "feedback", "train", "train-neural-plan", "models", "video", "video-analysis", "audio", "face", "faceswap-seam", "evidence-statement", "vendor-weights", "inpaint", "text-advanced", "compare", "watermark", "forensic", "classify", "multimodal", "realtime", "rppg", "prnu", "evidence", "api-serve", "batch", "explain", "agent", "3d", "avatar", "pixel-analysis", "ml-classify", "legal-report", "perf", "security", "release", "web", "-h", "--help"}

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


def _parse_realtime_scores(raw: str | None) -> list[int]:
    """``realtime --scores 10,20,30`` → integers; anything else is a usage error (Z1, exit 2).

    ``--scores abc`` used to raise ValueError inside the command (exit 1,
    "처리 오류 1건" and a log traceback).
    """
    if not raw:
        return []
    scores: list[int] = []
    for part in raw.split(","):
        text = part.strip()
        if not text:
            continue
        try:
            scores.append(int(text))
        except ValueError:
            raise UsageError(f"--scores에는 쉼표로 구분한 정수만 쓸 수 있습니다: {text!r} (예: --scores 10,20,30)") from None
    return scores


def _vendor_weights_pin(args: argparse.Namespace) -> int:
    """``vendor-weights pin <profile>``: write the profile's weight pin (G9)."""
    if not args.profile:
        print("오류: 'vendor-weights pin'에는 프로필 이름이나 경로가 필요합니다", file=sys.stderr)
        return 2
    try:
        resolve_profile_path(args.profile, args.models_dir)
    except ProfileNotFoundError as exc:
        # Z2: a profile that is neither a file nor a name in the models dir
        # is a usage error (exit 2), not a pin failure (exit 1).
        print(f"오류: {escape_echo(exc)}", file=sys.stderr)
        return 2
    try:
        result = pin_profile(args.profile, args.models_dir, revision=args.revision)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"오류: 프로필 고정 실패 — {exc}", file=sys.stderr)
        return 1
    if result["status"] == "needs-manual":
        print(result["instructions"], file=sys.stderr)
        return 1
    print(json_dumps(result, ensure_ascii=False, indent=2))
    return 0


def _multimodal_command(args: argparse.Namespace) -> int:
    """``multimodal [FILE…]`` (D1).

    With files: each one goes through analysis_api (the scan verdict) and
    the combined verdict follows the decision-rule order — any
    manipulation evidence wins, authenticity only when every file has it.
    The legacy ``--*-score`` inputs and ``--av-sync`` are unmeasured
    reference numbers and are reported as a layer diagnostic only.
    """
    files = list(getattr(args, "files", None) or [])
    av_sync_result = analyze_av_sync(args.av_sync) if args.av_sync else None
    has_scores = any(
        value is not None for value in (args.image_score, args.text_score, args.audio_score, args.video_score)
    )
    diagnostic = None
    if has_scores or av_sync_result is not None or not files:
        raw = analyze_multimodal(
            image_score=args.image_score,
            text_score=args.text_score,
            audio_score=args.audio_score,
            video_score=args.video_score,
            image_source_guess=args.image_source,
            text_source_guess=args.text_source,
            audio_source_guess=args.audio_source,
            video_source_guess=args.video_source,
            av_sync=av_sync_result,
        ).to_json()
        if av_sync_result is not None:
            raw["av_sync"] = av_sync_result.to_json()
        diagnostic = to_layer_diagnostic("multimodal_scores", raw, layer_label="멀티모달 원점수 조합")
    if not files:
        assert diagnostic is not None
        emit(diagnostic, fmt=args.format, json_out=args.json_out)
        return 0
    options = AnalysisOptions.from_cli_args(args)
    thresholds = load_thresholds(options, warn=thresholds_warning_printer(sys.stderr))
    per_file = [
        analysis_result_for_path(path, options, command="multimodal", thresholds=thresholds)[0]
        for path in files
    ]
    verdict_code, explanation = combined_verdict(per_file)
    payload: dict[str, object] = {
        "kind": ANALYSIS_RESULT_KIND,
        "command": "multimodal",
        "notice": ANALYSIS_RESULT_NOTICE,
        "verdict_code": verdict_code,
        "verdict_label": VERDICT_LABELS[Verdict(verdict_code)],
        "verdict": f"{VERDICT_LABELS[Verdict(verdict_code)]} — {explanation}",
        "items": per_file,
    }
    if diagnostic is not None:
        payload["layer_diagnostics"] = {"multimodal_scores": diagnostic}
    text = "\n\n".join(
        [f"[종합 결론] {payload['verdict']}", *(format_analysis_result(item) for item in per_file)]
    )
    emit(payload, fmt=args.format, json_out=args.json_out, text=text)
    return 0


def _explain_command(args: argparse.Namespace) -> int:
    """``explain FILE``: which decision rule produced the scan verdict (D1).

    ``--score`` alone (the former score-band explanation) can no longer be
    explained — a raw score is not a conclusion — and is reported as a
    layer diagnostic that says so.
    """
    from .decision import RULE_DESCRIPTIONS, decide_with_rule

    if args.file is None:
        raw: dict[str, object] = {
            "score": args.score,
            "reference_band": "unavailable",
            "reference_note": "점수만으로는 결론을 설명할 수 없습니다 — 결론은 근거·검사 범위·결정 규칙에서 나옵니다. `explain <파일>`을 사용하십시오.",
        }
        if args.signals:
            try:
                raw["signals"] = json.loads(args.signals)
            except json.JSONDecodeError:
                raw["limitations"] = ["--signals JSON을 해석할 수 없습니다."]
        emit(to_layer_diagnostic("explain_score", raw, layer_label="점수 설명"), fmt="json" if args.format == "json" else "table", json_out=None)
        return 0
    options = AnalysisOptions.from_cli_args(args)
    thresholds = load_thresholds(options, warn=thresholds_warning_printer(sys.stderr))
    # B1: the scan rows for the file — an archive is expanded like scan.
    payload, rows = analysis_result_for_path(args.file, options, command="explain", thresholds=thresholds)
    item = primary_row(rows)

    def explain_row(row: ScanItem) -> dict[str, object]:
        if row.result is None:
            return {"rule": "분석 결과가 없어 결정 규칙을 적용하지 못했습니다 → 판단 불가"}
        verdict, rule = decide_with_rule(row.result.evidence, row.result.coverage, row.result.grade)
        out: dict[str, object] = {"rule_number": rule, "rule": RULE_DESCRIPTIONS[rule]}
        if verdict != row.result.verdict_code:
            out["rule_note"] = "결정 규칙 재현 결과가 기록된 결론과 다릅니다 — 보정 임계값 프로필을 확인하십시오."
        return out

    if item.kind == "archive" and len(rows) > 1:
        # The container verdict is the members' roll-up, not a decide()
        # result; each member row has its own rule.
        payload["rule"] = ARCHIVE_ROLLUP_RULE
        payload["member_rules"] = [{"path": row.path, **explain_row(row)} for row in rows if row is not item]
    else:
        payload.update(explain_row(item))
    emit(payload, fmt="json" if args.format == "json" else "table", json_out=None)
    return 0


def _legal_report_command(args: argparse.Namespace) -> int:
    """``legal-report FILE``: report built from the scan result (D4)."""
    from .enhanced_forensics import build_legal_report, legal_report_text

    report = build_legal_report(
        args.file,
        AnalysisOptions.from_cli_args(args),
        analyst_id=args.analyst_id,
        key=resolve_report_key(args.key_file),
    )
    if args.json_out:
        _write_json_out(args.json_out, json_dumps(report, ensure_ascii=False, indent=2) + "\n")
    text = legal_report_text(report)
    members = member_rows_text(report.get("rows"))
    if members:
        # B1: an archive's member rows, in the scan table's wording.
        text += "\n\n=== 압축 구성 파일 ===\n" + "\n".join(members)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    if args.format == "json":
        print(json_dumps(report, ensure_ascii=False, indent=2))
    elif args.output:
        print(json_dumps({"output": str(args.output), "report_id": report.get("report_id")}, ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0


# verify-report exit codes (D14): documented in docs/deepfake-lens-cli.md.
VERIFY_EXIT_CODES = {"verified": 0, "tampered": 1, "key-mismatch": 2, "unsigned": 3}
VERIFY_EXIT_OTHER = 4


def _load_report_json(path: Path) -> dict[str, object]:
    """The report to verify as a JSON object, or a usage error (Z4: exit 4, stderr only).

    A corrupt or non-object file used to print ``읽을 수 없음: …`` on stdout.
    """
    from .cli_inputs import read_json_input

    # P9: the reason is Korean (no json/codec English, no Python type names).
    loaded = read_json_input(path, "보고서 JSON")
    assert isinstance(loaded, dict)
    return loaded


def _verify_report_command(args: argparse.Namespace) -> int:
    """``verify-report REPORT.json [--key-file F]`` (D14).

    Prints 검증됨 / 변조됨 / 키 ID 불일치 / 서명 없음 and exits 0/1/2/3.
    An unreadable report or a missing key exits 4 (사용 오류).
    """
    from .signing import REPORT_KEY_ENV, verify_report

    key = resolve_report_key(args.key_file)
    result = verify_report(_load_report_json(args.report), key)
    payload = {"report": str(args.report), **result.to_json()}
    if result.status == "no-key":
        payload["hint"] = f"검증 키가 없습니다 — --key-file 또는 {REPORT_KEY_ENV} 환경 변수를 지정하십시오."
    if args.format == "json":
        print(json_dumps(payload, ensure_ascii=False, indent=2))
    else:
        line = f"{result.reason}: {args.report}"
        if result.key_id:
            line += f" (키 ID {result.key_id})"
        print(line)
        if payload.get("hint"):
            print(payload["hint"], file=sys.stderr)
    return VERIFY_EXIT_CODES.get(result.status, VERIFY_EXIT_OTHER)


def main(argv: list[str] | None = None) -> int:
    # R13-4: a SIGTERM/SIGINT removes the native decoders' staging folder
    # first — installed here, in the main thread, because staging may first
    # happen in a worker thread (--workers, the servers' request threads).
    from .native_path import install_cleanup_handlers

    install_cleanup_handlers()
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
    # N8: an explicit --key-file must hold a key — checked before any work
    # (a scan can run for hours), never discovered as an unsigned report.
    key_file = getattr(args, "key_file", None)
    if key_file is not None:
        try:
            load_key_file(key_file)
        except ReportKeyError as exc:
            print(f"오류: {escape_echo(exc)}", file=sys.stderr)
            return 4 if args.command == "verify-report" else 2
    # N4/N7: every input path of every subcommand is checked before any
    # work — missing, a folder for a file (or the reverse), an unsupported
    # format: "오류: …" on stderr, exit 2 (verify-report: its usage code 4).
    from .cli_inputs import check_command_inputs

    try:
        check_command_inputs(args)
    except UsageError as exc:
        print(f"오류: {escape_echo(exc)}", file=sys.stderr)
        return VERIFY_EXIT_OTHER if args.command == "verify-report" else USAGE_EXIT
    # N8: tracebacks of routine per-file failures go to the log file; stderr
    # gets one Korean summary line (--verbose shows them).
    logs = configure_cli_logging(bool(getattr(args, "verbose", False)))
    try:
        return _run_command(args, parser, cmd_parsers)
    except UsageError as exc:
        print(f"오류: {escape_echo(exc)}", file=sys.stderr)
        return VERIFY_EXIT_OTHER if args.command == "verify-report" else USAGE_EXIT
    except (RecursionError, ValueError) as exc:
        # R10-5 (round 10): an input the command could not take — a JSON
        # nested past the recursion limit, a value it cannot use — is an
        # input error with the documented code (verify-report 4, else 2),
        # never 1 (verify-report's 1 means "변조됨").
        logs.command_errors += 1
        logging.getLogger(__name__).exception("command %s: input error", args.command)
        print(f"오류: {INPUT_ERROR_MESSAGE.format(reason=escape_echo(input_error_reason(exc)), log=logs.log_hint())}", file=sys.stderr)
        return VERIFY_EXIT_OTHER if args.command == "verify-report" else USAGE_EXIT
    except Exception as exc:  # noqa: BLE001 - N4: never an English traceback on the console
        logs.command_errors += 1
        logging.getLogger(__name__).exception("command %s failed", args.command)
        print(f"오류: 처리 중 예기치 않은 오류가 발생했습니다({type(exc).__name__}) — 상세는 {logs.log_hint()}", file=sys.stderr)
        return UNEXPECTED_ERROR_EXIT
    finally:
        logs.finish()


# Exit codes (docs/deepfake-lens-cli.md "종료 코드"): 2 = usage error (bad
# input path, bad option value), 1 = the command ran and failed.
USAGE_EXIT = 2
UNEXPECTED_ERROR_EXIT = 1
# R10-5: RecursionError/ValueError escaping a command (an unusable input).
INPUT_ERROR_MESSAGE = "입력을 처리할 수 없습니다({reason}) — 상세는 {log}"
# R11-8 (round 11): an untranslated library error read "입력을 처리할 수
# 없습니다(라이브러리 오류(ValueError) — 상세는 로그 참조) — 상세는 로그 파일 …" —
# the log was pointed to twice. The reason drops its own "— 상세는 로그 참조";
# the message names the log file once.
LOG_REFERENCE_TAIL = LIBRARY_ERROR_FALLBACK[LIBRARY_ERROR_FALLBACK.index("}") + 2:]


def input_error_reason(exc: BaseException) -> str:
    """The Korean reason of an input error without its own pointer to the log (R11-8)."""
    return read_error_ko(exc).removesuffix(LOG_REFERENCE_TAIL)


def _run_command(args: argparse.Namespace, parser: argparse.ArgumentParser, cmd_parsers: dict[str, argparse.ArgumentParser]) -> int:
    """Dispatch one parsed command (body of :func:`main`).

    G5/N4: the input paths were checked by ``cli_inputs.check_command_inputs``
    in :func:`main` before this runs.
    """
    if args.command == "corpus":
        from .corpus_manifest import run_corpus_cli

        return run_corpus_cli(args)
    if args.command == "verify-report":
        return _verify_report_command(args)
    if args.command == "collect":
        payload = write_collection_plan(args.folder, args.out, minimum_per_source=args.minimum_per_source)
        print(json_dumps({"out": str(args.out), "targets": len(payload["targets"])}, ensure_ascii=False, indent=2))
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
        print(json_dumps(summary.to_json(), ensure_ascii=False, indent=2))
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
        print(json_dumps(payload["metrics"], ensure_ascii=False, indent=2))
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
        print(json_dumps({"out": str(args.json_out), "rows": len(payload["rows"]), "best": payload["best"]}, ensure_ascii=False, indent=2))
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
        print(json_dumps({"out": str(args.out), "threshold": payload["profile"]["threshold"], "metrics": payload["metrics"]}, ensure_ascii=False, indent=2))
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
        print(json_dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == "feedback":
        from .cli_inputs import UsageError as FeedbackUsageError  # (UsageError is local in this function)
        from .feedback import FEEDBACK_NO_LABELS, FeedbackFileError, parse_feedback_rows

        try:
            entries = load_feedback(args.labels)
        except FeedbackFileError as exc:
            raise FeedbackUsageError(str(exc)) from exc
        if not entries:
            # R9-5: no usable label is a usage error, never a silent empty report.
            rows = parse_feedback_rows(Path(args.labels).read_text(encoding="utf-8"), args.labels)
            raise FeedbackUsageError(FEEDBACK_NO_LABELS.format(path=args.labels, rows=len(rows)))
        if args.scan_json:
            from .cli_inputs import read_json_input

            scan_payload = read_json_input(args.scan_json, "검사 JSON")  # P4: UsageError -> exit 2
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
        print(json_dumps(report, ensure_ascii=False, indent=2))
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
        print(json_dumps({"out": str(args.out), "threshold": payload["threshold"], "metrics": payload.get("metrics", {})}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "models":
        payload = list_detector_candidates(focus=args.focus)
        if args.json_out:
            write_detector_registry(args.json_out, focus=args.focus)
        if args.profile_out:
            if not args.checkpoint:
                cmd_parsers["models"].error("--profile-out에는 --checkpoint가 필요합니다")
            profile = write_runtime_profile(
                args.profile_out,
                args.candidate,
                args.checkpoint,
                runtime=args.runtime,
                input_size=args.input_size,
                score_index=args.score_index,
            )
            payload["runtime_profile"] = profile
        print(json_dumps(payload, ensure_ascii=False, indent=2))
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
        print(json_dumps({"out": str(args.out), "checkpoint": payload["artifacts"]["checkpoint"]}, ensure_ascii=False, indent=2))
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
            args.out.write_text(json_dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json_dumps({"count": payload["count"], "ffmpeg_available": payload["ffmpeg_available"], "out": str(args.out)}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "audio":
        # D1: layer diagnostic only — the verdict for an audio file is `scan`.
        audio_model_path = args.model_path or (None if args.no_default_engine else default_audio_model_paths() or None)
        analysis = analyze_audio(args.file, segment_seconds=args.segment_seconds, model_path=audio_model_path)
        return emit_layer(args, "audio", "오디오 음향 계층", analysis.to_json(), subject=str(args.file))
    if args.command == "face":
        analysis = analyze_faces(args.file)
        return emit_layer(args, "face", "얼굴 조작 계층", analysis.to_json(), subject=str(args.file))
    if args.command == "video-analysis":
        analysis = analyze_video_temporal(
            args.file,
            frame_sample_rate=args.frame_rate,
            max_frames=args.max_frames,
            model_path=list(args.model_path) if args.model_path else None,
        )
        return emit_layer(args, "video_temporal", "영상 시간축 계층", analysis.to_json(), subject=str(args.file))
    if args.command == "inpaint":
        analysis = analyze_inpainting(args.file)
        return emit_layer(args, "inpaint", "인페인팅 계층", analysis.to_json(), subject=str(args.file))
    if args.command == "text-advanced":
        text = args.file.read_text(encoding="utf-8", errors="replace")
        analysis = analyze_text_advanced(text)
        return emit_layer(args, "text_statistics", "텍스트 문체 통계 계층", analysis.to_json(), subject=str(args.file))
    if args.command == "compare":
        from .cli_inputs import AUDIO_SUFFIXES, UsageError, has_supported_suffix
        from .core import compare_files

        audio_pair = [has_supported_suffix(path, AUDIO_SUFFIXES) for path in (args.file_a, args.file_b)]
        if audio_pair[0] != audio_pair[1]:
            # N4: a mixed pair is a usage error (was an exit-1 message on stdout).
            raise UsageError(f"두 파일의 종류가 같아야 합니다(오디오 쌍 또는 텍스트·문서 쌍): {args.file_a}, {args.file_b}")
        result = compare_files(args.file_a, args.file_b, ecapa_revision=args.ecapa_revision)
        if "error" in result:
            if args.format == "json":
                emit({"error": result["error"]}, fmt="json", json_out=None)
            else:
                print(f"오류: {result['error']}", file=sys.stderr)
            return 1
        # D1: same-speaker / same-author similarity is an unmeasured
        # reference number; its former same/unclear/different band is dropped.
        return emit_layer(args, "compare", "두 파일 유사도 계층", result, subject=f"{args.file_a} ↔ {args.file_b}")
    if args.command == "watermark":
        if not args.synthid_keys and not args.secret:
            # N4: a usage error before the file is read (was JSON on stdout, exit 1).
            print("오류: --secret(KGW) 또는 --synthid-keys(SynthID) 중 하나가 필요합니다.", file=sys.stderr)
            return USAGE_EXIT
        text = _file_text(args.file)
        if text is None:
            print(f"오류: 텍스트를 추출할 수 없습니다: {escape_echo(args.file)}", file=sys.stderr)
            return 1
        if args.synthid_keys:
            from .watermark import detect_synthid_watermark

            keys = [int(part.strip()) for part in args.synthid_keys.split(",") if part.strip()]
            result = detect_synthid_watermark(text, keys=keys, tokenizer_model=args.tokenizer, tokenizer_revision=args.tokenizer_revision)
        else:
            result = detect_kgw_watermark(text, secret=args.secret, tokenizer_model=args.tokenizer, tokenizer_revision=args.tokenizer_revision, gamma=args.gamma)
        if args.format == "json":
            print(json_dumps(result.to_json(), ensure_ascii=False, indent=2))
        else:
            print(f"참고 원점수: {result.score} — {result.reference_note}")
            print(f"z={result.z_score}, green={result.green_fraction}, tokens={result.token_count}")
        return 0
    if args.command == "forensic":
        # D1: "is it fake" answers come from analysis_api — same as scan.
        # B1: an archive is expanded like scan (container + member rows).
        options = AnalysisOptions.from_cli_args(args)
        thresholds = load_thresholds(options, warn=thresholds_warning_printer(sys.stderr))
        payload, _ = analysis_result_for_path(args.file, options, command="forensic", thresholds=thresholds)
        payload["layer_diagnostics"] = {
            "provenance_metadata": (
                symlink_layer("provenance_metadata", "출처 메타데이터 계층")
                if is_symlink_path(args.file)  # G6: never read through a link
                else to_layer_diagnostic("provenance_metadata", analyze_metadata_forensic(args.file).to_json(), layer_label="출처 메타데이터 계층")
            ),
        }
        emit(payload, fmt=args.format, json_out=args.json_out)
        return 0
    if args.command == "classify":
        # D1: the conclusion is the scan verdict; tool attribution is a
        # reference list of marker matches, never a verdict.
        options = AnalysisOptions.from_cli_args(args)
        thresholds = load_thresholds(options, warn=thresholds_warning_printer(sys.stderr))
        payload, _ = analysis_result_for_path(args.file, options, command="classify", thresholds=thresholds)
        payload["tool_candidates"] = (
            symlink_layer("tool_attribution", "생성 도구 표지 대조")
            if is_symlink_path(args.file)  # G6
            else to_layer_diagnostic("tool_attribution", tool_attribution(args.file).to_json(), layer_label="생성 도구 표지 대조")
        )
        emit(payload, fmt=args.format, json_out=args.json_out)
        return 0
    if args.command == "multimodal":
        return _multimodal_command(args)
    if args.command == "realtime":
        detector = create_realtime_detector(
            window_size=args.window_size,
            alert_threshold=args.alert_threshold,
        )
        scores = _parse_realtime_scores(args.scores)
        state = detector.idle_state()
        for score in scores:
            state = detector.process_frame(score)
        raw = {**state.to_json(), "summary": detector.get_summary()}
        if not args.scores:
            raw["limitations"] = ["--scores가 없어 처리한 프레임 점수가 없습니다."]
        return emit_layer(args, "realtime", "실시간 프레임 점수 계층", raw)
    if args.command == "rppg":
        analysis = analyze_rppg(args.file, max_frames=args.max_frames)
        return emit_layer(args, "rppg", "rPPG 맥박 계층", analysis.to_json(), subject=str(args.file))
    if args.command == "prnu":
        analysis = analyze_prnu(args.file, args.reference)
        return emit_layer(args, "prnu", "PRNU 센서 지문 계층", analysis.to_json(), subject=str(args.file))
    if args.command == "evidence":
        chain = create_evidence_chain(
            args.file,
            results={"analyst_id": args.analyst_id},
            analyst_id=args.analyst_id,
        )
        if args.output:
            from .evidence import save_evidence_chains
            save_evidence_chains([chain], args.output)
            print(json_dumps({"output": str(args.output), "hash": chain.file_hash}, ensure_ascii=False, indent=2))
        else:
            print(json_dumps(chain.to_json(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "api-serve":
        from .api_server import LOCAL_HOSTS
        if args.host not in LOCAL_HOSTS and not args.token:
            cmd_parsers["api-serve"].error("localhost가 아닌 주소에 바인딩하려면 --token이 필요합니다 — API는 요청에 따라 로컬 파일을 읽습니다")
        run_api_server(host=args.host, port=args.port, token=args.token, allow_roots=args.allow_root)
        return 0
    if args.command == "batch":
        processor = BatchProcessor(max_workers=args.workers)
        files = sorted((path for path in args.folder.glob("*") if path.is_file()), key=lambda p: str(p))
        batch_options = AnalysisOptions.from_cli_args(args)
        batch_thresholds = load_thresholds(batch_options, warn=thresholds_warning_printer(sys.stderr))
        job = processor.process_batch(files, lambda f: analyze_path(f, batch_options, thresholds=batch_thresholds).to_json())
        if args.output:
            from .batch import save_batch_results
            save_batch_results(job, args.output)
            print(json_dumps({"output": str(args.output), "job_id": job.job_id, "processed": job.processed_files}, ensure_ascii=False, indent=2))
        else:
            summary = processor.get_summary(job.job_id)
            print(json_dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.command == "explain":
        return _explain_command(args)
    if args.command == "agent":
        # D1: text is reference grade — the verdict comes from the same text
        # path as scan (always 판단 불가/참고); the agent markers are a layer
        # diagnostic beside it.
        if args.file is None and not (args.text or "").strip():
            print("오류: --text 또는 --file 중 하나가 필요합니다.", file=sys.stderr)
            return 2
        options = AnalysisOptions.from_cli_args(args)
        thresholds = load_thresholds(options, warn=thresholds_warning_printer(sys.stderr))
        if args.file is not None:
            # G6: a symbolic link is a skipped row; its target's text is not read.
            text = "" if is_symlink_path(args.file) else args.file.read_text(encoding="utf-8", errors="replace")
            payload, _ = analysis_result_for_path(args.file, options, command="agent", thresholds=thresholds)
        else:
            text = args.text
            payload = analyze_text_payload(text, options, command="agent", thresholds=thresholds)
        payload["layer_diagnostics"] = {
            "agent_markers": to_layer_diagnostic("agent_markers", analyze_agent_content(text=text).to_json(), layer_label="AI 에이전트 문체 계층"),
        }
        emit(payload, fmt=args.format, json_out=args.json_out)
        return 0
    if args.command == "3d":
        text = args.text
        if args.file:
            text = args.file.read_text(encoding="utf-8", errors="replace")
        analysis = analyze_3d_content(text=text)
        return emit_layer(args, "threed", "3D 생성 마커 계층", analysis.to_json(), subject=str(args.file) if args.file else None)
    if args.command == "avatar":
        analysis = analyze_avatar(file_path=str(args.file) if args.file else None)
        return emit_layer(args, "avatar", "아바타 마커 계층", analysis.to_json(), subject=str(args.file) if args.file else None)
    if args.command == "pixel-analysis":
        # D1/D3: gated by the photo/non-photo classifier like scan; raw
        # pre-screen numbers only.
        return emit_layer(args, "pixel_prescreen", "픽셀 사전 선별 계층", gated_pixel_layer(args.file), subject=str(args.file))
    if args.command == "ml-classify":
        # Feature-threshold rules (rule_classifier) are unmeasured: layer
        # diagnostic only (D1).
        try:
            import cv2
            import numpy as np
            from .face import _imread_unicode
        except ImportError:
            # Y6: Korean stderr, exit 2 (a missing optional dependency, R6) — never JSON on stdout.
            print("오류: ml-classify에는 opencv와 numpy가 필요합니다(설치: pip install opencv-python-headless numpy)", file=sys.stderr)
            return USAGE_EXIT
        from .native_stderr import native_stderr_to_log

        # Y5: OpenCV's own messages (grfmt_png …) go to the log, not the console.
        with native_stderr_to_log():
            image = _imread_unicode(args.file)
            if image is None:
                # Y6: a corrupt or undecodable image is a Korean stderr error, exit 2.
                print(f"오류: 이미지를 읽을 수 없습니다(손상되었거나 디코드할 수 없는 이미지): {escape_echo(args.file)}", file=sys.stderr)
                return USAGE_EXIT
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            features = {
                "mean": float(np.mean(gray)),
                "std": float(np.std(gray)),
                "texture_variance": float(np.var(cv2.Laplacian(gray, cv2.CV_64F))),
            }
        rule_result = RuleClassifier().predict(features).to_json()
        # The rule's "ai"/"natural" label and probability_ai are not kept:
        # an unmeasured weight sum is neither a label nor a probability.
        raw = {
            "features": features,
            "rule_weight_sum": rule_result.get("probability_ai"),
            "rules_matched": rule_result.get("features_used", []),
            "limitations": ["특징 임계값 규칙은 측정되지 않았습니다 — rule_weight_sum은 확률이나 결론이 아닙니다."],
        }
        return emit_layer(args, "rule_features", "특징 임계값 규칙 계층", raw, subject=str(args.file))
    if args.command == "legal-report":
        return _legal_report_command(args)
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
        print(json_dumps({"out": str(args.out), "files_per_second": payload["files_per_second"], "summary": payload["summary"]}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "release":
        payload = write_release_checklist(Path.cwd(), args.out)
        print(json_dumps({"out": str(args.out), "entrypoint_present": payload["entrypoint_present"]}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "security":
        payload = write_security_check(Path.cwd(), args.out)
        print(json_dumps({"out": str(args.out), "passed": payload["passed"]}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "web":
        if args.allow_lan and not args.token:
            cmd_parsers["web"].error("--allow-lan에는 --token이 필요합니다 — API는 요청에 따라 로컬 파일을 읽고 분석합니다")
        run_server(args.host, args.port, default_folder=args.folder, allow_lan=args.allow_lan, token=args.token, models_dir=getattr(args, "models_dir", None), allow_roots=args.allow_root)
        return 0
    if args.command == "doctor":
        from .doctor import format_report, run_diagnostics

        report = run_diagnostics(getattr(args, "models_dir", None))
        if args.json_out:
            _write_json_out(args.json_out, json_dumps(report.to_json(), ensure_ascii=False, indent=2) + "\n")
        if args.format == "json":
            print(json_dumps(report.to_json(), ensure_ascii=False, indent=2))
        else:
            print(format_report(report))
        return 0
    if args.command == "faceswap-seam":
        analysis = analyze_faceswap_seam(args.file, thresholds=_load_thresholds_arg(args))
        return emit_layer(args, "faceswap_seam", "페이스스왑 경계면 계층", analysis.to_json(), subject=str(args.file))
    if args.command == "evidence-statement":
        target = Path(args.target)
        if args.max_files < 1:
            print("오류: --max-files는 1 이상이어야 합니다", file=sys.stderr)
            return USAGE_EXIT
        if args.pdf_out and not pdf_backend_available():
            # R6: missing optional renderer -> Korean message, exit 2, before any analysis.
            print(f"오류: {PdfDependencyMissing()}", file=sys.stderr)
            return 2
        items: list[ScanItem] = []
        # S6: the threshold provenance (with the in-sample caveat) goes into
        # the statement for every input kind.
        stmt_thresholds: object | None = None
        # X1: the scan summary behind the statement (cap, flat-scan
        # subfolders) for its "기록되지 않은 파일" section.
        stmt_summary: object | None = None
        # X1: folder and file inputs are analyzed with the same options as
        # `scan` (--max-files with scan's default, --recursive, --allow-symlinks…).
        stmt_options = AnalysisOptions.from_cli_args(args)
        if target.is_file() and target.suffix.lower() == ".json":
            from .cli_inputs import read_json_input
            from .core import _scan_item_from_json

            # P4/P9: unreadable, non-UTF-8, invalid or non-object JSON is a
            # Korean usage error (UsageError -> exit 2), no English detail.
            data = read_json_input(target, "검사 JSON")
            assert isinstance(data, dict)
            raw_items = data.get("items")
            if not isinstance(raw_items, list) or not any(isinstance(row, dict) for row in raw_items):
                # Y1: a JSON without scan rows is not a scan result — no
                # empty statement with exit 0.
                print(f"오류: 검사 JSON에 items가 없습니다(검사 결과 행 0건): {escape_echo(target)}", file=sys.stderr)
                return USAGE_EXIT
            items = []
            for index, row in enumerate(raw_items, start=1):
                if not isinstance(row, dict):
                    continue
                try:
                    items.append(_scan_item_from_json(row))
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    print(f"오류: 검사 JSON을 해석할 수 없습니다: {escape_echo(target)} — {index}번째 행의 형식이 맞지 않습니다({type(exc).__name__})", file=sys.stderr)
                    return USAGE_EXIT
            stmt_thresholds = data.get("thresholds")
            stmt_summary = data.get("summary") if isinstance(data.get("summary"), dict) else None
        elif target.is_dir():
            try:
                run = scan_folder_run(target, stmt_options, warn=thresholds_warning_printer(sys.stderr))
                items, stmt_thresholds, stmt_summary = run.items, run.thresholds, run.summary
            except OSError as exc:
                # S4: "오류: 폴더를 읽을 수 없습니다: … (권한이 없습니다)", exit 2.
                print(f"오류: {escape_echo(exc)}", file=sys.stderr)
                return 2
        elif target.is_file() or is_symlink_path(target):
            # B1: the folder scan's rows for the file — an archive yields
            # its member rows and the container row. S6: thresholds go into
            # the statement. G6: a symbolic link (even a dangling one) is
            # the scan's skipped row, never followed.
            stmt_thresholds = load_thresholds(stmt_options, warn=thresholds_warning_printer(sys.stderr))
            items = analyze_rows(target, stmt_options, thresholds=stmt_thresholds)
        else:
            # Unreachable after cli_inputs (N4) unless the target vanished meanwhile.
            from .cli_inputs import MISSING

            print(f"오류: {escape_echo(MISSING['either'].format(path=target))}", file=sys.stderr)
            return USAGE_EXIT

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
            thresholds=stmt_thresholds if isinstance(stmt_thresholds, dict) or stmt_thresholds is None else _thresholds_json(stmt_thresholds),
            # D5: a row without sha256 is hashed against the scanned folder,
            # never the cwd (a single file / JSON input has no scan root).
            scan_root=target if target.is_dir() else None,
            summary=stmt_summary,
        )
        # G30: one signed body backs every output (JSON, Markdown, PDF).
        signed_statement = signed_statement_body(statement, resolve_report_key(args.key_file))
        if args.md_out:
            write_evidence_statement_markdown(args.md_out, statement, signed=signed_statement)
        if args.pdf_out:
            write_evidence_statement_pdf(args.pdf_out, statement, signed=signed_statement)
        if args.json_out:
            write_evidence_statement_json(args.json_out, statement, signed=signed_statement)

        if args.format == "json":
            print(json_dumps(signed_statement, ensure_ascii=False, indent=2))
        elif args.format == "markdown":
            print(statement.to_markdown())
        else:
            # R10-1: every echoed value through escape_echo (names in
            # document_name are already display_name'd).
            print(f"=== {escape_echo(statement.case_name)} 증거설명서 ===")
            print(f"사건번호: {escape_echo(statement.case_no)}")
            print(f"원고(고소인): {escape_echo(statement.plaintiff)}")
            print(f"피고(피의자): {escape_echo(statement.defendant)}")
            print(f"증거 목록 ({len(statement.entries)}건):")
            unrecorded_lines = statement.unrecorded_lines()
            for entry in statement.entries:
                # D1: the verdict label only — an uncalibrated score (always 0) is not printed.
                print(f"  - [{entry.exhibit_no}] {escape_echo(entry.document_name)} (결론: {entry.verdict_label})")
                print(f"    SHA-256: {entry.sha256}" if entry.sha256 else "    SHA-256: 해시 불가 — 원본 접근 실패")
            # X1: the "기록되지 않은 파일" section.
            print(f"[{escape_echo(unrecorded_lines[0])}]")
            for line in unrecorded_lines[1:]:
                print(f"  - {escape_echo(line)}")
            if args.pdf_out:
                print(f"PDF 저장 완료: {escape_echo(args.pdf_out)}")
            if args.md_out:
                print(f"Markdown 저장 완료: {escape_echo(args.md_out)}")
            if args.json_out:
                print(f"서명 JSON 저장 완료: {escape_echo(args.json_out)}")
            print("서명: " + ("HMAC-SHA256 " + str(signed_statement.get("signature_key_id")) if signed_statement.get("signature") else "서명 없음 (키 미설정)"))
        return 0
    if args.command == "vendor-weights":
        if args.action == "pin":
            return _vendor_weights_pin(args)
        _modes =[bool(args.fetch), bool(args.verify), bool(args.bundle_to), bool(args.install), bool(args.manifest_out)]
        if sum(_modes) > 1:
            print("오류: vendor-weights 옵션은 함께 쓸 수 없습니다 — --fetch/--verify/--bundle-to/--install/--manifest-out 중 하나만 지정하십시오", file=sys.stderr)
            return 2
        if args.install:
            target = args.to or args.models_dir
            if target is None:
                print("오류: --install에는 대상이 필요합니다 — --to DIR 또는 --models-dir DIR을 지정하십시오 (또는 DEEPFAKE_LENS_MODELS_DIR)", file=sys.stderr)
                return 2
            res = install_bundle(args.install, target)
            print(json_dumps(res, ensure_ascii=False, indent=2))
            return 0 if res["status"] == "installed" else 1
        if args.fetch:
            fetch_res = fetch_weights(args.models_dir, offline=args.offline)
            print(json_dumps(fetch_res, ensure_ascii=False, indent=2))
            return 0 if fetch_res["status"] in {"ok", "skipped"} else 1
        if args.offline and not args.verify and not args.bundle_to:
            print("오류: --offline은 --fetch/--verify/--bundle-to와 함께만 쓸 수 있습니다", file=sys.stderr)
            return 2
        if args.bundle_to:
            manifest_file = bundle_offline_weights(
                args.bundle_to,
                models_dir=args.models_dir,
                copy_weights=args.copy_weights,
                force=args.force,
            )
            print(json_dumps({"bundle_dir": str(args.bundle_to), "manifest": str(manifest_file)}, ensure_ascii=False, indent=2))
            return 0
        if args.verify:
            verify_res = verify_offline_integrity(args.models_dir)
            if args.format == "json":
                print(json_dumps(verify_res, ensure_ascii=False, indent=2))
            else:
                st = verify_res["status"]
                status_str = "통과" if st == "pass" else ("통과(해시 미선언 가중치 있음)" if st == "pass-unverified" else "실패")
                print(f"오프라인 모델 무결성: {status_str}")
                print(f"프로필 {verify_res['total_profiles']}개, 로컬 체크포인트 {verify_res['local_checkpoints']}개, 검증됨 {verify_res['verified']}개, 크기 {verify_res['total_size_mb']} MB")
                if verify_res["unverified"]:
                    print(f"미검증(선언된 해시 없음): {', '.join(verify_res['unverified'])}")
                if verify_res["mismatches"]:
                    print("해시 불일치 체크포인트:")
                    for m in verify_res["mismatches"]:
                        print(f"  - {m['name']}: {m['checkpoint_relpath']}")
                if verify_res["missing"]:
                    print(f"없는 체크포인트: {', '.join(verify_res['missing'])}")
                if verify_res.get("hub_resolved"):
                    print(f"허브 모델(로컬 가중치 없음): {', '.join(verify_res['hub_resolved'])}")
                if verify_res.get("unsupported"):
                    print(f"비활성 프로필(집계 제외): {', '.join(verify_res['unsupported'])}")
            return 0 if verify_res["status"] in ("pass", "pass-unverified") else 1

        manifest = inspect_model_manifest(args.models_dir)
        if args.manifest_out:
            args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
            args.manifest_out.write_text(json_dumps(manifest.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.format == "json":
            print(json_dumps(manifest.to_json(), ensure_ascii=False, indent=2))
        elif args.format == "markdown":
            print(manifest.to_markdown())
        else:
            print(manifest.to_table())  # G3: Korean labels, counts from the rows
        return 0

    if args.max_files < 1:
        cmd_parsers["scan"].error("--max-files는 1 이상이어야 합니다")
    if args.text_bytes < 1:
        cmd_parsers["scan"].error("--text-bytes는 1 이상이어야 합니다")
    if args.metadata_bytes < 1:
        cmd_parsers["scan"].error("--metadata-bytes는 1 이상이어야 합니다")
    if args.pixel_max_side < 16:
        cmd_parsers["scan"].error("--pixel-max-side는 16 이상이어야 합니다")
    if args.workers < 1:
        cmd_parsers["scan"].error("--workers는 1 이상이어야 합니다")
    if args.max_file_bytes is not None and args.max_file_bytes < 1:
        cmd_parsers["scan"].error("--max-file-bytes는 1 이상이어야 합니다")
    if args.heatmaps and args.pixel != "deep":
        cmd_parsers["scan"].error("--heatmaps에는 --pixel deep이 필요합니다")
    if args.model_path and not args.model_path.exists():
        cmd_parsers["scan"].error("--model-path가 존재하지 않습니다")
    try:
        # S4: say why the folder cannot be scanned before any other output.
        check_scan_folder(Path(args.folder))
    except OSError as exc:
        print(f"오류: {escape_echo(exc)}", file=sys.stderr)
        return 2
    # G7: CLI, GUI and API all go through analysis_api. The default engine
    # set is every runtime profile in the models dir (--models-dir or the
    # packaged/$DEEPFAKE_LENS_MODELS_DIR one) — the adapter filters by
    # modality, and `supported`/`pin` decide which members actually run.
    # An explicit --model-path replaces the defaults.
    wants_statement_pdf = bool(getattr(args, "evidence_statement_pdf_out", None)) or (
        getattr(args, "evidence_statement_out", None) is not None
        and Path(args.evidence_statement_out).suffix.lower() == ".pdf"
    )
    if wants_statement_pdf and not pdf_backend_available():
        # R6: say so before a possibly hours-long scan, in Korean, exit 2 —
        # never a RuntimeError traceback after the scan finished.
        print(f"오류: {PdfDependencyMissing()}", file=sys.stderr)
        return 2
    if (args.pdf_out or getattr(args, "forensic_pdf_out", None)) and not pdf_backend_available():
        # B8: the scan PDF reports need pymupdf too — same refusal, never an
        # English Latin-1 PDF.
        print(f"오류: {PdfDependencyMissing(PDF_REPORT_DEPENDENCY_MESSAGE)}", file=sys.stderr)
        return 2
    options = AnalysisOptions.from_cli_args(args)
    engine_profiles = options.engine_profiles()
    if engine_profiles and args.model_path is None:
        names = [p.name for p in engine_profiles] if isinstance(engine_profiles, list) else [str(engine_profiles)]
        print(f"기본 엔진 프로필({options.resolved_models_dir()}): {names}", file=sys.stderr)

    try:
        if args.progress:
            print(f"검사 중: {escape_echo(args.folder)} (workers={args.workers}, pixel={args.pixel})…", file=sys.stderr)
        run = scan_folder_run(args.folder, options, warn=thresholds_warning_printer(sys.stderr))
        summary, items, thresholds = run.summary, run.items, run.thresholds
    except OSError as exc:
        # S4: core.ScanFolderError carries the reason — "폴더를 찾을 수 없습니다",
        # "폴더가 아니라 파일입니다 … (단일 파일은 forensic/classify를 사용)",
        # "폴더를 읽을 수 없습니다: … (사유)" — exit 2 (docs/deepfake-lens-cli.md).
        print(f"오류: {escape_echo(exc)}", file=sys.stderr)
        return 2
    if args.progress:
        print(f"검사 완료: 분석 {summary.analyzed}건, 캐시 사용 {summary.cached}건, 전체 {summary.total}건", file=sys.stderr)

    scan_coverage = weights_coverage(options.resolved_models_dir())
    if args.json_out:
        scan_payload = _maybe_sign(analysis_scan_payload(summary, items, thresholds, options), sign=args.sign, key_file=args.key_file)
        _write_json_out(args.json_out, json_dumps(scan_payload, ensure_ascii=False, indent=2) + "\n")
    if args.csv_out:
        _write_csv(args.csv_out, items, coverage=scan_coverage, thresholds=thresholds, summary=summary)
    # S3: --redact-paths also hides the tool's install path (model profile
    # paths and any other field naming it) in the HTML/PDF reports and the
    # signed body they embed; JSON/CSV/table output keeps full paths.
    report_items = redact_install_paths(items) if args.redact_paths else items
    if args.html_out:
        write_html_report(args.html_out, summary, report_items, redact_paths=args.redact_paths, thresholds=thresholds)
    if args.pdf_out:
        write_pdf_report(
            args.pdf_out, summary, report_items,
            redact_paths=args.redact_paths,
            thresholds=thresholds,
            coverage=scan_coverage,
            exhibit_no=getattr(args, "exhibit_no", "갑 제        호증"),
            law_firm=getattr(args, "law_firm", None),  # N17
            contact=getattr(args, "contact", None),
        )
    if getattr(args, "forensic_pdf_out", None):
        write_forensic_pdf_report(
            args.forensic_pdf_out,
            summary,
            report_items,
            redact_paths=args.redact_paths,
            exhibit_no=getattr(args, "exhibit_no", "갑 제        호증"),
            thresholds=thresholds,
            coverage=scan_coverage,
            law_firm=getattr(args, "law_firm", None),  # N17
            contact=getattr(args, "contact", None),
        )
    if getattr(args, "evidence_statement_out", None) or getattr(args, "evidence_statement_pdf_out", None):
        stmt = build_evidence_statement(
            items,
            case_no=getattr(args, "case_no", "(사건번호 입력)"),
            case_name=getattr(args, "case_name", "성폭력처벌법위반(허위영상물편집등) 및 정보통신망법위반"),
            plaintiff=getattr(args, "plaintiff", "(의뢰사 상호명 입력) 귀하"),
            defendant=getattr(args, "defendant", "(피고/피의자 성명 입력)"),
            court=getattr(args, "court", "○○지방법원 귀중"),
            law_firm=getattr(args, "law_firm", None),  # N17: flag, else config, else blank
            contact=getattr(args, "contact", None),
            center=getattr(args, "center", "디지털포렌식 감정센터"),
            thresholds=_thresholds_json(thresholds),
            coverage=scan_coverage,
            scan_root=args.folder,  # D5: resolve sha256-less rows here, not in the cwd
            summary=summary,  # X1: cap / flat-scan counts for "기록되지 않은 파일"
        )
        signed_stmt = signed_statement_body(stmt, resolve_report_key(getattr(args, "key_file", None)))
        if getattr(args, "evidence_statement_out", None):
            out_p = Path(args.evidence_statement_out)
            if out_p.suffix.lower() == ".pdf":
                write_evidence_statement_pdf(out_p, stmt, signed=signed_stmt)
            elif out_p.suffix.lower() == ".json":
                write_evidence_statement_json(out_p, stmt, signed=signed_stmt)
            else:
                write_evidence_statement_markdown(out_p, stmt, signed=signed_stmt)
        if getattr(args, "evidence_statement_pdf_out", None):
            write_evidence_statement_pdf(Path(args.evidence_statement_pdf_out), stmt, signed=signed_stmt)

    if args.format == "json":
        print(json_dumps(analysis_scan_payload(summary, items, thresholds, options), ensure_ascii=False, indent=2))
    else:
        _print_table(summary, items, include_low=args.include_low, coverage=scan_coverage, thresholds=thresholds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
