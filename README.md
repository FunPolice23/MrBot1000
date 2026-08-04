# MrBot1000 v2.0 — AI-Powered Earning Agent

A real-time AI agent system for automated earning opportunity discovery and execution. Runs entirely locally — no cloud dependencies, no data sharing.

## Quick Start

```bash
python main.py
```

Requires: Python 3.11+, Ollama (local LLM server), PySide6.

## What It Does

- Scans Reddit, Fiverr, Upwork, and other platforms for earning opportunities
- Evaluates and ranks opportunities using a local Ollama model
- Executes safe, repeatable actions with full validation
- Tracks earnings and payouts locally in SQLite

## Architecture

- **Main model** (I use gemma-4-E2B - GPU): Heavy analysis, code work, decisions
- **Chat model** (I use LFM2.5-1.2B - CPU): Fast conversation (~2s responses)
- **Multi-agent system**: Manager, Coordinator, Coder, Summarizer, JobSearch, Analyst
- **Cross-model communication**: SharedContext JSON file
- **Secure execution**: 5-stage action pipeline with validation

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Application entry point, UI setup |
| `manager.py` | Agent orchestration, intent routing |
| `action_pipeline.py` | Secure code execution with validation |
| `earning_pipeline.py` | Income tracking pipeline |
| `Agent.md` | Agent runtime contract & rules |
| `ARCHITECTURE.md` | Full system architecture documentation |
| `CHANGELOG.md` | Change history |

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
- **skills/** — Individual skill definitions (social-discovery, evaluation, filtering, execution, document-qa)

## Requirements

- GPU: 6GB VRAM minimum (GTX 1660 Super or better)
- RAM: 32GB recommended, depending on models or model used.
- Storage: 5GB+ for models and databases
- Ollama server running locally at `127.0.0.1:11434`
