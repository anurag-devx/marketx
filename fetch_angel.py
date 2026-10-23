"""
Step 1 (Angel One version) - Download daily prices for the Nifty 500 + Nifty 50 + India VIX
using your Angel One SmartAPI account.

CREDENTIALS - kept OUTSIDE this project folder, read only on your Mac:
  Create ~/.angel_env with these 4 lines (no quotes):
      ANGEL_API_KEY=your_api_key
      ANGEL_CLIENT_CODE=your_client_code
      ANGEL_PIN=your_mpin
      ANGEL_TOTP_SECRET=your_totp_secret
  then:  chmod 600 ~/.angel_env
  (If the file is missing, the script asks for them at runtime instead and stores nothing.)

Run:
    cd ~/"Personal Project - Finance Based/swing"
    python3 fetch_angel.py            update: only the days since the last download (a few minutes)
    python3 fetch_angel.py --full     re-download all history (about 30 minutes)
    python3 fetch_angel.py --broad    also every other NSE stock, for survivorship-free tests (3-4 hours once: Angel allows 5,000 requests an hour)
An update re-reads the last 10 days of each stock; if Angel's older prices changed (it re-adjusted
history for a split or bonus) or today's open gaps like a split, that stock is re-downloaded in full.
Output (same format as fetch_data.py): data/prices.parquet, data/index.parquet,
data/universe.csv, data/split_adjustments.csv, data/fetch_log.json, data/quality.json
"""
import subprocess, sys
for mod, pkg in [("SmartApi", "smartapi-python"), ("pyotp", "pyotp"), ("logzero", "logzero"),
                 ("websocket", "websocket-client"), ("pandas", "pandas"), ("pyarrow", "pyarrow"),
                 ("requests", "requests"), ("tabulate", "tabulate")]:
    try:
        __import__(mod)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])

import io, json, os, shutil, time, getpass
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np, pandas as pd, requests, pyotp
from SmartApi import SmartConnect
sys.path.insert(0, str(Path(__file__).resolve().parent))
from core.provider import AngelProvider, ProviderError
from core import atomic
import logging, logzero
# The SmartAPI library logs full request headers (API key, session token) on any error.
# Silence it completely so credentials never reach the screen, the dashboard or log files.
logzero.loglevel(logging.CRITICAL)
for _name in ("smartConnect", "SmartApi", "logzero_default", "urllib3"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)

START = datetime(2015, 1, 1)
CHUNK_DAYS = 1900          # API limit for ONE_DAY is 2000 days per request
SLEEP = 1.0                # start at 1 request/second: Angel's limiter rejects far below its published 3/s (core/provider.py)
RETRY_PASSES = (120, 300, 600)   # seconds to wait before each extra pass over stocks that still failed
OUT = Path(__file__).parent / "data"
OUT.mkdir(exist_ok=True)
ENV_FILE = Path.home() / ".angel_env"


def creds():
    keys = ["ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET"]
    vals = {k: os.environ.get(k) for k in keys}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                k = k.strip().removeprefix("export ").strip()      # tolerate 'export KEY=value'
                vals.setdefault(k, None)
                if not vals.get(k):
                    vals[k] = v.strip().strip('"').strip("'")       # tolerate quoted values
    missing = [k for k in keys if not vals.get(k)]
    if missing and not sys.stdin.isatty():
        sys.exit(f"Missing {', '.join(missing)} in ~/.angel_env. Add them there (see the top of fetch_angel.py).")
    for k in missing:
        vals[k] = getpass.getpass(f"{k}: ")
    return vals


def login():
    c = creds()
    api = SmartConnect(api_key=c["ANGEL_API_KEY"])
    s = api.generateSession(c["ANGEL_CLIENT_CODE"], c["ANGEL_PIN"],
                            pyotp.TOTP(c["ANGEL_TOTP_SECRET"]).now())
    if not s or not s.get("status"):
        sys.exit(f"Login failed: {s.get('message') if s else 'no response'}")
    print("Logged in to Angel One.")
    return api


def candles(prov, token, exchange="NSE", frm=None):
    """Raw (unadjusted), validated daily bars through the provider (pacing, retries, re-login: core/provider.py)."""
    return prov.candles(token, frm or START, exchange=exchange)


SPLIT_FACTORS = [1.5, 2, 2.5, 3, 4, 5, 10, 20]   # bonus 1:2, 1:1, splits 2:1, 5:1, 10:1 ...

