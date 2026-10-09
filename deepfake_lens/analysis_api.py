"""Single analysis entry point for the CLI, the web GUI and the REST API (G7).

Before phase 0 each front end assembled its own ``scan_directory`` /
``analyze_file`` call: the CLI auto-loaded the measured threshold profile,
the GUI passed no thresholds at all, and the FastAPI server ran a different
model set. The same folder therefore produced different verdict provenance
depending on which door it came through (G7).

Every front end now builds an :class:`AnalysisOptions` (``from_cli_args`` for
argparse, ``from_query`` for HTTP query strings) and calls only
:func:`analyze_rows` / :func:`analyze_path` (one file) or :func:`scan_folder`.

B1: one file goes through the folder scanner's own body
(``core.scan_paths`` with the file's folder as root, :func:`scan_file`), so
an archive given to ``forensic``/``classify``/``explain``/``legal-report``/
``evidence-statement <file>`` or ``/api/check`` is expanded exactly as
``scan`` expands it: the same member rows ("archive::inner"), the same
container row (verdict roll-up, coverage, rejected members, limitations)
and the same sha256 values. Thresholds are resolved once per
call by :func:`load_thresholds` — the explicit ``--thresholds`` file, else
``<models_dir>/thresholds.json`` — so a CLI scan and a GUI scan of the same
folder report the same ``thresholds`` provenance and the same verdicts.

HTTP callers can never name an arbitrary path for a model or fusion profile:
``model_path`` / ``fusion_profile`` query values must be a bare file name that
exists inside the server's models directory (:class:`InvalidOption` → 400).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Mapping

from .archives import is_archive
from .core import (
    DEFAULT_MAX_FILES,
    DEFAULT_TEXT_BYTES,
    BatchScanSummary,
    ScanItem,
    ScanProgress,
    _thresholds_json,
    _with_content_sha256,
    analyze_file,
    check_scan_folder,
    scan_directory,
    scan_paths,
    scan_to_json,
    summarize,
)
from .image_metadata import DEFAULT_METADATA_BYTES
from .pixel import DEFAULT_PIXEL_MAX_SIDE, SUPPORTED_PIXEL_MODES
from .vendor_weights import default_models_dir

logger = logging.getLogger(__name__)

# Profile file names the adapter treats as runnable engines (model_adapter
# _model_sources uses the same glob for a directory argument).
PROFILE_GLOB = "*-runtime.json"
THRESHOLDS_FILENAME = "thresholds.json"

# Web request limits (formerly webapp_api.MAX_SCAN_FILES /
# MAX_FILE_BYTES_CEILING; re-exported there). A browser request must not be
# able to start an unbounded scan or lift the per-file size cap.
WEB_DEFAULT_MAX_FILES = 500
WEB_MAX_SCAN_FILES = 2000
WEB_MAX_FILE_BYTES_CEILING = 1024 * 1024 * 1024

_TRUE_VALUES = {"1", "true", "yes", "on"}
# "thresholds not supplied" marker for analyze_path (None means builtin defaults).
_UNSET: Any = object()

ModelPath = Path | list[Path] | None
WarnFn = Callable[[str], None]


class InvalidOption(ValueError):
    """A request option that must be refused (HTTP 400, CLI usage error)."""


@dataclass(frozen=True)
class AnalysisOptions:
    """Everything that changes an analysis result, in one place.

    Mirrors the keyword arguments of ``core.scan_directory`` /
    ``core.analyze_file``; ``models_dir`` selects the engine profiles and
    the default threshold profile, ``model_path`` overrides the engine set.
    """

    pixel_mode: str = "off"
    pixel_max_side: int = DEFAULT_PIXEL_MAX_SIDE
    deep_signals: bool = False
    thresholds_path: Path | None = None
    models_dir: Path | None = None
    model_path: Path | tuple[Path, ...] | None = None
    no_default_engine: bool = False
    fusion_profile: Path | None = None
    max_files: int = DEFAULT_MAX_FILES
    recursive: bool = False
    cache: Path | None = None
    text_bytes: int = DEFAULT_TEXT_BYTES
    metadata_bytes: int = DEFAULT_METADATA_BYTES
    heatmaps: bool = False
    heatmap_dir: Path | None = None
    workers: int = 1
    max_file_bytes: int | None = None
    allow_symlinks: bool = False
    dedupe: bool = False
    hash_db: Path | None = None

    # -- constructors -------------------------------------------------------

    @classmethod
    def from_cli_args(cls, args: Any) -> "AnalysisOptions":
        """Options from an argparse namespace (any subcommand; absent flags default)."""
        defaults = cls()

        def opt(name: str, default: Any) -> Any:
            value = getattr(args, name, None)
            return default if value is None else value

        model_path = getattr(args, "model_path", None)
        if isinstance(model_path, (list, tuple)):
            model_path = tuple(Path(p) for p in model_path) or None
        elif model_path is not None:
            model_path = Path(model_path)
        models_dir = getattr(args, "models_dir", None)
        return cls(
            pixel_mode=str(opt("pixel", defaults.pixel_mode)),
            pixel_max_side=int(opt("pixel_max_side", defaults.pixel_max_side)),
            deep_signals=bool(getattr(args, "deep_signals", False)),
            thresholds_path=_opt_path(getattr(args, "thresholds", None)),
            models_dir=Path(models_dir).expanduser() if models_dir else None,
            model_path=model_path,
            no_default_engine=bool(getattr(args, "no_default_engine", False)),
            fusion_profile=_opt_path(getattr(args, "fusion_profile", None)),
            max_files=int(opt("max_files", defaults.max_files)),
            recursive=bool(getattr(args, "recursive", False)),
            cache=_opt_path(getattr(args, "cache", None)),
            text_bytes=int(opt("text_bytes", defaults.text_bytes)),
            metadata_bytes=int(opt("metadata_bytes", defaults.metadata_bytes)),
            heatmaps=bool(getattr(args, "heatmaps", False)),
            heatmap_dir=_opt_path(getattr(args, "heatmap_dir", None)),
            workers=int(opt("workers", defaults.workers)),
            max_file_bytes=getattr(args, "max_file_bytes", None),
            allow_symlinks=bool(getattr(args, "allow_symlinks", False)),
            dedupe=bool(getattr(args, "dedupe", False)),
            hash_db=_opt_path(getattr(args, "hash_db", None)),
        )

    @classmethod
    def from_query(cls, params: Mapping[str, Any], *, models_dir: Path | None) -> "AnalysisOptions":
        """Options from HTTP query parameters (``parse_qs`` dict or flat mapping).

        Raises :class:`InvalidOption` for malformed numbers, unknown pixel
        modes, and any ``model_path`` / ``fusion_profile`` that is not a bare
        file name inside ``models_dir``. Request values can only narrow what
        the server runs — never point it at another file on the host.
        """
        pixel = _param(params, "pixel", "off").strip().lower() or "off"
        if pixel not in SUPPORTED_PIXEL_MODES:
            raise InvalidOption(f"pixel은 {sorted(SUPPORTED_PIXEL_MODES)} 중 하나여야 합니다")
        try:
            max_files = int(_param(params, "max_files", str(WEB_DEFAULT_MAX_FILES)))
        except ValueError as exc:
            raise InvalidOption("max_files는 정수여야 합니다") from exc
        max_files = max(1, min(max_files, WEB_MAX_SCAN_FILES))
        max_file_bytes: int | None = None
        raw_bytes = _param(params, "max_file_bytes", "")
        if raw_bytes.strip():
            try:
                max_file_bytes = min(int(raw_bytes), WEB_MAX_FILE_BYTES_CEILING)
            except ValueError as exc:
                raise InvalidOption("max_file_bytes는 정수여야 합니다") from exc
        resolved_dir = Path(models_dir).expanduser().resolve() if models_dir else default_models_dir()
        model_name = _param(params, "model_path", "").strip()
        fusion_name = _param(params, "fusion_profile", "").strip()
        return cls(
            pixel_mode=pixel,
            deep_signals=_flag(params, "deep_signals"),
            models_dir=resolved_dir,
            model_path=_models_dir_file(model_name, resolved_dir, "model_path") if model_name else None,
            no_default_engine=_flag(params, "no_default_engine"),
            fusion_profile=_models_dir_file(fusion_name, resolved_dir, "fusion_profile") if fusion_name else None,
            max_files=max_files,
            recursive=_flag(params, "recursive"),
            heatmaps=_flag(params, "heatmaps") and pixel == "deep",
            max_file_bytes=max_file_bytes,
            dedupe=_flag(params, "dedupe"),
        )

    # -- derived values -----------------------------------------------------

    def resolved_models_dir(self) -> Path:
        return Path(self.models_dir).expanduser().resolve() if self.models_dir else default_models_dir()

    def engine_profiles(self) -> ModelPath:
        """The model_path handed to core: explicit override, else every profile in models_dir."""
        if self.model_path is not None:
            if isinstance(self.model_path, tuple):
                return list(self.model_path)
            return self.model_path
        if self.no_default_engine:
            return None
        return default_engine_profiles(self.resolved_models_dir()) or None


def default_engine_profiles(models_dir: Path | None = None) -> list[Path]:
    """Every runtime profile in ``models_dir`` — the engine set of a default scan.

    ``supported`` (measurement gate) and ``pin`` (integrity) decide whether a
    profile actually runs; a profile that cannot run is recorded in coverage
    as skipped/failed. ``deepfake-lens doctor`` counts the same set, so its
    "실행 가능" summary matches what a scan reports as ``ran``.
    """
    base = Path(models_dir) if models_dir is not None else default_models_dir()
    if not base.is_dir():
        return []
    return sorted(base.glob(PROFILE_GLOB))


def load_thresholds(options: AnalysisOptions, *, warn: WarnFn | None = None) -> Any:
    """Resolve the decision threshold profile once for one analysis call.

    Explicit ``thresholds_path`` first; otherwise ``<models_dir>/thresholds.json``
    when present (a provisioned models dir applies its thresholds without a
    flag). Returns None for builtin defaults. Warnings (unreadable,
    provisional, in-sample) go to ``warn`` or the module logger.
    """
    from .calibration import IN_SAMPLE_LABEL, load_threshold_profile

    # Servers report the profile state in every payload (thresholds.label);
    # the log line is informational there, a stderr warning in the CLI.
    emit: WarnFn = warn if warn is not None else logger.info
    path = options.thresholds_path
    if path is None:
        auto = options.resolved_models_dir() / THRESHOLDS_FILENAME
        if not auto.is_file():
            return None
        path = auto
    profile = load_threshold_profile(path)
    if profile is None:
        emit(f"경고: 임계값 프로필을 읽을 수 없거나 버전이 맞지 않습니다: {path}")
        return None
    if profile.provisional:
        emit(
            f"경고: 임계값 프로필 {path}은(는) 잠정값입니다"
            f"({profile.provisional_reason}) — 검증되지 않은 임계값입니다"
        )
    if profile.in_sample:
        emit(
            f"경고: 임계값 프로필 {path}은(는) {IN_SAMPLE_LABEL}입니다 — 평가에 쓴 같은 표본에서 맞춘 "
            "임계값이므로(G28) 참고로만 쓰십시오"
        )
    return profile


def analyze_rows(
    path: Path | str,
    options: AnalysisOptions,
    *,
    thresholds: Any = _UNSET,
    progress: ScanProgress | None = None,
) -> list[ScanItem]:
    """Every row a scan of the file's folder reports for this one file (B1).

    A regular file yields one row; an archive yields its member rows
    ("archive::inner") plus the container row, in scan order. Row paths are
    relative to the file's folder — exactly the folder scan's rows for the
    file. The single-file commands print :func:`primary_row` as the
    conclusion and list the member rows under it.
    """
    return scan_file(path, options, thresholds=thresholds, progress=progress)[1]


def primary_row(rows: list[ScanItem]) -> ScanItem:
    """The row for the file itself: the container row of an archive, else the only row."""
    if not rows:
        raise ValueError("no rows")
    top = [row for row in rows if "::" not in row.path]
    return top[0] if top else rows[0]


def analyze_path(
    path: Path | str,
    options: AnalysisOptions,
    *,
    root: Path | None = None,
    display: str | None = None,
    thresholds: Any = _UNSET,
) -> ScanItem:
    """Analyze one file exactly as a folder scan would; returns its row.

    ``thresholds`` lets a caller that analyzes many files (upload batches,
    streaming scans) resolve the profile once (None = builtin defaults); when
    omitted it is loaded here.

    B1: an archive is expanded like ``scan`` expands it and the returned row
    is its container row (verdict rolled up from the members, rejected
    members in coverage, the archive's sha256); use :func:`analyze_rows` to
    get the member rows too. ``display`` marks an already-extracted member
    (upload paths), which is analyzed as itself.
    """
    if thresholds is _UNSET:
        thresholds = load_thresholds(options)
    if display is None and is_archive(path):
        row = primary_row(analyze_rows(path, options, thresholds=thresholds))
        if root is not None:
            # Same naming rule as a row of a scan rooted at ``root``.
            from .scan_cache import _display_path

            row = replace(row, path=_display_path(Path(path), root=root))
        return row
    item = analyze_file(
        path,
        root=root,
        display=display,
        text_bytes=options.text_bytes,
        metadata_bytes=options.metadata_bytes,
        pixel_mode=options.pixel_mode,
        pixel_max_side=options.pixel_max_side,
        heatmaps=options.heatmaps,
        heatmap_dir=options.heatmap_dir,
        model_path=options.engine_profiles(),
        deep_signals=options.deep_signals,
        thresholds=thresholds,
    )
    # The row carries the analyzed bytes' SHA-256 like a folder-scan row
    # (uploads, single-file checks), so every leg reports the same digest.
    if item.status != "failed":
        item = _with_content_sha256(item, Path(path), None)
    fusion = _fusion_profile(options)
    if fusion is not None and item.result is not None:
        from .fusion import apply_fusion_to_items

        [item] = apply_fusion_to_items([item], fusion)
    return item


def scan_folder(
    folder: Path | str,
    options: AnalysisOptions,
    should_stop: Callable[[], bool] | None = None,
    *,
    warn: WarnFn | None = None,
    progress: ScanProgress | None = None,
    on_plan: Callable[[int], None] | None = None,
) -> tuple[BatchScanSummary, list[ScanItem], Any]:
    """Scan a folder; returns ``(summary, items, thresholds)``.

    ``progress(item, done, planned)`` is called with every row as it
    completes (archive members and container rows, symlink rows included)
    — streaming front ends report from it instead of walking the folder
    themselves (R1). Rows passed to ``progress`` are pre-fusion; the
    returned items are final.

    Raises ``core.ScanFolderError`` (a ``NotADirectoryError`` whose text is
    the Korean reason — missing, a file, unreadable; S4) like
    ``core.scan_directory``.
    """
    check_scan_folder(Path(folder))  # S4: the reason before any threshold warning
    thresholds = load_thresholds(options, warn=warn)
    summary, items = scan_directory(
        folder,
        recursive=options.recursive,
        max_files=options.max_files,
        text_bytes=options.text_bytes,
        metadata_bytes=options.metadata_bytes,
        pixel_mode=options.pixel_mode,
        pixel_max_side=options.pixel_max_side,
        heatmaps=options.heatmaps,
        heatmap_dir=options.heatmap_dir,
        model_path=options.engine_profiles(),
        cache_path=options.cache,
        workers=options.workers,
        max_file_bytes=options.max_file_bytes,
        allow_symlinks=options.allow_symlinks,
        dedupe=options.dedupe,
        hash_db_path=options.hash_db,
        deep_signals=options.deep_signals,
        thresholds=thresholds,
        should_stop=should_stop,
        progress=progress,
        on_plan=on_plan,
    )
    return _with_fusion(summary, items, options) + (thresholds,)


def scan_file(
    path: Path | str,
    options: AnalysisOptions,
    *,
    thresholds: Any = _UNSET,
    progress: ScanProgress | None = None,
) -> tuple[BatchScanSummary, list[ScanItem], Any]:
    """Analyze one file as a folder scan of its parent would; ``(summary, items, thresholds)``.

    A regular file yields one row (same as :func:`analyze_path`); an
    archive yields its member rows plus the container row with refused
    members recorded per member (R1) — so ``/api/check`` with an archive
    path reports what ``scan`` reports for that archive.
    """
    file_path = Path(path)
    if thresholds is _UNSET:
        thresholds = load_thresholds(options)
    summary, items = scan_paths(
        [file_path],
        root=file_path.parent,
        text_bytes=options.text_bytes,
        metadata_bytes=options.metadata_bytes,
        pixel_mode=options.pixel_mode,
        pixel_max_side=options.pixel_max_side,
        heatmaps=options.heatmaps,
        heatmap_dir=options.heatmap_dir,
        model_path=options.engine_profiles(),
        max_file_bytes=options.max_file_bytes,
        deep_signals=options.deep_signals,
        thresholds=thresholds,
        progress=progress,
    )
    return _with_fusion(summary, items, options) + (thresholds,)


def _with_fusion(
    summary: BatchScanSummary, items: list[ScanItem], options: AnalysisOptions,
) -> tuple[BatchScanSummary, list[ScanItem]]:
    fusion = _fusion_profile(options)
    if fusion is not None:
        from .fusion import apply_fusion_to_items

        items = apply_fusion_to_items(items, fusion)
        summary = replace(
            summarize(items, capped=summary.capped, cached=summary.cached),
            subfolders_skipped=summary.subfolders_skipped,
        )
    return summary, items


def scan_payload(summary: BatchScanSummary, items: list[ScanItem], thresholds: Any, options: AnalysisOptions) -> dict[str, object]:
    """The scan JSON every front end returns (schema, summary, provenance, items)."""
    return scan_to_json(summary, items, thresholds=thresholds, models_dir=options.resolved_models_dir())


def provenance(options: AnalysisOptions, thresholds: Any) -> dict[str, object]:
    """Coverage + threshold provenance for non-folder payloads (uploads, checks)."""
    from .vendor_weights import weights_coverage

    return {
        "coverage": dict(weights_coverage(options.resolved_models_dir())),
        "thresholds": _thresholds_json(thresholds),
    }


# -- helpers -----------------------------------------------------------------


def _fusion_profile(options: AnalysisOptions) -> Any:
    if options.fusion_profile is None:
        return None
    from .fusion import load_fusion_profile

    return load_fusion_profile(options.fusion_profile)


def _opt_path(value: object) -> Path | None:
    if value is None or value == "":
        return None
    return Path(str(value)).expanduser()


def _param(params: Mapping[str, Any], key: str, default: str) -> str:
    value = params.get(key)
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else default
    return str(value)


def _flag(params: Mapping[str, Any], key: str) -> bool:
    return _param(params, key, "false").strip().lower() in _TRUE_VALUES


def _models_dir_file(name: str, models_dir: Path, field_name: str) -> Path:
    """Resolve a request-supplied profile name strictly inside ``models_dir``.

    Only a bare file name is accepted: no separators, no ``..``, no drive or
    absolute path. The result must exist as a regular file under the models
    directory after symlink resolution.
    """
    if (
        name in {".", ".."}
        or "/" in name
        or "\\" in name
        or ":" in name
        or "\x00" in name
        or PurePosixPath(name).is_absolute()
        or PureWindowsPath(name).is_absolute()
        or PurePosixPath(name).name != name
    ):
        raise InvalidOption(f"{field_name}은(는) 모델 디렉터리 안의 파일 이름이어야 합니다")
    base = models_dir.resolve()
    candidate = (base / name).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise InvalidOption(f"{field_name}은(는) 모델 디렉터리 안의 파일 이름이어야 합니다") from exc
    if not candidate.is_file():
        raise InvalidOption(f"{field_name}을(를) 모델 디렉터리에서 찾을 수 없습니다: {name}")
    return candidate


def thresholds_warning_printer(stream: Any) -> WarnFn:
    """A ``warn`` callback that prints to ``stream`` (CLI stderr)."""

    def emit(message: str) -> None:
        print(message, file=stream)

    return emit


__all__ = [
    "AnalysisOptions",
    "InvalidOption",
    "WEB_MAX_FILE_BYTES_CEILING",
    "WEB_MAX_SCAN_FILES",
    "analyze_path",
    "analyze_rows",
    "default_engine_profiles",
    "load_thresholds",
    "primary_row",
    "provenance",
    "scan_file",
    "scan_folder",
    "scan_payload",
]
