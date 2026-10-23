"""News research page (does news / NSE filings move stocks?) and the news controls on Data health."""
import json
from ui.common import *  # noqa: F401,F403
from core import jobs, news as newsmod, announcements as annmod, news_sources as S

OUT = C.RESULTS / "news_events"
STATUS_ICON = {"ok": "🟢", "empty": "🟡", "stale": "🟡", "dead": "🔴", "error": "🔴", "blocked": "⛔"}


def _read(name):
    p = OUT / f"{name}.csv"
    try:
        return pd.read_csv(p) if p.exists() and p.stat().st_size > 2 else pd.DataFrame()
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _store_stats():
    nw = newsmod.load_store()
    an = annmod.load()
    return nw, an


# ---------------- Data health section ----------------
def data_section(busy):
    from news_fetch import options, save_options
    st.subheader("News and NSE filings")
    nw, an = _store_stats()
    c = st.columns(4)
    c[0].metric("Headlines stored", f"{len(nw):,}")
    c[1].metric("Linked to a stock", f"{int(nw.symbols.fillna('').astype(bool).sum()):,}" if len(nw) else "0")
    c[2].metric("News from", f"{nw.published.min():%d %b %Y}" if len(nw) else "—")
    c[3].metric("NSE filings stored", f"{len(an):,}", f"from {an.published.min():%d %b %Y}" if len(an) else None, delta_color="off")

    b = st.columns(4)
    if b[0].button("Fetch latest news + filings", width="stretch", disabled=jobs.busy("announcements"), type="primary"):
        launch("announcements")
    if b[1].button("Check news sources", width="stretch", disabled=jobs.busy("news-check"),
                   help="Tries every feed and sitemap once (about a minute) and lists what works."):
        launch("news-check")
    if b[2].button("Re-link headlines to stocks", width="stretch", disabled=jobs.busy("news-retag"),
                   help="Run after editing data/news_aliases.csv (extra names for a company, one per row: symbol,alias)."):
        launch("news-retag")
    st.caption("Older news, NSE filings and article text: see History downloads above.")
    b = st.columns(4)
    o = options()
    g = b[2].toggle("Google News search for my stocks", value=o["gnews"],
                    help="Each news fetch also searches Google News for the stocks you hold, the momentum portfolio and the screener "
                         f"shortlist (up to {o['gnews_max']}). Off by default; skipped automatically if Google's robots.txt disallows it.")
    sm = b[3].toggle("Read news sitemaps", value=o["sitemaps"], help="Publishers' last-48-hours sitemaps: more headlines than RSS.")
    if (g, sm) != (o["gnews"], o["sitemaps"]):
        save_options({"gnews": g, "sitemaps": sm})
        st.toast("Saved. Applies from the next news fetch.")

    bf = annmod.BACKFILL_STATE
    nbf = newsmod.BACKFILL
    notes = []
    if nbf.exists():
        try:
            notes.append(f"older news: {len(json.loads(nbf.read_text()).get('done', []))} archive files read")
        except Exception:
            pass
    if bf.exists():
        try:
            s = json.loads(bf.read_text())
            notes.append(f"older filings: {len(s.get('done', []))} stock-years done")
        except Exception:
            pass
    if notes:
        st.caption("Progress: " + " · ".join(notes) + ". Both downloads resume where they stopped.")

    stt = S.load_state()
    with st.expander("News sources: what works" + (f" (checked {stt.get('checked', '')[:16]})" if stt else ""), expanded=False):
        if not stt:
            st.caption("Not checked yet. Press Check news sources.")
        else:
            h = pd.DataFrame(stt.get("sources", []))
            if len(h):
                h["status"] = h.status.map(lambda s: f"{STATUS_ICON.get(s, '⚪')} {s}")
                show(h[[c for c in ["source", "method", "status", "items", "newest", "note"] if c in h]])
            st.caption("dead = no items or nothing newer than a week (dropped from fetches until it recovers). "
                       "blocked = robots.txt disallows it, so it is not read.")


