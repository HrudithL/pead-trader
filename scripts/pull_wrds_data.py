"""
One-time WRDS bulk pull for pead-trading.

Pulls I/B/E/S (analyst estimates + actuals), CRSP (returns, market benchmark,
delistings, identifiers), and Compustat (fundamentals, lower priority) for
1996-01-01 through 2013-12-31 -- the OptionMetrics archive's native window on
the external drive, so the earnings/estimates side and the options side line
up on the same period for the first time.

Deliberately does NOT check for or pull OptionMetrics/options data. This WRDS
account does not have that access, and options data comes from the external
drive archive instead. Do not add an optionm check here without confirming
that's actually changed.

This is meant to run ONCE. Every table lands under data/raw_wrds/<library>/ as
Parquet, gitignored, so this should not need to run again except for a
deliberate, explicit refresh -- per the standing "minimize WRDS queries" rule.

Column lists are checked against the account's actual schema via
db.describe_table() before each query, rather than assumed blind, since exact
column/table names can vary slightly by WRDS account vintage. Any table or
column that doesn't match is logged and skipped rather than crashing the run,
so one wrong name doesn't take down an otherwise-successful pull.

Usage:
    python scripts/pull_wrds_data.py
Requires WRDS_USERNAME and WRDS_PASSWORD in a .env file at the repo root.
"""

import builtins
import os
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw_wrds"
METADATA_DIR = REPO_ROOT / "data" / "metadata"

# Matches the OptionMetrics archive's native coverage (verified in
# memory/pead-trading-spec.md section 6b: 1996-2013, not 1997-2013).
START_DATE = "1996-01-01"
END_DATE = "2013-12-31"
# One year of lookback so rolling-window features (volatility, momentum) have
# history even for events near the start of the sample.
LOOKBACK_START_DATE = "1995-01-01"


