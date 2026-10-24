"""Intraday 5-minute bars: parsing, 90-day chunking, the history-depth probe, resumable download and the top-up."""
import json
import pandas as pd
import pytest
from core import intraday as I


class FakeProv:
    """Serves 5-minute bars for every weekday from `first` onwards; records the requests."""
    def __init__(self, first="2020-03-01"):
        self.first, self.calls = pd.Timestamp(first), []

    def request(self, p):
        self.calls.append(p)
        a, b = pd.Timestamp(p["fromdate"][:10]), pd.Timestamp(p["todate"][:10])
        rows = []
        for day in pd.bdate_range(max(a, self.first), b):
            for k in range(0, 75, 37):                      # three bars a day is enough for the tests
                t = day + pd.Timedelta(hours=9, minutes=15) + pd.Timedelta(minutes=5 * k)
                rows.append([t.strftime("%Y-%m-%dT%H:%M:%S+05:30"), 100, 101, 99, 100.5, 1000])
        return rows


@pytest.fixture
def tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(I, "DIR", tmp_path)
    monkeypatch.setattr(I, "BARS", tmp_path / "5min")
    monkeypatch.setattr(I, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(I, "tokens", lambda: {"RELIANCE": "1", "TCS": "2", "INFY": "3"})
    monkeypatch.setattr(I, "universe", lambda: ["RELIANCE", "TCS", "INFY", "NOTOKEN"])
    return tmp_path


def test_parse_drops_bad_rows_and_duplicates():
    d = I.parse([["2026-10-23T09:15:00+05:30", 1, 2, 1, 1.5, 10], ["2026-10-23T09:15:00+05:30", 1, 2, 1, 1.6, 11],
                 ["bad", 1, 2, 1, 1, 1], ["2026-10-23T09:20:00+05:30", 1, 0.5, 1, 1, 1]])
    assert len(d) == 1 and d.close.iat[0] == 1.6 and str(d.ts.iat[0]) == "2026-10-23 09:15:00"


def test_fetch_uses_90_day_requests():
    p = FakeProv()
    d = I.fetch(p, "1", "2024-01-01", "2024-12-31")
    assert len(p.calls) == 5 and all(c["interval"] == "FIVE_MINUTE" for c in p.calls)
    assert d.ts.is_monotonic_increasing and d.ts.is_unique


def test_probe_finds_start_month():
    start = I.probe(FakeProv("2020-03-01"), "1", log=lambda *_: None, today="2026-10-24")
    assert pd.Timestamp("2020-02-01") <= start <= pd.Timestamp("2020-04-15")


def test_run_downloads_then_tops_up_and_resumes(tmp):
    p = FakeProv("2026-01-01")
    r = I.run(p, mode="all", log=lambda *_: None, today="2026-06-30 18:00")
    st = json.loads((tmp / "state.json").read_text())
    assert r["done"] == 3 and set(st["symbols"]) == {"RELIANCE", "TCS", "INFY"}
    assert I.load("TCS").ts.max() == pd.Timestamp("2026-06-30 15:25")
    n0 = len(p.calls)
    I.run(p, mode="update", log=lambda *_: None, today="2026-06-30 18:30")      # already has today's last bar: nothing to ask
    assert len(p.calls) == n0
    I.run(p, mode="update", log=lambda *_: None, today="2026-07-02 18:00")      # two new days, one request per stock
    assert len(p.calls) == n0 + 3 and I.load("INFY").ts.max() == pd.Timestamp("2026-07-02 15:25")
    assert I.load("INFY").ts.is_unique


def test_update_mode_never_starts_new_history(tmp):
    p = FakeProv("2026-01-01")
    I.run(p, symbols=["TCS"], mode="all", log=lambda *_: None, today="2026-03-31 18:00")
    I.run(p, mode="update", log=lambda *_: None, today="2026-04-02 18:00")
    assert set(json.loads((tmp / "state.json").read_text())["symbols"]) == {"TCS"}


def test_lock_blocks_a_second_run(tmp):
    lk = I._lock()
    with pytest.raises(I.Busy):
        I._lock()
    lk.unlink()
    I._lock().unlink()


def test_intraday_route_groups_bars_and_adds_vwap(tmp):
    from web import api
    I.run(FakeProv("2026-06-01"), symbols=["TCS"], mode="all", log=lambda *_: None, today="2026-06-30 18:00")
    r = api.ROUTES[("GET", "stock/intraday")]({"symbol": "TCS", "days": "5", "bar": "60"})
    assert r["day"] == "2026-06-30" and len(set(t[:10] for t in r["bars"]["t"])) == 5
    assert len(r["bars"]["vwap"]) == len(r["bars"]["t"])
    assert api.ROUTES[("GET", "stock/intraday")]({"symbol": "INFY"})["empty"]
