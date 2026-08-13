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

import duckdb
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


def test_clean_dsf_year_dividend_and_scale_counters():
    # Pull-wide evidence classify_adjustment_status's dividend and
    # return-scale checks need (see round-3 review findings 1 and 2):
    # computed here, on the post-sentinel-mapping, post-dedup frame.
    raw = pd.DataFrame({
        "permno": [1, 1, 1],
        "date": ["2010-01-04", "2010-01-05", "2010-01-06"],
        # Day 2 is a real dividend day (ret includes it, retx doesn't).
        # Day 3 is a big raw move (>20%), unrelated to the dividend check.
        "ret": [0.01, 0.05, 0.30],
        "retx": [0.01, 0.02, 0.30],
        "prc": [10.0, 10.5, 13.5],
        "shrout": [100.0, 100.0, 100.0],
        "cfacpr": [1.0, 1.0, 1.0],
        "cfacshr": [1.0, 1.0, 1.0],
        "vol": [1000.0, 1000.0, 1000.0],
    })
    sn = pd.DataFrame({
        "permno": [1], "namedt": pd.to_datetime(["2000-01-01"]),
        "nameenddt": pd.to_datetime(["2020-01-01"]), "ticker": ["AAA"],
    })
    _cleaned, stats = neq.clean_dsf_year(raw, resolved_permnos=[1], stocknames=sn)
    assert stats["ret_nonnull_rows"] == 3
    assert stats["ret_and_retx_nonnull_rows"] == 3
    assert stats["ret_ne_retx_rows"] == 1  # only day 2 (0.05 vs 0.02)
    assert stats["ret_lt_retx_rows"] == 0  # ret > retx on the dividend day -- the expected direction
    assert stats["ret_abs_gt_big_move_rows"] == 1  # only day 3 (0.30 > 0.20)


def test_clean_dsf_year_ret_lt_retx_flags_wrong_direction():
    # A dividend is a cash inflow, so `ret` should almost never be LESS
    # than `retx` -- this directional sanity check exists to catch
    # ret/retx being mislabeled or swapped, not just "do they differ".
    raw = pd.DataFrame({
        "permno": [1, 1],
        "date": ["2010-01-04", "2010-01-05"],
        "ret": [0.01, 0.01],
        "retx": [0.01, 0.05],
        "prc": [10.0, 10.1],
        "shrout": [100.0, 100.0],
        "cfacpr": [1.0, 1.0],
        "cfacshr": [1.0, 1.0],
        "vol": [1000.0, 1000.0],
    })
    sn = pd.DataFrame({
        "permno": [1], "namedt": pd.to_datetime(["2000-01-01"]),
        "nameenddt": pd.to_datetime(["2020-01-01"]), "ticker": ["AAA"],
    })
    _cleaned, stats = neq.clean_dsf_year(raw, resolved_permnos=[1], stocknames=sn)
    assert stats["ret_ne_retx_rows"] == 1
    assert stats["ret_lt_retx_rows"] == 1


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


def test_clean_dsi_maps_missing_return_sentinel_codes():
    # Round-3 review finding: clean_dsi previously skipped the same CRSP
    # missing-return sentinel mapping clean_dsf_year applies to dsf.ret/
    # dsf.retx -- a sentinel surviving into market_benchmark.parquet is
    # strictly worse than one surviving in dsf, since every downstream
    # abnormal-return calculation subtracts this series.
    raw = pd.DataFrame({
        "date": ["1995-01-03", "1995-01-04"],
        "vwretd": [0.01, 0.02],
        "sprtrn": [0.01, -99.0],  # CRSP's documented missing-return sentinel
        "vwretx": [0.01, 0.02],
        "ewretd": [0.01, 0.02],
        "ewretx": [0.01, 0.02],
    })
    cleaned, stats = neq.clean_dsi(raw)
    assert stats["sentinel_rows_mapped_to_null"] == 1
    assert pd.isna(cleaned.loc[cleaned["date"] == pd.Timestamp("1995-01-04"), "sprtrn"].iloc[0])
    assert stats["null_value_rows"] == 1  # counted as missing, not as a literal -99.0


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
    # Both rows here have a naive move bigger than 20%, so they're both in
    # the "big naive move" subset -- naive_ret_abs_median/
    # actual_ret_abs_median/frac_actual_ret_gt_big_move are scoped to that
    # subset (see the function's own docstring for why: an earlier version
    # computed these over ALL events but presented them as describing the
    # big-move subset specifically).
    events = pd.DataFrame({
        "naive_ret": [-0.5, -0.5],
        "ret": [0.01, 0.02],
    })
    stats = neq.summarize_adjustment_events(events)
    assert stats["naive_ret_abs_median"] == pytest.approx(0.5)
    assert stats["actual_ret_abs_median"] == pytest.approx(0.015)
    assert stats["frac_actual_ret_gt_big_move"] == 0.0


def test_summarize_adjustment_events_scopes_stats_to_big_move_subset_only():
    # Regression test for the round-4 review finding: a THIRD row with a
    # small naive move (not a real split -- ordinary day-to-day noise)
    # must not leak into naive_ret_abs_median/actual_ret_abs_median/
    # frac_actual_ret_gt_big_move, which describe the big-move subset only.
    events = pd.DataFrame({
        "naive_ret": [-0.5, -0.5, 0.01],  # third row: small, NOT a big move
        "ret": [0.01, 0.02, 0.30],        # third row's actual ret is large, but it's excluded anyway
    })
    stats = neq.summarize_adjustment_events(events)
    assert stats["n_events"] == 3
    assert stats["n_events_with_big_naive_move"] == 2  # only the first two
    assert stats["naive_ret_abs_median"] == pytest.approx(0.5)  # unaffected by the small-move row
    assert stats["actual_ret_abs_median"] == pytest.approx(0.015)  # unaffected by the 0.30 outlier
    assert stats["frac_actual_ret_gt_big_move"] == 0.0  # neither 0.01 nor 0.02 exceeds the threshold
    assert stats["n_events_with_big_naive_move"] == 2
    assert stats["frac_big_naive_events_ret_equals_naive"] == 0.0


# ---------------------------------------------------------------------------
# classify_adjustment_status (Fix: the adjustment-status verdict must be
# derived from the evidence, not hardcoded to always say "adjusted")
# ---------------------------------------------------------------------------


def _events_df(n_big, frac_matches_naive):
    """n_big events where the naive (raw-price) move is a big one (>20%);
    frac_matches_naive of them have `ret` land within 1% of that same
    naive move (simulating "not adjusted" for that fraction)."""
    n_match = round(n_big * frac_matches_naive)
    naive = [-0.5] * n_big
    ret = [-0.5] * n_match + [0.01] * (n_big - n_match)
    return pd.DataFrame({"naive_ret": naive, "ret": ret})


