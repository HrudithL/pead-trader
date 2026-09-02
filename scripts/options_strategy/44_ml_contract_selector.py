"""
Tier 3, part 2: can a model beat "which decile is this event in" as the entry signal, using
features the decile-sort approach throws away -- IV level, liquidity (volume/open interest),
moneyness (delta), days-to-expiry, sector, size? All of these already exist in
option_event_panel.parquet (script 35 kept `iv`, `entry_volume`, `entry_oi`, `delta`, `dte` per
selected contract) but neither script 40's backtest nor script 43's sweep use anything except
`decile` and `ff12_sector`/`size_quintile` for neutralization.

This is a much smaller, more tractable model than the one already sitting unused in
`Options_Content/MSTR_Options_Storage/panel_model.py` (a cross-attention transformer over K other
contracts per timestep -- built for the Tradier live-snapshot data, which has that panel
structure; the OptionMetrics event panel here is one row per earnings event, not a timestep
series, so there's no "K other contracts at this timestep" to attend over). What IS reused from
that file is the training/search *pattern*: an Optuna study with a MedianPruner, sweeping both
data-representation and model hyperparameters together, training with early stopping on a held-out
validation split -- same idea, applied to a plain feed-forward regressor since a simple per-event
feature vector doesn't need attention or a sequence encoder.

Target: `{call,put}_ret_fwd_60d` (whichever side script 40's decile-tilt sign selects for that
event) -- the same 60-day horizon used as the headline number in PEAD_Options_Report.pdf. Held-out
evaluation is by ANNOUNCEMENT QUARTER (not a random row split), since events in the same quarter
share the same earnings "season" and macro/vol regime -- a random split would leak information
across same-quarter events the way every other quarter-clustered analysis in this project already
guards against (see script 02's Fama-MacBeth rationale).

Output: data/ml_contract_selector_summary.json -- best trial's val RMSE / IC vs. decile-only baseline
        data/ml_contract_selector_study.csv     -- every Optuna trial's params + val score
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.paths import DATA_DIR
from lib.gpu import add_mock_data_arg, add_smoke_test_arg, make_mock_option_event_panel, StageTimer
from lib.hw import recommended_cpu_jobs, cpu_count

DATA = DATA_DIR
TARGET_HORIZON = 60
FEATURE_COLS_NUMERIC = ["decile_signed", "size_quintile", "iv", "dte", "delta",
                         "entry_volume_log", "entry_oi_log"]


def load_panel(args):
    if args.mock_data:
        panel = make_mock_option_event_panel(n_events=args.mock_n_events)
        # mock panel doesn't carry iv/dte/delta/volume/oi (script 35-only columns) -- synthesize
        # plausible values so this script's feature pipeline and model are still fully exercised.
        rng = np.random.default_rng(1)
        n = len(panel)
        for cp in ["call", "put"]:
            panel[f"{cp}_iv"] = rng.uniform(0.25, 0.90, size=n)
            panel[f"{cp}_dte"] = rng.uniform(95, 140, size=n)
            panel[f"{cp}_delta"] = rng.uniform(0.40, 0.60, size=n) * (1 if cp == "call" else -1)
            panel[f"{cp}_entry_volume"] = rng.integers(1, 500, size=n)
            panel[f"{cp}_entry_oi"] = rng.integers(1, 5000, size=n)
    else:
        panel_path = DATA / "event_options" / "option_event_panel.parquet"
        if not panel_path.exists():
            raise FileNotFoundError(f"{panel_path} not found; run scripts 33-36 or pass --mock-data.")
        panel = pd.read_parquet(panel_path)
        panel = panel[panel["decile"].notna()].reset_index(drop=True)
        panel["decile"] = panel["decile"].astype(int)
    return panel


def build_features(panel: pd.DataFrame):
    """Select side (call/put) exactly as script 40 does (decile sign), then assemble the
    side-appropriate feature vector + target for every event with a valid target return."""
    d = (panel["decile"].to_numpy() - 5.5) / 4.5
    is_call = d > 0
    side_cp = np.where(is_call, "call", "put")

    def pick(col_suffix):
        call_col, put_col = f"call_{col_suffix}", f"put_{col_suffix}"
        if call_col not in panel.columns or put_col not in panel.columns:
            return np.full(len(panel), np.nan)
        return np.where(is_call, panel[call_col].to_numpy(), panel[put_col].to_numpy())

    target = pick(f"ret_fwd_{TARGET_HORIZON}d")
    feat = pd.DataFrame({
        "decile_signed": d,
        "size_quintile": panel["size_quintile"].to_numpy(),
        "iv": pick("iv"),
        "dte": pick("dte"),
        "delta": np.abs(pick("delta")),
        "entry_volume_log": np.log1p(np.abs(pick("entry_volume"))),
        "entry_oi_log": np.log1p(np.abs(pick("entry_oi"))),
    })
    feat["target"] = target
    feat["ann_quarter"] = panel["ann_quarter"].astype(str).to_numpy()
    feat = feat.dropna()
    return feat


def train_eval_mlp(torch, train_df, val_df, hidden_dim, n_layers, dropout, lr, epochs, device):
    nn = torch.nn
    x_cols = FEATURE_COLS_NUMERIC
    mu, sigma = train_df[x_cols].mean(), train_df[x_cols].std().replace(0, 1.0)

    def to_tensors(df):
        x = ((df[x_cols] - mu) / sigma).to_numpy(dtype=np.float32)
        y = df["target"].to_numpy(dtype=np.float32)
        return torch.tensor(x, device=device), torch.tensor(y, device=device)

    x_tr, y_tr = to_tensors(train_df)
    x_val, y_val = to_tensors(val_df)

    layers = []
    in_dim = len(x_cols)
    for _ in range(n_layers):
        layers += [nn.Linear(in_dim, hidden_dim), nn.ReLU(), nn.Dropout(dropout)]
        in_dim = hidden_dim
    layers += [nn.Linear(in_dim, 1)]
    model = nn.Sequential(*layers).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()

    best_val = float("inf")
    for _ in range(epochs):
        model.train()
        opt.zero_grad()
        pred = model(x_tr).squeeze(-1)
        loss = loss_fn(pred, y_tr)
        loss.backward()
        opt.step()
        model.eval()
        with torch.no_grad():
            val_pred = model(x_val).squeeze(-1)
            val_rmse = loss_fn(val_pred, y_val).sqrt().item()
        best_val = min(best_val, val_rmse)
    return best_val


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_mock_data_arg(parser)
    add_smoke_test_arg(parser)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu",
                         help="torch device (separate from gpu_lib's numpy/cupy backend -- this "
                              "script trains a PyTorch model, not a cupy array kernel)")
    parser.add_argument("--n-trials", type=int, default=20)
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--n-jobs", type=int, default=None,
                         help="Optuna trials run concurrently (threads -- each trial releases "
                              "the GIL during actual tensor ops, so this is a real speedup on a "
                              "multi-core CPU). Only used when --device cpu: on GPU, concurrent "
                              "trials would contend for the same VRAM, so this is forced to 1 "
                              "there regardless of what's passed. Default: all CPU cores "
                              f"({recommended_cpu_jobs()} on this machine) when --device cpu, "
                              "else 1.")
    args = parser.parse_args()

    n_trials, epochs = args.n_trials, args.epochs
    if args.smoke_test:
        n_trials, epochs = 2, 10
        args.mock_n_events = min(args.mock_n_events, 3_000)
        print(f"--smoke-test: shrunk to n_trials={n_trials}, epochs={epochs}, "
              f"mock_n_events={args.mock_n_events}")

    with StageTimer("44_ml_contract_selector", extra={"device": args.device, "mock_data": args.mock_data}):
        try:
            import torch
            import optuna
        except ImportError as e:
            raise RuntimeError(
                "44_ml_contract_selector.py needs torch + optuna (pip install -r "
                "requirements-gpu.txt). Not available here.") from e
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        panel = load_panel(args)
        feat = build_features(panel)
        print(f"panel: {len(panel):,} events -> {len(feat):,} with a valid "
              f"{TARGET_HORIZON}d target + full feature set")

        quarters = sorted(feat["ann_quarter"].unique())
        split_at = quarters[int(len(quarters) * 0.8)]
        train_df = feat[feat["ann_quarter"] < split_at]
        val_df = feat[feat["ann_quarter"] >= split_at]
        print(f"train: {len(train_df):,} events ({quarters[0]}..{split_at}), "
              f"val: {len(val_df):,} events ({split_at}..{quarters[-1]})")

        baseline_rmse = float(np.sqrt(np.mean((val_df["target"] - train_df["target"].mean()) ** 2)))
        device = args.device if (args.device == "cpu" or torch.cuda.is_available()) else "cpu"
        if args.device == "cuda" and device == "cpu":
            print("WARNING: --device cuda requested but torch sees no GPU, falling back to cpu")

        # concurrent trials only make sense on CPU -- on GPU they'd all fight over the same VRAM
        # and serialize on the device anyway, so force 1 there regardless of --n-jobs.
        n_jobs = args.n_jobs if args.n_jobs is not None else (recommended_cpu_jobs() if device == "cpu" else 1)
        if device == "cuda" and args.n_jobs and args.n_jobs > 1:
            print(f"--n-jobs {args.n_jobs} requested but device=cuda -- forcing n_jobs=1 to avoid "
                  f"concurrent trials contending for the same GPU's VRAM")
            n_jobs = 1
        if device == "cpu" and n_jobs > 1:
            # each of the n_jobs concurrent trials ALSO runs its own multi-threaded torch intra-op
            # pool (torch.get_num_threads() defaults to every core) -- without capping that, n_jobs
            # trials each spinning up a full-core-count thread pool oversubscribes this machine's
            # cores by roughly n_jobs x, thrashing instead of the intended speedup (or OOMing on a
            # many-core box). Give each trial a fair share instead.
            threads_per_trial = max(1, cpu_count() // n_jobs)
            torch.set_num_threads(threads_per_trial)
            print(f"n_jobs={n_jobs} concurrent trials on CPU: capping torch to "
                  f"{threads_per_trial} intra-op thread(s)/trial ({cpu_count()} cores total)")
        print(f"optuna: {n_trials} trials, n_jobs={n_jobs}, device={device}")

        def objective(trial):
            hidden_dim = trial.suggest_categorical("hidden_dim", [16, 32, 64, 128])
            n_layers = trial.suggest_int("n_layers", 1, 3)
            dropout = trial.suggest_float("dropout", 0.0, 0.5)
            lr = trial.suggest_float("lr", 1e-4, 1e-2, log=True)
            return train_eval_mlp(torch, train_df, val_df, hidden_dim, n_layers, dropout, lr,
                                   epochs, device)

        study = optuna.create_study(direction="minimize",
                                     pruner=optuna.pruners.MedianPruner())
        study.optimize(objective, n_trials=n_trials, n_jobs=n_jobs)

        trials_df = study.trials_dataframe()
        trials_df.to_csv(DATA / "ml_contract_selector_study.csv", index=False)

        summary = dict(
            n_trials=n_trials, device=device, target_horizon=TARGET_HORIZON,
            n_train=len(train_df), n_val=len(val_df),
            baseline_rmse_mean_predictor=baseline_rmse,
            best_val_rmse=study.best_value, best_params=study.best_params,
            improvement_vs_baseline_pct=float(
                100 * (baseline_rmse - study.best_value) / baseline_rmse) if baseline_rmse else None,
        )
        with open(DATA / "ml_contract_selector_summary.json", "w") as f:
            json.dump(summary, f, indent=2)
        print(f"\nbaseline (mean predictor) val RMSE: {baseline_rmse:.5f}")
        print(f"best model val RMSE: {study.best_value:.5f} (params: {study.best_params})")
        print(f"wrote {DATA / 'ml_contract_selector_summary.json'} and "
              f"{DATA / 'ml_contract_selector_study.csv'}")


if __name__ == "__main__":
    main()
