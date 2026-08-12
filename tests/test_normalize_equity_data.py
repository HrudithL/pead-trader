"""Tests for scripts/normalize_equity_data.py -- cleaning/normalizing the
raw CRSP pull (data/raw_wrds/crsp/) into data/normalized_equity/, plus the
master_stock.csv cross-validation (Phase I, Slice 2).

Unit tests exercise each cleaning/analysis function directly against small
synthetic DataFrames (no dependency on the real, multi-GB raw files). One
end-to-end test drives main() against a fully faked data/ tree under
tmp_path (small fixture Parquet/CSV files, every module-level path constant
monkeypatched), matching this repo's existing "test main() against fakes,
not just the pieces" convention (see test_pull_wrds_crsp.py's docstring).
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import normalize_equity_data as neq  # noqa: E402

lib = neq.lib


# ---------------------------------------------------------------------------
# discover_dsf_years
# ---------------------------------------------------------------------------


def test_discover_dsf_years_sorts_and_filters(tmp_path):
    for name in ["dsf_1996.parquet", "dsf_2005.parquet", "dsf_1995.parquet", "dsi.parquet", "dsedelist.parquet"]:
        (tmp_path / name).touch()
    assert neq.discover_dsf_years(tmp_path) == [1995, 1996, 2005]


def test_discover_dsf_years_empty_dir(tmp_path):
    assert neq.discover_dsf_years(tmp_path) == []


# ---------------------------------------------------------------------------
# parse_iso_date_column
# ---------------------------------------------------------------------------


def test_parse_iso_date_column_parses_valid_dates():
    out = neq.parse_iso_date_column(pd.Series(["2010-01-04", "2010-01-05"]))
    assert out.dtype.kind == "M"
    assert out.iloc[0] == pd.Timestamp("2010-01-04")


def test_parse_iso_date_column_raises_on_bad_format():
    with pytest.raises(ValueError):
        neq.parse_iso_date_column(pd.Series(["2010-01-04", "01/04/2010"]))


# ---------------------------------------------------------------------------
# clean_negative_prices
# ---------------------------------------------------------------------------


def test_clean_negative_prices_takes_abs_and_flags():
    prc = pd.Series([10.0, -11.0, np.nan, -0.0])
    cleaned, flag = neq.clean_negative_prices(prc)
    assert cleaned.tolist()[:2] == [10.0, 11.0]
    assert flag.tolist() == [False, True, False, False]
    assert pd.isna(cleaned.iloc[2])


# ---------------------------------------------------------------------------
# map_missing_return_codes
# ---------------------------------------------------------------------------


def test_map_missing_return_codes_replaces_only_documented_sentinels():
    ret = pd.Series([0.01, -66.0, -77.0, -88.0, -99.0, -0.5, np.nan])
    cleaned, n = neq.map_missing_return_codes(ret)
    assert n == 4
    assert cleaned.iloc[0] == 0.01
    assert cleaned.iloc[5] == -0.5  # a real, large-but-legitimate loss is untouched
    assert pd.isna(cleaned.iloc[1]) and pd.isna(cleaned.iloc[2])
    assert pd.isna(cleaned.iloc[3]) and pd.isna(cleaned.iloc[4])
    assert pd.isna(cleaned.iloc[6])  # pre-existing null stays null


# ---------------------------------------------------------------------------
# point-in-time ticker attachment
# ---------------------------------------------------------------------------


def _stocknames_fixture():
    return pd.DataFrame({
        "permno": [1, 1, 2],
        "namedt": pd.to_datetime(["2000-01-01", "2008-01-03", "1990-01-01"]),
        "nameenddt": pd.to_datetime(["2008-01-02", "2020-01-01", "2020-01-01"]),
        "ticker": ["OLDT", "NEWT", "BBB"],
    })


def test_attach_point_in_time_ticker_resolves_rename_correctly():
    sn = _stocknames_fixture()
    df = pd.DataFrame({
        "permno": [1, 1, 1, 2],
        "date": pd.to_datetime(["2007-12-31", "2008-01-03", "2019-01-01", "1999-01-01"]),
    })
    ticker = neq.attach_point_in_time_ticker(df, sn)
    assert ticker.tolist() == ["OLDT", "NEWT", "NEWT", "BBB"]


def test_attach_point_in_time_ticker_null_outside_recorded_history():
    sn = _stocknames_fixture()
    df = pd.DataFrame({"permno": [1], "date": pd.to_datetime(["1990-01-01"])})  # before permno 1's first segment
    ticker = neq.attach_point_in_time_ticker(df, sn)
    assert pd.isna(ticker.iloc[0])


def test_attach_point_in_time_ticker_preserves_original_row_order_and_index():
    sn = _stocknames_fixture()
    df = pd.DataFrame(
        {"permno": [2, 1, 1], "date": pd.to_datetime(["1999-01-01", "2019-01-01", "2007-12-31"])},
        index=[10, 20, 30],
    )
    ticker = neq.attach_point_in_time_ticker(df, sn)
    assert list(ticker.index) == [10, 20, 30]
    assert ticker.tolist() == ["BBB", "NEWT", "OLDT"]


def test_attach_point_in_time_ticker_handles_named_index():
    # Regression test: a naive reset_index(drop=False) names the new column
    # after df.index.name, so a caller passing a named index used to make
    # the internal .set_index("index") raise
    # KeyError: "None of ['index'] are in the columns".
    sn = _stocknames_fixture()
    df = pd.DataFrame(
        {"permno": [1, 2], "date": pd.to_datetime(["2010-01-01", "2010-01-01"])},
        index=pd.Index([100, 200], name="row_id"),
    )
    ticker = neq.attach_point_in_time_ticker(df, sn)
    assert ticker.index.name == "row_id"
    assert list(ticker.index) == [100, 200]
    assert ticker.tolist() == ["NEWT", "BBB"]


def test_attach_point_in_time_ticker_null_ticker_segment_passes_through():
    # stocknames itself sometimes has a segment with a null ticker (e.g. a
    # company mid-bankruptcy) -- distinct from "no segment at all" (tested
    # above): here a segment exists and covers the date, but its own
    # ticker value is null, which must come through as null, not crash or
    # get silently coerced to some placeholder.
    sn = pd.DataFrame({
        "permno": [1],
        "namedt": pd.to_datetime(["2000-01-01"]),
        "nameenddt": pd.to_datetime(["2020-01-01"]),
        "ticker": pd.array([None], dtype="string"),
    })
    df = pd.DataFrame({"permno": [1], "date": pd.to_datetime(["2010-01-01"])})
    ticker = neq.attach_point_in_time_ticker(df, sn)
    assert pd.isna(ticker.iloc[0])


# ---------------------------------------------------------------------------
# clean_dsf_year (the main per-year cleaning integration function)
# ---------------------------------------------------------------------------


def _raw_dsf_fixture():
    return pd.DataFrame({
        "permno": [1, 1, 2, 2, 3],
        "date": ["2010-01-04", "2010-01-05", "2010-01-04", "2010-01-05", "2010-01-04"],
        "ret": [0.01, -66.0, 0.02, -0.03, 0.05],
        "retx": [0.01, -0.02, 0.02, -0.03, 0.05],
        "prc": [10.0, -11.0, 20.0, None, 5.0],
        "shrout": [100.0, 100.0, 200.0, 200.0, 50.0],
        "cfacpr": [1.0, 1.0, 0.0, 0.0, 1.0],
        "cfacshr": [1.0, 1.0, 1.0, 1.0, 1.0],
        "vol": [1000.0, 2000.0, 3000.0, 4000.0, 500.0],
    })


def _stocknames_for_dsf_fixture():
    return pd.DataFrame({
        "permno": [1, 2],
        "namedt": pd.to_datetime(["2000-01-01", "2000-01-01"]),
        "nameenddt": pd.to_datetime(["2020-01-01", "2020-01-01"]),
        "ticker": ["AAA", "BBB"],
    })


def test_clean_dsf_year_filters_to_resolved_universe():
    raw = _raw_dsf_fixture()
    sn = _stocknames_for_dsf_fixture()
    cleaned, stats = neq.clean_dsf_year(raw, resolved_permnos=[1, 2], stocknames=sn)
    assert set(cleaned["permno"]) == {1, 2}
    assert stats["rows_permno_not_in_resolved_universe"] == 1  # permno 3


def test_clean_dsf_year_negative_price_and_sentinel_and_cfacpr_stats():
    raw = _raw_dsf_fixture()
    sn = _stocknames_for_dsf_fixture()
    cleaned, stats = neq.clean_dsf_year(raw, resolved_permnos=[1, 2], stocknames=sn)

    assert stats["negative_prc_rows"] == 1
    assert stats["ret_sentinel_rows_mapped_to_null"] == 1
    assert stats["cfacpr_zero_rows"] == 2  # permno 2's two rows have cfacpr == 0.0

    row1_1 = cleaned[(cleaned["permno"] == 1) & (cleaned["date"] == "2010-01-05")].iloc[0]
    assert row1_1["prc"] == 11.0
    assert row1_1["prc_is_bidask_avg"]
    assert pd.isna(row1_1["ret"])  # sentinel -66.0 mapped to null
    assert row1_1["retx"] == -0.02  # retx untouched (no sentinel there)

    row2 = cleaned[(cleaned["permno"] == 2) & (cleaned["date"] == "2010-01-04")].iloc[0]
    assert row2["cfacpr"] == 0.0  # carried through as-is; no derived prc_adj column


def test_clean_dsf_year_attaches_ticker_and_drops_duplicates():
    raw = _raw_dsf_fixture()
    dup = pd.concat([raw, raw.iloc[[0]]], ignore_index=True)  # duplicate the first (permno, date) row
    sn = _stocknames_for_dsf_fixture()
    cleaned, stats = neq.clean_dsf_year(dup, resolved_permnos=[1, 2], stocknames=sn)
    assert stats["duplicate_permno_date_rows_dropped"] == 1
    assert not cleaned.duplicated(subset=["permno", "date"]).any()
    assert set(cleaned.loc[cleaned["permno"] == 1, "ticker"]) == {"AAA"}


def test_clean_dsf_year_output_schema():
    raw = _raw_dsf_fixture()
    sn = _stocknames_for_dsf_fixture()
    cleaned, _stats = neq.clean_dsf_year(raw, resolved_permnos=[1, 2], stocknames=sn)
    assert list(cleaned.columns) == [
        "permno", "date", "ticker", "prc", "prc_is_bidask_avg",
        "ret", "retx", "shrout", "vol", "cfacpr", "cfacshr",
    ]
    assert cleaned["permno"].dtype == np.int64
    assert cleaned["date"].dtype.kind == "M"


# ---------------------------------------------------------------------------
# aggregate_year_stats
# ---------------------------------------------------------------------------


def test_aggregate_year_stats_sums_across_years():
    stats_by_year = {
        2010: {"input_rows": 5, "negative_prc_rows": 1},
        2011: {"input_rows": 7, "negative_prc_rows": 2},
    }
    agg = neq.aggregate_year_stats(stats_by_year)
    assert agg == {"input_rows": 12, "negative_prc_rows": 3}


# ---------------------------------------------------------------------------
# clean_dsi
# ---------------------------------------------------------------------------


def test_clean_dsi_parses_dates_and_detects_nulls():
    raw = pd.DataFrame({
        "date": ["1995-01-03", "1995-01-04"],
        "vwretd": [0.01, None],
        "sprtrn": [0.01, 0.02],
        "vwretx": [0.01, 0.02],
        "ewretd": [0.01, 0.02],
        "ewretx": [0.01, 0.02],
    })
    cleaned, stats = neq.clean_dsi(raw)
    assert stats["rows"] == 2
    assert stats["null_value_rows"] == 1
    assert stats["duplicate_date_rows_dropped"] == 0
    assert cleaned["date"].dtype.kind == "M"
    assert list(cleaned.columns) == ["date", "vwretd", "sprtrn", "vwretx", "ewretd", "ewretx"]


def test_clean_dsi_drops_duplicate_dates():
    # A duplicated dsi date would silently fan out every downstream
    # market-adjustment join -- must be dropped and counted, not silently
    # kept as two benchmark rows for the same trading day.
    raw = pd.DataFrame({
        "date": ["1995-01-03", "1995-01-03", "1995-01-04"],
        "vwretd": [0.01, 0.02, 0.03],
        "sprtrn": [0.01, 0.02, 0.03],
        "vwretx": [0.01, 0.02, 0.03],
        "ewretd": [0.01, 0.02, 0.03],
        "ewretx": [0.01, 0.02, 0.03],
    })
    cleaned, stats = neq.clean_dsi(raw)
    assert stats["duplicate_date_rows_dropped"] == 1
    assert len(cleaned) == 2
    assert not cleaned["date"].duplicated().any()


# ---------------------------------------------------------------------------
# summarize_dsedelist
# ---------------------------------------------------------------------------


def test_summarize_dsedelist(tmp_path):
    de = pd.DataFrame({
        "permno": [1, 2, 3],
        "dlstdt": ["2001-01-01", "2002-02-02", "2003-03-03"],
        "dlret": [0.0, None, -0.5],
        "dlstcd": [233, 233, 500],
    })
    path = tmp_path / "dsedelist.parquet"
    de.to_parquet(path, index=False)
    stats = neq.summarize_dsedelist(path, resolved_permnos=[1, 2])
    assert stats["rows"] == 3
    assert stats["distinct_permnos"] == 3
    assert stats["rows_outside_resolved_universe"] == 1  # permno 3
    assert stats["dlret_null_rows"] == 1
    assert stats["top_dlstcd_codes"][0] == (233, 2)


# ---------------------------------------------------------------------------
# detect_adjustment_events / summarize_adjustment_events / split_spot_check
# ---------------------------------------------------------------------------


def test_detect_adjustment_events_finds_split_not_small_drift():
    df = pd.DataFrame({
        "permno": [1, 1, 1, 2, 2],
        "date": pd.to_datetime(["2000-01-03", "2000-01-04", "2000-01-05", "2000-01-03", "2000-01-04"]),
        # permno 1: a real 2-for-1 split on 2000-01-04 (prc halves, cfacpr halves,
        # but the actual reported ret stays a normal small daily move).
        "prc": [100.0, 51.0, 52.0, 20.0, 20.2],
        "ret": [0.01, 0.02, 0.0196, 0.01, 0.01],  # permno 2: no real split, tiny cfacpr drift below threshold
        "cfacpr": [2.0, 1.0, 1.0, 1.0, 1.001],
    })
    events = neq.detect_adjustment_events(df, min_change=0.01)
    assert len(events) == 1
    assert events.iloc[0]["permno"] == 1
    assert events.iloc[0]["date"] == pd.Timestamp("2000-01-04")
    assert events.iloc[0]["naive_ret"] == pytest.approx(51.0 / 100.0 - 1)


def test_detect_adjustment_events_ignores_zero_prior_cfacpr_or_prc():
    # cfacpr == 0 is a real, documented CRSP data quirk (see clean_dsf_year's
    # cfacpr_zero_rows stat) for a small number of permnos. Without the
    # (prev_cfacpr > 0) / (prev_prc > 0) guard, a 0 -> positive transition
    # produces an infinite cfacpr_ratio/naive_ret -- not a real split, and
    # not evidence that belongs in the naive-vs-actual comparison.
    df = pd.DataFrame({
        "permno": [1, 1, 2, 2],
        "date": pd.to_datetime(["2000-01-03", "2000-01-04", "2000-01-03", "2000-01-04"]),
        # permno 1: prev_cfacpr == 0 on the transition day (blocked by the
        # prev_cfacpr > 0 half of the guard).
        # permno 2: prev_prc == 0 on the transition day, with a real
        # cfacpr change (blocked by the prev_prc > 0 half of the guard).
        "prc": [0.0, 10.0, 0.0, 10.0],
        "ret": [0.01, 0.02, 0.01, 0.02],
        "cfacpr": [0.0, 1.0, 1.0, 0.5],
    })
    events = neq.detect_adjustment_events(df, min_change=0.01)
    assert len(events) == 0


def test_summarize_adjustment_events_empty_is_safe():
    empty = pd.DataFrame(columns=["permno", "date", "prc", "ret", "cfacpr", "naive_ret", "cfacpr_ratio"])
    stats = neq.summarize_adjustment_events(empty)
    assert stats == {"n_events": 0}


def test_summarize_adjustment_events_contrasts_naive_vs_actual():
    events = pd.DataFrame({
        "naive_ret": [-0.5, -0.5],
        "ret": [0.01, 0.02],
    })
    stats = neq.summarize_adjustment_events(events)
    assert stats["naive_ret_abs_median"] == pytest.approx(0.5)
    assert stats["actual_ret_abs_median"] == pytest.approx(0.015)
    assert stats["frac_naive_ret_gt_20pct"] == 1.0
    assert stats["frac_actual_ret_gt_20pct"] == 0.0


# ---------------------------------------------------------------------------
# last_row_per_permno (the cross-year carryover used by main()'s
# incremental adjustment-event detection)
# ---------------------------------------------------------------------------


def test_last_row_per_permno_picks_most_recent_date_per_group():
    df = pd.DataFrame({
        "permno": [1, 1, 2, 2, 2],
        "date": pd.to_datetime(["2000-01-03", "2000-01-05", "2000-01-01", "2000-01-04", "2000-01-02"]),
        "prc": [10.0, 11.0, 20.0, 22.0, 21.0],
    })
    last = neq.last_row_per_permno(df)
    last = last.set_index("permno")
    assert last.loc[1, "date"] == pd.Timestamp("2000-01-05")
    assert last.loc[1, "prc"] == 11.0
    assert last.loc[2, "date"] == pd.Timestamp("2000-01-04")
    assert last.loc[2, "prc"] == 22.0


def test_update_event_carryover_first_year_returns_last_rows():
    year_cols = pd.DataFrame({
        "permno": [1, 1, 2], "date": pd.to_datetime(["2000-01-03", "2000-01-04", "2000-01-03"]),
        "prc": [10.0, 11.0, 20.0], "ret": [0.01, 0.02, 0.03], "cfacpr": [1.0, 1.0, 1.0],
    })
    carryover = neq.update_event_carryover(None, year_cols)
    assert sorted(carryover["permno"].tolist()) == [1, 2]
    assert carryover.set_index("permno").loc[1, "date"] == pd.Timestamp("2000-01-04")


def test_update_event_carryover_merges_across_years_keeping_absent_permnos():
    carryover = pd.DataFrame({
        "permno": [1, 2], "date": pd.to_datetime(["2000-12-31", "2000-06-15"]),
        "prc": [10.0, 5.0], "ret": [0.01, 0.02], "cfacpr": [1.0, 1.0],
    })
    # Only permno 1 trades this year; permno 2 has a gap and must keep its
    # OLD carryover row rather than disappearing.
    year_cols = pd.DataFrame({
        "permno": [1], "date": pd.to_datetime(["2001-01-03"]),
        "prc": [11.0], "ret": [0.03], "cfacpr": [1.0],
    })
    updated = neq.update_event_carryover(carryover, year_cols).set_index("permno")
    assert updated.loc[1, "date"] == pd.Timestamp("2001-01-03")  # replaced with the newer row
    assert updated.loc[2, "date"] == pd.Timestamp("2000-06-15")  # kept from the old carryover


def test_update_event_carryover_handles_full_permno_overlap_without_warning():
    # Regression test: when every permno in the old carryover also appears
    # in this year's data, filtering them out of the old carryover leaves
    # an EMPTY-but-typed remainder frame. Concatenating that with the new
    # data used to trigger a spurious NumPy/pandas DeprecationWarning on
    # the datetime64 column (see update_event_carryover's docstring) --
    # asserted here with warnings turned into errors so a regression fails
    # loudly rather than just adding noise to test output.
    carryover = pd.DataFrame({
        "permno": [1], "date": pd.to_datetime(["2000-12-31"]),
        "prc": [10.0], "ret": [0.01], "cfacpr": [1.0],
    })
    year_cols = pd.DataFrame({
        "permno": [1], "date": pd.to_datetime(["2001-01-03"]),
        "prc": [11.0], "ret": [0.02], "cfacpr": [1.0],
    })
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        updated = neq.update_event_carryover(carryover, year_cols)
    assert len(updated) == 1
    assert updated.iloc[0]["date"] == pd.Timestamp("2001-01-03")


# ---------------------------------------------------------------------------
# safe_concat
# ---------------------------------------------------------------------------


def _dt_frame(rows):
    return pd.DataFrame({
        "permno": [r[0] for r in rows], "date": pd.to_datetime([r[1] for r in rows]),
    })


def test_safe_concat_mixed_empty_and_nonempty_no_warning():
    empty = _dt_frame([]).astype({"permno": "int64"})
    nonempty = _dt_frame([(1, "2020-01-01")])
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        out = neq.safe_concat([empty, nonempty, empty], ignore_index=True)
    assert len(out) == 1
    assert out.iloc[0]["permno"] == 1


def test_safe_concat_all_empty_no_warning():
    empty1 = _dt_frame([]).astype({"permno": "int64"})
    empty2 = _dt_frame([]).astype({"permno": "int64"})
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        out = neq.safe_concat([empty1, empty2], ignore_index=True)
    assert len(out) == 0
    assert list(out.columns) == ["permno", "date"]


def test_safe_concat_empty_list_raises_like_plain_concat():
    with pytest.raises(ValueError):
        neq.safe_concat([])


def test_safe_concat_normal_case_matches_plain_concat():
    a = _dt_frame([(1, "2020-01-01")])
    b = _dt_frame([(2, "2020-01-02")])
    out = neq.safe_concat([a, b], ignore_index=True)
    assert out["permno"].tolist() == [1, 2]


# ---------------------------------------------------------------------------
# require_columns / read_parquet_with_projection (Fix 5: fail fast on a
# raw pull that's missing a column this script consumes)
# ---------------------------------------------------------------------------


def test_require_columns_passes_when_all_present():
    df = pd.DataFrame({"a": [1], "b": [2]})
    neq.require_columns(df, ["a", "b"], "some_file.parquet")  # must not raise


def test_require_columns_raises_naming_missing_columns_and_source():
    df = pd.DataFrame({"a": [1]})
    with pytest.raises(SystemExit) as exc_info:
        neq.require_columns(df, ["a", "b", "c"], "dsi.parquet")
    message = str(exc_info.value)
    assert "dsi.parquet" in message
    assert "'b'" in message and "'c'" in message


def test_read_parquet_with_projection_reads_only_requested_columns(tmp_path):
    path = tmp_path / "sample.parquet"
    pd.DataFrame({"a": [1, 2], "b": [3, 4], "c": [5, 6]}).to_parquet(path, index=False)
    out = neq.read_parquet_with_projection(path, ["a", "c"])
    assert list(out.columns) == ["a", "c"]
    assert len(out) == 2


def test_read_parquet_with_projection_missing_column_raises_actionable_error(tmp_path):
    path = tmp_path / "sample.parquet"
    pd.DataFrame({"a": [1, 2]}).to_parquet(path, index=False)
    with pytest.raises(SystemExit) as exc_info:
        neq.read_parquet_with_projection(path, ["a", "retx"])
    message = str(exc_info.value)
    assert str(path) in message
    assert "retx" in message


def test_split_spot_check_windows_by_calendar_days():
    df = pd.DataFrame({
        "permno": [1, 1, 1],
        "date": pd.to_datetime(["1999-12-01", "2000-06-20", "2000-06-21"]),
        "prc": [1.0, 2.0, 3.0], "ret": [0.1, 0.2, 0.3],
        "shrout": [10.0, 10.0, 20.0], "cfacpr": [2.0, 2.0, 1.0],
    })
    sub = neq.split_spot_check(df, permno=1, around_date="2000-06-21", window_days=7)
    assert sub["date"].tolist() == [pd.Timestamp("2000-06-20"), pd.Timestamp("2000-06-21")]


# ---------------------------------------------------------------------------
# summarize_prc_adj_lookahead_risk (quantifies why prc_adj isn't shipped)
# ---------------------------------------------------------------------------


def test_summarize_prc_adj_lookahead_risk_counts_disagreements():
    # permno 1: cfacpr == 1 throughout its final row -> prc / cfacpr == prc,
    # never disagrees with the raw-price screen.
    # permno 2: cfacpr ends at 2.0 (a split happened later in the pull) --
    # its first-year prc=6.0 (>= threshold, passes raw) but prc/cfacpr=3.0
    # (< threshold, fails the look-ahead-tainted screen): wrongly excluded.
    # permno 3: cfacpr ends at 0.5 -- first-year prc=3.0 (< threshold,
    # fails raw) but prc/cfacpr=6.0 (>= threshold): wrongly included.
    first_year_df = pd.DataFrame({
        "permno": [1, 2, 3],
        "prc": [5.0, 6.0, 3.0],
        "cfacpr": [1.0, 1.0, 1.0],  # first-year cfacpr is irrelevant; final_cfacpr drives the "would-be" adj price
    })
    final_cfacpr = pd.DataFrame({"permno": [1, 2, 3], "cfacpr": [1.0, 2.0, 0.5]})
    # summarize_prc_adj_lookahead_risk divides first_year_df's own prc by
    # its own cfacpr column (both from the same frame) -- to exercise the
    # "later split changes cfacpr" scenario, first_year_df's cfacpr must
    # itself already reflect the LATER (final) factor, exactly like a real
    # cfacpr column would for a permno whose split happened after the
    # first year. So set first_year_df's cfacpr to the final values.
    first_year_df["cfacpr"] = [1.0, 2.0, 0.5]
    result = neq.summarize_prc_adj_lookahead_risk(first_year_df, final_cfacpr, price_threshold=5.0)
    assert result["wrongly_excluded_rows"] == 1  # permno 2
    assert result["wrongly_included_rows"] == 1  # permno 3
    assert result["permnos_affected"] == 2
    assert result["n_permnos_cfacpr_ne_1_at_window_end"] == 2  # permnos 2 and 3
    assert result["n_permnos_total"] == 3


# ---------------------------------------------------------------------------
# lookup_permno_for_ticker
# ---------------------------------------------------------------------------


def test_lookup_permno_for_ticker_returns_most_recent():
    sn = _stocknames_fixture()
    assert neq.lookup_permno_for_ticker(sn, "NEWT") == 1
    assert neq.lookup_permno_for_ticker(sn, "BBB") == 2


def test_lookup_permno_for_ticker_raises_for_unknown_ticker():
    sn = _stocknames_fixture()
    with pytest.raises(ValueError):
        neq.lookup_permno_for_ticker(sn, "ZZZZ")


# ---------------------------------------------------------------------------
# duckdb_quote_literal / read_master_stock_raw
# ---------------------------------------------------------------------------


def test_duckdb_quote_literal_escapes_embedded_quote():
    assert neq.duckdb_quote_literal("a'b") == "'a''b'"


def test_read_master_stock_raw_handles_quoted_comma_value(tmp_path):
    # Regression test for the exact bug documented in read_master_stock_raw's
    # docstring: a quoted value containing a comma must stay one field, not
    # split into extra columns under a naively-sniffed no-quote dialect. Uses
    # a non-blank ticker (unlike the real file's actual stray block) since
    # read_master_stock_raw now filters blank tickers out via SQL -- see the
    # dedicated blank-ticker-filtering test below for that behavior.
    csv_path = tmp_path / "master_stock.csv"
    csv_path.write_text('Tic,Date,Price\nAAA,2011-01-03,10.5\nBBB,2011-01-04,"13,444.90"\n')
    raw = neq.read_master_stock_raw(csv_path)
    assert len(raw) == 2
    assert raw.loc[raw["ticker"] == "BBB", "price_text"].iloc[0] == "13,444.90"


def test_read_master_stock_raw_drops_blank_ticker_rows(tmp_path):
    csv_path = tmp_path / "master_stock.csv"
    csv_path.write_text('Tic,Date,Price\nAAA,2011-01-03,10.5\n,01/01/2018,"13,444.90"\n')
    raw = neq.read_master_stock_raw(csv_path)
    assert len(raw) == 1
    assert raw.iloc[0]["ticker"] == "AAA"


def test_read_master_stock_raw_pushes_down_end_date(tmp_path):
    csv_path = tmp_path / "master_stock.csv"
    csv_path.write_text(
        "Tic,Date,Price\nAAA,2011-01-03,10.5\nAAA,2014-01-01,20.0\n"
    )
    raw = neq.read_master_stock_raw(csv_path, end_date="2013-12-31")
    assert len(raw) == 1
    assert raw.iloc[0]["date_text"] == "2011-01-03"


def test_read_master_stock_raw_reordered_header_maps_by_name(tmp_path):
    # Regression test for the positional-columns bug this function's
    # docstring documents fixing: a reordered header must still map by
    # name, not by file position.
    csv_path = tmp_path / "master_stock.csv"
    csv_path.write_text("Date,Tic,Price\n2011-01-03,AAA,10.5\n")
    raw = neq.read_master_stock_raw(csv_path)
    assert raw.iloc[0]["ticker"] == "AAA"
    assert raw.iloc[0]["date_text"] == "2011-01-03"


def test_read_master_stock_raw_renamed_column_raises(tmp_path):
    csv_path = tmp_path / "master_stock.csv"
    csv_path.write_text("Symbol,Date,Price\nAAA,2011-01-03,10.5\n")
    with pytest.raises(Exception):
        neq.read_master_stock_raw(csv_path)


def test_profile_master_stock_file(tmp_path):
    csv_path = tmp_path / "master_stock.csv"
    csv_path.write_text(
        'Tic,Date,Price\n'
        'AAA,2011-01-03,10.5\n'
        'AAA,2011-01-04,10.6\n'
        ',01/01/2018,"1,000"\n'
        ',2020-01-01,500\n'
    )
    stats = neq.profile_master_stock_file(csv_path)
    assert stats["total_rows"] == 4
    assert stats["non_blank_ticker_rows"] == 2
    assert stats["blank_ticker_rows"] == 2
    assert stats["blank_ticker_mdy_rows"] == 1
    assert stats["blank_ticker_iso_rows"] == 1
    assert stats["distinct_tickers"] == 1
    assert stats["min_date_text"] == "2011-01-03"
    assert stats["max_date_text"] == "2011-01-04"
    assert stats["missing_price_rows"] == 0
    assert stats["infinite_price_rows"] == 0
    assert stats["negative_price_rows"] == 0


# ---------------------------------------------------------------------------
# clean_master_stock_frame
# ---------------------------------------------------------------------------


def _master_stock_raw_fixture():
    return pd.DataFrame({
        "ticker": ["AAA", "", None, "BBB", "AAA", "CCC", "DDD"],
        "date_text": [
            "2011-01-03", "01/01/2015", "01/01/2016", "2011-01-03",
            "2011-01-04", "13/45/2020", "2020-01-01",
        ],
        "price_text": ["10.5", "5.3", "434", "20.0", None, "9.9", "-inf"],
    })


def test_clean_master_stock_frame_drops_blank_tickers():
    cleaned, stats = neq.clean_master_stock_frame(_master_stock_raw_fixture())
    assert stats["blank_ticker_rows_dropped"] == 2
    assert set(cleaned["ticker"]) == {"AAA", "BBB", "DDD"}  # CCC dropped too (invalid date)


def test_clean_master_stock_frame_invalid_shape_matched_date_is_dropped():
    cleaned, stats = neq.clean_master_stock_frame(_master_stock_raw_fixture())
    assert stats["shape_matched_but_invalid_date_rows"] == 1  # "13/45/2020"
    assert "CCC" not in set(cleaned["ticker"])


def test_clean_master_stock_frame_missing_and_infinite_price_handling():
    cleaned, stats = neq.clean_master_stock_frame(_master_stock_raw_fixture())
    assert stats["missing_price_rows"] == 1  # AAA 2011-01-04, price_text=None
    assert stats["infinite_price_rows"] == 1  # DDD, "-inf"
    ddd_row = cleaned[cleaned["ticker"] == "DDD"].iloc[0]
    assert pd.isna(ddd_row["price"])


def test_clean_master_stock_frame_negative_price_flagged_invalid():
    raw = pd.DataFrame({
        "ticker": ["AAA"], "date_text": ["2011-01-03"], "price_text": ["-5.0"],
    })
    cleaned, stats = neq.clean_master_stock_frame(raw)
    assert stats["negative_price_rows"] == 1
    assert pd.isna(cleaned.iloc[0]["price"])


def test_clean_master_stock_frame_end_date_window():
    cleaned, stats = neq.clean_master_stock_frame(_master_stock_raw_fixture(), end_date="2011-01-03")
    # AAA and BBB both have a 2011-01-03 row; AAA's 2011-01-04 row, DDD, and
    # the invalid-date CCC row are all excluded.
    assert stats["rows_after_window_filter"] == 2
    assert cleaned["date"].max() == pd.Timestamp("2011-01-03")
    assert set(cleaned["ticker"]) == {"AAA", "BBB"}


# ---------------------------------------------------------------------------
# compute_return_from_prices
# ---------------------------------------------------------------------------


def test_compute_return_from_prices_normal_case():
    df = pd.DataFrame({
        "ticker": ["A", "A", "A"],
        "date": pd.to_datetime(["2011-01-03", "2011-01-04", "2011-01-05"]),
        "price": [10.0, 11.0, 9.9],
    })
    ret = neq.compute_return_from_prices(df, "ticker", "date", "price")
    assert ret.tolist()[0] != ret.tolist()[0]  # first is NaN (no prior day)
    assert ret.tolist()[1] == pytest.approx(0.1)


def test_compute_return_from_prices_blanks_across_large_gap():
    df = pd.DataFrame({
        "ticker": ["A", "A"],
        "date": pd.to_datetime(["2011-01-03", "2011-02-03"]),  # ~1 month gap
        "price": [10.0, 20.0],
    })
    ret = neq.compute_return_from_prices(df, "ticker", "date", "price", max_gap_days=4)
    assert pd.isna(ret.iloc[1])


# ---------------------------------------------------------------------------
# cross_validate_master_stock
# ---------------------------------------------------------------------------


def test_cross_validate_master_stock_basic_match():
    # 5 trading days (4 comparable return-days) so this single ticker
    # clears TICKER_ALIGNMENT_MIN_DAYS and the per-ticker gate doesn't
    # quarantine it for having too few days to judge.
    dates = pd.to_datetime(["2011-01-03", "2011-01-04", "2011-01-05", "2011-01-06", "2011-01-07"])
    prices = [10.0, 11.0, 9.9, 10.2, 10.5]
    rets = [0.0, 0.1, -0.1, 0.0303030303, 0.029411765]
    crsp = pd.DataFrame({
        "permno": [1] * 5, "ticker": ["AAA"] * 5, "date": dates, "prc": prices, "ret": rets,
    })
    master = pd.DataFrame({"ticker": ["AAA"] * 5, "date": dates, "price": prices})
    stats, merged = neq.cross_validate_master_stock(
        crsp, master, pd.Timestamp("2011-01-01"), pd.Timestamp("2011-12-31")
    )
    assert stats["matched_rows"] == 5
    assert stats["tickers_quarantined_misaligned"] == 0
    assert stats["return_correlation"] == pytest.approx(1.0)
    assert len(merged) == 5


def test_cross_validate_master_stock_drops_crsp_side_collisions():
    # Two different permnos both labeled "AAA" on the same date -- a
    # ticker-reuse/ambiguous-resolution artifact that must not silently
    # fan out into the join.
    crsp = pd.DataFrame({
        "permno": [1, 2],
        "ticker": ["AAA", "AAA"],
        "date": pd.to_datetime(["2011-01-03", "2011-01-03"]),
        "prc": [10.0, 99.0], "ret": [0.0, 0.5],
    })
    master = pd.DataFrame({
        "ticker": ["AAA"], "date": pd.to_datetime(["2011-01-03"]), "price": [10.0],
    })
    stats, merged = neq.cross_validate_master_stock(
        crsp, master, pd.Timestamp("2011-01-01"), pd.Timestamp("2011-12-31")
    )
    assert stats["crsp_ticker_date_collisions_dropped"] == 2
    assert stats["matched_rows"] == 0


def test_cross_validate_master_stock_drops_master_side_duplicates():
    crsp = pd.DataFrame({
        "permno": [1], "ticker": ["AAA"], "date": pd.to_datetime(["2011-01-03"]),
        "prc": [10.0], "ret": [0.0],
    })
    # master_stock.csv's real, confirmed bug: two different prices recorded
    # for the same (ticker, date).
    master = pd.DataFrame({
        "ticker": ["AAA", "AAA"], "date": pd.to_datetime(["2011-01-03", "2011-01-03"]),
        "price": [10.0, 20.0],
    })
    stats, merged = neq.cross_validate_master_stock(
        crsp, master, pd.Timestamp("2011-01-01"), pd.Timestamp("2011-12-31")
    )
    assert stats["master_ticker_date_collisions_dropped"] == 2
    assert stats["master_ticker_date_collision_tickers"] == 1
    assert stats["matched_rows"] == 0


def test_cross_validate_master_stock_trims_extreme_outlier_correlation():
    # A single implausible day for an otherwise well-matched ticker: the
    # per-ticker MEDIAN gate must not quarantine "AAA" for this one bad
    # day (median stays ~0 across the other 28 good days), but the
    # residual per-day extreme-difference check should still catch it.
    dates = pd.date_range("2011-01-03", periods=30, freq="B")
    rng = np.random.default_rng(0)
    ret = rng.normal(0, 0.01, size=len(dates))
    prices = 100 * np.cumprod(1 + ret)
    crsp = pd.DataFrame({"permno": 1, "ticker": "AAA", "date": dates, "prc": prices, "ret": ret})
    master_prices = prices.copy()
    master_prices[10] = master_prices[9] * 5  # one implausible 400% single-day jump
    master = pd.DataFrame({"ticker": "AAA", "date": dates, "price": master_prices})

    stats, _merged = neq.cross_validate_master_stock(
        crsp, master, dates.min(), dates.max()
    )
    assert stats["tickers_quarantined_misaligned"] == 0  # one bad day doesn't move the ticker's median much
    assert stats["return_extreme_diff_rows"] >= 1
    assert stats["return_correlation_excl_extreme"] > stats["return_correlation"]


# ---------------------------------------------------------------------------
# Small formatting helpers used by build_findings_markdown
# ---------------------------------------------------------------------------


def test_fmt_int_and_pct_and_date():
    assert neq._fmt_int(1234567) == "1,234,567"
    assert neq._fmt_pct(0.4829) == "48.29%"
    assert neq._fmt_pct(0.1, 0) == "10%"
    assert neq._fmt_date(pd.Timestamp("2010-01-04")) == "2010-01-04"


def test_fmt_int_and_fmt_pct_handle_nan_and_none_gracefully():
    # Regression test: a null shrout (or any other NaN reaching a spot-check
    # table) used to raise ValueError deep inside FINDINGS.md generation,
    # after every Parquet file had already been written.
    assert neq._fmt_int(float("nan")) == "n/a"
    assert neq._fmt_int(None) == "n/a"
    assert neq._fmt_pct(float("nan")) == "n/a"
    assert neq._fmt_pct(None) == "n/a"


def test_safe_div_returns_none_on_zero_denominator():
    assert neq._safe_div(5, 0) is None
    assert neq._fmt_pct(neq._safe_div(5, 0)) == "n/a"
    assert neq._safe_div(1, 4) == pytest.approx(0.25)


def test_markdown_table_basic_shape():
    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    table = neq._markdown_table(df)
    lines = table.splitlines()
    assert lines[0] == "| a | b |"
    assert lines[1] == "| --- | --- |"
    assert lines[2] == "| 1 | x |"


# ---------------------------------------------------------------------------
# build_findings_markdown -- smoke test with a minimal synthetic context,
# covering both the normal and the "crossval skipped" branches.
# ---------------------------------------------------------------------------


def _minimal_findings_ctx(crossval_skipped=False):
    empty_spot = pd.DataFrame(columns=["date", "prc", "ret", "shrout", "cfacpr"])
    empty_msft = pd.DataFrame(columns=["date", "prc", "ret", "retx", "shrout", "cfacpr"])
    dsf_stats = {
        "input_rows": 100, "output_rows": 99, "nonnull_prc_rows": 90, "negative_prc_rows": 1,
        "ret_sentinel_rows_mapped_to_null": 0, "retx_sentinel_rows_mapped_to_null": 0,
        "cfacpr_zero_rows": 0, "rows_with_no_ticker_on_record": 2,
    }
    dsi_stats = {
        "rows": 10, "date_min": pd.Timestamp("1995-01-03"), "date_max": pd.Timestamp("2014-06-30"),
        "null_value_rows": 0, "duplicate_date_rows_dropped": 0,
    }
    delist_stats = {
        "rows": 5, "date_min": pd.Timestamp("1996-01-01"), "date_max": pd.Timestamp("2013-01-01"),
        "top_dlstcd_codes": [(233, 3), (231, 2)], "rows_outside_resolved_universe": 0,
    }
    event_stats = {"n_events": 0}
    master_full_stats = {
        "min_date_text": "2011-01-03", "max_date_text": "2025-09-26",
        "non_blank_ticker_rows": 100, "blank_ticker_rows": 5,
        "blank_ticker_iso_rows": 1, "blank_ticker_mdy_rows": 4,
        "missing_price_rows": 3, "infinite_price_rows": 0, "negative_price_rows": 0,
    }
    master_clean_stats = {
        "blank_ticker_rows_dropped": 0, "missing_price_rows": 1, "unparseable_text_price_rows": 0,
        "infinite_price_rows": 0, "negative_price_rows": 0,
    }
    crossval_stats = {"skipped": True} if crossval_skipped else {
        "window_start": pd.Timestamp("2011-01-03"), "window_end": pd.Timestamp("2013-12-31"),
        "matched_rows": 10, "matched_distinct_tickers": 2,
        "crsp_distinct_tickers_in_window": 3, "master_distinct_tickers_in_window": 3,
        "master_ticker_date_collisions_dropped": 0, "master_ticker_date_collision_tickers": 0,
        "tickers_quarantined_misaligned": 0, "rows_quarantined_misaligned": 0,
        "return_comparison_rows": 10, "return_comparison_rows_retained": 10,
        "return_mean_abs_diff": 0.0, "return_median_abs_diff": 0.0, "return_frac_within_1pct": 1.0,
        "return_correlation": 0.99, "return_correlation_before_ticker_gate": 0.99,
        "return_extreme_diff_rows": 0, "return_extreme_diff_frac": 0.0,
    }
    prc_adj_risk = {
        "price_threshold": 5.0, "wrongly_excluded_rows": 0, "wrongly_included_rows": 0,
        "permnos_affected": 0, "n_permnos_cfacpr_ne_1_at_window_end": 0, "n_permnos_total": 3,
    }
    return {
        "n_years": 1, "year_min": 2010, "year_max": 2010,
        "resolved_permnos": [1, 2, 3],
        "dsf_stats": dsf_stats, "dsi_stats": dsi_stats, "delist_stats": delist_stats,
        "event_stats": event_stats, "aapl_permno": 1, "msft_permno": 2,
        "aapl_2000": empty_spot, "aapl_2005": empty_spot, "msft_div": empty_msft,
        "master_full_stats": master_full_stats, "master_clean_stats": master_clean_stats,
        "crossval_stats": crossval_stats, "prc_adj_risk": prc_adj_risk,
    }


def test_build_findings_markdown_normal_case_has_expected_sections():
    md = neq.build_findings_markdown(_minimal_findings_ctx())
    for heading in [
        "## What this covers", "## Cleaning CRSP's own data conventions",
        "## Are prices and returns already adjusted for stock splits and dividends?",
        "## Market benchmark", "## Delisting data", "## Cross-check against master_stock.csv",
        "## Output", "## What's deferred, not done here",
    ]:
        assert heading in md
    # prc_adj is deliberately not a shipped column -- must not be advertised as one
    assert "prc_adj" not in md


def test_build_findings_markdown_normal_case_reports_ticker_gate_and_verdict():
    ctx = _minimal_findings_ctx()
    ctx["crossval_stats"]["tickers_quarantined_misaligned"] = 4
    ctx["crossval_stats"]["rows_quarantined_misaligned"] = 40
    ctx["crossval_stats"]["quarantined_ticker_examples"] = [("ZZZZ", 40, 10.0, 200.0)]
    ctx["crossval_stats"]["return_comparison_rows_retained"] = 50  # clear the positive-verdict sample-size bar
    md = neq.build_findings_markdown(ctx)
    assert "4 tickers (40 rows) set aside" in md
    assert "`ZZZZ`" in md
    assert "**Verdict: this is a secondary, non-blocking check" in md


def test_build_findings_markdown_negative_verdict_when_agreement_weak():
    ctx = _minimal_findings_ctx()
    ctx["crossval_stats"]["return_frac_within_1pct"] = 0.10  # below the 0.95 bar
    md = neq.build_findings_markdown(ctx)
    assert "**Verdict: this check is inconclusive for this run.**" in md


def test_build_findings_markdown_handles_skipped_crossval():
    md = neq.build_findings_markdown(_minimal_findings_ctx(crossval_skipped=True))
    assert "no overlapping data to compare" in md
    # must not have tried to format fields that don't exist when skipped
    assert "tickers set aside this way" not in md
    assert "could not run this time" in md


# ---------------------------------------------------------------------------
# main() end-to-end, against a fully faked data/ tree (no real WRDS/CRSP
# files touched).
# ---------------------------------------------------------------------------


def _write_main_fixtures(tmp_path, monkeypatch):
    raw_crsp_dir = tmp_path / "raw_wrds" / "crsp"
    raw_equity_dir = tmp_path / "raw_equity"
    metadata_dir = tmp_path / "metadata"
    out_dir = tmp_path / "normalized_equity"
    raw_crsp_dir.mkdir(parents=True)
    raw_equity_dir.mkdir(parents=True)
    metadata_dir.mkdir(parents=True)

    monkeypatch.setattr(neq, "RAW_CRSP_DIR", raw_crsp_dir)
    monkeypatch.setattr(neq, "RAW_EQUITY_DIR", raw_equity_dir)
    monkeypatch.setattr(neq, "MASTER_STOCK_PATH", raw_equity_dir / "master_stock.csv")
    monkeypatch.setattr(neq, "OUT_DIR", out_dir)
    monkeypatch.setattr(lib, "METADATA_DIR", metadata_dir)
    monkeypatch.setattr(lib, "RESOLVED_IDS_PATH", metadata_dir / "resolved_universe_ids.parquet")

    pd.DataFrame({
        "secid": [1, 2], "cusip": ["A", "B"], "ticker": ["AAPL", "MSFT"], "issuer": ["X", "Y"],
        "permno": ["1", "2"], "gvkey": ["001", "002"], "ibes_ticker": ["AAPL", "MSFT"],
    }).to_parquet(metadata_dir / "resolved_universe_ids.parquet", index=False)

    pd.DataFrame({
        "permno": [1, 1, 2, 2],
        "date": ["2011-01-03", "2011-01-04", "2011-01-03", "2011-01-04"],
        "ret": [0.01, -0.02, 0.02, -0.03],
        "retx": [0.01, -0.02, 0.02, -0.03],
        "prc": [10.0, -10.5, 20.0, 19.5],
        "shrout": [100.0, 100.0, 200.0, 200.0],
        "cfacpr": [1.0, 1.0, 1.0, 1.0],
        "cfacshr": [1.0, 1.0, 1.0, 1.0],
        "vol": [1000.0, 1200.0, 3000.0, 3200.0],
    }).to_parquet(raw_crsp_dir / "dsf_2011.parquet", index=False)

    pd.DataFrame({
        "date": ["2011-01-03", "2011-01-04"],
        "vwretd": [0.005, -0.01], "sprtrn": [0.004, -0.011],
        "vwretx": [0.005, -0.01], "ewretd": [0.006, -0.009], "ewretx": [0.006, -0.009],
    }).to_parquet(raw_crsp_dir / "dsi.parquet", index=False)

    pd.DataFrame({
        "permno": [1], "dlstdt": ["2012-01-01"], "dlret": [0.0], "dlstcd": [233],
    }).to_parquet(raw_crsp_dir / "dsedelist.parquet", index=False)

    pd.DataFrame({
        "permno": [1, 2],
        "namedt": pd.to_datetime(["2000-01-01", "2000-01-01"]),
        "nameenddt": pd.to_datetime(["2020-01-01", "2020-01-01"]),
        "ticker": ["AAPL", "MSFT"],
    }).to_parquet(raw_crsp_dir / "stocknames.parquet", index=False)

    (raw_equity_dir / "master_stock.csv").write_text(
        "Tic,Date,Price\n"
        "AAPL,2011-01-03,10.0\n"
        "AAPL,2011-01-04,10.5\n"
        "MSFT,2011-01-03,20.0\n"
        ",01/01/2018,\"13,444.90\"\n"  # the real file's known stray-row shape
    )
    return out_dir


def test_main_end_to_end_produces_expected_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(neq, "ARCHIVE_END_DATE", "2013-12-31")
    monkeypatch.setattr(neq, "CROSSVAL_YEARS", (2011,))
    out_dir = _write_main_fixtures(tmp_path, monkeypatch)

    neq.main()

    equity_path = out_dir / "equity_2011.parquet"
    assert equity_path.exists()
    equity = pd.read_parquet(equity_path)
    assert len(equity) == 4
    assert set(equity["ticker"]) == {"AAPL", "MSFT"}
    assert equity["prc"].min() > 0  # negative-price row got abs()'d

    benchmark_path = out_dir / "market_benchmark.parquet"
    assert benchmark_path.exists()
    assert len(pd.read_parquet(benchmark_path)) == 2

    findings_path = out_dir / "FINDINGS.md"
    assert findings_path.exists()
    findings_text = findings_path.read_text()
    assert "## Are prices and returns already adjusted" in findings_text
    assert "master_stock.csv" in findings_text

    captured = capsys.readouterr()
    assert "DONE." in captured.out


def test_main_skips_crossval_gracefully_when_no_overlap_years(tmp_path, monkeypatch):
    monkeypatch.setattr(neq, "ARCHIVE_END_DATE", "2013-12-31")
    monkeypatch.setattr(neq, "CROSSVAL_YEARS", (2011,))
    out_dir = _write_main_fixtures(tmp_path, monkeypatch)
    # Rename the dsf file to a year outside CROSSVAL_YEARS so crossval_frames
    # is empty, exercising the graceful-skip path added for exactly this.
    (out_dir.parent / "raw_wrds" / "crsp" / "dsf_2011.parquet").rename(
        out_dir.parent / "raw_wrds" / "crsp" / "dsf_2009.parquet"
    )

    neq.main()

    findings_text = (out_dir / "FINDINGS.md").read_text()
    assert "no overlapping data to compare" in findings_text
    assert (out_dir / "equity_2009.parquet").exists()


def test_main_end_to_end_exercises_adjustment_evidence_branch(tmp_path, monkeypatch):
    """The other two main() e2e tests use a single dsf_2011.parquet with
    cfacpr == 1.0 throughout, so detect_adjustment_events finds nothing and
    both AAPL spot-check tables stay empty -- the "evidence" half of the
    adjustment-status section is never actually exercised end to end. This
    test adds a second year, 2005, with a real cfacpr change for AAPL's
    permno straddling AAPL_SPLIT_YEAR_DATES[2005], so main() has to
    populate a non-empty events table and a non-empty aapl_2005 spot-check
    table for real."""
    monkeypatch.setattr(neq, "ARCHIVE_END_DATE", "2013-12-31")
    monkeypatch.setattr(neq, "CROSSVAL_YEARS", (2011,))
    out_dir = _write_main_fixtures(tmp_path, monkeypatch)
    raw_crsp_dir = out_dir.parent / "raw_wrds" / "crsp"

    dates_2005 = [
        "2005-02-22", "2005-02-23", "2005-02-24", "2005-02-25",
        "2005-02-28", "2005-03-01", "2005-03-02",
    ]
    n = len(dates_2005)
    pd.DataFrame({
        "permno": [1] * n,
        "date": dates_2005,
        # A real 2-for-1 split effective 2005-02-28: prc and cfacpr both
        # halve, ret stays a normal small daily move (not ~-50%).
        "ret": [0.01, 0.01, 0.01, 0.01, 0.02, -0.01, 0.01],
        "retx": [0.01, 0.01, 0.01, 0.01, 0.02, -0.01, 0.01],
        "prc": [80.0, 81.0, 82.0, 83.0, 42.0, 41.5, 42.0],
        "shrout": [100.0] * 4 + [200.0] * 3,
        "cfacpr": [2.0] * 4 + [1.0] * 3,
        "cfacshr": [2.0] * 4 + [1.0] * 3,
        "vol": [1000.0] * n,
    }).to_parquet(raw_crsp_dir / "dsf_2005.parquet", index=False)

    neq.main()

    assert (out_dir / "equity_2005.parquet").exists()
    findings_text = (out_dir / "FINDINGS.md").read_text()
    # The whole-universe quantitative check found at least the one real event.
    assert "adjustment events happened across the whole universe" in findings_text
    assert "0 such adjustment events" not in findings_text
    # The AAPL 2005 spot-check table is populated (not the empty placeholder).
    assert "2005-02-28" in findings_text
