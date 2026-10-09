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

from .calibration import IN_SAMPLE_LABEL
from .result_text import (
    IN_SAMPLE_CAVEAT,
    TEXT_LEGAL_LIMITATION,
    coverage_counts_text,
    coverage_gaps,
    deciding_evidence,
    evidence_counts,
    evidence_counts_text,
    item_kind_label,
    summary_line,
)
from .result_types import (
    EVIDENCE_KIND_LABELS,
    VERDICT_LABELS,
    CoverageStatus,
    EvidenceKind,
    Grade,
    ScanItem,
    Verdict,
    is_verdict_row,
    status_label,
)
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
    """Resolve --thresholds to a ThresholdProfile; warn when it cannot load.

    G7: the resolution rule (explicit file, else ``<models_dir>/thresholds.json``)
    lives in analysis_api.load_thresholds so the CLI, GUI and API share it.
    """
    from .analysis_api import AnalysisOptions, load_thresholds, thresholds_warning_printer

    return load_thresholds(AnalysisOptions.from_cli_args(args), warn=thresholds_warning_printer(sys.stderr))


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
        print("참고: 서명 키가 없어 보고서를 서명 없이 저장했습니다(DEEPFAKE_LENS_REPORT_KEY 또는 --key-file 지정).", file=sys.stderr)
    return signed


def _print_table(summary, items: list[ScanItem], *, include_low: bool, coverage: dict[str, object] | None = None, thresholds: object | None = None) -> None:
    if coverage is not None:
        wa_raw, wc_raw = coverage.get("weights_available", 0), coverage.get("weights_total", 0)
        wa = wa_raw if isinstance(wa_raw, int) else 0
        wc = wc_raw if isinstance(wc_raw, int) else 0
        if wa == 0:
            print("!! 신경망 미탑재(측정 게이트 미충족) — 결정적 근거만 반영 !!")
        elif wa < wc:
            print(f"!! 신경망 가중치 부분 탑재 ({wa}/{wc}) — 일부 뉴럴 엔진이 실행되지 않았습니다 !!")
    if thresholds is not None and getattr(thresholds, "provisional", False):
        print("!! 판정 임계값: 미측정 잠정값 — 표본 코퍼스로 보정되기 전에는 임계값을 근거로 쓰지 마십시오 !!")
    if thresholds is not None and getattr(thresholds, "in_sample", False):
        # G28: fitted and evaluated on the same rows — reference only.
        print(f"!! 판정 임계값: {IN_SAMPLE_LABEL} — {IN_SAMPLE_CAVEAT} !!")
    cap_note = " (파일 수 상한 도달)" if summary.capped else ""
    print(summary_line(summary) + cap_note)
    subfolders = getattr(summary, "subfolders_skipped", 0)
    if subfolders:
        # N8: a non-recursive scan never omits subfolders silently.
        print(f"참고: 하위 폴더 {subfolders}개는 검사하지 않았습니다(바로 아래 파일만 검사) — 포함하려면 --recursive 를 추가하십시오.")
    print(
        "결론은 세 가지뿐입니다: 조작·생성 근거 있음 / 원본성 근거 있음 / 판단 불가. "
        "통계(모델)·어휘(키워드) 신호는 보정 전까지 결론을 바꾸지 않습니다."
    )
    if any(item.result and item.result.grade == Grade.REFERENCE for item in items):
        print(f"참고: {TEXT_LEGAL_LIMITATION}")
    print()
    print(f"{'결론':<14} {'등급':<4} {'근거(결정·통계·어휘)':<18} {'검사(실행·미실행·실패)':<20} {'유형':<6} 파일")
    print("-" * 110)
    # R5/R16: every row is shown except "원본성 근거 있음" rows, which
    # --include-low adds; the omitted count is printed so the table always
    # reconciles with the header counts.
    visible = [item for item in items if include_low or _is_priority_row(item)]
    hidden = len(items) - len(visible)
    if not visible:
        print("표시할 행이 없습니다. --include-low 로 원본성 근거 있음 행을 함께 볼 수 있습니다.")
        return
    for item in visible:
        if item.result:
            result = item.result
            verdict = VERDICT_LABELS[result.verdict_code]
            grade = "참고" if result.grade == Grade.REFERENCE else "근거"
            counts = evidence_counts_text(result)
            checks = coverage_counts_text(result)
            top = deciding_evidence(result)
            gaps = [entry for entry in coverage_gaps(result) if entry.status == CoverageStatus.FAILED]
            reason = (
                f"[{EVIDENCE_KIND_LABELS[top.kind]}] {top.title}" if top else "근거 항목 없음"
            ) + (f" | 실패: {'; '.join(entry.describe() for entry in gaps)}" if gaps else "")
        else:
            # N7: Korean status in the 결론 column (건너뜀/미지원/실패/중복).
            verdict, grade, counts, checks = status_label(item.status), "-", "-", "-"
            reason = item.error or ""
        print(f"{verdict:<14} {grade:<4} {counts:<18} {checks:<20} {item_kind_label(item.kind):<6} {item.path}  # {reason}")
    if hidden:
        print(f"(원본성 근거 있음 {hidden}건은 표에서 생략했습니다 — --include-low 로 표시)")


