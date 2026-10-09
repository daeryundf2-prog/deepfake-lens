"""Input-path checks for every CLI subcommand (N4/N7).

Before round 6 only scan and the single-file verdict commands checked the
path they were given: ``text-advanced <missing>`` / ``3d --file <missing>``
died with an English traceback (exit 1), ``audio``/``face``/``rppg``/``prnu``/
``evidence``/``batch``/``eval``… printed an empty or "판단 불가" report
about nothing with exit 0, and ``perf <file>`` raised ``ScanFolderError``.

Every subcommand now declares its input paths in :data:`INPUT_SPECS`
(argument, kind, supported formats) and :func:`check_command_inputs` runs
:func:`require_input_path` on each before the command starts. A bad path is
a :class:`UsageError` that ``cli.main`` prints as ``오류: …`` on stderr with
exit code 2 (verify-report keeps its own usage code 4 — its 2 means "key ID
mismatch"):

* missing path       → ``파일을 찾을 수 없습니다: …`` / ``폴더를 찾을 수 없습니다: …``
* a folder for a file → ``파일이 아니라 폴더입니다: …``
* a file for a folder → ``폴더가 아니라 파일입니다: …``
* a file named with a trailing separator (``photo.png/``, Z3) → ``폴더가 아니라
  파일입니다: … — 경로 끝의 구분자('/')는 폴더를 뜻합니다 …`` (every command)
* unsupported format → ``지원되지 않는 형식입니다: … (지원 형식: …)``
* a symbolic link given to a layer command → ``심볼릭 링크는 따라가지 않습니다: …``
  (the verdict commands keep G6: a link is a skipped row, never read).

``tests/test_cli_inputs.py`` runs every declared input of every subcommand
through a subprocess matrix and checks that no Path-typed input argument of
``cli_parser`` is missing from the table.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .archives import SUPPORTED_ARCHIVE_EXTENSIONS, is_archive
from .audio import SUPPORTED_AUDIO_EXTENSIONS
from .documents import SUPPORTED_DOCUMENT_EXTENSIONS
from .video_analysis import SUPPORTED_VIDEO_EXTENSIONS

PathKind = Literal["file", "folder", "either"]
SymlinkRule = Literal["allow", "refuse"]

# Kept equal to core.SUPPORTED_IMAGE_EXTENSIONS / SUPPORTED_TEXT_EXTENSIONS
# (core imports this module's callers; a test checks the sets stay equal).
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"})
TEXT_SUFFIXES = frozenset({".txt", ".md"})
AUDIO_SUFFIXES = frozenset(SUPPORTED_AUDIO_EXTENSIONS)
VIDEO_SUFFIXES = frozenset(SUPPORTED_VIDEO_EXTENSIONS)
DOCUMENT_SUFFIXES = frozenset(SUPPORTED_DOCUMENT_EXTENSIONS)
ARCHIVE_SUFFIXES = frozenset(SUPPORTED_ARCHIVE_EXTENSIONS)
# What scan analyzes (core.analyze_file): the verdict commands take the same set.
SCAN_SUFFIXES = IMAGE_SUFFIXES | TEXT_SUFFIXES | DOCUMENT_SUFFIXES | AUDIO_SUFFIXES | VIDEO_SUFFIXES | ARCHIVE_SUFFIXES
TEXT_DOCUMENT_SUFFIXES = TEXT_SUFFIXES | DOCUMENT_SUFFIXES
# core.compare_files: an audio pair, or a text/document pair (.rst/.log read as text).
COMPARE_TEXT_SUFFIXES = TEXT_DOCUMENT_SUFFIXES | {".rst", ".log"}
COMPARE_SUFFIXES = AUDIO_SUFFIXES | COMPARE_TEXT_SUFFIXES
# threed: 3D model formats (threed._analyze_file_extension) plus text read for markers.
THREED_SUFFIXES = frozenset({".glb", ".gltf", ".obj", ".fbx", ".ply", ".pcd", ".npy", ".npz"}) | TEXT_SUFFIXES
JSON_SUFFIXES = frozenset({".json"})
FEEDBACK_SUFFIXES = frozenset({".json", ".jsonl"})

MISSING = {
    "file": "파일을 찾을 수 없습니다: {path}",
    "folder": "폴더를 찾을 수 없습니다: {path}",
    "either": "파일이나 폴더를 찾을 수 없습니다: {path}",
}
IS_FOLDER = "파일이 아니라 폴더입니다: {path}{hint}"
IS_FILE = "폴더가 아니라 파일입니다: {path}{hint}"
UNSUPPORTED = "지원되지 않는 형식입니다: {path} (지원 형식: {formats})"
SYMLINK_REFUSED = "심볼릭 링크는 따라가지 않습니다: {path} — 링크 대상 파일을 직접 지정하십시오"
NOT_REGULAR = "일반 파일이 아닙니다: {path}"
UNREADABLE = "경로를 확인할 수 없습니다: {path}"
# Y2 (round 7): an explicit --thresholds file that exists but is not a
# threshold profile was a warning and the run went on with the defaults.
THRESHOLDS_UNREADABLE = "임계값 프로필을 읽을 수 없거나 버전이 맞지 않습니다: {path} (layer-thresholds-v1 JSON이어야 합니다)"
# Y7 (round 7): an output file argument naming an existing folder failed
# after the scan with a generic error and a misleading "처리 오류 1건".
OUTPUT_IS_FOLDER = "출력 경로가 폴더입니다: {path} — 저장할 파일 이름을 지정하십시오"
# Z3: "photo.png/" names a folder; Path() drops the trailing separator, so
# without this check the file was analyzed as if the slash were not there.
TRAILING_SEPARATOR_HINT = " — 경로 끝의 구분자('/')는 폴더를 뜻합니다. 파일이면 구분자 없이 지정하십시오"
# Z5: an output file whose parent folder does not exist; folders are never
# created for an output argument (a typo would scatter reports).
OUTPUT_FOLDER_MISSING = "출력 폴더가 없습니다: {folder} — 출력 폴더는 자동으로 만들지 않습니다. 폴더를 먼저 만들거나 기존 폴더를 지정하십시오"
FOLDER_HINT_SCAN = " (폴더는 scan을 사용)"
FILE_HINT_SINGLE = " (단일 파일은 forensic/classify를 사용)"


class UsageError(Exception):
    """A command-line usage error; ``str()`` is the Korean reason (``cli.main`` → ``오류: …``, exit 2)."""


def _formats(suffixes: frozenset[str]) -> str:
    return ", ".join(sorted(suffixes))


def has_supported_suffix(path: Path | str, suffixes: frozenset[str]) -> bool:
    """True when the file name ends with one of ``suffixes`` (``.tar.gz`` included), case-insensitive."""
    name = Path(path).name.lower()
    return any(name.endswith(suffix) for suffix in suffixes)


_SEPARATORS = tuple(sep for sep in (os.sep, os.altsep) if sep)


def _names_file_as_folder(shown: str) -> bool:
    """Z3: ``shown`` ends with a path separator but names an existing non-folder.

    ``Path("photo.png/")`` is ``Path("photo.png")``, so the raw string is
    checked. A missing path or a dangling link falls through to the usual
    rules (missing / symbolic link).
    """
    stripped = shown.rstrip("".join(_SEPARATORS))
    if not stripped or stripped == shown:
        return False
    # os.path.exists/isdir follow links and report an unreadable path as False.
    return os.path.exists(stripped) and not os.path.isdir(stripped)


def require_input_path(
    path: Path | str,
    kind: PathKind,
    *,
    supported_suffixes: frozenset[str] | None = None,
    symlinks: SymlinkRule = "refuse",
    folder_hint: str = "",
    file_hint: str = "",
) -> Path:
    """Return ``path`` as a Path, or raise :class:`UsageError` with the Korean reason.

    ``kind`` is what the argument must name; ``supported_suffixes`` (files
    only) the formats the command reads — an archive is accepted where
    :data:`SCAN_SUFFIXES` is. ``symlinks="allow"`` passes a symbolic link
    through unchecked (the verdict commands report it as a skipped row and
    never read its target, G6); ``"refuse"`` rejects it, because a layer
    command would read the link target.
    """
    target = Path(path)
    shown = str(path)
    # Z3: the text as typed (cli_parser.CliPath keeps it; Path() drops a trailing "/").
    typed = path if isinstance(path, str) else str(getattr(path, "cli_text", "") or shown)
    if _names_file_as_folder(typed):
        raise UsageError(IS_FILE.format(path=typed, hint=file_hint + TRAILING_SEPARATOR_HINT))
    try:
        is_link = target.is_symlink()
    except OSError as exc:
        raise UsageError(UNREADABLE.format(path=shown)) from exc
    if is_link:
        if symlinks == "allow":
            return target
        raise UsageError(SYMLINK_REFUSED.format(path=shown))
    try:
        exists = target.exists()
        is_dir = exists and target.is_dir()
        is_file = exists and target.is_file()
    except OSError as exc:
        raise UsageError(UNREADABLE.format(path=shown)) from exc
    if not exists:
        raise UsageError(MISSING[kind].format(path=shown))
    if kind == "file" and is_dir:
        raise UsageError(IS_FOLDER.format(path=shown, hint=folder_hint))
    if kind == "folder" and not is_dir:
        raise UsageError(IS_FILE.format(path=shown, hint=file_hint))
    if kind in ("file", "either") and not is_dir:
        if not is_file:
            raise UsageError(NOT_REGULAR.format(path=shown))
        if supported_suffixes is not None and not has_supported_suffix(target, supported_suffixes):
            if not (supported_suffixes >= ARCHIVE_SUFFIXES and is_archive(target)):
                raise UsageError(UNSUPPORTED.format(path=shown, formats=_formats(supported_suffixes)))
    return target


@dataclass(frozen=True)
class InputSpec:
    """One input-path argument of a subcommand."""

    attr: str
    kind: PathKind
    suffixes: frozenset[str] | None = None
    symlinks: SymlinkRule = "refuse"
    folder_hint: str = ""
    file_hint: str = ""


def _verdict_file(attr: str) -> InputSpec:
    # G5/G6: the verdict commands answer about one file through scan's path;
    # a link is a skipped row there (never followed).
    return InputSpec(attr, "file", SCAN_SUFFIXES, "allow", folder_hint=FOLDER_HINT_SCAN)


def _folder(attr: str, *, file_hint: str = "") -> InputSpec:
    # A folder argument may itself be a link the operator chose (as scan's root).
    return InputSpec(attr, "folder", None, "allow", file_hint=file_hint)


# Every subcommand's input paths (outputs such as --out/--json-out are not
# inputs). Keys are ``args.command``, or "corpus <sub>" for corpus.
INPUT_SPECS: dict[str, tuple[InputSpec, ...]] = {
    "scan": (_folder("folder", file_hint=FILE_HINT_SINGLE),),
    "collect": (_folder("folder"),),
    "dataset": (_folder("folder"),),
    "eval": (_folder("folder"),),
    "benchmark": (_folder("folder"),),
    "fusion": (_folder("folder"),),
    "calibrate": (_folder("folder"),),
    "feedback": (InputSpec("labels", "file", FEEDBACK_SUFFIXES), InputSpec("scan_json", "file", JSON_SUFFIXES)),
    "train": (_folder("folder"),),
    "train-neural-plan": (_folder("folder"),),
    "video": (_folder("folder"),),
    "audio": (InputSpec("file", "file", AUDIO_SUFFIXES),),
    "face": (InputSpec("file", "file", IMAGE_SUFFIXES),),
    "video-analysis": (InputSpec("file", "file", VIDEO_SUFFIXES),),
    "inpaint": (InputSpec("file", "file", IMAGE_SUFFIXES),),
    "text-advanced": (InputSpec("file", "file", TEXT_SUFFIXES),),
    "compare": (InputSpec("file_a", "file", COMPARE_SUFFIXES), InputSpec("file_b", "file", COMPARE_SUFFIXES)),
    "watermark": (InputSpec("file", "file", COMPARE_TEXT_SUFFIXES),),
    "forensic": (_verdict_file("file"),),
    "classify": (_verdict_file("file"),),
    "multimodal": (_verdict_file("files"), InputSpec("av_sync", "file", VIDEO_SUFFIXES)),
    "rppg": (InputSpec("file", "file", VIDEO_SUFFIXES),),
    "prnu": (InputSpec("file", "file", IMAGE_SUFFIXES), InputSpec("reference", "file", IMAGE_SUFFIXES)),
    # A chain-of-custody record can be made for any file — no format list.
    "evidence": (InputSpec("file", "file", None),),
    "batch": (_folder("folder"),),
    "explain": (_verdict_file("file"),),
    "agent": (InputSpec("file", "file", TEXT_DOCUMENT_SUFFIXES, "allow", folder_hint=FOLDER_HINT_SCAN),),
    "3d": (InputSpec("file", "file", THREED_SUFFIXES),),
    "avatar": (InputSpec("file", "file", VIDEO_SUFFIXES | IMAGE_SUFFIXES),),
    "pixel-analysis": (InputSpec("file", "file", IMAGE_SUFFIXES),),
    "ml-classify": (InputSpec("file", "file", IMAGE_SUFFIXES),),
    "legal-report": (_verdict_file("file"),),
    "verify-report": (InputSpec("report", "file", JSON_SUFFIXES),),
    "perf": (_folder("folder"),),
    "faceswap-seam": (InputSpec("file", "file", IMAGE_SUFFIXES),),
    # A folder, a scan-result JSON or one file (scan's formats).
    "evidence-statement": (InputSpec("target", "either", SCAN_SUFFIXES | JSON_SUFFIXES, "allow"),),
    "models": (InputSpec("checkpoint", "file", None),),
    "web": (_folder("folder"), _folder("allow_root")),
    "api-serve": (_folder("allow_root"),),
    "vendor-weights": (_folder("install"),),
    "corpus build": (_folder("folder"),),
    "corpus split": (InputSpec("manifest", "file", JSON_SUFFIXES),),
    "corpus verify": (InputSpec("manifest", "file", JSON_SUFFIXES), _folder("root")),
}


# Configuration inputs shared by many subcommands: an explicitly named
# threshold/fusion/calibration profile, model profile or models folder that
# does not exist was a warning (or silently ignored) and the command ran
# with the built-in defaults — a weaker analysis than the examiner asked
# for. Checked for every command that has the option (N4).
COMMON_INPUT_SPECS: tuple[InputSpec, ...] = (
    InputSpec("thresholds", "file", JSON_SUFFIXES),
    InputSpec("fusion_profile", "file", JSON_SUFFIXES),
    InputSpec("calibration", "file", JSON_SUFFIXES),
    # A profile .json, a checkpoint file or a folder of profiles.
    InputSpec("model_path", "either", None, "allow"),
    _folder("models_dir"),
)
# vendor-weights --models-dir may name the folder an --install creates.
COMMON_INPUT_EXEMPT: dict[str, frozenset[str]] = {"vendor-weights": frozenset({"models_dir"})}

# Y7: output arguments that name a file the command writes (folders such as
# --output-dir, --frame-root, --heatmap-dir, --to, --bundle-to are not here).
# Z5: the ``*_out`` ones (--json-out, --csv-out, --html-out, --pdf-out, …)
# also need an existing parent folder; --out/--output/--cache/--hash-db keep
# creating theirs.
OUTPUT_FILE_ATTRS: tuple[str, ...] = (
    "out", "output", "json_out", "csv_out", "html_out", "pdf_out", "md_out", "forensic_pdf_out",
    "evidence_statement_out", "evidence_statement_pdf_out", "manifest_out", "audit_out", "split_out",
    "robustness_out", "profile_out", "false_positive_out", "false_negative_out", "mapping_out",
    "cache", "hash_db",
)


def require_output_file(path: Path | str, *, parent_must_exist: bool = False) -> Path:
    """Refuse an output-file argument that names an existing folder (Y7) — before any work.

    ``parent_must_exist`` (Z5, the ``--*-out`` options): the folder the file
    goes into must already exist; it is never created.
    """
    target = Path(path)
    try:
        is_dir = target.is_dir()
        parent_ok = not parent_must_exist or target.parent.is_dir()
    except OSError as exc:
        raise UsageError(UNREADABLE.format(path=path)) from exc
    if is_dir:
        raise UsageError(OUTPUT_IS_FOLDER.format(path=path))
    if not parent_ok:
        raise UsageError(OUTPUT_FOLDER_MISSING.format(folder=target.parent))
    return target


def require_threshold_profile(path: Path | str) -> None:
    """An explicit --thresholds file must load as a threshold profile (Y2): else exit 2."""
    from .calibration import load_threshold_profile

    if load_threshold_profile(path) is None:
        raise UsageError(THRESHOLDS_UNREADABLE.format(path=path))


def command_key(args: argparse.Namespace) -> str:
    command = str(getattr(args, "command", "") or "")
    if command == "corpus":
        return f"corpus {getattr(args, 'corpus_command', '') or ''}".strip()
    return command


def check_command_inputs(args: argparse.Namespace) -> None:
    """Run :func:`require_input_path` on every declared input of the parsed command."""
    key = command_key(args)
    exempt = COMMON_INPUT_EXEMPT.get(key, frozenset())
    common = tuple(spec for spec in COMMON_INPUT_SPECS if spec.attr not in exempt)
    for spec in (*INPUT_SPECS.get(key, ()), *common):
        value = getattr(args, spec.attr, None)
        if value is None:
            continue
        for item in value if isinstance(value, (list, tuple)) else [value]:
            require_input_path(
                item,
                spec.kind,
                supported_suffixes=spec.suffixes,
                symlinks=spec.symlinks,
                folder_hint=spec.folder_hint,
                file_hint=spec.file_hint,
            )
    thresholds = getattr(args, "thresholds", None)
    if thresholds is not None and "thresholds" not in exempt:
        require_threshold_profile(thresholds)  # Y2: unreadable profile -> exit 2
    for attr in OUTPUT_FILE_ATTRS:
        value = getattr(args, attr, None)
        if isinstance(value, (str, Path)) and str(value):
            # Y7: a folder is not an output file; Z5: --*-out needs an existing folder.
            require_output_file(value, parent_must_exist=attr.endswith("_out"))
    cache = getattr(args, "cache", None)
    if isinstance(cache, (str, Path)) and str(cache):
        # Y4: an existing file that is not a scan cache is never overwritten.
        from .scan_cache import CacheFileError, check_scan_cache_file

        try:
            check_scan_cache_file(cache)
        except CacheFileError as exc:
            raise UsageError(str(exc)) from exc
