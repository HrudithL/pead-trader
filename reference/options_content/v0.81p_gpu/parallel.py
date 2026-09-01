# OptionsReturn_v08.1_GPU.py
import os

# --- GPU ACCELERATION IMPORTS ---
import cupy as cp
import cudf
# --- END GPU ACCELERATION IMPORTS ---

# Set thread environment variables for performance
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

from itertools import combinations
import matplotlib
matplotlib.use('Agg') # Use non-interactive backend for PDF/PNG
import pandas as pd
import numpy as np
from pathlib import Path
import datetime as dt
import time
import matplotlib.pyplot as plt
from fpdf import FPDF
import textwrap
import requests
from matplotlib.ticker import MaxNLocator
import sys

# Import the expected return calculator from the original v08 script
try:
    import optionExpReturn_v08
except ImportError as e:
    print(f"Warning: Could not import 'optionExpReturn_v08.py'. {e}")
    optionExpReturn_v08 = None 

print("--- Running OptionsReturn in ROBUST GPU-COMPUTE Mode (Pandas-Load, cuDF-Compute) ---")
print(f"--- CuPy (GPU) backend: {cp.__name__} ---")
print(f"--- cuDF (GPU) backend: {cudf.__name__} ---")


VERSION = "08.1_GPU"
OUTPUT_DIR = "Options_Return_Output/"
SUPER_PARENT_DIR = "Stock Market Research"
OPTION_TYPE = "call and put"

try:
    PATH = Path(__file__)
except NameError:
    PATH = Path(f"OptionsReturn.ipynb")

CURRENT_WORKING_DIR = PATH.parent.absolute()
os.chdir(CURRENT_WORKING_DIR)
end = str(CURRENT_WORKING_DIR).find(SUPER_PARENT_DIR)
splitter = os.sep
STOCK_MARKET_RESEARCH_DIR = Path(str(CURRENT_WORKING_DIR)[0:end+len(SUPER_PARENT_DIR)])

# Importing necessary modules from options.py and finance.py
sys.path.append(str(STOCK_MARKET_RESEARCH_DIR) + splitter + 'Libraries' + splitter)

# We still need the *original* Options class from v08_1, but *only*
# for its .from_ticker() method and to create dummy objects for the final report.
try:
    from options import Options
except ImportError as e:
    print(f"Warning: Could not import 'options.py'. {e}")
    Options = None 

# Import NEW GPU finance library (as seen in the v0.8p_gpu example)
# This is assumed to contain finance.total_return (GPU) and finance.fetch_stock_price (CPU)
try:
    import finance_gpu as finance
except ImportError as e:
    print(f"FATAL ERROR: Could not import 'finance_gpu.py'. {e}")
    print("This script requires the GPU-enabled finance library.")
    sys.exit(1)

#extend FPDF and add page numbers
class PDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, 'Page %s' % self.page_no(), 0, 0, 'C')

# helper functions for load options data
def _compute_df_for_exp_worker_serial_inner(options_data_gpu, expire, buy_date, sell_date, otype):
    """
    Serial worker (to be called in a loop) that runs the GPU-based
    generate_returns_by_strike function.
    """
    # This call is GPU-accelerated and returns a PANDAS df for plotting
    df = generate_returns_by_strike(options_data_gpu, expire, buy_date, sell_date, otype)
    label = "Call" if otype == "call" else "Put"
    return (expire, label, df)

