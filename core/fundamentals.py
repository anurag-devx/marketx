"""Quarterly results from NSE's own filings (XBRL): revenue, profit, EPS ... with the time each was filed.

Sources (public, no keys; field names and behaviour below were measured live by the open-source project
github.com/dhananjaym182/india-xbrl-filings on 2026-10-01; this module reads fields tolerantly and reports, never
guesses, when the shape is different):
1. Financial results list, FY2015-16 onward (a bare JSON list):
       https://www.nseindia.com/api/corporates-financial-results?index=equities&period=Quarterly&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY
   NSE silently truncates wide windows, so requests use short windows (WINDOW_DAYS).
2. Integrated filing (financials), 2025 onward, paginated ({"data": [...], "totalCount": n}):
       https://www.nseindia.com/api/integrated-filing-results?index=equities&from_date=..&to_date=..&page=N
   Row fields: symbol, toDate / qe_Date (quarter end), filingDate / creation_Date (time filed), consolidated
   ("Consolidated" / "Non-Consolidated"), audited, seqNumber, and `xbrl` (the XBRL file on nsearchives.nseindia.com).
   The `ixbrl` field is an HTML rendering, not data: it is never read.
3. The XBRL file itself (plain XBRL 2.1; taxonomy in-bse-fin before 2025, SEBI in-capmkt after). Facts are matched by
   their element name (RevenueFromOperations, ProfitLossForPeriod, BasicEarningsLossPerShare... see METRICS), in the
   context that covers exactly the quarter (about 3 months ending on the quarter end, no segment dimensions).
   The year-to-date context is never used for a quarter: mixing them would double or halve every number.
4. Results calendar (upcoming board meetings), best effort: https://www.nseindia.com/api/event-calendar?index=equities
   plus "board meeting ... financial results" intimations already in the NSE filings store.

Point in time: every number keeps `filed_at`, so studies only use results once they were public. A company that
files a revised result keeps both rows; the first filing is what the market reacted to.

Files: data/fundamentals/filings.parquet (every filing found), results.parquet (numbers read from each XBRL),
calendar.parquet, state.json (what is done, failures, coverage)."""
import json, random, re, threading, time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from .config import DATA
from . import atomic

DIR = DATA / "fundamentals"
FILINGS, RESULTS, CALENDAR, STATE = DIR / "filings.parquet", DIR / "results.parquet", DIR / "calendar.parquet", DIR / "state.json"
API = "https://www.nseindia.com/api"
LEGACY_URL = API + "/corporates-financial-results?index=equities&period=Quarterly&from_date={a}&to_date={b}"
INTEGRATED_URL = API + "/integrated-filing-results?index=equities&from_date={a}&to_date={b}&page={p}"
CALENDAR_URL = API + "/event-calendar?index=equities"
INTEGRATED_START = pd.Timestamp("2025-01-01")
WINDOW_DAYS = 30
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/"}
FILING_COLS = ["filing_id", "symbol", "company", "period_from", "period_end", "filed_at", "consolidated", "audited", "xbrl", "source"]
RESULT_COLS = ["filing_id", "symbol", "period_end", "filed_at", "consolidated", "status", "note", "taxonomy", "context",
               "revenue", "other_income", "total_income", "expenses", "finance_costs", "depreciation", "exceptional", "pbt", "tax",
               "profit", "profit_owners", "eps_basic", "elements", "read_at",
               # balance sheet (at the period end) and cash flow (year to date: 6 or 12 months), from half-year / annual filings
               "bs_date", "assets", "liabilities", "equity", "equity_owners", "share_capital", "borrowings_current", "borrowings_noncurrent",
               "cash", "bank_other", "current_assets", "current_liabilities", "inventories", "receivables",
               "cf_months", "cfo", "cfi", "cff", "capex", "capex_intangible", "dividends_paid", "paid_up", "face_value"]

