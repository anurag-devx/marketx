"""Indicators, warm-up, grouping, missing data."""
import numpy as np, pandas as pd, pytest
from screener import features as F
from tests.conftest import make_px


def one(close, high=None, low=None, vol=None, start="2020-01-01"):
    n = len(close)
    close = np.asarray(close, float)
    d = pd.DataFrame(dict(date=pd.bdate_range(start, periods=n), symbol="X", open=close, close=close,
                          high=close + 1 if high is None else high, low=close - 1 if low is None else low,
                          volume=np.full(n, 1000.0) if vol is None else vol))
    return F.build(d)


def test_sma_known_values():
    f = one(np.arange(1, 251))
    assert np.isnan(f.sma20.iat[18]) and f.sma20.iat[19] == pytest.approx(10.5)
    assert f.sma200.iat[199] == pytest.approx(100.5)
    assert np.isnan(f.sma200.iat[198])


def test_wilder_rsi_seed_and_recursion():
    close = np.array([44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64])
    f = one(close)
    d = np.diff(close)
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    au, ad = up[:14].mean(), dn[:14].mean()
    assert f.rsi14.iat[14] == pytest.approx(100 - 100 / (1 + au / ad))        # seed = simple mean of first 14 changes
    au, ad = (au * 13 + up[14]) / 14, (ad * 13 + dn[14]) / 14
    assert f.rsi14.iat[15] == pytest.approx(100 - 100 / (1 + au / ad))        # Wilder recursion
    assert np.isnan(f.rsi14.iat[13])
    assert 69 < f.rsi14.iat[14] < 72                                           # classic Wilder example: about 70.5


def test_rsi_extremes():
    assert one(np.arange(1, 40.0)).rsi14.iat[-1] == pytest.approx(100)
    assert one(np.tile([10.0, 11.0], 30)).rsi14.iat[-1] == pytest.approx(50, abs=5)


def test_adx_strong_trend_and_warmup():
    c = np.arange(100, 200.0)
    f = one(c, high=c + 0.5, low=c - 0.5)
    assert np.isnan(f.adx14.iat[26]) and not np.isnan(f.adx14.iat[27])        # 14 for DI + 14 for ADX
    assert f.adx14.iat[-1] > 95                                                # one-way trend: -DM is always 0


def test_previous_high_excludes_today():
    c = np.full(40, 100.0)
    h = np.full(40, 101.0)
    h[-1] = 150.0                                                              # today's own high is the highest
    f = one(c, high=h)
    assert f.prev_high_20.iat[-1] == pytest.approx(101.0)
    assert np.isnan(f.prev_high_20.iat[19]) and f.prev_high_20.iat[20] == pytest.approx(101.0)


def test_relative_volume_uses_previous_20():
    v = np.full(30, 100.0)
    v[-1] = 300.0
    f = one(np.full(30, 50.0), vol=v)
    assert f.rel_volume_20d.iat[-1] == pytest.approx(3.0)


def test_bollinger_percentile_warmup_and_value():
    rng = np.random.default_rng(0)
    c = 100 + np.cumsum(rng.normal(0, 1, 400))
    f = one(c)
    first = f.bb_width.first_valid_index()
    assert first == 19
    assert np.isnan(f.bb_width_pctile_252.iat[19 + 250]) and not np.isnan(f.bb_width_pctile_252.iat[19 + 251])
    w = f.bb_width.to_numpy()
    i = 350
    assert f.bb_width_pctile_252.iat[i] == pytest.approx(100 * (w[i - 251:i + 1] <= w[i]).mean())


def test_symbols_never_mix(market):
    px, bench = market
    a = F.build(px[px.symbol == "AAA"], bench)
    b = px.copy()
    b.loc[b.symbol == "BBB", "close"] *= 3                                    # change another stock
    both = F.build(b, bench)
    pd.testing.assert_frame_equal(a.reset_index(drop=True)[F.FEATURE_COLS],
                                  both[both.symbol == "AAA"].reset_index(drop=True)[F.FEATURE_COLS])
    first_b = both[both.symbol == "BBB"].iloc[0]
    assert np.isnan(first_b.rsi14_prev) and first_b.history_bars == 1


def test_missing_sessions_not_filled(market):
    px, bench = market
    a = px[px.symbol == "AAA"].copy()
    drop = a.index[1000:1010]                                                  # a 10-session suspension
    f = F.build(a.drop(drop), bench)
    assert len(f) == len(a) - 10                                               # no rows invented
    row = f[f.date == a.loc[1010, "date"]].iloc[0]
    assert row.valid_ratio_60d == pytest.approx(50 / 60)
    assert f.valid_ratio_60d.iloc[-1] == pytest.approx(1.0)


def test_regime_score_is_graded():
    import pandas as pd
    from agents.regime import score_parts
    t = pd.Series(dict(nifty=22889.0, nifty_sma200=24168.0, nifty_sma50=23514.0, vix=14.77, vix_median=12.9, breadth_200=0.37))
    p = score_parts(t)
    assert p["trend200"] == 0 and 0 < p["trend50"] < 5 and 5 < p["volatility"] < 10 and 4 < p["breadth"] < 5   # Mon 19 Oct 2026
    up = pd.Series(dict(nifty=110.0, nifty_sma200=100.0, nifty_sma50=104.0, vix=10.0, vix_median=13.0, breadth_200=0.75))
    assert sum(score_parts(up).values()) == 100
