"""Reproducible evaluation-corpus manifests (schema ``corpus-manifest-v1``).

Phase 0, WP-I (G27): every performance number must name the exact files it
was measured on. A manifest pins each file by content hash, records its
label/generator/variant and its train/val/test split, and carries a
``manifest_sha256`` over the canonical item list so a profile's
``measured_on.manifest_sha256`` identifies one corpus state exactly.

Manifest shape::

    {
      "schema": "corpus-manifest-v1",
      "corpus_id": "t-img-2026q4",
      "created": "2026-10-09T00:00:00Z",
      "items": [
        {"id": "…", "relpath": "real/camera/original/a.jpg", "sha256": "…",
         "modality": "image", "label": "real", "generator": "camera",
         "variant": "original", "split": "test", "source_note": "",
         "derived_from": null}
      ],
      "manifest_sha256": "…"
    }

``label`` is ``real`` | ``synthetic`` | ``edited`` (``null`` only while a
corpus is still unlabeled — ``verify`` reports it). ``split`` is ``train`` |
``val`` | ``test`` (``null`` until ``corpus split`` runs). ``variant`` is
free text: ``original``, ``kakao``, ``telegram``, ``instagram``,
``jpeg_q50`` … ``derived_from`` (optional) names the original an item was
made from — another item's ``id`` or ``relpath``, or the 64-hex sha256 of
an original file that is not itself in the corpus.

Directory convention for ``corpus build --label-from-dir``::

    <root>/<label>/<generator>/<variant>/<file>

e.g. ``real/galaxy-s23/kakao/IMG_0001.jpg`` or
``synthetic/midjourney-v6/original/0001.png``. Paths shallower than that
get ``generator="unknown"`` / ``variant="original"``. A non-original
variant whose file stem matches a file in the sibling ``original/``
directory (same label and generator) gets ``derived_from`` set to that
original's id, so ``corpus split`` keeps the pair in one split.

``manifest_sha256`` is the SHA-256 of the canonical JSON of ``items``
(sorted by ``id``, keys sorted, ``separators=(",", ":")``, UTF-8, no ASCII
escaping). ``corpus_id``/``created`` are outside the hash: renaming a
corpus does not change what was measured, re-splitting it does.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from .json_text import json_dumps

SCHEMA = "corpus-manifest-v1"
LABELS = ("real", "synthetic", "edited")
SPLITS = ("train", "val", "test")
ITEM_FIELDS = ("id", "relpath", "sha256", "modality", "label", "generator", "variant", "split", "source_note", "derived_from")
# Directory names accepted for --label-from-dir besides the canonical labels.
LABEL_ALIASES = {
    "real": "real",
    "authentic": "real",
    "original": "real",
    "synthetic": "synthetic",
    "fake": "synthetic",
    "ai": "synthetic",
    "generated": "synthetic",
    "edited": "edited",
    "manipulated": "edited",
    "faceswap": "edited",
}
MODALITY_BY_SUFFIX = {
    **{s: "image" for s in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic", ".heif")},
    **{s: "video" for s in (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".3gp")},
    **{s: "audio" for s in (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".amr")},
    **{s: "document" for s in (".pdf", ".docx", ".doc", ".hwp", ".hwpx", ".pptx", ".xlsx", ".odt", ".rtf")},
    **{s: "text" for s in (".txt", ".md")},
}
DEFAULT_GROUP_BY = "origin"
DEFAULT_RATIO = "60/20/20"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HASH_CHUNK = 1024 * 1024


class ManifestError(ValueError):
    """A manifest that cannot be built, read or split; message is Korean."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def item_id(relpath: str) -> str:
    """Stable item id: first 16 hex of SHA-256 over the POSIX relpath."""
    # R11-1: surrogateescape — a non-UTF-8 name hashes as its own bytes.
    return hashlib.sha256(relpath.encode("utf-8", "surrogateescape")).hexdigest()[:16]


