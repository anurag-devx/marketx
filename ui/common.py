"""Shared helpers for the dashboard pages."""
import json, subprocess, sys
for _p in ["streamlit", "duckdb", "pandas", "pyarrow", "tabulate", "plotly"]:
    try:
        __import__(_p)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", _p])
import duckdb, numpy as np, pandas as pd, streamlit as st
import plotly.graph_objects as go
from core import config as C, jobs
from core.indicators import build as build_features
from core.strategies import STRATEGIES
from agents import strategy as strategy_agent

LOGS = C.BASE / "logs"
ANALYSIS = C.RESULTS / "analysis"
ACCENT, MUTED, GOOD, BAD, REF = "#2f6fd6", "#a7adb5", "#2e9d5b", "#d0453b", "#7b8189"
STATE_LABEL = {"up_calm": "Uptrend, calm", "up_volatile": "Uptrend, volatile",
               "down_calm": "Downtrend, calm", "down_volatile": "Downtrend, volatile"}
PERIOD_LABEL = {"design": "Design 2016–20", "validation": "Validation 2021–23", "holdout": "Holdout 2024–26"}

AGENTS = [
    dict(key="fetch", name="Data", built=True, kind="Code",
         what="Downloads daily prices for the Nifty 500, plus Nifty 50 and India VIX, from Angel One using the keys in ~/.angel_env.",
         io="Angel One API → data/prices.parquet, data/index.parquet", table=None),
    dict(key="regime", name="Market regime", built=True, kind="Code",
         what="Classifies today's market state (trend × volatility) and switches on the strategies that passed the backtest in that state.",
         io="prices + strategy cards → regime_daily", table="regime_daily"),
    dict(key="screener", name="Screener", built=True, kind="Code",
         what="Candidate discovery: filters every downloaded stock on data quality and liquidity, checks five technical setups, ranks the qualifiers and saves a research shortlist. It does not decide trades.",
         io="prices + quality checks + enabled screener strategies -> screener_results, results/screener/*.csv", table="screener_results"),
    dict(key="strategy", name="Strategy", built=True, kind="Code",
         what="Owns the strategy library: decides which swing strategies run in today's market state, lists this month's holdings, buys and sells for approved portfolio strategies, auto-pauses failing strategies, and researches new ones. It proposes; you approve.",
         io="regime_daily + strategy cards + outcomes → strategies, portfolio_targets, proposals", table="strategies"),
    dict(key="technical", name="Technical", built=True, kind="Code",
         what="Scans the latest close with today's active strategies and scores each signal 0–100.",
         io="regime_daily + prices → candidates", table="candidates"),
    dict(key="risk", name="Risk", built=True, kind="Code",
         what="Sizes each candidate (1% risk per trade × state multiplier), applies portfolio limits, and flags exits due on your positions. Its veto is final.",
         io="candidates + positions.csv + account.json → decisions, exits_due", table="decisions"),
    dict(key="validation", name="Validation", built=True, kind="Code",
         what="Paper-tracks every approved recommendation to its exit with the backtest's rules and compares live results with the backtest.",
         io="decisions + prices → outcomes", table="outcomes"),
    dict(key="report", name="Supervisor", built=True, kind="Code (v0)",
         what="Assembles the daily report from what the agents stored. Ranks by setup score; catalysts and sector tilts join when those agents exist.",
         io="all agent tables → reports/YYYY-MM-DD.md", table=None),
    dict(key="backtest", name="Backtest + analysis", built=True, kind="Code",
         what="Tests every strategy on history by market state and period, selects the playbook, then runs the diagnostics behind the Strategy analysis page.",
         io="prices → strategy_cards, results/, results/analysis/", table="strategy_cards"),
    dict(key="macro-all", name="Macro and geopolitical", built=True, kind="Code",
         what="Fetches crude, USD/INR, US yields, the dollar and US VIX (FRED), the geopolitical risk index (GPR), worldwide news themes (GDELT) "
              "and FII/DII flows (NSE); measures each sector's sensitivity to them and what followed past shocks; links this week's themes to stocks in Search.",
         io="FRED, GPR, GDELT, NSE → data/macro/series.parquet → results/macro/", table=None),
    dict(key="announcements", name="News and NSE announcements", built=True, kind="Code (download + tagging + event study)",
         what="Downloads news (feeds, sitemaps, archives) and NSE filings, links them to stocks, reads article text where allowed, "
              "and measures how stocks moved around them (News research).",
         io="feeds + sitemaps + NSE → data/news/, data/announcements.parquet → results/news_events/", table=None),
    dict(key="market-report", name="Market report writer", built=True, kind="Code + local model",
         what="Writes the daily research note (what moved, sectors, new players, macro, filings) from computed facts; every number is checked.",
         io="prices + filings + news + macro → reports/market/YYYY-MM-DD.md", table=None),
    dict(key="debate", name="Market Debate Lab", built=True, kind="Code + local model",
         what="Bull and bear research agents argue one stock from the same evidence; a statistical agent tests every claim on history; "
              "a walk-forward probability model and a judge give a forecast or abstain. Records are immutable and scored when the horizon ends.",
         io="prices + news + filings + macro → data/debate/runs, forecasts.parquet, outcomes.parquet", table=None),
    dict(key="search-index", name="Search and documents", built=True, kind="Code + local model",
         what="Makes headlines, filings, article text and your documents searchable by meaning and keywords; powers Search, Deep search and Documents.",
         io="news + filings + documents → data/library/lancedb", table=None),
]
RUN_LABEL = {"fetch": "Fetch latest data", "regime": "Run regime", "screener": "Run screener", "strategy": "Run strategy (daily)", "technical": "Run technical", "risk": "Run risk",
             "validation": "Run validation", "report": "Build report", "backtest": "Run backtest + analysis",
             "announcements": "Fetch announcements", "news": "Fetch news", "news-check": "Check news sources",
             "news-backfill": "Download older news", "news-retag": "Re-link headlines to stocks",
             "announcements-backfill": "Download older NSE filings", "company-info": "Download company sectors and shares",
             "news-research": "Run the news study", "market-report": "Write the market report",
             "library-ingest": "Add to documents library", "news-articles": "Read news article text", "install-ai": "Install missing packages",
             "macro-fetch": "Fetch macro data", "macro-research": "Run macro study", "macro-all": "Fetch macro data + run study",
             "search-index": "Build the search index", "history-all": "Download all history",
             "debate": "Run a debate analysis", "debate-eval": "Score finished debate forecasts"}


