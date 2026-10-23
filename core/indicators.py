"""Per-stock indicators, cross-sectional ranks and the daily market state.
Everything at row (symbol, t) uses data up to and including the close of t only."""
import numpy as np, pandas as pd
from .config import STATE_CONFIRM_DAYS, VIX_LOOKBACK


def rsi(close, n):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def per_symbol(g):
    g = g.sort_values("date").copy()
    c, h, l, v = g.close, g.high, g.low, g.volume
    for n in (5, 10, 20, 50, 200):
        g[f"sma{n}"] = c.rolling(n).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    g["atr"] = tr.rolling(14).mean()
    g["vol20_prev"] = v.rolling(20).mean().shift(1)
    g["vol_ratio"] = v / g.vol20_prev
    g["turnover_cr"] = (c * v).rolling(20).mean() / 1e7
    g["hh55_prev"] = h.rolling(55).max().shift(1)
    g["hh252_prev"] = h.rolling(252).max().shift(1)
    g["hc20_prev"] = c.rolling(20).max().shift(1)
    # 7-day box before today, and whether it was a squeeze
    g["box_high"] = h.rolling(7).max().shift(1)
    g["box_low"] = l.rolling(7).min().shift(1)
    g["box_h"] = g.box_high - g.box_low
    bbw = 4 * c.rolling(20).std() / g.sma20
    sq = (bbw <= bbw.rolling(120).min() * 1.1).astype(float)
    rng = h - l
    nr7 = (rng <= rng.rolling(7).min()).astype(float)
    g["squeeze_prev"] = (sq.rolling(3).max().shift(1) == 1) | (nr7.shift(1) == 1)
    g["rsi3"] = rsi(c, 3)
    g["ret21"] = c / c.shift(21) - 1
    g["ret63"] = c / c.shift(63) - 1
    g["prev_close"] = c.shift(1)
    g["pb_low"] = np.minimum(l, l.shift(1))
    if "delivery_pct" in g:
        g["deliv20"] = g.delivery_pct.rolling(20).mean().shift(1)
    return g


def _confirm(raw, n):
    out, cur, cand, cnt = [], None, None, 0
    for s in raw:
        if s is None or (isinstance(s, float) and np.isnan(s)):
            out.append(cur); continue
        if cur is None:
            cur = s
        elif s != cur:
            cnt = cnt + 1 if s == cand else 1
            cand = s
            if cnt >= n:
                cur, cand, cnt = s, None, 0
        else:
            cand, cnt = None, 0
        out.append(cur)
    return out


def market_state(idx, px):
    """idx: date, nifty, vix. Returns one row per date with the confirmed market state."""
    m = idx.sort_values("date").copy()
    m["nifty_sma50"] = m.nifty.rolling(50).mean()
    m["nifty_sma200"] = m.nifty.rolling(200).mean()
    m["nifty_ret63"] = m.nifty / m.nifty.shift(63) - 1
    m["vix_median"] = m.vix.rolling(VIX_LOOKBACK, min_periods=120).median()
    up = m.nifty > m.nifty_sma200
    calm = m.vix <= m.vix_median          # missing VIX -> not calm (defensive)
    raw = np.where(up, "up_", "down_") + np.where(calm, "calm", "volatile")
    raw = pd.Series(raw, index=m.index).where(m.nifty_sma200.notna())
    m["state_raw"] = raw
    m["market_state"] = _confirm(list(raw), STATE_CONFIRM_DAYS)
    breadth = px.assign(a=px.close > px.sma200).groupby("date").a.mean().rename("breadth_200")
    m = m.merge(breadth, on="date", how="left")
    return m


def build(px, idx, universe):
    """px: date,symbol,OHLCV(+delivery_pct). Returns (features, market)."""
    px = px.dropna(subset=["open", "high", "low", "close"])
    px = px[(px.volume > 0) & (px.open > 0)].copy()
    # explicit loop rather than groupby.apply: pandas 3 drops the grouping column inside apply
    px = pd.concat([per_symbol(g) for _, g in px.groupby("symbol", sort=False)], ignore_index=True)
    sec = universe.rename(columns={"Symbol": "symbol", "Industry": "sector"})[["symbol", "sector"]]
    px = px.merge(sec, on="symbol", how="left")
    px["sector"] = px.sector.fillna("Unknown")

    mkt = market_state(idx, px)
    px = px.merge(mkt[["date", "nifty_ret63", "market_state"]], on="date", how="left")
    px["rs63"] = px.ret63 - px.nifty_ret63
    px["rs_pct"] = px.groupby("date").rs63.rank(pct=True)
    px["sector_rs_pct"] = px.groupby(["date", "sector"]).rs63.rank(pct=True)

    # sector rotation ranks (sectors with at least 3 stocks)
    s = px.groupby(["date", "sector"]).agg(sector_ret21=("ret21", "mean"), n=("ret21", "count")).reset_index()
    s = s[(s.n >= 3) & (s.sector != "Unknown")]
    s["sector_rank"] = s.groupby("date").sector_ret21.rank(ascending=False, method="first")
    px = px.merge(s[["date", "sector", "sector_rank"]], on=["date", "sector"], how="left")
    px["sector_top5"] = px.sector_rank <= 5
    px["rank_in_sector"] = px.groupby(["date", "sector"]).ret21.rank(ascending=False, method="first")
    return px.sort_values(["symbol", "date"]).reset_index(drop=True), mkt
