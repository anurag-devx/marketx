"""API: Market page (highlights, news, catalyst)."""
import numpy as np, pandas as pd
from core import config as C
from web.api import route, cached, _mtime, prices, index, universe, market_panel, UserError

RANGES = {"3M": 91, "6M": 182, "1Y": 365, "3Y": 1096, "5Y": 1827, "10Y": 3653, "All": None}
MCOLS = [["symbol", "Stock", "sym"], ["close", "Close", "inr"], ["r1", "Day", "spct2"], ["r21", "Month", "spct1"], ["vol_ratio", "Vol ×", "x1"]]


def _breadth():
    from core import market as mk
    p = C.DATA / "prices.parquet"
    return cached("breadth", _mtime(p), lambda: mk.breadth(market_panel()))


def _f(d):
    return None if d is None or pd.isna(d) else d


@route("GET", "market/highlights")
def highlights(p):
    from core import market as mk
    panel = market_panel()
    if panel is None:
        raise UserError("no price data yet")
    as_of = p.get("as_of") or None
    d, sec, s = mk.snapshot(panel, universe(), as_of)
    day = pd.Timestamp(s["date"])
    rng = RANGES.get(p.get("range", "1Y"), 365)
    start = panel.date.min() if rng is None else day - pd.Timedelta(days=rng)
    ix = index()
    ix = ix[(ix.date >= start) & (ix.date <= day)].dropna(subset=["nifty"]).copy() if ix is not None else pd.DataFrame()
    full = index()
    full = full.dropna(subset=["nifty"]).assign(sma200=lambda x: x.nifty.rolling(200).mean()) if full is not None else full
    ix = full[(full.date >= start) & (full.date <= day)] if full is not None else ix
    br = _breadth()
    br = br[(br.date >= start) & (br.date <= day)]
    per = p.get("period", "day")
    sv = sec[sec.stocks >= 3].sort_values(per, ascending=False)
    chg = 100 * (ix.nifty.iat[-1] / ix.nifty.iat[0] - 1) if len(ix) > 1 else None
    d = d.rename(columns={"company": "company"})
    out = [
        dict(kind="kpis", items=[dict(label="Rose / fell", value=f"{s['up']} / {s['down']}"), dict(label="Up more than 1%", value=str(s["up1"])),
                                 dict(label="Down more than 1%", value=str(s["down1"])), dict(label="New 52-week highs", value=str(s["new_highs"])),
                                 dict(label="New 52-week lows", value=str(s["new_lows"])),
                                 dict(label="Above 200-day average", value=f"{s['above200']:.0f}%" if s["above200"] == s["above200"] else "—")]),
        dict(kind="grid", cols=2, items=[
            dict(kind="chart", type="line", title="Nifty 50", aside=f"{chg:+.1f}% over the range" if chg is not None else "",
                 x=ix.date.dt.strftime("%Y-%m-%d").tolist(), series=[dict(name="Nifty 50", y=ix.nifty.round(1).tolist(), area=True),
                                                                    dict(name="200-day average", y=ix.sma200.round(1).tolist(), dash=True, width=1.2)]),
            dict(kind="chart", type="line", title="Share of stocks above their 200-day average", ysuffix="%", hlines=[50],
                 x=br.date.dt.strftime("%Y-%m-%d").tolist(), series=[dict(name="", y=br.pct_above_200.round(1).tolist(), area=True)])]),
        dict(kind="chart", type="hbar", title=f"Sectors: average move of their stocks ({per})", labels=sv.sector.tolist(),
             values=sv[per].round(2).tolist(), height=None),
        dict(kind="grid", cols=2, items=[
            dict(kind="table", title="Top gainers", columns=MCOLS, rows=d.nlargest(10, "r1"), tools=False),
            dict(kind="table", title="Top losers", columns=MCOLS, rows=d.nsmallest(10, "r1"), tools=False),
            dict(kind="table", title="Unusual volume (3× normal or more)", columns=MCOLS,
                 rows=d[d.vol_ratio >= 3].sort_values("vol_ratio", ascending=False).head(15), tools=False, empty="None that day."),
            dict(kind="table", title="New 52-week highs", columns=MCOLS, rows=d[d.new_high].sort_values("r1", ascending=False).head(15),
                 tools=False, empty="None that day.")]),
    ]
    return dict(day=day, first=panel.date.min(), last=panel.date.max(), sections=out)


