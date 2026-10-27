"""API: Market Lab (Move Radar, Stock analysis with the influence map, Scorecard, case explainer). Reads data/marketlab/
built by `python -m marketlab`; the walk-forward Scorecard comes from results/marketlab/score.json (`python -m marketlab test`)."""
import json, re
import numpy as np, pandas as pd
from core import config as C
from web.api import route, UserError
from web import tasks

DIR_MIN = 0.53          # past-years direction hit rate needed before a lean is shown (50% = coin flip)
MOVE_LABEL = {"cc": "close-to-close", "oc": "open-to-close", "gp": "overnight gap"}
WHEN = {"close": "After close", "pre": "Before open", "intra": "During the day"}
NOT_READY = {"pre": "needs overnight global data (Phase 2) and its own test on past years",
             "intra": "needs live 5-minute bars during the session (Phase 3) and its own test"}


def _args(p):
    m, s = p.get("move", "cc"), float(p.get("size", 1))
    if m not in MOVE_LABEL or s not in (0.5, 1.0, 2.0, 3.0):
        raise UserError("unknown move or size")
    return m, s


def _num(x, d=1):
    return None if x is None or not np.isfinite(x) else round(float(x), d)


def node_text(r, g):
    f = lambda k: float(r.get(k, np.nan))
    if g == "Price action":
        pos = "near its 20-day high" if f("f_rng") > 0.3 else "near its 20-day low" if f("f_rng") < -0.3 else "mid-range"
        return f"today {f('raw_r1'):+.1f}%, 5 days {f('raw_r5'):+.1f}%, opening gap {f('raw_gap'):+.1f}%, {pos}"
    if g == "Volume":
        return f"{f('raw_volx'):.1f}× its normal volume; 5-day vs 50-day volume {np.exp(f('f_vtr')):.1f}×"
    if g == "Volatility":
        return f"{f('raw_rv'):.0f}% a year (20-day), daily range {100 * np.exp(f('f_atr')):.1f}%"
    if g == "Market":
        vix = "above" if f("m_vix") > 0.1 else "below" if f("m_vix") < -0.1 else "near"
        return f"Nifty {f('raw_m_r1'):+.1f}% today, {f('m_up'):.0%} of stocks up, India VIX {vix} its 1-year median"
    if g == "Sector":
        return f"{r.get('sector') or 'sector'} {f('raw_s_r1'):+.1f}% today ({100 * f('s_r1'):+.1f} pts vs Nifty), 5 days {100 * f('s_r5'):+.1f} pts vs Nifty"
    if g == "Results":
        when = "after the close" if f("e_res_e") > 0 else "during the session" if f("e_res_s") > 0 else None
        return f"results filed {when}" + (f", surprise {f('e_sue'):+.1f} (SUE)" if abs(f("e_sue")) > 0 else "") if when else "no results filed today"
    if g == "News":
        n = int(round(np.expm1(f("n_cnt"))))
        tone = "positive" if f("n_tone") > 0.2 else "negative" if f("n_tone") < -0.2 else "mixed or neutral"
        return f"{n} headline{'s' if n != 1 else ''} naming the stock, tone {tone}" if n else "no headlines naming the stock"
    if g == "Filings":
        n = int(round(np.expm1(f("a_cnt"))))
        return (f"{n} filing{'s' if n != 1 else ''}" + (", at least one material (order, deal, rating, legal, insider or management)" if f("a_mat") > 0 else "")) if n else "no filings besides routine compliance"
    if g == "Macro":
        return f"Brent {f('x_brent'):+.1f}%, USD-INR {f('x_inr'):+.2f}%, US VIX {f('x_vix'):+.1f}, US 10-year {100 * f('x_y10'):+.0f} bp (last change)"
    return ""


