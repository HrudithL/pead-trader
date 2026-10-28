"""Outcome firewall (REF-outcome-firewall): columns that describe what happened AFTER entry.

`equity_vol_panel.parquet` carries realised outcomes (hold_ret, exit_*, ...) next to the ex-ante features, and every
`EquityStrategy.weights()` receives that frame. A strategy that reads an outcome column can leak the future, so
`firewall` hides them from `weights()` unless the strategy lists them in `outcome_columns_allowed` (and then it
must guard its own use; see tests/test_lookahead.py LA-02).
"""
import pandas as pd

OUTCOME_COLUMNS = ("hold_ret", "ret_fwd_60d_mktadj", "exit_date", "exit_row_idx", "holding_days", "holding_cell")


def firewall(events: pd.DataFrame, allowed=()) -> pd.DataFrame:
    """Return `events` without the outcome columns the strategy is not allowed to read (a new frame; input untouched)."""
    hidden = [c for c in OUTCOME_COLUMNS if c in events.columns and c not in allowed]
    return events.drop(columns=hidden) if hidden else events
