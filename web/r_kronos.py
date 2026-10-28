"""API: Market Lab → Kronos (experimental). Reads only results/kronos/, the price files and (for the side-by-side column)
the Market Lab ledger; nothing here feeds Kronos into the radar, the picks or the Tally."""
import json
import numpy as np, pandas as pd
from core import config as C, jobs
from web.api import route, cached, _mtime, prices, index, UserError

K = C.BASE / "kronos_lab"
RES = C.BASE / "results" / "kronos"
SETTINGS = RES / "settings.json"
UNITS = {"1d": "sessions", "5m": "five-minute bars"}
ARG_KEYS = ("list", "symbols", "model", "freq", "h", "lookback", "draws", "temp", "top_p", "device", "at", "replay", "step")


def _scored():
    from kronos_lab import score as S
    def build():
        fc = S.forecasts()
        return S.outcomes(fc, prices(), index()) if len(fc) else fc
    return cached("kronos_scored", (_mtime(S.FILE), _mtime(C.DATA / "prices.parquet"), _mtime(C.DATA / "intraday" / "state.json")), build)


def _key(r):
    return f"{r['model']}|{r['freq']}|{int(r['h'])}|{float(r['temp']):g}|{float(r['top_p']):g}"


def _settings_of(d):
    from kronos_lab import score as S
    if d.empty:
        return []
    g = d.groupby(S.SET, dropna=False).agg(forecasts=("symbol", "size"), days=("as_of", "nunique"), last=("as_of", "max"),
                                            scored=("realized_ret_pct", lambda x: int(x.notna().sum()))).reset_index()
    g = g.sort_values("last", ascending=False)
    return [dict(key=_key(r), label=S.setting(r), forecasts=int(r["forecasts"]), days=int(r["days"]), scored=int(r["scored"]),
                 last=str(pd.Timestamp(r["last"]))[:16]) for r in g.to_dict("records")]


def _pick(d, key):
    if d.empty:
        return d
    if not key:
        key = _settings_of(d)[0]["key"]
    m, f, h, t, p = key.split("|")
    return d[(d.model == m) & (d.freq == f) & (d.h == int(h)) & np.isclose(d.temp.astype(float), float(t)) & np.isclose(d.top_p.astype(float), float(p))]


def _f(v, n=4):
    try:
        v = float(v)
        return None if np.isnan(v) else round(v, n)
    except (TypeError, ValueError):
        return None


def _ml(day):
    """Market Lab's call for each stock on that night (close-to-close >=1%), for the side-by-side column. {} if none kept."""
    try:
        from marketlab import tally as T
        kind = T.pick_kind(str(pd.Timestamp(day).date()))
        if not kind:
            return {}
        c, _ = T._calls(kind, str(pd.Timestamp(day).date()), "cc", 1.0)
        return {r["symbol"]: dict(lean=r["lean"], chance=_f(r["chance"], 3), usual=_f(r["usual"], 3), pick=bool(r["pick"]))
                for r in c.to_dict("records")}
    except Exception:
        return {}


def load_settings():
    try:
        return json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return dict(nightly=False, args=[])


@route("GET", "kronos/status")
def status(_):
    from kronos_lab import forecast as F
    installed = dict(env=(C.BASE / ".venv-kronos" / "bin" / "python").exists(), code=(K / "vendor" / "Kronos" / "model").exists())
    hist = [x for x in jobs.history(80) if x["job"].startswith("kronos")]
    try:
        speed = json.loads(F.SPEED.read_text())
    except (OSError, ValueError):
        speed = {}
    if not speed:                                       # first runs, before speed.json existed: from their logs
        for x in hist:
            if x["result"] != "ok" or not x["job"].startswith("kronos-forecast"):
                continue
            t = open(x["path"], errors="ignore").read()
            try:
                n = int(t.split("Done: ", 1)[1].split(" forecasts", 1)[0])
                secs = float(t.rsplit(" in ", 1)[1].split(" s.", 1)[0])
                speed.setdefault("small|cpu|1d", dict(sec_per_unit=round(secs / max(1, n * 8 * 5), 5), runs=1, lookback=400, from_logs=True))
            except (IndexError, ValueError):
                pass
    d = _scored()
    five = F.BARS5.exists() and any(F.BARS5.glob("*.parquet"))
    return dict(installed=installed, ready=all(installed.values()), last=hist[0] if hist else None, history=hist[:8], total=len(d),
                settings=_settings_of(d), speed=speed, models={k: dict(context=v[2]) for k, v in F.MODELS.items()},
                intraday=five, running=jobs.current("kronos-forecast"), saved=load_settings())


