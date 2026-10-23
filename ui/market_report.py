"""Market report page: the daily research note (what went up / down, sectors, new players, filings and headlines),
written by a local model from computed facts."""
from ui.common import *  # noqa: F401,F403
from core import jobs
from research import report_writer as W

FMT_PCT = lambda lab: st.column_config.NumberColumn(lab, format="%+.2f%%")


def _settings_box():
    from core import llm
    ok, msg = llm.status("report")
    with st.expander(("🟢 " if ok else "🟡 ") + f"Report writer: {msg}", expanded=not ok):
        st.markdown("All numbers come from your data; the model only writes the sentences and every number it writes is checked. "
                    "Without a model, a plain template writes the report. Choose the model in **Settings → AI models** "
                    "(local by default: nothing leaves this Mac).")
        if not ok:
            st.markdown("**Local setup:** install Ollama (ollama.com), open it, then in Terminal `ollama pull <model>` "
                        "(e.g. qwen3.5:9b or gemma4:e4b — exact names on ollama.com/library).")


def page():
    st.title("Market report")
    busy = jobs.current() is not None
    days = W.saved_reports()
    c = st.columns([2, 1.4, 1.4, 1.2])
    pick = c[0].selectbox("Report", days, format_func=lambda d: pd.Timestamp(d).strftime("%a %d %b %Y"), index=0 if days else None,
                          placeholder="No reports yet")
    feat, _ = feats()
    last = pd.Timestamp(feat.date.max()).date() if feat is not None else pd.Timestamp.now().date()
    when = c[1].date_input("Write for", value=last, max_value=last, help="Any past trading day with prices.")
    use_model = c[2].toggle("Use local model", value=True, help="Off: template text only (instant).")
    if c[3].button("Write report", type="primary", width="stretch", disabled=busy):
        launch("market-report", [str(when)] + ([] if use_model else ["--no-llm"]))
    st.caption("Written automatically after each **Fetch data + run all agents**. With the model, a report takes about 2–4 minutes on a 16 GB Mac.")
    _settings_box()
    with st.expander("Job output", expanded=busy):
        job_panel("mr")
    if not pick:
        st.info("No report yet. Press **Write report**.")
        return
    rec = W.load_report(pick)
    f, rep = rec["facts"], rec["report"]
    show_report(f, rep)
    md = (W.OUT / f"{pick}.md")
    if md.exists():
        st.download_button("Download report (.md)", md.read_text(), file_name=f"market_report_{pick}.md", mime="text/markdown")


def _text(t):
    st.markdown(t.replace("$", "\\$"))


