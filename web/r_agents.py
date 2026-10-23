"""API: Agents, Signals, Strategies, Screener research, Validation, Positions, Reports & logs."""
import json
import numpy as np, pandas as pd
from core import config as C, jobs
from web.api import route, q, prices, universe, UserError

STATE_LABEL = {"up_calm": "Uptrend, calm", "up_volatile": "Uptrend, volatile", "down_calm": "Downtrend, calm", "down_volatile": "Downtrend, volatile"}
PERIOD_LABEL = {"design": "Design 2016–20", "validation": "Validation 2021–23", "holdout": "Holdout 2024–26"}
ANALYSIS = C.RESULTS / "analysis"


def _agents_meta():
    """The agent descriptions (kept in ui/common.py); read without importing Streamlit."""
    src = (C.BASE / "ui" / "common.py").read_text()
    start = src.index("AGENTS = [") + len("AGENTS = ")
    return eval(src[start:src.index("\n]\n", start) + 2], {"__builtins__": {}, "dict": dict})   # our own file: a list of dict(...) literals


def _df_cols(d, fmt=None):
    fmt = fmt or {}
    out = []
    for c in d.columns:
        t = fmt.get(c)
        if t is None:
            s = d[c]
            t = "num2" if pd.api.types.is_float_dtype(s) else "int" if pd.api.types.is_integer_dtype(s) else "bool" if pd.api.types.is_bool_dtype(s) \
                else "dt" if pd.api.types.is_datetime64_any_dtype(s) else "text"
            if c == "symbol":
                t = "symonly"
        out.append([c, c.replace("_", " "), t])
    return out


def table(title, d, fmt=None, **kw):
    d = d if d is not None else pd.DataFrame()
    return dict(kind="table", title=title, rows=d, columns=_df_cols(d, fmt), **kw)


# ---------------- agents ----------------
@route("GET", "agents")
def agents(_):
    meta = _agents_meta()
    lr = q("""select agent, status, started, seconds, rows_out, message from (select *, row_number() over (partition by agent order by started desc) rn
              from agent_runs) where rn = 1""")
    lr = lr.set_index("agent") if len(lr) else pd.DataFrame()
    busy = jobs.active_agents()
    out = []
    for a in meta:
        k = "data" if a["key"] == "fetch" else a["key"]
        row = lr.loc[k].to_dict() if len(lr) and k in lr.index else None
        out.append(dict(key=a["key"], name=a["name"], kind=a["kind"], what=a["what"], io=a["io"], built=a["built"], last=row,
                        working=busy.get(a["key"], {}).get("label")))
    return out


@route("GET", "agent")
def agent(p):
    key = p.get("key", "")
    meta = {a["key"]: a for a in _agents_meta()}
    if key not in meta:
        raise UserError("unknown agent")
    k = "data" if key == "fetch" else key
    secs = []
    if key in ("technical", "risk"):
        t = "candidates" if key == "technical" else "decisions"
        dates = q(f"select distinct cast(date as date) d from {t} order by d desc limit 60")
        d = p.get("date") or (str(dates.d.iat[0]) if len(dates) else None)
        if d:
            secs.append(table(f"Output on {d}", q(f"select * exclude (run_id) from {t} where cast(date as date) = cast(? as date) order by setup_score desc", [d]), csv=f"{t}_{d}"))
        else:
            secs.append(dict(kind="note", text="No output yet."))
    elif key == "strategy":
        secs.append(table("Strategy registry", q("select strategy, name, family, status, since, note from strategies order by family, strategy")))
        rr = q("select status, started, message from agent_runs where agent = 'strategy_research' order by started desc limit 1")
        if len(rr):
            secs.append(dict(kind="note", text=f"Last research run: {pd.Timestamp(rr.started.iat[0]):%d %b %H:%M} — {rr.message.iat[0]}"))
    elif key == "regime":
        secs.append(table("Market state by day", q("select date, market_state, days_in_state, regime_score, size_multiplier, active_strategies, nifty, vix, breadth_200, note "
                                                   "from regime_daily order by date desc limit 400"), {"date": "date"}, csv="regime"))
    elif key == "validation":
        secs.append(table("Tracked recommendations", q("select * exclude (run_id) from outcomes order by decision_date desc"), csv="outcomes"))
    elif key == "backtest":
        secs.append(table("Strategy cards", q("select strategy, name, state, trades, expectancy_pct, edge_pct, t_stat, active from strategy_cards order by strategy, state"),
                          {"expectancy_pct": "snum2", "edge_pct": "snum2", "t_stat": "num1"}))
    elif key == "report":
        reps = sorted(C.REPORTS.glob("*.md"), reverse=True) if C.REPORTS.exists() else []
        secs.append(dict(kind="md", title=reps[0].stem if reps else "", text=reps[0].read_text() if reps else "No report yet."))
    elif key == "screener":
        secs.append(dict(kind="note", text="See Screener → Agents' shortlist for the saved list."))
    elif key == "fetch":
        px = prices()
        if px is not None:
            secs.append(dict(kind="note", text=f"{px.symbol.nunique()} stocks, {len(px):,} rows, {px.date.min().date()} to {px.date.max().date()}"))
    hist = q("select started, status, seconds, rows_out, message, for_date from agent_runs where agent = ? order by started desc limit 50", [k])
    secs.append(table("Run history", hist, {"started": "dt", "seconds": "num0", "rows_out": "int", "message": "long", "status": "status"}, tools=False))
    return dict(sections=secs)


