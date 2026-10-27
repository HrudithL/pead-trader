# AGENTS.md

This file is the shared memory for every AI tool working in this repo — Claude Code,
Codex, and any other agent that reads `AGENTS.md`. Keep it in git, at the repo root,
so every clone, branch, and `git worktree` checkout sees the same instructions. Don't
fork a separate copy per tool; if a tool needs its own entrypoint (e.g. Claude Code's
`CLAUDE.md`), make that entrypoint a one-line pointer to this file instead of
duplicating content.

## Two-model workflow: plan with the strong model, execute with the local worker

This repo uses a two-tier setup to avoid burning expensive model tokens/time on
mechanical work:

- **Planner** = whichever strong model is driving (Claude, Codex, etc.). Responsible
  for understanding the problem, deciding the approach, and reviewing the result.
- **Worker** = a local Ollama model (`qwen2.5-coder:7b` by default) running on this
  machine. Responsible for mechanical implementation once the planner has already
  made the decisions.

### When the planner should delegate to the worker

Delegate once you (the planner) have already done the thinking and can write an
unambiguous spec — the worker should never have to make a design decision. Good
candidates:
- Implementing a function/class whose signature, behavior, and edge cases you've
  already fully specified.
- Writing tests from a spec you've already worked out.
- Mechanical refactors (rename, extract function, apply the same fix across N files)
  where the pattern is fully determined.
- Boilerplate: docstrings, straightforward CRUD, repetitive scaffolding.
- A bug fix where you've already found the root cause and know the fix.

### When NOT to delegate — keep this yourself

- Anything requiring architectural judgment, ambiguous requirements, or weighing
  tradeoffs.
- Security-sensitive code (auth, crypto, input validation at a trust boundary).
- Anything where you're not sure what "correct" looks like yet.
- The final review. Always review the worker's output yourself before it lands —
  treat it like a PR from a junior contributor, not a trusted commit.

### How to delegate

Call the worker script (on PATH as `qwen-delegate`, works from any shell):

```
qwen-delegate --task "<tight, unambiguous spec>" --files <relevant file paths> [--out <path>]
```

- `--task`: the spec. Be as specific as you'd be writing a ticket for a junior dev —
  exact function signature, inputs/outputs, edge cases, style to match.
- `--files`: existing files the worker needs as context (it reads them, doesn't
  guess at surrounding code).
- The script automatically also feeds the worker this AGENTS.md file for shared
  repo context.
- Omit `--out` to print the result to stdout and read it inline; use `--out` to
  save it to a scratch file you'll diff against before applying.
- The worker never edits files directly. You (the planner) always review the output
  and apply it yourself via your own edit tools — this keeps a human-reviewable
  seam between "local model wrote this" and "it's actually in the codebase."
- If the worker's response starts with `BLOCKED:`, the spec was ambiguous — tighten
  it and retry, don't just force an answer out of it.

## Ideation workflow: bouncing ideas off the worker before implementing

The worker isn't only for mechanical implementation — it's also useful as cheap
raw material for exploring a design question before you (the planner) commit to
an approach. The decision always stays with the planner; the worker never picks
a winner.

Use this when you (the planner) hit a question worth considering from more than
one angle — "should this be a cache or a queue", "three ways to shape this
migration", "what's the cleanest way to thread this through the existing code"
— before you've locked in an approach, not as a substitute for having an
opinion.

1. **Planner generates candidates.** Come up with 2-4 genuinely distinct
   theories/approaches yourself. Each one needs to be concrete enough to hand
   to a worker that cannot ask clarifying questions.
2. **Planner dispatches each candidate, one at a time, with `--role explore`:**
   ```
   qwen-delegate --role explore --task "<one candidate approach, described concretely>" \
     --files <relevant file paths> --out <scratch-file-per-candidate>
   ```
   `--role explore` switches the worker's instructions: instead of writing code,
   it fleshes out the ONE candidate it's given — how it would actually work,
   concrete risks/edge cases, and a rough implementation shape — without
   comparing it to alternatives or declaring a winner. See **Concurrency**
   below for why these calls are sequential, not fired off in parallel.
3. **Planner reads all the results and synthesizes.** Compare the fleshed-out
   candidates yourself. Either pick a winner and move to step 4, or let the
   results surface a new angle and run another round of step 1-2 with
   sharpened candidates.
4. **Planner commits to a strategy, then switches to the implementation
   workflow above** (`--role implement`, the default): break the chosen
   approach into unambiguous pieces and delegate each one, reviewing the
   output yourself as always.

The worker is a junior contributor in both roles — treat `explore` output as
one input to your thinking, not a conclusion, exactly as you'd treat
`implement` output as a draft PR rather than a merge-ready commit.

### Is qwen2.5-coder:7b the right model for `--role explore`?

`qwen2.5-coder:7b` is a code-specialized model; it's tuned for "write this
function," not for open-ended reasoning about tradeoffs. For the `explore`
role specifically, a general-purpose instruct model in the same size class
(e.g. `qwen2.5:7b-instruct`) will likely reason about tradeoffs more sensibly
than the coder variant, at no extra steady-state RAM cost — Ollama keeps one
model resident at a time, so swapping which model is loaded between the
`explore` phase and the `implement` phase costs a reload (a few seconds off
disk), not double the memory. If `explore` output reads shallow or generic,
try `--model qwen2.5:7b-instruct` for that phase before reaching for a bigger
model.

