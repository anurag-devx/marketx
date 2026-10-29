# MarketX research log

Living record of every test: what was asked, the pass mark set before it ran, what came out and what it means. Numbers are after costs unless marked gross. Nothing is edited after the fact; corrections get a new entry.

## Ground rules
- Goal: at least 1% a day on capital. Every result is reported against it, including the gap.
- No hindsight: each test year is predicted only from earlier years (walk-forward); saved forecasts are never recomputed.
- Real costs: delivery about 0.30% round trip + 0.10% slippage each way; intraday 0.06% + 0.05% each way; stop exits pay another 0.05%. Futures costs come with the F&O work.
- Baselines every time: random, simple momentum, Nifty buy-and-hold.
- Known bias in tests T1-T8: the stock list was today's Nifty 500 (stocks that later fell out or delisted are missing), which flatters absolute returns. The all-NSE work (R1-R2) removes it.

## Summary

| Test | Question | Status | Key result |
|---|---|---|---|
| T1 Market Lab as built (24 saved nights) | Do Market Lab's nightly picks make money after costs? | **FAILED** (2026-10-28) | Top 10, 1-day hold: Rs 1 lakh -> Rs 84,171 (-15.8%) vs Nifty -2.5%. All 24 setups lost money. |
| T2 Framework diagnostic | Is the analysis framework the weak link? Which inputs carry direction? | **PASSED** (2026-10-28) | Stable signals found (reversal, volatility, gap fade, sector strength, results day, delivery). Equal-weight matching 0.014 vs boosting 0.051: the framework captured about a quarter of what is there. |
| T3 Model v2: daily backtest 2019-2026 | With a learned model, do daily books make money after costs? | **PASSED** (2026-10-28) | On paper: intraday long top 10 / short bottom 10 +0.23% a day, worst drop -9%, no losing year. Later failed the stress test (T4). |
| T4 Stress test of the intraday long-short | Does the T3 intraday edge survive realistic entry and liquidity? | **FAILED** (2026-10-28) | Gone: all 500 at 09:20 +0.04% a day; liquid 200 at 09:20 -0.05%; everything negative at 0.10% slippage. |
| T5 Swing, buy-only, hold 5 / 10 / 20 sessions | Is there a swing edge after costs on liquid stocks? | **PASSED** (2026-10-28) | Simple rules, hold 20, top 10: 25.0% a year vs Nifty 8.3%, beat Nifty 8 of 8 years, worst drop -36% (Nifty -33%). |
| T6 Swing robustness | Does T5 hold for other start days, sizes, costs and a market filter? | **PASSED** (2026-10-28) | Hold 20 / 15 stocks: 16.4 / 19.0 / 23.9% a year (lowest / average / highest start day) vs Nifty 10%; 5-7 of 8 years. 2025 weak in every version. |
| T7 Intraday with stop-loss and targets | Can 3-5 morning setups a day with hard stops reach the 1% target with margin? | **FAILED** (2026-10-28) | Best: 3 trades at 09:30, hold to 15:15: +0.06% a day at 1x; +0.30% at 5x with a -95% worst drop; 2 losing years of 8. |
| T8 Day direction | Can the market's 09:30 -> 15:15 direction be called before or at 09:30? | **FAILED** (2026-10-29) | 52-54% right vs 51% always-up. Ceiling: if the direction were known, the intraday picks earn +0.58% a day at 1x, no losing year. |
| R1 Data check: all NSE | Do NSE's files match Angel's prices, and how big is the survivorship bias? | **pending** | needs: Bhavcopy 2015 -> download finished. |
| R2 Swing on all NSE | Does the swing book hold on every NSE stock, delistings included? | **pending** | needs: R1 passed. |
| R3 Day direction, more inputs | Do global closes, FII/client positions and option open interest improve the day call? | **pending** | needs: Macro download (S&P/Nasdaq/Nikkei) and F&O history. |
| R4 Nifty / Bank Nifty futures | Do intraday (09:30 -> 15:15) and overnight (close -> open) direction trades make money with futures costs and margin? | **pending** | needs: F&O history. |
| R5 Stock futures: open-interest build-up | Do long build-up / short covering / short build-up / long unwinding, basis and rollover predict 1- and 5-day moves, long and short? | **pending** | needs: F&O history. |
| R6 Options as signals | Do put-call ratio, open-interest shifts and max pain call the index's next-day direction? | **pending** | needs: F&O history. |
| R7 Intraday on all NSE | Do the intraday tests change with 1,000 liquid stocks instead of 200? | **pending** | needs: 5-minute bars for the top 1,000. |
| R8 Order flow | Do bid/ask imbalance and trade-side flow predict 5-30 minute moves? | **pending** | needs: About 60 recorded days (recorder started 29 Oct 2026). |

## Plan

Agreed 29 Oct 2026: data, then research, then screens. Data: D1 all NSE cash 2015 on (with delistings, split/bonus adjusted, liquidity floor you set, default Rs 5 crore a day); D2 F&O history 2015 on (futures per expiry, options OI, participant positions, near-month series); D3 S&P 500, Nasdaq, Nikkei. Screens after research: all NSE in every page, Live for the 1,000 most liquid, and a separate F&O section.

