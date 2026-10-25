"""TA evidence: every check behind the chart read, with its value, rule and verdict, so the label can be audited.
Daily bars up to the plan's day only. Labels, not signals: untested until Market Lab → Review grades them.

Groups and checks (verdict +1 up, -1 down, 0 neutral / information):
  Trend      E1 close vs 20-day average · E2 20-day vs 50-day · E3 close vs 200-day · E4 higher highs and lows (last 10 vs
             the 10 before) · E5 20-day average rising or falling (vs 5 sessions ago)
  Momentum   E6 RSI(14): > 60 up, < 40 down (> 70 / < 30 noted as stretched) · E7 MACD(12,26,9) histogram above / below 0
  Volume     E8 today's volume vs its 20-day average on an up / down day (>= 1.5x counts) · E9 up-day vs down-day volume, 10 days
  Volatility E10 ATR(14) now vs 20 sessions ago (information only: bigger or smaller daily ranges)
  Levels     E11 close vs pivot · E12 break of the previous 20-day high / low, or distance to them in ATR
  Candles    E13+ each pattern of the last 2 sessions: ±1 only when medium / strong in context and not 'no tested edge'
Read: net = sum of verdicts; >= +3 up, <= -3 down, else neutral."""
import numpy as np, pandas as pd

GROUPS = ("Trend", "Momentum", "Volume", "Volatility", "Levels", "Candles")
NET_MIN = 3


def rsi(c, n=14):
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def macd(c):
    m = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    s = m.ewm(span=9, adjust=False).mean()
    return m, s, m - s


def atr(g, n=14):
    h, l, c = g.high.astype(float), g.low.astype(float), g.close.astype(float)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def _r(x, d=2):
    return None if x is None or not np.isfinite(x) else round(float(x), d)


