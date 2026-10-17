#!/usr/bin/env python3
"""Fetch the real-speech measurement set: LibriSpeech dev-clean, 40 speakers x 6 utterances (TB-2).

Streams ``dev-clean.tar.gz`` (openslr.org SLR12, CC BY 4.0) once, hashes the
whole stream (sha256 printed and stored in ``manifest.json``), and keeps, per
speaker, the first :data:`PER_SPEAKER` ``.flac`` members in sorted path order —
dev-clean has 40 speakers, so the set is 240 utterances. Only the kept files
are written (about 60 MB); the archive is never stored. The set is used by
``scripts/measure_watermark_fpr.py --audio`` (false-positive rates of the
audio watermark decoders) and never committed.

Usage:
    python scripts/fetch_real_speech.py --out <folder> [--url <mirror>]
"""

from __future__ import annotations

import hashlib
import sys
import json
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

URL = "https://www.openslr.org/resources/12/dev-clean.tar.gz"
PER_SPEAKER = 6
USER_AGENT = "deepfake-lens-fetch-real-speech/1.0"


class _Hashing:
    def __init__(self, raw) -> None:  # noqa: ANN001 - an HTTP response
        self.raw = raw
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, n: int = -1) -> bytes:
        data = self.raw.read(n)
        self.digest.update(data)
        self.size += len(data)
        return data


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[1]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help

    parser = KoreanArgumentParser(description="LibriSpeech dev-clean 화자 40명x6발화 측정 세트를 받습니다(측정 전용, 커밋하지 않음).")
    parser.add_argument("--out", type=Path, required=True, help="저장할 폴더")
    parser.add_argument("--url", default=URL, help="dev-clean.tar.gz 주소(미러)")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    best: dict[str, list[tuple[str, bytes]]] = {}
    request = urllib.request.Request(args.url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=900) as response:
        stream = _Hashing(response)
        with tarfile.open(fileobj=stream, mode="r|gz") as archive:
            for member in archive:
                if not member.isfile() or not member.name.endswith(".flac"):
                    continue
                parts = PurePosixPath(member.name).parts
                speaker = parts[-3] if len(parts) >= 3 else "?"
                kept = best.setdefault(speaker, [])
                if len(kept) >= PER_SPEAKER and member.name >= kept[-1][0]:
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                kept.append((member.name, handle.read()))
                kept.sort()
                del kept[PER_SPEAKER:]
        while stream.read(1 << 20):  # hash the rest of the stream
            pass
    items = []
    for speaker in sorted(best):
        for name, data in best[speaker]:
            target = args.out / PurePosixPath(name).name
            target.write_bytes(data)
            items.append({"file": target.name, "member": name, "sha256": hashlib.sha256(data).hexdigest(), "speaker": speaker})
    listing = hashlib.sha256("\n".join(item["sha256"] for item in items).encode()).hexdigest()
    manifest = {"source": args.url, "license": "CC BY 4.0 (LibriSpeech, openslr.org SLR12)", "archive_sha256": stream.digest.hexdigest(),
                "archive_bytes": stream.size, "speakers": len(best), "per_speaker": PER_SPEAKER, "set_sha256": listing, "items": items}
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(items)}개 저장, 화자 {len(best)}명, 아카이브 sha256 {stream.digest.hexdigest()}, 세트 sha256 {listing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
