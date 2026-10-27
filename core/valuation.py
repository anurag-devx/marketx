"""Point-in-time valuation and quality ratios, computed by MarketX from the NSE XBRL results (data/fundamentals/
quarters.parquet), never copied from a website: each number is usable only from the moment its filing was public.

From each filing (known from its filing time):
  ttm_eps, ttm_profit   last 4 consecutive quarters (needs results history: 'Quarterly results history' job)
  equity, debt, shares  the latest balance sheet (half-yearly; 'balance' job) and paid-up capital / face value
  growth                profit and revenue vs the same quarter a year earlier (as filed)
With the day's close:   P/E, earnings yield, P/B, ROE, debt / equity.
Missing pieces stay empty (never guessed)."""
import numpy as np, pandas as pd
from .config import DATA

QUARTERS = DATA / "fundamentals" / "quarters.parquet"


def _q():
    if not QUARTERS.exists():
        return pd.DataFrame()
    cols = ["symbol", "period_end", "filed_at", "status", "eps", "profit_owners", "profit", "equity_owners", "equity", "paid_up", "share_capital",
            "face_value", "borrowings_current", "borrowings_noncurrent", "revenue_yoy", "profit_yoy"]
    q = pd.read_parquet(QUARTERS)
    return q[[c for c in cols if c in q]]


def filings(q=None):
    """One row per (symbol, filing): what was known after that filing."""
    q = _q() if q is None else q
    if q.empty:
        return pd.DataFrame()
    q = q[q.get("status", "ok") == "ok"].dropna(subset=["filed_at", "period_end"]).copy()
    q["filed_at"], q["period_end"] = pd.to_datetime(q.filed_at), pd.to_datetime(q.period_end)
    q["v_prof"] = q.profit_owners.fillna(q.profit) if "profit" in q else q.profit_owners
    q["v_eq"] = q.equity_owners.fillna(q.equity) if "equity" in q else q.equity_owners
    q["v_cap"] = q.paid_up.fillna(q.share_capital) if "share_capital" in q else q.paid_up
    out = []
    for sym, g in q.sort_values("filed_at").groupby("symbol"):
        g = g.drop_duplicates("period_end", keep="last").sort_values("period_end")
        for i in range(len(g)):
            known = g.iloc[: i + 1]
            last = known.iloc[-1]
            t4 = known.tail(4)
            gaps = t4.period_end.diff().dt.days.dropna()
            ok4 = len(t4) == 4 and gaps.between(80, 100).all() and t4.eps.notna().all()
            bs = known.dropna(subset=["v_eq"])
            b = bs.iloc[-1] if len(bs) else None
            shares = (b["v_cap"] / b["face_value"]) if b is not None and b["v_cap"] and b["face_value"] else np.nan
            debt = (np.nan_to_num(b["borrowings_current"]) + np.nan_to_num(b["borrowings_noncurrent"])) if b is not None else np.nan
            out.append(dict(symbol=sym, filed_at=last.filed_at, period_end=last.period_end,
                            ttm_eps=float(t4.eps.sum()) if ok4 else np.nan,
                            ttm_profit=float(t4["v_prof"].sum()) if ok4 and t4["v_prof"].notna().all() else np.nan,
                            equity=float(b["v_eq"]) if b is not None else np.nan, debt=float(debt) if b is not None else np.nan, shares=float(shares),
                            profit_yoy=last.get("profit_yoy"), revenue_yoy=last.get("revenue_yoy")))
    return pd.DataFrame(out)


def ratios(f, close):
    """f: a filings() row (dict-like); close: the day's price."""
    eps, eq, sh = f.get("ttm_eps"), f.get("equity"), f.get("shares")
    pe = close / eps if eps and np.isfinite(eps) and eps > 0 else np.nan
    bvps = eq / sh if eq and sh and np.isfinite(eq) and np.isfinite(sh) and sh > 0 else np.nan
    return dict(pe=pe, earnings_yield=(eps / close) if eps and np.isfinite(eps) and close else np.nan,
                pb=close / bvps if bvps and np.isfinite(bvps) and bvps > 0 else np.nan,
                roe=f.get("ttm_profit") / eq if eq and np.isfinite(eq) and eq > 0 and np.isfinite(f.get("ttm_profit") or np.nan) else np.nan,
                de=f.get("debt") / eq if eq and np.isfinite(eq) and eq > 0 and np.isfinite(f.get("debt") or np.nan) else np.nan,
                profit_yoy=f.get("profit_yoy"), revenue_yoy=f.get("revenue_yoy"))


def latest(symbol, close=None, as_of=None):
    """The stock's ratios from the last filing known at as_of (default now), with that day's close."""
    f = filings()
    if f.empty:
        return None
    f = f[f.symbol == symbol]
    if as_of is not None:
        f = f[f.filed_at <= pd.Timestamp(as_of) + pd.Timedelta(hours=21)]
    if f.empty:
        return None
    r = f.iloc[-1]
    if close is None:
        try:
            px = pd.read_parquet(DATA / "prices.parquet", columns=["date", "symbol", "close"])
            px = px[px.symbol == symbol]
            if as_of is not None:
                px = px[pd.to_datetime(px.date) <= pd.Timestamp(as_of)]
            close = float(px.sort_values("date").close.iat[-1]) if len(px) else None
        except Exception:
            close = None
    out = dict(filed_at=str(r.filed_at), period_end=str(r.period_end.date()), close=close, ttm_eps=r.ttm_eps, ttm_profit=r.ttm_profit,
               equity=r.equity, debt=r.debt, shares=r.shares)
    if close:
        out.update(ratios(r, close))
    return {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in out.items()}


def panel_columns(d, days, f=None):
    """Market Lab candidate features for every (symbol, date) row of d, from the last filing known by 21:00 that day:
    v_ey earnings yield, v_pb log P/B, v_roe ROE, v_de debt/equity (log1p), v_gr profit growth (clipped)."""
    from marketlab.features import decision_day
    f = filings() if f is None else f
    cols = ["v_ey", "v_pb", "v_roe", "v_de", "v_gr"]
    if f is None or f.empty:
        for c in cols:
            d[c] = np.nan
        return d
    f = f.copy()
    f["known"] = pd.to_datetime(decision_day(pd.to_datetime(f.filed_at), days)).astype("datetime64[ns]")
    f = f.dropna(subset=["known"]).sort_values("known")
    d = d.assign(date=d.date.astype("datetime64[ns]")).sort_values("date")
    m = pd.merge_asof(d[["symbol", "date", "close"]].reset_index(), f[["symbol", "known", "ttm_eps", "ttm_profit", "equity", "debt", "shares", "profit_yoy"]],
                      left_on="date", right_on="known", by="symbol", direction="backward").set_index("index")
    eq = m.equity.where(m.equity > 0)
    bvps = eq / m.shares.where(m.shares > 0)
    d.loc[m.index, "v_ey"] = (m.ttm_eps / m.close).clip(-0.5, 0.5)
    d.loc[m.index, "v_pb"] = np.log((m.close / bvps.where(bvps > 0)).clip(0.05, 100))
    d.loc[m.index, "v_roe"] = (m.ttm_profit / eq).clip(-1, 1)
    d.loc[m.index, "v_de"] = np.log1p((m.debt / eq).clip(0, 20))
    d.loc[m.index, "v_gr"] = pd.to_numeric(m.profit_yoy, errors="coerce").clip(-2, 2)
    return d
