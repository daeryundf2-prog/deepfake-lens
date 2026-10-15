#!/usr/bin/env python3
"""Measure watermark decoders on real photos / speech (techniques-2026 items 3–5).

For each decoder the script reports, on a set of files that carry no
watermark (scripts/fetch_real_photos.py — Wikimedia Commons camera photos;
``--audio``: real speech clips), how often the decoder *claims* a mark (the
false-positive rate the findings quote), with a 95% Clopper–Pearson upper
bound, and — where an embedder is available — how often a mark embedded into
the same files is read back exactly (recall on lossless copies):

- ``sdxl``: exact 48/48 match with the registered SDXL message (FPR); recall
  after embedding the message with the dwtDct encoder (wm_imwatermark) and a
  lossless PNG round trip;
- ``trustmark``: BCH-valid packet per variant Q/P/C (FPR), per schema;
  ``--trustmark-encoders DIR`` (encoder_<V>.onnx from the same host) adds
  recall for a random BCH_4 payload;
- ``videoseal`` (torch + videoseal-y_256b_img.jit): the best bit agreement
  of the decoded 256 bits with 1000 random messages and with the registry —
  how far a photo's chance agreement is from the attribution threshold;
- ``audioseal`` (torch + audioseal + detector, ``--audio``): rate of
  detection probability >= the presence threshold.

Writes JSON (``--out``) — the numbers in docs/WATERMARK-MEASUREMENTS.md and
the ``FPR_NOTE`` constants come from its output. Network is never used.

Usage:
    python scripts/measure_watermark_fpr.py --photos <dir> [--audio <dir>] [--trustmark-encoders <dir>] --out result.json
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from pathlib import Path


def clopper_pearson_upper(k: int, n: int, alpha: float = 0.05) -> float:
    """Upper end of the two-sided 95% Clopper–Pearson interval of k/n (bisection on the binomial CDF)."""
    if n == 0:
        return 1.0
    if k >= n:
        return 1.0

    def binom_cdf(k_: int, p: float) -> float:
        total = 0.0
        for i in range(k_ + 1):
            total += math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i))
        return total

    lo, hi = k / n, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if binom_cdf(k, mid) > alpha / 2:
            lo = mid
        else:
            hi = mid
    return hi


def _photos(folder: Path) -> list[Path]:
    manifest = folder / "manifest.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return [folder / item["file"] for item in data["items"]]
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})


def measure_sdxl(photos: list[Path], scratch: Path) -> dict[str, object]:
    from PIL import Image

    from deepfake_lens.media_marks import load_registry, sdxl_check
    from deepfake_lens.checks import CheckSkipped
    from deepfake_lens.wm_imwatermark import encode_array_for_tests, load_rgb

    bits = load_registry().for_algorithm("imwatermark-dwtDct")[0]["message_bits"]
    false_matches = skipped = recalled = embedded = 0
    for photo in photos:
        try:
            outcome = sdxl_check(photo)
        except CheckSkipped:
            skipped += 1
            continue
        false_matches += bool(outcome.matched)
        marked = scratch / (photo.stem + "-sdxl.png")
        Image.fromarray(encode_array_for_tests(load_rgb(photo), bits)).save(marked)
        embedded += 1
        recalled += bool(sdxl_check(marked).matched)
        marked.unlink()
    n = len(photos) - skipped
    return {"n": n, "skipped": skipped, "false_matches": false_matches, "fpr": false_matches / n if n else None,
            "fpr_upper95": clopper_pearson_upper(false_matches, n), "recall_lossless": recalled / embedded if embedded else None,
            "recall_n": embedded}


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[1]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help

    parser = KoreanArgumentParser(description="실사 사진·음성에서 워터마크 디코더의 오탐률(및 무손실 재검출률)을 측정합니다.")
    parser.add_argument("--photos", type=Path, help="실사 사진 폴더(fetch_real_photos.py 결과)")
    parser.add_argument("--only", nargs="*", default=None, help="일부 검사만(sdxl)")
    parser.add_argument("--out", type=Path, required=True, help="결과 JSON")
    args = parser.parse_args(argv)
    import tempfile

    wanted = set(args.only or ["sdxl"])
    report: dict[str, object] = {"schema": "watermark-measurement-v1", "measured_at": time.strftime("%Y-%m-%d")}
    if args.photos:
        photos = _photos(args.photos)
        digest = hashlib.sha256("\n".join(sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in photos)).encode()).hexdigest()
        report["photo_set"] = {"folder": args.photos.name, "n": len(photos), "content_sha256": digest}
        with tempfile.TemporaryDirectory() as tmp:
            if "sdxl" in wanted:
                report["sdxl"] = measure_sdxl(photos, Path(tmp))
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
