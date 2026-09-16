"""Archive container support for Deepfake Lens.

Archives are screening containers, not media: the module safely extracts
members to a temp directory so the existing per-file pipeline can analyze
each payload. Guards against the classic archive attack surface:

- zip-slip / path traversal (``..``, absolute paths, drive letters, ADS
  ``:`` names) — members are name-checked AND resolve-checked under dest
- zip bombs — cumulative uncompressed-byte cap, per-member cap, and a
  compression-ratio cap for deflate members
- symlink/hardlink/device members — skipped (zip external_attr bits,
  tarfile ``filter="data"``)
- member-count cap — quirk archives with tens of thousands of entries

``.zip`` and tar variants use the stdlib; ``.7z`` (py7zr) and ``.rar``
(rarfile + unrar binary) are optional and degrade to a warning.
"""

from __future__ import annotations

import os
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

SUPPORTED_ARCHIVE_EXTENSIONS = {
    ".zip", ".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tbz2",
    ".tar.xz", ".txz", ".7z", ".rar",
}

MAX_ARCHIVE_MEMBERS = 1000
MAX_ARCHIVE_TOTAL_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_RATIO = 200
MAX_NESTED_DEPTH = 2

# Windows device names that can never be created as files.
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


@dataclass
class ArchiveExtraction:
    members: list[Path] = field(default_factory=list)
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)


def archive_format(path: Path | str) -> str | None:
    """Return the archive format for a path, or None."""
    name = Path(path).name.lower()
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if name.endswith(suffix):
            return "tar"
    suffix = Path(name).suffix
    if suffix in {".tgz", ".tbz2", ".txz"}:
        return "tar"
    if suffix in {".zip", ".tar", ".7z", ".rar"}:
        return suffix.lstrip(".")
    return None


def is_archive(path: Path | str) -> bool:
    return archive_format(path) is not None


