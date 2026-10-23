"""Run logging: every agent run is recorded in the agent_runs table and in logs/YYYY-MM-DD.log."""
import logging, time, traceback
from contextlib import contextmanager
import pandas as pd
from .config import BASE, VERSION
from . import db

LOGS = BASE / "logs"


def get_logger():
    LOGS.mkdir(exist_ok=True)
    log = logging.getLogger("swing")
    if not log.handlers:
        log.setLevel(logging.INFO)
        fh = logging.FileHandler(LOGS / f"{pd.Timestamp.now().date()}.log")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
    return log


@contextmanager
def agent_run(run_id, agent, for_date=""):
    """Usage: with agent_run(rid, "technical") as rec: ...; rec["rows_out"] = len(df)
    Errors are logged and re-raised so the caller can fail safe."""
    log = get_logger()
    rec = dict(run_id=run_id, agent=agent, for_date=for_date, version=VERSION, started=pd.Timestamp.now().floor("s"),
               finished=None, seconds=None, status="running", rows_out=None, message=None)
    t0 = time.time()
    log.info(f"[{run_id}] {agent} started")
    try:
        yield rec
        rec["status"] = "ok"
        log.info(f"[{run_id}] {agent} ok rows_out={rec['rows_out']} {rec['message'] or ''}")
    except Exception as e:
        rec["status"], rec["message"] = "error", f"{type(e).__name__}: {e}"
        log.error(f"[{run_id}] {agent} FAILED {rec['message']}\n{traceback.format_exc()}")
        raise
    finally:
        rec["finished"] = pd.Timestamp.now().floor("s")
        rec["seconds"] = round(time.time() - t0, 2)
        try:
            db.write("agent_runs", pd.DataFrame([rec]))
        except Exception as e:  # logging must never break a run
            log.error(f"could not write agent_runs: {e}")
