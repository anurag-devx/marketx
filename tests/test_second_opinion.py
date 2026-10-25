"""Second opinion labels (TA, fundamentals), their grading against Market Lab's direction, and the TA agent's chat."""
import numpy as np, pandas as pd
from tradeplan import plan as TP, agent as AG
from marketlab import review as RV


def lv(**k):
    base = dict(close=110, sma20=105, sma50=100, P=108, atr=2, hi20=111, lo20=95, hi5=111, lo5=104, high=111, low=107, R1=112, S1=106, R2=114, S2=104, date="2026-10-23")
    return dict(base, **k)


def test_ta_view_scores_trend_pivot_and_patterns():
    t = TP.ta_view(lv(), [dict(date="2026-10-23", direction="bullish", label="Hammer")])
    assert t["dir"] == "up" and t["score"] == 4 and t["setup"] == "near the 20-day high"
    t = TP.ta_view(lv(close=96, sma20=100, sma50=104, P=99, hi20=111, lo20=95.5), [])
    assert t["dir"] == "down" and t["setup"] == "near the 20-day low"
    assert TP.ta_view(lv(close=106, P=107), [dict(date="2026-10-23", direction="bearish", label="x")])["dir"] == "neutral"


def test_fund_view_is_point_in_time_and_honest_about_missing_data():
    q = pd.DataFrame({"symbol": ["A", "A"], "filed_at": pd.to_datetime(["2026-07-20 18:00", "2026-10-23 19:00"]),
                      "period_end": pd.to_datetime(["2026-06-30", "2026-09-30"]), "profit_yoy": [0.2, -0.3], "revenue_yoy": [0.1, 0.0]})
    assert TP.fund_view(q, "A", "2026-10-22")["dir"] == "up"                 # the October result wasn't out yet
    f = TP.fund_view(q, "A", "2026-10-23")
    assert f["dir"] == "down" and f["fresh"]
    assert TP.fund_view(q, "B", "2026-10-23")["dir"] == "no data" and TP.fund_view(None, "A", "2026-10-23")["dir"] == "no data"
    q.loc[1, "profit_yoy"] = np.nan
    assert TP.fund_view(q, "A", "2026-10-23")["dir"] == "no data"


def test_second_opinion_agreement_and_direction_grading():
    calls = pd.DataFrame({"symbol": ["A", "B", "C"], "up_share": [0.7, 0.3, 0.6], "ret": [0.01, 0.02, np.nan]})
    plans = {"A": {"ta": {"dir": "up"}, "fund": {"dir": "down"}}, "B": {"ta": {"dir": "up", "setup": "tight 5-day range"}, "fund": {"dir": "no data"}}}
    c = RV.second_opinion(calls, plans).set_index("symbol")
    assert c.loc["A", "ta_agree"] == "agrees" and c.loc["A", "fund_agree"] == "disagrees" and c.loc["A", "dir_right"] == 1
    assert c.loc["B", "ta_agree"] == "disagrees" and c.loc["B", "dir_right"] == 0 and c.loc["B", "ta_setup"] == "tight 5-day range"
    assert c.loc["C", "ta_agree"] == "no data" and np.isnan(c.loc["C", "dir_right"])


FACTS = {"stock": "A", "lean": "up", "last_close": 110.0, "legs": [{"leg": "SL", "side": "long", "price": 106.5}], "mode": "intraday",
         "vs_20d_avg_pct": 4.7, "change_5d_pct": 2.1, "candle_patterns": [], "no_trade_reasons": [], "entries": 2, "exits": 4}


def test_ask_uses_the_model_and_withholds_invented_numbers(monkeypatch):
    from core import llm
    monkeypatch.setattr(llm, "status", lambda f: (True, "ok"))
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "The stop at 106.5 sits below the pivot.")
    assert AG.ask(FACTS, "why is the stop there?") == ("The stop at 106.5 sits below the pivot.", "model")
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "If it gaps 2% it could reach 131.4.")
    a, w = AG.ask(FACTS, "what if it gaps up 2%?")
    assert w == "withheld" and "131.4" not in a
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "A 2% gap would open above the stop.")
    assert AG.ask(FACTS, "what if it gaps up 2%?")[1] == "model"           # the question's own numbers are allowed


def test_ask_without_a_model_falls_back_to_the_facts(monkeypatch):
    from core import llm
    monkeypatch.setattr(llm, "status", lambda f: (False, "off"))
    monkeypatch.setattr(AG, "template", lambda f: "summary")
    a, w = AG.ask(FACTS, "anything")
    assert w == "off" and a.endswith("summary")


