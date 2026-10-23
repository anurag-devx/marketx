"""API: Market Debate Lab, Forecast scorecard, Macro & geopolitics, Market report, News research."""
import json
import numpy as np, pandas as pd
from core import config as C, jobs
from web.api import route, prices, universe, UserError
from web.r_agents import table

VERDICT_TONE = {"Bullish bias": "pos", "Bearish bias": "neg"}


def _f(x, d=2, plus=True):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), d)


# ---------------- Debate Lab ----------------
@route("GET", "debate/form")
def debate_form(_):
    from core import llm
    from debate.engine import TARGETS
    px = prices()
    last = None if px is None else str(pd.Timestamp(px.date.max()).date())
    ok, msg = llm.status("debate")
    u = universe()
    return dict(symbols=[[s, f"{s} — {c}"] for s, c in zip(u.Symbol, u["Company Name"])], last=last, model_ok=ok, model=msg,
                targets=[[k, v + (" (main)" if k == "rel" else " (second)" if k == "abs" else " (experimental)")] for k, v in TARGETS.items()],
                availability=_availability(last))


def _availability(last):
    from core import news as N, announcements as A
    from debate import evidence as EVM
    rows = [dict(data="Daily prices (Angel One, NSE)", status=f"to {last}" if last else "none")]
    try:
        n = N.load_store()
        linked = n[n.symbols.fillna("") != ""]
        rows.append(dict(data="News linked to stocks", status=f"{len(linked):,} headlines since {linked.published.min():%d %b %Y}" if len(linked) else "none"))
    except Exception:
        pass
    try:
        a = A.load()
        rows.append(dict(data="NSE filings", status=f"{len(a):,} since {a.published.min():%d %b %Y}" if len(a) else "none"))
    except Exception:
        pass
    rows.append(dict(data="Macro sensitivities", status="ready" if (C.RESULTS / "macro" / "sens_3y.csv").exists() else "run the macro study"))
    for k, (a_, b_) in EVM.UNAVAILABLE.items():
        rows.append(dict(data=a_, status="not available: " + b_))
    return rows


@route("GET", "debate/progress")
def debate_progress(_):
    from debate import store
    a = jobs.activity("debate")
    if not a:
        hist = [x for x in jobs.history(40) if x["job"] in ("debate", "debate-eval", "debate-batch", "debate-replay", "stock-lists")]
        h = hist[0] if hist else None
        return dict(running=False, last=dict(job=h["job"], result=h["result"], finished=h["finished"], file=h["file"],
                                             tail=jobs.tail(h["path"], 25) if h["result"] != "ok" else "") if h else None)
    p = store.read_progress() if a["job"] == "debate" else None
    return dict(running=True, job=a["job"], label=(p or {}).get("step") or a["label"], note=(p or {}).get("note"), pct=(p or {}).get("pct"),
                elapsed=a["elapsed"], tail=jobs.tail(a["log"], 20))


@route("GET", "debate/list")
def debate_list(_):
    from debate import store
    from debate.engine import TARGETS
    idx = store.index()
    if idx.empty:
        return []
    idx = idx.sort_values("created", ascending=False)
    return [[r.analysis_id, f"{r.symbol} · {r.horizon} sessions · {TARGETS.get(r.target, r.target)} · as of {r.as_of} · {r.verdict}"
             + (" · replay" if r.mode == "retrospective" else "")] for r in idx.itertuples()]


def _pct(x, d=1):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{100 * x:.{d}f}%"


