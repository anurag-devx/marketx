#!/bin/bash
# The new MarketX app (built-in Python web server, plain HTML/JS/CSS). Opens http://127.0.0.1:8504 in your browser.
# Uses the project's virtual environment if setup.sh has created it, otherwise the system Python.
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
"$PY" cli.py web 8504