def loadOptionsData(allowed_dates:list = [], exp = "", stock:str = "", isHist = False):
    """
    Loads options data from CSV (CPU), CLEANS it (CPU), 
    then uploads to a cuDF DataFrame (GPU).
    
    This replaces the slow, object-based load from v08_1.py.
    
    Returns:
    cudf.DataFrame: A GPU-based DataFrame with all options data.
    pandas.DataFrame: A CPU-based DataFrame for prompts/legacy code.
    """

    targFiles = []
    
    if stock:
        if not isHist:
            targFiles += [f"input_files/{stock}_1.csv"]
            targFiles += [f"input_files/{stock}_2.csv"]
        else:
            targFiles += [f'../../Options_History/{stock}_Options_Storage/{stock} Options History.csv']

    i = 0
    while True:
        if stock == "":
            csv_file = input("Please enter the path to the CSV file containing historical option data (default: finished entering): ")
        else:
            if i == len(targFiles):
                break
            csv_file = targFiles[i]
            i+=1
        
        if (csv_file == ''):
            print("exiting....")
            break

        # Check if the file exists (CPU operation)
        if not os.path.exists(csv_file):
            print(f"The specified file \"{csv_file}\" does not exist. Please try again.")
            stock = ""
            continue
        
        # We don't load here, just collect paths
        if csv_file not in targFiles:
             targFiles.append(csv_file)

    try:
        # 1. Load all CSVs into a *Pandas* DataFrame list (CPU)
        if not targFiles:
             raise FileNotFoundError("No input files were found.")
        print(f"Loading data from: {targFiles}")
        df_list = [pd.read_csv(f) for f in targFiles]
        
        # 2. Combine them on the CPU
        pandas_df = pd.concat(df_list, ignore_index=True)

        # 3. *** ROBUST CLEANING (on CPU using Pandas) ***
        # This robustly handles errors/bad data from the CSV
        pandas_df["Strike Price"]    = pd.to_numeric(pandas_df["Strike Price"], errors="coerce")
        pandas_df["Bid Price"]       = pd.to_numeric(pandas_df["Bid Price"], errors="coerce")
        pandas_df["Ask Price"]       = pd.to_numeric(pandas_df["Ask Price"], errors="coerce")
        pandas_df["Stock Price"]     = pd.to_numeric(pandas_df["Stock Price"], errors="coerce")
        pandas_df["Timestamp"]       = pd.to_datetime(pandas_df["Timestamp"], errors="coerce")
        pandas_df["Expiration Date"] = pd.to_datetime(pandas_df["Expiration Date"], errors="coerce")
        
        # 4. Drop rows where essential data failed to parse
        # This is critical before uploading to GPU
        clean_pandas_df = pandas_df.dropna(
            subset=["Symbol", "Option Type", "Strike Price", "Timestamp", "Expiration Date", "Stock Price"]
        )

        if clean_pandas_df.empty:
            raise ValueError("No valid data remained after cleaning.")
            
        # 5. *** NORMALIZE & UPLOAD TO GPU ***
        # Create the key columns needed for GPU filtering
        normalized_df = clean_pandas_df.copy()
        normalized_df['ExpireKey'] = normalized_df['Expiration Date'].dt.strftime("%Y-%m-%d")
        normalized_df['TimestampKey'] = normalized_df['Timestamp'].dt.strftime("%Y-%m-%d")
        
        # Rename 'Stock Price' to 'Underlying' for compatibility with GPU functions
        normalized_df.rename(columns={'Stock Price': 'Underlying'}, inplace=True)
        
        # Select only columns needed for computation
        cols_to_upload = [
            'Symbol', 'Option Type', 'Strike Price', 'Ask Price', 'Bid Price',
            'Underlying', 'ExpireKey', 'TimestampKey', 'Expiration Date', 'Timestamp'
        ]
        # Ensure all required columns exist
        cols_to_upload = [col for col in cols_to_upload if col in normalized_df.columns]
        
        OptionsData_GPU = cudf.from_pandas(normalized_df[cols_to_upload])
        
        if OptionsData_GPU.empty:
             raise ValueError("GPU DataFrame is empty after upload.")
             
        print(f"Successfully loaded and normalized {len(OptionsData_GPU)} options on GPU.")
        
        # 6. Create the legacy pandas df for UI/prompts
        # We filter the *already clean* pandas DF
        raw_df_pandas = clean_pandas_df.copy() 
        if allowed_dates and allowed_dates[0] != "" and allowed_dates[1] != "":
            # Ensure we compare strings to strings
            allowed_dates_str = [pd.to_datetime(d).strftime("%Y-%m-%d") for d in allowed_dates if d]
            raw_df_pandas = raw_df_pandas[raw_df_pandas['Timestamp'].dt.strftime("%Y-%m-%d").isin(allowed_dates_str)]
        if exp != "":
            raw_df_pandas = raw_df_pandas[raw_df_pandas['Expiration Date'].dt.strftime("%Y-%m-%d") == exp]

    except Exception as e:
        print(f"An error occurred while loading the data: {e}")
        OptionsData_GPU = cudf.DataFrame()
        raw_df_pandas = pd.DataFrame()
    
    return OptionsData_GPU, raw_df_pandas


def generate_returns_by_strike(options_data_gpu: cudf.DataFrame, expiration_date, buy_date, sell_date, oType="call") -> pd.DataFrame:
    """
    Build 'Return by Strike' for a single expiration.
    This is GPU-ONLY, operating on a cudf.DataFrame.
    Returns a PANDAS DataFrame *only* for plotting.
    (Replaces v08_1.py's generateReturnsByStrike)
    """
    if options_data_gpu.empty:
        return pd.DataFrame(columns=["Strike Price", "Return", "Underlying"])

    exp_key = pd.to_datetime(expiration_date, errors="coerce").strftime("%Y-%m-%d")
    
    # 1. Filter by expiration and type on GPU using boolean masking
    mask = (options_data_gpu['ExpireKey'] == exp_key) & (options_data_gpu['Option Type'] == oType)
    filtered_df = options_data_gpu[mask]
    
    if filtered_df.empty:
        # This is NOT an error, just no data for this combo
        print(f"[generate_returns_by_strike] No matches for {oType} @ {exp_key} (buy {buy_date}, sell {sell_date}).")
        return pd.DataFrame(columns=["Strike Price", "Return", "Underlying"])

    # 2. Get Asks/Bids on GPU using boolean masking
    buy_data = filtered_df[filtered_df['TimestampKey'] == buy_date]
    buy_data = buy_data[['Symbol', 'Ask Price', 'Strike Price', 'Underlying']]
    
    sell_data = filtered_df[filtered_df['TimestampKey'] == sell_date]
    sell_data = sell_data[['Symbol', 'Bid Price']]

    # 3. Merge Asks and Bids on GPU
    df = buy_data.merge(sell_data, on='Symbol', how='inner')

    if df.empty:
        print(f"[generate_returns_by_strike] No valid buy/sell pairs for {oType} @ {exp_key}.")
        return pd.DataFrame(columns=["Strike Price", "Return", "Underlying"])

    print(f"[INFO] generate_returns_by_strike: Calculating {len(df)} returns on GPU...")

    # 4. Calculate returns on GPU using CuPy
    cp_asks = df['Ask Price'].values
    cp_bids = df['Bid Price'].values
    
    # Use the finance_gpu.total_return function
    cp_returns = finance.total_return(cp_asks, cp_bids)

    # Handle ask=0 (inf) or nan cases, set return to -100%
    cp_returns = cp.where(cp_asks == 0, -100.0, cp_returns)
    cp_returns = cp.nan_to_num(cp_returns, nan=-100.0)
    
    # 5. Add new 'Return' column back to GPU DataFrame
    df['Return'] = cp_returns
    
    # 6. Transfer final DataFrame to CPU (pandas) for plotting
    df_pandas = df.to_pandas()
    
    print(f"[generate_returns_by_strike] Built {len(df_pandas)} rows for {oType} @ {exp_key}.")
    return df_pandas


