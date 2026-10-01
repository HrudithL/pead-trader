"""Orchestrator: one run = the whole daily cycle. Meant to be invoked once per trading day, before
the open (see .github/workflows/live_paper_trading.yml), by cron/GitHub Actions/any worker:

  1. scan_new_earnings   -- pull fresh earnings surprises for the universe, append to the rolling
                             cohort (data/live/earnings_events.csv).
  2. trade_equities       -- Strategy 6 weight construction over the live cohort -> open new
                             equity positions (Alpaca paper order if configured, else a no-op that
                             still updates the internal ledger).
  3. trade_options        -- Kelly-optimal contract selection for newly-decile'd events -> open
                             new (simulated) option positions.
  4. mark_to_market       -- reprice every open position, close anything past its holding-period
                             horizon, feed realized option outcomes back into decile_model, and
                             append today's NAV row.

Run with --dry-run to execute the full pipeline against live data without writing any ledger
state or submitting any orders (useful for checking the pipeline still works against today's real
data without touching paper-trading state). Run with --mock-data to skip all network calls
entirely and exercise the pipeline against synthetic data (useful in CI / no-network environments).
"""
import argparse
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_trading import config, ledger, data_sources, equity_signal, options_signal, decile_model
from live_trading import broker_alpaca, broker_options_sim


def log(msg):
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)


# ------------------------------------------------------------------------------------------------
# mock data (for --mock-data / no-network testing, mirrors the shape real data_sources returns)
# ------------------------------------------------------------------------------------------------

def _mock_universe(n=60):
    return [{"ticker": f"MOCK{i}", "sector": ["Tech", "Health Care", "Financials"][i % 3]}
            for i in range(n)]


def _mock_surprise(ticker, rng):
    return {
        "ticker": ticker, "report_date": date.today().isoformat(),
        "eps_estimate": 1.0, "eps_reported": round(1.0 + rng.normal(0, 0.1), 2),
        "surprise_pct": round(float(rng.normal(0, 8)), 2), "sue_z": float(rng.normal(0, 1.2)),
    }


# ------------------------------------------------------------------------------------------------
# step 1: scan for new earnings surprises
# ------------------------------------------------------------------------------------------------

def scan_new_earnings(universe, existing_events, mock, rng):
    last_seen = {}
    for row in existing_events:
        t = row["ticker"]
        if t not in last_seen or row["report_date"] > last_seen[t]:
            last_seen[t] = row["report_date"]

    new_rows = []
    for u in universe:
        ticker = u["ticker"]
        if mock:
            if last_seen.get(ticker) == date.today().isoformat():
                continue  # already recorded a mock report for this ticker today
            if rng.random() > 0.2:  # ~20% of universe "reports" on a mock run
                continue
            surprise = _mock_surprise(ticker, rng)
            price = {"market_cap": rng.uniform(5e9, 5e11), "adv21_dollars": rng.uniform(5e7, 5e9)}
        else:
            try:
                surprise = data_sources.get_earnings_surprise(ticker, as_of=last_seen.get(ticker))
            except Exception as e:
                log(f"  WARN earnings fetch failed for {ticker}: {e}")
                continue
            if surprise is None:
                continue
            price = data_sources.get_price_stats(ticker)
            if price is None:
                log(f"  WARN price fetch failed for {ticker}, skipping event")
                continue
        row = {
            **surprise, "detected_at": ledger.now_iso(), "sector": u["sector"],
            "market_cap": price["market_cap"], "adv21_dollars": price["adv21_dollars"],
            "processed_for_signal": "False",
        }
        new_rows.append(row)
        log(f"  new earnings surprise: {ticker} sue_z={row['sue_z']:.2f}")
    return new_rows


# ------------------------------------------------------------------------------------------------
# step 2: equity trades
# ------------------------------------------------------------------------------------------------

def cohort_window_events(all_events, window_days=config.EQUITY_COHORT_WINDOW_TRADING_DAYS):
    cutoff = (date.today() - timedelta(days=int(window_days * 1.45))).isoformat()  # trading->calendar days
    return [e for e in all_events if e["report_date"] >= cutoff]


