"""Nightly forecast batch, stock lists and the forecast scorecard."""
import json
import numpy as np, pandas as pd, pytest
from conftest import make_px

from core import stocklists as SL
from debate import batch as B, scorecard as SC, evaluate as EV, probability as PR, store


# ---------------- stock lists ----------------
def test_parse_nse_index_file():
    d = SL.parse_index_csv("Company Name,Industry,Symbol,Series,ISIN Code\nReliance Industries Ltd.,Oil Gas,RELIANCE ,EQ,INE002A01018\n")
    assert list(d.Symbol) == ["RELIANCE"] and list(d.columns) == ["Company Name", "Industry", "Symbol"]
    with pytest.raises(ValueError):
        SL.parse_index_csv("a,b\n1,2\n")


@pytest.fixture
def lists_dir(tmp_path, monkeypatch):
    from core import config as C
    monkeypatch.setattr(SL, "DIR", tmp_path / "lists")
    monkeypatch.setattr(SL, "CUSTOM", tmp_path / "lists" / "custom.json")
    uni = pd.DataFrame({"Company Name": ["A", "B", "C", "D"], "Industry": ["IT", "IT", "Banks", "Banks"], "Symbol": ["AAA", "BBB", "CCC", "DDD"]})
    monkeypatch.setattr(SL, "_universe", lambda: uni)
    (tmp_path / "lists").mkdir()
    return tmp_path


def test_resolve_rules_and_exclusions(lists_dir):
    SL.save_custom("Mine", ["ddd", "EEE"])
    sel = {"name": "t", "include": [{"type": "sector", "name": "IT"}, {"type": "custom", "name": "Mine"}],
           "exclude": [{"type": "symbols", "symbols": ["BBB"]}]}
    syms, notes = SL.resolve(sel, available={"AAA", "BBB", "CCC", "DDD"})
    assert syms == ["AAA", "DDD"]
    assert any("EEE" in n for n in notes)                          # no price data -> reported, skipped


def test_missing_index_is_reported_not_guessed(lists_dir):
    syms, notes = SL.resolve({"name": "x", "include": [{"type": "index", "name": "NIFTY 50"}]}, available={"AAA"})
    assert syms == [] and "not available" in notes[0]
    pd.DataFrame({"Symbol": ["AAA", "ZZZ"]}).to_csv(SL._file("NIFTY 50"), index=False)
    syms, _ = SL.resolve({"name": "x", "include": [{"type": "index", "name": "NIFTY 50"}]}, available={"AAA"})
    assert syms == ["AAA"]


# ---------------- batch ----------------
SYMS = tuple(f"S{i:02d}" for i in range(30))


