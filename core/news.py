"""News store: every headline we fetch is cleaned, linked to stocks, labelled, de-duplicated and kept.

    data/news/news.parquet         all headlines ever fetched (history grows with every update and backfill)
    data/news/backfill_state.json  which archive sitemaps were already read (backfill resumes)
    data/news_aliases.csv          your own extra names for stocks: symbol,alias  (e.g. LTIM,LTIMindtree)

Rule: a label is shown only when the headline itself is evidence for it. Otherwise the headline is shown plain.

Stock tags (Tagger, Nifty 500 companies only; headline only, never the summary):
  - the company's full name without "Ltd/Limited", a curated short name (SBI, L&T, Airtel ... and your aliases file),
    or a distinctive first-two-words / first-word short form ("Infosys") of a Nifty 500 company,
  - the NSE ticker in capitals ("TCS"), except tickers that are also words or acronyms (OIL, MCX, BSE ...), which need
    the name or "<TICKER> shares",
  - or an explicit "NSE: XYZ" in the feed's own metadata.
  Short forms must stand alone: "Paras Healthcare" is not Paras Defence, "Price Action" is not Action Construction,
  "Aarti Drugs" is not Aarti Industries. Brokers named as the source of a view ("...: Motilal Oswal", "Nuvama sees")
  are not tagged. data/instruments.csv is never used for names (its names are just tickers: SILVER, GLOBAL ...).
  Headlines naming 4+ stocks, or "stocks to watch"-type lists, are marked as roundups.

Type: the first matching explicit phrase (IPO, results preview, results, broker view, order, stake deal, M&A, fund
raising, corporate action, legal/regulatory, management change, index, economy, price move); blank when none.
Tone: only for a headline about exactly one stock that is not a roundup, from a direction phrase about it (shares
up/down x%, profit rises/falls, beat/miss, upgrade/downgrade, target cut/raised, order win, penalty/raid ...). Previews,
questions and conflicting phrases get no tone. type_why / tone_why keep the phrase that set each label."""
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
        "event_type", "type_why", "tone", "tone_why", "roundup", "n_sources", "fetched", "tag_version"]

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
    "TATASTEEL": ["Tata Steel"], "TATAPOWER": ["Tata Power"], "TITAN": ["Titan"], "SUNPHARMA": ["Sun Pharma"], "DRREDDY": ["Dr Reddy", "Dr. Reddy"], "LICI": ["LIC", "Life Insurance Corporation"],
    "NIACL": ["New India Assurance"], "BALKRISIND": ["BKT", "Balkrishna Industries"], "COLPAL": ["Colgate India", "Colgate-Palmolive"],
}
TAG_VERSION = 2          # bump when the tagging rules change: stored headlines are re-tagged automatically
SHORT_ALIASES = {"RELIANCE": ["Reliance"]}   # common short forms that only count when not followed by another name (Reliance Power)
AMBIGUOUS_TICKERS = {"BSE", "IDEA", "INDIA", "OIL", "NH", "ACE", "FACT", "MCX", "IEX", "CAMS", "KEI"}   # need the name, or "<TICKER> shares/stock"
ACRONYMS = {"RBI", "IPO", "GST", "SEBI", "FII", "FPI", "DII", "GDP", "CPI", "WPI", "PMI", "IMD", "EV", "AI", "US", "UK", "EU", "FD",
            "PSU", "OMC", "IT", "MF", "ETF", "SIP", "GMP", "NSE", "CEO", "CFO", "MD", "AGM", "OFS", "QIP", "NCD", "USFDA", "FDA", "IMF",
            "OPEC", "MSCI", "NBFC", "PLI", "DRHP", "SME", "LIC", "CBI", "ED", "NCLT", "SAT", "UPI", "ESG", "IIT", "EMS"}
COMMON_WORDS = {"india", "indian", "national", "central", "union", "punjab", "bank", "power", "capital", "finance", "general",
                "gujarat", "bharat", "hindustan", "oil", "steel", "max", "gold", "coal", "global", "new", "life", "home", "first",
                "great", "star", "sun", "jubilant", "kalyan", "aditya", "birla", "tata", "adani", "bajaj", "mahindra", "reliance",
                "hdfc", "icici", "kotak", "state", "the", "industries", "international", "energy", "infra", "housing", "motors",
                "nippon", "japan", "asian", "american", "british", "royal", "premier", "supreme", "prime", "metro", "united",
                "action", "engineers", "silver", "health", "healthcare", "wealth", "clean", "solar", "green", "smart", "digital",
                "data", "future", "city", "credit", "cement", "chemicals", "pharma", "foods", "textiles", "auto", "aviation"}
