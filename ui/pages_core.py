"""Overview, agents, strategies, portfolio, validation, reports, data pages."""
from ui.common import *  # noqa: F401,F403


# ---------------- pages ----------------
def page_overview():
    reg = q("select * from regime_daily order by date desc limit 1")
    st.title("Overview")
    from ui.data_health import status_line
    lvl, line = status_line()
    (st.caption if lvl == "ok" else st.warning if lvl == "warn" else st.error)(f"🩺 {line}")
    if not has(reg):
        start_here()
    else:
        r = reg.iloc[0]
        st.caption(f"Latest bar: {pd.Timestamp(r.date).date()}")
        c = st.columns(5)
        short = {"up_calm": "Up · calm", "up_volatile": "Up · volatile", "down_calm": "Down · calm", "down_volatile": "Down · volatile"}
        c[0].metric("Market state", short.get(r.market_state, r.market_state), help=f"{STATE_LABEL.get(r.market_state, r.market_state)} for {r.days_in_state} days")
        c[0].caption(f"for {r.days_in_state} days")
        c[1].metric("Regime score", f"{r.regime_score}/100",
                    help="Market health, for information: it does not decide trades (the market state and the tested playbook do). "
                         "Four parts of 0–25: long trend (Nifty vs 200-day: −5%→0, +5%→25), short trend (vs 50-day: −3%→0, +3%→25), "
                         "volatility (VIX vs its 1-year median: 1.3×→0, 0.8×→25), breadth (stocks above 200-day: 30%→0, 70%→25).")
        if "score_trend200" in r and pd.notna(r.score_trend200):
            c[1].caption(f"trend {r.score_trend200:.0f}+{r.score_trend50:.0f} · VIX {r.score_volatility:.0f} · breadth {r.score_breadth:.0f} (each /25)")
        c[2].metric("Nifty vs 200-day", f"{r.nifty_vs_200d_pct:+.1f}%")
        c[3].metric("India VIX", "—" if pd.isna(r.vix) else f"{r.vix:.1f}")
        if pd.notna(r.vix_median):
            c[3].caption(f"1-year median {r.vix_median:.1f}")
        c[4].metric("Size multiplier", r.size_multiplier)
        if r.note:
            st.warning(r.note)
        st.write(f"**Active strategies:** {r.active_strategies or 'none — the playbook is cash in this state'}")

    pend = q("select count(*) n from proposals where status = 'pending'")
    if has(pend) and pend.n.iat[0] > 0:
        st.warning(f"**{pend.n.iat[0]} proposal(s) waiting for your approval** — open Settings → Approvals.")
    _backdrop()
    st.subheader("Agent health")
    lr = last_runs()
    lr = lr.set_index("agent") if has(lr) else pd.DataFrame()
    cols = st.columns(4)
    for i, a in enumerate([a for a in AGENTS]):
        k = "data" if a["key"] == "fetch" else a["key"]
        with cols[i % 4]:
            if not a["built"]:
                st.markdown(f"⚪ **{a['name']}**  \n<span style='color:{REF}'>not built yet</span>", unsafe_allow_html=True)
            elif k in ("macro-all", "market-report", "search-index", "debate"):
                icon, when = _extra_status(k)
                st.markdown(f"{icon} **{a['name']}**  \n<span style='color:{REF}'>{when}</span>", unsafe_allow_html=True)
            elif k == "announcements":
                from core import announcements as ann, news as newsmod
                info, (_, _, nw) = ann.last_fetch(), newsmod.load()
                ok = info and not info.get("errors")
                icon = "🟢" if ok and nw else ("🟡" if (info or nw) else "⚫")
                when = f"news {str(nw)[5:16] if nw else 'never'} · filings {info['when'][5:16] if info else 'never'}"
                st.markdown(f"{icon} **{a['name']}**  \n<span style='color:{REF}'>{when}</span>", unsafe_allow_html=True)
                if info and info.get("errors"):
                    st.caption("Last filings fetch had errors: see Screener → NSE announcements")
            elif k in lr.index:
                row = lr.loc[k]
                icon = "🟢" if row.status == "ok" else ("🔴" if row.status == "error" else "🟡")
                st.markdown(f"{icon} **{a['name']}**  \n<span style='color:{REF}'>{pd.Timestamp(row.started):%d %b %H:%M}</span>", unsafe_allow_html=True)
                if row.status == "error":
                    st.caption(f"Failed: {str(row.message)[:90]}. Run again; if it fails again, see Reports and logs.")
            else:
                st.markdown(f"⚫ **{a['name']}**  \n<span style='color:{REF}'>never run</span>", unsafe_allow_html=True)

    st.subheader("Run")
    c = st.columns(3)
    busy = jobs.current() is not None
    if c[0].button("▶ Fetch data + run all agents", type="primary", disabled=busy, width="stretch", key="ov1"):
        launch("fetch+pipeline")
    if c[1].button("Run all agents", disabled=busy, width="stretch", key="ov2"):
        launch("pipeline")
    if c[2].button("Run backtest + analysis", disabled=busy, width="stretch", key="ov3"):
        launch("backtest")
    with st.expander("Job output", expanded=busy):
        job_panel("ov")

    if has(reg):
        d = reg.iloc[0].date
        st.subheader("Trades for the next open")
        show(q("select symbol, strategy_name, sector, entry, stop, target, exit_rule, quantity, value, risk_rs, setup_score "
               "from decisions where cast(date as date) = cast(? as date) and approved order by setup_score desc", [d]),
             "No approved trades." if r.active_strategies else "No trades: no strategy has a tested edge in this market state.")
        tg = q("select * from portfolio_targets where cast(date as date) = cast(? as date)", [d])
        if has(tg):
            for sid, g in tg.groupby("strategy"):
                st.subheader(f"Portfolio strategy {sid} (paper)")
                o = q("select * from orders where cast(date as date) = cast(? as date) and strategy = ? and list = 'this month'", [d, sid])
                if has(o):
                    act = o[o.action.isin(["BUY", "SELL", "ADD", "TRIM"])]
                    st.markdown(f"**Orders for your ₹{o.capital.iat[0]:,.0f}** · next rebalance {pd.Timestamp(o.next_rebalance.iat[0]).date()} "
                                f"· {len(act)} to place, {int((o.action == 'HOLD').sum())} already right")
                    show(act[["action", "symbol", "order_qty", "last_price", "order_value", "held_qty", "target_qty", "reason"]],
                         "Nothing to trade: your holdings match the targets.")
                else:
                    st.caption("Set the money for this strategy on **Positions and risk** to get share quantities.")
                with st.expander(f"This month's holdings ({int((g.portfolio == 'hold').sum())} stocks, target weights)"):
                    show(g[g.portfolio == "hold"][["symbol", "weight_pct"]])
        st.subheader("Exits due")
        show(q("select symbol, strategy, days_held, action, detail from exits_due where cast(date as date) = cast(? as date) and action <> 'hold'", [d]),
             "No exits due.")


