"""Stock lookup → News and NSE filings: every stored headline and filing for the stock over the chart's range,
marked on the chart, with how the stock moved (vs the Nifty) on the day and the days after."""
from ui.common import *  # noqa: F401,F403
from core import news as newsmod, announcements as annmod
from research.events import event_day

KIND_STYLE = {"news": dict(symbol="circle", color="#4F46E5", label="News"),
              "announcement": dict(symbol="diamond", color="#D97706", label="NSE filing")}


@st.cache_data(ttl=600)
def _stores(news_mtime, ann_mtime):
    return newsmod.load_store(), annmod.load()


def _mtime(p):
    return p.stat().st_mtime if p.exists() else 0


def stock_events(sym, f):
    """All stored news + filings for `sym`, with event day and abnormal moves. f = the stock's daily bars (sorted)."""
    nw, an = _stores(_mtime(newsmod.STORE) or _mtime(newsmod.LEGACY), _mtime(annmod.STORE))
    rows = []
    if len(nw):
        hit = nw[nw.symbols.fillna("").str.split(",").map(lambda xs: sym in xs)]
        for r in hit.itertuples():
            rows.append(dict(kind="news", published=r.published, type=r.event_type, tone=r.tone, headline=r.title,
                             source=r.source, link=r.link, roundup=bool(r.roundup), sources=int(r.n_sources or 1)))
    if len(an):
        for r in an[an.symbol == sym].itertuples():
            rows.append(dict(kind="announcement", published=r.published, type=r.category, tone=newsmod.tone(f"{r.subject} {r.text}"),
                             headline=(f"{r.subject}: {r.text}" if r.text and r.text != r.subject else r.subject)[:300],
                             source="NSE", link=r.link, roundup=False, sources=1))
    e = pd.DataFrame(rows, columns=["kind", "published", "type", "tone", "headline", "source", "link", "roundup", "sources"])
    if e.empty:
        return e
    dates = pd.DatetimeIndex(f.date)
    idx = pd.read_parquet(C.DATA / "index.parquet")
    idx["date"] = pd.to_datetime(idx.date).dt.tz_localize(None).dt.normalize()
    nifty = idx.set_index("date").nifty.reindex(dates)
    ar = 100 * (f.set_index("date").close.pct_change(fill_method=None) - nifty.pct_change(fill_method=None))
    vol = f.set_index("date").volume
    base_vol = vol.shift(6).rolling(55, min_periods=20).median()
    e["day"] = [event_day(t, dates) for t in e.published]
    e["move_vs_nifty"] = e.day.map(ar)
    pos = {d: i for i, d in enumerate(dates)}
    arr = ar.to_numpy()
    def nxt(d, a, b):
        i = pos.get(d)
        if i is None or i + b >= len(arr):
            return np.nan
        return float(np.nansum(arr[i + a:i + b + 1]))
    e["next_5_days"] = [nxt(d, 1, 5) for d in e.day]
    e["volume_x_normal"] = e.day.map(vol / base_vol)
    return e.sort_values("published", ascending=False).reset_index(drop=True)


def add_markers(fig, e, v):
    """Event markers above the candles (row 1). Hover shows the headlines of that day."""
    if e is None or e.empty:
        return
    hi = v.set_index("date").high
    for kind, sty in KIND_STYLE.items():
        g = e[(e.kind == kind) & e.day.isin(hi.index)]
        if g.empty:
            continue
        per = g.groupby("day").agg(n=("headline", "size"),
                                   text=("headline", lambda s: "<br>".join(f"• {t[:90]}" for t in list(s)[:6]) + (f"<br>… +{len(s) - 6} more" if len(s) > 6 else "")))
        off = 1.03 if kind == "news" else 1.06
        fig.add_trace(go.Scatter(x=per.index, y=hi.reindex(per.index) * off, mode="markers", name=sty["label"],
                                 marker=dict(symbol=sty["symbol"], size=8 + np.minimum(per.n, 5) * 1.5, color=sty["color"],
                                             line=dict(width=1, color="white")),
                                 customdata=np.stack([per.n, per.text], axis=1),
                                 hovertemplate=f"<b>{sty['label']}</b> · %{{customdata[0]}} item(s)<br>%{{customdata[1]}}<extra></extra>"),
                      row=1, col=1)


