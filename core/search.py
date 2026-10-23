"""Search: one box for stocks, sectors, themes and free text.

resolve(query)  -> what the query is about: stocks (names, tickers, aliases), sectors / industries, geopolitical or
                   macro themes, or plain text.
dossier(...)    -> everything stored about a stock: filings, its news (+ article text), sector and industry news,
                   peers, macro sensitivity (computed) and your exposure list, current geopolitical themes that reach it
                   through those exposures, document passages, upcoming events.
Exposure list (data/search/exposures.csv): your own map of themes / factors -> stocks or sectors (hurt / helped / mixed)."""
import re
import numpy as np, pandas as pd
from .config import DATA, RESULTS
from . import atomic

DIR = DATA / "search"
EXPOSURES = DIR / "exposures.csv"
EXP_COLS = ["driver", "target", "effect", "note"]

# which macro factors a geopolitical theme acts through (used to connect themes to computed sector sensitivities)
THEME_FACTORS = {"middle_east": ["Brent crude", "Geopolitical risk"], "oil_supply": ["Brent crude"], "shipping": ["Brent crude"],
                 "sanctions": ["Brent crude"], "russia_ukraine": ["Brent crude", "Geopolitical risk"], "india_pakistan": ["Geopolitical risk"],
                 "china_taiwan": ["Geopolitical risk", "US VIX"], "us_tariffs": ["US dollar index", "USD/INR"],
                 "central_banks": ["US 10-year yield", "USD/INR"], "india_elections": ["FII net buying"]}


# ---------------- exposure list ----------------
def exposures():
    if EXPOSURES.exists():
        return pd.read_csv(EXPOSURES).fillna("")
    return pd.DataFrame(columns=EXP_COLS)


def save_exposures(d):
    DIR.mkdir(parents=True, exist_ok=True)
    d = d.fillna("")
    atomic.to_csv(d[(d.driver.astype(str).str.strip() != "") & (d.target.astype(str).str.strip() != "")], EXPOSURES)


def driver_options():
    from .macro import THEMES
    from research.macro_impact import FACTORS
    return [v[0] for v in THEMES.values()] + [v[0] for v in FACTORS.values()]


# ---------------- understanding the query ----------------
def resolve(query, info=None):
    from . import news, macro, company_info
    q = query.strip()
    info = company_info.load() if info is None else info
    tagger, uni = news.load_tagger()
    stocks = tagger.find(q)
    up = q.upper().replace(" ", "")
    if up in set(info.symbol) and up not in stocks:
        stocks.insert(0, up)
    secs, inds = [], []
    ql = q.lower()
    from research.daily_moves import sectors_in
    for v in sectors_in(q) + sectors_in(f"{q} stocks"):      # synonyms: cement, banks, pharma, IT, auto, metals...
        if v not in secs:
            secs.append(v)
    for col, out in (("sector", secs), ("industry", inds), ("basic_industry", inds), ("nifty_industry", secs)):
        for v in info[col].dropna().unique():
            v = str(v)
            vl = v.lower()
            hit = ql == vl or (len(ql) >= 4 and ql in vl) or (len(v.split()) >= 2 and vl in ql)
            if len(v) >= 4 and hit and v not in out:
                out.append(v)
    if not stocks and not secs and not inds and not macro.themes_in(q) and len(q) >= 3 and q.replace(" ", "").isalpha():   # groups: "Bajaj", "HDFC", "Tata"
        rx = re.compile(rf"\b{re.escape(q)}", re.I)
        group = [r.symbol for r in info.itertuples() if isinstance(r.company, str) and rx.search(r.company)]
        if 1 <= len(group) <= 15:
            stocks = group
    themes = macro.themes_in(q)
    for k, (label, _, _) in macro.THEMES.items():
        if ql in label.lower() and k not in themes and len(ql) >= 4:
            themes.append(k)
    kind = "stock" if stocks else "sector" if (secs or inds) else "theme" if themes else "text"
    return dict(query=q, kind=kind, stocks=stocks[:15], sectors=secs[:3], industries=inds[:3], themes=themes)


# ---------------- pieces ----------------
def stock_news(symbol, news, days=None):
    if news is None or news.empty:
        return news
    m = news.symbols.fillna("").str.split(",").map(lambda xs: symbol in xs)
    d = news[m]
    if days:
        d = d[d.published >= pd.Timestamp.now() - pd.Timedelta(days=days)]
    return d.sort_values("published", ascending=False)


