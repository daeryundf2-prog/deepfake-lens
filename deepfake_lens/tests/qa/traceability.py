"""QA traceability helpers shared by the QA meta-tests and scripts/qa_phase0.py.

- ``load_traceability()`` reads ``traceability.json`` (requirement -> gap ->
  QA ID table plus the verbatim pass criteria).
- ``qa_tag(test)`` maps a unittest case to its QA ID: the method docstring's
  first line when it starts with a QA ID, else the class docstring's.
- ``is_canonical(test, criteria)`` is True when the method docstring's first
  line is exactly ``"<QA ID>: <criteria>"`` — each automated QA ID has
  exactly one such test.
- ``source_inventory(root)`` lists ``relpath::Class.test_method`` for every
  test method defined in a ``test*.py`` file (AST, no import), the unit the
  QA-SYS-10 baseline is recorded in.
"""

from __future__ import annotations

import ast
import json
import re
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any

QA_DIR = Path(__file__).resolve().parent
TESTS_DIR = QA_DIR.parent
REPO_ROOT = TESTS_DIR.parents[1]
TRACEABILITY_PATH = QA_DIR / "traceability.json"
BASELINE_PATH = QA_DIR / "test_inventory_baseline.json"
DELETIONS_DOC = REPO_ROOT / "docs" / "TEST-DELETIONS.md"

QA_ID = re.compile(r"^(QA-[A-Z]+-\d+)(?=[:\s(]|$)")
# G11 (round 5): the docstring first line of every test in tests/qa is either
# "<QA-ID>: <criterion verbatim>" (the one canonical test) or
# "<QA-ID>: 보조 검사 — <what it checks>".
AUXILIARY_PREFIX = "보조 검사 — "
# `Class.test_method` tokens in docs/TEST-DELETIONS.md.
DELETION_TOKEN = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*\.test[A-Za-z0-9_]*)`")


def load_traceability(path: Path = TRACEABILITY_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != "deepfake-lens-qa-traceability-v1":
        raise ValueError(f"not a traceability-v1 file: {path}")
    return data


def automated_criteria(data: dict[str, Any]) -> dict[str, str]:
    return {qa_id: str(entry["criteria"]) for qa_id, entry in data["qa"].items() if entry.get("mode") == "automated"}


def first_line(doc: str | None) -> str:
    lines = (doc or "").strip().splitlines()
    return lines[0].strip() if lines else ""


def iter_tests(suite: unittest.TestSuite | unittest.TestCase) -> Iterator[unittest.TestCase]:
    if isinstance(suite, unittest.TestCase):
        yield suite
        return
    for child in suite:
        yield from iter_tests(child)


def qa_tag(test: unittest.TestCase) -> str | None:
    """The QA ID a test case belongs to (method docstring first, then class)."""
    method_doc = getattr(getattr(test, test._testMethodName, None), "__doc__", None)
    for doc in (method_doc, type(test).__doc__):
        match = QA_ID.match(first_line(doc))
        if match:
            return match.group(1)
    return None


def is_canonical(test: unittest.TestCase, criteria: dict[str, str]) -> bool:
    method_doc = getattr(getattr(test, test._testMethodName, None), "__doc__", None)
    line = first_line(method_doc)
    match = QA_ID.match(line)
    if match is None:
        return False
    qa_id = match.group(1)
    return qa_id in criteria and line == f"{qa_id}: {criteria[qa_id]}"


def canonical_tests(tests: list[unittest.TestCase], criteria: dict[str, str]) -> dict[str, list[str]]:
    """QA ID -> ids of the tests whose docstring first line is the criterion."""
    found: dict[str, list[str]] = {qa_id: [] for qa_id in criteria}
    for test in tests:
        if is_canonical(test, criteria):
            tag = qa_tag(test)
            assert tag is not None
            found[tag].append(test.id())
    return found


def source_inventory(root: Path = TESTS_DIR) -> set[str]:
    """``relpath::Class.test_method`` for each test method in ``test*.py`` under ``root``.

    Only methods defined directly in a module-level class count (the same
    rule the recorded baseline used), so the count does not depend on
    imports, skips or inherited mixins.
    """
    names: set[str] = set()
    for path in sorted(root.rglob("test*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test"):
                        names.add(f"{rel}::{node.name}.{item.name}")
    return names


def short_names(names: set[str]) -> set[str]:
    """Drop the file part: tests that moved between files keep their identity."""
    return {name.split("::", 1)[1] for name in names}


def documented_deletions(path: Path = DELETIONS_DOC) -> dict[str, str]:
    """``Class.test_method`` -> section ("삭제"/"이름 변경"/…) from docs/TEST-DELETIONS.md."""
    section = ""
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        if line.startswith("|") and section:
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            match = DELETION_TOKEN.search(cells[0]) if cells else None
            if match:
                found[match.group(1)] = section
    return found
