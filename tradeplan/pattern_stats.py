"""History test for candlestick patterns: Nifty 500 daily bars since 2015. For every pattern and direction, how often the
price moved the pattern's way 1 and 5 sessions later (from the pattern day's close), against the normal rate for all
stock-days, overall and split 2015-2022 / 2023+ (a pattern whose edge flips between the halves isn't reliable).
Buckets: all · context (the prior trend the pattern needs) · strong (3+ of trend, location, volume, confirmation; the
confirmation is the next close, so 'strong' rows are measured from that next close, not the pattern day).
Neutral patterns (doji, inside bar): how often the 5-session move was >= the stock's typical 5-session move.
Output: results/patterns/stats.json. Run: python -m tradeplan.pattern_stats"""
import json, time
import numpy as np, pandas as pd
from core import config as C
from . import patterns as PT

OUT = C.BASE / "results" / "patterns"
SPLIT = pd.Timestamp("2023-01-01")


def events(g, prefer_talib=True):
    """One row per detected pattern for one stock, with context checks and forward returns."""
    g = g.sort_values("date").reset_index(drop=True)
    sig, src = PT.detect(g, prefer_talib)
    ctx = PT.context(g)
    c = g.close.astype(float)
    f1, f5 = c.shift(-1) / c - 1, c.shift(-5) / c - 1
    f5c = c.shift(-6) / c.shift(-1) - 1                                   # from the confirming close
    mv5 = (c.shift(-5) / c - 1).abs()
    typ5 = mv5.shift(5).rolling(250, min_periods=60).median()               # the stock's typical 5-session move, known by then
    rows = []
    for p in PT.PATTERNS:
        s = sig[p].to_numpy()
        for i in np.nonzero(s)[0]:
            v = int(s[i])
            gr = PT.grade(p, v, ctx.iloc[i])
            rows.append((p, "bullish" if v == 1 else "bearish" if v == -1 else "neutral", g.date.iat[i], gr["trend"], gr["strength"],
                         f1.iat[i], f5.iat[i], f5c.iat[i], mv5.iat[i], typ5.iat[i]))
    ev = pd.DataFrame(rows, columns=["pattern", "direction", "date", "trend", "strength", "f1", "f5", "f5c", "mv5", "typ5"])
    base = pd.DataFrame({"date": g.date, "f1": f1, "f5": f5, "big5": mv5 >= typ5})
    return ev, base, src


def _row(e, base, pattern, direction, bucket):
    if direction == "neutral":
        x = e.dropna(subset=["mv5", "typ5"])
        hit = (x.mv5 >= x.typ5)
        b = base.big5.dropna().mean()
        win5, win1, avg5 = hit.mean(), None, None
        halves = [hit[x.date < SPLIT].mean(), hit[x.date >= SPLIT].mean()]
        b1 = None
    else:
        s = 1 if direction == "bullish" else -1
        r5 = e.f5c if bucket == "strong" else e.f5
        x = e.assign(r5=r5).dropna(subset=["r5"])
        win5, win1, avg5 = (s * x.r5 > 0).mean(), (s * x.f1.dropna() > 0).mean(), 100 * (s * x.r5).mean()
        b = (s * base.f5.dropna() > 0).mean()
        b1 = (s * base.f1.dropna() > 0).mean()
        halves = [(s * x.r5[x.date < SPLIT] > 0).mean(), (s * x.r5[x.date >= SPLIT] > 0).mean()]
    n = int(len(x))
    f = lambda v: None if v is None or not np.isfinite(v) else round(float(v), 4)
    edge = None if win5 is None or not np.isfinite(win5) else win5 - b
    e1, e2 = [None if not np.isfinite(h) else h - b for h in halves]
    stable = edge is not None and e1 is not None and e2 is not None and np.sign(e1) == np.sign(e2) == np.sign(edge) and abs(edge) >= 0.01
    verdict = ("too few" if n < 30 else "edge" if stable and edge > 0 else "against" if stable and edge < 0 else "no tested edge")
    return dict(pattern=pattern, label=PT.LABEL[pattern], direction=direction, bucket=bucket, n=n, win1=f(win1), base1=f(b1), win5=f(win5), base5=f(b),
                edge5=f(edge), avg5_pct=f(avg5), edge_2015_22=f(e1), edge_2023_on=f(e2), n_2023_on=int((x.date >= SPLIT).sum()), verdict=verdict)


def run(log=print, prefer_talib=True):
    from core.data import load_raw
    t0 = time.time()
    px, _, _ = load_raw()
    evs, bases, src = [], [], None
    syms = sorted(px.symbol.unique())
    for k, (sym, g) in enumerate(px.groupby("symbol"), 1):
        if len(g) < 300:
            continue
        ev, base, src = events(g, prefer_talib)
        evs.append(ev)
        bases.append(base)
        if k % 100 == 0:
            log(f"  {k}/{len(syms)} stocks · {time.time() - t0:.0f} s")
    E, B = pd.concat(evs, ignore_index=True), pd.concat(bases, ignore_index=True)
    rows = []
    for (p, d), e in E.groupby(["pattern", "direction"]):
        rows.append(_row(e, B, p, d, "all"))
        if d != "neutral":
            rows.append(_row(e[e.trend == True], B, p, d, "context"))   # noqa: E712
            rows.append(_row(e[e.strength == "strong"], B, p, d, "strong"))
    OUT.mkdir(parents=True, exist_ok=True)
    meta = dict(built=str(pd.Timestamp.now().floor("s")), source=src, stocks=int(len(evs)), events=int(len(E)), first=str(B.date.min().date()), last=str(B.date.max().date()),
                note="Nifty 500 (today's list: some survivorship). Signal at the pattern day's close; strong rows from the confirming close.")
    (OUT / "stats.json").write_text(json.dumps(dict(meta=meta, rows=rows), indent=1))
    good = [r for r in rows if r["verdict"] == "edge"]
    log(f"Pattern test done in {time.time() - t0:.0f} s: {len(E):,} patterns on {len(evs)} stocks ({src}); {len(good)} pattern rows with a stable edge.")
    return rows


if __name__ == "__main__":
    run()
