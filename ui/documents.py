"""Documents: your own library of files, web pages, sites and news articles that the AI models answer from."""
import html
from ui.common import *  # noqa: F401,F403
from ui.ai_settings import ai_packages_ok
from core import llm

KIND_LABEL = {"file": "File", "web": "Web page", "site": "Website page", "news": "News article"}


def page():
    st.title("Documents")
    if not ai_packages_ok():
        st.info("The documents library needs a few Python packages first (about 200 MB, once).")
        if st.button("Install document library packages", type="primary", disabled=jobs.current() is not None):
            launch("install-ai")
        job_panel("doc-inst")
        return
    from core import library as L
    o = L.options()
    c = st.columns([1.4, 4])
    on = c[0].toggle("Use my documents", value=o["enabled"], help="Off: the library is ignored everywhere (here and in Ask the system).")
    if on != o["enabled"]:
        L.save_options({"enabled": on})
        st.rerun()
    s = L.stats()
    c[1].caption(f"{s['sources']} sources · {s['passages']:,} passages · " + " · ".join(f"{KIND_LABEL.get(k, k)} {v}" for k, v in s["by_kind"].items())
                 + f" · answers by {llm.status('docs')[1]}")
    emb = llm.settings()["embed_model"]
    try:
        models = llm.ollama_models() or []
    except llm.ModelError:
        models = []
    if not any(m.split(":")[0] == emb.split(":")[0] for m in models):
        st.warning(f"The search model isn't available: open Ollama and run `ollama pull {emb}` in Terminal (about 270 MB).")
    t = st.tabs(["💬 Ask", "➕ Add", "📚 Library", "📰 News articles"])
    with t[0]:
        ask_tab(L, on)
    with t[1]:
        add_tab(L, o)
    with t[2]:
        library_tab(L)
    with t[3]:
        articles_tab()


def ask_tab(L, on):
    if not on:
        st.info("The library is switched off. Turn on **Use my documents** above.")
        return
    reg = L.registry()
    c = st.columns([2, 3, 1.4])
    kinds = c[0].multiselect("Only these kinds", list(KIND_LABEL), format_func=KIND_LABEL.get, key="doc-kinds")
    srcs = c[1].multiselect("Only these sources", list(reg.source_id), format_func=lambda x: reg.set_index("source_id").title.get(x, x)[:70], key="doc-srcs")
    sym = c[2].text_input("Only passages naming", key="doc-sym", placeholder="e.g. HAL").strip().upper() or None
    if "doc_chat" not in st.session_state:
        st.session_state.doc_chat = []
    for m in st.session_state.doc_chat:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            if m.get("hits") is not None:
                _sources(m["hits"])
    q = st.chat_input("Ask about your documents, e.g. 'What order inflow did HAL guide for FY27?'")
    if q:
        st.session_state.doc_chat.append({"role": "user", "content": q})
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            with st.spinner("Searching your documents..."):
                try:
                    text, hits = L.answer(q, st.session_state.doc_chat[:-1], kinds=kinds or None, source_ids=srcs or None, symbol=sym)
                except Exception as e:
                    text, hits = f"Couldn't answer: {e}", None
            st.markdown(text)
            if hits is not None and len(hits):
                _sources(hits)
        st.session_state.doc_chat.append({"role": "assistant", "content": text, "hits": hits})
    if st.session_state.doc_chat and st.button("Clear conversation", key="doc-clear"):
        st.session_state.doc_chat = []
        st.rerun()


def _sources(hits):
    with st.expander(f"Sources ({len(hits)} passages)"):
        for i, r in enumerate(hits.itertuples(), 1):
            loc = r.location if str(r.location).startswith("http") else ""
            st.markdown(f"**[{i}] {html.escape(str(r.title))}**" + (f" · {html.escape(str(r.where))}" if r.where else "") + (f" · {r.published[:10]}" if r.published else "")
                        + (f" · [open]({loc})" if loc else "")
                        + f"  \n<small>{'meaning' if r.in_vector else ''}{' + ' if r.in_vector and r.in_keywords else ''}{'keywords' if r.in_keywords else ''} match</small>",
                        unsafe_allow_html=True)
            st.caption(r.text[:600] + ("…" if len(r.text) > 600 else ""))


def _ingest(L):
    """Start the queue now, or leave it queued if another history-lane job is running."""
    if jobs.busy("library-ingest"):
        cur = jobs.current("library-ingest")
        if cur and cur["name"] == "library-ingest":
            st.success("Added: the running library job picks it up.")
        else:
            st.info(f"Added to the queue. It starts when '{cur['name'] if cur else 'the current job'}' finishes: press Process queue then.")
    else:
        launch("library-ingest")


