# Incomplete run — do not use as final results

This directory contains artifacts from an earlier event-collision filter that was applied only after restricting the event table to extreme SUE deciles. That policy could retain one row from a repeated `(secid, day0_date)` source cluster when its partner had a middle SUE decile.

The associated daily 54-cell run was interrupted after 13 of 54 cells when that flaw was discovered. It has no complete daily portfolio grid. Its settlement artifacts are also from the superseded narrow-filter pass and must not be used as the final results.

Use `../options_expiry_grid_full_source_collision_cleaned/` instead. The final policy removes every source row in all repeated security/date clusters before applying the decile filter. Its audit records 147,142 input rows, 147,115 retained rows, 13 clusters and 27 source rows removed.
