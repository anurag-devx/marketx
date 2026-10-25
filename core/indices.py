"""NSE sector and broad indices (daily OHLC from Angel One, same source as Nifty 50 / India VIX), saved to
data/indices.parquet (date, index, open, high, low, close). Downloaded by fetch_angel.py with every price update.

SECTOR_INDEX maps the universe's NSE industry to the official sector index used for 'the sector's move'.
Industries without a clean index (Capital Goods, Services, Construction, Textiles, Diversified ...) keep the
median of their own stocks."""
import pandas as pd
from core import config as C

FILE = C.DATA / "indices.parquet"
INDICES = {   # column name -> names Angel may list it under (matched case-insensitively, spaces ignored)
    "nifty_bank": ["NIFTY BANK", "BANKNIFTY"], "nifty_fin": ["NIFTY FIN SERVICE", "FINNIFTY", "NIFTY FINANCIAL SERVICES"],
    "nifty_it": ["NIFTY IT"], "nifty_pharma": ["NIFTY PHARMA"], "nifty_health": ["NIFTY HEALTHCARE", "NIFTY HEALTHCARE INDEX"],
    "nifty_metal": ["NIFTY METAL"], "nifty_auto": ["NIFTY AUTO"], "nifty_fmcg": ["NIFTY FMCG"], "nifty_realty": ["NIFTY REALTY"],
    "nifty_energy": ["NIFTY ENERGY"], "nifty_oilgas": ["NIFTY OIL AND GAS", "NIFTY OIL & GAS"], "nifty_media": ["NIFTY MEDIA"],
    "nifty_psubank": ["NIFTY PSU BANK"], "nifty_pvtbank": ["NIFTY PVT BANK", "NIFTY PRIVATE BANK"],
    "nifty_durables": ["NIFTY CONSR DURBL", "NIFTY CONSUMER DURABLES"], "nifty_chem": ["NIFTY CHEMICALS"],
    "nifty_infra": ["NIFTY INFRA", "NIFTY INFRASTRUCTURE"], "nifty_cpse": ["NIFTY CPSE"],
    "nifty_mid150": ["NIFTY MIDCAP 150", "NIFTY MIDCAP150"], "nifty_small250": ["NIFTY SMALLCAP 250", "NIFTY SMLCAP 250"],
    "nifty_500": ["NIFTY 500"], "nifty_next50": ["NIFTY NEXT 50"],
}
SECTOR_INDEX = {"Financial Services": "nifty_fin", "Information Technology": "nifty_it", "Healthcare": "nifty_health",
                "Metals & Mining": "nifty_metal", "Automobile and Auto Components": "nifty_auto", "Fast Moving Consumer Goods": "nifty_fmcg",
                "Realty": "nifty_realty", "Oil Gas & Consumable Fuels": "nifty_oilgas", "Power": "nifty_energy",
                "Media Entertainment & Publication": "nifty_media", "Consumer Durables": "nifty_durables", "Chemicals": "nifty_chem"}
BROAD = ("nifty_500", "nifty_next50", "nifty_mid150", "nifty_small250")


def _norm(x):
    return "".join(str(x).upper().split())


def tokens(scrip_nse):
    """{column: token} for the indices Angel lists (scrip master rows with instrumenttype AMXIDX)."""
    ix = scrip_nse[scrip_nse.instrumenttype == "AMXIDX"]
    by = {}
    for col in ("name", "symbol"):
        for k, t in zip(ix[col].map(_norm), ix.token):
            by.setdefault(k, t)
    out, missing = {}, []
    for c, names in INDICES.items():
        t = next((by[_norm(n)] for n in names if _norm(n) in by), None)
        (out.__setitem__(c, t) if t is not None else missing.append(c))
    return out, missing


def load():
    return pd.read_parquet(FILE) if FILE.exists() else pd.DataFrame(columns=["date", "index", "open", "high", "low", "close"])


def wide(field="close"):
    d = load()
    if d.empty:
        return pd.DataFrame()
    return d.pivot_table(index="date", columns="index", values=field).sort_index()
