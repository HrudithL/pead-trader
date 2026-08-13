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
   guessed).
2. Day 0. The next trading day at/after anndats, in the RESOLVED PERMNO's OWN
   trading-day sequence (not a market-wide calendar), shifted one further
   trading day for after-market announcements (anntims >= 16:00:00) -- see
   find_day0_index(). anntims's 00:00:00 sentinel (time unknown, ~16% of
   rows) sorts below 16:00:00 and is therefore treated as not-after-market,
   the plain default -- deliberate, not an oversight (see FINDINGS.md).
3. Forward returns. Cumulative return over the h trading days strictly AFTER
   day 0 (day 0's own reaction-day return is excluded from every horizon),
   raw and market-adjusted. Market-adjusted = cumulative stock return minus
   cumulative CRSP dsi.sprtrn return over the exact same h dates (both sides
   read off the same per-permno rows, so "matched by date" holds even
   through a gap in that security's own trading history) -- see
   forward_window_return().
4. Delisting. normalized_equity does not merge dsedelist (unchanged, out of
   scope); a security's panel simply ends at its last trade, so any horizon
   needing a row past that point is null via the same end-of-panel bounds
   check used for the archive's own right edge -- no separate code path.
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
MARKET_RET_COL = "sprtrn"

# US market close, ET. anntims >= this is treated as after-market (see
# Decision 2). The IBES "time unknown" sentinel is 00:00:00, which sorts
# below this threshold and is therefore NOT treated as after-market -- the
# plain "day 0 = next trading day at/after anndats" default applies to it.
AFTER_MARKET_THRESHOLD = dt.time(16, 0, 0)


# ---------------------------------------------------------------------------
# Loading.
# ---------------------------------------------------------------------------


def load_earnings(path=EARNINGS_PATH) -> pd.DataFrame:
    """Reads ibes_clean's event-identity + surprise columns and filters to
    the core study window (Decision 5). Returns a fresh 0..n-1 index."""
    df = pd.read_parquet(path, columns=EARNINGS_USECOLS)
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
    """
    if len(dates) == 0 or anndats < dates[0]:
        return None, "anndats_before_permno_coverage_start"
    idx = int(np.searchsorted(dates, np.datetime64(anndats), side="left"))
    if is_after_market:
        idx += 1
    if idx >= len(dates):
        return None, "no_trading_day_at_or_after_anndats"
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
    return P[end] / P[start] - 1.0


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
    is_after_market = df["anntims"].map(lambda t: t >= AFTER_MARKET_THRESHOLD)

    resolved = [resolve_permno(row.permno, row.anndats, coverage) for row in df.itertuples()]
    df["permno_resolved"] = pd.array([r[0] for r in resolved], dtype="Int64")
    df["permno_ambiguous"] = [r[1] for r in resolved]
    df["permno_resolution"] = [r[2] for r in resolved]
    stats["permno_resolution_counts"] = df["permno_resolution"].value_counts().to_dict()

    day0_dates = pd.array([pd.NaT] * len(df), dtype="datetime64[ns]")
    day0_status = np.full(len(df), None, dtype=object)
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
        for h in HORIZONS:
            horizon_cols[f"ret_fwd_{h}d"][i] = forward_window_return(series["stock"], n_rows, idx0, h)
            horizon_cols[f"ret_fwd_{h}d_mktadj"][i] = (
                horizon_cols[f"ret_fwd_{h}d"][i] - forward_window_return(series["market"], n_rows, idx0, h)
            )

    df["day0_date"] = day0_dates
    df["day0_status"] = day0_status
    for col, values in horizon_cols.items():
        df[col] = values

    stats["day0_status_counts"] = pd.Series(day0_status).value_counts(dropna=False).to_dict()
    stats["n_with_day0"] = int(df["day0_date"].notna().sum())
    for h in HORIZONS:
        stats[f"n_ret_fwd_{h}d_computed"] = int(df[f"ret_fwd_{h}d"].notna().sum())

    df = df.rename(columns={"permno_resolved": "permno", "permno": "permno_raw"})
    out_cols = [
        "ibes_ticker", "permno", "permno_raw", "permno_ambiguous", "permno_resolution", "gvkey",
        "fpedats", "anndats", "anntims",
        "sue_analyst", "sue_seasonal", "sue_primary", "sue_source",
        "day0_date", "day0_status",
    ] + list(horizon_cols.keys())
    return df[out_cols], stats


# ---------------------------------------------------------------------------
# Findings.
# ---------------------------------------------------------------------------


def render_findings(events: pd.DataFrame, stats: dict) -> str:
    n = len(events)
    n_day0 = stats["n_with_day0"]
    res_counts = stats["permno_resolution_counts"]
    day0_counts = stats["day0_status_counts"]

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
        "which candidate(s) actually have `normalized_equity` coverage bracketing `anndats`:\n"
    )
    lines.append("| permno_resolution | rows |")
    lines.append("| --- | --- |")
    for status, count in sorted(res_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {status} | {count:,} |")
    lines.append(
        "\n`unresolved_no_bracketing_coverage` / `unresolved_multiple_bracketing_coverage` rows keep their "
        "surprise measure but get no permno, day 0, or return -- there is no honest way to pick a price "
        "series for them.\n"
    )
    lines.append("## Day-0 alignment\n")
    lines.append(
        "Day 0 is the next trading day at/after `anndats` in the resolved permno's own trading history "
        "(not a market-wide calendar), shifted one further trading day for after-market announcements "
        "(`anntims >= 16:00:00`). The `anntims` \"unknown\" sentinel (00:00:00) sorts below that threshold "
        "and is treated as not-after-market, the plain default -- ~16% of rows carry it.\n"
    )
    lines.append("| day0_status | rows |")
    lines.append("| --- | --- |")
    for status, count in sorted(day0_counts.items(), key=lambda kv: -kv[1]):
        label = "(permno unresolved -- see identifier resolution above)" if pd.isna(status) else status
        lines.append(f"| {label} | {count:,} |")
    lines.append("\n## Forward-return coverage by horizon\n")
    lines.append("| horizon | rows with a return |")
    lines.append("| --- | --- |")
    for h in HORIZONS:
        c = stats[f"n_ret_fwd_{h}d_computed"]
        lines.append(f"| +{h}d | {c:,} ({c / n:.1%}) |")
    lines.append(
        "\nCoverage shrinks with horizon length because longer horizons run past the end of the equity "
        "panel (2014-06-30) for events late in 2013, and because a security that delists mid-horizon has "
        "no rows past its last trade -- both are the same end-of-panel bounds check, surfaced as a null "
        "return rather than a truncated or silently wrong one. Delisting returns themselves "
        "(CRSP `dsedelist`) are not merged into `normalized_equity` and remain out of scope for this "
        "phase, per the project plan; a stock that goes private or is delisted mid-horizon simply stops "
        "contributing return data from that point, rather than having its final realized (possibly large) "
        "delisting return captured.\n"
    )
    lines.append("## Returns methodology\n")
    lines.append(
        "Each horizon is a compounded return over the h trading days strictly after day 0 (day 0's own "
        "reaction-day return is excluded from every horizon). Market-adjusted return is the stock's "
        "compounded return over that window minus CRSP `dsi.sprtrn`'s compounded return over the exact "
        "same dates. Any missing daily return inside a window nulls that horizon rather than being "
        "silently treated as 0%.\n"
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
