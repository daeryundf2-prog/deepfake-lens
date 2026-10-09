#!/usr/bin/env python3
"""Phase-0 QA harness: run the QA tests, write docs/CONFORMANCE.md (WP-J).

    python scripts/qa_phase0.py [--log-dir build/qa-logs] [--out docs/CONFORMANCE.md] [--qa-only]

1. Runs the whole unit-test suite in-process (QA-SYS-10 needs "유지 대상 전부
   통과"); ``--qa-only`` runs just ``tests/qa`` — the four area files
   test_qa_in.py / test_qa_out.py / test_qa_adv.py / test_qa_sys.py (W2) —
   plus the fail-closed and decision tests (QA-SYS-10 is then reported as
   건너뜀(환경)).
2. Collects per-test outcome, captured stdout/stderr, log records and
   tracebacks. Each test belongs to a QA ID through its docstring
   (``tests/qa/traceability.qa_tag``). N16: every QA test's docstring first
   line is "<QA ID>: <통과 기준 원문>"; a QA ID fails when one of its tests
   failed or it has no test, is 건너뜀(환경) when any of its tests was
   skipped (R10-3: a missing package, git history or tool is an environment
   gap, not a pass), and passes only when every one of its tests ran and
   passed.
3. Writes ``<log-dir>/<QA ID>.log`` per automated QA ID,
   ``<log-dir>/full-suite.log`` (one line per test, tracebacks of failures)
   and ``<log-dir>/results.json``.
4. Maps requirement -> gap -> QA ID from
   ``deepfake_lens/tests/qa/traceability.json`` and writes the conformance
   table: 요구사항 ID | 갭 ID | QA ID | 결과(통과/실패/수동/1단계) | 로그 경로.

Exit 0 when every automated test passed and each automated QA ID has at
least one test carrying its criterion line; 1 otherwise.

Preconditions of a recorded run (D6, D16):

- **fastapi + httpx** must be importable, or the harness exits 1 with
  "fastapi 필요" before running anything: QA-OUT-4 compares the CLI, the
  web server and the FastAPI server, and a record whose API leg was
  skipped is not a QA-OUT-4 pass. The unit-test suite itself still skips
  that leg without fastapi (CI). Run from a side venv that sees the
  project's other packages::

      python -m venv --system-site-packages /tmp/dflens-qa-venv
      /tmp/dflens-qa-venv/bin/pip install fastapi httpx uvicorn
      /tmp/dflens-qa-venv/bin/python scripts/qa_phase0.py

- **clean work tree**: tracked files must match HEAD (``git status
  --porcelain --untracked-files=no`` empty) unless ``--allow-dirty``; the
  header records that HEAD commit. The table is then committed on its own,
  so the commit holding docs/CONFORMANCE.md is a child of the recorded
  commit that changes nothing else — ``--verify-record`` checks exactly
  that (exit 1 otherwise).
"""

from __future__ import annotations

import importlib.util
import io
import json
import logging
import platform
import shutil
import subprocess
import sys
import time
import traceback
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from deepfake_lens.tests.qa.traceability import (  # noqa: E402
    TESTS_DIR,
    automated_criteria,
    iter_tests,
    load_traceability,
    qa_tag,
    tests_by_qa_id,
)

DEFAULT_LOG_DIR = REPO_ROOT / "build" / "qa-logs"
DEFAULT_OUT = REPO_ROOT / "docs" / "CONFORMANCE.md"
QA_ONLY_EXTRA_MODULES = ("deepfake_lens.tests.test_fail_closed", "deepfake_lens.tests.test_decision")
FULL_SUITE_QA_ID = "QA-SYS-10"
# Optional packages whose presence changes which tests run (reported in the header).
EXTRAS = (
    "numpy", "PIL", "cv2", "scipy", "librosa", "soundfile", "sklearn", "c2pa", "fastapi", "httpx", "uvicorn",
    "torch", "transformers", "mediapipe", "pymupdf", "fitz", "speechbrain", "py7zr", "rarfile",
)

