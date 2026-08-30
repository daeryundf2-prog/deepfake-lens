"""Verify that every external URL cited by the registry and docs resolves.

The v2.0 expansion shipped links that never existed (fabricated repos,
placeholder arXiv IDs). This checker keeps the registry honest: CI runs it
weekly and fails when a cited URL becomes unreachable.

Exit code 0 when every URL is reachable, 1 otherwise.
"""

from __future__ import annotations

import re
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES = [
    REPO_ROOT / "deepfake_lens" / "model_registry.py",
    REPO_ROOT / "README.md",
    *sorted((REPO_ROOT / "docs").glob("*.md")),
]
URL_PATTERN = re.compile(r"https?://[^\s\"'\\)\]>]+")
USER_AGENT = {"User-Agent": "deepfake-lens-link-check/1.0 (+https://github.com/daeryundf2-prog/deepfake-lens)"}
TIMEOUT_SECONDS = 20
RETRIES = 2


def extract_urls() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        for raw in URL_PATTERN.findall(text):
            url = raw.rstrip(".,;")
            if url not in seen:
                seen.add(url)
                found.append((path.name, url))
    return found


def check(url: str) -> tuple[bool, str]:
    request = urllib.request.Request(url, headers=USER_AGENT, method="GET")
    last_error = "unknown"
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                code = getattr(response, "status", 200)
                if code in (403, 429):
                    # Bot protection: the host exists but refuses automated
                    # clients. Treat as reachable, surface as a warning.
                    return True, f"HTTP {code} (bot-blocked)"
                return (code < 400), f"HTTP {code}"
        except Exception as exc:  # noqa: BLE001 - report any network failure
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < RETRIES:
                time.sleep(2 * (attempt + 1))
    return False, last_error


def main() -> int:
    entries = extract_urls()
    print(f"Checking {len(entries)} URLs from {len(SOURCES)} files...")
    failures: list[tuple[str, str, str]] = []
    for source, url in entries:
        ok, detail = check(url)
        print(f"  [{'OK  ' if ok else 'FAIL'}] {url} ({source}: {detail})")
        if not ok:
            failures.append((source, url, detail))
    if failures:
        print(f"\n{len(failures)} unreachable URL(s):", file=sys.stderr)
        for source, url, detail in failures:
            print(f"  {url} ({source}: {detail})", file=sys.stderr)
        return 1
    print("\nAll cited URLs reachable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
