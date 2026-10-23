"""Strategy Lab pages (a separate app from the agents' dashboard: python3 cli.py lab)."""
import json
import numpy as np, pandas as pd, streamlit as st
import plotly.graph_objects as go
from core import config as C
from core.data import has_broad
from lab import engine as E, store as S
from lab.expr import HELP, RuleError

ACCENT, MUTED, GOOD, BAD, REF, WARN = "#4F46E5", "#A8B0BF", "#059669", "#DC2626", "#94A3B8", "#D97706"
NAV = {}
STATE_LABEL = {"up_calm": "Uptrend, calm", "up_volatile": "Uptrend, volatile", "down_calm": "Downtrend, calm",
               "down_volatile": "Downtrend, volatile", "unknown": "Unknown"}
SNIPPETS = [
    ("Price above its 200-day average", "close > sma(200)"),
    ("50-day average above 200-day (uptrend)", "sma(50) > sma(200)"),
    ("New 55-day high (close above the previous 55-day high)", "close > prev(highest(high, 55))"),
    ("New 52-week high", "close >= prev(high52())"),
    ("Within 5% of the 52-week high", "close >= 0.95 * high52()"),
    ("Volume at least 2x normal", "vol_ratio(20) >= 2"),
    ("RSI(14) below 30 (oversold)", "rsi(14) < 30"),
    ("RSI(3) below 15 (sharp dip)", "rsi(3) < 15"),
    ("Close crosses above the 50-day average", "crosses_above(close, sma(50))"),
    ("Top 10% by 6-month return", "rank(ret(126)) >= 90"),
    ("Stronger than the Nifty over 3 months", "rs(63) > 0"),
    ("Top 30% in its sector by 3-month return", "sector_rank(ret(63)) >= 70"),
    ("Gap up of 2%+ at the open", "gap() >= 2"),
    ("Tight range: Bollinger width in its lowest 10% of the year", "bb_width(20, 2) <= 1.1 * lowest(bb_width(20, 2), 250)"),
    ("Pullback: close within 2% of the 20-day average", "abs(close / sma(20) - 1) <= 0.02"),
    ("3 up days in a row", "count(close > prev(close), 3) == 3"),
    ("MACD above its signal line", "macd() > macd_signal()"),
    ("Daily range (ATR) under 3% of price", "atr_pct(14) < 3"),
]
MARKET_SNIPPETS = [("Nifty above its 200-day average", "nifty > sma(nifty, 200)"), ("VIX below 20", "vix < 20"),
                   ("Market state is up and calm", 'market_state == "up_calm"'), ("More than 50% of stocks above their 200-day", "breadth > 50"),
                   ("Nifty up over 3 months", "ret(nifty, 63) > 0")]


def pct(v, d=2, sign=True):
    return "—" if v is None or (isinstance(v, float) and not np.isfinite(v)) else (f"{v:+.{d}f}%" if sign else f"{v:.{d}f}%")


def num(v, d=1):
    return "—" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:.{d}f}"


def fig_base(fig, h=340, legend=False):
    fig.update_layout(height=h, margin=dict(l=8, r=8, t=24, b=8), showlegend=legend,
                      legend=dict(orientation="h", y=1.1, x=0), hoverlabel=dict(namelength=-1))
    return fig


def current_sid():
    sid = st.session_state.get("sid")
    if sid and S.get(sid):
        return sid
    lst = S.list_strategies()
    if lst:
        st.session_state["sid"] = lst[0]["id"]
        return lst[0]["id"]
    return None


def open_strategy(sid, page="build"):
    st.session_state["sid"] = sid
    st.session_state.pop("rid", None)
    st.session_state.pop("loaded", None)
    st.switch_page(NAV[page])


# =============================================================== home
def page_home():
    st.title("Strategy Lab")
    st.caption("Your own workspace for testing strategy ideas. Its strategies and results are kept separately and "
               "the daily agents never read them, until you send a strategy that passes to the Strategy agent and approve it there.")
    with st.container(border=True):
        st.markdown("**New strategy**")
        c = st.columns([3, 2, 1])
        tpl = c[0].selectbox("Start from", list(E.TEMPLATES), key="new-tpl")
        name = c[1].text_input("Name", key="new-name", placeholder=E.TEMPLATES[tpl].get("name", "New strategy"))
        c[2].write("")
        if c[2].button("Create", type="primary", width="stretch"):
            d = E.normalize(E.TEMPLATES[tpl])
            d["name"] = name or d["name"]
            sid = S.save(d, None, note=f"created from template: {tpl}")
            open_strategy(sid)
    lst = S.list_strategies()
    if not lst:
        st.info("No strategies yet. Create one above: start from a template and change the rules, or write your own.")
        return
    allruns = S.runs()
    st.subheader("Your strategies")
    for s in lst:
        r = allruns[allruns.strategy_id == s["id"]] if len(allruns) else allruns
        last = r.iloc[0] if len(r) else None
        with st.container(border=True):
            c = st.columns([4, 2, 2, 2, 1.2, 1.2])
            kind = "Rotation (rank and hold)" if s["mode"] == "rotation" else "Signal (buy on a rule, exit on rules)"
            c[0].markdown(f"**{s['name']}**  \n<span style='color:{REF}'>{kind} · updated {s['updated'][:16]}</span>", unsafe_allow_html=True)
            if last is None:
                c[1].markdown("Not tested yet")
            else:
                ok = bool(last.passes_so_far)
                c[1].markdown(("✅ Passes design + validation" if ok else "❌ " + (str(last.first_fail).split(":")[0] if last.first_fail else "Fails")))
            c[2].markdown(f"Design edge **{pct(last.design_excess) if last is not None else '—'}**  \n"
                          f"<span style='color:{REF}'>t {num(last.design_t) if last is not None else '—'}</span>", unsafe_allow_html=True)
            c[3].markdown(f"Versions tested **{S.versions_tested(s['id'])}**  \n<span style='color:{REF}'>{len(r)} run(s)</span>", unsafe_allow_html=True)
            if c[4].button("Open", key=f"open-{s['id']}", width="stretch"):
                open_strategy(s["id"])
            if c[5].button("Results", key=f"res-{s['id']}", width="stretch", disabled=last is None):
                st.session_state["sid"] = s["id"]
                st.session_state["rid"] = last.run_id
                st.switch_page(NAV["results"])
    arch = S.list_strategies(archived=True)
    if arch:
        with st.expander(f"Archived ({len(arch)})"):
            for s in arch:
                c = st.columns([5, 1])
                c[0].write(s["name"])
                if c[1].button("Restore", key=f"unarch-{s['id']}"):
                    S.archive(s["id"], False); st.rerun()


