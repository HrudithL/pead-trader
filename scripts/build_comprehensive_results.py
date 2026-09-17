"""Build one labeled comparison table from all available backtest summaries."""
import json
from pathlib import Path

import pandas as pd


DATA = Path(__file__).resolve().parents[1] / "data"


def load_rows():
    rows = []
    for path in sorted(DATA.glob("backtest_v2_*_summary.json")):
        with path.open() as handle:
            result = json.load(handle)
        result.update(asset_class="equity", source_summary=str(path.relative_to(DATA)))
        rows.append(result)
    for path in sorted(DATA.glob("backtest_options_*_summary.json")):
        with path.open() as handle:
            result = json.load(handle)
        result.update(asset_class="options", source_summary=str(path.relative_to(DATA)))
        result["strategy"] = path.name.removesuffix("_summary.json")
        result.setdefault("name", result["strategy"])
        rows.append(result)
    return rows


def main():
    rows = load_rows()
    if not rows:
        raise RuntimeError("No backtest summaries found")
    output = DATA / "backtest_comprehensive_comparison.csv"
    pd.DataFrame(rows).sort_values(["asset_class", "name", "hold_horizon_days"], na_position="last").to_csv(
        output, index=False
    )
    print(f"wrote {output} ({len(rows)} rows)")


if __name__ == "__main__":
    main()