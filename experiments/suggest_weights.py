"""Derive ensemble_weight suggestions from an eval_all.py report.

    python experiments/eval_all.py --corpus <dir> --report report.json
    python experiments/suggest_weights.py report.json [--write]

Weight rule (transparent, documented): a member's discriminating power is
its AUROC distance from chance; the suggestion is
``clamp(4 * (auroc - 0.5), 0.05, 4.0)`` — AUROC 0.95 -> 1.8, AUROC 0.55 ->
0.2, AUROC <= 0.5 -> floor 0.05. Members with <20 scored samples per class
or no AUROC keep their current weight (insufficient evidence).

With --write the suggestion is stored in each profile JSON as
``ensemble_weight`` plus a ``weight_basis`` note naming the report. Without
--write it prints the table only. Always review before committing — a
single corpus can overfit the weights to its distribution.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def suggest(member: dict) -> float | None:
    auc = member.get("auroc")
    if auc is None:
        return None
    if member.get("pos", 0) < 20 or member.get("neg", 0) < 20:
        return None
    return max(0.05, min(4.0, round(4.0 * (auc - 0.5), 2)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    print("| member | AUROC | suggested weight |")
    print("|---|---|---|")
    for block in report["modalities"]:
        if "skipped" in block:
            continue
        for member in block["members"]:
            if member["profile"].startswith("<"):
                continue
            w = suggest(member)
            print(f"| {member['profile']} | {member.get('auroc')} | "
                  f"{w if w is not None else 'insufficient data'} |")
            if args.write and w is not None:
                path = REPO_ROOT / "models" / member["profile"]
                prof = json.loads(path.read_text(encoding="utf-8"))
                prof["ensemble_weight"] = w
                prof["weight_basis"] = (
                    f"derived from measured AUROC on {args.report.name} — "
                    "corpus-specific, re-derive after corpus changes"
                )
                path.write_text(json.dumps(prof, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
