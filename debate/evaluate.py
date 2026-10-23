"""Scores saved forecasts once their horizon has passed, and summarises how good the lab's forecasts have been.

For each forecast whose horizon has ended (h sessions after the analysis day exist in the price file):
  realised R = adjusted close h sessions later / adjusted close on the analysis day - 1 (benchmark-relative for 'rel')
  outcome    = the pre-defined target (R > 0; or up / neutral / down for three outcomes)
  Brier      = (p - outcome)^2 (binary) or the sum over the three classes; log loss likewise
  the reference close used now is compared with the one stored in the forecast: a difference means a corporate
  action was applied later, and is reported (the forecast itself is never changed).
Forecasts are never rewritten; outcomes are stored separately (data/debate/outcomes.parquet).
Summary metrics need many forecasts: with fewer than 30 scored forecasts the page says so instead of drawing
conclusions. Abstentions are scored too (their probability is still recorded), so skipping hard cases cannot flatter
the numbers.

  python3 -m debate.evaluate"""
import numpy as np, pandas as pd
from core import atomic
from core.filelock import locked
from . import store

MIN_FOR_SUMMARY = 30


def score(rows, prices, nifty):
    """rows: forecast index rows. prices: date, symbol, close. nifty: Series by date. Returns outcome rows."""
    out = []
    by = {s: g.set_index("date").close.sort_index() for s, g in prices[prices.symbol.isin(set(rows.symbol))].groupby("symbol")}
    for r in rows.itertuples():
        s = by.get(r.symbol)
        if s is None:
            continue
        d0 = pd.Timestamp(r.as_of)
        if d0 not in s.index:
            continue
        i = s.index.get_loc(d0)
        if i + r.horizon >= len(s):
            continue
        d1 = s.index[i + r.horizon]
        R = s.iat[i + r.horizon] / s.iat[i] - 1
        if r.target == "rel":
            if d0 not in nifty.index or d1 not in nifty.index:
                continue
            R -= nifty[d1] / nifty[d0] - 1
        ref_now = float(s.iat[i])
        adj = r.reference_price is not None and np.isfinite(r.reference_price) and abs(ref_now / r.reference_price - 1) > 0.005
        if r.target == "three":
            b = (r.band or 0.5) / 100
            cls = 2 if R > b else 0 if R < -b else 1
            P = np.array([r.p_down or 0, r.p_neutral or 0, r.p_up or 0], float)
            y = np.eye(3)[cls]
            br = float(((P - y) ** 2).sum()) if P.sum() > 0 else np.nan
            ll = float(-np.log(max(P[cls], 1e-6))) if P.sum() > 0 else np.nan
            outcome, hit_p = ["down", "neutral", "up"][cls], r.p_up
            predicted = ["down", "neutral", "up"][int(np.argmax(P))] if P.sum() > 0 else None
        else:
            y = float(R > 0)
            p = r.p_up
            br = float((p - y) ** 2) if p is not None and np.isfinite(p) else np.nan
            ll = float(-(y * np.log(max(p, 1e-6)) + (1 - y) * np.log(max(1 - p, 1e-6)))) if p is not None and np.isfinite(p) else np.nan
            outcome, hit_p = ("positive" if y else "not positive"), p
            predicted = None if p is None or not np.isfinite(p) else ("positive" if p > 0.5 else "not positive")
        base = r.base_up
        bb = float((base - float(R > 0)) ** 2) if r.target != "three" and base is not None and np.isfinite(base) else np.nan
        extra = {k: getattr(r, k, None) for k in ("sector", "regime", "list_name", "role", "edge")}
        out.append(dict(analysis_id=r.analysis_id, version=r.version, source=getattr(r, "source", None) or "debate",
                        mode=getattr(r, "mode", None) or "live", **extra, symbol=r.symbol, horizon=r.horizon, target=r.target, as_of=r.as_of,
                        end_date=str(d1.date()), realized_return_pct=float(100 * R), outcome=outcome, predicted=predicted, p_up=hit_p, base_up=base, brier=br,
                        brier_base=bb, logloss=ll, verdict=r.verdict, calibration=r.calibration, reference_now=ref_now,
                        adjusted_since=bool(adj), evaluated_at=str(pd.Timestamp.now().floor("s"))))
    return pd.DataFrame(out)


