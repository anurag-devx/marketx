"""API: Screener page (technical setups, market movers, daily moves, results, news, announcements, shortlist)."""
import json, threading
import numpy as np, pandas as pd
from core import config as C
from web.api import route, cached, _mtime, universe, UserError

SETUP_LABEL = {"trend_continuation": "Trend continuation", "breakout_volume": "Breakout with volume", "pullback_recovery": "Pullback recovery",
               "relative_strength": "Relative strength", "volatility_breakout": "Volatility contraction breakout"}
PARAM_LABEL = {
    "trend_continuation": {"slope_lookback": ("SMA50 rising over (sessions)", None),
                           "use_rsi": ("Require RSI inside a band", None), "rsi_min": ("RSI from", None), "rsi_max": ("RSI up to", None),
                           "use_adx": ("Require trend strength (ADX)", None), "adx_min": ("ADX above", None)},
    "breakout_volume": {"lookback": ("Close above the highest high of the previous (sessions)", "Today's high is not included"),
                        "rel_volume_min": ("Volume at least (× its 20-session average)", None),
                        "require_above_sma50": ("Also close above SMA50", None)},
    "pullback_recovery": {"rsi_cross": ("RSI crosses up through", "Yesterday below this level, today at or above it"),
                          "use_proximity": ("Require price near a moving average", None), "proximity_ma": ("Moving average", None),
                          "proximity_pct": ("Within (%)", None)},
    "relative_strength": {"return_window": ("Return measured over (sessions)", "63 ≈ 3 months, 126 ≈ 6 months"),
                          "use_benchmark_relative": ("Measure against the Nifty", None),
                          "min_percentile": ("Rank among eligible stocks at least (percentile)", "80 = top 20%")},
    "volatility_breakout": {"contraction_pctile_max": ("Bollinger width percentile yesterday at most", "Low = unusually quiet versus the past year"),
                            "breakout_lookback": ("Then close above the highest high of the previous (sessions)", None),
                            "use_rel_volume": ("Require volume confirmation", None), "rel_volume_min": ("Volume at least (×)", None)},
}
_last = {}                # last screen result (one user, one machine)
_lock = threading.Lock()


def _inputs():
    from screener import screen as SCR
    v = SCR.data_version()
    def build():
        px, bench, uni, issues = SCR.load_inputs()
        return SCR.features(), list(uni.Symbol), issues
    return cached("scr_inputs", v, build)


def _shortlist_symbols():
    try:
        from screener import interface
        sl = interface.load_shortlist()
        return set() if sl.empty() else set(sl.rows.symbol)
    except Exception:
        return set()


# ---------------- technical setups ----------------
@route("GET", "screener/schema")
def schema(_):
    from screener import config as SC, setups as SU, experiments as X
    try:
        strategies = X.load_strategies()
    except Exception:
        strategies = {}
    labels = PARAM_LABEL
    sch = {s: {k: dict(type=t.__name__, lo=lo, hi=hi, default=d, label=(labels.get(s, {}).get(k, (k.replace("_", " "), None))[0]),
                       help=(labels.get(s, {}).get(k, (None, None))[1]))
               for k, (t, lo, hi, d) in spec.items()} for s, spec in SC.SETUP_SCHEMA.items()}
    feat, _, _ = _inputs()
    dates = sorted(feat.date.unique())
    return dict(setups=sch, setup_label=SETUP_LABEL, descriptions=SU.DESCRIPTIONS, ranking=SC.RANKING_METHODS, default=SC.DEFAULT,
                strategies={k: dict(config=SC.validate(v["config"]), status=X.status_of(k), description=v.get("description"))
                            for k, v in strategies.items()},
                first=dates[0], last=dates[-1])


