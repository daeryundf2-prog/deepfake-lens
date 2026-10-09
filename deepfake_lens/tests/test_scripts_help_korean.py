"""G13 (round 5): ``scripts/*.py --help`` is Korean.

The verifier found the scripts' help in English (qa_phase0,
check_measurement_gate, sync_model_docs, make_adversarial_fixtures,
make_qa_manual_fixtures and the legacy fetch/build/run scripts). Every
script now builds its parser with ``cli_parser.KoreanArgumentParser`` —
argparse's own words ("사용법:", "옵션", "이 도움말을 보여 주고 끝냄") are
Korean — and every description and argument help is Korean; flag names and
metavars stay as they are. Scripts that took no options (check_registry_links,
cli_smoke_test, verify_contracts) answer ``--help`` too, and run_aide answers
it without torch installed.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from deepfake_lens.error_text import english_prose

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted((REPO_ROOT / "scripts").glob("*.py"))


def _help(script: Path) -> tuple[Path, int, str]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, (str(REPO_ROOT), os.environ.get("PYTHONPATH"))))}
    done = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, env=env, timeout=300, cwd=str(REPO_ROOT))
    return script, done.returncode, (done.stdout + done.stderr).decode("utf-8", "replace")


def help_prose_lines(text: str) -> list[str]:
    """The description and help lines of a rendered --help, flags and metavars removed."""
    lines = text.splitlines()
    body: list[str] = []
    in_usage = True
    for line in lines:
        if in_usage:
            if not line.strip():
                in_usage = False
            continue
        body.append(line)
    usage = "\n".join(lines[: len(lines) - len(body)])
    metavars = set(re.findall(r"\b[A-Z][A-Z0-9_]+\b", usage))
    out = []
    for line in body:
        line = re.sub(r"(?<![\w-])-{1,2}[A-Za-z][\w-]*", " ", line)
        line = re.sub(r"\{[^}]*\}", " ", line)
        line = " ".join(token for token in re.split(r"\s+", line) if token and token.strip(",") not in metavars)
        if line.strip():
            out.append(line.strip())
    return out


class ScriptsHelpIsKoreanTest(unittest.TestCase):
    def test_every_script_answers_help_in_korean(self) -> None:
        self.assertGreaterEqual(len(SCRIPTS), 27)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(_help, SCRIPTS))
        offenders: list[str] = []
        for script, code, text in results:
            name = script.name
            if code != 0:
                offenders.append(f"{name}: exit {code}: {text[-300:]!r}")
                continue
            if not text.startswith("사용법: "):
                offenders.append(f"{name}: no Korean usage line: {text[:80]!r}")
            if "이 도움말을 보여 주고 끝냄" not in text:
                offenders.append(f"{name}: argparse's -h help is not Korean")
            for english in ("usage:", "show this help message", "positional arguments", "options:", "optional arguments"):
                if english in text:
                    offenders.append(f"{name}: argparse English {english!r}")
            prose = help_prose_lines(text)
            if not any(re.search("[가-힣]", line) for line in prose):
                offenders.append(f"{name}: no Korean description")
            for line in prose:
                run = english_prose(line)
                if run:
                    offenders.append(f"{name}: {run!r} in {line[:120]!r}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_help_prose_lines_drops_flags_and_metavars(self) -> None:
        text = "사용법: x.py [-h] --input INPUT --output OUTPUT\n\n옵션:\n  --input INPUT    원본 파일\n  --output OUTPUT  출력 파일\n"
        self.assertEqual(help_prose_lines(text), ["옵션:", "원본 파일", "출력 파일"])
        self.assertIsNotNone(english_prose(" ".join(help_prose_lines("사용법: x\n\n  --a A  do not use this\n"))))


if __name__ == "__main__":
    unittest.main()