def show_report(f, rep):
    m = f["market"]
    st.header(f"{pd.Timestamp(f['day']):%A %d %B %Y}")
    k = st.columns(6)
    k[0].metric("Nifty 50", f"{m['nifty_close']:,.0f}", f"{m['nifty_change_pct']:+.2f}%")
    k[1].metric("India VIX", m["vix"], f"{m['vix_change']:+.2f}" if m["vix_change"] is not None else None, delta_color="inverse")
    k[2].metric("Advances / declines", f"{m['advances']} / {m['declines']}")
    k[3].metric("Up >2% / down >2%", f"{m['up_2pct']} / {m['down_2pct']}")
    k[4].metric("52-week highs / lows", f"{m['new_52w_highs']} / {m['new_52w_lows']}")
    k[5].metric("Above 200-day avg", f"{m['above_200dma_pct']:.0f}%")
    with st.container(border=True):
        st.markdown("##### Summary")
        _text(rep["summary"])
    sec = dict(rep["sections"])

    st.subheader("Market overview")
    _text(sec["Market overview"])
    h = pd.DataFrame(m["last_5_days"])
    fig = go.Figure()
    fig.add_bar(x=h.date, y=h.advances, name="Advances", marker_color=GOOD)
    fig.add_bar(x=h.date, y=-h.declines, name="Declines", marker_color=BAD)
    fig.add_scatter(x=h.date, y=h.nifty_pct, name="Nifty %", yaxis="y2", line=dict(color=ACCENT))
    fig.update_layout(barmode="relative", yaxis2=dict(overlaying="y", side="right", showgrid=False, title="Nifty %"), xaxis=dict(type="category"))
    st.plotly_chart(fig_base(fig, 240, legend=True), width="stretch")

    cols = ["symbol", "company", "sector", "move_pct", "vs_nifty_pct", "volume_x_normal", "traded_cr", "month_pct", "news_text"]
    cfg = {"move_pct": FMT_PCT("Move"), "vs_nifty_pct": FMT_PCT("vs Nifty"), "month_pct": FMT_PCT("1 month"),
           "volume_x_normal": st.column_config.NumberColumn("Volume × normal", format="%.1f×"),
           "traded_cr": st.column_config.NumberColumn("Traded ₹ cr", format="%.0f"),
           "news_text": st.column_config.TextColumn("Filing / headline that day", width="large")}
    for title, key in (("What went up", "up"), ("What went down", "down")):
        st.subheader(title)
        _text(sec[title])
        d = pd.DataFrame(f[key])
        if len(d):
            d["news_text"] = d.news.map(lambda ns: " | ".join(f"{n['kind']}: {n['text']}" for n in ns))
            st.dataframe(d[cols], hide_index=True, width="stretch", column_config=cfg, height=40 + 35 * len(d))

    st.subheader("Sectors")
    _text(sec["Sectors"])
    s = pd.DataFrame(f["sectors"]["table"])
    if len(s):
        a, b = st.columns([3, 2])
        s2 = s.sort_values("median_pct")
        fig = go.Figure()
        fig.add_bar(y=s2.sector, x=s2.median_pct, orientation="h", name="Median stock", marker_color=[GOOD if v > 0 else BAD for v in s2.median_pct])
        fig.add_scatter(y=s2.sector, x=s2.weighted_pct, mode="markers", name="Weighted by traded value", marker=dict(color=REF, symbol="diamond", size=8))
        fig.update_xaxes(title_text="% today")
        a.plotly_chart(fig_base(fig, 40 + 24 * len(s2), legend=True), width="stretch")
        with b:
            st.markdown("**Most affected** (largest move either way)")
            for x in f["sectors"]["most_affected"]:
                r = s[s.sector == x].iloc[0]
                st.markdown(f"- {x}: {r.median_pct:+.2f}% median, {r.share_up_pct:.0f}% of stocks up")
            st.markdown("**Least affected** (closest to flat)")
            for x in f["sectors"]["least_affected"]:
                r = s[s.sector == x].iloc[0]
                st.markdown(f"- {x}: {r.median_pct:+.2f}% median")
        st.dataframe(s, hide_index=True, width="stretch", column_config={
            "median_pct": FMT_PCT("Median"), "weighted_pct": FMT_PCT("Weighted"), "share_up_pct": st.column_config.NumberColumn("% up", format="%.0f"),
            "median_5d_pct": FMT_PCT("5 days"), "median_20d_pct": FMT_PCT("20 days"),
            "traded_vs_20d": st.column_config.NumberColumn("Traded × 20-day avg", format="%.2f×")})

    st.subheader("New players")
    _text(sec["New players"])
    n = f["new"]
    t = st.tabs([f"New leaders ({len(n['leaders'])})", f"Nifty 500 changes ({len(n['index_changes'])})", f"Unusual volume ({len(n['unusual_volume'])})",
                 f"Recent listings ({len(n['recent_listings'])})", f"IPO headlines ({len(n['ipo_headlines'])})"])
    for tab, key in zip(t, ["leaders", "index_changes", "unusual_volume", "recent_listings", "ipo_headlines"]):
        with tab:
            d = pd.DataFrame(n[key])
            if "news" in d:
                d["news"] = d.news.map(lambda ns: " | ".join(x["text"] for x in ns))
            show(d, "None.", column_config={"move_pct": FMT_PCT("Move"), "since_first_close_pct": FMT_PCT("Since first close"),
                                            "volume_x_normal": st.column_config.NumberColumn("Volume × normal", format="%.1f×")})
    if not n["recent_listings"]:
        st.caption("Recent listings come from stocks outside the Nifty 500 too, once Data health → Download all NSE stocks has run.")

    if "Macro and geopolitics" in sec:
        st.subheader("Macro and geopolitics")
        _text(sec["Macro and geopolitics"])
        mc = f.get("macro") or {}
        if mc.get("indicators"):
            show(pd.DataFrame(mc["indicators"])[["label", "date", "value", "unit", "chg_1d", "chg_5d", "chg_20d", "z_1y"]],
                 column_config={"z_1y": st.column_config.NumberColumn("vs past year (sd)", format="%+.1f")})
        if mc.get("upcoming"):
            st.markdown("**Coming up:** " + " · ".join(f"{e['date']} {e['event']}" for e in mc["upcoming"]))

    st.subheader("Filings and headlines")
    _text(sec["Filings and headlines"])
    fl = f["filings"]
    a, b = st.columns(2)
    with a:
        st.markdown(f"**Notable NSE filings** ({fl['count']} filings that day)")
        show(pd.DataFrame(fl["notable"]), "None for liquid stocks.", column_config={"move_pct": FMT_PCT("Stock move")})
    with b:
        st.markdown("**Market headlines**")
        for x in f["headlines"][:10]:
            st.markdown(f"- {x['text']} · {x['source']}" + (f" ({x['sources']} sources)" if x["sources"] > 1 else ""))
        if not f["headlines"]:
            st.caption("No stored market headlines for this day.")

    w = rep["writer"]
    tmpl = [k for k, v in w["used"].items() if v != "model"]
    st.caption(f"Written {'by ' + w['model'] if w['model'] else 'with the template'} in {w['seconds']} s. "
               + (f"Template text used for: {', '.join(tmpl)}. " if w["model"] and tmpl else "")
               + (f"Numbers the model got wrong (those sections were replaced): {w['flags']}. " if w["flags"] else "")
               + "Figures come from stored prices, NSE filings and headlines; 'alongside' news is not a claim of cause. Not investment advice.")
