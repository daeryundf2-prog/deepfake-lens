#!/usr/bin/env python3
"""Drift check for the vendored lazy-contracts files in contracts/.

contracts/PIN.json records the upstream commit and the sha256 of every
vendored file. Editing a vendored file without re-pinning fails this gate —
silent drift of the evidence schema or path-confinement helper is a
supply-chain risk, so re-vendoring must be deliberate: update the file and
PIN.json (with the new upstream commit) in the same change.

Exit 0 when every file matches its pin, 1 on missing/drifted/unpinned files.
"""

import hashlib
import json
import sys
from pathlib import Path

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
PIN_PATH = CONTRACTS / "PIN.json"


def main() -> int:
    pin = json.loads(PIN_PATH.read_text(encoding="utf-8"))
    expected: dict[str, str] = pin["sha256"]
    upstream = pin.get("upstream", "?")
    commit = pin.get("upstream_commit", "?")
    print(f"pinned upstream: {upstream} @ {commit}")

    failures: list[str] = []
    for name, want in sorted(expected.items()):
        path = CONTRACTS / name
        if not path.is_file():
            failures.append(f"MISSING  {name} (listed in PIN.json but absent)")
            continue
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        if got != want:
            failures.append(
                f"DRIFT    {name}\n"
                f"           pinned {want}\n"
                f"           actual {got}"
            )
        else:
            print(f"ok       {name}  sha256:{got[:16]}…")

    # Project-owned contracts (e.g. the scan result schema) are pinned in a
    # separate "local" map: they are not vendored, but an edit must still
    # come with a re-pin in the same commit.
    local: dict[str, str] = pin.get("local", {})
    for name, want in sorted(local.items()):
        path = CONTRACTS / name
        if not path.is_file():
            failures.append(f"MISSING  {name} (listed in PIN.json local but absent)")
            continue
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        if got != want:
            failures.append(
                f"DRIFT    {name} (local)\n"
                f"           pinned {want}\n"
                f"           actual {got}"
            )
        else:
            print(f"ok       {name}  sha256:{got[:16]}… (local)")

    # Anything else in contracts/ is unpinned and would drift unchecked.
    unpinned = sorted(
        p.name
        for p in CONTRACTS.iterdir()
        if p.is_file() and p.name not in expected and p.name not in local and p.name != PIN_PATH.name
    )
    for name in unpinned:
        failures.append(f"UNPINNED {name} (add it to PIN.json or remove it)")

    if failures:
        print("\ncontract drift detected:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        print(
            "\nIf this change is intentional, re-vendor from "
            f"{upstream} and update contracts/PIN.json in the same commit.",
            file=sys.stderr,
        )
        return 1
    print(f"\n{len(expected)} vendored + {len(local)} local file(s) match PIN.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
