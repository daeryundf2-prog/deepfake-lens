"""Archive container support for Deepfake Lens.

Archives are screening containers, not media: the module safely extracts
members to a temp directory so the existing per-file pipeline can analyze
each payload. Guards against the classic archive attack surface:

- zip-slip / path traversal (``..``, absolute paths, drive letters, ADS
  ``:`` names) — members are name-checked AND resolve-checked under dest
- zip bombs — per-archive and aggregate uncompressed-byte caps counted on
  the bytes actually written (declared sizes can lie), a per-member cap,
  and a compression-ratio cap for deflate members
- nesting bombs (G34) — one :class:`ExtractionBudget` per top-level archive
  is threaded through every nested extraction: total bytes written, total
  members, and number of nested archives opened are bounded across the
  whole tree, not per level
- symlink/hardlink/device members — skipped: zip ``external_attr`` mode
  bits, tar members that are not regular files (``TarInfo.isreg()``; the
  bytes are copied via ``extractfile``, never ``extract``/``extractall``),
  7z ``is_symlink``/junction entries and rar ``is_symlink()``/redirect
  (``file_redir``) or otherwise non-regular entries
- member-count cap — quirk archives with tens of thousands of entries
- duplicate member names (R12-3) — every member is written to its own
  index-numbered folder (``<dest>/<entry index>/<base name>``), so two
  entries that normalize to the same path (the same name twice,
  ``x/y.png`` + ``x\\y.png``, ``./p.png`` + ``p.png``, ``A.png``/``a.png``
  on a case-insensitive file system) never overwrite each other; the row
  name keeps the original path and a later duplicate is named ``<path>#2``
  (``#3`` …) with the reason "중복 멤버 이름" recorded

``.zip`` and tar variants use the stdlib; ``.7z`` (py7zr) and ``.rar``
(rarfile + unrar binary) are optional and degrade to a warning.
"""

from __future__ import annotations

import shutil
import tarfile
import unicodedata
import zlib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import IO, Any

from .error_text import exception_text, failure_reason

SUPPORTED_ARCHIVE_EXTENSIONS = {
    ".zip", ".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tbz2",
    ".tar.xz", ".txz", ".7z", ".rar",
}

# Per-archive limits (one container level).
MAX_ARCHIVE_MEMBERS = 1000
MAX_ARCHIVE_TOTAL_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_RATIO = 200
# Archive levels expanded below the top-level archive, the top included
# (N14: was 2 — an archive three levels deep was "판단 불가" because its
# innermost files were never reached). 8 levels stay well inside the tree
# budget below (50 nested archives, 5000 members, 2 GiB); a deeper archive
# is still reported per member: "중첩 압축 최대 깊이(8) 초과로 미해제".
MAX_NESTED_DEPTH = 8

# Aggregate limits across one top-level archive and everything nested in it
# (phase-0 spec WP-H, G34): 2 GiB written, 5000 members, 50 nested archives.
# The per-archive limits above apply to each level; these bound the tree —
# 100 inner zips of 512 MB each would otherwise write 51 GB.
TOTAL_EXTRACTION_BYTES = 2 * 1024 * 1024 * 1024
TOTAL_EXTRACTION_MEMBERS = 5000
TOTAL_NESTED_ARCHIVES = 50

_COPY_CHUNK_BYTES = 1024 * 1024

# Windows device names that can never be created as files.
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


