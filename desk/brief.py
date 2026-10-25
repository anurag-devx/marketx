"""Stock Desk AI brief: the local model writes a one-page brief from a snapshot's computed facts only. It cites the section
in brackets ([Market Lab], [Technical], [Behaviour], [Fundamentals], [Ownership], [Peers], [News]); every number must
appear in the facts, otherwise the brief is withheld and the facts-only summary is shown instead. No predictions of its own."""
import json
from tradeplan.agent import check

SYSTEM = ("You are an equity research assistant writing a short brief on one Indian stock for an investor. Use ONLY the FACTS. "
          "Cite the section in brackets after each point, e.g. [Market Lab], [Technical], [Behaviour], [Fundamentals], [Ownership], [Peers], [News]. "
          "Never add a number that is not in the facts, never predict prices, no advice to buy or sell. Structure: 1) one-line take, "
          "2) what supports a move up, 3) what argues for down or caution, 4) what to watch next. At most 14 short lines, plain English.")


def facts(s):
    ml = s.get("market_lab") or {}
    v = {f"{x['move']}_{x['size']}": {k: x[k] for k in ("chance", "usual", "lean", "confidence")} for x in ml.get("verdicts", []) if x["size"] in (1.0, 2.0)}
    ta = s.get("ta") or {}
    ev = ta.get("evidence") or {}
    q = ((s.get("fundamentals") or {}).get("quarters") or [])[-2:]
    o = (s.get("ownership") or [])[-2:]
    intr = {k: (s.get("intraday") or {}).get(k) for k in ("sessions", "crossed_pct", "median_first", "held_pct", "choppy_pct")}
    ev_res = (s.get("events") or {}).get("results") or {}
    peers = [{k: r.get(k) for k in ("symbol", "r3m", "ta", "pe", "chance")} for r in ((s.get("peers") or {}).get("rows") or [])][:6]
    return {"stock": s.get("symbol"), "name": s.get("name"), "sector": s.get("sector"), "as_of": s.get("as_of"), "price": s.get("price"),
            "Market Lab": dict(verdicts=v, closeness=ml.get("dist_pct")),
            "Technical": dict(read=ev.get("dir"), net=ev.get("net"), checks=[f"{c['id']} {c['reason']}" for c in ev.get("checks", []) if c["verdict"]][:8],
                              strong_patterns=[f"{p['label']} {p['date']}" for p in ta.get("patterns", []) if p.get("strength") == "strong"]),
            "Behaviour": dict(intraday=intr, results_reaction={k: ev_res.get(k) for k in ("n", "avg_abs1", "big1", "up1")}),
            "Fundamentals": q, "Ownership": o, "Peers": peers,
            "News": (s.get("news") or {}).get("summary"), "Upcoming": s.get("upcoming"),
            "Reasons for": [r["text"] for r in (s.get("reasons") or {}).get("pro", [])], "Reasons against": [r["text"] for r in (s.get("reasons") or {}).get("con", [])]}


def template(f):
    lines = [f"{f['stock']} ({f.get('sector') or ''}) as of {f['as_of']}."]
    c = (f["Market Lab"]["verdicts"] or {}).get("cc_1.0")
    if c:
        lines.append(f"Market Lab: chance of a 1%+ close-to-close move {round(100 * c['chance'])}% vs usual {round(100 * c['usual'])}%, lean {c['lean']}, confidence {c['confidence']}. [Market Lab]")
    t = f["Technical"]
    lines.append(f"Chart read: {t['read']} (net {t['net']}). [Technical]")
    if f["Reasons for"]:
        lines.append("For: " + "; ".join(f["Reasons for"][:4]) + ".")
    if f["Reasons against"]:
        lines.append("Against: " + "; ".join(f["Reasons against"][:4]) + ".")
    if f.get("Upcoming"):
        lines.append("Next: " + "; ".join(f"{x['what']} {x['date']}" for x in f["Upcoming"][:3]) + ".")
    return "\n".join(lines)


def write(s, log=print):
    f = facts(s)
    tmpl = template(f)
    try:
        from core import llm
        ok, msg = llm.status("debate")
        if not ok:
            return tmpl, "template"
        out = llm.chat("debate", [{"role": "user", "content": "FACTS:\n" + json.dumps(f, default=str)}], system=SYSTEM, max_tokens=700, timeout=240).strip()
        bad = check(out, f)
        if bad or not out:
            log(f"brief withheld: numbers not in the facts ({', '.join(sorted(bad)[:5])})")
            return tmpl, "template"
        return out, "model"
    except Exception as e:
        log(f"model not used ({type(e).__name__})")
        return tmpl, "template"
