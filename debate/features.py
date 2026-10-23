"""Point-in-time indicator panel for every stock (one row per symbol and trading day).

Every value at (symbol, t) uses prices up to and including the close of t only. Prices are the stored daily bars with
the corporate actions in data/corporate_actions.csv / split_adjustments applied (core.data.load_raw), so returns are
split-adjusted PRICE returns; dividends are not added back.

Indicator definitions (version in debate.VERSIONS["features"]):
  sma n           simple moving average of close
  rsi14 / rsi3    Wilder RSI
  macd            EMA12 - EMA26 of close; macd_sig = EMA9 of macd; macd_hist = macd - macd_sig
  adx14, pdi, mdi Wilder directional movement (14)
  bb_up / bb_lo   SMA20 +- 2 standard deviations (population)
  atr14           14-day mean true range; atr_pct = atr14 / close
  vol_ratio       volume / mean volume of the previous 20 sessions
  obv_chg20       on-balance volume change over 20 sessions / mean volume of 20 sessions
  hh55/ll55/hh252 highest high / lowest low of the previous n sessions (today excluded)
  gap             open / previous close - 1
  rs63            63-session return minus the Nifty 50's; rs_pct = cross-sectional percentile of rs63
  sector_rank_pct percentile of the stock's sector 21-session return among sectors (1 = strongest)
Market: nifty vs its 200-day average, VIX vs its 1-year median, breadth (share of stocks above their 200-day average)."""
import numpy as np, pandas as pd
from core import config as C

PANEL = C.DATA / "debate" / "panel.parquet"


def _wilder(x, n):
    return x.ewm(alpha=1 / n, adjust=False).mean()


