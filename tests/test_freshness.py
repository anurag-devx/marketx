import json
import pandas as pd
from core import freshness as F, config as C, jobs


def test_stale_from_prices_when_no_report(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA", tmp_path)
    monkeypatch.setattr(F, "FILE", tmp_path / "stale_symbols.json")
    d = pd.bdate_range("2026-10-01", periods=5)
    px = pd.concat([pd.DataFrame(dict(date=d, symbol="AAA")), pd.DataFrame(dict(date=d[:-1], symbol="BBB"))])
    px.to_parquet(tmp_path / "prices.parquet")
    pd.DataFrame({"Symbol": ["AAA", "BBB", "CCC"]}).to_csv(tmp_path / "universe.csv", index=False)
    s = F.stale()
    assert s["day"] == str(d[-1].date()) and [r["symbol"] for r in s["stocks"]] == ["BBB", "CCC"]
    (tmp_path / "stale_symbols.json").write_text(json.dumps(dict(day=str(d[-1].date()), stocks=[dict(symbol="BBB", last_bar="x", reason="download failed: throttled")])))
    assert F.stale()["stocks"][0]["reason"].startswith("download failed")      # the fetch's own reasons win when current


def test_jobs_prefer_project_venv(tmp_path, monkeypatch):
    v = tmp_path / ".venv" / "bin"
    v.mkdir(parents=True)
    (v / "python").write_text("")
    monkeypatch.setattr(jobs, "BASE", tmp_path)
    assert jobs._python() == str(v / "python")
