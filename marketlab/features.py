"""Scenario panel: one row per (stock, trading day t) describing what was known by 21:00 IST on t, plus what happened
on the next session (the outcomes). See docs/market_lab.md.

Point-in-time:
  prices / market / sector  from the close of t (and earlier)
  results / news / filings  published after 21:00 on the previous trading day and up to 21:00 on t
  macro                     the latest FRED value dated before t (lagged a day: US data lands overnight)
Outcomes (session t+1): cc = close/close - 1, oc = close/open - 1 (intraday), gp = open/previous close - 1 (overnight gap)."""
import numpy as np, pandas as pd
from core import config as C

PANEL = C.DATA / "marketlab" / "panel.parquet"
SIZES = (0.5, 1.0, 2.0, 3.0)
MOVES = ("cc", "oc", "gp")
MATERIAL = {"Order / contract", "Acquisition / merger", "Fund raising", "Credit rating", "Legal / regulatory", "Shareholding / insider",
            "Management change"}
GROUPS = {
    "Price action": ["f_ret1", "f_ret5", "f_gap", "f_rng"],
    "Volume": ["f_vol", "f_vtr"],
    "Volatility": ["f_rv", "f_atr"],
    "Market": ["m_r1", "m_r5", "m_vix", "m_up"],
    "Sector": ["s_r1", "s_r5"],
    "Results": ["e_res_s", "e_res_e", "e_sue"],
    "News": ["n_cnt", "n_tone"],
    "Filings": ["a_cnt", "a_mat"],
    "Macro": ["x_brent", "x_inr", "x_vix", "x_y10"],
}
FEATURES = [f for g in GROUPS.values() for f in g]
LABEL = {"f_ret1": "today's move (× its usual daily move)", "f_ret5": "5-day move (× usual)", "f_gap": "today's opening gap (× usual)",
         "f_rng": "position in its 20-day range (−0.5 low … +0.5 high)", "f_vol": "volume vs 20-day normal (log)", "f_vtr": "5-day vs 50-day volume (log)",
         "f_rv": "20-day volatility (log)", "f_atr": "daily range % (log)", "m_r1": "Nifty today", "m_r5": "Nifty 5 days", "m_vix": "India VIX vs 1-year median",
         "m_up": "share of stocks up today", "s_r1": "sector today minus Nifty", "s_r5": "sector 5 days minus Nifty",
         "e_res_s": "results filed during today's session", "e_res_e": "results filed after the close", "e_sue": "results surprise (SUE)",
         "n_cnt": "headlines naming the stock (log count)", "n_tone": "net news tone (−1 … +1)", "a_cnt": "other filings (log count)",
         "a_mat": "material filing (order, deal, rating, legal, insider, management)", "x_brent": "Brent crude, last change %",
         "x_inr": "USD-INR, last change %", "x_vix": "US VIX, last change", "x_y10": "US 10-year yield, last change"}


def decision_day(ts, days):
    """Trading day whose 21:00 IST cut-off is the first at or after ts. ts naive IST; days sorted unique."""
    days = pd.DatetimeIndex(days).astype(NS)
    cut = days + pd.Timedelta(hours=21)
    i = np.searchsorted(cut.values, pd.DatetimeIndex(ts).astype(NS).values, side="left")
    out = pd.Series(pd.NaT, index=range(len(i)), dtype="datetime64[ns]")
    ok = i < len(days)
    out[ok] = days[i[ok]]
    return out.values


NS = "datetime64[ns]"      # one date precision everywhere (newer pandas mixes ns and us otherwise)


