"""
Step 1 - Download daily price data for the Nifty 500 universe + market context.
Run from Terminal (needs normal internet):
    cd ~/"Personal Project - Finance Based/swing"
    python3 fetch_data.py
Output: data/prices.parquet (all stocks), data/index.parquet (Nifty 50, India VIX), data/universe.csv
"""
import subprocess, sys, io, time
for pkg in ["yfinance", "pandas", "pyarrow", "requests", "tabulate"]:
    try:
        __import__(pkg)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])

import pandas as pd, requests, yfinance as yf
from pathlib import Path

START = "2015-01-01"
OUT = Path(__file__).parent / "data"
OUT.mkdir(exist_ok=True)

# 1. Universe: current Nifty 500 constituents from NSE
url = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
hdr = {"User-Agent": "Mozilla/5.0"}
try:
    uni = pd.read_csv(io.StringIO(requests.get(url, headers=hdr, timeout=30).text))
except Exception:
    uni = pd.read_csv(io.StringIO(requests.get(url.replace("nsearchives", "archives"), headers=hdr, timeout=30).text))
uni.columns = [c.strip() for c in uni.columns]
uni = uni[["Company Name", "Industry", "Symbol"]]
uni.to_csv(OUT / "universe.csv", index=False)
tickers = [s + ".NS" for s in uni["Symbol"]]
print(f"Universe: {len(tickers)} stocks")

# 2. Stock prices (split/bonus adjusted), in batches
frames = []
for i in range(0, len(tickers), 50):
    batch = tickers[i:i + 50]
    df = yf.download(batch, start=START, auto_adjust=True, group_by="ticker",
                     progress=False, threads=True)
    for t in batch:
        try:
            d = df[t].dropna(how="all").copy()
        except KeyError:
            continue
        if d.empty:
            continue
        d["symbol"] = t.replace(".NS", "")
        frames.append(d.reset_index())
    print(f"  downloaded {min(i + 50, len(tickers))}/{len(tickers)}")
    time.sleep(1)
px = pd.concat(frames)
px.columns = [c.lower() for c in px.columns]
px = px[["date", "symbol", "open", "high", "low", "close", "volume"]]
px.to_parquet(OUT / "prices.parquet", index=False)
print(f"Prices: {px.symbol.nunique()} stocks, {len(px):,} rows, {px.date.min().date()} to {px.date.max().date()}")

# 3. Market context
idx = yf.download(["^NSEI", "^INDIAVIX"], start=START, auto_adjust=True, progress=False)["Close"]
idx = idx.rename(columns={"^NSEI": "nifty", "^INDIAVIX": "vix"}).reset_index()
idx.columns = [c.lower() for c in idx.columns]
idx.to_parquet(OUT / "index.parquet", index=False)
print("Index data saved. Done.")