# ---------------- signals ----------------
def _outcome(r, state):
    if not r.strategy_active:
        return "Off: strategy has no tested edge in " + STATE_LABEL.get(state, state).lower()
    if pd.isna(r.approved):
        return "Not ranked: a higher-scoring signal on the same stock was used"
    if r.approved:
        return f"Approved: {int(r.quantity)} shares"
    return f"Rejected by risk: {r.reject_reason}"


@route("GET", "signals")
def signals(p):
    dates = q("select distinct cast(date as date) d from signals order by d desc limit 120")
    if dates.empty:
        return dict(dates=[], sections=[dict(kind="note", text="No signals stored yet. Run all agents once.")])
    d = p.get("date") or str(dates.d.iat[0])
    sig = q("select * exclude (run_id, version) from signals where cast(date as date) = cast(? as date)", [d])
    dec = q("select symbol, strategy, approved, quantity, value, risk_rs, reject_reason, setup_score from decisions where cast(date as date) = cast(? as date)", [d])
    reg = q("select * from regime_daily where cast(date as date) = cast(? as date)", [d])
    state = reg.market_state.iat[0] if len(reg) else ""
    m = sig.merge(dec, on=["symbol", "strategy"], how="left") if len(dec) else sig.assign(approved=np.nan, quantity=np.nan, value=np.nan, risk_rs=np.nan,
                                                                                          reject_reason=None, setup_score=np.nan)
    m["outcome"] = [_outcome(r, state) for r in m.itertuples()]
    m["status"] = np.where(~m.strategy_active.astype(bool), "Strategy off", np.where(m.approved == True, "Approved",  # noqa: E712
                           np.where(m.approved == False, "Rejected by risk", "Not ranked")))  # noqa: E712
    n_all, n_on, n_ok = len(m), int(m.strategy_active.sum()), int((m.status == "Approved").sum())
    cnt = m.groupby(["strategy", "status"]).size().unstack(fill_value=0)
    strategies = sorted(m.strategy.unique())
    if p.get("strategies"):
        m = m[m.strategy.isin(p["strategies"].split(","))]
    if p.get("statuses"):
        m = m[m.status.isin(p["statuses"].split("|"))]
    m = m.sort_values(["strategy_active", "setup_score", "strength"], ascending=False)
    secs = [dict(kind="kpis", items=[dict(label="Signals", value=str(n_all)), dict(label="From strategies switched on", value=str(n_on)),
                                     dict(label="Approved by risk", value=str(n_ok)), dict(label="Market state", value=STATE_LABEL.get(state, state) or "—")])]
    if len(reg) and reg.note.iat[0]:
        secs.append(dict(kind="note", text=reg.note.iat[0]))
    if len(cnt):
        secs.append(dict(kind="chart", type="bars", title="Signals by strategy", height=220, x=list(cnt.index),
                         series=[dict(name=s_, y=cnt[s_].astype(float).tolist() if s_ in cnt else [0.0] * len(cnt),
                                      color={"Approved": "#059669", "Rejected by risk": "#dc2626", "Not ranked": "#94a3b8", "Strategy off": "#cbd5e1"}[s_])
                                 for s_ in ("Approved", "Rejected by risk", "Not ranked", "Strategy off")]))
    secs.append(dict(kind="table", rows=m, csv=f"signals_{d}", columns=[["symbol", "Stock", "symonly"], ["sector", "Sector"], ["strategy", "Strategy"],
                     ["strategy_name", "Name"], ["close", "Close", "inr"], ["stop", "Stop", "inr"], ["target", "Target", "inr"], ["strength", "Strength", "num2"],
                     ["rs_pct", "Strength rank", "num2"], ["turnover_cr", "Avg ₹ cr/day", "num0"], ["outcome", "What happened", "long"]]))
    return dict(dates=[str(x) for x in dates.d], date=d, strategies=strategies, sections=secs)


# ---------------- strategies (backtest analysis) ----------------
def _csv(name):
    p = ANALYSIS / name
    return pd.read_csv(p) if p.exists() else None


def _pivot(df, index, columns, values, order_rows=None, order_cols=None, labels=None):
    pv = df.pivot_table(index=index, columns=columns, values=values, aggfunc="first")
    if order_rows is not None:
        pv = pv.reindex([r for r in order_rows if r in pv.index])
    if order_cols is not None:
        pv = pv[[c for c in order_cols if c in pv.columns]]
    pv = pv.reset_index()
    cols = [[index, index.capitalize(), "text"]] + [[str(c), (labels or {}).get(c, str(c)), "snum2"] for c in pv.columns if c != index]
    pv.columns = [str(c) for c in pv.columns]
    return pv, cols


