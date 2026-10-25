# Candlestick patterns (tradeplan/patterns.py, v2)

Detection uses **TA-Lib** when installed (its definitions follow Steve Nison; "long" / "small" bodies and wicks are measured
against the stock's average of the previous 10 candles). Without TA-Lib a built-in version covers the common patterns;
ten rare multi-candle ones need TA-Lib. Inside bar and tweezers are always built-in (TA-Lib has no equivalent).

| Pattern | Kind | Built-in rule (TA-Lib's when installed) |
|---|---|---|
| Doji | indecision | body ≤ 10% of the range |
| Hammer / Hanging man | reversal | lower wick ≥ 2× body, small upper wick, small body; after a fall / rise |
| Inverted hammer / Shooting star | reversal | upper wick ≥ 2× body, small lower wick; after a fall / rise |
| Engulfing | reversal | body covers the previous opposite-coloured body |
| Harami | reversal | body inside the previous larger opposite body |
| Morning / Evening star | reversal | long candle, small gapped body, strong opposite candle |
| Three white soldiers / black crows | continuation | three long candles, closes rising / falling, near the extremes |
| Inside bar | indecision | range inside the previous range |
| Piercing line / Dark cloud cover | reversal | opens beyond the previous low / high, closes past the middle of the previous long body |
| Dragonfly / Gravestone doji | reversal | doji with a long lower / upper wick (≥ 60% of range); direction from the prior trend |
| Tweezer bottom / top | reversal | equal lows / highs (within 0.05 ATR), opposite colours, after a fall / rise |
| Belt hold | reversal | long candle opening at its extreme (wick ≤ 5% of range) against the trend |
| Kicker | reversal | long candle, then a gap the other way and a long opposite candle |
| Three inside / outside up-down | reversal | harami / engulfing confirmed by a third candle |
| Abandoned baby, Doji star, Meeting lines, Upside gap two crows, Ladder bottom, Concealing baby swallow | reversal | TA-Lib only |
| Rising / falling three methods, Mat hold, Separating lines, Three-line strike | continuation | TA-Lib only |

## Reading rules (strength)
Each detected pattern is checked on four rules; 3–4 = strong, 2 = medium, 0–1 = weak:
1. **Trend context**: a reversal needs the opposite prior trend (bullish after a fall: 5-day return < −2%, or below the 10-day
   average and falling); a continuation needs the same trend.
2. **Location** (reversals): the low (bullish) or high (bearish) within 0.5 ATR of the previous 20-day low / high or of S1 / R1.
3. **Volume**: ≥ 1.3× the 20-day average.
4. **Confirmation**: the next candle closes beyond the pattern candle's close in the signal's direction ("…" until it exists).

## History test (`python -m tradeplan.pattern_stats`, Data: Nifty 500 since 2015)
For each pattern, direction and bucket (all · in trend context · strong): share of cases where the price moved the pattern's
way after 1 and 5 sessions (from the pattern day's close; strong cases from the confirming close), against the same rate for all
stock-days. Split 2015–2022 vs 2023+: an edge counts only if it is ≥ 1 point and has the same sign in both halves.
Verdicts: edge · against · no tested edge · too few (n < 30). In the TA evidence a candle counts ±1 only when it is medium or
strong and its history verdict isn't "against" or "no tested edge". Survivorship: today's Nifty 500 list.
