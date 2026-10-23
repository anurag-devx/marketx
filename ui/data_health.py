"""Data health: freshness, downloads (update / full / all NSE stocks), quality checks, corporate actions, universe."""
from ui.common import *  # noqa: F401,F403
from core import quality, universe_pit
from core.data import has_broad, load_raw
from ui import news_research


def data_age():
    """(last bar, age in days, fetch log dict or None)."""
    px = prices()
    if px is None:
        return None, None, None
    last = px.date.max()
    log = None
    f = C.DATA / "fetch_log.json"
    if f.exists():
        try:
            log = json.loads(f.read_text())
        except Exception:
            pass
    return last, (pd.Timestamp.now().normalize() - last).days, log


def status_line():
    """One line for the Overview: data age and quality."""
    last, age, log = data_age()
    if last is None:
        return "error", "No price data yet."
    qs = quality.summary()
    parts = [f"Prices to **{last.date()}** ({'today' if age == 0 else f'{age} day(s) old'})"]
    if log:
        parts.append(f"last download {log['when'][:16]} ({log['mode']})")
    lvl = "ok" if age <= 4 else "error"
    if qs:
        if qs["quarantined"]:
            parts.append(f"**{len(qs['quarantined'])} stock(s) paused for recent data errors** ({', '.join(qs['quarantined'][:5])})")
            lvl = "warn" if lvl == "ok" else lvl
        elif qs["status"] == "errors":
            parts.append(f"{qs['errors']} older data issue(s) to review in Data health (none affects today's signals)")
        elif qs["status"] == "warnings":
            parts.append(f"{qs['warnings']} data warning(s) to look at")
        else:
            parts.append("data checks clean")
    return lvl, " · ".join(parts)