@dataclass
class ArchiveExtraction:
    """Extracted member paths plus every member that was not extracted.

    ``rejected`` holds one ``(member name, reason)`` per refused member
    (D9): path traversal, absolute path, link/device entry, budget or bomb
    limits, corrupt data, nesting limits. Names of members of nested
    archives are prefixed ``<inner archive>::``. ``skipped`` is its count.

    Y9 (round 7): an extracted member's *name* follows the same chain —
    ``inner/b.zip::c.png`` for ``c.png`` inside ``inner/b.zip`` (any depth:
    ``b.zip::c.zip::d.png``) — :meth:`member_name`. The scan row is
    ``<archive>::<member name>``. Nested archives are still unpacked into
    ``<inner archive>.unpacked/`` folders on disk, but that folder name never
    reaches a row (it used to: ``a.zip::inner/b.zip.unpacked/c.png``).
    """

    members: list[Path] = field(default_factory=list)
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    # The container could not be opened: a missing optional extractor
    # (py7zr / rarfile) or "<ExceptionClass>: <message>" of the parse error.
    # Only the top-level archive's own state; nested failures stay warnings.
    missing_dependency: str | None = None
    error: str | None = None
    # Y9: chain names of members extracted from nested archives. R12-3: every
    # member has one — its path inside the archive (``#2`` … for a duplicate),
    # never its index-numbered extraction folder.
    names: dict[Path, str] = field(default_factory=dict)
    # R12-3: the "중복 멤버 이름" reason of a member renamed ``<path>#n``.
    notes: dict[Path, str] = field(default_factory=dict)
    # R12-3: normalized member path (NFC, case-folded) -> first raw name that claimed it.
    claimed: dict[str, str] = field(default_factory=dict)

    def member_name(self, member: Path, root: Path) -> str:
        """``member``'s name inside the archive: ``dir/x.png`` or ``dir/inner.zip::x.png`` (Y9)."""
        return self.names.get(member) or _member_rel(member, root)

    def claim(self, rel: str, raw: str) -> tuple[str, str | None]:
        """R12-3: the row name for member path ``rel`` and, for a duplicate, the reason.

        The first member normalizing to a path keeps it; a later one —
        identical after separator/``./`` normalization, or differing only
        in letter case or Unicode normalization — becomes ``<rel>#2``
        (``#3`` …, skipping names already taken).
        """
        key = _claim_key(rel)
        first = self.claimed.get(key)
        if first is None:
            self.claimed[key] = raw
            return rel, None
        number = 2
        while _claim_key(f"{rel}#{number}") in self.claimed:
            number += 1
        name = f"{rel}#{number}"
        self.claimed[_claim_key(name)] = raw
        same = "같은 이름" if raw == first else "구분자·'./'·대소문자·유니코드 정규화 후 같은 경로"
        reason = (
            f"중복 멤버 이름: '{raw}'이(가) 앞선 멤버 '{first}'와 {same}입니다 — "
            f"덮어쓰지 않고 '{name}'(으)로 구분해 따로 해제·분석했습니다"
        )
        self.warnings.append(reason)
        return name, reason

    def reject(self, name: str, reason: str) -> None:
        self.skipped += 1
        self.rejected.append((name, reason))


def budget_reason(declared: int, limit: int, detail: str = "") -> str:
    """"압축 예산 초과(선언 크기 N, 한도 M)" (+ detail) — the D9 wording."""
    return f"압축 예산 초과(선언 크기 {declared}, 한도 {max(0, limit)})" + (f" — {detail}" if detail else "")


def _unsafe_name_reason(name: str) -> str:
    """Why ``_safe_member_name`` refused ``name``."""
    normalized = name.replace("\\", "/").strip()
    if not normalized:
        return "빈 멤버 이름"
    if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":") or normalized[0] == "~":
        return "절대 경로 멤버(대상 폴더 밖 쓰기 시도)"
    if any(part == ".." for part in PurePosixPath(normalized).parts):
        return "경로 이탈 멤버('..' — 대상 폴더 밖 쓰기 시도)"
    if ":" in normalized:
        return "허용되지 않는 이름(드라이브 문자·ADS ':' 포함)"
    return "허용되지 않는 이름(Windows 예약 장치명)"


def _claim_key(rel: str) -> str:
    return unicodedata.normalize("NFC", rel).casefold()


def _member_target(dest: Path, index: int, rel: str) -> Path | None:
    """R12-3: ``<dest>/<index>/<base name>`` — one folder per archive entry, never shared."""
    return _dest_for(dest, f"{index:05d}/{PurePosixPath(rel).name}")


def _place(out: ArchiveExtraction, target: Path, rel: str, raw: str) -> None:
    """Record the extracted ``target``'s row name (and duplicate reason)."""
    name, note = out.claim(rel, raw)
    out.names[target] = name
    if note:
        out.notes[target] = note


