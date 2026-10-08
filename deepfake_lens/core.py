from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .archives import archive_format, extract_archive, is_archive
from .audio import SUPPORTED_AUDIO_EXTENSIONS, AudioAnalysis, analyze_audio
from .video_analysis import SUPPORTED_VIDEO_EXTENSIONS, VideoTemporalAnalysis, analyze_video_temporal
from .documents import SUPPORTED_DOCUMENT_EXTENSIONS, extract_document_text
from .model_adapter import FAILED_CONFIDENCE, ExternalModelAnalysis, analyze_external_model
from .pixel import DEFAULT_PIXEL_MAX_SIDE, PixelAnalysis, analyze_image_pixels
from .pixel import PixelExpertResult
from .png import read_png_dimensions, read_png_metadata
from .image_metadata import (  # noqa: F401
    DEFAULT_METADATA_BYTES,
    ImageMetadataRead,
    guess_image_source,
    read_image_metadata,
    read_image_metadata_full,
)
from .checks import AnalyzerError, CheckSkipped, run_check, skipped
from .image_class import MEASURABLE_MIN_SIDE_PX, ImageClass, classify_image, resolution_out_of_range
from .checks import failed as failed_entry
from .checks import failure_reason
from .checks import skipped as skipped_entry
from .decision import decide
from .result_text import TEXT_LEGAL_LIMITATION
from .evidence_rules import (
    c2pa_evidence,
    deep_layer_reference,
    document_metadata_evidence,
    image_class_evidence,
    image_metadata_evidence,
    lexical_evidence,
    model_evidence,
    reference_signal,
)
from .text_heuristics import (  # noqa: F401
    _frontier_llm_fingerprints,
    _generic_text_signal,
    _repeated_shingle_signal,
    _sentence_uniformity_signal,
    _technical_document_density,
    guess_text_source,
)


SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
SUPPORTED_TEXT_EXTENSIONS = {".txt", ".md"}
# 2: result contract v2 (verdict_code/grade/evidence/coverage, no medium
# band, score only from calibrated probability) — phase 0, G5/G6/G12/G24.
SCAN_JSON_SCHEMA_VERSION = 2
TOOL_VERSION = "0.1.0"  # kept in sync with pyproject version
DEFAULT_MAX_FILES = 1000
DEFAULT_TEXT_BYTES = 64 * 1024
# D10: reason on the row of a symlink found in a scanned folder.
SYMLINK_SKIP_REASON = "심볼릭 링크 — 링크를 따라가지 않으므로 분석하지 않았습니다(allow_symlinks=false)"
# D9: rejected archive members listed one coverage entry each, up to this
# many per container; the rest are summarized in one entry with the count.
MAX_ARCHIVE_REJECTION_ENTRIES = 100
# Smallest image side a detector is run on — defined once in image_class
# (the photo/non-photo gate also uses it for ``too_small``); kept here as
# an alias for existing callers.
MODEL_MIN_SIDE_PX = MEASURABLE_MIN_SIDE_PX
# ExternalModelAnalysis.confidence value marking a member that raised
# (inference error, integrity mismatch) rather than one that was skipped.
MODEL_FAILED_CONFIDENCE = FAILED_CONFIDENCE

logger = logging.getLogger(__name__)


# Result types and scan-cache/serialization helpers live in leaf modules;
# re-exported here so existing ``from .core import ...`` call sites keep
# working unchanged.
from .result_types import (  # noqa: F401
    RISK_LABELS,
    SOURCE_CONFIDENCE_LABELS,
    VERDICT_LABELS,
    BatchScanSummary,
    ClassificationResult,
    CoverageEntry,
    CoverageStatus,
    EvidenceDirection,
    EvidenceItem,
    EvidenceKind,
    EvidenceSignal,
    EvidenceStrength,
    Grade,
    RiskBand,
    ScanItem,
    SourceConfidence,
    SourceGuess,
    Verdict,
    band_for_verdict,
    check_label,
)
from .serialization import (  # noqa: F401
    _classification_result_from_json,
    _model_analysis_from_json,
    _pixel_analysis_from_json,
    _scan_item_from_json,
)
from .scan_cache import (  # noqa: F401
    _cache_key,
    _content_sha256,
    _cache_scan_context,
    _cached_scan_item,
    _with_content_sha256,
    _display_path,
    _duplicate_map,
    _file_fingerprint,
    _iter_files,
    _load_hash_db,
    _load_scan_cache,
    _read_prefix,
    _write_hash_db,
    _write_scan_cache,
)


AI_IDENTITY_PHRASES = [
    "as an ai",
    "language model",
    "i cannot browse",
    "ai assistant",
    "인공지능으로서",
    "언어 모델",
    "제가 직접 경험할 수는",
    "실시간으로 확인할 수는",
]

SYNTHETIC_WRITING_PHRASES = [
    "결론적으로",
    "요약하자면",
    "다음과 같습니다",
    "중요한 것은",
    "다양한 관점",
    "균형 잡힌",
    "전반적으로",
    "필수적입니다",
    "도움이 됩니다",
    "it is important to note",
    "in conclusion",
    "overall",
    "from multiple perspectives",
    "balanced approach",
]




