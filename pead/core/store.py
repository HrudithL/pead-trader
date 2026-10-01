"""Where backtest results live and how the leaderboard is kept.

    results/<strategy>/summary.json     headline stats + strategy metadata + run provenance
    results/<strategy>/daily.csv.gz     date, daily_pnl, nav, daily_return, leg splits, exposures
    results/<strategy>/quarterlog.csv   per-quarter sizing decisions
    results/<strategy>/positions.parquet  every funded position with its P&L (git-ignored: large)
    results/<strategy>/report.md|.pdf + figures/   the per-strategy report
    results/leaderboard.json|.md        best strategy so far by asset class and metric
    results/comprehensive/              cross-strategy report
"""
import json
import subprocess
from datetime import datetime, timezone

import pandas as pd

from pead.common.paths import RUNS_DIR, REPO_ROOT

LEADERBOARD_METRICS = ("sharpe", "annualized_return", "calmar")


def run_dir(name):
    d = RUNS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _git_rev():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip() or None
    except Exception:
        return None


def _jsonable(o):
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if hasattr(o, "item"):
        o = o.item()
    if isinstance(o, float) and o != o:
        return None
    return o


def save_result(result) -> "Path":
    d = run_dir(result.name)
    result.daily.to_csv(d / "daily.csv.gz", index=False, float_format="%.10g", compression="gzip")
    result.quarterlog.to_csv(d / "quarterlog.csv", index=False, float_format="%.6g")
    if result.positions is not None:
        result.positions.to_parquet(d / "positions.parquet", index=False)
    payload = dict(result.meta, summary=_jsonable(result.summary),
                   run=dict(timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"), git_rev=_git_rev(),
                            pandas=pd.__version__))
    (d / "summary.json").write_text(json.dumps(_jsonable(payload), indent=2))
    update_leaderboard()
    return d


def load_result(name):
    """Rebuild a BacktestResult from disk (positions included when present)."""
    from pead.strategies.base import BacktestResult
    d = RUNS_DIR / name
    payload = json.loads((d / "summary.json").read_text())
    daily = pd.read_csv(d / "daily.csv.gz", parse_dates=["date"])
    q = pd.read_csv(d / "quarterlog.csv") if (d / "quarterlog.csv").exists() else pd.DataFrame()
    pos = pd.read_parquet(d / "positions.parquet") if (d / "positions.parquet").exists() else None
    summary = payload.pop("summary")
    payload.pop("run", None)
    return BacktestResult(name, payload.get("asset_class", ""), daily, summary, q, pos, payload)


_DAILY_CACHE = {}


def load_daily(name):
    """(daily DataFrame, asset_class) without touching the positions file -- cheap, cached."""
    if name not in _DAILY_CACHE:
        d = RUNS_DIR / name
        meta = json.loads((d / "summary.json").read_text())
        _DAILY_CACHE[name] = (pd.read_csv(d / "daily.csv.gz", parse_dates=["date"]), meta.get("asset_class", ""))
    return _DAILY_CACHE[name]


def saved_names():
    return sorted(p.parent.name for p in RUNS_DIR.glob("*/summary.json"))


def load_summaries() -> pd.DataFrame:
    rows = []
    for n in saved_names():
        p = json.loads((RUNS_DIR / n / "summary.json").read_text())
        rows.append({"name": n, "asset_class": p.get("asset_class"), "family": p.get("family"),
                     "status": p.get("status"), "parent": p.get("parent"), **p["summary"]})
    return pd.DataFrame(rows)


def update_leaderboard():
    """Rank every saved strategy; record the best per (asset class, metric). `validated` and
    `research` strategies compete; `reference` (cautionary counter-examples) and `unverified` do not."""
    df = load_summaries()
    if df.empty:
        return
    elig = df[df["status"].isin(["validated", "research"])]
    board = {}
    for ac, g in elig.groupby("asset_class"):
        board[ac] = {}
        for m in LEADERBOARD_METRICS:
            if m in g.columns and g[m].notna().any():
                best = g.loc[g[m].idxmax()]
                board[ac][m] = dict(strategy=best["name"], value=float(best[m]))
    (RUNS_DIR / "leaderboard.json").write_text(json.dumps(board, indent=2))
    cols = ["name", "asset_class", "status", "annualized_return", "annualized_vol", "sharpe", "max_drawdown", "calmar"]
    table = df.sort_values(["asset_class", "sharpe"], ascending=[True, False])[[c for c in cols if c in df]]
    lines = ["# Leaderboard", "", "Best so far (validated + research strategies):", ""]
    for ac, ms in board.items():
        lines.append(f"- **{ac}** -- " + "; ".join(f"best {m}: `{v['strategy']}` ({v['value']:.3g})" for m, v in ms.items()))
    lines += ["", "| " + " | ".join(table.columns) + " |", "|" + "---|" * len(table.columns)]
    for _, r in table.iterrows():
        lines.append("| " + " | ".join(f"{r[c]:.2%}" if c in ("annualized_return", "annualized_vol", "max_drawdown")
                                       else (f"{r[c]:.2f}" if isinstance(r[c], float) else str(r[c])) for c in table.columns) + " |")
    (RUNS_DIR / "leaderboard.md").write_text("\n".join(lines) + "\n")
