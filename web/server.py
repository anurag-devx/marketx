"""Local web server (standard library only): static files + a small JSON API.

Safety:
- binds to 127.0.0.1 only;
- every request must carry a Host header for this machine (stops DNS-rebinding pages from reading the API);
- requests that change anything (POST) must carry the per-start token from the page and a same-origin Origin
  (stops other websites open in your browser from starting jobs);
- no keys are ever sent to the browser: the API returns data and job status only;
- with users set up (web/auth.py), every page and API call needs a login, and the app can be reached through
  Tailscale (`tailscale serve`, https://<this-mac>.<tailnet>.ts.net) by the people you share this Mac with.
Run: python3 cli.py web   (default port 8504)"""
import json, mimetypes, os, secrets, sys, threading, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
sys.path.insert(0, str(ROOT.parent))
TOKEN = secrets.token_urlsafe(24)
OPEN_API = {"auth/login", "auth/whoami"}            # reachable before logging in
OPEN_STATIC = {"/app.css", "/icon.svg", "/login.js"}


def allowed_hosts(port):
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


class Handler(BaseHTTPRequestHandler):
    server_version = "MarketX"
    sys_version = ""

    def log_message(self, fmt, *a):          # quiet; errors still print
        pass

    # ---------- helpers ----------
    def _host(self):
        """The name the browser used. Behind `tailscale serve` (a proxy on this Mac) that may arrive as X-Forwarded-Host."""
        fh = self.headers.get("X-Forwarded-Host", "")
        if fh and self.client_address[0] in ("127.0.0.1", "::1") and fh.split(":")[0].lower().endswith(".ts.net"):
            return fh.split(",")[0].strip()
        return self.headers.get("Host", "")

    def _remote(self):
        """Reached through Tailscale (https://<mac>.<tailnet>.ts.net), only allowed once logins are on."""
        from web import auth
        h = self._host().split(":")[0].lower()
        return h.endswith(".ts.net") and auth.enabled()

    def _host_ok(self):
        return self._host() in allowed_hosts(self.server.server_address[1]) or self._remote()

    def _ip(self):
        ip = self.client_address[0]
        if ip in ("127.0.0.1", "::1") and self.headers.get("X-Forwarded-For"):      # tailscale serve on this Mac
            return self.headers["X-Forwarded-For"].split(",")[0].strip()
        return ip

    def _user(self):
        """None when logins are off (this Mac only) or nobody is signed in; else {'user', 'role'}."""
        from web import auth
        return auth.who(auth.cookie_token(self.headers.get("Cookie")))

    def _need_login(self):
        from web import auth
        return auth.enabled() and self._user() is None

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
                                                    "connect-src 'self'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, obj, code=200):
        from web.api import dumps
        self._send(code, dumps(obj))

    # ---------- GET ----------
    def do_GET(self):
        if not self._host_ok():
            return self._send(403, '{"error": "host not allowed"}')
        u = urlparse(self.path)
        if self._need_login():
            if u.path.startswith("/api/") and u.path[len("/api/"):].strip("/") not in OPEN_API:
                return self._send(401, '{"error": "login"}')
            if not u.path.startswith("/api/") and u.path not in OPEN_STATIC:
                return self._login_page()
        if u.path.startswith("/api/"):
            return self._api("GET", u)
        return self._static(u.path)

    def _login_page(self):
        html = (STATIC / "login.html").read_text().replace("__TOKEN__", TOKEN)
        return self._send(200, html, "text/html; charset=utf-8")

    def _static(self, path):
        if path in ("/", "/index.html"):
            html = (STATIC / "index.html").read_text().replace("__TOKEN__", TOKEN)
            return self._send(200, html, "text/html; charset=utf-8")
        f = (STATIC / path.lstrip("/")).resolve()
        if STATIC not in f.parents or not f.is_file():
            return self._send(404, '{"error": "not found"}')
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        return self._send(200, f.read_bytes(), ctype)

    # ---------- POST ----------
    def do_POST(self):
        if not self._host_ok():
            return self._send(403, '{"error": "host not allowed"}')
        origin = self.headers.get("Origin")
        port = self.server.server_address[1]
        ok_origins = {None, f"http://127.0.0.1:{port}", f"http://localhost:{port}"} | ({f"https://{self._host()}"} if self._remote() else set())
        if origin not in ok_origins or self.headers.get("X-MX-Token") != TOKEN:
            return self._send(403, '{"error": "not allowed"}')
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            return self._send(404, '{"error": "not found"}')
        name = u.path[len("/api/"):].strip("/")
        if name in ("auth/login", "auth/logout"):
            return self._auth(name)
        if self._need_login():
            return self._send(401, '{"error": "login"}')
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            return self._send(413, '{"error": "too large"}')
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, '{"error": "bad json"}')
        return self._api("POST", u, body)

    def _auth(self, name):
        from web import auth
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(min(n, 10_000)) or b"{}")
        except ValueError:
            body = {}
        secure = self._remote()
        if name == "auth/logout":
            auth.end_session(auth.cookie_token(self.headers.get("Cookie")))
            return self._send(200, '{"ok": true}', extra={"Set-Cookie": auth.clear_cookie(secure)})
        try:
            ok = auth.check(body.get("user"), body.get("password"), self._ip())
        except PermissionError as e:
            return self._json({"error": str(e)}, 429)
        if not ok:
            return self._json({"error": "wrong name or password"}, 401)
        tok = auth.new_session(body.get("user"))
        return self._send(200, '{"ok": true}', extra={"Set-Cookie": auth.set_cookie(tok, secure)})

    def _api(self, method, u, body=None):
        from web import api, auth
        name = u.path[len("/api/"):].strip("/")
        if name == "auth/whoami":
            return self._json(dict(enabled=auth.enabled(), **(self._user() or dict(user=None, role=None))))
        fn = api.ROUTES.get((method, name))
        if fn is None:
            return self._json({"error": f"unknown endpoint {name}"}, 404)
        params = {k: v[-1] for k, v in parse_qs(u.query).items()}
        who = self._user()
        if auth.enabled() and not auth.allowed(who, method, name):
            return self._json({"error": "only the owner can do this here"}, 403)
        auth.CURRENT.who = who                                 # for routes that keep per-user data
        try:
            return self._json(fn(params, body) if method == "POST" else fn(params))
        except api.UserError as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {str(e)[:200]}"}, 500)


def serve(port=8504, open_browser=True):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"
    print(f"MarketX running at {url}  (Ctrl+C to stop)", flush=True)
    if open_browser and os.environ.get("MX_NO_BROWSER") != "1":
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8504)