@route("POST", "screener/run")
def run(_, body):
    from screener import config as SC, screen as SCR
    try:
        cfg = SC.validate(body.get("config") or {})
    except SC.ConfigError as e:
        raise UserError(f"Settings: {e}")
    data = _inputs()
    res = SCR.screen(pd.Timestamp(body.get("date") or data[0].date.max()), cfg, data=data)
    with _lock:
        _last["res"] = res
    m, dg = res["meta"], res["diagnostics"]
    funnel = [dict(step=k, stocks=v) for k, v in dg.items()]
    t = SCR.table(res) if not res["shortlist"].empty else pd.DataFrame()
    show = ["rank", "symbol", "sector", "setups_passed", "qualification_reasons", "close", "ranking_score", "rsi14", "adx14", "relative_volume_20d",
            "return_63d", "return_126d", "benchmark_relative_return_126", "median_traded_value_20d_cr", "data_quality_flags"]
    rows = res["rows"]
    rej = pd.DataFrame()
    if res["shortlist"].empty:
        rr = rows[rows.rejection_reasons.str.len() > 0].rejection_reasons.str.split("; ").explode().value_counts().head(10)
        rej = rr.rename("stocks").reset_index().rename(columns={"rejection_reasons": "reason"})
    syms = list(res["shortlist"].symbol) + sorted(set(rows.symbol) - set(res["shortlist"].symbol))
    return dict(meta=dict(screen_date=m["screen_date"], config_id=m["config_id"], version=m["screener_version"], data=m["data_version"],
                          data_last=m["data_last"], ranking=SC.RANKING_METHODS[m["config"]["ranking"]["method"]], universe_note=m["universe_note"]),
                funnel=funnel, shortlist=t[[c for c in show if c in t]] if len(t) else [], rejections=rej, symbols=syms,
                all_csv=SCR.table(res, only_shortlist=False).to_csv(index=False), config=m)


@route("GET", "screener/detail")
def detail(p):
    from screener import config as SC
    res = _last.get("res")
    if not res:
        raise UserError("run a screen first")
    rows = res["rows"]
    r = rows[rows.symbol == p.get("symbol")]
    if r.empty:
        raise UserError("not in this screen")
    r = r.iloc[0]
    cfg = res["meta"]["config"]
    ok_lab = {"f_universe": "in the universe", "f_min_close": "price", "f_min_history": "enough history", "f_liquidity": "liquid enough",
              "f_valid_obs": "trades regularly", "f_not_etf": "not an ETF", "f_data_ok": "no open data error"}
    filters = [dict(name=lab, ok=bool(r[c])) for c, lab in ok_lab.items() if c in r]
    setups = [dict(name=SETUP_LABEL[s], ok=bool(r[f"{s}_pass"]), on=cfg["setups"][s]["enabled"]) for s in SC.SETUP_SCHEMA]
    vals = {"close": r.close, "SMA20": r.sma20, "SMA50": r.sma50, "SMA200": r.sma200, "SMA50 slope 20d %": r.sma50_slope_20d,
            "RSI14 (yesterday)": r.get("rsi14_prev"), "RSI14": r.rsi14, "ADX14": r.adx14, "relative volume": r.rel_volume_20d,
            "previous 20d high": r.prev_high_20, "return 63d %": r.return_63d, "return 126d %": r.return_126d,
            "vs Nifty 126d (pp)": r.rel_return_126, "BB width percentile": r.bb_width_pctile_252,
            "median value ₹ cr": r.median_value_20d_cr, "sessions traded (60)": r.valid_ratio_60d, "ranking score": r.ranking_score}
    return dict(symbol=r.symbol, filters=filters, setups=setups, rejection=r.rejection_reasons, qualification=r.qualification_reasons,
                flags=r.data_quality_flags, qualified=bool(r.qualified), qualifies=bool(r.qualifies),
                rank=None if pd.isna(r["rank"]) else int(r["rank"]),
                values=[dict(name=k, value=None if v is None or pd.isna(v) else round(float(v), 2)) for k, v in vals.items()])


