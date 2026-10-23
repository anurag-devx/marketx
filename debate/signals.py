"""Pre-registered technical and market signals: the only technical claims an agent can make.

Each signal is fixed BEFORE testing (conventional thresholds, never tuned on results), names the direction it claims
('bull' = higher forward returns than comparable stocks on the same day, 'bear' = lower), and belongs to a correlation
GROUP. Signals in the same group largely measure the same thing (RSI, MACD and moving averages all reflect the recent
trend), so the judge counts a group once, never each signal in it.

The statistical agent tests every signal, whether or not an agent cites it."""
import numpy as np, pandas as pd

GROUPS = {"trend": "Trend (moving averages, ADX, MACD)", "breakout": "Breakouts / breakdowns", "mean_reversion": "Overbought / oversold",
          "relative_strength": "Relative strength vs market and sector", "volume": "Volume confirmation", "gap": "Gaps",
          "volatility": "Volatility", "market": "Market backdrop (applies to every stock)"}


def _cross_up(a, b, sym, days=3):
    above = (a > b)
    prev = above.groupby(sym).shift(1).astype("boolean").fillna(False).astype(bool)
    x = (above & ~prev).astype(float)
    return x.groupby(sym).transform(lambda s: s.rolling(days, min_periods=1).max()) > 0


def _cross_dn(a, b, sym, days=3):
    return _cross_up(b, a, sym, days)


