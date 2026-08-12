"""Tests for scripts/report_builder.py -- the shared PDF report renderer.

Two layers, deliberately: fast tests against `_build_html()` (string-level
checks on escaping/structure, no PDF render) and slower end-to-end tests
against the public `build_pdf_report()` (real weasyprint render, checked via
the PDF magic bytes and a non-trivial file size). Both matter -- the HTML
layer catches logic bugs cheaply, but only a real render proves weasyprint
actually accepts the generated document.
"""

import io
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: no test here needs an on-screen window

import matplotlib.pyplot as plt
import pytest
import weasyprint

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import report_builder as rb  # noqa: E402

PDF_MAGIC = b"%PDF"


def _make_figure(label: str = "x", figsize: tuple[float, float] = (4, 2)) -> plt.Figure:
    fig, ax = plt.subplots(figsize=figsize)
    ax.plot([0, 1, 2], [0, 1, 0])
    ax.set_title(label)
    return fig


# --- End-to-end: build_pdf_report() produces a real, valid PDF -------------


def test_minimal_report_produces_valid_pdf(tmp_path):
    """Title only, no summary/sections/charts -- the smallest possible call
    still has to produce a real, openable PDF, not an empty, broken, or
    truncated file."""
    out = rb.build_pdf_report("Minimal Report", tmp_path / "minimal.pdf")

    assert out.exists()
    assert out.stat().st_size > 0
    data = out.read_bytes()
    assert data[:4] == PDF_MAGIC
    # A PDF whose bytes were cut off mid-write (e.g. by a crash) would be
    # missing its trailer -- a valid PDF always ends with %%EOF.
    assert data.rstrip().endswith(b"%%EOF")


def test_multiple_sections_and_charts(tmp_path):
    """The module's whole reason for existing: several narrative sections
    plus several charts, all landing in one PDF."""
    figs = [_make_figure(f"chart {i}") for i in range(3)]
    try:
        out = rb.build_pdf_report(
            "Multi-Content Report",
            tmp_path / "multi.pdf",
            summary="A short lede paragraph.",
            sections=[
                ("First section", "First body."),
                ("Second section", "Second body.\n\nWith a second paragraph."),
                ("Third section", "Third body."),
            ],
            charts=[(figs[0], "Caption one."), (figs[1], "Caption two."), (figs[2], "Caption three.")],
        )
        assert out.read_bytes()[:4] == PDF_MAGIC
        # A report with real content should be meaningfully larger than the
        # minimal-report case -- a cheap proxy for "the charts actually got
        # embedded" without needing a PDF-parsing dependency.
        minimal = rb.build_pdf_report("Minimal", tmp_path / "minimal_cmp.pdf")
        assert out.stat().st_size > minimal.stat().st_size * 2
    finally:
        for fig in figs:
            plt.close(fig)


def test_zero_charts(tmp_path):
    """Sections-only report (no charts) must still render cleanly."""
    out = rb.build_pdf_report(
        "Sections Only",
        tmp_path / "sections_only.pdf",
        sections=[("Heading", "Body text.")],
        charts=[],
    )
    assert out.read_bytes()[:4] == PDF_MAGIC


def test_zero_sections(tmp_path):
    """Charts-only report (no narrative sections) must still render cleanly."""
    fig = _make_figure()
    try:
        out = rb.build_pdf_report(
            "Charts Only",
            tmp_path / "charts_only.pdf",
            sections=[],
            charts=[(fig, "The only content is this chart.")],
        )
        assert out.read_bytes()[:4] == PDF_MAGIC
    finally:
        plt.close(fig)


def test_zero_sections_and_zero_charts(tmp_path):
    """Title (+ optional summary) with nothing else is a degenerate but
    valid input -- must not crash on empty lists."""
    out = rb.build_pdf_report(
        "Just A Title",
        tmp_path / "title_only.pdf",
        summary="Just a summary too.",
        sections=[],
        charts=[],
    )
    assert out.read_bytes()[:4] == PDF_MAGIC