def _dsf_stats_fixture(**overrides):
    """dsf_stats fields classify_adjustment_status needs for its dividend
    and return-scale checks, defaulted so both read "adjusted"/"plausible"
    (real-pull-like ratios: 0.7% ret!=retx, 0.4% |ret|>20%) -- lets a test
    override just the one dimension it's exercising without having to
    restate every field."""
    base = {
        "ret_and_retx_nonnull_rows": 1_000_000,
        "ret_ne_retx_rows": 7_000,
        "ret_lt_retx_rows": 0,
        "ret_nonnull_rows": 1_000_000,
        "ret_abs_gt_big_move_rows": 4_000,
    }
    base.update(overrides)
    return base


def test_classify_adjustment_status_adjusted():
    stats = neq.summarize_adjustment_events(_events_df(40, 0.0))
    result = neq.classify_adjustment_status(stats, _dsf_stats_fixture())
    assert result["split_status"] == "adjusted"
    assert result["dividend_status"] == "adjusted"
    assert result["ret_scale_status"] == "plausible"
    assert result["overall"] == "adjusted"
    assert result["reasons"] == []


def test_classify_adjustment_status_not_adjusted():
    stats = neq.summarize_adjustment_events(_events_df(40, 1.0))
    result = neq.classify_adjustment_status(stats, _dsf_stats_fixture())
    assert result["split_status"] == "not_adjusted"
    assert result["overall"] == "flagged"
    assert any("split check" in r for r in result["reasons"])


def test_classify_adjustment_status_mixed():
    stats = neq.summarize_adjustment_events(_events_df(40, 0.5))
    result = neq.classify_adjustment_status(stats, _dsf_stats_fixture())
    assert result["split_status"] == "mixed"
    assert result["overall"] == "flagged"


def test_classify_adjustment_status_inconclusive_when_no_events():
    stats = neq.summarize_adjustment_events(pd.DataFrame(columns=["naive_ret", "ret"]))
    result = neq.classify_adjustment_status(stats, _dsf_stats_fixture())
    assert result["split_status"] == "inconclusive"
    assert result["overall"] == "inconclusive"


def test_classify_adjustment_status_inconclusive_when_too_few_big_events():
    # Below ADJUSTMENT_VERDICT_MIN_EVENTS even though ret always matches
    # naive -- too small a sample to assert "not_adjusted" confidently.
    stats = neq.summarize_adjustment_events(_events_df(5, 1.0))
    result = neq.classify_adjustment_status(stats, _dsf_stats_fixture())
    assert result["split_status"] == "inconclusive"
    assert result["overall"] == "inconclusive"


def test_classify_adjustment_status_dividend_not_adjusted_when_ret_equals_retx():
    # Round-3 review finding: a pull where `ret` was mistakenly populated
    # with `retx`'s values (i.e. NOT dividend-adjusted) previously
    # classified as fully "adjusted", since the only discriminator that
    # ran was the split check -- cfacpr can't see dividends at all.
    stats = neq.summarize_adjustment_events(_events_df(40, 0.0))  # split evidence: clean
    dsf = _dsf_stats_fixture(ret_ne_retx_rows=0)  # ret and retx never disagree
    result = neq.classify_adjustment_status(stats, dsf)
    assert result["split_status"] == "adjusted"
    assert result["dividend_status"] == "not_adjusted"
    assert result["overall"] == "flagged"
    assert any("dividend check" in r for r in result["reasons"])


def test_classify_adjustment_status_scale_implausible_when_ret_mis_scaled():
    # A `ret` column delivered in percent units (1.0 meaning +100%) is
    # maximally FAR from the naive move, so the split check alone would
    # call this "adjusted" -- the scale check exists specifically to catch
    # a unit/magnitude bug the split check structurally cannot see.
    stats = neq.summarize_adjustment_events(_events_df(40, 0.0))
    dsf = _dsf_stats_fixture(ret_abs_gt_big_move_rows=800_000)  # 80% of rows "big" -- not a plausible daily return
    result = neq.classify_adjustment_status(stats, dsf)
    assert result["split_status"] == "adjusted"
    assert result["ret_scale_status"] == "implausible"
    assert result["overall"] == "flagged"
    assert any("scale check" in r for r in result["reasons"])


def test_classify_adjustment_status_dividend_and_scale_inconclusive_below_row_floor():
    stats = neq.summarize_adjustment_events(_events_df(40, 0.0))
    dsf = _dsf_stats_fixture(ret_and_retx_nonnull_rows=10, ret_nonnull_rows=10)
    result = neq.classify_adjustment_status(stats, dsf)
    assert result["split_status"] == "adjusted"
    assert result["dividend_status"] == "inconclusive"
    assert result["ret_scale_status"] == "inconclusive"
    assert result["overall"] == "inconclusive"


def test_classify_adjustment_status_dividend_not_adjusted_when_ret_retx_swapped():
    # Round-4 review BLOCKER: ret_lt_retx_rows was computed by
    # clean_dsf_year, asserted by a test, and printed in the report with
    # its own interpretation -- but classify_adjustment_status never read
    # it. The disagreement-RATE check alone (frac_ne) is direction-blind
    # by construction, so a pull where `ret` and `retx` are fully swapped
    # (every single disagreement in the wrong direction) still classified
    # "adjusted", since a swap produces plenty of ret != retx rows too.
    stats = neq.summarize_adjustment_events(_events_df(40, 0.0))  # split evidence: clean
    dsf = _dsf_stats_fixture(ret_ne_retx_rows=135_756, ret_lt_retx_rows=135_756)  # 100% wrong direction
    result = neq.classify_adjustment_status(stats, dsf)
    assert result["split_status"] == "adjusted"
    assert result["dividend_status"] == "not_adjusted"
    assert result["overall"] == "flagged"
    assert any("LARGER" in r for r in result["reasons"])


def test_classify_adjustment_status_dividend_wrong_direction_boundary():
    stats = neq.summarize_adjustment_events(_events_df(40, 0.0))
    # Just under DIVIDEND_EVIDENCE_MAX_WRONG_DIRECTION_FRACTION (1%) -> still "adjusted".
    dsf_ok = _dsf_stats_fixture(ret_ne_retx_rows=10_000, ret_lt_retx_rows=99)  # 0.99%
    assert neq.classify_adjustment_status(stats, dsf_ok)["dividend_status"] == "adjusted"
    # Just over -> "not_adjusted".
    dsf_bad = _dsf_stats_fixture(ret_ne_retx_rows=10_000, ret_lt_retx_rows=101)  # 1.01%
    assert neq.classify_adjustment_status(stats, dsf_bad)["dividend_status"] == "not_adjusted"


