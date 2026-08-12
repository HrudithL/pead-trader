# pead-trading

Research system for post-earnings-announcement drift (PEAD): determining whether the phenomenon
exists in equities and in options markets, and — only after it survives statistical and economic
validation — developing tradable equity and options strategies around it.

This is a research repository, not a live-trading system. See `CONTRIBUTING.md` for how work in
this repo is planned, branched, reviewed, and merged.

Project planning notes and working specs are kept in a local `memory/` directory, which is
gitignored and not part of this repository's history.

## Setup

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

One dependency needs a system-level prerequisite that plain `pip install` cannot satisfy on its
own: `weasyprint` (HTML/CSS-to-PDF rendering, used by `scripts/report_builder.py`) links against
Pango, Cairo and gdk-pixbuf. On macOS, install them first via Homebrew:

```
brew install pango cairo gdk-pixbuf
```

Without these, `pip install weasyprint` still succeeds, but importing it or calling
`HTML(...).write_pdf(...)` fails at render time. After installing, verify with:

```
.venv/bin/python -c "from weasyprint import HTML; HTML(string='<p>ok</p>').write_pdf('/tmp/_wp_check.pdf')"
```