def page():
    st.title("Data health")
    last, age, log = data_age()
    busy = jobs.current() is not None
    if last is not None:
        px = prices()
        c = st.columns(4)
        c[0].metric("Last bar", str(last.date()), f"{age} day(s) old", delta_color="inverse" if age > 4 else "off")
        c[1].metric("Nifty 500 stocks", px.symbol.nunique())
        c[2].metric("History from", str(px.date.min().date()))
        c[3].metric("Other NSE stocks", "downloaded" if has_broad() else "not downloaded",
                    help="Needed for survivorship-free (point-in-time) tests in the Strategy Lab")
        if age > 4:
            st.error("Data is stale: the agents won't recommend new trades until it is refreshed.")

    st.subheader("Download")
    c = st.columns(3)
    if c[0].button("Update prices (new days only)", type="primary", width="stretch", disabled=busy,
                   help="A few minutes. Re-reads the last 10 days; a stock whose history changed (split/bonus) is re-downloaded in full."):
        launch("fetch")
    if c[1].button("Re-download all history", width="stretch", disabled=busy, help="About 30 minutes. Only if something looks wrong."):
        launch("fetch-full")
    if c[2].button("Download all NSE stocks" if not has_broad() else "Update all NSE stocks", width="stretch", disabled=busy,
                   help="Every NSE equity outside today's Nifty 500, for point-in-time tests. 3–4 hours the first time (Angel allows 5,000 requests an hour); "
                        "after that every price update includes them."):
        launch("fetch-broad")
    if log:
        n5 = log.get("nifty500", {})
        st.caption(f"Last download {log['when'][:16]} ({log['mode']}): {n5.get('stocks', '?')} stocks, "
                   f"{n5.get('new', 0)} new, {n5.get('refetched', 0)} re-downloaded after a split/bonus, "
                   f"{len(n5.get('missing', []))} missing, {n5.get('seconds', 0) // 60} min.")
    with st.expander("Job output", expanded=busy):
        job_panel("dh")

    from ui import history
    history.panel(busy)

    news_research.data_section(busy)

    st.subheader("Quality checks")
    qs = quality.summary()
    c = st.columns([1, 4])
    if c[0].button("Run checks now", width="stretch", disabled=busy):
        launch("quality")
    if not qs:
        st.caption("Not run yet. Checks run after every download and before every agent run.")
    else:
        icon = {"ok": "🟢", "warnings": "🟡", "errors": "🔴"}[qs["status"]]
        c[1].markdown(f"{icon} **{qs['status'].capitalize()}** · {qs['errors']} error(s), {qs['warnings']} warning(s), "
                      f"{qs['reviewed']} reviewed · checked {qs['checked_at'][:16]}")
        if qs["quarantined"]:
            st.warning(f"No new signals for **{', '.join(qs['quarantined'])}** until their recent errors are reviewed or fixed.")
        iss = pd.read_csv(quality.ISSUES) if quality.ISSUES.exists() else pd.DataFrame()
        if len(iss):
            label = {"split_gap": "Split/bonus not adjusted", "bad_tick": "One wrong bar", "ohlc": "Impossible bar", "ohlc_minor": "Bar slightly off",
                     "big_move": "Big one-day move", "missing_days": "Missing days", "long_gap": "Long gap", "stale": "No recent bar",
                     "duplicates": "Duplicate dates"}
            f = st.columns([2, 2, 1])
            sev = f[0].multiselect("Severity", ["error", "warning"], default=["error", "warning"])
            chk = f[1].multiselect("Check", sorted(iss.check.unique()), format_func=lambda x: label.get(x, x))
            show_rev = f[2].toggle("Show reviewed")
            v = iss[iss.severity.isin(sev)]
            if chk:
                v = v[v.check.isin(chk)]
            if not show_rev:
                v = v[~v.reviewed]
            v = v.assign(what=v.check.map(label)).sort_values(["severity", "date"], ascending=[True, False])
            ev = st.dataframe(v[["severity", "what", "symbol", "date", "detail", "reviewed"]], hide_index=True, width="stretch", height=320,
                              on_select="rerun", selection_mode="multi-row", key="qi")
            rows = ev.selection.rows if ev and hasattr(ev, "selection") else []
            c = st.columns([2, 4])
            note = c[1].text_input("Note (why the data is right)", key="qnote", placeholder="e.g. real crash on results day")
            if c[0].button(f"Mark {len(rows)} selected as reviewed", disabled=not rows, width="stretch"):
                quality.mark_reviewed(v.iloc[rows][["symbol", "date", "check"]].fillna("").to_dict("records"), note)
                px_, idx_, _ = load_raw()
                quality.run(px_, idx_)
                st.rerun()
            st.caption("Select rows, check the stock in Stock lookup, then mark them reviewed if the data is right. "
                       "If a split or bonus was missed, add it below instead.")

    st.subheader("Corporate actions you add")
    st.caption("For a split or bonus the download didn't catch. Factor 2 = 1:1 bonus or 2:1 split; 1.5 = 1:2 bonus; 5 = 5:1 split. "
               "Prices before the ex-date are divided by the factor when loading. If Angel later adjusts its own history, "
               "the loader sees the gap is gone and skips it, so nothing is adjusted twice.")
    ca = pd.read_csv(quality.ACTIONS) if quality.ACTIONS.exists() else pd.DataFrame(columns=["symbol", "ex_date", "factor", "note"])
    ed = st.data_editor(ca, num_rows="dynamic", width="stretch", hide_index=True, key="ca",
                        column_config={"factor": st.column_config.NumberColumn(min_value=1.0, max_value=100.0, step=0.5),
                                       "ex_date": st.column_config.TextColumn(help="YYYY-MM-DD, the first day at the new price")})
    if st.button("Save corporate actions"):
        ed.dropna(subset=["symbol", "ex_date", "factor"]).to_csv(quality.ACTIONS, index=False)
        st.cache_data.clear()
        st.success("Saved. They apply from the next agent run, Lab test and quality check.")
    if len(ca) and prices() is not None:
        _, done = quality.apply_corporate_actions(prices())
        if done:
            show(pd.DataFrame(done))
    sa = C.DATA / "split_adjustments.csv"
    with st.expander("Adjustments made automatically by the download"):
        if sa.exists() and sa.stat().st_size > 1:
            show(pd.read_csv(sa))
        else:
            st.caption("None.")

    st.subheader("Stock universe")
    uni = universe()
    px = prices()
    if px is not None:
        miss = sorted(set(uni.Symbol) - set(px.symbol))
        st.markdown(f"Today's Nifty 500 list: **{len(uni)}** stocks, **{len(miss)}** without data"
                    + (f" ({', '.join(miss[:15])}{' …' if len(miss) > 15 else ''})" if miss else "") + ".")
    ch = C.DATA / "universe_changes.csv"
    if ch.exists():
        with st.expander("Nifty 500 changes seen by the download"):
            show(pd.read_csv(ch).sort_values("date", ascending=False))
    mem = universe_pit.MEMBERSHIP
    st.caption("Point-in-time universe for the Strategy Lab: "
               + ("official list in data/membership.csv." if mem.exists() else
                  "top 500 by traded value at each March/September review" + (" across all NSE stocks." if has_broad()
                                                                               else ". Download all NSE stocks above for it to include stocks that later left the index.")))