def test_classify_adjustment_status_every_check_has_teeth():
    # Round-4 review's own answer to "is there a fifth gap in this
    # mechanism": rounds 2, 3, and 4 each found a different piece of
    # evidence that was collected and DISPLAYED in the report but never
    # actually consulted by the verdict. Rather than trust that F1's fix
    # is the last one needed, perturb every single piece of evidence this
    # function reads, one at a time, away from "healthy," and confirm
    # `overall` stops being "adjusted" every time -- this would catch a
    # future regression on ANY of them, not just the ones already found.
    healthy_events = _events_df(40, 0.0)
    healthy_dsf = _dsf_stats_fixture()
    healthy_stats = neq.summarize_adjustment_events(healthy_events)
    baseline = neq.classify_adjustment_status(healthy_stats, healthy_dsf)
    assert baseline["overall"] == "adjusted"  # sanity check on the "healthy" fixture itself

    dsf_perturbations = [
        ("ret_ne_retx_rows dropped to 0 (dividend disagreement vanishes)",
         {"ret_ne_retx_rows": 0}, "dividend_status", "not_adjusted"),
        ("every ret!=retx disagreement in the wrong direction (swapped columns)",
         {"ret_lt_retx_rows": healthy_dsf["ret_ne_retx_rows"]}, "dividend_status", "not_adjusted"),
        ("no ret value ever exceeds the big-move threshold (degenerate/constant ret)",
         {"ret_abs_gt_big_move_rows": 0}, "ret_scale_status", "implausible"),
        ("every ret value exceeds the big-move threshold (percent-units ret)",
         {"ret_abs_gt_big_move_rows": healthy_dsf["ret_nonnull_rows"]}, "ret_scale_status", "implausible"),
    ]
    for label, overrides, field, expected in dsf_perturbations:
        result = neq.classify_adjustment_status(healthy_stats, _dsf_stats_fixture(**overrides))
        assert result[field] == expected, f"perturbation did not flip {field}: {label}"
        assert result["overall"] != "adjusted", f"perturbation did not flip overall: {label}"

    event_perturbations = [
        ("ret always matches the naive move (fully unadjusted)", _events_df(40, 1.0), "split_status", "not_adjusted"),
        ("ret matches the naive move half the time (mixed)", _events_df(40, 0.5), "split_status", "mixed"),
    ]
    for label, events_df, field, expected in event_perturbations:
        result = neq.classify_adjustment_status(neq.summarize_adjustment_events(events_df), healthy_dsf)
        assert result[field] == expected, f"perturbation did not flip {field}: {label}"
        assert result["overall"] != "adjusted", f"perturbation did not flip overall: {label}"

    # actual_ret_abs_median (on the big-move subset) implausibly large --
    # ret never lands close to naive (so split_status stays "adjusted"),
    # but ret's own size is far too big to be an ordinary daily return.
    mis_scaled_events = pd.DataFrame({"naive_ret": [-0.5] * 40, "ret": [0.30] * 40})
    result = neq.classify_adjustment_status(neq.summarize_adjustment_events(mis_scaled_events), healthy_dsf)
    assert result["split_status"] == "adjusted"  # isolates the event-median path, not the split check
    assert result["ret_scale_status"] == "implausible"
    assert result["overall"] != "adjusted"


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


def test_read_master_stock_raw_pushdown_does_not_drop_non_iso_dates(tmp_path):
    # Regression test: an earlier version of this pushdown applied the
    # end_date lexical comparison to every row regardless of format, which
    # silently dropped in-window rows written in dd/mm/yyyy,
    # single-digit-month m/d/yyyy, or textual date formats (a lexical
    # string comparison is only chronologically meaningful for ISO-shaped
    # text). Non-ISO-shaped dates must now always survive this read,
    # regardless of end_date, and flow through to clean_master_stock_frame
    # to be parsed (or correctly counted as unparseable) there instead.
    csv_path = tmp_path / "master_stock.csv"
    csv_path.write_text(
        "Tic,Date,Price\n"
        "AAA,2012-12-31,10.0\n"      # ISO, in-window -> kept
        "AAA,2014-01-01,11.0\n"      # ISO, out-of-window -> dropped
        "BBB,31/12/2012,12.0\n"      # dd/mm/yyyy -> must survive
        "CCC,9/5/2012,13.0\n"        # single-digit-month m/d/yyyy -> must survive
        "DDD,Dec 31 2012,14.0\n"     # textual -> must survive
    )
    raw = neq.read_master_stock_raw(csv_path, end_date="2013-12-31")
    assert set(raw["ticker"]) == {"AAA", "BBB", "CCC", "DDD"}
    assert len(raw) == 4


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
    # duckdb.BinderException specifically (not a bare Exception, which
    # would also pass on an unrelated failure like an import error) --
    # "column not found" is exactly the clear, actionable error the
    # NAME-based SELECT in read_master_stock_raw's docstring promises for
    # a renamed column, as opposed to the old positional-columns override
    # silently mislabeling data with no error at all.
    with pytest.raises(duckdb.BinderException, match="Tic"):
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
    assert stats["blank_ticker_first_row"] == 3
    assert stats["blank_ticker_last_row"] == 4
    assert stats["blank_ticker_is_contiguous_at_end"] is True


def test_profile_master_stock_file_detects_non_contiguous_blank_tickers(tmp_path):
    csv_path = tmp_path / "master_stock.csv"
    csv_path.write_text(
        'Tic,Date,Price\n'
        'AAA,2011-01-03,10.5\n'
        ',01/01/2018,"1,000"\n'  # blank ticker in the MIDDLE, not at the end
        'BBB,2011-01-04,10.6\n'
    )
    stats = neq.profile_master_stock_file(csv_path)
    assert stats["blank_ticker_rows"] == 1
    assert stats["blank_ticker_first_row"] == 2
    assert stats["blank_ticker_last_row"] == 2
    assert stats["blank_ticker_is_contiguous_at_end"] is False


def test_profile_master_stock_file_no_blank_tickers(tmp_path):
    csv_path = tmp_path / "master_stock.csv"
    csv_path.write_text('Tic,Date,Price\nAAA,2011-01-03,10.5\n')
    stats = neq.profile_master_stock_file(csv_path)
    assert stats["blank_ticker_rows"] == 0
    assert stats["blank_ticker_is_contiguous_at_end"] is False
    assert stats["blank_ticker_first_row"] is None
    assert stats["blank_ticker_last_row"] is None


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


