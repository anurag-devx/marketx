"""Where news comes from, and how it is fetched politely.

Three kinds of source, all published by the sites for machines:
  rss       section RSS feeds (markets, companies, earnings, economy...). Fetched like any feed reader.
  sitemap   news sitemaps (articles of the last ~2 days) and dated archive sitemaps (older articles), found
            through each site's robots.txt. Only headline, time, keywords and link are read; article pages
            are never downloaded. robots.txt rules are obeyed and requests to a site are spaced out.
  gnews     Google News search feed, one query per company (optional, off by default). Also robots-checked.

Nothing here logs in, gets around a paywall, or downloads full articles.
Feed URLs change over time: check_sources() reports which ones work, and dead feeds are skipped, never mixed in."""
import html, json, re, time
import urllib.robotparser as robotparser
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, quote
import pandas as pd
from .config import DATA

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) swing-research/1.0 (personal research; headlines only)"
HDRS = {"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"}
STATE = DATA / "news" / "sources_state.json"
DEAD_DAYS = 7                      # newest item older than this -> feed is dead
HOST_GAP = 2.0                     # seconds between requests to the same site (unless robots.txt asks for more)

# Candidate section feeds. Some may have moved or died; check_sources() and every fetch say which work.
FEEDS = [
    dict(name="ET Markets", url="https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", kind="markets"),
    dict(name="ET Stocks", url="https://economictimes.indiatimes.com/markets/stocks/news/rssfeeds/2146842.cms", kind="companies"),
    dict(name="ET Earnings", url="https://economictimes.indiatimes.com/markets/stocks/earnings/rssfeeds/2143429.cms", kind="earnings"),
    dict(name="ET Industry", url="https://economictimes.indiatimes.com/industry/rssfeeds/13352306.cms", kind="companies"),
    dict(name="ET Economy", url="https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms", kind="economy"),
    dict(name="Livemint Markets", url="https://www.livemint.com/rss/markets", kind="markets"),
    dict(name="Livemint Companies", url="https://www.livemint.com/rss/companies", kind="companies"),
    dict(name="Livemint Economy", url="https://www.livemint.com/rss/economy", kind="economy"),
    dict(name="Business Standard Markets", url="https://www.business-standard.com/rss/markets-106.rss", kind="markets"),
    dict(name="Business Standard Companies", url="https://www.business-standard.com/rss/companies-101.rss", kind="companies"),
    dict(name="Business Standard Economy", url="https://www.business-standard.com/rss/economy-102.rss", kind="economy"),
    dict(name="BusinessLine Markets", url="https://www.thehindubusinessline.com/markets/feeder/default.rss", kind="markets"),
    dict(name="BusinessLine Companies", url="https://www.thehindubusinessline.com/companies/feeder/default.rss", kind="companies"),
    dict(name="BusinessLine Economy", url="https://www.thehindubusinessline.com/economy/feeder/default.rss", kind="economy"),
    dict(name="Financial Express Markets", url="https://www.financialexpress.com/market/feed/", kind="markets"),
    dict(name="Financial Express Industry", url="https://www.financialexpress.com/business/industry/feed/", kind="companies"),
    dict(name="Moneycontrol Markets", url="https://www.moneycontrol.com/rss/marketreports.xml", kind="markets"),
    dict(name="Moneycontrol Business", url="https://www.moneycontrol.com/rss/business.xml", kind="companies"),
    dict(name="BBC World", url="https://feeds.bbci.co.uk/news/world/rss.xml", kind="world"),
]
# Sites whose news / archive sitemaps are read (found via robots.txt; never guessed).
SITEMAP_SITES = ["https://economictimes.indiatimes.com", "https://www.livemint.com", "https://www.business-standard.com",
                 "https://www.thehindubusinessline.com", "https://www.financialexpress.com", "https://www.moneycontrol.com"]
# Only articles under these parts of a site are kept from sitemaps (finance, not lifestyle or sport).
SECTION_RX = re.compile(r"/(markets?|stocks?|companies|company|industry|economy|earnings|results|ipo|business|money|"
                        r"corporate|banking|finance|commodit\w*|mutual-funds?|sme|news/economy|infra\w*)(/|-)", re.I)
SKIP_RX = re.compile(r"/(sports?|cricket|entertainment|lifestyle|movies?|tv|astrology|technology/gadgets|web-stories|"
                     r"photos?|videos?|podcast|auto/news/cars|travel|food|health|education|jobs|horoscope)/", re.I)


