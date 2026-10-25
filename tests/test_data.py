"""Provider failures, candle validation, incremental download, corporate actions, quality checks."""
import sys, types
import numpy as np, pandas as pd, pytest
from core.provider import AngelProvider, ProviderError, validate_candles
from core import quality

ROW = ["2024-01-02T00:00:00+05:30", 100, 102, 99, 101, 1000]


class FakeAPI:
    def __init__(self, script):
        self.script, self.calls = script, 0          # shared across re-logins: one scripted sequence

    def getCandleData(self, p):
        self.calls += 1
        x = self.script.pop(0) if self.script else {"status": True, "data": [ROW]}
        if isinstance(x, Exception):
            raise x
        return x


def provider(script, logins=None):
    logins = logins if logins is not None else []
    def login():
        logins.append(1)
        return FakeAPI(script)
    sleeps = []
    p = AngelProvider(login, sleep=sleeps.append, log=lambda *_: None, max_retries=3)
    return p, logins, sleeps


def test_throttle_backs_off_then_succeeds():
    p, _, sleeps = provider([Exception("Access denied because of exceeding access rate"),
                             {"status": False, "message": "Access denied because of exceeding access rate", "errorcode": "AB1021"},
                             {"status": True, "data": [ROW]}])
    assert len(p.request({})) == 1
    assert p.stats["throttled"] == 2 and sum(sleeps) >= 2 + 4


def test_expired_session_relogs_in_once():
    logins = []
    p, logins, _ = provider([{"status": False, "message": "Invalid Token", "errorcode": "AG8001"}], logins)
    assert len(p.request({})) == 1
    assert len(logins) == 2 and p.stats["relogins"] == 1


def test_auth_failure_after_relogin_raises():
    p, _, _ = provider([{"status": False, "message": "Invalid Token", "errorcode": "AG8001"}] * 3)
    p._login = lambda: FakeAPI([{"status": False, "message": "Token expired", "errorcode": "AG8002"}])
    with pytest.raises(ProviderError) as e:
        p.request({})
    assert e.value.kind == "auth"


def test_persistent_failure_raises_not_empty():
    p, _, _ = provider([Exception("timeout")] * 10)
    with pytest.raises(ProviderError) as e:
        p.request({})
    assert e.value.kind == "unavailable"


def test_pacing_between_requests():
    t = [0.0]
    sleeps = []
    p = AngelProvider(lambda: FakeAPI([]), sleep=lambda s: (sleeps.append(s), t.__setitem__(0, t[0] + s)), clock=lambda: t[0],
                      min_interval=0.5, log=lambda *_: None)
    p.request({}); p.request({})
    assert sleeps and sleeps[0] == pytest.approx(0.5)


def test_validate_candles_drops_bad_rows():
    rows = [ROW, ROW, ["2024-01-03", 100, 98, 99, 99, 10], ["bad", 1, 1, 1, 1, 1], ["2024-01-04", "x", 1, 1, 1, 1]]
    d = validate_candles(rows)
    assert list(d.date.dt.strftime("%Y-%m-%d")) == ["2024-01-02"]               # duplicate, high<low, bad date, bad number dropped


@pytest.fixture
def fetch_mod(monkeypatch):
    for m in ("SmartApi", "pyotp", "logzero", "websocket"):
        monkeypatch.setitem(sys.modules, m, types.ModuleType(m))
    sys.modules["SmartApi"].SmartConnect = object
    sys.modules["logzero"].loglevel = lambda *a: None
    import importlib, fetch_angel
    return importlib.reload(fetch_angel)


def series(n=300, start="2023-01-02"):
    d = pd.bdate_range(start, periods=n)
    c = np.linspace(100, 130, n)
    return pd.DataFrame(dict(date=d, open=c, high=c + 1, low=c - 1, close=c, volume=1000.0))


