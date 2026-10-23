"""Ask the system: questions answered from what the agents stored."""
from ui.common import *  # noqa: F401,F403
from core import ask as askmod, llm

EXAMPLES = ["Why were there no trades today?", "What is the momentum portfolio holding and why?",
            "Which strategies work in the current market state?", "What did the agents say about TECHM?"]


def lookup_answer(question, parts):
    """No API key: show the records that answer the question."""
    syms = [k.split("_")[0] for k in parts if k.endswith("_signal_history")]
    reg = parts.get("market_regime_last_days")
    out = []
    if reg is not None and len(reg):
        r = reg.iloc[0]
        out.append(f"Market state on {pd.Timestamp(r.date).date()}: **{STATE_LABEL.get(r.market_state, r.market_state)}**. "
                   f"Active swing strategies: {r.active_strategies or 'none'}." + (f" {r.note}" if r.note else ""))
    for s in syms:
        sig = parts.get(f"{s}_signal_history")
        dec = parts.get(f"{s}_decision_history")
        if sig is not None and len(sig):
            last = sig.iloc[0]
            out.append(f"**{s}**: last signal {pd.Timestamp(last.date).date()} from {last.strategy} "
                       f"({'switched on' if last.strategy_active else 'strategy switched off'}).")
        else:
            out.append(f"**{s}**: no signal recorded since the agents started running.")
        if dec is not None and len(dec):
            d = dec.iloc[0]
            out.append(f"Last risk decision: {'approved, ' + str(int(d.quantity)) + ' shares' if d.approved else 'rejected: ' + str(d.reject_reason)}.")
    return "\n\n".join(out) or "I found no stored records that match."


def page():
    st.title("Ask the system")
    ok, msg = llm.status("ask")
    c = st.columns([4, 1.6])
    if ok:
        c[0].caption(f"Answers come from the agents' stored data, written up by **{msg}**"
                     + (" (online: your question and the records are sent to that company)." if llm.is_online("ask") else ". Nothing leaves this Mac.")
                     + " The data used is shown under each answer.")
    else:
        c[0].info(f"No model available: {msg} Until then, I answer with the matching records. Choose models in Settings → AI models.")
    use_docs = c[1].toggle("Use my documents", value=False, help="Also search your Documents library and cite passages as [D1], [D2]...")
    if "chat" not in st.session_state:
        st.session_state.chat = []
    cols = st.columns(len(EXAMPLES))
    clicked = None
    for c, e in zip(cols, EXAMPLES):
        if c.button(e, width="stretch"):
            clicked = e
    for m in st.session_state.chat:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
    prompt = st.chat_input("Ask about today's signals, a stock, a strategy, a decision...") or clicked
    if prompt:
        st.session_state.chat.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Reading the agents' records..."):
                try:
                    text, mode, parts, hits = askmod.answer(prompt, st.session_state.chat[:-1], universe(), prices(), use_docs=use_docs)
                    if mode == "lookup":
                        text = lookup_answer(prompt, parts)
                except Exception as e:
                    text, parts, hits = f"Couldn't get an answer: {e}", {}, None
            st.markdown(text)
            if hits is not None and len(hits):
                with st.expander(f"Document passages ({len(hits)})"):
                    for i, r in enumerate(hits.itertuples(), 1):
                        st.markdown(f"**[D{i}] {r.title}**" + (f" · {r.where}" if r.where else ""))
                        st.caption(r.text[:500])
            with st.expander("Data used"):
                for k, v in parts.items():
                    st.markdown(f"**{k.replace('_', ' ')}**")
                    if isinstance(v, pd.DataFrame):
                        show(v.head(50), "none")
                    else:
                        st.json(v)
        st.session_state.chat.append({"role": "assistant", "content": text})
    if st.session_state.chat and st.button("Clear conversation"):
        st.session_state.chat = []
        st.rerun()