# R12-3: every 7z entry whose exact name another entry also uses.
SEVEN_ZIP_DUPLICATE_REASON = (
    "중복 멤버 이름: 같은 이름의 7z 멤버가 둘 이상입니다 — 7z 해제기(py7zr)는 이름으로만 꺼낼 수 있어 "
    "어느 내용이 어느 멤버인지 구분할 수 없으므로 덮어쓰지 않고 모두 미해제"
)


def _reject_rest(out: ArchiveExtraction, names: list[str], reason: str) -> None:
    for name in names:
        out.reject(name, reason)


@dataclass
class ExtractionBudget:
    """Aggregate extraction allowance for one top-level archive tree (G34).

    Every extractor charges the bytes it actually writes and each member it
    keeps; ``extract_archive`` charges each nested archive it opens. Once a
    dimension is exhausted the remaining members are skipped with a warning.
    """

    total_bytes: int = TOTAL_EXTRACTION_BYTES
    total_members: int = TOTAL_EXTRACTION_MEMBERS
    nested_archives: int = TOTAL_NESTED_ARCHIVES
    bytes_used: int = 0
    members_used: int = 0
    nested_used: int = 0
    exhausted_warned: set[str] = field(default_factory=set)

    @classmethod
    def default(cls) -> "ExtractionBudget":
        """A fresh budget from the module limits (read at call time)."""
        return cls(
            total_bytes=TOTAL_EXTRACTION_BYTES,
            total_members=TOTAL_EXTRACTION_MEMBERS,
            nested_archives=TOTAL_NESTED_ARCHIVES,
        )

    def bytes_left(self) -> int:
        return max(0, self.total_bytes - self.bytes_used)

    def members_left(self) -> int:
        return max(0, self.total_members - self.members_used)

    def nested_left(self) -> int:
        return max(0, self.nested_archives - self.nested_used)

    def note_exhausted(self, kind: str, out: ArchiveExtraction) -> None:
        """Warn once per budget dimension per tree."""
        if kind in self.exhausted_warned:
            return
        self.exhausted_warned.add(kind)
        if kind == "bytes":
            out.warnings.append(f"압축 해제 총량 예산({self.total_bytes // (1024 * 1024)}MB) 소진 — 나머지 생략")
        elif kind == "members":
            out.warnings.append(f"압축 해제 멤버 수 예산({self.total_members}) 소진 — 나머지 생략")
        else:
            out.warnings.append(f"중첩 압축 예산({self.nested_archives}개) 소진 — 나머지 중첩 압축 미해제")


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


def _member_cap(per_archive_total: int, budget: ExtractionBudget) -> int:
    """Bytes the next member may write: per-member, per-archive and tree caps."""
    return max(0, min(MAX_ARCHIVE_MEMBER_BYTES, MAX_ARCHIVE_TOTAL_BYTES - per_archive_total, budget.bytes_left()))


def _member_allowed(out: ArchiveExtraction, budget: ExtractionBudget) -> str | None:
    """None, or (with a warning) the reason the member cap stops extraction."""
    if len(out.members) >= MAX_ARCHIVE_MEMBERS:
        out.warnings.append(f"멤버 수 상한({MAX_ARCHIVE_MEMBERS}) 도달 — 나머지 생략")
        return f"멤버 수 상한({MAX_ARCHIVE_MEMBERS}) 도달로 미해제"
    if budget.members_left() <= 0:
        budget.note_exhausted("members", out)
        return f"압축 해제 멤버 수 예산({budget.total_members}) 소진으로 미해제"
    return None


def _declared_fits(declared: int, per_archive_total: int, out: ArchiveExtraction, budget: ExtractionBudget) -> str | None:
    """None, or (with a warning) the reason a member's declared size breaks a total cap."""
    if per_archive_total + declared > MAX_ARCHIVE_TOTAL_BYTES:
        out.warnings.append(f"해제 총량 상한({MAX_ARCHIVE_TOTAL_BYTES // (1024 * 1024)}MB) 도달 — 나머지 생략")
        return budget_reason(declared, MAX_ARCHIVE_TOTAL_BYTES - per_archive_total, "압축 파일당 해제 총량 상한")
    if declared > budget.bytes_left():
        budget.note_exhausted("bytes", out)
        return budget_reason(declared, budget.bytes_left(), "압축 해제 총량 예산 소진")
    return None


