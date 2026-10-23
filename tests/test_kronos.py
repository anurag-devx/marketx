"""Kronos section (experimental, separate): forecasting uses only bars up to the as-of day, results go only to
results/kronos/, and scoring measures realized returns h sessions later. No torch needed: the model is stubbed."""
import json
import numpy as np, pandas as pd
from kronos_lab import forecast as F, score as S


def _px(n=450, syms=("AAA", "BBB", "CCC")):
    d = pd.bdate_range("2024-01-01", periods=n)
    rows = []
    for k, s in enumerate(syms):
        c = 100 * np.cumprod(1 + np.random.default_rng(k).normal(0, 0.01, n))
        rows.append(pd.DataFrame(dict(date=d, symbol=s, open=c, high=c * 1.01, low=c * 0.99, close=c, volume=1e5)))
    return pd.concat(rows, ignore_index=True)


class Stub:
    def __init__(self):
        self.seen_last = []

    def predict_batch(self, dfs, xs, ys, pred_len, **_):
        self.seen_last += [x.iat[-1] for x in xs]
        return [pd.DataFrame({"close": d.close.iat[-1] * np.linspace(1.01, 1.05, pred_len)}, index=y) for d, y in zip(dfs, ys)]


def test_forecast_uses_only_bars_up_to_the_day():
    px = _px()
    day = px.date.unique()[420]
    stub = Stub()
    rows = F.forecast_day(stub, px, ["AAA", "BBB", "CCC"], pd.Timestamp(day), F.args(["draws=3", "lookback=400"]), 512)
    assert len(rows) == 3 and all(t == day for t in stub.seen_last)          # nothing after the as-of close
    r = rows[0]
    assert r["p_up"] == 1.0 and abs(r["pred_ret_pct"] - 5.0) < 1e-6 and len(json.loads(r["path"])) == 5


def test_save_writes_only_its_own_file(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "OUT", tmp_path)
    monkeypatch.setattr(F, "FILE", tmp_path / "forecasts.parquet")
    row = dict(as_of=pd.Timestamp("2025-01-02"), symbol="AAA", model="small", h=5, lookback=400, draws=3, close_asof=100.0,
               pred_close=101.0, pred_ret_pct=1.0, p_up=0.6, draw_sd_pct=1.0, path="[101]")
    F.save([row], "replay"); F.save([dict(row, pred_ret_pct=2.0)], "replay")
    d = pd.read_parquet(tmp_path / "forecasts.parquet")
    assert len(d) == 1 and d.pred_ret_pct.iat[0] == 2.0 and [p.name for p in tmp_path.iterdir()] == ["forecasts.parquet"]


def test_scoring_uses_close_h_sessions_later():
    px = _px(30)
    idx = pd.DataFrame(dict(date=px.date.unique(), nifty=100.0))
    day = px.date.unique()[10]
    c0 = px[(px.symbol == "AAA") & (px.date == day)].close.iat[0]
    c5 = px[(px.symbol == "AAA") & (px.date == px.date.unique()[15])].close.iat[0]
    fc = pd.DataFrame([dict(as_of=day, symbol="AAA", model="small", h=5, close_asof=c0, pred_ret_pct=1.0, p_up=0.7)])
    out = S.outcomes(fc, px, idx)
    assert abs(out.realized_ret_pct.iat[0] - 100 * (c5 / c0 - 1)) < 1e-9 and out.excess_pct.iat[0] == out.realized_ret_pct.iat[0]
    late = pd.DataFrame([dict(as_of=px.date.unique()[-2], symbol="AAA", model="small", h=5, close_asof=c0, pred_ret_pct=1.0, p_up=0.7)])
    assert S.outcomes(late, px, idx).realized_ret_pct.isna().all()           # horizon not passed: not scored
