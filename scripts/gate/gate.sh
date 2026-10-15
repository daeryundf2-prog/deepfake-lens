#!/bin/bash
# Phase-0 gate runner (HANDOFF §5). Copies /repo (minus venvs) to /work so file-system semantics are native Linux,
# then runs the gates. Every step runs with pipefail, so a failing step fails the gate even when piped to tail.
# Usage (from the repo root):
#   docker run --rm --init --cpus 4 -v "$PWD":/repo:ro -v "$PWD/scripts/gate/gate.sh":/gate.sh:ro deepfake-lens-gate /gate.sh full|tests|lint|qa-main|cmd "<shell>"
#   docker run --rm --init --cpus 3 --shm-size=1g -v "$PWD":/repo:ro -v "$PWD/scripts/gate/gate.sh":/gate.sh:ro deepfake-lens-gate:gui /gate.sh gui
#   techniques-2026: the same with deepfake-lens-gate:tech runs the watermark decoders (torch/onnxruntime + weights).
# Read each step's output, not only "GATE rc=". Records checks need git history: mount a --no-hardlinks clone whose
# local `main` points at the phase-0 start (dad9730).
set -u
mode=${1:-full}
rm -rf /work && mkdir /work && cd /repo && tar --exclude=./.venv --exclude=./.venv-api --exclude=./.git -cf - . | (cd /work && tar xf -)
cp -a /repo/.git /work/.git 2>/dev/null || true
# techniques-2026: the deepfake-lens-gate:tech image carries the pinned watermark decoder weights
# (scripts/gate/Dockerfile.tech); they go where the loaders read them. Absent in the reference image.
[ -d /opt/deepfake-lens-weights ] && cp -a /opt/deepfake-lens-weights/. /work/deepfake_lens/models/
cd /work
git config --global --add safe.directory /work 2>/dev/null || true
export PYTHONPATH=/work
P=/venv/bin/python
A=/venv-api/bin/python
rc=0
# R23 residual (round 23): the QA harness in a side venv that sees /venv first (librosa, numba, soundfile, c2pa) and
# /venv-api after it (fastapi/httpx — the harness's QA-OUT-4 precondition — and pymupdf), so QA-IN-1 / QA-OUT-4 run
# their audio cases with the audio stack. docs/CONFORMANCE.md is still generated from /venv-api (HANDOFF §5).
# R24-9 (round 24): it runs with HOME (and the XDG folders) in /tmp/qa-main/home and without writing bytecode, so it
# writes under /tmp only — /tmp/qa-main (records, logs, the HOME's tool folder ~/.cache/deepfake-lens with its logs
# and numba cache) and /tmp/qa-main-venv (with its environment's numba cache, R22-9); the tests' own temp folders are
# under /tmp too. It used to write the caller's ~/.cache/deepfake-lens and stdlib __pycache__ files.
QA_MAIN="mkdir -p /tmp/qa-main/home && export HOME=/tmp/qa-main/home XDG_CACHE_HOME=/tmp/qa-main/home/.cache \
  XDG_DATA_HOME=/tmp/qa-main/home/.local/share XDG_CONFIG_HOME=/tmp/qa-main/home/.config PYTHONDONTWRITEBYTECODE=1 && \
  python3 -m venv --without-pip /tmp/qa-main-venv && \
  { /venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()[\"purelib\"])'; /venv-api/bin/python -c 'import sysconfig; print(sysconfig.get_paths()[\"purelib\"])'; } \
  > \$(/tmp/qa-main-venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()[\"purelib\"])')/gate-qa-main.pth && \
  /tmp/qa-main-venv/bin/python -c 'import librosa, fastapi, httpx' && \
  /tmp/qa-main-venv/bin/python scripts/qa_phase0.py --qa-only --allow-dirty --out /tmp/qa-main/CONFORMANCE.md --log-dir /tmp/qa-main/logs 2>&1 | tail -4"
ut() { echo "=== unittest ($1)"; $1 -m unittest discover deepfake_lens/tests > /tmp/ut.log 2>&1; r=$?; grep -aE '^(FAIL|ERROR):|^Ran|^OK|^FAILED' /tmp/ut.log; [ $r -eq 0 ] || { echo "!!! FAILED rc=$r: unittest ($1)"; rc=1; }; }
run() { echo "=== $*"; bash -o pipefail -c "$*" || { echo "!!! FAILED rc=$?: $*"; rc=1; }; }
case $mode in
  tests) ut "$P"; ut "$A";;
  lint) run "/venv/bin/ruff check deepfake_lens scripts experiments"; run "/venv/bin/mypy";;
  cmd) shift; run "$*";;
  qa-main) run "$QA_MAIN";;
  gui)
    # R24-6 (round 24): gui_smoke.py uses Playwright's Chromium when Google Chrome is absent (this image) and scans
    # with subfolders (neither folder below has files at its top level).
    run "/venv-gui/bin/python scripts/gui_smoke.py --scan-dir fixtures --port 8899 --shot /tmp/gui-smoke.png 2>&1 | tail -5"
    run "/venv-gui/bin/python scripts/make_adversarial_fixtures.py --out /tmp/adv >/dev/null 2>&1; /venv-gui/bin/python scripts/gui_smoke.py --scan-dir /tmp/adv --port 8898 --shot /tmp/gui-smoke-adv.png 2>&1 | tail -5"
    ;;
  full)
    ut "$P"; ut "$A"
    run "/venv/bin/ruff check deepfake_lens scripts experiments"
    run "/venv/bin/mypy | tail -2"
    run "/venv/bin/coverage run -m unittest discover deepfake_lens/tests >/dev/null 2>&1; /venv/bin/coverage report | tail -1"
    run "$P scripts/cli_smoke_test.py | tail -2"
    run "$P scripts/check_measurement_gate.py | tail -2"
    run "$P scripts/sync_model_docs.py --check | tail -2"
    run "$P scripts/verify_contracts.py | tail -2"
    run "$P scripts/stress_shutdown.py --runs 30 | tail -3"
    run "$QA_MAIN"
    ;;
esac
echo "GATE rc=$rc"; exit $rc
