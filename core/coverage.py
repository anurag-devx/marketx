"""Coverage: for every Nifty 500 stock, which data MarketX has, which signals can be computed from it, and which of those
are used in the forecasts. One table (Coverage page) and a per-stock checklist. Read-only: nothing here changes data.

Data items (id: label, how 'available' is judged, the job that fills it):
  prices      daily bars (last date within 5 days of the newest)             fetch
  intraday    5-minute bars (Angel)                                         intraday
  bhavcopy    NSE bhavcopy with delivery % (last 20 sessions)               bhavcopy-backfill
  results     quarterly results filings read (4 consecutive = TTM ready)    fundamentals-backfill
  balance     a balance sheet read (equity, debt)                           fundamentals-balance
  holding     shareholding pattern                                          shareholding
  fo          F&O open interest (only F&O stocks have it)                   fo-backfill
  news        headlines naming the stock, last 90 days                      news-backfill
  filings     exchange filings, last 90 days                                announcements-backfill
Signals: Market Lab's groups (base + candidates) and TA. 'used' = feeds tonight's chance (in the engine AND tested to help
in the Scorecard); 'shown' = on the map / page but not shown to help; 'candidate' = built, tested separately, off."""
import json, time
import numpy as np, pandas as pd
from core import config as C

DATA_ITEMS = [("prices", "Daily prices", "fetch"), ("intraday", "5-minute bars", "intraday"), ("bhavcopy", "NSE bhavcopy + delivery", "bhavcopy-backfill"),
              ("results", "Quarterly results", "fundamentals-backfill"), ("balance", "Balance sheet", "fundamentals-balance"),
              ("holding", "Shareholding", "shareholding"), ("fo", "F&O open interest", "fo-backfill"), ("news", "News (90 days)", "news-backfill"),
              ("filings", "Filings (90 days)", "announcements-backfill")]
NEEDS = {"Price action": "prices", "Volume": "prices", "Volatility": "prices", "Market": "prices", "Sector": "prices", "Macro": "prices",
         "Results": "results", "News": "news", "Filings": "filings", "Delivery": "bhavcopy", "Valuation": "results"}
_cache = {}


def _read(path, cols=None):
    try:
        return pd.read_parquet(path, columns=cols)
    except Exception:
        return pd.DataFrame(columns=cols or [])


def _mt(*ps):
    return tuple((str(p), p.stat().st_mtime if p.exists() else 0) for p in ps)


def universe():
    u = pd.read_csv(C.DATA / "universe.csv")
    return u.rename(columns={"Symbol": "symbol", "Industry": "sector", "Company Name": "name"})[["symbol", "name", "sector"]]


def signal_status():
    """{group: 'used' | 'shown' | 'candidate'} from the engine's groups and the Scorecard."""
    from marketlab.features import BASE_GROUPS, CANDIDATES, active_candidates
    from marketlab import evaluate as V
    _, gs = V.skill_map()
    act = set(active_candidates())
    out = {}
    for g in BASE_GROUPS:
        out[g] = "used" if (gs.get(g) or {}).get("tested") else "shown"
    rc = V.load_candidates() or {}
    for g in CANDIDATES:
        helps = any(x["node"] == g and x["helps"] and x.get("years", 0) >= V.MIN_CAND_YEARS for x in rc.get("summary", []))
        out[g] = ("used" if helps else "shown") if g in act else "candidate"
    return out