def node_short(r, g):
    """A few words per node for the radar's 'Biggest drivers' column."""
    f = lambda k: float(r.get(k, np.nan))
    return {"Price action": f"{f('raw_r1'):+.1f}% today", "Volume": f"{f('raw_volx'):.1f}× volume", "Volatility": f"{f('raw_rv'):.0f}% vol",
            "Market": f"Nifty {f('raw_m_r1'):+.1f}%", "Sector": f"sector {f('raw_s_r1'):+.1f}%",
            "Results": "results after close" if f("e_res_e") > 0 else "results today" if f("e_res_s") > 0 else "no results",
            "News": f"{int(round(np.expm1(f('n_cnt'))))} headlines", "Filings": f"{int(round(np.expm1(f('a_cnt'))))} filings" + (" (material)" if f("a_mat") > 0 else ""),
            "Macro": f"Brent {f('x_brent'):+.1f}%"}.get(g, "")


def _node(dc, du):
    mag = max(abs(du), abs(dc))
    return dict(dir=1 if du >= 0.03 else -1 if du <= -0.03 else 0, strength=3 if mag >= 0.08 else 2 if mag >= 0.04 else 1)


def _skill(m, s):
    from marketlab import evaluate as V
    sk, gs = V.skill_map()
    return sk.get((m, s)), gs


def _verdict(r, m, s, skill):
    from marketlab import engine as E
    ch, us, up, nb = float(r[f"ch_{m}_{s}"]), float(r[f"us_{m}_{s}"]), float(r[f"up_{m}_{s}"]), int(r[f"nb_{m}_{s}"])
    raw = str(E.lean_of(np.array([up]), np.array([nb]))[0])
    ok = bool(skill and skill.get("skill"))
    dir_ok = bool(skill and (skill.get("dir_hit") or 0) >= DIR_MIN)
    lean = raw if dir_ok else "unclear"                    # a lean is shown only where direction was right often enough on past years
    conf = str(E.confidence(np.array([float(r.dist_pct)]), np.array([nb]), ok)[0])
    return dict(chance=ch, usual=us, lift=ch - us, up_share=up, n_big=nb, lean=lean, lean_raw=raw, direction_tested=dir_ok, confidence=conf, dist_pct=float(r.dist_pct))


NAMES = ("radar", "drivers", "cases", "spread")


def _view_day(p):
    d = str(p.get("day") or "")
    if d and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        raise UserError("day must be YYYY-MM-DD")
    return d or None


def frames(day=None):
    """(day, {radar, drivers, cases, spread}) for the latest radar, or for a past night as it was kept then (the radar
    folder, else the Review ledger). Past nights are read-only views; nothing is recomputed."""
    from marketlab import radar as R, tally as T, review as RV
    latest = R.latest_day()
    if not day or day == latest:
        return latest, {n: R.read(n) for n in NAMES}
    if (R.DIR / "radar" / f"{day}.parquet").exists():
        return day, {n: R.read(n, day) for n in NAMES}
    k = T.pick_kind(day)
    if not k:
        raise UserError(f"Nothing kept for {day}.")
    dd = RV._dir(k, day)
    return day, {n: (pd.read_parquet(dd / f"{n}.parquet") if (dd / f"{n}.parquet").exists() else R.read(n, day)) for n in NAMES}


@route("GET", "marketlab/days")
def ml_days(_):
    """Every night with a saved radar (newest first): the radar folder and the Review ledger (live and replayed)."""
    from marketlab import radar as R, tally as T
    ds = {p.stem: "radar" for p in (R.DIR / "radar").glob("*.parquet")} if (R.DIR / "radar").exists() else {}
    for x in T.days():
        ds.setdefault(x["day"], x["kind"])
    return dict(latest=R.latest_day(), items=[dict(day=d, kind=k) for d, k in sorted(ds.items(), reverse=True)])


