"""News store: every headline we fetch is cleaned, linked to stocks, labelled, de-duplicated and kept.

    data/news/news.parquet         all headlines ever fetched (history grows with every update and backfill)
    data/news/backfill_state.json  which archive sitemaps were already read (backfill resumes)
    data/news_aliases.csv          your own extra names for stocks: symbol,alias  (e.g. LTIM,LTIMindtree)

Stock matching (Tagger): a headline is linked to a stock when it contains
  - the company's name without "Ltd/Limited" (e.g. "Tata Consultancy Services"), or a distinctive short form
    (first two words; or the first word if no other listed company starts with it, e.g. "Infosys"),
  - a known short name (SBI, L&T, M&M, Airtel, Nykaa ... and your aliases file),
  - or the NSE symbol in capitals (e.g. "TCS"), except symbols that are also common words or exchange names
    (BSE, IDEA ...), which need the company name.
Longer matches win: "SBI Life" links SBILIFE, not SBIN. Headlines naming 4+ stocks, or "stocks to watch"-type
lists, are marked as roundups (kept, but excluded from the event study by default).

Event type: what the headline is about (Results, Order / contract, Broker view, ...), from keyword rules.
Tone: rough positive / negative / neutral from words in the headline. Both are reading aids, not signals."""
import hashlib, json, re, time
import pandas as pd
from .config import DATA
from . import news_sources as S
from . import atomic

DIR = DATA / "news"
STORE = DIR / "news.parquet"
LEGACY = DATA / "news.json"
ALIASES = DATA / "news_aliases.csv"
BACKFILL = DIR / "backfill_state.json"
COLS = ["id", "published", "source", "kind", "method", "title", "summary", "link", "keywords", "tickers", "symbols", "sectors",
        "event_type", "tone", "roundup", "n_sources", "fetched"]

BUILTIN_ALIASES = {
    "SBIN": ["SBI", "State Bank of India"], "LT": ["L&T", "Larsen & Toubro", "Larsen and Toubro"], "M&M": ["M&M", "Mahindra & Mahindra", "Mahindra and Mahindra"],
    "BHARTIARTL": ["Airtel", "Bharti Airtel"], "HINDUNILVR": ["HUL", "Hindustan Unilever"], "RELIANCE": ["RIL", "Reliance Industries"],
    "ICICIBANK": ["ICICI Bank"], "HDFCBANK": ["HDFC Bank"], "KOTAKBANK": ["Kotak Mahindra Bank", "Kotak Bank"], "AXISBANK": ["Axis Bank"],
    "INFY": ["Infosys"], "TCS": ["TCS", "Tata Consultancy"], "HCLTECH": ["HCL Tech", "HCLTech", "HCL Technologies"], "TECHM": ["Tech Mahindra", "TechM"],
    "LTIM": ["LTIMindtree", "LTI Mindtree"], "WIPRO": ["Wipro"], "MARUTI": ["Maruti Suzuki", "Maruti"], "TMPV": ["Tata Motors"],
    "BAJFINANCE": ["Bajaj Finance"], "BAJAJFINSV": ["Bajaj Finserv"], "BAJAJ-AUTO": ["Bajaj Auto"], "ONGC": ["ONGC"], "NTPC": ["NTPC"],
    "BHEL": ["BHEL"], "HAL": ["HAL", "Hindustan Aeronautics"], "BEL": ["BEL", "Bharat Electronics"], "IOC": ["IOC", "Indian Oil"],
    "BPCL": ["BPCL"], "HINDPETRO": ["HPCL", "Hindustan Petroleum"], "COALINDIA": ["Coal India"], "ADANIENT": ["Adani Enterprises"],
    "ADANIPORTS": ["Adani Ports"], "ETERNAL": ["Zomato", "Eternal"], "NYKAA": ["Nykaa"], "PAYTM": ["Paytm", "One 97"],
    "POLICYBZR": ["PB Fintech", "Policybazaar"], "MOTILALOFS": ["MOFSL", "Motilal Oswal"], "SBILIFE": ["SBI Life"], "SBICARD": ["SBI Card", "SBI Cards"],
    "HDFCLIFE": ["HDFC Life"], "ICICIPRULI": ["ICICI Pru Life", "ICICI Prudential Life"], "ICICIGI": ["ICICI Lombard"], "IDEA": ["Vodafone Idea"],
    "BSE": ["BSE Ltd", "BSE shares", "BSE stock"], "UJJIVANSFB": ["Ujjivan SFB", "Ujjivan Small Finance"], "PCJEWELLER": ["PC Jeweller"],
    "JIOFIN": ["Jio Financial", "Jio Finance"], "INDIGO": ["IndiGo", "InterGlobe Aviation"], "DMART": ["DMart", "Avenue Supermarts"],
    "TATASTEEL": ["Tata Steel"], "TATAPOWER": ["Tata Power"], "TITAN": ["Titan"], "SUNPHARMA": ["Sun Pharma"], "DRREDDY": ["Dr Reddy", "Dr. Reddy"],
}
AMBIGUOUS_TICKERS = {"BSE", "IDEA", "INDIA"}      # symbols that are also common words or exchange names
COMMON_WORDS = {"india", "indian", "national", "central", "union", "punjab", "bank", "power", "capital", "finance", "general",
                "gujarat", "bharat", "hindustan", "oil", "steel", "max", "gold", "coal", "global", "new", "life", "home", "first",
                "great", "star", "sun", "jubilant", "kalyan", "aditya", "birla", "tata", "adani", "bajaj", "mahindra", "reliance",
                "hdfc", "icici", "kotak", "state", "the", "industries", "international", "energy", "infra", "housing", "motors",
                "nippon", "japan", "asian", "american", "british", "royal", "premier", "supreme", "prime", "metro", "united"}