## Done

### T1. Market Lab as built (24 saved nights) - FAILED (2026-10-28)

- **Question:** Do Market Lab's nightly picks make money after costs?
- **Pass mark (set before running):** Simulate: beat Nifty after costs over the saved nights.
- **Result:** Top 10, 1-day hold: Rs 1 lakh -> Rs 84,171 (-15.8%) vs Nifty -2.5%. All 24 setups lost money.

| Measure | Value |
|---|---|
| Top 10, hold 1 day | -15.8% |
| Nifty same days | -2.5% |
| Costs paid | Rs 7,428 |
| Setups that made money | 0 of 24 |

The radar predicts 'moves at least 1% either way' (volatility), not direction; the lean was 'unclear' almost every night, so buy-only can't use it.
- **Script:** `Tally -> Simulate` · **Results:** `Tally -> Simulate`

### T2. Framework diagnostic - PASSED (2026-10-28)

- **Question:** Is the analysis framework the weak link? Which inputs carry direction?
- **Pass mark (set before running):** Find inputs whose link to next-day relative return keeps its sign in at least 10 of 12 years (|t| >= 3), and compare methods on the same data.
- **Result:** Stable signals found (reversal, volatility, gap fade, sector strength, results day, delivery). Equal-weight matching 0.014 vs boosting 0.051: the framework captured about a quarter of what is there.

| Measure | Value |
|---|---|
| Equal-weight matching (Market Lab) | 0.014 |
| Ridge | 0.015 |
| Gradient boosting | 0.051 |
| Opening gap fade (intraday), t | -26 |
| After strong up / down results day, 5 days | +0.42% / -0.79% |

Stable 12/12 years: big 1-5 day movers do worse next day; volatile stocks do worse; opening gaps fade intraday (strongest); strong sector -> stock does better; results day -> stock lags; delivery % up -> better. News tone very weak. Filings on average give no buy signal; first reaction to results continues for 5 days.
- **Script:** `research/diagnose.py` · **Results:** `results/diagnose/report.json`

### T3. Model v2: daily backtest 2019-2026 - PASSED (2026-10-28)

- **Question:** With a learned model, do daily books make money after costs?
- **Pass mark (set before running):** Positive after costs in every year group, beating random and momentum.
- **Result:** On paper: intraday long top 10 / short bottom 10 +0.23% a day, worst drop -9%, no losing year. Later failed the stress test (T4).

| Measure | Value |
|---|---|
| Intraday long-short, boosting | +0.23% a day |
| Same, simple rules | +0.07% a day |
| Buy top 10, sell next close, boosting | +0.28% a day, worst drop -61% |

Buy-only next-day: costs (about 0.4% a day) dominate and gains came in 2020-21, negative in 2025-26. Superseded by T4.
- **Script:** `research/model2.py` · **Results:** `results/model2/report.json`

### T4. Stress test of the intraday long-short - FAILED (2026-10-28)

- **Question:** Does the T3 intraday edge survive realistic entry and liquidity?
- **Pass mark (set before running):** Still positive after costs with entry at 09:20, liquid 200 only and 0.10% slippage per side.
- **Result:** Gone: all 500 at 09:20 +0.04% a day; liquid 200 at 09:20 -0.05%; everything negative at 0.10% slippage.

| Measure | Value |
|---|---|
| All 500, open, 0.05% slip | +0.18% a day |
| All 500, 09:20 | +0.04% |
| Liquid 200, 09:20 | -0.05% |
| Any, 0.10% slip at 09:20 | negative |

The 'edge' lived in the noisy opening print of thinly traded stocks. Not tradeable. Parked.
- **Script:** `research/model2.py part=stress` · **Results:** `results/model2/stress.csv`

### T5. Swing, buy-only, hold 5 / 10 / 20 sessions - PASSED (2026-10-28)

- **Question:** Is there a swing edge after costs on liquid stocks?
- **Pass mark (set before running):** Beat Nifty after costs in at least 6 of 8 years with a worst drop no worse than about Nifty's.
- **Result:** Simple rules, hold 20, top 10: 25.0% a year vs Nifty 8.3%, beat Nifty 8 of 8 years, worst drop -36% (Nifty -33%).

| Measure | Value |
|---|---|
| Simple rules | 25.0% a year, 8/8 years |
| 12-month momentum | 23.5%, 4/8 |
| Boosting | 19.9%, 5/8 |
| Random | 9.4% |
| Nifty | 8.3% |

ML not needed for swing: simple rules are the most consistent and explainable.
- **Script:** `research/swing.py` · **Results:** `results/swing/report.csv`

### T6. Swing robustness - PASSED (2026-10-28)

- **Question:** Does T5 hold for other start days, sizes, costs and a market filter?
- **Pass mark (set before running):** Every start day beats Nifty overall; at least 5 of 8 years.
- **Result:** Hold 20 / 15 stocks: 16.4 / 19.0 / 23.9% a year (lowest / average / highest start day) vs Nifty 10%; 5-7 of 8 years. 2025 weak in every version.

