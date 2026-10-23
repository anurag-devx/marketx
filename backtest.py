"""Extended backtest: every price-based strategy in the library, split by market state and research period.
    python3 backtest.py
Writes results/report.md, results/trades.csv, results/strategy_cards.csv and the strategy_cards table
(used by the regime agent to choose the playbook and by the technical agent to score signals)."""
import numpy as np, pandas as pd
from core import config as C
from core.data import load_raw
from core.indicators import build
from core.strategies import STRATEGIES, signals
from core.engine import simulate, trade_stats, portfolio, portfolio_stats
from core import db
from core.runlog import agent_run
import uuid


def period(d):
    d = pd.Timestamp(d)
    if d < pd.Timestamp(C.DESIGN[0]):
        return "warmup"
    if d <= pd.Timestamp(C.DESIGN[1]):
        return "design"
    if d <= pd.Timestamp(C.VALIDATION[1]):
        return "validation"
    return "holdout"


def control(feat, hold=7):
    """Random-entry control: buy any liquid stock at the next open, sell at the close `hold` days later.
    Its average return per (state, period) is what a strategy must beat."""
    f = feat[["symbol", "date", "open", "close", "turnover_cr", "market_state"]].sort_values(["symbol", "date"]).copy()
    g = f.groupby("symbol")
    f["fwd"] = g.close.shift(-hold) / g.open.shift(-1) - 1 - C.COST_RT
    f = f[(f.turnover_cr >= C.MIN_TURNOVER_CR) & f.fwd.notna() & f.market_state.notna()]
    f["period"] = f.date.map(period)
    return f.groupby(["market_state", "period"]).fwd.mean()


def t_stat(x, weeks):
    """t-stat of mean excess return with trades clustered by entry week (trades in the same week move together)."""
    w = pd.Series(x.values, index=weeks.values).groupby(level=0).mean()
    if len(w) < 10 or w.std() == 0:
        return np.nan
    return w.mean() / (w.std() / np.sqrt(len(w)))


def cards(tr, ctrl):
    """Per strategy x state, on design+validation only. Holdout is reported, never used to select.
    Active = enough trades, beats the random-entry control in BOTH design and validation, and t-stat >= 2."""
    tr = tr.copy()
    tr["ctrl"] = [ctrl.get((s, p), np.nan) for s, p in zip(tr.state, tr.period)]
    tr["excess"] = tr.ret - tr.ctrl
    tr["week"] = pd.to_datetime(tr.entry_date).dt.to_period("W").astype(str)
    sel = tr[tr.period.isin(["design", "validation"])]
    rows = []
    for (sid, st), g in sel.groupby(["strategy", "state"]):
        s = trade_stats(g)
        edge_d = g[g.period == "design"].excess.mean()
        edge_v = g[g.period == "validation"].excess.mean()
        t = t_stat(g.excess, g.week)
        hold = tr[(tr.strategy == sid) & (tr.state == st) & (tr.period == "holdout")]
        active = bool(s["trades"] >= C.MIN_TRADES_STATE and edge_d > 0 and edge_v > 0
                      and pd.notna(t) and t >= C.MIN_T_STAT)
        f = lambda v: None if pd.isna(v) else round(100 * v, 3)
        rows.append(dict(strategy=sid, name=STRATEGIES[sid]["name"], state=st, trades=int(s["trades"]),
                         win_pct=s["win %"], avg_win_pct=s["avg win %"], avg_loss_pct=s["avg loss %"],
                         payoff=s["payoff"], expectancy_pct=s["expectancy %"],
                         control_pct=f(g.ctrl.mean()), edge_pct=f(g.excess.mean()),
                         design_edge_pct=f(edge_d), validation_edge_pct=f(edge_v), t_stat=None if pd.isna(t) else round(t, 2),
                         holdout_trades=len(hold), holdout_exp_pct=f(hold.ret.mean()), holdout_edge_pct=f(hold.excess.mean()),
                         active=active, version=C.VERSION, built_at=pd.Timestamp.now().floor("s")))
    return pd.DataFrame(rows)


