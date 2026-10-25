"""Daily market report: the FACTS. Every number in the report is computed here, deterministically, from the
stored prices, Nifty/VIX, NSE filings and headlines. The writer (research/report_writer.py) only turns these
facts into prose and is checked against them.

Sections
  market    Nifty, VIX, breadth (advances/declines, >2% / >5% movers), median stock, % above 50/200-day averages,
            new 52-week highs/lows
  up/down   biggest gainers and losers among liquid stocks (avg traded value >= MIN_TV crore), with volume vs
            normal, 1-month return, sector, and any NSE filing / headline naming the stock that day
  sectors   every sector: median move, share of stocks up, traded-value-weighted move, 5- and 20-day trend;
            'most affected' = largest median move either way, 'least affected' = closest to flat
  new       new leaders (first 52-week high in 6 months, or newly in the top 50 by 3-month strength),
            Nifty 500 entries/exits, unusual volume, recent listings (under a year of history) and IPO headlines
  filings   NSE filings of the day by type, the notable ones (results, orders, deals) for liquid stocks
  news      market headlines of the day (not about one stock)
Usage: python3 -m research.market_report [YYYY-MM-DD]   (writes reports/market/<date>.json and .md)"""
import math, sys
import numpy as np, pandas as pd

MIN_TV = 5.0            # ₹ crore average daily traded value for a stock to appear in the gainer / loser lists
TOP_N = 12
NOTABLE = ("Results", "Order / contract", "Acquisition / merger", "Fund raising", "Dividend", "Buyback", "Bonus / split",
           "Rating", "Credit rating", "Litigation / regulatory", "Management change")


def _r(x, n=2):
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else round(float(x), n)


def _panel(px, idx):
    close = px.pivot_table(index="date", columns="symbol", values="close").sort_index()
    high = px.pivot_table(index="date", columns="symbol", values="high").reindex(close.index)
    low = px.pivot_table(index="date", columns="symbol", values="low").reindex(close.index)
    vol = px.pivot_table(index="date", columns="symbol", values="volume").reindex(close.index)
    ix = idx.set_index("date").reindex(close.index)
    return close, high, low, vol, ix


