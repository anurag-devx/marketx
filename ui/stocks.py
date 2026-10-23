"""Stock lookup: chart, key numbers, what every agent says about the stock, history and news."""
import math
from plotly.subplots import make_subplots
from ui.common import *  # noqa: F401,F403
from core.indicators import rsi
from core.strategies import levels, signals as compute_signals
from ui import stock_news


@st.cache_data(ttl=3600)
def momentum_ranks(mtime):
    """6-month return skipping the latest month (the approved momentum rule's ranking), percentile among liquid stocks."""
    feat, _ = feats()
    f = feat[["symbol", "date", "close", "turnover_cr"]].copy()
    f["month"] = f.date.dt.to_period("M")
    me = f.groupby(["symbol", "month"]).agg(close=("close", "last"), turnover=("turnover_cr", "last")).reset_index()
    close = me.pivot(index="month", columns="symbol", values="close").sort_index()
    turn = me.pivot(index="month", columns="symbol", values="turnover").sort_index()
    i = len(close) - 1
    mom = close.iloc[i - 1] / close.iloc[i - 7] - 1
    ok = (turn.iloc[i] >= C.MIN_TURNOVER_CR) & mom.notna()
    r = mom[ok].rank(ascending=False)
    return pd.DataFrame({"mom6_1_pct": 100 * mom[ok], "rank": r, "of": int(ok.sum())})


