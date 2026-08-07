# ARCHITECTURE.md — MrBot1000 Full Program Review & Scope

## Executive Summary

MrBot1000 is a **real-time AI agent system** for automated earning opportunity discovery, execution, and lifecycle tracking. It uses a **multi-agent architecture** with specialized workers communicating via a **SharedContext JSON file**, enabling cross-model coordination between a fast chat model (LFM2.5-1.2B) and accurate main model (Gemma-4-E2B).

**Key Achievements:**
- ✅ Real-time earning pipeline (not simulation)
- ✅ Secure execution with validation pipeline
- ✅ Multi-tier persistent memory (5 levels)
- ✅ Cross-model communication via JSON
- ✅ No cloud dependencies - all local
- ✅ Comprehensive self-improvement system

---

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            MR BOT 1000 v2.0                                  │
│                    Real-Time Earning Agent System                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                    │
│  │   USER      │────▶│ GUI (Tab UI)│────▶│  Manager    │                    │
│  │  (Type msg) │     │ Agents Tab  │     │Thread (QTh)│                    │
│  └─────────────┘     └─────────────┘     └─────────────┘                    │
│                           │                    │                          │
│                           ▼                    ▼                          │
│                   ┌──────────────┐      ┌─────────────┐                      │
│                   │ Summarizer   │      │ Intent      │                      │
│                   │ (Chat Model) │◄────▶│ Classifier  │                      │
│                   └──────────────┘      └─────────────┘                      │
│                           │                    │                          │
│                            ├─question──────────┘                          │
│                            │                                               │
│                            ▼ task                                                    │
│                   ┌──────────────┐                                         │
│                   │    Coder     │     Manager routes to:                   │
│                   │ (Main Model) │──►─→ [Coder, JobSearch, Analyst, etc.] │
│                   └──────────────┘                                         │
│                            │                                                  │
│                            ▼                                                │
│                   ┌──────────────┐                                         │
│                   │ ActionPipeline│◄───── Validate all changes            │
│                   │ (Secure Exec) │                                         │
│                   └──────────────┘                                         │
│                            │                                                  │
│                            ▼                                                │
│                   ┌──────────────┐                                         │
│                   │   Database   │     All events logged                   │
│                   │  (SQLite)    │                                        │
│                   └──────────────┘                                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Deep Dive

### 1. User Interface Layer (`main.py`, `ui.py`)

**MainWindow** (`main.py`):
- QMainWindow with 8-tab interface
- Tab 1: Management (agent controls)
- Tab 2: **Agents** (agent roster + **chat window**)
- Tab 3: Browse Root (file explorer)
- Tab 4: Payments (balance, payouts)
- Tab 5: Earnings (income tracking)
- Tab 6: Settings (configuration)
- Tab 7: Live Logs (debug output)
- Tab 8: DB Stats (database metrics)

**Key Features:**
- UnifiedChatWidget in Agents tab for chat
- Real-time status updates via signals
- Settings saved to `.env` file

### 2. Orchestration Layer (`manager.py`)

**ManagerThread** (QThread):
- Continuous background coordinator
- Heartbeat monitoring (every 60s default)
- Intent classification: `task` vs `command` vs `conversation`
- Routes:
        - `conversation` → **Manager** response path
  - `code task` → **Coder**
  - `job search` → **JobSearch**
  - `analysis` → **Analyst**

**Signal Flow:**
```python
human_queue ──► send_human_message() ──► _handle_chat()
                                           ├─> classify intent
                                           ├─> task/command → manager workflows
                                           └─> conversation → manager answer path
```

### 3. Agent Workers (`agents/`)

#### SummarizerThread (`summarizer.py`)
- **Purpose**: Thought summarization, conversation memory, and contextual support replies
- **Model**: LFM2.5-1.2B (Ollama chat mode)
- **Key Methods**:
  - `send_human_message(text)` - Queue for processing
  - `_handle_chat()` - Main chat logic
- **Features**:
  - Separate SQLite database (summarizer.db)
  - Conversation memory (last 30 exchanges)
  - Speech pattern adaptation