def max_return(options_data_gpu: cudf.DataFrame, expiration_date, buy_date, sell_date, oType):
    """
    Find best point-wise return for a given expiration and type.
    This is GPU-ONLY.
    Returns (best_option_CPU_object, best_return_value)
    (Replaces v08_1.py's max_return)
    """

    exp_key = pd.to_datetime(expiration_date, errors="coerce").strftime("%Y-%m-%d")

    # 1. Filter by expiration and type on GPU using boolean masking
    mask = (options_data_gpu['ExpireKey'] == exp_key) & (options_data_gpu['Option Type'] == oType)
    filtered_df = options_data_gpu[mask]
    
    if filtered_df.empty:
        return None, None # Not an error, just no data

    # 2. Get Asks/Bids on GPU using boolean masking
    buy_data = filtered_df[filtered_df['TimestampKey'] == buy_date]
    buy_data = buy_data[['Symbol', 'Ask Price', 'Strike Price', 'Underlying', 'ExpireKey']]
    
    sell_data = filtered_df[filtered_df['TimestampKey'] == sell_date]
    sell_data = sell_data[['Symbol', 'Bid Price']]

    # 3. Merge Asks and Bids on GPU
    df = buy_data.merge(sell_data, on='Symbol', how='inner')

    if df.empty:
        return None, None # Not an error

    print(f"[INFO] max_return: Scoring {len(df)} options on GPU...")
    
    # 4. Calculate scores on GPU
    cp_asks = df['Ask Price'].values
    cp_bids = df['Bid Price'].values
    cp_scores = finance.total_return(cp_asks, cp_bids)
        
    cp_scores = cp.where(cp_asks == 0, -100.0, cp_scores)
    cp_scores = cp.nan_to_num(cp_scores, nan=-100.0)
    
    # 5. Add 'Return' column and find max on GPU
    df['Return'] = cp_scores
    
    if df['Return'].empty:
        return None, None
        
    max_return_val = df['Return'].max()
    
    # 6. Get the single best row (still on GPU)
    best_row_gpu = df[df['Return'] == max_return_val].head(1)
    
    # 7. Transfer *only the single best row* back to CPU
    best_row_cpu = best_row_gpu.to_pandas().iloc[0] # This is a pandas.Series
    best_score = float(best_row_cpu['Return'])

    # 8. Re-create a *single* Options object (on CPU) for reporting
    #    using the *original* Options class from options.py
    try:
        opt = Options(
            underlying_asset = best_row_cpu['Underlying'],
            strike_price     = float(best_row_cpu['Strike Price']),
            option_type      = oType,
            expiration_date  = best_row_cpu['ExpireKey']
        )
        # Manually add the history for just the two dates needed
        opt.ask_history[buy_date] = float(best_row_cpu['Ask Price'])
        opt.bid_history[sell_date] = float(best_row_cpu['Bid Price'])
    except Exception as e:
        print(f"Warning: Could not create dummy Option object. {e}")
        opt = None

    return opt, best_score


def optimal_return(options_data_gpu: cudf.DataFrame, buy_date, sell_date, option) -> tuple[None | object, float]:
    """
    Find the highest max return across all possible expiration dates
    in the cuDF DataFrame.
    (Replaces v08_1.py's optimal_return)
    """
    # Get unique expiration dates (on GPU, then move to CPU list)
    unique_expiration_dates = options_data_gpu['ExpireKey'].unique().to_pandas().tolist()
    
    best_option = None
    highest_return = float('-inf')

    print(f"[INFO] optimal_return: Scanning {len(unique_expiration_dates)} expirations...")
    for expiration_date in unique_expiration_dates:
        # This call is now GPU-accelerated
        curropt, max_return_value = max_return(options_data_gpu, expiration_date,
                                                 buy_date, sell_date, option)

        if max_return_value is not None and max_return_value > highest_return:
            highest_return = max_return_value
            best_option = curropt

    return best_option, highest_return

