"""Money simulation: "if I had put Rs X into MarketX's picks, what would it be worth now?"

Uses only the projections kept that night in the Review ledger (no hindsight) and the real open/close prices.
One round = buy on a projection, sell after the hold, then the next round uses the projection made on the sell day,
so money is never in two rounds at once.
  hold 'intraday': buy at the next session's open, sell at its close (intraday costs)
  hold N (1/3/5):  buy at the projection day's close, sell at the close N sessions later (delivery costs)
Stocks: 'top' = top N picks by edge (close-to-close >=1% radar), 'up' = those that leaned up, 'plans' = the intraday
plans that were entered (entry and exit from plans_grade), 'basket' = the same list of stocks every round.
A round passes when it made money after costs. The run passes when it ended above the amount and beat Nifty
(bought and held over the same days). Long only, except 'plans', which follows each plan's side."""
import numpy as np, pandas as pd
from core import config as C
from . import review as RV, tally as T

PICKS = ("top", "up", "plans", "basket")
HOLDS = ("intraday", 1, 3, 5)
TOPS = (5, 10, 20)
MIN_ROUNDS = 60                     # fewer traded rounds than this -> 'small sample' warning


def defaults():
    return dict(amount=100000.0, start=None, end=None, picks="top", top=10, hold=1, split="equal", reinvest=True, costs=True, basket=[])


def _cost(buy, value, delivery):
    from core.live import fill_cost
    return fill_cost(buy, value, delivery=delivery)


class Data:
    """Everything a run needs, loaded once (a compare grid reuses it)."""
    def __init__(self):
        ds = T.days()
        self.days = sorted(d["day"] for d in ds)
        self.kind = {d["day"]: d["kind"] for d in ds}
        first = pd.Timestamp(self.days[0]) - pd.Timedelta(days=10) if self.days else pd.Timestamp("2100-01-01")
        try:
            px = pd.read_parquet(C.DATA / "prices.parquet", columns=["date", "symbol", "open", "close"])
            px = px[px.date >= first]
        except Exception:
            px = pd.DataFrame(columns=["date", "symbol", "open", "close"])
        self.open = px.pivot_table(index="date", columns="symbol", values="open", aggfunc="last")
        self.close = px.pivot_table(index="date", columns="symbol", values="close", aggfunc="last")
        self.sessions = [pd.Timestamp(x) for x in sorted(self.close.index)]
        try:
            nf = pd.read_parquet(C.DATA / "index.parquet", columns=["date", "nifty"]).dropna().set_index("date").nifty.sort_index()
        except Exception:
            nf = pd.Series(dtype=float)
        self.nifty = nf
        self._calls, self._plans = {}, {}

    def calls(self, day):
        if day not in self._calls:
            try:
                c, _ = T._calls(self.kind[day], day, "cc", 1.0)
                self._calls[day] = c.sort_values("rank")
            except Exception:
                self._calls[day] = pd.DataFrame(columns=["symbol", "rank", "pick", "lean", "chance", "usual"])
        return self._calls[day]

    def plans(self, day):
        if day not in self._plans:
            f = RV._dir(self.kind[day], day) / "plans_grade.parquet"
            try:
                g = pd.read_parquet(f)
                g = g[(g["mode"] == "intraday") & g.entry.notna() & g.exit_price.notna() & (g.status == "done")]
                self._plans[day] = g.set_index("symbol")
            except Exception:
                self._plans[day] = pd.DataFrame()
        return self._plans[day]

    def nifty_at(self, t):
        s = self.nifty[self.nifty.index <= t]
        return float(s.iloc[-1]) if len(s) else None

    def after(self, t, n):
        """The n-th session after date t (None if not in prices yet)."""
        later = [s for s in self.sessions if s > t]
        return later[n - 1] if len(later) >= n else None

    def px(self, frame, t, sym):
        try:
            v = float(frame.at[t, sym])
            return v if v > 0 and not np.isnan(v) else None
        except (KeyError, ValueError, TypeError):
            return None