@route("GET", "marketlab/radar")
def radar(p):
    from marketlab import radar as R
    when = p.get("when", "close")
    m, s = _args(p)
    vd = _view_day(p)
    day = R.latest_day() if not vd else vd
    base = dict(day=day, when=when, move=m, size=s, whens=WHEN, moves=MOVE_LABEL)
    if when != "close":
        return dict(base, ready=False, why=f"{WHEN.get(when, when)} isn't ready yet: it {NOT_READY.get(when, 'is not built')}.")
    if not day:
        return dict(base, ready=False, why="No radar yet. Run Fetch + run all (it builds the radar at the end), or Build tonight's radar below.")
    day, F = frames(vd)
    rd, dv = F["radar"], F["drivers"]
    skill, gs = _skill(m, s)
    dv = dv[(dv.move == m) & (dv["size"] == s)]
    rows = []
    for r in rd.to_dict("records"):
        v = _verdict(pd.Series(r), m, s, skill)
        d = dv[dv.symbol == r["symbol"]]
        nodes = [dict(node=x.node, **_node(x.d_chance, x.d_up)) for x in d.itertuples()]
        top = d.assign(w=d.d_chance.abs() + d.d_up.abs()).sort_values("w", ascending=False).head(3)
        why = " · ".join(f"{'▲' if x.d_up >= 0.03 else '▼' if x.d_up <= -0.03 else '•'} {x.node} ({node_short(r, x.node)})" for x in top.itertuples() if x.w >= 0.02)
        rows.append(dict(symbol=r["symbol"], sector=r.get("sector", ""), close=r.get("close"), **v, nodes=nodes, why=why))
    rows.sort(key=lambda x: -x["lift"])
    status = ("no skill: the walk-forward test didn't beat the usual rate for this combination" if skill and not skill.get("skill") else
              "tested" if skill else "not tested yet: run the Scorecard test (Scorecard tab)")
    dp = float(rd.day_pct.median()) if "day_pct" in rd else None
    day_note = ("Unusual market day: few past days had market / macro moves like these, so the matches lean on each stock's own situation."
                if dp is not None and dp >= 0.9 else None)
    return dict(base, ready=True, day=day, past=bool(vd) and vd != R.latest_day(), rows=rows, status=status, skill=skill, day_pct=dp, day_note=day_note, sectors=sorted(set(rd.sector.dropna()) - {""}))


def _evidence(sym, day):
    """Today's headlines and filings for the stock (same window as the scenario: after 21:00 yesterday, up to 21:00 today)."""
    from marketlab.features import decision_day
    out = dict(news=[], filings=[])
    days = None
    try:
        px = pd.read_parquet(C.DATA / "prices.parquet", columns=["date"])
        days = np.sort(pd.to_datetime(px.date.unique()))
    except Exception:
        return out
    day = pd.Timestamp(day)
    try:
        n = pd.read_parquet(C.DATA / "news" / "news.parquet", columns=["published", "title", "source", "link", "symbols", "tone", "event_type"])
        n = n[n.symbols.astype(str).str.contains(rf"(?:^|,)\s*{sym}\s*(?:,|$)", regex=True)]
        n = n[decision_day(pd.to_datetime(n.published), days) == np.datetime64(day)]
        out["news"] = n.sort_values("published", ascending=False).head(12).assign(published=lambda x: x.published.astype(str)).to_dict("records")
    except Exception:
        pass
    try:
        a = pd.read_parquet(C.DATA / "announcements.parquet", columns=["published", "symbol", "subject", "category", "link"])
        a = a[a.symbol == sym]
        a = a[decision_day(pd.to_datetime(a.published), days) == np.datetime64(day)]
        out["filings"] = a.sort_values("published", ascending=False).head(12).assign(published=lambda x: x.published.astype(str)).to_dict("records")
    except Exception:
        pass
    return out


