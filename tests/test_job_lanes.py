"""News jobs run in their own lane alongside the agents; shared files are locked."""
import sys, threading, time
import pytest
from core import jobs, filelock


def test_news_runs_while_main_job_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS", tmp_path)
    monkeypatch.setattr(jobs, "LOCK", tmp_path / "current.json")
    monkeypatch.setattr(jobs, "SIDE_LOCK", tmp_path / "current_side.json")
    monkeypatch.setattr(jobs, "COMMANDS", {"pipeline": [[sys.executable, "-c", "import time; time.sleep(3)"]],
                                           "news": [[sys.executable, "-c", "print('news ok')"]],
                                           "fetch": [[sys.executable, "-c", "print(1)"]]})
    jobs.start("pipeline")
    assert jobs.busy("fetch") and not jobs.busy("news")
    with pytest.raises(RuntimeError):
        jobs.start("fetch")                       # main lane is single-file
    log = jobs.start("news")                       # news lane is free
    for _ in range(50):
        if not jobs.busy("news"):
            break
        time.sleep(0.1)
    assert "news ok" in log.read_text()
    jobs.stop()
    assert not jobs.busy("pipeline")


def test_filelock_serialises_writers(tmp_path, monkeypatch):
    monkeypatch.setattr(filelock, "LOCKS", tmp_path)
    order = []

    def w(tag):
        with filelock.locked("news"):
            order.append(f"{tag}-in"); time.sleep(0.3); order.append(f"{tag}-out")
    ts = [threading.Thread(target=w, args=(t,)) for t in "ab"]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert order[1].endswith("-out") and order[3].endswith("-out")   # never interleaved


def test_history_lane_is_separate(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS", tmp_path)
    monkeypatch.setattr(jobs, "LOCK", tmp_path / "current.json")
    monkeypatch.setattr(jobs, "SIDE_LOCK", tmp_path / "current_side.json")
    monkeypatch.setattr(jobs, "COMMANDS", {"pipeline": [[sys.executable, "-c", "import time; time.sleep(3)"]],
                                           "history-all": [[sys.executable, "-c", "import time; time.sleep(3)"]],
                                           "news-backfill": [[sys.executable, "-c", "print(1)"]]})
    jobs.start("pipeline")
    jobs.start("history-all")                       # runs alongside the agents
    assert jobs.busy("news-backfill") and jobs.lane_of("news-backfill") == "history"
    with pytest.raises(RuntimeError):
        jobs.start("news-backfill")                 # but only one history job at a time
    jobs.stop(); jobs.stop("history-all")
    assert not jobs.busy("pipeline") and not jobs.busy("history-all")


def test_scheduled_daily_runs_the_same_steps_as_the_dashboard(monkeypatch):
    import cli
    from core import jobs
    calls = []
    monkeypatch.setattr(jobs, "current", lambda name=None: None)
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd, cwd=None: calls.append(cmd) or (1 if "debate.batch" in cmd else 0))
    assert cli.daily() == 0                                  # an optional step failing doesn't fail the day
    assert calls == jobs.COMMANDS["fetch+pipeline"]
    assert any("debate.batch" in c for c in calls) and any("debate.evaluate" in c for c in calls)
    calls.clear()
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd, cwd=None: calls.append(cmd) or (1 if "fetch_angel.py" in cmd else 0))
    assert cli.daily() == 1 and len(calls) == 1             # no prices: stop
    monkeypatch.setattr(jobs, "current", lambda name=None: {"name": "pipeline"})
    calls.clear()
    assert cli.daily() == 0 and calls == []                  # dashboard job running: skip


def test_progress_markers_and_activity(tmp_path, monkeypatch):
    """Each step of a chained job writes a marker; activity() reads the current step and the agent being run."""
    from core import jobs
    monkeypatch.setattr(jobs, "JOBS", tmp_path)
    monkeypatch.setattr(jobs, "LOCK", tmp_path / "current.json")
    monkeypatch.setattr(jobs, "COMMANDS", {"t": [["/bin/echo", "fetch_angel.py"], ["/bin/sleep", "3"]]})
    jobs.start("t")
    import time
    time.sleep(0.8)
    a = jobs.activity("t")
    assert a and a["step"] == 2 and a["steps"] == 2 and a["job"] == "t"
    assert "__STEP__" not in jobs.tail(a["log"])
    jobs.stop("t")


def test_activity_maps_pipeline_steps_to_agents(tmp_path, monkeypatch):
    from core import jobs
    log = tmp_path / "x.log"
    log.write_text("__STEP__ 5/9 Running the agents\nBackup saved\n--- regime ---\nregime: ok\n--- technical ---\n")
    monkeypatch.setattr(jobs, "current", lambda name=None: dict(name="fetch+pipeline", log=str(log), started="2026-10-23 10:00:00", pid=1))
    a = jobs.activity()
    assert a["agent"] == "technical" and a["step"] == 5 and a["steps"] == 9 and "technical (4 of 7)" in a["label"]
    log.write_text("__STEP__ 5/9 Running the agents\nloading\n")
    assert jobs.activity()["agent"] == "fetch"
