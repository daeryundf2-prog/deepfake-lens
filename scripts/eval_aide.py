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

from deepfake_lens.evaluation_metrics import (
    auroc as _auroc,
    eer as _eer,
    sweep as _sweep,
    threshold_at_fpr as _threshold_at_fpr,
    undefined_reason,
)


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
    try:
        _threshold_at_fpr([], args.target_fpr)
    except ValueError as exc:
        parser.error(str(exc))

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
    report = {
        "version": "aide-eval-v1",
        "checkpoint": str(args.checkpoint),
        "root": str(args.root),
        **_metrics_report(pairs, args.target_fpr),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    return 0


def _metrics_report(pairs: list[tuple[float, int]], target_fpr: float) -> dict:
    reason = undefined_reason(pairs)
    auc = _auroc(pairs)
    error_rate = _eer(pairs)
    threshold = _threshold_at_fpr(pairs, target_fpr)
    confusion = None
    if threshold is not None:
        confusion = {
            "tp": sum(1 for s, l in pairs if s >= threshold and l == 1),
            "fp": sum(1 for s, l in pairs if s >= threshold and l == 0),
            "fn": sum(1 for s, l in pairs if s < threshold and l == 1),
            "tn": sum(1 for s, l in pairs if s < threshold and l == 0),
        }
    report = {
        "samples": len(pairs),
        "accuracy_at_0.5": round(sum((s >= 0.5) == (l == 1) for s, l in pairs) / len(pairs), 4) if pairs else None,
        "auroc": round(auc, 4) if auc is not None else None,
        "eer": round(error_rate, 4) if error_rate is not None else None,
        "target_fpr": target_fpr,
        "threshold_at_target_fpr": threshold,
        "confusion_at_threshold": confusion,
    }
    if reason is not None:
        report.update(auroc_reason=reason, eer_reason=reason)
    if threshold is None:
        report["threshold_at_target_fpr_reason"] = reason
    if not pairs:
        report["accuracy_at_0.5_reason"] = reason
    return report


if __name__ == "__main__":
    raise SystemExit(main())
