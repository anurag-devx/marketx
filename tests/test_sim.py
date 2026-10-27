"""Money simulation on a tiny ledger: 2 stocks, 4 sessions, hand-checked numbers."""
import json
import pandas as pd
import pytest
from marketlab import sim as S, tally as T, review as RV

DAYS = ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]
# AAA closes 100 -> 110 -> 99 -> 99 ; BBB closes 50 -> 50 -> 55 -> 55 ; opens = previous close + 1
CL = {"AAA": [100, 110, 99, 99], "BBB": [50, 50, 55, 55]}


@pytest.fixture
def led(tmp_path, monkeypatch):
    monkeypatch.setattr(RV, "ledger", lambda: tmp_path / "ledger")
    monkeypatch.setattr(T.C, "DATA", tmp_path)
    rows = []
    for s, cl in CL.items():
        for i, d in enumerate(DAYS):
            rows.append(dict(date=pd.Timestamp(d), symbol=s, open=(cl[i - 1] if i else cl[0]) + 1, close=cl[i], volume=1))
    pd.DataFrame(rows).to_parquet(tmp_path / "prices.parquet")
    pd.DataFrame(dict(date=pd.to_datetime(DAYS), nifty=[1000, 1010, 1000, 1020], vix=15)).to_parquet(tmp_path / "index.parquet")
    for d in DAYS[:3]:
        dd = tmp_path / "ledger" / "replay" / d
        dd.mkdir(parents=True)
        pd.DataFrame(dict(symbol=["AAA"], date=[pd.Timestamp(d)])).to_parquet(dd / "radar.parquet")
        pd.DataFrame(dict(symbol=["AAA", "BBB"], sector="X", date=pd.Timestamp(d), move="cc", size=1.0, chance=[0.8, 0.5], usual=[0.4, 0.4],
                          lean=["up", "unclear"], confidence="low", ret=[0.01, 0.01], rank=[1, 2], pick=[True, True])).to_parquet(dd / "calls.parquet")
        pd.DataFrame(dict(symbol=["AAA"], day=[d], mode=["intraday"], status=["done"], side=["long"], entry=[100.0], exit_price=[102.0],
                          r=[1.0])).to_parquet(dd / "plans_grade.parquet")
    return tmp_path


def test_hold1_equal_no_costs(led):
    r = S.run(dict(amount=10000, top=2, hold=1, costs=False))
    assert r["ready"] and [x["sell"] for x in r["rounds"]] == DAYS[1:]
    # round 1: AAA +10%, BBB 0% -> +500 ; round 2 (on 10500): AAA -10%, BBB +10% -> 0 ; round 3: both flat
    assert [x["pnl"] for x in r["rounds"]] == [500.0, 0.0, 0.0]
    s = r["summary"]
    assert s["final"] == 10500.0 and s["passed_rounds"] == 1 and s["traded"] == 3 and s["losing_streak"] == 2
    assert s["nifty_ret"] == 0.02 and s["verdict"] == "passed" and s["small_sample"]
    assert {a["symbol"]: a["pnl"] for a in r["stocks"]} == {"AAA": 500 - 525.0, "BBB": 525.0}


def test_no_reinvest_and_costs_reduce(led):
    a = S.run(dict(amount=10000, top=2, hold=1, costs=False, reinvest=False))
    assert a["rounds"][1]["invested"] == 10000 and a["summary"]["final"] == 10500.0
    b = S.run(dict(amount=10000, top=2, hold=1, costs=True))
    assert b["summary"]["final"] < 10500 and b["summary"]["costs"] > 0


def test_hold3_and_intraday(led):
    r = S.run(dict(amount=10000, top=1, hold=3, costs=False))
    assert len(r["rounds"]) == 1 and r["rounds"][0]["sell"] == DAYS[3] and r["summary"]["final"] == 9900.0
    i = S.run(dict(amount=10000, top=1, hold="intraday", costs=False))
    # buys next open (prev close + 1), sells that close: 101->110, 111->99, 100->99
    assert i["rounds"][0]["buy"] == DAYS[1] and i["rounds"][0]["stocks"][0]["buy"] == 101
    assert i["rounds"][0]["pnl"] == round(10000 * (110 / 101 - 1), 2)


def test_up_filter_edge_split_plans_basket(led):
    assert all(len(x["stocks"]) == 1 for x in S.run(dict(picks="up", top=2, costs=False))["rounds"])
    e = S.run(dict(top=2, split="edge", costs=False, amount=10000))
    assert e["rounds"][0]["stocks"][0]["weight"] == round(0.4 / 0.5, 4)
    p = S.run(dict(picks="plans", top=2, costs=False, amount=10000))
    assert p["params"]["hold"] == "intraday" and p["rounds"][0]["pnl"] == 200.0
    b = S.run(dict(picks="basket", basket=["bbb"], costs=False, amount=10000))
    assert b["summary"]["final"] == 11000.0


def test_compare_and_validation(led):
    c = S.compare(dict(amount=10000, costs=False))
    assert c["ready"] and c["rows"][0]["final"] >= c["rows"][-1]["final"] or c["rows"][-1]["traded"] == 0
    with pytest.raises(ValueError):
        S.run(dict(picks="basket"))
    with pytest.raises(ValueError):
        S.run(dict(hold=2))
    assert not S.run(dict(start="2027-01-01"))["ready"]
