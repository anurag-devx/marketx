"""Market Lab: point-in-time scenario, event windows, analog estimates, drivers, the walk-forward metrics and the routes."""
import numpy as np, pandas as pd
import pytest
from marketlab import features as F, engine as E, evaluate as V


def synth(n_sym=30, n_days=420, seed=0):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2023-01-02", periods=n_days)
    rows = []
    for k in range(n_sym):
        vol = 0.01 + 0.02 * (k % 3) / 2
        c = 100 * np.exp(np.cumsum(rng.normal(0, vol, n_days)))
        o = c * np.exp(rng.normal(0, vol / 3, n_days))
        rows.append(pd.DataFrame({"date": days, "symbol": f"S{k}", "open": o, "high": np.maximum(o, c) * 1.005, "low": np.minimum(o, c) * 0.995,
                                  "close": c, "volume": rng.integers(1e5, 1e6, n_days).astype(float)}))
    px = pd.concat(rows, ignore_index=True)
    idx = pd.DataFrame({"date": days, "nifty": 100 * np.exp(np.cumsum(rng.normal(0, 0.008, n_days))), "vix": 14 + rng.normal(0, 1, n_days)})
    uni = pd.DataFrame({"Symbol": [f"S{k}" for k in range(n_sym)], "Industry": ["A", "B", "C"] * (n_sym // 3)})
    return px, idx, uni


def test_decision_day_uses_21h_cutoff():
    days = pd.to_datetime(["2026-10-21", "2026-10-22", "2026-10-23"]).values
    ts = pd.to_datetime(["2026-10-21 20:59", "2026-10-21 21:01", "2026-10-22 16:00", "2026-10-24 10:00"])
    out = pd.to_datetime(F.decision_day(ts, days))
    assert list(out[:3].strftime("%Y-%m-%d")) == ["2026-10-21", "2026-10-22", "2026-10-22"] and pd.isna(out[3])


def test_features_are_point_in_time():
    px, idx, uni = synth()
    a = F.build(px, idx, uni, log=lambda *_: None)
    t = px.date.unique()[300]
    px2 = px.copy()
    px2.loc[px2.date > t, ["open", "high", "low", "close"]] *= 1.5          # change the future only
    b = F.build(px2, idx, uni, log=lambda *_: None)
    fa, fb = a[a.date == t].set_index("symbol")[F.FEATURES], b[b.date == t].set_index("symbol")[F.FEATURES]
    pd.testing.assert_frame_equal(fa, fb)
    assert not np.allclose(a[a.date == t].cc.values, b[b.date == t].cc.values)   # the outcome does see the next day


def test_events_land_on_the_right_day():
    px, idx, uni = synth(n_sym=3, n_days=60)
    d0, d1 = px.date.unique()[40], px.date.unique()[41]
    ann = pd.DataFrame({"id": ["a", "b", "c"], "published": [d0 + pd.Timedelta(hours=11), d0 + pd.Timedelta(hours=17), d0 + pd.Timedelta(hours=22)],
                        "symbol": ["S0", "S1", "S2"], "category": ["Results", "Results", "Order / contract"]})
    d = F.build(px, idx, uni, ann=ann, log=lambda *_: None).set_index(["symbol", "date"])
    assert d.loc[("S0", d0), "e_res_s"] == 1 and d.loc[("S0", d0), "e_res_e"] == 0
    assert d.loc[("S1", d0), "e_res_e"] == 1                                 # after the close, before 21:00 -> tonight
    assert d.loc[("S2", d0), "a_cnt"] == 0 and d.loc[("S2", d1), "a_cnt"] > 0 and d.loc[("S2", d1), "a_mat"] == 1   # after 21:00 -> next day


def test_estimates_are_probabilities_and_drivers_cover_every_node():
    px, idx, uni = synth()
    d = F.build(px, idx, uni, log=lambda *_: None)
    last = d.date.max()
    lib, q = d[(d.next_date <= last) & d.cc.notna()], d[d.date == last].reset_index(drop=True)
    L = E.Library(lib, k=50)
    est, idx_, dist = L.estimate(q)
    for c in [c for c in est if c.startswith(("ch_", "up_"))]:
        assert est[c].between(0, 1).all()
    assert idx_.shape == (len(q), 50)
    dv = L.drivers(q, est)
    assert set(dv.node) == set(F.GROUPS) and len(dv) == len(q) * len(F.GROUPS) * len(F.MOVES) * len(F.SIZES)


def test_vol_scaling_makes_calm_stocks_need_bigger_relative_moves():
    px, idx, uni = synth()
    d = F.build(px, idx, uni, log=lambda *_: None).dropna(subset=["cc"])
    q = d[d.date == d.date.max() - pd.Timedelta(days=1)].head(10)
    lib = d[d.date < q.date.min()]
    a = E.Library(lib, k=50, vol_scale=False).estimate(q)[0]
    b = E.Library(lib, k=50, vol_scale=True).estimate(q)[0]
    calm = q.f_rv.to_numpy() < np.median(lib.f_rv)
    assert (b["ch_cc_2.0"].to_numpy()[calm] <= a["ch_cc_2.0"].to_numpy()[calm] + 1e-9).all()


def test_lean_and_confidence_rules():
    assert list(E.lean_of(np.array([0.7, 0.3, 0.52, 0.9]), np.array([40, 40, 40, 5]))) == ["up", "down", "unclear", "unclear"]
    assert list(E.confidence(np.array([0.2, 0.9]), np.array([50, 50]), True)) == ["high", "low"]
    assert list(E.confidence(np.array([0.2]), np.array([50]), False)) == ["low"]


def test_metrics_reward_a_ranking_that_finds_big_moves():
    rng = np.random.default_rng(1)
    q = pd.DataFrame({"date": np.repeat(pd.bdate_range("2024-01-01", periods=20), 50), "cc": rng.normal(0, 0.012, 1000)})
    big = (q.cc.abs() >= 0.01).astype(float)
    est = pd.DataFrame({"ch_cc_1.0": 0.2 + 0.6 * big, "us_cc_1.0": 0.4, "up_cc_1.0": np.where(q.cc > 0, 0.8, 0.2), "nb_cc_1.0": 40})
    m = V.metrics(q, est, "cc", 1.0)
    assert m["lift"] > 0.3 and m["auc"] > 0.95 and m["brier_skill"] > 0 and m["dir_hit"] == 1.0


def test_routes_read_the_saved_radar(tmp_path, monkeypatch):
    from marketlab import radar as R
    from web import api
    px, idx, uni = synth()
    d = F.build(px, idx, uni, log=lambda *_: None)
    monkeypatch.setattr(R, "DIR", tmp_path)
    monkeypatch.setattr(R, "panel", lambda log=print, save=True: d)
    R.run(log=lambda *_: None)
    r = api.ROUTES[("GET", "marketlab/radar")]({"move": "cc", "size": "1"})
    assert r["ready"] and len(r["rows"]) == 30 and all(len(x["nodes"]) == len(F.GROUPS) for x in r["rows"])
    s = api.ROUTES[("GET", "marketlab/stock")]({"symbol": "S3", "move": "oc", "size": "2"})
    assert s["verdict"]["lean"] in ("up", "down", "unclear") and len(s["cases"]) == R.N_CASES and s["spread"]["p10"] <= s["spread"]["p90"]
    assert not api.ROUTES[("GET", "marketlab/radar")]({"when": "pre"})["ready"]


def test_scaler_keeps_every_feature():
    """Regression: clipping only some columns must not blank out the others."""
    px, idx, uni = synth()
    d = F.build(px, idx, uni, log=lambda *_: None).dropna(subset=F.FEATURES[:14])
    sc = E.Scaler().fit(d)
    z = sc.transform(d)
    varying = [c for c in F.FEATURES if d[c].std() > 0]
    assert all(np.abs(z[:, sc.cols.index(c)]).max() > 0 for c in varying)


def test_macro_changes_count_from_publication_and_are_not_carried_forward():
    days = pd.bdate_range("2026-10-01", periods=20)
    d = pd.DataFrame({"date": days, "symbol": "S0"})
    obs = pd.bdate_range("2026-09-28", periods=6)                     # Brent observed for 6 days, then nothing new
    macro = pd.DataFrame({"date": obs, "series": "DCOILBRENTEU", "value": [100, 101, 99, 99, 103, 103]})
    out = F._macro(d.copy(), macro).set_index("date")
    # the 2.4% rise observed on 2 Oct is known 7 days later (9 Oct), not the next day
    assert out.loc["2026-10-05", "x_brent"] == 0 or abs(out.loc["2026-10-05", "x_brent"]) < 3
    assert out.loc["2026-10-09", "x_brent"] == pytest.approx(100 * (103 / 99 - 1))
    assert (out.loc["2026-10-16":, "x_brent"] == 0).all()           # stale: no new observation, no change carried forward
    assert (out.x_vix == 0).all()                                       # a missing series is neutral


def test_confidence_uses_the_stocks_own_features_and_flags_unusual_days():
    px, idx, uni = synth()
    d = F.build(px, idx, uni, log=lambda *_: None)
    last = d.date.max()
    lib, q = d[(d.next_date <= last) & d.cc.notna()], d[d.date == last].reset_index(drop=True)
    L = E.Library(lib, k=50)
    base = L.estimate(q)[0]
    wild = q.copy()
    wild[[f for g in E.DAY_GROUPS for f in F.GROUPS[g]]] = 50.0          # an extreme market / macro day for everyone
    w = L.estimate(wild)[0]
    assert (w.day_pct >= 0.99).all() and base.day_pct.between(0, 1).all()
    assert w.dist_pct.median() <= base.dist_pct.median() + 0.35     # the stock's own confidence isn't wrecked by the day
