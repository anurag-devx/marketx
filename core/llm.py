"""One place for every AI model the app uses.

Local (default): Ollama on this Mac (http://localhost:11434). Nothing leaves the Mac, no key.
Online (optional, off until you switch it on): Claude (Anthropic), GPT (OpenAI), Gemini (Google). Each needs its key
in ~/.swing_env (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY) AND the master switch 'online_enabled'.
Choosing an online model for a feature sends that feature's data (question, records, document passages) to that
company. Keys are read only when a request is made; never printed, logged or saved elsewhere.

Features: 'ask' (Ask the system), 'docs' (Documents), 'report' (Market report), 'search' (Deep search). Each has its own provider + model."""
import re
from . import config as C, secrets

PROVIDERS = {"ollama": "Local (Ollama, on this Mac)", "anthropic": "Claude (Anthropic, online)",
             "openai": "GPT (OpenAI, online)", "gemini": "Gemini (Google, online)"}
KEY_NAME = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}
FEATURES = {"ask": "Ask the system", "docs": "Documents", "report": "Market report", "search": "Deep search",
            "debate": "Market Debate Lab"}
CTX = {"ask": 16384, "docs": 16384, "report": 8192, "search": 16384, "debate": 16384}
DEFAULT = {
    "ollama_url": "http://localhost:11434",
    "embed_model": "nomic-embed-text",
    "online_enabled": False,
    "online_models": {"anthropic": "claude-sonnet-5-5", "openai": "", "gemini": ""},
    "features": {"ask": {"provider": "ollama", "model": "gemma4:e4b"},
                 "docs": {"provider": "ollama", "model": "gemma4:e4b"},
                 "report": {"provider": "ollama", "model": "qwen3.5:9b"},
                 "search": {"provider": "ollama", "model": "qwen3.5:9b"},
                 "debate": {"provider": "ollama", "model": "qwen3.5:9b"}},
    "temperature": 0.2,
    "timeout": 300,
}


class ModelError(RuntimeError):
    pass


def settings():
    s = getattr(C, "AI", None) or {}
    out = {**DEFAULT, **{k: v for k, v in s.items() if k not in ("features", "online_models")}}
    out["online_models"] = {**DEFAULT["online_models"], **(s.get("online_models") or {})}
    out["features"] = {k: {**DEFAULT["features"][k], **((s.get("features") or {}).get(k) or {})} for k in DEFAULT["features"]}
    return out


def _local_url(url):
    from urllib.parse import urlparse
    if (urlparse(url).hostname or "") not in ("localhost", "127.0.0.1", "::1"):
        raise ModelError("The local model address must be on this Mac (localhost).")
    return url.rstrip("/")


def _redact(text):
    return re.sub(r"(sk-[A-Za-z0-9_\-]{6})[A-Za-z0-9_\-]+|(AIza[0-9A-Za-z_\-]{4})[0-9A-Za-z_\-]+", lambda m: (m.group(1) or m.group(2)) + "…", str(text))


def ollama_models(s=None):
    """Installed local models, or None if Ollama isn't running."""
    import requests
    s = s or settings()
    try:
        r = requests.get(_local_url(s["ollama_url"]) + "/api/tags", timeout=4)
        r.raise_for_status()
        return [m.get("name", "") for m in r.json().get("models", [])]
    except ModelError:
        raise
    except Exception:
        return None


def _has_model(want, models):
    return any(m == want or m == f"{want}:latest" or m.split(":")[0] == want for m in models)


def resolve(feature):
    s = settings()
    f = s["features"][feature]
    prov, model = f["provider"], f["model"]
    if prov != "ollama":
        model = f.get("model") or s["online_models"].get(prov, "")
    return s, prov, model


def status(feature):
    """(ok, message) for the model a feature would use right now."""
    s, prov, model = resolve(feature)
    if prov == "ollama":
        try:
            models = ollama_models(s)
        except ModelError as e:
            return False, str(e)
        if models is None:
            return False, "Ollama is not running on this Mac (open the Ollama app)."
        if not _has_model(model, models):
            return False, f"Local model '{model}' isn't downloaded. In Terminal: ollama pull {model}"
        return True, f"{model} (local)"
    if not s["online_enabled"]:
        return False, f"{PROVIDERS[prov]} is selected but online models are switched off (Settings → AI models)."
    if not secrets.has(KEY_NAME[prov]):
        return False, f"{PROVIDERS[prov]} needs {KEY_NAME[prov]} in ~/.swing_env."
    if not model:
        return False, f"Set the {PROVIDERS[prov]} model name in Settings → AI models."
    return True, f"{model} ({PROVIDERS[prov]})"


