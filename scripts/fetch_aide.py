#!/usr/bin/env python3
"""Fetch the official AIDE (ICLR 2025) checkpoint with sha256 verification.

Downloads `progan_train.pth` (~3.3 GB) into `models/` so the committed
`models/aide-runtime.json` profile can drive scans. The checkpoint is NOT
committed to git (research license; see .gitignore).

Official distribution point: the "Model Zoo" Google Drive folder linked from
the AIDE README (https://github.com/shilinyan99/AIDE). That folder holds
several checkpoints, so a bare folder URL cannot be downloaded directly —
open it in a browser, copy the file ID of `progan_train.pth`, and pass:

    --url "https://drive.google.com/uc?id=<FILE_ID>"

(or `https://drive.google.com/file/d/<FILE_ID>/view`, or any other direct
download URL). Alternatively download it manually and place the file at the
destination shown by --dest.

No official sha256 is published by the AIDE authors. Pass --sha256 <hex> to
pin a digest you trust; the script always prints the downloaded digest so it
can be recorded.

Usage:
    python scripts/fetch_aide.py --url "https://drive.google.com/uc?id=<FILE_ID>" [--sha256 <hex>]
    python scripts/fetch_aide.py --dest models --name aide_progan_train.pth --force
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST_DIR = REPO_ROOT / "models"
DEFAULT_NAME = "aide_progan_train.pth"

# Official checkpoint location from the AIDE README "Model Zoo" section.
# It is a Google Drive *folder* containing several checkpoints; there is no
# single canonical file URL, so direct downloads come from --url.
AIDE_MODEL_ZOO_URL = "https://drive.google.com/drive/folders/1qx76UFvDpgCxaPLBCmsA2WY-SSzeJrd4"
AIDE_SOURCE_URL = "https://github.com/shilinyan99/AIDE"

# The AIDE authors publish no checksum; users must supply --sha256 to pin one.
EXPECTED_SHA256: str | None = None

LICENSE_NOTICE = """\
AIDE checkpoint notice
- Checkpoint: progan_train.pth (~3.3 GB) from the AIDE Model Zoo (ICLR 2025).
- License: AIDE research code license; the Chameleon dataset is academic-research only.
- The checkpoint is NOT covered by this repository's MIT license and must not
  be redistributed or committed — you are downloading it for local use only.
- Source: {source}
- Model zoo: {zoo}
"""

_CHUNK = 1024 * 1024
_INTERSTITIAL_PEEK = 256 * 1024
_USER_AGENT = "deepfake-lens-fetch-aide/1.0"


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_url(url: str) -> tuple[str, str | None]:
    """Classify a checkpoint URL as ('folder'|'file'|'direct', file_id|None)."""
    folder = re.search(r"drive\.google\.com/drive/folders/([\w-]+)", url)
    if folder:
        return "folder", folder.group(1)
    file_match = re.search(r"drive\.google\.com/file/d/([\w-]+)", url)
    if file_match:
        return "file", file_match.group(1)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    file_id = query.get("id", [None])[0]
    if "drive.google." in url or "drive.usercontent.google.com" in url:
        return "file", file_id
    return "direct", file_id


def _drive_download_url(url: str, file_id: str | None) -> str:
    if file_id:
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return url


def _resolve_drive_interstitial(html: bytes, original_url: str) -> str | None:
    """Extract the real download URL from Drive's virus-scan interstitial page."""
    text = html.decode("utf-8", errors="replace")
    link = re.search(r'href="(https://drive\.usercontent\.google\.com/download[^"]*)"', text)
    if link:
        return link.group(1).replace("&amp;", "&")
    action = re.search(r'action="(https://drive\.usercontent\.google\.com/download[^"]*)"', text)
    if action:
        fields = dict(re.findall(r'name="([^"]+)"[^>]*value="([^"]*)"', text))
        return f"{action.group(1)}?{urllib.parse.urlencode(fields)}"
    token = re.search(r"confirm=([0-9A-Za-z_-]+)", text)
    _, file_id = classify_url(original_url)
    if token and file_id:
        return f"https://drive.google.com/uc?export=download&confirm={token.group(1)}&id={file_id}"
    return None