# ---------------- market movers ----------------
def _snapshot(date=None):
    from screener import market_scan as MS, screen as SCR
    feat, _, _ = _inputs()
    return cached(f"snap_{date}", SCR.data_version(), lambda: MS.snapshot(feat, None if date is None else pd.Timestamp(date)))


@route("GET", "screener/movers")
def movers(p):
    from screener import market_scan as MS
    snap, day = _snapshot(p.get("date") or None)
    only = p.get("only", "all")
    syms = None if only == "all" else (set(universe().Symbol) if only == "nifty500" else _shortlist_symbols())
    preset = p.get("preset", "Top gainers")
    rows, counts = MS.scan(snap, preset, float(p.get("min_price", 20)), float(p.get("min_turnover", 2)),
                           [s for s in p.get("sectors", "").split(",") if s], syms, int(p.get("limit", 100)))
    cols = [["symbol", "Stock", "sym"], ["sector", "Sector"], ["close", "Close", "inr"], ["change_1d_pct", "1 day", "spct2"], ["gap_pct", "Gap", "spct2"],
            ["change_5d_pct", "5 days", "spct1"], ["change_1m_pct", "1 month", "spct1"], ["change_3m_pct", "3 months", "spct1"],
            ["rel_volume", "Volume ×", "x1"], ["from_52w_high_pct", "vs 52w high", "spct1"], ["rsi14", "RSI", "num0"],
            ["turnover_cr", "Traded ₹ cr", "num1"], ["median_value_20d_cr", "Usual ₹ cr/day", "num1"]]
    n = counts.get(f"match: {preset}", len(rows))
    return dict(presets=list(MS.PRESETS), day=day, sections=[
        dict(kind="note", tone="info", text=f"{n} stocks match on {pd.Timestamp(day):%a %d %b %Y}" + (f" (showing {len(rows)})" if len(rows) < n else "")
             + " · " + " → ".join(f"{k}: {v}" for k, v in counts.items())),
        dict(kind="table", rows=rows, columns=cols, csv=f"market_{preset.split(' (')[0].replace(' ', '_')}_{pd.Timestamp(day).date()}", per=100),
        _sector_table(MS.sector_summary(snap))])


def _sector_table(ss):
    ss = ss.round(2)
    if "sector" not in ss.columns:
        ss = ss.reset_index().rename(columns={"index": "sector"})
    cols = [["sector", "Sector"]] + [[c, c.replace("_", " "), "spct2" if ("pct" in c or "change" in c or "move" in c) else "num1"]
                                     for c in ss.columns if c != "sector"]
    return dict(kind="table", title="Sectors on this day", rows=ss, columns=cols, tools=False)


