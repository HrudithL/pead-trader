#!/usr/bin/env python3
# Parallel Optuna sweep that plays nice with CPU cores and avoids NaNs.
# - Parallel trials via Optuna n_jobs
# - Careful per-trial thread limits to prevent oversubscription
# - Stable dataset building and validation (prune on non-finite)
# - Works on CPU or single GPU (for GPU, set --n_jobs 1)

import argparse, os, json, random, hashlib, multiprocessing as mp
from typing import List, Tuple

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader, Subset

import optuna
from optuna.pruners import MedianPruner

# Your modules
from panel_model import PanelForecastNet
from Datacollection_panel import build_panel_sequences

# --- Safer multiprocessing (esp. with CUDA) ---
try:
    mp.set_start_method("spawn", force=True)
except RuntimeError:
    pass

# --- GPU speedups if on CUDA ---
if torch.cuda.is_available():
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.set_float32_matmul_precision("high")


# ---------------- Utils ----------------
def set_seed(seed: int = 1337):
    random.seed(seed); np.random.seed(seed)
    torch.manual_seed(seed); torch.cuda.manual_seed_all(seed)


def rmse_safe(y_true, y_pred):
    y_true = np.asarray(y_true); y_pred = np.asarray(y_pred)
    m = np.isfinite(y_true) & np.isfinite(y_pred)
    if not m.any(): return float('inf')
    v = float(np.sqrt(np.mean((y_pred[m] - y_true[m])**2)))
    return v if np.isfinite(v) else float('inf')


def shutdown_loader(dl):
    # Clean shutdown even when a trial is pruned mid-epoch
    try:
        it = getattr(dl, "_iterator", None)
        if it is not None:
            it._shutdown_workers()
    except Exception:
        pass


# ---------------- Dataset wrappers ----------------
class NpzPanelDataset(Dataset):
    """Loads panel_dataset .npz produced by build_panel_sequences."""
    def __init__(self, npz_path: str):
        data = np.load(npz_path, allow_pickle=True)
        self.X_main   = data["X_main"]      # [N, L, Fm]
        self.X_others = data["X_others"]    # [N, L, K, Fo]
        self.M_others = data["M_others"]    # [N, L, K]
        self.y        = data["y"]           # [N]
        self.n        = int(self.X_main.shape[0])

    def __len__(self): return self.n

    def __getitem__(self, i):
        xm = torch.from_numpy(self.X_main[i]).float()
        xo = torch.from_numpy(self.X_others[i]).float()
        mo = torch.from_numpy(self.M_others[i]).float()
        y  = torch.tensor(float(self.y[i]), dtype=torch.float32)
        return {"x_main": xm, "x_others": xo, "m_others": mo, "y": y}


def collate(batch):
    x_main   = torch.stack([b["x_main"] for b in batch], 0)
    x_others = torch.stack([b["x_others"] for b in batch], 0)
    m_others = torch.stack([b["m_others"] for b in batch], 0)
    y        = torch.stack([b["y"] for b in batch], 0)
    # sanitize & mask
    x_main   = torch.nan_to_num(x_main,   nan=0.0, posinf=0.0, neginf=0.0)
    x_others = torch.nan_to_num(x_others, nan=0.0, posinf=0.0, neginf=0.0)
    m_others = torch.nan_to_num(m_others, nan=0.0, posinf=0.0, neginf=0.0)
    x_others = x_others * m_others.unsqueeze(-1)
    return {"x_main": x_main, "x_others": x_others, "m_others": m_others, "y": y}


