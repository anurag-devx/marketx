"""Angel One throttling: the provider pauses and slows instead of dropping stocks; the fetch retries failed stocks in
later passes and reports every stock still missing the latest bar (never passes old prices off as new)."""
import json, sys, types
import numpy as np, pandas as pd, pytest
from core.provider import AngelProvider, ProviderError

ROW = ["2024-01-02T00:00:00+05:30", 100, 102, 99, 101, 1000]
THROTTLE = Exception("Couldn't parse the JSON response received from the server: b'Access denied because of exceeding access rate'")


def clocked(api, **kw):
    t = [0.0]
    def sleep(s):
        t[0] += s
    p = AngelProvider(lambda: api, sleep=sleep, clock=lambda: t[0], log=lambda *_: None, **kw)
    return p, t


class Blocked:
    """Refuses everything until `until` seconds have passed (Angel's multi-minute blocks)."""
    def __init__(self, t, until):
        self.t, self.until, self.calls = t, until, 0

    def getCandleData(self, p):
        self.calls += 1
        if self.t[0] < self.until:
            raise THROTTLE
        return {"status": True, "data": [ROW]}


def test_multi_minute_block_is_waited_out_not_failed():
    api = Blocked(None, 8 * 60)
    p, t = clocked(api)
    api.t = t
    assert len(p.request({})) == 1                         # waited ~8 minutes, then got the data
    assert t[0] >= 8 * 60 and p.stats["throttled"] >= 3
    assert p.interval > p.base_interval                    # and slowed down afterwards


def test_gives_up_only_after_the_throttle_budget():
    api = Blocked(None, 10 ** 9)
    p, t = clocked(api, throttle_budget=600)
    api.t = t
    with pytest.raises(ProviderError) as e:
        p.request({})
    assert e.value.kind == "throttled" and t[0] >= 600


def test_pace_recovers_after_successes():
    class Ok:
        def getCandleData(self, p):
            return {"status": True, "data": [ROW]}
    p, _ = clocked(Ok(), recover_after=5)
    p.interval = 3.0
    for _ in range(30):
        p.request({})
    assert p.interval == pytest.approx(p.base_interval, rel=0.3)


@pytest.fixture
def fetch_mod(monkeypatch, tmp_path):
    for m in ("SmartApi", "pyotp", "logzero", "websocket"):
        monkeypatch.setitem(sys.modules, m, types.ModuleType(m))
    sys.modules["SmartApi"].SmartConnect = object
    sys.modules["logzero"].loglevel = lambda *a: None
    import importlib, fetch_angel
    f = importlib.reload(fetch_angel)
    monkeypatch.setattr(f, "OUT", tmp_path)
    monkeypatch.setattr(f, "RETRY_PASSES", (0, 0))
    monkeypatch.setattr(f.time, "sleep", lambda s: None)
    return f


def _old(sym, days):
    d = pd.bdate_range("2024-01-01", periods=days)
    return pd.DataFrame(dict(date=d, symbol=sym, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0))


def test_failed_stock_is_retried_in_a_later_pass_and_stale_ones_reported(fetch_mod, tmp_path):
    f = fetch_mod
    old = pd.concat([_old("AAA", 40), _old("BBB", 40), _old("CCC", 40)])
    old.to_parquet(tmp_path / "prices.parquet")
    pd.DataFrame({"Company Name": ["A", "B", "C", "D"], "Industry": ["X"] * 4, "Symbol": ["AAA", "BBB", "CCC", "DDD"]}).to_csv(tmp_path / "universe.csv", index=False)
    new_day = pd.bdate_range("2024-01-01", periods=41)[-1]
    calls = {"BBB": 0}

    def fake_candles(api, token, exchange="NSE", frm=None):
        if token == "2":                                       # BBB: refused on the first pass, fine on the retry
            calls["BBB"] += 1
            if calls["BBB"] == 1:
                raise ProviderError("throttled", "Angel One kept refusing")
        if token == "3":                                       # CCC: refused every time
            raise ProviderError("throttled", "Angel One kept refusing")
        d = pd.bdate_range(pd.Timestamp(frm), new_day)
        return pd.DataFrame(dict(date=d, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0))

    f.candles = fake_candles
    tok = {"AAA": "1", "BBB": "2", "CCC": "3"}
    summ = f.run_set(object(), ["AAA", "BBB", "CCC", "DDD"], tok, tmp_path / "prices.parquet", False, "Nifty 500")
    assert summ["failed"] == ["CCC (throttled)"] and calls["BBB"] == 2
    px = pd.read_parquet(tmp_path / "prices.parquet")
    last = px.groupby("symbol").date.max()
    assert last["AAA"] == new_day and last["BBB"] == new_day and last["CCC"] < new_day   # CCC kept its OLD bars, dated as old
    rep = f.write_stale_report(summ, {"DDD"})
    why = {r["symbol"]: r["reason"] for r in rep["stocks"]}
    assert set(why) == {"CCC", "DDD"}
    assert why["CCC"].startswith("download failed") and "instrument list" in why["DDD"]
    assert json.loads((tmp_path / "stale_symbols.json").read_text())["day"] == str(new_day.date())
