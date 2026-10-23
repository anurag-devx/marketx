"""Market report: facts, number checker, model flow (fake model), template fallback, safety."""
import pandas as pd, pytest
from conftest import make_px
from research import market_report as MR, report_writer as W
from core import jobs

SYMS = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")


@pytest.fixture(scope="module")
def facts():
    px, bench = make_px(SYMS, start="2023-01-01", end="2025-06-30")
    px["volume"] = px.volume * 10000                           # make them liquid
    idx = bench.assign(vix=14.0)
    info = pd.DataFrame(dict(symbol=list(SYMS), company=[f"{s} Ltd" for s in SYMS], sector=["IT"] * 3 + ["Banks"] * 3))
    ann = pd.DataFrame(dict(published=[pd.Timestamp("2025-06-30 10:00")], symbol=["AAA"], subject=["Outcome of Board Meeting"],
                            text=["Q1 results"], category=["Results"], link=[""]))
    return MR.build(None, px, idx, info, None, ann, None)


def test_facts_sections(facts):
    m = facts["market"]
    assert facts["day"] == "2025-06-30"
    assert m["advances"] + m["declines"] + m["unchanged"] == m["stocks_traded"] == 6
    assert len(facts["up"]) + len(facts["down"]) <= 6
    assert {s["sector"] for s in facts["sectors"]["table"]} == {"IT", "Banks"}
    assert len(facts["sectors"]["most_affected"]) == 2
    assert facts["filings"]["count"] == 1
    aaa = [r for r in facts["up"] + facts["down"] if r["symbol"] == "AAA"]
    assert aaa and aaa[0]["news"][0]["kind"] == "NSE filing"


def test_number_checker():
    f = {"a": 1.234, "b": [23146.15, -0.65], "c": "Nifty 22,997.35"}
    assert W.check_numbers("Nifty rose 0.65% to 23,146.15 (1.2%), 52-week high, in 2025", "", f) == []
    assert W.check_numbers("Nifty rose 0.7% and 3.9%", "", f) == ["3.9"]
    assert W.check_numbers("from 22,997.35", "", f) == []


def test_template_report(facts, monkeypatch):
    rep = W.write(facts, use_llm=False, log=lambda *_: None)
    assert rep["writer"]["model"] is None and len(rep["sections"]) == len(W.SECTIONS)
    md = W.to_markdown(facts, rep)
    assert "## What went up" in md and "Not investment advice" in md
    for _, text in rep["sections"]:                            # the template itself passes the checker
        assert W.check_numbers(text, "", facts) == []


def test_model_flow_retries_then_falls_back(facts, monkeypatch):
    monkeypatch.setattr(W, "ollama_status", lambda s=None: (True, "fake", ["fake"]))
    calls = []

    def fake_chat(msgs, s):
        calls.append(msgs)
        if "Write the 'Market overview' section" in msgs[1]["content"]:
            return "The index rose 987.6% today."               # wrong twice -> template
        return "Breadth was mixed and no stock-specific reason was found."
    monkeypatch.setattr(W, "_chat", fake_chat)
    rep = W.write(facts, use_llm=True, log=lambda *_: None)
    used = rep["writer"]["used"]
    assert used["Market overview"].startswith("template") and "Market overview" in rep["writer"]["flags"]
    assert used["What went up"] == "model" and used["Summary"] == "model"
    assert dict(rep["sections"])["What went up"].startswith("Breadth")


def test_only_localhost_model():
    from core import llm
    with pytest.raises(llm.ModelError):
        llm._local_url("https://api.example.com")
    assert llm._local_url("http://localhost:11434/") == "http://localhost:11434"


def test_job_args_are_restricted():
    assert jobs.SAFE_ARG.match("2026-10-16") and jobs.SAFE_ARG.match("--no-llm") and jobs.SAFE_ARG.match("start=2016-01-01")
    for bad in ("; rm -rf /", "$(whoami)", "2026-10-16; ls", "--x y"):
        assert not jobs.SAFE_ARG.match(bad)


def test_model_error_is_retried_once(facts, monkeypatch):
    monkeypatch.setattr(W, "ollama_status", lambda s=None: (True, "fake", ["fake"]))
    monkeypatch.setattr(W.time, "sleep", lambda s: None)
    n = {"calls": 0}

    def flaky(msgs, s):
        n["calls"] += 1
        if n["calls"] == 1:
            raise RuntimeError("model busy")
        return "Breadth was mixed and no stock-specific reason was found."
    monkeypatch.setattr(W, "_chat", flaky)
    rep = W.write(facts, use_llm=True, log=lambda *a: None)
    assert rep["writer"]["used"]["Market overview"] in ("model", "template (numbers failed the check)")
    assert rep["writer"]["used"]["Market overview"] != "template (model error)"


def test_slow_model_cannot_stall_the_report(facts, monkeypatch):
    """A local model that keeps timing out: after MAX_TIMEOUTS the rest of the report uses the template at once."""
    monkeypatch.setattr(W, "ollama_status", lambda s=None: (True, "fake", ["fake"]))
    monkeypatch.setattr(W.time, "sleep", lambda s: None)
    n = {"calls": 0}

    def timeout(msgs, s):
        n["calls"] += 1
        raise RuntimeError("Local model not reachable (ReadTimeout). Is the Ollama app open?")
    monkeypatch.setattr(W, "_chat", timeout)
    rep = W.write(facts, use_llm=True, log=lambda *a: None)
    assert n["calls"] == W.MAX_TIMEOUTS                                  # no more calls once the model is known to be too slow
    assert all(v.startswith("template") for v in rep["writer"]["used"].values())
    assert any("too slow" in v for v in rep["writer"]["used"].values())


def test_report_budget(facts, monkeypatch):
    monkeypatch.setattr(W, "ollama_status", lambda s=None: (True, "fake", ["fake"]))
    t = [0.0]
    monkeypatch.setattr(W.time, "time", lambda: t[0])

    def slow(msgs, s):
        t[0] += W.BUDGET + 1                                             # one call eats the whole budget
        return "Breadth was mixed."
    monkeypatch.setattr(W, "_chat", slow)
    rep = W.write(facts, use_llm=True, log=lambda *a: None)
    used = list(rep["writer"]["used"].values())
    assert "budget" in used[1] and used[-1] == "template"               # later sections and the summary skip the model
