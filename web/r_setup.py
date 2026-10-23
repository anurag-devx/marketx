"""API: Settings (approvals, parameters, schedule and alerts, news feeds, backups, AI models) and Data health."""
import importlib, json
import pandas as pd
from core import config as C, jobs
from web.api import route, prices, universe, q, UserError
from web.r_agents import table

STATE_LABEL = {"up_calm": "Uptrend, calm", "up_volatile": "Uptrend, volatile", "down_calm": "Downtrend, calm", "down_volatile": "Downtrend, volatile"}


def _reload():
    importlib.reload(C)


# ---------------- Settings ----------------
@route("GET", "settings")
def settings(p):
    tab = p.get("tab", "approvals")
    from core import settings as S
    if tab == "approvals":
        pend = q("select * from proposals where status = 'pending' order by created")
        pend = [dict(id=r.id, title=r.title, agent=r.agent, created=r.created, rationale=r.rationale, evidence=r.evidence) for r in pend.itertuples()] if len(pend) else []
        done = q("select created, title, status, decided_at from proposals where status <> 'pending' order by decided_at desc")
        reg = q("select strategy, name, family, status, since, note from strategies order by family, strategy")
        return dict(pending=pend, decided=table("Decided proposals", done, {"created": "dt", "decided_at": "dt", "status": "status"}, empty="None yet."),
                    registry=table("Strategy library", reg, {"status": "status", "note": "long"}, empty="Empty: run the strategy agent."),
                    strategies=list(reg.strategy) if len(reg) else [])
    if tab == "parameters":
        cur = S.current()
        return dict(current=dict(RISK=cur["RISK"], STATE_MULT=cur["STATE_MULT"], COST_RT=cur["COST_RT"], MIN_TURNOVER_CR=cur["MIN_TURNOVER_CR"],
                                 MIN_PRICE=cur["MIN_PRICE"], MIN_TRADES_STATE=cur["MIN_TRADES_STATE"], MIN_T_STAT=cur["MIN_T_STAT"]),
                    version=C.VERSION, rev=C.SETTINGS_REV, states=STATE_LABEL, history=table("Change history", S.history(), {"changed": "long"}, empty="No changes yet."))
    if tab == "schedule":
        from core import schedule, secrets
        s = schedule.status() if schedule.supported() else None
        log = schedule.LOG
        cur = S.current()["ALERTS"]
        return dict(supported=schedule.supported(), schedule=s, log="\n".join(log.read_text(errors="ignore").splitlines()[-60:]) if log.exists() else None,
                    alerts=dict(macos=bool(cur.get("macos", True)), telegram=bool(cur.get("telegram", False))),
                    telegram_keys=bool(secrets.has("TELEGRAM_BOT_TOKEN") and secrets.has("TELEGRAM_CHAT_ID")))
    if tab == "feeds":
        d = pd.DataFrame(S.current()["NEWS_FEEDS"])
        return dict(rows=d.fillna("").astype(str).to_dict("records"), columns=list(d.columns) or ["name", "url", "kind"])
    if tab == "backups":
        from core import backup
        return dict(backups=backup.list_backups())
    if tab == "ai":
        return _ai()
    raise UserError("unknown tab")


@route("POST", "settings/proposal")
def settings_proposal(_, body):
    from agents import strategy as SA
    SA.decide(int(body["id"]) if str(body.get("id", "")).isdigit() else body.get("id"), bool(body.get("approve")))
    return dict(ok=True)


@route("POST", "settings/strategy-status")
def settings_strategy_status(_, body):
    from agents import strategy as SA
    if body.get("status") not in ("paused", "active", "paper", "retired"):
        raise UserError("unknown status")
    SA.set_status(body["strategy"], body["status"], "set by you in the dashboard")
    return dict(ok=True)


