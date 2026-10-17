"""TB: every table text the TB checks show in a row is Korean (no English run) — sources and notes are not shown."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from deepfake_lens.error_text import english_prose

MODELS = Path(__file__).resolve().parents[1] / "models"
# Fields a row shows (verbatim file values and identifiers are shown inside corner brackets or are not prose).
SHOWN = {
    "platform-traces.json": ("platform", "confidence", "meaning"),
    "device-labels.json": ("title", "meaning", "confidence", "self_declared_note"),
    "screen-traits.json": ("confidence",),
    "generator-strings.json": ("label", "reason", "service", "confidence"),
    "watermark-registry.json": ("generator", "title"),
}


def _strings(node: object, keys: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in keys and isinstance(value, str):
                out.append(value)
            else:
                out.extend(_strings(value, keys))
    elif isinstance(node, list):
        for value in node:
            out.extend(_strings(value, keys))
    return out


class TableTextIsKoreanTest(unittest.TestCase):
    def test_shown_fields_have_no_english_run(self) -> None:
        offenders = []
        for name, keys in SHOWN.items():
            for text in _strings(json.loads((MODELS / name).read_text(encoding="utf-8")), keys):
                hit = english_prose(text)
                if hit:
                    offenders.append(f"{name}: {hit!r} in {text!r}")
        # Phase-0 rule texts moved unchanged into generator-strings.json are exempt (their wording is phase 0's).
        phase0 = {"Flux / Black Forest Labs 추정", "Flux 또는 Black Forest Labs 단서가 메타데이터에 있습니다.", "Canva AI/Magic Media 단서가 메타데이터에 있습니다."}
        offenders = [o for o in offenders if not any(repr(p) in o for p in phase0)]
        self.assertEqual(offenders, [], "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