def section(sym, f, v, e, selected=None):
    """The table under the chart. `selected` = (start, end) picked on the chart, else the chart range."""
    st.subheader("News and NSE filings")
    if e is None or e.empty:
        st.caption("Nothing stored for this stock yet. The store grows every day news is fetched; to add past years run "
                   "Data health → News history → Download older news / NSE filings.")
        return
    lo, hi = (selected if selected else (v.date.min(), v.date.max()))
    c = st.columns([2, 2, 2, 3])
    kinds = c[0].multiselect("Show", ["News", "NSE filing"], default=["News", "NSE filing"], key=f"sn-kind-{sym}")
    roundups = c[1].toggle("Include market round-ups", value=False, key=f"sn-rnd-{sym}",
                           help="Lists like 'stocks to watch' that name many companies at once.")
    types = sorted(e.type.dropna().unique())
    pick = c[2].multiselect("Type", types, key=f"sn-type-{sym}")
    c[3].caption(f"Showing **{pd.Timestamp(lo):%d %b %Y} – {pd.Timestamp(hi):%d %b %Y}**"
                 + (" (your selection on the chart; double-click the chart to clear)" if selected else " (the chart range). "
                    "Drag a box on the price chart to narrow it."))
    want = {"News": "news", "NSE filing": "announcement"}
    s = e[e.kind.isin([want[k] for k in kinds]) & (e.published >= pd.Timestamp(lo)) & (e.published < pd.Timestamp(hi) + pd.Timedelta(days=1))]
    if not roundups:
        s = s[~s.roundup]
    if pick:
        s = s[s.type.isin(pick)]
    if s.empty:
        older = e[e.published < pd.Timestamp(lo)]
        st.caption("No items in this range." + (f" The stored history for this stock starts {e.published.min():%d %b %Y}." if len(e) else "")
                   + (" Try a longer chart range." if len(older) else ""))
        return
    m = st.columns(4)
    m[0].metric("Items", len(s), help="Headlines and filings in the range (duplicates from several publishers counted once).")
    m[1].metric("Days with news", s.day.nunique())
    big = s.dropna(subset=["move_vs_nifty"])
    m[2].metric("Avg move on those days", f"{big.drop_duplicates('day').move_vs_nifty.abs().mean():.1f}%" if len(big) else "—",
                help="Average size (up or down) of the stock's move vs the Nifty on days with news or filings.")
    quiet = _quiet_avg(f, v, set(e.day.dropna()))
    m[3].metric("Avg move on other days", f"{quiet:.1f}%" if pd.notna(quiet) else "—",
                help="Same, on days in the range with nothing stored. If news matters, the left number is bigger.")
    t = s.assign(when=s.published.dt.strftime("%d %b %Y %H:%M"), kind=s.kind.map(lambda k: KIND_STYLE[k]["label"]),
                 traded_on=s.day.dt.strftime("%d %b"))
    st.dataframe(t[["when", "kind", "type", "tone", "headline", "source", "traded_on", "move_vs_nifty", "next_5_days", "volume_x_normal", "link"]],
                 hide_index=True, width="stretch", height=min(560, 40 + 35 * len(t)),
                 column_config={"link": st.column_config.LinkColumn("Open", display_text="open"),
                                "headline": st.column_config.TextColumn(width="large"),
                                "traded_on": st.column_config.TextColumn("First trading day", help="Published after 3:30 pm or on a holiday → next trading day."),
                                "move_vs_nifty": st.column_config.NumberColumn("Move that day vs Nifty", format="%+.1f%%"),
                                "next_5_days": st.column_config.NumberColumn("Next 5 days vs Nifty", format="%+.1f%%"),
                                "volume_x_normal": st.column_config.NumberColumn("Volume × normal", format="%.1f×"),
                                "sources": None})
    st.caption("Moves are the stock's return minus the Nifty 50's on the same day. Headlines only (no article text); "
               "older years exist only as far as the history downloads reached.")
    history(sym, e)


def _quiet_avg(f, v, busy_days):
    d = f.set_index("date").close.pct_change(fill_method=None)
    idx = pd.read_parquet(C.DATA / "index.parquet")
    idx["date"] = pd.to_datetime(idx.date).dt.tz_localize(None).dt.normalize()
    n = idx.set_index("date").nifty.reindex(d.index).pct_change(fill_method=None)
    ar = (100 * (d - n)).loc[v.date.min():v.date.max()]
    ar = ar[~ar.index.isin(busy_days)]
    return float(ar.abs().mean()) if len(ar) else np.nan


def history(sym, e):
    """How this stock reacted to each type of event across all stored history."""
    g = e.dropna(subset=["move_vs_nifty"])
    g = g[~g.roundup]
    if g.day.nunique() < 5:
        return
    with st.expander(f"How {sym} reacted to past news and filings (all stored history)"):
        one = g.sort_values("sources", ascending=False).drop_duplicates(["day", "type"])
        s = one.groupby(["kind", "type"]).agg(times=("day", "nunique"), avg_size=("move_vs_nifty", lambda x: x.abs().mean()),
                                              avg_move=("move_vs_nifty", "mean"), avg_next_5=("next_5_days", "mean"),
                                              volume=("volume_x_normal", "median")).reset_index().sort_values("times", ascending=False)
        s["kind"] = s.kind.map(lambda k: KIND_STYLE[k]["label"])
        st.dataframe(s, hide_index=True, width="stretch",
                     column_config={"avg_size": st.column_config.NumberColumn("Avg size of move", format="%.1f%%"),
                                    "avg_move": st.column_config.NumberColumn("Avg move (signed)", format="%+.1f%%"),
                                    "avg_next_5": st.column_config.NumberColumn("Avg next 5 days", format="%+.1f%%"),
                                    "volume": st.column_config.NumberColumn("Volume × normal", format="%.1f×")})
        st.caption("Few events per row → treat as anecdotes, not evidence. The market-wide test is on the News research page.")