def _norm(p):
    o = defaults()
    o.update({k: v for k, v in (p or {}).items() if v is not None})
    o["amount"] = float(o["amount"])
    if not 1000 <= o["amount"] <= 1e10:
        raise ValueError("amount must be between 1,000 and 10,00,00,00,000")
    if o["picks"] not in PICKS:
        raise ValueError("picks must be one of " + ", ".join(PICKS))
    o["hold"] = o["hold"] if o["hold"] == "intraday" else int(o["hold"])
    if o["hold"] not in HOLDS:
        raise ValueError("hold must be intraday, 1, 3 or 5")
    o["top"] = int(o["top"])
    if not 1 <= o["top"] <= 50:
        raise ValueError("top must be 1-50")
    if o["picks"] == "plans":
        o["hold"] = "intraday"
    if o["split"] not in ("equal", "edge"):
        o["split"] = "equal"
    o["basket"] = [str(s).strip().upper() for s in (o["basket"] or []) if str(s).strip()][:30]
    if o["picks"] == "basket" and not o["basket"]:
        raise ValueError("add at least one stock to the basket")
    o["reinvest"], o["costs"] = bool(o["reinvest"]), bool(o["costs"])
    return o


def _choose(D, day, o):
    """[(symbol, weight, side)] for the round projected on `day`; weights sum to 1."""
    if o["picks"] == "basket":
        return [(s, 1 / len(o["basket"]), 1) for s in o["basket"]]
    c = D.calls(day)
    c = c[c["pick"].astype(bool)].head(o["top"]) if "pick" in c else c.head(0)
    if o["picks"] == "up":
        c = c[c.lean == "up"]
    if o["picks"] == "plans":
        g = D.plans(day)
        syms = [s for s in c.symbol if len(g) and s in g.index]
        return [(s, 1 / len(syms), 1 if g.loc[s, "side"] == "long" else -1) for s in syms] if syms else []
    if not len(c):
        return []
    if o["split"] == "edge":
        w = (c.chance.astype(float) - c.usual.astype(float)).clip(lower=0.01).values
    else:
        w = np.ones(len(c))
    w = w / w.sum()
    return [(s, float(x), 1) for s, x in zip(c.symbol, w)]


def run(p=None, data=None):
    o = _norm(p)
    D = data or Data()
    days = [d for d in D.days if (not o["start"] or d >= o["start"]) and (not o["end"] or d <= o["end"])]
    if not days:
        return dict(ready=False, why="No saved projections in that period.", params=o)
    intraday = o["hold"] == "intraday"
    value = o["amount"]
    rounds, contrib, waiting = [], {}, None
    i = 0
    while i < len(days):
        day = days[i]
        t0 = pd.Timestamp(day)
        buy_t = D.after(t0, 1) if intraday else t0
        sell_t = buy_t if intraday else D.after(t0, o["hold"])
        if buy_t is None or sell_t is None:
            waiting = day
            break
        stake = value if o["reinvest"] else o["amount"]
        picks = _choose(D, day, o)
        g = D.plans(day) if o["picks"] == "plans" else None
        legs, pnl, paid, skipped = [], 0.0, 0.0, []
        for sym, w, side in picks:
            if g is not None:
                buy, sell = float(g.loc[sym, "entry"]), float(g.loc[sym, "exit_price"])
            else:
                buy = D.px(D.open if intraday else D.close, buy_t, sym)
                sell = D.px(D.close, sell_t, sym)
            if buy is None or sell is None:
                skipped.append(sym)
                continue
            v = stake * w
            r = side * (sell / buy - 1)
            cost = (_cost(True, v, not intraday) + _cost(False, v * (1 + r), not intraday)) if o["costs"] else 0.0
            gain = v * r - cost
            pnl += gain
            paid += cost
            legs.append(dict(symbol=sym, weight=round(w, 4), side="long" if side > 0 else "short", buy=round(buy, 2), sell=round(sell, 2),
                             ret=round(r, 5), pnl=round(gain, 2)))
            a = contrib.setdefault(sym, dict(symbol=sym, rounds=0, wins=0, pnl=0.0))
            a["rounds"] += 1
            a["wins"] += round(gain, 2) > 0
            a["pnl"] += gain
        n0, n1 = D.nifty_at(t0), D.nifty_at(sell_t)          # Nifty close to close over the same span
        value += pnl
        traded = bool(legs)
        rounds.append(dict(day=day, buy=str(buy_t.date()), sell=str(sell_t.date()), stocks=legs, skipped=skipped, invested=round(stake, 2) if traded else 0,
                           pnl=round(pnl, 2), costs=round(paid, 2), ret=round(pnl / stake, 5) if traded else None,
                           passed=(round(pnl, 2) > 0) if traded else None, value=round(value, 2),
                           nifty_ret=round(n1 / n0 - 1, 5) if n0 and n1 else None))
        nxt = str(sell_t.date())
        i = next((k for k in range(i + 1, len(days)) if days[k] >= nxt), len(days))
    if not rounds:
        return dict(ready=False, why=f"The first projection ({days[0]}) has no finished session after it yet.", params=o)
    return dict(ready=True, params=o, rounds=rounds, waiting=waiting, summary=_summary(D, o, rounds),
                stocks=sorted(({**a, "pnl": round(a["pnl"], 2)} for a in contrib.values()), key=lambda a: -a["pnl"]),
                curve=_curve(D, o, rounds))


