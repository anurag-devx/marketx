"""Agent 7 - Strategy. Owns the strategy library: which strategies exist, their status, which run in
today's market state, and research into new ones. Deterministic code; it proposes, you approve.

Statuses: no_edge (tested, failed) · candidate (passed research, awaiting you) · paper (approved for
paper trading) · active (approved for real money) · paused · retired."""
import json, uuid
import numpy as np, pandas as pd
from core import config as C, db
from core.strategies import STRATEGIES
from research import momentum

RES = C.RESULTS / "strategy"
HALT_STATUSES = ("paused", "retired")


# ---------- registry ----------
def registry():
    r = db.read("select * from strategies")
    return r if r is not None else pd.DataFrame(columns=["strategy", "name", "family", "status", "since", "note", "version"])


def sync_registry(cards):
    """Adds any strategy missing from the registry; refreshes swing strategies' tested status
    (active / no_edge) from the latest backtest, without overriding paused or retired."""
    reg = registry()
    today = str(pd.Timestamp.now().date())
    for sid, s in STRATEGIES.items():
        edge = cards is not None and len(cards[(cards.strategy == sid) & cards.active]) > 0
        tested = "active" if edge else "no_edge"
        if sid not in set(reg.strategy):
            db.write("strategies", pd.DataFrame([dict(strategy=sid, name=s["name"], family="swing", status=tested,
                                                      since=today, note="from backtest", version=C.VERSION)]))
        else:
            cur = reg.loc[reg.strategy == sid, "status"].iat[0]
            if cur not in HALT_STATUSES and cur != tested:
                db.execute("update strategies set status = ?, since = ?, note = 'from backtest' where strategy = ?", [tested, today, sid])


def set_status(strategy, status, note):
    db.execute("update strategies set status = ?, since = ?, note = ? where strategy = ?",
               [status, str(pd.Timestamp.now().date()), note, strategy])


# ---------- proposals ----------
def propose(kind, strategy, title, rationale, evidence):
    p = db.read("select status, decided_at from proposals where strategy = ? and kind = ?", [strategy, kind])
    if p is not None and len(p):
        if (p.status.isin(["pending", "approved"])).any():
            return None                       # already waiting, or already done
        last_no = pd.to_datetime(p[p.status == "rejected"].decided_at).max()
        if pd.notna(last_no) and (pd.Timestamp.now() - last_no).days < 30:
            return None                       # you said no recently: don't ask again for 30 days
    pid = uuid.uuid4().hex[:8]
    db.write("proposals", pd.DataFrame([dict(id=pid, created=pd.Timestamp.now().floor("s"), agent="strategy", kind=kind,
                                             strategy=strategy, title=title, rationale=rationale,
                                             evidence=json.dumps(evidence, default=float), status="pending",
                                             decided_at=pd.NaT)]))
    return pid


def decide(pid, approve):
    p = db.read("select * from proposals where id = ?", [pid])
    if p is None or p.empty:
        raise ValueError("proposal not found")
    p = p.iloc[0]
    db.execute("update proposals set status = ?, decided_at = ? where id = ?",
               ["approved" if approve else "rejected", pd.Timestamp.now().floor("s"), pid])
    if approve:
        new = {"paper_trade": "paper", "go_live": "active", "retire": "retired", "resume": "active", "pause": "paused"}.get(p.kind)
        if new:
            set_status(p.strategy, new, f"approved proposal {pid}")


# ---------- research ----------
def research(px, idx):
    """Runs the momentum study and files a proposal if it passes. Returns the verdict."""
    RES.mkdir(parents=True, exist_ok=True)
    v, monthly, verdict = momentum.study(px, idx)
    v.to_csv(RES / "momentum_variants.csv", index=False)
    best = monthly[verdict["chosen"]]
    best.to_csv(RES / "momentum_best_monthly.csv", index=False)
    cv = v[v.variant == verdict["chosen"]].iloc[0]
    wide, nm = momentum.panel(px, idx)
    _, picks = momentum.run_variant(wide, nm, int(cv.lookback_m), int(cv.skip_m), int(cv.hold_m), bool(cv.regime_filter), want_picks=True)
    picks.assign(strategy=verdict["chosen"]).to_csv(RES / "momentum_picks.csv", index=False)
    (RES / "momentum_verdict.json").write_text(json.dumps(verdict, default=float, indent=2))
    sid = verdict["chosen"]
    reg = registry()
    row = v[v.variant == sid].iloc[0]
    if sid not in set(reg.strategy):
        db.write("strategies", pd.DataFrame([dict(
            strategy=sid, name=f"Momentum: top {momentum.TOP_N} by {int(row.lookback_m)}-month return, hold {int(row.hold_m)} months",
            family="momentum", status="candidate" if verdict["passes"] else "no_edge",
            since=str(pd.Timestamp.now().date()), note="momentum study", version=C.VERSION)]))
    if verdict["passes"]:
        ev = {k: row[k] for k in row.index if k.endswith(("excess_pct", "t_excess", "cagr_pct", "max_dd_pct", "sharpe", "months"))}
        ev.update(variants_tested=verdict["variants_tested"], search_adjusted_t=verdict["search_adjusted_t"])
        propose("paper_trade", sid, f"Paper-trade {sid}",
                f"Beat an equal-weight portfolio of all liquid stocks by {row.design_avg_month_excess_pct:.2f}% a month in 2016-20 "
                f"(t {row.design_t_excess:.1f}), {row.validation_avg_month_excess_pct:.2f}% in 2021-23 (t {row.validation_t_excess:.1f}) "
                f"and {row.holdout_avg_month_excess_pct:.2f}% in 2024-26 (t {row.holdout_t_excess:.1f}, not significant). "
                f"Chosen from {verdict['variants_tested']} variants. Survivorship bias flatters momentum; paper trading is the test.", ev)
    return verdict