# ---------------- daily moves ----------------
@route("GET", "screener/daily")
def daily(p):
    from web.r_today import _moves_panel, _company_info
    from research import daily_moves as DM
    P = _moves_panel()
    dates = P["dates"]
    thr, liq, vs = int(p.get("thr", 2)), float(p.get("liq", 0)), p.get("vs") == "true"
    cnt = DM.daily_counts(P, liq, vs)
    n = {"1M": 21, "3M": 63, "1Y": 252, "3Y": 756, "All": len(cnt)}[p.get("span", "3M")]
    v = cnt.tail(n)
    day = pd.Timestamp(p["day"]) if p.get("day") else dates[-1]
    if day not in dates:
        i = dates.searchsorted(day)
        day = dates[max(0, i - 1)]
    d = DM.day_table(P, day, _company_info(), None, liq)
    d["bucket"] = DM.bucket(d.vs_nifty_pct if vs else d.move_pct)
    bk = [b for b in p.get("buckets", "").split("|") if b]
    s = d[d.bucket.isin(bk)] if bk else d
    row = cnt[cnt.date == day]
    k = lambda c: str(int(row[c].iat[0])) if len(row) else "—"
    cols = [["symbol", "Stock", "sym"], ["move_pct", "Move", "spct2"], ["vs_nifty_pct", "vs Nifty", "spct2"], ["volume_x_normal", "Volume ×", "x1"],
            ["traded_cr", "Traded ₹ cr", "num0"], ["market_cap_cr", "Market cap ₹ cr", "num0"], ["sector", "Sector"], ["industry", "Industry"],
            ["basic_industry", "Basic industry"], ["close", "Close", "inr"]]
    out = [dict(kind="chart", type="bars", title=f"How many stocks moved more than {thr}% each day", height=300,
                x=v.date.dt.strftime("%Y-%m-%d").tolist(),
                series=[dict(name=f"Up > {thr}%", y=v[f"up_{thr}"].astype(float).tolist()), dict(name=f"Down > {thr}%", y=(-v[f"down_{thr}"]).astype(float).tolist())],
                line=dict(name="Nifty %", y=v.nifty_pct.round(2).tolist()), note="Down counts are drawn below zero. The line is the Nifty's daily move (its own scale)."),
           dict(kind="kpis", items=[dict(label=lab, value=k(c)) for lab, c in (("Up >1%", "up_1"), ("Up >2%", "up_2"), ("Up >5%", "up_5"),
                                                                              ("Down >1%", "down_1"), ("Down >2%", "down_2"), ("Down >5%", "down_5"))]),
           dict(kind="table", title=f"{len(s)} stocks on {day:%a %d %b %Y}", rows=s, columns=cols, csv=f"moves_{day.date()}", per=100)]
    if p.get("group") == "true":
        g = d.groupby(["sector", "industry"]).agg(stocks=("symbol", "size"), median_move=("move_pct", "median"),
                                                  up_2=("move_pct", lambda x: int((x > 2).sum())), down_2=("move_pct", lambda x: int((x < -2).sum()))).reset_index()
        out.insert(2, dict(kind="table", title="By industry", rows=g.sort_values("median_move", ascending=False),
                           columns=[["sector", "Sector"], ["industry", "Industry"], ["stocks", "Stocks", "int"], ["median_move", "Median move", "spct2"],
                                    ["up_2", "Up >2%", "int"], ["down_2", "Down >2%", "int"]]))
    return dict(day=day, buckets=["&gt;+5%"], sections=out)


