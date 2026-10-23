"""Market Debate Lab: bull vs bear research agents, statistical validation, probability engine and judge."""
import html
from ui.common import *  # noqa: F401,F403
from core import jobs, llm
from debate import store, evaluate as EV, stats as S, signals as SG, probability as PR
from debate.engine import TARGETS

STATUS_ICON = {S.SUPPORTED: "🟢", S.PARTIAL: "🟡", S.NOT_SUPPORTED: "⚪", S.CONTRA: "🔴", S.INSUFF: "⚫", S.QUAL: "📝", S.INVALID: "⛔"}
VERDICT_ICON = {"Bullish bias": "🟢", "Bearish bias": "🔴", "Neutral or mixed": "⚖️", "Insufficient evidence": "❔", "Forecast unavailable": "🚫"}
LINK = st.column_config.LinkColumn("source", display_text="open")


def page():
    st.title("Market Debate Lab")
    st.caption("A bullish and a bearish research agent argue from the same evidence; a statistical agent tests their claims on history; "
               "a probability engine and a judge give a forecast — or say the evidence is not good enough. Equities only, NSE, daily data. "
               "Research, not trade advice.")
    t = st.tabs(["▶ Run analysis", "📋 Results", "🌙 Nightly batch", "🧪 Model check", "ℹ️ How it works"])
    with t[0]:
        run_tab()
    with t[1]:
        results_tab()
    with t[2]:
        batch_tab()
    with t[3]:
        model_tab()
    with t[4]:
        how_tab()


# ---------------- run ----------------
def _availability(last):
    from core import news as N, announcements as A
    rows = [("Daily prices (Angel One, NSE)", f"✅ to {last}" if last else "❌ none")]
    try:
        n = N.load_store()
        linked = n[n.symbols.fillna("") != ""]
        rows.append(("News linked to stocks", f"✅ {len(linked):,} headlines since {linked.published.min():%d %b %Y}" if len(linked) else "⚠️ none"))
    except Exception:
        rows.append(("News linked to stocks", "⚠️ not loaded"))
    try:
        a = A.load()
        rows.append(("NSE filings", f"✅ {len(a):,} since {a.published.min():%d %b %Y}" if len(a) else "⚠️ none (Data health → Download all history)"))
    except Exception:
        rows.append(("NSE filings", "⚠️ not loaded"))
    rows.append(("Macro sensitivities", "✅" if (C.RESULTS / "macro" / "sens_3y.csv").exists() else "⚠️ run Macro & geopolitics → study"))
    for k, (a, b) in __import__("debate.evidence", fromlist=["x"]).UNAVAILABLE.items():
        rows.append((a, "❌ " + b))
    return pd.DataFrame(rows, columns=["data", "status"])


