"""Evidence layer shared by both research agents.

Every item is a record with an ID the agents must cite, the source, and separate timestamps:
  published   when the source first made it public (news / filings)
  event_day   first trading session the market could react (15:30 IST rule, research/events.py)
  observed    when a market value was observed (close of a session, 15:30 IST)
  retrieved   when this app downloaded it
  period      the period a value describes (e.g. 'last 21 sessions')
All times are Asia/Kolkata (+05:30). Nothing published after the information cutoff (the analysis day's 15:30 IST
close) is included, so an analysis of a past day sees only what was known then (except: the stored news / filings
were downloaded later, so items that were later edited or deleted at the source cannot be reconstructed — recorded
as a limitation on every forecast).

Raw facts and calculations are kept apart from model text: agents only cite these IDs; anything they say without
a valid ID is labelled an unverified hypothesis."""
import hashlib, json, re
from datetime import datetime
import numpy as np, pandas as pd
from core import config as C
from . import signals as SG

IST = "+05:30"
CLOSE = pd.Timedelta(hours=15, minutes=30)
PRIMARY = "Primary: exchange filing (NSE)"
EXCHANGE = "Exchange price data (Angel One SmartAPI)"
DERIVED = "Derived calculation (documented formula)"
PRESS = "Established business press"
OTHER = "Other / aggregator"
USER = "Your own list (user-maintained)"
STAT = "Statistical result (this app)"
PRESS_DOMAINS = ("economictimes", "moneycontrol", "livemint", "business-standard", "financialexpress", "thehindubusinessline",
                 "businessline", "reuters", "bloomberg", "cnbctv18", "ndtvprofit", "businesstoday", "thehindu", "ET ", "Livemint",
                 "BusinessLine", "Moneycontrol", "Business Standard")
MATERIAL = {"Results", "Acquisition / merger", "Legal / regulatory", "Fund raising", "Management change", "Credit rating",
            "Order / contract", "Buyback", "Dividend", "Bonus / split", "Block / bulk deal", "Block / bulk deal / stake",
            "Dividend / bonus / split / buyback"}
UNAVAILABLE = {
    "UA-FUNDAMENTALS": ("Financial statements (revenue, margins, EPS, cash flow, debt, ROE/ROIC) and valuation multiples",
                        "No point-in-time fundamentals provider is connected. Results filings appear only as announcements (text), not numbers."),
    "UA-ESTIMATES": ("Analyst estimates and revisions", "No licensed estimates provider. 'Broker view' headlines exist as news only."),
    "UA-DERIVATIVES": ("Options / futures data (open interest, implied volatility, put-call ratio)", "Equities only; no F&O data is downloaded."),
    "UA-POSITIONING": ("Short interest, institutional and insider holdings as numbers", "Only as filing text (Shareholding / insider category)."),
    "UA-INTRADAY": ("Intraday bars, VWAP, intraday horizons", "Only daily bars are stored."),
    "UA-BSE": ("BSE prices", "Prices come from NSE via Angel One; BSE-only instruments are not covered."),
}


def ist(ts):
    """Naive IST timestamp -> ISO string with +05:30 (None-safe)."""
    if ts is None or (not isinstance(ts, str) and pd.isna(ts)):
        return None
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("Asia/Kolkata").tz_localize(None)
    return t.strftime("%Y-%m-%dT%H:%M:%S") + IST


def now_ist():
    return ist(pd.Timestamp.now(tz="Asia/Kolkata"))


def cutoff_ts(day):
    return pd.Timestamp(day).normalize() + CLOSE


def _mtime(path):
    try:
        return ist(pd.Timestamp(datetime.fromtimestamp(path.stat().st_mtime)))
    except OSError:
        return None


def reliability_of(source):
    s = str(source or "")
    if "NSE" in s:
        return PRIMARY
    return PRESS if any(d in s for d in PRESS_DOMAINS) else OTHER


