"""Market Debate Lab: bull and bear research agents, a statistical validation engine, a probability engine and a judge.

Layers (each its own module, so the lab can change without touching the screener):
  features.py    point-in-time indicator panel for all stocks (deterministic)
  evidence.py    the evidence layer: every fact with an ID, source, timestamps and quality flag
  signals.py     pre-registered, testable signal definitions (the only technical claims an agent can make)
  stats.py       statistical agent: tests claims on history (no language model)
  probability.py probability engine: walk-forward logistic model, calibration and baselines
  agents.py      bull and bear research agents (local model writes the text; code checks every reference)
  engine.py      debate orchestration (independent theses -> cross-examination -> statistics -> revision -> ledger)
  judge.py       final verdict, abstention rules and explanation
  store.py       immutable forecast records; evaluate.py scores them once the horizon has passed

Methodology versions are recorded in every forecast so old forecasts stay interpretable."""
import subprocess, sys

VERSIONS = {"features": "1.0", "signals": "1.0", "stats": "1.0", "probability": "1.0", "agents": "1.0", "judge": "1.0"}


def _need(mod, pip_name):
    try:
        __import__(mod)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pip_name])


_need("scipy", "scipy")
