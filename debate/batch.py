"""Nightly forecast batch: the code-only part of the Market Debate Lab for a chosen list of stocks.

For every stock in the saved selection (default: Nifty 50) and every MAIN target (5 sessions vs the Nifty; 5 sessions
absolute), it records the probability engine's forecast and the judge's rule-based verdict — no language model, no
bull/bear text. The model and the signal tests learn from the whole liquid universe; only the forecasts are limited
to the selection.

Records go to data/debate/batch_forecasts.parquet, one row per (analysis day, stock, target), never overwritten.
debate/evaluate.py scores them once the horizon has passed; the Forecast scorecard page reports the results.

Runs as the last step of 'Fetch data + run all agents' (if enabled), or:
  python3 -m debate.batch                 forecasts for the latest day (live)
  python3 -m debate.batch days=20         also replays the previous 20 sessions (labelled retrospective)"""
import hashlib, json, sys, time
import numpy as np, pandas as pd
from core import config as C, atomic, stocklists as SL
from core.filelock import locked
from . import VERSIONS, signals as SG, stats as S, probability as PR, judge as J, evidence as E, store

CONFIG = store.DIR / "batch.json"
FORECASTS = store.DIR / "batch_forecasts.parquet"
MAIN_TARGETS = [{"h": 5, "target": "rel", "role": "main"}, {"h": 5, "target": "abs", "role": "second"}]
DEFAULT = {"enabled": True, "selection": SL.DEFAULT, "targets": MAIN_TARGETS}


def config():
    try:
        c = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    except Exception:
        c = {}
    return {**DEFAULT, **c}


def save_config(c):
    atomic.write_text(CONFIG, json.dumps({**config(), **c}, indent=1))


def selection_hash(sel):
    return hashlib.sha1(json.dumps(sel, sort_keys=True).encode()).hexdigest()[:10]


def load():
    if not FORECASTS.exists():
        return pd.DataFrame()
    return pd.read_parquet(FORECASTS)


def _need_indices(sel, log):
    for r in sel.get("include", []) + sel.get("exclude", []):
        if r.get("type") == "index" and r.get("name") in SL.INDICES:
            SL.refresh_index(r["name"], log)


def forecast_day(ctx, day, symbols, h, target, band, sel, role, latest, log):
    """Forecast rows for one analysis day (ctx already holds the panel for horizon h)."""
    from .engine import spec_for, universe_tests, _regime
    ctx.as_of = pd.Timestamp(day).normalize()
    ctx.cutoff = E.cutoff_ts(ctx.as_of)
    mrow = ctx.panel[ctx.panel.date == ctx.as_of]
    if mrow.empty:
        log(f"  {ctx.as_of.date()}: no trading data, skipped")
        return []
    regime = _regime(mrow.iloc[0])
    spec0 = spec_for("ALL", h, target, band, ctx.as_of)
    sig_res = universe_tests(ctx, spec0, regime, lambda *a: None)
    model = PR.build_model(ctx.panel, h, target, spec0["band"], ctx.as_of, log=lambda *a: None, stamp=ctx.stamp)
    created = E.now_ist()
    mode = "live" if ctx.as_of >= latest else "retrospective"
    rows = []
    for sym in symbols:
        px_items, last = E.price_evidence(ctx, sym)
        info = dict(symbol=sym, exchange="NSE")
        base = dict(analysis_id=f"B-{ctx.as_of:%Y%m%d}-{sym}-{target}{h}", version=1, source="batch", mode=mode, role=role,
                    list_name=sel.get("name"), selection=SL.describe(sel), selection_hash=selection_hash(sel), symbol=sym,
                    as_of=str(ctx.as_of.date()), cutoff=E.ist(ctx.cutoff), created=created, horizon=h, target=target, band=spec0["band"],
                    regime=regime, model_key=model["key"], calibration=model["calibration"], versions=json.dumps(VERSIONS))
        if last is None or pd.Timestamp(last.date) != ctx.as_of:
            rows.append(dict(base, verdict="Forecast unavailable", reasons="no bar on the analysis day", sector=None))
            continue
        items = px_items + E.signal_evidence(ctx, sym) + E.news_evidence(ctx, sym, days=5)
        ledger = []
        for e in items:
            if e["id"].startswith("SG-"):
                k = e["id"][3:]
                ledger.append(dict(claim_id=e["id"], side=SG.BY_ID[k]["side"], group=SG.BY_ID[k]["group"],
                                   status=sig_res.get(k, {}).get("status", S.INSUFF)))
        unverified = [e["id"] for e in items if e["id"].startswith("NW-") and (e.get("extra") or {}).get("event_type") in E.MATERIAL
                      and "unverified" in str((e.get("extra") or {}).get("verification", ""))
                      and pd.Timestamp(e["published"][:19]) >= ctx.cutoff - pd.Timedelta(days=3)]
        row = ctx.panel[(ctx.panel.symbol == sym) & (ctx.panel.date == ctx.as_of)]
        fc = PR.forecast(model, row)
        spec = spec_for(sym, h, target, band, ctx.as_of, float(last.close))
        v = J.decide(spec, info, last, items, ledger, model, fc, ctx, unverified)
        p, b = v["probabilities"], v["base_rates"]
        sup = lambda side: sum(1 for r in ledger if r["side"] == side and r["status"] in (S.SUPPORTED, S.PARTIAL))
        rows.append(dict(base, sector=last.sector, reference_price=float(last.close), p_up=p.get("up"), p_down=p.get("down"),
                         p_neutral=p.get("neutral"), base_up=b.get("up"), edge=v["edge"], verdict=v["verdict"], probability_label=v["probability_label"],
                         reasons="; ".join(v["reasons"]), active_bull=",".join(r["claim_id"][3:] for r in ledger if r["side"] == "bull"),
                         active_bear=",".join(r["claim_id"][3:] for r in ledger if r["side"] == "bear"), supported_bull=sup("bull"),
                         supported_bear=sup("bear"), net_groups=v["net_supported_groups"], quality=px_items[0].get("quality") if px_items else None))
    return rows


