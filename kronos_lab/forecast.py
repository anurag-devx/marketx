"""Kronos forecasts (experimental, separate from the agents and the Debate Lab).

Runs only in its own environment (.venv-kronos, made by kronos_lab/setup.sh) with the Kronos model code in
kronos_lab/vendor/Kronos (MIT licence, https://github.com/shiyu-coder/Kronos). Reads data/prices.parquet; writes only
results/kronos/forecasts.parquet. Nothing else in MarketX reads these forecasts.

For each stock and as-of day it feeds the last `lookback` daily bars (up to and including that day's close, nothing
later) to the model and samples `draws` independent paths `h` sessions ahead. Stored per forecast:
  pred_ret_pct  mean predicted close-to-close return over h sessions
  p_up          share of sampled paths that end above today's close
  draw_sd_pct   spread of the sampled returns (the model's own uncertainty)
  ohlc          mean predicted candle per step [[open, high, low, close], ...]
  band          10th and 90th percentile of the sampled closes per step [[low, high], ...]
Each run also records its speed (results/kronos/speed.json) so the app can estimate how long the next run takes.

    .venv-kronos/bin/python -m kronos_lab.forecast                       latest day, Nifty 50
    .venv-kronos/bin/python -m kronos_lab.forecast replay=50 step=5     50 past days, 5 sessions apart
    options: date=YYYY-MM-DD  list=nifty50|top100|all  symbols=TCS,INFY  model=mini|small|base  h=5  lookback=400  draws=8
             temp=1.0 (sampling temperature)  top_p=0.9 (nucleus sampling)  device=cpu|mps
             freq=1d|5m  at=1100 (5m only: forecast from the bars before this time, h bars ahead, same session)
    .venv-kronos/bin/python -m kronos_lab.forecast --download            fetch the model weights only
"""
import json, sys, time
from pathlib import Path
import numpy as np, pandas as pd

BASE = Path(__file__).resolve().parents[1]
VENDOR = Path(__file__).resolve().parent / "vendor" / "Kronos"
OUT = BASE / "results" / "kronos"
FILE = OUT / "forecasts.parquet"
SPEED = OUT / "speed.json"
BARS5 = BASE / "data" / "intraday" / "5min"
MODELS = {"mini": ("NeoQuasar/Kronos-mini", "NeoQuasar/Kronos-Tokenizer-2k", 2048),
          "small": ("NeoQuasar/Kronos-small", "NeoQuasar/Kronos-Tokenizer-base", 512),
          "base": ("NeoQuasar/Kronos-base", "NeoQuasar/Kronos-Tokenizer-base", 512)}
COLS = ["as_of", "symbol", "model", "freq", "h", "lookback", "draws", "temp", "top_p", "close_asof", "pred_close", "pred_ret_pct", "p_up",
        "draw_sd_pct", "path", "ohlc", "band", "mode", "created"]
KEY = ["as_of", "symbol", "model", "freq", "h", "temp", "top_p"]
DEFAULTS = dict(freq="1d", temp=1.0, top_p=0.9, ohlc=None, band=None)
SESSION_END = "15:30"


def args(argv):
    a = dict(date=None, list="nifty50", symbols="", model="small", h=5, lookback=400, draws=8, temp=1.0, top_p=0.9, device="cpu",
             freq="1d", at="11:00", replay=0, step=5)
    for x in argv:
        if "=" in x:
            k, v = x.split("=", 1)
            if k in a:
                d = a[k]
                a[k] = int(v) if isinstance(d, int) and not isinstance(d, bool) else float(v) if isinstance(d, float) else v
    a["download"] = "--download" in argv
    at = str(a["at"]).replace(":", "")                   # job arguments can't carry ':' (at=1100 means 11:00)
    if not (at.isdigit() and len(at) in (3, 4) and 915 <= int(at) <= 1525 and int(at[-2:]) % 5 == 0):
        raise SystemExit("at must be a five-minute time between 09:15 and 15:25, e.g. at=1100")
    a["at"] = f"{int(at[:-2]):02d}:{at[-2:]}"
    if a["model"] not in MODELS:
        raise SystemExit(f"model must be one of {', '.join(MODELS)}")
    if a["freq"] not in ("1d", "5m"):
        raise SystemExit("freq must be 1d or 5m")
    if not (0.05 <= a["temp"] <= 3 and 0.05 <= a["top_p"] <= 1 and 1 <= a["draws"] <= 64 and 1 <= a["h"] <= 60):
        raise SystemExit("temp 0.05-3, top_p 0.05-1, draws 1-64, h 1-60")
    return a


