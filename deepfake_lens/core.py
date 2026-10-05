from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .archives import archive_format, extract_archive, is_archive
from .audio import SUPPORTED_AUDIO_EXTENSIONS, AudioAnalysis, analyze_audio
from .video_analysis import SUPPORTED_VIDEO_EXTENSIONS, VideoTemporalAnalysis, analyze_video_temporal
from .documents import SUPPORTED_DOCUMENT_EXTENSIONS, extract_document_text
from .model_adapter import ExternalModelAnalysis, analyze_external_model
from .pixel import DEFAULT_PIXEL_MAX_SIDE, PixelAnalysis, analyze_image_pixels
from .pixel import PixelExpertResult
from .png import read_png_dimensions, read_png_metadata
from .image_metadata import (  # noqa: F401
    DEFAULT_METADATA_BYTES,
    guess_image_source,
    read_image_metadata,
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
SCAN_JSON_SCHEMA_VERSION = 1
TOOL_VERSION = "0.1.0"  # kept in sync with pyproject version
DEFAULT_MAX_FILES = 1000
DEFAULT_TEXT_BYTES = 64 * 1024


# Result types and scan-cache/serialization helpers live in leaf modules;
# re-exported here so existing ``from .core import ...`` call sites keep
# working unchanged.
from .result_types import (  # noqa: F401
    RISK_LABELS,
    SOURCE_CONFIDENCE_LABELS,
    BatchScanSummary,
    ClassificationResult,
    EvidenceSignal,
    RiskBand,
    ScanItem,
    SourceConfidence,
    SourceGuess,
)
from .serialization import (  # noqa: F401
    _classification_result_from_json,
    _model_analysis_from_json,
    _pixel_analysis_from_json,
    _scan_item_from_json,
)
from .scan_cache import (  # noqa: F401
    _cache_key,
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
    for path in _iter_files(
        root, recursive=recursive, allow_symlinks=allow_symlinks,
        on_error=lambda p, e: iter_errors.append((p, e)),
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
                archive_meta[rel] = {
                    "path": path, "fmt": archive_format(path),
                    "skipped": 0, "warnings": [f"압축 해제 실패: {exc}"],
                }
                archive_members[rel] = []
                continue
            archive_meta[rel] = {
                "path": path, "fmt": archive_format(path),
                "skipped": extraction.skipped, "warnings": extraction.warnings,
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
        if iter_errors:
            for err_path, exc in iter_errors:
                items.append(ScanItem(
                    _display_path(err_path, root=root), err_path.name,
                    "unknown", "failed", 0,
                    error=f"directory unreadable: {exc}",
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
) -> ScanItem:
    """Container row for an expanded archive; aggregates member bands."""
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    analyzed_members = [i for i in (member_items or []) if i.result and i.result.band != RiskBand.UNKNOWN]
    worst = RiskBand.UNKNOWN
    for item in analyzed_members:
        band = item.result.band  # type: ignore[union-attr]
        order = {RiskBand.LOW: 0, RiskBand.MEDIUM: 1, RiskBand.HIGH: 2}
        if order.get(band, -1) > order.get(worst, -1):
            worst = band
    # A container with zero analyzable members is NOT a clean scan —
    # corrupt/empty/all-unsupported archives stay UNKNOWN, never LOW.
    band = worst
    score = max((item.result.score for item in member_items or [] if item.result), default=0)
    signals = [EvidenceSignal("압축 컨테이너", f"{fmt or 'archive'} 형식 — 구성 파일 {members}개 개별 분석" + (f", 스킵 {skipped}개" if skipped else ""), 0)]
    limitations = list(warnings)
    limitations.append("컨테이너 행은 구성 파일 결과의 요약입니다. '아카이브::경로' 형태의 개별 결과를 확인하세요.")
    verdict = (
        f"압축 해제됨 — 구성 파일 {members}개 분석, {skipped}개 스킵"
        if analyzed_members
        else "아카이브에서 분석 가능한 구성 파일이 없습니다 — 판정 불가"
    )
    return ScanItem(
        # "expanded" (not "analyzed") keeps the roll-up row out of the band
        # counts — members already carry their own verdicts, and counting
        # the container too would double every archive member's tally.
        rel, name, "archive", "expanded" if analyzed_members else "unknown", size,
        ClassificationResult(
            score=score,
            band=band,
            band_label=RISK_LABELS.get(band, band.value),
            verdict=verdict,
            signals=signals,
            limitations=limitations,
            source_guess=SourceGuess.unknown("압축 컨테이너에는 출처 추정이 적용되지 않습니다."),
            next_checks=["구성 파일 중 고위험 항목부터 검토하세요."],
        ),
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
    duplicates = _duplicate_map(duplicates_paths, root=root, max_file_bytes=max_file_bytes, hash_db_path=hash_db_path) if dedupe or hash_db_path else {}
    cache = _load_scan_cache(cache_path)
    cache_items = cache.setdefault("items", {}) if cache is not None else {}
    # Cache keys embed analysis provenance so a stored verdict computed
    # under different thresholds or model coverage is never replayed as
    # if it were produced by the current configuration.
    cache_provenance = _cache_provenance(thresholds) if cache is not None else ""

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
            return ScanItem(display_path, path.name, "duplicate", "duplicate", size, error="duplicate content", duplicate_of=duplicates[path]), None, False
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
            )
            cached = cache_items.get(key) if isinstance(cache_items, dict) else None
            if isinstance(cached, dict):
                try:
                    return _scan_item_from_json(cached), key, True
                except (ValueError, TypeError, KeyError):
                    pass  # corrupt entry — fall through and re-analyze
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
            item = ScanItem(
                display or _display_path(path, root=root),
                path.name, "unknown", "failed", 0,
                error=f"analysis error: {type(exc).__name__}: {exc}",
            )
            return item, key, False
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
        # An unexpanded container is "unknown" — never a clean LOW verdict.
        return ScanItem(
            display_path, item_name, "archive", "analyzed", size,
            ClassificationResult(
                score=0,
                band=RiskBand.UNKNOWN,
                band_label="판단 유보(컨테이너)",
                verdict=f"{archive_format(file_path)} 압축 파일 — 내용물 미분석 상태로 판단을 유보합니다. 내부 파일은 폴더 스캔 또는 업로드 경로에서 개별 분석됩니다.",
                signals=[EvidenceSignal("압축 컨테이너", "내용물 분석은 스캔 경로에서 수행됩니다", 0)],
                limitations=["단일 파일 분석에서는 압축 내부를 펼치지 않습니다."],
                source_guess=SourceGuess.unknown("압축 컨테이너에는 출처 추정이 적용되지 않습니다."),
                next_checks=["압축 파일이 포함된 폴더를 스캔하거나 업로드하세요."],
            ),
        )

    if extension in SUPPORTED_TEXT_EXTENSIONS or extension in SUPPORTED_DOCUMENT_EXTENSIONS:
        try:
            doc_metadata: dict[str, str] = {}
            model_input = file_path
            tmp_text_path: Path | None = None
            if extension in SUPPORTED_DOCUMENT_EXTENSIONS:
                text, doc_metadata = extract_document_text(file_path)
                # Binary containers (docx/hwp/pdf) must not reach the text
                # members as raw bytes — feed the extracted text instead so
                # PPL/binoculars and the language gate see real prose.
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
                model_analysis = analyze_external_model(model_input, model_path, modality="text")
            finally:
                if tmp_text_path is not None:
                    tmp_text_path.unlink(missing_ok=True)
            result = analyze_text(text, model_analysis=model_analysis)
            result = _apply_document_metadata(result, doc_metadata)
            return ScanItem(display_path, item_name, "text", "analyzed", size, result)
        except OSError as exc:
            return ScanItem(display_path, item_name, "text", "failed", size, error=str(exc))

    if extension in SUPPORTED_IMAGE_EXTENSIONS:
        try:
            metadata, dimensions = read_image_metadata(file_path, metadata_bytes=metadata_bytes)
            pixel_analysis = None
            if pixel_mode != "off":
                pixel_analysis = analyze_image_pixels(
                    file_path,
                    mode=pixel_mode,
                    max_side=pixel_max_side,
                    heatmap_path=_heatmap_path_for(file_path, root=root, heatmap_dir=heatmap_dir) if heatmaps else None,
                )
            model_analysis = analyze_external_model(file_path, model_path)
            result = analyze_image_metadata(metadata, dimensions=dimensions, pixel_analysis=pixel_analysis, model_analysis=model_analysis)
            if deep_signals:
                result = _merge_deep_signals(result, _deep_image_layers(file_path, thresholds))
            return ScanItem(display_path, item_name, "image", "analyzed", size, result)
        except OSError as exc:
            return ScanItem(display_path, item_name, "image", "failed", size, error=str(exc))

    if extension in SUPPORTED_AUDIO_EXTENSIONS:
        try:
            analysis = analyze_audio(file_path, model_path=model_path)
            return ScanItem(display_path, item_name, "audio", "analyzed", size, _audio_result(analysis))
        except OSError as exc:
            return ScanItem(display_path, item_name, "audio", "failed", size, error=str(exc))

    if extension in SUPPORTED_VIDEO_EXTENSIONS:
        try:
            analysis = analyze_video_temporal(file_path, model_path=model_path, analyze_audio_track=True)
            result = _video_result(analysis)
            if deep_signals:
                result = _merge_deep_signals(result, _deep_video_layers(file_path, thresholds))
            return ScanItem(display_path, item_name, "video", "analyzed", size, result)
        except OSError as exc:
            return ScanItem(display_path, item_name, "video", "failed", size, error=str(exc))

    return ScanItem(display_path, item_name, "unsupported", "unsupported", size, error="지원 형식이 아닙니다.")


def _audio_result(analysis: AudioAnalysis) -> ClassificationResult:
    """Adapt an AudioAnalysis into the stable ClassificationResult contract.

    Keeps the heuristic score/band/signals and carries model_analysis through
    so external_model_active accounting works exactly like the image path.
    """
    try:
        band = RiskBand(analysis.band)
    except ValueError:
        band = RiskBand.UNKNOWN
    source_known = bool(analysis.source_guess) and analysis.source_guess != "unknown"
    source_guess = SourceGuess(
        analysis.source_guess or "출처 단서 없음",
        SourceConfidence.LOW if source_known else SourceConfidence.UNKNOWN,
        [analysis.source_guess] if source_known else ["오디오에서 출처를 판단할 단서가 부족합니다."],
    )
    return ClassificationResult(
        score=analysis.score,
        band=band,
        band_label=analysis.band_label,
        verdict=analysis.verdict,
        signals=[EvidenceSignal(signal.title, signal.detail, signal.weight) for signal in analysis.signals],
        limitations=[
            *analysis.limitations,
            *(analysis.model_analysis.limitations if analysis.model_analysis else []),
        ],
        source_guess=source_guess,
        next_checks=["원본 녹음이나 통화 원본을 확보하세요.", "동일 화자의 다른 샘플과 음향 특성을 비교하세요.", "업로드 맥락과 파일 메타데이터를 함께 검토하세요."],
        model_analysis=analysis.model_analysis,
        ai_score=analysis.score,
        source_attribution_label=source_guess.label,
    )


def _deep_image_layers(path: Path, thresholds=None) -> tuple[list[EvidenceSignal], list[str]]:
    """Opt-in deep image layers: face-manipulation and inpainting probes.

    These modules predate the unified scan but were never wired in — each
    degrades to a limitation note on missing deps (mediapipe, cv2) rather
    than failing the item.
    """
    signals: list[EvidenceSignal] = []
    limitations: list[str] = []
    try:
        from .face import analyze_faces
        face = analyze_faces(path)
        if face.face_count > 0:
            signals.append(EvidenceSignal(
                "얼굴 조작 분석",
                f"{face.verdict} (faces={face.face_count}, {face.manipulation_type})",
                min(face.score, 30),
            ))
        limitations.extend(face.limitations[:2])
    except Exception:
        limitations.append("얼굴 분석 레이어를 실행할 수 없습니다(선택 의존성 부재).")
    try:
        from .inpaint import analyze_inpainting
        inpaint = analyze_inpainting(path)
        if inpaint.regions_detected:
            signals.append(EvidenceSignal(
                "인페인팅/부분 변형 탐지",
                f"{inpaint.verdict} (영역 {inpaint.regions_detected}개)",
                min(inpaint.score, 25),
            ))
        limitations.extend(inpaint.limitations[:2])
    except Exception:
        limitations.append("인페인팅 분석 레이어를 실행할 수 없습니다(선택 의존성 부재).")
    try:
        from .faceswap_seam import analyze_faceswap_seam
        seam = analyze_faceswap_seam(path, thresholds=thresholds)
        if seam.signals:
            for sig in seam.signals:
                signals.append(EvidenceSignal(sig.title, sig.detail, min(sig.weight, 30)))
        limitations.extend(seam.limitations[:2])
    except Exception:
        limitations.append("페이스스왑 경계면 분석 레이어를 실행할 수 없습니다(선택 의존성 부재).")
    return signals, limitations


def _deep_video_layers(path: Path, thresholds=None) -> tuple[list[EvidenceSignal], list[str]]:
    """Opt-in deep video layers: rPPG pulse screening + avatar probe."""
    signals: list[EvidenceSignal] = []
    limitations: list[str] = []
    try:
        from .rppg import analyze_rppg
        rppg = analyze_rppg(path)
        signals.append(EvidenceSignal(
            "rPPG 맥박 신호",
            rppg.verdict,
            min(rppg.score, 20),
        ))
        limitations.extend(rppg.limitations[:2])
    except Exception:
        limitations.append("rPPG 맥박 분석을 실행할 수 없습니다(선택 의존성 부재).")
    try:
        from .avatar import analyze_avatar
        avatar = analyze_avatar(path)
        if avatar.score > 0:
            signals.append(EvidenceSignal(
                "아바타/디지털휴먼 탐지",
                avatar.verdict,
                min(avatar.score, 25),
            ))
        limitations.extend(avatar.limitations[:2])
    except Exception:
        limitations.append("아바타 분석 레이어를 실행할 수 없습니다(선택 의존성 부재).")
    try:
        from .lipsync import analyze_lipsync
        lipsync = analyze_lipsync(path)
        if lipsync.available and lipsync.score > 0:
            signals.append(EvidenceSignal(
                "립싱크 일관성",
                lipsync.verdict,
                min(lipsync.score, 30),
            ))
        limitations.extend(lipsync.limitations[:2])
    except Exception:
        limitations.append("립싱크 분석 레이어를 실행할 수 없습니다(선택 의존성 부재).")
    try:
        from .face_track import analyze_face_track
        track = analyze_face_track(path, thresholds=thresholds)
        if track.available and track.score > 0:
            signals.append(EvidenceSignal(
                "얼굴 트랙 시간-일관성",
                track.verdict,
                min(track.score, 35),
            ))
        limitations.extend(track.limitations[:2])
    except Exception:
        limitations.append("얼굴 트랙 분석 레이어를 실행할 수 없습니다(선택 의존성 부재).")
    return signals, limitations


def _merge_deep_signals(
    result: ClassificationResult,
    layers: tuple[list[EvidenceSignal], list[str]],
) -> ClassificationResult:
    """Fold deep-layer signals into a result and rescale score/band.

    Deep signals are capped per-layer so they shift prioritization without
    dominating the base analysis; band is recomputed on the merged total.
    """
    signals, limitations = layers
    if not signals and not limitations:
        return result
    merged = sorted([*result.signals, *signals], key=lambda s: s.weight, reverse=True)
    score = min(100, result.score + sum(s.weight for s in signals))
    if result.band == RiskBand.UNKNOWN:
        band = result.band
    elif score >= 67:
        band = RiskBand.HIGH
    elif score >= 35:
        band = RiskBand.MEDIUM
    else:
        band = RiskBand.LOW
    return replace(
        result,
        score=score,
        band=band,
        band_label=RISK_LABELS[band],
        signals=merged,
        limitations=[*result.limitations, *limitations, "심층 신호는 측정 전(provisional) 가중치입니다."],
        ai_score=score,
    )


def _video_result(analysis: "VideoTemporalAnalysis") -> ClassificationResult:
    """Adapt a VideoTemporalAnalysis into the ClassificationResult contract.

    Same shape as _audio_result: temporal score/band/signals pass through,
    and model_analysis (e.g. the video-frames profile scoring sampled
    frames with an image detector) is carried for external_model_active
    accounting.
    """
    try:
        band = RiskBand(analysis.band)
    except ValueError:
        band = RiskBand.UNKNOWN
    has_detail = analysis.frame_count > 0 and analysis.duration_seconds > 0
    source_guess = SourceGuess(
        "출처 단서 없음",
        SourceConfidence.UNKNOWN,
        [
            "영상 파일의 컨테이너 메타데이터에서 출처 단서를 찾지 못했습니다."
            if has_detail
            else "영상 분석이 불완전해 출처를 판단할 단서가 없습니다."
        ],
    )
    return ClassificationResult(
        score=analysis.score,
        band=band,
        band_label=analysis.band_label,
        verdict=analysis.verdict,
        signals=[EvidenceSignal(signal.title, signal.detail, signal.weight) for signal in analysis.signals],
        limitations=[
            *analysis.limitations,
            *(analysis.model_analysis.limitations if analysis.model_analysis else []),
        ],
        source_guess=source_guess,
        next_checks=[
            "원본 촬영 파일(인카메라 파일)이나 원 스트림을 확보하세요.",
            "프레임별 이미지 탐지 점수와 음성 트랙 분석을 함께 검토하세요.",
            "C2PA/출처 기록이 있는 영상인지 확인하세요.",
        ],
        model_analysis=analysis.model_analysis,
        ai_score=analysis.score,
        source_attribution_label=source_guess.label,
        av_audio=analysis.av_audio,
    )


def compare_files(file_a: Path | str, file_b: Path | str) -> dict[str, object]:
    """Two-file comparison dispatch: speaker distance for audio pairs,
    stylometry for text/document pairs."""
    from .audio import compare_speakers
    from .text_advanced import compare_texts

    path_a, path_b = Path(file_a), Path(file_b)
    text_exts = SUPPORTED_TEXT_EXTENSIONS | SUPPORTED_DOCUMENT_EXTENSIONS | {".rst", ".log"}
    ext_a, ext_b = path_a.suffix.lower(), path_b.suffix.lower()
    if ext_a in SUPPORTED_AUDIO_EXTENSIONS and ext_b in SUPPORTED_AUDIO_EXTENSIONS:
        result = compare_speakers(path_a, path_b)
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


def _apply_document_metadata(result: ClassificationResult, doc_metadata: dict[str, str]) -> ClassificationResult:
    """Fold office-document provenance metadata into the text result.

    Extraction notes (unavailable/failed extractors) become limitations;
    creator/producer/application fields become source-guess reasons — a
    document authored by 'ChatGPT' or produced by an AI export pipeline is
    provenance evidence, not a style signal.
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
    ai_hint = re.compile(r"chatgpt|openai|claude|anthropic|gemini|copilot|midjourney|stable.?diffusion|dall.?e|gamma|jasper|writesonic", re.I)
    if confidence == SourceConfidence.UNKNOWN:
        ai_hit = next((v for _, v in hints if ai_hint.search(v)), None)
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
    return replace(
        result,
        limitations=limitations,
        source_guess=SourceGuess(label, confidence, reasons),
        document_metadata=preserved or result.document_metadata,
    )


def analyze_text(text: str, *, model_analysis: ExternalModelAnalysis | None = None) -> ClassificationResult:
    trimmed = text.strip()
    if not trimmed:
        return ClassificationResult(
            score=0,
            band=RiskBand.UNKNOWN,
            band_label=RISK_LABELS[RiskBand.UNKNOWN],
            verdict="글에서 판단할 단서가 부족합니다.",
            signals=[],
            limitations=["분석할 원문이 비어 있습니다."],
            source_guess=SourceGuess.unknown(),
            next_checks=["분석할 원문을 더 길게 확보하세요."],
        )

    normalized = re.sub(r"\s+", " ", trimmed.lower())
    lines = [line.strip() for line in trimmed.splitlines() if line.strip()]
    sentences = [part.strip() for part in re.split(r"[.!?。！？\n]+", trimmed) if len(part.strip()) >= 8]
    words = re.findall(r"[\w']+", normalized, flags=re.UNICODE)
    signals: list[EvidenceSignal] = []

    identity_hits = sum(1 for phrase in AI_IDENTITY_PHRASES if phrase in normalized)
    if identity_hits:
        signals.append(EvidenceSignal("AI 자기표현 문구", f"AI 또는 언어 모델임을 암시하는 표현이 {identity_hits}개 발견되었습니다.", 35))

    phrase_hits = sum(1 for phrase in SYNTHETIC_WRITING_PHRASES if phrase in normalized)
    if phrase_hits >= 4:
        signals.append(EvidenceSignal("템플릿형 문장 전개", "요약/균형/결론형 연결 문구가 반복됩니다.", 22))
    elif phrase_hits >= 2:
        signals.append(EvidenceSignal("정형화된 연결 문구", f"자동 생성 글에서 자주 보이는 연결 표현이 {phrase_hits}개 보입니다.", 12))

    list_markers = sum(1 for line in lines if re.match(r"^(\d+[\).]|[-*•])\s+.+", line))
    if list_markers >= 6:
        signals.append(EvidenceSignal("과도하게 균일한 목록 구조", f"목록 항목이 {list_markers}개 이어집니다.", 18))
    elif list_markers >= 3:
        signals.append(EvidenceSignal("목록 중심 구성", "번호/불릿 구조가 두드러집니다.", 10))

    sentence_signal = _sentence_uniformity_signal(sentences)
    if sentence_signal:
        signals.append(sentence_signal)
    repeat_signal = _repeated_shingle_signal(words)
    if repeat_signal:
        signals.append(repeat_signal)
    generic_signal = _generic_text_signal(normalized, words)
    if generic_signal:
        signals.append(generic_signal)
    model_signal = _model_evidence_signal(model_analysis)
    if model_signal:
        signals.append(model_signal)
    fingerprint_signals = _frontier_llm_fingerprints(trimmed, normalized, sentences, words)
    signals.extend(fingerprint_signals)

    source_guess = guess_text_source(normalized, identity_hits)
    limitations = ["휴리스틱 기반 선별 결과이며 진위 판단이 아니라 검토 우선순위입니다."]
    tech_density = _technical_document_density(trimmed, lines)
    if tech_density >= 0.4:
        # Code fences/tables/headers inflate list-structure, uniformity,
        # and perplexity signals — devin-style agent docs measured at
        # PPL 61-131 for structural reasons alone. Down-weight the
        # structure-derived signals and disclose the gate.
        signals = [
            EvidenceSignal(s.title, s.detail, max(2, int(s.weight * 0.4)))
            if s.title in {"과도하게 균일한 목록 구조", "목록 중심 구성", "문장 길이 균일성", "낮은 문장 변주"}
            else s
            for s in signals
        ]
        limitations.append(
            f"기술문서 구조 밀도가 높습니다({tech_density:.0%}) — 코드/표/헤더가 목록·균일성·퍼플렉시티 신호를 부풀리므로 문체 기반 판별의 신뢰도가 낮습니다. 측정된 실패 영역입니다."
        )
    if len(trimmed) < 240:
        limitations.append("짧은 글은 문체 통계가 불안정하며, 점수가 66점으로 상한됩니다 — '강한 의심' 판정에는 더 긴 원문이 필요합니다.")
    if len(sentences) < 4:
        limitations.append("문장 수가 적어 반복도와 문장 길이 신호가 제한적입니다.")
    if model_analysis:
        limitations.extend(model_analysis.limitations)

    return _build_result(
        signals,
        subject="글",
        source_guess=source_guess,
        limitations=limitations,
        force_unknown=len(trimmed) < 24 and not signals and source_guess.confidence == SourceConfidence.UNKNOWN and not (model_analysis and model_analysis.available),
        model_analysis=model_analysis,
        score_cap=66 if len(trimmed) < 240 else None,
        score_cap_exempt_titles=frozenset({"AI 자기표현 문구"}) if len(trimmed) < 240 else None,
    )


def analyze_image_metadata(
    metadata: dict[str, str],
    *,
    dimensions: tuple[int, int] | None = None,
    pixel_analysis: PixelAnalysis | None = None,
    model_analysis: ExternalModelAnalysis | None = None,
) -> ClassificationResult:
    signals: list[EvidenceSignal] = []
    source_guess = guess_image_source(metadata)
    if source_guess.confidence in {SourceConfidence.MEDIUM, SourceConfidence.HIGH}:
        signals.append(
            EvidenceSignal(
                "생성 도구 메타데이터",
                source_guess.reasons[0] if source_guess.reasons else "생성 도구 단서가 발견되었습니다.",
                67 if source_guess.confidence == SourceConfidence.HIGH else 36,
            )
        )

    if dimensions:
        width, height = dimensions
        if width == height and width >= 512 and width % 64 == 0:
            signals.append(EvidenceSignal("생성 모델에 흔한 정사각 해상도", f"{width}x{height} 해상도는 생성 이미지 워크플로에서 자주 쓰입니다.", 9))

    pixel_signal = _pixel_evidence_signal(pixel_analysis)
    if pixel_signal:
        signals.append(pixel_signal)
    model_signal = _model_evidence_signal(model_analysis)
    if model_signal:
        signals.append(model_signal)

    limitations = ["기본 분석은 메타데이터와 파일 헤더 중심의 빠른 선별 도구입니다."]
    if not metadata:
        limitations.append("메타데이터가 없거나 읽지 못했습니다. 이는 사람이 만든 파일이라는 뜻이 아닙니다.")
    if not dimensions:
        limitations.append("이미지 크기를 파일 헤더에서 확인하지 못했습니다.")
    if pixel_analysis:
        limitations.extend(pixel_analysis.limitations)
    if model_analysis:
        limitations.extend(model_analysis.limitations)

    return _build_result(
        signals,
        subject="사진",
        source_guess=source_guess,
        limitations=limitations,
        force_unknown=not metadata and not dimensions and not (pixel_analysis and pixel_analysis.available),
        pixel_analysis=pixel_analysis,
        model_analysis=model_analysis,
    )


def _heatmap_path_for(path: Path, *, root: Path | None, heatmap_dir: Path | None) -> Path:
    output_root = heatmap_dir or path.parent / "deepfake_lens_heatmaps"
    try:
        relative = path.relative_to(root) if root else Path(path.name)
    except ValueError:
        relative = Path(path.name)
    safe_parts = [part.replace("/", "_").replace("\\", "_") for part in relative.parts]
    output_name = "__".join(safe_parts) + ".heatmap.png"
    return output_root / output_name


def sort_items(items: list[ScanItem]) -> list[ScanItem]:
    return sorted(items, key=lambda item: (_sort_bucket(item), -(item.result.score if item.result else -1), item.path.lower()))


def summarize(items: list[ScanItem], *, capped: bool, cached: int = 0) -> BatchScanSummary:
    analyzed = [item for item in items if item.status == "analyzed" and item.result]
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
    )


def _thresholds_json(thresholds: object | None) -> dict[str, object]:
    """Record which decision thresholds produced this scan.

    Builtin literals are unmeasured — every scan must admit that. A loaded
    ThresholdProfile additionally reports its sample count and provisional
    flag so downstream consumers can tell measured cutoffs from defaults.
    """
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
    }


def _cache_provenance(thresholds: object | None) -> str:
    """Provenance string that invalidates cached verdicts on drift."""
    from .vendor_weights import weights_coverage

    try:
        cov = weights_coverage()
    except Exception:
        cov = {}
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



def _build_result(
    signals: list[EvidenceSignal],
    *,
    subject: str,
    source_guess: SourceGuess,
    limitations: list[str],
    force_unknown: bool = False,
    pixel_analysis: PixelAnalysis | None = None,
    model_analysis: ExternalModelAnalysis | None = None,
    score_cap: int | None = None,
    score_cap_exempt_titles: frozenset[str] | None = None,
) -> ClassificationResult:
    sorted_signals = sorted(signals, key=lambda signal: signal.weight, reverse=True)
    exempt_titles = score_cap_exempt_titles or frozenset()
    exempt = sum(signal.weight for signal in sorted_signals if signal.title in exempt_titles)
    rest = sum(signal.weight for signal in sorted_signals if signal.title not in exempt_titles)
    if score_cap is not None:
        rest = min(rest, score_cap)
    score = min(100, exempt + rest)
    if force_unknown:
        band = RiskBand.UNKNOWN
    elif score >= 67:
        band = RiskBand.HIGH
    elif score >= 35:
        band = RiskBand.MEDIUM
    else:
        band = RiskBand.LOW

    verdict = {
        RiskBand.UNKNOWN: f"{subject}에서 판단할 단서가 부족합니다.",
        RiskBand.HIGH: f"{subject}에서 의심 신호가 강합니다.",
        RiskBand.MEDIUM: f"{subject}에서 몇 가지 의심 신호가 보여 추가 확인이 필요합니다.",
        RiskBand.LOW: f"{subject}에서 뚜렷한 의심 신호는 적습니다.",
    }[band]
    next_checks = (
        ["원본 파일을 확보해 메타데이터를 확인하세요.", "역이미지 검색이나 원본 촬영본을 비교하세요.", "게시 계정의 반복 패턴과 업로드 맥락을 함께 보세요."]
        if subject == "사진"
        else ["작성자의 초안이나 편집 이력을 확인하세요.", "짧은 문단보다 전체 글의 맥락을 함께 보세요.", "특정 AI 도구명이 직접 언급되었는지 확인하세요."]
    )
    return ClassificationResult(
        score=score,
        band=band,
        band_label=RISK_LABELS[band],
        verdict=verdict,
        signals=sorted_signals,
        limitations=limitations,
        source_guess=source_guess,
        next_checks=next_checks,
        pixel_analysis=pixel_analysis,
        model_analysis=model_analysis,
        ai_score=score,
        source_attribution_label=source_guess.label,
    )


def _pixel_evidence_signal(pixel_analysis: PixelAnalysis | None) -> EvidenceSignal | None:
    if not pixel_analysis or not pixel_analysis.available:
        return None
    if pixel_analysis.score >= 82:
        weight = 61
        title = "픽셀 앙상블 강한 의심"
    elif pixel_analysis.score >= 68:
        weight = 47
        title = "픽셀 앙상블 의심"
    elif pixel_analysis.score >= 48:
        weight = 35
        title = "픽셀 앙상블 약한 의심"
    elif pixel_analysis.score >= 32:
        weight = 18
        title = "픽셀 통계 확인 필요"
    else:
        return None

    top_details = pixel_analysis.signals[:2] or ["일부 픽셀 전문가 모델에서 약한 이상 신호가 있습니다."]
    detail = f"{pixel_analysis.model} score={pixel_analysis.score}, confidence={pixel_analysis.confidence}. " + " / ".join(top_details)
    return EvidenceSignal(title, detail, weight)



def _model_evidence_signal(model_analysis: ExternalModelAnalysis | None) -> EvidenceSignal | None:
    if not model_analysis or not model_analysis.available:
        return None
    if model_analysis.score >= 82:
        weight = 67
        title = "외부 모델 강한 의심"
    elif model_analysis.score >= 65:
        weight = 42
        title = "외부 모델 의심"
    elif model_analysis.score >= 45:
        weight = 24
        title = "외부 모델 약한 의심"
    else:
        return None
    return EvidenceSignal(title, f"{model_analysis.model}: {model_analysis.detail}", weight)


def _sort_bucket(item: ScanItem) -> int:
    if item.status != "analyzed" or not item.result:
        return 4
    return {
        RiskBand.HIGH: 0,
        RiskBand.MEDIUM: 1,
        RiskBand.UNKNOWN: 2,
        RiskBand.LOW: 3,
    }[item.result.band]

