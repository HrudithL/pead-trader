# OptionsReturnOCM_v08_1_gpu.py (GPU-Accelerated OptionMetrics version)
#
# This script combines:
#   - The OptionMetrics / parquet data-loading + backtest-summary logic from
#     OptionsReturnOCM_v08_1.py
#   - The vectorized GPU pipeline from OptionsReturn_v0.81_gpu.py
#
# Workflow:
#   1. Load data with pandas (CSV or OptionMetrics parquet) for robust parsing.
#   2. Upload the cleaned DataFrame to the GPU (cudf) when available.
#   3. Perform all joins, filtering, and return calculations in parallel on
#      the GPU using a vectorized buy/sell merge.
#   4. Bring only the small result rows back to the CPU for reporting.
#   5. Generate graphs and the PDF report on the CPU.
#
# If RAPIDS (cudf, cupy) is not available, the script transparently falls
# back to pandas / numpy on the CPU.

import matplotlib
matplotlib.use('Agg')
import os
import sys
import struct
import shutil
import datetime as dt
import time
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
from fpdf import FPDF
import requests

import optionExpReturn_v08
import backtest_optionmetrics_parquet

# --- GPU / CPU library configuration ---
import numpy as np_cpu  # always-available CPU numpy

try:
    import cudf
    import cupy as cp
    np = cp                        # so cp.* math dispatches on cupy arrays
    GPU_ENABLED = True
    print("cuDF and CuPy successfully imported. GPU acceleration is ENABLED.")
except ImportError:
    import numpy as np             # noqa: F401  (alias for parity with GPU path)
    cudf = pd                      # use pandas as the "compute lib"
    GPU_ENABLED = False
    print("cuDF/CuPy not found. Falling back to pandas/numpy. GPU acceleration is DISABLED.")


VERSION = "08.1_OCM_GPU"
OUTPUT_DIR = "Options_Return_Output/"
SUPER_PARENT_DIR = "Stock Market Research"
OPTION_TYPE = "call and put"

_PARQUET_CANDIDATES = [
    "/media/swirl/New Volume/OptionMetrics/parquet",
    r"D:\OptionMetrics\parquet",
    "/home/swirl/Option Metrics(CSV+Parquet+SAS)/parquet",
]


def _resolve_parquet_base_dir() -> str:
    for path in _PARQUET_CANDIDATES:
        if os.path.isdir(path) and os.path.exists(os.path.join(path, "secnmd.parquet")):
            return path
    return _PARQUET_CANDIDATES[0]


PARQUET_BASE_DIR = _resolve_parquet_base_dir()


try:
    PATH = Path(__file__)
except NameError:
    PATH = Path("OptionsReturnOCM_v08_1_gpu.ipynb")

CURRENT_WORKING_DIR = PATH.parent.absolute()
os.chdir(CURRENT_WORKING_DIR)
end = str(CURRENT_WORKING_DIR).find(SUPER_PARENT_DIR)
splitter = os.sep
STOCK_MARKET_RESEARCH_DIR = Path(str(CURRENT_WORKING_DIR)[0:end + len(SUPER_PARENT_DIR)])

# Keep Libraries on path for fetch_stock_price (still used as a fallback)
sys.path.append(str(STOCK_MARKET_RESEARCH_DIR) + splitter + 'Libraries' + splitter)
from finance import total_return  # noqa: E402  (local import after sys.path tweak)


class PDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, 'Page %s' % self.page_no(), 0, 0, 'C')


# ─── CSV loading (CPU clean -> optional GPU upload) ─────────────────────────

def combineInput(df_1: pd.DataFrame, file_path2: str) -> pd.DataFrame:
    df_2 = pd.read_csv(file_path2)
    if df_1.shape[1] != df_2.shape[1]:
        raise ValueError("Both DataFrames must have the same number of columns.")
    return pd.concat([df_1, df_2], ignore_index=True)


def clean_and_convert_pandas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.astype(str)
    for col in df.columns:
        if df[col].dtype == 'object':
            df[col] = df[col].str.strip()
    try:
        df['Timestamp'] = pd.to_datetime(df['Timestamp']).dt.strftime("%Y-%m-%d")
        df['Stock Price'] = pd.to_numeric(df['Stock Price'], errors='coerce')
        df['Symbol'] = df['Symbol'].astype(str)
        df['Option Type'] = df['Option Type'].astype(str)
        df['Strike Price'] = pd.to_numeric(df['Strike Price'], errors='coerce')
        df['Ask Price'] = pd.to_numeric(df['Ask Price'], errors='coerce')
        df['Bid Price'] = pd.to_numeric(df['Bid Price'], errors='coerce')
        df['Expiration Date'] = pd.to_datetime(df['Expiration Date']).dt.strftime("%Y-%m-%d")
    except Exception as e:
        print(df.columns)
        raise ValueError(f"Error converting column types: {e}")
    df.dropna(subset=['Strike Price', 'Ask Price', 'Bid Price', 'Timestamp', 'Expiration Date'], inplace=True)
    return df


def loadOptionsData_vec(allowed_dates: list = None, exp: str = "", stock: str = "",
                        isHist: bool = False, gpu_enabled: bool = True):
    """
    Load CSV options data into pandas, clean, optionally filter, and (if GPU
    is enabled) upload to cudf. Returns (pandas_df, cudf_df_or_None).
    """
    allowed_dates = allowed_dates or []
    currDf = pd.DataFrame()
    targFiles: list[str] = []

    if stock:
        if not isHist:
            targFiles += [f"input_files/{stock}_1.csv", f"input_files/{stock}_2.csv"]
        else:
            targFiles += [f'../../Options_History/{stock}_Options_Storage/{stock} Options History.csv']
    else:
        while True:
            csv_file = input("Please enter the path to the CSV file (default: finished): ")
            if csv_file == '':
                break
            if not os.path.exists(csv_file):
                print(f"File \"{csv_file}\" does not exist. Please try again.")
                continue
            currDf = pd.read_csv(csv_file) if currDf.empty else combineInput(currDf, csv_file)

    if stock:
        for csv_file in targFiles:
            if not os.path.exists(csv_file):
                print(f"Warning: File \"{csv_file}\" does not exist. Skipping.")
                continue
            currDf = pd.read_csv(csv_file) if currDf.empty else combineInput(currDf, csv_file)

    if currDf.empty:
        print("No data loaded.")
        return pd.DataFrame(), None

    try:
        currDf = clean_and_convert_pandas(currDf)
        if allowed_dates and allowed_dates[0] != "":
            currDf = currDf[currDf['Timestamp'].isin(allowed_dates)]
        if exp:
            currDf = currDf[currDf['Expiration Date'] == exp]
        currDf.reset_index(drop=True, inplace=True)

        if currDf.empty:
            print("Data is empty after filtering.")
            return pd.DataFrame(), None

        cudf_df = None
        if gpu_enabled:
            print(f"Uploading {len(currDf)} rows to GPU...")
            t0 = time.time()
            cudf_df = cudf.from_pandas(currDf)
            print(f"GPU upload complete in {time.time() - t0:.2f}s")
        print(f"Successfully loaded {len(currDf)} option rows from {targFiles}.")
        return currDf, cudf_df
    except Exception as e:
        print(f"An error occurred while loading the data: {e}")
        return pd.DataFrame(), None