| Measure | Value |
|---|---|
| Hold 20, 15 stocks, 0.25%/side | 19.0% a year (avg) |
| Same at 0.40%/side | 16.5% |
| Hold 10, 10 stocks + Nifty filter | 18.9%, worst drop -23% |
| 2025 vs Nifty | -6 to -19 points |

Built as Market Lab -> Swing picks (15 stocks, hold 20 sessions): rebuilt 2019-2026 22.3% a year vs Nifty 10.0%, worst drop -28.6%. About 0.08% a day: far below the 1% target.
- **Script:** `research/swing.py part=robust` · **Results:** `results/swing/robust.csv`

### T7. Intraday with stop-loss and targets - FAILED (2026-10-28)

- **Question:** Can 3-5 morning setups a day with hard stops reach the 1% target with margin?
- **Pass mark (set before running):** At least +0.2% a day net at 1x (so about 1% at 5x) with no losing year.
- **Result:** Best: 3 trades at 09:30, hold to 15:15: +0.06% a day at 1x; +0.30% at 5x with a -95% worst drop; 2 losing years of 8.

| Measure | Value |
|---|---|
| Win rate | 50.6% |
| Avg win / loss | +1.69% / -1.61% |
| Per day, 1x | +0.06% |
| Per day, 5x | +0.30% (worst drop -95%) |
| Stop / target grids | all worse than holding to 15:15 |

The model leans on market-wide inputs (VIX, how the market opened, breadth): it is mostly calling the day's direction. Baselines (momentum, gap fade, random) all lose.
- **Script:** `research/intraday.py` · **Results:** `results/intraday/report.csv`

### T8. Day direction - FAILED (2026-10-29)

- **Question:** Can the market's 09:30 -> 15:15 direction be called before or at 09:30?
- **Pass mark (set before running):** Right on at least 56% of days in every year group.
- **Result:** 52-54% right vs 51% always-up. Ceiling: if the direction were known, the intraday picks earn +0.58% a day at 1x, no losing year.

| Measure | Value |
|---|---|
| Best call | 54.2% right |
| Always 'up' | 51.1% |
| Value for intraday picks | +0.06% -> +0.07% a day |
| Ceiling (direction known) | +0.58% a day |

The day's direction is the main lever toward 1%. Not yet tried: S&P 500 / Nasdaq / Nikkei (download added), GIFT Nifty, FII/DII flows, F&O positioning: that is R3.
- **Script:** `research/daydir.py` · **Results:** `results/daydir/calls.csv`

## Planned

### R1. Data check: all NSE - pending

- **Question:** Do NSE's files match Angel's prices, and how big is the survivorship bias?
- **Pass mark (set before running):** Nifty 500 closes within 0.5% on at least 99% of stock-days after adjustment; adjustment events match known splits/bonuses.
- **Needs:** Bhavcopy 2015 -> download finished.
- **Script:** `research/(to write)`

### R2. Swing on all NSE - pending

- **Question:** Does the swing book hold on every NSE stock, delistings included?
- **Pass mark (set before running):** Beats Nifty after costs in at least 6 of 8 years at the chosen liquidity floor (tested at Rs 1 / 5 / 10 / 25 crore).
- **Needs:** R1 passed.

### R3. Day direction, more inputs - pending

- **Question:** Do global closes, FII/client positions and option open interest improve the day call?
- **Pass mark (set before running):** Right on at least 56% of days in every year group.
- **Needs:** Macro download (S&P/Nasdaq/Nikkei) and F&O history.

### R4. Nifty / Bank Nifty futures - pending

- **Question:** Do intraday (09:30 -> 15:15) and overnight (close -> open) direction trades make money with futures costs and margin?
- **Pass mark (set before running):** Positive after costs in every year group at 1x.
- **Needs:** F&O history.

### R5. Stock futures: open-interest build-up - pending

- **Question:** Do long build-up / short covering / short build-up / long unwinding, basis and rollover predict 1- and 5-day moves, long and short?
- **Pass mark (set before running):** Beats random and momentum after costs in 6 of 8 years.
- **Needs:** F&O history.

### R6. Options as signals - pending

- **Question:** Do put-call ratio, open-interest shifts and max pain call the index's next-day direction?
- **Pass mark (set before running):** Adds at least 2 points of hit rate to R3's call, every year group.
- **Needs:** F&O history.

### R7. Intraday on all NSE - pending

- **Question:** Do the intraday tests change with 1,000 liquid stocks instead of 200?
- **Pass mark (set before running):** Same as T7: at least +0.2% a day net at 1x, no losing year.
- **Needs:** 5-minute bars for the top 1,000.

### R8. Order flow - pending

- **Question:** Do bid/ask imbalance and trade-side flow predict 5-30 minute moves?
- **Pass mark (set before running):** Positive after intraday costs on held-out days, with realistic fills.
- **Needs:** About 60 recorded days (recorder started 29 Oct 2026).

## Entries

