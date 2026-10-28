# Kronos (experimental)

A separate section of MarketX for [Kronos](https://github.com/shiyu-coder/Kronos), an open-source foundation model
for price candles (MIT licence). It is kept apart on purpose:

- **Own environment:** `.venv-kronos` (PyTorch lives only here; the main `.venv` is untouched)
- **Own code:** `kronos_lab/` (ours) and `kronos_lab/vendor/Kronos` (theirs, pinned to one commit, not in git)
- **Own results:** `results/kronos/forecasts.parquet` — nothing else in MarketX reads it
- **Own tab:** *Market Lab → Kronos* (Forecasts · Scorecard · Run · How it works). The Market Lab column there is side by side only
- **Own job lane:** Kronos jobs never block the daily run, news, history downloads or the Debate Lab

The agents, the screener, the Debate Lab and the Forecast scorecard do not use Kronos.

## Install
Market Lab → Kronos → Run → **Install**, or in Terminal: `bash kronos_lab/setup.sh` (about 1 GB, once).

## Use
- **Forecast latest day:** Nifty 50, 5 sessions ahead, 8 sampled paths per stock
- **Replay:** the same on past days whose outcome is known, to score it straight away
- **Options (Run tab):** stocks, model (mini 2048 bars / small / base), lookback, horizon, sampled paths, temperature,
  top_p, processor or Mac chip, daily or 5-minute bars (`freq=5m at=1100 h=12`). The app estimates the run time from
  `results/kronos/speed.json`, which each run updates
- **Nightly (optional):** `kronos_lab/nightly.py` is the last step of Fetch + run all; it starts a forecast in Kronos's
  own lane when the toggle is on (`results/kronos/settings.json`)
- **Simulate ₹ (Tally):** "Kronos picks" = top N by chance of going up on each daily forecast day

## How it is scored (`kronos_lab/score.py`)
Direction hit rate vs always guessing the more common outcome; Brier skill of P(up) vs the base rate;
daily rank correlation (IC) between predicted and actual returns; top-fifth minus bottom-fifth actual return vs
the Nifty. t-statistics use only days at least one horizon apart. Kronos's training data may include the
replayed period, so replay results can be flattering; live forecasts are the real test.