# =============================================================== build
def _load_into_state(sid):
    if st.session_state.get("loaded") == sid:
        return
    d = S.get(sid)["definition"]
    st.session_state["loaded"] = sid
    k = lambda x: f"f-{x}"
    u, ex, pf, ro = d["universe"], d["exits"], d["portfolio"], d["rotation"]
    vals = {
        "name": d["name"], "notes": d["notes"], "mode": d["mode"], "src": u["source"], "pit_n": int(u["pit_n"]),
        "minpx": float(u["min_price"]), "minto": float(u["min_turnover_cr"]), "sectors": u["sectors"], "flag": bool(u["exclude_flagged"]),
        "mf": d["market_filter"], "entry": d["entry"], "eprice": d["entry_price"], "rank": d["rank_by"], "rdesc": bool(d["rank_desc"]),
        "stop": ex["stop"], "stopv": str(ex["stop_value"]), "tgt": ex["target"], "tgtv": str(ex["target_value"]),
        "trail": float(ex["trail_pct"]), "maxd": int(ex["max_days"]), "exitw": ex["exit_when"],
        "maxpos": int(pf["max_positions"]), "cost": float(pf["cost_pct"]), "cap": float(pf["capital"]),
        "rebal": ro["rebalance"], "topn": int(ro["top_n"]), "hold": int(ro["hold"]), "score": ro["score"], "sdesc": bool(ro["score_desc"]),
        "rfilter": ro["filter"], "regime": ro["regime"],
    }
    for name, v in vals.items():
        st.session_state[k(name)] = v


def _from_state():
    g = lambda x: st.session_state.get(f"f-{x}")
    fnum = lambda x: float(x) if str(x).replace(".", "", 1).replace("-", "", 1).isdigit() else x
    return E.normalize(dict(
        name=g("name"), notes=g("notes"), mode=g("mode") or "signal",
        universe=dict(source=g("src"), pit_n=g("pit_n"), min_price=g("minpx"), min_turnover_cr=g("minto"), sectors=g("sectors") or [],
                      exclude_flagged=g("flag")),
        market_filter=g("mf") or "", entry=g("entry") or "", entry_price=g("eprice"), rank_by=g("rank") or "", rank_desc=g("rdesc"),
        exits=dict(stop=g("stop"), stop_value=fnum(g("stopv") or 0), target=g("tgt"), target_value=fnum(g("tgtv") or 0),
                   trail_pct=g("trail"), max_days=g("maxd"), exit_when=g("exitw") or ""),
        portfolio=dict(max_positions=g("maxpos"), cost_pct=g("cost"), capital=g("cap")),
        rotation=dict(rebalance=g("rebal"), top_n=g("topn"), hold=g("hold"), score=g("score") or "", score_desc=g("sdesc"),
                      filter=g("rfilter") or "", regime=g("regime") or "")))


def _snippet_adder(target_key, options, label):
    c = st.columns([5, 1])
    pick = c[0].selectbox(label, [s[0] for s in options], key=f"snip-{target_key}", label_visibility="collapsed")
    if c[1].button("Add", key=f"add-{target_key}", width="stretch"):
        code = dict(options)[pick]
        cur = (st.session_state.get(f"f-{target_key}") or "").strip()
        st.session_state[f"f-{target_key}"] = f"{cur} and {code}" if cur else code
        st.rerun()


@st.cache_data(ttl=3600)
def _sectors():
    p = C.DATA / "universe.csv"
    return sorted(pd.read_csv(p).Industry.dropna().unique()) if p.exists() else []


