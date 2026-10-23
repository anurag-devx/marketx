"""Forecast records: immutable and auditable.

  data/debate/runs/<analysis_id>.json   the complete record of one analysis, written ONCE (never overwritten)
  data/debate/forecasts.parquet         one row per forecast version (index for the pages and the evaluator)
  data/debate/outcomes.parquet          one row per evaluated forecast (written by debate/evaluate.py)
  data/debate/progress.json             the running analysis's progress, for the page

A correction is a NEW record (id + '-v2', ...) with 'supersedes' set; the original stays. Every record stores the
SHA-256 of its evidence, of the stock's price rows up to the cutoff and of the model file, so a later rerun can show
whether the inputs changed. Files are written atomically and under a lock (the lab runs in its own job lane)."""
import json
import pandas as pd
from core import config as C, atomic
from core.filelock import locked

DIR = C.DATA / "debate"
RUNS = DIR / "runs"
INDEX = DIR / "forecasts.parquet"
OUTCOMES = DIR / "outcomes.parquet"
PROGRESS = DIR / "progress.json"
INDEX_COLS = ["analysis_id", "version", "supersedes", "mode", "symbol", "exchange", "horizon", "target", "band", "as_of", "cutoff", "created",
              "reference_price", "p_up", "p_down", "p_neutral", "base_up", "verdict", "calibration", "probability_label",
              "model_key", "writer", "record_sha256", "status"]


class RecordExists(RuntimeError):
    pass


def safe_id(s):
    return str(s).replace("&", "and").replace("/", "-").replace(" ", "")


def path(analysis_id):
    return RUNS / f"{safe_id(analysis_id)}.json"


def save(rec):
    """Write a new record and its index row. Refuses to overwrite an existing record."""
    from .evidence import digest
    p = path(rec["analysis_id"])
    with locked("debate"):
        if p.exists():
            raise RecordExists(f"{rec['analysis_id']} already exists; corrections must be saved as a new version")
        rec["record_sha256"] = digest({k: v for k, v in rec.items() if k != "record_sha256"})
        atomic.write_text(p, json.dumps(rec, default=_json_default, indent=1))
        v = rec["verdict"]
        pr = v.get("probabilities") or {}
        row = dict(analysis_id=rec["analysis_id"], version=rec.get("version", 1), supersedes=rec.get("supersedes"), mode=rec.get("mode", "live"), symbol=rec["spec"]["symbol"],
                   exchange=rec["spec"]["exchange"], horizon=rec["spec"]["h"], target=rec["spec"]["target"], band=rec["spec"].get("band"),
                   as_of=rec["spec"]["as_of"], cutoff=rec["spec"]["cutoff"], created=rec["created"], reference_price=rec["spec"].get("reference_price"),
                   p_up=pr.get("up"), p_down=pr.get("down"), p_neutral=pr.get("neutral"), base_up=(v.get("base_rates") or {}).get("up"),
                   verdict=v["verdict"], calibration=v.get("calibration"), probability_label=v.get("probability_label"),
                   model_key=(rec.get("model") or {}).get("key"), writer=rec.get("writers", {}).get("bull"), record_sha256=rec["record_sha256"],
                   status="final")
        idx = index()
        for c in INDEX_COLS:
            if c not in idx:
                idx[c] = None
        idx = pd.concat([idx, pd.DataFrame([row])], ignore_index=True) if len(idx) else pd.DataFrame([row])
        atomic.to_parquet(idx[INDEX_COLS], INDEX)
    return p


def _json_default(o):
    try:
        import numpy as np
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return None if not np.isfinite(o) else float(o)
        if isinstance(o, (np.bool_,)):
            return bool(o)
    except Exception:
        pass
    if isinstance(o, (pd.Timestamp,)):
        return str(o)
    return str(o)


def index():
    if not INDEX.exists():
        return pd.DataFrame(columns=INDEX_COLS)
    return pd.read_parquet(INDEX)


def load(analysis_id):
    p = path(analysis_id)
    return json.loads(p.read_text()) if p.exists() else None


def outcomes():
    if not OUTCOMES.exists():
        return pd.DataFrame()
    return pd.read_parquet(OUTCOMES)


def progress(step, pct, note="", analysis=None):
    DIR.mkdir(parents=True, exist_ok=True)
    atomic.write_text(PROGRESS, json.dumps(dict(step=step, pct=pct, note=note, analysis=analysis, at=str(pd.Timestamp.now().floor("s")))))


def read_progress():
    try:
        return json.loads(PROGRESS.read_text()) if PROGRESS.exists() else None
    except Exception:
        return None