def trade_equities(all_events, equity_positions, dry_run, mock):
    cohort = cohort_window_events(all_events)
    unprocessed = [e for e in cohort if e.get("processed_for_signal") != "True"]
    if len(cohort) < config.EQUITY_MIN_COHORT_SIZE or not unprocessed:
        log(f"  equity: cohort={len(cohort)}, {len(unprocessed)} unprocessed -- nothing to do")
        return equity_positions, all_events

    df = pd.DataFrame(cohort)
    df["sue_z"] = df["sue_z"].astype(float)
    df["market_cap"] = pd.to_numeric(df["market_cap"], errors="coerce")
    weighted = equity_signal.compute_target_weights(df)

    already_open = {p["ticker"] for p in equity_positions.values() if p["status"] == "open"}
    to_open = weighted[(weighted["weight"] != 0) & (~weighted["ticker"].isin(already_open))
                        & (weighted["ticker"].isin([e["ticker"] for e in unprocessed]))]

    adv_map = {r["ticker"]: float(r["adv21_dollars"]) for r in cohort if r.get("adv21_dollars")}
    sleeve_nav = ledger.latest_nav("equity")
    sized = equity_signal.size_positions(to_open, sleeve_nav, adv_map)

    for _, row in sized.iterrows():
        price_stats = {"last_price": max(row["market_cap"] / 1e9, 1.0)} if mock else \
            data_sources.get_price_stats(row["ticker"])
        if not price_stats or not price_stats.get("last_price"):
            continue
        price = price_stats["last_price"]
        shares = int(row["target_dollars"] / price)
        if shares == 0:
            continue
        side = "buy" if shares > 0 else "sell"
        if not dry_run:
            broker_alpaca.submit_market_order(row["ticker"], side, abs(shares))
            ledger.log_trade(sleeve="equity", ticker=row["ticker"], action="open", side=side,
                              quantity=abs(shares), price=price, notional=abs(shares) * price,
                              reason="strategy6_entry")
        pos_id = str(uuid.uuid4())[:8]
        equity_positions[pos_id] = {
            "ticker": row["ticker"], "shares": shares, "entry_price": price,
            "entry_date": date.today().isoformat(), "status": "open",
        }
        log(f"  equity OPEN {row['ticker']}: {shares} sh @ ~{price:.2f}")

    for e in unprocessed:
        e["processed_for_signal"] = "True"
    return equity_positions, all_events


# ------------------------------------------------------------------------------------------------
# step 3: options trades
# ------------------------------------------------------------------------------------------------

def trade_options(all_events, options_positions, dry_run, mock):
    cohort = cohort_window_events(all_events)
    if len(cohort) < config.EQUITY_MIN_COHORT_SIZE:
        return options_positions
    sue_zs = [float(e["sue_z"]) for e in cohort]
    todays = [e for e in all_events if e["report_date"] == date.today().isoformat()]

    sleeve_nav = ledger.latest_nav("options")
    outstanding_premium = sum(
        p["n_contracts"] * p["entry_premium"] * 100 for p in options_positions.values()
        if p["status"] == "open")

    for e in todays:
        decile = decile_model.decile_for_sue_z(float(e["sue_z"]), sue_zs)
        if decile not in (1, 2, 9, 10):  # trade only the SUE extremes, matching the backtest's trim
            continue
        cp_flag_bias = "C" if decile >= 9 else "P"
        mean_ret, std_ret = decile_model.get_decile_params(decile)

        if mock:
            spot = 100.0
            chain = pd.DataFrame({
                "strike": [95, 100, 105], "cp_flag": [cp_flag_bias] * 3,
                "mid": [6.0, 4.0, 2.5], "openInterest": [50, 50, 50],
            })
        else:
            spot, expiry, chain = data_sources.get_option_chain_candidates(e["ticker"])
            if spot is None or chain.empty:
                continue
            chain = chain[chain["cp_flag"] == cp_flag_bias]
            if chain.empty:
                continue

        best = options_signal.select_optimal_contract(spot, chain, mean_ret, std_ret)
        if best is None:
            continue
        n_contracts, premium_dollars = options_signal.size_option_position(
            best, sleeve_nav, outstanding_premium)
        if n_contracts <= 0:
            continue
        fill = broker_options_sim.simulate_open(best["mid"], n_contracts, "buy")
        outstanding_premium += n_contracts * fill["fill_price"] * 100

        pos_id = str(uuid.uuid4())[:8]
        options_positions[pos_id] = {
            "ticker": e["ticker"], "cp_flag": cp_flag_bias, "strike": float(best["strike"]),
            "n_contracts": n_contracts, "entry_premium": fill["fill_price"],
            "entry_spot": spot, "entry_date": date.today().isoformat(),
            "decile": decile, "status": "open",
        }
        if not dry_run:
            ledger.log_trade(sleeve="options", ticker=e["ticker"], action="open", side="buy",
                              quantity=n_contracts, price=fill["fill_price"],
                              notional=n_contracts * fill["fill_price"] * 100,
                              reason=f"kelly_decile{decile}")
        log(f"  options OPEN {e['ticker']} {cp_flag_bias}{best['strike']}: "
            f"{n_contracts}x @ {fill['fill_price']:.2f} (decile {decile})")
    return options_positions


