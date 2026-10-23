"""Macro & geopolitics page: indicators, geopolitical risk and themes, sector sensitivity, shock history,
India releases and an event calendar."""
import json
from ui.common import *  # noqa: F401,F403
from core import macro as M

OUT = C.RESULTS / "macro"
FACTOR_ORDER = ["Brent crude", "USD/INR", "US 10-year yield", "US dollar index", "US VIX", "Geopolitical risk", "FII net buying"]


def _read(name):
    p = OUT / f"{name}.csv"
    try:
        return pd.read_csv(p) if p.exists() and p.stat().st_size > 2 else pd.DataFrame()
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def page():
    st.title("Macro & geopolitics")
    lf = M.last_fetch()
    c = st.columns([1.3, 1.3, 4])
    if c[0].button("Fetch macro data", type="primary", width="stretch", disabled=jobs.busy("macro-fetch"),
                   help="FRED (US yields, dollar, Brent, USD/INR, VIX), the GPR geopolitical risk index, GDELT news themes, NSE FII/DII. About 2 minutes."):
        launch("macro-fetch")
    if c[1].button("Run macro study", width="stretch", disabled=jobs.busy("macro-research"),
                   help="Sector sensitivity to each factor and what followed past shocks (a minute)."):
        launch("macro-research")
    if lf:
        bad = [k for k, v in lf["status"].items() if not str(v).startswith("ok")]
        c[2].caption(f"Last fetch {lf['when'][:16]} · " + (f"not working: {', '.join(bad[:6])}" if bad else "all sources ok")
                     + " · runs automatically with Fetch data + run all agents")
    with st.expander("Job output", expanded=jobs.current("macro-fetch") is not None or jobs.current() is not None and jobs.current()["name"] == "macro-research"):
        job_panel("macro")
    t = st.tabs(["📊 Indicators", "🌍 Geopolitics", "🎯 Sector sensitivity", "⚡ Shocks", "🇮🇳 India data", "🗓 Calendar"])
    with t[0]:
        indicators_tab()
    with t[1]:
        geo_tab()
    with t[2]:
        sensitivity_tab()
    with t[3]:
        shocks_tab()
    with t[4]:
        india_tab()
    with t[5]:
        calendar_tab()


def indicators_tab():
    rows = M.summary()
    if not rows:
        st.info("No macro data yet. Press **Fetch macro data**.")
        return
    cols = st.columns(4)
    for i, r in enumerate(rows):
        val = f"{r['value']:,.2f}" + (" %" if r["unit"] == "%" else "")
        if r["change_kind"] == "total":
            delta = None if r["chg_5d"] is None else f"₹{r['chg_5d']:+,.0f} cr over 5 days"
            more = f"20-day total: ₹{r['chg_20d']:+,.0f} cr." if r["chg_20d"] is not None else ""
        else:
            unit = "pts" if r["change_kind"] == "points" else "%"
            delta = None if r["chg_1d"] is None else f"{r['chg_1d']:+.2f} {unit} (1 day)"
            more = (f"1 month: {r['chg_20d']:+.2f} {unit}. " if r["chg_20d"] is not None else "") + \
                   (f"Distance from its past-year average: {r['z_1y']:+.1f} standard deviations." if r["z_1y"] is not None else "")
        with cols[i % 4]:
            st.metric(r["label"], val, delta, delta_color="off" if r["series"] in ("DGS10", "DFF", "VIXCLS", "GPRD", "DEXINUS") else "normal",
                      help=f"As of {r['date']}. {more}")
            if r["z_1y"] is not None and abs(r["z_1y"]) >= 2:
                st.caption(f"⚠️ unusual: {r['z_1y']:+.1f} sd vs past year")
    w = M.wide()
    names = {r["series"]: r["label"] for r in rows}
    c = st.columns([3, 1])
    pick = c[0].multiselect("Chart", list(names), default=[s for s in ("DCOILBRENTEU", "DEXINUS", "DGS10") if s in names], format_func=names.get)
    yrs = c[1].selectbox("Period", [1, 3, 5, 10], index=1, format_func=lambda y: f"{y} year{'s' if y > 1 else ''}")
    if pick:
        v = w[pick][w.index >= w.index.max() - pd.DateOffset(years=yrs)]
        norm = c[1].toggle("Rebase to 100", value=len(pick) > 1)
        fig = go.Figure()
        for s in pick:
            x = v[s].dropna()
            if norm and len(x):
                x = 100 * x / x.iat[0]
            fig.add_scatter(x=x.index, y=x.values, name=names[s], mode="lines")
        st.plotly_chart(fig_base(fig, 360, legend=True), width="stretch")
    st.caption("US data is dated by the US day: its effect shows up in the next Indian session. FII/DII history builds up from your first fetch.")


