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


class StubOHLC(Stub):
    def predict_batch(self, dfs, xs, ys, pred_len, **kw):
        self.kw = kw
        self.seen_last += [x.iat[-1] for x in xs]
        out = []
        for d, y in zip(dfs, ys):
            c = d.close.iat[-1] * np.linspace(1.01, 1.05, pred_len)
            out.append(pd.DataFrame({"open": c * 0.995, "high": c * 1.01, "low": c * 0.98, "close": c}, index=y))
        return out


def test_sampling_options_candles_and_band():
    px = _px()
    day = pd.Timestamp(px.date.unique()[420])
    stub = StubOHLC()
    a = F.args(["draws=4", "temp=1.3", "top_p=0.8", "model=mini", "h=3"])
    rows = F.forecast_day(stub, {s: g for s, g in px.groupby("symbol")}, ["AAA"], day, a, 2048)
    assert stub.kw["T"] == 1.3 and stub.kw["top_p"] == 0.8
    r = rows[0]
    assert (r["model"], r["temp"], r["top_p"], r["freq"], r["h"]) == ("mini", 1.3, 0.8, "1d", 3)
    o, b = json.loads(r["ohlc"]), json.loads(r["band"])
    assert len(o) == 3 and len(o[0]) == 4 and o[0][1] > o[0][3] > o[0][2] and len(b) == 3 and b[0][0] <= b[0][1]


def test_intraday_uses_only_bars_before_the_time():
    t = pd.date_range("2026-10-20 09:15", periods=900, freq="5min")
    t = t[(t.time >= pd.Timestamp("09:15").time()) & (t.time <= pd.Timestamp("15:25").time())]
    c = 100 + np.arange(len(t)) * 0.01
    px = pd.DataFrame(dict(date=t, symbol="AAA", open=c, high=c, low=c, close=c, volume=1.0))
    at = pd.Timestamp(t[-1].date()) + pd.Timedelta(hours=11)
    stub = StubOHLC()
    a = F.args(["freq=5m", "at=1100", "h=12", "draws=2", "lookback=60"])
    rows = F.forecast_day(stub, px, ["AAA"], at, a, 512)
    assert rows and stub.seen_last[0] == at - pd.Timedelta(minutes=5) and rows[0]["as_of"] == at and rows[0]["freq"] == "5m"
    assert list(F.future_bars(at, 3)) == [at, at + pd.Timedelta(minutes=5), at + pd.Timedelta(minutes=10)]


def test_args_validation():
    import pytest
    assert F.args(["at=930"])["at"] == "09:30"
    for bad in (["model=large"], ["freq=1h"], ["temp=9"], ["at=1102"]):
        with pytest.raises(SystemExit):
            F.args(bad)


def test_old_files_get_defaults_and_settings_are_scored_apart(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "OUT", tmp_path)
    monkeypatch.setattr(F, "FILE", tmp_path / "forecasts.parquet")
    old = pd.DataFrame([dict(as_of=pd.Timestamp("2025-01-02"), symbol="AAA", model="small", h=5, lookback=400, draws=3, close_asof=100.0,
                             pred_close=101.0, pred_ret_pct=1.0, p_up=0.6, draw_sd_pct=1.0, path="[101]", mode="live", created=pd.Timestamp.now())])
    old.to_parquet(tmp_path / "forecasts.parquet")
    d = F.load()
    assert (d.freq.iat[0], d.temp.iat[0], d.top_p.iat[0]) == ("1d", 1.0, 0.9)
    F.save([dict(old.iloc[0].drop(["mode", "created"]).to_dict(), temp=1.3)], "replay")
    assert len(F.load()) == 2                                   # a different temperature is a different forecast
    assert S.setting(dict(model="small", freq="5m", h=12, temp=1.3, top_p=0.9)) == "small · 12 bars (5-min) · T1.3 p0.9"


def test_nightly_starts_only_when_on(tmp_path, monkeypatch):
    from kronos_lab import nightly as N
    from core import jobs
    monkeypatch.setattr(N, "SETTINGS", tmp_path / "s.json")
    started = []
    monkeypatch.setattr(jobs, "start", lambda name, args=None: started.append((name, args)) or tmp_path / "x.log")
    monkeypatch.setattr(jobs, "busy", lambda name: False)
    N.main()
    (tmp_path / "s.json").write_text(json.dumps(dict(nightly=True, args=["model=mini", "replay=5"])))
    monkeypatch.setattr(N, "BASE", tmp_path)
    N.main()
    assert started == []                                         # not installed: skipped
    (tmp_path / ".venv-kronos" / "bin").mkdir(parents=True); (tmp_path / ".venv-kronos" / "bin" / "python").write_text("")
    (tmp_path / "kronos_lab" / "vendor" / "Kronos" / "model").mkdir(parents=True)
    N.main()
    assert started == [("kronos-forecast", ["model=mini"])]      # replay options never run nightly
