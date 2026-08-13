"""Tests for scripts/build_equity_data_report.py -- the equity-data audit
PDF report (Phase 1, Slice 4).

None of these tests depend on the real data/normalized_equity/ or
data/raw_equity/master_stock.csv files (multi-hundred-MB, gitignored, not
present in a fresh checkout/CI) -- every test builds a small, fully
synthetic fixture tree under tmp_path and passes it in via
build_equity_data_report()'s path/parameter overrides, matching this
project's existing "drive the real pipeline against fixtures, not the real
multi-GB files" convention (see test_normalize_equity_data.py's docstring).
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import pandas as pd
import pytest
import weasyprint

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_equity_data_report as ber  # noqa: E402
import normalize_equity_data as ned  # noqa: E402

PDF_MAGIC = b"%PDF"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _equity_year_frame(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal equity_<year>.parquet-shaped frame -- just the
    columns build_equity_data_report.py's functions actually read
    (permno/ticker/date/prc/ret/retx/cfacpr/prc_is_bidask_avg), not the
    full 11-column schema normalize_equity_data.py writes."""
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    if "prc_is_bidask_avg" not in df.columns:
        df["prc_is_bidask_avg"] = False
    if "cfacpr" not in df.columns:
        df["cfacpr"] = 1.0
    return df


def _write_equity_year(data_dir: Path, year: int, df: pd.DataFrame) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(data_dir / f"equity_{year}.parquet", index=False)


def _write_master_stock_csv(path: Path, rows: list[tuple]) -> None:
    """rows: (ticker, date_str, price_str) tuples -- written with the real
    file's exact header names (Tic/Date/Price; see
    normalize_equity_data.read_master_stock_raw's docstring)."""
    lines = ["Tic,Date,Price"]
    for ticker, date_str, price_str in rows:
        lines.append(f"{ticker},{date_str},{price_str}")
    path.write_text("\n".join(lines) + "\n")


def _build_fixture_tree(tmp_path: Path) -> dict:
    """One year (1995) of synthetic data with:
    - ticker ZZZ: a clean, well-aligned series used both as the "split
      window" spot-check ticker and as a well-behaved cross-validation
      segment (CRSP and master_stock.csv prices track the same real moves).
    - ticker YYY: a second cross-validation segment whose CRSP and
      master_stock.csv price MOVEMENTS disagree a lot day to day (not just
      a scale difference, which the alignment gate can't see -- see
      cross_validate_master_stock's docstring) so it gets quarantined as
      misaligned, exercising that branch of the report too.
    Returns the paths/overrides build_equity_data_report() needs.
    """
    data_dir = tmp_path / "normalized_equity"
    master_stock_path = tmp_path / "raw_equity" / "master_stock.csv"
    master_stock_path.parent.mkdir(parents=True, exist_ok=True)

    dates = [f"1995-06-{d:02d}" for d in range(1, 9)]  # 8 consecutive days

    zzz_prc = [50.00, 50.50, 51.00, 50.75, 51.25, 51.50, 51.75, 52.00]
    zzz_ret = [0.0] + [zzz_prc[i] / zzz_prc[i - 1] - 1.0 for i in range(1, len(zzz_prc))]

    yyy_prc = [500.0, 510.0, 490.0, 505.0, 495.0, 520.0, 480.0, 525.0]
    yyy_ret = [0.0] + [yyy_prc[i] / yyy_prc[i - 1] - 1.0 for i in range(1, len(yyy_prc))]

    rows = []
    for i, d in enumerate(dates):
        rows.append(
            {
                "permno": 99999,
                "ticker": "ZZZ",
                "date": d,
                "prc": zzz_prc[i],
                "ret": zzz_ret[i],
                "retx": zzz_ret[i],
            }
        )
        rows.append(
            {
                "permno": 88888,
                "ticker": "YYY",
                "date": d,
                "prc": yyy_prc[i],
                "ret": yyy_ret[i],
                "retx": yyy_ret[i],
            }
        )
    equity_1995 = _equity_year_frame(rows)
    _write_equity_year(data_dir, 1995, equity_1995)

    # master_stock.csv: ZZZ tracks the same real moves as CRSP (near-perfect
    # agreement); YYY moves in a completely different day-to-day pattern
    # (misaligned -- a reused-ticker-symbol stand-in).
    master_zzz_prc = zzz_prc  # identical moves -> ~perfect agreement
    master_yyy_prc = [5.00, 5.01, 5.02, 5.01, 5.03, 5.02, 5.04, 5.03]  # tiny, unrelated moves

    master_rows = []
    for i, d in enumerate(dates):
        master_rows.append(("ZZZ", d, f"{master_zzz_prc[i]:.4f}"))
        master_rows.append(("YYY", d, f"{master_yyy_prc[i]:.4f}"))
    _write_master_stock_csv(master_stock_path, master_rows)

    return {
        "data_dir": data_dir,
        "master_stock_path": master_stock_path,
        "crossval_years": (1995,),
        "archive_end_date": "1995-12-31",
        "aapl_ticker": "ZZZ",
        "aapl_split_year": 1995,
        "aapl_split_date": "1995-06-04",
        "window_days": 10,
    }


