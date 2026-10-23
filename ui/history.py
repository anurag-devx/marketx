"""Data health → History downloads: every historical data set, how complete it is, and a button for each
(plus one button that runs them all in order). All downloads resume where they stopped."""
import json
from ui.common import *  # noqa: F401,F403
from core import news as N, announcements as A, articles as AR


def _json(p):
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _row(label, frac, detail, job, button, help_, busy, args=None):
    c = st.columns([2.2, 3, 1.6])
    c[0].markdown(f"**{label}**")
    with c[1]:
        st.progress(min(max(frac, 0.0), 1.0) if frac is not None else 0.0)
        st.caption(detail)
    if c[2].button(button, key=f"hist-{job}", width="stretch", disabled=busy, help=help_):
        launch(job, args)


def panel(_main_busy=None):
    busy = jobs.busy("history-all")          # history downloads have their own lane: they never block the daily run
    st.subheader("History downloads")
    st.caption("Everything the news study, Catalyst, Search and the reports learn from. Each download is polite to the source, "
               "resumes where it stopped, and can run while you use the dashboard.")
    if st.button("⬇ Download all history (runs for hours; safe to leave running)", type="primary", disabled=busy, key="hist-all",
                 help="In order: NSE filings since 2016 → news archives since 2016 → article text (300) → search index. "
                      "A step that fails is reported and the rest continue. Press again later to continue where it stopped."):
        launch("history-all")
    uni = universe()
    n_uni = max(len(uni), 1)
    years = pd.Timestamp.now().year - 2016 + 1

    bf = _json(A.BACKFILL_STATE)
    done = len(bf.get("done", []))
    an = A.load()
    _row("NSE filings (2016 →)", done / (n_uni * years), f"{done:,} of {n_uni * years:,} stock-years · {len(an):,} filings stored"
         + (f" · from {an.published.min():%b %Y}" if len(an) else ""), "announcements-backfill", "Download / continue",
         "One request per stock per year, newest first; ~75 min per 3,000 requests", busy)

    nb = _json(N.BACKFILL)
    nw = N.load_store()
    months = pd.period_range("2016-01", pd.Timestamp.now().to_period("M"), freq="M")
    per_month = nw.published.dt.to_period("M").value_counts() if len(nw) else pd.Series(dtype=int)
    covered = int((per_month.reindex(months).fillna(0) >= 100).sum())
    _row("News archives (2016 →)", covered / len(months), f"{len(nw):,} headlines · {covered} of {len(months)} months with 100+ headlines",
         "news-backfill", "Download / continue", f"Publishers' dated sitemaps; {len(nb.get('done', []))} archive files read so far", busy)

    st_ = AR.load_state()
    ok = int((st_.status == "ok").sum()) if len(st_) else 0
    _row("Article text", len(st_) / max(len(nw), 1), f"{len(st_):,} of {len(nw):,} headlines tried · {ok:,} with full text",
         "news-articles", "Read 300 more", "Where the site allows; paywalls never bypassed (~30–60 min per 300)", busy, ["max=300"])

    try:
        from core import search_index as SI
        pr = SI.progress()
        _row("Search index", pr["indexed"] / max(pr["total"], 1), f"{pr['indexed']:,} of {pr['total']:,} headlines + filings"
             + (f" · updated {pr['when'][:16]}" if pr.get("when") else ""), "search-index", "Update index",
             "Makes everything searchable by meaning (needs the document library packages and nomic-embed-text)", busy)
    except Exception:
        st.caption("Search index: install the document library packages first (Settings → AI models).")

    try:
        from core import macro as M
        w = M.load()
        if len(w):
            span = w.groupby("source").date.agg(["min", "max"])
            st.caption("Macro history: " + " · ".join(f"{s} {r['min']:%Y}→{r['max']:%d %b %Y}" for s, r in span.iterrows()))
    except Exception:
        pass
    with st.expander("Job output", expanded=busy):
        job_panel("hist")
