"""Quarterly results: NSE list parsing, XBRL quarter-context reading, choosing one filing per quarter, growth and
surprise on the same basis only, price reaction timing, and the update flow end to end with a fake NSE."""
import json
import numpy as np, pandas as pd, pytest
from core import fundamentals as F
from research import earnings as E


def xbrl(end="2026-09-30", rev=1000e7, profit=120e7, ytd_rev=1900e7, ns="http://www.bseindia.com/xbrl/fin", prefix="in-bse-fin",
         owners=None, segment=True, eps=12.5):
    start = (pd.Timestamp(end) - pd.DateOffset(months=3) + pd.Timedelta(days=1)).date()
    fy = pd.Timestamp(end).year if pd.Timestamp(end).month > 3 else pd.Timestamp(end).year - 1
    seg = ('<xbrli:context id="OneD_Seg"><xbrli:entity><xbrli:identifier scheme="x">INE1</xbrli:identifier><xbrli:segment>'
           '<xbrldi:explicitMember xmlns:xbrldi="http://xbrl.org/2006/xbrldi" dimension="a:b">a:c</xbrldi:explicitMember></xbrli:segment></xbrli:entity>'
           f'<xbrli:period><xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>'
           f'<{prefix}:RevenueFromOperations contextRef="OneD_Seg" unitRef="INR">1</{prefix}:RevenueFromOperations>') if segment else ""
    own = f'<{prefix}:ProfitOrLossAttributableToOwnersOfParent contextRef="OneD" unitRef="INR">{owners}</{prefix}:ProfitOrLossAttributableToOwnersOfParent>' if owners else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:{prefix}="{ns}">
 <xbrli:context id="OneD"><xbrli:entity><xbrli:identifier scheme="x">INE1</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:context id="FourD"><xbrli:entity><xbrli:identifier scheme="x">INE1</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:startDate>{fy}-04-01</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>
 {seg}
 <{prefix}:RevenueFromOperations contextRef="OneD" unitRef="INR" decimals="-5">{rev}</{prefix}:RevenueFromOperations>
 <{prefix}:RevenueFromOperations contextRef="FourD" unitRef="INR">{ytd_rev}</{prefix}:RevenueFromOperations>
 <{prefix}:OtherIncome contextRef="OneD" unitRef="INR">10e7</{prefix}:OtherIncome>
 <{prefix}:FinanceCosts contextRef="OneD" unitRef="INR">20e7</{prefix}:FinanceCosts>
 <{prefix}:DepreciationDepletionAndAmortisationExpense contextRef="OneD" unitRef="INR">30e7</{prefix}:DepreciationDepletionAndAmortisationExpense>
 <{prefix}:ProfitBeforeTax contextRef="OneD" unitRef="INR">160e7</{prefix}:ProfitBeforeTax>
 <{prefix}:ProfitLossForPeriod contextRef="OneD" unitRef="INR">{profit}</{prefix}:ProfitLossForPeriod>
 {own}
 <{prefix}:BasicEarningsLossPerShareFromContinuingOperations contextRef="OneD" unitRef="INRPerShare">{eps}</{prefix}:BasicEarningsLossPerShareFromContinuingOperations>