def run_tab():
    uni = universe()
    last = None
    px = prices()
    if px is not None:
        last = pd.Timestamp(px.date.max()).date()
    if uni is None or uni.empty or last is None:
        st.warning("No prices yet. Run **Fetch data + run all agents** first.")
        return
    names = dict(zip(uni.Symbol, uni["Company Name"]))
    ok, msg = llm.status("debate")
    with st.container(border=True, key="card-dl-run"):
        st.markdown("##### New analysis")
        c = st.columns([2.6, 1.1, 2.0])
        sym = c[0].selectbox("Stock (NSE)", sorted(names), format_func=lambda s: f"{s} — {names[s][:38]}", key="dl-sym",
                             index=sorted(names).index(st.session_state.get("stock")) if st.session_state.get("stock") in names else 0)
        hz = c[1].selectbox("Horizon", ["5", "1", "20", "60", "Custom"], key="dl-h", format_func=lambda x: x if x == "Custom" else f"{x} sessions")
        h = int(c[1].number_input("Sessions", 1, 120, 10, key="dl-hc")) if hz == "Custom" else int(hz)
        order = ["rel", "abs", "three"]
        tgt = c[2].selectbox("Question", order, key="dl-t",
                             format_func=lambda k: TARGETS[k] + (" (main)" if k == "rel" else " (second)" if k == "abs" else " (experimental)"))
        band, day, rounds, use_model = None, last, 1, True
        with st.expander("Advanced: date, debate rounds, model"):
            a = st.columns(4)
            day = a[0].date_input("Analysis date", value=last, max_value=last, key="dl-d",
                                  help="Evidence is cut off at 15:30 IST on this day. A past date is a replay, scored separately from live forecasts.")
            rounds = int(a[1].number_input("Cross-examination rounds", 1, 3, 1, key="dl-r", help="Stops early if a round raises nothing new."))
            if tgt == "three":
                band = float(a[2].number_input("Neutral band ± %", 0.1, 10.0, 0.5, 0.1, key="dl-b",
                                               help="Neutral = return between -band and +band: ±0.5% suits 1–5 sessions, wider for 20+."))
            use_model = a[3].toggle("Use language model", value=True, key="dl-m", help="Off: template theses (instant). Statistics and probabilities are the same either way.")
            st.caption("Exchange: NSE (Angel One prices; BSE not covered). Intraday horizons aren't available: only daily bars are stored.")
        if (h, tgt) not in ((5, "rel"), (5, "abs")):
            st.caption("⚗️ Experimental question — scored and reported apart from the two fixed questions (5 sessions vs Nifty, 5 sessions absolute).")
        if day < last:
            st.caption(f"↺ Replay for {day}: evidence stops at that day's close; labelled retrospective.")
        c = st.columns([1, 3])
        lab = jobs.current("debate")
        go = c[0].button("Run analysis", type="primary", disabled=lab is not None, key="dl-go", width="stretch")
        c[1].markdown(f'<div class="mx-tile-sub" style="margin-top:8px"><span class="mx-dot {"ok" if ok else "warn"}" style="display:inline-block;margin-right:6px"></span>'
                      f'Agents\' model: {html.escape(msg)} · about 3–8 min with the local model</div>', unsafe_allow_html=True)
    if go:
        args = [f"sym={sym.replace('&', '__amp__')}", f"h={h}", f"target={tgt}", f"date={day}", f"rounds={rounds}"]
        if band is not None:
            args.append(f"band={band}")
        if not use_model:
            args.append("--no-llm")
        try:
            jobs.start("debate", args)
            st.rerun()
        except RuntimeError as e:
            st.error(str(e))
    _progress()
    with st.expander("Data available to the agents"):
        show(_availability(last))


def _progress():
    def body():
        a = jobs.activity("debate")
        if a:
            p = store.read_progress() if a["job"] == "debate" else None
            if p and p.get("step"):
                a = {**a, "label": p["step"] + (f" — {p['note']}" if p.get("note") else "")}
            progress_card(a)
            if p:
                st.progress(min(100, int(p.get("pct", 0))) / 100)
            c = st.columns([1, 4])
            if c[0].button("Stop", key="dl-stop"):
                jobs.stop("debate"); st.rerun()
            with c[1].expander("Live output"):
                st.code(jobs.tail(a["log"], 25) or "(starting)", language=None)
        else:
            hist = [x for x in jobs.history(40) if x["job"] in ("debate", "debate-eval", "debate-batch", "debate-replay", "stock-lists")]
            if hist:
                hj = hist[0]
                (st.success if hj["result"] == "ok" else st.warning)(f"Last lab job: {RUN_LABEL.get(hj['job'], hj['job'])} — {hj['result']} at {hj['finished']}.")
                if hj["result"] != "ok":
                    st.code(jobs.tail(hj["path"], 25), language=None)
    if jobs.current("debate") and hasattr(st, "fragment"):
        st.fragment(run_every=2)(body)()
    else:
        body()


# ---------------- results ----------------
@st.cache_data(ttl=30)
def _index(mt):
    return store.index()


@st.cache_data(ttl=300)
def _record(aid, mt):
    return store.load(aid)


def _pick(key="dl-pick"):
    mt = store.INDEX.stat().st_mtime if store.INDEX.exists() else 0
    idx = _index(mt)
    if idx.empty:
        st.info("No analyses yet. Use **Run analysis**.")
        return None
    idx = idx.sort_values("created", ascending=False)
    lab = {r.analysis_id: f"{r.symbol} · {r.horizon} sessions · {TARGETS.get(r.target, r.target)} · as of {r.as_of} · {r.verdict}"
           + (" · replay" if r.mode == "retrospective" else "") for r in idx.itertuples()}
    opts = list(lab)
    other = st.session_state.get("dl-pick")
    default = opts.index(other) if key != "dl-pick" and other in opts else 0
    aid = st.selectbox("Analysis", opts, format_func=lambda a: lab[a], key=key, index=default)
    p = store.path(aid)
    return _record(aid, p.stat().st_mtime if p.exists() else 0)


def _pct(x, d=1):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{100 * x:.{d}f}%"


