"""Screener (research shortlist) and Screener research (strategies, experiments, comparison)."""
from ui.common import *  # noqa: F401,F403
from screener import config as SC, screen as SCR, experiments as X, evaluate as V, setups as SU, interface
from screener.filters import FILTERS, SHORT

SETUP_LABEL = {"trend_continuation": "Trend continuation", "breakout_volume": "Breakout with volume", "pullback_recovery": "Pullback recovery",
               "relative_strength": "Relative strength", "volatility_breakout": "Volatility contraction breakout"}
STATUS_ICON = {"experimental": "🧪 experimental", "validated": "✅ validated research candidate", "enabled": "🟢 enabled in the screener"}


@st.cache_resource(show_spinner="Preparing indicators for every stock (about 15 seconds, then cached)...")
def _inputs(version):
    px, bench, uni, issues = SCR.load_inputs()
    return SCR.features(), list(uni.Symbol), issues


def inputs():
    return _inputs(SCR.data_version())


def _strategy_options():
    try:
        st_ = X.load_strategies()
    except SC.ConfigError as e:
        st.error(f"Strategy files: {e}")
        st_ = {}
    return st_


# =============================================================== screener page
PARAM_LABEL = {
    "trend_continuation": {"slope_lookback": ("SMA50 rising over (sessions)", None),
                           "use_rsi": ("Require RSI inside a band", None), "rsi_min": ("RSI from", None), "rsi_max": ("RSI up to", None),
                           "use_adx": ("Require trend strength (ADX)", None), "adx_min": ("ADX above", None)},
    "breakout_volume": {"lookback": ("Close above the highest high of the previous (sessions)", "Today's high is not included"),
                        "rel_volume_min": ("Volume at least (× its 20-session average)", None),
                        "require_above_sma50": ("Also close above SMA50", None)},
    "pullback_recovery": {"rsi_cross": ("RSI crosses up through", "Yesterday below this level, today at or above it"),
                          "use_proximity": ("Require price near a moving average", None), "proximity_ma": ("Moving average", None),
                          "proximity_pct": ("Within (%)", None)},
    "relative_strength": {"return_window": ("Return measured over (sessions)", "63 ≈ 3 months, 126 ≈ 6 months"),
                          "use_benchmark_relative": ("Measure against the Nifty", None),
                          "min_percentile": ("Rank among eligible stocks at least (percentile)", "80 = top 20%")},
    "volatility_breakout": {"contraction_pctile_max": ("Bollinger width percentile yesterday at most", "Low = unusually quiet versus the past year"),
                            "breakout_lookback": ("Then close above the highest high of the previous (sessions)", None),
                            "use_rel_volume": ("Require volume confirmation", None), "rel_volume_min": ("Volume at least (×)", None)},
}


def page():
    st.title("Screener")
    st.warning("**Research lists, not buy recommendations.** The agents still apply their own regime, entry, risk and exit rules.")
    if prices() is None:
        st.info("No price data yet."); return
    stale_notice("the screener (every tab)")
    t1, t2, t6, t7, t3, t4, t5 = st.tabs(["📐 Technical setups", "📈 Market movers", "📊 Daily moves", "📑 Results", "📰 News", "📢 NSE announcements", "🤖 Agents' shortlist"])
    from ui import screener_more as M
    with t1:
        run_tab(_strategy_options())
    with t2:
        M.market_tab()
    with t6:
        from ui import moves_catalyst
        moves_catalyst.daily_moves_tab()
    with t3:
        M.news_tab()
    with t7:
        from ui import results
        results.results_tab()
    with t4:
        M.announcements_tab()
    with t5:
        latest_tab()


def latest_tab():
    sl = interface.load_shortlist()
    if sl.empty():
        st.caption("The screener runs with the agents (sidebar: Run all agents). Nothing saved yet.")
        return
    st.caption(f"Screen date {pd.Timestamp(sl.screen_date).date()} · screener v{sl.version} · data {sl.data_version}")
    for s, g in sl.rows.groupby("screener_strategy"):
        st.markdown(f"**{s}** · {len(g)} stocks")
        show(g[["rank", "symbol", "sector", "setups_passed", "qualification_reasons", "close", "rsi14", "adx14", "rel_volume_20d",
                "return_126d", "data_quality_flags"]].round(2))
    if sl.warnings:
        st.caption(f"Data warnings on these stocks: {', '.join(sl.warnings)}")