def append(rows):
    """Adds new rows; rows whose (as_of, symbol, target, horizon) already exist are kept as they were (never overwritten)."""
    if not rows:
        return 0
    new = pd.DataFrame(rows)
    with locked("debate"):
        old = load()
        if len(old):
            have = set(zip(old.as_of, old.symbol, old.target, old.horizon))
            new = new[[(a, s, t, h) not in have for a, s, t, h in zip(new.as_of, new.symbol, new.target, new.horizon)]]
            allr = pd.concat([old, new], ignore_index=True) if len(new) else old
        else:
            allr = new
        if len(new):
            atomic.to_parquet(allr, FORECASTS)
    return len(new)


def run(days=0, as_of=None, log=print, data=None, cfg=None):
    """Forecasts for the latest day (or as_of), plus `days` earlier sessions replayed (retrospective)."""
    t0 = time.time()
    cfg = cfg or config()
    if not cfg.get("enabled", True) and as_of is None and not days:
        log("Nightly batch is switched off (Market Debate Lab → Nightly batch).")
        return 0
    sel = cfg["selection"]
    if data is None:
        _need_indices(sel, log)
    avail = set(data["px"].symbol) if data is not None else None
    symbols, notes = SL.resolve(sel, available=avail)
    for n in notes:
        log("  " + n)
    if not symbols:
        log(f"Selection '{sel.get('name')}' has no stocks with price data; nothing to forecast.")
        return 0
    log(f"Nightly batch: {len(symbols)} stocks ({sel.get('name')}: {SL.describe(sel)}), targets "
        + ", ".join(f"{t['h']} sessions {t['target']}" for t in cfg["targets"]))
    total = 0
    by_h = {}
    for t in cfg["targets"]:
        by_h.setdefault(int(t["h"]), []).append(t)
    for h, targets in by_h.items():
        ctx = E.Context(h, as_of, log=log, data=data)
        latest = ctx.panel.date.max()
        end = pd.Timestamp(as_of).normalize() if as_of else latest
        dates = np.sort(ctx.panel.date.unique())
        dates = dates[dates <= np.datetime64(end)]
        days_list = list(dates[-(int(days) + 1):]) if len(dates) else []
        for t in targets:
            for d in days_list:
                rows = forecast_day(ctx, d, symbols, h, t["target"], t.get("band"), sel, t.get("role", "main"), latest, log)
                n = append(rows)
                total += n
                log(f"  {pd.Timestamp(d).date()} · {h} sessions {t['target']}: {n} new forecast(s)" + ("" if n == len(rows) else f" ({len(rows) - n} already recorded)"))
    log(f"Done: {total} new forecast(s) in {time.time() - t0:.0f} s.")
    return total


if __name__ == "__main__":
    a = dict(x.split("=", 1) for x in sys.argv[1:] if "=" in x)
    run(days=int(a.get("days", 0)), as_of=a.get("date"))