def page_build():
    sid = current_sid()
    if not sid:
        st.info("Create a strategy on the home page first.")
        return
    _load_into_state(sid)
    rec = S.get(sid)
    st.title(rec["name"])
    st.caption(f"Saved {rec['updated'][:16]} · version {rec['version']} · {S.versions_tested(sid)} version(s) tested")
    k = lambda x: f"f-{x}"

    st.subheader("1 · Idea")
    c = st.columns([2, 3])
    c[0].text_input("Name", key=k("name"))
    c[1].text_area("Your strategy in words (for you; the rules below are what gets tested)", key=k("notes"), height=80)
    st.segmented_control("Type", ["signal", "rotation"], key=k("mode"),
                         format_func=lambda m: "Signal: buy on a rule, exit on rules" if m == "signal" else "Rotation: rank stocks, hold the top N, rebalance")
    mode = st.session_state[k("mode")] or "signal"

    st.subheader("2 · Which stocks")
    c = st.columns([3, 1.3, 1.3, 1.3])
    c[0].radio("Universe", ["nifty500", "pit"], key=k("src"), horizontal=True,
               format_func=lambda s: "Today's Nifty 500" if s == "nifty500" else "Point-in-time (removes survivorship bias)",
               help="Today's Nifty 500 only has stocks that survived into today's index, which flatters results. "
                    "Point-in-time takes the top N stocks by traded value as they were at each date.")
    c[1].number_input("Min price ₹", 0.0, 10000.0, step=10.0, key=k("minpx"))
    c[2].number_input("Min avg traded value ₹ cr/day (20 days)", 0.0, 2000.0, step=5.0, key=k("minto"))
    c[3].number_input("Point-in-time size", 50, 1000, step=50, key=k("pit_n"), disabled=st.session_state[k("src")] != "pit")
    if st.session_state[k("src")] == "pit" and not has_broad():
        st.warning("For a real point-in-time test, download all NSE stocks once (main dashboard → Data health → Download all NSE stocks, "
                   "3–4 hours). Until then this ranks only today's Nifty 500 stocks, which removes only part of the bias.")
    c = st.columns([3, 2])
    c[0].multiselect("Only these sectors (empty = all)", _sectors(), key=k("sectors"))
    c[1].toggle("Skip stocks with data errors", key=k("flag"), help="Stocks with an unreviewed data error (see Data health) are left out.")

    if mode == "signal":
        st.subheader("3 · Entry")
        st.text_area("Buy when (at the close)", key=k("entry"), height=90,
                     placeholder="close > sma(200) and rsi(3) < 15")
        _snippet_adder("entry", SNIPPETS, "Add a condition")
        c = st.columns([2, 2, 1])
        c[0].text_input("Market filter (optional): only take signals when", key=k("mf"), placeholder="nifty > sma(nifty, 200)")
        c[1].text_input("Rank same-day signals by (optional)", key=k("rank"), placeholder="vol_ratio(20)",
                        help="When more signals than free slots, the portfolio takes the best by this value.")
        c[2].toggle("Highest first", key=k("rdesc"))
        _snippet_adder("mf", MARKET_SNIPPETS, "Add a market condition")
        st.radio("Buy at", ["next_open", "close"], key=k("eprice"), horizontal=True,
                 format_func=lambda s: "Next day's open (realistic)" if s == "next_open" else "Same day's close (needs you to act at 3:25 pm)")

        st.subheader("4 · Exit")
        c = st.columns(4)
        c[0].selectbox("Stop loss", ["atr", "pct", "expr", "none"], key=k("stop"),
                       format_func={"atr": "ATR multiple", "pct": "% below entry", "expr": "Formula (price)", "none": "No stop"}.get)
        c[1].text_input({"atr": "× ATR(14)", "pct": "% below entry", "expr": "Stop price formula", "none": "—"}[st.session_state[k("stop")]],
                        key=k("stopv"), disabled=st.session_state[k("stop")] == "none",
                        help="Formula example: lowest(low, 10) = the 10-day low at the signal")
        c[2].selectbox("Target", ["r", "atr", "pct", "expr", "none"], key=k("tgt"),
                       format_func={"r": "R multiple (× stop distance)", "atr": "ATR multiple", "pct": "% above entry", "expr": "Formula (price)", "none": "No target"}.get)
        c[3].text_input({"r": "× risk", "atr": "× ATR(14)", "pct": "% above entry", "expr": "Target price formula", "none": "—"}[st.session_state[k("tgt")]],
                        key=k("tgtv"), disabled=st.session_state[k("tgt")] == "none")
        c = st.columns([1, 1, 3])
        c[0].number_input("Trailing stop % (0 = off)", 0.0, 50.0, step=0.5, key=k("trail"), help="Below the highest close since entry")
        c[1].number_input("Max days held", 1, 500, key=k("maxd"))
        c[2].text_input("Also sell when (at the close, optional)", key=k("exitw"), placeholder="close < sma(10)")

        st.subheader("5 · Money")
        c = st.columns(3)
        c[0].number_input("Max positions at once", 1, 100, key=k("maxpos"))
        c[1].number_input("Round-trip cost %", 0.0, 3.0, step=0.05, key=k("cost"), help="STT, stamp duty, brokerage, slippage. 0.3% is realistic for delivery trades.")
        c[2].number_input("Starting capital ₹", 10000.0, 1e9, step=100000.0, key=k("cap"))
    else:
        st.subheader("3 · Ranking")
        c = st.columns([3, 1])
        c[0].text_input("Score (rank stocks by this at each rebalance)", key=k("score"), placeholder="prev(ret(126), 21)",
                        help="prev(ret(126), 21) = 6-month return, skipping the latest month")
        c[1].toggle("Highest first", key=k("sdesc"))
        c = st.columns(3)
        c[0].selectbox("Rebalance", ["M", "W"], key=k("rebal"), format_func={"M": "Monthly", "W": "Weekly"}.get)
        c[1].number_input("Hold the top", 1, 200, key=k("topn"))
        c[2].number_input("Hold each batch for (rebalances)", 1, 12, key=k("hold"),
                          help="More than 1 = overlapping batches, which lowers turnover (momentum uses 3)")
        st.text_input("Only consider stocks where (optional)", key=k("rfilter"), placeholder="close > sma(200)")
        st.text_input("Go to cash unless (market rule, optional)", key=k("regime"), placeholder="nifty > sma(nifty, 200)")
        _snippet_adder("regime", MARKET_SNIPPETS, "Add a market condition")
        st.subheader("4 · Money")
        c = st.columns(2)
        c[0].number_input("Round-trip cost %", 0.0, 3.0, step=0.05, key=k("cost"))
        c[1].number_input("Starting capital ₹", 10000.0, 1e9, step=100000.0, key=k("cap"))

    d = _from_state()
    errs = E.validate(d)
    hard = [e for e in errs if "broad download" not in e]
    for e in errs:
        (st.error if e in hard else st.info)(e)
    st.divider()
    c = st.columns([1.3, 1.3, 1.6, 1, 1])
    changed = E.version_hash(d) != rec["version"] or d["name"] != rec["name"] or d["notes"] != rec["definition"].get("notes")
    if c[0].button("Save", width="stretch", disabled=bool(hard) or not changed):
        S.save(d, sid); st.toast("Saved"); st.session_state.pop("loaded", None); st.rerun()
    check = c[1].button("Quick check", width="stretch", disabled=bool(hard), help="Counts signals per year and lists today's, in seconds")
    run = c[2].button("▶ Save and run full test", type="primary", width="stretch", disabled=bool(hard))
    if c[3].button("Duplicate", width="stretch"):
        open_strategy(S.duplicate(sid))
    if c[4].button("Archive", width="stretch"):
        S.archive(sid); st.session_state.pop("sid", None); st.switch_page(NAV["home"])
    if check:
        with st.spinner("Checking (the first time loads all prices, about 20 seconds)..."):
            try:
                qc = E.signal_counts(d)
                sig, last = E.latest_signals(d)
            except RuleError as e:
                st.error(str(e)); return
        a, b = st.columns([3, 2])
        with a:
            if mode == "signal":
                st.markdown(f"**{qc['total']:,} signals since 2015 in {qc['stocks']} stocks** (before the one-trade-per-stock rule)")
                fig = go.Figure(go.Bar(x=qc["per_year"].index.astype(str), y=qc["per_year"].values, marker_color=ACCENT))
            else:
                st.markdown(f"**About {int(qc['per_year'].median()):,} stocks eligible per day** ({qc['stocks']} different stocks)")
                fig = go.Figure(go.Bar(x=qc["per_year"].index.astype(str), y=qc["per_year"].values, marker_color=ACCENT))
            st.plotly_chart(fig_base(fig, 240), width="stretch")
            st.caption(qc["info"]["universe"])
        with b:
            st.markdown(f"**{'Signals' if mode == 'signal' else 'Top picks'} on {pd.Timestamp(last).date()}**")
            st.dataframe(sig.head(40), hide_index=True, width="stretch", height=260)
    if run:
        S.save(d, sid, note="saved for a test")
        bar = st.progress(0.0, "Starting")
        try:
            res = E.run(d, progress=lambda f, m: bar.progress(min(max(f, 0.0), 1.0), m))
        except RuleError as e:
            st.error(str(e)); return
        if mode == "signal" and (res["trades"] is None or res["trades"].empty):
            st.warning("No trades: the entry rule never fired (or every stop was above the entry). Try Quick check.")
            return
        rid = S.save_run(sid, res)
        st.session_state["rid"] = rid
        st.session_state.pop("loaded", None)
        st.switch_page(NAV["results"])


