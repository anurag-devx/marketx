"""Screener configuration: defaults, parameter ranges, validation, and a stable identifier for each config.

Everything here is a research setting, not a market rule. Overrides live in config/screener/*.json
(versioned with the project) and are validated before use: an invalid value fails loudly."""
import copy, hashlib, json
from pathlib import Path
from core import config as C

VERSION = "1.0.0"
CONF_DIR = C.BASE / "config" / "screener"
STRAT_DIR = CONF_DIR / "strategies"
EXP_DIR = CONF_DIR / "experiments"
ENABLED = CONF_DIR / "enabled.json"
OUT_DIR = C.RESULTS / "screener"

# (type, min or allowed values, max, default)
FILTER_SCHEMA = {
    "min_close": (float, 0, 100000, 20.0),
    "min_history": (int, 20, 5000, 252),
    "min_median_value_cr": (float, 0, 100000, 2.0),
    "min_valid_ratio_60d": (float, 0, 1, 0.95),
    "exclude_etf": (bool, None, None, True),
    "exclude_data_errors": (bool, None, None, True),
}

SETUP_SCHEMA = {
    "trend_continuation": {
        "enabled": (bool, None, None, True),
        "slope_lookback": (int, 1, 250, 20),
        "use_rsi": (bool, None, None, True), "rsi_min": (float, 0, 100, 50.0), "rsi_max": (float, 0, 100, 70.0),
        "use_adx": (bool, None, None, True), "adx_min": (float, 0, 100, 20.0),
    },
    "breakout_volume": {
        "enabled": (bool, None, None, True),
        "lookback": (int, 5, 250, 20),
        "rel_volume_min": (float, 0.5, 20, 1.5),
        "require_above_sma50": (bool, None, None, True),
    },
    "pullback_recovery": {
        "enabled": (bool, None, None, True),
        "rsi_cross": (float, 1, 99, 45.0),
        "use_proximity": (bool, None, None, False),
        "proximity_ma": (str, ["sma20", "sma50"], None, "sma20"),
        "proximity_pct": (float, 0, 50, 3.0),
    },
    "relative_strength": {
        "enabled": (bool, None, None, True),
        "return_window": (int, [63, 126], None, 126),
        "use_benchmark_relative": (bool, None, None, True),
        "min_percentile": (float, 0, 100, 80.0),
    },
    "volatility_breakout": {
        "enabled": (bool, None, None, True),
        "contraction_pctile_max": (float, 0, 100, 20.0),
        "breakout_lookback": (int, 5, 250, 20),
        "use_rel_volume": (bool, None, None, True), "rel_volume_min": (float, 0.5, 20, 1.5),
    },
}

RANKING_METHODS = {
    "relative_strength_126": "Percentile of 126-session return vs the Nifty (heuristic)",
    "relative_strength_63": "Percentile of 63-session return vs the Nifty (heuristic)",
    "setup_count_then_rs": "Number of setups passed, ties broken by 126-session relative strength (heuristic)",
    "weighted": "Weighted sum of percentile components (weights are yours; heuristic until validated)",
}
RANK_COMPONENTS = ["rs_126", "rs_63", "rel_volume", "adx", "trend_slope", "setups"]

DEFAULT = {
    "universe": "all",
    "pit_n": 500,
    "filters": {k: v[3] for k, v in FILTER_SCHEMA.items()},
    "setups": {s: {k: v[3] for k, v in p.items()} for s, p in SETUP_SCHEMA.items()},
    "combine": "any",
    "min_setups": 1,
    "ranking": {"method": "relative_strength_126",
                "weights": {"rs_126": 0.5, "rs_63": 0.2, "rel_volume": 0.1, "adx": 0.1, "trend_slope": 0.1}},
    "mode": "top_n",
    "top_n": 50,
    "horizons": [5, 10, 15, 20],
    "cost_pct": round(C.COST_RT * 100, 3),
}


class ConfigError(ValueError):
    pass


def merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _check(where, name, val, spec):
    typ, lo, hi, _ = spec
    if typ is bool:
        if not isinstance(val, bool):
            raise ConfigError(f"{where}.{name} must be true or false, got {val!r}")
        return
    if isinstance(lo, list):
        if val not in lo:
            raise ConfigError(f"{where}.{name} must be one of {lo}, got {val!r}")
        return
    if typ is int and (isinstance(val, bool) or not isinstance(val, int)):
        if isinstance(val, float) and val.is_integer():
            return
        raise ConfigError(f"{where}.{name} must be a whole number, got {val!r}")
    if typ is float and (isinstance(val, bool) or not isinstance(val, (int, float))):
        raise ConfigError(f"{where}.{name} must be a number, got {val!r}")
    if (lo is not None and val < lo) or (hi is not None and val > hi):
        raise ConfigError(f"{where}.{name} must be between {lo} and {hi}, got {val!r}")


def validate(cfg):
    """Returns the full config (defaults filled in) or raises ConfigError with a plain message."""
    c = merge(DEFAULT, cfg)
    unknown = set(c) - set(DEFAULT) - {"name", "version", "description", "hypothesis", "limitations", "status"}
    if unknown:
        raise ConfigError(f"unknown setting(s): {', '.join(sorted(unknown))}")
    if c["universe"] not in ("all", "nifty500_today", "pit_top"):
        raise ConfigError("universe must be all, nifty500_today or pit_top")
    for k in set(c["filters"]) - set(FILTER_SCHEMA):
        raise ConfigError(f"unknown filter '{k}'")
    for k, spec in FILTER_SCHEMA.items():
        _check("filters", k, c["filters"][k], spec)
    for s in set(c["setups"]) - set(SETUP_SCHEMA):
        raise ConfigError(f"unknown setup '{s}'. Known: {', '.join(SETUP_SCHEMA)}")
    for s, sch in SETUP_SCHEMA.items():
        for k in set(c["setups"][s]) - set(sch):
            raise ConfigError(f"unknown parameter setups.{s}.{k}")
        for k, spec in sch.items():
            _check(f"setups.{s}", k, c["setups"][s][k], spec)
    tc = c["setups"]["trend_continuation"]
    if tc["rsi_min"] >= tc["rsi_max"]:
        raise ConfigError("setups.trend_continuation.rsi_min must be below rsi_max")
    if c["combine"] not in ("any", "all", "min_k"):
        raise ConfigError("combine must be any, all or min_k")
    on = [s for s, p in c["setups"].items() if p["enabled"]]
    if not on:
        raise ConfigError("enable at least one setup")
    if c["combine"] == "min_k" and not (1 <= int(c["min_setups"]) <= len(on)):
        raise ConfigError(f"min_setups must be between 1 and the {len(on)} enabled setups")
    if c["ranking"]["method"] not in RANKING_METHODS:
        raise ConfigError(f"ranking.method must be one of {', '.join(RANKING_METHODS)}")
    for k, w in c["ranking"].get("weights", {}).items():
        if k not in RANK_COMPONENTS:
            raise ConfigError(f"unknown ranking weight '{k}'. Known: {', '.join(RANK_COMPONENTS)}")
        if not isinstance(w, (int, float)) or w < 0:
            raise ConfigError(f"ranking weight {k} must be a number >= 0")
    if c["mode"] not in ("top_n", "all"):
        raise ConfigError("mode must be top_n or all")
    if not (1 <= int(c["top_n"]) <= 5000):
        raise ConfigError("top_n must be between 1 and 5000")
    if not c["horizons"] or any(not isinstance(h, int) or h < 1 or h > 60 for h in c["horizons"]):
        raise ConfigError("horizons must be whole numbers of sessions between 1 and 60")
    if not (0 <= float(c["cost_pct"]) <= 5):
        raise ConfigError("cost_pct must be between 0 and 5")
    return c


SELECTION_KEYS = ["universe", "pit_n", "filters", "setups", "combine", "min_setups"]


def config_id(cfg, keys=None):
    """Stable short hash of what decides which stocks qualify (or of `keys`)."""
    c = validate(cfg)
    keys = keys or SELECTION_KEYS + ["ranking", "horizons", "cost_pct"]
    blob = json.dumps({k: c[k] for k in keys}, sort_keys=True, default=str)
    return hashlib.sha1((VERSION + blob).encode()).hexdigest()[:10]


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except json.JSONDecodeError as e:
        raise ConfigError(f"{Path(path).name}: not valid JSON ({e})") from None


def set_path(cfg, dotted, value):
    """Sets cfg['a']['b']['c'] from 'a.b.c' (used by experiment grids)."""
    cur = cfg
    parts = dotted.split(".")
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value
    return cfg