def sector_news(sector, industry, peers, news, days=30, exclude=None):
    """Headlines naming a peer, or about the sector in general (sector words), excluding the stock itself."""
    from research.daily_moves import sectors_in
    if news is None or news.empty:
        return news
    d = news[news.published >= pd.Timestamp.now() - pd.Timedelta(days=days)]
    peer_set = set(peers) - {exclude}
    m_peer = d.symbols.fillna("").str.split(",").map(lambda xs: bool(peer_set & set(xs)) and exclude not in xs)
    m_sec = d.symbols.fillna("").eq("") & d.title.map(lambda t: sector in sectors_in(t))
    out = d[m_peer | m_sec].copy()
    out["why"] = np.where(m_peer[m_peer | m_sec], "peer: " + out.symbols.fillna(""), "sector news")
    return out.sort_values("published", ascending=False)


def _read_csv(path):
    try:
        return pd.read_csv(path) if path.exists() and path.stat().st_size > 2 else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def macro_links(symbol, sector, nifty_industry=None, days=7, peers=None):
    """How this week's geopolitical / macro themes reach one stock. Four routes, each labelled:
      named   the theme's headlines name the stock
      sector  they name its sector (sector words, e.g. crude -> oil & gas) or its industry peers
      factor  the theme moves a factor (crude, USD/INR, yields...) the stock or its sector is sensitive to (macro study)
      list    your exposure list maps the theme, or one of its factors, to the stock or its sector
    Returns (exposures table, themes table)."""
    from . import macro
    from research.daily_moves import sectors_in
    sens = _read_csv(RESULTS / "macro" / "sens_3y.csv")
    sst = _read_csv(RESULTS / "macro" / "sens_stocks_3y.csv")
    keys = {sector, nifty_industry} - {None, ""}
    rows = []
    for key in keys:
        if len(sens):
            for r in sens[sens.sector == key].itertuples():
                rows.append(dict(driver=r.factor, via=f"sector ({key})", per_1sd_pct=r.per_1sd_pct, t=r.t, source="computed"))
    if len(sst):
        for r in sst[sst.symbol == symbol].itertuples():
            rows.append(dict(driver=r.factor, via="this stock", per_1sd_pct=r.per_1sd_pct, t=r.t, source="computed"))
    ex = exposures()
    tl = ex.target.astype(str).str.strip()
    mine = ex[tl.str.upper().eq(symbol.upper()) | tl.str.lower().isin({k.lower() for k in keys})]
    for r in mine.itertuples():
        rows.append(dict(driver=r.driver, via=f"your list ({r.target})", effect=r.effect, note=r.note, source="your list"))
    links = pd.DataFrame(rows)
    moves = {r["label"]: r for r in macro.summary()}
    h = macro.headline_themes()
    themes = []
    if len(h):
        from . import news as N
        store = N.load_store()
        syms = dict(zip(store.title, store.symbols.fillna("")))
        recent = h[h.published >= pd.Timestamp.now() - pd.Timedelta(days=days)]
        peer_set = set(peers or []) - {symbol}
        for th, g in recent.groupby("theme"):
            label, factors = macro.THEMES[th][0], THEME_FACTORS.get(th, [])
            routes = []
            named = [t for t in g.title if symbol in str(syms.get(t, "")).split(",")]
            if named:
                routes.append(f"named in {len(named)} headline(s)")
            sec_hits = [t for t in g.title if keys & set(sectors_in(t))]
            peer_hits = [t for t in g.title if peer_set & set(str(syms.get(t, "")).split(","))]
            if sec_hits:
                routes.append(f"its sector named in {len(sec_hits)} headline(s)")
            if peer_hits:
                routes.append(f"peers named in {len(peer_hits)} headline(s)")
            if len(links):
                comp = links[(links.source == "computed") & links.driver.isin(factors)]
                for r in comp.itertuples():
                    if abs(r.t) >= 2:
                        mv = moves.get(r.driver)
                        now = f"; {r.driver} {mv['chg_20d']:+.1f}{'%' if mv['change_kind'] == '%' else ' pts'} over 20 days" if mv and mv.get("chg_20d") is not None and mv["change_kind"] != "total" else ""
                        routes.append(f"via {r.driver}: {r.per_1sd_pct:+.2f}% per typical move ({r.via}){now}")
                for r in links[links.source == "your list"].itertuples():
                    if r.driver == label or r.driver in factors:
                        routes.append(f"your list: {r.driver} → {r.effect or 'affected'}" + (f" ({r.note})" if r.note else ""))
            strength = ("strong" if named or any(x.startswith("via ") for x in routes) or any(x.startswith("your list") for x in routes)
                        else "indirect" if routes else "none")
            evidence = (named + sec_hits + peer_hits)[:2] or list(g.sort_values("published", ascending=False).title.head(2))
            themes.append(dict(theme=label, headlines=len(g), link=strength, reaches_stock_via="; ".join(dict.fromkeys(routes)) or "no link found",
                               linked=strength != "none", example=" / ".join(evidence)))
    act = pd.DataFrame(themes)
    if len(act):
        act["_o"] = act.link.map({"strong": 0, "indirect": 1, "none": 2})
        act = act.sort_values(["_o", "headlines"], ascending=[True, False]).drop(columns="_o")
    return links, act


