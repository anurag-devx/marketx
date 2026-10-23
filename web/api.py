"""JSON API for the web app. Every function reads the same files and database the agents write; nothing here trades,
and nothing returns a key or a secret. GET handlers take (params); POST handlers take (params, body)."""
import json, math, threading, time
from datetime import date, datetime
import numpy as np, pandas as pd
from core import config as C, jobs

ROUTES = {}
_lock = threading.Lock()
_cache = {}


class UserError(ValueError):
    pass


def route(method, name):
    def deco(fn):
        ROUTES[(method, name)] = fn
        return fn
    return deco


# ---------------- JSON ----------------
def _clean(o):
    if isinstance(o, pd.DataFrame):
        return [_clean(r) for r in o.to_dict("records")]
    if isinstance(o, pd.Series):
        return _clean(o.to_dict())
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set, np.ndarray)):
        return [_clean(v) for v in o]
    if isinstance(o, (pd.Timestamp, datetime)):
        return None if pd.isna(o) else o.isoformat()
    if isinstance(o, date):
        return o.isoformat()
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return None if (math.isnan(o) or math.isinf(o)) else float(o)
    if o is pd.NaT or (o is not None and not isinstance(o, (str, bool, int)) and pd.api.types.is_scalar(o) and pd.isna(o)):
        return None
    return o


def dumps(o):
    return json.dumps(_clean(o), ensure_ascii=False, separators=(",", ":"))


# ---------------- cached data ----------------
def _mtime(p):
    return p.stat().st_mtime if p.exists() else 0


def cached(key, mtime, build):
    """Rebuild when the source file changes; one build at a time."""
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    v = build()
    with _lock:
        _cache[key] = (mtime, v)
    return v


def prices():
    p = C.DATA / "prices.parquet"
    def build():
        d = pd.read_parquet(p)
        d["date"] = pd.to_datetime(d.date).dt.tz_localize(None).dt.normalize()
        return d
    return cached("prices", _mtime(p), build) if p.exists() else None


def index():
    p = C.DATA / "index.parquet"
    def build():
        i = pd.read_parquet(p)
        i["date"] = pd.to_datetime(i.date).dt.tz_localize(None).dt.normalize()
        return i.sort_values("date")
    return cached("index", _mtime(p), build) if p.exists() else None


def universe():
    p = C.DATA / "universe.csv"
    return cached("universe", _mtime(p), lambda: pd.read_csv(p)) if p.exists() else pd.DataFrame(columns=["Company Name", "Industry", "Symbol"])


def market_panel():
    from core import market as mk
    p = C.DATA / "prices.parquet"
    return cached("market", _mtime(p), lambda: mk.prepare(prices())) if p.exists() else None


def q(sql, params=None):
    import duckdb
    if not C.DB_PATH.exists():
        return pd.DataFrame()
    try:
        con = duckdb.connect(str(C.DB_PATH), read_only=True)
        try:
            return con.execute(sql, params or []).df()
        finally:
            con.close()
    except Exception:
        return pd.DataFrame()


def warm():
    """Build the heavy caches in the background at start, so the first page is quick."""
    def run():
        try:
            prices(); market_panel()
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


# ---------------- status (every page) ----------------
LANE_PROBE = {"main": "fetch", "news": "news", "history": "history-all", "lab": "debate"}


@route("GET", "status")
def status(_):
    from core import freshness, quality
    px = prices()
    last = None if px is None else px.date.max()
    age = None if last is None else (pd.Timestamp.now().normalize() - last).days
    qs = quality.summary() or {}
    stale = freshness.stale()
    running = []
    for lane, probe in LANE_PROBE.items():
        a = jobs.activity(probe)
        if a:
            running.append(dict(lane=lane, job=a["job"], label=a["label"], step=a["step"], steps=a["steps"], elapsed=a["elapsed"],
                                frac=a.get("frac", 0.5), title=JOB_LABEL.get(a["job"], a["job"])))
    log = {}
    f = C.DATA / "fetch_log.json"
    if f.exists():
        try:
            log = json.loads(f.read_text())
        except Exception:
            pass
    return dict(last_bar=last, age_days=age, fresh=age is not None and age <= 4, last_download=log.get("when"),
                quality=dict(status=qs.get("status"), errors=qs.get("errors"), warnings=qs.get("warnings"),
                             quarantined=qs.get("quarantined", [])),
                stale=stale, running=running, now=pd.Timestamp.now().floor("s"))


