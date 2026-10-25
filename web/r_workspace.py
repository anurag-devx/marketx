"""API: Chart workspace (Strategy Lab). Saved charts with your drawings, notes, tags and status, plus a frozen snapshot of
what MarketX said when you saved (Market Lab verdict, trade plan, candle patterns), so you can review it later against
what happened. Stored only on this Mac in data/workspace/charts.json."""
import json, threading, uuid
import pandas as pd
from core import config as C, atomic
from web.api import route, UserError, prices

FILE = C.DATA / "workspace" / "charts.json"
_lock = threading.Lock()
STATUS = ("idea", "watching", "taken", "closed")
TOOLS = ("hline", "trend", "rect", "arrow", "text", "entry", "exit")


def _load():
    try:
        return json.loads(FILE.read_text())
    except (OSError, ValueError):
        return []


def _save(items):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(FILE, json.dumps(items, indent=1, default=str))


def _snapshot(sym):
    """What MarketX said about the stock at save time (whatever exists; nothing is required)."""
    snap = {}
    try:
        from marketlab import radar as R
        rd = R.read("radar")
        r = rd[rd.symbol == sym]
        if len(r):
            r = r.iloc[0]
            snap["market_lab"] = dict(day=R.latest_day(), **{f"{m}_1": dict(chance=float(r[f"ch_{m}_1.0"]), usual=float(r[f"us_{m}_1.0"]), up_share=float(r[f"up_{m}_1.0"]))
                                                              for m in ("cc", "oc", "gp")})
    except Exception:
        pass
    try:
        from tradeplan import plan as TP
        p = (TP.load() or {}).get("plans", {}).get(sym)
        if p:
            snap["plan"] = {m: dict(lean=p[m]["lean"], legs=[{k: x.get(k) for k in ("leg", "side", "price")} for x in p[m]["legs"]], tradeable=p[m]["tradeable"])
                            for m in ("intraday", "swing") if m in p}
            snap["patterns"] = p.get("patterns", [])
    except Exception:
        pass
    return snap


def _clean_drawings(ds):
    out = []
    for d in (ds or [])[:300]:
        if not isinstance(d, dict) or d.get("type") not in TOOLS:
            continue
        pts = [dict(t=str(p.get("t", ""))[:19], p=float(p["p"])) for p in (d.get("points") or [])[:2] if isinstance(p, dict) and p.get("p") is not None]
        if not pts:
            continue
        out.append(dict(id=str(d.get("id") or uuid.uuid4().hex[:8])[:12], type=d["type"], points=pts,
                        color=str(d.get("color") or "#4f46e5")[:20], text=str(d.get("text") or "")[:120]))
    return out


@route("GET", "workspace/list")
def ws_list(p):
    items = _load()
    q = str(p.get("q", "")).lower()
    if q:
        items = [x for x in items if q in (x["symbol"] + " " + x.get("title", "") + " " + " ".join(x.get("tags", []))).lower()]
    if p.get("status"):
        items = [x for x in items if x.get("status") == p["status"]]
    rows = [dict(id=x["id"], symbol=x["symbol"], title=x.get("title"), timeframe=x.get("timeframe"), saved_at=x.get("saved_at"), status=x.get("status"),
                 tags=x.get("tags", []), drawings=len(x.get("drawings", [])), has_notes=bool(x.get("notes")),
                 lean=((x.get("snapshot") or {}).get("plan") or {}).get("intraday", {}).get("lean")) for x in sorted(items, key=lambda x: x.get("saved_at", ""), reverse=True)]
    tags = sorted({t for x in _load() for t in x.get("tags", [])})
    return dict(items=rows, tags=tags, statuses=STATUS)


def _bars(sym, timeframe, start, saved_day):
    if timeframe == "5min":
        from core import intraday as I
        d = I.load(sym)
        if d.empty:
            return None
        d = d[d.ts >= pd.Timestamp(start)]
        return dict(t=d.ts.dt.strftime("%Y-%m-%dT%H:%M").tolist(), o=d.open.round(2).tolist(), h=d.high.round(2).tolist(), l=d.low.round(2).tolist(),
                    c=d.close.round(2).tolist(), v=d.volume.astype(float).tolist(), intraday=True)
    px = prices()
    g = px[(px.symbol == sym) & (px.date >= pd.Timestamp(start))].sort_values("date") if px is not None else pd.DataFrame()
    if g.empty:
        return None
    return dict(t=g.date.dt.strftime("%Y-%m-%d").tolist(), o=g.open.round(2).tolist(), h=g.high.round(2).tolist(), l=g.low.round(2).tolist(),
                c=g.close.round(2).tolist(), v=g.volume.astype(float).tolist(), intraday=False)