def chat(feature, messages, system=None, max_tokens=1500, temperature=None, json_mode=False, timeout=None):
    """messages: [{'role': 'user'|'assistant', 'content': str}]. Returns the reply text.
    json_mode: ask a local model for strict JSON output (online models are asked in the prompt).
    timeout: seconds for this call (default: the setting); lower values keep long batch jobs from stalling."""
    s, prov, model = resolve(feature)
    if timeout:
        s = dict(s, timeout=min(float(s["timeout"]), float(timeout)))
    temp = s["temperature"] if temperature is None else temperature
    if prov == "ollama":
        return _ollama(s, model, messages, system, temp, CTX.get(feature, 8192), max_tokens, json_mode)
    if not s["online_enabled"]:
        raise ModelError("Online models are switched off (Settings → AI models).")
    key = secrets.get(KEY_NAME[prov])
    if not key:
        raise ModelError(f"{KEY_NAME[prov]} is missing from ~/.swing_env.")
    if not model:
        raise ModelError(f"No model name set for {PROVIDERS[prov]}.")
    try:
        return {"anthropic": _anthropic, "openai": _openai, "gemini": _gemini}[prov](key, model, messages, system, temp, max_tokens, s["timeout"])
    except ModelError:
        raise
    except Exception as e:
        raise ModelError(_redact(f"{PROVIDERS[prov]} request failed: {type(e).__name__}: {e}".replace(key, "…"))) from None


def _post(url, headers, body, timeout):
    import requests
    r = requests.post(url, headers=headers, json=body, timeout=timeout)
    if r.status_code != 200:
        try:
            j = r.json()
            msg = (j.get("error") or {}).get("message") if isinstance(j.get("error"), dict) else j.get("error") or r.text[:200]
        except Exception:
            msg = r.text[:200]
        raise ModelError(_redact(f"error {r.status_code}: {msg}"))   # provider error bodies never contain the key; redact anyway
    return r.json()


def _ollama(s, model, messages, system, temp, ctx, max_tokens, json_mode=False):
    msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
    body = {"model": model, "messages": msgs, "stream": False,
            "options": {"temperature": temp, "num_ctx": ctx, "num_predict": max_tokens, "seed": 7}}
    if json_mode:
        body["format"] = "json"
    if model.startswith(("qwen3", "deepseek-r1", "gpt-oss")):
        body["think"] = False
    try:
        j = _post(_local_url(s["ollama_url"]) + "/api/chat", {}, body, s["timeout"])
    except ModelError:
        raise
    except Exception as e:
        raise ModelError(f"Local model not reachable ({type(e).__name__}). Is the Ollama app open?") from None
    return _clean(j.get("message", {}).get("content", ""))


def _anthropic(key, model, messages, system, temp, max_tokens, timeout):
    body = {"model": model, "max_tokens": max_tokens, "temperature": temp, "messages": list(messages)}
    if system:
        body["system"] = system
    j = _post("https://api.anthropic.com/v1/messages", {"x-api-key": key, "anthropic-version": "2023-06-01"}, body, timeout)
    return _clean("".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text"))


def _openai(key, model, messages, system, temp, max_tokens, timeout):
    msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
    body = {"model": model, "messages": msgs, "max_completion_tokens": max_tokens}
    j = _post("https://api.openai.com/v1/chat/completions", {"Authorization": f"Bearer {key}"}, body, timeout)
    return _clean(j["choices"][0]["message"].get("content") or "")


def _gemini(key, model, messages, system, temp, max_tokens, timeout):
    body = {"contents": [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in messages],
            "generationConfig": {"temperature": temp, "maxOutputTokens": max_tokens}}
    if system:
        body["system_instruction"] = {"parts": [{"text": system}]}
    j = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", {"x-goog-api-key": key}, body, timeout)
    parts = (j.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    return _clean("".join(p.get("text", "") for p in parts))


def _clean(text):
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def is_online(feature):
    return resolve(feature)[1] != "ollama"


# ---------------- embeddings (always local) ----------------
def embedder():
    """LlamaIndex embedding model served by Ollama (documents library)."""
    from llama_index.embeddings.ollama import OllamaEmbedding
    s = settings()
    return OllamaEmbedding(model_name=s["embed_model"], base_url=_local_url(s["ollama_url"]),
                           query_instruction="search_query: ", text_instruction="search_document: ")
