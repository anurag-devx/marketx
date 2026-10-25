"""Candlestick patterns. Uses TA-Lib when it's installed (optional: `pip install TA-Lib`), otherwise the built-in pandas
versions below, which cover the same 12 patterns. MarketX works either way; the source is reported with every result.

Built-in definitions (body = close - open, range = high - low, upper/lower = wicks; "after a fall/rise" = 5-day return):
  doji               |body| <= 10% of range
  hammer             lower wick >= 2x body, upper wick <= 30% of body (or 10% of range), after a fall      bullish
  hanging_man        hammer shape after a rise                                                             bearish
  inverted_hammer    upper wick >= 2x body, small lower wick, after a fall                                bullish
  shooting_star      inverted-hammer shape after a rise                                                   bearish
  engulfing          today's body covers yesterday's opposite-coloured body                               ±
  harami             today's body inside yesterday's larger opposite-coloured body                        ±
  morning_star       long red, a small body gapping below it, then a green close well into the red body   bullish
  evening_star       mirror of morning star                                                               bearish
  three_soldiers     three long green days, higher closes, each opening inside the prior body, closing at highs  bullish
  three_crows        mirror, after a rise                                                                 bearish
"Long" / "small" bodies are measured against the stock's average body of the previous 10 days, as TA-Lib does.
  inside_bar         today's high and low inside yesterday's range (no TA-Lib equivalent)                 neutral"""
import numpy as np, pandas as pd

PATTERNS = ["doji", "hammer", "hanging_man", "inverted_hammer", "shooting_star", "engulfing", "harami", "morning_star",
            "evening_star", "three_soldiers", "three_crows", "inside_bar"]
TALIB = {"doji": "CDLDOJI", "hammer": "CDLHAMMER", "hanging_man": "CDLHANGINGMAN", "inverted_hammer": "CDLINVERTEDHAMMER",
         "shooting_star": "CDLSHOOTINGSTAR", "engulfing": "CDLENGULFING", "harami": "CDLHARAMI", "morning_star": "CDLMORNINGSTAR",
         "evening_star": "CDLEVENINGSTAR", "three_soldiers": "CDL3WHITESOLDIERS", "three_crows": "CDL3BLACKCROWS"}
LABEL = {"doji": "Doji", "hammer": "Hammer", "hanging_man": "Hanging man", "inverted_hammer": "Inverted hammer", "shooting_star": "Shooting star",
         "engulfing": "Engulfing", "harami": "Harami", "morning_star": "Morning star", "evening_star": "Evening star",
         "three_soldiers": "Three white soldiers", "three_crows": "Three black crows", "inside_bar": "Inside bar"}

MEANING = {"doji": "open and close almost equal: buyers and sellers balanced → indecision; the next bar decides",
           "hammer": "long lower wick after a fall: sellers pushed it down, buyers pushed it back → possible bounce",
           "hanging_man": "hammer shape after a rise: selling showed up during the day → possible top",
           "inverted_hammer": "long upper wick after a fall: buyers tried to lift it → possible bounce if the next bar confirms",
           "shooting_star": "long upper wick after a rise: buyers pushed up, sellers pushed it back → possible top",
           "engulfing+": "today's green body swallowed yesterday's red one → buyers took over",
           "engulfing-": "today's red body swallowed yesterday's green one → sellers took over",
           "harami": "a small body inside yesterday's big one → the move is losing steam",
           "morning_star": "a fall, a small pause, then a strong up bar → possible bottom",
           "evening_star": "a rise, a small pause, then a strong down bar → possible top",
           "three_soldiers": "three strong up bars in a row → steady buying",
           "three_crows": "three strong down bars in a row → steady selling",
           "inside_bar": "the whole range inside yesterday's → compression; a break out of it often follows"}


def meaning(pattern, direction):
    if pattern == "engulfing":
        return MEANING["engulfing+" if direction == "bullish" else "engulfing-"]
    return MEANING.get(pattern, "")


def describe_candles(g, n=5):
    """The last n daily candles in words: colour, body vs range, wicks, change, gap, volume vs its 20-day average."""
    g = g.reset_index(drop=True)
    vol20 = g.volume.astype(float).rolling(20, min_periods=5).mean().shift(1)
    out = []
    for i in range(max(1, len(g) - n), len(g)):
        o, h, l, c = (float(g.loc[i, k]) for k in ("open", "high", "low", "close"))
        pc = float(g.loc[i - 1, "close"])
        rng = max(h - l, 1e-9)
        body, up_w, lo_w = abs(c - o) / rng, (h - max(o, c)) / rng, (min(o, c) - l) / rng
        kind = "doji-like (tiny body)" if body < 0.1 else "strong body" if body > 0.7 else "medium body"
        wick = ("long upper wick" if up_w > 0.4 else "") + (" and " if up_w > 0.4 and lo_w > 0.4 else "") + ("long lower wick" if lo_w > 0.4 else "")
        gap = 100 * (o / pc - 1)
        vx = float(g.loc[i, "volume"]) / vol20.iat[i] if pd.notna(vol20.iat[i]) and vol20.iat[i] > 0 else None
        out.append(dict(date=str(pd.Timestamp(g.loc[i, "date"]).date()), color="green" if c >= o else "red", change_pct=round(100 * (c / pc - 1), 2),
                        gap_pct=round(gap, 2), volume_x=round(vx, 1) if vx else None,
                        words=", ".join(x for x in [("green" if c >= o else "red") + f", {kind}", wick,
                                                     f"gapped {'up' if gap > 0 else 'down'} {abs(gap):.1f}%" if abs(gap) >= 0.5 else "",
                                                     f"volume {vx:.1f}× normal" if vx else ""] if x)))
    return out