def start_here():
    """Shown until the first successful daily run: what has happened and the one thing to do next."""
    from ui.data_health import data_age
    from core import announcements as ann, news as newsmod
    last, age, log = data_age()
    lr = last_runs()
    lr = lr.set_index("agent") if has(lr) else pd.DataFrame()
    failed = lr[lr.status == "error"] if len(lr) else lr
    _, _, nw = newsmod.load()
    info = ann.last_fetch()
    with st.container(border=True):
        st.markdown("#### Start here: the agents haven't completed a daily run yet")
        steps = [
            ("✅" if last is not None and age <= 4 else "⚠️", "Prices",
             f"to {last.date()} ({age} day(s) old; weekends and holidays count)" if last is not None else "not downloaded"),
            ("❌" if len(failed) else "⚪", "Agents",
             f"last attempt failed on {pd.Timestamp(failed.started.max()):%d %b %H:%M} ({', '.join(failed.index)}: "
             f"{str(failed.message.iat[0])[:60]}). That error is fixed; run them again." if len(failed) else "not run yet"),
            ("✅" if nw else "⚪", "News", f"downloaded {str(nw)[:16]}" if nw else "not downloaded yet"),
            ("✅" if info and not info.get("errors") else ("⚠️" if info else "⚪"), "NSE announcements",
             (f"fetched {info['when'][:16]}" + (" with errors" if info.get("errors") else "")) if info else "not fetched yet"),
        ]
        for icon, what, detail in steps:
            st.markdown(f"{icon} **{what}**: {detail}")
        busy = jobs.current() is not None
        c = st.columns([2, 2, 3])
        if c[0].button("▶ Fetch data + run all agents", type="primary", disabled=busy, width="stretch", key="sh1",
                       help="Updates prices (a few minutes), news and announcements, then runs every agent"):
            launch("fetch+pipeline")
        if c[1].button("Run all agents now", disabled=busy, width="stretch", key="sh2",
                       help="Uses the prices you already have; about a minute"):
            launch("pipeline")
        if busy:
            c[2].info(f"Running: {jobs.current()['name']}. This page updates when it finishes.")
        else:
            c[2].caption("After it finishes this page shows the market state, today's trades, the screener shortlist and momentum orders.")


def page_agents():
    st.title("Agents")
    st.caption("Each agent reads the previous agent's output from the database. After new data, run them in order, or use Run all agents.")
    tabs = st.tabs([("✅ " if a["built"] else "○ ") + a["name"] for a in AGENTS])
    busy = jobs.current() is not None
    lr = last_runs()
    lr = lr.set_index("agent") if has(lr) else pd.DataFrame()
    for tab, a in zip(tabs, AGENTS):
        with tab:
            k = "data" if a["key"] == "fetch" else a["key"]
            c = st.columns([3, 1])
            c[0].markdown(f"**{a['name']}** · {a['kind']}  \n{a['what']}  \n`{a['io']}`")
            if not a["built"]:
                c[1].info("Not built yet")
                continue
            if a["key"] == "debate":
                if "debate-lab" in NAV:
                    c[1].page_link(NAV["debate-lab"], label="Open Market Debate Lab", icon="⚖️")
                st.caption(" ".join(_extra_status("debate")))
                continue
            if c[1].button(RUN_LABEL[a["key"]], key=f"run-{a['key']}", type="primary", disabled=busy, width="stretch"):
                launch(a["key"])
            if a["key"] == "strategy" and c[1].button("Run research", key="run-strategy-research", disabled=busy, width="stretch",
                                                      help="Re-runs the momentum study; files a proposal if a strategy passes."):
                launch("strategy-research")
            if k in lr.index:
                row = lr.loc[k]
                m = st.columns(4)
                m[0].metric("Last status", {"ok": "✅ ok", "error": "❌ error"}.get(row.status, row.status))
                m[1].metric("Last run", f"{pd.Timestamp(row.started):%d %b %H:%M}")
                m[2].metric("Took", f"{row.seconds:.0f}s" if pd.notna(row.seconds) else "—")
                m[3].metric("Rows out", "—" if pd.isna(row.rows_out) else int(row.rows_out))
                if row.message:
                    (st.error if row.status == "error" else st.caption)(row.message)
            elif k in ("macro-all", "market-report", "search-index"):
                icon, when = _extra_status(k)
                st.caption(f"{icon} {when}")
            else:
                st.caption("Never run.")
            st.markdown("**Output**")
            if a["key"] in ("technical", "risk"):
                t = a["table"]
                dates = q(f"select distinct cast(date as date) d from {t} order by d desc")
                if has(dates):
                    dsel = st.selectbox("Date", dates.d.tolist(), key=f"d-{t}")
                    show(q(f"select * exclude (run_id) from {t} where cast(date as date) = ? order by setup_score desc", [dsel]))
                else:
                    st.caption("No output yet.")
            elif a["key"] == "strategy":
                show(q("select strategy, name, family, status, since, note from strategies order by family, strategy"), "Registry empty: run the strategy agent.")
                pend = q("select title, rationale, created from proposals where status = 'pending'")
                if has(pend):
                    st.warning(f"{len(pend)} proposal(s) waiting — see Settings → Approvals.")
                rr = q("select status, started, message from agent_runs where agent = 'strategy_research' order by started desc limit 1")
                if has(rr):
                    st.caption(f"Last research run: {pd.Timestamp(rr.started.iat[0]):%d %b %H:%M} — {rr.message.iat[0]}")
            elif a["key"] == "regime":
                show(q("select date, market_state, days_in_state, regime_score, size_multiplier, active_strategies, nifty, vix, breadth_200, note "
                       "from regime_daily order by date desc"))
            elif a["key"] == "validation":
                show(q("select * exclude (run_id) from outcomes order by decision_date desc"))
            elif a["key"] == "backtest":
                show(q("select strategy, name, state, trades, expectancy_pct, edge_pct, t_stat, active from strategy_cards order by strategy, state"))
            elif a["key"] == "report":
                reps = sorted(C.REPORTS.glob("*.md"), reverse=True) if C.REPORTS.exists() else []
                if reps:
                    st.markdown(reps[0].read_text())
                else:
                    st.caption("No report yet.")
            elif a["key"] == "fetch":
                px = prices()
                if px is not None:
                    st.caption(f"{px.symbol.nunique()} stocks, {len(px):,} rows, {px.date.min().date()} to {px.date.max().date()}")
            with st.expander("Run history"):
                show(q("select started, status, seconds, rows_out, message, for_date, run_id from agent_runs where agent = ? order by started desc limit 50", [k]))
            with st.expander("Job output", expanded=bool(jobs.current() and jobs.current()["name"] in (a["key"], "pipeline", "fetch+pipeline"))):
                job_panel(a["key"])


