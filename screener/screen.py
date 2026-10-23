"""Screener pipeline:
    market data -> data validation -> eligible universe -> liquidity filters -> indicators -> setups -> ranking -> shortlist

compute() runs the pipeline for every date at once (used by the live screen and by historical evaluation;
every column is point-in-time, see features.py). screen() returns one date's result with the full output
schema, diagnostics and metadata. The result is a RESEARCH SHORTLIST, not a buy recommendation."""
import hashlib, json, time
import numpy as np, pandas as pd
from core import config as C
from core.data import load_raw, has_broad
from core import quality
from . import config as SC, features as F, filters, setups, ranking, universe as U

CACHE = SC.OUT_DIR / "cache"


# ---------------- data ----------------
def data_version():
    """Identifies the exact input data (prices, corporate actions, reviewed issues)."""
    h = hashlib.sha1()
    for p in [C.DATA / "prices.parquet", C.DATA / "prices_broad.parquet", C.DATA / "index.parquet",
              quality.ACTIONS, quality.REVIEWED]:
        if p.exists():
            st = p.stat()
            h.update(f"{p.name}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()[:10]


def instruments():
    p = C.DATA / "instruments.csv"
    return pd.read_csv(p, dtype=str) if p.exists() else pd.DataFrame(columns=["symbol", "exchange", "token", "name"])


def load_inputs():
    """Validated prices (corporate actions applied), benchmark, today's Nifty 500 list, quality issues."""
    px, idx, uni = load_raw(broad=has_broad())
    px = px[["date", "symbol", "open", "high", "low", "close", "volume"]].copy()
    names = dict(zip(uni.Symbol, uni["Company Name"]))
    inst = instruments()
    names.update({s: n for s, n in zip(inst.symbol, inst.get("name", inst.symbol)) if s not in names})
    px["company_name"] = px.symbol.map(names).fillna("")
    px["sector"] = px.symbol.map(dict(zip(uni.Symbol, uni.Industry))).fillna("Other")
    issues, _ = quality.run(px, idx, save=False)
    return px, idx[["date", "nifty"]].copy(), uni, issues


def features(px=None, bench=None, use_cache=True):
    """All features for all rows, cached on disk per data version and screener VERSION."""
    key = f"{SC.VERSION}_{data_version()}"
    path = CACHE / f"features_{key}.parquet"
    if use_cache and px is None and path.exists():
        return pd.read_parquet(path)
    own = px is None
    if own:
        px, bench, _, _ = load_inputs()
    f = F.build(px, bench)
    if use_cache and own:
        CACHE.mkdir(parents=True, exist_ok=True)
        for old in CACHE.glob("features_*.parquet"):
            old.unlink()
        f.to_parquet(path, index=False)
    return f


# ---------------- data-quality flags, point in time ----------------
def quality_masks(feat, issues, live_date=None):
    """(flagged_error[row], flags_text[row]). An unreviewed error blocks a stock from the day it is
    knowable (a bad tick only the next day) for 400 days. Undated issues (stale, missing days) count
    only for the live date, because they are measured on today's data."""
    flagged = np.zeros(len(feat), dtype=bool)
    text = pd.Series("", index=feat.index, dtype=object)
    if issues is None or issues.empty:
        return flagged, text
    iss = issues[~issues.reviewed]
    sym = feat.symbol.to_numpy()
    dt = feat.date.to_numpy()
    for r in iss.itertuples():
        m = sym == r.symbol
        if not m.any():
            continue
        if pd.isna(r.date) or r.date in ("", None):
            if live_date is None:
                continue
            sel = m & (dt == np.datetime64(pd.Timestamp(live_date)))
            known_from = None
        else:
            d0 = pd.Timestamp(r.date)
            known_from = d0 + pd.Timedelta(days=1) if r.check == "bad_tick" else d0
            sel = m & (dt >= np.datetime64(known_from)) & (dt < np.datetime64(d0 + pd.Timedelta(days=400 if r.severity == "error" else 252)))
        if r.severity == "error":
            flagged |= sel
        if sel.any():
            idx = np.flatnonzero(sel)
            label = f"{r.check}" + (f" {r.date}" if isinstance(r.date, str) and r.date else "")
            text.iloc[idx] = (text.iloc[idx] + "; " + label).str.lstrip("; ")
    return flagged, text


# ---------------- pipeline ----------------
def compute(feat, cfg, today_symbols, issues=None, live_date=None):
    """Applies filters, setups and ranking to feature rows (any number of dates). Returns feat + all columns."""
    cfg = SC.validate(cfg)
    inu = U.membership_mask(feat, cfg["universe"], today_symbols, cfg["pit_n"])
    flagged, qtext = quality_masks(feat, issues, live_date)
    fl = filters.apply(feat, cfg["filters"], inu, flagged)
    st = setups.evaluate(feat, cfg["setups"], fl.eligible.to_numpy())
    qual, n = setups.combine(st, cfg)
    rk = ranking.rank(feat, qual, n, cfg["ranking"])
    out = pd.concat([feat, fl, st, rk], axis=1)
    out["setups_passed"] = n
    out["qualifies"] = qual
    out["data_quality_flags"] = qtext
    return out


def _reasons(r, on):
    passed = [s for s in on if r[f"{s}_pass"]]
    return ", ".join(passed)


def screen(date=None, cfg=None, data=None):
    """One date's screen. data: optional (feat, today_symbols, issues) to reuse already-loaded inputs.
    Returns dict(rows, shortlist, diagnostics, meta)."""
    t0 = time.time()
    cfg = SC.validate(cfg or {})
    if data is None:
        px, bench, uni, issues = load_inputs()
        feat = features()
        today = list(uni.Symbol)
    else:
        feat, today, issues = data
    last = feat.date.max()
    d = pd.Timestamp(date) if date is not None else last
    if d not in set(feat.date):
        prior = feat.date[feat.date <= d]
        if prior.empty:
            raise ValueError(f"No data on or before {d.date()}")
        d = prior.max()
    # only rows up to the screen date are needed; a little history for the setups that look one bar back
    window = feat[(feat.date <= d) & (feat.date > d - pd.Timedelta(days=400))]
    if cfg["universe"] == "pit_top":                      # membership needs history to rank traded value
        window = feat[feat.date <= d]
    res = compute(window.reset_index(drop=True), cfg, today, issues, live_date=d if d == last else None)
    rows = res[res.date == d].copy()
    on = [s for s, p in cfg["setups"].items() if p["enabled"]]
    rows["qualification_reasons"] = [("passes " + _reasons(r, on)) if r.qualifies else "" for _, r in rows.iterrows()]
    nopass = rows.eligible & ~rows.qualifies
    need = {"any": 1, "all": len(on), "min_k": int(cfg["min_setups"])}[cfg["combine"]]
    rows.loc[nopass, "rejection_reasons"] = ("eligible, but passed " + rows.loc[nopass, "setups_passed"].astype(str)
                                             + f" setup(s); needs {need}")
    rows["qualified"] = rows.qualifies & ((cfg["mode"] == "all") | (rows["rank"] <= cfg["top_n"]))
    cut = rows.qualifies & ~rows.qualified
    rows.loc[cut, "rejection_reasons"] = f"qualifies but ranked below the top {cfg['top_n']}"
    inst = instruments()
    tok = dict(zip(inst.symbol, inst.token))
    rows["exchange"] = "NSE"
    rows["security_id"] = rows.symbol.map(tok)
    rows["screen_date"] = d
    rows["ranking_method"] = cfg["ranking"]["method"] + " (heuristic)"
    cid = SC.config_id(cfg)
    meta = dict(screen_date=str(d.date()), data_last=str(last.date()), run_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                data_version=data_version(), screener_version=SC.VERSION, config_id=cid, config=cfg,
                universe_note=U.UNIVERSE_NOTE[cfg["universe"]], seconds=round(time.time() - t0, 1))
    rows["config_id"], rows["screener_version"], rows["data_version"], rows["run_at"] = cid, SC.VERSION, meta["data_version"], meta["run_at"]
    shortlist = rows[rows.qualified].sort_values("rank")
    diag = diagnostics(rows, cfg)
    return dict(rows=rows.sort_values(["qualified", "rank", "symbol"], ascending=[False, True, True]), shortlist=shortlist, diagnostics=diag, meta=meta)


def diagnostics(rows, cfg):
    on = [s for s, p in cfg["setups"].items() if p["enabled"]]
    d = {"stocks with data": int(len(rows))}
    for c in filters.FILTERS:
        d[f"fail: {filters.SHORT[c]}"] = int((~rows[c]).sum())
    d["eligible"] = int(rows.eligible.sum())
    for s in on:
        d[f"pass: {s}"] = int(rows[f"{s}_pass"].sum())
    d["qualify"] = int(rows.qualifies.sum())
    d["shortlist"] = int(rows.qualified.sum())
    return d


# ---------------- output ----------------
OUTPUT_COLS = ["screen_date", "symbol", "exchange", "security_id", "company_name", "sector", "close", "median_value_20d_cr",
               "valid_ratio_60d", "sma20", "sma50", "sma200", "sma50_slope_20d", "rsi14", "adx14", "rel_volume_20d",
               "prev_high_20", "return_63d", "return_126d", "rel_return_126", "rel_return_63", "bb_width_pctile_252",
               "trend_continuation_pass", "breakout_volume_pass", "pullback_recovery_pass", "relative_strength_pass",
               "volatility_breakout_pass", "setups_passed", "ranking_score", "ranking_method", "rank", "eligible", "qualified",
               "qualification_reasons", "rejection_reasons", "data_quality_flags", "config_id", "screener_version",
               "data_version", "run_at"]
RENAME = {"median_value_20d_cr": "median_traded_value_20d_cr", "valid_ratio_60d": "valid_observation_ratio_60d",
          "rel_volume_20d": "relative_volume_20d", "prev_high_20": "previous_20d_high", "rel_return_126": "benchmark_relative_return_126",
          "rel_return_63": "benchmark_relative_return_63", "bb_width_pctile_252": "bollinger_width_percentile_252d"}


def table(res, only_shortlist=True):
    r = res["shortlist"] if only_shortlist else res["rows"]
    t = r[[c for c in OUTPUT_COLS if c in r]].rename(columns=RENAME)
    num = t.select_dtypes("number").columns
    t[num] = t[num].round(4)
    return t


def export(res, name=None, only_shortlist=True):
    SC.OUT_DIR.mkdir(parents=True, exist_ok=True)
    m = res["meta"]
    path = SC.OUT_DIR / f"{name or 'shortlist'}_{m['screen_date']}_{m['config_id']}.csv"
    table(res, only_shortlist).to_csv(path, index=False)
    path.with_suffix(".json").write_text(json.dumps({**m, "diagnostics": res["diagnostics"]}, indent=2, default=str))
    return path