@route("GET", "workspace/chart")
def ws_chart(p):
    x = next((i for i in _load() if i["id"] == p.get("id")), None)
    if not x:
        raise UserError("saved chart not found")
    bars = _bars(x["symbol"], x.get("timeframe", "daily"), x["start"], x.get("saved_day"))
    review = None
    if bars and x.get("saved_day"):
        t = bars["t"]
        k = max((i for i, d in enumerate(t) if d[:10] <= x["saved_day"]), default=None)
        if k is not None and k < len(t) - 1:
            c0, c1 = bars["c"][k], bars["c"][-1]
            review = dict(saved_close=c0, latest_close=c1, change_pct=round(100 * (c1 / c0 - 1), 2), sessions=len(t) - 1 - k,
                          next_cc_pct=round(100 * (bars["c"][k + 1] / c0 - 1), 2), next_oc_pct=round(100 * (bars["c"][k + 1] / bars["o"][k + 1] - 1), 2), saved_index=k)
        elif k is not None:
            review = dict(saved_index=k, sessions=0)
    return dict(item=x, bars=bars, review=review, statuses=STATUS)


@route("POST", "workspace/save")
def ws_save(_, body):
    b = body or {}
    items = _load()
    with _lock:
        if b.get("id"):
            x = next((i for i in items if i["id"] == b["id"]), None)
            if not x:
                raise UserError("saved chart not found")
            for k in ("title", "notes"):
                if k in b:
                    x[k] = str(b[k] or "")[: 200 if k == "title" else 20000]
            if "tags" in b:
                x["tags"] = sorted({str(t).strip().lower()[:30] for t in (b["tags"] or []) if str(t).strip()})[:20]
            if "status" in b and b["status"] in STATUS:
                x["status"] = b["status"]
            if "drawings" in b:
                x["drawings"] = _clean_drawings(b["drawings"])
            if "trade" in b and isinstance(b["trade"], dict):
                x["trade"] = {k: b["trade"].get(k) for k in ("side", "entry", "exit", "qty", "entry_date", "exit_date")}
            x["updated_at"] = str(pd.Timestamp.now().floor("s"))
        else:
            sym = str(b.get("symbol", "")).upper().strip()
            if not sym:
                raise UserError("symbol missing")
            tf = "5min" if b.get("timeframe") == "5min" else "daily"
            px = prices()
            last = px[px.symbol == sym].date.max() if px is not None and (px.symbol == sym).any() else None
            if last is None:
                raise UserError(f"no prices for {sym}")
            days = max(5, min(2000, int(b.get("days", 120))))
            start = (pd.Timestamp(last) - pd.Timedelta(days=days * (1.45 if tf == "daily" else 1))).date() if tf == "daily" else (pd.Timestamp(last) - pd.Timedelta(days=days)).date()
            x = dict(id=uuid.uuid4().hex[:10], symbol=sym, title=str(b.get("title") or f"{sym} · {pd.Timestamp(last):%d %b %Y}")[:200], timeframe=tf,
                     start=str(start), saved_day=str(pd.Timestamp(last).date()), saved_at=str(pd.Timestamp.now().floor("s")), status="idea",
                     tags=sorted({str(t).strip().lower() for t in (b.get("tags") or []) if str(t).strip()}), notes="", drawings=_clean_drawings(b.get("drawings")),
                     source=str(b.get("source") or "")[:40], snapshot=_snapshot(sym), trade=None)
            items.append(x)
        _save(items)
    return dict(id=x["id"], saved=True)


@route("POST", "workspace/delete")
def ws_delete(_, body):
    with _lock:
        items = _load()
        keep = [i for i in items if i["id"] != (body or {}).get("id")]
        if len(keep) == len(items):
            raise UserError("saved chart not found")
        _save(keep)
    return dict(deleted=True)
