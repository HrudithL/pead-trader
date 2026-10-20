"""Build docs/SITUATIONS.md from results/situations_*.csv (run scripts/situations_report.py first)."""
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "results"
S = pd.read_csv(R / "situations_all.csv"); C = pd.read_csv(R / "situations_contrasts.csv")
D = pd.read_csv(R / "situations_drivers.csv"); B = pd.read_csv(R / "situations_books.csv").set_index("strategy")

CARDS = [  # (strategy, one-line mechanism in plain words)
 ("extreme_picks", "Puts half its money on each end of the ranking (biggest beats, biggest misses). Simple, so its fortunes are tied to how the very best and very worst surprises behave."),
 ("graded_bets", "Spreads money over every company, so a lot of it sits on mild surprises that barely pay."),
 ("size_balanced", "Extreme Picks with an equal budget for each company size, so no size group can dominate."),
 ("conviction_tilt", "Drops the unsure middle and leans on the strongest surprises, balanced by company size."),
 ("sector_balanced", "Conviction Tilt with an equal budget for each of 12 industry groups, so no industry can dominate."),
 ("balanced_leverage", "Sector Balanced allowed to hold more; same bets, bigger. The reference book."),
 ("calm_weighted", "Balanced Leverage with bets divided by how jumpy the stock is."),
 ("calm_rank_tilt", "Balanced Leverage with bets for the jumpiest stocks shrunk hard, so most of its money is in calm stocks."),
 ("market_exposed", "Balanced Leverage's signal with raw returns and no forced long/short match, so market moves leak in. (Its alpha figure here is still measured against the average announcing company, so it leaves out the market's own contribution, which is why it is below this strategy's total P&L.)"),
]
PICK = [("Side and surprise size", "long D10", "Biggest beats (long)"), ("Side and surprise size", "short D01", "Biggest misses (short)"),
        ("Company size", "size 1 (smallest)", "Smallest companies"), ("Company size", "size 5 (largest)", "Largest companies"),
        ("How easily traded", "liquidity 1 (thinnest)", "Thinnest trading"), ("How easily traded", "liquidity 5 (most traded)", "Most heavily traded"),
        ("How jumpy the stock is", "calm stocks", "Calm stocks"), ("How jumpy the stock is", "jumpy stocks", "Jumpy stocks"),
        ("Stock's market sensitivity", "low-beta stocks", "Low-beta stocks"), ("Stock's market sensitivity", "high-beta stocks", "High-beta stocks"),
        ("Hold length", ">90d", "Holds over 90 days"), ("Hold length", "<=40d", "Holds up to 40 days"),
        ("Earnings season", "Q2 reports (Apr-Jun)", "Reports in Apr-Jun"), ("Earnings season", "Q4 reports (Oct-Dec)", "Reports in Oct-Dec"),
        ("Period", "1996-01", "1996-2001"), ("Period", "2008-13", "2008-2013")]

def f(x, d=0): return "-" if pd.isna(x) else f"{x:.{d}f}"

def card(name, mech):
    s = S[S.strategy == name]; b = B.loc[name]
    out = [f"### {name.replace('_', ' ').title()}  (`{name}`)", "", mech, "",
           f"**Total:** ${b.alpha_M:.1f}M alpha over {int(b.quarters)} quarters, {b.alpha_bp:.0f} bp per dollar traded, "
           f"~{b.positions / 18:,.0f} positions a year.", "",
           "| Situation | Share of money traded | Share of alpha | Alpha per $ traded (bp) | Sure? (t) | Verdict |", "|---|---|---|---|---|---|"]
    for dim, sit, label in PICK:
        x = s[(s.dimension == dim) & (s.situation == sit)]
        if len(x):
            x = x.iloc[0]
            out.append(f"| {label} | {x.pct_of_dollars * 100:.0f}% | {x.pct_of_alpha * 100:.0f}% | {x.alpha_bp_per_dollar:.0f} | {f(x.t_stat, 1)} | {x.verdict} |")
    sd = s[s.dimension == "Side and surprise size"]
    weak = sd[sd.verdict.isin(["UNPROVEN", "LEAKS"])]
    out += ["", f"**Money with no proven edge:** {weak.pct_of_dollars.sum() * 100:.0f}% of dollars sit in surprise groups whose alpha is not reliably above zero "
            f"({', '.join(weak.situation) if len(weak) else 'none'}); together they earn {weak.pct_of_alpha.sum() * 100:.0f}% of the alpha.", ""]
    d = D[D.strategy == name].set_index("driver")["t_stat"]
    dd = D[D.strategy == name].set_index("driver")["bp_per_1sd"]
    sig = [(k, dd[k], d[k]) for k in d.index if not k.startswith("(") and abs(d[k]) >= 2]
    out.append("**What matters once everything is controlled for at once** (bp per $ for one standard deviation more of the feature; only |t| >= 2 shown): "
               + ("; ".join(f"{k}: {v:+.0f} (t {t:+.1f})" for k, v, t in sig) if sig else "nothing clears |t| >= 2.") + "")
    return "\n".join(out) + "\n"

