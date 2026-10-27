"""NSE bhavcopy + delivery: parsing (spaces, '-', series), resumable download, price check and the careful fill;
delivery and valuation as Market Lab candidates that stay out of the forecasts until switched on."""
import json
import numpy as np, pandas as pd, pytest
from core import bhavcopy as BH, valuation as V

CSV = """SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
AAA, EQ, 27-Oct-2026, 100.00, 101.00, 105.00, 99.00, 104.00, 104.50, 102.10, 1000, 10.21, 50, 600, 60.00
BBB, BE, 27-Oct-2026, 50.00, 50.00, 52.00, 49.00, 51.00, 51.00, 50.50, 200, 1.01, 10, 200, 100.00
CCC, N1, 27-Oct-2026, 1000.00, 1000.00, 1000.00, 1000.00, 1000.00, 1000.00, 1000.00, 5, 0.05, 1, -, -
"""


@pytest.fixture()
def tmpdata(tmp_path, monkeypatch):
    monkeypatch.setattr(BH, "DIR", tmp_path / "bhavcopy")
    monkeypatch.setattr(BH, "DAILY", tmp_path / "bhavcopy" / "daily.parquet")
    monkeypatch.setattr(BH, "STATE", tmp_path / "bhavcopy" / "state.json")
    monkeypatch.setattr(BH, "CHECK", tmp_path / "bhavcopy" / "check.json")
    monkeypatch.setattr(BH, "DATA", tmp_path)
    import core.filelock as FL
    monkeypatch.setattr(FL, "LOCKS", tmp_path / "locks")
    pd.DataFrame(dict(Symbol=["AAA", "BBB"])).to_csv(tmp_path / "universe.csv", index=False)
    return tmp_path


def test_parse_keeps_equity_series_and_numbers():
    d = BH.parse(CSV)
    assert list(d.symbol) == ["AAA", "BBB"] and d.close.tolist() == [104.5, 51.0] and d.deliv_pct.tolist() == [60.0, 100.0]
    assert d.date.iat[0] == pd.Timestamp("2026-10-27")
    with pytest.raises(ValueError):
        BH.parse("A,B\n1,2\n")


class Fake:
    def __init__(self, ok):
        self.ok, self.urls = ok, []

    def get(self, url):
        self.urls.append(url)
        if "27102026" in url and self.ok:
            return CSV.encode()
        raise FileNotFoundError(url)


def test_download_is_resumable_and_remembers_404(tmpdata):
    days = [pd.Timestamp("2026-10-26"), pd.Timestamp("2026-10-27")]
    f = Fake(True)
    assert BH.fetch_days(days, f, log=lambda *a: None) == 1
    assert len(BH.daily()) == 2 and BH.state()["missing"] == {"2026-10-26": "404"}
    f2 = Fake(True)
    BH.fetch_days(days, f2, log=lambda *a: None)
    assert f2.urls == []                                                       # nothing asked again


def test_check_and_fill_are_careful(tmpdata):
    px = pd.DataFrame(dict(date=pd.to_datetime(["2026-10-26", "2026-10-26"]), symbol=["AAA", "BBB"], open=[99.0, 49.0], high=[101.0, 51.0],
                           low=[98.0, 48.0], close=[100.0, 40.0], volume=[10.0, 10.0]))
    px.to_parquet(tmpdata / "prices.parquet")
    BH._save(BH.parse(CSV), {})
    out = BH.fill(log=lambda *a: None, path=tmpdata / "prices.parquet")
    assert [r["symbol"] for r in out] == ["AAA"]                               # BBB: NSE prev close 50 vs ours 40 -> not filled
    p = pd.read_parquet(tmpdata / "prices.parquet")
    assert p[(p.symbol == "AAA")].close.tolist() == [100.0, 104.5]
    assert BH.fill(log=lambda *a: None, path=tmpdata / "prices.parquet") == []  # second run: nothing new
    c = BH.check(log=lambda *a: None, px=p)
    assert c["compared"] == 1 and c["missing"] == [dict(date="2026-10-27", symbol="BBB", nse=51.0)]


