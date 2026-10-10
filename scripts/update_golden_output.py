#!/usr/bin/env python3
"""Record the golden scan output for the current output-format generation (R15-7, R16-5).

    python scripts/update_golden_output.py --reason '<사유>'
    python scripts/update_golden_output.py --check

Scans the fixed fixture folder (``deepfake_lens/tests/golden_output.py``)
twice — a stdlib-only child (``stdlib``) and a child with the site packages
(``full-extras``, needs ``golden_output.FULL_EXTRAS_REQUIRED``) — hashes
the normalised JSON and the user-facing Korean constants of ``result_text``
and ``core`` (``constants``), and appends ``{generation, reason, sets,
constants_sha256, full_extras_environment}`` to
``deepfake_lens/tests/golden_output.json``.

- A hash that changed under the same ``scan_cache.OUTPUT_FORMAT_GENERATION``
  is refused (exit 1): raise the generation first, in the same commit.
- R16-5: a raised generation is always recorded — whether or not a hash
  changed — and needs ``--reason`` (exit 2 without it), which is stored.
- Recording needs a full-extras environment (exit 1 otherwise: every new
  entry carries all three hashes).

``--check`` only reports (exit 1 when the record is out of date); the
full-extras hash is compared only where the environment matches the one it
was recorded in (the difference is printed otherwise).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from deepfake_lens.scan_cache import OUTPUT_FORMAT_GENERATION  # noqa: E402
from deepfake_lens.tests.golden_output import (  # noqa: E402
    FULL_EXTRAS_SET,
    GOLDEN_PATH,
    STDLIB_SET,
    check,
    digest,
    environment,
    golden_payload,
    missing_full_extras,
    user_constants,
)

NOTE = (
    "R15-7/R16-5: per scan_cache.OUTPUT_FORMAT_GENERATION, sha256 of the normalised scan JSON of the fixed fixture folder "
    "(deepfake_lens/tests/golden_output.py) in a stdlib-only child (sets.stdlib; 'sha256' repeats it) and in a child with the "
    "site packages (sets.full-extras, recorded in full_extras_environment), and of the user-facing Korean constants of "
    "result_text and core (constants_sha256), with the reason the generation was raised. Appended by "
    "scripts/update_golden_output.py; never edit an entry."
)


def _environment_difference(recorded: dict[str, str] | None, current: dict[str, str]) -> str:
    recorded = recorded or {}
    parts = [
        f"{module}: {recorded.get(module, '없음')} → {current.get(module, '없음')}"
        for module in sorted(set(recorded) | set(current))
        if recorded.get(module) != current.get(module)
    ]
    return "; ".join(parts) or "(차이 없음)"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help

    parser = KoreanArgumentParser(description="골든 스캔 출력(R15-7, R16-5): 고정 픽스처의 정규화 스캔 JSON(표준 라이브러리 전용·전체 선택 의존성)과 한국어 문구 상수의 해시를 현재 출력 형식 세대와 함께 기록합니다.")
    parser.add_argument("--check", action="store_true", help="기록하지 않고 확인만(최신이 아니면 종료 코드 1)")
    parser.add_argument("--reason", default="", help="세대를 올린 사유(새 세대를 기록할 때 필수, 골든 파일에 저장)")
    args = parser.parse_args(argv)
    record = json.loads(GOLDEN_PATH.read_text(encoding="utf-8")) if GOLDEN_PATH.is_file() else {"note": NOTE, "history": []}
    history = record.setdefault("history", [])
    described = environment()
    constants = user_constants()
    current = {STDLIB_SET: digest(golden_payload(STDLIB_SET)), "constants": digest(constants)}
    lacking = missing_full_extras(described)
    if not lacking:
        current[FULL_EXTRAS_SET] = digest(golden_payload(FULL_EXTRAS_SET))
    compared = dict(current)
    recorded_environment = history[-1].get("full_extras_environment") if history else None
    note = ""
    if FULL_EXTRAS_SET in compared and recorded_environment is None:
        compared.pop(FULL_EXTRAS_SET)
        note = "마지막 세대에 full-extras 골든이 없어 비교하지 않았습니다(R15-7 형식 기록)"
    elif FULL_EXTRAS_SET in compared and recorded_environment != described:
        compared.pop(FULL_EXTRAS_SET)
        note = f"full-extras 골든은 다른 환경에서 기록되어 비교하지 않았습니다 — {_environment_difference(recorded_environment, described)}"
    elif lacking:
        note = f"full-extras 골든은 비교하지 않았습니다 — 없는 모듈: {', '.join(lacking)}"
    problem = check(compared, OUTPUT_FORMAT_GENERATION, history)
    if note:
        print(note)
    if not problem:
        print(f"골든 최신: 세대 {OUTPUT_FORMAT_GENERATION}, " + ", ".join(f"{name} {value}" for name, value in sorted(compared.items())))
        return 0
    if args.check:
        print(problem)
        return 1
    if history and OUTPUT_FORMAT_GENERATION <= history[-1]["generation"]:
        print(problem)
        return 1
    reason = args.reason.strip()
    if not reason:
        print("오류: 새 세대를 기록하려면 --reason '<사유>'가 필요합니다(R16-5 — 사유는 골든 파일에 저장됩니다).", file=sys.stderr)
        return 2
    if lacking:
        print(f"오류: 새 세대의 골든에는 full-extras 세트가 필요합니다 — 이 환경에 없는 모듈: {', '.join(lacking)}", file=sys.stderr)
        return 1
    history.append(
        {
            "generation": OUTPUT_FORMAT_GENERATION,
            "reason": reason,
            "sha256": current[STDLIB_SET],
            "sets": {STDLIB_SET: current[STDLIB_SET], FULL_EXTRAS_SET: current[FULL_EXTRAS_SET]},
            "constants_sha256": current["constants"],
            "constants_count": len(constants),
            "full_extras_environment": described,
        }
    )
    record["note"] = NOTE
    GOLDEN_PATH.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"골든 기록: 세대 {OUTPUT_FORMAT_GENERATION} — {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
