"""Agent 1 - Market regime. Deterministic: no LLM.
Classifies today's market state. Which strategies run in it is the strategy agent's call."""
import pandas as pd
from core.config import STATE_MULT


def run(mkt, cards):
    m = mkt.dropna(subset=["market_state"])
    if m.empty:
        raise RuntimeError("Not enough index history to classify the market state.")
    t = m.iloc[-1]
    state = t.market_state
    parts = score_parts(t)
    score = round(sum(v for v in parts.values() if v == v))
    run_len = (m.market_state[::-1] != state).cumsum().eq(0).sum()
    return dict(
        date=pd.Timestamp(t.date), market_state=state, state_raw=t.state_raw,
        pending_change=bool(t.state_raw != state), days_in_state=int(run_len),
        regime_score=int(score), size_multiplier=STATE_MULT[state],
        active_strategies="", nifty=float(t.nifty),
        nifty_vs_200d_pct=float(100 * (t.nifty / t.nifty_sma200 - 1)),
        vix=float(t.vix) if pd.notna(t.vix) else None,
        vix_median=float(t.vix_median) if pd.notna(t.vix_median) else None,
        breadth_200=float(t.breadth_200) if pd.notna(t.breadth_200) else None,
        nifty_vs_50d_pct=float(100 * (t.nifty / t.nifty_sma50 - 1)),
        score_trend200=parts["trend200"], score_trend50=parts["trend50"], score_volatility=parts["volatility"],
        score_breadth=parts["breadth"], note="")


# Regime score (0-100, shown on the Overview; it does not decide trades: the market state and the tested playbook do).
# Four parts of 0-25 each, graded rather than pass/fail so a small miss isn't scored like a collapse:
#   long trend    Nifty vs its 200-day average:  -5% or worse -> 0,  +5% or better -> 25
#   short trend   Nifty vs its 50-day average:   -3% -> 0,  +3% -> 25
#   volatility    India VIX / its 1-year median: 1.3x or higher -> 0,  0.8x or lower -> 25
#   breadth       share of stocks above their 200-day average: 30% -> 0,  70% -> 25
SCORE_RANGES = {"trend200": (-5.0, 5.0), "trend50": (-3.0, 3.0), "volatility": (1.3, 0.8), "breadth": (0.30, 0.70)}


def _grade(x, lo, hi):
    if x is None or x != x:
        return float("nan")
    return round(25 * min(max((x - lo) / (hi - lo), 0.0), 1.0), 1)


def score_parts(t):
    vix_ratio = (t.vix / t.vix_median) if pd.notna(t.vix) and pd.notna(t.vix_median) and t.vix_median else float("nan")
    return dict(trend200=_grade(100 * (t.nifty / t.nifty_sma200 - 1), *SCORE_RANGES["trend200"]),
                trend50=_grade(100 * (t.nifty / t.nifty_sma50 - 1), *SCORE_RANGES["trend50"]),
                volatility=_grade(vix_ratio, *SCORE_RANGES["volatility"]),
                breadth=_grade(t.breadth_200 if pd.notna(t.breadth_200) else float("nan"), *SCORE_RANGES["breadth"]))