# ---------------- News research page ----------------
def page():
    st.title("News research")
    st.caption("Hypothesis: news and NSE filings move stocks. Test: on days a stock has news or a filing, is its move "
               "(vs the Nifty 50) bigger than on its quiet days, and does the move continue afterwards?")
    busy = jobs.current() is not None
    meta = json.loads((OUT / "meta.json").read_text()) if (OUT / "meta.json").exists() else None
    c = st.columns([1, 3])
    if c[0].button("Run the news study", type="primary", width="stretch", disabled=busy,
                   help="Uses every stored headline and filing (a minute or two)."):
        launch("news-research")
    if meta:
        cov = meta.get("coverage", {})
        c[1].caption(f"Last run {meta['when'][:16]} · {cov.get('events', 0):,} events on {cov.get('stocks', 0)} stocks · "
                     f"{cov.get('first', '?')} to {cov.get('last', '?')} · {cov.get('news', 0):,} news, {cov.get('announcements', 0):,} filings")
    with st.expander("Job output", expanded=busy):
        job_panel("nr")
    if not meta:
        st.info("No results yet. Press **Run the news study**. The more history is stored (Data health → Download older news / "
                "NSE filings), the more the answer means.")
        return
    cov = meta.get("coverage", {})
    if cov.get("events", 0) < 500:
        st.warning(f"Only {cov.get('events', 0)} events so far: too few for conclusions. Download older news and filings first.")

    s = _read("summary")
    if len(s):
        r = s.iloc[0]
        st.subheader("1. Are moves bigger on news days?")
        m = st.columns(4)
        m[0].metric("Avg move on event days", f"{r.abs_move_event_day:.2f}%", help="Size of the stock's move vs Nifty, up or down.")
        m[1].metric("Avg move on quiet days", f"{r.abs_move_quiet_day:.2f}%", help="Same stocks, days with nothing stored within ±5 days.")
        m[2].metric("Ratio", f"{r.times_bigger:.1f}×")
        m[3].metric("Moves over 3%", f"{r.pct_moves_over_3:.0f}% vs {r.pct_quiet_over_3:.0f}%", help="Share of event days vs quiet days.")
        bm = meta.get("big_moves", {})
        if bm.get("big_moves"):
            st.markdown(f"Of **{bm['big_moves']:,}** stock-days with a move over {bm['threshold']:.0f}% vs the Nifty (on {bm.get('days_covered', '?')} days "
                        f"with stored news), **{bm['pct_with_event']:.0f}%** had news or a filing that day or the day before. "
                        "The rest moved without anything we stored: the sources miss some news, and some moves have none.")

    st.subheader("2. By source and type")
    st.caption("times_bigger = average move on those event days ÷ average quiet-day move. t-stats use one average per day "
               "(events on the same day aren't independent); |t| above about 2 is unlikely to be luck, but many rows are tested, "
               "so expect a few by chance.")
    cols = {"events": "events", "stocks": "stocks", "times_bigger": st.column_config.NumberColumn("× quiet day", format="%.1f×"),
            "abs_move_event_day": st.column_config.NumberColumn("Avg size", format="%.2f%%"),
            "event_day_mean": st.column_config.NumberColumn("Event day (signed)", format="%+.2f%%"),
            "before_5d_mean": st.column_config.NumberColumn("5 days before", format="%+.2f%%"),
            "after_1_5d_mean": st.column_config.NumberColumn("Days 1–5 after", format="%+.2f%%"),
            "after_1_5d_t": st.column_config.NumberColumn("t (1–5)", format="%.1f"),
            "after_1_20d_mean": st.column_config.NumberColumn("Days 1–20 after", format="%+.2f%%"),
            "after_1_20d_t": st.column_config.NumberColumn("t (1–20)", format="%.1f"),
            "abn_volume_median": st.column_config.NumberColumn("Volume × normal", format="%.1f×")}
    keep = ["group", "events", "stocks", "abs_move_event_day", "times_bigger", "event_day_mean", "before_5d_mean", "after_1_5d_mean",
            "after_1_5d_t", "after_1_20d_mean", "after_1_20d_t", "abn_volume_median"]
    for name, title in (("by_source", "News vs NSE filings"), ("by_category", "By type of event"), ("by_sector", "By sector")):
        d = _read(name)
        st.markdown(f"**{title}**")
        if d.empty:
            st.caption("Not enough events per group yet (20 needed).")
        else:
            st.dataframe(d[[k for k in keep if k in d]], hide_index=True, width="stretch", column_config=cols, height=min(520, 40 + 35 * len(d)))

    st.subheader("3. Does the direction of the headline predict what follows?")
    t = _read("by_tone")
    if t.empty:
        st.caption("Not enough positive / negative events yet.")
    else:
        fig = go.Figure()
        for (src, tone), g in t.groupby(["source", "tone"]):
            fig.add_bar(x=g.window, y=g.mean_abnormal_pct, name=f"{src} · {tone}",
                        marker_color=GOOD if tone == "positive" else BAD, opacity=1 if src == "news" else 0.55,
                        customdata=np.stack([g.events, g.t.round(1)], axis=1),
                        hovertemplate="%{x}: %{y:+.2f}%<br>%{customdata[0]} events, t %{customdata[1]}<extra>%{fullData.name}</extra>")
        fig.update_layout(barmode="group", yaxis_title="Avg move vs Nifty, %")
        st.plotly_chart(fig_base(fig, 340, legend=True), width="stretch")
        st.caption("Tone is a word list (surge, record, upgrade … vs plunge, downgrade, probe …): rough. A continuing move on days 1–20 "
                   "is the tradable part; the event-day move is mostly gone by the time you could act on it.")
    st.caption("Limits: headlines only; publish times are as the source states them (archive sitemaps give the date only); "
               "the stock link is by company name, so a few links are wrong; only stocks in today's list are matched.")
