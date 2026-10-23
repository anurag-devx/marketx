"""Daily pipeline as independent agent steps. Agents talk only through the database, so each step
can run on its own (from the dashboard or `python3 run_agent.py <step>`), reading its inputs from
the step before it.
    regime -> technical -> risk -> validation -> report
Fail safe: if regime or technical failed (or was not run) for the latest bar, risk approves nothing."""
import argparse, uuid
import pandas as pd
from core import config as C, db
from core.data import load_raw
from core.indicators import build
from core.runlog import agent_run, get_logger
from agents import regime as regime_agent, technical, risk, validation, strategy as strategy_agent
from core.config import STATE_MULT
from core import quality, backup
from agents import orders as orders_agent

STEPS = ["regime", "screener", "strategy", "technical", "risk", "validation", "report"]
_ctx = {}


def ctx():
    if not _ctx:
        px, idx, uni = load_raw()
        _, qs = quality.run(px, idx)
        feat, mkt = build(px, idx, uni)
        last = pd.Timestamp(feat.date.max())
        _ctx.update(feat=feat, mkt=mkt, px=px, idx=idx, last=last, day=str(last.date()), quality=qs,
                    quarantined=set(qs["quarantined"]), stale=(pd.Timestamp.now().normalize() - last).days > 4)
    return _ctx


def cards():
    return db.read("select * from strategy_cards")


def last_ok(agent, day):
    r = db.read("select status from agent_runs where agent = ? and for_date = ? order by started desc limit 1", [agent, day])
    return r is not None and len(r) > 0 and r.status.iat[0] == "ok"


def today_regime(day):
    r = db.read("select * from regime_daily where cast(date as date) = cast(? as date)", [day])
    if r is None or r.empty:
        raise RuntimeError(f"No regime output for {day}: run the regime agent first.")
    reg = r.iloc[0].to_dict()
    reg["date"] = pd.Timestamp(reg["date"])
    return reg


def step_regime(rec, meta):
    c = ctx()
    reg = regime_agent.run(c["mkt"][c["mkt"].date <= c["last"]], cards())   # same day as the stock data, even if the index is newer
    if c["stale"]:
        reg["size_multiplier"], reg["note"] = 0.0, f"Data is stale (last bar {c['day']}): no new trades."
    db.delete("regime_daily", "cast(date as date) = cast(? as date)", [c["day"]])
    db.write("regime_daily", pd.DataFrame([{**reg, **meta}]))
    rec["rows_out"], rec["message"] = 1, f"{reg['market_state']} active={reg['active_strategies'] or 'none'}"


def step_screener(rec, meta):
    """Candidate discovery: runs every enabled screener strategy (or the default config, labelled
    experimental) on the latest bar. Writes screener_results and a CSV research shortlist."""
    from screener import screen as scr, experiments as sx, interface
    c = ctx()
    feat = scr.features()
    data = (feat, list(pd.read_csv(C.DATA / "universe.csv").Symbol), scr.quality.run(c["px"], c["idx"], save=False)[0])
    runs = sx.enabled() or [dict(key="default", name="default (experimental)", config={})]
    total, notes = 0, []
    for e in runs:
        res = scr.screen(c["last"], e["config"], data=data)
        interface.save(c["last"], res["rows"], e["name"], res["meta"])
        scr.export(res, name=f"shortlist_{e['name'].split(' ')[0]}")
        total += len(res["shortlist"])
        notes.append(f"{e['name']}: {len(res['shortlist'])} of {res['diagnostics']['eligible']} eligible")
    rec["rows_out"], rec["message"] = total, "; ".join(notes)


