"""Event study: do news and NSE announcements move stocks?

For every event (a headline linked to a stock, or an NSE filing), find its EVENT DAY:
  - published on a trading day at or before 15:30 IST  -> that day (the market could react the same day)
  - published after 15:30, on a holiday or a weekend    -> the next trading day
  - time unknown (exactly 00:00)                         -> that day if it is a trading day (dated sitemaps)
Then measure, per stock, ABNORMAL returns = stock return minus the Nifty 50's return on the same day:
  AR(0)        event day             CAR(-5,-1)  five days before (did the price move before the news?)
  CAR(1,5)     the week after        CAR(1,20)   the month after (does the move continue? that is the tradable part)
  abnormal volume = event-day volume / median volume of days -60..-6
BASELINE: the same stocks on days without any event within +-5 trading days. If news matters, |AR(0)| on event
days is clearly larger than on quiet days.
Uncertainty: events on the same day move together, so averages and t-stats are computed on daily means.
Results describe the past sample; event coverage is only as complete as the stored history."""
import numpy as np, pandas as pd

CUTOFF = pd.Timedelta(hours=15, minutes=30)
WINDOWS = {"car_pre": (-5, -1), "ar0": (0, 0), "car_1_5": (1, 5), "car_1_20": (1, 20)}


def returns_panel(px, idx):
    """Wide daily returns (date x symbol), the Nifty's daily return, abnormal returns and volume."""
    close = px.pivot_table(index="date", columns="symbol", values="close").sort_index()
    vol = px.pivot_table(index="date", columns="symbol", values="volume").reindex(close.index)
    r = close.pct_change(fill_method=None)
    n = idx.dropna(subset=["nifty"]).set_index("date").nifty.reindex(close.index)
    nr = n.pct_change(fill_method=None)
    ar = r.sub(nr, axis=0)
    return dict(ret=r, ar=ar, nifty=nr, vol=vol, dates=close.index)


def event_day(ts, dates):
    """Trading date on which the market could first react to something published at `ts` (IST)."""
    ts = pd.Timestamp(ts)
    if pd.isna(ts):
        return pd.NaT
    day = ts.normalize()
    i = dates.searchsorted(day)
    if i >= len(dates):
        return pd.NaT
    same = dates[i] == day
    t = ts - day
    if same and (t <= CUTOFF or t == pd.Timedelta(0)):
        return dates[i]
    j = i + 1 if same else i
    return dates[j] if j < len(dates) else pd.NaT


def events_from(news=None, ann=None, include_roundups=False):
    """One row per (stock, item): symbol, published, source_type, category, tone, title."""
    rows = []
    if news is not None and len(news):
        n = news if include_roundups else news[~news.roundup.fillna(False)]
        for r in n.itertuples():
            for s in str(r.symbols or "").split(","):
                if s:
                    rows.append((s, r.published, "news", r.event_type or "Unclassified", r.tone or "neutral", r.title))
    if ann is not None and len(ann):
        from core.news import tone
        for r in ann.itertuples():
            if r.symbol:
                rows.append((r.symbol, r.published, "announcement", r.category, tone(f"{r.subject} {r.text}"), f"{r.subject} {r.text}"[:200]))
    return pd.DataFrame(rows, columns=["symbol", "published", "source_type", "category", "tone", "title"])


def measure(events, P):
    """Adds event day and abnormal returns / volume to each event. Events on non-traded stocks are dropped."""
    if events.empty:
        return events
    dates = P["dates"]
    ar, vol = P["ar"], P["vol"]
    e = events.copy()
    e["d0"] = [event_day(t, dates) for t in e.published]
    e = e[e.d0.notna() & e.symbol.isin(ar.columns)].copy()
    pos = dates.get_indexer(e.d0)
    cols = ar.columns.get_indexer(e.symbol)
    A = ar.to_numpy()
    V = vol.to_numpy()
    T = len(dates)
    for name, (a, b) in WINDOWS.items():
        vals = np.full(len(e), np.nan)
        for k, (p, c) in enumerate(zip(pos, cols)):
            lo, hi = p + a, p + b
            if lo < 0 or hi >= T:
                continue
            w = A[lo:hi + 1, c]
            if np.isnan(w).any():
                continue
            vals[k] = w.sum()
        e[name] = 100 * vals
    av = np.full(len(e), np.nan)
    for k, (p, c) in enumerate(zip(pos, cols)):
        if p - 60 < 0:
            continue
        base = V[p - 60:p - 5, c]
        base = base[~np.isnan(base)]
        if len(base) >= 20 and np.median(base) > 0 and not np.isnan(V[p, c]):
            av[k] = V[p, c] / np.median(base)
    e["abn_volume"] = av
    return e