def test_very_long_caption(tmp_path):
    """A long, multi-hundred-word caption -- including one pathologically
    long unbroken token with no whitespace -- must still render without
    error (wrapping is a CSS concern, not a crash risk, but a naive
    implementation could still choke on it)."""
    fig = _make_figure()
    long_caption = ("This is a very long caption. " * 80) + ("x" * 300)
    try:
        out = rb.build_pdf_report(
            "Long Caption Report",
            tmp_path / "long_caption.pdf",
            charts=[(fig, long_caption)],
        )
        assert out.read_bytes()[:4] == PDF_MAGIC
    finally:
        plt.close(fig)


def test_creates_parent_directories(tmp_path):
    """Callers (future report scripts) shouldn't have to remember to mkdir
    their own output directory first."""
    nested = tmp_path / "a" / "b" / "c" / "report.pdf"
    assert not nested.parent.exists()

    out = rb.build_pdf_report("Nested Output", nested)

    assert out == nested
    assert out.read_bytes()[:4] == PDF_MAGIC


def test_accepts_str_output_path(tmp_path):
    """output_path must work as a plain str, not just a Path."""
    out = rb.build_pdf_report("Str Path", str(tmp_path / "str_path.pdf"))
    assert isinstance(out, Path)
    assert out.read_bytes()[:4] == PDF_MAGIC


def test_does_not_mutate_or_close_caller_figures(tmp_path):
    """Documented contract: build_pdf_report() does not take ownership of
    the figures it's given. The caller's Figure must still be usable
    afterward (e.g. to save it separately, or close it on its own terms),
    and must come back byte-for-byte identical -- not resized, not
    re-rasterized at a different dpi, not otherwise mutated by having been
    rendered into a report at some other dpi."""
    fig = _make_figure()
    try:
        size_before = fig.get_size_inches().copy()
        dpi_before = fig.get_dpi()
        with io.BytesIO() as buf:
            fig.savefig(buf, format="png")
            png_before = buf.getvalue()

        rb.build_pdf_report("Ownership Check", tmp_path / "ownership.pdf", charts=[(fig, "cap")])

        # If build_pdf_report had closed the figure, this would raise.
        assert plt.fignum_exists(fig.number)
        assert (fig.get_size_inches() == size_before).all()
        assert fig.get_dpi() == dpi_before
        with io.BytesIO() as buf:
            fig.savefig(buf, format="png")
            png_after = buf.getvalue()
        assert png_after == png_before
    finally:
        plt.close(fig)


def test_atomic_write_preserves_original_on_mid_render_failure(tmp_path, monkeypatch):
    """A crash partway through rendering must never corrupt or truncate an
    existing report, and must never leave a stray .tmp file behind.
    Regression test for build_pdf_report()'s render-to-sibling-temp-path +
    os.replace atomic-write ordering."""
    out_path = tmp_path / "report.pdf"
    good = rb.build_pdf_report("Good Report", out_path)
    original_bytes = good.read_bytes()
    assert original_bytes[:4] == PDF_MAGIC

    class _ExplodingHTML:
        """Stand-in for weasyprint.HTML: write_pdf() drops a short garbage
        file at whatever path it's given (simulating a renderer that dies
        partway through writing), then raises."""

        def __init__(self, *args, **kwargs):
            pass

        def write_pdf(self, target_path, *args, **kwargs):
            Path(target_path).write_bytes(b"%PDF-garbage-not-a-real-pdf")
            raise RuntimeError("simulated mid-render crash")

    monkeypatch.setattr(rb, "HTML", _ExplodingHTML)

    with pytest.raises(RuntimeError, match="simulated mid-render crash"):
        rb.build_pdf_report("Good Report", out_path)

    # The exception must propagate (checked above via pytest.raises) without
    # disturbing the pre-existing file: still the original bytes...
    assert out_path.read_bytes() == original_bytes
    # ...and no leftover temp file from the failed attempt.
    assert not out_path.with_name(out_path.name + ".tmp").exists()


# --- HTML-level checks (fast, no PDF render) --------------------------------


def test_html_escapes_special_characters_in_title():
    doc = rb._build_html(
        title="<script>alert(1)</script>", summary=None, sections=[], charts=[], dpi=100
    )
    assert "<script>alert(1)</script>" not in doc
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in doc


