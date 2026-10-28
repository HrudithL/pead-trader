"""Steady Drift Plus with CRSP delisting returns applied (MT-05, research): the survivorship / delisting restatement.

Same book as steady_drift_plus; the only change is that the terminal delisting return (dlret, or the Shumway/Shumway-Warther
imputed loss when it is missing for a performance delisting) is compounded onto each stock's last row. `_dlret_only` uses
recorded dlret and treats missing as 0; `_dlret_half` halves the imputed losses (sensitivity).
"""
import dataclasses

from pead.core.delisting import DelistPolicy, apply_delisting_returns, load_delistings
from pead.strategies.equity.steady_drift import SteadyDriftPlus
from pead.strategies.registry import register


class SteadyDriftDelist(SteadyDriftPlus):
    policy = DelistPolicy()
    legacy_twin = False
    parent = "steady_drift_plus"
    status = "research"
    family = "equity-longonly"

    def backtest(self, uni):
        r1, r2, info = apply_delisting_returns(uni.ret_raw, uni.ret_mktadj, uni.panel_date, uni.panel_permno,
                                               load_delistings(), self.policy)
        run = super().backtest(dataclasses.replace(uni, ret_raw=r1, ret_mktadj=r2))
        run.summary.update(delist_applied=info["n_applied"], delist_imputed=info["n_imputed"])
        return run


def _variant(suffix, doc, policy):
    @register
    class V(SteadyDriftDelist):
        name = f"steady_drift_plus_{suffix}"
        description = doc
    V.policy = policy
    V.__name__ = f"SteadyDriftPlus_{suffix}"


_variant("dlret", "steady_drift_plus with delisting returns (imputed -30%/-55% when a performance dlret is missing)", DelistPolicy())
_variant("dlret_only", "steady_drift_plus with recorded delisting returns only (missing = 0)", DelistPolicy(impute_missing_performance=False))
_variant("dlret_half", "Delisting returns with half the imputed losses (sensitivity)", DelistPolicy(loss_nyse_amex=-0.15, loss_nasdaq=-0.275))
