import pandas as pd, numpy as np
SRC="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86"
ev=pd.read_parquet(SRC+"/data/events/equity_events_1996_2013.parquet")
print(ev.shape); print(ev.dtypes.to_string())
print(ev.head(3).T.to_string())
for c in ['day0_status','sue_source','day0_lag_days']:
    print(ev[c].value_counts(dropna=False).head(10))
print(ev['anntims'].describe() if 'anntims' in ev else '')