def _is_priority_row(item: ScanItem) -> bool:
    """Rows shown without --include-low: everything but "원본성 근거 있음".

    R5: archive container rows are verdict rows like any other; failed,
    unsupported, skipped and duplicate rows are shown with their status so
    the table accounts for every header count.
    """
    if not is_verdict_row(item.status, item.result is not None) or item.result is None:
        return True
    return item.result.verdict_code != Verdict.AUTHENTICITY_EVIDENCE


# R16: CSV header names of the two v1 columns that used to read "score"
# (an uncalibrated number) and "risk" (a band).
CSV_CALIBRATED_SCORE_COLUMN = "보정점수(미보정시 공란)"
CSV_VERDICT_COLUMN = "결론"


def _write_csv(path: Path, items: list[ScanItem], *, coverage: dict[str, object] | None = None, thresholds: object | None = None) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        # Provenance is written as leading comment lines so the CSV can
        # never be mistaken for a fully-verified neural run.
        if coverage is not None:
            handle.write(f"# weights_available={coverage.get('weights_available', 0)} weights_total={coverage.get('weights_total', 0)}\n")
        if thresholds is not None:
            tp = thresholds if isinstance(thresholds, dict) else {
                "version": getattr(thresholds, "version", "?"),
                "provisional": getattr(thresholds, "provisional", True),
                "samples": getattr(thresholds, "samples", 0),
                "dataset_fingerprint": getattr(thresholds, "dataset_fingerprint", ""),
                "in_sample": getattr(thresholds, "in_sample", False),
                "source": "profile",
            }
            if tp.get("source") == "builtin_defaults":
                handle.write("# thresholds_source=builtin_defaults provisional=true\n")
            else:
                in_sample = f" in_sample=true label={IN_SAMPLE_LABEL}" if tp.get("in_sample") else ""
                handle.write(f"# thresholds_source=profile:{tp.get('version')} provisional={tp.get('provisional')} samples={tp.get('samples')} fingerprint={tp.get('dataset_fingerprint')}{in_sample}\n")
        else:
            handle.write("# thresholds_source=builtin_defaults provisional=true\n")
        writer = csv.writer(handle)
        writer.writerow(
            [
                "path",
                "kind",
                "status",
                # R16: the score is a calibrated probability x 100 or nothing
                # (blank when uncalibrated); the old "risk" column is the
                # three-way conclusion. Same column positions as v1.
                CSV_CALIBRATED_SCORE_COLUMN,
                CSV_VERDICT_COLUMN,
                "참고_픽셀_원점수",
                "참고_픽셀_신뢰도",
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
                # Contract v2 columns, appended so v1 column positions hold.
                "verdict_code",
                "verdict_label",
                "grade",
                "evidence_deterministic",
                "evidence_statistical",
                "evidence_lexical",
                "top_evidence",
                "coverage_failed",
                "coverage_skipped",
                "score_is_calibrated",
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
                    result.score if result and result.score_is_calibrated else "",
                    VERDICT_LABELS[result.verdict_code] if result else "",
                    pixel.raw_score if pixel and pixel.available else "",
                    pixel.reference_confidence if pixel and pixel.available else "",
                    pixel.model if pixel and pixel.available else "",
                    pixel.fusion if pixel and pixel.available else "",
                    _pixel_top_experts(pixel) if pixel and pixel.available else "",
                    model.label if model and model.available else "",
                    model.score if model and model.available else "",
                    pixel.heatmap_path if pixel and pixel.heatmap_path else "",
                    result.source_guess.label if result else "",
                    result.source_guess.confidence.value if result else "",
                    result.signals[0].title if result and result.signals else "",
                    item.error or "",
                    *_csv_v2_columns(item),
                ]
            )


def _csv_v2_columns(item: ScanItem) -> list[object]:
    result = item.result
    if result is None:
        return ["", "", "", "", "", "", "", "", "", ""]
    counts = evidence_counts(result)
    top = deciding_evidence(result)
    gaps = coverage_gaps(result)
    return [
        result.verdict_code.value,
        VERDICT_LABELS[result.verdict_code],
        result.grade.value,
        counts[EvidenceKind.DETERMINISTIC],
        counts[EvidenceKind.STATISTICAL],
        counts[EvidenceKind.LEXICAL],
        f"{top.kind.value}:{top.title}" if top else "",
        "; ".join(entry.describe() for entry in gaps if entry.status == CoverageStatus.FAILED),
        "; ".join(entry.describe() for entry in gaps if entry.status == CoverageStatus.SKIPPED),
        result.score_is_calibrated,
    ]


def _pixel_score_text(item: ScanItem) -> str:
    pixel = item.result.pixel_analysis if item.result else None
    if not pixel:
        return "-"
    if not pixel.available:
        return "사용 불가"
    return f"{pixel.raw_score}(참고)"


def _pixel_top_experts(pixel) -> str:
    active = [expert for expert in pixel.experts if expert.available and expert.score >= 45]
    return ";".join(expert.name for expert in sorted(active, key=lambda expert: expert.score, reverse=True)[:5])


def _parse_split_ratios(value: str) -> tuple[float, float, float]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("--split-ratios는 train,val,test 형식이어야 합니다")
    try:
        train, val, test = (float(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--split-ratios 값은 숫자여야 합니다") from exc
    if train < 0 or val < 0 or test < 0 or train + val + test <= 0:
        raise argparse.ArgumentTypeError("--split-ratios 값은 음수가 아니고 합이 0보다 커야 합니다")
    return train, val, test


def _parse_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]