def _item(eid, category, fact, source, **kw):
    base = dict(id=eid, category=category, fact=fact, source=source, source_ref=None, published=None, event_day=None, observed=None,
                retrieved=None, period=None, value=None, unit=None, adjusted=None, quality="ok", reliability=DERIVED,
                provenance=None, direction=None, horizon_note=None, extra={})
    base.update(kw)
    return base


def _f(x, d=2):
    return None if x is None or not np.isfinite(x) else round(float(x), d)


# ---------------- context ----------------
class Context:
    """Everything one analysis needs, loaded once. h: horizon in sessions."""

    def __init__(self, h, as_of=None, log=print, data=None):
        """data (tests / replays): dict with px, idx, uni and optionally news, ann — used instead of the stored files."""
        from . import features as F
        from core import news as N, announcements as A
        self.h = h
        if data is not None:
            panel, self.market = F.build(data["px"], data["idx"], data["uni"])
        else:
            panel, self.market = F.load(log=log)
        self.panel = F.forward(panel, h)
        self.as_of = pd.Timestamp(as_of or self.panel.date.max()).normalize()
        self.cutoff = cutoff_ts(self.as_of)
        self.sig = SG.compute(self.panel)
        self.stamp = data_stamp(self.panel)
        if data is not None:
            self.news = data.get("news", pd.DataFrame(columns=["id", "published", "title", "symbols", "event_type", "tone", "roundup", "source", "link", "n_sources"]))
            self.ann = data.get("ann", pd.DataFrame(columns=["id", "published", "symbol", "subject", "text", "category", "link"]))
            self.uni = data["uni"]
        else:
            self.news = N.load_store()
            self.ann = A.load()
            self.uni = pd.read_csv(C.DATA / "universe.csv")
        self.prices_file = C.DATA / "prices.parquet"
        self.retrieved_prices = _mtime(self.prices_file)
        self._P = None

    @property
    def P(self):
        if self._P is None:
            from research.events import returns_panel
            px = self.panel[["date", "symbol", "close", "volume"]]
            idx = self.market[["date", "nifty"]]
            self._P = returns_panel(px, idx)
        return self._P

    def rows(self, symbol):
        if not hasattr(self, "_rows"):
            self._rows = {k: g for k, g in self.panel.groupby("symbol", sort=False)}
        return self._rows.get(symbol, self.panel.iloc[0:0])

    def news_for(self, symbol):
        """Headlines linked to `symbol` (index built once per analysis)."""
        if not hasattr(self, "_news_idx"):
            n = self.news
            idx = {}
            if n is not None and len(n):
                for i, syms in zip(n.index, n.symbols.fillna("")):
                    for x in str(syms).split(","):
                        if x:
                            idx.setdefault(x, []).append(i)
            self._news_idx = idx
        return self.news.loc[self._news_idx.get(symbol, [])]


def data_stamp(panel):
    """Identifies the price data and the settings that change statistical results, for cache keys: a corrected price
    file or a changed liquidity filter must not reuse old models or tests."""
    sig = f"{len(panel)}|{panel.date.max()}|{float(panel.close.sum()):.4f}|{C.MIN_TURNOVER_CR}|{C.MIN_PRICE}|{C.COST_RT}"
    return hashlib.sha1(sig.encode()).hexdigest()[:8]


def safe_link(url):
    """Only http(s) links are kept (a javascript: or data: link from a feed must never become clickable)."""
    u = str(url or "").strip()
    return u if u.lower().startswith(("http://", "https://")) else None


