"""Second pre-registered grid: breadth (smaller positions, more names) x invested cap x risk target. Same acceptance rules."""
import sys, itertools
sys.path.insert(0, "meta")
from steady_grid import *
if __name__ == "__main__":
    rows = []
    for bu, cap, tgt in itertools.product([0.002, 0.0025, 0.003], [0.75, 0.80], [0.10, 0.12]):
        lab = f"bu{bu}_cap{cap}_vol{tgt}"
        rows.append(evaluate(lab, base_unit_fraction=bu, max_gross_leverage=cap, risk_target_vol=tgt, name_cap_frac=0.01)); print(lab, flush=True)
    R = pd.DataFrame(rows); R.to_csv("meta/out/steady_grid2.csv", index=False)
    f = R.copy()
    for c in [c for c in f if c.endswith(("_ret", "_dd")) or c == "alpha"]: f[c] = (f[c] * 100).round(2)
    pd.options.display.width = 250
    print(f[["variant", "all_ret", "all_sharpe", "all_dd", "e3_ret", "e3_sharpe", "h1_sharpe", "h2_sharpe", "beta", "alpha", "alpha_t", "n_pos", "liq_capped", "avg_exposure", "max_mv_over_nav"]].round(2).to_string(index=False))
    print("variants run (this grid):", len(R))
