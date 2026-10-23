"""Debate orchestration for one stock and one forecast question.

Round 1  independent theses      bull and bear see the same evidence and cutoff, never each other's work
Round 2  cross-examination       each critiques the other (model objections + code objections); repeats up to
                                 `rounds` times and stops early when a round adds no new objection
Round 3  statistical challenge   every claim mapped to tests (signals, event studies, macro associations); the
                                 statistical agent also tests every firing signal nobody cited, and flags correlated
                                 or duplicated evidence and certainty language
Round 4  revised theses          each side concedes or keeps claims given the statistics and objections
Round 5  evidence ledger         one row per claim with sources, timestamps, tests and final status
Then the probability engine (debate/probability.py) and the judge (debate/judge.py). The record is saved once.

Run from the dashboard (Market Debate Lab → Run analysis) or:
  python3 -m debate.engine sym=RELIANCE h=5 target=abs [band=0.5] [date=2026-10-22] [rounds=1] [--no-llm]"""
import json, sys, time
import numpy as np, pandas as pd
from core import config as C, atomic
from . import VERSIONS, signals as SG, stats as S, probability as PR, agents as AG, judge as J, store, evidence as E

CACHE = C.DATA / "debate" / "cache"
TARGETS = {"abs": "price return above 0%", "rel": "return vs the Nifty 50 above 0", "three": "three outcomes (up / neutral / down)"}
RANK = {S.SUPPORTED: 0, S.PARTIAL: 1, S.INSUFF: 2, S.NOT_SUPPORTED: 3, S.CONTRA: 4}


def spec_for(symbol, h, target, band, as_of, ref_price=None):
    as_of = pd.Timestamp(as_of).normalize()
    if target == "abs":
        d = (f"Probability that {symbol}'s price return R over the next {h} trading session(s) is above 0%, where R = P(t+{h}) / P(t) - 1, "
             f"P(t) = adjusted close on {as_of.date()} and P(t+{h}) = adjusted close {h} sessions later (NSE sessions; dividends not included).")
    elif target == "rel":
        d = (f"Probability that {symbol}'s {h}-session price return minus the Nifty 50's return over the same sessions is above 0 "
             f"(from the close of {as_of.date()}; benchmark-relative, not absolute).")
    elif target == "three":
        b = band if band is not None else 0.5
        d = (f"Probabilities of three mutually exclusive outcomes for {symbol}'s {h}-session price return R from the close of {as_of.date()}: "
             f"up (R > +{b}%), neutral (-{b}% <= R <= +{b}%), down (R < -{b}%). They sum to 100%.")
        band = b
    else:
        raise ValueError("target must be abs, rel or three")
    return dict(symbol=symbol, exchange="NSE", h=int(h), target=target, band=band, as_of=str(as_of.date()), cutoff=E.ist(E.cutoff_ts(as_of)),
                definition=d, reference_price=ref_price, reference_time=E.ist(E.cutoff_ts(as_of)), return_definition="R = P(t+h)/P(t) - 1",
                adjustment="split/bonus-adjusted closes (corporate actions applied); price return, dividends excluded")


def _regime(last):
    up = last.nifty_vs_200 > 0
    calm = last.vix_rel <= 1 if np.isfinite(last.vix_rel) else False
    return ("up_" if up else "down_") + ("calm" if calm else "volatile")


def _regime_mask(panel, label):
    up = panel.nifty_vs_200 > 0
    calm = panel.vix_rel <= 1
    lab = np.where(up, "up_", "down_").astype(object) + np.where(calm, "calm", "volatile")
    return pd.Series(lab == label, index=panel.index)


def universe_tests(ctx, spec, regime, log):
    key = f"{spec['as_of']}_{spec['h']}_{spec['target']}_{spec['band']}_{regime}_{VERSIONS['stats']}_{VERSIONS['signals']}_{getattr(ctx, 'stamp', '')}"
    f = CACHE / f"signals_{key}.json"
    if f.exists():
        return json.loads(f.read_text())
    log("Statistical agent: testing every pre-registered signal on the whole liquid universe...")
    res = S.signal_tests(ctx.panel, ctx.sig, ctx.as_of, spec["h"], spec["target"], spec["band"], regime_mask=_regime_mask(ctx.panel, regime))
    res = S.finalize(res, {k: SG.BY_ID[k]["side"] for k in res})
    atomic.write_text(f, json.dumps(PR._clean(res), default=float))
    return json.loads(f.read_text())


