#!/usr/bin/env python3
"""Shutdown stress test (R15-1, round 15): signal a real scan mid-flight, many times.

Each run scans a folder of Korean-named videos with an audio track
(``--workers 2``: every file is staged under an ASCII name and its audio is
pulled out by ``ffmpeg`` into a temp wav) and sends the signal as soon as a
session folder holds a ``.wav`` and — on Linux, where /proc shows it — an
``ffmpeg`` child of the scan runs. A run fails when, after the scan ended,

- (SIGTERM / SIGHUP) anything is left in its TMPDIR, at once or half a
  second later (an orphaned ``ffmpeg -y`` re-creating its output);
- any child of the scan is still running (all signals; for SIGKILL that is
  Linux PR_SET_PDEATHSIG); R16-7 (round 16): any *descendant* — every
  process under the scan when the signal was sent, grandchildren included
  (``--wrapped-ffmpeg`` puts an ``sh`` wrapper named ``ffmpeg`` first on
  PATH, so each real ``ffmpeg`` is a grandchild of the scan's child);
- (SIGKILL) more than the one session folder is left, or the next run's
  sweep does not remove it.

    python scripts/stress_shutdown.py [--runs 100] [--signal TERM|HUP|KILL] [--wrapped-ffmpeg]

The children start with SIGINT/SIGTERM/SIGHUP at their default action
(R15-4) whatever this script inherited. Exit 0 when every run passed, 1
when any failed, 2 when ffmpeg is missing. CI runs ``--runs 20``.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from deepfake_lens.native_path import SESSION_PREFIX, STALE_MIN_AGE_SECONDS, sweep_stale_sessions  # noqa: E402
from deepfake_lens.shutdown import with_default_signals  # noqa: E402

DEFAULT_RUNS = 100
# Videos in the scanned folder (enough decoding for the signal to land mid-scan).
DEFAULT_FILES = 8
RUN_TIMEOUT_SECONDS = 300
# How long nothing may re-appear after the scan ended.
STAYS_GONE_SECONDS = 0.5
# How long a child may take to disappear after the scan ended.
CHILD_GONE_SECONDS = 5.0


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            return handle.read().rsplit(b")", 1)[-1].split()[0] not in (b"Z", b"X")
    except OSError:
        return False


def _children(pid: int) -> list[int]:
    """Every live child of ``pid`` (all its threads; Linux /proc)."""
    found: list[int] = []
    try:
        tasks = os.listdir(f"/proc/{pid}/task")
    except OSError:
        return found
    for task in tasks:
        try:
            with open(f"/proc/{pid}/task/{task}/children", encoding="ascii") as handle:
                found.extend(int(text) for text in handle.read().split())
        except OSError:
            continue
    return [child for child in found if _alive(child)]


def _descendants(pid: int) -> list[int]:
    """R16-7: every live process under ``pid`` (children, grandchildren, …)."""
    found: list[int] = []
    stack = [pid]
    while stack:
        children = _children(stack.pop())
        found.extend(children)
        stack.extend(children)
    return found


# R16-7: a wrapper "ffmpeg" that runs the real one as its own child (no exec),
# reading the input in real time four times over (-re -stream_loop 3: ~12 s
# for the 3 s clip) so a grandchild that outlived the scan is still running
# when the run is checked.
WRAPPER_SCRIPT = '#!/bin/sh\n"$DFL_REAL_FFMPEG" -re -stream_loop 3 "$@"\n'


def _wrapper_dir(work: Path) -> Path:
    folder = work / "wrapped-bin"
    folder.mkdir(exist_ok=True)
    wrapper = folder / "ffmpeg"
    wrapper.write_text(WRAPPER_SCRIPT, encoding="utf-8")
    wrapper.chmod(0o755)
    return folder


def _is_ffmpeg(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            return any(os.path.basename(part) == b"ffmpeg" for part in handle.read().split(b"\0"))
    except OSError:
        return False


def _is_wrapper(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            return b"wrapped-bin" in handle.read()
    except OSError:
        return False


def _make_case(work: Path, files: int) -> Path:
    case = work / "case"
    if case.is_dir():
        return case
    case.mkdir(parents=True)
    clip = work / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-f", "lavfi", "-i",
         "color=c=gray:s=64x64:d=3", "-shortest", "-c:v", "mpeg4", "-c:a", "aac", str(clip)],
        check=True,
    )
    for index in range(files):
        shutil.copyfile(clip, case / f"녹화 {index:02d}.mp4")
    return case


def run_once(work: Path, case: Path, index: int, signum: int, python: str, wrapped: bool = False) -> dict[str, object]:
    base = work / f"tmp{index}"
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir()
    home = work / "home"
    env = {**os.environ, "TMPDIR": str(base), "HOME": str(home), "DEEPFAKE_LENS_LOG_DIR": str(home / "logs"),
           "PYTHONPATH": os.pathsep.join(filter(None, (str(REPO_ROOT), os.environ.get("PYTHONPATH"))))}
    if wrapped:
        env["DFL_REAL_FFMPEG"] = shutil.which("ffmpeg") or "ffmpeg"
        env["PATH"] = str(_wrapper_dir(work)) + os.pathsep + env.get("PATH", "")
    command = [python, "-m", "deepfake_lens", "scan", str(case), "--include-low", "--format", "json", "--workers", "2"]
    proc = subprocess.Popen(with_default_signals(command), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, cwd=str(work))
    linux = sys.platform.startswith("linux")
    sent = False
    children: list[int] = []
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    while proc.poll() is None and time.monotonic() < deadline:
        wav = False
        for folder in base.glob(SESSION_PREFIX + "*"):
            try:
                wav = wav or any(name.endswith(".wav") for name in os.listdir(folder))
            except OSError:
                continue
        children = _descendants(proc.pid) if linux else []  # R16-7: the whole tree
        real_ffmpeg = [child for child in children if _is_ffmpeg(child) and (not wrapped or not _is_wrapper(child))]
        if wav and (not linux or real_ffmpeg):
            proc.send_signal(signum)
            sent = True
            break
        time.sleep(0.002)
    try:
        code = proc.wait(timeout=RUN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = proc.wait()
    deadline = time.monotonic() + CHILD_GONE_SECONDS
    orphans = [child for child in children if _alive(child)]
    while orphans and time.monotonic() < deadline:
        time.sleep(0.02)
        orphans = [child for child in orphans if _alive(child)]
    left_now = sorted(os.listdir(base))
    time.sleep(STAYS_GONE_SECONDS)
    left_later = sorted(os.listdir(base))
    problems: list[str] = []
    if not sent:
        problems.append("신호를 보낼 시점(임시 wav + ffmpeg 실행 중)을 잡지 못함")
    if orphans:
        problems.append(f"스캔 종료 후에도 실행 중인 자손 프로세스: {orphans}")
    if signum == signal.SIGKILL:
        if len(left_later) > 1 or any(not name.startswith(SESSION_PREFIX) for name in left_later):
            problems.append(f"SIGKILL 후 남은 항목: {left_later}")
        for name in left_later:
            old = time.time() - 2 * STALE_MIN_AGE_SECONDS
            os.utime(base / name, (old, old))
        sweep_stale_sessions(str(base))
        if os.listdir(base):
            problems.append(f"다음 실행의 정리가 지우지 못함: {sorted(os.listdir(base))}")
    elif left_now or left_later:
        contents = {name: sorted(os.listdir(base / name)) for name in left_later if (base / name).is_dir()}
        problems.append(f"남은 항목: 직후 {left_now}, {STAYS_GONE_SECONDS}초 후 {left_later} {contents}")
    shutil.rmtree(base, ignore_errors=True)
    return {"run": index, "code": code, "children_at_signal": children, "problems": problems}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help

    parser = KoreanArgumentParser(description="종료 스트레스 시험(R15-1, R16-7): 실행 중인 스캔에 신호를 보내 남는 임시 항목·자손 프로세스가 없는지 반복 확인합니다.")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help=f"반복 횟수(기본 {DEFAULT_RUNS}, CI는 20)")
    parser.add_argument("--signal", choices=("TERM", "HUP", "KILL"), default="TERM", help="보낼 신호(기본 TERM)")
    parser.add_argument("--files", type=int, default=DEFAULT_FILES, help=f"스캔할 영상 수(기본 {DEFAULT_FILES})")
    parser.add_argument("--work", type=Path, default=None, help="작업 폴더(기본: 새 임시 폴더, 끝나면 삭제)")
    parser.add_argument("--python", default=sys.executable, help="스캔을 실행할 파이썬(기본: 이 파이썬)")
    parser.add_argument("--wrapped-ffmpeg", action="store_true", help="R16-7: 진짜 ffmpeg를 자식으로 실행하는 sh 래퍼를 PATH 앞에 둠(손자 프로세스 정리 확인)")
    args = parser.parse_args(argv)
    if shutil.which("ffmpeg") is None:
        print("ffmpeg가 없어 시험 영상을 만들 수 없습니다.")
        return 2
    signum = getattr(signal, f"SIG{args.signal}")
    owned = args.work is None
    work = Path(tempfile.mkdtemp(prefix="dfl-stress-")) if owned else args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    try:
        case = _make_case(work, args.files)
        failed = 0
        for index in range(args.runs):
            outcome = run_once(work, case, index, signum, args.python, args.wrapped_ffmpeg)
            if outcome["problems"]:
                failed += 1
                print(json.dumps(outcome, ensure_ascii=False), flush=True)
        wrapped = ", sh 래퍼 ffmpeg(손자)" if args.wrapped_ffmpeg else ""
        print(f"종료 스트레스 시험: 신호 SIG{args.signal}{wrapped}, {args.runs}회 중 실패 {failed}회")
        return 1 if failed else 0
    finally:
        if owned:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