#### CoordinatorWorker (`coordinator.py`)
- **Purpose**: Cross-model communication
- **Mechanism**: SharedContext JSON file (`~/.local/share/mrbot1000/shared_context.json`)
- **Functions**:
  - Routes chat vs main model tasks
  - Maintains shared state pool
  - Mediates model interactions

#### WorkerAgent (`base_worker.py`)
- **Purpose**: Base class for all workers
- **Methods**:
  - `llm()` - Unified LLM interface (chat=True/False)
  - `run_code()` - Safe code execution
  - `research_all()` - File scanning
- **Security**:
  - Path traversal prevention via `Path.is_relative_to()`
  - Clipboard validation (10KB limit, domain blocklist)

#### Other Agents
- **Coder** (`coder.py`): Python coding, code review
- **Analyst** (`analyst.py`): Code analysis, suggestions
- **JobSearch** (`job_search.py`): Reddit/Fiverr/Upwork search
- **EarningDiscoverer** (`earning_discoverer.py`): Opportunity detection
- **AirdropScanner** (`airdrop_scanner.py`): Token airdrop detection
- **SocialEarningProviders** (`social_earning_platform.py`): Platform integration

### 4. Action Pipeline (`action_pipeline.py`)

**5-Stage Secure Execution:**

```
PROPOSE  →  VALIDATE  →  APPROVE  →  EXECUTE  →  NOTIFY
   │           │           │           │           │
Agent    Syntax,Sec,API   Manager   Write/Run  DB/UI
describes                                 Log
action
```

**Validators** (all required):
1. **SecurityValidator** - Path traversal, dangerous patterns
2. **SyntaxValidator** - Python syntax (`ast.parse`)
3. **ImportValidator** - Safe imports only
4. **ApiUsageValidator** - API key patterns
5. **NullSafetyValidator** - None/null checks
6. **SpellingValidator** - Basic spell check

**Action Types:**
- `create_file`, `modify_file`, `delete_file`
- `self_improve` (requires checkbox + score ≥ 0.85)
- `assist_agent` (route to another worker)

### 5. Memory System (`earning_memory.py`)

**5-Tier Architecture:**

| Tier | Storage | Content | Persistence |
|------|---------|---------|-------------|
| 0 | `_conversation` | Recent chat | Session only |
| 1 | `conversation.db` | Chat history | Session |
| 2 | `agent.db` | Actions, heartbeats | Persistent |
| 3 | `summarizer.db` | Patterns, summaries | Persistent |
| 4 | Action logs | Full audit trail | Persistent |

---

## Flow Diagrams

### Chat Flow
```
User types in Agents tab chat
        ↓
UnifiedChatWidget.on_send()
        ↓
main._human_send()
        ↓
ManagerThread.send_human_message()
        ↓
ManagerThread._handle_chat()
        ↓
Intent classified: task / command / conversation
        ↓
conversation path uses Manager chat response
        ↓
Response received (278 chars)
        ↓
chat_reply.emit(label, text)
        ↓
MainWindow._on_chat_reply()
        ↓
agents_tab.append_reply(label, text)
        ↓
Tab switches to Agents, shows response
```

Note: Summarizer still contributes contextual information and background summaries via signal streams; it is not the default direct ingress for Agents-tab human messages.

### Task Flow (Coder Example)
```
User: "Fix the chat routing issue"
        ↓
Intent: "task"
        ↓
_execute_workflow(text, "Coder", skip_research=False)
        ↓
_build_context() → Full code context
        ↓
Coder.run_code() with prompt
        ↓
worker.llm(chat=False) → Main model (gemma-4-E2B)
        ↓
Response with fix suggestion
        ↓
_action_pipeline.process() → Full validation
        ↓
Manager approves
        ↓
_exec_modify_file() → Apply changes
        ↓
log_signal → Live Logs tab
```

---

## Database Schema

### agent.db (Main)
```sql
actions: id, ts, trigger, action_text, outcome
heartbeats: id, agent, status, task, ts
llm_calls: id, provider, model, latency, size, success, ts
opportunities: id, source, title, url, score, ts
```

### summarizer.db (Chat)
```sql
chat_history: id, ts, role, text, session
summaries: id, ts, text, strategy, topics
speech_patterns: id, pattern, frequency
```

