"""Daily moves: how many stocks moved more than 1 / 2 / 5 % up or down each day, and for any day the full list
with sector, industry, size, volume and the catalyst (news / filing about the stock, its sector, or a move shared
with its industry).

Moves are close-to-close returns. 'vs Nifty' = the stock's return minus the Nifty 50's.
Catalyst matching uses the same event-day rule as the news study: an item published after 15:30 or on a holiday
counts for the next trading day; the previous day's items are also shown (news often lands the evening before)."""
import re
import numpy as np, pandas as pd
from research.events import event_day

THRESHOLDS = (1, 2, 5)

# words in a headline that point at a whole sector (Nifty 500 industry names). Rough on purpose; shown as "sector news".
SECTOR_WORDS = {
    "Financial Services": r"\bbanks?\b|banking|lenders?|\bnbfcs?\b|microfinance|insurers?|insurance|\bamcs?\b|mutual funds?|credit growth|deposit rates?|repo rate|\brbi\b",
    "Information Technology": r"\bit (stocks|sector|services|companies|firms|majors)\b|software|tech services|\bh-?1b\b|nasdaq|\bai spending\b",
    "Healthcare": r"pharma|drug ?makers?|\busfda\b|\bfda\b|hospitals?|healthcare|generics?|tariffs? on (drugs|pharma)",
    "Automobile and Auto Components": r"\bauto (stocks|sector|sales|makers|companies)\b|automakers?|carmakers?|two-wheelers?|passenger vehicles?|\bevs?\b|electric vehicles?|tractors?|auto ancillar",
    "Fast Moving Consumer Goods": r"\bfmcg\b|consumer staples|rural demand|cigarettes?|edible oil",
    "Metals & Mining": r"\bmetals?\b|steel|aluminium|aluminum|copper|zinc|iron ore|mining|coal",
    "Oil Gas & Consumable Fuels": r"crude|oil prices?|brent|\bomcs?\b|refin(ers?|ing)|natural gas|\blng\b|petrol|diesel|fuel prices?",
    "Power": r"\bpower (stocks|sector|demand|tariffs?|companies)\b|electricity|renewables?|solar|wind energy|thermal power|power exchange",
    "Realty": r"realty|real estate|housing sales|property (market|prices)|home loans?",
    "Capital Goods": r"capital goods|capex|defen[cs]e (stocks|orders|sector|ministry|exports)|railways? (stocks|orders|budget)|infra(structure)? (spending|orders)",
    "Construction Materials": r"cement",
    "Telecommunication": r"telecom|tariff hikes?|\b5g\b|spectrum",
    "Chemicals": r"chemicals?|agrochem|fertili[sz]ers?",
    "Textiles": r"textiles?|apparel|cotton",
    "Consumer Durables": r"consumer durables|air conditioners?|jewellery|gold prices?",
    "Consumer Services": r"\bretail(ers)?\b|quick commerce|e-?commerce|airlines?|aviation|hotels?|tourism",
    "Media Entertainment & Publication": r"\bmedia\b|broadcast|box office|ott\b",
}
_SECTOR_RX = {k: re.compile(v, re.I) for k, v in SECTOR_WORDS.items()}


def sectors_in(text):
    t = str(text or "")
    return [k for k, rx in _SECTOR_RX.items() if rx.search(t)]


def panel(px, idx):
    close = px.pivot_table(index="date", columns="symbol", values="close").sort_index()
    vol = px.pivot_table(index="date", columns="symbol", values="volume").reindex(close.index)
    ret = 100 * close.pct_change(fill_method=None)
    n = idx.dropna(subset=["nifty"]).set_index("date").nifty.reindex(close.index)
    nret = 100 * n.pct_change(fill_method=None)
    base_vol = vol.shift(1).rolling(20, min_periods=10).median()
    tv = (close * vol / 1e7)                               # traded value, ₹ crore
    avg_tv = tv.shift(1).rolling(20, min_periods=10).mean()
    return dict(close=close, vol=vol, ret=ret, nret=nret, rel_vol=vol / base_vol, tv=tv, avg_tv=avg_tv, dates=close.index)


def daily_counts(P, min_avg_tv=0.0, vs_nifty=False):
    """One row per day: stocks traded, and how many moved more than 1/2/5 % up and down."""
    r = P["ret"].sub(P["nret"], axis=0) if vs_nifty else P["ret"]
    ok = r.notna()
    if min_avg_tv > 0:
        ok &= P["avg_tv"] >= min_avg_tv
    r = r.where(ok)
    out = pd.DataFrame({"stocks": ok.sum(axis=1)})
    for t in THRESHOLDS:
        out[f"up_{t}"] = (r > t).sum(axis=1)
        out[f"down_{t}"] = (r < -t).sum(axis=1)
    out["nifty_pct"] = P["nret"]
    out["median_move"] = r.median(axis=1)
    out = out[out.stocks > 0]
    out.index.name = "date"
    return out.reset_index()