# ---------------------------------------------------------------------------
# _discover_equity_years / _compute_coverage_and_cleaning_stats
# ---------------------------------------------------------------------------


def test_discover_equity_years_sorts_and_filters(tmp_path):
    for name in ["equity_1996.parquet", "equity_2005.parquet", "equity_1995.parquet", "market_benchmark.parquet"]:
        (tmp_path / name).touch()
    assert ber._discover_equity_years(tmp_path) == [1995, 1996, 2005]


def test_discover_equity_years_empty_dir(tmp_path):
    assert ber._discover_equity_years(tmp_path) == []


def test_compute_coverage_and_cleaning_stats(tmp_path):
    data_dir = tmp_path / "normalized_equity"
    df_1995 = _equity_year_frame(
        [
            {"permno": 1, "ticker": "A", "date": "1995-01-03", "prc": 10.0, "ret": 0.01, "retx": 0.01,
             "prc_is_bidask_avg": True},
            {"permno": 2, "ticker": "B", "date": "1995-01-03", "prc": 20.0, "ret": 0.02, "retx": 0.02,
             "prc_is_bidask_avg": False},
        ]
    )
    df_1996 = _equity_year_frame(
        [
            {"permno": 1, "ticker": "A", "date": "1996-01-03", "prc": 11.0, "ret": -66.0, "retx": -66.0,
             "prc_is_bidask_avg": False},
            {"permno": 3, "ticker": "C", "date": "1996-01-03", "prc": 30.0, "ret": 0.03, "retx": 0.03,
             "prc_is_bidask_avg": False},
        ]
    )
    _write_equity_year(data_dir, 1995, df_1995)
    _write_equity_year(data_dir, 1996, df_1996)

    coverage, stats = ber._compute_coverage_and_cleaning_stats(data_dir)

    assert list(coverage["year"]) == [1995, 1996]
    assert list(coverage["rows"]) == [2, 2]
    assert list(coverage["permnos"]) == [2, 2]
    assert stats["n_years"] == 2
    assert stats["year_min"] == 1995
    assert stats["year_max"] == 1996
    assert stats["total_rows"] == 4
    assert stats["total_distinct_permnos"] == 3  # permnos 1, 2, 3 -- permno 1 appears in both years
    assert stats["bidask_rows"] == 1
    # Fix 12d: bidask_frac's denominator is prc_rows (rows with a price at
    # all), not total_rows -- both happen to be 4 in this fixture (every
    # row has a non-null prc), so the fraction is unchanged, but the stat
    # it's actually keyed against is not.
    assert stats["prc_rows"] == 4
    assert stats["bidask_frac"] == pytest.approx(0.25)
    assert stats["missing_code_rows"] == 1  # the -66.0 sentinel in equity_1996
    # Fix 0: the adjustment-status verdict machinery is wired in and
    # doesn't crash on a tiny fixture -- with only 4 total rows (far below
    # PULL_WIDE_VERDICT_MIN_ROWS) and no cfacpr changes at all, every
    # sub-check is "inconclusive" for lack of evidence, not a false
    # "adjusted" or "flagged".
    assert stats["verdict"]["overall"] == "inconclusive"
    assert stats["event_stats"]["n_events"] == 0
    assert stats["prc_adj_stats"]["n_permnos_total"] == 3


def test_compute_coverage_and_cleaning_stats_raises_on_empty_dir(tmp_path):
    with pytest.raises(FileNotFoundError):
        ber._compute_coverage_and_cleaning_stats(tmp_path)


