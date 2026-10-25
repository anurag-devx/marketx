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
            "evening_star", "three_soldiers", "three_crows", "inside_bar",
            # added (v2, the full common set; definitions: docs/patterns.md)
            "piercing", "dark_cloud", "dragonfly_doji", "gravestone_doji", "tweezer_bottom", "tweezer_top", "belt_hold", "kicker",
            "three_inside", "three_outside", "abandoned_baby", "doji_star", "meeting_lines", "upside_gap_two_crows", "ladder_bottom",
            "concealing_baby_swallow", "rise_fall_three", "mat_hold", "separating_lines", "three_line_strike"]
TALIB = {"doji": "CDLDOJI", "hammer": "CDLHAMMER", "hanging_man": "CDLHANGINGMAN", "inverted_hammer": "CDLINVERTEDHAMMER",
         "shooting_star": "CDLSHOOTINGSTAR", "engulfing": "CDLENGULFING", "harami": "CDLHARAMI", "morning_star": "CDLMORNINGSTAR",
         "evening_star": "CDLEVENINGSTAR", "three_soldiers": "CDL3WHITESOLDIERS", "three_crows": "CDL3BLACKCROWS",
         "piercing": "CDLPIERCING", "dark_cloud": "CDLDARKCLOUDCOVER", "dragonfly_doji": "CDLDRAGONFLYDOJI", "gravestone_doji": "CDLGRAVESTONEDOJI",
         "belt_hold": "CDLBELTHOLD", "kicker": "CDLKICKING", "three_inside": "CDL3INSIDE", "three_outside": "CDL3OUTSIDE",
         "abandoned_baby": "CDLABANDONEDBABY", "doji_star": "CDLDOJISTAR", "meeting_lines": "CDLCOUNTERATTACK",
         "upside_gap_two_crows": "CDLUPSIDEGAP2CROWS", "ladder_bottom": "CDLLADDERBOTTOM", "concealing_baby_swallow": "CDLCONCEALBABYSWALL",
         "rise_fall_three": "CDLRISEFALL3METHODS", "mat_hold": "CDLMATHOLD", "separating_lines": "CDLSEPARATINGLINES", "three_line_strike": "CDL3LINESTRIKE"}
BUILTIN_ONLY = ("inside_bar", "tweezer_bottom", "tweezer_top")                      # no TA-Lib equivalent: always built-in
TALIB_ONLY = ("abandoned_baby", "doji_star", "meeting_lines", "upside_gap_two_crows", "ladder_bottom", "concealing_baby_swallow",
              "rise_fall_three", "mat_hold", "separating_lines", "three_line_strike")   # rare, multi-candle: only when TA-Lib is installed
LABEL = {"doji": "Doji", "hammer": "Hammer", "hanging_man": "Hanging man", "inverted_hammer": "Inverted hammer", "shooting_star": "Shooting star",
         "engulfing": "Engulfing", "harami": "Harami", "morning_star": "Morning star", "evening_star": "Evening star",
         "three_soldiers": "Three white soldiers", "three_crows": "Three black crows", "inside_bar": "Inside bar",
         "piercing": "Piercing line", "dark_cloud": "Dark cloud cover", "dragonfly_doji": "Dragonfly doji", "gravestone_doji": "Gravestone doji",
         "tweezer_bottom": "Tweezer bottom", "tweezer_top": "Tweezer top", "belt_hold": "Belt hold", "kicker": "Kicker",
         "three_inside": "Three inside up/down", "three_outside": "Three outside up/down", "abandoned_baby": "Abandoned baby",
         "doji_star": "Doji star", "meeting_lines": "Meeting lines", "upside_gap_two_crows": "Upside gap two crows", "ladder_bottom": "Ladder bottom",
         "concealing_baby_swallow": "Concealing baby swallow", "rise_fall_three": "Rising / falling three methods", "mat_hold": "Mat hold",
         "separating_lines": "Separating lines", "three_line_strike": "Three-line strike"}
