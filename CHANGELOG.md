# MrBot1000 v2.0 - CHANGELOG

## [4.0.6] - 2026-08-06 - Env Config Alignment (.env / .env.example)

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

## [4.0.5] - 2026-08-06 - Verification & Cleanup of 4.0.4

- **Verified**: Ad-hoc behavior-level verification of 4.0.4 changes passed 24/24
  (report: `C:\Users\cecil\AppData\Local\Temp\hermes-verify-404d.txt`).
  Confirmed: `project_file_tree()` lists only real files (no `source.py`/`_argcomplete.py`,
  no `.venv`/`site-packages` pollution); `research_all()` injects the tree as the
  authoritative file list; Manager prompts reference Fiverr/Upwork only and explicitly
  disable ClawGig/ClerkGig/uGig/Moltbook; `CoderWorker` uses `CODER_SYSTEM` (no
  `SEARCH_SYSTEM` reuse); `JobSearchWorker.SEARCH_SYSTEM` states it must not invent listings.
- **Cleaned**: Removed all temporary `hermes-verify-404*.py` scripts from temp dir.

---

## [4.0.4] - 2026-08-06 - Stop Hallucinated Files & Disabled-Platform Routing

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

## [4.0.3] - 2026-08-06 - Coder Execution & Real Client Integration

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

## [4.0.2] - 2026-08-06 - Exclusions & Guards

### Job Discovery Fixes - Initial

- **Added**: `EXCLUDED_PLATFORMS` constant to prevent searching disabled platforms
- **Added**: Exclusion guard in `search()` method
- **Added**: Updated `TEAM_SKILLS` with additional relevant skills

---

## [4.0.0] - 2026-08-06 - Opportunity Lifecycle Integration

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
    ├── coder.py             # NEW (v4.0.3): Coder agent with file execution
    ├── summarizer.py        # Chat agent (handles SummarizerThread)
    ├── job_search_worker.py # Job discovery agent (real client integration v4.0.2)
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
6. **Real Gig Discovery**: RSS feeds and web search for actual freelance jobs (v4.0.3)