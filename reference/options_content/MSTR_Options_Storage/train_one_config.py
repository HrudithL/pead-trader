#!/usr/bin/env python3
# Train one fixed config end-to-end (no Optuna), to verify pipeline stability.
import os, argparse, json, random, numpy as np
from typing import Tuple
import torch, torch.nn as nn
from torch.utils.data import Dataset, DataLoader

# Speedups (safe)
torch.backends.cuda.matmul.allow_tf32 = True
torch.set_float32_matmul_precision("high")

# Your modules
from Datacollection_panel import build_panel_sequences
from panel_model import PanelForecastNet

# ----------------- Utils -----------------
def set_seed(s=1337):
    random.seed(s); np.random.seed(s)
    torch.manual_seed(s); torch.cuda.manual_seed_all(s)

def rmse_safe(y, p):
    y = np.asarray(y); p = np.asarray(p)
    m = np.isfinite(y) & np.isfinite(p)
    if not m.any(): return float('inf')
    v = np.sqrt(np.mean((p[m]-y[m])**2))
    return float(v) if np.isfinite(v) else float('inf')

def mae_safe(y, p):
    y = np.asarray(y); p = np.asarray(p)
    m = np.isfinite(y) & np.isfinite(p)
    return float(np.mean(np.abs(p[m]-y[m]))) if m.any() else float('inf')

def mape_safe(y, p, eps=1e-8):
    y = np.asarray(y); p = np.asarray(p)
    m = np.isfinite(y) & np.isfinite(p) & (np.abs(y) > eps)
    return float(np.mean(np.abs((p[m]-y[m]) / (y[m]+eps)))) if m.any() else float('inf')

def r2_safe(y, p):
    y = np.asarray(y); p = np.asarray(p)
    m = np.isfinite(y) & np.isfinite(p)
    if not m.any(): return float('-inf')
    ym, pm = y[m], p[m]
    ss_res = float(np.sum((pm - ym)**2))
    ss_tot = float(np.sum((ym - np.mean(ym))**2)) + 1e-12
    return float(1.0 - ss_res/ss_tot)

def dir_acc_safe(y, p):
    y = np.asarray(y); p = np.asarray(p)
    m = np.isfinite(y) & np.isfinite(p)
    if not m.any(): return 0.0
    # Compare to previous true value direction
    y_prev = np.roll(y, 1); p_prev = np.roll(p, 1)
    m2 = m & np.isfinite(y_prev) & np.isfinite(p_prev)
    if not m2.any(): return 0.0
    dy_true = y[m2] - y_prev[m2]
    dy_pred = p[m2] - p_prev[m2]
    return float(np.mean(np.sign(dy_true) == np.sign(dy_pred)))

# ----------------- Dataset -----------------
class NpzPanelDataset(Dataset):
    """Loads the .npz from build_panel_sequences."""
    def __init__(self, npz_path: str):
        data = np.load(npz_path, allow_pickle=True)
        self.X_main   = data["X_main"]   # [N, L, Fm]
        self.X_others = data["X_others"] # [N, L, K, Fo]
        self.M_others = data["M_others"] # [N, L, K]
        self.y        = data["y"]        # [N]
        self.N = int(self.X_main.shape[0])

    def __len__(self): return self.N

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
    # sanitize and apply mask
    x_main   = torch.nan_to_num(x_main,   nan=0.0, posinf=0.0, neginf=0.0)
    x_others = torch.nan_to_num(x_others, nan=0.0, posinf=0.0, neginf=0.0)
    m_others = torch.nan_to_num(m_others, nan=0.0, posinf=0.0, neginf=0.0)
    x_others = x_others * m_others.unsqueeze(-1)
    return {"x_main": x_main, "x_others": x_others, "m_others": m_others, "y": y}

# ----------------- Split -----------------
def time_splits(N:int, train_share=0.7, val_share=0.1) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    train_end = int(N * train_share)
    val_end   = int(N * (train_share + val_share))
    train_idx = np.arange(0, train_end)
    val_idx   = np.arange(train_end, min(val_end, N))
    test_idx  = np.arange(min(val_end, N), N)
    return train_idx, val_idx, test_idx

