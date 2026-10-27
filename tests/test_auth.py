"""Logins: off until a user exists; then every page and API call needs a session; Tailscale host only with logins on;
passwords hashed; wrong passwords lock an address; cookies HttpOnly + SameSite=Strict (+ Secure over Tailscale)."""
import json, threading, http.client
import pytest
from web import server, auth


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "USERS", tmp_path / "users")
    monkeypatch.setattr(auth, "SESSIONS", tmp_path / "sessions")
    auth._fails.clear()
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()


def req(port, method, path, host=None, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    h = {"Host": host or f"127.0.0.1:{port}"}
    h.update(headers or {})
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    return r.status, r.read().decode(), dict(r.getheaders())


def login(port, user, pw, host=None, origin=None):
    h = {"X-MX-Token": server.TOKEN, "Content-Type": "application/json"}
    if origin:
        h["Origin"] = origin
    return req(port, "POST", "/api/auth/login", host=host, body={"user": user, "password": pw}, headers=h)


def test_off_until_a_user_exists(srv):
    assert not auth.enabled()
    code, body, _ = req(srv, "GET", "/")
    assert code == 200 and "Sign in" not in body
    assert req(srv, "GET", "/", host="mac.tail1234.ts.net")[0] == 403              # no remote access without logins


def test_login_required_then_session_works(srv):
    auth.add("anurag", "correct-horse-1", "owner")
    raw = auth.USERS.read_text()
    assert "correct-horse-1" not in raw and json.loads(raw)["users"]["anurag"]["role"] == "owner"
    code, body, _ = req(srv, "GET", "/")
    assert code == 200 and "Sign in" in body and server.TOKEN in body
    assert req(srv, "GET", "/api/status")[0] == 401
    assert req(srv, "GET", "/pages/live.js")[0] == 200 and "Sign in" in req(srv, "GET", "/pages/live.js")[1]   # page code only after login
    assert req(srv, "GET", "/app.css")[0] == 200
    assert login(srv, "anurag", "wrong-password")[0] == 401
    code, _, h = login(srv, "anurag", "correct-horse-1")
    ck = h["Set-Cookie"]
    assert code == 200 and "HttpOnly" in ck and "SameSite=Strict" in ck and "Secure" not in ck
    cookie = ck.split(";")[0]
    code, body, _ = req(srv, "GET", "/api/auth/whoami", headers={"Cookie": cookie})
    assert json.loads(body) == dict(enabled=True, user="anurag", role="owner")
    assert req(srv, "GET", "/api/nope", headers={"Cookie": cookie})[0] == 404       # past the gate
    assert "Sign in" not in req(srv, "GET", "/", headers={"Cookie": cookie})[1]
    assert auth.cookie_token(cookie) not in auth.SESSIONS.read_text()                 # only the hash is kept
    req(srv, "POST", "/api/auth/logout", headers={"Cookie": cookie, "X-MX-Token": server.TOKEN})
    assert json.loads(req(srv, "GET", "/api/auth/whoami", headers={"Cookie": cookie})[1])["user"] is None


def test_tailscale_host_and_origin(srv):
    auth.add("friend", "another-pass-2", "tester")
    host = "anurags-mac.tail1234.ts.net"
    assert req(srv, "GET", "/", host=host)[0] == 200
    assert login(srv, "friend", "another-pass-2", host=host, origin="https://evil.example")[0] == 403
    code, _, h = login(srv, "friend", "another-pass-2", host=host, origin=f"https://{host}")
    assert code == 200 and "Secure" in h["Set-Cookie"]
    assert req(srv, "GET", "/", host="evil.example")[0] == 403


def test_wrong_passwords_lock_the_address(srv):
    auth.add("anurag", "correct-horse-1", "owner")
    for _ in range(5):
        assert login(srv, "anurag", "nope-nope-nope")[0] == 401
    code, body, _ = login(srv, "anurag", "correct-horse-1")
    assert code == 429 and "15 minutes" in body


def test_password_rules_and_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "USERS", tmp_path / "u")
    monkeypatch.setattr(auth, "SESSIONS", tmp_path / "s")
    with pytest.raises(ValueError):
        auth.add("x", "short", "tester")
    with pytest.raises(ValueError):
        auth.add("bad name", "long-enough-pw", "tester")
    auth.add("friend", "long-enough-pw", "tester")
    t = auth.new_session("friend")
    assert auth.who(t) == dict(user="friend", role="tester")
    auth.remove("friend")
    assert auth.who(t) is None and not auth.enabled()