# ---------- daily ----------
def momentum_targets(px, sid, as_of):
    """What to hold and trade this month under a momentum rule.
    Each month-end ranks the top N; each batch is held for H months, so the portfolio is the last H
    batches at 1/H each. Returns rows tagged: hold (with weight), buy and sell at this month's first
    open, and next (provisional, ranked on the latest close; final at month-end)."""
    v = pd.read_csv(RES / "momentum_variants.csv").set_index("variant").loc[sid]
    L, S, H, n = int(v.lookback_m), int(v.skip_m), int(v.hold_m), momentum.TOP_N
    p = px.sort_values(["symbol", "date"]).copy()
    p["turnover_cr"] = (p.close * p.volume).groupby(p.symbol).transform(lambda s: s.rolling(20).mean()) / 1e7
    p["month"] = p.date.dt.to_period("M")
    me = p.groupby(["symbol", "month"]).agg(close=("close", "last"), turnover=("turnover_cr", "last")).reset_index()
    close = me.pivot(index="month", columns="symbol", values="close").sort_index()
    turn = me.pivot(index="month", columns="symbol", values="turnover").sort_index()

    def batch(ref):
        if ref not in close.index:
            return pd.Series(dtype=float)
        i = close.index.get_loc(ref)
        if i - S - L < 0:
            return pd.Series(dtype=float)
        mom = close.iloc[i - S] / close.iloc[i - S - L] - 1
        ok = (turn.iloc[i] >= C.MIN_TURNOVER_CR) & (close.iloc[i] >= C.MIN_PRICE) & mom.notna()
        return mom[ok].nlargest(n)

    cur = pd.Timestamp(as_of).to_period("M")
    batches = {k: batch(cur - k) for k in range(1, H + 2)}          # cur-1 .. cur-H-1
    out, base = [], dict(date=pd.Timestamp(as_of), strategy=sid)
    weight = {}
    for k in range(1, H + 1):
        for sym in batches[k].index:
            weight[sym] = weight.get(sym, 0) + 1 / (H * n)
    for sym, w in sorted(weight.items(), key=lambda x: -x[1]):
        out.append({**base, "portfolio": "hold", "symbol": sym, "weight_pct": round(100 * w, 2), "ranked_at": "", "momentum_pct": None})
    new, old = batches[1], batches[H + 1]
    for sym in new.index.difference(old.index):
        out.append({**base, "portfolio": "buy", "symbol": sym, "weight_pct": round(100 / (H * n), 2),
                    "ranked_at": f"{cur - 1} month-end", "momentum_pct": round(100 * new[sym], 1)})
    for sym in old.index.difference(new.index):
        out.append({**base, "portfolio": "sell", "symbol": sym, "weight_pct": round(100 / (H * n), 2),
                    "ranked_at": f"{cur - 1 - H} month-end", "momentum_pct": None})
    nxt = batch(cur)
    for sym, m in nxt.items():
        out.append({**base, "portfolio": "next (provisional)", "symbol": sym, "weight_pct": round(100 / (H * n), 2),
                    "ranked_at": f"close of {pd.Timestamp(as_of).date()}", "momentum_pct": round(100 * m, 1)})
    return pd.DataFrame(out)


def paper_performance(px, idx, sid, since):
    """The rule is deterministic, so its paper record since approval = the rule's monthly results since then."""
    v = pd.read_csv(RES / "momentum_variants.csv").set_index("variant").loc[sid]
    wide, nm = momentum.panel(px, idx)
    r = momentum.run_variant(wide, nm, int(v.lookback_m), int(v.skip_m), int(v.hold_m), bool(v.regime_filter))
    return r[r.month >= str(pd.Timestamp(since).to_period("M"))]


def select(reg_row, cards, registry_df):
    """Swing strategies active in today's state: passed the backtest there and not paused/retired."""
    state = reg_row["market_state"]
    if cards is None or cards.empty:
        return [], "No strategy cards yet: run the backtest first."
    halted = set(registry_df[registry_df.status.isin(HALT_STATUSES)].strategy) if len(registry_df) else set()
    act = sorted(set(cards[(cards.state == state) & cards.active].strategy) - halted)
    note = "" if act else "No swing strategy has a tested edge in this market state: no swing trades."
    return act, note


def health(outcomes, registry_df):
    """Weekly health on live (paper) swing results: auto-pause on a deep drawdown (risk reduction only),
    propose retirement after 30+ closed trades with negative expectancy."""
    acts = []
    if outcomes is None or outcomes.empty:
        return acts
    closed = outcomes[outcomes.status == "closed"]
    for sid, g in closed.groupby("strategy"):
        st = registry_df.loc[registry_df.strategy == sid, "status"]
        if st.empty or st.iat[0] in HALT_STATUSES:
            continue
        cum = g.sort_values("exit_date").ret_pct.cumsum()
        dd = (cum - cum.cummax()).min()
        if dd <= -15:
            set_status(sid, "paused", f"auto-paused: live drawdown {dd:.1f}%")
            propose("resume", sid, f"Resume {sid}?", f"Auto-paused after a {dd:.1f}% live drawdown.", {"drawdown_pct": dd})
            acts.append(f"{sid} paused")
        elif len(g) >= 30 and g.ret_pct.mean() < 0:
            propose("retire", sid, f"Retire {sid}?", f"{len(g)} closed trades, average {g.ret_pct.mean():.2f}% per trade.",
                    {"trades": len(g), "avg_pct": g.ret_pct.mean()})
            acts.append(f"{sid} retirement proposed")
    return acts
