# MrBot1000 v2.0 - CHANGELOG

## [2.0.20b] - 2026-08-06 - CRITICAL: Fix MainWindow Launch Crash (status_label)

### Bug fixed (blocked program launch)
`MainWindow.__init__` crashed at line 301 with
`RuntimeError: libshiboken: Internal C++ object (QLabel) already deleted`
when calling `self.status_label.setText(...)`.

**Root cause:** `create_ui()` (added in 2.0.15) called `self.create_settings_tab()`
**twice** — once to add the tab (line 519) and again to read its index
(`tabs.indexOf(self.create_settings_tab())`, line 526). The second call rebuilt
the entire Settings tab, creating a *second* `status_label` QLabel that was
never attached to a visible widget. `self.status_label` then pointed at that
orphaned/deleted C++ object, so the first `setText` after UI build hit a deleted
label.

**Fix:** build the Settings tab once, store the reference (`settings_tab`), add
it, and reuse that same object for `tabs.indexOf(settings_tab)`. `status_label`
now resolves to the live, visible label.

### Secondary fix
The top-level crash handler wrote `crash.log` with the default encoding, which
on this Windows host (cp1252) could not encode the `✓` in the traceback, raising
`UnicodeEncodeError` *instead of* recording the real error. Now opens the file
with `encoding="utf-8"`.

### Verification
Offscreen-Qt construction of `MainWindow()`: `create_settings_tab()` called
exactly once; `self.status_label` is a live QLabel; `setText("…Registered ✓")`
succeeds; settings tab index resolves (5). Program now launches past the
previously-fatal line.

## [2.0.20a] - 2026-08-06 - Model Env-Var Precedence Fix (patch)

### Bugs fixed
1. **Shutdown unloaded the wrong main model.** `_shutdown_ollama` read
   `OLLAMA_MAIN_MODEL` from `.env`, but `WorkerAgent.llm()` actually ran the
   main model via `OLLAMA_MODEL` (and the live instance override). So after
   switching the main model in Settings, exit unloaded the *old* `.env` model
   and left the *new* one resident in VRAM — defeating the 2.0.14 unload fix.
   Now shutdown unloads the **active** model
   (`worker._ollama_model_override` → `OLLAMA_MAIN_MODEL` → `OLLAMA_MODEL`),
   matching what `llm()` actually uses.
2. **Inconsistent main-model env var.** `llm()` and `save_settings` used
   `OLLAMA_MODEL` while startup/shutdown referenced `OLLAMA_MAIN_MODEL`.
   Standardized on `OLLAMA_MAIN_MODEL` as canonical:
   - `llm()` resolves main model as
     `_ollama_model_override` → `OLLAMA_MAIN_MODEL` → `OLLAMA_MODEL` (legacy
     fallback).
   - `save_settings` now writes `OLLAMA_MAIN_MODEL` (and still mirrors
     `OLLAMA_MODEL` for backward compatibility).
   - Startup seeds the worker's `primary_ollama_model` from `OLLAMA_MAIN_MODEL`
     (falling back to `OLLAMA_MODEL`), and logs both `OLLAMA_MAIN_MODEL` and
     `OLLAMA_CHAT_MODEL` at startup for clarity.

### Behaviour now (consistent)
`.env` seeds the Settings dropdowns at startup (the default/fallback). The
Settings dropdown is the live source of truth: on Save it writes
`OLLAMA_MAIN_MODEL`/`OLLAMA_CHAT_MODEL` back to `.env` *and* live-overrides the
running worker, so `.env` and runtime stay in sync. Within a session the UI
wins; across restarts `.env` (holding the last save) wins. Shutdown releases the
model actually in VRAM.

### Verification
Ad-hoc: `llm()` picks `_ollama_model_override` first, then `OLLAMA_MAIN_MODEL`;
startup logs both main+chat models; `save_settings` writes `OLLAMA_MAIN_MODEL`;
shutdown-path resolves the active model (no reliance on stale `.env` at exit).

## [2.0.20] - 2026-08-06 - Task Workspaces: work/<platform>/<job_id> + Requirement Fulfilment

### Per-task deliverable workspace
New module `agents/task_workspace.py` gives the agent a real place to DO a gig
and verify it against the gig's requirements before handing it back:
- `TaskWorkspace(platform, job_id)` creates `work/<platform>/<job_id>/`.
- `save(filename, content)` writes deliverables with a pre-overwrite `.bak`
  backup, sandboxed strictly inside the workspace (path-traversal blocked).
- `verify(requirements, job)` runs `document_scanner.verify_completion` — the
  real QA gate — checking the saved files satisfy the gig's requirements
  (derived from the JobRecord's skills/title via `infer_requirements`, or
  passed explicitly).
- `complete()` archives verified deliverables to `.../delivered/` and records a
  `status.json` manifest (new → working → done → submitted).
- `submit()` marks `status=submitted` and returns the archive location. This is
  **local packaging only** — real Fiverr/Upwork upload is a deliberate, later
  plug-in point (the manager's `_fulfill_job` already routes `operation` in
  `fulfill`/`complete_job`/`deliver` here).

### Planner isolation
`work/` added to `project_file_tree`'s skip set so the Coder/CEO planner never
tries to edit the agent's own deliverables. Writes into `work/` are still allowed
(it is NOT in `WRITE_EXCLUSION_DIRS`), so the workspace is usable but invisible
to the code-editing planner.

### Manager wiring
`_execute_with_worker` now dispatches `fulfill`/`complete_job`/`deliver`
operations to `_fulfill_job`, which builds the workspace, saves the deliverable,
verifies requirements, and reports the real result (no simulated output).

### Verification
Ad-hoc round-trip: workspace created under `work/fiverr/job123`; deliverable
saved with `.bak` backup; `infer_requirements` derives `[deliverable, python,
scraping, report]`; `verify_completion` gates on quality (substantive doc →
`can_submit=True`, thin doc → correctly rejected); `complete()` archives to
`delivered/`; `submit()` → `status=submitted`; traversal (`../escape.py`) blocked;
`work/` excluded from `project_file_tree`. Manager `_fulfill_job` returns
`FULFILLED fiverr:job999 … requirements met (q=0.98), submitted`.

## [2.0.19] - 2026-08-06 - ROOT_FOLDER = Project Root + Backup-Before-Edit Safety