@route("GET", "kronos/forecasts")
def forecasts(p):
    from kronos_lab import score as S
    d = _scored()
    if d.empty:
        return dict(ready=False, why="No forecasts yet: Run → Forecast latest day, or Replay to score past days straight away.")
    sel = _pick(d, p.get("set"))
    if sel.empty:
        raise UserError("no forecasts for that setting")
    r0 = sel.iloc[0]
    days = sorted(sel.as_of.unique(), reverse=True)
    day = pd.Timestamp(p["as_of"]) if p.get("as_of") else days[0]
    x = sel[sel.as_of == day].sort_values("pred_ret_pct", ascending=False)
    ml = _ml(day) if r0["freq"] == "1d" else {}
    rows, agree = [], [0, 0]
    for r in x.to_dict("records"):
        k_dir = "up" if r["p_up"] > 0.5 else "down" if r["p_up"] < 0.5 else "flat"
        m = ml.get(r["symbol"])
        ag = None if not m or m["lean"] not in ("up", "down") or k_dir == "flat" else m["lean"] == k_dir
        if ag is not None:
            agree[0] += ag
            agree[1] += 1
        rows.append(dict(symbol=r["symbol"], close=_f(r["close_asof"], 2), pred=_f(r["pred_ret_pct"], 3), p_up=_f(r["p_up"], 3), sd=_f(r["draw_sd_pct"], 3),
                         dir=k_dir, actual=_f(r.get("realized_ret_pct"), 3), excess=_f(r.get("excess_pct"), 3), mode=r["mode"],
                         ml_lean=m["lean"] if m else None, ml_chance=m["chance"] if m else None, ml_usual=m["usual"] if m else None,
                         ml_pick=m["pick"] if m else None, agree=ag))
    return dict(ready=True, set=_key(r0), label=S.setting(r0), freq=r0["freq"], h=int(r0["h"]), unit=UNITS[r0["freq"]],
                day=str(day)[:16] if r0["freq"] == "5m" else str(day.date()), days=[str(t)[:16] if r0["freq"] == "5m" else str(pd.Timestamp(t).date()) for t in days[:150]],
                rows=rows, ml=bool(ml), agree=dict(n=agree[1], yes=agree[0]), scored=bool(x.realized_ret_pct.notna().any()),
                up_share=_f((x.p_up > 0.5).mean(), 3), avg_pred=_f(x.pred_ret_pct.mean(), 3))


@route("GET", "kronos/score")
def score(p):
    from kronos_lab import score as S
    d = _scored()
    if d.empty:
        return dict(ready=False, why="No forecasts yet.")
    sel = _pick(d, p.get("set"))
    sm = S.summary(sel)
    out = dict(set=_key(sel.iloc[0]) if len(sel) else None, label=S.setting(sel.iloc[0]) if len(sel) else "", doc=(S.__doc__ or "").split("\n", 1)[1].strip())
    if sm is None or not len(sm):
        first = sel.as_of.min() if len(sel) else None
        left = ""
        if first is not None and sel.freq.iat[0] == "1d":
            done = int((prices().date.drop_duplicates() > first).sum())
            n = max(0, int(sel.h.iat[0]) - done)
            left = f" (the first, from {pd.Timestamp(first):%d %b}, needs {n} more session{'s' if n != 1 else ''})" if n else ""
        return dict(out, ready=False, why="Nothing scored yet for this setting: a forecast is scored once its horizon has passed"
                    + left + ". Run a replay to score past days now.")
    r = sm.iloc[0]
    verdict = ("ranks stocks better than chance" if pd.notna(r.ic_t) and r.ic_t >= 2 else
               "worse than chance at ranking" if pd.notna(r.ic_t) and r.ic_t <= -2 else "no reliable ranking skill yet")
    k = dict(forecasts=int(r.forecasts), days=int(r.days), stocks=int(r.stocks), hit_rate=_f(r.hit_rate, 2), best_guess=_f(r.best_guess, 2),
             share_up=_f(r.share_up, 2), skill=_f(r.skill, 4), brier=_f(r.brier, 4), brier_base=_f(r.brier_base, 4), mean_ic=_f(r.mean_ic, 4),
             ic_t=_f(r.ic_t, 2), spread=_f(r.spread_pct, 3), spread_t=_f(r.spread_t, 2), independent=int(r.independent_days), verdict=verdict,
             good=bool(pd.notna(r.ic_t) and r.ic_t >= 2), bad=bool(pd.notna(r.ic_t) and r.ic_t <= -2))
    bd = S.by_day(sel)
    days = [dict(as_of=str(x.as_of)[:16], ic=_f(x.ic), hit_rate=_f(x.hit_rate, 2), spread=_f(x.top_minus_bottom, 3), stocks=int(x.stocks))
            for x in bd.sort_values("as_of").itertuples()] if len(bd) else []
    cal = S.calibration(sel)
    sc = sel[sel.realized_ret_pct.notna()]
    sc = sc.sample(min(len(sc), 2500), random_state=0) if len(sc) else sc
    return dict(out, ready=True, kpis=k, by_day=days, mode_mix={k2: int(v) for k2, v in sc["mode"].value_counts().items()} if len(sc) else {},
                calibration=[dict(predicted=_f(x.predicted, 3), observed=_f(x.observed, 3), n=int(x.forecasts)) for x in cal.itertuples()] if len(cal) else [],
                points=[dict(x=_f(x.pred_ret_pct, 2), y=_f(x.realized_ret_pct, 2), label=f"{x.symbol} {pd.Timestamp(x.as_of):%d %b}", sym=x.symbol) for x in sc.itertuples()])


