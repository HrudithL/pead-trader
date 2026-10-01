"""Shared ReportLab scaffolding for every PDF report script (legacy/05, equity_pead/32,
equity_strategy/30 and 31, options_pead/39). Each report previously copy-pasted its own
stylesheet and the same seven `story.append(...)` one-liner helpers; this factory returns the
same helpers bound to a fresh `story`/`styles` pair so each script's call sites
(`h1("...")`, `fig(path)`, `make_table(rows)`, ...) are unchanged.
"""
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak, HRFlowable,
    KeepTogether,
)

__all__ = ["new_report"]


def _build_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Body", parent=styles["Normal"], fontSize=9.7, leading=13.5, spaceAfter=8))
    styles.add(ParagraphStyle(name="H1", parent=styles["Heading1"], fontSize=16, spaceBefore=4, spaceAfter=8, textColor=colors.HexColor("#1a3a2a")))
    styles.add(ParagraphStyle(name="H2", parent=styles["Heading2"], fontSize=12.5, spaceBefore=14, spaceAfter=6, textColor=colors.HexColor("#1a3a2a")))
    styles.add(ParagraphStyle(name="H3", parent=styles["Heading3"], fontSize=10.8, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#2f4f3f")))
    styles.add(ParagraphStyle(name="Caption", parent=styles["Normal"], fontSize=8.3, leading=11, textColor=colors.HexColor("#555555"), spaceAfter=10))
    styles.add(ParagraphStyle(name="TitleSub", parent=styles["Normal"], fontSize=11, textColor=colors.HexColor("#555555"), spaceAfter=4))
    styles.add(ParagraphStyle(name="Small", parent=styles["Normal"], fontSize=8.3, leading=11.5))
    styles.add(ParagraphStyle(name="Glossary", parent=styles["Normal"], fontSize=9.0, leading=12.5,
                               textColor=colors.HexColor("#1a3a2a"), backColor=colors.HexColor("#f0f4f1"),
                               borderColor=colors.HexColor("#c8d8cc"), borderWidth=0.6, borderPadding=8,
                               spaceAfter=10))
    return styles


class ReportBuilder:
    """Holds one report's `story`/`styles` state; call `.save(out_path, title)` at the end."""

    def __init__(self, fig_width=6.4 * inch):
        self.story = []
        self.styles = _build_styles()
        self.default_fig_width = fig_width

    def h1(self, t):
        self.story.append(Paragraph(t, self.styles["H1"]))

    def h2(self, t):
        self.story.append(Paragraph(t, self.styles["H2"]))

    def h3(self, t):
        self.story.append(Paragraph(t, self.styles["H3"]))

    def body(self, t):
        self.story.append(Paragraph(t, self.styles["Body"]))

    def caption(self, t):
        self.story.append(Paragraph(t, self.styles["Caption"]))

    def glossary(self, t):
        self.story.append(Paragraph(t, self.styles["Glossary"]))

    def rule(self):
        self.story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#bbbbbb"),
                                      spaceBefore=4, spaceAfter=10))

    def fig(self, path, width=None):
        img = Image(str(path))
        ratio = img.imageHeight / img.imageWidth
        w = self.default_fig_width if width is None else width
        img.drawWidth = w
        img.drawHeight = w * ratio
        self.story.append(img)

    def make_table(self, rows, col_widths=None, header_bg="#1a3a2a", fontsize=8.3, align_first_left=True):
        t = Table(rows, colWidths=col_widths, hAlign="LEFT")
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), fontsize),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f5")]),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ]
        if align_first_left:
            style.append(("ALIGN", (0, 0), (0, -1), "LEFT"))
        t.setStyle(TableStyle(style))
        return t

    def save(self, out_path, title):
        doc = SimpleDocTemplate(str(out_path), pagesize=letter,
                                 leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                                 topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                                 title=title)
        doc.build(self.story)


def new_report(fig_width=6.4 * inch):
    """Return (rb, story, styles, h1, h2, h3, body, caption, glossary, rule, fig, make_table)
    bound to one fresh report -- unpack only the names a given script needs, e.g.:

        rb, story, styles, h1, h2, body, caption, rule, fig, make_table = new_report()[:10]
    """
    rb = ReportBuilder(fig_width=fig_width)
    return (rb, rb.story, rb.styles, rb.h1, rb.h2, rb.h3, rb.body, rb.caption, rb.glossary,
            rb.rule, rb.fig, rb.make_table)