def event_tests(ctx, spec, items, log):
    """Event studies for the kinds of news / filings present in this stock's evidence."""
    kinds = set()
    for e in items:
        x = e.get("extra") or {}
        if e["id"].startswith("NW-"):
            kinds.add(("news", x.get("event_type"), x.get("tone")))
        elif e["id"].startswith("AN-"):
            kinds.add(("announcement", x.get("category"), x.get("tone")))
    if not kinds:
        return {}, None
    log(f"Statistical agent: event studies for {len(kinds)} kind(s) of news / filings...")
    ev = S.event_sample(ctx.news, ctx.ann, ctx.P, ctx.as_of, spec["h"])
    if len(ev):
        ev["car"], ev["ar0"] = S.car(ev, ctx.P, spec["h"])
    out = {}
    for st, cat, tone in kinds:
        sub = ev[(ev.source_type == st) & (ev.category == cat)] if len(ev) else ev
        if cat == "Other" and len(sub):
            sub = sub[sub.tone == tone]
        for side in ("bull", "bear"):
            k = f"{st}|{cat}|{tone if cat == 'Other' else '*'}|{side}"
            r = S.event_test(sub, side, spec["h"])
            if "status" not in r:
                r["status"], r["why"] = S.classify(r, side)
            out[k] = r
    return out, dict(n_events=int(len(ev)), first=str(ev.d0.min().date()) if len(ev) else None, last=str(ev.d0.max().date()) if len(ev) else None)


def _event_key(e, side):
    x = e.get("extra") or {}
    if e["id"].startswith("NW-"):
        cat = x.get("event_type")
        return f"news|{cat}|{x.get('tone') if cat == 'Other' else '*'}|{side}"
    return f"announcement|{x.get('category')}|{x.get('tone') if x.get('category') == 'Other' else '*'}|{side}"


def _summ(r, h):
    if not r:
        return ""
    d, v = r.get("design") or {}, r.get("validation") or {}
    def f(x):
        return "n/a" if x is None or not np.isfinite(x) else f"{x:+.2f}"
    return (f"design {f(d.get('effect'))} pp (n {d.get('eff_n', 0)}), validation {f(v.get('effect'))} pp (n {v.get('eff_n', 0)}) "
            f"over {h} sessions")


def claim_tests(claim, side, by_id, sig_res, stock_res, ev_res, h):
    """Status of one claim from the tests behind its cited evidence (weakest link wins: conservative)."""
    tests = []
    for eid in claim["evidence_ids"]:
        e = by_id.get(eid, {})
        if eid.startswith("SG-"):
            k = eid[3:]
            r = sig_res.get(k, {})
            st = r.get("status", S.INSUFF)
            if SG.BY_ID[k]["side"] != side:
                st, why = S.INVALID, f"{k} is a {SG.BY_ID[k]['side']} signal by its pre-registered definition, cited for the {side} case"
            else:
                why = r.get("why", "")
            tests.append(dict(evidence_id=eid, kind="signal", status=st, why=why, summary=_summ(r, h), group=SG.BY_ID[k]["group"],
                              q_design=r.get("q_design"), stock_only=stock_res.get(k), economic=S.economic(r, h) if r else None))
        elif eid.startswith(("NW-", "AN-")):
            r = ev_res.get(_event_key(e, side), {})
            tests.append(dict(evidence_id=eid, kind="event study", status=r.get("status", S.INSUFF), why=r.get("why", ""),
                              summary=_summ(r, h) + (f"; {r.get('n_events', 0)} past events" if r else ""), group="events"))
        elif eid.startswith("MC-"):
            x = e.get("extra") or {}
            r = S.macro_test({"t": x.get("t", np.nan) if x.get("t") is not None else np.nan, "per_1sd_pct": x.get("per_1sd_pct", np.nan) or np.nan,
                              "weeks": x.get("weeks")}, side)
            if r["status"] == S.PARTIAL and e.get("direction") not in (side,):
                r["status"], r["why"] = S.NOT_SUPPORTED, "the factor's recent move points the other way for this stock (or has no recent move)"
            tests.append(dict(evidence_id=eid, kind="macro association", status=r["status"], why=r["why"], summary=f"t = {x.get('t')}", group="macro"))
    testable = [t for t in tests if t["status"] != S.QUAL]
    if not testable:
        return S.QUAL, "descriptive fact or interpretation; no pre-registered test applies", tests, None
    worst = max(testable, key=lambda t: RANK.get(t["status"], 2) if t["status"] != S.INVALID else 9)
    return worst["status"], worst["why"], tests, worst.get("group")


