"""'Ask the system': answers questions from what the agents stored, written up by the model set for 'ask'
(local by default, see core/llm.py); without a model it returns the matching records."""
import json, re
import pandas as pd
from . import config as C, db

SYSTEM = (
    "You are the assistant inside a personal NSE swing-trading research system. Answer ONLY from the DATA "
    "provided below, which comes from the system's own agents and backtests. If the data does not contain the "
    "answer, say so plainly and say which agent or run would produce it. Be concise and specific: quote numbers, "
    "dates and stock symbols from the data. Do not invent prices, news or results. You may explain the system's "
    "rules and reasoning, but do not give personal financial advice or tell the user to buy or sell; describe what "
    "the system's rules say instead. Write in plain language, short paragraphs or a small table.")


def _q(sql, params=None):
    r = db.read(sql, params)
    return r if r is not None else pd.DataFrame()


def symbols_in(question, universe):
    q = question.upper()
    found = [s for s in universe.Symbol if re.search(rf"\b{re.escape(s)}\b", q)]
    if not found:
        for r in universe.itertuples():
            name = str(r[1]).split(" ")[0]
            if len(name) >= 5 and re.search(rf"\b{re.escape(name.upper())}\b", q):
                found.append(r.Symbol)
    return found[:5]


def context(question, universe, prices=None):
    parts = {}
    reg = _q("select * from regime_daily order by date desc limit 5")
    parts["market_regime_last_days"] = reg.drop(columns=["run_id"], errors="ignore")
    day = reg.date.iat[0] if len(reg) else None
    parts["strategy_library"] = _q("select strategy, name, family, status, since, note from strategies")
    parts["pending_proposals"] = _q("select title, rationale, created from proposals where status = 'pending'")
    if day is not None:
        parts["signals_today_all_strategies"] = _q("select symbol, strategy, strategy_name, strategy_active, close, stop, target from signals where cast(date as date) = cast(? as date)", [day])
        parts["risk_decisions_today"] = _q("select symbol, strategy, approved, quantity, reject_reason, setup_score from decisions where cast(date as date) = cast(? as date)", [day])
        parts["momentum_portfolio_today"] = _q("select strategy, portfolio, symbol, weight_pct from portfolio_targets where cast(date as date) = cast(? as date)", [day])
    parts["paper_outcomes_summary"] = _q("select strategy, status, count(*) n, avg(ret_pct) avg_ret_pct from outcomes group by 1, 2")
    parts["backtest_by_strategy_and_state"] = _q("select strategy, state, trades, expectancy_pct, edge_pct, t_stat, active from strategy_cards")
    vj = C.RESULTS / "strategy" / "momentum_verdict.json"
    if vj.exists():
        parts["momentum_study_verdict"] = json.loads(vj.read_text())
    syms = symbols_in(question, universe)
    for s in syms:
        parts[f"{s}_signal_history"] = _q("select date, strategy, strategy_active, close from signals where symbol = ? order by date desc limit 20", [s])
        parts[f"{s}_decision_history"] = _q("select date, strategy, approved, quantity, reject_reason from decisions where symbol = ? order by date desc limit 20", [s])
        if prices is not None:
            p = prices[prices.symbol == s].sort_values("date").tail(260)
            if len(p):
                c = p.close
                parts[f"{s}_price_summary"] = dict(
                    last_date=str(p.date.iat[-1].date()), last_close=round(float(c.iat[-1]), 2),
                    ret_1m_pct=round(100 * (c.iat[-1] / c.iat[-22] - 1), 1) if len(c) > 22 else None,
                    ret_3m_pct=round(100 * (c.iat[-1] / c.iat[-64] - 1), 1) if len(c) > 64 else None,
                    ret_1y_pct=round(100 * (c.iat[-1] / c.iat[0] - 1), 1),
                    high_52w=round(float(p.high.max()), 2), low_52w=round(float(p.low.min()), 2),
                    above_200d=bool(c.iat[-1] > c.tail(200).mean()))
        sec = universe[universe.Symbol == s]
        if len(sec):
            parts[f"{s}_company"] = dict(name=sec.iloc[0, 0], sector=sec.Industry.iat[0])
    return parts, syms


def _render(parts):
    out = []
    for k, v in parts.items():
        if isinstance(v, pd.DataFrame):
            out.append(f"## {k}\n" + (v.head(60).to_csv(index=False) if len(v) else "(none)\n"))
        else:
            out.append(f"## {k}\n{json.dumps(v, default=str)}\n")
    return "\n".join(out)


def answer(question, history, universe, prices=None, use_docs=False):
    """Returns (text, mode, parts, doc_hits). mode = 'model' or 'lookup' (no model available).
    The model is the one set for 'ask' in Settings → AI models (local Ollama by default)."""
    from . import llm
    parts, syms = context(question, universe, prices)
    hits = None
    if use_docs:
        try:
            from . import library
            if library.options()["enabled"]:
                hits = library.search(question, k=6)
        except Exception:
            hits = None
    ok, _ = llm.status("ask")
    if not ok:
        return None, "lookup", parts, hits
    msgs = [{"role": m["role"], "content": m["content"]} for m in history[-6:] if m["role"] in ("user", "assistant")]
    docs = ""
    if hits is not None and len(hits):
        from . import library
        docs = ("\n\nPASSAGES FROM THE USER'S DOCUMENTS (cite as [D1], [D2]...; they are background, the DATA above is the system's record):\n"
                + library.passages_text(hits, 1200, prefix="D"))
    msgs.append({"role": "user", "content": f"DATA (as stored by the agents):\n{_render(parts)}{docs}\n\nQUESTION: {question}"})
    return llm.chat("ask", msgs, system=SYSTEM, max_tokens=1200), "model", parts, hits
