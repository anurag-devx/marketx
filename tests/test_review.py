"""Market Lab Review: intraday crossings with a re-cross band, trade plan grading on 5-minute and daily bars, call grading,
and the ledger round trip (save → grade → route)."""
import json
import numpy as np, pandas as pd
import pytest
from marketlab import review as RV, radar as R, features as F


def bars(path, day="2026-10-26", start="09:15"):
    """path: list of (high, low, close); open = previous close."""
    ts = pd.date_range(f"{day} {start}", periods=len(path), freq="5min")
    o = [path[0][2]] + [p[2] for p in path[:-1]]
    return pd.DataFrame({"ts": ts, "open": o, "high": [p[0] for p in path], "low": [p[1] for p in path], "close": [p[2] for p in path], "volume": 1000})


def test_profile_counts_spells_with_the_recross_band():
    # ref 100, threshold 1% (101), band 0.8% (100.8)
    path = [(100.5, 99.9, 100.2), (101.2, 100.4, 101.0), (101.1, 100.85, 100.9),   # cross; wobble above the band: same spell
            (100.9, 100.3, 100.5),                                                  # closes inside the band: spell ends (15 min)
            (101.3, 100.5, 101.2), (101.4, 101.0, 101.3)]                           # second cross, held to the close
    p = RV.profile(bars(path), 100.0, 1.0)
    assert p["n_up"] == 2 and p["n_down"] == 0 and p["first_cross"] == "09:20"
    assert p["spells"][0]["minutes"] == 15 and p["held_to_close"] and p["held_dir"] == "up"
    # a band equal to the threshold ends the first spell a bar earlier
    assert RV.profile(bars(path), 100.0, 1.0, band=1.0)["spells"][0]["minutes"] == 10
    assert RV.profile(bars([(100.5, 99.5, 100)] * 4), 100.0, 1.0)["n_cross"] == 0


def test_profile_down_moves():
    p = RV.profile(bars([(100, 98.8, 98.9), (99.0, 98.7, 98.8)]), 100.0, 1.0)
    assert p["n_down"] == 1 and p["held_dir"] == "down" and p["worst_pct"] < -1


def plan(lean="up", e1=101, sl=100, t1=102, t2=103, tradeable=True):
    sides = ["long"] if lean == "up" else ["short"] if lean == "down" else ["long", "short"]
    legs = []
    for sd in sides:
        s = 1 if sd == "long" else -1
        legs += [dict(leg="E1", side=sd, price=100 + s * (e1 - 100)), dict(leg="SL", side=sd, price=100 + s * (sl - 100)),
                 dict(leg="T1", side=sd, price=100 + s * (t1 - 100)), dict(leg="T2", side=sd, price=100 + s * (t2 - 100))]
    return dict(lean=lean, legs=legs + [dict(leg="TX", side="both", price=None)], tradeable=tradeable)


def test_intraday_plan_hits_t1_then_t2_after_0930():
    path = [(101.5, 100, 101)] * 3 + [(101.2, 100.6, 101.1), (102.2, 101.0, 102.0), (103.1, 101.8, 103.0)]  # first 3 bars before 09:30
    r = RV.grade_plan(plan(), bars(path), "intraday")
    assert r["status"] == "done" and r["entry_time"] == "09:30" and r["first"] == "T1" and r["exit"] == "T2"
    assert r["r"] == pytest.approx(0.5 * 1 + 0.5 * 2)


def test_stop_first_when_one_bar_touches_both_and_breakeven_after_t1():
    path = [(100.5, 100, 100.2)] * 3 + [(101.2, 100.6, 101.1), (102.5, 99.5, 101)]
    assert RV.grade_plan(plan(), bars(path), "intraday")["exit"] == "SL"
    path = [(100.5, 100, 100.2)] * 3 + [(101.2, 100.6, 101.1), (102.1, 101.5, 102.0), (102.0, 100.9, 101.0)]
    r = RV.grade_plan(plan(), bars(path), "intraday")
    assert r["first"] == "T1" and r["exit"] == "BE" and r["r"] == pytest.approx(0.5)


def test_time_exit_no_entry_and_both_ways():
    path = [(100.5, 100, 100.2)] * 3 + [(101.2, 100.6, 101.1)] + [(101.3, 100.9, 101.2)] * 70
    r = RV.grade_plan(plan(), bars(path), "intraday")
    assert r["exit"] == "TX" and r["exit_time"] == "15:15"
    assert RV.grade_plan(plan(), bars([(100.5, 99.5, 100)] * 10), "intraday")["status"] == "no entry"
    path = [(100.5, 100, 100.2)] * 3 + [(100.5, 98.9, 99.0), (99.1, 97.9, 98.0)]                 # unclear lean: the short side breaks first
    r = RV.grade_plan(plan("unclear"), bars(path), "intraday")
    assert r["side"] == "short" and r["first"] == "T1"


