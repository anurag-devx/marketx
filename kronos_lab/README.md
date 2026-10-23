# Kronos (experimental)

A separate section of MarketX for [Kronos](https://github.com/shiyu-coder/Kronos), an open-source foundation model
for price candles (MIT licence). It is kept apart on purpose:

- **Own environment:** `.venv-kronos` (PyTorch lives only here; the main `.venv` is untouched)
- **Own code:** `kronos_lab/` (ours) and `kronos_lab/vendor/Kronos` (theirs, pinned to one commit, not in git)
- **Own results:** `results/kronos/forecasts.parquet` — nothing else in MarketX reads it
- **Own page:** *Experimental → Kronos* in the web app, with its own scorecard
- **Own job lane:** Kronos jobs never block the daily run, news, history downloads or the Debate Lab

The agents, the screener, the Debate Lab and the Forecast scorecard do not use Kronos.

## Install
Kronos page → **Install**, or in Terminal: `bash kronos_lab/setup.sh` (about 1 GB, once).

## Use
- **Forecast latest day:** Nifty 50, 5 sessions ahead, 8 sampled paths per stock
- **Replay:** the same on past days whose outcome is known, to score it straight away

## How it is scored (`kronos_lab/score.py`)
Direction hit rate vs always guessing the more common outcome; Brier skill of P(up) vs the base rate;
daily rank correlation (IC) between predicted and actual returns; top-fifth minus bottom-fifth actual return vs
the Nifty. t-statistics use only days at least one horizon apart. Kronos's training data may include the
replayed period, so replay results can be flattering; live forecasts are the real test.
