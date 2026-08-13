"""Builds the earnings-event table with forward equity returns -- Phase I,
Slice 8 ("Event table assembly") of memory/phase1-equity-pead-plan.md.

Joins data/earnings/ibes_clean_1995_2014.parquet (one row per (ibes_ticker,
fiscal quarter), already carrying sue_analyst/sue_seasonal/sue_primary) to
data/normalized_equity/ on permno, aligns each announcement to a trading-day
"day 0", and computes forward raw + market-adjusted cumulative returns at
+1/+5/+10/+20/+40/+60 trading days. One row per input earnings row is always
kept (tag, don't filter) -- see data/events/FINDINGS.md for exclusion counts.

Key decisions (numbers behind each are in FINDINGS.md):

1. Identifier resolution. ibes_clean's `permno` is usually a single value but
   is sometimes a comma-joined multi-candidate string. Its own
   `identifier_ambiguous` conflates permno AND gvkey ambiguity, which is the
   wrong flag for an equity join -- see resolve_permno()'s docstring. A
   multi-candidate permno is resolved by checking which candidate(s) have
   normalized_equity coverage bracketing anndats: exactly one bracketing
   candidate resolves it; zero or 2+ is left unresolved (tagged, not
   guessed). Coverage bracketing proves a candidate traded then, not that it
   is exclusively this ticker's -- flag_contested_permnos() separately tags
   rows where the same resolved permno is also claimed by a different
   ticker for an overlapping anndats range (almost always an upstream
   IBES<->CRSP link ambiguity, not something arbitrable here).
2. Day 0. The next trading day at/after anndats, in the RESOLVED PERMNO's OWN
   trading-day sequence (not a market-wide calendar), shifted one further
   trading day for after-market announcements -- see find_day0_index(). The
   shift only applies when the roll-forward landed exactly on anndats (i.e.
   anndats itself was a real session); if anndats wasn't a trading day, the
   rolled-forward session hasn't happened yet and needs no further shift.
   anntims's 00:00:00 "time unknown" sentinel (~13% of in-window rows) is
   treated as after-market: empirically, most sentinel rows' reaction shows
   up at day0+1 rather than day0, i.e. they are genuinely after-market more
   often than not, not merely "safer to assume so" -- see FINDINGS.md. The
   `anntims_unknown` column keeps this recoverable/filterable.
3. Forward returns. Cumulative return over the h trading days strictly AFTER
   day 0 (day 0's own reaction-day return is excluded from every horizon),
   raw and market-adjusted. Market-adjusted = cumulative stock return minus
   cumulative CRSP dsi.vwretd return over the exact same h dates -- both legs
   are total return (dividend-inclusive): `ret` includes dividends, and
   vwretd (the value-weighted market index's total return) matches that
   convention, unlike dsi.sprtrn (S&P 500 price return, dividend-exclusive),
   which would bias every `*_mktadj` column upward by the market's dividend
   yield over the window. Both sides read off the same per-permno rows, so
   the two legs are matched by date even through a gap in that security's
   own trading history (the benchmark leg simply omits the market's return
   on days the security didn't trade) -- see forward_window_return().
   Daily-frequency data can't fully separate an intraday/pre-open
   announcement's own reaction from its first drift day; see FINDINGS.md's
   Returns methodology section for the honest caveat.
4. Delisting. normalized_equity does not merge dsedelist (unchanged, out of
   scope); a security's panel simply ends at its last trade, so any horizon
   needing a row past that point is null via the same end-of-panel bounds
   check used for the archive's own right edge -- no separate code path.
   `n_fwd_sessions` records how many sessions actually remained after day0,
   so a null return is distinguishable as "ran out of panel" vs. "hit a
   missing return inside an otherwise-long-enough window." In practice this
   attrition is ~entirely delisting, not the archive's 2014-06-30 edge (the
   buffer past 2013 is long enough) -- see the attrition comparison in
   FINDINGS.md, gated on statistical significance rather than asserted
   outright.
5. Event window. Output is filtered to anndats in [1996-01-01, 2013-12-31]
   -- the project's core study window. The 1995 lookback and 2014-H1 forward
   buffer in ibes_clean exist only to support seasonal-SUE history and
   forward-return computation and are used for that (a 2013 event's +60d
   return legitimately reads 2014 equity rows); they are not events in their
   own right and are dropped before resolution/day-0 work even starts.

Usage:
    python scripts/build_equity_events.py
Reads data/earnings/ibes_clean_1995_2014.parquet and
data/normalized_equity/ (read-only). Writes data/events/.
"""

import datetime as dt
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
EARNINGS_PATH = REPO_ROOT / "data" / "earnings" / "ibes_clean_1995_2014.parquet"
NORMALIZED_EQUITY_DIR = REPO_ROOT / "data" / "normalized_equity"
MARKET_BENCHMARK_PATH = NORMALIZED_EQUITY_DIR / "market_benchmark.parquet"
OUTPUT_DIR = REPO_ROOT / "data" / "events"
OUTPUT_PATH = OUTPUT_DIR / "equity_events_1996_2013.parquet"
FINDINGS_PATH = OUTPUT_DIR / "FINDINGS.md"