def all_forecasts():
    """Debate forecasts + nightly batch forecasts in one frame (source column tells them apart)."""
    from . import batch
    a = store.index()
    if len(a):
        a = a.assign(source="debate")
    b = batch.load()
    parts = [x for x in (a, b) if len(x)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def run(log=print):
    from core.data import load_raw
    idx = all_forecasts()
    if idx.empty:
        log("No forecasts yet.")
        return pd.DataFrame()
    done = store.outcomes()
    have = set(zip(done.analysis_id, done.version)) if len(done) else set()
    todo = idx[[(a, v) not in have for a, v in zip(idx.analysis_id, idx.version)]]
    if todo.empty:
        log("Nothing new to score.")
        return done
    px, ix, _ = load_raw()
    nifty = ix.dropna(subset=["nifty"]).set_index("date").nifty
    new = score(todo, px, nifty)
    log(f"Scored {len(new)} forecast(s); {len(todo) - len(new)} still inside their horizon.")
    if len(new):
        with locked("debate"):
            all_ = pd.concat([store.outcomes(), new], ignore_index=True) if len(store.outcomes()) else new
            atomic.to_parquet(all_, store.OUTCOMES)
        return all_
    return done


def summary(out=None):
    """Aggregate quality of scored forecasts by horizon and target (binary targets)."""
    out = store.outcomes() if out is None else out
    if out is None or out.empty:
        return pd.DataFrame(), "No forecast has reached the end of its horizon yet."
    rows = []
    if "mode" not in out:
        out = out.assign(mode="live")
    out = out.assign(mode=out["mode"].fillna("live"))
    for (mode, h, t), g in out.groupby(["mode", "horizon", "target"]):
        b = g.dropna(subset=["brier"])
        hit = (g.outcome.isin(["positive", "up"])).mean()
        rows.append(dict(mode=mode, horizon=h, target=t, forecasts=len(g), brier=b.brier.mean() if len(b) else np.nan,
                         brier_base=b.brier_base.mean() if b.brier_base.notna().any() else np.nan, logloss=b.logloss.mean() if len(b) else np.nan,
                         share_positive=hit, mean_p_up=g.p_up.mean(),
                         accuracy=float((g.predicted == g.outcome).mean()) if "predicted" in g and g.predicted.notna().any() else np.nan))
    s = pd.DataFrame(rows)
    n = int((out["mode"] == "live").sum())
    msg = (f"{n} live forecast(s) scored ({len(out) - n} retrospective replays shown separately)." + ("" if n >= MIN_FOR_SUMMARY else f" Fewer than {MIN_FOR_SUMMARY}: too few to judge the lab's skill; "
                                         "treat these numbers as anecdotes."))
    return s, msg


def reliability(out=None, bins=5):
    out = store.outcomes() if out is None else out
    if out is None or len(out) < 10:
        return pd.DataFrame()
    g = out[out.target != "three"].dropna(subset=["p_up"])
    if len(g) < 10:
        return pd.DataFrame()
    g = g.assign(bin=pd.cut(g.p_up, np.linspace(0, 1, bins + 1), include_lowest=True))
    return g.groupby("bin", observed=True).agg(predicted=("p_up", "mean"), observed=("outcome", lambda x: (x == "positive").mean()),
                                               n=("p_up", "size")).reset_index().assign(bin=lambda d: d.bin.astype(str))


if __name__ == "__main__":
    run()
    s, msg = summary()
    print(msg)
    if len(s):
        print(s.to_string(index=False))
