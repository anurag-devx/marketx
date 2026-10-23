"""News article text: for stored headlines, read the article page where the site allows it, keep the main text in
the documents library (kind 'news'), and re-link the headline to stocks using the article, not just the headline.

Polite and lawful by construction:
- robots.txt is checked for every article; disallowed pages are skipped and recorded as 'blocked'.
- one request per site every ~12 seconds (or the site's crawl-delay if longer); a run stops at `max_articles`.
- paywalls are never bypassed: a page that only shows a teaser / subscribe prompt is recorded as 'paywall'.
- Google News redirect links are not followed.
The text stays on this Mac for your own research.

Status per headline (data/news/articles_state.parquet): ok, paywall, blocked, short, error, redirect."""
import re, time
from collections import Counter
from urllib.parse import urlparse
import pandas as pd
from .config import DATA
from . import atomic

STATE = DATA / "news" / "articles_state.parquet"
GAP = 12.0
MIN_CHARS = 700
PAYWALL_RX = re.compile(r"subscribe to (read|continue|unlock)|this (story|article) is for (subscribers|members)|already a subscriber|"
                        r"exclusive (for|to) (prime|premium) (members|subscribers)|unlock this (story|article)|sign in to read|"
                        r"premium (story|article)|et prime|bs premium|mint premium|start your subscription", re.I)


def load_state():
    if STATE.exists():
        return pd.read_parquet(STATE)
    return pd.DataFrame(columns=["id", "status", "chars", "fetched", "note"])


def _save_state(d):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    atomic.to_parquet(d, STATE)


def classify(html, text):
    """'ok', 'paywall' or 'short' for an extracted page."""
    if len(text) >= MIN_CHARS:
        if PAYWALL_RX.search(text[-600:]) and len(text) < 2500:
            return "paywall"
        return "ok"
    return "paywall" if PAYWALL_RX.search(html or "") else "short"


def relink(title, text, tagger):
    """Stocks a story is about: named in the headline, or named at least twice in the article's opening."""
    head = tagger.find(title)
    body = text[:2500]
    counts = Counter()
    for s in tagger.find(body):
        counts[s] = 0
    if tagger.name_rx:
        for m in tagger.name_rx.finditer(body):
            t = m.group(1)
            if " " in t or t[0].isupper():
                counts[tagger.alias[t.lower()]] += 1
    if tagger.tick_rx:
        for m in tagger.tick_rx.finditer(body):
            counts[m.group(1)] += 1
    extra = [s for s, c in counts.most_common() if c >= 2 and s not in head]
    return (head + extra)[:6]


def _interleave(rows):
    """Order rows so consecutive requests go to different sites (less waiting per site)."""
    by = {}
    for r in rows:
        by.setdefault(urlparse(r["link"]).netloc, []).append(r)
    out = []
    while any(by.values()):
        for h in list(by):
            if by[h]:
                out.append(by[h].pop(0))
    return out


def fetch(max_articles=300, days=None, linked_first=True, log=print, polite=None):
    """Reads article text for headlines not tried yet. Returns a status count."""
    from . import news, library
    from .news_sources import Polite
    store = news.load_store()
    if store.empty:
        log("No headlines stored yet.")
        return {}
    st = load_state()
    tried = set(st.id)
    d = store[~store.id.isin(tried) & store.link.fillna("").str.startswith("http")].copy()
    if days:
        d = d[d.published >= pd.Timestamp.now() - pd.Timedelta(days=days)]
    if linked_first:
        d["_l"] = d.symbols.fillna("").ne("")
        d = d.sort_values(["_l", "published"], ascending=[False, False])
    rows = _interleave(d.head(max_articles * 2).to_dict("records"))[:max_articles]
    log(f"{len(rows)} articles to try ({len(store) - len(d)} already tried or without a link)")
    polite = polite or Polite(gap=GAP)
    tagger, uni = news.load_tagger()
    new_state, relinked, counts = [], {}, Counter()
    for i, r in enumerate(rows, 1):
        url = r["link"]
        status, chars, note = "error", 0, ""
        if "news.google.com" in url:
            status, note = "redirect", "Google News link (not followed)"
        else:
            try:
                html = polite.get(url).decode("utf-8", "ignore")
                title, text, date = library.extract_page(html, url)
                status, chars = classify(html, text), len(text)
                if status == "ok":
                    library.add_parts("news", url, r["title"], [(text, "")], published=str(r["published"])[:19], rebuild_fts=False)
                    syms = relink(r["title"], text, tagger)
                    if ",".join(syms) != (r.get("symbols") or ""):
                        relinked[r["id"]] = syms
            except PermissionError:
                status, note = "blocked", "robots.txt disallows"
            except Exception as e:
                status, note = "error", f"{type(e).__name__}"
        counts[status] += 1
        new_state.append(dict(id=r["id"], status=status, chars=chars, fetched=str(pd.Timestamp.now().floor("s")), note=note))
        if i % 25 == 0:
            log(f"  {i}/{len(rows)}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
            _flush(st, new_state, relinked, tagger, uni)
            st, new_state, relinked = load_state(), [], {}
    _flush(st, new_state, relinked, tagger, uni)
    try:
        library._fts(library._table())
    except Exception:
        pass
    log("Articles: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return dict(counts)


def _flush(st, new_state, relinked, tagger, uni):
    from . import news
    if new_state:
        _save_state(pd.concat([st, pd.DataFrame(new_state)], ignore_index=True).drop_duplicates("id", keep="last"))
    if relinked:
        from .filelock import locked
        with locked("news"):
            _relink_store(relinked, uni)


def _relink_store(relinked, uni):
    from . import news
    store = news.load_store()
    sec = dict(zip(uni.Symbol, uni.Industry))
    m = store.id.isin(relinked)
    store.loc[m, "symbols"] = store.loc[m, "id"].map(lambda i: ",".join(relinked[i]))
    store.loc[m, "sectors"] = store.loc[m, "id"].map(lambda i: ",".join(sorted({sec.get(s, "") for s in relinked[i]} - {""})))
    store.loc[m, "roundup"] = store.loc[m, "id"].map(lambda i: len(relinked[i]) >= 4) | store.loc[m, "title"].map(lambda t: bool(news.ROUNDUP_RX.search(t)))
    news.save_store(store)


def summary():
    st = load_state()
    return st.status.value_counts().to_dict() if len(st) else {}


if __name__ == "__main__":
    import sys
    a = dict(x.split("=", 1) for x in sys.argv[1:] if "=" in x)
    fetch(max_articles=int(a.get("max", 300)), days=int(a["days"]) if "days" in a else None)