def test_clean_master_stock_frame_price_quality_counters_respect_window():
    # Round-3 review finding: missing/infinite/negative price counters
    # must describe only the window this check actually uses (FINDINGS.md
    # has always claimed this) -- a bad price on a row the window filter
    # excludes anyway must not be counted. An earlier version of this
    # function computed these counters BEFORE the window filter ran,
    # benign only by coincidence (the upstream DuckDB read already
    # excludes most out-of-window ISO-dated rows).
    raw = pd.DataFrame({
        "ticker": ["AAA", "AAA"],
        "date_text": ["2011-01-03", "2020-01-01"],
        "price_text": ["10.5", "-inf"],  # the bad price sits OUTSIDE the window
    })
    cleaned, stats = neq.clean_master_stock_frame(raw, end_date="2011-01-03")
    assert stats["rows_after_window_filter"] == 1
    assert stats["infinite_price_rows"] == 0  # the -inf row was already excluded by the window filter
    assert stats["missing_price_rows"] == 0
    assert stats["negative_price_rows"] == 0
    assert len(cleaned) == 1


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
    # 5 trading days (4 comparable consecutive price pairs) so this single
    # ticker clears TICKER_ALIGNMENT_MIN_PRICE_PAIRS and the per-ticker
    # gate doesn't quarantine it for having too few pairs to judge.
    dates = pd.to_datetime(["2011-01-03", "2011-01-04", "2011-01-05", "2011-01-06", "2011-01-07"])
    prices = [10.0, 11.0, 9.9, 10.2, 10.5]
    rets = [0.0, 0.1, -0.1, 0.0303030303, 0.029411765]
    crsp = pd.DataFrame({
        "permno": [1] * 5, "ticker": ["AAA"] * 5, "date": dates, "prc": prices,
        "ret": rets, "retx": rets,
    })
    master = pd.DataFrame({"ticker": ["AAA"] * 5, "date": dates, "price": prices})
    stats, merged = neq.cross_validate_master_stock(
        crsp, master, pd.Timestamp("2011-01-01"), pd.Timestamp("2011-12-31")
    )
    assert stats["matched_rows"] == 5
    assert stats["segments_quarantined_misaligned"] == 0
    assert stats["segments_quarantined_too_few_prices"] == 0
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
        "prc": [10.0, 99.0], "ret": [0.0, 0.5], "retx": [0.0, 0.5],
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
        "prc": [10.0], "ret": [0.0], "retx": [0.0],
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


def test_cross_validate_master_stock_collision_diagnostics():
    # Round-4 review finding: the report described every master_stock.csv
    # collision as "exactly two rows" / "two unrelated price levels"
    # unconditionally -- neither is true of every group. These diagnostics
    # (group-size distribution, valid-price counts, relative price
    # difference) let the report describe what's actually there instead.
    dates = pd.to_datetime(["2011-01-03", "2011-01-04", "2011-01-05"])
    crsp = pd.DataFrame({
        "permno": [1, 1, 1], "ticker": ["AAA"] * 3, "date": dates,
        "prc": [10.0, 10.1, 10.2], "ret": [0.0, 0.01, 0.01], "retx": [0.0, 0.01, 0.01],
    })
    master = pd.DataFrame({
        "ticker": [
            "AAA", "AAA",         # 2011-01-03: a 2-row collision, only ONE valid price
            "AAA", "AAA",         # 2011-01-04: a 2-row collision, two valid prices within 1%
            "AAA", "AAA", "AAA",  # 2011-01-05: a 3-row collision
        ],
        "date": [dates[0], dates[0], dates[1], dates[1], dates[2], dates[2], dates[2]],
        "price": [10.0, np.nan, 20.0, 20.1, 30.0, 40.0, 50.0],
    })
    stats, _merged = neq.cross_validate_master_stock(crsp, master, dates.min(), dates.max())
    assert stats["master_collision_groups"] == 3
    assert stats["master_collision_groups_2_rows"] == 2
    assert stats["master_collision_groups_3plus_rows"] == 1
    assert stats["master_collision_groups_one_valid_price"] == 1
    assert stats["master_collision_rows_one_valid_price_group"] == 2
    assert stats["master_collision_two_valid_price_groups"] == 1
    assert stats["master_collision_two_valid_price_groups_median_rel_diff"] == pytest.approx(0.005)
    assert stats["master_collision_two_valid_price_groups_within_1pct"] == 1
    # And, despite the recoverable single-valid-price row, ALL rows in
    # every colliding group are still dropped from the comparison --
    # recovering them was tried and measured to make agreement worse
    # (see the docstring), not just re-derived from "seems fine."
    assert stats["matched_rows"] == 0


def test_cross_validate_master_stock_trims_extreme_outlier_correlation():
    # A single implausible day for an otherwise well-matched ticker: the
    # per-ticker gate (price-ratio-steps, not returns) must not quarantine
    # "AAA" for this one bad day (its median price-step disagreement stays
    # tiny across the other 28 good days), but the residual per-day
    # extreme-return-difference check should still catch it.
    dates = pd.date_range("2011-01-03", periods=30, freq="B")
    rng = np.random.default_rng(0)
    ret = rng.normal(0, 0.01, size=len(dates))
    prices = 100 * np.cumprod(1 + ret)
    crsp = pd.DataFrame({
        "permno": 1, "ticker": "AAA", "date": dates, "prc": prices, "ret": ret, "retx": ret,
    })
    master_prices = prices.copy()
    master_prices[10] = master_prices[9] * 5  # one implausible 400% single-day jump
    master = pd.DataFrame({"ticker": "AAA", "date": dates, "price": master_prices})

    stats, _merged = neq.cross_validate_master_stock(
        crsp, master, dates.min(), dates.max()
    )
    assert stats["segments_quarantined_misaligned"] == 0  # one bad day doesn't move the ticker's median much
    assert stats["return_extreme_diff_rows"] >= 1
    assert stats["return_correlation_excl_extreme"] > stats["return_correlation"]


def test_cross_validate_master_stock_price_ratio_gate_ignores_return_convention():
    # Regression test for the exact false-positive class round 2's review
    # found: a ticker whose CRSP/master price ratio is perfectly constant
    # (same company, just two different price-adjustment conventions)
    # must be RETAINED even if a naive per-ticker return-difference gate
    # (using a calendar-gap-tolerant master return) would have flagged it
    # because a couple of its comparable "return" pairs actually spanned
    # more than one trading day. Simulated here directly: 6 consecutive
    # matched trading days, master's price is always exactly 2x CRSP's
    # (a split/adjustment-convention difference, not a different company).
    dates = pd.to_datetime([
        "2012-01-03", "2012-01-04", "2012-01-05", "2012-01-06", "2012-01-09", "2012-01-10",
    ])
    prices = [10.0, 10.5, 9.8, 10.2, 10.1, 10.4]
    crsp = pd.DataFrame({
        "permno": 1, "ticker": "SAME", "date": dates, "prc": prices,
        "ret": [0.0] + [prices[i] / prices[i - 1] - 1 for i in range(1, len(prices))],
    })
    crsp["retx"] = crsp["ret"]
    master = pd.DataFrame({"ticker": "SAME", "date": dates, "price": [2 * p for p in prices]})
    stats, _merged = neq.cross_validate_master_stock(
        crsp, master, dates.min(), dates.max()
    )
    assert stats["segments_quarantined_misaligned"] == 0
    assert stats["segments_quarantined_too_few_prices"] == 0
    assert stats["return_correlation"] == pytest.approx(1.0)


