"""News system: cleaning, parsing, robots, tagging, dedupe, filing backfill, company info, event study."""
import numpy as np, pandas as pd, pytest
from core import news as N, news_sources as S, announcements as A, company_info as CI
from research import events as E

UNI = pd.DataFrame({"Company Name": ["Reliance Industries Ltd.", "Persistent Systems Ltd.", "State Bank of India", "Tata Consultancy Services Ltd.",
                                     "Larsen & Toubro Ltd.", "Infosys Ltd."],
                    "Industry": ["Oil Gas", "Information Technology", "Financial Services", "Information Technology", "Construction",
                                 "Information Technology"],
                    "Symbol": ["RELIANCE", "PERSISTENT", "SBIN", "TCS", "LT", "INFY"]})


@pytest.fixture
def tagger():
    return N.Tagger(UNI)


# ---------------- cleaning / encoding ----------------
def test_clean_decodes_double_encoded_entities():
    assert S.clean("Japan&amp;#39;s rally &amp;amp; more") == "Japan's rally & more"
    assert S.clean("Japan#39;s exports") == "Japan's exports"
    assert S.clean("<b>Bold</b>  text\n") == "Bold text"


def test_to_ist_converts_timezones():
    assert S.to_ist("Sat, 18 Oct 2026 04:00:00 GMT") == pd.Timestamp("2026-10-18 09:30:00")
    assert S.to_ist("2026-10-18T09:30:00+05:30") == pd.Timestamp("2026-10-18 09:30:00")
    assert pd.isna(S.to_ist("not a date"))


# ---------------- RSS ----------------
def _rss(pub):
    return f"""<?xml version="1.0"?><rss><channel><item><title>Infosys Q2 profit rises 8%</title><link>https://x.com/a</link>
<pubDate>{pub}</pubDate><description>&lt;p&gt;Body&lt;/p&gt;</description></item></channel></rss>""".encode()


class FakePolite:
    def __init__(self, pages):
        self.pages, self.requests = pages, 0

    def get(self, url, check_robots=True, timeout=25):
        self.requests += 1
        v = self.pages[url]
        if isinstance(v, Exception):
            raise v
        return v


def test_parse_rss_fields():
    it = S.parse_rss(_rss("Sat, 18 Oct 2026 04:00:00 GMT"), {"name": "Test", "kind": "markets"})
    assert it[0]["title"] == "Infosys Q2 profit rises 8%" and it[0]["summary"] == "Body" and it[0]["source"] == "Test"


def test_fetch_rss_marks_dead_and_error_feeds():
    now = pd.Timestamp.now()
    fresh = (now - pd.Timedelta(hours=2)).strftime("%a, %d %b %Y %H:%M:%S +0530")
    old = (now - pd.Timedelta(days=40)).strftime("%a, %d %b %Y %H:%M:%S +0530")
    feeds = [{"name": "A", "url": "a"}, {"name": "B", "url": "b"}, {"name": "C", "url": "c"}, {"name": "D", "url": "d", "enabled": False}]
    p = FakePolite({"a": _rss(fresh), "b": _rss(old), "c": RuntimeError("boom")})
    items, health = S.fetch_rss(feeds, p)
    st = {h["source"]: h["status"] for h in health}
    assert st == {"A": "ok", "B": "dead", "C": "error"}
    assert len(items) == 1                                     # dead feed's items are not used


# ---------------- sitemaps ----------------
SITEMAP = b"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
<url><loc>https://site.com/markets/stocks/news/tcs-wins-big-deal/articleshow/123456789.cms</loc>
<news:news><news:publication_date>2026-10-17T10:15:00+05:30</news:publication_date><news:title>TCS wins big deal</news:title>
<news:stock_tickers>NSE:TCS</news:stock_tickers></news:news></url>
<url><loc>https://site.com/markets/infosys-shares-jump-after-results-12345678.html</loc><lastmod>2026-10-16</lastmod></url>
</urlset>"""
INDEX = b"""<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://site.com/sitemap/2026-10-17.xml</loc></sitemap><sitemap><loc>https://site.com/sitemap/2019-03.xml</loc></sitemap></sitemapindex>"""


def test_parse_sitemap_urlset_and_index():
    kind, e = S.parse_sitemap(SITEMAP)
    assert kind == "urlset" and len(e) == 2
    assert e[0]["title"] == "TCS wins big deal" and e[0]["tickers"] == "NSE:TCS"
    kind, k = S.parse_sitemap(INDEX)
    assert kind == "index" and [x["loc"] for x in k][1].endswith("2019-03.xml")


