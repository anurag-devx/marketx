"""Walk-forward test of the analog engine (the Scorecard), and each node's group test.

For each test year Y (2019 onwards): the library is every stock-day whose next session ended before 1 Jan Y; queries are
every 5th trading day of Y (all stocks). The library is a random 400,000 of those stock-days (to keep the test to ~15 min). No information from Y or later is used to score Y.
Per combination (move x size):
  top20_hit     share of each day's 20 highest-chance stocks that moved at least that much
  top20_usual   the same stocks' usual chance (what you'd expect without the model)
  lift          top20_hit - top20_usual  (percentage points; the edge)
  auc           does a higher chance mean a big move more often (0.5 = no)
  brier_skill   1 - Brier(chance) / Brier(usual chance); above 0 = better than the usual rate
  dir_hit       where a lean was given: share of next sessions that moved that way; dir_cov = share of rows with a lean
Group test (every 2nd test year, every 20th day, close-to-close at 1%): drop one node's features, re-score. A node is 'tested' when dropping
it lowers the brier skill or the direction hit rate in most years (and on average)."""
import json, time
import numpy as np, pandas as pd
from core import config as C, atomic
from .features import GROUPS, MOVES, SIZES
from . import engine as E

OUT = C.RESULTS / "marketlab" / "score.json"
FIRST_YEAR = 2019


def _auc(p, y):
    y = np.asarray(y, bool)
    if y.all() or (~y).all():
        return np.nan
    r = pd.Series(p).rank().to_numpy()
    n1 = y.sum()
    return (r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * (len(y) - n1))


def metrics(q, est, m, s):
    y = q[m].to_numpy(float)
    ok = np.isfinite(y)
    big = np.abs(y) >= s / 100
    ch, us, up, nb = est[f"ch_{m}_{s}"].to_numpy(), est[f"us_{m}_{s}"].to_numpy(), est[f"up_{m}_{s}"].to_numpy(), est[f"nb_{m}_{s}"].to_numpy()
    ok &= np.isfinite(us)
    y, big, ch, us, up, nb, dates = y[ok], big[ok], ch[ok], us[ok], up[ok], nb[ok], q.date.to_numpy()[ok]
    bs, bu = np.mean((ch - big) ** 2), np.mean((us - big) ** 2)
    t = pd.DataFrame({"d": dates, "ch": ch, "us": us, "big": big}).sort_values(["d", "ch"], ascending=[True, False]).groupby("d").head(20)
    lean = E.lean_of(up, nb)
    has = lean != "unclear"
    hit = np.where(lean == "up", y > 0, y < 0)
    return dict(n=int(ok.sum()), top20_hit=float(t.big.mean()), top20_usual=float(t.us.mean()), lift=float(t.big.mean() - t.us.mean()),
                auc=float(_auc(ch, big)), brier_skill=float(1 - bs / bu) if bu > 0 else np.nan,
                dir_hit=float(hit[has].mean()) if has.any() else np.nan, dir_cov=float(has.mean()), dir_n=int(has.sum()), base_rate=float(big.mean()))


LIB_ROWS = 400_000      # past stock-days sampled per test year (the nightly radar uses all of them)
ABLATION_YEARS = 2      # node test on every 2nd year, every 20th day


