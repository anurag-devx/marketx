"""Probability engine: the only source of the lab's numerical probabilities.

Model: logistic regression (binary targets) or multinomial logistic regression (three outcomes with a neutral band),
pooled over the liquid universe, on standardised technical and market features (FEATURES below). Fitted with L2
penalty by Newton's method (binary) or L-BFGS (three outcomes). Deterministic: same data -> same numbers.

No leakage:
  - a training row is used only if its outcome window ENDED before the test period starts (purged walk-forward)
    or before the analysis cutoff (final model);
  - feature scaling and winsorising use training rows only;
  - test predictions are scored every h-th day so the scored windows do not overlap in time.
Walk-forward: for each calendar year Y (from 2018), train on everything that ended before 1 Jan Y, predict Y.
Out-of-sample metrics: Brier score, log loss, ROC-AUC (discrimination), expected calibration error (10 equal-count
bins) and a reliability table (calibration), Brier skill score vs the historical base rate with a bootstrap interval
(resampling days). 'Calibrated' and 'skill' are claimed only when these out-of-sample numbers support them.

Baselines compared on the same out-of-sample rows: historical base rate, market backdrop only, stock technicals only,
and the full model. The full bull/bear/judge pipeline cannot be replayed on history (language-model outputs cannot be
made point-in-time), so it is scored only on its own recorded forecasts as they mature (debate/evaluate.py)."""
import json, hashlib
import numpy as np, pandas as pd
from scipy import optimize
from core import config as C, atomic
from . import VERSIONS
from .stats import eligible, bootstrap_ci

MODELS = C.DATA / "debate" / "models"
TECH = ["ret5", "ret21", "ret63", "rs_c", "rsi_c", "macd_n", "adx_dir", "vs50", "vs200", "atr_pct", "lvol", "gap", "bbpos",
        "sector_c", "obv_n", "rv20"]
MKT = ["nifty_vs_200", "vix_c", "breadth_c"]
FEATURES = TECH + MKT
FEATURE_LABEL = {"ret5": "5-day return", "ret21": "21-day return", "ret63": "63-day return", "rs_c": "relative strength percentile",
                 "rsi_c": "RSI(14)", "macd_n": "MACD histogram / ATR", "adx_dir": "ADX x trend direction", "vs50": "distance from 50-day SMA",
                 "vs200": "distance from 200-day SMA", "atr_pct": "ATR % of price", "lvol": "log volume ratio", "gap": "opening gap",
                 "bbpos": "position in Bollinger band", "sector_c": "sector strength percentile", "obv_n": "OBV 20-day change",
                 "rv20": "20-day realised volatility", "nifty_vs_200": "Nifty vs 200-day SMA", "vix_c": "VIX vs 1-year median",
                 "breadth_c": "share of stocks above 200-day SMA"}
SETS = {"Market backdrop only": MKT, "Stock technicals only": TECH, "Full statistical model": FEATURES}
L2 = 1.0
FIRST_TEST_YEAR = 2018
ECE_OK = 0.03
MIN_OOS = 5000


def design_matrix(p):
    sd20 = (p.bb_up - p.sma20) / 2
    X = pd.DataFrame({
        "ret5": p.ret5, "ret21": p.ret21, "ret63": p.ret63, "rs_c": p.rs_pct - 0.5, "rsi_c": p.rsi14 / 100 - 0.5,
        "macd_n": p.macd_hist / p.atr14, "adx_dir": p.adx14 / 100 * np.sign(p.pdi - p.mdi), "vs50": p.close / p.sma50 - 1,
        "vs200": p.close / p.sma200 - 1, "atr_pct": p.atr_pct, "lvol": np.log(p.vol_ratio.clip(lower=0.05)), "gap": p.gap,
        "bbpos": (p.close - p.sma20) / (2 * sd20.replace(0, np.nan)), "sector_c": p.sector_rank_pct.fillna(0.5) - 0.5,
        "obv_n": p.obv_chg20.clip(-20, 20), "rv20": p.rv20, "nifty_vs_200": p.nifty_vs_200, "vix_c": p.vix_rel - 1,
        "breadth_c": p.breadth - 0.5}, index=p.index)
    return X.replace([np.inf, -np.inf], np.nan)