def page_analysis():
    st.title("Strategy analysis")
    s = acsv("strategies.csv")
    if s is None:
        st.info("No analysis yet. Run **Backtest + analysis** (Agents page or Overview).")
        if st.button("Run backtest + analysis", disabled=jobs.current() is not None):
            launch("backtest")
        return

    tr, t0, t1, t2, t3, t4, t5 = st.tabs(["Report", "Summary", "Scorecard", "Why no edge", "When they work", "Strategy rules", "Momentum research"])
    from ui import report as R
    with tr:
        R.report_tab(s)
    with t0:
        summary_tab(s)
    with t5:
        momentum_tab()
    with t1:
        st.dataframe(s.drop(columns=["states_with_edge"]), width="stretch", hide_index=True, column_config={
            "net_pct": st.column_config.NumberColumn("Net / trade %", format="%+.3f"),
            "gross_pct": st.column_config.NumberColumn("Gross / trade %", format="%+.3f"),
            "win_pct": st.column_config.NumberColumn("Win %", format="%.1f"),
            "avg_win_pct": st.column_config.NumberColumn("Avg win %", format="%+.2f"),
            "avg_loss_pct": st.column_config.NumberColumn("Avg loss %", format="%+.2f"),
            "cagr_pct": st.column_config.NumberColumn("CAGR %", format="%+.1f"),
            "max_dd_pct": st.column_config.NumberColumn("Max DD %", format="%.1f"),
            "trades": st.column_config.NumberColumn("Trades", format="%d")})
        cards = q("select strategy, state, active from strategy_cards")
        if has(cards):
            play = cards[cards.active].groupby("state").strategy.apply(lambda x: ", ".join(sorted(x))).reindex(list(STATE_LABEL)).fillna("cash")
            st.markdown("**Playbook selected by the backtest**")
            st.dataframe(play.rename(index=STATE_LABEL).to_frame("active strategies"), width="stretch")
    with t2:
        a, b = st.columns(2)
        with a:
            st.markdown("**The gross edge is about the size of the cost**")
            st.caption("Average return per trade before costs vs the round-trip cost.")
            st.plotly_chart(R.chart_gross(s), width="stretch")
        with b:
            st.markdown("**Momentum pays after 20–60 days; our trades exit within 10**")
            st.caption("Return after the signal minus buying any liquid stock, by days held (no stops, no costs).")
            hz = acsv("horizon.csv")
            if hz is not None:
                st.plotly_chart(R.chart_horizon(hz), width="stretch")
        a, b = st.columns(2)
        with a:
            st.markdown("**Most trades end on the clock or at a stop**")
            ex = acsv("exits.csv")
            if ex is not None:
                st.plotly_chart(R.chart_exits(ex), width="stretch")
            stp = acsv("stops.csv")
            if stp is not None:
                st.caption(f"Stops are not the problem: only {stp.recovered_10d_pct.iat[0]}% of {stp.stopped_trades.iat[0]:,} stopped trades would have been in profit 10 days later.")
        with b:
            st.markdown("**No strategy grows the account**")
            st.caption("₹10 lakh, 10 equal positions, weekly value.")
            eqw = acsv("equity_weekly.csv")
            if eqw is not None:
                pick = st.multiselect("Highlight", sorted(eqw.strategy.unique()), default=[], key="eqpick")
                st.plotly_chart(R.chart_equity(eqw, pick), width="stretch")
    with t3:
        a, b = st.columns(2)
        stt = acsv("states.csv")
        order = s.strategy.tolist()
        with a:
            st.markdown("**Net return per trade by market state**")
            if stt is not None:
                st.plotly_chart(R.heat_states(stt, order), width="stretch")
                st.caption("The positive column is positive for random picks too; no edge there is statistically significant.")
        with b:
            st.markdown("**Net return per trade by research period**")
            per = acsv("periods.csv")
            if per is not None:
                st.plotly_chart(R.heat_periods(per, order), width="stretch")
        st.markdown("**Year by year: results follow the market**")
        yr, mk = acsv("years.csv"), acsv("market_years.csv")
        if yr is not None:
            c1, c2 = st.columns([3, 1.2])
            c1.plotly_chart(R.heat_years(yr, order), width="stretch")
            if mk is not None:
                c2.caption("Market in the same year")
                c2.dataframe(mk.rename(columns={"year": "Year", "nifty_return_pct": "Nifty 50", "random_stock_5d_gross_pct": "Random stock, 5d"}),
                             hide_index=True, width="stretch", height=40 + 35 * len(mk),
                             column_config={"Year": st.column_config.NumberColumn(format="%d"),
                                            "Nifty 50": st.column_config.NumberColumn(format="%+.1f%%"),
                                            "Random stock, 5d": st.column_config.NumberColumn(format="%+.2f%%")})
    with t4:
        rules = []
        for sid, x in STRATEGIES.items():
            stop = f"{x['stop'][1]}× ATR" if x["stop"][0] == "atr" else x["stop"][1].replace("_", " ")
            tgt = "none" if x["target"] is None else (f"{x['target'][1]}× ATR" if x["target"][0] == "atr" else f"{x['target'][2]}× {x['target'][1]}")
            rules.append(dict(id=sid, strategy=x["name"], stop=stop, target=tgt, rule_exits=", ".join(x["exits"]) or "—", max_days=x["max_days"]))
        st.dataframe(pd.DataFrame(rules), hide_index=True, width="stretch")
        st.caption("Entry signals are defined in core/strategies.py. Signal on the close, entry at the next open, 0.3% round-trip cost.")