# R10-3 (round 10): a QA ID with a skipped test is "건너뜀(환경)", never "통과".
PASS, FAIL, MANUAL, PHASE1, SKIPPED = "통과", "실패", "수동", "1단계", "건너뜀(환경)"
# QA-OUT-4's API-server leg needs these; a recorded run without them fails.
REQUIRED_FOR_RECORD = ("fastapi", "httpx")
SIDE_VENV_HINT = (
    "python -m venv --system-site-packages /tmp/dflens-qa-venv && "
    "/tmp/dflens-qa-venv/bin/pip install fastapi httpx uvicorn && "
    "/tmp/dflens-qa-venv/bin/python scripts/qa_phase0.py"
)
# Header line the record is parsed back from by --verify-record.
COMMIT_LINE_PREFIX = "- 검증 커밋: `"


@dataclass
class TestRecord:
    test_id: str
    qa_id: str | None
    status: str = "passed"  # passed | failed | error | skipped
    detail: str = ""
    output: str = ""
    seconds: float = 0.0
    subtest_failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in {"passed", "skipped"}


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: list[str] = []
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:  # noqa: BLE001 - a log formatting error must not abort the run
            self.lines.append(f"(log record unformattable: {record.msg!r})")


class RecordingResult(unittest.TestResult):
    """Per-test outcome plus captured stdout/stderr and log records."""

    def __init__(self) -> None:
        super().__init__()
        self.records: dict[str, TestRecord] = {}
        self._capture: io.StringIO | None = None
        self._saved: tuple[Any, Any] | None = None
        self._handler: _ListHandler | None = None
        self._started = 0.0

    def _record(self, test: unittest.TestCase) -> TestRecord:
        test_id = test.id()
        if test_id not in self.records:
            self.records[test_id] = TestRecord(test_id, qa_tag(test) if isinstance(test, unittest.TestCase) and hasattr(test, "_testMethodName") else None)
        return self.records[test_id]

    def startTest(self, test: unittest.TestCase) -> None:
        super().startTest(test)
        self._record(test)
        self._capture = io.StringIO()
        self._saved = (sys.stdout, sys.stderr)
        sys.stdout = sys.stderr = self._capture
        self._handler = _ListHandler()
        logging.getLogger().addHandler(self._handler)
        self._started = time.perf_counter()

    def stopTest(self, test: unittest.TestCase) -> None:
        record = self._record(test)
        record.seconds = time.perf_counter() - self._started
        if self._saved is not None:
            sys.stdout, sys.stderr = self._saved
            self._saved = None
        if self._handler is not None:
            logging.getLogger().removeHandler(self._handler)
            record.output = (self._capture.getvalue() if self._capture else "") + "".join(f"{line}\n" for line in self._handler.lines)
            self._handler = None
        super().stopTest(test)

    def _fail(self, test: unittest.TestCase, status: str, err: Any) -> None:
        record = self._record(test)
        record.status = status
        record.detail += "".join(traceback.format_exception(*err)) if isinstance(err, tuple) else str(err)

    def addError(self, test: unittest.TestCase, err: Any) -> None:
        super().addError(test, err)
        self._fail(test, "error", err)

    def addFailure(self, test: unittest.TestCase, err: Any) -> None:
        super().addFailure(test, err)
        self._fail(test, "failed", err)

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:
        super().addSkip(test, reason)
        record = self._record(test)
        record.status = "skipped"
        record.detail = reason

    def addUnexpectedSuccess(self, test: unittest.TestCase) -> None:
        super().addUnexpectedSuccess(test)
        self._fail(test, "failed", "unexpected success")

    def addSubTest(self, test: unittest.TestCase, subtest: unittest.TestCase, err: Any) -> None:
        super().addSubTest(test, subtest, err)
        if err is not None:
            record = self._record(test)
            failed = issubclass(err[0], test.failureException)
            record.status = "failed" if failed else "error"
            record.subtest_failures.append(subtest.id())
            record.detail += f"--- {subtest.id()}\n" + "".join(traceback.format_exception(*err))


