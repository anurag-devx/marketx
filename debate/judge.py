"""Judge: turns the evidence ledger and the probability engine's output into a verdict, or abstains.

The judge never invents a number. Probabilities come from debate/probability.py; returns and downside from its
out-of-sample history and from the stock's own past h-session returns. The judge's own logic is rules (below); a
language model may only word the explanation, and every number it writes is checked against the verdict facts.

Verdicts: Bullish bias / Bearish bias / Neutral or mixed / Insufficient evidence / Forecast unavailable.
  Forecast unavailable   price data missing, quarantined, too little history, untraded recently
  Insufficient evidence  the probability model has no demonstrated out-of-sample skill for this question, or a
                         material news item is unverified, or there are no supported claims at all
  Neutral or mixed       model edge vs the base rate below EDGE_MIN, or the edge and the supported evidence disagree
  Bullish / Bearish bias the model has skill, its edge clears EDGE_MIN, and the supported evidence does not disagree
Correlated evidence: signals are grouped (trend, breakout, mean reversion, ...); each group counts once per side
(1 if any claim in it is supported, 0.5 if only partially supported); news/filing tests form one 'events' group."""
import numpy as np, pandas as pd
from . import VERSIONS, stats as S
from .signals import GROUPS

EDGE_MIN = 0.02
MIN_HISTORY = 260


def gate(info, last, items, ctx):
    reasons = []
    if last is None:
        return ["no price data on or before the analysis day"]
    if pd.Timestamp(last.date) < ctx.as_of:
        reasons.append(f"no bar on the analysis day (last bar {pd.Timestamp(last.date).date()})")
    q = next((e for e in items if e["id"] == "PX-REF"), {}).get("quality")
    if q == "quarantined":
        reasons.append("price data quarantined by the data-quality checks (Data health)")
    if last.n_hist < MIN_HISTORY:
        reasons.append(f"only {int(last.n_hist)} sessions of history (needs {MIN_HISTORY})")
    r = ctx.rows(info["symbol"])
    r = r[r.date <= ctx.as_of].tail(5)
    if len(r) and (r.volume <= 0).any():
        reasons.append("no trading volume in at least one of the last 5 sessions (suspension or circuit?)")
    return reasons


def evidence_score(ledger):
    """Net supported evidence by correlation group: {group: {'bull': w, 'bear': w}}."""
    g = {}
    for row in ledger:
        st = row.get("status")
        w = 1.0 if st == S.SUPPORTED else 0.5 if st == S.PARTIAL else 0.0
        if not w or row.get("side") not in ("bull", "bear"):
            continue
        grp = row.get("group") or "events"
        g.setdefault(grp, {"bull": 0.0, "bear": 0.0})
        g[grp][row["side"]] = max(g[grp][row["side"]], w)
    net = sum(v["bull"] - v["bear"] for v in g.values())
    return g, net


def downside(ctx, symbol, h):
    r = ctx.rows(symbol)
    r = r[(r.end_date <= ctx.cutoff) & (r.date >= ctx.as_of - pd.Timedelta(days=3 * 365))]
    f = 100 * r.fwd.dropna()
    if len(f) < 100:
        return None
    return dict(n=int(len(f)), p05=float(f.quantile(0.05)), p10=float(f.quantile(0.10)), p50=float(f.median()), p90=float(f.quantile(0.90)),
                share_below_minus5=float((f < -5).mean()), note=f"this stock's own {h}-session returns over the last 3 years (overlapping windows)")