# ─── OptionMetrics parquet loading (CPU -> optional GPU upload) ─────────────

def _parquet_secids(ticker: str) -> set:
    secnmd_path = os.path.join(PARQUET_BASE_DIR, "secnmd.parquet")
    if not os.path.exists(secnmd_path):
        print(f"Missing {secnmd_path}; parquet ticker lookup unavailable.")
        return set()
    return backtest_optionmetrics_parquet.get_secids_for_ticker(secnmd_path, ticker)


def _parquet_discovery_df(ticker: str, ref_date: str, secids: set) -> pd.DataFrame:
    """Lightweight parquet read (date+exdate only) to discover trading dates."""
    if not secids:
        return pd.DataFrame()
    year = pd.to_datetime(ref_date).year
    opprcd_path = os.path.join(PARQUET_BASE_DIR, f"opprcd{year}.parquet")
    if not os.path.exists(opprcd_path):
        print(f"Missing parquet file: {opprcd_path}")
        return pd.DataFrame()
    opts = pd.read_parquet(opprcd_path, columns=["secid", "date", "exdate"])
    opts = opts[opts["secid"].isin(secids)].copy()
    opts["date"] = pd.to_datetime(opts["date"])
    opts["exdate"] = pd.to_datetime(opts["exdate"])
    return pd.DataFrame({
        "Timestamp": opts["date"].dt.strftime("%Y-%m-%d"),
        "Expiration Date": opts["exdate"].dt.strftime("%Y-%m-%d"),
    }).drop_duplicates().reset_index(drop=True)


def loadOptionsDataFromParquet_vec(ticker: str, allowed_dates: list = None, exp: str = "",
                                   secids: set = None, gpu_enabled: bool = True):
    """
    Load options for the requested dates from OptionMetrics parquet.
    Returns (pandas_df, cudf_df_or_None) — schema matches loadOptionsData_vec.
    """
    allowed_dates = allowed_dates or []
    if not allowed_dates or allowed_dates[0] == "":
        return pd.DataFrame(), None
    if secids is None:
        secids = _parquet_secids(ticker)
    if not secids:
        print(f"Ticker {ticker} not found in parquet secnmd.")
        return pd.DataFrame(), None

    buy_date = allowed_dates[0]
    sell_date = allowed_dates[1] if len(allowed_dates) > 1 and allowed_dates[1] != "" else buy_date
    buy_year = pd.to_datetime(buy_date).year
    sell_year = pd.to_datetime(sell_date).year

    all_opts: list[pd.DataFrame] = []
    all_stocks: list[pd.DataFrame] = []

    for yr in range(buy_year, sell_year + 1):
        opprcd_path = os.path.join(PARQUET_BASE_DIR, f"opprcd{yr}.parquet")
        secprd_path = os.path.join(PARQUET_BASE_DIR, f"secprd{yr}.parquet")
        if not os.path.exists(opprcd_path):
            print(f"Missing parquet file: {opprcd_path}")
            continue

        opt_cols = ["secid", "date", "optionid", "cp_flag", "exdate", "strike_price", "best_bid", "best_offer"]
        opts = pd.read_parquet(opprcd_path, columns=opt_cols)
        opts = opts[opts["secid"].isin(secids)].copy()
        opts["date"] = pd.to_datetime(opts["date"])
        opts["exdate"] = pd.to_datetime(opts["exdate"])
        date_filter = pd.to_datetime(allowed_dates)
        opts = opts[opts["date"].isin(date_filter)]
        if exp:
            opts = opts[opts["exdate"].dt.strftime("%Y-%m-%d") == exp]
        all_opts.append(opts)

        if os.path.exists(secprd_path):
            stk = pd.read_parquet(secprd_path, columns=["secid", "date", "close"])
            stk = stk[stk["secid"].isin(secids)].copy()
            stk["date"] = pd.to_datetime(stk["date"])
            stk = stk[stk["date"].isin(date_filter)]
            all_stocks.append(stk)

    if not all_opts:
        return pd.DataFrame(), None

    options_df = pd.concat(all_opts, ignore_index=True)
    if all_stocks:
        stocks_df = pd.concat(all_stocks, ignore_index=True)
        merged = options_df.merge(stocks_df[["secid", "date", "close"]], on=["secid", "date"], how="left")
    else:
        merged = options_df.copy()
        merged["close"] = float("nan")

    merged["strike_raw_int"] = pd.to_numeric(merged["strike_price"], errors="coerce").fillna(0).astype(int)
    merged["Symbol"] = (
        ticker
        + merged["exdate"].dt.strftime("%y%m%d")
        + merged["cp_flag"].astype(str)
        + merged["strike_raw_int"].apply(lambda x: f"{x:08d}")
    )

    df = pd.DataFrame({
        "Timestamp":       merged["date"].dt.strftime("%Y-%m-%d"),
        "Stock Price":     pd.to_numeric(merged["close"], errors="coerce"),
        "Symbol":          merged["Symbol"],
        "Option Type":     merged["cp_flag"].map({"C": "call", "P": "put"}).fillna("call"),
        "Strike Price":    pd.to_numeric(merged["strike_price"], errors="coerce") / 1000.0,
        "Ask Price":       pd.to_numeric(merged["best_offer"], errors="coerce"),
        "Bid Price":       pd.to_numeric(merged["best_bid"],   errors="coerce"),
        "Expiration Date": merged["exdate"].dt.strftime("%Y-%m-%d"),
    })
    df = df.dropna(subset=["Stock Price", "Ask Price", "Bid Price", "Strike Price"])
    df = df[(df["Ask Price"] > 0) & (df["Bid Price"] >= 0) & (df["Strike Price"] > 0)]
    df = df.reset_index(drop=True)

    print(f"Parquet: loaded {len(df)} rows for {ticker} ({', '.join(allowed_dates)}).")

    cudf_df = None
    if gpu_enabled and not df.empty:
        t0 = time.time()
        cudf_df = cudf.from_pandas(df)
        print(f"Parquet GPU upload complete ({len(df)} rows) in {time.time() - t0:.2f}s")

    return df, cudf_df


