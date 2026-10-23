"""Catalyst: given a headline, which stocks were affected when SIMILAR news came out before?

1. Find similar past items (stored headlines and NSE filings) by word overlap (TF-IDF cosine). Company names are
   replaced by a placeholder first, so "Infosys Q2 profit up 10%" matches other results headlines, not other
   Infosys news. Items of the same event type (Results, Order win, ...) get a small bonus.
2. For each past item, take its event day (same rule as the news study) and measure what every stock did that day
   and over the next 5 days, vs the Nifty 50.
3. Average across the similar days: the named stocks' own reaction, their industry peers, every sector, size groups
   and individual stocks. t-stats use one value per day; with few similar days these are anecdotes.
Pure numpy (no scikit-learn)."""
import math, re
from collections import Counter, defaultdict
import numpy as np, pandas as pd
from research.events import event_day

STOP = set("""a an the and or of to in on for at by with from as is are was were be been it its this that these those after before over
under into amid about up down vs via per new says said say will may can could would than more most less into out off amid amp
stock stocks share shares market markets india indian nse bse sensex nifty ltd limited company rs crore cr lakh percent pct today
""".split())
WORD = re.compile(r"[a-z][a-z0-9\-]{2,}")


def _stem(w):
    for suf in ("ings", "ing", "ies", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def tokens(text, tagger=None):
    t = str(text or "")
    if tagger is not None and tagger.name_rx is not None:
        t = tagger.name_rx.sub(" zzcompany ", t)
        if tagger.tick_rx is not None:
            t = tagger.tick_rx.sub(" zzcompany ", t)
    t = t.lower()
    t = re.sub(r"\bq[1-4]\b", " qtr ", t)
    return [_stem(w) for w in WORD.findall(t) if w not in STOP and not w.isdigit()]


class Index:
    """Inverted index over past items. docs: DataFrame with text, published, symbols, kind, event_type, link."""
    def __init__(self, docs, tagger=None):
        self.docs = docs.reset_index(drop=True)
        self.tagger = tagger
        post = defaultdict(list)
        tfs = []
        for i, t in enumerate(self.docs.text):
            c = Counter(tokens(t, tagger))
            tfs.append(c)
            for w in c:
                post[w].append(i)
        n = max(len(self.docs), 1)
        self.idf = {w: math.log((1 + n) / (1 + len(p))) + 1 for w, p in post.items()}
        self.post = {w: np.array(p, dtype=np.int64) for w, p in post.items()}
        self.tf = tfs
        self.norm = np.array([math.sqrt(sum((1 + math.log(v)) ** 2 * self.idf[w] ** 2 for w, v in c.items())) or 1.0 for c in tfs])

    def search(self, text, k=150, min_score=0.15, event_type=None, type_bonus=0.1, exclude_after=None):
        q = Counter(tokens(text, self.tagger))
        q = {w: v for w, v in q.items() if w in self.idf}
        if not q:
            return self.docs.iloc[0:0].assign(score=[])
        qw = {w: (1 + math.log(v)) * self.idf[w] for w, v in q.items()}
        qn = math.sqrt(sum(x * x for x in qw.values()))
        score = np.zeros(len(self.docs))
        for w, x in qw.items():
            ids = self.post[w]
            dw = np.array([(1 + math.log(self.tf[i][w])) * self.idf[w] for i in ids])
            score[ids] += x * dw
        score = score / (self.norm * qn)
        if event_type and event_type != "Other" and "event_type" in self.docs:
            score = score + type_bonus * (self.docs.event_type.to_numpy() == event_type)
        if exclude_after is not None:
            score[(self.docs.published >= pd.Timestamp(exclude_after)).to_numpy()] = 0
        top = np.argsort(-score)[:k]
        top = top[score[top] >= min_score]
        return self.docs.iloc[top].assign(score=score[top])


def build_docs(news, ann):
    parts = []
    if news is not None and len(news):
        n = news[~news.roundup.fillna(False).astype(bool)]
        parts.append(pd.DataFrame(dict(text=n.title, published=n.published, symbols=n.symbols.fillna(""), kind="News",
                                       event_type=n.event_type, link=n.link)))
    if ann is not None and len(ann):
        txt = np.where(ann.text.fillna("").eq(ann.subject) | ann.text.fillna("").eq(""), ann.subject, ann.subject + ": " + ann.text.fillna(""))
        parts.append(pd.DataFrame(dict(text=txt, published=ann.published, symbols=ann.symbol.fillna(""), kind="NSE filing",
                                       event_type=ann.category, link=ann.link)))
    if not parts:
        return pd.DataFrame(columns=["text", "published", "symbols", "kind", "event_type", "link"])
    d = pd.concat(parts, ignore_index=True)
    return d[d.published.notna() & d.text.fillna("").str.len().gt(10)].reset_index(drop=True)


def _t(x):
    x = pd.Series(x, dtype=float).dropna()
    n = len(x)
    if n < 3 or x.std() == 0:
        return float(x.mean()) if n else np.nan, np.nan, n
    return float(x.mean()), float(x.mean() / (x.std() / math.sqrt(n))), n


def impact(similar, P, info, min_days=5):
    """similar: rows from Index.search. P: research.daily_moves.panel. info: company_info.load()."""
    dates = P["dates"]
    ar = P["ret"].sub(P["nret"], axis=0)                    # % vs Nifty
    fwd5 = ar[::-1].rolling(5, min_periods=3).sum()[::-1].shift(-1)  # days 1..5 after
    sim = similar.copy()
    sim["day"] = [event_day(t, dates) for t in sim.published]
    sim = sim[sim.day.notna()]
    out = dict(similar=sim, named=pd.DataFrame(), peers=pd.DataFrame(), sectors=pd.DataFrame(), sizes=pd.DataFrame(), stocks=pd.DataFrame(), days=0)
    if sim.empty:
        return out
    inf = info.set_index("symbol")
    ind = inf.industry.reindex(ar.columns)
    sec = inf.sector.reindex(ar.columns).fillna("Other")
    # named stocks: their own move on their news day
    rows = []
    for r in sim.itertuples():
        for s in [x for x in str(r.symbols).split(",") if x]:
            if s in ar.columns and r.day in ar.index:
                peers = ind.index[(ind == ind.get(s)) & (ind.index != s)]
                rows.append(dict(day=r.day, symbol=s, sector=sec.get(s), headline=r.text[:160], score=r.score,
                                 move_day=ar.at[r.day, s], next_5=fwd5.at[r.day, s],
                                 peers_day=ar.loc[r.day, peers].median() if len(peers) else np.nan,
                                 peers_next_5=fwd5.loc[r.day, peers].median() if len(peers) else np.nan))
    named = pd.DataFrame(rows)
    out["named"] = named
    if len(named):
        pr = []
        for lab, col in (("Named stock, event day", "move_day"), ("Named stock, next 5 days", "next_5"),
                         ("Its industry peers, event day", "peers_day"), ("Its industry peers, next 5 days", "peers_next_5")):
            m, t, n = _t(named.groupby("day")[col].mean())
            ab = named.groupby("day")[col].mean().abs().mean()
            pr.append(dict(who=lab, avg_move=m, avg_size=ab, t=t, days=n))
        out["peers"] = pd.DataFrame(pr)
    # cross-section on the distinct similar days
    days = sorted(set(sim.day) & set(ar.index))
    out["days"] = len(days)
    if len(days) < 1:
        return out
    A, F = ar.loc[days], fwd5.loc[days]
    secs = []
    for s_name, cols in sec.groupby(sec).groups.items():
        cols = list(cols)
        if len(cols) < 3:
            continue
        d0 = A[cols].median(axis=1); d5 = F[cols].median(axis=1)
        m0, t0, n = _t(d0); m5, t5, _ = _t(d5)
        secs.append(dict(sector=s_name, stocks=len(cols), days=n, event_day=m0, t_day=t0, up_days_pct=100 * (d0 > 0).mean(), next_5=m5, t_5=t5))
    out["sectors"] = pd.DataFrame(secs).sort_values("t_day", key=lambda x: -x.abs().fillna(0)) if secs else pd.DataFrame()
    # size groups by average traded value rank on each day
    tv = P["avg_tv"].loc[days]
    rk = tv.rank(axis=1, ascending=False)
    sz = []
    for lab, lo, hi in (("Largest 100 by traded value", 0, 100), ("Next 150", 100, 250), ("The rest", 250, 1e9)):
        msk = (rk > lo) & (rk <= hi)
        d0 = A.where(msk).median(axis=1); d5 = F.where(msk).median(axis=1)
        m0, t0, n = _t(d0); m5, t5, _ = _t(d5)
        sz.append(dict(group=lab, days=n, event_day=m0, t_day=t0, next_5=m5, t_5=t5))
    out["sizes"] = pd.DataFrame(sz)
    if len(days) >= min_days:
        st = pd.DataFrame({"days": A.notna().sum(), "event_day": A.mean(), "sd": A.std(), "up_days_pct": 100 * (A > 0).sum() / A.notna().sum(),
                           "next_5": F.mean()})
        st = st[st.days >= min_days]
        st["t_day"] = st.event_day / (st.sd / np.sqrt(st.days))
        st["sector"] = sec.reindex(st.index)
        st["industry"] = ind.reindex(st.index)
        out["stocks"] = st.drop(columns="sd").reset_index(names="symbol").sort_values("t_day", key=lambda x: -x.abs().fillna(0))
    return out