# ---------------- quality ----------------
def price_quality(symbol, as_of):
    from core import quality as Q
    q = "ok"
    notes = []
    if symbol in Q.quarantined():
        q = "quarantined"
        notes.append("unreviewed data error in the last 400 days (Data health)")
    if Q.ISSUES.exists():
        iss = pd.read_csv(Q.ISSUES)
        iss = iss[(iss.symbol == symbol) & (~iss.reviewed.astype(bool))]
        iss = iss[pd.to_datetime(iss.date) >= pd.Timestamp(as_of) - pd.Timedelta(days=400)]
        if len(iss):
            notes += [f"{r.check} on {r.date} ({r.severity})" for r in iss.head(5).itertuples()]
            if q == "ok":
                q = "warning"
    return q, notes


# ---------------- evidence builders ----------------
def price_evidence(ctx, symbol):
    r = ctx.rows(symbol)
    r = r[r.date <= ctx.as_of]
    if r.empty:
        return [], None
    last = r.iloc[-1]
    q, notes = price_quality(symbol, ctx.as_of)
    common = dict(source=EXCHANGE, source_ref="data/prices.parquet", retrieved=ctx.retrieved_prices, reliability=EXCHANGE,
                  adjusted="split/bonus adjusted (corporate actions applied); dividends not added", quality=q)
    obs = ist(pd.Timestamp(last.date) + CLOSE)
    out = [_item("PX-REF", "Price and volume", f"Reference close {last.close:,.2f} on {pd.Timestamp(last.date).date()}", observed=obs,
                 value=float(last.close), unit="INR", period="session close", provenance="close of the analysis day", **common)]
    for n, lab in ((1, "1 session"), (5, "5 sessions"), (21, "21 sessions"), (63, "63 sessions")):
        v = last.get(f"ret{n}")
        if np.isfinite(v):
            out.append(_item(f"PX-RET{n}", "Price and volume", f"Return over the last {lab}: {100 * v:+.2f}%", observed=obs, value=_f(100 * v),
                             unit="%", period=f"last {lab}", provenance=f"close / close {n} sessions earlier - 1", **common))
    if np.isfinite(last.hh252):
        lo252 = r.low.iloc[-253:-1].min() if len(r) > 2 else np.nan
        out.append(_item("PX-52W", "Price and volume", f"Close is {100 * (last.close / last.hh252 - 1):+.1f}% from the previous 52-week high "
                         f"({last.hh252:,.2f}) and {100 * (last.close / lo252 - 1):+.1f}% from the 52-week low ({lo252:,.2f})", observed=obs,
                         value=_f(100 * (last.close / last.hh252 - 1)), unit="%", period="previous 252 sessions", provenance="high/low of the previous 252 sessions", **common))
    if np.isfinite(last.vol_ratio):
        out.append(_item("PX-VOL", "Price and volume", f"Volume {last.volume:,.0f} = {last.vol_ratio:.2f}x its 20-session average", observed=obs,
                         value=_f(last.vol_ratio), unit="x", period="session vs previous 20", provenance="volume / mean volume of previous 20 sessions", **common))
    out.append(_item("PX-LIQ", "Price and volume", f"Average traded value {last.turnover_cr:,.0f} crore per day (20 sessions); "
                     f"{int(last.n_hist)} sessions of history", observed=obs, value=_f(last.turnover_cr, 1), unit="INR crore",
                     period="20 sessions", provenance="mean(close x volume) over 20 sessions", **common))
    if notes:
        out.append(_item("PX-QUALITY", "Price and volume", "Data quality notes: " + "; ".join(notes), quality=q, source="Data health checks (core/quality.py)",
                         reliability=DERIVED))
    return out, last


