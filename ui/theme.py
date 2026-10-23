"""MarketX look: one palette, one type scale, cards, soft shadows and small transitions.

Two layers:
  .streamlit/config.toml [theme]  official Streamlit theme (colours, fonts, radius, chart colours); always on
  CSS below                        card styling, shadows, gradients, hover effects; switchable
                                   (Settings → Appearance), so the plain look stays one click away
Only presentation changes here: no page logic, data or agent code depends on this file."""
import json
import streamlit as st
from core import config as C, atomic

PREFS = C.DATA / "ui.json"
ASSETS = C.BASE / "ui" / "assets"

# palette (also in .streamlit/config.toml)
INDIGO, CYAN, SLATE, MUTED_TXT = "#4F46E5", "#06B6D4", "#0F172A", "#64748B"
GREEN, RED, AMBER = "#059669", "#DC2626", "#D97706"
BORDER, SURFACE, CANVAS = "#E3E6EE", "#FFFFFF", "#F6F7FB"

CSS = """
<style>
:root{
  --mx-accent:#4F46E5; --mx-accent-2:#06B6D4; --mx-text:#0F172A; --mx-muted:#64748B;
  --mx-border:#E3E6EE; --mx-surface:#FFFFFF; --mx-canvas:#F6F7FB;
  --mx-shadow:0 1px 2px rgba(15,23,42,.04),0 4px 14px rgba(15,23,42,.05);
  --mx-shadow-hover:0 2px 4px rgba(15,23,42,.05),0 10px 24px rgba(15,23,42,.08);
  --mx-radius:14px; --mx-ease:cubic-bezier(.2,.7,.3,1);
}
/* canvas: a faint gradient wash at the top */
[data-testid="stApp"]{
  background:radial-gradient(1200px 380px at 12% -8%, rgba(79,70,229,.07), transparent 60%),
             radial-gradient(900px 320px at 95% -10%, rgba(6,182,212,.06), transparent 60%), var(--mx-canvas);
}
[data-testid="stHeader"]{background:rgba(246,247,251,.72);backdrop-filter:saturate(1.4) blur(10px);-webkit-backdrop-filter:saturate(1.4) blur(10px)}
[data-testid="stAppDeployButton"]{display:none}
[data-testid="stMainBlockContainer"]{padding-top:2.4rem;padding-bottom:4rem;max-width:1480px}

/* typography */
h1{font-weight:720!important;letter-spacing:-.025em;font-size:2rem!important;line-height:1.15!important}
h2{font-weight:680!important;letter-spacing:-.02em}
h3,h4{font-weight:650!important;letter-spacing:-.01em}
[data-testid="stCaptionContainer"]{color:var(--mx-muted)}

/* metric cards */
[data-testid="stMetric"]{
  background:var(--mx-surface);border:1px solid var(--mx-border);border-radius:var(--mx-radius);
  padding:14px 16px 12px;box-shadow:var(--mx-shadow);
  transition:transform .18s var(--mx-ease),box-shadow .18s var(--mx-ease),border-color .18s;
}
[data-testid="stMetric"]:hover{transform:translateY(-2px);box-shadow:var(--mx-shadow-hover);border-color:#D5D9E5}
[data-testid="stMetricLabel"]{color:var(--mx-muted);font-weight:550}
[data-testid="stMetricLabel"] p{font-size:.8rem!important}
[data-testid="stMetricValue"]{font-weight:680;letter-spacing:-.02em}
[data-testid="stMetricValue"],[data-testid="stMetricValue"] div,[data-testid="stMetricValue"] p{font-size:clamp(1.3rem,1.6vw,1.8rem)!important;line-height:1.25}
[data-testid="stMetricValue"] .stMarkdownEllipsis,[data-testid="stMetricValue"] p{white-space:normal!important;overflow:visible!important;text-overflow:clip!important}

/* keyed containers: st.container(border=True, key="tile-..." or "card-...") become cards */
[class*="st-key-tile-"],[class*="st-key-card-"]{background:var(--mx-surface);box-shadow:var(--mx-shadow);
  transition:transform .18s var(--mx-ease),box-shadow .18s var(--mx-ease)}
[class*="st-key-tile-"]:hover{transform:translateY(-2px);box-shadow:var(--mx-shadow-hover)}

/* tabs as soft pills */
[role="tablist"]{gap:4px;border-bottom:1px solid var(--mx-border);padding-bottom:6px}
[data-testid="stTab"]{border-radius:999px;padding:6px 14px!important;transition:background .15s,color .15s}
[data-testid="stTab"]:hover{background:rgba(79,70,229,.06)}
[data-testid="stTab"][aria-selected="true"]{background:rgba(79,70,229,.10);color:var(--mx-accent)}
[data-testid="stTab"][aria-selected="true"] p{font-weight:650;color:var(--mx-accent)}

/* expanders, charts, tables, alerts, code: rounded surfaces */
[data-testid="stExpander"] details{border-radius:var(--mx-radius);border:1px solid var(--mx-border);background:var(--mx-surface);box-shadow:var(--mx-shadow)}
[data-testid="stExpander"] summary:hover{color:var(--mx-accent)}
[data-testid="stPlotlyChart"]{background:var(--mx-surface);border:1px solid var(--mx-border);border-radius:var(--mx-radius);padding:10px 10px 12px;box-shadow:var(--mx-shadow)}
[data-testid="stDataFrame"]{border-radius:12px;overflow:hidden;box-shadow:var(--mx-shadow)}
[data-testid="stAlert"]>div{border-radius:12px}
[data-testid="stCode"] pre{border-radius:12px}

/* buttons */
[data-testid="stBaseButton-primary"]{
  background:linear-gradient(135deg,#4F46E5 0%,#6366F1 55%,#0EA5E9 130%)!important;border:0!important;
  box-shadow:0 1px 2px rgba(79,70,229,.25),0 6px 16px rgba(79,70,229,.22);
  transition:transform .15s var(--mx-ease),box-shadow .15s var(--mx-ease),filter .15s;
}
[data-testid="stBaseButton-primary"]:hover{transform:translateY(-1px);filter:brightness(1.05);box-shadow:0 2px 4px rgba(79,70,229,.25),0 10px 22px rgba(79,70,229,.26)}
[data-testid="stBaseButton-secondary"]{background:var(--mx-surface)!important;transition:border-color .15s,color .15s,box-shadow .15s}
[data-testid="stBaseButton-secondary"]:hover{border-color:var(--mx-accent)!important;color:var(--mx-accent)!important;box-shadow:0 4px 12px rgba(79,70,229,.10)}
button:disabled{box-shadow:none!important;transform:none!important}

/* sidebar */
[data-testid="stSidebar"]{background:linear-gradient(180deg,#FFFFFF 0%,#FAFBFE 100%);border-right:1px solid var(--mx-border)}
[data-testid="stSidebarNavLink"]{border-radius:10px;margin:1px 6px;transition:background .15s}
[data-testid="stSidebarNavLink"]:hover{background:rgba(79,70,229,.06)}
[data-testid="stSidebarNavLink"][aria-current="page"]{background:linear-gradient(90deg,rgba(79,70,229,.12),rgba(6,182,212,.06))}
[data-testid="stSidebarNavLink"][aria-current="page"] span{color:var(--mx-accent)!important;font-weight:650}
[data-testid="stNavSectionHeader"]{letter-spacing:.06em;text-transform:uppercase;font-size:.7rem!important;color:var(--mx-muted)}

/* accessibility */
:focus-visible{outline:2px solid var(--mx-accent)!important;outline-offset:2px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}

/* phones */
@media (max-width:640px){
  [data-testid="stMainBlockContainer"]{padding:1.4rem .9rem 3rem}
  h1{font-size:1.5rem!important}
  [data-testid="stMetric"]{padding:10px 12px}
  [role="tablist"]{overflow-x:auto;flex-wrap:nowrap}
}
</style>
"""


def prefs():
    try:
        return json.loads(PREFS.read_text()) if PREFS.exists() else {}
    except Exception:
        return {}


def modern():
    return prefs().get("modern", True)


def set_modern(on):
    atomic.write_text(PREFS, json.dumps({**prefs(), "modern": bool(on)}))


def apply():
    """Call once per page run, right after st.set_page_config."""
    if (ASSETS / "logo.svg").exists():
        try:
            st.logo(str(ASSETS / "logo.svg"), icon_image=str(ASSETS / "icon.svg"), size="large")
        except Exception:
            pass
    if modern():
        st.markdown(CSS, unsafe_allow_html=True)