# ---------------- results (quarterly) ----------------
@route("GET", "screener/results")
def results(p):
    from core import fundamentals as F
    from web.api import _quarters
    q = _quarters()
    st_ = F.state()
    r = F.results()
    status = "No results downloaded yet."
    if len(r):
        ok = r[r.status == "ok"]
        status = f"{ok.symbol.nunique()} stocks, {len(ok):,} quarterly results read" + (f" (from {pd.to_datetime(ok.period_end).min():%b %Y})" if len(ok) else "")
        if st_.get("failed"):
            status += f" · {len(st_['failed'])} files failed (retried next run)"
        if st_.get("last_update"):
            status += f" · last update {str(st_['last_update'])[:16]}"
    view = p.get("view", "latest")
    cal = F.calendar()
    uni = universe()
    secmap = dict(zip(uni.Symbol, uni.Industry))
    if view == "calendar":
        c = cal.assign(company=cal.symbol.map(dict(zip(uni.Symbol, uni["Company Name"]))), sector=cal.symbol.map(secmap)) if len(cal) else cal
        return dict(status=status, sections=[dict(kind="table", title="Results meetings coming up", rows=c,
                    columns=[["date", "Date", "date"], ["symbol", "Stock", "sym"], ["sector", "Sector"], ["source", "Source"], ["purpose", "Purpose", "long"]],
                    empty="None known. Dates come from NSE's event calendar and board-meeting notices in the NSE filings.")])
    if view == "coverage":
        return dict(status=status, sections=[dict(kind="table", title="Stocks with numbers per quarter", rows=F.coverage().head(24),
                    columns=[["period_end", "Quarter end", "date"], ["stocks_with_numbers", "Stocks with numbers", "int"], ["universe", "Universe", "int"]],
                    note="Gaps: the company filed no XBRL, NSE did not list it, or the file has not been read yet (Download history continues where it stopped).")])
    if q.empty:
        return dict(status=status, sections=[dict(kind="note", tone="info", text="No quarterly results yet. Press Download history once (background, a few hours); after that results update with every Fetch data run.")])
    if view == "drift":
        from research.earnings import drift_study
        d = drift_study(q)
        if d.empty:
            return dict(status=status, sections=[dict(kind="note", tone="info", text="Needs at least 5 quarters per stock. Download history first.")])
        cols = [["sample", "Sample"], ["surprise", "Profit surprise"], ["events", "Events", "int"]]
        for c, lab in (("react_rel", "Reaction day"), ("drift_5", "Next 5 days"), ("drift_20", "Next 20 days")):
            cols += [[f"{c}_mean", lab, "spct2"], [f"{c}_t", "t", "num1"], [f"{c}_hit", "hit %", "num0"]]
        cols.append(["enough", "Enough events", "bool"])
        return dict(status=status, sections=[dict(kind="note", tone="info", text="Average move minus the Nifty after results with each size of profit surprise. "
                    "t above about 2 means the average is unlikely to be luck. The latest quarters are kept apart: a pattern that shows only in the earlier period is not one to rely on."),
                    dict(kind="table", rows=d, columns=cols, tools=False)])
    days = int(p.get("days", 30))
    v = q[pd.to_datetime(q.filed_at) >= pd.Timestamp.now() - pd.Timedelta(days=days)].assign(sector=lambda x: x.symbol.map(secmap))
    if p.get("sectors"):
        v = v[v.sector.isin(p["sectors"].split(","))]
    if p.get("sue_min"):
        v = v[v.sue_profit >= float(p["sue_min"])]
    if p.get("shortlist") == "true":
        v = v[v.symbol.isin(_shortlist_symbols())]
    v = v.sort_values("filed_at", ascending=False)
    cols = [["symbol", "Stock", "sym"], ["quarter", "Quarter"], ["basis", "Basis"], ["filed_at", "Filed", "dt"], ["revenue_cr", "Revenue ₹ cr", "num0"],
            ["revenue_yoy", "Rev YoY", "spct1"], ["profit_cr", "Profit ₹ cr", "num1"], ["profit_yoy", "Profit YoY", "spct1"], ["margin_pct", "Net margin", "pct1"],
            ["ebitda_margin", "EBITDA margin", "pct1"], ["sue_profit", "Profit surprise", "snum1"], ["sue_revenue", "Rev surprise", "snum1"],
            ["react_rel", "Reaction vs Nifty", "spct1"], ["drift_5", "Next 5d", "spct1"], ["revised", "Revised", "bool"]]
    return dict(status=status, sections=[dict(kind="table", title=f"{len(v)} results filed in the last {days} days", rows=v, columns=cols, csv="results",
                note="Blank = not available or not comparable (different basis a year ago, a loss a year ago, or fewer than 4 earlier quarters for the surprise). "
                     "The first filing is shown because that is what the market reacted to.")])


