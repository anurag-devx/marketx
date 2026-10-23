"""NSE corporate announcements: download, categorise, keep a running history.

Sources (both public, no keys):
1. NSE's official RSS feed of the latest announcements (primary, structure confirmed Oct 2026):
       https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml
   item: title = company name, description = "SYMBOL  : text", pubDate = "18-Oct-2026 07:00:00", link = PDF (sometimes empty).
   It only holds the latest few dozen items, so every fetch is appended to data/announcements.parquet.
2. NSE's website API (best effort, for a few days of history):
       https://www.nseindia.com/api/corporate-announcements?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY
   NSE does not document its fields and needs a browser-like session (cookies from the home page). Records are
   read field-tolerantly (symbol/company/subject/text/date/attachment); if the shape is not recognised the fetch
   reports an error instead of guessing.

Categories are keyword rules on the subject and text (rough, shown as such). They describe what was filed,
not whether it is good or bad news."""
import hashlib, html, io, json, re, time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import quote
import pandas as pd
from .config import DATA
from . import atomic

STORE = DATA / "announcements.parquet"
LOG = DATA / "announcements_fetch.json"
RSS_URL = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"
API_URL = "https://www.nseindia.com/api/corporate-announcements?index=equities&from_date={a}&to_date={b}"
HOME = "https://www.nseindia.com"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/"}
COLS = ["id", "published", "symbol", "company", "subject", "text", "category", "link", "source", "fetched"]

# first match wins; order matters (e.g. "outcome of board meeting ... financial results" is Results)
CATEGORIES = [
    ("Results", r"financial result|quarterly result|un-?audited|audited result|results for the (quarter|year)|earnings"),
    ("Dividend", r"dividend"),
    ("Bonus / split", r"\bbonus\b|sub-?division|split of|stock split"),
    ("Buyback", r"buy-?back"),
    ("Shareholding / insider", r"\bsast\b|substantial acquisition|takeovers\) regulations|regulation 29|regulation 31|reg\.? 29|pledge|encumbr|insider|shareholding pattern|trading window"),
    ("Legal / regulatory", r"penalty|litigation|\bcourt\b|tribunal|\bnclt\b|sebi order|show cause|demand notice|\bgst\b|income tax|\braid\b|search and seizure|\bfine\b"),
    ("Order / contract", r"\border\b|contract|letter of (award|intent)|\bloa\b|\bloi\b|bagged|awarded|work order|purchase order"),
    ("Acquisition / merger", r"acqui|merger|amalgamat|demerger|scheme of arrangement|takeover|open offer|joint venture|\bstake\b"),
    ("Employee stock options", r"\besop|\besos|employee stock|stock option"),
    ("Fund raising", r"\bqip\b|qualified institution|preferential|rights issue|\bncd|debenture|fund rais|raising of funds|warrants|private placement|allotment"),
    ("Credit rating", r"credit rating|\brating\b"),
    ("Board meeting", r"board meeting|meeting of the board"),
    ("Management change", r"resign|appoint|cessation|re-?designat|chief executive|managing director|\bceo\b|\bcfo\b|key managerial|director"),
    ("Investor meet / call", r"analyst|investor|earnings call|conference call|con\.? call|transcript|audio recording|presentation"),
    ("Press release", r"press release|media release"),
    ("Routine compliance", r"newspaper|publication|certificate|compliance|reg\.? 74|regulation 74|loss of share|duplicate share|closure of trading|record date|annual report|\bagm\b|e-?voting|postal ballot|book closure|net asset value"),
]
_CAT = [(n, re.compile(p, re.I)) for n, p in CATEGORIES]


def categorise(subject, text=""):
    s = f"{subject or ''} {text or ''}"
    for name, pat in _CAT:
        if pat.search(s):
            return name
    return "Other"


def _clean(t):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(t or ""))).strip()