def _stream_response(response, dest: Path, first: bytes = b"") -> None:
    with dest.open("wb") as handle:
        if first:
            handle.write(first)
        for chunk in iter(lambda: response.read(_CHUNK), b""):
            handle.write(chunk)


def download(url: str, dest: Path) -> None:
    """Stream a URL to dest, following Drive's large-file confirm interstitial."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request) as response:
        first = response.read(_INTERSTITIAL_PEEK)
        if b"<html" in first.lower():
            resolved = _resolve_drive_interstitial(first, url)
            if resolved is None:
                raise RuntimeError(
                    "URL returned an HTML page instead of the checkpoint; "
                    "pass a direct-download link via --url (e.g. https://drive.google.com/uc?id=<FILE_ID>)"
                )
            follow = urllib.request.Request(resolved, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(follow) as resolved_response:
                _stream_response(resolved_response, dest)
            return
        _stream_response(response, dest, first)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch the AIDE progan_train checkpoint into models/.")
    parser.add_argument("--url", default=AIDE_MODEL_ZOO_URL, help="direct checkpoint download URL (default: the AIDE Model Zoo folder, which prints instructions)")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST_DIR, help=f"destination directory (default: {DEFAULT_DEST_DIR})")
    parser.add_argument("--name", default=DEFAULT_NAME, help=f"destination filename (default: {DEFAULT_NAME})")
    parser.add_argument("--sha256", help="expected lowercase sha256 hex digest; the download fails if it does not match")
    parser.add_argument("--force", action="store_true", help="overwrite an existing destination file")
    args = parser.parse_args(argv)

    dest_dir = Path(args.dest)
    dest = dest_dir / args.name
    part = dest.with_name(dest.name + ".part")

    print(LICENSE_NOTICE.format(source=AIDE_SOURCE_URL, zoo=AIDE_MODEL_ZOO_URL))

    if dest.exists() and not args.force:
        print(f"error: {dest} already exists; pass --force to overwrite", file=sys.stderr)
        return 2

    kind, file_id = classify_url(args.url)
    if kind == "folder":
        print(
            "error: the configured URL is the AIDE Model Zoo Google Drive folder,\n"
            "which cannot be downloaded directly. Open it in a browser, locate\n"
            "progan_train.pth, and re-run with its direct link:\n"
            f"  {AIDE_MODEL_ZOO_URL}\n"
            "  python scripts/fetch_aide.py --url \"https://drive.google.com/uc?id=<FILE_ID>\"\n"
            "or place the file manually at:\n"
            f"  {dest}",
            file=sys.stderr,
        )
        return 2

    url = _drive_download_url(args.url, file_id) if kind == "file" else args.url
    dest_dir.mkdir(parents=True, exist_ok=True)
    print(f"downloading {url}\n  -> {dest}")
    try:
        download(url, part)
    except Exception as exc:  # noqa: BLE001 - report any fetch failure uniformly.
        part.unlink(missing_ok=True)
        print(f"error: download failed: {exc}", file=sys.stderr)
        return 1

    digest = sha256_file(part)
    expected = (args.sha256 or EXPECTED_SHA256 or "").strip().lower() or None
    if expected is not None and digest != expected:
        part.unlink(missing_ok=True)
        print(f"error: sha256 mismatch: expected {expected}, got {digest}", file=sys.stderr)
        return 1

    part.replace(dest)
    print(f"sha256: {digest}" + (" (verified)" if expected else " (no --sha256 supplied; record this digest)"))
    print(f"saved {dest} ({dest.stat().st_size / (1024 ** 3):.2f} GiB)")
    print("scans now auto-discover models/aide-runtime.json; no extra flags needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
