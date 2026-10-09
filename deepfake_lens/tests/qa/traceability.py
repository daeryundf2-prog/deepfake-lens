"""QA traceability helpers shared by the QA meta-tests and scripts/qa_phase0.py.

- ``load_traceability()`` reads ``traceability.json`` (requirement -> gap ->
  QA ID table plus the verbatim pass criteria).
- ``qa_tag(test)`` maps a unittest case to its QA ID: the method docstring's
  first line when it starts with a QA ID, else the class docstring's.
- ``criterion_line(qa_id, criteria)`` is the exact first line every test of
  that QA ID carries — ``"<QA ID>: <통과 기준 원문>"`` (N16: no "보조 검사"
  variant; what a particular test checks goes on the second line).
- ``tests_by_qa_id(tests, criteria)`` groups the tests whose first line is
  their QA ID's criterion line; a QA ID's result folds all of them.
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
# N16 (round 6): the docstring first line of EVERY test in tests/qa is
# "<QA-ID>: <criterion verbatim from the spec>" (traceability.json
# ``criteria``); the optional second line says what that test checks. The
# round-5 "<QA-ID>: 보조 검사 — …" first lines are gone.
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


def criterion_line(qa_id: str, criteria: dict[str, str]) -> str:
    """The first docstring line every test of ``qa_id`` carries (N16)."""
    return f"{qa_id}: {criteria[qa_id]}"


def carries_criterion(test: unittest.TestCase, criteria: dict[str, str]) -> bool:
    """True when the method docstring's first line is its QA ID's criterion line."""
    method_doc = getattr(getattr(test, test._testMethodName, None), "__doc__", None)
    line = first_line(method_doc)
    match = QA_ID.match(line)
    if match is None:
        return False
    qa_id = match.group(1)
    return qa_id in criteria and line == criterion_line(qa_id, criteria)


def tests_by_qa_id(tests: list[unittest.TestCase], criteria: dict[str, str]) -> dict[str, list[str]]:
    """QA ID -> ids of the tests whose docstring first line is that QA ID's criterion line."""
    found: dict[str, list[str]] = {qa_id: [] for qa_id in criteria}
    for test in tests:
        if carries_criterion(test, criteria):
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


# R11-2 (round 11): environment-dependent tests. A test that skips without
# an optional package, tool, platform feature or fixture is listed with its
# reason and the extras that enable it in docs/QA-ENV-DEPENDENT-TESTS.md;
# every skip reason in the suite must appear there (meta-test in
# test_qa_sys.py). Reasons are read from the source (AST, no import):
# ``@unittest.skip/skipIf/skipUnless(..., reason)``, ``self.skipTest(reason)``
# and ``raise unittest.SkipTest(reason)``. A reason is a string literal, a
# module-level string constant, or an f-string whose placeholders are kept
# as written (``f"numpy unavailable: {exc}"`` -> ``numpy unavailable: {exc}``).
ENV_DEPENDENT_DOC = REPO_ROOT / "docs" / "QA-ENV-DEPENDENT-TESTS.md"
_SKIP_DECORATORS = {"skip": 0, "skipIf": 1, "skipUnless": 1}
_SETUP_NAMES = ("setUp", "setUpClass", "asyncSetUp")
UNRESOLVED_REASON = "<해석 불가>"