def _id(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def parse_rss(content):
    """RSS bytes -> DataFrame (COLS). Items whose description lacks 'SYMBOL : text' keep symbol empty."""
    root = ET.fromstring(content)
    rows = []
    for it in root.iter("item"):
        title = _clean(it.findtext("title"))
        desc = _clean(it.findtext("description"))
        m = re.match(r"^([A-Z0-9&\-\.]{1,25})\s*:\s*(.*)$", desc)
        sym, text = (m.group(1), m.group(2)) if m else ("", desc)
        pub = (it.findtext("pubDate") or "").strip()
        ts = pd.to_datetime(pub, format="%d-%b-%Y %H:%M:%S", errors="coerce")
        if pd.isna(ts):
            ts = pd.to_datetime(pub, errors="coerce")
        link = (it.findtext("link") or "").strip()
        rows.append(dict(id=_id("rss", sym, title, pub, text[:120]), published=ts, symbol=sym, company=title,
                         subject="", text=text, link=link, source="NSE RSS"))
    return _finish(pd.DataFrame(rows))


def _pick(rec, *names):
    low = {k.lower(): v for k, v in rec.items()}
    for n in names:
        v = low.get(n.lower())
        if v not in (None, "", "-"):
            return v
    return ""


def parse_api(payload):
    """NSE website JSON -> DataFrame (COLS). Field names are not documented: common ones are tried; raises
    ValueError when the response does not look like a list of announcements."""
    recs = payload
    if isinstance(payload, dict):
        recs = next((payload[k] for k in ("data", "Data", "rows", "results", "Table") if isinstance(payload.get(k), list)), None)
    if not isinstance(recs, list):
        raise ValueError("unexpected response shape from NSE (not a list of announcements)")
    rows = []
    for r in recs:
        if not isinstance(r, dict):
            continue
        sym = str(_pick(r, "symbol", "sm_symbol")).strip()
        subj = _clean(str(_pick(r, "desc", "subject", "sub", "category")))
        text = _clean(str(_pick(r, "attchmntText", "details", "text", "body")))
        when = _pick(r, "an_dt", "sort_date", "dt", "date", "exchdisstime", "broadcastdatetime")
        ts = pd.to_datetime(when, dayfirst=True, errors="coerce") if when else pd.NaT
        link = str(_pick(r, "attchmntFile", "attachment", "pdf", "url", "link"))
        if not (sym or text or subj):
            continue
        rows.append(dict(id=_id("api", sym, subj, when, text[:120]), published=ts, symbol=sym, company=_clean(str(_pick(r, "sm_name", "company", "companyname"))),
                         subject=subj, text=text, link=link if link.startswith("http") else "", source="NSE website"))
    if recs and not rows:
        raise ValueError("NSE response had records but none with a symbol, subject or text")
    return _finish(pd.DataFrame(rows))


def _finish(d):
    if d.empty:
        return pd.DataFrame(columns=COLS)
    d["category"] = [categorise(s, t) for s, t in zip(d.subject, d.text)]
    d["fetched"] = pd.Timestamp.now().floor("s")
    d["published"] = pd.to_datetime(d.published)
    return d[COLS]


def fetch_rss(timeout=20):
    import requests
    r = requests.get(RSS_URL, headers=UA, timeout=timeout)
    r.raise_for_status()
    return parse_rss(r.content)


def fetch_api(days=7, timeout=25):
    import requests
    s = requests.Session()
    s.headers.update(UA)
    s.get(HOME, timeout=timeout)                         # sets the cookies NSE's API expects
    b = datetime.now()
    a = b - timedelta(days=days)
    r = s.get(API_URL.format(a=a.strftime("%d-%m-%Y"), b=b.strftime("%d-%m-%Y")), timeout=timeout)
    r.raise_for_status()
    return parse_api(r.json())


BACKFILL_STATE = DATA / "announcements_backfill.json"


class NSESession:
    """One browser-like session for NSE's website API: cookies from the home page, refreshed when they expire,
    requests spaced out (NSE blocks fast clients), and the same robots.txt check as the news sources."""
    def __init__(self, gap=1.5, timeout=25, sleep=time.sleep):
        import requests
        self.requests_mod = requests
        self.gap, self.timeout, self._sleep = gap, timeout, sleep
        self.s, self.last, self.count = None, 0.0, 0

    def _open(self):
        self.s = self.requests_mod.Session()
        self.s.headers.update(UA)
        self.s.get(HOME, timeout=self.timeout)

    def get_json(self, url):
        for attempt in range(3):
            if self.s is None:
                self._open()
            wait = self.gap - (time.monotonic() - self.last)
            if wait > 0:
                self._sleep(wait)
            self.last = time.monotonic()
            self.count += 1
            r = self.s.get(url, timeout=self.timeout)
            if r.status_code in (401, 403):            # cookies expired or blocked for a moment: new session, wait longer
                self.s = None
                self._sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"NSE refused {url.split('?')[0]} three times (status {r.status_code})")


