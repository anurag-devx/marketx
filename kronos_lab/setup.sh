#!/bin/bash
# Installs Kronos in its own environment, apart from the rest of MarketX.
#   .venv-kronos/              its own Python packages (PyTorch etc.), not shared with .venv
#   kronos_lab/vendor/Kronos/  the model code from GitHub (MIT licence), pinned to one tested commit
#   ~/.cache/huggingface/      the model weights (downloaded once)
# Safe to run again: it only adds what is missing.
set -e
cd "$(dirname "$0")/.."
COMMIT=67b630e67f6a18c9e9be918d9b4337c960db1e9a
REPO=kronos_lab/vendor/Kronos
PY=.venv/bin/python; [ -x "$PY" ] || PY=python3
echo "1/4 Python environment (.venv-kronos)"
[ -x .venv-kronos/bin/python ] || "$PY" -m venv .venv-kronos
.venv-kronos/bin/python -m pip install -q --upgrade pip
echo "2/4 Kronos code (github.com/shiyu-coder/Kronos @ ${COMMIT:0:7})"
mkdir -p kronos_lab/vendor
if [ ! -d "$REPO/.git" ]; then git clone -q https://github.com/shiyu-coder/Kronos.git "$REPO"; fi
git -C "$REPO" fetch -q origin || true
git -C "$REPO" -c advice.detachedHead=false checkout -q "$COMMIT"
echo "3/4 Packages: PyTorch and friends (about 1 GB the first time)"
.venv-kronos/bin/python -m pip install -q "torch>=2.2" numpy pandas pyarrow "einops==0.8.1" "huggingface_hub==0.33.1" "safetensors==0.6.2" tqdm
echo "4/4 Model weights (Kronos-small, about 100 MB)"
.venv-kronos/bin/python -u -m kronos_lab.forecast --download
echo "Kronos is installed."