# =============================================================== results
def page_results():
    sid = current_sid()
    if not sid:
        st.info("Create and test a strategy first.")
        return
    rec = S.get(sid)
    rr = S.runs(sid)
    if not len(rr):
        st.title(rec["name"])
        st.info("Not tested yet. Open the strategy and press **Save and run full test**.")
        if st.button("Open strategy"):
            open_strategy(sid)
        return
    rid = st.session_state.get("rid")
    if rid not in set(rr.run_id):
        rid = rr.run_id.iat[0]
    c = st.columns([3, 1])
    labels = {r.run_id: f"{pd.Timestamp(r.created):%d %b %H:%M} · version {r.version}" + (" · holdout seen" if r.holdout_revealed else "")
              for r in rr.itertuples()}
    rid = c[0].selectbox("Test run", list(labels), index=list(labels).index(rid), format_func=labels.get)
    st.session_state["rid"] = rid
    if c[1].button("Edit rules", width="stretch"):
        open_strategy(sid)
    run = S.load_run(rid)
    if run is None:
        st.error("Run files missing."); return
    report(run, sid, rec)


def _verdict_block(run, sid, revealed):
    bp = dict(run["by_period"])
    if not revealed:
        bp.pop("holdout", None)
    n = S.versions_tested(sid)
    v = E.verdict(bp, run["mode"], n)
    if v["passes"]:
        st.success("**Passes every test, including the holdout.** It beat random picks after costs in all three periods. "
                   "You can send it to the Strategy agent for paper trading.")
    elif v["passes_so_far"] and not revealed:
        st.success("**Passes design and validation.** The last test is the holdout (2024 onwards), kept hidden so it can't "
                   "influence your choices. Reveal it once you're done changing the rules.")
    elif v["passes_so_far"]:
        st.warning("**Passes design and validation, fails the holdout.**")
    else:
        st.error(f"**Does not pass.** First failure: {v['first_fail']}.")
    if n > 1:
        msg = (f"You've tested **{n} versions** of this strategy. With that many tries, a design t-stat of "
               f"{v['search_t']:.1f} is needed to rule out luck from the search itself"
               + (" — this one clears it." if v["search_ok"] else " — this one doesn't, so treat a pass with caution."))
        st.caption(msg)
    return v


def _period_cards(run, revealed):
    bp = run["by_period"]
    cols = st.columns(3)
    for col, (p, lab) in zip(cols, E.PERIOD_LABEL.items()):
        s = bp.get(p, {})
        with col.container(border=True):
            st.markdown(f"**{lab}**" + (" · searched" if p == "design" else " · checked" if p == "validation" else ""))
            if p == "holdout" and not revealed:
                st.markdown("🔒 Hidden")
                st.caption("Reveal it when you've stopped changing the rules. Every look after a change weakens it as a test.")
                continue
            if not s or not (s.get("trades") or s.get("periods")):
                st.caption("No data"); continue
            if run["mode"] == "signal":
                st.metric("Net per trade", pct(s.get("avg_net_pct")), f"{pct(s.get('excess_pct'))} vs random", delta_color="normal")
                st.caption(f"{s['trades']:,} trades · win {s['win_pct']:.0f}% · t {num(s.get('t_excess'))} · random picks {pct(s.get('control_avg_pct'))}")
            else:
                st.metric("Return a year", pct(s.get("cagr_pct"), 1), f"{pct(s.get('excess_pct'))} a {'week' if run.get('freq') == 'W' else 'month'} vs market")
                st.caption(f"Control {pct(s.get('control_cagr_pct'), 1)} a year · t {num(s.get('t_excess'))} · max drop {pct(s.get('max_dd_pct'), 0, False)}")


