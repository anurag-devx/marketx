"""Market-data, news and announcement screeners."""
import numpy as np, pandas as pd, pytest
from core import announcements as A
from screener import market_scan as MS, news_scan as NS

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>NSE News - Latest Announcements</title>
<item><title>Zen Technologies Limited</title><link>https://nsearchives.nseindia.com/corporate/ZEN_18102026.pdf</link>
<description>ZENTEC  : Zen Technologies Limited has informed the Exchange about Acquisition of 51% stake in XYZ</description>
<pubDate>18-Oct-2026 07:00:00</pubDate></item>
<item><title>Quantum Gold ETF</title><link/>
<description>QGOLDHALF  : Quantum Asset Management Company has informed the Exchange that the Net Asset Value is declared</description>
<pubDate>18-Oct-2026 06:30:00</pubDate></item>
<item><title>No Symbol Co</title><link/><description>Plain text without a symbol</description><pubDate>bad date</pubDate></item>
</channel></rss>"""


def test_parse_rss_symbol_text_date_and_category():
    d = A.parse_rss(RSS)
    assert list(d.symbol) == ["ZENTEC", "QGOLDHALF", ""]
    assert d.published.iat[0] == pd.Timestamp("2026-10-18 07:00:00")
    assert pd.isna(d.published.iat[2])                       # unparseable date stays missing, not invented
    assert d.category.iat[0] == "Acquisition / merger" and d.category.iat[1] == "Routine compliance"
    assert d.link.iat[0].endswith(".pdf") and d.link.iat[1] == ""


def test_parse_api_tolerant_and_strict():
    d = A.parse_api({"data": [{"symbol": "HAL", "desc": "Bagging/Receiving of orders/contracts", "attchmntText": "received order of Rs 500 crore",
                               "an_dt": "17-Oct-2026 18:05:00", "attchmntFile": "https://x/y.pdf", "sm_name": "Hindustan Aeronautics"}]})
    assert d.symbol.iat[0] == "HAL" and d.category.iat[0] == "Order / contract" and d.company.iat[0] == "Hindustan Aeronautics"
    with pytest.raises(ValueError):
        A.parse_api({"message": "unauthorised"})
    with pytest.raises(ValueError):
        A.parse_api([{"foo": 1}])


@pytest.mark.parametrize("text,cat", [
    ("Outcome of Board Meeting - Financial Results for quarter ended Sep 2026", "Results"),
    ("Disclosure under SEBI (Substantial Acquisition of Shares and Takeovers) Regulations", "Shareholding / insider"),
    ("SEBI order imposing penalty", "Legal / regulatory"),
    ("Intimation of record date for interim dividend", "Dividend"),
    ("Allotment of shares under ESOS", "Employee stock options"),
    ("Something unusual", "Other"),
])
def test_categories(text, cat):
    assert A.categorise(text) == cat


def test_merge_dedupes_across_sources_and_fetches():
    d = A.parse_rss(RSS)
    m = A.merge(d, d.copy())
    assert len(m) == len(d)
    api = d.iloc[[0]].assign(id="other", source="NSE website")            # same filing, other source
    assert len(A.merge(m, api)) == len(d)


def _feat(n=300, symbols=("AAA", "BBB")):
    dates = pd.bdate_range("2024-01-01", periods=n)
    rows = []
    for i, s in enumerate(symbols):
        c = 100 + np.arange(n) * (0.1 if i == 0 else -0.05)
        rows.append(pd.DataFrame(dict(date=dates, symbol=s, open=c, high=c + 1, low=c - 1, close=c, volume=1000.0,
                                      sector="X", company_name=s, rsi14=50.0, sma50=c - 1, sma200=c - 2, median_value_20d_cr=5.0)))
    f = pd.concat(rows, ignore_index=True)
    last = f.date == dates[-1]
    f.loc[last & (f.symbol == "AAA"), ["open", "close", "high", "volume"]] = [135.0, 140.0, 141.0, 5000.0]   # gap up, big move
    return f.sort_values(["symbol", "date"]).reset_index(drop=True), dates


def test_snapshot_values():
    f, dates = _feat()
    snap, d = MS.snapshot(f)
    a = snap.set_index("symbol").loc["AAA"]
    prev = 100 + (len(dates) - 2) * 0.1
    assert a.change_1d_pct == pytest.approx(100 * (140 / prev - 1))
    assert a.gap_pct == pytest.approx(100 * (135 / prev - 1))
    assert a.rel_volume == pytest.approx(5.0)
    assert bool(a.new_52w_high) and not bool(snap.set_index("symbol").loc["BBB"].new_52w_high)


def test_snapshot_no_lookahead():
    f, dates = _feat()
    d0 = dates[-5]
    s1, _ = MS.snapshot(f, d0)
    g = f.copy()
    g.loc[g.date > d0, ["open", "high", "low", "close"]] *= 3
    s2, _ = MS.snapshot(g, d0)
    pd.testing.assert_frame_equal(s1, s2)


def test_scan_presets_and_counts():
    f, _ = _feat()
    snap, _ = MS.snapshot(f)
    r, counts = MS.scan(snap, "Gap up (≥ 2%)")
    assert list(r.symbol) == ["AAA"] and counts["match: Gap up (≥ 2%)"] == 1
    r, counts = MS.scan(snap, "Top losers")
    assert list(r.symbol) == ["BBB"]
    r, counts = MS.scan(snap, "Top gainers", min_turnover_cr=10)
    assert r.empty and counts["after price and liquidity"] == 0          # empty with the reason visible
    for p in MS.PRESETS:
        MS.scan(snap, p)                                                   # every preset runs


def test_news_tone_and_filters():
    assert NS.tone("Sensex surges as banks rally") == "neutral"                  # the index, not a stock: no direction claimed
    assert NS.tone("Government against new enterprise policy") == "neutral"     # no partial-word matches
    assert NS.tone("Shares plunge after loss") == "negative"
    d = pd.DataFrame(dict(published=pd.to_datetime(["2026-10-18 10:00", "2026-10-10 10:00", "2026-10-18 09:00"]),
                          source=["ET", "ET", "BBC"], kind=["companies", "companies", "world"],
                          title=["HAL bags order", "TCS results beat", "Oil prices fall"], summary=["", "", ""],
                          symbols=[["HAL"], ["TCS"], []], sectors=[["Capital Goods"], ["IT"], []]))
    d["tone"] = d.title.map(NS.tone)
    assert list(NS.filter_items(d, since="2026-10-17").title) == ["HAL bags order", "Oil prices fall"]
    assert list(NS.filter_items(d, keywords="results, crude").title) == ["TCS results beat"]
    assert list(NS.filter_items(d, sectors=["IT"]).title) == ["TCS results beat"]
    assert list(NS.filter_items(d, only_tagged=True, kinds=["companies"]).symbols.map(tuple)) == [("HAL",), ("TCS",)]
    s = NS.stocks_in_items(d, shortlist={"HAL"})
    assert set(s.symbol) == {"HAL", "TCS"} and bool(s.set_index("symbol").loc["HAL"].on_shortlist)