def results_tab():
    rec = _pick()
    if not rec:
        return
    summary(rec)
    sub = st.tabs(["⚖️ Verdict", "🐂 Bull vs 🐻 Bear", "🗣 Debate timeline", "📊 Statistical validation", "🗂 Evidence", "⬇️ Record"])
    with sub[0]:
        verdict_view(rec)
    with sub[1]:
        sides_view(rec)
    with sub[2]:
        timeline_view(rec)
    with sub[3]:
        stats_view(rec)
    with sub[4]:
        evidence_view(rec)
    with sub[5]:
        st.caption(f"Record {rec['analysis_id']} · SHA-256 {rec.get('record_sha256', '')[:16]}… · versions {rec['versions']}")
        st.caption(rec["reproducibility"])
        st.download_button("Download the full record (.json)", json.dumps(rec, indent=1, default=str), file_name=f"{rec['analysis_id']}.json",
                           mime="application/json")


def summary(rec):
    s, v, info = rec["spec"], rec["verdict"], rec["info"]
    px = prices()
    cur = px[px.symbol == s["symbol"]].sort_values("date").iloc[-1] if px is not None and (px.symbol == s["symbol"]).any() else None
    st.subheader(f"{VERDICT_ICON.get(v['verdict'], '')} {s['symbol']} — {info.get('company', '')}: {v['verdict']}")
    st.markdown(f"**Forecast question.** {s['definition']}")
    k = st.columns(6)
    k[0].metric("Reference close", f"₹{s['reference_price']:,.2f}", help=f"Close at {s['reference_time']} — the price the forecast is measured from.")
    if cur is not None:
        k[1].metric("Latest close", f"₹{cur.close:,.2f}", f"{pd.Timestamp(cur.date):%d %b %Y}", delta_color="off",
                    help="Today's data, for comparison. The forecast uses the reference close, not this.")
    p, b = v["probabilities"], v["base_rates"]
    k[2].metric("P(outcome positive)" if s["target"] != "three" else "P(up)", _pct(p.get("up")), f"base rate {_pct(b.get('up'))}", delta_color="off")
    if s["target"] == "three":
        k[3].metric("P(down)", _pct(p.get("down")), f"neutral {_pct(p.get('neutral'))}", delta_color="off")
    else:
        k[3].metric("P(not positive)", _pct(p.get("down")))
    er = v.get("expected_return")
    k[4].metric("Expected return", f"{er['mean']:+.2f}%" if er and er.get("usable") else "n/a",
                help=(er or {}).get("note") or "The model has no out-of-sample history for this.")
    k[5].metric("Horizon (sessions)", s["h"])
    st.caption(f"Probability: **{v['probability_label']}** · Calibration: {v['calibration']} · Data quality: "
               f"{next((e['quality'] for e in rec['evidence'] if e['id'] == 'PX-REF'), 'n/a')} · Created {rec['created'][:16].replace('T', ' ')} IST · "
               f"Mode: {rec.get('mode', 'live')} · Cutoff {s['cutoff'][:16].replace('T', ' ')} IST")
    if rec.get("mode") == "retrospective":
        st.caption("↺ " + rec.get("mode_note", ""))


