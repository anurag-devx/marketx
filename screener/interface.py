"""What the downstream strategy module receives from the screener: candidates, not trade decisions.

    from screener.interface import load_shortlist
    sl = load_shortlist()                 # latest date, every enabled screener strategy
    sl.rows                               # one row per (strategy, symbol): ids, features, setup flags, ranking, warnings
    sl.symbols()                          # qualified symbols
    sl.config[strategy]                   # exact screener configuration used, sl.version, sl.data_version

The downstream module applies its own regime, entry, risk and exit rules. It never needs to refetch data
or recompute indicators: every feature value the screener used is in `rows`."""
import json
from dataclasses import dataclass, field
import pandas as pd
from core import db

TABLE = "screener_results"


@dataclass
class Shortlist:
    screen_date: pd.Timestamp
    rows: pd.DataFrame
    config: dict = field(default_factory=dict)
    version: str = ""
    data_version: str = ""
    warnings: list = field(default_factory=list)

    def symbols(self, strategy=None):
        r = self.rows if strategy is None else self.rows[self.rows.screener_strategy == strategy]
        return sorted(r.symbol.unique())

    def empty(self):
        return self.rows.empty


def save(date, results, strategy_name, meta):
    """results: screen()['rows'] (all stocks, so rejections stay inspectable). Replaces that date + strategy."""
    from .screen import OUTPUT_COLS
    r = results[[c for c in OUTPUT_COLS if c in results]].copy()
    r["screener_strategy"] = strategy_name
    r["config_json"] = json.dumps(meta["config"], default=str)
    db.delete(TABLE, "cast(screen_date as date) = cast(? as date) and screener_strategy = ?", [str(pd.Timestamp(date).date()), strategy_name])
    db.write(TABLE, r)


def load_shortlist(date=None, strategy=None, qualified_only=True):
    q = f"select * from {TABLE}"
    r = db.read(q)
    if r is None or r.empty:
        return Shortlist(pd.NaT, pd.DataFrame(), warnings=["screener has not run yet"])
    r["screen_date"] = pd.to_datetime(r.screen_date)
    d = pd.Timestamp(date) if date is not None else r.screen_date.max()
    r = r[r.screen_date == d]
    if strategy:
        r = r[r.screener_strategy == strategy]
    if qualified_only:
        r = r[r.qualified]
    cfg = {s: json.loads(g.config_json.iat[0]) for s, g in r.groupby("screener_strategy")} if len(r) else {}
    warn = sorted({w for x in r.data_quality_flags.fillna("") for w in x.split("; ") if w})
    return Shortlist(d, r.drop(columns=["config_json"]).sort_values(["screener_strategy", "rank"]).reset_index(drop=True), cfg,
                     version=r.screener_version.iat[0] if len(r) else "", data_version=r.data_version.iat[0] if len(r) else "",
                     warnings=warn)
