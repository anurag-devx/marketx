"""Stock Desk: notes cleaning and versions, decisions with outcomes and review prompts, search, watchlist, reasons,
seasonality / price blocks, and the routes reading saved snapshots."""
import json
import numpy as np, pandas as pd
import pytest
from desk import notes as N, analyse as A


@pytest.fixture
def ddir(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "DIR", tmp_path)
    monkeypatch.setattr(N, "DIR", tmp_path)
    return tmp_path


def test_clean_keeps_formatting_and_drops_scripts_and_js_links():
    h = N.clean('<p onclick="x()">Hi <b>bold</b><script>alert(1)</script><a href="javascript:x">bad</a><a href="https://nse.in">ok</a><span class="pin" data-pin="1">📌 E6</span><img src=x></p>')
    assert "<script" not in h and "onclick" not in h and "javascript" not in h and "<img" not in h
    assert '<a href="https://nse.in">ok</a>' in h and '<span class="pin" data-pin="1">' in h and "<b>bold</b>" in h


def test_notes_versions_tags_search_and_watchlist(ddir):
    N.save_note("TCS", "<p>first idea</p>", ["Breakout", "breakout", "results"])
    n = N.save_note("TCS", "<p>second idea: margin story</p>")
    assert n["tags"] == ["breakout", "results"] and len(n["versions"]) == 1 and n["versions"][0]["html"] == "<p>first idea</p>"
    assert N.search("margin")[0]["symbol"] == "TCS" and N.search("results")[0]["symbol"] == "TCS" and N.search("nothinghere") == []
    assert N.set_watchlist(["tcs", "TCS", "bad sym!", "M&M"]) == ["TCS", "M&M"]


def test_decision_outcome_and_review_prompt(ddir):
    snap = {"as_of": "2026-01-02", "price": {"close": 100.0},
            "market_lab": {"verdicts": [{"move": "cc", "size": 1.0, "chance": 0.6, "usual": 0.4, "lean": "up", "confidence": "medium"}]},
            "ta": {"evidence": {"dir": "up"}, "plans": {"swing": {"lean": "up", "legs": [{"leg": "SL", "side": "long", "price": 95}, {"leg": "T1", "side": "long", "price": 106}]}}}}
    d = N.add_decision("TCS", "entered", "breakout", 3, snap)
    assert d["said"]["market_lab"]["chance"] == 0.6 and d["said"]["ta"] == "up"
    px = pd.DataFrame({"symbol": "TCS", "date": pd.bdate_range("2026-01-05", periods=4), "high": [103, 107, 104, 105.0], "low": [99, 101, 100, 102.0], "close": [102, 105, 103, 104.0]})
    o = N.outcome("TCS", d, px)
    assert o["sessions"] == 3 and o["plan_first"] == "target" and o["ret"] == pytest.approx(0.03)
    due = N.due("TCS", px)
    assert len(due) == 1 and due[0]["id"] == d["id"]
    N.add_review("TCS", d["id"], "worked: breakout held", o)
    assert N.due("TCS", px) == [] and N.load("TCS")["decisions"][0]["review"]["lesson"].startswith("worked")
    with pytest.raises(ValueError):
        N.add_decision("TCS", "bought-lots")


def test_price_block_seasonality_and_reasons():
    g = pd.DataFrame({"date": pd.bdate_range("2023-01-02", periods=800)})
    g["close"] = 100 * 1.001 ** np.arange(800)
    g["high"], g["low"] = g.close * 1.01, g.close * 0.99
    p = A.price_block(g)
    assert p["r1y"] == pytest.approx(1.001 ** 250 - 1, rel=1e-3) and p["hi52"] > p["lo52"]
    s = A.seasonality(g)
    assert {x["day"] for x in s["weekday"]} == {"Mon", "Tue", "Wed", "Thu", "Fri"} and len(s["month"]) == 12
    snap = {"market_lab": {"ready": True, "verdicts": [{"move": "cc", "size": 1.0, "lean": "down"}],
                           "frames": {"drivers": [{"symbol": "X", "node": "Sector", "move": "cc", "size": 1.0, "d_chance": 0.02, "d_up": 0.06}]}},
            "ta": {"ready": True, "evidence": {"checks": [{"id": "E1", "reason": "above the 20-day", "verdict": 1}]}, "patterns": []},
            "fundamentals": {"quarters": [{"profit_yoy": -0.2}]}, "ownership": [{"fii_pct": 10.0}, {"fii_pct": 11.0}]}
    r = A.reasons(snap)
    pro, con = [x["text"] for x in r["pro"]], [x["text"] for x in r["con"]]
    assert any("Sector" in x for x in pro) and any("E1" in x for x in pro) and any("FII" in x for x in pro)
    assert con[0].startswith("Market Lab leans down") and any("profit" in x for x in con)