def test_delivery_read(tmpdata):
    rows = [dict(date=pd.Timestamp("2026-10-01") + pd.Timedelta(days=i), symbol="AAA", series="EQ", close=100.0, deliv_qty=100.0 if i < 20 else 300.0,
                 deliv_pct=40.0 if i < 20 else 70.0, volume=250.0) for i in range(21)]
    BH._save(pd.DataFrame(rows).reindex(columns=BH.COLS), {})
    r = BH.delivery_read("AAA")
    assert r["deliv_pct"] == 70.0 and r["avg20"] == 40.0 and r["diff_pts"] == 30.0 and r["qty_x"] == 3.0


def test_candidates_are_built_but_not_used_until_switched_on(tmp_path, monkeypatch):
    from marketlab import features as F
    assert "Delivery" not in F.GROUPS and "d_rel" not in F.FEATURES and "d_rel" in F.ALL_FEATURES
    bh = pd.DataFrame([dict(date=pd.Timestamp("2026-01-01") + pd.Timedelta(days=i), symbol="AAA", series="EQ", deliv_qty=100.0 + (200 if i == 25 else 0),
                            deliv_pct=40.0 + (30 if i == 25 else 0)) for i in range(26)])
    d = pd.DataFrame(dict(symbol="AAA", date=pd.to_datetime(bh.date)))
    out = F._delivery(d, bh)
    last = out.iloc[-1]
    assert abs(last.d_rel - np.log(3.0)) < 1e-9 and abs(last.d_pct - 0.30) < 1e-9 and np.isnan(out.d_rel.iat[0])
    monkeypatch.setattr(F, "ACTIVE_FILE", tmp_path / "a.json")
    (tmp_path / "a.json").write_text(json.dumps(["Delivery", "Bogus"]))
    assert F.active_candidates() == ["Delivery"]


def test_valuation_ttm_needs_four_consecutive_quarters():
    q = pd.DataFrame(dict(symbol="AAA", period_end=pd.to_datetime(["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]),
                          filed_at=pd.to_datetime(["2025-11-01", "2026-02-01", "2026-05-01", "2026-08-01"]), status="ok", eps=[1.0, 2.0, 3.0, 4.0],
                          profit_owners=[10.0, 20.0, 30.0, 40.0], equity_owners=[np.nan, np.nan, 1000.0, np.nan], paid_up=[np.nan, np.nan, 100.0, np.nan],
                          face_value=[np.nan, np.nan, 10.0, np.nan], borrowings_current=[np.nan, np.nan, 50.0, np.nan], borrowings_noncurrent=[np.nan, np.nan, 150.0, np.nan],
                          revenue_yoy=np.nan, profit_yoy=[np.nan, np.nan, np.nan, 0.25]))
    f = V.filings(q)
    assert np.isnan(f.ttm_eps.iat[2]) and f.ttm_eps.iat[3] == 10.0 and f.ttm_profit.iat[3] == 100.0
    r = V.ratios(f.iloc[3], 200.0)
    assert r["pe"] == 20.0 and r["pb"] == 2.0 and r["roe"] == 0.1 and r["de"] == 0.2


def test_candidate_switch_needs_a_passing_test(tmp_path, monkeypatch):
    from web import api
    from web.api import UserError
    from marketlab import features as F, evaluate as MV
    monkeypatch.setattr(F, "ACTIVE_FILE", tmp_path / "a.json")
    monkeypatch.setattr(MV, "OUT_C", tmp_path / "c.json")
    set_ = api.ROUTES[("POST", "marketlab/candidates")]
    with pytest.raises(UserError):
        set_(None, {"node": "Delivery", "on": True})
    (tmp_path / "c.json").write_text(json.dumps(dict(summary=[dict(node="Delivery", move="cc", helps=True, years=1)])))
    with pytest.raises(UserError):                                             # one year is not enough
        set_(None, {"node": "Delivery", "on": True})
    (tmp_path / "c.json").write_text(json.dumps(dict(summary=[dict(node="Delivery", move="cc", helps=True, years=4)])))
    assert set_(None, {"node": "Delivery", "on": True})["active"] == ["Delivery"]
    assert set_(None, {"node": "Delivery", "on": False})["active"] == []
