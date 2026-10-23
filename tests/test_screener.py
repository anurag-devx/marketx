"""Filters, setups, ranking, point-in-time behaviour, empty results, reproducibility, canonical listings."""
import numpy as np, pandas as pd, pytest
from screener import config as SC, screen as S, setups, universe as U, features as F
from tests.conftest import make_px

ALL = list(SC.SETUP_SCHEMA)


def run(feat, cfg=None):
    return S.compute(feat, cfg or {}, today_symbols=list(feat.symbol.unique()), issues=None)


def test_rsi_cross_logic(feat):
    res = run(feat, {"setups": {s: {"enabled": s == "pullback_recovery"} for s in ALL}})
    hit = res[res.pullback_recovery_pass]
    assert len(hit) > 0
    assert (hit.rsi14_prev < 45).all() and (hit.rsi14 >= 45).all()
    assert (hit.close > hit.sma200).all() and (hit.sma50 > hit.sma200).all()
    # the day after a cross is not a cross
    nxt = res.groupby("symbol").pullback_recovery_pass.shift(1).fillna(False).astype(bool)
    assert not (res.pullback_recovery_pass & nxt & (res.rsi14_prev >= 45)).any()


def test_breakout_needs_close_above_previous_highs(feat):
    res = run(feat, {"setups": {s: {"enabled": s == "breakout_volume"} for s in ALL}})
    hit = res[res.breakout_volume_pass]
    assert len(hit) > 0
    assert (hit.close > hit.prev_high_20).all() and (hit.rel_volume_20d >= 1.5).all()


def test_setups_are_independent(feat):
    base = run(feat)
    other = run(feat, {"setups": {"breakout_volume": {"enabled": False}, "trend_continuation": {"adx_min": 40}}})
    for s in ("pullback_recovery", "relative_strength", "volatility_breakout"):
        assert (base[f"{s}_pass"] == other[f"{s}_pass"]).all()
    assert not other.breakout_volume_pass.any()


def test_combination_logic(feat):
    any_ = run(feat, {"combine": "any"})
    all_ = run(feat, {"combine": "all"})
    k2 = run(feat, {"combine": "min_k", "min_setups": 2})
    n = any_[[f"{s}_pass" for s in ALL]].sum(axis=1)
    assert (any_.qualifies == (n >= 1)).all()
    assert (k2.qualifies == (n >= 2)).all()
    assert (all_.qualifies == (n == 5)).all()


def test_filters_have_reasons(feat):
    res = run(feat, {"filters": {"min_close": 150}})
    low = res[res.close < 150]
    assert (~low.f_min_close).all() and (~low.eligible).all()
    assert low.rejection_reasons.str.contains("close below 150").all()
    assert not res[res.history_bars < 252].eligible.any()
    assert not (res.qualifies & ~res.eligible).any()                         # a setup never rescues an ineligible stock


def test_point_in_time_percentiles(feat):
    """Adding an ineligible stock with a huge return must not change anyone's percentile or rank."""
    d = feat.date.iloc[-1]
    a = run(feat)
    extra = feat[feat.symbol == "AAA"].copy()
    extra["symbol"] = "ZZZ"
    extra["return_126d"] *= 50
    extra["rel_return_126"] *= 50
    extra["close"] = 1.0                                                       # fails the price filter
    b = run(pd.concat([feat, extra], ignore_index=True))
    cols = ["relative_strength_pass", "ranking_score", "rank"]
    pa = a[a.date == d].set_index("symbol")[cols]
    pb = b[(b.date == d) & (b.symbol != "ZZZ")].set_index("symbol")[cols]
    pd.testing.assert_frame_equal(pa.sort_index(), pb.sort_index())


def test_no_lookahead(market):
    """Changing (or removing) prices after a screening date must not change that date's results."""
    px, bench = market
    px = px.assign(company_name="", sector="Test")
    d = pd.Timestamp("2021-06-15")
    full = F.build(px, bench)
    later = px.copy()
    m = later.date > d
    later.loc[m, ["open", "high", "low", "close"]] *= np.linspace(0.5, 3, m.sum())[:, None]
    later.loc[m, "volume"] *= 7
    changed = F.build(later, bench)
    cut = F.build(px[px.date <= d], bench[bench.date <= d])
    cols = F.FEATURE_COLS + ["setups_passed", "eligible", "qualifies", "ranking_score", "rank"] + [f"{s}_pass" for s in ALL]
    res = [run(x)[lambda r: r.date == d].set_index("symbol")[cols].sort_index() for x in (full, changed, cut)]
    pd.testing.assert_frame_equal(res[0], res[1])
    pd.testing.assert_frame_equal(res[0], res[2])