# ---------------------------------------------------------------------------
# End-to-end: build_equity_data_report() produces a real, valid PDF
# ---------------------------------------------------------------------------


def test_build_equity_data_report_produces_valid_multi_chart_pdf(tmp_path):
    fixture = _build_fixture_tree(tmp_path)
    out = ber.build_equity_data_report(tmp_path / "report.pdf", **fixture)

    assert out.exists()
    data = out.read_bytes()
    assert data[:4] == PDF_MAGIC
    assert data.rstrip().endswith(b"%%EOF")
    # Five narrative sections plus three charts (split, cross-validation,
    # coverage) is substantially more content than an empty report -- a
    # cheap sanity check that the charts actually made it into the PDF.
    assert out.stat().st_size > 20_000

    pages = weasyprint.HTML(filename=str(out)).render().pages
    assert len(pages) >= 3  # five sections + three chart blocks overflow a single A4 page
    assert pages[0].width / 96 * 25.4 == pytest.approx(210, abs=0.1)  # A4 width
    assert pages[0].height / 96 * 25.4 == pytest.approx(297, abs=0.1)  # A4 height


def test_build_equity_data_report_skips_crossval_when_master_stock_has_no_overlap(tmp_path):
    """If master_stock.csv has no rows that overlap the CRSP crossval
    years, cross-validation is skipped (matching normalize_equity_data.
    main()'s own guard) rather than crashing -- the report should still
    build cleanly with just the split and coverage charts."""
    fixture = _build_fixture_tree(tmp_path)
    # Replace master_stock.csv with a file whose only ticker/date pairs are
    # far outside the 1995 crossval window.
    _write_master_stock_csv(fixture["master_stock_path"], [("ZZZ", "2020-01-02", "999.0")])

    out = ber.build_equity_data_report(tmp_path / "report_no_crossval.pdf", **fixture)

    assert out.read_bytes()[:4] == PDF_MAGIC

    # Fix 10: direct assertions on the section-building function itself,
    # not just "the PDF is still valid" -- section 3 must say the
    # cross-check didn't run, and NO section anywhere may still claim an
    # agreement/correlation result or reference the crossval chart (which
    # was never built this run).
    heading, body = ber._build_crossval_section(
        {"skipped": True}, {}, crossval_has_comparison=False,
        chart_numbers={"split": 1, "crossval": None, "coverage": 2},
    )
    assert "did not run" in body
    assert "correlation" not in body
    assert "agree" not in body
    assert "Chart" not in body

    closing_heading, closing_body = ber._build_closing_section(
        {"overall": "inconclusive", "reasons": ["not enough evidence"]},
        crossval_has_comparison=False,
        crossval_stats={"skipped": True},
        chart_numbers={"split": 1, "crossval": None, "coverage": 2},
    )
    # The closing note's cross-check sentence must be entirely omitted --
    # not merely softened -- when the cross-check didn't run this build.
    assert "cross-check" not in closing_body.lower()


