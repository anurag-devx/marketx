"""Labels, purging, baseline consistency, experiments (metadata, skip, failures, new strategies)."""
import json
import numpy as np, pandas as pd, pytest
from screener import config as SC, screen as S, evaluate as V, experiments as X, setups

ALL = list(SC.SETUP_SCHEMA)


def ctx_for(feat, bench):
    c = X.Context.__new__(X.Context)
    c.feat, c.bench, c.today, c.issues = feat, bench, list(feat.symbol.unique()), None
    c.calendar = sorted(bench.date.unique())
    c.data_version = "test-data"
    c.regime = pd.Series("up_calm", index=pd.DatetimeIndex(c.calendar))
    return c


def test_label_alignment(feat, market):
    _, bench = market
    res = S.compute(feat, {}, list(feat.symbol.unique()))
    lab = V.labels(res, [5], bench, sorted(bench.date.unique()))
    a = lab[lab.symbol == "AAA"].reset_index(drop=True)
    i = 300
    assert a.gross_5.iat[i] == pytest.approx(a.close.iat[i + 5] / a.open.iat[i + 1] - 1)   # open t+1 -> close t+5
    assert a.end_5.iat[i] == a.date.iat[i + 5]
    assert a.gross_5.iloc[-5:].isna().all()                                                # no label without future bars


def test_purging_at_boundaries():
    dates = pd.to_datetime(["2020-12-23", "2020-12-31", "2020-12-10", "2023-12-28"])
    ends = pd.to_datetime(["2021-01-01", "2020-12-31", "2020-12-17", "2024-01-03"])
    assert list(V.purge_mask(dates, ends)) == [False, True, True, False]


def test_newey_west_wider_than_naive_for_overlap():
    rng = np.random.default_rng(0)
    x = np.convolve(rng.normal(0, 1, 2000), np.ones(10) / 10, mode="valid")              # overlapping windows
    naive = x.std() / np.sqrt(len(x))
    assert V.newey_west_se(x, 10) > 2 * naive


def test_whole_universe_has_zero_edge(feat, market):
    """If every eligible stock is a signal, the strategy IS the baseline: lift and excess must be 0."""
    _, bench = market
    cfg = {"setups": {s: {"enabled": s == "relative_strength", **({"min_percentile": 0.0} if s == "relative_strength" else {})} for s in ALL}}
    res = S.compute(feat, cfg, list(feat.symbol.unique()))
    ev = V.evaluate(res, [10], bench, sorted(bench.date.unique()), 0.3)
    d = ev["by_period"]["development"][10]
    assert d["excess"] == pytest.approx(0, abs=1e-9) and d["lift_pp"] == pytest.approx(0, abs=1e-9)
    assert d["mean_net"] == pytest.approx(d["mean_gross"] - 0.3)


def test_same_assumptions_across_strategies(feat, market, tmp_project):
    _, bench = market
    write_strategy(tmp_project, "a", {"setups": {s: {"enabled": s == "breakout_volume"} for s in ALL}})
    write_strategy(tmp_project, "b", {"setups": {s: {"enabled": s == "trend_continuation"} for s in ALL}})
    write_exp(tmp_project, "e", ["a", "b"])
    ctx = ctx_for(feat, bench)
    X.run(["e"], ctx=ctx, log=lambda *_: None)
    res = [X.load_result(r) for r in X.index().run_key]
    for r in res:
        assert r["config"]["cost_pct"] == res[0]["config"]["cost_pct"] and r["config"]["horizons"] == res[0]["config"]["horizons"]
        assert r["data_version"] == "test-data" and r["config"]["universe"] == res[0]["config"]["universe"]


def write_strategy(root, name, cfg, version=1):
    (root / "strategies" / f"{name}.json").write_text(json.dumps(dict(name=name, version=version, description="t", hypothesis="t", config=cfg)))


def write_exp(root, name, strategies, grid=None):
    (root / "experiments" / f"{name}.json").write_text(json.dumps(dict(name=name, strategies=strategies, grid=grid or {})))