def _param_input(name, pname, spec, value, key):
    typ, lo, hi, _ = spec
    lab, hlp = PARAM_LABEL.get(name, {}).get(pname, (pname.replace("_", " "), None))
    if typ is bool:
        return st.toggle(lab, value, key=key, help=hlp)
    if isinstance(lo, list):
        return st.selectbox(lab, lo, index=lo.index(value), key=key, help=hlp)
    if typ is int:
        return int(st.number_input(lab, int(lo), int(hi), int(value), key=key, help=hlp))
    return float(st.number_input(lab, float(lo), float(hi), float(value), key=key, help=hlp))


def run_tab(strategies):
    feat, today, issues = inputs()
    dates = sorted(feat.date.unique())
    k = lambda x: f"scr-{x}"

    with st.container(border=True):
        st.markdown("##### 1 · What to screen")
        c = st.columns([2.2, 1.2, 2.6])
        src = c[0].selectbox("Start from", ["Screener defaults"] + list(strategies),
                             format_func=lambda x: x if x == "Screener defaults" else f"{x} · {STATUS_ICON[X.status_of(x)]}",
                             help="A saved strategy loads its exact settings; you can still change them below for this run.")
        base = SC.validate({} if src == "Screener defaults" else strategies[src]["config"])
        if st.session_state.get("scr-src") != src:
            st.session_state["scr-src"] = src
            for key in [x for x in st.session_state if x.startswith("scr-") and x not in ("scr-src", "scr-result")]:
                del st.session_state[key]
        d = c[1].date_input("Screen date", value=pd.Timestamp(dates[-1]).date(), min_value=pd.Timestamp(dates[0]).date(),
                            max_value=pd.Timestamp(dates[-1]).date(), key=k("date"), help="Uses only data up to that day's close")
        uni = c[2].radio("Universe", ["all", "nifty500_today", "pit_top"], horizontal=True, key=k("uni"),
                         index=["all", "nifty500_today", "pit_top"].index(base["universe"]),
                         format_func={"all": "All downloaded", "nifty500_today": "Today's Nifty 500", "pit_top": "Point-in-time top N"}.get)

    with st.form("screen", border=False):
        with st.container(border=True):
            st.markdown("##### 2 · Must pass: hard filters")
            st.caption("A stock has to pass every one of these before any setup is checked.")
            fb = base["filters"]
            c = st.columns(4)
            f = dict(min_close=c[0].number_input("Price at least ₹", 0.0, 100000.0, float(fb["min_close"]), 5.0, key=k("f1")),
                     min_history=c[1].number_input("History at least (sessions)", 20, 5000, int(fb["min_history"]), key=k("f2"),
                                                   help="252 ≈ one year of trading"),
                     min_median_value_cr=c[2].number_input("Daily traded value at least ₹ cr", 0.0, 100000.0, float(fb["min_median_value_cr"]), 0.5,
                                                           key=k("f3"), help="Median over the previous 20 sessions"),
                     min_valid_ratio_60d=c[3].number_input("Traded on at least (% of last 60 sessions)", 0, 100,
                                                           int(round(100 * fb["min_valid_ratio_60d"])), key=k("f4")) / 100)
            c = st.columns(4)
            f["exclude_etf"] = c[0].toggle("Leave out ETFs", fb["exclude_etf"], key=k("f5"))
            f["exclude_data_errors"] = c[1].toggle("Leave out stocks with data errors", fb["exclude_data_errors"], key=k("f6"),
                                                   help="Unreviewed errors from Data health (split gaps, bad bars)")

        with st.container(border=True):
            st.markdown("##### 3 · Look for: technical setups")
            st.caption("Switch setups on or off here, then adjust each one in its own tab.")
            names = list(SC.SETUP_SCHEMA)
            c = st.columns(len(names))
            on = {n: c[i].checkbox(SETUP_LABEL[n], base["setups"][n]["enabled"], key=k(f"{n}-on"), help=SU.DESCRIPTIONS[n])
                  for i, n in enumerate(names)}
            tabs = st.tabs([SETUP_LABEL[n] for n in names])
            sc = {}
            for tab, name in zip(tabs, names):
                with tab:
                    st.caption(SU.DESCRIPTIONS[name])
                    b = base["setups"][name]
                    p = {"enabled": on[name]}
                    params = [(pn, sp) for pn, sp in SC.SETUP_SCHEMA[name].items() if pn != "enabled"]
                    cols = st.columns(3)
                    for i, (pn, sp) in enumerate(params):
                        with cols[i % 3]:
                            p[pn] = _param_input(name, pn, sp, b[pn], k(f"{name}-{pn}"))
                    sc[name] = p

        with st.container(border=True):
            st.markdown("##### 4 · Qualify, rank and show")
            c = st.columns([2.2, 0.8, 2.6])
            comb = c[0].radio("A stock qualifies when", ["any", "min_k", "all"], index=["any", "min_k", "all"].index(base["combine"]),
                              key=k("comb"), format_func={"any": "any switched-on setup passes", "min_k": "at least k setups pass",
                                                          "all": "every switched-on setup passes"}.get)
            mk = c[1].number_input("k", 1, 5, int(base["min_setups"]), key=k("k"), help="Used only with 'at least k'")
            rm = c[2].selectbox("Rank qualifiers by", list(SC.RANKING_METHODS), index=list(SC.RANKING_METHODS).index(base["ranking"]["method"]),
                                format_func=SC.RANKING_METHODS.get, key=k("rank"))
            weights = base["ranking"]["weights"]
            with st.expander("Weights (only for the weighted ranking)", expanded=rm == "weighted"):
                c2 = st.columns(5)
                wl = {"rs_126": "6-month strength", "rs_63": "3-month strength", "rel_volume": "relative volume", "adx": "trend strength",
                      "trend_slope": "SMA50 slope"}
                weights = {w: c2[i].number_input(wl[w], 0.0, 10.0, float(weights.get(w, 0)), 0.1, key=k(f"w-{w}"))
                           for i, w in enumerate(wl)}
            c = st.columns([2, 1, 3])
            mode = c[0].radio("Show", ["top_n", "all"], index=0 if base["mode"] == "top_n" else 1, key=k("mode"),
                              format_func={"top_n": "the top N", "all": "every qualifying stock"}.get, horizontal=True)
            top_n = c[1].number_input("N", 1, 1000, int(base["top_n"]), key=k("n"))
        go_ = st.form_submit_button("▶ Run screen", type="primary")
    cfg = dict(universe=uni, filters=f, setups=sc, combine=comb, min_setups=mk, ranking=dict(method=rm, weights=weights), mode=mode, top_n=top_n)
    if go_:
        try:
            cfg = SC.validate(cfg)
        except SC.ConfigError as e:
            st.error(f"Settings: {e}"); return
        with st.spinner("Screening..."):
            st.session_state["scr-result"] = SCR.screen(pd.Timestamp(d), cfg, data=(feat, today, issues))
    res = st.session_state.get("scr-result")
    if res:
        results(res)