def report(run, sid, rec):
    d = run["definition"]
    revealed = bool(run.get("holdout_revealed"))
    st.title(d["name"])
    st.caption(f"Tested {run['created'][:16]} on data to {run['data_last']} · {run['info'].get('universe', '')} · took {run['seconds']}s")
    if d.get("notes"):
        st.markdown(f"> {d['notes']}")
    v = _verdict_block(run, sid, revealed)
    _period_cards(run, revealed)
    c = st.columns([2, 2, 3])
    if not revealed:
        if c[0].button("Reveal holdout", help="Shows 2024 onwards. Recorded: the Lab counts how often you look."):
            S.reveal_holdout(run["run_id"]); st.rerun()
    else:
        n, nv = S.reveals(sid)
        c[0].caption(f"Holdout seen {n} time(s), {nv} version(s)")
    if v["passes"]:
        if c[1].button("Send to Strategy agent", type="primary"):
            live_id, pid = S.send_to_agents(sid, run["run_id"])
            st.success(f"Filed as {live_id}" + (f" (proposal {pid})" if pid else " (a proposal already exists)") +
                       ". Approve it in the dashboard: Settings → Approvals.")
    with st.expander("Every test"):
        st.dataframe(pd.DataFrame(v["checks"]).assign(period=lambda x: x.period.map(E.PERIOD_LABEL),
                                                      ok=lambda x: np.where(x.ok, "✅", "❌")),
                     hide_index=True, width="stretch")
    if run["mode"] == "signal":
        signal_report(run, revealed)
    else:
        rotation_report(run, revealed)
    st.subheader("Rules tested")
    st.markdown(describe(d))
    with st.expander("As saved (JSON)"):
        st.json(d)
    html = export_html(run, revealed)
    st.download_button("Download this report (HTML)", html, file_name=f"{sid}_{run['run_id']}.html", mime="text/html")


def _hide(df, col, revealed):
    if df is None or revealed or col not in df:
        return df
    return df[pd.to_datetime(df[col]) < pd.Timestamp(C.HOLDOUT_FROM)]


def _equity_fig(e, extra=None, revealed=True, log=False, h=360):
    fig = go.Figure()
    if extra is not None:
        for name, x, y, colr in extra:
            fig.add_trace(go.Scatter(x=x, y=y, name=name, line=dict(color=colr, width=1.6)))
    fig.add_trace(go.Scatter(x=e.date, y=e.equity / 1e5, name="Strategy", line=dict(color=ACCENT, width=2.4)))
    for x0, lab in (("2021-01-01", "validation"), ("2024-01-01", "holdout")):
        if x0 == "2024-01-01" and not revealed:
            continue
        fig.add_vline(x=x0, line_dash="dot", line_color=REF)
        fig.add_annotation(x=x0, y=1.05, yref="paper", text=lab, showarrow=False, font=dict(color=REF))
    fig.update_yaxes(title="₹ lakh", type="log" if log else "linear")
    return fig_base(fig, h, legend=True)


def _nifty_line(start_value, dates):
    _, idx, _, market = E.base_data()
    m = market.dropna(subset=["nifty"])
    m = m[(m.date >= dates.min()) & (m.date <= dates.max())]
    if m.empty:
        return None
    return ("Nifty 50", m.date, start_value / 1e5 * m.nifty / m.nifty.iat[0], MUTED)


