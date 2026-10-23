"""Market-data providers behind one interface, so other markets (e.g. a US provider) can be added later
without touching the screener or the agents. Only Angel One (NSE cash market, daily bars) exists today.

AngelProvider wraps the SmartAPI client:
- pacing: at most one historical request every `min_interval` seconds, and at most `per_minute` / `per_hour`
  in any rolling minute / hour. Angel's published limits for getCandleData are 3/second, 180/minute
  (moderator correction of the table's 500) and 5000/hour (SmartAPI forum, "Changes in API Rate Limit",
  2024); the defaults stay just under them. The hourly cap is what makes a full all-NSE download take 3+ hours.
- throttling ("exceeding access rate", raised as an exception or returned as an error): Angel's server-side limiter
  has been rejecting requests far below the published rates and blocking for minutes at a time (SmartAPI forum,
  Aug-Sep 2026). So a throttle is treated as a pause for the WHOLE client, not a failure of one stock: wait
  (cooldown, starting at `cooldown` seconds and doubling to `max_cooldown`), slow the pace (x1.5, up to `max_interval`),
  then retry the same request. Throttle waits don't use up the request's retries; a request gives up only after
  `throttle_budget` seconds of throttling. After `recover_after` successes in a row the pace speeds back up.
- expired or invalid session (error codes AG8001 / AG8002 / AG8003 or a message about the token): one
  re-login, then the request is retried
- other failures: retried `max_retries` times, then ProviderError (never an empty result passed off as data)
- each response is validated: parseable dates, no duplicate dates, numeric OHLCV, high >= low
The error codes come from SmartAPI's library behaviour and its community forum; Angel's official error
table was not reachable when this was written, so unknown codes are treated as ordinary retryable errors.

Credentials are never handled here: the caller passes a login function that reads ~/.angel_env."""
import time
from datetime import datetime, timedelta
import pandas as pd

TOKEN_ERRORS = {"AG8001", "AG8002", "AG8003"}
CHUNK_DAYS = 1900                      # ONE_DAY interval allows up to 2000 days per request


import re as _re
_SECRET = _re.compile(r"eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{5,}|(?i:bearer)\s+\S+|(?i:(?:api[_-]?key|token|password|pin|totp)\W{0,3}[:=]\s*)\S+")


def redact(text):
    """Masks JWTs, bearer tokens and key=value secrets in error text before it is printed or stored."""
    return _SECRET.sub("[hidden]", str(text))


class ProviderError(RuntimeError):
    def __init__(self, kind, message):
        message = redact(message)
        super().__init__(f"{kind}: {message}")
        self.kind = kind               # auth | throttle | unavailable | bad_data


class DataProvider:
    name = "base"
    markets = ()

    def candles(self, security_id, frm, to=None):
        raise NotImplementedError

    def instruments(self):
        raise NotImplementedError


def _is_throttle(text):
    t = str(text).lower()
    return "exceeding access rate" in t or "too many requests" in t or "rate limit" in t


def _is_token(code, text):
    t = str(text).lower()
    return str(code) in TOKEN_ERRORS or "invalid token" in t or "token expired" in t or "invalid session" in t


