#!/usr/bin/env python3
"""Regenerate the commit traceability table from the current git history (P12).

``docs/traceability-commits.json`` holds two kinds of data:

- curated data, edited by hand and only carried over by this script:
  ``description``, ``notes``, ``wp_gaps`` (spec WP -> gap IDs), ``ids[]``
  (verification finding ID -> round, source, summary, spec gaps G1–G34 or a
  "신규(스펙 외)" reason) and ``unused_ids`` (IDs no commit subject uses, with
  the reason);
- derived data, rebuilt here from ``git log <base>..<head>`` (merges
  excluded): ``commits[]`` (sha, subject, IDs, the parsed ``Gaps:`` line),
  ``ids[].commits`` / ``ids[].used_in_commit_subjects`` and ``range``.

Commits are matched to the previous table **by subject** (also by the
subject before the history rewrite, or with its trailing "(…; Gaps: …)"
removed), so the curated per-commit fields (round, IDs, 신규 reasons) of a
rewritten commit carry over to its new hash. A commit with no previous entry
gets its IDs from the parenthesis of its subject ("(P1, P6; Gaps: G30)",
ranges such as "Z1-Z5" expanded) and its round from ``ids[]``.

The ``Gaps:`` line is parsed from the body when it has one, else from the
subject (P14: "(Z1-Z5; Gaps: G7, 신규)" in the subject is valid).

The table covers ``range.base`` (merge base with ``main``) up to
``range.head`` = the parent of the commit that regenerated it: a commit
cannot list its own hash. ``--check`` (CI) rebuilds the table for exactly
that range and fails when the committed JSON/Markdown differ, when
``range.head`` is not the parent of the last commit that changed the JSON
(or HEAD itself, for an uncommitted regeneration), or when the history the
table was built from is gone (rewritten without regenerating).

R9-10 (round 9): it also fails when more than one commit follows
``range.head`` — the only commit allowed after the table's range is the
one regenerating it — or when that commit changes anything besides
``docs/traceability-commits.json`` and ``docs/TRACEABILITY-COMMITS.md``. A
commit made after the table therefore needs a regeneration (as the last
commit) before ``--check`` passes again.

Usage:
    python scripts/build_traceability_commits.py            # regenerate for <merge-base main>..HEAD
    python scripts/build_traceability_commits.py --check    # exit 1 if stale (CI; needs full history)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = REPO_ROOT / "docs" / "traceability-commits.json"
MD_PATH = REPO_ROOT / "docs" / "TRACEABILITY-COMMITS.md"
JSON_REL = "docs/traceability-commits.json"
MD_REL = "docs/TRACEABILITY-COMMITS.md"
SCHEMA = "deepfake-lens-traceability-commits-v2"
NEW_LABEL = "신규(스펙 외)"

GAP_TOKEN = re.compile(r"(?<![\w-])G([1-9]\d?)(?![\w-])")
GAPS_LINE = re.compile(r"^Gaps:\s*(.+?)\s*$", re.MULTILINE)
SUBJECT_GAPS = re.compile(r"Gaps:\s*([^)]*)")
TRAILING_PAREN = re.compile(r"\s*\(([^()]*)\)\s*$")
ID_TOKEN = re.compile(r"^(?:WP-[A-J]|QA-[A-Z]+-\d+|N[36]-\d+|V5-G\d+|R\d+-\d+|[A-Z]\d+)$")
# R9-10: round-9 finding IDs ("R9-1" … "R9-10") are IDs, never a range ("R9-10"
# is not R9..R10 — the round-2 IDs R9 and R10).
ROUND_ID = re.compile(r"^R\d+-\d+$")
ID_RANGE = re.compile(r"^([A-Z])(\d+)-(?:\1)?(\d+)$")


class TraceabilityError(Exception):
    """A problem the operator must fix (message is Korean)."""


# ----------------------------------------------------------------------------
# git


def _git(*args: str) -> str:
    done = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:
        raise TraceabilityError(f"git {' '.join(args)} 실패(종료 코드 {done.returncode}): {done.stderr.strip()[:300]}")
    return done.stdout


def _rev(ref: str) -> str:
    return _git("rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def _exists(sha: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=REPO_ROOT, capture_output=True).returncode == 0


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=REPO_ROOT, capture_output=True).returncode == 0


def git_commits(base: str, head: str) -> list[dict[str, str]]:
    """Non-merge commits of ``base..head``, oldest first: sha, subject, body."""
    raw = _git("log", "--reverse", "--no-merges", "--format=%H%x1f%s%x1f%b%x1e", f"{base}..{head}")
    out = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, subject, body = (record.split("\x1f") + ["", ""])[:3]
        out.append({"sha": sha.strip(), "subject": subject.strip(), "body": body})
    return out


def default_base(ref: str | None) -> str:
    """Merge base of HEAD with ``ref`` (default ``main``, else ``origin/main``)."""
    candidates = [ref] if ref else ["main", "origin/main"]
    for candidate in candidates:
        try:
            return _git("merge-base", candidate, "HEAD").strip()
        except TraceabilityError:
            continue
    raise TraceabilityError(f"기준 브랜치를 찾을 수 없습니다: {', '.join(candidates)} (--base로 지정하십시오)")


# ----------------------------------------------------------------------------
# parsing


def parse_gaps(subject: str, body: str) -> dict[str, Any]:
    """The commit's ``Gaps:`` line — body first, else the subject (P14)."""
    match = GAPS_LINE.search(body or "")
    source = "body"
    text = match.group(1) if match else None
    if text is None:
        sub = SUBJECT_GAPS.search(subject)
        if sub is None:
            return {"gaps": [], "new": False, "gaps_line": None, "gaps_source": None}
        text, source = sub.group(1), "subject"
    gaps = [f"G{number}" for number in GAP_TOKEN.findall(text)]
    gaps = list(dict.fromkeys(gaps))
    new = "신규" in text
    return {"gaps": gaps, "new": new, "gaps_line": format_gaps_line(gaps, new), "gaps_source": source}