def technical_evidence(ctx, last):
    obs = ist(pd.Timestamp(last.date) + CLOSE)
    c = dict(source="Indicator calculation (debate/features.py)", observed=obs, reliability=DERIVED, adjusted="computed from adjusted closes")
    out = []
    def add(eid, fact, value, unit, prov, period):
        out.append(_item(eid, "Technical indicators", fact, value=value, unit=unit, provenance=prov, period=period, **c))
    if np.isfinite(last.sma200):
        add("TA-SMA", f"Close {last.close:,.2f}; 20-day SMA {last.sma20:,.2f}; 50-day SMA {last.sma50:,.2f}; 200-day SMA {last.sma200:,.2f} "
            f"(close {100 * (last.close / last.sma50 - 1):+.1f}% vs 50-day, {100 * (last.close / last.sma200 - 1):+.1f}% vs 200-day)",
            _f(100 * (last.close / last.sma200 - 1)), "% vs 200-day SMA", "simple moving averages of close", "20/50/200 sessions")
    add("TA-RSI", f"RSI(14) {last.rsi14:.1f}; RSI(3) {last.rsi3:.1f}", _f(last.rsi14, 1), "index 0-100", "Wilder RSI", "14 and 3 sessions")
    add("TA-MACD", f"MACD {last.macd:.2f} vs signal {last.macd_sig:.2f} (histogram {last.macd_hist:+.2f})", _f(last.macd_hist), "INR",
        "EMA12-EMA26, signal EMA9", "26 sessions")
    add("TA-ADX", f"ADX(14) {last.adx14:.1f}; +DI {last.pdi:.1f}; -DI {last.mdi:.1f}", _f(last.adx14, 1), "index", "Wilder directional movement", "14 sessions")
    add("TA-BB", f"Bollinger bands {last.bb_lo:,.2f} to {last.bb_up:,.2f} (SMA20 ± 2 sd)", None, "INR", "SMA20 ± 2 population sd", "20 sessions")
    add("TA-ATR", f"ATR(14) {last.atr14:,.2f} = {100 * last.atr_pct:.2f}% of price (percentile {100 * last.atr_pct_rank:.0f} among stocks); "
        f"20-day realised volatility {100 * last.rv20:.0f}% annualised", _f(100 * last.atr_pct), "% of price", "14-session mean true range", "14/20 sessions")
    add("TA-OBV", f"On-balance volume change over 20 sessions: {last.obv_chg20:+.2f} average days of volume", _f(last.obv_chg20), "days of volume",
        "OBV(t) - OBV(t-20), divided by mean daily volume", "20 sessions")
    add("TA-RS", f"63-session return vs Nifty {100 * last.rs63:+.1f} pp (percentile {100 * last.rs_pct:.0f} of stocks)", _f(100 * last.rs63, 1),
        "pp", "stock 63-session return minus Nifty's", "63 sessions")
    if np.isfinite(last.get("sector_rank_pct", np.nan)):
        add("TA-SECTOR", f"Sector ({last.sector}) 21-session average return {100 * last.sector_ret21:+.1f}% (percentile {100 * last.sector_rank_pct:.0f} of sectors)",
            _f(100 * last.sector_ret21, 1), "%", "mean 21-session return of stocks in the sector (3+ stocks)", "21 sessions")
    return out


def market_evidence(ctx, last):
    obs = ist(pd.Timestamp(last.date) + CLOSE)
    c = dict(source="Nifty 50, India VIX (Angel One) and breadth of the Nifty 500 list", observed=obs, reliability=DERIVED)
    out = [_item("MK-NIFTY", "Market benchmarks", f"Nifty 50 {last.nifty:,.0f}, {100 * last.nifty_vs_200:+.1f}% vs its 200-day SMA; "
                 f"63-session return {100 * last.nifty_ret63:+.1f}%", value=_f(100 * last.nifty_vs_200), unit="%", provenance="index close vs 200-day SMA", **c)]
    if np.isfinite(last.vix_rel):
        out.append(_item("MK-VIX", "Market benchmarks", f"India VIX is {100 * (last.vix_rel - 1):+.0f}% vs its 1-year median", value=_f(100 * (last.vix_rel - 1), 0),
                         unit="%", provenance="VIX / rolling 252-session median", **c))
    out.append(_item("MK-BREADTH", "Market benchmarks", f"{100 * last.breadth:.0f}% of stocks are above their 200-day SMA", value=_f(100 * last.breadth, 0),
                     unit="%", provenance="share of the stored universe above its 200-day SMA (today's Nifty 500 list)", **c))
    return out


