"""Smoke-test every deepfake-lens subcommand against the bundled fixtures.

Runs without optional dependencies (numpy/opencv/librosa/fastapi) to verify
the stdlib-only degradation path. Exit code 0 means every registered
subcommand responds to --help and every functional check passes.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "fixtures" / "deepfake-lens-sample"
TIMEOUT_SECONDS = 120


def run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "deepfake_lens", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT_SECONDS,
        cwd=cwd,
        env=env,
    )


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    from deepfake_lens import cli

    commands = sorted(name for name in cli.COMMANDS if not name.startswith("-"))
    failures: list[str] = []

    for command in commands:
        proc = run_cli([command, "--help"], cwd=REPO_ROOT)
        if proc.returncode != 0:
            failures.append(f"{command} --help exited {proc.returncode}: {proc.stderr.strip()[:300]}")

    real_sample = FIXTURES / "train" / "real" / "camera" / "sample-real.txt"
    ai_sample = FIXTURES / "train" / "ai" / "flux" / "sample-ai.txt"
    checks: list[tuple[list[str], int]] = [
        (["scan", str(FIXTURES), "--recursive", "--format", "json"], 0),
        (["scan", str(FIXTURES), "--pixel", "deep", "--recursive", "--format", "json"], 0),
        (["collect", str(FIXTURES), "--out", "@OUT@/collect.json"], 0),
        (["dataset", str(FIXTURES), "--manifest-out", "@OUT@/manifest.json"], 0),
        (["eval", str(FIXTURES), "--pixel", "deep", "--max-files", "8"], 0),
        (["fusion", str(FIXTURES), "--pixel", "off", "--out", "@OUT@/fusion.json"], 0),
        (["perf", str(FIXTURES), "--out", "@OUT@/perf.json", "--max-files", "8"], 0),
        (["models"], 0),
        (["models", "--focus", "benchmark"], 0),
        (["release", "--out", "@OUT@/release.json"], 0),
        (["security", "--out", "@OUT@/security.json"], 0),
        (["batch", str(FIXTURES), "--output", "@OUT@/batch.json"], 0),
        (["evidence", str(real_sample), "--output", "@OUT@/evidence.json"], 0),
        (["classify", str(ai_sample)], 0),
        (["forensic", str(real_sample)], 0),
        (["legal-report", str(real_sample)], 0),
        (["text-advanced", str(ai_sample)], 0),
        (["explain", "--score", "70"], 0),
        (["realtime", "--scores", "10,20,30"], 0),
        (["multimodal", "--image-score", "50", "--text-score", "10"], 0),
        (["agent", "--text", "hello world"], 0),
        (["3d", "--text", "a small cube"], 0),
        (["avatar"], 0),
        (["video", str(FIXTURES), "--out", "@OUT@/video-plan.json", "--frame-root", "@OUT@/frames"], 0),
    ]

    with tempfile.TemporaryDirectory(prefix="deepfake-lens-smoke-") as tmp:
        out_dir = Path(tmp)
        for raw_args, expected in checks:
            args = [str(out_dir) if arg == "@OUT@" else arg for arg in raw_args]
            proc = run_cli(args, cwd=REPO_ROOT)
            if proc.returncode != expected:
                label = " ".join(Path(arg).name if arg.startswith("/") else arg for arg in args)
                failures.append(
                    f"{label} exited {proc.returncode} (expected {expected}): "
                    f"{proc.stderr.strip() or proc.stdout.strip()[:300]}"
                )

    if failures:
        print(f"SMOKE FAILED ({len(failures)}):", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(f"SMOKE OK: {len(commands)} subcommands --help, {len(checks)} functional checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
