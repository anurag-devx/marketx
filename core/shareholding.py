"""Quarterly shareholding pattern from NSE: who owns each company, quarter by quarter, with the time it was published.

Sources (public, no keys; shapes measured on real responses on 2026-10-24):
1. Shareholding list per stock (one request, every quarter since listing; a bare JSON list):
       https://www.nseindia.com/api/corporate-share-holdings-master?index=equities&symbol=TCS
   Row fields used: date (quarter end, "30-SEP-2026"), broadcastDate (published, IST), pr_and_prgrp (promoter %),
   public_val (public %), employeeTrusts, revisedData, recordId, xbrl (the full filing).
2. The XBRL filing (SEBI shareholding taxonomy, in-bse-shp): every category is a context with exactly one explicit member,
   e.g. InstitutionsForeignMember. Facts read: ShareholdingAsAPercentageOfTotalNumberOfShares (a fraction, 0.7177 = 71.77%),
   NumberOfShares, NumberOfShareholders. Individual shareholders use typed members and are not read.

Categories stored (percent of total shares):
  promoter_pct  ShareholdingOfPromoterAndPromoterGroupMember     public_pct  PublicShareholdingMember
  fii_pct       InstitutionsForeignMember (FPIs + FDI + others)   fpi_pct     FPI category I + II
  dii_pct       InstitutionsDomesticMember                       mf_pct      MutualFundsOrUTIMember
  insurance_pct InsuranceCompaniesMember                          retail_pct  resident individuals up to Rs 2 lakh
  shares_total  NumberOfShares for the whole pattern              holders     NumberOfShareholders for the whole pattern
The list (1) gives promoter / public for every quarter cheaply; the XBRL (2) is read for the latest `quarters` quarters.

Point in time: `published` is when NSE broadcast the filing; analyses only use a row after it was public.
Files: data/shareholding/holdings.parquet, state.json."""
import json, sys, time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np, pandas as pd
from .config import DATA
from . import atomic

DIR = DATA / "shareholding"
FILE, STATE = DIR / "holdings.parquet", DIR / "state.json"
MASTER_URL = "https://www.nseindia.com/api/corporate-share-holdings-master?index=equities&symbol={s}"
X = "{http://www.xbrl.org/2003/instance}"
MEMBERS = {"ShareholdingOfPromoterAndPromoterGroupMember": "promoter", "PublicShareholdingMember": "public",
           "InstitutionsForeignMember": "fii", "InstitutionsForeignPortfolioInvestorCategoryOneMember": "fpi1",
           "InstitutionsForeignPortfolioInvestorCategoryTwoMember": "fpi2", "InstitutionsDomesticMember": "dii",
           "MutualFundsOrUTIMember": "mf", "InsuranceCompaniesMember": "insurance",
           "ResidentIndividualShareholdersHoldingNominalShareCapitalUpToRsTwoLakhMember": "retail",
           "ShareholdingPatternMember": "total"}
COLS = ["symbol", "period_end", "published", "promoter_pct", "public_pct", "fii_pct", "fpi_pct", "dii_pct", "mf_pct", "insurance_pct",
        "retail_pct", "shares_total", "holders", "revised", "record_id", "xbrl", "detail"]


def _num(v):
    try:
        x = float(str(v).replace(",", "").strip())
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def parse_master(sym, j):
    """NSE list -> one row per quarter (promoter / public only). Raises ValueError on an unexpected shape."""
    rows = j if isinstance(j, list) else (j or {}).get("data")
    if not isinstance(rows, list):
        raise ValueError("unexpected response shape from the NSE shareholding list")
    out = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("date"):
            continue
        pe = pd.to_datetime(r.get("date"), format="%d-%b-%Y", errors="coerce")
        pub = pd.to_datetime(r.get("broadcastDate"), format="%d-%b-%Y %H:%M:%S", errors="coerce")
        if pd.isna(pe):
            continue
        out.append(dict(symbol=sym, period_end=pe, published=pub, promoter_pct=_num(r.get("pr_and_prgrp")), public_pct=_num(r.get("public_val")),
                        revised=str(r.get("revisedData", "N")).upper() == "Y", record_id=str(r.get("recordId") or ""),
                        xbrl=str(r.get("xbrl") or "") if str(r.get("xbrl") or "").endswith(".xml") else None, detail=False))
    return pd.DataFrame(out, columns=[c for c in COLS if c not in ("fii_pct", "fpi_pct", "dii_pct", "mf_pct", "insurance_pct", "retail_pct",
                                                                   "shares_total", "holders")])