EARNINGS_USECOLS = [
    "ibes_ticker", "permno", "gvkey", "fpedats", "anndats", "anntims",
    "sue_analyst", "sue_seasonal", "sue_primary", "sue_source",
]

# The project's core study window (see module docstring, Decision 5). Events
# outside this range exist in ibes_clean purely as lookback/forward buffer.
EVENT_WINDOW_START = pd.Timestamp("1996-01-01")
EVENT_WINDOW_END = pd.Timestamp("2013-12-31")

HORIZONS = (1, 5, 10, 20, 40, 60)
# `ret` (the stock leg, used everywhere in this table) is CRSP total return
# (dividend-inclusive). `vwretd` is CRSP's value-weighted market index total
# return -- also dividend-inclusive -- so both legs of the market adjustment
# are on the same total-return convention. `sprtrn` (S&P 500, price only,
# dividend-exclusive) would silently bias every `*_mktadj` column upward by
# roughly its dividend yield; it stays in market_benchmark.parquet for
# anyone who wants that as a separate robustness check, but is not used
# here. See module docstring Decision 3 and FINDINGS.md's Returns
# methodology section.
MARKET_RET_COL = "vwretd"

# US market close, ET. anntims >= this is treated as after-market (see
# Decision 2).
AFTER_MARKET_THRESHOLD = dt.time(16, 0, 0)

# IBES's "announcement time unknown" sentinel. Defaulted to after-market
# (Decision 2, revised after review): locating each sentinel row's actual
# reaction day empirically (via the SUE-spread jump in market-adjusted
# returns at day0 vs day0+1) shows the jump sits mostly at day0+1, not
# day0 -- i.e. most sentinel rows are genuinely after-market, not merely
# "safer to assume so." A wrong after-market guess only loses one legitimate
# drift day; a wrong before-market guess (the old default) folds a same-
# session reaction jump into what gets reported as "+1 day of drift,"
# contaminating the exact quantity a PEAD study measures. See FINDINGS.md.
# (The market-benchmark switch from sprtrn to vwretd, above, shifts every
# market-adjusted return by roughly the same additive amount per horizon --
# that shift cancels in a day0-vs-day0+1 comparison, so it does not affect
# this empirical justification.)
ANNTIMS_UNKNOWN = dt.time(0, 0, 0)


# ---------------------------------------------------------------------------
# Loading.
# ---------------------------------------------------------------------------


def load_earnings(path=EARNINGS_PATH) -> pd.DataFrame:
    """Reads ibes_clean's event-identity + surprise columns and filters to
    the core study window (Decision 5). Returns a fresh 0..n-1 index."""
    df = pd.read_parquet(path, columns=EARNINGS_USECOLS)
    # ibes_clean parses anndats with errors="raise", so a null here would
    # mean that guarantee broke upstream -- a tripwire, not a real code
    # path: `.between()` silently treats NaT as "not in window" and would
    # otherwise drop such a row before it ever reaches "tag, don't filter."
    assert df["anndats"].notna().all(), "load_earnings: found a null anndats -- upstream guarantee broke"
    # Same tripwire logic for the other two columns this table cannot do
    # without: a null permno would blow up resolve_permno's .split(",") with
    # an opaque AttributeError, and a null anntims would blow up the
    # after-market comparison with an opaque TypeError. Latent today -- zero
    # nulls in either column in-window -- but cheap insurance against an
    # opaque crash if that upstream guarantee ever breaks.
    assert df["permno"].notna().all(), "load_earnings: found a null permno -- upstream guarantee broke"
    assert df["anntims"].notna().all(), "load_earnings: found a null anntims -- upstream guarantee broke"
    in_window = df["anndats"].between(EVENT_WINDOW_START, EVENT_WINDOW_END)
    return df.loc[in_window].reset_index(drop=True)


def load_equity_with_market(equity_dir=NORMALIZED_EQUITY_DIR, market_path=MARKET_BENCHMARK_PATH) -> pd.DataFrame:
    """One row per (permno, date) with that day's own stock return and the
    same day's market return side by side -- doing this join once, up front,
    on `date` (an exact calendar match) is what makes forward_window_return's
    per-permno windows automatically "matched by date" on the market side,
    with no separate range lookup. DuckDB does the projection (only
    permno/date/ret out of normalized_equity's 11 columns) and the join so
    the ~20M-row panel is never materialized wider than needed."""
    con = duckdb.connect()
    equity_glob = str(equity_dir / "equity_*.parquet")
    query = f"""
        select e.permno, e.date, e.ret, m.{MARKET_RET_COL} as mkt_ret
        from read_parquet(?) e
        left join read_parquet(?) m on e.date = m.date
        order by e.permno, e.date
    """
    return con.execute(query, [equity_glob, str(market_path)]).df()


# ---------------------------------------------------------------------------
# Identifier resolution (Decision 1).
# ---------------------------------------------------------------------------


def parse_permno_candidates(permno_raw: str) -> list[int]:
    return [int(x) for x in permno_raw.split(",")]