def test_html_escapes_special_characters_in_sections_and_summary():
    doc = rb._build_html(
        title="Title",
        summary="Summary with <b>bold</b> & an ampersand.",
        sections=[("Heading & <tag>", "Body with <i>italic</i> & more.")],
        charts=[],
        dpi=100,
    )
    assert "<b>bold</b>" not in doc
    assert "<i>italic</i>" not in doc
    assert "Heading &amp; &lt;tag&gt;" in doc
    assert "&lt;b&gt;bold&lt;/b&gt;" in doc


def test_multiline_text_containers_are_div_not_p():
    """Regression test: _escape_multiline() already wraps its output in <p>
    tags, so every container it's embedded in -- summary, section-body, and
    caption (three call sites) -- must be <div>, not <p> -- a <p> cannot
    validly contain a nested <p>, and an HTML parser will silently
    auto-close the outer tag, dropping its class/styling entirely."""
    fig = _make_figure()
    try:
        doc = rb._build_html(
            title="T",
            summary="A summary.",
            sections=[("Heading", "A body.")],
            charts=[(fig, "A caption.")],
            dpi=100,
        )
    finally:
        plt.close(fig)

    assert '<div class="summary">' in doc
    assert '<p class="summary">' not in doc
    assert '<div class="section-body">' in doc
    assert '<p class="section-body">' not in doc
    assert '<div class="caption">' in doc
    assert '<p class="caption">' not in doc


def test_multiparagraph_body_produces_multiple_paragraphs():
    doc = rb._build_html(
        title="T",
        summary=None,
        sections=[("H", "First paragraph.\n\nSecond paragraph.\n\nThird paragraph.")],
        charts=[],
        dpi=100,
    )
    assert doc.count("<p>") == 3
    assert "First paragraph.</p>" in doc
    assert "Second paragraph.</p>" in doc
    assert "Third paragraph.</p>" in doc


def test_single_newline_becomes_br_not_new_paragraph():
    doc = rb._build_html(
        title="T",
        summary=None,
        sections=[("H", "Line one.\nLine two, same paragraph.")],
        charts=[],
        dpi=100,
    )
    assert doc.count("<p>") == 1
    assert "Line one.<br>Line two, same paragraph." in doc


def test_no_charts_or_sections_omits_empty_containers():
    """Zero-length lists shouldn't leave behind empty section/chart divs."""
    doc = rb._build_html(title="T", summary=None, sections=[], charts=[], dpi=100)
    assert '<div class="section">' not in doc
    assert '<div class="chart-block">' not in doc


# --- _escape_multiline(): paragraph-splitting edge cases --------------------


@pytest.mark.parametrize(
    "text, expected_p_count",
    [
        pytest.param("Para one.\r\n\r\nPara two.", 2, id="crlf_blank_line"),
        pytest.param("Para one.\r\rPara two.", 2, id="bare_cr_blank_line"),
        pytest.param("Para one.\n   \nPara two.", 2, id="space_only_blank_line"),
        pytest.param("Para one.\n\t\nPara two.", 2, id="tab_only_blank_line"),
        pytest.param("A.\n\n\nB.", 2, id="triple_newline_still_two_paragraphs"),
        pytest.param("Line one.\nLine two.", 1, id="single_newline_same_paragraph"),
    ],
)
def test_paragraph_splitting_edge_cases(text, expected_p_count):
    """Line endings are normalized (CRLF or bare CR -> LF) before splitting,
    and a blank separator line counts even if it's whitespace-only (spaces
    or tabs), not just a bare empty line. No literal \\r may survive into
    the output regardless of which line-ending style came in."""
    doc = rb._escape_multiline(text)
    assert doc.count("<p>") == expected_p_count
    assert "\r" not in doc


def test_single_newline_becomes_br_within_one_paragraph():
    doc = rb._escape_multiline("Line one.\nLine two.")
    assert doc.count("<p>") == 1
    assert "Line one.<br>Line two." in doc