def step_strategy(rec, meta):
    """Strategy agent: keeps the library in sync, picks today's swing strategies, lists the holdings of
    approved portfolio strategies, and runs health checks on live results."""
    c = ctx()
    reg = today_regime(c["day"])
    cd = cards()
    strategy_agent.sync_registry(cd)
    rg = strategy_agent.registry()
    act, note = strategy_agent.select(reg, cd, rg)
    if c["stale"]:
        act, note = [], f"Data is stale (last bar {c['day']}): no new trades."
    reg["active_strategies"] = ",".join(act)
    reg["size_multiplier"] = STATE_MULT.get(reg["market_state"], 0.0) if act else 0.0
    reg["note"] = note
    db.delete("regime_daily", "cast(date as date) = cast(? as date)", [c["day"]])
    db.write("regime_daily", pd.DataFrame([{**{k: v for k, v in reg.items() if k not in ("run_id", "version")}, **meta}]))
    # portfolio strategies approved for paper or live trading
    db.delete("portfolio_targets", "cast(date as date) = cast(? as date)", [c["day"]])
    held = rg[(rg.family == "momentum") & rg.status.isin(["paper", "active"])] if len(rg) else rg
    n_targets = 0
    db.delete("orders", "cast(date as date) = cast(? as date)", [c["day"]])
    last_px = c["px"][c["px"].date == c["last"]].set_index("symbol").close
    positions = risk.load_positions()
    for sid in held.strategy:
        t = strategy_agent.momentum_targets(c["px"], sid, c["last"])
        if len(t):
            db.write("portfolio_targets", t.assign(**meta))
            n_targets += len(t)
            cap = orders_agent.allocation(sid)
            if cap > 0:
                for kind in ("hold", "next (provisional)"):
                    o = orders_agent.orders(t, last_px, positions, sid, cap, kind)
                    if len(o):
                        db.write("orders", o.assign(date=c["last"], list=("this month" if kind == "hold" else "next month (provisional)"),
                                                    next_rebalance=orders_agent.next_rebalance(c["last"]), capital=cap, **meta))
    acts = strategy_agent.health(db.read("select * from outcomes"), rg)
    rec["rows_out"] = len(act) + n_targets
    rec["message"] = f"swing active: {','.join(act) or 'none'}; portfolio strategies: {len(held)}" + (f"; {', '.join(acts)}" if acts else "")


def step_technical(rec, meta):
    c = ctx()
    reg = today_regime(c["day"])
    cands = pd.DataFrame()
    q = c["quarantined"]
    feat = c["feat"][~c["feat"].symbol.isin(q)] if q else c["feat"]
    if reg["active_strategies"] and reg["size_multiplier"] > 0:
        cands = technical.run(feat, reg, cards())
    db.delete("candidates", "cast(date as date) = cast(? as date)", [c["day"]])
    if len(cands):
        db.write("candidates", cands.assign(**meta))
    # every signal from every strategy, active or not, so the dashboard can show what was filtered and why
    allsig = technical.all_signals(feat, reg)
    lab_n = 0
    try:     # strategies approved from the Strategy Lab: today's signals, shown for paper trading
        import json
        rg = strategy_agent.registry()
        labs = rg[(rg.family == "lab") & rg.status.isin(["paper", "active"])] if len(rg) else rg
        if len(labs):
            from lab import engine as lab_engine
            for sid, name in zip(labs.strategy, labs.name):
                f = C.BASE / "lab" / "workspace" / "approved" / f"{sid}.json"
                if not f.exists():
                    continue
                sig, _ = lab_engine.latest_signals(json.loads(f.read_text())["definition"])
                if len(sig):
                    sig = sig[~sig.symbol.isin(q)] if q else sig
                    allsig = pd.concat([allsig, pd.DataFrame(dict(date=reg["date"], symbol=sig.symbol, sector=sig.get("sector"), strategy=sid,
                                                                   strategy_name=name, close=sig.close.round(2), strategy_active=False,
                                                                   market_state=reg["market_state"]))], ignore_index=True)
                    lab_n += len(sig)
    except Exception as e:
        print(f"Lab strategies skipped: {e}", flush=True)
    db.delete("signals", "cast(date as date) = cast(? as date)", [c["day"]])
    if len(allsig):
        db.write("signals", allsig.assign(**meta))
    rec["rows_out"] = len(cands)
    rec["message"] = f"{len(allsig)} signals from all strategies ({lab_n} from Lab strategies), {len(cands)} from active ones" + (
        f"; skipped {len(q)} stock(s) with data errors: {', '.join(sorted(q))}" if q else "")


def step_risk(rec, meta):
    c = ctx()
    reg = today_regime(c["day"])
    positions, account = risk.load_positions(), risk.load_account()
    upstream = [a for a in ("regime", "strategy", "technical") if not last_ok(a, c["day"])]
    cands = db.read("select * from candidates where cast(date as date) = cast(? as date)", [c["day"]])
    cands = cands.drop(columns=["run_id", "version"], errors="ignore") if cands is not None else pd.DataFrame()
    dec, halt = pd.DataFrame(), None
    if upstream:
        halt = f"{' and '.join(upstream)} did not run successfully for {c['day']}: no new trades"
        if len(cands):
            dec = cands.assign(approved=False, quantity=0, value=0.0, risk_rs=0.0, size_multiplier=0.0, reject_reason=halt)
    elif len(cands):
        dec, info = risk.run(cands, reg, positions, account)
        halt = info["halt"]
    exits = risk.exits_due(positions, c["feat"], c["last"])
    db.delete("decisions", "cast(date as date) = cast(? as date)", [c["day"]])
    if len(dec):
        db.write("decisions", dec.assign(**meta))
    db.delete("exits_due", "cast(date as date) = cast(? as date)", [c["day"]])
    if len(exits):
        db.write("exits_due", exits.assign(date=c["last"], **meta))
    rec["rows_out"] = int(dec.approved.sum()) if len(dec) else 0
    rec["message"] = halt