def table(force=False):
    """One row per stock: data items (status, detail), signal groups (available, status), plan/radar presence."""
    paths = [C.DATA / "prices.parquet", C.DATA / "intraday" / "state.json", C.DATA / "bhavcopy" / "daily.parquet", C.DATA / "fundamentals" / "quarters.parquet",
             C.DATA / "shareholding" / "holdings.parquet", C.DATA / "derivatives" / "fo_daily.parquet", C.DATA / "news" / "news.parquet",
             C.DATA / "announcements.parquet", C.DATA / "marketlab" / "active_groups.json", C.RESULTS / "marketlab" / "score.json"]
    key = _mt(*paths)
    if not force and _cache.get("key") == key and time.time() - _cache.get("at", 0) < 600:
        return _cache["out"]
    uni = universe()
    syms = set(uni.symbol)
    px = _read(C.DATA / "prices.parquet", ["date", "symbol"])
    px["date"] = pd.to_datetime(px.date)
    newest = px.date.max() if len(px) else None
    pl = px.groupby("symbol").date.agg(["max", "count"]) if len(px) else pd.DataFrame(columns=["max", "count"])
    try:
        ist = json.loads((C.DATA / "intraday" / "state.json").read_text()).get("symbols", {})
    except (OSError, ValueError):
        ist = {}
    bh = _read(C.DATA / "bhavcopy" / "daily.parquet", ["date", "symbol", "deliv_pct"])
    if len(bh):
        bh["date"] = pd.to_datetime(bh.date)
        bdays = sorted(bh.date.unique())[-20:]
        bl = bh[bh.date.isin(bdays)].groupby("symbol").agg(last=("date", "max"), n=("deliv_pct", lambda x: int(x.notna().sum())))
    else:
        bl = pd.DataFrame(columns=["last", "n"])
    try:
        from core import valuation as VAL
        vf = VAL.filings()
    except Exception:
        vf = pd.DataFrame()
    q = _read(C.DATA / "fundamentals" / "quarters.parquet", ["symbol", "period_end", "status", "equity_owners", "equity", "bs_date"])
    q = q[q.status == "ok"] if len(q) else q
    ql = q.groupby("symbol").agg(n=("period_end", "nunique"), last=("period_end", "max")) if len(q) else pd.DataFrame(columns=["n", "last"])
    bs = q.dropna(subset=["bs_date"]).groupby("symbol").bs_date.max() if len(q) and "bs_date" in q else pd.Series(dtype=object)
    ttm = set(vf[vf.ttm_eps.notna()].symbol) if len(vf) else set()
    sh = _read(C.DATA / "shareholding" / "holdings.parquet", ["symbol", "period_end"])
    shl = sh.groupby("symbol").period_end.max() if len(sh) else pd.Series(dtype=object)
    fo = _read(C.DATA / "derivatives" / "fo_daily.parquet", ["date", "symbol"])
    fol = fo.groupby("symbol").date.max() if len(fo) else pd.Series(dtype=object)
    cut = pd.Timestamp.now() - pd.Timedelta(days=90)
    nw = _read(C.DATA / "news" / "news.parquet", ["published", "symbols"])
    if len(nw):
        nw = nw[pd.to_datetime(nw.published) >= cut]
        nc = nw.symbols.astype(str).str.split(",").explode().str.strip().value_counts()
    else:
        nc = pd.Series(dtype=int)
    an = _read(C.DATA / "announcements.parquet", ["published", "symbol"])
    ac = an[pd.to_datetime(an.published) >= cut].symbol.value_counts() if len(an) else pd.Series(dtype=int)
    from tradeplan import plan as TP
    from marketlab import radar as R
    saved = TP.load() or {}
    plans = saved.get("plans", {})
    rd = R.read("radar", R.latest_day())
    in_radar = set(rd.symbol) if len(rd) else set()
    sig = signal_status()
    rows = []
    for u in uni.itertuples():
        s = u.symbol
        d = {}
        last = pl["max"].get(s) if s in pl.index else None
        d["prices"] = ("ok" if last is not None and newest is not None and (newest - last).days <= 5 else "stale" if last is not None else "missing",
                       f"to {last:%d %b %Y} · {int(pl['count'][s]):,} bars" if last is not None else "no bars")
        it = ist.get(s) or {}
        d["intraday"] = ("ok" if it.get("bars") else "missing", f"{int(it['bars']):,} bars since {str(it.get('first', ''))[:10]}, to {str(it.get('last', ''))[:10]}" if it.get("bars") else "not downloaded")
        if s in bl.index:
            d["bhavcopy"] = ("ok" if bl.n[s] >= 15 else "partial", f"delivery on {bl.n[s]} of the last 20 sessions · to {bl['last'][s]:%d %b}")
        else:
            d["bhavcopy"] = ("missing", "not downloaded")
        if s in ql.index:
            n = int(ql.n[s])
            d["results"] = ("ok" if s in ttm else "partial", f"{n} quarter(s) · last {pd.Timestamp(ql['last'][s]):%b %Y}" + ("" if s in ttm else " · TTM needs 4 in a row"))
        else:
            d["results"] = ("missing", "none read")
        d["balance"] = ("ok", f"as of {str(bs[s])[:10]}") if s in bs.index else ("missing", "none read")
        d["holding"] = ("ok", f"last {pd.Timestamp(shl[s]):%b %Y}") if s in shl.index else ("missing", "none")
        d["fo"] = ("ok", f"to {pd.Timestamp(fol[s]):%d %b}") if s in fol.index else ("n/a", "not an F&O stock (or not downloaded)")
        d["news"] = ("ok" if nc.get(s, 0) else "none", f"{int(nc.get(s, 0))} headline(s)")
        d["filings"] = ("ok" if ac.get(s, 0) else "none", f"{int(ac.get(s, 0))} filing(s)")
        groups = {}
        for g, st in sig.items():
            need = d.get(NEEDS.get(g, "prices"), ("missing",))[0]
            avail = need in ("ok", "partial") or (g in ("News", "Filings") and need == "none")
            groups[g] = dict(available=bool(avail), status=st)
        p = plans.get(s) or {}
        rows.append(dict(symbol=s, name=u.name, sector=u.sector, data={k: dict(state=v[0], detail=v[1]) for k, v in d.items()}, signals=groups,
                         radar=s in in_radar, plan=bool(p), ta=(p.get("ta") or {}).get("dir"), fund=(p.get("fund") or {}).get("dir"),
                         missing=[k for k, v in d.items() if v[0] in ("missing", "stale")]))
    n = len(rows) or 1
    summary = {k: round(sum(r["data"][k]["state"] in ("ok", "partial", "n/a") for r in rows) / n, 3) for k, _, _ in DATA_ITEMS}
    out = dict(built=str(pd.Timestamp.now().floor("s")), items=[dict(id=k, label=l, job=j) for k, l, j in DATA_ITEMS], signals=sig,
               summary=summary, rows=rows, radar_day=R.latest_day(), plan_day=saved.get("day"))
    _cache.update(key=key, at=time.time(), out=out)
    return out