# ---------------- data helpers ----------------
def q(sql, params=None):
    if not C.DB_PATH.exists():
        return None
    try:
        con = duckdb.connect(str(C.DB_PATH), read_only=True)
        try:
            return con.execute(sql, params or []).df()
        finally:
            con.close()
    except Exception as e:
        if "lock" in str(e).lower():
            st.toast("Database busy while an agent writes; refresh in a moment.")
        return None


def has(df):
    return df is not None and len(df) > 0


def show(df, empty="Nothing yet.", **kw):
    if has(df):
        st.dataframe(df, width="stretch", hide_index=True, **kw)
    else:
        st.caption(empty)


@st.cache_data(ttl=120)
def prices():
    p = C.DATA / "prices.parquet"
    if not p.exists():
        return None
    d = pd.read_parquet(p)
    d["date"] = pd.to_datetime(d.date).dt.tz_localize(None).dt.normalize()
    return d


@st.cache_data(ttl=60)
def acsv(name):
    p = ANALYSIS / name
    return pd.read_csv(p) if p.exists() else None


def account():
    a = dict(capital=1_000_000, equity=None, peak_equity=None, today_pnl=0.0)
    if C.ACCOUNT_JSON.exists():
        a.update(json.loads(C.ACCOUNT_JSON.read_text()))
    a["equity"] = a["equity"] or a["capital"]
    a["peak_equity"] = max(a["peak_equity"] or a["equity"], a["equity"])
    return a


def last_runs():
    return q("""select agent, status, started, seconds, rows_out, message, for_date from (
                select *, row_number() over (partition by agent order by started desc) rn from agent_runs) where rn = 1""")


def pct(v, d=2):
    return "—" if v is None or pd.isna(v) else f"{v:+.{d}f}%"


# ---------------- jobs ----------------
def launch(name, args=None):
    try:
        jobs.start(name, args)
        st.cache_data.clear()
        st.rerun()
    except RuntimeError as e:
        st.error(str(e))


def job_panel(key):
    """Live output of the running (or last) job; refreshes itself every 3 s while a job runs."""
    def body():
        j = jobs.current()
        hist = jobs.history(1)
        path = j["log"] if j else (hist[0]["path"] if hist else None)
        if j:
            st.info(f"Running **{j['name']}** since {j['started']}")
        elif hist:
            res = hist[0]["result"]
            (st.success if res == "ok" else st.warning)(f"Last job: **{hist[0]['job']}** — {res} at {hist[0]['finished']}")
        if path:
            st.code(jobs.tail(path, 60) or "(no output yet)", language=None)
        for lane, probe in (("news", "news"), ("history", "history-all"), ("lab", "debate")):
            sj = jobs.current(probe)
            if sj:
                st.info(f"Also running **{sj['name']}** ({lane} lane) since {sj['started']}")
                st.code(jobs.tail(sj["log"], 25) or "(no output yet)", language=None)
                if st.button(f"Stop {sj['name']}", key=f"{key}-stop-{lane}"):
                    jobs.stop(probe); st.rerun()
    if (jobs.current() or jobs.current("news") or jobs.current("history-all") or jobs.current("debate")) and hasattr(st, "fragment"):
        st.fragment(run_every=3)(body)()
    else:
        body()


