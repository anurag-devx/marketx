"""Stock Desk notes: plain rich text per stock (versioned), pinned evidence, a decision log with review prompts, tags,
search across stocks, and the Desk watchlist. Stored only on this Mac in data/desk/<SYMBOL>/notes.json and data/desk/watchlist.json."""
import html, json, re, threading, uuid
from html.parser import HTMLParser
import pandas as pd
from core import atomic
from .analyse import DIR

_lock = threading.Lock()
KEEP_VERSIONS = 50
ALLOWED = {"p", "div", "br", "b", "strong", "i", "em", "u", "s", "h2", "h3", "ul", "ol", "li", "blockquote", "span", "a", "code", "pre", "hr"}
ALLOWED_ATTR = {"span": {"class", "data-pin"}, "a": {"href"}, "li": {"class"}, "div": {"class"}}
DECISIONS = ("watching", "entered", "exited", "skipped", "note")


class _Clean(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "iframe", "object"):
            self.skip += 1
            return
        if tag not in ALLOWED or self.skip:
            return
        keep = []
        for k, v in attrs:
            if k in ALLOWED_ATTR.get(tag, set()) and v is not None:
                if k == "href" and not re.match(r"^(https?://|#/)", v):
                    continue
                keep.append(f' {k}="{html.escape(v, quote=True)}"')
        self.out.append(f"<{tag}{''.join(keep)}>")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe", "object"):
            self.skip = max(0, self.skip - 1)
            return
        if tag in ALLOWED and not self.skip and tag not in ("br", "hr"):
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))


def clean(h):
    p = _Clean()
    p.feed(str(h or "")[:200_000])
    return "".join(p.out)


def text_of(h):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h or ""))).strip()


def _file(sym):
    return DIR / sym / "notes.json"


def load(sym):
    try:
        return json.loads(_file(sym).read_text())
    except (OSError, ValueError):
        return dict(symbol=sym, html="", tags=[], versions=[], decisions=[], updated=None)


def _save(sym, n):
    _file(sym).parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(_file(sym), json.dumps(n, indent=1, default=str))


def save_note(sym, html_, tags=None):
    with _lock:
        n = load(sym)
        new = clean(html_)
        now = str(pd.Timestamp.now().floor("s"))
        if new != n.get("html"):
            if n.get("html"):
                n.setdefault("versions", []).append(dict(at=n.get("updated") or now, html=n["html"]))
                n["versions"] = n["versions"][-KEEP_VERSIONS:]
            n["html"], n["updated"] = new, now
        if tags is not None:
            n["tags"] = sorted({str(t).strip().lower()[:30] for t in tags if str(t).strip()})[:20]
        _save(sym, n)
        return n


def add_decision(sym, kind, note="", horizon_days=None, snapshot=None):
    """kind: watching / entered / exited / skipped / note. Keeps what the Desk said at that moment (from the snapshot)."""
    if kind not in DECISIONS:
        raise ValueError("unknown decision")
    with _lock:
        n = load(sym)
        s = snapshot or {}
        mlv = next((x for x in ((s.get("market_lab") or {}).get("verdicts") or []) if x["move"] == "cc" and x["size"] == 1.0), None)
        plans = ((s.get("ta") or {}).get("plans") or {})
        d = dict(id=uuid.uuid4().hex[:8], at=str(pd.Timestamp.now().floor("s")), kind=kind, note=str(note or "")[:2000],
                 horizon_days=int(horizon_days) if horizon_days else None, as_of=s.get("as_of"), price=(s.get("price") or {}).get("close"),
                 said=dict(market_lab=mlv, ta=((s.get("ta") or {}).get("evidence") or {}).get("dir"),
                           plan={m: dict(lean=p.get("lean"), legs=[{k: x.get(k) for k in ("leg", "side", "price")} for x in p.get("legs", [])]) for m, p in plans.items() if isinstance(p, dict) and "legs" in p}),
                 review=None)
        n.setdefault("decisions", []).append(d)
        _save(sym, n)
        return d


