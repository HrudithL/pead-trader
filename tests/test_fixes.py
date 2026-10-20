"""Regression tests for the four methodology fixes (open-book leverage cap, point-in-time deciles,
bid/ask option fills, search-window decay). Synthetic, except one check against the committed decile events."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pead.common.paths import DATA_DIR  # noqa: E402
from pead.common.pit_rank import rank_pct  # noqa: E402
from pead.core.data import EquityUniverse  # noqa: E402
from pead.engines.equity import Sizing, run_equity_backtest  # noqa: E402
from pead.engines.options import OptionsSizing, run_options_backtest  # noqa: E402


# ------------------------------------------------------------------------------ fix 1: open-book cap
def _two_cohort_universe(n_per=40, hold=100):
    cal = pd.bdate_range("2000-01-03", periods=260)
    d_q1, d_q2 = 5, 70                                    # entries in Q1 and Q2 of 2000; each held `hold` days -> they overlap
    rows, ev = 0, []
    dates = []
    for start in [d_q1] * n_per + [d_q2] * n_per:
        dates.extend(cal[start - 1:start + hold + 1])    # block of rows: entry row, then `hold` rows, then 1 spare
        ev.append(dict(entry_row_idx=rows + 1, exit_row_idx=rows + 1 + hold, day0_date=cal[start], exit_date=cal[start + hold],
                       avg_dollar_volume_21d=1e12, liquidity_quintile=3))
        rows += hold + 2
    events = pd.DataFrame(ev)
    events["entry_row_idx"] = events["entry_row_idx"].astype(int)
    z = np.zeros(rows)
    return EquityUniverse(events=events, ret_mktadj=z, ret_raw=z, panel_date=np.array(dates, dtype="datetime64[ns]"),
                          calendar=cal, vwretd=np.zeros(len(cal)), initial_capital=1e6)


def test_open_book_cap_bounds_daily_gross_and_legacy_does_not():
    uni = _two_cohort_universe()
    w = np.ones(len(uni.events))
    kw = dict(base_unit_fraction=1.0 / 40, max_gross_leverage=1.0, liquidity_cap_frac=1.0)   # each cohort alone = exactly the cap
    legacy = run_equity_backtest(uni, w, Sizing(exposure_cap="entry_cohort", **kw))
    fixed = run_equity_backtest(uni, w, Sizing(exposure_cap="open_book", **kw))
    qstart = run_equity_backtest(uni, w, Sizing(exposure_cap="open_book_qstart", **kw))
    lev = lambda r: (r.daily["gross_exposure"] / r.daily["nav"]).max()
    assert lev(legacy) > 1.8                       # both cohorts open at once -> ~2x the stated cap
    assert lev(fixed) < 1.02 and lev(qstart) < 1.02
    assert fixed.summary["max_gross_over_nav"] < 1.02 <= legacy.summary["max_gross_over_nav"] * 0.6


def test_open_book_matches_legacy_when_nothing_overlaps():
    uni = _two_cohort_universe(hold=30)            # Q1 cohort exits before Q2 cohort enters
    w = np.ones(len(uni.events))
    kw = dict(base_unit_fraction=1.0 / 40, max_gross_leverage=1.0, liquidity_cap_frac=1.0)
    a = run_equity_backtest(uni, w, Sizing(exposure_cap="entry_cohort", **kw)).daily["nav"].to_numpy()
    b = run_equity_backtest(uni, w, Sizing(exposure_cap="open_book", **kw)).daily["nav"].to_numpy()
    assert np.abs(a - b).max() < 1e-6


# ----------------------------------------------------------------------------- fix 2: point-in-time rank
def test_pit_rank_uses_only_strictly_earlier_quarters():
    rng = np.random.default_rng(0)
    q = pd.Series(np.repeat(pd.period_range("2000Q1", periods=6, freq="Q"), 50))
    v = pd.Series(rng.normal(size=len(q)))
    base = rank_pct(v, q, "prior_quarters")
    assert base[q == q.min()].isna().all() and base[q > q.min()].notna().all()
    late = q >= pd.Period("2000Q4", "Q")
    v2 = v.copy()
    v2[late] = rng.normal(size=int(late.sum()))                                  # change the future
    again = rank_pct(v2, q, "prior_quarters")
    assert np.allclose(base[~late].fillna(-1), again[~late].fillna(-1))          # earlier ranks cannot see later data
    # a SAME-quarter peer must not move an event's rank (the old within-quarter rank let it)
    in_q3 = np.flatnonzero(q == pd.Period("2000Q3", "Q"))
    i, peer = int(in_q3[0]), int(in_q3[1])
    v3 = v.copy()
    v3.iloc[peer] = v.iloc[i] + 10.0 if v.iloc[peer] < v.iloc[i] else v.iloc[i] - 10.0
    assert rank_pct(v3, q, "prior_quarters").iloc[i] == base.iloc[i]
    assert rank_pct(v3, q, "within_quarter").iloc[i] != rank_pct(v, q, "within_quarter").iloc[i]


def test_pit_rank_mode_env_and_ties(monkeypatch):
    from pead.common import pit_rank
    monkeypatch.setenv(pit_rank.MODE_ENV, "within_quarter")
    assert pit_rank.ranking_mode() == "within_quarter"
    monkeypatch.setenv(pit_rank.MODE_ENV, "bogus")
    with pytest.raises(ValueError):
        pit_rank.ranking_mode()
    monkeypatch.delenv(pit_rank.MODE_ENV)
    assert pit_rank.ranking_mode() == "prior_quarters"
    q = pd.Series([0, 0, 1, 1])
    out = rank_pct(pd.Series([1.0, 1.0, 1.0, 2.0]), q, "prior_quarters")    # pool {1,1}: tie -> midpoint 0.5; 2 above all -> 1.0
    assert out.iloc[:2].isna().all() and out.iloc[2] == 0.5 and out.iloc[3] == 1.0


@pytest.mark.skipif(not (DATA_DIR / "decile_events.parquet").exists(), reason="needs the committed decile events")
def test_within_quarter_reproduces_stored_sue_rank_pct_exactly():
    ev = pd.read_parquet(DATA_DIR / "decile_events.parquet")
    got = rank_pct(ev["sue_analyst"].round(8), ev["ann_quarter"], "within_quarter")
    assert np.abs(got - ev["sue_rank_pct"]).max() == 0.0


# ------------------------------------------------------------------------- fix 4: search-window decay
def test_decay_search_fraction_is_configurable_and_defaults_to_holdout():
    src = (REPO / "pead" / "prep" / "equity" / "daily_decay.py").read_text()
    assert 'PEAD_DECAY_SEARCH_FRAC", "0.7"' in src and "vmask = ~np.isnan(col) & search_mask" in src


# -------------------------------------------------------------------------------- fix 3: bid/ask fills
def _option_panel(entry_ask=1.10, exit_bid=0.90):
    cal = pd.bdate_range("2000-01-03", periods=200).to_numpy()
    row = dict(kelly_growth=0.1, kelly_premium=1.0, kelly_fraction=0.5, kelly_side="C", day0_date=pd.Timestamp(cal[5]),
               kelly_fwd_ask_0d=entry_ask, kelly_fwd_bid_0d=2 - entry_ask, kelly_fwd_mid_0d=1.0)
    for h in (1, 5, 10, 20, 40, 60):
        row.update({f"kelly_fwd_mid_{h}d": 1.0, f"kelly_fwd_bid_{h}d": exit_bid if h == 60 else 0.95,
                    f"kelly_fwd_ask_{h}d": 2 - exit_bid if h == 60 else 1.05})
    return pd.DataFrame([row]), cal


def test_bid_ask_fill_pnl_is_exactly_bid_minus_ask():
    panel, cal = _option_panel()
    daily, _, pos, s = run_options_backtest(panel, cal, OptionsSizing(max_premium_frac=0.2, roundtrip_cost_pct=0.0, fill_mode="bid_ask"), 1e7)
    lots = pos["contracts"].iloc[0]
    assert lots > 0 and s["n_entries_filled_at_ask"] == 1
    assert abs(pos["gross_pnl"].iloc[0] - lots * 100 * (0.90 - 1.10)) < 1e-6          # bought at the ask, sold at the bid
    assert abs(daily["daily_pnl"].sum() - pos["gross_pnl"].iloc[0]) < 1e-6
    _, _, pos_mid, _ = run_options_backtest(panel, cal, OptionsSizing(max_premium_frac=0.2, roundtrip_cost_pct=0.0, fill_mode="mid"), 1e7)
    assert abs(pos_mid["gross_pnl"].iloc[0]) < 1e-6                                   # mid in, mid out: no spread paid


def test_mid_fill_ignores_quote_columns_and_needs_none():
    panel, cal = _option_panel()
    slim = panel.drop(columns=[c for c in panel.columns if c.endswith("_0d")])
    run_options_backtest(slim, cal, OptionsSizing(max_premium_frac=0.2, fill_mode="mid"), 1e7)      # must not raise
    try:
        run_options_backtest(slim, cal, OptionsSizing(max_premium_frac=0.2, fill_mode="bid_ask"), 1e7)
        raise AssertionError("bid_ask without entry quotes must refuse to run")
    except ValueError:
        pass


# ------------------------------------------------------------------------------ prep stage runner device
def test_prep_auto_device_resolves_to_a_concrete_value(monkeypatch):
    import builtins
    from pead.prep import stages
    assert stages.resolve_device("cpu") == "cpu" and stages.resolve_device("cuda") == "cuda"
    real_import = builtins.__import__

    def no_cupy(name, *a, **k):
        if name == "cupy":
            raise ImportError("no cupy")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_cupy)
    assert stages.resolve_device("auto") == "cpu"
    cmd = stages._cmd(stages.BY_NAME["optimal_contract_selector"], stages.resolve_device("auto"), 4, False)
    assert cmd[-2:] == ["--device", "cpu"]


@pytest.mark.parametrize("quarterlog", ["empty", "missing", "normal"])
def test_load_result_tolerates_empty_or_missing_quarterlog(tmp_path, monkeypatch, quarterlog):
    """Portfolio (blend) results write an empty quarterlog.csv; reports must still load them."""
    import json
    from pead.core import store
    d = tmp_path / "blend"
    d.mkdir()
    (d / "summary.json").write_text(json.dumps({"asset_class": "portfolio", "summary": {"sharpe": 1.0}, "run": {}}))
    pd.DataFrame({"date": ["2000-01-03"], "daily_pnl": [0.0], "nav": [1.0], "daily_return": [0.0]}).to_csv(d / "daily.csv.gz", index=False)
    if quarterlog == "empty":
        (d / "quarterlog.csv").write_text("\n")
    elif quarterlog == "normal":
        pd.DataFrame({"quarter": ["2000Q1"], "x": [1]}).to_csv(d / "quarterlog.csv", index=False)
    monkeypatch.setattr(store, "RUNS_DIR", tmp_path)
    r = store.load_result("blend")
    assert r.summary["sharpe"] == 1.0
    assert len(r.quarterlog) == (1 if quarterlog == "normal" else 0)