@route("GET", "market/news")
def market_news(p):
    from core import news as N
    nw, errs, when = N.load(days=int(p.get("days", 7)))
    if nw is None or nw.empty:
        return dict(when=when, errors=errs, items=[], kinds=[])
    kinds = sorted(nw.kind.fillna("").replace("", "other").unique())
    v = nw.assign(kind=nw.kind.fillna("").replace("", "other"))
    if p.get("kinds"):
        v = v[v.kind.isin(p["kinds"].split(","))]
    q = p.get("q", "").strip()
    if q:
        v = v[v.title.str.contains(q, case=False, regex=False) | v.summary.fillna("").str.contains(q, case=False, regex=False)]
    if p.get("tagged") == "true":
        v = v[v.symbols.fillna("") != ""]
    v = v.sort_values("published", ascending=False).head(int(p.get("limit", 150)))
    items = [dict(title=r.title, link=r.link, source=r.source, published=r.published, symbols=r.symbols, type=r.event_type,
                  type_why=getattr(r, "type_why", ""), tone=r.tone, tone_why=getattr(r, "tone_why", ""), roundup=bool(r.roundup),
                  summary=r.summary) for r in v.itertuples()]
    return dict(when=when, errors=errs, items=items, kinds=kinds, total=len(nw))


# ---------------- catalyst ----------------
def _moves_panel():
    from research import daily_moves as DM
    from core.quality import apply_corporate_actions
    p = C.DATA / "prices.parquet"
    def build():
        px, _ = apply_corporate_actions(prices())
        return DM.panel(px, index())
    return cached("dm_panel", _mtime(p), build)


def _company_info():
    from core import company_info
    return cached("cinfo", _mtime(company_info.FILE), company_info.load)


def _cat_index(fil):
    from core import news as N, announcements as A
    from research import catalyst as CA
    def build():
        nw = N.load_store()
        tagger, _ = N.load_tagger()
        return CA.Index(CA.build_docs(nw, A.load() if fil else None), tagger), tagger
    return cached(f"cat_ix_{fil}", (_mtime(N.STORE), _mtime(A.STORE)), build)


@route("GET", "catalyst/recent")
def catalyst_recent(_):
    from core import news as N
    nw = N.load_store()
    if nw.empty:
        return []
    r = nw[(nw.published >= nw.published.max() - pd.Timedelta(days=7)) & ~nw.roundup.fillna(False).astype(bool)]
    r = r.sort_values("published", ascending=False).head(200)
    return [dict(title=x.title, published=x.published) for x in r.itertuples()]