def stock(symbol):
    """Checklist for one stock: data items, and every signal with today's value and whether it feeds the forecast."""
    t = table()
    row = next((r for r in t["rows"] if r["symbol"] == symbol), None)
    if not row:
        return None
    from marketlab.features import BASE_GROUPS, CANDIDATES, LABEL
    from marketlab import radar as R
    rd = R.read("radar", R.latest_day())
    r = rd[rd.symbol == symbol]
    vals = r.iloc[0].to_dict() if len(r) else {}
    why = {"used": "feeds the chance: in the engine and shown to help in the Scorecard",
           "shown": "in the engine and on the map, but the Scorecard hasn't shown it helps (dashed)",
           "candidate": "built and tested separately; not used until switched on (Scorecard → Candidate signals)"}
    sigs = []
    for g, fs in list(BASE_GROUPS.items()) + list(CANDIDATES.items()):
        st = t["signals"].get(g)
        for f in fs:
            v = vals.get(f)
            sigs.append(dict(group=g, feature=f, label=LABEL.get(f, f), value=None if v is None or (isinstance(v, float) and not np.isfinite(v)) else round(float(v), 4),
                             status=st, available=row["signals"][g]["available"], why=why.get(st, "")))
    try:
        from tradeplan import plan as TP
        p = ((TP.load() or {}).get("plans") or {}).get(symbol) or {}
        ta = p.get("ta") or {}
    except Exception:
        ta = {}
    return dict(row=row, items=t["items"], signals=sigs, ta=dict(dir=ta.get("dir"), score=ta.get("score"), why=ta.get("why", [])),
                radar_day=t["radar_day"], plan_day=t["plan_day"])