# ─── Vectorized GPU/CPU return calculation ──────────────────────────────────

def _perform_vectorized_join_and_return_calc(df, buy_date, sell_date, oType, lib):
    """Filter buy and sell rows, merge on contract, compute return per row."""
    buy_df = df[(df['Timestamp'] == buy_date) & (df['Option Type'] == oType)]
    sell_df = df[(df['Timestamp'] == sell_date) & (df['Option Type'] == oType)]

    if buy_df.empty or sell_df.empty:
        return lib.DataFrame()

    joined_df = lib.merge(
        buy_df, sell_df,
        on=['Strike Price', 'Expiration Date', 'Option Type'],
        suffixes=('_buy', '_sell'),
    )
    if joined_df.empty:
        return lib.DataFrame()

    joined_df['Return'] = (
        (joined_df['Bid Price_sell'] - joined_df['Ask Price_buy'])
        / joined_df['Ask Price_buy'] * 100
    )
    return joined_df.dropna(subset=['Return'])


def max_return_vec(df, buy_date, sell_date, oType, lib):
    """Best-return row (as a pandas.Series) and the return value as a float."""
    joined_df = _perform_vectorized_join_and_return_calc(df, buy_date, sell_date, oType, lib)
    if joined_df.empty:
        return None, float('-inf')

    sorted_df = joined_df.sort_values(by='Return', ascending=False)
    best_trade_df = sorted_df.head(1)
    best_pd = best_trade_df.to_pandas() if lib is cudf else best_trade_df
    best_row = best_pd.iloc[0]
    return best_row, float(best_row['Return'].item() if hasattr(best_row['Return'], 'item') else best_row['Return'])


def generateReturnsByStrike_vec(df, buy_date, sell_date, oType, lib):
    """Returns a pandas DataFrame of (Strike Price, Return) for graphing."""
    joined_df = _perform_vectorized_join_and_return_calc(df, buy_date, sell_date, oType, lib)
    if joined_df.empty:
        return pd.DataFrame(columns=["Strike Price", "Return"])

    result_df = joined_df[['Strike Price', 'Return']]
    if lib is cudf:
        result_df = result_df.to_pandas()
    return result_df


# ─── Parquet backtest summary (unchanged behavior, kept for parity) ─────────

def run_parquet_backtest_summary(ticker: str, buy_date: str, sell_date: str, option_type: str) -> dict:
    year = pd.to_datetime(buy_date).year
    opprcd_path = f"{PARQUET_BASE_DIR}/opprcd{year}.parquet"
    secprd_path = f"{PARQUET_BASE_DIR}/secprd{year}.parquet"
    secnmd_path = f"{PARQUET_BASE_DIR}/secnmd.parquet"

    if not (os.path.exists(opprcd_path) and os.path.exists(secprd_path) and os.path.exists(secnmd_path)):
        missing = [p for p in [opprcd_path, secprd_path, secnmd_path] if not os.path.exists(p)]
        return {"status": "missing_files", "missing": missing}

    hold_days = max(1, int((pd.to_datetime(sell_date) - pd.to_datetime(buy_date)).days))
    r, q = optionExpReturn_v08.load_return_states(str(Path(__file__).with_name("return_states.csv")))

    if option_type == "call":
        cp_flags = ["C"]
    elif option_type == "put":
        cp_flags = ["P"]
    else:
        cp_flags = ["C", "P"]

    by_flag, total_trades = {}, 0
    for cp_flag in cp_flags:
        prepared = backtest_optionmetrics_parquet.load_and_prepare(
            opprcd_path=opprcd_path, secprd_path=secprd_path, secnmd_path=secnmd_path,
            cp_flag=cp_flag, start_date=f"{year}-01-01", end_date=f"{year}-12-31", ticker=ticker,
        )
        results = backtest_optionmetrics_parquet.run_backtest(
            df=prepared, r=r, q=q, hold_days=hold_days, max_buy_dates=None,
        )
        if results.empty:
            by_flag[cp_flag] = {"trades": 0, "mean": None, "median": None, "hit_rate": None,
                                "corr": None, "output_csv": None}
            continue
        out_csv = Path(OUTPUT_DIR) / f"{ticker}_parquet_backtest_{year}_{cp_flag}.csv"
        results.to_csv(out_csv, index=False)
        realized = pd.to_numeric(results["realized_return"], errors="coerce")
        valid = results[["expected_return", "realized_return"]].dropna()
        corr = float(valid["expected_return"].corr(valid["realized_return"])) if len(valid) > 1 else None
        by_flag[cp_flag] = {
            "trades": int(len(results)),
            "mean": float(realized.mean()), "median": float(realized.median()),
            "hit_rate": float((realized > 0).mean()), "corr": corr,
            "output_csv": str(out_csv),
        }
        total_trades += int(len(results))

    return {"status": "ok", "year": year, "hold_days": hold_days,
            "by_flag": by_flag, "total_trades": total_trades, "ticker": ticker}


# ─── Reporting helpers (CPU-only) ───────────────────────────────────────────

def getDateInput(ask: str, default: str = "") -> str:
    while True:
        dateStr = input(f"{ask} (format: yyyy-mm-dd) : \n")
        if dateStr == "":
            return default
        formatCheck = dateStr.split("-")
        if (len(dateStr) != 10 or len(formatCheck) != 3
                or len(formatCheck[0]) != 4 or len(formatCheck[1]) != 2 or len(formatCheck[2]) != 2):
            print("invalid date format, please try again")
            continue
        return dateStr


def _format_mmddyy(date_like) -> str:
    d = pd.to_datetime(date_like)
    return f"{d.month}/{d.day}/{d.strftime('%y')}"


