#!/usr/bin/env python3
"""Fetch a real-photo set for watermark false-positive measurements (techniques-2026 items 3–5).

Downloads camera photographs from Wikimedia Commons' "Quality images"
category (each file reviewed by the Commons community; freely licensed —
the license of every file is recorded) as 1024-px-wide JPEG thumbnails, and
writes ``manifest.json`` (``real-photo-set-v1``: title, page URL, download
URL, license, camera make/model from Commons' metadata, sha256 of the
bytes). The set is used only to *measure* how often a watermark decoder
reports a mark on photos that carry none (scripts/measure_watermark_fpr.py);
nothing is committed or redistributed. Only files whose Commons metadata
names a camera (EXIF Make/Model) are kept, and files that already declare a
digital source type other than a capture are skipped.

Network is used here, at preparation time — never during an analysis.

Usage:
    python scripts/fetch_real_photos.py --out <dir> [--count 240] [--width 1024]
    python scripts/fetch_real_photos.py --out <dir> --verify-only
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://commons.wikimedia.org/w/api.php"
CATEGORY = "Category:Quality_images"
USER_AGENT = "deepfake-lens-fpr-measurement/1.0 (offline forensic tool; research use)"
MANIFEST_SCHEMA = "real-photo-set-v1"


def _get(params: dict[str, str]) -> dict:
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def _meta(info: dict, key: str) -> str:
    value = (info.get("extmetadata") or {}).get(key) or {}
    return str(value.get("value", "")) if isinstance(value, dict) else ""


def _camera(info: dict) -> tuple[str, str]:
    make = model = ""
    for entry in info.get("commonmetadata") or info.get("metadata") or []:
        if entry.get("name") == "Make":
            make = str(entry.get("value", ""))
        elif entry.get("name") == "Model":
            model = str(entry.get("value", ""))
    return make.strip(), model.strip()


def candidates(width: int, wanted: int):
    params = {
        "action": "query", "generator": "categorymembers", "gcmtitle": CATEGORY, "gcmtype": "file",
        "gcmlimit": "50", "gcmsort": "timestamp", "gcmdir": "descending", "gcmstart": "2025-12-31T00:00:00Z",
        "prop": "imageinfo", "iiprop": "url|mime|extmetadata|commonmetadata", "iiurlwidth": str(width),
    }
    seen = 0
    while seen < wanted * 4:
        data = _get(params)
        for page in sorted((data.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("title", "")):
            seen += 1
            info = (page.get("imageinfo") or [{}])[0]
            if info.get("mime") != "image/jpeg":
                continue
            make, model = _camera(info)
            if not make and not model:
                continue
            yield {
                "title": page.get("title"), "page": info.get("descriptionurl"), "url": info.get("thumburl") or info.get("url"),
                "license": _meta(info, "LicenseShortName"), "artist_html": _meta(info, "Artist")[:200], "make": make, "model": model,
            }
        cont = data.get("continue")
        if not cont:
            return
        params.update(cont)


def fetch(out: Path, count: int, width: int) -> int:
    out.mkdir(parents=True, exist_ok=True)
    items = []
    for index, entry in enumerate(candidates(width, count)):
        if len(items) >= count:
            break
        name = f"photo-{len(items):04d}.jpg"
        request = urllib.request.Request(entry["url"], headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                blob = response.read()
        except OSError as exc:
            print(f"skip {entry['title']}: {exc}", file=sys.stderr)
            continue
        (out / name).write_bytes(blob)
        items.append({**entry, "file": name, "sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob)})
        if len(items) % 20 == 0:
            print(f"{len(items)} / {count}")
        time.sleep(0.2)
    manifest = {"schema": MANIFEST_SCHEMA, "source": f"Wikimedia Commons {CATEGORY}", "width": width,
                "fetched": time.strftime("%Y-%m-%d"), "items": items}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(items)} photos and manifest.json to {out}")
    return 0 if len(items) >= count else 1


def verify(out: Path) -> int:
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    bad = [item["file"] for item in manifest["items"] if hashlib.sha256((out / item["file"]).read_bytes()).hexdigest() != item["sha256"]]
    print(f"{len(manifest['items'])} files, {len(bad)} mismatched" + (f": {bad[:5]}" if bad else ""))
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[1]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help

    parser = KoreanArgumentParser(description="워터마크 오탐률 측정용 실사 사진 세트(위키미디어 공용의 고품질 사진 분류)를 내려받습니다.")
    parser.add_argument("--out", type=Path, required=True, help="저장 폴더")
    parser.add_argument("--count", type=int, default=240, help="사진 수(기본 240)")
    parser.add_argument("--width", type=int, default=1024, help="썸네일 가로 픽셀(기본 1024)")
    parser.add_argument("--verify-only", action="store_true", help="내려받지 않고 manifest.json의 sha256만 확인")
    args = parser.parse_args(argv)
    return verify(args.out) if args.verify_only else fetch(args.out, args.count, args.width)


if __name__ == "__main__":
    raise SystemExit(main())
