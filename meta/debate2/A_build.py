"""A_build.py: per-event cumulative (log) stock / market paths to H=150 sessions for ALL vol-panel events (post-fix PIT deciles).
Writes meta/debate2/A_paths.npz (read-only inputs elsewhere)."""
import numpy as np, pandas as pd
from pathlib import Path
V = Path("C:/Users/hrudi/Documents/BEI/_pead_consolidation/vintage_fixed")
SRC = Path("C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86")
OUT = Path(__file__).resolve().parent
H = 150
vp = pd.read_parquet(V/"data/equity_vol_panel.parquet")
vp["day0_date"] = pd.to_datetime(vp["day0_date"])
vp = vp.drop_duplicates(["permno", "day0_date"]).reset_index(drop=True)
eq = pd.concat([pd.read_parquet(SRC/f"data/normalized_equity/equity_{y}.parquet", columns=["permno","date","ret"]) for y in range(1995,2015)], ignore_index=True)
eq = eq.sort_values(["permno","date"]).drop_duplicates(["permno","date"]).reset_index(drop=True)
mk = pd.read_parquet(SRC/"data/normalized_equity/market_benchmark.parquet", columns=["date","vwretd"]).drop_duplicates("date")
eq = eq.merge(mk, on="date", how="left")
eq["ret"] = eq["ret"].fillna(0.0); eq["vwretd"] = eq["vwretd"].fillna(0.0)
perm = eq["permno"].to_numpy()
last_row = pd.Series(np.arange(len(eq))).groupby(perm).max()
key = eq[["permno","date"]].reset_index().rename(columns={"index":"row","date":"day0_date"})
vp = vp.merge(key, on=["permno","day0_date"], how="left")
print("events", len(vp), "unmatched rows", vp.row.isna().sum())
vp = vp[vp.row.notna()].reset_index(drop=True)
row0 = vp.row.to_numpy(np.int64); last = last_row.reindex(vp.permno).to_numpy()
ls = eq.ret.to_numpy(float); lm = eq.vwretd.to_numpy(float)
idx = row0[:,None] + 1 + np.arange(H)[None,:]
valid = idx <= last[:,None]
idc = np.minimum(idx, len(eq)-1)
CS = np.cumsum(np.where(valid, ls[idc], 0.0), axis=1).astype(np.float32)
CM = np.cumsum(np.where(valid, lm[idc], 0.0), axis=1).astype(np.float32)
vp.drop(columns=["row"]).to_parquet(OUT/"A_events.parquet")
np.savez_compressed(OUT/"A_paths.npz", CS=CS, CM=CM)
# validation vs a strategy's hold_ret (hold_ret = SUM of daily mkt-adj returns)
p = pd.read_parquet(V/"runs/balanced_leverage/positions.parquet", columns=["permno","day0_date","holding_days","hold_ret"])
m = p.merge(vp.reset_index()[["index","permno","day0_date"]], on=["permno","day0_date"]).head(20000)
ii = m["index"].to_numpy(); h = m.holding_days.to_numpy()
for off in (-1,0,1):
    hh = np.clip(h-1+off,0,H-1)
    mine = CS[ii,hh]-CM[ii,hh]
    print("off",off,"corr",np.corrcoef(mine,m.hold_ret)[0,1],"mad",np.abs(mine-m.hold_ret).mean())
