"""Trade plan: for each Market Lab stock, how to trade the expected move (entries, exits, size), from levels computed on
daily bars. Secondary to Market Lab: it never changes the verdict. Rules are fixed and versioned (VERSION).

Levels (from the last daily bar, i.e. known the evening before):
  H / L / C        the last session's high, low, close       ATR   14-day average true range
  P, R1, R2, S1, S2 classic pivots: P = (H+L+C)/3, R1 = 2P-L, S1 = 2P-H, R2 = P+(H-L), S2 = P-(H-L)
  swing highs/lows over 5 and 20 sessions; 20- and 50-day averages
When 5-minute bars exist, the opening range and VWAP are added as live conditions (Phase 3); the prices here use daily levels.

Up-lean (a down-lean mirrors it):
  E1  break above the last high H (intraday: after the first 15 minutes)                       50%
  E2  after E1, a shallow pullback that holds: the pivot P (swing: the 20-day average) but never more than 0.35 ATR back   50%
  SL  the tighter of: just below P (0.25 ATR), or 1 ATR below E1; never wider than 1.5 ATR; always below E2
  T1  E1 + Market Lab's upper-quartile move (at least the chosen size), or the next resistance if nearer   book 50%, stop to breakeven
  T2  the larger of 2x risk from the average entry and the next resistance beyond T1           rest
  TX  intraday: close everything by 15:15 · swing: after 5 sessions
Unclear lean: one entry each way (long above H, short below L, one cancels the other), stop 0.5 ATR, then T1 and T2.
No-trade: reward:risk at T2 below 1.5, stop wider than 2 ATR, Market Lab edge <= 0, or the risk limits are full.
Size: shares = 1% of equity / (average entry - SL), capped by the largest-position limit."""
import json
import numpy as np, pandas as pd
from core import config as C, atomic

VERSION = "tp-1"
DIR = C.DATA / "tradeplan"
MODES = {"intraday": dict(move="oc", hold="Close by 15:15 the same day"), "swing": dict(move="cc", hold="Exit after 5 sessions if no target or stop")}
RR_MIN, STOP_MAX_ATR = 1.5, 2.0


def levels(g):
    """g: one stock's daily bars, date-sorted (open, high, low, close). Levels from the LAST bar."""
    g = g.tail(260)
    h, l, c = g.high.astype(float), g.low.astype(float), g.close.astype(float)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    H, L, Cl = float(h.iat[-1]), float(l.iat[-1]), float(c.iat[-1])
    P = (H + L + Cl) / 3
    return dict(date=str(pd.Timestamp(g.date.iat[-1]).date()), high=H, low=L, close=Cl, atr=float(tr.tail(14).mean()),
                P=P, R1=2 * P - L, S1=2 * P - H, R2=P + (H - L), S2=P - (H - L),
                hi5=float(h.tail(5).max()), lo5=float(l.tail(5).min()), hi20=float(h.tail(20).max()), lo20=float(l.tail(20).min()),
                sma20=float(c.tail(20).mean()), sma50=float(c.tail(50).mean()) if len(c) >= 50 else None)


def _tick(x):
    return round(round(float(x) / 0.05) * 0.05, 2)        # NSE tick 0.05


def _side(lv, side, mode, move_pct, tight=False):
    """Legs for one side. side +1 long, -1 short. tight: one-entry plan (unclear lean) with a 0.5 ATR stop."""
    s, atr = side, lv["atr"]
    if s > 0:
        e1 = lv["high"] * 1.001
        e2 = lv["P"] if mode == "intraday" else max(lv["P"], lv["sma20"]) if lv["sma20"] < e1 else lv["P"]
        res = sorted(x for x in (lv["R1"], lv["R2"], lv["hi5"], lv["hi20"]) if x > e1 + 0.3 * atr)
    else:
        e1 = lv["low"] * 0.999
        e2 = lv["P"] if mode == "intraday" else min(lv["P"], lv["sma20"]) if lv["sma20"] > e1 else lv["P"]
        res = sorted((x for x in (lv["S1"], lv["S2"], lv["lo5"], lv["lo20"]) if x < e1 - 0.3 * atr), reverse=True)
    pull = s * (e1 - e2)                                   # how far back the pivot / average is
    e2 = e1 - s * (min(pull, 0.35 * atr) if pull > 0.1 * atr else 0.35 * atr)   # a shallow pullback: at most 0.35 ATR
    if s > 0:
        sl = max(lv["P"] - 0.25 * atr, e1 - atr)          # the tighter (higher) of the two
        sl = min(sl, e2 - 0.25 * atr)                     # always below E2
    else:
        sl = min(lv["P"] + 0.25 * atr, e1 + atr)
        sl = max(sl, e2 + 0.25 * atr)
    if tight:
        e2, sl = e1, e1 - s * 0.5 * atr
    avg = (e1 + e2) / 2
    risk = abs(avg - sl)
    t1 = e1 * (1 + s * move_pct / 100)
    if res and s * (res[0] - t1) < 0:
        t1 = res[0]
    beyond = [x for x in res if s * (x - t1) > 0]
    t2 = avg + s * max(2 * risk, abs((beyond[0] if beyond else t1) - avg))
    return dict(e1=e1, e2=e2, sl=sl, t1=t1, t2=t2, avg=avg, risk=risk)


