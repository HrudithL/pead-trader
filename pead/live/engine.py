"""The daily paper-trading cycle, driven by registered strategies.

    1. scan    new earnings surprises across the universe -> rolling cohort (data/live/earnings_events.csv)
    2. equity  strategy.weights(cohort events) -> dollar targets -> shared capacity caps -> broker orders
    3. options score today's reporters' live option chains with the Kelly objective -> premium-budgeted buys
    4. mark    reprice open positions, close those past their holding window, append the NAV row

Nothing about HOW to trade is defined here: the equity weights come from `EquityStrategy.weights`, the
sizing from the strategy's `Sizing` and `pead.engines.equity.cap_notionals`, the contract choice from
`pead.core.kelly`, and the cost model from the options strategy's own `OptionsSizing`. Swap
PEAD_LIVE_EQUITY / PEAD_LIVE_OPTIONS to paper-trade a different registered strategy.

One NAV, two sleeves: the equity book runs at `sleeve multiple x strategy sizing` and the options book at
`multiple x premium cap`, where the multiples come from the PEAD_LIVE_BLEND portfolio strategy
(hedged_blend: 0.8x equity, 1.8x options) -- the same stacked structure the backtest blend uses.
"""
import uuid
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from pead.core import kelly
from pead.engines.equity import cap_notionals
from pead.strategies import registry

from . import broker_options_sim, config, data_sources, decile_model, features, ledger
from .brokers import default_broker, LedgerBroker


def log(msg):
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)


def _hold_cal_days(trading_days):
    return int(trading_days * config.CALENDAR_PER_TRADING_DAY)


