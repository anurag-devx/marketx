"""API: Market Lab → Swing picks (monthly buy-only book; separate from the intraday work). Data: data/swing/ (marketlab/swingbook.py)."""
import json
import pandas as pd
from core import config as C
from web.api import route, UserError

DIR = C.DATA / "swing"


def _j(p, default=None):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


@route("GET", "swing")
def swing(_):
    from marketlab import swingbook as S
    led = S.ledger()
    tonight = _j(DIR / "tonight.json")
    if not led or not tonight:
        return dict(ready=False, why="No swing picks yet: run Swing picks (rebuild), or Fetch + run all tonight.")
    cur, prev = led[-1], (led[-2] if len(led) > 1 else None)
    so_far = (_j(DIR / "stocks.json", {}) or {}).get(cur["day"], {})
    g = pd.read_parquet(DIR / "graded.parquet") if (DIR / "graded.parquet").exists() else pd.DataFrame()
    row = g[g.day == cur["day"]].iloc[0].to_dict() if len(g) and (g.day == cur["day"]).any() else {}
    held = [dict(p, ret=so_far.get(p["symbol"])) for p in cur["picks"]]
    pn = {p["symbol"] for p in prev["picks"]} if prev else set()
    cn = {p["symbol"] for p in cur["picks"]}
    rank = tonight["rank"]
    pos = {r["symbol"]: i + 1 for i, r in enumerate(rank)}
    for h in held:
        h["rank_tonight"] = pos.get(h["symbol"])
    preview = [r for r in rank[:S.TOPN]]
    clean = lambda v: None if v is None or (isinstance(v, float) and v != v) else v
    return dict(ready=True, tonight=tonight, book=dict(day=cur["day"], kind=cur["kind"], entry=row.get("entry"), exit=row.get("exit"),
                done=bool(row.get("done")), gross=clean(row.get("gross")), net=clean(row.get("net")), nifty=clean(row.get("nifty")), held=held),
                bought=sorted(cn - pn), sold=sorted(pn - cn),
                preview=preview, would_buy=[r["symbol"] for r in preview if r["symbol"] not in cn],
                would_sell=sorted(cn - {r["symbol"] for r in preview}), signs=cur.get("signs", {}), hold=S.HOLD, topn=S.TOPN, summary=S.summary())


@route("GET", "swing/history")
def history(_):
    from marketlab import swingbook as S
    f = DIR / "graded.parquet"
    if not f.exists():
        return dict(ready=False, why="Nothing graded yet.")
    g = pd.read_parquet(f)
    d = g[g.done & g.net.notna()].copy()
    d["you_eq"] = (1 + d.net).cumprod()
    d["nifty_eq"] = (1 + d.nifty.fillna(0)).cumprod()
    rows = [{k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()} for r in g.sort_values("day", ascending=False).to_dict("records")]
    return dict(ready=True, summary=S.summary(), rows=rows,
                curve=dict(x=d.exit.tolist(), you=(100 * d.you_eq).round(2).tolist(), nifty=(100 * d.nifty_eq).round(2).tolist()))


@route("GET", "swing/rebalance")
def rebalance(p):
    day = str(p.get("day") or "")
    rec = _j(DIR / "ledger" / f"{day}.json")
    if not rec:
        raise UserError("no rebalance saved on that day")
    rets = (_j(DIR / "stocks.json", {}) or {}).get(day, {})
    rec["picks"] = [dict(x, ret=rets.get(x["symbol"])) for x in rec["picks"]]
    return rec
