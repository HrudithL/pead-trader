"""One report definition, two outputs: Markdown (diff-friendly, tracked in git) and PDF (ReportLab).

    doc = Doc("Title")
    doc.h2("Section"); doc.p("text"); doc.bullets([...]); doc.table(df, fmt={...}); doc.image(path, "caption")
    doc.save(folder, "report")        # -> folder/report.md and folder/report.pdf
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, Spacer

from pead.common.report_pdf import ReportBuilder

PCT_COLS = ("return", "vol", "drawdown", "share", "rate", "frac", "contrib", "drag", "roi")
INT_COLS = {"decile", "trades", "positions", "size_quintile", "liquidity_quintile", "n", "n_days"}


def _fmt_cell(col, v, fmt):
    if isinstance(v, str):
        return v
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "-"
    if col in fmt:
        return fmt[col].format(v)
    if col == "year" and isinstance(v, (int, float, np.integer, np.floating)):
        return str(int(v))
    if col in INT_COLS and isinstance(v, (int, float, np.integer, np.floating)):
        return f"{int(v):,}"
    if isinstance(v, (int, np.integer)):
        return f"{v:,}"
    if isinstance(v, (float, np.floating)):
        lc = str(col).lower()
        if any(k in lc for k in PCT_COLS) and abs(v) < 20:
            return f"{v:.1%}"
        return f"{v:,.2f}"
    return str(v)


def _pretty_header(c):
    c = str(c)
    m = {"sharpe_1996_2001": "Sharpe 96-01", "sharpe_2002_2007": "Sharpe 02-07", "sharpe_2008_2013": "Sharpe 08-13",
         "annualized_return": "ann. return", "annualized_vol": "ann. vol", "max_drawdown": "max DD"}
    if c in m:
        return m[c]
    return c.replace("annualized", "ann.").replace("_", " ")


def _escape_pdf(t):
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`(.+?)`", r"<font face='Courier'>\1</font>", t)
    return t


def _is_num(c):
    return bool(re.fullmatch(r"[-+$]?[0-9,.]+[%MBKx]?", c.strip())) or c.strip() == "-"


class Doc:
    def __init__(self, title, subtitle=""):
        self.title, self.subtitle = title, subtitle
        self.items = []

    def h1(self, t): self.items.append(("h1", t))
    def h2(self, t): self.items.append(("h2", t))
    def h3(self, t): self.items.append(("h3", t))
    def p(self, t): self.items.append(("p", t))
    def bullets(self, lines): self.items.append(("bullets", list(lines)))
    def pagebreak(self): self.items.append(("break", None))

    def image(self, path, caption=""):
        self.items.append(("img", (str(path), caption)))

    def table(self, df: pd.DataFrame, fmt=None, index=True, caption=""):
        fmt = fmt or {}
        d = df.reset_index() if index else df.copy()
        rows = [[str(c) for c in d.columns]]
        for _, r in d.iterrows():
            rows.append([_fmt_cell(c, r[c], fmt) for c in d.columns])
        self.items.append(("table", (rows, caption)))

    # ---- renderers ------------------------------------------------------------------------------
    def to_markdown(self, rel_to: Path = None) -> str:
        out = [f"# {self.title}", ""]
        if self.subtitle:
            out += [f"_{self.subtitle}_", ""]
        for kind, v in self.items:
            if kind == "h1": out += [f"# {v}", ""]
            elif kind == "h2": out += [f"## {v}", ""]
            elif kind == "h3": out += [f"### {v}", ""]
            elif kind == "p": out += [v, ""]
            elif kind == "bullets": out += [f"- {b}" for b in v] + [""]
            elif kind == "img":
                p = Path(v[0])
                try:
                    p = p.relative_to(rel_to) if rel_to else p
                except ValueError:
                    pass
                out += [f"![{v[1]}]({str(p).replace(chr(92), '/')})", "", f"*{v[1]}*" if v[1] else "", ""]
            elif kind == "table":
                rows, cap = v
                out += ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
                out += ["| " + " | ".join(r) + " |" for r in rows[1:]] + [""]
                if cap:
                    out += [f"*{cap}*", ""]
        return "\n".join(out)

    def to_pdf(self, path: Path):
        rb = ReportBuilder()
        rb.story.append(Paragraph(_escape_pdf(self.title), rb.styles["H1"]))
        if self.subtitle:
            rb.story.append(Paragraph(_escape_pdf(self.subtitle), rb.styles["TitleSub"]))
        for kind, v in self.items:
            if kind == "h1": rb.h1(_escape_pdf(v))
            elif kind == "h2": rb.h2(_escape_pdf(v))
            elif kind == "h3": rb.h3(_escape_pdf(v))
            elif kind == "p": rb.body(_escape_pdf(v))
            elif kind == "bullets":
                for b in v:
                    rb.body("&bull; " + _escape_pdf(b))
            elif kind == "break": rb.story.append(PageBreak())
            elif kind == "img":
                rb.fig(v[0])
                if v[1]:
                    rb.caption(_escape_pdf(v[1]))
            elif kind == "table":
                rows, cap = v
                ncols = len(rows[0])
                width = 7.0 * inch
                lens = [min(max(max(len(r[j]) for r in rows[1:]) if len(rows) > 1 else 1, len(_pretty_header(rows[0][j])) * 0.75, 6), 30)
                        for j in range(ncols)]
                widths = [width * l / sum(lens) for l in lens]
                fs = 7.6 if ncols <= 8 else 6.4
                from reportlab.lib.styles import ParagraphStyle
                from reportlab.lib import colors
                body = ParagraphStyle("c", parent=rb.styles["Small"], fontSize=fs, leading=fs + 1.6)
                head = ParagraphStyle("h", parent=body, textColor=colors.white, fontName="Helvetica-Bold")
                cells = [[Paragraph(_escape_pdf(_pretty_header(c)), head) for c in rows[0]]]
                for r in rows[1:]:
                    cells.append([Paragraph(_escape_pdf(c), body) if (j == 0 or not _is_num(c)) else c for j, c in enumerate(r)])
                rb.story.append(rb.make_table(cells, col_widths=widths, fontsize=fs))
                rb.story.append(Spacer(1, 6))
                if cap:
                    rb.caption(_escape_pdf(cap))
        rb.save(path, self.title)

    def save(self, folder: Path, stem="report", pdf=True):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{stem}.md").write_text(self.to_markdown(rel_to=folder), encoding="utf-8")
        if pdf:
            self.to_pdf(folder / f"{stem}.pdf")
        return folder / f"{stem}.md"