def _safe_member_name(name: str) -> str | None:
    """Normalize an archive member name; None when it escapes dest.

    Rejects absolute paths, ``..`` components, drive-letter/ADS colons,
    Windows device names, and empty/traversal tricks. Returns a relative
    POSIX-style path safe to join under the extraction root.
    """
    name = name.replace("\\", "/").strip()
    if not name or name.startswith("/"):
        return None
    if ":" in name.split("/")[0] or name[0] == "~":
        return None
    parts = [p for p in PurePosixPath(name).parts if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        return None
    stem = parts[-1].split(".")[0].strip().lower()
    if stem in _WINDOWS_RESERVED:
        return None
    return "/".join(parts)


def _zip_member_name(info: zipfile.ZipInfo) -> str:
    """Decode a zip member name, trying euc-kr for Korean archives whose
    names were stored without the UTF-8 flag bit."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("euc-kr")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return info.filename


def _dest_for(dest: Path, rel: str) -> Path | None:
    target = (dest / rel).resolve()
    try:
        target.relative_to(dest.resolve())
    except ValueError:
        return None
    return target


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _extract_zip(path: Path, dest: Path, out: ArchiveExtraction) -> None:
    total = 0
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if len(out.members) >= MAX_ARCHIVE_MEMBERS:
                out.warnings.append(f"멤버 수 상한({MAX_ARCHIVE_MEMBERS}) 도달 — 나머지 생략")
                break
            if info.is_dir():
                continue
            if _is_zip_symlink(info):
                out.skipped += 1
                continue
            rel = _safe_member_name(_zip_member_name(info))
            if rel is None:
                out.skipped += 1
                continue
            if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                out.skipped += 1
                continue
            if info.compress_size and info.file_size // max(1, info.compress_size) > MAX_ARCHIVE_RATIO:
                out.skipped += 1
                continue
            if total + info.file_size > MAX_ARCHIVE_TOTAL_BYTES:
                out.warnings.append(f"해제 총량 상한({MAX_ARCHIVE_TOTAL_BYTES // (1024 * 1024)}MB) 도달 — 나머지 생략")
                break
            target = _dest_for(dest, rel)
            if target is None:
                out.skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info) as src, target.open("wb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
            except (OSError, zipfile.BadZipFile, RuntimeError):
                out.skipped += 1
                continue
            total += info.file_size
            out.members.append(target)


def _extract_tar(path: Path, dest: Path, out: ArchiveExtraction) -> None:
    total = 0
    with tarfile.open(path) as tf:
        for member in tf.getmembers():
            if len(out.members) >= MAX_ARCHIVE_MEMBERS:
                out.warnings.append(f"멤버 수 상한({MAX_ARCHIVE_MEMBERS}) 도달 — 나머지 생략")
                break
            if not member.isreg():
                out.skipped += 1
                continue
            rel = _safe_member_name(member.name)
            if rel is None or member.size > MAX_ARCHIVE_MEMBER_BYTES:
                out.skipped += 1
                continue
            if total + member.size > MAX_ARCHIVE_TOTAL_BYTES:
                out.warnings.append(f"해제 총량 상한({MAX_ARCHIVE_TOTAL_BYTES // (1024 * 1024)}MB) 도달 — 나머지 생략")
                break
            target = _dest_for(dest, rel)
            if target is None:
                out.skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                src = tf.extractfile(member)
                if src is None:
                    out.skipped += 1
                    continue
                with src, target.open("wb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
            except (OSError, tarfile.TarError):
                out.skipped += 1
                continue
            total += member.size
            out.members.append(target)


def _extract_7z(path: Path, dest: Path, out: ArchiveExtraction) -> None:
    try:
        import py7zr
    except ImportError:
        out.warnings.append("7z 해제에는 py7zr이 필요합니다 (pip install deepfake-lens[archive])")
        return
    total = 0
    try:
        with py7zr.SevenZipFile(path) as sf:
            infos = {i.filename: i for i in sf.list() if not i.is_directory}
            targets: list[str] = []
            for name, info in infos.items():
                if len(targets) >= MAX_ARCHIVE_MEMBERS:
                    out.warnings.append(f"멤버 수 상한({MAX_ARCHIVE_MEMBERS}) 도달 — 나머지 생략")
                    break
                rel = _safe_member_name(name)
                if rel is None or info.uncompressed > MAX_ARCHIVE_MEMBER_BYTES:
                    out.skipped += 1
                    continue
                if total + info.uncompressed > MAX_ARCHIVE_TOTAL_BYTES:
                    out.warnings.append(f"해제 총량 상한({MAX_ARCHIVE_TOTAL_BYTES // (1024 * 1024)}MB) 도달 — 나머지 생략")
                    break
                total += info.uncompressed
                targets.append(name)
            if targets:
                sf.extract(dest, targets=targets)
            for name in targets:
                rel = _safe_member_name(name)
                target = _dest_for(dest, rel) if rel else None
                if target and target.is_file():
                    out.members.append(target)
                else:
                    out.skipped += 1
    except Exception as exc:  # py7zr raises several custom error types
        out.warnings.append(f"7z 해제 실패: {exc}")


def _extract_rar(path: Path, dest: Path, out: ArchiveExtraction) -> None:
    try:
        import rarfile
    except ImportError:
        out.warnings.append("rar 해제에는 rarfile이 필요합니다 (pip install deepfake-lens[archive])")
        return
    total = 0
    try:
        with rarfile.RarFile(path) as rf:
            for info in rf.infolist():
                if len(out.members) >= MAX_ARCHIVE_MEMBERS:
                    out.warnings.append(f"멤버 수 상한({MAX_ARCHIVE_MEMBERS}) 도달 — 나머지 생략")
                    break
                if info.isdir():
                    continue
                rel = _safe_member_name(info.filename)
                if rel is None or info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                    out.skipped += 1
                    continue
                if total + info.file_size > MAX_ARCHIVE_TOTAL_BYTES:
                    out.warnings.append(f"해제 총량 상한({MAX_ARCHIVE_TOTAL_BYTES // (1024 * 1024)}MB) 도달 — 나머지 생략")
                    break
                target = _dest_for(dest, rel)
                if target is None:
                    out.skipped += 1
                    continue
                try:
                    rf.extract(info, dest)
                except Exception:
                    out.skipped += 1
                    continue
                if target.is_file():
                    total += info.file_size
                    out.members.append(target)
                else:
                    out.skipped += 1
    except Exception as exc:
        out.warnings.append(f"rar 해제 실패: {exc}")


def extract_archive(
    path: Path | str,
    dest: Path | str,
    *,
    max_depth: int = MAX_NESTED_DEPTH,
    _depth: int = 0,
) -> ArchiveExtraction:
    """Extract archive members into ``dest`` and return real file paths.

    Nested archives inside the archive are re-expanded up to
    ``max_depth`` levels; deeper ones are counted as skipped. Never
    raises for a malformed archive — problems land in ``warnings``.
    """
    src = Path(path)
    root = Path(dest)
    root.mkdir(parents=True, exist_ok=True)
    out = ArchiveExtraction()
    fmt = archive_format(src)
    try:
        if fmt == "zip":
            _extract_zip(src, root, out)
        elif fmt == "tar":
            _extract_tar(src, root, out)
        elif fmt == "7z":
            _extract_7z(src, root, out)
        elif fmt == "rar":
            _extract_rar(src, root, out)
        else:
            out.warnings.append("지원하지 않는 압축 형식입니다.")
            return out
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as exc:
        out.warnings.append(f"압축 해제 실패: {exc}")
        return out

    nested = [m for m in out.members if is_archive(m)]
    if nested:
        if _depth + 1 >= max_depth:
            out.skipped += len(nested)
            out.warnings.append(f"중첩 압축 {len(nested)}개 — 최대 깊이({max_depth})로 미해제")
            for m in nested:
                out.members.remove(m)
        else:
            for m in nested:
                sub = extract_archive(m, root / (m.stem + ".unpacked"), max_depth=max_depth, _depth=_depth + 1)
                if sub.members:
                    out.members.remove(m)
                    out.members.extend(sub.members)
                out.skipped += sub.skipped
                out.warnings.extend(sub.warnings)
    return out