STARTER_EXPOSURES = [
    # driver, target, effect, note  (general relationships to review; not computed from your data)
    ("Brent crude", "Oil Gas & Consumable Fuels", "mixed", "starter: refiners/OMCs squeezed when crude rises; upstream producers gain"),
    ("Brent crude", "ASIANPAINT", "hurt", "starter: crude-linked raw materials"),
    ("Brent crude", "INDIGO", "hurt", "starter: jet fuel is the largest cost"),
    ("Brent crude", "ONGC", "helped", "starter: upstream realisations rise with crude"),
    ("Brent crude", "BPCL", "hurt", "starter: marketing margins fall when crude rises faster than pump prices"),
    ("Brent crude", "HINDPETRO", "hurt", "starter: as above"),
    ("Brent crude", "IOC", "hurt", "starter: as above"),
    ("USD/INR", "Information Technology", "helped", "starter: dollar revenues, rupee costs"),
    ("USD/INR", "Healthcare", "helped", "starter: export-heavy pharma"),
    ("US 10-year yield", "Financial Services", "mixed", "starter: global rates drive FII flows into banks"),
    ("Middle East conflict", "Oil Gas & Consumable Fuels", "mixed", "starter: works through crude"),
    ("Red Sea / Hormuz shipping", "Capital Goods", "hurt", "starter: longer routes raise freight and input costs"),
    ("US tariffs / trade war", "Textiles", "hurt", "starter: US is a large export market"),
    ("US tariffs / trade war", "Healthcare", "mixed", "starter: generics exposure to US policy"),
    ("US tariffs / trade war", "Information Technology", "mixed", "starter: US demand and visa rules (H-1B)"),
    ("India–Pakistan", "Capital Goods", "helped", "starter: defence order expectations"),
    ("RBI / Fed policy", "Financial Services", "mixed", "starter: rate path changes margins and credit growth"),
    ("RBI / Fed policy", "Realty", "mixed", "starter: home-loan rates"),
]


def starter_rows():
    return pd.DataFrame(STARTER_EXPOSURES, columns=EXP_COLS)


def theme_view(theme, days=30):
    from . import macro
    h = macro.headline_themes()
    h = h[(h.theme == theme) & (h.published >= pd.Timestamp.now() - pd.Timedelta(days=days))].sort_values("published", ascending=False)
    factors = THEME_FACTORS.get(theme, [])
    sens = _read_csv(RESULTS / "macro" / "sens_3y.csv")
    exp_sec = sens[sens.factor.isin(factors)].copy() if len(sens) else pd.DataFrame()
    if len(exp_sec):
        exp_sec = exp_sec.reindex(exp_sec.t.abs().sort_values(ascending=False).index).head(12)
    sst = _read_csv(RESULTS / "macro" / "sens_stocks_3y.csv")
    exp_st = sst[sst.factor.isin(factors)].head(15) if len(sst) else pd.DataFrame()
    label = macro.THEMES[theme][0]
    mine = exposures()
    mine = mine[mine.driver == label]
    return dict(label=label, factors=factors, headlines=h, sectors=exp_sec, stocks=exp_st, mine=mine)