# ---------------- text helpers ----------------
def clean(t):
    """Strips tags and decodes HTML entities, including double-encoded ones (&amp;#39; and stray #39;)."""
    t = t or ""
    for _ in range(3):
        u = html.unescape(t)
        if u == t:
            break
        t = u
    t = re.sub(r"(?<!&)#(\d{2,5});", lambda m: chr(int(m.group(1))), t)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def to_ist(value):
    """Any feed/sitemap time -> naive Asia/Kolkata timestamp (NaT if unparseable)."""
    if value is None or value == "":
        return pd.NaT
    try:
        ts = parsedate_to_datetime(value)
        ts = pd.Timestamp(ts)
    except Exception:
        ts = pd.to_datetime(value, errors="coerce", utc=False)
    if pd.isna(ts):
        return pd.NaT
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        return ts                                   # assume already local (Indian sites publish IST)
    return ts.tz_convert("Asia/Kolkata").tz_localize(None)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


# ---------------- politeness ----------------
class Polite:
    """robots.txt checks and per-site spacing. One instance per run."""
    def __init__(self, session=None, gap=HOST_GAP, sleep=time.sleep, clock=time.monotonic):
        import requests
        self.s = session or requests.Session()
        self.s.headers.update(HDRS)
        self.gap, self._sleep, self._clock = gap, sleep, clock
        self.robots, self.last, self.requests = {}, {}, 0

    def rules(self, url):
        host = "{0.scheme}://{0.netloc}".format(urlparse(url))
        if host not in self.robots:
            rp = robotparser.RobotFileParser()
            try:
                r = self.s.get(host + "/robots.txt", timeout=15)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except Exception:
                rp.parse([])                         # unreachable robots.txt: treat as no rules, keep spacing
            self.robots[host] = rp
        return self.robots[host]

    def allowed(self, url):
        return self.rules(url).can_fetch(UA, url)

    def sitemaps(self, site):
        return list(self.rules(site + "/").site_maps() or [])

    def get(self, url, check_robots=True, timeout=25):
        if check_robots and not self.allowed(url):
            raise PermissionError(f"robots.txt does not allow {url}")
        host = urlparse(url).netloc
        delay = max(self.gap, float(self.rules(url).crawl_delay(UA) or 0)) if check_robots else self.gap
        if host in self.last:
            wait = delay - (self._clock() - self.last[host])
            if wait > 0:
                self._sleep(wait)
        self.last[host] = self._clock()
        self.requests += 1
        r = self.s.get(url, timeout=timeout)
        r.raise_for_status()
        return r.content


# ---------------- RSS ----------------
def parse_rss(content, feed):
    """RSS or Atom bytes -> list of items (dicts)."""
    root = ET.fromstring(content)
    out = []
    for it in [e for e in root.iter() if _local(e.tag) in ("item", "entry")]:
        f = {_local(c.tag): c for c in it}
        title = clean(f["title"].text if "title" in f else "")
        if not title:
            continue
        link = (f["link"].text or f["link"].get("href", "")) if "link" in f else ""
        pub = next((f[k].text for k in ("pubDate", "published", "updated", "date") if k in f and f[k].text), None)
        desc = next((f[k].text for k in ("description", "summary", "content") if k in f and f[k].text), "")
        out.append(dict(published=to_ist(pub), source=feed["name"], kind=feed.get("kind", "markets"), title=title,
                        summary=clean(desc)[:500], link=(link or "").strip(), keywords="", tickers="", method="rss"))
    return out


def fetch_rss(feeds=None, polite=None):
    """Returns (items, health rows). A feed with no items, or whose newest item is older than DEAD_DAYS, is dead."""
    polite = polite or Polite(gap=0.5)
    items, health = [], []
    now = pd.Timestamp.now()
    for f in feeds or FEEDS:
        if f.get("enabled") is False:
            continue
        row = dict(source=f["name"], method="rss", url=f["url"], status="", items=0, newest=None, note="")
        try:
            got = parse_rss(polite.get(f["url"], check_robots=False), f)
            newest = max((g["published"] for g in got if pd.notna(g["published"])), default=pd.NaT)
            row.update(items=len(got), newest=str(newest)[:16] if pd.notna(newest) else None)
            if not got:
                row.update(status="dead", note="no items")
            elif pd.notna(newest) and (now - newest).days > DEAD_DAYS:
                row.update(status="dead", note=f"newest item is {(now - newest).days} days old: feed no longer updated")
            else:
                row["status"] = "ok"
                items += got
        except Exception as e:
            row.update(status="error", note=f"{type(e).__name__}: {str(e)[:120]}")
        health.append(row)
    return items, health