SUFFIX = re.compile(r"\b(ltd|limited|ltd\.|pvt|private|inc|corporation|corp|company|co)\b\.?", re.I)
RESULTS_RX = re.compile(r"\bq[1-4]\b.{0,40}(result|profit|loss|revenue|earning|preview|numbers)|quarterly (result|earnings|profit)|"
                        r"\b(profit|pat|ebitda|revenue|net income|nii)\b.{0,25}\d+(\.\d+)?\s?%|"
                        r"(net )?profit (rises|falls|jumps|drops|surges|declines|up|down)|\bresults?:|earnings (beat|miss)|\bpat\b.{0,20}(rises|falls|jumps|drops)", re.I)
NEWS_TYPES = [
    ("Broker view", r"target price|price target|\bupgrade|\bdowngrade|initiat\w* coverage|buy rating|sell rating|\boverweight|\bunderweight|brokerage|\bcall on\b"),
    ("Block / bulk deal", r"block deal|bulk deal|stake sale|offloads|\bofs\b|offer for sale|sells stake|buys stake"),
    ("IPO", r"\bipo\b|\bgmp\b|subscription status|subscribed \d|listing (gain|day|price)|lists at"),
]
ROUNDUP_RX = re.compile(r"stocks to (watch|buy)|top (gainers|losers)|market wrap|buzzing stocks|\b\d+ stocks\b|stocks in news|"
                        r"sensex|nifty 50 today|closing bell|opening bell|market live|stock market today", re.I)

# ---------------- tone ----------------
POS = (r"surge[sd]?|surging|soar(s|ed|ing)?|jump(s|ed)?|rall(y|ies|ied)|gains?|gained|rise[sn]?|rising|record high|all-time high|"
       r"beats?|upgrade[sd]?|wins?|won|bags?|bagged|strong(er)?|outperform(s|ed)?|approv(al|ed|es)|expan(d|ds|sion)|upbeat|robust")
