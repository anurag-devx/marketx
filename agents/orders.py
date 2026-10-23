"""Order list for portfolio (momentum) strategies: turns target weights into shares to buy and sell.

The rule rebalances at the first open of each month. For the money you allocate to the strategy:
    target shares = floor(allocation x weight / last close)
    order         = target shares - shares you hold for this strategy (positions.csv, strategy column)
Small top-ups and trims (within BAND of the target) are skipped to save costs; names that enter or leave
are always traded. Places no orders: you place them with your broker."""
import json, math
import numpy as np, pandas as pd
from core.config import ACCOUNT_JSON
from core import atomic

BAND = 0.20


def allocation(sid):
    if not ACCOUNT_JSON.exists():
        return 0.0
    a = json.loads(ACCOUNT_JSON.read_text())
    return float(a.get("allocations", {}).get(sid, 0) or 0)


def set_allocation(sid, amount):
    a = json.loads(ACCOUNT_JSON.read_text()) if ACCOUNT_JSON.exists() else {}
    a.setdefault("allocations", {})[sid] = float(amount)
    atomic.write_text(ACCOUNT_JSON, json.dumps(a))


def next_rebalance(as_of):
    """First weekday of the next month (NSE holidays not known here: if it is one, the next trading day)."""
    d = (pd.Timestamp(as_of).to_period("M") + 1).to_timestamp()
    while d.weekday() >= 5:
        d += pd.Timedelta(days=1)
    return d


def rebalance_window(as_of, trading_days):
    """True during the first 3 trading days of the month: this month's orders are due now."""
    t = pd.Timestamp(as_of)
    month_days = [d for d in trading_days if pd.Timestamp(d).to_period("M") == t.to_period("M")]
    return t in [pd.Timestamp(d) for d in sorted(month_days)[:3]]


def orders(targets, last_price, positions, sid, capital, kind="hold"):
    """targets: portfolio_targets rows for one date. kind: 'hold' (this month) or 'next (provisional)'.
    Returns one row per stock with current, target and order quantities."""
    t = targets[(targets.strategy == sid) & (targets.portfolio == kind)]
    held = positions[positions.strategy == sid].groupby("symbol").quantity.sum() if len(positions) else pd.Series(dtype=float)
    syms = sorted(set(t.symbol) | set(held.index))
    w = t.set_index("symbol").weight_pct / 100 if len(t) else pd.Series(dtype=float)
    if kind != "hold" and len(w):
        w = w / w.sum()          # provisional list: equal weight across its names
    rows = []
    for s in syms:
        px = float(last_price.get(s, np.nan))
        cur = int(held.get(s, 0))
        tw = float(w.get(s, 0.0))
        tgt = math.floor(capital * tw / px) if px > 0 and tw > 0 else 0
        diff = tgt - cur
        if cur == 0 and tgt > 0:
            act, why = "BUY", "new in the portfolio"
        elif tgt == 0 and cur > 0:
            act, why = "SELL", "left the portfolio"
        elif diff == 0 or (tgt > 0 and abs(diff) <= BAND * tgt):
            act, why, diff = "HOLD", "within 20% of target" if diff else "at target", 0
        elif diff > 0:
            act, why = "ADD", "below target weight"
        elif tgt == 0 and cur == 0:
            act, why = "SKIP", "allocation too small for one share"
        else:
            act, why = "TRIM", "above target weight"
        if tw > 0 and tgt == 0 and cur == 0:
            act, why = "SKIP", "allocation too small for one share"
        rows.append(dict(strategy=sid, symbol=s, action=act, held_qty=cur, target_qty=tgt, order_qty=diff,
                         last_price=round(px, 2) if px == px else None, order_value=round(diff * px, 0) if px == px else None,
                         target_weight_pct=round(100 * tw, 2), reason=why))
    out = pd.DataFrame(rows)
    if len(out):
        order = {"SELL": 0, "TRIM": 1, "BUY": 2, "ADD": 3, "HOLD": 4, "SKIP": 5}
        out = out.sort_values(["action", "symbol"], key=lambda c: c.map(order) if c.name == "action" else c).reset_index(drop=True)
    return out


def summary(o, capital):
    if o is None or o.empty:
        return dict(buys=0, sells=0, buy_value=0, sell_value=0, invested_after=0, cash_after=capital)
    after = (o.target_qty.where(o.action != "HOLD", o.held_qty) * o.last_price.fillna(0)).sum()
    return dict(buys=int(o.action.isin(["BUY", "ADD"]).sum()), sells=int(o.action.isin(["SELL", "TRIM"]).sum()),
                buy_value=float(o[o.order_qty > 0].order_value.sum()), sell_value=float(-o[o.order_qty < 0].order_value.sum()),
                invested_after=float(after), cash_after=float(capital - after))
