"""Search index over stored headlines and NSE filings (LanceDB table 'items', next to the documents library).
Each item is embedded once with the local embedding model; later runs add only new items.
Article text and your documents are already in the library's 'chunks' table; Search queries both."""
import json, time
import numpy as np, pandas as pd
from . import library as L

TABLE = "items"
STATE = L.DIR / "items_state.json"


def _table(db=None, create_with=None):
    db = db or L._db()
    try:
        names = db.list_tables()
        names = list(getattr(names, "tables", names))
    except Exception:
        names = db.table_names()
    if TABLE in names:
        return db.open_table(TABLE)
    return db.create_table(TABLE, data=create_with) if create_with is not None else None


def _items(news, ann):
    rows = []
    if news is not None and len(news):
        for r in news.itertuples():
            txt = r.title + (f". {r.summary}" if isinstance(r.summary, str) and r.summary and r.summary[:40] not in r.title else "")
            rows.append(dict(id=f"n-{r.id}", kind="news", title=str(r.title)[:300], text=txt[:1200], published=str(r.published)[:19],
                             symbols=r.symbols or "", source=str(r.source or ""), link=str(r.link or ""), category=str(r.event_type or "")))
    if ann is not None and len(ann):
        for r in ann.itertuples():
            txt = r.subject if not r.text or r.text == r.subject else f"{r.subject}: {r.text}"
            rows.append(dict(id=f"a-{r.id}", kind="filing", title=f"{r.symbol}: {r.subject}"[:300], text=f"{r.company} ({r.symbol}). {txt}"[:1200],
                             published=str(r.published)[:19], symbols=r.symbol or "", source="NSE", link=str(r.link or ""), category=str(r.category or "")))
    return pd.DataFrame(rows)


def indexed_ids():
    t = _table()
    if t is None:
        return set()
    try:
        return set(t.to_arrow(columns=["id"]).column("id").to_pylist())
    except TypeError:
        return set(t.to_pandas()["id"])


def build(batch=256, max_items=None, log=print):
    from . import news, announcements
    allit = _items(news.load_store(), announcements.load())
    if allit.empty:
        log("Nothing to index yet.")
        return 0
    have = indexed_ids()
    todo = allit[~allit.id.isin(have)].drop_duplicates("id")
    if max_items:
        todo = todo.head(int(max_items))
    log(f"{len(allit):,} headlines and filings stored, {len(have):,} already indexed, {len(todo):,} to add")
    emb = L.embedder()
    db = L._db()
    done, t0 = 0, time.time()
    for i in range(0, len(todo), batch):
        b = todo.iloc[i:i + batch]
        vecs = emb.get_text_embedding_batch(list(b.text), show_progress=False)
        data = [dict(**{k: v for k, v in r.items()}, vector=np.asarray(v, dtype="float32")) for r, v in zip(b.to_dict("records"), vecs)]
        from .filelock import locked
        with locked("library"):
            t = _table(db)
            if t is None:
                _table(db, create_with=data)
            else:
                t.add(data)
        done += len(b)
        if (i // batch) % 20 == 0:
            rate = done / max(time.time() - t0, 1e-6)
            log(f"  {done:,}/{len(todo):,} indexed ({rate:.0f}/s, about {int((len(todo) - done) / max(rate, 1e-6) / 60)} min left)")
    L._fts(_table(db))
    STATE.write_text(json.dumps(dict(when=str(pd.Timestamp.now().floor("s")), indexed=len(have) + done)))
    log(f"Search index: {done:,} added, {len(have) + done:,} total.")
    return done


def progress():
    try:
        st = json.loads(STATE.read_text())
    except Exception:
        st = {}
    from . import news, announcements
    total = len(news.load_store()) + len(announcements.load())
    return dict(indexed=st.get("indexed", 0), total=total, when=st.get("when"))


def search(query, k=30, kinds=None, since=None, symbol=None):
    """Hybrid search over headlines + filings. Falls back to plain keyword matching if the index isn't built."""
    t = _table() if L.DB.exists() else None
    if t is None:
        return keyword_search(query, k, kinds, since, symbol)
    where = []
    if kinds:
        where.append("kind IN (" + ",".join(f"'{x}'" for x in kinds if x in ("news", "filing")) + ")")
    if since is not None:
        where.append(f"published >= '{pd.Timestamp(since).strftime('%Y-%m-%d')}'")
    if symbol and symbol.replace("&", "").replace("-", "").isalnum():
        where.append(f"symbols LIKE '%{symbol}%'")
    w = " AND ".join(where) or None
    try:
        qv = L.embedder().get_query_embedding(query)
        s = t.search(np.asarray(qv, dtype="float32"), vector_column_name="vector").limit(k * 3)
        vec = (s.where(w, prefilter=True) if w else s).to_pandas()
    except Exception:
        vec = pd.DataFrame()
    try:
        s = t.search(query, query_type="fts").limit(k * 3)
        kw = (s.where(w, prefilter=True) if w else s).to_pandas()
    except Exception:
        kw = pd.DataFrame()
    score = {}
    for df in (vec, kw):
        for rank, cid in enumerate(df.id if len(df) else []):
            score[cid] = score.get(cid, 0) + 1 / (60 + rank)
    both = pd.concat([x for x in (vec, kw) if len(x)]).drop_duplicates("id") if (len(vec) or len(kw)) else pd.DataFrame()
    if both.empty:
        return keyword_search(query, k, kinds, since, symbol)
    both["score"] = both.id.map(score)
    return both.drop(columns=[c for c in ("vector", "_distance", "_score", "_relevance_score") if c in both]).sort_values("score", ascending=False).head(k)


def keyword_search(query, k=30, kinds=None, since=None, symbol=None):
    """No index yet: case-insensitive word matching over the stores (all words must appear)."""
    from . import news, announcements
    it = _items(news.load_store(), announcements.load())
    if it.empty:
        return it
    words = [w for w in query.lower().split() if len(w) > 1]
    m = pd.Series(True, index=it.index)
    low = it.text.str.lower()
    for w in words:
        m &= low.str.contains(w, regex=False)
    if kinds:
        m &= it.kind.isin(kinds)
    if since is not None:
        m &= it.published >= pd.Timestamp(since).strftime("%Y-%m-%d")
    if symbol:
        m &= it.symbols.str.split(",").map(lambda xs: symbol in xs)
    return it[m].sort_values("published", ascending=False).head(k).assign(score=np.nan)


if __name__ == "__main__":
    import sys
    a = dict(x.split("=", 1) for x in sys.argv[1:] if "=" in x)
    build(max_items=a.get("max"))