def verdict_view(rec):
    v = rec["verdict"]
    st.markdown(f"### {VERDICT_ICON.get(v['verdict'], '')} {v['verdict']}")
    for r in v["reasons"]:
        st.markdown(f"- {r}")
    st.markdown(v.get("explanation", "").replace("$", "\\$"))
    st.caption(f"Explanation written by: {v.get('explanation_writer')}. {v['note']}")
    c = st.columns([1.2, 1])
    with c[0]:
        p = v["probabilities"]
        lab = {"up": "positive / up", "neutral": "neutral", "down": "not positive / down"}
        df = pd.DataFrame([(lab[k], 100 * p[k], 100 * (v["base_rates"].get(k) or 0)) for k in ("up", "neutral", "down") if k in p],
                          columns=["outcome", "model", "base rate"])
        fig = go.Figure([go.Bar(name="model (" + v["probability_label"].split(":")[0] + ")", x=df.outcome, y=df.model, marker_color=ACCENT,
                                text=[f"{x:.1f}%" for x in df.model], textposition="outside"),
                         go.Bar(name="historical base rate", x=df.outcome, y=df["base rate"], marker_color=MUTED,
                                text=[f"{x:.1f}%" for x in df["base rate"]], textposition="outside")])
        fig.update_yaxes(title="%", range=[0, 100])
        st.plotly_chart(fig_base(fig, 300, legend=True), width="stretch")
        st.caption("The probabilities sum to 100%. A probability is a forecast, not a guarantee.")
    with c[1]:
        st.markdown("**What moved the model (largest contributions)**")
        dr = pd.DataFrame(v.get("top_drivers") or [])
        if len(dr):
            dr["direction"] = np.where(dr.contribution > 0, "towards positive", "towards negative")
            show(dr[["label", "z", "contribution", "direction"]].round(3))
        d = v.get("downside")
        if d:
            st.markdown(f"**Downside (this stock's own history):** 1 in 10 past {rec['spec']['h']}-session windows lost more than "
                        f"{-d['p10']:.1f}%; 1 in 20 more than {-d['p05']:.1f}% ({d['n']} windows, last 3 years).")
        er = v.get("expected_return")
        if er:
            st.markdown(f"**Similar past predictions:** average {er['mean']:+.2f}% (95% {er['ci_lo']:+.2f} to {er['ci_hi']:+.2f}), median "
                        f"{er['median']:+.2f}%, 10th–90th percentile {er['p10']:+.1f}% to {er['p90']:+.1f}% ({er['n']:,} predictions).")
            st.caption(er["note"])
    c = st.columns(3)
    lv = v.get("invalidation_levels") or {}
    with c[0]:
        st.markdown("**Invalidation levels (code)**")
        if lv.get("bull_invalid_below"):
            st.markdown(f"- Bull case weakens below ₹{lv['bull_invalid_below']:,.2f} (lower of 50-day SMA and 55-day low)")
        if lv.get("bear_invalid_above"):
            st.markdown(f"- Bear case weakens above ₹{lv['bear_invalid_above']:,.2f} (higher of 50-day SMA and 55-day high)")
    with c[1]:
        st.markdown("**Catalysts named by the agents**")
        for side, xs in v.get("catalysts", {}).items():
            for x in xs[:4]:
                st.markdown(f"- ({side}) {x}")
    with c[2]:
        st.markdown("**Update this assessment when**")
        for x in v.get("update_when", []):
            st.markdown(f"- {x}")
    g = v.get("evidence_groups") or {}
    if g:
        st.markdown("**Supported evidence by group** (each group counts once: correlated indicators are not double-counted)")
        show(pd.DataFrame([dict(group=k, bull=x["bull"], bear=x["bear"]) for k, x in g.items()]))


def _claims_df(rec, side):
    rows = []
    led = {r["claim_id"]: r for r in rec["ledger"]}
    rev = rec["revisions"][side]
    for c in rec["reports"][side]["claims"]:
        r = led.get(c["id"], {})
        rows.append(dict(id=c["id"], claim=c["text"], evidence=", ".join(c["evidence_ids"]) or "—", status=f"{STATUS_ICON.get(r.get('status'), '')} {r.get('status', '')}",
                         after_revision="withdrawn" if c["id"] in rev["conceded"] else "kept", flags="; ".join(c["flags"])))
    return pd.DataFrame(rows)


def sides_view(rec):
    c = st.columns(2)
    for col, side, title in ((c[0], "bull", "🐂 Bullish case"), (c[1], "bear", "🐻 Bearish case")):
        r, rv = rec["reports"][side], rec["revisions"][side]
        with col:
            st.markdown(f"### {title}")
            st.caption(f"Written by: {r['writer']}{(' · ' + r['model']) if r.get('model') else ''} · argument confidence: {rv['confidence']} "
                       f"({r['confidence_note']})")
            st.markdown(f"**Revised thesis.** {rv['thesis']}".replace("$", "\\$"))
            with st.expander("Initial thesis and scenarios"):
                st.markdown(r["thesis"].replace("$", "\\$"))
                if r["strong_scenario"]:
                    st.markdown(f"**Strong scenario:** {r['strong_scenario']}")
                if r["moderate_scenario"]:
                    st.markdown(f"**Moderate scenario:** {r['moderate_scenario']}")
                for lab, key in (("Conditions", "conditions"), ("Invalidation", "invalidation"), ("Assumptions", "assumptions")):
                    if r[key]:
                        st.markdown(f"**{lab}:** " + "; ".join(r[key]))
            show(_claims_df(rec, side), "No claims.")
            if r["contradictory"]:
                st.caption("Contradictory evidence it acknowledged: " + ", ".join(r["contradictory"]))
            if r["catalysts"]:
                st.markdown("**Catalysts:** " + "; ".join(r["catalysts"]))
            with st.expander("Missing data"):
                for m in r["missing_data"]:
                    st.markdown(f"- {m}")