@route("GET", "catalyst")
def catalyst(p):
    from core import news as N
    from research import daily_moves as DM, catalyst as CA
    text = p.get("text", "").strip()
    if not text:
        raise UserError("type or pick a headline")
    when = pd.Timestamp(p["when"]) if p.get("when") else None
    fil = p.get("filings", "true") == "true"
    ix, tagger = _cat_index(fil)
    if len(ix.docs) == 0:
        return dict(sections=[dict(kind="note", tone="warn", text="No stored news yet. Fetch news, and download older history in Data health.")])
    et = N.event_type(text) or "Other"
    named, secs = tagger.find(text), DM.sectors_in(text)
    cutoff = (when - pd.Timedelta(hours=12)) if when is not None else None
    sim = ix.search(text, k=int(p.get("k", 100)), min_score=float(p.get("min_score", 0.15)), event_type=et, exclude_after=cutoff)
    if p.get("same") == "true" and et != "Other":
        sim = sim[sim.event_type == et]
    res = CA.impact(sim, _moves_panel(), _company_info())
    nd = res["days"]
    head = dict(kind="note", tone="info", text=f"Type: {et} · names: {', '.join(named) or 'no stock'} · sector words: {', '.join(secs) or 'none'}")
    if sim.empty or nd == 0:
        return dict(sections=[head, dict(kind="note", tone="warn", text="No similar items with price data. Lower the minimum similarity, or download more news history.")])
    n_used = len(res["similar"])
    out = [head, dict(kind="note", tone="warn" if nd < 10 else "info",
                      text=f"{n_used} similar items on {nd} trading days" + (" — too few to rely on; treat as examples." if nd < 10 else "."))]
    lines = _likely(res, named, nd)
    if lines:
        out.append(dict(kind="md", title="Likely affected, going by the past", text="\n".join(f"- {x}" for x in lines)))
    s = res["sectors"]
    items = []
    if len(s):
        s2 = s.sort_values("event_day", ascending=False)
        items.append(dict(kind="chart", type="hbar", title="Sectors on those days (median stock vs Nifty)", labels=s2.sector.tolist(), values=s2.event_day.round(2).tolist()))
    items.append(dict(kind="table", title="The named stock and its industry peers", rows=res["peers"], tools=False,
                      columns=[[c, c.replace("_", " "), "snum2" if c in ("avg_move",) else "num2" if c in ("avg_size", "t") else "text"] for c in res["peers"].columns] if len(res["peers"]) else [],
                      empty="The similar items don't name stocks (sector or economy news)."))
    out.append(dict(kind="grid", cols=2, items=items))
    out.append(dict(kind="table", title="By size (median stock vs Nifty)", rows=res["sizes"], tools=False,
                    columns=[[c, c.replace("_", " "), "snum2" if c in ("event_day", "next_5") else "num1" if c.startswith("t") else "text"] for c in res["sizes"].columns]))
    if len(res["stocks"]):
        out.append(dict(kind="table", title="Stocks that moved most consistently on those days", rows=res["stocks"].head(40),
                        columns=[["symbol", "Stock", "sym"], ["event_day", "Avg on event day", "spct2"], ["t_day", "t", "num1"],
                                 ["up_days_pct", "Up on % of days", "pct0"], ["next_5", "Avg next 5 days", "spct2"], ["days", "Days", "int"]],
                        note="With ~500 stocks tested, about 25 show |t| above 2 by chance alone. Trust a stock only when its sector agrees and there are many days."))
    sim2 = res["similar"].copy()
    out.append(dict(kind="table", title="The similar past items", rows=sim2.assign(when=sim2.published), csv="similar_items",
                    columns=[["when", "When", "date"], ["kind", "Kind"], ["event_type", "Type"], ["text", "Headline", "long"], ["symbols", "Stocks"],
                             ["score", "Similarity", "num2"], ["link", "", "link"]]))
    return dict(sections=out)


def _likely(res, named, nd):
    out = []
    p = res["peers"]
    if len(p):
        r = p.iloc[0]
        if named:
            out.append(f"**{', '.join(named)}** (named): stocks named in similar items moved **{r.avg_move:+.1f}%** vs Nifty on average "
                       f"(typical size {r.avg_size:.1f}%) over {int(r.days)} days.")
        if len(p) > 2:
            pe = p.iloc[2]
            if pd.notna(pe.t) and abs(pe.t) >= 2:
                out.append(f"Industry peers of the named stock moved **{pe.avg_move:+.2f}%** on the day (t {pe.t:.1f}).")
    s = res["sectors"]
    if len(s):
        sig = s[(s.t_day.abs() >= 2) & (s.days >= 5)]
        if len(sig):
            out.append("Sectors that moved consistently: " + ", ".join(f"**{r.sector}** {r.event_day:+.2f}% (t {r.t_day:.1f})" for r in sig.head(5).itertuples()))
    stx = res["stocks"]
    if len(stx):
        sig = stx[(stx.t_day.abs() >= 3) & (stx.days >= 8)]
        if len(sig):
            out.append("Stocks with the most consistent moves: " + ", ".join(f"{r.symbol} {r.event_day:+.1f}%" for r in sig.head(8).itertuples()))
    if not out and nd >= 10:
        out.append("No sector or stock moved consistently on these days: past news like this didn't have a clear market-wide effect.")
    return out