RULE = {
    "S1": "Buys a close above the 55-day high on 2× normal volume, in a stock above its 200-day average. Holds up to 10 days.",
    "S2": "Buys the top 10% of stocks by 3-month strength vs Nifty when they make a new 20-day closing high. Holds up to 10 days.",
    "S3": "Buys a breakout from a tight 7-day range on 1.5× volume. Holds up to 7 days.",
    "S4": "Buys a close at a new 52-week high; sells when it closes below its 10-day average. Up to 10 days.",
    "S5": "Buys a sharp 3-day dip (RSI below 15) in a stock in a long-term uptrend; sells when it closes back above its 5-day average.",
    "S6": "Buys a sector leader that pulls back to its 20-day average and bounces. Holds up to 10 days.",
    "S10": "Buys the 2 strongest stocks in the 3 strongest sectors of the past month. Holds up to 10 days.",
    "S11": "Built for falling markets: buys a liquid stock that is extremely oversold (3-day RSI under 10) but closed in the upper half of its day. Sells when it closes back above its 5-day average, or after 5 days.",
    "S12": "Built for falling markets: buys a calm, relatively strong stock (low volatility, top 20% on 3-month strength, above its 200-day average) on a short dip. Sells on the rebound above its 5-day average, or after 7 days.",
}


def describe(p, base):
    size = max(["Smaller (< ₹50 cr/day)", "Mid (₹50–200 cr/day)", "Large (> ₹200 cr/day)"], key=lambda c: p.get(c, 0))
    vol = p.median_atr_pct / base.median_atr_pct
    vol_t = "more volatile than average" if vol > 1.15 else ("calmer than average" if vol < 0.9 else "average volatility")
    rs = p.median_rs_pct
    rs_t = "market leaders" if rs >= 0.8 else ("middle of the pack" if rs >= 0.45 else "laggards")
    return (f"{rs_t.capitalize()} (median strength rank {rs:.0%}), up {p.median_3m_return_pct:.0f}% over 3 months "
            f"vs {base.median_3m_return_pct:.0f}% for the market; mostly {size.split(' (')[0].lower()} stocks; {vol_t} "
            f"(daily range {p.median_atr_pct:.1f}% vs {base.median_atr_pct:.1f}%).")