def _size_reason(declared: int, compressed: int | None = None) -> str | None:
    """Per-member bomb checks on declared sizes: member cap and ratio cap."""
    if declared > MAX_ARCHIVE_MEMBER_BYTES:
        return budget_reason(declared, MAX_ARCHIVE_MEMBER_BYTES, "멤버당 크기 상한")
    if compressed and declared // max(1, compressed) > MAX_ARCHIVE_RATIO:
        return budget_reason(
            declared, compressed * MAX_ARCHIVE_RATIO,
            f"압축률 {declared // max(1, compressed)}:1 > {MAX_ARCHIVE_RATIO}:1 (압축 폭탄 의심)",
        )
    return None


def _copy_capped(src: IO[bytes], target: Path, cap: int) -> tuple[int, bool]:
    """Stream ``src`` into ``target`` writing at most ``cap`` bytes.

    Returns ``(written, truncated)``. Declared member sizes can lie (forged
    local/central headers), so the cap applies to bytes actually produced.
    """
    written = 0
    truncated = False
    with target.open("wb") as dst:
        while True:
            chunk = src.read(_COPY_CHUNK_BYTES)
            if not chunk:
                break
            remaining = cap - written
            if len(chunk) > remaining:
                dst.write(chunk[:remaining])
                written += remaining
                truncated = True
                break
            dst.write(chunk)
            written += len(chunk)
    return written, truncated


def _charge(out: ArchiveExtraction, budget: ExtractionBudget, target: Path, rel: str, written: int, truncated: bool) -> None:
    budget.bytes_used += written
    budget.members_used += 1
    if truncated:
        out.warnings.append(f"{rel}: 크기 상한 초과로 일부만 해제됨")
    out.members.append(target)


def _extract_zip(path: Path, dest: Path, out: ArchiveExtraction, budget: ExtractionBudget) -> None:
    total = 0
    with zipfile.ZipFile(path) as zf:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        for index, info in enumerate(infos):
            name = _zip_member_name(info)
            stop = _member_allowed(out, budget)
            if stop:
                _reject_rest(out, [_zip_member_name(i) for i in infos[index:]], stop)
                break
            if _is_zip_symlink(info):
                out.reject(name, "심볼릭 링크 멤버")
                continue
            rel = _safe_member_name(name)
            if rel is None:
                out.reject(name, _unsafe_name_reason(name))
                continue
            too_big = _size_reason(info.file_size, info.compress_size)
            if too_big:
                out.reject(name, too_big)
                continue
            stop = _declared_fits(info.file_size, total, out, budget)
            if stop:
                out.reject(name, stop)
                _reject_rest(out, [_zip_member_name(i) for i in infos[index + 1:]], "앞선 멤버에서 해제 예산 소진으로 미해제")
                break
            target = _member_target(dest, index, rel)
            if target is None:
                out.reject(name, "경로 이탈 멤버(해석 결과가 대상 폴더 밖)")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info) as src:
                    written, truncated = _copy_capped(src, target, _member_cap(total, budget))
            except (OSError, zipfile.BadZipFile, RuntimeError, EOFError, ValueError, zlib.error) as exc:
                # Corrupt member data (bad CRC, truncated stream, encrypted):
                # drop the partial file so it is neither analyzed nor left
                # occupying budget-free disk space.
                target.unlink(missing_ok=True)
                out.reject(name, f"손상된 멤버 데이터({type(exc).__name__}: {exception_text(exc, 120)})")
                continue
            total += written
            _place(out, target, rel, name)
            _charge(out, budget, target, rel, written, truncated)