@route("POST", "settings/parameters")
def settings_parameters(_, body):
    from core import settings as S
    if body.get("reset"):
        S.reset(); _reload()
        return dict(message="Reset to defaults.")
    try:
        R, f = body["RISK"], float
        ch = dict(RISK=dict(risk_per_trade=f(R["risk_per_trade"]) / 100, max_positions=int(R["max_positions"]), max_position_pct=f(R["max_position_pct"]) / 100,
                            max_per_sector=int(R["max_per_sector"]), daily_loss_cap=f(R["daily_loss_cap"]) / 100, dd_half=f(R["dd_half"]) / 100, dd_stop=f(R["dd_stop"]) / 100),
                  STATE_MULT={k: f(body["STATE_MULT"][k]) for k in STATE_LABEL} | {k: v for k, v in S.current()["STATE_MULT"].items() if k not in STATE_LABEL},
                  COST_RT=f(body["COST_RT"]) / 100, MIN_TURNOVER_CR=f(body["MIN_TURNOVER_CR"]), MIN_PRICE=f(body["MIN_PRICE"]),
                  MIN_TRADES_STATE=int(body["MIN_TRADES_STATE"]), MIN_T_STAT=f(body["MIN_T_STAT"]))
    except (KeyError, TypeError, ValueError) as e:
        raise UserError(f"check the numbers ({e})")
    rev = S.save(ch, str(body.get("note") or ""))
    if rev == C.SETTINGS_REV:
        return dict(message="Nothing changed.")
    _reload()
    return dict(message=f"Saved as revision {rev}. The next agent run uses it.")


@route("POST", "settings/schedule")
def settings_schedule(_, body):
    from core import schedule
    try:
        if body.get("remove"):
            schedule.remove()
        else:
            schedule.install(int(body["hour"]), int(body["minute"]))
    except Exception as e:
        raise UserError(str(e))
    return dict(ok=True)


@route("POST", "settings/alerts")
def settings_alerts(_, body):
    from core import settings as S, notify
    if body.get("test"):
        sent = notify.send("MarketX", "Test alert from the dashboard")
        return dict(message="Sent via: " + (", ".join(sent) or "nothing (check the toggles and keys)"))
    S.save(dict(ALERTS=dict(macos=bool(body.get("macos")), telegram=bool(body.get("telegram")))), "alerts changed"); _reload()
    return dict(message="Saved.")


@route("POST", "settings/feeds")
def settings_feeds(_, body):
    from core import settings as S
    rows = [r for r in (body.get("rows") or []) if str(r.get("url", "")).startswith("http")]
    S.save(dict(NEWS_FEEDS=rows), "news feeds changed"); _reload()
    return dict(message=f"Saved {len(rows)} feeds.")


@route("POST", "settings/backup")
def settings_backup(_, body):
    from core import backup
    if body.get("restore"):
        if jobs.current() is not None:
            raise UserError("Wait for the running job to finish first.")
        try:
            safety = backup.restore(body["restore"])
        except Exception as e:
            raise UserError(str(e))
        from web import api
        api._cache.clear()
        return dict(message=f"Restored {body['restore']}. What was there before is saved as backups/{safety}.")
    return dict(message=f"Saved backups/{backup.backup('manual')}")


PACKAGES = {"llama_index.core": "LlamaIndex", "llama_index.embeddings.ollama": "LlamaIndex Ollama embeddings", "lancedb": "LanceDB",
            "trafilatura": "trafilatura", "pypdf": "pypdf", "docx": "python-docx", "xlrd": "xlrd (geopolitical risk index)"}


def _missing():
    import importlib.util
    def has(m):
        try:
            return importlib.util.find_spec(m) is not None
        except (ImportError, ValueError):
            return False
    return [l for m, l in PACKAGES.items() if not has(m)]


def _ai():
    from core import llm, secrets
    s = llm.settings()
    try:
        models = llm.ollama_models(s)
    except llm.ModelError:
        models = None
    feats = []
    for f, label in llm.FEATURES.items():
        ok, msg = llm.status(f)
        feats.append(dict(key=f, label=label, provider=s["features"][f]["provider"], model=s["features"][f]["model"], ok=ok, status=msg))
    return dict(ollama=models is not None, models=models or [], online=s["online_enabled"], online_models=s["online_models"],
                providers=llm.PROVIDERS, keys={p: secrets.has(llm.KEY_NAME[p]) for p in ("anthropic", "openai", "gemini")}, key_names=llm.KEY_NAME,
                features=feats, embed_model=s["embed_model"], missing=_missing(), packages=list(PACKAGES.values()))


