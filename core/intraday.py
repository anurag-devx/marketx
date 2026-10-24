"""Intraday 5-minute bars from Angel One for the Nifty 500. Separate from the daily prices: nothing else reads these yet
except the Stock page's intraday chart.

Files (data/intraday/):
  5min/<SYMBOL>.parquet  ts (exchange time, IST, no zone), open, high, low, close, volume. Raw: NOT adjusted for splits
                         or bonuses (fine for charts; adjust before any long-range study).
  state.json             earliest date Angel serves (found by `probe`), and per stock the range already downloaded.

How far back: Angel doesn't publish its intraday history depth, so `probe` asks for one liquid stock at earlier and
earlier dates and keeps the earliest that returns bars. Requests are 90 days each (Angel's cap for FIVE_MINUTE is
around 100), paced and retried by core/provider.py. Resumable: each stock is saved as soon as it's done, and a stopped
run continues where it left off. A normal run first tops up stocks already downloaded, then fills the rest.

Run:  python -m core.intraday            top-up + continue the history download
      python -m core.intraday update     only top up stocks already downloaded (the daily step)
      python -m core.intraday probe      only find how far back Angel goes
      python -m core.intraday symbols=TCS,INFY   just these stocks"""
import json, sys, time
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
from core import config as C, atomic

DIR = C.DATA / "intraday"
BARS = DIR / "5min"
STATE = DIR / "state.json"
INTERVAL, CHUNK = "FIVE_MINUTE", 90
PROBE_SYMBOL = "RELIANCE"
FLOOR = pd.Timestamp("2010-01-01")
COLS = ["ts", "open", "high", "low", "close", "volume"]


def _now():
    return pd.Timestamp(datetime.now())


def state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"start": None, "symbols": {}}


def _save_state(s):
    DIR.mkdir(parents=True, exist_ok=True)
    atomic.write_text(STATE, json.dumps(s, indent=1))


def parse(rows):
    """Angel rows [[ts, o, h, l, c, v], ...] -> clean frame (bad rows dropped, one row per timestamp)."""
    if not rows:
        return pd.DataFrame(columns=COLS)
    d = pd.DataFrame(rows, columns=COLS)
    d["ts"] = pd.to_datetime(d.ts.astype(str).str[:19], errors="coerce")
    for c in COLS[1:]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d[d.ts.notna() & d[COLS[1:]].notna().all(axis=1) & (d.high >= d.low)]
    return d.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)


def fetch(prov, token, frm, to):
    """5-minute bars from `frm` to `to`, in 90-day requests."""
    out, a, end = [], pd.Timestamp(frm).normalize(), pd.Timestamp(to)
    while a <= end:
        b = min(a + timedelta(days=CHUNK - 1), end)
        out.append(parse(prov.request({"exchange": "NSE", "symboltoken": str(token), "interval": INTERVAL,
                                       "fromdate": a.strftime("%Y-%m-%d 09:15"), "todate": b.strftime("%Y-%m-%d 15:30")})))
        a = b + timedelta(days=1)
    out = [x for x in out if len(x)]
    return pd.concat(out, ignore_index=True).drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True) if out else pd.DataFrame(columns=COLS)


def probe(prov, token, log=print, today=None):
    """Earliest month with 5-minute bars for one stock: step back a year at a time, then narrow to the month."""
    today = pd.Timestamp(today or _now()).normalize()
    has = lambda d: len(parse(prov.request({"exchange": "NSE", "symboltoken": str(token), "interval": INTERVAL,
                                             "fromdate": d.strftime("%Y-%m-%d 09:15"),
                                             "todate": (d + timedelta(days=14)).strftime("%Y-%m-%d 15:30")}))) > 0
    good, d = today - timedelta(days=30), today - timedelta(days=30)
    if not has(good):
        raise RuntimeError("Angel returned no 5-minute bars even for last month")
    while d > FLOOR:
        d = d - pd.DateOffset(years=1)
        if has(d):
            good = d
            log(f"  bars exist around {d:%b %Y}")
        else:
            log(f"  nothing around {d:%b %Y}")
            break
    lo, hi = d, good                                     # lo: empty (or floor), hi: has bars
    while (hi - lo).days > 31:
        mid = lo + (hi - lo) / 2
        mid = pd.Timestamp(mid.date())
        if has(mid):
            hi = mid
        else:
            lo = mid
    log(f"Angel serves 5-minute bars back to about {hi:%b %Y}")
    return hi.normalize()


def load(symbol):
    p = BARS / f"{symbol}.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=COLS)


def _store(symbol, new):
    old = load(symbol)
    d = pd.concat([old, new], ignore_index=True) if len(old) else new
    d = d.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
    BARS.mkdir(parents=True, exist_ok=True)
    atomic.to_parquet(d, BARS / f"{symbol}.parquet", index=False)
    return d


