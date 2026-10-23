"""Loads the files written by fetch_angel.py (or fetch_data.py).
Corporate actions you add in data/corporate_actions.csv are applied here (see core/quality.py)."""
import subprocess, sys
for _p in ["pandas", "pyarrow", "tabulate"]:
    try:
        __import__(_p)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", _p])
import pandas as pd
from .config import DATA

BROAD = DATA / "prices_broad.parquet"     # stocks outside today's Nifty 500 (fetch_angel.py --broad)


def _norm(d):
    d["date"] = pd.to_datetime(d.date).dt.tz_localize(None).dt.normalize()
    return d


def load_raw(broad=False):
    """broad=True adds the stocks outside today's Nifty 500 (if downloaded), for point-in-time universes."""
    from .quality import apply_corporate_actions
    px = _norm(pd.read_parquet(DATA / "prices.parquet"))
    if broad and BROAD.exists():
        b = _norm(pd.read_parquet(BROAD))
        px = pd.concat([px, b[~b.symbol.isin(set(px.symbol))]], ignore_index=True)
    idx = _norm(pd.read_parquet(DATA / "index.parquet"))
    uni = pd.read_csv(DATA / "universe.csv")
    px, _ = apply_corporate_actions(px)
    return px, idx, uni


def has_broad():
    return BROAD.exists()