def graph_return_strike(in_stock: str, buy: str, sell: str, expire: str, oType: str,
                        df: pd.DataFrame, figs: list):
    if df.empty:
        print(f"Strike {oType} DF is empty for expiration date {expire}. Skipping graph.")
        return

    df = df.drop_duplicates(subset=["Strike Price"]).sort_values(by="Strike Price").reset_index(drop=True)
    strike_x = list(df["Strike Price"])
    strike_y = list(df["Return"])

    fig, _ = plt.subplots(figsize=(9, 5.5))
    plt.plot(strike_x, strike_y, marker='o')
    try:
        max_y_value = max(strike_y)
        max_x_value = strike_x[strike_y.index(max_y_value)]
        fb = _format_mmddyy(buy)
        fs = _format_mmddyy(sell)
        fe = _format_mmddyy(expire)
        plt.suptitle(f"{in_stock} {oType} Buy {fb} | Sell {fs} | Expiration {fe}")
        plt.title(f'Max Return: {max_y_value:.0f}% at Strike Price: ${max_x_value}',
                  fontsize=12, color='red', weight='bold')
        plt.xlabel('Strike Price ($)')
        plt.ylabel('Return (%)')
        plt.grid(True)
        plt.tight_layout(rect=[0, 0, 1, 0.93])
        figs.append(fig)
        plt.savefig(OUTPUT_DIR + f"Return vs Strike Plot for {in_stock} {oType} Option with expiration date {expire}.pdf",
                    format="pdf", bbox_inches="tight")
        print(f"Strike {oType} Graph successfully created for expiration {expire}")
    except Exception as e:
        print(f"Error generating Strike {oType} graph for {expire}: {e}")
    plt.close()


def graph_histogram(returns_data: list, oType: str, ticker: str, figs: list):
    if not returns_data:
        return
    clean = [x for x in returns_data if x is not None and x > -99999]
    if not clean:
        return
    fig, _ = plt.subplots(figsize=(9, 5.5))
    try:
        plt.hist(clean, bins='fd', color='#1f77b4', alpha=0.7, rwidth=0.6, edgecolor='black')
    except Exception:
        plt.hist(clean, bins='auto', color='#1f77b4', alpha=0.7, rwidth=0.6, edgecolor='black')
    avg = sum(clean) / len(clean)
    median = sorted(clean)[len(clean) // 2]
    plt.title(f'Distribution of Optimal {oType} Returns for {ticker}\n'
              f'(Avg: {avg:.1f}%, Median: {median:.1f}%, Max: {max(clean):.1f}%)',
              fontsize=10, weight='bold')
    plt.xlabel('Return Percentage (%)')
    plt.ylabel('Frequency (Count of Date Combinations)')
    plt.grid(axis='y', alpha=0.5)
    plt.axvline(avg, color='red', linestyle='dashed', linewidth=1, label=f'Avg: {avg:.1f}%')
    plt.legend()
    plt.tight_layout()
    figs.append(fig)
    try:
        plt.savefig(OUTPUT_DIR + f"Histogram_Returns_{ticker}_{oType}.pdf", format="pdf", bbox_inches="tight")
    except Exception as e:
        print(f"Error saving histogram: {e}")
    plt.close()


def create_graph(x, y, plot_label, fig, ax):
    if len(x) != len(y):
        raise ValueError("x and y must be of the same length")
    ax.plot(x, y, label=plot_label, marker="")
    ax.set_xlim(left=-1)
    max_ticks = 10
    step = max(1, len(x) // max_ticks)
    if hasattr(x, 'iloc'):
        xticks = list(x.iloc[::step])
        last = x.iloc[-1]
    else:
        xticks = list(x[::step])
        last = x[-1]
    if last not in xticks:
        xticks.append(last)
    ax.set_xticks(xticks)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=10, prune='both'))
    fig.autofmt_xdate(rotation=30)
    ax.tick_params(axis="x", rotation=30, labelsize=6)
    ax.tick_params(axis="y", labelsize=8)


def plot_stock_graph(fig_storage: list, ticker: str, buy_date: str, sell_date: str,
                     h: str = "2024-08-09"):
    """Tradier API stock-price graph; falls back path mirrors the GPU CSV version."""
    date_list_unprocessed: list[str] = []
    price_list: list[float] = []
    date_list: list[str] = []

    local_file_path = f'../../Options_History/{ticker}_Options_Storage/{ticker} Options History.csv'
    data_loaded_locally = False
    if os.path.exists(local_file_path):
        try:
            print(f"Loading stock price data from local file: {local_file_path}")
            stock_df = pd.read_csv(local_file_path)
            if 'Timestamp' in stock_df.columns and 'Stock Price' in stock_df.columns:
                stock_df['Timestamp'] = pd.to_datetime(stock_df['Timestamp']).dt.strftime("%Y-%m-%d")
                stock_df = stock_df[['Timestamp', 'Stock Price']].drop_duplicates(subset=['Timestamp']).sort_values('Timestamp')
                date_list_unprocessed = stock_df['Timestamp'].tolist()
                price_list = stock_df['Stock Price'].tolist()
                date_list = [pd.to_datetime(d).strftime("%m/%d/%y").lstrip('0') for d in date_list_unprocessed]
                data_loaded_locally = True
                print(f"Loaded {len(price_list)} stock price records from local file.")
        except Exception as e:
            print(f"Error reading local stock data for {ticker}: {e}. Falling back to API.")

    if not data_loaded_locally:
        try:
            response = requests.get(
                'https://api.tradier.com/v1/markets/history',
                params={'symbol': ticker, 'interval': 'daily', 'start': h,
                        'end': f'{dt.datetime.now().date()}', 'session_filter': 'all'},
                headers={'Authorization': 'Bearer REDACTED_SEE_ROTATE_THIS_TOKEN', 'Accept': 'application/json'},  # noqa: original hardcoded a live token -- redacted when copying into this repo, see reference/options_content/README.md
            )
            j = response.json()
            if not j.get("history") or not j["history"].get("day"):
                print(f"Could not fetch stock price history for {ticker}.")
                return None, None
            for day in j["history"]["day"]:
                date_list_unprocessed.append(day["date"])
                date_list.append(pd.to_datetime(day["date"]).strftime("%m/%d/%y").lstrip('0'))
                price_list.append(day["close"])
        except Exception as e:
            print(f"Error processing stock price data for {ticker}: {e}")
            return None, None

    try:
        buy_stock_price = price_list[date_list_unprocessed.index(buy_date)]
        sell_stock_price = price_list[date_list_unprocessed.index(sell_date)]
    except ValueError as e:
        print(f"Skipping combination: {buy_date} to {sell_date} - Date not found in stock data: {e}")
        return None, None

    fig, ax = plt.subplots(figsize=(9, 5.5))
    create_graph(pd.Series(date_list), pd.Series(price_list), "Stock Price", fig, ax)
    plt.title(f"{ticker} Stock Price", fontsize=8)
    plt.xlabel("Date")
    plt.ylabel("Stock Price ($)")
    fig_storage.append(fig)
    plt.close()
    return buy_stock_price, sell_stock_price


