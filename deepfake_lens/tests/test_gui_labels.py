"""B5: gui.js label tables equal the Python ones, so they cannot drift.

Round 4 found "archive_member 미실행" and "출처 추정 (HIGH)" in the GUI: a
check name added to result_types.CHECK_LABELS never reached gui.js's copy,
and the source-guess confidence was printed as the raw enum value. This
test parses each ``const NAME = { … };`` label object out of gui.js and
compares it with the result_types table it mirrors.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from deepfake_lens.result_types import (
    CHECK_LABELS,
    COVERAGE_STATUS_LABELS,
    EVIDENCE_DIRECTION_LABELS,
    EVIDENCE_KIND_LABELS,
    EVIDENCE_STRENGTH_LABELS,
    REFERENCE_SOURCE_PREFIX,
    SOURCE_CONFIDENCE_LABELS,
    VERDICT_LABELS,
)

GUI_JS = Path(__file__).resolve().parents[1] / "gui.js"
_ENTRY = re.compile(r"""^\s*(?:(\w+)|'([^']+)')\s*:\s*'((?:[^'\\]|\\.)*)'\s*$""")


def js_object(source: str, name: str) -> dict[str, str]:
    """The ``const <name> = { key: 'value', … };`` object literal of ``source``."""
    match = re.search(rf"const {re.escape(name)} = \{{(.*?)\}};", source, re.DOTALL)
    if match is None:
        raise AssertionError(f"gui.js has no `const {name} = {{…}};`")
    body = re.sub(r"//[^\n]*", "", match.group(1))
    table: dict[str, str] = {}
    for raw in re.split(r",(?=(?:[^']*'[^']*')*[^']*$)", body):
        if not raw.strip():
            continue
        entry = _ENTRY.match(raw)
        if entry is None:
            raise AssertionError(f"{name}: cannot parse entry {raw.strip()!r}")
        table[entry.group(1) or entry.group(2)] = entry.group(3)
    return table


def by_value(table: dict) -> dict[str, str]:
    return {getattr(key, "value", key): label for key, label in table.items()}


class GuiLabelTablesTest(unittest.TestCase):
    source: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = GUI_JS.read_text(encoding="utf-8")

    def test_tables_equal_result_types(self) -> None:
        for js_name, table in (
            ("CHECK_LABELS", CHECK_LABELS),
            ("COVERAGE_STATUS_LABELS", COVERAGE_STATUS_LABELS),
            ("VERDICT_LABELS", VERDICT_LABELS),
            ("KIND_LABELS", EVIDENCE_KIND_LABELS),
            ("DIRECTION_LABELS", EVIDENCE_DIRECTION_LABELS),
            ("STRENGTH_LABELS", EVIDENCE_STRENGTH_LABELS),
            ("SOURCE_CONFIDENCE_LABELS", SOURCE_CONFIDENCE_LABELS),
        ):
            with self.subTest(table=js_name):
                self.assertEqual(js_object(self.source, js_name), by_value(table))

    def test_archive_member_is_labelled(self) -> None:
        self.assertEqual(js_object(self.source, "CHECK_LABELS")["archive_member"], CHECK_LABELS["archive_member"])

    def test_source_guess_confidence_is_never_the_raw_code(self) -> None:
        self.assertIn(f"const REFERENCE_SOURCE_PREFIX = '{REFERENCE_SOURCE_PREFIX}';", self.source)
        self.assertNotIn("sg.confidence || 'unknown'", self.source)
        self.assertIn("출처 추정 (${escapeHtml(sourceConfidenceLabel(r, sg))})", self.source)
        function = re.search(r"function sourceConfidenceLabel\(r, sg\) \{(.*?)\n        \}", self.source, re.DOTALL)
        assert function is not None
        self.assertIn("return '참고'", function.group(1))
        self.assertIn("r.grade === 'reference'", function.group(1))

    def test_parser_rejects_a_drifted_table(self) -> None:
        drifted = self.source.replace("archive_member: '압축 구성 파일',", "")
        self.assertNotEqual(js_object(drifted, "CHECK_LABELS"), by_value(CHECK_LABELS))


if __name__ == "__main__":
    unittest.main()
