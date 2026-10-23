"""Stock lists and selections: NSE indices, sectors, liquidity, your holdings, the screener shortlist, your exposure list
and custom lists you save. Used by the nightly forecast batch (Market Debate Lab) and anywhere a group of stocks is needed.

NSE index members come from NSE's public index files (nsearchives.nseindia.com/content/indices/<file>.csv — the same
source the Nifty 500 list already uses). They are refreshed when older than REFRESH_DAYS; if NSE can't be reached the
last saved copy is used, and if there is none that index is reported as unavailable (never guessed).

A SELECTION is a saved recipe, so it stays the same experiment even as members change:
  {"name": "Nifty 50", "include": [{"type": "index", "name": "NIFTY 50"}], "exclude": []}
Rule types: index, sector, top_liquid (n), holdings, screener, exposure, custom (name), symbols (list).
Resolved members are always limited to stocks with price data."""
import io, json, time
import pandas as pd
from . import config as C, atomic

DIR = C.DATA / "lists"
CUSTOM = DIR / "custom.json"
REFRESH_DAYS = 30
BASE_URL = "https://nsearchives.nseindia.com/content/indices/{f}"
INDICES = {
    "NIFTY 50": "ind_nifty50list.csv", "NIFTY NEXT 50": "ind_niftynext50list.csv", "NIFTY 100": "ind_nifty100list.csv",
    "NIFTY BANK": "ind_niftybanklist.csv", "NIFTY FINANCIAL SERVICES": "ind_niftyfinancelist.csv", "NIFTY PRIVATE BANK": "ind_nifty_privatebanklist.csv",
    "NIFTY PSU BANK": "ind_niftypsubanklist.csv", "NIFTY IT": "ind_niftyitlist.csv", "NIFTY PHARMA": "ind_niftypharmalist.csv",
    "NIFTY AUTO": "ind_niftyautolist.csv", "NIFTY FMCG": "ind_niftyfmcglist.csv", "NIFTY METAL": "ind_niftymetallist.csv",
    "NIFTY REALTY": "ind_niftyrealtylist.csv", "NIFTY ENERGY": "ind_niftyenergylist.csv", "NIFTY MEDIA": "ind_niftymedialist.csv",
    "NIFTY MIDCAP 100": "ind_niftymidcap100list.csv", "NIFTY SMALLCAP 100": "ind_niftysmallcap100list.csv",
}
RULE_LABEL = {"index": "NSE index", "sector": "Sector", "top_liquid": "Most liquid", "holdings": "Your holdings (positions.csv)",
              "screener": "Latest screener shortlist", "exposure": "Your exposure list", "custom": "Custom list", "symbols": "Hand-picked"}
DEFAULT = {"name": "Nifty 50", "include": [{"type": "index", "name": "NIFTY 50"}], "exclude": []}


def _file(index):
    return DIR / f"index_{INDICES[index].replace('.csv', '')}.csv"


def parse_index_csv(text):
    d = pd.read_csv(io.StringIO(text))
    d.columns = [c.strip() for c in d.columns]
    if "Symbol" not in d:
        raise ValueError("unexpected NSE index file (no Symbol column)")
    cols = [c for c in ("Company Name", "Industry", "Symbol") if c in d]
    return d[cols].assign(Symbol=lambda x: x.Symbol.astype(str).str.strip())