NEG = (r"plunge[sd]?|slump(s|ed)?|crash(es|ed)?|tumble[sd]?|falls?|fell|drops?|dropped|slides?|slid|sinks?|sank|downgrade[sd]?|"
       r"miss(es|ed)?|loss(es)?|probe|raids?|fraud|penalty|penalties|fined|fines|default(s|ed)?|resign(s|ed|ation)?|weak(er|ness)?|"
       r"cuts?|bans?|banned|halts?|halted|recalls?|warning|lawsuit|sell-?off|slips?|slipped|declines?|declined")
_POS, _NEG = re.compile(rf"\b(?:{POS})\b", re.I), re.compile(rf"\b(?:{NEG})\b", re.I)


def tone(text):
    t = str(text or "")
    p, n = len(_POS.findall(t)), len(_NEG.findall(t))
    return "positive" if p > n else ("negative" if n > p else "neutral")


NEWS_SKIP_CATEGORIES = {"Investor meet / call", "Routine compliance", "Shareholding / insider", "Management change"}
STRONG = re.compile(r"conference call|con-?call|analyst meet|resigns?|steps down|appoint\w* (as )?(ceo|md|cfo|chairman)|"
                    r"pledge|insider trading", re.I)


def event_type(text):
    """What a headline is about. Announcement categories that are too broad for news (investor, director...)
    only count with a strong phrase."""
    from .announcements import categorise
    t = str(text or "")
    c = categorise(t)
    if c in NEWS_SKIP_CATEGORIES and not STRONG.search(t):
        c = "Other"
    if c == "Results" or RESULTS_RX.search(t):
        return "Results"
    for name, rx in NEWS_TYPES:
        if re.search(rx, t, re.I):
            return name
    if c != "Other":
        return c
    return "Market commentary" if ROUNDUP_RX.search(t) else "Other"


# ---------------- tagging ----------------
class Tagger:
    def __init__(self, universe, instruments=None, extra=None):
        names = dict(zip(universe.Symbol, universe["Company Name"]))
        if instruments is not None and len(instruments):
            for s, n in zip(instruments.symbol, instruments.get("name", instruments.symbol)):
                names.setdefault(s, n)
        firsts = pd.Series([self._base(n).split()[0].lower() for n in names.values() if self._base(n)]).value_counts()
        alias = {}
        for sym, name in names.items():
            base = self._base(name)
            words = base.split()
            cand = set()
            if len(base) >= 5:
                cand.add(base)
            if len(words) >= 2 and words[1].lower() not in ("of", "and", "&", "the", "india") and len(" ".join(words[:2])) >= 6:
                cand.add(" ".join(words[:2]))
            if words and len(words[0]) >= 5 and words[0].lower() not in COMMON_WORDS and firsts.get(words[0].lower(), 0) == 1:
                cand.add(words[0])
            for a in cand:
                alias.setdefault(a.lower(), sym)
        for sym, al in BUILTIN_ALIASES.items():
            if sym in names:
                for a in al:
                    alias[a.lower()] = sym
        if extra is not None and len(extra):
            for s, a in zip(extra.symbol, extra.alias):
                if isinstance(a, str) and a.strip():
                    alias[a.strip().lower()] = str(s).strip()
        self.alias = alias
        keys = sorted(alias, key=len, reverse=True)
        self.name_rx = re.compile(r"(?<![\w&])(" + "|".join(re.escape(k) for k in keys) + r")(?![\w&])", re.I) if keys else None
        tick = sorted([s for s in names if len(s) >= 3 and s not in AMBIGUOUS_TICKERS and re.fullmatch(r"[A-Z0-9&\-]+", s)], key=len, reverse=True)
        self.tick_rx = re.compile(r"(?<![\w&])(" + "|".join(re.escape(t) for t in tick) + r")(?![\w&])") if tick else None
        self.symbols = set(names)

    @staticmethod
    def _base(name):
        b = SUFFIX.sub("", str(name or "")).replace("(India)", "").replace("(I)", "")
        return re.sub(r"\s+", " ", re.sub(r"[.,]", " ", b)).strip()

    def find(self, text):
        t = str(text or "")
        hits = []
        if self.name_rx:
            for m in self.name_rx.finditer(t):
                txt = m.group(1)
                if " " not in txt and not txt[0].isupper():
                    continue                       # one-word names must be capitalised: "persistent selling" is not Persistent Systems
                hits.append((m.start(), m.end(), self.alias[txt.lower()]))
        if self.tick_rx:
            hits += [(m.start(), m.end(), m.group(1)) for m in self.tick_rx.finditer(t)]
        hits.sort(key=lambda h: (-(h[1] - h[0]), h[0]))
        taken, keep = [], []
        for a, b, s in hits:
            if any(a < y and b > x for x, y in taken):
                continue
            taken.append((a, b))
            keep.append((a, s))
        out = []
        for _, s in sorted(keep):                  # in the order the headline names them
            if s not in out:
                out.append(s)
        return out