SUFFIX = re.compile(r"\b(ltd|limited|ltd\.|pvt|private|inc|corporation|corp|company|co)\b\.?", re.I)
# a capitalised word right after a short name is allowed when it is one of these (otherwise it is probably another company)
CONTINUE_OK = re.compile(r"^(shares?|stocks?|scrip|q[1-4]\w*|fy\d*|h[12]\w*|ltd\.?|limited|ipo|results?|share price|price|board|"
                         r"group|chairman|ceo|md|cfo|management|promoters?|block|bulk|order|arm|unit|subsidiary|vs\.?|and|plunges?|"
                         r"surges?|jumps?|falls?|rises?|gains?|slips?|slumps?|soars?|rallies|tanks?|crashes|hits?|sinks?|drops?|"
                         r"climbs?|declines?|q\d|standalone|consolidated|reit|invit|net|india)$", re.I)
LOWER_OK = re.compile(r"^(posts?|reports?|says|announces?|bags?|wins?|signs?|launches|appoints?|acquires?|plans?|gets?|receives?|sees|"
                      r"eyes|raises?|cuts?|hikes?|bets?|expects?|aims?|targets?|denies|clarifies|declares?|approves?|enters?|exits?|"
                      r"inks?|partners?|secures?|completes?|commissions?|unveils?|opens?|q[1-4]\w*|fy\d*)$", re.I)
CONNECTORS = {"of", "in", "on", "for", "at", "by", "as", "and", "with", "to", "from", "vs", "vs.", "&", "buy", "sell", "hold",
              "accumulate", "reduce", "add", "the", "about", "after", "over", "on", "into", "like", "than", "where", "why", "how"}
BROKER_ALIASES = {"nuvama", "motilal oswal", "mofsl", "jm financial", "iifl", "anand rathi", "icici securities", "hdfc securities",
                  "kotak institutional equities", "kotak securities", "axis securities", "axis capital", "emkay", "geojit",
                  "sharekhan", "prabhudas lilladher", "choice broking", "elara", "religare", "angel one", "5paisa", "sbi securities"}
BROKER_CTX = re.compile(r"^\W{0,3}(analysts?|research|sees|says|expects|recommends|maintains|retains|keeps|initiates|upgrades|downgrades|bullish|bearish|"
                        r"picks|suggests|likes|has|gives|raises|cuts|reiterates|values|forecasts|estimates|predicts)\b", re.I)

# ---------------- event type: explicit phrases only ----------------
RX = lambda p: re.compile(p, re.I)
MOVE_W = (r"jumps?|surges?|soars?|rall(y|ies)|gains?|climbs?|rises?|zooms?|spurts?|falls?|drops?|slips?|slumps?|plunges?|crash(es)?|"
          r"tumbles?|sinks?|tanks?|declines?|dips?|sheds?|up|down")