def canonical_items_bytes(items: list[dict[str, Any]]) -> bytes:
    ordered = sorted((_normalized_item(item) for item in items), key=lambda item: str(item["id"]))
    return json_dumps(ordered, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def manifest_sha256(items: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_items_bytes(items)).hexdigest()


def is_hex64(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX64.match(value))


def _normalized_item(item: dict[str, Any]) -> dict[str, Any]:
    """Exactly the schema fields, missing ones as None, so hashes are stable."""
    return {key: item.get(key) for key in ITEM_FIELDS}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _label_from_parts(parts: tuple[str, ...]) -> tuple[str | None, str, str]:
    """``(label, generator, variant)`` from ``<label>/<generator>/<variant>/file``."""
    directories = parts[:-1]
    label = LABEL_ALIASES.get(directories[0].lower()) if directories else None
    generator = directories[1] if len(directories) >= 2 else "unknown"
    variant = directories[2] if len(directories) >= 3 else "original"
    return label, generator, variant


def build_manifest(
    root: Path | str,
    *,
    corpus_id: str | None = None,
    label_from_dir: bool = False,
    source_note: str = "",
    exclude: tuple[Path, ...] = (),
) -> tuple[dict[str, Any], list[str]]:
    """Hash every supported file under ``root`` into a manifest.

    Returns ``(manifest, skipped_relpaths)``. Files with unknown suffixes and
    hidden files are skipped (and listed); with ``label_from_dir`` a top
    directory that is not a known label is an error, not a guess.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        raise ManifestError(f"코퍼스 디렉터리가 없습니다: {root_path}")
    excluded = {path.resolve() for path in exclude}
    items: list[dict[str, Any]] = []
    skipped: list[str] = []
    for path in sorted(root_path.rglob("*"), key=lambda p: p.as_posix()):
        if not path.is_file() or path.resolve() in excluded:
            continue
        relpath = path.relative_to(root_path).as_posix()
        parts = tuple(relpath.split("/"))
        if any(part.startswith(".") for part in parts) or path.name.upper() == "README.MD":
            skipped.append(relpath)
            continue
        modality = MODALITY_BY_SUFFIX.get(path.suffix.lower())
        if modality is None:
            skipped.append(relpath)
            continue
        label: str | None = None
        generator, variant = "unknown", "original"
        if label_from_dir:
            label, generator, variant = _label_from_parts(parts)
            if label is None:
                raise ManifestError(
                    f"라벨을 디렉터리에서 읽을 수 없습니다: {relpath} — 최상위 디렉터리는 "
                    f"{'/'.join(LABELS)} 중 하나여야 합니다(<label>/<generator>/<variant>/파일)."
                )
        items.append(
            {
                "id": item_id(relpath),
                "relpath": relpath,
                "sha256": file_sha256(path),
                "modality": modality,
                "label": label,
                "generator": generator,
                "variant": variant,
                "split": None,
                "source_note": source_note,
                "derived_from": None,
            }
        )
    if label_from_dir:
        _infer_derived_from(items)
    manifest = {
        "schema": SCHEMA,
        "corpus_id": corpus_id or root_path.resolve().name,
        "created": _utc_now(),
        "items": items,
        "manifest_sha256": manifest_sha256(items),
    }
    return manifest, skipped


def _infer_derived_from(items: list[dict[str, Any]]) -> None:
    """Link ``<label>/<gen>/<variant>/x.*`` to ``<label>/<gen>/original/x.*``."""
    originals: dict[tuple[str, str, str], str] = {}
    for item in items:
        if item["variant"] == "original":
            key = (str(item["label"]), str(item["generator"]), Path(str(item["relpath"])).stem)
            originals.setdefault(key, str(item["id"]))
    for item in items:
        if item["variant"] == "original":
            continue
        key = (str(item["label"]), str(item["generator"]), Path(str(item["relpath"])).stem)
        if key in originals:
            item["derived_from"] = originals[key]


def write_manifest(path: Path | str, manifest: dict[str, Any], *, root_hint: str | None = None) -> None:
    payload = dict(manifest)
    payload["manifest_sha256"] = manifest_sha256(list(payload["items"]))
    if root_hint is not None:
        payload["root_hint"] = root_hint
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json_dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_manifest(path: Path | str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"매니페스트를 읽을 수 없습니다: {path} ({exc})") from exc
    except (json.JSONDecodeError, RecursionError) as exc:  # R10-5: too-deep nesting is a damaged manifest
        raise ManifestError(f"매니페스트 JSON이 손상되었습니다: {path} ({exc})") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ManifestError(f"스키마가 {SCHEMA}가 아닙니다: {path}")
    if not isinstance(payload.get("items"), list):
        raise ManifestError(f"items 배열이 없습니다: {path}")
    return payload


def parse_ratio(text: str) -> tuple[float, float, float]:
    """``"60/20/20"`` (or ``0.6,0.2,0.2``) → normalized train/val/test fractions."""
    parts = [part for part in re.split(r"[/,:]", text.strip()) if part.strip()]
    if len(parts) != 3:
        raise ManifestError(f"비율은 train/val/test 세 값이어야 합니다: {text!r}")
    try:
        values = [float(part) for part in parts]
    except ValueError as exc:
        raise ManifestError(f"비율에 숫자가 아닌 값이 있습니다: {text!r}") from exc
    if any(value < 0 for value in values) or sum(values) <= 0:
        raise ManifestError(f"비율은 음수가 아니고 합이 0보다 커야 합니다: {text!r}")
    total = sum(values)
    return values[0] / total, values[1] / total, values[2] / total


def origin_key(item: dict[str, Any], by_ref: dict[str, dict[str, Any]]) -> str:
    """Group key tying an item to the original file it derives from.

    Follows ``derived_from`` (item id, relpath, or a bare sha256) to the
    root original and returns ``sha256:<root sha256>``; an item with no
    ``derived_from`` is its own root. Exact duplicate files therefore also
    share a group — a leak-safe default.
    """
    current = item
    seen: set[str] = set()
    while True:
        ref = current.get("derived_from")
        if not ref:
            return f"sha256:{current.get('sha256') or current.get('id')}"
        ref = str(ref)
        if ref in seen:
            raise ManifestError(f"derived_from 순환 참조: {ref}")
        seen.add(ref)
        target = by_ref.get(ref)
        if target is None:
            if is_hex64(ref):
                return f"sha256:{ref}"
            raise ManifestError(f"derived_from이 가리키는 항목이 없습니다: {ref} (항목 {current.get('id')})")
        current = target


def _refs(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_ref: dict[str, dict[str, Any]] = {}
    for item in items:
        by_ref[str(item.get("id"))] = item
        by_ref[str(item.get("relpath"))] = item
    return by_ref


def group_key(item: dict[str, Any], group_by: str, by_ref: dict[str, dict[str, Any]]) -> str:
    if group_by == DEFAULT_GROUP_BY:
        return origin_key(item, by_ref)
    value = item.get(group_by)
    if value is None or value == "":
        # Items lacking the field fall back to their origin, never to a
        # shared "missing" bucket that would glue unrelated items together.
        return origin_key(item, by_ref)
    return f"{group_by}:{value}"


@dataclass(frozen=True)
class SplitReport:
    groups: int
    counts: dict[str, dict[str, int]]

    def to_json(self) -> dict[str, object]:
        return {"groups": self.groups, "counts": self.counts}


def assign_splits(
    manifest: dict[str, Any],
    *,
    seed: int,
    ratio: tuple[float, float, float] = (0.6, 0.2, 0.2),
    group_by: str = DEFAULT_GROUP_BY,
) -> SplitReport:
    """Assign ``split`` in place; every item of one group lands in one split.

    Groups are visited in a seeded shuffle of their sorted keys and each goes
    to the split with the largest label-weighted deficit against the target
    ratio, which keeps per-label proportions close to the ratio even though
    groups move as a whole. Deterministic for a given seed and item set.
    """
    items: list[dict[str, Any]] = manifest["items"]
    if group_by != DEFAULT_GROUP_BY and group_by not in ITEM_FIELDS:
        raise ManifestError(f"알 수 없는 그룹 필드: {group_by} (가능: {DEFAULT_GROUP_BY}, {', '.join(ITEM_FIELDS)})")
    by_ref = _refs(items)
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(group_key(item, group_by, by_ref), []).append(item)
    keys = sorted(groups)
    random.Random(seed).shuffle(keys)
    label_totals: dict[str, int] = {}
    for item in items:
        label_totals[str(item.get("label"))] = label_totals.get(str(item.get("label")), 0) + 1
    targets = {
        split: {label: share * total for label, total in label_totals.items()}
        for split, share in zip(SPLITS, ratio)
    }
    assigned = {split: {label: 0 for label in label_totals} for split in SPLITS}
    for key in keys:
        members = groups[key]
        composition: dict[str, int] = {}
        for item in members:
            composition[str(item.get("label"))] = composition.get(str(item.get("label")), 0) + 1

        def need(split: str) -> float:
            return sum(
                count * (targets[split][label] - assigned[split][label]) / max(1, label_totals[label])
                for label, count in composition.items()
            )

        best = max(SPLITS, key=lambda split: (need(split), -SPLITS.index(split)))
        for item in members:
            item["split"] = best
        for label, count in composition.items():
            assigned[best][label] += count
    manifest["manifest_sha256"] = manifest_sha256(items)
    return SplitReport(groups=len(groups), counts=assigned)


RELPATH_OUTSIDE = "relpath가 코퍼스 밖을 가리킵니다: {relpath}"


def relpath_outside_corpus(relpath: str, root: Path | None = None) -> bool:
    """R12-8 (round 12): True when ``relpath`` can name a file outside the corpus root.

    Checked under both path flavours, whatever the OS running the check: a
    POSIX absolute path, a Windows drive (``C:\\x``, drive-relative ``C:x``),
    a rooted ``\\x``, a UNC ``\\\\server\\share``, a ``..`` part with either
    separator, an empty path or a NUL. Before R12-8 only ``/…`` and a POSIX
    ``..`` were refused, so ``C:\\x`` and ``\\x`` passed the check on Windows.
    With ``root``, the resolved path must also stay inside the resolved root
    (a symbolic link inside the corpus pointing out of it).
    """
    if not relpath or "\x00" in relpath:
        return True
    posix, windows = PurePosixPath(relpath), PureWindowsPath(relpath)
    if posix.is_absolute() or posix.root or windows.drive or windows.root or windows.is_absolute():
        return True
    if ".." in posix.parts or ".." in windows.parts:
        return True
    if root is not None:
        try:
            (root / relpath).resolve().relative_to(root.resolve())
        except (OSError, RuntimeError, ValueError):
            return True
    return False


def verify_manifest(manifest: dict[str, Any], root: Path | str) -> list[str]:
    """Re-hash every file and re-check ``manifest_sha256``; Korean problems."""
    problems: list[str] = []
    items: list[dict[str, Any]] = manifest.get("items", [])
    root_path = Path(root)
    declared = manifest.get("manifest_sha256")
    if not is_hex64(declared):
        problems.append("manifest_sha256이 64자리 16진수가 아닙니다.")
    elif declared != manifest_sha256(items):
        problems.append("manifest_sha256 불일치: 항목 목록이 매니페스트 작성 이후 바뀌었습니다.")
    seen_ids: set[str] = set()
    for item in items:
        ident = str(item.get("id"))
        relpath = str(item.get("relpath", ""))
        if ident in seen_ids:
            problems.append(f"중복 id: {ident}")
        seen_ids.add(ident)
        label = item.get("label")
        if label is None:
            problems.append(f"라벨 없음: {relpath}")
        elif label not in LABELS:
            problems.append(f"허용되지 않는 라벨 {label!r}: {relpath}")
        split = item.get("split")
        if split is not None and split not in SPLITS:
            problems.append(f"허용되지 않는 split {split!r}: {relpath}")
        if relpath_outside_corpus(relpath, root_path):
            problems.append(RELPATH_OUTSIDE.format(relpath=relpath))
            continue
        path = root_path / relpath
        if not path.is_file():
            problems.append(f"파일 없음: {relpath}")
            continue
        actual = file_sha256(path)
        if actual != item.get("sha256"):
            problems.append(f"해시 불일치: {relpath} (기록 {str(item.get('sha256'))[:12]}…, 실제 {actual[:12]}…)")
    return problems


def _resolve_root(manifest: dict[str, Any], manifest_path: Path, root: Path | None) -> Path:
    if root is not None:
        return root
    hint = manifest.get("root_hint")
    if isinstance(hint, str) and hint:
        candidate = Path(hint)
        if not candidate.is_absolute():
            candidate = manifest_path.parent / candidate
        if candidate.is_dir():
            return candidate
    return manifest_path.parent


def add_corpus_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``corpus build|split|verify`` on the CLI's subparsers."""
    corpus_parser: argparse.ArgumentParser = subparsers.add_parser(
        "corpus", help="재현 가능한 코퍼스 매니페스트(corpus-manifest-v1) 생성·분할·검증"
    )
    corpus_sub = corpus_parser.add_subparsers(dest="corpus_command", help="하위 명령: build(생성) | split(분할) | verify(검증)")
    build = corpus_sub.add_parser("build", help="폴더 아래 모든 파일의 해시로 매니페스트 생성")
    build.add_argument("folder", type=Path, help="코퍼스 최상위 폴더")
    build.add_argument("--out", type=Path, required=True, help="저장할 매니페스트 JSON")
    build.add_argument("--label-from-dir", action="store_true", help="<라벨>/<생성기>/<변형>/파일 경로에서 label/generator/variant를 읽음")
    build.add_argument("--corpus-id", help="코퍼스 식별자(기본: 폴더 이름)")
    build.add_argument("--source-note", default="", help="모든 항목에 복사할 출처 메모")
    split = corpus_sub.add_parser("split", help="train/val/test 배정(같은 원본의 변형은 같은 split)")
    split.add_argument("--manifest", type=Path, required=True, help="분할할 매니페스트 JSON")
    split.add_argument("--seed", type=int, required=True, help="분할 시드(정수)")
    split.add_argument("--ratio", default=DEFAULT_RATIO, help="train/val/test 비율(기본 60/20/20)")
    split.add_argument("--group-by", default=DEFAULT_GROUP_BY, help="값이 같으면 같은 split에 둘 항목 필드(기본: origin = derived_from으로 찾은 원본의 sha256)")
    split.add_argument("--out", type=Path, help="--manifest를 덮어쓰지 않고 이 파일에 저장")
    verify = corpus_sub.add_parser("verify", help="파일을 다시 해시해 manifest_sha256 확인")
    verify.add_argument("--manifest", type=Path, required=True, help="검증할 매니페스트 JSON")
    verify.add_argument("--root", type=Path, help="코퍼스 최상위 폴더(기본: 매니페스트의 root_hint, 없으면 매니페스트가 있는 폴더)")
    return corpus_parser


def run_corpus_cli(args: argparse.Namespace) -> int:
    """Dispatch ``deepfake-lens corpus …``; 0 ok, 1 verification failure, 2 usage error."""
    command = getattr(args, "corpus_command", None)
    try:
        if command == "build":
            manifest, skipped = build_manifest(
                args.folder,
                corpus_id=args.corpus_id,
                label_from_dir=args.label_from_dir,
                source_note=args.source_note,
                exclude=(args.out,),
            )
            root_hint = _relative_hint(args.folder, args.out)
            write_manifest(args.out, manifest, root_hint=root_hint)
            print(json_dumps({
                "out": str(args.out),
                "corpus_id": manifest["corpus_id"],
                "items": len(manifest["items"]),
                "skipped": len(skipped),
                "manifest_sha256": manifest["manifest_sha256"],
            }, ensure_ascii=False, indent=2))
            return 0
        if command == "split":
            manifest = load_manifest(args.manifest)
            report = assign_splits(manifest, seed=args.seed, ratio=parse_ratio(args.ratio), group_by=args.group_by)
            out = args.out or args.manifest
            write_manifest(out, manifest)
            print(json_dumps({
                "out": str(out),
                "seed": args.seed,
                "group_by": args.group_by,
                **report.to_json(),
                "manifest_sha256": manifest["manifest_sha256"],
            }, ensure_ascii=False, indent=2))
            return 0
        if command == "verify":
            manifest = load_manifest(args.manifest)
            root = _resolve_root(manifest, args.manifest, args.root)
            problems = verify_manifest(manifest, root)
            if problems:
                print(f"검증 실패: {args.manifest} — 문제 {len(problems)}건")
                for problem in problems:
                    print(f"  - {problem}")
                return 1
            print(f"검증 통과: {args.manifest} — 항목 {len(manifest['items'])}개, manifest_sha256 {manifest['manifest_sha256']}")
            return 0
    except ManifestError as exc:
        # N4: errors go to stderr like every other command's.
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print("사용법: deepfake-lens corpus {build,split,verify} …", file=sys.stderr)
    return 2


def _relative_hint(folder: Path, out: Path) -> str:
    """Corpus root relative to the manifest's directory when possible."""
    try:
        return Path(folder).resolve().relative_to(Path(out).resolve().parent).as_posix() or "."
    except ValueError:
        return str(Path(folder).resolve())