# ---------------- sitemaps ----------------
def parse_sitemap(content):
    """Returns ('index', [child urls]) or ('urlset', [entries]). Entries: loc, lastmod, published, title, keywords, tickers."""
    root = ET.fromstring(content)
    kind = _local(root.tag)
    if kind == "sitemapindex":
        kids = []
        for sm in root:
            f = {_local(c.tag): c.text for c in sm}
            if f.get("loc"):
                kids.append(dict(loc=f["loc"].strip(), lastmod=f.get("lastmod")))
        return "index", kids
    entries = []
    for u in root:
        e = dict(loc="", lastmod=None, published=None, title="", keywords="", tickers="")
        for c in u.iter():
            n = _local(c.tag)
            if n == "loc" and not e["loc"] and c.text:
                e["loc"] = c.text.strip()
            elif n == "lastmod":
                e["lastmod"] = c.text
            elif n == "publication_date":
                e["published"] = c.text
            elif n == "title" and c.text and not e["title"]:
                e["title"] = clean(c.text)
            elif n == "keywords" and c.text:
                e["keywords"] = clean(c.text)
            elif n == "stock_tickers" and c.text:
                e["tickers"] = clean(c.text)
        if e["loc"]:
            entries.append(e)
    return "urlset", entries


def title_from_url(url):
    """Headline from the article's address when the sitemap gives none: last path part, ids removed."""
    seg = [s for s in urlparse(url).path.split("/") if s]
    if not seg:
        return ""
    cands = [s for s in seg if "-" in s and not re.fullmatch(r"[\d\-]+", s)]
    s = (cands[-1] if cands else seg[-1])
    s = re.sub(r"\.(cms|html?|php|aspx)$", "", s)
    s = re.sub(r"(-|_)?(articleshow|newsid|amp)?[-_]?\d{5,}$", "", s)
    s = re.sub(r"-\d{5,}-\d+$|-\d{5,}$", "", s)
    words = [w for w in re.split(r"[-_]+", s) if w]
    return " ".join(words).strip().capitalize()


DATE_PATTERNS = [
    (re.compile(r"(20\d\d)[-_/](0[1-9]|1[0-2])[-_/](0[1-9]|[12]\d|3[01])"), "ymd"),
    (re.compile(r"(20\d\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])"), "ymd"),
    (re.compile(r"(20\d\d)[-_](january|february|march|april|may|june|july|august|september|october|november|december)[-_](\d{1,2})", re.I), "ymonthd"),
    (re.compile(r"(20\d\d)[-_/](0[1-9]|1[0-2])(?!\d)"), "ym"),
    (re.compile(r"(january|february|march|april|may|june|july|august|september|october|november|december)[-_](20\d\d)", re.I), "monthy"),
]


