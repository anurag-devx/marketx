"""TA agent: explains a trade plan in plain English from computed facts only (no chart images, no new numbers).

facts()    levels, legs, recent price action, candle patterns and the Market Lab verdict for one stock and mode
template() a short summary built from the facts (always available, instant)
explain()  the local model words the same facts in 3-5 lines; every number it writes must appear in the facts,
           otherwise the template is used. Uses the 'debate' model setting (Settings → AI models).
ask()      follow-up questions about the same plan (short chat): answers only from the facts; numbers checked the same way
           (numbers in the question itself are allowed); an answer with unknown numbers is withheld, not shown."""
import json, re
import numpy as np, pandas as pd

SYSTEM = ("You are a technical analyst explaining a pre-computed trade plan to an investor. Use ONLY the facts given and never add a number "
          "that is not in them. Write 3-5 short lines, plain English, no headings, no advice to buy or sell:\n"
          "1) what the evidence shows: cite the check ids in brackets, e.g. [E1, E6], and the candle patterns, 2) why the entry, stop and targets sit where they do (name the levels), "
          "3) what would cancel the plan, 4) one caution (e.g. wide stop, conflicting pattern, weak market, untested rules).")


SYSTEM_ASK = ("You are a technical analyst answering an investor's questions about a pre-computed trade plan. Use ONLY the FACTS given. "
              "Cite the evidence check ids in brackets (e.g. [E4, E8]) when you use them. If the facts don't answer the question, say so plainly and say what would be needed. Never add a number that is not in the facts "
              "or the question; do not estimate or project prices. No advice to buy or sell. At most 6 short lines, plain English.")


def _r(x, d=2):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), d)


def facts(sym, mode, plan, patterns, bars, verdict=None):
    """bars: daily bars (date, open, high, low, close), date-sorted, up to the plan's day."""
    c = bars.close.astype(float)
    lv = plan["levels"]
    f = dict(stock=sym, mode=mode, lean=plan["lean"], plan_rules=plan.get("version"),
             last_close=_r(c.iat[-1]), change_1d_pct=_r(100 * (c.iat[-1] / c.iat[-2] - 1)) if len(c) > 1 else None,
             change_5d_pct=_r(100 * (c.iat[-1] / c.iat[-6] - 1)) if len(c) > 5 else None,
             change_20d_pct=_r(100 * (c.iat[-1] / c.iat[-21] - 1)) if len(c) > 20 else None,
             vs_20d_avg_pct=_r(100 * (c.iat[-1] / lv["sma20"] - 1)), vs_50d_avg_pct=_r(100 * (c.iat[-1] / lv["sma50"] - 1)) if lv.get("sma50") else None,
             atr=_r(lv["atr"]), last_high=_r(lv["high"]), last_low=_r(lv["low"]), pivot=_r(lv["P"]), r1=_r(lv["R1"]), s1=_r(lv["S1"]),
             high_20d=_r(lv["hi20"]), low_20d=_r(lv["lo20"]),
             legs=[dict(leg=x["leg"], side=x["side"], price=x["price"], when=x["condition"]) for x in plan["legs"]],
             entries=plan["n_entries"], exits=plan["n_exits"],
             sizing=[dict(side=s["side"], shares=s["shares"], risk_rs=s["risk_rs"], reward_to_risk=s["rr"], stop_in_atr=s["stop_atr"]) for s in plan.get("sides", [])],
             no_trade_reasons=plan["flags"], tradeable=plan["tradeable"],
             candle_patterns=[f"{p['label']} ({p['direction']}, {p['date']})" for p in patterns],
             rules_tested_on_history=False)
    if verdict:
        f["market_lab"] = {k: verdict[k] for k in ("chance_pct", "usual_pct", "lean") if k in verdict}
    return f


