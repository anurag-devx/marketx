"""API: Experimental → Kronos. Reads only results/kronos/ and the price files; nothing here touches the agents."""
import json
import pandas as pd
from core import config as C, jobs
from web.api import route, cached, _mtime, prices, index, UserError
from web.r_agents import table

K = C.BASE / "kronos_lab"


def _scored():
    from kronos_lab import score as S
    px_path = C.DATA / "prices.parquet"
    def build():
        fc = S.forecasts()
        return S.outcomes(fc, prices(), index()) if len(fc) else fc
    return cached("kronos_scored", (_mtime(S.FILE), _mtime(px_path)), build)


@route("GET", "kronos")
def kronos(p):
    from kronos_lab import score as S
    installed = dict(env=(C.BASE / ".venv-kronos" / "bin" / "python").exists(), code=(K / "vendor" / "Kronos" / "model").exists())
    hist = [x for x in jobs.history(60) if x["job"].startswith("kronos")]
    last = hist[0] if hist else None
    d = _scored()
    out = dict(installed=installed, ready=all(installed.values()), last=last, total=len(d))
    if d.empty:
        return dict(out, sections=[])
    view = p.get("view", "latest")
    if view == "latest":
        day = pd.Timestamp(p["as_of"]) if p.get("as_of") else d.as_of.max()
        x = d[d.as_of == day].sort_values("pred_ret_pct", ascending=False)
        out["days"] = [str(t.date()) for t in sorted(d.as_of.unique(), reverse=True)[:120]]
        out["day"] = str(day.date())
        scored = x.realized_ret_pct.notna().any()
        cols = [["symbol", "Stock", "symonly"], ["close_asof", "Close", "inr"], ["pred_ret_pct", f"Predicted ({int(x.h.iat[0]) if len(x) else 5} sessions)", "spct2"],
                ["p_up", "P(up)", "frac"], ["draw_sd_pct", "Spread of paths", "num2"]]
        if scored:
            cols += [["realized_ret_pct", "Actual", "spct2"], ["excess_pct", "Actual vs Nifty", "spct2"]]
        cols += [["model", "Model"], ["mode", "Mode"]]
        out["sections"] = [dict(kind="table", title=f"Kronos forecasts made at the close of {day:%d %b %Y}", rows=x, columns=cols, csv=f"kronos_{day.date()}",
                                note="Predicted = mean of the sampled paths. P(up) = share of paths ending above the close. Spread = how much the paths disagree. "
                                     "Experimental: not used by the agents, the screener or the Debate Lab.")]
        return out
    if view == "score":
        sm = S.summary(d)
        if sm is None:
            return dict(out, sections=[dict(kind="note", text="Nothing scored yet: a forecast is scored once its horizon has passed. Run a replay to score past days now.")])
        secs = []
        for r in sm.itertuples():
            verdict = ("Ranks stocks better than chance" if pd.notna(r.ic_t) and r.ic_t >= 2 else
                       "Worse than chance at ranking" if pd.notna(r.ic_t) and r.ic_t <= -2 else "No reliable ranking skill yet")
            secs.append(dict(kind="kpis", cols=4, items=[
                dict(label=f"{r.model} · {r.h} sessions", value=f"{r.forecasts:,} scored", delta=f"{r.days} days · {r.stocks} stocks"),
                dict(label="Direction right", value=f"{r.hit_rate:.1f}%", delta=f"always guessing the common side: {r.best_guess:.1f}%"),
                dict(label="P(up) skill vs base rate", value=f"{r.skill:+.3f}", delta=f"Brier {r.brier:.3f} vs {r.brier_base:.3f}"),
                dict(label="Ranking (mean daily IC)", value=f"{r.mean_ic:+.3f}" if pd.notna(r.mean_ic) else "—",
                     delta=f"t {r.ic_t:.1f} on {r.independent_days} independent days" if pd.notna(r.ic_t) else "too few days"),
                dict(label="Top fifth − bottom fifth", value=f"{r.spread_pct:+.2f}%" if pd.notna(r.spread_pct) else "—",
                     delta=f"actual, vs Nifty · t {r.spread_t:.1f}" if pd.notna(r.spread_t) else ""),
                dict(label="Verdict", value=verdict)]))
        secs.append(dict(kind="md", title="How this is scored", text=(S.__doc__ or "").split("\n", 1)[1].strip()))
        bd = S.by_day(d)
        if len(bd):
            bd = bd.sort_values("as_of")
            secs.append(dict(kind="chart", type="bars", title="Daily ranking skill (IC): above 0 = the model's higher-ranked stocks did better", height=220, signed=True, decimals=2,
                             x=bd.as_of.dt.strftime("%Y-%m-%d").tolist(), series=[dict(name="IC", y=bd.ic.round(3).tolist())]))
            secs.append(table("By day", bd.sort_values("as_of", ascending=False),
                              {"as_of": "date", "ic": "snum2", "hit_rate": "num1", "top_minus_bottom": "spct2", "stocks": "int"}, csv="kronos_by_day"))
        cal = S.calibration(d)
        if len(cal):
            secs.append(dict(kind="chart", type="line", title="Calibration: predicted P(up) vs share that went up", x=[f"{v:.2f}" for v in cal.predicted],
                             series=[dict(name="observed", y=cal.observed.round(3).tolist()), dict(name="perfect", y=cal.predicted.round(3).tolist(), dash=True)]))
        secs.append(dict(kind="note", tone="warn", text="Replays score past days at once, but Kronos was trained on market data that may include those days, "
                                                         "so replay results can look better than reality. Live forecasts are the real test."))
        return dict(out, sections=secs)
    raise UserError("unknown view")


@route("GET", "kronos/stock")
def kronos_stock(p):
    d = _scored()
    sym, day = p.get("symbol", ""), p.get("as_of")
    x = d[(d.symbol == sym) & (d.as_of == pd.Timestamp(day))] if len(d) and day else pd.DataFrame()
    if x.empty:
        raise UserError("no forecast for that stock and day")
    r = x.iloc[0]
    px = prices()
    g = px[px.symbol == sym].sort_values("date").reset_index(drop=True)
    i = g.index[g.date == r.as_of]
    if not len(i):
        raise UserError("no price on that day")
    i = int(i[0])
    hist = g.iloc[max(0, i - 60): i + 1]
    after = g.iloc[i + 1: i + 1 + int(r.h)]
    path = json.loads(r.path)
    n_h, n_f = len(hist), len(path)
    xs = hist.date.dt.strftime("%Y-%m-%d").tolist() + [f"+{k}" for k in range(1, n_f + 1)]
    actual = hist.close.round(2).tolist() + [round(float(v), 2) for v in after.close] + [None] * (n_f - len(after))
    pred = [None] * (n_h - 1) + [round(float(r.close_asof), 2)] + path
    return dict(title=f"{sym}: Kronos-{r.model} from {r.as_of:%d %b %Y} · predicted {r.pred_ret_pct:+.2f}% · P(up) {100 * r.p_up:.0f}%"
                      + (f" · actual {r.realized_ret_pct:+.2f}%" if pd.notna(r.realized_ret_pct) else " · outcome not known yet"),
                chart=dict(kind="chart", type="line", height=300, x=xs,
                           series=[dict(name="Actual close", y=actual, width=2), dict(name="Kronos (mean of paths)", y=pred, dash=True, width=2)]))
