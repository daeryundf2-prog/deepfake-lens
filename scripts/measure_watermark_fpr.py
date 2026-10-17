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
- ``perth`` (torch + librosa + ffmpeg + perth-implicit-250000.pth.tar, ``--audio``,
  TB-2): rates of PerTh confidence >= 0.5 (presence) and >= the registry's
  ``min_confidence`` (attribution); the real-speech set is
  ``scripts/fetch_real_speech.py`` (LibriSpeech dev-clean, 40 speakers x 6).

Writes JSON (``--out``) — the numbers in docs/WATERMARK-MEASUREMENTS.md and
the ``FPR_NOTE`` constants come from its output. Network is never used.

Usage:
    python scripts/measure_watermark_fpr.py --photos <dir> [--audio <dir>] [--trustmark-encoders <dir>] --out result.json
"""

from __future__ import annotations

import hashlib
import json
import math
import random
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


def measure_trustmark(photos: list[Path], encoders: Path | None) -> dict[str, object]:
    from PIL import Image

    from deepfake_lens import wm_trustmark as tm

    present, missing = tm.available_variants()
    result: dict[str, object] = {"variants": sorted(present), "missing": missing}
    for variant in sorted(present):
        asset, size, square = tm.VARIANTS[variant]
        session = tm._session(asset, present[variant])
        hits: dict[str, int] = {}
        n = 0
        for photo in photos:
            with Image.open(photo) as opened:
                image = opened.convert("RGB")
            logits = session.run(None, {session.get_inputs()[0].name: tm.preprocess(image, size, square)})[0]
            bits = "".join("1" if v >= 0 else "0" for v in logits.reshape(-1)[:100])
            decoded = tm.decode_packet(bits)
            n += 1
            if decoded is not None:
                hits[tm.SCHEMAS[decoded[0]][0]] = hits.get(tm.SCHEMAS[decoded[0]][0], 0) + 1
        k = sum(hits.values())
        entry: dict[str, object] = {"n": n, "false_detections": k, "by_schema": hits, "fpr": k / n if n else None,
                                    "fpr_upper95": clopper_pearson_upper(k, n)}
        encoder_path = encoders / f"encoder_{variant}.onnx" if encoders else None
        if encoder_path is not None and encoder_path.is_file():
            entry.update(_trustmark_recall(photos, variant, encoder_path, session, size, square))
        result[variant] = entry
    return result


def _trustmark_recall(photos, variant, encoder_path, decoder, size, square) -> dict[str, object]:
    """Embed a random BCH_4 payload with the ONNX encoder (residual method of trustmark.encode) and decode it back."""
    import onnxruntime as ort
    from PIL import Image

    from deepfake_lens import wm_trustmark as tm

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from build_trustmark_fixture import encode_packet, embed

    encoder = ort.InferenceSession(str(encoder_path), providers=["CPUExecutionProvider"])
    rng = random.Random(7)
    ok = 0
    for photo in photos:
        payload = "".join(rng.choice("01") for _ in range(68))
        with Image.open(photo) as opened:
            cover = opened.convert("RGB")
        stego = embed(encoder, cover, encode_packet(payload, schema=2), variant)
        logits = decoder.run(None, {decoder.get_inputs()[0].name: tm.preprocess(stego, size, square)})[0]
        decoded = tm.decode_packet("".join("1" if v >= 0 else "0" for v in logits.reshape(-1)[:100]))
        ok += decoded == (2, payload)
    return {"recall_lossless": ok / len(photos) if photos else None, "recall_n": len(photos)}


def measure_videoseal(photos: list[Path]) -> dict[str, object]:
    from deepfake_lens import wm_meta

    rng = random.Random(11)
    messages = ["".join(rng.choice("01") for _ in range(256)) for _ in range(1000)]
    best: list[int] = []
    for photo in photos:
        reading = wm_meta.videoseal_read_image(photo)
        bits = reading.bits
        best.append(max(sum(a == b for a, b in zip(bits, message)) for message in messages))
    threshold = wm_meta.VIDEOSEAL_MATCH_BITS
    return {"n": len(photos), "max_agreement_with_1000_random_messages": max(best) if best else None,
            "mean_best_agreement": sum(best) / len(best) if best else None, "attribution_threshold_bits": threshold,
            "photos_at_or_above_threshold": sum(b >= threshold for b in best)}


def measure_audioseal(folder: Path) -> dict[str, object]:
    from deepfake_lens import wm_meta

    clips = sorted(p for p in folder.rglob("*") if p.suffix.lower() in {".wav", ".flac", ".mp3", ".ogg"})
    detected = 0
    probabilities = []
    for clip in clips:
        reading = wm_meta.audioseal_read(clip)
        probabilities.append(reading.probability)
        detected += reading.probability >= wm_meta.AUDIOSEAL_PRESENCE_THRESHOLD
    n = len(clips)
    return {"n": n, "false_detections": detected, "fpr": detected / n if n else None, "fpr_upper95": clopper_pearson_upper(detected, n),
            "max_probability": max(probabilities) if probabilities else None, "threshold": wm_meta.AUDIOSEAL_PRESENCE_THRESHOLD}


def measure_perth(folder: Path) -> dict[str, object]:
    """TB-2: PerTh implicit confidence on unmarked speech — presence (>= 0.5) and registry-match (>= min_confidence) rates."""
    from deepfake_lens import wm_perth
    from deepfake_lens.media_marks import load_registry

    [entry] = load_registry().for_algorithm("perth-implicit")
    cut = float(entry.get("min_confidence", 0.9))
    clips = sorted(p for p in folder.rglob("*") if p.suffix.lower() in {".wav", ".flac", ".mp3", ".ogg"})
    values = [wm_perth.perth_read(clip).confidence for clip in clips]
    n = len(values)
    present = sum(v >= wm_perth.PRESENCE_THRESHOLD for v in values)
    matched = sum(v >= cut for v in values)
    return {"n": n, "presence_threshold": wm_perth.PRESENCE_THRESHOLD, "false_presence": present, "fpr": present / n if n else None,
            "fpr_upper95": clopper_pearson_upper(present, n), "registry_min_confidence": cut, "false_registry_matches": matched,
            "registry_fpr_upper95": clopper_pearson_upper(matched, n), "max_confidence": max(values) if values else None}


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[1]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help

    parser = KoreanArgumentParser(description="실사 사진·음성에서 워터마크 디코더의 오탐률(및 무손실 재검출률)을 측정합니다.")
    parser.add_argument("--photos", type=Path, help="실사 사진 폴더(fetch_real_photos.py 결과)")
    parser.add_argument("--audio", type=Path, help="실제 음성 파일 폴더")
    parser.add_argument("--trustmark-encoders", type=Path, help="TrustMark encoder_<V>.onnx 폴더(재검출률 측정용, 선택)")
    parser.add_argument("--only", nargs="*", default=None, help="일부 검사만(sdxl/trustmark/videoseal/audioseal/perth)")
    parser.add_argument("--out", type=Path, required=True, help="결과 JSON")
    args = parser.parse_args(argv)
    import tempfile

    wanted = set(args.only or ["sdxl", "trustmark", "videoseal", "audioseal", "perth"])
    report: dict[str, object] = {"schema": "watermark-measurement-v1", "measured_at": time.strftime("%Y-%m-%d")}
    if args.photos:
        photos = _photos(args.photos)
        digest = hashlib.sha256("\n".join(sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in photos)).encode()).hexdigest()
        report["photo_set"] = {"folder": args.photos.name, "n": len(photos), "content_sha256": digest}
        with tempfile.TemporaryDirectory() as tmp:
            if "sdxl" in wanted:
                report["sdxl"] = measure_sdxl(photos, Path(tmp))
            if "trustmark" in wanted:
                report["trustmark"] = measure_trustmark(photos, args.trustmark_encoders)
            if "videoseal" in wanted:
                report["videoseal"] = measure_videoseal(photos)
    if args.audio and "audioseal" in wanted:
        report["audioseal"] = measure_audioseal(args.audio)
    if args.audio and "perth" in wanted:
        report["perth"] = measure_perth(args.audio)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
