"""Tapetide REST API client (api.tapetide.com/v1, docs: developers.tapetide.com) for a one-time point-in-time history pull.
Token: TAPETIDE_TOKEN in ~/.swing_env (never printed or logged). Data stays on this Mac: their terms forbid redistribution,
so nothing from Tapetide goes into the repository (data/ and logs/ are git-ignored).

python -m core.tapetide usage   your quota (free call)
python -m core.tapetide probe   ~8 calls: metric list, quarterly history for 2 stocks, one full statement → logs/tapetide/"""
import json, sys, time
import requests
from core import config as C, secrets

BASE = "https://api.tapetide.com/v1"
OUT = C.BASE / "logs" / "tapetide"


def token_shape():
    """Describes the saved token without showing it."""
    t = secrets.get("TAPETIDE_TOKEN") or ""
    lines = secrets.ENV.read_text().splitlines() if secrets.ENV.exists() else []
    n = sum(1 for l in lines if l.strip().removeprefix("export ").strip().startswith("TAPETIDE_TOKEN"))
    return f"set={bool(t)}, starts with tpt_rt_={t.startswith('tpt_rt_')}, length={len(t)}, lines defining it={n}"


class Client:
    def __init__(self, pause=1.1):
        tok = secrets.get("TAPETIDE_TOKEN")
        if not tok:
            raise SystemExit("TAPETIDE_TOKEN is not set in ~/.swing_env")
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {tok}"
        self.pause, self.calls, self._last = pause, 0, 0.0

    def get(self, path, **params):
        wait = self.pause - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)                                   # stay well under 60 a minute
        r = self.s.get(f"{BASE}{path}", params={k: v for k, v in params.items() if v is not None}, timeout=60)
        self._last = time.time()
        if r.status_code == 401:
            raise SystemExit(f"Tapetide refused the token (401): {r.text[:200]}\nToken check: {token_shape()}")
        if r.status_code == 429:
            raise SystemExit(f"Tapetide quota or rate limit reached (429): {r.text[:200]}. Stopping; resume later.")
        r.raise_for_status()
        if not path.startswith("/me/"):
            self.calls += 1
        return r.json()

    def pages(self, path, **params):
        rows, cur, n = [], None, 0
        while True:
            b = self.get(path, **params, cursor=cur)
            rows += b.get("data") or []
            n += 1
            cur = (b.get("meta") or {}).get("next_cursor")
            if not cur:
                return rows, n


def main(argv):
    OUT.mkdir(parents=True, exist_ok=True)
    c = Client()
    cmd = argv[0] if argv else "usage"
    if cmd == "usage":
        print(json.dumps(c.get("/me/usage").get("data"), indent=1))
        return
    if cmd == "probe":
        save = lambda name, obj: (OUT / name).write_text(json.dumps(obj, indent=1, default=str))
        m, n = c.pages("/fundamentals/metrics", limit=100)
        save("metrics.json", m)
        print(f"metrics: {len(m)} ({n} calls) e.g. {', '.join(x.get('metric', x.get('name', '?')) for x in m[:12])}")
        for sym in ("TCS", "STLTECH"):
            rows, n = c.pages(f"/stocks/{sym}/fundamentals", period_type="quarter", **{"from": "2005-01-01"}, limit=1000)
            save(f"fund_{sym}.json", rows)
            per = sorted({r.get("period_end") for r in rows if r.get("period_end")})
            av = [r.get("available_from") for r in rows if r.get("available_from")]
            keys = sorted(rows[0]) if rows else []
            print(f"{sym}: {len(rows)} rows in {n} calls · quarters {per[0] if per else '—'} → {per[-1] if per else '—'} ({len(per)}) · "
                  f"rows with available_from {len(av)}/{len(rows)} · fields: {', '.join(keys)}")
        f = c.get("/stocks/TCS/financials", section="quarterly")
        save("financials_TCS_quarterly.json", f)
        print(f"TCS financials (quarterly): saved")
        print(f"Probe done: {c.calls} paid calls. Files in logs/tapetide/. Quota: {json.dumps(c.get('/me/usage').get('data'))}")


if __name__ == "__main__":
    main(sys.argv[1:])
