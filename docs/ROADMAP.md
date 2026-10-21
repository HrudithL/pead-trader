# Roadmap: things known to be missing

Written 2026-10-21. Items are listed with why they matter and a rough definition of done. Add to the bottom; strike through when done.

| # | Item | Why it matters | Done when |
|---|---|---|---|
| 1 | **Investable small-cap benchmark** | "Alpha vs the cap-weighted index" for the long-only books (Steady Drift) includes the small/mid-cap premium of 1997-2013; the passive equal-weight index (16.4%/yr over 2000-13) is not investable (micro-caps, free rebalancing). The shuffled-surprise placebo is only a proxy. | A documented, reproducible benchmark series (same universe filters and cost model as the strategy, value-weighted, monthly rebalance) in `pead/core/`, and `steady_drift*` reports alpha against it next to alpha vs the CRSP VW index. |
| 2 | Real T-bill series | Idle cash yield and the risk throttle's cash leg use annual averages typed from memory (`pead/core/rates.py`). | Replace by a downloaded daily or monthly 3-month bill series; re-run the four `steady_drift*` strategies. |
| 3 | Live-signal validation | The live trader scores yfinance EPS surprises, the research uses IBES analyst SUE; top-500 announcers earned ~0.5%/yr alpha in 2009-13 in the full-universe analysis. | Steady Drift run on the live signal and an S&P-style universe with an impact term; capacity vs NAV re-measured. |
| 4 | Port `steady_drift` to the live engine | `live_ready = False`: the live engine needs fixed 60-session holds, T-bill cash and the risk throttle, and must stop trading shorts and options at mid. | `python -m pead live run` can paper-trade it. |
| 5 | Extra signal information (revisions, dispersion, earnings-day return) | Nothing tuned on the current signal raised surprise-specific alpha; only new information can. | Pull IBES detail from WRDS, add to the event table, test with the shuffled-surprise placebo. |
| 6 | Decay-aware exposure | Surprise skill alpha fell 9.4 -> 7.9 -> 4.1 points across eras. | A strictly-past rolling estimate scales exposure; placebo-checked. |
| 7 | Data after 2013 | Every result ends 2013; the design is in-sample on 1996-2013. | Extend CRSP/IBES to the present and re-run as a true holdout. |
| 8 | Sector-aware admission in the engine | The prototype's 20% sector cap made no difference but is a natural risk limit. | Engine option, tested for causality. |
| 9 | Options on a bid/ask-aware selector | Corrected options books earn ~0. | A selector scored at the ask ("Tier B" in docs/FIXES.md) shows an edge before any satellite is reconsidered. |
