"""Research-library equity candidates on the steady_drift_plus machinery (EQ-01, EQ-03 part C, EQ-04, EQ-05, EQ-06; research).

All are long-only, cash-filled, risk-throttled (12%), sized by causal inverse variance like steady_drift_plus; only the signal
mask and/or the exit rule change. Each family documents the deviation from its spec in `docs/EQ_LIBRARY_CANDIDATES.md`.

  seasonal  (EQ-06)  seasonal-SUE arms: S (top decile), S5 (top 5%), AND (analyst >= .9 and seasonal >= .667), OR, AVG (mean rank >= .9)
  pathexit  (EQ-04)  the steady_drift_plus book with a 5% / 10% close-based stop or a latched 3%/2% trailing stop
  ea1904    (EQ-01)  seasonal SUE >= 1.5 AND day-0 relative volume >= 1.5, 20-session hold, 5% stop (and a no-stop control)
  bouddat   (EQ-05)  analyst SUE >= 0.635 (a broad "decent beat" screen), held to day0 + 85 calendar days (and a 60-session control)
  react     (EQ-03C) top-decile SUE split by the day-0 close-to-close reaction: agree (>= +0.5%), contrarian (<= -0.5%), flat
"""
import dataclasses

import numpy as np

from pead.core.event_signals import add_sue_signals
from pead.core.gap_features import add_gap_features
from pead.engines.exits import with_calendar_hold, with_path_exits
from pead.strategies import weights as W
from pead.strategies.equity.steady_drift import SteadyDriftPlus
from pead.strategies.registry import register


class _Candidate(SteadyDriftPlus):
    legacy_twin = False
    parent = "steady_drift_plus"
    status = "research"
    family = "equity-longonly"
    hold_sessions = None                     # exits are applied in backtest()
    arm = ""
    max_hold = 60
    stop_pct = trail_act = trail_amt = None
    cal_days = None

    def params(self):
        return {**super().params(), "arm": self.arm, "max_hold": self.max_hold, "stop_pct": self.stop_pct,
                "trail_activation": self.trail_act, "trail_amount": self.trail_amt, "calendar_hold_days": self.cal_days}

    def mask(self, ev):
        raise NotImplementedError

    def weights(self, events):
        adv = np.nan_to_num(events["avg_dollar_volume_21d"].astype(float).to_numpy(), nan=0.0)
        return W.causal_inverse_variance(events, self.mask(events) & (adv >= self.min_dollar_volume))

    def backtest(self, uni):
        ev = add_sue_signals(add_gap_features(uni.events))
        ev["react"] = uni.ret_raw[ev["entry_row_idx"].to_numpy()]
        u = dataclasses.replace(uni, events=ev)
        u = with_calendar_hold(u, self.cal_days) if self.cal_days else with_path_exits(
            u, self.max_hold, self.stop_pct, self.trail_act, self.trail_amt)
        return super().backtest(u)

    def base_mask(self, ev):
        return ev["sue_rank_pct"].to_numpy(dtype=float) >= self.top_rank


def _make(suffix, doc, mask, **attrs):
    @register
    class V(_Candidate):
        name = f"steady_drift_plus_{suffix}"
        description = doc
        def mask(self, ev):                                           # noqa: N805
            return mask(self, ev)
    for k, v in attrs.items():
        setattr(V, k, v)
    V.__name__ = f"Cand_{suffix}"


def _f(a):
    return np.nan_to_num(a, nan=-np.inf)


# ---- EQ-06 seasonal SUE
_make("seas_s", "Top decile of the seasonal-SUE rank (SUE >= 0)", lambda s, e: _f(e["sue_seas_rank"].to_numpy(float)) >= 0.9, arm="S")
_make("seas_s5", "Top 5% of the seasonal-SUE rank", lambda s, e: _f(e["sue_seas_rank"].to_numpy(float)) >= 0.95, arm="S5")
_make("seas_and", "Analyst top decile AND seasonal rank >= 0.667",
      lambda s, e: s.base_mask(e) & (_f(e["sue_seas_rank"].to_numpy(float)) >= 0.667), arm="AND")
_make("seas_or", "Analyst top decile OR seasonal top decile",
      lambda s, e: s.base_mask(e) | (_f(e["sue_seas_rank"].to_numpy(float)) >= 0.9), arm="OR")
_make("seas_avg", "Mean of the analyst and seasonal PIT ranks >= 0.9",
      lambda s, e: (e["sue_rank_pct"].to_numpy(float) + e["sue_seas_rank"].to_numpy(float)) / 2 >= 0.9, arm="AVG")

# ---- EQ-04 path exits
_make("stop5", "steady_drift_plus with a 5% close-based stop", lambda s, e: s.base_mask(e), arm="stop5", stop_pct=0.05)
_make("stop10", "steady_drift_plus with a 10% close-based stop", lambda s, e: s.base_mask(e), arm="stop10", stop_pct=0.10)
_make("trail", "steady_drift_plus with a latched trailing stop (3% activation, 2% trail)", lambda s, e: s.base_mask(e),
      arm="trail", trail_act=0.03, trail_amt=0.02)

# ---- EQ-01 EA1904-style
_ea = lambda s, e: (_f(e["sue_seasonal_raw"].to_numpy(float)) >= 1.5) & (_f(e["rvol_d"].to_numpy(float)) >= 1.5)
_make("ea1904", "Seasonal SUE >= 1.5 AND day-0 relative volume >= 1.5, 20-session hold, 5% stop", _ea, arm="ea1904",
      max_hold=20, stop_pct=0.05)
_make("ea1904_nostop", "EA1904-style entry, 20-session hold, no stop", _ea, arm="ea1904_nostop", max_hold=20)

# ---- EQ-05 Bouddat long leg
_bd = lambda s, e: _f(e["sue_analyst_raw"].to_numpy(float)) >= 0.635
_make("bouddat", "Analyst SUE >= 0.635, held to day0 + 85 calendar days", _bd, arm="bouddat", cal_days=85)
_make("bouddat_60s", "Analyst SUE >= 0.635, 60-session hold (control)", _bd, arm="bouddat_60s")

# ---- EQ-03 part C reaction arms
_make("react_agree", "Top-decile SUE whose day-0 reaction is >= +0.5%", lambda s, e: s.base_mask(e) & (e["react"].to_numpy(float) >= 0.005),
      arm="agree")
_make("react_contra", "Top-decile SUE whose day-0 reaction is <= -0.5%", lambda s, e: s.base_mask(e) & (e["react"].to_numpy(float) <= -0.005),
      arm="contrarian")
_make("react_flat", "Top-decile SUE with a flat (|r| < 0.5%) day-0 reaction",
      lambda s, e: s.base_mask(e) & (np.abs(e["react"].to_numpy(float)) < 0.005), arm="flat")