def sitemap_period(url):
    """(start, end) dates a dated archive sitemap covers, from its address; None if it has no date."""
    for rx, kind in DATE_PATTERNS:
        m = rx.search(url)
        if not m:
            continue
        try:
            if kind == "ymd":
                d = pd.Timestamp(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                return d, d
            if kind == "ymonthd":
                d = pd.Timestamp(f"{m.group(3)} {m.group(2)} {m.group(1)}")
                return d, d
            if kind == "ym":
                d = pd.Timestamp(int(m.group(1)), int(m.group(2)), 1)
                return d, d + pd.offsets.MonthEnd(0)
            if kind == "monthy":
                d = pd.Timestamp(f"1 {m.group(1)} {m.group(2)}")
                return d, d + pd.offsets.MonthEnd(0)
        except Exception:
            continue
    return None


def entries_to_items(entries, site, method):
    name = urlparse(site).netloc.replace("www.", "")
    out = []
    for e in entries:
        loc = e["loc"]
        if SKIP_RX.search(loc) or not SECTION_RX.search(loc):
            continue
        title = e["title"] or title_from_url(loc)
        if len(title) < 15:
            continue
        pub = to_ist(e["published"] or e["lastmod"])
        out.append(dict(published=pub, source=f"{name} ({'news sitemap' if method == 'sitemap' else 'archive'})", kind="sitemap",
                        title=title, summary="", link=loc, keywords=e.get("keywords", ""), tickers=e.get("tickers", ""), method=method))
    return out


def fetch_news_sitemaps(sites=None, polite=None, max_requests=60):
    """Recent articles from each site's news sitemap(s) listed in robots.txt. Returns (items, health)."""
    polite = polite or Polite()
    items, health = [], []
    for site in sites or SITEMAP_SITES:
        row = dict(source=urlparse(site).netloc, method="sitemap", url=site + "/robots.txt", status="", items=0, newest=None, note="")
        try:
            sms = [u for u in polite.sitemaps(site) if re.search(r"news", u, re.I)]
            if not sms:
                row.update(status="none", note="robots.txt lists no news sitemap"); health.append(row); continue
            got, used = [], 0
            queue = list(sms)
            while queue and used < 8 and polite.requests < max_requests:
                u = queue.pop(0)
                used += 1
                kind, data = parse_sitemap(polite.get(u))
                if kind == "index":
                    queue += [k["loc"] for k in data[:4]]
                else:
                    got += entries_to_items(data, site, "sitemap")
            newest = max((g["published"] for g in got if pd.notna(g["published"])), default=pd.NaT)
            row.update(status="ok" if got else "empty", items=len(got), newest=str(newest)[:16] if pd.notna(newest) else None,
                       note=f"{used} sitemap file(s)")
            items += got
        except PermissionError as e:
            row.update(status="blocked", note=str(e)[:120])
        except Exception as e:
            row.update(status="error", note=f"{type(e).__name__}: {str(e)[:120]}")
        health.append(row)
    return items, health


def archive_sitemaps(site, polite, start, end, max_index_files=40):
    """Dated archive sitemap URLs of a site that fall in [start, end], found by walking its sitemap indexes."""
    found, seen = [], set()
    queue = [u for u in polite.sitemaps(site)]
    reads = 0
    while queue and reads < max_index_files:
        u = queue.pop(0)
        if u in seen:
            continue
        seen.add(u)
        per = sitemap_period(u)
        if per is not None and not (per[1] < start or per[0] > end):
            if not re.search(r"(video|photo|image|web-?stor|amp)", u, re.I):
                found.append((u, per))
            continue
        if per is not None:
            continue                                   # dated but outside the range
        try:
            reads += 1
            kind, data = parse_sitemap(polite.get(u))
        except Exception:
            continue
        if kind == "index":
            for k in data:
                p = sitemap_period(k["loc"])
                if p is None or not (p[1] < start or p[0] > end):
                    queue.append(k["loc"])
    return sorted(set(found), key=lambda x: x[1][0], reverse=True)


# ---------------- Google News search (optional) ----------------
GNEWS = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"


def fetch_gnews(company, polite=None, days=30, after=None, before=None, exact=True):
    """Headlines mentioning one company (exact phrase) or any query (exact=False). after:/before: for older periods. Robots-checked."""
    polite = polite or Polite()
    q = (f'"{company}"' if exact else company) + (f" after:{after} before:{before}" if after else f" when:{days}d")
    url = GNEWS.format(q=quote(q))
    items = parse_rss(polite.get(url), dict(name="Google News", kind="search"))
    for i in items:
        i["method"] = "gnews"
        # Google News titles end with " - Publisher"
        m = re.match(r"^(.*)\s+-\s+([^-]{2,60})$", i["title"])
        if m:
            i["title"], i["source"] = m.group(1).strip(), f"Google News: {m.group(2).strip()}"
    return items


# ---------------- health ----------------
def save_state(health):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(dict(checked=str(pd.Timestamp.now().floor("s")), sources=health), indent=1, default=str))


def load_state():
    try:
        return json.loads(STATE.read_text()) if STATE.exists() else None
    except Exception:
        return None


def check_sources(include_gnews=True):
    """Tries every source once and reports what works (used by the dashboard's Check news sources button)."""
    p = Polite()
    _, h1 = fetch_rss(polite=p)
    _, h2 = fetch_news_sitemaps(polite=p)
    health = h1 + h2
    if include_gnews:
        row = dict(source="Google News search", method="gnews", url=GNEWS.format(q="Infosys"), status="", items=0, newest=None, note="")
        try:
            got = fetch_gnews("Infosys", p, days=7)
            row.update(status="ok" if got else "empty", items=len(got))
        except PermissionError as e:
            row.update(status="blocked", note=str(e)[:120])
        except Exception as e:
            row.update(status="error", note=f"{type(e).__name__}: {str(e)[:120]}")
        health.append(row)
    save_state(health)
    return health
