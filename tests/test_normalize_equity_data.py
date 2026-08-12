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
# compute_adjusted_price
# ---------------------------------------------------------------------------


def test_compute_adjusted_price_normal_case():
    prc = pd.Series([100.0, 50.0])
    cfacpr = pd.Series([2.0, 1.0])
    adj, n_bad = neq.compute_adjusted_price(prc, cfacpr)
    assert n_bad == 0
    assert adj.tolist() == [50.0, 50.0]


def test_compute_adjusted_price_guards_non_positive_cfacpr():
    prc = pd.Series([100.0, 100.0, 100.0])
    cfacpr = pd.Series([0.0, -1.0, np.nan])
    adj, n_bad = neq.compute_adjusted_price(prc, cfacpr)
    assert n_bad == 3
    assert adj.isna().all()


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
    assert stats["cfacpr_non_positive_rows"] == 2  # permno 2's two rows have cfacpr == 0.0

    row1_1 = cleaned[(cleaned["permno"] == 1) & (cleaned["date"] == "2010-01-05")].iloc[0]
    assert row1_1["prc"] == 11.0
    assert row1_1["prc_is_bidask_avg"]
    assert pd.isna(row1_1["ret"])  # sentinel -66.0 mapped to null
    assert row1_1["retx"] == -0.02  # retx untouched (no sentinel there)

    row2 = cleaned[(cleaned["permno"] == 2) & (cleaned["date"] == "2010-01-04")].iloc[0]
    assert pd.isna(row2["prc_adj"])  # cfacpr == 0 -> null, not inf


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
        "permno", "date", "ticker", "prc", "prc_is_bidask_avg", "prc_adj",
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
    assert cleaned["date"].dtype.kind == "M"
    assert list(cleaned.columns) == ["date", "vwretd", "sprtrn", "vwretx", "ewretd", "ewretx"]


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
    # split into extra columns under a naively-sniffed no-quote dialect.
    csv_path = tmp_path / "master_stock.csv"
    csv_path.write_text('Tic,Date,Price\nAAA,2011-01-03,10.5\n,01/01/2018,"13,444.90"\n')
    raw = neq.read_master_stock_raw(csv_path)
    assert len(raw) == 2
    assert raw.loc[1, "price_text"] == "13,444.90"
    assert pd.isna(raw.loc[1, "ticker"]) or raw.loc[1, "ticker"] == ""


def test_profile_master_stock_dates(tmp_path):
    csv_path = tmp_path / "master_stock.csv"
    csv_path.write_text('Tic,Date,Price\nAAA,2011-01-03,10.5\nAAA,2011-01-04,10.6\n,01/01/2018,"1,000"\n')
    stats = neq.profile_master_stock_dates(csv_path)
    assert stats["non_blank_ticker_rows"] == 2
    assert stats["min_date_text"] == "2011-01-03"
    assert stats["max_date_text"] == "2011-01-04"


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
    crsp = pd.DataFrame({
        "permno": [1, 1, 1],
        "ticker": ["AAA", "AAA", "AAA"],
        "date": pd.to_datetime(["2011-01-03", "2011-01-04", "2011-01-05"]),
        "prc": [10.0, 11.0, 9.9],
        "prc_adj": [10.0, 11.0, 9.9],
        "ret": [0.0, 0.1, -0.1],
    })
    master = pd.DataFrame({
        "ticker": ["AAA", "AAA", "AAA"],
        "date": pd.to_datetime(["2011-01-03", "2011-01-04", "2011-01-05"]),
        "price": [10.0, 11.0, 9.9],
    })
    stats, merged = neq.cross_validate_master_stock(
        crsp, master, pd.Timestamp("2011-01-01"), pd.Timestamp("2011-12-31")
    )
    assert stats["matched_rows"] == 3
    assert stats["return_correlation"] == pytest.approx(1.0)
    assert len(merged) == 3