########### Super Return ##########
def super_return_fast(options_data_gpu: cudf.DataFrame, df_pandas: pd.DataFrame, start_date, end_date, expiration_date, oType="call"):
    """
    Fast super return: GPU-ONLY.
    Vectorizes the (buy, sell) scan across CuPy arrays built by cuDF.
    This function replaces the `use_date_range` loop from v08_1.py.
    Returns: (best_buy, best_sell, best_ret) or None
    """

    # 1) build trading date list from pandas_df (CPU task)
    ts_col = 'Timestamp' # From our new loadOptionsData
    if ts_col not in df_pandas.columns:
        raise ValueError(f"super_return_fast: 'Timestamp' column not in DataFrame.")

    all_ts = pd.to_datetime(df_pandas[ts_col].unique(), errors='coerce')
    all_ts = [d for d in all_ts if pd.notna(d)]
    sdt = pd.to_datetime(start_date)
    edt = pd.to_datetime(end_date)
    dates = sorted([d for d in all_ts if sdt <= d <= edt])
    if len(dates) < 2:
        print("[WARN] Not enough trading days in range.")
        return None
    
    date_keys = [d.strftime("%Y-%m-%d") for d in dates]
    T = len(date_keys)

    # 2) pre-filter options once on GPU using boolean masking
    exp_key = pd.to_datetime(expiration_date, errors="coerce").strftime("%Y-%m-%d")
    
    mask = (options_data_gpu['ExpireKey'] == exp_key) & \
           (options_data_gpu['Option Type'] == oType) & \
           (options_data_gpu['TimestampKey'].isin(date_keys))
    df = options_data_gpu[mask]

    if df.empty:
        print("[WARN] No options match expiration/type/dates.")
        return None
    
    M = df['Symbol'].nunique()
    print(f"[INFO] super_return_fast: Processing {M} options over {T} days on GPU.")

    # 3) build ASK/BID matrices (M x T) directly on GPU
    print("[INFO] super_return_fast: Building pivot tables on GPU...")
    try:
        asks_gpu_df = df.pivot_table(index='Symbol', columns='TimestampKey', values='Ask Price')
        bids_gpu_df = df.pivot_table(index='Symbol', columns='TimestampKey', values='Bid Price')
    except Exception as e:
        print(f"[WARN] cuDF pivot_table failed. This can happen with duplicate (Symbol, TimestampKey) entries. {e}")
        return None

    # Re-order columns to match our sorted date_keys list
    asks_gpu_df = asks_gpu_df.reindex(columns=date_keys)
    bids_gpu_df = bids_gpu_df.reindex(columns=date_keys)

    # 4) Get CuPy arrays from cuDF DataFrames (zero-copy)
    ASK = asks_gpu_df.values.astype(cp.float32)
    BID = bids_gpu_df.values.astype(cp.float32)
    del asks_gpu_df, bids_gpu_df # Free GPU memory

    # 5) scan pairs vectorially on GPU
    invASK = cp.where((cp.isnan(ASK) | (ASK==0)), cp.nan, (1.0 / ASK))
    del ASK # Free GPU memory

    best_val = -cp.inf
    best_i = best_j = -1

    # Iterate over sell index j (outer)
    for j in range(1, T):
        bj = BID[:, j] # shape (M,)
        if cp.all(cp.isnan(bj)):
            continue

        # (M,) * (M, T) -> (M, T) element-wise
        # nanmax over axis=0 (options) -> (T,)
        ratios = cp.nanmax(bj[:, None] * invASK, axis=0)
        
        # Only consider i < j
        valid = ~cp.isnan(ratios)
        if j > 0:
            valid[j:] = False # Ignore same-day and future buys
        
        if not cp.any(valid):
            continue

        # best for this j
        j_best_idx_gpu = cp.nanargmax(cp.where(valid, ratios, -cp.inf))
        j_best_idx = int(j_best_idx_gpu.get())
        j_best_val = float(ratios[j_best_idx].get()) - 1.0 # convert b/a -> b/a - 1

        if j_best_val > best_val:
            best_val = j_best_val
            best_i, best_j = j_best_idx, j

    if best_i < 0:
        print("[WARN] Super Return found no valid trades in the range.")
        return None

    best_buy = date_keys[best_i]
    best_sell = date_keys[best_j]
    best_ret_pct = best_val * 100.0

    print(f"[INFO] Super Return (fast): Buy {best_buy}, Sell {best_sell}, Return: {round(best_ret_pct)}%")
    return best_buy, best_sell, best_ret_pct

# --- The following functions are CPU-bound (I/O, plotting) and MUST REMAIN ---

def super_underlying_return(ticker, start_date, end_date) -> tuple[str, str, float] | None:
    """
    Find best (buy, sell) dates. This is I/O-bound and remains on CPU.
    It uses the CPU function finance.fetch_stock_price
    """
    
    start_dt = dt.datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = dt.datetime.strptime(end_date, "%Y-%m-%d")
    
    date_list = [start_dt + dt.timedelta(days=i)
                 for i in range((end_dt - start_dt).days + 1)]

    best_buy, best_sell = None, None
    best_ret = float('-inf')

    for buy_dt, sell_dt in combinations(date_list, 2):
        buy_str = buy_dt.strftime("%Y-%m-%d")
        sell_str = sell_dt.strftime("%Y-%m-%d")
        
        fig=[]
        try:
            # Use the CPU I/O function
            buy_stock, sell_stock = plot_stock_graph(fig, ticker, buy_str, sell_str)
            if buy_stock is None or sell_stock is None:
                continue
            max_ret = finance.total_return(buy_stock, sell_stock)
        except Exception as e:
            continue

        if max_ret is not None and max_ret > best_ret:
            best_ret = max_ret
            best_buy, best_sell = buy_str, sell_str

    if best_buy is None or best_sell is None:
        print("[WARN] Super Underlying Return found no valid trades in the range.")
        return None
        
    print(f"[INFO] Super Underlying Return best: Buy {best_buy}, Sell {best_sell}, Return: {round(best_ret)}%")

    return (best_buy, best_sell, best_ret)


def getDateInput(ask:str, default:str="") -> str:
    '''
    CPU-bound user input.
    '''
    while(True):
        dateStr = input(f"{ask} (format: yyyy-mm-dd): \n")
        if dateStr == "":
            return default
        formatCheck = dateStr.split("-")
        if dateStr.replace('-','').isnumeric() == False or len(dateStr) != 10 or len(formatCheck) != 3 or len(formatCheck[0]) != 4 or len(formatCheck[1]) != 2 or len(formatCheck[2]) != 2:
            print("invalid date format, please try again")
            continue
        return dateStr

