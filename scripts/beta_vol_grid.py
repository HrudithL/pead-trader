"""Is the 'calm stocks earn more' result really a low-beta result?  Alpha per $ by stock-specific volatility tercile (rows)
x beta tercile (columns) for the reference book, with size-matched alpha (same definition as strategy_profiles.py), money
shares and quarter-clustered t-statistics. Also the two paired contrasts that answer the question:
  calm minus jumpy, holding beta fixed   and   low-beta minus high-beta, holding volatility fixed.
Writes results/profiles_beta_vol_grid.csv.   python scripts/beta_vol_grid.py [strategy]   (default balanced_leverage)
Run with PEAD_DATA_DIR / PEAD_RAW_EQUITY_DIR pointing at the corrected data vintage (see strategy_profiles.py)."""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategy_profiles import prep, R, _quarter_series, _tstat

name = sys.argv[1] if len(sys.argv) > 1 else "balanced_leverage"
p = prep(name)
p["vol3"] = p["ivol_252d_tercile"].map({0: "calm", 1: "average", 2: "jumpy"})
p["beta3x"] = p["beta3"].str.replace("-beta", "")
p = p.dropna(subset=["vol3", "beta3x"])
VOL, BETA = ["calm", "average", "jumpy"], ["low", "mid", "high"]
tot_n, tot_a = p["notional"].abs().sum(), p["alpha_net"].sum()

def cell(g, kind, vol, beta):
    n = g["notional"].abs().sum()
    qs = _quarter_series(g)
    return dict(strategy=name, kind=kind, vol=vol, beta=beta, positions=len(g), money_pct=100 * n / tot_n,
                alpha_M=g["alpha_net"].sum() / 1e6, alpha_share_pct=100 * g["alpha_net"].sum() / tot_a,
                alpha_bp=1e4 * g["alpha_net"].sum() / n, t=_tstat(qs), quarters=len(qs),
                real_bp=1e4 * g["net_pnl"].sum() / n, avg_beta=float(g["beta_252d"].mean()))

rows = []
for v in VOL:
    for b in BETA:
        rows.append(cell(p[(p.vol3 == v) & (p.beta3x == b)], "cell", v, b))
for v in VOL:
    rows.append(cell(p[p.vol3 == v], "vol margin", v, "all"))
for b in BETA:
    rows.append(cell(p[p.beta3x == b], "beta margin", "all", b))

def paired(a, b):
    d = (_quarter_series(a) - _quarter_series(b)).dropna()
    return float(d.mean()), _tstat(d), len(d)

for b in BETA:   # volatility effect with beta held fixed
    m, t, k = paired(p[(p.vol3 == "calm") & (p.beta3x == b)], p[(p.vol3 == "jumpy") & (p.beta3x == b)])
    rows.append(dict(strategy=name, kind="contrast: calm minus jumpy", vol="calm-jumpy", beta=b, alpha_bp=m, t=t, quarters=k))
for v in VOL:    # beta effect with volatility held fixed
    m, t, k = paired(p[(p.vol3 == v) & (p.beta3x == "low")], p[(p.vol3 == v) & (p.beta3x == "high")])
    rows.append(dict(strategy=name, kind="contrast: low-beta minus high-beta", vol=v, beta="low-high", alpha_bp=m, t=t, quarters=k))
m, t, k = paired(p[p.vol3 == "calm"], p[p.vol3 == "jumpy"])
rows.append(dict(strategy=name, kind="contrast: calm minus jumpy", vol="calm-jumpy", beta="all (not beta-matched)", alpha_bp=m, t=t, quarters=k))
m, t, k = paired(p[p.beta3x == "low"], p[p.beta3x == "high"])
rows.append(dict(strategy=name, kind="contrast: low-beta minus high-beta", vol="all (not vol-matched)", beta="low-high", alpha_bp=m, t=t, quarters=k))
# overlap of the two tags
rows.append(dict(strategy=name, kind="overlap", vol="jumpy", beta="high", alpha_bp=np.nan,
                 money_pct=100 * ((p.vol3 == "jumpy") & (p.beta3x == "high")).sum() / (p.vol3 == "jumpy").sum()))
rows.append(dict(strategy=name, kind="overlap", vol="calm", beta="high", alpha_bp=np.nan,
                 money_pct=100 * ((p.vol3 == "calm") & (p.beta3x == "high")).sum() / (p.vol3 == "calm").sum()))
out = pd.DataFrame(rows)
out.to_csv(R / "profiles_beta_vol_grid.csv", index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
print(out.round(2).to_string())