def adjust_splits(d, sym, log):
    """Angel candles are NOT split/bonus adjusted. Detect overnight gaps matching a
    standard ratio and back-adjust earlier prices. Every adjustment is logged for review."""
    d = d.reset_index(drop=True).astype({c: float for c in ["open", "high", "low", "close", "volume"]})
    for i in range(1, len(d)):
        ratio = d.open.iat[i] / d.close.iat[i - 1]
        if ratio >= 0.72:
            continue
        f = min(SPLIT_FACTORS, key=lambda x: abs(x * ratio - 1))
        if abs(f * ratio - 1) < 0.08:
            # A split/bonus multiplies the share count, so volume rises about f times. A crash of the same size does not
            # do that reliably. With 10+ bars on each side, require median volume to rise at least 0.6 x f; otherwise
            # leave the prices alone (the quality check then flags the gap for you to review).
            before, after = d.volume.iloc[max(0, i - 20):i], d.volume.iloc[i:i + 20]
            if len(before) >= 10 and len(after) >= 10 and before.median() > 0 and after.median() / before.median() < 0.6 * f:
                log.append(dict(symbol=sym, date=d.date.iat[i].date(), factor=f, gap_ratio=round(ratio, 3),
                                note="not adjusted: volume did not rise like a split; review in Data health"))
                continue
            d.loc[:i - 1, ["open", "high", "low", "close"]] /= f
            d.loc[:i - 1, "volume"] *= f
            log.append(dict(symbol=sym, date=d.date.iat[i].date(), factor=f, gap_ratio=round(ratio, 3), note="adjusted"))
    return d


OVERLAP_DAYS = 10           # re-read the last 10 days each update to catch revised or re-adjusted bars
READJUST_TOL = 0.02         # stored close vs Angel's close differs by 2%+ on an old day -> history was re-adjusted


def update_symbol(fetch, old, sym, log):
    """One stock. fetch(from_dt) -> raw candles from that date. old: this stock's stored (adjusted) rows.
    Returns (rows, how, raw): how in new | update | refetch (history changed: split/bonus) | none;
    raw = the unadjusted bars just downloaded (kept separately from the adjusted prices)."""
    if old is None or old.empty:
        d = fetch(START)
        return (adjust_splits(d, sym, log), "new", d) if len(d) else (d, "none", d)
    last = old.date.max()
    d = fetch(last - timedelta(days=OVERLAP_DAYS))
    if d.empty:
        return old, "none", d
    ov = old.merge(d[["date", "close"]], on="date", suffixes=("", "_new"))
    ov = ov[ov.date < last]                       # the last stored bar may have been provisional
    readjusted = len(ov) and ((ov.close_new / ov.close - 1).abs() > READJUST_TOL).any()
    fresh = d[d.date > last]
    if len(fresh):
        prev_close = float(old.sort_values("date").close.iat[-1])
        ratio = float(fresh.open.iat[0]) / prev_close
        f = min(SPLIT_FACTORS, key=lambda x: abs(x * ratio - 1))
        split_now = ratio < 0.72 and abs(f * ratio - 1) < 0.08
    else:
        split_now = False
    if readjusted or split_now:
        full = fetch(START)
        return adjust_splits(full, sym, log), "refetch", full
    keep = old[old.date < d.date.min()]
    out = pd.concat([keep, d.astype({c: float for c in ["open", "high", "low", "close", "volume"]})], ignore_index=True)
    return out.drop_duplicates("date", keep="last").sort_values("date"), "update", d


def load_universe():
    url = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
    try:
        uni = pd.read_csv(io.StringIO(requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text))
        uni.columns = [c.strip() for c in uni.columns]
        uni = uni[["Company Name", "Industry", "Symbol"]]
        old = OUT / "universe.csv"
        if old.exists():
            prev = pd.read_csv(old)
            added, dropped = set(uni.Symbol) - set(prev.Symbol), set(prev.Symbol) - set(uni.Symbol)
            if added or dropped:
                hist = OUT / "universe_changes.csv"
                ch = pd.DataFrame([dict(date=str(datetime.now().date()), symbol=s, change="added") for s in sorted(added)]
                                  + [dict(date=str(datetime.now().date()), symbol=s, change="removed") for s in sorted(dropped)])
                ch.to_csv(hist, mode="a", header=not hist.exists(), index=False)
                print(f"Nifty 500 changes: +{len(added)} -{len(dropped)} (logged in data/universe_changes.csv)")
        uni.to_csv(old, index=False)
        return uni
    except Exception as e:
        if (OUT / "universe.csv").exists():
            print(f"Could not refresh the Nifty 500 list ({e}); using the saved one.")
            return pd.read_csv(OUT / "universe.csv")
        raise


