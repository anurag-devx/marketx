"""Search database compaction: merges fragments, prunes versions, keeps the data, keeps one backup."""
import pandas as pd
import pytest
lancedb = pytest.importorskip("lancedb")
from core import library as L, search_compact as SC


def test_compaction_keeps_rows_and_merges(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "DIR", tmp_path)
    monkeypatch.setattr(L, "DB", tmp_path / "lancedb")
    monkeypatch.setattr(SC, "BACKUPS", tmp_path / "backups")
    monkeypatch.setattr(L, "_fts", lambda t: None)
    db = lancedb.connect(str(L.DB))
    t = db.create_table("items", data=pd.DataFrame({"id": ["a"], "text": ["x"], "vector": [[0.1, 0.2]]}))
    for i in range(30):
        t.add(pd.DataFrame({"id": [f"r{i}"], "text": [f"row {i}"], "vector": [[float(i), 1.0]]}))
    before = SC._counts(L.DB / "items.lance")
    r = SC.run(backup=True, log=lambda *_: None)
    after = SC._counts(L.DB / "items.lance")
    assert after["files"] < before["files"] and after["versions"] <= 2
    assert db.open_table("items").count_rows() == 31 and len(list((tmp_path / "backups").glob("lancedb_*"))) == 1
    SC.run(backup=True, log=lambda *_: None)
    assert len(list((tmp_path / "backups").glob("lancedb_*"))) == 1