def outcome(sym, d, px):
    """What happened since a decision: return, best / worst, and whether the swing plan's stop or first target came first."""
    if not d.get("as_of") or not d.get("price"):
        return None
    g = px[(px.symbol == sym) & (px.date > pd.Timestamp(d["as_of"]))].sort_values("date")
    if d.get("horizon_days"):
        g = g.head(int(d["horizon_days"]))
    if g.empty:
        return dict(sessions=0)
    p0 = float(d["price"])
    out = dict(sessions=int(len(g)), last=float(g.close.iat[-1]), ret=float(g.close.iat[-1] / p0 - 1), best=float(g.high.max() / p0 - 1), worst=float(g.low.min() / p0 - 1))
    plan = ((d.get("said") or {}).get("plan") or {}).get("swing")
    if plan and plan.get("legs"):
        side = next((x["side"] for x in plan["legs"] if x.get("side") in ("long", "short")), None)
        lv = {x["leg"]: x["price"] for x in plan["legs"] if x.get("side") == side and x.get("price") is not None}
        first = None
        for r in g.itertuples():
            stop = (r.low <= lv.get("SL", -1e18)) if side == "long" else (r.high >= lv.get("SL", 1e18))
            tgt = (r.high >= lv.get("T1", 1e18)) if side == "long" else (r.low <= lv.get("T1", -1e18))
            if stop or tgt:
                first = "stop" if stop else "target"          # same bar: stop first
                break
        out["plan_side"], out["plan_first"] = side, first
    return out


def due(sym, px, today=None):
    """Decisions whose horizon has passed and that have no lesson yet."""
    n = load(sym)
    out = []
    for d in n.get("decisions", []):
        if d.get("review") or d["kind"] not in ("watching", "entered") or not d.get("horizon_days"):
            continue
        o = outcome(sym, d, px)
        if o and o.get("sessions", 0) >= d["horizon_days"]:
            out.append(dict(d, outcome=o))
    return out


def add_review(sym, decision_id, lesson, outcome_=None):
    with _lock:
        n = load(sym)
        for d in n.get("decisions", []):
            if d["id"] == decision_id:
                d["review"] = dict(at=str(pd.Timestamp.now().floor("s")), lesson=str(lesson or "")[:2000], outcome=outcome_)
                _save(sym, n)
                return d
    raise KeyError("decision not found")


def search(q):
    q = str(q or "").strip().lower()
    out = []
    if not DIR.exists():
        return out
    for f in DIR.glob("*/notes.json"):
        try:
            n = json.loads(f.read_text())
        except ValueError:
            continue
        txt = text_of(n.get("html", ""))
        hay = (txt + " " + " ".join(n.get("tags", [])) + " " + " ".join(d.get("note", "") for d in n.get("decisions", []))).lower()
        if not q or q in hay or q == f.parent.name.lower():
            i = txt.lower().find(q) if q else 0
            out.append(dict(symbol=f.parent.name, tags=n.get("tags", []), updated=n.get("updated"), decisions=len(n.get("decisions", [])),
                            snippet=txt[max(0, i - 60): i + 140] if txt else ""))
    return sorted(out, key=lambda x: x.get("updated") or "", reverse=True)


def watchlist():
    try:
        return json.loads((DIR / "watchlist.json").read_text())
    except (OSError, ValueError):
        return []


def set_watchlist(items):
    DIR.mkdir(parents=True, exist_ok=True)
    w = []
    for s in items:
        s = str(s).upper().strip()
        if re.fullmatch(r"[A-Z0-9&\-]{1,20}", s) and s not in w:
            w.append(s)
    atomic.write_text(DIR / "watchlist.json", json.dumps(w[:100]))
    return w


def snapshots(sym):
    d = DIR / sym / "snaps"
    return sorted((p.name for p in d.glob("*.json")), reverse=True) if d.exists() else []


def load_snapshot(sym, name=None):
    p = (DIR / sym / "snaps" / name) if name else (DIR / sym / "latest.json")
    if name and (not re.fullmatch(r"[\d\-_]+\.json", name) or not p.exists()):
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None
