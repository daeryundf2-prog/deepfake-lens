#!/usr/bin/env python3
"""Record the golden scan output for the current output-format generation (R15-7, round 15).

    python scripts/update_golden_output.py [--check]

Scans the fixed fixture folder with a stdlib-only interpreter
(``deepfake_lens/tests/golden_output.py``), hashes the normalised JSON and
appends ``{generation, sha256}`` to ``deepfake_lens/tests/golden_output.json``
— only when ``scan_cache.OUTPUT_FORMAT_GENERATION`` is higher than the last
recorded generation. A changed output under the same generation is refused
(exit 1): bump the generation first, in the same commit. ``--check`` only
reports (exit 1 when the record is out of date).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from deepfake_lens.scan_cache import OUTPUT_FORMAT_GENERATION  # noqa: E402
from deepfake_lens.tests.golden_output import GOLDEN_PATH, check, digest, golden_payload  # noqa: E402

NOTE = (
    "R15-7: sha256 of the normalised scan JSON of the fixed fixture folder (deepfake_lens/tests/golden_output.py), "
    "per scan_cache.OUTPUT_FORMAT_GENERATION. Appended by scripts/update_golden_output.py; never edit an entry."
)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help

    parser = KoreanArgumentParser(description="골든 스캔 출력(R15-7): 고정 픽스처의 정규화 스캔 JSON 해시를 현재 출력 형식 세대와 함께 기록합니다.")
    parser.add_argument("--check", action="store_true", help="기록하지 않고 확인만(최신이 아니면 종료 코드 1)")
    args = parser.parse_args(argv)
    record = json.loads(GOLDEN_PATH.read_text(encoding="utf-8")) if GOLDEN_PATH.is_file() else {"note": NOTE, "history": []}
    history = record.setdefault("history", [])
    current = digest(golden_payload())
    problem = check(current, OUTPUT_FORMAT_GENERATION, history)
    if not problem:
        print(f"골든 최신: 세대 {OUTPUT_FORMAT_GENERATION}, sha256 {current}")
        return 0
    if args.check:
        print(problem)
        return 1
    if history and OUTPUT_FORMAT_GENERATION <= history[-1]["generation"]:
        print(problem)
        return 1
    if history and current == history[-1]["sha256"]:
        print(f"출력이 바뀌지 않았습니다(세대 {history[-1]['generation']}) — 세대만 올리지 마십시오.")
        return 1
    history.append({"generation": OUTPUT_FORMAT_GENERATION, "sha256": current})
    record["note"] = NOTE
    GOLDEN_PATH.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"골든 기록: 세대 {OUTPUT_FORMAT_GENERATION}, sha256 {current}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
