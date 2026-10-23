"""Article crawler (robots / paywall / relinking) and Ask the system on the model layer."""
import pandas as pd, pytest
from core import articles as A, news as N, llm, ask

UNI = pd.DataFrame({"Company Name": ["Hindustan Aeronautics Ltd.", "Bharat Electronics Ltd.", "Reliance Industries Ltd."],
                    "Industry": ["Capital Goods"] * 2 + ["Oil Gas"], "Symbol": ["HAL", "BEL", "RELIANCE"]})


def test_classify():
    long = "Revenue rose. " * 100
    assert A.classify("<html/>", long) == "ok"
    assert A.classify("<div>Subscribe to read the full story</div>", "Short teaser.") == "paywall"
    assert A.classify("<html/>", "Too short.") == "short"


def test_relink_uses_headline_and_repeated_mentions():
    tg = N.Tagger(UNI)
    text = ("Bharat Electronics won a defence order. BEL said the order is worth Rs 500 crore. "
            "Separately, Reliance Industries was mentioned once in passing.")
    assert A.relink("Defence stock jumps on order win", text, tg) == ["BEL"]          # named twice in the text
    assert A.relink("HAL shares rise", text, tg)[0] == "HAL"                          # headline match kept first


class FakePolite:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, check_robots=True, timeout=25):
        v = self.pages[url]
        if isinstance(v, Exception):
            raise v
        return v.encode()


def test_fetch_records_status_and_relinks(tmp_path, monkeypatch):
    pytest.importorskip("trafilatura")
    from core import library as L
    monkeypatch.setattr(A, "STATE", tmp_path / "state.parquet")
    store = N.enrich(pd.DataFrame([
        dict(published="2026-10-16 10:00", source="X", title="Defence stock jumps on order win", link="https://a.com/1"),
        dict(published="2026-10-16 11:00", source="Y", title="HAL Q2 preview", link="https://b.com/2"),
        dict(published="2026-10-16 12:00", source="Z", title="Markets close higher", link="https://c.com/3"),
        dict(published="2026-10-16 12:00", source="G", title="Something", link="https://news.google.com/rss/articles/abc")]), N.Tagger(UNI), UNI)
    saved = {}
    monkeypatch.setattr(N, "load_store", lambda: saved.get("s", store))
    monkeypatch.setattr(N, "save_store", lambda d: saved.__setitem__("s", d))
    monkeypatch.setattr(N, "load_tagger", lambda: (N.Tagger(UNI), UNI))
    added = []
    monkeypatch.setattr(L, "add_parts", lambda *a, **k: added.append(a[1]) or ("x", 1, False))
    monkeypatch.setattr(L, "_fts", lambda t: None)
    monkeypatch.setattr(L, "_table", lambda: None)
    body = "<html><body><article><p>" + ("Bharat Electronics won a defence order. BEL said the order is big. " * 12) + "</p></article></body></html>"
    pages = {"https://a.com/1": body, "https://b.com/2": "<html><div class='paywall'>Subscribe to read</div><p>HAL teaser</p></html>",
             "https://c.com/3": PermissionError("robots")}
    res = A.fetch(max_articles=10, polite=FakePolite(pages), log=lambda *_: None)
    assert res == {"ok": 1, "paywall": 1, "blocked": 1, "redirect": 1}
    assert added == ["https://a.com/1"]
    s = saved["s"].set_index("link")
    assert s.at["https://a.com/1", "symbols"] == "BEL"
    assert len(A.load_state()) == 4
    assert A.fetch(max_articles=10, polite=FakePolite(pages), log=lambda *_: None) == {}   # nothing retried


def test_ask_uses_model_layer_and_falls_back(monkeypatch):
    uni = pd.DataFrame({"Company Name": ["Tata Consultancy Services Ltd."], "Industry": ["IT"], "Symbol": ["TCS"]})
    monkeypatch.setattr(ask, "context", lambda q, u, p=None: ({"x": {"a": 1}}, []))
    monkeypatch.setattr(llm, "status", lambda f: (False, "no model"))
    text, mode, parts, hits = ask.answer("Why no trades?", [], uni)
    assert mode == "lookup" and text is None
    monkeypatch.setattr(llm, "status", lambda f: (True, "fake"))
    seen = {}
    monkeypatch.setattr(llm, "chat", lambda f, msgs, system=None, max_tokens=0: seen.update(f=f, m=msgs) or "Because regime is down.")
    text, mode, parts, hits = ask.answer("Why no trades?", [], uni)
    assert mode == "model" and seen["f"] == "ask" and "DATA" in seen["m"][-1]["content"]