def baseline(events, P, sample=200000, seed=7):
    """|AR| on quiet days: stock-days with no event within +-5 trading days, same stocks as the events."""
    ar = P["ar"]
    syms = [s for s in events.symbol.unique() if s in ar.columns]
    if not syms:
        return pd.Series(dtype=float)
    dates = P["dates"]
    busy = np.zeros((len(dates), len(ar.columns)), dtype=bool)
    ci = ar.columns.get_indexer(events.symbol)
    pi = dates.get_indexer(events.d0)
    for p, c in zip(pi, ci):
        if p >= 0 and c >= 0:
            busy[max(0, p - 5):p + 6, c] = True
    lo = max(0, dates.get_loc(events.d0.min()) - 60)     # the events' period, widened by ~3 months each side
    hi = min(len(dates) - 1, dates.get_loc(events.d0.max()) + 60)
    span = (dates >= dates[lo]) & (dates <= dates[hi])
    sub = ar.loc[span, syms].to_numpy()
    quiet = ~busy[span][:, ar.columns.get_indexer(syms)] & ~np.isnan(sub)
    vals = 100 * sub[quiet]
    if len(vals) > sample:
        vals = np.random.default_rng(seed).choice(vals, sample, replace=False)
    return pd.Series(vals)


def _t(x, dates):
    """Mean and t-stat using daily averages (events on the same day are not independent)."""
    d = pd.Series(np.asarray(x, float), index=pd.to_datetime(dates)).dropna()
    if len(d) == 0:
        return np.nan, np.nan, 0
    daily = d.groupby(level=0).mean()
    n = len(daily)
    if n < 5 or daily.std() == 0:
        return float(daily.mean()), np.nan, n
    return float(daily.mean()), float(daily.mean() / (daily.std() / np.sqrt(n))), n


def summarise(m, base, by=None, min_events=20):
    """Per group: events, |AR(0)| vs quiet days, signed moves by tone, pre-event drift, after-event drift, volume."""
    groups = [("All events", m)] if by is None else list(m.groupby(by))
    quiet_abs = float(base.abs().mean()) if len(base) else np.nan
    quiet_big = float((base.abs() >= 3).mean() * 100) if len(base) else np.nan
    rows = []
    for k, g in groups:
        if len(g) < min_events:
            continue
        a0 = g.ar0.abs()
        row = dict(group=k if not isinstance(k, tuple) else " · ".join(map(str, k)), events=len(g), stocks=g.symbol.nunique(),
                   days=g.d0.nunique(), abs_move_event_day=a0.mean(), abs_move_quiet_day=quiet_abs,
                   times_bigger=a0.mean() / quiet_abs if quiet_abs else np.nan,
                   pct_moves_over_3=100 * (a0 >= 3).mean(), pct_quiet_over_3=quiet_big,
                   abn_volume_median=g.abn_volume.median())
        for col, lab in (("ar0", "event_day"), ("car_pre", "before_5d"), ("car_1_5", "after_1_5d"), ("car_1_20", "after_1_20d")):
            mean, t, n = _t(g[col], g.d0)
            row[f"{lab}_mean"], row[f"{lab}_t"] = mean, t
        rows.append(row)
    out = pd.DataFrame(rows, columns=SUMMARY_COLS) if not rows else pd.DataFrame(rows)
    return out.sort_values("times_bigger", ascending=False) if by is not None and len(out) else out


SUMMARY_COLS = ["group", "events", "stocks", "days", "abs_move_event_day", "abs_move_quiet_day", "times_bigger", "pct_moves_over_3",
                "pct_quiet_over_3", "abn_volume_median"] + [f"{l}_{k}" for l in ("event_day", "before_5d", "after_1_5d", "after_1_20d")
                                                           for k in ("mean", "t")]


