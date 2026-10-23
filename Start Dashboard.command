#!/bin/bash
# Uses the project's virtual environment if setup.sh has created it, otherwise the system Python.
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
"$PY" cli.py dashboard 8503