def build(day=None, px=None, idx=None, info=None, news=None, ann=None, uni_changes=None):
    """Returns the facts dict for `day` (default: the latest trading day in the data)."""
    from research.events import event_day
    close, high, low, vol, ix = _panel(px, idx)
    dates = close.index
    day = dates[-1] if day is None else pd.Timestamp(day)
    if day not in dates:
        day = dates[dates <= day][-1]
    i = dates.get_loc(day)
    if i < 260:
        raise ValueError("need about a year of history before the report day")
    ret = 100 * close.pct_change(fill_method=None)
    r0 = ret.iloc[i]
    traded = close * vol / 1e7
    avg_tv = traded.iloc[i - 20:i].mean()
    med_vol = vol.iloc[i - 20:i].median()
    volx = vol.iloc[i] / med_vol
    nifty = ix.nifty
    n_ret = 100 * (nifty.iloc[i] / nifty.iloc[i - 1] - 1)
    traded_today = r0.notna()
    liquid = traded_today & (avg_tv >= MIN_TV)
    inf = info.set_index("symbol")
    sector = inf.sector.reindex(close.columns).fillna("Other")
    company = inf.company.reindex(close.columns).fillna("")

    # ---------- market ----------
    sma50 = close.iloc[i - 49:i + 1].mean()
    sma200 = close.iloc[i - 199:i + 1].mean()
    hi52 = high.iloc[i - 251:i].max()
    lo52 = low.iloc[i - 251:i].min()
    new_hi = traded_today & (high.iloc[i] > hi52)
    new_lo = traded_today & (low.iloc[i] < lo52)
    r = r0[traded_today]
    vix = ix.vix if "vix" in ix else pd.Series(np.nan, index=dates)
    market = dict(
        date=str(day.date()), weekday=day.strftime("%A"), stocks_traded=int(traded_today.sum()),
        nifty_close=_r(nifty.iloc[i]), nifty_change_pct=_r(n_ret),
        nifty_5d_pct=_r(100 * (nifty.iloc[i] / nifty.iloc[i - 5] - 1)), nifty_20d_pct=_r(100 * (nifty.iloc[i] / nifty.iloc[i - 20] - 1)),
        nifty_from_52w_high_pct=_r(100 * (nifty.iloc[i] / nifty.iloc[i - 251:i + 1].max() - 1)),
        vix=_r(vix.iloc[i]), vix_change=_r(vix.iloc[i] - vix.iloc[i - 1]),
        advances=int((r > 0).sum()), declines=int((r < 0).sum()), unchanged=int((r == 0).sum()),
        up_2pct=int((r > 2).sum()), down_2pct=int((r < -2).sum()), up_5pct=int((r > 5).sum()), down_5pct=int((r < -5).sum()),
        median_stock_pct=_r(r.median()), avg_stock_pct=_r(r.mean()),
        beat_nifty_pct=_r(100 * (r > n_ret).mean(), 1),
        above_50dma_pct=_r(100 * (close.iloc[i][traded_today] > sma50[traded_today]).mean(), 1),
        above_200dma_pct=_r(100 * (close.iloc[i][traded_today] > sma200[traded_today]).mean(), 1),
        new_52w_highs=int(new_hi.sum()), new_52w_lows=int(new_lo.sum()),
        traded_value_cr=_r(traded.iloc[i][traded_today].sum(), 0), traded_value_vs_20d=_r(traded.iloc[i][traded_today].sum() / traded.iloc[i - 20:i].sum(axis=1).mean()),
    )
    # breadth history for context
    hist = []
    for k in range(i - 4, i + 1):
        rr = ret.iloc[k].dropna()
        hist.append(dict(date=str(dates[k].date()), nifty_pct=_r(100 * (nifty.iloc[k] / nifty.iloc[k - 1] - 1)),
                         advances=int((rr > 0).sum()), declines=int((rr < 0).sum())))
    market["last_5_days"] = hist

    # ---------- what each stock had that day (filings first: they are tied to the symbol by NSE) ----------
    notes = {}
    prev = dates[i - 1]
    if ann is not None and len(ann):
        a = ann[ann.symbol.fillna("") != ""].copy()
        a["d0"] = [event_day(t, dates) for t in a.published]
        a = a[a.d0 == day]
        for rr_ in a.itertuples():
            txt = rr_.subject if not rr_.text or rr_.text == rr_.subject else f"{rr_.subject}: {rr_.text}"
            notes.setdefault(rr_.symbol, []).append(dict(kind="NSE filing", type=rr_.category, text=str(txt)[:220]))
    if news is not None and len(news):
        nw = news[~news.roundup.fillna(False).astype(bool)].copy()
        nw["d0"] = [event_day(t, dates) for t in nw.published]
        nw = nw[nw.d0 == day]
        for rr_ in nw.itertuples():
            syms = [s for s in str(rr_.symbols or "").split(",") if s]
            if len(syms) == 1:                     # only headlines about exactly one stock
                notes.setdefault(syms[0], []).append(dict(kind="headline", type=rr_.event_type, text=str(rr_.title)[:220]))

    ret20 = 100 * (close.iloc[i] / close.iloc[i - 21] - 1)

    def row(s):
        return dict(symbol=s, company=company.get(s, ""), sector=sector.get(s, "Other"), close=_r(close.iloc[i][s]),
                    move_pct=_r(r0[s]), vs_nifty_pct=_r(r0[s] - n_ret), volume_x_normal=_r(volx.get(s), 1),
                    traded_cr=_r(traded.iloc[i][s], 0), month_pct=_r(ret20.get(s), 1),
                    new_52w_high=bool(new_hi.get(s, False)), new_52w_low=bool(new_lo.get(s, False)),
                    news=notes.get(s, [])[:2])

    lr = r0[liquid].sort_values()
    up = [row(s) for s in lr[lr > 0].index[::-1][:TOP_N]]
    down = [row(s) for s in lr[lr < 0].index[:TOP_N]]

    # ---------- sectors ----------
    secs = []
    r5 = 100 * (close.iloc[i] / close.iloc[i - 5] - 1)
    for name, cols in sector[traded_today].groupby(sector[traded_today]).groups.items():
        cols = list(cols)
        if len(cols) < 3:
            continue
        rs = r0[cols]
        w = traded.iloc[i][cols].fillna(0)
        best = rs.idxmax(); worst = rs.idxmin()
        secs.append(dict(sector=name, stocks=len(cols), median_pct=_r(rs.median()), weighted_pct=_r((rs * w).sum() / w.sum() if w.sum() else np.nan),
                         share_up_pct=_r(100 * (rs > 0).mean(), 0), median_5d_pct=_r(r5[cols].median()), median_20d_pct=_r(ret20[cols].median()),
                         best=f"{best} {rs[best]:+.1f}%", worst=f"{worst} {rs[worst]:+.1f}%",
                         traded_vs_20d=_r(traded.iloc[i][cols].sum() / traded.iloc[i - 20:i][cols].sum(axis=1).mean())))
    secs = sorted(secs, key=lambda x: -(x["median_pct"] or 0))
    by_abs = sorted(secs, key=lambda x: -abs(x["median_pct"] or 0))
    sectors = dict(table=secs, most_affected=[s["sector"] for s in by_abs[:3]], least_affected=[s["sector"] for s in by_abs[::-1][:3]],
                   best=[s["sector"] for s in secs[:3]], worst=[s["sector"] for s in secs[::-1][:3]])

    # ---------- new players ----------
    first_high = new_hi & (high.iloc[i - 126:i].max() < high.iloc[i - 251:i - 126].max())   # no 52w high in the last 6 months
    rs3 = (close.iloc[i] / close.iloc[i - 63]) - (nifty.iloc[i] / nifty.iloc[i - 63])
    rs3_then = (close.iloc[i - 20] / close.iloc[i - 83]) - (nifty.iloc[i - 20] / nifty.iloc[i - 83])
    liq_then = traded.iloc[i - 40:i - 20].mean() >= MIN_TV
    top_now = set(rs3[liquid].nlargest(50).index)
    top_then = set(rs3_then[liq_then].nlargest(50).index)
    leaders = [dict(symbol=s, company=company.get(s, ""), sector=sector.get(s), move_pct=_r(r0[s]),
                    reason="first 52-week high in over 6 months") for s in first_high[first_high & liquid].index]
    leaders += [dict(symbol=s, company=company.get(s, ""), sector=sector.get(s), move_pct=_r(r0[s]),
                     reason="new in the top 50 by 3-month strength vs Nifty") for s in sorted(top_now - top_then)
                if s not in {x["symbol"] for x in leaders}]
    unusual = (volx >= 3) & liquid & ((r0.abs() >= 2) | (volx >= 5))
    unusual_rows = [dict(symbol=s, company=company.get(s, ""), sector=sector.get(s), move_pct=_r(r0[s]), volume_x_normal=_r(volx[s], 1),
                         news=notes.get(s, [])[:1]) for s in volx[unusual].sort_values(ascending=False).index[:15]]
    first_bar = close.apply(lambda c: c.first_valid_index())
    recent = first_bar[(first_bar > dates[i - 250]) & (first_bar <= day) & traded_today]
    listings = []
    for s, d0 in recent.sort_values(ascending=False).items():
        c0 = close[s].loc[d0]
        listings.append(dict(symbol=s, company=company.get(s, ""), sector=sector.get(s), first_day=str(d0.date()),
                             sessions=int(close[s].loc[d0:day].notna().sum()), since_first_close_pct=_r(100 * (close.iloc[i][s] / c0 - 1), 1),
                             move_pct=_r(r0[s])))
    idx_changes = []
    if uni_changes is not None and len(uni_changes):
        u = uni_changes[pd.to_datetime(uni_changes.date) >= day - pd.Timedelta(days=30)]
        idx_changes = [dict(date=str(x.date), symbol=x.symbol, change=x.change) for x in u.itertuples()]
    ipo_news = []
    if news is not None and len(news):
        recent_news = news[(news.published >= day - pd.Timedelta(days=7)) & (news.published < day + pd.Timedelta(days=1))]
        ipo_news = [dict(date=str(t.published)[:10], text=t.title[:200]) for t in recent_news[recent_news.event_type == "IPO"].head(8).itertuples()]
    new = dict(leaders=leaders[:15], index_changes=idx_changes, unusual_volume=unusual_rows, recent_listings=listings[:15], ipo_headlines=ipo_news)

    # ---------- filings and market news ----------
    filings = dict(count=0, by_type={}, notable=[])
    if ann is not None and len(ann):
        a = ann.copy()
        a["d0"] = [event_day(t, dates) for t in a.published]
        a = a[a.d0 == day]
        filings["count"] = int(len(a))
        filings["by_type"] = {k: int(v) for k, v in a.category.value_counts().head(10).items()}
        nb = a[a.category.isin(NOTABLE) & a.symbol.isin(set(liquid[liquid].index))]
        filings["notable"] = [dict(symbol=x.symbol, type=x.category, text=(x.subject if not x.text or x.text == x.subject else f"{x.subject}: {x.text}")[:200],
                                   move_pct=_r(r0.get(x.symbol))) for x in nb.head(15).itertuples()]
    headlines = []
    if news is not None and len(news):
        nw = news.copy()
        nw["d0"] = [event_day(t, dates) for t in nw.published]
        nw = nw[(nw.d0 == day) & (nw.symbols.fillna("") == "") & ~nw.roundup.fillna(False).astype(bool)]
        nw = nw.sort_values("n_sources", ascending=False)
        headlines = [dict(source=x.source, text=x.title[:200], sources=int(x.n_sources or 1)) for x in nw.head(12).itertuples()]
    return dict(day=str(day.date()), market=market, up=up, down=down, sectors=sectors, new=new, filings=filings, headlines=headlines,
                macro=macro_facts(day, news),
                settings=dict(min_traded_cr=MIN_TV, top_n=TOP_N))