class AngelProvider(DataProvider):
    name = "angel_one"
    markets = ("NSE",)

    def __init__(self, login, api=None, exchange="NSE", min_interval=1.0, max_retries=4, backoff=2.0,
                 sleep=time.sleep, clock=time.monotonic, log=print, per_minute=170, per_hour=4800,
                 cooldown=20.0, max_cooldown=300.0, max_interval=3.0, throttle_budget=900.0, recover_after=25):
        self._login, self.api = login, api
        self.exchange, self.min_interval, self.max_retries, self.backoff = exchange, min_interval, max_retries, backoff
        self.base_interval, self.interval = min_interval, min_interval
        self.cooldown, self.max_cooldown, self.max_interval = cooldown, max_cooldown, max_interval
        self.throttle_budget, self.recover_after = throttle_budget, recover_after
        self._sleep, self._clock, self._log = sleep, clock, log
        self._last = None
        self._next_cool, self._ok_run = cooldown, 0
        self.per_minute, self.per_hour = per_minute, per_hour
        from collections import deque
        self._sent = deque()                 # monotonic times of requests in the last hour
        self.stats = dict(requests=0, retries=0, throttled=0, relogins=0, waited_for_cap=0, throttle_wait_s=0.0)

    def _pace(self):
        if self._last is not None:
            wait = self.interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        for window, cap in ((60.0, self.per_minute), (3600.0, self.per_hour)):
            now = self._clock()
            while self._sent and now - self._sent[0] >= 3600.0:
                self._sent.popleft()
            recent = [t for t in self._sent if now - t < window] if window < 3600 else list(self._sent)
            if cap and len(recent) >= cap:
                wait = window - (now - recent[len(recent) - cap]) + 0.05
                if wait > 0:
                    if wait > 30:
                        self._log(f"  pausing {wait / 60:.0f} min: Angel allows {cap} requests per {'hour' if window > 60 else 'minute'}")
                    self.stats["waited_for_cap"] += 1
                    self._sleep(wait)
        self._last = self._clock()
        self._sent.append(self._last)

    def _throttled(self):
        """Angel said 'too fast': pause everything, then go slower."""
        self.stats["throttled"] += 1
        wait = self._next_cool
        self.interval = min(self.max_interval, max(self.interval, self.base_interval) * 1.5)
        if wait >= 60:
            self._log(f"  Angel One is limiting requests: waiting {wait / 60:.0f} min, then {self.interval:.1f} s between requests")
        self._sleep(wait)
        self.stats["throttle_wait_s"] += wait
        self._next_cool = min(self.max_cooldown, self._next_cool * 2)
        self._ok_run = 0
        return wait

    def _succeeded(self):
        self._ok_run += 1
        self._next_cool = self.cooldown
        if self._ok_run >= self.recover_after and self.interval > self.base_interval:
            self.interval = max(self.base_interval, self.interval / 1.25)
            self._ok_run = 0

    def _ensure(self):
        if self.api is None:
            self.api = self._login()

    def request(self, params):
        """One getCandleData call with pacing, retries, throttle pauses and re-login. Returns raw rows."""
        self._ensure()
        relogged, last_err, attempt, throttle_waited = False, "", 0, 0.0
        while attempt <= self.max_retries:
            self._pace()
            self.stats["requests"] += 1
            try:
                r = self.api.getCandleData(params)
            except Exception as e:                       # the library raises on HTTP / throttle errors
                r, last_err = None, str(e)
                if _is_throttle(e):
                    if throttle_waited >= self.throttle_budget:
                        raise ProviderError("throttled", f"Angel One kept refusing for {throttle_waited / 60:.0f} min ({last_err[:120]})")
                    throttle_waited += self._throttled()
                    continue                             # same attempt: throttling is not this request's fault
                if _is_token("", e) and not relogged:
                    self._relogin(); relogged = True
                    continue
            if r is not None:
                if r.get("status"):
                    self._succeeded()
                    return r.get("data") or []
                code, msg = r.get("errorcode", ""), r.get("message", "")
                last_err = f"{code} {msg}".strip()
                if _is_token(code, msg):
                    if relogged:
                        raise ProviderError("auth", f"session rejected after re-login ({last_err})")
                    self._relogin(); relogged = True
                    continue
                if _is_throttle(msg):
                    if throttle_waited >= self.throttle_budget:
                        raise ProviderError("throttled", f"Angel One kept refusing for {throttle_waited / 60:.0f} min ({last_err[:120]})")
                    throttle_waited += self._throttled()
                    continue
            self.stats["retries"] += 1
            self._sleep(self.backoff * (attempt + 1))
            attempt += 1
        raise ProviderError("unavailable", f"no data after {self.max_retries + 1} attempts ({last_err or 'empty response'})")

    def _relogin(self):
        self.stats["relogins"] += 1
        self._log("  session expired or invalid: logging in again")
        self.api = self._login()

    def candles(self, security_id, frm, to=None, exchange=None):
        """Daily bars from `frm` to `to` (default now), requested in chunks. Raw, unadjusted, validated."""
        rows, frm, end = [], pd.Timestamp(frm).to_pydatetime(), pd.Timestamp(to).to_pydatetime() if to else datetime.now()
        while frm < end:
            stop = min(frm + timedelta(days=CHUNK_DAYS), end)
            rows += self.request({"exchange": exchange or self.exchange, "symboltoken": str(security_id), "interval": "ONE_DAY",
                                  "fromdate": frm.strftime("%Y-%m-%d 09:15"), "todate": stop.strftime("%Y-%m-%d 15:30")})
            frm = stop + timedelta(days=1)
        return validate_candles(rows)


def validate_candles(rows):
    cols = ["date", "open", "high", "low", "close", "volume"]
    if not rows:
        return pd.DataFrame(columns=cols)
    d = pd.DataFrame(rows, columns=cols)
    d["date"] = pd.to_datetime(d.date.astype(str).str[:10], errors="coerce")
    for c in cols[1:]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    bad = d.date.isna() | d[cols[1:]].isna().any(axis=1) | (d.high < d.low)
    if bad.any():
        d = d[~bad]
    return d.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)


class USProviderNotAvailable(DataProvider):
    """Placeholder: Angel One does not supply US data. A US provider must implement DataProvider."""
    name, markets = "us_unavailable", ("US",)

    def candles(self, *a, **k):
        raise ProviderError("unavailable", "no US market data provider is configured")
