"""Background jobs for the dashboard: one job at a time, output to logs/jobs/<name>_<time>.log."""
import json, os, re, signal, subprocess, sys, time
from pathlib import Path
from .config import BASE

JOBS = BASE / "logs" / "jobs"
LOCK = JOBS / "current.json"
# Jobs run in three lanes, one job at a time per lane:
#   main     the agents, price downloads, studies (they write the database)
#   news     quick news / filings / macro refreshes, so they work while the agents run
#   history  long history downloads and indexing (hours), so they never block the daily run
#   lab      Market Debate Lab analyses (minutes, mostly waiting on the local model); writes only data/debate/
# Lanes never share the database; files they share are protected by core/filelock.py and written atomically.
LANES = {"news": {"news", "news-check", "announcements", "macro-fetch", "macro-all", "fundamentals", "fo"},
         "history": {"history-all", "announcements-backfill", "news-backfill", "news-articles", "search-index",
                     "library-ingest", "news-retag", "fundamentals-backfill", "shareholding", "fo-backfill", "search-compact", "fundamentals-balance"},
         "lab": {"debate", "debate-eval", "debate-batch", "debate-replay", "stock-lists", "market-lab", "market-lab-test", "market-lab-replay", "pattern-test", "desk-analyse", "talib-install"},
         "kronos": {"kronos-install", "kronos-forecast", "kronos-replay"},      # experimental, separate: see kronos_lab/README.md
         "intraday": {"intraday", "intraday-probe"}}      # 5-minute bars from Angel One: core/intraday.py
SIDE = LANES["news"]                         # kept for older callers
SIDE_LOCK = JOBS / "current_side.json"


def lane_of(name):
    return next((lane for lane, names in LANES.items() if name in names), "main")
def _python():
    """The project's .venv Python when it exists (it has every package, e.g. xlrd for the GPR macro source), whatever
    interpreter happens to be running the dashboard; otherwise the current one."""
    v = BASE / ".venv" / "bin" / "python"
    return str(v) if v.exists() else sys.executable


PY = _python()
PYK = str(BASE / ".venv-kronos" / "bin" / "python")      # Kronos's own environment (kronos_lab/setup.sh)

