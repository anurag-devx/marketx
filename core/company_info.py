"""Company details the Nifty 500 list doesn't have: four industry levels and shares outstanding (for market cap).

Source: NSE's stock quote API (https://www.nseindia.com/api/quote-equity?symbol=...), one request per stock,
downloaded once and refreshed when you ask (Data health). NSE does not document this API; the fields are read
tolerantly and anything missing stays blank. If it can't be reached, every screen falls back to the Nifty 500
list's "Industry" column as the sector, and market cap is left blank (shown as traded value instead).

Market cap here = shares outstanding (today) x close. Shares change with splits, bonuses and new issues, so for
old dates it is an approximation."""
import json, time
import pandas as pd
from .config import DATA
from . import atomic

FILE = DATA / "company_info.csv"
URL = "https://www.nseindia.com/api/quote-equity?symbol={s}"
COLS = ["symbol", "macro", "sector", "industry", "basic_industry", "shares", "isin", "listing_date", "fetched"]


def _pick(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def parse_quote(sym, j):
    """NSE quote JSON -> one row. Raises ValueError if the response has none of the expected parts."""
    if not isinstance(j, dict) or not any(k in j for k in ("industryInfo", "info", "securityInfo", "metadata")):
        raise ValueError("unexpected response shape from NSE quote API")
    ii = j.get("industryInfo") or {}
    shares = _pick(j, "securityInfo", "issuedSize") or _pick(j, "securityInfo", "issuedCap")
    try:
        shares = float(shares) if shares not in (None, "", "-") else None
    except (TypeError, ValueError):
        shares = None
    return dict(symbol=sym, macro=ii.get("macro") or "", sector=ii.get("sector") or "", industry=ii.get("industry") or "",
                basic_industry=ii.get("basicIndustry") or "", shares=shares,
                isin=_pick(j, "info", "isin") or _pick(j, "metadata", "isin") or "",
                listing_date=_pick(j, "info", "listingDate") or _pick(j, "metadata", "listingDate") or "",
                fetched=str(pd.Timestamp.now().floor("s")))


def download(symbols=None, refresh=False, log=print, session=None):
    from .announcements import NSESession
    uni = pd.read_csv(DATA / "universe.csv")
    symbols = symbols or list(uni.Symbol)
    old = load(raw=True)
    have = set(old.symbol) if len(old) and not refresh else set()
    sess = session or NSESession(gap=1.2)
    rows, errors = [], 0
    for i, s in enumerate([s for s in symbols if s not in have], 1):
        try:
            rows.append(parse_quote(s, sess.get_json(URL.format(s=s.replace("&", "%26")))))
        except ValueError as e:
            log(f"{s}: {e}. Stopping; the data source changed its format — keep this message for troubleshooting.")
            break
        except Exception as e:
            errors += 1
            log(f"{s}: {type(e).__name__}")
            if errors >= 10 and not rows:
                log("NSE is not answering; try again later.")
                break
        if i % 50 == 0:
            log(f"  {i} stocks done")
            _save(old, rows)
    _save(old, rows)
    log(f"Company info: {len(rows)} stocks downloaded, {errors} failed.")
    return len(rows)


def _save(old, rows):
    if not rows:
        return
    new = pd.DataFrame(rows, columns=COLS)
    d = pd.concat([old[~old.symbol.isin(new.symbol)] if len(old) else old, new], ignore_index=True)
    atomic.to_csv(d, FILE)


def load(raw=False):
    """One row per Nifty 500 stock with sector / industry (falling back to the Nifty 500 'Industry') and shares."""
    info = pd.read_csv(FILE) if FILE.exists() else pd.DataFrame(columns=COLS)
    if raw:
        return info
    uni = pd.read_csv(DATA / "universe.csv").rename(columns={"Symbol": "symbol", "Company Name": "company", "Industry": "nifty_industry"})
    d = uni.merge(info, on="symbol", how="left")
    d["sector"] = d.sector.where(d.sector.fillna("") != "", d.nifty_industry)
    d["industry"] = d.industry.where(d.industry.fillna("") != "", d.sector)
    d["basic_industry"] = d.basic_industry.where(d.basic_industry.fillna("") != "", d.industry)
    return d[["symbol", "company", "nifty_industry", "macro", "sector", "industry", "basic_industry", "shares"]]


if __name__ == "__main__":
    import sys
    download(refresh="refresh" in sys.argv)