KIND = {p: "reversal" for p in PATTERNS}                    # what the pattern claims: reversal of the prior trend, continuation, or indecision
KIND.update({"doji": "indecision", "inside_bar": "indecision", "three_soldiers": "continuation", "three_crows": "continuation",
             "rise_fall_three": "continuation", "mat_hold": "continuation", "separating_lines": "continuation", "three_line_strike": "continuation"})

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
           "inside_bar": "the whole range inside yesterday's → compression; a break out of it often follows",
           "piercing": "after a fall, a green candle that opens below yesterday's close and closes above the middle of its red body → buyers pushing back",
           "dark_cloud": "after a rise, a red candle that opens above yesterday's high and closes below the middle of its green body → sellers pushing back",
           "dragonfly_doji": "open, high and close together with a long lower wick → sellers were rejected; bullish after a fall",
           "gravestone_doji": "open, low and close together with a long upper wick → buyers were rejected; bearish after a rise",
           "tweezer_bottom": "two candles with the same low after a fall → a floor held twice",
           "tweezer_top": "two candles with the same high after a rise → a ceiling held twice",
           "belt_hold+": "opens at its low and closes near its high → buyers in control all day",
           "belt_hold-": "opens at its high and closes near its low → sellers in control all day",
           "kicker+": "a red candle, then a gap up and a strong green candle → a sharp change of sentiment upward",
           "kicker-": "a green candle, then a gap down and a strong red candle → a sharp change of sentiment downward",
           "three_inside+": "a harami followed by a green close above the first candle → the reversal up is confirmed",
           "three_inside-": "a harami followed by a red close below the first candle → the reversal down is confirmed",
           "three_outside+": "a bullish engulfing followed by a higher close → the reversal up is confirmed",
           "three_outside-": "a bearish engulfing followed by a lower close → the reversal down is confirmed",
           "abandoned_baby": "a doji gapped away from both neighbours → an isolated turning point (rare)",
           "doji_star": "a doji gapping away after a long candle → the move is stalling",
           "meeting_lines": "two opposite candles closing at the same price → the move met resistance",
           "upside_gap_two_crows": "after a rise, two red candles above a gap, the second engulfing the first → buyers fading",
           "ladder_bottom": "four falling red candles, then a strong green candle → selling exhausted (rare)",
           "concealing_baby_swallow": "four red candles where the last swallows the third → selling exhausting (rare)",
           "rise_fall_three": "a long candle, three small ones against it inside its range, then another long candle → the trend resumes",
           "mat_hold": "like rising three methods, but the pause holds higher → the uptrend resumes",
           "separating_lines": "two opposite candles opening at the same price → the trend resumes",
           "three_line_strike": "three candles one way, then one long candle wiping them out → often a continuation (Bulkowski)"}


def meaning(pattern, direction):
    k = f"{pattern}{'+' if direction == 'bullish' else '-'}"
    if k in MEANING:
        return MEANING[k]
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
    # v2 additions (definitions in docs/patterns.md)
    mid = (po + pc) / 2
    ph, pl = sh(h), sh(l)
    out["piercing"] = np.where(fell & (pc < po) & sh(long_.astype(float)).astype(bool) & (c > o) & (o < pl) & (c > mid) & (c < po), 1, 0)
    out["dark_cloud"] = np.where(rose & (pc > po) & sh(long_.astype(float)).astype(bool) & (c < o) & (o > ph) & (c < mid) & (c > po), -1, 0)
    doji = ab <= 0.1 * rng
    out["dragonfly_doji"] = np.where(doji & (up <= 0.1 * rng) & (lo >= 0.6 * rng) & fell, 1, 0)
    out["gravestone_doji"] = np.where(doji & (lo <= 0.1 * rng) & (up >= 0.6 * rng) & rose, -1, 0)
    atr = pd.Series(np.maximum(h, sh(c)) - np.minimum(l, sh(c))).rolling(14, min_periods=5).mean().to_numpy()
    out["tweezer_bottom"] = np.where(fell & (np.abs(l - pl) <= 0.05 * atr) & (pc < po) & (c > o), 1, 0)
    out["tweezer_top"] = np.where(rose & (np.abs(h - ph) <= 0.05 * atr) & (pc > po) & (c < o), -1, 0)
    out["belt_hold"] = np.where(long_ & (c > o) & (lo <= 0.05 * rng) & (up <= 0.25 * ab) & fell, 1,
                                np.where(long_ & (c < o) & (up <= 0.05 * rng) & (lo <= 0.25 * ab) & rose, -1, 0))
    plong = sh(long_.astype(float)).astype(bool)
    out["kicker"] = np.where(plong & long_ & (pc < po) & (c > o) & (o > po) & (l > po), 1,
                             np.where(plong & long_ & (pc > po) & (c < o) & (o < po) & (h < po), -1, 0))
    out["three_inside"] = np.where((sh(har) == 1) & (c > o) & (c > o2), 1, np.where((sh(har) == -1) & (c < o) & (c < o2), -1, 0))
    out["three_outside"] = np.where((sh(eng) == 1) & (c > pc), 1, np.where((sh(eng) == -1) & (c < pc), -1, 0))
    for k in TALIB_ONLY:
        out[k] = 0                                          # needs TA-Lib (rare multi-candle patterns)
    return out[PATTERNS].fillna(0).astype(int)