def signal_evidence(ctx, symbol):
    r = ctx.panel.index[(ctx.panel.symbol == symbol) & (ctx.panel.date == ctx.as_of)]
    if len(r) == 0:
        return []
    row = ctx.sig.loc[r[0]]
    out = []
    for k in SG.active(row):
        s = SG.BY_ID[k]
        out.append(_item(f"SG-{k}", "Technical indicators", f"Signal firing: {s['name']} ({s['definition']})", source="Pre-registered signal (debate/signals.py)",
                         observed=ist(ctx.as_of + CLOSE), reliability=DERIVED, provenance=s["definition"], direction=s["side"],
                         extra=dict(signal=k, group=s["group"])))
    return out


def _event_reaction(ctx, symbol, d0):
    """Abnormal move (vs Nifty) from the close before the event day to the analysis close, and on the event day."""
    r = ctx.rows(symbol)
    r = r[r.date <= ctx.as_of].reset_index(drop=True)
    i = r.index[r.date == d0]
    if len(i) == 0 or i[0] == 0:
        return None, None
    i = i[0]
    b = r.iloc[i - 1]
    e0 = r.iloc[i]
    last = r.iloc[-1]
    ar0 = (e0.close / b.close - 1) - (e0.nifty / b.nifty - 1)
    since = (last.close / b.close - 1) - (last.nifty / b.nifty - 1)
    return 100 * ar0, 100 * since


BROKER_WORDS = re.compile(r"\b(buy|sell|neutral|hold|accumulate|reduce|add|target|recommendations?|picks?|top bets?)\b", re.I)


def broker_mention(title, company):
    """True when the stock's company appears only as the BROKER behind a call on other stocks
    ("Buy X; target of Rs 100: Motilal Oswal", "8 'Buy' recommendations by Motilal Oswal")."""
    t = str(title or "")
    words = [w for w in re.findall(r"[A-Za-z]+", str(company or "")) if len(w) > 2 and w.lower() not in ("ltd", "limited", "the", "and", "financial", "services", "india", "company")]
    if not words or not BROKER_WORDS.search(t):
        return False
    low = t.lower()
    pos = low.rfind(words[0].lower())
    if pos < 0:
        return False
    after = re.sub(r"[^a-z ]", "", low[pos:])
    rest = after.split()[1:]
    others = [w.lower() for w in words[1:3]]
    rest = [w for w in rest if w not in others]
    before = low[:pos]
    return BROKER_WORDS.search(before) is not None and len(" ".join(rest)) <= 3


COMMENTARY_VERBS = (r"(sees|expects|says|said|flags|estimates|forecasts|remains (bullish|bearish|positive|cautious)|is bullish|is bearish|bullish on|bearish on|recommends|initiates|upgrades|downgrades|picks|"
                    r"likes|prefers|top picks|retains|maintains|raises target|cuts target)")


def _name_words(company):
    return [w for w in re.findall(r"[A-Za-z]+", str(company or "")) if len(w) > 2 and w.lower() not in
            ("ltd", "limited", "the", "and", "financial", "services", "india", "company", "industries", "corporation", "bank")]


def relevance(title, company, symbol):
    """'direct' when the headline names the company, 'commentary' when the company is the analyst / broker talking
    about other stocks, 'weak' when the headline does not name it (linked from article text or keywords only)."""
    t = str(title or "")
    words = _name_words(company)
    named = re.search(rf"\b{re.escape(symbol)}\b", t, re.I) is not None or (bool(words) and re.search(rf"\b{re.escape(words[0])}\b", t, re.I) is not None)
    if not named:
        return "weak"
    if broker_mention(t, company):
        return "commentary"
    if words:
        pat = rf"\b{re.escape(words[0])}" + (rf"(\W+{re.escape(words[1])})?" if len(words) > 1 else "") + r"\b"
        lead = re.sub(pat, "NAMEX", t, count=1, flags=re.I)
        if (re.search(rf"NAMEX\W+(\w+\W+){{0,2}}{COMMENTARY_VERBS}\b", lead, re.I)
                or re.search(r"(according to|as per|says|per)\W+NAMEX|NAMEX\W+(report|research|note|analysts?)\b", lead, re.I)):
            return "commentary"
    return "direct"


