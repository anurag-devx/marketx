"""F&O (futures and options) data from NSE's daily public files: open interest, put-call ratio, futures basis, and how
each type of participant (FIIs, DIIs, clients, proprietary traders) is positioned. Equities analysis stays on the cash
market; this adds context.

Sources (public, no keys; layouts measured on real files on 2026-10-24):
1. F&O bhavcopy, one zip per trading day.
   From 8 Jul 2024 (UDiFF):  https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip
       columns used: TradDt, FinInstrmTp (STF/STO/IDF/IDO), TckrSymb, XpryDt, StrkPric, OptnTp, ClsPric, UndrlygPric,
       OpnIntrst, ChngInOpnIntrst
   Before (old layout):     https://nsearchives.nseindia.com/content/historical/DERIVATIVES/YYYY/MON/foDDMONYYYYbhav.csv.zip
       columns used: INSTRUMENT (FUTSTK/OPTSTK/FUTIDX/OPTIDX), SYMBOL, EXPIRY_DT, STRIKE_PR, OPTION_TYP, CLOSE, OPEN_INT, CHG_IN_OI
   Open interest is in shares (units) in both layouts.
2. Participant-wise open interest (contracts), one CSV per day:
       https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_DDMMYYYY.csv
   Rows Client / DII / FII / Pro / TOTAL; columns Future Index Long/Short, Future Stock Long/Short, Option Index Call/Put
   Long/Short, Option Stock Call/Put Long/Short, Total Long/Short Contracts.

Stored per underlying and day (data/derivatives/fo_daily.parquet):
  fut_oi, fut_oi_chg        futures open interest, all expiries (shares)
  fut_close, fut_expiry     nearest-expiry future
  basis_pct                 (nearest future - underlying) / underlying, when the file has the underlying price
  call_oi, put_oi, pcr      options open interest (shares) and put/call ratio of open interest
Participant positions: data/derivatives/participant_oi.parquet (one row per day and participant type).
Trading days are taken from the stored Nifty series, so holidays are never requested."""
import io, json, sys, time, zipfile
import numpy as np, pandas as pd
from .config import DATA
from . import atomic

DIR = DATA / "derivatives"
DAILY, PART, STATE = DIR / "fo_daily.parquet", DIR / "participant_oi.parquet", DIR / "state.json"
BASE = "https://nsearchives.nseindia.com/content"
NEW_FROM = pd.Timestamp("2024-07-08")
KIND = {"STF": "FUTSTK", "STO": "OPTSTK", "IDF": "FUTIDX", "IDO": "OPTIDX"}
DAILY_COLS = ["date", "symbol", "index", "fut_oi", "fut_oi_chg", "fut_close", "fut_expiry", "underlying", "basis_pct", "call_oi", "put_oi",
              "pcr", "call_oi_chg", "put_oi_chg"]
PART_COLS = ["date", "client", "fut_idx_long", "fut_idx_short", "fut_stk_long", "fut_stk_short", "opt_idx_call_long", "opt_idx_put_long",
             "opt_idx_call_short", "opt_idx_put_short", "total_long", "total_short"]


def bhav_url(day):
    d = pd.Timestamp(day)
    if d >= NEW_FROM:
        return f"{BASE}/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    return f"{BASE}/historical/DERIVATIVES/{d:%Y}/{d.strftime('%b').upper()}/fo{d:%d}{d.strftime('%b').upper()}{d:%Y}bhav.csv.zip"


def part_url(day):
    return f"{BASE}/nsccl/fao_participant_oi_{pd.Timestamp(day):%d%m%Y}.csv"


def _read_zip_csv(content):
    z = zipfile.ZipFile(io.BytesIO(content))
    return pd.read_csv(z.open(z.namelist()[0]))


def normalize(df):
    """Either layout -> date, kind, symbol, expiry, strike, opt, close, underlying, oi, oi_chg. ValueError if unknown."""
    if "FinInstrmTp" in df.columns:
        d = pd.DataFrame(dict(date=pd.to_datetime(df.TradDt), kind=df.FinInstrmTp.map(KIND), symbol=df.TckrSymb.astype(str).str.strip(),
                              expiry=pd.to_datetime(df.XpryDt), strike=pd.to_numeric(df.StrkPric, errors="coerce"),
                              opt=df.OptnTp.fillna("XX").astype(str).str.strip(), close=pd.to_numeric(df.ClsPric, errors="coerce"),
                              underlying=pd.to_numeric(df.UndrlygPric, errors="coerce"), oi=pd.to_numeric(df.OpnIntrst, errors="coerce"),
                              oi_chg=pd.to_numeric(df.ChngInOpnIntrst, errors="coerce")))
    elif "INSTRUMENT" in df.columns:
        d = pd.DataFrame(dict(date=pd.to_datetime(df.TIMESTAMP, format="%d-%b-%Y"), kind=df.INSTRUMENT.astype(str).str.strip(),
                              symbol=df.SYMBOL.astype(str).str.strip(), expiry=pd.to_datetime(df.EXPIRY_DT, format="%d-%b-%Y"),
                              strike=pd.to_numeric(df.STRIKE_PR, errors="coerce"), opt=df.OPTION_TYP.astype(str).str.strip(),
                              close=pd.to_numeric(df.CLOSE, errors="coerce"), underlying=np.nan,
                              oi=pd.to_numeric(df.OPEN_INT, errors="coerce"), oi_chg=pd.to_numeric(df.CHG_IN_OI, errors="coerce")))
    else:
        raise ValueError(f"unknown F&O bhavcopy layout (columns {list(df.columns)[:6]})")
    return d[d.kind.isin(KIND.values())]


