"""EQ-30 / EQ-21: compare SUE definitions as signals for forward returns, search vs holdout (read-only).

    python scripts/signal_report.py [--outcome ret_fwd_60d_mktadj] [--out results/signal_report]
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.common.paths import DATA_DIR, RUNS_DIR  # noqa: E402
from pead.core import event_stats as es, splits  # noqa: E402

SIGNALS = ["sue_primary", "sue_analyst", "sue_seasonal", "sue_analyst_r", "sue_rank_pct"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outcome", default="ret_fwd_60d_mktadj")
    ap.add_argument("--out", default=str(RUNS_DIR / "signal_report"))
    a = ap.parse_args()
    ev = pd.read_parquet(DATA_DIR / "event_options" / "decile_events_secid_all.parquet")
    ev = ev[ev["day0_status"] == "ok"].copy()
    ev["ann_quarter"] = ev["ann_quarter"].astype(str)
    s_q, h_q = splits.split_quarters(ev["ann_quarter"])
    parts = {"search": ev[ev["ann_quarter"].isin([str(q) for q in s_q])], "holdout": ev[ev["ann_quarter"].isin([str(q) for q in h_q])]}
    rows = []
    for sig in SIGNALS:
        for w, d in parts.items():
            ic = es.ic_summary(es.quarterly_ic(d, sig, a.outcome))
            sp = es.decile_spread(d, sig, a.outcome)
            rows.append(dict(signal=sig, window=w, n_events=int(d[sig].notna().sum()), **ic,
                             spread=sp.get("spread"), spread_t=sp.get("spread_t")))
    out = pd.DataFrame(rows).round(4)
    p = Path(a.out)
    p.mkdir(parents=True, exist_ok=True)
    out.to_csv(p / "signal_ic.csv", index=False)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