class LiveEngine:
    def __init__(self, dry_run=False, mock=False, broker=None, seed=None, limit_universe=None):
        self.dry_run, self.mock, self.limit = dry_run, mock, limit_universe
        self.rng = np.random.default_rng(seed)
        self.broker = broker or (LedgerBroker() if mock else default_broker())
        self.equity = registry.get(config.EQUITY_STRATEGY)
        self.options = registry.get(config.OPTIONS_STRATEGY)
        blend = registry.get(config.BLEND_STRATEGY) if config.BLEND_STRATEGY in registry.all_strategies() else None
        w = getattr(blend, "sleeve_weights", {}) if blend else {}
        self.equity_multiple = w.get(config.EQUITY_STRATEGY, 1.0)
        self.options_multiple = w.get(config.OPTIONS_STRATEGY, 1.0)
        self.today = date.today()

    # ---- 1. scan -----------------------------------------------------------------------------
    def scan(self, universe, existing):
        last_seen = {}
        for r in existing:
            if r["ticker"] not in last_seen or r["report_date"] > last_seen[r["ticker"]]:
                last_seen[r["ticker"]] = r["report_date"]
        spy = None if self.mock else data_sources.get_market_returns()
        new = []
        for u in universe:
            t = u["ticker"]
            if self.mock:
                if self.rng.random() > 0.25:
                    continue
                s = dict(ticker=t, report_date=self.today.isoformat(), eps_estimate=1.0, eps_reported=1.0,
                         surprise_pct=float(self.rng.normal(0, 8)), sue_z=float(self.rng.normal(0, 1.2)))
                px = dict(market_cap=float(self.rng.uniform(5e9, 5e11)), adv21_dollars=float(self.rng.uniform(5e7, 5e9)))
                iv, tv = float(self.rng.uniform(0.008, 0.03)), float(self.rng.uniform(0.01, 0.035))
            else:
                try:
                    s = data_sources.get_earnings_surprise(t, as_of=last_seen.get(t))
                    px = data_sources.get_price_stats(t) if s else None
                except Exception as e:  # one bad ticker must not stop the run
                    log(f"  WARN {t}: {e}")
                    continue
                if not s or not px:
                    continue
                iv, tv = data_sources.get_vol_stats(t, spy)
            row = {**s, "detected_at": ledger.now_iso(), "sector": u["sector"], "market_cap": px["market_cap"],
                   "adv21_dollars": px["adv21_dollars"], "ivol_252d": iv, "tvol_60d": tv, "processed_for_signal": "False"}
            new.append(row)
            log(f"  new surprise {t}: sue_z={row['sue_z']:.2f}")
        return new

    def _cohort(self, events):
        cutoff = (self.today - timedelta(days=_hold_cal_days(config.COHORT_WINDOW_TRADING_DAYS))).isoformat()
        return [e for e in events if e["report_date"] >= cutoff]

    def _unit_dollars(self, nav, events):
        """Dollars per unit of strategy weight.

        The backtest's `base_unit_fraction` is calibrated to a book of ~1,400 concurrently open names (the whole
        CRSP universe). The S&P 500 universe has roughly a seventh of the reporters, so the same fraction would leave
        the book nearly empty. Keep the backtest's STEADY-STATE gross exposure instead: size one unit so that the
        expected number of open positions at mean |weight| 0.5 sums to the backtest's gross/NAV, i.e.
            unit = (avg_open_bt x 0.5 x base_unit_fraction) x NAV x multiple / (0.5 x expected_open_live)."""
        s = self.equity.sizing
        bt_open = 1400.0
        try:
            import json
            from pead.common.paths import RUNS_DIR
            bt_open = float(json.loads((RUNS_DIR / self.equity.name / "summary.json").read_text())["summary"]["avg_n_open_positions"])
        except Exception:
            pass
        gross_x = min(bt_open * 0.5 * s.base_unit_fraction, s.max_gross_leverage)
        window = (self.today - timedelta(days=91)).isoformat()
        n_recent = sum(e["report_date"] >= window for e in events)
        per_quarter = max(n_recent, 0.7 * (self.limit or 500) if self.mock else 350)
        expected_open = max(per_quarter * 0.7 * (np.mean(list(features.holding_table().values()) or [60]) / 63.0), 20.0)
        return gross_x * nav * self.equity_multiple / (0.5 * expected_open)

    # ---- 2. equity ---------------------------------------------------------------------------
    def trade_equities(self, events, positions):
        cohort = self._cohort(events)
        pending = {e["ticker"] + e["report_date"] for e in cohort if e.get("processed_for_signal") != "True"}
        if len(cohort) < config.MIN_COHORT_SIZE or not pending:
            log(f"  equity: cohort={len(cohort)}, pending={len(pending)} -- nothing to do")
            return positions
        hist_cut = (self.today - timedelta(days=_hold_cal_days(config.COHORT_WINDOW_TRADING_DAYS + 63))).isoformat()
        first = min(e["report_date"] for e in cohort)
        history = pd.DataFrame([e for e in events if hist_cut <= e["report_date"] < first])
        ev = features.build_events(pd.DataFrame(cohort), history if len(history) else None)
        w = self.equity.weights(ev)
        ev["weight"] = w
        ev = ev[(ev["weight"] != 0) & (ev["ticker"] + ev["report_date"]).isin(pending)]
        open_tickers = {p["ticker"] for p in positions.values() if p["status"] == "open"}
        ev = ev[~ev["ticker"].isin(open_tickers)]
        if ev.empty:
            return positions

        nav = ledger.latest_nav()
        s = self.equity.sizing
        base_unit = self._unit_dollars(nav, events)
        gross_open = sum(abs(p["shares"] * p["entry_price"]) for p in positions.values() if p["status"] == "open")
        room = max(s.max_gross_leverage * self.equity_multiple * nav - gross_open, 0.0)
        liq = np.where(ev["avg_dollar_volume_21d"].notna(), s.liquidity_cap_frac * ev["avg_dollar_volume_21d"], np.inf)
        target, status, dropped = cap_notionals(ev["weight"].to_numpy() * base_unit, liq, room, s.cap_mode)
        log(f"  equity: {len(ev)} candidates, gross room ${room:,.0f}, cap status={status}, dropped={dropped}")
        for (_, row), dollars in zip(ev.iterrows(), target):
            if dollars == 0:
                continue
            px = (max(row["market_cap"] / 1e9, 1.0) if self.mock else
                  (data_sources.get_price_stats(row["ticker"]) or {}).get("last_price"))
            if not px:
                continue
            shares = int(dollars / px)
            if shares == 0:
                continue
            side = "buy" if shares > 0 else "sell"
            if not self.dry_run:
                self.broker.submit(row["ticker"], side, abs(shares))
                ledger.log_trade(sleeve="equity", ticker=row["ticker"], action="open", side=side, quantity=abs(shares),
                                 price=px, notional=abs(shares) * px, reason=self.equity.name)
            positions[str(uuid.uuid4())[:8]] = dict(
                ticker=row["ticker"], shares=shares, entry_price=px, entry_date=self.today.isoformat(),
                hold_days=int(row["holding_days"]), strategy=self.equity.name, status="open")
            log(f"  equity OPEN {row['ticker']}: {shares} sh @ {px:.2f} (hold {int(row['holding_days'])}d)")
        return positions

    # ---- 3. options --------------------------------------------------------------------------
    def trade_options(self, events, positions):
        have_today = {p["ticker"] for p in positions.values() if p["entry_date"] == self.today.isoformat()}
        todays = [e for e in events if e["report_date"] == self.today.isoformat() and e["ticker"] not in have_today]
        cohort = self._cohort(events)
        if not todays or len(cohort) < config.MIN_COHORT_SIZE:
            return positions
        ev = features.build_events(pd.DataFrame(cohort))
        ev = ev[(ev["report_date"] == self.today.isoformat()) & ev["ticker"].isin({e["ticker"] for e in todays})]
        sz = self.options.sizing
        nav = ledger.latest_nav()
        budget = sz.max_premium_frac * self.options_multiple * nav
        outstanding = sum(p["contracts"] * p["entry_premium"] * 100 for p in positions.values() if p["status"] == "open")
        f_ref = config.OPTIONS_EXPECTED_CONCURRENT_POSITIONS * config.OPTIONS_MEAN_HALF_KELLY
        for _, e in ev.iterrows():
            expiry = None
            if self.mock:
                spot = 100.0
                strikes = np.array([90., 100., 110., 120.] * 2)
                chain = pd.DataFrame({"strike": strikes, "cp_flag": ["C"] * 4 + ["P"] * 4,
                                      "mid": [11., 5., 2., 0.7, 0.3, 2.5, 7., 14.], "openInterest": 100})
            else:
                spot, expiry, chain = data_sources.get_option_chain_candidates(e["ticker"])
                if spot is None or chain is None or chain.empty:
                    continue
            ret_state, prob = decile_model.get_return_states(int(e["decile"]), sz.hold_horizon)
            chain = chain[chain["mid"] > max(sz.min_premium, 0.01)]
            if chain.empty:
                continue
            best = kelly.best_contract(spot, np.where(chain["cp_flag"] == "C", 1.0, -1.0), chain["strike"].to_numpy(),
                                       chain["mid"].to_numpy(), ret_state, prob)
            if best is None:
                continue
            i, f_star, growth = best
            row = chain.iloc[i]
            half_k = f_star * sz.half_kelly
            premium = min(budget * half_k / f_ref, max(budget - outstanding, 0.0))
            lots = int(premium // (row["mid"] * 100))
            if lots <= 0:
                continue
            fill = broker_options_sim.simulate_open(row["mid"], lots, sz.roundtrip_cost_pct, sz.min_roundtrip_cost_per_share)
            outstanding += lots * fill["fill_price"] * 100
            positions[str(uuid.uuid4())[:8]] = dict(
                ticker=e["ticker"], cp_flag=row["cp_flag"], strike=float(row["strike"]), contracts=lots,
                expiry=expiry.isoformat() if expiry else None,
                entry_premium=fill["fill_price"], entry_spot=float(spot), entry_date=self.today.isoformat(),
                hold_days=sz.hold_horizon, decile=int(e["decile"]), kelly_growth=growth, strategy=self.options.name,
                status="open")
            if not self.dry_run:
                ledger.log_trade(sleeve="options", ticker=e["ticker"], action="open", side="buy", quantity=lots,
                                 price=fill["fill_price"], notional=lots * fill["fill_price"] * 100,
                                 reason=f"{self.options.name} d{int(e['decile'])}")
            log(f"  options OPEN {e['ticker']} {row['cp_flag']}{row['strike']:g}: {lots}x @ {fill['fill_price']:.2f} (growth {growth:.3f})")
        return positions

    # ---- 4. mark to market -------------------------------------------------------------------
    def mark(self, eq_pos, op_pos):
        eq_pnl = op_pnl = outstanding = gross = 0.0
        sz = self.options.sizing
        for pid, p in eq_pos.items():
            held = (self.today - date.fromisoformat(p["entry_date"])).days
            stats = None if self.mock else data_sources.get_price_stats(p["ticker"])
            price = stats["last_price"] if stats else p["entry_price"]
            if p["status"] == "open" and held >= _hold_cal_days(p.get("hold_days", config.DEFAULT_HOLDING_TRADING_DAYS)):
                p.update(status="closed", exit_price=price, exit_date=self.today.isoformat(),
                         realized_pnl=p["shares"] * (price - p["entry_price"]))
                if not self.dry_run:
                    side = "sell" if p["shares"] > 0 else "buy"
                    self.broker.submit(p["ticker"], side, abs(p["shares"]))
                    ledger.log_trade(sleeve="equity", ticker=p["ticker"], action="close", side=side, quantity=abs(p["shares"]),
                                     price=price, notional=abs(p["shares"]) * price, position_id=pid, reason="holding_exit")
                log(f"  equity CLOSE {p['ticker']}: pnl={p['realized_pnl']:,.0f}")
            if p["status"] == "closed":
                eq_pnl += p["realized_pnl"]
            else:
                eq_pnl += p["shares"] * (price - p["entry_price"])
                gross += abs(p["shares"] * price)
        for pid, p in op_pos.items():
            held = (self.today - date.fromisoformat(p["entry_date"])).days
            mid, spot_now = p["entry_premium"], p["entry_spot"]
            if not self.mock:
                s, _, chain = data_sources.get_option_chain_candidates(p["ticker"], expiry=p.get("expiry"))
                spot_now = s or spot_now
                if chain is not None:
                    # The backtest's flaw was carrying a dead option's last live quote forward. A contract that is
                    # absent from its own expiry's chain (no bid / no open interest) is worth ~nothing: mark it 0.
                    m = chain[(chain["strike"] == p["strike"]) & (chain["cp_flag"] == p["cp_flag"])]
                    mid = float(m.iloc[0]["mid"]) if not m.empty else 0.0
                elif p.get("expiry") and date.fromisoformat(p["expiry"]) <= self.today:
                    # expired: settle at intrinsic value on the spot we can still see
                    mid = max(spot_now - p["strike"], 0.0) if p["cp_flag"] == "C" else max(p["strike"] - spot_now, 0.0)
            if p["status"] == "open" and held >= _hold_cal_days(p.get("hold_days", sz.hold_horizon)):
                fill = broker_options_sim.simulate_close(mid, p["contracts"], sz.roundtrip_cost_pct, sz.min_roundtrip_cost_per_share)
                p.update(status="closed", exit_premium=fill["fill_price"], exit_date=self.today.isoformat(),
                         realized_pnl=fill["proceeds"] - p["contracts"] * p["entry_premium"] * 100)
                if not self.dry_run:
                    decile_model.update_with_realized_return(p["decile"], (spot_now - p["entry_spot"]) / p["entry_spot"])
                    ledger.log_trade(sleeve="options", ticker=p["ticker"], action="close", side="sell", quantity=p["contracts"],
                                     price=fill["fill_price"], notional=fill["proceeds"], position_id=pid, reason="holding_exit")
                log(f"  options CLOSE {p['ticker']}: pnl={p['realized_pnl']:,.0f}")
            if p["status"] == "closed":
                op_pnl += p["realized_pnl"]
            else:
                op_pnl += p["contracts"] * 100 * (mid - p["entry_premium"])
                outstanding += p["contracts"] * mid * 100
        nav = config.INITIAL_CAPITAL + eq_pnl + op_pnl
        row = dict(date=self.today.isoformat(), nav=round(nav, 2), equity_pnl=round(eq_pnl, 2), options_pnl=round(op_pnl, 2),
                   equity_gross_exposure=round(gross, 2), options_outstanding_premium=round(outstanding, 2),
                   n_equity_positions=sum(p["status"] == "open" for p in eq_pos.values()),
                   n_options_positions=sum(p["status"] == "open" for p in op_pos.values()))
        if not self.dry_run:
            ledger.append_nav_row(row)
        log(f"  NAV {nav:,.0f}  (equity {eq_pnl:+,.0f}, options {op_pnl:+,.0f})")
        return eq_pos, op_pos

    # ---- orchestration -----------------------------------------------------------------------
    def run(self):
        log(f"=== live run: equity={self.equity.name} x{self.equity_multiple}, options={self.options.name} "
            f"x{self.options_multiple}, broker={self.broker.name}, dry_run={self.dry_run}, mock={self.mock} ===")
        if self.mock:
            universe = [{"ticker": f"MOCK{i}", "sector": list(config.GICS_TO_FF12)[i % 10]} for i in range(self.limit or 60)]
        else:
            universe = data_sources.load_universe()[: self.limit] if self.limit else data_sources.load_universe()
        events = ledger.load_earnings_events()
        log(f"1/4 scanning {len(universe)} tickers")
        new = self.scan(universe, events)
        if not self.dry_run:
            ledger.append_earnings_events(new)
        events = events + new
        eq_pos, op_pos = ledger.load_equity_positions(), ledger.load_options_positions()
        log("2/4 equity")
        eq_pos = self.trade_equities(events, eq_pos)
        log("3/4 options")
        op_pos = self.trade_options(events, op_pos)
        log("4/4 mark to market")
        eq_pos, op_pos = self.mark(eq_pos, op_pos)
        if not self.dry_run:
            for e in events:
                e["processed_for_signal"] = "True"
            ledger.save_equity_positions(eq_pos)
            ledger.save_options_positions(op_pos)
            ledger.rewrite_earnings_events(events)
            ledger.log_run("daily", dict(n_new_events=len(new), equity_open=sum(p["status"] == "open" for p in eq_pos.values()),
                                         options_open=sum(p["status"] == "open" for p in op_pos.values())))
        log("=== done ===")
