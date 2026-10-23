"""Synthetic, deterministic market data for tests (no network, no real files)."""
import sys
from pathlib import Path
import numpy as np, pandas as pd, pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def make_px(symbols=("AAA", "BBB", "CCC", "DDD", "EEE", "FFF"), start="2015-01-01", end="2025-06-30", seed=1, drift=0.0004):
    dates = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    rows = []
    for i, s in enumerate(symbols):
        r = rng.normal(drift * (1 + 0.3 * i), 0.018, len(dates))
        close = 100 * (1 + i) * np.exp(np.cumsum(r))
        opn = close * np.exp(rng.normal(0, 0.004, len(dates)))
        opn[1:] = close[:-1] * np.exp(rng.normal(0, 0.004, len(dates) - 1))
        hi = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, 0.006, len(dates))))
        lo = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, 0.006, len(dates))))
        vol = rng.integers(200_000, 2_000_000, len(dates)).astype(float)
        rows.append(pd.DataFrame(dict(date=dates, symbol=s, open=opn, high=hi, low=lo, close=close, volume=vol)))
    px = pd.concat(rows, ignore_index=True)
    bench = pd.DataFrame(dict(date=dates, nifty=10000 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(dates))))))
    return px, bench


@pytest.fixture(scope="session")
def market():
    return make_px()


@pytest.fixture(scope="session")
def feat(market):
    from screener import features as F
    px, bench = market
    return F.build(px.assign(company_name="", sector="Test"), bench)


@pytest.fixture
def tmp_project(tmp_path, monkeypatch):
    """Points every file the screener and experiments write at a temporary folder."""
    from core import db, config as C
    from screener import config as SC, experiments as X
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.duckdb")
    monkeypatch.setattr(SC, "STRAT_DIR", tmp_path / "strategies")
    monkeypatch.setattr(SC, "EXP_DIR", tmp_path / "experiments")
    monkeypatch.setattr(SC, "ENABLED", tmp_path / "enabled.json")
    monkeypatch.setattr(SC, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(X, "RES_DIR", tmp_path / "out" / "experiments")
    (tmp_path / "strategies").mkdir()
    (tmp_path / "experiments").mkdir()
    return tmp_path