@route("GET", "marketlab/stock")
def stock(p):
    from marketlab import radar as R
    m, s = _args(p)
    sym = str(p.get("symbol", "")).upper().strip()
    vd = _view_day(p)
    if not R.latest_day():
        raise UserError("No radar yet: build tonight's radar first.")
    day, F = frames(vd)
    rd = F["radar"]
    if rd[rd.symbol == sym].empty:
        raise UserError(f"{sym} isn't in the radar of {day} (not in the Nifty 500, or no price that day).")
    out = stock_view(F, sym, m, s)
    return dict(out, day=day, past=bool(vd) and vd != R.latest_day(), evidence=_evidence(sym, day))


def stock_view(F, sym, m, s):
    """Verdict, influence-map nodes, closest cases and outcome spread for one stock from Market Lab frames
    (radar / drivers / cases / spread). Used by Market Lab → Stock analysis and the Stock Desk."""
    from marketlab.features import GROUPS, LABEL
    rd = F["radar"]
    r = rd[rd.symbol == sym].iloc[0]
    skill, gs = _skill(m, s)
    v = _verdict(r, m, s, skill)
    dv = F["drivers"]
    dv = dv[(dv.symbol == sym) & (dv.move == m) & (dv["size"] == s)] if len(dv) else dv
    nodes = []
    for x in dv.itertuples():
        g = gs.get(x.node) or {}
        nodes.append(dict(node=x.node, d_chance=float(x.d_chance), d_up=float(x.d_up), tested=bool(g.get("tested")), test=g or None,
                          text=node_text(r, x.node), values=[dict(label=LABEL[f], value=_num(r[f], 2)) for f in GROUPS[x.node]], **_node(x.d_chance, x.d_up)))
    cs = F["cases"]
    cs = cs[cs.symbol == sym].sort_values("rank") if len(cs) else cs
    cases = [dict(rank=int(c.rank), symbol=c.case_symbol, date=str(pd.Timestamp(c.case_date).date()), move=_num(100 * getattr(c, m), 2),
                  cc=_num(100 * c.cc, 2), oc=_num(100 * c.oc, 2), gp=_num(100 * c.gp, 2), distance=_num(c.distance, 2)) for c in cs.itertuples()]
    sp = F["spread"]
    sp = sp[(sp.symbol == sym) & (sp.move == m)] if len(sp) else sp
    spread = sp.iloc[0].drop(["symbol", "move"]).astype(float).round(2).to_dict() if len(sp) else None
    return dict(symbol=sym, sector=r.get("sector"), close=float(r.close), move=m, size=s, move_label=MOVE_LABEL[m], verdict=v, nodes=nodes,
                cases=cases, spread=spread, skill=skill, status="tested" if skill and skill.get("skill") else "no skill yet" if skill else "not tested yet")


def _case_items(cases, news, ann, days):
    from marketlab.features import decision_day
    out = []
    for c in cases[:10]:
        d = np.datetime64(pd.Timestamp(c["date"]))
        t = []
        if news is not None:
            n = news[news.symbols.astype(str).str.contains(rf"(?:^|,)\s*{c['symbol']}\s*(?:,|$)", regex=True)]
            n = n[decision_day(pd.to_datetime(n.published), days) == d]
            t += [f"news: {x}" for x in n.title.astype(str).head(3)]
        if ann is not None:
            a = ann[ann.symbol == c["symbol"]]
            a = a[decision_day(pd.to_datetime(a.published), days) == d]
            t += [f"filing ({x.category}): {str(x.subject)[:90]}" for x in a.head(3).itertuples()]
        out.append(dict(c, items=t))
    return out


