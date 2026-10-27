"""NSE cash-market bhavcopy with delivery: one public CSV per trading day, every NSE security.

Source (public, no keys): https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv
  SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY,
  TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER   (column names carry spaces; '-' where there is no delivery figure)
Published around 18:00 IST on the trading day, so it is inside Market Lab's 21:00 cut-off (point-in-time).

Stored (data/bhavcopy/daily.parquet), equity series only (EQ, BE, BZ, RR, IV): date, symbol, series, prev_close, open,
high, low, close, avg_price, volume, turnover_lacs, trades, deliv_qty, deliv_pct.

Also:
  check()   NSE's official close vs MarketX's prices (Angel) for the last sessions: mismatches and missing bars
            (data/bhavcopy/check.json, shown in Data health).
  fill()    adds a missing Nifty 500 daily bar from the bhavcopy (only for the last few sessions, only after the stock's last
            Angel bar, only when the price is in line with it: no split guesswork). Angel's next download overwrites it.
Run: python -m core.bhavcopy [update | backfill start=2021-01-01 | check]"""
import io, json, sys, time
import numpy as np, pandas as pd
from .config import DATA
from . import atomic

DIR = DATA / "bhavcopy"
DAILY, STATE, CHECK = DIR / "daily.parquet", DIR / "state.json", DIR / "check.json"
URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{:%d%m%Y}.csv"
SERIES = ("EQ", "BE", "BZ", "RR", "IV")
COLS = ["date", "symbol", "series", "prev_close", "open", "high", "low", "close", "avg_price", "volume", "turnover_lacs", "trades",
        "deliv_qty", "deliv_pct"]
SRC = {"SYMBOL": "symbol", "SERIES": "series", "DATE1": "date", "PREV_CLOSE": "prev_close", "OPEN_PRICE": "open", "HIGH_PRICE": "high",
       "LOW_PRICE": "low", "CLOSE_PRICE": "close", "AVG_PRICE": "avg_price", "TTL_TRD_QNTY": "volume", "TURNOVER_LACS": "turnover_lacs",
       "NO_OF_TRADES": "trades", "DELIV_QTY": "deliv_qty", "DELIV_PER": "deliv_pct"}
MISMATCH = 0.005          # official close vs ours: more than 0.5% apart is reported
FILL_DAYS = 5


def parse(text):
    """One day's CSV -> rows (equity series). ValueError if the layout is unknown."""
    df = pd.read_csv(io.StringIO(text), dtype=str)
    df.columns = [c.strip().upper() for c in df.columns]
    missing = [c for c in SRC if c not in df.columns]
    if missing:
        raise ValueError(f"unknown bhavcopy layout (missing {missing[:3]})")
    df = df[list(SRC)].rename(columns=SRC)
    for c in df:
        df[c] = df[c].astype(str).str.strip()
    df = df[df.series.isin(SERIES)].copy()
    df["date"] = pd.to_datetime(df.date, format="%d-%b-%Y", errors="coerce")
    for c in COLS[3:]:
        df[c] = pd.to_numeric(df[c].str.replace(",", ""), errors="coerce")
    return df.dropna(subset=["date", "close"])[COLS]


def daily():
    if not DAILY.exists():
        return pd.DataFrame(columns=COLS)
    d = pd.read_parquet(DAILY)
    d["date"] = pd.to_datetime(d.date)
    return d


def state():
    try:
        return json.loads(STATE.read_text()) if STATE.exists() else {}
    except Exception:
        return {}


def _save(new, st):
    from .filelock import locked
    DIR.mkdir(parents=True, exist_ok=True)
    with locked("bhavcopy"):
        if len(new):
            d = pd.concat([x for x in (daily(), new) if len(x)], ignore_index=True).drop_duplicates(["date", "symbol", "series"], keep="last")
            atomic.to_parquet(d.sort_values(["date", "symbol"]).reindex(columns=COLS), DAILY)
        atomic.write_text(STATE, json.dumps(st, indent=1, default=str))