@route("GET", "strategies")
def strategies(p):
    s = _csv("strategies.csv")
    if s is None:
        return dict(empty=True)
    tab = p.get("tab", "report")
    cost = C.COST_RT * 100
    order = s.strategy.tolist()
    if tab == "report":
        edge = s[s.states_with_edge > 0]
        best = s.loc[s.net_pct.idxmax()]
        clear = s[s.gross_pct > cost]
        headline = (f"{'No' if edge.empty else len(edge)} swing strateg{'y has' if len(edge) == 1 else 'ies have'} a tested edge. "
                    f"The best, {best.strategy} {best['name']}, makes {best.net_pct:+.2f}% per trade after costs. "
                    f"Before costs the strategies make {s.gross_pct.min():+.2f}% to {s.gross_pct.max():+.2f}% per trade, against a {cost:.1f}% cost"
                    + (f": only {', '.join(clear.strategy)} clear{'s' if len(clear) == 1 else ''} it." if len(clear) else ": none clears it."))
        secs = [dict(kind="note", tone="info" if len(edge) else "warn", text=headline),
                dict(kind="kpis", items=[dict(label="Trades tested", value=f"{int(s.trades.sum()):,}"),
                                         dict(label=f"Best net / trade ({best.strategy})", value=f"{best.net_pct:+.2f}%"),
                                         dict(label=f"Best gross ({s.loc[s.gross_pct.idxmax(), 'strategy']})", value=f"{s.gross_pct.max():+.2f}%"),
                                         dict(label="With an edge", value=f"{len(edge)} of {len(s)}"),
                                         dict(label=f"Best CAGR ({s.loc[s.cagr_pct.idxmax(), 'strategy']})", value=f"{s.cagr_pct.max():+.1f}%")]),
                table("Scorecard (per trade, after costs unless marked gross)", s.drop(columns=["states_with_edge"]),
                      {"net_pct": "snum2", "gross_pct": "snum2", "win_pct": "num1", "avg_win_pct": "snum2", "avg_loss_pct": "snum2", "profit_factor": "num2",
                       "cagr_pct": "snum1", "max_dd_pct": "num1", "sharpe": "num2", "trades": "int"}, tools=False, csv="strategies")]
        g = s.sort_values("gross_pct", ascending=False)
        secs.append(dict(kind="chart", type="hbar", title=f"Gross return per trade vs the {cost:.1f}% round-trip cost", labels=(g.strategy + " " + g["name"]).tolist(),
                         values=g.gross_pct.round(3).tolist(), decimals=2))
        hz = _csv("horizon.csv")
        if hz is not None:
            pv = hz.pivot_table(index="days", columns="strategy", values="excess_pct")
            secs.append(dict(kind="chart", type="line", title="When the payoff arrives: return after a signal minus any liquid stock, by days held",
                             x=[f"{d}d" for d in pv.index], ysuffix="%", zero=True,
                             series=[dict(name=c, y=pv[c].round(3).tolist(), width=2.2 if hz[hz.strategy == c].group.iat[0] == "momentum" else 1.2) for c in pv.columns]))
        ex = _csv("exits.csv")
        if ex is not None:
            pv, cols = _pivot(ex, "strategy", "exit", "share_pct", order)
            secs.append(dict(kind="table", title="How trades end (% of trades)", rows=pv, columns=[c if i == 0 else [c[0], c[1], "pct1"] for i, c in enumerate(cols)], tools=False))
        eq = _csv("equity_weekly.csv")
        if eq is not None:
            pv = eq.pivot_table(index="date", columns="strategy", values="equity")
            pv = pv.iloc[::2]
            secs.append(dict(kind="chart", type="line", title="Account value: ₹10 lakh, 10 equal positions (₹ lakh)", x=list(pv.index), hlines=[10],
                             series=[dict(name=c, y=(pv[c] / 1e5).round(2).tolist(), width=1.3) for c in pv.columns]))
        stt = _csv("states.csv")
        if stt is not None:
            pv, cols = _pivot(stt, "strategy", "state", "net_pct", order, list(STATE_LABEL), STATE_LABEL)
            secs.append(dict(kind="table", title="Net return per trade by market state (%)", rows=pv, columns=cols, tools=False))
        per = _csv("periods.csv")
        if per is not None:
            pv, cols = _pivot(per, "strategy", "period", "net_pct", order, list(PERIOD_LABEL), PERIOD_LABEL)
            secs.append(dict(kind="table", title="Net return per trade by research period (%)", rows=pv, columns=cols, tools=False))
        yr, mk = _csv("years.csv"), _csv("market_years.csv")
        if yr is not None:
            pv, cols = _pivot(yr.assign(year=yr.year.astype(str)), "year", "strategy", "net_pct", None, order)
            if mk is not None:
                pv = pv.merge(mk.assign(year=mk.year.astype(str)).rename(columns={"nifty_return_pct": "Nifty", "random_stock_5d_gross_pct": "Random 5d"}), on="year", how="left")
                cols += [["Nifty", "Nifty 50", "snum1"], ["Random 5d", "Random stock 5d", "snum2"]]
            secs.append(dict(kind="table", title="Year by year: net return per trade (%) beside the market", rows=pv, columns=cols, tools=False))
        stp = _csv("stops.csv")
        if stp is not None:
            secs.append(dict(kind="note", text=f"Stops aren't the problem: only {stp.recovered_10d_pct.iat[0]}% of {int(stp.stopped_trades.iat[0]):,} stopped trades would have been in profit 10 days later."))
        return dict(sections=secs)
    if tab == "playbook":
        cards = q("select strategy, state, active from strategy_cards")
        secs = []
        if len(cards):
            play = cards[cards.active].groupby("state").strategy.apply(lambda x: ", ".join(sorted(x))).reindex(list(STATE_LABEL)).fillna("cash")
            secs.append(dict(kind="table", title="Playbook selected by the backtest", tools=False, rows=pd.DataFrame(dict(state=[STATE_LABEL[k] for k in play.index], active=play.values)),
                             columns=[["state", "Market state"], ["active", "Active strategies"]]))
        from core.strategies import STRATEGIES
        rules = []
        for sid, x in STRATEGIES.items():
            stop = f"{x['stop'][1]}× ATR" if x["stop"][0] == "atr" else x["stop"][1].replace("_", " ")
            tgt = "none" if x["target"] is None else (f"{x['target'][1]}× ATR" if x["target"][0] == "atr" else f"{x['target'][2]}× {x['target'][1]}")
            rules.append(dict(id=sid, strategy=x["name"], stop=stop, target=tgt, rule_exits=", ".join(x["exits"]) or "—", max_days=x["max_days"]))
        secs.append(table("Strategy rules", pd.DataFrame(rules), tools=False,
                          note="Entry signals are defined in core/strategies.py. Signal on the close, entry at the next open, 0.3% round-trip cost."))
        return dict(sections=secs)
    if tab == "momentum":
        res = C.RESULTS / "strategy"
        vp, mp, jp = res / "momentum_variants.csv", res / "momentum_best_monthly.csv", res / "momentum_verdict.json"
        if not vp.exists():
            return dict(sections=[dict(kind="note", text="No momentum study yet. Agents → Strategy → Run research.")])
        v, m, verdict = pd.read_csv(vp), pd.read_csv(mp), json.loads(jp.read_text())
        best = v[v.variant == verdict["chosen"]].iloc[0]
        ok = verdict["passes"]
        m["cum"] = (1 + m.net).cumprod() * 10
        m["cum_ctrl"] = (1 + m.control).cumprod() * 10
        secs = [dict(kind="note", tone="info" if ok else "warn", text=f"{verdict['chosen']} {'passes' if ok else 'does not pass'}: buy the top 20 liquid stocks by "
                     f"{int(best.lookback_m)}-month return{' (skipping the last month)' if best.skip_m else ''}, hold each batch {int(best.hold_m)} month(s), rebalance monthly. "
                     f"Chosen as the best of {verdict['variants_tested']} variants on 2016–20 only."),
                dict(kind="kpis", items=[dict(label=lab, value=f"{best[f'{p}_avg_month_excess_pct']:+.2f}% / month", delta=f"t = {best[f'{p}_t_excess']:.1f}")
                                         for p, lab in (("design", "2016–20 (searched)"), ("validation", "2021–23 (checked once)"), ("holdout", "2024–26 (holdout)"))]
                     + [dict(label="CAGR vs control", value=f"{best.all_cagr_pct:.1f}%", delta=f"control {best.all_control_cagr_pct:.1f}%")]),
                dict(kind="chart", type="line", title="Growth of ₹10 lakh after costs (₹ lakh)", x=m.month.astype(str).tolist(),
                     series=[dict(name=verdict["chosen"], y=m.cum.round(2).tolist(), width=2.4), dict(name="Equal-weight control", y=m.cum_ctrl.round(2).tolist())]),
                dict(kind="md", title="Caveats", text="- The stock list is today's Nifty 500, so stocks that fell out of the index are missing; that flatters momentum.\n"
                     "- The 2024–26 result is positive but not statistically significant.\n"
                     f"- Drawdowns are deep: the worst fall from a peak was {best.all_max_dd_pct:.0f}%. Paper trading is the real test before any money goes in.\n"
                     f"- Best-of-{verdict['variants_tested']} search: a t of {verdict['search_adjusted_t']:.1f} would be needed in 2016–20 alone to rule out luck; it scored {verdict['design_t']:.1f}."),
                table(f"All {len(v)} variants tested", v[["variant", "design_avg_month_excess_pct", "design_t_excess", "validation_avg_month_excess_pct", "validation_t_excess",
                                                          "holdout_avg_month_excess_pct", "holdout_t_excess", "all_cagr_pct", "all_max_dd_pct", "all_sharpe"]]
                      .sort_values("design_t_excess", ascending=False).round(2))]
        return dict(sections=secs)
    if tab == "summary":
        prof = _csv("profiles.csv")
        if prof is None:
            return dict(sections=[dict(kind="note", text="Run Backtest + analysis to build the summary.")])
        stt = _csv("states.csv")
        rows = []
        for _, pr in prof[prof.strategy != "ALL"].iterrows():
            sid = pr.strategy
            act = stt[(stt.strategy == sid) & (stt.active == True)] if stt is not None else pd.DataFrame()  # noqa: E712
            r = s[s.strategy == sid]
            rows.append(dict(strategy=sid, family=pr.family, edge=len(act) > 0, works_in=", ".join(STATE_LABEL.get(x, x) for x in act.state) or "No market state",
                             result=f"{r.net_pct.iat[0]:+.2f}% per trade ({int(r.trades.iat[0]):,} trades)" if len(r) else "—",
                             overweights=pr.overweight_sectors if isinstance(pr.overweight_sectors, str) else "",
                             strength_rank=pr.median_rs_pct, return_3m=pr.median_3m_return_pct, daily_range=pr.median_atr_pct))
        return dict(sections=[table("What each strategy picks and how it did", pd.DataFrame(rows),
                                    {"strength_rank": "frac", "return_3m": "snum1", "daily_range": "num1", "overweights": "long"}, tools=False)])
    raise UserError("unknown tab")


