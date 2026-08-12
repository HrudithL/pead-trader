"""Generic PDF report renderer for pead-trading's phase reports.

Every phase of this project ends in a plain-language PDF report (per
CONTRIBUTING.md's reporting requirement and memory/phase1-equity-pead-plan.md's
"Reports" section): a research log crossed with a results tearsheet, not just
a chart dump -- a title, narrative sections explaining reasoning/decisions/
what was learned, and (chart, caption) pairs, laid out on A4 pages for a
reader with no finance background.

This module is that renderer, built once here (Slice 1) so the later
data-audit, earnings-data and event-study-result reports (and every future
phase's reports) call the same function instead of each hand-rolling PDF
generation. It has no data dependency of its own -- callers own their charts,
numbers and prose; this module only lays them out.

Implementation: plain Python f-strings build an HTML/CSS document (no
templating-library dependency -- jinja2 was considered and explicitly
declined per the plan's own dependency note, since string formatting is
simple enough for a template this small), then
`weasyprint.HTML(string=...).write_pdf(...)` rasterizes it to a real A4 PDF.
Matplotlib figures are embedded as base64-encoded PNG data URIs directly in
the HTML -- no temp image files to write or clean up, no relative-path/
base_url resolution to get wrong.

Public API: `build_pdf_report()`. See its docstring for the full contract.
"""

import base64
import datetime as dt
import html
import io
import os
import re
from collections.abc import Sequence
from pathlib import Path

import matplotlib.figure
from weasyprint import HTML

# Plain tuples, per the plan's own "(chart, caption) pairs" phrasing -- a
# two-field pair doesn't earn a dataclass wrapper. Named here so both this
# module's signature and callers' own type hints can refer to them.
NarrativeSection = tuple[str, str]  # (heading, body)
ChartCaption = tuple[matplotlib.figure.Figure, str]  # (figure, caption)

# Print-quality default. Raise for charts with a lot of fine detail (dense
# scatter, small tick labels); the acceptance bar is "readable, not
# stretched/cut off," not any specific resolution. Affects the raster
# sharpness and file size of the embedded PNG only -- not layout: every
# chart lays out at the full printable width regardless of dpi (see
# div.chart-block img in _CSS).
DEFAULT_CHART_DPI = 150

# @page size is fixed at A4 per the plan -- not a caller-configurable option,
# since "A4" is a hard requirement of every report this module renders, not a
# style preference.
_CSS = """
@page {
    size: A4;
    margin: 18mm 20mm;
    @bottom-center {
        content: "Page " counter(page) " of " counter(pages);
        font-size: 8pt;
        color: #8a8a8a;
    }
}

* {
    box-sizing: border-box;
}

body {
    font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
    color: #1a1a1a;
    font-size: 11pt;
    line-height: 1.5;
}

h1.report-title {
    font-size: 22pt;
    margin: 0 0 2mm 0;
    overflow-wrap: break-word;
}

p.generated-on {
    font-size: 9pt;
    color: #8a8a8a;
    margin: 0 0 8mm 0;
}

div.summary {
    font-size: 12.5pt;
    font-style: italic;
    color: #333333;
    border-left: 3px solid #cccccc;
    padding: 1mm 0 1mm 5mm;
    margin: 0 0 8mm 0;
    overflow-wrap: break-word;
}

div.summary p {
    margin: 0 0 2mm 0;
}

div.section {
    margin-bottom: 6mm;
}

h2.section-heading {
    font-size: 15pt;
    margin: 0 0 2mm 0;
    padding-bottom: 1mm;
    border-bottom: 1px solid #dddddd;
    overflow-wrap: break-word;
}

div.section-body {
    /* Matches div.summary/div.caption, which already set this on their
    container, so long unbroken tokens stay on-page even if the wrapper
    element changes later. */
    overflow-wrap: break-word;
}

div.section-body p {
    margin: 0 0 3mm 0;
    overflow-wrap: break-word;
}

div.chart-block {
    /* Best-effort: keeps a chart+caption together on one page as long as
    the block actually fits within a page's height. A block taller than one
    full page can still split -- max-height on the image below keeps that
    from happening in practice for any reasonably-sized source figure. */
    page-break-inside: avoid;
    margin: 8mm 0 10mm 0;
    text-align: center;
}

div.chart-block img {
    /* max-width relative to the content box (not a hardcoded mm value) so
    it always matches the real printable width regardless of @page margins.
    max-height is an explicit mm value, deliberately less than the ~261mm
    full content height, leaving room below for a normal-length caption on
    the same page -- a sufficiently long caption can still flow onto
    following page(s); no content is ever lost, but the chart+caption
    block is not guaranteed to stay on one page. width: 100% makes the
    chart's on-page size depend only on the printable width, not on dpi;
    object-fit: contain prevents stretching/cropping when max-height
    clamps a tall figure. */
    max-width: 100%;
    max-height: 180mm;
    width: 100%;
    object-fit: contain;
    height: auto;
    display: block;
    margin: 0 auto;
}

div.caption {
    font-size: 10pt;
    color: #444444;
    text-align: left;
    margin: 4mm 6mm 0 6mm;
    overflow-wrap: break-word;
}

div.caption p {
    margin: 0 0 2mm 0;
}
"""