def test_tailscale_proxy_forwarded_host(srv):
    auth.add("friend", "another-pass-2", "tester")
    host = "anurags-mac.tail1234.ts.net"
    c = http.client.HTTPConnection("127.0.0.1", srv, timeout=20)
    c.request("POST", "/api/auth/login", body=json.dumps({"user": "friend", "password": "another-pass-2"}),
              headers={"Host": f"127.0.0.1:{srv}", "X-Forwarded-Host": host, "Origin": f"https://{host}", "X-MX-Token": server.TOKEN})
    r = c.getresponse()
    assert r.status == 200 and "Secure" in r.getheader("Set-Cookie")


def test_tester_is_refused_owner_actions(srv):
    auth.add("anurag", "correct-horse-1", "owner")
    auth.add("friend", "another-pass-2", "tester")
    ck = {u: login(srv, u, p)[2]["Set-Cookie"].split(";")[0] for u, p in (("anurag", "correct-horse-1"), ("friend", "another-pass-2"))}
    post = lambda who, path, body: req(srv, "POST", f"/api/{path}", body=body, headers={"Cookie": ck[who], "X-MX-Token": server.TOKEN})
    get = lambda who, path: req(srv, "GET", f"/api/{path}", headers={"Cookie": ck[who]})
    for path, body in [("job/start", {"name": "news"}), ("job/stop", {"lane": "main"}), ("live/start", {"replay": True}), ("live/stop", {}),
                       ("settings/ai", {}), ("positions/save", {}), ("lab/run", {})]:
        code, out, _ = post("friend", path, body)
        assert code == 403 and "owner" in out, path
    for path in ("settings", "job/log?lane=main", "job/file?file=x"):
        assert get("friend", path)[0] == 403, path
    assert post("friend", "desk/watchlist", {})[0] != 403                           # his own things are fine
    assert get("friend", "auth/whoami")[0] == 200 and get("friend", "overview")[0] != 403
    assert post("anurag", "job/start", {"name": "rm -rf"})[0] == 400                 # the owner reaches the route
    assert auth.allowed(None, "POST", "job/start")                                   # logins off: this Mac's owner


def test_each_person_keeps_their_own_watchlist_notes_and_charts(srv, tmp_path, monkeypatch):
    from core import config as C
    from desk import notes as N
    from web import r_workspace as W
    monkeypatch.setattr(C, "DATA", tmp_path / "data")
    monkeypatch.setattr(N, "DIR", tmp_path / "data" / "desk")
    monkeypatch.setattr(W, "FILE", tmp_path / "data" / "workspace" / "charts.json")
    auth.add("anurag", "correct-horse-1", "owner")
    auth.add("friend", "another-pass-2", "tester")
    ck = {u: login(srv, u, p)[2]["Set-Cookie"].split(";")[0] for u, p in (("anurag", "correct-horse-1"), ("friend", "another-pass-2"))}
    post = lambda who, path, body: req(srv, "POST", f"/api/{path}", body=body, headers={"Cookie": ck[who], "X-MX-Token": server.TOKEN})
    get = lambda who, path: json.loads(req(srv, "GET", f"/api/{path}", headers={"Cookie": ck[who]})[1])
    assert post("anurag", "desk/watchlist", {"add": "TCS"})[0] == 200
    assert post("friend", "desk/watchlist", {"add": "INFY"})[0] == 200
    assert [x["symbol"] for x in get("anurag", "desk/watchlist")["items"]] == ["TCS"]
    assert [x["symbol"] for x in get("friend", "desk/watchlist")["items"]] == ["INFY"]
    assert post("friend", "desk/notes", {"symbol": "TCS", "html": "<p>friend's note</p>"})[0] == 200
    assert (tmp_path / "data" / "users" / "friend" / "desk" / "TCS" / "notes.json").exists()
    assert not (tmp_path / "data" / "desk" / "TCS" / "notes.json").exists()                  # the owner's notes untouched
    assert (tmp_path / "data" / "desk" / "watchlist.json").exists()                          # owner's files stay where they were
