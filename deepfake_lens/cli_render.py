"""CLI output and argument helpers — table/CSV rendering, JSON artifact
writes, report signing, small value parsers.

Extracted from ``cli.py`` so the command dispatcher file stays focused on
argparse wiring and handler routing; names are imported back there.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from .calibration import MIN_CALIBRATION_SAMPLES, load_threshold_profile
from .result_types import RiskBand, ScanItem
from .signing import resolve_report_key, sign_report


def _file_text(path: Path) -> str | None:
    """Extract text for compare/watermark: plain text or document pipeline."""
    from .core import SUPPORTED_TEXT_EXTENSIONS, _read_prefix
    from .documents import SUPPORTED_DOCUMENT_EXTENSIONS, extract_document_text

    extension = path.suffix.lower()
    try:
        if extension in SUPPORTED_DOCUMENT_EXTENSIONS:
            text, _ = extract_document_text(path)
            return text or None
        # Match compare_files' text set — .rst/.log are plain text too.
        if extension in SUPPORTED_TEXT_EXTENSIONS | {".rst", ".log"}:
            return _read_prefix(path, 4 * 1024 * 1024).decode("utf-8", errors="replace")
    except OSError:
        return None
    return None


def _load_thresholds_arg(args: argparse.Namespace):
    """Resolve --thresholds to a ThresholdProfile; warn when it cannot load."""
    path = getattr(args, "thresholds", None)
    if path is None:
        return None
    profile = load_threshold_profile(path)
    if profile is None:
        print(f"warning: threshold profile unreadable or wrong version: {path}", file=sys.stderr)
    elif profile.provisional:
        print(
            f"warning: threshold profile {path} is provisional "
            f"({profile.samples} samples < {MIN_CALIBRATION_SAMPLES}); thresholds are unvalidated",
            file=sys.stderr,
        )
    return profile


def _write_json_out(path: Path, payload: str) -> None:
    """Write a JSON artifact, creating parent directories on demand."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload, encoding="utf-8")


def _maybe_sign(payload: dict[str, object], *, sign: bool, key_file: Path | None) -> dict[str, object]:
    """HMAC-sign a report payload when --sign was passed.

    Without a configured key the report is still written — unsigned, with a
    self-describing signature_note — rather than failing or fabricating a
    signature.
    """
    if not sign:
        return payload
    signed = sign_report(payload, resolve_report_key(key_file))
    if signed.get("signature") is None:
        print("note: report written unsigned (no key; set DEEPFAKE_LENS_REPORT_KEY or --key-file)", file=sys.stderr)
    return signed


def _has_subdirectories(folder: Path | str) -> bool:
    """True when the scan root has child directories the non-recursive scan cannot enter."""
    try:
        return any(child.is_dir() for child in Path(folder).iterdir())
    except OSError:
        return False


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