def load_tagger():
    uni = pd.read_csv(DATA / "universe.csv")
    inst = pd.read_csv(DATA / "instruments.csv", dtype=str) if (DATA / "instruments.csv").exists() else None
    extra = pd.read_csv(ALIASES, dtype=str) if ALIASES.exists() else None
    return Tagger(uni, inst, extra), uni


def enrich(items, tagger=None, universe=None):
    """items: DataFrame of raw headlines -> adds symbols, sectors, event_type, tone, roundup, id."""
    if items is None or len(items) == 0:
        return pd.DataFrame(columns=COLS)
    if tagger is None:
        tagger, universe = load_tagger()
    d = items.copy()
    for c in ("summary", "keywords", "tickers", "kind", "method", "link"):
        if c not in d:
            d[c] = ""
        d[c] = d[c].fillna("")
    d["title"] = d.title.map(S.clean)
    d["summary"] = d.summary.map(S.clean)
    sec = dict(zip(universe.Symbol, universe.Industry)) if universe is not None else {}
    syms = []
    for t, s, tk in zip(d.title, d.summary, d.tickers):
        found = tagger.find(t)
        if not found:                              # the summary only counts when the headline names no stock,
            found = [x for x in tagger.find(s)[:2] if True][:1]   # and then only its first company
        for x in re.findall(r"(?:NSE|BOM|BSE)\s*:\s*([A-Z0-9&\-]+)", tk or ""):
            if x in tagger.symbols and x not in found:
                found.append(x)
        syms.append(found)
    d["symbols"] = [",".join(x) for x in syms]
    d["sectors"] = [",".join(sorted({sec.get(x, "") for x in xs} - {""})) for xs in syms]
    d["event_type"] = d.title.map(event_type)          # the headline says what the story is about; summaries wander
    d["tone"] = d.title.map(tone)
    d["roundup"] = [len(xs) >= 4 or bool(ROUNDUP_RX.search(t)) for xs, t in zip(syms, d.title)]
    d["published"] = pd.to_datetime(d.published, errors="coerce")
    d["id"] = [hashlib.sha1(_norm(t).encode()).hexdigest()[:16] for t in d.title]
    d["n_sources"] = 1
    d["fetched"] = pd.Timestamp.now().floor("s")
    return d[COLS]


def _norm(t):
    return re.sub(r"[^a-z0-9]", "", str(t).lower())[:80]


def merge(old, new):
    """Same story from several sites -> one row (earliest time kept, n_sources counts the sites)."""
    d = pd.concat([x for x in (old, new) if x is not None and len(x)], ignore_index=True) if (old is not None and len(old)) else new
    if d is None or d.empty:
        return pd.DataFrame(columns=COLS)
    d = d.sort_values("published", na_position="last")
    agg = d.groupby("id", sort=False).agg(n=("source", "nunique"))
    first = d.drop_duplicates("id", keep="first").set_index("id")
    first["n_sources"] = agg.n.reindex(first.index).clip(lower=first.n_sources.fillna(1))
    return first.reset_index()[COLS].sort_values("published", ascending=False, na_position="last").reset_index(drop=True)


