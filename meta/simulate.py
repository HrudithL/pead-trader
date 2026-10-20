"""Unlevered, walk-forward event-selection engine ("Selective Drift Book").  Imported by run_meta.py.

Rules that make it honest:
  * NO BORROWING. Long-only stock positions bought with cash; the rest of the account sits in the market
    index (or cash). Holdings' market value never exceeds NAV (checked and reported: max_hold_over_nav).
  * NO LOOK-AHEAD. Each quarter the expected-excess-return model is re-fit ONLY on events whose 60-session
    hold had already ended before that quarter began; features are trailing / pre-day0; SUE percentile is
    ranked against the trailing 365 days of EARLIER announcements; every hold is a fixed 60 sessions.
  * Entry at the close of day0 (after-close announcers were already shifted to the next session), earning
    from day0+1, the same convention as the existing engine.
  * Real compounding: position value = notional x buy-and-hold growth (delistings stay in via the stock's
    own last return); sizing is set from the NAV at the START of each quarter.
  * Costs: per-side bps by liquidity quintile (25/15/10/7/5) charged at entry and again at exit;
    COST_MULT stresses them. 5% of 21-day dollar volume cap per name.
"""
import numpy as np
import pandas as pd

OUT_COST = {1: 25, 2: 15, 3: 10, 4: 7, 5: 5}
FEATURES = ["sue", "sue_top", "thin", "mid_liq", "lowbeta", "highbeta", "ivol", "sue_x_thin", "sue_x_lowbeta",
            "sue_x_ivol", "small"]


def make_features(ev: pd.DataFrame) -> np.ndarray:
    s = ev["pit_sue_pct"].to_numpy(float) - 0.5
    liq = ev["liquidity_quintile"].to_numpy(float)
    beta = ev["beta_252d"].to_numpy(float)
    iv = ev["ivol_252d_pct"].to_numpy(float) - 0.5
    thin = (liq <= 2).astype(float)
    lowb = (beta < 0.8).astype(float)
    X = np.column_stack([
        s, (s > 0.4).astype(float), thin, (liq == 3).astype(float), lowb, (beta > 1.3).astype(float), iv,
        s * thin, s * lowb, s * iv, (ev["size_quintile"].to_numpy(float) <= 2).astype(float)])
    return X


def fit_ridge(X, y, lam=50.0):
    A = np.column_stack([np.ones(len(X)), X])
    P = np.eye(A.shape[1]) * lam
    P[0, 0] = 0
    return np.linalg.solve(A.T @ A + P, A.T @ y)


def cost_per_side(ev):
    return ev["liquidity_quintile"].map(OUT_COST).fillna(15).to_numpy(float) / 1e4


def prepare(ev, paths, mkt):
    """Static per-row path arrays: growth factor before each day, used for exact buy-and-hold P&L."""
    r = paths["ret"].to_numpy(float)
    e = paths["ev"].to_numpy()
    c = paths["cal"].to_numpy()
    lg = np.log1p(np.clip(r, -0.999, None))
    cs = np.cumsum(lg)
    starts = np.r_[0, np.flatnonzero(np.diff(e)) + 1]
    lengths = np.diff(np.r_[starts, len(e)])
    base = np.repeat(cs[starts] - lg[starts], lengths)
    cum_before = np.exp(cs - lg - base)           # growth up to the previous close
    return dict(r=r, e=e, c=c, cum_before=cum_before, starts=starts)