COMMANDS = {
    "fetch": [[PY, "-u", "fetch_angel.py"]],
    "fetch-full": [[PY, "-u", "fetch_angel.py", "--full"]],
    "fetch-broad": [[PY, "-u", "fetch_angel.py", "--broad"]],
    "quality": [[PY, "-u", "-m", "core.quality"]],
    "screener": [[PY, "-u", "pipeline.py", "screener"]],
    "screener-experiments": [[PY, "-u", "-m", "screener", "experiments"]],
    "screener-experiments-force": [[PY, "-u", "-m", "screener", "experiments", "--force"]],
    "pipeline": [[PY, "-u", "pipeline.py", "all"]],
    "fetch+pipeline": [[PY, "-u", "fetch_angel.py"], [PY, "-u", "news_fetch.py"], [PY, "-u", "-m", "core.announcements"],
                       [PY, "-u", "-m", "core.fundamentals"], [PY, "-u", "-m", "research.earnings"], [PY, "-u", "-m", "core.derivatives"], [PY, "-u", "-m", "core.macro"], [PY, "-u", "pipeline.py", "all"],
                       [PY, "-u", "-m", "research.market_report", "--if-auto"],     # written only when auto is on (Market news → Report)
                       [PY, "-u", "-m", "marketlab"], [PY, "-u", "-m", "core.intraday", "update"]],
    # dropped from the nightly run (on demand only): macro impact study, Debate batch + evaluation (Labs)
    "announcements": [[PY, "-u", "news_fetch.py"], [PY, "-u", "-m", "core.announcements"]],
    "backtest": [[PY, "-u", "backtest.py"], [PY, "-u", "strategy_research.py"], [PY, "-u", "analysis.py"]],
    "analysis": [[PY, "-u", "analysis.py"]],
    "news": [[PY, "-u", "news_fetch.py"]],
    "news-check": [[PY, "-u", "-m", "core.news", "check"]],
    "news-backfill": [[PY, "-u", "-m", "core.news", "backfill", "start=2016-01-01"]],
    "news-retag": [[PY, "-u", "-m", "core.news", "retag"]],
    "announcements-backfill": [[PY, "-u", "-m", "core.announcements", "backfill", "start=2016-01-01"]],
    "news-research": [[PY, "-u", "-m", "research.events"]],
    "fundamentals": [[PY, "-u", "-m", "core.fundamentals"], [PY, "-u", "-m", "research.earnings"]],
    "fundamentals-backfill": [[PY, "-u", "-m", "core.fundamentals", "backfill", "start=2021-01-01"], [PY, "-u", "-m", "research.earnings"]],
    "market-report": [[PY, "-u", "-m", "research.market_report"]],
    "macro-fetch": [[PY, "-u", "-m", "core.macro"]],
    "macro-research": [[PY, "-u", "-m", "research.macro_impact"]],
    "macro-all": [[PY, "-u", "-m", "core.macro"], [PY, "-u", "-m", "research.macro_impact"]],
    "search-index": [[PY, "-u", "-m", "core.search_index"]],
    "shareholding": [[PY, "-u", "-m", "core.shareholding"]],
    "fo": [[PY, "-u", "-m", "core.derivatives"]],
    "intraday": [[PY, "-u", "-m", "core.intraday"]],
    "market-lab": [[PY, "-u", "-m", "marketlab"]],
    "search-compact": [[PY, "-u", "-m", "core.search_compact"]],
    "fundamentals-balance": [[PY, "-u", "-m", "core.fundamentals", "balance"]],
    "talib-install": [[PY, "-m", "pip", "install", "--only-binary=:all:", "TA-Lib"]],
    "market-lab-test": [[PY, "-u", "-m", "marketlab", "test"]],
    "market-lab-replay": [[PY, "-u", "-m", "marketlab", "replay"]],
    "pattern-test": [[PY, "-u", "-m", "tradeplan.pattern_stats"]],
    "desk-analyse": [[PY, "-u", "-m", "desk"]],
    "intraday-probe": [[PY, "-u", "-m", "core.intraday", "probe"]],
    "fo-backfill": [[PY, "-u", "-m", "core.derivatives", "backfill"]],
    "history-all": [[PY, "-u", "-m", "core.announcements", "backfill", "start=2016-01-01"], [PY, "-u", "-m", "core.shareholding"],
                    [PY, "-u", "-m", "core.derivatives", "backfill"], [PY, "-u", "-m", "core.news", "backfill", "start=2016-01-01"], [PY, "-u", "-m", "core.articles", "max=300"],
                    [PY, "-u", "-m", "core.search_index"]],
    "library-ingest": [[PY, "-u", "-m", "core.library", "ingest"]],
    "news-articles": [[PY, "-u", "-m", "core.articles"]],
    "install-ai": [[PY, "-m", "pip", "install", "-q", "-r", "requirements-ai.txt"]],
    "debate": [[PY, "-u", "-m", "debate.engine"]],
    "debate-eval": [[PY, "-u", "-m", "debate.evaluate"]],
    "debate-batch": [[PY, "-u", "-m", "debate.batch"], [PY, "-u", "-m", "debate.evaluate"]],
    "debate-replay": [[PY, "-u", "-m", "debate.batch", "days=20"], [PY, "-u", "-m", "debate.evaluate"]],
    "stock-lists": [[PY, "-u", "-m", "core.stocklists"]],
    "kronos-install": [["/bin/bash", "kronos_lab/setup.sh"]],
    "kronos-forecast": [[PYK, "-u", "-m", "kronos_lab.forecast"]],
    "kronos-replay": [[PYK, "-u", "-m", "kronos_lab.forecast", "replay=50", "step=5"]],
    "regime": [[PY, "-u", "pipeline.py", "regime"]],
    "strategy": [[PY, "-u", "pipeline.py", "strategy"]],
    "strategy-research": [[PY, "-u", "strategy_research.py"], [PY, "-u", "analysis.py"]],
    "technical": [[PY, "-u", "pipeline.py", "technical"]],
    "risk": [[PY, "-u", "pipeline.py", "risk"]],
    "validation": [[PY, "-u", "pipeline.py", "validation"]],
    "report": [[PY, "-u", "pipeline.py", "report"]],
}


KEEP_GOING = {"history-all"}
OPTIONAL = ("marketlab", "core.intraday", "news_fetch.py", "core.announcements", "core.fundamentals", "core.derivatives", "research.earnings", "research.market_report", "core.macro", "research.macro_impact", "debate.batch", "debate.evaluate")   # a failure here is reported but never stops the agents


