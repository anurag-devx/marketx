"""Review: keep every night's Market Lab calls and trade plans, grade them against what happened next, and profile the
session intraday. Nothing here changes a call: it only records and grades.

Ledger    data/marketlab/ledger/<kind>/<day>/   kind = live (saved after each nightly radar) or replay (rebuilt for a past day
          from data up to that day; news and filings are as stored now, so replays can be slightly optimistic)
          radar.parquet, spread.parquet, plans.json  ·  calls.parquet, plans_grade.parquet, profile.parquet (the grades)
Calls     for every stock, move (cc / oc / gp) and size: chance, usual, edge, lean (shown only where the Scorecard's
          direction hit rate >= 53%), confidence; then what happened. "Picks" = the TOP stocks by edge, as on the radar.
          hit = the move reached the size; lean_right = the move went the leaned way.
Plans     intraday: next session's 5-minute bars. E1 triggers after 09:30 (fill at E1, or the open if it gapped through),
          then stop / T1 (book half, stop to entry) / T2, all closed at 15:15. Same bar touching stop and target = stop first;
          on the entry bar itself only its close counts (the order inside that bar is unknown).
          E2 is not graded yet. swing: daily bars over the next 5 sessions, same rules. Result in R (multiples of the risk).
Profile   crossings of +/- threshold from the reference (cc: yesterday's close, oc: today's open). A spell starts when a bar
          reaches the threshold and ends when a bar closes back inside the re-cross band (default 0.8 of the threshold),
          so noise around the level isn't counted twice. Minutes from 5-minute bars."""
import json, shutil
import numpy as np, pandas as pd
from core import config as C, atomic
from .features import MOVES, SIZES
from . import radar as R, engine as E

KINDS = ("live", "replay")
TOP = 20
DIR_MIN = 0.53
BAND = 0.8
OPEN, TRIGGER, EXIT, CLOSE = "09:15", "09:30", "15:15", "15:30"


def ledger():
    return R.DIR / "ledger"


def _dir(kind, day):
    return ledger() / kind / str(day)


def days(kind):
    k = ledger() / kind
    return sorted(p.name for p in k.iterdir() if (p / "radar.parquet").exists()) if k.exists() else []


def save_live(day, log=print):
    from tradeplan import plan as TP
    dst = _dir("live", day)
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("radar", "spread", "drivers"):
        src = R.DIR / name / f"{day}.parquet"
        if src.exists():
            shutil.copy2(src, dst / f"{name}.parquet")
    if (TP.DIR / f"{day}.json").exists():
        shutil.copy2(TP.DIR / f"{day}.json", dst / "plans.json")
    atomic.write_text(dst / "meta.json", json.dumps(dict(kind="live", day=str(day), saved=str(pd.Timestamp.now().floor("s")))))
    log(f"Review: tonight's calls kept ({day}).")


def replay(n_days=21, start=None, end=None, log=print):
    """Rebuild the radar and plans for past days, each from data up to that day only, then grade them."""
    from core.data import load_raw
    from tradeplan import plan as TP
    d = R.panel(log)
    past = sorted(pd.to_datetime(d.date.unique()))[:-1]                   # the latest day is tonight's live radar
    sel = [x for x in past if (start is None or x >= pd.Timestamp(start)) and (end is None or x <= pd.Timestamp(end))]
    sel = sel if (start or end) else sel[-int(n_days):]
    px, _, _ = load_raw()
    for i, day in enumerate(sel, 1):
        tag = f"{day:%Y-%m-%d}"
        log(f"Replay {i}/{len(sel)}: {tag} ...")
        f = R.match_day(d, day, log=lambda *_: None, drivers="top", cases=False)
        dst = _dir("replay", tag)
        dst.mkdir(parents=True, exist_ok=True)
        atomic.to_parquet(f["radar"], dst / "radar.parquet")
        atomic.to_parquet(f["spread"], dst / "spread.parquet")
        atomic.to_parquet(f["drivers"], dst / "drivers.parquet")
        atomic.write_text(dst / "plans.json", TP.dumps(TP.build(tag, f["radar"], f["spread"], px)))
        atomic.write_text(dst / "meta.json", json.dumps(dict(kind="replay", day=tag, saved=str(pd.Timestamp.now().floor("s")))))
    log(f"Replay: {len(sel)} days rebuilt. Grading ...")
    grade("replay", d=d, log=log, force=True)