def test_routes_read_snapshots_and_compare(ddir):
    from web import api
    snap = {"symbol": "AAA", "as_of": "2026-10-23", "built": "x", "price": {"close": 10, "r1m": 0.1}, "sector": "IT",
            "market_lab": {"ready": False, "why": "n/a"}, "ta": {"evidence": {"dir": "up", "net": 4}, "plans": {}}, "fundamentals": {"val": {"pe": 20}},
            "reasons": {"pro": [{"text": "a"}], "con": []}, "relative": {"t": ["2026-10-22", "2026-10-23"], "stock": [100, 101]}}
    (ddir / "AAA" / "snaps").mkdir(parents=True)
    (ddir / "AAA" / "latest.json").write_text(json.dumps(snap))
    (ddir / "AAA" / "snaps" / "2026-10-23_120000.json").write_text(json.dumps(snap))
    r = api.ROUTES[("GET", "desk/snapshot")]({"symbol": "AAA"})
    assert r["ready"] and r["snapshots"] == ["2026-10-23_120000.json"]
    assert not api.ROUTES[("GET", "desk/snapshot")]({"symbol": "BBB"})["ready"]
    assert api.ROUTES[("GET", "desk/snapshot")]({"symbol": "AAA", "at": "../../etc.json"})["ready"] is False
    c = api.ROUTES[("GET", "desk/compare")]({"symbols": "AAA,BBB"})
    assert c["rows"][0]["ta"] == "up" and c["missing"] == ["BBB"] and c["series"]["AAA"]["y"] == [100, 101]
    assert not api.ROUTES[("GET", "desk/marketlab")]({"symbol": "AAA"})["ready"]
    with pytest.raises(Exception):
        api.ROUTES[("POST", "desk/analyse")](None, {"symbols": []})


def test_desk_diff_compares_a_saved_analysis_with_the_latest(tmp_path, monkeypatch):
    import json
    from desk import notes as N
    from web import api
    monkeypatch.setattr(N, "DIR", tmp_path)
    old = dict(symbol="AAA", as_of="2026-10-20", built="b1", price=dict(close=100.0), market_lab=dict(verdicts=[dict(move="cc", size=1.0, chance=0.4, lean="unclear", confidence="low")]),
               ta=dict(evidence=dict(dir="down", net=-3)), reasons=dict(pro=[], con=[dict(text="below the 20-day")]))
    new = dict(old, as_of="2026-10-23", built="b2", price=dict(close=110.0), ta=dict(evidence=dict(dir="up", net=4)), reasons=dict(pro=[dict(text="above the 20-day")], con=[]))
    (tmp_path / "AAA" / "snaps").mkdir(parents=True)
    (tmp_path / "AAA" / "snaps" / "2026-10-20_100000.json").write_text(json.dumps(old))
    (tmp_path / "AAA" / "latest.json").write_text(json.dumps(new))
    r = api.ROUTES[("GET", "desk/diff")]({"symbol": "AAA", "at": "2026-10-20_100000.json"})
    rows = {x["label"]: x for x in r["rows"]}
    assert abs(r["moved"] - 0.1) < 1e-9 and rows["Chart read"]["then"] == "down" and rows["Chart read"]["now"] == "up" and rows["Chart read"]["changed"]
    assert r["reasons"]["pro_new"] == ["above the 20-day"] and r["reasons"]["con_gone"] == ["below the 20-day"]