def news_evidence(ctx, symbol, days=30, max_items=25):
    from research.events import event_day
    n = ctx.news
    if n is None or n.empty:
        return []
    d = ctx.news_for(symbol)
    d = d[(d.published <= ctx.cutoff) & (d.published >= ctx.cutoff - pd.Timedelta(days=days))].sort_values("published")
    if d.empty:
        return []
    dates = ctx.P["dates"]
    ann = ctx.ann
    ann_s = ann[(ann.symbol == symbol)] if ann is not None and len(ann) else pd.DataFrame(columns=["published", "category", "id"])
    ann_from = ann.published.min() if ann is not None and len(ann) else None
    company = ctx.uni.loc[ctx.uni.Symbol == symbol, "Company Name"]
    company = company.iloc[0] if len(company) else symbol
    out, seen = [], {}
    for r in d.itertuples():
        rel = relevance(r.title, company, symbol)
        if rel == "commentary":
            continue                      # the company is the broker / analyst, not the subject
        d0 = event_day(r.published, dates)
        ar0, since = _event_reaction(ctx, symbol, d0) if pd.notna(d0) else (None, None)
        key = r.event_type or "untyped"
        prev = seen.get(key)
        novelty = "first report of this kind in the last 10 days" if prev is None or (r.published - prev).days > 10 else \
            f"follow-up: similar '{key}' item first seen {prev.date()}"
        seen.setdefault(key, r.published)
        confirm = None
        if r.event_type in MATERIAL:
            near = ann_s[(ann_s.published - r.published).abs() <= pd.Timedelta(days=2)]
            if len(near):
                confirm = "matching NSE filing(s) within 2 days: " + ", ".join("AN-" + str(x)[:8] for x in near.id.head(3))
            elif ann_from is not None and r.published >= ann_from:
                confirm = "no matching NSE filing found within 2 days (unverified)"
            else:
                confirm = "cannot verify: the stored NSE filings start later"
        roundup = bool(getattr(r, "roundup", False))
        fact = (f"News ({r.event_type or 'type not stated'}, tone {r.tone or 'no clear direction'}): \"{str(r.title)[:180]}\" — {r.source}, published {pd.Timestamp(r.published):%Y-%m-%d %H:%M} IST"
                + (f"; covered by {int(r.n_sources)} sources" if getattr(r, "n_sources", 1) and r.n_sources > 1 else "")
                + (f"; stock vs Nifty on the event day {ar0:+.1f}%, since then {since:+.1f}%" if ar0 is not None else "")
                + ("; multi-stock roundup" if roundup else ""))
        out.append(_item(f"NW-{str(r.id)[:8]}", "News", fact, source=r.source, source_ref=safe_link(r.link), published=ist(r.published),
                         event_day=None if pd.isna(d0) else str(pd.Timestamp(d0).date()), retrieved=ist(getattr(r, "fetched", None)),
                         reliability=reliability_of(r.source), value=None if since is None else _f(since), unit="% abnormal since event",
                         direction={"positive": "bull", "negative": "bear"}.get(str(r.tone)) if rel == "direct" and not roundup else None,
                         quality="roundup (many stocks)" if roundup else "weak link (company not named in the headline)" if rel == "weak" else "ok",
                         extra=dict(event_type=r.event_type, tone=r.tone, n_sources=int(getattr(r, "n_sources", 1) or 1), novelty=novelty,
                                    verification=confirm, ar0=_f(ar0), since=_f(since), title=str(r.title))))
    out = sorted(out, key=lambda e: (e["quality"] == "ok", e["published"]))
    return out[-max_items:]