def plan_for(lv, verdict, spread, mode, account, risk_cfg, open_positions=0, sector_count=0, sector_limit=None):
    """verdict: {lean, lift, chance}; spread: Market Lab outcome percentiles (signed %, for this mode's move)."""
    cfg = MODES[mode]
    lean = verdict.get("lean", "unclear")
    size_pct = float(verdict.get("size", 1.0))
    up_move = max(size_pct, float(spread.get("p75", size_pct)) if spread else size_pct)
    dn_move = max(size_pct, abs(float(spread.get("p25", -size_pct))) if spread else size_pct)
    equity = float(account.get("equity", 0) or 0)
    risk_rs = equity * float(risk_cfg.get("risk_per_trade", 0.01))
    cap_rs = equity * float(risk_cfg.get("max_position_pct", 0.15))
    sides = [1] if lean == "up" else [-1] if lean == "down" else [1, -1]
    legs, flags = [], []
    out = dict(mode=mode, lean=lean, version=VERSION, levels=lv, hold=cfg["hold"])
    for s in sides:
        two = len(sides) == 1
        x = _side(lv, s, mode, up_move if s > 0 else dn_move, tight=not two)
        word = "long" if s > 0 else "short"
        per_share = x["risk"] if two else abs(x["e1"] - x["sl"])
        entry = x["avg"] if two else x["e1"]
        shares = int(min(risk_rs / max(per_share, 1e-9), cap_rs / max(entry, 1e-9))) if equity > 0 else 0
        e1_sh = (shares + 1) // 2 if two else shares
        pre = "" if two else f"{word.title()}: "
        legs += [dict(leg="E1", side=word, price=_tick(x["e1"]), share=0.5 if two else 1.0, shares=e1_sh,
                      condition=f"{pre}trades {'above' if s > 0 else 'below'} the last {'high' if s > 0 else 'low'}" + (" after 09:30" if mode == "intraday" else ""))]
        if two:
            legs.append(dict(leg="E2", side=word, price=_tick(x["e2"]), share=0.5, shares=shares - e1_sh,
                             condition=f"after E1, a pullback that holds {'above' if s > 0 else 'below'} {_tick(x['e2'])} (pivot / average)"))
        legs += [dict(leg="SL", side=word, price=_tick(x["sl"]), share=1.0, condition=f"{pre}stop: exit everything {'below' if s > 0 else 'above'} this"),
                 dict(leg="T1", side=word, price=_tick(x["t1"]), share=0.5, condition=f"{pre}book half; move the stop to breakeven"),
                 dict(leg="T2", side=word, price=_tick(x["t2"]), share=0.5, condition=f"{pre}book the rest (or trail)")]
        rr = abs(x["t2"] - entry) / max(per_share, 1e-9)
        out.setdefault("sides", []).append(dict(side=word, shares=shares, risk_rs=round(shares * per_share), rr=round(rr, 2),
                                                 stop_atr=round(abs(entry - x["sl"]) / max(lv["atr"], 1e-9), 2),
                                                 value_rs=round(shares * entry)))
        if rr < RR_MIN:
            flags.append(f"{word}: reward:risk {rr:.1f} is below {RR_MIN}")
        if s < 0 and mode == "swing":
            flags.append("short: holding a short overnight needs F&O (stock futures); cash shares can only be shorted intraday")
        if abs(entry - x["sl"]) > STOP_MAX_ATR * lv["atr"]:
            flags.append(f"{word}: stop wider than {STOP_MAX_ATR:g}× ATR")
    legs.append(dict(leg="TX", side="both", price=None, share=None, condition=cfg["hold"]))
    if verdict.get("lift", 0) <= 0:
        flags.append("Market Lab edge is zero or negative")
    if open_positions >= int(risk_cfg.get("max_positions", 10)):
        flags.append("risk limit: maximum open positions reached")
    if sector_limit and sector_count >= sector_limit:
        flags.append("risk limit: sector already at its maximum")
    if equity <= 0:
        flags.append("set your equity in Positions & risk → Account to get share sizes")
    soft = ("set your equity", "short: holding")
    out.update(legs=legs, n_entries=sum(1 for x in legs if x["leg"].startswith("E")), n_exits=sum(1 for x in legs if x["leg"] in ("SL", "T1", "T2", "TX")),
               flags=flags, tradeable=not [f for f in flags if not f.startswith(soft)])
    return out