def plot_stock_graph_parquet(fig_storage: list, ticker: str, buy_date: str, sell_date: str, secids: set):
    """Stock price graph using parquet secprd; returns (buy_price, sell_price)."""
    context_start = pd.to_datetime(buy_date) - pd.DateOffset(months=3)
    sell_year = pd.to_datetime(sell_date).year

    stock_frames: list[pd.DataFrame] = []
    for yr in range(context_start.year, sell_year + 1):
        path = os.path.join(PARQUET_BASE_DIR, f"secprd{yr}.parquet")
        if not os.path.exists(path):
            continue
        stk = pd.read_parquet(path, columns=["secid", "date", "close"])
        stk = stk[stk["secid"].isin(secids)].copy()
        stk["date"] = pd.to_datetime(stk["date"])
        stk = stk[stk["date"] >= context_start]
        stock_frames.append(stk)

    if not stock_frames:
        return plot_stock_graph(fig_storage, ticker, buy_date, sell_date,
                                h=context_start.strftime("%Y-%m-%d"))

    stocks = pd.concat(stock_frames).sort_values("date").reset_index(drop=True)
    stocks = stocks[stocks["date"] <= pd.to_datetime(sell_date)]

    date_list_raw = stocks["date"].dt.strftime("%Y-%m-%d").tolist()
    date_list = [d.lstrip("0") for d in stocks["date"].dt.strftime("%m/%d/%y").tolist()]
    price_list = stocks["close"].astype(float).tolist()

    try:
        buy_stock_price = price_list[date_list_raw.index(buy_date)]
        sell_stock_price = price_list[date_list_raw.index(sell_date)]
    except ValueError as e:
        print(f"Stock price lookup failed in parquet graph: {e}")
        return None, None

    fig, ax = plt.subplots(figsize=(9, 5.5))
    create_graph(pd.Series(date_list), pd.Series(price_list), "Stock Price", fig, ax)
    plt.title(f"{ticker} Stock Price", fontsize=8)
    plt.xlabel("Date")
    plt.ylabel("Stock Price ($)")
    fig_storage.append(fig)
    plt.close()
    return buy_stock_price, sell_stock_price


def find_closest_expiration_to_sell_date(all_expire_dates, sell_date):
    if not all_expire_dates:
        return None
    sell_dt = pd.to_datetime(sell_date).date()
    closest, min_diff = None, None
    for e in all_expire_dates:
        e_dt = pd.to_datetime(e).date()
        if e_dt >= sell_dt:
            diff = abs((e_dt - sell_dt).days)
            if min_diff is None or diff < min_diff:
                min_diff, closest = diff, e
    if closest is None:
        for e in all_expire_dates:
            e_dt = pd.to_datetime(e).date()
            diff = abs((e_dt - sell_dt).days)
            if min_diff is None or diff < min_diff:
                min_diff, closest = diff, e
    return closest


def generate_date_combinations(start_date, end_date, available_dates):
    start_dt = pd.to_datetime(start_date).date()
    end_dt = pd.to_datetime(end_date).date()
    valid = sorted([d for d in available_dates if start_dt <= pd.to_datetime(d).date() <= end_dt])
    return [(valid[i], valid[j]) for i in range(len(valid)) for j in range(i + 1, len(valid))]


# ─── Main ───────────────────────────────────────────────────────────────────

