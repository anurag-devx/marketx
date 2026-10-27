"""API: Coverage (what data and signals each stock has, and which feed the forecasts) and analysis runs: analyse a
selection of stocks from Coverage with the full Stock Desk analysis; every run is kept and can be reopened."""
import re, secrets
import pandas as pd
from web.api import route, UserError
from core import jobs

SYM = re.compile(r"^[A-Z0-9\-]{1,20}$")
MAX_RUN = 100


@route("GET", "coverage")
def coverage(p):
    from core import coverage as CV
    return CV.table(force=p.get("refresh") == "1")


@route("GET", "coverage/stock")
def coverage_stock(p):
    from core import coverage as CV
    s = str(p.get("symbol") or "").upper()
    out = CV.stock(s) if SYM.match(s) else None
    if not out:
        raise UserError("not in the Nifty 500 list")
    return out


def _who():
    from web import auth
    w = auth.current()
    return w["user"] if w else "owner"


@route("POST", "desk/run")
def run_start(_, body):
    """Analyse the selected stocks (up to 100) as one run: one background job, results recorded per stock."""
    from desk import analyse as A
    b = body or {}
    syms = []
    for s in b.get("symbols") or []:
        s = str(s).upper().strip()
        if SYM.match(s) and s not in syms:
            syms.append(s)
    if not syms:
        raise UserError("select at least one stock (symbols with '&' can't be analysed from here yet)")
    if len(syms) > MAX_RUN:
        raise UserError(f"at most {MAX_RUN} stocks per run")
    if jobs.busy("desk-analyse"):
        raise UserError("an analysis is already running: wait for it to finish (bar at the top)")
    rid = pd.Timestamp.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    A._run_update(rid, id=rid, user=_who(), created=str(pd.Timestamp.now().floor("s")), symbols=syms, status="queued",
                  label=str(b.get("label") or "")[:120], filter=b.get("filter") if isinstance(b.get("filter"), dict) else {})
    jobs.start("desk-analyse", [f"symbols={','.join(syms)}", f"run={rid}"])
    return dict(started=True, id=rid, n=len(syms), estimate_min=round(len(syms) * 20 / 60, 1))


def _runs():
    from desk import analyse as A
    out = []
    if A.RUNS.exists():
        for f in sorted(A.RUNS.glob("*.json"), reverse=True):
            r = A.run_record(f.stem)
            if r:
                out.append(r)
    me = _who()
    return [r for r in out if r.get("user", "owner") == me or me == "owner"]


@route("GET", "desk/runs")
def runs(_):
    out = []
    for r in _runs()[:200]:
        res = r.get("results") or {}
        out.append(dict(id=r["id"], created=r.get("created"), user=r.get("user"), label=r.get("label"), n=len(r.get("symbols", [])),
                        done=sum(1 for x in res.values() if x.get("ok")), failed=sum(1 for x in res.values() if not x.get("ok")),
                        status=r.get("status"), finished=r.get("finished"), filter=r.get("filter")))
    return dict(items=out)


@route("GET", "desk/run")
def run_get(p):
    """One run: each stock's analysis exactly as saved in that run (headline numbers + a link to open it in Stock Desk)."""
    from desk import notes as N
    rid = str(p.get("id") or "")
    r = next((x for x in _runs() if x["id"] == rid), None)
    if not r:
        raise UserError("run not found")
    rows = []
    for s in r.get("symbols", []):
        x = (r.get("results") or {}).get(s)
        if not x:
            rows.append(dict(symbol=s, state="waiting"))
            continue
        if not x.get("ok"):
            rows.append(dict(symbol=s, state="failed", why=x.get("why")))
            continue
        snap = N.load_snapshot(s, x["snap"]) or {}
        ml = next((v for v in ((snap.get("market_lab") or {}).get("verdicts") or []) if v.get("move") == "cc" and float(v.get("size", 0)) == 1.0), {})
        ev = ((snap.get("ta") or {}).get("evidence") or {})
        rs = snap.get("reasons") or {}
        rows.append(dict(symbol=s, state="done", snap=x["snap"], as_of=snap.get("as_of"), name=snap.get("name"), sector=snap.get("sector"),
                         close=(snap.get("price") or {}).get("close"), change=(snap.get("price") or {}).get("change"),
                         chance=ml.get("chance"), usual=ml.get("usual"), lean=ml.get("lean"), confidence=ml.get("confidence"),
                         ta=ev.get("dir"), ta_net=ev.get("net"), pros=len(rs.get("pro", [])), cons=len(rs.get("con", []))))
    return dict(run={k: v for k, v in r.items() if k != "results"}, rows=rows)