def _prices(px):
    px = px.assign(date=pd.to_datetime(px.date).astype(NS)).sort_values(["symbol", "date"]).reset_index(drop=True)
    g = px.groupby("symbol", sort=False)
    c, o, h, l, v = px.close, px.open, px.high, px.low, px.volume.astype(float)
    pc = g.close.shift(1)
    r1 = c / pc - 1
    sd = r1.groupby(px.symbol).transform(lambda x: x.rolling(20, min_periods=15).std())
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.groupby(px.symbol).transform(lambda x: x.rolling(14, min_periods=10).mean())
    hh = h.groupby(px.symbol).transform(lambda x: x.rolling(20, min_periods=10).max())
    ll = l.groupby(px.symbol).transform(lambda x: x.rolling(20, min_periods=10).min())
    v20 = v.groupby(px.symbol).transform(lambda x: x.shift(1).rolling(20, min_periods=10).mean())
    v5 = v.groupby(px.symbol).transform(lambda x: x.rolling(5).mean())
    v50 = v.groupby(px.symbol).transform(lambda x: x.rolling(50, min_periods=30).mean())
    c5 = g.close.shift(5)
    d = pd.DataFrame({"symbol": px.symbol, "date": px.date, "close": c, "r1": r1})
    sdv = sd.replace(0, np.nan)
    d["f_ret1"] = r1 / sdv
    d["f_ret5"] = (c / c5 - 1) / (sdv * np.sqrt(5))
    d["f_gap"] = (o / pc - 1) / sdv
    d["f_rng"] = (c - ll) / (hh - ll).replace(0, np.nan) - 0.5
    d["f_vol"] = np.log((v / v20.replace(0, np.nan)).clip(0.05, 50))
    d["f_vtr"] = np.log((v5 / v50.replace(0, np.nan)).clip(0.05, 50))
    d["f_rv"] = np.log((sd * np.sqrt(252)).clip(0.02, 5))
    d["f_atr"] = np.log((atr / c).clip(0.001, 0.5))
    nc, no = g.close.shift(-1), g.open.shift(-1)
    d["cc"], d["oc"], d["gp"] = nc / c - 1, nc / no - 1, no / c - 1
    d["next_date"] = g.date.shift(-1)
    d["raw_r1"], d["raw_r5"], d["raw_gap"] = 100 * r1, 100 * (c / c5 - 1), 100 * (o / pc - 1)
    d["raw_volx"], d["raw_rv"] = v / v20.replace(0, np.nan), 100 * sd * np.sqrt(252)
    # the stock's usual chance: share of the last 250 sessions with a move that big (moves known by t)
    a = {"cc": r1.abs(), "oc": (c / o - 1).abs(), "gp": (o / pc - 1).abs()}
    for m, s in a.items():
        for z in SIZES:
            d[f"u_{m}_{z}"] = (s >= z / 100).astype(float).where(s.notna()).groupby(px.symbol).transform(
                lambda x: x.rolling(250, min_periods=60).mean())
    return d


def _market(d, idx):
    ix = idx.assign(date=pd.to_datetime(idx.date).astype(NS)).sort_values("date").copy()
    ix["m_r1"] = ix.nifty.pct_change(fill_method=None)
    ix["m_r5"] = ix.nifty.pct_change(5, fill_method=None)
    ix["m_vix"] = np.log((ix.vix / ix.vix.rolling(252, min_periods=60).median()).clip(0.2, 5)) if "vix" in ix else 0.0
    up = d.groupby("date").r1.apply(lambda x: (x > 0).mean()).rename("m_up").reset_index()
    return d.merge(ix[["date", "m_r1", "m_r5", "m_vix"]], on="date", how="left").merge(up, on="date", how="left")


def _sector(d, uni):
    col = "Symbol" if "Symbol" in uni else "symbol"
    sec = dict(zip(uni[col].astype(str), uni.get("Industry", pd.Series("", index=uni.index)).astype(str)))
    d["sector"] = d.symbol.map(sec).fillna("")
    d["r5"] = d.groupby("symbol").close.pct_change(5, fill_method=None)
    s = d.groupby(["date", "sector"]).agg(s1=("r1", "mean"), s5=("r5", "mean")).reset_index()
    d = d.merge(s, on=["date", "sector"], how="left")
    d["s_r1"] = d.s1 - d.m_r1
    d["s_r5"] = d.s5 - d.m_r5
    return d.drop(columns=["s1", "s5", "r5"])


def _events(d, days, news=None, ann=None, quarters=None):
    keys = ["symbol", "date"]
    parts = []
    if ann is not None and len(ann):
        a = ann.dropna(subset=["published", "symbol"]).copy()
        a["date"] = decision_day(pd.to_datetime(a.published), days)
        a = a.dropna(subset=["date"])
        t = pd.to_datetime(a.published)
        a["evening"] = (t.dt.normalize() == a.date) & (t.dt.hour * 60 + t.dt.minute > 15 * 60 + 30)
        res = a[a.category == "Results"]
        oth = a[~a.category.isin(["Results", "Routine compliance"])]
        parts.append(res.groupby(keys).agg(e_res_s=("evening", lambda x: float((~x).any())), e_res_e=("evening", lambda x: float(x.any()))))
        parts.append(oth.assign(m=oth.category.isin(MATERIAL)).groupby(keys).agg(a_cnt=("id", "count"), a_mat=("m", "max")))
    if quarters is not None and len(quarters) and "filed_at" in quarters:
        qd = quarters.dropna(subset=["filed_at"]).copy()
        qd["date"] = decision_day(pd.to_datetime(qd.filed_at), days)
        qd = qd.dropna(subset=["date"])
        t = pd.to_datetime(qd.filed_at)
        ev = (t.dt.normalize() == qd.date) & (t.dt.hour * 60 + t.dt.minute > 15 * 60 + 30)
        qd["q_s"], qd["q_e"] = (~ev).astype(float), ev.astype(float)
        parts.append(qd.groupby(keys).agg(q_s=("q_s", "max"), q_e=("q_e", "max"), e_sue=("sue_profit", "last")))
    if news is not None and len(news):
        n = news[news.symbols.astype(str).str.len() > 0][["published", "symbols", "tone"]].copy()
        n["symbol"] = n.symbols.astype(str).str.split(",")
        n = n.explode("symbol")
        n["symbol"] = n.symbol.str.strip()
        n["date"] = decision_day(pd.to_datetime(n.published), days)
        n = n.dropna(subset=["date"])
        n["tn"] = n.tone.map({"positive": 1.0, "negative": -1.0}).fillna(0.0)
        parts.append(n.groupby(keys).agg(n_cnt=("tn", "size"), n_tone=("tn", "mean")))
    for p in parts:
        d = d.merge(p.reset_index(), on=keys, how="left")
    for c in ("e_res_s", "e_res_e", "q_s", "q_e", "a_cnt", "a_mat", "n_cnt", "n_tone", "e_sue"):
        if c not in d:
            d[c] = 0.0
    d["e_res_s"] = np.fmax(d.e_res_s.fillna(0), d.q_s.fillna(0))
    d["e_res_e"] = np.fmax(d.e_res_e.fillna(0), d.q_e.fillna(0))
    d["e_sue"] = d.e_sue.astype(float).clip(-5, 5).fillna(0)
    d["a_cnt"] = np.log1p(d.a_cnt.fillna(0).astype(float))
    d["a_mat"] = d.a_mat.fillna(0).astype(float)
    d["n_cnt"] = np.log1p(d.n_cnt.fillna(0).astype(float))
    d["n_tone"] = d.n_tone.fillna(0).astype(float)
    return d.drop(columns=["q_s", "q_e"])