# balance sheet: the context dated exactly at the period end (no dimensions)
BS_METRICS = {
    "assets": ["Assets"], "liabilities": ["Liabilities"], "equity": ["Equity"],
    "equity_owners": ["EquityAttributableToOwnersOfParent"], "share_capital": ["EquityShareCapital"],
    "borrowings_current": ["BorrowingsCurrent", "CurrentBorrowings"], "borrowings_noncurrent": ["BorrowingsNoncurrent", "NoncurrentBorrowings"],
    "cash": ["CashAndCashEquivalents"], "bank_other": ["BankBalanceOtherThanCashAndCashEquivalents"],
    "current_assets": ["CurrentAssets"], "current_liabilities": ["CurrentLiabilities"], "inventories": ["Inventories"],
    "receivables": ["TradeReceivablesCurrent", "TradeReceivables"],
}
# cash flow: the longest year-to-date context ending at the period end (6 months for September, 12 for March)
CF_METRICS = {
    "cfo": ["CashFlowsFromUsedInOperatingActivities", "NetCashFlowsFromUsedInOperatingActivities"],
    "cfi": ["CashFlowsFromUsedInInvestingActivities", "NetCashFlowsFromUsedInInvestingActivities"],
    "cff": ["CashFlowsFromUsedInFinancingActivities", "NetCashFlowsFromUsedInFinancingActivities"],
    "capex": ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", "PurchaseOfPropertyPlantAndEquipment"],
    "capex_intangible": ["PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities"],
    "dividends_paid": ["DividendsPaidClassifiedAsFinancingActivities"],
}
# share details: reported in the quarter context
SHARE_METRICS = {"paid_up": ["PaidUpValueOfEquityShareCapital"], "face_value": ["FaceValueOfEquityShareCapital"]}

# element local names, first found wins (Ind-AS company format, then bank / NBFC / older names)
METRICS = {
    "revenue": ["RevenueFromOperations", "InterestEarned", "TotalRevenueFromOperations", "RevenueFromOperationsNet"],
    "other_income": ["OtherIncome"],
    "total_income": ["Income", "TotalIncome", "TotalIncomeFromOperations"],
    "expenses": ["Expenses", "TotalExpenses", "TotalExpenditure"],
    "finance_costs": ["FinanceCosts", "InterestExpended"],
    "depreciation": ["DepreciationDepletionAndAmortisationExpense", "DepreciationAndAmortisationExpense"],
    "exceptional": ["ExceptionalItemsBeforeTax", "ExceptionalItems"],
    "pbt": ["ProfitBeforeTax", "ProfitLossBeforeTax", "ProfitLossFromContinuingOperationsBeforeTax", "ProfitLossFromOrdinaryActivitiesBeforeTax"],
    "tax": ["TaxExpense", "TotalTaxExpenses", "TaxExpenses"],
    "profit": ["ProfitLossForPeriod", "ProfitLossForThePeriod", "NetProfitLossForThePeriod", "ProfitLoss", "NetProfitAfterTax",
               "ProfitLossForPeriodFromContinuingOperations"],
    "profit_owners": ["ProfitOrLossAttributableToOwnersOfParent", "ProfitLossAttributableToOwnersOfParent"],
    "eps_basic": ["BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations", "BasicEarningsLossPerShareFromContinuingOperations",
                  "BasicEarningsPerShareAfterExtraordinaryItems", "BasicEarningsPerShareBeforeExtraordinaryItems", "BasicEarningsLossPerShare"],
}