def signal_report(run, revealed):
    t = _hide(run["trades"], "signal_date", revealed)
    e = _hide(run["equity"], "date", revealed)
    if t is None or t.empty:
        st.info("No trades."); return
    t = t[t.period != "warmup"].copy()
    t["net_pct"], t["control_pct"], t["excess_pct"] = 100 * t.net, 100 * t.control, 100 * t.excess
    st.subheader("Growth of the money")
    pstats = run["portfolio"]
    if e is not None and len(e):
        e = e.copy(); e["date"] = pd.to_datetime(e.date)
        nl = _nifty_line(e.equity.iat[0], e.date)
        st.plotly_chart(_equity_fig(e, [nl] if nl else None, revealed), width="stretch")
        cols = st.columns(4)
        key = "all" if revealed else "design"
        ps = pstats.get(key, {})
        tk = t[t.taken]
        cols[0].metric("Return a year", pct(ps.get("cagr_pct"), 1), f"Nifty {pct(ps.get('nifty_cagr_pct'), 1)}", delta_color="off",
                       help="Whole test" if revealed else "Design period only (holdout hidden)")
        cols[1].metric("Worst drop from a peak", pct(ps.get("max_dd_pct"), 0, False))
        cols[2].metric("Sharpe", num(ps.get("sharpe"), 2))
        cols[3].metric("Signals taken", f"{len(tk):,} of {len(t):,}", help=f"At most {run['definition']['portfolio']['max_positions']} positions at once")
        st.caption(f"₹{run['definition']['portfolio']['capital'] / 1e5:.0f} lakh, at most {run['definition']['portfolio']['max_positions']} positions, "
                   "each sized equity ÷ max positions at entry, marked to market daily, after costs.")

    a, b = st.columns(2)
    with a:
        st.markdown("**Edge over random picks, by year**")
        st.caption("Average net return per trade minus random stocks bought the same day with the same exits.")
        y = t.groupby("year").agg(ex=("excess_pct", "mean"), net=("net_pct", "mean"), n=("net", "size")).reset_index()
        fig = go.Figure(go.Bar(x=y.year.astype(str), y=y.ex, marker_color=[GOOD if v > 0 else BAD for v in y.ex],
                               customdata=np.stack([y.net, y.n], axis=1),
                               hovertemplate="%{x}: %{y:+.2f}% vs random<br>net %{customdata[0]:+.2f}% · %{customdata[1]} trades<extra></extra>"))
        fig.add_hline(y=0, line_color=REF)
        fig.update_yaxes(ticksuffix="%")
        st.plotly_chart(fig_base(fig, 300), width="stretch")
    with b:
        st.markdown("**By market state**")
        st.caption("Net per trade, random picks, and the difference.")
        s = t.groupby("state").agg(trades=("net", "size"), net=("net_pct", "mean"), random=("control_pct", "mean"), edge=("excess_pct", "mean"),
                                   win=("net", lambda r: 100 * (r > 0).mean())).reindex(list(STATE_LABEL)).dropna(how="all").reset_index()
        s["state"] = s.state.map(STATE_LABEL)
        st.dataframe(s.round(2), hide_index=True, width="stretch", column_config={
            "net": st.column_config.NumberColumn("Net / trade %", format="%+.2f"), "random": st.column_config.NumberColumn("Random %", format="%+.2f"),
            "edge": st.column_config.NumberColumn("Edge %", format="%+.2f"), "win": st.column_config.NumberColumn("Win %", format="%.0f"),
            "trades": st.column_config.NumberColumn("Trades", format="%d")})
    a, b = st.columns(2)
    with a:
        st.markdown("**How trades ended**")
        r = t.groupby("reason").agg(n=("net", "size"), avg=("net_pct", "mean")).reset_index()
        r["share"] = 100 * r.n / r.n.sum()
        colors = {"stop": BAD, "target": GOOD, "exit rule": ACCENT, "time": MUTED}
        fig = go.Figure(go.Bar(x=r.share, y=r.reason, orientation="h", marker_color=[colors.get(x, MUTED) for x in r.reason],
                               text=[f"{s:.0f}% · avg {v:+.1f}%" for s, v in zip(r.share, r.avg)], textposition="auto",
                               hovertemplate="%{y}: %{x:.1f}% of trades<extra></extra>"))
        fig.update_xaxes(ticksuffix="%")
        st.plotly_chart(fig_base(fig, 260), width="stretch")
    with b:
        st.markdown("**Distribution of net returns per trade**")
        x = t.net_pct.clip(-25, 25)
        fig = go.Figure(go.Histogram(x=x, nbinsx=60, marker_color=ACCENT, opacity=0.85))
        fig.add_vline(x=0, line_color=REF)
        fig.add_vline(x=t.net_pct.mean(), line_dash="dash", line_color=GOOD if t.net_pct.mean() > 0 else BAD,
                      annotation_text=f"average {t.net_pct.mean():+.2f}%")
        fig.update_xaxes(ticksuffix="%")
        st.plotly_chart(fig_base(fig, 260), width="stretch")
    st.markdown("**By sector** (sectors with 30+ trades)")
    sc = t.groupby("sector").agg(trades=("net", "size"), net=("net_pct", "mean"), edge=("excess_pct", "mean")).reset_index()
    sc = sc[sc.trades >= 30].sort_values("edge")
    if len(sc):
        fig = go.Figure(go.Bar(x=sc.edge, y=sc.sector, orientation="h", marker_color=[GOOD if v > 0 else BAD for v in sc.edge],
                               customdata=np.stack([sc.net, sc.trades], axis=1),
                               hovertemplate="%{y}: %{x:+.2f}% vs random<br>net %{customdata[0]:+.2f}% · %{customdata[1]} trades<extra></extra>"))
        fig.update_xaxes(ticksuffix="%")
        st.plotly_chart(fig_base(fig, 60 + 22 * len(sc)), width="stretch")

    st.subheader("Trades")
    c = st.columns([2, 2, 2])
    per = c[0].multiselect("Period", sorted(t.period.unique()), default=sorted(t.period.unique()), format_func=E.PERIOD_LABEL.get)
    only = c[1].toggle("Only trades the portfolio took", value=False)
    sym = c[2].text_input("Stock (optional)").strip().upper()
    v = t[t.period.isin(per)]
    if only:
        v = v[v.taken]
    if sym:
        v = v[v.symbol == sym]
    show = v[["signal_date", "symbol", "sector", "entry", "exit", "reason", "days", "net_pct", "control_pct", "taken"]].sort_values("signal_date", ascending=False)
    ev = st.dataframe(show, hide_index=True, width="stretch", height=320, on_select="rerun", selection_mode="single-row",
                      column_config={"signal_date": st.column_config.DateColumn("Signal"), "net_pct": st.column_config.NumberColumn("Net %", format="%+.2f"),
                                     "control_pct": st.column_config.NumberColumn("Random %", format="%+.2f"),
                                     "entry": st.column_config.NumberColumn(format="%.2f"), "exit": st.column_config.NumberColumn(format="%.2f")})
    st.download_button("Download trades (CSV)", show.to_csv(index=False), file_name="trades.csv", mime="text/csv")
    rows = ev.selection.rows if ev and hasattr(ev, "selection") else []
    if rows:
        trade_chart(show.iloc[rows[0]], run["trades"])
    else:
        st.caption("Select a trade to see it on the chart.")


def trade_chart(tr, all_trades):
    px, _, _, _ = E.base_data()
    g = px[px.symbol == tr.symbol].sort_values("date")
    t0, t1 = pd.Timestamp(tr.signal_date), pd.Timestamp(all_trades.loc[(all_trades.symbol == tr.symbol) & (all_trades.signal_date == tr.signal_date), "exit_date"].iat[0])
    v = g[(g.date >= t0 - pd.Timedelta(days=120)) & (g.date <= t1 + pd.Timedelta(days=40))]
    full = all_trades[(all_trades.symbol == tr.symbol) & (all_trades.signal_date == tr.signal_date)].iloc[0]
    fig = go.Figure(go.Candlestick(x=v.date, open=v.open, high=v.high, low=v.low, close=v.close, increasing_line_color=GOOD,
                                   decreasing_line_color=BAD, showlegend=False))
    fig.add_trace(go.Scatter(x=[full.entry_date], y=[full.entry], mode="markers", marker=dict(symbol="triangle-up", size=13, color=ACCENT), name="buy"))
    fig.add_trace(go.Scatter(x=[full.exit_date], y=[full.exit], mode="markers", marker=dict(symbol="triangle-down", size=13, color=BAD if full.net < 0 else GOOD), name=f"sell ({full.reason})"))
    if pd.notna(full.stop):
        fig.add_shape(type="line", x0=full.entry_date, x1=full.exit_date, y0=full.stop, y1=full.stop, line=dict(color=BAD, dash="dot"))
    if pd.notna(full.target):
        fig.add_shape(type="line", x0=full.entry_date, x1=full.exit_date, y0=full.target, y1=full.target, line=dict(color=GOOD, dash="dot"))
    fig.update_layout(xaxis_rangeslider_visible=False)
    st.markdown(f"**{tr.symbol}**: bought {pd.Timestamp(full.entry_date).date()} at {full.entry:.2f}, sold {pd.Timestamp(full.exit_date).date()} "
                f"at {full.exit:.2f} ({full.reason}), {100 * full.net:+.2f}% after costs")
    st.plotly_chart(fig_base(fig, 380, legend=True), width="stretch")