def test_title_from_url_and_period():
    assert S.title_from_url("https://site.com/markets/infosys-shares-jump-after-results-12345678.html") == "Infosys shares jump after results"
    a, b = S.sitemap_period("https://site.com/sitemap/2026-10-17.xml")
    assert a == pd.Timestamp("2026-10-17") and b == pd.Timestamp("2026-10-17")
    a, b = S.sitemap_period("https://site.com/sitemap/2019-03.xml")
    assert a == pd.Timestamp("2019-03-01") and b.month == 3 and b.day == 31
    assert S.sitemap_period("https://site.com/sitemap.xml") is None


class Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code, self.content = text, status, text.encode()

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, robots):
        self.robots, self.headers, self.got = robots, {}, []

    def get(self, url, timeout=None):
        self.got.append(url)
        return Resp(self.robots) if url.endswith("/robots.txt") else Resp("<rss/>")


def test_polite_obeys_robots_and_spacing():
    sess = FakeSession("User-agent: *\nDisallow: /private/\nCrawl-delay: 5\n")
    waits, t = [], [100.0]
    p = S.Polite(session=sess, gap=2, sleep=lambda s: waits.append(s), clock=lambda: t[0])
    with pytest.raises(PermissionError):
        p.get("https://x.com/private/a")
    p.get("https://x.com/news/a")
    p.get("https://x.com/news/b")
    assert waits and waits[-1] == pytest.approx(5)              # robots crawl-delay beats our 2s gap
    assert "https://x.com/private/a" not in sess.got


# ---------------- tagging ----------------
def test_tagger_names_tickers_aliases(tagger):
    assert tagger.find("Reliance Industries shares rise") == ["RELIANCE"]
    assert tagger.find("SBI cuts lending rates") == ["SBIN"]
    assert tagger.find("L&T bags order worth Rs 2,000 crore") == ["LT"]
    assert tagger.find("TCS and Infosys lead IT rally") == ["TCS", "INFY"]
    assert tagger.find("persistent selling drags market") == []          # lowercase single word is not a company


def test_enrich_event_type_tone_roundup(tagger):
    raw = pd.DataFrame([dict(published="2026-10-17 10:00", source="X", title="Infosys Q2 results: profit jumps 12%", summary="", link="a"),
                        dict(published="2026-10-17 11:00", source="X", title="Stocks to watch: TCS, Infosys, SBI, L&T, Reliance", summary="", link="b"),
                        dict(published="2026-10-17 12:00", source="X", title="Rupee slips", summary="Reliance Industries said...", link="c")])
    d = N.enrich(raw, tagger, UNI)
    assert d.event_type.iat[0] == "Results" and d.tone.iat[0] == "positive" and d.symbols.iat[0] == "INFY"
    assert bool(d.roundup.iat[1]) and not bool(d.roundup.iat[0])
    assert d.symbols.iat[2] == ""                             # a summary naming a company is not evidence: headline only
    assert d.tone.iat[1] == "" and d.tone.iat[2] == ""        # no tone for a round-up or a headline about no stock


def test_merge_dedupes_and_counts_sources(tagger):
    a = N.enrich(pd.DataFrame([dict(published="2026-10-17 10:00", source="ET", title="TCS wins big deal", link="a")]), tagger, UNI)
    b = N.enrich(pd.DataFrame([dict(published="2026-10-17 09:00", source="Mint", title="TCS wins big deal!", link="b"),
                               dict(published="2026-10-17 09:30", source="BS", title="Something else", link="c")]), tagger, UNI)
    m = N.merge(a, b)
    assert len(m) == 2
    row = m[m.title.str.startswith("TCS")].iloc[0]
    assert row.source == "Mint" and row.n_sources == 2         # earliest kept, two sites counted


# ---------------- NSE filings backfill ----------------
class FakeNSE:
    def __init__(self, fail=False):
        self.count, self.urls, self.fail = 0, [], fail

    def get_json(self, url):
        self.count += 1
        self.urls.append(url)
        if self.fail:
            raise RuntimeError("down")
        return {"data": [{"symbol": "TCS", "desc": "Outcome of Board Meeting", "attchmntText": "Dividend declared", "an_dt": "10-Jan-2025 17:00:00"}]}