def run(symbol, h=5, target="abs", band=None, as_of=None, rounds=1, use_llm=True, log=print, save=True, data=None):
    t0 = time.time()
    rounds = int(max(1, min(3, rounds)))
    store.progress("Loading data", 3)
    ctx = E.Context(h, as_of, log=log, data=data)
    items, last, info = E.gather(ctx, symbol)
    if last is None:
        raise ValueError(info.get("error", "no data"))
    spec = spec_for(symbol, h, target, band, ctx.as_of, float(last.close))
    created = E.now_ist()
    aid = store.safe_id(f"{symbol}-{ctx.as_of:%Y%m%d}-h{h}-{target}-{pd.Timestamp.now():%Y%m%d%H%M%S}")
    store.progress("Evidence gathered", 10, f"{len(items)} evidence items", aid)
    by_id = {e["id"]: e for e in items}
    look = [e["id"] for e in items if (e.get("published") and e["published"] > spec["cutoff"]) or (e.get("observed") and e["observed"] > spec["cutoff"])]

    # round 1: independent theses (separate calls; neither sees the other)
    store.progress("Round 1: bull thesis", 15, analysis=aid)
    log("Round 1: bullish agent (independent)...")
    bull = AG.research("bull", items, spec, info, use_llm, log)
    store.progress("Round 1: bear thesis", 25, analysis=aid)
    log("Round 1: bearish agent (independent)...")
    bear = AG.research("bear", items, spec, info, use_llm, log)

    # round 2: cross-examination
    objections, n = [], 1
    for rd in range(1, rounds + 1):
        store.progress(f"Round 2: cross-examination {rd}/{rounds}", 35 + 5 * rd, analysis=aid)
        log(f"Round 2: cross-examination {rd}/{rounds}...")
        new = AG.cross_examine("bull", bull, bear, items, spec, use_llm, log, start=n, round_no=rd)
        n += len(new)
        new2 = AG.cross_examine("bear", bear, bull, items, spec, use_llm, log, start=n, round_no=rd)
        n += len(new2)
        seen = {(o["claim_id"], o["type"], o["text"]) for o in objections}
        fresh = [o for o in new + new2 if (o["claim_id"], o["type"], o["text"]) not in seen]
        objections += fresh
        if not fresh:
            log("  no new objections; ending cross-examination")
            break

    # round 3: statistical challenge
    store.progress("Round 3: statistical tests", 55, analysis=aid)
    regime = _regime(last)
    sig_res = universe_tests(ctx, spec, regime, log)
    rows = ctx.rows(symbol)
    stock_res = {}
    for e in items:
        if e["id"].startswith("SG-"):
            k = e["id"][3:]
            if k not in SG.MARKET_WIDE:
                stock_res[k] = PR._clean(S.stock_only_test(rows, ctx.sig.loc[rows.index, k].to_numpy(), ctx.as_of, h, target, spec["band"]))
    ev_res, ev_meta = event_tests(ctx, spec, items, log)
    claim_status = {}
    ledger = []
    cited_signals = set()
    for rep in (bull, bear):
        for c in rep["claims"]:
            st, why, tests, grp = claim_tests(c, rep["side"], by_id, sig_res, stock_res, ev_res, h)
            claim_status[c["id"]] = dict(status=st, why=why, summary="; ".join(t["summary"] for t in tests if t.get("summary")))
            cited_signals |= set(c["signal_ids"])
            ledger.append(_ledger_row(c, rep, st, why, tests, grp, by_id, items, objections))
    # the statistical agent's own checks: firing signals nobody cited
    for e in items:
        if e["id"].startswith("SG-") and e["id"][3:] not in cited_signals:
            k = e["id"][3:]
            pseudo = dict(id=f"S-{k}", side=SG.BY_ID[k]["side"], text=f"{SG.BY_ID[k]['name']} ({SG.BY_ID[k]['definition']})", evidence_ids=[e["id"]],
                          signal_ids=[k], flags=["raised by the statistical agent: firing but not cited by either side"])
            st, why, tests, grp = claim_tests(pseudo, pseudo["side"], by_id, sig_res, stock_res, ev_res, h)
            ledger.append(_ledger_row(pseudo, dict(side=pseudo["side"], agent="Statistical agent"), st, why, tests, grp, by_id, items, objections))
    stat_notes = _stat_notes(ledger, look, items, ev_meta)

    # round 4: revised theses
    store.progress("Round 4: revised theses", 70, analysis=aid)
    log("Round 4: revised theses...")
    bull_rev = AG.revise(bull, objections, claim_status, items, spec, use_llm, log)
    bear_rev = AG.revise(bear, objections, claim_status, items, spec, use_llm, log)
    for row in ledger:
        rev = bull_rev if row["side"] == "bull" else bear_rev
        if row["source_agent"] != "Statistical agent":
            row["after_revision"] = "withdrawn" if row["claim_id"] in rev["conceded"] else "kept"

    # probability engine + judge
    store.progress("Probability model", 80, "walk-forward evaluation (cached after the first run of the day)", aid)
    model = PR.build_model(ctx.panel, h, target, spec["band"], ctx.as_of, log=log, stamp=ctx.stamp)
    row = ctx.panel[(ctx.panel.symbol == symbol) & (ctx.panel.date == ctx.as_of)]
    fc = PR.forecast(model, row) if len(row) else None
    unverified = [e["id"] for e in items if e["id"].startswith("NW-") and (e.get("extra") or {}).get("event_type") in E.MATERIAL
                  and "unverified" in str((e.get("extra") or {}).get("verification", ""))
                  and pd.Timestamp(e["published"][:19]) >= ctx.cutoff - pd.Timedelta(days=3)]
    store.progress("Judge", 90, analysis=aid)
    verdict = J.decide(spec, info, last, items, ledger, model, fc, ctx, unverified)
    verdict["invalidation_levels"] = J.invalidation_levels(last)
    verdict["update_when"] = ["the horizon ends (the forecast is then scored)", "a new NSE filing or material news for this stock",
                              "close below the bull invalidation level or above the bear invalidation level",
                              f"the market regime changes (now {regime.replace('_', ', ')})"]
    verdict["catalysts"] = {"bull": bull["catalysts"], "bear": bear["catalysts"]}
    verdict["risks"] = {"bull_case_risks": bear["catalysts"], "invalidation": {"bull": bull["invalidation"], "bear": bear["invalidation"]}}
    text, jw = J.explain(verdict, spec, info, bull_rev, bear_rev, ledger, use_llm, log)
    verdict["explanation"], verdict["explanation_writer"] = text, jw
    latest = ctx.panel.date.max()
    mode = "live" if ctx.as_of >= latest else "retrospective"
    rec = dict(analysis_id=aid, version=1, supersedes=None, created=created, mode=mode,
               mode_note=("made with the latest data available: a genuine forecast" if mode == "live" else
                          f"made on {created[:10]} for a past day ({ctx.as_of.date()}): evidence is cut off at that day, but this is a "
                          "replay, not a forecast made at the time; scored separately from live forecasts"),
               spec=spec, info=info, regime=regime,
               evidence=items, reports=dict(bull=bull, bear=bear), objections=objections, statistics=dict(signals=sig_res, stock_only=stock_res,
               events=ev_res, event_sample=ev_meta, notes=stat_notes), revisions=dict(bull=bull_rev, bear=bear_rev), ledger=ledger,
               model={k: model[k] for k in ("key", "version", "h", "target", "band", "cutoff", "n_train", "calibration", "calibration_why", "metrics",
                                            "buckets", "oos_years", "base")}, forecast=fc, verdict=verdict,
               writers=dict(bull=bull["writer"], bear=bear["writer"], judge=jw, model=bull.get("model")),
               settings=dict(rounds=rounds, use_llm=use_llm), versions=VERSIONS,
               hashes=dict(evidence=E.digest(items), prices=E.digest(rows[rows.date <= ctx.as_of][["date", "close", "volume"]].to_dict("list")),
                           model_file=E.digest(model)),
               reproducibility=("Prices and indicators are recomputed from data/prices.parquet; news and filings come from the stored copies, downloaded "
                                "after publication (items later edited or removed at the source cannot be reconstructed). Language-model text "
                                "can differ between reruns; statistics, probabilities and verdict rules are deterministic."),
               seconds=round(time.time() - t0, 1))
    if save:
        store.save(rec)
    store.progress("Done", 100, f"{verdict['verdict']} in {rec['seconds']:.0f} s", aid)
    log(f"Done: {verdict['verdict']} ({rec['seconds']:.0f} s). Record {aid}")
    return rec