def universe():
    u = pd.read_csv(C.DATA / "universe.csv")
    col = "Symbol" if "Symbol" in u else "symbol"
    return u[col].astype(str).tolist()


def tokens():
    t = pd.read_csv(C.DATA / "instruments.csv")
    return dict(zip(t.symbol.astype(str), t.token.astype(str)))


def run(prov, symbols=None, mode="all", log=print, today=None):
    """Top up stocks already downloaded, then (mode 'all') download the history of the rest. Returns a summary."""
    today = pd.Timestamp(today or _now())
    end = today.normalize() + timedelta(hours=15, minutes=30)
    close_mark = f"{today:%Y-%m-%d} 15:25"
    st = state()
    tok = tokens()
    syms = symbols or universe()
    if mode in ("all", "probe") and not st.get("start"):
        log(f"Finding how far back Angel's 5-minute history goes (using {PROBE_SYMBOL}) ...")
        st["start"] = str(probe(prov, tok[PROBE_SYMBOL], log, today).date())
        _save_state(st)
    if mode == "probe":
        return dict(start=st["start"])
    start = pd.Timestamp(st.get("start") or (today - timedelta(days=365)).date())
    have = st.setdefault("symbols", {})
    todo_up = [s for s in syms if s in have]
    todo_new = [s for s in syms if s not in have] if mode == "all" else []
    missing = [s for s in syms if s not in tok]
    if missing:
        log(f"  {len(missing)} stocks have no Angel token (skipped): {', '.join(missing[:8])}{' ...' if len(missing) > 8 else ''}")
    done, failed, t0 = 0, [], time.time()
    jobs_ = [(s, "update") for s in todo_up if s in tok] + [(s, "history") for s in todo_new if s in tok]
    log(f"{len([j for j in jobs_ if j[1] == 'update'])} stocks to top up, {len([j for j in jobs_ if j[1] == 'history'])} to download from {start:%d %b %Y}")
    for i, (s, kind) in enumerate(jobs_, 1):
        frm = pd.Timestamp(have[s]["to"]) if kind == "update" else start
        if kind == "update" and (have[s].get("last") or "") >= close_mark:
            continue                                 # already has today's last bar
        try:
            new = fetch(prov, tok[s], frm, end)
            d = _store(s, new) if len(new) else load(s)
            have[s] = dict(frm=str(start.date()) if kind == "history" else have[s]["frm"], to=str(end.date()),
                           bars=int(len(d)), first=str(d.ts.min()) if len(d) else None, last=str(d.ts.max()) if len(d) else None)
            _save_state(st)
            done += 1
        except Exception as e:                       # one stock failing doesn't stop the run
            failed.append(s)
            log(f"  {s}: {str(e)[:160]}")
        if i % 10 == 0 or i == len(jobs_):
            rate = i / max(time.time() - t0, 1e-6)
            log(f"  {i}/{len(jobs_)} stocks ({done} ok, {len(failed)} failed) · about {int((len(jobs_) - i) / max(rate, 1e-6) / 60)} min left")
    log(f"Intraday: {done} stocks saved, {len(failed)} failed" + (f" ({', '.join(failed[:10])})" if failed else "") + ".")
    return dict(done=done, failed=failed, start=str(start.date()))


class Busy(RuntimeError):
    pass


def _lock():
    """One intraday download at a time (the daily top-up and the history download share state.json)."""
    import os
    DIR.mkdir(parents=True, exist_ok=True)
    lk = DIR / ".lock"
    if lk.exists():
        try:
            os.kill(int(lk.read_text().strip()), 0)
            raise Busy("another intraday download is running")
        except (ValueError, ProcessLookupError, PermissionError):
            pass                                     # stale lock from a stopped run
    lk.write_text(str(os.getpid()))
    return lk


def summary():
    st = state()
    sy = st.get("symbols", {})
    return dict(start=st.get("start"), stocks=len(sy), bars=sum(int(v.get("bars") or 0) for v in sy.values()),
                last=max((v.get("last") or "" for v in sy.values()), default=None) or None)


def main(argv):
    sys.path.insert(0, str(C.BASE))
    from fetch_angel import login                      # reads ~/.angel_env (never printed)
    from core.provider import AngelProvider
    mode = "update" if "update" in argv else "probe" if "probe" in argv else "all"
    syms = next((a.split("=", 1)[1].split(",") for a in argv if a.startswith("symbols=")), None)
    try:
        lk = _lock()
    except Busy as e:
        print(f"Skipped: {e}; it tops up as well.")
        return
    try:
        prov = AngelProvider(login, min_interval=1.0, log=print)
        r = run(prov, syms, mode)
    finally:
        lk.unlink(missing_ok=True)
    if mode != "probe" and r["failed"] and len(r["failed"]) == r["done"] + len(r["failed"]):
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
