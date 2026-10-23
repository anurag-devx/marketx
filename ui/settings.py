"""Settings: approvals, strategy library, parameters (versioned), schedule and alerts, news feeds, data health."""
import importlib
from ui.common import *  # noqa: F401,F403
from ui import pages_core as P
from core import settings as S, schedule, notify, secrets, backup


def approvals():
    pend = q("select * from proposals where status = 'pending' order by created")
    if has(pend):
        for p in pend.itertuples():
            with st.container(border=True):
                st.markdown(f"**{p.title}** · from the {p.agent} agent · {pd.Timestamp(p.created):%d %b %Y %H:%M}")
                st.write(p.rationale)
                with st.expander("Evidence"):
                    st.json(json.loads(p.evidence))
                b = st.columns([1, 1, 6])
                if b[0].button("Approve", key=f"ap-{p.id}", type="primary"):
                    strategy_agent.decide(p.id, True); st.rerun()
                if b[1].button("Reject", key=f"rj-{p.id}"):
                    strategy_agent.decide(p.id, False); st.rerun()
    else:
        st.caption("No proposals waiting.")
    with st.expander("Decided proposals"):
        show(q("select created, title, status, decided_at from proposals where status <> 'pending' order by decided_at desc"))
    st.markdown("**Strategy library**")
    reg = q("select strategy, name, family, status, since, note from strategies order by family, strategy")
    show(reg, "Empty: run the strategy agent.")
    if has(reg):
        c = st.columns([2, 2, 1])
        sid = c[0].selectbox("Strategy", reg.strategy.tolist(), key="reg-sid")
        new_status = c[1].selectbox("Set status", ["paused", "active", "paper", "retired"], key="reg-st",
                                    help="Takes effect at the next run. Active means approved for real money.")
        if c[2].button("Apply", width="stretch"):
            strategy_agent.set_status(sid, new_status, "set by you in the dashboard"); st.rerun()


def parameters():
    cur = S.current()
    R = cur["RISK"]
    st.caption(f"Current version {C.VERSION}. Saving creates a new revision; the next agent run uses it. "
               "Changes to costs, liquidity or selection rules only affect strategy choice after you re-run the backtest.")
    with st.form("params"):
        st.markdown("**Risk agent**")
        c = st.columns(4)
        rpt = c[0].number_input("Risk per trade (% of equity)", 0.1, 5.0, float(R["risk_per_trade"] * 100), 0.1)
        mpos = c[1].number_input("Max open positions", 1, 50, int(R["max_positions"]))
        mpp = c[2].number_input("Max per stock (% of equity)", 1.0, 100.0, float(R["max_position_pct"] * 100), 1.0)
        mps = c[3].number_input("Max per sector", 1, 20, int(R["max_per_sector"]))
        c = st.columns(3)
        dlc = c[0].number_input("Daily loss cap (% of equity)", 0.5, 10.0, float(R["daily_loss_cap"] * 100), 0.5)
        ddh = c[1].number_input("Half size at drawdown (%)", 1.0, 50.0, float(R["dd_half"] * 100), 1.0)
        dds = c[2].number_input("Stop new trades at drawdown (%)", 1.0, 60.0, float(R["dd_stop"] * 100), 1.0)
        st.markdown("**Size multiplier by market state**")
        c = st.columns(4)
        sm = {k: c[i].number_input(STATE_LABEL[k], 0.0, 2.0, float(cur["STATE_MULT"][k]), 0.05) for i, k in enumerate(STATE_LABEL)}
        st.markdown("**Costs, universe and selection**")
        c = st.columns(5)
        cost = c[0].number_input("Round-trip cost (%)", 0.0, 2.0, float(cur["COST_RT"] * 100), 0.05)
        mto = c[1].number_input("Min avg traded value (₹ cr/day, 20 days)", 0.0, 1000.0, float(cur["MIN_TURNOVER_CR"]), 5.0)
        mpr = c[2].number_input("Min price (₹)", 0.0, 5000.0, float(cur["MIN_PRICE"]), 10.0)
        mts = c[3].number_input("Min trades per state", 10, 500, int(cur["MIN_TRADES_STATE"]), 10)
        mtt = c[4].number_input("Min t-stat", 0.0, 5.0, float(cur["MIN_T_STAT"]), 0.1)
        st.markdown("**Ask the system**")
        llm = st.text_input("Claude model", cur["LLM_MODEL"])
        note = st.text_input("Why are you changing this? (saved with the revision)")
        if st.form_submit_button("Save settings", type="primary"):
            rev = S.save(dict(
                RISK=dict(risk_per_trade=rpt / 100, max_positions=int(mpos), max_position_pct=mpp / 100, max_per_sector=int(mps),
                          daily_loss_cap=dlc / 100, dd_half=ddh / 100, dd_stop=dds / 100),
                STATE_MULT=sm, COST_RT=cost / 100, MIN_TURNOVER_CR=mto, MIN_PRICE=mpr, MIN_TRADES_STATE=int(mts),
                MIN_T_STAT=float(mtt), LLM_MODEL=llm), note)
            if rev == C.SETTINGS_REV:
                st.info("Nothing changed.")
            else:
                importlib.reload(C)
                st.success(f"Saved as revision {rev}. The next agent run uses it.")
    c = st.columns([1, 5])
    if c[0].button("Reset to defaults"):
        S.reset(); importlib.reload(C); st.rerun()
    st.markdown("**Change history**")
    show(S.history(), "No changes yet.")