def test_candles_in_words_and_pattern_meanings():
    from tradeplan import patterns as PT
    g = pd.DataFrame({"date": pd.bdate_range("2026-09-01", periods=30), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0})
    g.loc[29, ["open", "high", "low", "close", "volume"]] = [101.0, 103.1, 100.9, 103.0, 2500.0]
    c = PT.describe_candles(g)
    assert len(c) == 5 and c[-1]["color"] == "green" and c[-1]["change_pct"] == 3.0 and c[-1]["volume_x"] == 2.5
    assert "strong body" in c[-1]["words"] and "gapped up 1.0%" in c[-1]["words"]
    assert "buyers took over" in PT.meaning("engulfing", "bullish") and "sellers took over" in PT.meaning("engulfing", "bearish")
    assert all(PT.meaning(p, "bullish") for p in PT.PATTERNS)


def test_paper_take_toggles_and_shows_in_review(tmp_path, monkeypatch):
    from web import api, r_tradeplan as RT
    from marketlab import review as RV2
    monkeypatch.setattr(RT, "_paper_file", lambda: tmp_path / "paper" / "takes.json")
    plans = {"day": "2026-10-23", "plans": {"AAA": {"intraday": {"lean": "up", "tradeable": True, "sides": [{"risk_rs": 1000}], "legs": []}}}}
    monkeypatch.setattr(TP, "load", lambda day=None: plans)
    take = api.ROUTES[("POST", "tradeplan/paper")]
    assert take(None, {"symbol": "AAA", "mode": "intraday"})["taken"]
    assert api.ROUTES[("GET", "tradeplan/paper")]({})["items"][0]["risk_rs"] == 1000
    assert not take(None, {"symbol": "AAA", "mode": "intraday"})["taken"] and RT.paper_list() == []
    take(None, {"symbol": "AAA", "mode": "intraday"})
    calls = pd.DataFrame({"symbol": ["AAA"], "move": "oc", "size": 1.0, "ret": 0.02, "pick": True, "hit": 1.0, "usual": 0.5, "lean": "unclear",
                          "lean_right": np.nan, "confidence": "low", "chance": 0.6, "lift": 0.1, "rank": 1, "sector": "X", "kind": "live", "day": "2026-10-23"})
    g = pd.DataFrame({"symbol": ["AAA"], "day": "2026-10-23", "mode": "intraday", "status": "done", "side": "long", "exit": "T2", "r": 1.5, "entry_time": "09:35",
                      "kind": "live", "tradeable": True})
    monkeypatch.setattr(RV2, "load", lambda kind, name: calls if name == "calls" else g if name == "plans_grade" else pd.DataFrame())
    r = api.ROUTES[("GET", "marketlab/review")]({"kind": "live", "move": "oc", "size": "1"})
    assert r["paper"]["done"] == 1 and r["paper"]["pnl_rs"] == 1500 and r["paper"]["avg_r"] == 1.5


def test_evidence_lists_every_check_and_reads_a_clean_uptrend_as_up():
    from tradeplan import evidence as EV
    n = 260
    c = 100 * 1.003 ** np.arange(n) * (1 + 0.004 * np.sin(np.arange(n)))           # steady uptrend with wiggles
    g = pd.DataFrame({"date": pd.bdate_range("2025-10-01", periods=n), "open": c * 0.998, "high": c * 1.01, "low": c * 0.99, "close": c,
                      "volume": np.where(np.diff(np.r_[c[0], c]) > 0, 2000.0, 1000.0)})
    ev = EV.evidence(g, [dict(date=str(g.date.iat[-1].date()), label="Hammer", pattern="hammer", direction="bullish", meaning="m")])
    ids = [x["id"] for x in ev["checks"]]
    assert ids[:12] == [f"E{i}" for i in range(1, 13)] and ids[-1] == "E13"
    assert ev["dir"] == "up" and ev["net"] >= EV.NET_MIN and all(set(x) >= {"metric", "value", "rule", "verdict", "reason"} for x in ev["checks"])
    assert len(ev["series"]["rsi"]) == 120 and ev["series"]["sma200"][-1] is not None
    down = g.assign(close=g.close[::-1].to_numpy(), open=g.open[::-1].to_numpy(), high=g.high[::-1].to_numpy(), low=g.low[::-1].to_numpy())
    assert EV.evidence(down)["dir"] == "down"
    t = TP.ta_view(lv(close=float(c[-1]), hi20=float(c[-1]) + 1), [], g)
    assert t["dir"] == "up" and t["why"][0].startswith("E1 ▲")