def by_tone_drift(m, min_events=20):
    """Does the move continue after the event? Signed by tone (positive / negative headlines)."""
    out = []
    for (st, tn), g in m.groupby(["source_type", "tone"]):
        if len(g) < min_events or tn == "neutral":
            continue
        for col, lab in (("ar0", "event day"), ("car_1_5", "days 1-5"), ("car_1_20", "days 1-20")):
            mean, t, n = _t(g[col], g.d0)
            out.append(dict(source=st, tone=tn, window=lab, events=len(g), mean_abnormal_pct=mean, t=t, days=n))
    return pd.DataFrame(out)


def big_move_attribution(P, m, threshold=5.0, start=None):
    """Of all stock-days with |abnormal return| >= threshold, how many had an event that day or the day before?
    Only days the stored history covers count (a day with no stored items at all is a gap, not 'no news')."""
    ar = 100 * P["ar"]
    if start is not None:
        ar = ar[ar.index >= pd.Timestamp(start)]
    covered = set(m.d0)
    ar = ar[ar.index.isin(covered)]
    if ar.empty:
        return dict(big_moves=0)
    big = ar.stack()
    big = big[big.abs() >= threshold]
    if big.empty:
        return dict(big_moves=0)
    keys = set(zip(m.symbol, m.d0))
    dates = P["dates"]
    prev = {d: dates[i - 1] for i, d in enumerate(dates) if i > 0}
    hit = [((s, d) in keys) or ((s, prev.get(d)) in keys) for d, s in big.index]
    return dict(big_moves=int(len(big)), with_event=int(sum(hit)), pct_with_event=100 * float(np.mean(hit)),
                threshold=threshold, days_covered=int(len(ar)), from_date=str(ar.index.min().date()))


def run(px, idx, news, ann, include_roundups=False, start=None, min_events=20):
    """Full study. Returns a dict of tables (also used by the dashboard)."""
    P = returns_panel(px, idx)
    ev = events_from(news, ann, include_roundups)
    if start is not None and len(ev):
        ev = ev[ev.published >= pd.Timestamp(start)]
    m = measure(ev, P)
    if len(m):
        try:
            from core.company_info import load as info
            m = m.merge(info()[["symbol", "sector"]], on="symbol", how="left")
            m["sector"] = m.sector.fillna("Unknown")
        except Exception:
            pass
    if m.empty:
        return dict(events=m, summary=pd.DataFrame(), by_source=pd.DataFrame(), by_category=pd.DataFrame(), by_sector=pd.DataFrame(),
                    by_tone=pd.DataFrame(), big_moves={}, coverage={})
    base = baseline(m, P)
    cov = dict(events=len(m), stocks=int(m.symbol.nunique()), first=str(m.d0.min().date()), last=str(m.d0.max().date()),
               news=int((m.source_type == "news").sum()), announcements=int((m.source_type == "announcement").sum()))
    return dict(events=m, summary=summarise(m, base, min_events=1), by_source=summarise(m, base, "source_type", min_events),
                by_category=summarise(m, base, ["source_type", "category"], min_events),
                by_sector=summarise(m, base, "sector", min_events) if "sector" in m else pd.DataFrame(),
                by_tone=by_tone_drift(m, min_events), big_moves=big_move_attribution(P, m, start=m.d0.min()), coverage=cov)




def save(res):
    """Writes the study's tables to results/news_events/ (the News research page reads them)."""
    import json
    from core.config import RESULTS
    out = RESULTS / "news_events"
    out.mkdir(parents=True, exist_ok=True)
    for k in ("summary", "by_source", "by_category", "by_sector", "by_tone"):
        if isinstance(res.get(k), pd.DataFrame):
            res[k].to_csv(out / f"{k}.csv", index=False)
    ev = res.get("events")
    if isinstance(ev, pd.DataFrame) and len(ev):
        ev.to_parquet(out / "events.parquet", index=False)
    meta = dict(when=str(pd.Timestamp.now().floor("s")), coverage=res.get("coverage", {}), big_moves=res.get("big_moves", {}))
    (out / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    return out


if __name__ == "__main__":
    import sys
    from core.data import load_raw
    from core import news, announcements
    px, idx, _ = load_raw(broad=True)
    res = run(px, idx, news.load_store(), announcements.load(), include_roundups="roundups" in sys.argv)
    out = save(res)
    print("Coverage:", res["coverage"])
    print("Big moves:", res["big_moves"])
    if len(res["summary"]):
        print(res["summary"].T.to_string())
    print("Saved to", out)
