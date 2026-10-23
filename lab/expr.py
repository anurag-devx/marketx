"""The Strategy Lab rule language: plain formulas evaluated for every stock on every day.

    close > sma(200) and rsi(14) < 30
    crosses_above(close, prev(highest(high, 55))) and volume > 2 * sma(volume, 20)
    rank(ret(126)) >= 90 and nifty > sma(nifty, 200)

Everything at (stock, day t) uses data up to the close of day t only. Formulas are parsed and checked
against a fixed list of names and functions (no other code can run). See HELP for the full list."""
import ast, re
import numpy as np, pandas as pd

# ---------------- reference (also shown in the app) ----------------
HELP = {
    "Prices (each stock, today)": [
        ("open, high, low, close, volume", "today's bar"),
        ("turnover", "today's traded value, ₹ crore"),
        ("sector", 'the stock\'s sector, e.g. sector == "Information Technology"'),
    ],
    "Averages and levels": [
        ("sma(n) · sma(x, n)", "simple average of close (or of x) over n days, e.g. sma(200), sma(volume, 20)"),
        ("ema(n) · ema(x, n)", "exponential average"),
        ("highest(x, n) · lowest(x, n)", "highest / lowest value of x over the last n days, today included"),
        ("prev(x) · prev(x, n)", "x one (or n) days ago. Use prev(highest(high, 55)) for 'the 55-day high before today'"),
        ("high52() · low52()", "52-week high / low, today included"),
        ("bb_upper(n, k) · bb_lower(n, k) · bb_width(n, k)", "Bollinger bands (default 20, 2); width in % of the average"),
    ],
    "Momentum and oscillators": [
        ("ret(n)", "% return of close over n days, e.g. ret(126) = 6 months"),
        ("change(x, n)", "% change of any value over n days"),
        ("rsi(n) · rsi(x, n)", "RSI (default 14)"),
        ("macd() · macd_signal() · macd_hist()", "MACD 12/26/9 on close"),
        ("rs(n)", "ret(n) minus the Nifty's ret(n): strength vs the market, in % points"),
        ("gap()", "% gap of today's open vs yesterday's close"),
    ],
    "Volatility and volume": [
        ("atr(n)", "average true range in ₹ (default 14)"),
        ("atr_pct(n)", "ATR as % of close"),
        ("stdev(x, n)", "standard deviation of x over n days"),
        ("vol_ratio(n)", "today's volume ÷ average volume of the previous n days (default 20)"),
    ],
    "Compare stocks with each other (same day)": [
        ("rank(x)", "percentile 0-100 of x among all stocks in the universe that day; rank(ret(126)) >= 90 = top 10%"),
        ("sector_rank(x)", "percentile 0-100 within the stock's sector"),
        ("sector_avg(x)", "average of x across the stock's sector that day"),
    ],
    "Events and counting": [
        ("crosses_above(a, b) · crosses_below(a, b)", "a moved from below b yesterday to above b today (or the reverse)"),
        ("count(condition, n)", "on how many of the last n days the condition was true"),
        ("bars_since(condition)", "days since the condition was last true (0 = today)"),
        ("between(x, lo, hi)", "lo <= x <= hi"),
        ("abs(x) · min(a, b) · max(a, b) · log(x)", "maths"),
    ],
    "Market (same value for every stock that day)": [
        ("nifty · vix", "Nifty 50 close and India VIX"),
        ("breadth", "% of stocks above their 200-day average"),
        ("market_state", '"up_calm", "up_volatile", "down_calm" or "down_volatile" (the regime agent\'s state)'),
        ("sma(nifty, 200) · ret(nifty, 63) …", "any function also works on nifty, vix or breadth"),
    ],
    "Combine": [
        ("and · or · not · ( )", "AND / OR / NOT in capitals also work"),
        ("> < >= <= == !=", "comparisons; = is read as =="),
        ("+ - * /", "arithmetic"),
    ],
}

PRICE_NAMES = {"open", "high", "low", "close", "volume", "turnover", "sector", "symbol"}
MARKET_NAMES = {"nifty", "vix", "breadth", "market_state"}
FUNCS = {"sma", "avg", "ema", "rsi", "atr", "atr_pct", "stdev", "highest", "lowest", "prev", "ret", "change", "rs",
         "gap", "vol_ratio", "high52", "low52", "bb_upper", "bb_lower", "bb_width", "macd", "macd_signal", "macd_hist",
         "rank", "sector_rank", "sector_avg", "crosses_above", "crosses_below", "count", "bars_since", "between",
         "abs", "min", "max", "log", "sum"}