@route("POST", "settings/ai")
def settings_ai(_, body):
    from core import llm, settings as S
    s = llm.settings()
    online = bool(body.get("online"))
    om = {p: str((body.get("online_models") or {}).get(p, s["online_models"].get(p, ""))).strip() for p in ("anthropic", "openai", "gemini")}
    feats = {}
    for f in llm.FEATURES:
        x = (body.get("features") or {}).get(f) or s["features"][f]
        prov = x.get("provider") if x.get("provider") in llm.PROVIDERS else "ollama"
        if prov != "ollama" and not online:
            prov = "ollama"
        feats[f] = {"provider": prov, "model": str(x.get("model") or s["features"][f]["model"]).strip() if prov == "ollama" else ""}
    S.save({"AI": {"ollama_url": s["ollama_url"], "embed_model": str(body.get("embed_model") or s["embed_model"]).strip(), "online_enabled": online,
                   "online_models": om, "features": feats, "temperature": s["temperature"], "timeout": s["timeout"]}}, "AI models")
    _reload()
    return dict(message="Saved.")


# ---------------- Data health ----------------
def _json(p):
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


@route("GET", "data")
def data(p):
    tab = p.get("tab", "prices")
    from core.data import has_broad
    px = prices()
    if tab == "prices":
        if px is None:
            return dict(sections=[dict(kind="note", tone="warn", text="No price data yet. Press Update prices.")], has_broad=has_broad())
        last = px.date.max()
        age = (pd.Timestamp.now().normalize() - last).days
        log = _json(C.DATA / "fetch_log.json") or None
        secs = [dict(kind="kpis", items=[dict(label="Last bar", value=str(last.date()), delta=f"{age} day(s) old", tone="down" if age > 4 else ""),
                                         dict(label="Nifty 500 stocks", value=str(px.symbol.nunique())), dict(label="History from", value=str(px.date.min().date())),
                                         dict(label="Other NSE stocks", value="downloaded" if has_broad() else "not downloaded")])]
        if age > 4:
            secs.append(dict(kind="note", tone="warn", text="Data is stale: the agents won't recommend new trades until it is refreshed."))
        if log:
            n5 = log.get("nifty500", {})
            secs.append(dict(kind="note", text=f"Last download {log['when'][:16]} ({log['mode']}): {n5.get('stocks', '?')} stocks, {n5.get('new', 0)} new, "
                             f"{n5.get('refetched', 0)} re-downloaded after a split/bonus, {len(n5.get('missing', []))} missing, {n5.get('seconds', 0) // 60} min."))
        lastd = px.groupby("symbol").date.max()
        secs.append(table("Stocks missing the latest bar", lastd[lastd < last].rename("last_bar").reset_index(), {"symbol": "symonly", "last_bar": "date"}, tools=False, empty="All stocks are up to date."))
        return dict(sections=secs, has_broad=has_broad())
    if tab == "history":
        return _history()
    if tab == "news":
        return _news_section()
    if tab == "quality":
        from core import quality
        qs = quality.summary()
        iss = pd.read_csv(quality.ISSUES) if quality.ISSUES.exists() else pd.DataFrame()
        label = {"split_gap": "Split/bonus not adjusted", "bad_tick": "One wrong bar", "ohlc": "Impossible bar", "ohlc_minor": "Bar slightly off",
                 "big_move": "Big one-day move", "missing_days": "Missing days", "long_gap": "Long gap", "stale": "No recent bar", "duplicates": "Duplicate dates"}
        if len(iss):
            iss = iss.assign(what=iss.check.map(label).fillna(iss.check), date=iss.date.fillna("")).sort_values(["severity", "date"], ascending=[True, False])
            iss["key"] = iss.symbol + "|" + iss.date.astype(str) + "|" + iss.check
        return dict(summary=qs, issues=iss[["key", "severity", "what", "check", "symbol", "date", "detail", "reviewed"]].to_dict("records") if len(iss) else [],
                    checks=[[k, v] for k, v in label.items()])
    if tab == "actions":
        from core import quality
        ca = pd.read_csv(quality.ACTIONS) if quality.ACTIONS.exists() else pd.DataFrame(columns=["symbol", "ex_date", "factor", "note"])
        done = []
        if len(ca) and px is not None:
            _, done = quality.apply_corporate_actions(px)
        sa = C.DATA / "split_adjustments.csv"
        auto = pd.read_csv(sa) if sa.exists() and sa.stat().st_size > 1 else pd.DataFrame()
        return dict(rows=ca.fillna("").astype(str).to_dict("records"), columns=["symbol", "ex_date", "factor", "note"],
                    applied=table("Applied when loading", pd.DataFrame(done), tools=False, empty="None applied.") if len(ca) else None,
                    auto=table("Adjustments made automatically by the download", auto, tools=False, empty="None."))
    if tab == "universe":
        from core import universe_pit
        uni = universe()
        secs = []
        if px is not None:
            miss = sorted(set(uni.Symbol) - set(px.symbol))
            secs.append(dict(kind="note", text=f"Today's Nifty 500 list: {len(uni)} stocks, {len(miss)} without data" + (f" ({', '.join(miss[:15])}{' …' if len(miss) > 15 else ''})" if miss else "") + "."))
        ch = C.DATA / "universe_changes.csv"
        if ch.exists():
            secs.append(table("Nifty 500 changes seen by the download", pd.read_csv(ch).sort_values("date", ascending=False)))
        secs.append(dict(kind="note", text="Point-in-time universe for the Strategy Lab: " + ("official list in data/membership.csv." if universe_pit.MEMBERSHIP.exists() else
                         "top 500 by traded value at each March/September review" + (" across all NSE stocks." if has_broad() else ". Download all NSE stocks for it to include stocks that later left the index."))))
        return dict(sections=secs)
    raise UserError("unknown tab")