def results(res):
    m, dg, rows = res["meta"], res["diagnostics"], res["rows"]
    st.divider()
    st.markdown(f"### {len(res['shortlist'])} stocks on {m['screen_date']}")
    st.caption(f"Config {m['config_id']} · screener v{m['screener_version']} · data {m['data_version']} (to {m['data_last']}) · "
               f"ranking: {SC.RANKING_METHODS[m['config']['ranking']['method']]} · {m['universe_note']}")
    funnel = pd.DataFrame([{"step": k, "stocks": v} for k, v in dg.items()])
    a, b = st.columns([1.6, 3])
    a.dataframe(funnel, hide_index=True, width="stretch", height=min(40 + 35 * len(funnel), 700),
                column_config={"step": st.column_config.TextColumn(width="medium"), "stocks": st.column_config.NumberColumn(width="small")})
    with b:
        if res["shortlist"].empty:
            st.info("No stock qualifies with these settings. Nothing was relaxed: the counts show where stocks drop out.")
            rr = rows[rows.rejection_reasons.str.len() > 0].rejection_reasons.str.split("; ").explode().value_counts().head(10)
            show(rr.rename("stocks").reset_index().rename(columns={"rejection_reasons": "reason"}))
        else:
            t = SCR.table(res)
            show_cols = ["rank", "symbol", "sector", "setups_passed", "qualification_reasons", "close", "ranking_score", "rsi14", "adx14",
                         "relative_volume_20d", "return_63d", "return_126d", "benchmark_relative_return_126", "median_traded_value_20d_cr",
                         "trend_continuation_pass", "breakout_volume_pass", "pullback_recovery_pass", "relative_strength_pass",
                         "volatility_breakout_pass", "data_quality_flags"]
            st.dataframe(t[[c for c in show_cols if c in t]], hide_index=True, width="stretch", height=420,
                         column_config={"rank": st.column_config.NumberColumn(format="%d"),
                                        "ranking_score": st.column_config.NumberColumn("score", format="%.1f")})
    c = st.columns(3)
    c[0].download_button("Download shortlist (CSV)", SCR.table(res).to_csv(index=False), file_name=f"shortlist_{m['screen_date']}_{m['config_id']}.csv")
    c[1].download_button("Download every stock with reasons (CSV)", SCR.table(res, only_shortlist=False).to_csv(index=False),
                         file_name=f"screen_all_{m['screen_date']}_{m['config_id']}.csv")
    c[2].download_button("Download settings (JSON)", json.dumps(m, indent=2, default=str), file_name=f"screen_{m['config_id']}.json")
    st.markdown("#### Why a stock is in or out")
    syms = list(res["shortlist"].symbol) + sorted(set(rows.symbol) - set(res["shortlist"].symbol))
    sym = st.selectbox("Stock", syms, key="scr-detail")
    detail(rows[rows.symbol == sym].iloc[0], m["config"])


