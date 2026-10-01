"""Charts shared by the single-strategy and comprehensive reports. Every function writes a PNG and
returns its path."""
import numpy as np
import pandas as pd

from pead.common.plotting import apply_style, plt
from pead.core.metrics import drawdown, yearly_table

COLORS = ["#1a3a2a", "#c0392b", "#2980b9", "#d4a017", "#7d3c98", "#16a085", "#e67e22", "#7f8c8d"]


def _prep():
    apply_style()


def market_series(dates):
    """Cumulative CRSP value-weighted market NAV on the given dates (starting at 1)."""
    from pead.common.paths import RAW_EQUITY_DIR
    m = pd.read_parquet(RAW_EQUITY_DIR / "market_benchmark.parquet", columns=["date", "vwretd"])
    m["date"] = pd.to_datetime(m["date"])
    r = m.drop_duplicates("date").set_index("date")["vwretd"].reindex(pd.DatetimeIndex(dates)).fillna(0.0)
    return r


def nav_chart(daily: pd.DataFrame, path, title, extra: dict = None, benchmark=True):
    _prep()
    fig, ax = plt.subplots(figsize=(9, 4.2))
    d = pd.DatetimeIndex(daily["date"])
    ax.plot(d, daily["nav"] / 1e7, color=COLORS[0], lw=1.8, label="strategy")
    if benchmark:
        mk = (1 + market_series(d)).cumprod()
        ax.plot(d, mk.to_numpy(), color="#999999", lw=1.2, label="market (CRSP VW)")
    for i, (k, s) in enumerate((extra or {}).items(), start=1):
        ax.plot(d, s, lw=1.1, color=COLORS[i % len(COLORS)], label=k)
    ax.set_yscale("log")
    ax.set_ylabel("growth of $1 (log)")
    ax.set_title(title)
    ax.legend(frameon=False, ncol=3)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def drawdown_chart(daily, path, title):
    _prep()
    fig, ax = plt.subplots(figsize=(9, 2.8))
    d = pd.DatetimeIndex(daily["date"])
    dd = drawdown(daily["nav"])
    ax.fill_between(d, dd * 100, 0, color="#c0392b", alpha=0.55)
    ax.set_ylabel("drawdown %")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def yearly_chart(daily, path, title):
    _prep()
    y = yearly_table(daily, 1e7)
    mk = market_series(daily["date"])
    mky = mk.groupby(mk.index.year).apply(lambda x: (1 + x).prod() - 1)
    fig, ax = plt.subplots(figsize=(9, 3.4))
    x = np.arange(len(y))
    ax.bar(x - 0.2, y["ret"] * 100, 0.4, color=COLORS[0], label="strategy")
    ax.bar(x + 0.2, [mky.get(int(v), np.nan) * 100 for v in y["year"]], 0.4, color="#bbbbbb", label="market")
    ax.set_xticks(x, y["year"].astype(int), rotation=60)
    ax.set_ylabel("calendar-year return %")
    ax.set_title(title)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def legs_chart(daily, path, title, long_col, short_col, long_label, short_label):
    _prep()
    fig, ax = plt.subplots(figsize=(9, 3.6))
    d = pd.DatetimeIndex(daily["date"])
    ax.plot(d, daily[long_col].cumsum() / 1e6, color=COLORS[0], label=long_label)
    ax.plot(d, daily[short_col].cumsum() / 1e6, color=COLORS[1], label=short_label)
    ax.plot(d, daily["daily_pnl"].cumsum() / 1e6, color="#222222", lw=2, label="total")
    ax.axhline(0, color="#888888", lw=0.8)
    ax.set_ylabel("cumulative P&L, $M")
    ax.set_title(title)
    ax.legend(frameon=False, ncol=3)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def bar_chart(df: pd.DataFrame, path, title, columns, labels=None, ylabel="", rotation=0, figsize=(9, 3.4)):
    _prep()
    fig, ax = plt.subplots(figsize=figsize)
    x = np.arange(len(df))
    w = 0.8 / len(columns)
    for i, c in enumerate(columns):
        ax.bar(x + (i - (len(columns) - 1) / 2) * w, df[c], w, color=COLORS[i % len(COLORS)],
               label=(labels or columns)[i])
    ax.axhline(0, color="#888888", lw=0.8)
    ax.set_xticks(x, [str(i) for i in df.index], rotation=rotation)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    if len(columns) > 1:
        ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def rolling_sharpe_chart(daily, path, title, window=252):
    _prep()
    r = pd.Series(daily["daily_return"].to_numpy(), index=pd.DatetimeIndex(daily["date"]))
    rs = r.rolling(window).mean() / r.rolling(window).std() * np.sqrt(252)
    fig, ax = plt.subplots(figsize=(9, 2.8))
    ax.plot(rs.index, rs, color=COLORS[2])
    ax.axhline(0, color="#888888", lw=0.8)
    ax.set_ylabel(f"rolling {window}d Sharpe")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def heatmap(df: pd.DataFrame, path, title, fmt="{:.2f}", cmap="RdYlGn", vmin=-1, vmax=1, figsize=(8, 6.5)):
    _prep()
    fig, ax = plt.subplots(figsize=figsize)
    im = ax.imshow(df.to_numpy(), cmap=cmap, vmin=vmin, vmax=vmax)
    ax.set_xticks(range(len(df.columns)), df.columns, rotation=70, ha="right", fontsize=8)
    ax.set_yticks(range(len(df.index)), df.index, fontsize=8)
    ax.grid(False)
    for i in range(len(df.index)):
        for j in range(len(df.columns)):
            ax.text(j, i, fmt.format(df.iat[i, j]), ha="center", va="center", fontsize=7)
    ax.set_title(title)
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def frontier_scatter(df: pd.DataFrame, path, title, x="annualized_vol", y="annualized_return", label="name",
                     color_by="asset_class", figsize=(9, 5.4)):
    _prep()
    fig, ax = plt.subplots(figsize=figsize)
    for i, (k, g) in enumerate(df.groupby(color_by)):
        ax.scatter(g[x] * 100, g[y] * 100, s=46, color=COLORS[i % len(COLORS)], label=k, zorder=3)
        for _, r in g.iterrows():
            ax.annotate(r[label], (r[x] * 100, r[y] * 100), fontsize=6.5, xytext=(3, 3), textcoords="offset points")
    ax.set_xlabel("annualized volatility %")
    ax.set_ylabel("annualized return %")
    ax.set_title(title)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path
