"""Coverage statuses and analysis runs (start, record per stock, list per user, reopen)."""
import json
import pytest
from web import api
from web.api import UserError


def test_signal_status_needs_a_test_for_used(monkeypatch, tmp_path):
    from core import coverage as CV
    from marketlab import evaluate as V, features as F
    monkeypatch.setattr(V, "skill_map", lambda: ({}, {"Market": {"tested": True}, "Macro": {"tested": False}}))
    monkeypatch.setattr(V, "load_candidates", lambda: {"summary": [dict(node="Valuation", move="gp", helps=True, years=1)]})
    monkeypatch.setattr(F, "ACTIVE_FILE", tmp_path / "a.json")
    (tmp_path / "a.json").write_text(json.dumps(["Valuation"]))
    s = CV.signal_status()
    assert s["Market"] == "used" and s["Macro"] == "shown" and s["Delivery"] == "candidate"
    assert s["Valuation"] == "shown"                              # switched on, but one test year is not a pass


def test_runs_start_record_and_reopen(monkeypatch, tmp_path):
    from desk import analyse as A, notes as N
    from core import jobs
    monkeypatch.setattr(A, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(N, "DIR", tmp_path / "desk")
    started = []
    monkeypatch.setattr(jobs, "busy", lambda name: False)
    monkeypatch.setattr(jobs, "start", lambda name, args: started.append((name, args)))
    run = api.ROUTES[("POST", "desk/run")]
    with pytest.raises(UserError):
        run(None, {"symbols": []})
    with pytest.raises(UserError):
        run(None, {"symbols": [f"S{i}" for i in range(101)]})
    r = run(None, {"symbols": ["tcs", "INFY", "TCS"], "label": "test"})
    assert r["n"] == 2 and started[0][0] == "desk-analyse" and started[0][1] == ["symbols=TCS,INFY", f"run={r['id']}"]
    snap = dict(symbol="TCS", as_of="2026-10-27", price=dict(close=100.0), market_lab=dict(verdicts=[dict(move="cc", size=1.0, chance=0.6, usual=0.4, lean="up", confidence="low")]),
                ta=dict(evidence=dict(dir="up", net=4)), reasons=dict(pro=[1, 2], con=[1]))
    (tmp_path / "desk" / "TCS" / "snaps").mkdir(parents=True)
    (tmp_path / "desk" / "TCS" / "snaps" / "2026-10-27_100000.json").write_text(json.dumps(snap))
    A._run_update(r["id"], result=("TCS", dict(snap="2026-10-27_100000.json", ok=True)))
    A._run_update(r["id"], result=("INFY", dict(ok=False, why="no prices")), status="done")
    lst = api.ROUTES[("GET", "desk/runs")]({})["items"]
    assert lst[0]["done"] == 1 and lst[0]["failed"] == 1 and lst[0]["status"] == "done" and lst[0]["label"] == "test"
    d = api.ROUTES[("GET", "desk/run")]({"id": r["id"]})
    rows = {x["symbol"]: x for x in d["rows"]}
    assert rows["TCS"]["chance"] == 0.6 and rows["TCS"]["ta"] == "up" and rows["TCS"]["pros"] == 2 and rows["INFY"]["state"] == "failed"