# ---------------- jobs ----------------
JOB_LABEL = {"strategy-research": "Strategy research", "screener-experiments": "Screener experiments", "screener-experiments-force": "Re-run all experiments",
             "history-all": "Download all history", "news-backfill": "Download older news", "announcements-backfill": "Download older NSE filings",
             "company-info": "Download company sectors and shares", "search-index": "Build the search index", "library-ingest": "Add to documents",
             "news-articles": "Read news article text", "news-research": "Run the news study", "macro-fetch": "Fetch macro data", "macro-research": "Run macro study",
             "debate": "Debate analysis", "debate-eval": "Score forecasts", "debate-replay": "Replay 20 sessions", "stock-lists": "Download NSE index lists",
             "install-ai": "Install missing packages", "news-check": "Check news sources", "news-retag": "Re-link headlines", "fetch-full": "Re-download all prices",
             "fetch-broad": "Download all NSE stocks", "quality": "Data quality checks", "regime": "Run regime", "strategy": "Run strategy",
             "technical": "Run technical", "risk": "Run risk", "validation": "Run validation", "report": "Build report",
             "fetch+pipeline": "Fetch data + run all agents", "pipeline": "Run all agents", "fetch": "Fetch prices", "news": "Fetch news",
             "announcements": "Fetch news and filings", "fundamentals": "Update quarterly results",
             "fundamentals-backfill": "Download results history", "macro-all": "Fetch macro data + study", "market-report": "Write market report",
             "debate-batch": "Nightly forecasts", "backtest": "Run backtest + analysis", "screener": "Run screener"}
STARTABLE = set(jobs.COMMANDS)                # only the fixed commands in core/jobs.py; arguments are dates and flags only


@route("POST", "job/start")
def job_start(_, body):
    name = str((body or {}).get("name", ""))
    if name not in STARTABLE or name not in jobs.COMMANDS:
        raise UserError("not a job you can start from here")
    if jobs.busy(name):
        raise UserError("another job is running in that lane")
    args = [str(a) for a in ((body or {}).get("args") or [])]
    log = jobs.start(name, args)          # jobs.start only accepts dates and flags as arguments
    return dict(started=name, log=log.name)


@route("POST", "job/stop")
def job_stop(_, body):
    lane = str((body or {}).get("lane", "main"))
    if lane not in LANE_PROBE:
        raise UserError("unknown lane")
    jobs.stop(LANE_PROBE[lane])
    return dict(stopped=lane)


@route("GET", "job/log")
def job_log(p):
    lane = p.get("lane", "main")
    j = jobs.current(LANE_PROBE.get(lane, "fetch"))
    if not j:
        files = sorted((C.BASE / "logs" / "jobs").glob("*.log"), key=lambda f: f.stat().st_mtime)
        if not files:
            return dict(running=False, lines=[])
        path = files[-1]
    else:
        path = j["log"]
    return dict(running=bool(j), file=str(path).rsplit("/", 1)[-1], lines=jobs.tail(path, 200).splitlines()[-200:])


# ---------------- search ----------------
@route("GET", "search")
def search(p):
    term = str(p.get("q", "")).strip().lower()
    u = universe()
    if not term:
        return []
    m = u[u.Symbol.str.lower().str.startswith(term) | u["Company Name"].str.lower().str.contains(term, regex=False)]
    m = m.assign(_exact=(m.Symbol.str.lower() == term)).sort_values(["_exact", "Symbol"], ascending=[False, True]).head(12)
    return [dict(symbol=r.Symbol, company=r["Company Name"], sector=r.Industry) for _, r in m.iterrows()]