def format_gaps_line(gaps: list[str], new: bool) -> str:
    parts = [", ".join(gaps)] if gaps else []
    if new:
        parts.append(NEW_LABEL)
    return "Gaps: " + ("; ".join(parts) if parts else "—")


def subject_without_gaps(subject: str) -> str:
    """``subject`` minus a trailing "(…Gaps: …)" parenthesis (the rewrite's suffix)."""
    match = TRAILING_PAREN.search(subject)
    if match and "Gaps:" in match.group(1):
        return subject[: match.start()].rstrip()
    return subject


def subject_ids(subject: str) -> list[str]:
    """IDs named in the subject's trailing parenthesis ("(P1, P6; Gaps: …)", "(Z1-Z5; …)", "(WP-J)")."""
    match = TRAILING_PAREN.search(subject)
    if not match:
        return []
    ids: list[str] = []
    for part in match.group(1).split(";"):
        part = part.strip()
        if not part or part.startswith("Gaps:"):
            continue
        for token in re.split(r"[,\s]+", part):
            token = token.strip().rstrip(":")
            if ROUND_ID.match(token):
                ids.append(token)
                continue
            span = ID_RANGE.match(token)
            if span and int(span.group(2)) < int(span.group(3)):
                ids.extend(f"{span.group(1)}{n}" for n in range(int(span.group(2)), int(span.group(3)) + 1))
            elif ID_TOKEN.match(token) and not GAP_TOKEN.fullmatch(token):
                ids.append(token)
    return list(dict.fromkeys(ids))


def gaps_named_in_subject(subject: str) -> list[str]:
    return list(dict.fromkeys(f"G{number}" for number in GAP_TOKEN.findall(subject)))


# ----------------------------------------------------------------------------
# build