def filing_evidence(ctx, symbol, days=60, max_items=20):
    from research.events import event_day
    a = ctx.ann
    if a is None or a.empty:
        return []
    d = a[(a.symbol == symbol) & (a.published <= ctx.cutoff) & (a.published >= ctx.cutoff - pd.Timedelta(days=days))].sort_values("published")
    from core.news import tone
    dates = ctx.P["dates"]
    out = []
    for r in d.tail(max_items).itertuples():
        tn = tone(f"{r.subject} {r.text}")
        d0 = event_day(r.published, dates)
        ar0, since = _event_reaction(ctx, symbol, d0) if pd.notna(d0) else (None, None)
        text = re.sub(r"\s+", " ", f"{r.subject}: {r.text}")[:260]
        out.append(_item(f"AN-{str(r.id)[:8]}", "Corporate announcements" if r.category != "Results" else "Earnings releases",
                         f"NSE filing ({r.category}) {pd.Timestamp(r.published):%Y-%m-%d %H:%M} IST: {text}"
                         + (f"; stock vs Nifty on the event day {ar0:+.1f}%, since then {since:+.1f}%" if ar0 is not None else ""),
                         source="NSE corporate announcements", source_ref=safe_link(r.link), published=ist(r.published),
                         event_day=None if pd.isna(d0) else str(pd.Timestamp(d0).date()), retrieved=ist(getattr(r, "fetched", None)),
                         reliability=PRIMARY, value=None if since is None else _f(since), unit="% abnormal since event",
                         direction={"positive": "bull", "negative": "bear"}.get(tn),
                         quality="routine" if r.category in ("Routine compliance", "Investor meet / call") else "ok",
                         extra=dict(category=r.category, tone=tn, ar0=_f(ar0), since=_f(since))))
    return out


def macro_evidence(ctx, symbol, sector):
    """Stock (or sector) sensitivity to macro factors, and how those factors moved recently (as of the analysis day)."""
    from core import macro as M
    out = []
    res = C.RESULTS / "macro"
    try:
        summ = {s["label"]: s for s in M.summary(ctx.as_of)}
    except Exception:
        summ = {}
    sens = None
    level = None
    f1, f2 = res / "sens_stocks_3y.csv", res / "sens_3y.csv"
    if f1.exists():
        s = pd.read_csv(f1)
        s = s[s.symbol == symbol]
        if len(s):
            sens, level = s, "stock"
    if sens is None and f2.exists():
        s = pd.read_csv(f2)
        s = s[s.sector == sector]
        if len(s):
            sens, level = s, "sector"
    meta = res / "meta.json"
    when = json.loads(meta.read_text()).get("when") if meta.exists() else None
    if sens is not None:
        for r in sens.itertuples():
            mv = summ.get(r.factor)
            move = None
            if mv:
                ch = mv.get("chg_5d")
                move = f"{r.factor} changed {ch:+.2f}{'' if mv['change_kind'] == '%' else ' pts' if mv['change_kind'] == 'points' else ' cr'}" \
                       f"{'%' if mv['change_kind'] == '%' else ''} over 5 observations to {mv['date']}" if ch is not None else None
            implied = None
            if mv and mv.get("chg_5d") is not None and np.isfinite(r.per_1sd_pct):
                implied = "bull" if np.sign(mv["chg_5d"]) * np.sign(r.per_1sd_pct) > 0 else "bear"
            sid = re.sub(r"[^A-Z0-9]", "", str(r.factor).upper())[:10]
            out.append(_item(f"MC-{sid}", "Macro-economic data",
                             f"{'This stock' if level == 'stock' else 'Sector ' + str(sector)}: {r.per_1sd_pct:+.2f}% weekly return per 1-sd weekly move in "
                             f"{r.factor} (t = {r.t:.1f}, {int(r.weeks)} weeks, Nifty as control)" + (f"; recent move: {move}" if move else ""),
                             source="FRED / GPR / NSE series + weekly regression (research/macro_impact.py)", source_ref="results/macro",
                             retrieved=when, reliability=STAT, value=_f(r.per_1sd_pct), unit="% per 1 sd", period="last 3 years, weekly",
                             direction=implied if abs(r.t) >= 2 else None,
                             extra=dict(factor=r.factor, t=_f(r.t), per_1sd_pct=_f(r.per_1sd_pct), weeks=int(r.weeks), level=level,
                                        recent=mv)))
    try:
        from core import search as S
        ex = S.exposures()
        if len(ex):
            ex = ex[ex.target.astype(str).str.upper().isin([symbol.upper(), str(sector).upper()])]
        for i, r in enumerate(ex.itertuples()):
            out.append(_item(f"UX-{i + 1}", "Sector and industry data",
                             f"Your exposure list: {r.driver} affects {r.target} ({r.effect})" + (f" — {r.note}" if r.note else ""),
                             source="Search → My exposure list (data/search/exposures.csv)", reliability=USER,
                             extra=dict(driver=r.driver, effect=r.effect)))
    except Exception:
        pass
    return out