TYPE_RULES = [
    ("IPO / listing", RX(r"\bipo\b|\bgmp\b|grey market premium|subscription status|subscribed \d|\blists? at\b|listing (gain|day|price)|"
                         r"\bdebut\b|price band|anchor investors|\bdrhp\b|\brhp\b")),
    ("Results preview", RX(r"(q[1-4]|quarterly|earnings|results?)\b.{0,40}\bpreview\b|\bpreview\b.{0,40}\b(q[1-4]|results?|earnings)|"
                           r"results? (date|tomorrow|on \w+day)|ahead of (its |their )?(q[1-4]|results?|earnings)|"
                           r"what to expect|earnings (date|schedule)|results? (calendar|schedule)|board meeting to consider")),
    ("Results", RX(r"\bq[1-4]\s?(fy\s?\d+\s?)?(results?|net profit|profit|loss|revenue|earnings|numbers|pat|ebitda|nii|update|scorecard)|"
                   r"(post|after) (its |the )?q[1-4]|q[1-4] (fy\d+ )?(beat|miss)|quarterly (results?|earnings|profit|loss)|"
                   r"\b(net profit|pat|ebitda|net income|nii|revenue|sales)\b.{0,30}\b(rises?|falls?|jumps?|drops?|surges?|declines?|"
                   r"grows?|slips?|dips?|soars?|plunges?|up|down|doubles?|halves?)\b|earnings (beat|miss)|"
                   r"(reports?|posts?) .{0,20}(profit|loss)|loss (widens?|narrows?)|(widen|narrow)\w* (net )?loss")),
    ("Broker view", RX(r"target price|price target|\btarget of (rs|₹|inr)?\s?[\d,]+|\btarget (to|at) (rs|₹)|\b(upgrades?|downgrades?)\b|initiat\w* coverage|"
                       r"\b(buy|sell|hold|accumulate|reduce|add|neutral|overweight|underweight|outperform|underperform) rating|"
                       r"^(buy|sell|hold|accumulate|reduce|add)\s+[A-Z][^;:]{1,60}[;:,]\s*(target|tp)|"
                       r"(retains?|maintains?|keeps?|reiterates?) .{0,15}'?(buy|sell|hold|neutral|overweight|underweight|add|reduce)'?|"
                       r"(cuts?|raises?|hikes?|trims?|slashes?) (its |the )?(target|tp)\b|sees \d+% (upside|downside)")),
    ("Order / contract", RX(r"\b(bags?|bagged|wins?|won|secures?|secured|receives?|received|gets?|bag)\b.{0,60}\b(orders?|contracts?|deal|"
                            r"project|work|tender|loa|letter of award)\b|\border (win|inflow|book)\b|\bl1 bidder\b")),
    ("Block / bulk deal / stake", RX(r"block deal|bulk deal|stake sale|\boffloads?\b|\bofs\b|offer for sale|(sells?|buys?|acquires?|raises?|"
                                     r"hikes?|cuts?|trims?|pares?|ups?) .{0,20}stake|stake (cut|hike)|promoter (sells?|buys?)|pledge")),
    ("Acquisition / merger", RX(r"\bacquir\w*|\bacquisition|\bmerger\b|\bmerge\b|amalgamat|demerg\w*|takeover|open offer|joint venture|"
                                r"\bbuyout\b|to buy .{0,30}(firm|company|business|unit)")),
    ("Fund raising", RX(r"\bqip\b|qualified institution|preferential (issue|allotment)|rights issue|\bncds?\b|fund ?rais\w*|raises? rs|"
                        r"raises? ₹|raises? \$|private placement|\bwarrants\b")),
    ("Dividend / bonus / split / buyback", RX(r"\bdividend\b|\bbonus (issue|shares?)\b|stock split|\bsplit\b|sub-?division|buy-?back|record date")),
    ("Legal / regulatory", RX(r"\bsebi (order|bans?|penalty|probe|notice|fines?)|\bpenalt(y|ies)\b|\bfined?\b|\bfines\b|\braids?\b|\bprobe\b|"
                              r"show[- ]cause|\bnclt\b|prohibition order|\bfssai\b|insolvency|\bcourt\b|tribunal|tax (demand|notice)|gst (demand|notice)|"
                              r"usfda|warning letter|form 483|observations|import alert|licen[cs]e (cancel|suspend)|fraud")),
    ("Management change", RX(r"\b(appoints?|appointed|names?|named|elevated|elevates)\b.{0,40}\b(ceo|md|cfo|chairman|chairperson|"
                             r"chief|director|head)\b|\b(resigns?|resignation|steps down|quits)\b")),
    ("Market (index)", RX(r"\bsensex\b|\bnifty\b|market (wrap|close|crash|falls?|rall\w*|today|live|outlook)|closing bell|opening bell|"
                          r"\bd-street\b|dalal street|stocks to (watch|buy)|buzzing stocks|top (gainers|losers)|stock market today|"
                          r"broader markets?|midcaps?|smallcaps?")),
    ("Economy / commodities", RX(r"\brbi\b|\binflation\b|\bgdp\b|\bpmi\b|\bcpi\b|\bwpi\b|repo rate|\brupee\b|\bcrude\b|\bbrent\b|"
                                 r"\bgold (price|rate)s?\b|\bgold\b.{0,20}\b(rs|₹|\$)|\bsilver\b.{0,20}\b(rs|₹|\$|kg)|\bbond yields?\b|"
                                 r"\bfed\b|monsoon|\bgst (council|collection)|fiscal deficit|trade deficit|\bforex reserves\b|\bfpis?\b|\bfiis?\b")),
    ("Price move", RX(rf"\b(shares?|stock|scrip)\b.{{0,25}}\b({MOVE_W})\b[^.;]{{0,15}}\d+(\.\d+)?\s?%|"
                      r"\b(upper|lower) circuit\b|52-week (high|low)|record high|all-time high")),
]
SPECULATIVE = RX(r"\?|\b(may|might|could|likely|expected to|set to|to (rise|fall|grow)|preview|ahead of|what to expect|should you|"
                 r"buy, sell or hold|will it|can it|is it time)\b")


