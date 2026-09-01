#!/usr/bin/env python3
import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

import optionExpReturn_v08 as er_mod


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parquet-only OptionMetrics backtest using existing optionExpReturn_v08 ER logic"
    )
    parser.add_argument(
        "--opprcd-parquet",
        default="/media/swirl/New Volume/OptionMetrics/parquet/opprcd2005.parquet",
        help="Path to OptionMetrics opprcd parquet",
    )
    parser.add_argument(
        "--secprd-parquet",
        default="/media/swirl/New Volume/OptionMetrics/parquet/secprd2005.parquet",
        help="Path to OptionMetrics secprd parquet",
    )
    parser.add_argument(
        "--secnmd-parquet",
        default="/media/swirl/New Volume/OptionMetrics/parquet/secnmd.parquet",
        help="Path to OptionMetrics secnmd parquet used for ticker -> secid mapping",
    )
    parser.add_argument(
        "--ticker",
        default=None,
        help="Optional underlying ticker filter (ex: AAPL, MSFT, SPY)",
    )
    parser.add_argument(
        "--return-states",
        default=str(Path(__file__).with_name("return_states.csv")),
        help="Path to return_states.csv (same format as existing ER module)",
    )
    parser.add_argument(
        "--cp-flag",
        default="C",
        choices=["C", "P"],
        help="Option type flag to backtest: C (calls) or P (puts)",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Start date inclusive (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="End date inclusive (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--hold-days",
        type=int,
        default=5,
        help="Hold period in trading days between buy and sell",
    )
    parser.add_argument(
        "--max-buy-dates",
        type=int,
        default=None,
        help="Optional cap for number of buy dates processed (for quick runs)",
    )
    parser.add_argument(
        "--output-csv",
        default="backtest_optionmetrics_results.csv",
        help="Output CSV path for per-trade backtest rows",
    )
    return parser.parse_args()


def get_secids_for_ticker(secnmd_path: str, ticker: str) -> set[float]:
    secnmd = pd.read_parquet(secnmd_path, columns=["secid", "ticker"])
    secnmd["ticker"] = secnmd["ticker"].astype(str).str.upper().str.strip()
    secids = set(pd.to_numeric(secnmd[secnmd["ticker"] == ticker.upper()]["secid"], errors="coerce").dropna().tolist())
    return secids


def load_and_prepare(
    opprcd_path: str,
    secprd_path: str,
    secnmd_path: str,
    cp_flag: str,
    start_date: str | None,
    end_date: str | None,
    ticker: str | None,
) -> pd.DataFrame:
    option_cols = [
        "secid",
        "date",
        "optionid",
        "cp_flag",
        "exdate",
        "strike_price",
        "best_bid",
        "best_offer",
        "symbol",
    ]
    options = pd.read_parquet(opprcd_path, columns=option_cols)

    options = options[options["cp_flag"] == cp_flag].copy()

    if ticker:
        secids = get_secids_for_ticker(secnmd_path, ticker)
        if not secids:
            raise ValueError(f"Ticker {ticker} not found in {secnmd_path}")
        options = options[options["secid"].isin(secids)].copy()

    options["date"] = pd.to_datetime(options["date"])
    options["exdate"] = pd.to_datetime(options["exdate"])

    if start_date:
        options = options[options["date"] >= pd.to_datetime(start_date)]
    if end_date:
        options = options[options["date"] <= pd.to_datetime(end_date)]

    # OptionMetrics strike_price is stored in 1/1000 dollars.
    options["Strike Price"] = pd.to_numeric(options["strike_price"], errors="coerce") / 1000.0
    options["Ask Price"] = pd.to_numeric(options["best_offer"], errors="coerce")
    options["Bid Price"] = pd.to_numeric(options["best_bid"], errors="coerce")

    sec_cols = ["secid", "date", "close"]
    stocks = pd.read_parquet(secprd_path, columns=sec_cols)
    stocks["date"] = pd.to_datetime(stocks["date"])
    stocks["Stock Price"] = pd.to_numeric(stocks["close"], errors="coerce")

    merged = options.merge(
        stocks[["secid", "date", "Stock Price"]],
        on=["secid", "date"],
        how="left",
    )

    merged = merged.dropna(subset=["Stock Price", "Strike Price", "Ask Price"])
    merged = merged[(merged["Ask Price"] > 0) & (merged["Strike Price"] > 0)]

    # Use optionid as a stable contract key and Symbol field consumed by the ER loader.
    merged["contract_key"] = merged["optionid"].astype("Int64").astype(str)
    merged["Symbol"] = merged["contract_key"]

    return merged


def compute_best_contract_for_day(day_df: pd.DataFrame, r: list[float], q: list[float]):
    if day_df.empty:
        return None

    er_input = day_df[["Strike Price", "Ask Price", "Stock Price", "Symbol"]].copy()
    options = er_mod.load_options(er_input)
    er_mod.calcER(options, len(r), r, q)
    best = er_mod.calcMaxER(options)

    return best


def run_backtest(df: pd.DataFrame, r: list[float], q: list[float], hold_days: int, max_buy_dates: int | None) -> pd.DataFrame:
    all_dates = np.array(sorted(df["date"].unique()))
    if len(all_dates) <= hold_days:
        return pd.DataFrame()

    if max_buy_dates is not None:
        all_dates = all_dates[: max_buy_dates + hold_days]

    trades: list[dict] = []

    for idx in range(0, len(all_dates) - hold_days):
        buy_date = all_dates[idx]
        sell_date = all_dates[idx + hold_days]

        buy_slice = df[(df["date"] == buy_date) & (df["exdate"] > sell_date)]
        if buy_slice.empty:
            continue

        best_opt = compute_best_contract_for_day(buy_slice, r, q)
        if best_opt is None:
            continue

        chosen = buy_slice[buy_slice["contract_key"] == str(best_opt.tick)]
        if chosen.empty:
            continue

        chosen_row = chosen.iloc[0]

        sell_slice = df[(df["date"] == sell_date) & (df["contract_key"] == chosen_row["contract_key"])]
        if sell_slice.empty:
            continue

        sell_row = sell_slice.iloc[0]

        buy_ask = float(chosen_row["Ask Price"])
        sell_bid = float(sell_row["Bid Price"]) if pd.notna(sell_row["Bid Price"]) else math.nan

        if not math.isfinite(buy_ask) or buy_ask <= 0:
            continue
        if not math.isfinite(sell_bid):
            continue

        realized_ret = (sell_bid - buy_ask) / buy_ask

        trades.append(
            {
                "buy_date": pd.Timestamp(buy_date).date().isoformat(),
                "sell_date": pd.Timestamp(sell_date).date().isoformat(),
                "hold_days": hold_days,
                "secid": float(chosen_row["secid"]),
                "optionid": chosen_row["contract_key"],
                "symbol": chosen_row.get("symbol"),
                "cp_flag": chosen_row["cp_flag"],
                "expiration": pd.Timestamp(chosen_row["exdate"]).date().isoformat(),
                "stock_price_buy": float(chosen_row["Stock Price"]),
                "strike_price": float(chosen_row["Strike Price"]),
                "buy_ask": buy_ask,
                "sell_bid": sell_bid,
                "expected_return": float(best_opt.ER),
                "realized_return": realized_ret,
            }
        )

    return pd.DataFrame(trades)


def summarize(results: pd.DataFrame) -> None:
    if results.empty:
        print("No matched trades found for the selected filters.")
        return

    realized = pd.to_numeric(results["realized_return"], errors="coerce")
    expected = pd.to_numeric(results["expected_return"], errors="coerce")

    print("\nBacktest summary")
    print(f"Trades: {len(results)}")
    print(f"Average realized return: {realized.mean():.6f}")
    print(f"Median realized return: {realized.median():.6f}")
    print(f"Hit rate (realized > 0): {(realized > 0).mean():.4f}")

    valid = results[["expected_return", "realized_return"]].dropna()
    if len(valid) > 1:
        corr = valid["expected_return"].corr(valid["realized_return"])
        print(f"Corr(expected, realized): {corr:.6f}")


def main() -> None:
    args = parse_args()

    print("Loading return states using existing logic...")
    r, q = er_mod.load_return_states(args.return_states)

    print("Loading and joining parquet datasets...")
    df = load_and_prepare(
        opprcd_path=args.opprcd_parquet,
        secprd_path=args.secprd_parquet,
        secnmd_path=args.secnmd_parquet,
        cp_flag=args.cp_flag,
        start_date=args.start_date,
        end_date=args.end_date,
        ticker=args.ticker,
    )

    print("Running backtest...")
    results = run_backtest(
        df=df,
        r=r,
        q=q,
        hold_days=args.hold_days,
        max_buy_dates=args.max_buy_dates,
    )

    out_path = Path(args.output_csv)
    results.to_csv(out_path, index=False)
    print(f"Saved results: {out_path.resolve()}")

    summarize(results)


if __name__ == "__main__":
    main()
