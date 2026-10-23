"""The Strategy Lab workspace, kept apart from the live agents:
    lab/workspace/strategies/<id>.json     your strategies (rules + notes + every saved version)
    lab/workspace/runs/<run_id>/           results of each test (trades, equity, stats)
    lab/workspace/lab.duckdb               index of runs, holdout reveals
Nothing here is read by the daily agents until you send a strategy to the Strategy agent and approve it."""
import json, re, shutil, time, uuid
import numpy as np, pandas as pd
import duckdb
from core import config as C
from . import engine as E

WS = C.BASE / "lab" / "workspace"
STRATS = WS / "strategies"
RUNS = WS / "runs"
DB = WS / "lab.duckdb"
for p in (STRATS, RUNS):
    p.mkdir(parents=True, exist_ok=True)


def _con():
    con = duckdb.connect(str(DB))
    con.execute("""create table if not exists runs (run_id varchar, strategy_id varchar, name varchar, version varchar,
                   created timestamp, mode varchar, universe varchar, seconds double, data_last varchar,
                   design_excess double, design_t double, validation_excess double, validation_t double,
                   passes_so_far boolean, first_fail varchar, holdout_revealed boolean, note varchar)""")
    con.execute("create table if not exists reveals (strategy_id varchar, run_id varchar, version varchar, seen_at timestamp)")
    return con


def q(sql, params=None):
    con = _con()
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()


def ex(sql, params=None):
    con = _con()
    try:
        con.execute(sql, params or [])
    finally:
        con.close()


# ---------------- strategies ----------------
def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "strategy"
    return f"{s}-{uuid.uuid4().hex[:4]}"


def list_strategies(archived=False):
    out = []
    for f in sorted(STRATS.glob("*.json")):
        try:
            s = json.loads(f.read_text())
        except Exception:
            continue
        if bool(s.get("archived")) != archived:
            continue
        out.append(s)
    return sorted(out, key=lambda s: s.get("updated", ""), reverse=True)


def get(sid):
    f = STRATS / f"{sid}.json"
    return json.loads(f.read_text()) if f.exists() else None


def save(defn, sid=None, note=""):
    """Saves (creates if sid is None). Keeps a history entry whenever the rules change."""
    d = E.normalize(defn)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    if sid is None:
        sid = slug(d["name"])
        rec = dict(id=sid, created=now, history=[])
    else:
        rec = get(sid) or dict(id=sid, created=now, history=[])
    v = E.version_hash(d)
    if not rec["history"] or rec["history"][-1]["version"] != v:
        rec["history"].append(dict(version=v, saved=now, note=note, rules={k: d[k] for k in E.RULE_KEYS}))
    rec.update(definition=d, name=d["name"], mode=d["mode"], updated=now, version=v, archived=False)
    (STRATS / f"{sid}.json").write_text(json.dumps(rec, indent=2, default=str))
    return sid


def archive(sid, on=True):
    rec = get(sid)
    if rec:
        rec["archived"] = on
        (STRATS / f"{sid}.json").write_text(json.dumps(rec, indent=2, default=str))


def duplicate(sid):
    rec = get(sid)
    d = dict(rec["definition"])
    d["name"] = d["name"] + " (copy)"
    return save(d, None, note=f"copied from {sid}")


def versions_tested(sid):
    r = q("select count(distinct version) n from runs where strategy_id = ?", [sid])
    return int(r.n.iat[0]) if len(r) else 0


def reveals(sid):
    r = q("select count(*) n, count(distinct version) v from reveals where strategy_id = ?", [sid])
    return (int(r.n.iat[0]), int(r.v.iat[0])) if len(r) else (0, 0)


