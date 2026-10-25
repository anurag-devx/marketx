# MarketX — NSE equity research

[![tests](https://github.com/anurag-devx/marketx/actions/workflows/tests.yml/badge.svg)](https://github.com/anurag-devx/marketx/actions/workflows/tests.yml)

Personal research project. A local research app for Indian equities (NSE cash market). It downloads daily and 5-minute
prices, news, NSE filings, results and macro data, and turns them into research: which stocks are likely to move tomorrow
(Market Lab), a rule-based trade plan for each, screeners, strategy tests and reports.

Runs on your own computer, binds to localhost only and **places no orders**. AI features use local models via Ollama by
default; online models are optional and off until you switch them on. Research, not investment advice.

## Requirements
- macOS (Linux works for everything except the after-close schedule), Python 3.10+ (3.11 recommended)
- An Angel One SmartAPI account for prices
- Optional: [Ollama](https://ollama.com) with `gemma4:e4b`, `qwen3.5:9b` and `nomic-embed-text` for the AI features
- Optional: TA-Lib (candle patterns; MarketX has a built-in fallback). Install from the app: Trade plan → Install TA-Lib

## Setup (one time)
```bash
git clone https://github.com/anurag-devx/marketx.git && cd marketx
./setup.sh            # creates .venv, installs packages, runs the tests, writes requirements.lock
./setup.sh --ai       # same, plus the documents-library / AI packages
```
Keys live **outside** the project, in your home folder (never in this repository): copy the names from
[`.env.example`](.env.example) into `~/.angel_env` (required) and `~/.swing_env` (optional), then
`chmod 600 ~/.angel_env ~/.swing_env`. Copy `account.example.json` to `account.json` and set your capital.

## Start
```bash
.venv/bin/python cli.py web               # http://127.0.0.1:8504  (or double-click "Start MarketX.command")
```
Every page has a **? How to use** guide, and terms in tables explain themselves on hover.

## What's in the app
| Section | What it does |
|---|---|
| **Market Lab** (Research) | For every stock, the chance of a 1%+ move next session (close-to-close, open-to-close, gap), the lean (up/down) when it has been tested, and why. Tabs: Move Radar, Stock analysis, Influence map, Scorecard, Debate (the older bull-vs-bear lab). See [docs/market_lab.md](docs/market_lab.md) |
| **Trade plan** | Rule-based entries (E1 break, E2 pullback), stop, targets (T1, T2) and time exit for intraday or swing, with size from your account and no-trade flags. Chart with tags and zones, candle patterns, and a TA agent that explains the plan in words. Secondary to Market Lab: it never changes the verdict |
| **Chart workspace** (Strategy Lab) | Save any chart, draw on it (lines, trends, boxes, entry/exit), add notes, tags and status, and review later against what happened. Keeps a snapshot of what MarketX said when you saved. Export as PNG |
| Today | Overview, market highlights, signals, stock lookup (chart, intraday, results, balance sheet, news, filings), screener |
| Agents and strategies | Regime, screener, strategy, technical, risk, validation and report agents; strategy reports; screener research |
| Your money | Positions and risk in plain words: exposure vs limits, momentum order list |
| Research | Market report, macro & geopolitics, search and deep search, documents library, Kronos (experimental) |
| Setup | Settings, schedule, alerts, backups; Data health (every download and its state) |

## Data (all downloaded from Data health, stored only on your Mac)
| Data | Source | First download | Daily |
|---|---|---|---|
| Daily prices, Nifty 500 (+ all NSE optional) | Angel One | ~30 min (all NSE: 3–4 h) | minutes |
| 5-minute bars, Nifty 500, from Sept 2016 | Angel One | several hours (~1.5 GB) | minutes |
| News, NSE announcements, filings | RSS / NSE | builds up over time | with each fetch |
| Quarterly results (NSE XBRL) | NSE | 1–3 h | with each fetch |
| Balance sheet & cash flow (NSE XBRL) | NSE | 15–20 min | with each fetch |
| Shareholding, F&O, macro | NSE / FRED | minutes | with each fetch |
| Search index (news + filings) | local | follows downloads | incremental; **Compact** in Data health keeps it fast |

From balance sheets and results MarketX computes, point in time: market cap, P/E (trailing 12 months), P/B, ROE,
debt/equity, net debt and free cash flow. A missing input leaves a figure blank; nothing is estimated.

## How results are tested
- **Point in time**: each figure only uses what was known by 21:00 IST on the decision day (macro lagged one day).
- **Out of sample**: Market Lab is scored walk-forward from 2019 on; strategies use design 2016–20, validation 2021–23
  and a hidden holdout 2024+.
- A **lean (up/down) is only shown if it has been tested and holds** (direction hit rate ≥ 53%); otherwise confidence is "low".
- Current Scorecard (2019–2026): Market Lab is good at **size** (the top-20 picks moved 1%+ about 74% of the time vs 68%
  for a typical stock, in 8 of 8 years). **Direction is close to a coin flip** (51–55%), except the overnight gap (~65%).
- The trade plan is not yet tested on history (next step, needs the 5-minute data).

## Command line (optional)
| Command | What it does |
|---|---|
| `cli.py web` | Open MarketX (port 8504) |
| `cli.py daily` | Fetch data and run everything (agents, reports, Market Lab radar, trade plans, intraday update) |
| `cli.py fetch` / `fetch --full` / `fetch --broad` | Update prices / re-download all / every NSE stock |
| `python -m marketlab` / `python -m marketlab test` | Build tonight's radar / rerun the Scorecard |
| `python -m core.intraday update` | Update 5-minute bars |
| `python -m core.fundamentals balance` | Read balance sheets and cash flows |
| `python -m core.search_compact` | Compact the search database |
| `cli.py screen` / `experiments` | Screener / screener experiments |
| `cli.py backtest`, `cli.py run all`, `cli.py status` | Backtest, agents, last runs |
| `cli.py quality`, `cli.py backup`, `cli.py test` | Data checks, backup, tests |

## Layout
- `web/` the app (standard-library HTTP server, plain HTML/JS/CSS); `web/static/` pages, charts, help
- `marketlab/` features, matching engine, Scorecard, radar · `tradeplan/` plan rules, candle patterns, TA agent
- `core/` config, providers (Angel One), intraday, fundamentals, news, search index, jobs, store
- `agents/`, `screener/`, `lab/` (Strategy Lab), `research/` (events, macro, valuation), `debate/`, `kronos_lab/`
- `tests/` automated tests (synthetic data only, no keys, no network; run on every push)
- Not in the repository (`.gitignore`): `data/`, `results/`, `reports/`, `logs/`, `backups/`, `lab/workspace/`,
  `settings.json`, `account.json`, `positions.csv`, `.venv/`, key files

## Safety rules built in
- Nothing places orders. You execute.
- Keys stay in `~/.angel_env` and `~/.swing_env`, outside the project; all data stays on your Mac.
- Risk: 1% risk per trade, max 10 positions, 15% per stock, 3 per sector, 2% daily loss cap, half size at 10% drawdown, stop at 15%.
- Stale data (last bar more than 4 days old) → no new trades. A stock with an unreviewed data error gets no signals.
- No strategy is used unless it beats a random-entry control in design and validation with enough trades.