NOT_BUSINESS_ORDER = RX(r"\b(prohibition|court|tax|demand|closure|stop[- ]work|suspension|gag|cease|tribunal|sebi|interim|"
                        r"restraining|attachment|detention|regulator\w*)\s+(order|notice)")


TRADE_IDEA = RX(r"\bfor an? (gain|upmove|upside|return)|trades? for today|trading idea|stocks? to (buy|sell)")


def classify(text):
    """(event type, matched phrase) from the headline. ('', '') when no explicit phrase matches."""
    t = str(text or "")
    for name, rx in TYPE_RULES:
        if name == "Order / contract" and NOT_BUSINESS_ORDER.search(t):
            continue
        if name == "Price move" and TRADE_IDEA.search(t):
            continue                               # "a stock for a gain of 7%" is a trade idea's target, not a move
        m = rx.search(t)
        if m:
            return name, m.group(0).strip()[:60]
    return "", ""


def event_type(text):
    return classify(text)[0]


# ---------------- tone: a direction phrase, or nothing ----------------
UP_W = r"jumps?|jumped|surges?|surged|soars?|soared|rall(y|ies|ied)|gains?|gained|climbs?|climbed|rises?|rose|zooms?|spurts?|up|higher|doubles?"
DN_W = (r"falls?|fell|drops?|dropped|slips?|slipped|slumps?|slumped|plunges?|plunged|crash(es|ed)?|tumbles?|tumbled|sinks?|sank|tanks?|"
        r"tanked|declines?|declined|dips?|dipped|sheds?|down|lower|halves?|skids?|slides?|slid")
TONE_RULES = [
    ("positive", RX(rf"\b(shares?|stock|scrip)\b.{{0,25}}\b({UP_W})\b[^.;]{{0,15}}\d+(\.\d+)?\s?%|\bupper circuit\b|"
                    r"\b52-week high\b|\b(record|all-time) high\b")),
    ("negative", RX(rf"\b(shares?|stock|scrip)\b.{{0,25}}\b({DN_W})\b[^.;]{{0,15}}\d+(\.\d+)?\s?%|\blower circuit\b|"
                    r"\b52-week low\b|\bnew low\b")),
    ("positive", RX(r"\b(shares?|stock)\b.{0,15}\b(surge[sd]?|soar(s|ed)?|zoom(s|ed)?|jump(s|ed)?|rall(y|ies|ied)|skyrocket(s|ed)?)\b")),
    ("negative", RX(r"\b(shares?|stock)\b.{0,15}\b(plunge[sd]?|crash(es|ed)?|tank(s|ed)?|slump(s|ed)?|tumble[sd]?|nosedive[sd]?)\b")),
    ("positive", RX(r"\b(net profit|profit|pat|ebitda|revenue|sales|net income|nii)\b.{0,25}\b(rises?|jumps?|surges?|soars?|grows?|"
                    r"doubles?|climbs?|up)\b|loss narrows?|narrow\w* (net )?loss|\bbeats?\b.{0,20}(estimates|expectations|street)|"
                    r"(above|beats?) (estimates|expectations)")),
    ("negative", RX(r"\b(net profit|profit|pat|ebitda|revenue|sales|net income|nii)\b.{0,25}\b(falls?|drops?|declines?|slips?|plunges?|"
                    r"halves?|dips?|down|slumps?)\b|loss widens?|widen\w* (net )?loss|\bmiss(es|ed)?\b.{0,20}(estimates|expectations|street)|"
                    r"below (estimates|expectations)|posts? .{0,10}loss")),
    ("positive", RX(r"^(buy|accumulate|add)\s+[A-Z][^;:]{1,60}[;:,]\s*(target|tp)|\bupgrades?\b|\b(raises?|hikes?) (its |the )?(target|tp)\b|initiat\w* .{0,30}\b(buy|overweight|outperform)\b|"
                    r"\b(bags?|bagged|wins?|won|secures?|secured)\b.{0,60}\b(orders?|contracts?|deal|project)\b|zero observations|"
                    r"\bno observations\b|\bnai\b|clears? usfda")),
    ("negative", RX(r"^(sell|reduce)\s+[A-Z][^;:]{1,60}[;:,]\s*(target|tp)|\bdowngrades?\b|\b(cuts?|slashes?|trims?|lowers?) (its |the )?(target|tp)s?\b|initiat\w* .{0,30}\b(sell|underweight|"
                    r"underperform)\b|\braids?\b|\bpenalt(y|ies)\b|\bfined\b|\bfraud\b|\bprobe\b|warning letter|import alert|"
                    r"show[- ]cause|\bdefaults?\b|\bresigns?\b")),
]


