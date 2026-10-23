"""Strategy Lab engine: runs a strategy definition on history with the framework's discipline.

Two kinds of strategy:
  signal    buy a stock when the entry rule is true at the close; enter at the next open (or that close);
            exit on stop / target / trailing stop / exit rule / time limit. Every signal is a trade in the
            edge test; the portfolio simulation takes at most N at a time (ranked by rank_by).
  rotation  at each month (or week) end, rank the eligible stocks by a score and hold the top N until the
            next rebalance (optionally overlapping batches held for several periods), like momentum.

The test, per period (design 2016-20, validation 2021-23, holdout 2024+):
  signal    each trade is compared with a random-entry control: on the same day, random stocks from the
            same universe with the same exit rules. Edge = strategy minus control, t-stat on weekly clusters.
  rotation  each period's return is compared with an equal-weight portfolio of all eligible stocks.
A strategy passes when design (t >= MIN_T_STAT) and validation (t >= 1.65) both beat the control after
costs, and the holdout - looked at only when you choose to reveal it - is positive too."""
import hashlib, json, math, time
from statistics import NormalDist
import numpy as np, pandas as pd
from core import config as C
from core.data import load_raw, has_broad
from core import quality, universe_pit
from .expr import Panel, market_panel, RuleError, market_only, parse

PERIODS = [("design", C.DESIGN[0], C.DESIGN[1]), ("validation", C.VALIDATION[0], C.VALIDATION[1]), ("holdout", C.HOLDOUT_FROM, "2100-01-01")]
PERIOD_LABEL = {"design": "Design 2016–20", "validation": "Validation 2021–23", "holdout": "Holdout 2024+"}
CONTROL_DRAWS = 3

DEFAULT = dict(
    name="New strategy", notes="", mode="signal",
    universe=dict(source="nifty500", pit_n=500, min_price=C.MIN_PRICE, min_turnover_cr=C.MIN_TURNOVER_CR, sectors=[], exclude_flagged=True),
    market_filter="",
    entry="", entry_price="next_open", rank_by="", rank_desc=True,
    exits=dict(stop="atr", stop_value=2.0, target="r", target_value=2.0, trail_pct=0.0, max_days=10, exit_when=""),
    portfolio=dict(max_positions=10, cost_pct=round(C.COST_RT * 100, 3), capital=1_000_000),
    rotation=dict(rebalance="M", top_n=20, hold=1, score="", score_desc=True, filter="", regime=""),
)

TEMPLATES = {
    "Blank signal strategy": dict(name="New strategy", entry="close > sma(200)"),
    "Breakout on volume (like S1)": dict(
        name="Volume breakout", notes="Buy a close above the 55-day high on twice normal volume, in an uptrend.",
        entry="close > sma(200) and close > prev(highest(high, 55)) and vol_ratio(20) >= 2",
        exits=dict(stop="atr", stop_value=2.0, target="atr", target_value=4.0, trail_pct=0.0, max_days=10, exit_when="")),
    "Oversold dip in an uptrend (like S5)": dict(
        name="Oversold dip", notes="Buy a sharp 3-day dip in a stock in a long-term uptrend; sell when it bounces above its 5-day average.",
        entry="close > sma(200) and sma(50) > sma(200) and rsi(3) < 15",
        exits=dict(stop="atr", stop_value=2.0, target="none", target_value=0, trail_pct=0.0, max_days=5, exit_when="close > sma(5)")),
    "Momentum rotation (like MOM_L6_S1_H3)": dict(
        name="Momentum rotation", mode="rotation",
        notes="Each month-end buy the 20 stocks with the best 6-month return, skipping the latest month; hold each batch 3 months.",
        rotation=dict(rebalance="M", top_n=20, hold=3, score="prev(ret(126), 21)", score_desc=True, filter="", regime="")),
}