# ----------------- Train/Eval -----------------
def train_one(args):
    set_seed(args.seed)
    device = torch.device(args.device)

    os.makedirs(args.outdir, exist_ok=True)

    # 1) Build one dataset .npz (or reuse if exists)
    npz_path = args.out_npz
    if not os.path.exists(npz_path):
        build_panel_sequences(
            path=args.raw_csv,
            out_npz=npz_path,
            main_strike=args.main_strike,
            main_expiration=args.main_expiration,
            option_type=args.option_type,
            input_length=args.input_length,
            horizon=args.horizon,
            top_k=args.top_k,
            include_bid=args.include_bid,
            include_stock_price=args.include_stock_price,
            dedupe_main=True,
            dedupe_others=True,
            verbose=True,
        )

    # 2) Load dataset + splits
    full = NpzPanelDataset(npz_path)
    N = len(full)
    if N < 50:
        raise RuntimeError(f"Too few samples ({N}) for a stable split; lower input_length/horizon/top_k or add data.")

    train_idx, val_idx, test_idx = time_splits(N, train_share=0.70, val_share=0.10)

    ds_train = torch.utils.data.Subset(full, train_idx.tolist())
    ds_val   = torch.utils.data.Subset(full, val_idx.tolist())
    ds_test  = torch.utils.data.Subset(full, test_idx.tolist())

    dlkw = dict(
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=args.num_workers,   # start with 0 to avoid mp issues
        pin_memory=True,
        persistent_workers=False,
        prefetch_factor=None if args.num_workers==0 else 4,
        collate_fn=collate,
        drop_last=False,
    )
    dl_train = DataLoader(ds_train, **dlkw)
    dlkw["shuffle"] = False
    dl_val   = DataLoader(ds_val,   **dlkw)
    dl_test  = DataLoader(ds_test,  **dlkw)

    # 3) Infer feature dims
    smp = next(iter(DataLoader(ds_train, batch_size=1, shuffle=False, collate_fn=collate, num_workers=0)))
    Fm = smp["x_main"].shape[-1]
    Fo = smp["x_others"].shape[-1]

    # 4) Model/opt
    model = PanelForecastNet(
        main_feat_dim=Fm, other_feat_dim=Fo,
        d_model=args.d_model, n_heads=args.n_heads, n_layers=args.n_layers,
        dropout=args.dropout, ff_mult=args.ff_mult, use_mean_pool=args.use_mean_pool
    ).to(device)
    if args.torch_compile:
        try:
            model = torch.compile(model, mode="reduce-overhead")
        except Exception:
            pass

    opt   = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    sched = torch.optim.lr_scheduler.ReduceLROnPlateau(opt, mode="min", factor=0.5, patience=3)
    crit  = nn.MSELoss()
    scaler = torch.amp.GradScaler('cuda', enabled=args.amp)

    # 5) Train loop with early stop on val RMSE
    best_val, best_state, patience_left = float("inf"), None, args.patience
    for epoch in range(1, args.epochs+1):
        model.train()
        for b in dl_train:
            xm = b["x_main"].to(device, non_blocking=True)
            xo = b["x_others"].to(device, non_blocking=True)
            mo = b["m_others"].to(device, non_blocking=True)
            y  = b["y"].to(device, non_blocking=True)

            opt.zero_grad(set_to_none=True)
            with torch.amp.autocast('cuda', enabled=args.amp):
                pred = model(xm, xo, mo)
                mask = torch.isfinite(pred) & torch.isfinite(y)
                loss = crit(pred[mask], y[mask]) if mask.any() else (pred.mean() * 0.0)

            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.grad_clip)
            scaler.step(opt); scaler.update()

        # --- validation
        model.eval()
        pv, yv = [], []
        with torch.no_grad(), torch.amp.autocast('cuda', enabled=args.amp):
            for b in dl_val:
                xm = b["x_main"].to(device, non_blocking=True)
                xo = b["x_others"].to(device, non_blocking=True)
                mo = b["m_others"].to(device, non_blocking=True)
                y  = b["y"].to(device, non_blocking=True)
                ph = model(xm, xo, mo)
                pv.append(ph.float().cpu().numpy()); yv.append(y.float().cpu().numpy())
        pv = np.concatenate(pv) if pv else np.array([])
        yv = np.concatenate(yv) if yv else np.array([])
        val_rmse = rmse_safe(yv, pv)
        if np.isfinite(val_rmse): sched.step(val_rmse)

        print(f"Epoch {epoch:03d} | val RMSE: {val_rmse:.6f}")

        # early stop
        if val_rmse < best_val - 1e-6:
            best_val = val_rmse
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            patience_left = args.patience
        else:
            patience_left -= 1
        if patience_left <= 0:
            break

    # restore best
    if best_state is not None:
        model.load_state_dict(best_state)

    # 6) Final evaluation on val & test
    def _eval(dl):
        model.eval()
        preds, trues = [], []
        with torch.no_grad(), torch.amp.autocast('cuda', enabled=args.amp):
            for b in dl:
                xm = b["x_main"].to(device, non_blocking=True)
                xo = b["x_others"].to(device, non_blocking=True)
                mo = b["m_others"].to(device, non_blocking=True)
                y  = b["y"].to(device, non_blocking=True)
                ph = model(xm, xo, mo)
                preds.append(ph.float().cpu().numpy()); trues.append(y.float().cpu().numpy())
        p = np.concatenate(preds) if preds else np.array([])
        y = np.concatenate(trues) if trues else np.array([])
        return {
            "rmse": rmse_safe(y, p),
            "mae":  mae_safe(y, p),
            "mape": mape_safe(y, p),
            "r2":   r2_safe(y, p),
            "direction_acc": dir_acc_safe(y, p),
            "n":    int(y.size),
        }

    val_metrics = _eval(dl_val)
    test_metrics = _eval(dl_test)

    print("\n== Results ==")
    print("Val :", val_metrics)
    print("Test:", test_metrics)

    # 7) Save model
    os.makedirs(args.outdir, exist_ok=True)
    out_path = os.path.join(args.outdir, "single_config_model.pt")
    torch.save({"state_dict": model.state_dict(), "cfg": vars(args)}, out_path)
    print(f"Saved model -> {out_path}")

    # 8) Save metrics
    with open(os.path.join(args.outdir, "single_config_metrics.json"), "w") as f:
        json.dump({"val": val_metrics, "test": test_metrics}, f, indent=2)

