# MrBot1000 v2.0 - CHANGELOG

## Update 2026-08-04 - Chat, Security & Documentation

### Documentation Enhanced
### Agent.md Comprehensive Update
- **Enhanced**: Full architecture documentation with agent roster, model routing, memory tiers
- **Added**: Data flow diagrams, debugging info, development notes
- **Impact**: Clear reference for any model interacting with the system
 - Chat and Self-Improvement Fixes

### Chat Routing Fixed
- **Fixed**: Chat responses now appear in Agents tab instead of popup window
- **File**: `main.py`
- **Change**: `_on_summarizer_chat_reply` handler now routes to `agents_tab.append_reply()`
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

### ClawGig Deprecation Removed
- **Removed**: All references to discontinued ClawGig service
- **Files**: `main.py`, `manager.py`, documentation
- **Impact**: Cleaner codebase, updated prompts to Reddit/Fiverr/Upwork

### Settings UI Improvements
- **Added**: Refresh button for Ollama Chat Model dropdown (mirrors main model refresh)
- **File**: `main.py`
- **Change**: Both main and chat model dropdowns now have independent refresh buttons
- **Impact**: Chat model list stays in sync when models are added/removed

### Chat Window Consolidation
- **Fixed**: "Show Chat Window" and "Summarizer Chat" menu items now switch to Agents tab instead of opening popup windows
- **File**: `main.py`
- **Change**: `_show_chat()` and `_show_summarizer_chat()` route to `agents_tab` via `setCurrentIndex(1)`
- **Impact**: Single unified chat surface in Agents tab; no more popup windows

---

## [2026-08-03] - Core Architecture Stabilization

### Worker Method Restoration
- **Restored**: `research_all()`, `file_index()`, `read_specific_files()` methods
- **File**: `agents/base_worker.py`
- **Impact**: Core functionality restored for agent operations

### Summary Building Fixed
- **Fixed**: `_build_summary` method signature updated
- **File**: `agents/summarizer.py`
- **Impact**: Proper context building for chat interactions

---

## [2026-08-02] - Security Hardening

### Path Validation
- **Fixed**: Path traversal vulnerability using `Path.is_relative_to()`
- **File**: `agents/base_worker.py`
- **Impact**: Secure file access

### Crash Log Security
- **Fixed**: Crash log path moved to user directory
- **File**: `main.py`
- **Impact**: Prevents system directory writes

### Environment Files
- **Added**: `.env.example` and `.gitignore` templates
- **Impact**: Better security practices

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
    ├── job_search.py        # Job discovery agent
    ├── coordinator.py       # Cross-model coordination
    ├── shared_context.py    # Shared state JSON file
    └── coordinator_agent.py # Cross-model agent
```

---

## Agent Roster (Current)

| Agent | Role | Model | Color |
|-------|------|-------|-------|
| Manager | Coordinator | Main | #bb86fc |
| Coordinator | Cross-model bridge | Main | #a855f7 |
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