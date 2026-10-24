"""Shareholding patterns: the NSE list and XBRL are read as measured on real files (tests/fixtures, trimmed copies of
NSE's TCS filing), the download is resumable, and analyses only see a pattern after it was published."""
import json
from pathlib import Path
import pandas as pd, pytest
from core import shareholding as SH

FX = Path(__file__).parent / "fixtures"


def test_parse_master_and_xbrl():
    m = SH.parse_master("TCS", json.loads((FX / "shp_master_TCS.json").read_text()))
    assert len(m) == 4 and m.period_end.iat[0] == pd.Timestamp("2026-09-30") and m.published.iat[0] == pd.Timestamp("2026-10-19 18:37:52")
    assert m.promoter_pct.iat[0] == 71.77 and m.xbrl.iat[0].endswith(".xml")
    x = SH.parse_xbrl((FX / "shp_TCS.xml").read_bytes())
    assert x["promoter_pct"] == 71.77 and x["fii_pct"] == 9.01 and x["dii_pct"] == 13.57 and x["mf_pct"] == 5.65
    assert x["shares_total"] == 3618087518 and x["holders"] == 2579099
    with pytest.raises(ValueError):
        SH.parse_master("TCS", {"unexpected": 1})
    with pytest.raises(ValueError):
        SH.parse_xbrl(b"<html>blocked</html>")


class Sess:
    def __init__(self):
        self.calls = 0

    def get_json(self, url):
        self.calls += 1
        return json.loads((FX / "shp_master_TCS.json").read_text())


class Fetch:
    def __init__(self):
        self.calls = 0

    def get(self, url):
        self.calls += 1
        return (FX / "shp_TCS.xml").read_bytes()


def test_update_is_resumable_and_point_in_time(tmp_path, monkeypatch):
    monkeypatch.setattr(SH, "DIR", tmp_path)
    monkeypatch.setattr(SH, "FILE", tmp_path / "h.parquet")
    monkeypatch.setattr(SH, "STATE", tmp_path / "s.json")
    from core import filelock
    monkeypatch.setattr(filelock, "LOCKS", tmp_path / "locks")
    s, f = Sess(), Fetch()
    SH.update(["TCS"], quarters=2, log=lambda *a: None, session=s, fetch=f, workers=1)
    d = SH.load()
    assert len(d) == 4 and d.detail.sum() == 2 and s.calls == 1 and f.calls == 2
    SH.update(["TCS"], quarters=2, log=lambda *a: None, session=s, fetch=f, workers=1)
    assert s.calls == 1 and f.calls == 2                                     # nothing fetched twice
    before = SH.as_of(SH.load(), "TCS", pd.Timestamp("2026-10-19 15:30"))    # Sep-26 pattern published 18:37 that day
    assert before.period_end.max() == pd.Timestamp("2026-06-30")
    after = SH.as_of(SH.load(), "TCS", pd.Timestamp("2026-10-20 15:30"))
    assert after.period_end.max() == pd.Timestamp("2026-09-30") and SH.changes(after) == {"promoter_pct": 0.0, "fii_pct": 0.0, "dii_pct": 0.0,
                                                                                         "mf_pct": 0.0, "retail_pct": 0.0, "public_pct": 0.0}