def macro_facts(day, news=None):
    """Latest macro readings (as of the report day), geopolitical risk, themed headlines of the past week, upcoming events."""
    try:
        from core import macro as M
    except Exception:
        return {}
    out = dict(indicators=[], gpr=None, themes=[], upcoming=[])
    try:
        out["indicators"] = [r for r in M.summary(day) if (pd.Timestamp(day) - pd.Timestamp(r["date"])).days <= 10]
        w = M.wide(["GPRD"])
        if "GPRD" in w:
            g = w.GPRD.dropna()
            g = g[g.index <= pd.Timestamp(day)]
            if len(g) > 40:
                out["gpr"] = dict(date=str(g.index[-1].date()), value=_r(g.iat[-1], 1), avg_30d=_r(g.tail(30).mean(), 1),
                                  avg_1y=_r(g.tail(365).mean(), 1), pct_rank_1y=_r(100 * (g.tail(365) < g.iat[-1]).mean(), 0))
        h = M.headline_themes(news)
        if len(h):
            h = h[(h.published > pd.Timestamp(day) - pd.Timedelta(days=7)) & (h.published < pd.Timestamp(day) + pd.Timedelta(days=1))]
            for th, g in h.groupby("theme"):
                g = g.sort_values("published", ascending=False)
                out["themes"].append(dict(theme=M.THEMES[th][0], headlines=int(len(g)), latest=[t[:160] for t in g.title.head(3)]))
            out["themes"].sort(key=lambda x: -x["headlines"])
        cal = M.calendar()
        if len(cal):
            up = cal[(cal.date > pd.Timestamp(day)) & (cal.date <= pd.Timestamp(day) + pd.Timedelta(days=14))].sort_values("date")
            out["upcoming"] = [dict(date=str(x.date.date()), event=x.event, country=x.country) for x in up.itertuples()]
    except Exception:
        pass
    return out


