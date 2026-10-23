"""Company details for the Nifty 500 stocks: sector / industry from the Nifty 500 list.

The NSE quote API this used to download from (finer industry levels and shares outstanding) is closed to outside
use, so nothing is downloaded any more. load() keeps the same columns: sector, industry and basic_industry all fall
back to the Nifty 500 list's "Industry", and shares is blank (market cap is not shown). A company_info.csv left
from an earlier download is still read if it exists."""
import pandas as pd
from .config import DATA

FILE = DATA / "company_info.csv"
COLS = ["symbol", "macro", "sector", "industry", "basic_industry", "shares", "isin", "listing_date", "fetched"]


def load(raw=False):
    """One row per Nifty 500 stock with sector / industry (falling back to the Nifty 500 'Industry') and shares."""
    info = pd.read_csv(FILE) if FILE.exists() else pd.DataFrame(columns=COLS)
    if raw:
        return info
    uni = pd.read_csv(DATA / "universe.csv").rename(columns={"Symbol": "symbol", "Company Name": "company", "Industry": "nifty_industry"})
    d = uni.merge(info, on="symbol", how="left")
    d["sector"] = d.sector.where(d.sector.fillna("") != "", d.nifty_industry)
    d["industry"] = d.industry.where(d.industry.fillna("") != "", d.sector)
    d["basic_industry"] = d.basic_industry.where(d.basic_industry.fillna("") != "", d.industry)
    return d[["symbol", "company", "nifty_industry", "macro", "sector", "industry", "basic_industry", "shares"]]