# ---------------- store ----------------
def load_store():
    if STORE.exists():
        d = pd.read_parquet(STORE)
        d["published"] = pd.to_datetime(d.published)
        return d
    if LEGACY.exists():                            # first run after the upgrade: bring over the old cache
        try:
            j = json.loads(LEGACY.read_text())
            return enrich(pd.DataFrame(j.get("items", [])).assign(method="rss"))
        except Exception:
            pass
    return pd.DataFrame(columns=COLS)


def save_store(d):
    DIR.mkdir(parents=True, exist_ok=True)
    atomic.to_parquet(d, STORE)


def retag():
    """Re-links every stored headline (after you edit data/news_aliases.csv)."""
    from .filelock import locked
    with locked("news"):
        d = load_store()
        if len(d):
            n_src = d.set_index("id").n_sources if "n_sources" in d else None
            new = enrich(d.drop(columns=["symbols", "sectors", "event_type", "tone", "roundup"], errors="ignore"))
            if n_src is not None:
                new["n_sources"] = new.id.map(n_src).fillna(1).astype(int)
            save_store(new)


def update(gnews_symbols=None, use_sitemaps=True):
    """Fetches RSS feeds + news sitemaps (+ Google News for some stocks), adds new headlines to the store."""
    polite = S.Polite()
    items, health = S.fetch_rss(polite=S.Polite(gap=0.5))
    if use_sitemaps:
        it2, h2 = S.fetch_news_sitemaps(polite=polite)
        items += it2; health += h2
    if gnews_symbols:
        tagger, uni = load_tagger()
        names = dict(zip(uni.Symbol, uni["Company Name"]))
        ok = 0
        for s in gnews_symbols:
            try:
                q = BUILTIN_ALIASES.get(s, [Tagger._base(names.get(s, s))])[-1]
                items += S.fetch_gnews(q, polite, days=30)
                ok += 1
            except PermissionError as e:
                health.append(dict(source="Google News search", method="gnews", status="blocked", items=0, note=str(e)[:120])); break
            except Exception as e:
                health.append(dict(source=f"Google News: {s}", method="gnews", status="error", items=0, note=f"{type(e).__name__}"))
        health.append(dict(source="Google News search", method="gnews", status="ok" if ok else "error", items=ok, note=f"{ok} stocks searched"))
    new = enrich(pd.DataFrame(items)) if items else pd.DataFrame(columns=COLS)
    added, total = merge_save(new)
    S.save_state(health)
    info = dict(when=time.strftime("%Y-%m-%d %H:%M:%S"), fetched=len(new), added=added, total=total,
                dead=[h["source"] for h in health if h.get("status") in ("dead", "error", "blocked")])
    (DIR / "last_update.json").write_text(json.dumps(info, indent=1))
    return info, health


