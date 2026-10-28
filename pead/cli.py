"""Command line entry point:  python -m pead <command>

  list                          every registered strategy with its family and status
  run <sel...> [--jobs N]       backtest strategies (names, 'all', 'equity', 'options', 'family:x', 'prefix*')
  report <name...>              (re)build the per-strategy report from a saved run
  eval <name...>                search/holdout evaluation of a saved run (writes evaluation.json)
  leaderboard2                  leaderboard with bootstrap CIs, holdout split and DSR (results/leaderboard_v2.md)
  trials [--family f]           count of logged trials (deflated-Sharpe denominator)
  compare [<name...>]           comprehensive cross-strategy report (correlations, combinations, attribution)
  portfolio                     optimal equity + options hedged blend
  paper [--no-pdf]              rebuild the research paper (numbers, tables, figures, PDF) from results/
  prep <stage...> | prep --list build the data inputs the backtests read (WRDS / OptionMetrics stages)
  live <command>                paper-trading engine
"""
import argparse
import sys


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "paper":
        from pead.reporting.paper import main as paper_main
        return paper_main(argv[1:])
    if argv and argv[0] in ("prep", "live"):          # these own their argument parsing (flags pass straight through)
        if argv[0] == "prep":
            from pead.prep.stages import main as prep_main
            return prep_main(argv[1:])
        from pead.live.cli import main as live_main
        return live_main(argv[1:])
    ap = argparse.ArgumentParser(prog="python -m pead", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    p = sub.add_parser("run")
    p.add_argument("selectors", nargs="+")
    p.add_argument("--jobs", type=int, default=None, help="max worker processes (default: planned from cores/RAM)")
    p.add_argument("--no-report", action="store_true", help="skip the per-strategy report")

    p = sub.add_parser("report")
    p.add_argument("names", nargs="+")

    p = sub.add_parser("eval")
    p.add_argument("names", nargs="+")

    sub.add_parser("leaderboard2")

    p = sub.add_parser("trials")
    p.add_argument("--family", default=None)

    p = sub.add_parser("compare")
    p.add_argument("names", nargs="*")

    p = sub.add_parser("portfolio")
    p.add_argument("--equity", default=None)
    p.add_argument("--options", default=None)

    p = sub.add_parser("prep", add_help=False)
    p.add_argument("rest", nargs=argparse.REMAINDER)

    p = sub.add_parser("live", add_help=False)
    p.add_argument("rest", nargs=argparse.REMAINDER)

    args = ap.parse_args(argv)

    if args.cmd == "list":
        from pead.strategies import registry
        rows = sorted(registry.all_strategies().values(), key=lambda s: (s.asset_class, s.family, s.name))
        print(f"{'name':36s} {'class':8s} {'family':20s} {'status':10s} live  description")
        for s in rows:
            print(f"{s.name:36s} {s.asset_class:8s} {s.family:20s} {s.status:10s} {'yes ' if s.live_ready else 'no  '}  {s.description}")
    elif args.cmd == "run":
        from pead.runner import run_strategies
        run_strategies(args.selectors, jobs=args.jobs, report=not args.no_report)
    elif args.cmd == "report":
        from pead.core.store import load_result
        from pead.reporting.single import build_strategy_report
        from pead.strategies import registry
        for n in args.names:
            r = load_result(n)
            try:   # descriptive text comes from the CURRENT strategy source, so edited docstrings reach the report
                fresh = registry.get(n).meta()
                r.meta.update({k: fresh[k] for k in ("module_doc", "description", "thesis", "status") if k in fresh})
            except KeyError:
                pass
            print("wrote", build_strategy_report(r))
    elif args.cmd == "eval":
        import json
        from pead.core.data import load_equity_universe
        from pead.eval.harness import evaluate, write_evaluation
        from pead.strategies import registry
        q = load_equity_universe().events["ann_quarter"]
        for n in args.names:
            ev = evaluate(n, q, family=registry.get(n).family)
            write_evaluation(ev)
            print(json.dumps(ev, indent=2, default=float))
    elif args.cmd == "leaderboard2":
        from pead.core.data import load_equity_universe
        from pead.eval import leaderboard
        df = leaderboard.build(load_equity_universe().events["ann_quarter"])
        leaderboard.write(df)
        print(f"wrote results/leaderboard_v2.md ({len(df)} runs)")
    elif args.cmd == "trials":
        from pead.eval import registry as trials
        print(f"{trials.n_trials(args.family)} trials" + (f" in family {args.family}" if args.family else ""))
    elif args.cmd == "compare":
        from pead.reporting.comprehensive import build_comprehensive_report
        print("wrote", build_comprehensive_report(args.names or None))
    elif args.cmd == "portfolio":
        from pead.portfolio.combine import run_portfolio_study
        run_portfolio_study(equity=args.equity, options=args.options)
    elif args.cmd == "prep":
        from pead.prep.stages import main as prep_main
        prep_main(args.rest)
    elif args.cmd == "live":
        from pead.live.cli import main as live_main
        live_main(args.rest)


if __name__ == "__main__":
    main()
