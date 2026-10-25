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