def _cmd(cmd):
    c = " ".join(f'"{x}"' for x in cmd)
    if any(o in cmd for o in OPTIONAL):
        return f'( {c} || echo "(optional step failed: continuing)" )'
    return c


def _alive(pid, log=None):
    """True while the job runs. Reaps finished children (no zombies) and also trusts the exit marker in the log."""
    if log and "__EXIT_CODE__=" in Path(log).read_text(errors="ignore")[-200:]:
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        return False
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except ChildProcessError:
        pass          # not our child (dashboard restarted): fall back to a signal check
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def lane_lock(name=None):
    lane = lane_of(name)
    return LOCK if lane == "main" else SIDE_LOCK if lane == "news" else JOBS / f"current_{lane}.json"


def current(name=None):
    """The running job in the lane `name` would use (main lane by default), or None. Clears stale locks."""
    lock = lane_lock(name)
    if not lock.exists():
        return None
    try:
        j = json.loads(lock.read_text())
    except Exception:
        lock.unlink(missing_ok=True)
        return None
    if _alive(j["pid"], j.get("log")):
        return j
    lock.unlink(missing_ok=True)
    return None


def busy(name):
    """True if `name` can't start now (its lane is busy)."""
    return current(name) is not None


SAFE_ARG = re.compile(r"^(20\d\d-\d\d-\d\d|--[a-z\-]+|[a-z_]+=[\w\-,.]+)$")