# ---------------- overview ----------------
AGENTS = [("fetch", "Data"), ("regime", "Market regime"), ("screener", "Screener"), ("strategy", "Strategy"), ("technical", "Technical"),
          ("risk", "Risk"), ("validation", "Validation"), ("report", "Supervisor"), ("announcements", "News and filings"),
          ("macro-all", "Macro"), ("market-report", "Market report"), ("debate", "Debate Lab")]
STATE = {"up_calm": "Up · calm", "up_volatile": "Up · volatile", "down_calm": "Down · calm", "down_volatile": "Down · volatile"}


def extra_status(k):
    """(state, text) for agents that keep their own files instead of agent_runs rows; None for the rest."""
    try:
        if k == "macro-all":
            from core import macro as M
            lf = M.last_fetch()
            meta = C.RESULTS / "macro" / "meta.json"
            study = json.loads(meta.read_text())["when"][5:16] if meta.exists() else None
            if not lf:
                return "idle", "never fetched"
            src = {x.replace(" vol", "").replace(" tone", "") for x in lf["status"]}
            bad = {x.replace(" vol", "").replace(" tone", "") for x, v in lf["status"].items() if not str(v).startswith(("ok", "skipped"))}
            return ("ok" if not bad and study else "warn"), f"data {lf['when'][5:16]} · study {study or 'not run'}" + (f" · {len(bad)}/{len(src)} sources failed" if bad else "")
        if k == "market-report":
            from research import report_writer as W
            days = W.saved_reports()
            if not days:
                return "idle", "no report yet"
            rec = W.load_report(days[0]) or {}
            used = rec.get("report", {}).get("writer", {}).get("model")
            return "ok", f"{days[0]} · {'model ' + used if used else 'template'}"
        if k == "debate":
            from debate import store as DS
            idx = DS.index()
            if idx.empty:
                return "idle", "no analysis yet"
            out = DS.outcomes()
            return "ok", f"{len(idx)} analyses · {0 if out is None else len(out):,} forecasts scored"
        if k == "announcements":
            from core import announcements as A, news as N
            info, (_, _, nw) = A.last_fetch(), N.load()
            ok = info and not info.get("errors")
            return ("ok" if ok and nw else "warn" if (info or nw) else "idle"), f"news {str(nw)[5:16] if nw else 'never'} · filings {info['when'][5:16] if info else 'never'}"
    except Exception as e:
        return "warn", f"needs setup ({type(e).__name__})"
    return None


def _agents():
    lr = q("""select agent, status, started, message from (select *, row_number() over (partition by agent order by started desc) rn
              from agent_runs) where rn = 1""")
    lr = lr.set_index("agent") if len(lr) else pd.DataFrame()
    busy = jobs.active_agents()
    out = []
    for key, name in AGENTS:
        k = "data" if key == "fetch" else key
        a = busy.get(key)
        if a:
            out.append(dict(key=key, name=name, state="run", sub=f"{a['label']} · step {a['step']}/{a['steps']}"))
        elif extra_status(key):
            st, txt = extra_status(key)
            out.append(dict(key=key, name=name, state=st, sub=txt))
        elif len(lr) and k in lr.index:
            r = lr.loc[k]
            st = "ok" if r.status == "ok" else ("bad" if r.status == "error" else "warn")
            out.append(dict(key=key, name=name, state=st, sub=pd.Timestamp(r.started).strftime("%d %b %H:%M"),
                            note=str(r.message)[:120] if r.status == "error" else None))
        else:
            out.append(dict(key=key, name=name, state="idle", sub="—"))
    return out


