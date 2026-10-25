"""Trade plan: levels, leg ordering, sizing, no-trade rules, candle patterns (built-in and TA-Lib when present)."""
import numpy as np, pandas as pd
import pytest
from tradeplan import plan as TP, patterns as PT


def bars(n=120, seed=3, drift=0.0):
    rng = np.random.default_rng(seed)
    c = 1000 * np.exp(np.cumsum(rng.normal(drift, 0.015, n)))
    o = c * np.exp(rng.normal(0, 0.004, n))
    return pd.DataFrame({"date": pd.bdate_range("2026-01-01", periods=n), "open": o, "high": np.maximum(o, c) * 1.006, "low": np.minimum(o, c) * 0.994, "close": c})


ACCT, RISK = {"equity": 1_000_000}, {"risk_per_trade": 0.01, "max_position_pct": 0.15, "max_positions": 10}


def legs(p, side):
    return {x["leg"]: x for x in p["legs"] if x["side"] == side}


def test_levels_are_from_the_last_bar():
    g = bars()
    lv = TP.levels(g)
    assert lv["high"] == pytest.approx(g.high.iat[-1]) and lv["P"] == pytest.approx((g.high.iat[-1] + g.low.iat[-1] + g.close.iat[-1]) / 3)
    assert lv["S2"] < lv["S1"] < lv["P"] < lv["R1"] < lv["R2"] and lv["atr"] > 0


@pytest.mark.parametrize("lean,side", [("up", "long"), ("down", "short")])
def test_one_way_plan_orders_its_legs(lean, side):
    lv = TP.levels(bars())
    p = TP.plan_for(lv, dict(lean=lean, lift=0.05, size=1.0), {"p25": -1.5, "p75": 1.5}, "intraday", ACCT, RISK)
    L, s = legs(p, side), 1 if side == "long" else -1
    assert p["n_entries"] == 2 and p["n_exits"] == 4
    assert s * (L["T2"]["price"] - L["T1"]["price"]) >= 0 and s * (L["T1"]["price"] - L["E1"]["price"]) > 0
    assert s * (L["E1"]["price"] - L["E2"]["price"]) >= 0 and s * (L["E2"]["price"] - L["SL"]["price"]) > 0
    assert abs(L["E1"]["price"] - L["E2"]["price"]) <= 0.36 * lv["atr"] + 0.05            # shallow add-on
    sd = p["sides"][0]
    assert sd["risk_rs"] <= 10_000 + 1 and sd["value_rs"] <= 150_000 + L["E1"]["price"]    # 1% risk, 15% position cap


def test_unclear_lean_plans_both_ways_with_one_entry_each():
    lv = TP.levels(bars())
    p = TP.plan_for(lv, dict(lean="unclear", lift=0.05, size=1.0), None, "intraday", ACCT, RISK)
    assert {s["side"] for s in p["sides"]} == {"long", "short"} and p["n_entries"] == 2
    for side in ("long", "short"):
        L = legs(p, side)
        assert "E2" not in L and abs(L["E1"]["price"] - L["SL"]["price"]) == pytest.approx(0.5 * lv["atr"], abs=0.1)


def test_no_trade_rules():
    lv = TP.levels(bars())
    p = TP.plan_for(lv, dict(lean="up", lift=-0.01, size=1.0), None, "intraday", ACCT, RISK, open_positions=10)
    assert not p["tradeable"] and any("edge" in f for f in p["flags"]) and any("maximum open positions" in f for f in p["flags"])
    s = TP.plan_for(lv, dict(lean="down", lift=0.05, size=1.0), None, "swing", ACCT, RISK)
    assert any("F&O" in f for f in s["flags"]) and s["tradeable"]          # swing short: a note, not a block
    z = TP.plan_for(lv, dict(lean="up", lift=0.05, size=1.0), None, "intraday", {"equity": 0}, RISK)
    assert z["sides"][0]["shares"] == 0 and any("equity" in f for f in z["flags"])


def test_builtin_engulfing_and_inside_bar():
    g = pd.DataFrame({"date": pd.bdate_range("2026-01-01", periods=13), "open": [100] * 10 + [105, 99, 101], "high": [106] * 10 + [106, 108, 104],
                      "low": [94] * 10 + [98, 98, 100], "close": [100] * 10 + [100, 107, 102]})
    s = PT.builtin(g)
    assert s.engulfing.iat[11] == 1 and s.inside_bar.iat[12] == 2


@pytest.mark.skipif(not PT.talib_status()["ok"], reason="TA-Lib not installed")
def test_builtin_matches_talib_on_engulfing():
    g = bars(600, seed=9)
    a, b = PT.builtin(g), PT.with_talib(g)
    both = ((a.engulfing != 0) & (b.engulfing != 0)).sum()
    assert both >= 0.9 * max(1, (b.engulfing != 0).sum())


def test_recent_reports_source_and_direction():
    out, src = PT.recent(bars(60), days=5)
    assert src in ("TA-Lib", "built-in") and all(x["direction"] in ("bullish", "bearish", "neutral") for x in out)
