"""`<name>_legacy` twins of every equity strategy: the ORIGINAL leverage cap (new positions only).

Same weights and sizing as the base strategy, but `exposure_cap="entry_cohort"`: the leverage cap compares
only each quarter's NEW positions with the limit and ignores positions still open from earlier quarters,
which is how every result before the 2026-09 methodology fix was produced. Kept so those numbers (and the
reproduction tests against the original scripts' committed NAVs) stay reproducible; use the base strategy
for anything new. Status is "reference", same convention as `options_cap20_legacy`.

The twins are generated from the live registry, so this module imports every sibling strategy module first
(importing is idempotent) and then copies each equity strategy."""
import copy
import importlib
import pkgutil
from dataclasses import replace

import pead.strategies.equity as _pkg
from pead.strategies import registry

for _m in pkgutil.iter_modules(_pkg.__path__):
    if _m.name != __name__.rsplit(".", 1)[-1]:
        importlib.import_module(f"{_pkg.__name__}.{_m.name}")

for _base in [s for s in registry._REGISTRY.values()
              if s.asset_class == "equity" and not s.name.endswith("_legacy")]:
    _twin = copy.copy(_base)
    _twin.name = f"{_base.name}_legacy"
    _twin.parent = _base.name
    _twin.status = "reference"
    _twin.live_ready = False
    _twin.description = f"{_base.description} -- ORIGINAL leverage cap (new positions only, open book ignored)"
    _twin.sizing = replace(_base.sizing, exposure_cap="entry_cohort")
    registry.register_instance(_twin)
