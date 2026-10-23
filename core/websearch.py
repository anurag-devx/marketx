"""Web search for Deep search: one interface over several providers. Off unless a provider is chosen and its key is set.

Provider   key in ~/.swing_env   free tier (Oct 2026)                       paid
Tavily     TAVILY_API_KEY       1,000 searches / month, no card              ~$0.008 per search
Brave      BRAVE_API_KEY        $5 credit / month (~1,000), card required   $5 per 1,000
Serper     SERPER_API_KEY       2,500 searches once                          from $0.30 per 1,000
Exa        EXA_API_KEY          ~$10 credit / month                          pay as you go
Free tiers change; check the provider's page. The monthly cap (Search → Web search settings) keeps you inside a
free tier; raise it if you pay. Only the search words are sent to the provider. Results are used for the brief
you asked for and kept with it on this Mac."""
import json, time
from .config import DATA
from . import secrets

DIR = DATA / "search"
OPTS = DIR / "web_options.json"
USAGE = DIR / "web_usage.json"
PROVIDERS = {"none": ("Off (only your stored data)", None), "tavily": ("Tavily", "TAVILY_API_KEY"), "brave": ("Brave Search", "BRAVE_API_KEY"),
             "serper": ("Serper (Google results)", "SERPER_API_KEY"), "exa": ("Exa", "EXA_API_KEY")}
DEFAULT = {"provider": "none", "monthly_cap": 900, "results": 6, "india_bias": True}


class WebSearchError(RuntimeError):
    pass


def options():
    try:
        return {**DEFAULT, **json.loads(OPTS.read_text())}
    except Exception:
        return dict(DEFAULT)


def save_options(o):
    DIR.mkdir(parents=True, exist_ok=True)
    OPTS.write_text(json.dumps({**options(), **o}, indent=1))


def usage():
    try:
        u = json.loads(USAGE.read_text())
    except Exception:
        u = {}
    return u.get(time.strftime("%Y-%m"), {})


def _count(provider):
    DIR.mkdir(parents=True, exist_ok=True)
    try:
        u = json.loads(USAGE.read_text())
    except Exception:
        u = {}
    m = time.strftime("%Y-%m")
    u.setdefault(m, {})
    u[m][provider] = u[m].get(provider, 0) + 1
    USAGE.write_text(json.dumps(u, indent=1))


def status():
    o = options()
    p = o["provider"]
    if p == "none":
        return False, "Web search is off: Deep search uses your stored data and Google News headlines."
    key = PROVIDERS[p][1]
    if not secrets.has(key):
        return False, f"{PROVIDERS[p][0]} selected but {key} isn't in ~/.swing_env."
    used = usage().get(p, 0)
    if used >= o["monthly_cap"]:
        return False, f"{PROVIDERS[p][0]}: monthly cap reached ({used}/{o['monthly_cap']})."
    return True, f"{PROVIDERS[p][0]} · {used}/{o['monthly_cap']} searches used this month"


def _norm(rows):
    out = []
    for r in rows:
        if r.get("url"):
            out.append(dict(title=(r.get("title") or "")[:300], url=r["url"], snippet=(r.get("snippet") or "")[:1500],
                            published=str(r.get("published") or "")[:25], provider=r.get("provider", "")))
    return out


def search(query, n=None, news=False):
    """-> list of {title, url, snippet, published, provider}. Raises WebSearchError when unavailable."""
    import requests
    ok, msg = status()
    if not ok:
        raise WebSearchError(msg)
    o = options()
    p = o["provider"]
    key = secrets.get(PROVIDERS[p][1])
    n = int(n or o["results"])
    q = query + (" India" if o["india_bias"] and "india" not in query.lower() else "")
    try:
        if p == "tavily":
            r = requests.post("https://api.tavily.com/search", timeout=40, headers={"Authorization": f"Bearer {key}"},
                              json={"query": q, "max_results": n, "search_depth": "basic", "topic": "news" if news else "general"})
            r.raise_for_status()
            rows = [dict(title=x.get("title"), url=x.get("url"), snippet=x.get("content"), published=x.get("published_date"), provider="Tavily")
                    for x in r.json().get("results", [])]
        elif p == "brave":
            path = "news/search" if news else "web/search"
            r = requests.get(f"https://api.search.brave.com/res/v1/{path}", timeout=40,
                             headers={"X-Subscription-Token": key, "Accept": "application/json"}, params={"q": q, "count": n})
            r.raise_for_status()
            j = r.json()
            res = j.get("results", []) if news else (j.get("web") or {}).get("results", [])
            rows = [dict(title=x.get("title"), url=x.get("url"), snippet=x.get("description"), published=x.get("page_age") or x.get("age"), provider="Brave")
                    for x in res]
        elif p == "serper":
            r = requests.post(f"https://google.serper.dev/{'news' if news else 'search'}", timeout=40, headers={"X-API-KEY": key},
                              json={"q": q, "num": n, "gl": "in"})
            r.raise_for_status()
            j = r.json()
            rows = [dict(title=x.get("title"), url=x.get("link"), snippet=x.get("snippet"), published=x.get("date"), provider="Serper")
                    for x in (j.get("news") if news else j.get("organic")) or []]
        elif p == "exa":
            r = requests.post("https://api.exa.ai/search", timeout=40, headers={"x-api-key": key},
                              json={"query": q, "numResults": n, "type": "auto", "contents": {"text": {"maxCharacters": 1500}}})
            r.raise_for_status()
            rows = [dict(title=x.get("title"), url=x.get("url"), snippet=x.get("text"), published=x.get("publishedDate"), provider="Exa")
                    for x in r.json().get("results", [])]
        else:
            raise WebSearchError(f"unknown provider {p}")
    except WebSearchError:
        raise
    except Exception as e:
        msg = str(e)
        if key:
            msg = msg.replace(key, "…")
        raise WebSearchError(f"{PROVIDERS[p][0]} search failed: {type(e).__name__}: {msg[:160]}") from None
    _count(p)
    return _norm(rows)[:n]


def google_news(query, days=30):
    """No-key fallback: Google News RSS headlines (robots.txt checked)."""
    from .news_sources import fetch_gnews, Polite
    items = fetch_gnews(query, Polite(gap=2.0), days=days, exact=False)
    return [dict(title=i["title"], url=i["link"], snippet=i.get("summary", ""), published=str(i.get("published"))[:19], provider="Google News")
            for i in items[:15]]