def parse_xbrl(content):
    """Shareholding XBRL -> dict of category percentages (0-100), shares_total, holders. Raises ValueError if unreadable."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise ValueError(f"not XBRL ({str(e)[:60]})")
    ctx = {}
    for c in root.iter(X + "context"):
        ex = [m.text.split(":")[-1] for m in c.iter() if m.tag.endswith("explicitMember") and m.text]
        typed = any(m.tag.endswith("typedMember") for m in c.iter())
        if len(ex) == 1 and not typed and ex[0] in MEMBERS:
            ctx[c.get("id")] = MEMBERS[ex[0]]
    got = {}
    for el in root:
        k = ctx.get(el.get("contextRef"))
        if not k or el.text is None:
            continue
        name = el.tag.split("}")[-1]
        v = _num(el.text)
        if v is None:
            continue
        if name == "ShareholdingAsAPercentageOfTotalNumberOfShares":
            got.setdefault(f"{k}_pct", round(100 * v if v <= 1.0000001 else v, 4))
        elif name == "NumberOfShares":
            got.setdefault(f"{k}_shares", v)
        elif name == "NumberOfShareholders":
            got.setdefault(f"{k}_holders", v)
    if "total_shares" not in got and "promoter_pct" not in got:
        raise ValueError("no shareholding categories found in the XBRL")
    fpi = [got.get("fpi1_pct"), got.get("fpi2_pct")]
    return dict(promoter_pct=got.get("promoter_pct"), public_pct=got.get("public_pct"), fii_pct=got.get("fii_pct"),
                fpi_pct=round(sum(x for x in fpi if x is not None), 4) if any(x is not None for x in fpi) else None,
                dii_pct=got.get("dii_pct"), mf_pct=got.get("mf_pct"), insurance_pct=got.get("insurance_pct"), retail_pct=got.get("retail_pct"),
                shares_total=got.get("total_shares"), holders=got.get("total_holders"))


def load():
    if not FILE.exists():
        return pd.DataFrame(columns=COLS)
    d = pd.read_parquet(FILE)
    d["period_end"] = pd.to_datetime(d.period_end)
    d["published"] = pd.to_datetime(d.published)
    return d


def state():
    try:
        return json.loads(STATE.read_text()) if STATE.exists() else {}
    except Exception:
        return {}


def _save(d, st=None):
    DIR.mkdir(parents=True, exist_ok=True)
    from .filelock import locked
    with locked("shareholding"):
        old = load()
        both = pd.concat([x for x in (old, d) if len(x)], ignore_index=True) if len(old) or len(d) else d
        if len(both):
            # a detailed (XBRL) row wins over a list-only row for the same quarter; otherwise the newest record wins
            both = both.sort_values(["symbol", "period_end", "detail", "published"]).drop_duplicates(["symbol", "period_end"], keep="last")
        atomic.to_parquet(both.reindex(columns=COLS) if len(both) else pd.DataFrame(columns=COLS), FILE)
        if st is not None:
            atomic.write_text(STATE, json.dumps(st, indent=1, default=str))
        return both


def _universe():
    return list(pd.read_csv(DATA / "universe.csv").Symbol)


def update(symbols=None, quarters=4, refresh_days=20, log=print, session=None, fetch=None, workers=3):
    """Resumable. For each stock: the shareholding list (if not fetched in the last `refresh_days` days), then the XBRL
    files of its latest `quarters` quarters that haven't been read yet."""
    from .announcements import NSESession
    from .fundamentals import Fetcher
    syms = symbols or _universe()
    sess = session or NSESession(gap=1.5)
    st = state()
    fetched = st.setdefault("master_fetched", {})
    failed = st.setdefault("xbrl_failed", {})
    now = pd.Timestamp.now()
    todo = [s for s in syms if s not in fetched or (now - pd.Timestamp(fetched[s])).days >= refresh_days]
    log(f"Shareholding: {len(syms)} stocks, {len(todo)} lists to fetch (others fetched in the last {refresh_days} days)")
    rows, errors, t0 = [], 0, time.time()
    for i, s in enumerate(todo, 1):
        try:
            m = parse_master(s, sess.get_json(MASTER_URL.format(s=s.replace("&", "%26"))))
            rows.append(m)
            fetched[s] = str(now.floor("s"))
        except ValueError as e:
            log(f"{s}: {e}. Stopping; the data source changed its format — keep this message for troubleshooting.")
            break
        except Exception as e:
            errors += 1
            log(f"{s}: {type(e).__name__}")
            if errors >= 10 and not rows:
                log("NSE is not answering the shareholding list; try again later.")
                break
        if i % 50 == 0 or i == len(todo):
            if rows:
                _save(pd.concat(rows, ignore_index=True), st)
                rows = []
            log(f"  lists: {i}/{len(todo)} ({time.time() - t0:.0f} s)")
    if rows:
        _save(pd.concat(rows, ignore_index=True), st)
    d = load()
    if d.empty:
        log("Nothing stored yet.")
        return 0
    want = d[d.symbol.isin(syms)].sort_values("period_end").groupby("symbol").tail(int(quarters))     # latest quarters per stock...
    want = want[want.xbrl.notna() & ~want.detail.astype(bool)]                                          # ...not read yet
    want = want[want.record_id.map(lambda r: failed.get(r, 0) < 3)]
    log(f"Shareholding detail: {len(want)} XBRL files to read (latest {quarters} quarters per stock)")
    fetch = fetch or Fetcher(gap=1.5)
    done, bad, batch = 0, 0, []

    def one(r):
        try:
            return r, parse_xbrl(fetch.get(r.xbrl)), None
        except Exception as e:
            return r, None, f"{type(e).__name__}: {str(e)[:80]}"

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, r) for r in want.itertuples()]
        for i, fu in enumerate(as_completed(futs), 1):
            r, res, err = fu.result()
            if err:
                failed[r.record_id] = failed.get(r.record_id, 0) + 1
                bad += 1
            else:
                failed.pop(r.record_id, None)
                row = r._asdict()
                row.pop("Index", None)
                row.update({k: v for k, v in res.items() if v is not None})
                row["detail"] = True
                batch.append(row)
                done += 1
            if len(batch) >= 50 or i == len(futs):
                if batch:
                    _save(pd.DataFrame(batch), st)
                    batch = []
            if i % 50 == 0:
                log(f"  detail: {i}/{len(futs)} read")
    st.update(last_update=str(pd.Timestamp.now().floor("s")), last_read=done, last_failed=bad)
    _save(pd.DataFrame(columns=COLS), st)
    log(f"Shareholding: {done} detailed filings read, {bad} failed (retried next run).")
    return done


def as_of(d, symbol, cutoff):
    """This stock's rows published by `cutoff` (naive IST), oldest first."""
    if d is None or d.empty:
        return d
    x = d[(d.symbol == symbol) & d.published.notna() & (d.published <= cutoff)]
    return x.sort_values("period_end")


def changes(rows):
    """Latest quarter vs the one before (percentage points) for each category present in both."""
    if rows is None or len(rows) < 2:
        return {}
    a, b = rows.iloc[-2], rows.iloc[-1]
    out = {}
    for c in ("promoter_pct", "fii_pct", "dii_pct", "mf_pct", "retail_pct", "public_pct"):
        if pd.notna(a.get(c)) and pd.notna(b.get(c)):
            out[c] = float(b[c] - a[c])
    return out


if __name__ == "__main__":
    args = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    update(quarters=int(args.get("quarters", 4)), refresh_days=int(args.get("refresh", 20)))
