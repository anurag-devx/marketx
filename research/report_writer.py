"""Turns the report FACTS (research/market_report.py) into a written report.

Writer: the model set for 'report' in Settings → AI models (local Ollama by default: nothing leaves the Mac).
  Each section is written separately from only that section's facts (small models stay accurate that way).
Checker: every number in the model's text must match a number in the facts (rounding allowed). A section with
  unmatched numbers is retried once with the problems listed; if it still fails, the section falls back to the
  template text and the report says so.
Template: plain sentences built from the facts. Used when Ollama isn't running or for --no-llm.
Tables in the report always come from the facts, never from the model."""
import json, re, time
import pandas as pd
from core import config as C

OUT = C.REPORTS / "market"

SYSTEM = """You are an equity research analyst writing the daily Indian stock market wrap for professional readers.
Rules:
- Use ONLY the FACTS given. Every number you write must appear in FACTS (you may round to one decimal). Never compute new numbers, never estimate.
- Do not invent reasons for a move. Mention a reason only if a filing or headline listed in FACTS says it, and write "alongside" or "after", not "because". If a stock has no news listed, you may say no stock-specific news was found in the stored sources.
- No buy/sell/hold advice, no price targets, no forecasts.
- Indian conventions: ₹, crore, Nifty 50. Use stock symbols as given.
- Write tight, specific prose like a research note: lead with the most important observation, compare (vs Nifty, vs recent days, vs other sectors), point out what is unusual.
- No headings, no bullet points, no preamble like "Here is". Plain paragraphs only."""

SECTIONS = [
    ("market", "Market overview", 140, "Describe the day: Nifty move and level vs its 52-week high, VIX, breadth (advances vs declines, share beating the Nifty, big movers), how the typical stock did vs the index, participation (traded value vs normal), 52-week highs vs lows, and how today compares with the last 5 days and the 50/200-day trend share."),
    ("up", "What went up", 130, "Describe the biggest gainers among liquid stocks: who led, which sectors they came from, whether the moves came on heavy volume, which ones made 52-week highs, and the news or filings listed with them. Group similar stories; don't just list every stock."),
    ("down", "What went down", 130, "Describe the biggest losers among liquid stocks in the same way: who, which sectors, volume, 52-week lows, listed news or filings."),
    ("sectors", "Sectors", 150, "Explain which sectors were most affected (largest moves either way) and which were least affected (closest to flat), the best and worst, how broad each move was (share of stocks up), heavy-weight vs typical stock (weighted vs median), and the 5- and 20-day trend for context."),
    ("new", "New players", 130, "Cover new leaders (first 52-week highs in months, new entrants to the 3-month strength top 50), Nifty 500 entries and exits, unusual volume, recent listings and how they trade, and IPO headlines. If a part is empty, say briefly that there was none."),
    ("macro", "Macro and geopolitics", 120, "Describe the global and macro backdrop from the indicators (crude, USD/INR, US yields, dollar, VIX, FII/DII flows: level and recent change; flag anything unusual vs its past year), the geopolitical risk index vs its averages, which geopolitical themes dominated the week's headlines, and the events coming up. Do not link these to specific stock moves unless FACTS do."),
    ("filings", "Filings and headlines", 100, "Summarise the day's NSE filings (how many, which types) and the notable ones for liquid stocks with the stock's move, then the main market headlines not tied to one stock."),
]


# ---------------- facts -> compact text for the prompt ----------------
def _tbl(rows, cols):
    if not rows:
        return "(none)"
    out = [" | ".join(cols)]
    for r in rows:
        vals = []
        for c in cols:
            v = r.get(c)
            if c == "news":
                v = "; ".join(f"{n['kind']} [{n['type']}]: {n['text']}" for n in (v or [])) or "-"
            vals.append("-" if v is None else str(v))
        out.append(" | ".join(vals))
    return "\n".join(out)