def aggregate(n):
    """Normalized rows of one day -> one row per underlying."""
    out = []
    for sym, g in n.groupby("symbol"):
        fut, opt = g[g.kind.str.startswith("FUT")], g[g.kind.str.startswith("OPT")]
        row = dict(date=g.date.iat[0], symbol=sym, index=bool(g.kind.str.endswith("IDX").any()))
        if len(fut):
            near = fut.sort_values("expiry").iloc[0]
            row.update(fut_oi=float(fut.oi.sum()), fut_oi_chg=float(fut.oi_chg.sum()), fut_close=float(near.close), fut_expiry=near.expiry)
        und = g.underlying.dropna()
        if len(und):
            row["underlying"] = float(und.iat[0])
            if row.get("fut_close"):
                row["basis_pct"] = 100 * (row["fut_close"] / row["underlying"] - 1)
        if len(opt):
            c, p = opt[opt.opt == "CE"], opt[opt.opt == "PE"]
            row.update(call_oi=float(c.oi.sum()), put_oi=float(p.oi.sum()), call_oi_chg=float(c.oi_chg.sum()), put_oi_chg=float(p.oi_chg.sum()))
            row["pcr"] = row["put_oi"] / row["call_oi"] if row["call_oi"] > 0 else np.nan
        out.append(row)
    return pd.DataFrame(out).reindex(columns=DAILY_COLS)


def parse_participant(text, day):
    """Participant-wise OI CSV -> rows per participant type. ValueError if the layout is unknown."""
    lines = [l for l in text.splitlines() if l.strip()]
    start = next((i for i, l in enumerate(lines) if l.lower().startswith("client type")), None)
    if start is None:
        raise ValueError("unknown participant-wise OI layout")
    df = pd.read_csv(io.StringIO("\n".join(lines[start:])))
    df.columns = [c.strip().lower() for c in df.columns]
    m = {"client type": "client", "future index long": "fut_idx_long", "future index short": "fut_idx_short", "future stock long": "fut_stk_long",
         "future stock short": "fut_stk_short", "option index call long": "opt_idx_call_long", "option index put long": "opt_idx_put_long",
         "option index call short": "opt_idx_call_short", "option index put short": "opt_idx_put_short", "total long contracts": "total_long",
         "total short contracts": "total_short"}
    if not set(m) <= set(df.columns):
        raise ValueError(f"participant-wise OI: missing columns {sorted(set(m) - set(df.columns))[:3]}")
    df = df[list(m)].rename(columns=m)
    df["client"] = df.client.astype(str).str.strip()
    for c in PART_COLS[2:]:
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce")
    df.insert(0, "date", pd.Timestamp(day).normalize())
    return df[df.client.isin(["Client", "DII", "FII", "Pro", "TOTAL"])][PART_COLS]


def _load(path, cols):
    if not path.exists():
        return pd.DataFrame(columns=cols)
    d = pd.read_parquet(path)
    d["date"] = pd.to_datetime(d.date)
    return d


def daily():
    return _load(DAILY, DAILY_COLS)


def participants():
    return _load(PART, PART_COLS)


def state():
    try:
        return json.loads(STATE.read_text()) if STATE.exists() else {}
    except Exception:
        return {}


def _save(new_daily, new_part, st):
    from .filelock import locked
    DIR.mkdir(parents=True, exist_ok=True)
    with locked("derivatives"):
        if len(new_daily):
            d = pd.concat([x for x in (daily(), new_daily) if len(x)], ignore_index=True).drop_duplicates(["date", "symbol"], keep="last")
            atomic.to_parquet(d.sort_values(["date", "symbol"]).reindex(columns=DAILY_COLS), DAILY)
        if len(new_part):
            p = pd.concat([x for x in (participants(), new_part) if len(x)], ignore_index=True).drop_duplicates(["date", "client"], keep="last")
            atomic.to_parquet(p.sort_values(["date", "client"]), PART)
        atomic.write_text(STATE, json.dumps(st, indent=1, default=str))