def test_swing_plan_on_daily_bars():
    d = pd.DataFrame({"date": pd.bdate_range("2026-10-26", periods=5), "open": [100.5, 101.5, 102, 102.5, 102.8],
                      "high": [101.5, 102.2, 102.6, 103.2, 103], "low": [100, 101, 101.5, 102, 102.5], "close": [101.4, 102, 102.4, 103, 102.9]})
    r = RV.grade_plan(plan(), d, "swing")
    assert r["first"] == "T1" and r["exit"] == "T2" and r["status"] == "done"


def test_calls_mark_picks_hits_and_lean():
    rd = pd.DataFrame({"symbol": ["A", "B", "C"], "sector": "X", "date": pd.Timestamp("2026-10-23"), "dist_pct": 0.5})
    for m in F.MOVES:
        for s in F.SIZES:
            rd[f"ch_{m}_{s}"], rd[f"us_{m}_{s}"], rd[f"up_{m}_{s}"], rd[f"nb_{m}_{s}"] = [0.8, 0.5, 0.4], 0.5, [0.8, 0.5, 0.2], 60
    outc = pd.DataFrame({"symbol": ["A", "B", "C"], "date": pd.Timestamp("2026-10-23"), "next_date": pd.Timestamp("2026-10-26"),
                         "cc": [0.02, 0.001, np.nan], "oc": 0.0, "gp": 0.0})
    sk = {("cc", 1.0): dict(skill=True, dir_hit=0.6)}
    c = RV._calls(rd, outc, sk)
    a = c[(c.move == "cc") & (c["size"] == 1.0)].set_index("symbol")
    assert a.loc["A", "hit"] == 1 and a.loc["B", "hit"] == 0 and np.isnan(a.loc["C", "hit"])
    assert a.loc["A", "lean"] == "up" and a.loc["A", "lean_right"] == 1 and a.loc["A", "rank"] == 1
    assert (c[(c.move == "oc")].lean == "unclear").all()                    # no tested direction skill for oc: no lean


def test_ledger_round_trip_and_route(tmp_path, monkeypatch):
    from tests.test_marketlab import synth
    from web import api
    from tradeplan import plan as TP
    px, idx, uni = synth(n_sym=12, n_days=320)
    d = F.build(px, idx, uni, log=lambda *_: None)
    monkeypatch.setattr(R, "DIR", tmp_path)
    monkeypatch.setattr(R, "panel", lambda log=print, save=True: d)
    monkeypatch.setattr("core.data.load_raw", lambda *a, **k: (px, idx, uni))
    monkeypatch.setattr(RV.Bars, "session", lambda self, sym, day: pd.DataFrame())
    monkeypatch.setattr(TP, "build", lambda day, rd, sp, px=None: {"day": day, "plans": {}})
    RV.replay(n_days=3, log=lambda *_: None)
    assert len(RV.days("replay")) == 3
    calls = RV.load("replay", "calls")
    assert len(calls) and calls.ret.notna().all() and calls.pick.sum() == 3 * len(F.MOVES) * len(F.SIZES) * min(RV.TOP, 12)
    r = api.ROUTES[("GET", "marketlab/review")]({"kind": "replay", "move": "cc", "size": "1"})
    assert r["ready"] and len(r["by_day"]) == 3 and r["kpis"][0]["value"] == 3 and r["calibration"]
    assert len(r["sectors"]) == 3 and all(x["verdict"] for x in r["sectors"])
    assert any(x["nodes"] for x in r["rows"]) and all(len(x["nodes"]) == len(F.GROUPS) for x in r["rows"] if x["nodes"])


def test_verdict_names_sector_quiet_day_or_stock_level():
    from web.r_review import verdict
    m = [dict(sector="Metals", misses=4, move=-0.028), dict(sector="IT", misses=1, move=0.004), dict(sector="Banks", misses=1, move=0.0)]
    assert verdict(m, 0.001, 0.5, 0.55, 1.0).startswith("4 of 6 misses in Metals, sector ▼ -2.8%")
    s = [dict(sector=x, misses=1, move=0.0) for x in "ABCDE"]
    assert "market-wide calm" in verdict(s, 0.0, 0.3, 0.55, 1.0)
    assert verdict(s, 0.0, 0.5, 0.55, 1.0) == "misses spread across sectors on a flat market (• +0.0%) → stock-level"
    assert verdict([dict(sector="A", misses=0, move=0.01)], 0.01, 0.6, 0.5, 1.0).startswith("no misses")