def test_empty_and_whitespace_only_input_produce_nothing():
    assert rb._escape_multiline("") == ""
    assert rb._escape_multiline("   \n  ") == ""


def test_paragraph_splitting_indented_triple_quoted_blank_line():
    """The realistic case this bug actually bit: a caller writes `body` as
    an indented triple-quoted string in their own report script, and an
    editor's auto-indent (or a human matching indentation for visual
    symmetry) leaves the "blank" line between paragraphs carrying the
    surrounding code's indentation whitespace rather than being a bare
    empty line. The blank line's spaces are written as \\x20 escapes
    (rather than literal trailing whitespace) so they can't be silently
    stripped by an editor or formatter before this test ever runs."""
    indented_body = """First paragraph.
\x20\x20\x20\x20
    Second paragraph."""
    assert "\n    \n" in indented_body  # sanity check: the blank line really is whitespace-only

    doc = rb._escape_multiline(indented_body)
    assert doc.count("<p>") == 2
    assert "\r" not in doc
    assert "First paragraph.</p>" in doc
    assert "Second paragraph.</p>" in doc


# --- Chart embedding and caption handling -----------------------------------


def test_chart_is_embedded_as_base64_png_with_alt_text():
    """The <img> tag must carry an actual base64-encoded PNG data URI (not
    a file path or an empty src), and the escaped caption text must appear
    as its alt attribute."""
    fig = _make_figure()
    try:
        doc = rb._build_html(
            title="T",
            summary=None,
            sections=[],
            charts=[(fig, "A <special> caption.")],
            dpi=100,
        )
    finally:
        plt.close(fig)

    assert '<img src="data:image/png;base64,' in doc
    assert 'alt="A &lt;special&gt; caption."' in doc


def test_whitespace_only_caption_behaves_like_empty():
    """A whitespace-only caption must behave exactly like an empty one --
    gating on the raw (unstripped) string would leave a spurious alt text
    while still rendering an empty caption div, an inconsistent result."""
    fig = _make_figure()
    try:
        doc = rb._build_html(
            title="T", summary=None, sections=[], charts=[(fig, "   ")], dpi=100
        )
    finally:
        plt.close(fig)

    assert 'alt="Chart"' in doc
    assert '<div class="caption"></div>' in doc


def test_whitespace_only_summary_behaves_like_empty():
    """Same gap as the caption case above, found in round-2 review: gating
    summary_html on truthiness rather than .strip() let a whitespace-only
    summary through, emitting an empty <div class="summary"> that still
    paints its CSS border/padding as a visible stray artifact with no
    content -- exactly the kind of "one call site fixed, a twin site
    missed" bug this module has already had twice."""
    doc_whitespace = rb._build_html(
        title="T", summary="   \n\t  ", sections=[], charts=[], dpi=100
    )
    doc_empty = rb._build_html(title="T", summary="", sections=[], charts=[], dpi=100)
    doc_none = rb._build_html(title="T", summary=None, sections=[], charts=[], dpi=100)

    assert '<div class="summary">' not in doc_whitespace
    assert doc_whitespace == doc_empty == doc_none


def test_empty_section_heading_omits_heading_element():
    """Third instance of the same gap (round-3 review finding), on
    h2.section-heading this time: it carries padding-bottom/border-bottom,
    so an empty or whitespace-only heading must be omitted entirely rather
    than rendered empty, or it paints a stray full-width rule with no text
    above it. The section body must still render regardless."""
    doc_empty = rb._build_html(
        title="T", summary=None, sections=[("", "A body.")], charts=[], dpi=100
    )
    doc_whitespace = rb._build_html(
        title="T", summary=None, sections=[("   \n\t ", "A body.")], charts=[], dpi=100
    )
    doc_real = rb._build_html(
        title="T", summary=None, sections=[("Heading", "A body.")], charts=[], dpi=100
    )

    for doc in (doc_empty, doc_whitespace):
        assert "<h2" not in doc
        assert '<div class="section-body">' in doc  # body still renders

    assert doc_empty == doc_whitespace
    assert "<h2" in doc_real


