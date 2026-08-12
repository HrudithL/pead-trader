"""Tests for scripts/report_builder.py -- the shared PDF report renderer.

Two layers, deliberately: fast tests against `_build_html()` (string-level
checks on escaping/structure, no PDF render) and slower end-to-end tests
against the public `build_pdf_report()` (real weasyprint render, checked via
the PDF magic bytes and a non-trivial file size). Both matter -- the HTML
layer catches logic bugs cheaply, but only a real render proves weasyprint
actually accepts the generated document.
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: no test here needs an on-screen window

import matplotlib.pyplot as plt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import report_builder as rb  # noqa: E402

PDF_MAGIC = b"%PDF"


def _make_figure(label: str = "x") -> plt.Figure:
    fig, ax = plt.subplots(figsize=(4, 2))
    ax.plot([0, 1, 2], [0, 1, 0])
    ax.set_title(label)
    return fig


# --- End-to-end: build_pdf_report() produces a real, valid PDF -------------


def test_minimal_report_produces_valid_pdf(tmp_path):
    """Title only, no summary/sections/charts -- the smallest possible call
    still has to produce a real, openable PDF, not an empty or broken file."""
    out = rb.build_pdf_report("Minimal Report", tmp_path / "minimal.pdf")

    assert out.exists()
    assert out.stat().st_size > 0
    assert out.read_bytes()[:4] == PDF_MAGIC


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


def test_does_not_close_caller_figures(tmp_path):
    """Documented contract: build_pdf_report() does not take ownership of
    the figures it's given. The caller's Figure must still be usable
    afterward (e.g. to save it separately, or close it on its own terms)."""
    fig = _make_figure()
    try:
        rb.build_pdf_report("Ownership Check", tmp_path / "ownership.pdf", charts=[(fig, "cap")])
        # If build_pdf_report had closed the figure, this would raise.
        assert plt.fignum_exists(fig.number)
    finally:
        plt.close(fig)


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


def test_summary_and_caption_are_div_not_p():
    """Regression test: _escape_multiline() already wraps its output in <p>
    tags, so the summary/caption containers must be <div>, not <p> -- a <p>
    cannot validly contain a nested <p>, and an HTML parser will silently
    auto-close the outer tag, dropping its class/styling entirely."""
    fig = _make_figure()
    try:
        doc = rb._build_html(
            title="T",
            summary="A summary.",
            sections=[],
            charts=[(fig, "A caption.")],
            dpi=100,
        )
    finally:
        plt.close(fig)

    assert '<div class="summary">' in doc
    assert '<p class="summary">' not in doc
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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
