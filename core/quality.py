"""Data quality checks on the price files. Runs after every fetch and before the agents.

Checks per stock:
  split_gap     overnight gap matching a split/bonus ratio (1:1 bonus, 2:1 split, ...) that was not
                adjusted. Error: every indicator across that date is wrong -> stock quarantined.
  bad_tick      a 20%+ move that fully reverses the next day (back within 5%), on a day the Nifty moved
                less than 5% (one wrong bar). Error -> quarantined.
  ohlc          high below open/close by 2%+, low above them by 2%+, or a price <= 0. Error -> quarantined.
  ohlc_minor    the same by less than 2% (common in Angel bars, e.g. 2023-03-03). Warning.
  big_move      a 25%+ one-day move that does not reverse. Warning: usually real news, worth a look.
  missing_days  more than 2% of trading days missing since the stock's first bar. Warning.
  long_gap      5+ trading days missing in a row (suspension?). Warning.
  stale         no bar on the latest trading day. Warning: suspended, delisted or a failed download.
  duplicates    the same date twice. Error -> quarantined.
Index: missing Nifty or VIX values.

Quarantined (an unreviewed error in the last 400 days) stocks get no new signals from the agents (positions in them are still checked). If you
look at an issue and the data is right (e.g. a real 30% crash), mark it reviewed in the dashboard:
reviewed issues stop quarantining. If a split was missed, add it to data/corporate_actions.csv and
the loader adjusts history (and skips the fix automatically if Angel already adjusted it)."""
import json
import numpy as np, pandas as pd
from .config import DATA

SPLIT_FACTORS = [1.5, 2, 2.5, 3, 4, 5, 10, 20]
ERRORS = {"split_gap", "bad_tick", "ohlc", "duplicates"}
REVIEWED = DATA / "quality_reviewed.csv"
ISSUES = DATA / "quality_issues.csv"
SUMMARY = DATA / "quality.json"
ACTIONS = DATA / "corporate_actions.csv"


# ---------- corporate actions you add by hand ----------
def apply_corporate_actions(px):
    """data/corporate_actions.csv: symbol, ex_date, factor, note. factor 2 = 1:1 bonus or 2:1 split.
    Divides prices before ex_date by factor (volume x factor) only if the raw gap is still there."""
    if not ACTIONS.exists():
        return px, []
    a = pd.read_csv(ACTIONS)
    if a.empty:
        return px, []
    px = px.sort_values(["symbol", "date"]).reset_index(drop=True)
    done = []
    for r in a.itertuples():
        m = px.symbol == r.symbol
        g = px[m]
        ex = pd.Timestamp(r.ex_date)
        after = g[g.date >= ex].head(1)
        before = g[g.date < ex].tail(1)
        if after.empty or before.empty:
            done.append(dict(symbol=r.symbol, ex_date=str(ex.date()), factor=r.factor, applied=False, why="dates not in data"))
            continue
        ratio = after.open.iat[0] / before.close.iat[0]
        if abs(ratio * float(r.factor) - 1) > 0.12:
            done.append(dict(symbol=r.symbol, ex_date=str(ex.date()), factor=r.factor, applied=False,
                             why=f"no {r.factor}x gap in the data (ratio {ratio:.2f}): already adjusted"))
            continue
        sel = m & (px.date < ex)
        px.loc[sel, ["open", "high", "low", "close"]] /= float(r.factor)
        px.loc[sel, "volume"] *= float(r.factor)
        done.append(dict(symbol=r.symbol, ex_date=str(ex.date()), factor=r.factor, applied=True, why=""))
    return px, done