def ta_view(lv, patterns, g=None):
    """Second opinion from the chart (daily levels + candle patterns of the last 2 sessions). Labels only: untested until
    Market Lab → Review grades them. dir: up / down / neutral (score of 4 simple checks, |score| >= 2 counts)."""
    if g is not None and len(g) >= 30:                     # the full evidence (tradeplan/evidence.py)
        from . import evidence as EV
        ev = EV.evidence(g, patterns, lv)
        atr_ = lv["atr"] or np.nan
        c = lv["close"]
        setup = ("near the 20-day high" if (lv["hi20"] - c) <= 0.5 * atr_ else "near the 20-day low" if (c - lv["lo20"]) <= 0.5 * atr_
                 else "tight 5-day range" if (lv["hi5"] - lv["lo5"]) <= 2.5 * atr_ else None)
        return dict(dir=ev["dir"], score=ev["net"], up=ev["up"], down=ev["down"], setup=setup,
                    why=[f"{x['id']} {'▲' if x['verdict'] > 0 else '▼' if x['verdict'] < 0 else '•'} {x['reason']}" for x in ev["checks"]])
    c, why, score = lv["close"], [], 0
    for ok, up, dn in ((c > lv["sma20"], "above the 20-day average", "below the 20-day average"),
                       (lv.get("sma50") is not None and lv["sma20"] > lv["sma50"], "20-day average above the 50-day", "20-day average below the 50-day"),
                       (c > lv["P"], "closed above the pivot", "closed below the pivot")):
        score += 1 if ok else -1
        why.append(up if ok else dn)
    recent = sorted({p["date"] for p in patterns})[-2:]
    pat = sum(1 if p["direction"] == "bullish" else -1 if p["direction"] == "bearish" else 0 for p in patterns if p["date"] in recent)
    if pat:
        score += 1 if pat > 0 else -1
        why.append("recent bullish candles" if pat > 0 else "recent bearish candles")
    atr = lv["atr"] or np.nan
    setup = ("near the 20-day high" if (lv["hi20"] - c) <= 0.5 * atr else "near the 20-day low" if (c - lv["lo20"]) <= 0.5 * atr
             else "tight 5-day range" if (lv["hi5"] - lv["lo5"]) <= 2.5 * atr else None)
    return dict(dir="up" if score >= 2 else "down" if score <= -2 else "neutral", score=int(score), setup=setup, why=why)


def fund_view(q, sym, day):
    """Second opinion from the latest quarterly result filed by the evening of `day` (q: fundamentals quarters table)."""
    if q is None or q.empty:
        return dict(dir="no data", why=["no quarterly results downloaded"])
    r = q[(q.symbol == sym) & (pd.to_datetime(q.filed_at) <= pd.Timestamp(day) + pd.Timedelta(hours=21))]
    if r.empty:
        return dict(dir="no data", why=["no results filed yet"])
    r = r.sort_values("filed_at").iloc[-1]
    fresh = (pd.Timestamp(day) + pd.Timedelta(hours=21) - pd.Timestamp(r.filed_at)) <= pd.Timedelta(days=3)
    py, ry = r.get("profit_yoy"), r.get("revenue_yoy")
    why = [f"results for {pd.Timestamp(r.period_end):%b %Y} filed {pd.Timestamp(r.filed_at):%d %b}" + (" (just out)" if fresh else "")]
    if py is None or pd.isna(py):
        return dict(dir="no data", fresh=bool(fresh), why=why + ["no year-ago quarter to compare (download results history)"])
    why.append(f"profit {py:+.0%} vs a year ago" + (f", revenue {ry:+.0%}" if ry is not None and pd.notna(ry) else ""))
    return dict(dir="up" if py > 0.05 else "down" if py < -0.05 else "neutral", fresh=bool(fresh), why=why)


