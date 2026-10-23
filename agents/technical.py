"""Agent 4 - Technical. Deterministic: no LLM.
Scans the latest close with today's active strategies and scores each signal."""
import numpy as np, pandas as pd
from core.config import SCORE_W
from core.strategies import STRATEGIES, signals, levels


def run(feat, regime, cards):
    ids = [s for s in regime["active_strategies"].split(",") if s]
    if not ids:
        return pd.DataFrame()
    d = feat[feat.date == regime["date"]].copy()
    d = signals(d, ids)
    card = cards[cards.state == regime["market_state"]].set_index("strategy")
    rows = []
    for sid in ids:
        c = card.loc[sid]
        for _, r in d[d[f"sig_{sid}"]].iterrows():
            entry = float(r.close)              # provisional: actual entry is tomorrow's open
            stop, target = levels(sid, entry, r)
            if not np.isfinite(stop) or stop >= entry:
                continue
            rr = (target - entry) / (entry - stop) if np.isfinite(target) else (c.payoff if pd.notna(c.payoff) else 1.0)
            score = 100 * (SCORE_W["expectancy"] * np.clip(c.expectancy_pct / 2.0, 0, 1)
                           + SCORE_W["strength"] * float(r[f"str_{sid}"])
                           + SCORE_W["rr"] * np.clip(rr / 3.0, 0, 1))
            rows.append(dict(date=regime["date"], symbol=r.symbol, sector=r.sector, strategy=sid,
                             strategy_name=STRATEGIES[sid]["name"], entry=round(entry, 2), stop=round(stop, 2),
                             target=round(target, 2) if np.isfinite(target) else None,
                             exit_rule=", ".join(STRATEGIES[sid]["exits"]) or "target",
                             max_days=STRATEGIES[sid]["max_days"], atr=round(float(r.atr), 2),
                             turnover_cr=round(float(r.turnover_cr), 1), reward_risk=round(float(rr), 2),
                             tested_expectancy_pct=round(float(c.expectancy_pct), 2),
                             setup_score=round(float(score), 1)))
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values("setup_score", ascending=False).drop_duplicates("symbol").reset_index(drop=True)


def all_signals(feat, regime):
    """Every signal on the latest bar from every strategy in the library, with whether the strategy
    is switched on today. The decision trail (risk outcome) is joined later from `decisions`."""
    active = set(s for s in (regime.get("active_strategies") or "").split(",") if s)
    d = feat[feat.date == regime["date"]].copy()
    d = signals(d)
    rows = []
    for sid in STRATEGIES:
        for _, r in d[d[f"sig_{sid}"]].iterrows():
            entry = float(r.close)
            stop, target = levels(sid, entry, r)
            rows.append(dict(date=regime["date"], symbol=r.symbol, sector=r.sector, strategy=sid,
                             strategy_name=STRATEGIES[sid]["name"], close=round(entry, 2),
                             stop=round(stop, 2) if np.isfinite(stop) else None,
                             target=round(target, 2) if np.isfinite(target) else None,
                             strength=round(float(r[f"str_{sid}"]), 3), rs_pct=round(float(r.rs_pct), 3) if pd.notna(r.rs_pct) else None,
                             turnover_cr=round(float(r.turnover_cr), 1), strategy_active=sid in active,
                             market_state=regime["market_state"]))
    return pd.DataFrame(rows)