def test_build_equity_data_report_crossval_zero_overlap_with_nonempty_inputs(tmp_path):
    """Round-2 fix 3: a DISTINCT gap from the {"skipped": True} case above
    -- here both CRSP and master_stock.csv have real, non-empty, in-window
    rows (so _run_master_stock_crossval does NOT short-circuit to
    {"skipped": True}), but they share zero overlapping (ticker, date)
    keys, so cross_validate_master_stock's join produces matched_rows == 0
    and never sets return_comparison_rows_retained/return_correlation at
    all. Before this fix, the section-building code gated its "these two
    sources agree closely" narrative on whether the crossval merely ran
    (inputs non-empty), not on whether it produced anything to report --
    so it would print an agreement claim here beside no statistic
    whatsoever. This test drives the real report-building path (not just
    the section-building helpers) to confirm the PDF still builds and that
    claim no longer appears."""
    fixture = _build_fixture_tree(tmp_path)
    # master_stock.csv: ticker "QQQ", squarely inside the 1995 crossval
    # window -- non-empty and in-window, but a ticker the CRSP fixture
    # (ZZZ/YYY only) never has, so there are zero overlapping (ticker,
    # date) keys to join on.
    dates = [f"1995-06-{d:02d}" for d in range(1, 9)]
    _write_master_stock_csv(fixture["master_stock_path"], [("QQQ", d, "100.00") for d in dates])

    out = ber.build_equity_data_report(tmp_path / "report_zero_overlap.pdf", **fixture)
    assert out.exists()
    assert out.read_bytes()[:4] == PDF_MAGIC

    # Reproduce the same stats dict build_equity_data_report() computes
    # internally, to check directly that this is really the zero-matched-
    # rows path (not the empty-inputs skip path) and that the crossval
    # section's narrative text has no agreement/correlation/chart claim.
    crsp_crossval = ber._load_crossval_crsp_frame(fixture["data_dir"], fixture["crossval_years"])
    master_raw = ned.read_master_stock_raw(fixture["master_stock_path"], end_date=fixture["archive_end_date"])
    master_clean, master_clean_stats = ned.clean_master_stock_frame(master_raw, end_date=fixture["archive_end_date"])
    assert not crsp_crossval.empty
    assert not master_clean.empty  # confirms this is NOT the {"skipped": True} path

    window_start = max(pd.Timestamp(master_clean["date"].min()), pd.Timestamp(crsp_crossval["date"].min()))
    window_end = min(pd.Timestamp(fixture["archive_end_date"]), pd.Timestamp(crsp_crossval["date"].max()))
    crossval_stats, _ = ned.cross_validate_master_stock(crsp_crossval, master_clean, window_start, window_end)

    assert crossval_stats.get("matched_rows") == 0
    assert "return_comparison_rows_retained" not in crossval_stats
    assert "return_correlation" not in crossval_stats

    crossval_has_comparison = crossval_stats.get("return_comparison_rows_retained", 0) > 0
    assert crossval_has_comparison is False

    heading, body = ber._build_crossval_section(
        crossval_stats, master_clean_stats, crossval_has_comparison=crossval_has_comparison,
        chart_numbers={"split": 1, "crossval": None, "coverage": 2},
    )
    assert "did not run" in body
    assert "correlation" not in body
    assert "agree" not in body
    assert "Chart" not in body


def test_load_ticker_window_resolves_ticker_reassignment_to_center_date_permno(tmp_path):
    """Fix 12b: a ticker symbol can be reassigned to a different company
    over time (unlike a permno, which never is -- see the module's own
    intro text). A window that happens to straddle such a reassignment
    must resolve to a single permno -- the one on the row matching the
    window's own center date -- rather than silently mixing two
    companies' prices into one plotted series."""
    data_dir = tmp_path / "normalized_equity"
    dates = [f"1995-06-{d:02d}" for d in range(1, 9)]
    rows = []
    for i, d in enumerate(dates):
        # First half of the window: permno 111 traded as SAME. Second
        # half (from 06-05 onward): permno 222 has since been assigned
        # the same ticker symbol.
        permno = 111 if i < 4 else 222
        rows.append({
            "permno": permno, "ticker": "SAME", "date": d,
            "prc": 10.0 + i, "ret": 0.01, "retx": 0.01,
        })
    _write_equity_year(data_dir, 1995, _equity_year_frame(rows))

    # Center date 1995-06-06 falls in the permno-222 half.
    window = ber._load_ticker_window(data_dir, 1995, "SAME", "1995-06-06", window_days=10)

    assert set(window["permno"].unique()) == {222}
    assert len(window) == 4  # only the 4 rows belonging to permno 222


def test_load_ticker_window_raises_when_center_date_missing_with_multiple_permnos(tmp_path):
    """Fix 1: when a ticker has multiple permnos in the window AND the
    center_date row is missing, _load_ticker_window must raise ValueError
    instead of silently picking the majority permno (which can be wrong)."""
    data_dir = tmp_path / "normalized_equity"
    dates = ["1995-06-01", "1995-06-02", "1995-06-03", "1995-06-08", "1995-06-09"]
    rows = []
    # Two permnos (111 and 222) sharing ticker "MULTI" on non-overlapping
    # date ranges, with a gap around the center_date we'll request below.
    for d in dates[:3]:
        rows.append({"permno": 111, "ticker": "MULTI", "date": d, "prc": 10.0, "ret": 0.01, "retx": 0.01})
    for d in dates[3:]:
        rows.append({"permno": 222, "ticker": "MULTI", "date": d, "prc": 20.0, "ret": 0.02, "retx": 0.02})
    _write_equity_year(data_dir, 1995, _equity_year_frame(rows))

    # Center_date 1995-06-05 is NOT present in the frame -- the window
    # would span both permnos, but there's no row on the center date
    # itself to disambiguate.
    with pytest.raises(ValueError, match=r"ticker 'MULTI' has multiple permnos.*cannot disambiguate"):
        ber._load_ticker_window(data_dir, 1995, "MULTI", "1995-06-05", window_days=10)


