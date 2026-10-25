"""Balance sheet, cash flow and valuation from NSE results filings, point in time (each figure counts from when it was filed).

balance_sheets()  one row per stock and half-year / year end (consolidated when filed, else standalone; first filing):
  debt = current + non-current borrowings      net_debt = debt - cash - other bank balances
  debt_to_equity = debt / equity (owners')     current_ratio = current assets / current liabilities
  fcf = operating cash flow - capex (PP&E + intangibles), over cf_months (6 or 12)
  shares = paid-up capital / face value
valuation(symbol, price, as_of)  latest balance sheet filed by `as_of`, trailing-12-month profit from the last 4 quarters
  filed by `as_of` (same basis), then: market cap = shares x price, P/E = market cap / TTM profit (blank if loss),
  P/B = market cap / owners' equity (blank if equity <= 0), ROE = TTM profit / average owners' equity (now and a year earlier).
Amounts in ₹ crore. Nothing is estimated: a missing input leaves the figure blank."""
import numpy as np, pandas as pd

CR = 1e7


def balance_sheets(res=None):
    from research.earnings import _first_filings
    if res is None:
        from core import fundamentals
        res = fundamentals.results()
    if res is None or res.empty or "assets" not in res:
        return pd.DataFrame()
    r = res[pd.to_numeric(res.assets, errors="coerce").notna() | pd.to_numeric(res.cfo, errors="coerce").notna()]
    b = _first_filings(r)
    if b.empty:
        return b
    n = lambda c: pd.to_numeric(b[c], errors="coerce")
    eq = n("equity_owners").where(n("equity_owners").notna(), n("equity"))
    debt = n("borrowings_current").fillna(0) + n("borrowings_noncurrent").fillna(0)
    debt = debt.where(n("borrowings_current").notna() | n("borrowings_noncurrent").notna())
    out = pd.DataFrame({
        "symbol": b.symbol, "period_end": b.period_end, "filed_at": b.filed_at, "basis": b.basis,
        "assets_cr": n("assets") / CR, "equity_cr": eq / CR, "debt_cr": debt / CR, "cash_cr": (n("cash").fillna(0) + n("bank_other").fillna(0)).where(n("cash").notna()) / CR,
        "current_ratio": n("current_assets") / n("current_liabilities").where(n("current_liabilities") > 0),
        "cfo_cr": n("cfo") / CR, "capex_cr": (n("capex").abs().fillna(0) + n("capex_intangible").abs().fillna(0)).where(n("capex").notna()) / CR,
        "cf_months": n("cf_months"), "shares": n("paid_up") / n("face_value").where(n("face_value") > 0)})
    out["net_debt_cr"] = out.debt_cr - out.cash_cr.fillna(0)
    out["debt_to_equity"] = out.debt_cr / out.equity_cr.where(out.equity_cr > 0)
    out["fcf_cr"] = out.cfo_cr - out.capex_cr
    return out.sort_values(["symbol", "period_end"]).reset_index(drop=True)


def valuation(symbol, price, as_of, bs=None, q=None):
    """Point-in-time valuation for one stock. bs: balance_sheets(); q: research.earnings.quarters()."""
    as_of = pd.Timestamp(as_of)
    if bs is None:
        bs = balance_sheets()
    if q is None:
        from research.earnings import quarters
        q = quarters()
    out = dict(symbol=symbol, as_of=str(as_of.date()), price=price)
    b = bs[(bs.symbol == symbol) & (pd.to_datetime(bs.filed_at) <= as_of)] if len(bs) else bs
    if b is None or b.empty:
        return dict(out, note="no balance sheet filed yet")
    last = b.iloc[-1]
    out.update({k: (None if pd.isna(last[k]) else float(last[k])) for k in ("equity_cr", "debt_cr", "cash_cr", "net_debt_cr", "debt_to_equity",
                                                                          "current_ratio", "cfo_cr", "capex_cr", "fcf_cr", "cf_months", "shares", "assets_cr")})
    out["balance_sheet_date"], out["basis"] = str(pd.Timestamp(last.period_end).date()), last.basis
    yr = b[pd.to_datetime(b.period_end) <= pd.Timestamp(last.period_end) - pd.Timedelta(days=330)]
    eq_prev = float(yr.iloc[-1].equity_cr) if len(yr) and pd.notna(yr.iloc[-1].equity_cr) else None
    qq = q[(q.symbol == symbol) & (pd.to_datetime(q.filed_at) <= as_of) & (q.basis == last.basis)].sort_values("period_end") if q is not None and len(q) else pd.DataFrame()
    ttm = None
    if len(qq) >= 4:
        l4 = qq.tail(4)
        span = (pd.Timestamp(l4.period_end.iat[-1]) - pd.Timestamp(l4.period_end.iat[0])).days
        if 250 <= span <= 300 and l4.profit_cr.notna().all():
            ttm = float(l4.profit_cr.sum())
    out["ttm_profit_cr"] = ttm
    mcap = out["shares"] * price / CR if out.get("shares") and price else None
    out["market_cap_cr"] = mcap
    out["pe"] = mcap / ttm if mcap and ttm and ttm > 0 else None
    out["pb"] = mcap / out["equity_cr"] if mcap and out.get("equity_cr") and out["equity_cr"] > 0 else None
    avg_eq = (out["equity_cr"] + eq_prev) / 2 if out.get("equity_cr") is not None and eq_prev is not None else out.get("equity_cr")
    out["roe_pct"] = 100 * ttm / avg_eq if ttm is not None and avg_eq and avg_eq > 0 else None
    return out
