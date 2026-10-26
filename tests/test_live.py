"""Live terminal: the Book (ticks -> quotes, 5-min bars, alerts, paper takes, picks) with recorded-style ticks; replay ticks."""
import datetime as dt
import pandas as pd
from core import live as L

TOK = {"101": "AAA", "102": "BBB"}
IDX = {"99926000": "nifty"}


def plan(e1l=105.0, sll=103.0, t1l=108.0, t2l=110.0, e1s=95.0):
    legs = [dict(leg="E1", side="long", price=e1l, shares=100), dict(leg="SL", side="long", price=sll), dict(leg="T1", side="long", price=t1l),
            dict(leg="T2", side="long", price=t2l), dict(leg="E1", side="short", price=e1s, shares=100), dict(leg="SL", side="short", price=97.0),
            dict(leg="T1", side="short", price=92.0), dict(leg="T2", side="short", price=90.0), dict(leg="TX", side="both", price=None)]
    return {"intraday": dict(lean="unclear", legs=legs, sides=[dict(side="long", shares=100, risk_rs=200), dict(side="short", shares=100, risk_rs=200)])}


def tk(tok, hhmm, px, prev=100.0, vol=0, o=100.0):
    h, m = map(int, hhmm.split(":")[:2])
    s = int(hhmm.split(":")[2]) if hhmm.count(":") == 2 else 0
    ts = dt.datetime(2026, 10, 26, h, m, s, tzinfo=L.IST)
    return dict(token=tok, exchange_timestamp=int(ts.timestamp() * 1000), last_traded_price=round(px * 100), closed_price=round(prev * 100),
                open_price_of_the_day=round(o * 100), volume_trade_for_the_day=vol, average_traded_price=round(px * 100))


def book(**kw):
    return L.Book(TOK, IDX, watch={"AAA"}, plans={"AAA": plan()}, **kw)


def test_quotes_bars_and_breadth():
    b = book()
    for t in [tk("101", "09:15:05", 100.5, vol=10), tk("101", "09:17:00", 101.0, vol=30), tk("101", "09:21:00", 99.0, vol=60),
              tk("102", "09:16:00", 98.0), tk("99926000", "09:16:00", 25100, prev=25000)]:
        b.tick(t)
    v = b.view()
    assert v["breadth"]["adv"] == 0 and v["breadth"]["dec"] == 2
    assert v["strip"][0]["label"] == "Nifty 50" and v["strip"][0]["chg"] == 0.4
    c = b.chart("AAA")
    assert c["bars"]["t"] == ["2026-10-26T09:15:00", "2026-10-26T09:20:00"]
    assert c["bars"]["h"][0] == 101.0 and c["bars"]["v"] == [20, 30]          # first tick only sets the volume base
    assert c["or_high"] == 101.0 and c["or_low"] == 99.0
    assert "BBB" not in b.bars                                                   # not watched: quote only


def test_cross_alerts_fire_once_and_plan_legs_follow_the_triggered_side():
    b = book()
    for t in [tk("101", "09:20", 101.2), tk("101", "09:21", 101.5), tk("101", "09:25", 105.5),   # E1 before 09:30: no
              tk("101", "09:31", 105.2), tk("101", "09:40", 108.3), tk("101", "09:50", 102.9)]:
        b.tick(t)
    texts = [a["text"] for a in b.view()["alerts"]]
    assert sum("+1%" in x for x in texts) == 1 and sum("+2%" in x for x in texts) == 1
    assert any("E1 long triggered at 105.20" in x for x in texts)
    assert any("long: T1 reached" in x for x in texts) and any("long: SL hit" in x for x in texts)
    assert not any("short" in x for x in texts)
    assert b.view(since=b.seq)["alerts"] == []


def test_paper_take_entry_half_at_t1_then_breakeven():
    b = book(takes=[dict(symbol="AAA", day="2026-10-23", mode="intraday", lean="up", risk_rs=200)])
    for t in [tk("101", "09:31", 105.0), tk("101", "09:45", 108.0), tk("101", "10:00", 104.9)]:
        b.tick(t)
    x = b.view()["takes"][0]
    assert x["state"] == "closed" and x["side"] == "long" and x["entry"] == 105.0
    assert x["gross"] == 145 and x["costs"] > 0 and x["pnl"] == x["gross"] - x["costs"] and abs(x["r"] - x["pnl"] / 200) < 0.01
    c = L.fill_cost(True, 10500) + L.fill_cost(False, 5400) + L.fill_cost(False, 5245)
    assert abs(x["costs"] - c) <= 1
    assert [e["what"].split(":")[0] for e in x["events"]] == ["entered 100 shares (E1 105.00)", "T1", "back to entry"]


def test_paper_take_lean_down_ignores_long_and_closes_at_1515():
    b = book(takes=[dict(symbol="AAA", day="d", mode="intraday", lean="down", risk_rs=200)])
    for t in [tk("101", "09:35", 106.0), tk("101", "11:00", 94.5), tk("101", "15:16", 93.0)]:
        b.tick(t)
    x = b.view()["takes"][0]
    assert x["side"] == "short" and x["entry"] == 94.5 and x["state"] == "closed" and x["gross"] == 150 and x["pnl"] < 150


