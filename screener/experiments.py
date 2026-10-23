"""Strategy definitions, experiments and the comparison report.

A STRATEGY is a JSON file in config/screener/strategies/ (no code needed):
    {"name": "breakout_volume", "version": 1, "description": "...", "hypothesis": "...", "limitations": "...",
     "config": {"setups": {...only what differs from the defaults...}, "combine": "any"},
     "primary_horizon": 10, "evaluate_on": "qualifies"}           # or "shortlist" (rank <= top_n) for ranking tests
A setup that needs new logic is a Python function in setups.py (see the note there); the strategy then names it.

An EXPERIMENT is a JSON file in config/screener/experiments/:
    {"name": "breakout_volume_thresholds", "description": "...", "strategies": ["breakout_volume"],
     "grid": {"setups.breakout_volume.rel_volume_min": [1.5, 2.0, 2.5]}, "base": {...overrides for all...}}
Every strategy x grid point is one configuration. Its id is a hash of the configuration, the screener
and evaluation versions and the data version, so a finished configuration is skipped unless forced, and
any configuration can be re-run exactly. Results go to results/screener/experiments/<run_key>.json and the
table screener_experiments; failures are recorded there too, never dropped."""
import hashlib, itertools, json, time, traceback
import numpy as np, pandas as pd
from core import db
from . import config as SC, screen as S, evaluate as V

RES_DIR = SC.OUT_DIR / "experiments"
STATUS = ("experimental", "validated", "enabled")


# ---------------- strategies ----------------
def load_strategies():
    out = {}
    for p in sorted(SC.STRAT_DIR.glob("*.json")):
        d = SC.load_json(p)
        for k in ("name", "version", "config"):
            if k not in d:
                raise SC.ConfigError(f"{p.name}: missing '{k}'")
        d["config"] = SC.validate(d["config"])                    # fails loudly on bad parameters
        d.setdefault("primary_horizon", 10)
        d.setdefault("evaluate_on", "qualifies")
        if d["primary_horizon"] not in d["config"]["horizons"]:
            raise SC.ConfigError(f"{p.name}: primary_horizon must be one of {d['config']['horizons']}")
        if d["evaluate_on"] not in ("qualifies", "shortlist"):
            raise SC.ConfigError(f"{p.name}: evaluate_on must be qualifies or shortlist")
        d["file"] = p.name
        key = f"{d['name']}@v{d['version']}"
        if key in out:
            raise SC.ConfigError(f"duplicate strategy {key}")
        out[key] = d
    return out


def load_experiments():
    return {SC.load_json(p)["name"]: {**SC.load_json(p), "file": p.name} for p in sorted(SC.EXP_DIR.glob("*.json"))}


def expand(exp, strategies):
    """Experiment -> list of configurations (dicts with strategy, params, config, run_key)."""
    names = exp.get("strategies") or []
    if not names:
        raise SC.ConfigError(f"experiment {exp.get('name')}: list at least one strategy")
    grid = exp.get("grid") or {}
    keys = list(grid)
    for k, v in grid.items():
        if not isinstance(v, list) or not v:
            raise SC.ConfigError(f"experiment {exp['name']}: grid '{k}' must be a non-empty list")
    out = []
    for n in names:
        matches = [s for k, s in strategies.items() if k == n or s["name"] == n]
        if not matches:
            raise SC.ConfigError(f"experiment {exp['name']}: unknown strategy '{n}'")
        st = sorted(matches, key=lambda s: s["version"])[-1]
        for combo in itertools.product(*[grid[k] for k in keys]) if keys else [()]:
            cfg = SC.merge(st["config"], exp.get("base") or {})
            params = dict(zip(keys, combo))
            for k, v in params.items():
                SC.set_path(cfg, k, v)
            cfg = SC.validate(cfg)
            out.append(dict(experiment=exp["name"], strategy=st["name"], strategy_version=st["version"], params=params,
                            config=cfg, primary_horizon=exp.get("primary_horizon", st["primary_horizon"]),
                            evaluate_on=exp.get("evaluate_on", st["evaluate_on"]), config_id=SC.config_id(cfg)))
    return out