def section_facts(f, key):
    m = f["market"]
    if key == "market":
        d = {k: v for k, v in m.items() if k != "last_5_days"}
        return json.dumps(d) + "\nLast 5 days:\n" + _tbl(m["last_5_days"], ["date", "nifty_pct", "advances", "declines"])
    if key in ("up", "down"):
        return (f"Nifty today {m['nifty_change_pct']}%. Liquid stocks only (avg traded value >= ₹{f['settings']['min_traded_cr']} crore).\n"
                + _tbl(f[key], ["symbol", "company", "sector", "move_pct", "vs_nifty_pct", "volume_x_normal", "traded_cr", "month_pct", "new_52w_high", "new_52w_low", "news"]))
    if key == "sectors":
        s = f["sectors"]
        return (f"Nifty today {m['nifty_change_pct']}%. Most affected (largest move either way): {s['most_affected']}. Least affected: {s['least_affected']}.\n"
                + _tbl(s["table"], ["sector", "stocks", "median_pct", "weighted_pct", "share_up_pct", "median_5d_pct", "median_20d_pct", "best", "worst", "traded_vs_20d"]))
    if key == "new":
        n = f["new"]
        return ("New leaders:\n" + _tbl(n["leaders"], ["symbol", "company", "sector", "move_pct", "reason"])
                + "\nNifty 500 changes (last 30 days):\n" + _tbl(n["index_changes"], ["date", "symbol", "change"])
                + "\nUnusual volume:\n" + _tbl(n["unusual_volume"], ["symbol", "sector", "move_pct", "volume_x_normal", "news"])
                + "\nRecent listings (under a year of trading):\n" + _tbl(n["recent_listings"], ["symbol", "company", "first_day", "sessions", "since_first_close_pct", "move_pct"])
                + "\nIPO headlines (last 7 days):\n" + _tbl(n["ipo_headlines"], ["date", "text"]))
    if key == "macro":
        mc = f.get("macro") or {}
        return ("Indicators (value, change over 1 / 5 / 20 days: % change, except points for yields (%), and for FII/DII flows (change_kind 'total') "
                "chg_5d / chg_20d are 5- and 20-day TOTALS in ₹ crore; z_1y = distance from past-year average in sd):\n"
                + _tbl(mc.get("indicators", []), ["label", "date", "value", "unit", "change_kind", "chg_1d", "chg_5d", "chg_20d", "z_1y"])
                + f"\nGeopolitical risk index: {json.dumps(mc.get('gpr'))}\nThemes in the week's headlines:\n"
                + _tbl([dict(theme=t["theme"], headlines=t["headlines"], latest=" / ".join(t["latest"])) for t in mc.get("themes", [])], ["theme", "headlines", "latest"])
                + "\nUpcoming events (14 days):\n" + _tbl(mc.get("upcoming", []), ["date", "event", "country"]))
    if key == "filings":
        fl = f["filings"]
        return (f"NSE filings today: {fl['count']}. By type: {json.dumps(fl['by_type'])}\nNotable:\n" + _tbl(fl["notable"], ["symbol", "type", "text", "move_pct"])
                + "\nMarket headlines (not about one stock):\n" + _tbl(f["headlines"], ["source", "text", "sources"]))
    raise KeyError(key)


# ---------------- number check ----------------
NUM = re.compile(r"(?<![\w.])[-+−]?\d[\d,]*(?:\.\d+)?")
ALWAYS_OK = {0, 1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 50, 52, 100, 200, 500}