def main():
    ap = argparse.ArgumentParser()
    # Data
    ap.add_argument("--raw_csv", type=str, default="MSTR Options History.csv")
    ap.add_argument("--main_strike", type=float, default=300.0)
    ap.add_argument("--main_expiration", type=str, default="2025-12-19")
    ap.add_argument("--option_type", type=str, default="call")
    ap.add_argument("--input_length", type=int, default=30)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--top_k", type=int, default=8)
    ap.add_argument("--include_bid", action="store_true")
    ap.add_argument("--include_stock_price", action="store_true")

    ap.add_argument("--out_npz", type=str, default="panel_single_config.npz")

    # Model
    ap.add_argument("--d_model", type=int, default=160)
    ap.add_argument("--n_heads", type=int, default=5)
    ap.add_argument("--n_layers", type=int, default=3)
    ap.add_argument("--ff_mult", type=int, default=4)
    ap.add_argument("--dropout", type=float, default=0.2)
    ap.add_argument("--use_mean_pool", action="store_true")

    # Train
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--batch_size", type=int, default=96)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--weight_decay", type=float, default=1e-4)
    ap.add_argument("--grad_clip", type=float, default=1.5)
    ap.add_argument("--patience", type=int, default=10)

    # System
    ap.add_argument("--device", type=str, default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--num_workers", type=int, default=0)  # start at 0 for stability
    ap.add_argument("--amp", action="store_true")
    ap.add_argument("--torch_compile", action="store_true")
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--outdir", type=str, default="out_single")

    args = ap.parse_args()
    train_one(args)

if __name__ == "__main__":
    main()
