"""Which securities can be screened.

Data today: Angel One NSE cash-market bars for symbols in the "-EQ" series (fetch_angel.py). That already
leaves out SME boards (SM/ST series), bonds and most odd instruments. ETFs also trade in the EQ series and
are removed by name (heuristic below; documented limitation). BSE bars are not downloaded yet, so every
company currently has one listing; canonicalize() is ready for when a BSE feed is added."""
import re
import numpy as np, pandas as pd

ETF_PATTERNS = [r"BEES$", r"ETF", r"^NIFTY\d", r"^BANKNIFTY", r"IETF$", r"^GOLD(?:CASE|IETF|SHARE|1)", r"^SILVER\d?",
                r"^LIQUID", r"^MON100", r"^MAFANG", r"^NV20", r"^N100", r"^HNGSNG", r"^CPSE", r"^BHARATIWIN"]
_ETF = re.compile("|".join(ETF_PATTERNS))


def is_etf(symbol, name=""):
    return bool(_ETF.search(str(symbol).upper())) or " ETF" in f" {str(name).upper()}" or "EXCHANGE TRADED" in str(name).upper()


def canonicalize(listings, key="company_key", prefer="NSE"):
    """listings: one row per (company, exchange) with columns company_key (ISIN or another stable id),
    exchange, symbol, security_id, median_value_20d_cr. Keeps ONE row per company: the preferred exchange
    if it trades, else the more liquid one. The other listing's identifiers are kept in alt_* columns."""
    if listings.empty:
        return listings.assign(alt_exchange=None, alt_symbol=None, alt_security_id=None)
    l = listings.copy()
    l["_pref"] = (l.exchange == prefer).astype(int)
    l["_liq"] = l.median_value_20d_cr.fillna(-1)
    l = l.sort_values([key, "_pref", "_liq"], ascending=[True, False, False])
    first = l.groupby(key, sort=False).head(1).set_index(key)
    rest = l.groupby(key, sort=False).nth(1).set_index(key) if (l.groupby(key).size() > 1).any() else pd.DataFrame(columns=l.columns).set_index(key)
    out = first.copy()
    out["alt_exchange"] = rest.exchange.reindex(out.index)
    out["alt_symbol"] = rest.symbol.reindex(out.index)
    out["alt_security_id"] = rest.security_id.reindex(out.index)
    out["alt_median_value_20d_cr"] = rest.median_value_20d_cr.reindex(out.index)
    return out.drop(columns=["_pref", "_liq"]).reset_index()


def membership_mask(feat, universe, today_symbols, pit_n=500):
    """Boolean array: is (symbol, date) in the chosen universe on that date?
    all            every downloaded stock (the hard filters decide eligibility, point in time)
    nifty500_today today's Nifty 500 list (survivorship-biased in history; fine for today's screen)
    pit_top        top pit_n by traded value at each March/September review (core/universe_pit.py)"""
    if universe == "all":
        return np.ones(len(feat), dtype=bool)
    if universe == "nifty500_today":
        return feat.symbol.isin(set(today_symbols)).to_numpy()
    from core import universe_pit
    mem, _ = universe_pit.membership(feat[["date", "symbol", "close", "volume"]], n=int(pit_n))
    return universe_pit.mask(feat.date.to_numpy(), feat.symbol.to_numpy(), mem)


UNIVERSE_NOTE = {
    "all": "All downloaded NSE stocks that pass the filters on each date. History includes only stocks still listed "
           "today (delisted stocks are not available from Angel One), so results are somewhat flattered.",
    "nifty500_today": "Today's Nifty 500 list. In history this is survivorship-biased: stocks that fell out are missing.",
    "pit_top": "Top N by traded value at each March/September review, using only data known then.",
}