def rotation_report(run, revealed):
    r = _hide(run["periods"], "period_start", revealed)
    e = _hide(run["equity"], "date", revealed)
    if r is None or r.empty:
        st.info("No results."); return
    r = r[r.period != "warmup"].copy()
    unit = "week" if run.get("freq") == "W" else "month"
    st.subheader("Growth of the money")
    if e is not None and len(e):
        e = e.copy(); e["date"] = pd.to_datetime(e.date)
        nl = _nifty_line(e.equity.iat[0], e.date)
        extra = [("Equal-weight control (all eligible stocks)", e.date, e.control_equity / 1e5, WARN)] + ([nl] if nl else [])
        st.plotly_chart(_equity_fig(e, extra, revealed, log=True), width="stretch")
        ps = run["portfolio"].get("all" if revealed else "design", {})
        c = st.columns(4)
        c[0].metric("Return a year", pct(ps.get("cagr_pct"), 1), f"Nifty {pct(ps.get('nifty_cagr_pct'), 1)}", delta_color="off")
        c[1].metric("Worst drop from a peak", pct(ps.get("max_dd_pct"), 0, False))
        c[2].metric("Sharpe", num(ps.get("sharpe"), 2))
        c[3].metric(f"{unit.capitalize()}s beating the control", f"{100 * (r.excess > 0).mean():.0f}%")
        st.caption("Log scale. Monthly (or weekly) returns, open to open, after costs on the names replaced.")
    a, b = st.columns(2)
    with a:
        st.markdown(f"**Edge over the control, by year** (average per {unit})")
        y = r.groupby("year").agg(ex=("excess", "mean"), net=("net", "mean")).reset_index()
        fig = go.Figure(go.Bar(x=y.year.astype(str), y=100 * y.ex, marker_color=[GOOD if v > 0 else BAD for v in y.ex],
                               customdata=100 * y.net, hovertemplate="%{x}: %{y:+.2f}% vs control<br>net %{customdata:+.2f}%<extra></extra>"))
        fig.add_hline(y=0, line_color=REF)
        fig.update_yaxes(ticksuffix="%")
        st.plotly_chart(fig_base(fig, 300), width="stretch")
    with b:
        st.markdown(f"**Every {unit}: strategy vs control**")
        fig = go.Figure(go.Bar(x=r.period_start, y=100 * r.excess, marker_color=[GOOD if v > 0 else BAD for v in r.excess],
                               hovertemplate="%{x|%b %Y}: %{y:+.2f}%<extra></extra>"))
        fig.update_yaxes(ticksuffix="%")
        st.plotly_chart(fig_base(fig, 300), width="stretch")
    st.subheader("What it would buy at the next rebalance")
    nx = run.get("next")
    if nx is not None and len(nx):
        st.dataframe(nx, hide_index=True, width="stretch", height=300)
        st.caption(f"Ranked on data to {run['data_last']}; final at the period end.")
    pk = _hide(run.get("picks"), "period_start", revealed)
    if pk is not None and len(pk):
        with st.expander("Every pick, every period"):
            st.dataframe(pk.sort_values(["period_start", "rank"], ascending=[False, True]), hide_index=True, width="stretch", height=360)
            st.download_button("Download picks (CSV)", pk.to_csv(index=False), file_name="picks.csv", mime="text/csv")


# =============================================================== compare
def page_compare():
    st.title("Compare test runs")
    rr = S.runs()
    if not len(rr):
        st.info("No test runs yet."); return
    lab = {r.run_id: f"{r.name} · {pd.Timestamp(r.created):%d %b %H:%M} · v{r.version}" for r in rr.itertuples()}
    pick = st.multiselect("Runs", list(lab), default=list(lab)[: min(3, len(lab))], format_func=lab.get)
    if not pick:
        return
    rows, fig = [], go.Figure()
    for i, rid in enumerate(pick):
        run = S.load_run(rid)
        if run is None:
            continue
        rev = bool(run.get("holdout_revealed"))
        row = dict(run=lab[rid], type=run["mode"])
        for p in ("design", "validation", "holdout"):
            s = run["by_period"].get(p, {})
            if p == "holdout" and not rev:
                row[f"{p} edge %"] = "🔒"; row[f"{p} t"] = "🔒"; continue
            row[f"{p} edge %"] = round(s.get("excess_pct") or np.nan, 2)
            row[f"{p} t"] = round(s.get("t_excess") or np.nan, 1)
        ps = run["portfolio"].get("all" if rev else "design", {})
        row["return a year %"] = round(ps.get("cagr_pct") or np.nan, 1)
        row["worst drop %"] = round(ps.get("max_dd_pct") or np.nan, 0)
        rows.append(row)
        e = _hide(run.get("equity"), "date", rev)
        if e is not None and len(e):
            fig.add_trace(go.Scatter(x=pd.to_datetime(e.date), y=e.equity / e.equity.iat[0], name=lab[rid][:40]))
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    fig.update_yaxes(type="log", title="growth of 1")
    st.plotly_chart(fig_base(fig, 380, legend=True), width="stretch")
    st.caption("Holdout results show only for runs where you revealed them.")


