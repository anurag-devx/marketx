"""Quarterly results screens: Screener → Results tab and the Stock lookup section."""
from ui.common import *  # noqa: F401,F403
from core import fundamentals as F

COLS_FMT = {
    "filed_at": st.column_config.DatetimeColumn("filed", format="DD MMM YY HH:mm"),
    "revenue_cr": st.column_config.NumberColumn("revenue ₹ cr", format="%,.0f"),
    "revenue_yoy": st.column_config.NumberColumn("revenue YoY %", format="%+.1f"),
    "profit_cr": st.column_config.NumberColumn("profit ₹ cr", format="%,.1f"),
    "profit_yoy": st.column_config.NumberColumn("profit YoY %", format="%+.1f"),
    "margin_pct": st.column_config.NumberColumn("net margin %", format="%.1f"),
    "ebitda_margin": st.column_config.NumberColumn("EBITDA margin %", format="%.1f",
                                                   help="Proxy: profit before tax + finance costs + depreciation − other income"),
    "eps": st.column_config.NumberColumn("EPS ₹", format="%.2f"),
    "sue_profit": st.column_config.NumberColumn("profit surprise (SUE)", format="%+.1f",
                                                help="Change vs the same quarter last year, in units of how much that change usually varies "
                                                     "(previous 8 quarters). Against the company's own history, not analyst estimates."),
    "sue_revenue": st.column_config.NumberColumn("revenue surprise (SUE)", format="%+.1f"),
    "react_rel": st.column_config.NumberColumn("reaction vs Nifty %", format="%+.1f", help="First trading day the market could react"),
    "drift_5": st.column_config.NumberColumn("next 5 days vs Nifty %", format="%+.1f"),
    "drift_20": st.column_config.NumberColumn("next 20 days vs Nifty %", format="%+.1f"),
}


@st.cache_data(ttl=300)
def _quarters(mtime):
    p = F.DIR / "quarters.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


def quarters():
    p = F.DIR / "quarters.parquet"
    return _quarters(p.stat().st_mtime if p.exists() else 0)


def _status():
    st_ = F.state()
    r = F.results()
    fl = F.filings()
    bits = []
    if len(r):
        ok = r[r.status == "ok"]
        bits.append(f"{ok.symbol.nunique()} stocks, {len(ok):,} quarterly results read (from {pd.to_datetime(ok.period_end).min():%b %Y})")
        other = r[r.status != "ok"].status.value_counts()
        if len(other):
            bits.append("not usable: " + ", ".join(f"{v} {k}" for k, v in other.items()))
    if st_.get("failed"):
        bits.append(f"{len(st_['failed'])} files failed to download (retried next run)")
    if st_.get("last_update"):
        bits.append(f"last update {str(st_['last_update'])[:16]}")
    return " · ".join(bits) if bits else ("Results listed but not read yet." if len(fl) else "No results downloaded yet.")


def results_tab():
    c = st.columns([1.2, 1.4, 4])
    if c[0].button("Update results", width="stretch", disabled=jobs.busy("fundamentals"), key="rs-upd",
                   help="Reads the last 120 days of results filings from NSE (also part of Fetch data + run all agents)"):
        launch("fundamentals")
    if c[1].button("Download history", width="stretch", disabled=jobs.busy("fundamentals-backfill"), key="rs-bf",
                   help="Results since 2021 for every Nifty 500 stock: a few hours the first time, resumable"):
        launch("fundamentals-backfill")
    c[2].caption(_status() + ". Numbers come from the XBRL files companies file with NSE, with the time each was filed.")
    q = quarters()
    if q.empty:
        st.info("No quarterly results yet. Press **Download history** once (runs in the background, a few hours), then results "
                "update with every Fetch data run.")
        _calendar()
        return
    uni = universe()
    sec = dict(zip(uni.Symbol, uni.Industry))
    q = q.assign(sector=q.symbol.map(sec))
    t1, t2, t3, t4 = st.tabs(["Latest results", "Coming up", "Does surprise move prices?", "Coverage"])
    with t1:
        f = st.columns([1.2, 2.5, 1.5, 1.5])
        days = f[0].selectbox("Filed in the last", [3, 7, 15, 30, 60, 90], index=3, key="rs-days", format_func=lambda d: f"{d} days")
        secs = f[1].multiselect("Sectors", sorted(uni.Industry.dropna().unique()), key="rs-sec")
        sue_min = f[2].number_input("Profit surprise at least", value=None, step=0.5, key="rs-sue", placeholder="any")
        only_sl = f[3].toggle("Only the agents' shortlist", key="rs-sl")
        v = q[pd.to_datetime(q.filed_at) >= pd.Timestamp.now() - pd.Timedelta(days=days)]
        if secs:
            v = v[v.sector.isin(secs)]
        if sue_min is not None:
            v = v[v.sue_profit >= sue_min]
        if only_sl:
            from ui.screener_more import _shortlist_symbols
            v = v[v.symbol.isin(set(_shortlist_symbols()))]
        v = v.sort_values("filed_at", ascending=False)
        st.markdown(f"**{len(v)} results** filed in the last {days} days")
        cols = ["symbol", "quarter", "basis", "filed_at", "revenue_cr", "revenue_yoy", "profit_cr", "profit_yoy", "margin_pct",
                "ebitda_margin", "sue_profit", "sue_revenue", "react_rel", "drift_5", "revised"]
        st.dataframe(v[[c for c in cols if c in v]], hide_index=True, width="stretch", height=min(560, 38 + 35 * max(len(v), 1)),
                     column_config=COLS_FMT)
        st.caption("Blank means not available or not comparable (a different basis a year ago, a loss a year ago for profit growth, "
                   "or fewer than 4 earlier quarters for the surprise). 'revised' = the company filed this quarter again later; "
                   "the first filing is shown because that is what the market reacted to.")
        from ui.screener_more import _open_stock
        _open_stock(list(v.symbol), "rs")
    with t2:
        _calendar()
    with t3:
        _drift(q)
    with t4:
        cov = F.coverage()
        show(cov.head(20), "Nothing read yet.")
        st.caption("Stocks with numbers per quarter. Gaps: the company filed no XBRL, NSE did not list it, or the file has not been read "
                   "yet (Download history continues where it stopped).")