def explain_job(sym, m, s, log, progress):
    """Case explainer + skeptic. Uses the debate model when available; otherwise a template from the same facts."""
    d = stock(dict(symbol=sym, move=m, size=s))
    progress(0.2, "Reading what happened around the past cases ...")
    days = np.sort(pd.to_datetime(pd.read_parquet(C.DATA / "prices.parquet", columns=["date"]).date.unique()))
    try:
        news = pd.read_parquet(C.DATA / "news" / "news.parquet", columns=["published", "title", "symbols"])
    except Exception:
        news = None
    try:
        ann = pd.read_parquet(C.DATA / "announcements.parquet", columns=["published", "symbol", "subject", "category"])
    except Exception:
        ann = None
    cases = _case_items(d["cases"], news, ann, days)
    v = d["verdict"]
    strong = [n for n in d["nodes"] if n["strength"] >= 2]
    greens, reds = [n["node"] for n in strong if n["dir"] > 0], [n["node"] for n in strong if n["dir"] < 0]
    with_items = [c for c in cases if c["items"]]
    skeptic = []
    if v["n_big"] < 30:
        skeptic.append(f"only {v['n_big']} of the past cases had a move that big: thin evidence")
    if v["dist_pct"] >= 0.8:
        skeptic.append("today's situation is unusual: the closest past cases are further away than normal")
    if greens and reds:
        skeptic.append(f"drivers disagree: {', '.join(greens)} push up while {', '.join(reds)} push down")
    if not d["skill"] or not d["skill"].get("skill"):
        skeptic.append("this combination hasn't shown tested skill on past years (Scorecard)")
    untested = [n["node"] for n in d["nodes"] if not n["tested"] and n["strength"] >= 2]
    if untested:
        skeptic.append(f"{', '.join(untested)} {'is' if len(untested) == 1 else 'are'} strong today but not tested on history")
    lean_word = {"up": "lean up", "down": "lean down"}.get(v["lean"], "have no clear direction")
    template = (f"{sym}: {100 * v['chance']:.0f}% chance of a {d['move_label']} move of at least {s:g}% (usual {100 * v['usual']:.0f}%); the closest cases {lean_word}.\n\n"
                f"Past cases: {len(with_items)} of the 10 closest had news or filings that day"
                + (f" (e.g. {with_items[0]['symbol']} {with_items[0]['date']}: {with_items[0]['items'][0]})" if with_items else "")
                + ". Today: " + (f"{len(d['evidence']['news'])} headlines, {len(d['evidence']['filings'])} filings." if d["evidence"]["news"] or d["evidence"]["filings"] else "no stock-specific news or filings.")
                + "\n\nWhat could make this wrong:\n" + "\n".join(f"- {x}" for x in skeptic or ["nothing obvious from the data"]))
    text, writer = template, "template"
    try:
        from core import llm
        if llm.status("debate")[0]:
            progress(0.5, "Asking the model to explain the cases ...")
            facts = dict(stock=sym, move=d["move_label"], size_pct=s, chance_pct=round(100 * v["chance"]), usual_pct=round(100 * v["usual"]), lean=v["lean"],
                         drivers=[dict(node=n["node"], push="up" if n["dir"] > 0 else "down" if n["dir"] < 0 else "neutral", strength=n["strength"], today=n["text"], tested=n["tested"]) for n in d["nodes"]],
                         today_news=[x["title"] for x in d["evidence"]["news"][:6]], today_filings=[f"{x['category']}: {x['subject']}" for x in d["evidence"]["filings"][:6]],
                         past_cases=[dict(stock=c["symbol"], date=c["date"], next_move_pct=c["move"], what_happened_that_day=c["items"]) for c in cases], skeptic_points=skeptic)
            sysmsg = ("You explain a stock-move forecast to an investor in plain English. Use ONLY the facts given; do not add numbers that are not in them. "
                      "Write three short sections with these headings: 'What drove the past cases' (what kind of catalysts the closest past cases had and how they moved), "
                      "'Is today the same kind?' (compare today's news/filings and drivers with the past cases), 'What could make this wrong' (use the skeptic points). "
                      "Under 220 words. No advice to buy or sell.")
            out = llm.chat("debate", [{"role": "user", "content": json.dumps(facts, default=str)}], system=sysmsg, max_tokens=700, timeout=240)
            nums = set(__import__("re").findall(r"\d+(?:\.\d+)?", out))
            allowed = set(__import__("re").findall(r"\d+(?:\.\d+)?", json.dumps(facts, default=str))) | {str(i) for i in range(0, 11)}
            if nums - allowed:
                log("model used numbers that aren't in the facts: using the template instead")
            elif out.strip():
                text, writer = out.strip(), "model"
    except Exception as e:                         # model off or failed: the template stands
        log(f"model not used ({type(e).__name__})")
    progress(1.0, "Done")
    return dict(text=text, writer=writer, cases=cases)