def load_env_file(path: Path) -> None:
    """Minimal .env loader. Avoids adding python-dotenv as a dependency for this."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    import wrds

    # Avoid hanging on the interactive "create a .pgpass file?" prompt -- we
    # keep credentials in .env only and never write them to disk in plaintext.
    builtins.input = lambda *a, **k: "n"

    username = os.environ["WRDS_USERNAME"]
    password = os.environ["WRDS_PASSWORD"]
    print(f"Connecting to WRDS as {username} ...", flush=True)
    # 60s is a safety net, not an accommodation for a live Duo push. If this
    # account's 30-day MFA grace period (per WRDS docs) has lapsed for this
    # network, re-authenticate via the WRDS website or SSH first.
    db = wrds.Connection(
        wrds_username=username,
        wrds_password=password,
        wrds_connect_args={"connect_timeout": 60},
    )
    print("Connected.", flush=True)
    return db


def pull_table(db, log, library, table, desired_columns, where_sql="", out_name=None):
    """Check real columns first, select only what exists, save to Parquet, log the outcome."""
    out_name = out_name or table
    out_dir = RAW_DIR / library
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{out_name}.parquet"

    try:
        schema = db.describe_table(library, table)
        actual_columns = {c.lower() for c in schema["name"]}
    except Exception as e:
        print(f"  {library}.{table}: table not found ({e})", flush=True)
        log.append(
            {"library": library, "table": table, "status": "no_such_table",
             "rows": 0, "path": "", "missing_columns": "", "error": str(e)}
        )
        return

    cols_to_select = [c for c in desired_columns if c.lower() in actual_columns]
    missing = [c for c in desired_columns if c.lower() not in actual_columns]
    if missing:
        print(f"  {library}.{table}: columns not present, skipping: {missing}", flush=True)
    if not cols_to_select:
        log.append(
            {"library": library, "table": table, "status": "no_matching_columns",
             "rows": 0, "path": "", "missing_columns": ",".join(missing),
             "error": f"none of {desired_columns} found"}
        )
        return

    sql = f"SELECT {', '.join(cols_to_select)} FROM {library}.{table}"
    if where_sql:
        sql += f" WHERE {where_sql}"

    try:
        print(f"Pulling {library}.{table} -> "
              f"{out_path.relative_to(REPO_ROOT)} ...", flush=True)
        df = db.raw_sql(sql)
        df.to_parquet(out_path, index=False)
        print(f"  {len(df):,} rows.", flush=True)
        log.append(
            {"library": library, "table": table, "status": "ok", "rows": len(df),
             "path": str(out_path.relative_to(REPO_ROOT)),
             "missing_columns": ",".join(missing), "error": ""}
        )
    except Exception as e:
        print(f"  FAILED: {e}", flush=True)
        log.append(
            {"library": library, "table": table, "status": "query_failed",
             "rows": 0, "path": "", "missing_columns": ",".join(missing), "error": str(e)}
        )


def main():
    load_env_file(REPO_ROOT / ".env")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_DIR.mkdir(parents=True, exist_ok=True)

    db = connect()
    log = []

    try:
        # --- One-time library audit ---
        libraries = sorted(db.list_libraries())
        (METADATA_DIR / "wrds_available_libraries.txt").write_text(
            "\n".join(libraries) + "\n"
        )
        print(
            f"{len(libraries)} libraries visible. "
            f"Saved to data/metadata/wrds_available_libraries.txt",
            flush=True,
        )

        have_ibes = "ibes" in libraries
        have_crsp = "crsp" in libraries
        comp_candidates = [lib for lib in ("comp", "compa", "compustat") if lib in libraries]
        have_comp = bool(comp_candidates)
        comp_lib = comp_candidates[0] if have_comp else None
        # Deliberately not checking for optionm/OptionMetrics here -- see module
        # docstring. Options data comes from the external drive archive.

        # --- I/B/E/S ---
        if have_ibes:
            pull_table(
                db, log, "ibes", "statsumu_epsus",
                ["ticker", "cusip", "oftic", "cname", "statpers", "measure",
                 "fiscalp", "fpi", "curcode", "usfirm", "fpedats",
                 "numest", "numup", "numdown", "medest", "meanest",
                 "stdev", "highest", "lowest"],
                where_sql=f"statpers BETWEEN '{START_DATE}' AND '{END_DATE}'",
            )
            pull_table(
                db, log, "ibes", "actu_epsus",
                ["ticker", "cusip", "oftic", "cname", "pends", "anndats",
                 "anntims", "value", "curr_act", "measure"],
                where_sql=f"anndats BETWEEN '{START_DATE}' AND '{END_DATE}'",
            )
            pull_table(
                db, log, "ibes", "idsum",
                ["ticker", "cusip", "cname", "oftic", "sdates"],
            )
            # Analyst-level detail: STDEV/NUMEST from the summary file already cover
            # dispersion and analyst count for most purposes, but this is a one-shot
            # pull, so grab it now rather than come back for it later if a future
            # slice wants revision-level detail.
            pull_table(
                db, log, "ibes", "det_epsus",
                ["ticker", "cusip", "estimator", "analys", "fpedats",
                 "revdats", "revtims", "value", "measure"],
                where_sql=f"revdats BETWEEN '{START_DATE}' AND '{END_DATE}'",
            )
        else:
            print("ibes library not visible on this account -- skipping.", flush=True)
            log.append(
                {"library": "ibes", "table": "*", "status": "library_unavailable",
                 "rows": 0, "path": "", "missing_columns": "", "error": ""}
            )

        # --- CRSP ---
        if have_crsp:
            pull_table(
                db, log, "crsp", "dsf",
                ["permno", "date", "ret", "retx", "prc", "vol", "shrout",
                 "cfacpr", "cfacshr"],
                where_sql=f"date BETWEEN '{LOOKBACK_START_DATE}' AND '{END_DATE}'",
            )
            pull_table(
                db, log, "crsp", "dsi",
                ["date", "vwretd", "vwretx", "ewretd", "ewretx", "sprtrn"],
                where_sql=f"date BETWEEN '{LOOKBACK_START_DATE}' AND '{END_DATE}'",
            )
            pull_table(
                db, log, "crsp", "dsedelist",
                ["permno", "dlstdt", "dlret", "dlstcd"],
                where_sql=f"dlstdt BETWEEN '{LOOKBACK_START_DATE}' AND '{END_DATE}'",
            )
            pull_table(
                db, log, "crsp", "stocknames",
                ["permno", "permco", "namedt", "nameenddt", "ticker",
                 "comnam", "cusip", "shrcd", "exchcd"],
            )
        else:
            print("crsp library not visible on this account -- skipping.", flush=True)
            log.append(
                {"library": "crsp", "table": "*", "status": "library_unavailable",
                 "rows": 0, "path": "", "missing_columns": "", "error": ""}
            )

        # --- Compustat (lower priority) ---
        if have_comp:
            pull_table(
                db, log, comp_lib, "fundq",
                ["gvkey", "datadate", "fyearq", "fqtr", "atq", "ceqq",
                 "saleq", "niq", "epspxq"],
                where_sql=f"datadate BETWEEN '{LOOKBACK_START_DATE}' AND '{END_DATE}'",
            )
            pull_table(
                db, log, comp_lib, "company",
                ["gvkey", "conm", "sic", "gsector"],
            )
            # Annual fundamentals alongside quarterly -- book-to-market and
            # other annual-basis controls aren't in fundq, and this is a
            # one-shot pull, so grab it now.
            pull_table(
                db, log, comp_lib, "funda",
                ["gvkey", "datadate", "fyear", "at", "ceq", "sale", "ni", "epspx"],
                where_sql=f"datadate BETWEEN '{LOOKBACK_START_DATE}' AND '{END_DATE}'",
            )
        else:
            print("Compustat library not visible on this account -- skipping.", flush=True)
            log.append(
                {"library": "comp", "table": "*", "status": "library_unavailable",
                 "rows": 0, "path": "", "missing_columns": "", "error": ""}
            )

        # --- Linking tables (best-effort; exact names vary by account, so try
        # a couple of known candidates -- a failed attempt just logs and moves
        # on, and this is a one-shot pull so it's worth trying more than one) ---
        if have_ibes and have_crsp:
            pull_table(
                db, log, "wrdsapps", "ibcrsphist",
                ["ticker", "permno", "ncusip", "score", "sdate", "edate"],
            )
            pull_table(
                db, log, "wrdsapps", "iclink",
                ["ticker", "permno", "cusip", "score", "sdate", "edate"],
            )
        if have_crsp and have_comp:
            pull_table(
                db, log, "crsp", "ccmxpf_lnkhist",
                ["gvkey", "lpermno", "linktype", "linkprim", "linkdt", "linkenddt"],
            )
    finally:
        db.close()
        print("Connection closed.", flush=True)

    # --- Manifest ---
    manifest = pd.DataFrame(log)
    manifest_path = METADATA_DIR / "wrds_pull_manifest.csv"
    manifest.to_csv(manifest_path, index=False)
    print(f"\nManifest written to data/metadata/wrds_pull_manifest.csv")
    print(manifest.to_string(index=False))


if __name__ == "__main__":
    main()