@route("GET", "overview")
def overview(_):
    reg = q("select * from regime_daily order by date desc limit 1")
    r = reg.iloc[0].to_dict() if len(reg) else None
    regime = None
    if r:
        regime = dict(date=r["date"], state=STATE.get(r["market_state"], r["market_state"]), days=r.get("days_in_state"),
                      score=r.get("regime_score"), nifty_vs_200=r.get("nifty_vs_200d_pct"), vix=r.get("vix"), vix_median=r.get("vix_median"),
                      size=r.get("size_multiplier"), active=r.get("active_strategies"), note=r.get("note"))
    day = r["date"] if r else None
    trades = q("select symbol, strategy_name, sector, entry, stop, target, quantity, value, risk_rs, setup_score from decisions "
               "where cast(date as date) = cast(? as date) and approved order by setup_score desc", [day]) if day else pd.DataFrame()
    exits = q("select symbol, strategy, days_held, action, detail from exits_due where cast(date as date) = cast(? as date) and action <> 'hold'",
              [day]) if day else pd.DataFrame()
    pend = q("select count(*) n from proposals where status = 'pending'")
    return dict(regime=regime, agents=_agents(), trades=trades, exits=exits, pending=int(pend.n.iat[0]) if len(pend) else 0,
                market=_market_today(), backdrop=_backdrop(), shortlist=_shortlist(10), results=_results_today())


def _market_today():
    p = market_panel()
    if p is None:
        return None
    from core import market as mk
    d, sec, s = mk.snapshot(p, universe())
    d = d.dropna(subset=["r1"])
    cols = ["symbol", "company", "sector", "close", "r1", "vol_ratio"]
    sec = sec.sort_values("day", ascending=False)
    idx = index()
    nifty = None
    if idx is not None and len(idx) > 1:
        i = idx.dropna(subset=["nifty"])
        nifty = dict(value=i.nifty.iat[-1], chg=100 * (i.nifty.iat[-1] / i.nifty.iat[-2] - 1),
                     spark=i.nifty.tail(60).round(1).tolist(), vix=i.vix.dropna().iat[-1] if "vix" in i and i.vix.notna().any() else None)
    return dict(summary=s, nifty=nifty, gainers=d.nlargest(6, "r1")[cols], losers=d.nsmallest(6, "r1")[cols],
                sectors_top=sec.head(4), sectors_bottom=sec.tail(4).iloc[::-1],
                unusual=d[d.vol_ratio >= 3].nlargest(6, "vol_ratio")[cols])


def _backdrop():
    try:
        from core import macro as M
        rows = {r["series"]: r for r in M.summary()}
    except Exception:
        return []
    return [rows[s] for s in ("DCOILBRENTEU", "DEXINUS", "DGS10", "VIXCLS", "GPRD", "FII_NET") if s in rows]


def _shortlist(n):
    d = q("select screen_date, symbol, company_name, sector, close, setups_passed, ranking_score, rank, qualification_reasons "
          "from screener_results where screen_date = (select max(screen_date) from screener_results) and qualified "
          "order by rank nulls last limit ?", [n])
    return d.rename(columns={"company_name": "company"}) if len(d) else d


def _quarters():
    from core import fundamentals as F
    p = F.DIR / "quarters.parquet"
    return cached("quarters", _mtime(p), lambda: pd.read_parquet(p)) if p.exists() else pd.DataFrame()


def _results_today():
    from core import fundamentals as F
    qd = _quarters()
    recent = qd[pd.to_datetime(qd.filed_at) >= pd.Timestamp.now() - pd.Timedelta(days=3)] if len(qd) else qd
    cols = ["symbol", "quarter", "filed_at", "revenue_yoy", "profit_yoy", "sue_profit", "react_rel"]
    cal = F.calendar()
    return dict(recent=recent.sort_values("filed_at", ascending=False)[[c for c in cols if c in recent]].head(10) if len(recent) else [],
                upcoming=cal.head(10) if len(cal) else [])