def load_inputs(broad=True):
    from core.data import load_raw
    from core import news, announcements, company_info
    from core.config import DATA
    px, idx, _ = load_raw(broad=broad)
    ch = DATA / "universe_changes.csv"
    return dict(px=px, idx=idx, info=_info_all(px, company_info.load()), news=news.load_store(), ann=announcements.load(),
                uni_changes=pd.read_csv(ch) if ch.exists() else None)


def _info_all(px, info):
    """company_info.load() covers the Nifty 500; other stocks get names from instruments.csv when available."""
    from core.config import DATA
    extra = sorted(set(px.symbol) - set(info.symbol))
    if not extra:
        return info
    names = {}
    p = DATA / "instruments.csv"
    if p.exists():
        ins = pd.read_csv(p, dtype=str)
        if "name" in ins:
            names = dict(zip(ins.symbol, ins.name))
    add = pd.DataFrame(dict(symbol=extra, company=[names.get(s, "") for s in extra], sector="Outside Nifty 500"))
    return pd.concat([info, add], ignore_index=True)


def auto_on():
    """Nightly market report on/off (Market news → Report). Off by default: written only when you click Write."""
    import json
    from core import config as C
    try:
        return bool(json.loads((C.DATA / "auto_jobs.json").read_text()).get("market_report"))
    except (OSError, ValueError):
        return False


if __name__ == "__main__":
    if "--if-auto" in sys.argv and not auto_on():
        print("Market report: manual (turn on auto in Market news → Report). Skipped.")
        sys.exit(0)
    from research import report_writer as W
    day = next((a for a in sys.argv[1:] if a[:2] == "20"), None)
    inp = load_inputs()
    facts = build(day, **inp)
    path = W.write_and_save(facts, use_llm="--no-llm" not in sys.argv)
    print("Report saved:", path)