def talib_status():
    try:
        import talib
        return dict(ok=True, version=getattr(talib, "__version__", "?"))
    except Exception as e:                        # not installed or broken: the built-in versions are used
        return dict(ok=False, why=type(e).__name__)


def builtin(g):
    """g: one stock's daily bars (open, high, low, close), date-sorted. Returns a frame of signals: +1 bullish, -1 bearish, 0 none."""
    o, h, l, c = (g[k].astype(float).to_numpy() for k in ("open", "high", "low", "close"))
    body, rng = c - o, np.maximum(h - l, 1e-12)
    ab, up, lo = np.abs(body), h - np.maximum(o, c), np.minimum(o, c) - l
    sh = lambda x, k=1: np.concatenate([np.full(k, np.nan), x[:-k]])
    trend = c / sh(c, 5) - 1
    avg = pd.Series(ab).shift(1).rolling(10, min_periods=5).mean().to_numpy()   # typical body size (TA-Lib style)
    long_ = ab > avg
    fell, rose = trend < -0.02, trend > 0.02
    small_up = (up <= np.maximum(0.3 * ab, 0.1 * rng))
    small_lo = (lo <= np.maximum(0.3 * ab, 0.1 * rng))
    ham = (lo >= 2 * ab) & small_up & (ab > 0) & (ab < avg)
    inv = (up >= 2 * ab) & small_lo & (ab > 0) & (ab < avg)
    po, pc, pab = sh(o), sh(c), sh(ab)
    eng = np.where((pc < po) & (c > o) & (o <= pc) & (c >= po) & (ab > pab), 1, np.where((pc > po) & (c < o) & (o >= pc) & (c <= po) & (ab > pab), -1, 0))
    har = np.where((pc < po) & (c > o) & (np.maximum(o, c) < po) & (np.minimum(o, c) > pc) & (pab > 2 * ab), 1,
                   np.where((pc > po) & (c < o) & (np.maximum(o, c) < pc) & (np.minimum(o, c) > po) & (pab > 2 * ab), -1, 0))
    o2, c2, ab2 = sh(o, 2), sh(c, 2), sh(ab, 2)
    rng2 = sh(rng, 2)
    avg2 = sh(avg, 2)
    star_small = (pab <= 0.3 * ab2) & (pab < sh(avg))
    gap_dn, gap_up = np.maximum(po, pc) < c2, np.minimum(po, pc) > c2
    ms = (c2 < o2) & (ab2 > avg2) & star_small & gap_dn & (c > o) & (c > c2 + 0.3 * ab2)
    es = (c2 > o2) & (ab2 > avg2) & star_small & gap_up & (c < o) & (c < c2 - 0.3 * ab2)
    g1 = (c > o) & (sh(c) > sh(o)) & (c2 > o2)
    near_hi = (up <= 0.1 * rng) & (sh(up) <= 0.1 * sh(rng)) & (sh(up, 2) <= 0.1 * rng2)
    longs3 = long_ & sh(long_.astype(float)).astype(bool) & sh(long_.astype(float), 2).astype(bool)
    sold = g1 & (c > pc) & (pc > c2) & (o > po) & (o < pc) & (po > o2) & (po < c2) & near_hi & longs3
    r1 = (c < o) & (sh(c) < sh(o)) & (c2 < o2)
    near_lo = (lo <= 0.1 * rng) & (sh(lo) <= 0.1 * sh(rng)) & (sh(lo, 2) <= 0.1 * rng2)
    crows = r1 & (c < pc) & (pc < c2) & (o < po) & (o > pc) & (po < o2) & (po > c2) & near_lo & longs3 & (sh(trend, 3) > 0)
    inside = (h < sh(h)) & (l > sh(l))
    out = pd.DataFrame({
        "hammer": np.where(ham & fell, 1, 0), "hanging_man": np.where(ham & rose, -1, 0),
        "inverted_hammer": np.where(inv & fell, 1, 0), "shooting_star": np.where(inv & rose, -1, 0),
        "engulfing": eng, "harami": har, "morning_star": np.where(ms, 1, 0), "evening_star": np.where(es, -1, 0),
        "three_soldiers": np.where(sold, 1, 0), "three_crows": np.where(crows, -1, 0)}, index=g.index)
    out["doji"] = np.where(ab <= 0.1 * rng, 2, 0)          # 2 = present, no direction
    out["inside_bar"] = np.where(inside, 2, 0)
    return out[PATTERNS]


def with_talib(g):
    import talib
    a = {k: g[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")}
    out = pd.DataFrame(index=g.index)
    for p, fn in TALIB.items():
        x = getattr(talib, fn)(a["open"], a["high"], a["low"], a["close"])
        out[p] = np.sign(x).astype(int)
    out["doji"] = np.where(out.doji != 0, 2, 0)
    out["inside_bar"] = builtin(g)["inside_bar"]
    return out[PATTERNS]


def detect(g, prefer_talib=True):
    """Signals for one stock. Returns (frame, source)."""
    if prefer_talib and talib_status()["ok"]:
        try:
            return with_talib(g), "TA-Lib"
        except Exception:
            pass
    return builtin(g), "built-in"


def recent(g, days=5, prefer_talib=True):
    """Patterns in the last `days` bars: [{date, pattern, label, direction}]."""
    sig, src = detect(g.tail(days + 10), prefer_talib)
    sig = sig.tail(days)
    out = []
    for idx, row in sig.iterrows():
        for p, v in row.items():
            if v:
                dr = "bullish" if v == 1 else "bearish" if v == -1 else "neutral"
                out.append(dict(date=str(pd.Timestamp(g.loc[idx, "date"]).date()), pattern=p, label=LABEL[p], direction=dr, meaning=meaning(p, dr)))
    return out, src
