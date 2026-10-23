"""Local web server (standard library only): static files + a small JSON API.

Safety:
- binds to 127.0.0.1 only;
- every request must carry a Host header for this machine (stops DNS-rebinding pages from reading the API);
- requests that change anything (POST) must carry the per-start token from the page and a same-origin Origin
  (stops other websites open in your browser from starting jobs);
- no keys are ever sent to the browser: the API returns data and job status only.
Run: python3 cli.py web   (default port 8504)"""
import json, mimetypes, os, secrets, sys, threading, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
sys.path.insert(0, str(ROOT.parent))
TOKEN = secrets.token_urlsafe(24)


def allowed_hosts(port):
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


class Handler(BaseHTTPRequestHandler):
    server_version = "MarketX"
    sys_version = ""

    def log_message(self, fmt, *a):          # quiet; errors still print
        pass

    # ---------- helpers ----------
    def _host_ok(self):
        return self.headers.get("Host", "") in allowed_hosts(self.server.server_address[1])

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
        if u.path.startswith("/api/"):
            return self._api("GET", u)
        return self._static(u.path)

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
        if origin not in (None, f"http://127.0.0.1:{port}", f"http://localhost:{port}") or self.headers.get("X-MX-Token") != TOKEN:
            return self._send(403, '{"error": "not allowed"}')
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            return self._send(404, '{"error": "not found"}')
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            return self._send(413, '{"error": "too large"}')
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, '{"error": "bad json"}')
        return self._api("POST", u, body)

    def _api(self, method, u, body=None):
        from web import api
        name = u.path[len("/api/"):].strip("/")
        fn = api.ROUTES.get((method, name))
        if fn is None:
            return self._json({"error": f"unknown endpoint {name}"}, 404)
        params = {k: v[-1] for k, v in parse_qs(u.query).items()}
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