Do **not** reach for a bigger model (14B/32B) by default. A 14B model at
typical quantization needs roughly 8-9GB resident, which this machine cannot
sustain alongside normal use (see measured free RAM below) — confirm actual
free RAM first with `(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory`
(Windows) before ever trying one, and expect to close other apps.

### Resource constraints to keep in mind

This runs on a 16GB machine. The worker defaults to a **16k** context window
(not 32k — 32k was tested and leaves under 1GB RAM free on this machine, risking
swapping/thrashing). Don't hand it more file content than it needs.

`qwen-delegate` runs a pre-flight budget check before contacting the model at all:
it estimates tokens for the system prompt, task spec, and every `--files` entry,
reserves ~2048 tokens for the response, and **refuses to send** (exit code 3,
nothing sent to Ollama) if the total doesn't fit in 16k. It prints a per-component
breakdown to stderr either way — read it.

If you hit the refusal:
- Trim or drop the largest file(s) it lists.
- Paste only the relevant function/section of a file instead of the whole thing.
- Split the task into smaller pieces and delegate each separately — this is
  usually the right move anyway, since a task small enough to fit a tight spec
  in 16k is also small enough to review properly.
- Only pass `--num-ctx 32768` if you've confirmed real free RAM first (check,
  don't assume); never pass `--force` just to make a refusal go away without
  first reading why it refused.

### Concurrency: one worker call at a time, never several at once

It's tempting, especially for the ideation workflow above, to fire off all the
candidate `explore` calls in parallel to save wall-clock time. **Don't.** On
this machine's actual hardware, concurrent local workers are not feasible:

- Total RAM is ~16GB, and a baseline-business-use measurement on this machine
  showed as little as ~1.85GB free with normal apps open — there is routinely
  very little headroom to begin with, before any model is loaded.
- Graphics is an Intel Arc integrated GPU reporting ~2GB of *shared* adapter
  memory (not dedicated VRAM), and stock Ollama on Windows has no Intel Arc
  acceleration backend, so inference runs on the CPU and consumes ordinary
  system RAM — there's no separate GPU memory pool to offload to.
- `qwen2.5-coder:7b` at the quantization in use is ~4.7GB on disk; resident
  memory while loaded is similar plus KV-cache overhead that scales with
  `num_ctx` (16k context was chosen specifically because 32k already leaves
  under 1GB free for a *single* loaded instance).
- Running two instances at once (two processes, or `OLLAMA_NUM_PARALLEL > 1`
  against one process) roughly doubles resident memory to ~9-10GB+ plus a
  second KV cache, on a machine already measured near its limit. That's a
  recipe for swapping/thrashing or an outright OOM, not real parallelism.

So: dispatch `qwen-delegate` calls **one at a time, sequentially** — wait for
each response before sending the next, whether you're exploring candidates or
delegating implementation pieces. Don't background multiple `qwen-delegate`
invocations and don't set `OLLAMA_NUM_PARALLEL` above 1. "Multiple agents"
here means multiple *turns* through the same single local worker, not
multiple workers running at the same instant.

Before any heavier-than-usual local run (bigger `--num-ctx`, a bigger model),
check actual free memory rather than assuming it from the numbers above —
they drift with whatever else is open:
```
# PowerShell
(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory  # KB, not bytes
```
If free RAM is under ~3GB, close other apps before delegating.

If you genuinely need true concurrent multi-agent fan-out, that needs either
substantially more RAM (32GB+ is a reasonable floor for two 7B models at once
with headroom) or a worker that isn't a local process at all — e.g. a hosted/
cloud model called over the network instead of Ollama. Don't try to force
concurrency out of this machine as it's configured today.

## Worktrees

When working on multiple tasks in parallel, use `git worktree add` rather than
juggling branches in one checkout. Since this file is tracked in git, every worktree
gets an identical copy automatically — no extra setup needed per worktree. Convention:
worktrees live as sibling directories, e.g. `../<repo>-worktrees/<task-slug>`.

## Project-specific notes

- **What it is:** post-earnings-announcement drift (PEAD) research on WRDS (IBES/CRSP/Compustat) and OptionMetrics data, 1996-2013: equity long/short books, an options book, blends of the two, and a paper-trading engine.
- **Framework:** one strategy = one file under `pead/strategies/{equity,options,portfolio}/`, auto-registered. Engines are in `pead/engines/`, data and metrics in `pead/core/`, reports in `pead/reporting/`, live trading in `pead/live/`. `pead/research/` is an archive; nothing imports from it.
- **Commands:** `python -m pead list | run <name|class|family:x> | compare | portfolio | live`, and `python -m pytest tests -q`. Results go to `results/<strategy>/`; the leaderboard is `results/leaderboard.md`.
- **Environment:** results reproduce only under the default Python 3.12 / pandas 3. Raw data is a real copy (no links) with its master on `D:`; `data/` holds small tracked files and big derived data is git-ignored.
- **Read first:** `docs/REPO_MAP.md`, then `docs/FIXES.md`. Four methodology defects were fixed (leverage cap, decile look-ahead, option mid fills, full-sample holding periods), so older numbers are overstated. They are kept as `*_legacy` / `*_prefix_fix` and must not be quoted as current.
- **Current best:** `steady_drift_plus` (long-only, no borrowing, no look-ahead; see `docs/STEADY_DRIFT.md`). Open items are in `docs/ROADMAP.md`.
- **Caveats:** short-borrow fees, financing and market impact are not modeled. Strategy constants are partly in-sample. `validated` means the engine reproduces the original script, not that the strategy has an edge.
