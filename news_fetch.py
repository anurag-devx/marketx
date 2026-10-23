"""Refresh news headlines (RSS feeds + news sitemaps, optionally Google News for your stocks):  python3 news_fetch.py

Google News search per stock is OFF unless switched on in Data health → News sources. When on, it searches
only the stocks you hold, the momentum portfolio and today's screener shortlist (at most NEWS_GNEWS_MAX), at a
polite pace, and only if Google's robots.txt allows the search feed."""
import json, uuid
import pandas as pd
from core import news
from core.config import DATA, POSITIONS_CSV
from core.runlog import agent_run

OPTIONS = DATA / "news" / "options.json"
DEFAULT_OPTIONS = {"gnews": False, "gnews_max": 40, "sitemaps": True}


def options():
    try:
        return {**DEFAULT_OPTIONS, **json.loads(OPTIONS.read_text())}
    except Exception:
        return dict(DEFAULT_OPTIONS)


def save_options(o):
    OPTIONS.parent.mkdir(parents=True, exist_ok=True)
    OPTIONS.write_text(json.dumps({**options(), **o}, indent=1))


def watched_symbols(limit=40):
    """Stocks worth a per-stock search: held, in the momentum portfolio, on the latest screener shortlist."""
    out = []
    try:
        if POSITIONS_CSV.exists():
            out += list(pd.read_csv(POSITIONS_CSV).symbol)
    except Exception:
        pass
    try:
        from core import db
        for sql in ("select symbol from portfolio_targets where cast(date as date) = (select max(cast(date as date)) from portfolio_targets)",
                    "select symbol from candidates where cast(date as date) = (select max(cast(date as date)) from candidates) limit 30",
                    "select symbol from screener_results where cast(date as date) = (select max(cast(date as date)) from screener_results) limit 30"):
            try:
                out += list(db.read(sql).symbol)
            except Exception:
                pass
    except Exception:
        pass
    seen, res = set(), []
    for s in out:
        if s not in seen:
            seen.add(s); res.append(s)
    return res[:limit]


if __name__ == "__main__":
    o = options()
    with agent_run(uuid.uuid4().hex[:8], "news") as rec:
        syms = watched_symbols(o["gnews_max"]) if o["gnews"] else None
        info, health = news.update(gnews_symbols=syms, use_sitemaps=o["sitemaps"])
        bad = [f"{h['source']} ({h['status']})" for h in health if h.get("status") in ("dead", "error", "blocked")]
        rec["rows_out"] = info["added"]
        rec["message"] = f"{info['added']} new headlines, {info['total']} stored" + (f"; not working: {', '.join(bad[:6])}" if bad else "")
        print(f"{info['fetched']} headlines read, {info['added']} new, {info['total']} stored")
        if syms:
            print(f"Google News searched for {len(syms)} stocks")
        for h in health:
            print(f"  {h['source']:<40} {h.get('status', ''):<8} {h.get('items', '')!s:>5}  {h.get('note', '')}")
