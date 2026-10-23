"""Swing agents control centre. Runs locally on your Mac; places no orders.
    python3 cli.py dashboard        (or: streamlit run dashboard.py)
"""
import subprocess, sys
for _p in ["streamlit", "duckdb", "pandas", "pyarrow", "tabulate", "plotly", "requests"]:
    try:
        __import__(_p)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", _p])
import streamlit as st

st.set_page_config(page_title="Swing agents", page_icon="📈", layout="wide")

from ui import common, pages_core as P, signals, stocks, market, ask, settings, data_health, screener as scr, news_research, market_report, documents, macro, search, debate, scorecard  # noqa: E402

pages = {
    "Today": [
        st.Page(P.page_overview, title="Overview", icon="🏠", url_path="overview", default=True),
        st.Page(search.page, title="Search", icon="🔍", url_path="search"),
        st.Page(market.page, title="Market", icon="🌐", url_path="market"),
        st.Page(market_report.page, title="Market report", icon="📑", url_path="market-report"),
        st.Page(macro.page, title="Macro & geopolitics", icon="🌍", url_path="macro"),
        st.Page(scr.page, title="Screener", icon="🧭", url_path="screener"),
        st.Page(signals.page, title="Signals and decisions", icon="🎯", url_path="signals"),
        st.Page(stocks.page, title="Stock lookup", icon="🔎", url_path="stocks"),
        st.Page(debate.page, title="Market Debate Lab", icon="⚖️", url_path="debate-lab"),
        st.Page(scorecard.page, title="Forecast scorecard", icon="🎯", url_path="forecast-scorecard"),
    ],
    "Agents and strategies": [
        st.Page(P.page_agents, title="Agents", icon="🤖", url_path="agents"),
        st.Page(P.page_analysis, title="Strategies", icon="🧪", url_path="strategies"),
        st.Page(scr.research_page, title="Screener research", icon="🔬", url_path="screener-research"),
        st.Page(news_research.page, title="News research", icon="📰", url_path="news-research"),
        st.Page(P.page_validation, title="Validation", icon="✅", url_path="validation"),
    ],
    "Your money": [
        st.Page(P.page_positions, title="Positions and risk", icon="💼", url_path="positions"),
    ],
    "Ask and records": [
        st.Page(ask.page, title="Ask the system", icon="💬", url_path="ask"),
        st.Page(documents.page, title="Documents", icon="📚", url_path="documents"),
        st.Page(P.page_logs, title="Reports and logs", icon="📄", url_path="reports"),
    ],
    "Setup": [
        st.Page(settings.page, title="Settings", icon="⚙️", url_path="settings"),
        st.Page(data_health.page, title="Data health", icon="🩺", url_path="data"),
    ],
}
common.NAV.update({pg.url_path: pg for group in pages.values() for pg in group})
nav = st.navigation(pages)
common.sidebar_actions()
nav.run()