def schedule_alerts():
    st.markdown("**After-close schedule**")
    if not schedule.supported():
        st.caption("Scheduling is available on macOS.")
    else:
        s = schedule.status()
        if s:
            st.success(f"Runs **fetch + all agents** on weekdays at {s['hour']:02d}:{s['minute']:02d}. If the Mac is asleep then, it runs when it wakes.")
        else:
            st.info("Not scheduled. The daily run only happens when you click it.")
        c = st.columns([1, 1, 1, 3])
        t = c[0].time_input("Time (IST)", value=pd.Timestamp(f"2026-01-01 {(s or {}).get('hour', 16):02d}:{(s or {}).get('minute', 20):02d}").time())
        if c[1].button("Save schedule", type="primary"):
            try:
                schedule.install(t.hour, t.minute); st.rerun()
            except Exception as e:
                st.error(str(e))
        if s and c[2].button("Remove schedule"):
            schedule.remove(); st.rerun()
        log = schedule.LOG
        if log.exists():
            with st.expander("Last scheduled run output"):
                st.code("\n".join(log.read_text(errors="ignore").splitlines()[-60:]), language=None)
    st.markdown("**Alerts after each daily run**")
    cur = S.current()["ALERTS"]
    c = st.columns(3)
    mac = c[0].toggle("macOS notification", value=bool(cur.get("macos", True)))
    tg = c[1].toggle("Telegram", value=bool(cur.get("telegram", False)),
                     help="Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in ~/.swing_env")
    if tg and not (secrets.has("TELEGRAM_BOT_TOKEN") and secrets.has("TELEGRAM_CHAT_ID")):
        c[1].caption("Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to ~/.swing_env first.")
    if c[2].button("Save alerts"):
        S.save(dict(ALERTS=dict(macos=mac, telegram=tg)), "alerts changed"); importlib.reload(C); st.success("Saved.")
    if st.button("Send a test alert"):
        sent = notify.send("Swing agents", "Test alert from the dashboard")
        st.write("Sent via: " + (", ".join(sent) or "nothing (check the toggles and keys)"))


def feeds():
    st.caption("RSS feeds for the Market page's news. Add, remove or edit rows, then save.")
    df = pd.DataFrame(S.current()["NEWS_FEEDS"])
    ed = st.data_editor(df, num_rows="dynamic", width="stretch", hide_index=True,
                        column_config={"kind": st.column_config.SelectboxColumn(options=["markets", "companies", "world"])})
    if st.button("Save feeds"):
        S.save(dict(NEWS_FEEDS=ed.dropna(subset=["url"]).to_dict("records")), "news feeds changed"); importlib.reload(C); st.success("Saved.")


def backups():
    st.caption("Backups hold the agents' database, your settings, positions, account and the Strategy Lab. "
               "One is made automatically each day before the agents run; the newest 20 are kept in the backups folder.")
    c = st.columns([1, 4])
    if c[0].button("Back up now", type="primary", width="stretch"):
        name = backup.backup("manual")
        st.success(f"Saved backups/{name}")
    bl = backup.list_backups()
    if not bl:
        st.caption("No backups yet.")
        return
    show(pd.DataFrame(bl))
    st.markdown("**Restore**")
    c = st.columns([3, 2, 1.2])
    name = c[0].selectbox("Backup", [b["name"] for b in bl], label_visibility="collapsed")
    sure = c[1].checkbox("Replace current data with this backup")
    if c[2].button("Restore", disabled=not sure or jobs.current() is not None, width="stretch"):
        try:
            safety = backup.restore(name)
            st.cache_data.clear()
            st.success(f"Restored {name}. What was there before is saved as backups/{safety}.")
        except Exception as e:
            st.error(str(e))


def page():
    st.title("Settings")
    t = st.tabs(["Approvals and strategies", "Parameters", "Schedule and alerts", "News feeds", "Backups", "AI models", "Appearance"])
    with t[0]:
        approvals()
    with t[1]:
        parameters()
    with t[2]:
        schedule_alerts()
    with t[3]:
        feeds()
    with t[4]:
        backups()
    with t[6]:
        appearance()
    with t[5]:
        from ui import ai_settings
        ai_settings.panel()


def appearance():
    from ui import theme
    on = st.toggle("Modern look (cards, soft shadows, gradients, transitions)", value=theme.modern(), key="ui-modern")
    if on != theme.modern():
        theme.set_modern(on)
        st.rerun()
    st.caption("Off = Streamlit's plain styling. Colours, fonts and rounded corners come from .streamlit/config.toml and stay "
               "either way. Only the presentation changes; data and agents are not affected.")
