"""Settings → AI models: which model each feature uses (local by default), online slots, document-library packages."""
import importlib.util
from ui.common import *  # noqa: F401,F403
from core import llm, secrets, settings as S


def _has(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):          # parent package missing (e.g. llama_index for llama_index.core)
        return False


PACKAGES = {"llama_index.core": "LlamaIndex", "llama_index.embeddings.ollama": "LlamaIndex Ollama embeddings", "lancedb": "LanceDB",
            "trafilatura": "trafilatura", "pypdf": "pypdf", "docx": "python-docx", "xlrd": "xlrd (geopolitical risk index)"}


def missing_packages():
    return [label for mod, label in PACKAGES.items() if not _has(mod)]


def ai_packages_ok():
    """Documents library usable (xlrd is only for the macro GPR series, so it doesn't block the library)."""
    return all(_has(m) for m in PACKAGES if m != "xlrd")


def panel():
    s = llm.settings()
    try:
        models = llm.ollama_models(s)
    except llm.ModelError:
        models = None
    if models is None:
        st.warning("Ollama isn't running on this Mac. Install it from ollama.com and open it; local models are the default for every feature.")
        models = []
    else:
        st.success(f"Ollama running · installed: {', '.join(models) or 'no models yet'}")
    st.caption("Local models run on this Mac: questions, your records and your documents never leave it. "
               "Online models are optional and stay off until you switch them on below.")

    st.markdown("##### Online models")
    c = st.columns([1.4, 3])
    online = c[0].toggle("Allow online models", value=s["online_enabled"])
    if online:
        c[1].warning("When a feature uses an online model, its data (your question, the agents' records or document passages) is sent to that company.")
    k = st.columns(3)
    om = dict(s["online_models"])
    for col, prov in zip(k, ("anthropic", "openai", "gemini")):
        with col:
            have = secrets.has(llm.KEY_NAME[prov])
            st.markdown(f"**{llm.PROVIDERS[prov].split(' (')[0]}** · key {'✅ found' if have else '— missing'}")
            om[prov] = st.text_input("Model name", om.get(prov, ""), key=f"om-{prov}",
                                     help=f"Exact model name from the provider's documentation. Key goes in ~/.swing_env as {llm.KEY_NAME[prov]}=...")
    st.caption("Keys live only in ~/.swing_env on this Mac (outside the project). The app reads them when it makes a request and never shows, logs or saves them.")

    st.markdown("##### Model per feature")
    feats_new = {}
    provs = ["ollama"] + (["anthropic", "openai", "gemini"] if online else [])
    for f, label in llm.FEATURES.items():
        cur = s["features"][f]
        c = st.columns([1.3, 1.6, 2.2, 1.6])
        c[0].markdown(f"**{label}**")
        p = c[1].selectbox("Provider", provs, index=provs.index(cur["provider"]) if cur["provider"] in provs else 0,
                           format_func=lambda x: llm.PROVIDERS[x], key=f"pv-{f}", label_visibility="collapsed")
        if p == "ollama":
            opts = [m for m in dict.fromkeys(models + ([cur["model"]] if cur["provider"] == "ollama" else [])) if m]
            m = c[2].selectbox("Model", opts or ["gemma4:e4b"], index=(opts.index(cur["model"]) if cur["model"] in opts else 0),
                               key=f"md-{f}", label_visibility="collapsed", accept_new_options=True)
        else:
            m = c[2].text_input("Model", om.get(p, ""), key=f"mdo-{f}", label_visibility="collapsed", disabled=True)
        feats_new[f] = {"provider": p, "model": m if p == "ollama" else ""}
        ok, msg = llm.status(f)
        c[3].caption(("🟢 " if ok else "🟡 ") + msg)
    emb = st.text_input("Embedding model (documents library, always local)", s["embed_model"],
                        help="Turns passages into vectors for search. Pull it once: ollama pull nomic-embed-text")
    if st.button("Save AI settings", type="primary"):
        S.save({"AI": {"ollama_url": s["ollama_url"], "embed_model": emb.strip(), "online_enabled": online,
                       "online_models": om, "features": feats_new, "temperature": s["temperature"], "timeout": s["timeout"]}}, "AI models")
        st.success("Saved.")
        st.rerun()

    st.markdown("##### Extra packages (documents library and macro data)")
    miss = missing_packages()
    if not miss:
        st.caption("Installed: " + ", ".join(PACKAGES.values()) + ".")
    else:
        st.info("Missing: **" + ", ".join(miss) + "**. Install once (about 200 MB if nothing is installed yet).")
        if st.button("Install missing packages", disabled=jobs.current() is not None):
            launch("install-ai")
    st.markdown("**First-time model downloads** (Terminal):  \n`ollama pull gemma4:e4b`  ·  `ollama pull qwen3.5:9b`  ·  `ollama pull nomic-embed-text`  \n"
                "Use the exact names shown on ollama.com/library.")
