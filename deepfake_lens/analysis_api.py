"""Single analysis entry point for the CLI, the web GUI and the REST API (G7).

Before phase 0 each front end assembled its own ``scan_directory`` /
``analyze_file`` call: the CLI auto-loaded the measured threshold profile,
the GUI passed no thresholds at all, and the FastAPI server ran a different
model set. The same folder therefore produced different verdict provenance
depending on which door it came through (G7).

Every front end now builds an :class:`AnalysisOptions` (``from_cli_args`` for
argparse, ``from_query`` for HTTP query strings) and calls only
:func:`analyze_path` or :func:`scan_folder`. Thresholds are resolved once per
call by :func:`load_thresholds` — the explicit ``--thresholds`` file, else
``<models_dir>/thresholds.json`` — so a CLI scan and a GUI scan of the same
folder report the same ``thresholds`` provenance and the same verdicts.

HTTP callers can never name an arbitrary path for a model or fusion profile:
``model_path`` / ``fusion_profile`` query values must be a bare file name that
exists inside the server's models directory (:class:`InvalidOption` → 400).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Mapping

from .core import (
    DEFAULT_MAX_FILES,
    DEFAULT_TEXT_BYTES,
    BatchScanSummary,
    ScanItem,
    _thresholds_json,
    analyze_file,
    scan_directory,
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
            raise InvalidOption(f"pixel must be one of {sorted(SUPPORTED_PIXEL_MODES)}")
        try:
            max_files = int(_param(params, "max_files", str(WEB_DEFAULT_MAX_FILES)))
        except ValueError as exc:
            raise InvalidOption("max_files must be an integer") from exc
        max_files = max(1, min(max_files, WEB_MAX_SCAN_FILES))
        max_file_bytes: int | None = None
        raw_bytes = _param(params, "max_file_bytes", "")
        if raw_bytes.strip():
            try:
                max_file_bytes = min(int(raw_bytes), WEB_MAX_FILE_BYTES_CEILING)
            except ValueError as exc:
                raise InvalidOption("max_file_bytes must be an integer") from exc
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
        emit(f"warning: threshold profile unreadable or wrong version: {path}")
        return None
    if profile.provisional:
        emit(
            f"warning: threshold profile {path} is provisional "
            f"({profile.provisional_reason}); thresholds are unvalidated"
        )
    if profile.in_sample:
        emit(
            f"warning: threshold profile {path} is {IN_SAMPLE_LABEL} — cutoffs were fitted on the rows "
            "they were evaluated on (G28); treat as reference only"
        )
    return profile


def analyze_path(
    path: Path | str,
    options: AnalysisOptions,
    *,
    root: Path | None = None,
    display: str | None = None,
    thresholds: Any = _UNSET,
) -> ScanItem:
    """Analyze one file exactly as a folder scan would.

    ``thresholds`` lets a caller that analyzes many files (upload batches,
    streaming scans) resolve the profile once (None = builtin defaults); when
    omitted it is loaded here.
    """
    if thresholds is _UNSET:
        thresholds = load_thresholds(options)
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
) -> tuple[BatchScanSummary, list[ScanItem], Any]:
    """Scan a folder; returns ``(summary, items, thresholds)``.

    Raises ``NotADirectoryError``/``OSError`` like ``core.scan_directory``.
    """
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
    )
    fusion = _fusion_profile(options)
    if fusion is not None:
        from .fusion import apply_fusion_to_items

        items = apply_fusion_to_items(items, fusion)
        summary = summarize(items, capped=summary.capped, cached=summary.cached)
    return summary, items, thresholds


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
        raise InvalidOption(f"{field_name} must be a file name inside the models directory")
    base = models_dir.resolve()
    candidate = (base / name).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise InvalidOption(f"{field_name} must be a file name inside the models directory") from exc
    if not candidate.is_file():
        raise InvalidOption(f"{field_name} not found in the models directory: {name}")
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
    "default_engine_profiles",
    "load_thresholds",
    "provenance",
    "scan_folder",
    "scan_payload",
]