import json, re
F = json.loads((R / "situations_facts.json").read_text())
BC = pd.read_csv(R / "profiles_benchmark_compare.csv")
REF = F["strategy"]
SR = S[S.strategy == REF]; CR = C[C.strategy == REF].set_index("dimension"); DR = D[D.strategy == REF].set_index("driver")

def row(sit, dim=None):
    x = SR[(SR.situation == sit) & ((SR.dimension == dim) if dim else True)]
    return x.iloc[0]
def pc(x): return f"{x * 100:.0f}%"
def bp(x): return f"{x:.0f}"
def sg(x, d=0): return f"{x:+.{d}f}"
def t1(x): return f"{x:.1f}"

V = dict(nq=str(int(B.loc[REF].quarters)))
def put(prefix, sit, dim=None, bp_=True, money=True, alpha=True, t=False):
    r = row(sit, dim)
    if money: V[f"{prefix}_money"] = pc(r.pct_of_dollars)
    if alpha: V[f"{prefix}_alpha"] = pc(r.pct_of_alpha)
    if bp_: V[f"{prefix}_bp"] = bp(r.alpha_bp_per_dollar)
    if t: V[f"{prefix}_t"] = t1(r.t_stat)
    return r
l4, l5 = row("liquidity 4"), row("liquidity 5 (most traded)")
V["liq45_money"] = pc(l4.pct_of_dollars + l5.pct_of_dollars); V["liq45_alpha"] = pc(l4.pct_of_alpha + l5.pct_of_alpha)
put("liq1", "liquidity 1 (thinnest)"); V["liq5_bp"] = bp(l5.alpha_bp_per_dollar)
put("size1", "size 1 (smallest)"); put("size2", "size 2"); put("size5", "size 5 (largest)")
c = CR.loc["Thin vs heavily traded"]; V["thin_diff"] = bp(c.diff_bp); V["thin_t"] = t1(c.t_stat)
c = CR.loc["Small vs large companies"]; V["small_diff"] = bp(c.diff_bp); V["small_t"] = t1(c.t_stat)
V["corr_size_liq"] = f"{F['corr_size_liquidity']:.2f}"
put("hold90", ">90d", "Hold length"); put("hold40", "<=40d", "Hold length")
V["hold_t"] = t1(CR.loc["Long holds vs short holds"].t_stat)
put("lowbeta", "low-beta stocks"); h = put("highbeta", "high-beta stocks", t=True)
eras = lambda r: " / ".join(bp(r[k]) for k in ("bp_1996_01", "bp_2002_07", "bp_2008_13"))
V["lowbeta_eras"] = eras(row("low-beta stocks")); V["highbeta_eras"] = eras(h)
V["beta_driver_bp"] = f"{abs(DR.loc['Higher beta'].bp_per_1sd):.0f}"; V["beta_driver_t"] = t1(DR.loc["Higher beta"].t_stat)
put("d10", "long D10", t=True); V["d09_bp"] = bp(row("long D09").alpha_bp_per_dollar)
for k, sit in (("s1", "short D01"), ("s2", "short D02"), ("s3", "short D03")): V[f"{k}_bp"] = bp(row(sit).alpha_bp_per_dollar)
d7, d8 = row("long D07"), row("long D08")
V["d78_money"] = pc(d7.pct_of_dollars + d8.pct_of_dollars); V["d78_alpha"] = pc(d7.pct_of_alpha + d8.pct_of_alpha)
V["d07_bp"] = bp(d7.alpha_bp_per_dollar); V["d07_t"] = t1(d7.t_stat)
c = CR.loc["Biggest beats (long D10) vs biggest misses (short D01)"]; V["d10_vs_s1"] = bp(c.diff_bp); V["d10_vs_s1_t"] = t1(c.t_stat)
q2, q3, q4, q1 = (row(k) for k in ("Q2 reports (Apr-Jun)", "Q3 reports (Jul-Sep)", "Q4 reports (Oct-Dec)", "Q1 reports (Jan-Mar: year-end results)"))
V["amj_jas_money"] = pc(q2.pct_of_dollars + q3.pct_of_dollars); V["amj_jas_alpha"] = pc(q2.pct_of_alpha + q3.pct_of_alpha)
V["amj_bp"], V["jas_bp"] = bp(q2.alpha_bp_per_dollar), bp(q3.alpha_bp_per_dollar)
V["q4_money"], V["q4_alpha"], V["q4_bp"], V["q4_t"], V["q4_eras"] = pc(q4.pct_of_dollars), pc(q4.pct_of_alpha), bp(q4.alpha_bp_per_dollar), t1(q4.t_stat), eras(q4)
V["q1_money"], V["q1_alpha"], V["q1_bp"], V["q1_t"], V["q1_eras"] = pc(q1.pct_of_dollars), pc(q1.pct_of_alpha), bp(q1.alpha_bp_per_dollar), t1(q1.t_stat), eras(q1)
V["worst_q"] = F["worst_quarter"]; V["worst_q_M"] = f"{abs(F['worst_quarter_M']):.2f}"; V["next5_M"] = f"{abs(F['next_five_worst_M']):.2f}"
V["q1_bp_ex"] = bp(F["q1_reports_bp_ex2009"])
e1, e2, e3 = row("1996-01", "Period"), row("2002-07", "Period"), row("2008-13", "Period")
V["era1_bp"], V["era2_bp"], V["era3_bp"] = bp(e1.alpha_bp_per_dollar), bp(e2.alpha_bp_per_dollar), bp(e3.alpha_bp_per_dollar)
V["era3_money"], V["era3_alpha"], V["era3_t"] = pc(e3.pct_of_dollars), pc(e3.pct_of_alpha), t1(e3.t_stat)
c = CR.loc["Early vs late period"]; V["era_diff"] = bp(c.diff_bp); V["era_t"] = t1(c.t_stat)
V["mrise_bp"], V["mfell_bp"] = bp(row("market rose >5%").alpha_bp_per_dollar), bp(row("market fell >5%").alpha_bp_per_dollar)
c = CR.loc["Market rose vs fell during hold"]; V["mdiff"] = sg(c.diff_bp); V["mdiff_t"] = f"{c.t_stat:.2f}"
V["corr_q_mkt"] = f"{F['corr_quarter_alpha_market']:+.2f}"
V["pct_q_pos"] = pc(F["pct_quarters_positive"]); V["best10"] = pc(F["best10_share_of_alpha"])
V["liq_bps"] = " / ".join(bp(row(f"liquidity {i}" + {1: " (thinnest)", 5: " (most traded)"}.get(i, "")).alpha_bp_per_dollar) for i in range(1, 6))
V["size_bps"] = " / ".join(bp(row(f"size {i}" + {1: " (smallest)", 5: " (largest)"}.get(i, "")).alpha_bp_per_dollar) for i in range(1, 6))
V["calm_bp"], V["jumpy_bp"] = bp(row("calm stocks").alpha_bp_per_dollar), bp(row("jumpy stocks").alpha_bp_per_dollar)
c = CR.loc["Calm vs jumpy stocks"]; V["calm_q_bp"], V["jumpy_q_bp"], V["calm_t"] = bp(c.a_bp), bp(c.b_bp), t1(c.t_stat)
V["jumpy_driver_bp"] = sg(DR.loc["Jumpier stock (stock-specific vol)"].bp_per_1sd); V["jumpy_driver_t"] = t1(DR.loc["Jumpier stock (stock-specific vol)"].t_stat)
V["pct_jumpy_hb"], V["pct_calm_hb"], V["corr_vol_beta"] = pc(F["pct_jumpy_high_beta"]), pc(F["pct_calm_high_beta"]), f"{F['corr_vol_beta']:.2f}"
# benchmark table
bc = BC[BC.strategy == REF].set_index("cut")
labels = [("all", "Whole book"), ("long leg", "Long leg"), ("short leg", "Short leg"), ("smallest size quintile", "Smallest companies (size 1)"),
          ("size 2", "Size 2"), ("size 3", "Size 3"), ("size 4", "Size 4"), ("largest size quintile", "Largest companies (size 5)"),
          ("thin", "Thin trading"), ("low-beta", "Low-beta stocks")]
