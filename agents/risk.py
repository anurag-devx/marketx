"""Agent 5 - Risk. Deterministic: no LLM. Its veto is final.
Sizes each candidate so hitting the stop loses risk_per_trade of equity (x state multiplier) and
enforces portfolio limits."""
import json, math
import pandas as pd
from core.config import RISK, POSITIONS_CSV, ACCOUNT_JSON

POS_COLS = ["symbol", "sector", "strategy", "entry_date", "entry_price", "quantity", "stop", "target"]


def load_positions():
    if POSITIONS_CSV.exists():
        p = pd.read_csv(POSITIONS_CSV)
        if len(p):
            p["entry_date"] = pd.to_datetime(p.entry_date)
            return p
    return pd.DataFrame(columns=POS_COLS)


def load_account():
    a = dict(capital=1_000_000, equity=None, peak_equity=None, today_pnl=0.0)
    if ACCOUNT_JSON.exists():
        a.update(json.loads(ACCOUNT_JSON.read_text()))
    a["equity"] = a["equity"] or a["capital"]
    a["peak_equity"] = max(a["peak_equity"] or a["equity"], a["equity"])
    return a


def run(cands, regime, positions, account):
    eq = float(account["equity"])
    dd = 1 - eq / float(account["peak_equity"])
    halt = None
    if dd >= RISK["dd_stop"]:
        halt = f"drawdown {dd:.1%} >= {RISK['dd_stop']:.0%}: new trades stopped, review needed"
    elif -float(account["today_pnl"]) >= RISK["daily_loss_cap"] * eq:
        halt = "daily loss cap reached"
    elif regime["size_multiplier"] <= 0:
        halt = "regime allows no new trades"
    mult = regime["size_multiplier"] * (0.5 if dd >= RISK["dd_half"] else 1.0)

    invested = float((positions.entry_price * positions.quantity).sum()) if len(positions) else 0.0
    cash = eq - invested
    slots = RISK["max_positions"] - len(positions)
    held = set(positions.symbol)
    per_sector = positions.sector.value_counts().to_dict() if len(positions) else {}

    out = []
    for _, c in cands.iterrows():
        qty, reason = 0, None
        if halt:
            reason = halt
        elif c.symbol in held:
            reason = "already held"
        elif slots <= 0:
            reason = "no free position slots"
        elif per_sector.get(c.sector, 0) >= RISK["max_per_sector"]:
            reason = f"sector limit ({RISK['max_per_sector']}) reached"
        else:
            per_share = c.entry - c.stop
            qty = math.floor(eq * RISK["risk_per_trade"] * mult / per_share)
            qty = min(qty, math.floor(eq * RISK["max_position_pct"] / c.entry))
            if qty < 1:
                reason, qty = "stop too wide for the risk budget", 0
            else:
                qty = min(qty, math.floor(cash / c.entry))
                if qty < 1:
                    reason, qty = "not enough cash", 0
        approved = reason is None
        if approved:
            slots -= 1
            cash -= qty * c.entry
            held.add(c.symbol)
            per_sector[c.sector] = per_sector.get(c.sector, 0) + 1
        out.append({**c.to_dict(), "approved": approved, "quantity": qty,
                    "value": round(qty * c.entry, 0), "risk_rs": round(qty * (c.entry - c.stop), 0),
                    "size_multiplier": mult, "reject_reason": reason})
    return pd.DataFrame(out), dict(drawdown=dd, multiplier=mult, cash_after=cash, halt=halt)


def exits_due(positions, feat, today):
    """Checks held positions against the latest bar: stop, target, or time limit."""
    from core.strategies import STRATEGIES
    if positions.empty:
        return pd.DataFrame()
    last = feat[feat.date == today].set_index("symbol")
    days = sorted(feat.date.unique())
    rows = []
    for _, p in positions.iterrows():
        if p.symbol not in last.index:
            rows.append(dict(symbol=p.symbol, action="check manually", detail="no price today")); continue
        r = last.loc[p.symbol]
        held = sum(1 for d in days if pd.Timestamp(d) >= p.entry_date)
        s = STRATEGIES.get(p.strategy, {})
        if str(p.strategy).startswith("MOM"):
            rows.append(dict(symbol=p.symbol, strategy=p.strategy, days_held=held, action="hold",
                             detail="portfolio strategy: changes only at the monthly rebalance (see Orders)"))
            continue
        action, detail = "hold", f"close {r.close:.2f}, stop {p.stop}"
        if r.low <= p.stop:
            action, detail = "EXIT", f"stop {p.stop} hit (low {r.low:.2f})"
        elif pd.notna(p.get("target")) and r.high >= p.target:
            action, detail = "EXIT", f"target {p.target} hit (high {r.high:.2f})"
        elif "revert_sma5" in s.get("exits", []) and r.close > r.sma5:
            action, detail = "EXIT at open", "closed above 5-day average"
        elif "trail_sma10" in s.get("exits", []) and r.close < r.sma10:
            action, detail = "EXIT at open", "closed below 10-day average"
        elif "flag_off:sector_top5" in s.get("exits", []) and not bool(r.sector_top5):
            action, detail = "EXIT at open", "sector left the top 5"
        elif held >= s.get("max_days", 10):
            action, detail = "EXIT at open", f"time limit ({held} days)"
        rows.append(dict(symbol=p.symbol, strategy=p.strategy, days_held=held, action=action, detail=detail))
    return pd.DataFrame(rows)