def _per_symbol(g):
    g = g.sort_values("date").copy()
    c, h, l, v, o = g.close, g.high, g.low, g.volume.astype(float), g.open
    for n in (20, 50, 200):
        g[f"sma{n}"] = c.rolling(n).mean()
    d = c.diff()
    for n in (14, 3):
        up, dn = _wilder(d.clip(lower=0), n), _wilder((-d).clip(lower=0), n)
        g[f"rsi{n}"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    e12, e26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    g["macd"] = e12 - e26
    g["macd_sig"] = g.macd.ewm(span=9, adjust=False).mean()
    g["macd_hist"] = g.macd - g.macd_sig
    pc = c.shift()
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    g["atr14"] = tr.rolling(14).mean()
    up_m, dn_m = h.diff(), -l.diff()
    pdm = np.where((up_m > dn_m) & (up_m > 0), up_m, 0.0)
    mdm = np.where((dn_m > up_m) & (dn_m > 0), dn_m, 0.0)
    atr_w = _wilder(tr, 14)
    g["pdi"] = 100 * _wilder(pd.Series(pdm, index=g.index), 14) / atr_w
    g["mdi"] = 100 * _wilder(pd.Series(mdm, index=g.index), 14) / atr_w
    dx = 100 * (g.pdi - g.mdi).abs() / (g.pdi + g.mdi).replace(0, np.nan)
    g["adx14"] = _wilder(dx, 14)
    sd = c.rolling(20).std(ddof=0)
    g["bb_up"], g["bb_lo"] = g.sma20 + 2 * sd, g.sma20 - 2 * sd
    vm = v.rolling(20).mean()
    g["vol_ratio"] = v / vm.shift(1)
    obv = (np.sign(d.fillna(0)) * v).cumsum()
    g["obv_chg20"] = (obv - obv.shift(20)) / vm
    g["hh55"] = h.rolling(55).max().shift(1)
    g["ll55"] = l.rolling(55).min().shift(1)
    g["hh252"] = h.rolling(252).max().shift(1)
    g["gap"] = o / pc - 1
    g["prev_close"] = pc
    for n in (1, 5, 21, 63):
        g[f"ret{n}"] = c / c.shift(n) - 1
    g["rv20"] = c.pct_change(fill_method=None).rolling(20).std() * np.sqrt(252)
    g["turnover_cr"] = (c * v).rolling(20).mean() / 1e7
    g["n_hist"] = np.arange(1, len(g) + 1)
    return g


def market_frame(idx, px):
    m = idx.sort_values("date").copy()
    m["nifty_sma200"] = m.nifty.rolling(200).mean()
    m["nifty_vs_200"] = m.nifty / m.nifty_sma200 - 1
    m["nifty_ret63"] = m.nifty / m.nifty.shift(63) - 1
    m["vix_rel"] = m.vix / m.vix.rolling(252, min_periods=120).median()
    br = px.assign(a=px.close > px.sma200).groupby("date").a.mean().rename("breadth")
    return m.merge(br, on="date", how="left")


def build(px, idx, universe):
    """px: date, symbol, open, high, low, close, volume. Returns (panel, market)."""
    px = px.dropna(subset=["open", "high", "low", "close"])
    px = px[(px.close > 0) & (px.open > 0)].copy()
    px = pd.concat([_per_symbol(g) for _, g in px.groupby("symbol", sort=False)], ignore_index=True)
    sec = universe.rename(columns={"Symbol": "symbol", "Industry": "sector"})[["symbol", "sector"]].drop_duplicates("symbol")
    px = px.merge(sec, on="symbol", how="left")
    px["sector"] = px.sector.fillna("Unknown")
    m = market_frame(idx, px)
    px = px.merge(m[["date", "nifty", "nifty_vs_200", "nifty_ret63", "vix_rel", "breadth"]], on="date", how="left")
    px["rs63"] = px.ret63 - px.nifty_ret63
    px["rs_pct"] = px.groupby("date").rs63.rank(pct=True)
    px["atr_pct"] = px.atr14 / px.close
    px["atr_pct_rank"] = px.groupby("date").atr_pct.rank(pct=True)
    s = px[px.sector != "Unknown"].groupby(["date", "sector"]).agg(sret=("ret21", "mean"), n=("ret21", "count")).reset_index()
    s = s[s.n >= 3]
    s["sector_rank_pct"] = s.groupby("date").sret.rank(pct=True)
    px = px.merge(s[["date", "sector", "sector_rank_pct", "sret"]].rename(columns={"sret": "sector_ret21"}), on=["date", "sector"], how="left")
    return px.sort_values(["symbol", "date"]).reset_index(drop=True), m


def load(refresh=False, log=print):
    """Cached panel, rebuilt when prices change. Returns (panel, market)."""
    from core.data import load_raw
    from core import atomic
    from . import VERSIONS
    src = C.DATA / "prices.parquet"
    stamp = f"{src.stat().st_mtime:.0f}|{VERSIONS['features']}"
    meta = PANEL.with_suffix(".stamp")
    mfile = PANEL.with_name("market.parquet")
    if not refresh and PANEL.exists() and mfile.exists() and meta.exists() and meta.read_text() == stamp:
        return pd.read_parquet(PANEL), pd.read_parquet(mfile)
    log("Building the indicator panel for all stocks (once per price update, about a minute)...")
    px, idx, uni = load_raw()
    panel, m = build(px, idx, uni)
    atomic.to_parquet(panel, PANEL)
    atomic.to_parquet(m, mfile)
    atomic.write_text(meta, stamp)
    return panel, m


def forward(panel, h, market=None):
    """Adds fwd (h-session forward return of the stock) and fwd_rel (minus the Nifty's) plus end_date.
    Rows whose window runs past the data have NaN."""
    p = panel
    g = p.groupby("symbol", sort=False)
    p = p.assign(fwd=g.close.shift(-h) / p.close - 1, end_date=g.date.shift(-h))
    nf = p.groupby("symbol", sort=False).nifty.shift(-h) / p.nifty - 1
    p["fwd_rel"] = p.fwd - nf
    return p