def detail(r, cfg):
    a, b, c = st.columns(3)
    with a:
        st.markdown("**Filters**")
        ok_lab = {"f_universe": "in the universe", "f_min_close": "price", "f_min_history": "enough history", "f_liquidity": "liquid enough",
                  "f_valid_obs": "trades regularly", "f_not_etf": "not an ETF", "f_data_ok": "no open data error"}
        for col, lab in ok_lab.items():
            st.markdown(f"{'✅' if r[col] else '❌'} {lab}")
        if r.rejection_reasons:
            st.caption(f"Out because: {r.rejection_reasons}")
        if r.data_quality_flags:
            st.warning(f"Data: {r.data_quality_flags}")
    with b:
        st.markdown("**Setups**")
        for s in SC.SETUP_SCHEMA:
            on = cfg["setups"][s]["enabled"]
            st.markdown(f"{'✅' if r[f'{s}_pass'] else ('❌' if on else '⚪')} {SETUP_LABEL[s]}" + ("" if on else " (off)"))
        st.caption(r.qualification_reasons or "")
        if r.qualified:
            st.success(f"In the shortlist at rank {int(r['rank'])}")
        elif r.qualifies:
            st.info(f"Qualifies, ranked {int(r['rank'])}")
    with c:
        st.markdown("**Values used**")
        vals = {"close": r.close, "SMA20": r.sma20, "SMA50": r.sma50, "SMA200": r.sma200, "SMA50 slope 20d %": r.sma50_slope_20d,
                "RSI14 (yesterday)": r.rsi14_prev, "RSI14": r.rsi14, "ADX14": r.adx14, "relative volume": r.rel_volume_20d,
                "previous 20d high": r.prev_high_20, "return 63d %": r.return_63d, "return 126d %": r.return_126d,
                "vs Nifty 126d (pp)": r.rel_return_126, "BB width percentile": r.bb_width_pctile_252,
                "median value ₹ cr": r.median_value_20d_cr, "sessions traded (60)": r.valid_ratio_60d, "ranking score": r.ranking_score}
        st.dataframe(pd.DataFrame({"value": {k: (None if pd.isna(v) else round(float(v), 2)) for k, v in vals.items()}}), width="stretch", height=420)
    if st.button(f"Open {r.symbol} in Stock lookup"):
        go_stock(r.symbol)


# =============================================================== research page
def research_page():
    st.title("Screener research")
    st.caption("Tests screener strategies on history: stock-level signal statistics against every eligible stock on the same days. "
               "These are not portfolio returns. For full trade simulation with stops and position limits, use the Strategy Lab.")
    try:
        strategies = X.load_strategies()
        exps = X.load_experiments()
    except SC.ConfigError as e:
        st.error(f"Configuration error: {e}"); return
    t1, t2, t3, t4 = st.tabs(["Compare", "One result in detail", "Strategies and live selection", "Run experiments"])
    with t4:
        run_experiments_tab(strategies, exps)
    with t3:
        strategies_tab(strategies)
    with t1:
        compare_tab()
    with t2:
        detail_tab()


