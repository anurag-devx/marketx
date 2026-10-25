"""TA agent: explains a trade plan in plain English from computed facts only (no chart images, no new numbers).

facts()    levels, legs, recent price action, candle patterns and the Market Lab verdict for one stock and mode
template() a short summary built from the facts (always available, instant)
explain()  the local model words the same facts in 3-5 lines; every number it writes must appear in the facts,
           otherwise the template is used. Uses the 'debate' model setting (Settings → AI models)."""
import json, re
import numpy as np, pandas as pd

SYSTEM = ("You are a technical analyst explaining a pre-computed trade plan to an investor. Use ONLY the facts given and never add a number "
          "that is not in them. Write 3-5 short lines, plain English, no headings, no advice to buy or sell:\n"
          "1) what the recent price action and candle patterns show, 2) why the entry, stop and targets sit where they do (name the levels), "
          "3) what would cancel the plan, 4) one caution (e.g. wide stop, conflicting pattern, weak market, untested rules).")


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
    if f["candle_patterns"]:
        lines.append("Recent candles: " + "; ".join(f["candle_patterns"][:3]) + ".")
    caution = f["no_trade_reasons"][0] if f["no_trade_reasons"] else "the entry and exit rules aren't tested on history yet"
    lines.append(f"Caution: {caution}.")
    return "\n".join(lines)


def _numbers(text):
    return set(re.findall(r"\d+(?:\.\d+)?", text.replace(",", "")))


def check(text, f):
    """Numbers in the model's text that aren't in the facts (commas ignored; small counts allowed)."""
    allowed = _numbers(json.dumps(f, default=str)) | {str(i) for i in range(0, 21)}
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
