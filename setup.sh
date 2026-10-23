#!/bin/bash
# One-time setup (and safe to re-run): creates .venv in this folder, installs the packages, runs the tests,
# records the exact versions in requirements.lock, and points the after-close schedule at the new Python.
#   ./setup.sh            core packages
#   ./setup.sh --ai       also the documents-library / AI packages (about 200 MB more)
# Your data, settings and keys are not touched. To go back to the old setup: delete the .venv folder.
set -euo pipefail
cd "$(dirname "$0")"
trap 'echo; echo "Setup stopped before finishing. Nothing was switched: the old Python and the schedule still work as before."' ERR

pick_python() {
  for p in python3.11 python3.12 python3.13 python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
      echo "$p"; return
    fi
  done
  echo "No Python 3.10+ found. Install Python 3.11 from python.org first." >&2; exit 1
}

PY=$(pick_python)
echo "Using $($PY --version) at $(command -v $PY)"
if [ ! -x .venv/bin/python ]; then
  echo "Creating .venv ..."
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install -q --upgrade pip
echo "Installing packages (a few minutes the first time) ..."
.venv/bin/python -m pip install -q -r requirements.txt -r requirements-dev.txt
# AI packages: when asked (--ai) or when the old Python already had them, so Documents / Search keep working
if [ "${1:-}" = "--ai" ] || "$PY" -c "import lancedb, llama_index.core" >/dev/null 2>&1; then
  echo "Installing documents-library / AI packages ..."
  .venv/bin/python -m pip install -q -r requirements-ai.txt
fi
echo "Running the tests ..."
.venv/bin/python -m pytest -q -p no:cacheprovider tests | tail -3
.venv/bin/python -m pip freeze > requirements.lock
echo "Exact versions saved to requirements.lock"
.venv/bin/python cli.py schedule-refresh || true
echo
echo "Done. Start the dashboard with:  .venv/bin/python cli.py dashboard 8503   (or double-click Start Dashboard.command)"
