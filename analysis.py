"""Strategy diagnostics, run after backtest.py. Explains WHY each strategy does or doesn't have an edge.
    python3 analysis.py
Writes results/analysis/*.csv, read by the dashboard's Strategy analysis page."""
import uuid, warnings
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
from core import config as C
from core.data import load_raw
from core.indicators import build
from core.strategies import STRATEGIES, signals
from core.engine import trade_stats
from core.runlog import agent_run

OUT = C.RESULTS / "analysis"
TYPES = {"S1": "Momentum", "S2": "Momentum", "S3": "Momentum", "S4": "Momentum",
         "S5": "Mean reversion", "S6": "Mean reversion", "S10": "Rotation", "S11": "Mean reversion (falling markets)",
         "S12": "Mean reversion (falling markets)"}
HORIZONS = (1, 3, 5, 10, 20, 60)
EXIT_LABEL = {"stop": "Stop", "target": "Target", "time": "Time limit",
              "revert_sma5": "Rule exit", "trail_sma10": "Rule exit", "flag_off": "Rule exit"}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tr = pd.read_csv(C.RESULTS / "trades.csv", parse_dates=["signal_date", "entry_date", "exit_date"])
    cards = pd.read_csv(C.RESULTS / "strategy_cards.csv")
    eq = pd.read_csv(C.RESULTS / "equity.csv", index_col=0, parse_dates=True)
    ids = [s for s in STRATEGIES if s in set(tr.strategy)]

    # 1. per-strategy summary
    def pstats(s):
        s = s.dropna(); r = s.pct_change().dropna(); yrs = (s.index[-1] - s.index[0]).days / 365.25
        return 100 * ((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1), 100 * (s / s.cummax() - 1).min(), np.sqrt(252) * r.mean() / r.std()
    rows = []
    for sid in ids:
        t = tr[tr.strategy == sid]; st = trade_stats(t)
        c, d, sh = pstats(eq[sid]) if sid in eq else (np.nan,) * 3
        rows.append(dict(strategy=sid, name=STRATEGIES[sid]["name"], type=TYPES.get(sid, ""), trades=int(st["trades"]),
                         win_pct=round(st["win %"], 1), avg_win_pct=round(st["avg win %"], 2), avg_loss_pct=round(st["avg loss %"], 2),
                         gross_pct=round(100 * t.ret.mean() + 100 * C.COST_RT, 3), net_pct=round(st["expectancy %"], 3),
                         profit_factor=round(st["profit factor"], 2), cagr_pct=round(c, 1), max_dd_pct=round(d, 1), sharpe=round(sh, 2),
                         states_with_edge=int(cards[(cards.strategy == sid) & cards.active].shape[0])))
    pd.DataFrame(rows).to_csv(OUT / "strategies.csv", index=False)

    # 2. by state, period, year
    st = tr.groupby(["strategy", "state"]).ret.agg(["mean", "size"]).reset_index().rename(columns={"mean": "net_pct", "size": "trades"})
    st["net_pct"] = (100 * st.net_pct).round(3)
    st.merge(cards[["strategy", "state", "edge_pct", "t_stat", "active"]], on=["strategy", "state"], how="left").to_csv(OUT / "states.csv", index=False)
    for col, name in (("period", "periods"),):
        p = tr.groupby(["strategy", col]).ret.agg(["mean", "size"]).reset_index().rename(columns={"mean": "net_pct", "size": "trades"})
        p["net_pct"] = (100 * p.net_pct).round(3); p.to_csv(OUT / f"{name}.csv", index=False)
    tr["year"] = tr.signal_date.dt.year
    y = tr.groupby(["year", "strategy"]).ret.agg(["mean", "size"]).reset_index().rename(columns={"mean": "net_pct", "size": "trades"})
    y["net_pct"] = (100 * y.net_pct).round(3); y.to_csv(OUT / "years.csv", index=False)

    # 3. exits
    tr["exit"] = tr.reason.map(EXIT_LABEL).fillna(tr.reason)
    e = tr.groupby(["strategy", "exit"]).ret.agg(trades="size", avg="mean").reset_index()
    e["share_pct"] = (100 * e.trades / e.groupby("strategy").trades.transform("sum")).round(1)
    e["avg_net_pct"] = (100 * e.avg).round(2)
    e.drop(columns="avg").to_csv(OUT / "exits.csv", index=False)

    # 4. equity, weekly
    w = eq.resample("W-FRI").last().dropna(how="all")
    w.index.name = "date"
    w.reset_index().melt(id_vars="date", var_name="strategy", value_name="equity").dropna().to_csv(OUT / "equity_weekly.csv", index=False)

    # 5. forward returns after the signal vs any liquid stock (no stops, no costs)
    px, idx, uni = load_raw()
    feat, mkt = build(px, idx, uni)
    feat = signals(feat)
    f = feat.sort_values(["symbol", "date"]).copy()
    g = f.groupby("symbol")
    nxt = g.open.shift(-1)
    for h in HORIZONS:
        f[f"fwd{h}"] = g.close.shift(-h) / nxt - 1
    f = f[(f.date >= pd.Timestamp(C.DESIGN[0])) & (f.turnover_cr >= C.MIN_TURNOVER_CR) & f[f"fwd{max(HORIZONS)}"].notna()]
    ctrl = {h: f[f"fwd{h}"].mean() for h in HORIZONS}
    hz = []
    for sid in ids:
        s = f[f[f"sig_{sid}"]]
        for h in HORIZONS:
            hz.append(dict(strategy=sid, days=h, excess_pct=round(100 * (s[f"fwd{h}"].mean() - ctrl[h]), 3),
                           up_pct=round(100 * (s[f"fwd{h}"] > 0).mean(), 1), control_up_pct=round(100 * (f[f"fwd{h}"] > 0).mean(), 1),
                           group="momentum" if TYPES.get(sid) == "Momentum" and sid != "S3" else "other"))
    pd.DataFrame(hz).to_csv(OUT / "horizon.csv", index=False)

    # 6. market by year
    f["year"] = f.date.dt.year
    cy = (100 * f.groupby("year").fwd5.mean()).round(2)
    n = mkt.set_index("date").nifty.resample("YE").last()
    nr = (100 * n.pct_change()).round(1).dropna()
    m = pd.DataFrame({"year": nr.index.year, "nifty_return_pct": nr.values})
    m["random_stock_5d_gross_pct"] = m.year.map(cy)
    m.to_csv(OUT / "market_years.csv", index=False)

    # 7. stops that would have recovered
    fm = f.set_index(["symbol", "date"])["fwd10"]
    stp = tr[tr.reason == "stop"].join(fm, on=["symbol", "signal_date"])
    pd.DataFrame([dict(stopped_trades=len(stp), recovered_10d_pct=round(100 * (stp.fwd10 > 0).mean(), 1))]).to_csv(OUT / "stops.csv", index=False)
    # 8. what kind of stocks each strategy picks
    feat["atr_pct"] = 100 * feat.atr / feat.close
    attrs = feat.set_index(["symbol", "date"])[["sector", "turnover_cr", "atr_pct", "close", "rs_pct", "ret63"]]
    sw = tr[["strategy", "symbol", "signal_date"]].rename(columns={"signal_date": "date"})
    sw = sw.join(attrs, on=["symbol", "date"], rsuffix="_f")
    sw["family"] = "swing"
    pk_path = C.RESULTS / "strategy" / "momentum_picks.csv"
    frames = [sw]
    if pk_path.exists():
        pk = pd.read_csv(pk_path)
        # attributes at the rebalance: the last trading day of the month before the holding month
        lastday = feat.assign(month=feat.date.dt.to_period("M")).groupby("month").date.max()
        pk["date"] = pk.month.map(lambda m: lastday.get(pd.Period(m) - 1))
        pk = pk.dropna(subset=["date"]).join(attrs, on=["symbol", "date"])
        pk["family"] = "momentum"
        frames.append(pk[["strategy", "symbol", "date", "sector", "turnover_cr", "atr_pct", "close", "rs_pct", "ret63", "family"]])
    allp = pd.concat(frames, ignore_index=True)
    allp["size"] = pd.cut(allp.turnover_cr, [0, 50, 200, np.inf], labels=["Smaller (< ₹50 cr/day)", "Mid (₹50–200 cr/day)", "Large (> ₹200 cr/day)"])
    prof = allp.groupby("strategy").agg(
        family=("family", "first"), picks=("symbol", "size"), distinct_stocks=("symbol", "nunique"),
        median_turnover_cr=("turnover_cr", "median"), median_atr_pct=("atr_pct", "median"), median_price=("close", "median"),
        median_rs_pct=("rs_pct", "median"), median_3m_return_pct=("ret63", lambda x: 100 * x.median())).round(2).reset_index()
    sz = allp.groupby(["strategy", "size"], observed=False).size().unstack(fill_value=0)
    sz = (100 * sz.div(sz.sum(axis=1), axis=0)).round(1)
    prof = prof.merge(sz.reset_index(), on="strategy", how="left")
    allp["sector"] = allp.sector.fillna("Unknown")
    sec = allp.groupby(["strategy", "sector"]).size().rename("picks").reset_index()
    sec["share_pct"] = (100 * sec.picks / sec.groupby("strategy").picks.transform("sum")).round(1)
    # sector tilt vs the liquid universe
    uni_sec = feat[(feat.turnover_cr >= C.MIN_TURNOVER_CR) & (feat.date >= pd.Timestamp(C.DESIGN[0]))].sector.fillna("Unknown").value_counts(normalize=True) * 100
    sec["universe_pct"] = sec.sector.map(uni_sec).round(1)
    sec["tilt_pct"] = (sec.share_pct - sec.universe_pct).round(1)
    top = sec.sort_values("share_pct", ascending=False).groupby("strategy").head(3)
    prof["top_sectors"] = prof.strategy.map(top.groupby("strategy").apply(lambda g: ", ".join(f"{r.sector} {r.share_pct:.0f}%" for r in g.itertuples())))
    tilt = sec[sec.picks >= 20].sort_values("tilt_pct", ascending=False).groupby("strategy").head(3)
    prof["overweight_sectors"] = prof.strategy.map(tilt.groupby("strategy").apply(lambda g: ", ".join(f"{r.sector} +{r.tilt_pct:.0f} pts" for r in g.itertuples() if r.tilt_pct > 0)))
    # baseline: every liquid stock-day, for comparison
    u = feat[(feat.turnover_cr >= C.MIN_TURNOVER_CR) & (feat.date >= pd.Timestamp(C.DESIGN[0]))]
    usz = pd.cut(u.turnover_cr, [0, 50, 200, np.inf], labels=list(sz.columns)).value_counts(normalize=True) * 100
    base = dict(strategy="ALL", family="market baseline", picks=len(u), distinct_stocks=u.symbol.nunique(),
                median_turnover_cr=round(u.turnover_cr.median(), 2), median_atr_pct=round(u.atr_pct.median(), 2),
                median_price=round(u.close.median(), 2), median_rs_pct=round(u.rs_pct.median(), 2),
                median_3m_return_pct=round(100 * u.ret63.median(), 2), top_sectors="", overweight_sectors="")
    base.update({c: round(usz.get(c, 0), 1) for c in sz.columns})
    prof = pd.concat([prof, pd.DataFrame([base])], ignore_index=True)
    prof.to_csv(OUT / "profiles.csv", index=False)
    sec.sort_values(["strategy", "share_pct"], ascending=[True, False]).to_csv(OUT / "sector_mix.csv", index=False)
    print(f"Analysis written to {OUT}")


if __name__ == "__main__":
    with agent_run(uuid.uuid4().hex[:8], "analysis") as rec:
        main()
        rec["message"] = "results/analysis/*.csv"
