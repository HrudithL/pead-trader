"""EQ-28 part A: has the SUE payoff compressed after the pre-registered break dates? Quarter-clustered, read-only.

    python scripts/era_interaction.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pead.common.paths import DATA_DIR, RUNS_DIR  # noqa: E402
from pead.core import event_stats as es  # noqa: E402

BREAKS = {"decimalisation_2001-04-09": "2001-04-09", "regnms_2005-01-01": "2005-01-01", "crisis_2008-01-01": "2008-01-01"}


def main():
    ev = pd.read_parquet(DATA_DIR / "event_options" / "decile_events_secid_all.parquet")
    ev = ev[ev["day0_status"] == "ok"].copy()
    ev["ann_quarter"] = ev["ann_quarter"].astype(str)
    ev["ff12_sector"] = ev["ff12_sector"].astype(str)
    ev = ev.sort_values(["permno", "ann_quarter"]).drop_duplicates(["permno", "ann_quarter"])
    rows = []
    for y in ("ret_fwd_1d_mktadj", "ret_fwd_60d_mktadj"):
        for name, bd in BREAKS.items():
            rows.append(dict(outcome=y, brk=name, **es.era_interaction(ev, y, "sue_rank_pct", bd)))
    out = pd.DataFrame(rows).round(4)
    p = RUNS_DIR / "era_interaction"
    p.mkdir(parents=True, exist_ok=True)
    out.to_csv(p / "era_interaction.csv", index=False)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