</xbrli:xbrl>""".encode()


def test_xbrl_reads_the_quarter_not_year_to_date_or_segments():
    r = F.parse_xbrl(xbrl(owners=110e7), "2026-09-30")
    assert r["status"] == "ok" and r["context"] == "OneD" and r["taxonomy"] == "in-bse-fin"
    assert r["revenue"] == 1000e7 and r["profit"] == 120e7 and r["profit_owners"] == 110e7 and r["eps_basic"] == 12.5
    assert json.loads(r["elements"])["eps_basic"] == "BasicEarningsLossPerShareFromContinuingOperations"


def test_xbrl_sebi_taxonomy_and_bad_files():
    r = F.parse_xbrl(xbrl(ns="http://www.sebi.gov.in/xbrl/2025-01-31", prefix="in-capmkt"), "2026-09-30")
    assert r["status"] == "ok" and r["taxonomy"] == "in-capmkt (SEBI)"
    assert F.parse_xbrl(b"<html><body>table</body></html>")["status"] == "unreadable"      # the ixbrl HTML rendering
    annual = xbrl().replace(b'id="OneD"', b'id="Old"').replace(b'contextRef="OneD"', b'contextRef="Old"')
    annual = annual.replace(b"<xbrli:startDate>2026-07-01</xbrli:startDate>", b"<xbrli:startDate>2025-10-01</xbrli:startDate>")
    assert F.parse_xbrl(annual, "2026-09-30")["status"] == "no quarter"                     # 12-month context is not a quarter


LEGACY = [{"symbol": "AAA", "companyName": "Aaa Ltd", "fromDate": "01-Jul-2026", "toDate": "30-Sep-2026", "filingDate": "23-Oct-2026 16:45",
           "consolidated": "Consolidated", "audited": "Un-Audited", "seqNumber": "101", "xbrl": "https://nsearchives.nseindia.com/a/101.xml",
           "ixbrl": "https://nsearchives.nseindia.com/a/101.html"},
          {"symbol": "AAA", "companyName": "Aaa Ltd", "toDate": "30-Sep-2026", "filingDate": "23-Oct-2026 16:40",
           "consolidated": "Non-Consolidated", "seqNumber": "100", "xbrl": "https://nsearchives.nseindia.com/a/100.xml"},
          {"symbol": "BBB", "toDate": "30-Sep-2026", "filingDate": "22-Oct-2026 11:00", "consolidated": "Non-Consolidated",
           "seqNumber": "102", "xbrl": "-"},                                                     # NSE's "no file" sentinel
          {"symbol": "ZZZ", "toDate": "30-Sep-2026", "filingDate": "22-Oct-2026 11:00", "consolidated": "Non-Consolidated",
           "seqNumber": "103", "xbrl": "https://nsearchives.nseindia.com/a/103.xml"}]


def test_discovery_parsing_and_plan():
    d = F.parse_discovery(LEGACY, "nse")
    assert len(d) == 4 and d.consolidated.tolist() == [True, False, False, False]
    assert d.filed_at.iat[0] == pd.Timestamp("2026-10-23 16:45") and pd.isna(d.xbrl.iat[2])
    with pytest.raises(ValueError):
        F.parse_discovery({"error": "x"}, "nse")
    p = F.plan(d, {"AAA", "BBB"}, done=set(), failed={})
    assert p.filing_id.tolist() == ["nse:101"]          # consolidated only; BBB has no file; ZZZ not in the universe
    assert F.plan(d, {"AAA"}, done=set(), failed={"nse:101": 3}).empty                     # gave up after 3 failures


class FakeResp:
    def __init__(self, code, content):
        self.status_code, self.content = code, content


class FakeSession:
    def __init__(self, routes):
        self.routes, self.headers, self.urls = routes, {}, []

    def get(self, url, timeout=None):
        self.urls.append(url)
        for k, v in self.routes.items():
            if k in url:
                v = v(url) if callable(v) else v
                return FakeResp(200, v if isinstance(v, bytes) else json.dumps(v).encode())
        return FakeResp(404, b"")


def test_update_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "DATA", tmp_path)
    monkeypatch.setattr(F, "DIR", tmp_path / "fundamentals")
    monkeypatch.setattr(F, "FILINGS", tmp_path / "fundamentals" / "filings.parquet")
    monkeypatch.setattr(F, "RESULTS", tmp_path / "fundamentals" / "results.parquet")
    monkeypatch.setattr(F, "CALENDAR", tmp_path / "fundamentals" / "calendar.parquet")
    monkeypatch.setattr(F, "STATE", tmp_path / "fundamentals" / "state.json")
    monkeypatch.setattr(F, "_universe", lambda: {"AAA", "BBB"})
    import core.filelock as FL
    monkeypatch.setattr(FL, "LOCKS", tmp_path / "locks")
    seen = {"legacy": 0}

    def legacy(url):
        seen["legacy"] += 1
        return LEGACY if seen["legacy"] == 1 else []
    sess = FakeSession({"corporates-financial-results": legacy,
                        "integrated-filing-results": {"data": [], "totalCount": 0},
                        "a/101.xml": xbrl(), "event-calendar": [{"symbol": "BBB", "purpose": "Financial Results", "date": "30-Oct-2099"}]})
    fetch = F.Fetcher(gap=0, backoff=0, sleep=lambda s: None, session_factory=lambda: sess)
    ok, bad = F.update(days=40, log=lambda *a: None, fetch=fetch, workers=2)
    assert (ok, bad) == (1, 0)
    r = F.results()
    assert r.filing_id.tolist() == ["nse:101"] and r.revenue.iat[0] == 1000e7
    assert not any("100.xml" in u for u in sess.urls)                         # standalone skipped: consolidated exists
    assert not any(".html" in u for u in sess.urls)                           # the ixbrl rendering is never fetched
    assert F.calendar().symbol.tolist() == ["BBB"]
    ok2, _ = F.update(days=40, log=lambda *a: None, fetch=fetch, workers=2)  # nothing new: nothing read again
    assert ok2 == 0 and len(F.results()) == 1
    fl = pd.read_parquet(F.FILINGS)                                            # an older unread filing listed by a backfill
    old = fl.iloc[[0]].assign(filing_id="nse:old", xbrl="a/old.xml", period_end=pd.Timestamp("2022-03-31"))
    pd.concat([fl, old]).to_parquet(F.FILINGS)
    msgs = []
    ok3, _ = F.update(days=40, log=msgs.append, fetch=fetch, workers=2)       # the nightly update leaves it to the backfill
    assert ok3 == 0 and not any("old.xml" in u for u in sess.urls) and any("older ones not read yet" in m for m in msgs)


def test_calendar_from_board_meeting_filings():
    ann = pd.DataFrame(dict(published=[pd.Timestamp("2026-10-20 18:00")] * 3, symbol=["AAA", "BBB", "CCC"],
                            subject=["Board Meeting Intimation", "Board Meeting Intimation", "Board Meeting Intimation"],
                            text=["to consider and approve the Unaudited Financial Results for the quarter ended September 30, 2026 on October 28, 2026",
                                  "Board meeting on 05-11-2026 to consider fund raising",            # not a results meeting
                                  "Meeting of the Board on 3rd November, 2026 to approve financial results"]))
    c = F.calendar_from_filings(ann, now="2026-10-23")
    assert dict(zip(c.symbol, c.date)) == {"AAA": pd.Timestamp("2026-10-28"), "CCC": pd.Timestamp("2026-11-03")}


def _res(sym, ends, profits, revs=None, cons=True, filed_lag=40):
    rows = []
    for i, (e, p) in enumerate(zip(ends, profits)):
        rows.append(dict(filing_id=f"{sym}{i}{cons}", symbol=sym, period_end=pd.Timestamp(e), filed_at=pd.Timestamp(e) + pd.Timedelta(days=filed_lag, hours=17),
                         consolidated=cons, status="ok", revenue=(revs[i] if revs else 1000e7), profit=p, profit_owners=None,
                         pbt=None, finance_costs=None, depreciation=None, other_income=None, eps_basic=1.0))
    return pd.DataFrame(rows)


def test_growth_and_surprise_same_basis_only():
    ends = [pd.Timestamp(e) for e in pd.date_range("2022-06-30", periods=14, freq="3ME")]
    noise = [0, 3, -2, 5, 1, -4, 2, 0, 4, -1, 3, -3, 2]
    profits = [100e7 + 2e7 * i + noise[i] * 1e7 for i in range(13)] + [300e7]                # a big jump in the last quarter
    q = E.quarters(_res("AAA", ends, profits))
    last = q.iloc[-1]
    assert last.quarter.startswith("Q") and last.basis == "consolidated"
    assert last.profit_yoy == pytest.approx(100 * (300e7 / profits[-5] - 1))
    assert last.sue_profit > 2                                                                  # a big beat vs its own history
    assert q.sue_profit.iloc[:8].isna().all()                                                   # not enough history yet
    # a basis change a year ago: no comparison
    mixed = pd.concat([_res("BBB", ends[:6], profits[:6], cons=False), _res("BBB", ends[6:], profits[6:], cons=True)])
    qb = E.quarters(mixed)
    assert qb.profit_yoy.iloc[6:10].isna().all() and qb.profit_yoy.iloc[10:].notna().all()


def test_revised_filing_keeps_first_and_loss_base_has_no_growth():
    ends = [pd.Timestamp(e) for e in pd.date_range("2024-06-30", periods=6, freq="3ME")]
    r = _res("CCC", ends, [-50e7, 10e7, 20e7, 30e7, 40e7, 50e7])
    rev = r.iloc[[-1]].assign(filing_id="rev", filed_at=r.filed_at.iat[-1] + pd.Timedelta(days=20), profit=99e7)
    q = E.quarters(pd.concat([r, rev]))
    assert q.profit_cr.iat[-1] == pytest.approx(50) and bool(q.revised.iat[-1])                # what the market saw first
    assert np.isnan(q.profit_yoy.iat[4])                                                      # a year ago was a loss


def test_reaction_uses_next_day_after_market_hours():
    dates = pd.bdate_range("2026-10-01", periods=40)
    px = pd.DataFrame(dict(date=dates, symbol="AAA", close=np.r_[np.full(10, 100.0), np.full(30, 110.0)]))
    idx = pd.DataFrame(dict(date=dates, nifty=1000.0))
    q = pd.DataFrame(dict(symbol=["AAA"], filed_at=[dates[9] + pd.Timedelta(hours=17)]))         # after 15:30 on day 9
    r = E.add_reaction(q, px, idx)
    assert r.day0.iat[0] == dates[10] and r.react_rel.iat[0] == pytest.approx(10)
    assert r.drift_5.iat[0] == pytest.approx(0)


def test_drift_study_buckets_and_holdout():
    rng = np.random.default_rng(0)
    n = 400
    ends = rng.choice(pd.date_range("2022-06-30", periods=16, freq="3ME"), n)
    sue = rng.normal(0, 2, n)
    q = pd.DataFrame(dict(period_end=ends, sue_profit=sue, react_rel=sue + rng.normal(0, 1, n), drift_5=rng.normal(0, 1, n),
                          drift_20=rng.normal(0, 1, n)))
    d = E.drift_study(q, holdout_quarters=4)
    assert set(d["sample"]) == {"earlier", "latest 4 quarters (held out)"}
    big = d[(d["sample"] == "earlier") & (d.surprise.str.startswith("big beat"))].iloc[0]
    assert big.react_rel_mean > 2 and big.react_rel_t > 2
