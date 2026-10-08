#!/usr/bin/env python3
"""Evaluate AI-text detection on a labeled corpus.

Manifest format (JSON):
  {"samples": [{"path": "relative/or/abs.txt", "label": "ai"|"human",
                "generator": "devin|codex|claude|gemini|grok|kimi|human|...",
                "language": "ko"|"en"|...}]}

Reports AUROC, EER, precision/recall/FPR at configurable thresholds,
plus per-language and per-generator breakdowns. Every AUROC/recall/FPR
carries a 95% stratified bootstrap CI and n_pos/n_neg (G26).

Scores are *raw, uncalibrated* member scores (G26/G27): under contract v2
``result.score`` is 0 for every uncalibrated result, so this script reads
``deepfake_lens.evaluate.raw_member_score`` (the text model's raw 0-100
output, else a statistical evidence raw score). Samples with no raw member
score — e.g. no text model installed — are reported as unscored, not as 0.
Profiles default to ``deepfake_lens.cli.default_models_dir()``.

Usage:
  python experiments/eval_text_detect.py experiments/text-corpus/manifest.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepfake_lens import analyze_file  # noqa: E402
from deepfake_lens.cli import default_models_dir  # noqa: E402
from deepfake_lens.evaluate import SCORE_BASIS, raw_member_score  # noqa: E402
from deepfake_lens.evaluation_metrics import auroc, ci_summary, eer, format_ci, undefined_reason  # noqa: E402


def _auroc(labels: list[int], scores: list[float]) -> float | None:
    return auroc(list(zip(scores, labels, strict=True)))


def _eer(labels: list[int], scores: list[float]) -> float | None:
    return eer(list(zip(scores, labels, strict=True)))


def _confusion(labels: list[int], scores: list[float], threshold: float) -> dict:
    tp = sum(1 for l, s in zip(labels, scores) if l == 1 and s >= threshold)
    fp = sum(1 for l, s in zip(labels, scores) if l == 0 and s >= threshold)
    fn = sum(1 for l, s in zip(labels, scores) if l == 1 and s < threshold)
    tn = sum(1 for l, s in zip(labels, scores) if l == 0 and s < threshold)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "fpr": fp / (fp + tn) if fp + tn else None,
    }


def _report(name: str, rows: list[dict], threshold: float) -> dict:
    labels = [1 if r["label"] == "ai" else 0 for r in rows]
    scores = [r["score"] for r in rows]
    ci = ci_summary(scores, labels, threshold=threshold)
    out = {
        "n": len(rows),
        "n_pos": ci["n_pos"],
        "n_neg": ci["n_neg"],
        "score_basis": SCORE_BASIS,
        "auroc": _auroc(labels, scores),
        "auroc_ci": ci["auroc_ci"],
        "eer": _eer(labels, scores),
        f"confusion@{threshold:g}": _confusion(labels, scores, threshold),
        "recall_ci": ci["recall_at_threshold_ci"],
        "fpr_ci": ci["fpr_at_threshold_ci"],
        "recall_at_fpr_0_01": ci["recall_at_fpr"],
        "recall_at_fpr_0_01_ci": ci["recall_at_fpr_ci"],
        "ci_method": ci["ci_method"],
    }
    reason = undefined_reason(list(zip(scores, labels, strict=True)))
    if reason is not None:
        out.update(auroc_reason=reason, eer_reason=reason)
    print(f"\n== {name} (n={out['n']}, n_pos={out['n_pos']}, n_neg={out['n_neg']}; {SCORE_BASIS}) ==")
    print(f"  AUROC: {format_ci(out['auroc'], out['auroc_ci'])}")
    print(f"  EER:   {out['eer'] if out['eer'] is None else round(out['eer'], 3)}")
    c = out[f"confusion@{threshold:g}"]
    print(f"  @{threshold:g}: tp={c['tp']} fp={c['fp']} fn={c['fn']} tn={c['tn']}"
          f" precision={c['precision']} recall={format_ci(c['recall'], out['recall_ci'])}"
          f" fpr={format_ci(c['fpr'], out['fpr_ci'])}")
    print(f"  recall@FPR1%: {format_ci(out['recall_at_fpr_0_01'], out['recall_at_fpr_0_01_ci'])}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--threshold", type=float, default=50.0,
                    help="ensemble score threshold for the confusion matrix (default 50)")
    ap.add_argument("--model-path", type=Path,
                    default=default_models_dir(),
                    help="model profile dir/file for neural members (default: deepfake_lens.cli.default_models_dir())")
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    base = args.manifest.parent
    rows: list[dict] = []
    unscored = 0
    for i, sample in enumerate(manifest["samples"]):
        path = Path(sample["path"])
        if not path.is_absolute():
            path = base / path
        item = analyze_file(path, model_path=args.model_path)
        if item.result is None:
            print(f"  [skip] {path.name}: no result ({item.error})")
            continue
        raw, basis = raw_member_score(item.result)
        if raw is None:
            print(f"  [unscored] {path.name}: no raw member score (no text model ran)")
            unscored += 1
            continue
        rows.append({**sample, "path": str(path), "score": float(raw), "score_basis": basis})
        if (i + 1) % 10 == 0:
            print(f"  scored {i + 1}/{len(manifest['samples'])}", flush=True)

    report: dict = {"threshold": args.threshold, "score_basis": SCORE_BASIS, "unscored": unscored,
                    "overall": None, "by_language": {}, "by_generator": {}}
    report["overall"] = _report("overall", rows, args.threshold)

    by_lang: dict[str, list] = defaultdict(list)
    by_gen: dict[str, list] = defaultdict(list)
    for r in rows:
        by_lang[r.get("language", "?")].append(r)
        by_gen[r.get("generator", "?")].append(r)
    for lang, group in sorted(by_lang.items()):
        report["by_language"][lang] = _report(f"language={lang}", group, args.threshold)
    for gen, group in sorted(by_gen.items()):
        report["by_generator"][gen] = _report(f"generator={gen}", group, args.threshold)

    report["rows"] = rows
    if args.json_out:
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        print(f"\nreport -> {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