def make_splits(N: int, n_folds: int = 3) -> List[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    splits = []
    # time-based splits: 60–80% train; 10% val; remainder test
    for train_share in np.linspace(0.6, 0.8, num=n_folds):
        train_end = int(train_share * N)
        val_end = min(N - 5, int(train_end + 0.1 * N))
        if train_end < 5 or val_end <= train_end or N - val_end < 5:
            continue
        train_idx = np.arange(0, train_end)
        val_idx   = np.arange(train_end, val_end)
        test_idx  = np.arange(val_end, N)
        splits.append((train_idx, val_idx, test_idx))
    return splits


# ------------- Per-trial dataset build (with cache) -------------
def build_trial_dataset(raw_csv: str, main_strike: float, main_expiration: str, option_type: str,
                        L: int, H: int, K: int, include_bid: bool, include_stock: bool,
                        outdir: str) -> str:
    cfg = {
        "raw_csv": os.path.abspath(raw_csv),
        "strike": float(main_strike),
        "exp": str(main_expiration),
        "type": option_type.lower(),
        "L": int(L), "H": int(H), "K": int(K),
        "incl_bid": bool(include_bid), "incl_stock": bool(include_stock),
        "dedupe_main": True, "dedupe_others": True,
    }
    key = hashlib.md5(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]
    npz_path = os.path.join(outdir, f"panel_L{L}_H{H}_K{K}_{key}.npz")
    if os.path.exists(npz_path):
        return npz_path
    build_panel_sequences(
        path=raw_csv,
        out_npz=npz_path,
        main_strike=main_strike,
        main_expiration=main_expiration,
        option_type=option_type,
        input_length=L, horizon=H, top_k=K,
        include_bid=include_bid, include_stock_price=include_stock,
        dedupe_main=True, dedupe_others=True, verbose=False,
    )
    return npz_path


def maybe_compile(model: nn.Module, enable: bool):
    if not enable: return model
    try:
        return torch.compile(model, mode="reduce-overhead")
    except Exception:
        return model


# ---------------- Optuna objective ----------------
def objective(trial: optuna.Trial, args) -> float:
    device = torch.device(args.device)
    use_cuda = (device.type == "cuda")

    # Respect per-trial CPU thread limits to avoid oversubscription when n_jobs>1
    if args.threads_per_trial > 0:
        torch.set_num_threads(args.threads_per_trial)
        os.environ["OMP_NUM_THREADS"] = str(args.threads_per_trial)
        os.environ["MKL_NUM_THREADS"] = str(args.threads_per_trial)

    # ---- Data knobs (narrow, reasonable) ----
    L = trial.suggest_categorical("input_length", [20, 30, 40, 60])
    H = trial.suggest_categorical("horizon", [3, 5, 10])
    K = trial.suggest_categorical("top_k", [4, 8, 12])

    include_bid   = trial.suggest_categorical("include_bid", [False, True])
    include_stock = trial.suggest_categorical("include_stock_price", [True, False])  # usually helpful

    os.makedirs(args.tmp_datasets, exist_ok=True)
    try:
        npz_path = build_trial_dataset(
            raw_csv=args.raw_csv, main_strike=args.main_strike,
            main_expiration=args.main_expiration, option_type=args.option_type,
            L=L, H=H, K=K, include_bid=include_bid, include_stock=include_stock,
            outdir=args.tmp_datasets
        )
    except Exception as e:
        # Ill-posed windows -> prune quickly
        raise optuna.exceptions.TrialPruned() from e

    ds = NpzPanelDataset(npz_path)
    N_total = len(ds)
    if N_total < 60:  # need enough for train/val/test
        raise optuna.exceptions.TrialPruned()

    splits = make_splits(N_total, n_folds=3)
    if not splits:
        raise optuna.exceptions.TrialPruned()
    # use fewer folds for speed if requested
    splits = splits[:max(1, min(args.n_folds_use, len(splits)))]

    # ---- Model knobs (divisibility enforced) ----
    d_model = trial.suggest_categorical("d_model", [128, 160, 192])
    n_heads = trial.suggest_categorical("n_heads", [4, 5, 8])
    if d_model % n_heads != 0:
        raise optuna.exceptions.TrialPruned()
    n_layers = trial.suggest_int("n_layers", 2, 4)
    ff_mult  = trial.suggest_categorical("ff_mult", [2, 4, 6])
    dropout  = trial.suggest_float("dropout", 0.05, 0.30, step=0.05)
    use_mean_pool = trial.suggest_categorical("use_mean_pool", [False, True])

    # ---- Train knobs (safe ranges) ----
    lr           = trial.suggest_float("lr", 3e-4, 3e-3, log=True)
    weight_decay = trial.suggest_float("weight_decay", 1e-6, 1e-3, log=True)
    batch_size   = trial.suggest_categorical("batch_size", [32, 64, 96])
    patience     = trial.suggest_int("patience", 8, 14)
    grad_clip    = trial.suggest_categorical("grad_clip", [1.0, 1.5, 2.0])

    fold_val_rmses = []
    for fold_idx, (train_idx, val_idx, _) in enumerate(splits, 1):
        ds_train = Subset(ds, train_idx.tolist())
        ds_val   = Subset(ds, val_idx.tolist())

        # DataLoaders—no persistent workers to avoid prune races
        num_workers = max(0, int(args.num_workers_per_trial))
        common_dl_kw = dict(
            num_workers=num_workers,
            pin_memory=use_cuda,  # pin only if GPU
            persistent_workers=False,
            prefetch_factor=(4 if num_workers > 0 else None),
            collate_fn=collate,
        )
        dl_train = DataLoader(ds_train, batch_size=batch_size, shuffle=True,  **common_dl_kw)
        dl_val   = DataLoader(ds_val,   batch_size=batch_size, shuffle=False, **common_dl_kw)

        try:
            # Infer dims
            smp = next(iter(DataLoader(ds_train, batch_size=1, shuffle=False, collate_fn=collate, num_workers=0)))
            Fm = smp["x_main"].shape[-1]; Fo = smp["x_others"].shape[-1]

            model = PanelForecastNet(
                main_feat_dim=Fm, other_feat_dim=Fo,
                d_model=d_model, n_heads=n_heads, n_layers=n_layers,
                dropout=dropout, ff_mult=ff_mult, use_mean_pool=use_mean_pool
            ).to(device)
            model = maybe_compile(model, enable=(args.torch_compile and use_cuda))

            opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
            sched = torch.optim.lr_scheduler.ReduceLROnPlateau(opt, mode="min", factor=0.5, patience=3)
            crit  = nn.MSELoss()
            scaler = torch.amp.GradScaler('cuda', enabled=(args.amp and use_cuda))

            best_val, best_state, patience_left = float("inf"), None, patience

            for epoch in range(1, args.max_epochs + 1):
                model.train()
                for b in dl_train:
                    xm = b["x_main"].to(device, non_blocking=use_cuda)
                    xo = b["x_others"].to(device, non_blocking=use_cuda)
                    mo = b["m_others"].to(device, non_blocking=use_cuda)
                    y  = b["y"].to(device, non_blocking=use_cuda)

                    opt.zero_grad(set_to_none=True)
                    with torch.amp.autocast('cuda', enabled=(args.amp and use_cuda)):
                        pred = model(xm, xo, mo)
                        mask = torch.isfinite(pred) & torch.isfinite(y)
                        loss = crit(pred[mask], y[mask]) if mask.any() else (pred.mean() * 0.0)
                    scaler.scale(loss).backward()
                    scaler.unscale_(opt)
                    torch.nn.utils.clip_grad_norm_(model.parameters(), grad_clip)
                    scaler.step(opt); scaler.update()

                # Validation
                model.eval()
                preds, trues = [], []
                with torch.no_grad(), torch.amp.autocast('cuda', enabled=(args.amp and use_cuda)):
                    for b in dl_val:
                        xm = b["x_main"].to(device, non_blocking=use_cuda)
                        xo = b["x_others"].to(device, non_blocking=use_cuda)
                        mo = b["m_others"].to(device, non_blocking=use_cuda)
                        y  = b["y"].to(device, non_blocking=use_cuda)
                        ph = model(xm, xo, mo)
                        preds.append(ph.float().cpu().numpy()); trues.append(y.float().cpu().numpy())
                pv = np.concatenate(preds) if preds else np.array([])
                yv = np.concatenate(trues) if trues else np.array([])
                val_rmse = rmse_safe(yv, pv)

                if np.isfinite(val_rmse): sched.step(val_rmse)
                trial.report(val_rmse, step=(fold_idx - 1) * args.max_epochs + epoch)

                # prune on non-finite or unpromising
                if (not np.isfinite(val_rmse)) or trial.should_prune():
                    raise optuna.exceptions.TrialPruned()

                # early stopping by val RMSE
                if val_rmse < best_val - 1e-6:
                    best_val = val_rmse
                    best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
                    patience_left = patience
                else:
                    patience_left -= 1
                if patience_left <= 0:
                    break

            if best_state is not None:
                model.load_state_dict(best_state)

            # Final fold val RMSE
            model.eval()
            preds, trues = [], []
            with torch.no_grad(), torch.amp.autocast('cuda', enabled=(args.amp and use_cuda)):
                for b in dl_val:
                    xm = b["x_main"].to(device, non_blocking=use_cuda)
                    xo = b["x_others"].to(device, non_blocking=use_cuda)
                    mo = b["m_others"].to(device, non_blocking=use_cuda)
                    y  = b["y"].to(device, non_blocking=use_cuda)
                    ph = model(xm, xo, mo)
                    preds.append(ph.float().cpu().numpy()); trues.append(y.float().cpu().numpy())
            pv = np.concatenate(preds) if preds else np.array([])
            yv = np.concatenate(trues) if trues else np.array([])
            fold_val_rmses.append(rmse_safe(yv, pv))

        finally:
            shutdown_loader(dl_train)
            shutdown_loader(dl_val)

    obj = float(np.mean(fold_val_rmses)) if fold_val_rmses else float('inf')
    if not np.isfinite(obj):  # extra guard
        raise optuna.exceptions.TrialPruned()
    return obj


# ---------------- Main ----------------
def main():
    ap = argparse.ArgumentParser()
    # Data
    ap.add_argument("--raw_csv", type=str, default="MSTR Options History.csv")
    ap.add_argument("--main_strike", type=float, default=300.0)
    ap.add_argument("--main_expiration", type=str, default="2025-12-19")
    ap.add_argument("--option_type", type=str, default="call")

    # Search control
    ap.add_argument("--trials", type=int, default=80)
    ap.add_argument("--max_epochs", type=int, default=60)
    ap.add_argument("--n_folds_use", type=int, default=2)

    # Parallelism
    ap.add_argument("--n_jobs", type=int, default=-1,
                    help="-1 uses all cores; on single GPU set to 1")
    ap.add_argument("--threads_per_trial", type=int, default=2,
                    help="BLAS/torch threads per trial to avoid oversubscription")
    ap.add_argument("--num_workers_per_trial", type=int, default=0,
                    help="DataLoader workers per trial (0 safest when n_jobs>1)")

    # System
    ap.add_argument("--device", type=str, default="cpu")  # CPU default for parallelism
    ap.add_argument("--amp", action="store_true")
    ap.add_argument("--torch_compile", action="store_true")

    # Output
    ap.add_argument("--outdir", type=str, default="optuna_parallel_out")
    ap.add_argument("--tmp_datasets", type=str, default="optuna_parallel_out/tmp_datasets")
    ap.add_argument("--study_name", type=str, default="panel_optuna_parallel")
    ap.add_argument("--storage", type=str, default="", help="e.g., sqlite:///optuna.db")
    ap.add_argument("--seed", type=int, default=1337)

    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    os.makedirs(args.tmp_datasets, exist_ok=True)
    set_seed(args.seed)

    # Resolve n_jobs
    if args.n_jobs == -1:
        args.n_jobs = max(1, os.cpu_count() or 1)

    pruner = MedianPruner(n_startup_trials=8, n_warmup_steps=5, interval_steps=5)

    if args.storage:
        study = optuna.create_study(
            study_name=args.study_name, storage=args.storage, load_if_exists=True,
            direction="minimize", pruner=pruner
        )
    else:
        study = optuna.create_study(direction="minimize", pruner=pruner)

    # Run parallel trials
    study.optimize(lambda t: objective(t, args),
                   n_trials=args.trials,
                   n_jobs=args.n_jobs,
                   gc_after_trial=True)

    # Save results
    best = study.best_trial
    print("\n=== Best Trial ===")
    print(f"Avg Val RMSE: {best.value:.6f}")
    for k, v in best.params.items():
        print(f"  {k}: {v}")

    with open(os.path.join(args.outdir, "best_params.json"), "w") as f:
        json.dump(best.params, f, indent=2)
    with open(os.path.join(args.outdir, "study_summary.json"), "w") as f:
        json.dump({"best_value": best.value, "best_params": best.params,
                   "n_trials": len(study.trials)}, f, indent=2)
    try:
        study.trials_dataframe().to_csv(os.path.join(args.outdir, "trials.csv"), index=False)
    except Exception:
        pass


if __name__ == "__main__":
    main()