def fetch_days(days, fetch, log=print, save_every=25):
    """Downloads each trading day not yet done. Resumable through state.json; a 404 is remembered (holiday or not published)."""
    st = state()
    done, missing = set(st.get("done", [])), st.setdefault("missing", {})
    todo = [pd.Timestamp(d) for d in days if f"{pd.Timestamp(d):%Y-%m-%d}" not in done]
    log(f"Bhavcopy: {len(todo)} trading day(s) to download")
    batch, n, t0 = [], 0, time.time()
    for i, day in enumerate(todo, 1):
        k = f"{day:%Y-%m-%d}"
        try:
            batch.append(parse(fetch.get(URL.format(day)).decode("utf-8", errors="replace")))
            done.add(k); n += 1
        except FileNotFoundError:
            missing[k] = "404"; done.add(k)
        except ValueError as e:
            log(f"{k}: {e}. Stopping; the file layout changed — keep this message for troubleshooting.")
            break
        except Exception as e:
            log(f"{k}: {type(e).__name__} (retried next run)")
        if i % save_every == 0 or i == len(todo):
            st["done"] = sorted(done)
            _save(pd.concat(batch, ignore_index=True) if batch else pd.DataFrame(), st)
            batch = []
            log(f"  {i}/{len(todo)} days ({time.time() - t0:.0f} s)")
    st.update(done=sorted(done), last_update=str(pd.Timestamp.now().floor("s")))
    _save(pd.DataFrame(), st)
    log(f"Bhavcopy: {n} day(s) read.")
    return n


def _today_published(day):
    """Today's file appears around 18:00 IST; earlier, today is skipped (asked again next run)."""
    now = pd.Timestamp.now()
    return pd.Timestamp(day).normalize() < now.normalize() or now.hour >= 18


def update(days=10, log=print, fetch=None):
    from .derivatives import trading_days
    from .fundamentals import Fetcher
    ds = [d for d in trading_days(pd.Timestamp.now().normalize() - pd.Timedelta(days=days * 2)) if _today_published(d)]
    n = fetch_days(ds, fetch or Fetcher(gap=1.2), log)
    try:
        check(log=log)
        fill(log=log)
    except Exception as e:
        log(f"Price check skipped: {type(e).__name__}: {str(e)[:120]}")
    return n


def backfill(start="2021-01-01", log=print, fetch=None):
    from .derivatives import trading_days
    from .fundamentals import Fetcher
    return fetch_days([d for d in trading_days(start) if _today_published(d)], fetch or Fetcher(gap=1.2), log)


# ---------------- price check and fill ----------------
def _universe():
    try:
        return set(pd.read_csv(DATA / "universe.csv").Symbol.astype(str))
    except Exception:
        return set()


def check(sessions=10, log=print, px=None, bh=None):
    """Official close vs MarketX's daily bars for the Nifty 500, last `sessions` days in both."""
    px = pd.read_parquet(DATA / "prices.parquet", columns=["date", "symbol", "close"]) if px is None else px
    bh = daily() if bh is None else bh
    if bh.empty:
        out = dict(ready=False, why="no bhavcopy downloaded yet")
    else:
        uni = _universe() or set(px.symbol)
        px = px.assign(date=pd.to_datetime(px.date).astype("datetime64[ns]"))
        bh = bh.assign(date=pd.to_datetime(bh.date).astype("datetime64[ns]"))
        days = sorted(set(px.date.unique()) & set(bh.date.unique()))[-sessions:]
        b = bh[bh.date.isin(days) & bh.symbol.isin(uni)].sort_values("series").drop_duplicates(["date", "symbol"])   # EQ first
        p = px[px.date.isin(days) & px.symbol.isin(uni)]
        m = b.merge(p, on=["date", "symbol"], how="left", suffixes=("_nse", ""))
        m["diff"] = m.close / m.close_nse - 1
        bad = m[m.close.notna() & (m["diff"].abs() > MISMATCH)]
        miss = m[m.close.isna()]
        out = dict(ready=True, built=str(pd.Timestamp.now().floor("s")), days=[str(pd.Timestamp(d).date()) for d in days], compared=int(m.close.notna().sum()),
                   mismatches=[dict(date=str(r.date.date()), symbol=r.symbol, nse=float(r.close_nse), ours=float(r.close), diff_pct=round(100 * r.diff, 2))
                               for r in bad.sort_values("date").itertuples()][-50:],
                   missing=[dict(date=str(r.date.date()), symbol=r.symbol, nse=float(r.close_nse)) for r in miss.sort_values("date").itertuples()][-50:])
        log(f"Price check: {out['compared']} bars compared with NSE over {len(days)} session(s): {len(bad)} differ by more than "
            f"{100 * MISMATCH:.1f}%, {len(miss)} missing in MarketX.")
    DIR.mkdir(parents=True, exist_ok=True)
    atomic.write_text(CHECK, json.dumps(out, indent=1, default=str))
    return out