STOCK_ONLY_FUNCS = {"atr", "atr_pct", "gap", "vol_ratio", "high52", "low52", "rank", "sector_rank", "sector_avg", "rs"}


class RuleError(ValueError):
    pass


def normalize(text):
    t = (text or "").strip()
    t = re.sub(r"\bAND\b", "and", t)
    t = re.sub(r"\bOR\b", "or", t)
    t = re.sub(r"\bNOT\b", "not", t)
    t = re.sub(r"(?<![<>=!])=(?!=)", "==", t)          # a single = means equals
    t = t.replace("≥", ">=").replace("≤", "<=").replace("\n", " ")
    return t


def parse(text):
    t = normalize(text)
    if not t:
        raise RuleError("empty rule")
    try:
        tree = ast.parse(t, mode="eval")
    except SyntaxError as e:
        raise RuleError(f"can't read this formula near character {e.offset}: {t}") from None
    _check(tree.body)
    return tree.body


def names_used(text):
    node = parse(text)
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Call):
            out.add(n.func.id + "()")
    return out


def _check(n):
    ok = (ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd, ast.BinOp, ast.Add, ast.Sub,
          ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.Compare, ast.Gt, ast.Lt, ast.GtE, ast.LtE, ast.Eq, ast.NotEq,
          ast.Call, ast.Name, ast.Constant, ast.Load)
    func_nodes = {id(x.func) for x in ast.walk(n) if isinstance(x, ast.Call)}
    for x in ast.walk(n):
        if id(x) in func_nodes:
            continue
        if not isinstance(x, ok):
            raise RuleError(f"'{type(x).__name__}' is not allowed in a rule")
        if isinstance(x, ast.Call):
            if not isinstance(x.func, ast.Name) or x.func.id not in FUNCS:
                nm = x.func.id if isinstance(x.func, ast.Name) else "?"
                raise RuleError(f"unknown function '{nm}()'. See the rule reference.")
            if x.keywords:
                raise RuleError(f"{x.func.id}(): give values in order, without names")
        if isinstance(x, ast.Name) and x.id not in PRICE_NAMES | MARKET_NAMES | {"true", "false", "True", "False"}:
            raise RuleError(f"unknown name '{x.id}'. See the rule reference.")
        if isinstance(x, ast.Constant) and not isinstance(x.value, (int, float, str, bool)):
            raise RuleError("only numbers and quoted text are allowed as values")


def market_only(text):
    """True if a formula uses only market values (for market filters and rotation regime rules)."""
    node = parse(text)
    for x in ast.walk(node):
        if isinstance(x, ast.Name) and x.id in PRICE_NAMES:
            return False
        if isinstance(x, ast.Call) and x.func.id in STOCK_ONLY_FUNCS:
            return False
    return True