# ------------------------------------------------------------------------------------------------
# step 4: mark-to-market + exits
# ------------------------------------------------------------------------------------------------

def mark_to_market(equity_positions, options_positions, dry_run, mock):
    today = date.today()
    equity_alloc = config.INITIAL_CAPITAL_TOTAL * config.EQUITY_SLEEVE_FRAC
    options_alloc = config.INITIAL_CAPITAL_TOTAL * config.OPTIONS_SLEEVE_FRAC
    equity_realized = equity_unrealized = 0.0
    options_realized = options_unrealized = 0.0
    outstanding_premium = 0.0

    for pos_id, p in equity_positions.items():
        entry_date = date.fromisoformat(p["entry_date"])
        held_days = (today - entry_date).days
        stats = None if mock else data_sources.get_price_stats(p["ticker"])
        price = stats["last_price"] if stats else p["entry_price"]
        if p["status"] == "open" and held_days >= config.EQUITY_HOLDING_TRADING_DAYS * 1.45:
            pnl = p["shares"] * (price - p["entry_price"])
            p["status"], p["exit_price"], p["exit_date"], p["realized_pnl"] = "closed", price, today.isoformat(), pnl
            if not dry_run:
                side = "sell" if p["shares"] > 0 else "buy"
                broker_alpaca.submit_market_order(p["ticker"], side, abs(p["shares"]))
                ledger.log_trade(sleeve="equity", ticker=p["ticker"], action="close", side=side,
                                  quantity=abs(p["shares"]), price=price, notional=abs(p["shares"]) * price,
                                  position_id=pos_id, reason="holding_period_exit")
            log(f"  equity CLOSE {p['ticker']}: pnl={pnl:.2f}")
        if p["status"] == "closed":
            equity_realized += p.get("realized_pnl", 0.0)
        else:
            equity_unrealized += p["shares"] * (price - p["entry_price"])

    for pos_id, p in options_positions.items():
        entry_date = date.fromisoformat(p["entry_date"])
        held_days = (today - entry_date).days
        if mock:
            mid = p["entry_premium"]
            spot_now = p["entry_spot"]
        else:
            spot_now, _, chain = data_sources.get_option_chain_candidates(p["ticker"])
            row = None
            if chain is not None and not chain.empty:
                match = chain[(chain["strike"] == p["strike"]) & (chain["cp_flag"] == p["cp_flag"])]
                row = match.iloc[0] if not match.empty else None
            mid = float(row["mid"]) if row is not None else p["entry_premium"]
            spot_now = spot_now or p["entry_spot"]
        if p["status"] == "open" and held_days >= config.OPTIONS_HOLDING_TRADING_DAYS * 1.45:
            fill = broker_options_sim.simulate_close(mid, p["n_contracts"])
            entry_cost = p["n_contracts"] * p["entry_premium"] * 100
            pnl = fill["proceeds"] - entry_cost
            p["status"], p["exit_premium"], p["exit_date"], p["realized_pnl"] = (
                "closed", fill["fill_price"], today.isoformat(), pnl)
            underlying_ret = (spot_now - p["entry_spot"]) / p["entry_spot"]
            if not dry_run:
                decile_model.update_with_realized_return(p["decile"], underlying_ret)
                ledger.log_trade(sleeve="options", ticker=p["ticker"], action="close", side="sell",
                                  quantity=p["n_contracts"], price=fill["fill_price"],
                                  notional=fill["proceeds"], position_id=pos_id,
                                  reason="holding_period_exit")
            log(f"  options CLOSE {p['ticker']}: pnl={pnl:.2f}")
        if p["status"] == "closed":
            options_realized += p.get("realized_pnl", 0.0)
        else:
            options_unrealized += p["n_contracts"] * 100 * (mid - p["entry_premium"])
            outstanding_premium += p["n_contracts"] * mid * 100

    equity_nav = equity_alloc + equity_realized + equity_unrealized
    options_nav = options_alloc + options_realized + options_unrealized
    gross_exposure = sum(abs(p["shares"] * p.get("entry_price", 0)) for p in equity_positions.values()
                          if p["status"] == "open")

    if not dry_run:
        ledger.append_nav_row({
            "date": today.isoformat(), "equity_sleeve_nav": round(equity_nav, 2),
            "options_sleeve_nav": round(options_nav, 2), "total_nav": round(equity_nav + options_nav, 2),
            "equity_gross_exposure": round(gross_exposure, 2),
            "options_outstanding_premium": round(outstanding_premium, 2),
            "n_equity_positions": sum(1 for p in equity_positions.values() if p["status"] == "open"),
            "n_options_positions": sum(1 for p in options_positions.values() if p["status"] == "open"),
        })
    log(f"  NAV: equity={equity_nav:,.0f} options={options_nav:,.0f} total={equity_nav+options_nav:,.0f}")
    return equity_positions, options_positions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="run the full pipeline, write no state")
    ap.add_argument("--mock-data", action="store_true", help="use synthetic data, no network calls")
    ap.add_argument("--limit-universe", type=int, default=None, help="cap tickers scanned (testing)")
    ap.add_argument("--seed", type=int, default=None, help="RNG seed for --mock-data (default: random)")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    log(f"=== live_trading run_daily start (dry_run={args.dry_run}, mock={args.mock_data}) ===")

    universe = _mock_universe(args.limit_universe or 60) if args.mock_data else data_sources.load_universe()
    if args.limit_universe and not args.mock_data:
        universe = universe[: args.limit_universe]

    all_events = ledger.load_earnings_events()
    log(f"step 1/4: scanning {len(universe)} tickers for new earnings surprises...")
    new_events = scan_new_earnings(universe, all_events, args.mock_data, rng)
    if not args.dry_run:
        ledger.append_earnings_events(new_events)
    all_events = all_events + new_events

    equity_positions = ledger.load_equity_positions()
    options_positions = ledger.load_options_positions()

    log("step 2/4: equity signal + trades...")
    equity_positions, all_events = trade_equities(all_events, equity_positions, args.dry_run, args.mock_data)

    log("step 3/4: options signal + trades...")
    options_positions = trade_options(all_events, options_positions, args.dry_run, args.mock_data)

    log("step 4/4: mark-to-market + exits...")
    equity_positions, options_positions = mark_to_market(
        equity_positions, options_positions, args.dry_run, args.mock_data)

    if not args.dry_run:
        ledger.save_equity_positions(equity_positions)
        ledger.save_options_positions(options_positions)
        ledger.rewrite_earnings_events(all_events)
        ledger.log_run("daily", {
            "n_new_events": len(new_events), "n_equity_open": sum(
                1 for p in equity_positions.values() if p["status"] == "open"),
            "n_options_open": sum(1 for p in options_positions.values() if p["status"] == "open"),
        })
    log("=== done ===")


if __name__ == "__main__":
    main()