def _numbers_in(obj, acc):
    if isinstance(obj, dict):
        for v in obj.values():
            _numbers_in(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers_in(v, acc)
    elif isinstance(obj, bool):
        pass
    elif isinstance(obj, (int, float)):
        if obj == obj:
            acc.add(abs(float(obj)))
    elif isinstance(obj, str):
        for t in NUM.findall(obj):
            try:
                acc.add(abs(float(t.replace(",", "").replace("−", "-"))))
            except ValueError:
                pass
    return acc


def check_numbers(text, facts_text, facts):
    """Numbers in `text` not found in the facts (allowing rounding to 0 or 1 decimal)."""
    ok = _numbers_in(facts, set()) | _numbers_in(facts_text, set())
    bad = []
    for t in NUM.findall(text):
        try:
            v = abs(float(t.replace(",", "").replace("−", "-")))
        except ValueError:
            continue
        if v in ALWAYS_OK or 1990 <= v <= 2100:
            continue
        if any(abs(v - x) <= 0.051 or abs(v - round(x)) < 1e-9 or abs(v - round(x, 1)) < 1e-9 for x in ok):
            continue
        bad.append(t)
    return bad


# ---------------- model (via core.llm: local Ollama by default) ----------------
def llm_settings():
    from core import llm
    _, prov, model = llm.resolve("report")
    return {"model": model, "provider": prov, "enabled": (getattr(C, "LOCAL_LLM", {}) or {}).get("enabled", True)}


def ollama_status(s=None):
    """(ok, message, installed local models)."""
    from core import llm
    ok, msg = llm.status("report")
    try:
        models = llm.ollama_models() or []
    except Exception:
        models = []
    return ok, msg, models


def _chat(messages, s):
    from core import llm
    system = next((m["content"] for m in messages if m["role"] == "system"), None)
    rest = [m for m in messages if m["role"] != "system"]
    txt = llm.chat("report", rest, system=system, max_tokens=900)
    return re.sub(r"^\s*#+.*$", "", txt, flags=re.M).strip()


def llm_section(facts, key, title, words, brief, s, log=print):
    ft = section_facts(facts, key)
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Report date: {facts['day']}. Write the '{title}' section in about {words} words.\n{brief}\n\nFACTS:\n{ft}"}]
    text = _chat(msgs, s)
    bad = check_numbers(text, ft, facts)
    if bad:
        log(f"  {title}: numbers not in the facts {bad[:6]}; asking for a correction")
        msgs += [{"role": "assistant", "content": text},
                 {"role": "user", "content": f"These numbers are not in FACTS: {', '.join(bad[:10])}. Rewrite the section using only numbers that appear in FACTS."}]
        text = _chat(msgs, s)
        bad = check_numbers(text, ft, facts)
    return text, bad


def llm_summary(facts, parts, s):
    body = "\n\n".join(f"{t}:\n{x}" for t, x in parts)
    ft = section_facts(facts, "market")
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": "Write a 3-4 sentence executive summary of this market report: the single most important takeaway first, "
                                        "then what led and lagged, then anything new or unusual. Use only numbers that appear below.\n\n" + body}]
    text = _chat(msgs, s)
    bad = check_numbers(text, ft + body, facts)
    return text, bad


# ---------------- template (no model) ----------------
def _p(x, d=1):
    return "n/a" if x is None else f"{x:+.{d}f}%"