def evidence(g, patterns=(), lv=None):
    """g: daily bars (date, open, high, low, close, volume), date-sorted, up to the plan's day. Returns dict."""
    g = g.reset_index(drop=True)
    c, v = g.close.astype(float), g.volume.astype(float)
    n = len(g)
    s20, s50, s200 = c.rolling(20).mean(), c.rolling(50).mean(), c.rolling(200).mean()
    rs, (m, sig, hist), at = rsi(c), macd(c), atr(g)
    last, prev = c.iat[-1], c.iat[-2] if n > 1 else np.nan
    out = []

    def add(eid, grp, metric, value, rule, verdict, reason):
        out.append(dict(id=eid, group=grp, metric=metric, value=value, rule=rule, verdict=int(verdict), reason=reason))

    sgn = lambda x: 1 if x > 0 else -1 if x < 0 else 0
    if np.isfinite(s20.iat[-1]):
        d = 100 * (last / s20.iat[-1] - 1)
        add("E1", "Trend", "Close vs 20-day average", f"{d:+.1f}%", "above = up, below = down", sgn(d), f"close {'above' if d > 0 else 'below'} its 20-day average ({_r(s20.iat[-1])})")
    if np.isfinite(s50.iat[-1]):
        d = 100 * (s20.iat[-1] / s50.iat[-1] - 1)
        add("E2", "Trend", "20-day vs 50-day average", f"{d:+.1f}%", "20 above 50 = up", sgn(d), f"short-term average {'above' if d > 0 else 'below'} the 50-day ({_r(s50.iat[-1])})")
    if np.isfinite(s200.iat[-1]):
        d = 100 * (last / s200.iat[-1] - 1)
        add("E3", "Trend", "Close vs 200-day average", f"{d:+.1f}%", "above = long-term uptrend", sgn(d), f"{'above' if d > 0 else 'below'} the 200-day average ({_r(s200.iat[-1])}): long-term {'up' if d > 0 else 'down'}trend")
    else:
        add("E3", "Trend", "Close vs 200-day average", "—", "needs 200 sessions", 0, "not enough history for the 200-day average")
    if n >= 20:
        a, b = g.iloc[-10:], g.iloc[-20:-10]
        hh, hl = a.high.max() > b.high.max(), a.low.min() > b.low.min()
        lh, ll = a.high.max() < b.high.max(), a.low.min() < b.low.min()
        vd = 1 if hh and hl else -1 if lh and ll else 0
        add("E4", "Trend", "Highs and lows (last 10 vs previous 10 sessions)", "higher high, higher low" if vd > 0 else "lower high, lower low" if vd < 0 else "mixed",
            "both higher = up, both lower = down", vd, "a staircase up" if vd > 0 else "a staircase down" if vd < 0 else "no clear staircase: range or turning")
    if n >= 25 and np.isfinite(s20.iat[-6]):
        d = 100 * (s20.iat[-1] / s20.iat[-6] - 1)
        add("E5", "Trend", "20-day average slope (5 sessions)", f"{d:+.2f}%", "rising = up", sgn(round(d, 2)), f"the 20-day average is {'rising' if d > 0 else 'falling' if d < 0 else 'flat'}")
    if np.isfinite(rs.iat[-1]):
        r = rs.iat[-1]
        vd = 1 if r > 60 else -1 if r < 40 else 0
        note = " (stretched: overbought)" if r > 70 else " (stretched: oversold)" if r < 30 else ""
        add("E6", "Momentum", "RSI(14)", f"{r:.0f}", "> 60 up, < 40 down", vd, f"momentum {'strong' if vd > 0 else 'weak' if vd < 0 else 'neutral'}{note}")
    if n >= 35:
        h0, h1 = hist.iat[-1], hist.iat[-2]
        cross = " (crossed today)" if sgn(h0) != sgn(h1) else ""
        add("E7", "Momentum", "MACD histogram (12,26,9)", f"{h0:+.2f}", "above 0 = up", sgn(h0), f"MACD {'above' if h0 > 0 else 'below'} its signal line{cross}")
    if n >= 21:
        avg = v.iloc[-21:-1].mean()
        x = v.iat[-1] / avg if avg > 0 else np.nan
        up = last >= prev
        vd = (1 if up else -1) if np.isfinite(x) and x >= 1.5 else 0
        add("E8", "Volume", "Today's volume vs 20-day average", f"{x:.1f}×" if np.isfinite(x) else "—", ">= 1.5x on an up day = up, on a down day = down", vd,
            f"{'heavy' if vd else 'normal'} volume on a{'n up' if up else ' down'} day" + (": conviction" if vd else ""))
    if n >= 11:
        ch = c.diff().iloc[-10:]
        vv = v.iloc[-10:]
        upv, dnv = vv[ch > 0].sum(), vv[ch < 0].sum()
        ratio = upv / dnv if dnv > 0 else np.inf
        vd = 1 if ratio > 1.3 else -1 if ratio < 0.77 else 0
        add("E9", "Volume", "Up-day vs down-day volume (10 sessions)", "all up" if not np.isfinite(ratio) else f"{ratio:.2f}", "> 1.3 up, < 0.77 down", vd,
            "buying volume outweighs selling" if vd > 0 else "selling volume outweighs buying" if vd < 0 else "buying and selling volume balanced")
    if n >= 35 and np.isfinite(at.iat[-21]):
        d = 100 * (at.iat[-1] / at.iat[-21] - 1)
        add("E10", "Volatility", "ATR(14) vs 20 sessions ago", f"{d:+.0f}%", "information only", 0, f"daily ranges {'expanding' if d > 10 else 'shrinking' if d < -10 else 'steady'} (ATR {_r(at.iat[-1])})")
    if n >= 2:
        H, L, Cp = g.high.iat[-1], g.low.iat[-1], last
        P = (H + L + Cp) / 3 if lv is None else lv["P"]
        add("E11", "Levels", "Close vs pivot", f"{100 * (last / P - 1):+.1f}%", "above = up", sgn(last - P), f"closed {'above' if last > P else 'below'} the pivot ({_r(P)})")
    if n >= 22:
        phi, plo, a1 = g.high.iloc[-21:-1].max(), g.low.iloc[-21:-1].min(), at.iat[-1] if np.isfinite(at.iat[-1]) else np.nan
        if last > phi:
            add("E12", "Levels", "20-day high / low", f"broke above {_r(phi)}", "close beyond = breakout", 1, "closed above the previous 20-day high: breakout")
        elif last < plo:
            add("E12", "Levels", "20-day high / low", f"broke below {_r(plo)}", "close beyond = breakdown", -1, "closed below the previous 20-day low: breakdown")
        else:
            add("E12", "Levels", "20-day high / low", f"{(phi - last) / a1:.1f} ATR below the high, {(last - plo) / a1:.1f} ATR above the low" if np.isfinite(a1) else "inside the range",
                "inside = neutral", 0, f"inside the 20-day range ({_r(plo)}–{_r(phi)})")
    recent = sorted({p["date"] for p in patterns})[-2:]
    for i, p in enumerate([p for p in patterns if p["date"] in recent], 13):
        vd = 1 if p.get("direction") == "bullish" else -1 if p.get("direction") == "bearish" else 0
        st, t = p.get("strength"), p.get("tested")
        why = p.get("meaning") or p.get("direction", "")
        if st:
            why += f" · {st}"
        if st == "weak":
            vd = 0                                                    # out of context, no volume, no confirmation: not counted
        if t:
            why += f" · history: {100 * t['win5']:.0f}% went its way in 5 sessions vs {100 * t['base5']:.0f}% normally (n={t['n']:,}, {t['verdict']})"
            if t["verdict"] in ("against", "no tested edge"):
                vd = 0                                                # no tested edge on 10 years: not counted
        add(f"E{i}", "Candles", p.get("label", p.get("pattern")), p["date"], "medium / strong pattern with a tested edge counts ±1", vd, why)
    net = sum(x["verdict"] for x in out)
    by = {gname: dict(up=sum(x["verdict"] > 0 for x in out if x["group"] == gname), down=sum(x["verdict"] < 0 for x in out if x["group"] == gname),
                      n=sum(x["group"] == gname for x in out)) for gname in GROUPS}
    k = 120
    tail = lambda s: [None if not np.isfinite(x) else round(float(x), 2) for x in s.iloc[-k:]]
    series = dict(t=pd.to_datetime(g.date.iloc[-k:]).dt.strftime("%Y-%m-%d").tolist(), sma20=tail(s20), sma50=tail(s50), sma200=tail(s200), rsi=tail(rs),
                  macd_hist=tail(hist))
    return dict(checks=out, net=int(net), up=sum(x["verdict"] > 0 for x in out), down=sum(x["verdict"] < 0 for x in out), by_group=by,
                dir="up" if net >= NET_MIN else "down" if net <= -NET_MIN else "neutral", series=series)
