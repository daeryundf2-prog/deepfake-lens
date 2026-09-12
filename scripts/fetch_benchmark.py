#!/usr/bin/env python3
"""Fetch a small benchmark dataset with per-file sha256 verification.

Manifest-driven so no dataset URL is baked in: pass --manifest pointing at a
JSON file (local path or URL) describing the sample set, and every downloaded
file is checked against its recorded sha256 before it lands in the dataset
root.

Expected dataset layout (consumed by `deepfake-lens dataset|eval|benchmark`
via deepfake_lens/datasets.py): folders named by label — `0_real/` for camera
images, `1_fake/` (or `ai`/`synthetic`/`generated`) for generated images.
Numeric class prefixes like `0_real`/`1_fake` are stripped by the labeler.

Manifest format:

    {
      "name": "synthbuster-sample",
      "license": "note on the dataset license / terms",
      "base_url": "https://host/prefix/",          // optional; joined to file "path"
      "files": [
        {"url": "https://.../a.png",               // or "path": "a.png" with base_url
         "dest": "1_fake/firefly/a.png",           // REQUIRED, relative label folder
         "sha256": "<hex>"}                         // REQUIRED for verification
      ]
    }

Candidate source already cited in this repo: the Synthbuster dataset
(https://zenodo.org/records/10066048) — check its terms before use; Zenodo
per-file URLs (`.../files/<name>.zip?download=1`) work as manifest entries,
though a small curated manifest is recommended over whole archives.

CI never fetches external data — it runs the committed synthetic
fixtures/benchmark/ set via deepfake_lens/tests/test_benchmark_e2e.py.

Usage:
    python scripts/fetch_benchmark.py --manifest bench.json --dest public_datasets/bench
    python scripts/fetch_benchmark.py --manifest https://host/bench.json --max-files 20
    python scripts/fetch_benchmark.py --manifest bench.json --verify-only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST_ROOT = REPO_ROOT / "public_datasets"

_CHUNK = 1024 * 1024
_USER_AGENT = "deepfake-lens-fetch-benchmark/1.0"
LABEL_FOLDERS = ("0_real", "1_fake", "real", "fake", "ai", "synthetic", "generated")


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(source: str) -> dict[str, object]:
    """Read the manifest from a local path or an http(s)/file URL."""
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme in {"http", "https", "file"}:
        request = urllib.request.Request(source, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    return json.loads(Path(source).read_text(encoding="utf-8"))


def manifest_entries(manifest: dict[str, object]) -> list[dict[str, str]]:
    """Normalize manifest file entries to {url, dest, sha256} dicts."""
    base_url = str(manifest.get("base_url") or "")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("manifest has no 'files' list")
    entries = []
    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise ValueError(f"manifest files[{index}] is not an object")
        url = str(entry.get("url") or "").strip()
        if not url:
            rel = str(entry.get("path") or "").strip()
            if not rel or not base_url:
                raise ValueError(f"manifest files[{index}] needs 'url' or manifest 'base_url' + 'path'")
            url = urllib.parse.urljoin(base_url, rel)
        dest = str(entry.get("dest") or "").strip()
        if not dest:
            raise ValueError(f"manifest files[{index}] needs 'dest' (relative label-folder path)")
        sha256 = str(entry.get("sha256") or "").strip().lower()
        if sha256 and (len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256)):
            raise ValueError(f"manifest files[{index}] sha256 is not a lowercase hex digest")
        entries.append({"url": url, "dest": dest, "sha256": sha256})
    return entries


def safe_dest(root: Path, relpath: str) -> Path:
    """Resolve a manifest 'dest' inside root, refusing traversal."""
    target = (root / relpath).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"manifest dest escapes the dataset root: {relpath}") from exc
    if Path(relpath).is_absolute() or ".." in Path(relpath).parts:
        raise ValueError(f"manifest dest must be a relative path without '..': {relpath}")
    return target


def download(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request) as response:
        with dest.open("wb") as handle:
            for chunk in iter(lambda: response.read(_CHUNK), b""):
                handle.write(chunk)


def fetch_entry(entry: dict[str, str], root: Path, *, force: bool) -> tuple[str, str]:
    """Download one manifest entry; returns (status, detail)."""
    target = safe_dest(root, entry["dest"])
    expected = entry["sha256"]
    if target.exists() and not force:
        if expected and sha256_file(target) != expected:
            return "corrupt", f"{entry['dest']}: sha256 mismatch on existing file (use --force to redownload)"
        return "kept", entry["dest"]
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    try:
        download(entry["url"], part)
    except Exception as exc:  # noqa: BLE001 - report any fetch failure uniformly.
        part.unlink(missing_ok=True)
        return "failed", f"{entry['dest']}: {exc}"
    digest = sha256_file(part)
    if expected and digest != expected:
        part.unlink(missing_ok=True)
        return "failed", f"{entry['dest']}: sha256 mismatch: expected {expected}, got {digest}"
    part.replace(target)
    return "fetched", f"{entry['dest']} sha256={digest}" + ("" if expected else " (no sha256 recorded)")


def verify_entry(entry: dict[str, str], root: Path) -> tuple[str, str]:
    target = safe_dest(root, entry["dest"])
    if not target.exists():
        return "missing", entry["dest"]
    if entry["sha256"]:
        digest = sha256_file(target)
        if digest != entry["sha256"]:
            return "mismatch", f"{entry['dest']}: expected {entry['sha256']}, got {digest}"
    return "ok", entry["dest"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch a checksummed benchmark dataset (0_real/1_fake layout).")
    parser.add_argument("--manifest", required=True, help="manifest JSON path or URL (see module docstring for the format)")
    parser.add_argument("--dest", type=Path, help=f"dataset root (default: {DEFAULT_DEST_ROOT}/<manifest name>)")
    parser.add_argument("--max-files", type=int, help="cap the number of manifest entries fetched")
    parser.add_argument("--verify-only", action="store_true", help="check existing files against sha256 without downloading")
    parser.add_argument("--force", action="store_true", help="redownload files that already exist")
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
        entries = manifest_entries(manifest)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: manifest could not be loaded: {exc}", file=sys.stderr)
        return 2

    name = str(manifest.get("name") or "benchmark")
    dest_root = Path(args.dest) if args.dest else DEFAULT_DEST_ROOT / name
    license_note = str(manifest.get("license") or "").strip()
    print(f"manifest: {name} ({len(entries)} files)")
    if license_note:
        print(f"license: {license_note}")
    print("scores on this dataset are screening metrics, not truth labels; check dataset terms before use.")

    capped = entries[: args.max_files] if args.max_files else entries
    counts: dict[str, int] = {}
    for entry in capped:
        try:
            status, detail = (verify_entry(entry, dest_root) if args.verify_only else fetch_entry(entry, dest_root, force=args.force))
        except ValueError as exc:
            status, detail = "failed", str(exc)
        counts[status] = counts.get(status, 0) + 1
        print(f"  {status:>8}  {detail}")

    bad = counts.get("failed", 0) + counts.get("mismatch", 0) + counts.get("corrupt", 0) + counts.get("missing", 0)
    print(json.dumps({"dest": str(dest_root), "counts": counts}, ensure_ascii=False))
    if bad:
        return 1

    # Cross-check the label-folder convention so a mislabeled fetch is obvious.
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from deepfake_lens.datasets import discover_dataset

        summary, _ = discover_dataset(dest_root)
        print(f"dataset audit: total={summary.total} positive={summary.positive} negative={summary.negative} unknown={summary.unknown}")
        if summary.unknown:
            print(f"warning: {summary.unknown} files landed outside {LABEL_FOLDERS} label folders", file=sys.stderr)
    except ImportError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