# ---------------- HTTP ----------------
class Fetcher:
    """Polite NSE client: one session per worker thread, a global minimum gap with jitter, escalating back-off.
    nsearchives refuses fast clients (0.6 s spacing failed 8 of 20 in the measurement above), so the gap is wide and
    a few requests run in parallel instead (NSE holds connections for tens of seconds)."""

    def __init__(self, gap=1.5, timeout=45, retries=4, backoff=6.0, sleep=time.sleep, session_factory=None):
        self.gap, self.timeout, self.retries, self.backoff, self._sleep = gap, timeout, retries, backoff, sleep
        self._factory = session_factory
        self._local, self._lock, self._last = threading.local(), threading.Lock(), 0.0
        self.count = 0

    def _session(self):
        s = getattr(self._local, "s", None)
        if s is None:
            if self._factory:
                s = self._factory()
            else:
                import requests
                s = requests.Session()
                s.headers.update(UA)
            self._local.s = s
        return s

    def _pace(self):
        with self._lock:
            wait = self.gap - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait + random.uniform(0, 0.3 * self.gap))
            self._last = time.monotonic()
            self.count += 1

    def get(self, url):
        last = ""
        for attempt in range(self.retries + 1):
            self._pace()
            try:
                r = self._session().get(url, timeout=self.timeout)
                if r.status_code == 200 and r.content:
                    return r.content
                last = f"HTTP {r.status_code}"
                if r.status_code == 404:
                    raise FileNotFoundError(url)
                if r.status_code in (401, 403):
                    self._local.s = None                   # fresh session next time
            except FileNotFoundError:
                raise
            except Exception as e:
                last = f"{type(e).__name__}"
            self._sleep(self.backoff * (attempt + 1) * random.uniform(1.0, 1.4))
        raise RuntimeError(f"NSE did not answer {url.split('?')[0]} after {self.retries + 1} tries ({last})")

    def json(self, url):
        return json.loads(self.get(url).decode("utf-8", errors="replace"))


# ---------------- discovery ----------------
def _date(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return pd.NaT
    s = re.sub(r"\s+", " ", str(v).replace("IST", "")).strip()
    if not s or s in ("-", "NA"):
        return pd.NaT
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                "%d-%m-%Y %H:%M:%S", "%d-%m-%Y", "%d-%b-%y"):
        t = pd.to_datetime(s, format=fmt, errors="coerce")
        if not pd.isna(t):
            return pd.Timestamp(t)
    t = pd.to_datetime(s[:19], errors="coerce", dayfirst=True)
    return pd.NaT if pd.isna(t) else pd.Timestamp(t).tz_localize(None) if pd.Timestamp(t).tzinfo else pd.Timestamp(t)


def _bool(v):
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    if s.startswith(("non-", "non ", "un-", "un ")) or s in ("n", "no", "false", "0", "standalone", ""):
        return False
    return True


def _url(v):
    s = str(v or "").strip()
    if not s or s.upper() in ("NA", "N/A", "NULL", "-"):
        return None
    if s.startswith("/"):
        s = "https://www.nseindia.com" + s
    if not s.startswith("http") or s.rstrip("/").rsplit("/", 1)[-1] in ("-", ""):
        return None
    return s


def parse_discovery(payload, source):
    """NSE results-list JSON -> DataFrame(FILING_COLS). ValueError if the shape is not a list of records."""
    rows = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("unexpected NSE results-list format (no list of records)")
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym = str(r.get("symbol") or r.get("sm_symbol") or "").strip().upper()
        end = _date(r.get("toDate") or r.get("qe_Date") or r.get("to") or r.get("periodTo"))
        filed = _date(r.get("filingDate") or r.get("creation_Date") or r.get("broadCastDate") or r.get("filingDateTs"))
        url = _url(r.get("xbrl"))
        cons = r.get("consolidated", r.get("consol"))
        fid = str(r.get("seqNumber") or r.get("filingId") or r.get("seq_Id") or "").strip() or f"{sym}|{end}|{cons}|{filed}"
        out.append(dict(filing_id=f"{source}:{fid}", symbol=sym, company=str(r.get("companyName") or r.get("company") or r.get("sm_name") or "").strip(),
                        period_from=_date(r.get("fromDate") or r.get("from") or r.get("periodFrom")), period_end=end, filed_at=filed,
                        consolidated=_bool(cons), audited=_bool(r.get("audited")), xbrl=url, source=source))
    d = pd.DataFrame(out, columns=FILING_COLS)
    return d[(d.symbol != "") & d.period_end.notna()]


def _windows(start, end, days=WINDOW_DAYS):
    a = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    while a <= end:
        b = min(a + pd.Timedelta(days=days - 1), end)
        yield a, b
        a = b + pd.Timedelta(days=1)