def _holdout_shown():
    return st.session_state.get("hold-on", False)


def compare_tab():
    n = X.configs_tested()
    c = st.columns([1, 1, 2])
    h = c[0].selectbox("Horizon (sessions)", [5, 10, 15, 20], index=1, key="cmp-h")
    hold = c[1].toggle("Show holdout", key="hold-on", help="2024 onwards. Never used to choose or validate. Each time you look is logged.")
    if hold and not st.session_state.get("hold-logged"):
        SC.OUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(SC.OUT_DIR / "holdout_views.csv", "a") as f:
            f.write(f"{pd.Timestamp.now():%Y-%m-%d %H:%M:%S},compare\n")
        st.session_state["hold-logged"] = True
    t = X.comparison(h, include_holdout=hold)
    if t.empty:
        st.info("No experiments yet. Run them in the last tab."); return
    c[2].metric("Configurations tested so far", n, help="Every distinct configuration counts toward the multiple-testing bar")
    bt = V.validate({"by_period": {}, "by_year": []}, h, max(n, 1))["search_t"]
    st.caption(f"With {n} configurations tried, a development t of about {bt:.1f} is needed before the best one can be told apart from luck. "
               "Don't pick by win rate alone: look at the size of the edge, its interval, the downside and whether it holds in validation.")
    ok = t[t.status == "ok"].copy()
    bad = t[t.status != "ok"]
    if len(bad):
        st.error(f"{len(bad)} configuration(s) failed or are incomplete: " + "; ".join(f"{r.strategy} {r.params}: {r.get('error', r.status)}" for _, r in bad.iterrows()))
    if ok.empty:
        return
    for p in ("dev", "val", "hold"):
        if f"{p}_excess_ci95" in ok:
            ok[f"{p}_ci95"] = ok[f"{p}_excess_ci95"].map(lambda x: f"{x[0]:+.2f} to {x[1]:+.2f}" if isinstance(x, (list, tuple)) and None not in x else "")
    cols = ["experiment", "strategy", "params", "validated", "dev_signals", "dev_stocks", "dev_dates", "dev_win_%", "dev_base_win_%", "dev_lift_pp",
            "dev_mean_net_%", "dev_median_net_%", "dev_excess_%", "dev_ci95", "dev_t", "dev_p5_net_%", "dev_vs_nifty_%",
            "val_signals", "val_win_%", "val_base_win_%", "val_lift_pp", "val_mean_net_%", "val_excess_%", "val_ci95", "val_t", "val_p5_net_%", "neighbours_val_positive"]
    if hold:
        cols += ["hold_signals", "hold_win_%", "hold_lift_pp", "hold_mean_net_%", "hold_excess_%", "hold_ci95", "hold_t"]
    cols += ["dev_warning", "val_warning", "run_key"]
    view = ok[[c for c in cols if c in ok]].round(2)
    st.dataframe(view, hide_index=True, width="stretch", height=520, column_config={
        "params": st.column_config.TextColumn(width="medium"), "validated": st.column_config.CheckboxColumn(),
        "dev_excess_%": st.column_config.NumberColumn("dev excess %", help="Average return minus the eligible-stock average on the same days"),
        "dev_lift_pp": st.column_config.NumberColumn("dev win lift pp")})
    st.download_button("Download comparison (CSV)", view.to_csv(index=False), file_name=f"screener_comparison_h{h}.csv")
    def short(row):
        try:
            p = json.loads(row.params)
        except Exception:
            p = {}
        tail = ", ".join(f"{k.split('.')[-1]}={v}" for k, v in p.items())
        return f"{row.strategy.replace(' v1', '')}" + (f" ({tail})" if tail else "")
    ok["label"] = ok.apply(short, axis=1)
    ok = ok.drop_duplicates(["label", "config_id"])
    a, b = st.columns([1, 1.3])
    with a:
        st.markdown("**Edge over the eligible baseline, development vs validation**")
        fig = go.Figure(go.Scatter(x=ok["dev_excess_%"], y=ok["val_excess_%"], mode="markers", text=ok.label,
                                   marker=dict(size=9, color=[GOOD if v else ACCENT for v in ok.validated.fillna(False)]),
                                   hovertemplate="%{text}<br>dev %{x:+.2f}% · val %{y:+.2f}%<extra></extra>"))
        fig.add_hline(y=0, line_color=REF); fig.add_vline(x=0, line_color=REF)
        fig.update_xaxes(title="development excess %", ticksuffix="%"); fig.update_yaxes(title="validation excess %", ticksuffix="%")
        st.plotly_chart(fig_base(fig, 340), width="stretch")
        st.caption("Top right = beat the baseline in both periods. Green = passed every validation check.")
    with b:
        st.markdown("**Win rate vs baseline (development)**")
        o = ok.sort_values("dev_lift_pp")
        fig = go.Figure(go.Bar(x=o["dev_lift_pp"], y=o.label, orientation="h", text=[f"{v:+.1f}" for v in o["dev_lift_pp"]],
                               marker_color=[GOOD if v > 0 else BAD for v in o["dev_lift_pp"].fillna(0)]))
        fig.update_xaxes(title="win rate minus eligible stocks' (pp)")
        fig.update_yaxes(automargin=True)
        st.plotly_chart(fig_base(fig, max(260, 26 * len(o))), width="stretch")