class Panel:
    """A long table (one row per symbol and date, sorted by symbol then date) that formulas run on.
    market: one row per date with nifty, vix, breadth, market_state (broadcast to every stock)."""

    def __init__(self, df, market=None, rank_mask=None):
        self.df = df.reset_index(drop=True)
        self.n = len(self.df)
        sym = self.df.symbol.to_numpy()
        self.start = np.r_[True, sym[1:] != sym[:-1]]
        self.pos = pd.Series(np.arange(self.n) - np.maximum.accumulate(np.where(self.start, np.arange(self.n), 0)))
        self.market = market
        if market is not None:
            m = market.set_index("date")
            self._mi = m.index.get_indexer(self.df.date)
            self._m = m
        self.rank_mask = rank_mask          # rows that count for rank() (the universe); None = all
        self.default_x = "close"
        self.cache = {}

    # ----- helpers -----
    def col(self, name):
        if name in self.cache:
            return self.cache[name]
        if name in self.df:
            s = self.df[name]
        elif name == "turnover":
            s = self.df.close * self.df.volume / 1e7
        elif name in MARKET_NAMES and self.market is not None:
            v = self._m[name].to_numpy()
            s = pd.Series(np.where(self._mi >= 0, v[np.clip(self._mi, 0, None)], np.nan if v.dtype != object else None))
        else:
            raise RuleError(f"'{name}' is not available here")
        self.cache[name] = s
        return s

    def _valid(self, s, n):
        return s.where(self.pos >= n - 1)

    def roll(self, s, n, how):
        n = int(n)
        r = getattr(s.rolling(n, min_periods=n), how)()
        return self._valid(r, n)

    def shift(self, s, n=1):
        n = int(n)
        return s.shift(n).where(self.pos >= n)

    def grouped(self, s, fn):
        key = np.cumsum(self.start)
        return s.groupby(key).transform(fn)

    def by_date(self, s, how, by_sector=False):
        keys = [self.df.date] + ([self.df.sector] if by_sector else [])
        if self.rank_mask is not None:
            s = s.where(self.rank_mask)
        g = s.groupby(keys)
        return g.rank(pct=True) * 100 if how == "rank" else g.transform("mean")

    # ----- evaluation -----
    def eval(self, text):
        node = parse(text)
        v = self._ev(node)
        if not isinstance(v, pd.Series):
            v = pd.Series(np.full(self.n, v))
        return v

    def _ev(self, n):
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            if n.id in ("true", "True"):
                return True
            if n.id in ("false", "False"):
                return False
            return self.col(n.id)
        if isinstance(n, ast.BoolOp):
            vals = [_bool(self._ev(v)) for v in n.values]
            out = vals[0]
            for v in vals[1:]:
                out = (out & v) if isinstance(n.op, ast.And) else (out | v)
            return out
        if isinstance(n, ast.UnaryOp):
            v = self._ev(n.operand)
            if isinstance(n.op, ast.Not):
                return ~_bool(v)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp):
            a, b = self._ev(n.left), self._ev(n.right)
            op = type(n.op)
            if op is ast.Add: return a + b
            if op is ast.Sub: return a - b
            if op is ast.Mult: return a * b
            if op is ast.Div:
                return a / (b.replace(0, np.nan) if isinstance(b, pd.Series) else (np.nan if b == 0 else b))
            if op is ast.Pow: return a ** b
            if op is ast.Mod: return a % b
        if isinstance(n, ast.Compare):
            out, left = None, self._ev(n.left)
            for op, comp in zip(n.ops, n.comparators):
                right = self._ev(comp)
                t = type(op)
                r = {ast.Gt: lambda: left > right, ast.Lt: lambda: left < right, ast.GtE: lambda: left >= right,
                     ast.LtE: lambda: left <= right, ast.Eq: lambda: left == right, ast.NotEq: lambda: left != right}[t]()
                r = _bool(r)
                out = r if out is None else out & r
                left = right
            return out
        if isinstance(n, ast.Call):
            key = ast.dump(n)
            if key in self.cache:
                return self.cache[key]
            v = self._call(n.func.id, n.args)
            self.cache[key] = v
            return v
        raise RuleError("unsupported formula")

    def _xn(self, args, default_n, name):
        """(series, n, rest) from f(n) or f(x, n): x defaults to close."""
        vals = [self._ev(a) for a in args]
        if vals and isinstance(vals[0], pd.Series):
            x, rest = vals[0], vals[1:]
        else:
            x, rest = self.col(self.default_x), vals
        n = rest[0] if rest else default_n
        if n is None:
            raise RuleError(f"{name}() needs a number of days, e.g. {name}(20)")
        if not isinstance(n, (int, float)) or n < 1 or n > 2000:
            raise RuleError(f"{name}(): the number of days must be between 1 and 2000")
        return x, int(n), rest[1:]

    def _call(self, f, args):
        c = self.col
        if f in ("sma", "avg"):
            x, n, _ = self._xn(args, None, f); return self.roll(x, n, "mean")
        if f == "sum":
            x, n, _ = self._xn(args, None, f); return self.roll(x.astype(float), n, "sum")
        if f == "stdev":
            x, n, _ = self._xn(args, None, f); return self.roll(x, n, "std")
        if f == "highest":
            x, n, _ = self._xn(args, None, f); return self.roll(x, n, "max")
        if f == "lowest":
            x, n, _ = self._xn(args, None, f); return self.roll(x, n, "min")
        if f == "ema":
            x, n, _ = self._xn(args, None, f)
            return self._valid(self.grouped(x, lambda s: s.ewm(span=n, adjust=False, min_periods=n).mean()), n)
        if f == "rsi":
            x, n, _ = self._xn(args, 14, f)
            d = self.shift(x, 0) - self.shift(x, 1)
            up = self.grouped(d.clip(lower=0), lambda s: s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean())
            dn = self.grouped((-d).clip(lower=0), lambda s: s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean())
            return self._valid(100 - 100 / (1 + up / dn.replace(0, np.nan)), n + 1)
        if f == "prev":
            vals = [self._ev(a) for a in args]
            if not vals or not isinstance(vals[0], pd.Series):
                raise RuleError("prev(): give a value, e.g. prev(close) or prev(high, 5)")
            return self.shift(vals[0], int(vals[1]) if len(vals) > 1 else 1)
        if f in ("ret", "change"):
            x, n, _ = self._xn(args, None, f)
            return 100 * (x / self.shift(x, n).replace(0, np.nan) - 1)
        if f == "rs":
            _, n, _ = self._xn(args, None, f)
            cl, nf = c("close"), c("nifty")
            return 100 * (cl / self.shift(cl, n) - 1) - 100 * (nf / self.shift(nf, n) - 1)
        if f in ("atr", "atr_pct"):
            n = int(self._ev(args[0])) if args else 14
            h, l, cl = c("high"), c("low"), c("close")
            pc = self.shift(cl, 1)
            tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
            a = self.roll(tr, n, "mean")
            return a if f == "atr" else 100 * a / cl
        if f == "gap":
            return 100 * (c("open") / self.shift(c("close"), 1) - 1)
        if f == "vol_ratio":
            n = int(self._ev(args[0])) if args else 20
            return c("volume") / self.shift(self.roll(c("volume"), n, "mean"), 1).replace(0, np.nan)
        if f == "high52":
            return self.roll(c("high"), 252, "max")
        if f == "low52":
            return self.roll(c("low"), 252, "min")
        if f in ("bb_upper", "bb_lower", "bb_width"):
            vals = [self._ev(a) for a in args]
            n, k = (int(vals[0]) if vals else 20), (float(vals[1]) if len(vals) > 1 else 2.0)
            m, sd = self.roll(c("close"), n, "mean"), self.roll(c("close"), n, "std")
            return m + k * sd if f == "bb_upper" else (m - k * sd if f == "bb_lower" else 100 * 2 * k * sd / m)
        if f in ("macd", "macd_signal", "macd_hist"):
            cl = c("close")
            e = lambda s, n: self.grouped(s, lambda x: x.ewm(span=n, adjust=False, min_periods=n).mean())
            mac = e(cl, 12) - e(cl, 26)
            sig = e(mac, 9)
            return self._valid(mac if f == "macd" else (sig if f == "macd_signal" else mac - sig), 35)
        if f in ("rank", "sector_rank", "sector_avg"):
            if len(args) != 1:
                raise RuleError(f"{f}() takes one value, e.g. {f}(ret(126))")
            x = self._ev(args[0])
            if f == "sector_avg":
                return self.by_date(x, "mean", by_sector=True)
            return self.by_date(x, "rank", by_sector=(f == "sector_rank"))
        if f in ("crosses_above", "crosses_below"):
            if len(args) != 2:
                raise RuleError(f"{f}() takes two values, e.g. {f}(close, sma(50))")
            a, b = self._ev(args[0]), self._ev(args[1])
            a = a if isinstance(a, pd.Series) else pd.Series(np.full(self.n, a))
            b = b if isinstance(b, pd.Series) else pd.Series(np.full(self.n, b))
            pa, pb = self.shift(a, 1), self.shift(b, 1)
            return ((a > b) & (pa <= pb)) if f == "crosses_above" else ((a < b) & (pa >= pb))
        if f == "count":
            if len(args) != 2:
                raise RuleError("count() takes a condition and a number of days, e.g. count(close > prev(close), 5)")
            cond, n = _bool(self._ev(args[0])).astype(float), int(self._ev(args[1]))
            return self.roll(cond, n, "sum")
        if f == "bars_since":
            cond = _bool(self._ev(args[0])).to_numpy()
            out = np.full(self.n, np.nan)
            last = -1
            for i in range(self.n):
                if self.start[i]:
                    last = -1
                if cond[i]:
                    last = i
                if last >= 0:
                    out[i] = i - last
            return pd.Series(out)
        if f == "between":
            x, lo, hi = (self._ev(a) for a in args)
            return _bool((x >= lo) & (x <= hi))
        if f == "abs":
            return abs(self._ev(args[0]))
        if f in ("min", "max"):
            a, b = self._ev(args[0]), self._ev(args[1])
            fn = np.minimum if f == "min" else np.maximum
            return pd.Series(fn(a, b)) if isinstance(a, pd.Series) or isinstance(b, pd.Series) else fn(a, b)
        if f == "log":
            v = self._ev(args[0])
            return np.log(v.where(v > 0)) if isinstance(v, pd.Series) else np.log(v)
        raise RuleError(f"unknown function {f}()")


def _bool(v):
    if isinstance(v, pd.Series):
        if v.dtype == bool:
            return v
        return v.fillna(False).astype(bool) if v.dtype == object else (v.fillna(0) != 0)
    return bool(v)


def market_panel(market):
    """A one-row-per-date panel for market-only formulas (nifty, vix, breadth, market_state)."""
    m = market.sort_values("date").reset_index(drop=True).assign(symbol="MARKET")
    p = Panel(m[["date", "symbol"]], market=market)
    p.default_x = "nifty"
    return p