def timeline_view(rec):
    st.markdown("**Round 1 — independent theses** (same evidence and cutoff; neither saw the other)")
    for side in ("bull", "bear"):
        r = rec["reports"][side]
        st.markdown(f"- *{r['agent']}*: {r['thesis'][:400]}".replace("$", "\\$"))
    st.markdown("**Round 2 — cross-examination**")
    ob = pd.DataFrame(rec["objections"])
    if len(ob):
        show(ob[["id", "round", "from", "claim_id", "type", "text", "evidence_ids", "by"]].assign(evidence_ids=lambda d: d.evidence_ids.map(lambda x: ", ".join(x))))
    else:
        st.caption("No objections.")
    st.markdown("**Round 3 — statistical challenge**")
    for n in rec["statistics"]["notes"]:
        st.markdown(f"- {n}")
    led = pd.DataFrame(rec["ledger"])
    if len(led):
        counts = led.status.value_counts()
        st.markdown(" · ".join(f"{STATUS_ICON.get(k, '')} {k}: {v}" for k, v in counts.items()))
    st.markdown("**Round 4 — revised theses**")
    for side in ("bull", "bear"):
        rv = rec["revisions"][side]
        st.markdown(f"- *{rv['agent']}* ({rv['writer']}): kept {len(rv['maintained'])}, withdrew {len(rv['conceded'])}. {rv['thesis'][:400]}".replace("$", "\\$"))
        for rp in rv.get("replies", []):
            st.caption(f"  reply to {rp['objection_id']}: {rp['reply']}")
    st.markdown("**Round 5 — evidence ledger** (see Statistical validation)")


def _fmt(x, d=2, plus=True):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else (f"{x:+.{d}f}" if plus else f"{x:.{d}f}")


def stats_view(rec):
    h = rec["spec"]["h"]
    st.caption(f"Effects are in percentage points of {h}-session {'relative ' if rec['spec']['target'] == 'rel' else ''}return. Design 2016–20, "
               "validation 2021–23, recent 2024+ (reported, never used to decide). 'Not supported' means the data could not show the effect — not that it is false.")
    rows = []
    for r in rec["ledger"]:
        t = next((x for x in r["tests"] if x["kind"] != "qualitative"), {}) if r["tests"] else {}
        res = {}
        if t.get("kind") == "signal":
            res = rec["statistics"]["signals"].get(t["evidence_id"][3:], {})
        d, v, rc = res.get("design") or {}, res.get("validation") or {}, res.get("recent") or {}
        so = t.get("stock_only") or {}
        rows.append(dict(claim=r["claim_id"], side=r["side"], by=r["source_agent"], status=f"{STATUS_ICON.get(r['status'], '')} {r['status']}",
                         claim_text=r["claim"][:140], test=t.get("kind", "—"), design=_fmt(d.get("effect")), design_ci=f"{_fmt(d.get('ci_lo'))} … {_fmt(d.get('ci_hi'))}" if d else "—",
                         n_design=d.get("eff_n"), q_fdr=_fmt(res.get("q_design"), 3, False), validation=_fmt(v.get("effect")), p_val=_fmt(v.get("p"), 3, False),
                         n_val=v.get("eff_n"), recent=_fmt(rc.get("effect")), hit_signal=_pct(d.get("hit_signal")), hit_others=_pct(d.get("hit_control")),
                         same_regime=_fmt((res.get("same_regime") or {}).get("effect")), stock_only=(f"{_fmt(so.get('effect'))} (n {so.get('n_signal')})" if so else "—"),
                         economic=t.get("economic") or "—", why=r["why"], evidence=", ".join(r["evidence_ids"]), contradicted_by=", ".join(r["contradicting"]),
                         uncertainty=r.get("uncertainty") or ""))
    df = pd.DataFrame(rows)
    show(df, "No claims.")
    sig = pd.DataFrame([dict(id=k, effect=(v.get("validation") or {}).get("effect"), lo=(v.get("validation") or {}).get("ci_lo"),
                             hi=(v.get("validation") or {}).get("ci_hi"), side=SG.BY_ID[k]["side"]) for k, v in rec["statistics"]["signals"].items()
                        if k in SG.BY_ID])
    sig = sig.dropna(subset=["effect"])
    if len(sig):
        st.markdown("**Every pre-registered signal on the liquid universe — validation period (2021–23) effect with 95% interval**")
        sig = sig.sort_values("effect")
        fig = go.Figure(go.Scatter(x=sig.effect, y=sig.id, mode="markers", marker=dict(color=np.where(sig.side == "bull", GOOD, BAD), size=9),
                                   error_x=dict(type="data", symmetric=False, array=sig.hi - sig.effect, arrayminus=sig.effect - sig.lo, color=MUTED)))
        fig.add_vline(x=0, line_color=REF)
        fig.add_vline(x=100 * C.COST_RT, line_dash="dot", line_color=MUTED)
        fig.add_vline(x=-100 * C.COST_RT, line_dash="dot", line_color=MUTED)
        fig.update_xaxes(title=f"pp over {h} sessions vs other stocks the same day (dotted: round-trip cost)")
        st.plotly_chart(fig_base(fig, 40 + 22 * len(sig)), width="stretch")
        st.caption("Green = bullish by definition (should sit right of 0), red = bearish (should sit left). Market-wide signals are compared across days instead.")
    ev = rec["statistics"].get("events") or {}
    if ev:
        with st.expander("Event studies (news and filings)"):
            show(pd.DataFrame([dict(kind=k.rsplit("|", 1)[0], tested_for=k.rsplit("|", 1)[1], status=v.get("status"), events=v.get("n_events"),
                                    first=v.get("first"), last=v.get("last"), first_half=_fmt((v.get("design") or {}).get("effect")),
                                    second_half=_fmt((v.get("validation") or {}).get("effect")), why=v.get("why")) for k, v in ev.items()]))
    with st.expander("Methods"):
        st.markdown(S.__doc__)