# ---------------- definitions ----------------
def normalize(d):
    """Fills missing fields with defaults (so old saved strategies keep working)."""
    out = json.loads(json.dumps(DEFAULT))
    for k, v in (d or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


RULE_KEYS = ["mode", "universe", "market_filter", "entry", "entry_price", "rank_by", "rank_desc", "exits", "portfolio", "rotation"]


def version_hash(d):
    """Identifies the rules (not the name or notes): each distinct hash counts as one more variant tried."""
    d = normalize(d)
    core = {k: d[k] for k in RULE_KEYS if not (d["mode"] == "signal" and k == "rotation")
            and not (d["mode"] == "rotation" and k in ("entry", "entry_price", "rank_by", "rank_desc", "exits", "market_filter"))}
    return hashlib.sha1(json.dumps(core, sort_keys=True).encode()).hexdigest()[:10]


def validate(d):
    """Returns a list of problems in plain words (empty = OK)."""
    d = normalize(d)
    errs = []
    def chk(label, text, required=False, market=False):
        if not (text or "").strip():
            if required:
                errs.append(f"{label}: required")
            return
        try:
            parse(text)
            if market and not market_only(text):
                errs.append(f"{label}: use market values only (nifty, vix, breadth, market_state)")
        except RuleError as e:
            errs.append(f"{label}: {e}")
    if d["mode"] == "signal":
        chk("Entry rule", d["entry"], required=True)
        chk("Market filter", d["market_filter"], market=True)
        chk("Rank by", d["rank_by"])
        chk("Exit rule", d["exits"]["exit_when"])
        if d["exits"]["stop"] == "expr":
            chk("Stop formula", str(d["exits"]["stop_value"]), required=True)
        if d["exits"]["target"] == "expr":
            chk("Target formula", str(d["exits"]["target_value"]), required=True)
        for which in ("stop", "target"):
            kind = d["exits"][which]
            if kind in ("atr", "pct", "r"):
                try:
                    if float(d["exits"][f"{which}_value"]) <= 0:
                        errs.append(f"{which.capitalize()}: the number must be above 0")
                except (TypeError, ValueError):
                    errs.append(f"{which.capitalize()}: enter a number")
        if int(d["exits"]["max_days"]) < 1:
            errs.append("Max days held must be at least 1")
        if d["exits"]["target"] == "r" and d["exits"]["stop"] == "none":
            errs.append("A target in R multiples needs a stop")
    else:
        r = d["rotation"]
        chk("Score", r["score"], required=True)
        chk("Filter", r["filter"])
        chk("Regime rule", r["regime"], market=True)
        if int(r["top_n"]) < 1:
            errs.append("Hold at least 1 stock")
    if d["universe"]["source"] == "pit" and not has_broad():
        errs.append("Point-in-time universe needs the broad download first (Data page: Download all NSE stocks). "
                    "Until then it ranks only today's Nifty 500 stocks, which removes only part of the bias.")
    return errs


# ---------------- data ----------------
_DATA = {}


def _mtime():
    p = C.DATA / "prices.parquet"
    b = C.DATA / "prices_broad.parquet"
    return (p.stat().st_mtime if p.exists() else 0, b.stat().st_mtime if b.exists() else 0)


def base_data(broad=False):
    """Prices (+ the broad set) and the market table, cached in memory until the files change."""
    key = ("broad" if broad else "n500", _mtime())
    if key in _DATA:
        return _DATA[key]
    px, idx, uni = load_raw(broad=broad)
    px = px.dropna(subset=["open", "high", "low", "close"])
    px = px[(px.volume > 0) & (px.open > 0)].sort_values(["symbol", "date"]).reset_index(drop=True)
    sec = dict(zip(uni.Symbol, uni.Industry))
    px["sector"] = px.symbol.map(sec).fillna("Other (outside today's Nifty 500)" if broad else "Unknown")
    # market table: nifty, vix, breadth (on today's Nifty 500 names), regime state
    from core.indicators import market_state
    n5 = px[px.symbol.isin(set(uni.Symbol))][["date", "symbol", "close"]].copy()
    n5["sma200"] = n5.groupby("symbol").close.transform(lambda s: s.rolling(200).mean())
    m = market_state(idx, n5)
    market = m[["date", "nifty", "vix", "market_state"]].copy()
    market["breadth"] = 100 * m.breadth_200
    market["market_state"] = market.market_state.fillna("unknown")
    for k in [k for k in _DATA if k[0] == key[0]]:
        _DATA.pop(k)
    _DATA[key] = (px, idx, uni, market)
    return _DATA[key]


def build_panel(d):
    """Panel + universe mask for a definition's universe settings. Returns (panel, mask, info)."""
    u = normalize(d)["universe"]
    pit = u["source"] == "pit"
    px, idx, uni, market = base_data(broad=pit and has_broad())
    info = {}
    if pit:
        mem, how = universe_pit.membership(px, n=int(u["pit_n"]))
        inu = universe_pit.mask(px.date.to_numpy(), px.symbol.to_numpy(), mem)
        info["universe"] = f"Point-in-time: {how}" + ("" if has_broad() else " (today's Nifty 500 stocks only: broad data not downloaded)")
    else:
        inu = px.symbol.isin(set(uni.Symbol)).to_numpy()
        info["universe"] = "Today's Nifty 500 (survivorship bias: stocks that fell out of the index are missing)"
    turn = (px.close * px.volume / 1e7).groupby(px.symbol).transform(lambda s: s.rolling(20, min_periods=20).mean())
    liq = (turn >= float(u["min_turnover_cr"])) & (px.close >= float(u["min_price"]))
    mask = inu & liq.to_numpy()
    if u["sectors"]:
        mask &= px.sector.isin(set(u["sectors"])).to_numpy()
    if u["exclude_flagged"]:
        iss = quality.check(px, idx)
        bad = set(iss[(iss.severity == "error") & ~iss.reviewed].symbol)
        if bad:
            mask &= ~px.symbol.isin(bad).to_numpy()
            info["excluded"] = sorted(bad)
    panel = Panel(px, market=market, rank_mask=pd.Series(mask))
    return panel, mask, info


# ---------------- signal strategies ----------------
def _levels(d, panel, cache):
    """Per-row stop and target distances evaluated at the signal bar (arrays; NaN = none)."""
    ex = d["exits"]
    n = panel.n
    out = {}
    for which in ("stop", "target"):
        kind, val = ex[which], ex[f"{which}_value"]
        if kind == "expr":
            out[which] = panel.eval(str(val)).to_numpy(float)
        elif kind == "atr":
            if "atr14" not in cache:
                cache["atr14"] = panel.eval("atr(14)").to_numpy(float)
            out[which] = cache["atr14"] * float(val)
        else:
            out[which] = np.full(n, float(val) if kind in ("pct", "r") else np.nan)
    return out


def _sim(i, e_px, first, o, h, l, c, flag, end, stop_px, tgt_px, trail, max_days):
    """One trade from signal row i. Entry at e_px; checks start at row `first`. Returns (exit_row, price, reason) or None if open."""
    last = min(first + max_days - 1, end[i])
    if first > end[i]:
        return None
    hi_close = e_px
    for dd in range(first, last + 1):
        s = stop_px
        if trail > 0:
            s = max(s if s == s else -np.inf, hi_close * (1 - trail))
        if s == s and l[dd] <= s:
            return dd, (o[dd] if o[dd] < s else s), "stop"          # gap below the stop: out at the open
        if tgt_px == tgt_px and h[dd] >= tgt_px:
            return dd, max(o[dd], tgt_px), "target"
        if flag is not None and flag[dd]:
            return dd, c[dd], "exit rule"
        hi_close = max(hi_close, c[dd])
    if last - first + 1 < max_days:
        return None              # still open at the end of the data
    return last, c[last], "time"


def run_signal(d, panel, mask, progress=None):
    d = normalize(d)
    ex, pf = d["exits"], d["portfolio"]
    cost = float(pf["cost_pct"]) / 100
    df = panel.df
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    dates = df.date.to_numpy()
    sym = df.symbol.to_numpy()
    n = panel.n
    # last row of each symbol
    end = np.empty(n, dtype=np.int64)
    ends = np.r_[np.flatnonzero(panel.start)[1:] - 1, n - 1]
    starts = np.flatnonzero(panel.start)
    for s0, e0 in zip(starts, ends):
        end[s0:e0 + 1] = e0

    if progress: progress(0.05, "Evaluating the entry rule")
    sig = panel.eval(d["entry"]).to_numpy(bool) & mask
    if d["market_filter"].strip():
        mp = market_panel(panel.market)
        mf = mp.eval(d["market_filter"]).to_numpy(bool)
        mfd = dict(zip(mp.df.date.to_numpy(), mf))
        sig &= np.array([mfd.get(x, False) for x in dates]) if n else sig
    flag = panel.eval(ex["exit_when"]).to_numpy(bool) if ex["exit_when"].strip() else None
    rank = panel.eval(d["rank_by"]).to_numpy(float) if d["rank_by"].strip() else None
    cache = {}
    lv = _levels(d, panel, cache)
    trail = float(ex.get("trail_pct") or 0) / 100
    max_days = int(ex["max_days"])
    at_close = d["entry_price"] == "close"
    state = df.date.map(panel.market.set_index("date").market_state).fillna("unknown").to_numpy() if panel.market is not None else np.full(n, "")

    def one(i):
        e_row = i if at_close else i + 1
        if e_row > end[i]:
            return None
        e_px = c[i] if at_close else o[e_row]
        first = i + 1
        sd = lv["stop"][i]
        if ex["stop"] == "pct":
            stop_px = e_px * (1 - sd / 100)
        elif ex["stop"] == "atr":
            stop_px = e_px - sd
        elif ex["stop"] == "expr":
            stop_px = sd
        else:
            stop_px = np.nan
        if stop_px == stop_px and stop_px >= e_px:
            return "bad_stop"
        td = lv["target"][i]
        if ex["target"] == "pct":
            tgt = e_px * (1 + td / 100)
        elif ex["target"] == "atr":
            tgt = e_px + td
        elif ex["target"] == "r":
            tgt = e_px + td * (e_px - stop_px) if stop_px == stop_px else np.nan
        elif ex["target"] == "expr":
            tgt = td if td > e_px else np.nan
        else:
            tgt = np.nan
        r = _sim(i, e_px, first, o, h, l, c, flag, end, stop_px, tgt, trail, max_days)
        if r is None:
            return None
        x, px_out, why = r
        return dict(row=i, e_row=e_row, x_row=x, entry=e_px, exit=px_out, reason=why, stop=stop_px, target=tgt,
                    days=x - e_row + 1, gross=px_out / e_px - 1)

    if progress: progress(0.2, "Simulating trades")
    trades, open_n, bad = [], 0, 0
    sig_rows = np.flatnonzero(sig)
    busy_until = {}
    for k, i in enumerate(sig_rows):
        s = sym[i]
        if i <= busy_until.get(s, -1):
            continue
        r = one(i)
        if r is None:
            open_n += 1
            continue
        if r == "bad_stop":
            bad += 1
            continue
        busy_until[s] = r["x_row"]
        trades.append(r)
        if progress and k % 2000 == 0:
            progress(0.2 + 0.4 * k / max(len(sig_rows), 1), f"Simulating trades ({len(trades):,} so far)")
    t = pd.DataFrame(trades)
    if t.empty:
        return dict(trades=t, open_at_end=open_n, signals=int(sig.sum()), bad_stop=bad)
    t["symbol"] = sym[t.row]
    t["sector"] = df.sector.to_numpy()[t.row]
    t["signal_date"] = pd.to_datetime(dates[t.row])
    t["entry_date"] = pd.to_datetime(dates[t.e_row])
    t["exit_date"] = pd.to_datetime(dates[t.x_row])
    t["state"] = state[t.row]
    t["net"] = t.gross - cost
    if rank is not None:
        t["rank_value"] = rank[t.row]

    # random-entry control: same day, random eligible stocks, same exit rules
    if progress: progress(0.65, "Random-entry control")
    rng = np.random.default_rng(7)
    elig = np.flatnonzero(mask & (np.arange(n) < end))
    by_date = pd.Series(elig).groupby(dates[elig]).apply(np.array).to_dict()
    ctrl = np.full(len(t), np.nan)
    for j, i in enumerate(t.row.to_numpy()):
        pool = by_date.get(dates[i])
        if pool is None or len(pool) == 0:
            continue
        draws = rng.choice(pool, size=min(CONTROL_DRAWS, len(pool)), replace=False)
        rs = []
        for q in draws:
            r = one(q)
            if isinstance(r, dict):
                rs.append(r["gross"] - cost)
        if rs:
            ctrl[j] = np.mean(rs)
        if progress and j % 3000 == 0:
            progress(0.65 + 0.25 * j / len(t), "Random-entry control")
    t["control"] = ctrl
    t["excess"] = t.net - t.control
    t["period"] = period_of(t.signal_date)
    t["year"] = t.signal_date.dt.year

    if progress: progress(0.92, "Portfolio simulation")
    eq, taken = portfolio(t, panel, int(pf["max_positions"]), float(pf["capital"]), d["rank_desc"])
    t["taken"] = taken
    keep = ["signal_date", "entry_date", "exit_date", "symbol", "sector", "state", "entry", "exit", "stop", "target",
            "reason", "days", "gross", "net", "control", "excess", "period", "year", "taken"] + (["rank_value"] if rank is not None else [])
    return dict(trades=t[keep], equity=eq, open_at_end=open_n, signals=int(sig.sum()), bad_stop=bad)


def period_of(dates):
    d = pd.to_datetime(pd.Series(dates))
    out = pd.Series("warmup", index=d.index)
    for p, a, b in PERIODS:
        out[(d >= a) & (d <= b)] = p
    return out.to_numpy()


def portfolio(t, panel, max_pos, capital, rank_desc=True):
    """Takes signals in date order, at most max_pos open at once, each sized equity / max_pos at entry.
    Same-day signals are ranked by rank_value (if given). Daily mark-to-market equity."""
    df = panel.df
    c = df.close.to_numpy(float)
    t = t.copy()
    t["ix"] = np.arange(len(t))
    sort_cols = ["entry_date"] + (["rank_value"] if "rank_value" in t else [])
    asc = [True] + ([not rank_desc] if "rank_value" in t else [])
    order = t.sort_values(sort_cols, ascending=asc)
    cal = pd.DatetimeIndex(sorted(df.date.unique()))
    start = max(pd.Timestamp(C.DESIGN[0]), t.entry_date.min())
    cal = cal[cal >= start]
    ent = {k: g for k, g in order.groupby("entry_date", sort=False)}
    cash, openp, taken = capital, [], np.zeros(len(t), dtype=bool)
    eq = []
    sym_rows = {}   # (symbol) -> (dates array, close array) for marking
    for s, g in df.groupby("symbol", sort=False):
        sym_rows[s] = (g.date.to_numpy(), g.close.to_numpy(float))
    for day in cal:
        # exits at today's prices
        still = []
        for p in openp:
            if p["exit_date"] <= day:
                cash += p["size"] * (1 + p["net"])
            else:
                still.append(p)
        openp = still
        # entries at today's open
        if day in ent and len(openp) < max_pos:
            equity_now = cash + sum(_mark(p, day, sym_rows) for p in openp)
            for r in ent[day].itertuples():
                if len(openp) >= max_pos:
                    break
                if any(p["symbol"] == r.symbol for p in openp):
                    continue
                size = min(equity_now / max_pos, cash)
                if size <= 0:
                    break
                cash -= size
                openp.append(dict(symbol=r.symbol, entry=r.entry, size=size, net=r.net, exit_date=r.exit_date, exit=r.exit))
                taken[r.ix] = True
        eq.append((day, cash + sum(_mark(p, day, sym_rows) for p in openp), len(openp)))
    e = pd.DataFrame(eq, columns=["date", "equity", "positions"])
    return e, taken


def _mark(p, day, sym_rows):
    if p["exit_date"] <= day:
        return p["size"] * (1 + p["net"])
    d, c = sym_rows[p["symbol"]]
    k = np.searchsorted(d, np.datetime64(day), side="right") - 1
    px = c[k] if k >= 0 else p["entry"]
    return p["size"] * px / p["entry"]


# ---------------- rotation strategies ----------------
def run_rotation(d, panel, mask, progress=None):
    d = normalize(d)
    r = d["rotation"]
    cost = float(d["portfolio"]["cost_pct"]) / 100
    df = panel.df
    if progress: progress(0.1, "Evaluating the score")
    score = panel.eval(r["score"]).to_numpy(float)
    filt = panel.eval(r["filter"]).to_numpy(bool) if r["filter"].strip() else np.ones(panel.n, bool)
    freq = "W" if r["rebalance"] == "W" else "M"
    per = df.date.dt.to_period(freq)
    tmp = pd.DataFrame(dict(symbol=df.symbol, per=per, date=df.date, open=df.open, score=score, ok=mask & filt, elig=mask))
    g = tmp.groupby(["symbol", "per"])
    m = g.agg(open_first=("open", "first"), score=("score", "last"), ok=("ok", "last"), elig=("elig", "last"),
              last_date=("date", "last")).reset_index()
    wide = {k: m.pivot(index="per", columns="symbol", values=k).sort_index() for k in ("open_first", "score", "ok", "elig")}
    opn = wide["open_first"]
    fwd = opn.shift(-1) / opn - 1                 # held during period k: first open of k -> first open of k+1
    sc = wide["score"].shift(1)                   # ranked at the end of period k-1
    ok = wide["ok"].shift(1).fillna(False).astype(bool) & fwd.notna() & sc.notna()
    el = wide["elig"].shift(1).fillna(False).astype(bool) & fwd.notna()
    if r["regime"].strip():
        mp = market_panel(panel.market)
        rv = pd.Series(mp.eval(r["regime"]).to_numpy(bool), index=pd.to_datetime(mp.df.date))
        on = rv.groupby(rv.index.to_period(freq)).last().reindex(opn.index).shift(1).fillna(False).astype(bool)
    else:
        on = pd.Series(True, index=opn.index)
    n, H = int(r["top_n"]), max(1, int(r["hold"]))
    picks = {}
    if progress: progress(0.5, "Building portfolios")
    for k in opn.index:
        e = ok.loc[k]
        if e.sum() < n:
            continue
        s = sc.loc[k][e]
        picks[k] = list((s.nlargest(n) if r["score_desc"] else s.nsmallest(n)).index)
    months = sorted(picks)
    rows, prev_in = [], False
    for i, k in enumerate(months):
        ctrl = fwd.loc[k][el.loc[k]].mean()
        cohorts = [picks[months[j]] for j in range(max(0, i - H + 1), i + 1)]
        inv = bool(on.loc[k])
        if inv:
            gross = float(np.mean([fwd.loc[k][cc].mean() for cc in cohorts]))
            old = set(picks[months[i - H]]) if i - H >= 0 else set()
            replaced = len(set(picks[k]) - old) / n
            cst = replaced * cost / H if prev_in else cost / 2
        else:
            gross, cst = 0.0, (cost / 2 if prev_in else 0.0)
        rows.append(dict(period_start=k.start_time, gross=gross, net=gross - cst, control=ctrl, invested=inv,
                         names=len(set().union(*map(set, cohorts)))))
        prev_in = inv
    res = pd.DataFrame(rows)
    if res.empty:
        return dict(periods=res, picks=pd.DataFrame(), freq=freq)
    res["excess"] = res.net - res.control
    res["period"] = period_of(res.period_start)
    res["year"] = res.period_start.dt.year
    pk = pd.DataFrame([dict(period_start=k.start_time, symbol=s, rank=j + 1, score=float(sc.loc[k, s]),
                            fwd_pct=100 * float(fwd.loc[k, s]) if pd.notna(fwd.loc[k, s]) else np.nan)
                       for k in months for j, s in enumerate(picks[k])])
    # latest ranking (what it would buy at the next rebalance)
    last_k = wide["score"].index[-1]
    ls = wide["score"].loc[last_k][wide["ok"].loc[last_k].fillna(False).astype(bool)]
    nxt = (ls.nlargest(n) if r["score_desc"] else ls.nsmallest(n))
    cap = float(d["portfolio"]["capital"])
    eq = res[["period_start", "net", "control"]].copy()
    eq = eq[eq.period_start >= pd.Timestamp(C.DESIGN[0])]
    eq["equity"] = cap * (1 + eq.net).cumprod()
    eq["control_equity"] = cap * (1 + eq.control).cumprod()
    return dict(periods=res, picks=pk, next=nxt.rename("score").reset_index().rename(columns={"index": "symbol"}),
                equity=eq.rename(columns={"period_start": "date"}), freq=freq)


# ---------------- statistics ----------------
def _t(x):
    x = pd.Series(x).dropna()
    if len(x) < 3 or x.std() == 0:
        return np.nan
    return float(x.mean() / (x.std() / math.sqrt(len(x))))


def signal_stats(t):
    if t is None or t.empty:
        return dict(trades=0)
    w, lo = t.net[t.net > 0], t.net[t.net <= 0]
    wk = t.assign(w=t.entry_date.dt.to_period("W")).groupby("w").agg(net=("net", "mean"), ex=("excess", "mean"))
    yrs = max((t.signal_date.max() - t.signal_date.min()).days / 365.25, 0.25)
    return dict(trades=int(len(t)), trades_per_year=round(len(t) / yrs, 1), win_pct=100 * len(w) / len(t),
                avg_net_pct=100 * t.net.mean(), median_net_pct=100 * t.net.median(), avg_gross_pct=100 * t.gross.mean(),
                avg_win_pct=100 * w.mean() if len(w) else 0.0, avg_loss_pct=100 * lo.mean() if len(lo) else 0.0,
                profit_factor=float(w.sum() / -lo.sum()) if len(lo) and lo.sum() < 0 else np.nan,
                avg_days=float(t.days.mean()), control_avg_pct=100 * t.control.mean(), excess_pct=100 * t.excess.mean(),
                t_excess=_t(wk.ex), t_net=_t(wk.net), weeks=int(len(wk)))


def rotation_stats(r, freq="M"):
    if r is None or len(r) < 3:
        return dict(periods=0 if r is None else len(r))
    per_year = 52 if freq == "W" else 12
    eq = (1 + r.net).cumprod()
    yrs = len(r) / per_year
    return dict(periods=int(len(r)), cagr_pct=100 * (eq.iat[-1] ** (1 / yrs) - 1),
                control_cagr_pct=100 * ((1 + r.control).prod() ** (1 / yrs) - 1),
                avg_net_pct=100 * r.net.mean(), excess_pct=100 * r.excess.mean(), t_excess=_t(r.excess),
                win_pct=100 * (r.excess > 0).mean(), max_dd_pct=100 * (eq / eq.cummax() - 1).min(),
                sharpe=float(math.sqrt(per_year) * r.net.mean() / r.net.std()) if r.net.std() > 0 else np.nan,
                invested_pct=100 * r.invested.mean())


def equity_stats(e, col="equity"):
    if e is None or len(e) < 20:
        return {}
    e = e.copy()
    e["date"] = pd.to_datetime(e.date)
    yrs = max((e.date.iat[-1] - e.date.iat[0]).days / 365.25, 0.1)
    v = e[col].to_numpy(float)
    ret = pd.Series(v).pct_change().dropna()
    daily = len(e) / yrs > 100
    k = math.sqrt(252 if daily else (52 if len(e) / yrs > 30 else 12))
    return dict(cagr_pct=100 * ((v[-1] / v[0]) ** (1 / yrs) - 1), max_dd_pct=100 * float((v / np.maximum.accumulate(v) - 1).min()),
                sharpe=float(k * ret.mean() / ret.std()) if ret.std() > 0 else np.nan, final=float(v[-1]))


def nifty_cagr(idx_or_market, a, b):
    m = idx_or_market.dropna(subset=["nifty"])
    m = m[(m.date >= pd.Timestamp(a)) & (m.date <= pd.Timestamp(b))]
    if len(m) < 20:
        return np.nan
    yrs = max((m.date.iat[-1] - m.date.iat[0]).days / 365.25, 0.1)
    return 100 * ((m.nifty.iat[-1] / m.nifty.iat[0]) ** (1 / yrs) - 1)


def bonferroni_t(n):
    return NormalDist().inv_cdf(1 - 0.05 / (2 * max(1, n)))


def verdict(by_period, mode, versions_tested):
    """Pass/fail with the reasons, per period. Holdout counts only if present (revealed)."""
    min_trades = {"design": C.MIN_TRADES_STATE, "validation": 30}
    min_obs = {"design": 24, "validation": 12}
    checks = []
    for p in ("design", "validation"):
        s = by_period.get(p, {})
        need_t = C.MIN_T_STAT if p == "design" else 1.65
        if mode == "signal":
            n_ok = s.get("trades", 0) >= min_trades[p]
            checks.append((p, "enough trades", n_ok, f"{s.get('trades', 0)} trades (need {min_trades[p]})"))
            checks.append((p, "makes money after costs", s.get("avg_net_pct", -1) > 0, f"{s.get('avg_net_pct', np.nan):+.2f}% per trade"))
        else:
            n_ok = s.get("periods", 0) >= min_obs[p]
            checks.append((p, "enough periods", n_ok, f"{s.get('periods', 0)} rebalances (need {min_obs[p]})"))
            checks.append((p, "makes money after costs", s.get("avg_net_pct", -1) > 0, f"{s.get('avg_net_pct', np.nan):+.2f}% per period"))
        t = s.get("t_excess", np.nan)
        checks.append((p, "beats random picks", s.get("excess_pct", -1) > 0 and t == t and t >= need_t,
                       f"{s.get('excess_pct', np.nan):+.2f}% vs control, t = {t:.1f} (need {need_t})"))
    if "holdout" in by_period:
        s = by_period["holdout"]
        checks.append(("holdout", "still beats random picks", s.get("excess_pct", -1) > 0 and s.get("avg_net_pct", -1) > 0,
                       f"{s.get('excess_pct', np.nan):+.2f}% vs control, {s.get('avg_net_pct', np.nan):+.2f}% net"))
    passed = all(c[2] for c in checks)
    first_fail = next((c for c in checks if not c[2]), None)
    bt = bonferroni_t(versions_tested)
    dt = by_period.get("design", {}).get("t_excess", np.nan)
    return dict(passes=passed and "holdout" in by_period, passes_so_far=passed,
                checks=[dict(period=c[0], test=c[1], ok=bool(c[2]), detail=c[3]) for c in checks],
                first_fail=None if first_fail is None else f"{PERIOD_LABEL.get(first_fail[0], first_fail[0])}: {first_fail[1]} ({first_fail[3]})",
                versions_tested=int(versions_tested), search_t=bt, search_ok=bool(dt == dt and dt >= bt))


# ---------------- top level ----------------
def run(d, progress=None):
    """Runs a definition. Returns a result dict (dataframes + stats); the store saves it."""
    d = normalize(d)
    errs = [e for e in validate(d) if "broad download" not in e]
    if errs:
        raise RuleError("; ".join(errs))
    t0 = time.time()
    if progress: progress(0.01, "Loading data")
    panel, mask, info = build_panel(d)
    market = panel.market
    if d["mode"] == "signal":
        out = run_signal(d, panel, mask, progress)
        t = out["trades"]
        byp = {p: signal_stats(t[t.period == p]) for p, _, _ in PERIODS} if len(t) else {}
        overall = signal_stats(t[t.period != "warmup"]) if len(t) else {}
        eq = out.get("equity")
    else:
        out = run_rotation(d, panel, mask, progress)
        r = out["periods"]
        byp = {p: rotation_stats(r[r.period == p], out["freq"]) for p, _, _ in PERIODS} if len(r) else {}
        overall = rotation_stats(r[r.period != "warmup"], out["freq"]) if len(r) else {}
        eq = out.get("equity")
    port = {}
    if eq is not None and len(eq):
        e = eq.copy()
        e["date"] = pd.to_datetime(e.date)
        for p, a, b in PERIODS:
            s = e[(e.date >= a) & (e.date <= b)]
            port[p] = {**equity_stats(s), "nifty_cagr_pct": nifty_cagr(market, a, b)}
        port["all"] = {**equity_stats(e[e.date >= C.DESIGN[0]]), "nifty_cagr_pct": nifty_cagr(market, C.DESIGN[0], "2100-01-01")}
    if progress: progress(1.0, "Done")
    return dict(definition=d, mode=d["mode"], info=info, by_period=byp, overall=overall, portfolio=port,
                seconds=round(time.time() - t0, 1), data_last=str(panel.df.date.max().date()), **out)


def latest_signals(d):
    """Today's signals for a signal strategy (or the current ranking for rotation), for paper use."""
    d = normalize(d)
    panel, mask, _ = build_panel(d)
    df = panel.df
    last = df.date.max()
    if d["mode"] == "rotation":
        r = d["rotation"]
        sc = panel.eval(r["score"]).to_numpy(float)
        ok = mask & (panel.eval(r["filter"]).to_numpy(bool) if r["filter"].strip() else True) & (df.date == last).to_numpy()
        s = pd.Series(sc[ok], index=df.symbol.to_numpy()[ok]).dropna()
        top = s.nlargest(int(r["top_n"])) if r["score_desc"] else s.nsmallest(int(r["top_n"]))
        close = df[df.date == last].set_index("symbol").close
        sector = df[df.date == last].set_index("symbol").sector
        return pd.DataFrame(dict(symbol=top.index, sector=sector.reindex(top.index).values, score=top.values.round(3),
                                 close=close.reindex(top.index).values)), last
    sig = panel.eval(d["entry"]).to_numpy(bool) & mask & (df.date == last).to_numpy()
    if d["market_filter"].strip():
        mp = market_panel(panel.market)
        ok = bool(mp.eval(d["market_filter"]).to_numpy(bool)[-1])
        if not ok:
            return pd.DataFrame(columns=["symbol", "sector", "close"]), last
    out = df[sig][["symbol", "sector", "close"]].copy()
    if d["rank_by"].strip():
        out["rank_value"] = panel.eval(d["rank_by"]).to_numpy(float)[sig]
        out = out.sort_values("rank_value", ascending=not d["rank_desc"])
    return out.reset_index(drop=True), last


def signal_counts(d):
    """Quick check before a full run: signals per year and on the latest day."""
    d = normalize(d)
    panel, mask, info = build_panel(d)
    df = panel.df
    if d["mode"] == "signal":
        sig = panel.eval(d["entry"]).to_numpy(bool) & mask
        if d["market_filter"].strip():
            mp = market_panel(panel.market)
            mfd = dict(zip(mp.df.date.to_numpy(), mp.eval(d["market_filter"]).to_numpy(bool)))
            sig &= np.array([mfd.get(x, False) for x in df.date.to_numpy()])
        per_year = pd.Series(sig).groupby(df.date.dt.year).sum()
        return dict(per_year=per_year, total=int(sig.sum()), stocks=int(df.symbol[sig].nunique()), info=info)
    sc = panel.eval(d["rotation"]["score"])
    ok = pd.Series(mask) & sc.notna()
    per_year = ok.groupby(df.date.dt.year).sum() / df.groupby(df.date.dt.year).date.nunique()
    return dict(per_year=per_year.round(0), total=None, stocks=int(df.symbol[ok].nunique()), info=info)
