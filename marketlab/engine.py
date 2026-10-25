"""Analog engine: standardise the scenario, find the nearest past stock-days, turn their next-session outcomes into the
chance of a move, a lean, a confidence, and per-node drivers for the influence map. See docs/market_lab.md."""
import numpy as np, pandas as pd
from .features import GROUPS, FEATURES, MOVES, SIZES

K = 150                 # past cases per query
SHRINK = 40             # pseudo-cases at the stock's usual rate (pulls thin evidence toward "normal")
LEAN_SHRINK = 10        # pseudo-cases at 50% for the direction
LEAN_EDGE = 0.08        # lean shown only when the shrunk up-share is this far from 50%
MIN_BIG = 15            # cases with a big move needed before giving a lean
DAY_GROUPS = ("Market", "Macro")   # the same for every stock on a day: kept out of confidence, reported as "unusual market day"
CLIP = {"f_ret1": 8, "f_ret5": 8, "f_gap": 8, "x_brent": 15, "x_inr": 3, "x_vix": 10, "x_y10": 0.4}


class Scaler:
    """Mean / std on past data, clipped to ±4; each node's features together weigh as much as one feature."""
    def fit(self, X, groups=GROUPS):
        self.cols = [f for g in groups.values() for f in g]
        self.hi = pd.Series({c: CLIP.get(c, np.inf) for c in self.cols})
        x = self._clip(X)
        self.mu, self.sd = x.mean(), x.std().replace(0, 1).fillna(1)
        self.w = pd.Series({f: 1 / np.sqrt(len(fs)) for fs in groups.values() for f in fs})
        return self

    def _clip(self, X):
        """Caps extreme values of the features listed in CLIP; every other feature passes through unchanged."""
        return X[self.cols].astype(float).clip(lower=-self.hi, upper=self.hi, axis=1)

    def transform(self, X):
        x = self._clip(X)
        z = ((x - self.mu) / self.sd).clip(-4, 4).fillna(0) * self.w
        return z.to_numpy(np.float32)