def run(panel=None, log=print, step=5, ablation_step=20, groups=GROUPS):
    from . import radar
    d = panel if panel is not None else radar.panel(log)
    d = d.dropna(subset=["cc"])
    years = sorted(y for y in d.date.dt.year.unique() if y >= FIRST_YEAR)
    res = {"built": str(pd.Timestamp.now().floor("s")), "years": [int(y) for y in years], "by_year": [], "groups": [], "step": step}
    t0 = time.time()
    for y in years:
        start = pd.Timestamp(f"{y}-01-01")
        lib = d[d.next_date < start]
        days = np.sort(d[d.date.dt.year == y].date.unique())
        if len(lib) < 50000 or not len(days):
            continue
        q = d[d.date.isin(days[::step])]
        L = E.Library(lib, groups, max_rows=LIB_ROWS, seed=int(y))
        est, _, _ = L.estimate(q)
        for m in MOVES:
            for s in SIZES:
                res["by_year"].append(dict(year=int(y), move=m, size=s, **metrics(q, est, m, s)))
        if (y - years[0]) % ABLATION_YEARS:
            r = next(x for x in res["by_year"][::-1] if x["year"] == y and x["move"] == "cc" and x["size"] == 1.0)
            log(f"  {y}: {len(q):,} test rows · ≥1% close-to-close: top-20 hit {r['top20_hit']:.1%} vs usual {r['top20_usual']:.1%} · {int(time.time() - t0)} s")
            continue
        qa = d[d.date.isin(days[::ablation_step])]
        base = metrics(qa, L.estimate(qa)[0], "cc", 1.0)
        for g in groups:
            sub = {k: v for k, v in groups.items() if k != g}
            Lg = E.Library(lib, sub, max_rows=LIB_ROWS, seed=int(y))
            mg = metrics(qa, Lg.estimate(qa)[0], "cc", 1.0)
            res["groups"].append(dict(year=int(y), node=g, d_brier=base["brier_skill"] - mg["brier_skill"], d_dir=(base["dir_hit"] or 0) - (mg["dir_hit"] or 0)))
        r = next(x for x in res["by_year"][::-1] if x["year"] == y and x["move"] == "cc" and x["size"] == 1.0)
        log(f"  {y}: {len(q):,} test rows · ≥1% close-to-close: top-20 hit {r['top20_hit']:.1%} vs usual {r['top20_usual']:.1%} · {int(time.time() - t0)} s")
    by = pd.DataFrame(res["by_year"])
    if len(by):
        agg = by.groupby(["move", "size"]).apply(lambda g: pd.Series({
            "n": int(g.n.sum()), "top20_hit": np.average(g.top20_hit, weights=g.n), "top20_usual": np.average(g.top20_usual, weights=g.n),
            "lift": np.average(g.lift, weights=g.n), "lift_years_pos": int((g.lift > 0).sum()), "years": int(len(g)),
            "auc": np.nanmean(g.auc), "brier_skill": np.average(g.brier_skill, weights=g.n), "skill_years_pos": int((g.brier_skill > 0).sum()),
            "dir_hit": np.nanmean(g.dir_hit), "dir_cov": np.nanmean(g.dir_cov), "base_rate": np.average(g.base_rate, weights=g.n)}), include_groups=False).reset_index()
        agg["ready"] = True
        agg["skill"] = (agg.brier_skill > 0) & (agg.skill_years_pos >= np.ceil(0.6 * agg.years)) & (agg.lift > 0)
        res["summary"] = agg.to_dict("records")
    gr = pd.DataFrame(res["groups"])
    if len(gr):
        ga = gr.groupby("node").agg(d_brier=("d_brier", "mean"), d_dir=("d_dir", "mean"), years=("year", "count"),
                                    brier_pos=("d_brier", lambda x: int((x > 0).sum())), dir_pos=("d_dir", lambda x: int((x > 0).sum()))).reset_index()
        ga["tested"] = ((ga.d_brier > 0) & (ga.brier_pos >= np.ceil(0.6 * ga.years))) | ((ga.d_dir > 0) & (ga.dir_pos >= np.ceil(0.6 * ga.years)))
        res["group_summary"] = ga.to_dict("records")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(OUT, json.dumps(res, default=lambda o: None if isinstance(o, float) and not np.isfinite(o) else (o.item() if hasattr(o, "item") else str(o)), indent=1))
    log(f"Market Lab test done in {int(time.time() - t0)} s.")
    return res


OUT_C = C.RESULTS / "marketlab" / "candidates.json"
MIN_CAND_YEARS = 3      # a candidate must help in most of at least 3 test years before it may enter the forecasts


