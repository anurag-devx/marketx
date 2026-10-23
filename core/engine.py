"""Trade simulation shared by the backtest (history) and validation (live tracking)."""
import numpy as np, pandas as pd
from .config import COST_RT
from .strategies import STRATEGIES, levels


def _exit_hit(rule, d, c, cols):
    if rule == "revert_sma5":
        return c[d] > cols["sma5"][d]
    if rule == "trail_sma10":
        return c[d] < cols["sma10"][d]
    if rule.startswith("flag_off:"):
        v = cols[rule.split(":", 1)[1]][d]
        return not (v is True or v == 1)
    raise ValueError(rule)


def simulate(px, ids=None, keep_path=True):
    ids = ids or list(STRATEGIES)
    need = {"sma5", "sma10", "sector_top5"}
    trades = []
    for sym, g in px.groupby("symbol", sort=False):
        g = g.sort_values("date").reset_index(drop=True)
        o, h, l, c = (g[k].to_numpy(float) for k in ["open", "high", "low", "close"])
        dates = g.date.to_numpy()
        cols = {k: g[k].to_numpy() for k in need if k in g}
        n = len(g)
        for sid in ids:
            s = STRATEGIES[sid]
            busy_until = -1
            for t in np.flatnonzero(g[f"sig_{sid}"].to_numpy()):
                e = t + 1
                if e >= n or e <= busy_until:
                    continue
                row = g.iloc[t]
                entry = o[e]
                stop, target = levels(sid, entry, row)
                if not np.isfinite(stop) or stop >= entry:
                    continue
                exit_px = reason = x = None
                last = min(e + s["max_days"], n) - 1
                for d in range(e, last + 1):
                    if l[d] <= stop:
                        exit_px, reason, x = (min(o[d], stop) if d > e else stop), "stop", d
                        break
                    if h[d] >= target:
                        exit_px, reason, x = (max(o[d], target) if d > e else target), "target", d
                        break
                    hit = next((r for r in s["exits"] if _exit_hit(r, d, c, cols)), None)
                    if hit:
                        exit_px, reason, x = c[d], hit.split(":")[0], d
                        break
                if exit_px is None:
                    if last < e + s["max_days"] - 1:
                        continue          # still open at end of data
                    exit_px, reason, x = c[last], "time", last
                rec = dict(strategy=sid, symbol=sym, sector=row.get("sector"), signal_date=dates[t],
                           entry_date=dates[e], exit_date=dates[x], entry=entry, stop=stop,
                           target=target if np.isfinite(target) else np.nan, exit=exit_px, reason=reason,
                           days=x - e + 1, ret=exit_px / entry - 1 - COST_RT,
                           state=row.get("market_state"), strength=row[f"str_{sid}"])
                if keep_path:
                    p = c[e:x + 1] / entry
                    p[-1] = exit_px / entry
                    rec["_dates"], rec["_path"] = dates[e:x + 1], p
                trades.append(rec)
                busy_until = x
    return pd.DataFrame(trades)


def trade_stats(t):
    if len(t) == 0:
        return pd.Series(dtype=float)
    w, lo = t.ret[t.ret > 0], t.ret[t.ret <= 0]
    weeks = max((pd.Timestamp(t.entry_date.max()) - pd.Timestamp(t.entry_date.min())).days / 7, 1)
    return pd.Series({
        "trades": len(t), "signals/week": len(t) / weeks, "win %": 100 * len(w) / len(t),
        "avg win %": 100 * w.mean() if len(w) else 0.0, "avg loss %": 100 * lo.mean() if len(lo) else 0.0,
        "payoff": w.mean() / -lo.mean() if len(w) and len(lo) and lo.mean() < 0 else np.nan,
        "expectancy %": 100 * t.ret.mean(),
        "profit factor": w.sum() / -lo.sum() if len(lo) and lo.sum() < 0 else np.nan,
        "avg days": t.days.mean(),
    })


def portfolio(tr, all_dates, slots=10, capital=1_000_000):
    """Equal-weight, daily mark-to-market; same-day candidates ranked by strength."""
    tr = tr.sort_values(["entry_date", "strength"], ascending=[True, False])
    by_day = {d: grp for d, grp in tr.groupby("entry_date")}
    cash = equity = capital
    open_pos, held, curve = [], set(), []
    for d in all_dates:
        for _, r in by_day.get(d, pd.DataFrame()).iterrows():
            if len(open_pos) >= slots or r.symbol in held:
                continue
            alloc = min(equity / slots, cash)
            if alloc <= 0:
                break
            cash -= alloc
            open_pos.append(dict(alloc=alloc, m=dict(zip(r._dates, r._path)), exit=r.exit_date, sym=r.symbol, last=1.0))
            held.add(r.symbol)
        value, still = 0.0, []
        for p in open_pos:
            p["last"] = p["m"].get(d, p["last"])
            if d == p["exit"]:
                cash += p["alloc"] * (p["last"] - COST_RT)   # exit value less round-trip cost
                held.discard(p["sym"])
            else:
                value += p["alloc"] * p["last"]
                still.append(p)
        open_pos = still
        equity = cash + value
        curve.append((d, equity, len(open_pos)))
    return pd.DataFrame(curve, columns=["date", "equity", "positions"]).set_index("date")


def portfolio_stats(eq):
    r = eq.equity.pct_change().dropna()
    yrs = max((pd.Timestamp(eq.index[-1]) - pd.Timestamp(eq.index[0])).days / 365.25, 1e-9)
    dd = eq.equity / eq.equity.cummax() - 1
    cagr = (eq.equity.iat[-1] / eq.equity.iat[0]) ** (1 / yrs) - 1
    downside = r[r < 0].std()
    return pd.Series({
        "CAGR %": 100 * cagr, "avg daily return %": 100 * r.mean(), "max drawdown %": 100 * dd.min(),
        "Calmar": cagr / -dd.min() if dd.min() < 0 else np.nan,
        "Sharpe": np.sqrt(252) * r.mean() / r.std() if r.std() else np.nan,
        "Sortino": np.sqrt(252) * r.mean() / downside if downside else np.nan,
        "avg positions": eq.positions.mean(),
    })