def run(day=None, log=print):
    """Plans for every stock in tonight's radar, both modes. Saved to data/tradeplan/<day>.json."""
    from marketlab import radar as R
    day = day or R.latest_day()
    rd, sp = R.read("radar", day), R.read("spread", day)
    if rd.empty:
        log("Trade plan: no radar for that day.")
        return {}
    out = build(day, rd, sp)
    DIR.mkdir(parents=True, exist_ok=True)
    atomic.write_text(DIR / f"{day}.json", dumps(out))
    log(f"Trade plan: {len(out['plans'])} stocks planned for {day} (patterns: {out['patterns_source']}).")
    return out


def dumps(out):
    return json.dumps(out, default=lambda o: None if isinstance(o, float) and not np.isfinite(o) else (o.item() if hasattr(o, "item") else str(o)))


def build(day, rd, sp, px=None):
    """Plans for the radar rows rd (spread sp) on `day`, from daily bars up to that day only."""
    from marketlab import engine as E
    from marketlab import evaluate as V
    from . import patterns as PT
    if px is None:
        from core.data import load_raw
        px, _, _ = load_raw()
    px = px[px.date <= pd.Timestamp(day)]
    acct = json.loads(C.ACCOUNT_JSON.read_text()) if C.ACCOUNT_JSON.exists() else {}
    pos = pd.read_csv(C.POSITIONS_CSV) if C.POSITIONS_CSV.exists() else pd.DataFrame(columns=["symbol", "sector"])
    pos = pos.dropna(subset=["symbol"]) if len(pos) else pos
    sk, _ = V.skill_map()
    out = {"day": day, "version": VERSION, "patterns_source": None, "plans": {}}
    groups = dict(tuple(px.sort_values("date").groupby("symbol")))
    try:
        quarters = pd.read_parquet(C.DATA / "fundamentals" / "quarters.parquet", columns=["symbol", "filed_at", "period_end", "profit_yoy", "revenue_yoy"])
    except Exception:
        quarters = None
    for r in rd.to_dict("records"):
        r = pd.Series(r)
        g = groups.get(r.symbol)
        if g is None or len(g) < 30:
            continue
        lv = levels(g)
        pats, src = PT.recent(g.reset_index(drop=True), days=5)
        out["patterns_source"] = src
        entry = {"patterns": pats, "ta": ta_view(lv, pats, g.tail(260)), "fund": fund_view(quarters, r.symbol, day)}
        sec_n = int((pos.get("sector", pd.Series(dtype=str)) == r.sector).sum()) if len(pos) else 0
        for mode, cfg in MODES.items():
            m, s = cfg["move"], 1.0
            skill = sk.get((m, s)) or {}
            up, nb = r[f"up_{m}_{s}"], r[f"nb_{m}_{s}"]
            raw = str(E.lean_of(np.array([up]), np.array([nb]))[0])
            lean = raw if (skill.get("dir_hit") or 0) >= 0.53 else "unclear"
            ch, us = r[f"ch_{m}_{s}"], r[f"us_{m}_{s}"]
            spr = sp[(sp.symbol == r.symbol) & (sp.move == m)]
            spread = spr.iloc[0].to_dict() if len(spr) else None
            entry[mode] = plan_for(lv, dict(lean=lean, lift=float(ch - us), chance=float(ch), size=s), spread, mode, acct, C.RISK,
                                   open_positions=len(pos), sector_count=sec_n, sector_limit=C.RISK.get("max_per_sector"))
        out["plans"][r.symbol] = entry
    return out


def load(day=None):
    from marketlab import radar as R
    day = day or R.latest_day()
    p = DIR / f"{day}.json" if day else None
    return json.loads(p.read_text()) if p and p.exists() else None
