#!/usr/bin/env python3
"""Evaluate AI-text detection on a labeled corpus.

Manifest format (JSON):
  {"samples": [{"path": "relative/or/abs.txt", "label": "ai"|"human",
                "generator": "devin|codex|claude|gemini|grok|kimi|human|...",
                "language": "ko"|"en"|...}]}

Reports AUROC, EER, precision/recall/FPR at configurable thresholds,
plus per-language and per-generator breakdowns. Scores are produced by the
full text ensemble (statistical layer + wired neural profiles), so results
reflect what the tool actually ships — not an idealized single model.

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


def _auroc(labels: list[int], scores: list[float]) -> float | None:
    """Rank-based AUROC; None if only one class present."""
    pos = [s for l, s in zip(labels, scores) if l == 1]
    neg = [s for l, s in zip(labels, scores) if l == 0]
    if not pos or not neg:
        return None
    wins = ties = 0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1
            elif p == n:
                ties += 1
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def _eer(labels: list[int], scores: list[float]) -> float | None:
    """Equal-error rate: threshold where FPR ~= FNR, midpoint reported."""
    thresholds = sorted(set(scores))
    best = None
    for t in thresholds:
        fp = sum(1 for l, s in zip(labels, scores) if l == 0 and s >= t)
        fn = sum(1 for l, s in zip(labels, scores) if l == 1 and s < t)
        tn = sum(1 for l, s in zip(labels, scores) if l == 0 and s < t)
        tp = sum(1 for l, s in zip(labels, scores) if l == 1 and s >= t)
        fpr = fp / (fp + tn) if fp + tn else 0.0
        fnr = fn / (fn + tp) if fn + tp else 0.0
        diff = abs(fpr - fnr)
        if best is None or diff < best[0]:
            best = (diff, (fpr + fnr) / 2)
    return best[1] if best else None


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
    out = {
        "n": len(rows),
        "auroc": _auroc(labels, scores),
        "eer": _eer(labels, scores),
        f"confusion@{threshold:g}": _confusion(labels, scores, threshold),
    }
    print(f"\n== {name} (n={out['n']}) ==")
    print(f"  AUROC: {out['auroc'] if out['auroc'] is None else round(out['auroc'], 3)}")
    print(f"  EER:   {out['eer'] if out['eer'] is None else round(out['eer'], 3)}")
    c = out[f"confusion@{threshold:g}"]
    print(f"  @{threshold:g}: tp={c['tp']} fp={c['fp']} fn={c['fn']} tn={c['tn']}"
          f" precision={c['precision']} recall={c['recall']} fpr={c['fpr']}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--threshold", type=float, default=50.0,
                    help="ensemble score threshold for the confusion matrix (default 50)")
    ap.add_argument("--model-path", type=Path,
                    default=Path(__file__).resolve().parent.parent / "models",
                    help="model profile dir/file for neural members (default: repo models/)")
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    base = args.manifest.parent
    rows: list[dict] = []
    for i, sample in enumerate(manifest["samples"]):
        path = Path(sample["path"])
        if not path.is_absolute():
            path = base / path
        item = analyze_file(path, model_path=args.model_path)
        if item.result is None:
            print(f"  [skip] {path.name}: no result ({item.error})")
            continue
        rows.append({**sample, "path": str(path), "score": float(item.result.score)})
        if (i + 1) % 10 == 0:
            print(f"  scored {i + 1}/{len(manifest['samples'])}", flush=True)

    report: dict = {"threshold": args.threshold, "overall": None, "by_language": {}, "by_generator": {}}
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
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nreport -> {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
