"""Tonight's Move Radar: match every stock's scenario against history and save the chances, leans, node drivers and
closest past cases for the web page. Run: python -m marketlab  (build)  ·  python -m marketlab test  (Scorecard)."""
import numpy as np, pandas as pd
from core import config as C, atomic
from .features import PANEL, MOVES, SIZES, FEATURES, build, load_inputs
from . import engine as E

DIR = C.DATA / "marketlab"
N_CASES = 15


def panel(log=print, save=True):
    log("Building the scenario panel ...")
    d = build(*load_inputs(), log=log)
    if save:
        PANEL.parent.mkdir(parents=True, exist_ok=True)
        atomic.to_parquet(d, PANEL)
    return d


def load_panel():
    return pd.read_parquet(PANEL) if PANEL.exists() else None


def match_day(d, day, log=print, drivers=True, cases=True):
    """Tonight's numbers for one day, using only stock-days whose next session was known by then.
    Returns {"radar", "drivers", "cases", "spread"} frames (drivers / cases empty when switched off).
    drivers="top": only for the stocks in any combination's top 20 by edge (the replay: about 10x faster)."""
    day = pd.Timestamp(day)
    q = d[d.date == day].reset_index(drop=True)
    lib = d[(d.next_date <= day) & d.cc.notna()]
    log(f"Matching {len(q)} stocks on {day:%d %b %Y} against {len(lib):,} past stock-days ...")
    L = E.Library(lib)
    est, idx, dist = L.estimate(q)
    out = pd.concat([q[["symbol", "date", "sector", "close"] + [c for c in q if c.startswith("raw_")] + FEATURES], est], axis=1)
    out["built"] = str(pd.Timestamp.now().floor("s"))
    drv = pd.DataFrame(columns=["symbol", "node", "move", "size", "d_chance", "d_up"])
    if drivers == "top":
        top = set()
        for m in MOVES:
            for s in SIZES:
                lift = est[f"ch_{m}_{s}"] - est[f"us_{m}_{s}"]
                top |= set(q.symbol[lift.rank(ascending=False, method="first") <= 20])
        k = q.symbol.isin(top).to_numpy()
        if k.any():
            drv = L.drivers(q[k].reset_index(drop=True), est[k].reset_index(drop=True))
    elif drivers:
        log("Working out each node's push (influence map) ...")
        drv = L.drivers(q, est)
    rows = []
    if cases:
        for i, sym in enumerate(q.symbol):
            for r in range(min(N_CASES, idx.shape[1])):
                j = idx[i, r]
                rows.append((sym, r + 1, L.meta.symbol.iat[j], L.meta.date.iat[j], float(dist[i, r]),
                             float(L.out["cc"][j]), float(L.out["oc"][j]), float(L.out["gp"][j])))
    cs = pd.DataFrame(rows, columns=["symbol", "rank", "case_symbol", "case_date", "distance", "cc", "oc", "gp"])
    pct = []                                          # outcome spread of all K cases (percentiles per move)
    for m in MOVES:
        y = L.outcomes(q, idx, m) * 100
        for i, sym in enumerate(q.symbol):
            p = np.percentile(y[i], [10, 25, 50, 75, 90])
            pct.append((sym, m, *[float(x) for x in p], float((y[i] > 0).mean())))
    ps = pd.DataFrame(pct, columns=["symbol", "move", "p10", "p25", "p50", "p75", "p90", "up_share"])
    return {"radar": out, "drivers": drv, "cases": cs, "spread": ps}


def run(log=print, day=None):
    d = panel(log)
    day = pd.Timestamp(day) if day else d.date.max()
    frames = match_day(d, day, log)
    tag = f"{day:%Y-%m-%d}"
    for name, frame in frames.items():
        (DIR / name).mkdir(parents=True, exist_ok=True)
        atomic.to_parquet(frame, DIR / name / f"{tag}.parquet")
    log(f"Market Lab: radar for {len(frames['radar'])} stocks saved for {day:%d %b %Y}.")
    try:                                              # secondary layer: trade plans for the same stocks (tradeplan/)
        from tradeplan import plan as TP
        TP.run(tag, log)
    except Exception as e:
        log(f"Trade plan skipped: {type(e).__name__}: {str(e)[:160]}")
    try:                                              # Review: keep tonight's calls, grade the earlier ones
        from . import review as RV
        RV.save_live(tag, log)
        RV.grade("live", d=d, log=log)
    except Exception as e:
        log(f"Review skipped: {type(e).__name__}: {str(e)[:160]}")
    return frames["radar"]


def latest_day():
    p = sorted((DIR / "radar").glob("*.parquet")) if (DIR / "radar").exists() else []
    return p[-1].stem if p else None


def read(name, day=None):
    day = day or latest_day()
    p = DIR / name / f"{day}.parquet"
    return pd.read_parquet(p) if day and p.exists() else pd.DataFrame()