def page():
    st.title("Stock lookup")
    uni = universe()
    feat, mkt = feats()
    if feat is None:
        st.info("No price data yet. Fetch data from the sidebar.")
        return
    names = dict(zip(uni.Symbol, uni["Company Name"]))
    syms = sorted(feat.symbol.unique())
    default = st.session_state.get("stock", "RELIANCE" if "RELIANCE" in syms else syms[0])
    c = st.columns([3, 2])
    sym = c[0].selectbox("Stock", syms, index=syms.index(default) if default in syms else 0,
                         format_func=lambda s: f"{s} — {names.get(s, '')}")
    st.session_state["stock"] = sym
    span = c[1].radio("Chart range", ["3M", "6M", "1Y", "3Y", "Max"], index=2, horizontal=True)

    f = feat[feat.symbol == sym].sort_values("date").copy()
    if f.empty:
        st.warning("No data for this stock.")
        return
    f = compute_signals(f)
    last = f.iloc[-1]
    sector = last.sector
    ret = lambda n: 100 * (f.close.iat[-1] / f.close.iat[-1 - n] - 1) if len(f) > n else np.nan
    hi52, lo52 = f.high.tail(252).max(), f.low.tail(252).min()
    st.caption(f"{names.get(sym, '')} · {sector} · last bar {last.date.date()}")
    m = st.columns(4)
    bar = pd.Timestamp(last.date)
    is_today = bar.normalize() == pd.Timestamp.now().normalize()
    day_lab = "today" if is_today else f"on {bar:%a %d %b}"
    m[0].metric(f"Close {'today' if is_today else f'({bar:%a %d %b})'}", f"₹{last.close:,.0f}" if last.close >= 1000 else f"₹{last.close:,.2f}",
                f"{ret(1):+.2f}% {day_lab}", help="Last available daily close. Prices update after the market closes and you fetch data.")
    m[1].metric("1 month", pct(ret(21), 1))
    m[2].metric("3 months", pct(ret(63), 1))
    m[3].metric("1 year", pct(ret(252), 1))
    m = st.columns(4)
    m[0].metric("From 52-week high", f"{100 * (last.close / hi52 - 1):+.1f}%")
    m[1].metric("Daily range (ATR)", f"{100 * last.atr / last.close:.1f}%")
    day_val = last.close * last.volume / 1e7
    m[2].metric("Avg traded value (20 days)", f"₹{last.turnover_cr:,.0f} cr",
                help="Average of close × shares traded over the last 20 sessions: how much money changes hands in this stock on a normal day.")
    m[2].caption(f"₹{day_val:,.0f} cr traded {day_lab}")
    m[3].metric("Strength rank", f"{100 * last.rs_pct:.0f} / 100" if pd.notna(last.rs_pct) else "—",
                help="Share of liquid stocks it beats on 3-month return vs Nifty")

    # ---------- chart ----------
    days = {"3M": 63, "6M": 126, "1Y": 252, "3Y": 756, "Max": len(f)}[span]
    v = f.tail(days).copy()
    v["rsi14"] = rsi(f.close, 14).tail(days)
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.62, 0.18, 0.2], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(x=v.date, open=v.open, high=v.high, low=v.low, close=v.close, name=sym,
                                 increasing_line_color=GOOD, decreasing_line_color=BAD, showlegend=False), row=1, col=1)
    for col, colr, w in (("sma20", "#9b8bd0", 1), ("sma50", "#e0a43a", 1.3), ("sma200", ACCENT, 1.6)):
        fig.add_trace(go.Scatter(x=v.date, y=v[col], name=col.upper().replace("SMA", "") + "-day avg", line=dict(color=colr, width=w)), row=1, col=1)
    marks = []
    for sid in STRATEGIES:
        hit = v[v[f"sig_{sid}"]]
        if len(hit):
            marks.append((sid, hit))
    if marks:
        for sid, hit in marks:
            fig.add_trace(go.Scatter(x=hit.date, y=hit.low * 0.985, mode="markers", name=f"{sid} signal",
                                     marker=dict(symbol="triangle-up", size=9), hovertemplate=f"{sid}: %{{x|%d %b %Y}}<extra></extra>"), row=1, col=1)
    fig.add_trace(go.Bar(x=v.date, y=v.volume, marker_color=MUTED, name="Volume", showlegend=False), row=2, col=1)
    fig.add_trace(go.Scatter(x=v.date, y=v.rsi14, line=dict(color=ACCENT, width=1.3), name="RSI 14", showlegend=False), row=3, col=1)
    for lvl in (30, 70):
        fig.add_hline(y=lvl, line_dash="dot", line_color=REF, row=3, col=1)
    ev = stock_news.stock_events(sym, f)
    stock_news.add_markers(fig, ev, v)
    fig.update_layout(dragmode="zoom", xaxis_rangeslider_visible=False, height=660, margin=dict(l=8, r=8, t=56, b=8),
                      legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0, font=dict(size=11)), hovermode="x unified")
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    fig.update_yaxes(title_text="Price ₹", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    fig.update_yaxes(title_text="RSI", range=[0, 100], row=3, col=1)
    chosen = st.plotly_chart(fig, width="stretch", key=f"chart-{sym}-{span}", on_select="rerun", selection_mode="box",
                             config={"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "autoScale2d"]})
    st.caption("Triangles: days a swing setup fired. Blue circles: news; orange diamonds: NSE filings (hover for headlines). "
               "To list news for a period, pick **Box select** in the chart's toolbar and drag across the dates.")
    selected = _box_range(chosen)
    stock_news.section(sym, f, v, ev, selected)

    # ---------- what the agents say ----------
    st.subheader("What the agents say")
    reg = q("select * from regime_daily order by date desc limit 1")
    a = st.columns(2)
    with a[0]:
        st.markdown("**Market regime**")
        if has(reg):
            r = reg.iloc[0]
            st.write(f"{STATE_LABEL.get(r.market_state, r.market_state)} · active swing strategies: {r.active_strategies or 'none'}")
        st.markdown(f"**Technical and strategy agents: setups firing on the latest bar ({pd.Timestamp(last.date):%a %d %b})**")
        today = f.tail(1)
        active = set((reg.active_strategies.iat[0] or "").split(",")) if has(reg) else set()
        rows = []
        acc = account()
        mult = float(reg.size_multiplier.iat[0]) if has(reg) else 0.0
        for sid in STRATEGIES:
            if bool(today[f"sig_{sid}"].iat[0]):
                stop, target = levels(sid, float(last.close), today.iloc[0])
                per = last.close - stop
                qty_full = math.floor(acc["equity"] * C.RISK["risk_per_trade"] / per) if per > 0 else 0
                qty_full = min(qty_full, math.floor(acc["equity"] * C.RISK["max_position_pct"] / last.close))
                rows.append(dict(strategy=f"{sid} {STRATEGIES[sid]['name']}", switched_on="yes" if sid in active else "no (no tested edge now)",
                                 stop=round(stop, 2), target=None if not np.isfinite(target) else round(target, 2),
                                 size_at_full_risk=qty_full, size_at_current_multiplier=math.floor(qty_full * mult) if sid in active else 0))
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        else:
            st.caption("No setup fires on this stock on the latest bar.")
    with a[1]:
        st.markdown("**Strategy agent: momentum**")
        mr = momentum_ranks(C.DATA.joinpath("prices.parquet").stat().st_mtime)
        if sym in mr.index:
            row = mr.loc[sym]
            st.write(f"6-month return (skipping the latest month): **{row.mom6_1_pct:+.1f}%**, rank **{int(row['rank'])}** of {int(row['of'])} liquid stocks. The momentum rule buys the top 20.")
        else:
            st.write("Not ranked (not liquid enough or too little history).")
        tg = q("select portfolio, weight_pct from portfolio_targets where symbol = ? and cast(date as date) = (select max(cast(date as date)) from portfolio_targets)", [sym])
        if has(tg):
            st.write("In the momentum portfolio: " + ", ".join(f"{r.portfolio} ({r.weight_pct:.1f}%)" for r in tg.itertuples()))
        st.markdown("**Risk agent: your position**")
        pos = pd.read_csv(C.POSITIONS_CSV) if C.POSITIONS_CSV.exists() else pd.DataFrame()
        if len(pos) and sym in set(pos.symbol):
            p = pos[pos.symbol == sym].iloc[0]
            st.write(f"Held: {int(p.quantity)} at ₹{p.entry_price} · P&L {100 * (last.close / p.entry_price - 1):+.1f}% · stop ₹{p.stop}")
        else:
            st.caption("Not held.")
        st.markdown("**Strength**")
        st.write(f"Beats {100 * last.rs_pct:.0f}% of liquid stocks on 3-month return vs Nifty; rank {int(last.rank_in_sector) if pd.notna(last.rank_in_sector) else '—'} in its sector on 1-month return.")

    # ---------- history ----------
    st.subheader("History")
    h = st.columns(2)
    with h[0]:
        st.markdown("**Decisions the agents made**")
        dh = q("select cast(s.date as date) date, s.strategy, s.strategy_active, d.approved, d.quantity, d.reject_reason from signals s "
               "left join decisions d on d.symbol = s.symbol and d.strategy = s.strategy and cast(d.date as date) = cast(s.date as date) "
               "where s.symbol = ? order by s.date desc limit 50", [sym])
        show(dh, "No signals recorded for this stock since the agents started running.")
    with h[1]:
        st.markdown("**Backtest trades on this stock (2016 – Oct 2026)**")
        tp = C.RESULTS / "trades.csv"
        if tp.exists():
            tr = pd.read_csv(tp, usecols=["strategy", "symbol", "ret", "entry_date"])
            t = tr[tr.symbol == sym]
            if len(t):
                s = t.groupby("strategy").ret.agg(trades="size", win_pct=lambda r: round(100 * (r > 0).mean(), 1), avg_net_pct=lambda r: round(100 * r.mean(), 2)).reset_index()
                show(s)
            else:
                st.caption("No backtest trades on this stock.")



def _box_range(event):
    """(start, end) dates from a box drawn on the chart, else None."""
    try:
        boxes = event["selection"]["box"]
        if boxes:
            xs = pd.to_datetime(boxes[0]["x"])
            return xs.min().normalize(), xs.max().normalize()
        pts = event["selection"]["points"]
        if pts:
            xs = pd.to_datetime([p["x"] for p in pts])
            return xs.min().normalize(), xs.max().normalize()
    except Exception:
        pass
    return None
