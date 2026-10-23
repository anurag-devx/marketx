"""Screener tabs for market data, news and NSE announcements."""
from ui.common import *  # noqa: F401,F403
from screener import market_scan as MS, news_scan as NS, interface
from core import news as newsmod, announcements as ann


def _features():
    from ui.screener import inputs
    return inputs()[0]


@st.cache_data(ttl=600, show_spinner="Working out the day's moves for every stock...")
def _snapshot(version, date):
    return MS.snapshot(_features(), date)


def snapshot(date=None):
    from screener.screen import data_version
    return _snapshot(data_version(), None if date is None else str(date))


def _shortlist_symbols():
    try:
        sl = interface.load_shortlist()
        return set() if sl.empty() else set(sl.rows.symbol)
    except Exception:
        return set()


def _h(df, cap=480):
    return int(min(cap, 40 + 35 * max(len(df), 1)))


def _open_stock(symbols, key):
    syms = sorted(set(symbols))
    if not syms:
        return
    c = st.columns([3, 1])
    sym = c[0].selectbox("Open a stock in Stock lookup", syms, key=f"{key}-open-sym", index=None, placeholder="Choose a stock")
    if c[1].button("Open", key=f"{key}-open-go", disabled=sym is None, width="stretch"):
        go_stock(sym)


# =============================================================== market movers
def market_tab():
    feat = _features()
    dates = sorted(feat.date.unique())
    uni = universe()
    with st.container(border=True):
        st.markdown("##### What to look for")
        c = st.columns([2.2, 1.2, 1])
        preset = c[0].selectbox("Scan", list(MS.PRESETS), key="mm-preset")
        d = c[1].date_input("Date", value=pd.Timestamp(dates[-1]).date(), min_value=pd.Timestamp(dates[260]).date(),
                            max_value=pd.Timestamp(dates[-1]).date(), key="mm-date")
        limit = c[2].number_input("Show up to", 10, 2000, 100, 10, key="mm-limit")
    with st.container(border=True):
        st.markdown("##### Which stocks")
        c = st.columns([1, 1, 2.5, 1.5])
        mp = c[0].number_input("Price at least ₹", 0.0, 100000.0, 20.0, 5.0, key="mm-price")
        mt = c[1].number_input("Traded value at least ₹ cr/day", 0.0, 10000.0, 2.0, 1.0, key="mm-turn", help="Median of the previous 20 sessions")
        secs = c[2].multiselect("Sectors (empty = all)", sorted(uni.Industry.dropna().unique()), key="mm-sec")
        only = c[3].radio("Limit to", ["all", "nifty500", "shortlist"], key="mm-only",
                          format_func={"all": "All stocks", "nifty500": "Today's Nifty 500", "shortlist": "Agents' shortlist"}.get)
    snap, day = snapshot(pd.Timestamp(d))
    syms = None if only == "all" else (set(uni.Symbol) if only == "nifty500" else _shortlist_symbols())
    rows, counts = MS.scan(snap, preset, mp, mt, secs, syms, int(limit))
    st.markdown(f"**{counts[f'match: {preset}']} stocks match** on {pd.Timestamp(day).date()}"
                + (f" (showing {len(rows)})" if len(rows) < counts[f"match: {preset}"] else ""))
    st.caption(" → ".join(f"{k}: {v}" for k, v in counts.items()))
    st.dataframe(rows, hide_index=True, width="stretch", height=460, column_config={
        "company_name": st.column_config.TextColumn("company"),
        "change_1d_pct": st.column_config.NumberColumn("1 day %", format="%+.2f"), "gap_pct": st.column_config.NumberColumn("gap %", format="%+.2f"),
        "change_5d_pct": st.column_config.NumberColumn("5 days %", format="%+.1f"), "change_1m_pct": st.column_config.NumberColumn("1 month %", format="%+.1f"),
        "change_3m_pct": st.column_config.NumberColumn("3 months %", format="%+.1f"), "rel_volume": st.column_config.NumberColumn("volume ×", format="%.1f"),
        "from_52w_high_pct": st.column_config.NumberColumn("vs 52w high %", format="%+.1f"),
        "from_52w_low_pct": st.column_config.NumberColumn("vs 52w low %", format="%+.1f"),
        "new_52w_high": st.column_config.CheckboxColumn("new high"), "new_52w_low": st.column_config.CheckboxColumn("new low"),
        "rsi14": st.column_config.NumberColumn("RSI", format="%.0f"), "turnover_cr": st.column_config.NumberColumn("traded ₹ cr", format="%.1f"),
        "median_value_20d_cr": st.column_config.NumberColumn("usual ₹ cr/day", format="%.1f"), "close": st.column_config.NumberColumn(format="%.2f")})
    c = st.columns([1, 3])
    c[0].download_button("Download (CSV)", rows.to_csv(index=False), file_name=f"market_{preset.split(' (')[0].replace(' ', '_')}_{pd.Timestamp(day).date()}.csv")
    _open_stock(rows.symbol, "mm")
    with st.expander("Sectors on this day"):
        show(MS.sector_summary(snap).round(2))


