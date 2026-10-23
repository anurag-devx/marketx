# MarketX — NSE equity research agents

[![tests](https://github.com/anurag-devx/marketx/actions/workflows/tests.yml/badge.svg)](https://github.com/anurag-devx/marketx/actions/workflows/tests.yml)

Personal research project.

A local research system for Indian equities (NSE, cash market only). It downloads daily prices, news, NSE filings and macro
data; runs a set of agents (market regime, screener, strategy, technical, risk, validation, supervisor report); and provides
research tools: market report, macro & geopolitics, search and deep search, a documents library, the Strategy Lab and the
**Market Debate Lab** (bull vs bear agents, statistical validation, a probability engine and a forecast scorecard).

Runs on your own computer, binds to localhost only, and **places no orders**. AI features use local models via Ollama by
default; online models are optional and off until you switch them on. Research, not investment advice.

## Requirements
- macOS (Linux works for everything except the after-close schedule), Python 3.10+ (3.11 recommended)
- An Angel One SmartAPI account for prices
- Optional: [Ollama](https://ollama.com) with `gemma4:e4b`, `qwen3.5:9b` and `nomic-embed-text` for the AI features

## Setup (one time)
```bash
git clone https://github.com/anurag-devx/marketx.git && cd marketx
./setup.sh            # creates .venv, installs packages, runs the tests, writes requirements.lock
./setup.sh --ai       # same, plus the documents-library / AI packages
```
Keys live **outside** the project, in your home folder (never in this repository): copy the names from
[`.env.example`](.env.example) into `~/.angel_env` (required) and `~/.swing_env` (optional), then `chmod 600 ~/.angel_env ~/.swing_env`.
Copy `account.example.json` to `account.json` and set your capital; `positions.csv` is created when you add a position.

## Start here
```bash
.venv/bin/python cli.py dashboard          # port 8503; or double-click "Start Dashboard.command"
```
The launchers (`Start Dashboard.command`, `Start Strategy Lab.command`) use `.venv` when it exists, otherwise the system
Python. After `setup.sh`, the after-close schedule (Settings) is re-pointed to `.venv` automatically
(`.venv/bin/python cli.py schedule-refresh` does it by hand). Delete `.venv` to go back to the system Python.

From the dashboard you can fetch data, run any agent or all of them, run the backtest, edit positions and your account,
and read every output, report and log. The **Strategy Lab** opens from the sidebar, with `cli.py lab`, or with its launcher (port 8502).

## What is (and isn't) in this repository
Code, tests, screener strategy configs and examples only. Ignored (see `.gitignore`): `data/`, `results/`, `reports/`,
`logs/`, `backups/`, `lab/workspace/`, `settings.json`, `account.json`, `positions.csv`, `.venv/` and any key files.
Tests use synthetic data only (no keys, no network) and run on every push (`.github/workflows/tests.yml`).

## Command line (optional)
Use `.venv/bin/python` instead of `python3` once `setup.sh` has run.

| Command | What it does |
|---|---|
| `python3 cli.py dashboard` | Open the control centre |
| `python3 cli.py daily` | Same as the dashboard's "Fetch data + run all agents" (data, agents, reports, the lab's nightly forecasts) |
| `python3 cli.py schedule-refresh` | Point the after-close schedule at the Python running the command |
| `python3 cli.py lab` | Open the Strategy Lab |
| `python3 cli.py fetch` | Update prices: only the days since the last download (a few minutes) |
| `python3 cli.py fetch --full` | Re-download all history (about 30 minutes) |
| `python3 cli.py fetch --broad` | Also download every other NSE stock, for point-in-time tests (3-4 hours once: Angel allows 5,000 requests an hour) |
| `python3 cli.py quality` | Run the data quality checks |
| `python3 cli.py backup` | Back up the database, settings, positions, account and the Lab |
| `python3 cli.py run regime` | Run one agent (regime, strategy, technical, risk, validation, report) or `all` |
| `python3 cli.py backtest` | Backtest + strategy research + analysis |
| `python3 cli.py research` | Strategy agent research only |
| `python3 cli.py status` | Last run of each agent |

## Dashboard pages
| Section | Page | What it's for |
|---|---|---|
| Today | Overview | Market state, agent health, run buttons, trades and exits, momentum buys/sells |
| Today | Market | Highlights (breadth, sectors, gainers/losers, 52-week highs, unusual volume) and news |
| Today | Signals and decisions | Every signal from every strategy and what happened to it |
| Today | Stock lookup | Chart, numbers, what each agent says, history, news for any stock |
| Agents and strategies | Agents / Strategies / Validation | Run any agent; Strategies → Report is the full swing backtest report (downloadable); paper results |
| Your money | Positions and risk | Edit positions and account; exposure vs limits; momentum order list (shares to buy/sell for the money you allocate) |
| Ask and records | Ask the system / Reports and logs | Questions answered from agent data; reports and logs |
| Setup | Settings | Approvals, parameters (versioned), schedule, alerts, news feeds, backups and restore |
| Setup | Data health | Downloads (update / full / all NSE stocks), quality checks, corporate actions, universe |

## Screener (candidate discovery)
Pipeline: market data → data validation (`core/quality.py`) → universe (`screener/universe.py`) → hard filters
(`screener/filters.py`) → indicators (`screener/features.py`) → setups (`screener/setups.py`) → ranking
(`screener/ranking.py`) → shortlist (`screener/screen.py`) → downstream strategy module (`screener/interface.py`).
It produces a **research shortlist, not buy recommendations**. It runs with the agents (step `screener`, after regime).

- Dashboard: **Today → Screener** has five tabs:
  - **Technical setups**: four steps (what to screen → hard filters → setups, each with its own settings tab → qualify, rank, show);
  - **Market movers**: gainers, losers, gaps, volume spikes, 52-week highs/lows, RSI extremes, 3-month leaders, on any date (`screener/market_scan.py`);
  - **News**: headlines filtered by time, source, words, stocks, sectors and a rough keyword tone, plus the stocks named (`screener/news_scan.py`);
  - **NSE announcements**: filings filtered by type (results, orders, M&A, fund raising, dividends, ...), words, stocks and sectors
    (`core/announcements.py`: NSE's announcements RSS, plus a best-effort read of NSE's website; every fetch adds to
    `data/announcements.parquet`, so history builds up over time; `python3 cli.py announcements`);
  - **Agents' shortlist**: what the screener step saved in the last agent run.
  News and announcements are fetched with every data download (sidebar button or `python3 cli.py daily`).
- Also in the dashboard: **Today → Screener → Technical setups** (run any date and settings, sortable table, reasons, CSV) and
  **Agents and strategies → Screener research** (experiments, comparison, validation, enabling strategies).
- Command line: `python3 cli.py screen [--date 2026-10-16] [--strategy two_confirmations] [--all | --top 50]`
- Settings and their allowed ranges: `screener/config.py`. Defaults: close ₹20+, 252 sessions of history, median traded value
  ₹2 cr+ over the previous 20 sessions, 95% of the last 60 sessions traded, ETFs and stocks with open data errors excluded.
- Output columns: every feature used, one pass/fail column per filter and per setup, ranking score and method, rank,
  qualification and rejection reasons, data-quality flags, config id, screener version, data version, run time.
- Downstream: `from screener.interface import load_shortlist` → `Shortlist` (rows with ids, features, flags, ranking,
  warnings, the exact config). The screener never decides trades.

### Strategies and experiments
- A strategy = a JSON file in `config/screener/strategies/` (name, version, description, hypothesis, limitations, `config`
  overrides, `primary_horizon`, `evaluate_on`). New setup logic = one function in `screener/setups.py` + its ranges in
  `screener/config.py`.
- An experiment = a JSON file in `config/screener/experiments/` (strategies + optional `grid` of dotted settings).
- Run: dashboard button or `python3 cli.py experiments` (finished configurations are skipped; `--force` re-runs).
- Compare: Screener research → Compare, or `python3 -m screener compare` (writes results/screener/comparison.csv).
- Evaluation (`screener/evaluate.py`): entry at the next open, exits after 5/10/15/20 sessions, after costs; baseline = every
  eligible stock on the same days; development 2016–20, validation 2021–23, holdout 2024+ (hidden by default, views logged);
  labels crossing a period end are purged; uncertainty from daily averages with Newey-West errors (overlap-aware).
- **Validated research candidate** (development and validation only, at the primary horizon): enough signals and dates,
  beats the baseline (t ≥ 2.0 development, ≥ 1.65 validation), positive after costs in both, 5% worst outcome no more than
  2 pp worse than the baseline's, and beating the baseline in at least 60% of years. The number of configurations tried is
  recorded and the matching multiple-testing threshold is shown. The comparison also shows, for grid experiments, how many
  neighbouring settings (one numeric setting moved one step) also beat the baseline in validation: prefer results that
  hold across neighbours over a single best setting.
- Promote: Screener research → Strategies → Enable (stored with its exact config in `config/screener/enabled.json`).
  Unvalidated strategies can be enabled but are labelled.
- Limitations: NSE only (BSE listings are not downloaded; `universe.canonicalize` is ready for them); delisted stocks are not
  available from Angel One, so history is still somewhat survivor-biased; ETFs are excluded by name (heuristic); the index
  leg of benchmark-relative returns runs close-to-close because index opens are not in the data.

### Tests
`python3 cli.py test` runs the whole suite in `tests/` (about 290 tests, synthetic data only). The screener part covers: indicators (Wilder RSI/ADX, warm-up), previous-high excluding today,
RSI crossing, grouping, missing sessions, duplicate listings, corporate actions, point-in-time ranking, no look-ahead
(changing later prices doesn't change a past screen), label alignment and purging, empty results, provider throttling /
expired sessions / failures, reproducibility, experiment metadata, skipping, failures and adding a new setup.

## Strategy Lab (separate workspace)
Write a strategy as rules (`close > sma(200) and rsi(3) < 15`), or rank-and-hold (`prev(ret(126), 21)`), and test it
with the same discipline as the agents: design 2016-20, validation 2021-23, holdout 2024+ hidden until you reveal it,
a random-entry control, t-stat on weekly clusters, and a count of how many versions you tried. Each test has a full
report (downloadable). Strategies live in `lab/workspace/`; the agents ignore them until a passing strategy is sent to
the Strategy agent and you approve it (Settings → Approvals); after that its daily signals appear in Signals and decisions.
Rule language reference: Lab → Rule language.

## Data safety
- Price updates fetch only new days; if Angel re-adjusts a stock's history (split/bonus), that stock is re-downloaded.
- Raw (unadjusted) bars are kept in `data/raw/`, separate from the adjusted prices the agents use; `data/instruments.csv`
  holds Angel's security ids.
- Quality checks run after every download and before every agent run. A stock with an unreviewed recent error gets
  no new signals. Review issues or add a missed split in Data health (`data/corporate_actions.csv`).
- Point-in-time universe: `core/universe_pit.py` (top N by traded value at each March/September review), or NSE's
  historical lists in `data/membership.csv` (symbol, from_date, to_date) if you have them.
- A backup is taken automatically once a day before the agents run (`backups/`, newest 20 kept).

## Optional keys (~/.swing_env, outside this folder)
All optional; the full list with explanations is in [`.env.example`](.env.example): online model keys (Anthropic, OpenAI,
Gemini), web search keys (Tavily, Brave, Serper, Exa), FRED, Telegram alerts. Then `chmod 600 ~/.swing_env`.

## Files you maintain
- `positions.csv`: every trade you actually take (symbol, sector, strategy, entry_date, entry_price, quantity, stop, target); delete the row when you exit
- `account.json`: equity, peak_equity, today_pnl, and the money allocated to each portfolio strategy (set on Positions)

## Layout
- `core/` shared code: config (all parameters), indicators, strategy library, simulation engine, DuckDB store
- `agents/` regime, strategy, technical, risk, validation, orders (momentum order list)
- `screener/` screener package; `config/screener/` its strategies and experiments; `tests/` automated tests
- `core/provider.py` market-data provider interface (Angel One; pacing, retries, re-login, validation)
- `lab/` Strategy Lab: rule language (expr.py), test engine, workspace store, pages; `lab_app.py` starts it
- `research/` event study, daily moves, catalysts, macro sensitivity, market report and its writer
- `debate/` Market Debate Lab: evidence layer, signals, statistical validation, probability engine, agents, judge,
  nightly batch and forecast scorecard (see `debate/README.md`)
- `ui/` dashboard pages; `dashboard.py` starts it
- `data/` prices, index, universe, framework.duckdb (all agent tables)
- `results/` backtest report, trades, strategy cards
- `reports/` daily reports
- `logs/` one log file per day; every agent run is also in the `agent_runs` table

## Safety rules built in
- No strategy trades unless the backtest shows 50+ trades, an edge over a random-entry control in both the
  design (2016-20) and validation (2021-23) periods, and t-stat >= 2. 2024+ is a holdout, reported only.
- Stale data (last bar more than 4 days old) -> no new trades.
- Risk agent veto is final: 1% risk per trade x state multiplier, max 10 positions, 15% per stock,
  3 per sector, 2% daily loss cap, half size at 10% drawdown, stop at 15%.
- Nothing places orders. You execute.
