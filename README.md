# MrBot1000 v2.0 — AI-Powered Earning Agent - W.I.P not a final product

A real-time AI agent system for automated earning opportunity discovery, execution, and lifecycle tracking. Runs entirely locally — no cloud dependencies, no data sharing.

## Quick Start

```bash
python main.py
```

To exercise the workflow without making real changes, run either of these:

```bash
python main.py -sm
# or
python main.py --safe-mode
```

This is equivalent to enabling `MRBOT_SAFE_MODE=true` for the session.

Requires: Python 3.11+, Ollama (local LLM server), and the packages listed in requirements.txt.

## What It Does

- Scans Reddit, Fiverr, Upwork, and other platforms for earning opportunities
- Evaluates and ranks opportunities using a local Ollama model
- Executes safe, repeatable actions with full validation
- Tracks opportunities through discovery, application, submission, and payout stages with explicit state transitions
- Surfaces startup warnings and runtime issues so configuration gaps are visible early
- Supports a safe mode that validates actions and skips real file changes while the workflow is being exercised
- Shares research snapshots across the manager and chat-side runtime context so both models can benefit from the same knowledge base
- Tracks earnings and payouts locally in SQLite

## Current Workflow

1. Discover opportunities from supported sources
2. Evaluate and filter them by value, risk, and fit
3. Create a concrete next-action plan for the best options
4. Execute approved steps safely and record the result
5. Update the opportunity lifecycle so the assistant can answer progress questions from live state

## Running Tests

```bash
# Run all tests
python -m tests --all

# List available tests
python -m tests --list

# Run specific test
python -m tests --test check_syntax

# Run test category
python -m tests --category import
```

### Test Categories

| Category | Tests | Purpose |
|----------|-------|---------|
| `syntax` | `check_syntax` | Validate Python syntax across all files |
| `import` | `test_imports`, `test_analyst_worker`, `test_job_search`, `test_main` | Verify module imports work |
| `health` | `test_analyst_metrics`, `test_job_evaluation`, `test_coordinator` | Check agent functionality |
| `integration` | `run_full_suite`, `run_quick_check` | Run comprehensive test bundles |

Test results are saved to `tests/test_results/test_run_YYYYMMDD_HHMMSS.json`.

## Architecture

- **Main model** (gemma-4-E2B or any model): GPU - Heavy analysis, code work, decisions
- **Chat model** (LFM2.5-1.2B or any model): CPU - Fast conversation (~2s responses)
- **Multi-agent system**: Manager, Coordinator, Coder, Summarizer, JobSearch, Analyst
- **Cross-model communication**: SharedContext JSON file
- **Opportunity lifecycle tracking**: Explicit, auditable stage transitions for discovered, researched, applied, in progress, submitted, paid, and failed opportunities
- **Startup validation**: Checks configuration, provider availability, and safe-mode status before workflows begin
- **Secure execution**: 5-stage action pipeline with validation

## UI Layout

The Agents tab contains:

1. **Chat Window** (center) - Conversational interface with main model
2. **Agent Roster** (right side) - Live agent status indicators
3. **Notifications Panel** (collapsible) - Agent actions, heartbeat logs, system events

> **Change in v2.0**: Notifications and agent actions are now separated from the chat window into a collapsible side panel. The chat window remains clean for conversational flow with the main AI model.

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Application entry point, UI setup |
| `manager.py` | Agent orchestration, intent routing, chat handling |
| `action_pipeline.py` | Secure code execution with validation |
| `earning_pipeline.py` | Income tracking pipeline |
| `Agent.md` | Agent runtime contract & rules |
| `ARCHITECTURE.md` | Full system architecture documentation |
| `CHANGELOG.md` | Change history |
| `tests/__main__.py` | Test suite runner |

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Key settings:
- `OLLAMA_MODEL` — Main model for heavy work
- `OLLAMA_CHAT_MODEL` — Chat model (smaller, faster)
- `OLLAMA_CHAT_GPU=0` — Chat model runs on CPU (offload from GPU)
- `PIPELINE_ALLOW_SELF_IMPROVE` — Enable/disable auto code updates

## UI Tabs

1. **Management** — Agent controls, pause/resume
2. **Agents** — Agent roster, chat interface, live status
3. **Browse Root** — File explorer
4. **Payments** — Balance, payouts
5. **Earnings** — Income tracking
6. **Settings** — Model selection, configuration
7. **Live Logs** — Debug output
8. **DB Stats** — Database metrics

## Agent Roster

| Agent | Role | Model | Purpose |
|-------|------|-------|---------|
| Manager | Coordinator | Main | Orchestrates tasks, routes to workers |
| Coordinator | Cross-model | Main | Bridges chat and main models via SharedContext |
| Coder | Coding | Main | Code refactoring, bug fixes, implementation |
| Summarizer | Chat | Chat | Handles conversational queries |
| JobSearch | Job Discovery | Main | Finds gigs on Reddit, Fiverr, Upwork |
| Analyst | Analysis | Main | Proposal metrics, job evaluation |

## Security

- Path traversal prevention (`Path.is_relative_to()`)
- Clipboard validation (10KB limit, domain blocklist)
- Payout limits ($10K max, triple confirmation)
- Credentials in `.env` only, never hardcoded
- Crash logs in user directory

## Documentation

- **Agent.md** — Runtime contract for any model interacting with the system
- **ARCHITECTURE.md** — Full system design and component reference
- **Skill.md** — Template for creating new skill specifications
- **skills/** — Individual skill definitions including social-discovery, evaluation, filtering, execution, lifecycle tracking, and document QA

## Requirements

- Python packages from requirements.txt:
  - PySide6 for the desktop UI
  - ollama for local LLM integration
  - python-dotenv for .env-based configuration
  - requests for HTTP/network access
  - anthropic and openai for optional cloud-provider integrations
  - feedparser and beautifulsoup4 for feed and HTML-based discovery
- GPU: 6GB VRAM minimum (GTX 1660 Super or better)
- RAM: 32GB recommended
- Storage: 5GB+ for models and databases
- Ollama server running locally at `127.0.0.1:11434`