def load_model(name, device):
    if not (VENDOR / "model").exists():
        raise SystemExit("Kronos isn't installed: run kronos_lab/setup.sh (Market Lab → Kronos → Run → Install).")
    sys.path.insert(0, str(VENDOR))
    from model import Kronos, KronosTokenizer, KronosPredictor
    mid, tid, ctx = MODELS[name]
    tok = KronosTokenizer.from_pretrained(tid)
    mod = Kronos.from_pretrained(mid)
    return KronosPredictor(mod, tok, device=device, max_context=ctx), ctx


def prices():
    px = pd.read_parquet(BASE / "data" / "prices.parquet", columns=["date", "symbol", "open", "high", "low", "close", "volume"])
    px["date"] = pd.to_datetime(px.date)
    try:                                              # same split/bonus fixes the rest of the app applies (pure pandas)
        sys.path.insert(0, str(BASE))
        from core.quality import apply_corporate_actions
        px, _ = apply_corporate_actions(px)
    except Exception:
        pass
    return px.sort_values(["symbol", "date"])


def bars5(symbols, since=None):
    """5-minute bars (data/intraday/5min) as one frame with the daily column names: date = bar start time."""
    out = []
    for s in symbols:
        f = BARS5 / f"{s}.parquet"
        if f.exists():
            g = pd.read_parquet(f).rename(columns={"ts": "date"})
            g = g.assign(symbol=s, date=pd.to_datetime(g["date"]))
            out.append(g[g.date >= since] if since is not None else g)
    return pd.concat(out, ignore_index=True).sort_values(["symbol", "date"]) if out else pd.DataFrame(columns=["date", "symbol", "open", "high", "low", "close", "volume"])


def universe(px, which, day, symbols=""):
    if symbols:
        return sorted({x.strip().upper() for x in symbols.split(",") if x.strip()})
    if which == "all":
        return sorted(px[px.date == day].symbol.unique())
    if which == "nifty50":
        try:
            sys.path.insert(0, str(BASE))
            from core.stocklists import index_members
            m = index_members("NIFTY 50")
            if m:
                return sorted(m)
        except Exception:
            pass
    n = 100 if which == "top100" else 50                 # fallback: most traded over the last 45 days
    r = px[(px.date > day - pd.Timedelta(days=45)) & (px.date <= day)]
    return list(r.assign(tv=r.close * r.volume).groupby("symbol").tv.mean().nlargest(n).index)


def future_days(day, h):
    return pd.Series(pd.bdate_range(day + pd.Timedelta(days=1), periods=h))   # weekdays; exchange holidays not known here


def future_bars(at, h):
    return pd.Series(pd.date_range(at, periods=h, freq="5min"))             # the next h five-minute bars, same session


