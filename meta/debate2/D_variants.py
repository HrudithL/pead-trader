from D_lib import *
import json, sys
from concurrent.futures import ProcessPoolExecutor

def pit_pct(win, group=None, minpool=500):
    f = DOUT / f"D_pit_{win}_{group}.npy"
    if f.exists(): return np.load(f)
    d0 = ev["day0_date"].to_numpy("datetime64[D]"); sue = ev["sue_primary"].to_numpy(float)
    g = ev[group].to_numpy() if group else np.zeros(len(ev), int)
    pct = np.full(len(ev), np.nan)
    days = np.unique(d0)
    lo = np.searchsorted(d0, days - np.timedelta64(win, "D"), "left"); hi = np.searchsorted(d0, days, "left")
    for k, day in enumerate(days):
        a, b = lo[k], hi[k]
        m0, m1 = np.searchsorted(d0, day, "left"), np.searchsorted(d0, day, "right")
        if b - a < minpool: continue
        for gv in np.unique(g[m0:m1]):
            idx = m0 + np.flatnonzero(g[m0:m1] == gv)
            pool = np.sort(sue[a:b][g[a:b] == gv])
            if len(pool) < (minpool if not group else 100): continue
            v = sue[idx]; pct[idx] = (np.searchsorted(pool, v, "left") + np.searchsorted(pool, v, "right")) / 2 / len(pool)
    np.save(f, pct); return pct

def wf_hold():
    """choose hold in {40,60,90} each quarter from completed top-decile events only (excess per session)."""
    pit = ev.pit_sue_pct.to_numpy(); Hs = [40, 60, 90]
    ex = {h: getP(h)[0]["excess_hold"].to_numpy() for h in Hs}
    q = pd.PeriodIndex(ev["day0_date"].dt.to_period("Q")); qid = pd.PeriodIndex(cal, freq="Q")
    ent = ev.entry_cal.to_numpy(); Hvec = np.full(len(ev), 60); choice = {}
    for qq in q.unique():
        d0 = int(np.argmax(qid == qq))
        done = (pit >= 0.9) & (ent + 90 < d0)
        if done.sum() < 300: continue
        best = max(Hs, key=lambda h: np.nanmean(ex[h][done]) / h)
        Hvec[np.asarray(q == qq)] = best; choice[str(qq)] = best
    return Hvec, choice

def spec(name):
    pit = ev.pit_sue_pct.to_numpy(); prc = ev.d0prc.to_numpy(); adv = ev.avg_dollar_volume_21d.to_numpy(float)
    liq = ev.liquidity_quintile.to_numpy(float); d0ex = ev.d0ex.to_numpy(); d0r = ev.d0ret.to_numpy()
    kw = {}; H = 60; evp = None; mask = np.ones(len(ev), bool); p = pit
    if name.startswith("pool"): p = pit_pct(int(name[4:]))
    elif name.startswith("thr"): thr = float(name[3:])
    elif name == "H40": H = 40
    elif name == "H90": H = 90
    elif name == "wfH":
        Hvec, ch = wf_hold(); evp = make(Hvec); kw["_ch"] = ch
    elif name == "prc5": mask = prc >= 5
    elif name == "prc10": mask = prc >= 10
    elif name == "adv1m": mask = adv >= 1e6
    elif name == "adv5m": mask = adv >= 5e6
    elif name == "liq3": mask = liq >= 3
    elif name == "d0pos": mask = d0ex > 0
    elif name == "d0strong": mask = d0ex > 0.03
    elif name == "d0neg": mask = d0ex < 0
    elif name == "d0notneg": mask = d0ex > -0.02
    elif name == "sizematch": p = pit_pct(365, "size_quintile")
    elif name == "sectormatch": p = pit_pct(365, "ff12_sector")
    elif name == "ewfill": kw["fillret"] = ew
    elif name == "cashfill": kw["fill"] = "cash"
    elif name == "trend200":
        px = (1 + vws).cumprod(); ma = px.rolling(200).mean(); on = (px > ma).shift(1).fillna(True).to_numpy()
        kw["fillret"] = vw * on
    elif name == "cap1": kw["name_cap"] = 0.01
    elif name == "cap4": kw["name_cap"] = 0.04
    elif name == "eqw": kw["risk_power"] = 0
    elif name == "gc50": kw["gross_cap"] = 0.5
    elif name == "gc95": kw["gross_cap"] = 0.95
    elif name == "combo_q": mask = (prc >= 5) & (adv >= 1e6)
    elif name == "combo_q_ew": mask = (prc >= 5) & (adv >= 1e6); kw["fillret"] = ew
    elif name == "base": pass
    thr_ = float(name[3:]) if name.startswith("thr") else 0.9
    return p, thr_, mask, H, evp, kw

def spec_multi(name):
    parts = [spec(t) for t in name.split("+")]
    p, thr, mask, H, evp, kw = parts[0]
    for q in parts[1:]:
        if q[0] is not ev.pit_sue_pct.to_numpy() and not np.array_equal(q[0], ev.pit_sue_pct.to_numpy(), equal_nan=True): p = q[0]
        thr = q[1] if q[1] != 0.9 else thr
        mask = mask & q[2]; H = q[3] if q[3] != 60 else H; evp = q[4] or evp; kw.update(q[5])
    return p, thr, mask, H, evp, kw

def runone(args):
    name, seeds = args
    p, thr, mask, H, evp, kw = spec_multi(name)
    ch = kw.pop("_ch", None)
    def sel(pp):
        return lambda e, c: np.where((pp[c] >= thr) & mask[c], 0.05, -1.0)
    r, n, h = go(sel(p), H=H, evp=evp, **kw)
    s = summarize(r); row = dict(name=name, n=n, maxH=h)
    for k, (m, x) in s.items():
        row.update({f"{k}_ret": m["ann"], f"{k}_sh": m["sharpe"], f"{k}_dd": m["maxdd"], f"{k}_alpha": x["alpha"], f"{k}_t": x["t"], f"{k}_beta": x["beta"]})
    pl = []
    for sd in seeds:
        rp, _, _ = go(sel(pct_placebo(p, sd)), H=H, evp=evp, **kw); sp = summarize(rp)
        pl.append((sp["full"][0]["ann"], sp["full"][1]["alpha"], sp["h2"][1]["alpha"]))
    pl = np.array(pl).mean(0); row.update(pl_ret=pl[0], pl_alpha=pl[1], pl_alpha_h2=pl[2])
    if ch: row["hold_choice"] = json.dumps(pd.Series(ch).value_counts().to_dict())
    return row

if __name__ == "__main__":
    names = sys.argv[1].split(",")
    seeds = [0, 1, 2]
    with ProcessPoolExecutor(10) as ex:
        rows = list(ex.map(runone, [(n, seeds) for n in names]))
    df = pd.DataFrame(rows); out = DOUT / f"D_results_{sys.argv[2]}.csv"; df.to_csv(out, index=False)
    pd.options.display.width = 250
    print(df[["name", "n", "full_ret", "full_sh", "full_dd", "full_alpha", "full_t", "pl_alpha", "h1_ret", "h2_ret", "0813_ret", "h2_alpha", "pl_alpha_h2"]].round(3).to_string())