def add_tab(L, o):
    busy = False                                   # adding to the queue is always allowed
    st.caption("Everything is read and indexed on this Mac. Large folders and websites run in the background; watch **Job output**.")
    a, b = st.columns(2)
    with a:
        with st.container(border=True):
            st.markdown("##### Files")
            up = st.file_uploader("Drop files: PDF (with selectable text), Word (.docx, .doc), .txt, .md, .html", accept_multiple_files=True,
                                  type=["pdf", "docx", "doc", "txt", "md", "html", "htm", "rtf"], key="doc-up")
            if st.button("Add files", disabled=busy or not up, width="stretch"):
                for f in up:
                    L.enqueue("file", L.save_upload(f.name, f.getvalue()))
                _ingest(L)
        with st.container(border=True):
            st.markdown("##### A folder on this Mac")
            folder = st.text_input("Folder path", placeholder="/Users/you/Documents/Research", key="doc-folder",
                                   help="Every readable file inside, including subfolders. The files stay where they are.")
            if st.button("Add folder", disabled=busy or not folder.strip(), width="stretch"):
                L.enqueue("folder", folder.strip())
                _ingest(L)
    with b:
        with st.container(border=True):
            st.markdown("##### Web pages")
            urls = st.text_area("One link per line (just that page)", key="doc-urls", height=100)
            if st.button("Add pages", disabled=busy or not urls.strip(), width="stretch"):
                for u in [x.strip() for x in urls.splitlines() if x.strip().startswith("http")]:
                    L.enqueue("url", u)
                _ingest(L)
        with st.container(border=True):
            st.markdown("##### A whole website (or section)")
            site = st.text_input("Start page", placeholder="https://www.company.com/investors/", key="doc-site")
            c = st.columns(2)
            lim = c[0].number_input("Page limit", 1, 5000, int(o["site_page_limit"]), 10, key="doc-lim",
                                    help="Pages read per crawl. ~2–3 seconds per page; robots.txt is obeyed.")
            same = c[1].toggle("Stay in this section", value=True, key="doc-same", help="Only pages under the start page's folder, e.g. /investors/")
            if st.button("Crawl website", disabled=busy or not site.strip().startswith("http"), width="stretch"):
                L.enqueue("site", site.strip(), limit=int(lim), same_path=bool(same))
                L.save_options({"site_page_limit": int(lim)})
                _ingest(L)
    q = L.queued()
    lane_busy = jobs.busy("library-ingest")
    if q:
        st.caption(f"Waiting in the queue: {len(q)} task(s)" + ("" if lane_busy else " — press below to start"))
        if not lane_busy and st.button("Process queue"):
            launch("library-ingest")
    with st.expander("Job output", expanded=lane_busy):
        job_panel("doc")


def library_tab(L):
    reg = L.registry()
    if reg.empty:
        st.caption("Nothing added yet.")
        return
    v = reg.sort_values("updated", ascending=False).copy()
    v["kind"] = v.kind.map(lambda k: KIND_LABEL.get(k, k))
    v["enabled"] = v.enabled.fillna(True).astype(bool)
    f = st.columns([2, 3])
    kinds = f[0].multiselect("Kind", sorted(v.kind.unique()), key="lib-k")
    qq = f[1].text_input("Search titles / links", key="lib-q")
    if kinds:
        v = v[v.kind.isin(kinds)]
    if qq:
        v = v[(v.title.fillna("") + " " + v.location.fillna("")).str.contains(qq, case=False, regex=False)]
    ed = st.data_editor(v[["enabled", "title", "kind", "chunks", "pages", "status", "note", "updated", "location", "source_id"]], hide_index=True,
                        width="stretch", height=min(600, 40 + 35 * len(v)), key="lib-ed",
                        disabled=["title", "kind", "chunks", "pages", "status", "note", "updated", "location", "source_id"],
                        column_config={"enabled": st.column_config.CheckboxColumn("Use", help="Untick to keep it but leave it out of answers"),
                                       "chunks": st.column_config.NumberColumn("Passages"), "source_id": None,
                                       "location": st.column_config.TextColumn("File / link", width="medium")})
    changed = ed[ed.enabled != v.enabled.values]
    for r in changed.itertuples():
        L.set_enabled(r.source_id, r.enabled)
    if len(changed):
        st.rerun()
    c = st.columns([3, 1, 1])
    pick = c[0].selectbox("Source", list(v.source_id), format_func=lambda x: reg.set_index("source_id").title.get(x, x)[:90], key="lib-pick")
    busy = jobs.busy("library-ingest")
    if c[1].button("Summarise", disabled=busy or not pick, width="stretch", help="Reads the whole source in parts and combines them (a few minutes for long reports)."):
        L.enqueue("summary", pick)
        launch("library-ingest")
    if c[2].button("Remove", disabled=busy or not pick, width="stretch", help="Deletes its passages (and the copy of a dropped file). Your own folders are never touched."):
        L.remove(pick)
        st.rerun()
    sp = L.SUMMARIES / f"{pick}.md" if pick else None
    if sp is not None and sp.exists():
        with st.container(border=True):
            st.markdown("##### Summary")
            st.markdown(sp.read_text().replace("$", "\\$"))


def articles_tab():
    from core import articles as A
    st.markdown("Reads the full text of stored news headlines where the site allows it, adds it to the library (kind **News article**) and "
                "re-links each headline to the stocks the article is actually about.")
    st.caption("robots.txt is checked for every page; one request per site every ~12 seconds; paywalled articles are recorded, never bypassed; "
               "Google News links aren't followed. Expect full text for roughly a third to half of headlines.")
    s = A.summary()
    if s:
        lab = {"ok": "full text", "paywall": "paywalled", "blocked": "not allowed (robots.txt)", "short": "too short", "error": "failed", "redirect": "Google link"}
        st.markdown(" · ".join(f"**{v}** {lab.get(k, k)}" for k, v in s.items()))
    c = st.columns([1, 1, 3])
    n = c[0].selectbox("Articles per run", [100, 300, 1000], index=1, key="art-n", help="300 ≈ 30–60 minutes")
    if c[1].button("Read article text", type="primary", disabled=jobs.busy("news-articles"), width="stretch"):
        launch("news-articles", [f"max={n}"])
    with st.expander("Job output", expanded=jobs.busy("news-articles")):
        job_panel("art")