@route("GET", "debate/record")
def debate_record(p):
    from debate import store, stats as S, signals as SG
    rec = store.load(p.get("id", ""))
    if not rec:
        raise UserError("analysis not found")
    s, v, info = rec["spec"], rec["verdict"], rec["info"]
    view = p.get("view", "verdict")
    pr, br = v["probabilities"], v["base_rates"]
    head = dict(symbol=s["symbol"], company=info.get("company", ""), verdict=v["verdict"], tone=VERDICT_TONE.get(v["verdict"], ""),
                definition=s["definition"], reference_price=s["reference_price"], reference_time=s["reference_time"],
                p_up=pr.get("up"), base_up=br.get("up"), p_down=pr.get("down"), p_neutral=pr.get("neutral"), h=s["h"], target=s["target"],
                expected=(v.get("expected_return") or {}).get("mean") if (v.get("expected_return") or {}).get("usable") else None,
                label=v["probability_label"], calibration=v["calibration"], created=rec["created"], mode=rec.get("mode", "live"),
                mode_note=rec.get("mode_note", ""), cutoff=s["cutoff"])
    secs = []
    if view == "verdict":
        secs.append(dict(kind="md", title=v["verdict"], text="\n".join(f"- {r}" for r in v["reasons"]) + "\n\n" + (v.get("explanation") or "")
                         + f"\n\n*Explanation written by: {v.get('explanation_writer')}. {v['note']}*"))
        lab = {"up": "positive / up", "neutral": "neutral", "down": "not positive / down"}
        secs.append(dict(kind="grid", cols=2, items=[
            table("Probabilities (model vs historical base rate)", pd.DataFrame([dict(outcome=lab[k], model=100 * pr[k], base_rate=100 * (br.get(k) or 0))
                                                                                  for k in ("up", "neutral", "down") if k in pr]),
                  {"model": "pct1", "base_rate": "pct1"}, tools=False, note="The probabilities sum to 100%. A probability is a forecast, not a guarantee."),
            table("What moved the model (largest contributions)", pd.DataFrame(v.get("top_drivers") or []).round(3), tools=False)]))
        lines = []
        d = v.get("downside")
        if d:
            lines.append(f"**Downside (this stock's own history):** 1 in 10 past {s['h']}-session windows lost more than {-d['p10']:.1f}%; "
                         f"1 in 20 more than {-d['p05']:.1f}% ({d['n']} windows, last 3 years).")
        er = v.get("expected_return")
        if er:
            lines.append(f"**Similar past predictions:** average {er['mean']:+.2f}% (95% {er['ci_lo']:+.2f} to {er['ci_hi']:+.2f}), median {er['median']:+.2f}%, "
                         f"10th–90th percentile {er['p10']:+.1f}% to {er['p90']:+.1f}% ({er['n']:,} predictions). {er['note']}")
        lv = v.get("invalidation_levels") or {}
        if lv.get("bull_invalid_below"):
            lines.append(f"- Bull case weakens below ₹{lv['bull_invalid_below']:,.2f} (lower of 50-day SMA and 55-day low)")
        if lv.get("bear_invalid_above"):
            lines.append(f"- Bear case weakens above ₹{lv['bear_invalid_above']:,.2f} (higher of 50-day SMA and 55-day high)")
        for side, xs in (v.get("catalysts") or {}).items():
            for x in xs[:4]:
                lines.append(f"- Catalyst ({side}): {x}")
        for x in v.get("update_when", []):
            lines.append(f"- Update when: {x}")
        if lines:
            secs.append(dict(kind="md", title="Risks, levels and catalysts", text="\n".join(lines)))
        g = v.get("evidence_groups") or {}
        if g:
            secs.append(table("Supported evidence by group (each group counts once)", pd.DataFrame([dict(group=k, bull=x["bull"], bear=x["bear"]) for k, x in g.items()]), tools=False))
    elif view == "sides":
        items = []
        led = {r["claim_id"]: r for r in rec["ledger"]}
        for side, title in (("bull", "Bullish case"), ("bear", "Bearish case")):
            r, rv = rec["reports"][side], rec["revisions"][side]
            txt = (f"*Written by {r['writer']}{(' · ' + r['model']) if r.get('model') else ''} · argument confidence {rv['confidence']} ({r['confidence_note']})*\n\n"
                   f"**Revised thesis.** {rv['thesis']}\n\n**Initial thesis.** {r['thesis']}\n\n"
                   + (f"**Strong scenario:** {r['strong_scenario']}\n\n" if r["strong_scenario"] else "")
                   + (f"**Moderate scenario:** {r['moderate_scenario']}\n\n" if r["moderate_scenario"] else "")
                   + "".join(f"**{lab}:** {'; '.join(r[k])}\n\n" for lab, k in (("Conditions", "conditions"), ("Invalidation", "invalidation"), ("Assumptions", "assumptions")) if r[k])
                   + (f"**Catalysts:** {'; '.join(r['catalysts'])}\n\n" if r["catalysts"] else "")
                   + (f"Missing data: {'; '.join(r['missing_data'])}" if r["missing_data"] else ""))
            items.append(dict(kind="md", title=title, text=txt))
            rows = [dict(id=c["id"], claim=c["text"], evidence=", ".join(c["evidence_ids"]) or "—", status=led.get(c["id"], {}).get("status", ""),
                         after_revision="withdrawn" if c["id"] in rv["conceded"] else "kept", flags="; ".join(c["flags"])) for c in r["claims"]]
            items.append(table(f"{title}: claims", pd.DataFrame(rows), {"claim": "long", "status": "status"}, tools=False))
        secs.append(dict(kind="grid", cols=2, items=[items[0], items[2]]))
        secs += [items[1], items[3]]
    elif view == "timeline":
        lines = ["**Round 1 — independent theses** (same evidence and cutoff; neither saw the other)"]
        for side in ("bull", "bear"):
            r = rec["reports"][side]
            lines.append(f"- *{r['agent']}*: {r['thesis'][:500]}")
        lines.append("\n**Round 3 — statistical challenge**")
        lines += [f"- {n}" for n in rec["statistics"]["notes"]]
        lines.append("\n**Round 4 — revised theses**")
        for side in ("bull", "bear"):
            rv = rec["revisions"][side]
            lines.append(f"- *{rv['agent']}* ({rv['writer']}): kept {len(rv['maintained'])}, withdrew {len(rv['conceded'])}. {rv['thesis'][:500]}")
            for rp in rv.get("replies", []):
                lines.append(f"  - reply to {rp['objection_id']}: {rp['reply']}")
        secs.append(dict(kind="md", title="Debate timeline", text="\n".join(lines)))
        ob = pd.DataFrame(rec["objections"])
        if len(ob):
            ob = ob[["id", "round", "from", "claim_id", "type", "text", "evidence_ids", "by"]].assign(evidence_ids=lambda d: d.evidence_ids.map(lambda x: ", ".join(x)))
        secs.append(table("Round 2 — cross-examination", ob, {"text": "long"}, tools=False, empty="No objections."))
    elif view == "stats":
        rows = []
        for r in rec["ledger"]:
            t = next((x for x in r["tests"] if x["kind"] != "qualitative"), {}) if r["tests"] else {}
            res = rec["statistics"]["signals"].get(t["evidence_id"][3:], {}) if t.get("kind") == "signal" else {}
            d, va, rc = res.get("design") or {}, res.get("validation") or {}, res.get("recent") or {}
            rows.append(dict(claim=r["claim_id"], side=r["side"], status=r["status"], claim_text=r["claim"][:160], test=t.get("kind", "—"),
                             design=_f(d.get("effect")), design_ci=f"{_f(d.get('ci_lo'))} … {_f(d.get('ci_hi'))}" if d else "—", n_design=d.get("eff_n"),
                             q_fdr=_f(res.get("q_design"), 3), validation=_f(va.get("effect")), p_val=_f(va.get("p"), 3), recent=_f(rc.get("effect")),
                             hit_signal=_pct(d.get("hit_signal")), hit_others=_pct(d.get("hit_control")), why=r["why"]))
        secs.append(dict(kind="note", text=f"Effects in percentage points of {s['h']}-session {'relative ' if s['target'] == 'rel' else ''}return. Design 2016–20, validation 2021–23, recent 2024+ "
                         "(reported, never used to decide). 'Not supported' means the data could not show the effect — not that it is false."))
        secs.append(table("Claims tested on history", pd.DataFrame(rows), {"status": "status", "claim_text": "long", "why": "long", "design": "snum2", "validation": "snum2", "recent": "snum2"}))
        sig = pd.DataFrame([dict(signal=k, side=SG.BY_ID[k]["side"], validation_effect=(v_.get("validation") or {}).get("effect"),
                                 lo=(v_.get("validation") or {}).get("ci_lo"), hi=(v_.get("validation") or {}).get("ci_hi"))
                            for k, v_ in rec["statistics"]["signals"].items() if k in SG.BY_ID]).dropna(subset=["validation_effect"])
        if len(sig):
            sig = sig.sort_values("validation_effect", ascending=False)
            secs.append(dict(kind="chart", type="hbar", title="Every pre-registered signal — validation-period effect (pp vs other stocks the same day)", suffix="",
                             labels=(sig.signal + " (" + sig.side + ")").tolist(), values=sig.validation_effect.round(3).tolist(), decimals=2,
                             note="Bullish signals should sit right of 0, bearish left. Round-trip cost is about 0.3 pp."))
        ev = rec["statistics"].get("events") or {}
        if ev:
            secs.append(table("Event studies (news and filings)", pd.DataFrame([dict(kind=k.rsplit("|", 1)[0], tested_for=k.rsplit("|", 1)[1], status=x.get("status"),
                              events=x.get("n_events"), first=x.get("first"), last=x.get("last"), why=x.get("why")) for k, x in ev.items()]), {"why": "long"}, tools=False))
        secs.append(dict(kind="md", title="Methods", text=S.__doc__ or ""))
    elif view == "evidence":
        ev = pd.DataFrame(rec["evidence"])
        cols = ["id", "category", "fact", "source", "reliability", "published", "event_day", "quality", "direction", "source_ref"]
        secs.append(dict(kind="table", rows=ev[[c for c in cols if c in ev]], csv=f"evidence_{rec['analysis_id']}",
                         columns=[["id", "ID"], ["category", "Category"], ["fact", "Fact", "long"], ["source", "Source"], ["reliability", "Reliability"],
                                  ["published", "Published"], ["event_day", "Event day"], ["quality", "Quality"], ["direction", "Direction"], ["source_ref", "Link", "link"]],
                         note="All times IST. 'published' = first public; 'event_day' = first session the market could react (15:30 rule). Direction is a pre-registered side or headline tone, not a verdict."))
    elif view == "record":
        return dict(head=head, record=rec)
    return dict(head=head, sections=secs)