def step_validation(rec, meta):
    c = ctx()
    hist = db.read("select * from decisions")
    out = pd.DataFrame()
    if hist is not None and len(hist):
        hist["date"] = pd.to_datetime(hist.date)
        out = validation.track(c["feat"], hist)
        if len(out):
            db.write("outcomes", out.assign(**meta), mode="replace")
    rec["rows_out"] = len(out)


def _md(df, cols):
    return df[[x for x in cols if x in df]].to_markdown(index=False) if df is not None and len(df) else "_none_"


def step_report(rec, meta):
    """Supervisor v0: assembles the day's report from what the agents stored."""
    c = ctx()
    day = c["day"]
    reg = today_regime(day)
    failed = [a for a in ("regime", "strategy", "technical", "risk") if not last_ok(a, day)]
    dec = db.read("select * from decisions where cast(date as date) = cast(? as date) order by setup_score desc", [day])
    exits = db.read("select * from exits_due where cast(date as date) = cast(? as date)", [day])
    out = db.read("select * from outcomes")
    vsum = validation.summary(out, cards()) if out is not None and len(out) else pd.DataFrame()
    acc = risk.load_account()
    dd = 1 - acc["equity"] / acc["peak_equity"]
    f1 = lambda v: "n/a" if v is None or pd.isna(v) else round(v, 1)
    appr = dec[dec.approved] if dec is not None and len(dec) else None
    rej = dec[~dec.approved] if dec is not None and len(dec) else None
    halt = rej.reject_reason.iat[0] if rej is not None and len(rej) and (appr is None or appr.empty) else None
    r = [f"# Daily report for {day} (run {meta['run_id']}, v{C.VERSION})\n"]
    if failed:
        r += [f"## ⚠ Not run or failed for {day}: {', '.join(failed)}. No new trades until they succeed.", ""]
    r += ["## Market",
          f"- State: **{reg['market_state']}** for {reg['days_in_state']} days"
          + (f" (raw signal now {reg['state_raw']}, switch pending confirmation)" if reg["pending_change"] else ""),
          f"- Regime score {reg['regime_score']}/100 | Nifty {reg['nifty']:.0f} ({reg['nifty_vs_200d_pct']:+.1f}% vs 200-day)"
          f" | VIX {f1(reg['vix'])} (median {f1(reg['vix_median'])})"
          f" | {0 if pd.isna(reg['breadth_200']) else round(100 * reg['breadth_200'])}% of stocks above 200-day",
          f"- Active strategies: {reg['active_strategies'] or 'none'} | size multiplier {reg['size_multiplier']}",
          f"- Drawdown from peak: {dd:.1%}" + (f" | **{halt}**" if halt else ""),
          *([f"- **{reg['note']}**"] if reg.get("note") else []), "",
          "## Exits due on open positions",
          _md(exits[exits.action != "hold"] if exits is not None and len(exits) else None, ["symbol", "strategy", "days_held", "action", "detail"]), "",
          "## New trades for the next open (approved by risk agent)",
          _md(appr, ["symbol", "strategy_name", "entry", "stop", "target", "exit_rule", "quantity", "value", "risk_rs", "setup_score"]), "",
          "Entry shown is the signal-day close; buy near the next open. If it gaps more than 1 ATR from the entry shown, skip the trade.",
          "After buying, add the trade to positions.csv (symbol, sector, strategy, entry_date, entry_price, quantity, stop, target).", "",
          "## Rejected candidates (top 10)", _md(rej.head(10) if rej is not None else None, ["symbol", "strategy", "setup_score", "reject_reason"]), "",
          "## Open positions", _md(exits, ["symbol", "strategy", "days_held", "action", "detail"]), "",
          "## Screener: research shortlist (not buy recommendations)",
          _screener_section(day), "",
          "## Strategy agent",
          _strategy_section(day), "",
          "## Paper results of all recommendations so far (validation v0)",
          _md(vsum, ["strategy", "trades", "win_pct", "live_exp_pct", "backtest_exp_pct"])]
    C.REPORTS.mkdir(exist_ok=True)
    path = C.REPORTS / f"{day}.md"
    path.write_text("\n".join(r))
    rec["message"] = f"saved {path.name}"
    print("\n".join(r))
    try:
        from core import notify
        n_tr = int(appr.shape[0]) if appr is not None else 0
        pend = db.read("select count(*) n from proposals where status = 'pending'")
        n_p = int(pend.n.iat[0]) if pend is not None and len(pend) else 0
        tg = db.read("select count(*) n from portfolio_targets where cast(date as date) = cast(? as date) and portfolio in ('buy','sell')", [day])
        msg = (f"{reg['market_state'].replace('_', ', ')} · {n_tr} swing trade(s)"
               + (f" · {int(tg.n.iat[0])} momentum buys/sells this month" if tg is not None and len(tg) and tg.n.iat[0] else "")
               + (f" · {n_p} proposal(s) to approve" if n_p else "")
               + (f" · FAILED: {', '.join(failed)}" if failed else ""))
        notify.send(f"Swing agents: {day}", msg)
    except Exception:
        pass


