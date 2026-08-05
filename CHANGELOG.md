# MrBot1000 v2.0 - CHANGELOG

## Update 2026-08-05 - Shared Research Context, Safe Mode & Startup Validation

### Shared Research Knowledge Base
- **Added**: Research snapshots are now persisted into the shared context layer so both the main-model workflow and the chat-model workflow can reuse the same research knowledge.
- **Improved**: The chat router now includes the latest shared research snapshot in its runtime context, making conversational responses more grounded and consistent with the manager’s research scans.
- **Enhanced**: The Management tab now exposes a visible research snapshot so the selected folder’s value is easier to inspect at a glance.
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

### Chat Routing Fixed
- **Fixed**: Chat responses now appear in Agents tab instead of popup window
- **File**: `main.py`
- **Change**: `_on_summarizer_chat_reply` handler routes to `agents_tab.append_reply()`
- **Impact**: Better UX - chat integrated directly into tab interface

### Chat Model Optimization
- **Added**: `chat=True` parameter to summarizer LLM calls
- **File**: `agents/summarizer.py`
- **Change**: Faster responses (~2s instead of 18+s) by using chat model directly
- **Impact**: Smoother chat experience

### Self-Improvement Security
- **Fixed**: `PIPELINE_ALLOW_SELF_IMPROVE` setting now actually enforced
- **Files**: `main.py`, `action_pipeline.py`
- **Change**: Added proper permission check before executing self-improve actions
- **Impact**: Safety - unchecked checkbox prevents automatic code modifications

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
    ├── coder.py             # Coding agent
    ├── summarizer.py        # Chat agent (handles SummarizerThread)
    ├── job_search_worker.py # Job discovery agent
    ├── analyst_worker.py    # Proposal analysis (IMPLEMENTED)
    ├── coordinator.py       # Cross-model coordination
    ├── shared_context.py    # Shared state JSON file
    └── coordinator_agent.py # Cross-model agent

tests/                          # NEW - Test suite
├── __init__.py                # Package initialization
├── __main__.py                # Test suite runner
└── test_results/              # Generated test result files
    └── test_run_YYYYMMDD_HHMMSS.json
```