def test_empty_result_with_diagnostics(feat):
    res = S.screen(None, {"filters": {"min_close": 99999}}, data=(feat, list(feat.symbol.unique()), None))
    assert res["shortlist"].empty
    assert res["diagnostics"]["eligible"] == 0 and res["diagnostics"]["fail: price too low"] == res["diagnostics"]["stocks with data"]
    assert res["rows"].rejection_reasons.str.len().gt(0).all()
    assert res["meta"]["config"]["filters"]["min_close"] == 99999           # not relaxed


def test_top_n_and_all_modes(feat):
    data = (feat, list(feat.symbol.unique()), None)
    a = S.screen(None, {"mode": "all", "setups": {"relative_strength": {"min_percentile": 0}}}, data=data)
    t = S.screen(None, {"mode": "top_n", "top_n": 2, "setups": {"relative_strength": {"min_percentile": 0}}}, data=data)
    assert len(a["shortlist"]) == a["diagnostics"]["qualify"]
    assert len(t["shortlist"]) == min(2, t["diagnostics"]["qualify"])
    assert list(t["shortlist"]["rank"]) == sorted(t["shortlist"]["rank"])


def test_reproducible(feat):
    data = (feat, list(feat.symbol.unique()), None)
    x, y = S.screen("2022-03-01", {}, data=data), S.screen("2022-03-01", {}, data=data)
    cols = [c for c in S.OUTPUT_COLS if c != "run_at" and c in x["rows"]]
    pd.testing.assert_frame_equal(x["rows"][cols], y["rows"][cols])
    assert x["meta"]["config_id"] == y["meta"]["config_id"] == SC.config_id({})


def test_config_validation_fails_clearly():
    for bad, msg in [({"setups": {"breakout_volume": {"rel_volume_min": -1}}}, "between"),
                     ({"setups": {"nope": {}}}, "unknown setup"),
                     ({"setups": {"trend_continuation": {"rsi_min": 80, "rsi_max": 70}}}, "rsi_min"),
                     ({"filters": {"min_history": "lots"}}, "whole number"),
                     ({"ranking": {"method": "magic"}}, "ranking.method"),
                     ({"combine": "min_k", "min_setups": 9}, "min_setups"),
                     ({"setups": {s: {"enabled": False} for s in ALL}}, "at least one")]:
        with pytest.raises(SC.ConfigError, match=msg):
            SC.validate(bad)


def test_canonical_listing_prefers_nse_and_keeps_ids():
    l = pd.DataFrame([dict(company_key="INE1", exchange="BSE", symbol="500001", security_id="b1", median_value_20d_cr=50),
                      dict(company_key="INE1", exchange="NSE", symbol="ABC", security_id="n1", median_value_20d_cr=10),
                      dict(company_key="INE2", exchange="BSE", symbol="500002", security_id="b2", median_value_20d_cr=5)])
    c = U.canonicalize(l)
    assert len(c) == 2
    r = c.set_index("company_key").loc["INE1"]
    assert r.exchange == "NSE" and r.alt_exchange == "BSE" and r.alt_security_id == "b1"
    assert c.set_index("company_key").loc["INE2"].exchange == "BSE"


def test_etf_heuristic():
    assert U.is_etf("NIFTYBEES") and U.is_etf("GOLDBEES") and U.is_etf("XYZ", "Some Mutual Fund ETF")
    assert not U.is_etf("RELIANCE", "Reliance Industries")


def test_data_quality_errors_block_from_when_known(feat):
    iss = pd.DataFrame([dict(symbol="AAA", date="2020-05-05", check="bad_tick", severity="error", detail="", value=0, reviewed=False)])
    flagged, text = S.quality_masks(feat, iss)
    aaa = feat.symbol == "AAA"
    on_day = flagged[(aaa & (feat.date == "2020-05-05")).to_numpy()]
    after = flagged[(aaa & (feat.date == "2020-05-06")).to_numpy()]
    assert not on_day.any() and after.all()                                    # a bad tick is only knowable the next day
    assert not flagged[(feat.symbol != "AAA").to_numpy()].any()