class Library:
    """Past stock-days whose next-session outcome is known.
    vol_scale: a past case's move is resized by (this stock's volatility / that case's volatility), so a 2% move in a
    stock that usually moves 3% a day counts as a small move for a stock that usually moves 1%."""
    def __init__(self, lib, groups=GROUPS, k=K, vol_scale=True, max_rows=None, seed=0):
        lib = lib.dropna(subset=["cc", "oc", "gp"])
        if max_rows and len(lib) > max_rows:                    # the test samples the past to stay fast; the nightly radar uses all of it
            lib = lib.sample(max_rows, random_state=seed)
        self.groups, self.k = groups, min(k, max(1, len(lib) - 1))
        self.sc = Scaler().fit(lib, groups)
        self.X = self.sc.transform(lib)
        self.x2 = (self.X.astype(np.float32) ** 2).sum(axis=1)
        self.out = {m: lib[m].to_numpy(np.float32) for m in MOVES}
        self.meta = lib[["symbol", "date"]].reset_index(drop=True)
        self.vol_scale = vol_scale
        self.rv = lib["f_rv"].to_numpy(np.float32)
        day_cols = {f for g in DAY_GROUPS for f in groups.get(g, [])}
        self.own = np.array([c not in day_cols for c in self.sc.cols])   # the stock's own features
        rng = np.random.default_rng(0)                         # typical neighbour distances, for confidence
        ri = rng.choice(len(self.X), min(1000, len(self.X)), replace=False)
        self.dist_ref = self.day_ref = np.array([])
        _, ii = self.query(self.X[ri], k=min(self.k + 1, len(self.X)))
        own, day = self.part_dist(self.X[ri], ii[:, 1:])
        self.dist_ref, self.day_ref = np.sort(own), np.sort(day)

    def part_dist(self, Z, idx, chunk=2000):
        """Mean distance to the matched cases, split into the stock's own features and the day-wide ones (Market, Macro)."""
        Z = np.asarray(Z, np.float32)
        own, day = np.empty(len(Z)), np.empty(len(Z))
        for a in range(0, len(Z), chunk):
            d2 = (Z[a:a + chunk, None, :] - self.X[idx[a:a + chunk]]) ** 2
            own[a:a + chunk] = np.sqrt(d2[..., self.own].sum(axis=2)).mean(axis=1)
            day[a:a + chunk] = np.sqrt(d2[..., ~self.own].sum(axis=2)).mean(axis=1) if (~self.own).any() else 0.0
        return own, day

    def query(self, Z, k=None):
        """Exact nearest neighbours by chunked matrix search (fast for this many dimensions)."""
        k = k or self.k
        Z = np.asarray(Z, np.float32)
        step = max(1, int(6e7 // max(1, len(self.X))))
        D, I = np.empty((len(Z), k), np.float32), np.empty((len(Z), k), np.int64)
        for a in range(0, len(Z), step):
            z = Z[a:a + step]
            d2 = self.x2[None, :] - 2 * (z @ self.X.T) + (z ** 2).sum(axis=1)[:, None]
            part = np.argpartition(d2, k - 1, axis=1)[:, :k]
            dk = np.take_along_axis(d2, part, axis=1)
            o = np.argsort(dk, axis=1)
            I[a:a + step] = np.take_along_axis(part, o, axis=1)
            D[a:a + step] = np.sqrt(np.maximum(np.take_along_axis(dk, o, axis=1), 0))
        return D, I

    def estimate(self, Q, Z=None, combos=None):
        """Q: query rows (needs u_<move>_<size>). Returns one row per query with chance / lean / n for each combo."""
        Z = self.sc.transform(Q) if Z is None else Z
        dist, idx = self.query(Z)
        own, day = self.part_dist(Z, idx)
        out = {"dist_pct": np.searchsorted(self.dist_ref, own) / max(1, len(self.dist_ref)),      # confidence: the stock's own situation
               "day_pct": np.searchsorted(self.day_ref, day) / max(1, len(self.day_ref))}         # how unusual the market / macro day is
        scale = 1.0
        if self.vol_scale:
            qrv = Q["f_rv"].to_numpy(np.float32)
            scale = np.exp(np.clip(np.nan_to_num(qrv[:, None] - self.rv[idx]), -1.5, 1.5))
        for m in MOVES:
            y = self.out[m][idx] * scale                        # queries x K, resized to this stock's volatility
            for s in (combos or SIZES):
                big = np.abs(y) >= s / 100
                usual = Q[f"u_{m}_{s}"].to_numpy(float)
                usual = np.where(np.isfinite(usual), usual, big.mean(axis=1))
                n = big.shape[1]
                out[f"ch_{m}_{s}"] = (big.sum(axis=1) + SHRINK * usual) / (n + SHRINK)
                nb = big.sum(axis=1)
                ups = ((y > 0) & big).sum(axis=1)
                out[f"up_{m}_{s}"] = (ups + LEAN_SHRINK * 0.5) / (nb + LEAN_SHRINK)
                out[f"nb_{m}_{s}"] = nb
                out[f"us_{m}_{s}"] = usual
        return pd.DataFrame(out, index=Q.index), idx, dist

    def outcomes(self, Q, idx, m):
        """The K cases' next-session moves for one move type, resized like estimate() does."""
        y = self.out[m][idx]
        if self.vol_scale:
            y = y * np.exp(np.clip(np.nan_to_num(Q["f_rv"].to_numpy(np.float32)[:, None] - self.rv[idx]), -1.5, 1.5))
        return y

    def drivers(self, Q, base):
        """Per node: re-match with that node's features at 'typical' and compare. Returns long frame."""
        Z = self.sc.transform(Q)
        rows = []
        for g, fs in self.groups.items():
            z = Z.copy()
            cols = [self.sc.cols.index(f) for f in fs]
            z[:, cols] = 0.0
            cf, _, _ = self.estimate(Q, z)
            for m in MOVES:
                for s in SIZES:
                    rows.append(pd.DataFrame({"symbol": Q.symbol.values, "node": g, "move": m, "size": s,
                                              "d_chance": (base[f"ch_{m}_{s}"] - cf[f"ch_{m}_{s}"]).values,
                                              "d_up": (base[f"up_{m}_{s}"] - cf[f"up_{m}_{s}"]).values}))
        return pd.concat(rows, ignore_index=True)


def lean_of(up, nb):
    return np.where(nb < MIN_BIG, "unclear", np.where(up >= 0.5 + LEAN_EDGE, "up", np.where(up <= 0.5 - LEAN_EDGE, "down", "unclear")))


def confidence(dist_pct, nb, skill_ok):
    """How close and how many the past cases are; always 'low' when the combination showed no tested skill."""
    c = np.where((dist_pct <= 0.4) & (nb >= 30), "high", np.where((dist_pct >= 0.8) | (nb < MIN_BIG), "low", "medium"))
    return c if skill_ok else np.full(len(c), "low", dtype=object)
