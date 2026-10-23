"""Forecast scorecard: every Market Debate Lab forecast (nightly batch and debates) compared with what happened."""
from ui.common import *  # noqa: F401,F403
from core import jobs
from debate import store, evaluate as EV, scorecard as SC, batch as B

TARGET = {"rel": "5 sessions vs Nifty (main)", "abs": "5 sessions absolute (second)"}


@st.cache_data(ttl=60)
def _data(m1, m2, m3):
    return store.outcomes(), EV.all_forecasts()


def _mt(p):
    return p.stat().st_mtime if p.exists() else 0


def page():
    st.title("Forecast scorecard")
    st.caption("Forecasts compared with actual outcomes, scored after their horizon ends. All numbers computed by code. "
               "A forecast is never changed after it is made; outcomes are stored next to it.")
    out, fc = _data(_mt(store.OUTCOMES), _mt(B.FORECASTS), _mt(store.INDEX))
    c = st.columns([1, 3])
    if c[0].button("Score finished forecasts", disabled=jobs.current("debate-eval") is not None, key="sc-eval"):
        try:
            jobs.start("debate-eval"); st.cache_data.clear(); st.rerun()
        except RuntimeError as e:
            st.error(str(e))
    pending = 0 if fc.empty else len(fc) - (len(out) if len(out) else 0)
    c[1].caption(f"{len(fc):,} forecasts made · {len(out) if len(out) else 0:,} scored · about {max(pending, 0):,} waiting for their horizon to end. "
                 "Scoring runs after every **Fetch data + run all agents**.")
    if out is None or out.empty:
        st.info("Nothing scored yet. Forecasts are scored 5 sessions after they are made. Turn on the nightly batch "
                "(Market Debate Lab → Nightly batch), or replay recent sessions there to see the page filled (labelled retrospective).")
        return
    o = SC.binary(out)
    with st.container(border=True):
        f = st.columns(6)
        src = f[0].multiselect("Source", sorted(o.source.dropna().unique()), default=sorted(o.source.dropna().unique()), key="sc-src")
        mode = f[1].radio("Forecasts", ["live", "retrospective", "both"], index=0 if (o["mode"] == "live").any() else 1, key="sc-mode",
                          help="Live = made with that day's data. Retrospective = replays made later; never mixed into the live record unless you choose both.")
        tg = f[2].multiselect("Question", list(TARGET), default=list(TARGET), format_func=TARGET.get, key="sc-t")
        lists = sorted(o.list_name.dropna().unique()) if "list_name" in o else []
        ls = f[3].multiselect("Stock list", lists, default=lists, key="sc-l")
        secs = sorted(o.sector.dropna().unique()) if "sector" in o else []
        sc = f[4].multiselect("Sector", secs, key="sc-s", placeholder="all")
        d0, d1 = pd.to_datetime(o.as_of).min().date(), pd.to_datetime(o.as_of).max().date()
        rng = f[5].date_input("Forecast dates", (d0, d1), min_value=d0, max_value=d1, key="sc-d")
    v = o[o.source.isin(src) & o.target.isin(tg)]
    if mode != "both":
        v = v[v["mode"] == mode]
    if ls and "list_name" in v:
        v = v[v.list_name.isin(ls) | v.list_name.isna()]
    if sc:
        v = v[v.sector.isin(sc)]
    if isinstance(rng, tuple) and len(rng) == 2:
        v = v[(pd.to_datetime(v.as_of) >= pd.Timestamp(rng[0])) & (pd.to_datetime(v.as_of) <= pd.Timestamp(rng[1]))]
    h = SC.headline(v)
    st.markdown(f"### {h['status']}")
    if h["n"]:
        k = st.columns(7)
        k[0].metric("Scored forecasts", f"{h['n']:,}", f"{h['days']} days · {h['stocks']} stocks", delta_color="off")
        k[1].metric("Brier score", f"{h['brier']:.4f}", f"base rate {h['brier_base']:.4f}" if np.isfinite(h["brier_base"]) else None,
                    delta_color="off", help="Lower is better. 0.25 = always saying 50%.")
        k[2].metric("Skill vs base rate", f"{h['skill']:+.4f}" if np.isfinite(h["skill"]) else "—",
                    f"95% {h['skill_lo']:+.3f} … {h['skill_hi']:+.3f}" if np.isfinite(h["skill"]) else None, delta_color="off")
        k[3].metric("Hit rate", f"{100 * h['hit']:.1f}%", help="Share where P > 50% matched the outcome.")
        k[4].metric("Calibration error", f"{h['ece']:.3f}" if np.isfinite(h["ece"]) else "—", help="Mean gap between predicted and observed; lower is better.")
        k[5].metric("Outcome positive", f"{100 * h['share_positive']:.1f}%", f"avg forecast {100 * h['mean_p']:.1f}%", delta_color="off")
        k[6].metric("Avg actual return", f"{h['mean_return']:+.2f}%")
    t = st.tabs(["Forecast vs actual", "Calibration", "Over time", "Probability buckets", "Verdicts", "Breakdowns", "Biggest misses", "Debate vs batch"])
    with t[0]:
        cols = ["as_of", "end_date", "symbol", "sector", "target", "p_up", "base_up", "verdict", "realized_return_pct", "outcome", "hit", "brier", "source", "mode", "list_name"]
        show(v.assign(hit=v.hit.astype(bool)).sort_values(["as_of", "symbol"], ascending=[False, True])[[c for c in cols if c in v]],
             column_config={"p_up": st.column_config.NumberColumn("P(positive)", format="%.3f"), "base_up": st.column_config.NumberColumn("base rate", format="%.3f"),
                            "realized_return_pct": st.column_config.NumberColumn("actual %", format="%+.2f"), "hit": st.column_config.CheckboxColumn("hit"),
                            "brier": st.column_config.NumberColumn(format="%.3f")})
        st.download_button("Download (.csv)", v.to_csv(index=False), file_name="forecast_vs_actual.csv", mime="text/csv")
    with t[1]:
        r = SC.reliability(v)
        if r.empty:
            st.caption("Needs at least 20 scored forecasts.")
        else:
            lo, hi = float(min(r.predicted.min(), r.observed.min())) - 0.02, float(max(r.predicted.max(), r.observed.max())) + 0.02
            fig = go.Figure([go.Scatter(x=[lo, hi], y=[lo, hi], mode="lines", line=dict(color=REF, dash="dot"), name="perfect calibration"),
                             go.Scatter(x=r.predicted, y=r.observed, mode="markers+lines", name="forecasts", marker=dict(color=ACCENT, size=8),
                                        text=[f"{n} forecasts" for n in r.n], hovertemplate="predicted %{x:.3f}<br>observed %{y:.3f}<br>%{text}")])
            fig.update_xaxes(title="predicted probability (equal-count bins)"); fig.update_yaxes(title="share that came true")
            st.plotly_chart(fig_base(fig, 380, legend=True), width="stretch")
            show(r.round(3))
            st.caption("On the dotted line = what the model says happens that often. Forecasts bunched near 50% mean the model rarely commits.")
    with t[2]:
        w = SC.over_time(v)
        if len(w):
            fig = go.Figure([go.Bar(x=w.week, y=w.skill, name="weekly skill", marker_color=np.where(w.skill > 0, GOOD, BAD)),
                             go.Scatter(x=w.week, y=w.rolling_skill, name="rolling 4-week skill", line=dict(color=ACCENT, width=2))])
            fig.add_hline(y=0, line_color=REF)
            fig.update_yaxes(title="skill vs base rate (> 0 better)")
            st.plotly_chart(fig_base(fig, 340, legend=True), width="stretch")
            show(w.round(4))
            st.caption("Week = when the outcome became known. Single weeks are noisy (forecasts in a week share the same market move).")
    with t[3]:
        d = SC.deciles(v)
        if d.empty:
            st.caption("Needs at least 20 scored forecasts.")
        else:
            fig = go.Figure(go.Bar(x=d.bucket.astype(str), y=d.mean_return_pct, marker_color=np.where(d.mean_return_pct > 0, GOOD, BAD),
                                   text=[f"{x:.0%}" for x in d.share_positive], textposition="outside"))
            fig.update_xaxes(title="probability bucket (1 = lowest predicted P)"); fig.update_yaxes(title="average actual return %")
            st.plotly_chart(fig_base(fig, 320), width="stretch")
            show(d.round(3))
            st.caption("If the model works, returns and the share positive (labels) should rise from left to right.")
    with t[4]:
        show(SC.verdicts(v).round(4))
        st.caption("What happened after each verdict. If 'Insufficient evidence' cases do as well as the others, abstaining cost nothing; "
                   "if bias verdicts don't beat them, the verdict adds nothing.")
    with t[5]:
        for by, lab in (("sector", "By sector"), ("regime", "By market regime at forecast time"), ("symbol", "By stock")):
            if by in v and v[by].notna().any():
                st.markdown(f"**{lab}**")
                show(SC._group(v, by).sort_values("forecasts", ascending=False).round(4))
        st.caption(f"Rows with fewer than {SC.MIN_CELL} forecasts (enough = false) are anecdotes.")
    with t[6]:
        show(SC.misses(v).round(3), "No misses yet.")
        st.caption("Wrong forecasts sorted by how far the probability was from 50%.")
    with t[7]:
        hh = SC.head_to_head(o if mode == "both" else o[o["mode"] == mode])
        if hh.empty:
            st.caption("Needs debate analyses on the same stock, day and question as the nightly batch. Note: today the debate's probability IS the "
                       "statistical model's, so the two match; a separate debate-judge probability is the next planned step.")
        else:
            show(hh.round(4))
    with st.expander("How these numbers are computed"):
        st.markdown(SC.__doc__)