# =============================================================== rules reference
def page_rules():
    st.title("Rule language")
    st.markdown("Write rules as formulas. Each is worked out for every stock on every day, using data up to that day's close only. "
                "Signals fire at the close; trades start at the next open unless you choose the same close.")
    st.markdown("**Examples**")
    st.code('close > sma(200) and rsi(3) < 15\n'
            'close > prev(highest(high, 55)) and vol_ratio(20) >= 2\n'
            'rank(ret(126)) >= 90 and rs(63) > 0\n'
            'crosses_above(sma(20), sma(50)) and sector == "Information Technology"\n'
            'nifty > sma(nifty, 200) and vix < 18          (market filter)', language=None)
    for group, items in HELP.items():
        st.markdown(f"**{group}**")
        st.dataframe(pd.DataFrame(items, columns=["write", "means"]), hide_index=True, width="stretch")
    st.markdown("**How the test works**")
    st.markdown(
        "- **Periods:** design 2016–20 (change rules freely), validation 2021–23 (a check), holdout 2024 onwards (hidden until you reveal it).\n"
        "- **Random-entry control:** for each trade, random stocks from the same universe are bought the same day with the same exits. "
        "The edge is your return minus theirs. This separates a good rule from a rising market.\n"
        "- **t-stat:** edge ÷ its standard error, on weekly groups of trades. Design needs 2.0+, validation 1.65+.\n"
        "- **Versions tested:** every distinct set of rules you run counts. The more you try, the higher the bar to rule out luck.\n"
        "- **One trade per stock at a time.** Every signal counts in the edge test; the money simulation takes at most your max positions.\n"
        "- **Costs:** the round-trip cost is taken off every trade (0.3% default).\n"
        "- **Stops:** if a stock opens below the stop, the trade exits at the open. If stop and target are both touched on one day, the stop counts.")


def describe(d):
    u = d["universe"]
    uni = ("today's Nifty 500" if u["source"] == "nifty500" else f"point-in-time top {u['pit_n']} by traded value") + \
          f", price ₹{u['min_price']:.0f}+, turnover ₹{u['min_turnover_cr']:.0f} cr+/day" + (f", sectors: {', '.join(u['sectors'])}" if u["sectors"] else "")
    cost = f"{float(d['portfolio']['cost_pct']):.2f}% round-trip cost"
    if d["mode"] == "rotation":
        r = d["rotation"]
        return "\n".join([f"- **Stocks:** {uni}" + (f"; only where `{r['filter']}`" if r["filter"] else ""),
                          f"- **Each {'month' if r['rebalance'] == 'M' else 'week'}-end:** rank by `{r['score']}` ({'highest' if r['score_desc'] else 'lowest'} first), "
                          f"buy the top {r['top_n']} at the next open, hold each batch {r['hold']} rebalance(s)",
                          f"- **Cash when:** not `{r['regime']}`" if r["regime"] else "- **Always invested**", f"- **Costs:** {cost}"])
    ex = d["exits"]
    stop = {"atr": f"{ex['stop_value']}× ATR(14) below entry", "pct": f"{ex['stop_value']}% below entry", "expr": f"at `{ex['stop_value']}`", "none": "none"}[ex["stop"]]
    tgt = {"r": f"{ex['target_value']}× the risk above entry", "atr": f"{ex['target_value']}× ATR(14) above entry", "pct": f"{ex['target_value']}% above entry",
           "expr": f"at `{ex['target_value']}`", "none": "none"}[ex["target"]]
    lines = [f"- **Stocks:** {uni}", f"- **Buy when:** `{d['entry']}`" + (f", only if `{d['market_filter']}`" if d["market_filter"] else ""),
             f"- **Buy at:** {'the next open' if d['entry_price'] == 'next_open' else 'the same close'}",
             f"- **Stop:** {stop} · **Target:** {tgt}" + (f" · **Trailing stop:** {ex['trail_pct']}%" if float(ex["trail_pct"] or 0) else ""),
             f"- **Also sell when:** `{ex['exit_when']}`" if ex["exit_when"] else "", f"- **Time limit:** {ex['max_days']} days",
             f"- **Portfolio:** at most {d['portfolio']['max_positions']} positions" + (f", ranked by `{d['rank_by']}`" if d["rank_by"] else ""),
             f"- **Costs:** {cost}"]
    return "\n".join(l for l in lines if l)


# =============================================================== export
def export_html(run, revealed):
    d = run["definition"]
    bp = run["by_period"]
    rows = []
    for p, lab in E.PERIOD_LABEL.items():
        s = bp.get(p, {})
        if p == "holdout" and not revealed:
            rows.append(f"<tr><td>{lab}</td><td colspan=5>hidden</td></tr>")
            continue
        if run["mode"] == "signal":
            rows.append(f"<tr><td>{lab}</td><td>{s.get('trades', 0):,}</td><td>{pct(s.get('avg_net_pct'))}</td><td>{pct(s.get('control_avg_pct'))}</td>"
                        f"<td>{pct(s.get('excess_pct'))}</td><td>{num(s.get('t_excess'))}</td></tr>")
        else:
            rows.append(f"<tr><td>{lab}</td><td>{s.get('periods', 0)}</td><td>{pct(s.get('cagr_pct'), 1)}</td><td>{pct(s.get('control_cagr_pct'), 1)}</td>"
                        f"<td>{pct(s.get('excess_pct'))}</td><td>{num(s.get('t_excess'))}</td></tr>")
    head = ("<th>Period</th><th>Trades</th><th>Net / trade</th><th>Random picks</th><th>Edge</th><th>t</th>" if run["mode"] == "signal"
            else "<th>Period</th><th>Rebalances</th><th>Return a year</th><th>Control</th><th>Edge / period</th><th>t</th>")
    figs = []
    e = _hide(run.get("equity"), "date", revealed)
    if e is not None and len(e):
        e = e.copy(); e["date"] = pd.to_datetime(e.date)
        figs.append(_equity_fig(e, None, revealed, log=run["mode"] == "rotation").to_html(full_html=False, include_plotlyjs="cdn"))
    esc = lambda x: str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{esc(d['name'])}</title>
<style>body{{font-family:-apple-system,Segoe UI,sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#222}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left}}pre{{background:#f5f5f5;padding:10px;overflow:auto}}</style></head><body>
<h1>{esc(d['name'])}</h1><p>{esc(d.get('notes', ''))}</p>
<p>Tested {run['created'][:16]} on data to {run['data_last']}. {esc(run['info'].get('universe', ''))}</p>
<h2>Results</h2><table><tr>{head}</tr>{''.join(rows)}</table>
<h2>Growth</h2>{''.join(figs)}
<h2>Rules</h2><pre>{esc(json.dumps(d, indent=2))}</pre></body></html>"""