@route("GET", "debate/model")
def debate_model(p):
    from debate import store, probability as PR
    from debate.engine import TARGETS
    rec = store.load(p.get("id", ""))
    if not rec:
        raise UserError("analysis not found")
    m = rec["model"]
    rows = [dict(model=n, brier=x.get("brier"), log_loss=x.get("logloss"), auc=x.get("auc"), calibration_error=x.get("ece"), brier_skill=x.get("bss"),
                 skill_lo=x.get("bss_lo"), skill_hi=x.get("bss_hi"), predictions=x.get("n")) for n, x in m["metrics"].items() if not n.startswith("_")]
    secs = [dict(kind="note", text=f"Probability model for {rec['spec']['h']} sessions, {TARGETS[rec['spec']['target']]} (cutoff {m['cutoff']}, {m['n_train']:,} training rows, "
                 f"out-of-sample years {', '.join(map(str, m['oos_years']))}). Calibration status: {m['calibration']} — {m['calibration_why']}"),
            table("Out-of-sample scores (same rows for every model)", pd.DataFrame(rows).round(4), {"brier_skill": "snum2"}, tools=False,
                  note="Brier skill > 0 means better than always predicting the historical base rate. AUC 0.5 = no ranking ability.")]
    rel = (m["metrics"].get("Full statistical model") or {}).get("reliability")
    if rel:
        r = pd.DataFrame(rel)
        secs.append(dict(kind="chart", type="line", title="Reliability: predicted vs observed (points on the diagonal = calibrated)",
                         x=[f"{x:.2f}" for x in r.pred], series=[dict(name="observed", y=r.obs.round(3).tolist()), dict(name="perfect", y=r.pred.round(3).tolist(), dash=True)]))
    secs.append(dict(kind="md", title="Model details", text=(PR.__doc__ or "") + "\n\nFeatures: " + ", ".join(PR.FEATURE_LABEL[f] for f in PR.FEATURES)))
    return dict(sections=secs)