def _extract_tar(path: Path, dest: Path, out: ArchiveExtraction, budget: ExtractionBudget) -> None:
    total = 0
    with tarfile.open(path) as tf:
        members = [member for member in tf.getmembers() if not member.isdir()]
        for index, member in enumerate(members):
            stop = _member_allowed(out, budget)
            if stop:
                _reject_rest(out, [m.name for m in members[index:]], stop)
                break
            # Symlinks, hardlinks, devices and FIFOs are never materialized:
            # only regular files are copied, and only via extractfile().
            if not member.isreg():
                kind = "심볼릭 링크 멤버" if member.issym() else "하드 링크 멤버" if member.islnk() else "일반 파일이 아닌 멤버(장치·FIFO)"
                out.reject(member.name, kind)
                continue
            rel = _safe_member_name(member.name)
            if rel is None:
                out.reject(member.name, _unsafe_name_reason(member.name))
                continue
            too_big = _size_reason(member.size)
            if too_big:
                out.reject(member.name, too_big)
                continue
            stop = _declared_fits(member.size, total, out, budget)
            if stop:
                out.reject(member.name, stop)
                _reject_rest(out, [m.name for m in members[index + 1:]], "앞선 멤버에서 해제 예산 소진으로 미해제")
                break
            target = _member_target(dest, index, rel)
            if target is None:
                out.reject(member.name, "경로 이탈 멤버(해석 결과가 대상 폴더 밖)")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                src = tf.extractfile(member)
                if src is None:
                    out.reject(member.name, "멤버 데이터를 열 수 없음")
                    continue
                with src:
                    written, truncated = _copy_capped(src, target, _member_cap(total, budget))
            except (OSError, tarfile.TarError, EOFError, zlib.error) as exc:
                target.unlink(missing_ok=True)
                out.reject(member.name, f"손상된 멤버 데이터({type(exc).__name__}: {exception_text(exc, 120)})")
                continue
            total += written
            _place(out, target, rel, member.name)
            _charge(out, budget, target, rel, written, truncated)


def _flag(entry: Any, name: str) -> bool:
    """Read a bool attribute or zero-arg predicate method from a library entry."""
    value = getattr(entry, name, False)
    if callable(value):
        try:
            value = value()
        except TypeError:
            return False
    return bool(value)


def _7z_link_names(sf: Any) -> set[str]:
    """Names of 7z entries that are symlinks/junctions/devices (never extracted).

    py7zr exposes per-entry ``is_symlink``/``is_junction``/``is_socket`` on
    ``SevenZipFile.files``; ``list()`` rows may carry ``is_symlink`` too.
    """
    names: set[str] = set()
    entries = list(getattr(sf, "files", None) or [])
    for entry in entries:
        if any(_flag(entry, attr) for attr in ("is_symlink", "is_junction", "is_socket")):
            names.add(str(getattr(entry, "filename", "")))
    return names