def run_set(api, symbols, tok, path, full, label):
    """Downloads or updates one price file (adjusted) and its raw twin in data/raw/. Returns a summary dict."""
    old = pd.read_parquet(path) if (path.exists() and not full) else pd.DataFrame(columns=["date", "symbol"])
    if len(old):
        old["date"] = pd.to_datetime(old.date).dt.tz_localize(None).dt.normalize()
    by_sym = {s: g.drop(columns="symbol") for s, g in old.groupby("symbol")} if len(old) else {}
    raw_path = OUT / "raw" / path.name.replace(".parquet", "_raw.parquet")
    raw_old = pd.read_parquet(raw_path) if (raw_path.exists() and not full) else pd.DataFrame(columns=["date", "symbol"])
    raw_by = {s: g.drop(columns="symbol") for s, g in raw_old.groupby("symbol")} if len(raw_old) else {}
    log, missing, how = [], [], {}
    res, raw_res, failed_why = {}, {}, {}          # symbol -> adjusted rows / raw rows to keep / last failure
    t0 = time.time()

    def one(sym):
        try:
            d, h, raw = update_symbol(lambda frm, t=tok[sym]: candles(api, t, frm=frm), by_sym.get(sym), sym, log)
        except ProviderError as e:
            if e.kind == "auth":
                raise
            failed_why[sym] = f"{e.kind}: {str(e)[:140]}"
            res[sym] = by_sym.get(sym, pd.DataFrame())
            raw_res[sym] = raw_by[sym] if sym in raw_by else None
            return "failed"
        failed_why.pop(sym, None)
        if h in ("new", "refetch"):
            raw_res[sym] = raw
        elif h == "update":
            prev = raw_by.get(sym, pd.DataFrame(columns=raw.columns))
            raw_res[sym] = pd.concat([prev[prev.date < raw.date.min()] if len(prev) else prev, raw])
        else:
            raw_res[sym] = raw_by.get(sym)
        if h == "refetch":
            print(f"  {sym}: history changed (split/bonus?) -> re-downloaded in full", flush=True)
        res[sym] = d
        return h

    todo = []
    for n, sym in enumerate(symbols, 1):
        if sym not in tok:
            missing.append(sym)
            if sym in by_sym:
                res[sym] = by_sym[sym]
                raw_res[sym] = raw_by.get(sym)
            continue
        todo.append(sym)
        h = one(sym)
        if h == "failed":
            print(f"  {sym}: download failed ({failed_why[sym][:90]}); will retry after this pass", flush=True)
        how[h] = how.get(h, 0) + 1
        if n % 50 == 0:
            el = time.time() - t0
            print(f"  {label}: {n}/{len(symbols)} stocks, {el / 60:.1f} min, about {el / n * (len(symbols) - n) / 60:.0f} min left", flush=True)
    # stocks Angel refused: retry later, more slowly, instead of keeping yesterday's prices
    for i, wait in enumerate(RETRY_PASSES, 1):
        again = [s_ for s_ in todo if s_ in failed_why]
        if not again:
            break
        print(f"  {label}: {len(again)} stock(s) failed; retry pass {i}/{len(RETRY_PASSES)} in {wait // 60} min: {', '.join(again[:15])}"
              f"{' ...' if len(again) > 15 else ''}", flush=True)
        time.sleep(wait)
        if hasattr(api, "interval"):
            api.interval = max(api.interval, 2.0)       # gentler for the retries
        for sym in again:
            h = one(sym)
            if h != "failed":
                how["failed"] -= 1
                how[h] = how.get(h, 0) + 1
                print(f"  {sym}: downloaded on retry pass {i}", flush=True)
    failed = [f"{s_} ({failed_why[s_].split(':')[0]})" for s_ in todo if s_ in failed_why]
    for sym in failed_why:
        print(f"  {sym}: STILL FAILED after {len(RETRY_PASSES)} retry passes ({failed_why[sym][:100]}); its last bar stays "
              f"{pd.Timestamp(by_sym[sym].date.max()).date() if sym in by_sym else 'none'} and it is left out of today's signals", flush=True)
    frames, raw_frames = [], []
    for sym in symbols:
        d = res.get(sym)
        if d is not None and len(d):
            frames.append(d.assign(symbol=sym))
        elif sym in tok and sym not in missing:
            missing.append(sym)
        r = raw_res.get(sym)
        if r is not None and len(r):
            raw_frames.append(r.assign(symbol=sym))
    px = pd.concat(frames, ignore_index=True)[["date", "symbol", "open", "high", "low", "close", "volume"]]
    if path.exists():
        shutil.copy2(path, path.with_suffix(".prev.parquet"))      # one previous copy, in case
    atomic.to_parquet(px, path)
    if raw_frames:
        raw_path.parent.mkdir(exist_ok=True)
        atomic.to_parquet(pd.concat(raw_frames, ignore_index=True)[["date", "symbol", "open", "high", "low", "close", "volume"]], raw_path)
    if log:
        sa = OUT / "split_adjustments.csv"
        new = pd.DataFrame(log)
        if sa.exists() and not full and sa.stat().st_size > 1:
            new = pd.concat([pd.read_csv(sa), new]).drop_duplicates(["symbol", "date"], keep="last")
        new.to_csv(sa, index=False)
    n_adj = sum(1 for x in log if x.get("note") == "adjusted")
    print(f"{label}: {px.symbol.nunique()} stocks, {len(px):,} rows, last bar {pd.Timestamp(px.date.max()).date()}. "
          f"New {how.get('new', 0)}, updated {how.get('update', 0)}, re-downloaded {how.get('refetch', 0)}, unchanged {how.get('none', 0)}. "
          f"Split/bonus fixes: {n_adj} (+{len(log) - n_adj} gaps left for review). Missing: {', '.join(missing[:20]) or 'none'}{' ...' if len(missing) > 20 else ''}"
          + (f". FAILED (kept old data): {', '.join(failed)}" if failed else ""), flush=True)
    return dict(stocks=int(px.symbol.nunique()), rows=int(len(px)), last_bar=str(pd.Timestamp(px.date.max()).date()),
                missing=missing, failed=failed, failed_detail=dict(failed_why), refetched=how.get("refetch", 0), new=how.get("new", 0), seconds=round(time.time() - t0),
                provider=dict(api.stats) if hasattr(api, "stats") else {})


