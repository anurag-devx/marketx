"""Macro and geopolitical data: fetch, store, read. Free sources, no keys.

Series store: data/macro/series.parquet, long format (date, series, value, source).
  FRED (US data, CSV, no key):  DGS10 US 10-year yield (%), DFF Fed funds (%), DTWEXBGS broad dollar index,
                                DCOILBRENTEU Brent ($/bbl), DEXINUS rupees per US$, VIXCLS US VIX
  GPR (Caldara & Iacoviello, matteoiacoviello.com): daily Geopolitical Risk index GPRD, GPRD_ACT (acts), GPRD_THREAT
  GDELT DOC 2.0 (global news): daily article count and tone per THEME. GDELT keeps only 3 months, so the store
                                keeps growing from the first fetch.
  NSE: FII / DII net buying (₹ crore) for the latest day; history accumulates from the first fetch.
  Your headlines: daily count per THEME from the stored news (data/news), computed on read.
India releases (CPI, GDP, IIP, PMI, GST, repo rate): data/macro/india.csv, edited on the page (official releases).
Calendar of upcoming events: data/macro/calendar.csv, edited on the page."""
import io, json, re, time
from urllib.parse import quote
import pandas as pd
from .config import DATA
from . import atomic

DIR = DATA / "macro"
STORE = DIR / "series.parquet"
INDIA = DIR / "india.csv"
CALENDAR = DIR / "calendar.csv"
LOG = DIR / "last_fetch.json"

FRED = {"DGS10": ("US 10-year yield", "%"), "DFF": ("US Fed funds rate", "%"), "DTWEXBGS": ("US dollar index (broad)", "index"),
        "DCOILBRENTEU": ("Brent crude", "$/bbl"), "DEXINUS": ("USD/INR", "₹ per $"), "VIXCLS": ("US VIX", "index")}
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={s}"
GPR_PAGE = "https://www.matteoiacoviello.com/gpr.htm"
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode={m}&format=json&{t}"
NSE_FIIDII = "https://www.nseindia.com/api/fiidiiTradeReact"

# theme: (label, GDELT query, headline regex)
THEMES = {
    "india_pakistan": ("India–Pakistan", '(India OR Indian) (Pakistan OR Pakistani) (military OR attack OR border OR ceasefire OR terror)',
                       r"\bpakistan\b|line of control|\bloc\b.{0,20}(firing|ceasefire)|operation sindoor|cross-border (terror|firing)"),
    "middle_east": ("Middle East conflict", '(Israel OR Iran OR Gaza OR Hezbollah OR Houthi) (strike OR attack OR war OR missile)',
                    r"\bisrael|\biran\b|\biranian|gaza|hezbollah|houthi|middle east (tension|conflict|war|crisis)|west asia (tension|conflict|war|crisis)"),
    "russia_ukraine": ("Russia–Ukraine / Russian oil", '(Russia OR Russian) (Ukraine OR Ukrainian) (war OR attack OR offensive OR ceasefire)',
                       r"\bukrain|\brussia"),
    "china_taiwan": ("China–Taiwan / South China Sea", '(China OR Chinese) (Taiwan OR "South China Sea") (military OR drills OR tension)',
                     r"taiwan|south china sea|(china|chinese).{0,25}\b(military|drills|troops|border|ladakh|lac)\b"),
    "us_tariffs": ("US tariffs / trade war", '(tariff OR tariffs OR "trade war" OR "trade deal") (Trump OR "United States" OR Washington)',
                   r"(trump|\bus\b|u\.s\.|america|washington|white house|reciprocal).{0,60}\btariffs?\b|\btariffs?\b.{0,60}(trump|\bus\b|u\.s\.|america|washington)|trade war|trade deal|\bh-?1b\b"),
    "sanctions": ("Sanctions", 'sanctions (oil OR crude OR Russia OR Iran OR China)', r"\bsanction"),
    "oil_supply": ("Oil supply / OPEC", '(OPEC OR "oil supply" OR "crude output" OR "oil production")',
                   r"\bopec|crude (output|supply|production|prices?|imports?)|oil (supply|output|prices?|imports?)|brent"),
    "shipping": ("Red Sea / Hormuz shipping", '("Red Sea" OR "Strait of Hormuz" OR Suez) (shipping OR attack OR tanker OR vessel)',
                 r"red sea|hormuz|suez|shipping (route|lane|disruption)|freight (rates?|prices?) (surge|jump|spike)"),
    "india_elections": ("Indian elections", '(India OR Indian) (election OR elections OR polls) (BJP OR Congress OR vote)',
                        r"lok sabha|assembly (election|polls|results)|exit polls?|election commission of india|\bbjp\b|"
                        r"(bihar|maharashtra|delhi|karnataka|tamil nadu|kerala|west bengal|uttar pradesh|gujarat|punjab|assam|jharkhand|haryana)\W+(election|polls)"),
    "central_banks": ("RBI / Fed policy", '("Federal Reserve" OR "Reserve Bank of India" OR RBI) ("rate cut" OR "rate hike" OR "interest rate" OR policy)',
                      r"repo rate|monetary policy|\bmpc\b|policy rate|rate (cut|hike)s?|\bfomc\b|federal reserve|powell|"
                      r"\bfed\b.{0,30}\b(rate|cut|hike|policy|meeting)|\brbi\b.{0,40}\b(rate|policy|liquidity|inflation|governor|crr)\b"),
}

