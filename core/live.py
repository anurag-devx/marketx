"""Live market feed for the Live terminal (#/live). Angel SmartAPI WebSocket (QUOTE mode) inside the web server process.
Read only: never places orders. Keys are read by fetch_angel.creds() and are never printed or logged by MarketX.

What it keeps (in memory, saved to data/live/<day>/ every minute and on stop):
  quotes   last price, day open/high/low, previous close, VWAP (Angel's average traded price), volume, for the Nifty 500
           and the strip indices (breadth comes from the 500)
  bars     5-minute bars for watched stocks and the indices, built from ticks (volume = change in the day's volume)
  alerts   for watched stocks (Stock Desk list + Market Lab picks + paper takes): crossed +/-1% and +/-2% from the previous
           close, and tonight's intraday plan legs (E1 long/short after 09:30, then SL / T1 / T2 of the side that triggered)
  takes    paper takes (Trade plan -> Paper take) followed live: entry when E1 trades, half booked at T1 (stop to entry),
           rest at T2 or the stop; intraday closes at 15:15. P&L in rupees and in R (risk = the plan's risk_rs), after costs
           (brokerage, STT, exchange, SEBI, stamp, GST, slippage: COSTS below; the exit still to pay is counted while open)
  picks    Market Lab's top picks for today (close-to-close and open-to-close, >= 1%): moved 1% yet, minutes beyond, lean right so far

Replay: feeds a saved session's 5-minute bars (data/intraday/5min) through the same code as ticks, so the terminal can be
tried out of market hours, and tests use it. Live runs only 09:00-15:35 IST on weekdays; it reconnects on its own."""
import json, threading, time, datetime as dt
from collections import deque
import numpy as np, pandas as pd
from core import config as C, atomic

DIR = C.DATA / "live"
IDX_FILE = C.DATA / "index_tokens.json"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
FALLBACK_IDX = {"nifty": "99926000", "nifty_bank": "99926009", "vix": "99926017"}     # Angel's well-known index tokens
STRIP = [("nifty", "Nifty 50"), ("nifty_bank", "Bank Nifty"), ("nifty_fin", "Fin Nifty"), ("nifty_it", "Nifty IT"),
         ("nifty_mid150", "Midcap 150"), ("nifty_small250", "Smallcap 250"), ("vix", "India VIX")]
START_T, OPEN_T, ENTRY_T, EXIT_T, CLOSE_T, STOP_T = (dt.time(9, 0), dt.time(9, 15), dt.time(9, 30), dt.time(15, 15),
                                                    dt.time(15, 30), dt.time(15, 35))
BAR_MIN = 5
SLOTS = 75                                   # 5-min bars 09:15-15:30


def index_label(col):
    lab = dict(STRIP).get(col)
    if lab:
        return lab
    try:
        from core import indices as IX
        n = (IX.INDICES.get(col) or [col])[0]
        return n.title().replace("Nifty", "Nifty").replace("Fin Service", "Financial Services")
    except Exception:
        return col
CROSS = (1.0, 2.0)
PICK_SIZE = 1.0
QUOTE_MODE, NSE_CM = 2, 1


def now():
    return dt.datetime.now(IST).replace(tzinfo=None)


def market_open(t=None):
    t = t or now()
    return t.weekday() < 5 and START_T <= t.time() <= STOP_T