# ---------------- screener research ----------------
@route("GET", "screener-research")
def screener_research(p):
    from screener import experiments as X, config as SC, evaluate as V
    tab = p.get("tab", "compare")
    if tab == "compare":
        h, hold = int(p.get("h", 10)), p.get("hold") == "true"
        n = X.configs_tested()
        if hold:
            SC.OUT_DIR.mkdir(parents=True, exist_ok=True)
            with open(SC.OUT_DIR / "holdout_views.csv", "a") as f:
                f.write(f"{pd.Timestamp.now():%Y-%m-%d %H:%M:%S},compare-web\n")
        t = X.comparison(h, include_holdout=hold)
        if t.empty:
            return dict(sections=[dict(kind="note", text="No experiments yet. Run them in the last tab.")])
        bt = V.validate({"by_period": {}, "by_year": []}, h, max(n, 1))["search_t"]
        ok = t[t.status == "ok"].copy()
        bad = t[t.status != "ok"]
        for pp in ("dev", "val", "hold"):
            if f"{pp}_excess_ci95" in ok:
                ok[f"{pp}_ci95"] = ok[f"{pp}_excess_ci95"].map(lambda x: f"{x[0]:+.2f} to {x[1]:+.2f}" if isinstance(x, (list, tuple, np.ndarray)) and None not in list(x) else "")
        cols = ["experiment", "strategy", "params", "validated", "dev_signals", "dev_win_%", "dev_base_win_%", "dev_lift_pp", "dev_mean_net_%", "dev_excess_%",
                "dev_ci95", "dev_t", "dev_p5_net_%", "val_signals", "val_win_%", "val_lift_pp", "val_mean_net_%", "val_excess_%", "val_ci95", "val_t",
                "neighbours_val_positive"] + (["hold_signals", "hold_win_%", "hold_excess_%", "hold_ci95", "hold_t"] if hold else []) + ["dev_warning", "val_warning"]
        view = ok[[c for c in cols if c in ok]].round(2)
        secs = [dict(kind="kpis", items=[dict(label="Configurations tested", value=str(n)), dict(label="t needed to beat luck", value=f"{bt:.1f}")]),
                dict(kind="note", text=f"With {n} configurations tried, a development t of about {bt:.1f} is needed before the best one can be told apart from luck. "
                     "Don't pick by win rate alone: look at the size of the edge, its interval, the downside and whether it holds in validation.")]
        if len(bad):
            secs.append(dict(kind="note", tone="warn", text=f"{len(bad)} configuration(s) failed or are incomplete."))
        secs.append(table(None, view, {"validated": "bool", "params": "long"}, csv=f"screener_comparison_h{h}"))
        return dict(sections=secs)
    if tab == "detail":
        idx = X.index()
        idx = idx[idx.status == "ok"] if len(idx) else idx
        if idx.empty:
            return dict(options=[], sections=[dict(kind="note", text="No finished experiments.")])
        opts = [[r.run_key, f"{r.experiment} · {r.strategy} v{r.strategy_version} {r.params if r.params != '{}' else ''}"] for r in idx.itertuples()]
        rk = p.get("rk") or opts[0][0]
        res = X.load_result(rk)
        if res is None:
            raise UserError("result file missing")
        ev, vd = res["evaluation"], res["verdict"]
        hold = p.get("hold") == "true"
        rows = []
        for per in ["development", "validation"] + (["holdout"] if hold else []):
            for hh, s_ in (ev["by_period"].get(per) or {}).items():
                if not s_.get("signals"):
                    continue
                rows.append(dict(period=V.PERIOD_LABEL[per], horizon=int(hh), signals=s_["signals"], stocks=s_["stocks"], dates=s_["dates"],
                                 win_pct=s_["positive_rate"], baseline_win_pct=s_["baseline_positive_rate"], lift_pp=s_["lift_pp"], mean_net=s_["mean_net"],
                                 excess=s_["excess"], t=s_["t_excess"], p5_net=s_["p5_net"], vs_nifty=s_["bench_relative"], warning=s_["warning"]))
        secs = [dict(kind="note", tone="info" if vd["validated"] else "warn",
                     text=f"{'Validated research candidate' if vd['validated'] else 'Not validated'} at {vd['horizon']} sessions (development and validation only; holdout never used). "
                          f"Tested as configuration #{vd['configs_tested']}."),
                table("Checks", pd.DataFrame(vd["checks"]), {"ok": "bool"}, tools=False),
                table("By period and horizon (net after costs; excess vs eligible stocks on the same days)", pd.DataFrame(rows).round(2),
                      {"excess": "snum2", "mean_net": "snum2", "vs_nifty": "snum2", "warning": "long"}, tools=False)]
        hz = res["primary_horizon"]
        for key, title in (("by_year", "By year"), ("by_regime", "By market state"), ("by_liquidity", "By liquidity")):
            d = pd.DataFrame(ev[key])
            if len(d):
                d = d[(d.horizon == hz) & d.period.isin(["development", "validation"] + (["holdout"] if hold else []))]
                secs.append(table(f"{title} (horizon {hz})", d, {"excess": "snum2"}, tools=False))
        secs.append(dict(kind="md", title="Exact configuration", text="```\n" + json.dumps({k: res[k] for k in ("strategy", "strategy_version", "params", "config_id", "data_version", "run_key", "finished")}, indent=1, default=str) + "\n```"))
        return dict(options=opts, rk=rk, sections=secs)
    if tab == "strategies":
        strategies = X.load_strategies()
        en = {e["key"]: e for e in X.enabled()}
        out = [dict(key=k, status=X.status_of(k), enabled=k in en, description=s_.get("description"), hypothesis=s_.get("hypothesis"),
                    file=s_.get("file"), evaluate_on=s_.get("evaluate_on"), horizon=s_.get("primary_horizon")) for k, s_ in strategies.items()]
        return dict(strategies=out)
    if tab == "experiments":
        strategies, exps = X.load_strategies(), X.load_experiments()
        rows = []
        for name, e in exps.items():
            try:
                n, err = len(X.expand(e, strategies)), ""
            except SC.ConfigError as ex:
                n, err = 0, str(ex)
            rows.append(dict(experiment=name, description=e.get("description", ""), configurations=n, problem=err))
        return dict(sections=[table("Experiments", pd.DataFrame(rows), {"description": "long", "problem": "long"}, tools=False)])
    raise UserError("unknown tab")


