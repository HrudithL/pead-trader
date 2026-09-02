"""
Strategy 8, Tier 2: replace the single hand-built SUE-rank tilt with a learned cross-sectional
ranking model, trained WALK-FORWARD so every prediction is made only from data strictly before
its own announcement quarter -- the same discipline options_strategy/48_optimal_contract_selector.py
documents for its own walk-forward Kelly selector, applied here to the equity side.

The model is a small multi-layer perceptron (one hidden layer, tanh activation) trained with
hand-rolled Adam gradient descent written directly against the `xp` array backend (numpy on CPU,
cupy on GPU) -- deliberately NOT torch, so this stage needs nothing beyond what
requirements.txt/requirements-gpu.txt already ship (cupy is already required for the
options-strategy GPU pipeline) and so --device cpu --mock-data works on this dev machine with no
extra install. Every array op (matmul, elementwise) is exactly the kind of dense computation that
scales from this CPU box to the 5090 with zero code changes.

Where the extra compute actually goes: this is a walk-forward EXPANDING window, not one fit --
one full retrain per calendar year from `--warmup-years` years in (need enough history to train
on before trusting a model at all) through the end of the sample. Each later fold trains on more
data than the last, and the whole loop is one more knob (`--n-hidden`, `--n-epochs`) that turns a
few-minute CPU run into a genuinely GPU-bound one at full scale -- unlike a single hand-tuned
SUE-rank formula, this is compute you can always buy more signal with by training longer/bigger.

Features: sue_rank_pct, size_quintile, bm_tercile, liquidity_quintile, ear, momentum_12_1,
vol_60d, and a one-hot FF12 sector -- everything 32_gpu_feature_panel.py built, standardized using
ONLY that fold's training-window mean/std (never the test fold's, and never the full sample's --
the walk-forward point). Target: fwd_realized_ret (each event's own realized market-adjusted
return over its holding window) -- a genuine regression on real historical outcomes, not a proxy.

Training rows are also purged by LABEL availability, not just announcement year: a prior-year
event whose holding-period label (fwd_realized_ret, realized only once its position actually
exits) wouldn't be known yet as of the test fold's start is excluded from that fold's training
set -- splitting on announcement year alone would otherwise leak January/February test-year
returns into every fold's training data, since 20-60-day holding windows routinely cross the
year boundary.

Each fold's predictions are checkpointed to
data/equity_walkforward_folds/<config_hash>/fold_<year>.parquet immediately after that fold
trains, and a fold whose checkpoint already exists is skipped on re-run (same crash/reboot-safe
convention as scripts 35/36/41's per-year output files) -- so an interrupted multi-year
walk-forward run resumes instead of restarting. The checkpoint directory is keyed by a hash of
this run's config (mock_data, n_hidden, n_epochs, lr, l2, warmup_years, min_train_events) so a
mock/smoke run's folds are never silently reused by a later real run with different
hyperparameters; --force retrains every fold regardless.

Output: data/equity_ml_signal.parquet (event_id, fold_year, ml_score_raw, ml_rank_pct -- the
cross-sectional percentile rank of ml_score_raw within its own ann_quarter, so it's on the same
[0,1] scale as sue_rank_pct for 34_gpu_param_sweep.py to blend the two), and
data/equity_walkforward_summary.json (per-fold information coefficient + train/test counts).
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.paths import DATA_DIR
from lib.gpu import get_backend, to_host, add_device_arg, add_mock_data_arg, add_smoke_test_arg, \
    StageTimer, make_mock_feature_panel, mark_mock_output

DATA = DATA_DIR
FOLDS_ROOT = DATA / "equity_walkforward_folds"
FEATURE_COLS_NUM = ["sue_rank_pct", "size_quintile", "bm_tercile", "liquidity_quintile",
                     "ear", "momentum_12_1", "vol_60d"]


# ---------------------------------------------------------------------------
# Hand-rolled MLP + Adam, written directly against the xp backend
# ---------------------------------------------------------------------------

def init_mlp(rng, n_features, n_hidden):
    limit1 = np.sqrt(6.0 / (n_features + n_hidden))
    limit2 = np.sqrt(6.0 / (n_hidden + 1))
    return {
        "W1": rng.uniform(-limit1, limit1, size=(n_features, n_hidden)),
        "b1": np.zeros(n_hidden),
        "W2": rng.uniform(-limit2, limit2, size=(n_hidden,)),
        "b2": np.zeros(()),
    }


def forward(xp, params, X):
    Z1 = X @ params["W1"] + params["b1"]
    A1 = xp.tanh(Z1)
    pred = A1 @ params["W2"] + params["b2"]
    return pred, A1


def train_mlp(xp, X_np, y_np, n_hidden, n_epochs, lr, l2, seed):
    """Full-batch Adam on the whole fold at once -- for a real fold (thousands to tens of
    thousands of events) this is a dense (n, d) x (d, h) matmul per epoch, the same family of
    operation cuBLAS is built for; batching it further would only matter past millions of rows."""
    rng = np.random.default_rng(seed)
    n, d = X_np.shape
    params = {k: xp.asarray(v) for k, v in init_mlp(rng, d, n_hidden).items()}
    m = {k: xp.zeros_like(v) for k, v in params.items()}
    v = {k: xp.zeros_like(v) for k, v in params.items()}
    beta1, beta2, eps = 0.9, 0.999, 1e-8
    X, y = xp.asarray(X_np), xp.asarray(y_np)

    loss = None
    for t in range(1, n_epochs + 1):
        pred, A1 = forward(xp, params, X)
        err = pred - y
        loss = xp.mean(err ** 2) + l2 * ((params["W1"] ** 2).sum() + (params["W2"] ** 2).sum())

        grad_pred = 2.0 * err / n
        gW2 = A1.T @ grad_pred + 2 * l2 * params["W2"]
        gb2 = grad_pred.sum()
        dA1 = xp.outer(grad_pred, params["W2"])
        dZ1 = dA1 * (1 - A1 ** 2)
        gW1 = X.T @ dZ1 + 2 * l2 * params["W1"]
        gb1 = dZ1.sum(axis=0)
        grads = {"W1": gW1, "b1": gb1, "W2": gW2, "b2": gb2}

        for k in params:
            m[k] = beta1 * m[k] + (1 - beta1) * grads[k]
            v[k] = beta2 * v[k] + (1 - beta2) * (grads[k] ** 2)
            mhat = m[k] / (1 - beta1 ** t)
            vhat = v[k] / (1 - beta2 ** t)
            params[k] = params[k] - lr * mhat / (xp.sqrt(vhat) + eps)

    return params, float(to_host(xp, loss))


def predict_mlp(xp, params, X_np):
    pred, _ = forward(xp, params, xp.asarray(X_np))
    return to_host(xp, pred)


# ---------------------------------------------------------------------------
# Design matrix + walk-forward loop
# ---------------------------------------------------------------------------

def build_design_matrix(df, sector_categories, mean, std):
    X_num = (df[FEATURE_COLS_NUM].to_numpy(dtype=float) - mean) / std
    sector_onehot = pd.get_dummies(df["ff12_sector"]).reindex(columns=sector_categories, fill_value=0)
    return np.concatenate([X_num, sector_onehot.to_numpy(dtype=float)], axis=1)


def spearman_ic(a, b):
    if len(a) < 5:
        return float("nan")
    ra, rb = pd.Series(a).rank(), pd.Series(b).rank()
    return float(np.corrcoef(ra, rb)[0, 1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_device_arg(parser)
    add_mock_data_arg(parser)
    add_smoke_test_arg(parser)
    parser.add_argument("--warmup-years", type=int, default=3,
                         help="years of history required before the first walk-forward fold "
                              "trains -- events in the warmup window get no ml_score (there is "
                              "no leak-free model to score them with yet). Default: %(default)s")
    parser.add_argument("--n-hidden", type=int, default=32)
    parser.add_argument("--n-epochs", type=int, default=300)
    parser.add_argument("--lr", type=float, default=0.01)
    parser.add_argument("--l2", type=float, default=1e-4)
    parser.add_argument("--min-train-events", type=int, default=200,
                         help="skip a fold if fewer than this many training events are available")
    parser.add_argument("--force", action="store_true",
                         help="retrain every fold even if a checkpoint already exists for this "
                              "exact run configuration")
    args = parser.parse_args()
    xp, device = get_backend(args.device)

    n_hidden = 8 if args.smoke_test else args.n_hidden
    n_epochs = 20 if args.smoke_test else args.n_epochs
    warmup_years = 2 if args.smoke_test else args.warmup_years
    min_train = 20 if args.smoke_test else args.min_train_events

    # per-fold checkpoints are keyed by this run's actual config, not just fold_year -- otherwise
    # a mock/smoke run's fold_<year>.parquet would be silently reused by a later real run (or one
    # with different --n-hidden/--n-epochs/etc.), corrupting equity_ml_signal.parquet with
    # predictions from a different model than the one this run reports.
    config_key = dict(mock_data=args.mock_data, n_hidden=n_hidden, n_epochs=n_epochs,
                       lr=args.lr, l2=args.l2, warmup_years=warmup_years, min_train=min_train)
    config_hash = hashlib.sha1(json.dumps(config_key, sort_keys=True).encode()).hexdigest()[:10]
    FOLDS_DIR = FOLDS_ROOT / config_hash

    with StageTimer("33_gpu_walkforward_signal", extra={"device": device, "mock_data": args.mock_data}):
        if args.mock_data:
            n_events = 300 if args.smoke_test else 4_000
            n_permnos = 40 if args.smoke_test else 200
            print(f"building mock feature panel ({n_permnos} permnos, {n_events} events)...")
            df = make_mock_feature_panel(n_events=n_events, n_permnos=n_permnos, seed=0)
        else:
            print("loading data/equity_feature_panel.parquet...")
            df = pd.read_parquet(DATA / "equity_feature_panel.parquet")

        df["day0_date"] = pd.to_datetime(df["day0_date"])
        df["exit_date"] = pd.to_datetime(df["exit_date"])
        before = len(df)
        df = df.dropna(subset=FEATURE_COLS_NUM + ["fwd_realized_ret"]).reset_index(drop=True)
        print(f"dropped {before - len(df):,} / {before:,} events missing a feature or label "
              f"(mostly insufficient trailing history for momentum_12_1)")
        df["year"] = df["day0_date"].dt.year
        sector_categories = sorted(df["ff12_sector"].unique())

        years = sorted(df["year"].unique())
        fold_years = [y for y in years if y >= years[0] + warmup_years]
        print(f"walk-forward folds: {fold_years[0] if fold_years else 'none'}-"
              f"{fold_years[-1] if fold_years else 'none'} ({len(fold_years)} folds), "
              f"backend={xp.__name__} (device={device})")

        FOLDS_DIR.mkdir(parents=True, exist_ok=True)
        fold_summaries = []
        for fold_year in fold_years:
            fold_path = FOLDS_DIR / f"fold_{fold_year}.parquet"
            if fold_path.exists() and not args.force:
                print(f"[fold {fold_year}] already done, skipping")
                fold_df = pd.read_parquet(fold_path)
                fold_summaries.append(dict(fold_year=fold_year, n_train=None, n_test=len(fold_df),
                                            ic=None, loss=None, skipped_resume=True))
                continue

            # embargo, not just a year cutoff: a training event whose holding-period label
            # (fwd_realized_ret) wasn't yet realized as of this fold's test-year start would leak
            # test-period information into training, since 20-60-day holding windows routinely
            # cross the year boundary.
            fold_start = pd.Timestamp(year=fold_year, month=1, day=1)
            train_df = df[(df["year"] < fold_year) & (df["exit_date"] < fold_start)]
            test_df = df[df["year"] == fold_year]
            if len(train_df) < min_train or len(test_df) == 0:
                print(f"[fold {fold_year}] skipped: n_train={len(train_df)}, n_test={len(test_df)} "
                      f"(need >= {min_train} train events and >= 1 test event)")
                continue

            mean = train_df[FEATURE_COLS_NUM].to_numpy(dtype=float).mean(axis=0)
            std = train_df[FEATURE_COLS_NUM].to_numpy(dtype=float).std(axis=0)
            std[std == 0] = 1.0

            X_train = build_design_matrix(train_df, sector_categories, mean, std)
            y_train = train_df["fwd_realized_ret"].to_numpy(dtype=float)
            X_test = build_design_matrix(test_df, sector_categories, mean, std)

            params, loss = train_mlp(xp, X_train, y_train, n_hidden, n_epochs, args.lr, args.l2,
                                      seed=fold_year)
            ml_score_raw = predict_mlp(xp, params, X_test)
            ic = spearman_ic(ml_score_raw, test_df["fwd_realized_ret"].to_numpy())

            fold_df = pd.DataFrame({
                "event_id": test_df["event_id"].to_numpy(), "ann_quarter": test_df["ann_quarter"].to_numpy(),
                "fold_year": fold_year, "ml_score_raw": ml_score_raw,
            })
            fold_df.to_parquet(fold_path, index=False)
            print(f"[fold {fold_year}] n_train={len(train_df):,} n_test={len(test_df):,} "
                  f"train_loss={loss:.5f} test_IC={ic:.4f}")
            fold_summaries.append(dict(fold_year=fold_year, n_train=len(train_df), n_test=len(test_df),
                                        ic=ic, loss=loss, skipped_resume=False))

        all_folds = sorted(FOLDS_DIR.glob("fold_*.parquet"))
        if not all_folds:
            raise RuntimeError("no walk-forward folds produced any predictions -- check "
                                "--warmup-years / --min-train-events against the data available")
        signal = pd.concat([pd.read_parquet(p) for p in all_folds], ignore_index=True)
        signal["ml_rank_pct"] = signal.groupby("ann_quarter")["ml_score_raw"].rank(pct=True)
        signal_path = DATA / "equity_ml_signal.parquet"
        signal.to_parquet(signal_path, index=False)
        mark_mock_output(signal_path, is_mock=args.mock_data)
        print(f"wrote {signal_path} ({len(signal):,} scored events across {len(all_folds)} folds)")

        real_ics = [f["ic"] for f in fold_summaries if f["ic"] is not None and not np.isnan(f["ic"])]
        summary = dict(device=device, mock_data=args.mock_data, n_hidden=n_hidden, n_epochs=n_epochs,
                        warmup_years=warmup_years, config_hash=config_hash, n_folds=len(fold_summaries),
                        mean_test_ic=float(np.mean(real_ics)) if real_ics else None,
                        folds=fold_summaries)
        with open(DATA / "equity_walkforward_summary.json", "w") as f:
            json.dump(summary, f, indent=2, default=str)
        print(f"mean out-of-sample IC across {len(real_ics)} freshly-trained folds: "
              f"{summary['mean_test_ic']}")


if __name__ == "__main__":
    main()