def _screener_section(day):
    try:
        from screener.interface import load_shortlist
        sl = load_shortlist(day)
    except Exception as e:
        return f"_screener unavailable: {e}_"
    if sl.empty():
        return "_no stocks qualified, or the screener did not run_"
    lines = []
    for s, g in sl.rows.groupby("screener_strategy"):
        top = g.sort_values("rank").head(15)
        lines.append(f"- **{s}**: {len(g)} stocks. Top 15: " + ", ".join(f"{r.symbol} ({int(r.setups_passed)})" for r in top.itertuples()))
    if sl.warnings:
        lines.append(f"- Data warnings on shortlisted stocks: {', '.join(sl.warnings[:8])}")
    return "\n".join(lines)


def _strategy_section(day):
    rg = strategy_agent.registry()
    lines = []
    if len(rg):
        counts = rg.status.value_counts().to_dict()
        lines.append("- Library: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    pend = db.read("select title from proposals where status = 'pending'")
    if pend is not None and len(pend):
        lines.append(f"- **{len(pend)} proposal(s) waiting for your approval** (dashboard: Settings and approvals)")
    t = db.read("select * from portfolio_targets where cast(date as date) = cast(? as date)", [day])
    if t is not None and len(t):
        for sid, g in t.groupby("strategy", sort=False):
            hold = g[g.portfolio == "hold"]
            buy, sell = g[g.portfolio == "buy"], g[g.portfolio == "sell"]
            lines.append(f"- **{sid}** (paper): {len(hold)} holdings this month, top 5 by weight: "
                         + ", ".join(f"{r.symbol} {r.weight_pct:.1f}%" for r in hold.head(5).itertuples()))
            lines.append(f"  - Bought at this month's first open: {', '.join(buy.symbol) or 'none'}")
            lines.append(f"  - Sold at this month's first open: {', '.join(sell.symbol) or 'none'}")
            o = db.read("select * from orders where cast(date as date) = cast(? as date) and strategy = ? and list = 'this month'", [day, sid])
            if o is not None and len(o):
                act = o[o.action.isin(["BUY", "SELL", "ADD", "TRIM"])]
                lines.append(f"  - Orders for your ₹{o.capital.iat[0]:,.0f} (next rebalance {pd.Timestamp(o.next_rebalance.iat[0]).date()}): "
                             + (", ".join(f"{r.action} {abs(r.order_qty)} {r.symbol}" for r in act.itertuples()) or "nothing to trade"))
            else:
                lines.append("  - No order list: set the money for this strategy on the Positions page.")
    return "\n".join(lines) or "_nothing to report_"


FUNCS = dict(regime=step_regime, screener=step_screener, strategy=step_strategy, technical=step_technical, risk=step_risk, validation=step_validation, report=step_report)


def run(steps):
    run_id = uuid.uuid4().hex[:8]
    meta = dict(run_id=run_id, version=C.VERSION)
    log = get_logger()
    try:
        b = backup.backup("daily")
        if b:
            print(f"Backup saved: backups/{b}", flush=True)
    except Exception as e:
        print(f"Backup failed (continuing): {e}", flush=True)
    try:
        with agent_run(run_id, "data") as rec:
            c = ctx()
            rec["for_date"] = c["day"]
            qs = c["quality"]
            rec["rows_out"], rec["message"] = len(c["feat"]), (f"last bar {c['day']}, {c['feat'].symbol.nunique()} stocks; "
                                                               f"quality {qs['status']} ({qs['errors']} errors, {qs['warnings']} warnings)")
    except Exception as e:
        print(f"Data load failed: {e}")
        return 1
    errors = 0
    for s in steps:
        print(f"--- {s} ---", flush=True)
        try:
            with agent_run(run_id, s, for_date=c["day"]) as rec:
                FUNCS[s](rec, meta)
            print(f"{s}: ok", flush=True)
        except Exception as e:
            errors += 1
            print(f"{s}: FAILED - {e}", flush=True)
    log.info(f"[{run_id}] steps={steps} errors={errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Run one agent or the whole daily pipeline.")
    ap.add_argument("step", choices=STEPS + ["all"])
    a = ap.parse_args()
    raise SystemExit(run(STEPS if a.step == "all" else [a.step]))