def evidence_view(rec):
    ev = pd.DataFrame(rec["evidence"])
    cats = ["all"] + sorted(ev.category.unique())
    cat = st.selectbox("Category", cats, key="dl-evcat")
    if cat != "all":
        ev = ev[ev.category == cat]
    cols = ["id", "category", "fact", "source", "reliability", "published", "event_day", "observed", "retrieved", "quality", "direction", "source_ref"]
    show(ev[cols], column_config={"source_ref": st.column_config.LinkColumn("link", display_text="open")})
    st.caption("All times IST (+05:30). 'published' = first public; 'event_day' = first session the market could react (15:30 rule); "
               "'observed' = market value time; 'retrieved' = when this app downloaded it. Direction is a pre-registered side or headline tone, not a verdict.")


# ---------------- nightly batch ----------------
def batch_tab():
    from core import stocklists as SL
    from debate import batch as B
    cfg = B.config()
    sel = cfg["selection"]
    st.caption("After every **Fetch data + run all agents**, the code-only part of the lab forecasts this list (no language model). "
               "The model still learns from all liquid stocks. Results: **Forecast scorecard**.")
    c = st.columns([1, 1, 1, 1.4])
    on = c[0].toggle("Run nightly", value=bool(cfg.get("enabled", True)), key="bt-on")
    if on != bool(cfg.get("enabled", True)):
        B.save_config({"enabled": on}); st.rerun()
    busy = jobs.current("debate-batch") is not None
    if c[1].button("Run for the latest day", disabled=busy, key="bt-run"):
        _start("debate-batch")
    if c[2].button("Replay last 20 sessions", disabled=busy, key="bt-replay",
                   help="Fills the scorecard quickly with RETROSPECTIVE forecasts (each day cut off at its own close). Reported apart from live forecasts."):
        _start("debate-replay")
    if c[3].button("Download NSE index lists", disabled=busy, key="bt-idx"):
        _start("stock-lists")
    _progress()
    syms_now, notes = SL.resolve(sel)
    with st.container(border=True, key="card-bt-sel"):
        st.markdown(f"##### {sel.get('name')} · {len(syms_now)} stocks")
        st.caption(SL.describe(sel) + " · questions: 5 sessions vs Nifty (main), 5 sessions absolute (second)")
        for n in notes:
            st.warning(n)
        if syms_now:
            st.caption(", ".join(syms_now))
    uni = universe()
    names = dict(zip(uni.Symbol, uni["Company Name"]))
    customs = SL.custom_lists()
    rules = {r["type"]: r for r in sel.get("include", [])}
    idx_sel = [r["name"] for r in sel.get("include", []) if r["type"] == "index"]
    sec_sel = [r["name"] for r in sel.get("include", []) if r["type"] == "sector"]
    cus_sel = [r["name"] for r in sel.get("include", []) if r["type"] == "custom" and r["name"] in customs]
    sym_sel = next((r["symbols"] for r in sel.get("include", []) if r["type"] == "symbols"), [])
    ex_sec = [r["name"] for r in sel.get("exclude", []) if r["type"] == "sector"]
    ex_sym = next((r["symbols"] for r in sel.get("exclude", []) if r["type"] == "symbols"), [])
    with st.expander("Change the stock list"), st.form("bt-form"):
        name = st.text_input("Name of this selection", sel.get("name", "Nifty 50"))
        c = st.columns(2)
        idx = c[0].multiselect("NSE indices", list(SL.INDICES), default=[x for x in idx_sel if x in SL.INDICES])
        secs = c[1].multiselect("Sectors (Nifty 500 industry)", SL.sectors(), default=[x for x in sec_sel if x in SL.sectors()])
        c = st.columns([1, 1, 1, 1])
        top_on = c[0].checkbox("Most liquid", value="top_liquid" in rules)
        top_n = c[0].number_input("how many", 10, 500, int(rules.get("top_liquid", {}).get("n", 50)), step=10)
        hold = c[1].checkbox("Your holdings", value="holdings" in rules)
        scr_ = c[2].checkbox("Screener shortlist", value="screener" in rules)
        expo = c[3].checkbox("Exposure list stocks", value="exposure" in rules)
        cus = st.multiselect("Custom lists", list(customs), default=cus_sel)
        syms = st.multiselect("Add stocks by hand", sorted(names), default=[x for x in sym_sel if x in names], format_func=lambda x: f"{x} — {names[x][:30]}")
        c = st.columns(2)
        xs = c[0].multiselect("Exclude sectors", SL.sectors(), default=[x for x in ex_sec if x in SL.sectors()])
        xy = c[1].multiselect("Exclude stocks", sorted(names), default=[x for x in ex_sym if x in names])
        if st.form_submit_button("Save selection", type="primary"):
            inc = ([{"type": "index", "name": x} for x in idx] + [{"type": "sector", "name": x} for x in secs]
                   + ([{"type": "top_liquid", "n": int(top_n)}] if top_on else []) + ([{"type": "holdings"}] if hold else [])
                   + ([{"type": "screener"}] if scr_ else []) + ([{"type": "exposure"}] if expo else []) + [{"type": "custom", "name": x} for x in cus]
                   + ([{"type": "symbols", "symbols": syms}] if syms else []))
            exc = [{"type": "sector", "name": x} for x in xs] + ([{"type": "symbols", "symbols": xy}] if xy else [])
            if not inc:
                st.error("Choose at least one group.")
            else:
                B.save_config({"selection": {"name": name.strip() or "Selection", "include": inc, "exclude": exc}})
                st.success("Saved. New forecasts use it from the next run; past forecasts keep the list they were made with.")
                st.rerun()
    with st.expander("Custom lists"):
        c = st.columns([1, 3, 1])
        nm = c[0].text_input("List name", key="bt-cn")
        members = c[1].multiselect("Stocks", sorted(names), key="bt-cm", format_func=lambda x: f"{x} — {names[x][:30]}")
        if c[2].button("Save list", key="bt-cs"):
            try:
                SL.save_custom(nm, members); st.success(f"Saved {nm}"); st.rerun()
            except ValueError as e:
                st.error(str(e))
        for k, v in customs.items():
            cc = st.columns([4, 1])
            cc[0].markdown(f"**{k}** ({len(v)}): {', '.join(v[:30])}{'…' if len(v) > 30 else ''}")
            if cc[1].button("Delete", key=f"bt-del-{k}"):
                SL.delete_custom(k); st.rerun()
    with st.expander("NSE index lists on this Mac"):
        show(SL.index_status())
        st.caption(f"From NSE's index files; refreshed when older than {SL.REFRESH_DAYS} days. If NSE can't be reached the saved copy is used.")
    fc = B.load()
    if len(fc):
        st.subheader("Recent batch forecasts")
        last = fc[fc.as_of == fc.as_of.max()]
        st.caption(f"{fc.as_of.max()}: {len(last)} forecasts · " + " · ".join(f"{k}: {v}" for k, v in last.verdict.value_counts().items()))
        show(last[["symbol", "sector", "target", "p_up", "base_up", "edge", "verdict", "active_bull", "active_bear", "calibration", "mode"]].round(3))