def build_pdf_report(
    title: str,
    output_path: str | Path,
    *,
    summary: str | None = None,
    sections: Sequence[NarrativeSection] = (),
    charts: Sequence[ChartCaption] = (),
    dpi: int = DEFAULT_CHART_DPI,
) -> Path:
    """Render a title + narrative sections + (chart, caption) pairs to an A4 PDF.

    Parameters
    ----------
    title:
        Report title, rendered as the page-1 heading.
    output_path:
        Where to write the PDF (``str`` or ``Path``). Parent directories are
        created if they don't already exist.
    summary:
        Optional short lede paragraph rendered under the title, before any
        sections -- a one- or two-sentence "what this report found."
    sections:
        Ordered ``(heading, body)`` pairs -- the narrative/reasoning content
        (what was tried, decided, learned). ``body`` is plain text: blank
        lines separate paragraphs, single newlines become line breaks. No
        HTML needed or expected from callers. Rendered in order, before any
        charts.
    charts:
        Ordered ``(figure, caption)`` pairs. ``figure`` is any object with a
        matplotlib-compatible ``.savefig()`` (a ``matplotlib.figure.Figure``
        in practice). Each chart is rendered in its own region sized to fit
        one A4 page -- scaled to the full printable width, and scaled down
        further only if needed to fit the page height, aspect ratio always
        preserved, never stretched or cropped -- with its caption directly
        beneath it. Rendered in order, after all sections. This function
        does not close the figures it's given; the caller keeps ownership
        and should call ``plt.close(fig)`` itself afterward if generating
        many charts in one process.
    dpi:
        Raster resolution used when embedding each chart as a PNG.
        Controls the sharpness (and file size) of the embedded image only,
        NOT the chart's size on the page -- every chart lays out at full
        printable width with aspect ratio preserved regardless of dpi; a
        low dpi looks blurry, not small.

    Returns
    -------
    Path
        The resolved output path (the same location ``output_path`` names).

    Notes
    -----
    Every text field (title, summary, section heading/body, caption) is
    HTML-escaped before being embedded in the generated document, so
    special characters (``<``, ``&``, quotes, ...) in real report content
    can't corrupt the layout.
    """
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Built only after the mkdir above, so a bad output path fails fast
    # instead of paying for HTML/chart-rasterization work first.
    document_html = _build_html(
        title=title, summary=summary, sections=sections, charts=charts, dpi=dpi
    )
    # Render to a sibling temp path and atomically replace the destination,
    # so a failure or crash mid-render never leaves a truncated/corrupt PDF
    # at out_path -- readers either see the old file (if any) or the
    # complete new one, never a partial write. A sibling path (not
    # tempfile.mkstemp) guarantees the same filesystem, which os.replace
    # needs to be atomic; there are no concurrent writers to worry about.
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    try:
        HTML(string=document_html).write_pdf(str(tmp_path))
        os.replace(tmp_path, out_path)
    finally:
        # No-op if the replace above succeeded (nothing left at tmp_path);
        # cleans up the leftover partial file if the render or replace
        # failed instead.
        tmp_path.unlink(missing_ok=True)
    return out_path


def _build_html(
    *,
    title: str,
    summary: str | None,
    sections: Sequence[NarrativeSection],
    charts: Sequence[ChartCaption],
    dpi: int,
) -> str:
    """Build the full HTML document string. Split out from build_pdf_report()
    so tests can check escaping/structure directly against the HTML, without
    paying for a full PDF render on every assertion."""
    generated_on = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    summary_html = f'<div class="summary">{_escape_multiline(summary)}</div>' if summary else ""

    sections_html = "".join(
        '<div class="section">'
        f'<h2 class="section-heading">{html.escape(heading)}</h2>'
        f'<div class="section-body">{_escape_multiline(body)}</div>'
        "</div>"
        for heading, body in sections
    )

    charts_html = "".join(_chart_block_html(figure, caption, dpi) for figure, caption in charts)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<h1 class="report-title">{html.escape(title)}</h1>
<p class="generated-on">Generated {generated_on}</p>
{summary_html}
{sections_html}
{charts_html}
</body>
</html>"""


def _chart_block_html(figure: matplotlib.figure.Figure, caption: str, dpi: int) -> str:
    data_uri = _figure_to_png_data_uri(figure, dpi)
    alt_text = html.escape(caption) if caption.strip() else "Chart"
    return (
        '<div class="chart-block">'
        f'<img src="{data_uri}" alt="{alt_text}">'
        f'<div class="caption">{_escape_multiline(caption)}</div>'
        "</div>"
    )


def _figure_to_png_data_uri(figure: matplotlib.figure.Figure, dpi: int) -> str:
    """Render a matplotlib figure to a PNG and encode it as a base64 data
    URI, so it can be embedded directly in the HTML with no temp file."""
    with io.BytesIO() as buffer:
        figure.savefig(buffer, format="png", dpi=dpi, bbox_inches="tight")
        encoded = base64.b64encode(buffer.getbuffer()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _escape_multiline(text: str) -> str:
    """HTML-escape text and turn blank-line-separated paragraphs into <p>
    tags (single newlines become <br>), so callers can write plain, natural
    multi-paragraph prose without knowing any HTML. Line endings are
    normalized first (CRLF and bare CR both become LF), and a blank line
    counts as a paragraph separator even if it's whitespace-only (spaces or
    tabs), not just a fully empty line."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [p.strip() for p in re.split(r"\n[ \t]*\n", normalized.strip()) if p.strip()]
    rendered = []
    for paragraph in paragraphs:
        escaped = html.escape(paragraph).replace("\n", "<br>")
        rendered.append(f"<p>{escaped}</p>")
    return "".join(rendered)