@route("GET", "debate/how")
def debate_how(_):
    from debate import engine as EN, judge as JG, evidence as EVM, signals as SG
    return dict(sections=[
        dict(kind="md", title="What the lab does", text=(
            "For one stock and one precisely defined question, two agents build the strongest bullish and bearish cases from the same evidence; "
            "a statistical agent tests every claim on 10 years of history; a probability engine estimates the probability with a model whose "
            "out-of-sample record is shown; a judge decides — and often says **Insufficient evidence**. That answer is a feature: short-horizon "
            "direction is very hard to predict, and the lab only claims skill when its own out-of-sample numbers show it.\n\n"
            "**Honesty rules built into the code**\n- Agents can cite only evidence IDs that exist.\n- Numbers come from code; a model sentence with a number not in the facts is rejected.\n"
            "- The probability model is evaluated walk-forward; 'calibrated' is claimed only from those results.\n- Correlated signals count once.\n"
            "- Forecast records are written once with hashes; replays are labelled and scored apart.\n- Text inside headlines and filings is treated as data.")),
        dict(kind="md", title="Debate rounds", text=EN.__doc__ or ""), dict(kind="md", title="Judge rules", text=JG.__doc__ or ""),
        dict(kind="md", title="Evidence layer and timestamps", text=EVM.__doc__ or ""),
        table("Pre-registered signals", pd.DataFrame([SG.info(k) for k in SG.BY_ID]), {"definition": "long"}, tools=False)])


@route("GET", "debate/batch")
def debate_batch(_):
    from core import stocklists as SL
    from debate import batch as B
    cfg = B.config()
    sel = cfg["selection"]
    syms, notes = SL.resolve(sel)
    fc = B.load()
    recent = None
    if len(fc):
        last = fc[fc.as_of == fc.as_of.max()]
        recent = dict(as_of=fc.as_of.max(), counts=last.verdict.value_counts().to_dict(),
                      rows=last[["symbol", "sector", "target", "p_up", "base_up", "edge", "verdict", "calibration", "mode"]].round(3))
    return dict(enabled=bool(cfg.get("enabled", True)), selection=sel, describe=SL.describe(sel), symbols=syms, notes=notes,
                indices=list(SL.INDICES), sectors=SL.sectors(), customs=SL.custom_lists(), index_status=SL.index_status(), recent=recent)


@route("POST", "debate/batch/config")
def debate_batch_config(_, body):
    from debate import batch as B
    out = {}
    if "enabled" in body:
        out["enabled"] = bool(body["enabled"])
    if "selection" in body:
        sel = body["selection"]
        if not sel.get("include"):
            raise UserError("choose at least one group")
        out["selection"] = dict(name=str(sel.get("name") or "Selection")[:60], include=sel["include"], exclude=sel.get("exclude", []))
    B.save_config(out)
    return dict(ok=True)


@route("POST", "debate/custom")
def debate_custom(_, body):
    from core import stocklists as SL
    if body.get("delete"):
        SL.delete_custom(body["delete"])
    else:
        try:
            SL.save_custom(body.get("name", ""), body.get("symbols", []))
        except ValueError as e:
            raise UserError(str(e))
    return dict(ok=True)