@route("POST", "screener-research/enable")
def sr_enable(_, body):
    from screener import experiments as X, config as SC
    key, on = body.get("key"), bool(body.get("on"))
    strategies = X.load_strategies()
    if key not in strategies:
        raise UserError("unknown strategy")
    if not on:
        X.set_enabled(key, False)
        return dict(ok=True)
    s_ = strategies[key]
    idx = X.index()
    own = idx[(idx.strategy == s_["name"]) & (idx.status == "ok") & (idx.config_id == SC.config_id(s_["config"]))] if len(idx) else idx
    rk = own.sort_values("finished").run_key.iat[-1] if len(own) else None
    status = X.status_of(key)
    X.set_enabled(key, True, rk, note="" if status == "validated" else "enabled before validation")
    return dict(ok=True)


# ---------------- validation ----------------
@route("GET", "validation")
def validation(_):
    out = q("select * from outcomes")
    if out.empty:
        return dict(sections=[dict(kind="note", text="No recommendations tracked yet. Validation starts after the first approved trade.")])
    closed = out[out.status == "closed"].copy()
    secs = [dict(kind="kpis", items=[dict(label="Recommendations tracked", value=str(len(out))), dict(label="Closed", value=str(len(closed))),
                                     dict(label="Win rate", value=f"{(closed.ret_pct > 0).mean():.0%}" if len(closed) else "—"),
                                     dict(label="Avg return per trade", value=f"{closed.ret_pct.mean():+.2f}%" if len(closed) else "—")])]
    if len(closed):
        cl = closed.sort_values("exit_date")
        secs.append(dict(kind="chart", type="line", title="Paper P&L, cumulative % (equal size per trade)", zero=True, x=pd.to_datetime(cl.exit_date).dt.strftime("%Y-%m-%d").tolist(),
                         series=[dict(name="", y=cl.ret_pct.cumsum().round(2).tolist(), area=True)]))
        s_ = closed.groupby("strategy").agg(trades=("ret_pct", "size"), win_pct=("ret_pct", lambda r: round(100 * (r > 0).mean(), 1)), live_exp_pct=("ret_pct", "mean")).round(2).reset_index()
        cards = q("select strategy, trades, expectancy_pct from strategy_cards where active")
        if len(cards):
            bt = cards.groupby("strategy").apply(lambda g: (g.expectancy_pct * g.trades).sum() / g.trades.sum()).round(2)
            s_["backtest_exp_pct"] = s_.strategy.map(bt)
        secs.append(table("Live vs backtest", s_, {"live_exp_pct": "snum2", "backtest_exp_pct": "snum2"}, tools=False,
                          note="Rules change only after 30+ closed trades per strategy, and only with your approval."))
    secs.append(table("All tracked recommendations", out.sort_values("decision_date", ascending=False), csv="outcomes"))
    return dict(sections=secs)