# ---------------- intraday ----------------
class Bars:
    """5-minute bars per stock, loaded once; session(sym, day) = that day's regular-session bars."""
    def __init__(self, since=None):
        self.c, self.since = {}, pd.Timestamp(since) if since is not None else None

    def session(self, sym, day):
        if sym not in self.c:
            from core import intraday as I
            b = I.load(sym)
            if len(b):
                b = b.assign(ts=pd.to_datetime(b.ts))
                t = b.ts.dt.strftime("%H:%M")
                b = b[(t >= OPEN) & (t < CLOSE)].assign(d=lambda x: x.ts.dt.normalize())
                if self.since is not None:                                  # keep only the days being graded (memory)
                    b = b[b.d >= self.since]
            self.c[sym] = b
        b = self.c[sym]
        return b[b.d == pd.Timestamp(day)].reset_index(drop=True) if len(b) else b


def profile(bars, ref, thr=1.0, band=None):
    """Crossings of +/- thr% from ref. band (% from ref, default 0.8 x thr): a spell ends when a bar closes back inside it."""
    band = BAND * thr if band is None else min(float(band), thr)
    if bars is None or not len(bars) or not ref:
        return None
    up_hi, up_band, dn_lo, dn_band = ref * (1 + thr / 100), ref * (1 + band / 100), ref * (1 - thr / 100), ref * (1 - band / 100)
    spells, cur = [], None
    for b in bars.itertuples():
        if cur is None:
            if b.high >= up_hi:
                cur = dict(dir="up", start=b.ts)
            elif b.low <= dn_lo:
                cur = dict(dir="down", start=b.ts)
            if cur is None:
                continue
        if (cur["dir"] == "up" and b.close < up_band) or (cur["dir"] == "down" and b.close > dn_band):
            cur["end"] = b.ts + pd.Timedelta(minutes=5)
            spells.append(cur)
            cur = None
    last = bars.ts.iat[-1] + pd.Timedelta(minutes=5)
    held = cur is not None
    if held:
        cur["end"] = last
        spells.append(cur)
    for s in spells:
        s["minutes"] = int((s["end"] - s["start"]).total_seconds() // 60)
    return dict(ref=float(ref), thr=thr, band=band, n_up=sum(s["dir"] == "up" for s in spells), n_down=sum(s["dir"] == "down" for s in spells),
                n_cross=len(spells), minutes_beyond=sum(s["minutes"] for s in spells),
                first_cross=spells[0]["start"].strftime("%H:%M") if spells else None, first_dir=spells[0]["dir"] if spells else None,
                held_to_close=held, held_dir=spells[-1]["dir"] if held else None,
                best_pct=100 * (bars.high.max() / ref - 1), worst_pct=100 * (bars.low.min() / ref - 1), close_pct=100 * (bars.close.iat[-1] / ref - 1),
                spells=[dict(dir=s["dir"], start=s["start"].strftime("%H:%M"), end=s["end"].strftime("%H:%M"), minutes=s["minutes"]) for s in spells])


# ---------------- plans ----------------
def _legs(plan):
    out = {}
    for x in plan.get("legs", []):
        if x.get("side") in ("long", "short") and x.get("price") is not None:
            out.setdefault(x["side"], {})[x["leg"]] = float(x["price"])
    return out


def _walk(bars, side, lv, entry_i, entry, exit_at=None):
    """Manage one entered trade over bars[entry_i:]. Returns (first, final, exit price, R, exit time)."""
    s = 1 if side == "long" else -1
    sl, t1, t2 = lv["SL"], lv.get("T1"), lv.get("T2")
    risk = abs(entry - sl) or np.nan
    stop, half, first, booked = sl, False, None, 0.0
    for i in range(entry_i, len(bars)):
        b = bars.iloc[i]
        tstr = str(b.get("tstr", ""))
        if exit_at and tstr >= exit_at:
            px = float(b.open)
            return (first or "TX"), "TX", px, (booked + (0.5 if half else 1.0) * s * (px - entry)) / risk, tstr
        lo, hi = float(b.low), float(b.high)
        if i == entry_i:                                       # the entry bar: its order is unknown, so only its close counts
            lo = hi = float(b.close)
        hit_stop = (lo <= stop) if s > 0 else (hi >= stop)
        if hit_stop:                                           # stop first when a bar touches both
            return (first or "SL"), ("BE" if half else "SL"), stop, (booked + (0.5 if half else 1.0) * s * (stop - entry)) / risk, tstr
        if not half and t1 is not None and ((hi >= t1) if s > 0 else (lo <= t1)):
            half, first, booked, stop = True, "T1", 0.5 * s * (t1 - entry), entry
        if half and t2 is not None and ((hi >= t2) if s > 0 else (lo <= t2)):
            return first, "T2", t2, (booked + 0.5 * s * (t2 - entry)) / risk, tstr
    b = bars.iloc[-1]
    return first, "open", float(b.close), (booked + (0.5 if half else 1.0) * s * (float(b.close) - entry)) / risk, str(b.get("tstr", ""))


def _trigger(bars, legs, start_i=0):
    """First bar where an E1 breaks; both sides in the same bar = ambiguous."""
    for i in range(start_i, len(bars)):
        b = bars.iloc[i]
        hits = [sd for sd, lv in legs.items() if "E1" in lv and ((b.high >= lv["E1"]) if sd == "long" else (b.low <= lv["E1"]))]
        if len(hits) > 1:
            return i, None
        if hits:
            sd, e1 = hits[0], legs[hits[0]]["E1"]
            fill = max(e1, float(b.open)) if sd == "long" else min(e1, float(b.open))
            return i, (sd, fill)
    return None, None


def grade_plan(plan, bars, mode):
    """bars: intraday = the next session's 5-minute bars; swing = the next 5 daily bars. Returns a dict."""
    legs = _legs(plan or {})
    if not legs or bars is None or not len(bars):
        return dict(status="no data")
    b = bars.copy()
    if mode == "intraday":
        b["tstr"] = pd.to_datetime(b.ts).dt.strftime("%H:%M")
        start = int((b.tstr < TRIGGER).sum())
        i, hit = _trigger(b, legs, start)
        exit_at = EXIT
    else:
        b["tstr"] = pd.to_datetime(b.date).dt.strftime("%Y-%m-%d")
        i, hit = _trigger(b, legs, 0)
        exit_at = None
    if i is None:
        return dict(status="no entry", tradeable=bool(plan.get("tradeable")))
    if hit is None:
        return dict(status="ambiguous", tradeable=bool(plan.get("tradeable")), entry_time=str(b.tstr.iat[i]))
    side, fill = hit
    first, final, px, r, t = _walk(b, side, legs[side], i, fill, exit_at)
    if mode == "swing" and final == "open" and len(b) >= 5:
        final, first = "TX", first or "TX"
    return dict(status="open" if final == "open" else "done", tradeable=bool(plan.get("tradeable")), side=side, entry=fill,
                entry_time=str(b.tstr.iat[i]), first=first, exit=final, exit_price=px, exit_time=t, r=float(r) if np.isfinite(r) else None)


# ---------------- grading ----------------
def _calls(rd, outc, sk):
    g = rd.merge(outc, on=["symbol", "date"], how="left")
    rows = []
    for m in MOVES:
        for s in SIZES:
            k = f"{m}_{s}"
            if f"ch_{k}" not in g:
                continue
            skill = sk.get((m, s)) or {}
            up, nb = g[f"up_{k}"].to_numpy(float), g[f"nb_{k}"].to_numpy(float)
            raw = E.lean_of(up, nb)
            lean = raw if (skill.get("dir_hit") or 0) >= DIR_MIN else np.full(len(g), "unclear", dtype=object)
            conf = E.confidence(g.dist_pct.to_numpy(float), nb, bool(skill.get("skill")))
            ret = g[m].astype(float)
            lift = g[f"ch_{k}"] - g[f"us_{k}"]
            f = pd.DataFrame({"symbol": g.symbol, "sector": g.get("sector", ""), "date": g.date, "move": m, "size": s,
                              "chance": g[f"ch_{k}"], "usual": g[f"us_{k}"], "lift": lift, "up_share": up, "lean": lean, "confidence": conf,
                              "ret": ret})
            f["rank"] = f.lift.rank(ascending=False, method="first").astype(int)
            f["pick"] = f["rank"] <= TOP
            f["hit"] = np.where(ret.notna(), (ret.abs() >= s / 100).astype(float), np.nan)
            f["lean_right"] = np.where(ret.notna() & (f.lean != "unclear"), ((f.lean == "up") & (ret > 0)) | ((f.lean == "down") & (ret < 0)), np.nan).astype(float)
            rows.append(f)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def second_opinion(calls, plans):
    """Attach the TA and fundamentals labels saved with the plans, and whether each agrees with Market Lab's direction
    (the up-share above / below 50%, shown or not). dir_right: the move went Market Lab's way."""
    if calls.empty:
        return calls
    lab = pd.DataFrame([dict(symbol=k, ta_dir=(v.get("ta") or {}).get("dir"), ta_setup=(v.get("ta") or {}).get("setup"),
                             fund_dir=(v.get("fund") or {}).get("dir")) for k, v in plans.items()], columns=["symbol", "ta_dir", "ta_setup", "fund_dir"])
    c = calls.drop(columns=[x for x in ("ta_dir", "ta_setup", "fund_dir") if x in calls]).merge(lab, on="symbol", how="left")
    ml = np.where(c.up_share > 0.5, "up", "down")
    for src in ("ta", "fund"):
        x = c[f"{src}_dir"].fillna("no data")
        c[f"{src}_agree"] = np.where(x.isin(["up", "down"]), np.where(x == ml, "agrees", "disagrees"), np.where(x == "neutral", "neutral", "no data"))
    c["ml_dir"] = ml
    c["dir_right"] = np.where(c.ret.notna() & (c.ret != 0), ((ml == "up") & (c.ret > 0)) | ((ml == "down") & (c.ret < 0)), np.nan).astype(float)
    return c


def grade(kind, d=None, log=print, force=False):
    """Grade every day of a ledger whose outcome is now known. Cheap: re-grades only days with pending rows."""
    from core.data import load_raw
    from . import evaluate as V
    ds = days(kind)
    if not ds:
        return 0
    d = d if d is not None else R.load_panel()
    if d is None:
        log("Review: no scenario panel yet.")
        return 0
    outc = d[["symbol", "date", "next_date", "cc", "oc", "gp"]]
    sk, _ = V.skill_map()
    px, _, _ = load_raw()
    pxg = dict(tuple(px.sort_values("date").groupby("symbol")))
    bars, n = Bars(since=min(ds)), 0
    for day in ds:
        dd = _dir(kind, day)
        old = pd.read_parquet(dd / "calls.parquet") if (dd / "calls.parquet").exists() else None
        oldp = pd.read_parquet(dd / "plans_grade.parquet") if (dd / "plans_grade.parquet").exists() else None
        if not force and old is not None and old.ret.notna().all() and oldp is not None and not (oldp.status == "open").any():
            continue
        rd = pd.read_parquet(dd / "radar.parquet")
        rd["date"] = pd.to_datetime(rd.date).astype(outc.date.dtype)
        plans = json.loads((dd / "plans.json").read_text()).get("plans", {}) if (dd / "plans.json").exists() else {}
        calls = second_opinion(_calls(rd, outc, sk), plans)
        atomic.to_parquet(calls, dd / "calls.parquet")
        nxt = outc[outc.date == pd.Timestamp(day)].set_index("symbol").next_date
        picks = set(calls[calls.pick & calls.move.isin(["cc", "oc"])].symbol)
        prow, frow = [], []
        for sym in sorted(set(plans) | picks):
            nd = nxt.get(sym)
            if nd is None or pd.isna(nd):
                continue
            sess = bars.session(sym, nd)
            hist = pxg.get(sym, px.iloc[:0])
            g = hist[hist.date > pd.Timestamp(day)].head(5)
            if sym in plans:
                for mode in ("intraday", "swing"):
                    if mode in plans[sym]:
                        r = grade_plan(plans[sym][mode], sess if mode == "intraday" else g, mode)
                        prow.append(dict(symbol=sym, day=day, mode=mode, lean=plans[sym][mode].get("lean"), **r))
            if sym in picks and len(sess):
                prev_close = hist[hist.date <= pd.Timestamp(day)].close.tail(1)
                for m, ref in (("cc", float(prev_close.iat[0]) if len(prev_close) else None), ("oc", float(sess.open.iat[0]))):
                    for s in SIZES:
                        p = profile(sess, ref, s)
                        if p:
                            p.pop("spells")
                            frow.append(dict(symbol=sym, day=day, move=m, size=s, **p))
        atomic.to_parquet(pd.DataFrame(prow), dd / "plans_grade.parquet")
        atomic.to_parquet(pd.DataFrame(frow), dd / "profile.parquet")
        n += 1
    log(f"Review: graded {n} {kind} day(s).")
    return n


# ---------------- reading ----------------
def load(kind, name):
    ks = KINDS if kind == "all" else (kind,)
    fr = []
    for k in ks:
        for day in days(k):
            p = _dir(k, day) / f"{name}.parquet"
            if p.exists():
                f = pd.read_parquet(p)
                if len(f):
                    fr.append(f.assign(kind=k, day=day))
    return pd.concat(fr, ignore_index=True) if fr else pd.DataFrame()


def calibration(calls, bins=(0, .2, .3, .4, .5, .6, .7, .8, 1.0001)):
    c = calls.dropna(subset=["hit"])
    if c.empty:
        return pd.DataFrame()
    c = c.assign(bin=pd.cut(c.chance, bins, right=False))
    out = c.groupby("bin", observed=True).agg(n=("hit", "size"), said=("chance", "mean"), happened=("hit", "mean")).reset_index()
    out["bin"] = out.bin.astype(str)
    return out