def write_stale_report(summary, not_listed, path=None):
    """data/stale_symbols.json: every Nifty 500 stock without a bar for the latest day, and why. Read by the dashboard."""
    path = path or OUT / "stale_symbols.json"
    px = pd.read_parquet(OUT / "prices.parquet", columns=["date", "symbol"])
    px["date"] = pd.to_datetime(px.date)
    day = px.date.max()
    last = px.groupby("symbol").date.max()
    detail = summary.get("failed_detail", {})
    rows = []
    for sym in sorted(set(load_universe_cached().Symbol)):
        lb = last.get(sym)
        if lb is not None and lb >= day:
            continue
        why = (f"download failed: {detail[sym]}" if sym in detail else
               "not in Angel One's instrument list" if sym in not_listed else
               "no price history" if lb is None else f"Angel returned no bar for {day.date()} (not traded / suspended?)")
        rows.append(dict(symbol=sym, last_bar=None if lb is None else str(lb.date()), reason=why))
    out = dict(day=str(day.date()), checked=str(datetime.now().replace(microsecond=0)), stocks=rows)
    atomic.write_text(path, json.dumps(out, indent=1))
    if rows:
        print(f"Not up to date for {day.date()}: {len(rows)} stock(s), left out of today's screener and agents: "
              + ", ".join(r["symbol"] for r in rows[:30]) + (" ..." if len(rows) > 30 else ""), flush=True)
    return out