# ---------------- positions ----------------
POS_COLS = ["symbol", "sector", "strategy", "entry_date", "entry_price", "quantity", "stop", "target"]


def _account():
    a = dict(capital=1_000_000, equity=None, peak_equity=None, today_pnl=0.0)
    if C.ACCOUNT_JSON.exists():
        a.update(json.loads(C.ACCOUNT_JSON.read_text()))
    a["equity"] = a["equity"] or a["capital"]
    a["peak_equity"] = max(a["peak_equity"] or a["equity"], a["equity"])
    return a


def _positions():
    return pd.read_csv(C.POSITIONS_CSV) if C.POSITIONS_CSV.exists() else pd.DataFrame(columns=POS_COLS)


@route("GET", "positions")
def positions(_):
    from core.strategies import STRATEGIES
    a, pos = _account(), _positions()
    reg = q("select strategy from strategies")
    opts = list(dict.fromkeys(list(STRATEGIES) + (reg.strategy.tolist() if len(reg) else []) + ["manual"]))
    live, sec = pd.DataFrame(), pd.DataFrame()
    px = prices()
    if len(pos) and px is not None:
        lastpx = px.sort_values("date").groupby("symbol").tail(1).set_index("symbol").close
        p_ = pos.copy()
        for c in ("entry_price", "quantity", "stop"):
            p_[c] = pd.to_numeric(p_[c], errors="coerce")
        p_["last"] = p_.symbol.map(lastpx)
        p_["value"] = (p_.entry_price * p_.quantity).round(0)
        p_["pnl_rs"] = ((p_["last"] - p_.entry_price) * p_.quantity).round(0)
        p_["pnl_pct"] = (100 * (p_["last"] / p_.entry_price - 1)).round(2)
        p_["to_stop_pct"] = (100 * (p_.stop / p_["last"] - 1)).round(2)
        live = p_[["symbol", "strategy", "entry_price", "last", "quantity", "value", "pnl_rs", "pnl_pct", "to_stop_pct"]]
        sec = p_.groupby("sector").agg(positions=("symbol", "count"), value=("value", "sum")).reset_index()
        sec["limit"] = C.RISK["max_per_sector"]
    dd = 1 - a["equity"] / a["peak_equity"]
    momentum = q("select strategy, status from strategies where family = 'momentum' and status in ('paper', 'active')")
    return dict(account=a, positions=pos.fillna("").astype(str).to_dict("records"), columns=POS_COLS, strategy_options=opts,
                risk=C.RISK, drawdown=dd, live=live, sectors=sec,
                deployed=float(live.value.sum() / a["equity"]) if len(live) else 0.0,
                momentum=[dict(strategy=r.strategy, status=r.status) for r in momentum.itertuples()])