def summary_tab(s):
    prof = acsv("profiles.csv")
    stt = acsv("states.csv")
    reg = q("select strategy, status, family from strategies")
    vjson = C.RESULTS / "strategy" / "momentum_verdict.json"
    verdict = json.loads(vjson.read_text()) if vjson.exists() else None
    mv = pd.read_csv(C.RESULTS / "strategy" / "momentum_variants.csv") if (C.RESULTS / "strategy" / "momentum_variants.csv").exists() else None
    if prof is None:
        st.info("Run Backtest + analysis to build the summary.")
        return
    base = prof[prof.strategy == "ALL"].iloc[0]
    status = dict(zip(reg.strategy, reg.status)) if has(reg) else {}
    rows = []
    for _, p in prof[prof.strategy != "ALL"].iterrows():
        sid = p.strategy
        if p.family == "momentum" and mv is not None and sid in set(mv.variant):
            b = mv[mv.variant == sid].iloc[0]
            rule = (f"Each month-end, buys the 20 liquid stocks with the best {int(b.lookback_m)}-month return"
                    f"{' (skipping the latest month)' if b.skip_m else ''}; holds each batch {int(b.hold_m)} months.")
            works = "All market states (no filter)" if not b.regime_filter else "Only when Nifty is above its 200-day average"
            result = f"{b.all_avg_month_excess_pct:+.2f}% a month vs the market (t {b.all_t_excess:.1f}); {b.all_cagr_pct:.0f}% a year vs {b.all_control_cagr_pct:.0f}%"
            edge = bool(verdict and verdict["chosen"] == sid and verdict["passes"])
        else:
            rule = RULE.get(sid, STRATEGIES.get(sid, {}).get("name", sid))
            act = stt[(stt.strategy == sid) & (stt.active == True)] if stt is not None else pd.DataFrame()
            works = ", ".join(STATE_LABEL.get(x, x) for x in act.state) or "No market state"
            r = s[s.strategy == sid]
            result = f"{r.net_pct.iat[0]:+.2f}% per trade after costs ({int(r.trades.iat[0]):,} trades)" if len(r) else "—"
            edge = len(act) > 0
        rows.append(dict(Strategy=sid, Type="Momentum portfolio" if p.family == "momentum" else "Swing trade",
                         Status=status.get(sid, "—"), Edge="✅ Yes" if edge else "❌ No", **{"How it picks": rule},
                         **{"What kind of stocks": describe(p, base)},
                         **{"Over-weights": p.overweight_sectors if isinstance(p.overweight_sectors, str) and p.overweight_sectors else "No clear sector tilt"},
                         **{"Works in": works, "Tested result": result}))
    tbl = pd.DataFrame(rows).sort_values(["Edge", "Strategy"], ascending=[True, True])

    winners = tbl[tbl.Edge.str.startswith("✅")]
    swing_lose = tbl[(tbl.Type == "Swing trade") & tbl.Edge.str.startswith("❌")]
    lines = [f"**Combined result: {len(winners)} of {len(tbl)} strategies has a tested edge.**"]
    for w in winners.itertuples():
        lines.append(f"- **{w.Strategy}** ({w.Status}) — {w._5[0].lower() + w._5[1:]} Result: {w._9}. It picks: {w._6[0].lower() + w._6[1:]}")
    if len(swing_lose):
        lead = prof[prof.strategy.isin(swing_lose.Strategy) & (prof.median_rs_pct >= 0.8)].strategy.tolist()
        lines.append(f"- **{len(swing_lose)} swing setups have no edge.** "
                     + (f"{', '.join(lead)} pick the same kind of stocks as momentum (strong recent gainers) but sell within 10 days, before the payoff arrives. " if lead and len(winners) else "")
                     + "They stay switched off.")
    if len(winners):
        lines.append("- **Next step:** paper-trade the strategy with an edge for at least 2–3 months before real money; build the announcements agent to look for a second, independent edge.")
    st.markdown("\n".join(lines))
    st.dataframe(tbl, hide_index=True, width="stretch", column_config={
        "How it picks": st.column_config.TextColumn(width="large"), "What kind of stocks": st.column_config.TextColumn(width="large")})

    st.markdown("**Look inside one strategy**")
    sid = st.selectbox("Strategy", tbl.Strategy.tolist(), key="sum-sid")
    sec = acsv("sector_mix.csv")
    a, b = st.columns(2)
    if sec is not None:
        g = sec[(sec.strategy == sid) & (sec.picks >= 10)].sort_values("tilt_pct")
        g = pd.concat([g.head(4), g.tail(6)]).drop_duplicates("sector")
        fig = go.Figure(go.Bar(x=g.tilt_pct, y=g.sector, orientation="h", marker_color=[ACCENT if v > 0 else MUTED for v in g.tilt_pct],
                               customdata=np.stack([g.share_pct, g.universe_pct], axis=1),
                               hovertemplate="%{y}<br>%{customdata[0]:.1f}% of picks vs %{customdata[1]:.1f}% of the market<extra></extra>",
                               text=[f"{v:+.1f}" for v in g.tilt_pct], textposition="outside"))
        fig.update_traces(cliponaxis=False)
        a.markdown("Sector over- and under-weights vs the market (percentage points)")
        a.plotly_chart(fig_base(fig, 340), width="stretch")
    pr = prof.set_index("strategy")
    sizes = ["Smaller (< ₹50 cr/day)", "Mid (₹50–200 cr/day)", "Large (> ₹200 cr/day)"]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=sizes, y=[pr.loc["ALL", c] for c in sizes], name="Market", marker_color=MUTED))
    fig.add_trace(go.Bar(x=sizes, y=[pr.loc[sid, c] for c in sizes], name=sid, marker_color=ACCENT))
    fig.update_yaxes(ticksuffix="%")
    b.markdown("Size of the stocks it picks (by daily trading value)")
    b.plotly_chart(fig_base(fig, 340, legend=True), width="stretch")


@st.cache_data(ttl=600)
def paper_perf(sid, since):
    px = prices()
    idx = pd.read_parquet(C.DATA / "index.parquet")
    idx["date"] = pd.to_datetime(idx.date).dt.tz_localize(None).dt.normalize()
    return strategy_agent.paper_performance(px, idx, sid, since)


def momentum_tab():
    res = C.RESULTS / "strategy"
    vp, mp, jp = res / "momentum_variants.csv", res / "momentum_best_monthly.csv", res / "momentum_verdict.json"
    if not vp.exists():
        st.info("No momentum study yet. Agents → Strategy → Run research.")
        return
    v, m, verdict = pd.read_csv(vp), pd.read_csv(mp), json.loads(jp.read_text())
    best = v[v.variant == verdict["chosen"]].iloc[0]
    ok = verdict["passes"]
    (st.success if ok else st.warning)(
        f"**{verdict['chosen']}** {'passes' if ok else 'does not pass'}: buy the top 20 liquid stocks by "
        f"{int(best.lookback_m)}-month return{' (skipping the last month)' if best.skip_m else ''}, hold each batch "
        f"{int(best.hold_m)} month(s), rebalance monthly. Chosen as the best of {verdict['variants_tested']} variants on 2016–20 only.")
    c = st.columns(4)
    for col, p, lab in zip(c[:3], ("design", "validation", "holdout"), ("2016–20 (searched)", "2021–23 (checked once)", "2024–26 (holdout)")):
        col.metric(lab, f"{best[f'{p}_avg_month_excess_pct']:+.2f}% / month", f"t = {best[f'{p}_t_excess']:.1f}", delta_color="off")
    c[3].metric("CAGR vs control", f"{best.all_cagr_pct:.1f}%", f"control {best.all_control_cagr_pct:.1f}%", delta_color="off")
    st.caption(f"Excess = monthly return after 0.3% per name replaced, minus an equal-weight portfolio of all liquid stocks. "
               f"Best-of-{verdict['variants_tested']} search: a t of {verdict['search_adjusted_t']:.1f} would be needed in 2016–20 alone to rule out luck; "
               f"it scored {verdict['design_t']:.1f}, which is why the later periods matter.")
    m["cum"] = (1 + m.net).cumprod()
    m["cum_ctrl"] = (1 + m.control).cumprod()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=m.month, y=m.cum_ctrl * 10, name="Equal-weight control", line=dict(color=MUTED, width=1.6)))
    fig.add_trace(go.Scatter(x=m.month, y=m.cum * 10, name=verdict["chosen"], line=dict(color=ACCENT, width=2.4)))
    for x0, lab in (("2021-01", "validation"), ("2024-01", "holdout")):
        fig.add_vline(x=x0, line_dash="dot", line_color=REF)
        fig.add_annotation(x=x0, y=1.04, yref="paper", text=lab, showarrow=False, font=dict(color=REF))
    fig.update_yaxes(type="log", title="₹ lakh (log scale)")
    st.markdown("**Growth of ₹10 lakh, after costs**")
    st.plotly_chart(fig_base(fig, 360, legend=True), width="stretch")
    st.markdown("**Caveats**")
    st.markdown("- The stock list is today's Nifty 500, so stocks that fell out of the index are missing. That flatters momentum more than most strategies, because past winners are over-represented.\n"
                "- The 2024–26 result is positive but not statistically significant.\n"
                "- Drawdowns are deep: the worst fall from a peak was "
                f"{best.all_max_dd_pct:.0f}%. Paper trading is the real test before any money goes in.")
    with st.expander(f"All {len(v)} variants tested"):
        cols = ["variant", "design_avg_month_excess_pct", "design_t_excess", "validation_avg_month_excess_pct", "validation_t_excess",
                "holdout_avg_month_excess_pct", "holdout_t_excess", "all_cagr_pct", "all_max_dd_pct", "all_sharpe"]
        st.dataframe(v[cols].sort_values("design_t_excess", ascending=False).round(2), hide_index=True, width="stretch")
    reg = q("select * from strategies where family = 'momentum'")
    if has(reg):
        for r in reg.itertuples():
            if r.status in ("paper", "active"):
                perf = paper_perf(r.strategy, r.since)
                st.markdown(f"**{r.strategy}: paper record since {r.since}**")
                if len(perf):
                    perf = perf.assign(excess_pct=100 * perf.excess, net_pct=100 * perf.net)
                    show(perf[["month", "net_pct", "excess_pct"]].round(2))
                else:
                    st.caption("No completed month since approval yet.")