def _start(name):
    try:
        jobs.start(name); st.rerun()
    except RuntimeError as e:
        st.error(str(e))


# ---------------- model check ----------------
def model_tab():
    mt = store.INDEX.stat().st_mtime if store.INDEX.exists() else 0
    if _index(mt).empty:
        st.info("Run an analysis first: the model check shows the probability model behind it.")
        return
    rec = _pick("dl-pick-model")
    m = rec["model"]
    st.markdown(f"**Probability model for {rec['spec']['h']} sessions, {TARGETS[rec['spec']['target']]}** (cutoff {m['cutoff']}, "
                f"{m['n_train']:,} training rows, out-of-sample years {', '.join(map(str, m['oos_years']))})")
    st.markdown(f"**Calibration status: {m['calibration']}** — {m['calibration_why']}")
    rows = []
    for name, x in m["metrics"].items():
        if name.startswith("_"):
            continue
        rows.append(dict(model=name, brier=x.get("brier"), log_loss=x.get("logloss"), auc=x.get("auc"), calibration_error=x.get("ece"),
                         brier_skill=x.get("bss"), skill_lo=x.get("bss_lo"), skill_hi=x.get("bss_hi"), predictions=x.get("n")))
    show(pd.DataFrame(rows).round(4))
    st.caption("Same out-of-sample rows for every model (walk-forward by year, purged so no training outcome overlaps the test year). "
               "Brier skill > 0 means better than always predicting the historical base rate; the interval resamples days. "
               "AUC 0.5 = no ranking ability. Fundamentals-only and news-only baselines are not possible with the data available "
               "(no fundamentals; news history too short). The full debate pipeline is scored only on its own saved forecasts (Forecast history).")
    rel = (m["metrics"].get("Full statistical model") or {}).get("reliability")
    if rel:
        r = pd.DataFrame(rel)
        fig = go.Figure([go.Scatter(x=[r.pred.min(), r.pred.max()], y=[r.pred.min(), r.pred.max()], mode="lines", line=dict(color=REF, dash="dot"), name="perfect"),
                         go.Scatter(x=r.pred, y=r.obs, mode="markers+lines", marker=dict(color=ACCENT), name="full model")])
        fig.update_xaxes(title="predicted"); fig.update_yaxes(title="observed")
        st.plotly_chart(fig_base(fig, 320, legend=True), width="stretch")
        st.caption("Reliability diagram (10 equal-count bins). Points on the dotted line = well calibrated.")
    with st.expander("Model details"):
        st.markdown(PR.__doc__)
        st.markdown("Features: " + ", ".join(PR.FEATURE_LABEL[f] for f in PR.FEATURES))


