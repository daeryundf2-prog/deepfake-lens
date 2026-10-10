"""R16-12 (round 16): one rule for the tests that look commits up in this checkout's history.

``test_qa_sys`` (each deleted test's commit explains the deletion) and
``test_shutdown`` (``KNOWN-HISTORICAL-ISSUES.md`` names real commits) find
commits by subject in HEAD's history:

- no ``git`` executable → skipped "git not available";
- not a git work tree (an exported tarball) → skipped "not a git work tree";
- a commit that is not in the history — a shallow clone cut before it, or a
  rewritten history → **fail**, with the same message in both tests
  (:func:`missing_commit_message`, naming a shallow clone when the checkout
  is one). The verifier's ``--depth 1`` clone failed both; ``test_shutdown``
  said only "'…' not found in []". A shallow clone deep enough to hold the
  cited commits passes (this repository's own checkout is shallow).

``docs/QA-ENV-DEPENDENT-TESTS.md`` documents the rule.
"""

from __future__ import annotations

import subprocess

SHALLOW_HINT = "얕은 복제(shallow clone)라 이력이 잘려 있습니다 — `git fetch --unshallow` 후 다시 실행하십시오"
REWRITTEN_HINT = "이력이 재작성되었거나 커밋 제목이 바뀌었습니다"


def is_shallow(git: str, repo: object) -> bool:
    """True when ``repo`` is a shallow clone (``git rev-parse --is-shallow-repository``)."""
    done = subprocess.run([git, "rev-parse", "--is-shallow-repository"], cwd=str(repo), capture_output=True, text=True, check=False)
    return done.returncode == 0 and done.stdout.strip() == "true"


def missing_commit_message(git: str, repo: object, subject: str) -> str:
    """The failure message of a cited commit that HEAD's history does not hold."""
    hint = SHALLOW_HINT if is_shallow(git, repo) else REWRITTEN_HINT
    return f"HEAD 이력에 제목이 {subject!r}인 커밋이 없습니다 — {hint} (docs/QA-ENV-DEPENDENT-TESTS.md)"