def template_section(f, key):
    m = f["market"]
    if key == "market":
        tone = "gained" if (m["nifty_change_pct"] or 0) > 0 else "fell"
        breadth = "Gainers outnumbered losers" if m["advances"] > m["declines"] else "Losers outnumbered gainers"
        return (f"The Nifty 50 {tone} {abs(m['nifty_change_pct']):.2f}% to {m['nifty_close']:,.2f}, {_p(m['nifty_from_52w_high_pct'])} from its 52-week high; "
                f"over five sessions it is {_p(m['nifty_5d_pct'])} and over twenty {_p(m['nifty_20d_pct'])}. India VIX was {m['vix']} ({m['vix_change']:+.2f}). "
                f"{breadth}: {m['advances']} advances against {m['declines']} declines, with {m['up_2pct']} stocks up more than 2% and {m['down_2pct']} down more than 2%. "
                f"The median stock moved {_p(m['median_stock_pct'], 2)} and {m['beat_nifty_pct']}% of stocks beat the index. "
                f"{m['new_52w_highs']} stocks made 52-week highs and {m['new_52w_lows']} made 52-week lows; {m['above_200dma_pct']}% trade above their 200-day average. "
                f"Traded value was {m['traded_value_vs_20d']}× its 20-day average.")
    if key in ("up", "down"):
        rows = f[key]
        if not rows:
            return "No liquid stock moved this way."
        lead = ", ".join(f"{r['symbol']} ({_p(r['move_pct'])})" for r in rows[:5])
        secs = pd.Series([r["sector"] for r in rows]).value_counts()
        heavy = [r["symbol"] for r in rows if (r["volume_x_normal"] or 0) >= 2]
        with_news = [r for r in rows if r["news"]]
        s = f"{'Leading the gainers' if key == 'up' else 'The biggest losers'} were {lead}. "
        s += f"{secs.index[0]} had the most names in the list ({secs.iat[0]} of {len(rows)}). "
        if heavy:
            s += f"{', '.join(heavy[:6])} traded at least twice their normal volume. "
        if with_news:
            s += " ".join(f"{r['symbol']} moved alongside a {r['news'][0]['kind']} ({r['news'][0]['type']}): {r['news'][0]['text'][:120]}." for r in with_news[:3])
        else:
            s += "No stock-specific filing or headline was found for these names in the stored sources."
        return s
    if key == "sectors":
        sc = f["sectors"]
        t = {x["sector"]: x for x in sc["table"]}
        most = "; ".join(f"{k} {_p(t[k]['median_pct'])} (median, {t[k]['share_up_pct']:.0f}% of stocks up)" for k in sc["most_affected"])
        least = ", ".join(f"{k} {_p(t[k]['median_pct'])}" for k in sc["least_affected"])
        return (f"Most affected: {most}. Least affected: {least}. Best: {', '.join(sc['best'])}; worst: {', '.join(sc['worst'])}. "
                f"Weighted moves differ from medians where the heaviest stocks diverged from the typical one (see table).")
    if key == "new":
        n = f["new"]
        parts = []
        if n["leaders"]:
            parts.append("New leaders: " + ", ".join(f"{x['symbol']} ({x['reason']})" for x in n["leaders"][:6]) + ".")
        if n["index_changes"]:
            parts.append("Nifty 500 changes: " + ", ".join(f"{x['symbol']} {x['change']}" for x in n["index_changes"][:8]) + ".")
        if n["unusual_volume"]:
            parts.append("Unusual volume: " + ", ".join(f"{x['symbol']} {x['volume_x_normal']}× ({_p(x['move_pct'])})" for x in n["unusual_volume"][:6]) + ".")
        if n["recent_listings"]:
            parts.append("Recent listings: " + ", ".join(f"{x['symbol']} {_p(x['since_first_close_pct'])} since its first close" for x in n["recent_listings"][:5]) + ".")
        if n["ipo_headlines"]:
            parts.append(f"{len(n['ipo_headlines'])} IPO headlines in the past week.")
        return " ".join(parts) or "No new leaders, index changes, unusual volume or recent listings."
    if key == "macro":
        mc = f.get("macro") or {}
        parts = []
        for r in mc.get("indicators", []):
            if r["change_kind"] == "total":
                parts.append(f"{r['label']} ₹{r['value']:,.0f} crore on {r['date']}"
                             + (f" (₹{r['chg_20d']:,.0f} crore over 20 days)" if r["chg_20d"] is not None else ""))
                continue
            unit = " pts" if r["change_kind"] == "points" else "%"
            parts.append(f"{r['label']} {r['value']:,.2f}{' %' if r['unit'] == '%' else ''}"
                         + (f" ({r['chg_20d']:+.2f}{unit} over 20 days)" if r["chg_20d"] is not None else ""))
        s = ("Backdrop: " + "; ".join(parts) + ". ") if parts else "No macro data fetched yet. "
        g = mc.get("gpr")
        if g:
            s += f"The geopolitical risk index read {g['value']} against a 30-day average of {g['avg_30d']} and a one-year average of {g['avg_1y']}. "
        if mc.get("themes"):
            s += "Most-covered themes this week: " + ", ".join(f"{t['theme']} ({t['headlines']} headlines)" for t in mc["themes"][:4]) + ". "
        if mc.get("upcoming"):
            s += "Coming up: " + "; ".join(f"{e['date']} {e['event']}" for e in mc["upcoming"][:5]) + "."
        return s
    if key == "filings":
        fl = f["filings"]
        s = f"{fl['count']} NSE filings were matched to this day"
        s += (": " + ", ".join(f"{k} {v}" for k, v in list(fl["by_type"].items())[:5]) + ". ") if fl["by_type"] else ". "
        if fl["notable"]:
            s += "Notable: " + "; ".join(f"{x['symbol']} {x['type']} ({_p(x['move_pct'])})" for x in fl["notable"][:5]) + ". "
        if f["headlines"]:
            s += "Main market headlines: " + " / ".join(h["text"] for h in f["headlines"][:3]) + "."
        return s
    raise KeyError(key)


def template_summary(f):
    m, sc = f["market"], f["sectors"]
    return (f"Nifty 50 {_p(m['nifty_change_pct'], 2)} with {m['advances']} advances and {m['declines']} declines; the median stock {_p(m['median_stock_pct'], 2)}. "
            f"{', '.join(sc['best'][:2])} led and {', '.join(sc['worst'][:2])} lagged. "
            f"{m['new_52w_highs']} new 52-week highs against {m['new_52w_lows']} lows.")


