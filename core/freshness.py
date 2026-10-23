"""Which Nifty 500 stocks have no bar for the latest trading day, and why.

fetch_angel.py writes data/stale_symbols.json after every download (reason per stock: download failed, not in
Angel One's instrument list, or no bar returned). If that file is missing or older than the prices, the list is
worked out from the prices themselves (reason unknown). Those stocks have no row on the latest day, so the screener
and agents leave them out: this module is how that is made visible instead of silent."""
import json
import pandas as pd
from . import config as C

FILE = C.DATA / "stale_symbols.json"


def stale():
    """{'day': 'YYYY-MM-DD' | None, 'stocks': [{'symbol', 'last_bar', 'reason'}]}"""
    p = C.DATA / "prices.parquet"
    if not p.exists():
        return dict(day=None, stocks=[])
    px = pd.read_parquet(p, columns=["date", "symbol"])
    px["date"] = pd.to_datetime(px.date)
    day = px.date.max()
    try:
        j = json.loads(FILE.read_text()) if FILE.exists() else None
    except Exception:
        j = None
    if j and j.get("day") == str(day.date()):
        return j
    uni = C.DATA / "universe.csv"
    syms = set(pd.read_csv(uni).Symbol) if uni.exists() else set(px.symbol)
    last = px.groupby("symbol").date.max()
    rows = [dict(symbol=s, last_bar=None if s not in last else str(last[s].date()), reason="no bar for the latest day (reason not recorded)")
            for s in sorted(syms) if s not in last or last[s] < day]
    return dict(day=str(day.date()), stocks=rows)
