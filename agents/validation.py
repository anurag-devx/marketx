"""Agent 6 - Validation, v0: paper-tracks every approved recommendation to its exit using the
same rules as the backtest, and compares live results with the strategy's backtest card.
Later versions add attribution per agent, weekly health rules and change proposals."""
import numpy as np, pandas as pd
from core.config import COST_RT
from core.strategies import STRATEGIES
from core.engine import _exit_hit


def track(feat, decisions):
    appr = decisions[decisions.approved].drop_duplicates(["date", "symbol"])
    if appr.empty:
        return pd.DataFrame()
    need = ["date", "open", "high", "low", "close", "sma5", "sma10", "sector_top5"]
    by_sym = {s: g[need].sort_values("date").reset_index(drop=True)
              for s, g in feat[feat.symbol.isin(appr.symbol)].groupby("symbol")}
    rows = []
    for _, d in appr.iterrows():
        g = by_sym.get(d.symbol)
        s = STRATEGIES.get(d.strategy)
        base = dict(decision_date=d.date, symbol=d.symbol, strategy=d.strategy, planned_entry=d.entry,
                    stop=d.stop, target=d.target, quantity=d.quantity, setup_score=d.setup_score)
        if g is None or s is None:
            rows.append({**base, "status": "no data"}); continue
        fut = g[g.date > d.date].reset_index(drop=True)
        if fut.empty:
            rows.append({**base, "status": "pending entry"}); continue
        o, h, l, c = (fut[k].to_numpy(float) for k in ["open", "high", "low", "close"])
        cols = {k: fut[k].to_numpy() for k in ["sma5", "sma10", "sector_top5"]}
        entry, stop = o[0], float(d.stop)
        target = float(d.target) if pd.notna(d.target) else np.inf
        gap_skip = abs(entry - d.entry) > 1.0 * (d.atr if pd.notna(d.get("atr")) else np.inf)
        if gap_skip or entry <= stop:
            rows.append({**base, "entry_date": fut.date[0], "entry": entry, "status": "skipped (gap)"}); continue
        exit_px = reason = x = None
        last = min(s["max_days"], len(fut)) - 1
        for i in range(0, last + 1):
            if l[i] <= stop:
                exit_px, reason, x = (min(o[i], stop) if i > 0 else stop), "stop", i; break
            if h[i] >= target:
                exit_px, reason, x = (max(o[i], target) if i > 0 else target), "target", i; break
            hit = next((r for r in s["exits"] if _exit_hit(r, i, c, cols)), None)
            if hit:
                exit_px, reason, x = c[i], hit.split(":")[0], i; break
        if exit_px is None and last == s["max_days"] - 1:
            exit_px, reason, x = c[last], "time", last
        if exit_px is None:
            rows.append({**base, "entry_date": fut.date[0], "entry": entry, "status": "open",
                         "last_close": c[-1], "open_ret_pct": round(100 * (c[-1] / entry - 1), 2),
                         "days": len(fut)}); continue
        rows.append({**base, "entry_date": fut.date[0], "entry": entry, "exit_date": fut.date[x], "exit": exit_px,
                     "exit_reason": reason, "days": x + 1, "status": "closed",
                     "ret_pct": round(100 * (exit_px / entry - 1 - COST_RT), 3)})
    return pd.DataFrame(rows)


def summary(out, cards):
    """Live (paper) results per strategy next to its backtest expectancy."""
    closed = out[out.status == "closed"] if len(out) else out
    if closed is None or closed.empty:
        return pd.DataFrame()
    s = closed.groupby("strategy").agg(trades=("ret_pct", "size"), win_pct=("ret_pct", lambda r: 100 * (r > 0).mean()),
                                       live_exp_pct=("ret_pct", "mean")).round(2)
    if cards is not None and len(cards):
        bt = cards[cards.active].groupby("strategy").apply(
            lambda g: np.average(g.expectancy_pct, weights=g.trades)).rename("backtest_exp_pct").round(2)
        s = s.join(bt)
    return s.reset_index()
