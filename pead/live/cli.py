"""python -m pead live <command>

  run [--dry-run] [--mock-data] [--limit-universe N] [--seed S]   one daily cycle
  status                                                           NAV history + open positions
"""
import argparse

from . import config, ledger


def main(argv):
    ap = argparse.ArgumentParser(prog="python -m pead live", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true", help="full pipeline, no state written, no orders sent")
    r.add_argument("--mock-data", action="store_true", help="synthetic data, no network")
    r.add_argument("--limit-universe", type=int, default=None)
    r.add_argument("--seed", type=int, default=None)
    sub.add_parser("status")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        from .engine import LiveEngine
        LiveEngine(dry_run=a.dry_run, mock=a.mock_data, seed=a.seed, limit_universe=a.limit_universe).run()
    else:
        nav = ledger.load_nav_history()
        eq, op = ledger.load_equity_positions(), ledger.load_options_positions()
        print(f"equity strategy {config.EQUITY_STRATEGY}, options strategy {config.OPTIONS_STRATEGY}")
        print(f"NAV rows: {len(nav)}; latest: {nav[-1] if nav else 'none yet'}")
        print(f"open equity positions: {sum(p['status'] == 'open' for p in eq.values())}; "
              f"open options positions: {sum(p['status'] == 'open' for p in op.values())}")