INDIA_COLS = ["indicator", "period", "value", "unit", "released", "source", "note"]
INDIA_DEFAULT_INDICATORS = ["CPI inflation (y/y)", "WPI inflation (y/y)", "GDP growth (y/y)", "IIP growth (y/y)", "Manufacturing PMI",
                            "Services PMI", "GST collections", "RBI repo rate", "Trade deficit", "Forex reserves"]
CAL_COLS = ["date", "event", "country", "importance", "note"]


# ---------------- store ----------------
def load():
    if STORE.exists():
        d = pd.read_parquet(STORE)
        d["date"] = pd.to_datetime(d.date)
        return d
    return pd.DataFrame(columns=["date", "series", "value", "source"])


def _merge_save(new):
    from .filelock import locked
    if new is None or new.empty:
        return 0
    new = new.dropna(subset=["value"]).copy()
    new["date"] = pd.to_datetime(new.date).dt.normalize()
    with locked("macro"):
        old = load()
        d = pd.concat([old, new], ignore_index=True).drop_duplicates(["date", "series"], keep="last").sort_values(["series", "date"])
        DIR.mkdir(parents=True, exist_ok=True)
        atomic.to_parquet(d, STORE)
        return len(d) - len(old)


def wide(series=None, start=None):
    """date x series table (daily, as published, no filling)."""
    d = load()
    if series:
        d = d[d.series.isin(series)]
    if start is not None:
        d = d[d.date >= pd.Timestamp(start)]
    return d.pivot_table(index="date", columns="series", values="value").sort_index()


# ---------------- parsers (kept separate so they can be tested without the network) ----------------
def parse_fred(text, sid):
    d = pd.read_csv(io.StringIO(text))
    dc = "observation_date" if "observation_date" in d else ("DATE" if "DATE" in d else d.columns[0])
    vc = sid if sid in d else d.columns[-1]
    out = pd.DataFrame({"date": pd.to_datetime(d[dc], errors="coerce"), "value": pd.to_numeric(d[vc], errors="coerce")})
    return out.dropna().assign(series=sid, source="FRED")


def gpr_link(html):
    """Absolute link to the latest daily GPR .xls from the GPR page HTML."""
    links = re.findall(r'href="([^"]*data_gpr_daily_recent[^"]*\.xlsx?)"', html, re.I)
    if not links:
        raise ValueError("daily GPR file link not found on the GPR page")
    u = links[0]
    return u if u.startswith("http") else "https://www.matteoiacoviello.com/" + u.lstrip("/")


def parse_gpr(df):
    """GPR daily sheet -> long rows for GPRD, GPRD_ACT, GPRD_THREAT."""
    cols = {c.lower(): c for c in df.columns}
    dcol = cols.get("date") or cols.get("day") or next((c for c in df.columns if "date" in str(c).lower()), None)
    if dcol is None:
        raise ValueError("GPR file has no date column")
    dates = pd.to_datetime(df[dcol], errors="coerce")
    out = []
    for name in ("GPRD", "GPRD_ACT", "GPRD_THREAT"):
        c = cols.get(name.lower())
        if c is not None:
            out.append(pd.DataFrame({"date": dates, "value": pd.to_numeric(df[c], errors="coerce"), "series": name, "source": "GPR"}))
    if not out:
        raise ValueError("GPR file has none of GPRD / GPRD_ACT / GPRD_THREAT")
    return pd.concat(out).dropna()