def start(name, args=None):
    """args: extra arguments for the job's last command (dates and flags only)."""
    args = list(args or [])
    if any(not SAFE_ARG.match(str(a)) for a in args):
        raise RuntimeError(f"Not an allowed job argument: {args}")
    if current(name):
        raise RuntimeError("Another job is running. Wait for it to finish or stop it.")
    JOBS.mkdir(parents=True, exist_ok=True)
    log = JOBS / f"{name.replace('+', '_')}_{time.strftime('%Y%m%d_%H%M%S')}.log"
    # chain the commands in one shell so 'fetch+pipeline' stops if the fetch fails; exit code goes to the log
    cmds = [list(c) for c in COMMANDS[name]]
    cmds[-1] += args
    n = len(cmds)
    mark = lambda i, c: f'echo "__STEP__ {i}/{n} {step_of(c)[0]}"'   # progress markers for the dashboard
    if name in KEEP_GOING:                       # long multi-step downloads: one failing source doesn't stop the rest
        chain = " ; ".join(f'( {mark(i, c)}; {" ".join(chr(34) + x + chr(34) for x in c)} || echo "(step failed: continuing)" )'
                           for i, c in enumerate(cmds, 1))
    else:
        chain = " && ".join(f"{mark(i, c)} && {_cmd(c)}" for i, c in enumerate(cmds, 1))
    script = f'{chain}; echo "__EXIT_CODE__=$?"'
    with open(log, "w") as f:
        f.write(f"job {name} started {time.strftime('%Y-%m-%d %H:%M:%S')} · Python {PY}\n")
        f.flush()
        p = subprocess.Popen(["/bin/bash", "-c", script], cwd=BASE, stdout=f, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
    lane_lock(name).write_text(json.dumps(dict(name=name, pid=p.pid, started=time.strftime("%Y-%m-%d %H:%M:%S"), log=str(log))))
    return log


def stop(name=None):
    j = current(name)
    if j:
        try:
            os.killpg(j["pid"], signal.SIGTERM)
        except OSError:
            pass
        with open(j["log"], "a") as f:
            f.write("\njob stopped by user\n__EXIT_CODE__=143\n")
        lane_lock(j["name"]).unlink(missing_ok=True)


def history(n=30):
    JOBS.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(JOBS.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:n]:
        txt = f.read_text(errors="ignore")
        code = None
        if "__EXIT_CODE__=" in txt:
            code = int(txt.rsplit("__EXIT_CODE__=", 1)[1].split()[0])
        out.append(dict(job=f.stem.rsplit("_", 2)[0], file=f.name,
                        finished=time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
                        result="running" if code is None else ("ok" if code == 0 else "stopped" if code == 143 else f"failed ({code})"), path=str(f)))
    return out


def tail(path, n=300):
    p = Path(path)
    if not p.exists():
        return ""
    lines = p.read_text(errors="ignore").splitlines()
    return "\n".join(l for l in lines[-n:] if not l.startswith(("__EXIT_CODE__", "__STEP__")))


# ---------------- progress (for the dashboard) ----------------
# command -> (what the user sees, agent tile key)
STEPS = [("fetch_angel.py", "Downloading prices", "fetch"), ("news_fetch.py", "Fetching news", "announcements"),
         ("core.announcements", "Fetching NSE filings", "announcements"), ("core.news", "Downloading news history", "announcements"),
         ("core.articles", "Reading article text", "search-index"),
         ("core.fundamentals", "Reading quarterly results (NSE XBRL)", "announcements"),
         ("research.earnings", "Results: growth, surprise and price reaction", "announcements"),
         ("core.macro", "Fetching macro and geopolitical data", "macro-all"), ("research.macro_impact", "Running the macro study", "macro-all"),
         ("pipeline.py", "Running the agents", None), ("research.market_report", "Writing the market report", "market-report"),
         ("debate.batch", "Nightly forecasts", "debate"), ("debate.evaluate", "Scoring forecasts", "debate"),
         ("debate.engine", "Debate analysis", "debate"), ("core.stocklists", "Downloading NSE index lists", "debate"),
         ("core.search_index", "Building the search index", "search-index"), ("core.library", "Adding to the documents library", "search-index"),
         ("backtest.py", "Backtesting strategies", "backtest"), ("strategy_research.py", "Strategy research", "backtest"),
         ("analysis.py", "Strategy analysis", "backtest"), ("research.events", "Running the news study", "announcements"),
         ("core.quality", "Checking data quality", "fetch"), ("pip", "Installing packages", None),
         ("kronos_lab.forecast", "Kronos forecasts", None), ("core.shareholding", "Reading shareholding patterns", "announcements"), ("core.derivatives", "Downloading F&O data", "announcements"), ("kronos_lab/setup.sh", "Installing Kronos", None),
         ("core.intraday", "Downloading 5-minute bars", None), ("marketlab", "Market Lab: tonight's radar", "market-lab"), ("core.search_compact", "Compacting the search database", "search-index")]
AGENT_STEP = {"regime": "regime", "screener": "screener", "strategy": "strategy", "technical": "technical", "risk": "risk",
              "validation": "validation", "report": "report"}


def step_of(cmd):
    for key, label, agent in STEPS:
        if key in cmd:
            return label, agent
    return "Working", None


def activity(name=None):
    """What the job in `name`'s lane is doing now: dict(job, label, step, steps, agent, elapsed) or None."""
    j = current(name)
    if not j:
        return None
    txt = Path(j["log"]).read_text(errors="ignore") if Path(j["log"]).exists() else ""
    step, steps, label = 0, 1, "Starting"
    marks = re.findall(r"^__STEP__ (\d+)/(\d+) (.*)$", txt, re.M)
    if marks:
        step, steps, label = int(marks[-1][0]), int(marks[-1][1]), marks[-1][2]
    agent = next((a for k, lab, a in STEPS if lab == label), None)
    frac = 0.5                                       # how far into the current step (unknown -> halfway)
    if label == "Running the agents":
        sub = re.findall(r"^--- (\w+) ---$", txt[txt.rfind("__STEP__"):], re.M)
        order = list(AGENT_STEP)
        if sub:
            agent = AGENT_STEP.get(sub[-1], sub[-1])
            k = order.index(sub[-1]) + 1 if sub[-1] in order else 1
            label = f"Running the agents: {sub[-1]} ({k} of {len(order)})"
            frac = k / (len(order) + 1)
        else:
            agent, label, frac = "fetch", "Running the agents: loading data and checking quality", 0.05
    try:
        elapsed = time.time() - time.mktime(time.strptime(j["started"], "%Y-%m-%d %H:%M:%S"))
    except Exception:
        elapsed = 0
    return dict(job=j["name"], label=label, step=step, steps=steps, agent=agent, elapsed=elapsed, log=j["log"], frac=frac)


def active_agents():
    """Agent tile keys being worked on right now (all lanes)."""
    out = {}
    for probe in ("fetch", "news", "history-all", "debate"):
        a = activity(probe)
        if a and a["agent"]:
            out[a["agent"]] = a
    return out
