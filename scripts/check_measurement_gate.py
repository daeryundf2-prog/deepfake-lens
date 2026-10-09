#!/usr/bin/env python3
"""Measurement gate (phase 0, WP-I — G26/G28, QA-SYS-9).

For every ``<models_dir>/*-runtime.json`` whose ``supported`` is true (or
absent — absent counts as true, as in experiments/eval_all.py), require a
``measured_on`` record on a reproducible corpus's *test* split with
n_pos >= 200, n_neg >= 200 and an AUROC 95% CI lower bound >= 0.85
(text members: n_pos/n_neg >= 200 and ``recall_at_fpr_0_01`` present, no
AUROC floor), and a 64-hex ``manifest_sha256`` naming the corpus manifest
whose file ``manifest_path`` must exist and hash to it (D16).
Rules live in ``deepfake_lens/measurement_gate.py``.

    python scripts/check_measurement_gate.py [--models-dir DIR]

Exit 0 when every active profile is measured, 1 otherwise (one Korean
explanation per failing profile), 2 when the directory does not exist.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from deepfake_lens.measurement_gate import PROFILE_GLOB, check_models_dir, gate_report_lines  # noqa: E402

DEFAULT_MODELS_DIR = REPO_ROOT / "deepfake_lens" / "models"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help
    parser = KoreanArgumentParser(description="측정 게이트(0단계 WP-I, G26/G28, QA-SYS-9): supported 프로필의 측정 기록을 검사합니다.")
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR, help="프로필 폴더(기본: deepfake_lens/models)")
    args = parser.parse_args(argv)
    if not args.models_dir.is_dir():
        print(f"모델 디렉터리가 없습니다: {args.models_dir}")
        return 2
    results = check_models_dir(args.models_dir)
    print(f"측정 게이트 검사: {args.models_dir} ({PROFILE_GLOB} {len(results)}개)")
    lines, passed = gate_report_lines(results)
    for line in lines:
        print(line)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