### earnings.db (Finance)
```sql
earnings: id, source, amount, status, ts
expenses: id, type, amount, reason, ts
payouts: id, amount, wallet, method, status, ts
```

---

## Security Considerations

### Implemented
- ✅ Path traversal blocking (`Path.is_relative_to()`)
- ✅ Clipboard validation (10KB JSON limit, domain blocklist)
- ✅ Payout limits ($10K max, triple confirmation)
- ✅ Crash log moved to user directory
- ✅ Credentials never hardcoded (`.env` only)

### Validation Pipeline
- All file changes require ≥ 0.85 validation score
- Self-improvement requires checkbox enabled
- Security checks run before any modification

---

## Configuration (.env)

| Variable | Default | Purpose |
|----------|---------|---------|
| `PIPELINE_ALLOW_SELF_IMPROVE` | false | Allow agent code changes |
| `MAX_TOKENS` | 1024 | Response length limit |
| `HEARTBEAT_INTERVAL` | 60 | Status update frequency |
| `STARTUP_DELAY_SECS` | 2 | Initial wait period |
| `RESEARCH_CACHE_TTL` | 300 | File cache expiration |

---

## Performance Characteristics

### Response Times
- **Chat mode** (`chat=True`): 1-3 seconds (LFM2.5-1.2B on CPU)
- **Task mode** (`chat=False`): 15-30 seconds (Gemma-4-E2B on GPU)

### Resource Usage
- **GPU VRAM**: 6GB minimum (Gemma-4-E2B quantized)
- **System RAM**: 32GB recommended
- **Disk**: ~5GB for models + databases

---

## Current Capabilities

### Core Functions
1. **Job Discovery**: Search Reddit, Fiverr, Upwork for earning opportunities
2. **Opportunity Planning**: Turn discovered leads into practical next-step plans
3. **Lifecycle Tracking**: Move opportunities through discovered, applied, in progress, submitted, paid, or failed states
4. **Code Assistance**: Python coding, debugging, suggestions
5. **Conversation**: Natural language chat with context awareness
6. **File Operations**: Safe read/write/delete with validation
7. **Income Tracking**: Log earnings, track payouts
8. **System Updates**: Self-improvement when enabled

### Integration Points
- Ollama local server (required)
- Optional OpenAI/Anthropic APIs
- SQLite for all persistence
- PySide6 for desktop UI

---

## Skill Specifications

Each skill is documented in its own markdown file in `/skills/`:

| File | Purpose | Responsible Agent |
|------|---------|-------------------|
| `social-discovery.md` | Find jobs on Reddit/Fiverr/Upwork | JobSearch |
| `opportunity-evaluation.md` | Score/rank opportunities | Analyst |
| `opportunity-filtering.md` | Filter by risk/reward | JobSearch |
| `opportunity-execution.md` | Execute accepted tasks | Coder |
| `opportunity-lifecycle.md` | Track and summarize opportunity progress | Summarizer/Manager |
| `document-qa.md` | Answer questions about files | Summarizer |

**Template**: `Skill.md` - Use this template to create new skill specifications

---

## Known Limitations

1. **Self-improvement disabled by default** - Requires explicit checkbox enablement
2. **Single GPU acceleration** - Models can't both run on GPU simultaneously
3. **No automatic deployment** - All changes require manual review
4. **Platform-specific** - Currently Windows-focused (MSYS shells)

---

## Roadmap (Future Enhancements)

### Short Term
- [ ] Auto-platform integration (Discord, Telegram)
- [ ] Portfolio-based gig filtering
- [ ] Automated earnings reporting

### Long Term
- [ ] Multi-GPU model loading
- [ ] Cloud API fallback
- [ ] Plugin system for new platforms

---

## Quick Reference

### Run the Program
```bash
python main.py
```

### Key Files
- `main.py` - Application entry point
- `manager.py` - Orchestration
- `action_pipeline.py` - Secure execution
- `agents/summarizer.py` - Chat handling
- `Agent.md` - Agent runtime contract
- `CHANGELOG.md` - Change history

### Debugging
- **Live Logs Tab**: Real-time LLM calls and agent thoughts
- **DB Stats Tab**: Model usage statistics
- **Settings Tab**: Toggle features, adjust limits