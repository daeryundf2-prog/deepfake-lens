"""Measure faceswap_seam raw metrics on a labeled corpus and emit a
layer-thresholds-v1 ThresholdProfile.

    python experiments/eval_seam_thresholds.py \
        --real-dir eval_corpus/face/real \
        --fake-dir eval_corpus/face/fake \
        --profile-out models/thresholds.faceswap_seam.json \
        --report experiments/seam_eval_report.json

or with an explicit label file:

    python experiments/eval_seam_thresholds.py \
        --labels-csv manifest.csv   # rows: path,label (0=real, 1=fake)

What it measures
----------------
For every scored sample the script records the four raw seam metrics
(boundary_residual, noise_discrepancy_ratio, chrominance_delta,
corneal_asymmetry) plus the final 0-100 score. Threshold candidates are
chosen from the real-class distribution at a target FPR:

- one-sided metrics (seam_ratio, chroma_delta, corneal_asymmetry):
  cutoff = smallest metric value whose empirical FPR <= target
- noise_discrepancy_ratio: scored as |ln(ratio)| so a single deviation
  cutoff maps back to the symmetric (low, high) pair
- score_high / score_medium: swept on the aggregate score at the
  --fpr-high / --fpr-medium operating points

Provenance written into the profile: corpus fingerprint (sha256 over the
sorted "relpath:size:sha256" manifest), scored sample count, per-metric
AUROC, and the aggregate score AUROC/EER/FPR at the chosen cutoffs. A
corpus below MIN_CALIBRATION_SAMPLES produces a provisional profile —
the CLI already warns on that.

Skipped/unmeasurable samples (no face, unreadable image) are counted and
reported, never treated as negatives.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from deepfake_lens.calibration import (  # noqa: E402
    ThresholdProfile,
    THRESHOLD_PROFILE_VERSION,
    write_threshold_profile,
)
from deepfake_lens.evaluation_metrics import auroc, eer, threshold_at_fpr  # noqa: E402
from deepfake_lens.faceswap_seam import SEAM_THRESHOLDS, analyze_faceswap_seam  # noqa: E402

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}

# Faceswap seam keys that are one-sided "higher is suspicious" metrics.
ONE_SIDED_KEYS = ("seam_ratio_high", "chroma_delta", "corneal_asymmetry_px")

_METRIC_FIELD = {
    "seam_ratio_high": "boundary_residual",
    "noise_ratio": "noise_discrepancy_ratio",
    "chroma_delta": "chrominance_delta",
    "corneal_asymmetry_px": "corneal_asymmetry",
}


def _load_corpus(real_dir: Path | None, fake_dir: Path | None, labels_csv: Path | None) -> list[tuple[Path, int]]:
    """Return (path, label) pairs; label 1 = manipulated."""
    if labels_csv:
        rows: list[tuple[Path, int]] = []
        with labels_csv.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                rows.append((Path(row["path"]), int(row["label"])))
        return rows
    pairs: list[tuple[Path, int]] = []
    if real_dir:
        pairs += [(p, 0) for p in sorted(real_dir.rglob("*")) if p.suffix.lower() in IMAGE_SUFFIXES and p.is_file()]
    if fake_dir:
        pairs += [(p, 1) for p in sorted(fake_dir.rglob("*")) if p.suffix.lower() in IMAGE_SUFFIXES and p.is_file()]
    return pairs


def _corpus_fingerprint(entries: list[tuple[Path, int]], roots: list[Path]) -> str:
    """sha256 over the sorted path/size/content manifest — reproducing the
    measurement requires the identical corpus."""
    digest = hashlib.sha256()
    for path, label in sorted(entries, key=lambda item: str(item[0])):
        display = str(path)
        for root in roots:
            try:
                display = str(path.relative_to(root))
                break
            except ValueError:
                continue
        try:
            content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            size = path.stat().st_size
        except OSError:
            content_hash, size = "unreadable", 0
        digest.update(f"{display}:{size}:{content_hash}:{label}\n".encode("utf-8"))
    return digest.hexdigest()


def _score_rows(entries: list[tuple[Path, int]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path, label in entries:
        result = analyze_faceswap_seam(path)
        measured = any(
            getattr(result, field) is not None
            for field in _METRIC_FIELD.values()
        )
        rows.append(
            {
                "path": str(path),
                "label": label,
                "band": result.band,
                "score": result.score,
                "measured": measured,
                "boundary_residual": result.boundary_residual,
                "noise_discrepancy_ratio": result.noise_discrepancy_ratio,
                "chrominance_delta": result.chrominance_delta,
                "corneal_asymmetry": result.corneal_asymmetry,
            }
        )
    return rows


def _metric_pairs(rows: list[dict[str, object]], field: str) -> list[tuple[float, int]]:
    return [
        (float(row[field]), int(row["label"]))
        for row in rows
        if row[field] is not None and math.isfinite(float(row[field]))
    ]


def _pick_thresholds(rows: list[dict[str, object]], *, fpr: float, fpr_medium: float) -> tuple[dict[str, float], dict[str, object]]:
    """Fit each threshold key from measured rows; fall back to defaults."""
    values: dict[str, float] = {}
    detail: dict[str, object] = {}

    for key in ONE_SIDED_KEYS:
        pairs = _metric_pairs(rows, _METRIC_FIELD[key])
        metric_auroc = auroc(pairs) if pairs else None
        fitted = threshold_at_fpr(pairs, fpr) if pairs else None
        if fitted is None:
            values[key] = SEAM_THRESHOLDS[key]
            detail[key] = {"n": len(pairs), "auroc": metric_auroc, "fitted": None, "fell_back_to_default": True}
        else:
            values[key] = round(float(fitted), 4)
            detail[key] = {"n": len(pairs), "auroc": metric_auroc, "fitted": values[key], "fell_back_to_default": False}

    # noise ratio: symmetric |ln(r)| deviation → (low, high) pair.
    noise_pairs_raw = _metric_pairs(rows, "noise_discrepancy_ratio")
    deviation_pairs = [(abs(math.log(max(r, 1e-9))), label) for r, label in noise_pairs_raw]
    noise_auroc = auroc(deviation_pairs) if deviation_pairs else None
    deviation_cut = threshold_at_fpr(deviation_pairs, fpr) if deviation_pairs else None
    if deviation_cut is None:
        values["noise_ratio_low"] = SEAM_THRESHOLDS["noise_ratio_low"]
        values["noise_ratio_high"] = SEAM_THRESHOLDS["noise_ratio_high"]
        fitted_noise = None
    else:
        values["noise_ratio_low"] = round(math.exp(-float(deviation_cut)), 4)
        values["noise_ratio_high"] = round(math.exp(float(deviation_cut)), 4)
        fitted_noise = float(deviation_cut)
    detail["noise_ratio"] = {
        "n": len(noise_pairs_raw),
        "auroc_log_abs": noise_auroc,
        "fitted_deviation": fitted_noise,
        "fell_back_to_default": fitted_noise is None,
    }

    # Aggregate score cutoffs.
    score_pairs = [(float(row["score"]), int(row["label"])) for row in rows if row["measured"]]
    score_auroc = auroc(score_pairs) if score_pairs else None
    score_eer = eer(score_pairs) if score_pairs else None
    high_cut = threshold_at_fpr(score_pairs, fpr) if score_pairs else None
    med_cut = threshold_at_fpr(score_pairs, fpr_medium) if score_pairs else None
    if high_cut is not None:
        values["score_high"] = round(float(high_cut), 4)
    if med_cut is not None:
        values["score_medium"] = min(round(float(med_cut), 4), values.get("score_high", 100.0))
    detail["score"] = {
        "n": len(score_pairs),
        "auroc": score_auroc,
        "eer": score_eer,
        "fpr_target_high": fpr,
        "fpr_target_medium": fpr_medium,
        "fitted_high": values.get("score_high"),
        "fitted_medium": values.get("score_medium"),
    }
    return values, detail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--real-dir", type=Path, help="Directory of real face images (label 0)")
    parser.add_argument("--fake-dir", type=Path, help="Directory of manipulated/fake face images (label 1)")
    parser.add_argument("--labels-csv", type=Path, help="CSV manifest with columns: path,label")
    parser.add_argument("--profile-out", type=Path, help="Write the fitted layer-thresholds-v1 profile JSON here")
    parser.add_argument("--report", type=Path, default=REPO_ROOT / "experiments/seam_eval_report.json")
    parser.add_argument("--fpr-high", type=float, default=0.01, help="Target real-class FPR for *_high cutoffs (default 1%%)")
    parser.add_argument("--fpr-medium", type=float, default=0.10, help="Target real-class FPR for score_medium (default 10%%)")
    args = parser.parse_args()

    if not args.labels_csv and not args.real_dir and not args.fake_dir:
        parser.error("provide --labels-csv or at least one of --real-dir/--fake-dir")

    entries = _load_corpus(args.real_dir, args.fake_dir, args.labels_csv)
    if not entries:
        parser.error("corpus is empty")
    roots = [root for root in (args.real_dir, args.fake_dir, args.labels_csv.parent if args.labels_csv else None) if root]
    fingerprint = _corpus_fingerprint(entries, roots)

    rows = _score_rows(entries)
    scored = [row for row in rows if row["measured"]]
    skipped = len(rows) - len(scored)

    values, detail = _pick_thresholds(scored, fpr=args.fpr_high, fpr_medium=args.fpr_medium)

    score_pairs = [(float(row["score"]), int(row["label"])) for row in scored]
    metrics: dict[str, float | int] = {
        "n": len(rows),
        "scored": len(scored),
        "skipped_unmeasured": skipped,
    }
    if score_pairs:
        metrics["score_auroc"] = auroc(score_pairs) or 0.0
        metrics["score_eer"] = eer(score_pairs) or 0.0
        metrics["coverage"] = round(len(scored) / len(rows), 4)

    profile = ThresholdProfile(
        version=THRESHOLD_PROFILE_VERSION,
        values={f"faceswap_seam.{key}": value for key, value in values.items()},
        samples=len(scored),
        dataset_fingerprint=fingerprint,
        measured_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        metrics=metrics,
    )

    report = {
        "corpus_fingerprint": fingerprint,
        "n_entries": len(entries),
        "scored": len(scored),
        "skipped_unmeasured": skipped,
        "provisional": profile.provisional,
        "fitted": detail,
        "profile": profile.to_json(),
        "rows": rows,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.profile_out:
        args.profile_out.parent.mkdir(parents=True, exist_ok=True)
        write_threshold_profile(args.profile_out, profile)

    status = "PROVISIONAL" if profile.provisional else "MEASURED"
    print(f"[{status}] entries={len(entries)} scored={len(scored)} unmeasured={skipped}")
    if "score_auroc" in metrics:
        print(f"score auroc={metrics['score_auroc']} eer={metrics['score_eer']} coverage={metrics['coverage']}")
    for key, fitted in detail.items():
        if isinstance(fitted, dict):
            print(f"  {key}: n={fitted.get('n')} auroc={fitted.get('auroc', fitted.get('auroc_log_abs'))} fitted={fitted.get('fitted', fitted.get('fitted_deviation'))} fallback={fitted.get('fell_back_to_default')}")
    if args.profile_out:
        print(f"profile -> {args.profile_out}")
    print(f"report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