def how_tab():
    from debate import engine as EN, judge as JG, agents as AGM, evidence as EVM
    st.markdown("""
**What the lab does.** For one stock and one precisely defined question (e.g. *"Is the 5-session price return above 0%?"*), two agents
build the strongest bullish and bearish cases from the same evidence; a statistical agent tests every claim on 10 years of history;
a probability engine estimates the probability with a model whose out-of-sample record is shown; a judge decides — and often says
**Insufficient evidence**. That answer is a feature: short-horizon stock direction is very hard to predict, and the lab only claims
skill when its own out-of-sample numbers show it.

**What it cannot see.** Fundamentals, analyst estimates, options/futures, short interest, intraday bars and BSE prices are not
connected; the agents are told so and the page lists them as unavailable. Stored news linked to stocks starts mid-2026, so most
news event studies return *Insufficient data* for now; NSE filings history appears after **Data health → Download all history**.

**Honesty rules built into the code.**
- Agents can cite only evidence IDs that exist; anything else is marked an unverified hypothesis.
- Numbers come from code. A model-written sentence with a number not found in the evidence or verdict facts is rejected.
- The probability model is evaluated walk-forward; 'calibrated' and 'skill' are claimed only from those results.
- Correlated signals count once; duplicate coverage of one story counts once.
- Forecast records are written once with hashes; scoring never edits them; replays are labelled and scored apart from live forecasts.
- Text inside headlines and filings is treated as data. The models' output is only text and IDs: it cannot run tools or change any number.
""")
    with st.expander("Debate rounds"):
        st.markdown(EN.__doc__)
    with st.expander("Judge rules"):
        st.markdown(JG.__doc__)
    with st.expander("Evidence layer and timestamps"):
        st.markdown(EVM.__doc__)
    with st.expander("Pre-registered signals"):
        show(pd.DataFrame([SG.info(k) for k in SG.BY_ID]))
    with st.expander("Data providers"):
        show(pd.DataFrame([
            ("Angel One SmartAPI", "Daily OHLCV, Nifty 50, India VIX", "Daily after close", "2015 →", "Your keys in ~/.angel_env; bars dated by session"),
            ("NSE corporate announcements", "Filings", "On fetch; backfill on request", "Backfill to 2016", "Exchange timestamp (IST)"),
            ("News sitemaps / RSS", "Headlines linked to stocks", "On fetch", "Short (mostly 2026)", "Publisher timestamp; deduplicated by title"),
            ("FRED, GPR, GDELT, NSE FII/DII", "Macro factors", "On fetch", "Years", "Observation date; aligned to the next Indian session"),
            ("Not connected", "Fundamentals, estimates, derivatives, intraday", "—", "—", "Shown as unavailable"),
        ], columns=["provider", "data", "updates", "history", "timestamps / notes"]))