def parse_gdelt(j, series):
    rows = []
    for tl in (j or {}).get("timeline", []):
        for p in tl.get("data", []):
            rows.append(dict(date=pd.to_datetime(str(p.get("date", ""))[:8], format="%Y%m%d", errors="coerce"),
                             value=p.get("value"), series=series, source="GDELT"))
    d = pd.DataFrame(rows, columns=["date", "value", "series", "source"])
    return d.dropna().groupby(["date", "series", "source"], as_index=False).value.sum() if series.endswith("_vol") else \
        d.dropna().groupby(["date", "series", "source"], as_index=False).value.mean()


def parse_fiidii(j):
    """NSE fiidiiTradeReact -> long rows FII_NET / DII_NET (₹ crore)."""
    recs = j if isinstance(j, list) else (j or {}).get("data", [])
    out = []
    for r in recs:
        cat = str(r.get("category", "")).upper()
        name = "FII_NET" if "FII" in cat or "FPI" in cat else "DII_NET" if "DII" in cat else None
        if not name:
            continue
        try:
            v = float(str(r.get("netValue", "")).replace(",", ""))
        except ValueError:
            continue
        out.append(dict(date=pd.to_datetime(r.get("date"), format="%d-%b-%Y", errors="coerce"), value=v, series=name, source="NSE"))
    if recs and not out:
        raise ValueError("unexpected FII/DII response shape")
    return pd.DataFrame(out, columns=["date", "value", "series", "source"]).dropna()


# ---------------- fetch ----------------
def fetch_all(log=print, gdelt_days=None):
    from .news_sources import Polite
    p = Polite(gap=6.0)                       # GDELT asks for at most one request every 5 seconds
    status = {}
    from . import secrets
    for sid in FRED:
        try:
            try:
                n = _merge_save(parse_fred(p.get(FRED_URL.format(s=sid)).decode("utf-8", "ignore"), sid))
            except PermissionError:
                key = secrets.get("FRED_API_KEY")           # free key from fred.stlouisfed.org, in ~/.swing_env
                if not key:
                    raise
                import requests
                r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                                 params=dict(series_id=sid, api_key=key, file_type="json"), timeout=30)
                r.raise_for_status()
                obs = pd.DataFrame(r.json().get("observations", []))
                n = _merge_save(pd.DataFrame({"date": pd.to_datetime(obs.date), "value": pd.to_numeric(obs.value, errors="coerce"),
                                              "series": sid, "source": "FRED"}))
            status[sid] = f"ok (+{n})"
        except Exception as e:
            status[sid] = f"failed: {type(e).__name__}"
        log(f"FRED {sid}: {status[sid]}")
    try:
        html = p.get(GPR_PAGE).decode("utf-8", "ignore")
        link = gpr_link(html)
        raw = p.get(link)
        df = pd.read_excel(io.BytesIO(raw))
        n = _merge_save(parse_gpr(df))
        status["GPR"] = f"ok (+{n})"
    except ImportError:
        status["GPR"] = "failed: needs the xlrd package (Settings → AI models → Install missing packages)"
    except Exception as e:
        status["GPR"] = f"failed: {type(e).__name__}: {str(e)[:120]}"
    log(f"GPR: {status['GPR']}")
    have = load()
    for key, (label, q, _) in THEMES.items():
        last = have[have.series == f"GDELT_{key}_vol"].date.max() if len(have) else pd.NaT
        days = gdelt_days or (90 if pd.isna(last) else max(3, min(90, (pd.Timestamp.now() - last).days + 2)))
        for mode, suf in (("timelinevolraw", "vol"), ("timelinetone", "tone")):
            try:
                j = json.loads(p.get(GDELT.format(q=quote(q), m=mode, t=f"timespan={days}d")).decode("utf-8", "ignore") or "{}")
                n = _merge_save(parse_gdelt(j, f"GDELT_{key}_{suf}"))
                status[f"GDELT {key} {suf}"] = f"ok (+{n})"
            except Exception as e:
                status[f"GDELT {key} {suf}"] = f"failed: {type(e).__name__}"
        log(f"GDELT {label}: {status[f'GDELT {key} vol']}")
    try:
        from .announcements import NSESession
        n = _merge_save(parse_fiidii(NSESession(gap=1.5).get_json(NSE_FIIDII)))
        status["FII/DII"] = f"ok (+{n})"
    except Exception as e:
        status["FII/DII"] = f"failed: {type(e).__name__}: {str(e)[:120]}"
    log(f"NSE FII/DII: {status['FII/DII']}")
    DIR.mkdir(parents=True, exist_ok=True)
    LOG.write_text(json.dumps(dict(when=time.strftime("%Y-%m-%d %H:%M:%S"), status=status), indent=1))
    return status


