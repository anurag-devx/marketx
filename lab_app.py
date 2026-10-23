"""Strategy Lab: your separate workspace for testing strategy ideas. Runs locally; places no orders.
    python3 cli.py lab          (opens http://localhost:8502)
"""
import subprocess, sys
for _p in ["streamlit", "duckdb", "pandas", "pyarrow", "plotly"]:
    try:
        __import__(_p)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", _p])
import streamlit as st

st.set_page_config(page_title="Strategy Lab · MarketX", page_icon=str(__import__("pathlib").Path(__file__).resolve().parent / "ui" / "assets" / "icon.svg"), layout="wide")
from ui import theme  # noqa: E402
theme.apply()

from lab import ui  # noqa: E402

pages = {
    "Lab": [
        st.Page(ui.page_home, title="My strategies", icon="🧪", url_path="home", default=True),
        st.Page(ui.page_build, title="Build and test", icon="🛠️", url_path="build"),
        st.Page(ui.page_results, title="Results", icon="📊", url_path="results"),
        st.Page(ui.page_compare, title="Compare runs", icon="⚖️", url_path="compare"),
    ],
    "Help": [st.Page(ui.page_rules, title="Rule language", icon="📖", url_path="rules")],
}
ui.NAV.update({pg.url_path: pg for group in pages.values() for pg in group})
nav = st.navigation(pages)
with st.sidebar:
    st.divider()
    sid = st.session_state.get("sid")
    if sid:
        from lab import store
        rec = store.get(sid)
        if rec:
            st.caption(f"Working on: **{rec['name']}**")
    st.link_button("Agents dashboard ↗", "http://localhost:8501", width="stretch")
    st.caption("Separate workspace · the daily agents don't read it · places no orders")
nav.run()
