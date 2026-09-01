# Reference material from `Options_Content`

These files are copied (not symlinked/junctioned) from a separate personal folder,
`C:\Users\hrudi\Documents\BEI\Options_Content`, outside this git-tracked repo -- copied in here so
everything the README's "Options-strategy GPU roadmap" section cites as prior-art inspiration for
scripts 40+ actually lives inside this repo, rather than the README pointing at a folder that
isn't part of this project.

**These are reference/inspiration material only, not part of the active pipeline.** Nothing under
`scripts/` imports or runs anything in this directory. They're kept as-is (not adapted to this
project's data/conventions) so the original GPU/ML patterns they demonstrate stay legible:

- `MSTR_Options_Storage/panel_model.py`, `optuna_search_all.py`, `optuna_search_parallel.py`,
  `train_one_config.py` -- a PyTorch cross-attention transformer + Optuna hyperparameter-search
  harness (GPU-sequential and CPU-parallel-trials variants). The Optuna search *pattern* (sweep
  data + model hyperparameters together, `MedianPruner`, early stopping on held-out RMSE) is what
  `scripts/44_ml_contract_selector.py` reuses -- the model itself is a plain feed-forward net
  there, not this transformer, since the OptionMetrics event panel is one row per earnings event
  rather than a per-timestep series with "K other contracts" to attend over.
- `v0.81_gpuOptionMetrics/backtest_optionmetrics_parquet.py`, `OptionsReturnOCM_v08_1_gpu.py` --
  reads the same OptionMetrics IvyDB `opprcd`/`secprd` parquet schema this project's
  `scripts/options_lib.py` does, with a RAPIDS (cudf/cupy) vectorized-join layer on top.
- `v0.81p_gpu/parallel.py` -- `super_return_fast()`, a dense (contracts x trading-days) `cupy`
  array scan to find the best buy/sell pair. This is the direct ancestor of the "batch a parameter
  grid as an extra tensor axis" pattern used in `scripts/42_gpu_exit_optimizer.py` and
  `scripts/43_gpu_param_sweep.py`.

**Redaction note:** `v0.81p_gpu/parallel.py` and `v0.81_gpuOptionMetrics/OptionsReturnOCM_v08_1_gpu.py`
originally contained a hardcoded, live-looking Tradier API bearer token in plaintext. It has been
replaced with `REDACTED_SEE_ROTATE_THIS_TOKEN` in both copies here before they entered this
git-tracked repo. The same token is still hardcoded in plaintext across roughly 20 files in the
original `Options_Content` folder (outside this repo) -- if that token is still active, it should
be rotated, and future scripts there should read it from an environment variable instead of
hardcoding it, independent of anything in this repo.
