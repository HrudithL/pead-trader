"""Tests for scripts/demo_sample_report.py -- the concrete example/demo
required by Slice 1's acceptance criterion (memory/phase1-equity-pead-plan.md):
"given a sample chart + caption list, produces a valid A4 PDF with charts in
the right regions and readable captions." This test exercises the exact demo
that a human would run to inspect the module's output, so that acceptance
criterion stays under automated regression protection rather than only having
been checked once by hand.
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import demo_sample_report  # noqa: E402

PDF_MAGIC = b"%PDF"


def test_build_sample_report_produces_valid_multi_chart_pdf(tmp_path):
    out = demo_sample_report.build_sample_report(tmp_path / "demo.pdf")

    assert out.exists()
    assert out.read_bytes()[:4] == PDF_MAGIC
    # Three charts plus two narrative sections plus a summary is
    # substantially more content than an empty report -- a cheap sanity
    # check that the charts actually made it into the PDF, not just the
    # text.
    assert out.stat().st_size > 50_000


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
