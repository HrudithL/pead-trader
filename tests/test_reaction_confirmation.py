import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.engines.equity import Sizing, run_equity_backtest, with_entry_lag, with_fixed_hold  # noqa: E402
from tests.pit_kit import jump, make_universe  # noqa: E402

N = 120
KW = dict(base_unit_fraction=0.5, max_gross_leverage=2.5, liquidity_cap_frac=1e9, cap_mode="chronological")


def test_entry_lag_moves_entry_and_dates_and_never_crosses_permno():
    uni = make_universe([dict(day0=10, exit=40), dict(day0=N - 1, exit=N - 1)], [np.zeros(N), np.zeros(N)])
    lagged = with_entry_lag(uni, 2)
    assert len(lagged.events) == 1                                      # the event at the last row of its run is dropped
    e = lagged.events.iloc[0]
    assert e["entry_row_idx"] == 12 and e["day0_date"] == uni.calendar[12] and e["day0_date_orig"] == uni.calendar[10]
    assert with_entry_lag(uni, 0) is uni


def test_lag_forfeits_the_skipped_return_and_books_costs_on_the_lagged_date():
    path = jump(N, 11, 0.05)                                            # earned only by entering at day0
    uni = make_universe([dict(day0=10, exit=40)], [path])
    base = run_equity_backtest(with_fixed_hold(uni, 30), np.ones(1), Sizing(**KW))
    lag = run_equity_backtest(with_fixed_hold(with_entry_lag(uni, 1), 30), np.ones(1), Sizing(**KW))
    assert base.daily["nav"].iloc[-1] - lag.daily["nav"].iloc[-1] == pytest.approx(0.5e6 * 0.05, rel=1e-3)
    assert lag.daily["turnover_dollars"].to_numpy().nonzero()[0][0] == 11  # entry booked on the lagged day