def forecast_day(pred, px, syms, day, a, ctx):
    """Daily: day = the as-of session (its close is the last bar used). 5m: day = the as-of time; bars before it are used."""
    intraday = a.get("freq", "1d") == "5m"
    lb = min(int(a["lookback"]), ctx)
    dfs, xs, ys, keep, last_close = [], [], [], [], []
    by = px if isinstance(px, dict) else None              # {symbol: bars} (main() splits once) or one frame (tests)
    for s in syms:
        g = by.get(s) if by is not None else px[px.symbol == s]
        if g is None or not len(g):
            continue
        g = g[(g.date < day) if intraday else (g.date <= day)].tail(lb)
        if len(g) < lb or g[["open", "high", "low", "close", "volume"]].isna().any().any():
            continue                                      # not enough clean history
        if intraday:
            if g.date.iat[-1].date() != day.date() or g.date.iat[-1] < day - pd.Timedelta(minutes=5):
                continue                                  # no bar just before the as-of time
        elif g.date.iat[-1] != day:
            continue                                      # no bar on that day
        dfs.append(g[["open", "high", "low", "close", "volume"]].reset_index(drop=True))
        xs.append(g.date.reset_index(drop=True))
        ys.append(future_bars(day, a["h"]) if intraday else future_days(day, a["h"]))
        keep.append(s)
        last_close.append(float(g.close.iat[-1]))
    if not keep:
        return []
    draws = []
    for _ in range(int(a["draws"])):                     # independent sampled paths (the model averages within a call)
        out = pred.predict_batch(dfs, xs, ys, pred_len=int(a["h"]), T=float(a["temp"]), top_p=float(a["top_p"]), sample_count=1, verbose=False)
        draws.append(np.stack([np.stack([o[c].to_numpy() if c in o else o["close"].to_numpy() for c in ("open", "high", "low", "close")], axis=1)
                               for o in out]))       # (stocks, h, 4)
    cube = np.stack(draws)                                # (draws, stocks, h, 4)
    paths = cube[..., 3]                                  # closes (draws, stocks, h)
    c0 = np.array(last_close)
    ends = paths[:, :, -1] / c0 - 1                       # (draws, stocks)
    mean_path, mean_ohlc = paths.mean(axis=0), cube.mean(axis=0)
    lo, hi = np.percentile(paths, 10, axis=0), np.percentile(paths, 90, axis=0)
    r2 = lambda v: round(float(v), 2)
    rows = []
    for i, s in enumerate(keep):
        rows.append(dict(as_of=day if intraday else day.normalize(), symbol=s, model=a["model"], freq=a.get("freq", "1d"), h=int(a["h"]), lookback=lb,
                         draws=int(a["draws"]), temp=float(a["temp"]), top_p=float(a["top_p"]),
                         close_asof=c0[i], pred_close=float(mean_path[i, -1]), pred_ret_pct=float(100 * ends[:, i].mean()),
                         p_up=float((ends[:, i] > 0).mean()), draw_sd_pct=float(100 * ends[:, i].std()),
                         path=json.dumps([r2(v) for v in mean_path[i]]),
                         ohlc=json.dumps([[r2(x) for x in step] for step in mean_ohlc[i]]),
                         band=json.dumps([[r2(lo[i, k]), r2(hi[i, k])] for k in range(lo.shape[1])])))
    return rows


def load():
    if not FILE.exists():
        return pd.DataFrame(columns=COLS)
    d = pd.read_parquet(FILE)
    for k, v in DEFAULTS.items():                         # files written before these columns existed
        if k not in d:
            d[k] = v
        elif v is not None:
            d[k] = d[k].fillna(v)
    return d


def save(rows, mode):
    if not rows:
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    new = pd.DataFrame(rows).assign(mode=mode, created=pd.Timestamp.now().floor("s"))
    for k, v in DEFAULTS.items():
        if k not in new:
            new[k] = v
    old = load()
    d = pd.concat([old, new], ignore_index=True) if len(old) else new
    d["as_of"] = pd.to_datetime(d.as_of)
    d = d.drop_duplicates(KEY, keep="last")
    tmp = FILE.with_suffix(".tmp")
    d[COLS].to_parquet(tmp, index=False)
    tmp.replace(FILE)
    return len(new)


def record_speed(a, seconds, units):
    """Seconds per (stock x path x step) for this model / device / bar size, averaged with earlier runs."""
    if units <= 0 or seconds <= 0:
        return
    k = f"{a['model']}|{a['device']}|{a.get('freq', '1d')}"
    try:
        sp = json.loads(SPEED.read_text())
    except (OSError, ValueError):
        sp = {}
    v = seconds / units
    old = sp.get(k)
    sp[k] = dict(sec_per_unit=round(v if not old else 0.5 * old["sec_per_unit"] + 0.5 * v, 5), runs=(old or {}).get("runs", 0) + 1,
                 lookback=int(a["lookback"]), updated=str(pd.Timestamp.now().floor("s")))
    OUT.mkdir(parents=True, exist_ok=True)
    SPEED.write_text(json.dumps(sp, indent=1))