def _history():
    from core import news as N, announcements as A, articles as AR, company_info as CI
    uni = universe()
    n_uni = max(len(uni), 1)
    years = pd.Timestamp.now().year - 2016 + 1
    rows = []
    info = CI.load(raw=True)
    rows.append(dict(label="Company sectors and shares", frac=len(info) / n_uni, detail=f"{len(info)} of {n_uni} stocks", job="company-info", button="Download",
                     help="NSE's industry levels and shares outstanding (~10 min)"))
    bf = _json(A.BACKFILL_STATE)
    done = len(bf.get("done", []))
    an = A.load()
    rows.append(dict(label="NSE filings (2016 →)", frac=done / (n_uni * years), job="announcements-backfill", button="Download / continue",
                     detail=f"{done:,} of {n_uni * years:,} stock-years · {len(an):,} filings stored" + (f" · from {an.published.min():%b %Y}" if len(an) else ""),
                     help="One request per stock per year, newest first; ~75 min per 3,000 requests"))
    nb = _json(N.BACKFILL)
    nw = N.load_store()
    months = pd.period_range("2016-01", pd.Timestamp.now().to_period("M"), freq="M")
    per_month = nw.published.dt.to_period("M").value_counts() if len(nw) else pd.Series(dtype=int)
    covered = int((per_month.reindex(months).fillna(0) >= 100).sum())
    rows.append(dict(label="News archives (2016 →)", frac=covered / len(months), job="news-backfill", button="Download / continue",
                     detail=f"{len(nw):,} headlines · {covered} of {len(months)} months with 100+ headlines",
                     help=f"Publishers' dated sitemaps; {len(nb.get('done', []))} archive files read so far"))
    st_ = AR.load_state()
    ok = int((st_.status == "ok").sum()) if len(st_) else 0
    rows.append(dict(label="Article text", frac=len(st_) / max(len(nw), 1), job="news-articles", button="Read 300 more", args=["max=300"],
                     detail=f"{len(st_):,} of {len(nw):,} headlines tried · {ok:,} with full text", help="Where the site allows; paywalls never bypassed (~30–60 min per 300)"))
    try:
        from core import fundamentals as F
        cov = F.coverage()
        last4 = cov.head(4)
        frac = float(last4.stocks_with_numbers.mean() / n_uni) if len(last4) else 0.0
        rows.append(dict(label="Quarterly results (NSE XBRL)", frac=frac, job="fundamentals-backfill", button="Download / continue",
                         detail=(f"last 4 quarters: {', '.join(f'{pd.Timestamp(r.period_end):%b %y} {r.stocks_with_numbers}' for r in last4.itertuples())} of {n_uni} stocks" if len(last4) else "nothing read yet"),
                         help="Reads each company's quarterly results filing (XBRL) since 2021; resumes where it stopped"))
    except Exception:
        pass
    try:
        from core import search_index as SI
        pr = SI.progress()
        rows.append(dict(label="Search index", frac=pr["indexed"] / max(pr["total"], 1), job="search-index", button="Update index",
                         detail=f"{pr['indexed']:,} of {pr['total']:,} headlines + filings" + (f" · updated {pr['when'][:16]}" if pr.get("when") else ""),
                         help="Makes everything searchable by meaning (needs the document library packages and nomic-embed-text)"))
    except Exception:
        rows.append(dict(label="Search index", frac=None, job=None, detail="Install the document library packages first (Settings → AI models)."))
    macro = None
    try:
        from core import macro as M
        w = M.load()
        if len(w):
            span = w.groupby("source").date.agg(["min", "max"])
            macro = "Macro history: " + " · ".join(f"{s} {r['min']:%Y}→{r['max']:%d %b %Y}" for s, r in span.iterrows())
    except Exception:
        pass
    return dict(rows=rows, busy=bool(jobs.busy("history-all")), macro=macro)