# ---------------- news ----------------
@route("GET", "screener/news")
def news(p):
    from core import news as N
    from screener import news_scan as NS
    d0, errors, when = N.load()
    if when is None:
        return dict(when=None, items=[], stocks=[], types=[], sources=[])
    d = cached("ns_prepared", _mtime(N.STORE), lambda: NS.prepare_news(N.load_store(), universe()))
    hours = int(p.get("hours", 24))
    since = pd.Timestamp.now() - pd.Timedelta(hours=hours)
    tones = [("" if t == "(no tone)" else t) for t in p.get("tones", "").split(",") if t]
    v = NS.filter_items(d, since, [s for s in p.get("sources", "").split("|") if s], (), p.get("kw", ""),
                        [s for s in p.get("symbols", "").split(",") if s], [s for s in p.get("sectors", "").split("|") if s], tones,
                        p.get("tagged") == "true")
    types = [t for t in p.get("types", "").split("|") if t]
    if types:
        v = v[v.event_type.fillna("").isin([("" if t == "(no type)" else t) for t in types])]
    if p.get("no_roundups", "true") == "true":
        v = v[~v.roundup.fillna(False).astype(bool)]
    if p.get("shortlist") == "true":
        v = NS.filter_items(v, symbols=_shortlist_symbols() or {"__none__"})
    v = v.sort_values("published", ascending=False)
    from web.r_screener import _snapshot as snapf
    try:
        snap, _ = snapf()
    except Exception:
        snap = None
    stocks = NS.stocks_in_items(v, snap, _shortlist_symbols())
    items = [dict(title=r.title, link=r.link, source=r.source, published=r.published, symbols=list(r.symbols), type=r.event_type,
                  type_why=getattr(r, "type_why", ""), tone=r.tone, tone_why=getattr(r, "tone_why", ""), roundup=bool(r.roundup))
             for r in v.head(int(p.get("limit", 200))).itertuples()]
    return dict(when=when, errors=errors[:6], total=len(d), matched=len(v), items=items, stocks=stocks.head(80),
                types=sorted(x for x in d.event_type.fillna("").unique() if x) + ["(no type)"], sources=sorted(d.source.dropna().unique()))


# ---------------- announcements ----------------
@route("GET", "screener/announcements")
def announcements(p):
    from core import announcements as A
    from screener import news_scan as NS
    info = A.last_fetch()
    d = cached("ann_prepared", _mtime(A.STORE), lambda: NS.prepare_announcements(A.load(), universe()))
    if d.empty:
        return dict(info=info, rows=[], stocks=[], categories=[])
    since = pd.Timestamp.now() - pd.Timedelta(days=int(p.get("days", 7)))
    v = NS.filter_items(d, since, keywords=p.get("kw", ""), symbols=[s for s in p.get("symbols", "").split(",") if s],
                        sectors=[s for s in p.get("sectors", "").split("|") if s], text_cols=("subject", "text", "company"))
    cats = [c for c in p.get("cats", "").split("|") if c]
    if cats:
        v = v[v.category.isin(cats)]
    if p.get("n500", "true") == "true":
        v = v[v.in_nifty500]
    if p.get("shortlist") == "true":
        v = v[v.symbol.isin(_shortlist_symbols())]
    v = v.sort_values("published", ascending=False)
    try:
        snap, _ = _snapshot()
    except Exception:
        snap = None
    return dict(info=info, total=len(d), rows=v[["published", "symbol", "company", "category", "subject", "text", "link"]].head(1000),
                stocks=NS.stocks_in_items(v, snap, _shortlist_symbols()).head(80),
                categories=[x[0] for x in A.CATEGORIES] + ["Other"])


# ---------------- shortlist ----------------
@route("GET", "screener/shortlist")
def shortlist(_):
    from screener import interface
    sl = interface.load_shortlist()
    if sl.empty():
        return dict(empty=True)
    groups = []
    for s, g in sl.rows.groupby("screener_strategy"):
        groups.append(dict(name=s, rows=g[[c for c in ["rank", "symbol", "sector", "setups_passed", "qualification_reasons", "close", "rsi14", "adx14",
                                                       "rel_volume_20d", "return_126d", "data_quality_flags"] if c in g]].round(2)))
    return dict(empty=False, date=sl.screen_date, version=sl.version, data=sl.data_version, warnings=sl.warnings, groups=groups)


@route("GET", "meta/lists")
def lists(_):
    u = universe()
    return dict(sectors=sorted(u.Industry.dropna().unique()), symbols=[[s, f"{s} — {c}"] for s, c in zip(u.Symbol, u["Company Name"])])