def _events_by_day(news, ann, dates):
    """{(symbol, day): [labels]}, {day: [(sectors, headline)]} for news without a stock (sector / macro news)."""
    stock, sector = {}, {}
    if news is not None and len(news):
        for r in news.itertuples():
            d = event_day(r.published, dates)
            if pd.isna(d):
                continue
            syms = [s for s in str(r.symbols or "").split(",") if s]
            if syms and not bool(r.roundup):
                for s in syms:
                    stock.setdefault((s, d), []).append(("News", r.title, r.link))
            elif not syms:
                secs = sectors_in(r.title)
                if secs:
                    sector.setdefault(d, []).append((secs, r.title, r.link))
    if ann is not None and len(ann):
        for r in ann.itertuples():
            if not r.symbol:
                continue
            d = event_day(r.published, dates)
            if pd.notna(d):
                txt = r.subject if not r.text or r.text == r.subject else f"{r.subject}: {r.text}"
                stock.setdefault((r.symbol, d), []).append(("Filing", txt[:200], r.link))
    return stock, sector


class Catalysts:
    """Built once per news/filings store; answers 'what was the catalyst for this stock on this day'."""
    def __init__(self, news, ann, dates):
        self.dates = pd.DatetimeIndex(dates)
        self.stock, self.sector = _events_by_day(news, ann, self.dates)

    def for_day(self, day):
        day = pd.Timestamp(day)
        i = self.dates.get_loc(day)
        prev = self.dates[i - 1] if i > 0 else None
        return day, prev

    def stock_items(self, sym, day, prev):
        return self.stock.get((sym, day), []) + [(k + " (day before)", t, l) for k, t, l in self.stock.get((sym, prev), [])]

    def sector_items(self, sector, day):
        return [(t, l) for secs, t, l in self.sector.get(day, []) if sector in secs]


def day_table(P, day, info, cat=None, min_avg_tv=0.0):
    """Every stock on `day`: sector, industry, move, vs Nifty, volume × normal, traded value, market cap, catalyst."""
    day = pd.Timestamp(day)
    r = P["ret"].loc[day]
    d = pd.DataFrame({"symbol": r.index, "move_pct": r.values})
    d = d.dropna(subset=["move_pct"])
    d["vs_nifty_pct"] = d.move_pct - P["nret"].loc[day]
    d["close"] = d.symbol.map(P["close"].loc[day])
    d["volume"] = d.symbol.map(P["vol"].loc[day])
    d["volume_x_normal"] = d.symbol.map(P["rel_vol"].loc[day])
    d["traded_cr"] = d.symbol.map(P["tv"].loc[day])
    d["avg_traded_cr"] = d.symbol.map(P["avg_tv"].loc[day])
    if min_avg_tv > 0:
        d = d[d.avg_traded_cr >= min_avg_tv]
    inf = info.set_index("symbol")
    for c in ("company", "sector", "industry", "basic_industry", "nifty_industry"):
        d[c] = d.symbol.map(inf[c]) if c in inf else ""
    d["sector"] = d.sector.fillna("Other")
    d["industry"] = d.industry.fillna(d.sector)
    sh = d.symbol.map(inf["shares"]) if "shares" in inf else pd.Series(np.nan, index=d.index)
    d["market_cap_cr"] = pd.to_numeric(sh, errors="coerce") * d.close / 1e7
    # moves shared with the industry: same sign and the industry's median move beyond 1%
    grp = d.groupby("industry").move_pct
    d["industry_median_pct"] = grp.transform("median")
    d["industry_stocks"] = grp.transform("size")
    d["catalyst"], d["catalyst_type"], d["catalyst_link"] = "", "none found", ""
    key = d.symbol.map(inf["nifty_industry"]).fillna(d.sector) if "nifty_industry" in inf else d.sector
    if cat is not None and day in cat.dates:
        day_, prev = cat.for_day(day)
        cs, ts, ls = [], [], []
        for s, k, ind_med, n_ind, mv in zip(d.symbol, key, d.industry_median_pct, d.industry_stocks, d.move_pct):
            items = cat.stock_items(s, day_, prev)
            sitems = cat.sector_items(k, day_)
            together = n_ind >= 3 and abs(ind_med) >= 0.75 and np.sign(ind_med) == np.sign(mv)
            if items:
                txt, typ, link = " | ".join(f"{a}: {t}" for a, t, _ in items[:3]), "stock news / filing", items[0][2]
            elif sitems and together:
                txt, typ, link = " | ".join(f"Sector: {t}" for t, _ in sitems[:2]), "sector news", sitems[0][1]
            elif together:
                txt, typ, link = f"Moved with its industry (median {ind_med:+.1f}% across {int(n_ind)} stocks)", "industry move", ""
            elif sitems:
                txt, typ, link = " | ".join(f"Sector headline: {t}" for t, _ in sitems[:1]), "sector headline only", sitems[0][1]
            else:
                txt, typ, link = "", "none found", ""
            cs.append(txt); ts.append(typ); ls.append(link)
        d["catalyst"], d["catalyst_type"], d["catalyst_link"] = cs, ts, ls
    return d.sort_values("move_pct", ascending=False).reset_index(drop=True)


def bucket(m):
    return np.select([m >= 5, m >= 2, m >= 1, m > -1, m > -2, m > -5], [">+5%", "+2 to +5%", "+1 to +2%", "-1 to +1%", "-2 to -1%", "-5 to -2%"], "< -5%")


def catalyst_share(P, info, cat, days, threshold=2.0, min_avg_tv=0.0):
    """Across `days`: of stocks moving more than ±threshold, what share had each catalyst type."""
    rows = []
    for d in days:
        t = day_table(P, d, info, cat, min_avg_tv)
        t = t[t.move_pct.abs() > threshold]
        if len(t):
            rows.append(t.catalyst_type.value_counts().rename(pd.Timestamp(d)))
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).fillna(0).astype(int)
