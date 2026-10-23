"""All tunable parameters in one place. Changes here = a new version (log it in CHANGELOG.md)."""
from pathlib import Path

VERSION = "0.1.0"

BASE = Path(__file__).resolve().parent.parent
DATA, RESULTS, REPORTS = BASE / "data", BASE / "results", BASE / "reports"
DB_PATH = DATA / "framework.duckdb"
POSITIONS_CSV = BASE / "positions.csv"      # you maintain this: trades you actually hold
ACCOUNT_JSON = BASE / "account.json"        # you maintain this: capital, peak equity, today's P&L

# Costs and universe
COST_RT = 0.003            # 0.3% round trip: STT 0.2% delivery + stamp + brokerage + slippage
MIN_TURNOVER_CR = 20       # 20-day avg turnover, Rs crore
MIN_PRICE = 50

# Research periods (strategy lab discipline)
DESIGN = ("2016-01-01", "2020-12-31")
VALIDATION = ("2021-01-01", "2023-12-31")
HOLDOUT_FROM = "2024-01-01"   # reported, never used for selection

# Market states
STATES = ["up_calm", "up_volatile", "down_calm", "down_volatile"]
STATE_MULT = {"up_calm": 1.0, "up_volatile": 0.5, "down_calm": 0.25, "down_volatile": 0.25}
STATE_CONFIRM_DAYS = 2     # a new state must hold 2 closes before the playbook switches
VIX_LOOKBACK = 252

# Playbook selection from the backtest
MIN_TRADES_STATE = 50      # trades needed in a state before a strategy can be active there
MIN_T_STAT = 2.0           # edge over random-entry control must be statistically significant (weekly clusters)

# Risk agent
RISK = dict(
    risk_per_trade=0.01,   # hitting the stop loses 1% of capital (x state multiplier)
    max_positions=10,
    max_position_pct=0.15,
    max_per_sector=3,
    daily_loss_cap=0.02,
    dd_half=0.10,
    dd_stop=0.15,
)

# Technical agent scoring weights
SCORE_W = dict(expectancy=0.5, strength=0.3, rr=0.2)

# Market news feeds (RSS). Edit from the dashboard: Settings.
NEWS_FEEDS = [
    dict(name="ET Markets", url="https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", kind="markets"),
    dict(name="ET Stocks", url="https://economictimes.indiatimes.com/markets/stocks/news/rssfeeds/2146842.cms", kind="companies"),
    dict(name="Moneycontrol Markets", url="https://www.moneycontrol.com/rss/marketreports.xml", kind="markets"),
    dict(name="Moneycontrol Business", url="https://www.moneycontrol.com/rss/business.xml", kind="companies"),
    dict(name="Livemint Markets", url="https://www.livemint.com/rss/markets", kind="markets"),
    dict(name="Business Standard Markets", url="https://www.business-standard.com/rss/markets-106.rss", kind="markets"),
    dict(name="BBC World", url="https://feeds.bbci.co.uk/news/world/rss.xml", kind="world"),
]

# Alerts after each daily run
ALERTS = dict(macos=True, telegram=False)

# Model used by "Ask the system" when an Anthropic API key is in ~/.swing_env
LLM_MODEL = "claude-sonnet-5-5"
LOCAL_LLM = {"enabled": True}      # market report: use the model at all (model choice lives in AI → features → report)
AI = {}                            # AI models per feature (see core/llm.py for the defaults); edited in Settings → AI models

# ---------- your overrides, saved from the dashboard (Settings) ----------
SETTINGS_JSON = BASE / "settings.json"
EDITABLE = ["COST_RT", "MIN_TURNOVER_CR", "MIN_PRICE", "STATE_MULT", "STATE_CONFIRM_DAYS", "MIN_TRADES_STATE",
            "MIN_T_STAT", "RISK", "SCORE_W", "NEWS_FEEDS", "ALERTS", "LLM_MODEL", "LOCAL_LLM", "AI"]
SETTINGS_REV = 0


def _apply_overrides():
    global VERSION, SETTINGS_REV
    import json
    if not SETTINGS_JSON.exists():
        return
    try:
        o = json.loads(SETTINGS_JSON.read_text())
    except Exception:
        return
    g = globals()
    for k in EDITABLE:
        if k in o:
            if isinstance(g[k], dict) and isinstance(o[k], dict):
                g[k] = {**g[k], **o[k]}
            else:
                g[k] = o[k]
    SETTINGS_REV = int(o.get("_rev", 0))
    if SETTINGS_REV:
        VERSION = f"{VERSION}+s{SETTINGS_REV}"


_apply_overrides()
