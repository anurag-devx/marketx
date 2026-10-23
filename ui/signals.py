"""Every signal on a day, from every strategy, with what happened to it and why."""
from ui.common import *  # noqa: F401,F403


def outcome(r, state):
    if not r.strategy_active:
        return "Off: strategy has no tested edge in " + STATE_LABEL.get(state, state).lower()
    if pd.isna(r.approved):
        return "Not ranked: a higher-scoring signal on the same stock was used"
    if r.approved:
        return f"Approved: {int(r.quantity)} shares"
    return f"Rejected by risk: {r.reject_reason}"


def page():
    st.title("Signals and decisions")
    st.caption("Every setup that fired, from every strategy, including those switched off today, and what the agents decided.")
    dates = q("select distinct cast(date as date) d from signals order by d desc")
    if not has(dates):
        st.info("No signals stored yet. Run all agents once (sidebar).")
        return
    d = st.selectbox("Day", dates.d.tolist(), format_func=lambda x: x.strftime("%a %d %b %Y"))
    sig = q("select * exclude (run_id, version) from signals where cast(date as date) = ?", [d])
    dec = q("select symbol, strategy, approved, quantity, value, risk_rs, reject_reason, setup_score from decisions where cast(date as date) = ?", [d])
    reg = q("select * from regime_daily where cast(date as date) = ?", [d])
    state = reg.market_state.iat[0] if has(reg) else ""
    m = sig.merge(dec, on=["symbol", "strategy"], how="left") if has(dec) else sig.assign(approved=np.nan, quantity=np.nan, value=np.nan, risk_rs=np.nan, reject_reason=None, setup_score=np.nan)
    m["outcome"] = [outcome(r, state) for r in m.itertuples()]
    m["status"] = np.where(~m.strategy_active, "Strategy off", np.where(m.approved == True, "Approved", np.where(m.approved == False, "Rejected by risk", "Not ranked")))

    n_all, n_on, n_ok = len(m), int(m.strategy_active.sum()), int((m.status == "Approved").sum())
    c = st.columns(4)
    c[0].metric("Signals", n_all)
    c[1].metric("From strategies switched on", n_on)
    c[2].metric("Approved by risk", n_ok)
    c[3].metric("Market state", {"up_calm": "Up · calm", "up_volatile": "Up · volatile", "down_calm": "Down · calm", "down_volatile": "Down · volatile"}.get(state, state))
    if has(reg) and reg.note.iat[0]:
        st.info(reg.note.iat[0])

    a, b = st.columns([1, 1])
    with a:
        st.markdown("**From signal to trade**")
        fig = go.Figure(go.Funnel(y=["Signals", "Strategy switched on", "Approved"], x=[n_all, n_on, n_ok],
                                  marker=dict(color=[MUTED, ACCENT, GOOD]), textinfo="value"))
        st.plotly_chart(fig_base(fig, 220), width="stretch")
    with b:
        st.markdown("**Signals by strategy**")
        cnt = m.groupby(["strategy", "status"]).size().reset_index(name="n")
        fig = go.Figure()
        for stt, col in (("Approved", GOOD), ("Rejected by risk", BAD), ("Not ranked", REF), ("Strategy off", MUTED)):
            g = cnt[cnt.status == stt]
            fig.add_trace(go.Bar(x=g.strategy, y=g.n, name=stt, marker_color=col))
        fig.update_layout(barmode="stack")
        st.plotly_chart(fig_base(fig, 220, legend=True), width="stretch")

    f = st.columns([2, 2, 2])
    strat = f[0].multiselect("Strategy", sorted(m.strategy.unique()))
    status = f[1].multiselect("Outcome", ["Approved", "Rejected by risk", "Not ranked", "Strategy off"])
    search = f[2].text_input("Find a stock", placeholder="e.g. TECHM")
    v = m
    if strat:
        v = v[v.strategy.isin(strat)]
    if status:
        v = v[v.status.isin(status)]
    if search:
        v = v[v.symbol.str.contains(search.upper())]
    v = v.sort_values(["strategy_active", "setup_score", "strength"], ascending=False)
    st.dataframe(v[["symbol", "sector", "strategy", "strategy_name", "close", "stop", "target", "strength", "rs_pct", "turnover_cr", "outcome"]],
                 hide_index=True, width="stretch", column_config={
                     "strength": st.column_config.ProgressColumn("Signal strength", min_value=0, max_value=1, format="%.2f"),
                     "rs_pct": st.column_config.NumberColumn("Strength rank", format="%.2f", help="Share of liquid stocks it beats on 3-month return vs Nifty"),
                     "turnover_cr": st.column_config.NumberColumn("Avg traded ₹ cr (20d)", format="%.0f", help="Average daily traded value over the last 20 sessions"),
                     "outcome": st.column_config.TextColumn("What happened", width="large")})
    pick = st.selectbox("Open a stock in Stock lookup", [""] + sorted(v.symbol.unique()), key="sig-open")
    if pick:
        go_stock(pick)