def test_incremental_update_and_readjusted_history(fetch_mod):
    full = series()
    old = full.iloc[:-10]
    fetch = lambda frm: full[full.date >= pd.Timestamp(frm)].reset_index(drop=True)
    rows, how, raw = fetch_mod.update_symbol(fetch, old, "X", [])
    assert how == "update" and len(rows) == len(full) and raw.date.min() > old.date.min()
    halved = full.copy(); halved[["open", "high", "low", "close"]] /= 2         # Angel re-adjusted its history
    rows, how, _ = fetch_mod.update_symbol(lambda frm: halved[halved.date >= pd.Timestamp(frm)], old, "X", [])
    assert how == "refetch" and rows.close.iat[-1] == pytest.approx(full.close.iat[-1] / 2)


def test_split_in_new_bars_triggers_refetch_and_adjustment(fetch_mod):
    raw = series()
    raw.loc[raw.index[-5:], ["open", "high", "low", "close"]] /= 2               # 2:1 split, raw (unadjusted) bars
    old = raw.iloc[:-5]
    log = []
    rows, how, kept_raw = fetch_mod.update_symbol(lambda frm: raw[raw.date >= pd.Timestamp(frm)], old, "X", log)
    assert how == "refetch" and log and log[0]["factor"] == 2
    assert rows.close.iat[0] == pytest.approx(raw.close.iat[0] / 2)                # history adjusted
    assert kept_raw.close.iat[0] == pytest.approx(raw.close.iat[0])                # raw kept unadjusted


def test_corporate_action_applied_once(tmp_path, monkeypatch):
    px = series().assign(symbol="X")
    px.loc[px.index[150:], ["open", "high", "low", "close"]] /= 2
    monkeypatch.setattr(quality, "ACTIONS", tmp_path / "ca.csv")
    pd.DataFrame([dict(symbol="X", ex_date=str(px.date.iat[150].date()), factor=2, note="")]).to_csv(tmp_path / "ca.csv", index=False)
    adj, done = quality.apply_corporate_actions(px.copy())
    assert done[0]["applied"] and adj.close.iat[149] == pytest.approx(px.close.iat[149] / 2)
    again, done2 = quality.apply_corporate_actions(adj.copy())                    # already adjusted: skipped
    assert not done2[0]["applied"] and again.close.iat[149] == pytest.approx(adj.close.iat[149])


def test_quality_flags_invalid_bars(monkeypatch, tmp_path):
    monkeypatch.setattr(quality, "REVIEWED", tmp_path / "rev.csv")
    px = series().assign(symbol="X")
    px.loc[100, "high"] = px.loc[100, "low"] * 0.5                               # impossible bar
    px.loc[200, ["open", "high", "low", "close"]] *= 0.5                          # split-like gap
    px = pd.concat([px, px.iloc[[50]]])                                            # duplicate date
    idx = pd.DataFrame(dict(date=pd.bdate_range("2023-01-02", periods=300), nifty=1.0))
    iss = quality.check(px, idx)
    assert {"ohlc", "duplicates"} <= set(iss.check)
    assert (iss[iss.check.isin(["ohlc", "duplicates"])].severity == "error").all()


def test_hourly_and_minute_caps():
    """Angel's published getCandleData limits: 3/s, 180/min, 5000/h. The provider never exceeds its caps."""
    t = [0.0]
    sent = []
    def sleep(s):
        t[0] += s
    class Api:
        def getCandleData(self, p):
            sent.append(t[0]); return {"status": True, "data": [ROW]}
    p = AngelProvider(lambda: Api(), sleep=sleep, clock=lambda: t[0], min_interval=0.0, per_minute=5, per_hour=12,
                      log=lambda *_: None)
    for _ in range(30):
        p.request({})
    ts = np.array(sent)
    assert max(((ts > x - 60) & (ts <= x)).sum() for x in ts) <= 5          # never more than 5 in any minute
    assert max(((ts > x - 3600) & (ts <= x)).sum() for x in ts) <= 12       # never more than 12 in any hour
    assert p.stats["waited_for_cap"] > 0


