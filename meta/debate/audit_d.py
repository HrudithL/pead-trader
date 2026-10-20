import pandas as pd, numpy as np
SRC="C:/Users/hrudi/Documents/BEI/PEAD_Trading/.claude/worktrees/strategy-analysis-backtest-481b86"
f=pd.read_parquet(SRC+"/data/results/event_classification_features.parquet")
print(f.shape, list(f.columns)); print(f.describe().T.to_string())
p=pd.read_parquet(SRC+"/data/equity_vol_panel.parquet"); print(p.shape,list(p.columns))
e=pd.read_parquet(SRC+"/data/normalized_equity/equity_2005.parquet"); print(e.shape,list(e.columns)); print(e.describe().T.to_string())