def _previous_index(previous: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for entry in previous:
        for key in (entry.get("subject"), entry.get("pre_rewrite_subject"), entry.get("relabeled_subject")):
            if isinstance(key, str) and key:
                index.setdefault(key, entry)
                index.setdefault(subject_without_gaps(key), entry)
    return index


def build_commits(log: list[dict[str, str]], data: dict[str, Any]) -> list[dict[str, Any]]:
    index = _previous_index(data.get("commits") or [])
    rounds = {entry["id"]: entry.get("round") for entry in data["ids"]}
    reasons = {entry["id"]: entry.get("new") for entry in data["ids"]}
    out: list[dict[str, Any]] = []
    last_round: str | None = None
    for commit in log:
        subject = commit["subject"]
        previous = index.get(subject) or index.get(subject_without_gaps(subject))
        gaps = parse_gaps(subject, commit["body"])
        if previous is not None:
            ids = list(previous.get("ids") or [])
            round_ = previous.get("round")
            new_reasons = list(previous.get("new_reasons") or [])
            pre = previous.get("pre_rewrite_subject") or (previous.get("subject") if previous.get("subject") != subject else None)
        else:
            ids = subject_ids(subject)
            known = [rounds[i] for i in ids if rounds.get(i) is not None]
            round_ = str(known[0]) if known else ("wp" if ids and all(i.startswith("WP-") for i in ids) and last_round in (None, "wp") else last_round)
            new_reasons = [f"{i}: {reasons[i]}" for i in ids if reasons.get(i)]
            pre = None
        last_round = round_
        out.append({
            "sha": commit["sha"],
            "short": commit["sha"][:7],
            "round": round_,
            "subject": subject,
            "pre_rewrite_subject": pre,
            "ids": ids,
            "gaps_named_in_subject": gaps_named_in_subject(subject),
            "gaps": gaps["gaps"],
            "new": gaps["new"],
            "new_reasons": new_reasons,
            "gaps_line": gaps["gaps_line"],
            "gaps_source": gaps["gaps_source"],
        })
    return out


def build(data: dict[str, Any], base: str, head: str) -> dict[str, Any]:
    """The full table for ``base..head`` with ``data``'s curated parts."""
    commits = build_commits(git_commits(base, head), data)
    by_id: dict[str, list[str]] = {}
    for commit in commits:
        for id_ in commit["ids"]:
            by_id.setdefault(id_, []).append(commit["short"])
    ids = []
    for entry in data["ids"]:
        entry = {key: entry.get(key) for key in ("id", "round", "source", "summary", "gaps", "new")}
        entry["commits"] = by_id.get(entry["id"], [])
        entry["used_in_commit_subjects"] = bool(entry["commits"])
        ids.append(entry)
    unused = {key: value for key, value in (data.get("unused_ids") or {}).items() if key not in by_id}
    return {
        "schema": SCHEMA,
        "description": data["description"],
        "generated_by": "scripts/build_traceability_commits.py",
        "range": {
            "base": base,
            "head": head,
            "note": "range.base..range.head(병합 커밋 제외). range.head는 이 표를 재생성한 커밋의 부모 — 그 커밋 자신의 해시는 표에 없다.",
        },
        "notes": list(data.get("notes") or []),
        "wp_gaps": data["wp_gaps"],
        "ids": ids,
        "unused_ids": unused,
        "commits": commits,
    }


# ----------------------------------------------------------------------------
# markdown


def _cell(text: object) -> str:
    return str(text if text not in (None, "") else "").replace("|", "\\|").replace("\n", " ")


def render_markdown(table: dict[str, Any]) -> str:
    ids = table["ids"]
    mapped = [entry for entry in ids if entry["gaps"]]
    both = [entry for entry in mapped if entry["new"]]
    new_only = [entry for entry in ids if not entry["gaps"]]
    commits = table["commits"]
    lines = [
        "# 검증 ID → 스펙 갭 ID 추적표 (Y13, P12)",
        "",
        "<!-- 생성 파일: scripts/build_traceability_commits.py가 docs/traceability-commits.json과 git 히스토리에서 만든다. 손으로 고치지 말 것 — 매핑은 JSON의 ids[]를 고친 뒤 스크립트를 다시 실행한다. -->",
        "",
        table["description"],
        "",
        f"범위: `{table['range']['base'][:7]}..{table['range']['head'][:7]}`(병합 커밋 제외, 커밋 {len(commits)}개). "
        "이 표는 **표를 재생성한 커밋의 부모까지**를 덮는다 — 재생성 커밋 자신의 해시는 표에 없다(자기 해시를 담을 수 없음). "
        "CI(`python scripts/build_traceability_commits.py --check`)가 같은 범위를 히스토리에서 다시 만들어 커밋된 표와 비교한다.",
        "",
        f"요약: ID {len(ids)}개 — 스펙 갭에 매핑 {len(mapped)}개(그중 신규 사유 병기 {len(both)}개), {NEW_LABEL}만 {len(new_only)}개.",
        "",
        "## WP → 갭 (스펙 머리글)",
        "",
        "| WP | 갭 |",
        "| --- | --- |",
    ]
    for wp, gaps in table["wp_gaps"].items():
        lines.append(f"| {wp} | {', '.join(gaps) if gaps else '— (QA 하네스·적합성 표)'} |")
    lines += [
        "",
        "## 검증 ID → 갭",
        "",
        "| 검증 ID | 라운드 | 내용 | 스펙 갭 | 신규(스펙 외) | 커밋 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry in ids:
        lines.append(
            f"| {entry['id']} | {_cell(entry['round'])} | {_cell(entry['summary'])} | {', '.join(entry['gaps']) or '—'} | "
            f"{_cell(f'{NEW_LABEL}: ' + entry['new'] if entry['new'] else '')} | {', '.join(entry['commits']) or '—'} |"
        )
    lines.append("")
    if table["unused_ids"]:
        lines.append("커밋 제목에 쓰이지 않은 ID:")
        lines.append("")
        for key, reason in table["unused_ids"].items():
            lines.append(f"- {key}: {reason}")
        lines.append("")
    for note in table["notes"]:
        lines.append(note)
        lines.append("")
    lines += [
        "## 커밋별 `Gaps:` 줄",
        "",
        "`Gaps 출처`: 본문의 `Gaps:` 줄(body) 또는 제목 괄호 안의 `Gaps:`(subject, P14 — 규칙상 허용).",
        "",
        "| 커밋 | 라운드 | 제목의 ID | Gaps 줄 | Gaps 출처 | 제목 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for commit in commits:
        lines.append(
            f"| {commit['short']} | {_cell(commit['round'])} | {', '.join(commit['ids']) or '—'} | {_cell(commit['gaps_line'] or '없음')} | "
            f"{_cell(commit['gaps_source'] or '—')} | {_cell(commit['subject'])} |"
        )
    return "\n".join(lines) + "\n"


def dump_json(table: dict[str, Any]) -> str:
    return json.dumps(table, ensure_ascii=False, indent=2) + "\n"


# ----------------------------------------------------------------------------
# check


def _last_json_commit() -> str | None:
    sha = _git("log", "-1", "--format=%H", "--", JSON_REL).strip()
    return sha or None


def _json_dirty() -> bool:
    return bool(_git("status", "--porcelain", "--", JSON_REL).strip())


TABLE_FILES = frozenset({JSON_REL, MD_REL})
# The conformance record (docs/CONFORMANCE.md) is another generated record that is
# legitimately regenerated in the same final commit (WP-J).
GENERATED_RECORD_FILES = TABLE_FILES | {"docs/CONFORMANCE.md"}


def _later_commit_problems(head: str) -> list[str]:
    """R9-10: after ``range.head`` only the regenerating commit, touching only the two table files."""
    if _json_dirty():
        return []  # an uncommitted regeneration: range.head must be HEAD (checked above)
    later = _git("rev-list", f"{head}..HEAD").split()
    if len(later) > 1:
        return [
            f"표 이후 커밋이 {len(later)}개입니다({head[:7]}..HEAD) — 표의 범위 뒤에는 표를 재생성한 커밋 1개만 올 수 있습니다. "
            "python scripts/build_traceability_commits.py 로 재생성해 마지막 커밋으로 넣으십시오"
        ]
    if later:
        changed = set(_git("diff-tree", "--no-commit-id", "--name-only", "-r", "-m", later[0]).split())
        extra = sorted(changed - GENERATED_RECORD_FILES)
        if extra:
            return [
                f"표를 재생성한 커밋 {later[0][:7]}이(가) 추적표 파일 밖의 파일도 바꿨습니다: {', '.join(extra[:10])}"
                f"{' 외' if len(extra) > 10 else ''} — 재생성 커밋에는 {JSON_REL}과 {MD_REL}만 넣으십시오"
            ]
    return []


def check(committed_json: str, committed_md: str) -> list[str]:
    """Problems with the committed table (empty list = current)."""
    try:
        data = json.loads(committed_json)
    except json.JSONDecodeError as exc:
        return [f"{JSON_REL}을(를) JSON으로 해석할 수 없습니다(줄 {exc.lineno}, 열 {exc.colno})"]
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return [f"{JSON_REL}의 schema가 {SCHEMA}이(가) 아닙니다 — 스크립트로 재생성하십시오"]
    base, head = data["range"]["base"], data["range"]["head"]
    problems = []
    for name, sha in (("range.base", base), ("range.head", head)):
        if not _exists(sha):
            problems.append(f"{name} {sha[:12]}이(가) 히스토리에 없습니다(히스토리 재작성 후 재생성하지 않았거나 얕은 클론 — fetch-depth: 0 필요)")
    if problems:
        return problems
    if not _is_ancestor(head, "HEAD"):
        problems.append(f"range.head {head[:7]}이(가) HEAD의 조상이 아닙니다")
    expected_parent = _rev("HEAD") if _json_dirty() else None
    if expected_parent is None:
        last = _last_json_commit()
        if last is not None:
            parents = _git("rev-list", "--parents", "-n", "1", last).split()[1:]
            expected_parent = parents[0] if parents else None
    if expected_parent is not None and head != expected_parent:
        problems.append(
            f"range.head {head[:7]}이(가) 표를 마지막으로 재생성한 커밋의 부모({expected_parent[:7]})가 아닙니다 — "
            "표는 재생성 커밋의 부모까지를 덮어야 합니다"
        )
    problems += _later_commit_problems(head)
    expected = build(data, base, head)
    if dump_json(expected) != committed_json:
        problems.append(f"{JSON_REL}이(가) 히스토리에서 다시 만든 표와 다릅니다 — python scripts/build_traceability_commits.py 를 실행하십시오")
    if render_markdown(expected) != committed_md:
        problems.append("docs/TRACEABILITY-COMMITS.md가 JSON에서 다시 만든 표와 다릅니다 — python scripts/build_traceability_commits.py 를 실행하십시오")
    return problems


def main(argv: list[str] | None = None) -> int:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help

    parser = KoreanArgumentParser(description="현재 git 히스토리(<기준>..HEAD)에서 커밋 추적표(docs/traceability-commits.json, docs/TRACEABILITY-COMMITS.md)를 다시 만듭니다(P12).")
    parser.add_argument("--check", action="store_true", help="커밋된 표가 그 범위의 히스토리에서 다시 만든 표와 다르면 종료 코드 1(CI)")
    parser.add_argument("--base", help="기준 브랜치(기본: main, 없으면 origin/main) — 범위의 시작은 HEAD와의 병합 기준점")
    args = parser.parse_args(argv)
    try:
        committed_json = JSON_PATH.read_text(encoding="utf-8")
        committed_md = MD_PATH.read_text(encoding="utf-8")
        if args.check:
            problems = check(committed_json, committed_md)
            for problem in problems:
                print(f"오류: {problem}", file=sys.stderr)
            if problems:
                return 1
            data = json.loads(committed_json)
            later = _git("rev-list", "--count", f"{data['range']['head']}..HEAD").strip()
            print(f"추적표 최신: 커밋 {len(data['commits'])}개({data['range']['base'][:7]}..{data['range']['head'][:7]}), 표 이후 커밋 {later}개(재생성 커밋만)")
            return 0
        data = json.loads(committed_json)
        table = build(data, default_base(args.base), _rev("HEAD"))
        JSON_PATH.write_text(dump_json(table), encoding="utf-8")
        MD_PATH.write_text(render_markdown(table), encoding="utf-8")
        print(f"추적표 재생성: 커밋 {len(table['commits'])}개, ID {len(table['ids'])}개 ({table['range']['base'][:7]}..{table['range']['head'][:7]})")
        return 0
    except (TraceabilityError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"오류: 추적표를 만들 수 없습니다: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