# ---------------- assemble ----------------
def write(facts, use_llm=True, log=print):
    s = llm_settings()
    ok, msg, _ = ollama_status(s) if use_llm and s.get("enabled", True) else (False, "model not used", [])
    log(("Writer: " + msg) if use_llm else "Writer: template (no model)")
    parts, flags, used = [], {}, {}
    t0 = time.time()
    for key, title, words, brief in SECTIONS:
        text = None
        if ok:
            for attempt in (1, 2):                     # one retry: a busy or reloading local model often succeeds the second time
                try:
                    log(f"  writing {title}..." if attempt == 1 else f"  {title}: retrying once...")
                    text, bad = llm_section(facts, key, title, words, brief, s, log)
                    if bad:
                        flags[title] = bad
                        text = None
                    used[title] = "model" if text else "template (numbers failed the check)"
                    break
                except Exception as e:
                    reason = f"{type(e).__name__}: {str(e)[:160]}"
                    if attempt == 1:
                        log(f"  {title}: model error ({reason})")
                        time.sleep(5)
                    else:
                        log(f"  {title}: model error again ({reason}); using the template")
                        used[title] = "template (model error)"
        if text is None:
            text = template_section(facts, key)
            used.setdefault(title, "template")
        parts.append((title, text))
    summary = None
    if ok:
        try:
            summary, bad = llm_summary(facts, parts, s)
            if bad:
                flags["Summary"] = bad
                summary = None
        except Exception:
            summary = None
    used["Summary"] = "model" if summary else "template"
    summary = summary or template_summary(facts)
    return dict(summary=summary, sections=parts, writer=dict(model=s["model"] if ok else None, status=msg, used=used, flags=flags,
                                                            seconds=round(time.time() - t0)))


def to_markdown(facts, rep):
    m = facts["market"]
    L = [f"# Indian equities: daily market report, {pd.Timestamp(facts['day']):%A %d %B %Y}", "",
         f"Nifty 50 {m['nifty_close']:,.2f} ({m['nifty_change_pct']:+.2f}%) · India VIX {m['vix']} · advances {m['advances']} / declines {m['declines']}", "",
         "## Summary", "", rep["summary"], ""]
    tables = {"What went up": ("up", ["symbol", "sector", "move_pct", "vs_nifty_pct", "volume_x_normal", "traded_cr", "month_pct"]),
              "What went down": ("down", ["symbol", "sector", "move_pct", "vs_nifty_pct", "volume_x_normal", "traded_cr", "month_pct"])}
    for title, text in rep["sections"]:
        L += [f"## {title}", "", text, ""]
        if title in tables:
            k, cols = tables[title]
            L += _md_table(facts[k], cols)
        if title == "Sectors":
            L += _md_table(facts["sectors"]["table"], ["sector", "stocks", "median_pct", "weighted_pct", "share_up_pct", "median_5d_pct", "median_20d_pct", "best", "worst"])
    w = rep["writer"]
    L += ["---", f"Numbers computed from stored NSE prices, filings and headlines. Text written by "
          f"{'the local model ' + w['model'] if w['model'] else 'the template writer'}; every number in the text was checked against the data. "
          "Moves 'alongside' news are not claims of cause. Not investment advice."]
    return "\n".join(L)


def _md_table(rows, cols):
    if not rows:
        return ["(none)", ""]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join("" if r.get(c) is None else str(r.get(c)) for c in cols) + " |")
    return out + [""]


def write_and_save(facts, use_llm=True, log=print):
    rep = write(facts, use_llm, log)
    OUT.mkdir(parents=True, exist_ok=True)
    day = facts["day"]
    (OUT / f"{day}.json").write_text(json.dumps(dict(facts=facts, report=rep, written=str(pd.Timestamp.now().floor("s"))), indent=1, default=str))
    p = OUT / f"{day}.md"
    p.write_text(to_markdown(facts, rep))
    log(f"Writer used: {rep['writer']['used']}")
    return p


def saved_reports():
    if not OUT.exists():
        return []
    return sorted([p.stem for p in OUT.glob("*.json")], reverse=True)


def load_report(day):
    p = OUT / f"{day}.json"
    return json.loads(p.read_text()) if p.exists() else None
