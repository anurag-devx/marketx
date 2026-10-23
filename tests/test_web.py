"""Web app: the built-in server refuses other hosts and unsigned changes, serves the page with its token, and the API
returns plain JSON (no NaN, dates as text) for the main pages."""
import json, threading, http.client
import numpy as np, pandas as pd, pytest
from web import server, api


@pytest.fixture(scope="module")
def srv():
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd.server_address[1]
    httpd.shutdown()


def req(port, method, path, host=None, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    h = {"Host": host or f"127.0.0.1:{port}"}
    h.update(headers or {})
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    return r.status, r.read().decode(), dict(r.getheaders())


def test_page_has_token_and_security_headers(srv):
    code, body, h = req(srv, "GET", "/")
    assert code == 200 and server.TOKEN in body and "__TOKEN__" not in body
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"] and h["X-Content-Type-Options"] == "nosniff"


def test_other_hosts_refused(srv):
    assert req(srv, "GET", "/api/search?q=a", host="evil.example")[0] == 403          # DNS rebinding
    assert req(srv, "GET", "/", host="attacker.test:80")[0] == 403


def test_post_needs_token_and_same_origin(srv):
    assert req(srv, "POST", "/api/job/start", body={"name": "news"})[0] == 403        # no token
    assert req(srv, "POST", "/api/job/start", body={"name": "news"},
               headers={"X-MX-Token": server.TOKEN, "Origin": "https://evil.example"})[0] == 403
    code, body, _ = req(srv, "POST", "/api/job/start", body={"name": "rm -rf"}, headers={"X-MX-Token": server.TOKEN})
    assert code == 400 and "not a job" in body                                         # only listed jobs


def test_static_cannot_escape_folder(srv):
    assert req(srv, "GET", "/../core/secrets.py")[0] == 404
    assert req(srv, "GET", "/%2e%2e/cli.py")[0] == 404


def test_json_is_clean():
    out = json.loads(api.dumps({"a": np.nan, "b": pd.Timestamp("2026-10-23"), "c": pd.DataFrame({"x": [1.5, np.nan]}),
                                "d": np.int64(3), "e": pd.NaT, "f": np.bool_(True)}))
    assert out == {"a": None, "b": "2026-10-23T00:00:00", "c": [{"x": 1.5}, {"x": None}], "d": 3, "e": None, "f": True}


def test_unknown_endpoint(srv):
    code, body, _ = req(srv, "GET", "/api/nope")
    assert code == 404


def test_search_and_stock(srv, monkeypatch, tmp_path):
    uni = pd.DataFrame({"Company Name": ["Alpha Ltd", "Beta Ltd"], "Industry": ["X", "Y"], "Symbol": ["ALPHA", "BETA"]})
    d = pd.bdate_range("2025-01-01", periods=300)
    px = pd.DataFrame(dict(date=list(d) * 2, symbol=["ALPHA"] * 300 + ["BETA"] * 300, open=100.0, high=101.0, low=99.0,
                           close=np.r_[np.linspace(100, 130, 300), np.linspace(50, 40, 300)], volume=1000.0))
    monkeypatch.setattr(api, "universe", lambda: uni)
    monkeypatch.setattr(api, "prices", lambda: px)
    monkeypatch.setattr(api, "_stock_news", lambda s, n=40: [])
    monkeypatch.setattr(api, "_stock_results", lambda s: {"quarters": [], "next": None})
    code, body, _ = req(srv, "GET", "/api/search?q=alp")
    assert code == 200 and json.loads(body)[0]["symbol"] == "ALPHA"
    code, body, _ = req(srv, "GET", "/api/stock?symbol=ALPHA")
    j = json.loads(body)
    assert code == 200 and j["info"]["close"] == pytest.approx(130) and len(j["bars"]["c"]) > 100 and "NaN" not in body
    assert req(srv, "GET", "/api/stock?symbol=ZZZ")[0] == 400


# ---------------- every page and every API call the pages make exist ----------------
import re, time
from pathlib import Path
STATIC = Path(__file__).resolve().parents[1] / "web" / "static"


def _js():
    return {p.name: p.read_text() for p in [STATIC / "app.js", *sorted((STATIC / "pages").glob("*.js"))]}


def test_every_nav_entry_has_a_page():
    js = _js()
    nav = re.search(r"const NAV = \[(.*?)\n  \];", js["app.js"], re.S).group(1)
    keys = set(re.findall(r'\["([a-z\-]+)", "', nav))
    pages = set(re.findall(r'MX\.page\("([a-z\-]+)"', "".join(js.values())))
    assert keys and keys <= pages, keys - pages


def test_every_api_path_used_by_the_pages_exists():
    from web import r_today, r_screener, r_agents, r_research, r_tools, r_setup  # noqa: F401  (register routes)
    names = {n for _, n in api.ROUTES}
    used = set()
    for src in _js().values():
        for m in re.finditer(r'\b(?:api|post)\(\s*[`"]([a-z][a-z0-9\-/]*)', src):
            used.add(m.group(1).rstrip("/"))
    missing = {u for u in used if u not in names}
    assert not missing, missing


def test_no_inline_handlers_or_js_urls():
    """The CSP blocks inline scripts: an onclick= or javascript: link would silently do nothing."""
    for name, src in _js().items():
        assert "javascript:" not in src and not re.search(r'\son(click|change|input)=', src), name
    html = (STATIC / "index.html").read_text()
    assert "<script>" not in html and "onclick" not in html


def test_background_task_reports_result_and_error():
    from web import tasks
    a = tasks.start("t-ok", lambda log, prog: (log("step"), prog(0.5, "half"), 42)[-1])
    b = tasks.start("t-bad", lambda log, prog: 1 / 0)
    for _ in range(100):
        if not tasks.get(a)["running"] and not tasks.get(b)["running"]:
            break
        time.sleep(0.02)
    assert tasks.get(a)["result"] == 42 and "step" in tasks.get(a)["log"]
    assert tasks.get(b)["error"] and tasks.get(b)["result"] is None
    blocker = tasks.start("t-slow", lambda log, prog: time.sleep(0.3))
    with pytest.raises(RuntimeError):
        tasks.start("t-slow", lambda log, prog: None)          # one at a time per kind
    assert tasks.get(blocker)["running"]


def test_edit_tables_validate(monkeypatch, tmp_path):
    from web import r_research, r_setup  # noqa: F401
    from core import macro as M, quality
    saved = {}
    monkeypatch.setattr(M, "save_calendar", lambda d: saved.setdefault("cal", d))
    with pytest.raises(api.UserError):
        api.ROUTES[("POST", "macro/calendar")]({}, {"rows": [{c: ("RBI policy" if c != "date" else "") for c in M.CAL_COLS}]})
    r = api.ROUTES[("POST", "macro/calendar")]({}, {"rows": [{c: ("2026-12-05" if c == "date" else "x") for c in M.CAL_COLS}, {c: "" for c in M.CAL_COLS}]})
    assert r["saved"] == 1 and len(saved["cal"]) == 1
    monkeypatch.setattr(quality, "ACTIONS", tmp_path / "ca.csv")
    with pytest.raises(api.UserError):
        api.ROUTES[("POST", "data/actions")]({}, {"rows": [{"symbol": "abc", "ex_date": "2026-01-05", "factor": "0.5", "note": ""}]})
    api.ROUTES[("POST", "data/actions")]({}, {"rows": [{"symbol": "abc", "ex_date": "2026-01-05", "factor": "2", "note": "bonus"}]})
    assert pd.read_csv(tmp_path / "ca.csv").symbol.tolist() == ["ABC"]


def test_lab_validate_reports_rule_errors():
    from web import r_tools  # noqa: F401
    from lab import engine as E
    d = E.normalize(E.TEMPLATES["Blank signal strategy"])
    d["entry"] = "close > sma(200) and nonsense_fn(3)"
    r = api.ROUTES[("POST", "lab/validate")]({}, {"definition": d})
    assert r["errors"] and r["changed"]
