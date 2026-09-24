"""Plain-file state store for the live paper-trading engine.

Positions are JSON dicts keyed by an internal position id (so a run can be re-executed against
the same state without needing a database); NAV history and the trade log are append-only CSVs so
`git diff` on a run's commit shows exactly what changed. Every writer here reads-modifies-writes
the whole (small) file -- state is a few hundred open positions at most, never large enough for
that to matter.
"""
import csv
import json
from datetime import datetime, timezone

from . import config


def _load_json(path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True, default=str)
    tmp.replace(path)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# --- equity / options positions --------------------------------------------------------------

def load_equity_positions():
    return _load_json(config.EQUITY_POSITIONS_JSON, {})


def save_equity_positions(positions):
    _save_json(config.EQUITY_POSITIONS_JSON, positions)


def load_options_positions():
    return _load_json(config.OPTIONS_POSITIONS_JSON, {})


def save_options_positions(positions):
    _save_json(config.OPTIONS_POSITIONS_JSON, positions)


# --- earnings events cohort (rolling window used for cross-sectional SUE ranking) -------------

EARNINGS_EVENT_FIELDS = [
    "ticker", "report_date", "detected_at", "eps_estimate", "eps_reported", "surprise_pct",
    "sue_z", "sector", "market_cap", "adv21_dollars", "processed_for_signal",
]


def load_earnings_events():
    if not config.EARNINGS_EVENTS_CSV.exists():
        return []
    with open(config.EARNINGS_EVENTS_CSV, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_earnings_events(rows):
    if not rows:
        return
    exists = config.EARNINGS_EVENTS_CSV.exists()
    with open(config.EARNINGS_EVENTS_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=EARNINGS_EVENT_FIELDS)
        if not exists:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in EARNINGS_EVENT_FIELDS})


def rewrite_earnings_events(rows):
    with open(config.EARNINGS_EVENTS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=EARNINGS_EVENT_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in EARNINGS_EVENT_FIELDS})


# --- NAV history -------------------------------------------------------------------------------

NAV_FIELDS = [
    "date", "equity_sleeve_nav", "options_sleeve_nav", "total_nav", "equity_gross_exposure",
    "options_outstanding_premium", "n_equity_positions", "n_options_positions",
]


def append_nav_row(row):
    exists = config.NAV_HISTORY_CSV.exists()
    with open(config.NAV_HISTORY_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=NAV_FIELDS)
        if not exists:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in NAV_FIELDS})


def load_nav_history():
    if not config.NAV_HISTORY_CSV.exists():
        return []
    with open(config.NAV_HISTORY_CSV, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def latest_nav(sleeve):
    hist = load_nav_history()
    if not hist:
        if sleeve == "equity":
            return config.INITIAL_CAPITAL_TOTAL * config.EQUITY_SLEEVE_FRAC
        return config.INITIAL_CAPITAL_TOTAL * config.OPTIONS_SLEEVE_FRAC
    row = hist[-1]
    key = "equity_sleeve_nav" if sleeve == "equity" else "options_sleeve_nav"
    return float(row[key])


# --- trade log -----------------------------------------------------------------------------

TRADE_FIELDS = [
    "timestamp", "sleeve", "ticker", "action", "side", "quantity", "price", "notional",
    "position_id", "reason",
]


def log_trade(**kwargs):
    exists = config.TRADE_LOG_CSV.exists()
    with open(config.TRADE_LOG_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_FIELDS)
        if not exists:
            w.writeheader()
        row = {"timestamp": now_iso()}
        row.update(kwargs)
        w.writerow({k: row.get(k, "") for k in TRADE_FIELDS})


def log_run(phase, summary):
    with open(config.RUN_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now_iso(), "phase": phase, **summary}, default=str) + "\n")
