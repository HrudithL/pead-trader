"""Parse official Kenneth French daily factor ZIPs without republishing source rows."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import Path

import pandas as pd


FF3_URL = ("https://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/ftp/"
           "F-F_Research_Data_Factors_daily_CSV.zip")
MOM_URL = ("https://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/ftp/"
           "F-F_Momentum_Factor_daily_CSV.zip")


def _read_daily(path: Path, names: list[str]) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        raw = z.read(z.namelist()[0]).decode("latin1")
    lines = [line for line in raw.splitlines() if re.match(r"^\s*\d{8}\s*,", line)]
    table = pd.read_csv(io.StringIO("\n".join(lines)), header=None,
                        names=["yyyymmdd", *names], skipinitialspace=True)
    table["date"] = pd.to_datetime(table.pop("yyyymmdd").astype(str), format="%Y%m%d")
    for col in names:
        table[col] = pd.to_numeric(table[col], errors="coerce") / 100.0
        table.loc[table[col] < -0.9, col] = float("nan")
    return table


def load_french_daily(directory: Path) -> tuple[pd.DataFrame, dict]:
    ff_path = directory / "F-F_Research_Data_Factors_daily_CSV.zip"
    mom_path = directory / "F-F_Momentum_Factor_daily_CSV.zip"
    ff = _read_daily(ff_path, ["Mkt-RF", "SMB", "HML", "RF"])
    mom = _read_daily(mom_path, ["Mom"])
    merged = ff.merge(mom, on="date", how="inner", validate="one_to_one")
    provenance = {
        "ff3_url": FF3_URL, "momentum_url": MOM_URL,
        "ff3_sha256": hashlib.sha256(ff_path.read_bytes()).hexdigest(),
        "momentum_sha256": hashlib.sha256(mom_path.read_bytes()).hexdigest(),
        "factor_zip_directory": str(directory),
    }
    return merged, provenance
