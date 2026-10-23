"""Evidence-only news labels: every misfire seen in the real store (Oct 2026) must give no tag or the right one,
and known-good headlines must still tag."""
import pandas as pd, pytest
from core import news as N

NAMES = [("Paras Defence and Space Technologies Ltd.", "PARAS"), ("Jyoti CNC Automation Ltd.", "JYOTICNC"),
         ("Engineers India Ltd.", "ENGINERSIN"), ("Action Construction Equipment Ltd.", "ACE"), ("Nuvama Wealth Management Ltd.", "NUVAMA"),
         ("Motilal Oswal Financial Services Ltd.", "MOTILALOFS"), ("Vedanta Ltd.", "VEDL"), ("Welspun Corp Ltd.", "WELCORP"),
         ("Swiggy Ltd.", "SWIGGY"), ("HDFC Bank Ltd.", "HDFCBANK"), ("Infosys Ltd.", "INFY"), ("Kotak Mahindra Bank Ltd.", "KOTAKBANK"),
         ("Tata Motors Passenger Vehicles Ltd.", "TMPV"), ("SBI Life Insurance Company Ltd.", "SBILIFE"), ("State Bank of India", "SBIN"),
         ("Havells India Ltd.", "HAVELLS"), ("Sagility Ltd.", "SAGILITY"), ("Multi Commodity Exchange of India Ltd.", "MCX"),
         ("Nippon Life India Asset Management Ltd.", "NAM-INDIA"), ("Crompton Greaves Consumer Electricals Ltd.", "CROMPTON"),
         ("Aarti Industries Ltd.", "AARTIIND"), ("The New India Assurance Company Ltd.", "NIACL"), ("Reliance Industries Ltd.", "RELIANCE"),
         ("Life Insurance Corporation of India", "LICI"), ("Vishal Mega Mart Ltd.", "VMM"), ("Colgate Palmolive (India) Ltd.", "COLPAL"),
         ("Clean Science and Technology Ltd.", "CLEAN")]
UNI = pd.DataFrame({"Company Name": [n for n, _ in NAMES], "Industry": ["X"] * len(NAMES), "Symbol": [s for _, s in NAMES]})
# instruments.csv: every NSE ticker, whose 'name' is just the ticker (this is what produced SILVER, GLOBAL ...)
INST = pd.DataFrame({"symbol": ["SILVER", "GLOBAL", "HEALTHCARE", "DIVIDEND", "WEALTH", "MOMENTUM"],
                     "name": ["SILVER", "GLOBAL", "HEALTHCARE", "DIVIDEND", "WEALTH", "MOMENTUM"]})


@pytest.fixture(scope="module")
def tg():
    return N.Tagger(UNI, INST)


@pytest.mark.parametrize("headline", [
    "Silver ETFs fall up to 5% as oil surge, inflation fears hit bullion markets",
    "Global Market: Japan's core inflation accelerates, putting BOJ rate hikes in focus",
    "Momentum Stocks: Renaissance Global, EMS, Revathi Equipment share under pressure, down 5-6%",
    "Paras Healthcare IPO booked 39% so far on Day 3",
    "Jyoti Structures shares gain 6% on Rs 690-crore transmission line order",
    "Core4 Engineers SME IPO opens today: Check price band",
    "Gold price on MCX hits record as dollar weakens",
    "'If you can't beat them, join them': Uday Kotak backs Chinese investment in India",
    "Aarti Drugs consolidated june 2026 net sales at rs 702 crore",
    "Will working cut your Social Security check? The new $25,560 earnings limit",
    "Mahindra Manulife seeks licence, builds senior team for life insurance entry",
    "Vishal nirmiti ipo day 2 gmp at 16 check subscription",
])
def test_no_stock_without_evidence(tg, headline):
    assert tg.find(headline) == []


