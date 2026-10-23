"""News and announcement screeners: filter headlines / filings and see which stocks are being talked about.

Tone is a rough keyword label on the headline (positive / negative / neutral words), shown as such: it is a
reading aid, not sentiment analysis and not a signal. Stock tags come from core/news.tag (ticker or a
distinctive part of the company name in the headline), so they can miss or over-match."""
import re
import numpy as np, pandas as pd

from core.news import tone  # noqa: E402  (one tone rule for the whole project)


def prepare_news(news, universe):
    """news: core.news.load() frame. Adds published (datetime), symbols (list), sectors (list), tone."""
    from core import news as N
    if news is None or news.empty:
        return pd.DataFrame(columns=["published", "source", "kind", "title", "summary", "link", "symbols", "sectors", "tone", "event_type", "roundup"])
    d = N.tag(news, universe).copy()
    d["published"] = pd.to_datetime(d.published, errors="coerce")
    d["symbols"] = d.symbols.fillna("").map(lambda s: [x for x in s.split(",") if x] if isinstance(s, str) else list(s))
    d["sectors"] = d.sectors.fillna("").map(lambda s: [x for x in s.split(",") if x] if isinstance(s, str) else list(s))
    if "tone" not in d:
        d["tone"] = d.title.map(tone)
    return d


def filter_items(d, since=None, sources=(), kinds=(), keywords="", symbols=(), sectors=(), tones=(), only_tagged=False,
                 text_cols=("title", "summary")):
    """Common filter for news and announcements. keywords: comma-separated, any may match (case-insensitive)."""
    if d is None or d.empty:
        return d
    m = pd.Series(True, index=d.index)
    if since is not None:
        m &= d.published >= pd.Timestamp(since)
    if sources:
        m &= d.source.isin(sources)
    if kinds and "kind" in d:
        m &= d.kind.isin(kinds)
    kws = [k.strip() for k in str(keywords or "").split(",") if k.strip()]
    if kws:
        pat = re.compile("|".join(re.escape(k) for k in kws), re.I)
        txt = d[[c for c in text_cols if c in d]].fillna("").astype(str).agg(" ".join, axis=1)
        m &= txt.str.contains(pat)
    if symbols:
        s = set(symbols)
        m &= d.symbols.map(lambda xs: bool(s & set(xs if isinstance(xs, (list, np.ndarray)) else [xs])))
    if sectors:
        s = set(sectors)
        m &= d.sectors.map(lambda xs: bool(s & set(xs)))
    if tones and "tone" in d:
        m &= d.tone.isin(tones)
    if only_tagged:
        m &= d.symbols.map(len) > 0
    return d[m]


def stocks_in_items(d, snap=None, shortlist=()):
    """One row per stock mentioned: how many items, latest item, plus the day's move and shortlist membership."""
    if d is None or d.empty:
        return pd.DataFrame(columns=["symbol", "items", "latest", "latest_item"])
    e = d.explode("symbols").dropna(subset=["symbols"])
    e = e[e.symbols != ""]
    if e.empty:
        return pd.DataFrame(columns=["symbol", "items", "latest", "latest_item"])
    title_col = "title" if "title" in e else "text"
    g = e.sort_values("published", ascending=False).groupby("symbols")
    out = g.agg(items=(title_col, "size"), latest=("published", "max"), latest_item=(title_col, "first")).reset_index()
    out = out.rename(columns={"symbols": "symbol"})
    if "tone" in e:
        t = e.groupby("symbols").tone.agg(lambda x: f"{(x == 'positive').sum()}+ / {(x == 'negative').sum()}-")
        out["tone_pos_neg"] = out.symbol.map(t)
    if "category" in e:
        out["categories"] = out.symbol.map(e.groupby("symbols").category.agg(lambda x: ", ".join(sorted(set(x)))))
    if snap is not None and len(snap):
        s = snap.set_index("symbol")
        out["sector"] = out.symbol.map(s.sector)
        out["change_1d_pct"] = out.symbol.map(s.change_1d_pct)
        out["rel_volume"] = out.symbol.map(s.rel_volume)
    out["on_shortlist"] = out.symbol.isin(set(shortlist))
    return out.sort_values(["items", "latest"], ascending=[False, False]).reset_index(drop=True)


def prepare_announcements(a, universe):
    """Announcements store -> same shape the filters expect (symbols as a list, sectors, title)."""
    if a is None or a.empty:
        return pd.DataFrame(columns=["published", "symbol", "company", "subject", "text", "category", "link", "source", "symbols", "sectors", "title"])
    d = a.copy()
    sec = dict(zip(universe.Symbol, universe.Industry))
    d["symbols"] = d.symbol.fillna("").map(lambda s: [s] if s else [])
    d["sectors"] = d.symbol.map(lambda s: [sec[s]] if s in sec else [])
    d["title"] = d.company.fillna("") + ": " + d.subject.where(d.subject.fillna("") != "", d.text.fillna("").str[:140])
    d["in_nifty500"] = d.symbol.isin(set(universe.Symbol))
    return d
