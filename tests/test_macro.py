"""Macro & geopolitics: parsers, timing alignment, sensitivity and shock studies (synthetic data, no network)."""
import numpy as np, pandas as pd, pytest
from core import macro as M
from research import macro_impact as MI


def test_parse_fred_both_header_styles():
    a = M.parse_fred("observation_date,DGS10\n2026-10-15,4.10\n2026-10-16,.\n", "DGS10")
    b = M.parse_fred("DATE,DGS10\n2026-10-15,4.10\n", "DGS10")
    assert len(a) == 1 and a.value.iat[0] == 4.10 and len(b) == 1      # '.' (missing) dropped


def test_gpr_link_and_parse():
    html = '<a href="gpr_files/data_gpr_daily_recent_20261013.xls">here</a> <a href="gpr_files/data_gpr_export_202610.xls">x</a>'
    assert M.gpr_link(html) == "https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent_20261013.xls"
    df = pd.DataFrame({"DATE": ["2026-10-12", "2026-10-13"], "GPRD": [120.5, 180.2], "GPRD_ACT": [100, 150], "GPRD_THREAT": [110, 170], "event": ["", ""]})
    d = M.parse_gpr(df)
    assert set(d.series) == {"GPRD", "GPRD_ACT", "GPRD_THREAT"} and len(d) == 6
    with pytest.raises(ValueError):
        M.gpr_link("<html>nothing</html>")


def test_parse_gdelt_and_fiidii():
    j = {"timeline": [{"series": "Article Count", "data": [{"date": "20261015T000000Z", "value": 120, "norm": 9000},
                                                             {"date": "20261016T000000Z", "value": 300, "norm": 9100}]}]}
    d = M.parse_gdelt(j, "GDELT_middle_east_vol")
    assert list(d.value) == [120, 300]
    f = M.parse_fiidii([{"category": "DII **", "date": "17-Oct-2026", "buyValue": "15000", "sellValue": "12000", "netValue": "3,000.50"},
                        {"category": "FII/FPI *", "date": "17-Oct-2026", "netValue": "-2500"}])
    assert dict(zip(f.series, f.value)) == {"DII_NET": 3000.5, "FII_NET": -2500.0}
    with pytest.raises(ValueError):
        M.parse_fiidii([{"foo": 1}])


def test_themes_in_headlines():
    assert "middle_east" in M.themes_in("Oil jumps as Israel strikes Iran") and "us_tariffs" in M.themes_in("US announces new tariffs on steel")
    assert M.themes_in("Infosys Q2 profit rises") == []


def _world(n=900, seed=3):
    dates = pd.bdate_range("2022-01-03", periods=n)
    rng = np.random.default_rng(seed)
    crude_chg = rng.normal(0, 2.0, n)                         # % per day, dated the US day before the Indian session
    crude_chg[[300, 400, 500, 600, 700]] = [12, 11, -11, 13, 12]   # shocks
    nifty_r = rng.normal(0, 0.009, n)
    rows = []
    for i, sym in enumerate(["OMC1", "OMC2", "OMC3", "IT1", "IT2", "IT3"]):
        beta = -0.004 if sym.startswith("OMC") else 0.0       # oil marketing companies fall when crude rises
        shifted = np.r_[0, crude_chg[:-1]]                     # yesterday's US crude move hits today's Indian session
        r = nifty_r + beta * shifted + rng.normal(0, 0.006, n)
        rows.append(pd.DataFrame(dict(date=dates, symbol=sym, close=100 * np.cumprod(1 + r))))
    px = pd.concat(rows)
    idx = pd.DataFrame(dict(date=dates, nifty=10000 * np.cumprod(1 + nifty_r)))
    brent = 80 * np.cumprod(1 + crude_chg / 100)
    w = pd.DataFrame({"DCOILBRENTEU": brent}, index=dates)      # factor dated the same calendar day (US)
    return px, idx, w, {s: ("Oil" if s.startswith("OMC") else "IT") for s in ["OMC1", "OMC2", "OMC3", "IT1", "IT2", "IT3"]}


def test_alignment_moves_factor_to_next_session():
    px, idx, w, sec = _world()
    dates = pd.DatetimeIndex(sorted(px.date.unique()))
    f = MI.factor_changes(w, dates)
    assert np.isnan(f.DCOILBRENTEU.iat[0]) and f.DCOILBRENTEU.iat[301] == pytest.approx(12, abs=1e-6)   # day-300 move -> session 301


def test_sensitivity_and_shocks_find_planted_effect():
    px, idx, w, sec = _world()
    s, st = MI.sensitivity(px, idx, w, sec, years=None)
    oil = s[(s.sector == "Oil") & (s.factor == "Brent crude")].iloc[0]
    it = s[(s.sector == "IT") & (s.factor == "Brent crude")].iloc[0]
    assert oil.per_1sd_pct < -1 and oil.t < -5 and abs(it.t) < 3
    sh, det = MI.shocks(px, idx, w, sec, z=3)
    up = det[(det.factor == "Brent crude") & (det.direction == "up") & (det.sector == "Oil")]
    assert len(up) and up.mean_vs_nifty.iat[0] < -3


def test_summary_flows_are_totals(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "DIR", tmp_path)
    monkeypatch.setattr(M, "STORE", tmp_path / "s.parquet")
    monkeypatch.setattr(M.filelock if hasattr(M, "filelock") else __import__("core.filelock", fromlist=["x"]), "LOCKS", tmp_path)
    d = pd.bdate_range("2026-09-01", periods=30)
    M._merge_save(pd.concat([pd.DataFrame(dict(date=d, value=100.0, series="FII_NET", source="t")),
                             pd.DataFrame(dict(date=d, value=[4.0] * 29 + [4.5], series="DGS10", source="t")),
                             pd.DataFrame(dict(date=d, value=[80.0] * 29 + [88.0], series="DCOILBRENTEU", source="t"))]))
    s = {r["series"]: r for r in M.summary()}
    assert s["FII_NET"]["chg_1d"] is None and s["FII_NET"]["chg_5d"] == 500 and s["FII_NET"]["change_kind"] == "total"
    assert s["DGS10"]["chg_1d"] == 0.5 and s["DGS10"]["change_kind"] == "points"
    assert s["DCOILBRENTEU"]["chg_1d"] == 10.0
