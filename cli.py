"""Command line for the swing framework.
    python3 cli.py dashboard            open the control centre in your browser
    python3 cli.py web                  the new MarketX app (plain HTML/JS, built-in server) on http://127.0.0.1:8504
    python3 cli.py lab                  open the Strategy Lab (separate workspace for your strategies)
    python3 cli.py fetch [--full|--broad]  update prices (Angel One); --full re-downloads, --broad adds all NSE stocks
    python3 cli.py quality              run the data quality checks
    python3 cli.py backup               back up the database, settings, positions and the Lab
    python3 cli.py screen [args]        research shortlist (same as python3 -m screener screen ...)
    python3 cli.py experiments [args]   run screener experiments (python3 -m screener experiments ...)
    python3 cli.py test                 run the automated tests
    python3 cli.py run [agent|all]      run one agent (regime, strategy, technical, risk, validation, report) or all
    python3 cli.py schedule-refresh     point the after-close schedule at the Python running this command (after setup.sh)
    python3 cli.py daily                same as the dashboard's 'Fetch data + run all agents' (prices, news, filings, macro,
                                        all agents, macro study, market report, the lab's nightly batch and scoring)
    python3 cli.py announcements        fetch NSE announcements only
    python3 cli.py news                 fetch news headlines (feeds + sitemaps)
    python3 cli.py news-check           try every news source once, list what works
    python3 cli.py news-backfill        download older headlines from archive sitemaps (resumable)
    python3 cli.py announcements-backfill   download older NSE filings, 2016 onwards (resumable)
    python3 cli.py company-info         download sectors / industries / shares from NSE
    python3 cli.py news-research        run the event study (does news move stocks?)
    python3 cli.py macro                fetch macro & geopolitical data;  macro-research: run the macro study
    python3 cli.py market-report [YYYY-MM-DD] [--no-llm]   write the daily market report (local model via Ollama)
    python3 cli.py backtest             backtest + strategy research + analysis
    python3 cli.py research             strategy agent research only
    python3 cli.py status               last run of each agent
"""
import subprocess, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
PY = sys.executable


def sh(*args):
    return subprocess.call([PY, *args], cwd=BASE)


def in_venv():
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def pip_user():
    """--user only outside a virtual environment (pip refuses it inside one)."""
    return [] if in_venv() else ["--user"]


def ensure_packages():
    need = []
    for mod in ("streamlit", "plotly", "duckdb", "pyarrow", "tabulate"):
        try:
            __import__(mod)
        except ImportError:
            need.append(mod)
    if need:
        print(f"First run: installing {', '.join(need)} (1-2 minutes)...", flush=True)
        subprocess.check_call([PY, "-m", "pip", "install", *pip_user(), "-q", *need])
    cred = Path.home() / ".streamlit" / "credentials.toml"
    if not cred.exists():
        cred.parent.mkdir(exist_ok=True)
        cred.write_text('[general]\nemail = ""\n')


def streamlit(app, port, open_browser=True):
    url = f"http://localhost:{port}"
    print(f"Starting at {url} ... (close this window or press Ctrl+C to stop)", flush=True)
    if open_browser:
        import threading, webbrowser
        threading.Timer(4, lambda: webbrowser.open(url)).start()
    return subprocess.call([PY, "-m", "streamlit", "run", app, "--server.port", str(port),
                            "--server.headless", "true", "--server.address", "localhost",
                            "--browser.gatherUsageStats", "false", "--logger.level", "error"], cwd=BASE)


