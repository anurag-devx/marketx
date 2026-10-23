"""The swing backtest as one detailed report (Strategies → Report), plus the charts it shares with the other tabs.
Everything is read from results/analysis/*.csv, written by `Run backtest + analysis`."""
from ui.common import *  # noqa: F401,F403


# ---------------- charts (also used by the other Strategies tabs) ----------------
def chart_gross(s):
    d = s.sort_values("gross_pct")
    cost = C.COST_RT * 100
    fig = go.Figure(go.Bar(x=d.gross_pct, y=d.strategy + " " + d["name"], orientation="h",
                           marker_color=[ACCENT if v > cost else MUTED for v in d.gross_pct],
                           text=[f"{v:+.2f}%" for v in d.gross_pct], textposition="outside",
                           hovertemplate="%{y}<br>gross %{x:+.3f}% per trade<extra></extra>"))
    fig.update_traces(cliponaxis=False)
    lo = min(0, d.gross_pct.min())
    fig.update_xaxes(range=[lo - 0.08, max(cost + 0.12, d.gross_pct.max() + 0.1)], ticksuffix="%")
    fig.add_vline(x=cost, line_dash="dash", line_color=REF)
    fig.add_annotation(x=cost, y=1.06, yref="paper", text=f"{cost:.1f}% cost", showarrow=False, font=dict(color=REF))
    fig = fig_base(fig, 340)
    fig.update_layout(margin=dict(l=8, r=8, t=30, b=8))
    return fig


def chart_horizon(hz):
    fig = go.Figure()
    for sid, g in hz.groupby("strategy"):
        mom = g.group.iat[0] == "momentum"
        fig.add_trace(go.Scatter(x=g.days.astype(str) + "d", y=g.excess_pct, name=sid, mode="lines+markers",
                                 line=dict(color=ACCENT if mom else MUTED, width=2.5 if mom else 1.5),
                                 hovertemplate=f"{sid}, %{{x}}: %{{y:+.2f}}%<extra></extra>"))
    fig.add_hline(y=0, line_color=REF)
    fig.add_vrect(x0=-0.5, x1=3.3, fillcolor=MUTED, opacity=0.15, line_width=0, annotation_text="our exits", annotation_position="top left")
    fig.update_yaxes(ticksuffix="%")
    return fig_base(fig, 330, legend=True)


def chart_exits(ex):
    fig = go.Figure()
    for name, col in [("Target", GOOD), ("Rule exit", ACCENT), ("Time limit", MUTED), ("Stop", BAD)]:
        g = ex[ex.exit == name]
        fig.add_trace(go.Bar(y=g.strategy, x=g.share_pct, name=name, orientation="h", marker_color=col, opacity=0.8,
                             customdata=g.avg_net_pct, text=[f"{v:.0f}%" for v in g.share_pct], textposition="inside",
                             hovertemplate=f"%{{y}} · {name}: %{{x:.1f}}% of trades<br>avg net %{{customdata:+.2f}}%<extra></extra>"))
    fig.update_layout(barmode="stack")
    fig.update_xaxes(ticksuffix="%")
    return fig_base(fig, 330, legend=True)


def chart_equity(eqw, pick=()):
    fig = go.Figure()
    for sid, g in eqw.groupby("strategy"):
        on = sid in pick
        fig.add_trace(go.Scatter(x=pd.to_datetime(g.date), y=g.equity / 1e5, name=sid, mode="lines",
                                 line=dict(color=ACCENT if on else MUTED, width=2.5 if on else 1.3),
                                 hovertemplate=f"{sid} %{{x|%b %Y}}: ₹%{{y:.2f}} lakh<extra></extra>"))
    fig.add_hline(y=10, line_dash="dash", line_color=REF, annotation_text="start ₹10 lakh")
    fig.update_yaxes(title="₹ lakh")
    return fig_base(fig, 320, legend=bool(pick))


def heat_states(stt, order):
    d = {(r.strategy, r.state): r.net_pct for r in stt.itertuples()}
    n = {(r.strategy, r.state): r.trades for r in stt.itertuples()}
    return heat(d, order, list(STATE_LABEL), col_label=STATE_LABEL.get, text_extra=lambda r, c: f"<br><sub>{n.get((r, c), 0):,}</sub>")


def heat_periods(per, order):
    d = {(r.strategy, r.period): r.net_pct for r in per.itertuples()}
    return heat(d, order, list(PERIOD_LABEL), col_label=PERIOD_LABEL.get)