MACRO_LAG = {"x_brent": 7, "x_inr": 7, "x_vix": 1, "x_y10": 1}   # days after the observation date a value is taken as known
MACRO_FRESH = 4                                                     # a change counts only if it became known in the last 4 days


def _macro(d, macro):
    """Each series' latest change, counted from the day it was published (FRED: Brent and USD-INR come about a week late),
    so history only uses what was known then. A change is used once (within MACRO_FRESH days), never carried forward."""
    names = {"DCOILBRENTEU": "x_brent", "DEXINUS": "x_inr", "VIXCLS": "x_vix", "DGS10": "x_y10"}
    if macro is None or not len(macro):
        for c in names.values():
            d[c] = 0.0
        return d
    macro = macro.assign(date=pd.to_datetime(macro.date).astype(NS))
    m = macro[macro.series.isin(names)].pivot_table(index="date", columns="series", values="value").sort_index()
    d = d.assign(date=d.date.astype(NS)).sort_values("date")
    for s, c in names.items():
        if s not in m:
            d[c] = 0.0
            continue
        x = m[s].dropna()
        ch = (100 * x.pct_change(fill_method=None) if c in ("x_brent", "x_inr") else x.diff()).dropna()
        k = pd.DataFrame({"known": (ch.index + pd.Timedelta(days=MACRO_LAG[c])).astype(NS), c: ch.to_numpy(float)}).sort_values("known")
        d = pd.merge_asof(d, k, left_on="date", right_on="known", direction="backward",
                          tolerance=pd.Timedelta(days=MACRO_FRESH)).drop(columns="known")
        d[c] = d[c].fillna(0.0)
    return d


def build(px, idx, uni, news=None, ann=None, quarters=None, macro=None, log=print):
    log("  prices, volume, volatility ...")
    d = _prices(px)
    log("  market and sector ...")
    d = _sector(_market(d, idx), uni)
    days = np.sort(d.date.unique())
    log("  results, news and filings ...")
    d = _events(d, days, news, ann, quarters)
    log("  macro ...")
    d = _macro(d, macro)
    d["raw_m_r1"], d["raw_s_r1"] = 100 * d.m_r1, 100 * (d.s_r1 + d.m_r1)
    keep = ["symbol", "date", "next_date", "sector", "close"] + [c for c in d if c.startswith("raw_")] + FEATURES + list(MOVES) + [c for c in d if c.startswith("u_")]
    d = d[keep].sort_values(["date", "symbol"]).reset_index(drop=True)
    for c in FEATURES + list(MOVES) + [c for c in d if c.startswith("u_")]:
        d[c] = d[c].astype("float32")
    return d


def load_inputs():
    from core.data import load_raw
    px, idx, uni = load_raw()
    news = ann = quarters = macro = None
    try:
        news = pd.read_parquet(C.DATA / "news" / "news.parquet", columns=["published", "symbols", "tone"])
    except Exception:
        pass
    try:
        ann = pd.read_parquet(C.DATA / "announcements.parquet", columns=["id", "published", "symbol", "category"])
    except Exception:
        pass
    try:
        quarters = pd.read_parquet(C.DATA / "fundamentals" / "quarters.parquet", columns=["symbol", "filed_at", "sue_profit"])
    except Exception:
        pass
    try:
        macro = pd.read_parquet(C.DATA / "macro" / "series.parquet")
    except Exception:
        pass
    return px, idx, uni, news, ann, quarters, macro
