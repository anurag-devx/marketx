"""Search: query understanding, exposure list, web providers (fake HTTP), usage cap, deep search flow."""
import json, pandas as pd, pytest
from core import search as S, websearch as WS, deepsearch as D, llm, jobs

INFO = pd.DataFrame(dict(symbol=["RELIANCE", "ULTRACEMCO", "HAL", "TCS"], company=["Reliance Industries Ltd.", "UltraTech Cement Ltd.",
                    "Hindustan Aeronautics Ltd.", "Tata Consultancy Services Ltd."],
                    nifty_industry=["Oil Gas & Consumable Fuels", "Construction Materials", "Capital Goods", "Information Technology"],
                    macro="", sector=["Oil Gas & Consumable Fuels", "Construction Materials", "Capital Goods", "Information Technology"],
                    industry=["Refineries", "Cement", "Aerospace & Defense", "IT - Services"], basic_industry=["Refineries", "Cement", "Aerospace & Defense", "Computers"],
                    shares=None))


@pytest.fixture
def tagger(monkeypatch):
    """A tagger built from the test universe, so the test needs no data files (clean checkout / CI)."""
    from core import news as N
    uni = pd.DataFrame({"Company Name": list(INFO.company), "Industry": list(INFO.nifty_industry), "Symbol": list(INFO.symbol)})
    monkeypatch.setattr(N, "load_tagger", lambda: (N.Tagger(uni, None, None), uni))


def test_resolve_kinds(tagger):
    assert S.resolve("Reliance", INFO)["stocks"][0] == "RELIANCE"
    r = S.resolve("cement", INFO)
    assert r["kind"] == "sector" and "Construction Materials" in r["sectors"]
    assert S.resolve("IT services", INFO)["sectors"] == ["Information Technology"]
    assert S.resolve("Red Sea shipping", INFO)["themes"] == ["shipping"]
    assert S.resolve("quarterly guidance commentary", INFO)["kind"] == "text"


def test_exposure_list_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "DIR", tmp_path)
    monkeypatch.setattr(S, "EXPOSURES", tmp_path / "exp.csv")
    S.save_exposures(pd.DataFrame([dict(driver="Brent crude", target="Paints", effect="hurt", note="raw material"),
                                   dict(driver="", target="X", effect="", note="")]))
    d = S.exposures()
    assert len(d) == 1 and d.target.iat[0] == "Paints"


class R:
    def __init__(self, j):
        self._j = j

    def raise_for_status(self):
        pass

    def json(self):
        return self._j


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr(WS, "DIR", tmp_path)
    monkeypatch.setattr(WS, "OPTS", tmp_path / "o.json")
    monkeypatch.setattr(WS, "USAGE", tmp_path / "u.json")
    monkeypatch.setattr(WS.secrets, "get", lambda k: "KEY123")
    monkeypatch.setattr(WS.secrets, "has", lambda k: True)
    return WS


@pytest.mark.parametrize("prov,payload,key", [
    ("tavily", {"results": [{"title": "T", "url": "https://a", "content": "c", "published_date": "2026-10-01"}]}, "post"),
    ("brave", {"web": {"results": [{"title": "T", "url": "https://a", "description": "c", "page_age": "2026-10-01"}]}}, "get"),
    ("serper", {"organic": [{"title": "T", "link": "https://a", "snippet": "c"}]}, "post"),
    ("exa", {"results": [{"title": "T", "url": "https://a", "text": "c", "publishedDate": "2026-10-01"}]}, "post")])
def test_providers_normalised_and_counted(web, monkeypatch, prov, payload, key):
    import requests
    seen = {}
    monkeypatch.setattr(requests, key, lambda url, **kw: seen.update(url=url, kw=kw) or R(payload))
    web.save_options({"provider": prov, "monthly_cap": 2})
    out = web.search("HAL order")
    assert out[0]["url"] == "https://a" and out[0]["title"] == "T"
    assert web.usage()[prov] == 1
    assert "KEY123" not in json.dumps(seen.get("kw", {}).get("json", {}))      # key only in headers
    web.search("x"); 
    with pytest.raises(WS.WebSearchError):                                       # cap of 2 reached
        web.search("y")


def test_web_off_by_default(web):
    ok, msg = web.status()
    assert not ok and "off" in msg.lower()