def scan_directory(
    directory: Path | str,
    *,
    recursive: bool = False,
    max_files: int = DEFAULT_MAX_FILES,
    text_bytes: int = DEFAULT_TEXT_BYTES,
    metadata_bytes: int = DEFAULT_METADATA_BYTES,
    pixel_mode: str = "off",
    pixel_max_side: int = DEFAULT_PIXEL_MAX_SIDE,
    heatmaps: bool = False,
    heatmap_dir: Path | None = None,
    model_path: Path | str | list[Path | str] | tuple[Path | str, ...] | None = None,
    cache_path: Path | None = None,
    workers: int = 1,
    max_file_bytes: int | None = None,
    allow_symlinks: bool = False,
    dedupe: bool = False,
    hash_db_path: Path | None = None,
    deep_signals: bool = False,
    thresholds: object | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[BatchScanSummary, list[ScanItem]]:
    root = Path(directory)
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    paths: list[Path] = []
    capped = False
    iter_errors: list[tuple[Path, OSError]] = []
    symlinks: list[Path] = []
    for path in _iter_files(
        root, recursive=recursive, allow_symlinks=allow_symlinks,
        on_error=lambda p, e: iter_errors.append((p, e)),
        on_symlink=symlinks.append,
    ):
        if len(paths) >= max_files:
            capped = True
            break
        paths.append(path)

    # Archive containers are expanded into member jobs up front: each
    # member is analyzed like a regular file with an "archive::inner"
    # display path, and the archive itself gets a container row that
    # aggregates the worst member band. Temp extraction dirs are removed
    # in the finally block below.
    specs: list[tuple[Path, str | None]] = []
    archive_members: dict[str, list[ScanItem]] = {}
    archive_meta: dict[str, dict] = {}
    temp_dirs: list[Path] = []
    try:
        for path in paths:
            if not is_archive(path):
                specs.append((path, None))
                continue
            rel = _display_path(path, root=root)
            dest = Path(tempfile.mkdtemp(prefix="dflens-arc-"))
            temp_dirs.append(dest)
            dest = dest.resolve()
            try:
                extraction = extract_archive(path, dest)
            except Exception as exc:
                # A corrupt/unreadable archive becomes a failed container
                # row — one bad file must not kill the whole scan.
                logger.exception("archive extraction failed: %s", path)
                archive_meta[rel] = {
                    "path": path, "fmt": archive_format(path),
                    "skipped": 0, "warnings": [f"압축 해제 실패: {exc}"],
                    "error": failure_reason(exc),
                }
                archive_members[rel] = []
                continue
            archive_meta[rel] = {
                "path": path, "fmt": archive_format(path),
                "skipped": extraction.skipped, "warnings": extraction.warnings,
                "rejected": list(extraction.rejected),
            }
            archive_members[rel] = []
            for member in extraction.members:
                member_rel = member.relative_to(dest).as_posix()
                specs.append((member, f"{rel}::{member_rel}"))

        summary, items = _scan_specs(
            specs, duplicates_paths=[p for p, d in specs if d is None],
            archive_members=archive_members, archive_meta=archive_meta, root=root, dedupe=dedupe,
            max_file_bytes=max_file_bytes, hash_db_path=hash_db_path,
            text_bytes=text_bytes, metadata_bytes=metadata_bytes,
            pixel_mode=pixel_mode, pixel_max_side=pixel_max_side,
            heatmaps=heatmaps, heatmap_dir=heatmap_dir, model_path=model_path,
            cache_path=cache_path, workers=workers, deep_signals=deep_signals,
            capped=capped, thresholds=thresholds, should_stop=should_stop,
        )
        if iter_errors or symlinks:
            for err_path, exc in iter_errors:
                items.append(ScanItem(
                    _display_path(err_path, root=root), err_path.name,
                    "unknown", "failed", 0,
                    error=f"directory unreadable: {exc}",
                ))
            # D10: a symlink in the evidence folder is listed (never followed)
            # so the report accounts for every directory entry it was given.
            for link in symlinks:
                items.append(ScanItem(
                    _display_path(link, root=root), link.name,
                    "unknown", "skipped", 0,
                    error=SYMLINK_SKIP_REASON,
                ))
            items = sort_items(items)
            summary = summarize(items, capped=summary.capped, cached=summary.cached)
        return summary, items
    finally:
        for temp_dir in temp_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _archive_container_item(
    rel: str,
    name: str,
    path: Path,
    *,
    fmt: str | None,
    members: int,
    skipped: int,
    warnings: list[str],
    member_items: list[ScanItem] | None = None,
    extraction_error: str | None = None,
    rejected: list[tuple[str, str]] | None = None,
    sha256: str | None = None,
) -> ScanItem:
    """Container row for an expanded archive; rolls member verdicts up.

    manipulation if any member has manipulation evidence; authenticity only
    if every member was analyzed, none was skipped, and all have
    authenticity evidence; otherwise undetermined. The row carries no
    evidence of its own — the members do. Each member the extractor
    refused (traversal, absolute path, link, budget/bomb limits, …) is a
    ``skipped`` ``archive_member`` coverage entry and a limitation naming
    the member and the reason (D9); ``sha256`` is the archive file's
    digest so a signed report binds the container too.
    """
    rejected = list(rejected or [])
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    analyzed_members = [i for i in (member_items or []) if i.result is not None and i.status == "analyzed"]
    verdicts = [i.result.verdict_code for i in analyzed_members if i.result is not None]
    manipulated = sum(1 for v in verdicts if v == Verdict.MANIPULATION_EVIDENCE)
    if manipulated:
        verdict_code = Verdict.MANIPULATION_EVIDENCE
    elif (
        verdicts
        and all(v == Verdict.AUTHENTICITY_EVIDENCE for v in verdicts)
        and len(verdicts) == members
        and not skipped
        and not warnings
        and extraction_error is None
    ):
        verdict_code = Verdict.AUTHENTICITY_EVIDENCE
    else:
        verdict_code = Verdict.UNDETERMINED
    coverage = [
        failed_entry("archive", AnalyzerError(extraction_error))
        if extraction_error
        else CoverageEntry("archive", CoverageStatus.RAN)
    ]
    shown = rejected[:MAX_ARCHIVE_REJECTION_ENTRIES]
    coverage.extend(skipped_entry("archive_member", f"{name}: {reason}") for name, reason in shown)
    if len(rejected) > len(shown):
        coverage.append(skipped_entry("archive_member", f"외 {len(rejected) - len(shown)}개 구성 파일 거부(사유는 위 항목과 경고 참조)"))
    signals = [EvidenceSignal("압축 컨테이너", f"{fmt or 'archive'} 형식 — 구성 파일 {members}개 개별 분석" + (f", 스킵 {skipped}개" if skipped else ""), 0)]
    limitations = list(warnings)
    limitations.extend(f"구성 파일 거부: {name} — {reason}" for name, reason in shown)
    limitations.append("컨테이너 행은 구성 파일 결과의 요약입니다. '아카이브::경로' 형태의 개별 결과를 확인하세요.")
    first_rejection = f" 거부 {len(rejected)}개(예: {rejected[0][0]} — {rejected[0][1]})." if rejected else ""
    if analyzed_members:
        verdict = (
            f"압축 파일: {VERDICT_LABELS[verdict_code]} — 구성 파일 {members}개 분석"
            + (f"(조작·생성 근거 {manipulated}건)" if manipulated else "")
            + f", {skipped}개 스킵."
            + first_rejection
        )
    elif extraction_error:
        verdict = f"압축 파일: 판단 불가 — 압축 해제 실패({extraction_error})."
    else:
        verdict = "압축 파일: 판단 불가 — 아카이브에서 분석 가능한 구성 파일이 없습니다." + first_rejection
    band = band_for_verdict(verdict_code)
    return ScanItem(
        # "expanded" (not "analyzed") keeps the roll-up row out of the
        # verdict counts — members already carry their own verdicts, and
        # counting the container too would double every member's tally.
        rel, name, "archive", "expanded" if analyzed_members else "unknown", size,
        ClassificationResult(
            score=max((i.result.score for i in analyzed_members if i.result is not None), default=0),
            band=band,
            band_label=VERDICT_LABELS[verdict_code],
            verdict=verdict,
            signals=signals,
            limitations=limitations,
            source_guess=SourceGuess.unknown("압축 컨테이너에는 출처 추정이 적용되지 않습니다."),
            next_checks=["구성 파일 중 조작·생성 근거가 있는 항목부터 검토하세요."],
            verdict_code=verdict_code,
            coverage=coverage,
        ),
        sha256=sha256,
    )


def _scan_specs(
    specs: list[tuple[Path, str | None]],
    *,
    duplicates_paths: list[Path],
    archive_members: dict[str, list[ScanItem]],
    archive_meta: dict[str, dict],
    root: Path,
    dedupe: bool,
    max_file_bytes: int | None,
    hash_db_path: Path | None,
    text_bytes: int,
    metadata_bytes: int,
    pixel_mode: str,
    pixel_max_side: int,
    heatmaps: bool,
    heatmap_dir: Path | None,
    model_path: Path | str | list[Path | str] | tuple[Path | str, ...] | None,
    cache_path: Path | None,
    workers: int,
    deep_signals: bool,
    capped: bool,
    thresholds: object | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[BatchScanSummary, list[ScanItem]]:
    """Analyze (path, display) spec pairs — the inner loop of scan_directory."""
    fingerprints: dict[Path, str] = {}  # per-scan SHA-256 memo shared by dedupe, cache key, item.sha256 (G11)
    duplicates = _duplicate_map(duplicates_paths, root=root, max_file_bytes=max_file_bytes, hash_db_path=hash_db_path, fingerprints=fingerprints) if dedupe or hash_db_path else {}
    cache = _load_scan_cache(cache_path)
    cache_items = cache.setdefault("items", {}) if cache is not None else {}
    # Cache keys embed analysis provenance so a stored verdict computed
    # under different thresholds or model coverage is never replayed as
    # if it were produced by the current configuration.
    cache_provenance = _cache_provenance(thresholds) if cache is not None else ""
    scan_context = _cache_scan_context(model_path) if cache is not None else ""

    def analyze_one(spec: tuple[Path, str | None]) -> tuple[ScanItem, str | None, bool]:
        path, display = spec
        if should_stop is not None and should_stop():
            return ScanItem(display or _display_path(path, root=root), path.name, "unknown", "skipped", 0, error="scan cancelled"), None, False
        if path in duplicates:
            display_path = display or _display_path(path, root=root)
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            return ScanItem(display_path, path.name, "duplicate", "duplicate", size, error="duplicate content", duplicate_of=duplicates[path], sha256=fingerprints.get(path)), None, False
        if max_file_bytes is not None:
            try:
                size = path.stat().st_size
            except OSError as exc:
                return ScanItem(display or _display_path(path, root=root), path.name, "unknown", "failed", 0, error=str(exc)), None, False
            if size > max_file_bytes:
                return ScanItem(display or _display_path(path, root=root), path.name, "unknown", "skipped", size, error=f"file exceeds --max-file-bytes ({max_file_bytes})"), None, False
        # Archive members live in a temp dir with unstable paths — caching
        # them would both miss every scan and bloat the cache file.
        key = None
        if display is None:
            key = _cache_key(
                path,
                root=root,
                text_bytes=text_bytes,
                metadata_bytes=metadata_bytes,
                pixel_mode=pixel_mode,
                pixel_max_side=pixel_max_side,
                heatmaps=heatmaps,
                model_path=model_path,
                deep_signals=deep_signals,
                provenance=cache_provenance,
                fingerprints=fingerprints,
                scan_context=scan_context,
            ) if cache is not None else None
            cached_item = _cached_scan_item(cache_items.get(key) if key and isinstance(cache_items, dict) else None, path, root=root)
            if cached_item is not None:
                return _with_content_sha256(cached_item, path, fingerprints), key, True
        try:
            item = analyze_file(
            path,
            root=root,
            display=display,
            text_bytes=text_bytes,
            metadata_bytes=metadata_bytes,
            pixel_mode=pixel_mode,
            pixel_max_side=pixel_max_side,
            # Member heatmaps would land inside the temp extraction dir and
            # be deleted before the GUI could fetch them.
            heatmaps=heatmaps and display is None,
            heatmap_dir=heatmap_dir,
            model_path=model_path,
            deep_signals=deep_signals,
            thresholds=thresholds,
        )
        except Exception as exc:
            # Analyzer internals can raise non-OSError (codec errors,
            # malformed profiles, decoder failures). Surface a failed row
            # instead of aborting the entire batch.
            logger.exception("analysis failed: %s", path)
            item = ScanItem(
                display or _display_path(path, root=root),
                path.name, "unknown", "failed", 0,
                error=f"analysis error: {type(exc).__name__}: {exc}",
            )
            return item, key, False
        item = _with_content_sha256(item, path, fingerprints)
        if display is not None and "::" in display:
            archive_members.setdefault(display.split("::", 1)[0], []).append(item)
        return item, key, False

    # Flush per-item results into the cache as the scan proceeds — a
    # killed/cancelled multi-hour scan must not lose every completed
    # analysis. A rerun with the same --cache resumes instantly on the
    # items that already finished.
    _CACHE_FLUSH_EVERY = 20

    def flush_cache(entries: list[tuple[ScanItem, str | None, bool]]) -> None:
        if cache is None:
            return
        for item, key, _ in entries:
            if key:
                cache_items[key] = item.to_json()
        _write_scan_cache(cache_path, cache)

    analyzed: list[tuple[ScanItem, str | None, bool]] = []
    if workers > 1 and len(specs) > 1:
        # Each worker still checks should_stop so a cancel short-circuits
        # remaining items instead of running every analysis to completion.
        with ThreadPoolExecutor(max_workers=workers) as executor:
            analyzed = list(executor.map(analyze_one, specs))
    else:
        try:
            for spec in specs:
                if should_stop is not None and should_stop():
                    break
                analyzed.append(analyze_one(spec))
                if len(analyzed) % _CACHE_FLUSH_EVERY == 0:
                    flush_cache(analyzed[-_CACHE_FLUSH_EVERY:])
        finally:
            flush_cache(analyzed)

    cached_count = sum(1 for _, _, was_cached in analyzed if was_cached)
    items = [item for item, _, _ in analyzed]
    if cache is not None and workers > 1:
        flush_cache(analyzed)

    for rel, member_items in archive_members.items():
        meta = archive_meta.get(rel, {})
        arc_path = meta.get("path", Path(rel))
        items.append(_archive_container_item(
            rel, arc_path.name, arc_path, fmt=meta.get("fmt") or archive_format(rel),
            members=len(member_items), skipped=meta.get("skipped", 0),
            warnings=meta.get("warnings", []), member_items=member_items,
            extraction_error=meta.get("error"),
            rejected=meta.get("rejected", []),
            # D9: the container's own digest binds the archive into a signed report.
            sha256=_content_sha256(arc_path, fingerprints) or None,
        ))

    sorted_items = sort_items(items)
    return summarize(sorted_items, capped=capped, cached=cached_count), sorted_items

def analyze_file(
    path: Path | str,
    *,
    root: Path | None = None,
    display: str | None = None,
    text_bytes: int = DEFAULT_TEXT_BYTES,
    metadata_bytes: int = DEFAULT_METADATA_BYTES,
    pixel_mode: str = "off",
    pixel_max_side: int = DEFAULT_PIXEL_MAX_SIDE,
    heatmaps: bool = False,
    heatmap_dir: Path | None = None,
    model_path: Path | str | list[Path | str] | tuple[Path | str, ...] | None = None,
    deep_signals: bool = False,
    thresholds: "object | None" = None,
) -> ScanItem:
    file_path = Path(path)
    display_path = display or _display_path(file_path, root=root)
    item_name = display or file_path.name
    try:
        size = file_path.stat().st_size
    except OSError as exc:
        return ScanItem(display_path, item_name, "unknown", "failed", 0, error=str(exc))
    extension = file_path.suffix.lower()

    if is_archive(file_path):
        # Single-item callers get a container row; member-level results
        # come through scan_directory / upload paths that expand first.
        # An unexpanded container is undetermined — never a clean verdict.
        return ScanItem(
            display_path, item_name, "archive", "analyzed", size,
            build_classification_result(
                subject="압축 파일",
                evidence=[],
                coverage=[skipped("archive", "단일 파일 분석에서는 압축 내부를 펼치지 않습니다")],
                source_guess=SourceGuess.unknown("압축 컨테이너에는 출처 추정이 적용되지 않습니다."),
                limitations=[
                    f"{archive_format(file_path)} 압축 파일 — 내부 파일은 폴더 스캔 또는 업로드 경로에서 개별 분석됩니다.",
                ],
                next_checks=["압축 파일이 포함된 폴더를 스캔하거나 업로드하세요."],
            ),
        )

    if extension in SUPPORTED_TEXT_EXTENSIONS or extension in SUPPORTED_DOCUMENT_EXTENSIONS:
        try:
            return ScanItem(
                display_path, item_name, "text", "analyzed", size,
                _analyze_text_file(file_path, extension, text_bytes=text_bytes, model_path=model_path),
            )
        except OSError as exc:
            return ScanItem(display_path, item_name, "text", "failed", size, error=str(exc))

    if extension in SUPPORTED_IMAGE_EXTENSIONS:
        try:
            metadata_read = read_image_metadata_full(file_path, metadata_bytes=metadata_bytes)
            result = _analyze_image_file(
                file_path, metadata_read.metadata, metadata_read.dimensions,
                root=root, pixel_mode=pixel_mode, pixel_max_side=pixel_max_side,
                heatmaps=heatmaps, heatmap_dir=heatmap_dir, model_path=model_path,
                deep_signals=deep_signals, thresholds=thresholds,
                metadata_read=metadata_read,
            )
            return ScanItem(display_path, item_name, "image", "analyzed", size, result)
        except OSError as exc:
            return ScanItem(display_path, item_name, "image", "failed", size, error=str(exc))

    if extension in SUPPORTED_AUDIO_EXTENSIONS:
        return ScanItem(display_path, item_name, "audio", "analyzed", size, _analyze_audio_file(file_path, model_path=model_path))

    if extension in SUPPORTED_VIDEO_EXTENSIONS:
        return ScanItem(
            display_path, item_name, "video", "analyzed", size,
            _analyze_video_file(file_path, model_path=model_path, deep_signals=deep_signals, thresholds=thresholds),
        )

    return ScanItem(display_path, item_name, "unsupported", "unsupported", size, error="지원 형식이 아닙니다.")


# ---------------------------------------------------------------------------
# Per-modality analysis. Every analyzer call goes through checks.run_check so
# its outcome lands in ``coverage``; evidence comes from evidence_rules; the
# verdict comes from decision.decide via build_classification_result.
# ---------------------------------------------------------------------------

DEEP_IMAGE_CHECKS = ("face_manipulation", "inpaint", "faceswap_seam")
DEEP_VIDEO_CHECKS = ("rppg", "avatar", "lipsync", "face_track")
DEEP_DISABLED_REASON = "비활성화(deep_signals=false)"
# Shown in the verdict sentence and first limitation of a gated image (WP-D).
NON_PHOTO_NOTICE = "사진 아님 — 생성 탐지 비적용"
OUT_OF_RANGE_NOTICE = "측정 범위 밖(해상도) — 생성 탐지 비적용"


def _model_entry(check: str, *, available: bool, confidence: str, detail: str) -> CoverageEntry:
    if available:
        return CoverageEntry(check, CoverageStatus.RAN)
    reason = detail.strip() or "모델 결과 없음"
    if confidence == MODEL_FAILED_CONFIDENCE:
        return CoverageEntry(check, CoverageStatus.FAILED, reason)
    if reason.startswith("의존성 부재"):
        return CoverageEntry(check, CoverageStatus.SKIPPED, reason)
    return CoverageEntry(check, CoverageStatus.SKIPPED, f"모델 실행 불가: {reason}")


def model_coverage(model: ExternalModelAnalysis, check: str = "external_model") -> list[CoverageEntry]:
    """Coverage entries for an external-model result.

    A model zoo/profile set reports one entry per member (``model:<name>``)
    so the examiner sees exactly which detector ran, was skipped, or failed.
    """
    members = [entry for entry in model.models if isinstance(entry, dict) and "profile" in entry]
    if members:
        return [
            _model_entry(
                f"model:{member.get('model') or Path(str(member.get('profile'))).stem}",
                available=bool(member.get("available")),
                confidence=str(member.get("confidence") or ""),
                detail=str(member.get("detail") or ""),
            )
            for member in members
        ]
    return [_model_entry(check, available=model.available, confidence=model.confidence, detail=model.detail)]


def _run_model(
    media_path: Path,
    model_path: Path | str | list[Path | str] | tuple[Path | str, ...] | None,
    *,
    modality: str,
    dimensions: tuple[int, int] | None = None,
) -> tuple[ExternalModelAnalysis | None, list[CoverageEntry]]:
    """Run the external-model check with its range gate (G1/QA-OUT-5)."""
    out_of_range = resolution_out_of_range(dimensions) if model_path is not None else None
    if out_of_range:
        return None, [skipped("external_model", out_of_range)]
    model, entry = run_check(
        "external_model",
        lambda: analyze_external_model(media_path, model_path, modality=modality),
    )
    if entry.status != CoverageStatus.RAN:
        return None, [entry]
    if model is None:
        return None, [_no_model_entry(model_path, modality)]
    return model, model_coverage(model)


def _no_model_entry(model_path: object | None, modality: str) -> CoverageEntry:
    if model_path is None:
        return skipped("external_model", "모델 프로필 미지정")
    return skipped("external_model", f"{modality} 형식에 맞는 모델 프로필 없음")


def _validate_c2pa(path: Path) -> dict[str, object]:
    import c2pa  # noqa: F401 — dependency probe: absent SDK is "skipped", not "failed"

    from .c2pa import validate_c2pa_manifest

    validation = validate_c2pa_manifest(path)
    if validation is None:
        raise ModuleNotFoundError("c2pa", name="c2pa")
    if validation.get("status") == "unavailable":
        # D8: a reader/validation error is a failed check — whether a
        # manifest exists is unknown, so it is never reported as absent.
        if validation.get("error_kind") == "not_supported":
            raise CheckSkipped(f"C2PA SDK가 지원하지 않는 형식: {validation.get('error', '')}")
        stage = "검증" if validation.get("present") else "판독"
        raise AnalyzerError(f"C2PA {stage} 실패: {validation.get('error', '')}")
    return validation


# Words in a legacy analyzer's "unavailable" verdict that mean the input
# could not be processed (a failure) rather than that the check does not
# apply (a skip, e.g. no face or clip too short).
_ANALYZER_ERROR_MARKERS = ("읽을 수 없", "디코딩할 수 없", "읽기 오류", "열 수 없", "열기 오류", "오류", "error", "failed")


def _raise_unavailable(message: str) -> None:
    lowered = message.lower()
    if any(marker in lowered for marker in _ANALYZER_ERROR_MARKERS):
        raise AnalyzerError(message)
    raise CheckSkipped(message or "적용 불가")


@dataclass
class DeepLayers:
    """Outcome of the opt-in deep layers for one file.

    Uncalibrated deep-layer outputs go to ``reference`` (D13) — never to
    ``evidence``; ``evidence`` stays for a future calibrated layer.
    """

    evidence: list[EvidenceItem] = field(default_factory=list)
    coverage: list[CoverageEntry] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    reference: list[EvidenceSignal] = field(default_factory=list)


def _deep_image_layers(path: Path, thresholds=None) -> DeepLayers:
    """Opt-in deep image layers: face manipulation, inpainting, face-swap seam.

    Each layer is a separate check: a missing dependency is ``skipped``, no
    detected face is ``skipped`` "얼굴 미검출", any other exception is
    ``failed`` (G1/G12). Flags become reference signals (D13: no calibration,
    so no part in the decision).
    """
    out = DeepLayers()

    def face_check():
        import cv2  # noqa: F401 — dependency probe

        from .face import analyze_faces

        face = analyze_faces(path)
        if face.face_count == 0:
            if face.manipulation_type == "none":
                raise CheckSkipped("얼굴 미검출")
            if face.manipulation_type == "unavailable":
                raise CheckSkipped(f"의존성 부재: {face.verdict}")
            raise AnalyzerError(face.verdict)
        return face

    face, entry = run_check("face_manipulation", face_check)
    out.coverage.append(entry)
    if face is not None:
        if face.score > 0:
            out.reference.append(deep_layer_reference(
                "얼굴 조작 분석", f"{face.verdict} (faces={face.face_count}, {face.manipulation_type})", face.score,
            ))
        out.limitations.extend(face.limitations[:2])

    def inpaint_check():
        import cv2  # noqa: F401 — dependency probe

        from .inpaint import analyze_inpainting

        inpaint = analyze_inpainting(path)
        if inpaint.band == "unknown":
            _raise_unavailable(inpaint.verdict)
        return inpaint

    inpaint, entry = run_check("inpaint", inpaint_check)
    out.coverage.append(entry)
    if inpaint is not None:
        if inpaint.regions_detected:
            out.reference.append(deep_layer_reference(
                "인페인팅/부분 변형 탐지", f"{inpaint.verdict} (영역 {inpaint.regions_detected}개)", inpaint.score,
            ))
        out.limitations.extend(inpaint.limitations[:2])

    def seam_check():
        import cv2  # noqa: F401 — dependency probe

        from .face import face_detector_unavailable_reason
        from .faceswap_seam import analyze_faceswap_seam

        missing = face_detector_unavailable_reason()
        if missing:
            raise CheckSkipped(f"의존성 부재: {missing}")
        seam = analyze_faceswap_seam(path, thresholds=thresholds)
        if seam.band == "unknown":
            if seam.face_count == 0 and "얼굴" in seam.verdict:
                raise CheckSkipped("얼굴 미검출")
            _raise_unavailable(seam.verdict)
        return seam

    seam, entry = run_check("faceswap_seam", seam_check)
    out.coverage.append(entry)
    if seam is not None:
        for sig in seam.signals:
            out.reference.append(deep_layer_reference(sig.title, sig.detail, sig.weight))
        out.limitations.extend(seam.limitations[:2])
    return out


def _require_haar(cv2_module: object) -> None:
    """rPPG and lip-sync call cv2.CascadeClassifier directly; OpenCV 5
    builds without contrib lack it, which is a missing dependency, not an
    analysis failure."""
    if not hasattr(cv2_module, "CascadeClassifier"):
        raise CheckSkipped("의존성 부재: cv2.CascadeClassifier (opencv-contrib)")


def _deep_video_layers(path: Path, thresholds=None) -> DeepLayers:
    """Opt-in deep video layers: rPPG, avatar, lip-sync, face-track."""
    out = DeepLayers()

    def rppg_check():
        import cv2

        from .rppg import analyze_rppg

        _require_haar(cv2)
        rppg = analyze_rppg(path)
        if rppg.band == "unknown":
            _raise_unavailable(rppg.verdict)
        return rppg

    rppg, entry = run_check("rppg", rppg_check)
    out.coverage.append(entry)
    if rppg is not None:
        if rppg.score > 0:
            out.reference.append(deep_layer_reference("rPPG 맥박 신호", rppg.verdict, rppg.score))
        out.limitations.extend(rppg.limitations[:2])

    def avatar_check():
        from .avatar import analyze_avatar

        return analyze_avatar(path)

    avatar, entry = run_check("avatar", avatar_check)
    out.coverage.append(entry)
    if avatar is not None:
        from .avatar import FORMAT_SIGNAL_TITLE

        # "It is a video" is not an avatar indicator: only marker signals
        # become (non-deciding) evidence.
        markers = [signal for signal in avatar.signals if signal.title != FORMAT_SIGNAL_TITLE]
        if markers:
            raw = min(100, sum(signal.weight for signal in markers))
            out.reference.append(deep_layer_reference("아바타/디지털휴먼 탐지", avatar.verdict, raw))
        out.limitations.extend(avatar.limitations[:2])

    def lipsync_check():
        import cv2

        from .lipsync import analyze_lipsync

        _require_haar(cv2)
        lipsync = analyze_lipsync(path)
        if not lipsync.available:
            _raise_unavailable(lipsync.verdict)
        return lipsync

    lipsync, entry = run_check("lipsync", lipsync_check)
    out.coverage.append(entry)
    if lipsync is not None:
        if lipsync.score > 0:
            out.reference.append(deep_layer_reference("립싱크 일관성", lipsync.verdict, lipsync.score))
        out.limitations.extend(lipsync.limitations[:2])

    def track_check():
        from .face import face_detector_unavailable_reason
        from .face_track import analyze_face_track

        missing = face_detector_unavailable_reason()
        if missing:
            raise CheckSkipped(f"의존성 부재: {missing}")
        track = analyze_face_track(path, thresholds=thresholds)
        if not track.available:
            _raise_unavailable(track.verdict)
        return track

    track, entry = run_check("face_track", track_check)
    out.coverage.append(entry)
    if track is not None:
        if track.score > 0:
            out.reference.append(deep_layer_reference("얼굴 트랙 시간-일관성", track.verdict, track.score))
        out.limitations.extend(track.limitations[:2])
    return out


def _deep_disabled(checks: tuple[str, ...]) -> DeepLayers:
    return DeepLayers(coverage=[skipped(check, DEEP_DISABLED_REASON) for check in checks])


def _analyze_image_file(
    file_path: Path,
    metadata: dict[str, str],
    dimensions: tuple[int, int] | None,
    *,
    root: Path | None,
    pixel_mode: str,
    pixel_max_side: int,
    heatmaps: bool,
    heatmap_dir: Path | None,
    model_path: Path | str | list[Path | str] | tuple[Path | str, ...] | None,
    deep_signals: bool,
    thresholds: object | None,
    metadata_read: ImageMetadataRead | None = None,
) -> ClassificationResult:
    # D16: a metadata read that stopped early (truncated structure, empty
    # file, unrecognized header, EXIF error) is a failed check, not an
    # absence of metadata.
    read_error = metadata_read.error if metadata_read is not None else None
    coverage: list[CoverageEntry] = [
        failed_entry("metadata", AnalyzerError(read_error)) if read_error else CoverageEntry("metadata", CoverageStatus.RAN)
    ]
    c2pa_validation, entry = run_check("c2pa", lambda: _validate_c2pa(file_path))
    coverage.append(entry)
    c2pa_unknown = entry.status == CoverageStatus.FAILED

    # Photo/non-photo gate (WP-D, G13): detectors are only applied to
    # photographs. A non-photo (or too small) image keeps metadata + C2PA;
    # pixel, model and deep checks are recorded as skipped with the class.
    # If the gate itself cannot run, its entry says so (skipped/failed) and
    # the detectors run as before — a failed gate already forces undetermined.
    image_class, entry = run_check("image_class", lambda: classify_image(file_path, dimensions=dimensions))
    coverage.append(entry)
    gate = image_class.skip_reason() if image_class is not None and not image_class.is_photo else None

    pixel_analysis: PixelAnalysis | None = None
    if gate:
        coverage.append(skipped("pixel", gate))
    elif pixel_mode == "off":
        coverage.append(skipped("pixel", "비활성화(pixel=off) — 참고 신호 전용 검사"))
    else:
        pixel_analysis, entry = run_check(
            "pixel",
            lambda: analyze_image_pixels(
                file_path,
                mode=pixel_mode,
                max_side=pixel_max_side,
                heatmap_path=_heatmap_path_for(file_path, root=root, heatmap_dir=heatmap_dir) if heatmaps else None,
            ),
            reraise=(OSError,),
        )
        if pixel_analysis is not None and not pixel_analysis.available:
            reason = pixel_analysis.limitations[0] if pixel_analysis.limitations else "픽셀 분석 불가"
            entry = skipped("pixel", reason)
        coverage.append(entry)

    model_analysis: ExternalModelAnalysis | None = None
    if gate:
        coverage.append(skipped("external_model", gate))
    else:
        model_analysis, model_entries = _run_model(file_path, model_path, modality="image", dimensions=dimensions)
        coverage.extend(model_entries)

    if gate:
        deep = DeepLayers(coverage=[skipped(check, gate) for check in DEEP_IMAGE_CHECKS])
    else:
        deep = _deep_image_layers(file_path, thresholds) if deep_signals else _deep_disabled(DEEP_IMAGE_CHECKS)
    coverage.extend(deep.coverage)

    image_format = metadata_read.image_format if metadata_read is not None else None
    jpeg_quality = None
    if image_format == "jpeg" and (metadata.get("exif.Make") or metadata.get("exif.Model")):
        from .jpegq import estimate_jpeg_quality

        jpeg_quality = estimate_jpeg_quality(file_path)
    return analyze_image_metadata(
        metadata,
        dimensions=dimensions,
        pixel_analysis=pixel_analysis,
        model_analysis=model_analysis,
        c2pa_validation=c2pa_validation,
        coverage=coverage,
        extra_evidence=deep.evidence,
        extra_limitations=[*(metadata_read.notes if metadata_read is not None else []), *deep.limitations],
        image_class=image_class,
        metadata_read_error=read_error,
        c2pa_unknown=c2pa_unknown,
        image_format=image_format,
        jpeg_quality=jpeg_quality,
        extra_reference=deep.reference,
    )


def _analyze_audio_file(
    file_path: Path,
    *,
    model_path: Path | str | list[Path | str] | tuple[Path | str, ...] | None,
) -> ClassificationResult:
    analysis, entry = run_check("audio_analysis", lambda: analyze_audio(file_path, model_path=model_path))
    if analysis is None:
        return build_classification_result(
            subject="오디오",
            evidence=[],
            coverage=[entry],
            source_guess=SourceGuess.unknown("오디오를 분석하지 못했습니다."),
            limitations=[],
            next_checks=AUDIO_NEXT_CHECKS,
        )
    coverage = [entry]
    if analysis.features is not None:
        coverage.append(CoverageEntry("audio_features", CoverageStatus.RAN))
    elif analysis.feature_error.startswith("의존성 부재"):
        coverage.append(skipped("audio_features", analysis.feature_error))
    elif analysis.feature_error:
        coverage.append(CoverageEntry("audio_features", CoverageStatus.FAILED, analysis.feature_error))
    elif importlib.util.find_spec("librosa") is None:
        coverage.append(skipped("audio_features", "의존성 부재: librosa"))
    else:
        # Early-exit error analysis (empty/oversized/unreadable file).
        coverage.append(failed_entry("audio_features", AnalyzerError(analysis.verdict)))
    if analysis.model_analysis is None:
        coverage.append(_no_model_entry(model_path, "audio"))
    else:
        coverage.extend(model_coverage(analysis.model_analysis))
    return _audio_result(analysis, coverage=coverage)


def _audio_result(analysis: AudioAnalysis, *, coverage: list[CoverageEntry] | None = None) -> ClassificationResult:
    """Adapt an AudioAnalysis into the result contract.

    Audio: legacy acoustic heuristics are reference-only; the model is
    statistical evidence; nothing deterministic exists yet (G21, phase 1).
    """
    if coverage is None:
        coverage = [CoverageEntry("audio_analysis", CoverageStatus.RAN)]
        if analysis.model_analysis is not None:
            coverage.extend(model_coverage(analysis.model_analysis))
    evidence: list[EvidenceItem] = []
    model_item = model_evidence(analysis.model_analysis)
    if model_item is not None:
        evidence.append(model_item)
    reference = [
        reference_signal(signal.title, signal.detail, signal.weight)
        for signal in analysis.signals
        if not signal.title.startswith("외부 모델")
    ]
    source_known = bool(analysis.source_guess) and analysis.source_guess != "unknown"
    source_guess = SourceGuess(
        analysis.source_guess if source_known else "출처 단서 없음",
        SourceConfidence.LOW if source_known else SourceConfidence.UNKNOWN,
        [analysis.source_guess] if source_known else ["오디오에서 출처를 판단할 단서가 부족합니다."],
    )
    limitations = [
        "오디오 음향 휴리스틱은 측정 전 참고 신호이며 결론에 참여하지 않습니다.",
        *analysis.limitations,
        *(analysis.model_analysis.limitations if analysis.model_analysis else []),
    ]
    return build_classification_result(
        subject="오디오",
        evidence=evidence,
        coverage=coverage,
        source_guess=source_guess,
        limitations=limitations,
        next_checks=AUDIO_NEXT_CHECKS,
        reference_signals=reference,
        model_analysis=analysis.model_analysis,
    )


def _analyze_video_file(
    file_path: Path,
    *,
    model_path: Path | str | list[Path | str] | tuple[Path | str, ...] | None,
    deep_signals: bool,
    thresholds: object | None,
) -> ClassificationResult:
    def video_check() -> VideoTemporalAnalysis:
        import cv2  # noqa: F401 — dependency probe

        analysis = analyze_video_temporal(file_path, model_path=model_path, analyze_audio_track=True)
        if analysis.band == "unknown" and analysis.duration_seconds <= 0 and not analysis.signals:
            raise AnalyzerError(analysis.verdict)
        return analysis

    analysis, entry = run_check("video_analysis", video_check)
    deep = _deep_video_layers(file_path, thresholds) if deep_signals else _deep_disabled(DEEP_VIDEO_CHECKS)
    coverage: list[CoverageEntry] = [entry]
    if analysis is None:
        if model_path is not None:
            coverage.append(skipped("external_model", "영상 분석이 실행되지 않아 모델 검사도 수행되지 않음"))
        coverage.extend(deep.coverage)
        return build_classification_result(
            subject="영상",
            evidence=deep.evidence,
            coverage=coverage,
            source_guess=SourceGuess.unknown("영상 분석이 불완전해 출처를 판단할 단서가 없습니다."),
            limitations=deep.limitations,
            next_checks=VIDEO_NEXT_CHECKS,
            reference_signals=deep.reference,
        )
    if analysis.model_analysis is None:
        coverage.append(_no_model_entry(model_path, "video"))
    else:
        coverage.extend(model_coverage(analysis.model_analysis))
    coverage.extend(deep.coverage)
    return _video_result(analysis, coverage=coverage, deep=deep)


def _video_result(
    analysis: VideoTemporalAnalysis,
    *,
    coverage: list[CoverageEntry] | None = None,
    deep: DeepLayers | None = None,
) -> ClassificationResult:
    """Adapt a VideoTemporalAnalysis into the result contract.

    Temporal heuristics are reference-only; the frame model is statistical
    evidence; deep layers are reference signals (D13).
    """
    deep = deep or DeepLayers()
    if coverage is None:
        coverage = [CoverageEntry("video_analysis", CoverageStatus.RAN)]
        if analysis.model_analysis is not None:
            coverage.extend(model_coverage(analysis.model_analysis))
    evidence: list[EvidenceItem] = list(deep.evidence)
    model_item = model_evidence(analysis.model_analysis)
    if model_item is not None:
        evidence.append(model_item)
    reference = [
        reference_signal(signal.title, signal.detail, signal.weight)
        for signal in analysis.signals
        if not signal.title.startswith("외부 모델")
    ]
    reference.extend(deep.reference)
    has_detail = analysis.frame_count > 0 and analysis.duration_seconds > 0
    source_guess = SourceGuess.unknown(
        "영상 파일의 컨테이너 메타데이터에서 출처 단서를 찾지 못했습니다."
        if has_detail
        else "영상 분석이 불완전해 출처를 판단할 단서가 없습니다."
    )
    limitations = [
        "영상 시간축 휴리스틱은 측정 전 참고 신호이며 결론에 참여하지 않습니다.",
        *analysis.limitations,
        *(analysis.model_analysis.limitations if analysis.model_analysis else []),
        *deep.limitations,
    ]
    return build_classification_result(
        subject="영상",
        evidence=evidence,
        coverage=coverage,
        source_guess=source_guess,
        limitations=limitations,
        next_checks=VIDEO_NEXT_CHECKS,
        reference_signals=reference,
        model_analysis=analysis.model_analysis,
        av_audio=analysis.av_audio,
    )


def _analyze_text_file(
    file_path: Path,
    extension: str,
    *,
    text_bytes: int,
    model_path: Path | str | list[Path | str] | tuple[Path | str, ...] | None,
) -> ClassificationResult:
    coverage: list[CoverageEntry] = []
    doc_metadata: dict[str, str] = {}
    model_input = file_path
    tmp_text_path: Path | None = None
    if extension in SUPPORTED_DOCUMENT_EXTENSIONS:
        extracted, entry = run_check("document_text", lambda: extract_document_text(file_path), reraise=(OSError,))
        text, doc_metadata = extracted if extracted is not None else ("", {})
        extractor = doc_metadata.get("extractor", "")
        if entry.status == CoverageStatus.RAN:
            if extractor.startswith("unavailable:"):
                entry = skipped("document_text", f"의존성 부재: {extractor.split(':', 1)[1]}")
            elif extractor.startswith("skipped:"):
                entry = skipped("document_text", f"측정 범위 밖: {extractor.split(':', 1)[1]}")
            elif extractor.startswith("failed:"):
                entry = failed_entry("document_text", AnalyzerError(f"문서 텍스트 추출 실패 ({extractor})"))
        coverage.append(entry)
        # Binary containers (docx/hwp/pdf) must not reach the text members
        # as raw bytes — feed the extracted text instead so PPL/binoculars
        # and the language gate see real prose.
        if text.strip():
            fd, tmp_name = tempfile.mkstemp(suffix=".txt", prefix="dflens-")
            try:
                os.write(fd, text.encode("utf-8", errors="replace"))
            finally:
                os.close(fd)
            tmp_text_path = Path(tmp_name)
            model_input = tmp_text_path
    else:
        text = _read_prefix(file_path, text_bytes).decode("utf-8", errors="replace")
    try:
        model_analysis, model_entries = _run_model(model_input, model_path, modality="text")
    finally:
        if tmp_text_path is not None:
            tmp_text_path.unlink(missing_ok=True)
    coverage.extend(model_entries)
    result = analyze_text(text, model_analysis=model_analysis, coverage=coverage)
    return _apply_document_metadata(result, doc_metadata)


AUDIO_NEXT_CHECKS = [
    "원본 녹음이나 통화 원본을 확보하세요.",
    "동일 화자의 다른 샘플과 음향 특성을 비교하세요.",
    "업로드 맥락과 파일 메타데이터를 함께 검토하세요.",
]
VIDEO_NEXT_CHECKS = [
    "원본 촬영 파일(인카메라 파일)이나 원 스트림을 확보하세요.",
    "프레임별 이미지 탐지 점수와 음성 트랙 분석을 함께 검토하세요.",
    "C2PA/출처 기록이 있는 영상인지 확인하세요.",
]
IMAGE_NEXT_CHECKS = [
    "원본 파일을 확보해 메타데이터를 확인하세요.",
    "역이미지 검색이나 원본 촬영본을 비교하세요.",
    "게시 계정의 반복 패턴과 업로드 맥락을 함께 보세요.",
]
TEXT_NEXT_CHECKS = [
    "작성자의 초안이나 편집 이력을 확인하세요.",
    "짧은 문단보다 전체 글의 맥락을 함께 보세요.",
    "특정 AI 도구명이 직접 언급되었는지 확인하세요.",
]


def compare_files(file_a: Path | str, file_b: Path | str, *, ecapa_revision: str | None = None) -> dict[str, object]:
    """Two-file comparison dispatch: speaker distance for audio pairs,
    stylometry for text/document pairs. ``ecapa_revision`` pins the ECAPA
    speaker model's hub commit (G10; else $DEEPFAKE_LENS_ECAPA_REVISION)."""
    from .audio import compare_speakers
    from .text_advanced import compare_texts

    path_a, path_b = Path(file_a), Path(file_b)
    text_exts = SUPPORTED_TEXT_EXTENSIONS | SUPPORTED_DOCUMENT_EXTENSIONS | {".rst", ".log"}
    ext_a, ext_b = path_a.suffix.lower(), path_b.suffix.lower()
    if ext_a in SUPPORTED_AUDIO_EXTENSIONS and ext_b in SUPPORTED_AUDIO_EXTENSIONS:
        result = compare_speakers(path_a, path_b, ecapa_revision_value=ecapa_revision)
        return {"kind": "speaker", "score": result.same_speaker_score, "band": result.band, "verdict": result.verdict, "distance": result.distance, "method": result.method, "limitations": result.limitations}
    if ext_a in text_exts and ext_b in text_exts:
        def _text(path: Path) -> str | None:
            try:
                if path.suffix.lower() in SUPPORTED_DOCUMENT_EXTENSIONS:
                    text, _ = extract_document_text(path)
                    return text or None
                return _read_prefix(path, 4 * 1024 * 1024).decode("utf-8", errors="replace")
            except OSError:
                return None
        text_a, text_b = _text(path_a), _text(path_b)
        if text_a is None or text_b is None:
            return {"error": "한쪽 파일의 텍스트 추출에 실패했습니다."}
        result = compare_texts(text_a, text_b)
        return {"kind": "stylometry", "score": result.same_author_score, "band": result.band, "verdict": result.verdict, "distance": result.distance, "limitations": result.limitations}
    return {"error": f"지원되는 쌍이 아닙니다 ({ext_a} vs {ext_b}) — 오디오끼리 또는 텍스트/문서끼리 비교하세요."}




_DOCUMENT_AI_HINT = re.compile(
    r"chatgpt|openai|claude|anthropic|gemini|copilot|midjourney|stable.?diffusion|dall.?e|gamma|jasper|writesonic",
    re.I,
)


def _apply_document_metadata(result: ClassificationResult, doc_metadata: dict[str, str]) -> ClassificationResult:
    """Fold office-document provenance metadata into the text result.

    Extraction notes (unavailable/failed extractors) become limitations;
    creator/producer/application fields become source-guess reasons, and an
    AI tool name in them becomes deterministic (moderate) evidence — still
    reference-grade, because text results never conclude (G24).
    """
    if not doc_metadata:
        return result
    limitations = list(result.limitations)
    extractor = doc_metadata.get("extractor", "")
    if extractor.startswith("unavailable:") or extractor.startswith("failed:") or extractor.startswith("skipped:"):
        limitations.append(f"문서 텍스트 추출 불가 ({extractor}) — 텍스트 신호는 추출된 부분만 반영합니다.")
    reasons = list(result.source_guess.reasons)
    label = result.source_guess.label
    confidence = result.source_guess.confidence
    provenance_keys = (
        ("pdf.producer", "PDF 생성기"),
        ("pdf.creator", "PDF 작성 도구"),
        ("pdf.author", "PDF 작성자"),
        ("docx.creator", "문서 작성자"),
        ("docx.last_modified_by", "최종 수정자"),
        ("docx.application", "작성 애플리케이션"),
    )
    hints = [(label_text, doc_metadata[key]) for key, label_text in provenance_keys if doc_metadata.get(key)]
    for label_text, value in hints:
        reasons.append(f"{label_text}: {value}")
    ai_hit = next((v for _, v in hints if _DOCUMENT_AI_HINT.search(v)), None)
    if confidence == SourceConfidence.UNKNOWN:
        if ai_hit:
            label = "AI 도구 생성 메타데이터 추정"
            confidence = SourceConfidence.MEDIUM
            reasons.append(f"문서 메타데이터에 AI 도구명이 기록되어 있습니다: {ai_hit}")
        elif hints:
            reasons.append("문서 메타데이터에서 작성 도구 단서가 발견되었습니다.")
    # Preserve the extracted provenance fields verbatim so API/GUI/report
    # consumers can show the raw metadata record, not just its folded
    # source-guess interpretation.
    preserved = {key: value for key, value in doc_metadata.items() if value and key != "extractor"}
    extra = document_metadata_evidence(ai_hit)
    rebuilt = build_classification_result(
        subject="글",
        evidence=[*result.evidence, *extra],
        coverage=result.coverage,
        grade=result.grade,
        source_guess=SourceGuess(label, confidence, reasons),
        limitations=limitations,
        next_checks=result.next_checks,
        reference_signals=result.reference_signals,
        model_analysis=result.model_analysis,
    )
    return replace(rebuilt, document_metadata=preserved or result.document_metadata)


TEXT_LEXICAL_LIMITATION = "어휘·문체 신호(키워드, 연결 문구, 문장 통계)는 사람이 쓴 글에도 나타나며 결론을 바꾸지 않습니다."
# Below this many characters, style statistics are unstable (sentence-length
# variance and shingle repetition need several sentences) — disclosed only.
SHORT_TEXT_CHARS = 240
# Display thresholds for which lexical item to list (legacy heuristics,
# unmeasured; they only choose a title — lexical items never decide).
TEMPLATE_PHRASES_MANY = 4
TEMPLATE_PHRASES_SOME = 2
LIST_ITEMS_MANY = 6
LIST_ITEMS_SOME = 3
# Weight carried on the intermediate EvidenceSignal before it becomes a
# lexical EvidenceItem; deliberately 0 — a keyword hit is not points.
LEXICAL_SIGNAL_WEIGHT = 0


def analyze_text(
    text: str,
    *,
    model_analysis: ExternalModelAnalysis | None = None,
    coverage: list[CoverageEntry] | None = None,
) -> ClassificationResult:
    """Text screening — always reference grade with the legal limitation
    first (G24). Phrase/style signals are lexical evidence only (G4)."""
    coverage = list(coverage) if coverage is not None else _default_model_coverage(model_analysis)
    trimmed = text.strip()
    if not trimmed:
        return build_classification_result(
            subject="글",
            evidence=[],
            coverage=[*coverage, skipped("text_lexical", "분석할 원문이 비어 있음")],
            grade=Grade.REFERENCE,
            source_guess=SourceGuess.unknown(),
            limitations=[TEXT_LEGAL_LIMITATION, "분석할 원문이 비어 있습니다."],
            next_checks=["분석할 원문을 더 길게 확보하세요."],
            model_analysis=model_analysis,
        )

    normalized = re.sub(r"\s+", " ", trimmed.lower())
    lines = [line.strip() for line in trimmed.splitlines() if line.strip()]
    sentences = [part.strip() for part in re.split(r"[.!?。！？\n]+", trimmed) if len(part.strip()) >= 8]
    words = re.findall(r"[\w']+", normalized, flags=re.UNICODE)
    # Phrase and structure hits are lexical evidence only (G4): they are
    # listed for the examiner and can never move the verdict. The legacy
    # point weights (+35 for one "language model" mention, …) are gone;
    # LEXICAL_SIGNAL_WEIGHT marks "not a score".
    signals: list[EvidenceSignal] = []

    identity_hits = sum(1 for phrase in AI_IDENTITY_PHRASES if phrase in normalized)
    if identity_hits:
        signals.append(EvidenceSignal(
            "AI 자기표현 문구",
            f"AI 또는 언어 모델을 언급하는 표현이 {identity_hits}개 있습니다. AI에 대해 쓴 사람의 글에도 흔히 나타납니다.",
            LEXICAL_SIGNAL_WEIGHT,
        ))

    phrase_hits = sum(1 for phrase in SYNTHETIC_WRITING_PHRASES if phrase in normalized)
    if phrase_hits >= TEMPLATE_PHRASES_MANY:
        signals.append(EvidenceSignal("템플릿형 문장 전개", "요약/균형/결론형 연결 문구가 반복됩니다.", LEXICAL_SIGNAL_WEIGHT))
    elif phrase_hits >= TEMPLATE_PHRASES_SOME:
        signals.append(EvidenceSignal("정형화된 연결 문구", f"자동 생성 글에서 자주 보이는 연결 표현이 {phrase_hits}개 보입니다.", LEXICAL_SIGNAL_WEIGHT))

    list_markers = sum(1 for line in lines if re.match(r"^(\d+[\).]|[-*•])\s+.+", line))
    if list_markers >= LIST_ITEMS_MANY:
        signals.append(EvidenceSignal("과도하게 균일한 목록 구조", f"목록 항목이 {list_markers}개 이어집니다.", LEXICAL_SIGNAL_WEIGHT))
    elif list_markers >= LIST_ITEMS_SOME:
        signals.append(EvidenceSignal("목록 중심 구성", "번호/불릿 구조가 두드러집니다.", LEXICAL_SIGNAL_WEIGHT))

    for optional in (
        _sentence_uniformity_signal(sentences),
        _repeated_shingle_signal(words),
        _generic_text_signal(normalized, words),
    ):
        if optional:
            signals.append(optional)
    signals.extend(_frontier_llm_fingerprints(trimmed, normalized, sentences, words))

    evidence = lexical_evidence(signals)
    model_item = model_evidence(model_analysis)
    if model_item is not None:
        evidence.append(model_item)

    source_guess = guess_text_source(normalized, identity_hits)
    limitations = [TEXT_LEGAL_LIMITATION, TEXT_LEXICAL_LIMITATION]
    tech_density = _technical_document_density(trimmed, lines)
    if tech_density >= 0.4:
        limitations.append(
            f"기술문서 구조 밀도가 높습니다({tech_density:.0%}) — 코드/표/헤더가 목록·균일성·퍼플렉시티 신호를 부풀리므로 문체 기반 판별의 신뢰도가 낮습니다. 측정된 실패 영역입니다."
        )
    if len(trimmed) < SHORT_TEXT_CHARS:
        limitations.append("짧은 글은 문체 통계가 불안정합니다 — 어휘 신호가 있어도 참고 정보로만 표시합니다.")
    if len(sentences) < 4:
        limitations.append("문장 수가 적어 반복도와 문장 길이 신호가 제한적입니다.")
    if model_analysis:
        limitations.extend(model_analysis.limitations)

    return build_classification_result(
        subject="글",
        evidence=evidence,
        coverage=[*coverage, CoverageEntry("text_lexical", CoverageStatus.RAN)],
        grade=Grade.REFERENCE,
        source_guess=source_guess,
        limitations=limitations,
        next_checks=TEXT_NEXT_CHECKS,
        model_analysis=model_analysis,
    )


def _default_model_coverage(model_analysis: ExternalModelAnalysis | None) -> list[CoverageEntry]:
    """Coverage for direct (non-file) calls that pass a model result in."""
    if model_analysis is None:
        return [skipped("external_model", "모델 프로필 미지정")]
    return model_coverage(model_analysis)


def analyze_image_metadata(
    metadata: dict[str, str],
    *,
    dimensions: tuple[int, int] | None = None,
    pixel_analysis: PixelAnalysis | None = None,
    model_analysis: ExternalModelAnalysis | None = None,
    c2pa_validation: dict[str, object] | None = None,
    coverage: list[CoverageEntry] | None = None,
    extra_evidence: list[EvidenceItem] | None = None,
    extra_limitations: list[str] | None = None,
    image_class: ImageClass | None = None,
    metadata_read_error: str | None = None,
    c2pa_unknown: bool = False,
    image_format: str | None = None,
    jpeg_quality: float | None = None,
    extra_reference: list[EvidenceSignal] | None = None,
) -> ClassificationResult:
    """Image result from already-collected analyzer outputs.

    ``coverage`` is supplied by ``analyze_file``; direct callers (tests,
    benchmarks) get a coverage record synthesized from the arguments.
    ``image_class`` (the photo/non-photo gate) adds its neutral evidence
    item and, for a non-photo, the "사진 아님 — 생성 탐지 비적용" notice.
    ``metadata_read_error`` / ``c2pa_unknown`` suppress the "메타데이터
    부재" item when the metadata or the C2PA read did not complete (D16);
    ``image_format`` and ``jpeg_quality`` feed the camera-EXIF rule (D7).
    """
    c2pa_present = bool(c2pa_validation and c2pa_validation.get("present"))
    evidence = [
        *image_metadata_evidence(
            metadata, dimensions,
            metadata_read_error=metadata_read_error,
            c2pa_present=c2pa_present or c2pa_unknown,
            image_format=image_format,
            jpeg_quality=jpeg_quality,
        ),
        *c2pa_evidence(c2pa_validation),
        *image_class_evidence(image_class),
    ]
    model_item = model_evidence(model_analysis)
    if model_item is not None:
        evidence.append(model_item)
    evidence.extend(extra_evidence or [])

    reference: list[EvidenceSignal] = list(extra_reference or [])
    if pixel_analysis and pixel_analysis.available:
        top = " / ".join(pixel_analysis.signals[:2]) or "픽셀 전문가 신호 요약 없음"
        reference.append(reference_signal(
            "픽셀 앙상블(참고, 미측정)",
            f"{pixel_analysis.model} 원점수 {pixel_analysis.raw_score}/100 ({pixel_analysis.reference_confidence}, 미측정 — 결론에 참여하지 않습니다). {top}",
            pixel_analysis.raw_score,
        ))

    if coverage is None:
        coverage = [CoverageEntry("metadata", CoverageStatus.RAN)]
        if pixel_analysis is not None:
            coverage.append(CoverageEntry("pixel", CoverageStatus.RAN) if pixel_analysis.available else skipped("pixel", "픽셀 분석 불가"))
        coverage.extend(_default_model_coverage(model_analysis))

    limitations = ["결론은 메타데이터·출처 기록 같은 결정적 근거로만 내립니다. 통계·휴리스틱 신호는 보정 전까지 결론에 참여하지 않습니다."]
    subject = "이미지"
    if image_class is not None and not image_class.is_photo:
        notice = NON_PHOTO_NOTICE if image_class.kind != "too_small" else OUT_OF_RANGE_NOTICE
        subject = f"이미지({notice})"
        limitations.insert(0, (
            f"{notice}: 이미지 유형 {image_class.label}({image_class.kind}). 픽셀·모델·얼굴 검사를 적용하지 않았습니다 — "
            "생성 탐지기는 사진에서만 측정 의미가 있으며, 메타데이터·C2PA 검사만 수행했습니다."
        ))
    if not dimensions:
        limitations.append("이미지 크기를 파일 헤더에서 확인하지 못했습니다.")
    if pixel_analysis:
        limitations.extend(pixel_analysis.limitations)
    if model_analysis:
        limitations.extend(model_analysis.limitations)
    limitations.extend(extra_limitations or [])

    return build_classification_result(
        subject=subject,
        evidence=evidence,
        coverage=coverage,
        source_guess=guess_image_source(metadata),
        limitations=limitations,
        next_checks=IMAGE_NEXT_CHECKS,
        reference_signals=reference,
        pixel_analysis=pixel_analysis,
        model_analysis=model_analysis,
    )


# ---------------------------------------------------------------------------
# Result assembly (contract v2)
# ---------------------------------------------------------------------------

_KIND_ORDER = {EvidenceKind.DETERMINISTIC: 0, EvidenceKind.STATISTICAL: 1, EvidenceKind.LEXICAL: 2}
_STRENGTH_ORDER = {EvidenceStrength.STRONG: 0, EvidenceStrength.MODERATE: 1, EvidenceStrength.WEAK: 2}
_DIRECTION_ORDER = {EvidenceDirection.SYNTHETIC: 0, EvidenceDirection.AUTHENTIC: 1, EvidenceDirection.NEUTRAL: 2}


def _ordered_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    return sorted(items, key=lambda i: (_KIND_ORDER[i.kind], _STRENGTH_ORDER[i.strength], _DIRECTION_ORDER[i.direction]))


def _verdict_text(
    subject: str,
    verdict: Verdict,
    grade: Grade,
    evidence: list[EvidenceItem],
    failed_entries: list[CoverageEntry],
) -> str:
    if grade == Grade.REFERENCE:
        lexical = sum(1 for item in evidence if item.kind == EvidenceKind.LEXICAL)
        note = f" (어휘적 신호 {lexical}건은 참고 정보)" if lexical else ""
        failure = f" 검사 실패: {', '.join(check_label(entry.check) for entry in failed_entries)}." if failed_entries else ""
        return f"참고: 근거 부족 — {subject}의 생성 여부는 결론을 내리지 않습니다{note}.{failure}"
    if verdict == Verdict.MANIPULATION_EVIDENCE:
        basis = next(
            (
                item for item in evidence
                if (item.kind == EvidenceKind.DETERMINISTIC and item.direction == EvidenceDirection.SYNTHETIC and item.strength == EvidenceStrength.STRONG)
                or (item.kind == EvidenceKind.STATISTICAL and item.is_calibrated and item.direction == EvidenceDirection.SYNTHETIC)
            ),
            None,
        )
        return f"{subject}: {VERDICT_LABELS[verdict]} — {basis.title if basis else '근거 목록 참조'}."
    if verdict == Verdict.AUTHENTICITY_EVIDENCE:
        basis = next((item for item in evidence if item.direction == EvidenceDirection.AUTHENTIC and item.strength == EvidenceStrength.STRONG), None)
        return f"{subject}: {VERDICT_LABELS[verdict]} — {basis.title if basis else '근거 목록 참조'}."
    if failed_entries:
        names = ", ".join(check_label(entry.check) for entry in failed_entries)
        return f"{subject}: 판단 불가 — 검사 실패({names})로 결론을 내리지 않습니다."
    return f"{subject}: 판단 불가 — 결론을 뒷받침할 결정적 근거나 보정된 모델 근거가 없습니다. 원본이라는 뜻이 아닙니다."


def build_classification_result(
    *,
    subject: str,
    evidence: list[EvidenceItem],
    coverage: list[CoverageEntry],
    source_guess: SourceGuess,
    limitations: list[str],
    next_checks: list[str],
    grade: Grade = Grade.EVIDENCE,
    reference_signals: list[EvidenceSignal] | None = None,
    pixel_analysis: PixelAnalysis | None = None,
    model_analysis: ExternalModelAnalysis | None = None,
    av_audio: dict | None = None,
    document_metadata: dict | None = None,
    probability_thresholds: dict[str, float] | None = None,
) -> ClassificationResult:
    """The only constructor new code uses for a ClassificationResult.

    Verdict comes from ``decision.decide``; every legacy field is derived:
    band from the verdict (never MEDIUM), score from a calibrated
    probability only (else 0), signals from the evidence list.
    """
    ordered = _ordered_evidence(list(evidence))
    entries = list(coverage)
    verdict_code = decide(ordered, entries, grade, probability_thresholds)
    calibrated = [item for item in ordered if item.kind == EvidenceKind.STATISTICAL and item.is_calibrated]
    top = max(calibrated, key=lambda item: item.probability or 0.0) if calibrated else None
    probability = top.probability if top else None
    score = int(round(probability * 100)) if probability is not None else 0
    band = band_for_verdict(verdict_code)
    failed_entries = [entry for entry in entries if entry.status == CoverageStatus.FAILED]
    all_limitations = list(limitations)
    if grade == Grade.REFERENCE:
        all_limitations = [TEXT_LEGAL_LIMITATION, *(lim for lim in all_limitations if lim != TEXT_LEGAL_LIMITATION)]
    for entry in failed_entries:
        all_limitations.append(f"검사 실패 — {entry.describe()}")
    legacy_signals = sorted((item.legacy_signal() for item in ordered), key=lambda signal: signal.weight, reverse=True)
    return ClassificationResult(
        score=score,
        band=band,
        band_label=VERDICT_LABELS[verdict_code],
        verdict=_verdict_text(subject, verdict_code, grade, ordered, failed_entries),
        signals=legacy_signals,
        limitations=all_limitations,
        source_guess=source_guess,
        next_checks=list(next_checks),
        pixel_analysis=pixel_analysis,
        model_analysis=model_analysis,
        ai_score=score,
        source_attribution_label=source_guess.label,
        av_audio=av_audio,
        document_metadata=document_metadata,
        verdict_code=verdict_code,
        grade=grade,
        evidence=ordered,
        coverage=entries,
        probability=probability,
        probability_ci=top.probability_ci if top else None,
        score_is_calibrated=probability is not None,
        reference_signals=list(reference_signals or []),
    )


# R-IN-1 (QA-IN-1): an analysis never writes into the evidence folder. Heatmaps
# without an explicit --heatmap-dir go to a tool-owned output root (one
# subfolder per scanned folder), not to ``<folder>/deepfake_lens_heatmaps``.
HEATMAP_DIR_ENV = "DEEPFAKE_LENS_HEATMAP_DIR"
HEATMAP_SUFFIX = ".heatmap.png"
# Hex chars of sha256(resolved folder) naming the per-folder subdirectory:
# 64 bits keeps distinct case folders apart without unwieldy paths.
HEATMAP_FOLDER_KEY_CHARS = 16


def default_heatmap_root() -> Path:
    """Tool-owned heatmap output root: ``$DEEPFAKE_LENS_HEATMAP_DIR`` or
    ``~/.cache/deepfake-lens/heatmaps``. Never inside the evidence."""
    env = os.environ.get(HEATMAP_DIR_ENV)
    return Path(env).expanduser() if env else Path.home() / ".cache" / "deepfake-lens" / "heatmaps"


def is_default_heatmap_output(path: Path | str) -> bool:
    """True for a ``*.heatmap.png`` under the tool-owned heatmap root —
    the web server may serve these without a read root (they are tool
    output, not caller-chosen files)."""
    try:
        resolved = Path(path).expanduser().resolve()
        resolved.relative_to(default_heatmap_root().expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return False
    return resolved.name.endswith(HEATMAP_SUFFIX)


def _heatmap_path_for(path: Path, *, root: Path | None, heatmap_dir: Path | None) -> Path:
    if heatmap_dir is not None:
        output_root = heatmap_dir
    else:
        folder = str(Path(root or path.parent).expanduser().resolve())
        key = hashlib.sha256(folder.encode("utf-8")).hexdigest()[:HEATMAP_FOLDER_KEY_CHARS]
        output_root = default_heatmap_root() / key
    try:
        relative = path.relative_to(root) if root else Path(path.name)
    except ValueError:
        relative = Path(path.name)
    safe_parts = [part.replace("/", "_").replace("\\", "_") for part in relative.parts]
    output_name = "__".join(safe_parts) + HEATMAP_SUFFIX
    return output_root / output_name


def sort_items(items: list[ScanItem]) -> list[ScanItem]:
    return sorted(items, key=lambda item: (_sort_bucket(item), -(item.result.score if item.result else -1), item.path.lower()))


def summarize(items: list[ScanItem], *, capped: bool, cached: int = 0) -> BatchScanSummary:
    analyzed = [item for item in items if item.status == "analyzed" and item.result]

    def count(verdict: Verdict) -> int:
        return sum(1 for item in analyzed if item.result and item.result.verdict_code == verdict)

    return BatchScanSummary(
        total=len(items),
        analyzed=len(analyzed),
        high=sum(1 for item in analyzed if item.result and item.result.band == RiskBand.HIGH),
        medium=sum(1 for item in analyzed if item.result and item.result.band == RiskBand.MEDIUM),
        unknown=sum(1 for item in analyzed if item.result and item.result.band == RiskBand.UNKNOWN),
        low=sum(1 for item in analyzed if item.result and item.result.band == RiskBand.LOW),
        unsupported_or_failed=sum(1 for item in items if item.status not in {"analyzed", "duplicate", "skipped"}),
        capped=capped,
        cached=cached,
        duplicates=sum(1 for item in items if item.status == "duplicate"),
        skipped=sum(1 for item in items if item.status == "skipped"),
        external_model_active=sum(1 for item in analyzed if item.result and item.result.model_analysis and item.result.model_analysis.available),
        manipulation_evidence=count(Verdict.MANIPULATION_EVIDENCE),
        authenticity_evidence=count(Verdict.AUTHENTICITY_EVIDENCE),
        undetermined=count(Verdict.UNDETERMINED),
        checks_failed=sum(
            1 for item in analyzed
            if item.result and any(entry.status == CoverageStatus.FAILED for entry in item.result.coverage)
        ),
    )


def _thresholds_json(thresholds: object | None) -> dict[str, object]:
    """Record which decision thresholds produced this scan.

    Builtin literals are unmeasured — every scan must admit that. A loaded
    ThresholdProfile additionally reports its sample count and provisional
    flag so downstream consumers can tell measured cutoffs from defaults.
    """
    from .calibration import threshold_display_label

    if thresholds is None:
        return {"source": "builtin_defaults", "provisional": True, "measured": False}
    to_json = getattr(thresholds, "to_json", None)
    payload = to_json() if callable(to_json) else {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "source": "threshold_profile",
        "version": str(payload.get("version", "")),
        "provisional": bool(payload.get("provisional", True)),
        "samples": int(payload.get("samples", 0) or 0),
        "dataset_fingerprint": str(payload.get("dataset_fingerprint", "")),
        "measured_at": str(payload.get("measured_at", "")),
        "measured": not bool(payload.get("provisional", True)),
        # G28: cutoffs fitted on the rows they were evaluated on are shown
        # as "in-sample(참고)" (calibration.threshold_display_label).
        "in_sample": bool(payload.get("in_sample", False)),
        "note": str(payload.get("note", "") or ""),
        "label": threshold_display_label(payload),
    }


def _cache_provenance(thresholds: object | None) -> str:
    """Provenance string that invalidates cached verdicts on drift."""
    from .vendor_weights import weights_coverage

    try:
        cov = weights_coverage()
    except Exception:
        # Unreadable weights state must not abort the scan, but it is logged
        # and recorded as "unknown" so cached verdicts are not replayed.
        logger.exception("weights coverage unavailable for cache provenance")
        cov = {"weights_available": "unknown", "weights_total": "unknown"}
    tj = _thresholds_json(thresholds)
    return "|".join(
        [
            f"tool:{TOOL_VERSION}",
            f"schema:{SCAN_JSON_SCHEMA_VERSION}",
            f"thr:{tj.get('source', '')}:{tj.get('version', '')}:{tj.get('dataset_fingerprint', '')}",
            f"w:{cov.get('weights_available', 0)}/{cov.get('weights_total', 0)}",
        ]
    )


def scan_to_json(summary: BatchScanSummary, items: list[ScanItem], *, thresholds: object | None = None, models_dir: object | None = None) -> dict[str, object]:
    from .vendor_weights import weights_coverage

    return {
        "schema_version": SCAN_JSON_SCHEMA_VERSION,
        "summary": summary.to_json(),
        # Weight coverage is surfaced per-scan so a heuristic-only run can
        # never masquerade as a full neural pipeline in downstream reports.
        "coverage": weights_coverage(models_dir),
        # Threshold provenance: unmeasured builtin defaults vs a profile
        # fit on labeled data (with its corpus fingerprint and sample n).
        "thresholds": _thresholds_json(thresholds),
        "items": [item.to_json() for item in items],
    }


def scan_to_json_text(summary: BatchScanSummary, items: list[ScanItem], *, thresholds: object | None = None, models_dir: object | None = None) -> str:
    return json.dumps(scan_to_json(summary, items, thresholds=thresholds, models_dir=models_dir), ensure_ascii=False, indent=2)



def _sort_bucket(item: ScanItem) -> int:
    if item.status != "analyzed" or not item.result:
        return 4
    return {
        RiskBand.HIGH: 0,
        RiskBand.MEDIUM: 1,
        RiskBand.UNKNOWN: 2,
        RiskBand.LOW: 3,
    }[item.result.band]
