# Contributing Guide (Agent Playbook)

This guide is a **drop-in template** for any GitHub repository. It defines how an autonomous coding agent (and its subagents) must plan, branch, commit, review, and merge work. It is written in imperative voice: every "MUST" / "MUST NOT" is a hard rule.

The agent's north star: **ship small, reviewable, reversible slices; keep it simple and decide the obvious yourself, escalating only genuine forks; never touch `main` without explicit approval.**

---

## Table of Contents

1. [Core Principles](#1-core-principles)
2. [Model Assignment (Mandatory)](#2-model-assignment-mandatory)
3. [Phase 1 — Plan Before You Touch Code](#3-phase-1--plan-before-you-touch-code)
4. [Phase 2 — The Branch Tree](#4-phase-2--the-branch-tree)
5. [Phase 3 — Executing Slices (Subagents)](#5-phase-3--executing-slices-subagents)
6. [Phase 4 — Pull Requests](#6-phase-4--pull-requests)
7. [Phase 5 — Review Agent](#7-phase-5--review-agent)
8. [Phase 6 — Merging Up the Tree](#8-phase-6--merging-up-the-tree)
9. [Phase 7 — Merging to `main`](#9-phase-7--merging-to-main)
10. [Phase 8 — Branch Cleanup](#10-phase-8--branch-cleanup)
11. [Subjective vs. Objective Decisions](#11-subjective-vs-objective-decisions)
12. [Hard Prohibitions](#12-hard-prohibitions)
13. [Quick Reference Checklist](#13-quick-reference-checklist)

---

## 1. Core Principles

- **Keep it simple and linear.** Build the smallest thing that satisfies the spec. Do not add scope, abstraction, features, or ceremony the task did not ask for. Do not make the project more than it is. When two paths both work, take the simpler one. When in doubt, do less. This applies to writing, not just code: comments, PR descriptions, and report prose should be terse and plain — a comment earns its place only by explaining a non-obvious *why*, not by narrating what the code already says. A verbose codebase and verbose PRs are their own kind of complexity, and cost real time and tokens to write, read, and review.
- **Use senior-developer discretion.** Act like a talented senior engineer: make straightforward, reasonable fixes and decisions yourself instead of asking permission for the obvious. Don't make silly mistakes, and know where the boundaries are (§11). The bar for interrupting the human is high — see [§11.1](#111-when-to-decide-vs-ask).
- **Plan, then read your plan, then execute.** Never begin editing code before a written spec exists and has been re-read.
- **One small feature per branch — err toward smaller, always.** A branch holds a few commits at
  most, each tightly scoped to one condensed piece of behavior. When a slice from the plan turns
  out to bundle more than one natural sub-behavior (e.g. "clean the data," "compute a derived
  finding," and "cross-validate against a second source" are three, not one), split it into
  separate sub-branches *before* writing code, not after the diff turns out large. Smaller PRs are
  not just tidier — they are the mechanism that makes review actually work: a reviewer (human or
  agent) reliably catches everything in a diff it can hold in its head at once, and reliably misses
  things in a diff it can't. A large diff is what turns one review round into four or five, each
  catching one more thing the last one missed. Many small, cleanly-reviewed PRs land faster in
  total than a few large ones that each need repeated review rounds to get right — see [§6](#6-phase-4--pull-requests)
  for the concrete sizing guideline.
- **Distribute and parallelize** work across a tree of branches using subagents. The tree always terminates at a single **root branch** that is the only branch that merges to `main`.
- **The agent does not make *genuine-fork* calls alone.** Product/UX, public API shape, naming, dependencies, architecture, security posture — the categories in [§11](#11-subjective-vs-objective-decisions) — are escalated. Straightforward, obviously-right fixes are made at the agent's discretion, not escalated (see [§11.1](#111-when-to-decide-vs-ask)).
- **The internal review agent is triaged each round, not chased to zero.** Wait for the review subagent's pass ([§7](#7-phase-5--review-agent)), decide at the Opus tier which of its findings actually warrant a change (§11.1), make only those, and re-review if warranted. **Two rounds is the norm; a round 3 on the same PR is not — a PR still generating real, substantive findings after round 2 is a signal the PR is too big or too complex for the review loop to converge on it, and the response is to stop reviewing it and *split it*, not to add more rounds.** See [§7](#7-phase-5--review-agent). A finding that repeats a pattern from an earlier round gets fixed as a class, not chased instance-by-instance across further rounds.
- **`main` is sacred.** No direct pushes, no force pushes, no auto-merge, no shortcuts.

---

## 2. Model Assignment (Mandatory)

This project's agent runs as a fleet of subagents, and **each subagent's model is dictated by the
kind of work it does — never by whatever model the parent chat session happens to be running.**
If you are the parent/orchestrating agent, you are very likely running as a different model than
the one required below for a given piece of work. That does not exempt you: when you spawn a
subagent to do that work, you MUST explicitly pin its model to the assignment below. This rule is
not a default or a suggestion — it is a hard requirement, checked on every subagent spawn.

| Kind of work | Required model | Notes |
|---|---|---|
| **Writing/editing code** (implementing a slice, fixing a bug, writing tests) | **Sonnet 5** (`claude-sonnet-5`) | All "coding" subagents from [§5](#5-phase-3--executing-slices-subagents). |
| **Thinking and planning** — writing/updating the plan in `memory/`, deciding branch/slice breakdown, triaging review findings ([§7](#7-phase-5--review-agent)), deciding whether a proposed doc/fix change should be made at all | **Opus 5** (`claude-opus-5`); if unavailable, **Opus 4.8** | This is the "judgment" layer — anywhere the agent is deciding *what* to do or *whether* something is worth doing, not executing a already-decided mechanical step. |
| **Documentation and simple mechanical fixes** — writing/updating docs, README content, changelog entries, and applying already-decided, already-verified simple fixes after a PR has passed review | **Haiku** (`claude-haiku-4-5-20251001`) | Only for execution of a change that Opus has already decided should happen (see above). Haiku does not decide anything — it writes what it's told to write. |
| **The review agent itself** ([§7](#7-phase-5--review-agent)) | **Opus 5** (`claude-opus-5`); if unavailable, **Opus 4.8** | An elite adversarial review is a judgment task (finding defects, weighing severity), not a code-writing task — it uses the thinking-tier model, not the coding-tier model. |
| **External Codex/ChatGPT review, only if it is ever used** ([§7](#7-phase-5--review-agent)) | **`gpt-5.6-terra`** — never the default `gpt-5.6-sol` | This project has no active Codex review right now (see [§7](#7-phase-5--review-agent)), so this row is dormant. If Codex review is ever reactivated, pin it to Terra: Sol is the flagship tier ($5/$30 per M tokens) and buys no review-quality gain over Terra ($2/$12, "competitive with GPT-5.5") worth its cost here. Do not drop to Luna ($0.20/$1.20) either — per the "never downgrade a thinking/review task" rule below, Luna is sized for high-volume simple work, not adversarial review. |

Rules for applying this table:

- **Pin the model explicitly on every spawn.** Never rely on inheritance from the parent session.
- **Never downgrade a thinking/review task to save cost.** If Opus 5 is unavailable, fall back to
  Opus 4.8 — do not fall back further to Sonnet or Haiku for planning, triage, or review.
- **Never upgrade a mechanical task "just in case."** Docs and already-decided simple fixes stay on
  Haiku; if a "simple fix" turns out to need judgment once a subagent is in it, that subagent
  escalates back to the parent rather than silently deciding on its own tier.
- **Parallelize aggressively, serialize only real dependencies.** Independent coding slices spawn
  as multiple concurrent Sonnet 5 subagents in one batch. A slice that depends on another's output
  waits for it — do not parallelize dependent work just because subagents are cheap to spawn. This
  applies at every layer: independent sub-branches within a feature, independent feature branches
  within the root, and independent review/fix cycles across sibling PRs.

---

## 3. Phase 1 — Plan Before You Touch Code

For every task, spec, or feature request:

1. Create a working plan file at:
   ```
   memory/<spec-name>.md
   ```
   The `memory/` directory MUST be listed in `.gitignore`. This is the agent's private working
   memory for this project — planning notes, specs, and progress state — not artifacts of the PR.

2. The plan file MUST include:
   - **Goal** — one paragraph.
   - **Scope in / Scope out** — bulleted.
   - **Assumptions** — anything the agent inferred.
   - **Subjective items to escalate** — list every decision the agent identifies as opinion-shaped (see [§11](#11-subjective-vs-objective-decisions)). Ask the human before proceeding on any of these.
   - **Slice breakdown** — the ordered list of small features, each destined for its own branch.
     Break each slice down to sub-slice grain up front: if describing a slice needs "and" ("cleans
     the data **and** computes a finding **and** cross-validates it"), it's more than one slice.
     Default to more, smaller entries here rather than fewer, larger ones — see [§1](#1-core-principles)
     and [§6](#6-phase-4--pull-requests).
   - **Branch tree sketch** — root branch, feature branches, sub-branches, and their parent relationships.
   - **Acceptance criteria** — per slice, measurable and testable.
   - **Risks / rollback notes**.

3. **Re-read the plan** in a fresh step before executing. This is not optional — the read pass is what catches contradictions the write pass missed.

4. If the plan changes mid-flight, update `memory/<spec-name>.md` first, then continue.

---

## 4. Phase 2 — The Branch Tree

Work is organized as a **git worktree**: many small branches that all trace back to one root branch, which is the only branch permitted to open a PR into `main`. It is a requirement to use **git worktrees** to develop as this allows other agents to work in parallel.

```
main
 └── root branch          (integration branch for the whole spec)
      ├── feature branch A
      │    ├── sub-branch A1
      │    └── sub-branch A2
      ├── feature branch B
      │    └── sub-branch B1
      └── feature branch C
```

### Rules for the tree

- **Naming:** follow the repo's existing branch conventions. Inspect recent branches (`git branch -a`, PR history, any `CONTRIBUTING`/`STYLE` docs) and mirror the shape. If no convention exists, propose one to the user and get approval before creating branches.
- **Root branch:** created off the latest `main`. It is long-lived for the duration of the spec and only receives merges from its feature children.
- **Feature branches:** branch off the root. Each represents one cohesive capability from the plan.
- **Sub-branches:** branch off a feature. Each holds **one atomic slice** — the smallest shippable unit of that feature.
- **Commits per branch:** keep it to a handful of small, semantically meaningful commits. If a branch grows large, split it into more sub-branches.
- **Parallelism:** independent slices MUST be developed in parallel via subagents (see [§4](#4-phase-3--executing-slices-subagents)). Dependent slices are serialized behind their parent.
- **Isolation:** a single repository checkout can only have one branch checked out at a time — if two subagents share it, one switching branches yanks the working tree out from under the other, and edits or commits can land on the wrong branch. Each subagent working an in-flight branch in parallel with another MUST get its own git worktree (or clone). If isolated worktrees genuinely aren't available, serialize the branch work instead of sharing one checkout.
- **Every branch is a leaf until proven otherwise.** Only create children when needed.
- **The tree shape is a guide, not a cage — small, mergeable increments are the goal, not strict hierarchy.** The default `root → feature → sub` shape above fits most work, but nothing prevents richer topologies when they keep each PR smaller:
  - **Stacked sub-branches within a feature.** If sub-branch B depends on sub-branch A's changes, cut B off A (not off the feature branch) and PR B into A. When A merges into the feature, B's PR retargets to the feature and reviews only B's incremental diff. This is preferable to piling both A's and B's changes onto one branch just to keep the tree "clean."
  - **Parallel feature branches under the same root.** Two features that don't depend on each other MUST develop in parallel (per the parallelism rule above), each merging into root independently.
  - **Feature-on-feature stacks.** If feature B naturally builds on feature A's output, it is fine for B to branch off A and PR into A, rather than waiting for A to reach root first. When A merges up, B retargets.
  - **Cross-branch small PRs.** A tiny fix relevant to work on a sibling branch can be cut as its own sub-branch off the sibling and PR'd into it, rather than duplicating the change or waiting for a merge-up.

  The rule is only this: every branch has exactly one parent branch it merges up into, that parent is a branch (never `main`), and the chain terminates at the root. What the intermediate shape looks like — flat fan, deep stack, mixed — is whatever keeps each PR small and each merge clean.
- **Naming when stacking.** Three-level nesting (`feat/<spec>/<feature>/<slice>`) does not work once `feat/<spec>/<feature>` already exists as a pushed ref — git cannot nest a ref under an existing leaf ref. Use a flattened form for sub-branches: `feat/<spec>/<feature>-<slice>` (e.g. `feat/phase1-equity-pead/audit-reports-equity-data-report`).

---

## 5. Phase 3 — Executing Slices (Subagents)

The agent MUST delegate slice work to subagents to parallelize and to keep each unit of work optimally scoped.

### When to spawn a subagent

- The slice is independent of other in-flight slices.
- The slice has clear, written acceptance criteria in the plan.
- The slice can be completed without needing to negotiate scope with the user mid-execution.

### Subagent context contract

Each subagent invocation MUST include a **context contract** in its prompt:

1. **Targeted goal** — a single-sentence objective for the slice.
2. **Branch to work on** — exact branch name, and the parent it was cut from.
3. **Files/areas expected to change** — the agent's best guess; the subagent may expand this after investigating.
4. **Detailed acceptance criteria** — how "done" is measured (tests to pass, behaviors to demonstrate, files to produce).
5. **Non-goals** — what the subagent MUST NOT touch or refactor.
6. **Escalation triggers** — the categories from [§11](#11-subjective-vs-objective-decisions) that, if encountered, must be surfaced to the parent agent (which surfaces to the human) rather than decided unilaterally.

The subagent **may read the entire repository** as needed to understand context, but its **writes must be surgical** and confined to what the acceptance criteria require. Broad reads, narrow writes.

### Subagent completion

A subagent's final message back to the parent MUST include:
- Files changed and why.
- Any assumptions it made.
- Any items it flagged as subjective and did **not** decide.
- Test/lint results.
- The PR URL (see [§5](#5-phase-4--pull-requests)).

---

## 6. Phase 4 — Pull Requests

Every branch (sub → feature, feature → root) is integrated via a PR. No exceptions.

### PR requirements

- **Base branch:** the direct parent in the tree. Sub-branch PRs target their feature branch. Feature PRs target the root branch. Only the root branch PR targets `main`.
- **Title:** concise, imperative, scoped to the slice.
- **Description MUST contain:**
  - The relevant excerpt from the parent plan section in `memory/<spec-name>.md`, pasted directly into the description — `memory/` is gitignored, so a link to it would 404 for reviewers; the pasted excerpt is the only thing that actually resolves.
  - Summary of behavior change.
  - Explicit list of what was **not** changed / left for later slices.
  - Test evidence (commands run, output summary).
  - Any items the agent flagged as subjective and is awaiting human input on.
- **Size: small, by default, not as a fallback.** Aim for one cohesive piece of behavior in one
  new/changed file (or a couple of tightly related ones). **Split trigger: ~800 lines of diff (added
  + deleted, excluding lockfiles/generated artifacts/gitignored data) is the point at which a PR
  MUST be split; the tilde is about the exact number (a 820-line PR is not required to split, a
  1200-line one is), not about whether the rule is optional.** A good PR is well under that — 800 is
  already a lot; treat it as an upper bound, not a target to fill. The real goal is a diff a reviewer
  can hold in their head in one pass, not one that needs a table of contents. If a slice's natural
  output is "a pipeline stage with five sub-steps and a report," that's five-plus PRs, not one: e.g.
  split "clean the raw data," "derive a finding from it," "cross-validate it against a second
  source," and "render the findings as a report" into separate branches even though they're all part
  of the same slice. Don't wait for a diff to turn out sprawling and split it after the fact — decide
  the split at planning time (see [§3](#3-phase-1--plan-before-you-touch-code)'s slice breakdown)
  and cut the branches small from the start. If a working diff *is* growing past the ~800-line
  trigger mid-implementation, that itself is the signal to stop, split, and stack (per
  [§4](#4-phase-2--the-branch-tree)'s stacked-sub-branches rule) — not to negotiate the ceiling.
  This is a direct lesson from this project's own history: large PRs needed 4-5 review-fix rounds to
  get right, while small ones tend to clear in 1-2 — many small, cleanly-reviewed iterations compound
  into the big change faster than a few large ones that each drag through repeated rounds.
- **Keep the description short.** The five required items above, each a few lines — not a running
  log of every review round. When a review round changes something, edit the relevant bullet in
  place (e.g. update "Test evidence" with the new pass count) rather than appending an "Update N"
  section on top of the last one. A reader should be able to understand the PR in under a minute;
  a PR description that takes longer to read than the diff has failed at its job.
- **Draft first** if the agent is still iterating; mark ready for review only when it believes the slice is complete.
- **Request the review agent immediately.** As soon as the PR is opened (or marked ready), spawn the review subagent. See [§7](#7-phase-5--review-agent) for the full context-contract and triage protocol.

---

## 7. Phase 5 — Review Agent

There is no external Codex/ChatGPT review available to this project (Codex review credits are
exhausted). The agent MUST NOT wait for, poll for, or otherwise depend on an external
`chatgpt-codex-connector[bot]` review — none is coming. Instead, the orchestrating agent runs its
**own internal review subagent** for every PR, and that self-hosted review is the mandatory gate
before any PR merges up the tree.

**Two rounds is the norm; more rounds is a signal, not a plan.** Round 1 catches real findings; fix
them. Round 2 is primarily a *convergence check* — confirming round 1's fixes stuck and catching
anything round 1 missed. If round 2 comes back clean or with only cosmetic/deferrable findings
(cosmetic/deferrable = fails the [§11.1](#111-when-to-decide-vs-ask) "worth changing" bar, or is
explicitly scope-deferred with a written reason on the PR — anything else is substantive), fix the
trivial items in place if any and merge. **If round 2 still surfaces real, substantive issues that
aren't in the cosmetic/deferrable bucket, do not fix them in place and do not spawn round 3 on the
same PR — that is the "PR is too big or too complex for this loop to converge on it" signal. Stop
reviewing this PR and reshape the work instead.** See "When to stop reviewing" below for the
specifics, but the two default responses are: (a) *split-and-ship* — extract the parts of the diff
that ARE now cleanly reviewable into a smaller PR that will pass a fresh 2-round pass, merge that,
and reopen the remainder as one or more new small PRs stacked on top (this is the one case where the
extracted sub-branch's PR targets its parent's parent, not the current stuck branch — the current
branch is precisely what's not merging); or (b) *simplify-in-place* — cut the elaborate section the
review keeps flagging rather than patching it again. The point of not-adding-more-rounds is not to
force-merge unreviewed code; it is to force the work to be resized so the review can actually
finish. A finding that repeats a pattern from an earlier round still gets fixed as a *class* within
its round (not chased instance-by-instance across further rounds) — that rule is independent of the
round-count guidance.

**If external Codex review is ever reactivated for this project,** per
[§2](#2-model-assignment-mandatory) it MUST be pinned to the **`gpt-5.6-terra`** tier — via the
connected Codex workspace's model setting, or `--model gpt-5.6-terra` if invoked through the Codex
CLI — never left on the default flagship **`gpt-5.6-sol`** tier. Sol costs more ($5/$30 per M
tokens vs. Terra's $2/$12) without a review-quality gain worth paying for on top of the internal
Opus review above; do not go the other direction to Luna either, since a lighter, high-throughput
tier is the wrong fit for adversarial review judgment.

### The review agent's context contract — PR only, nothing more

This is the load-bearing rule of this section. The review subagent MUST be spawned with:

- the PR's **full diff** (every changed file, not just hunks — enough surrounding code to judge
  correctness in context),
- the PR **title and description**,
- and **nothing else.**

The review subagent must **not** receive: the parent session's conversation history, the working
plan in `memory/`, the implementer's rationale for why it made the choices it made, or prior
review rounds' comments on this same PR. This is deliberate, not an oversight — a reviewer that
only sees the diff and the PR's own stated intent reviews what the code actually does, not what
the implementer intended it to do, and cannot be talked out of a finding by context the code
itself doesn't carry. If a later round is needed after fixes, spawn a **fresh** review subagent
with the same restricted contract against the new diff — do not reuse or carry forward the prior
round's subagent state.

### Model and goal

Per [§2](#2-model-assignment-mandatory), the review subagent runs on **Opus 5** (or Opus 4.8 if
Opus 5 is unavailable) — review is a judgment task, not a code-writing task, so it uses the
thinking-tier model regardless of what tier wrote the code under review.

Brief the review subagent to be an **elite, adversarial reviewer**, not a friendly pass. Its job
is to find every way the diff is wrong, unsafe, or wasteful — not to summarize what it does. At
minimum it must actively hunt for:

- **Correctness bugs** — logic errors, off-by-one errors, incorrect edge-case handling, wrong
  operator/boundary, race conditions, incorrect assumptions about inputs, silently swallowed
  exceptions.
- **Security issues** — injection (SQL/shell/path), unsafe deserialization, secrets or credentials
  handling, SSRF, unsafe use of `eval`/`exec`/pickle, missing input validation at trust boundaries.
- **Inefficiencies** — quadratic-or-worse algorithms where linear is available, loading more data
  into memory than needed (e.g. reading the whole 300 GB archive into pandas instead of a
  filtered/streamed scan), redundant recomputation, unnecessary I/O in a loop, missing
  predicate/projection pushdown on Parquet/DuckDB/Polars scans.
- **Poorly managed data** — anything that mutates the immutable raw archive, look-ahead bias
  (using information not yet available at the point-in-time of a decision), survivorship bias,
  point-in-time violations in joins (e.g. joining a consensus estimate that wasn't actually known
  before the event), timezone/session mismatches, silent NaN/null handling that changes results,
  non-reproducible randomness (missing seeds), train/test leakage, misuse of statistical tests.
- **Missing or weak tests** for the behavior the PR claims to add.

Findings must be reported **ordered most-severe-first**, each with a concrete failure
scenario (specific input/state → specific wrong output or crash) — not vague style commentary.
End with a short verdict on whether the PR is safe to merge up as-is.

**Keep the report short.** One tight paragraph per finding — what's wrong, the concrete failure
case, the fix. Not a forensic essay: no multi-table statistical breakdowns, no embedded
verification-script transcripts, no restating every number in the diff back at the author. If a
claim needs checking, check it and state the one-line result ("verified: X reproduces exactly") —
don't show the work. A review this size should read like a sharp colleague's PR comment, not a
research paper. This matters as much as thoroughness: a 3,000-word review costs as much time and
context to write, relay, and triage as it saves, and a terse list of real problems gets fixed faster
than an exhaustive one.

### When the review agent runs

- **On every PR open (or draft → ready transition)** — spawn the review subagent against the
  initial diff before any merge-up consideration.
- **After every subsequent push** — a prior round's review is stale the moment new commits land;
  spawn a fresh review subagent against the new diff.
- **Wait for CI too.** Before triaging a round of findings, also wait for the **entire CI run**
  (every job configured for this repo, not just the fastest one) to finish. Collect all CI
  failures and all review findings together, then address them in one follow-up pass — do not
  react to a partial signal from either source.

### Triage the findings — this is an Opus decision

Reading the review subagent's report and deciding what to do about each finding is **thinking
work**: per [§2](#2-model-assignment-mandatory), this triage happens at the Opus tier, never
delegated to the Sonnet coding subagent and never done by skimming. For each finding, apply the
[§11.1](#111-when-to-decide-vs-ask) test:

1. **If the fix is clear and reasonable — decide to make it**, then hand the specific, scoped fix
   to a Sonnet 5 coding subagent (or, if it is genuinely just prose/doc/no-judgment-required, a
   Haiku subagent per [§2](#2-model-assignment-mandatory)). A defect, an inconsistency, a missed
   test, a straightforward correctness/security/data-integrity fix is the agent's to decide and
   delegate at its own discretion.
2. **Escalate only a genuine fork:** a fix with **multiple materially-different reasonable
   implementations**, or a true product/policy/naming/architecture/security-posture decision per
   [§11](#11-subjective-vs-objective-decisions). Ask one focused question — "implement it this way
   or that way?" — with a recommendation. Do not ask about fixes that are straightforward.
3. **Decline what would overcomplicate.** A suggestion adding scope/abstraction/infrastructure
   beyond what the spec needs may be declined or deferred — note why on the PR. Simplicity (§1)
   outranks satisfying every finding.
4. **Push a follow-up commit with the decided fixes and spawn a fresh review subagent** against
   the new diff. Per the 2-round cap below, this second round's findings get the same triage and
   get fixed (including a class-fix per "When to stop reviewing" if the finding repeats a pattern)
   — then merge. Do not spawn a third round.

### When to stop reviewing

The goal is never a spotless report — it's a small, reviewable PR, triaged properly, then merge.
After round 1, fix what's real. After round 2, look honestly at what the report is *still* saying:

- **If it's clean, or only restating cosmetic/deferred items:** merge.
- **If it's still finding real, substantive issues that aren't cosmetic/deferred:** do not spawn
  round 3 on this PR. That is the "PR is too big or too complex for this review loop to converge on
  it" signal, and adding rounds does not fix it — it just papers over the underlying sizing problem.
  Pick one of two responses, whichever fits:

  1. **Split-and-ship (default).** Keep the fixes made so far on the branch. Extract the parts of
     the diff that ARE now cleanly reviewable into a smaller PR — cut a new sub-branch off the
     current one, move the reviewable subset there, PR it into the *current branch's parent* (i.e.
     retarget past the stuck branch, unlike the pre-planned stacked-sub-branches pattern in
     [§4](#4-phase-2--the-branch-tree) where B PRs into A; here the current branch is precisely the
     one that isn't merging, so the extracted PR skips it), run a fresh 2-round review that actually
     converges on that smaller scope, and merge. Reopen the remainder as one or more new small PRs
     stacked on top. The original larger PR either becomes empty (close it) or is itself reshaped
     down to what's left.
  2. **Simplify-in-place.** If the recurring finding points at a section that is elaborate for what
     it does, cut the elaborate section rather than patching it again — fewer report claims, a
     narrower feature, a plainer implementation that has less surface for the class of bug. Note the
     simplification on the PR the same way a fix would be. This is a legitimate outcome of triage,
     not a failure to fully satisfy the review.

  Either way, close the review loop on *this* PR and let the reshaped smaller PR(s) carry the rest
  of the work forward.

**If a round's finding is the same *class* of problem as an earlier round's (not the same instance —
the same shape: e.g. two different unconditional claims in report-generation prose, two different
missing null-checks, two different off-by-one date joins), do not fix the one instance and move on.**
Find every other instance of that class in the affected file (a `grep` for the shape of the pattern
is usually enough) and fix them all in the same pass. A pattern that recurs across rounds means the
code has a structural weak spot, not a series of unrelated typos — the fix is to close the weak spot
once, not to let each round discover one more example of it.

Stop the review loop on this PR (typically by round 2; always by the round where it becomes clear
the loop isn't converging) once remaining findings are:

- restating something already declined or deferred with a reason on the PR,
- purely stylistic/cosmetic with no behavior, security, or data-integrity impact,
- scope the PR deliberately doesn't cover,
- otherwise not clearing the [§11.1](#111-when-to-decide-vs-ask) bar for "worth changing," or
- **substantive but pointing at a size/complexity problem the review loop can't fix — in which case
  split per above and merge the reviewable subset.**

Note the outcome briefly on the PR ("round 2 fixed the remaining findings — merging," or "round 2
  surfaced structural issues we're splitting rather than adding rounds — reviewable subset merges
  here; PR #NNN carries the rest") and move on. This is an Opus-tier judgment call the agent makes
itself, not a genuine fork to escalate to the human.

The PR is eligible to merge up once **both** are true: the review loop has reached its stopping
point (clean, or split-and-shipped), and any escalated forks are resolved by the human. None of this
requires a spotless report — it requires that every round's findings were actually triaged at the
Opus tier and either acted on or explicitly handled by resizing the work.

### Posting the review for auditability

Post the review subagent's findings (or a summary of them, for a long report) as a PR comment,
clearly labelled as the internal review agent's pass and the model tier it ran on, so the record
of what was reviewed and decided is visible on the PR itself rather than only in the agent's own
working memory.

---

## 8. Phase 6 — Merging Up the Tree

- Merges from sub → feature and feature → root are **allowed and expected** without additional user gating, provided **CI is green (all jobs)** and the review agent pass in [§7](#7-phase-5--review-agent) has been triaged.
- **Use merge commits everywhere** (not squash, not rebase-and-merge). Full history is preserved so the small-commit trail up the tree stays intact and auditable.
- After each successful merge, propose branch cleanup per [§9](#9-phase-8--branch-cleanup).

---

## 9. Phase 7 — Merging to `main`

The root-branch → `main` merge is the **only** merge that requires the human user's explicit, complete review and acceptance.

Before proposing the merge to `main`, the agent MUST:
- Confirm every feature PR has merged into the root branch.
- Confirm CI is green on the root branch.
- Post a consolidated summary to the human: goal, slice list, notable decisions, subjective items resolved, test coverage, migration/rollback notes.
- **Wait for the human's explicit approval** to merge to `main`.

Auto-merge MUST NOT be enabled on any PR targeting `main`. Merge only happens after the human says "merge it."

---

## 10. Phase 8 — Branch Cleanup

**Merged branches MUST be removed once they are no longer relevant.** Stale branches clutter the tree and confuse future work.

- After a sub-branch is merged into its feature, the sub-branch is a candidate for deletion.
- After a feature branch is merged into the root, the feature branch is a candidate for deletion.
- After the root branch is merged into `main`, the root branch is a candidate for deletion.

Rules:
- The agent MUST propose the deletion to the user (list of branches, local + remote) and **wait for approval** before deleting. Branch deletion is one of the actions that requires explicit user consent.
- Never delete a branch that still has open PRs, unmerged commits, or ongoing work by any collaborator.
- Delete both the local and the remote branch once approved.

---

## 11. Subjective vs. Objective Decisions

The agent's job is to **think about what is important** and, for every change under consideration, ask: *"Is any part of this dependent on human opinion, product judgment, or repo-level policy?"* If yes — and it is a genuine fork — it escalates. Otherwise it decides and moves on.

### 11.1 When to decide vs. ask

Default to **deciding**. Interrupt the human only when it is genuinely warranted. Before asking, run this test:

- **Is the fix/decision straightforward with one obviously-right answer?** → **Decide and do it.** (correctness fixes, inconsistencies, mechanical refactors, tests, security/robustness fixes, following an explicit instruction, the plainly-simpler of two options.)
- **Are there multiple materially-different reasonable implementations?** → **Ask** one focused "this way or that way?" question with a recommendation. A difference that is trivial or cosmetic is *not* material — pick the clean one and move on.
- **Is it one of the always-subjective categories below (product/UX, public API shape, naming, deps, architecture, security posture)?** → **Ask.**
- **Would it add scope/abstraction/infra the spec doesn't need?** → **Don't do it** (or do the minimal version); note the choice.

Batch genuinely-needed questions; never fan a single review out into many small questions. When you do proceed on your own, say briefly what you decided and why, so it stays auditable.

### Always subjective — escalate to the human

- **Product/UX behavior and copy** — what the feature does from a user's perspective, wording, tone.
- **API shape / public interface design** — endpoints, payload shapes, function signatures exposed to consumers.
- **Naming** — files, symbols, endpoints, config keys. Naming carries meaning; the human owns it.
- **Dependency additions or version bumps** — adding a package, upgrading a major version, swapping a library.
- **Architectural tradeoffs** — performance vs. readability, sync vs. async, monolith vs. split, caching strategy.
- **Security posture decisions** — auth models, threat scope, what data is trusted, what is logged.
- **Deleting or renaming existing public APIs** — anything that could break downstream consumers.
- **Anything else the agent's judgment flags as opinion-shaped.** When in doubt, ask.

### Safe to decide autonomously

- Mechanical refactors that preserve behavior.
- Fixing a demonstrated bug with a minimal change.
- Adding tests for existing behavior.
- Following an explicit instruction the human already gave.
- Complying with lint/format rules already configured in the repo.

### How to escalate

- Present the decision, the options, the tradeoffs, and a recommendation.
- Do not proceed with the affected code path until the human answers.
- Record the answer in the PR description so the decision is auditable.

---

## 12. Hard Prohibitions

The agent MUST NOT:

- Push directly to `main`.
- Force push to any branch (`--force`, `--force-with-lease`, or otherwise) without explicit human instruction for that specific push.
- Bypass hooks (`--no-verify`) for any reason.
- Amend or rewrite commits that have already been pushed.
- Enable auto-merge on any PR targeting `main`.
- Delete any branch without user approval.
- Commit secrets, tokens, credentials, or `.env` files. If one is discovered committed, stop and alert the user.
- Make a genuine-fork decision on the human's behalf — a §11 category (product/UX, public API shape, naming, deps, architecture, security posture) or a choice with multiple materially-different reasonable implementations (see [§11.1](#111-when-to-decide-vs-ask)). Straightforward fixes are the agent's to make.
- Merge a PR up the tree before the review agent's pass ([§7](#7-phase-5--review-agent)) has been read and triaged (at the Opus tier) for the latest diff.
- Treat "the CI passed" as a substitute for the review agent's pass.
- Spawn any subagent without pinning its model per [§2](#2-model-assignment-mandatory) — including spawning the review agent on anything other than Opus 5/4.8, or having the review agent's context include anything beyond the PR diff and description.

---

## 13. Quick Reference Checklist

Before every task:
- [ ] Wrote `memory/<spec-name>.md` with goal, scope, slices, acceptance criteria, and subjective items.
- [ ] Re-read the plan.
- [ ] Escalated all subjective items in the plan to the human and received answers.

Per slice:
- [ ] Cut a sub-branch off its correct parent (feature branch, or another sub-branch when stacking per [§4](#4-phase-2--the-branch-tree)); used the flattened `feat/<spec>/<feature>-<slice>` form.
- [ ] Spawned a subagent with a full context contract (goal, branch, files, acceptance, non-goals, escalation triggers).
- [ ] Made a small number of tightly scoped commits, keeping the diff under the ~800-line split trigger ([§6](#6-phase-4--pull-requests)); split into a stacked sub-branch mid-implementation if it started growing past.
- [ ] Opened a PR to the parent branch with a complete description.

Per PR:
- [ ] Spawned the review subagent as part of opening the PR — on Opus 5/4.8, context-limited to the PR diff + description only, nothing more.
- [ ] Waited for the **entire CI run** (completed, green or red) AND the **fresh review subagent pass** against the latest diff to finish before making any fix — no reacting to partial signals.
- [ ] Triaged the review subagent's findings at the Opus tier per [§11.1](#111-when-to-decide-vs-ask); delegated decided fixes to Sonnet 5 (or Haiku for pure docs/no-judgment fixes).
- [ ] Posted the review subagent's findings (or a summary) as a PR comment, labelled as the internal review agent's pass.
- [ ] Made the reasonable fixes at own discretion each round; declined/deferred anything that would overcomplicate (noted why).
- [ ] Escalated only genuine forks (multiple reasonable implementations, or a §11 decision); recorded the human's answer.
- [ ] Ran the review loop until it converged (typically 2 rounds); if round 2 still surfaced real substantive issues, split the PR into smaller ones (or simplified the affected section) rather than spawning round 3. Fixed any repeated-pattern finding as a class, not instance-by-instance. Noted the stop-or-split reason on the PR.
- [ ] Merged up with a **merge commit** (not squash, not rebase).
- [ ] Proposed branch deletion to the human.

For `main`:
- [ ] All features merged into root; CI green.
- [ ] Posted consolidated summary to the human.
- [ ] Received **explicit** approval.
- [ ] Merged via merge commit. No force push. No auto-merge.
- [ ] Proposed root-branch deletion.