@route("POST", "positions/save")
def positions_save(_, body):
    rows = body.get("rows") or []
    d = pd.DataFrame(rows, columns=POS_COLS)
    d = d.replace("", np.nan).dropna(how="all")
    if len(d):
        d["symbol"] = d.symbol.astype(str).str.strip().str.upper()
        for c in ("entry_price", "quantity", "stop", "target"):
            bad = pd.to_numeric(d[c], errors="coerce").isna() & d[c].notna()
            if bad.any():
                raise UserError(f"{c} must be a number (row {int(bad.idxmax()) + 1})")
        if d.entry_date.notna().any() and pd.to_datetime(d.entry_date.dropna(), errors="coerce").isna().any():
            raise UserError("entry_date must be YYYY-MM-DD")
    d.to_csv(C.POSITIONS_CSV, index=False)
    return dict(saved=len(d))


@route("POST", "account/save")
def account_save(_, body):
    old = json.loads(C.ACCOUNT_JSON.read_text()) if C.ACCOUNT_JSON.exists() else {}
    try:
        new = {k: float(body[k]) for k in ("capital", "equity", "peak_equity", "today_pnl")}
    except (KeyError, TypeError, ValueError):
        raise UserError("capital, equity, peak equity and today's P&L must be numbers")
    new["peak_equity"] = max(new["peak_equity"], new["equity"])
    C.ACCOUNT_JSON.write_text(json.dumps({**old, **new}))
    return dict(ok=True)