def test_cross_validate_master_stock_quarantines_genuinely_misaligned_ticker():
    dates = pd.to_datetime([
        "2012-01-03", "2012-01-04", "2012-01-05", "2012-01-06", "2012-01-09", "2012-01-10",
    ])
    crsp_prices = [10.0, 10.5, 9.8, 10.2, 10.1, 10.4]
    # A ticker whose master-side price moves are unrelated to CRSP's --
    # simulating master_stock.csv's current-ticker label pointing at a
    # different company than CRSP's point-in-time one for this symbol.
    master_prices = [50.0, 30.0, 80.0, 20.0, 65.0, 15.0]
    crsp = pd.DataFrame({
        "permno": 1, "ticker": "WRONG", "date": dates, "prc": crsp_prices,
        "ret": [0.0] + [crsp_prices[i] / crsp_prices[i - 1] - 1 for i in range(1, len(crsp_prices))],
    })
    crsp["retx"] = crsp["ret"]
    master = pd.DataFrame({"ticker": "WRONG", "date": dates, "price": master_prices})
    stats, _merged = neq.cross_validate_master_stock(
        crsp, master, dates.min(), dates.max()
    )
    assert stats["segments_quarantined_misaligned"] == 1
    assert stats["rows_quarantined_misaligned"] == 6
    assert stats["return_comparison_rows_retained"] == 0
    example_tickers = [e[0] for e in stats["quarantined_segment_examples"]]
    assert "WRONG" in example_tickers


def test_cross_validate_master_stock_too_few_price_pairs_quarantined_separately():
    # Only ONE matched day for this ticker -- zero consecutive-date price
    # pairs, so there's no way to judge alignment at all. Must be counted
    # as "too few to judge," not folded into "misaligned."
    crsp = pd.DataFrame({
        "permno": 1, "ticker": "LONE", "date": pd.to_datetime(["2012-01-03"]),
        "prc": [10.0], "ret": [0.01], "retx": [0.01],
    })
    master = pd.DataFrame({"ticker": "LONE", "date": pd.to_datetime(["2012-01-03"]), "price": [10.0]})
    stats, _merged = neq.cross_validate_master_stock(
        crsp, master, pd.Timestamp("2012-01-01"), pd.Timestamp("2012-01-31")
    )
    assert stats["segments_quarantined_misaligned"] == 0
    assert stats["segments_quarantined_too_few_prices"] == 1
    assert stats["rows_quarantined_too_few_prices"] == 1


def test_cross_validate_master_stock_single_pair_ticker_is_too_few_not_misaligned():
    # Round-3 review finding: without a minimum-pairs guard on the
    # "misaligned" mask, a segment with exactly ONE comparable price pair
    # whose one step happens to disagree would be classified "misaligned"
    # (a confident, wrong-company claim from a single data point) instead
    # of "too few to judge". TICKER_ALIGNMENT_MIN_PRICE_PAIRS == 2, so one
    # pair must land in "too few", never "misaligned", and must not be
    # named as a misaligned example.
    dates = pd.to_datetime(["2012-01-03", "2012-01-04"])
    crsp = pd.DataFrame({
        "permno": 1, "ticker": "ONE", "date": dates,
        "prc": [10.0, 10.1], "ret": [0.0, 0.01], "retx": [0.0, 0.01],
    })
    # A single price pair, disagreeing by a lot -- would clear the
    # misaligned threshold on its own if the n_pairs guard were missing.
    master = pd.DataFrame({"ticker": "ONE", "date": dates, "price": [10.0, 40.0]})
    stats, _merged = neq.cross_validate_master_stock(
        crsp, master, dates.min(), dates.max()
    )
    assert stats["segments_quarantined_misaligned"] == 0
    assert stats["segments_quarantined_too_few_prices"] == 1
    examples = stats.get("quarantined_segment_examples") or []
    assert all(e[0] != "ONE" for e in examples)


def test_cross_validate_master_stock_ticker_spanning_two_permnos_quarantines_only_bad_segment():
    # Round-3 review BLOCKER: master_stock.csv labels history with a
    # ticker's CURRENT symbol, so a ticker that hands off from one permno
    # to another mid-window (real example: "SAN" = Banco Santander Chile,
    # then Banco Santander S.A.) must be judged PER (ticker, permno), not
    # pooled per ticker -- otherwise a well-aligned majority segment
    # dilutes a genuinely misaligned minority segment below the
    # quarantine threshold.
    dates_a = pd.to_datetime(["2012-01-03", "2012-01-04", "2012-01-05", "2012-01-06"])
    dates_b = pd.to_datetime(["2012-06-04", "2012-06-05", "2012-06-06", "2012-06-07", "2012-06-08"])
    # Segment A (permno 100, the MINORITY of matched rows): CRSP and
    # master price levels are unrelated -- a different company under the
    # same symbol.
    crsp_a_prices = [10.0, 10.5, 9.8, 10.2]
    master_a_prices = [50.0, 30.0, 80.0, 20.0]
    # Segment B (permno 200, the MAJORITY of matched rows): CRSP and
    # master agree exactly -- the correct company.
    crsp_b_prices = [20.0, 20.5, 19.8, 20.2, 20.1]
    master_b_prices = list(crsp_b_prices)

    crsp = pd.DataFrame({
        "permno": [100] * 4 + [200] * 5,
        "ticker": "SPAN",
        "date": list(dates_a) + list(dates_b),
        "prc": crsp_a_prices + crsp_b_prices,
    })
    # Varying (not constant) so return_correlation isn't a 0/0 no-op --
    # this test is about which SEGMENT gets quarantined, not about the
    # return-agreement numbers themselves.
    crsp["retx"] = crsp.groupby("permno")["prc"].pct_change().fillna(0.0)
    crsp["ret"] = crsp["retx"]
    master = pd.DataFrame({
        "ticker": "SPAN",
        "date": list(dates_a) + list(dates_b),
        "price": master_a_prices + master_b_prices,
    })
    stats, merged = neq.cross_validate_master_stock(crsp, master, dates_a.min(), dates_b.max())

    assert stats["tickers_spanning_multiple_permnos"] == 1
    assert stats["segments_quarantined_misaligned"] == 1
    assert stats["rows_quarantined_misaligned"] == 4  # only segment A's rows, not segment B's 5
    examples = stats["quarantined_segment_examples"]
    assert len(examples) == 1
    assert examples[0][0] == "SPAN"
    assert examples[0][1] == 100  # the bad segment's own permno, not a ticker-pooled statistic
    # Segment B's rows must still be present in the join output, not
    # silently dropped just because the ticker as a whole has a problem.
    assert set(merged["permno"]) == {100, 200}