def _curve(D, o, rounds):
    t0 = pd.Timestamp(rounds[0]["day"])
    n0 = D.nifty_at(t0)
    pts = [dict(date=rounds[0]["day"], value=o["amount"], nifty=o["amount"])]
    for r in rounds:
        n = D.nifty_at(pd.Timestamp(r["sell"]))
        pts.append(dict(date=r["sell"], value=r["value"], nifty=round(o["amount"] * n / n0, 2) if n0 and n else None))
    return pts


def _summary(D, o, rounds):
    amt, final = o["amount"], rounds[-1]["value"]
    traded = [r for r in rounds if r["ret"] is not None]
    vals = np.array([amt] + [r["value"] for r in rounds])
    peak = np.maximum.accumulate(vals)
    dd = (vals - peak) / peak
    k = int(dd.argmin())
    streak = best_streak = 0
    for r in traded:
        streak = streak + 1 if r["pnl"] <= 0 else 0
        best_streak = max(best_streak, streak)
    n0, n1 = D.nifty_at(pd.Timestamp(rounds[0]["day"])), D.nifty_at(pd.Timestamp(rounds[-1]["sell"]))
    nifty_ret = (n1 / n0 - 1) if n0 and n1 else None
    ret = final / amt - 1
    best = max(traded, key=lambda r: r["pnl"]) if traded else None
    worst = min(traded, key=lambda r: r["pnl"]) if traded else None
    passed = final > amt and (nifty_ret is None or ret > nifty_ret)
    verdict = "no trades" if not traded else "passed" if passed else "failed"
    why = "no stock qualified in this period" if not traded else  ("ended above the amount" if final > amt else "ended below the amount") + (
        "" if nifty_ret is None else (" and beat Nifty" if ret > nifty_ret else " but did not beat Nifty" if final > amt else ""))
    return dict(amount=amt, final=round(final, 2), pnl=round(final - amt, 2), ret=round(ret, 5), nifty_ret=None if nifty_ret is None else round(nifty_ret, 5),
                nifty_final=None if nifty_ret is None else round(amt * (1 + nifty_ret), 2),
                rounds=len(rounds), traded=len(traded), passed_rounds=sum(1 for r in traded if r["passed"]),
                costs=round(sum(r["costs"] for r in rounds), 2),
                max_dd=round(float(dd.min()), 5), max_dd_rs=round(float(vals[k] - peak[k]), 2),
                best=dict(day=best["day"], pnl=best["pnl"], ret=best["ret"]) if best else None,
                worst=dict(day=worst["day"], pnl=worst["pnl"], ret=worst["ret"]) if worst else None,
                losing_streak=best_streak, verdict=verdict, verdict_why=why,
                small_sample=len(traded) < MIN_ROUNDS, start=rounds[0]["day"], end=rounds[-1]["sell"])


def compare(p=None, grid=None):
    """Run a grid of setups on the same data; rows ranked by final value."""
    base = _norm(p)
    D = Data()
    grid = grid or [dict(top=n, hold=h, picks=f) for f in ("top", "up") for n in TOPS for h in HOLDS]
    rows = []
    for g in grid:
        q = {**base, **g}
        try:
            r = run(q, D)
        except ValueError:
            continue
        if not r.get("ready"):
            continue
        s = r["summary"]
        rows.append(dict(params={k: r["params"][k] for k in ("picks", "top", "hold", "split", "reinvest", "costs", "basket")},
                         final=s["final"], ret=s["ret"], nifty_ret=s["nifty_ret"], traded=s["traded"], passed_rounds=s["passed_rounds"],
                         max_dd=s["max_dd"], verdict=s["verdict"], small_sample=s["small_sample"]))
    rows.sort(key=lambda x: (x["traded"] == 0, -x["final"]))
    return dict(ready=bool(rows), rows=rows, amount=base["amount"], why=None if rows else "No setup could run in that period.")