def test_picks_live_move_touched_and_lean():
    b = book(picks=[dict(symbol="AAA", move="cc", size=1.0, lean="down", chance=0.6, usual=0.4, confidence="low", rank=1),
                    dict(symbol="AAA", move="oc", size=1.0, lean="up", chance=0.6, usual=0.4, confidence="low", rank=1)])
    for t in [tk("101", "09:16", 98.8, o=100.5), tk("101", "09:20", 99.5, o=100.5)]:
        b.tick(t)
    cc, oc = b.view()["picks"]
    assert cc["touched"] and not cc["moved"] and cc["lean_right"] is True and cc["first"] == "09:16"
    assert oc["move_now"] == round(100 * (99.5 / 100.5 - 1), 2) and oc["lean_right"] is False


def test_save_writes_bars_alerts_takes(tmp_path):
    b = book()
    b.tick(tk("101", "09:20", 101.5))
    b.save(tmp_path)
    d = tmp_path / "2026-10-26"
    assert (d / "bars.parquet").exists() and (d / "alerts.json").exists() and (d / "takes.json").exists()


def test_replay_ticks_from_5min_bars(tmp_path, monkeypatch):
    from core import intraday as ID
    monkeypatch.setattr(ID, "BARS", tmp_path)
    ts = pd.to_datetime(["2026-10-23 09:15", "2026-10-23 09:20", "2026-10-24 09:15"])
    pd.DataFrame(dict(ts=ts, open=[100, 101, 1], high=[102, 103, 1], low=[99, 100, 1], close=[101, 102, 1], volume=[400, 800, 1])).to_parquet(tmp_path / "AAA.parquet")
    b = book(prev_close={"AAA": 99.0})
    t = L.replay_ticks(b, "2026-10-23")
    assert len(t) == 8 and t[-1]["volume_trade_for_the_day"] == 1200
    for x in t:
        b.tick(x)
    c = b.chart("AAA")
    assert c["bars"]["o"] == [100, 101] and c["bars"]["c"] == [101, 102] and c["prev"] == 99.0


def test_market_hours():
    assert L.market_open(dt.datetime(2026, 10, 26, 10, 0)) and not L.market_open(dt.datetime(2026, 10, 25, 10, 0))
    assert not L.market_open(dt.datetime(2026, 10, 26, 15, 40)) and L.bar_start(dt.datetime(2026, 10, 26, 9, 19, 59)).minute == 15


def test_live_routes_exist():
    from web import api
    for k in ["live/state", "live/start", "live/stop", "live/chart", "live/watch", "live/note"]:
        assert any(k == r[1] for r in api.ROUTES) or k in str(api.ROUTES)


def test_fill_cost_intraday_vs_delivery():
    v = 100000
    buy, sell = L.fill_cost(True, v), L.fill_cost(False, v)
    assert sell > buy                                                    # STT on the intraday sell side
    assert abs(sell - (20 + 25 + 2.97 + 0.1 + 0.18 * (20 + 2.97 + 0.1) + 30)) < 0.01
    assert L.fill_cost(True, v, delivery=True) > buy and L.fill_cost(True, 1000) < L.fill_cost(True, 100000)


def test_merge_bars_joins_api_and_live():
    k = lambda m: dt.datetime(2026, 10, 26, 9, m)
    base = {k(15): [100, 102, 99, 101, 500], k(20): [101, 103, 100, 102, 400]}
    live = {k(20): [101.5, 104, 101, 103.5, 50], k(25): [103.5, 104, 103, 103.8, 80]}
    m = L.merge_bars(base, live)
    assert sorted(m) == [k(15), k(20), k(25)]
    assert m[k(20)] == [101, 104, 100, 103.5, 400] and m[k(25)] == live[k(25)]


def test_chart_uses_base_bars_and_carries_the_call():
    b = book(picks=[dict(symbol="AAA", move="cc", size=1.0, lean="up", chance=0.6, usual=0.4, confidence="low", rank=1)])
    b.plans["AAA"]["ta"] = dict(dir="up", score=3, why=["above the 20-day average"])
    b.base["AAA"] = {dt.datetime(2026, 10, 26, 9, 15): [100, 101, 99, 100.5, 900]}
    b.tick(tk("101", "13:21", 101.0, vol=10))
    c = b.chart("AAA")
    assert c["bars"]["t"][0] == "2026-10-26T09:15:00" and c["from_api"] == 1 and c["or_high"] == 101
    assert c["call"]["ta"]["dir"] == "up" and c["call"]["picks"][0]["chance"] == 0.6 and c["call"]["intraday"]["legs"]
    row = b.view()["watch"][0]
    assert row["ml"]["lean"] == "up" and row["ta"] == "up" and row["ta_score"] == 3


def test_added_stocks_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "DIR", tmp_path)
    monkeypatch.setattr(L, "ADDED", tmp_path / "added.json")
    L.save_added("TVSMOTOR"); L.save_added("TVSMOTOR")
    assert L.added() == ["TVSMOTOR"]