def fill(log=print, path=None, bh=None):
    """Adds missing Nifty 500 bars for the last FILL_DAYS sessions, after the stock's last bar, when the official previous
    close agrees with ours within 2% (so no split or bonus in between). Returns the rows added."""
    path = path or DATA / "prices.parquet"
    px = pd.read_parquet(path)
    bh = daily() if bh is None else bh
    if bh.empty or px.empty:
        return []
    px["date"] = pd.to_datetime(px.date)
    bh = bh.assign(date=pd.to_datetime(bh.date))
    uni = _universe() or set(px.symbol)
    recent = sorted(bh.date.unique())[-FILL_DAYS:]
    last = px.groupby("symbol").agg(last_date=("date", "max"))
    lastc = px.sort_values("date").groupby("symbol").close.last()
    add = []
    b = bh[bh.date.isin(recent) & bh.symbol.isin(uni)].sort_values(["series", "date"]).drop_duplicates(["date", "symbol"]).sort_values("date")
    for r in b.itertuples():
        if r.symbol not in last.index or r.date <= last.at[r.symbol, "last_date"]:
            continue
        ref = add[-1]["close"] if add and add[-1]["symbol"] == r.symbol else lastc[r.symbol]
        if not (r.prev_close and ref and abs(r.prev_close / ref - 1) <= 0.02):
            continue
        add.append(dict(date=r.date, symbol=r.symbol, open=r.open, high=r.high, low=r.low, close=r.close, volume=float(r.volume)))
        last.at[r.symbol, "last_date"] = r.date
    if add:
        new = pd.concat([px, pd.DataFrame(add).astype({"date": px.date.dtype})], ignore_index=True).sort_values(["symbol", "date"])
        atomic.to_parquet(new.reset_index(drop=True), path)
        DIR.mkdir(parents=True, exist_ok=True)
        log_f = DIR / "fills.json"
        old = json.loads(log_f.read_text()) if log_f.exists() else []
        atomic.write_text(log_f, json.dumps((old + [dict(r, date=str(pd.Timestamp(r["date"]).date()), at=str(pd.Timestamp.now().floor("s"))) for r in add])[-500:], indent=1))
    log(f"Price fill: {len(add)} missing bar(s) added from the NSE bhavcopy." if add else "Price fill: nothing missing.")
    return add


# ---------------- reading (Desk, TA, Market Lab) ----------------
def delivery(symbol, until=None, n=60):
    """The stock's recent delivery: date, deliv_pct, deliv_qty, volume (EQ series first)."""
    d = daily()
    d = d[d.symbol == symbol]
    if until is not None:
        d = d[d.date <= pd.Timestamp(until)]
    d = d.sort_values("series").drop_duplicates("date").sort_values("date").tail(n)
    return d[["date", "deliv_pct", "deliv_qty", "volume", "close"]].reset_index(drop=True)


def delivery_read(symbol, until=None):
    """Latest delivery % vs the stock's previous 20 sessions (a description; not a tested signal)."""
    d = delivery(symbol, until, 25).dropna(subset=["deliv_pct"])
    if len(d) < 6:
        return None
    last, base = d.iloc[-1], d.iloc[:-1].tail(20)
    avg = float(base.deliv_pct.mean())
    q = float(base.deliv_qty.mean()) if base.deliv_qty.notna().any() else np.nan
    return dict(date=str(last.date.date()), deliv_pct=float(last.deliv_pct), avg20=round(avg, 1), diff_pts=round(float(last.deliv_pct) - avg, 1),
                qty_x=None if not np.isfinite(q) or q <= 0 else round(float(last.deliv_qty) / q, 2))


if __name__ == "__main__":
    args = dict(a.split("=", 1) for a in sys.argv[2:] if "=" in a)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "update"
    if cmd == "backfill":
        backfill(args.get("start", "2021-01-01"))
    elif cmd == "check":
        check()
    else:
        update(int(args.get("days", 10)))