def test_cross_validate_master_stock_excludes_permno_handoff_row_from_return_comparison():
    # The single day a ticker hands off from one permno to another is
    # excluded from the return-agreement stats regardless of either
    # segment's own alignment verdict: master_stock.csv's day-over-day
    # return on that specific day is computed across the handoff itself
    # (comparing two different companies' price LEVELS) and describes
    # neither company individually. Uses adjacent trading days (gap well
    # under compute_return_from_prices' own max_gap_days=4) so this is
    # only caught by the explicit is_permno_handoff exclusion, not by the
    # pre-existing calendar-gap tolerance.
    dates_a = pd.to_datetime(["2012-01-03", "2012-01-04", "2012-01-05", "2012-01-06"])  # Tue-Fri
    dates_b = pd.to_datetime(["2012-01-09", "2012-01-10", "2012-01-11", "2012-01-12"])  # following Mon-Thu

    # Segment A (permno 100) and segment B (permno 200) each individually
    # match master exactly, day to day -- but the two segments sit at
    # completely different price LEVELS, so a "return" computed ACROSS
    # the boundary (master's Jan-9 price versus its own Jan-6 price)
    # would be wildly wrong and would corrupt the comparison if not
    # excluded.
    crsp_a = [10.0, 10.1, 10.2, 10.3]
    crsp_b = [50.0, 45.0, 49.5, 48.0]
    crsp = pd.DataFrame({
        "permno": [100] * 4 + [200] * 4,
        "ticker": "HAND",
        "date": list(dates_a) + list(dates_b),
        "prc": crsp_a + crsp_b,
    })
    crsp["retx"] = crsp.groupby("permno")["prc"].pct_change().fillna(0.0)
    crsp["ret"] = crsp["retx"]
    master = pd.DataFrame({
        "ticker": "HAND",
        "date": list(dates_a) + list(dates_b),
        "price": crsp_a + crsp_b,  # matches CRSP exactly within each segment
    })
    stats, _merged = neq.cross_validate_master_stock(crsp, master, dates_a.min(), dates_b.max())

    assert stats["segments_quarantined_misaligned"] == 0  # both segments individually match perfectly
    assert stats["segments_quarantined_too_few_prices"] == 0
    assert stats["tickers_spanning_multiple_permnos"] == 1
    assert stats["segment_boundary_rows_excluded"] == 1
    # 8 matched rows; Jan-3 drops for the usual first-day-has-no-prior-
    # return reason, Jan-9 (the handoff day) drops via is_permno_handoff.
    assert stats["return_comparison_rows"] == 6
    assert stats["return_comparison_rows_retained"] == 6
    # A still-included handoff day would corrupt this to well below 1.0.
    assert stats["return_correlation"] == pytest.approx(1.0)


def test_cross_validate_master_stock_quarantined_example_handles_all_null_prc():
    # A quarantined ticker whose prc happens to be entirely null for the
    # matched rows must not render a literal "nan" into the stats tuple --
    # _nan_to_none converts it to None, which the report layer formats as
    # "n/a" (see _fmt_usd), not "$nan".
    dates = pd.to_datetime([
        "2012-01-03", "2012-01-04", "2012-01-05", "2012-01-06", "2012-01-09", "2012-01-10",
    ])
    crsp = pd.DataFrame({
        "permno": 1, "ticker": "NULLPRC", "date": dates,
        "prc": [np.nan] * len(dates), "ret": [0.01] * len(dates), "retx": [0.01] * len(dates),
    })
    master = pd.DataFrame({
        "ticker": "NULLPRC", "date": dates, "price": [50.0, 30.0, 80.0, 20.0, 65.0, 15.0],
    })
    stats, _merged = neq.cross_validate_master_stock(
        crsp, master, dates.min(), dates.max()
    )
    # prc is all-null -> zero valid price pairs -> "too few", not "misaligned"
    assert stats["segments_quarantined_too_few_prices"] == 1


def test_cross_validate_master_stock_return_comparison_uses_retx_not_ret():
    # ret and retx diverge only on the one ex-dividend day; the reported
    # agreement statistics must be computed against retx (price-only),
    # matching master_stock.csv's own price-only series, not ret.
    dates = pd.to_datetime(["2012-01-03", "2012-01-04", "2012-01-05"])
    prices = [10.0, 10.5, 10.5]  # flat on the ex-div day: master shows a 0% price move
    crsp = pd.DataFrame({
        "permno": 1, "ticker": "DIVCO", "date": dates,
        "prc": prices,
        "retx": [0.0, 0.05, 0.0],       # price-only: flat on day 3, matches master exactly
        "ret": [0.0, 0.05, 0.03],       # total return: day 3 includes a 3% dividend master can't see
    })
    master = pd.DataFrame({"ticker": "DIVCO", "date": dates, "price": prices})
    stats, _merged = neq.cross_validate_master_stock(
        crsp, master, dates.min(), dates.max()
    )
    assert stats["return_median_abs_diff"] < 1e-9  # perfect match using retx
    assert stats["n_ex_dividend_days"] == 1
    assert stats["ex_dividend_median_abs_diff_retx"] < stats["ex_dividend_median_abs_diff_ret"]


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


