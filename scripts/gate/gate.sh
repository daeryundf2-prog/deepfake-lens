#!/bin/bash
# Phase-0 gate runner (HANDOFF §5). Copies /repo (minus venvs) to /work so file-system semantics are native Linux,
# then runs the gates. Every step runs with pipefail, so a failing step fails the gate even when piped to tail.
# Usage (from the repo root):
#   docker run --rm --init --cpus 4 -v "$PWD":/repo:ro -v "$PWD/scripts/gate/gate.sh":/gate.sh:ro deepfake-lens-gate /gate.sh full|tests|lint|cmd "<shell>"
#   docker run --rm --init --cpus 3 --shm-size=1g -v "$PWD":/repo:ro -v "$PWD/scripts/gate/gate.sh":/gate.sh:ro deepfake-lens-gate:gui /gate.sh gui
# Read each step's output, not only "GATE rc=". Records checks need git history: mount a --no-hardlinks clone whose
# local `main` points at the phase-0 start (dad9730).
set -u
mode=${1:-full}
rm -rf /work && mkdir /work && cd /repo && tar --exclude=./.venv --exclude=./.venv-api --exclude=./.git -cf - . | (cd /work && tar xf -)
cp -a /repo/.git /work/.git 2>/dev/null || true
cd /work
git config --global --add safe.directory /work 2>/dev/null || true
export PYTHONPATH=/work
P=/venv/bin/python
A=/venv-api/bin/python
rc=0
ut() { echo "=== unittest ($1)"; $1 -m unittest discover deepfake_lens/tests > /tmp/ut.log 2>&1; r=$?; grep -aE '^(FAIL|ERROR):|^Ran|^OK|^FAILED' /tmp/ut.log; [ $r -eq 0 ] || { echo "!!! FAILED rc=$r: unittest ($1)"; rc=1; }; }
run() { echo "=== $*"; bash -o pipefail -c "$*" || { echo "!!! FAILED rc=$?: $*"; rc=1; }; }
case $mode in
  tests) ut "$P"; ut "$A";;
  lint) run "/venv/bin/ruff check deepfake_lens scripts experiments"; run "/venv/bin/mypy";;
  cmd) shift; run "$*";;
  gui)
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
    ;;
esac
echo "GATE rc=$rc"; exit $rc