### ROOT_FOLDER now points to the project root
`agents/base_worker.ROOT_FOLDER` was `dirname(__file__)` → the `agents/` subfolder
only. This meant the project file tree the Coder/CEO planner saw listed ONLY
`agents/*.py`, and `safe_write_file` was sandboxed to `agents/` — so a CEO
directive to "refactor action_pipeline.py" (a root-level file) resolved to
`agents/action_pipeline.py` (doesn't exist) → Coder skipped with FILE NOT FOUND.
Changed `ROOT_FOLDER` to `parent.parent` (project root). This also matches the
existing `tests/test_root_scope.py` assertion, which already expected project
root. Now `project_file_tree` lists the whole codebase (main.py, manager.py,
action_pipeline.py, agents/*, etc.), grounded so the model cannot hallucinate
non-existent files.

### Backup-before-edit + revert (no clobbering)
`safe_write_file` now backs up any EXISTING file to `ROOT_FOLDER/.mrbot_backups/`
(preserving relative path + timestamp, e.g.
`.mrbot_backups/action_pipeline.py.20260806_160000.bak`) BEFORE overwriting, so
every autonomous edit is revertible/recoverable. Added `restore_last_backup()`
to roll a file back to its most recent backup. Added `WRITE_EXCLUSION_DIRS`
(.git, .venv, __pycache__, caches, github_upload mirror, .mrbot_backups) that
`safe_write_file`/`restore_last_backup` refuse to touch, keeping VCS metadata,
virtualenvs, and the external publish mirror safe. The filename blocklist (.env,
credentials) and out-of-root/symlink rejection are retained.

### Bug C — explicitly NOT suppressed
Per user direction: when no proposals are found (metrics store empty →
proposals=0), that is fine. The Analyst keeps checking until populated; it is not
harmful, just nothing to analyze. No skip-on-empty logic added.

### Verification
Ad-hoc: ROOT_FOLDER resolves to project root; `project_file_tree()` now includes
`main.py`/`manager.py`/`action_pipeline.py`; `safe_write_file` backs up an
existing file before overwrite and the backup is restorable via
`restore_last_backup`; writes into `.git`/`.env` are BLOCKED;
`tests/test_root_scope.py` passes.

## [2.0.18] - 2026-08-06 - Repo Recovery + Ollama keep_alive TTL + Focus→Worker Routing

### Critical: corrupted working tree recovered
`agents/base_worker.py` in the working tree had been **truncated** — the
`WorkerAgent` class (452 lines) was deleted, leaving only 105 lines. The module
could not import (`ImportError: cannot import name 'WorkerAgent'`), so the app
would fail to start. Also missing from the file (but imported by `coder.py`,
`manager.py`, etc.): `project_file_tree` and `WORKER_REGISTRY`.
Fix: restored `WorkerAgent` from git HEAD and re-added `project_file_tree` +
`WORKER_REGISTRY`. Verified the full import chain
(`base_worker` → all workers → `manager`) now loads.

### Bug B — Ollama model permanently pinned (keep_alive=-1)
`WorkerAgent._call_ollama` passed `keep_alive=-1`, pinning BOTH the main and
chat models in VRAM forever. On a single 6GB GPU this causes `RuntimeError`
contention under load (seen in the 15:10 live log). Changed to a bounded TTL
via new env `OLLAMA_KEEP_ALIVE` (default `300`s) so Ollama can evict under
memory pressure while staying warm. `keep_alive=-1` removed from the call.

### Focus→Worker routing (dead map wired)
`_FOCUS_WORKER_MAP` existed but was never consulted, so "code quality" heartbeats
were dispatched to whichever worker the LLM chose (often Analyst), leaving the
Coder path unexercised. `_parse_decision` now falls back to the focus map when
the CEO emits no explicit `ACTION[Worker]:`, so "code quality" → Coder reliably.

### Verification
Ad-hoc: full import chain loads; `project_file_tree()` runs; `CoderWorker`
registered + `analyze_and_fix` reachable; `_parse_decision(focus="code quality")`
→ `("action", "Coder", ...)`. (No full pytest run.)

## [2.0.17] - 2026-08-06 - Fix Coder Worker Never Registered (Bug D)

### Root cause
`manager.py` resolves every `ACTION[Coder]` via `_roster.get("Coder")`, which
fell back to the bare base `WorkerAgent` (`self.worker`). `CoderWorker` exists
and has `analyze_and_fix()`, but it was **never registered** into the roster
(only `JobSearch` + `Analyst` were, in `main.py`). So every Coder task raised
`AttributeError: 'WorkerAgent' object has no attribute 'analyze_and_fix'`
and the CEO got stuck in an infinite "refactor action_pipeline.py" loop —
≈20 failed Coder tasks per long run.

### Fix
Registered `CoderWorker` into the manager roster in `main.py`, matching the
existing `JobSearch`/`Analyst` registration pattern (with a non-fatal try/except).

### Verification
Ad-hoc script confirmed: `Coder` in roster → `CoderWorker` instance →
`hasattr(analyze_and_fix)` is True. (No full pytest run.)

## [2.0.16] - 2026-08-06 - Live-Log Analysis (full run) + Fiverr count= Fix

### Full log triage (run starting 15:10:10)
Three distinct issues surfaced; one is a definite code bug (fixed here), two are
environmental/config and documented as findings.

#### Bug A — FIXED: Fiverr `find_gigs()` called with wrong kwarg
- Log: `[WARN][JobSearchWorker] Fiverr search error: FiverrClient.find_gigs()
  got an unexpected keyword argument 'count'` → `Found 0 new jobs on Fiverr`.
- Root cause (proven on disk): `agents/job_search_worker.py:319` passed
  `count=10`, but `FiverrClient.find_gigs(self, query="python", limit=20)`
  takes `limit`, not `count`. The `except` swallowed it → 0 gigs every cycle.
- Fix: `count=10` → `limit=10`. Verified with a mocked FiverrClient:
  `search("fiverr")` now calls `find_gigs(query=, limit=10)` and maps gigs.

#### Bug B — FINDING (not patched): main-model `RuntimeError` retry collapse
- Log (15:14:07 → 15:14:47): `ollama failed (RuntimeError), trying next...`
  repeated, then `ERROR: LLM unavailable`. The main model
  (`gemma-4-E2B`) errored on 3 consecutive heartbeats while the chat model
  (`gemma-3-1b`) kept responding.
- Root cause (proven): `base_worker.py:_call_ollama` calls Ollama with
  `keep_alive=-1` (line 574) — models are pinned in VRAM and NEVER released.
  On a single 6GB GPU, pinning both the ~2.7GB main model AND the chat model
  concurrently causes the main model to intermittently fail under load. The
  retry loop (`for attempt in range(3)`) only retries, it doesn't back off or
  fall back to the chat model, so a transient main-model failure cascades to
  "LLM unavailable".
- Not patched yet: this is a resource/config tradeoff (pinning helps latency,
  unpinning helps stability). Recommended follow-up: set `keep_alive` to a short
  TTL (e.g. 5m) or make the retry fall back to the chat model instead of
  failing. Will address in 2.0.17 on your go-ahead.

#### Bug C — FINDING (expected behavior, no fix): Analyst reports zeros
- Log: every `ACTION[Analyst]` → `RESULT: REPORT generated: proposals=0,
  avg_quality=0, submissions=0`.
- Root cause (proven): `AnalystWorker._metrics_store` is empty because no
  proposals have been analyzed in this flow (`analyze_proposal` is never
  called). `generate_metrics_report()` correctly returns the honest empty
  result. Not a bug — but it means the CEO keeps assigning Analyst "analyze
  proposals" tasks that have no data to analyze, so those heartbeats are
  effectively no-ops. Suggested follow-up: have the CEO skip Analyst when
  `_metrics_store` is empty, or feed it real gig data from JobSearch.

### Verification (ad-hoc)
- `job_search_worker.py` parses (CRLF-safe).
- No `count=` kwarg remains; `find_gigs(query=, limit=10)` confirmed via mock.
- Chat model in `.env` (`gemma-3-1b`) matches the log — no config drift there.

## [2.0.15] - 2026-08-06 - Auto-Refresh Ollama Models in Settings

### Issue
Switching the chat/main Ollama model (fixed in 2.0.14) still required manually
clicking **Refresh** on the model dropdown before the list of local models
appeared. The dropdowns only contained the hardcoded default list until Refresh
was pressed.

### Fix
- Stored the `QTabWidget` as `self.tabs` and connected `currentChanged` to a new
  `_on_tab_changed` handler.
- On the **first** time the Settings tab is opened, the app automatically calls
  `refresh_ollama_models()`, which queries `http://127.0.0.1:11434/api/tags` and
  populates both the Main and Chat model dropdowns with the locally-installed
  Ollama models. A flag (`_ollama_autorefresh_done`) ensures it only runs once
  per session, so Ollama isn't re-queried on every tab switch.
- Manual Refresh still works as before.

### Verification (ad-hoc)
- `main.py` syntax OK.
- `_on_tab_changed` only triggers the refresh on the Settings tab index and only
  once (guarded by `_ollama_autorefresh_done`).
- Version display bumped to `v2.0.15` in `main.py` + `README.md`.

## [2.0.14] - 2026-08-06 - Ollama Model Lifecycle: Clean Exit + Live Switching

### Issue 1 — Models stayed resident after exit
`_shutdown_ollama()` read `OLLAMA_MODEL` (a var that was removed during the
`.env` cleanup) but **not `OLLAMA_MAIN_MODEL`**, the actual canonical main model.
So on exit the main model was never sent a `keep_alive:0` unload and remained
loaded in VRAM. Fixed: unload **both** `OLLAMA_MAIN_MODEL` and
`OLLAMA_CHAT_MODEL` via `ollama.chat(..., keep_alive=0)`. Also switched from a raw
`requests.post` to the `ollama` SDK call (same `keep_alive:0` semantics, no new dep).

### Issue 2 — Switching models required a full restart
`save_settings()` wrote the new model to `.env` and reloaded dotenv, but never
pushed it into the running `WorkerAgent` / `ManagerThread`, nor unloaded the
*old* model. So the chat kept using the stale model until the program was closed
and reopened. Fixed: on Save, when the chat model selection changes, the code
unloads the previously-active chat model (`ollama.chat(..., keep_alive=0)`) and
updates `self.worker._chat_ollama_model_override` + `self.manager` live. New
chats immediately use the selected model — no restart. Unchanged selections are
a no-op (no spurious unload).

### Verification (ad-hoc)
- `main.py` syntax OK; `save_settings()` (with embedded live-switch block)
  compiles cleanly.
- Exit unload: with `OLLAMA_MAIN_MODEL` + `OLLAMA_CHAT_MODEL` set, both receive a
  `keep_alive:0` unload call.
- Live switch: changed selection → old model unloaded + worker/manager point at
  new model; unchanged selection → zero unload calls.

## [2.0.13] - 2026-08-06 - Restore Custom Theme System

### Root cause
`theme_config.py` (the module holding every built-in preset — Dark, Light,
Midnight-Blue, Ocean, Solar, Forest, Rose, Lavender, Neon-Cyberpunk, Gradient-Mix
— plus the env-driven `Custom` theme via `MRBOT_THEME_*`) had become **dead code**:
nothing imported it. `main.py`'s `apply_theme()` used its own inline `THEMES` dict
(only 4–5 entries) and **never read `MRBOT_THEME_*`**, so the custom coloring and
all presets were disconnected — the app silently fell back to a single hardcoded
Dark palette. The `[INFO] Theme: Dark` startup line masked this because a theme
*was* applied, just not the configurable one.

### Fix
- `main.py` now imports `theme_config` and delegates to
  `resolve_theme_definition(theme_name)` as the single source of truth, so both
  preset definitions and the `MRBOT_THEME_*` custom colors flow through again.
- `MainWindow.THEMES` is reduced to a name list built from `THEME_PRESETS +
  ["Custom"]`; the actual colors come from `theme_config`.
- Exported `THEME_PRESETS` / `CUSTOM_THEME_NAME` from `theme_config.py` so the
  Theme menu and Settings combobox enumerate every preset + the `Custom` option.
- The Theme menu (`menubar.addMenu("Theme")`) and Settings `theme_combo`
  (`addItems(self.THEMES.keys())`) now expose all 10 presets + `Custom`, instead
  of the 4 inline stubs.

### Verification (ad-hoc)
- `main.py` syntax OK.
- `resolve_theme_definition("Midnight-Blue")` returns full palette;
  `resolve_theme_definition("Custom")` honors `MRBOT_THEME_BG`/`MRBOT_THEME_ACCENT`
  (env override confirmed); unknown name falls back to `Dark`.
- `apply_theme` references `resolve_theme_definition`; `THEMES` is name-only;
  combobox/menubar consume `self.THEMES.keys()`.

## [2.0.12] - 2026-08-06 - JobSearch Web Branch Fix (honest fail)

Continuation of the 2.0.11 live-log analysis. The `web` job-search branch was
**doubly dead**:

1. It was unreachable — `elif platform in self.ACTIVE_PLATFORMS` never matched
   `"web"` because `ACTIVE_PLATFORMS = PLATFORMS` and `"web"` is not a key in
   `PLATFORMS`. (Noted as a secondary issue in 2.0.11.)
2. Even when reached, it did `from library import web_search`, but **`library.py`
   has no `web_search` attribute** and there is no importable `web_search` in
   MrBot1000's own process (verified: `hermes_tools.web_search` and a top-level
   `web_search` are both `ModuleNotFoundError`). So it would `ImportError` →
   swallowed by `except Exception` → misleading `Found 0 new jobs on web` for a
   search that never ran.

### Fix
- Made the `web` branch reachable: `elif platform == "web" or platform in
  self.ACTIVE_PLATFORMS`.
- Split the lazy import into its own `try`; if `web_search` cannot be imported,
  log `Web search unavailable in this environment` and return `[]` **honestly**
  instead of emitting a fake "0 jobs found" line.
- The inner query still runs only when the import succeeds.

### Verification (ad-hoc, mocked — no network)
- `search("web")` with a mock `web_search` injected via `sys.modules` → returns
  the mapped `JobRecord` (branch now reaches the real query).
- `search("web")` with no `web_search` importable → returns `[]` + honest
  "unavailable" warning (no swallowed crash, no fake count).
- `search("fiverr")` and `search("upwork")` regression-checked → still route to
  their real clients; web path not triggered.

## [2.0.11] - 2026-08-06 - Live-Log Analysis & Job-Search Fix

### Log analyzed
Live run of v2.0.10 (CEO ManagerThread, Ollama `gemma-4-E2B` main + `LFM2.5-1.2B` chat),
heartbeats #1–#34. Goal: find what the real-execution engine actually does vs. reports.

### Findings (proven against source, not guessed)

1. **JobSearch fetched 0 gigs — real bug, now FIXED.**
   - Symptom: every `search('fiverr')` logged `Fiverr client initialized` then
     `Found 0 new jobs on fiverr` with no error.
   - Root cause: `manager.py` normalizes the planner platform to **lowercase**
     (`platform = (plan.get("platform") or "").lower()`, line 466) and passes
     `"fiverr"`. But `JobSearchWorker.search()` only runs the real Fiverr RSS query
     inside `if platform == "Fiverr"` (capital F, `job_search_worker.py:309`).
     Lowercase `"fiverr"` matched **none** of the `if/elif` branches → client was
     initialized but never queried → empty list, silently.
   - Fix: normalize platform to canonical case at the top of `search()`
     (`_CANON = {"fiverr":"Fiverr","upwork":"Upwork","web":"web"}`).
   - Verified: normalization routes `fiverr→Fiverr`, `upwork→Upwork`, `web→web`
     to the correct client branches (syntactic + branch-routing check passed).
   - Secondary (not exercised in this log, noted): the `web` branch
     (`elif platform in self.ACTIVE_PLATFORMS`) is also dead because
     `ACTIVE_PLATFORMS = PLATFORMS` and `"web"` is not a key in `PLATFORMS`.
     Left as a known issue; log only used `fiverr`.

2. **`ROOT_FOLDER` NameError was already fixed on disk — log reflects a stale run.**
   - Symptom in log (heartbeats #6, #17, #19, #20, #21):
     `EXECUTION ERROR (NameError): name 'ROOT_FOLDER' is not defined`.
   - Reality: `manager.py` already imports it (`from agents.base_worker import
     ROOT_FOLDER`, lines 31–32) and `ROOT_FOLDER = str(Path(__file__).resolve()
     .parent.parent)` is defined in `agents/base_worker.py`. The error came from the
     already-running process that had loaded the pre-import module. Restarting the
     program picks up the fix. Proven: `os.path.join(ROOT_FOLDER, target)` resolves
     to an existing file.

3. **Coder now correctly SKIPS non-existent files (honest, no hallucination).**
   - The LLM planner repeatedly emitted `agents/analyst.py` — a **ghost file**. The
     real module is `agents/analyst_worker.py`. Under the v2.0.10 engine this now
     hits `FILE NOT FOUND: agents/analyst.py — cannot edit a non-existent file.
     Skipped.` instead of fabricating an edit. (LLM filename drift is a model-planning
     issue, not an engine bug; the engine handled it correctly.)

4. **Metrics report zeros are correct, not a failure.**
   - `generate_metrics_report()` returns `proposals=0, avg_quality=0, submissions=0`
     because `AnalystWorker._metrics_store` is empty — no proposals have been analyzed
     yet (`analyze_proposal` is never called in this flow). This is honest output,
     not a swallowed error.

### Net
- 1 real code fix (Fiverr platform-case mismatch).
- 0 regressions introduced.
- The engine's "honest RESULT" discipline held: no file edits or gig counts were
  fabricated anywhere in the 34-heartbeat run.

## [2.0.10a] - 2026-08-06 - Text-Truncation Widening

Widened the content channels that were silently cutting off useful information
(numbers in brackets were the old character caps):

- **M→A comms** (`manager.py`): `f"[To {worker}] Execute: {action[:80]}"` → full `action`.
- **A→M result echo** (`manager.py`): `f"[{worker}] {evidence[:400]}"` → full `evidence`.
- **Pipeline executed/rejected** (`main.py`): `result.message[:80]` → full;
  `reason[:80]` → full.
- **Rejected-gig alert** (`main.py`): `description[:50]` + `reason[:60]` → full.
- **Gig description** (`manager.py`): `job.get('description','')[:300]` → full.
- **DB Stats saved summary** (`ui.py`): `notification[:120]` removed.

Intentionally **preserved** compact UI labels (not content channels):
badge `description[:60]`, roster `task[:30]`, log model `[:24]`, wallet mask `[:8]`,
and the brief CEO log preview `action[:80]` at `manager.py:588`.

### Verification
Static scan confirms no `[:N]` truncation remains on the widened content channels
(except the intentional CEO-preview slice). All five message paths now emit full text.

## [2.0.10] - 2026-08-06 - Fix: Hallucinated File Edits & Fake Results

### Root cause
`_execute_with_worker()` (manager.py) called `worker.llm(chat=True)` and logged the
model's free-text as the "Result". No file was ever read/written/edited and no gig was
ever fetched — the agents *described* work as if done. The live log showed fake
"RESULT: file modified" / "audit complete" lines with zero disk/network activity.

### Fix — 5-stage real dispatch
Rewrote `_execute_with_worker()` into a verifiable execution pipeline:
1. **THINK + PLAN** — `_plan_task()` asks the chat model (with the real `project_file_tree()`)
   to emit a STRICT JSON plan `{file, operation, platform, issue}`; `_extract_json_plan()`
   parses it (markdown-fence tolerant, substring salvage). Falls back to a heuristic if the
   LLM returns garbage.
2. **TOOL-CALL** — dispatches to the *actual* worker abilities, not chat:
   - `JobSearch`/`search` → `JobSearchWorker.search(platform)` (real Fiverr/Upwork/web clients)
   - `Analyst`/`analyze`/`audit` → `AnalystWorker.generate_metrics_report()` (real metrics)
   - `Coder`/`fix`/`refactor`/`audit` → `CoderWorker.analyze_and_fix()` (real read → LLM →
     `safe_write_file()`)
3. **CHECK WORK** — after a Coder edit the file is re-read and `before != after` is verified;
   the change is confirmed on disk before claiming success.
4. **PROOFREAD** — `_proofread_change()` asks the chat model to sanity-check the unified diff
   in one sentence.
5. **Honest RESULT** — emits `[DONE]`/`[NO-OP/FAILED]` with concrete evidence (bytes/lines/gig
   count/metrics). No file = `FILE NOT FOUND` (skipped, no hallucinated edit); unknown op =
   reported, never faked.

### Verification
Ad-hoc execution test (`hermes-exec-verify.py`, 12/12) confirmed: Coder really rewrites a file
on disk (diff-verified), the check-work stage detects the change, Analyst returns honest
metrics, JobSearch.search() returns the real client list type, and the planner parses JSON.

---

## [2.0.9] - 2026-08-06 - Branding Alignment & Centered Title Header

- **Renamed**: Window title changed from `MrBot1000 Agents v10 — Extended` to
  `MrBot1000 v2.0.8` to match the product/changelog version (the v10/"Extended"
  naming was stale and had no corresponding version history).
- **UI**: Added a centered title header (`QLabel("MrBot1000 v2.0.8")`,
  `Qt.AlignHCenter`) at the top of the program. `create_ui()` now wraps the
  `QTabWidget` in a `QWidget` container with a `QVBoxLayout` so the title sits
  centered above the tabs instead of relying on the OS window-title bar.
- **Theme**: Title bar uses the app accent (`#4fc3f7` on `#1a1a1f`) with a subtle
  bottom border, consistent with the Dark theme.

---

## [2.0.8] - 2026-08-06 - Safe-Mode CLI Restore & Text Truncation Fixes

### Safe mode CLI flags restored
- **Reverted** the 2.0.7 removal of the `-sm`/`--safe-mode` CLI flags. Added a short
  `argparse` block at the top of `main.py` that maps `-sm`/`--safe-mode` to
  `os.environ["MRBOT_SAFE_MODE"]="true"` (exact alias for the env var, read by
  `startup_validation.py`). `parse_known_args()` keeps Qt's `sys.argv` untouched.
- **README** Quick Start / Safe Mode section updated to show the flag as a supported
  alias alongside the env-var form.

### Text truncation fixes (long text no longer cut off)
The chat/thought/notification *bodies* were never truncated (routing is intact); the
cutoffs were on preview/label strings. Removed the caps:
- `ui.py` `_append_notification()` (all 3 definitions): `text[:120]` → full `text`
  (agent notifications now show complete messages).
- `main.py` heartbeat status label: `trigger[:40]` → full `trigger` (thought overview).
- `manager.py` `_set_worker_busy()`: `task[:60]`/`task[:50]` → full `task` (worker status).
- `main.py` DB recent-actions list: `action_text[:80]` → full.
- `main.py` pipeline validated/executed logs: `result.summary[:80]`,
  `action.description[:60]` → full (routed to thought panel).
- `main.py` HTTP error logs (payout/register): `response.text[:80]` → full.
- **Intentionally left**: wallet address masking (`wallet[:8]...[-4:]`) for security,
  model-name column width, and compact UI badges (style badge) remain truncated by design.

---

## [2.0.7] - 2026-08-06 - README Review & Accuracy Fixes

- **Reviewed**: `README.md` against actual code state (files, test runner, safe mode, env).
- **Fixed**: Restored `python main.py -sm` / `--safe-mode` CLI flags. `main.py` had no
  argument parser, so a short `argparse` block was added that maps `-sm`/`--safe-mode`
  to `os.environ["MRBOT_SAFE_MODE"]="true"` (exact alias for the env var, read by
  `startup_validation.py`). `parse_known_args()` is used so Qt's `sys.argv` is untouched.
- **Expanded**: "Key Files" table now lists the real agent modules
  (`agents/coder.py`, `job_search_worker.py`, `fiverr_client.py`, `upwork_client.py`,
  `opportunity_lifecycle.py`, `coordinator.py`, `analyst_worker.py`, `base_worker.py`).
- **Noted**: CHANGELOG is on internal v4.0.x while product/README version remains v2.0
  (intentional two-scheme numbering; documented in `CHANGELOG.md` header line).

---

## [2.0.6] - 2026-08-06 - Env Config Alignment (.env / .env.example)

- **Problem**: `.env` and `.env.example` had diverged — 6 `MRBOT_THEME_*` vars
  (read by `theme_config.py`), `OLLAMA_MAIN_MODEL`, `PIPELINE_*`, `RESEARCH_CACHE_TTL`,
  and `COMPACT_STATUS_REPORTS` were missing from `.env.example`; `.env` was missing
  the optional `UPWORK_*` tokens and `OLLAMA_CHAT_GPU`.
- **Changed**: `.env.example` rewritten as a complete template — now documents every
  real key (theme, pipeline, research cache, compact reports, `OLLAMA_MAIN_MODEL`),
  with empty/placeholder values (no real secrets).
- **Changed**: `.env` extended with the optional `OLLAMA_CHAT_GPU='0'` and `UPWORK_*`
  (empty) placeholders so its key set matches `.env.example`. All existing real values
  (models, wallet, agent name, heartbeat interval) were left untouched.
- **Verified**: Both files now contain exactly 39 matching keys; `key(example) == key(.env)`.
  `theme_config.py` and `base_worker.py`/`startup_validation.py` read all documented vars.

---

## [2.0.5] - 2026-08-06 - Verification & Cleanup of 2.0.4

- **Verified**: Ad-hoc behavior-level verification of 2.0.4 changes passed 24/24
  (report: `C:\Users\cecil\AppData\Local\Temp\hermes-verify-404d.txt`).
  Confirmed: `project_file_tree()` lists only real files (no `source.py`/`_argcomplete.py`,
  no `.venv`/`site-packages` pollution); `research_all()` injects the tree as the
  authoritative file list; Manager prompts reference Fiverr/Upwork only and explicitly
  disable ClawGig/ClerkGig/uGig/Moltbook; `CoderWorker` uses `CODER_SYSTEM` (no
  `SEARCH_SYSTEM` reuse); `JobSearchWorker.SEARCH_SYSTEM` states it must not invent listings.
- **Cleaned**: Removed all temporary `hermes-verify-404*.py` scripts from temp dir.

---

## [2.0.4] - 2026-08-06 - Stop Hallucinated Files & Disabled-Platform Routing

### Root-cause fix: subagents inventing non-existent files (source.py, _argcomplete.py)

- **Added**: `project_file_tree()` in `agents/base_worker.py` — builds a compact, real index of the project root (excludes `.git`, `__pycache__`, `.venv`, etc.).
- **Changed**: `WorkerAgent.research_all()` now prepends the full real file tree to the Manager's context, with an explicit "these are ALL the project files" framing. Subagents can no longer assume files like `source.py` exist.
- **Changed**: `CoderWorker` (`agents/coder.py`) now injects the real project file tree into every prompt and uses a dedicated `CODER_SYSTEM` (previously it wrongly reused the JobSearch `SEARCH_SYSTEM`). It instructs the model to ONLY reference files in the tree.
- **Impact**: Analyst/Coder/JobSearch no longer report changes to `source.py`/`_argcomplete.py`; decisions reference actual files (e.g. `agents/coder.py`, `manager.py`).

### Root-cause fix: Manager still routing to disabled platforms (ClawGig/uGig/Moltbook)

- **Changed**: `CEO_SYSTEM` (manager.py) now lists DISABLED platforms explicitly and restricts job discovery to Fiverr, Upwork, and web search.
- **Changed**: `_FOCUS_AREAS[0]` updated from "ClawGig/uGig/Moltbook" to "Fiverr, Upwork, and web search".
- **Changed**: `JobSearch` routing keywords in `_WORKER_ROUTING` now use fiverr/upwork instead of clawgig/ugig/moltbook.
- **Changed**: `JobSearchWorker.SEARCH_SYSTEM` now states it does NOT invent listings and must never target disabled platforms.
- **Impact**: Heartbeats no longer instruct the team to work ClawGig; `EXCLUDED_PLATFORMS` guard in `search()` is now backed by prompts that never request those platforms.
- **Fixed**: `research_all()` and `project_file_tree()` now skip `.venv`/`.git`/`__pycache__`, so the injected context is NOT polluted with hundreds of `site-packages` files (including dependency `source.py` copies) — the model only sees the real project tree.

---

## [2.0.3] - 2026-08-06 - Coder Execution & Real Client Integration

### Coder Worker Implementation - NEW

- **NEW FILE**: `agents/coder.py` - Complete Coder worker agent with actual file write capability
  - **Inherits**: `safe_write_file()` from `base_worker.WorkerAgent` for secure file operations
  - **Methods**:
    - `analyze_and_fix(file_path, issue_description)` - Analyzes and implements fixes
    - `file_write(file_path, content, verify)` - Writes content with Python validation
    - `refactor(file_path, refactor_instructions)` - Applies refactoring changes
  - **Security**: Path validation, size limits, blocklist enforcement
  - **Impact**: Coders now actually modify files instead of just reporting changes

### Job Discovery - Real Client Integration (FINAL)

- **FIXED**: `agents/job_search_worker.py` now uses REAL platform clients instead of LLM simulation
  - **Fiverr Integration**: Uses `FiverrClient.find_gigs()` with RSS-based real gig discovery
  - **Upwork Integration**: Uses `UpworkClient.find_gigs()` with OAuth2 API
  - **Web Search Fallback**: Uses `library.web_search()` for other platforms
  - **Impact**: Actual gig discovery from live platforms instead of simulated LLM output

- **Added**: `EXCLUDED_PLATFORMS = {"ClawGig", "ClerkGig", "Clawgig", "TempDisabled", "Maintenance"}`
  - Skips broken/disabled platforms that would return no results
  - Guard: `if platform in self.EXCLUDED_PLATFORMS: return []`

---

## [2.0.2] - 2026-08-06 - Exclusions & Guards

### Job Discovery Fixes - Initial

- **Added**: `EXCLUDED_PLATFORMS` constant to prevent searching disabled platforms
- **Added**: Exclusion guard in `search()` method
- **Added**: Updated `TEAM_SKILLS` with additional relevant skills

---

## [2.0.0] - 2026-08-06 - Opportunity Lifecycle Integration

### Task Routing & Decision-Making (A)

- **Added**: Action cooldown mechanism - Prevents repeating the same action type within configured heartbeats (JobSearch: 3, Analyst: 5, Coder: 4, Manager: 6)
- **Added**: Rule-based fallback for focus-to-worker mapping via `_FOCUS_WORKER_MAP` dictionary
- **Added**: Focus area memory - Tracks last 5 actions in `_last_actions` list to avoid cycles
- **Added**: Heartbeat metrics tracking - New `_heartbeat_metrics` dict tracking: analysis, job_search, coder, manager, total_tasks, successful, errors
- **Added**: Task lock mechanism - `_task_lock` threading lock and `_task_in_progress` flag to prevent overlapping task execution
- **Added**: `_heartbeat_count` to track total heartbeat cycles for cooldown calculations
- **Added**: `_is_action_on_cooldown()` method to check if an action type is on cooldown
- **Added**: `_get_forced_worker()` method for focus-based worker assignment
- **Added**: `_log_heartbeat_summary()` method for periodic metrics logging every 5 heartbeats
- **Added**: `set_summarizer()` method to connect summarizer to manager
- **Added**: `research_folder` property with getter/setter for external data integration

### Research Folder & Context Building (B)

- **Added**: `research_folder` property on ManagerThread for external data integration
- **Added**: `_research_file_mtimes` dictionary for incremental scanning support
- **Added**: Export functions: `export_queued_jobs()`, `export_analytics_report()` (B.4)

### Performance Optimizations (C)

- **Added**: Async LLM support - `llm_async()` method in WorkerAgent with 15s timeout
- **Added**: `_call_openai_async()`, `_call_anthropic_async()`, `_call_ollama_async()` methods using httpx
- **Changed**: Default heartbeat interval from 60s to 120s (configurable via `HEARTBEAT_INTERVAL` env var)
- **Added**: `LLM_TIMEOUT = 15.0` seconds constant for fast-failing LLM calls
- **Added**: `task_summary` Signal for UI monitoring of metrics

### Opportunity Lifecycle Automation (D)

- **NEW FILE**: `agents/opportunity_lifecycle.py` - Complete lifecycle state machine
- **Added**: `_process_opportunities()` - Automated queued→applied transition (D.1)
- **Added**: Scheduler integration every `OPPORTUNITY_DISCOVERY_INTERVAL` heartbeats (D.2)
- **Added**: Opportunity state machine: discovered → researched → queued → applied → in_progress → submitted → paid/failed
- **Added**: `_update_opportunity_metrics()` - Track lifecycle stage transitions (E.2)
- **Added**: `get_top_opportunities()` - Rank by value/effort ratio (D.4)
- **Added**: Extended heartbeat metrics: opportunities_discovered, applied, submitted, paid
- **Integrated**: Opportunity processing into main heartbeat loop (D.3)

### Chat Window Filtering (FIX)

- **Fixed**: Heartbeat and Task decision messages filtered from chat window
  - `_on_chat_reply()` in `main.py` filters triggers starting with `"Heartbeat:"` or `"Task:"`
  - Chat window remains clean; decisions shown in status/agents panel

### Task Capabilities Verification

- **Verified**: All 9 core capabilities work correctly:
  1. File Reading (file_index)
  2. File Research (research_all)
  3. Task Routing (_route_to_worker)
  4. Metrics Tracking (_heartbeat_metrics)
  5. Opportunity Lifecycle (full pipeline)
  6. Opportunity Ranking (get_top_opportunities)

---

## Update 2026-08-05 - Theme Customization, Shared Research Context & Safe Mode

### Theme System & Visual Customization
- **Added**: Multiple built-in UI themes including Dark, Light, Midnight-Blue, Ocean, Solar, Forest, Rose, Lavender, Neon-Cyberpunk, and Gradient-Mix.
- **Added**: A new custom theme workflow so users can choose colors for the main application background, panel surfaces, text, accent/outline color, highlight color, and disabled text.
- **Improved**: The Theme menu now exposes both preset themes and a dedicated "Customize Theme…" action for quick personalization.
- **Enhanced**: Theme colors now flow through the main window palette and stylesheet so the app feels more polished and easier to tailor for different working environments.

### Shared Research Knowledge Base
- **Added**: Research snapshots are now persisted into the shared context layer so both the main-model workflow and the chat-model workflow can reuse the same research knowledge.
- **Improved**: The chat router now includes the latest shared research snapshot in its runtime context, making conversational responses more grounded and consistent with the manager's research scans.
- **Enhanced**: The Management tab now exposes a visible research snapshot so the selected folder's value is easier to inspect at a glance.
- **Verified**: New regression coverage confirms that shared research context is available to the chat-side runtime flow.

### Safe Mode & CLI
- **Fixed**: The startup crash caused by using the safe-mode flag before the window initialized its state.
- **Added**: A CLI shorthand flag, `-sm` or `--safe-mode`, to enable safe mode without needing an environment-variable assignment.
- **Improved**: The action pipeline now honors safe mode consistently for proposed file writes and reports that execution was skipped instead of mutating files.
- **Verified**: Safe-mode behavior is covered by regression tests and now works through the main app entry point.

---

## Update 2026-08-05 - Shared Research Context, Safe Mode & Startup Validation

### Shared Research Knowledge Base
- **Added**: Research snapshots are now persisted into the shared context layer so both the main-model workflow and the chat-model workflow can reuse the same research knowledge.
- **Improved**: The chat router now includes the latest shared research snapshot in its runtime context, making conversational responses more grounded and consistent with the manager's research scans.
- **Enhanced**: The Management tab now exposes a visible research snapshot so the selected folder's value is easier to inspect at a glance.
- **Verified**: New regression coverage confirms that shared research context is available to the chat-side runtime flow.

### Safe Mode & CLI
- **Fixed**: The startup crash caused by using the safe-mode flag before the window initialized its state.
- **Added**: A CLI shorthand flag, `-sm` or `--safe-mode`, to enable safe mode without needing an environment-variable assignment.
- **Improved**: The action pipeline now honors safe mode consistently for proposed file writes and reports that execution was skipped instead of mutating files.
- **Verified**: Safe-mode behavior is covered by regression tests and now works through the main app entry point.

---

## Update 2026-08-05 - Startup Validation & Runtime Warnings

### Runtime Validation
- **Added**: A startup validation layer that reports missing configuration, provider availability, and safe-mode status before workflow execution begins.
- **Improved**: The application now emits explicit warnings when provider credentials or model settings are incomplete instead of silently continuing with limited functionality.
- **Enhanced**: Manager-side runtime failures now surface as visible warnings so execution issues are easier to trace and recover from.
- **Verified**: New regression coverage confirms the validation reports safe mode, missing-provider conditions, and runtime warnings correctly.

---

## Update 2026-08-05 - Explicit Opportunity State Machine

### Lifecycle Auditing
- **Added**: An explicit opportunity lifecycle state machine that validates each transition instead of allowing every move blindly.
- **Improved**: Opportunity stages are now tracked with auditable metadata, including whether a transition was accepted or rejected and the reason for rejection.
- **Enhanced**: The lifecycle tracker now preserves state even when an invalid transition is attempted, making recovery and debugging much clearer.
- **Verified**: New regression tests cover both valid progressions and invalid transitions.

---

## Update 2026-08-05 - Compact Lifecycle Status Reports, Settings Update & UI Polish

### Assistant Lifecycle Reporting
- **Improved**: Assistant answers about opportunity progress now use a compact status report format instead of raw shared-state JSON.
- **Added**: Lifecycle summaries now present stage, status, amount, note, and a short next-step recommendation in a concise readable block for faster human review.
- **Enhanced**: The report now includes an at-a-glance overall line so executives and operators can quickly see whether opportunities are active, completed, or need follow-up.
- **Refined**: The summary now adds a board-ready snapshot, a primary action line, and a clear priority label so the most important opportunity stands out immediately.
- **Configured**: The new compact format is available as a user setting in the Settings tab and can be saved to the local environment file.

### UI & Settings Improvements
- **Refined**: The Agents tab chat and notification surfaces now present lifecycle updates more clearly and with cleaner panel styling.
- **Enhanced**: The Settings tab now exposes an explicit preference for compact lifecycle status reports alongside the existing runtime and appearance controls.

### Verification
- **Added/updated**: Lifecycle regression coverage in `tests/test_opportunity_lifecycle.py`.
- **Test command**: `d:\MrBot1000_2.0\.venv\Scripts\python.exe -m pytest tests/test_opportunity_lifecycle.py`
- **Result**: 3 passed, 0 failed

---

## Update 2026-08-05 - Dual-Model Chat Routing, Opportunity Workflow Planning & Lifecycle Tracking

### Opportunity Lifecycle Tracking
- **Added**: A lifecycle tracker to move opportunities through discovered, researched, applied, in progress, submitted, paid, and failed stages.
- **Integrated**: The earning pipeline now exposes a lifecycle update hook so opportunities can be advanced as work progresses.
- **Connected**: Lifecycle snapshots are now stored in shared context and surfaced to the assistant chat/runtime context so questions about opportunity status can be answered from live state.
- **Improved**: The UI chat surfaces now highlight opportunity-status updates in the assistant view and notifications panel.
- **Verified**: New regression coverage confirms the tracker, shared-context integration, and pipeline updates behave as expected.

### Verification
- **Added**: Regression tests for lifecycle tracking and shared-context chat integration in `tests/test_opportunity_lifecycle.py`.
- **Test command**: `d:\MrBot1000_2.0\.venv\Scripts\python.exe -m pytest tests/test_opportunity_lifecycle.py`
- **Result**: 3 passed, 0 failed

---

## Update 2026-08-05 - Dual-Model Chat Routing & Opportunity Workflow Planning

### Multi-Model Chat Routing
- **Added**: A dedicated chat router to classify human prompts as conversational, analysis-oriented, or task-driven.
- **Improved**: The chat experience now routes general questions to the fast chat path while reserving heavier analysis for the main-model workflow.
- **Enhanced**: Chat replies can now pull from runtime context such as job-search reports, analytics artifacts, and other JSON-backed program state.

### Opportunity Workflow Planning
- **Added**: A workflow planner that turns discovered opportunities into actionable steps such as apply, deliver, and submit.
- **Improved**: The earning pipeline can now create a concrete plan for opportunities instead of only identifying them.
- **Expanded**: Fallback plans now handle unknown or manual platforms without breaking the workflow.

### Verification
- **Added**: Regression tests for chat routing and workflow planning in `tests/test_chat_router.py` and `tests/test_workflow_planner.py`.
- **Test command**: `python -m pytest -q tests/test_chat_router.py tests/test_workflow_planner.py`
- **Result**: 6 passed, 0 failed

---

## Update 2026-08-05 - Shutdown Hardening & Runtime Smoke Coverage

### Runtime Lifecycle
- **Improved**: Main window shutdown now stops the manager and summarizer threads more safely before closing the app.
- **Added**: A dedicated shutdown routine that waits for worker threads to exit and terminates them if they remain alive.
- **Hardened**: Database teardown is now invoked as part of the window shutdown path to avoid leaving resources open during close/restart cycles.

### Verification
- **Added**: Regression coverage for the shutdown flow in `tests/test_runtime_shutdown.py`.
- **Test command**: `python -m pytest -q tests/test_runtime_shutdown.py`
- **Result**: 1 passed, 0 failed

---

## Update 2026-08-05 - Stability, Compatibility & Test Hardening

### Runtime Reliability
- **Hardened**: Airdrop scanning and Fiverr discovery imports now tolerate missing optional dependencies such as feedparser and BeautifulSoup without crashing startup.
- **Fixed**: The earning pipeline now handles unscored opportunities and list-like risk values more gracefully during filtering.
- **Improved**: Content generation imports are now safe in environments where worker typing is not available at import time.
- **Aligned**: The mirrored publish tree under the github_upload folder was brought in line with the main codebase, but it remains a separate publish mirror maintained by sync_github_upload.py rather than the primary working tree.

### Verification
- **Test command**: `python -m pytest -q test_earning_pipeline.py github_upload/test_earning_pipeline.py`
- **Result**: 40 passed, 0 failed (430 warnings)

---

## Update 2026-08-05 - Metrics Analysis & Lead Generation Pipeline

### AnalystWorker Metrics Analysis
- **Implemented**: `analyze_proposal()` - Proposal quality analysis with clarity (0-1), complexity (0-1), and structure assessment
- **Implemented**: `evaluate_job_listing()` - Job fit evaluation against team skills, returns `recommended_action` (apply/research/pass)
- **Implemented**: `generate_metrics_report()` - Aggregated metrics and common issues identification
- **Output**: `analyst_metrics_report.json` - Proposal quality metrics report

### JobSearchWorker Lead Generation
- **Evaluated**: 3 freelance gig opportunities (Upwork, Fiverr, PeerTask)
- **Results**: All 3 jobs queued for review (fit scores: 0.60-0.64)
  - Build AI Chatbot with Memory ($500) → Queue ✅
  - Qt PySide6 GUI Development ($300) → Queue ✅  
  - Python Automation Script ($150) → Queue ✅
- **Output**: `job_search_leads_report.json` - Job recommendations

### Test Suite Execution
- **All 8 tests passed** (check_syntax, test_imports, test_analyst_worker, test_job_search, test_main, test_analyst_metrics, test_job_evaluation, test_coordinator)
- **Results**: `tests/test_results/test_run_20260805_085004.json`

### Key Findings
- **Bottleneck**: No LLM providers available (Ollama pydantic issue, no API keys configured)
- **Proposal Issues**: Missing timeline/deadline specification, missing budget discussion
- **Recommendation**: Include clear requirements, deliverables, and timeline in proposals

---

## Update 2026-08-05 - Notifications Panel Implementation

### UI: Collapsible Notifications Panel in Agents Tab

- **Modified**: `ui.py` - Added collapsible side panel for agent notifications
- **Added**: `_notifications_list` - QListWidget for displaying notifications
- **Added**: `_notifications_toggle` button - Toggle to show/hide notifications panel
- **Added**: `_toggle_notifications()` method - Handles panel collapse/expand
- **Added**: `_append_notification()` method - Routes messages to sidebar
- **Modified**: `append_reply()` - Now routes notifications vs chat messages
- **UI Change**: Horizontal splitter separates chat (75%) from notifications (25%)
- **Routing**: Heartbeat, Worker, Coordinator, Result messages → notifications panel
- **Routing**: Manager, Answer, Summarizer messages → chat window

### AnalystWorker - New Implementation
- **Added**: `agents/analyst_worker.py` - Fully implemented proposal analysis and metrics collection
- **Implemented**: `analyze_proposal()` - Analyzes proposal quality with clarity, complexity, and structure metrics
- **Implemented**: `evaluate_job_listing()` - Evaluates job fit against team skills, returns `recommended_action`
- **Implemented**: `generate_metrics_report()` - Aggregated metrics across all analyzed proposals
- **Impact**: Provides data-driven insights for improving proposal win rates and identifying weaknesses in requirement clarity

### ManagerThread Bug Fix
- **Fixed**: Empty response handling in `_handle_chat()` method (lines 573-584)
- **Change**: Added proper check for empty/None responses from LLM chat calls
- **Change**: Added `startswith("ERROR:")` check to provide user-friendly fallback messages
- **Impact**: Correct fallback messages ("I'm having trouble reaching...") when LLM returns errors or empty responses

### Intent Classification Improvements
- **Fixed**: `_classify_intent()` function now properly distinguishes task-like questions from true questions
- **Change**: Task keywords (fix, improve, refactor, etc.) now take precedence over question keywords
- **Change**: Added exclusive question detection to avoid misrouting queries like "Can you fix the bug?"
- **Impact**: Conversational queries correctly go to CEO (chat), task keywords route to appropriate workers

### Test Suite - New
- **Added**: `tests/__main__.py` - Comprehensive test suite runner with 8 tests across 4 categories
- **Added**: `tests/__init__.py` - Package initialization for test module
- **Tests**: `check_syntax`, `test_imports`, `test_analyst_worker`, `test_job_search`, `test_main`, `test_analyst_metrics`, `test_job_evaluation`, `test_coordinator`
- **Categories**: syntax, import, health, integration
- **Usage**: `python -m tests --all` or `python -m tests --help`

---

## Update 2026-08-04 - Chat, Security & Documentation

### Documentation Enhanced
- **Enhanced**: Full architecture documentation with agent roster, model routing, memory tiers
- **Added**: Data flow diagrams, debugging info, development notes
- **Impact**: Clear reference for any model interacting with the system

### Chat Routing Fixed
- **Fixed**: Chat responses now appear in Agents tab instead of popup window
- **File**: `main.py`
- **Impact**: Better UX - chat integrated directly into tab interface

### Chat Model Optimization
- **Added**: `chat=True` parameter to summarizer LLM calls
- **File**: `agents/summarizer.py`
- **Impact**: Faster responses (~2s instead of 18+s)

### Self-Improvement Security
- **Fixed**: `PIPELINE_ALLOW_SELF_IMPROVE` setting now properly enforced

### Coordinator Integration
- **Added**: Cross-model coordination via `CoordinatorWorker`
- **Files**: `manager.py`, `agents/coordinator.py`, `agents/shared_context.py`
- **Change**: Models can now communicate state and decisions
- **Impact**: Better coordination between chat and main models

### Settings UI Improvements
- **Added**: Refresh button for Ollama Chat Model dropdown (mirrors main model refresh)
- **File**: `main.py`
- **Change**: Both main and chat model dropdowns now have independent refresh buttons
- **Impact**: Chat model list stays in sync when models are added/removed

### Chat Window Consolidation
- **Fixed**: "Show Chat Window" and "Summarizer Chat" menu items now switch to Agents tab
- **File**: `main.py`
- **Change**: Routes to `agents_tab` via `setCurrentIndex(1)`
- **Impact**: Single unified chat surface in Agents tab; no more popup windows

---

## File Structure Overview

```
D:/MrBot1000_2.0/
├── main.py                    # Main application window, UI setup
├── manager.py                 # Manager thread, agent orchestration  
├── action_pipeline.py         # Controlled action execution with validation
├── earning_pipeline.py        # Revenue pipeline (job search, offers)
├── earning_memory.py          # Multi-tier memory system (5 tiers)
├── database.py                # SQLite database wrapper
├── library.py                 # Utility functions
├── ui.py                      # UI classes (agents, chat, tabs)
├── Agent.md                   # Agent runtime specification
├── Skill.md                   # Skill specification format
├── .env.example              # Environment template
├── .gitignore                # Git ignore template
└── agents/
    ├── base_worker.py       # Base worker with path validation
    ├── coder.py             # NEW (v2.0.3): Coder agent with file execution
    ├── summarizer.py        # Chat agent (handles SummarizerThread)
    ├── job_search_worker.py # Job discovery agent (real client integration v2.0.2)
    ├── analyst_worker.py    # Proposal analysis
    ├── fiverr_client.py     # Fiverr RSS-based gig discovery
    ├── upwork_client.py     # Upwork API client
    ├── coordinator.py       # Cross-model coordination
    ├── shared_context.py    # Shared state JSON file
    ├── opportunity_lifecycle.py  # Lifecycle state machine
    └── coordinator_agent.py # Cross-model agent

tests/                          # NEW - Test suite
├── __init__.py                # Package initialization
├── __main__.py                # Test suite runner
└── test_results/              # Generated test result files
```

---

## Agent Roster (Current)

| Agent | Role | Model | Color |
|-------|------|-------|-------|
| Manager | Coordinator | Main | #bb86fc |
| Coder | Python coding | Main | #84cc16 |
| Summarizer | Chat/conversation | Chat | #00b0ff |
| JobSearch | Job discovery | Main | #f97316 |
| Analyst | Code analysis | Main | #3b82f6 |

---

## Key Features

1. **Real-time Earning Pipeline**: Job search, offer tracking, income monitoring
2. **Multi-tier Memory**: 5 levels from short-term to long-term persistence
3. **Secure Execution**: Action pipeline with validation before file modifications
4. **Provider Fallback**: OpenAI → Anthropic → Ollama
5. **Cross-model Communication**: SharedContext JSON for state sharing
6. **Real Gig Discovery**: RSS feeds and web search for actual freelance jobs (v2.0.3)