def test_deep_search_flow(monkeypatch, tmp_path):
    monkeypatch.setattr(D, "BRIEFS", tmp_path)
    calls = []

    def fake_chat(feature, msgs, system=None, max_tokens=0, temperature=None):
        calls.append(feature)
        if "JSON list" in (system or ""):
            return '["HAL order book", "defence orders India"]'
        return "HAL's order book is large [1]. Exports grew [2]. Unsupported [9]."
    monkeypatch.setattr(llm, "chat", fake_chat)
    ev = [dict(kind="headline", title="HAL order book at Rs 1.9 lakh crore", text="...", url="https://x/1", date="2026-10-01", source="ET"),
          dict(kind="web", title="Defence exports rise", text="...", url="https://y/2", date="2026-09-01", source="Tavily")]
    monkeypatch.setattr(D, "gather", lambda qs, use_web=True, log=print: ev)
    rec = D.run("What is HAL's order outlook?", log=lambda *_: None)
    assert rec["queries"][0] == "What is HAL's order outlook?" and "HAL order book" in rec["queries"]
    assert rec["bad_citations"] == [9] and set(calls) == {"search"}
    assert len(D.history()) == 1


def test_history_chain_keeps_going(tmp_path, monkeypatch):
    import sys, time
    monkeypatch.setattr(jobs, "JOBS", tmp_path)
    monkeypatch.setattr(jobs, "LOCK", tmp_path / "c.json")
    monkeypatch.setattr(jobs, "SIDE_LOCK", tmp_path / "s.json")
    monkeypatch.setattr(jobs, "COMMANDS", {"history-all": [[sys.executable, "-c", "raise SystemExit(3)"], [sys.executable, "-c", "print('second ran')"]]})
    log = jobs.start("history-all")
    for _ in range(50):
        if not jobs.busy("history-all"):
            break
        time.sleep(0.1)
    txt = log.read_text()
    assert "step failed: continuing" in txt and "second ran" in txt


def test_theme_patterns_are_specific():
    from core import macro as M
    assert M.themes_in("Jio has headroom left on phone tariffs") == []
    assert M.themes_in("Brazil election looms") == []
    assert M.themes_in("RBI fixes SGB redemption price") == []
    assert "us_tariffs" in M.themes_in("Trump threatens tariffs on Indian pharma")
    assert "central_banks" in M.themes_in("RBI keeps repo rate unchanged")
    assert "oil_supply" in M.themes_in("India's Russia crude imports fell 12%")


def test_macro_links_routes(monkeypatch, tmp_path):
    from core import macro as M, news as N
    now = pd.Timestamp.now()
    h = pd.DataFrame([dict(published=now, theme="middle_east", title="Brent crude jumps as Iran strikes", source="x", link="", tone="negative"),
                      dict(published=now, theme="us_tariffs", title="Trump tariffs hit Reliance exports", source="x", link="", tone="negative"),
                      dict(published=now, theme="india_pakistan", title="Pakistan border firing", source="x", link="", tone="negative")])
    monkeypatch.setattr(M, "headline_themes", lambda news=None: h)
    monkeypatch.setattr(M, "summary", lambda day=None: [dict(label="Brent crude", chg_20d=8.0, change_kind="%")])
    monkeypatch.setattr(N, "load_store", lambda: pd.DataFrame(dict(title=h.title, symbols=["", "RELIANCE", ""])))
    monkeypatch.setattr(S, "_read_csv", lambda p: pd.DataFrame([dict(sector="Oil Gas & Consumable Fuels", factor="Brent crude", per_1sd_pct=-1.2, t=-3.1, weeks=150)])
                        if "sens_3y" in str(p) else pd.DataFrame())
    monkeypatch.setattr(S, "exposures", lambda: pd.DataFrame([dict(driver="Geopolitical risk", target="RELIANCE", effect="hurt", note="mine")]))
    links, act = S.macro_links("RELIANCE", "Oil Gas & Consumable Fuels", "Oil Gas & Consumable Fuels")
    a = act.set_index("theme")
    assert a.at["Middle East conflict", "link"] == "strong" and "via Brent crude" in a.at["Middle East conflict", "reaches_stock_via"]
    assert "named in 1" in a.at["US tariffs / trade war", "reaches_stock_via"]
    assert "your list: Geopolitical risk" in a.at["India–Pakistan", "reaches_stock_via"]       # theme → factor → your list


def test_market_snapshot_any_date():
    from conftest import make_px
    from core import market as mk
    px, _ = make_px(start="2020-01-01", end="2022-12-30")
    uni = pd.DataFrame({"Symbol": px.symbol.unique(), "Industry": "Test", "Company Name": "X"})
    p = mk.prepare(px)
    d1, sec1, s1 = mk.snapshot(p, uni, "2021-06-05")          # Saturday -> Friday 4 June
    assert s1["date"] == pd.Timestamp("2021-06-04") and s1["up"] + s1["down"] <= 6
    d2, _, s2 = mk.snapshot(p, uni)
    assert s2["date"] == p.date.max()
    assert len(mk.breadth(p)) > 400