def discover(start, end, fetch, log=print, symbol=None):
    """Every results filing NSE lists with a filing date in [start, end] (both endpoints)."""
    frames = []
    sym = f"&symbol={symbol}" if symbol else ""
    for a, b in _windows(start, end):
        q = dict(a=a.strftime("%d-%m-%Y"), b=b.strftime("%d-%m-%Y"))
        frames.append(parse_discovery(fetch.json(LEGACY_URL.format(**q) + sym), "nse"))
        if b >= INTEGRATED_START:
            n = 0
            for p in range(1, 200):
                j = fetch.json(INTEGRATED_URL.format(p=p, **q) + sym)
                got = parse_discovery(j, "nse-integrated")
                frames.append(got)
                n += len(j.get("data") or []) if isinstance(j, dict) else len(got)
                total = j.get("totalCount") if isinstance(j, dict) else None
                if got.empty or not isinstance(total, int) or n >= total:
                    break
    d = pd.concat([f for f in frames if len(f)], ignore_index=True) if any(len(f) for f in frames) else pd.DataFrame(columns=FILING_COLS)
    return d.drop_duplicates("filing_id")


# ---------------- XBRL ----------------
_XBRLI = "{http://www.xbrl.org/2003/instance}"


def parse_xbrl(content, period_end=None):
    """XBRL instance bytes -> dict(metrics..., taxonomy, context, elements, status, note). Only the quarter context."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        return dict(status="unreadable", note=f"not XBRL ({str(e)[:60]})")
    if root.tag != f"{_XBRLI}xbrl":
        return dict(status="unreadable", note="not an XBRL instance (an HTML page?)")
    ctx, inst = {}, {}
    for c in root.iter(f"{_XBRLI}context"):
        p = c.find(f"{_XBRLI}period")
        if p is None:
            continue
        s, e = p.findtext(f"{_XBRLI}startDate"), p.findtext(f"{_XBRLI}endDate")
        has_dims = c.find(f"{_XBRLI}entity/{_XBRLI}segment") is not None or c.find(f"{_XBRLI}scenario") is not None
        if s and e and not has_dims:
            ctx[c.get("id")] = (pd.Timestamp(s.strip()), pd.Timestamp(e.strip()))
        elif p.findtext(f"{_XBRLI}instant") and not has_dims:
            inst[c.get("id")] = pd.Timestamp(p.findtext(f"{_XBRLI}instant").strip())
    pe = pd.Timestamp(period_end) if period_end is not None and not pd.isna(period_end) else None
    quarter = {k: v for k, v in ctx.items() if 60 <= (v[1] - v[0]).days <= 120 and (pe is None or abs((v[1] - pe).days) <= 3)}
    if not quarter:
        return dict(status="no quarter", note="no context covering just the quarter (annual or year-to-date only)")
    cid = "OneD" if "OneD" in quarter else sorted(quarter, key=lambda k: (quarter[k][1], -(quarter[k][1] - quarter[k][0]).days))[-1]
    q_end = quarter[cid][1]
    bs_ids = [k for k, v in inst.items() if abs((v - q_end).days) <= 3]
    bs_cid = bs_ids[0] if bs_ids else None
    ytd = {k: v for k, v in ctx.items() if abs((v[1] - q_end).days) <= 3 and (v[1] - v[0]).days >= 150}
    cf_cid = max(ytd, key=lambda k: (ytd[k][1] - ytd[k][0]).days) if ytd else None
    by_ctx = {bs_cid: {}, cf_cid: {}}
    facts, ns = {}, set()
    for el in root.iter():
        ref = el.get("contextRef")
        if ref in by_ctx and ref is not None and el.text is not None:
            try:
                by_ctx[ref].setdefault(el.tag.split("}")[-1], float(el.text.strip().replace(",", "")))
            except ValueError:
                pass
        if ref != cid or el.text is None:
            continue
        tag = el.tag
        if tag.startswith("{"):
            uri, local = tag[1:].split("}", 1)
            ns.add(uri)
        else:
            local = tag
        try:
            facts.setdefault(local, float(el.text.strip().replace(",", "")))
        except ValueError:
            continue
    out, used = {}, {}
    for m, names in METRICS.items():
        name = next((n for n in names if n in facts), None)
        out[m] = facts.get(name) if name else None
        if name:
            used[m] = name
    tax = "in-capmkt (SEBI)" if any("sebi.gov.in" in u for u in ns) else "in-bse-fin" if any("bseindia" in u for u in ns) else ", ".join(sorted(ns))[:80]
    if out["revenue"] is None and out["profit"] is None:
        return dict(status="no numbers", note=f"no revenue or profit element in the quarter context ({len(facts)} facts)", taxonomy=tax, context=cid)
    pick = lambda src, names: next((src[n] for n in names if n in src), None)
    bs, cf = by_ctx.get(bs_cid, {}) if bs_cid else {}, by_ctx.get(cf_cid, {}) if cf_cid else {}
    for m, names in BS_METRICS.items():
        out[m] = pick(bs, names)
    for m, names in CF_METRICS.items():
        out[m] = pick(cf, names)
    for m, names in SHARE_METRICS.items():
        out[m] = pick(facts, names)
    if out["assets"] is not None:
        out["bs_date"] = str(inst[bs_cid].date())
    if out["cfo"] is not None:
        a, b = ctx[cf_cid]
        out["cf_months"] = int(round((b - a).days / 30.4))
    return dict(out, status="ok", note="", taxonomy=tax, context=cid, elements=json.dumps(used))


# ---------------- stores ----------------
def _read(path, cols):
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame(columns=cols)


def filings():
    return _read(FILINGS, FILING_COLS)


def results():
    return _read(RESULTS, RESULT_COLS)


def state():
    try:
        return json.loads(STATE.read_text()) if STATE.exists() else {}
    except Exception:
        return {}


def _save_state(s):
    DIR.mkdir(parents=True, exist_ok=True)
    atomic.write_text(STATE, json.dumps(s, indent=1, default=str))


def _merge_filings(new):
    from .filelock import locked
    with locked("fundamentals"):
        old = filings()
        d = pd.concat([x for x in (old, new) if len(x)], ignore_index=True) if len(old) or len(new) else new
        d = d.drop_duplicates("filing_id", keep="last") if len(d) else d
        DIR.mkdir(parents=True, exist_ok=True)
        atomic.to_parquet(d, FILINGS)
        return d


def _append_results(rows):
    if not rows:
        return
    from .filelock import locked
    with locked("fundamentals"):
        old = results()
        new = pd.DataFrame(rows).reindex(columns=RESULT_COLS)
        d = pd.concat([x for x in (old, new) if len(x)], ignore_index=True).drop_duplicates("filing_id", keep="last")
        atomic.to_parquet(d, RESULTS)


def plan(fl, universe, done, failed, max_fail=3):
    """Which XBRL files to read: one per stock and quarter (consolidated if filed, else standalone), newest quarters
    first, skipping ones already read and ones that failed `max_fail` times. A revised filing is read too."""
    f = fl[fl.symbol.isin(universe) & fl.xbrl.notna()].copy()
    if f.empty:
        return f
    has_cons = f[f.consolidated].groupby(["symbol", "period_end"]).size()
    f["cons_exists"] = [((s, p) in has_cons.index) for s, p in zip(f.symbol, f.period_end)]
    f = f[f.consolidated | ~f.cons_exists]
    f = f[~f.filing_id.isin(done)]
    f = f[f.filing_id.map(lambda i: failed.get(i, 0) < max_fail).astype(bool)] if len(f) else f
    return f.sort_values(["period_end", "filed_at"], ascending=[False, True])


def read_filings(todo, fetch, log=print, workers=4, max_files=None):
    """Download and read XBRL files in parallel (politely). Returns (ok, failed) counts; results saved in batches."""
    todo = todo.head(max_files) if max_files else todo
    st = state()
    failed = st.setdefault("failed", {})
    rows, ok, bad, t0 = [], 0, 0, time.time()

    def one(r):
        try:
            res = parse_xbrl(fetch.get(r.xbrl), r.period_end)
        except FileNotFoundError:
            res = dict(status="missing file", note="NSE has no XBRL file at the listed link (404)")
        except Exception as e:
            return r, None, f"{type(e).__name__}: {str(e)[:120]}"
        return r, res, None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, r) for r in todo.itertuples()]
        for i, fu in enumerate(as_completed(futs), 1):
            r, res, err = fu.result()
            if err:
                failed[r.filing_id] = failed.get(r.filing_id, 0) + 1
                bad += 1
            else:
                failed.pop(r.filing_id, None)
                rows.append(dict(res, filing_id=r.filing_id, symbol=r.symbol, period_end=r.period_end, filed_at=r.filed_at,
                                 consolidated=bool(r.consolidated), read_at=pd.Timestamp.now().floor("s")))
                ok += res.get("status") == "ok"
            if len(rows) >= 100 or i == len(futs):
                _append_results(rows); rows = []
                _save_state(st)
            if i % 100 == 0:
                el = time.time() - t0
                log(f"  results files: {i}/{len(futs)} read, {el / 60:.0f} min, about {el / i * (len(futs) - i) / 60:.0f} min left", )
    _save_state(st)
    return ok, bad


def refresh_balance(log=print, fetch=None, workers=4, max_files=None):
    """Re-read half-year and annual results files (period ending March or September) that were read before the balance
    sheet and cash flow were parsed. Resumable: a file is skipped once its balance sheet (or a 'none' note) is stored."""
    fetch = fetch or Fetcher()
    res, fl = results(), filings()
    if res.empty or fl.empty:
        log("No results read yet: run the results download first.")
        return 0, 0
    pe = pd.to_datetime(res.period_end)
    need = res[(res.status == "ok") & pe.dt.month.isin([3, 9]) & res.assets.isna() & res.cfo.isna()] if "assets" in res else res[(res.status == "ok") & pe.dt.month.isin([3, 9])]
    todo = fl[fl.filing_id.isin(set(need.filing_id))].sort_values("period_end", ascending=False)
    log(f"{len(todo)} half-year / annual results files to re-read for the balance sheet and cash flow")
    ok, bad = read_filings(todo, fetch, log, workers, max_files)
    log(f"Balance sheet refresh: {ok} read, {bad} failed (retried next run). {fetch.count} requests.")
    return ok, bad


def _universe():
    p = DATA / "universe.csv"
    return set(pd.read_csv(p).Symbol) if p.exists() else set()


def coverage(universe=None, start=None):
    """Per quarter end: how many universe stocks have numbers read. Gaps are stocks that filed nothing NSE lists,
    filings without XBRL, or files not read yet."""
    r = results()
    uni = universe or _universe()
    if r.empty:
        return pd.DataFrame(columns=["period_end", "stocks_with_numbers", "universe"])
    r = r[(r.status == "ok") & r.symbol.isin(uni)]
    if start is not None:
        r = r[pd.to_datetime(r.period_end) >= pd.Timestamp(start)]
    c = r.groupby("period_end").symbol.nunique().rename("stocks_with_numbers").reset_index()
    c["universe"] = len(uni)
    return c.sort_values("period_end", ascending=False)


def update(days=120, log=print, fetch=None, workers=4):
    """Recent filings (last `days` days): discover, then read every new XBRL file. Runs in the daily chain."""
    fetch = fetch or Fetcher()
    end = pd.Timestamp.now().normalize()
    st = state()
    log(f"Results filings from {(end - pd.Timedelta(days=days)).date()} to {end.date()}...")
    try:
        new = discover(end - pd.Timedelta(days=days), end, fetch, log)
    except ValueError as e:
        log(f"NSE results list: {e}. Stopping; nothing guessed.")
        raise
    fl = _merge_filings(new)
    log(f"  {len(new)} filings listed ({new.symbol.isin(_universe()).sum()} for Nifty 500 stocks)")
    done = set(results().filing_id)
    recent = fl[fl.filing_id.isin(set(new.filing_id))] if "filing_id" in new else fl.iloc[0:0]
    todo = plan(recent, _universe(), done, st.get("failed", {}))            # only this window; older unread files: backfill
    backlog = len(plan(fl, _universe(), done, st.get("failed", {}))) - len(todo)
    log(f"  {len(todo)} results files to read" + (f" ({backlog} older ones not read yet: run 'Quarterly results history')" if backlog > 0 else ""))
    ok, bad = read_filings(todo, fetch, log, workers)
    st = state()
    st.update(last_update=str(pd.Timestamp.now().floor("s")), last_update_read=ok, last_update_failed=bad)
    _save_state(st)
    log(f"Results: {ok} read, {bad} failed (retried next run). {fetch.count} requests.")
    try:
        cal = update_calendar(fetch, log)
        log(f"Results calendar: {len(cal)} upcoming results meetings for Nifty 500 stocks.")
    except Exception as e:
        log(f"Results calendar: not available ({type(e).__name__}: {str(e)[:100]})")
    return ok, bad


def backfill(start="2021-01-01", log=print, fetch=None, workers=4, max_files=None):
    """History, resumable: list every quarter's filings (newest windows first), then read the files newest quarters
    first. Run it again to continue; stocks with gaps are re-checked one by one at the end."""
    fetch = fetch or Fetcher()
    st = state()
    done_w = set(st.get("windows_done", []))
    end = pd.Timestamp.now().normalize()
    wins = list(_windows(start, end))[::-1]
    for a, b in wins:
        key = f"{a.date()}|{b.date()}"
        if key in done_w and b < end - pd.Timedelta(days=WINDOW_DAYS * 4):
            continue
        try:
            got = discover(a, b, fetch, log)
        except ValueError as e:
            log(f"NSE results list {a.date()}..{b.date()}: {e}. Stopping; nothing guessed.")
            raise
        except Exception as e:
            log(f"  {a.date()}..{b.date()}: {type(e).__name__}; will retry next run")
            continue
        _merge_filings(got)
        done_w.add(key)
        st["windows_done"] = sorted(done_w)
        _save_state(st)
        log(f"  listed {a.date()}..{b.date()}: {len(got)} filings")
    fl = filings()
    uni = _universe()
    gap_check(fl, uni, start, fetch, log)
    fl = filings()
    todo = plan(fl, uni, set(results().filing_id), state().get("failed", {}))
    log(f"{len(todo)} results files to read (newest quarters first; you can stop and continue later)")
    ok, bad = read_filings(todo, fetch, log, workers, max_files)
    log(f"Backfill: {ok} read, {bad} failed (retried next run). {fetch.count} requests.")
    cov = coverage(uni, start)
    if len(cov):
        log("Coverage (Nifty 500 stocks with numbers) for the latest quarters: "
            + ", ".join(f"{pd.Timestamp(r.period_end):%b %Y} {r.stocks_with_numbers}" for r in cov.head(8).itertuples()))
    return ok, bad


def gap_check(fl, uni, start, fetch, log, max_symbols=150):
    """Market-wide lists can miss rows; stocks with a missing quarter are listed again on their own, once per run."""
    if fl.empty:
        return
    f = fl[fl.symbol.isin(uni)]
    q_ends = sorted({p for p in pd.to_datetime(f.period_end) if p >= pd.Timestamp(start)})
    if len(q_ends) < 2:
        return
    recent = [q for q in q_ends if q <= pd.Timestamp.now() - pd.Timedelta(days=60)][-8:]   # results due by now
    have = f.groupby("symbol").period_end.apply(lambda s: set(pd.to_datetime(s)))
    gaps = [s for s in sorted(uni) if not set(recent) <= have.get(s, set())]
    st = state()
    checked = set(st.get("gap_checked", []))
    todo = [s for s in gaps if s not in checked][:max_symbols]
    if not todo:
        return
    log(f"{len(gaps)} stock(s) miss a recent quarter in the market-wide lists; listing {len(todo)} of them one by one")
    for s in todo:
        try:
            got = discover(recent[0] - pd.Timedelta(days=10), pd.Timestamp.now(), fetch, log, symbol=s)
            _merge_filings(got)
        except Exception as e:
            log(f"  {s}: {type(e).__name__}")
        checked.add(s)
    st = state()
    st["gap_checked"] = sorted(checked)
    _save_state(st)


# ---------------- results calendar ----------------
RESULTS_MEETING = re.compile(r"financial results|quarterly results|audited results|unaudited|results for the (quarter|period|year)", re.I)
_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
DATE_IN_TEXT = re.compile(rf"(\d{{1,2}})(?:st|nd|rd|th)?[\s\-/.,]*({_MONTHS})[a-z]*[\s\-/.,]*(\d{{4}})|({_MONTHS})[a-z]*\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})|"
                          r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", re.I)


def parse_calendar(payload):
    rows = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("unexpected NSE event-calendar format")
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        purpose = " ".join(str(r.get(k) or "") for k in ("purpose", "bm_purpose", "bm_desc", "desc"))
        if not RESULTS_MEETING.search(purpose):
            continue
        out.append(dict(symbol=str(r.get("symbol") or r.get("bm_symbol") or "").strip().upper(), date=_date(r.get("date") or r.get("bm_date")),
                        purpose=purpose.strip()[:200], source="NSE event calendar"))
    return pd.DataFrame(out, columns=["symbol", "date", "purpose", "source"])


def _date_from_text(t, after):
    for m in DATE_IN_TEXT.finditer(str(t)):
        try:
            if m.group(1):
                d = pd.Timestamp(f"{m.group(1)} {m.group(2)[:3]} {m.group(3)}")
            elif m.group(4):
                d = pd.Timestamp(f"{m.group(5)} {m.group(4)[:3]} {m.group(6)}")
            else:
                d = pd.Timestamp(year=int(m.group(9)), month=int(m.group(8)), day=int(m.group(7)))
        except Exception:
            continue
        if after <= d <= after + pd.Timedelta(days=60):
            return d
    return None


def calendar_from_filings(ann, now=None):
    """Board-meeting intimations in the NSE filings store that name a results meeting and a date."""
    now = pd.Timestamp(now or pd.Timestamp.now()).normalize()
    if ann is None or ann.empty:
        return pd.DataFrame(columns=["symbol", "date", "purpose", "source"])
    a = ann[(ann.published >= now - pd.Timedelta(days=45)) & (ann.symbol.fillna("") != "")]
    txt = (a.subject.fillna("") + " " + a.text.fillna(""))
    a = a[txt.str.contains(r"board meeting|meeting of the board", case=False, regex=True) & txt.map(lambda t: bool(RESULTS_MEETING.search(t)))]
    rows = []
    for r in a.itertuples():
        d = _date_from_text(f"{r.subject} {r.text}", pd.Timestamp(r.published).normalize())
        if d is not None:
            rows.append(dict(symbol=r.symbol, date=d, purpose=f"{r.subject} {r.text}".strip()[:200], source="NSE filing (board meeting intimation)"))
    return pd.DataFrame(rows, columns=["symbol", "date", "purpose", "source"])


def update_calendar(fetch, log=print):
    frames = []
    try:
        frames.append(parse_calendar(fetch.json(CALENDAR_URL)))
    except Exception as e:
        log(f"  NSE event calendar: {type(e).__name__}; using board-meeting filings only")
    try:
        from . import announcements
        frames.append(calendar_from_filings(announcements.load()))
    except Exception:
        pass
    d = pd.concat([f for f in frames if len(f)], ignore_index=True) if any(len(f) for f in frames) else pd.DataFrame(columns=["symbol", "date", "purpose", "source"])
    uni = _universe()
    d = d[d.symbol.isin(uni) & d.date.notna()].sort_values("source").drop_duplicates(["symbol", "date"]).sort_values("date")
    DIR.mkdir(parents=True, exist_ok=True)
    atomic.to_parquet(d, CALENDAR)
    return d


def calendar(upcoming_only=True):
    d = _read(CALENDAR, ["symbol", "date", "purpose", "source"])
    if upcoming_only and len(d):
        d = d[pd.to_datetime(d.date) >= pd.Timestamp.now().normalize()]
    return d


if __name__ == "__main__":
    import sys
    args = dict(a.split("=", 1) for a in sys.argv[2:] if "=" in a)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "update"
    if cmd == "balance":
        refresh_balance(max_files=int(args["max"]) if "max" in args else None)
    elif cmd == "backfill":
        backfill(args.get("start", "2021-01-01"), max_files=int(args["max"]) if "max" in args else None)
    else:
        update(int(args.get("days", 120)))
