"""API: plain-English names and the strategy guide. Read-only: explains what the other pages already show."""
import json
import pandas as pd
from core import config as C
from web.api import route, q, UserError

FAMILY = {
    "breakout": dict(label="Breakouts", why="A stock pushing to a new high on heavy buying shows fresh demand, which often carries on for a few days.",
                     fails="False breakouts: in choppy or falling markets the price slips back under the old high, and the 0.3% trading cost eats small wins."),
    "leader": dict(label="Strength leaders", why="Stocks that are already beating the market tend to keep doing so for a while, because big buyers build positions slowly.",
                   fails="Sharp market reversals hit the strongest, most crowded stocks first."),
    "dip": dict(label="Buying short dips", why="A sudden 2–4 day drop in a healthy stock is often an over-reaction that bounces back within a week.",
                fails="Dips caused by real bad news keep falling, and in a downtrend most dips are not over-reactions."),
    "momentum": dict(label="Monthly momentum", why="Over months, past winners keep beating past losers. This is one of the best-documented effects in markets.",
                     fails="Momentum crashes: when the market turns suddenly, last year's winners fall hardest. Expect deep drops along the way."),
}

GUIDE = {
    "S1": dict(family="breakout", how="The stock is above its 200-day average, closes above its highest price of the last 55 days, and trades at least twice its normal volume."),
    "S2": dict(family="leader", how="The stock is in the strongest 10% of the market, closes above its best close of the last 20 days, and is above its 50-day average."),
    "S3": dict(family="breakout", how="After a quiet spell where the price barely moved (a 'squeeze'), it closes above the top of that range on 1.5× normal volume, above its 50-day average."),
    "S4": dict(family="breakout", how="The stock closes at a new 1-year high, with more shares than usual taken for delivery (real buying, not just day trading)."),
    "S5": dict(family="dip", how="A stock in a long uptrend (above its 200-day average, 50-day above 200-day) drops sharply for 2–3 days (3-day RSI under 15)."),
    "S6": dict(family="dip", how="A stock in an uptrend, in a strong sector, dips to its 20-day average during the day, then closes back above it and up on the day."),
    "S10": dict(family="leader", how="Pick the 3 strongest sectors, then the 2 strongest stocks in each, as long as they're above their 50-day average."),
    "S11": dict(family="dip", how="A very liquid stock (₹25 crore+ traded a day) is extremely oversold (3-day RSI under 10) but closes in the top half of the day's range: buyers stepped in."),
    "S12": dict(family="dip", how="A strong, calm stock (top 20% for strength, bottom 40% for volatility, above its 200-day average) has a short dip (3-day RSI under 20)."),
}

EXIT_WORDS = {"revert_sma5": "it closes back above its 5-day average", "trail_sma10": "it closes below its 10-day average",
              "flag_off:sector_top5": "its sector drops out of the top 5"}


def _exit_text(x):
    stop = f"{x['stop'][1]:g}× the daily range (ATR) below entry" if x["stop"][0] == "atr" else \
        {"box_low": "the bottom of the quiet range", "pb_low": "the low of the dip"}.get(x["stop"][1], x["stop"][1])
    parts = [f"Sell if the price falls to {stop}"]
    if x["target"] is not None:
        parts.append(f"take profit at {x['target'][1]:g}× ATR above entry" if x["target"][0] == "atr" else f"take profit at {x['target'][2]:g}× the range height above entry")
    parts += [f"sell when {EXIT_WORDS.get(e, e)}" for e in x["exits"]]
    parts.append(f"otherwise sell after {x['max_days']} trading days")
    return "; ".join(parts) + "."


def momentum_name(vid):
    """MOM_L6_S1_H3[_F] -> plain words. Mirrors research/momentum.py."""
    try:
        p = vid.split("_")
        L, S, H = int(p[1][1:]), int(p[2][1:]), int(p[3][1:])
    except (IndexError, ValueError):
        return vid
    t = f"Momentum: buy the 20 stocks with the best {L}-month return"
    t += f" (ignoring the latest {S} month{'s' if S > 1 else ''})" if S else ""
    t += f", hold each batch {H} month{'s' if H > 1 else ''}"
    return t + (", cash while the Nifty is below its 200-day average" if vid.endswith("_F") else "")


@route("GET", "meta/names")
def names(_):
    from core.strategies import STRATEGIES
    out = {k: v["name"] for k, v in STRATEGIES.items()}
    reg = q("select strategy from strategies")
    for s in (reg.strategy.tolist() if len(reg) else []):
        if s.startswith("MOM_"):
            out[s] = momentum_name(s)
    out["manual"] = "Your own pick (no strategy)"
    return out


@route("GET", "strategies/guide")
def guide(_):
    from core.strategies import STRATEGIES
    a = C.RESULTS / "analysis"
    s = pd.read_csv(a / "strategies.csv") if (a / "strategies.csv").exists() else pd.DataFrame()
    cards = q("select strategy, state, active from strategy_cards")
    reg = q("select strategy, family, status from strategies")
    st = dict(zip(reg.strategy, reg.status)) if len(reg) else {}
    lab = {"up_calm": "uptrend, calm", "up_volatile": "uptrend, volatile", "down_calm": "downtrend, calm", "down_volatile": "downtrend, volatile"}
    items = []
    for sid, x in STRATEGIES.items():
        g = GUIDE.get(sid, {})
        f = FAMILY.get(g.get("family"), {})
        r = s[s.strategy == sid] if len(s) else s
        on = cards[(cards.strategy == sid) & cards.active].state.map(lab).tolist() if len(cards) else []
        items.append(dict(id=sid, name=x["name"], family=f.get("label", ""), what=f"A swing trade lasting up to {x['max_days']} days.",
                          how=g.get("how", ""), exit=_exit_text(x), why=f.get("why", ""), fails=f.get("fails", ""),
                          status=st.get(sid, "not registered"), works_in=on,
                          net_pct=float(r.net_pct.iat[0]) if len(r) else None, trades=int(r.trades.iat[0]) if len(r) else None,
                          win_pct=float(r.win_pct.iat[0]) if len(r) and "win_pct" in r else None))
    jp = C.RESULTS / "strategy" / "momentum_verdict.json"
    if jp.exists():
        v = json.loads(jp.read_text())
        f = FAMILY["momentum"]
        items.append(dict(id=v["chosen"], name="Monthly momentum", family=f["label"], what="A portfolio of 20 stocks, rebalanced once a month.",
                          how=momentum_name(v["chosen"]) + ". Only liquid stocks count.", exit="A stock is sold at a rebalance when it drops out of the top 20.",
                          why=f["why"], fails=f["fails"], status=st.get(v["chosen"], "research"), works_in=[],
                          verdict=("Passed" if v["passes"] else "Did not pass") + f" its tests (best of {v['variants_tested']} variants)."))
    return dict(items=items, cost_pct=C.COST_RT * 100)


@route("GET", "meta/strategy")
def one(p):
    g = guide(p)
    for it in g["items"]:
        if it["id"] == p.get("id"):
            return it
    raise UserError("unknown strategy")