def test_real_crash_is_not_mistaken_for_a_split(fetch_mod):
    crash = series()
    crash.loc[crash.index[-30:], ["open", "high", "low", "close"]] /= 2         # halves, volume unchanged: a crash
    log = []
    out = fetch_mod.adjust_splits(crash, "X", log)
    assert out.close.iat[0] == pytest.approx(crash.close.iat[0])                # history left alone
    assert log and log[0]["note"].startswith("not adjusted")
    split = crash.copy(); split.loc[split.index[-30:], "volume"] *= 2           # same gap with doubled volume: a split
    log = []
    out = fetch_mod.adjust_splits(split, "X", log)
    assert out.close.iat[0] == pytest.approx(split.close.iat[0] / 2) and log[0]["note"] == "adjusted"


def test_secrets_are_redacted_from_errors():
    from core.provider import redact, ProviderError
    msg = "401 Authorization: Bearer eyJhbGciOiJIUzUxMiJ9.eyJ1c2VybmFtZSI6IkExMjMifQ.abcdefghijk api_key=Xyz123 rest"
    assert "eyJ" not in redact(msg) and "Xyz123" not in redact(msg) and "rest" in redact(msg)
    assert "Xyz123" not in str(ProviderError("auth", msg))


def test_optional_job_steps_do_not_block():
    from core import jobs
    chain = " && ".join(jobs._cmd(c) for c in jobs.COMMANDS["fetch+pipeline"])
    assert '|| echo "(optional step failed: continuing)"' in chain and chain.count("||") == 10        # every step except prices and the agents is optional
    assert chain.index("fetch_angel.py") < chain.index("pipeline.py")


def test_key_file_parsing_tolerates_quotes(tmp_path, monkeypatch):
    from core import secrets
    f = tmp_path / "env"
    f.write_text('# c\nexport ANTHROPIC_API_KEY="sk-test"\nTELEGRAM_CHAT_ID=\'123\'\n')
    monkeypatch.setattr(secrets, "ENV", f)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert secrets.get("ANTHROPIC_API_KEY") == "sk-test" and secrets.get("TELEGRAM_CHAT_ID") == "123"


def test_instrument_tokens_fall_back_to_be_reit_invit_series_and_save_them():
    import fetch_angel as FA
    sm = pd.DataFrame({"exch_seg": "NSE", "symbol": ["AAA-EQ", "STLTECH-BE", "EMBASSY-RR", "PGINVIT-IV", "BBB-BE", "BBB-EQ", "NIFTY"],
                       "token": ["1", "2", "3", "4", "5", "6", "7"], "name": ["A", "S", "E", "P", "B", "B", "N"]})
    tok, inst = FA.instrument_tokens(sm, ["AAA", "STLTECH", "EMBASSY", "PGINVIT", "BBB", "GONE"])
    assert tok == {"AAA": "1", "BBB": "6", "STLTECH": "2", "EMBASSY": "3", "PGINVIT": "4"}       # -EQ wins over -BE
    t = dict(zip(inst.symbol, inst.token))
    assert t["STLTECH"] == "2" and t["EMBASSY"] == "3" and t["BBB"] == "6" and "GONE" not in t
    assert list(inst.columns) == ["symbol", "exchange", "token", "name", "trading_symbol"]


def test_article_scope_and_single_reader(tmp_path, monkeypatch):
    from core import articles as AR
    monkeypatch.setattr(AR, "STATE", tmp_path / "st.parquet")
    monkeypatch.setattr(AR, "LOCK", tmp_path / "articles.lock")
    now = pd.Timestamp.now()
    store = pd.DataFrame({"id": ["a", "b", "c", "d"], "link": ["https://x/1", "https://x/2", "https://x/3", ""],
                          "published": [now, now - pd.Timedelta(days=200), now, now], "symbols": ["TCS", "INFY", "", "TCS"]})
    allx, todo = AR.scope(store, days=90, linked=True)
    assert list(allx.id) == ["a"] and list(todo.id) == ["a"]
    assert AR._take_lock() and AR._take_lock()                 # the same process may re-take its own lock
    (tmp_path / "articles.lock").write_text("1")                 # pid 1 is alive: someone else is reading
    assert AR.fetch(log=lambda *_: None) == {}
    (tmp_path / "articles.lock").write_text("999999")            # dead pid: stale lock is taken over
    assert AR._take_lock()