def compute_permno_coverage(equity: pd.DataFrame) -> dict[int, tuple[pd.Timestamp, pd.Timestamp]]:
    """permno -> (min_date, max_date) across normalized_equity -- the
    "does this candidate even trade around this announcement" check used by
    resolve_permno(). Bracketing is necessary, not sufficient, for a good
    match (a mid-history trading gap can still fall inside [min, max]) --
    find_day0_index() separately guards the exact-anndats edge case."""
    g = equity.groupby("permno")["date"].agg(["min", "max"])
    return {int(permno): (row["min"], row["max"]) for permno, row in g.iterrows()}


def resolve_permno(permno_raw: str, anndats: pd.Timestamp, coverage: dict) -> tuple[int | None, bool, str]:
    """Returns (resolved_permno_or_None, permno_ambiguous, status).

    ibes_clean's own `identifier_ambiguous` column is True whenever EITHER
    permno OR gvkey resolved to multiple candidates -- gvkey ambiguity is
    irrelevant here (gvkey is never used to join equity data), so a row like
    ibes_ticker="AA" (single clean permno, ambiguous gvkey) would be wrongly
    treated as unresolvable if that upstream column were reused directly.
    This function re-derives ambiguity from `permno` alone.

    A single candidate is used as-is regardless of its own coverage -- if it
    turns out to have no usable equity data, that surfaces later as a
    distinct, more informative day0_status ("no equity data" vs "don't even
    know which company this is"), not as a resolution failure here.

    A multi-candidate permno is resolved via coverage bracketing: exactly
    one candidate whose [min_date, max_date] brackets anndats wins; zero or
    2+ bracketing candidates is left unresolved (status explains which).

    `coverage` (from compute_permno_coverage) is computed once over the
    FULL normalized_equity sample, not point-in-time as of each `anndats`
    -- so resolving an ambiguous row can use a candidate's post-anndats
    trading history. This only affects which candidate permno gets picked
    here; it never reaches the forward-return computation itself, which is
    strictly forward from day 0 regardless of how permno was resolved.
    """
    candidates = parse_permno_candidates(permno_raw)
    if len(candidates) == 1:
        return candidates[0], False, "single_candidate"

    bracketing = [c for c in candidates if c in coverage and coverage[c][0] <= anndats <= coverage[c][1]]
    if len(bracketing) == 1:
        return bracketing[0], True, "resolved_by_coverage"
    if len(bracketing) == 0:
        return None, True, "unresolved_no_bracketing_coverage"
    return None, True, "unresolved_multiple_bracketing_coverage"


def flag_contested_permnos(df: pd.DataFrame) -> pd.Series:
    """True where the resolved `permno` is also claimed by a DIFFERENT
    ibes_ticker with an overlapping anndats range -- e.g. two tickers that
    both carry the same ambiguous permno_raw candidate list and both
    resolve to the same candidate for the same period. Coverage bracketing
    (resolve_permno) only proves a candidate traded then, not that it
    belongs to this ticker and no other -- almost always an upstream IBES
    ticker<->permno link issue (most such rows were never permno_ambiguous
    at all), not something resolvable from this table alone. Does not
    change permno/day0/returns -- a trust flag only, per tag-don't-filter.

    `permno_contested` is a permno-LEVEL flag: once a permno is contested,
    EVERY row resolving to that permno is flagged, including rows whose own
    `anndats` falls outside the actual overlapping span between the
    colliding tickers. This is intentionally conservative (same call as
    round 1's) -- narrowing the flag to only the overlapping sub-range would
    require deciding, per row, which of the colliding tickers it "really"
    belongs to, which is exactly the upstream ambiguity this function can
    surface but not arbitrate.
    """
    resolved = df.loc[df["permno"].notna(), ["permno", "ibes_ticker", "anndats"]]
    spans = resolved.groupby(["permno", "ibes_ticker"])["anndats"].agg(lo="min", hi="max").reset_index()

    contested = set()
    for permno, group in spans.groupby("permno"):
        if group["ibes_ticker"].nunique() < 2:
            continue
        running_max = None
        for _, row in group.sort_values("lo").iterrows():
            if running_max is not None and row["lo"] <= running_max:
                contested.add(int(permno))
            running_max = row["hi"] if running_max is None else max(running_max, row["hi"])

    if not contested:
        return pd.Series(False, index=df.index)
    return df["permno"].isin(contested)


# ---------------------------------------------------------------------------
# Per-permno return series + day-0/window math.
# ---------------------------------------------------------------------------


