"""API: Live terminal. Start / stop the Angel live feed (market hours) or a replay of a saved session, read the live state
(strip, breadth, watch list, Market Lab picks, paper takes, new alerts), a stock's live 5-min chart, add a stock to watch,
and quick notes (appended to the stock's Stock Desk notes, with the time and price). No orders anywhere."""
import html, re
from web.api import route, UserError

SYM = re.compile(r"^[A-Z0-9&\-]{1,20}$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _sym(x):
    s = str(x or "").upper().strip()
    if not SYM.match(s):
        raise UserError("pick a stock symbol")
    return s


def _feed():
    from core import live
    return live.FEED


@route("GET", "live/state")
def state(p):
    f = _feed()
    try:
        since = int(p.get("since") or 0)
    except ValueError:
        since = 0
    out = dict(status=f.status(), plan_day=getattr(f, "plan_day", None))
    if f.book:
        out.update(f.book.view(since))
    return out


@route("POST", "live/start")
def start(_, body):
    b = body or {}
    day = b.get("day") or None
    if day and not DAY.match(str(day)):
        raise UserError("day must be YYYY-MM-DD")
    try:
        speed = float(b.get("speed") or 4)
    except ValueError:
        speed = 4.0
    try:
        return _feed().start(replay=bool(b.get("replay")), day=day, speed=min(max(speed, 0.2), 200))
    except RuntimeError as e:
        raise UserError(str(e))


@route("POST", "live/stop")
def stop(_, body):
    return _feed().stop()


@route("GET", "live/chart")
def chart(p):
    f = _feed()
    if not f.book:
        raise UserError("start the feed first")
    ix = str(p.get("index") or "")
    if ix:
        if not re.fullmatch(r"[a-z0-9_]{2,30}", ix) or ix not in set(f.book.indices.values()):
            raise UserError("unknown index")
        return f.book.chart(ix)
    sym = _sym(p.get("symbol"))
    return f.book.chart(sym, extra=f.backfill(sym))


@route("GET", "live/sector")
def sector(p):
    f = _feed()
    if not f.book:
        raise UserError("start the feed first")
    return dict(items=f.book.sector_rows(str(p.get("name") or "")[:80]))


@route("POST", "live/watch")
def watch(_, body):
    f, sym = _feed(), _sym((body or {}).get("symbol"))
    if f.book and sym not in f.book.sym_tok:
        raise UserError(f"{sym} is not in the Nifty 500 list")
    f.add(sym, "added")
    if f.book:
        import threading
        threading.Thread(target=f.backfill, args=(sym,), daemon=True).start()
    return dict(ok=True)


@route("POST", "live/note")
def note(_, body):
    """Quick note: appended to the stock's Stock Desk notes as '<time> · <price> · text'."""
    from desk import notes as N
    from core import live
    b = body or {}
    sym, text = _sym(b.get("symbol")), str(b.get("text") or "").strip()[:2000]
    if not text:
        raise UserError("write a note first")
    f = _feed()
    q = (f.book.q.get(sym) if f.book else None) or {}
    t = live.now()
    stamp = f"{t:%d %b %H:%M}" + (f" · {q['ltp']:.2f}" if q.get("ltp") else "")
    n = N.load(sym)
    line = f"<p><b>{html.escape(stamp)}</b> · {html.escape(text)}</p>"
    N.save_note(sym, (n.get("html") or "") + line, n.get("tags"))
    return dict(ok=True, stamp=stamp)
