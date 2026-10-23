"""How Indian stocks and sectors respond to macro and geopolitical moves.

Timing: US and global series (FRED, GPR, GDELT) are dated by their own day, which ends after Indian markets close,
and FII/DII figures come out after the close too. So a factor change dated d is matched to the NEXT Indian trading
day (the first session that could react). No look-ahead.

Factors (change per period): Brent crude %, USD/INR %, US 10-year yield (points), US dollar index %, US VIX %,
GPR (geopolitical risk) log change, FII net buying (₹ crore).

1. Sensitivity (weekly, robust to holiday mismatches): for each sector (equal-weight of its stocks) and each stock,
   weekly return = a + b_mkt * Nifty + b * factor (one factor at a time). Reported: the extra % move for a typical
   (1 standard deviation) weekly factor move, with its t-stat. Window: last 3 years and the full history.
2. Shocks: days when a factor's change is beyond 2 standard deviations of its past year (up or down). For each,
   the Nifty move on the reaction day and over the next 5 / 20 days, and each sector vs the Nifty.
   t-stats across shock days. Past behaviour, not a forecast."""
import json
import numpy as np, pandas as pd

FACTORS = {"DCOILBRENTEU": ("Brent crude", "pct"), "DEXINUS": ("USD/INR", "pct"), "DGS10": ("US 10-year yield", "diff"),
           "DTWEXBGS": ("US dollar index", "pct"), "VIXCLS": ("US VIX", "pct"), "GPRD": ("Geopolitical risk", "log"),
           "FII_NET": ("FII net buying", "level")}


def factor_changes(w, dates):
    """Daily factor changes aligned to the Indian trading day that first reacts (next session after the factor's date)."""
    out = {}
    for sid, (_, kind) in FACTORS.items():
        if sid not in w:
            continue
        s = w[sid].dropna()
        if len(s) < 30:
            continue
        ch = {"pct": 100 * s.pct_change(), "diff": s.diff(), "log": np.log(s.clip(lower=1e-6)).diff(), "level": s}[kind].dropna()
        idx = dates.searchsorted(ch.index, side="right")          # first Indian session strictly after the factor's date
        ok = idx < len(dates)
        al = pd.Series(ch.values[ok], index=dates[idx[ok]])
        out[sid] = al.groupby(level=0).sum() if kind != "level" else al.groupby(level=0).last()
    return pd.DataFrame(out).reindex(dates)


def _ols_t(y, X):
    m = ~(np.isnan(y) | np.isnan(X).any(axis=1))
    y, X = y[m], X[m]
    if len(y) < 30:
        return np.nan, np.nan, len(y)
    X = np.column_stack([np.ones(len(y)), X])
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ b
    s2 = e @ e / (len(y) - X.shape[1])
    cov = s2 * np.linalg.inv(X.T @ X)
    return b[-1], b[-1] / np.sqrt(cov[-1, -1]), len(y)


def sector_returns(px, sector_of):
    close = px.pivot_table(index="date", columns="symbol", values="close").sort_index()
    r = close.pct_change(fill_method=None)
    sec = pd.Series(sector_of).reindex(r.columns).fillna("Other")
    return r, r.T.groupby(sec).mean().T, sec


def sensitivity(px, idx, w, sector_of, years=3, top_stocks=12):
    from_ = px.date.max() - pd.DateOffset(years=years) if years else px.date.min()
    r, sr, sec = sector_returns(px[px.date >= from_], sector_of)
    dates = r.index
    nifty = idx.set_index("date").nifty.reindex(dates).pct_change(fill_method=None)
    f = factor_changes(w, dates)
    wk = lambda x: (1 + x.fillna(0)).resample("W-FRI").prod() - 1
    S, N = wk(sr), wk(nifty)
    rows, stock_rows = [], []
    Wr = wk(r)
    for sid, (label, kind) in FACTORS.items():
        if sid not in f or f[sid].notna().sum() < 60:
            continue
        F = f[sid].resample("W-FRI").sum()
        sd = F.std()
        if not sd or np.isnan(sd):
            continue
        X = np.column_stack([N.values, F.reindex(N.index).values])
        for col in S.columns:
            b, t, n = _ols_t(S[col].values, X)
            rows.append(dict(sector=col, factor=label, per_1sd_pct=100 * b * sd, t=t, weeks=n))
        res = []
        for col in Wr.columns:
            b, t, n = _ols_t(Wr[col].values, X)
            if n >= 52:
                res.append(dict(symbol=col, sector=sec.get(col), factor=label, per_1sd_pct=100 * b * sd, t=t, weeks=n))
        res = pd.DataFrame(res)
        if len(res):
            stock_rows.append(res.reindex(res.t.abs().sort_values(ascending=False).index).head(top_stocks))
    return pd.DataFrame(rows), (pd.concat(stock_rows, ignore_index=True) if stock_rows else pd.DataFrame())