def graph_return_strike(buy:str, sell:str, expire: str, oType:str, df:pd.DataFrame, figs:list[plt.figure]):
    """
    Plots the graph. Takes a PANDAS DataFrame.
    This MUST use matplotlib to generate images for the PDF.
    """
    if df is None or df.empty:
        print(f"Skipping plot for {oType} @ {expire}: No data.")
        return # nothing to plot
        
    df = df.drop_duplicates(subset=["Strike Price"]).sort_values(by="Strike Price", ascending=True).reset_index(drop=True)

    strike_x = list(df["Strike Price"])
    strike_y = list(df["Return"])
    
    fig, ax = plt.subplots()
    ax.plot(strike_x, strike_y, marker='o')

    cur_ticker = "UNKNOWN"
    try:
        if strike_y:
            max_y_idx = np.argmax(strike_y)
            max_y_value = strike_y[max_y_idx]
            max_x_value = strike_x[max_y_idx]
            
            # Use suptitle for main info and title for max return
            formatted_buy_date = dt.datetime.strptime(buy, "%Y-%m-%d").strftime("%m/%d/%y")
            formatted_sell_date = dt.datetime.strptime(sell, "%Y-%m-%d").strftime("%m/%d/%y")
            formatted_expire_date = dt.datetime.strptime(expire, "%Y-%m-%d").strftime("%m/%d/%y")
            
            cur_ticker = df.loc[0, "Underlying"]
            
            ax.set_title(f"{cur_ticker} {oType} Buy {formatted_buy_date} | Sell {formatted_sell_date} | Expiration {formatted_expire_date}", fontsize=10)
            fig.suptitle(f'Max Return: {max_y_value:.0f}% at Strike Price: ${max_x_value}', fontsize=12, color='red', weight='bold')

        else:
             ax.set_title(f"Return vs Strike for {oType} @ {expire} (No Data)")
    
    except Exception as e:
        ax.set_title(f"Return vs Strike for {oType} @ {expire}") # Fallback title
        print(f"Error during plotting: {e}")

    ax.set_xlabel('Strike Price ($)')
    ax.set_ylabel('Return (%)')
    ax.grid(True)
    fig.tight_layout(rect=[0, 0.03, 1, 0.95]) # Adjust for suptitle

    try:
        plt.savefig(OUTPUT_DIR + f"Return vs Strike Price Plot for {cur_ticker} {oType} Option with expiration date {expire}.pdf",
                    format="pdf", bbox_inches="tight")
        print(f"Strike {oType} Graph successfully created for expiration {expire}")
    except Exception as e:
        print(f"Failed to save plot: {e}")
        pass 

    figs.append(fig)
    plt.close(fig)

def plot_stock_graph(fig_storage: list, 
                     ticker: str, buy_date:str,
                     sell_date:str, h: str = "2024-08-09") -> tuple[float | None, float | None]:
    """
    Adds a stock price graph. This is I/O-bound and remains on CPU.
    It MUST use matplotlib for the PDF.
    """
    # Use the finance_gpu library's CPU function
    buy_stock_price, sell_stock_price = None, None
    try:
        buy_stock_price = finance.fetch_stock_price(ticker, buy_date)
        sell_stock_price = finance.fetch_stock_price(ticker, sell_date)
    except ValueError as e:
        print(f"Could not find buy ({buy_date}) or sell ({sell_date}) in stock history. {e}")
        # We can still plot the graph even if exact dates are missing
    except Exception as e:
        print(f"Error fetching stock price: {e}")

    
    # --- Plotting logic (CPU/Matplotlib) ---
    response = requests.get('https.api.tradier.com/v1/markets/history',
        params={'symbol': f'{ticker}', 'interval': 'daily', 'start': f'{h}', 'end': f'{dt.datetime.now().date()}', 'session_filter': 'all'},
        headers={'Authorization': 'Bearer REDACTED_SEE_ROTATE_THIS_TOKEN', 'Accept': 'application/json'}  # noqa: original hardcoded a live token -- redacted when copying into this repo, see reference/options_content/README.md
    )
    json_response = response.json()
    date_list = []
    price_list = []
    try:
        for i in range(len(json_response["history"]["day"])):
            raw_date = json_response["history"]["day"][i]["date"]
            date_obj = pd.to_datetime(raw_date)
            date_list.append(date_obj.strftime("%m/%d/%y").lstrip('0'))
            price_list.append(json_response["history"]["day"][i]["close"])
    except Exception:
        print(f"{ticker} raised an error parsing stock history.")
        return buy_stock_price, sell_stock_price

    if not date_list:
        print(f"{ticker} returned no stock history data.")
        return buy_stock_price, sell_stock_price

    fig, ax = plt.subplots()
    create_graph(date_list, price_list, "Stock Price", fig, ax)
    plt.title(f"{ticker} Stock Price", fontsize=8)
    plt.xlabel(f"Date")
    plt.ylabel("Stock Price ($)")

    fig_storage.append(fig)
    plt.close(fig)
    
    return buy_stock_price, sell_stock_price