def unavailable():
    return [_item(k, "Unavailable", f"{a} — not available: {b}", source="none", reliability="n/a", quality="unavailable") for k, (a, b) in UNAVAILABLE.items()]


def gather(ctx, symbol, news_days=30, filing_days=60):
    """All evidence for one stock at the context's cutoff. Returns (items, last panel row or None, info)."""
    px_items, last = price_evidence(ctx, symbol)
    if last is None:
        return [], None, dict(error="no price data for this stock on or before the analysis day")
    sector = last.sector
    items = (px_items + technical_evidence(ctx, last) + market_evidence(ctx, last) + signal_evidence(ctx, symbol)
             + news_evidence(ctx, symbol, news_days) + filing_evidence(ctx, symbol, filing_days) + macro_evidence(ctx, symbol, sector) + unavailable())
    company = ctx.uni.loc[ctx.uni.Symbol == symbol, "Company Name"]
    info = dict(symbol=symbol, exchange="NSE", company=company.iloc[0] if len(company) else symbol, sector=sector,
                last_bar=str(pd.Timestamp(last.date).date()), stale_sessions=int((ctx.panel.date.max() > last.date) and
                                                                                 (ctx.panel[(ctx.panel.date > last.date) & (ctx.panel.date <= ctx.as_of)].date.nunique())),
                news_coverage_from=str(ctx.news.published.min().date()) if ctx.news is not None and len(ctx.news) else None,
                filings_coverage_from=str(ctx.ann.published.min().date()) if ctx.ann is not None and len(ctx.ann) else None)
    try:
        from core import company_info as CI
        ci = CI.load()
        row = ci[ci.symbol == symbol] if len(ci) else ci
        if len(row):
            info["isin"] = row.iloc[0].get("isin") or None
            info["industry"] = row.iloc[0].get("industry") or None
    except Exception:
        pass
    return items, last, info


def pack_text(items, limit=None):
    """Compact evidence list for a language model: one line per item, ID first."""
    lines = []
    for e in items[:limit]:
        meta = []
        if e.get("published"):
            meta.append(f"published {e['published'][:16]}")
        if e.get("reliability"):
            meta.append(e["reliability"])
        x = e.get("extra") or {}
        if x.get("verification"):
            meta.append(x["verification"])
        if x.get("novelty"):
            meta.append(x["novelty"])
        lines.append(f"[{e['id']}] {e['fact']}" + (f" ({'; '.join(meta)})" if meta else ""))
    return "\n".join(lines)


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()
