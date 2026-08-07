# Live-Log Triage Method (MrBot1000)

How to analyze a pasted live run and turn it into a grounded CHANGELOG entry + fix.

## Step 0 — separate SIMULATION from REAL EXECUTION
Recall THE SIMULATION TRAP. A "RESULT:" line is not proof of work. But ALSO: a
traceback in the log is not necessarily a bug in the CURRENT code.

## Step 1 — Stale-process check (do this BEFORE "fixing" any log error)
When a log shows an exception the current source shouldn't have:
1. grep the disk for the symbol: `grep -rn "ROOT_FOLDER" manager.py`
2. confirm import/definition exists:
   `python3 -c "from agents.base_worker import ROOT_FOLDER; print(ROOT_FOLDER)"`
3. prove the expression resolves against a real file:
   `python3 -c "import os; from agents.base_worker import ROOT_FOLDER; print(os.path.isfile(os.path.join(ROOT_FOLDER,'agents/base_worker.py')))"`
If all green → the on-disk code is correct; the error came from an already-running
process that predates the fix. Recommend RESTART; do NOT write another patch.

## Step 2 — Symptom → root-cause against source (ground, don't guess)
For each suspicious log line, find the emitting code and read it:
- `Fiverr client initialized` + `Found 0 new jobs on fiverr` with no error
  → client built but never queried. Check the `if platform == "Fiverr"` branch vs what
  `manager.py` actually passes (lowercase from `platform.lower()`).
- `RESULT: proposals=0, avg_quality=0, submissions=0`
  → not a failure; `AnalystWorker._metrics_store` is empty (`analyze_proposal` never
  called in this flow). Honest output.
- `FILE NOT FOUND: agents/analyst.py — cannot edit a non-existent file. Skipped.`
  → CORRECT behavior. The LLM named a ghost file; the real module is `analyst_worker.py`.

## Step 3 — Fix only REAL bugs, prove with a mocked unit test
For a genuine bug (e.g. the platform case-mismatch):
1. Patch the source.
2. Write a temp ad-hoc verify script
   (`C:\Users\cecil\AppData\Local\Temp\hermes-verify-*.py`) that stubs/mocks the worker
   (`unittest.mock.MagicMock`) so NO network/disk is hit, exercises the fixed path,
   asserts the real client is called, and asserts no regression on the uppercase
   control. Run from repo dir with `python3`.
3. Report PASS lines; DELETE the temp script.
If a check FAILS due to a harness bug — e.g. you bypassed `__init__` and hit a missing
attribute like `log_signal` — that is NOT a code failure; fix the harness and re-run.

## Step 4 — Record as a top-of-CHANGELOG entry (append-only, never overwrite)
Newest first: `## [2.0.11] ...` with Findings (proven against source), Net (fix count,
regressions), and a discipline note. The 2.0.10a truncation-widening entry is the
sibling pattern.

## The Fiverr case-mismatch fix (concrete)
`manager.py`: `platform = (plan.get("platform") or "").lower()` → `"fiverr"`
`job_search_worker.py` `search()` branches on `if platform == "Fiverr"` → dead for lowercase.
Fix (top of `search()`):
```python
_CANON = {"fiverr": "Fiverr", "upwork": "Upwork", "web": "web"}
platform = _CANON.get((platform or "").strip().lower(), platform)
```
Verify: mocked `search("fiverr")` calls `find_gigs`; `search("Fiverr")` still routes.

## 2026-08-06 Full Run Triage (log 15:10:10 → 15:14:57)

Findings from a complete ~5min autonomous run:

1. **Bug A (FIXED in 2.0.16)**: `job_search_worker.py:319` called
   `find_gigs(count=10)` but `FiverrClient.find_gigs(query, limit)` has no
   `count` param → `TypeError` swallowed by `except` → 0 gigs every cycle.
   Fix: `count=10` → `limit=10`.

2. **Bug B (FINDING)**: main model `gemma-4-E2B` hit `RuntimeError` 3×
   (15:14:07–15:14:47) while chat model `gemma-3-1b` stayed healthy. Root
   cause: `_call_ollama` uses `keep_alive=-1` (pinned in VRAM, never released).
   On a single 6GB GPU, pinning both models concurrently causes main-model
   instability; the retry loop has no backoff/fallback, so it collapses to
   `ERROR: LLM unavailable`. Follow-up: short `keep_alive` TTL or chat-model
   fallback (candidate for 2.0.17).

3. **Bug C (FINDING, expected)**: every `ACTION[Analyst]` returned
   `proposals=0` because `_metrics_store` is empty (no proposals analyzed).
   Honest output, but the CEO keeps dispatching Analyst at empty data →
   no-op heartbeats. Follow-up: skip Analyst when store empty, or route real
   JobSearch gig data into it.

Discipline note: the `Theme: Dark` line confirmed the restored theme system
(2.0.13) is live; the `setWindowTitle`/`header` now both read `v2.0.15` after
the 2.0.15 bump corrected a missed window-title string.

## 2026-08-06 Long Run Triage (log 15:16:17 → 15:44:24, 61+ heartbeats)

This run is the same binary as the 15:10 run — it was started BEFORE the 2.0.16
fix. The `count=` Fiverr errors that re-appear here (15:32:18, 15:32:36) are the
STALE binary, not the current code. Confirmed on disk: `job_search_worker.py`
calls `find_gigs(query=, limit=10)` and `grep` finds no `count=` in any project
`.py`. **Do not "re-fix" Fiverr — it is already fixed in 2.0.16.**

New finding (the dominant failure of this run):

4. **Bug D (FIXED in 2.0.17)**: `ACTION[Coder]` dispatched ~20× and EVERY one
   crashed with `AttributeError: 'WorkerAgent' object has no attribute
   'analyze_and_fix'`. Root cause: `CoderWorker` was never registered into the
   manager roster (`_roster`). `main.py` only registered `JobSearch` + `Analyst`;
   so `ACTION[Coder]` fell back to the bare base `WorkerAgent` (`self.worker`),
   which lacks `analyze_and_fix`. Consequence: the Coder could never complete, so
   the CEO looped endlessly on "refactor action_pipeline.py" / "harden
   action_pipeline.py". Fix: register `CoderWorker` in `main.py` (try/except,
   non-fatal), matching the JobSearch/Analyst pattern. Verified: `Coder` in
   roster → `CoderWorker` instance with `analyze_and_fix`.

Pattern reinforced: when an exception appears in a log, always (a) check whether
the running process predates the fix, and (b) trace the dispatch path, not just
the erroring method. The fix was in the *caller's* roster, not in `coder.py`.

## 2026-08-06 Dispatched-Task Loop Analysis (companion to 2.0.17)

The 8-cycle "worker focus" rotation (job search → proposal quality → code
quality → worker coordination → error handling → agent speed → security →
revenue) repeats ~7× across this log. Combined with Bug D, the run never escaped
the "code quality" cycle because the Coder never produced a result. With Bug D
fixed, the Coder can now actually execute file edits, so the loop should
progress. Remaining loop quality issues (not defects, FYIs):
- Heartbeats 1–8 and 9–16 dispatch near-identical directives (the rotation
  restarts rather than escalating). Low priority.
- Every Analyst call returns `proposals=0` (Bug C) — see 2.0.16 notes.