def page_positions():
    st.title("Positions and risk")
    a = account()
    dd = 1 - a["equity"] / a["peak_equity"]
    pos = pd.read_csv(C.POSITIONS_CSV) if C.POSITIONS_CSV.exists() else pd.DataFrame(columns=["symbol", "sector", "strategy", "entry_date", "entry_price", "quantity", "stop", "target"])
    c = st.columns(4)
    c[0].metric("Equity", f"₹{a['equity']:,.0f}")
    c[1].metric("Drawdown from peak", f"{dd:.1%}", f"half size at {C.RISK['dd_half']:.0%}, stop at {C.RISK['dd_stop']:.0%}", delta_color="off")
    c[2].metric("Open positions", f"{len(pos)}/{C.RISK['max_positions']}")
    c[3].metric("Today's P&L", f"₹{a['today_pnl']:,.0f}", f"cap {C.RISK['daily_loss_cap']:.0%} of equity", delta_color="off")

    st.subheader("Your positions")
    st.caption("Add a row when you buy, delete it when you sell, then Save. The risk agent reads this file.")
    reg_all = q("select strategy from strategies")
    opts = list(dict.fromkeys(list(STRATEGIES) + (reg_all.strategy.tolist() if has(reg_all) else []) + ["manual"]))
    edited = st.data_editor(pos, num_rows="dynamic", width="stretch", hide_index=True, key="pos-editor",
                            column_config={"strategy": st.column_config.SelectboxColumn(options=opts),
                                           "entry_date": st.column_config.TextColumn(help="YYYY-MM-DD")})
    if st.button("Save positions", type="primary"):
        edited.dropna(how="all").to_csv(C.POSITIONS_CSV, index=False)
        st.success("Saved positions.csv")

    px = prices()
    if len(pos) and px is not None:
        lastpx = px.sort_values("date").groupby("symbol").tail(1).set_index("symbol").close
        p = pos.copy()
        p["last"] = p.symbol.map(lastpx)
        p["value"] = (p.entry_price * p.quantity).round(0)
        p["pnl_rs"] = ((p["last"] - p.entry_price) * p.quantity).round(0)
        p["pnl_pct"] = (100 * (p["last"] / p.entry_price - 1)).round(2)
        p["to_stop_pct"] = (100 * (p.stop / p["last"] - 1)).round(2)
        st.subheader("Live view")
        show(p[["symbol", "strategy", "entry_price", "last", "quantity", "value", "pnl_rs", "pnl_pct", "to_stop_pct"]])
        inv = p.value.sum()
        st.subheader("Exposure vs limits")
        sec = p.groupby("sector").agg(positions=("symbol", "count"), value=("value", "sum")).reset_index()
        sec["limit"] = C.RISK["max_per_sector"]
        c = st.columns(2)
        c[0].metric("Capital deployed", f"{inv / a['equity']:.0%}")
        c[1].dataframe(sec, hide_index=True, width="stretch")

    orders_section(pos)

    with st.expander("Account (edit)"):
        with st.form("acct"):
            f = st.columns(4)
            cap = f[0].number_input("Capital", value=float(a["capital"]), step=10000.0)
            eq = f[1].number_input("Equity now", value=float(a["equity"]), step=1000.0)
            pk = f[2].number_input("Peak equity", value=float(a["peak_equity"]), step=1000.0)
            tp = f[3].number_input("Today's P&L", value=float(a["today_pnl"]), step=100.0)
            if st.form_submit_button("Save account"):
                old = json.loads(C.ACCOUNT_JSON.read_text()) if C.ACCOUNT_JSON.exists() else {}
                C.ACCOUNT_JSON.write_text(json.dumps({**old, **dict(capital=cap, equity=eq, peak_equity=max(pk, eq), today_pnl=tp)}))
                st.success("Saved account.json")
    with st.expander("Risk limits"):
        st.json(C.RISK)