def detail_tab():
    idx = X.index()
    idx = idx[idx.status == "ok"] if len(idx) else idx
    if idx.empty:
        st.info("No finished experiments."); return
    lab = {r.run_key: f"{r.experiment} · {r.strategy} v{r.strategy_version} {r.params if r.params != '{}' else ''}" for r in idx.itertuples()}
    rk = st.selectbox("Configuration", list(lab), format_func=lab.get, key="det-rk")
    res = X.load_result(rk)
    if res is None:
        st.error("Result file missing."); return
    ev, vd = res["evaluation"], res["verdict"]
    (st.success if vd["validated"] else st.warning)(
        f"**{'Validated research candidate' if vd['validated'] else 'Not validated'}** at {vd['horizon']} sessions "
        f"(development and validation only; holdout never used). Tested as configuration #{vd['configs_tested']}.")
    st.dataframe(pd.DataFrame(vd["checks"]).assign(ok=lambda x: np.where(x.ok, "✅", "❌")), hide_index=True, width="stretch")
    if not vd["search_ok"]:
        st.caption(f"Multiple-testing note: development t is below the {vd['search_t']:.1f} needed after {vd['configs_tested']} configurations.")
    hold = _holdout_shown()
    rows = []
    for p in ["development", "validation"] + (["holdout"] if hold else []):
        for h, s in (ev["by_period"].get(p) or {}).items():
            if not s.get("signals"):
                continue
            rows.append(dict(period=V.PERIOD_LABEL[p], horizon=int(h), signals=s["signals"], stocks=s["stocks"], dates=s["dates"],
                             win_pct=s["positive_rate"], baseline_win_pct=s["baseline_positive_rate"], lift_pp=s["lift_pp"],
                             mean_net=s["mean_net"], median_net=s["median_net"], excess=s["excess"],
                             excess_ci95=f"{s['excess_ci95'][0]:+.2f} to {s['excess_ci95'][1]:+.2f}" if None not in s["excess_ci95"] else "",
                             t=s["t_excess"], p5_net=s["p5_net"], p25_net=s["p25_net"], vs_nifty=s["bench_relative"], warning=s["warning"]))
    st.markdown("**By period and horizon** (net = after the round-trip cost; excess = vs eligible stocks on the same days)")
    show(pd.DataFrame(rows).round(2))
    h = st.selectbox("Breakdowns at horizon", res["config"]["horizons"], index=res["config"]["horizons"].index(res["primary_horizon"]), key="det-h")
    periods = ["development", "validation"] + (["holdout"] if hold else [])
    a, b, c = st.columns(3)
    for col, key, title in ((a, "by_year", "By year"), (b, "by_regime", "By market state"), (c, "by_liquidity", "By liquidity (tercile that day)")):
        d = pd.DataFrame(ev[key])
        if d.empty:
            continue
        d = d[(d.horizon == h) & d.period.isin(periods)]
        with col:
            st.markdown(f"**{title}**")
            fig = go.Figure(go.Bar(x=d.group, y=d.excess, marker_color=[GOOD if v and v > 0 else BAD for v in d.excess],
                                   customdata=np.stack([d.signals, d.period], axis=1),
                                   hovertemplate="%{x}: %{y:+.2f}% vs baseline<br>%{customdata[0]} signals · %{customdata[1]}<extra></extra>"))
            fig.update_yaxes(ticksuffix="%")
            st.plotly_chart(fig_base(fig, 260), width="stretch")
    st.caption(f"Coverage after purging labels that cross period ends: {res['evaluation']['coverage']}")
    with st.expander("Exact configuration"):
        st.json({k: res[k] for k in ("strategy", "strategy_version", "params", "config", "config_id", "data_version", "run_key", "finished")})