def labels(p, target, band=None):
    """binary: 1 if outcome > 0 (abs: R, rel: R - R_nifty). three: 0 down (< -band), 1 neutral, 2 up (> +band)."""
    y = p.fwd_rel if target == "rel" else p.fwd
    if target == "three":
        b = (band or 0.5) / 100
        lab = pd.Series(np.where(y > b, 2, np.where(y < -b, 0, 1)), index=p.index).astype(float)
        return lab.where(y.notna())
    return (y > 0).astype(float).where(y.notna())


class Scaler:
    def __init__(self, X):
        self.lo, self.hi = X.quantile(0.01), X.quantile(0.99)
        Z = X.clip(self.lo, self.hi, axis=1)
        self.mu, self.sd = Z.mean(), Z.std().replace(0, 1)

    def __call__(self, X):
        Z = (X.clip(self.lo, self.hi, axis=1) - self.mu) / self.sd
        return np.column_stack([np.ones(len(Z)), Z.fillna(0).to_numpy()])

    def to_dict(self):
        return {k: getattr(self, k).to_dict() for k in ("lo", "hi", "mu", "sd")}

    @classmethod
    def from_dict(cls, d):
        s = cls.__new__(cls)
        for k in ("lo", "hi", "mu", "sd"):
            setattr(s, k, pd.Series(d[k]))
        return s


def fit_binary(Z, y, l2=L2, iters=25):
    w = np.zeros(Z.shape[1])
    R = l2 * np.eye(Z.shape[1]); R[0, 0] = 0
    for _ in range(iters):
        pr = 1 / (1 + np.exp(-np.clip(Z @ w, -30, 30)))
        g = Z.T @ (pr - y) + R @ w
        H = (Z * (pr * (1 - pr))[:, None]).T @ Z + R
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-7:
            break
    return w


def predict_binary(Z, w):
    return 1 / (1 + np.exp(-np.clip(Z @ w, -30, 30)))


def fit_multi(Z, y, k=3, l2=L2):
    n, d = Z.shape
    Y = np.eye(k)[y.astype(int)]

    def f(wf):
        W = wf.reshape(d, k)
        S = Z @ W
        S -= S.max(1, keepdims=True)
        P = np.exp(S); P /= P.sum(1, keepdims=True)
        reg = 0.5 * l2 * (W[1:] ** 2).sum()
        loss = -(Y * np.log(np.clip(P, 1e-12, 1))).sum() + reg
        G = Z.T @ (P - Y)
        G[1:] += l2 * W[1:]
        return loss, G.ravel()
    r = optimize.minimize(f, np.zeros(d * k), jac=True, method="L-BFGS-B", options={"maxiter": 500})
    return r.x.reshape(d, k)


def predict_multi(Z, W):
    S = Z @ W
    S -= S.max(1, keepdims=True)
    P = np.exp(S)
    return P / P.sum(1, keepdims=True)


# ---------------- metrics ----------------
def brier(p, y):
    return float(np.mean((p - y) ** 2))