# ---------------- charts ----------------
def fig_base(fig, h=360, legend=False):
    fig.update_layout(height=h, margin=dict(l=8, r=8, t=8, b=8), showlegend=legend,
                      legend=dict(orientation="h", y=1.08, x=0), hoverlabel=dict(namelength=-1))
    return fig


def heat(df, rows, cols, row_label=None, col_label=None, text_extra=None, h=None):
    z = [[df.get((r, c), np.nan) for c in cols] for r in rows]
    txt = [[("" if pd.isna(df.get((r, c), np.nan)) else f"{df[(r, c)]:+.2f}%") + (text_extra(r, c) if text_extra else "")
            for c in cols] for r in rows]
    m = np.nanmax(np.abs(np.array(z, dtype=float))) or 1
    fig = go.Figure(go.Heatmap(z=z, x=[col_label(c) if col_label else c for c in cols], y=[row_label(r) if row_label else r for r in rows],
                               text=txt, texttemplate="%{text}", colorscale=[[0, BAD], [0.5, "#f4f4f2"], [1, GOOD]],
                               zmin=-m, zmax=m, showscale=False, xgap=3, ygap=3, hovertemplate="%{y} · %{x}<br>%{text}<extra></extra>"))
    fig.update_yaxes(autorange="reversed", type="category")
    fig.update_xaxes(type="category", side="top")
    return fig_base(fig, h or 60 + 36 * len(rows))




@st.cache_data(ttl=3600, show_spinner="Building indicators for all stocks (about 30 seconds, once per hour)...")
def features(mtime):
    px = prices()
    idx = pd.read_parquet(C.DATA / "index.parquet")
    idx["date"] = pd.to_datetime(idx.date).dt.tz_localize(None).dt.normalize()
    uni = pd.read_csv(C.DATA / "universe.csv")
    feat, mkt = build_features(px, idx, uni)
    return feat, mkt


def feats():
    p = C.DATA / "prices.parquet"
    return features(p.stat().st_mtime) if p.exists() else (None, None)


@st.cache_data(ttl=3600)
def universe():
    p = C.DATA / "universe.csv"
    return pd.read_csv(p) if p.exists() else pd.DataFrame(columns=["Company Name", "Industry", "Symbol"])


NAV = {}   # page objects by key, filled by dashboard.py so pages can link to each other


def go_stock(symbol):
    st.session_state["stock"] = symbol
    if "stocks" in NAV:
        st.switch_page(NAV["stocks"])


def sidebar_actions():
    j = jobs.current()
    with st.sidebar:
        st.divider()
        for lane, probe in (("News", "news"), ("History", "history-all"), ("Lab", "debate")):
            sj = jobs.current(probe)
            if sj:
                st.caption(f"{lane} lane: {sj['name']} running (since {sj['started'][11:16]})")
        if j:
            st.info(f"Running: {j['name']} (since {j['started'][11:16]})")
            if st.button("Stop job", width="stretch"):
                jobs.stop(); st.rerun()
        else:
            if st.button("▶ Fetch data + run all agents", type="primary", width="stretch", key="sb-daily"):
                launch("fetch+pipeline")
            if st.button("Run all agents (no download)", width="stretch", key="sb-pipe"):
                launch("pipeline")
        pend = q("select count(*) n from proposals where status = 'pending'")
        if has(pend) and pend.n.iat[0] > 0:
            st.warning(f"{pend.n.iat[0]} proposal(s) to approve")
        from core import labproc
        if labproc.running():
            st.link_button("🧪 Strategy Lab ↗", labproc.URL, width="stretch")
        elif st.button("🧪 Open Strategy Lab", width="stretch", key="sb-lab", help="Your separate workspace for testing strategies (opens in a new tab)"):
            labproc.start()
            import time as _t
            for _ in range(40):
                if labproc.running():
                    break
                _t.sleep(0.25)
            st.rerun()
        venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        st.caption(f"v{C.VERSION} · runs on this Mac · places no orders · Python {sys.version.split()[0]} "
                   + ("(project .venv)" if venv else "(system — start with Start Dashboard.command to use .venv)"))