def create_graph(x: list, y: list, plot_label: str, fig: plt.Figure, ax: plt.Axes) -> None:
    """
    Creates a graph on the provided fig and ax. CPU/Matplotlib.
    """
    if len(x) == len(y):
        plt.plot(x, y, label=plot_label, marker="")
        ax.set_xlim(left=-1)
        max_ticks = 10
        step = max(1, len(x) // max_ticks)
        xticks = x[::step]
        
        if len(x) > 0 and x[-1] not in xticks:
            xticks = list(xticks) + [x[-1]]
            
        ax.set_xticks(xticks)
        ax.xaxis.set_major_locator(MaxNLocator(nbins=10, prune='both'))
        fig.autofmt_xdate(rotation=30)
        ax.tick_params(axis="x", rotation=30, labelsize=6)
        ax.tick_params(axis="y", labelsize=8)
    else:
        raise ValueError("x and y must be of the same length")

def main(defaults: bool = False, in_stock:str = "", isHist: bool = True,
         prefix="", buy = "", sell = "", expire="", output=True, pdf = None, Otype=""):
    """
    Main function. GPU-COMPUTE-FOCUSED.
    I/O, Cleaning, and Plotting remain on CPU.
    """

    t0 = time.perf_counter()

    if not defaults:
        in_stock = input("What ticker would you like to perform calulations for? ").upper()
        prefix = in_stock + '_'
        auto = input("Would you like to use automatic data loading? (y/n): ").strip().lower() in ['y', 'yes']
        if auto:
            b = input("would you like to use local (input_files/{stock}_1, input_files/{stock}_2) or history files (../../Options_History/{stock}_Options_Storage/CURRENT {stock} Options History.csv)? (h/l): ")
            isHist = b.lower() in ['h', 'history']
        else:
            auto = True # Force auto if not stock
        
        if defaults:
            in_option_type = Otype
        else:
            in_option_type = input("Call or Put option (C or P) (Default is both): ")

        if in_option_type.startswith("C") or in_option_type.startswith("c"):
            OPTION_TYPE = "call"
        elif in_option_type.startswith("P") or in_option_type.startswith("p"):
            OPTION_TYPE = "put"
        else:
            OPTION_TYPE = "call and put"
    else:
        OPTION_TYPE = Otype if Otype else "call and put"
        auto = True

    # PDF/File output setup (CPU-bound)
    current_date = dt.datetime.now().strftime('%Y-%m-%d')
    output_pdf_path = prefix + "Options_Return_Report_v" + VERSION + "_" + current_date + ".pdf"
    try:
        os.makedirs(OUTPUT_DIR)
    except FileExistsError:
        pass
    if pdf == None:
        pdf = PDF()
    return_file_path = OUTPUT_DIR + "Options_Return_Output.txt"
    return_file = open(return_file_path, "w")
    return_file.write("Timestamp: " + str(dt.datetime.now()) + '\n')

    if not defaults:
        # Date input logic (CPU-bound)
        buy_date = getDateInput("Please enter the buy date in format YYYY-MM-DD (default to earliest): ")
        sell_date = getDateInput("Please enter the sell date in format YYYY-MM-DD (default to latest): ")
        
        # REPLACED use_date_range with use_super
        use_super_input = input("Find best trade (Super Return) in date range? (y/n) default is n: ").strip().lower()
        use_super = use_super_input in ["y", "yes"]
        
        use_super_underlying_input = input("Find best *stock* trade in date range? (y/n) default is n: ").strip().lower()
        use_super_underlying = use_super_underlying_input in ["y", "yes"]
        
        if use_super:
            expiration_date = "" # Super return finds the best expiration
        else:
            expiration_date = getDateInput("Please enter a specific expiration date (default: all): ", default="")
    else:
        buy_date, sell_date, expiration_date = buy, sell, expire
        use_super = use_super_underlying = False

    t_load = time.perf_counter()
    if use_super: expiration_date = ""
    all_expire_dates = []
    allowed_dates = [buy_date, sell_date] 
    
    # *** ROBUST HYBRID LOAD (CPU-Clean, GPU-Upload) ***
    # options_data_gpu is a cudf.DataFrame
    # raw_df is a pandas.DataFrame (for CPU tasks)
    options_data_gpu, raw_df = loadOptionsData(allowed_dates, expiration_date, in_stock, isHist)
    
    if options_data_gpu.empty or raw_df.empty:
        print("No options data loaded. Exiting.")
        return_file.close()
        return
        
    df = raw_df # df is the pandas DF (for CPU UI logic)
    
    # Fill default buy/sell if empty (after df is loaded)
    try:
        if buy_date == "":
            buy_date = df['Timestamp'].min().strftime("%Y-%m-%d")
            print(f" [INFO] Using earliest available buy date: {buy_date}")
        if sell_date == "":
            sell_date = df['Timestamp'].max().strftime("%Y-%m-%d")
            print(f" [INFO] Using latest available sell date: {sell_date}")
    except Exception as e:
        print(f"Error setting default dates: {e}. Using user-provided.")
        if buy_date == "" or sell_date == "":
             raise ValueError("Could not determine default dates and none provided.")

    if sell_date == buy_date:
        print("Buy date should not be the same as sell date. Exiting.")
        return_file.close()
        return

    # Get all available expiration dates from the GPU
    all_expiry_dates_pd = options_data_gpu['Expiration Date'].unique().to_pandas()
    all_expiry_dates_dt = [d.to_pydatetime() for d in all_expiry_dates_pd]

    if use_super:
        sell_date_dt = dt.datetime.strptime(sell_date, "%Y-%m-%d")
        valid_expiry_dates = [date for date in all_expiry_dates_dt if date > sell_date_dt]
        if not valid_expiry_dates:
            print("No valid expiration dates found after the sell date for Super Return.")
            # Fallback: use all dates
            all_expire_dates = options_data_gpu['ExpireKey'].unique().to_pandas().tolist()
        else:
            nearest_expiry = min(valid_expiry_dates)
            expiration_date = nearest_expiry.strftime("%Y-%m-%d")
            all_expire_dates = [expiration_date]
            print(f"Super Return using nearest expiration date: {expiration_date}")
    else:
        unique_expire_keys = options_data_gpu['ExpireKey'].unique().to_pandas().tolist()
        if expiration_date != "":
            if expiration_date not in unique_expire_keys:
                print(f"Warning: specified expiration {expiration_date} not found...")
                all_expire_dates = unique_expire_keys # Use all
                expiration_date = all_expire_dates[0] if all_expire_dates else ""
            else:
                all_expire_dates = [expiration_date] # Use specified
        else:
            all_expire_dates = unique_expire_keys # Use all
            expiration_date = all_expire_dates[0] if all_expire_dates else ""
    
    if not all_expire_dates:
        print("no valid expiration date found - exiting")
        return_file.close()
        return

    t_load_final = time.perf_counter()
    print(f"LOADING TIME: {t_load_final - t_load:.2f}")

    h = "2024-08-09"
    figs = []
    
    try:
        earliest_data_date = df['Timestamp'].min()
        if h < earliest_data_date.strftime("%Y-%m-%d"):
            h = earliest_data_date.strftime("%Y-%m-%d")
    except Exception:
        pass 

    # This is a CPU I/O + Plotting function
    buy_stock, sell_stock = plot_stock_graph(figs, in_stock, buy_date, sell_date, h=h)

    if use_super_underlying:
        try:
            # This is a CPU I/O function
            super_underlying_result = super_underlying_return(in_stock, buy_date, sell_date)
        except Exception as e:
            print(f" [WARN] super_underlying_return raised an error: {e}")
            super_underlying_result = None
    else:
        super_underlying_result = None
    
    ########## 5 (from v08_1) ##########
    print("Running Expected Return calculations (from v08)...")
    
    # This call is to the v08 (CPU?) version from the target file
    # It requires the pandas DataFrame `df`
    if optionExpReturn_v08:
        opt_obj, ret = optionExpReturn_v08.main(df) 
        
        print(f"Expected Return calculation complete. Best option found:")
        print(opt_obj)
        
        # We use the CPU-based Options class for parsing
        maxOpt = Options.from_ticker(opt_obj.tick)
    else:
        print("Skipping Expected Return: module not loaded.")
        maxOpt = Options(in_stock, 0, 'call', '1970-01-01') # Dummy
    
    # Report writing (CPU-bound)
    formatted_buy_date = dt.datetime.strptime(buy_date, "%Y-%m-%d").strftime("%m/%d/%y")
    formatted_sell_date = dt.datetime.strptime(sell_date, "%Y-%m-%d").strftime("%m/%d/%y")

    return_file.write(f'Ticker: {maxOpt.underlying_asset}\n')
    return_file.write(f'Buy Date: {formatted_buy_date}\n')
    return_file.write(f'Sell Date: {formatted_sell_date}\n')

    # --- Job List Creation ---
    available_types = options_data_gpu['Option Type'].unique().to_pandas().tolist()
    print(f"[INFO] Data contains option types: {available_types}")

    jobs = []
    if OPTION_TYPE in ("call", "call and put") and 'call' in available_types:
        jobs += [("call", e) for e in all_expire_dates]
    
    if OPTION_TYPE in ("put", "call and put") and 'put' in available_types:
        jobs += [("put", e) for e in all_expire_dates]
    
    if not jobs:
        print("[WARN] No jobs to process based on user request and available data.")
        
    t_returns_by_strike = time.perf_counter()

    computed = []
    print(f"[INFO] Processing {len(jobs)} expiration/type pairs on GPU (serially)...")
    for kind, e in jobs:
        # *** PURE GPU COMPUTE HAPPENS HERE ***
        computed.append(
            _compute_df_for_exp_worker_serial_inner(
                options_data_gpu, e, buy_date, sell_date, kind
            )
        )
    
    t_returns_by_strike_done = time.perf_counter()
    print(f"TIME TO GENERATE RETURNS BY STRIKE: {t_returns_by_strike_done - t_returns_by_strike:.2f}")

    # Plotting is CPU-bound
    for expire, label, df_result in computed:
        graph_return_strike(buy_date, sell_date, expire, label, df_result, figs)

    print("Calculating Optimal Returns...")
    t_calc = time.perf_counter()

    ########## 10 (GPU Version) ##########
    if output == True:
        return_file.write("\n")
        if (OPTION_TYPE == "call" or OPTION_TYPE == "call and put"):
            if 'call' in available_types:
                # *** PURE GPU COMPUTE HAPPENS HERE ***
                optimal_call_option, opt_call_return = optimal_return(options_data_gpu,
                    buy_date, sell_date, "call")
                
                if optimal_call_option and opt_call_return is not None:
                    duration = dt.datetime.strptime(optimal_call_option.expiration_date, '%Y-%m-%d') - dt.datetime.strptime(buy_date, '%Y-%m-%d')
                    duration_days = duration.days
                    return_file.write(
                        f"Best Call Option: {optimal_call_option.readable_ticker()}, "
                        f"Return: {opt_call_return:.0f}%, or "
                    )
                    if buy_stock is not None and sell_stock is not None:
                        stock_return = finance.total_return(buy_stock, sell_stock)
                        if stock_return != 0:
                            return_file.write(f"{(opt_call_return/stock_return):.2f}x the underlying.\n")
                        else:
                            return_file.write("N/A x the underlying.\n")
                    else:
                        return_file.write("N/A x the underlying.\n")
                    
                    if 'use_super' in locals() and use_super:
                        # *** PURE GPU COMPUTE HAPPENS HERE ***
                        print(f"Calculating Super Return for Call @ {expiration_date}...")
                        best_trade = super_return_fast(options_data_gpu, raw_df,
                            buy_date, sell_date, expiration_date, oType="call")
                        if best_trade:
                            best_buy_sr, best_sell_sr, best_ret_sr = best_trade
                            return_file.write(
                                f"Super Return Best Trade (Call): Buy {best_buy_sr}, Sell {best_sell_sr}, Return: {round(best_ret_sr)}%\n"
                            )
                    
                    if buy_stock is not None and buy_stock > 0:
                        return_file.write(f"Duration is {duration_days} days, hurdle is {((optimal_call_option.strike_price / buy_stock) - 1)*100:.2f}%.\n")
                    else:
                        return_file.write(f"Duration is {duration_days} days.\n")

                    return_file.write(
                        f'Bought at ${float(optimal_call_option.ask_history[buy_date]):.2f} on {buy_date}. '
                        f'Underlying stock price on {buy_date} was ${buy_stock:.2f}\n'
                        f'Sold at ${float(optimal_call_option.bid_history[sell_date]):.2f} on {sell_date}. '
                        f'Underlying stock price on {sell_date} was ${sell_stock:.2f}\n'
                    )
                    if buy_stock is not None and sell_stock is not None:
                        return_file.write(f'Underlying stock return is {finance.total_return(buy_stock, sell_stock):.2f}% \n\n')
                    else:
                        return_file.write('\n')
                else:
                    print("No valid call option found.")
                    return_file.write("No valid call option found.\n\n")
            else:
                print("No valid call option data found.")
                return_file.write("No valid call option data found.\n\n")

        if (OPTION_TYPE == "put" or OPTION_TYPE == "call and put"):
            if 'put' in available_types:
                # *** PURE GPU COMPUTE HAPPENS HERE ***
                optimal_put_option, opt_put_return = optimal_return(options_data_gpu,
                    buy_date, sell_date, "put")
                
                if optimal_put_option:
                    return_file.write(f"Best Put Option: {optimal_put_option.readable_ticker()}, Return: {opt_put_return:.0f}%\n")
                    
                    if 'use_super' in locals() and use_super:
                        # *** PURE GPU COMPUTE HAPPENS HERE ***
                        print(f"Calculating Super Return for Put @ {expiration_date}...")
                        best_trade = super_return_fast(options_data_gpu, raw_df,
                            buy_date, sell_date, expiration_date, oType="put")
                        if best_trade:
                            best_buy_sr, best_sell_sr, best_ret_sr = best_trade
                            return_file.write(
                                f"Super Return Best Trade (Put): Buy {best_buy_sr}, Sell {best_sell_sr}, Return: {round(best_ret_sr)}%\n"
                            )
                            
                    return_file.write(
                        f'Bought at ${float(optimal_put_option.ask_history[buy_date]):.2f} on {buy_date}. '
                        f'Underlying stock price on {buy_date} was ${buy_stock:.2f}\n'
                        f'Sold at ${float(optimal_put_option.bid_history[sell_date]):.2f} on {sell_date}. '
                        f'Underlying stock price on {sell_date} was ${sell_stock:.2f}\n\n'
                    )
                else:
                    print("No valid put option found.")
                    return_file.write("No valid put option found.\n\n")
            else:
                print("No valid put option data found.")
                return_file.write("No valid put option data found.\n\n")

    return_file.close()
    
    print(f"Calculation time: {time.perf_counter() - t_calc:.2f} seconds")

    # --- PDF Generation (CPU-Bound) ---
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font("Arial", size=12)

    with open(return_file_path, 'r') as file:
        text_content = file.read()
    lines = text_content.split('\n')
    try:
        if use_super_underlying and super_underlying_result:
            sbuy, ssell, sret = super_underlying_result
            insert_line = f"Super underlying return: {sret:.2f}% (Buy {sbuy} Sell {ssell})"
            new_lines = []
            inserted = False
            for ln in lines:
                new_lines.append(ln)
                if (not inserted) and ln.strip().lower().startswith("underlying stock return is"):
                    new_lines.append(insert_line)
                    inserted = True
            if not inserted:
                new_lines.append("")
                new_lines.append(insert_line)
            lines = new_lines
    except NameError:
        pass

    pdf.add_page()
    for line in lines:
        if len(line) > 100:
            wrapped_lines = textwrap.wrap(line, width=100)
            for wrapped_line in wrapped_lines:
                pdf.cell(0, 10, wrapped_line, ln=True)
        else:
            pdf.cell(0, 10, line, ln=True)

    if figs: 
        pdf.add_page()
        img = OUTPUT_DIR + f"stock_price.png"
        try:
            figs[0].savefig(img)
            pdf.image(img, x=10, y=10, w=180)
        except Exception as e:
            print(f"Error saving stock price fig: {e}")

    for i in range(1, len(figs), 2):
        pdf.add_page()
        image_path_1 = OUTPUT_DIR + f"figure_{i}.png"
        try:
            figs[i].savefig(image_path_1)
            pdf.image(image_path_1, x=10, y=10, w=180)
        except Exception as e:
            print(f"Error saving fig {i}: {e}")
        if i + 1 < len(figs):
            try:
                image_path_2 = OUTPUT_DIR + f"figure_{i+1}.png"
                figs[i + 1].savefig(image_path_2)
                pdf.image(image_path_2, x=10, y=150, w=180)
            except Exception as e:
                print(f"Error saving fig {i+1}: {e}")

    if (output == True):
        pdf.output(output_pdf_path)
    else:
        return pdf

    print(f"PDF file '{output_pdf_path}' has been created successfully.")
    print(f"TOTAL runtime: {time.perf_counter() - t0:.2f} seconds")

if __name__ == "__main__":
    try:
        main()
    except ImportError as e:
        print(f"FATAL ERROR: {e}")
        print("This script requires 'cupy' and 'cudf'. Please install them.")
        print("It also requires 'finance_gpu.py' to be in the library path.")
    except Exception as e:
        print(f"An unexpected error occurred in main: {e}")