"""F&O data: both bhavcopy layouts and the participant file are read as measured on real NSE files (tests/fixtures,
trimmed to three underlyings), days are only requested once, and a 404 (no file that day) is not retried forever."""
from pathlib import Path
import pandas as pd, pytest
from core import derivatives as D

FX = Path(__file__).parent / "fixtures"


def test_both_bhavcopy_layouts():
    new = D.aggregate(D.normalize(D._read_zip_csv((FX / "fo_bhav_20261023.zip").read_bytes()))).set_index("symbol")
    old = D.aggregate(D.normalize(D._read_zip_csv((FX / "fo_bhav_old_20240628.zip").read_bytes()))).set_index("symbol")
    assert set(new.index) == set(old.index) == {"NIFTY", "RELIANCE", "TCS"}
    r = new.loc["RELIANCE"]
    assert r.fut_oi == 163108500 and r.fut_expiry == pd.Timestamp("2026-10-27") and abs(r.basis_pct - (100 * (1187 / 1190 - 1))) < 1e-9
    assert abs(r.pcr - r.put_oi / r.call_oi) < 1e-12 and bool(new.loc["NIFTY", "index"]) and not bool(r["index"])
    assert old.loc["NIFTY"].fut_oi == 15490575 and pd.isna(old.loc["NIFTY"].basis_pct)          # old layout has no underlying price
    with pytest.raises(ValueError):
        D.normalize(pd.DataFrame({"x": [1]}))


def test_participants_and_fii_positioning():
    p = D.parse_participant((FX / "participant_oi_23102026.csv").read_text(), "2026-10-23")
    f = p.set_index("client").loc["FII"]
    assert f.fut_idx_long == 36986 and f.fut_idx_short == 317169 and list(p.client) == ["Client", "DII", "FII", "Pro", "TOTAL"]
    pos = D.fii_index_positioning(p)
    assert abs(pos["long_pct"] - 100 * 36986 / (36986 + 317169)) < 1e-9
    assert D.buildup(1.2, 5e5) == "long build-up" and D.buildup(-1.0, 5e5) == "short build-up" and D.buildup(-1, -1) == "long unwinding"


class Fetch:
    def __init__(self):
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        if "20261022" in url or "22102026" in url:
            raise FileNotFoundError(url)
        if url.endswith(".zip"):
            return (FX / "fo_bhav_20261023.zip").read_bytes()
        return (FX / "participant_oi_23102026.csv").read_bytes()


def test_fetch_days_resumable(tmp_path, monkeypatch):
    for k, v in (("DIR", tmp_path), ("DAILY", tmp_path / "d.parquet"), ("PART", tmp_path / "p.parquet"), ("STATE", tmp_path / "s.json")):
        monkeypatch.setattr(D, k, v)
    from core import filelock
    monkeypatch.setattr(filelock, "LOCKS", tmp_path / "locks")
    f = Fetch()
    days = [pd.Timestamp("2026-10-22"), pd.Timestamp("2026-10-23")]
    D.fetch_days(days, f, log=lambda *a: None)
    assert len(D.daily()) == 3 and len(D.participants()) == 5 and len(f.urls) == 4
    D.fetch_days(days, f, log=lambda *a: None)
    assert len(f.urls) == 4                                                   # nothing requested twice, 404 days included
    assert "2026-10-22" in D.state()["missing"]