# ---------------- Forecast scorecard ----------------
@route("GET", "scorecard")
def scorecard(p):
    from debate import store, evaluate as EV, scorecard as SC
    out, fc = store.outcomes(), EV.all_forecasts()
    n_fc, n_out = (0 if fc is None else len(fc)), (0 if out is None else len(out))
    status = f"{n_fc:,} forecasts made · {n_out:,} scored · about {max(n_fc - n_out, 0):,} waiting for their horizon to end."
    if out is None or out.empty:
        return dict(status=status, empty=True)
    o = SC.binary(out)
    filters = dict(sources=sorted(o.source.dropna().unique()), lists=sorted(o.list_name.dropna().unique()) if "list_name" in o else [],
                   sectors=sorted(o.sector.dropna().unique()) if "sector" in o else [], first=str(pd.to_datetime(o.as_of).min().date()),
                   last=str(pd.to_datetime(o.as_of).max().date()), has_live=bool((o["mode"] == "live").any()))
    v = o
    if p.get("sources"):
        v = v[v.source.isin(p["sources"].split("|"))]
    if p.get("targets"):
        v = v[v.target.isin(p["targets"].split(","))]
    mode = p.get("mode") or ("live" if filters["has_live"] else "retrospective")
    if mode != "both":
        v = v[v["mode"] == mode]
    if p.get("lists") and "list_name" in v:
        v = v[v.list_name.isin(p["lists"].split("|")) | v.list_name.isna()]
    if p.get("sectors"):
        v = v[v.sector.isin(p["sectors"].split("|"))]
    if p.get("start"):
        v = v[pd.to_datetime(v.as_of) >= pd.Timestamp(p["start"])]
    if p.get("end"):
        v = v[pd.to_datetime(v.as_of) <= pd.Timestamp(p["end"])]
    h = SC.headline(v)
    fin = lambda x, f="{:.4f}": f.format(x) if x is not None and np.isfinite(x) else "—"
    secs = [dict(kind="note", tone="info" if h["status"].startswith("Better") else "warn" if h["status"].startswith("Worse") else "info", text=h["status"])]
    if h["n"]:
        secs.append(dict(kind="kpis", cols=4, items=[dict(label="Scored forecasts", value=f"{h['n']:,}", delta=f"{h['days']} days · {h['stocks']} stocks"),
                         dict(label="Brier score (lower is better)", value=fin(h["brier"]), delta=f"base rate {fin(h['brier_base'])}"),
                         dict(label="Skill vs base rate", value=fin(h["skill"], "{:+.4f}"), delta=f"95% {fin(h['skill_lo'], '{:+.3f}')} … {fin(h['skill_hi'], '{:+.3f}')}"),
                         dict(label="Hit rate", value=f"{100 * h['hit']:.1f}%"), dict(label="Calibration error", value=fin(h["ece"], "{:.3f}")),
                         dict(label="Outcome positive", value=f"{100 * h['share_positive']:.1f}%", delta=f"avg forecast {100 * h['mean_p']:.1f}%"),
                         dict(label="Avg actual return", value=f"{h['mean_return']:+.2f}%")]))
    view = p.get("view", "table")
    if view == "table":
        cols = ["as_of", "end_date", "symbol", "sector", "target", "p_up", "base_up", "verdict", "realized_return_pct", "outcome", "hit", "brier", "source", "mode", "list_name"]
        secs.append(dict(kind="table", rows=v.assign(hit=v.hit.astype(bool)).sort_values(["as_of", "symbol"], ascending=[False, True])[[c for c in cols if c in v]],
                         csv="forecast_vs_actual", columns=[["as_of", "Forecast date", "date"], ["end_date", "Ends", "date"], ["symbol", "Stock", "symonly"], ["sector", "Sector"],
                         ["target", "Question"], ["p_up", "P(positive)", "num3"], ["base_up", "Base rate", "num3"], ["verdict", "Verdict"], ["realized_return_pct", "Actual", "spct2"],
                         ["outcome", "Outcome"], ["hit", "Hit", "bool"], ["brier", "Brier", "num3"], ["source", "Source"], ["mode", "Mode"], ["list_name", "List"]]))
    elif view == "calibration":
        r = SC.reliability(v)
        if r.empty:
            secs.append(dict(kind="note", text="Needs at least 20 scored forecasts."))
        else:
            secs.append(dict(kind="chart", type="line", title="Predicted probability vs share that came true", x=[f"{x:.2f}" for x in r.predicted],
                             series=[dict(name="observed", y=r.observed.round(3).tolist()), dict(name="perfect calibration", y=r.predicted.round(3).tolist(), dash=True)]))
            secs.append(table(None, r.round(3), tools=False))
    elif view == "time":
        w = SC.over_time(v)
        if len(w):
            secs.append(dict(kind="chart", type="line", title="Skill vs base rate by week (> 0 is better)", zero=True, x=[str(x)[:10] for x in w.week],
                             series=[dict(name="weekly", y=w.skill.round(4).tolist()), dict(name="rolling 4 weeks", y=w.rolling_skill.round(4).tolist(), width=2.4)]))
            secs.append(table(None, w.assign(week=w.week.astype(str).str[:10]).round(4), tools=False))
    elif view == "buckets":
        d = SC.deciles(v)
        secs.append(dict(kind="note", text="Needs at least 20 scored forecasts.") if d.empty else
                    table("By probability bucket (1 = lowest predicted P): returns should rise left to right if the model works", d.round(3), {"mean_return_pct": "snum2"}, tools=False))
    elif view == "verdicts":
        secs.append(table("What happened after each verdict", SC.verdicts(v).round(4), tools=False))
    elif view == "breakdowns":
        for by, lab in (("sector", "By sector"), ("regime", "By market regime"), ("symbol", "By stock")):
            if by in v and v[by].notna().any():
                secs.append(table(lab, SC._group(v, by).sort_values("forecasts", ascending=False).round(4)))
    elif view == "misses":
        secs.append(table("Wrong forecasts, most confident first", SC.misses(v).round(3), empty="No misses yet."))
    elif view == "h2h":
        hh = SC.head_to_head(o if mode == "both" else o[o["mode"] == mode])
        secs.append(dict(kind="note", text="Needs debate analyses on the same stock, day and question as the nightly batch.") if hh.empty else table(None, hh.round(4)))
    return dict(status=status, filters=filters, mode=mode, sections=secs)


# ---------------- Macro ----------------
MOUT = C.RESULTS / "macro"
FACTOR_ORDER = ["Brent crude", "USD/INR", "US 10-year yield", "US dollar index", "US VIX", "Geopolitical risk", "FII net buying"]


def _mread(name):
    p = MOUT / f"{name}.csv"
    try:
        return pd.read_csv(p) if p.exists() and p.stat().st_size > 2 else pd.DataFrame()
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