def last_fetch():
    try:
        return json.loads(LOG.read_text())
    except Exception:
        return None


# ---------------- your headlines by theme ----------------
_THEME_RX = {k: re.compile(v[2], re.I) for k, v in THEMES.items()}


def themes_in(text):
    return [k for k, rx in _THEME_RX.items() if rx.search(str(text or ""))]


def headline_themes(news=None):
    """Stored headlines tagged with themes: one row per (headline, theme)."""
    from . import news as N
    news = N.load_store() if news is None else news
    if news is None or news.empty:
        return pd.DataFrame(columns=["published", "theme", "title", "source", "link", "tone"])
    rows = []
    for r in news.itertuples():
        for t in themes_in(r.title):
            rows.append(dict(published=r.published, theme=t, title=r.title, source=r.source, link=r.link, tone=r.tone))
    return pd.DataFrame(rows, columns=["published", "theme", "title", "source", "link", "tone"])


# ---------------- India releases and calendar (edited on the page) ----------------
def india_table():
    if INDIA.exists():
        return pd.read_csv(INDIA)
    return pd.DataFrame(columns=INDIA_COLS)


def save_india(d):
    DIR.mkdir(parents=True, exist_ok=True)
    d = d.dropna(subset=["indicator", "period"]).copy()
    atomic.to_csv(d, INDIA)


def calendar():
    if CALENDAR.exists():
        d = pd.read_csv(CALENDAR)
        d["date"] = pd.to_datetime(d.date, errors="coerce")
        return d
    return pd.DataFrame(columns=CAL_COLS)


def save_calendar(d):
    DIR.mkdir(parents=True, exist_ok=True)
    atomic.to_csv(d.dropna(subset=["date", "event"]), CALENDAR)


FLOWS = ("FII_NET", "DII_NET")


def _change(s, n, unit, sid):
    """Change over n observations: points for rates (%), running total for flows, % for the rest."""
    if len(s) <= n:
        return None
    if sid in FLOWS:
        return round(float(s.iloc[-n:].sum()), 1)
    last, prev = s.iat[-1], s.iat[-1 - n]
    if unit == "%":
        return round(float(last - prev), 3)
    return round(float(100 * (last / prev - 1)), 2) if prev else None


def summary(day=None):
    """Latest values and changes for the main series (as of `day`), for the page and the market report.
    Rates (%): change in points. Flows (FII/DII, ₹ crore): chg_5d / chg_20d are 5- and 20-day TOTALS, no 1-day change.
    Everything else: % change."""
    w = wide()
    if w.empty:
        return []
    if day is not None:
        w = w[w.index <= pd.Timestamp(day)]
    out = []
    for sid, (label, unit) in list(FRED.items()) + [("GPRD", ("Geopolitical risk (GPR daily)", "index")),
                                                     ("FII_NET", ("FII net buying", "₹ cr")), ("DII_NET", ("DII net buying", "₹ cr"))]:
        if sid not in w:
            continue
        s = w[sid].dropna()
        if s.empty:
            continue
        last, d = s.iat[-1], s.index[-1]
        hist = s.iloc[-260:]
        z = float((last - hist.mean()) / hist.std()) if len(hist) > 30 and hist.std() > 0 else None
        flow = sid in FLOWS
        out.append(dict(series=sid, label=label, unit=unit, date=str(d.date()), value=round(float(last), 3),
                        chg_1d=None if flow else _change(s, 1, unit, sid), chg_5d=_change(s, 5, unit, sid), chg_20d=_change(s, 20, unit, sid),
                        z_1y=None if z is None or flow else round(z, 2),
                        change_kind="total" if flow else "points" if unit == "%" else "%"))
    return out


if __name__ == "__main__":
    fetch_all()