def main(argv):
    cmd = argv[0] if argv else "help"
    if cmd == "lab":
        ensure_packages()
        return streamlit("lab_app.py", argv[1] if len(argv) > 1 else "8502")
    if cmd == "screen":
        return sh("-m", "screener", "screen", *argv[1:])
    if cmd == "experiments":
        return sh("-m", "screener", "experiments", *argv[1:])
    if cmd == "test":
        try:
            __import__("pytest")
        except ImportError:
            subprocess.check_call([PY, "-m", "pip", "install", *pip_user(), "-q", "pytest"])
        return sh("-m", "pytest", "-q", "tests")
    if cmd == "announcements":
        return sh("-m", "core.announcements")
    extra = {"news": ("news_fetch.py",), "news-check": ("-m", "core.news", "check"),
             "news-backfill": ("-m", "core.news", "backfill", *argv[1:]),
             "announcements-backfill": ("-m", "core.announcements", "backfill", *argv[1:]),
             "company-info": ("-m", "core.company_info", *argv[1:]), "news-research": ("-m", "research.events", *argv[1:]),
             "market-report": ("-m", "research.market_report", *argv[1:]),
             "macro": ("-m", "core.macro"), "macro-research": ("-m", "research.macro_impact")}
    if cmd in extra:
        return sh(*extra[cmd])
    if cmd == "quality":
        return sh("-m", "core.quality")
    if cmd == "schedule-refresh":
        return schedule_refresh()
    if cmd == "backup":
        return sh("-m", "core.backup", "manual")
    if cmd == "web":
        sys.path.insert(0, str(BASE))
        from web import server, api
        api.warm()
        return server.serve(int(argv[1]) if len(argv) > 1 else 8504)
    if cmd == "dashboard":
        ensure_packages()
        return streamlit("dashboard.py", argv[1] if len(argv) > 1 else "8503")
    if cmd == "fetch":
        return sh("fetch_angel.py", *argv[1:])
    if cmd == "run":
        return sh("pipeline.py", argv[1] if len(argv) > 1 else "all")
    if cmd == "daily":
        return daily()
    if cmd == "backtest":
        return sh("backtest.py") or sh("strategy_research.py") or sh("analysis.py")
    if cmd == "research":
        return sh("strategy_research.py") or sh("analysis.py")
    if cmd == "status":
        sys.path.insert(0, str(BASE))
        from core import db
        r = db.read("""select agent, status, started, rows_out, message from (select *, row_number() over
                       (partition by agent order by started desc) rn from agent_runs) where rn = 1 order by started""")
        print(r.to_string(index=False) if r is not None and len(r) else "No runs yet.")
        return 0
    print(__doc__)
    return 0


def schedule_refresh():
    """If the after-close schedule is installed, re-register it with THIS Python (e.g. after moving to .venv), same times."""
    sys.path.insert(0, str(BASE))
    from core import schedule as S
    if not S.supported():
        print("Schedule: macOS only; nothing to do.")
        return 0
    st = S.status()
    if not st:
        print("Schedule: not installed (set it in Settings if you want the after-close run).")
        return 0
    S.install(st["hour"], st["minute"], tuple(st["weekdays"]))
    print(f"Schedule: now runs with {PY} at {st['hour']:02d}:{st['minute']:02d} on weekdays {st['weekdays']}.")
    return 0


def daily():
    """The same steps, in the same order, as the dashboard's 'Fetch data + run all agents' (one list: core/jobs.py).
    The price download must succeed; optional steps (news, filings, macro, reports, the lab's nightly batch and scoring)
    are reported but never stop the run. Skips if the dashboard is already running a job in the main lane."""
    sys.path.insert(0, str(BASE))
    from core import jobs
    if jobs.current():
        print(f"Skipped: '{jobs.current()['name']}' is already running from the dashboard.", flush=True)
        return 0
    rc = 0
    for i, cmd in enumerate(jobs.COMMANDS["fetch+pipeline"]):
        code = subprocess.call(cmd, cwd=BASE)
        if code == 0:
            continue
        if any(o in cmd for o in jobs.OPTIONAL):
            print(f"(optional step failed: {' '.join(cmd[2:])}; continuing)", flush=True)
            continue
        if i == 0:
            return 1                                    # no fresh prices: stop
        rc = code                                       # agents failed: report it, but still run the later optional steps
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