def test_cross_validate_master_stock_drops_crsp_side_collisions():
    # Two different permnos both labeled "AAA" on the same date -- a
    # ticker-reuse/ambiguous-resolution artifact that must not silently
    # fan out into the join.
    crsp = pd.DataFrame({
        "permno": [1, 2],
        "ticker": ["AAA", "AAA"],
        "date": pd.to_datetime(["2011-01-03", "2011-01-03"]),
        "prc": [10.0, 99.0], "prc_adj": [10.0, 99.0], "ret": [0.0, 0.5],
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
        "prc": [10.0], "prc_adj": [10.0], "ret": [0.0],
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
    dates = pd.date_range("2011-01-03", periods=30, freq="B")
    rng = np.random.default_rng(0)
    ret = rng.normal(0, 0.01, size=len(dates))
    prices = 100 * np.cumprod(1 + ret)
    crsp = pd.DataFrame({
        "permno": 1, "ticker": "AAA", "date": dates,
        "prc": prices, "prc_adj": prices, "ret": ret,
    })
    master_prices = prices.copy()
    master_prices[10] = master_prices[9] * 5  # one implausible 400% single-day jump
    master = pd.DataFrame({"ticker": "AAA", "date": dates, "price": master_prices})

    stats, _merged = neq.cross_validate_master_stock(
        crsp, master, dates.min(), dates.max()
    )
    assert stats["return_extreme_diff_rows"] >= 1
    assert stats["return_correlation_excl_extreme"] > stats["return_correlation"]
    assert stats["top_extreme_diff_tickers"][0][0] == "AAA"


# ---------------------------------------------------------------------------
# Small formatting helpers used by build_findings_markdown
# ---------------------------------------------------------------------------


def test_fmt_int_and_pct_and_date():
    assert neq._fmt_int(1234567) == "1,234,567"
    assert neq._fmt_pct(0.4829) == "48.29%"
    assert neq._fmt_pct(0.1, 0) == "10%"
    assert neq._fmt_date(pd.Timestamp("2010-01-04")) == "2010-01-04"


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
        "input_rows": 100, "nonnull_prc_rows": 90, "negative_prc_rows": 1,
        "ret_sentinel_rows_mapped_to_null": 0, "retx_sentinel_rows_mapped_to_null": 0,
        "cfacpr_non_positive_rows": 0,
    }
    dsi_stats = {"rows": 10, "date_min": pd.Timestamp("1995-01-03"), "date_max": pd.Timestamp("2014-06-30")}
    delist_stats = {
        "rows": 5, "date_min": pd.Timestamp("1996-01-01"), "date_max": pd.Timestamp("2013-01-01"),
        "top_dlstcd_codes": [(233, 3), (231, 2)],
    }
    event_stats = {"n_events": 0}
    master_full_stats = {"min_date_text": "2011-01-03", "max_date_text": "2025-09-26", "non_blank_ticker_rows": 100}
    master_clean_stats = {
        "blank_ticker_rows_dropped": 5, "missing_price_rows": 1, "unparseable_text_price_rows": 0,
        "infinite_price_rows": 0, "negative_price_rows": 0,
    }
    crossval_stats = {"skipped": True} if crossval_skipped else {
        "matched_rows": 10, "matched_distinct_tickers": 2,
        "crsp_distinct_tickers_in_window": 3, "master_distinct_tickers_in_window": 3,
        "master_ticker_date_collisions_dropped": 0, "master_ticker_date_collision_tickers": 0,
        "return_comparison_rows": 10, "return_median_abs_diff": 0.0, "return_frac_within_1pct": 1.0,
        "return_correlation": 0.99, "return_extreme_diff_rows": 0, "return_extreme_diff_frac": 0.0,
    }
    return {
        "n_years": 1, "year_min": 2010, "year_max": 2010,
        "resolved_permnos": [1, 2, 3],
        "dsf_stats": dsf_stats, "dsi_stats": dsi_stats, "delist_stats": delist_stats,
        "event_stats": event_stats, "aapl_permno": 1, "msft_permno": 2,
        "aapl_2000": empty_spot, "aapl_2005": empty_spot, "msft_div": empty_msft,
        "master_full_stats": master_full_stats, "master_clean_stats": master_clean_stats,
        "crossval_stats": crossval_stats,
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


def test_build_findings_markdown_handles_skipped_crossval():
    md = neq.build_findings_markdown(_minimal_findings_ctx(crossval_skipped=True))
    assert "no overlapping data to compare" in md
    # must not have tried to format fields that don't exist when skipped
    assert "Comparison result for the 2011-2013 overlap" not in md


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