tb = ["| Cut | Share of money | Real P&L per $ (bp) | Size-matched alpha (bp) | All-announcer alpha (bp) |", "|---|---|---|---|---|"]
for k, lab in labels:
    r = bc.loc[k]; tb.append(f"| {lab} | {r.money_pct:.0f}% | {r.real_bp:.0f} | {r.size_matched_bp:.0f} | {r.all_announcers_bp:.0f} |")
V["bench_table"] = "\n".join(tb)
g = lambda k, col: f"{bc.loc[k, col]:.0f}"
V.update(b_thin_sm=g("thin", "size_matched_bp"), b_thin_all=g("thin", "all_announcers_bp"), b_low_sm=g("low-beta", "size_matched_bp"), b_low_all=g("low-beta", "all_announcers_bp"),
         b_s1_sm=g("smallest size quintile", "size_matched_bp"), b_s1_all=g("smallest size quintile", "all_announcers_bp"),
         b_s5_sm=g("largest size quintile", "size_matched_bp"), b_s5_all=g("largest size quintile", "all_announcers_bp"),
         b_long_all=g("long leg", "all_announcers_bp"), b_short_all=g("short leg", "all_announcers_bp"),
         b_long_sm=g("long leg", "size_matched_bp"), b_short_sm=g("short leg", "size_matched_bp"))