def logloss(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def auc(p, y):
    y = np.asarray(y).astype(bool)
    if y.all() or (~y).all():
        return np.nan
    r = pd.Series(p).rank().to_numpy()
    n1, n0 = y.sum(), (~y).sum()
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def reliability(p, y, bins=10):
    """Equal-count bins: mean predicted vs observed frequency."""
    d = pd.DataFrame({"p": p, "y": y})
    d["bin"] = 0 if d.p.nunique() <= 1 else pd.qcut(d.p.rank(method="average"), bins, labels=False, duplicates="drop")   # ties share a bin
    t = d.groupby("bin").agg(pred=("p", "mean"), obs=("y", "mean"), n=("y", "size")).reset_index()
    ece = float((t.n * (t.pred - t.obs).abs()).sum() / t.n.sum())
    return t, ece


def multi_brier(P, y, k=3):
    return float(np.mean(((P - np.eye(k)[y.astype(int)]) ** 2).sum(1)))


def multi_logloss(P, y):
    return float(-np.mean(np.log(np.clip(P[np.arange(len(y)), y.astype(int)], 1e-6, 1))))


def skill_ci(loss_model, loss_base, days, seed=5):
    """Brier skill score 1 - model/base with a bootstrap interval resampling whole days."""
    d = pd.DataFrame({"m": loss_model, "b": loss_base, "d": days}).groupby("d").sum()
    bss = 1 - d.m.sum() / d.b.sum()
    rng = np.random.default_rng(seed)
    M, B = d.m.to_numpy(), d.b.to_numpy()
    sims = []
    for _ in range(500):
        i = rng.integers(0, len(d), len(d))
        sims.append(1 - M[i].sum() / B[i].sum())
    return float(bss), float(np.quantile(sims, 0.025)), float(np.quantile(sims, 0.975))


# ---------------- walk-forward ----------------
def _key(h, target, band, cutoff, stamp=""):
    raw = f"{h}|{target}|{band}|{pd.Timestamp(cutoff).date()}|{VERSIONS['probability']}|{VERSIONS['features']}|{stamp}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _train_rows(p, h, upto):
    """Liquid rows whose outcome window ended strictly before `upto`, subsampled every max(h,3) days."""
    step = max(h, 3)
    dates = np.sort(p.date.unique())
    pick = set(dates[::step])
    m = eligible(p) & p.end_date.notna() & (p.end_date < upto) & p.date.isin(pick)
    return m


def _fit(Z, y, target):
    return fit_multi(Z, y) if target == "three" else fit_binary(Z, y)


def _pred(Z, w, target):
    return predict_multi(Z, w) if target == "three" else predict_binary(Z, w)


def walk_forward(p, h, target, band, cutoff, log=print):
    """Out-of-sample predictions year by year for each feature set. p has fwd columns. Returns (oos frame, per-set metrics)."""
    cutoff = pd.Timestamp(cutoff)
    X = design_matrix(p)
    Y = labels(p, target, band)
    dates = np.sort(p.date.unique())
    score_days = set(dates[::h])
    years = range(FIRST_TEST_YEAR, cutoff.year + 1)
    rows = []
    for Yr in years:
        start, end = pd.Timestamp(f"{Yr}-01-01"), pd.Timestamp(f"{Yr}-12-31")
        tr = _train_rows(p, h, start) & Y.notna()
        te = eligible(p) & (p.date >= start) & (p.date <= end) & p.end_date.notna() & (p.end_date <= cutoff) & p.date.isin(score_days) & Y.notna()
        if tr.sum() < 5000 or te.sum() < 200:
            continue
        out = pd.DataFrame({"date": p.date[te], "symbol": p.symbol[te], "y": Y[te], "fwd": (p.fwd_rel if target == "rel" else p.fwd)[te], "year": Yr})
        base = Y[tr].value_counts(normalize=True).sort_index() if target == "three" else Y[tr].mean()
        if target == "three":
            for c in range(3):
                out[f"base_{c}"] = float(base.get(float(c), 0))
        else:
            out["base"] = float(base)
        for name, cols in SETS.items():
            sc = Scaler(X.loc[tr, cols])
            w = _fit(sc(X.loc[tr, cols]), Y[tr].to_numpy(), target)
            pr = _pred(sc(X.loc[te, cols]), w, target)
            if target == "three":
                for c in range(3):
                    out[f"{name}|{c}"] = pr[:, c]
            else:
                out[name] = pr
        rows.append(out)
        log(f"  walk-forward {Yr}: trained on {int(tr.sum()):,} rows, scored {int(te.sum()):,}")
    if not rows:
        return pd.DataFrame(), {}
    oos = pd.concat(rows, ignore_index=True)
    return oos, evaluate_oos(oos, target)


def evaluate_oos(oos, target):
    y = oos.y.to_numpy()
    res = {}
    if target == "three":
        Pb = oos[[f"base_{c}" for c in range(3)]].to_numpy()
        lb = ((Pb - np.eye(3)[y.astype(int)]) ** 2).sum(1)
        res["Historical base rate"] = dict(brier=multi_brier(Pb, y), logloss=multi_logloss(Pb, y), auc=np.nan, ece=np.nan, bss=0.0, bss_lo=0.0, bss_hi=0.0)
        for name in SETS:
            P = oos[[f"{name}|{c}" for c in range(3)]].to_numpy()
            lm = ((P - np.eye(3)[y.astype(int)]) ** 2).sum(1)
            t, ece = reliability(P[:, 2], (y == 2).astype(float))
            bss, lo, hi = skill_ci(lm, lb, oos.date.to_numpy())
            res[name] = dict(brier=multi_brier(P, y), logloss=multi_logloss(P, y), auc=auc(P[:, 2], y == 2), ece=ece, bss=bss, bss_lo=lo, bss_hi=hi,
                             reliability=t.to_dict("records"))
    else:
        b = oos.base.to_numpy()
        lb = (b - y) ** 2
        res["Historical base rate"] = dict(brier=brier(b, y), logloss=logloss(b, y), auc=np.nan, ece=reliability(b, y)[1],
                                           bss=0.0, bss_lo=0.0, bss_hi=0.0)
        for name in SETS:
            pr = oos[name].to_numpy()
            t, ece = reliability(pr, y)
            bss, lo, hi = skill_ci((pr - y) ** 2, lb, oos.date.to_numpy())
            res[name] = dict(brier=brier(pr, y), logloss=logloss(pr, y), auc=auc(pr, y), ece=ece, bss=bss, bss_lo=lo, bss_hi=hi,
                             reliability=t.to_dict("records"))
    for name in res:
        res[name]["n"] = int(len(oos))
    res["_years"] = sorted(int(x) for x in oos.year.unique())
    return res


def calibration_status(metrics):
    m = (metrics or {}).get("Full statistical model")
    if not m:
        return "Not evaluated", "no out-of-sample predictions could be made"
    yrs = len(metrics.get("_years", []))
    if m["n"] < MIN_OOS or yrs < 3:
        return "Not enough out-of-sample history", f"{m['n']:,} scored predictions over {yrs} years"
    if m["bss_hi"] < 0:
        return "No skill: worse than the base rate out of sample", (f"Brier skill {m['bss']:+.4f} (95% {m['bss_lo']:+.4f} to {m['bss_hi']:+.4f}); "
                                                                     f"calibration error {m['ece']:.3f}")
    if m["ece"] > ECE_OK:
        return "Not calibrated out of sample", f"calibration error {m['ece']:.3f} > {ECE_OK}"
    if m["bss_lo"] > 0:
        return "Calibrated, with measurable skill", f"calibration error {m['ece']:.3f}; Brier skill {m['bss']:+.4f} (95% {m['bss_lo']:+.4f} to {m['bss_hi']:+.4f})"
    return "Calibrated, no skill over the base rate", (f"calibration error {m['ece']:.3f}; Brier skill {m['bss']:+.4f} "
                                                       f"(95% {m['bss_lo']:+.4f} to {m['bss_hi']:+.4f}) — not distinguishable from guessing the base rate")


# ---------------- final model + forecast ----------------
def build_model(p, h, target, band, cutoff, log=print, refresh=False, stamp=""):
    """Walk-forward evaluation + final model trained on everything known by the cutoff. Cached per (h, target, band, cutoff)."""
    key = _key(h, target, band, cutoff, stamp)
    f = MODELS / f"{key}.json"
    if f.exists() and not refresh:
        return json.loads(f.read_text())
    log(f"Probability model: {h} sessions, target {target}{'' if band is None else f' ±{band}%'}, cutoff {pd.Timestamp(cutoff).date()}")
    oos, metrics = walk_forward(p, h, target, band, cutoff, log=log)
    X = design_matrix(p)
    Y = labels(p, target, band)
    tr = _train_rows(p, h, pd.Timestamp(cutoff) + pd.Timedelta(days=1)) & Y.notna()
    sc = Scaler(X.loc[tr, FEATURES])
    w = _fit(sc(X.loc[tr, FEATURES]), Y[tr].to_numpy(), target)
    base = ({str(int(k)): float(v) for k, v in Y[tr].value_counts(normalize=True).sort_index().items()} if target == "three"
            else float(Y[tr].mean()))
    buckets = []
    if len(oos):
        col = "Full statistical model|2" if target == "three" else "Full statistical model"
        o = oos.assign(b=pd.qcut(oos[col].rank(method="average"), 10, labels=False, duplicates="drop"))
        for b, g in o.groupby("b"):
            ci = bootstrap_ci(100 * g.fwd.to_numpy(), block=1, n_boot=300)
            buckets.append(dict(lo=float(g[col].min()), hi=float(g[col].max()), n=int(len(g)), mean=float(100 * g.fwd.mean()),
                                ci_lo=ci[0], ci_hi=ci[1], median=float(100 * g.fwd.median()), p10=float(100 * g.fwd.quantile(0.10)),
                                p90=float(100 * g.fwd.quantile(0.90)), hit=float((g.y == (2 if target == "three" else 1)).mean())))
    status, why = calibration_status(metrics)
    coef = w.tolist()
    rec = dict(key=key, version=VERSIONS["probability"], h=h, target=target, band=band, cutoff=str(pd.Timestamp(cutoff).date()),
               n_train=int(tr.sum()), features=FEATURES, scaler=sc.to_dict(), coef=coef, base=base,
               metrics=_clean(metrics), calibration=status, calibration_why=why, buckets=buckets,
               oos_years=metrics.get("_years", []))
    atomic.write_text(f, json.dumps(rec, default=float))
    return rec


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    return o


def forecast(model, row):
    """row: one-row panel DataFrame at the cutoff. Returns probabilities + feature contributions."""
    X = design_matrix(row)
    sc = Scaler.from_dict(model["scaler"])
    Z = sc(X[model["features"]].astype(float))
    if model["target"] == "three":
        W = np.array(model["coef"])
        P = predict_multi(Z, W)[0]
        probs = {"down": float(P[0]), "neutral": float(P[1]), "up": float(P[2])}
        contrib = (Z[0][1:] * (W[1:, 2] - W[1:, 0]))
        base = {"down": model["base"].get("0", 0.0), "neutral": model["base"].get("1", 0.0), "up": model["base"].get("2", 0.0)}
        p_up = probs["up"]
    else:
        w = np.array(model["coef"])
        p_up = float(predict_binary(Z, w)[0])
        probs = {"up": p_up, "down": 1 - p_up}
        contrib = Z[0][1:] * w[1:]
        base = {"up": model["base"], "down": 1 - model["base"]}
    order = np.argsort(-np.abs(contrib))
    top = [dict(feature=model["features"][i], label=FEATURE_LABEL[model["features"][i]], z=float(Z[0][i + 1]), contribution=float(contrib[i]))
           for i in order[:6]]
    bucket = next((b for b in model["buckets"] if b["lo"] <= p_up <= b["hi"]), None)
    if bucket is None and model["buckets"]:
        bucket = min(model["buckets"], key=lambda b: min(abs(p_up - b["lo"]), abs(p_up - b["hi"])))
    missing = [FEATURE_LABEL[c] for c in model["features"] if not np.isfinite(float(X[c].iloc[0])) ] if len(X) else []
    return dict(probs=probs, base=base, top_drivers=top, bucket=bucket, missing_inputs=missing)