def run(ev, P, mkt_ret, cal, *, start="2000-01-01", gross_cap=0.85, theta=0.015, name_cap=0.02, adv_cap=0.05,
        cost_mult=1.0, fill="index", model="ridge", sue_min=0.0, init=1e7, refit_lam=50.0, longs_only=True,
        min_train=20000, risk_power=1.0, return_detail=False, sel_fn=None):
    n_days = len(cal)
    X = make_features(ev)
    y = np.clip(ev["excess_hold"].to_numpy(float), *np.nanpercentile(ev["excess_hold"], [1, 99]))
    exit_cal = ev["exit_cal"].to_numpy()
    entry_cal = ev["entry_cal"].to_numpy()
    qid = pd.PeriodIndex(cal, freq="Q")
    uq = qid.unique()
    q_first = {q: int(np.argmax(qid == q)) for q in uq}
    start_cal = int(cal.searchsorted(pd.Timestamp(start)))
    ok_feat = np.isfinite(X).all(1) & np.isfinite(y) & ev["pit_sue_pct"].notna().to_numpy()
    cps = cost_per_side(ev) * cost_mult
    adv = ev["avg_dollar_volume_21d"].to_numpy(float)
    ivol = ev["ivol_252d"].to_numpy(float)

    pnl_stock = np.zeros(n_days)               # stock-sleeve daily P&L in dollars
    cost_pnl = np.zeros(n_days)
    cost_basis = np.zeros(n_days + 1)          # open cost basis by day (difference array)
    value_hold = np.zeros(n_days)              # marked value of holdings, accumulated per day
    nav = np.full(n_days, np.nan)
    idx_pnl = np.zeros(n_days)
    nav_prev = init
    chosen = []
    coefs = []
    ev_q = pd.PeriodIndex(ev["day0_date"].dt.to_period("Q"))
    notional_all = np.zeros(len(ev))
    path_by_ev_start = np.searchsorted(P["e"], np.arange(len(ev)), "left")
    path_by_ev_end = np.searchsorted(P["e"], np.arange(len(ev)), "right")

    for q in uq:
        d0 = q_first[q]
        d1 = q_first.get(q + 1, n_days)
        days = np.arange(d0, d1)
        nav_start = nav_prev if np.isfinite(nav_prev) else init
        if d1 > start_cal:
            cand = np.flatnonzero((ev_q == q) & ok_feat & (entry_cal >= max(d0, start_cal)))
            train = np.flatnonzero(ok_feat & (exit_cal < d0))
            if len(cand) and len(train) >= min_train:
                beta_hat = fit_ridge(X[train], y[train], refit_lam)
                coefs.append((str(q), beta_hat))
                pred = beta_hat[0] + X[cand] @ beta_hat[1:]
                net = pred - 2 * cps[cand]
                if sel_fn is not None:
                    net = sel_fn(ev, cand)
                sel = net > theta
                if sue_min:
                    sel &= ev["pit_sue_pct"].to_numpy()[cand] >= sue_min
                cand, net = cand[sel], net[sel]
                if len(cand):
                    risk = np.clip(ivol[cand], 0.008, 0.08) ** (2 * risk_power) if risk_power else np.ones(len(cand))
                    w = net / risk
                    w = w / w.sum()
                    # raw target sizing: quarter budget = gross_cap x NAV, per-name caps, liquidity cap
                    tgt = np.minimum(w * gross_cap * nav_start * 1.0, name_cap * nav_start)
                    tgt = np.minimum(tgt, np.where(np.isfinite(adv[cand]), adv[cand] * adv_cap, np.inf))
                    # open-book headroom on cost basis: admit highest expected edge first
                    order = np.argsort(-net, kind="stable")
                    head = gross_cap * nav_start - np.cumsum(cost_basis[:-1])[d0:d1] if d1 > d0 else np.array([])
                    used = np.zeros(d1 - d0)
                    for j in order:
                        i = cand[j]
                        a = max(entry_cal[i] - d0, 0)
                        b = min(exit_cal[i] - d0 + 1, d1 - d0)
                        want = tgt[j]
                        room = (head[a:b] - used[a:b]).min() if b > a else want
                        give = min(want, max(room, 0.0))
                        if give <= 0:
                            continue
                        used[a:b] += give
                        notional_all[i] = give
                        chosen.append(i)
                        # P&L path (exact buy-and-hold) added once, for the whole life
                        s, e_ = path_by_ev_start[i], path_by_ev_end[i]
                        cc = P["c"][s:e_]
                        pn = give * P["cum_before"][s:e_] * P["r"][s:e_]
                        np.add.at(pnl_stock, cc, pn)
                        np.add.at(value_hold, cc, give * P["cum_before"][s:e_] * (1 + P["r"][s:e_]))
                        c_in, c_out = give * cps[i], give * cps[i]
                        cost_pnl[entry_cal[i]] -= c_in
                        cost_pnl[min(exit_cal[i], n_days - 1)] -= c_out
                        cost_basis[entry_cal[i]] += give
                        cost_basis[min(exit_cal[i], n_days - 1) + 1] -= give
        # finalize days of this quarter
        for t in days:
            held_val_prev = value_hold[t - 1] if t > 0 else 0.0
            resid = max(nav_prev - held_val_prev, 0.0) if fill == "index" else 0.0
            ip = resid * mkt_ret[t] if (fill == "index" and t >= start_cal) else (nav_prev * mkt_ret[t] if (fill == "index" and t < start_cal) else 0.0)
            idx_pnl[t] = ip
            nav_prev = nav_prev + pnl_stock[t] + cost_pnl[t] + ip
            nav[t] = nav_prev
    daily = pd.DataFrame({"date": cal, "nav": nav, "pnl_stock": pnl_stock, "pnl_cost": cost_pnl, "pnl_index": idx_pnl,
                          "hold_value": value_hold})
    daily["daily_return"] = daily["nav"].pct_change().fillna(0.0)
    daily["hold_over_nav"] = daily["hold_value"] / daily["nav"].shift(1)
    out = dict(daily=daily, chosen=np.array(chosen), notional=notional_all, coefs=coefs)
    return out