def test_experiment_metadata_skip_and_rerun(feat, market, tmp_project):
    _, bench = market
    write_strategy(tmp_project, "bo", {"setups": {s: {"enabled": s == "breakout_volume"} for s in ALL}})
    write_exp(tmp_project, "grid", ["bo"], {"setups.breakout_volume.rel_volume_min": [1.5, 2.0]})
    ctx = ctx_for(feat, bench)
    first = X.run(["grid"], ctx=ctx, log=lambda *_: None)
    assert [o["status"] for o in first] == ["ok", "ok"]
    idx = X.index()
    for col in ("run_key", "experiment", "strategy", "strategy_version", "config_id", "params", "data_version",
                "screener_version", "eval_version", "started", "finished", "validated"):
        assert col in idx
    again = X.run(["grid"], ctx=ctx, log=lambda *_: None)
    assert [o["status"] for o in again] == ["skipped", "skipped"]
    forced = X.run(["grid"], force=True, ctx=ctx, log=lambda *_: None)
    assert [o["status"] for o in forced] == ["ok", "ok"]
    a, b = (X.load_result(o["run_key"]) for o in first)
    assert a["params"] != b["params"] and a["config"]["setups"]["breakout_volume"]["rel_volume_min"] == 1.5
    r1 = X.load_result(forced[0]["run_key"])
    assert r1["evaluation"]["by_period"]["development"] == a["evaluation"]["by_period"]["development"]   # reproducible
    assert X.configs_tested() == 2


def test_failed_experiment_is_recorded(feat, market, tmp_project, monkeypatch):
    _, bench = market
    write_strategy(tmp_project, "bo", {})
    write_strategy(tmp_project, "tc", {"setups": {s: {"enabled": s == "trend_continuation"} for s in ALL}})
    write_exp(tmp_project, "e", ["bo", "tc"])
    real = X.evaluate_config
    def boom(c, ctx):
        if c["strategy"] == "bo":
            raise RuntimeError("disk full")
        return real(c, ctx)
    monkeypatch.setattr(X, "evaluate_config", boom)
    out = X.run(["e"], ctx=ctx_for(feat, bench), log=lambda *_: None)
    assert sorted(o["status"] for o in out) == ["failed", "ok"]
    idx = X.index().set_index("strategy")
    assert idx.loc["bo"].status == "failed" and "disk full" in idx.loc["bo"].error
    comp = X.comparison()
    assert set(comp.status) == {"failed", "ok"}


def test_invalid_strategy_file_fails_clearly(tmp_project):
    write_strategy(tmp_project, "bad", {"setups": {"breakout_volume": {"rel_volume_min": 99}}})
    with pytest.raises(SC.ConfigError, match="between"):
        X.load_strategies()


def test_adding_a_setup_does_not_change_existing(feat, monkeypatch):
    before = S.compute(feat, {}, list(feat.symbol.unique()))
    monkeypatch.setitem(setups.SETUPS, "always", lambda f, p, ctx: np.ones(len(f), dtype=bool))
    monkeypatch.setitem(SC.SETUP_SCHEMA, "always", {"enabled": (bool, None, None, False)})
    monkeypatch.setitem(SC.DEFAULT["setups"], "always", {"enabled": False})
    after = S.compute(feat, {}, list(feat.symbol.unique()))
    for s in ALL:
        assert (before[f"{s}_pass"] == after[f"{s}_pass"]).all()
    on = S.compute(feat, {"setups": {"always": {"enabled": True}}}, list(feat.symbol.unique()))
    assert (on.always_pass == on.eligible).all()


def test_enable_records_exact_config(tmp_project):
    write_strategy(tmp_project, "tc", {"setups": {"trend_continuation": {"adx_min": 25}}})
    X.set_enabled("tc@v1", True)
    e = X.enabled()[0]
    assert e["config"]["setups"]["trend_continuation"]["adx_min"] == 25 and e["validated"] is False
    assert X.status_of("tc@v1") == "enabled"
    X.set_enabled("tc@v1", False)
    assert X.status_of("tc@v1") == "experimental"


def test_validation_criteria_need_both_periods():
    good = dict(signals=1000, dates=300, excess=0.5, t_excess=3.0, mean_net=0.4, p5_net=-8, baseline_p5_net=-8)
    ev = {"by_period": {"development": {10: good}, "validation": {10: {**good, "t_excess": 1.0}}},
          "by_year": [dict(horizon=10, period="development", signals=10, excess=0.3)]}
    v = V.validate(ev, 10, 1)
    assert not v["validated"]
    assert [c["ok"] for c in v["checks"] if c["period"] == "validation" and "baseline" in c["test"]] == [False]
    ev["by_period"]["validation"][10]["t_excess"] = 2.5
    assert V.validate(ev, 10, 1)["validated"]
    assert V.validate(ev, 10, 500)["search_t"] > 3.5


def test_neighbour_robustness_column():
    import json as _j
    import pandas as _pd
    from screener.experiments import add_neighbours
    t = _pd.DataFrame([dict(experiment="e", strategy="s", status="ok", params=_j.dumps({"x": v}), **{"val_excess_%": ex})
                       for v, ex in [(1.5, 0.2), (2.0, -0.1), (2.5, 0.3)]])
    out = add_neighbours(t)
    assert list(out.neighbours_val_positive) == ["0 of 1", "2 of 2", "0 of 1"]