def _prefix_arrays(ret: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(P, nan_count), each length len(ret)+1, both leading with a sentinel
    for "before this permno's first row" (P[0]=1.0, nan_count[0]=0).
    P[k] = prod of (1+ret) over raw positions [0, k-1] with NaN treated as 0%
    -- a single historical NaN must not poison every later ratio, which a
    plain cumprod would do (anything * NaN = NaN propagates forever).
    nan_count[k] = count of NaN among those same positions, checked
    separately so a window that actually contains a NaN is still nulled
    despite the 0%-filled P value -- see forward_window_return()."""
    filled = np.nan_to_num(ret, nan=0.0)
    P = np.concatenate(([1.0], np.cumprod(1.0 + filled)))
    nan_count = np.concatenate(([0], np.cumsum(np.isnan(ret).astype(int))))
    return P, nan_count


def build_permno_series(equity_with_mkt: pd.DataFrame) -> dict:
    """permno -> {"dates": np.ndarray, "stock": (P, nan_count), "market": (P, nan_count)}.
    Assumes equity_with_mkt is already sorted by (permno, date) -- see
    load_equity_with_market()."""
    out = {}
    for permno, g in equity_with_mkt.groupby("permno", sort=False):
        out[int(permno)] = {
            "dates": g["date"].to_numpy(),
            "stock": _prefix_arrays(g["ret"].to_numpy(dtype="float64")),
            "market": _prefix_arrays(g["mkt_ret"].to_numpy(dtype="float64")),
        }
    return out


def find_day0_index(dates: np.ndarray, anndats: pd.Timestamp, is_after_market: bool) -> tuple[int | None, str]:
    """Returns (idx0_or_None, status). idx0 is a position in `dates`
    (0-indexed, that permno's own row sequence).

    anndats before this permno's very first row is NOT resolved to that
    first row -- np.searchsorted alone would do exactly that (return index
    0), silently anchoring the event to however much later the security
    actually started trading. That is a real look-ahead-adjacent mismatch
    (e.g. an old ticker relinked to a permno that starts trading years
    later), not an ordinary non-trading-day gap, so it is guarded
    explicitly rather than left to fall out of searchsorted's default
    behavior.

    The after-market shift only applies when the roll-forward landed
    EXACTLY on anndats -- i.e. anndats itself was a real trading day whose
    own close could plausibly have already passed. If anndats was not a
    trading day (weekend/holiday/market closure), searchsorted already
    rolled forward to the first tradeable session, which has not happened
    yet as of anndats and can fully absorb the news -- shifting past it too
    would skip a real trading day for no reason (verified: e.g.
    anndats=2001-09-11 23:00 -- markets were closed through 2001-09-14, so
    the correct day 0 is 2001-09-17, the first session back, not one past
    it).

    Two distinct causes can leave idx0 unresolved past this point, kept as
    two distinct status strings rather than one shared string: (i) the
    panel itself genuinely ends before anndats ("no_trading_day_at_or_
    after_anndats") -- searchsorted alone already ran off the end, before
    any after-market shift is even considered; (ii) the panel had a valid
    session at/after anndats, but the after-market shift pushed one step
    past the panel's last row ("after_market_shift_past_end_of_panel") --
    e.g. an after-market announcement on a security's very last trading
    day. Collapsing these into one string would hide that (ii) is really a
    "day 0 would be the day after the last trade" case, not a "this
    security never traded near anndats" case.
    """
    if len(dates) == 0 or anndats < dates[0]:
        return None, "anndats_before_permno_coverage_start"
    idx = int(np.searchsorted(dates, np.datetime64(anndats), side="left"))
    if idx >= len(dates):
        return None, "no_trading_day_at_or_after_anndats"
    if is_after_market and dates[idx] == np.datetime64(anndats):
        idx += 1
        if idx >= len(dates):
            return None, "after_market_shift_past_end_of_panel"
    return idx, "ok"


def forward_window_return(prefix: tuple[np.ndarray, np.ndarray], n_rows: int, idx0: int, horizon: int) -> float:
    """Cumulative return over the `horizon` rows strictly after idx0 (raw
    positions [idx0+1, idx0+horizon]), or NaN if that window runs past the
    last available row (end-of-panel/delisting truncation -- Decision 4) or
    contains any missing return (Decision 3 -- never silently treated as
    0%)."""
    P, nan_count = prefix
    end = idx0 + horizon + 1  # index into P, which is length n_rows+1
    if end > n_rows:
        return np.nan
    start = idx0 + 1
    if nan_count[end] - nan_count[start] > 0:
        return np.nan
    if P[start] == 0.0:
        # A -100% return already zeroed the running product -- no position
        # survives that to compound forward, so this window has no
        # meaningful percentage return (0/0), not a real number. Dormant
        # today (no ret == -1.0 in normalized_equity) but guards against a
        # future dsedelist-derived delisting return doing so.
        return np.nan
    return P[end] / P[start] - 1.0


def welch_group_stats(group1: np.ndarray, group2: np.ndarray) -> dict:
    """n and mean for each group, plus a Welch's t-statistic for the
    difference (group1 - group2): `t = (mean1 - mean2) / sqrt(var1/n1 +
    var2/n2)`, sample variance (ddof=1). numpy-only by design -- scipy is
    deliberately excluded from requirements.txt for this project, reserved
    for a later statistics slice.

    Returns {"n1", "mean1", "n2", "mean2", "t"}. `t` is None if either
    group is empty or has fewer than 2 observations (sample variance
    undefined), or if both groups happen to have zero variance (a real 0/0,
    not a meaningful t-stat) -- used by render_findings to gate any
    directional claim on this comparison rather than assert it outright.
    """
    n1, n2 = len(group1), len(group2)
    mean1 = float(np.mean(group1)) if n1 > 0 else None
    mean2 = float(np.mean(group2)) if n2 > 0 else None
    t = None
    if n1 >= 2 and n2 >= 2:
        var1 = float(np.var(group1, ddof=1))
        var2 = float(np.var(group2, ddof=1))
        se = (var1 / n1 + var2 / n2) ** 0.5
        if se > 0:
            t = (mean1 - mean2) / se
    return {"n1": n1, "mean1": mean1, "n2": n2, "mean2": mean2, "t": t}


# ---------------------------------------------------------------------------
# Event table assembly.
# ---------------------------------------------------------------------------


def build_event_table(earnings: pd.DataFrame, equity_with_mkt: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Pure function of its inputs (no file I/O) -- see main() for disk I/O.
    Returns (event_table, stats). `earnings` must already be filtered to the
    core study window (see load_earnings)."""
    stats = {"input_rows": len(earnings)}

    coverage = compute_permno_coverage(equity_with_mkt)
    series_by_permno = build_permno_series(equity_with_mkt)

    df = earnings.reset_index(drop=True).copy()
    anntims_unknown = df["anntims"] == ANNTIMS_UNKNOWN
    is_after_market = anntims_unknown | df["anntims"].map(lambda t: t >= AFTER_MARKET_THRESHOLD)
    df["anntims_unknown"] = anntims_unknown
    stats["n_anntims_unknown"] = int(anntims_unknown.sum())
    # Sentinel detection is exact-00:00:00 only. Rows in 00:00:01-03:59:59
    # are pre-dawn but carry their own real timestamp (not the "unknown"
    # sentinel), so they are taken at face value as before-market like any
    # other anntims < AFTER_MARKET_THRESHOLD -- this just counts how many
    # such rows exist, for FINDINGS.md to disclose (see Decision 2). Not
    # widening the sentinel window is a deliberate non-fix, not an oversight.
    pre_4am = (df["anntims"] > ANNTIMS_UNKNOWN) & (df["anntims"] <= dt.time(3, 59, 59))
    stats["n_anntims_pre_4am"] = int(pre_4am.sum())

    resolved = [resolve_permno(row.permno, row.anndats, coverage) for row in df.itertuples()]
    df["permno_resolved"] = pd.array([r[0] for r in resolved], dtype="Int64")
    df["permno_ambiguous"] = [r[1] for r in resolved]
    df["permno_resolution"] = [r[2] for r in resolved]
    stats["permno_resolution_counts"] = df["permno_resolution"].value_counts().to_dict()

    day0_dates = pd.array([pd.NaT] * len(df), dtype="datetime64[ns]")
    day0_status = np.full(len(df), None, dtype=object)
    n_fwd_sessions = np.full(len(df), np.nan)  # sessions available after day0 in that permno's own series
    horizon_cols = {f"ret_fwd_{h}d": np.full(len(df), np.nan) for h in HORIZONS}
    horizon_cols.update({f"ret_fwd_{h}d_mktadj": np.full(len(df), np.nan) for h in HORIZONS})

    for i, (permno, anndats, amc) in enumerate(zip(df["permno_resolved"], df["anndats"], is_after_market)):
        if pd.isna(permno):
            continue
        series = series_by_permno.get(int(permno))
        if series is None:
            day0_status[i] = "permno_not_in_equity_panel"
            continue
        idx0, status = find_day0_index(series["dates"], anndats, bool(amc))
        day0_status[i] = status
        if idx0 is None:
            continue
        day0_dates[i] = series["dates"][idx0]
        n_rows = len(series["dates"])
        n_fwd_sessions[i] = n_rows - idx0 - 1
        for h in HORIZONS:
            horizon_cols[f"ret_fwd_{h}d"][i] = forward_window_return(series["stock"], n_rows, idx0, h)
            horizon_cols[f"ret_fwd_{h}d_mktadj"][i] = (
                horizon_cols[f"ret_fwd_{h}d"][i] - forward_window_return(series["market"], n_rows, idx0, h)
            )

    df["day0_date"] = day0_dates
    df["day0_status"] = day0_status
    # Calendar (not trading-day) lag from the announcement to its resolved
    # day 0 -- e.g. a Friday-after-market anndats rolling to the following
    # Monday is a 3-calendar-day lag despite being the very next trading
    # day. NA wherever day0_date itself is NA (nothing to subtract).
    df["day0_lag_days"] = (pd.Series(day0_dates) - df["anndats"]).dt.days.astype("Int64")
    df["n_fwd_sessions"] = pd.array(n_fwd_sessions, dtype="Int64")
    for col, values in horizon_cols.items():
        df[col] = values

    stats["day0_status_counts"] = pd.Series(day0_status).value_counts(dropna=False).to_dict()
    stats["n_with_day0"] = int(df["day0_date"].notna().sum())
    lag_notna = df["day0_lag_days"].dropna()
    stats["max_day0_lag_days"] = int(lag_notna.max()) if len(lag_notna) else None
    stats["n_day0_lag_over_5d"] = int((lag_notna > 5).sum())
    for h in HORIZONS:
        computed = df[f"ret_fwd_{h}d"].notna()
        stats[f"n_ret_fwd_{h}d_computed"] = int(computed.sum())
        # Of the rows with a day0 but no return at this horizon, how many
        # simply didn't have enough sessions left (truncated -- panel end
        # or delisting) vs. had enough sessions but a NaN inside the window
        # (Decision 3's never-silently-fill-missing-returns rule)? See
        # render_findings -- this replaces a prior claim that blamed the
        # 2013 events span into 2014-06-30, which the data does not support.
        missing_with_day0 = df["day0_date"].notna() & ~computed
        truncated = missing_with_day0 & (df["n_fwd_sessions"] < h)
        stats[f"n_ret_fwd_{h}d_truncated"] = int(truncated.sum())
        stats[f"n_ret_fwd_{h}d_nulled_by_missing_ret"] = int((missing_with_day0 & ~truncated).sum())

    df = df.rename(columns={"permno_resolved": "permno", "permno": "permno_raw"})
    df["permno_contested"] = flag_contested_permnos(df)
    stats["n_permno_contested"] = int(df["permno_contested"].sum())

    has_day0 = df["day0_date"].notna()
    dup_size = df.loc[has_day0].groupby(["permno", "day0_date"])["permno"].transform("size")
    df["n_events_same_permno_day0"] = pd.array([pd.NA] * len(df), dtype="Int64")
    df.loc[has_day0, "n_events_same_permno_day0"] = dup_size.astype("Int64")
    stats["n_duplicate_permno_day0"] = int((dup_size > 1).sum())

    # Attrition-vs-outcome check for the FINDINGS disclosure (Decision 4):
    # a simple two-group mean comparison plus a Welch's t-test for whether
    # the difference is distinguishable from noise, not a quantile sort --
    # the actual quantile/IC apparatus is the next slice's job, not this
    # table's. Group membership is defined purely from day0_date/
    # n_fwd_sessions -- NOT gated on ret_fwd_1d.notna(), which would
    # undercount attrition (a row can be genuinely short-panel and still
    # separately have a missing +1d return, or vice versa); the two
    # comparisons below each drop nulls from their own metric only, after
    # group membership is already fixed.
    attrited = df["day0_date"].notna() & (df["n_fwd_sessions"] < max(HORIZONS))
    retained = df["day0_date"].notna() & (df["n_fwd_sessions"] >= max(HORIZONS))
    stats["n_attrited_before_max_horizon"] = int(attrited.sum())

    ret_diff = welch_group_stats(
        df.loc[attrited, "ret_fwd_1d"].dropna().to_numpy(),
        df.loc[retained, "ret_fwd_1d"].dropna().to_numpy(),
    )
    stats["n_ret_attrited"] = ret_diff["n1"]
    stats["mean_ret_attrited"] = ret_diff["mean1"]
    stats["n_ret_retained"] = ret_diff["n2"]
    stats["mean_ret_retained"] = ret_diff["mean2"]
    stats["t_ret_attrition_diff"] = ret_diff["t"]

    sue_diff = welch_group_stats(
        df.loc[attrited, "sue_primary"].dropna().to_numpy(),
        df.loc[retained, "sue_primary"].dropna().to_numpy(),
    )
    stats["n_sue_attrited"] = sue_diff["n1"]
    stats["mean_sue_attrited"] = sue_diff["mean1"]
    stats["n_sue_retained"] = sue_diff["n2"]
    stats["mean_sue_retained"] = sue_diff["mean2"]
    stats["t_sue_attrition_diff"] = sue_diff["t"]

    out_cols = [
        "ibes_ticker", "permno", "permno_raw", "permno_ambiguous", "permno_resolution", "permno_contested", "gvkey",
        "fpedats", "anndats", "anntims", "anntims_unknown",
        "sue_analyst", "sue_seasonal", "sue_primary", "sue_source",
        "day0_date", "day0_status", "day0_lag_days", "n_fwd_sessions", "n_events_same_permno_day0",
    ] + list(horizon_cols.keys())
    return df[out_cols], stats


# ---------------------------------------------------------------------------
# Findings.
# ---------------------------------------------------------------------------


def _attrition_comparison_line(label: str, n1, mean1, n2, mean2, t, fmt) -> str:
    """One line for the FINDINGS attrition disclosure: both groups' n and
    mean, the t-stat, and -- ONLY when `abs(t) >= 2.0` -- a directional
    clause whose wording ("lower"/"higher") is read off the observed sign,
    never hardcoded. Below that threshold (or when `t` is None because a
    group was empty or too small), states plainly that the difference
    isn't statistically distinguishable rather than asserting randomness or
    non-randomness either way -- this project's rule against printing a
    causal/directional claim next to a stat that doesn't actually back it.
    """
    if n1 == 0 or n2 == 0:
        return f"{label}: insufficient data in one or both groups to compare."
    t_str = f"{t:.2f}" if t is not None else "undefined (n<2 in one or both groups)"
    line = (
        f"{label} -- attrited: {n1:,} events average {fmt(mean1)}; retained: {n2:,} events average "
        f"{fmt(mean2)} (t = {t_str})"
    )
    if t is not None and abs(t) >= 2.0:
        direction = "lower" if mean1 < mean2 else "higher"
        line += f", a gap unlikely to be noise at conventional significance -- attrited events average {direction}."
    else:
        line += ", not statistically distinguishable from noise at conventional levels."
    return line


def render_findings(events: pd.DataFrame, stats: dict) -> str:
    # A single tripwire covering every division-by-`n` site below (rows-with-
    # day0 percentages, per-horizon coverage percentages, etc.) rather than
    # guarding each site individually -- an empty events table is not a real
    # code path today, just a defensive stop against a silent
    # ZeroDivisionError/NaN if one is ever passed in.
    assert len(events) > 0, "render_findings: empty events table -- nothing to report"
    n = len(events)
    n_day0 = stats["n_with_day0"]
    res_counts = stats["permno_resolution_counts"]
    day0_counts = stats["day0_status_counts"]
    n_pre_4am = stats["n_anntims_pre_4am"]

    lines = []
    lines.append("# Equity event table: join, day-0, and return-coverage findings\n")
    lines.append(
        f"{n:,} earnings events in the 1996-2013 core window (`data/earnings/ibes_clean_1995_2014.parquet`, "
        f"filtered to `anndats` in that range). {n_day0:,} ({n_day0 / n:.1%}) resolved a permno and a "
        f"trading-day day 0; every row is kept regardless (tag, don't filter), with the reason for any "
        f"gap in `permno_resolution` / `day0_status`.\n"
    )
    lines.append("## Identifier resolution\n")
    lines.append(
        "`permno` in ibes_clean is occasionally a comma-joined multi-candidate string. Unlike ibes_clean's "
        "own `identifier_ambiguous` (which also fires on gvkey-only ambiguity, irrelevant to this join), "
        "this table's `permno_ambiguous` looks at permno alone. Ambiguous cases are resolved by checking "
        "which candidate(s) actually have `normalized_equity` coverage bracketing `anndats`. The coverage "
        "brackets themselves are computed once, over the full 1995-2014 `normalized_equity` sample, not "
        "point-in-time as of each announcement -- so resolving an ambiguous row can use post-`anndats` "
        "information about which candidate keeps trading. This only affects *which candidate permno* gets "
        "picked for the ~4.5% ambiguous rows; it never touches the forward-return computation itself, "
        "which is strictly forward from day 0 regardless of how the permno was resolved:\n"
    )
    lines.append("| permno_resolution | rows |")
    lines.append("| --- | --- |")
    for status, count in sorted(res_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {status} | {count:,} |")
    n_contested = stats["n_permno_contested"]
    lines.append(
        "\n`unresolved_no_bracketing_coverage` / `unresolved_multiple_bracketing_coverage` rows keep their "
        "surprise measure but get no permno, day 0, or return -- there is no honest way to pick a price "
        f"series for them. Separately, {n_contested:,} rows' resolved permno is also claimed by a "
        "*different* ibes_ticker for an overlapping announcement period (`permno_contested=True`) -- "
        "almost always an upstream IBES-to-CRSP link ambiguity this table can surface but not arbitrate; "
        "these rows keep their (possibly wrong) permno and return rather than being nulled. "
        "`permno_contested` is a permno-level flag: it applies to every row sharing that permno, not only "
        "rows whose own `anndats` falls inside the colliding tickers' actual overlapping span -- "
        "intentionally conservative, matching round 1's earlier decision on this same code path.\n"
    )
    n_unknown = stats["n_anntims_unknown"]
    lines.append("## Day-0 alignment\n")
    lines.append(
        "Day 0 is the next trading day at/after `anndats` in the resolved permno's own trading history "
        "(not a market-wide calendar). The after-market shift (one further trading day) only applies when "
        "`anndats` itself was a real session -- if it wasn't (weekend/holiday/market closure), the session "
        "the roll-forward already lands on hasn't happened yet and needs no further shift. `anntims`'s "
        f"00:00:00 \"time unknown\" sentinel ({n_unknown:,} rows, {n_unknown / n:.1%}) is treated as "
        "after-market: checking where the SUE-related return jump actually lands (day 0 vs. day0+1) shows "
        "most sentinel rows react at day0+1, i.e. they are genuinely after-market more often than not. "
        "`anntims_unknown` keeps this subset filterable for a robustness check. Sentinel detection is "
        f"exact-00:00:00 only; a further {n_pre_4am:,} rows carry a timestamp in 00:00:01-03:59:59 -- "
        "pre-dawn, but their own real timestamp rather than the \"unknown\" sentinel, so they are taken at "
        "face value as before-market like any other early anntims. `anntims` remains in the output, so "
        "this subset stays filterable if anyone wants to check it separately.\n"
    )
    lines.append("| day0_status | rows |")
    lines.append("| --- | --- |")
    for status, count in sorted(day0_counts.items(), key=lambda kv: -kv[1]):
        label = "(permno unresolved -- see identifier resolution above)" if pd.isna(status) else status
        lines.append(f"| {label} | {count:,} |")
    max_lag = stats["max_day0_lag_days"]
    n_lag_over_5d = stats["n_day0_lag_over_5d"]
    if max_lag is not None:
        lines.append(
            f"\n`day0_lag_days` (calendar, not trading, days from `anndats` to `day0_date`) ranges up to "
            f"{max_lag:,}; {n_lag_over_5d:,} rows exceed 5 calendar days -- almost entirely a run of "
            "holidays/weekend adjacent to a non-trading anndats rather than anything wrong with the "
            "roll-forward.\n"
        )
    lines.append("\n## Forward-return coverage by horizon\n")
    lines.append("| horizon | rows with a return | truncated (ran out of panel) | nulled (missing return in window) |")
    lines.append("| --- | --- | --- | --- |")
    for h in HORIZONS:
        c = stats[f"n_ret_fwd_{h}d_computed"]
        t = stats[f"n_ret_fwd_{h}d_truncated"]
        m = stats[f"n_ret_fwd_{h}d_nulled_by_missing_ret"]
        lines.append(f"| +{h}d | {c:,} ({c / n:.1%}) | {t:,} | {m:,} |")
    n_attrited = stats["n_attrited_before_max_horizon"]
    attrition_sentence = ""
    if n_attrited > 0:
        ret_line = _attrition_comparison_line(
            "+1d forward return", stats["n_ret_attrited"], stats["mean_ret_attrited"],
            stats["n_ret_retained"], stats["mean_ret_retained"], stats["t_ret_attrition_diff"],
            fmt=lambda x: f"{x:.2%}",
        )
        sue_line = _attrition_comparison_line(
            "sue_primary", stats["n_sue_attrited"], stats["mean_sue_attrited"],
            stats["n_sue_retained"], stats["mean_sue_retained"], stats["t_sue_attrition_diff"],
            fmt=lambda x: f"{x:.3f}",
        )
        attrition_sentence = (
            f" Comparing the {n_attrited:,} events whose panel ends before the longest "
            f"({max(HORIZONS)}-day) horizon (\"attrited\") to those with a full panel (\"retained\"): "
            f"{ret_line} {sue_line}"
        )
    lines.append(
        "\nCoverage shrinks with horizon length almost entirely because a security's own panel ends "
        "before that horizon's target date (predominantly delisting, not the archive's 2014-06-30 edge -- "
        "the buffer past the 2013 window is long enough that this is not a meaningful contributor); a "
        "smaller share is a missing daily return somewhere inside an otherwise-long-enough window "
        "(Decision 3: never silently treated as 0%). Delisting returns themselves (CRSP `dsedelist`) are "
        "not merged into `normalized_equity` and remain out of scope for this phase, per the project "
        "plan -- a stock that goes private or is delisted mid-horizon simply stops contributing return "
        "data from that point, rather than having its final realized (possibly large) delisting return "
        f"captured.{attrition_sentence} Long-horizon statistics computed on this table implicitly "
        "condition on survival to that horizon.\n"
    )
    n_dup = stats["n_duplicate_permno_day0"]
    lines.append(
        f"{n_dup:,} rows share a `(permno, day0_date)` pair with at least one other row (e.g. a "
        "delinquent filer announcing several fiscal quarters on the same date) -- `n_events_same_permno_"
        "day0` counts the group size so a later statistical pass can weight or dedupe rather than "
        "silently double- or triple-counting one firm-day.\n"
    )
    lines.append("## Returns methodology\n")
    lines.append(
        "Each horizon is a compounded return over the h trading days strictly after day 0 (day 0's own "
        "reaction-day return is excluded from every horizon). Market-adjusted return is the stock's "
        "compounded return over that window minus CRSP `dsi.vwretd`'s compounded return over the exact "
        "same dates -- both legs are total return (dividend-inclusive): `ret` includes dividends, and "
        "`vwretd` (the value-weighted market index's total return) is the matching convention, unlike "
        "`dsi.sprtrn` (S&P 500 price return, dividend-exclusive), which would have biased every "
        "`*_mktadj` value upward by roughly the market's dividend yield over the window -- about +0.16% "
        "at 20 sessions and +0.49% at 60, measured against this same data. `sprtrn` remains in "
        "`market_benchmark.parquet` for anyone who wants that as a separate robustness check, but is not "
        "used here. Any missing daily return inside a window nulls that horizon rather than being "
        "silently treated as 0%.\n"
    )
    lines.append(
        "**Known limitation.** Daily-frequency data cannot fully separate an announcement's own reaction "
        "from its first day of drift when the announcement lands before or during market hours: some of "
        "the reaction can still show up on day0+1, which is inside every `ret_fwd_*d` window. This is most "
        "acute for the shortest horizon (+1d). Announcements confidently after-market "
        "(`anntims_unknown=False` and `anntims >= 16:00:00`) are the cleanest subset for a short-horizon "
        "robustness check.\n"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def main():
    # Pass the module constants explicitly rather than relying on
    # load_earnings/load_equity_with_market's own defaults -- a default
    # parameter value is bound once at function-definition time, so it
    # would not observe a test's monkeypatch.setattr(module, "EARNINGS_PATH", ...).
    earnings = load_earnings(EARNINGS_PATH)
    equity_with_mkt = load_equity_with_market(NORMALIZED_EQUITY_DIR, MARKET_BENCHMARK_PATH)
    events, stats = build_event_table(earnings, equity_with_mkt)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    events.to_parquet(OUTPUT_PATH, index=False)
    print(f"Wrote {len(events):,} rows to {OUTPUT_PATH}", flush=True)

    findings = render_findings(events, stats)
    FINDINGS_PATH.write_text(findings)
    print(f"Wrote findings to {FINDINGS_PATH}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