def strategies_tab(strategies):
    en = {e["key"]: e for e in X.enabled()}
    st.markdown("Statuses: **experimental** (not yet validated) · **validated research candidate** (passed development and validation "
                "on the latest data, after costs, with enough signals, a downside no worse than the baseline and stable years) · "
                "**enabled** (used by the live screener when the agents run). Enabling an unvalidated strategy is allowed but labelled.")
    for key, s in strategies.items():
        status = X.status_of(key)
        with st.container(border=True):
            c = st.columns([4, 2, 1.2])
            c[0].markdown(f"**{key}** · {STATUS_ICON[status]}  \n{s['description']}  \n<span style='color:{REF}'>Hypothesis: {s.get('hypothesis', '')}</span>",
                          unsafe_allow_html=True)
            idx = X.index()
            own = idx[(idx.strategy == s["name"]) & (idx.status == "ok") & (idx.config_id == SC.config_id(s["config"]))] if len(idx) else idx
            ev_rk = own.sort_values("finished").run_key.iat[-1] if len(own) else None
            c[1].caption(f"File {s['file']} · evaluate on {s['evaluate_on']} · primary horizon {s['primary_horizon']}"
                         + ("" if ev_rk else " · not tested with these exact settings yet"))
            if key in en:
                if c[2].button("Disable", key=f"dis-{key}", width="stretch"):
                    X.set_enabled(key, False); st.rerun()
            else:
                lab = "Enable" if status == "validated" else "Enable (unvalidated)"
                if c[2].button(lab, key=f"en-{key}", width="stretch", type="primary" if status == "validated" else "secondary"):
                    X.set_enabled(key, True, ev_rk, note="" if status == "validated" else "enabled before validation")
                    st.rerun()
    if not en:
        st.caption("Nothing enabled: the agents run the screener defaults, labelled experimental.")
    with st.expander("How to add a strategy or an experiment"):
        st.markdown(
            "- **New strategy:** copy a file in `config/screener/strategies/`, give it a new `name` (or raise `version`), and change only the "
            "settings that differ from the defaults under `config`. Invalid values are rejected with a message.\n"
            "- **New experiment:** add a file in `config/screener/experiments/` listing `strategies` and an optional `grid`, e.g. "
            "`{\"setups.breakout_volume.rel_volume_min\": [1.5, 2.0, 2.5]}`. Every combination is one configuration.\n"
            "- **New setup logic:** a function in `screener/setups.py` plus its parameter ranges in `screener/config.py`.\n"
            "- **Run:** the button in the next tab, or `python3 -m screener experiments`. Finished configurations are skipped.\n"
            "- **Promote:** a strategy whose own configuration validates can be enabled here; the live screener stores and uses that exact configuration.")


def run_experiments_tab(strategies, exps):
    busy = jobs.current() is not None
    rows = []
    for name, e in exps.items():
        try:
            n = len(X.expand(e, strategies)); err = ""
        except SC.ConfigError as ex:
            n, err = 0, str(ex)
        rows.append(dict(experiment=name, description=e.get("description", ""), configurations=n, problem=err))
    show(pd.DataFrame(rows))
    c = st.columns(3)
    if c[0].button("▶ Run all experiments (skips finished ones)", type="primary", disabled=busy, width="stretch"):
        launch("screener-experiments")
    if c[1].button("Re-run everything (force)", disabled=busy, width="stretch", help="Only needed after a code change; new data re-runs automatically"):
        launch("screener-experiments-force")
    if c[2].button("Run today's screen now", disabled=busy, width="stretch"):
        launch("screener")
    st.caption("About 15 seconds per configuration. Results are kept per data version, so new prices mean a fresh run.")
    job_panel("scr")