def heat_years(yr, order):
    d = {(r.year, r.strategy): r.net_pct for r in yr.itertuples()}
    years = sorted(yr.year.unique())
    return heat(d, years, order, row_label=str, h=70 + 32 * len(years))


# ---------------- the report ----------------
def _p(text):
    st.markdown(text)


def report_tab(s):
    cost = C.COST_RT * 100
    hz, ex, stp, eqw = acsv("horizon.csv"), acsv("exits.csv"), acsv("stops.csv"), acsv("equity_weekly.csv")
    stt, per, yr, mk = acsv("states.csv"), acsv("periods.csv"), acsv("years.csv"), acsv("market_years.csv")
    order = s.strategy.tolist()
    edge = s[s.states_with_edge > 0]
    best = s.loc[s.net_pct.idxmax()]
    clear = s[s.gross_pct > cost]
    ran = None
    rp = C.RESULTS / "trades.csv"
    if rp.exists():
        ran = pd.Timestamp(rp.stat().st_mtime, unit="s").tz_localize("UTC").tz_convert("Asia/Kolkata")
    figs = {}

    st.markdown("## Swing strategy backtest")
    st.caption((f"Run {ran:%d %b %Y %H:%M} · " if ran is not None else "") + f"{len(s)} strategies · {s.trades.sum():,} trades · "
               f"2016 to the latest bar · today's Nifty 500 · {cost:.1f}% round-trip cost · signal at the close, entry at the next open")
    head = (f"**{'No' if edge.empty else len(edge)} swing strateg{'y has' if len(edge) == 1 else 'ies have'} a tested edge.** "
            f"The best, {best.strategy} {best['name']}, makes {best.net_pct:+.2f}% per trade after costs. "
            + (f"Before costs the strategies make {s.gross_pct.min():+.2f}% to {s.gross_pct.max():+.2f}% per trade, against a {cost:.1f}% cost"
               + (f": only {', '.join(clear.strategy)} clear{'s' if len(clear) == 1 else ''} it." if len(clear) else ": none clears it.")))
    (st.success if len(edge) else st.warning)(head)
    c = st.columns(5)
    c[0].metric("Trades tested", f"{s.trades.sum():,}")
    c[1].metric(f"Best net / trade ({best.strategy})", pct(best.net_pct))
    c[2].metric(f"Best gross ({s.loc[s.gross_pct.idxmax(), 'strategy']})", pct(s.gross_pct.max()), help=f"Cost is {cost:.1f}% per round trip")
    c[3].metric("With an edge", f"{len(edge)} of {len(s)}")
    c[4].metric(f"Best account CAGR ({s.loc[s.cagr_pct.idxmax(), 'strategy']})", pct(s.cagr_pct.max(), 1))

    st.markdown("### 1 · Scorecard")
    st.caption("Per trade, after costs unless marked gross. Click a column to sort.")
    st.dataframe(s.drop(columns=["states_with_edge"]), width="stretch", hide_index=True, column_config={
        "name": st.column_config.TextColumn("Strategy"), "type": st.column_config.TextColumn("Family"),
        "net_pct": st.column_config.NumberColumn("Net / trade %", format="%+.3f"), "gross_pct": st.column_config.NumberColumn("Gross %", format="%+.3f"),
        "win_pct": st.column_config.NumberColumn("Win %", format="%.1f"), "avg_win_pct": st.column_config.NumberColumn("Avg win %", format="%+.2f"),
        "avg_loss_pct": st.column_config.NumberColumn("Avg loss %", format="%+.2f"), "profit_factor": st.column_config.NumberColumn("Profit factor", format="%.2f"),
        "cagr_pct": st.column_config.NumberColumn("CAGR %", format="%+.1f"), "max_dd_pct": st.column_config.NumberColumn("Max DD %", format="%.1f"),
        "sharpe": st.column_config.NumberColumn("Sharpe", format="%.2f"), "trades": st.column_config.NumberColumn("Trades", format="%d")})

    st.markdown("### 2 · Why there is no edge" if edge.empty else "### 2 · Where the edge comes from")
    a, b = st.columns(2)
    with a:
        st.markdown("**The gross edge is about the size of the cost**")
        st.caption(f"Average return per trade before costs, against the {cost:.1f}% a round trip costs.")
        figs["Gross return per trade vs cost"] = chart_gross(s)
        st.plotly_chart(figs["Gross return per trade vs cost"], width="stretch", key="rep-gross")
        gap = (clear.gross_pct - cost).max() if len(clear) else None
        _p(f"Costs take {cost:.1f}% off every trade. " + (f"{', '.join(clear.strategy)} clear{'s' if len(clear) == 1 else ''} it by at most {gap:.2f}%, "
                                                          "too little to survive slippage." if gap is not None else "Not one strategy earns that much before costs."))
    with b:
        st.markdown("**When the payoff arrives**")
        st.caption("Return after a signal minus buying any liquid stock, by days held, before costs and stops.")
        if hz is not None:
            figs["Excess return by holding period"] = chart_horizon(hz)
            st.plotly_chart(figs["Excess return by holding period"], width="stretch", key="rep-hz")
            mom = hz[hz.group == "momentum"]
            if len(mom):
                at = lambda dd: mom[mom.days == dd].excess_pct.mean() if (mom.days == dd).any() else np.nan
                short, long_ = at(10), at(int(mom.days.max()))
                _p(f"The momentum-type setups (breakouts, strength leaders) lag random stocks by {short:+.2f}% after 10 days but lead by "
                   f"{long_:+.2f}% after {int(mom.days.max())} days. Our swing exits sell within 10 days, before the payoff. "
                   "That is what the momentum portfolio strategy tests instead.")
    a, b = st.columns(2)
    with a:
        st.markdown("**How trades end**")
        st.caption("Share of trades by how they ended. Hover for the average net return.")
        if ex is not None:
            figs["How trades end"] = chart_exits(ex)
            st.plotly_chart(figs["How trades end"], width="stretch", key="rep-ex")
            tl = ex[ex.exit.isin(["Time limit", "Stop"])].groupby("strategy").share_pct.sum()
            _p(f"Between {tl.min():.0f}% and {tl.max():.0f}% of trades end on the clock or at a stop."
               + (f" Stops aren't the problem: only {stp.recovered_10d_pct.iat[0]}% of {stp.stopped_trades.iat[0]:,} stopped trades "
                  "would have been in profit 10 days later." if stp is not None else ""))
    with b:
        st.markdown("**What it does to an account**")
        st.caption("₹10 lakh, 10 equal positions, weekly value.")
        if eqw is not None:
            pick = st.multiselect("Highlight", sorted(eqw.strategy.unique()), default=[best.strategy], key="rep-eqpick")
            figs["Account value"] = chart_equity(eqw, pick)
            st.plotly_chart(figs["Account value"], width="stretch", key="rep-eq")
            end = eqw.sort_values("date").groupby("strategy").equity.last()
            _p(f"{int((end < 1e6).sum())} of {len(end)} strategies end below the starting ₹10 lakh; the best ends at ₹{end.max() / 1e5:.1f} lakh.")

    st.markdown("### 3 · When they work, and when they don't")
    a, b = st.columns(2)
    with a:
        st.markdown("**Net return per trade by market state**")
        st.caption("Trades in small type.")
        if stt is not None:
            figs["By market state"] = heat_states(stt, order)
            st.plotly_chart(figs["By market state"], width="stretch", key="rep-st")
            col = stt.groupby("state").net_pct.mean().reindex(list(STATE_LABEL))
            good = col[col > 0]
            sig = stt[(stt.t_stat >= C.MIN_T_STAT) & (stt.edge_pct > 0)]
            _p((f"{', '.join(STATE_LABEL[x] for x in good.index)} is the only state where trades make money on average"
                if len(good) == 1 else f"Trades make money on average in {len(good)} of 4 states" if len(good) else "No state makes money on average")
               + f". {'No' if sig.empty else len(sig)} strategy-state pair{'s' if len(sig) != 1 else ''} beat{'s' if len(sig) == 1 else ''} "
               f"random picks with t ≥ {C.MIN_T_STAT:.0f}" + ("." if sig.empty else f": {', '.join(sig.strategy + ' in ' + sig.state.map(STATE_LABEL))}."))
    with b:
        st.markdown("**Net return per trade by research period**")
        st.caption("Design 2016–20 is where rules were chosen; validation and holdout are checks.")
        if per is not None:
            figs["By research period"] = heat_periods(per, order)
            st.plotly_chart(figs["By research period"], width="stretch", key="rep-per")
            pv = per.pivot(index="strategy", columns="period", values="net_pct")
            only_val = pv[(pv.get("validation", 0) > 0) & (pv.get("design", 0) <= 0) & (pv.get("holdout", 0) <= 0)]
            _p(f"{len(only_val)} of {len(pv)} strategies made money only in 2021–23, a strong rising market, and lost in both other periods."
               if len(only_val) else "Results differ by period; no strategy is positive in all three.")
    st.markdown("**Year by year: results follow the market**")
    st.caption("Net return per trade by the year the signal fired, beside the Nifty's return and a random liquid stock's 5-day return that year.")
    if yr is not None:
        c1, c2 = st.columns([3, 1.2])
        figs["By year"] = heat_years(yr, order)
        c1.plotly_chart(figs["By year"], width="stretch", key="rep-yr")
        if mk is not None:
            c2.dataframe(mk.rename(columns={"year": "Year", "nifty_return_pct": "Nifty 50", "random_stock_5d_gross_pct": "Random stock, 5d"}),
                         hide_index=True, width="stretch", height=40 + 35 * len(mk),
                         column_config={"Year": st.column_config.NumberColumn(format="%d"),
                                        "Nifty 50": st.column_config.NumberColumn(format="%+.1f%%"),
                                        "Random stock, 5d": st.column_config.NumberColumn(format="%+.2f%%")})
            ym = yr.groupby("year").net_pct.mean().rename("avg").reset_index().merge(mk, on="year")
            if len(ym) > 3:
                corr = np.corrcoef(ym.avg, ym.random_stock_5d_gross_pct)[0, 1]
                _p(f"The average strategy's year moves with a random stock's year (correlation {corr:.2f}): "
                   "the results mostly reflect the market, not the rules.")

    st.markdown("### 4 · Verdict and next steps")
    vjson = C.RESULTS / "strategy" / "momentum_verdict.json"
    lines = [f"- **Swing setups:** {'none passes' if edge.empty else ', '.join(edge.strategy) + ' pass'} the test "
             f"(beat random-entry picks after costs with t ≥ {C.MIN_T_STAT:.0f} in a market state, with {C.MIN_TRADES_STATE}+ trades). "
             + ("They stay switched off; the regime agent's playbook is cash." if edge.empty else "They run only in the states where they passed.")]
    if vjson.exists():
        v = json.loads(vjson.read_text())
        lines.append(f"- **Momentum portfolio ({v['chosen']}):** {'passes' if v['passes'] else 'does not pass'} design, validation and holdout "
                     f"— details in the Momentum research tab.")
    lines.append("- **Test new ideas in the Strategy Lab** (sidebar). It applies the same periods, random control and t-stat rule, "
                 "and can remove the survivorship bias with a point-in-time universe.")
    _p("\n".join(lines))
    with st.expander("Method and caveats"):
        _p(f"- **Universe:** today's Nifty 500, liquid stocks only (₹{C.MIN_TURNOVER_CR} cr+ a day, price ₹{C.MIN_PRICE}+). Stocks that left the index "
           "are missing, which flatters results (survivorship bias).\n"
           f"- **Trades:** signal on the close, entry at the next open, {cost:.1f}% round-trip cost, one trade per stock at a time.\n"
           "- **Control:** random liquid stocks bought on the same days with the same exits; edge = strategy minus control.\n"
           f"- **Selection:** a strategy is switched on in a market state only with {C.MIN_TRADES_STATE}+ trades and t ≥ {C.MIN_T_STAT:.0f} on weekly clusters.\n"
           "- **Periods:** design 2016–20, validation 2021–23, holdout 2024 onwards.")
    rep = C.RESULTS / "report.md"
    if rep.exists():
        with st.expander("Backtest log (report.md)"):
            st.markdown(rep.read_text())
    st.download_button("Download this report (HTML)", _html(s, head, figs), file_name="swing_backtest_report.html", mime="text/html")


def _html(s, head, figs):
    esc = lambda x: str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    tbl = s.drop(columns=["states_with_edge"]).round(3).to_html(index=False, border=0)
    parts, first = [], True
    for title, f in figs.items():
        parts.append(f"<h3>{esc(title)}</h3>" + f.to_html(full_html=False, include_plotlyjs="cdn" if first else False))
        first = False
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>Swing strategy backtest</title><style>"
            "body{font-family:-apple-system,Segoe UI,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#222}"
            "table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid #ddd;padding:5px 6px;text-align:left}</style></head><body>"
            f"<h1>Swing strategy backtest</h1><p>{esc(head.replace('**', ''))}</p><h2>Scorecard</h2>{tbl}{''.join(parts)}</body></html>")
