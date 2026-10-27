"""Tally: projected vs happened for one session, from a small ledger."""
import json
import pandas as pd
from marketlab import tally as T, review as RV


def _ledger(tmp_path, monkeypatch, graded=True):
    monkeypatch.setattr(RV, "ledger", lambda: tmp_path / "ledger")
    dd = tmp_path / "ledger" / "live" / "2026-10-22"
    dd.mkdir(parents=True)
    pd.DataFrame(dict(symbol=["AAA"], date=[pd.Timestamp("2026-10-22")])).to_parquet(dd / "radar.parquet")
    calls = pd.DataFrame(dict(symbol=["AAA", "BBB"], sector=["X", "Y"], date=pd.Timestamp("2026-10-22"), move="cc", size=1.0, chance=[0.7, 0.3],
                              usual=[0.4, 0.3], lift=[0.3, 0.0], up_share=[0.7, 0.5], lean=["up", "unclear"], confidence="low",
                              ret=[0.021 if graded else None, -0.004 if graded else None], rank=[1, 2], pick=[True, False]))
    calls.to_parquet(dd / "calls.parquet")
    plans = {"AAA": {"ta": {"dir": "down"}, "fund": {"dir": "up"}, "intraday": {"lean": "up", "tradeable": True,
             "legs": [dict(leg="E1", side="long", price=101), dict(leg="SL", side="long", price=99), dict(leg="TX", side="both", price=None)]}},
             "BBB": {"intraday": {"lean": "unclear", "legs": []}}}
    (dd / "plans.json").write_text(json.dumps(dict(day="2026-10-22", plans=plans)))
    pd.DataFrame(dict(symbol=["AAA"], day=["2026-10-22"], mode=["intraday"], lean=["up"], status=["done"], tradeable=[True], side=["long"],
                      entry=[101.0], entry_time=["09:35"], first=["T1"], exit=["T2"], exit_price=[104.0], exit_time=["11:00"], r=[1.5])).to_parquet(dd / "plans_grade.parquet")
    monkeypatch.setattr(T.C, "DATA", tmp_path)
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper" / "takes.json").write_text(json.dumps([dict(symbol="AAA", day="2026-10-22", mode="intraday", lean="up", risk_rs=2000)]))
    pd.DataFrame(dict(date=pd.to_datetime(["2026-10-22", "2026-10-23"]))).to_parquet(tmp_path / "prices.parquet")


def test_build_rows_and_kpis(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    o = T.build("2026-10-22")
    assert o["ready"] and o["graded"] and o["for_day"] == "2026-10-23" and o["for_known"]
    a = {r["symbol"]: r for r in o["rows"]}["AAA"]
    assert a["pick"] and a["hit"] and a["lean_right"] is True and a["ta_right"] is False and a["fund_right"] is True
    assert a["r"] == 1.5 and a["paper"] and a["pnl_rs"] == 3000 and a["legs"] == {"E1↑": 101, "SL↑": 99}
    k = o["kpis"]
    assert (k["picks_hit"], k["picks_done"], k["said"], k["happened"]) == (1, 1, 0.7, 1.0)
    assert k["plans_r"] == 1.5 and k["target_first"] == 1 and k["paper_rs"] == 3000
    assert T.days() == [dict(day="2026-10-22", kind="live", graded=True)]


def test_not_graded_shows_projections_only(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch, graded=False)
    o = T.build("2026-10-22")
    a = {r["symbol"]: r for r in o["rows"]}["AAA"]
    assert not o["graded"] and a["ret"] is None and a["hit"] is None and o["kpis"]["picks_done"] == 0


def test_assistant_keywords_route():
    from marketlab import assistant as A
    k = lambda q: A.keywords(q, ["RELIANCE", "TCS"])
    assert k("Which stocks matched with real?")["intent"] == "matched"
    assert k("which stocks did not match")["intent"] == "missed"
    assert k("hit rate by sector last 5 days") == dict(intent="hit_rate", by="sector", days=5)
    assert k("worst plans yesterday")["order"] == "worst" and k("worst plans yesterday")["intent"] == "plans"
    assert k("RELIANCE history") == dict(intent="stock", symbol="RELIANCE")
    assert k("banking misses")["sector"] == "Financial Services"
    assert "sector" not in k("is it getting better") and k("is it getting better")["intent"] == "trend"


def test_assistant_answers_from_the_ledger_without_a_model(tmp_path, monkeypatch):
    from marketlab import assistant as A
    _ledger(tmp_path, monkeypatch)
    A._cache.clear()
    o = A.answer("Which stocks matched with real?", use_model=False)
    assert o["intent"] == "matched" and [r["symbol"] for r in o["rows"]] == ["AAA"] and o["writer"] == "template"
    assert "1 of 1" in o["title"] and o["rows"][0]["ret"] == 0.021
    o = A.answer("which did not match", use_model=False)
    assert o["rows"] == []
    o = A.answer("best plans", use_model=False)
    assert o["rows"][0]["r"] == 1.5 and o["days"] == ["2026-10-22"]
    o = A.answer("matched on 2026-10-23", use_model=False)              # a date = the session that happened that day
    assert o["days"] == ["2026-10-22"]


def test_assistant_withholds_model_numbers_not_in_the_facts(monkeypatch):
    from marketlab import assistant as A
    from core import llm
    monkeypatch.setattr(llm, "status", lambda f: (True, ""))
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "11 of 20 matched; best was TTML at 99.9%.")
    text, w = A.write("q", "Matched", dict(count=11, graded=20))
    assert w == "template" and "count: 11" in text
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "11 of the 20 picks matched.")
    assert A.write("q", "Matched", dict(count=11, graded=20)) == ("11 of the 20 picks matched.", "model")


def test_compare_and_stock_history_routes(tmp_path, monkeypatch):
    from web import api
    from marketlab import assistant as A
    _ledger(tmp_path, monkeypatch)
    A._cache.clear()
    c = api.ROUTES[("GET", "tally/compare")]({})
    d = c["days"][0]
    assert c["available"] == ["2026-10-22"] and (d["picks_hit"], d["plans_entered"], d["plans_r"], d["paper_rs"]) == (1, 1, 1.5, 3000)
    s = api.ROUTES[("GET", "tally/stock")]({"symbol": "AAA"})
    assert s["summary"]["hit"] == 1 and s["rows"][0]["r"] == 1.5 and s["rows"][0]["for_day"] == "2026-10-23"
    assert api.ROUTES[("GET", "tally/stock")]({"symbol": "ZZZ"})["rows"] == []


def test_past_night_plans_come_from_the_ledger(tmp_path, monkeypatch):
    from web import r_tradeplan as RT
    from tradeplan import plan as TP
    _ledger(tmp_path, monkeypatch)
    monkeypatch.setattr(TP, "DIR", tmp_path / "tradeplan")
    s = RT._saved({"day": "2026-10-22"})
    assert s["day"] == "2026-10-22" and "AAA" in s["plans"]
    assert RT._saved({"day": "2026-01-01"}) is None