def orders_section(pos):
    from agents import orders as O
    reg = q("select strategy, status from strategies where family = 'momentum' and status in ('paper', 'active')")
    if not has(reg):
        return
    st.subheader("Portfolio strategy orders")
    st.caption("Shares to buy and sell so your holdings match the strategy. It rebalances at the first open of each month. "
               "Tag the holdings with the strategy's name in your positions above so they are counted.")
    px = prices()
    for r in reg.itertuples():
        tg = q("select * from portfolio_targets where strategy = ? and cast(date as date) = (select max(cast(date as date)) from portfolio_targets where strategy = ?)",
               [r.strategy, r.strategy])
        with st.container(border=True):
            c = st.columns([2, 2, 2])
            c[0].markdown(f"**{r.strategy}** ({r.status})")
            cap = c[1].number_input("Money for this strategy ₹", 0.0, 1e10, float(O.allocation(r.strategy)), 50000.0, key=f"alloc-{r.strategy}")
            if cap != O.allocation(r.strategy):
                O.set_allocation(r.strategy, cap)
            if not has(tg):
                st.caption("No targets yet: run the agents."); continue
            asof = pd.Timestamp(tg.date.iat[0])
            nxt = O.next_rebalance(asof)
            due = O.rebalance_window(asof, sorted(px.date.unique())) if px is not None else False
            c[2].markdown(f"Targets from {asof.date()}  \n" + ("**Rebalance due now** (first days of the month)" if due else f"Next rebalance **{nxt.date()}**"))
            if cap <= 0:
                st.info("Enter the money you want in this strategy to get share quantities."); continue
            last = px[px.date == px.date.max()].set_index("symbol").close if px is not None else pd.Series(dtype=float)
            p = pos.copy()
            if len(p):
                p["quantity"] = pd.to_numeric(p.quantity, errors="coerce").fillna(0)
            which = st.radio("List", ["This month", "Next month (provisional)"], horizontal=True, key=f"ol-{r.strategy}",
                             help="Next month is ranked on the latest close; it becomes final at month-end.")
            kind = "hold" if which == "This month" else "next (provisional)"
            o = O.orders(tg, last, p if len(p) else pd.DataFrame(columns=["strategy", "symbol", "quantity"]), r.strategy, cap, kind)
            sm = O.summary(o, cap)
            m = st.columns(4)
            m[0].metric("Buy orders", sm["buys"], f"₹{sm['buy_value']:,.0f}", delta_color="off")
            m[1].metric("Sell orders", sm["sells"], f"₹{sm['sell_value']:,.0f}", delta_color="off")
            m[2].metric("Invested after", f"₹{sm['invested_after']:,.0f}")
            m[3].metric("Cash left", f"₹{sm['cash_after']:,.0f}")
            show(o.drop(columns=["strategy"]), "Nothing to trade.", column_config={
                "order_qty": st.column_config.NumberColumn("Order qty", format="%+d"),
                "order_value": st.column_config.NumberColumn("Order ₹", format="%+,.0f"),
                "target_weight_pct": st.column_config.NumberColumn("Weight %", format="%.2f")})
            st.download_button("Download order list (CSV)", o.to_csv(index=False), file_name=f"orders_{r.strategy}_{asof.date()}.csv",
                               key=f"dl-{r.strategy}-{kind}")
            if kind == "hold" and st.button("I placed these orders: update my positions", key=f"apply-{r.strategy}",
                                           help="Sets this strategy's rows in your positions to the target quantities at the last close. Edit prices afterwards if your fills differ."):
                keep = pos[pos.strategy != r.strategy] if len(pos) else pos
                sec = dict(zip(universe().Symbol, universe().Industry))
                new = o[o.target_qty > 0].assign(sector=lambda x: x.symbol.map(sec), strategy=r.strategy, entry_date=str(px.date.max().date()),
                                                 entry_price=lambda x: x.last_price, quantity=lambda x: x.target_qty.where(x.action != "HOLD", x.held_qty),
                                                 stop=None, target=None)
                old_px = pos[pos.strategy == r.strategy].set_index("symbol")[["entry_date", "entry_price"]] if len(pos) else None
                if old_px is not None and len(old_px):
                    hold = new.symbol.isin(old_px.index)
                    new.loc[hold, "entry_date"] = new.loc[hold, "symbol"].map(old_px.entry_date)
                    new.loc[hold, "entry_price"] = new.loc[hold, "symbol"].map(old_px.entry_price)
                out = pd.concat([keep, new[["symbol", "sector", "strategy", "entry_date", "entry_price", "quantity", "stop", "target"]]], ignore_index=True)
                out.to_csv(C.POSITIONS_CSV, index=False)
                st.success("Positions updated."); st.rerun()


def page_validation():
    st.title("Validation")
    out = q("select * from outcomes")
    if not has(out):
        st.info("No recommendations tracked yet. Validation starts after the first approved trade.")
        return
    closed = out[out.status == "closed"].copy()
    c = st.columns(4)
    c[0].metric("Recommendations tracked", len(out))
    c[1].metric("Closed", len(closed))
    c[2].metric("Win rate", f"{(closed.ret_pct > 0).mean():.0%}" if len(closed) else "—")
    c[3].metric("Avg return per trade", pct(closed.ret_pct.mean()) if len(closed) else "—")
    if len(closed):
        cl = closed.sort_values("exit_date")
        fig = go.Figure(go.Scatter(x=pd.to_datetime(cl.exit_date), y=cl.ret_pct.cumsum(), mode="lines", line=dict(color=ACCENT, width=2)))
        fig.add_hline(y=0, line_color=REF)
        st.markdown("**Paper P&L, cumulative % (equal size per trade)**")
        st.plotly_chart(fig_base(fig, 280), width="stretch")
        s = closed.groupby("strategy").agg(trades=("ret_pct", "size"), win_pct=("ret_pct", lambda r: round(100 * (r > 0).mean(), 1)),
                                           live_exp_pct=("ret_pct", "mean")).round(2).reset_index()
        cards = q("select strategy, trades, expectancy_pct from strategy_cards where active")
        if has(cards):
            bt = cards.groupby("strategy").apply(lambda g: (g.expectancy_pct * g.trades).sum() / g.trades.sum()).round(2)
            s["backtest_exp_pct"] = s.strategy.map(bt)
        st.markdown("**Live vs backtest**")
        show(s)
        st.caption("Rules change only after 30+ closed trades per strategy, and only with your approval.")
    status = st.multiselect("Status", sorted(out.status.unique()), default=sorted(out.status.unique()))
    show(out[out.status.isin(status)].sort_values("decision_date", ascending=False))