def _ledger_row(c, rep, st, why, tests, grp, by_id, items, objections):
    side = rep["side"]
    other = "bear" if side == "bull" else "bull"
    pub = [by_id[x]["published"] or by_id[x]["observed"] for x in c["evidence_ids"] if x in by_id]
    contra = []
    if grp and grp not in ("events", "macro"):
        contra = [e["id"] for e in items if e["id"].startswith("SG-") and e.get("direction") == other and (e.get("extra") or {}).get("group") == grp]
    objs = [o["id"] for o in objections if o["claim_id"] == c["id"]]
    unc = []
    for t in tests:
        if t.get("stock_only") and not t["stock_only"].get("enough"):
            unc.append(f"stock-only sample small ({t['stock_only'].get('n_signal', 0)} events)")
    return dict(claim_id=c["id"], side=side, source_agent=rep.get("agent"), claim=c["text"], direction="positive" if side == "bull" else "negative",
                evidence_ids=c["evidence_ids"], sources=sorted({by_id[x]["source"] for x in c["evidence_ids"] if x in by_id}),
                timestamps=pub, flags=c.get("flags", []), contradicting=contra, objections=objs, tests=tests, status=st, why=why, group=grp,
                uncertainty="; ".join(unc) or None, after_revision=None)


def _stat_notes(ledger, look, items, ev_meta):
    notes = []
    notes.append("Look-ahead check: " + ("passed — no evidence is timestamped after the cutoff" if not look else
                                         f"FAILED for {', '.join(look)}"))
    by_side_group = {}
    for r in ledger:
        if r["group"]:
            by_side_group.setdefault((r["side"], r["group"]), []).append(r["claim_id"])
    for (side, g), ids in by_side_group.items():
        if len(ids) > 1 and g in SG.GROUPS:
            notes.append(f"Correlated evidence ({side}): {', '.join(ids)} all measure {SG.GROUPS[g].lower()} — counted once by the judge.")
    dup = {}
    for e in items:
        x = e.get("extra") or {}
        if e["id"].startswith("NW-") and x.get("n_sources", 1) > 1:
            dup[e["id"]] = x["n_sources"]
    if dup:
        notes.append("Duplicate coverage: " + ", ".join(f"{k} (same story in {v} sources, counted once)" for k, v in dup.items()))
    cert = [r["claim_id"] for r in ledger if "overstated certainty" in r.get("flags", [])]
    if cert:
        notes.append("Overstated certainty in: " + ", ".join(cert))
    causal = [r["claim_id"] for r in ledger if r["group"] == "macro"]
    if causal:
        notes.append("Macro claims rest on associations (regressions), not demonstrated causes: " + ", ".join(causal))
    if ev_meta:
        notes.append(f"Event studies use {ev_meta['n_events']:,} stored events from {ev_meta['first']} to {ev_meta['last']}; "
                     "news history is short, so most event tests return 'Insufficient data'.")
    notes.append("Universe tests use today's Nifty 500 list for all years (stocks that left the index are missing: survivorship bias).")
    return notes


def _args(argv):
    a = dict(x.split("=", 1) for x in argv if "=" in x)
    flags = {x for x in argv if x.startswith("--")}
    return a, flags


if __name__ == "__main__":
    a, flags = _args(sys.argv[1:])
    sym = a.get("sym", "").replace("__amp__", "&")
    if not sym:
        sys.exit("usage: python3 -m debate.engine sym=SYMBOL h=5 target=abs|rel|three [band=0.5] [date=YYYY-MM-DD] [rounds=1] [--no-llm]")
    band = float(a["band"]) if a.get("band") else None
    rec = run(sym, int(a.get("h", 5)), a.get("target", "abs"), band, a.get("date"), int(a.get("rounds", 1)), use_llm="--no-llm" not in flags)
    print(json.dumps({k: rec["verdict"][k] for k in ("verdict", "probabilities", "base_rates", "calibration")}, default=str, indent=1))