@route("POST", "marketlab/explain")
def explain(_, body):
    m, s = _args(body or {})
    sym = str((body or {}).get("symbol", "")).upper().strip()
    try:
        return dict(task=tasks.start("marketlab-explain", lambda log, prog: explain_job(sym, m, s, log, prog)))
    except RuntimeError as e:
        raise UserError(str(e))


@route("GET", "marketlab/score")
def score(_):
    from marketlab import evaluate as V
    r = V.load()
    if not r or not r.get("summary"):
        return dict(sections=[dict(kind="note", tone="warn", text="Not tested yet. Click 'Run the Scorecard test' (about 20–40 minutes); it tests every combination on past years it never saw.")])
    sm = pd.DataFrame(r["summary"])
    sm["combo"] = "After close · " + sm.move.map(MOVE_LABEL) + " · ≥" + sm["size"].map(lambda x: f"{x:g}") + "%"
    sm["verdict"] = np.where(sm.skill, "tested skill", "no skill")
    for c in ("top20_hit", "top20_usual", "base_rate", "dir_hit", "dir_cov"):
        sm[c] = 100 * sm[c]
    sm["lift"] = 100 * sm.lift
    best = sm[(sm.move == "cc") & (sm["size"] == 1.0)]
    secs = []
    if len(best):
        b = best.iloc[0]
        secs.append(dict(kind="kpis", items=[dict(label="Top-20 moved ≥1% (close-to-close)", value=f"{b.top20_hit:.0f}%", delta=f"their usual: {b.top20_usual:.0f}%"),
                                             dict(label="Edge (lift)", value=f"{b.lift:+.1f} pts", delta=f"positive in {int(b.lift_years_pos)} of {int(b.years)} years"),
                                             dict(label="Direction right (when a lean is given)", value=f"{b.dir_hit:.0f}%" if pd.notna(b.dir_hit) else "—", delta=f"lean given {b.dir_cov:.0f}% of the time"),
                                             dict(label="Tested on", value=f"{r['years'][0]}–{r['years'][-1]}", delta="years it never saw")]))
    secs.append(dict(kind="table", title="Every combination (After close)", rows=sm[["combo", "verdict", "top20_hit", "top20_usual", "lift", "lift_years_pos", "years", "auc", "brier_skill", "dir_hit", "dir_cov", "n"]].round(3),
                     columns=[["combo", "Combination"], ["verdict", "Verdict", "status"], ["top20_hit", "Top-20 hit %", "num1"], ["top20_usual", "Their usual %", "num1"], ["lift", "Edge (pts)", "snum1"],
                              ["lift_years_pos", "Years with edge", "int"], ["years", "Years", "int"], ["auc", "AUC", "num3"], ["brier_skill", "Skill vs usual", "snum2"], ["dir_hit", "Direction right %", "num1"],
                              ["dir_cov", "Lean given %", "num0"], ["n", "Test rows", "int"]], tools=False,
                     note="Top-20 hit: share of each test day's 20 highest-chance stocks that moved at least that much. Their usual: what those stocks normally do. "
                          "Skill vs usual above 0 means the chance beats simply using each stock's usual rate. Direction right: 50% is a coin flip."))
    by = pd.DataFrame(r["by_year"])
    b1 = by[(by.move == "cc") & (by["size"] == 1.0)]
    if len(b1):
        secs.append(dict(kind="chart", type="bars", title="Edge by year: top-20 hit minus their usual (pts), close-to-close ≥1%", signed=True, decimals=1, height=220,
                         x=b1.year.astype(str).tolist(), series=[dict(name="edge", y=(100 * b1.lift).round(1).tolist())]))
    if r.get("group_summary"):
        g = pd.DataFrame(r["group_summary"])
        g["verdict"] = np.where(g.tested, "tested: helps", "not shown to help")
        secs.append(dict(kind="table", title="Does each node earn its place? (drop it and re-test)", tools=False,
                         rows=g[["node", "verdict", "d_brier", "brier_pos", "d_dir", "dir_pos", "years"]].round(4),
                         columns=[["node", "Node"], ["verdict", "Verdict", "status"], ["d_brier", "Chance skill lost without it", "snum2"], ["brier_pos", "Years it helped (chance)", "int"],
                                  ["d_dir", "Direction lost without it", "snum2"], ["dir_pos", "Years it helped (direction)", "int"], ["years", "Years", "int"]],
                         note="Solid lines on the influence map = nodes marked 'tested: helps' here. Dashed = not shown to help yet."))
    secs.append(dict(kind="note", text=f"Tested {r['built']}. Before open and During the day are Phase 2 and 3."))
    return dict(sections=secs)