def geo_tab():
    w = M.wide()
    if "GPRD" in w:
        g = w.GPRD.dropna()
        g = g[g.index >= g.index.max() - pd.DateOffset(years=3)]
        fig = go.Figure()
        fig.add_scatter(x=g.index, y=g.values, name="GPR daily", mode="lines", line=dict(color=MUTED, width=1))
        fig.add_scatter(x=g.index, y=g.rolling(30).mean(), name="30-day average", mode="lines", line=dict(color=BAD, width=2))
        st.markdown("**Geopolitical Risk index** (Caldara & Iacoviello: share of newspaper articles about wars, terrorism and tensions; 100 = 1985–2019 average)")
        st.plotly_chart(fig_base(fig, 300, legend=True), width="stretch")
    else:
        st.caption("GPR index not fetched yet.")
    labels = {k: v[0] for k, v in M.THEMES.items()}
    vol = [c for c in w.columns if c.startswith("GDELT_") and c.endswith("_vol")]
    st.markdown("**News themes worldwide** (GDELT: articles per day)")
    if vol:
        keys = [c[6:-4] for c in vol]
        pick = st.multiselect("Themes", keys, default=keys[:5], format_func=lambda k: labels.get(k, k), key="geo-themes")
        fig = go.Figure()
        for k in pick:
            s = w[f"GDELT_{k}_vol"].dropna()
            fig.add_scatter(x=s.index, y=s.rolling(3, min_periods=1).mean(), name=labels.get(k, k), mode="lines")
        st.plotly_chart(fig_base(fig, 300, legend=True), width="stretch")
        tone = {labels.get(k, k): w[f"GDELT_{k}_tone"].dropna().tail(7).mean() for k in keys if f"GDELT_{k}_tone" in w}
        if tone:
            st.caption("Average tone, last 7 days (negative = more hostile coverage): "
                       + " · ".join(f"{k} {v:+.1f}" for k, v in sorted(tone.items(), key=lambda kv: kv[1])))
    else:
        st.caption("GDELT themes not fetched yet. GDELT keeps 3 months; history grows from your first fetch.")
    st.markdown("**Your stored headlines by theme**")
    h = M.headline_themes()
    if h.empty:
        st.caption("No themed headlines stored yet.")
        return
    c = st.columns([2, 1])
    th = c[0].selectbox("Theme", list(labels), format_func=labels.get, key="geo-th")
    days = c[1].selectbox("From the last", [7, 30, 90, 365], index=1, key="geo-d", format_func=lambda d: f"{d} days")
    v = h[(h.theme == th) & (h.published >= pd.Timestamp.now() - pd.Timedelta(days=days))].sort_values("published", ascending=False)
    cnt = h[h.published >= pd.Timestamp.now() - pd.Timedelta(days=days)].theme.value_counts()
    st.caption("Headlines per theme: " + " · ".join(f"{labels.get(k, k)} {n}" for k, n in cnt.items()))
    show(v[["published", "title", "source", "tone", "link"]], "None in this period.", height=min(500, 40 + 35 * max(len(v), 1)),
         column_config={"published": st.column_config.DatetimeColumn("time", format="DD MMM HH:mm"),
                        "title": st.column_config.TextColumn("headline", width="large"), "link": st.column_config.LinkColumn("open", display_text="open")})


def _meta():
    p = OUT / "meta.json"
    return json.loads(p.read_text()) if p.exists() else None