def test_chart_css_preserves_aspect_ratio_declaration():
    """Regression guard for the exact mutation round-2 review demonstrated:
    deleting `object-fit: contain` from the chart-image CSS rule leaves
    every existing test green (nothing exercises a portrait/tall figure,
    the one case where max-height's clamp actually engages and width:100%
    alone would stretch it), yet silently reintroduces the stretching bug
    width:100% was added to fix. This can't be caught by rendering a
    landscape figure -- box-tree geometry isn't part of weasyprint's public
    render() API -- so it's pinned at the CSS-declaration level instead.

    max-height is included too (round-3 review finding): it's the reason
    object-fit is needed at all (without a height clamp, nothing to
    letterbox), and deleting it alone was equally undetected by the rest
    of the suite -- a portrait figure would lay out full-height instead of
    being clamped, defeating page-break-inside: avoid."""
    assert "object-fit: contain;" in rb._CSS
    assert "width: 100%;" in rb._CSS
    assert "max-height: 180mm;" in rb._CSS


def test_dpi_actually_changes_embedded_image_data():
    """test_chart_layout_is_dpi_independent only proves *page layout* is
    dpi-invariant -- it would pass just as well if dpi were silently
    ignored by _figure_to_png_data_uri entirely (round-3 review finding:
    confirmed by deleting `dpi=dpi` from the savefig() call, which left
    the full suite green). This test pins that dpi is actually threaded
    through to the raster: a higher dpi must produce a larger encoded PNG
    for the same figure."""
    fig = _make_figure(figsize=(4, 3))
    try:
        low = rb._figure_to_png_data_uri(fig, dpi=60)
        high = rb._figure_to_png_data_uri(fig, dpi=300)
    finally:
        plt.close(fig)

    assert len(high) > len(low) * 2


# --- Rendering-level checks: real weasyprint render, page geometry ---------


def test_chart_layout_is_dpi_independent():
    """Regression test for the div.chart-block img CSS fix (width: 100% +
    object-fit: contain instead of width: auto): a chart's on-page size
    -- and therefore how many pages a report needs -- must depend only on
    the printable width, never on dpi. Before the fix, a higher dpi made
    weasyprint's auto-width calculation lay the same figure out larger on
    the page, changing the page count for identical content."""
    figs = [_make_figure(f"chart {i}", figsize=(7, 4)) for i in range(3)]
    try:
        charts = [(figs[i], f"Caption {i}.") for i in range(3)]
        doc_low_dpi = rb._build_html(
            title="DPI Check", summary=None, sections=[], charts=charts, dpi=60
        )
        doc_high_dpi = rb._build_html(
            title="DPI Check", summary=None, sections=[], charts=charts, dpi=300
        )
    finally:
        for fig in figs:
            plt.close(fig)

    pages_low = weasyprint.HTML(string=doc_low_dpi).render().pages
    pages_high = weasyprint.HTML(string=doc_high_dpi).render().pages
    assert len(pages_low) == len(pages_high)


def test_a4_geometry_and_page_count():
    """weasyprint's own render().pages gives exact page geometry and count
    directly -- no PDF-parsing dependency needed. A minimal report is a
    single A4 page (210mm x 297mm, at the CSS-standard 96px/inch); a report
    with enough charts must overflow onto more than one page."""
    minimal_doc = rb._build_html(title="T", summary=None, sections=[], charts=[], dpi=100)
    pages = weasyprint.HTML(string=minimal_doc).render().pages

    assert len(pages) == 1
    assert pages[0].width / 96 * 25.4 == pytest.approx(210, abs=0.1)
    assert pages[0].height / 96 * 25.4 == pytest.approx(297, abs=0.1)

    figs = [_make_figure(f"chart {i}") for i in range(3)]
    try:
        charts = [(figs[i], f"Caption {i}.") for i in range(3)]
        multi_doc = rb._build_html(
            title="T", summary=None, sections=[], charts=charts, dpi=100
        )
    finally:
        for fig in figs:
            plt.close(fig)

    multi_pages = weasyprint.HTML(string=multi_doc).render().pages
    assert len(multi_pages) >= 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