def load_universe_cached():
    return pd.read_csv(OUT / "universe.csv")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Download or update daily prices from Angel One.")
    ap.add_argument("--full", action="store_true", help="re-download all history (about 30 minutes)")
    ap.add_argument("--broad", action="store_true",
                    help="also download every other NSE stock (for survivorship-free tests; 3-4 hours the first time)")
    a = ap.parse_args()
    full = a.full or not (OUT / "prices.parquet").exists()
    print(f"Mode: {'full download' if full else 'update (only new days)'}" + (" + broad universe" if a.broad else ""), flush=True)
    api = AngelProvider(login, min_interval=SLEEP)       # logs in on the first request; re-logs in if the session expires
    uni = load_universe()

    # Angel instrument tokens (public scrip master)
    sm = pd.DataFrame(requests.get(
        "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json", timeout=60).json())
    nse = sm[sm.exch_seg == "NSE"]
    eq = nse[nse.symbol.str.endswith("-EQ")].assign(base=lambda x: x.symbol.str[:-3])
    tok = dict(zip(eq.base, eq.token))
    # A stock moved to the trade-for-trade segment is listed as SYMBOL-BE, not -EQ: same company, same daily bars.
    be = nse[nse.symbol.str.endswith("-BE")].assign(base=lambda x: x.symbol.str[:-3])
    be_tok = dict(zip(be.base, be.token))
    for sym in uni.Symbol:
        if sym not in tok and sym in be_tok:
            tok[sym] = be_tok[sym]
            print(f"  {sym}: not listed as -EQ in Angel's instrument list; using its -BE (trade-for-trade) series", flush=True)
    for sym in uni.Symbol:
        if sym not in tok:
            print(f"  {sym}: not in Angel One's instrument list (no -EQ or -BE series): no prices can be downloaded", flush=True)
    eq.assign(exchange="NSE").rename(columns={"base": "symbol_", "symbol": "trading_symbol"})[
        ["symbol_", "exchange", "token", "name", "trading_symbol"]].rename(columns={"symbol_": "symbol"}).to_csv(OUT / "instruments.csv", index=False)

    info = dict(when=str(datetime.now().replace(microsecond=0)), mode="full" if full else "update")
    info["nifty500"] = run_set(api, list(uni.Symbol), tok, OUT / "prices.parquet", full, "Nifty 500")

    if a.broad or (OUT / "prices_broad.parquet").exists():
        others = sorted(set(tok) - set(uni.Symbol))
        others = [s for s in others if s.replace("&", "").replace("-", "").isalnum()]
        bfull = full or not (OUT / "prices_broad.parquet").exists()
        print(f"Broad universe: {len(others)} other NSE stocks ({'full download' if bfull else 'update'})", flush=True)
        info["broad"] = run_set(api, others, tok, OUT / "prices_broad.parquet", bfull, "Other NSE stocks")

    # Nifty 50 and India VIX (index tokens from scrip master)
    ix = nse[nse.instrumenttype == "AMXIDX"]
    def idx_token(names):
        m = ix[ix.name.str.upper().isin(names) | ix.symbol.str.upper().isin(names)]
        return m.token.iat[0] if len(m) else None
    old_idx = None if full or not (OUT / "index.parquet").exists() else pd.read_parquet(OUT / "index.parquet")
    out = None
    for col, names in [("nifty", {"NIFTY", "NIFTY 50", "NIFTY50"}), ("vix", {"INDIA VIX", "INDIAVIX"})]:
        t = idx_token(names)
        if t is None:
            print(f"  could not find index token for {col}"); continue
        frm = START
        if old_idx is not None and col in old_idx and old_idx[col].notna().any():
            frm = pd.Timestamp(old_idx.loc[old_idx[col].notna(), "date"].max()).to_pydatetime() - timedelta(days=OVERLAP_DAYS)
        d = candles(api, t, frm=frm)[["date", "close"]].rename(columns={"close": col})
        if old_idx is not None and col in old_idx:
            prev = old_idx[["date", col]].copy()
            prev["date"] = pd.to_datetime(prev.date).dt.tz_localize(None)
            d = pd.concat([prev[prev.date < d.date.min()] if len(d) else prev, d]).dropna().drop_duplicates("date", keep="last")
        out = d if out is None else out.merge(d, on="date", how="outer")
    atomic.to_parquet(out.sort_values("date"), OUT / "index.parquet")
    info["index_last"] = str(pd.Timestamp(out.date.max()).date())
    print(f"Index data saved, last bar {info['index_last']}.", flush=True)

    write_stale_report(info["nifty500"], set(uni.Symbol) - set(tok))

    # data quality checks on what was just saved
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from core.data import load_raw
        from core import quality
        px, idx, _ = load_raw()
        _, qs = quality.run(px, idx)
        info["quality"] = {k: qs[k] for k in ("status", "errors", "warnings")}
        print(f"Data quality: {qs['status']} ({qs['errors']} errors, {qs['warnings']} warnings)"
              + (f"; no new signals for {', '.join(qs['quarantined'])} until reviewed" if qs["quarantined"] else ""), flush=True)
    except Exception as e:
        print(f"Data quality check failed: {e}")
    (OUT / "fetch_log.json").write_text(json.dumps(info, indent=2, default=str))
    print("Done.")
    try:
        if api.api is not None:
            api.api.terminateSession(creds()["ANGEL_CLIENT_CODE"])
    except Exception:
        pass


if __name__ == "__main__":
    main()
