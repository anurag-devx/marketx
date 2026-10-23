"""Deep search: a cited research brief for a question.

1. Plan: the model (set for 'search' in Settings → AI models) splits the question into 3-5 sub-searches.
2. Gather, for each sub-search: stored headlines + filings (search index), your documents and article text
   (library), and, if a web provider is set, web results (whose pages are read where robots.txt allows) or
   Google News headlines when no provider is set.
3. Write: the model writes the brief only from the numbered evidence, citing [n]. Citations that don't exist are
   flagged. The brief and its sources are saved in data/search/briefs/."""
import json, re, time
import pandas as pd
from .config import DATA

BRIEFS = DATA / "search" / "briefs"
MAX_EVIDENCE = 24
PLAN_SYSTEM = ("You plan searches for an Indian equity research assistant. Reply with ONLY a JSON list of 3 to 5 short search queries "
               "(company names, sectors, events, policy terms) that together cover the question. No explanation.")
WRITE_SYSTEM = """You are an equity research analyst writing a brief for an Indian stock market investor.
Use ONLY the numbered EVIDENCE. Cite every fact with its number like [3]; combine citations like [2][5].
Structure: a 2-3 sentence answer first, then short sections with headings where useful (What happened, Why it matters,
Who is affected, What to watch). Say clearly what the evidence does not cover. Quote numbers exactly.
No buy/sell advice, no price targets, no invented facts."""


def plan(question, log=print):
    from . import llm
    try:
        txt = llm.chat("search", [{"role": "user", "content": question}], system=PLAN_SYSTEM, max_tokens=200, temperature=0.1)
        m = re.search(r"\[.*\]", txt, re.S)
        qs = [q.strip() for q in json.loads(m.group(0)) if isinstance(q, str) and q.strip()] if m else []
    except Exception as e:
        log(f"  planning step failed ({type(e).__name__}); searching the question as written")
        qs = []
    qs = list(dict.fromkeys([question] + qs))[:5]
    return qs


def gather(queries, use_web=True, read_pages=3, log=print):
    from . import search_index, websearch
    ev, seen = [], set()

    def add(kind, title, text, url, date, src):
        key = (url or title)[:200]
        if key in seen or not (text or title):
            return
        seen.add(key)
        ev.append(dict(kind=kind, title=str(title)[:200], text=str(text)[:1500], url=url or "", date=str(date or "")[:10], source=src))

    for q in queries:
        try:
            hits = search_index.search(q, k=6)
            for r in hits.itertuples():
                add("filing" if r.kind == "filing" else "headline", r.title, r.text, r.link, r.published, r.source)
        except Exception as e:
            log(f"  stored headlines: {type(e).__name__}")
        try:
            from . import library
            if library.options()["enabled"]:
                for r in library.search(q, k=4).itertuples():
                    add("document" if r.kind != "news" else "article", r.title, r.text, r.location if str(r.location).startswith("http") else "",
                        r.published, r.title)
        except Exception:
            pass
        if use_web:
            ok, msg = websearch.status()
            try:
                if ok:
                    res = websearch.search(q, news=True) + websearch.search(q)
                else:
                    res = websearch.google_news(q, days=60)
                for r in res:
                    add("web", r["title"], r["snippet"] or r["title"], r["url"], r["published"], r["provider"])
            except Exception as e:
                log(f"  web: {str(e)[:120]}")
        log(f"  searched: {q} ({len(ev)} items so far)")
    if use_web and read_pages:
        from . import library
        from .news_sources import Polite
        p = Polite(gap=2.0)
        n = 0
        for e in [x for x in ev if x["kind"] == "web" and x["url"].startswith("http") and "news.google.com" not in x["url"]]:
            if n >= read_pages:
                break
            try:
                _, txt, _ = library.extract_page(p.get(e["url"], timeout=20).decode("utf-8", "ignore"), e["url"])
                if len(txt) > 400:
                    e["text"] = txt[:3000]
                    e["kind"] = "web page"
                    n += 1
            except Exception:
                continue
    return ev[:MAX_EVIDENCE]


def write(question, evidence):
    from . import llm
    if not evidence:
        return "Nothing relevant was found in your stored data, documents or the web for this question.", []
    lines = [f"[{i}] ({e['kind']}, {e['source']}, {e['date']}) {e['title']}\n{e['text']}" for i, e in enumerate(evidence, 1)]
    text = llm.chat("search", [{"role": "user", "content": f"QUESTION: {question}\n\nEVIDENCE:\n" + "\n\n".join(lines)}],
                    system=WRITE_SYSTEM, max_tokens=1600)
    cited = {int(x) for x in re.findall(r"\[(\d{1,3})\]", text)}
    bad = sorted(c for c in cited if c < 1 or c > len(evidence))
    return text, bad


def run(question, use_web=True, log=print):
    t0 = time.time()
    log("Planning searches...")
    qs = plan(question, log)
    log("Gathering evidence...")
    ev = gather(qs, use_web=use_web, log=log)
    log(f"Writing the brief from {len(ev)} items...")
    text, bad = write(question, ev)
    rec = dict(question=question, when=str(pd.Timestamp.now().floor("s")), queries=qs, evidence=ev, brief=text, bad_citations=bad,
               seconds=round(time.time() - t0))
    BRIEFS.mkdir(parents=True, exist_ok=True)
    (BRIEFS / f"{time.strftime('%Y%m%d_%H%M%S')}.json").write_text(json.dumps(rec, indent=1, default=str))
    return rec


def history(n=30):
    if not BRIEFS.exists():
        return []
    out = []
    for p in sorted(BRIEFS.glob("*.json"), reverse=True)[:n]:
        try:
            out.append(json.loads(p.read_text()) | {"file": p.name})
        except Exception:
            pass
    return out
