# Resuming the point-in-time option-spread tables (paused: OptionMetrics drive offline)

Status (2026-10-20): the paper's option-spread and option-coverage tables (Table 4 option columns, Table 1 option rows) are still the
PRE-correction tables from `data/` and are labelled as such in the paper. Rebuilding them on the corrected deciles is half done.

## What exists (nothing deleted)
Folder `C:\Users\hrudi\Documents\BEI\_pead_consolidation\vintage_paper\data2\` (isolated; the repo's `data/` was not touched):
- `event_options/decile_events_secid.parquet` and `metadata/om_trading_calendar.parquet`: copies of the corrected inputs (the former is byte-identical to `vintage_fixed`).
- Stage `select_entry_contracts`: COMPLETE. `entry_contracts_1996..2010, 2012, 2013.parquet` (17 years; 2011 has no option file) and `entry_contracts_all.parquet` = 124,686 rows. Log: `vintage_paper\entry.log`.
- Stage `forward_option_prices`: NOT STARTED in effect. It needed the OptionMetrics files and the drive disconnected, so it wrote no `forward_prices_<year>.parquet` and no `option_event_panel.parquet`. The process was stopped. Log of the failed attempt: `vintage_paper\fwd.log`.
- Not yet run: `pead/research/options_evidence/37_decile_summary_options.py`.

## Resume recipe (about 1 hour once D: is back; stage 1 was 9 minutes, stage 2 reads the same files)
```
set V=C:\Users\hrudi\Documents\BEI\_pead_consolidation\vintage_paper\data2
set PEAD_DATA_DIR=%V%
set PEAD_METADATA_DIR=%V%\metadata
set OPTIONMETRICS_DIR=D:/OptionMetrics/parquet
cd C:\Users\hrudi\Documents\BEI\_pead_consolidation\paper2
python pead/prep/options/forward_option_prices.py --jobs 3      # years already written are skipped; remaining: all 17
python pead/research/options_evidence/37_decile_summary_options.py   # writes %V%\option_decile_horizon_stats.csv, option_spread_horizon_stats.csv, option_coverage_stats.csv
```
Remaining year files for stage 2: forward_prices_1996 ... 2010, 2012, 2013 (17 files).
Then, in `pead/reporting/paper.py`: copy the three CSVs into `docs/paper/cache/pit3/` (extend `sync_pit2`), make `signal_evidence` read `osp`/`ocov` from there instead of `data/`, add a pre/post comparison table, delete the "pre-correction" captions and labels (Table 1 option rows, Table 4 caption, Section 4.6 heading and first paragraph, Section 11 item 3, Appendix B data-vintages paragraph), then
`python -m pead.reporting.paper` with `PEAD_MAIN_REPO=C:\Users\hrudi\Documents\BEI\PEAD_Trading`, and rewrite any sentence the new numbers contradict (the magnitude check of the call/stock ratio is generated).
Do not substitute the Kelly-picked forward prices: they are a different object from the near-ATM evidence panel.