@route("GET", "macro")
def macro(p):
    from core import macro as M
    tab = p.get("tab", "indicators")
    lf = M.last_fetch()
    status = None
    if lf:
        bad = [k for k, v in lf["status"].items() if not str(v).startswith(("ok", "skipped"))]
        status = f"Last fetch {lf['when'][:16]} · " + (f"not working: {', '.join(bad[:6])}" if bad else "all sources ok")
    if tab == "indicators":
        rows = M.summary()
        if not rows:
            return dict(status=status, sections=[dict(kind="note", text="No macro data yet. Press Fetch macro data.")])
        items = []
        for r in rows:
            if r["change_kind"] == "total":
                delta = None if r["chg_5d"] is None else f"₹{r['chg_5d']:+,.0f} cr over 5 days"
            else:
                unit = "pts" if r["change_kind"] == "points" else "%"
                delta = (f"{r['chg_20d']:+.2f} {unit} in a month" if r["chg_20d"] is not None else None)
            items.append(dict(label=r["label"] + (" ⚠ unusual" if r["z_1y"] is not None and abs(r["z_1y"]) >= 2 else ""), value=f"{r['value']:,.2f}" + (" %" if r["unit"] == "%" else ""), delta=delta))
        w = M.wide()
        names = {r["series"]: r["label"] for r in rows}
        pick = [s for s in (p.get("series") or "DCOILBRENTEU,DEXINUS,DGS10").split(",") if s in names and s in w]
        yrs = int(p.get("years", 3))
        rebase = p.get("rebase", "true") == "true" and len(pick) > 1
        secs = [dict(kind="kpis", cols=4, items=items)]
        if pick:
            v = w[pick][w.index >= w.index.max() - pd.DateOffset(years=yrs)].dropna(how="all")
            series = []
            for s_ in pick:
                x = v[s_].ffill()
                if rebase and x.notna().any():
                    x = 100 * x / x.dropna().iat[0]
                series.append(dict(name=names[s_], y=[None if pd.isna(a) else round(float(a), 3) for a in x.values]))
            secs.append(dict(kind="chart", type="line", title="Rebased to 100" if rebase else "", x=[d.strftime("%Y-%m-%d") for d in v.index], series=series,
                             note="US data is dated by the US day: its effect shows up in the next Indian session."))
        return dict(status=status, series=[[k, v] for k, v in names.items()], sections=secs)
    if tab == "geo":
        w = M.wide()
        secs = []
        if "GPRD" in w:
            g = w.GPRD.dropna()
            g = g[g.index >= g.index.max() - pd.DateOffset(years=3)]
            secs.append(dict(kind="chart", type="line", title="Geopolitical Risk index (100 = 1985–2019 average)", x=[d.strftime("%Y-%m-%d") for d in g.index],
                             series=[dict(name="daily", y=g.round(1).tolist(), width=1, color="#a8b0bf"), dict(name="30-day average", y=[None if pd.isna(a) else round(a, 1) for a in g.rolling(30).mean()], color="#dc2626", width=2)]))
        labels = {k: v[0] for k, v in M.THEMES.items()}
        vol = [c for c in w.columns if c.startswith("GDELT_") and c.endswith("_vol")]
        if vol:
            v = w[vol].dropna(how="all")
            secs.append(dict(kind="chart", type="line", title="News themes worldwide (GDELT: articles per day, 3-day average)", x=[d.strftime("%Y-%m-%d") for d in v.index],
                             series=[dict(name=labels.get(c[6:-4], c[6:-4]), y=[None if pd.isna(a) else round(a, 0) for a in v[c].rolling(3, min_periods=1).mean()]) for c in vol[:6]]))
        h = M.headline_themes()
        if len(h):
            days = int(p.get("days", 30))
            hh = h[h.published >= pd.Timestamp.now() - pd.Timedelta(days=days)].copy()
            hh["theme"] = hh.theme.map(lambda t: labels.get(t, t))
            secs.append(table(f"Your stored headlines by theme (last {days} days)", hh.sort_values("published", ascending=False)[["published", "theme", "title", "source", "link"]],
                              {"published": "dt", "title": "long", "link": "link"}, csv="themed_headlines"))
        return dict(status=status, sections=secs or [dict(kind="note", text="Nothing fetched yet.")])
    if tab == "sensitivity":
        s_ = _mread("sens_3y" if p.get("window", "3y") == "3y" else "sens_all")
        if s_.empty:
            return dict(status=status, sections=[dict(kind="note", text="Press Run macro study after fetching macro data.")])
        facs = [f for f in FACTOR_ORDER if f in set(s_.factor)]
        pv = s_.pivot_table(index="sector", columns="factor", values="per_1sd_pct").reindex(columns=facs).round(2).reset_index()
        secs = [dict(kind="table", title="Extra weekly move of each sector (%) when the factor moves by a typical amount", rows=pv, tools=False,
                     columns=[["sector", "Sector"]] + [[f, f, "snum2"] for f in facs],
                     note="Equal-weight sector, beyond what the Nifty explains, per 1 standard deviation of the factor's weekly change. Example: −1.5 under Brent means the sector tends to fall 1.5% more than the market in a week crude jumps by a typical amount.")]
        st_ = _mread("sens_stocks_3y")
        if len(st_):
            secs.append(table("Most sensitive stocks (last 3 years)", st_, {"per_1sd_pct": "snum2", "t": "num1"}, csv="sensitive_stocks"))
        return dict(status=status, sections=secs)
    if tab == "shocks":
        sh = _mread("shocks")
        if sh.empty:
            return dict(status=status, sections=[dict(kind="note", text="Press Run macro study after fetching macro data.")])
        secs = [table("Days when a factor moved more than 2 standard deviations, and what the Nifty did", sh,
                      {"nifty_day": "spct2", "nifty_5d": "spct2", "nifty_20d": "spct2", "t_day": "num1", "t_5d": "num1", "t_20d": "num1", "share_down_day": "num0"}, tools=False)]
        d = _mread("shock_sectors")
        if len(d):
            secs.append(table("Sectors on the reaction day (% vs Nifty)", d.sort_values(["factor", "direction", "mean_vs_nifty"]), {"mean_vs_nifty": "snum2", "t": "num1"}, csv="shock_sectors"))
        return dict(status=status, sections=secs)
    if tab == "india":
        d = M.india_table()
        if d.empty:
            d = pd.DataFrame({"indicator": M.INDIA_DEFAULT_INDICATORS, "period": "", "value": np.nan, "unit": "", "released": "", "source": "", "note": ""})
        return dict(status=status, rows=d.fillna("").astype(str).to_dict("records"), columns=list(d.columns))
    if tab == "calendar":
        d = M.calendar()
        cols = M.CAL_COLS
        rows = d.assign(date=pd.to_datetime(d.date).dt.strftime("%Y-%m-%d")).fillna("").astype(str).to_dict("records") if len(d) else []
        return dict(status=status, rows=rows, columns=cols)
    raise UserError("unknown tab")