def _extract_7z(path: Path, dest: Path, out: ArchiveExtraction, budget: ExtractionBudget) -> None:
    try:
        import py7zr
    except ImportError:
        out.warnings.append("7z 해제에는 py7zr이 필요합니다 (`pip install deepfake-lens[archive]`)")
        out.missing_dependency = "py7zr"
        return
    total = 0
    try:
        with py7zr.SevenZipFile(path) as sf:
            links = _7z_link_names(sf)
            entries = [i for i in sf.list() if not i.is_directory]
            names = [str(i.filename) for i in entries]
            repeated = {name for name in names if names.count(name) > 1}
            planned: list[tuple[int, str, str]] = []  # (entry index, raw name, normalized path)
            for index, info in enumerate(entries):
                name = names[index]
                if len(planned) >= MAX_ARCHIVE_MEMBERS:
                    out.warnings.append(f"멤버 수 상한({MAX_ARCHIVE_MEMBERS}) 도달 — 나머지 생략")
                    _reject_rest(out, names[index:], f"멤버 수 상한({MAX_ARCHIVE_MEMBERS}) 도달로 미해제")
                    break
                if len(planned) >= budget.members_left():
                    budget.note_exhausted("members", out)
                    _reject_rest(out, names[index:], f"압축 해제 멤버 수 예산({budget.total_members}) 소진으로 미해제")
                    break
                if name in links or _flag(info, "is_symlink"):
                    out.reject(name, "심볼릭 링크 멤버")
                    continue
                if name in repeated:
                    # R12-3: py7zr extracts by name only — entries sharing one exact
                    # name land on one file (the last wins), so which bytes belong to
                    # which entry is unknowable: none of them is extracted.
                    out.reject(name, SEVEN_ZIP_DUPLICATE_REASON)
                    continue
                rel = _safe_member_name(name)
                declared = int(getattr(info, "uncompressed", 0) or 0)
                if rel is None:
                    out.reject(name, _unsafe_name_reason(name))
                    continue
                too_big = _size_reason(declared)
                if too_big:
                    out.reject(name, too_big)
                    continue
                stop = _declared_fits(declared, total, out, budget)
                if stop:
                    out.reject(name, stop)
                    _reject_rest(out, names[index + 1:], "앞선 멤버에서 해제 예산 소진으로 미해제")
                    break
                total += declared
                planned.append((index, name, rel))
            # R12-3: members whose paths collide on a case-insensitive file
            # system go to separate batch folders, then each moves to its own
            # index-numbered folder.
            batches: list[list[tuple[int, str, str]]] = []
            for item in planned:
                key = _claim_key(item[2])
                for batch in batches:
                    if all(_claim_key(other[2]) != key for other in batch):
                        batch.append(item)
                        break
                else:
                    batches.append([item])
            for number, batch in enumerate(batches):
                if number:
                    sf.reset()
                batch_dir = dest / f"7z-batch-{number:03d}"
                sf.extract(batch_dir, targets=[name for _, name, _ in batch])
                for index, name, rel in batch:
                    extracted = _dest_for(batch_dir, rel)
                    target = _member_target(dest, index, rel)
                    if extracted is None or target is None or extracted.is_symlink() or not extracted.is_file():
                        if extracted is not None and extracted.is_symlink():
                            extracted.unlink(missing_ok=True)
                        out.reject(name, "해제 결과가 일반 파일이 아님(링크·누락)")
                        continue
                    size = extracted.stat().st_size
                    if size > budget.bytes_left():
                        # Headers under-declared the payload: never keep bytes
                        # beyond the tree budget.
                        extracted.unlink(missing_ok=True)
                        budget.note_exhausted("bytes", out)
                        out.reject(name, budget_reason(size, budget.bytes_left(), "헤더가 실제 크기를 축소 선언"))
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    extracted.replace(target)
                    _place(out, target, rel, name)
                    _charge(out, budget, target, rel, size, False)
                shutil.rmtree(batch_dir, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001 - py7zr raises several custom error types
        out.warnings.append(f"7z 해제 실패: {failure_reason(exc)}")
        out.error = failure_reason(exc)


def _rar_member_rejected(info: Any) -> bool:
    """True for rar entries that are not plain regular files.

    rarfile >= 4 exposes ``is_symlink()`` and ``is_file()``; RAR5 hardlinks,
    file copies and junctions carry a ``file_redir`` tuple. Anything that is
    not a regular file is refused (fail-closed), not followed.
    """
    if _flag(info, "is_symlink"):
        return True
    if getattr(info, "file_redir", None):
        return True
    is_file = getattr(info, "is_file", None)
    if callable(is_file):
        try:
            return not bool(is_file())
        except TypeError:
            return True
    return False


def _extract_rar(path: Path, dest: Path, out: ArchiveExtraction, budget: ExtractionBudget) -> None:
    try:
        import rarfile
    except ImportError:
        out.warnings.append("rar 해제에는 rarfile이 필요합니다 (`pip install deepfake-lens[archive]`)")
        out.missing_dependency = "rarfile"
        return
    total = 0
    try:
        with rarfile.RarFile(path) as rf:
            infos = [info for info in rf.infolist() if not (_flag(info, "isdir") or _flag(info, "is_dir"))]
            for index, info in enumerate(infos):
                name = str(info.filename)
                stop = _member_allowed(out, budget)
                if stop:
                    _reject_rest(out, [str(i.filename) for i in infos[index:]], stop)
                    break
                if _rar_member_rejected(info):
                    out.reject(name, "링크·리다이렉트 또는 일반 파일이 아닌 멤버")
                    continue
                rel = _safe_member_name(name)
                declared = int(getattr(info, "file_size", 0) or 0)
                if rel is None:
                    out.reject(name, _unsafe_name_reason(name))
                    continue
                too_big = _size_reason(declared)
                if too_big:
                    out.reject(name, too_big)
                    continue
                stop = _declared_fits(declared, total, out, budget)
                if stop:
                    out.reject(name, stop)
                    _reject_rest(out, [str(i.filename) for i in infos[index + 1:]], "앞선 멤버에서 해제 예산 소진으로 미해제")
                    break
                target = _member_target(dest, index, rel)
                if target is None:
                    out.reject(name, "경로 이탈 멤버(해석 결과가 대상 폴더 밖)")
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with rf.open(info) as src:
                        written, truncated = _copy_capped(src, target, _member_cap(total, budget))
                except Exception as exc:  # noqa: BLE001 - rarfile/unrar errors vary by backend
                    target.unlink(missing_ok=True)
                    out.reject(name, f"손상된 멤버 데이터({type(exc).__name__}: {exception_text(exc, 120)})")
                    continue
                total += written
                _place(out, target, rel, name)
                _charge(out, budget, target, rel, written, truncated)
    except Exception as exc:  # noqa: BLE001 - rarfile raises several custom error types
        out.warnings.append(f"rar 해제 실패: {failure_reason(exc)}")
        out.error = failure_reason(exc)


def extract_archive(
    path: Path | str,
    dest: Path | str,
    *,
    max_depth: int = MAX_NESTED_DEPTH,
    budget: ExtractionBudget | None = None,
    _depth: int = 0,
) -> ArchiveExtraction:
    """Extract archive members into ``dest`` and return real file paths.

    Nested archives inside the archive are re-expanded up to ``max_depth``
    levels; deeper ones are counted as skipped. ``budget`` bounds the whole
    tree (bytes written, members kept, nested archives opened); a top-level
    call creates a fresh :meth:`ExtractionBudget.default` and passes it down.
    Never raises for a malformed archive — problems land in ``warnings``.
    """
    src = Path(path)
    root = Path(dest)
    root.mkdir(parents=True, exist_ok=True)
    tree_budget = budget if budget is not None else ExtractionBudget.default()
    out = ArchiveExtraction()
    fmt = archive_format(src)
    try:
        if fmt == "zip":
            _extract_zip(src, root, out, tree_budget)
        elif fmt == "tar":
            _extract_tar(src, root, out, tree_budget)
        elif fmt == "7z":
            _extract_7z(src, root, out, tree_budget)
        elif fmt == "rar":
            _extract_rar(src, root, out, tree_budget)
        else:
            out.warnings.append("지원하지 않는 압축 형식입니다.")
            return out
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError, ValueError) as exc:
        out.warnings.append(f"압축 해제 실패: {failure_reason(exc)}")
        out.error = failure_reason(exc)
        return out

    nested = [m for m in out.members if is_archive(m)]
    if nested:
        if _depth + 1 >= max_depth:
            out.warnings.append(f"중첩 압축 {len(nested)}개 — 최대 깊이({max_depth})로 미해제")
            for m in nested:
                out.members.remove(m)
                out.reject(out.member_name(m, root), f"중첩 압축 최대 깊이({max_depth}) 초과로 미해제")
        else:
            for m in nested:
                inner_rel = out.member_name(m, root)
                if tree_budget.nested_left() <= 0:
                    tree_budget.note_exhausted("nested", out)
                    out.members.remove(m)
                    out.reject(inner_rel, f"중첩 압축 예산({tree_budget.nested_archives}개) 소진으로 미해제")
                    continue
                tree_budget.nested_used += 1
                # Unpack next to the inner archive (inside root) so two inner
                # archives with the same stem in different folders never
                # share — and overwrite — one extraction directory.
                sub_root = m.parent / (m.name + ".unpacked")
                sub = extract_archive(
                    m, sub_root,
                    max_depth=max_depth, budget=tree_budget, _depth=_depth + 1,
                )
                if sub.members:
                    out.members.remove(m)
                    out.members.extend(sub.members)
                    # Y9: "<inner archive>::<member>" — the rejection chain's form.
                    for member in sub.members:
                        out.names[member] = f"{inner_rel}::{sub.member_name(member, sub_root)}"
                        if member in sub.notes:
                            out.notes[member] = sub.notes[member]
                out.skipped += sub.skipped
                out.rejected.extend((f"{inner_rel}::{name}", reason) for name, reason in sub.rejected)
                out.warnings.extend(sub.warnings)
    return out


def _member_rel(member: Path, root: Path) -> str:
    """``member``'s path inside the archive (extraction targets are resolved)."""
    for base in (root, root.resolve()):
        try:
            return member.relative_to(base).as_posix()
        except ValueError:
            continue
    return member.name