def test_announcements_backfill_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "STORE", tmp_path / "ann.parquet")
    monkeypatch.setattr(A, "BACKFILL_STATE", tmp_path / "bf.json")
    s = FakeNSE()
    A.backfill(["TCS", "INFY"], start="2024-01-01", end="2025-06-30", session=s, log=lambda *_: None)
    assert s.count == 4                                        # 2 stocks x 2 calendar years
    assert "2025" in s.urls[0] and "TCS" in s.urls[0]          # newest year first
    assert len(A.load()) >= 1
    s2 = FakeNSE()
    A.backfill(["TCS", "INFY"], start="2024-01-01", end="2025-07-15", session=s2, log=lambda *_: None)
    assert s2.count == 0                                       # a later day resumes, nothing re-downloaded


def test_announcements_backfill_stops_when_nse_down(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "STORE", tmp_path / "ann.parquet")
    monkeypatch.setattr(A, "BACKFILL_STATE", tmp_path / "bf.json")
    s = FakeNSE(fail=True)
    A.backfill([f"S{i}" for i in range(50)], start="2020-01-01", end="2025-06-30", session=s, log=lambda *_: None)
    assert s.count == 20


def test_company_info_parse():
    j = {"info": {"isin": "INE467B01029", "listingDate": "25-Aug-2004"},
         "industryInfo": {"macro": "Information Technology", "sector": "Information Technology", "industry": "IT - Services",
                          "basicIndustry": "Computers - Software & Consulting"},
         "securityInfo": {"issuedSize": "3618087518"}}
    r = CI.parse_quote("TCS", j)
    assert r["basic_industry"].startswith("Computers") and r["shares"] == 3618087518.0 and r["isin"] == "INE467B01029"
    with pytest.raises(ValueError):
        CI.parse_quote("TCS", {"unexpected": 1})


# ---------------- event study ----------------
def test_event_day_alignment():
    d = pd.DatetimeIndex(pd.to_datetime(["2026-10-15", "2026-10-16", "2026-10-19"]))
    assert E.event_day("2026-10-15 10:00", d) == pd.Timestamp("2026-10-15")
    assert E.event_day("2026-10-15 15:30", d) == pd.Timestamp("2026-10-15")
    assert E.event_day("2026-10-15 15:31", d) == pd.Timestamp("2026-10-16")      # after the close -> next session
    assert E.event_day("2026-10-17 09:00", d) == pd.Timestamp("2026-10-19")      # weekend -> Monday
    assert E.event_day("2026-10-16 00:00", d) == pd.Timestamp("2026-10-16")      # date-only stamp -> that day
    assert pd.isna(E.event_day("2026-10-19 18:00", d))                            # no later session in the data


def _panel():
    dates = pd.bdate_range("2024-01-01", periods=120)
    rng = np.random.default_rng(3)
    px = []
    for s in ("AAA", "BBB"):
        r = rng.normal(0, 0.01, len(dates))
        if s == "AAA":
            r[60] = 0.08                                         # the news day
            r[61:66] += 0.004                                    # drift after
        px.append(pd.DataFrame(dict(date=dates, symbol=s, close=100 * np.exp(np.cumsum(r)), volume=1e6 * (1 + 4 * (np.arange(len(dates)) == 60)))))
    idx = pd.DataFrame(dict(date=dates, nifty=10000.0))           # flat index: abnormal = raw return
    return pd.concat(px), idx, dates


def test_car_windows_and_volume():
    px, idx, dates = _panel()
    P = E.returns_panel(px, idx)
    ev = pd.DataFrame(dict(symbol=["AAA"], published=[dates[60] + pd.Timedelta(hours=10)], source_type=["news"], category=["Results"],
                           tone=["positive"], title=["x"]))
    m = E.measure(ev, P)
    close = px[px.symbol == "AAA"].set_index("date").close
    r = close.pct_change()
    assert m.ar0.iat[0] == pytest.approx(100 * r.iloc[60])
    assert m.car_1_5.iat[0] == pytest.approx(100 * r.iloc[61:66].sum())
    assert m.abn_volume.iat[0] == pytest.approx(5.0)


def test_run_reports_bigger_moves_on_event_days():
    px, idx, dates = _panel()
    news = pd.DataFrame(dict(published=[dates[60] + pd.Timedelta(hours=10)], symbols=["AAA"], event_type=["Results"], tone=["positive"],
                             title=["AAA results"], roundup=[False]))
    res = E.run(px, idx, news, None, min_events=1)
    s = res["summary"].iloc[0]
    assert s.times_bigger > 3 and res["coverage"]["events"] == 1