@route("POST", "macro/india")
def macro_india(_, body):
    from core import macro as M
    d = pd.DataFrame(body.get("rows") or [])
    if len(d):
        d["value"] = pd.to_numeric(d.value, errors="coerce")
        d = d[d.value.notna()]
    M.save_india(d)
    return dict(saved=len(d))


@route("POST", "macro/calendar")
def macro_calendar(_, body):
    from core import macro as M
    d = pd.DataFrame(body.get("rows") or [], columns=M.CAL_COLS).replace("", np.nan).dropna(how="all")
    if len(d):
        d["date"] = pd.to_datetime(d.date, errors="coerce")
        if d.date.isna().any():
            raise UserError("every event needs a date (YYYY-MM-DD)")
    M.save_calendar(d)
    return dict(saved=len(d))


# ---------------- Market report ----------------
@route("GET", "report")
def report(p):
    from research import report_writer as W
    from core import llm
    days = W.saved_reports()
    ok, msg = llm.status("report")
    px = prices()
    last = None if px is None else str(pd.Timestamp(px.date.max()).date())
    pick = p.get("day") or (days[0] if days else None)
    base = dict(days=[str(d) for d in days], last=last, model_ok=ok, model=msg, day=pick)
    if not pick:
        return dict(**base, sections=[dict(kind="note", text="No report yet. Press Write report.")])
    rec = W.load_report(pick)
    f, rep = rec["facts"], rec["report"]
    m = f["market"]
    sec = dict(rep["sections"])
    S = []
    S.append(dict(kind="kpis", items=[dict(label="Nifty 50", value=f"{m['nifty_close']:,.0f}", delta=f"{m['nifty_change_pct']:+.2f}%", tone="up" if m["nifty_change_pct"] > 0 else "down"),
                                      dict(label="India VIX", value=str(m["vix"]), delta=f"{m['vix_change']:+.2f}" if m["vix_change"] is not None else None),
                                      dict(label="Advances / declines", value=f"{m['advances']} / {m['declines']}"),
                                      dict(label="Up >2% / down >2%", value=f"{m['up_2pct']} / {m['down_2pct']}"),
                                      dict(label="52-week highs / lows", value=f"{m['new_52w_highs']} / {m['new_52w_lows']}"),
                                      dict(label="Above 200-day avg", value=f"{m['above_200dma_pct']:.0f}%")]))
    S.append(dict(kind="md", title="Summary", text=rep["summary"]))
    S.append(dict(kind="md", title="Market overview", text=sec["Market overview"]))
    h = pd.DataFrame(m["last_5_days"])
    if len(h):
        S.append(dict(kind="chart", type="bars", title="Advances and declines, last 5 days", height=220, x=h.date.astype(str).tolist(),
                      series=[dict(name="Advances", y=h.advances.astype(float).tolist()), dict(name="Declines", y=(-h.declines).astype(float).tolist())],
                      line=dict(name="Nifty %", y=h.nifty_pct.round(2).tolist())))
    mcols = [["symbol", "Stock", "symonly"], ["sector", "Sector"], ["move_pct", "Move", "spct2"], ["vs_nifty_pct", "vs Nifty", "spct2"], ["volume_x_normal", "Volume ×", "x1"],
             ["traded_cr", "Traded ₹ cr", "num0"], ["month_pct", "1 month", "spct1"], ["news_text", "Filing / headline that day", "long"]]
    for title, key in (("What went up", "up"), ("What went down", "down")):
        S.append(dict(kind="md", title=title, text=sec[title]))
        d = pd.DataFrame(f[key])
        if len(d):
            d["news_text"] = d.news.map(lambda ns: " | ".join(f"{n['kind']}: {n['text']}" for n in ns))
            S.append(dict(kind="table", rows=d, columns=mcols, tools=False))
    S.append(dict(kind="md", title="Sectors", text=sec["Sectors"]))
    s = pd.DataFrame(f["sectors"]["table"])
    if len(s):
        s2 = s.sort_values("median_pct", ascending=False)
        S.append(dict(kind="chart", type="hbar", title="Median stock move by sector today", labels=s2.sector.tolist(), values=s2.median_pct.round(2).tolist()))
        S.append(table(None, s, {"median_pct": "spct2", "weighted_pct": "spct2", "share_up_pct": "num0", "median_5d_pct": "spct2", "median_20d_pct": "spct2", "traded_vs_20d": "x1"}, tools=False))
    S.append(dict(kind="md", title="New players", text=sec["New players"]))
    n = f["new"]
    for key, lab in (("leaders", "New leaders"), ("index_changes", "Nifty 500 changes"), ("unusual_volume", "Unusual volume"), ("recent_listings", "Recent listings"), ("ipo_headlines", "IPO headlines")):
        d = pd.DataFrame(n[key])
        if len(d):
            if "news" in d:
                d["news"] = d.news.map(lambda ns: " | ".join(x["text"] for x in ns))
            S.append(table(f"{lab} ({len(d)})", d, {"move_pct": "spct2", "since_first_close_pct": "spct2", "volume_x_normal": "x1", "news": "long"}, tools=False))
    if "Macro and geopolitics" in sec:
        S.append(dict(kind="md", title="Macro and geopolitics", text=sec["Macro and geopolitics"]))
        mc = f.get("macro") or {}
        if mc.get("indicators"):
            S.append(table(None, pd.DataFrame(mc["indicators"])[["label", "date", "value", "unit", "chg_1d", "chg_5d", "chg_20d", "z_1y"]], {"z_1y": "snum1"}, tools=False))
    S.append(dict(kind="md", title="Filings and headlines", text=sec["Filings and headlines"]))
    fl = f["filings"]
    S.append(table(f"Notable NSE filings ({fl['count']} filings that day)", pd.DataFrame(fl["notable"]), {"move_pct": "spct2"}, tools=False, empty="None for liquid stocks."))
    if f["headlines"]:
        S.append(dict(kind="md", title="Market headlines", text="\n".join(f"- {x['text']} · {x['source']}" + (f" ({x['sources']} sources)" if x["sources"] > 1 else "") for x in f["headlines"][:10])))
    w = rep["writer"]
    tmpl = [k for k, v in w["used"].items() if v != "model"]
    S.append(dict(kind="note", text=f"Written {'by ' + w['model'] if w['model'] else 'with the template'} in {w['seconds']} s. "
                  + (f"Template text used for: {', '.join(tmpl)}. " if w["model"] and tmpl else "")
                  + "Figures come from stored prices, NSE filings and headlines; 'alongside' news is not a claim of cause. Not investment advice."))
    mdp = W.OUT / f"{pick}.md"
    return dict(**base, markdown=mdp.read_text() if mdp.exists() else None, sections=S)