def _minimal_findings_ctx(crossval_skipped=False, adjustment_status="adjusted"):
    empty_spot = pd.DataFrame(columns=["date", "prc", "ret", "retx", "shrout", "cfacpr"])
    empty_msft = pd.DataFrame(columns=["date", "prc", "ret", "retx", "shrout", "cfacpr"])
    dsf_stats = {
        "input_rows": 100, "output_rows": 99, "nonnull_prc_rows": 90, "negative_prc_rows": 1,
        "ret_sentinel_rows_mapped_to_null": 0, "retx_sentinel_rows_mapped_to_null": 0,
        "cfacpr_zero_rows": 0, "rows_with_no_ticker_on_record": 2,
        "rows_permno_not_in_resolved_universe": 0, "duplicate_permno_date_rows_dropped": 1,
        # classify_adjustment_status's dividend/scale discriminators, sized
        # so both read "adjusted"/"plausible" by default (real-pull-like
        # ratios: 0.7% ret!=retx, 0.4% |ret|>20%) -- a test exercising the
        # split-status branches doesn't also need to think about these.
        "ret_and_retx_nonnull_rows": 1_000_000, "ret_ne_retx_rows": 7_000, "ret_lt_retx_rows": 0,
        "ret_nonnull_rows": 1_000_000, "ret_abs_gt_big_move_rows": 4_000,
    }
    dsi_stats = {
        "rows": 10, "date_min": pd.Timestamp("1995-01-03"), "date_max": pd.Timestamp("2014-06-30"),
        "null_value_rows": 0, "duplicate_date_rows_dropped": 0, "sentinel_rows_mapped_to_null": 0,
    }
    delist_stats = {
        "rows": 5, "date_min": pd.Timestamp("1996-01-01"), "date_max": pd.Timestamp("2013-01-01"),
        "top_dlstcd_codes": [(233, 3), (231, 2)], "rows_outside_resolved_universe": 0,
    }
    # event_stats shaped to actually produce `adjustment_status` via
    # classify_adjustment_status, rather than hand-waving n_events=0 for
    # every test regardless of what's being asserted.
    if adjustment_status == "inconclusive":
        event_stats = {"n_events": 0}
    else:
        frac = {"adjusted": 0.0, "not_adjusted": 1.0, "mixed": 0.5}[adjustment_status]
        event_stats = {
            "n_events": 40, "naive_ret_abs_median": 0.48,
            "actual_ret_abs_median": 0.02 if adjustment_status != "not_adjusted" else 0.47,
            "frac_actual_ret_gt_big_move": 0.02 if adjustment_status != "not_adjusted" else 0.78,
            "n_events_with_big_naive_move": 35,
            "frac_big_naive_events_ret_equals_naive": frac,
        }
    master_full_stats = {
        "total_rows": 105, "min_date_text": "2011-01-03", "max_date_text": "2025-09-26",
        "non_blank_ticker_rows": 100, "blank_ticker_rows": 5,
        "blank_ticker_iso_rows": 1, "blank_ticker_mdy_rows": 4,
        "missing_price_rows": 3, "infinite_price_rows": 0, "negative_price_rows": 0,
        "blank_ticker_first_row": 101, "blank_ticker_last_row": 105,
        "blank_ticker_is_contiguous_at_end": True,
    }
    master_clean_stats = {
        "blank_ticker_rows_dropped": 0, "missing_price_rows": 1, "unparseable_text_price_rows": 0,
        "infinite_price_rows": 0, "negative_price_rows": 0,
        "unrecognized_date_format_rows": 0, "shape_matched_but_invalid_date_rows": 0,
    }
    crossval_stats = {"skipped": True} if crossval_skipped else {
        "window_start": pd.Timestamp("2011-01-03"), "window_end": pd.Timestamp("2013-12-31"),
        "matched_rows": 10, "matched_distinct_tickers": 2,
        "crsp_distinct_tickers_in_window": 3, "master_distinct_tickers_in_window": 3,
        "crsp_ticker_date_collisions_dropped": 0,
        "master_ticker_date_collisions_dropped": 0, "master_ticker_date_collision_tickers": 0,
        "segments_quarantined_misaligned": 0, "rows_quarantined_misaligned": 0,
        "segments_quarantined_too_few_prices": 0, "rows_quarantined_too_few_prices": 0,
        "tickers_spanning_multiple_permnos": 0, "segment_boundary_rows_excluded": 0,
        "ticker_gate_worst_retained_median_diff": 6.9e-4, "ticker_gate_closest_quarantined_median_diff": None,
        "return_comparison_rows": 10, "return_comparison_rows_retained": 10,
        "return_mean_abs_diff": 0.0, "return_median_abs_diff": 0.0, "return_frac_within_1pct": 1.0,
        "return_correlation": 0.99, "return_correlation_before_ticker_gate": 0.99,
        "return_frac_within_1pct_before_ticker_gate": 0.98,
        "return_extreme_diff_rows": 0, "return_extreme_diff_frac": 0.0,
        "n_ex_dividend_days": 0,
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
        # Computed from the same event_stats/dsf_stats above via the real
        # function (not hand-rolled) -- build_findings_markdown now reads
        # this precomputed verdict from ctx rather than recomputing it
        # (round-4 review: classify_adjustment_status was being called
        # twice per run from identical inputs).
        "adjustment_verdict": neq.classify_adjustment_status(event_stats, dsf_stats),
        "aapl_2000": empty_spot, "aapl_2005": empty_spot, "msft_div": empty_msft,
        "master_full_stats": master_full_stats, "master_clean_stats": master_clean_stats,
        "crossval_stats": crossval_stats, "prc_adj_risk": prc_adj_risk,
        "aapl_last_cfacpr": 4.0, "aapl_last_date": pd.Timestamp("2014-06-30"),
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
    ctx["crossval_stats"]["segments_quarantined_misaligned"] = 4
    ctx["crossval_stats"]["rows_quarantined_misaligned"] = 40
    # (ticker, permno, median_price_step_diff, crsp_price_median, master_price_median)
    ctx["crossval_stats"]["quarantined_segment_examples"] = [("ZZZZ", 99999, 0.05, 10.0, 200.0)]
    ctx["crossval_stats"]["return_comparison_rows_retained"] = 50  # clear the positive-verdict sample-size bar
    md = neq.build_findings_markdown(ctx)
    assert "4 segments (40 rows)" in md
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
    # Must not have tried to format fields that don't exist when skipped --
    # asserting on a string _crossval_detail_lines actually emits (a
    # previous version of this test asserted on a string that appeared
    # nowhere in the module at all, so it would have passed even if the
    # entire crossval detail block leaked into the skipped-run report).
    assert "single most important finding of this cross-check" not in md
    assert "could not run this time" in md


def test_build_findings_markdown_ticker_gate_margin_is_derived_not_asserted():
    # Round-5 review: "cleanly separated with a gap between them" is
    # unfalsifiable (retained/misaligned are complementary masks on the
    # same threshold) -- the report now derives a ratio instead.
    ctx = _minimal_findings_ctx()
    ctx["crossval_stats"]["segments_quarantined_misaligned"] = 2
    ctx["crossval_stats"]["rows_quarantined_misaligned"] = 100
    ctx["crossval_stats"]["ticker_gate_worst_retained_median_diff"] = 6.9e-4
    ctx["crossval_stats"]["ticker_gate_closest_quarantined_median_diff"] = 3.79e-3
    md = neq.build_findings_markdown(ctx)
    assert "5.5x apart" in md
    assert "cleanly separated" not in md

    ctx2 = _minimal_findings_ctx()
    ctx2["crossval_stats"]["ticker_gate_worst_retained_median_diff"] = 0.0
    ctx2["crossval_stats"]["ticker_gate_closest_quarantined_median_diff"] = 3.79e-3
    md2 = neq.build_findings_markdown(ctx2)
    assert "Every kept segment on this run measured exactly 0" in md2


def test_cross_validate_master_stock_full_overlap_but_no_usable_price_is_too_few():
    # Round-5 review finding: "too few overlapping DAYS" was wrong for a
    # real ticker (TOPS) with COMPLETE date overlap but an inf/-inf (i.e.
    # null after cleaning) master price on every one of those days --
    # quarantined for too few usable price PAIRS, not date overlap.
    dates = pd.date_range("2012-01-03", periods=5, freq="B")
    crsp = pd.DataFrame({
        "permno": 1, "ticker": "TOPS", "date": dates,
        "prc": [10.0, 10.1, 10.2, 10.3, 10.4], "ret": 0.01, "retx": 0.01,
    })
    master = pd.DataFrame({"ticker": "TOPS", "date": dates, "price": [np.nan] * 5})
    stats, _merged = neq.cross_validate_master_stock(crsp, master, dates.min(), dates.max())
    assert stats["matched_rows"] == 5
    assert stats["segments_quarantined_too_few_prices"] == 1
    assert stats["rows_quarantined_too_few_prices"] == 5
    assert stats["segments_quarantined_misaligned"] == 0


def test_build_findings_markdown_too_few_bucket_names_unusable_prices():
    ctx = _minimal_findings_ctx()
    ctx["crossval_stats"]["segments_quarantined_too_few_prices"] = 1
    ctx["crossval_stats"]["rows_quarantined_too_few_prices"] = 754
    md = neq.build_findings_markdown(ctx)
    assert "don't have enough overlapping days" not in md
    assert "too few days where both sides actually have a price" in md


def test_build_findings_markdown_split_bullet_matches_verdict_gate():
    # Round-5 review BLOCKER: the split bullet used to sound conclusive
    # whenever n_big >= 1, while the verdict needs n_big >= 30 -- both now
    # read the same gate (adjustment_verdict["split_status"]).
    for n_big in [0, 1, 5, 29, 30, 35]:
        ctx = _minimal_findings_ctx()
        event_stats = neq.summarize_adjustment_events(_events_df(n_big, 0.0))
        ctx["event_stats"] = event_stats
        ctx["adjustment_verdict"] = neq.classify_adjustment_status(event_stats, ctx["dsf_stats"])
        md = neq.build_findings_markdown(ctx)
        expect_conclusive = ctx["adjustment_verdict"]["split_status"] != "inconclusive"
        conclusive = "a fully unadjusted return column would measure close to 100% here" in md
        could_not_run = "so this check could not run" in md
        assert conclusive == expect_conclusive, f"n_big={n_big}"
        assert could_not_run != expect_conclusive, f"n_big={n_big}"


def test_build_findings_markdown_does_not_judge_ret_scale_when_verdict_cannot():
    # n_big=5 is below ADJUSTMENT_VERDICT_MIN_EVENTS, so split_status is
    # "inconclusive" -- even though actual_ret_abs_median (0.5, from ret
    # always matching naive) is implausibly large, the report must not
    # print a scale judgment the verdict itself declines to make.
    event_stats = neq.summarize_adjustment_events(_events_df(5, 1.0))
    ctx = _minimal_findings_ctx()
    ctx["event_stats"] = event_stats
    ctx["adjustment_verdict"] = neq.classify_adjustment_status(event_stats, ctx["dsf_stats"])
    assert ctx["adjustment_verdict"]["split_status"] == "inconclusive"
    assert ctx["adjustment_verdict"]["ret_scale_status"] == "plausible"  # can't judge -> defaults, doesn't flag
    md = neq.build_findings_markdown(ctx)
    assert "far too large to be an ordinary daily return" not in md


def test_build_findings_markdown_suspiciously_close_threshold_uses_named_constant():
    ctx = _minimal_findings_ctx()
    ctx["crossval_stats"]["return_median_abs_diff"] = 9.9e-5  # just under CROSSVAL_SUSPICIOUSLY_CLOSE_MEDIAN_DIFF
    md = neq.build_findings_markdown(ctx)
    assert "hundred-thousandths" not in md
    assert "rarely agree to that many decimal places" in md


def test_build_findings_markdown_coverage_gap_derived_from_crossval_years(monkeypatch):
    monkeypatch.setattr(neq, "CROSSVAL_YEARS", (2005, 2006))
    ctx = _minimal_findings_ctx()
    ctx["crossval_stats"]["return_comparison_rows_retained"] = 50
    md = neq.build_findings_markdown(ctx)
    assert "1996-2004" in md
    assert "1996-2010" not in md


def test_build_findings_markdown_crossval_verdict_requires_correlation_sanity_floor():
    # Round-5 review finding: frac-within-1pct alone can't rule out a
    # pathological near-zero-correlation case.
    ctx = _minimal_findings_ctx()
    ctx["crossval_stats"]["return_comparison_rows_retained"] = 50
    ctx["crossval_stats"]["return_frac_within_1pct"] = 0.96
    ctx["crossval_stats"]["return_correlation"] = 0.02
    md = neq.build_findings_markdown(ctx)
    assert "**Verdict: this check is inconclusive for this run.**" in md


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


def test_main_end_to_end_exercises_adjustment_evidence_branch(tmp_path, monkeypatch, capsys):
    """The other two main() e2e tests use a single dsf_2011.parquet with
    cfacpr == 1.0 throughout, so detect_adjustment_events finds nothing and
    both AAPL spot-check tables stay empty -- the "evidence" half of the
    adjustment-status section is never actually exercised end to end. This
    test adds a second year, 2005, with a real cfacpr change for AAPL's
    permno straddling AAPL_SPLIT_YEAR_DATES[2005], so main() has to
    populate a non-empty events table and a non-empty aapl_2005 spot-check
    table for real.

    All three main() e2e fixtures are small enough that dividend/scale
    row counts and the event count both stay far below classify_adjustment_
    status's PULL_WIDE_VERDICT_MIN_ROWS/ADJUSTMENT_VERDICT_MIN_EVENTS
    floors, so `overall` always comes back "inconclusive" here and the
    terminal escalation banner in main() fires on every one of these
    tests -- this test is the one that actually asserts on it, covering a
    path that was previously exercised (all three fixtures print it) but
    never checked."""
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
    # Only 1 big-raw-move event -- below ADJUSTMENT_VERDICT_MIN_EVENTS (30),
    # so the report's split bullet now follows the verdict's own gate
    # (split_status == "inconclusive") and reports "could not run" rather
    # than sounding conclusive on a single event.
    assert "Only 1 big-raw-move adjustment event(s)" in findings_text
    assert "so this check could not run" in findings_text
    assert "adjustment events happened across the whole universe" not in findings_text
    # The AAPL 2005 spot-check table is populated (not the empty placeholder).
    assert "2005-02-28" in findings_text

    # This fixture's single real event is far below ADJUSTMENT_VERDICT_MIN_
    # EVENTS, and its row counts are far below PULL_WIDE_VERDICT_MIN_ROWS,
    # so every one of the three checks comes back "inconclusive" and the
    # terminal banner fires -- assert on it directly rather than just on
    # "DONE." (which every run prints regardless of adjustment status).
    captured = capsys.readouterr()
    assert "ADJUSTMENT STATUS: INCONCLUSIVE" in captured.out
    assert "split check:" in captured.out
    assert "dividend check:" in captured.out
    assert "scale check:" in captured.out
    assert "DONE." in captured.out