def _calendar():
    cal = F.calendar()
    st.markdown("**Results meetings coming up**")
    if cal.empty:
        st.caption("None known. Dates come from NSE's event calendar and from board-meeting notices in the NSE filings.")
        return
    uni = universe()
    cal = cal.assign(company=cal.symbol.map(dict(zip(uni.Symbol, uni["Company Name"]))), sector=cal.symbol.map(dict(zip(uni.Symbol, uni.Industry))))
    show(cal[["date", "symbol", "company", "sector", "source", "purpose"]], column_config={
        "date": st.column_config.DateColumn(format="ddd DD MMM")})


def _drift(q):
    from research.earnings import drift_study
    d = drift_study(q)
    if d.empty:
        st.info("Needs at least 5 quarters per stock (the surprise uses the same quarter a year earlier plus 4 earlier changes). "
                "Download history first.")
        return
    st.caption("Average move, minus the Nifty, after results with each size of profit surprise. 't' above about 2 means the average is "
               "unlikely to be luck; 'hit %' is how often the move was positive. The latest quarters are kept apart: a pattern that "
               "shows only in the earlier period is not one to rely on.")
    cfg = {"events": st.column_config.NumberColumn(format="%d")}
    for c, lab in (("react_rel", "reaction day"), ("drift_5", "next 5 days"), ("drift_20", "next 20 days")):
        cfg[f"{c}_mean"] = st.column_config.NumberColumn(f"{lab} %", format="%+.2f")
        cfg[f"{c}_t"] = st.column_config.NumberColumn("t", format="%.1f")
        cfg[f"{c}_hit"] = st.column_config.NumberColumn("hit %", format="%.0f")
    st.dataframe(d, hide_index=True, width="stretch", column_config=cfg)
    st.caption("Rows with fewer than 20 events are marked enough = false: too few to read anything into.")


def stock_section(sym):
    """Stock lookup: the stock's recent quarters."""
    q = quarters()
    st.subheader("Quarterly results")
    if q.empty or sym not in set(q.symbol):
        cal = F.calendar()
        nxt = cal[cal.symbol == sym]
        st.caption("No results read for this stock yet (Screener → Results → Download history)."
                   + (f" Next results meeting: {pd.Timestamp(nxt.date.iat[0]):%a %d %b %Y}." if len(nxt) else ""))
        return
    g = q[q.symbol == sym].sort_values("period_end", ascending=False).head(12)
    cal = F.calendar()
    nxt = cal[cal.symbol == sym]
    if len(nxt):
        st.caption(f"Next results meeting: {pd.Timestamp(nxt.date.iat[0]):%a %d %b %Y} ({nxt.source.iat[0]})")
    cols = ["quarter", "basis", "filed_at", "revenue_cr", "revenue_yoy", "profit_cr", "profit_yoy", "margin_pct", "ebitda_margin", "eps",
            "sue_profit", "react_rel", "drift_20", "revised"]
    st.dataframe(g[[c for c in cols if c in g]], hide_index=True, width="stretch", column_config=COLS_FMT)
    h = g.sort_values("period_end")
    fig = go.Figure()
    fig.add_bar(x=h.quarter, y=h.revenue_cr, name="revenue ₹ cr", marker_color="rgba(79,70,229,0.35)")
    fig.add_scatter(x=h.quarter, y=h.profit_cr, name="profit ₹ cr", yaxis="y2", mode="lines+markers", line=dict(color=ACCENT))
    fig.update_layout(yaxis=dict(title="revenue ₹ cr"), yaxis2=dict(title="profit ₹ cr", overlaying="y", side="right", showgrid=False))
    st.plotly_chart(fig_base(fig, 300, legend=True), width="stretch")