def bar_start(t):
    m = (t.hour * 60 + t.minute - (9 * 60 + 15)) // BAR_MIN * BAR_MIN + 9 * 60 + 15
    return t.replace(hour=m // 60, minute=m % 60, second=0, microsecond=0)


# ---------------------------------------------------------------- trading costs (NSE cash; edit here if your broker differs)
COSTS = dict(brokerage_pct=0.03, brokerage_max=20.0,          # per order: 0.03% or Rs 20, whichever is lower
             stt_intraday_sell=0.025, stt_delivery=0.1,        # STT: intraday on the sell side; delivery on both sides
             exchange=0.00297, sebi=0.0001,                    # NSE transaction charge, SEBI fee (both sides)
             stamp_intraday_buy=0.003, stamp_delivery_buy=0.015, gst=18.0,
             slippage=0.03)                                     # per side, % of value: fills worse than the alert price


def fill_cost(buy, value, delivery=False, c=COSTS):
    """Rupees paid on one order of `value` rupees (all rates in %)."""
    if value <= 0:
        return 0.0
    v = value / 100
    brk = min(c["brokerage_max"], c["brokerage_pct"] * v)
    stt = c["stt_delivery"] * v if delivery else (0 if buy else c["stt_intraday_sell"] * v)
    exch, sebi = c["exchange"] * v, c["sebi"] * v
    stamp = (c["stamp_delivery_buy"] if delivery else c["stamp_intraday_buy"]) * v if buy else 0
    return brk + stt + exch + sebi + stamp + c["gst"] / 100 * (brk + exch + sebi) + c["slippage"] * v


# ---------------------------------------------------------------- paper take, followed tick by tick
class Take:
    def __init__(self, t, plan):
        self.t, self.sym, self.mode = t, t["symbol"], t.get("mode", "intraday")
        p = (plan or {}).get(self.mode) or {}
        self.legs = {(x["side"], x["leg"]): x for x in p.get("legs", []) if x.get("price") is not None}
        self.sides = {x["side"]: x for x in p.get("sides", [])}
        lean = t.get("lean") or p.get("lean")
        self.allowed = ["long"] if lean == "up" else ["short"] if lean == "down" else ["long", "short"]
        self.state, self.side, self.entry, self.qty, self.left, self.stop = "waiting", None, None, 0, 0, None
        self.booked, self.events, self.last, self.paid = 0.0, [], None, 0.0
        self.delivery = self.mode != "intraday"
        self.risk = float(t.get("risk_rs") or 0) or None

    def _fill(self, opening, px, qty):
        buy = (self.side == "long") == opening
        self.paid += fill_cost(buy, px * qty, self.delivery)

    def _ev(self, ts, what, price):
        self.events.append(dict(at=str(ts)[11:19], what=what, price=round(price, 2)))
        return f"{self.sym} paper {self.side or ''}: {what} at {price:.2f}"

    def tick(self, ts, px):
        """Returns alert texts (or [])."""
        self.last, out = px, []
        if self.state == "waiting":
            if self.mode == "intraday" and ts.time() < ENTRY_T:
                return out
            if self.mode == "intraday" and ts.time() >= EXIT_T:
                self.state = "no trade"
                return out
            for sd in self.allowed:
                e1 = self.legs.get((sd, "E1"))
                if e1 and ((sd == "long" and px >= e1["price"]) or (sd == "short" and px <= e1["price"])):
                    self.state, self.side, self.entry = "in", sd, px
                    self.qty = self.left = int(e1.get("shares") or self.sides.get(sd, {}).get("shares") or 0)
                    self.stop = (self.legs.get((sd, "SL")) or {}).get("price")
                    self.risk = float(self.sides.get(sd, {}).get("risk_rs") or 0) or self.risk
                    self._fill(True, px, self.qty)
                    out.append(self._ev(ts, f"entered {self.qty} shares (E1 {e1['price']:.2f})", px))
                    break
            return out
        if self.state not in ("in", "half"):
            return out
        sg = 1 if self.side == "long" else -1
        if self.mode == "intraday" and ts.time() >= EXIT_T:
            return self._exit(ts, px, "closed at 15:15")
        if self.stop is not None and sg * (px - self.stop) <= 0:
            return self._exit(ts, px, "stop hit" if self.state == "in" else "back to entry: rest out")
        t1, t2 = (self.legs.get((self.side, "T1")) or {}).get("price"), (self.legs.get((self.side, "T2")) or {}).get("price")
        if self.state == "in" and t1 is not None and sg * (px - t1) >= 0:
            half = self.qty // 2
            self.booked += sg * (px - self.entry) * half
            self._fill(False, px, half)
            self.left -= half
            self.state, self.stop = "half", self.entry
            out.append(self._ev(ts, f"T1: booked {half}, stop to entry", px))
        if t2 is not None and sg * (px - t2) >= 0:
            out += self._exit(ts, px, "T2: booked the rest")
        return out

    def _exit(self, ts, px, why):
        sg = 1 if self.side == "long" else -1
        self.booked += sg * (px - self.entry) * self.left
        self._fill(False, px, self.left)
        self.left, self.state = 0, "closed"
        return [self._ev(ts, why, px)]

    def view(self):
        sg = 1 if self.side == "long" else -1
        live = self.entry is not None and self.last is not None
        gross = self.booked + (sg * (self.last - self.entry) * self.left if live else 0)
        cost = self.paid + (fill_cost(self.side == "short", self.last * self.left, self.delivery) if live and self.left else 0)   # + the exit still to pay
        pnl = gross - cost
        return dict(symbol=self.sym, mode=self.mode, lean=self.t.get("lean"), state=self.state, side=self.side,
                    entry=None if self.entry is None else round(self.entry, 2), qty=self.qty, left=self.left, stop=self.stop,
                    last=self.last, gross=round(gross, 0), costs=round(cost, 0), pnl=round(pnl, 0),
                    r=round(pnl / self.risk, 2) if self.risk and self.entry is not None else None,
                    events=self.events[-6:])


# ---------------------------------------------------------------- the book: all live state, fed by ticks
class Book:
    def __init__(self, stocks, indices, watch=(), plans=None, takes=(), picks=(), prev_close=None, sources=None, sectors=None):
        """stocks / indices: {token: symbol-or-column}. watch: symbols that get bars and alerts."""
        self.lock = threading.RLock()
        self.stocks, self.indices = dict(stocks), dict(indices)
        self.sym_tok = {v: k for k, v in self.stocks.items()}
        self.watch = set(watch)
        self.sources = sources or {}
        self.sector_of = dict(sectors or {})
        self.plans = plans or {}
        self.q = {}                                   # name -> quote
        self.bars, self._cumv = {}, {}                 # name -> {bar start: [o, h, l, c, v]}
        self.base = {}                                 # name -> bars from Angel's candle API (before the feed started)
        self.fill = dict(done=0, failed=0, error=None, waiting=None)  # backfill progress
        self.alerts, self.seq, self.fired = deque(maxlen=3000), 0, set()
        self.armed = {}                                # (sym, side) -> entry price, for plan alerts
        self.beyond = {}                               # sym -> seconds beyond +/-1%, last ts
        self.takes = [Take(t, self.plans.get(t["symbol"])) for t in takes]
        self.picks = list(picks)
        self.prev = dict(prev_close or {})
        self.ticks, self.last_ts, self.day = 0, None, None
        self.raw, self.zero, self.raw_ts = 0, 0, None              # every packet / packets with no price (pre-open shows here)

    def add_watch(self, sym, src="desk"):
        with self.lock:
            self.watch.add(sym)
            self.sources.setdefault(sym, set()).add(src)

    def signals(self, sym):
        """Compact read for a watch row: Market Lab (best pick for the stock), TA and fundamentals labels, plan lean."""
        p = self.plans.get(sym) or {}
        ml = [x for x in self.picks if x["symbol"] == sym]
        best = max(ml, key=lambda x: x["chance"] - x["usual"]) if ml else None
        return dict(ml=None if not best else dict(move=best["move"], chance=best["chance"], usual=best["usual"], lean=best["lean"], conf=best["confidence"]),
                    ta=(p.get("ta") or {}).get("dir"), ta_score=(p.get("ta") or {}).get("score"), fund=(p.get("fund") or {}).get("dir"),
                    lean=((p.get("intraday") or {}).get("lean")))

    def call(self, sym):
        """Everything MarketX said about the stock last night: Market Lab picks, TA, fundamentals, both plans."""
        p = self.plans.get(sym) or {}
        plan = lambda m: None if not p.get(m) else dict(lean=p[m].get("lean"), hold=p[m].get("hold"), tradeable=p[m].get("tradeable"),
                                                        flags=p[m].get("flags", []), sides=p[m].get("sides", []),
                                                        legs=[x for x in p[m].get("legs", []) if x.get("price") is not None])
        return dict(picks=[x for x in self.picks if x["symbol"] == sym], ta=p.get("ta"), fund=p.get("fund"),
                    patterns=p.get("patterns", []), intraday=plan("intraday"), swing=plan("swing"), has_plan=bool(p))

    def alert(self, ts, sym, kind, text, tone="info"):
        self.seq += 1
        self.alerts.append(dict(id=self.seq, at=str(ts)[11:19], symbol=sym, kind=kind, text=text, tone=tone))

    def tick(self, t):
        """t: one parsed SmartWebSocketV2 message (prices in paise) or the same shape from replay."""
        self.raw += 1
        self.raw_ts = now()
        if not t.get("last_traded_price"):
            self.zero += 1
        tok = str(t.get("token"))
        name = self.stocks.get(tok) or self.indices.get(tok)
        if not name:
            return
        ms = t.get("exchange_timestamp")
        ts = dt.datetime.fromtimestamp(ms / 1000, IST).replace(tzinfo=None) if ms else now()
        px = t.get("last_traded_price", 0) / 100
        if px <= 0:
            return
        with self.lock:
            self.ticks += 1
            self.last_ts, self.day = ts, ts.date()
            q = self.q.setdefault(name, {})
            prev = (t.get("closed_price") or 0) / 100 or self.prev.get(name)
            q.update(ltp=px, prev=prev, ts=ts, open=(t.get("open_price_of_the_day") or 0) / 100 or q.get("open"),
                     high=max(px, (t.get("high_price_of_the_day") or 0) / 100, q.get("high") or px),
                     low=min(px, (t.get("low_price_of_the_day") or 0) / 100 or px, q.get("low") or px),
                     vwap=(t.get("average_traded_price") or 0) / 100 or q.get("vwap"), vol=t.get("volume_trade_for_the_day") or q.get("vol"))
            is_idx = tok in self.indices
            if is_idx or name in self.watch:
                self._bar(name, ts, px, q.get("vol"))
            if not is_idx and name in self.watch and ts.time() >= OPEN_T:
                self._alerts(name, ts, px, prev)
            for tk in self.takes:
                if tk.sym == name and ts.time() >= OPEN_T:
                    for msg in tk.tick(ts, px):
                        self.alert(ts, name, "paper", msg, "accent")

    def _bar(self, name, ts, px, cumv):
        if ts.time() < OPEN_T or ts.time() > CLOSE_T:
            return
        b = self.bars.setdefault(name, {})
        k = bar_start(ts)
        dv = 0
        if cumv is not None:
            last = self._cumv.get(name)
            dv = max(0, cumv - last) if last is not None else 0
            self._cumv[name] = cumv
        if k in b:
            x = b[k]
            x[1], x[2], x[3], x[4] = max(x[1], px), min(x[2], px), px, x[4] + dv
        else:
            b[k] = [px, px, px, px, dv]

    def _once(self, key):
        if key in self.fired:
            return False
        self.fired.add(key)
        return True

    def _alerts(self, sym, ts, px, prev):
        if prev:
            ch = 100 * (px / prev - 1)
            for lv in CROSS:
                for sg in (1, -1):
                    if sg * ch >= lv and self._once((sym, "x", sg, lv)):
                        self.alert(ts, sym, "cross", f"{sym} {'+' if sg > 0 else '-'}{lv:g}% from yesterday ({px:.2f})", "up" if sg > 0 else "down")
            bt = self.beyond.setdefault(sym, dict(s=0.0, ts=None, first=None))
            if abs(ch) >= PICK_SIZE:
                if bt["ts"] is not None:
                    bt["s"] += min(60.0, (ts - bt["ts"]).total_seconds())
                bt["first"] = bt["first"] or str(ts)[11:16]
                bt["ts"] = ts
            else:
                bt["ts"] = None
        p = ((self.plans.get(sym) or {}).get("intraday") or {})
        legs = {(x["side"], x["leg"]): x["price"] for x in p.get("legs", []) if x.get("price") is not None}
        for sd, sg in (("long", 1), ("short", -1)):
            e1 = legs.get((sd, "E1"))
            if e1 is None:
                continue
            if (sym, sd) not in self.armed:
                if ts.time() >= ENTRY_T and sg * (px - e1) >= 0 and self._once((sym, sd, "E1")):
                    self.armed[(sym, sd)] = px
                    self.alert(ts, sym, "plan", f"{sym} E1 {sd} triggered at {px:.2f} (plan {e1:.2f})", "up" if sg > 0 else "down")
                continue
            for leg, hit in (("SL", lambda v: sg * (px - v) <= 0), ("T1", lambda v: sg * (px - v) >= 0), ("T2", lambda v: sg * (px - v) >= 0)):
                v = legs.get((sd, leg))
                if v is not None and hit(v) and self._once((sym, sd, leg)):
                    self.alert(ts, sym, "plan", f"{sym} {sd}: {leg} {'hit' if leg == 'SL' else 'reached'} at {px:.2f} (plan {v:.2f})",
                               "down" if leg == "SL" else "up")

    # ---------------- views
    def _qv(self, name):
        q = self.q.get(name) or {}
        ltp, prev = q.get("ltp"), q.get("prev")
        return dict(ltp=ltp, prev=prev, chg=None if not (ltp and prev) else round(100 * (ltp / prev - 1), 2),
                    open=q.get("open"), high=q.get("high"), low=q.get("low"), vwap=q.get("vwap"),
                    vs_vwap=None if not (ltp and q.get("vwap")) else round(100 * (ltp / q["vwap"] - 1), 2),
                    ts=str(q["ts"])[11:19] if q.get("ts") else None)

    def view(self, since=0):
        with self.lock:
            strip = []
            for col, label in STRIP:
                if col in self.q:
                    strip.append(dict(key=col, label=label, **self._qv(col)))
            ch = [v for k, v in ((k, self._qv(k)) for k in self.stocks.values()) if v["chg"] is not None]
            adv, dec = sum(v["chg"] > 0 for v in ch), sum(v["chg"] < 0 for v in ch)
            breadth = dict(n=len(ch), adv=adv, dec=dec, unch=len(ch) - adv - dec,
                           above_vwap=sum(1 for v in ch if (v["vs_vwap"] or 0) > 0), up1=sum(v["chg"] >= 1 for v in ch),
                           down1=sum(v["chg"] <= -1 for v in ch))
            rows = []
            for s in sorted(self.watch):
                v = self._qv(s)
                fired = sorted(f"{k[1]} {k[2]}" for k in self.fired if k[0] == s and k[1] in ("long", "short"))
                cross = [f"{'+' if k[2] > 0 else '-'}{k[3]:g}%" for k in self.fired if k[0] == s and k[1] == "x"]
                rows.append(dict(symbol=s, src=sorted(self.sources.get(s, [])), fired=fired, crossed=sorted(cross), sector=self.sector_of.get(s),
                                 **self.signals(s), **v))
            picks = []
            for p in self.picks:
                v = self._qv(p["symbol"])
                q = self.q.get(p["symbol"]) or {}
                base = q.get("prev") if p["move"] == "cc" else q.get("open")
                mv = None if not (v["ltp"] and base) else 100 * (v["ltp"] / base - 1)
                far = None if not base or not q.get("high") else 100 * max(q["high"] / base - 1, 1 - q["low"] / base)
                bt = self.beyond.get(p["symbol"]) or {}
                picks.append(dict(p, ltp=v["ltp"], move_now=None if mv is None else round(mv, 2), touched=None if far is None else far >= p["size"],
                                  moved=None if mv is None else abs(mv) >= p["size"], beyond_min=round(bt.get("s", 0) / 60, 1), first=bt.get("first"),
                                  lean_right=None if mv is None or p["lean"] not in ("up", "down") or abs(mv) < 0.1 else (mv > 0) == (p["lean"] == "up")))
            new = [a for a in self.alerts if a["id"] > since]
            sectors = self._sectors()
            idx = [dict(key=k, label=index_label(k), **self._qv(k)) for k in sorted(set(self.indices.values())) if k in self.q]
            for x in idx:
                x["vs_open"] = None if not (x["ltp"] and x["open"]) else round(100 * (x["ltp"] / x["open"] - 1), 2)
            return dict(strip=strip, breadth=breadth, watch=rows, picks=picks, takes=[t.view() for t in self.takes], fill=dict(self.fill),
                        sectors=sectors, indices=idx,
                        alerts=new[-200:], seq=self.seq, ticks=self.ticks, last=str(self.last_ts)[:19] if self.last_ts else None,
                        packets=dict(all=self.raw, no_price=self.zero, last=str(self.raw_ts)[11:19] if self.raw_ts else None))

    def _sectors(self):
        """Live sector table from every stock's quote: up / down, median change, best / worst, the sector index if any."""
        try:
            from core.indices import SECTOR_INDEX
        except Exception:
            SECTOR_INDEX = {}
        g = {}
        for s in self.stocks.values():
            v = self._qv(s)
            if v["chg"] is None:
                continue
            g.setdefault(self.sector_of.get(s) or "Other", []).append((s, v["chg"], v["vs_vwap"]))
        out = []
        for sec, xs in g.items():
            ch = sorted(x[1] for x in xs)
            best, worst = max(xs, key=lambda x: x[1]), min(xs, key=lambda x: x[1])
            ix = SECTOR_INDEX.get(sec)
            iv = self._qv(ix) if ix and ix in self.q else None
            out.append(dict(sector=sec, n=len(xs), adv=sum(1 for x in xs if x[1] > 0), dec=sum(1 for x in xs if x[1] < 0),
                            median=round(float(np.median(ch)), 2), above_vwap=sum(1 for x in xs if (x[2] or 0) > 0),
                            best=dict(symbol=best[0], chg=best[1]), worst=dict(symbol=worst[0], chg=worst[1]),
                            index=None if not iv else dict(key=ix, label=index_label(ix), chg=iv["chg"])))
        return sorted(out, key=lambda x: -x["median"])

    def sector_rows(self, sector):
        with self.lock:
            rows = [dict(symbol=s, watched=s in self.watch, **self._qv(s)) for s in self.stocks.values() if (self.sector_of.get(s) or "Other") == sector]
        return sorted([r for r in rows if r["chg"] is not None], key=lambda r: -r["chg"])

    def chart(self, sym, extra=None):
        with self.lock:
            live = {k: list(v) for k, v in self.bars.get(sym, {}).items()}
            base = dict(extra or self.base.get(sym) or {})
            q = dict(self.q.get(sym) or {})
        b = fill_gaps(merge_bars(base, live))
        ks = sorted(b)
        t = [k.strftime("%Y-%m-%dT%H:%M:00") for k in ks]
        o, h, l, c, v = ([b[k][i] for k in ks] for i in range(5))
        tp = np.array([(h[i] + l[i] + c[i]) / 3 for i in range(len(ks))])
        vol = np.array(v, float)
        cv = np.cumsum(vol)
        vwap = [None if cv[i] <= 0 else round(float(np.cumsum(tp * vol)[i] / cv[i]), 2) for i in range(len(ks))]
        orb = [k for k in ks if k.time() < ENTRY_T]
        orh = max(b[k][1] for k in orb) if orb else None
        orl = min(b[k][2] for k in orb) if orb else None
        plan = (self.plans.get(sym) or {}).get("intraday") or {}
        first = ks[0] if ks else None
        offset = 0 if not first else max(0, (first.hour * 60 + first.minute - (9 * 60 + 15)) // BAR_MIN)
        return dict(symbol=sym, index=sym in set(self.indices.values()), label=index_label(sym) if sym in set(self.indices.values()) else sym,
                    offset=offset, slots=SLOTS, bars=dict(t=t, o=o, h=h, l=l, c=c, v=v, vwap=vwap), prev=q.get("prev"), ltp=q.get("ltp"),
                    or_high=orh, or_low=orl, legs=[x for x in plan.get("legs", []) if x.get("price") is not None], lean=plan.get("lean"),
                    call=self.call(sym), from_api=len([k for k in base if k not in live]))

    def save(self, d=None):
        with self.lock:
            day = self.day
            if not day:
                return
            rows = [dict(name=n, ts=k, open=x[0], high=x[1], low=x[2], close=x[3], volume=x[4]) for n, b in self.bars.items() for k, x in b.items()]
            alerts = list(self.alerts)
            takes = [t.view() for t in self.takes]
        out = (d or DIR) / str(day)
        out.mkdir(parents=True, exist_ok=True)
        if rows:
            atomic.to_parquet(pd.DataFrame(rows), out / "bars.parquet")
        atomic.write_text(out / "alerts.json", json.dumps(alerts, indent=0, default=str))
        atomic.write_text(out / "takes.json", json.dumps(takes, indent=1, default=str))


def fill_gaps(b):
    """5-min slots with no trade get a flat bar at the last close (keeps the time axis honest)."""
    if not b:
        return b
    ks = sorted(b)
    out, k, last = {}, ks[0], None
    step = dt.timedelta(minutes=BAR_MIN)
    while k <= ks[-1]:
        if k in b:
            out[k] = b[k]
            last = b[k][3]
        elif last is not None and dt.time(9, 15) <= k.time() <= dt.time(15, 25):
            out[k] = [last, last, last, last, 0]
        k += step
    return out


def merge_bars(base, live):
    """Bars from the candle API before the feed started + bars built from ticks. The first live bar was only partly seen
    by the feed, so it is joined with the API's bar for the same 5 minutes."""
    out = dict(base)
    if not live:
        return out
    first = min(live)
    for k, x in live.items():
        a = base.get(k)
        out[k] = [a[0], max(a[1], x[1]), min(a[2], x[2]), x[3], max(a[4], x[4])] if a and k == first else x
    return {k: v for k, v in out.items() if k >= first or k in base}


# ---------------------------------------------------------------- inputs: tokens, plans, picks, takes
ADDED = DIR / "added.json"


def added():
    try:
        return [str(x) for x in json.loads(ADDED.read_text())][:50]
    except (OSError, ValueError):
        return []


def save_added(sym):
    a = added()
    if sym not in a:
        DIR.mkdir(parents=True, exist_ok=True)
        atomic.write_text(ADDED, json.dumps(a + [sym]))
def index_tokens(log=print):
    try:
        d = json.loads(IDX_FILE.read_text())
        if d:
            return d
    except (OSError, ValueError):
        pass
    try:                                                # public scrip master, once; saved for next time
        import requests
        from core import indices as IX
        sm = pd.DataFrame(requests.get("https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json", timeout=60).json())
        nse = sm[sm.exch_seg == "NSE"]
        tok, _ = IX.tokens(nse)
        ix = nse[nse.instrumenttype == "AMXIDX"]
        for col, names in (("nifty", {"NIFTY", "NIFTY 50", "NIFTY50"}), ("vix", {"INDIA VIX", "INDIAVIX"})):
            m = ix[ix.name.str.upper().isin(names) | ix.symbol.str.upper().isin(names)]
            if len(m):
                tok[col] = m.token.iat[0]
        tok = {k: str(v) for k, v in tok.items()}
        atomic.write_text(IDX_FILE, json.dumps(tok, indent=1))
        return tok
    except Exception as e:
        log(f"Live: index list not downloaded ({type(e).__name__}); using Nifty, Bank Nifty and VIX only")
        return dict(FALLBACK_IDX)


def stock_tokens():
    inst = pd.read_csv(C.DATA / "instruments.csv", dtype={"token": str})
    uni = set(pd.read_csv(C.DATA / "universe.csv").Symbol)
    inst = inst[inst.symbol.isin(uni)].drop_duplicates("symbol")
    return dict(zip(inst.token, inst.symbol))


def picks_for(radar_day=None):
    """Market Lab's top picks (cc and oc, >= 1%) from the latest radar: symbol, move, size, lean, chance, usual."""
    from marketlab import radar as R, review as RV, evaluate as V
    rd = R.read("radar", radar_day or R.latest_day())
    if rd.empty:
        return []
    outc = rd[["symbol", "date"]].assign(cc=np.nan, oc=np.nan, gp=np.nan)
    sk, _ = V.skill_map()
    calls = RV._calls(rd, outc, sk)
    if calls.empty:
        return []
    c = calls[calls.pick & calls.move.isin(["cc", "oc"]) & (calls["size"] == PICK_SIZE)]
    return [dict(symbol=r.symbol, move=r.move, size=float(r["size"]), lean=r.lean, chance=round(float(r.chance), 3),
                 usual=round(float(r.usual), 3), confidence=r.confidence, rank=int(r["rank"])) for _, r in c.sort_values(["move", "rank"]).iterrows()]


def inputs(plan_day=None):
    """Everything the Book needs except ticks: watch list with sources, plans, picks, paper takes, previous closes."""
    from tradeplan import plan as TP
    from desk import notes as N
    from web.r_tradeplan import paper_list
    saved = TP.load(plan_day) or {}
    day = saved.get("day")
    plans = saved.get("plans", {})
    picks = picks_for(day) if day else []
    takes = [t for t in paper_list() if t.get("day") == day]
    src = {}
    for s in N.watchlist():
        src.setdefault(s, set()).add("desk")
    for p in picks:
        src.setdefault(p["symbol"], set()).add("pick")
    for t in takes:
        src.setdefault(t["symbol"], set()).add("paper")
    for s in added():
        src.setdefault(s, set()).add("added")
    return dict(day=day, plans=plans, picks=picks, takes=takes, sources=src)


# ---------------------------------------------------------------- the feed (one per server process)
class Feed:
    def __init__(self):
        self.book, self.state, self.msg, self.mode = None, "stopped", "", None
        self._stop, self._ws, self._api, self._thr = threading.Event(), None, None, None
        self.started, self.reconnects, self._backfilled, self._lock = None, 0, {}, threading.Lock()
        self._lock_api, self._last_call = threading.Lock(), 0.0

    def status(self):
        b = self.book
        return dict(state=self.state, mode=self.mode, msg=self.msg, started=self.started, reconnects=self.reconnects,
                    ticks=b.ticks if b else 0, last=str(b.last_ts)[:19] if b and b.last_ts else None,
                    market_open=market_open(), now=str(now())[:19])

    def _build(self, plan_day=None, prev=None):
        inp = inputs(plan_day)
        stocks = stock_tokens()
        idx = {str(v): k for k, v in index_tokens(self._log).items()}                      # every index Angel lists (about 20)
        try:
            u = pd.read_csv(C.DATA / "universe.csv")
            sectors = dict(zip(u.Symbol, u.Industry))
        except Exception:
            sectors = {}
        self.book = Book(stocks, idx, watch=set(inp["sources"]), plans=inp["plans"], takes=inp["takes"], picks=inp["picks"],
                         prev_close=prev, sources=inp["sources"], sectors=sectors)
        self.plan_day = inp["day"]

    def _log(self, m):
        self.msg = m

    def start(self, replay=False, day=None, speed=4.0):
        with self._lock:
            if self.state in ("starting", "live", "replay"):
                return self.status()
            if not replay and not market_open():
                raise RuntimeError("market is closed: live runs 09:00-15:35 on weekdays (try Replay)")
            self._stop.clear()
            self.state, self.mode, self.started, self.reconnects, self._backfilled = "starting", "replay" if replay else "live", str(now())[:19], 0, {}
            self._thr = threading.Thread(target=self._replay if replay else self._run, kwargs=dict(day=day, speed=speed) if replay else {}, daemon=True, name="mx-live")
            self._thr.start()
            return self.status()

    def stop(self):
        self._stop.set()
        ws = self._ws
        if ws:
            try:
                ws.close_connection()
            except Exception:
                pass
        if self.book:
            try:
                self.book.save()
            except Exception:
                pass
        self.state = "stopped"
        return self.status()

    def add(self, sym, src="desk"):
        b = self.book
        if not b:
            return
        b.add_watch(sym, src)
        if src == "added":
            save_added(sym)
        tok = b.sym_tok.get(sym)
        if self._ws and tok:
            try:
                self._ws.subscribe("mx2", QUOTE_MODE, [dict(exchangeType=NSE_CM, tokens=[tok])])
            except Exception:
                pass

    # ---- live
    def _login(self):
        import pyotp, fetch_angel as FA
        from SmartApi import SmartConnect
        c = FA.creds()
        api = SmartConnect(api_key=c["ANGEL_API_KEY"], timeout=20)
        s = api.generateSession(c["ANGEL_CLIENT_CODE"], c["ANGEL_PIN"], pyotp.TOTP(c["ANGEL_TOTP_SECRET"]).now())
        if not s or not s.get("status"):
            raise RuntimeError(f"Angel login failed: {(s or {}).get('message', 'no response')}")
        self._api = api
        return c, s["data"]["jwtToken"], api.getfeedToken()

    def _run(self):
        from SmartApi.smartWebSocketV2 import SmartWebSocketV2
        try:
            self._build()
        except Exception as e:
            self.state, self.msg = "error", f"could not load the watch list: {type(e).__name__}: {e}"
            return
        saver = threading.Thread(target=self._saver, daemon=True)
        saver.start()
        threading.Thread(target=self._fill_loop, daemon=True, name="mx-live-fill").start()
        wait = 3
        while not self._stop.is_set():
            if not market_open():
                self.msg = "market closed: stopped"
                break
            try:
                c, jwt, feed = self._login()
                ws = SmartWebSocketV2(jwt, c["ANGEL_API_KEY"], c["ANGEL_CLIENT_CODE"], feed, max_retry_attempt=2, retry_delay=5)
                b = self.book
                toks = [dict(exchangeType=NSE_CM, tokens=list(b.stocks) + list(b.indices))]

                def on_open(_):
                    self.state, self.msg = "live", "connected"
                    ws.subscribe("mx", QUOTE_MODE, toks)

                def on_data(_, m):
                    try:
                        b.tick(m)
                    except Exception as e:
                        self.msg = f"tick skipped: {type(e).__name__}"

                ws.on_open, ws.on_data = on_open, on_data
                ws.on_error = lambda *a: setattr(self, "msg", "feed error: reconnecting")
                ws.on_close = lambda *a: None
                self._ws = ws
                ws.connect()                                  # blocks until the socket closes
                wait = 3
            except SystemExit:
                self.state, self.msg = "error", "Angel keys missing in ~/.angel_env"
                return
            except Exception as e:
                self.msg = f"{type(e).__name__}: {str(e)[:120]} (retrying)"
            self._ws = None
            if self._stop.is_set():
                break
            self.reconnects += 1
            self.state = "reconnecting"
            self._stop.wait(wait)
            wait = min(60, wait * 2)
        self.book and self.book.save()
        self.state = "stopped"

    def _saver(self):
        while not self._stop.wait(60):
            try:
                if self.book:
                    self.book.save()
            except Exception:
                pass
            if self.state in ("live", "reconnecting") and not market_open():
                self.stop()
                self.msg = "market closed: stopped and saved"

    def backfill(self, sym, force=False):
        """Today's 5-min bars from 09:15 (Angel candle API). Kept in book.base; a failure is retried after a minute."""
        b, api = self.book, self._api
        if self.mode != "live" or not b or not api:
            return (b.base.get(sym) if b else None) or {}
        if sym in b.base and not force:
            return b.base[sym]
        if time.time() - self._backfilled.get(sym, 0) < 60:
            return {}
        self._backfilled[sym] = time.time()
        tok, t = b.sym_tok.get(sym), now()
        if not tok or t.time() <= OPEN_T:
            return {}
        try:
            with self._lock_api:
                wait = 0.4 - (time.time() - self._last_call)
                if wait > 0:
                    time.sleep(wait)                          # Angel allows 3 candle calls a second
                r = api.getCandleData(dict(exchange="NSE", symboltoken=str(tok), interval="FIVE_MINUTE",
                                           fromdate=f"{t.date()} 09:15", todate=t.strftime("%Y-%m-%d %H:%M")))
                self._last_call = time.time()
            rows = (r or {}).get("data") if isinstance(r, dict) else None
            if not rows:
                raise RuntimeError(str((r or {}).get("message") if isinstance(r, dict) else r)[:120] or "no bars returned")
            got = {}
            for row in rows:
                k = pd.Timestamp(str(row[0])[:19]).to_pydatetime()
                got[k] = [float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])]
            with b.lock:
                b.base[sym] = got
                b.fill["done"] += 1
            return got
        except Exception as e:
            with b.lock:
                b.fill["failed"] += 1
                b.fill["error"] = f"{sym}: {type(e).__name__}: {str(e)[:120]}"
            return {}

    def _fill_loop(self):
        """Earlier bars for every watched stock, from 09:20 (when there is a bar to fetch); failures (timeouts at the open
        are common) are retried every minute until they load."""
        b = self.book
        while not self._stop.wait(20):
            if self.book is not b or self.state == "stopped":
                return
            if self.state != "live" or now().time() < dt.time(9, 20):
                continue
            for s in sorted(b.watch):
                if self._stop.is_set():
                    return
                if s not in b.base:
                    self.backfill(s)
            with b.lock:
                b.fill["waiting"] = len([s for s in b.watch if s not in b.base])

    # ---- replay: a saved session's 5-min bars as ticks
    def _replay(self, day=None, speed=4.0):
        try:
            from core import intraday as ID
            files = sorted(ID.BARS.glob("*.parquet"))
            if not files:
                raise RuntimeError("no 5-minute bars saved (Data health -> Intraday)")
            if not day:
                day = str(pd.read_parquet(files[0], columns=["ts"]).ts.max().date())
            prev_day = _prev_session(day)
            from tradeplan import plan as TP
            plan_day = prev_day if TP.load(prev_day) else None          # no plan saved for that night: use the latest (test only)
            px = pd.read_parquet(C.DATA / "prices.parquet", columns=["date", "symbol", "close"])
            px = px[px.date == pd.Timestamp(prev_day)]
            self._build(plan_day=plan_day, prev=dict(zip(px.symbol, px.close)))
            plan_day = self.plan_day
            b = self.book
            ticks = replay_ticks(b, day)
            if not ticks:
                raise RuntimeError(f"no 5-minute bars for {day}")
            self.state, self.msg = "replay", f"replaying {day} (plan of {plan_day}) at {speed:g} bars/s"
            last = None
            for tk in ticks:
                if self._stop.is_set():
                    break
                k = tk["_bar"]
                if last is not None and k != last:
                    time.sleep(1.0 / max(0.2, float(speed)))
                last = k
                b.tick(tk)
            self.msg = f"replay of {day} finished"
        except Exception as e:
            self.msg = f"replay failed: {type(e).__name__}: {e}"
        self.state = "stopped"