def backfill(start, end=None, sites=None, max_requests=1500, log=print):
    """Reads dated archive sitemaps (headlines + links only) for [start, end], newest first, resumable."""
    start, end = pd.Timestamp(start), pd.Timestamp(end or pd.Timestamp.now().normalize())
    state = json.loads(BACKFILL.read_text()) if BACKFILL.exists() else {"done": []}
    done = set(state["done"])
    polite = S.Polite()
    tagger, uni = load_tagger()
    total_new = 0
    for site in sites or S.SITEMAP_SITES:
        if polite.requests >= max_requests:
            break
        try:
            files = S.archive_sitemaps(site, polite, start, end)
        except Exception as e:
            log(f"{site}: could not list archive sitemaps ({type(e).__name__}: {e})"); continue
        todo = [(u, p) for u, p in files if u not in done]
        log(f"{site}: {len(files)} archive sitemap(s) in range, {len(todo)} not read yet")
        batch = []
        for u, per in todo:
            if polite.requests >= max_requests:
                log(f"  stopping at the request limit ({max_requests}); run again to continue"); break
            try:
                kind, data = S.parse_sitemap(polite.get(u))
            except PermissionError:
                log(f"  robots.txt disallows {u}: skipped"); done.add(u); continue
            except Exception as e:
                log(f"  {u}: {type(e).__name__}"); continue
            if kind == "urlset":
                its = S.entries_to_items(data, site, "archive")
                batch += its
                log(f"  {per[0].date()}: {len(its)} finance headlines")
            done.add(u)
            if len(batch) > 5000:
                total_new += _save_batch(batch, tagger, uni); batch = []
                BACKFILL.write_text(json.dumps({"done": sorted(done)}))
        if batch:
            total_new += _save_batch(batch, tagger, uni)
        BACKFILL.write_text(json.dumps({"done": sorted(done)}))
    log(f"Backfill: {total_new} new headlines stored, {polite.requests} requests.")
    return total_new


def _save_batch(batch, tagger, uni):
    return merge_save(enrich(pd.DataFrame(batch), tagger, uni))[0]


def merge_save(new):
    """Re-reads the store under a lock, adds `new`, writes it. Returns (added, total). Safe with parallel jobs."""
    from .filelock import locked
    with locked("news"):
        old = load_store()
        d = merge(old, new)
        save_store(d)
        return len(d) - len(old), len(d)


# ---------------- compatibility with the existing screens ----------------
def fetch(feeds=None, timeout=10):
    info, health = update()
    errs = [f"{h['source']}: {h['status']}" for h in health if h.get("status") in ("dead", "error", "blocked")]
    return load_store().head(info["total"]).to_dict("records"), errs


def load(days=None):
    """(DataFrame, problems, last update time) as the screens expect."""
    d = load_store()
    if d.empty:
        return None, [], None
    if days:
        d = d[d.published >= pd.Timestamp.now() - pd.Timedelta(days=days)]
    info = {}
    p = DIR / "last_update.json"
    if p.exists():
        info = json.loads(p.read_text())
    st = S.load_state() or {}
    errs = [f"{h['source']} ({h['status']})" for h in st.get("sources", []) if h.get("status") in ("dead", "error", "blocked")]
    when = info.get("when") or (json.loads(LEGACY.read_text()).get("fetched") if LEGACY.exists() else None)
    return d, errs, when


def tag(news, universe=None):
    """Kept for older code: the store is already tagged; untagged frames are tagged here."""
    if news is None or news.empty:
        return news
    if "event_type" in news and "symbols" in news:
        return news
    return enrich(news)


def for_symbol(symbol, start=None, end=None, include_roundups=True):
    d = load_store()
    if d.empty:
        return d
    m = d.symbols.fillna("").str.split(",").map(lambda xs: symbol in xs)
    d = d[m]
    if not include_roundups:
        d = d[~d.roundup]
    if start is not None:
        d = d[d.published >= pd.Timestamp(start)]
    if end is not None:
        d = d[d.published <= pd.Timestamp(end) + pd.Timedelta(days=1)]
    return d.sort_values("published", ascending=False)


def _args(argv):
    return dict(a.split("=", 1) for a in argv if "=" in a)


if __name__ == "__main__":
    # python3 -m core.news backfill [start=2016-01-01] [end=...] [max=1500]  |  retag  |  check
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "update"
    a = _args(sys.argv[2:])
    if cmd == "backfill":
        backfill(a.get("start", "2016-01-01"), a.get("end"), max_requests=int(a.get("max", 1500)))
    elif cmd == "retag":
        retag(); print("Re-linked", len(load_store()), "headlines")
    elif cmd == "check":
        for h in S.check_sources():
            print(f"{h['source']:<40} {h['method']:<8} {h['status']:<8} {h['items']!s:>5}  {h.get('note', '')}")
    else:
        info, _ = update(); print(info)