def _git(*args: str) -> str:
    git = shutil.which("git")
    if git is None:
        return ""
    done = subprocess.run([git, *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    return done.stdout.strip() if done.returncode == 0 else ""


def environment() -> dict[str, Any]:
    from deepfake_lens.core import TOOL_VERSION

    commit = _git("rev-parse", "HEAD") or "알 수 없음(git 없음)"
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no"))
    return {
        "commit": commit,
        "dirty": dirty,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool_version": TOOL_VERSION,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "extras_present": [name for name in EXTRAS if importlib.util.find_spec(name) is not None],
        "extras_missing": [name for name in EXTRAS if importlib.util.find_spec(name) is None],
        "ffmpeg": bool(shutil.which("ffmpeg")),
    }


def load_suite(qa_only: bool) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    if not qa_only:
        return loader.discover(str(TESTS_DIR), top_level_dir=str(REPO_ROOT))
    suite = loader.discover(str(TESTS_DIR / "qa"), top_level_dir=str(REPO_ROOT))
    suite.addTests(loader.loadTestsFromNames(QA_ONLY_EXTRA_MODULES))
    return suite


def qa_outcomes(
    records: dict[str, TestRecord],
    criterion_tests: dict[str, list[str]],
    *,
    full_suite: bool,
) -> dict[str, dict[str, Any]]:
    """Automated QA ID -> {result, tests, failed, skipped, notes}.

    N16: ``criterion_tests`` maps each automated QA ID to its tests (every
    test whose docstring first line is the QA ID's criterion). The result
    folds them all with every other test tagged with the QA ID.
    """
    outcomes: dict[str, dict[str, Any]] = {}
    suite_failures = sorted(test_id for test_id, record in records.items() if not record.ok)
    for qa_id, test_ids in criterion_tests.items():
        tagged = sorted(
            {record.test_id: record for record in records.values() if record.qa_id == qa_id or record.test_id in test_ids}.values(),
            key=lambda r: r.test_id,
        )
        failed = [record.test_id for record in tagged if not record.ok]
        skipped = [record.test_id for record in tagged if record.status == "skipped"]
        ran = [record for record in tagged if record.status != "skipped"]
        notes: list[str] = []
        if not test_ids:
            result = FAIL
            notes.append("통과 기준 원문을 docstring 첫 줄로 가진 테스트가 없음")
        elif not tagged:
            result = FAIL
            notes.append("테스트가 실행되지 않음")
        elif failed:
            result = FAIL
        elif skipped:
            # R10-3: one skipped test is enough — "통과" only when every test ran and passed.
            result = SKIPPED
            if not ran:
                notes.append("모든 테스트 건너뜀")
        else:
            result = PASS
        if skipped:
            notes.append("건너뛴 관련 테스트: " + ", ".join(
                f"{test_id.rsplit('.', 1)[-1]} ({records[test_id].detail})" for test_id in skipped))
        if qa_id == FULL_SUITE_QA_ID:
            if not full_suite:
                result = SKIPPED if result == PASS else result
                notes.append("전체 스위트 미실행(--qa-only)")
            elif suite_failures:
                result = FAIL
                notes.append(f"전체 스위트 실패 {len(suite_failures)}건")
            else:
                notes.append(f"전체 스위트 {len(records)}개 실행, 실패 0건")
        outcomes[qa_id] = {
            "result": result,
            "tests": [record.test_id for record in tagged],
            "failed": failed,
            "skipped": skipped,
            "notes": notes,
        }
    return outcomes


def write_logs(log_dir: Path, records: dict[str, TestRecord], outcomes: dict[str, dict[str, Any]], env: dict[str, Any]) -> dict[str, Path]:
    log_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for qa_id, outcome in outcomes.items():
        path = log_dir / f"{qa_id}.log"
        lines = [f"{qa_id}: {outcome['result']}", f"테스트 {len(outcome['tests'])}개", *outcome["notes"], ""]
        for test_id in outcome["tests"]:
            record = records[test_id]
            lines.append(f"=== {test_id} [{record.status}] {record.seconds:.2f}s")
            if record.detail:
                lines.append(record.detail.rstrip())
            if record.output.strip():
                lines.append("--- 출력")
                lines.append(record.output.rstrip())
            lines.append("")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        paths[qa_id] = path
    full = [f"{record.status:7s} {record.seconds:7.2f}s {record.test_id}" + (f"  [{record.qa_id}]" if record.qa_id else "") for record in sorted(records.values(), key=lambda r: r.test_id)]
    failures = [record for record in records.values() if not record.ok]
    full.append("")
    full.append(f"합계 {len(records)}개, 실패 {len(failures)}개, 건너뜀 {sum(1 for r in records.values() if r.status == 'skipped')}개")
    for record in failures:
        full.extend(["", f"=== {record.test_id} [{record.status}]", record.detail.rstrip()])
    (log_dir / "full-suite.log").write_text("\n".join(full) + "\n", encoding="utf-8")
    (log_dir / "results.json").write_text(json.dumps({
        "environment": env,
        "qa": outcomes,
        "tests": {test_id: {"qa_id": record.qa_id, "status": record.status, "seconds": round(record.seconds, 3)} for test_id, record in sorted(records.items())},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return paths


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def conformance_rows(data: dict[str, Any], outcomes: dict[str, dict[str, Any]], log_paths: dict[str, Path]) -> list[tuple[str, str, str, str, str]]:
    rows: list[tuple[str, str, str, str, str]] = []
    qa = data["qa"]

    def cell(qa_id: str, note: str = "") -> tuple[str, str, str]:
        entry = qa[qa_id]
        # A QA-level label (e.g. QA-OUT-5 "구조 검사(0단계에 보정 모델 없음)")
        # qualifies what "통과" means on every row that cites the QA ID.
        notes = [text for text in (note, entry.get("label", "")) if text]
        label = f"{qa_id} ({'; '.join(notes)})" if notes else qa_id
        if entry["mode"] == "automated":
            return label, outcomes[qa_id]["result"], f"`{_rel(log_paths[qa_id])}`"
        if entry["mode"] == "manual":
            return label, MANUAL, f"`{entry['checklist']}`"
        return label, PHASE1, "—"

    for row in data["requirements"]:
        gaps = ", ".join(row["gaps"]) or "—"
        if not row["qa"]:
            rows.append((row["id"], gaps, "—", PHASE1, f"— ({row.get('phase1', '1단계')})"))
            continue
        for qa_id in row["qa"]:
            rows.append((row["id"], gaps, *cell(qa_id, row.get("notes", {}).get(qa_id, ""))))
    for qa_id in data["out_of_scope"]["ids"]:
        rows.append(("—", "—", qa_id, PHASE1, "— (0단계 범위 밖)"))
    return rows


def summary_counts(data: dict[str, Any], outcomes: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Counted per distinct QA ID; a requirement with no QA ID counts once."""
    counts = {PASS: 0, FAIL: 0, MANUAL: 0, PHASE1: 0, SKIPPED: 0}
    for qa_id, entry in data["qa"].items():
        if entry["mode"] == "automated":
            counts[outcomes[qa_id]["result"]] += 1
        elif entry["mode"] == "manual":
            counts[MANUAL] += 1
        else:
            counts[PHASE1] += 1
    counts[PHASE1] += sum(1 for row in data["requirements"] if not row["qa"])
    counts[PHASE1] += len(data["out_of_scope"]["ids"])
    return counts


def summary_line(counts: dict[str, int]) -> str:
    line = f"{counts[PASS]} 통과 / {counts[FAIL]} 실패 / {counts[MANUAL]} 수동 / {counts[PHASE1]} 1단계"
    if counts[SKIPPED]:
        line += f" / {counts[SKIPPED]} {SKIPPED}"
    return line


def render(data: dict[str, Any], outcomes: dict[str, dict[str, Any]], log_paths: dict[str, Path], records: dict[str, TestRecord], env: dict[str, Any], *, full_suite: bool) -> str:
    counts = summary_counts(data, outcomes)
    failures = sum(1 for record in records.values() if not record.ok)
    skipped = sum(1 for record in records.values() if record.status == "skipped")
    lines = [
        "# 0단계 적합성 표 (CONFORMANCE)",
        "",
        "<!-- scripts/qa_phase0.py가 생성 — 손으로 고치지 말 것 -->",
        "",
        f"{COMMIT_LINE_PREFIX}{env['commit']}`"
        + (
            " (작업 트리에 커밋되지 않은 변경 있음 — --allow-dirty 실행, 기록으로 쓰지 말 것)"
            if env["dirty"]
            else " — 변경 없는 작업 트리에서 실행. 이 표는 이 커밋의 직계 자식 커밋에 단독으로 담긴다"
            " (`python scripts/qa_phase0.py --verify-record`로 확인)"
        ),
        f"- 생성 일시(UTC): {env['date']}",
        f"- 도구 버전: deepfake-lens {env['tool_version']}",
        f"- 환경: Python {env['python']} / {env['platform']} / ffmpeg {'있음' if env['ffmpeg'] else '없음'}",
        f"- 설치된 선택 패키지: {', '.join(env['extras_present']) or '없음'}",
        f"- 없는 선택 패키지(해당 테스트는 건너뜀): {', '.join(env['extras_missing']) or '없음'}",
        f"- 실행 범위: {'전체 단위 테스트 스위트' if full_suite else 'tests/qa + 실패 차단·결정 규칙 테스트(--qa-only)'} — {len(records)}개 실행, 실패 {failures}개, 건너뜀 {skipped}개",
        "- 신경망 가중치: 없음(모든 프로필 supported:false, 모델 경로는 가짜 프로필 + monkeypatch로 검증)",
        "",
        f"**요약: {summary_line(counts)}**",
        "",
        "집계 단위: 고유 QA ID 1건씩. QA ID가 없는 1단계 요구사항(R-IMG-1, R-VID-*, R-AUD-*, R-DOC-*)과 "
        "0단계 범위 밖 ID(QA-MOD-*, QA-ADV-4–6)는 각각 1건으로 1단계에 센다. 아래 표는 요구사항×QA ID 쌍마다 한 행이라 "
        "같은 QA ID가 여러 행에 나올 수 있다.",
        "",
        "## 요구사항 → 갭 → QA",
        "",
        "| 요구사항 ID | 갭 ID | QA ID | 결과 | 로그 경로 |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines.extend(f"| {req} | {gaps} | {qa_id} | {result} | {log} |" for req, gaps, qa_id, result, log in conformance_rows(data, outcomes, log_paths))
    lines.extend([
        "",
        "## 자동 QA 상세",
        "",
        "QA 테스트마다 docstring 첫 줄이 \"<QA ID>: <통과 기준 원문>\"이고 둘째 줄에 그 테스트가 검사하는 내용을 적는다(N16). "
        "QA ID의 결과는 그 QA ID의 모든 테스트 결과를 합친 것이다(실패 하나면 실패, 건너뛴 테스트가 하나라도 있으면 "
        "건너뜀(환경), 모든 테스트가 실행되어 통과했을 때만 통과).",
        "",
        "| QA ID | 테스트(실패/건너뜀/전체) | 결과 | 비고 |",
        "| --- | --- | --- | --- |",
    ])
    for qa_id, outcome in outcomes.items():
        lines.append(
            f"| {qa_id} | {len(outcome['failed'])}/{len(outcome['skipped'])}/{len(outcome['tests'])} "
            f"| {outcome['result']} | {'; '.join(outcome['notes']) or '—'} |"
        )
    lines.extend(["", "## 수동·1단계", ""])
    for qa_id, entry in data["qa"].items():
        if entry["mode"] == "manual":
            lines.append(f"- {qa_id} ({entry['title']}): 수동 — 체크리스트 `{entry['checklist']}`")
    for qa_id, entry in data["qa"].items():
        if entry["mode"] == "phase1":
            lines.append(f"- {qa_id} ({entry['title']}): 1단계")
    lines.append(f"- {', '.join(data['out_of_scope']['ids'])}: 1단계 — {data['out_of_scope']['note']}")
    for gap, note in data.get("gaps_without_qa", {}).items():
        lines.append(f"- {gap}: {note}")
    lines.append("")
    return "\n".join(lines)


def recorded_commit(table: str) -> str | None:
    for line in table.splitlines():
        if line.startswith(COMMIT_LINE_PREFIX):
            return line[len(COMMIT_LINE_PREFIX):].split("`", 1)[0] or None
    return None


def verify_record(out: Path) -> int:
    """The committed table must come from a clean run on HEAD's parent (or
    HEAD itself, before it is committed) with no other change since (D16)."""
    try:
        table = out.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[qa] 기록 없음: {out}: {exc}", file=sys.stderr)
        return 1
    commit = recorded_commit(table)
    if not commit or "커밋되지 않은 변경 있음" in table:
        print(f"[qa] 기록 불가: 검증 커밋이 없거나 변경 있는 작업 트리에서 생성됨 ({out})", file=sys.stderr)
        return 1
    head = _git("rev-parse", "HEAD")
    if not head:
        print("[qa] git 없음 — 기록을 확인할 수 없습니다.", file=sys.stderr)
        return 1
    if commit != head and _git("rev-parse", "HEAD^") != commit:
        print(f"[qa] 기록 커밋 {commit[:12]}이 HEAD({head[:12]})나 그 부모가 아닙니다 — 다시 생성하세요.", file=sys.stderr)
        return 1
    changed = [name for name in _git("diff", "--name-only", commit).splitlines() if name]
    relative = _rel(out)
    # The commit-traceability table (R9-10) is another generated record that is
    # legitimately regenerated in the same final commit as this record.
    co_generated = {relative, "docs/traceability-commits.json", "docs/TRACEABILITY-COMMITS.md"}
    others = [name for name in changed if name not in co_generated]
    if others:
        print(f"[qa] 기록 이후 {len(others)}개 파일이 바뀌었습니다(예: {', '.join(others[:5])}) — 다시 생성하세요.", file=sys.stderr)
        return 1
    print(f"[qa] 기록 확인: {relative}는 {commit[:12]}에서 생성되었고 그 뒤 다른 변경이 없습니다.")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help
    parser = KoreanArgumentParser(description="0단계 QA 하네스: QA 테스트를 실행하고 docs/CONFORMANCE.md 적합성 표를 만듭니다.")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR, help="QA별 로그, full-suite.log, results.json을 쓸 폴더(기본: build/qa-logs)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="적합성 표 파일(기본: docs/CONFORMANCE.md)")
    parser.add_argument("--qa-only", action="store_true", help="tests/qa와 fail-closed·결정 규칙 테스트만 실행(QA-SYS-10은 건너뜀)")
    parser.add_argument("--allow-dirty", action="store_true", help="커밋되지 않은 변경이 있는 작업 트리에서도 실행(기록에 그 사실을 남김)")
    parser.add_argument("--verify-record", action="store_true", help="--out 파일이 해당 커밋의 부모에서 만들어졌고 그 뒤 다른 변경이 없는지 확인")
    args = parser.parse_args(argv)

    if args.verify_record:
        return verify_record(args.out)
    missing = [name for name in REQUIRED_FOR_RECORD if importlib.util.find_spec(name) is None]
    if missing:
        print(f"[qa] fastapi 필요: {', '.join(missing)} 없음 — QA-OUT-4의 API 서버 레그를 건너뛴 기록은 통과로 쓸 수 없습니다.", file=sys.stderr)
        print(f"[qa] 사이드 venv: {SIDE_VENV_HINT}", file=sys.stderr)
        return 1
    if _git("rev-parse", "HEAD") and _git("status", "--porcelain", "--untracked-files=no") and not args.allow_dirty:
        print("[qa] 작업 트리에 커밋되지 않은 변경이 있습니다 — 커밋한 뒤 실행하거나 --allow-dirty(기록으로 쓰지 말 것)를 주세요.", file=sys.stderr)
        return 1

    data = load_traceability()
    criteria = automated_criteria(data)
    suite = load_suite(args.qa_only)
    tests = list(iter_tests(suite))
    criterion_tests = tests_by_qa_id([test for test in tests if hasattr(test, "_testMethodName")], criteria)

    env = environment()
    print(f"[qa] {len(tests)}개 테스트 실행 중 (commit {env['commit'][:12]}) …", flush=True)
    result = RecordingResult()
    started = time.perf_counter()
    suite.run(result)
    elapsed = time.perf_counter() - started

    records = result.records
    outcomes = qa_outcomes(records, criterion_tests, full_suite=not args.qa_only)
    log_paths = write_logs(args.log_dir, records, outcomes, env)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(data, outcomes, log_paths, records, env, full_suite=not args.qa_only), encoding="utf-8")

    failures = [record for record in records.values() if not record.ok]
    untested = [qa_id for qa_id, ids in criterion_tests.items() if not ids]
    counts = summary_counts(data, outcomes)
    print(f"[qa] {elapsed:.1f}s, 테스트 {len(records)}개, 실패 {len(failures)}개")
    for record in failures:
        print(f"[qa] 실패: {record.test_id}")
    for qa_id in untested:
        print(f"[qa] 통과 기준 원문을 첫 줄로 가진 테스트 없음: {qa_id}")
    print(f"[qa] {summary_line(counts)}")
    print(f"[qa] 적합성 표: {_rel(args.out)}, 로그: {_rel(args.log_dir)}")
    return 1 if failures or untested or counts[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