ll = BC[BC.cut.isin(["long leg", "short leg"])].pivot(index="strategy", columns="cut", values="size_matched_bp")
ratio = ll["long leg"] / ll["short leg"]
V["b_ratio_min"], V["b_ratio_max"] = f"{ratio.min():.1f}", f"{ratio.max():.1f}"

intro = (ROOT / "scripts" / "situations_intro.md").read_text(encoding="utf-8")
intro = re.sub(r"\{\{(\w+)\}\}", lambda m: V[m.group(1)], intro)   # KeyError = a placeholder with no computed value
body = "\n".join(card(n, m) for n, m in CARDS)
_max = [n for n in B.index if n.endswith("_max")]
_base = [n[:-4] for n in _max if n[:-4] in B.index]
MAXBP = (B.loc[_max].alpha_bp.min(), B.loc[_max].alpha_bp.max()); BASEBP = (B.loc[_base].alpha_bp.min(), B.loc[_base].alpha_bp.max())
_pl = lambda n: json.loads((R / n / "summary.json").read_text())["summary"]["pct_liquidity_capped"]
CAPPED_MAX, CAPPED_BASE = _pl("balanced_leverage_max"), _pl("balanced_leverage")
note = ("\n### Variants that share a book with the above\n\n"
        "* **Market Boost** trades exactly Balanced Leverage's positions (same table); its extra return comes from a separate half-size market position, not from any situation.\n"
        f"* **Sector Balanced Max, Balanced Leverage Max and Market Boost Max** hold an identical book (same positions, same {B.loc['balanced_leverage_max'].alpha_bp:.0f} bp per $); only the overlay differs.\n"
        f"* **Max versions of every strategy** keep the same situations but earn less alpha per dollar ({MAXBP[0]:.0f}-{MAXBP[1]:.0f} bp vs {BASEBP[0]:.0f}-{BASEBP[1]:.0f} bp for the base books), consistent with the 5%-of-volume trading limit binding more often at that size (Balanced Leverage Max: {CAPPED_MAX:.0%} of positions liquidity-capped vs {CAPPED_BASE:.0%} for Balanced Leverage; `pct_liquidity_capped` in each `summary.json`). That is an association, not a tested cause.\n"
        "* **Calm Weighted Filter, Volatility Filter, Calm Weighted Matched and Calm Rank Levered** look like their parents in every table; see `results/situations_all.csv`.\n")
(ROOT / "docs" / "SITUATIONS.md").write_text(intro + "\n## Strategy by strategy\n\n" + body + note, encoding="utf-8")
print("wrote docs/SITUATIONS.md")