def sensitivity_tab():
    meta = _meta()
    if not meta:
        st.info("Press **Run macro study** after fetching macro data.")
        return
    win = st.radio("Window", ["Last 3 years", "Full history"], horizontal=True, key="sens-w")
    s = _read("sens_3y" if win == "Last 3 years" else "sens_all")
    if s.empty:
        st.caption("Not enough overlapping data yet.")
        return
    facs = [f for f in FACTOR_ORDER if f in set(s.factor)]
    secs = sorted(s.sector.unique())
    z = [[s[(s.sector == r) & (s.factor == f)].per_1sd_pct.mean() for f in facs] for r in secs]
    tt = [[s[(s.sector == r) & (s.factor == f)].t.mean() for f in facs] for r in secs]
    txt = [[("" if pd.isna(a) else f"{a:+.2f}%" + (" *" if abs(b) >= 2 else "")) for a, b in zip(ra, rb)] for ra, rb in zip(z, tt)]
    m = np.nanmax(np.abs(np.array(z, dtype=float))) or 1
    fig = go.Figure(go.Heatmap(z=z, x=facs, y=secs, text=txt, texttemplate="%{text}", colorscale=[[0, BAD], [0.5, "#f4f4f2"], [1, GOOD]],
                               zmin=-m, zmax=m, showscale=False, xgap=3, ygap=3,
                               customdata=np.round(np.array(tt, dtype=float), 1),
                               hovertemplate="%{y} · %{x}: %{text}<br>t = %{customdata}<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(side="top")
    st.plotly_chart(fig_base(fig, 60 + 28 * len(secs)), width="stretch")
    st.caption("Extra weekly move of each sector (equal-weight, beyond what the Nifty explains) when the factor moves by a typical amount "
               "(1 standard deviation of its weekly change). * = |t| ≥ 2. Example: −1.5% under Brent crude means the sector tends to fall "
               "1.5% more than the market in a week crude jumps by a typical amount.")
    if win == "Last 3 years":
        st_ = _read("sens_stocks_3y")
        if len(st_):
            f = st.selectbox("Most sensitive stocks to", facs, key="sens-f")
            show(st_[st_.factor == f].drop(columns="factor"), column_config={
                "per_1sd_pct": st.column_config.NumberColumn("Extra move per typical factor move", format="%+.2f%%"),
                "t": st.column_config.NumberColumn(format="%.1f")})
    st.caption("Data coverage: " + " · ".join(f"{k} {v[0]}→{v[1]}" for k, v in meta.get("coverage", {}).items()))


def shocks_tab():
    sh = _read("shocks")
    if sh.empty:
        st.info("Press **Run macro study** after fetching macro data.")
        return
    st.caption("Days when a factor moved more than 2 standard deviations of its past year. Nifty moves are averaged over those days "
               "(the first Indian session after the shock, then 5 and 20 sessions). Few events → anecdotes.")
    show(sh, column_config={"nifty_day": st.column_config.NumberColumn("Nifty, reaction day", format="%+.2f%%"),
                            "nifty_5d": st.column_config.NumberColumn("Nifty, 5 days", format="%+.2f%%"),
                            "nifty_20d": st.column_config.NumberColumn("Nifty, 20 days", format="%+.2f%%"),
                            "t_day": st.column_config.NumberColumn("t", format="%.1f"), "t_5d": st.column_config.NumberColumn("t 5d", format="%.1f"),
                            "t_20d": st.column_config.NumberColumn("t 20d", format="%.1f"),
                            "share_down_day": st.column_config.NumberColumn("% of times Nifty fell", format="%.0f")})
    d = _read("shock_sectors")
    if len(d):
        c = st.columns(2)
        f = c[0].selectbox("Factor", [x for x in FACTOR_ORDER if x in set(d.factor)], key="sh-f")
        dr = c[1].radio("Direction", ["up", "down"], horizontal=True, key="sh-d")
        v = d[(d.factor == f) & (d.direction == dr)].sort_values("mean_vs_nifty")
        if len(v):
            fig = go.Figure(go.Bar(x=v.mean_vs_nifty, y=v.sector, orientation="h", marker_color=[GOOD if x > 0 else BAD for x in v.mean_vs_nifty],
                                   customdata=np.round(v.t, 1), hovertemplate="%{y}: %{x:+.2f}% vs Nifty (t %{customdata})<extra></extra>"))
            fig.update_xaxes(title_text="% vs Nifty on the reaction day")
            st.plotly_chart(fig_base(fig, 40 + 24 * len(v)), width="stretch")


def india_tab():
    st.caption("India's official releases (MOSPI, RBI, GST Council, S&P Global PMI). Enter each release as it comes out; the market report "
               "and charts use this table. The app doesn't guess these numbers.")
    d = M.india_table()
    if d.empty:
        d = pd.DataFrame({"indicator": M.INDIA_DEFAULT_INDICATORS, "period": "", "value": np.nan, "unit": "", "released": "", "source": "", "note": ""})
    ed = st.data_editor(d, num_rows="dynamic", width="stretch", hide_index=True, key="india-ed",
                        column_config={"indicator": st.column_config.TextColumn(width="medium"),
                                       "period": st.column_config.TextColumn(help="e.g. 2026-09 or Q2 FY27"),
                                       "value": st.column_config.NumberColumn(format="%.2f"),
                                       "released": st.column_config.TextColumn(help="Release date, YYYY-MM-DD")})
    if st.button("Save India data"):
        M.save_india(ed[ed.value.notna()])
        st.success("Saved.")
    v = ed[ed.value.notna()]
    if len(v):
        ind = st.selectbox("Chart", sorted(v.indicator.unique()), key="india-ch")
        x = v[v.indicator == ind].sort_values("period")
        st.plotly_chart(fig_base(go.Figure(go.Scatter(x=x.period, y=x.value, mode="lines+markers")), 260), width="stretch")


def calendar_tab():
    st.caption("Upcoming events that move markets: RBI policy, CPI / GDP releases, US Fed meetings, the Budget, elections, big index rebalances. "
               "Add them from the official schedules; the Overview and market report show what's coming in the next two weeks.")
    d = M.calendar()
    ed = st.data_editor(d if len(d) else pd.DataFrame(columns=M.CAL_COLS), num_rows="dynamic", width="stretch", hide_index=True, key="cal-ed",
                        column_config={"date": st.column_config.DateColumn(), "importance": st.column_config.SelectboxColumn(options=["high", "medium", "low"]),
                                       "country": st.column_config.SelectboxColumn(options=["India", "US", "Global", "Other"])})
    if st.button("Save calendar"):
        M.save_calendar(ed)
        st.success("Saved.")
    if len(d):
        up = d[(d.date >= pd.Timestamp.now().normalize()) & (d.date <= pd.Timestamp.now() + pd.Timedelta(days=14))].sort_values("date")
        st.markdown("**Next two weeks**")
        show(up, "Nothing scheduled.")