# ---------- checks ----------
def check(px, idx):
    px = px.sort_values(["symbol", "date"]).reset_index(drop=True)
    issues = []

    def add(sym, date, chk, detail, value=np.nan):
        issues.append(dict(symbol=sym, date=None if date is None else str(pd.Timestamp(date).date()), check=chk,
                           severity="error" if chk in ERRORS else "warning", detail=detail, value=value))

    dup = px[px.duplicated(["symbol", "date"], keep=False)]
    for (s, d), _ in dup.groupby(["symbol", "date"]):
        add(s, d, "duplicates", "same date appears more than once")
    px = px.drop_duplicates(["symbol", "date"], keep="last")

    g = px.groupby("symbol", sort=False)
    pc = g.close.shift()
    nxt = g.close.shift(-1)
    gap = px.open / pc
    move = px.close / pc - 1
    back = nxt / px.close - 1

    hi_gap = px[["open", "close"]].max(axis=1) / px.high - 1
    lo_gap = 1 - px[["open", "close"]].min(axis=1) / px.low
    worst = np.maximum(hi_gap, lo_gap)
    nonpos = (px[["open", "high", "low", "close"]] <= 0).any(axis=1)
    for r in px.assign(w=worst)[nonpos | (worst > 0.001)].itertuples():
        chk = "ohlc" if (r.w > 0.02 or min(r.open, r.high, r.low, r.close) <= 0) else "ohlc_minor"
        add(r.symbol, r.date, chk, f"O {r.open} H {r.high} L {r.low} C {r.close}", r.w)

    # split-like gap: the open sits at prev close / factor (or x factor for a reverse split / consolidation)
    for r in px.assign(gap=gap)[(gap < 0.72) | (gap > 1.45)].itertuples():
        f = min(SPLIT_FACTORS, key=lambda x: min(abs(x * r.gap - 1), abs(r.gap / x - 1)))
        if min(abs(f * r.gap - 1), abs(r.gap / f - 1)) < 0.06:
            add(r.symbol, r.date, "split_gap", f"opened at {r.gap:.3f}x the previous close: looks like a {f}x split/bonus not adjusted", r.gap)

    nmove = px.date.map(idx.set_index("date").nifty.pct_change()).abs().fillna(0)
    level = (1 + move) * (1 + back)              # close two days later vs the close before the move
    tick = (move.abs() >= 0.20) & ((level - 1).abs() <= 0.05) & (nmove < 0.05)
    for r in px.assign(move=move, back=back)[tick].itertuples():
        add(r.symbol, r.date, "bad_tick", f"{100 * r.move:+.0f}% then {100 * r.back:+.0f}% the next day", r.move)
    big = (move.abs() >= 0.25) & ~tick
    for r in px.assign(move=move)[big].itertuples():
        add(r.symbol, r.date, "big_move", f"{100 * r.move:+.0f}% in one day", r.move)

    # calendar: trading days = days the Nifty has a value
    cal = pd.DatetimeIndex(sorted(idx.dropna(subset=["nifty"]).date.unique()))
    last_day = cal.max() if len(cal) else px.date.max()
    pos = pd.Series(np.arange(len(cal)), index=cal)
    for sym, d in px.groupby("symbol", sort=False).date:
        d = pd.DatetimeIndex(d)
        span = cal[(cal >= d.min()) & (cal <= d.max())]
        miss = len(span.difference(d))
        if len(span) > 50 and miss / len(span) > 0.02:
            add(sym, None, "missing_days", f"{miss} of {len(span)} trading days missing", miss)
        p = pos.reindex(d).dropna().to_numpy()
        if len(p) > 1:
            jumps = np.diff(p)
            for i in np.flatnonzero(jumps > 5):
                add(sym, d[i + 1], "long_gap", f"{int(jumps[i]) - 1} trading days missing before this date", jumps[i] - 1)
        if d.max() < last_day:
            add(sym, d.max(), "stale", f"last bar {d.max().date()}, market has {pd.Timestamp(last_day).date()}")

    for col in ("nifty", "vix"):
        if col in idx:
            n = int(idx[col].isna().sum())
            if n:
                add(f"INDEX:{col}", None, "missing_days", f"{n} days without a {col} value", n)

    out = pd.DataFrame(issues, columns=["symbol", "date", "check", "severity", "detail", "value"])
    return _mark_reviewed(out)


def _mark_reviewed(out):
    out["reviewed"] = False
    if REVIEWED.exists() and len(out):
        rv = pd.read_csv(REVIEWED, dtype=str).fillna("")
        key = set(zip(rv.symbol, rv.date, rv.check))
        out["reviewed"] = [(s, d or "", c) in key for s, d, c in zip(out.symbol, out.date, out.check)]
    return out


def mark_reviewed(rows, note=""):
    """rows: iterable of dicts with symbol, date, check."""
    old = pd.read_csv(REVIEWED, dtype=str) if REVIEWED.exists() else pd.DataFrame(columns=["symbol", "date", "check", "note", "when"])
    new = pd.DataFrame([dict(symbol=r["symbol"], date=r.get("date") or "", check=r["check"], note=note,
                             when=str(pd.Timestamp.now().floor("s"))) for r in rows])
    pd.concat([old, new]).drop_duplicates(["symbol", "date", "check"], keep="last").to_csv(REVIEWED, index=False)


def run(px, idx, save=True):
    """Runs all checks, saves data/quality_issues.csv and data/quality.json. Returns (issues, summary)."""
    iss = check(px, idx)
    open_err = iss[(iss.severity == "error") & ~iss.reviewed]
    recent = pd.to_datetime(open_err.date) >= px.date.max() - pd.Timedelta(days=400)
    q = sorted(s for s in open_err[recent | open_err.date.isna()].symbol.unique() if not s.startswith("INDEX:"))
    summ = dict(checked_at=str(pd.Timestamp.now().floor("s")), stocks=int(px.symbol.nunique()), last_bar=str(px.date.max().date()),
                errors=int(len(open_err)), warnings=int(((iss.severity == "warning") & ~iss.reviewed).sum()),
                reviewed=int(iss.reviewed.sum()), quarantined=q,
                status="errors" if len(open_err) else ("warnings" if ((iss.severity == "warning") & ~iss.reviewed).any() else "ok"),
                by_check=iss[~iss.reviewed].check.value_counts().to_dict())
    if save:
        iss.to_csv(ISSUES, index=False)
        SUMMARY.write_text(json.dumps(summ, indent=2))
    return iss, summ


def quarantined():
    if not SUMMARY.exists():
        return set()
    try:
        return set(json.loads(SUMMARY.read_text()).get("quarantined", []))
    except Exception:
        return set()


def summary():
    try:
        return json.loads(SUMMARY.read_text()) if SUMMARY.exists() else None
    except Exception:
        return None


if __name__ == "__main__":
    from .data import load_raw
    px, idx, _ = load_raw()
    iss, s = run(px, idx)
    print(json.dumps({k: v for k, v in s.items() if k != "quarantined"}, indent=2))
    print("quarantined:", ", ".join(s["quarantined"]) or "none")