def decide(spec, info, last, items, ledger, model, fc, ctx, unverified):
    """Returns the verdict dict (all numbers from code)."""
    reasons = gate(info, last, items, ctx)
    groups, net = evidence_score(ledger)
    cal = model.get("calibration") if model else "Not evaluated"
    skill = cal == "Calibrated, with measurable skill"
    probs = (fc or {}).get("probs") or {}
    base = (fc or {}).get("base") or {}
    p_up, b_up = probs.get("up"), base.get("up")
    edge = None if p_up is None or b_up is None else p_up - b_up
    why = []
    if reasons:
        verdict = "Forecast unavailable"
        why = reasons
    elif not skill:
        verdict = "Insufficient evidence"
        why.append(f"probability model: {cal} ({model.get('calibration_why', '') if model else ''})")
    elif unverified:
        verdict = "Insufficient evidence"
        why.append("material news not confirmed by an NSE filing: " + ", ".join(unverified[:3]))
    elif edge is None or abs(edge) < EDGE_MIN:
        verdict = "Neutral or mixed"
        why.append(f"model edge vs the base rate is {0 if edge is None else edge:+.3f}, below {EDGE_MIN}")
    elif (edge > 0 and net < 0) or (edge < 0 and net > 0):
        verdict = "Neutral or mixed"
        why.append(f"model edge {edge:+.3f} disagrees with the supported evidence (net {net:+.1f} groups)")
    else:
        verdict = "Bullish bias" if edge > 0 else "Bearish bias"
        why.append(f"model edge {edge:+.3f} over the base rate with measurable out-of-sample skill; supported evidence net {net:+.1f} groups")
    if not reasons and not any(r.get("status") in (S.SUPPORTED, S.PARTIAL) for r in ledger):
        why.append("no claim from either side is supported by historical evidence")
    bucket = (fc or {}).get("bucket")
    exp = None
    if bucket:
        exp = dict(mean=bucket["mean"], ci_lo=bucket["ci_lo"], ci_hi=bucket["ci_hi"], median=bucket["median"], p10=bucket["p10"],
                   p90=bucket["p90"], n=bucket["n"], usable=skill,
                   note=("average outcome of past out-of-sample predictions in the same probability decile" +
                         ("" if skill else " — shown for reference only: the model has no demonstrated skill")))
    label = ("calibrated out of sample" if skill else "experimental: no demonstrated out-of-sample skill" if cal.startswith("Calibrated")
             else "experimental: not calibrated" if "Not calibrated" in cal else "experimental: not evaluated")
    return dict(verdict=verdict, reasons=why, probabilities=probs, base_rates=base, edge=edge, probability_label=label,
                calibration=cal, calibration_detail=model.get("calibration_why") if model else None, expected_return=exp,
                downside=downside(ctx, info["symbol"], spec["h"]), evidence_groups={GROUPS.get(k, "News / filings (event studies)"): v for k, v in groups.items()},
                net_supported_groups=net, top_drivers=(fc or {}).get("top_drivers", []), version=VERSIONS["judge"],
                note="A probability is a forecast, not a guarantee. Bias verdicts describe the balance of tested evidence, not trade advice.")


def invalidation_levels(last):
    """Price levels from the indicator panel (code, not model)."""
    if last is None:
        return {}
    return dict(bull_invalid_below=float(min(last.sma50, last.ll55)) if np.isfinite(last.sma50) and np.isfinite(last.ll55) else None,
                bear_invalid_above=float(max(last.sma50, last.hh55)) if np.isfinite(last.sma50) and np.isfinite(last.hh55) else None,
                atr=float(last.atr14) if np.isfinite(last.atr14) else None)


def explain(v, spec, info, bull_rev, bear_rev, ledger, use_llm=True, log=print):
    """Short explanation. Model wording is accepted only if every number it uses is in the verdict facts."""
    sup = [r for r in ledger if r.get("status") in (S.SUPPORTED, S.PARTIAL)]
    opp = [r for r in ledger if r.get("status") in (S.NOT_SUPPORTED, S.CONTRA)]
    p = v["probabilities"]
    facts = dict(verdict=v["verdict"], reasons=v["reasons"], p_up=None if p.get("up") is None else round(100 * p["up"], 1),
                 base_up=None if v["base_rates"].get("up") is None else round(100 * v["base_rates"]["up"], 1), calibration=v["calibration"],
                 supported=[f"{r['claim_id']}: {r['claim'][:120]} ({r['status']})" for r in sup[:8]],
                 rejected=[f"{r['claim_id']}: {r['claim'][:120]} ({r['status']})" for r in opp[:8]])
    tmpl = (f"Verdict: {v['verdict']}. " + " ".join(r[0].upper() + r[1:] + "." for r in v["reasons"]) +
            (f" Model probability of the outcome: {facts['p_up']}% vs a historical base rate of {facts['base_up']}% ({v['probability_label']})." if facts["p_up"] is not None else "") +
            f" Claims supported or partly supported by history: {len(sup)}; not supported or contradicted: {len(opp)}.")
    if not use_llm:
        return tmpl, "template"
    try:
        from core import llm
        from research.report_writer import check_numbers
        ok, _ = llm.status("debate")
        if not ok:
            return tmpl, "template"
        import json
        user = (f"Forecast question: {spec['definition']}\nStock: {info['symbol']}\nVERDICT FACTS (computed by code, do not change them):\n"
                f"{json.dumps(facts, default=str)}\nBull revised thesis: {bull_rev['thesis']}\nBear revised thesis: {bear_rev['thesis']}\n\n"
                "Write 4-6 plain sentences explaining the verdict for an investor. Use only these facts; cite claim IDs in brackets; "
                "do not add numbers that are not in the facts; do not give trading instructions.")
        txt = llm.chat("debate", [{"role": "user", "content": user}], system="You are the judge of an equity research debate. Be precise and neutral.",
                       max_tokens=600, temperature=0.1)
        bad = check_numbers(txt, json.dumps(facts, default=str) + spec["definition"], facts)
        if bad or not txt.strip():
            log(f"  judge explanation used numbers not in the facts ({', '.join(bad[:4])}); using the template")
            return tmpl, "template (model numbers failed the check)"
        return txt.strip(), "model"
    except Exception as e:
        log(f"  judge explanation model call failed ({type(e).__name__}); using the template")
        return tmpl, "template"
