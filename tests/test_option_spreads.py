"""OPT-01 spread diagnostics: synthetic chain, known answers."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.reporting.option_spreads import pick_contracts, rel_spread, spread_report  # noqa: E402


def _chain():
    d = pd.Timestamp("2005-01-10").date()
    ex = pd.Timestamp("2005-02-18").date()          # 39 DTE
    rows = [  # cp, bid, ask, delta
        ("C", 4.0, 4.4, 0.52), ("C", 3.0, 3.1, 0.30), ("C", 9.0, 9.2, 0.82), ("C", 0.0, 0.5, 0.50), ("P", 2.0, 2.2, -0.48)]
    return pd.DataFrame([dict(secid=1.0, date=d, exdate=ex, cp_flag=c, best_bid=b, best_offer=a, delta=dl,
                              open_interest=100, optionid=i) for i, (c, b, a, dl) in enumerate(rows)])


def test_rel_spread():
    assert rel_spread(4.0, 4.4) == pytest.approx(0.4 / 4.2)


def test_pick_nearest_delta_requires_live_bid_and_report_math():
    chain = _chain()
    spot = pd.DataFrame([dict(secid=1.0, date=chain["date"].iloc[0], underlying_price=100.0)])
    p = pick_contracts(chain, spot)
    atm = p[p["bucket"] == "call_atm_30_60d"].iloc[0]
    assert atm["best_bid"] == 4.0                       # the zero-bid 0.50-delta call is excluded
    rep = spread_report(p, n_events=1).set_index("bucket")
    assert rep.loc["call_atm_30_60d", "breakeven_move_median"] == pytest.approx(0.4 / (0.52 * 100.0))
    assert rep.loc["call_atm_30_60d", "coverage"] == 1.0
    assert "call_itm80_30_90d" in rep.index