# ---------------- News research ----------------
NOUT = C.RESULTS / "news_events"


@route("GET", "news-research")
def news_research(_):
    def rd(name):
        p_ = NOUT / f"{name}.csv"
        try:
            return pd.read_csv(p_) if p_.exists() and p_.stat().st_size > 2 else pd.DataFrame()
        except pd.errors.EmptyDataError:
            return pd.DataFrame()
    mp = NOUT / "meta.json"
    meta = json.loads(mp.read_text()) if mp.exists() else None
    if not meta:
        return dict(status=None, sections=[dict(kind="note", text="No results yet. Press Run the news study. The more history is stored (Data health → history downloads), the more the answer means.")])
    cov = meta.get("coverage", {})
    status = (f"Last run {meta['when'][:16]} · {cov.get('events', 0):,} events on {cov.get('stocks', 0)} stocks · {cov.get('first', '?')} to {cov.get('last', '?')} · "
              f"{cov.get('news', 0):,} news, {cov.get('announcements', 0):,} filings")
    secs = []
    if cov.get("events", 0) < 500:
        secs.append(dict(kind="note", tone="warn", text=f"Only {cov.get('events', 0)} events so far: too few for conclusions. Download older news and filings first."))
    s = rd("summary")
    if len(s):
        r = s.iloc[0]
        secs.append(dict(kind="kpis", items=[dict(label="Avg move on event days", value=f"{r.abs_move_event_day:.2f}%"), dict(label="Avg move on quiet days", value=f"{r.abs_move_quiet_day:.2f}%"),
                                             dict(label="Ratio", value=f"{r.times_bigger:.1f}×"), dict(label="Moves over 3%", value=f"{r.pct_moves_over_3:.0f}% vs {r.pct_quiet_over_3:.0f}%")]))
        bm = meta.get("big_moves", {})
        if bm.get("big_moves"):
            secs.append(dict(kind="note", text=f"Of {bm['big_moves']:,} stock-days with a move over {bm['threshold']:.0f}% vs the Nifty, {bm['pct_with_event']:.0f}% had news or a filing that day or the day before."))
    keep = ["group", "events", "stocks", "abs_move_event_day", "times_bigger", "event_day_mean", "before_5d_mean", "after_1_5d_mean", "after_1_5d_t", "after_1_20d_mean", "after_1_20d_t", "abn_volume_median"]
    fmt = {"abs_move_event_day": "num2", "times_bigger": "x1", "event_day_mean": "spct2", "before_5d_mean": "spct2", "after_1_5d_mean": "spct2", "after_1_5d_t": "num1",
           "after_1_20d_mean": "spct2", "after_1_20d_t": "num1", "abn_volume_median": "x1"}
    for name, title in (("by_source", "News vs NSE filings"), ("by_category", "By type of event"), ("by_sector", "By sector")):
        d = rd(name)
        secs.append(table(title, d[[k for k in keep if k in d]] if len(d) else d, fmt, tools=False, empty="Not enough events per group yet (20 needed)."))
    t = rd("by_tone")
    if len(t):
        secs.append(table("Does the direction of the headline predict what follows?", t, {"mean_abnormal_pct": "spct2", "t": "num1"}, tools=False,
                          note="Tone comes from explicit direction phrases in the headline. A continuing move on days 1–20 is the tradable part."))
    return dict(status=status, sections=secs)
