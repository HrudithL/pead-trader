"""Tests for scripts/build_event_study_stats.py -- rank-normalization,
per-horizon summary statistics, quantile sort + monotonicity, and Spearman
IC (Phase I, Slice 9).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats as sps

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_event_study_stats as bess  # noqa: E402


# ---------------------------------------------------------------------------
# compute_sue_rank_pct -- rank-normalization within each sue_source group.
# ---------------------------------------------------------------------------


def test_compute_sue_rank_pct_produces_0_to_1_distribution_within_each_group():
    # Two sue_source groups on very different raw scales (analyst-like,
    # seasonal-like) -- the whole point of rank normalization is that each
    # group's OWN percentile rank spans 0..1 regardless of its raw scale.
    df = pd.DataFrame({
        "sue_primary": [100.0, 200.0, 300.0, 400.0, 1.0, 2.0, 3.0, 4.0],
        "sue_source": ["analyst"] * 4 + ["seasonal"] * 4,
    })
    ranks = bess.compute_sue_rank_pct(df)

    analyst_ranks = ranks[df["sue_source"] == "analyst"].tolist()
    seasonal_ranks = ranks[df["sue_source"] == "seasonal"].tolist()
    # pandas rank(pct=True) on 4 distinct ascending values -> 0.25, 0.5, 0.75, 1.0
    assert analyst_ranks == pytest.approx([0.25, 0.5, 0.75, 1.0])
    assert seasonal_ranks == pytest.approx([0.25, 0.5, 0.75, 1.0])
    # Despite raw values differing by two orders of magnitude between groups,
    # both groups' ranks land in exactly the same 0-1 range.
    assert ranks.min() == pytest.approx(0.25)
    assert ranks.max() == pytest.approx(1.0)


def test_compute_sue_rank_pct_null_sue_primary_stays_null():
    df = pd.DataFrame({
        "sue_primary": [1.0, np.nan, 3.0],
        "sue_source": ["analyst", "analyst", "analyst"],
    })
    ranks = bess.compute_sue_rank_pct(df)
    assert pd.isna(ranks.iloc[1])
    assert not pd.isna(ranks.iloc[0])
    assert not pd.isna(ranks.iloc[2])


def test_compute_sue_rank_pct_all_null_group_is_all_null_not_an_error():
    # sue_source == "none" in the real event table has sue_primary entirely
    # null by construction -- ranking must not raise, and must leave that
    # group entirely null (nothing to rank).
    df = pd.DataFrame({
        "sue_primary": [np.nan, np.nan, 5.0],
        "sue_source": ["none", "none", "analyst"],
    })
    ranks = bess.compute_sue_rank_pct(df)
    assert ranks.iloc[0:2].isna().all()
    assert ranks.iloc[2] == pytest.approx(1.0)


def test_compute_sue_rank_pct_groups_are_independent_of_each_other():
    # A value that is the SMALLEST in group A but would be the LARGEST in
    # group B must rank low in A and high in B -- groups never see each
    # other's values.
    df = pd.DataFrame({
        "sue_primary": [10.0, 20.0, 30.0, 1.0, 2.0, 10.0],
        "sue_source": ["A", "A", "A", "B", "B", "B"],
    })
    ranks = bess.compute_sue_rank_pct(df)
    # 10.0 is the minimum in group A (rank 1/3) and the maximum in group B (rank 1.0).
    assert ranks.iloc[0] == pytest.approx(1 / 3)
    assert ranks.iloc[5] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# summary_stats -- hand-computable t-stat/CI against a tiny fixture.
# ---------------------------------------------------------------------------


def test_summary_stats_matches_hand_computed_values():
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    result = bess.summary_stats(x, confidence=0.95)

    expected_mean = 3.0
    expected_median = 3.0
    expected_sd = float(np.std(x, ddof=1))  # sqrt(2.5) = 1.5811388300841898
    expected_se = expected_sd / np.sqrt(5)
    expected_t = expected_mean / expected_se
    expected_t_crit = float(sps.t.ppf(0.975, df=4))
    expected_ci_low = expected_mean - expected_t_crit * expected_se
    expected_ci_high = expected_mean + expected_t_crit * expected_se

    assert result["n"] == 5
    assert result["mean"] == pytest.approx(expected_mean)
    assert result["median"] == pytest.approx(expected_median)
    assert result["sd"] == pytest.approx(expected_sd)
    assert result["se"] == pytest.approx(expected_se)
    assert result["t_stat"] == pytest.approx(expected_t)
    assert result["ci_low"] == pytest.approx(expected_ci_low)
    assert result["ci_high"] == pytest.approx(expected_ci_high)
    # Sanity-check against the literal hand-computed numbers too, not just
    # numpy/scipy re-derivations of the same formula.
    assert result["sd"] == pytest.approx(1.5811388300841898)
    assert result["se"] == pytest.approx(0.7071067811865476)
    assert result["t_stat"] == pytest.approx(4.242640687119285)


def test_summary_stats_negative_mean_gives_negative_t_stat():
    x = np.array([-1.0, -2.0, -3.0, -4.0, -5.0])
    result = bess.summary_stats(x)
    assert result["mean"] == pytest.approx(-3.0)
    assert result["t_stat"] < 0
    assert result["ci_low"] < result["ci_high"]


def test_summary_stats_n_zero_returns_all_none_except_n():
    result = bess.summary_stats(np.array([]))
    assert result["n"] == 0
    assert result["mean"] is None
    assert result["sd"] is None
    assert result["t_stat"] is None
    assert result["ci_low"] is None


def test_summary_stats_n_one_sd_and_dependents_are_none():
    # Sample variance (ddof=1) is undefined at n=1 -- must not silently
    # divide by zero or return a bogus 0/0-derived t-stat.
    result = bess.summary_stats(np.array([5.0]))
    assert result["n"] == 1
    assert result["mean"] == pytest.approx(5.0)
    assert result["sd"] is None
    assert result["se"] is None
    assert result["t_stat"] is None
    assert result["ci_low"] is None
    assert result["ci_high"] is None


def test_summary_stats_zero_variance_gives_none_t_stat_not_inf():
    # All values identical -> sd == 0 -> se == 0 -> a real 0/0, not a
    # meaningful t-stat (mirrors welch_group_stats's se>0 guard in
    # build_equity_events.py).
    result = bess.summary_stats(np.array([2.0, 2.0, 2.0]))
    assert result["sd"] == pytest.approx(0.0)
    assert result["se"] == pytest.approx(0.0)
    assert result["t_stat"] is None
    assert result["ci_low"] is None


# ---------------------------------------------------------------------------
# quantile_sort / monotonicity_check -- known monotonic and non-monotonic
# synthetic fixtures.
# ---------------------------------------------------------------------------


def test_monotonicity_check_strictly_increasing_is_monotonic():
    result = bess.monotonicity_check([-0.02, -0.01, 0.0, 0.01, 0.02])
    assert result["strict_monotonic"] is True
    assert result["spearman_rho"] == pytest.approx(1.0)
    assert result["spearman_p"] < 0.05


def test_monotonicity_check_non_monotonic_sequence_reported_honestly():
    # Deliberately non-monotonic: Q3 dips below Q2.
    result = bess.monotonicity_check([-0.02, 0.01, -0.01, 0.02, 0.03])
    assert result["strict_monotonic"] is False
    # Softer measure still computed and reported, not hidden.
    assert result["spearman_rho"] is not None
    assert -1.0 <= result["spearman_rho"] <= 1.0


def test_quantile_sort_recovers_known_monotonic_relationship():
    # rank_col strictly increasing with value_col by construction -> the
    # resulting quintile means must be strictly increasing (monotonic=True).
    n = 500
    rank_col = np.linspace(0.001, 0.999, n)
    value_col = rank_col * 0.10 + np.zeros(n)  # noiseless, deterministic
    df = pd.DataFrame({"rank": rank_col, "value": value_col})

    records, mono = bess.quantile_sort(df, "rank", "value", n_quantiles=5)
    assert len(records) == 5
    assert [r["quintile"] for r in records] == [1, 2, 3, 4, 5]
    means = [r["mean"] for r in records]
    assert means == sorted(means)
    assert mono["strict_monotonic"] is True
    assert mono["spearman_rho"] == pytest.approx(1.0)


def test_quantile_sort_reports_non_monotonic_relationship_honestly():
    # A U-shaped relationship: value_col is high at both extremes of
    # rank_col and low in the middle -- quintile means must NOT be reported
    # as monotonic, and the honest (non-monotonic) result must show up in
    # both the strict check and a weak/negative-ish Spearman rho.
    n = 500
    rank_col = np.linspace(0.001, 0.999, n)
    value_col = (rank_col - 0.5) ** 2  # U-shaped: high at edges, low at center
    df = pd.DataFrame({"rank": rank_col, "value": value_col})

    records, mono = bess.quantile_sort(df, "rank", "value", n_quantiles=5)
    means = [r["mean"] for r in records]
    assert mono["strict_monotonic"] is False
    # U-shape: middle quintile mean must be lower than the two extremes.
    assert means[2] < means[0]
    assert means[2] < means[4]


def test_quantile_sort_drops_rows_with_null_rank_or_value():
    df = pd.DataFrame({
        "rank": [0.1, 0.2, np.nan, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
        "value": [1.0, 2.0, 3.0, np.nan, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0],
    })
    records, _ = bess.quantile_sort(df, "rank", "value", n_quantiles=5)
    total_n = sum(r["n"] for r in records)
    # Rows at index 2 (null rank) and index 3 (null value) both drop out --
    # 8 of the original 10 rows remain.
    assert total_n == 8


def test_quantile_sort_quintile_1_is_lowest_rank_quintile_5_is_highest():
    n = 100
    rank_col = np.linspace(0.0, 1.0, n)
    # value_col deliberately in the OPPOSITE order of rank_col's index, so
    # a labeling bug (e.g. reversed quintile order) would flip the sign of
    # the relationship rather than just failing a magnitude check.
    value_col = -rank_col
    df = pd.DataFrame({"rank": rank_col, "value": value_col})
    records, mono = bess.quantile_sort(df, "rank", "value", n_quantiles=5)
    q1_mean = next(r["mean"] for r in records if r["quintile"] == 1)
    q5_mean = next(r["mean"] for r in records if r["quintile"] == 5)
    assert q1_mean > q5_mean  # lowest rank quintile has the highest (least negative) value


# ---------------------------------------------------------------------------
# spearman_ic -- hand-computable/scipy cross-checked.
# ---------------------------------------------------------------------------


def test_spearman_ic_matches_scipy_directly():
    rng = np.random.default_rng(42)
    x = rng.normal(size=200)
    y = 0.3 * x + rng.normal(size=200) * 0.5

    result = bess.spearman_ic(x, y)
    expected_rho, expected_p = sps.spearmanr(x, y)

    assert result["n"] == 200
    assert result["ic"] == pytest.approx(float(expected_rho))
    assert result["p_value"] == pytest.approx(float(expected_p))


def test_spearman_ic_perfect_monotonic_relationship_is_rho_one():
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    y = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
    result = bess.spearman_ic(x, y)
    assert result["ic"] == pytest.approx(1.0)
    assert result["p_value"] < 0.05


def test_spearman_ic_perfect_inverse_relationship_is_rho_negative_one():
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    y = np.array([50.0, 40.0, 30.0, 20.0, 10.0])
    result = bess.spearman_ic(x, y)
    assert result["ic"] == pytest.approx(-1.0)


def test_spearman_ic_n_less_than_two_is_none():
    result = bess.spearman_ic(np.array([1.0]), np.array([2.0]))
    assert result["n"] == 1
    assert result["ic"] is None
    assert result["p_value"] is None


# ---------------------------------------------------------------------------
# build_results -- integration across a small synthetic event table.
# ---------------------------------------------------------------------------


def _synthetic_day0_ok_events(n_per_source=200, seed=7) -> pd.DataFrame:
    """A day0_status=='ok'-shaped dataframe (the module never looks at
    day0_status itself -- that filter happens in load_day0_ok_events, at
    the DuckDB read) with a real, noisy but monotonic surprise-return
    relationship built in, across two sue_source groups on very different
    raw scales -- exercises rank normalization, summary stats, quantile
    sort, and Spearman IC together the way build_results actually calls
    them."""
    rng = np.random.default_rng(seed)
    rows = []
    for source, scale in (("analyst", 9.0), ("seasonal", 3.0)):
        raw_sue = rng.normal(scale=scale, size=n_per_source)
        rows.append(pd.DataFrame({
            "sue_primary": raw_sue,
            "sue_source": source,
        }))
    df = pd.concat(rows, ignore_index=True)
    rank = df.groupby("sue_source")["sue_primary"].rank(pct=True)
    noise = rng.normal(scale=0.02, size=len(df))
    signal = (rank - 0.5) * 0.05  # monotonic in rank, same for every horizon here
    for h in bess.HORIZONS:
        df[f"ret_fwd_{h}d"] = signal + noise + h * 0.0001
        df[f"ret_fwd_{h}d_mktadj"] = signal + noise
    df["permno_contested"] = False
    df["n_events_same_permno_day0"] = 1
    return df


def test_build_results_end_to_end_on_synthetic_fixture():
    df = _synthetic_day0_ok_events()
    results = bess.build_results(df)

    assert results["meta"]["n_day0_ok"] == len(df)
    assert len(results["summary_stats"]) == len(bess.HORIZONS) * len(bess.RETURN_KINDS)
    assert len(results["spearman_ic"]) == len(bess.HORIZONS) * len(bess.RETURN_KINDS)
    # Built-in monotonic signal -> every horizon's IC should come back
    # positive and every quintile sort should come back monotonic.
    for rec in results["spearman_ic"]:
        assert rec["ic"] > 0
    for mono in results["monotonicity"]:
        assert mono["strict_monotonic"] is True


def test_build_results_asserts_on_empty_input():
    with pytest.raises(AssertionError):
        bess.build_results(pd.DataFrame({
            "sue_primary": pd.Series(dtype="float64"),
            "sue_source": pd.Series(dtype="object"),
            "permno_contested": pd.Series(dtype="bool"),
            "n_events_same_permno_day0": pd.Series(dtype="int64"),
            **{f"ret_fwd_{h}d{suffix}": pd.Series(dtype="float64") for h in bess.HORIZONS for _, suffix in bess.RETURN_KINDS},
        }))


def test_render_findings_smoke():
    df = _synthetic_day0_ok_events()
    results = bess.build_results(df)
    findings = bess.render_findings(results)

    assert "# Event-study statistics" in findings
    assert "Rank-normalizing" in findings
    assert "Quantile sort" in findings
    assert "Spearman IC" in findings
    assert "No clustering" in findings
    assert "Duplicate firm-days and contested permnos" in findings


# ---------------------------------------------------------------------------
# main() -- end-to-end against the real data/events archive, skipped if not
# present (matches this project's existing real-data test convention, e.g.
# tests/test_clean_ibes_earnings.py's _real_data_available()).
# ---------------------------------------------------------------------------


def test_main_end_to_end_against_real_data(tmp_path, monkeypatch, capsys):
    if not bess.EVENTS_PATH.exists():
        pytest.skip("real data/events/equity_events_1996_2013.parquet not present in this environment")

    out_dir = tmp_path / "results"
    monkeypatch.setattr(bess, "OUTPUT_DIR", out_dir)
    monkeypatch.setattr(bess, "OUTPUT_PATH", out_dir / "event_study_stats.json")
    monkeypatch.setattr(bess, "FINDINGS_PATH", out_dir / "FINDINGS.md")

    bess.main()

    import json
    results = json.loads((out_dir / "event_study_stats.json").read_text())
    assert results["meta"]["n_day0_ok"] > 0
    assert len(results["summary_stats"]) == len(bess.HORIZONS) * len(bess.RETURN_KINDS)
    assert len(results["spearman_ic"]) == len(bess.HORIZONS) * len(bess.RETURN_KINDS)
    for rec in results["summary_stats"]:
        assert rec["n"] > 0

    findings_text = (out_dir / "FINDINGS.md").read_text()
    assert "# Event-study statistics" in findings_text
    captured = capsys.readouterr()
    assert "Wrote results to" in captured.out
    assert "Wrote findings to" in captured.out


def test_load_day0_ok_events_filters_and_projects(tmp_path):
    path = tmp_path / "events.parquet"
    cols = {"sue_primary": [1.0, 2.0], "sue_source": ["analyst", "analyst"],
            "permno_contested": [False, True], "n_events_same_permno_day0": [1, 2],
            "day0_status": ["ok", "no_trading_day_at_or_after_anndats"]}
    for h in bess.HORIZONS:
        cols[f"ret_fwd_{h}d"] = [0.01, 0.02]
        cols[f"ret_fwd_{h}d_mktadj"] = [0.005, 0.015]
    df = pd.DataFrame(cols)
    df.to_parquet(path, index=False)

    out = bess.load_day0_ok_events(path)
    assert len(out) == 1  # only the day0_status=="ok" row survives
    assert "day0_status" not in out.columns  # projected away -- not in EVENT_LOAD_COLUMNS
    assert set(bess.EVENT_LOAD_COLUMNS) == set(out.columns)