def shocks(px, idx, w, sector_of, z=2.0):
    r, sr, sec = sector_returns(px, sector_of)
    dates = r.index
    nifty = idx.set_index("date").nifty.reindex(dates).pct_change(fill_method=None)
    f = factor_changes(w, dates)
    abn = sr.sub(nifty, axis=0)
    N, A = 100 * nifty.values, 100 * abn.values
    T = len(dates)
    out, detail = [], []
    for sid, (label, kind) in FACTORS.items():
        if sid not in f:
            continue
        x = f[sid]
        zz = (x - x.rolling(250, min_periods=120).mean().shift(1)) / x.rolling(250, min_periods=120).std().shift(1)
        for direction, mask in (("up", zz > z), ("down", zz < -z)):
            days = [dates.get_loc(d) for d in zz.index[mask.fillna(False)]]
            days = [p for p in days if p + 20 < T]
            if len(days) < 3:
                continue
            d0 = np.array([N[p] for p in days])
            d5 = np.array([np.nansum(N[p:p + 6]) for p in days])
            d20 = np.array([np.nansum(N[p:p + 21]) for p in days])
            secm = {c: np.nanmean([A[p, j] for p in days]) for j, c in enumerate(abn.columns)}
            best = sorted(secm.items(), key=lambda kv: -kv[1] if kv[1] == kv[1] else 0)
            t = lambda a: float(np.nanmean(a) / (np.nanstd(a, ddof=1) / np.sqrt(np.sum(~np.isnan(a))))) if np.sum(~np.isnan(a)) > 2 and np.nanstd(a) > 0 else np.nan
            out.append(dict(factor=label, direction=direction, events=len(days), last=str(dates[days[-1]].date()),
                            nifty_day=float(np.nanmean(d0)), t_day=t(d0), nifty_5d=float(np.nanmean(d5)), t_5d=t(d5),
                            nifty_20d=float(np.nanmean(d20)), t_20d=t(d20), share_down_day=float(100 * np.mean(d0 < 0)),
                            best_sectors=", ".join(f"{k} {v:+.2f}%" for k, v in best[:3]),
                            worst_sectors=", ".join(f"{k} {v:+.2f}%" for k, v in best[::-1][:3])))
            for j, c in enumerate(abn.columns):
                vals = np.array([A[p, j] for p in days])
                detail.append(dict(factor=label, direction=direction, sector=c, mean_vs_nifty=float(np.nanmean(vals)), t=t(vals), events=len(days)))
    return pd.DataFrame(out), pd.DataFrame(detail)


def run(save=True, log=print):
    from core.data import load_raw
    from core import macro, company_info
    from core.config import RESULTS
    px, idx, _ = load_raw()
    w = macro.wide()
    if w.empty:
        log("No macro data yet: run 'Fetch macro data' first.")
        return {}
    info = company_info.load()
    sector_of = dict(zip(info.symbol, info.sector))
    s3, st3 = sensitivity(px, idx, w, sector_of, years=3)
    sall, _ = sensitivity(px, idx, w, sector_of, years=None)
    sh, shd = shocks(px, idx, w, sector_of)
    res = dict(sens_3y=s3, sens_stocks_3y=st3, sens_all=sall, shocks=sh, shock_sectors=shd)
    if save:
        out = RESULTS / "macro"
        out.mkdir(parents=True, exist_ok=True)
        for k, v in res.items():
            v.to_csv(out / f"{k}.csv", index=False)
        cov = {sid: [str(w[sid].dropna().index.min().date()), str(w[sid].dropna().index.max().date())] for sid in FACTORS if sid in w and w[sid].notna().any()}
        (out / "meta.json").write_text(json.dumps(dict(when=str(pd.Timestamp.now().floor("s")), coverage=cov), indent=1))
        log(f"Macro study saved: {len(s3)} sector sensitivities, {len(sh)} shock groups.")
    return res


if __name__ == "__main__":
    run()
