"""Slice 6 of memory/phase3-options-pead-plan.md: options quality gate.

Purpose
-------
Runs the plan's data-quality checks against a canonical options frame
(the output of ``options_loader_archive.load_options``, or any other
loader that conforms to ``options_canonical_schema``). Emits:

  - a *clean* frame of rows that pass every error-severity rule,
  - a *quarantine* frame of rows that fail at least one error-severity
    rule, with a per-row ``quarantine_reasons`` column listing every
    rule the row failed,
  - a *warnings* frame of rows that were kept in ``clean`` but tripped
    at least one warning-severity rule (so a downstream report can
    still count them without them being excluded from research),
  - a *counts* dict of {rule_code: rows_flagged}.

Exclusion contract
------------------
Exclusions happen at exactly one named, documented step -- this module.
No downstream slice quietly filters options rows; every filter that
matters for research goes through here. This is deliberate: a single
choke point makes the exclusion picture inspectable ("what did the
quality gate throw out, and why?") rather than scattered decisions
inside each downstream slice.

Rule inventory (matches the plan's Scope-In list)
-------------------------------------------------
Error-severity (row goes to quarantine, NOT to clean):
  Q001 crossed_market            bid > ask
  Q002 non_positive_bid          bid < 0
  Q003 non_positive_ask          ask < 0
  Q004 non_standard_multiplier   multiplier != 100
  Q005 impossible_dte            expiration < date  (loader also drops
                                                     these; belt-and-
                                                     braces)
  Q006 strike_scaling_error      strike < underlying * 0.01 OR
                                 strike > underlying * 100 (well-outside
                                 the plausible strike/underlying band)
  Q007 adjusted_contract         raw_suffix is present and non-empty
                                 (corporate-action-adjusted contract)
  Q008 extreme_iv                iv > 5.0 (500% vol) OR iv < 0
  Q009 duplicate_option_date     (option_id, date) not unique

Warning-severity (row STAYS in clean; flagged separately):
  Q101 zero_bid_and_ask          both bid and ask exactly 0 (illiquid
                                 but real -- a bid of zero on a deep-
                                 OTM contract is legitimate market
                                 state)
  Q102 stale_quote_prev_day      bid AND ask identical to the same
                                 option's prior trading-day quotes
                                 (potentially stale; downstream event-
                                 study slices can decide whether to
                                 exclude)

Rules are single-row where possible; only Q009 and Q102 need
cross-row context. Adding a new rule is a matter of appending a
``QualityRule`` to ``QUALITY_RULES`` -- the runner is generic.

Usage
-----
    from options_quality_gate import run_quality_gate

    result = run_quality_gate(loader_df)
    print(result.summary())
    # result.clean is the frame downstream slices consume
    # result.quarantine gets written to data/metadata/ for the QA report
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

# --- Rule constants --------------------------------------------------

# Standard equity-option contract multiplier. Anything else in a US
# equity option context is a corporate-action-adjusted contract, a
# non-standard multiplier deliverable, or a data error -- all of
# which quarantine per the plan.
STANDARD_MULTIPLIER: int = 100

# Strike / underlying band: strike * 0.01 <= underlying * 100 is a very
# wide band (spans a 10,000x range) intentionally, since deep ITM/OTM
# contracts exist on high-priced underlyings. Anything outside this
# band is a plausible strike-scale bug (e.g. an unscaled IvyDB strike
# leaking through / 1000).
STRIKE_UNDERLYING_MIN_RATIO: float = 0.01
STRIKE_UNDERLYING_MAX_RATIO: float = 100.0

# Extreme IV threshold. 500% is way past any plausible equity-option
# implied vol; usually a solver blow-up on a near-zero price.
IV_MAX_PLAUSIBLE: float = 5.0

# --- Rule primitive -------------------------------------------------


Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class QualityRule:
    """One quality check.

    ``detector`` returns a boolean Series aligned with ``df.index``,
    True where the row fails the rule. Detectors are single-purpose --
    no rule combines two independent conditions -- so the reason codes
    on a quarantine row are individually meaningful.
    """

    code: str
    description: str
    severity: Severity
    detector: Callable[[pd.DataFrame], pd.Series]


@dataclass
class QualityGateResult:
    """Outcome of ``run_quality_gate``.

    ``counts`` includes every rule's code, with 0 rows flagged
    reported explicitly so downstream reports can list rules that
    ran-and-passed rather than only rules that fired.
    """

    clean: pd.DataFrame
    quarantine: pd.DataFrame
    warnings: pd.DataFrame
    counts: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        """Compact text summary suitable for a report bullet."""
        input_rows = len(self.clean) + len(self.quarantine)
        return (
            f"quality-gate: {input_rows} input rows, "
            f"{len(self.clean)} clean, "
            f"{len(self.quarantine)} quarantined, "
            f"{len(self.warnings)} flagged with warnings only. "
            f"per-rule counts: {self.counts}"
        )


# --- Detectors -------------------------------------------------------
#
# Every detector below is a pure function of a DataFrame and returns a
# boolean Series indexed the same way. Detectors handle NaN gracefully
# (a NaN in a nullable column doesn't count as a rule failure -- the
# canonical schema layer flags nulls in non-null columns, not this
# module).


def _detect_crossed_market(df: pd.DataFrame) -> pd.Series:
    return (df["bid"].notna() & df["ask"].notna() & (df["bid"] > df["ask"]))


def _detect_non_positive_bid(df: pd.DataFrame) -> pd.Series:
    return df["bid"].notna() & (df["bid"] < 0)


def _detect_non_positive_ask(df: pd.DataFrame) -> pd.Series:
    return df["ask"].notna() & (df["ask"] < 0)


def _detect_non_standard_multiplier(df: pd.DataFrame) -> pd.Series:
    return df["multiplier"] != STANDARD_MULTIPLIER


def _detect_impossible_dte(df: pd.DataFrame) -> pd.Series:
    return df["expiration"] < df["date"]


def _detect_strike_scaling_error(df: pd.DataFrame) -> pd.Series:
    # Underlying <= 0 would be caught by the schema layer; guard against
    # zero here so the ratio is well-defined.
    up = df["underlying_price"]
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = df["strike"] / up.where(up > 0)
    too_low = ratio < STRIKE_UNDERLYING_MIN_RATIO
    too_high = ratio > STRIKE_UNDERLYING_MAX_RATIO
    return (too_low | too_high).fillna(False)


def _detect_adjusted_contract(df: pd.DataFrame) -> pd.Series:
    # raw_suffix is populated by the archive loader (Slice 5b). If a
    # future loader doesn't carry a raw_suffix column at all, we cannot
    # detect this class -- return all-False so the rule doesn't crash,
    # and downstream reporting sees a "0 rows flagged" for this rule.
    if "raw_suffix" not in df.columns:
        return pd.Series(False, index=df.index)
    suffix = df["raw_suffix"].astype("string")
    return suffix.notna() & (suffix.str.strip() != "")


def _detect_extreme_iv(df: pd.DataFrame) -> pd.Series:
    iv = df["iv"]
    return iv.notna() & ((iv < 0) | (iv > IV_MAX_PLAUSIBLE))


def _detect_duplicate_option_date(df: pd.DataFrame) -> pd.Series:
    # ``keep=False`` marks both members of every duplicate group, so both
    # rows go to quarantine (rather than us arbitrarily keeping one).
    return df.duplicated(subset=["option_id", "date"], keep=False)


def _detect_zero_bid_and_ask(df: pd.DataFrame) -> pd.Series:
    return (
        df["bid"].notna()
        & df["ask"].notna()
        & (df["bid"] == 0)
        & (df["ask"] == 0)
    )


def _detect_stale_quote_prev_day(df: pd.DataFrame) -> pd.Series:
    """True where this row's (bid, ask) equals the same option's prior
    trading-day quotes.

    Uses a per-``option_id`` shift by one row after sorting by date.
    Requires >= 2 observations of the same contract to fire; a
    contract's first-ever observation is never flagged.
    """
    if len(df) == 0:
        return pd.Series(False, index=df.index, dtype=bool)
    # Sort once, use the original index to align the result back.
    sorted_df = df.sort_values(["option_id", "date"], kind="stable")
    prev_bid = sorted_df.groupby("option_id")["bid"].shift(1)
    prev_ask = sorted_df.groupby("option_id")["ask"].shift(1)
    flag = (
        sorted_df["bid"].notna()
        & sorted_df["ask"].notna()
        & prev_bid.notna()
        & prev_ask.notna()
        & (sorted_df["bid"] == prev_bid)
        & (sorted_df["ask"] == prev_ask)
    )
    # Reindex to the input df's row order.
    return flag.reindex(df.index).fillna(False).astype(bool)


# --- Rule registry ---------------------------------------------------


QUALITY_RULES: list[QualityRule] = [
    QualityRule("Q001", "crossed market (bid > ask)", "error", _detect_crossed_market),
    QualityRule("Q002", "non-positive bid", "error", _detect_non_positive_bid),
    QualityRule("Q003", "non-positive ask", "error", _detect_non_positive_ask),
    QualityRule("Q004", "non-standard multiplier (!= 100)", "error",
                _detect_non_standard_multiplier),
    QualityRule("Q005", "impossible DTE (expiration < date)", "error",
                _detect_impossible_dte),
    QualityRule("Q006", "strike/underlying ratio out of plausible band",
                "error", _detect_strike_scaling_error),
    QualityRule("Q007", "corporate-action-adjusted contract (raw_suffix set)",
                "error", _detect_adjusted_contract),
    QualityRule("Q008", "extreme IV (< 0 or > 5.0)", "error",
                _detect_extreme_iv),
    QualityRule("Q009", "duplicate (option_id, date)", "error",
                _detect_duplicate_option_date),
    QualityRule("Q101", "zero bid AND zero ask (illiquid but real)",
                "warning", _detect_zero_bid_and_ask),
    QualityRule("Q102", "stale quote (equal to same option's prior day)",
                "warning", _detect_stale_quote_prev_day),
]


# --- Runner ----------------------------------------------------------


def run_quality_gate(
    df: pd.DataFrame,
    rules: list[QualityRule] | None = None,
) -> QualityGateResult:
    """Apply every rule to ``df`` in one pass, split into clean and
    quarantine, keep the warnings-only rows in clean but track them
    separately.

    Row-level accumulation: a row that fails multiple error rules gets
    every failing code in its ``quarantine_reasons`` list, so a
    downstream reader isn't misled about the reason count. Empty
    ``quarantine_reasons`` marks a clean row.

    Passing ``rules`` is only for tests / experiments. Production code
    uses the module-level ``QUALITY_RULES``; overriding it in a
    research script would defeat the "single named filter step" rule.
    """
    active_rules = list(rules) if rules is not None else list(QUALITY_RULES)

    if len(df) == 0:
        empty = df.copy()
        empty["quarantine_reasons"] = pd.Series([], dtype="object")
        return QualityGateResult(
            clean=df.copy().assign(quarantine_reasons=pd.Series([], dtype="object")),
            quarantine=empty,
            warnings=empty,
            counts={rule.code: 0 for rule in active_rules},
        )

    # Per-row reason accumulators, one list per row.
    error_reasons: list[list[str]] = [[] for _ in range(len(df))]
    warning_reasons: list[list[str]] = [[] for _ in range(len(df))]
    counts: dict[str, int] = {}

    for rule in active_rules:
        try:
            failing = rule.detector(df)
        except KeyError as exc:  # noqa: BLE001 -- surface as a rule-level fault
            raise ValueError(
                f"Rule {rule.code} ({rule.description!r}) requires a column "
                f"the input frame does not have: {exc}. "
                f"Was the input frame produced by a canonical loader?"
            ) from exc
        failing = failing.reindex(df.index).fillna(False).astype(bool)
        counts[rule.code] = int(failing.sum())
        target = error_reasons if rule.severity == "error" else warning_reasons
        for i, flag in enumerate(failing.to_numpy()):
            if flag:
                target[i].append(rule.code)

    # Attach reason columns.
    out = df.copy()
    out["quarantine_reasons"] = error_reasons
    out["warning_reasons"] = warning_reasons

    is_quarantine = pd.Series(
        [len(r) > 0 for r in error_reasons], index=df.index
    )
    is_warning = pd.Series(
        [len(r) > 0 for r in warning_reasons], index=df.index
    )

    clean = out.loc[~is_quarantine].drop(columns=["warning_reasons"])
    quarantine = out.loc[is_quarantine].drop(columns=["warning_reasons"])
    warnings = out.loc[is_warning & ~is_quarantine].drop(columns=["quarantine_reasons"])

    return QualityGateResult(
        clean=clean,
        quarantine=quarantine,
        warnings=warnings,
        counts=counts,
    )