def _string_constants(tree: ast.Module) -> dict[str, ast.expr]:
    found: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            found[node.targets[0].id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            found[node.target.id] = node.value
    return found


def _reason_text(node: ast.expr | None, constants: dict[str, ast.expr], depth: int = 0) -> str:
    """The skip reason ``node`` evaluates to, f-string placeholders kept as ``{expr}``."""
    if node is None or depth > 5:
        return UNRESOLVED_REASON
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("{" + ast.unparse(value.value) + "}")
        return "".join(parts)
    if isinstance(node, ast.Name) and node.id in constants:
        return _reason_text(constants[node.id], constants, depth + 1)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _reason_text(node.left, constants, depth + 1), _reason_text(node.right, constants, depth + 1)
        return UNRESOLVED_REASON if UNRESOLVED_REASON in (left, right) else left + right
    return UNRESOLVED_REASON


def _call_name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""


def _skip_call_reason(call: ast.Call, constants: dict[str, ast.expr]) -> str | None:
    """The reason of a skip call (``skipTest``/``SkipTest``/``skip*`` decorator), else None."""
    name = _call_name(call)
    keyword = next((kw.value for kw in call.keywords if kw.arg in {"reason", "msg"}), None)
    if name in {"skipTest", "SkipTest"}:
        return _reason_text(call.args[0] if call.args else keyword, constants)
    if name in _SKIP_DECORATORS:
        index = _SKIP_DECORATORS[name]
        return _reason_text(call.args[index] if len(call.args) > index else keyword, constants)
    return None


def skip_reason_sites(root: Path = TESTS_DIR) -> list[tuple[str, int, str]]:
    """``(relpath, line, reason)`` for every skip call in the test sources under ``root``."""
    sites: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = _string_constants(tree)
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                reason = _skip_call_reason(node, constants)
                if reason is not None:
                    sites.append((rel, node.lineno, reason))
    return sites


class _ModuleSkips:
    """Skip reasons reachable from each test method of one module (static)."""

    def __init__(self, tree: ast.Module, imported: dict[str, set[str]]) -> None:
        self.constants = _string_constants(tree)
        self.functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        self.imported = imported  # name imported from another test module -> its reasons

    def _bases(self, cls: ast.ClassDef) -> list[ast.ClassDef]:
        chain: list[ast.ClassDef] = []
        for base in cls.bases:
            name = base.id if isinstance(base, ast.Name) else None
            if name in self.classes and self.classes[name] is not cls:
                parent = self.classes[name]
                chain.append(parent)
                chain.extend(self._bases(parent))
        return chain

    def _method(self, cls: ast.ClassDef, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        for owner in (cls, *self._bases(cls)):
            for item in owner.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
                    return item
        return None

    def reasons_in(self, node: ast.AST, cls: ast.ClassDef | None, seen: set[int]) -> set[str]:
        """Reasons of skip calls in ``node`` and in the same-module helpers it calls."""
        if id(node) in seen:
            return set()
        seen.add(id(node))
        found: set[str] = set()
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            reason = _skip_call_reason(child, self.constants)
            if reason is not None:
                found.add(reason)
                continue
            func = child.func
            target: ast.AST | None = None
            if isinstance(func, ast.Name):
                target = self.functions.get(func.id)
                found |= self.imported.get(func.id, set())
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in {"self", "cls"} and cls is not None:
                target = self._method(cls, func.attr)
            if target is not None:
                found |= self.reasons_in(target, cls, seen)
        return found

    def function_reasons(self, name: str) -> set[str]:
        node = self.functions.get(name)
        return self.reasons_in(node, None, set()) if node is not None else set()

    def tests(self) -> Iterator[tuple[str, str, set[str]]]:
        """``(class name, defining class name, reasons)`` per test method run under each class."""
        module_wide = self.function_reasons("setUpModule")
        for cls in self.classes.values():
            owners = (cls, *self._bases(cls))
            class_wide = set(module_wide)
            for owner in owners:
                for decorator in owner.decorator_list:
                    if isinstance(decorator, ast.Call):
                        reason = _skip_call_reason(decorator, self.constants)
                        if reason is not None:
                            class_wide.add(reason)
            for setup in _SETUP_NAMES:
                method = self._method(cls, setup)
                if method is not None:
                    class_wide |= self.reasons_in(method, cls, set())
            done: set[str] = set()
            for owner in owners:
                for item in owner.body:
                    if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) or not item.name.startswith("test") or item.name in done:
                        continue
                    done.add(item.name)
                    reasons = set(class_wide)
                    for decorator in item.decorator_list:
                        if isinstance(decorator, ast.Call):
                            reason = _skip_call_reason(decorator, self.constants)
                            if reason is not None:
                                reasons.add(reason)
                    reasons |= self.reasons_in(item, cls, set())
                    yield cls.name, f"{owner.name}.{item.name}", reasons


def skipping_tests(root: Path = TESTS_DIR, *, base: Path | None = None) -> dict[str, tuple[str, set[str]]]:
    """``Class.test_method`` (as run) -> (defining ``Class.test_method``, skip reasons).

    Only tests with at least one reachable skip are listed. Helpers are
    followed within the module and through ``from <test module> import name``
    (module names relative to ``base``, default the repository root).
    """
    trees = {path: ast.parse(path.read_text(encoding="utf-8")) for path in sorted(root.rglob("*.py"))}
    base = REPO_ROOT if base is None else base
    module_of = {".".join(path.relative_to(base).with_suffix("").parts): path for path in trees}
    helper_reasons: dict[Path, dict[str, set[str]]] = {}

    def helpers(path: Path) -> dict[str, set[str]]:
        if path not in helper_reasons:
            helper_reasons[path] = {}
            module = _ModuleSkips(trees[path], {})
            helper_reasons[path] = {name: module.function_reasons(name) for name in module.functions}
        return helper_reasons[path]

    found: dict[str, tuple[str, set[str]]] = {}
    for path, tree in trees.items():
        if not path.name.startswith("test"):
            continue
        imported: dict[str, set[str]] = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module in module_of:
                source = helpers(module_of[node.module])
                for alias in node.names:
                    if source.get(alias.name):
                        imported[alias.asname or alias.name] = source[alias.name]
        for run_class, defined, reasons in _ModuleSkips(tree, imported).tests():
            if reasons:
                found[f"{run_class}.{defined.split('.', 1)[1]}"] = (defined, reasons)
    return found


def documented_env_reasons(path: Path = ENV_DEPENDENT_DOC) -> set[str]:
    """Skip reasons (first-column backtick text) of the reasons table in docs/QA-ENV-DEPENDENT-TESTS.md."""
    return _doc_table_keys(path, "## 건너뛰기 사유")


def documented_env_tests(path: Path = ENV_DEPENDENT_DOC) -> set[str]:
    """``Class.test_method`` rows of the baseline table in docs/QA-ENV-DEPENDENT-TESTS.md."""
    return _doc_table_keys(path, "## 환경 의존 기준선 테스트")


def _doc_table_keys(path: Path, heading: str) -> set[str]:
    text = path.read_text(encoding="utf-8")
    if heading not in text:
        return set()
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    keys: set[str] = set()
    for line in section.splitlines():
        if line.startswith("| `"):
            cell = line[1:].split(" | ", 1)[0].strip()
            if cell.startswith("`` ") and cell.endswith(" ``"):
                cell = cell[3:-3]  # a reason holding a backtick
            elif cell.startswith("`") and cell.endswith("`"):
                cell = cell[1:-1]
            keys.add(cell.replace("\\|", "|"))
    return keys


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