@route("GET", "orders")
def orders(p):
    from agents import orders as O
    sid, kind = p.get("strategy"), "hold" if p.get("list", "this") == "this" else "next (provisional)"
    tg = q("select * from portfolio_targets where strategy = ? and cast(date as date) = (select max(cast(date as date)) from portfolio_targets where strategy = ?)", [sid, sid])
    cap = O.allocation(sid)
    if tg.empty:
        return dict(allocation=cap, empty=True)
    px = prices()
    asof = pd.Timestamp(tg.date.iat[0])
    due = O.rebalance_window(asof, sorted(px.date.unique())) if px is not None else False
    if cap <= 0:
        return dict(allocation=cap, asof=asof, next=O.next_rebalance(asof), due=due, needs_money=True)
    last = px[px.date == px.date.max()].set_index("symbol").close
    pos = _positions()
    if len(pos):
        pos["quantity"] = pd.to_numeric(pos.quantity, errors="coerce").fillna(0)
    o = O.orders(tg, last, pos if len(pos) else pd.DataFrame(columns=["strategy", "symbol", "quantity"]), sid, cap, kind)
    return dict(allocation=cap, asof=asof, next=O.next_rebalance(asof), due=due, summary=O.summary(o, cap), orders=o.drop(columns=["strategy"]))


@route("POST", "orders/allocation")
def orders_allocation(_, body):
    from agents import orders as O
    try:
        O.set_allocation(str(body["strategy"]), float(body["amount"]))
    except (KeyError, ValueError):
        raise UserError("amount must be a number")
    return dict(ok=True)


@route("POST", "orders/apply")
def orders_apply(_, body):
    """'I placed these orders': this strategy's rows in positions.csv become the target quantities at the last close."""
    from agents import orders as O
    sid = str(body.get("strategy"))
    tg = q("select * from portfolio_targets where strategy = ? and cast(date as date) = (select max(cast(date as date)) from portfolio_targets where strategy = ?)", [sid, sid])
    if tg.empty:
        raise UserError("no targets")
    px = prices()
    last = px[px.date == px.date.max()].set_index("symbol").close
    pos = _positions()
    p_ = pos.copy()
    if len(p_):
        p_["quantity"] = pd.to_numeric(p_.quantity, errors="coerce").fillna(0)
    o = O.orders(tg, last, p_ if len(p_) else pd.DataFrame(columns=["strategy", "symbol", "quantity"]), sid, O.allocation(sid), "hold")
    keep = pos[pos.strategy != sid] if len(pos) else pos
    u = universe()
    sec = dict(zip(u.Symbol, u.Industry))
    new = o[o.target_qty > 0].assign(sector=lambda x: x.symbol.map(sec), strategy=sid, entry_date=str(px.date.max().date()),
                                     entry_price=lambda x: x.last_price, quantity=lambda x: x.target_qty.where(x.action != "HOLD", x.held_qty), stop=None, target=None)
    old_px = pos[pos.strategy == sid].set_index("symbol")[["entry_date", "entry_price"]] if len(pos) else None
    if old_px is not None and len(old_px):
        hold = new.symbol.isin(old_px.index)
        new.loc[hold, "entry_date"] = new.loc[hold, "symbol"].map(old_px.entry_date)
        new.loc[hold, "entry_price"] = new.loc[hold, "symbol"].map(old_px.entry_price)
    pd.concat([keep, new[POS_COLS]], ignore_index=True).to_csv(C.POSITIONS_CSV, index=False)
    return dict(ok=True)


# ---------------- reports & logs ----------------
@route("GET", "records")
def records(p):
    tab = p.get("tab", "reports")
    if tab == "reports":
        reps = sorted(C.REPORTS.glob("*.md"), reverse=True) if C.REPORTS.exists() else []
        names = [r.stem for r in reps]
        f = p.get("name") or (names[0] if names else None)
        text = (C.REPORTS / f"{f}.md").read_text() if f and f in names else "No reports yet."
        return dict(options=names, name=f, text=text)
    if tab == "logs":
        logs = sorted((C.BASE / "logs").glob("*.log"), reverse=True)
        names = [x.stem for x in logs]
        f = p.get("name") or (names[0] if names else None)
        lines = (C.BASE / "logs" / f"{f}.log").read_text(errors="ignore").splitlines() if f in names else []
        ag = p.get("agent", "all")
        if ag != "all":
            lines = [l for l in lines if f" {ag} " in l or f"] {ag}" in l]
        if p.get("errors") == "true":
            lines = [l for l in lines if "ERROR" in l or "FAILED" in l]
        return dict(options=names, name=f, lines=lines[-600:])
    if tab == "jobs":
        h = jobs.history(60)
        return dict(jobs=[{k: v for k, v in r.items() if k != "path"} for r in h])
    raise UserError("unknown tab")


@route("GET", "job/file")
def job_file(p):
    name = p.get("file", "")
    f = (C.BASE / "logs" / "jobs" / name)
    if "/" in name or not f.exists():
        raise UserError("no such job log")
    return dict(file=name, lines=jobs.tail(f, 600).splitlines())