def test_format_split_caption_returns_numberless_branch_on_zero_prev_price(tmp_path):
    """Fix 4: when prev_price is <= 0, _format_split_caption must fall
    through to the numberless branch instead of dividing by zero."""
    data_dir = tmp_path / "normalized_equity"
    rows = [
        {"permno": 99, "ticker": "ZERO", "date": "1995-06-03", "prc": 0.0, "ret": 0.0, "retx": 0.0},
        {"permno": 99, "ticker": "ZERO", "date": "1995-06-04", "prc": 50.0, "ret": 0.0, "retx": 0.0},
    ]
    _write_equity_year(data_dir, 1995, _equity_year_frame(rows))

    window_df = ber._load_ticker_window(data_dir, 1995, "ZERO", "1995-06-04", window_days=3)
    caption = ber._format_split_caption(window_df, "ZERO", "1995-06-04")

    # The numberless branch does not emit the direction tokens ("fell",
    # "rose", "crash", "spike") that the numbered branch does -- assert
    # their absence rather than hardcoding the exact wording.
    assert "fell" not in caption
    assert "rose" not in caption
    assert "crash" not in caption
    assert "spike" not in caption


def test_format_split_caption_returns_numberless_branch_on_zero_naive_pct(tmp_path):
    """Fix 4: when abs(naive_pct) < 1e-4, _format_split_caption must fall
    through to the numberless branch (e.g. a non-split date passed in)."""
    data_dir = tmp_path / "normalized_equity"
    rows = [
        {"permno": 99, "ticker": "FLAT", "date": "1995-06-03", "prc": 100.0, "ret": 0.0, "retx": 0.0},
        {"permno": 99, "ticker": "FLAT", "date": "1995-06-04", "prc": 100.0, "ret": 0.0, "retx": 0.0},
    ]
    _write_equity_year(data_dir, 1995, _equity_year_frame(rows))

    window_df = ber._load_ticker_window(data_dir, 1995, "FLAT", "1995-06-04", window_days=3)
    caption = ber._format_split_caption(window_df, "FLAT", "1995-06-04")

    # Same assertion as the prev_price guard test above.
    assert "fell" not in caption
    assert "rose" not in caption
    assert "crash" not in caption
    assert "spike" not in caption


def test_split_caption_reports_real_computed_numbers(tmp_path):
    fixture = _build_fixture_tree(tmp_path)
    window_df = ber._load_ticker_window(
        fixture["data_dir"], fixture["aapl_split_year"], fixture["aapl_ticker"],
        fixture["aapl_split_date"], fixture["window_days"],
    )
    caption = ber._format_split_caption(window_df, fixture["aapl_ticker"], fixture["aapl_split_date"])

    # 1995-06-04 in the fixture: prc goes from 51.00 (06-03) to 50.75
    # (06-04), a naive -0.49% move -- the caption must state that computed
    # number, not a hardcoded one.
    assert "ZZZ" in caption
    assert "0.49%" in caption
    assert "fell" in caption


# ---------------------------------------------------------------------------
# _is_partial_final_year (Round-2 fix 6)
# ---------------------------------------------------------------------------


def test_is_partial_final_year_real_2011_is_not_partial():
    """The real data's 2011 is a genuinely COMPLETE year whose actual last
    trading day is 2011-12-30 (Dec 31 fell on a Saturday) -- only 1 day
    short of Dec 31st. A strict exact-Dec-31 comparison would misclassify
    this as partial; the calendar-slack tolerance must not."""
    assert ber._is_partial_final_year("2011-12-30", 2011) is False


def test_is_partial_final_year_real_2014_is_partial():
    """The real data's final year, 2014, genuinely ends 2014-06-30 -- a
    real truncation, nowhere close to Dec 31, and must still be flagged as
    partial despite the calendar-slack tolerance added for the 2011 case
    above."""
    assert ber._is_partial_final_year("2014-06-30", 2014) is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