@pytest.mark.parametrize("headline, expected", [
    ("Welspun Corp bags Rs 5,500 crore US pipeline order, order book hits record", ["WELCORP"]),
    ("Swiggy tanks 15%: Brokerages trim target after Q2 but retain 'Buy'; Nuvama sees 91% upside", ["SWIGGY"]),
    ("Buy Sagility; target of Rs 60: Motilal Oswal", ["SAGILITY"]),
    ("Buy bajaj auto target of rs 12096 motilal oswal", []),
    ("Price Action: Havells shares slip 4% as Nomura keeps 'neutral'", ["HAVELLS"]),
    ("HDFC Bank Share Price Live Updates", ["HDFCBANK"]),
    ("Tata Motors and SBI Life gain; SBI cuts lending rates", ["TMPV", "SBILIFE", "SBIN"]),
    ("Nippon Life India AMC debt down 6% on one-time provision", ["NAM-INDIA"]),
    ("Crompton Greaves Consumer Electrical: Nomura maintains 'Buy'", ["CROMPTON"]),
    ("MCX shares jump 5% on record volumes", ["MCX"]),
    ("Motilal Oswal Q2 profit jumps 20%", ["MOTILALOFS"]),
    ("Clean Science and Technology among 3 stocks rose above VWAP", ["CLEAN"]),
    ("Reliance hits back at Musk over satellite clearances", ["RELIANCE"]),
    ("Reliance Power shares hit upper circuit", []),
])
def test_stock_tags(tg, headline, expected):
    assert tg.find(headline) == expected


@pytest.mark.parametrize("headline, typ", [
    ("Buy Sagility; target of Rs 60: Prabhudas Lilladher", "Broker view"),
    ("Germany target of 'intensive' espionage, Merz says", ""),
    ("Jio IPO Day 3: GMP at 22%, issue subscribed over 5X; check brokerage views", "IPO / listing"),
    ("Cyient shares jump 10% post Q2, ₹12 dividend", "Results"),
    ("Kotak Mahindra Bank Q2 results preview: PAT may rise up to 32% YoY", "Results preview"),
    ("Swiggy receives prohibition order from FSSAI, says issue resolved", "Legal / regulatory"),
    ("Welspun Corp bags Rs 5,500 crore US pipeline order", "Order / contract"),
    ("Two Trades for Today: a large-cap hospitality stock for a gain", ""),
    ("Total ban on protests in Marseille after riots", ""),
    ("Meet the founder who built a side hustle", ""),
])
def test_type_needs_an_explicit_phrase(headline, typ):
    assert N.classify(headline)[0] == typ


def _tone(tg, h):
    d = N.enrich(pd.DataFrame([dict(published="2026-10-23 10:00", source="X", title=h, link="x")]), tg, UNI)
    return d.tone.iat[0], d.tone_why.iat[0]


@pytest.mark.parametrize("headline, tone", [
    ("Bernstein slashes Nifty target to 25,000: Why 6-year low valuation won't trigger a rally", ""),   # no stock
    ("Rupee rises 3 paise to 94.78 against US dollar", ""),                                          # not a stock
    ("Kotak Mahindra Bank Q2 results preview: PAT may rise up to 32% YoY", ""),                      # a preview
    ("Nippon Life India AMC debt down 6% on one-time provision", ""),                                # debt, not the share price
    ("Swiggy shares hit 10% lower circuit post Q2 results", "negative"),
    ("Swiggy tanks 15% after Q2", "negative"),
    ("Welspun Corp bags Rs 5,500 crore US pipeline order", "positive"),
    ("Buy Sagility; target of Rs 60: Prabhudas Lilladher", "positive"),
    ("Infosys Q2 results: profit jumps 12%", "positive"),
    ("Swiggy shares plunge 14% on Q2 losses; Infosys gains 9%", ""),                                 # two stocks: whose tone?
    ("Infosys shares jump 3% ahead of Q2: what to expect?", "positive"),                            # the stated move is a fact
    ("Infosys profit rises but revenue falls short; shares drop 3%", ""),                           # conflicting
])
def test_tone_only_with_evidence(tg, headline, tone):
    t, why = _tone(tg, headline)
    assert t == tone
    assert bool(why) == bool(t)                                     # every tone carries the phrase that set it


def test_old_store_is_retagged(tmp_path, monkeypatch, tg):
    monkeypatch.setattr(N, "STORE", tmp_path / "news.parquet")
    monkeypatch.setattr(N, "load_tagger", lambda: (tg, UNI))
    old = pd.DataFrame([dict(id="a", published=pd.Timestamp("2026-10-23 10:00"), source="X", kind="", method="rss",
                             title="Silver ETFs fall up to 5%", summary="", link="x", keywords="", tickers="", symbols="SILVER",
                             sectors="", event_type="Other", tone="negative", roundup=False, n_sources=3, fetched=pd.Timestamp("2026-10-23"))])
    old.to_parquet(N.STORE)
    d = N.load_store()
    assert d.symbols.iat[0] == "" and d.tone.iat[0] == "" and int(d.n_sources.iat[0]) == 3
    assert int(d.tag_version.iat[0]) == N.TAG_VERSION