# ---------------- stock ----------------
@route("GET", "stock")
def stock(p):
    sym = str(p.get("symbol", "")).upper().strip()
    px = prices()
    if px is None:
        raise UserError("no price data yet")
    g = px[px.symbol == sym].sort_values("date")
    if g.empty:
        raise UserError(f"no prices for {sym}")
    u = universe()
    row = u[u.Symbol == sym]
    c = g.close
    g = g.assign(sma50=c.rolling(50).mean(), sma200=c.rolling(200).mean())
    last = g.iloc[-1]
    ret = lambda n: 100 * (c.iat[-1] / c.iat[-1 - n] - 1) if len(c) > n else None
    tail = g[g.date >= g.date.max() - pd.Timedelta(days=int(p.get("days", 1100)))]
    bars = dict(t=tail.date.dt.strftime("%Y-%m-%d").tolist(), o=tail.open.round(2).tolist(), h=tail.high.round(2).tolist(),
                l=tail.low.round(2).tolist(), c=tail.close.round(2).tolist(), v=tail.volume.astype(float).tolist(),
                sma50=tail.sma50.round(2).tolist(), sma200=tail.sma200.round(2).tolist())
    hi, lo = g.high.tail(252).max(), g.low.tail(252).min()
    info = dict(symbol=sym, company=row["Company Name"].iat[0] if len(row) else sym, sector=row.Industry.iat[0] if len(row) else None,
                last_date=last.date, close=last.close, chg=ret(1), r1m=ret(21), r3m=ret(63), r1y=ret(252), hi52=hi, lo52=lo,
                from_hi=100 * (last.close / hi - 1) if hi else None, avg_turnover_cr=float((g.close * g.volume).tail(20).mean() / 1e7))
    sig = q("select cast(s.date as date) date, s.strategy, d.approved, d.reject_reason from signals s left join decisions d on "
            "d.symbol = s.symbol and d.strategy = s.strategy and cast(d.date as date) = cast(s.date as date) where s.symbol = ? "
            "order by s.date desc limit 10", [sym])
    scr = q("select screen_date, qualified, rank, qualification_reasons, rejection_reasons from screener_results where symbol = ? "
            "order by screen_date desc limit 1", [sym])
    screener = None
    if len(scr):
        s = scr.iloc[0]
        screener = dict(date=s.screen_date, qualifies=bool(s.qualified), rank=s["rank"] if s.qualified else None,
                        reasons=s.qualification_reasons or s.rejection_reasons)
    return dict(info=info, bars=bars, signals=sig, screener=screener, results=_stock_results(sym), news=_stock_news(sym))


def _stock_results(sym):
    from core import fundamentals as F
    qd = _quarters()
    g = qd[qd.symbol == sym].sort_values("period_end", ascending=False).head(12) if len(qd) else qd
    cols = ["quarter", "basis", "filed_at", "revenue_cr", "revenue_yoy", "profit_cr", "profit_yoy", "margin_pct", "ebitda_margin",
            "eps", "sue_profit", "react_rel", "drift_20", "revised"]
    cal = F.calendar()
    nxt = cal[cal.symbol == sym]
    return dict(quarters=g[[c for c in cols if c in g]] if len(g) else [],
                next=dict(date=nxt.date.iat[0], source=nxt.source.iat[0]) if len(nxt) else None)


def _stock_news(sym, n=40):
    from core import news as N, announcements as A
    out = []
    try:
        nw = N.for_symbol(sym).head(n)
        for r in nw.itertuples():
            out.append(dict(kind="news", published=r.published, title=r.title, link=r.link, source=r.source,
                            type=r.event_type or None, type_why=getattr(r, "type_why", "") or None,
                            tone=r.tone if r.tone in ("positive", "negative") else None, tone_why=getattr(r, "tone_why", "") or None,
                            roundup=bool(r.roundup)))
    except Exception:
        pass
    try:
        an = A.load()
        an = an[an.symbol == sym].sort_values("published", ascending=False).head(20)
        for r in an.itertuples():
            out.append(dict(kind="filing", published=r.published, title=(r.subject or r.text or "")[:200], link=r.link, source="NSE",
                            type=r.category, type_why=None, tone=None, tone_why=None, roundup=False))
    except Exception:
        pass
    return sorted(out, key=lambda x: pd.Timestamp(x["published"]) if x["published"] is not None else pd.Timestamp(0), reverse=True)[:n]


# route modules register themselves on import
from web import r_today, r_screener, r_agents, r_research, r_tools, r_setup  # noqa: E402,F401
