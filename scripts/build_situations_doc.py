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

intro = (ROOT / "scripts" / "situations_intro.md").read_text(encoding="utf-8")
body = "\n".join(card(n, m) for n, m in CARDS)
note = ("\n### Variants that share a book with the above\n\n"
        "* **Market Boost** trades exactly Balanced Leverage's positions (same table); its extra return comes from a separate half-size market position, not from any situation.\n"
        "* **Sector Balanced Max, Balanced Leverage Max and Market Boost Max** hold an identical book (same positions, same 36 bp per $); only the overlay differs.\n"
        "* **Max versions of every strategy** keep the same situations but earn roughly half the alpha per dollar (31-58 bp vs 60-97 bp), consistent with the 5%-of-volume trading limit binding about four times as often at that size and pushing money out of the thinnest stocks.\n"
        "* **Calm Weighted Filter, Volatility Filter, Calm Weighted Matched and Calm Rank Levered** look like their parents in every table; see `results/situations_all.csv`.\n")
(ROOT / "docs" / "SITUATIONS.md").write_text(intro + "\n## Strategy by strategy\n\n" + body + note, encoding="utf-8")
print("wrote docs/SITUATIONS.md")