_AFTER = r"^[’'s]*\s*(\S+\s+)?({w})\s+(by\s+|over\s+|up to\s+|nearly\s+|about\s+|another\s+)?\d+(\.\d+)?\s?%"
AFTER_UP, AFTER_DN = RX(_AFTER.format(w=UP_W)), RX(_AFTER.format(w=DN_W))


def tone_evidence(text, after=()):
    """('positive'|'negative'|'', matched phrase). Nothing when the headline is speculative or its phrases conflict.
    after: end positions of the stock's name in the text; "<Name> jumps 8%" counts only right after the name."""
    t = str(text or "")
    found = {}
    for lab, rx in TONE_RULES:
        m = rx.search(t)
        if m and lab not in found:
            found[lab] = m.group(0).strip()[:60]
    for e in after:
        for lab, rx in (("positive", AFTER_UP), ("negative", AFTER_DN)):
            m = rx.match(t[e:])
            if m and lab not in found:
                found[lab] = m.group(0).strip()[:60]
    if len(found) != 1:
        return "", ""
    lab, why = next(iter(found.items()))
    if SPECULATIVE.search(t) and not re.search(r"\d+(\.\d+)?\s?%", why):   # a stated move is a fact even in a question headline
        return "", ""
    return lab, why


def tone(text):
    """Kept for event studies on filings: positive / negative / neutral (neutral = no clear direction phrase)."""
    return tone_evidence(text)[0] or "neutral"


ROUNDUP_RX = re.compile(r"stocks to (watch|buy)|top (gainers|losers)|market wrap|buzzing stocks|\b\d+ stocks\b|stocks in news|"
                        r"sensex|nifty 50 today|closing bell|opening bell|market live|stock market today|results today live|"
                        r"among \d+ stocks|\bstocks\b.{0,10}:", re.I)