def run_candidates(panel=None, log=print, step=10):
    """Candidate groups (features.CANDIDATES): for each test year, score the engine without and with the group (every 10th
    day, all moves at 1%) on the same walk-forward split. A candidate 'helps' when adding it raises the brier skill or the
    direction hit rate in most years and on average. Rows with no data for a candidate count as average (zero after
    scaling), so only the years where its data exists can show a difference."""
    from . import radar
    from .features import BASE_GROUPS, CANDIDATES
    d = panel if panel is not None else radar.panel(log)
    d = d.dropna(subset=["cc"])
    base_g = dict(BASE_GROUPS)
    years = sorted(y for y in d.date.dt.year.unique() if y >= FIRST_YEAR)
    rows, t0 = [], time.time()
    for y in years:
        start = pd.Timestamp(f"{y}-01-01")
        lib = d[d.next_date < start]
        days = np.sort(d[d.date.dt.year == y].date.unique())
        if len(lib) < 50000 or not len(days):
            continue
        q = d[d.date.isin(days[::step])]
        L0 = E.Library(lib, base_g, max_rows=LIB_ROWS, seed=int(y))
        e0 = L0.estimate(q)[0]
        for g, fs in CANDIDATES.items():
            cover = float(q[fs].notna().any(axis=1).mean()) if all(f in q for f in fs) else 0.0
            if cover < 0.05:
                rows.append(dict(year=int(y), node=g, coverage=cover, skipped=True))
                continue
            L1 = E.Library(lib, dict(base_g, **{g: fs}), max_rows=LIB_ROWS, seed=int(y))
            e1 = L1.estimate(q)[0]
            for m in MOVES:
                a, b = metrics(q, e0, m, 1.0), metrics(q, e1, m, 1.0)
                rows.append(dict(year=int(y), node=g, move=m, coverage=cover, d_brier=b["brier_skill"] - a["brier_skill"],
                                 d_dir=(b["dir_hit"] or 0) - (a["dir_hit"] or 0), d_lift=b["lift"] - a["lift"]))
        log(f"  {y}: candidates scored · {int(time.time() - t0)} s")
    r = pd.DataFrame(rows)
    summary = []
    if len(r) and "d_brier" in r:
        ok = r.dropna(subset=["d_brier"])
        for (g, m), x in ok.groupby(["node", "move"]):
            yrs = len(x)
            helps = bool(yrs >= MIN_CAND_YEARS and (((x.d_brier.mean() > 0) and (x.d_brier > 0).sum() >= np.ceil(0.6 * yrs)) or
                                                    ((x.d_dir.mean() > 0) and (x.d_dir > 0).sum() >= np.ceil(0.6 * yrs))))
            summary.append(dict(node=g, move=m, years=yrs, d_brier=float(x.d_brier.mean()), d_dir=float(x.d_dir.mean()), d_lift=float(x.d_lift.mean()),
                                brier_pos=int((x.d_brier > 0).sum()), dir_pos=int((x.d_dir > 0).sum()), coverage=float(x.coverage.mean()), helps=helps))
    res = dict(built=str(pd.Timestamp.now().floor("s")), step=step, rows=rows, summary=summary,
               skipped=sorted({x["node"] for x in rows if x.get("skipped")} - {x["node"] for x in summary}))
    OUT_C.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(OUT_C, json.dumps(res, default=lambda o: None if isinstance(o, float) and not np.isfinite(o) else (o.item() if hasattr(o, "item") else str(o)), indent=1))
    log(f"Candidate test done in {int(time.time() - t0)} s: " + ("; ".join(f"{x['node']} {x['move']}: {'helps' if x['helps'] else 'no help'}" for x in summary) or "no candidate had enough data"))
    return res


def load_candidates():
    try:
        return json.loads(OUT_C.read_text())
    except (OSError, ValueError):
        return None


def load():
    try:
        return json.loads(OUT.read_text())
    except (OSError, ValueError):
        return None


def skill_map():
    r = load() or {}
    return {(x["move"], float(x["size"])): x for x in r.get("summary", [])}, {x["node"]: x for x in r.get("group_summary", [])}