@pytest.fixture(scope="module")
def fixture_data():
    px, bench = make_px(SYMS, start="2015-01-01", end="2024-03-29", seed=4)
    px["volume"] = px.volume * 100
    uni = pd.DataFrame({"Company Name": [f"{s} Ltd" for s in SYMS], "Industry": ["IT"] * 15 + ["Banks"] * 15, "Symbol": list(SYMS)})
    return dict(px=px, idx=bench.assign(vix=14.0), uni=uni)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    from debate import engine as EN
    from core import config as C, quality as Q, search as SE, filelock
    for k, v in dict(DIR=tmp_path, RUNS=tmp_path / "runs", INDEX=tmp_path / "f.parquet", OUTCOMES=tmp_path / "o.parquet",
                     PROGRESS=tmp_path / "p.json").items():
        monkeypatch.setattr(store, k, v)
    monkeypatch.setattr(B, "FORECASTS", tmp_path / "batch.parquet")
    monkeypatch.setattr(B, "CONFIG", tmp_path / "batch.json")
    monkeypatch.setattr(EN, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(PR, "MODELS", tmp_path / "models")
    monkeypatch.setattr(C, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(Q, "SUMMARY", tmp_path / "q.json")
    monkeypatch.setattr(Q, "ISSUES", tmp_path / "q.csv")
    monkeypatch.setattr(SE, "EXPOSURES", tmp_path / "e.csv")
    monkeypatch.setattr(filelock, "LOCKS", tmp_path / "locks")
    return tmp_path


def test_batch_forecasts_only_the_selection_but_learns_from_all(fixture_data, lab):
    cfg = dict(B.DEFAULT, selection={"name": "five", "include": [{"type": "symbols", "symbols": ["S01", "S02", "S03", "S04", "S05"]}], "exclude": []})
    n = B.run(days=2, as_of="2024-03-01", log=lambda *a: None, data=fixture_data, cfg=cfg)
    d = B.load()
    assert n == len(d) == 5 * 2 * 3                                 # 5 stocks x 2 targets x 3 days
    assert set(d.symbol) == {"S01", "S02", "S03", "S04", "S05"}
    assert set(d.target) == {"rel", "abs"} and set(d.horizon) == {5}
    assert set(d["mode"]) == {"retrospective"}                      # the fixture has later data, so these are replays
    m = json.loads((lab / "models" / f"{d.model_key.iloc[0]}.json").read_text())
    assert m["n_train"] > 5 * 2000 / 5                              # trained on all 30 stocks, not just the five
    p = d.dropna(subset=["p_up"])
    assert np.allclose(p.p_up + p.p_down, 1)
    # rerunning never overwrites: same rows stay, nothing new
    before = d.copy()
    assert B.run(days=2, as_of="2024-03-01", log=lambda *a: None, data=fixture_data, cfg=cfg) == 0
    pd.testing.assert_frame_equal(B.load(), before)
    # scoring after the horizon: realised return matches the prices
    out = EV.score(d, fixture_data["px"], fixture_data["idx"].set_index("date").nifty)
    assert len(out) == len(d) and set(out.source) == {"batch"} and out.sector.notna().all()
    r = out[(out.symbol == "S01") & (out.target == "abs") & (out.as_of == "2024-03-01")].iloc[0]
    s = fixture_data["px"][fixture_data["px"].symbol == "S01"].set_index("date").close
    i = s.index.get_loc(pd.Timestamp("2024-03-01"))
    assert r.realized_return_pct == pytest.approx(100 * (s.iat[i + 5] / s.iat[i] - 1))
    rr = out[(out.symbol == "S01") & (out.target == "rel") & (out.as_of == "2024-03-01")].iloc[0]
    n_ = fixture_data["idx"].set_index("date").nifty
    assert rr.realized_return_pct == pytest.approx(100 * (s.iat[i + 5] / s.iat[i] - n_.iloc[n_.index.get_loc(s.index[i + 5])] / n_.loc[s.index[i]]))


# ---------------- scorecard ----------------
def _outcomes(informative, n=3000, seed=0):
    rng = np.random.default_rng(seed)
    p = rng.uniform(0.3, 0.7, n)
    y = (rng.random(n) < (p if informative else 0.5)).astype(float)
    days = pd.bdate_range("2026-01-01", periods=60)
    as_of = rng.choice(days, n)
    d = pd.DataFrame(dict(as_of=[str(pd.Timestamp(x).date()) for x in as_of], end_date=[str((pd.Timestamp(x) + pd.Timedelta(days=7)).date()) for x in as_of],
                          symbol=rng.choice(["A", "B", "C"], n), target="rel", p_up=p, base_up=0.5, outcome=np.where(y == 1, "positive", "not positive"),
                          realized_return_pct=np.where(y == 1, 1.0, -1.0) * rng.uniform(0.1, 3, n), source="batch", mode="live", verdict="Insufficient evidence",
                          sector="IT", regime="up_calm", list_name="t"))
    d["brier"] = (d.p_up - y) ** 2
    d["brier_base"] = (0.5 - y) ** 2
    d["logloss"] = -(y * np.log(d.p_up) + (1 - y) * np.log(1 - d.p_up))
    return d


def test_scorecard_detects_skill_and_its_absence():
    good = SC.headline(SC.binary(_outcomes(True)))
    assert good["skill_lo"] > 0 and "Better" in good["status"]
    bad = SC.headline(SC.binary(_outcomes(False)))
    assert bad["skill"] < 0.005 and "Better" not in bad["status"]


def test_scorecard_small_samples_are_flagged():
    h = SC.headline(SC.binary(_outcomes(True, n=50)))
    assert "too few" in h["status"]
    g = SC._group(SC.binary(_outcomes(True, n=50)), "symbol")
    assert not g.enough.any()


def test_buckets_rise_when_the_model_is_informative():
    d = SC.deciles(SC.binary(_outcomes(True)))
    assert d.share_positive.iloc[-1] > d.share_positive.iloc[0] + 0.2
    r = SC.reliability(SC.binary(_outcomes(True)))
    assert (r.n * (r.predicted - r.observed).abs()).sum() / r.n.sum() < 0.05      # calibration error of a calibrated forecaster


def test_misses_are_confident_and_wrong():
    m = SC.misses(SC.binary(_outcomes(True)))
    assert len(m) == 20 and ((m.p_up > 0.5) != (m.outcome == "positive")).all()
    assert (m.p_up - 0.5).abs().is_monotonic_decreasing


def test_old_outcome_files_without_new_columns_still_work():
    d = _outcomes(True, n=200).drop(columns=["source", "mode", "sector", "regime", "list_name"])
    o = SC.binary(d)
    assert set(o.source) == {"debate"} and set(o["mode"]) == {"live"}
    assert SC.headline(o)["n"] == 200
