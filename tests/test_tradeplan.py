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


def _facts(lean="up"):
    from tradeplan import agent as AG
    g = bars()
    lv = TP.levels(g)
    p = TP.plan_for(lv, dict(lean=lean, lift=0.05, size=1.0), {"p25": -1.5, "p75": 1.5}, "intraday", ACCT, RISK)
    return AG.facts("TEST", "intraday", p, [dict(label="Hammer", direction="bullish", date="2026-05-01")], g, dict(chance_pct=70, usual_pct=55, lean=lean))


def test_ta_template_uses_only_plan_numbers():
    from tradeplan import agent as AG
    for lean in ("up", "down", "unclear"):
        f = _facts(lean)
        t = AG.template(f)
        assert "Caution" in t and not AG.check(t, f)


def test_ta_check_catches_invented_numbers_and_falls_back(monkeypatch):
    from tradeplan import agent as AG
    from core import llm
    f = _facts()
    assert AG.check("Target 99999.5 looks good", f) == {"99999.5"}
    monkeypatch.setattr(llm, "status", lambda feat: (True, "fake"))
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "Stop at 12345.6 is safe.")
    text, writer = AG.explain(f, log=lambda *_: None)
    assert writer == "template" and "12345.6" not in text
    e1 = next(x["price"] for x in f["legs"] if x["leg"] == "E1")
    monkeypatch.setattr(llm, "chat", lambda *a, **k: f"Entry on a break of {e1}; cancel below the stop.")
    assert AG.explain(f, log=lambda *_: None)[1] == "model"


def test_v2_patterns_piercing_tweezer_three_outside():
    base = dict(open=[100.0] * 10, high=[101.0] * 10, low=[99.0] * 10, close=[100.0] * 10)
    def frame(rows):
        d = {k: base[k] + [r[i] for r in rows] for i, k in enumerate(("open", "high", "low", "close"))}
        return pd.DataFrame(dict(date=pd.bdate_range("2026-01-01", periods=len(d["open"])), volume=1000.0, **d))
    # falling: 100 → 94 over 3 bars, long red, then piercing (opens below the low, closes above the middle)
    g = frame([(99, 99.5, 97, 97.2), (97, 97.2, 95, 95.1), (95, 95.2, 91, 91.2), (90, 93.6, 89.8, 93.4)])
    s = PT.builtin(g)
    assert s.piercing.iat[-1] == 1
    g = frame([(99, 99.5, 97, 97.2), (97, 97.2, 95, 95.1), (96.5, 96.6, 94.0, 94.2), (94.2, 95.6, 94.01, 95.5)])
    assert PT.builtin(g).tweezer_bottom.iat[-1] == 1
    g = frame([(99, 99.5, 97, 97.2), (97, 97.2, 95, 95.1), (95.0, 95.2, 93.0, 93.2), (93.0, 96.0, 92.8, 95.8), (95.8, 97.5, 95.5, 97.3)])
    s = PT.builtin(g)
    assert s.engulfing.iat[-2] == 1 and s.three_outside.iat[-1] == 1
    assert set(PT.PATTERNS) == set(PT.LABEL) and all(PT.meaning(p, d) for p in PT.PATTERNS for d in ("bullish", "bearish"))


def test_grade_counts_trend_location_volume_confirmation():
    ctx = dict(fell=True, rose=False, near_low=True, near_high=False, vol_ok=True, close=100.0, next_close=101.0)
    g = PT.grade("hammer", 1, ctx)
    assert g["strength"] == "strong" and g["score"] == 4 and g["confirmed"]
    g = PT.grade("hammer", 1, dict(ctx, fell=False, near_low=False, next_close=float("nan")))
    assert g["strength"] == "weak" and g["confirmed"] is None and g["trend"] is False
    assert PT.grade("three_soldiers", 1, dict(ctx, fell=False, rose=True))["trend"] is True        # continuation needs the same trend
    assert PT.grade("doji", 2, ctx)["strength"] == "neutral"


def test_pattern_stats_rows_split_halves_and_verdicts():
    from tradeplan import pattern_stats as S
    rng = np.random.default_rng(0)
    d = pd.bdate_range("2016-01-01", periods=2000)
    e = pd.DataFrame({"date": rng.choice(d, 400), "f1": rng.normal(0.004, 0.01, 400), "f5": rng.normal(0.01, 0.02, 400), "f5c": 0.0, "mv5": 0.0, "typ5": 0.0,
                      "trend": True, "strength": "strong"})
    base = pd.DataFrame({"date": d, "f1": rng.normal(0, 0.01, 2000), "f5": rng.normal(0, 0.02, 2000), "big5": True})
    r = S._row(e, base, "hammer", "bullish", "all")
    assert r["n"] == 400 and r["win5"] > r["base5"] and r["verdict"] == "edge"
    assert S._row(e.head(10), base, "hammer", "bullish", "all")["verdict"] == "too few"
    r = S._row(e.assign(f5=-e.f5), base, "hammer", "bullish", "all")
    assert r["verdict"] == "against"


def test_recent_reports_strength_checks_and_kind():
    out, _ = PT.recent(bars(120, seed=4), days=10)
    assert all({"strength", "checks", "kind", "tested"} <= set(x) for x in out)