def targets_5m(a):
    """As-of times for 5-minute forecasts: `at` on the latest day with bars, or on past days for a replay."""
    ref = next((p for p in [BARS5 / "RELIANCE.parquet", *sorted(BARS5.glob("*.parquet"))[:1]] if p.exists()), None)
    if ref is None:
        raise SystemExit("No 5-minute bars yet: Data health → download 5-minute bars first.")
    days = sorted(pd.to_datetime(pd.read_parquet(ref, columns=["ts"]).ts).dt.normalize().unique())
    hh, mm = (int(x) for x in a["at"].split(":"))
    at = lambda d: pd.Timestamp(d) + pd.Timedelta(hours=hh, minutes=mm)
    if at(days[-1]) + pd.Timedelta(minutes=5 * int(a["h"])) > pd.Timestamp(days[-1]) + pd.Timedelta(hours=15, minutes=30):
        raise SystemExit(f"{a['at']} + {a['h']} bars runs past the 15:30 close: pick an earlier time or fewer bars")
    if a["date"]:
        days = [d for d in days if d <= pd.Timestamp(a["date"])]
    if a["replay"]:
        return [at(d) for d in days[::-1][:: max(1, int(a["step"]))][: int(a["replay"])][::-1]]
    return [at(days[-1])]


def main(argv):
    a = args(argv)
    t0 = time.time()
    print(f"Kronos-{a['model']} on {a['device']} · temperature {a['temp']} · top_p {a['top_p']} · loading the model (first time downloads it)", flush=True)
    pred, ctx = load_model(a["model"], a["device"])
    if a["download"]:
        print("Model ready.")
        return
    if a["freq"] == "5m":
        targets = targets_5m(a)
        mode = "replay"                                   # stored bars: the outcome is already known
        px0 = prices()
        syms = universe(px0, a["list"], px0.date.max(), a["symbols"])
        px = bars5(syms, since=min(targets) - pd.Timedelta(days=21))
        what = f"{a['h']} five-minute bars after {a['at']}"
    else:
        px = prices()
        days = sorted(px.date.unique())
        last = pd.Timestamp(days[-1])
        if a["replay"]:
            stop = pd.Timestamp(a["date"]) if a["date"] else last
            pool = [pd.Timestamp(d) for d in days if pd.Timestamp(d) <= stop][: -int(a["h"])]   # only days whose outcome is already known
            targets = pool[::-1][:: max(1, int(a["step"]))][: int(a["replay"])][::-1]
            mode = "replay"
        else:
            targets = [pd.Timestamp(a["date"]) if a["date"] else last]
            mode = "live" if targets[0] == last else "replay"
        syms = universe(px, a["list"], targets[-1], a["symbols"])
        what = f"{a['h']} sessions ahead"
    print(f"{len(syms)} stocks · {len(targets)} run(s) · {what} · {a['draws']} sampled paths each", flush=True)
    total, work, units = 0, 0.0, 0
    by = {s: g for s, g in px[px.symbol.isin(syms)].groupby("symbol")}
    for i, day in enumerate(targets, 1):
        t1 = time.time()
        rows = forecast_day(pred, by, syms, day, a, ctx)
        work += time.time() - t1
        units += len(rows) * int(a["draws"]) * int(a["h"])
        total += save(rows, mode)
        stamp = day.strftime("%Y-%m-%d %H:%M" if a["freq"] == "5m" else "%Y-%m-%d")
        print(f"  {stamp}: {len(rows)} forecasts ({i}/{len(targets)}, {time.time() - t0:.0f} s)", flush=True)
    record_speed(a, work, units)
    print(f"Done: {total} forecasts saved to results/kronos/forecasts.parquet in {time.time() - t0:.0f} s.")


if __name__ == "__main__":
    main(sys.argv[1:])