def with_talib(g):
    import talib
    a = {k: g[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")}
    out = pd.DataFrame(index=g.index)
    for p, fn in TALIB.items():
        x = getattr(talib, fn)(a["open"], a["high"], a["low"], a["close"])
        out[p] = np.sign(x).astype(int)
    out["doji"] = np.where(out.doji != 0, 2, 0)
    c = a["close"]
    trend = c / np.concatenate([np.full(5, np.nan), c[:-5]]) - 1
    out["dragonfly_doji"] = np.where((out.dragonfly_doji != 0) & (trend < -0.02), 1, 0)     # TA-Lib gives no direction: use the trend
    out["gravestone_doji"] = np.where((out.gravestone_doji != 0) & (trend > 0.02), -1, 0)
    b = builtin(g)
    for p in BUILTIN_ONLY:
        out[p] = b[p]
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
    """Patterns in the last `days` bars: [{date, pattern, label, kind, direction, meaning, strength, checks, tested}]."""
    gg = g.tail(days + 40)
    sig, src = detect(gg, prefer_talib)
    ctx = context(gg)
    sig = sig.tail(days)
    stats = load_stats()
    out = []
    for idx, row in sig.iterrows():
        for p, v in row.items():
            if v:
                dr = "bullish" if v == 1 else "bearish" if v == -1 else "neutral"
                gr = grade(p, int(v), ctx.loc[idx])
                out.append(dict(date=str(pd.Timestamp(g.loc[idx, "date"]).date()), pattern=p, label=LABEL[p], kind=gr["kind"], direction=dr,
                                meaning=meaning(p, dr), strength=gr["strength"], checks={k: gr[k] for k in ("trend", "location", "volume", "confirmed")},
                                tested=tested(stats, p, dr, gr)))
    return out, src


def load_stats():
    import json
    from core import config as C
    try:
        return json.loads((C.BASE / "results" / "patterns" / "stats.json").read_text())
    except (OSError, ValueError):
        return None


def tested(stats, pattern, direction, gr):
    """The history test's row for this pattern, direction and the strictest bucket it qualifies for (strong > in context > all)."""
    if not stats:
        return None
    rows = {(r["pattern"], r["direction"], r["bucket"]): r for r in stats.get("rows", [])}
    for b in (["strong"] if gr.get("strength") == "strong" else []) + (["context"] if gr.get("trend") else []) + ["all"]:
        r = rows.get((pattern, direction, b))
        if r and r.get("n", 0) >= 30:
            return r
    return None


# ---------------- context: is the pattern where it should be? (reading rules, docs/patterns.md) ----------------
LOC_ATR, VOL_X = 0.5, 1.3


def context(g):
    """Per bar: fell / rose (prior trend), near_low / near_high (within 0.5 ATR of the previous 20-day low / high or of S1 / R1),
    vol_ok (>= 1.3x its 20-day average volume), next_close (to judge confirmation). g: one stock, date-sorted."""
    o, h, l, c = (g[k].astype(float).to_numpy() for k in ("open", "high", "low", "close"))
    v = g["volume"].astype(float).to_numpy() if "volume" in g else np.full(len(c), np.nan)
    s = pd.Series(c)
    trend = c / s.shift(5).to_numpy() - 1
    below10 = c < s.rolling(10).mean().to_numpy()
    pc = s.shift().to_numpy()
    tr = np.maximum(h, pc) - np.minimum(l, pc)
    atr = pd.Series(tr).rolling(14, min_periods=5).mean().to_numpy()
    lo20, hi20 = pd.Series(l).shift().rolling(20, min_periods=10).min().to_numpy(), pd.Series(h).shift().rolling(20, min_periods=10).max().to_numpy()
    ph, pl = pd.Series(h).shift().to_numpy(), pd.Series(l).shift().to_numpy()
    P = (ph + pl + pc) / 3
    S1, R1 = 2 * P - ph, 2 * P - pl
    near_low = (np.abs(l - lo20) <= LOC_ATR * atr) | (np.abs(l - S1) <= LOC_ATR * atr)
    near_high = (np.abs(h - hi20) <= LOC_ATR * atr) | (np.abs(h - R1) <= LOC_ATR * atr)
    vavg = pd.Series(v).shift().rolling(20, min_periods=10).mean().to_numpy()
    return pd.DataFrame({"fell": (trend < -0.02) | (below10 & (trend < 0)), "rose": (trend > 0.02) | (~below10 & (trend > 0)),
                         "near_low": near_low, "near_high": near_high, "vol_ok": v >= VOL_X * vavg,
                         "close": c, "next_close": s.shift(-1).to_numpy()}, index=g.index)


def grade(pattern, sign, ctx_row):
    """Checks for one detected pattern (sign +1 bullish, -1 bearish, 2 none). Returns dict with strength weak / medium / strong."""
    kind = KIND.get(pattern, "reversal")
    if sign not in (1, -1):
        return dict(kind=kind, trend=None, location=None, volume=bool(ctx_row["vol_ok"]), confirmed=None, score=None, strength="neutral")
    bull = sign == 1
    trend = bool(ctx_row["fell"] if (bull == (kind == "reversal")) else ctx_row["rose"])
    loc = bool(ctx_row["near_low"] if bull else ctx_row["near_high"]) if kind == "reversal" else None
    nc = ctx_row["next_close"]
    conf = None if nc is None or not np.isfinite(nc) else bool((nc > ctx_row["close"]) if bull else (nc < ctx_row["close"]))
    score = int(trend) + int(bool(loc)) + int(bool(ctx_row["vol_ok"])) + int(bool(conf))
    return dict(kind=kind, trend=trend, location=loc, volume=bool(ctx_row["vol_ok"]), confirmed=conf, score=score,
                strength="strong" if score >= 3 else "medium" if score == 2 else "weak")