@route("GET", "marketlab/candidates")
def candidates(_):
    """Candidate signal groups (delivery, valuation): test results and whether each is used in the forecasts."""
    from marketlab import evaluate as V
    from marketlab.features import CANDIDATES, LABEL, active_candidates
    r = V.load_candidates() or {}
    act = active_candidates()
    out = []
    for g, fs in CANDIDATES.items():
        rows = [x for x in r.get("summary", []) if x["node"] == g]
        out.append(dict(node=g, features=[LABEL.get(f, f) for f in fs], active=g in act, tested=bool(rows),
                        helps=any(x["helps"] and x.get("years", 0) >= V.MIN_CAND_YEARS for x in rows), min_years=V.MIN_CAND_YEARS,
                        results=rows, skipped=g in (r.get("skipped") or [])))
    return dict(built=r.get("built"), items=out)


@route("POST", "marketlab/candidates")
def candidates_set(_, body):
    """Switch a candidate group on / off for the forecasts (owner). Only a group the test showed to help can be switched
    on. Takes effect at the next radar build; re-run the Scorecard test afterwards."""
    import json as _json
    from core import atomic
    from marketlab.features import CANDIDATES, ACTIVE_FILE, active_candidates
    from marketlab import evaluate as V
    b = body or {}
    g = str(b.get("node") or "")
    if g not in CANDIDATES:
        raise UserError("unknown candidate")
    act = set(active_candidates())
    if b.get("on"):
        r = V.load_candidates() or {}
        if not any(x["node"] == g and x["helps"] and x.get("years", 0) >= V.MIN_CAND_YEARS for x in r.get("summary", [])):
            raise UserError(f"{g} hasn't been shown to help in at least {V.MIN_CAND_YEARS} test years yet (download more history, then re-run the candidate test)")
        act.add(g)
    else:
        act.discard(g)
    ACTIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(ACTIVE_FILE, _json.dumps(sorted(act)))
    return dict(active=sorted(act), note="Used from the next radar build (restart the app so every page uses it).")


@route("GET", "marketlab/asof")
def asof(_):
    """Which day the radar was built from, the session it is for, and whether newer prices exist (radar out of date)."""
    from marketlab import radar as R
    from web.api import prices
    day = R.latest_day()
    px = prices()
    last = None if px is None else str(pd.Timestamp(px.date.max()).date())
    nxt = None
    if day:
        n = pd.Timestamp(day) + pd.Timedelta(days=1)
        while n.weekday() >= 5:
            n += pd.Timedelta(days=1)
        nxt = str(n.date())
    return dict(day=day, for_day=nxt, prices_last=last, stale=bool(day and last and last > day))