def refresh_index(index, log=print, force=False):
    """Download one index's members. Returns (frame or None, status text)."""
    f = _file(index)
    if f.exists() and not force and time.time() - f.stat().st_mtime < REFRESH_DAYS * 86400:
        return pd.read_csv(f), "saved copy (fresh)"
    try:
        import requests
        r = requests.get(BASE_URL.format(f=INDICES[index]), headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        d = parse_index_csv(r.text)
        atomic.to_csv(d, f)
        return d, f"downloaded {len(d)} members"
    except Exception as e:
        if f.exists():
            log(f"{index}: could not refresh ({type(e).__name__}); using the saved copy from {pd.Timestamp(f.stat().st_mtime, unit='s'):%d %b %Y}")
            return pd.read_csv(f), "saved copy (refresh failed)"
        log(f"{index}: not available ({type(e).__name__}: {str(e)[:80]})")
        return None, f"unavailable ({type(e).__name__})"


def refresh_all(log=print, force=False):
    return {k: refresh_index(k, log, force)[1] for k in INDICES}


def index_members(index):
    f = _file(index)
    return list(pd.read_csv(f).Symbol) if f.exists() else None


def index_status():
    rows = []
    for k in INDICES:
        f = _file(k)
        rows.append(dict(index=k, members=len(pd.read_csv(f)) if f.exists() else None,
                         updated=pd.Timestamp(f.stat().st_mtime, unit="s").strftime("%d %b %Y") if f.exists() else "not downloaded"))
    return pd.DataFrame(rows)


# ---------------- custom lists ----------------
def custom_lists():
    try:
        return json.loads(CUSTOM.read_text()) if CUSTOM.exists() else {}
    except Exception:
        return {}


def save_custom(name, symbols):
    d = custom_lists()
    name = str(name).strip()[:60]
    if not name:
        raise ValueError("give the list a name")
    d[name] = sorted({str(s).strip().upper() for s in symbols if str(s).strip()})
    atomic.write_text(CUSTOM, json.dumps(d, indent=1))


def delete_custom(name):
    d = custom_lists()
    d.pop(name, None)
    atomic.write_text(CUSTOM, json.dumps(d, indent=1))


# ---------------- rules ----------------
def _universe():
    p = C.DATA / "universe.csv"
    return pd.read_csv(p) if p.exists() else pd.DataFrame(columns=["Company Name", "Industry", "Symbol"])


def sectors():
    return sorted(_universe().Industry.dropna().unique())


def _rule(r, px_last=None):
    t = r.get("type")
    if t == "index":
        m = index_members(r["name"])
        return set(m) if m is not None else None
    if t == "sector":
        u = _universe()
        return set(u.Symbol[u.Industry == r["name"]])
    if t == "top_liquid":
        if px_last is None:
            return None
        return set(px_last.nlargest(int(r.get("n", 100)), "turnover").symbol)
    if t == "holdings":
        p = C.POSITIONS_CSV
        if not p.exists():
            return set()
        d = pd.read_csv(p)
        col = next((c for c in d.columns if c.lower() == "symbol"), None)
        return set(d[col].astype(str).str.strip()) if col else set()
    if t == "screener":
        from . import db
        d = db.read("select symbol from screener_results where cast(date as date) = (select max(cast(date as date)) from screener_results)")
        return set(d.symbol) if d is not None else set()
    if t == "exposure":
        from . import search as S
        ex = S.exposures()
        u = _universe()
        tg = set(ex.target.astype(str).str.upper()) if len(ex) else set()
        return {s for s in u.Symbol if s.upper() in tg} | set(u.Symbol[u.Industry.str.upper().isin(tg)])
    if t == "custom":
        return set(custom_lists().get(r["name"], []))
    if t == "symbols":
        return {str(s).strip().upper() for s in r.get("symbols", [])}
    raise ValueError(f"unknown rule type {t}")


def _liquidity():
    p = C.DATA / "prices.parquet"
    if not p.exists():
        return None
    px = pd.read_parquet(p, columns=["date", "symbol", "close", "volume"])
    px["date"] = pd.to_datetime(px.date)
    recent = px[px.date >= px.date.max() - pd.Timedelta(days=45)]
    return recent.assign(turnover=recent.close * recent.volume).groupby("symbol").turnover.mean().reset_index()


def resolve(selection, available=None):
    """(sorted symbols, notes). available: symbols with price data (default: from prices.parquet)."""
    liq = _liquidity() if any(r.get("type") == "top_liquid" for r in selection.get("include", []) + selection.get("exclude", [])) else None
    if available is None:
        p = C.DATA / "prices.parquet"
        available = set(pd.read_parquet(p, columns=["symbol"]).symbol.unique()) if p.exists() else set()
    notes, inc = [], set()
    for r in selection.get("include", []):
        s = _rule(r, liq)
        if s is None:
            notes.append(f"{describe_rule(r)}: not available (download the index list)")
            continue
        inc |= s
    for r in selection.get("exclude", []):
        s = _rule(r, liq)
        if s:
            inc -= s
    missing = sorted(inc - set(available))
    if missing:
        notes.append(f"{len(missing)} stock(s) have no price data and are skipped: {', '.join(missing[:12])}{'…' if len(missing) > 12 else ''}")
    return sorted(inc & set(available)), notes


def describe_rule(r):
    t = r.get("type")
    if t in ("index", "sector", "custom"):
        return f"{RULE_LABEL[t]}: {r['name']}"
    if t == "top_liquid":
        return f"Top {r.get('n', 100)} by traded value"
    if t == "symbols":
        return f"{len(r.get('symbols', []))} hand-picked"
    return RULE_LABEL.get(t, t)


def describe(selection):
    inc = " + ".join(describe_rule(r) for r in selection.get("include", [])) or "nothing"
    exc = " − ".join(describe_rule(r) for r in selection.get("exclude", []))
    return inc + (f" − {exc}" if exc else "")


if __name__ == "__main__":
    for k, v in refresh_all(force=True).items():
        print(f"{k:<26} {v}")