def template(f):
    side = {"up": "long", "down": "short"}.get(f["lean"])
    legs = {(x["leg"], x["side"]): x["price"] for x in f["legs"]}
    trend = ("above" if (f["vs_20d_avg_pct"] or 0) > 0 else "below") + " its 20-day average"
    lines = [f"{f['stock']} closed at {f['last_close']} ({f['change_5d_pct']:+}% over 5 days), {trend}."]
    if side:
        lines.append(f"Plan ({f['mode']}, {side}): enter on a break of the last {'high' if side == 'long' else 'low'} at {legs.get(('E1', side))}, "
                     f"add at {legs.get(('E2', side))}, stop {legs.get(('SL', side))}, targets {legs.get(('T1', side))} and {legs.get(('T2', side))}.")
        lines.append(f"Cancelled if price trades {'below' if side == 'long' else 'above'} {legs.get(('SL', side))} first.")
    else:
        lines.append(f"No tested lean: long above {legs.get(('E1', 'long'))} or short below {legs.get(('E1', 'short'))}, whichever breaks first; "
                     f"stops {legs.get(('SL', 'long'))} / {legs.get(('SL', 'short'))}.")
    r = f.get("ta_evidence_read")
    if r:
        lines.append(f"Evidence: {r['up_checks']} checks up, {r['down_checks']} down (net {r['net']:+d}) → chart read {r['direction']}.")
    if f["candle_patterns"]:
        lines.append("Recent candles: " + "; ".join(f["candle_patterns"][:3]) + ".")
    caution = f["no_trade_reasons"][0] if f["no_trade_reasons"] else "the entry and exit rules aren't tested on history yet"
    lines.append(f"Caution: {caution}.")
    return "\n".join(lines)


def _numbers(text):
    return set(re.findall(r"\d+(?:\.\d+)?", text.replace(",", "")))


def check(text, f, extra_ok=""):
    """Numbers in the model's text that aren't in the facts (commas ignored; small counts allowed)."""
    allowed = _numbers(json.dumps(f, default=str)) | {str(i) for i in range(0, 21)} | _numbers(extra_ok)
    extra = set()
    for n in _numbers(text) - allowed:
        try:                                           # allow the same number written with fewer decimals (1024.6 -> 1024.60 or 1025?)
            v = float(n)
            if any(abs(v - float(a)) < 1e-9 for a in allowed if re.fullmatch(r"\d+(?:\.\d+)?", a)):
                continue
        except ValueError:
            pass
        extra.add(n)
    return extra


def explain(f, log=print):
    """(text, writer): writer is 'model' or 'template'."""
    tmpl = template(f)
    try:
        from core import llm
        ok, msg = llm.status("debate")
        if not ok:
            log(f"model not used: {msg}")
            return tmpl, "template"
        out = llm.chat("debate", [{"role": "user", "content": json.dumps(f, default=str)}], system=SYSTEM, max_tokens=500, timeout=180).strip()
        bad = check(out, f)
        if bad or not out:
            log(f"model wrote numbers that aren't in the facts ({', '.join(sorted(bad)[:5])}): using the template")
            return tmpl, "template"
        return out, "model"
    except Exception as e:                            # model off or failed: the template stands
        log(f"model not used ({type(e).__name__})")
        return tmpl, "template"


def ask(f, question, history=(), log=print):
    """(answer, writer): writer 'model', 'withheld' (unknown numbers) or 'off' (no model: the template instead)."""
    question = str(question or "").strip()[:500]
    try:
        from core import llm
        ok, msg = llm.status("debate")
        if not ok:
            return ("The TA agent needs the local model (Settings → AI models). Here is the plan from the computed facts:\n" + template(f)), "off"
        msgs = [{"role": "user", "content": "FACTS:\n" + json.dumps(f, default=str)}, {"role": "assistant", "content": "Understood. I will answer only from these facts."}]
        for h in list(history)[-6:]:
            msgs += [{"role": "user", "content": str(h.get("q", ""))[:500]}, {"role": "assistant", "content": str(h.get("a", ""))[:1500]}]
        msgs.append({"role": "user", "content": question})
        out = llm.chat("debate", msgs, system=SYSTEM_ASK, max_tokens=400, timeout=180).strip()
        prior = " ".join(str(h.get("q", "")) + " " + str(h.get("a", "")) for h in history)
        bad = check(out, f, question + " " + prior)
        if bad or not out:
            log(f"answer withheld: numbers not in the facts ({', '.join(sorted(bad)[:5])})")
            return ("I couldn't answer that from the computed facts without adding numbers that aren't in them, so the answer was withheld. "
                    "Try asking about the levels, the stop, the targets, the patterns or what would cancel the plan."), "withheld"
        return out, "model"
    except Exception as e:
        log(f"model not used ({type(e).__name__})")
        return ("The TA agent couldn't reach the local model. Here is the plan from the computed facts:\n" + template(f)), "off"