# =============================================================== news
@st.cache_data(ttl=300)
def _news(fetched):
    n, errors, when = newsmod.load()
    return NS.prepare_news(n, universe()), errors, when


def news_tab():
    _, errors, when = newsmod.load()
    c = st.columns([1.2, 4])
    if c[0].button("Refresh news", width="stretch", disabled=jobs.busy("news"), key="nw-refresh"):
        launch("news")
    if when is None:
        c[1].info("No news downloaded yet. Press Refresh news (needs internet on this Mac).")
        return
    c[1].caption(f"Last fetched {str(when)[:16]} from news feeds and publishers' news sitemaps · sources and older history: Data health."
                 + (f" Not working: {', '.join(errors[:6])}" if errors else ""))
    d, _, _ = _news(when)
    uni = universe()
    with st.container(border=True):
        st.markdown("##### Which headlines")
        c = st.columns([1.2, 2, 2, 2.5])
        hours = c[0].selectbox("From the last", [6, 24, 72, 168, 720, 24 * 365 * 15], index=1, key="nw-h",
                               format_func=lambda h: "all stored" if h > 9000 else f"{h} hours" if h < 72 else f"{h // 24} days")
        srcs = c[1].multiselect("Sources", sorted(d.source.dropna().unique()), key="nw-src")
        types = c[2].multiselect("Type", sorted(d.event_type.dropna().unique()), key="nw-kind",
                                 help="Results, broker view, order win… from keyword rules on the headline")
        kw = c[3].text_input("Words (comma-separated, any)", key="nw-kw", placeholder="results, order, RBI, crude")
    with st.container(border=True):
        st.markdown("##### About which stocks")
        c = st.columns([2.5, 2.5, 1.6, 1.4])
        syms = c[0].multiselect("Stocks", sorted(uni.Symbol), key="nw-sym")
        secs = c[1].multiselect("Sectors", sorted(uni.Industry.dropna().unique()), key="nw-sec")
        tones = c[2].multiselect("Tone (rough)", ["positive", "negative", "neutral"], key="nw-tone",
                                 help="Keyword-based label on the headline; a reading aid, not a signal")
        tagged = c[3].toggle("Only headlines naming a stock", key="nw-tag")
        no_round = c[3].toggle("Hide market round-ups", key="nw-rnd", value=True, help="'Stocks to watch' style lists naming many companies")
        only_sl = c[3].toggle("Only stocks on the agents' shortlist", key="nw-sl")
    since = pd.Timestamp.now() - pd.Timedelta(hours=hours)
    sl = _shortlist_symbols()
    v = NS.filter_items(d, since, srcs, (), kw, syms, secs, tones, tagged)
    if types:
        v = v[v.event_type.isin(types)]
    if no_round:
        v = v[~v.roundup.fillna(False).astype(bool)]
    if only_sl:
        v = NS.filter_items(v, symbols=sl or {"__none__"})
    st.markdown(f"**{len(v)} headlines** of {len(d)} downloaded")
    a, b = st.columns([3, 2])
    with a:
        t = v.assign(stocks=v.symbols.map(", ".join))[["published", "source", "event_type", "tone", "title", "stocks", "link"]]
        st.dataframe(t, hide_index=True, width="stretch", height=_h(v), column_config={
            "published": st.column_config.DatetimeColumn("time", format="DD MMM HH:mm"),
            "event_type": "type", "title": st.column_config.TextColumn("headline", width="large"), "link": st.column_config.LinkColumn("open", display_text="open")})
    with b:
        st.markdown("**Stocks in these headlines**")
        snap, _ = snapshot()
        s = NS.stocks_in_items(v, snap, sl)
        show(s, "No stock named in these headlines.", height=_h(v), column_config={
            "latest": st.column_config.DatetimeColumn(format="DD MMM HH:mm"), "latest_item": st.column_config.TextColumn("latest headline"),
            "change_1d_pct": st.column_config.NumberColumn("last day %", format="%+.2f"), "rel_volume": st.column_config.NumberColumn("volume ×", format="%.1f")})
    st.caption("Stock tags match the ticker or a distinctive part of the company name in the headline; they can miss or over-match.")
    _open_stock([x for xs in v.symbols for x in xs], "nw")