def run_key(c, data_version):
    blob = json.dumps(dict(cfg=c["config_id"], ev=V.EVAL_VERSION, sv=SC.VERSION, data=data_version, sel=c["evaluate_on"]), sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def index():
    r = db.read("select * from screener_experiments order by finished desc")
    return r if r is not None else pd.DataFrame()


def configs_tested():
    """Distinct configurations ever evaluated (for the multiple-testing warning)."""
    r = db.read("select count(distinct config_id) n from screener_experiments where status = 'ok'")
    return int(r.n.iat[0]) if r is not None and len(r) else 0


# ---------------- runner ----------------
class Context:
    """Loaded once per run: validated data, features, benchmark, calendar, regime by date."""
    def __init__(self):
        px, bench, uni, issues = S.load_inputs()
        self.feat = S.features()
        self.bench, self.today, self.issues = bench, list(uni.Symbol), issues
        self.calendar = sorted(bench.dropna(subset=["nifty"]).date.unique())
        self.data_version = S.data_version()
        self.regime = _regime(self.feat, bench)


def _regime(feat, bench):
    from core.indicators import market_state
    idx = bench.copy()
    idx["vix"] = np.nan
    try:
        from core.data import load_raw
        _, raw_idx, _ = load_raw()
        idx = raw_idx[["date", "nifty", "vix"]]
    except Exception:
        pass
    m = market_state(idx, feat[["date", "symbol", "close", "sma200"]])
    return m.set_index("date").market_state


def run(experiment_names=None, force=False, ctx=None, log=print):
    """Runs experiments. Returns a list of {run_key, status}. Never raises for one failed configuration."""
    strategies = load_strategies()
    exps = load_experiments()
    names = experiment_names or list(exps)
    todo = []
    for n in names:
        if n not in exps:
            raise SC.ConfigError(f"unknown experiment '{n}'")
        todo += expand(exps[n], strategies)
    ctx = ctx or Context()
    done = index()
    have = set(done[done.status == "ok"].run_key) if len(done) else set()
    RES_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for i, c in enumerate(todo, 1):
        rk = run_key(c, ctx.data_version)
        label = f"[{i}/{len(todo)}] {c['experiment']} · {c['strategy']} {c['params'] or ''}"
        if rk in have and not force:
            log(f"{label}: already done, skipped")
            out.append(dict(run_key=rk, status="skipped"))
            continue
        t0 = time.time()
        row = dict(run_key=rk, experiment=c["experiment"], strategy=c["strategy"], strategy_version=int(c["strategy_version"]),
                   config_id=c["config_id"], params=json.dumps(c["params"], default=str), data_version=ctx.data_version,
                   screener_version=SC.VERSION, eval_version=V.EVAL_VERSION, evaluate_on=c["evaluate_on"],
                   primary_horizon=int(c["primary_horizon"]), started=pd.Timestamp.now().floor("s"))
        try:
            ev = evaluate_config(c, ctx)
            n_tested = configs_tested() + (0 if c["config_id"] in set(done.config_id if len(done) else []) else 1)
            verdict = V.validate(ev, c["primary_horizon"], n_tested)
            payload = dict(**{k: v for k, v in c.items() if k != "config"}, config=c["config"], run_key=rk, data_version=ctx.data_version,
                           evaluation=ev, verdict=verdict, finished=time.strftime("%Y-%m-%d %H:%M:%S"))
            (RES_DIR / f"{rk}.json").write_text(json.dumps(_clean(payload), indent=1, default=str))
            d = ev["by_period"]["development"][c["primary_horizon"]]
            v = ev["by_period"]["validation"][c["primary_horizon"]]
            row.update(status="ok", error="", validated=bool(verdict["validated"]), seconds=round(time.time() - t0, 1),
                       dev_signals=d.get("signals", 0), dev_excess=_f(d.get("excess")), dev_t=_f(d.get("t_excess")),
                       dev_lift_pp=_f(d.get("lift_pp")), dev_mean_net=_f(d.get("mean_net")),
                       val_signals=v.get("signals", 0), val_excess=_f(v.get("excess")), val_t=_f(v.get("t_excess")),
                       val_lift_pp=_f(v.get("lift_pp")), val_mean_net=_f(v.get("mean_net")))
            log(f"{label}: ok in {row['seconds']}s, {'VALIDATED' if verdict['validated'] else 'not validated'}")
        except Exception as e:
            row.update(status="failed", error=f"{type(e).__name__}: {e}", validated=False, seconds=round(time.time() - t0, 1))
            log(f"{label}: FAILED {row['error']}\n{traceback.format_exc(limit=3)}")
        row["finished"] = pd.Timestamp.now().floor("s")
        db.delete("screener_experiments", "run_key = ?", [rk])
        db.write("screener_experiments", pd.DataFrame([row]))
        out.append(dict(run_key=rk, status=row["status"]))
    return out


def evaluate_config(c, ctx):
    cfg = c["config"]
    res = S.compute(ctx.feat, cfg, ctx.today, ctx.issues)
    return V.evaluate(res, cfg["horizons"], ctx.bench, ctx.calendar, cfg["cost_pct"],
                      select=c["evaluate_on"], top_n=cfg["top_n"], regime=ctx.regime)


def load_result(rk):
    p = RES_DIR / f"{rk}.json"
    return json.loads(p.read_text()) if p.exists() else None


def _f(x):
    try:
        x = float(x)
        return x if np.isfinite(x) else None
    except Exception:
        return None


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


# ---------------- comparison ----------------
def comparison(horizon=None, include_holdout=False):
    """One row per finished configuration (latest data version first). Signal statistics, not portfolio returns."""
    idx = index()
    if idx.empty:
        return pd.DataFrame()
    rows = []
    for r in idx.itertuples():
        base = dict(experiment=r.experiment, strategy=f"{r.strategy} v{r.strategy_version}", params=r.params, status=r.status,
                    config_id=r.config_id, run_key=r.run_key, data_version=r.data_version, finished=r.finished)
        if r.status != "ok":
            rows.append({**base, "error": r.error}); continue
        res = load_result(r.run_key)
        if res is None:
            rows.append({**base, "status": "missing file"}); continue
        h = horizon or res["primary_horizon"]
        ev = res["evaluation"]
        row = {**base, "horizon": h, "validated": res["verdict"]["validated"] if h == res["primary_horizon"] else None}
        periods = ["development", "validation"] + (["holdout"] if include_holdout else [])
        for p in periods:
            s = (ev["by_period"].get(p) or {}).get(str(h)) or (ev["by_period"].get(p) or {}).get(h) or {}
            pre = {"development": "dev", "validation": "val", "holdout": "hold"}[p]
            row[f"{pre}_signals"] = s.get("signals")
            row[f"{pre}_stocks"] = s.get("stocks")
            row[f"{pre}_dates"] = s.get("dates")
            row[f"{pre}_win_%"] = s.get("positive_rate")
            row[f"{pre}_base_win_%"] = s.get("baseline_positive_rate")
            row[f"{pre}_lift_pp"] = s.get("lift_pp")
            row[f"{pre}_mean_net_%"] = s.get("mean_net")
            row[f"{pre}_median_net_%"] = s.get("median_net")
            row[f"{pre}_excess_%"] = s.get("excess")
            row[f"{pre}_excess_ci95"] = s.get("excess_ci95")
            row[f"{pre}_t"] = s.get("t_excess")
            row[f"{pre}_p5_net_%"] = s.get("p5_net")
            row[f"{pre}_vs_nifty_%"] = s.get("bench_relative")
            row[f"{pre}_warning"] = s.get("warning")
        rows.append(row)
    return add_neighbours(pd.DataFrame(rows))


def add_neighbours(t):
    """Robustness to nearby parameters: for each grid point, the share of its neighbours (same experiment and
    strategy, one grid setting moved to the adjacent value) that also beat the baseline in validation. A result
    that only works at one exact setting is likely luck. Empty when the configuration has no grid neighbours."""
    if t.empty or "val_excess_%" not in t:
        return t
    t = t.copy()
    t["neighbours_val_positive"] = None
    ok = t[t.status == "ok"]
    for (_, _), g in ok.groupby(["experiment", "strategy"]):
        ps = {i: json.loads(p) if isinstance(p, str) else {} for i, p in g.params.items()}
        keys = sorted({k for p in ps.values() for k in p})
        values = {k: sorted({json.dumps(p.get(k)) for p in ps.values()}) for k in keys}
        for i, p in ps.items():
            nb = []
            for j, q in ps.items():
                if i == j:
                    continue
                diff = [k for k in keys if p.get(k) != q.get(k)]
                if len(diff) == 1 and all(isinstance(json.loads(x), (int, float, bool)) for x in values[diff[0]]):
                    k = diff[0]
                    a, b = values[k].index(json.dumps(p.get(k))), values[k].index(json.dumps(q.get(k)))
                    if abs(a - b) == 1:
                        nb.append(j)
            if nb:
                v = t.loc[nb, "val_excess_%"].astype(float)
                t.at[i, "neighbours_val_positive"] = f"{int((v > 0).sum())} of {len(nb)}"
    return t


# ---------------- live selection ----------------
def enabled():
    if not SC.ENABLED.exists():
        return []
    return json.loads(SC.ENABLED.read_text()).get("strategies", [])


def set_enabled(strategy_key, on, run_key=None, note=""):
    """strategy_key: 'name@vN'. Stores the exact saved configuration so the live screener never depends on
    later edits or on any backtest outcome."""
    strategies = load_strategies()
    cur = [e for e in enabled() if e["key"] != strategy_key]
    if on:
        st = strategies[strategy_key]
        res = load_result(run_key) if run_key else None
        cur.append(dict(key=strategy_key, name=st["name"], version=st["version"], config=st["config"],
                        config_id=SC.config_id(st["config"]), validated=bool(res and res["verdict"]["validated"]),
                        evidence_run=run_key, enabled_at=time.strftime("%Y-%m-%d %H:%M:%S"), note=note))
    SC.ENABLED.parent.mkdir(parents=True, exist_ok=True)
    SC.ENABLED.write_text(json.dumps(dict(strategies=cur), indent=2, default=str))


def status_of(strategy_key):
    """experimental | validated | enabled (enabled wins; validated = latest finished run of its own
    configuration passed the criteria on the latest data version)."""
    if any(e["key"] == strategy_key for e in enabled()):
        return "enabled"
    idx = index()
    if idx.empty:
        return "experimental"
    name, v = strategy_key.split("@v")
    st = load_strategies().get(strategy_key)
    if not st:
        return "experimental"
    own = idx[(idx.strategy == name) & (idx.strategy_version == int(v)) & (idx.config_id == SC.config_id(st["config"])) & (idx.status == "ok")]
    if own.empty:
        return "experimental"
    return "validated" if bool(own.sort_values("finished").validated.iat[-1]) else "experimental"