# ---------------- runs ----------------
def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def save_run(sid, res, note=""):
    rid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    d = RUNS / rid
    d.mkdir(parents=True)
    for k in ("trades", "equity", "periods", "picks", "next"):
        v = res.get(k)
        if isinstance(v, pd.DataFrame) and len(v):
            v.to_parquet(d / f"{k}.parquet", index=False)
    defn = res["definition"]
    meta = dict(run_id=rid, strategy_id=sid, version=E.version_hash(defn), definition=defn, mode=res["mode"], info=res["info"],
                by_period=res["by_period"], overall=res["overall"], portfolio=res["portfolio"], seconds=res["seconds"],
                data_last=res["data_last"], open_at_end=res.get("open_at_end"), signals=res.get("signals"),
                bad_stop=res.get("bad_stop"), freq=res.get("freq"), created=time.strftime("%Y-%m-%d %H:%M:%S"))
    (d / "stats.json").write_text(json.dumps(_clean(meta), indent=2, default=str))
    n = versions_tested(sid) + (0 if len(q("select 1 from runs where strategy_id = ? and version = ?", [sid, meta["version"]])) else 1)
    v = E.verdict({k: x for k, x in res["by_period"].items() if k != "holdout"}, res["mode"], n)
    bp = res["by_period"]
    ex("insert into runs values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
       [rid, sid, defn["name"], meta["version"], pd.Timestamp.now().floor("s"), res["mode"], defn["universe"]["source"], res["seconds"],
        res["data_last"], _f(bp.get("design", {}).get("excess_pct")), _f(bp.get("design", {}).get("t_excess")),
        _f(bp.get("validation", {}).get("excess_pct")), _f(bp.get("validation", {}).get("t_excess")),
        bool(v["passes_so_far"]), v["first_fail"], False, note])
    return rid


def _f(x):
    try:
        x = float(x)
        return x if np.isfinite(x) else None
    except Exception:
        return None


def load_run(rid):
    d = RUNS / rid
    if not (d / "stats.json").exists():
        return None
    meta = json.loads((d / "stats.json").read_text())
    for k in ("trades", "equity", "periods", "picks", "next"):
        f = d / f"{k}.parquet"
        meta[k] = pd.read_parquet(f) if f.exists() else None
    r = q("select holdout_revealed from runs where run_id = ?", [rid])
    meta["holdout_revealed"] = bool(r.holdout_revealed.iat[0]) if len(r) else False
    return meta


def runs(sid=None, limit=200):
    if sid:
        return q("select * from runs where strategy_id = ? order by created desc limit ?", [sid, limit])
    return q("select * from runs order by created desc limit ?", [limit])


def reveal_holdout(rid):
    r = q("select strategy_id, version, holdout_revealed from runs where run_id = ?", [rid])
    if not len(r) or r.holdout_revealed.iat[0]:
        return
    ex("update runs set holdout_revealed = true where run_id = ?", [rid])
    ex("insert into reveals values (?,?,?,?)", [r.strategy_id.iat[0], rid, r.version.iat[0], pd.Timestamp.now().floor("s")])


def delete_run(rid):
    ex("delete from runs where run_id = ?", [rid])
    shutil.rmtree(RUNS / rid, ignore_errors=True)


# ---------------- hand-over to the live agents ----------------
def send_to_agents(sid, rid):
    """Files a proposal with the Strategy agent (main database). You approve it in the dashboard."""
    from core import db
    from agents import strategy as strategy_agent
    run = load_run(rid)
    rec = get(sid)
    d = run["definition"]
    live_id = "LAB_" + re.sub(r"[^A-Z0-9]+", "_", sid.upper())[:28]
    reg = strategy_agent.registry()
    if live_id not in set(reg.strategy):
        db.write("strategies", pd.DataFrame([dict(strategy=live_id, name=d["name"], family="lab", status="candidate",
                                                  since=str(pd.Timestamp.now().date()), note=f"from Strategy Lab ({sid}, run {rid})",
                                                  version=C.VERSION)]))
    (WS / "approved").mkdir(exist_ok=True)
    (WS / "approved" / f"{live_id}.json").write_text(json.dumps(dict(lab_id=sid, run_id=rid, definition=d), indent=2))
    bp = run["by_period"]
    f = lambda p, k: bp.get(p, {}).get(k)
    pid = strategy_agent.propose(
        "paper_trade", live_id, f"Paper-trade {d['name']} (from the Strategy Lab)",
        f"{rec.get('definition', {}).get('notes') or d['name']}. Beat random picks by {f('design', 'excess_pct') or 0:+.2f}% "
        f"in 2016-20 (t {f('design', 't_excess') or 0:.1f}) and {f('validation', 'excess_pct') or 0:+.2f}% in 2021-23 "
        f"(t {f('validation', 't_excess') or 0:.1f}); holdout {f('holdout', 'excess_pct') or 0:+.2f}%. "
        f"{versions_tested(sid)} version(s) tested in the Lab.", dict(lab_id=sid, run_id=rid, by_period=bp))
    return live_id, pid