def backfill(symbols, start="2016-01-01", end=None, max_requests=3000, log=print, session=None):
    """History of announcements per stock from NSE's website API, newest years first, resumable.
    One request per stock per calendar year. Unknown response shapes are reported, never guessed."""
    end = pd.Timestamp(end or pd.Timestamp.now().normalize())
    start = pd.Timestamp(start)
    state = json.loads(BACKFILL_STATE.read_text()) if BACKFILL_STATE.exists() else {"done": [], "errors": {}}
    done = set(state["done"])
    sess = session or NSESession()
    # calendar-year windows, newest first; the key is the year so a run on another day resumes, not restarts
    windows = [(max(start, pd.Timestamp(y, 1, 1)), min(end, pd.Timestamp(y, 12, 31))) for y in range(end.year, start.year - 1, -1)]
    added, fails = 0, 0
    batch = []
    for a, b in windows:                       # newest year for every stock first, so a partial run is still useful
        for sym in symbols:
            key = f"{sym}|{a.year}"
            if key in done:
                continue
            if sess.count >= max_requests:
                log(f"Stopping at the request limit ({max_requests}); run again to continue.")
                break
            url = (API_URL.format(a=a.strftime("%d-%m-%Y"), b=b.strftime("%d-%m-%Y")) + "&symbol=" + quote(sym))
            try:
                got = parse_api(sess.get_json(url))
                got = got[(got.symbol == "") | (got.symbol == sym)]
                batch.append(got)
                done.add(key)
                fails = 0
            except ValueError as e:                # response shape not recognised: stop, don't guess
                state["errors"][key] = str(e)
                log(f"{sym} {a.date()}..{b.date()}: {e}. Stopping; the data source changed its format — keep this message for troubleshooting.")
                BACKFILL_STATE.write_text(json.dumps({"done": sorted(done), "errors": state["errors"]}))
                return added
            except Exception as e:
                state["errors"][key] = f"{type(e).__name__}: {str(e)[:120]}"
                log(f"{sym} {a.date()}..{b.date()}: {type(e).__name__}")
                fails += 1
                if fails >= 20:
                    log("20 failures in a row: NSE is not answering. Stopping; try again later.")
                    break
            if len(batch) >= 50:
                n_add, total = _merge_save(pd.concat(batch, ignore_index=True))
                added += n_add
                batch = []
                BACKFILL_STATE.write_text(json.dumps({"done": sorted(done), "errors": state["errors"]}))
                log(f"  {a.year}: {len(done)} stock-periods done, {total:,} announcements stored")
        else:
            continue
        break
    total = len(load())
    if batch:
        n_add, total = _merge_save(pd.concat(batch, ignore_index=True))
        added += n_add
    BACKFILL_STATE.write_text(json.dumps({"done": sorted(done), "errors": state["errors"]}))
    log(f"Announcements backfill: {added:,} new, {total:,} stored, {sess.count} requests.")
    return added


def merge(old, new):
    d = pd.concat([old, new], ignore_index=True) if old is not None and len(old) else new
    if d.empty:
        return d
    # the same filing can arrive from both sources: keep one per (symbol, minute, first words of text)
    d["_k"] = d.symbol.fillna("") + "|" + pd.to_datetime(d.published).dt.floor("min").astype(str) + "|" + \
        d.text.fillna("").str.lower().str.replace(r"[^a-z0-9]", "", regex=True).str[:60]
    d = d.sort_values(["source"]).drop_duplicates("id").drop_duplicates("_k").drop(columns="_k")
    return d.sort_values("published", ascending=False).reset_index(drop=True)


def load():
    if not STORE.exists():
        return pd.DataFrame(columns=COLS)
    d = pd.read_parquet(STORE)
    d["published"] = pd.to_datetime(d.published)
    return d


def _merge_save(new):
    """Re-reads the store under a lock, adds `new`, writes it. Returns (added, total). Safe with parallel jobs."""
    from .filelock import locked
    with locked("announcements"):
        old = load()
        d = merge(old, new)
        if len(d):
            atomic.to_parquet(d, STORE)
        return len(d) - len(old), len(d)


def update(use_api=True, api_days=7):
    """Fetches both sources, merges into the store. Returns a summary dict (errors listed, never hidden)."""
    got, errors = [], []
    try:
        got.append(fetch_rss())
    except Exception as e:
        errors.append(f"RSS: {type(e).__name__}: {e}")
    if use_api:
        try:
            got.append(fetch_api(api_days))
        except Exception as e:
            errors.append(f"NSE website API (best effort): {type(e).__name__}: {str(e)[:160]}")
    new = pd.concat(got, ignore_index=True) if got else pd.DataFrame(columns=COLS)
    added, total = _merge_save(new)
    info = dict(when=time.strftime("%Y-%m-%d %H:%M:%S"), fetched=int(len(new)), added=int(added), total=int(total),
                sources={x.source.iat[0]: int(len(x)) for x in got if len(x)}, errors=errors)
    LOG.write_text(json.dumps(info, indent=2))
    return info


def last_fetch():
    try:
        return json.loads(LOG.read_text()) if LOG.exists() else None
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        args = dict(a.split("=", 1) for a in sys.argv[2:] if "=" in a)
        uni = pd.read_csv(DATA / "universe.csv")
        syms = args.get("symbols", "").split(",") if args.get("symbols") else list(uni.Symbol)
        backfill(syms, start=args.get("start", "2016-01-01"), max_requests=int(args.get("max", 3000)))
        raise SystemExit(0)
    i = update()
    print(f"Announcements: {i['fetched']} fetched ({i['sources']}), {i['added']} new, {i['total']} stored.")
    for e in i["errors"]:
        print("  ", e)