def main(defaults: bool = False, in_stock: str = "", isHist: bool = True, prefix: str = "",
         buy: str = "", sell: str = "", expire: str = "", output: bool = True,
         pdf=None, Otype: str = "", isParquet: bool = False):

    start_time = time.time()
    print(f"Starting Options Return Analysis (GPU: {GPU_ENABLED}) at "
          f"{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 60)

    if not defaults:
        in_stock = input("What ticker would you like to perform calulations for? ").upper()
        prefix = in_stock + '_'
        auto_inp = input("Would you like to use automatic data loading? (y/n) : ").strip().lower()
        auto = auto_inp in ['y', 'yes']
        if auto:
            b = input("Data source — local csv (l), history csv (h), or parquet (p)? : ").strip().lower()
            isHist = b in ['h', 'history']
            isParquet = b in ['p', 'parquet']
    else:
        auto = True

    in_option_type = Otype if defaults else input("Call or Put option (C or P) (Default is both): ")
    if in_option_type.lower() in ["c", "call"]:
        OPTION_TYPE = "call"
    elif in_option_type.lower() in ["p", "put"]:
        OPTION_TYPE = "put"
    else:
        OPTION_TYPE = "call and put"

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_pdf_path = prefix + "Options_Return_Report_v" + VERSION + "_" + timestamp + ".pdf"

    try:
        os.makedirs(OUTPUT_DIR)
    except FileExistsError:
        pass

    if pdf is None:
        pdf = PDF()

    use_date_range = False
    start_date = end_date = ""
    if not defaults:
        rng_in = input("Do you want to test all buy/sell date combinations within a date range? (y/n) [default: n]: ").strip().lower()
        use_date_range = rng_in in ['y', 'yes']
        if use_date_range:
            start_date = getDateInput("Please enter the start date for the range")
            end_date = getDateInput("Please enter the end date for the range")
            expiration_date = getDateInput("Please enter the expiration date (default to closest to end date)")
            buy_date = sell_date = ""
        else:
            buy_date = getDateInput("Please enter the buy date (default to earliest)")
            sell_date = getDateInput("Please enter the sell date (default to latest)")
            expiration_date = getDateInput("Please enter the expiration date (default to closest to sell date)")
    else:
        buy_date, sell_date, expiration_date = buy, sell, expire

    parquet_secids: set = set()
    if isParquet:
        parquet_secids = _parquet_secids(in_stock)
        if not parquet_secids:
            secnmd_path = os.path.join(PARQUET_BASE_DIR, "secnmd.parquet")
            if os.path.exists(secnmd_path):
                print(f"Warning: {in_stock} not found in parquet secnmd. Falling back to CSV.")
            else:
                print(f"Warning: parquet secnmd missing at {secnmd_path}. Falling back to CSV.")
            isParquet = False

    # ── Initial load (discovery) ────────────────────────────────────────────
    print("Loading initial CSV/parquet data to determine available dates...")
    if isParquet:
        ref = buy_date if buy_date else (start_date if use_date_range else f"{dt.datetime.now().year}-01-01")
        df_temp = _parquet_discovery_df(in_stock, ref, parquet_secids)
        gpu_df_temp = None
    elif auto:
        df_temp, gpu_df_temp = loadOptionsData_vec([], "", in_stock, isHist, gpu_enabled=GPU_ENABLED)
    else:
        df_temp, gpu_df_temp = loadOptionsData_vec([], "", "", False, gpu_enabled=GPU_ENABLED)

    if df_temp.empty:
        print("No data loaded from the source. Exiting.")
        return

    available_dates = sorted(pd.Timestamp(d).strftime('%Y-%m-%d') for d in df_temp['Timestamp'].unique())
    print(f"Data loaded. Date range: {min(available_dates)} to {max(available_dates)}")
    print(f"Total unique trading dates: {len(available_dates)}")

    # ── Default / clamp dates to available range ────────────────────────────
    if buy_date == "":
        buy_date = start_date if use_date_range else min(available_dates)
    if sell_date == "":
        sell_date = end_date if use_date_range else max(available_dates)

    available_dates_dt = pd.to_datetime(available_dates)
    if buy_date not in available_dates:
        idx = np_cpu.abs((available_dates_dt - pd.to_datetime(buy_date)).days).argmin()
        original = buy_date
        buy_date = available_dates[idx]
        print(f"Requested buy date {original} not available. Using closest date: {buy_date}")

    if sell_date not in available_dates or pd.to_datetime(sell_date) <= pd.to_datetime(buy_date):
        buy_dt = pd.to_datetime(buy_date)
        after = available_dates_dt[available_dates_dt > buy_dt]
        if len(after) > 0:
            idx = np_cpu.abs((after - pd.to_datetime(sell_date)).days).argmin()
            sell_date = pd.Timestamp(after[idx]).strftime('%Y-%m-%d')
            print(f"Adjusted sell date to {sell_date} (first available after {buy_date}).")
        else:
            sell_date = available_dates[-1]
            print(f"Warning: no available dates after buy date {buy_date}. Using {sell_date}.")

    print(f"Looking for data between buy date: {buy_date} and sell date: {sell_date}")

    available_expires = []
    for e in df_temp['Expiration Date'].unique():
        sub = df_temp[df_temp['Expiration Date'] == e]
        if buy_date in sub['Timestamp'].values and sell_date in sub['Timestamp'].values:
            available_expires.append(e)
    available_expires.sort()
    print(f"Found {len(available_expires)} expiration dates with data for both buy and sell dates")

    if expiration_date == "":
        if available_expires:
            closest = find_closest_expiration_to_sell_date(available_expires, sell_date)
            all_expire_dates = [closest] if closest else available_expires
            print(f"Default expiration date set to closest to sell date ({sell_date}): "
                  f"{closest if closest else 'all available'}")
        else:
            print("No expiration dates found with data for both buy and sell dates")
            return
    else:
        all_expire_dates = [expiration_date]

    if buy_date == sell_date:
        raise ValueError("Buy date equals sell date — likely disjoint expirations between dates.")
    if not all_expire_dates:
        print("No valid expiration date found — exiting.")
        return

    # ── Date combinations ───────────────────────────────────────────────────
    if use_date_range:
        date_combinations = generate_date_combinations(start_date, end_date, available_dates)
        if not date_combinations:
            print(f"No valid date combinations found between {start_date} and {end_date}")
            return
        print(f"Testing {len(date_combinations)} buy/sell date combinations from {start_date} to {end_date}...")
    else:
        date_combinations = [(buy_date, sell_date)]

    # ── Pre-load full dataset to GPU once for CSV mode (parquet is per-pair) ─
    full_pd_df = full_gpu_df = None
    if not isParquet and auto:
        full_pd_df, full_gpu_df = loadOptionsData_vec([], "", in_stock, isHist, gpu_enabled=GPU_ENABLED)
    compute_lib = cudf if (GPU_ENABLED and not isParquet) else pd  # parquet path re-uploads each combo

    all_combination_results = []
    for combo_idx, (current_buy, current_sell) in enumerate(date_combinations):
        if use_date_range:
            elapsed = time.time() - start_time
            print(f"Combination {combo_idx + 1}/{len(date_combinations)}: "
                  f"Buy {current_buy}, Sell {current_sell} (Elapsed: {elapsed:.1f}s)")

        # Determine combo expiration up front
        combo_expiration = expiration_date
        if not combo_expiration:
            combo_exps = []
            ref_df = full_pd_df if full_pd_df is not None and not full_pd_df.empty else df_temp
            for e in ref_df['Expiration Date'].unique():
                sub = ref_df[ref_df['Expiration Date'] == e]
                if current_buy in sub['Timestamp'].values and current_sell in sub['Timestamp'].values:
                    combo_exps.append(e)
            if combo_exps:
                combo_expiration = find_closest_expiration_to_sell_date(sorted(combo_exps), current_sell)

        if not combo_expiration:
            if use_date_range:
                continue
            print("No expiration date found for this combination")
            return

        # Load (and upload) the slice for this combination
        if isParquet:
            pd_combo, gpu_combo = loadOptionsDataFromParquet_vec(
                in_stock, [current_buy, current_sell], combo_expiration,
                parquet_secids, gpu_enabled=GPU_ENABLED,
            )
            combo_lib = cudf if (GPU_ENABLED and gpu_combo is not None) else pd
            compute_df = gpu_combo if combo_lib is cudf else pd_combo
        else:
            # Filter the already-loaded full df to just buy/sell + expiration
            base_df = full_gpu_df if (GPU_ENABLED and full_gpu_df is not None) else full_pd_df
            combo_lib = cudf if (GPU_ENABLED and full_gpu_df is not None) else pd
            compute_df = base_df[
                (base_df['Timestamp'].isin([current_buy, current_sell]))
                & (base_df['Expiration Date'] == combo_expiration)
            ]
            pd_combo = full_pd_df  # used only for "is it empty" debug below

        if compute_df is None or len(compute_df) == 0:
            if use_date_range:
                continue
            print("No data for the specified dates")
            return

        if isParquet and parquet_secids:
            buy_stock, sell_stock = plot_stock_graph_parquet([], in_stock, current_buy, current_sell, parquet_secids)
        else:
            buy_stock, sell_stock = plot_stock_graph([], in_stock, current_buy, current_sell)

        if buy_stock is None or sell_stock is None:
            if use_date_range:
                continue
            return

        call_row = put_row = None
        call_ret = put_ret = float('-inf')
        if OPTION_TYPE in ["call", "call and put"]:
            call_row, call_ret = max_return_vec(compute_df, current_buy, current_sell, "call", combo_lib)
        if OPTION_TYPE in ["put", "call and put"]:
            put_row, put_ret = max_return_vec(compute_df, current_buy, current_sell, "put", combo_lib)

        def _row_dict(row):
            if row is None:
                return None
            try:
                return {
                    'Ask Price_buy':  float(row['Ask Price_buy']),
                    'Bid Price_sell': float(row['Bid Price_sell']),
                    'Strike Price':   float(row['Strike Price']),
                    'Expiration Date': str(row['Expiration Date']),
                }
            except Exception:
                return None

        all_combination_results.append({
            'buy_date':            current_buy,
            'sell_date':           current_sell,
            'buy_stock':           buy_stock,
            'sell_stock':          sell_stock,
            'stock_return':        total_return(buy_stock, sell_stock),
            'optimal_call_return': call_ret,
            'optimal_put_return':  put_ret,
            'optimal_call_row':    _row_dict(call_row),
            'optimal_put_row':     _row_dict(put_row),
            'combo_expiration':    combo_expiration,
        })
        if use_date_range:
            print(f"  Call: {call_ret:.1f}%  Put: {put_ret:.1f}%  "
                  f"Stock: {total_return(buy_stock, sell_stock):.1f}%")

    if not all_combination_results:
        print("No valid trades found across any date combinations. Exiting.")
        return

    # ── Reporting ───────────────────────────────────────────────────────────
    if not output:
        return pdf

    cur_runtime = time.time() - start_time
    runtime_str = f"{int(cur_runtime // 3600):02d}:{int((cur_runtime % 3600) // 60):02d}:{cur_runtime % 60:05.2f}"

    valid_calls = [r for r in all_combination_results if r['optimal_call_return'] > float('-inf')]
    valid_puts = [r for r in all_combination_results if r['optimal_put_return'] > float('-inf')]

    best_call_combo = max(valid_calls, key=lambda x: x['optimal_call_return']) if valid_calls else None
    best_put_combo = max(valid_puts, key=lambda x: x['optimal_put_return']) if valid_puts else None

    if OPTION_TYPE == "put":
        report_combo = best_put_combo
    elif OPTION_TYPE == "call":
        report_combo = best_call_combo
    else:
        report_combo = best_call_combo or best_put_combo

    if report_combo is None:
        print("No valid results to report.")
        return

    report_buy = report_combo['buy_date']
    report_sell = report_combo['sell_date']
    report_exp = report_combo['combo_expiration']

    final_figs: list = []
    if OPTION_TYPE in ["call", "call and put"] and valid_calls:
        graph_histogram([r['optimal_call_return'] for r in valid_calls], "Call", in_stock, final_figs)
    if OPTION_TYPE in ["put", "call and put"] and valid_puts:
        graph_histogram([r['optimal_put_return'] for r in valid_puts], "Put", in_stock, final_figs)

    if isParquet and parquet_secids:
        plot_stock_graph_parquet(final_figs, in_stock, report_buy, report_sell, parquet_secids)
    else:
        plot_stock_graph(final_figs, in_stock, report_buy, report_sell)

    # Strike graph for the report combo (vectorized, GPU or CPU)
    if isParquet:
        graph_pd, graph_gpu = loadOptionsDataFromParquet_vec(
            in_stock, [report_buy, report_sell], report_exp, parquet_secids,
            gpu_enabled=GPU_ENABLED,
        )
        graph_lib = cudf if (GPU_ENABLED and graph_gpu is not None) else pd
        graph_df = graph_gpu if graph_lib is cudf else graph_pd
    else:
        base_df = full_gpu_df if (GPU_ENABLED and full_gpu_df is not None) else full_pd_df
        graph_lib = cudf if (GPU_ENABLED and full_gpu_df is not None) else pd
        graph_df = base_df[
            (base_df['Timestamp'].isin([report_buy, report_sell]))
            & (base_df['Expiration Date'] == report_exp)
        ]

    if graph_df is not None and len(graph_df) > 0:
        if OPTION_TYPE in ["call", "call and put"]:
            call_strike_df = generateReturnsByStrike_vec(graph_df, report_buy, report_sell, "call", graph_lib)
            if not call_strike_df.empty:
                graph_return_strike(in_stock, report_buy, report_sell, report_exp, 'Call', call_strike_df, final_figs)
        if OPTION_TYPE in ["put", "call and put"]:
            put_strike_df = generateReturnsByStrike_vec(graph_df, report_buy, report_sell, "put", graph_lib)
            if not put_strike_df.empty:
                graph_return_strike(in_stock, report_buy, report_sell, report_exp, 'Put', put_strike_df, final_figs)

    # ── PDF assembly ────────────────────────────────────────────────────────
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 15, "Options Return Analysis Report", ln=True, align='C')
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, f"Ticker: {in_stock}", ln=True, align='C')
    pdf.ln(5)

    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 8, "Analysis Parameters:", ln=True)
    pdf.set_font("Arial", size=10)
    display_start = start_date if use_date_range else buy_date
    display_end = end_date if use_date_range else sell_date
    pdf.cell(0, 6, f"Start Date: {display_start}", ln=True)
    pdf.cell(0, 6, f"End Date: {display_end}", ln=True)
    if expiration_date:
        pdf.cell(0, 6, f"Expiration Date: {expiration_date} (Fixed)", ln=True)
    else:
        pdf.cell(0, 6, "Expiration Date: Auto-selected (Closest to sell date)", ln=True)
    pdf.cell(0, 6, f"Total Combinations Analyzed: {len(all_combination_results)}", ln=True)
    pdf.cell(0, 6, f"GPU Acceleration: {'ENABLED' if GPU_ENABLED else 'DISABLED'}", ln=True)
    pdf.cell(0, 6, f"Runtime: {runtime_str}", ln=True)
    pdf.ln(5)

    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 8, "Top 20 Date Combinations and Returns:", ln=True)
    pdf.ln(3)

    def _write_table(results_list, return_key, row_key, label):
        pdf.set_font("Arial", size=10)
        pdf.cell(0, 5, f"Sorted by: {label} Return, Order: Descending", ln=True)
        pdf.ln(1)
        pdf.set_font("Arial", size=6.5)
        for col, w in [("Buy Date", 22), ("Sell Date", 22), ("Opt %", 16), ("Opt Buy", 19),
                       ("Opt Sell", 19), ("Strike", 19), ("Exp Date", 22), ("Stk %", 16),
                       ("Stk Buy", 19), ("Stk Sell", 19)]:
            pdf.cell(w, 5, col, 1, 0, 'C')
        pdf.ln()
        for res in sorted(results_list, key=lambda x: x[return_key], reverse=True)[:20]:
            row = res[row_key]
            ret_val = res[return_key]
            pdf.cell(22, 4, res['buy_date'], 1, 0, 'C')
            pdf.cell(22, 4, res['sell_date'], 1, 0, 'C')
            pdf.cell(16, 4, f"{ret_val:.1f}" if ret_val > float('-inf') else "N/A", 1, 0, 'C')
            pdf.cell(19, 4, f"{row['Ask Price_buy']:.2f}" if row else "0.00", 1, 0, 'C')
            pdf.cell(19, 4, f"{row['Bid Price_sell']:.2f}" if row else "0.00", 1, 0, 'C')
            pdf.cell(19, 4, f"{row['Strike Price']:.0f}" if row else "0", 1, 0, 'C')
            pdf.cell(22, 4, row['Expiration Date'] if row else '', 1, 0, 'C')
            pdf.cell(16, 4, f"{res['stock_return']:.1f}", 1, 0, 'C')
            pdf.cell(19, 4, f"{res['buy_stock']:.2f}", 1, 0, 'C')
            pdf.cell(19, 4, f"{res['sell_stock']:.2f}", 1, 1, 'C')
        pdf.ln(5)

    if OPTION_TYPE in ["call", "call and put"] and valid_calls:
        _write_table(valid_calls, 'optimal_call_return', 'optimal_call_row', 'Call')
    if OPTION_TYPE in ["put", "call and put"] and valid_puts:
        _write_table(valid_puts, 'optimal_put_return', 'optimal_put_row', 'Put')

    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 8, "Detailed Analysis of Best Results:", ln=True)
    pdf.set_font("Arial", size=10)
    pdf.cell(0, 5, f"Timestamp: {dt.datetime.now()}", ln=True)
    pdf.cell(0, 5, f"VERSION: {VERSION} (GPU: {GPU_ENABLED})", ln=True)
    pdf.cell(0, 5, f"Ticker: {in_stock}", ln=True)
    pdf.cell(0, 5, f"Runtime: {runtime_str}", ln=True)
    pdf.ln(3)

    if best_call_combo and best_call_combo['optimal_call_row']:
        row = best_call_combo['optimal_call_row']
        pdf.cell(0, 5, f"Best Call Option: Strike Price: ${row['Strike Price']:.1f}, "
                       f"Exp Date: {row['Expiration Date']}, "
                       f"Return: {best_call_combo['optimal_call_return']:.0f}%", ln=True)
        pdf.cell(0, 5, f"Best dates: Buy {best_call_combo['buy_date']}, Sell {best_call_combo['sell_date']}", ln=True)
        pdf.cell(0, 5, f"Bought at ${row['Ask Price_buy']:.2f}, Sold at ${row['Bid Price_sell']:.2f}", ln=True)
        pdf.ln(3)

    if best_put_combo and best_put_combo['optimal_put_row']:
        row = best_put_combo['optimal_put_row']
        pdf.cell(0, 5, f"Best Put Option: Strike Price: ${row['Strike Price']:.1f}, "
                       f"Exp Date: {row['Expiration Date']}, "
                       f"Return: {best_put_combo['optimal_put_return']:.0f}%", ln=True)
        pdf.cell(0, 5, f"Best dates: Buy {best_put_combo['buy_date']}, Sell {best_put_combo['sell_date']}", ln=True)
        pdf.cell(0, 5, f"Bought at ${row['Ask Price_buy']:.2f}, Sold at ${row['Bid Price_sell']:.2f}", ln=True)
        pdf.ln(3)

    if OPTION_TYPE == "put":
        best_stk = min(all_combination_results, key=lambda x: x['stock_return'])
        pdf.cell(0, 5, f"BEST SHORT STOCK PERFORMANCE:  Sell {best_stk['buy_date']} at ${best_stk['buy_stock']:.2f}, "
                       f"Buy {best_stk['sell_date']} at ${best_stk['sell_stock']:.2f} -> "
                       f"Stock Decline: {best_stk['stock_return']:.2f}%", ln=True)
    else:
        best_stk = max(all_combination_results, key=lambda x: x['stock_return'])
        pdf.cell(0, 5, f"BEST STOCK PERFORMANCE:  Buy {best_stk['buy_date']} at ${best_stk['buy_stock']:.2f}, "
                       f"Sell {best_stk['sell_date']} at ${best_stk['sell_stock']:.2f} -> "
                       f"Stock Return: {best_stk['stock_return']:.2f}%", ln=True)

    PAGE_W, PAGE_H = 210, 297
    IMG_W = 150
    MAX_H = PAGE_H - 60

    for i, fig in enumerate(final_figs):
        pdf.add_page()
        img_path = OUTPUT_DIR + f"figure_{i}.png"
        fig.savefig(img_path, bbox_inches='tight', dpi=120)
        with open(img_path, 'rb') as _f:
            _f.read(16)
            _pw = struct.unpack('>I', _f.read(4))[0]
            _ph = struct.unpack('>I', _f.read(4))[0]
        prop_h = IMG_W * (_ph / _pw)
        if prop_h > MAX_H:
            out_w = MAX_H * (_pw / _ph)
            out_h = MAX_H
        else:
            out_w = IMG_W
            out_h = prop_h
        x = (PAGE_W - out_w) / 2
        y = (PAGE_H - out_h) / 2
        pdf.image(img_path, x=x, y=y, w=out_w, h=out_h)

    pdf.output(output_pdf_path)
    print(f"PDF report '{output_pdf_path}' created successfully.")

    total_runtime = time.time() - start_time
    hrs = int(total_runtime // 3600)
    mins = int((total_runtime % 3600) // 60)
    secs = total_runtime % 60
    print("-" * 60)
    print("Options Return Analysis Complete!")
    print(f"Total Runtime: {hrs:02d}:{mins:02d}:{secs:05.2f}")
    print(f"Finished at {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Analyzed {len(all_combination_results)} date combination(s)")
    print(f"GPU Acceleration: {'ENABLED' if GPU_ENABLED else 'DISABLED'}")
    print("-" * 60)

    shutil.rmtree(OUTPUT_DIR.replace(splitter, ""), ignore_errors=True)


if __name__ == "__main__":
    main()
    # Example programmatic calls (defaults=True skips prompts):
    # main(True, 'NVDA', False, "NVDA_2009_", "2009-01-02", "2009-01-08", isParquet=True)
    # main(True, 'PLTR', True, "PLTR_", "2025-02-18", "2025-02-24")
    # main(True, 'GOOG', True, "GOOG_", "2025-11-21", "2025-11-25")
