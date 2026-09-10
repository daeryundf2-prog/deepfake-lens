#!/usr/bin/env python3
"""Evaluate the AIDE pretrained detector on a labeled image folder.

Batches every image under --root (0_real/1_fake or real/ai folder labels),
runs scripts/run_aide.py's faithful AIDE reimplementation, and reports
accuracy/AUROC/EER plus a threshold at target FPR. Writes a JSON report.

Usage:
    python scripts/eval_aide.py --checkpoint aide-progan.pth --root progan/cat \
        --report aide-progan-cat.json [--max-files 400] [--model-path-profile out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import importlib.util  # noqa: E402


def _load_run_aide():
    spec = importlib.util.spec_from_file_location("run_aide", REPO_ROOT / "scripts" / "run_aide.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the AIDE detector on a labeled folder.")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-files", type=int)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--target-fpr", type=float, default=0.05)
    args = parser.parse_args(argv)

    import torch
    from PIL import Image

    from deepfake_lens.datasets import discover_dataset, is_positive_label, is_negative_label

    ra = _load_run_aide()
    model = ra.load_model(args.checkpoint)
    dct = ra.DctPreprocessor()

    _, records = discover_dataset(args.root, max_files=args.max_files)
    labeled = [r for r in records if is_positive_label(r.label) or is_negative_label(r.label)]
    if not labeled:
        print("error: no labeled records found under the root", file=sys.stderr)
        return 2

    scores: list[float] = []
    labels: list[int] = []
    for start in range(0, len(labeled), args.batch_size):
        chunk = labeled[start : start + args.batch_size]
        tensors = []
        for record in chunk:
            with Image.open(record.path) as img:
                tensors.append(ra.preprocess(img, dct))
        with torch.no_grad():
            probs = torch.softmax(model(torch.stack(tensors)), dim=1)[:, 1]
        scores.extend(float(v) for v in probs)
        labels.extend(1 if is_positive_label(r.label) else 0 for r in chunk)
        done = start + len(chunk)
        if done % 80 == 0 or done == len(labeled):
            print(f"  {done}/{len(labeled)}", file=sys.stderr)

    pairs = list(zip(scores, labels))
    acc = sum((s >= 0.5) == (l == 1) for s, l in pairs) / len(pairs)
    auroc = _auroc(pairs)

    threshold = _threshold_at_fpr(pairs, args.target_fpr)
    tp = sum(1 for s, l in pairs if s >= threshold and l == 1)
    fp = sum(1 for s, l in pairs if s >= threshold and l == 0)
    fn = sum(1 for s, l in pairs if s < threshold and l == 1)
    tn = sum(1 for s, l in pairs if s < threshold and l == 0)

    report = {
        "version": "aide-eval-v1",
        "checkpoint": str(args.checkpoint),
        "root": str(args.root),
        "samples": len(pairs),
        "accuracy_at_0.5": round(acc, 4),
        "auroc": round(auroc, 4),
        "eer": round(_eer(pairs), 4),
        "target_fpr": args.target_fpr,
        "threshold_at_target_fpr": round(threshold, 4),
        "confusion_at_threshold": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    return 0


def _auroc(pairs: list[tuple[float, int]]) -> float:
    ranked = sorted(pairs, key=lambda p: p[0])
    total_pos = sum(1 for _, l in pairs if l == 1)
    total_neg = len(pairs) - total_pos
    if total_pos == 0 or total_neg == 0:
        return float("nan")
    pos_rank_sum = 0.0
    for i, (_, label) in enumerate(ranked):
        if label == 1:
            pos_rank_sum += i + 1
    return (pos_rank_sum - total_pos * (total_pos + 1) / 2) / (total_pos * total_neg)


def _sweep(pairs: list[tuple[float, int]]):
    thresholds = sorted(set(s for s, _ in pairs))
    total_pos = sum(1 for _, l in pairs if l == 1)
    total_neg = len(pairs) - total_pos
    for t in thresholds:
        fp = sum(1 for s, l in pairs if s >= t and l == 0)
        fn = sum(1 for s, l in pairs if s < t and l == 1)
        yield (t, fp / max(1, total_neg), fn / max(1, total_pos))


def _eer(pairs: list[tuple[float, int]]) -> float:
    """EER = FAR == FRR crossing; approximate with the closest sweep point."""
    candidates = []
    for t, far, frr in _sweep(pairs):
        candidates.append(abs(far - frr))
    return min(candidates) / 2


def _threshold_at_fpr(pairs: list[tuple[float, int]], target_fpr: float) -> float:
    best_t, best_gap = 1.0, float("inf")
    for t, far, _ in _sweep(pairs):
        gap = abs(far - target_fpr)
        if gap < best_gap:
            best_t, best_gap = t, gap
    return best_t


def _threshold_at_fpr(pairs: list[tuple[float, int]], target_fpr: float) -> float:
    best_t, best_gap = 1.0, float("inf")
    for t, far, _ in _sweep(pairs):
        gap = abs(far - target_fpr)
        if gap < best_gap:
            best_t, best_gap = t, gap
    return best_t


if __name__ == "__main__":
    raise SystemExit(main())
