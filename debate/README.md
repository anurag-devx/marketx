# Market Debate Lab

Dashboard page: **Today → Market Debate Lab**. CLI: `python3 -m debate.engine sym=RELIANCE h=5 target=abs` (add `--no-llm` for template agents,
`date=YYYY-MM-DD` for a replay, `band=0.5` with `target=three`, `rounds=1..3`). Score finished forecasts: `python3 -m debate.evaluate`
(also runs at the end of **Fetch data + run all agents**).

## Fixed questions
Main: **5 sessions vs the Nifty 50** (`target=rel`). Second: **5 sessions absolute** (`target=abs`). Other horizons/targets are
experimental and reported separately.

## Nightly batch and scorecard
`python3 -m debate.batch` forecasts the saved stock selection (default Nifty 50; Market Debate Lab → Nightly batch) for both
fixed questions, code only, and appends to data/debate/batch_forecasts.parquet (never overwritten). It runs after
"Fetch data + run all agents" when switched on; `days=20` replays earlier sessions (labelled retrospective).
NSE index lists: `python3 -m core.stocklists` (saved in data/lists/, refreshed monthly).
Results: **Forecast scorecard** page (forecast vs actual, calibration, skill over time, probability buckets, verdicts,
breakdowns, biggest misses).

## Setup
- `scipy` is installed automatically the first time the lab runs (it needs it for the statistical tests). No other new package.
- Agents' model: Settings → AI models → *Market Debate Lab* (default local `qwen3.5:9b` via Ollama). Online models stay off unless you switch them on.
- Analyses run in their own job lane ("lab"): downloads and agents keep working meanwhile.

## Files
| path | what |
|---|---|
| data/debate/panel.parquet, market.parquet | indicator panel (rebuilt when prices change) |
| data/debate/models/*.json | probability models + out-of-sample metrics, per horizon/target/cutoff |
| data/debate/cache/*.json | universe signal tests, per cutoff/horizon/target/regime |
| data/debate/runs/<id>.json | one immutable record per analysis |
| data/debate/forecasts.parquet, outcomes.parquet | forecast index, scored outcomes |

## Providers used (all already in the app)
| provider | data | history | timestamp semantics |
|---|---|---|---|
| Angel One SmartAPI | daily OHLCV, Nifty 50, India VIX | 2015 → | session date; close = 15:30 IST |
| NSE announcements | filings | from the last backfill | exchange time, IST |
| news sitemaps / RSS | headlines linked to stocks | mostly 2026 | publisher time, IST; same-title stories merged |
| FRED, GPR, GDELT, NSE FII/DII | macro factors and sensitivities | years | observation date, aligned to the next Indian session |

Not available (shown as such, never guessed): fundamentals and valuation, analyst estimates, options/futures, short interest and
holdings as numbers, intraday bars, BSE prices.

## Known limitations
- Short-horizon direction models show no out-of-sample skill on this data so far, so most verdicts are *Insufficient evidence*. That
  is the honest result, not a bug.
- Universe tests use today's Nifty 500 list for all years (survivorship bias).
- News history is short: most event studies return *Insufficient data* until more history accumulates.
- Stored news/filings were downloaded after publication; edits or deletions at the source cannot be reconstructed for replays.
- Language-model text can differ between reruns; statistics, probabilities and verdict rules are deterministic.
