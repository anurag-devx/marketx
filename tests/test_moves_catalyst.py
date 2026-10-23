"""Daily moves counts / catalysts, and the Catalyst similarity search + impact."""
import numpy as np, pandas as pd, pytest
from research import daily_moves as DM, catalyst as CA
from core import news as N


def _px():
    dates = pd.bdate_range("2025-01-01", periods=60)
    rets = {"AAA": 0.0, "BBB": 0.0, "CCC": 0.0, "DDD": 0.0}
    rows = []
    for s in rets:
        r = np.zeros(len(dates))
        r[30] = {"AAA": 0.06, "BBB": 0.025, "CCC": -0.03, "DDD": 0.012}[s]
        r[40] = 0.03 if s in ("AAA", "BBB", "CCC") else 0.0
        c = 100 * np.cumprod(1 + r)
        rows.append(pd.DataFrame(dict(date=dates, symbol=s, close=c, volume=1e6 * (1 + (np.arange(60) == 30)))))
    idx = pd.DataFrame(dict(date=dates, nifty=10000.0))
    return pd.concat(rows), idx, dates


INFO = pd.DataFrame(dict(symbol=["AAA", "BBB", "CCC", "DDD"], company=list("ABCD"), nifty_industry=["Information Technology"] * 3 + ["Financial Services"],
                         sector=["Information Technology"] * 3 + ["Financial Services"], industry=["IT"] * 3 + ["Banks"],
                         basic_industry=["IT"] * 3 + ["Banks"], shares=[1e7, np.nan, 1e7, 1e7]))


def test_daily_counts_thresholds():
    px, idx, dates = _px()
    P = DM.panel(px, idx)
    c = DM.daily_counts(P).set_index("date")
    r = c.loc[dates[30]]
    assert (r.up_1, r.up_2, r.up_5, r.down_1, r.down_2, r.down_5) == (3, 2, 1, 1, 1, 0)
    assert c.loc[dates[10]].up_1 == 0


def test_day_table_catalysts_and_market_cap():
    px, idx, dates = _px()
    P = DM.panel(px, idx)
    news = pd.DataFrame(dict(published=[dates[30] + pd.Timedelta(hours=11), dates[39] + pd.Timedelta(hours=18)],
                             symbols=["AAA", ""], roundup=[False, False], title=["AAA wins deal", "IT stocks rally on software demand"], link=["a", "b"]))
    cat = DM.Catalysts(news, None, P["dates"])
    d = DM.day_table(P, dates[30], INFO, cat).set_index("symbol")
    assert d.at["AAA", "catalyst_type"] == "stock news / filing" and "AAA wins deal" in d.at["AAA", "catalyst"]
    assert d.at["AAA", "market_cap_cr"] == pytest.approx(1e7 * 106 / 1e7)
    assert pd.isna(d.at["BBB", "market_cap_cr"])
    d40 = DM.day_table(P, dates[40], INFO, cat).set_index("symbol")      # evening news -> next day, sector moved together
    assert d40.at["BBB", "catalyst_type"] == "sector news"
    assert d40.at["DDD", "catalyst_type"] == "none found"


def test_sectors_in():
    assert DM.sectors_in("Crude jumps 5% as OPEC cuts output") == ["Oil Gas & Consumable Fuels"]
    assert "Financial Services" in DM.sectors_in("RBI keeps repo rate unchanged")


def test_tokens_mask_company_names():
    uni = pd.DataFrame({"Company Name": ["Infosys Ltd.", "HDFC Bank Ltd."], "Industry": ["IT", "Fin"], "Symbol": ["INFY", "HDFCBANK"]})
    tg = N.Tagger(uni)
    a, b = CA.tokens("Infosys Q2 profit rises 10%", tg), CA.tokens("HDFC Bank Q2 profit rises 5%", tg)
    assert a == b and "zzcompany" in a


def test_index_search_and_impact():
    px, idx, dates = _px()
    P = DM.panel(px, idx)
    docs = pd.DataFrame(dict(text=["AAA bags large defence order", "BBB bags large defence order from ministry", "Rupee falls to record low"],
                             published=[dates[30] + pd.Timedelta(hours=10), dates[40] + pd.Timedelta(hours=10), dates[20] + pd.Timedelta(hours=10)],
                             symbols=["AAA", "BBB", ""], kind="News", event_type=["Order / contract", "Order / contract", "Other"], link=""))
    ix = CA.Index(docs)
    s = ix.search("CCC bags defence order worth crores", min_score=0.1)
    assert list(s.symbols)[:2] in (["AAA", "BBB"], ["BBB", "AAA"]) and "" not in list(s.symbols)
    assert ix.search("CCC bags order", exclude_after=dates[35]).symbols.tolist() == ["AAA"]   # no look-ahead
    r = CA.impact(s, P, INFO, min_days=2)
    assert r["days"] == 2
    named = r["named"].set_index("symbol")
    assert named.at["AAA", "move_day"] == pytest.approx(6.0)
    assert named.at["AAA", "peers_day"] == pytest.approx(-0.25)          # median of BBB +2.5, CCC -3
    assert set(r["sectors"].sector) == {"Information Technology"}         # sectors need 3+ stocks