# =============================================================== announcements
@st.cache_data(ttl=300)
def _ann(mtime):
    return NS.prepare_announcements(ann.load(), universe())


def announcements_tab():
    info = ann.last_fetch()
    c = st.columns([1.4, 4])
    if c[0].button("Fetch announcements", width="stretch", disabled=jobs.busy("announcements"), key="an-refresh",
                   help="NSE's announcements RSS (latest filings) plus a best-effort read of the last 7 days from NSE's website"):
        launch("announcements")
    if info is None:
        c[1].info("Nothing downloaded yet. Press Fetch announcements (needs internet on this Mac). They are also fetched with "
                  "every data download, and each fetch adds to the history kept in data/announcements.parquet.")
        return
    c[1].caption(f"Last fetch {info['when']}: {info['fetched']} read ({', '.join(f'{k} {v}' for k, v in info['sources'].items()) or 'none'}), "
                 f"{info['added']} new, {info['total']} kept in history.")
    for e in info.get("errors", []):
        st.warning(e)
    mt = ann.STORE.stat().st_mtime if ann.STORE.exists() else 0
    d = _ann(mt)
    if d.empty:
        st.caption("No announcements stored yet.")
        return
    uni = universe()
    with st.container(border=True):
        st.markdown("##### Which filings")
        c = st.columns([1.2, 3, 2.5])
        days = c[0].selectbox("From the last", [1, 3, 7, 30, 90, 365], index=2, key="an-d", format_func=lambda x: f"{x} day{'s' if x > 1 else ''}")
        cats = c[1].multiselect("Type", [x[0] for x in ann.CATEGORIES] + ["Other"], key="an-cat",
                                default=[x for x in ("Results", "Order / contract", "Acquisition / merger", "Fund raising", "Dividend",
                                                     "Bonus / split", "Buyback", "Credit rating", "Legal / regulatory") if x in set(d.category)])
        kw = c[2].text_input("Words in the filing (comma-separated, any)", key="an-kw", placeholder="crore, NHAI, defence")
    with st.container(border=True):
        st.markdown("##### About which stocks")
        c = st.columns([2.5, 2.5, 1.8])
        syms = c[0].multiselect("Stocks", sorted(set(d.symbol.dropna()) | set(uni.Symbol)), key="an-sym")
        secs = c[1].multiselect("Sectors", sorted(uni.Industry.dropna().unique()), key="an-sec")
        n500 = c[2].toggle("Only today's Nifty 500", value=True, key="an-n500")
        only_sl = c[2].toggle("Only the agents' shortlist", key="an-sl")
    since = pd.Timestamp.now() - pd.Timedelta(days=days)
    v = NS.filter_items(d, since, keywords=kw, symbols=syms, sectors=secs, text_cols=("subject", "text", "company"))
    if cats:
        v = v[v.category.isin(cats)]
    if n500:
        v = v[v.in_nifty500]
    sl = _shortlist_symbols()
    if only_sl:
        v = v[v.symbol.isin(sl)]
    st.markdown(f"**{len(v)} filings** of {len(d)} stored")
    st.caption("Types come from keyword rules on the filing text: they say what was filed, not whether it is good news.")
    a, b = st.columns([3, 2])
    with a:
        show(v[["published", "symbol", "company", "category", "subject", "text", "link"]], "No filings match.", height=_h(v), column_config={
            "published": st.column_config.DatetimeColumn("time", format="DD MMM HH:mm"),
            "text": st.column_config.TextColumn("filing", width="large"), "link": st.column_config.LinkColumn("PDF", display_text="PDF")})
    with b:
        st.markdown("**Stocks with filings**")
        snap, _ = snapshot()
        show(NS.stocks_in_items(v, snap, sl), "None.", height=_h(v), column_config={
            "latest": st.column_config.DatetimeColumn(format="DD MMM HH:mm"), "latest_item": st.column_config.TextColumn("latest filing"),
            "change_1d_pct": st.column_config.NumberColumn("last day %", format="%+.2f"), "rel_volume": st.column_config.NumberColumn("volume ×", format="%.1f")})
    st.download_button("Download (CSV)", v.drop(columns=["symbols", "sectors"]).to_csv(index=False), file_name="nse_announcements.csv")
    _open_stock(v.symbol.dropna(), "an")