def page_logs():
    st.title("Reports and logs")
    t1, t2, t3 = st.tabs(["Daily reports", "Agent logs", "Job output"])
    with t1:
        reps = sorted(C.REPORTS.glob("*.md"), reverse=True) if C.REPORTS.exists() else []
        if reps:
            f = st.selectbox("Report", reps, format_func=lambda p: p.stem)
            st.markdown(f.read_text())
        else:
            st.caption("No reports yet.")
    with t2:
        logs = sorted(LOGS.glob("*.log"), reverse=True) if LOGS.exists() else []
        if logs:
            f = st.selectbox("Day", logs, format_func=lambda p: p.stem)
            lines = f.read_text().splitlines()
            ag = st.selectbox("Agent", ["all"] + [a["key"] for a in AGENTS if a["built"]] + ["data", "analysis"])
            if ag != "all":
                lines = [l for l in lines if f" {ag} " in l or f"] {ag}" in l]
            if st.checkbox("Errors only"):
                lines = [l for l in lines if "ERROR" in l or "FAILED" in l]
            st.code("\n".join(lines[-400:]) or "(empty)", language=None)
        else:
            st.caption("No logs yet.")
    with t3:
        h = jobs.history(50)
        if h:
            show(pd.DataFrame(h).drop(columns=["path"]))
            f = st.selectbox("Job", h, format_func=lambda r: f"{r['finished']} · {r['job']} · {r['result']}")
            st.code(jobs.tail(f["path"], 400) or "(empty)", language=None)
        else:
            st.caption("No jobs yet.")


def page_data():
    px = prices()
    if px is None:
        st.info("No price data. Use **Fetch latest data** on the Agents page.")
        return
    last = px.date.max()
    age = (pd.Timestamp.now().normalize() - last).days
    c = st.columns(4)
    c[0].metric("Last bar", str(last.date()), f"{age} days old", delta_color="inverse" if age > 4 else "off")
    c[1].metric("Stocks", px.symbol.nunique())
    c[2].metric("Rows", f"{len(px):,}")
    c[3].metric("From", str(px.date.min().date()))
    if age > 4:
        st.error("Data is stale: the agents will not recommend new trades until it is refreshed.")
    lastd = px.groupby("symbol").date.max()
    st.subheader("Stocks missing the latest bar")
    show(lastd[lastd < last].rename("last_bar").reset_index(), "All stocks are up to date.")
    uni = C.DATA / "universe.csv"
    if uni.exists():
        miss = sorted(set(pd.read_csv(uni).Symbol) - set(px.symbol))
        st.subheader("In the Nifty 500 list but not downloaded")
        st.caption(", ".join(miss) if miss else "None.")
    sa = C.DATA / "split_adjustments.csv"
    st.subheader("Split and bonus adjustments to review")
    if sa.exists() and sa.stat().st_size > 1:
        show(pd.read_csv(sa))
    else:
        st.caption("None recorded.")


def _extra_status(k):
    """(icon, text) for agents that keep their own files rather than agent_runs rows."""
    import json
    try:
        if k == "macro-all":
            from core import macro as M
            lf = M.last_fetch()
            meta = C.RESULTS / "macro" / "meta.json"
            study = json.loads(meta.read_text())["when"][5:16] if meta.exists() else None
            if not lf:
                return "⚫", "never fetched"
            bad = [x for x, v in lf["status"].items() if not str(v).startswith("ok")]
            return ("🟢" if not bad and study else "🟡"), f"data {lf['when'][5:16]} · study {study or 'not run'}" + (f" · {len(bad)} source(s) failing" if bad else "")
        if k == "market-report":
            from research import report_writer as W
            days = W.saved_reports()
            if not days:
                return "⚫", "no report yet"
            rec = W.load_report(days[0])
            used = (rec or {}).get("report", {}).get("writer", {}).get("model")
            return "🟢", f"{days[0]} · {'model ' + used if used else 'template'}"
        if k == "search-index":
            from core import search_index as SI
            pr = SI.progress()
            if not pr["indexed"]:
                return "⚫", "index not built"
            frac = pr["indexed"] / max(pr["total"], 1)
            return ("🟢" if frac > 0.95 else "🟡"), f"{pr['indexed']:,} of {pr['total']:,} indexed"
        if k == "debate":
            from debate import store as DS
            idx = DS.index()
            if idx.empty:
                return "⚫", "no analysis yet"
            last = idx.sort_values("created").iloc[-1]
            n_out = len(DS.outcomes())
            return "🟢", f"{len(idx)} analyses · last {last.symbol} {str(last.created)[5:16].replace('T', ' ')} · {n_out} scored"
    except Exception as e:
        return "🟡", f"needs setup ({type(e).__name__})"
    return "⚫", "—"


def _backdrop():
    """Macro & geopolitics strip on the Overview."""
    try:
        from core import macro as M
        rows = {r["series"]: r for r in M.summary()}
    except Exception:
        rows = {}
    if not rows:
        st.caption("Macro & geopolitics: no data yet (Macro & geopolitics → Fetch macro data).")
        return
    st.subheader("Backdrop")
    cols = st.columns(6)
    for col, sid in zip(cols, ["DCOILBRENTEU", "DEXINUS", "DGS10", "VIXCLS", "GPRD", "FII_NET"]):
        r = rows.get(sid)
        if not r:
            continue
        if r["change_kind"] == "total":
            col.metric(r["label"], f"₹{r['value']:,.0f} cr", f"₹{r['chg_5d']:+,.0f} cr (5 days)" if r["chg_5d"] is not None else None, help=f"As of {r['date']}")
        else:
            unit = " pts" if r["change_kind"] == "points" else "%"
            col.metric(r["label"], f"{r['value']:,.2f}", f"{r['chg_20d']:+.2f}{unit} (1 month)" if r["chg_20d"] is not None else None,
                       delta_color="off", help=f"As of {r['date']}")
    h = M.headline_themes()
    if len(h):
        wk = h[h.published >= pd.Timestamp.now() - pd.Timedelta(days=7)].theme.value_counts().head(5)
        if len(wk):
            st.caption("This week's themes in the headlines: " + " · ".join(f"{M.THEMES[t][0]} ({n})" for t, n in wk.items())
                       + " — see Search for how they reach a stock.")
