"""Kronos forecasts (experimental, separate from the agents and the Debate Lab).

Runs only in its own environment (.venv-kronos, made by kronos_lab/setup.sh) with the Kronos model code in
kronos_lab/vendor/Kronos (MIT licence, https://github.com/shiyu-coder/Kronos). Reads data/prices.parquet; writes only
results/kronos/forecasts.parquet. Nothing else in MarketX reads these forecasts.

For each stock and as-of day it feeds the last `lookback` daily bars (up to and including that day's close, nothing
later) to the model and samples `draws` independent paths `h` sessions ahead. Stored per forecast:
  pred_ret_pct  mean predicted close-to-close return over h sessions
  p_up          share of sampled paths that end above today's close
  draw_sd_pct   spread of the sampled returns (the model's own uncertainty)

    .venv-kronos/bin/python -m kronos_lab.forecast                       latest day, Nifty 50
    .venv-kronos/bin/python -m kronos_lab.forecast replay=50 step=5     50 past days, 5 sessions apart
    options: date=YYYY-MM-DD  list=nifty50|top100|all  model=small|mini|base  h=5  lookback=400  draws=8  device=cpu|mps
    .venv-kronos/bin/python -m kronos_lab.forecast --download            fetch the model weights only
"""
import json, sys, time
from pathlib import Path
import numpy as np, pandas as pd

BASE = Path(__file__).resolve().parents[1]
VENDOR = Path(__file__).resolve().parent / "vendor" / "Kronos"
OUT = BASE / "results" / "kronos"
FILE = OUT / "forecasts.parquet"
MODELS = {"mini": ("NeoQuasar/Kronos-mini", "NeoQuasar/Kronos-Tokenizer-2k", 2048),
          "small": ("NeoQuasar/Kronos-small", "NeoQuasar/Kronos-Tokenizer-base", 512),
          "base": ("NeoQuasar/Kronos-base", "NeoQuasar/Kronos-Tokenizer-base", 512)}
COLS = ["as_of", "symbol", "model", "h", "lookback", "draws", "close_asof", "pred_close", "pred_ret_pct", "p_up", "draw_sd_pct",
        "path", "mode", "created"]


def args(argv):
    a = dict(date=None, list="nifty50", model="small", h=5, lookback=400, draws=8, device="cpu", replay=0, step=5)
    for x in argv:
        if "=" in x:
            k, v = x.split("=", 1)
            if k in a:
                a[k] = type(a[k])(v) if isinstance(a[k], int) else v
    a["download"] = "--download" in argv
    return a


def load_model(name, device):
    if not (VENDOR / "model").exists():
        raise SystemExit("Kronos isn't installed: run kronos_lab/setup.sh (Kronos page → Install).")
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


def universe(px, which, day):
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


def forecast_day(pred, px, syms, day, a, ctx):
    lb = min(int(a["lookback"]), ctx)
    dfs, xs, ys, keep, last_close = [], [], [], [], []
    for s in syms:
        g = px[(px.symbol == s) & (px.date <= day)].tail(lb)
        if len(g) < lb or g.date.iat[-1] != day or g[["open", "high", "low", "close", "volume"]].isna().any().any():
            continue                                      # not enough clean history, or no bar on that day
        dfs.append(g[["open", "high", "low", "close", "volume"]].reset_index(drop=True))
        xs.append(g.date.reset_index(drop=True))
        ys.append(future_days(day, a["h"]))
        keep.append(s)
        last_close.append(float(g.close.iat[-1]))
    if not keep:
        return []
    paths = []
    for _ in range(int(a["draws"])):                     # independent sampled paths (the model averages within a call)
        out = pred.predict_batch(dfs, xs, ys, pred_len=int(a["h"]), T=1.0, top_p=0.9, sample_count=1, verbose=False)
        paths.append(np.stack([o["close"].to_numpy() for o in out]))   # (stocks, h)
    paths = np.stack(paths)                               # (draws, stocks, h)
    c0 = np.array(last_close)
    ends = paths[:, :, -1] / c0 - 1                       # (draws, stocks)
    mean_path = paths.mean(axis=0)
    rows = []
    for i, s in enumerate(keep):
        rows.append(dict(as_of=day.normalize(), symbol=s, model=a["model"], h=int(a["h"]), lookback=lb, draws=int(a["draws"]),
                         close_asof=c0[i], pred_close=float(mean_path[i, -1]), pred_ret_pct=float(100 * ends[:, i].mean()),
                         p_up=float((ends[:, i] > 0).mean()), draw_sd_pct=float(100 * ends[:, i].std()),
                         path=json.dumps([round(float(v), 2) for v in mean_path[i]])))
    return rows


def save(rows, mode):
    if not rows:
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    new = pd.DataFrame(rows).assign(mode=mode, created=pd.Timestamp.now().floor("s"))
    old = pd.read_parquet(FILE) if FILE.exists() else pd.DataFrame(columns=COLS)
    d = pd.concat([old, new], ignore_index=True).drop_duplicates(["as_of", "symbol", "model", "h"], keep="last")
    tmp = FILE.with_suffix(".tmp")
    d[COLS].to_parquet(tmp, index=False)
    tmp.replace(FILE)
    return len(new)


def main(argv):
    a = args(argv)
    t0 = time.time()
    print(f"Kronos-{a['model']} on {a['device']} · loading the model (first time downloads it)", flush=True)
    pred, ctx = load_model(a["model"], a["device"])
    if a["download"]:
        print("Model ready.")
        return
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
    syms = universe(px, a["list"], targets[-1])
    print(f"{len(syms)} stocks · {len(targets)} day(s) · {a['h']} sessions ahead · {a['draws']} sampled paths each", flush=True)
    total = 0
    for i, day in enumerate(targets, 1):
        rows = forecast_day(pred, px, syms, day, a, ctx)
        total += save(rows, mode)
        print(f"  {day.date()}: {len(rows)} forecasts ({i}/{len(targets)}, {time.time() - t0:.0f} s)", flush=True)
    print(f"Done: {total} forecasts saved to results/kronos/forecasts.parquet in {time.time() - t0:.0f} s.")


if __name__ == "__main__":
    main(sys.argv[1:])