SIGNALS = [
    # id, side, group, name, definition, function(panel) -> bool
    ("TREND_UP", "bull", "trend", "Uptrend alignment", "close > 50-day SMA > 200-day SMA",
     lambda p: (p.close > p.sma50) & (p.sma50 > p.sma200)),
    ("TREND_DOWN", "bear", "trend", "Downtrend alignment", "close < 50-day SMA < 200-day SMA",
     lambda p: (p.close < p.sma50) & (p.sma50 < p.sma200)),
    ("ADX_UP", "bull", "trend", "Strong directional uptrend", "ADX(14) > 25 and +DI > -DI",
     lambda p: (p.adx14 > 25) & (p.pdi > p.mdi)),
    ("ADX_DOWN", "bear", "trend", "Strong directional downtrend", "ADX(14) > 25 and -DI > +DI",
     lambda p: (p.adx14 > 25) & (p.mdi > p.pdi)),
    ("MACD_BULL_X", "bull", "trend", "MACD bullish crossover", "MACD(12,26) crossed above its 9-day signal in the last 3 sessions",
     lambda p: _cross_up(p.macd, p.macd_sig, p.symbol)),
    ("MACD_BEAR_X", "bear", "trend", "MACD bearish crossover", "MACD(12,26) crossed below its 9-day signal in the last 3 sessions",
     lambda p: _cross_dn(p.macd, p.macd_sig, p.symbol)),
    ("BREAKOUT_55_VOL", "bull", "breakout", "55-day breakout on volume", "close above the previous 55-session high with volume >= 1.5x its 20-day average",
     lambda p: (p.close > p.hh55) & (p.vol_ratio >= 1.5)),
    ("BREAKDOWN_55_VOL", "bear", "breakout", "55-day breakdown on volume", "close below the previous 55-session low with volume >= 1.5x its 20-day average",
     lambda p: (p.close < p.ll55) & (p.vol_ratio >= 1.5)),
    ("NEAR_52W_HIGH", "bull", "breakout", "Near 52-week high", "close within 2% of the previous 252-session high",
     lambda p: p.close >= 0.98 * p.hh252),
    ("RSI14_OVERSOLD", "bull", "mean_reversion", "RSI(14) oversold", "RSI(14) < 30",
     lambda p: p.rsi14 < 30),
    ("RSI14_OVERBOUGHT", "bear", "mean_reversion", "RSI(14) overbought", "RSI(14) > 70",
     lambda p: p.rsi14 > 70),
    ("RSI3_DIP_UPTREND", "bull", "mean_reversion", "Short-term dip in an uptrend", "RSI(3) < 10 and close > 200-day SMA",
     lambda p: (p.rsi3 < 10) & (p.close > p.sma200)),
    ("BB_LOWER", "bull", "mean_reversion", "Below lower Bollinger band", "close < SMA20 - 2 standard deviations",
     lambda p: p.close < p.bb_lo),
    ("BB_UPPER", "bear", "mean_reversion", "Above upper Bollinger band", "close > SMA20 + 2 standard deviations",
     lambda p: p.close > p.bb_up),
    ("STRETCHED_UP", "bear", "mean_reversion", "Stretched above 20-day average", "close more than 2 ATR(14) above the 20-day SMA",
     lambda p: (p.close - p.sma20) > 2 * p.atr14),
    ("HIGH_RS", "bull", "relative_strength", "Market leader (relative strength)", "63-session return vs Nifty in the top 20% of stocks",
     lambda p: p.rs_pct >= 0.8),
    ("LOW_RS", "bear", "relative_strength", "Market laggard (relative strength)", "63-session return vs Nifty in the bottom 20% of stocks",
     lambda p: p.rs_pct <= 0.2),
    ("SECTOR_STRONG", "bull", "relative_strength", "Strong sector", "sector's 21-session return in the top 20% of sectors",
     lambda p: p.sector_rank_pct >= 0.8),
    ("SECTOR_WEAK", "bear", "relative_strength", "Weak sector", "sector's 21-session return in the bottom 20% of sectors",
     lambda p: p.sector_rank_pct <= 0.2),
    ("OBV_CONFIRM", "bull", "volume", "Volume confirms the rise", "on-balance volume up over 20 sessions and close above the 20-day SMA",
     lambda p: (p.obv_chg20 > 0) & (p.close > p.sma20)),
    ("DISTRIBUTION_DAY", "bear", "volume", "Heavy-volume selling", "close down more than 2% on volume >= 1.5x its 20-day average",
     lambda p: (p.ret1 < -0.02) & (p.vol_ratio >= 1.5)),
    ("GAP_UP_HOLD", "bull", "gap", "Gap up that held", "opened 2%+ above the previous close and closed at or above the open",
     lambda p: (p.gap >= 0.02) & (p.close >= p.open)),
    ("GAP_DOWN", "bear", "gap", "Gap down that held", "opened 2%+ below the previous close and closed at or below the open",
     lambda p: (p.gap <= -0.02) & (p.close <= p.open)),
    ("HIGH_VOLATILITY", "bear", "volatility", "Elevated volatility", "ATR(14) / close in the top 10% of stocks",
     lambda p: p.atr_pct_rank >= 0.9),
    ("MARKET_UPTREND", "bull", "market", "Market above its 200-day average", "Nifty 50 above its 200-day SMA",
     lambda p: p.nifty_vs_200 > 0),
    ("MARKET_DOWNTREND", "bear", "market", "Market below its 200-day average", "Nifty 50 below its 200-day SMA",
     lambda p: p.nifty_vs_200 < 0),
    ("WEAK_BREADTH", "bear", "market", "Weak market breadth", "fewer than 40% of stocks above their 200-day SMA",
     lambda p: p.breadth < 0.40),
    ("HIGH_VIX", "bear", "market", "Fear elevated", "India VIX more than 20% above its 1-year median",
     lambda p: p.vix_rel > 1.2),
]
BY_ID = {s[0]: dict(id=s[0], side=s[1], group=s[2], name=s[3], definition=s[4], fn=s[5]) for s in SIGNALS}
# Market-wide signals are the same for every stock on a day: a same-day comparison is impossible, so they are tested
# against other DAYS (time series), not other stocks.
MARKET_WIDE = {k for k, v in BY_ID.items() if v["group"] == "market"}


def compute(panel, ids=None):
    """Boolean frame (same index as panel) with one column per signal. Missing inputs -> False."""
    out = {}
    for k in ids or BY_ID:
        with np.errstate(invalid="ignore"):
            v = BY_ID[k]["fn"](panel)
        out[k] = pd.Series(v, index=panel.index).astype("boolean").fillna(False).astype(bool)
    return pd.DataFrame(out, index=panel.index)


def active(row_signals):
    """Signals firing for one row (a Series of booleans)."""
    return [k for k, v in row_signals.items() if bool(v)]


def info(k):
    s = BY_ID[k]
    return {x: s[x] for x in ("id", "side", "group", "name", "definition")}