def trading_days(start, end=None):
    idx = pd.read_parquet(DATA / "index.parquet", columns=["date"])
    d = pd.to_datetime(idx.date)
    d = d[(d >= pd.Timestamp(start)) & (d <= pd.Timestamp(end or d.max()))]
    return sorted(d.dt.normalize().unique())


def fetch_days(days, fetch, log=print, save_every=20):
    """Downloads both files for each day not yet done. Resumable through state.json."""
    st = state()
    done_b, done_p = set(st.get("bhav_done", [])), set(st.get("part_done", []))
    missing = st.setdefault("missing", {})
    nd, np_, batch_d, batch_p, t0 = 0, 0, [], [], time.time()
    todo = [pd.Timestamp(d) for d in days if f"{pd.Timestamp(d):%Y-%m-%d}" not in done_b or f"{pd.Timestamp(d):%Y-%m-%d}" not in done_p]
    log(f"F&O: {len(todo)} trading day(s) to download")
    for i, day in enumerate(todo, 1):
        k = f"{day:%Y-%m-%d}"
        if k not in done_b:
            try:
                batch_d.append(aggregate(normalize(_read_zip_csv(fetch.get(bhav_url(day))))))
                done_b.add(k); nd += 1
            except FileNotFoundError:
                missing[k] = "no bhavcopy (404)"; done_b.add(k)
            except ValueError as e:
                log(f"{k}: {e}. Stopping; the file layout changed — keep this message for troubleshooting.")
                break
            except Exception as e:
                log(f"{k} bhavcopy: {type(e).__name__} (retried next run)")
        if k not in done_p:
            try:
                batch_p.append(parse_participant(fetch.get(part_url(day)).decode("utf-8", errors="replace"), day))
                done_p.add(k); np_ += 1
            except FileNotFoundError:
                missing[f"{k} participants"] = "404"; done_p.add(k)
            except ValueError as e:
                log(f"{k}: {e}")
            except Exception as e:
                log(f"{k} participant OI: {type(e).__name__} (retried next run)")
        if i % save_every == 0 or i == len(todo):
            st.update(bhav_done=sorted(done_b), part_done=sorted(done_p))
            _save(pd.concat(batch_d, ignore_index=True) if batch_d else pd.DataFrame(), pd.concat(batch_p, ignore_index=True) if batch_p else pd.DataFrame(), st)
            batch_d, batch_p = [], []
            log(f"  {i}/{len(todo)} days ({time.time() - t0:.0f} s)")
    st.update(bhav_done=sorted(done_b), part_done=sorted(done_p), last_update=str(pd.Timestamp.now().floor("s")))
    _save(pd.DataFrame(), pd.DataFrame(), st)
    log(f"F&O: {nd} bhavcopies and {np_} participant files read.")
    return nd


def update(days=10, log=print, fetch=None):
    from .fundamentals import Fetcher
    return fetch_days(trading_days(pd.Timestamp.now().normalize() - pd.Timedelta(days=days * 2)), fetch or Fetcher(gap=1.2), log)


def backfill(start=None, log=print, fetch=None):
    from .fundamentals import Fetcher
    start = start or (pd.Timestamp.now().normalize() - pd.DateOffset(years=1))
    return fetch_days(trading_days(start), fetch or Fetcher(gap=1.2), log)


# ---------------- reading ----------------
def buildup(price_chg, oi_chg):
    """The usual reading of price and futures open interest moving together (a description, not a tested signal)."""
    if price_chg is None or oi_chg is None or not np.isfinite(price_chg) or not np.isfinite(oi_chg):
        return None
    if price_chg >= 0:
        return "long build-up" if oi_chg > 0 else "short covering"
    return "short build-up" if oi_chg > 0 else "long unwinding"


def fii_index_positioning(p=None, as_of=None):
    """FII index futures: long share of their long + short, latest and 5 days earlier (percent)."""
    p = participants() if p is None else p
    f = p[p.client == "FII"].sort_values("date")
    if as_of is not None:
        f = f[f.date <= pd.Timestamp(as_of)]
    if f.empty:
        return None
    ls = 100 * f.fut_idx_long / (f.fut_idx_long + f.fut_idx_short)
    return dict(date=f.date.iat[-1], long_pct=float(ls.iat[-1]), long_pct_5d_ago=float(ls.iat[-6]) if len(ls) > 5 else None,
                series=pd.DataFrame(dict(date=f.date, long_pct=ls)))


if __name__ == "__main__":
    args = dict(a.split("=", 1) for a in sys.argv[2:] if "=" in a)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "update"
    if cmd == "backfill":
        backfill(args.get("start"))
    else:
        update(int(args.get("days", 10)))