@route("GET", "kronos/stock")
def stock(p):
    """One forecast as candles: recent history, the mean predicted candles, the 10-90% range of the sampled closes, and what happened."""
    from kronos_lab import score as S, forecast as F
    d = _scored()
    sym = str(p.get("symbol", "")).upper()
    sel = _pick(d, p.get("set"))
    x = sel[(sel.symbol == sym) & (sel.as_of == pd.Timestamp(p.get("as_of")))] if len(sel) and p.get("as_of") else pd.DataFrame()
    if x.empty:
        raise UserError("no forecast for that stock and time")
    r = x.iloc[0]
    h = int(r.h)
    if r.freq == "5m":
        f = F.BARS5 / f"{sym}.parquet"
        g = pd.read_parquet(f).rename(columns={"ts": "date"}) if f.exists() else pd.DataFrame(columns=["date"])
        g["date"] = pd.to_datetime(g.date)
        g = g[(g.date >= r.as_of - pd.Timedelta(days=3)) & (g.date < r.as_of + pd.Timedelta(minutes=5 * h))].reset_index(drop=True)
        hist, after = g[g.date < r.as_of].tail(75), g[g.date >= r.as_of].head(h)
        fmt = "%Y-%m-%d %H:%M"
        fut = [t.strftime(fmt) for t in F.future_bars(r.as_of, h)]
    else:
        px = prices()
        g = px[px.symbol == sym].sort_values("date").reset_index(drop=True)
        i = g.index[g.date == r.as_of]
        if not len(i):
            raise UserError("no price on that day")
        i = int(i[0])
        hist, after = g.iloc[max(0, i - 44): i + 1], g.iloc[i + 1: i + 1 + h]
        fmt = "%Y-%m-%d"
        fut = [t.strftime(fmt) for t in after.date] + [f"+{k}" for k in range(len(after) + 1, h + 1)]
    bar = lambda t: dict(t=[v.strftime(fmt) for v in t.date], o=t.open.round(2).tolist(), h=t.high.round(2).tolist(), l=t.low.round(2).tolist(), c=t.close.round(2).tolist())
    path = json.loads(r.path)
    c0 = float(r.close_asof)
    prev = lambda k: c0 if k == 0 else path[k - 1]                 # older forecasts kept only closes: draw flat candles from them
    ohlc = json.loads(r.ohlc) if isinstance(r.ohlc, str) and r.ohlc else [[prev(k), max(prev(k), path[k]), min(prev(k), path[k]), path[k]] for k in range(len(path))]
    band = json.loads(r.band) if isinstance(r.band, str) and r.band else None
    return dict(symbol=sym, setting=S.setting(r), freq=r.freq, h=h, as_of=str(r.as_of)[:16] if r.freq == "5m" else str(pd.Timestamp(r.as_of).date()), close_asof=_f(r.close_asof, 2),
                pred=_f(r.pred_ret_pct, 3), p_up=_f(r.p_up, 3), actual=_f(r.realized_ret_pct, 3), history=bar(hist), after=bar(after),
                future=fut, forecast=dict(o=[v[0] for v in ohlc], h=[v[1] for v in ohlc], l=[v[2] for v in ohlc], c=[v[3] for v in ohlc]),
                band=band, has_candles=isinstance(r.ohlc, str) and bool(r.ohlc))


@route("POST", "kronos/settings")
def save_settings(_, body):
    """Nightly toggle and the run options it uses (Fetch + run all starts a Kronos forecast at the end when on)."""
    b = body or {}
    s = load_settings()
    if "nightly" in b:
        s["nightly"] = bool(b["nightly"])
    if "args" in b:
        args = [str(a) for a in (b.get("args") or [])]
        if any(not jobs.SAFE_ARG.match(a) or a.split("=", 1)[0] not in ARG_KEYS or a.startswith(("replay=", "step=")) for a in args):
            raise UserError("not an allowed Kronos option")
        s["args"] = args
    RES.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, indent=1))
    return s