# ---------------- tagging ----------------
class Tagger:
    """Links a headline to Nifty 500 stocks only on strong evidence (see the module docstring)."""

    def __init__(self, universe, instruments=None, extra=None):
        names = dict(zip(universe.Symbol, universe["Company Name"]))
        # instruments.csv holds every NSE ticker, but its 'name' column is just the ticker: it is used only for explicit
        # "NSE: XYZ" metadata, never for matching words in a headline.
        self.all_symbols = set(names) | (set(instruments.symbol) if instruments is not None and len(instruments) else set())
        firsts = pd.Series([re.sub(r"^the\s+", "", self._base(n), flags=re.I).split()[0].lower() for n in names.values() if self._base(n)]).value_counts()
        alias, short = {}, set()
        for sym, name in names.items():
            base = re.sub(r"^the\s+", "", self._base(name), flags=re.I)
            words = base.split()
            if len(base) >= 5:
                alias.setdefault(base.lower(), sym)
                if " " not in base and len(re.sub(r"\b(ltd|limited)\b\.?", "", str(name), flags=re.I).split()) > 1:
                    short.add(base.lower())        # "Welspun Corp Ltd" -> "Welspun": a short form, so it gets the short-name checks
            if len(words) >= 3 and words[1].lower() not in ("of", "and", "&", "the", "india") and len(" ".join(words[:2])) >= 6:
                k = " ".join(words[:2]).lower()
                if k not in alias:
                    alias[k] = sym; short.add(k)
            if (len(words) >= 2 and len(words[0]) >= 5 and words[0].lower() not in COMMON_WORDS
                    and firsts.get(words[0].lower(), 0) == 1 and words[0].lower() not in alias):
                alias[words[0].lower()] = sym; short.add(words[0].lower())
        curated = set()
        for sym, al in BUILTIN_ALIASES.items():
            if sym in names:
                for a in al:
                    alias[a.lower()] = sym; short.discard(a.lower())
                    if " " not in a and a.lower() != self._base(names[sym]).lower():
                        curated.add(a.lower())     # curated one-word names: still checked for "<name> <Other Company>"
        for sym, al in SHORT_ALIASES.items():
            if sym in names:
                for a in al:
                    alias[a.lower()] = sym; curated.add(a.lower())
        if extra is not None and len(extra):
            for s, a in zip(extra.symbol, extra.alias):
                if isinstance(a, str) and a.strip() and str(s).strip() in names:
                    alias[a.strip().lower()] = str(s).strip(); short.discard(a.strip().lower())
        self.curated = curated
        self.full = {sym: re.sub(r"\s+", " ", re.sub(r"\b(ltd|limited)\b\.?|[.,]|^the\s+", " ", str(n), flags=re.I)).strip().lower()
                     for sym, n in names.items()}
        self.alias, self.short = alias, short
        keys = sorted(alias, key=len, reverse=True)
        self.name_rx = re.compile(r"(?<![\w&])(" + "|".join(re.escape(k) for k in keys) + r")(?![\w&])", re.I) if keys else None
        tick = sorted([s for s in names if len(s) >= 3 and s not in ACRONYMS and re.fullmatch(r"[A-Z0-9&\-]+", s)], key=len, reverse=True)
        self.tick_rx = re.compile(r"(?<![\w&])(" + "|".join(re.escape(t) for t in tick) + r")(?![\w&])") if tick else None
        self.symbols = set(names)

    @staticmethod
    def _base(name):
        b = SUFFIX.sub("", str(name or "")).replace("(India)", "").replace("(I)", "")
        return re.sub(r"\s+", " ", re.sub(r"[.,]", " ", b)).strip()

    @staticmethod
    def _next_word(t, end):
        m = re.match(r"\s+([A-Za-z][\w'&.-]*)", t[end:])
        return m.group(1) if m else None

    @staticmethod
    def _prev_word(t, start):
        m = re.search(r"([A-Za-z0-9][\w'&.-]*)\s+$", t[:start])
        return m.group(1) if m else None

    def _standalone(self, t, a, b, key, lower_case):
        """A short name must not be part of a longer proper name ('Paras Healthcare', 'Price Action')."""
        nxt = self._next_word(t, b)
        own = self.full.get(self.alias[key], "")
        if nxt and own.startswith(f"{key} {nxt.lower()}"[:len(own)]) and len(own) > len(key):
            return True                            # the headline goes on with the company's own name ("Nippon Life India ...")
        if nxt and nxt[0].isupper() and not nxt.isupper() and not CONTINUE_OK.match(nxt):
            return False
        if key in self.curated:
            return True
        if " " not in key:
            prv = self._prev_word(t, a)
            if prv and prv[0].isupper() and not prv.isupper() and prv.lower() not in CONNECTORS and a > 0:
                return False
            if lower_case and not (nxt and (CONTINUE_OK.match(nxt) or LOWER_OK.match(nxt))):
                return False                       # all-lower-case headline (built from a URL): no capitals to tell names apart
        return True

    def _broker_source(self, t, a, b, key):
        if key not in BROKER_ALIASES:
            return False
        tail = t[b:]
        if BROKER_CTX.match(tail):
            return True
        if re.match(r"^\W*\d*\W*$", tail):          # named last: "...; target of Rs 60: Motilal Oswal" / "... target of rs 60 motilal oswal"
            return bool(re.search(r"[:\-–|]\s*$", t[:a])) or classify(t)[0] == "Broker view"
        return False

    def find(self, text):
        out = []
        for _, _, s in self.spans(text):           # in the order the headline names them
            if s not in out:
                out.append(s)
        return out

    def spans(self, text):
        """[(start, end, symbol)] of every accepted mention, in order."""
        t = str(text or "")
        letters = re.sub(r"[^A-Za-z]", "", t)
        shouting = len(letters) > 12 and sum(c.isupper() for c in letters) / len(letters) > 0.7
        lower_case = not any(c.isupper() for c in t[1:])
        hits = []
        if self.name_rx:
            for m in self.name_rx.finditer(t):
                txt, key = m.group(1), m.group(1).lower()
                if " " not in txt and not txt[0].isupper():
                    continue                       # one-word names must be capitalised: "persistent selling" is not Persistent Systems
                if (key in self.short or key in self.curated) and not lower_case and not txt[0].isupper():
                    continue                       # "life insurance" in a normal headline is words, not LIC
                if (key in self.short or key in self.curated) and not self._standalone(t, m.start(), m.end(), key, lower_case):
                    continue
                if self._broker_source(t, m.start(), m.end(), key):
                    continue
                hits.append((m.start(), m.end(), self.alias[key]))
        if self.tick_rx and not shouting:
            for m in self.tick_rx.finditer(t):
                s = m.group(1)
                if s in AMBIGUOUS_TICKERS and not re.match(r"\s+(shares?|stock|q[1-4])\b", t[m.end():], re.I):
                    continue
                hits.append((m.start(), m.end(), s))
        hits.sort(key=lambda h: (-(h[1] - h[0]), h[0]))
        taken, keep = [], []
        for a, b, s in hits:
            if any(a < y and b > x for x, y in taken):
                continue
            taken.append((a, b))
            keep.append((a, b, s))
        return sorted(keep)

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
    syms, ends = [], []
    for t, tk in zip(d.title, d.tickers):
        sp = tagger.spans(t)                       # the headline only: a summary naming a company is not evidence the story is about it
        found = list(dict.fromkeys(s for _, _, s in sp))
        ends.append([b for _, b, _ in sp])
        for x in re.findall(r"(?:NSE|BOM|BSE)\s*:\s*([A-Z0-9&\-]+)", tk or ""):
            if x in tagger.symbols and x not in found:   # explicit exchange ticker in the feed's own metadata
                found.append(x)
        syms.append(found)
    d["symbols"] = [",".join(x) for x in syms]
    d["sectors"] = [",".join(sorted({sec.get(x, "") for x in xs} - {""})) for xs in syms]
    ty = [classify(t) for t in d.title]
    d["event_type"], d["type_why"] = [a for a, _ in ty], [b for _, b in ty]
    d["roundup"] = [len(xs) >= 4 or bool(ROUNDUP_RX.search(t)) for xs, t in zip(syms, d.title)]
    # tone only when the headline is about exactly one stock and is not a round-up: otherwise whose direction would it be?
    to = [tone_evidence(t, e) if len(xs) == 1 and not r else ("", "") for t, xs, r, e in zip(d.title, syms, d.roundup, ends)]
    d["tone"], d["tone_why"] = [a for a, _ in to], [b for _, b in to]
    d["tag_version"] = TAG_VERSION
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
def _retagged(d):
    """Stored headlines re-labelled with the current rules (keeps when each was fetched and how many sites carried it)."""
    d = d.reset_index(drop=True)
    new = enrich(d.drop(columns=[c for c in ("symbols", "sectors", "event_type", "type_why", "tone", "tone_why", "roundup",
                                             "tag_version", "n_sources", "fetched") if c in d])).reset_index(drop=True)
    for c in ("n_sources", "fetched", "id"):     # same rows in the same order: carry these over as they were
        if c in d:
            new[c] = d[c].where(d[c].notna(), new[c]).values
    new["n_sources"] = pd.to_numeric(new.n_sources, errors="coerce").fillna(1).astype(int)
    return new.drop_duplicates("id")


def load_store(retag_old=True, save=True):
    if STORE.exists():
        d = pd.read_parquet(STORE)
        d["published"] = pd.to_datetime(d.published)
        if retag_old and len(d) and ("tag_version" not in d or (pd.to_numeric(d.tag_version, errors="coerce") != TAG_VERSION).any()):
            d = _retagged(d)                       # the tagging rules changed: never show labels made by the old rules
            try:
                if not save:
                    raise RuntimeError
                from .filelock import locked
                with locked("news", timeout=5):
                    save_store(d)
            except Exception:
                pass                               # read-only use is fine; the next news update saves it
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
        d = load_store(retag_old=False)
        if len(d):
            save_store(_retagged(d))


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
        old = load_store(save=False)               # (re-tagged here if the rules changed; saved just below)
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