def main():
    C.RESULTS.mkdir(exist_ok=True)
    px, idx, uni = load_raw()
    print("Building features...")
    feat, mkt = build(px, idx, uni)
    feat = signals(feat)
    print("Simulating trades...")
    tr = simulate(feat)
    if tr.empty:
        print("No trades generated."); return
    tr["period"] = tr.signal_date.map(period)
    tr = tr[tr.period != "warmup"].dropna(subset=["state"])
    all_dates = np.sort(feat.date[feat.date >= pd.Timestamp(C.DESIGN[0])].unique())

    pct = lambda x: (100 * x).round(2)
    out = [f"# Extended backtest (v{C.VERSION})\n",
           f"Universe: {feat.symbol.nunique()} stocks | {pd.Timestamp(all_dates[0]).date()} to {pd.Timestamp(all_dates[-1]).date()} | "
           f"costs {C.COST_RT*100:.1f}% round trip",
           f"Periods: design {C.DESIGN[0]}..{C.DESIGN[1]}, validation ..{C.VALIDATION[1]}, holdout {C.HOLDOUT_FROM}+ (not used to select)\n"]
    if "delivery_pct" not in px:
        out.append("Note: no delivery % data, so S4 runs without its delivery filter.\n")

    names = {k: f"{k} {v['name']}" for k, v in STRATEGIES.items()}
    out += ["## 1. Per-trade results by strategy (all periods)\n",
            tr.groupby("strategy").apply(trade_stats).rename(index=names).round(2).to_markdown(), ""]

    exp = tr.pivot_table(index="strategy", columns="state", values="ret", aggfunc="mean").reindex(columns=C.STATES)
    cnt = tr.pivot_table(index="strategy", columns="state", values="ret", aggfunc="count").reindex(columns=C.STATES)
    cell = pct(exp).astype(str) + " (" + cnt.fillna(0).astype(int).astype(str) + ")"
    out += ["## 2. Expectancy % by market state (trades)\n", cell.rename(index=names).to_markdown(), ""]

    per = tr.pivot_table(index="strategy", columns="period", values="ret", aggfunc="mean")
    per = per.reindex(columns=[c for c in ["design", "validation", "holdout"] if c in per])
    out += ["## 3. Expectancy % by research period\n", pct(per).rename(index=names).to_markdown(), ""]

    state_days = mkt[mkt.date >= pd.Timestamp(C.DESIGN[0])].market_state.value_counts()
    out += ["## 4. Days in each market state\n", state_days.reindex(C.STATES).to_frame("days").to_markdown(), ""]

    ctrl = control(feat)
    cd = cards(tr, ctrl)
    edge = cd.pivot_table(index="strategy", columns="state", values="edge_pct").reindex(columns=C.STATES)
    tst = cd.pivot_table(index="strategy", columns="state", values="t_stat").reindex(columns=C.STATES)
    out += ["## 5. Edge % over random-entry control, design + validation (t-stat)\n",
            (edge.round(2).astype(str) + " (t " + tst.round(1).astype(str) + ")").rename(index=names).to_markdown(), ""]
    db.write("strategy_cards", cd, mode="replace")
    cd.to_csv(C.RESULTS / "strategy_cards.csv", index=False)
    play = cd[cd.active].groupby("state").strategy.apply(lambda s: ", ".join(sorted(s))).reindex(C.STATES).fillna("none (stay in cash)")
    out += ["## 6. Playbook selected from design + validation (needs "
            f"{C.MIN_TRADES_STATE}+ trades, positive edge over control in both periods, t-stat >= {C.MIN_T_STAT})\n",
            play.to_frame("active strategies").to_markdown(), ""]

    out.append("## 7. Portfolio simulation (10 slots, equal weight, daily mark-to-market)\n")
    rows, curves = {}, {}
    for sid in list(STRATEGIES) + ["PLAYBOOK"]:
        if sid == "PLAYBOOK":
            act = set(zip(cd[cd.active].strategy, cd[cd.active].state))
            sub = tr[[(a, b) in act for a, b in zip(tr.strategy, tr.state)]]
        else:
            sub = tr[tr.strategy == sid]
        if sub.empty:
            continue
        eq = portfolio(sub, all_dates)
        rows[names.get(sid, sid)] = portfolio_stats(eq)
        curves[sid] = eq.equity
        if sid == "PLAYBOOK":
            hold = eq[eq.index >= pd.Timestamp(C.HOLDOUT_FROM)]
            if len(hold) > 20:
                rows["PLAYBOOK, holdout only"] = portfolio_stats(hold)
    out.append(pd.DataFrame(rows).T.round(3).to_markdown())
    out += ["", "Reference: 1% average daily return is roughly 1,100% CAGR.",
            "PLAYBOOK uses only strategy/state pairs chosen on design + validation data; its holdout line is the honest out-of-sample check.",
            "Caveat: universe = today's Nifty 500, so delisted and dropped stocks are missing (results flattered)."]

    (C.RESULTS / "report.md").write_text("\n".join(out))
    tr.drop(columns=["_dates", "_path"]).to_csv(C.RESULTS / "trades.csv", index=False)
    pd.DataFrame(curves).to_csv(C.RESULTS / "equity.csv")
    print("\n".join(out))


if __name__ == "__main__":
    with agent_run(uuid.uuid4().hex[:8], "backtest") as rec:
        main()
        rec["message"] = f"report at results/report.md"