def _prev_session(day):
    px = pd.read_parquet(C.DATA / "prices.parquet", columns=["date"]).date.drop_duplicates().sort_values()
    p = px[px < pd.Timestamp(day)]
    return str(p.iat[-1].date()) if len(p) else str(day)


def replay_ticks(book, day, symbols=None):
    """Four ticks per 5-min bar (open, then low/high in the bar's direction, close), stamped inside the bar; cumulative
    volume like Angel's. Watched stocks + every universe stock (for breadth) that has bars that day."""
    from core import intraday as ID
    out = []
    names = symbols or list(book.stocks.values())
    for s in names:
        f = ID.BARS / f"{s}.parquet"
        if not f.exists():
            continue
        d = pd.read_parquet(f)
        d = d[d.ts.dt.date == pd.Timestamp(day).date()].sort_values("ts")
        if d.empty:
            continue
        tok, cum, pv, o0, hi, lo = book.sym_tok.get(s), 0, 0.0, float(d.open.iat[0]), -1e18, 1e18
        prev = book.prev.get(s)
        for r in d.itertuples():
            seq = [r.open, r.low, r.high, r.close] if r.close >= r.open else [r.open, r.high, r.low, r.close]
            for j, p in enumerate(seq):
                dv = int(r.volume) // 4 if j < 3 else int(r.volume) - 3 * (int(r.volume) // 4)
                cum, pv = cum + dv, pv + dv * p
                hi, lo = max(hi, p), min(lo, p)
                ts = r.ts.to_pydatetime() + dt.timedelta(seconds=30 + 70 * j)
                out.append(dict(token=tok, _bar=r.ts, exchange_timestamp=int(ts.replace(tzinfo=IST).timestamp() * 1000),
                                last_traded_price=round(p * 100), closed_price=round(prev * 100) if prev else 0,
                                open_price_of_the_day=round(o0 * 100), high_price_of_the_day=round(hi * 100), low_price_of_the_day=round(lo * 100),
                                volume_trade_for_the_day=cum, average_traded_price=round(100 * pv / cum) if cum else 0))
    out.sort(key=lambda x: x["exchange_timestamp"])
    return out


FEED = Feed()