def _news_section():
    from core import news as N, announcements as A, news_sources as NS
    from news_fetch import options
    nw, an = N.load_store(), A.load()
    secs = [dict(kind="kpis", items=[dict(label="Headlines stored", value=f"{len(nw):,}"),
                                     dict(label="Linked to a stock", value=f"{int(nw.symbols.fillna('').astype(bool).sum()):,}" if len(nw) else "0"),
                                     dict(label="News from", value=f"{nw.published.min():%d %b %Y}" if len(nw) else "—"),
                                     dict(label="NSE filings stored", value=f"{len(an):,}", delta=f"from {an.published.min():%d %b %Y}" if len(an) else None)])]
    stt = NS.load_state()
    if stt:
        h = pd.DataFrame(stt.get("sources", []))
        if len(h):
            secs.append(table(f"News sources: what works (checked {stt.get('checked', '')[:16]})", h[[c for c in ["source", "method", "status", "items", "newest", "note"] if c in h]],
                              {"status": "status", "note": "long"}, note="dead = no items or nothing newer than a week (dropped from fetches until it recovers). blocked = robots.txt disallows it, so it is not read."))
    else:
        secs.append(dict(kind="note", text="News sources not checked yet. Press Check news sources."))
    return dict(options=options(), sections=secs)


@route("POST", "data/news-options")
def data_news_options(_, body):
    from news_fetch import save_options
    save_options({"gnews": bool(body.get("gnews")), "sitemaps": bool(body.get("sitemaps"))})
    return dict(message="Saved. Applies from the next news fetch.")


@route("POST", "data/review")
def data_review(_, body):
    from core import quality
    from core.data import load_raw
    keys = body.get("keys") or []
    if not keys:
        raise UserError("select issues first")
    rows = []
    for k in keys:
        s, d, c = (str(k).split("|") + ["", "", ""])[:3]
        rows.append(dict(symbol=s, date=d, check=c))
    quality.mark_reviewed(rows, str(body.get("note") or ""))
    px_, idx_, _ = load_raw()
    quality.run(px_, idx_)
    return dict(message=f"Marked {len(rows)} as reviewed.")


@route("POST", "data/actions")
def data_actions(_, body):
    from core import quality
    d = pd.DataFrame(body.get("rows") or [], columns=["symbol", "ex_date", "factor", "note"]).replace("", pd.NA).dropna(subset=["symbol", "ex_date", "factor"])
    if len(d):
        d["symbol"] = d.symbol.astype(str).str.strip().str.upper()
        d["factor"] = pd.to_numeric(d.factor, errors="coerce")
        if d.factor.isna().any() or (d.factor < 1).any():
            raise UserError("factor must be a number of 1 or more (2 = 1:1 bonus or 2:1 split)")
        if pd.to_datetime(d.ex_date, errors="coerce").isna().any():
            raise UserError("ex_date must be YYYY-MM-DD")
    d.to_csv(quality.ACTIONS, index=False)
    from web import api
    api._cache.clear()
    return dict(message="Saved. They apply from the next agent run, Lab test and quality check.")
