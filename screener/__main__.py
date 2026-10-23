"""Screener command line.
    python3 -m screener screen [--date YYYY-MM-DD] [--strategy NAME] [--all | --top N]   research shortlist -> CSV
    python3 -m screener experiments [NAME ...] [--force]                                run experiments (skips finished ones)
    python3 -m screener compare [--horizon H]                                           comparison table -> CSV
    python3 -m screener strategies                                                      strategies and their status
    python3 -m screener enable NAME@vN | disable NAME@vN                                live screener selection
"""
import argparse, sys
import pandas as pd
from . import config as SC, screen as S, experiments as X


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m screener")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("screen"); a.add_argument("--date"); a.add_argument("--strategy"); a.add_argument("--all", action="store_true")
    a.add_argument("--top", type=int)
    e = sub.add_parser("experiments"); e.add_argument("names", nargs="*"); e.add_argument("--force", action="store_true")
    c = sub.add_parser("compare"); c.add_argument("--horizon", type=int)
    sub.add_parser("strategies")
    for n in ("enable", "disable"):
        p = sub.add_parser(n); p.add_argument("key")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "screen":
            cfg = {}
            if args.strategy:
                st = {k: v for k, v in X.load_strategies().items() if k == args.strategy or v["name"] == args.strategy}
                if not st:
                    raise SC.ConfigError(f"unknown strategy {args.strategy}")
                cfg = sorted(st.values(), key=lambda s: s["version"])[-1]["config"]
            if args.all:
                cfg = SC.merge(cfg, {"mode": "all"})
            if args.top:
                cfg = SC.merge(cfg, {"mode": "top_n", "top_n": args.top})
            res = S.screen(args.date, cfg)
            path = S.export(res)
            print(f"Research shortlist for {res['meta']['screen_date']} (config {res['meta']['config_id']}): {len(res['shortlist'])} stocks")
            for k, v in res["diagnostics"].items():
                print(f"  {k}: {v}")
            print(S.table(res)[["symbol", "rank", "setups_passed", "qualification_reasons"]].head(20).to_string(index=False) if len(res["shortlist"])
                  else "  No stock qualified. Filters were not relaxed; see the counts above.")
            print(f"Saved {path}")
        elif args.cmd == "experiments":
            out = X.run(args.names or None, force=args.force)
            failed = [o for o in out if o["status"] == "failed"]
            print(f"{len(out)} configurations: {sum(o['status'] == 'ok' for o in out)} run, "
                  f"{sum(o['status'] == 'skipped' for o in out)} skipped, {len(failed)} failed. "
                  f"{X.configs_tested()} distinct configurations tested so far.")
            return 1 if failed else 0
        elif args.cmd == "compare":
            t = X.comparison(args.horizon)
            if t.empty:
                print("No experiments yet."); return 0
            SC.OUT_DIR.mkdir(parents=True, exist_ok=True)
            p = SC.OUT_DIR / "comparison.csv"
            t.to_csv(p, index=False)
            cols = [c for c in ["experiment", "strategy", "params", "horizon", "dev_signals", "dev_lift_pp", "dev_excess_%", "dev_t",
                                "val_excess_%", "val_t", "validated", "status"] if c in t]
            print(t[cols].round(2).to_string(index=False))
            print(f"Saved {p}. Signal statistics, not portfolio returns. Holdout not shown.")
        elif args.cmd == "strategies":
            for k, s in X.load_strategies().items():
                print(f"{k:32s} {X.status_of(k):12s} {s['description']}")
        elif args.cmd in ("enable", "disable"):
            X.set_enabled(args.key, args.cmd == "enable")
            print(f"{args.key} {args.cmd}d for the live screener.")
    except SC.ConfigError as e:
        print(f"Configuration error: {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
