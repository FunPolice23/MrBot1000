"""
manager.py - Agent Orchestration and Management
===============================================

AGENT COORDINATION LAYER

This file contains:
- ManagerThread class: Main orchestration thread
- Agent lifecycle: Start, pause, stop agents
- Task routing: Human input -> appropriate agent
- Intent classification: question vs task routing
- Heartbeat: Agent status monitoring

USAGE:
    managed by MainWindow in main.py

KEY COMPONENTS:
    - ManagerThread: QThread that runs continuously
    - _classify_intent(): Determines question vs task routing
    - _handle_human_message(): Routes user input to agents
    - stop(): Graceful shutdown
    - CoordinatorWorker: Cross-model communication

AGENT ROUTING:
    - question/conversation -> Summarizer (chat mode!)
    - code task            -> Coder
    - job search           -> JobSearch
    - analysis             -> Analyst

TODO: Add more detailed section markers for each method group
"""

"""
manager.py — CEO ManagerThread  (v4 — Shared Context Edition)

The Manager now acts as a CEO managing a team of specialized workers:
  • Maintains a worker roster (name → WorkerAgent subclass)
  • Routes tasks to the most appropriate worker based on specialty
  • Monitors the job search queue and assigns gigs to workers
  • Coordinates multi-worker tasks via shared context
  • SharedContext enables cross-model communication between main and chat models
  • New signals: worker_assigned, job_found, model_signal
"""

import json
import queue
import re
import time
import os
from typing import Dict, List, Optional
from PySide6.QtCore import QThread, Signal

# Worker imports
from agents.base_worker import WorkerAgent
from agents.job_search_worker import JobSearchWorker
from agents.summarizer import SummarizerThread
from agents.coordinator import CoordinatorWorker


class SimpleLogger:
    """Simple logger wrapper for workers that don't have access to QSignal."""
    def __init__(self):
        self.messages = []
    
    def emit(self, msg):
        self.messages.append(msg)
        print(f"[Coordinator] {msg}")

# ─────────────────────────────────────────────────────────────────────────────
#  Focus areas rotate through per heartbeat
# ─────────────────────────────────────────────────────────────────────────────
_FOCUS_AREAS = [
    "job search — find high-value gigs on Reddit, Fiverr, Upwork, social platforms",
    "proposal quality — improve win rate on open gigs",
    "code quality — refactor and harden agent code",
    "worker coordination — review task queue and reassign stale tasks",
    "error handling — identify and fix reliability gaps",
    "agent speed — reduce latency in the main execution loop",
    "security — audit file access and sandboxing rules",
    "revenue — identify highest-paying achievable gig types",
]

_INTENT_KEYWORDS = {
    "task":     ["improve", "fix", "refactor", "add", "implement", "update",
                 "create", "optimize", "scan", "review", "build", "write"],
    "question": ["what", "how", "why", "which", "when", "status", "explain",
                 "tell me", "describe", "show", "list", "report"],
    "command":  ["pause", "stop", "start", "reset", "clear", "run", "execute",
                 "assign", "search", "scan jobs"],
}

# Worker specialty routing keywords (updated for active platforms)
_WORKER_ROUTING = {
    "JobSearch":  ["job", "gig", "find work", "search", "reddit", "fiverr",
                   "upwork", "social", "earn", "apply", "listing", "opportunity"],
    "Analyst":    ["analyse", "analyze", "metric", "report", "complexity",
                   "duplicate", "quality", "debt", "chart", "stats"],
    "Summarizer": ["summary", "explain", "simplify", "translate", "describe",
                   "what happened", "tldr", "recap"],
    "Coder":      ["code", "bug", "fix", "refactor", "implement", "write",
                   "function", "class", "module", "file", "python"],
}


def _classify_intent(text: str) -> str:
    lower = text.lower()
    scores = {intent: sum(1 for kw in kws if kw in lower)
              for intent, kws in _INTENT_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "question"


def _route_to_worker(text: str) -> str:
    lower = text.lower()
    scores = {role: sum(1 for kw in kws if kw in lower)
              for role, kws in _WORKER_ROUTING.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "Coder"


# ─────────────────────────────────────────────────────────────────────────────
#  ManagerThread
# ─────────────────────────────────────────────────────────────────────────────
class ManagerThread(QThread):
    # ── Thought channels ──────────────────────────────────────────────────────
    manager_thought = Signal(str)        # Manager's own reasoning
    agent_thought   = Signal(str)        # Forwarded worker reasoning
    comms           = Signal(str, str)   # (direction, message)

    # ── UI signals ────────────────────────────────────────────────────────────
    log          = Signal(str)
    chat_reply   = Signal(str, str)      # (label, full_text)
    agent_status = Signal(str, str)      # (status, task)

    # ── New roster/job signals ────────────────────────────────────────────────
    worker_assigned = Signal(str, str)   # (worker_name, task_summary)
    job_found       = Signal(str)        # JSON of job list

    # ── Backward compat ───────────────────────────────────────────────────────
    thought = Signal(str, str)

    # ══════════════════════════════════════════════════════════════════════════
    #  Prompts
    # ══════════════════════════════════════════════════════════════════════════

    CEO_SYSTEM = (
        "You are the CEO of MrBot1000, an autonomous AI freelance agency. "
        "You manage a team of specialized AI workers:\n"
        "  • Coder        — Python coding, refactoring, bug fixing\n"
        "  • Analyst      — code quality metrics, complexity, reports\n"
        "  • JobSearch    — finds gigs on Reddit, Fiverr, Upwork, social platforms\n"
        "  • Summarizer   — explains agent activity in plain language\n"
        "  • Earning      — manages crypto airdrops, DeFi, microtasks\n"
        "Core goals: 1) Win profitable gigs  2) Improve team code  "
        "3) Maximize USDC earnings  4) Self-upgrade workers.\n"
        "Given the context, decide the single most impactful next action.\n"
        "Respond in EXACTLY one of these formats:\n"
        "  ACTION[Coder]: <specific coding task>\n"
        "  ACTION[Analyst]: <analysis task>\n"
        "  ACTION[JobSearch]: <search/apply task>\n"
        "  ACTION[Manager]: <direct management task>\n"
        "  ACTION[Summarizer]: <summarization task>\n"
        "  NO_ACTION: <brief reason>\n"
        "  ESCALATE: <reason needing human input>\n"
        "Keep response under 180 words. Reference filenames when possible."
    )

    WORKER_SYSTEM = (
        "You are an autonomous worker in the MrBot1000 AI agency. "
        "Your manager has given you a task. Execute it precisely:\n"
        "1. Identify exactly which file(s) need changing\n"
        "2. Describe the specific line-level changes\n"
        "3. Explain the expected improvement\n"
        "Always cite filenames. Keep response under 200 words.\n"
        "End with: RESULT: [what was done or found]"
    )

    CHAT_SYSTEM = (
        "You are the CEO of MrBot1000, an autonomous AI freelance agency. "
        "The human operator is asking you a question or issuing a command. "
        "You have a team of specialized workers: Coder, Analyst, JobSearch, Summarizer. "
        "Respond as an executive: direct, specific, action-oriented. "
        "Reference actual files and workers when relevant. "
        "If it's a task, plan who on your team would handle it and how. "
        "Max 200 words."
    )

    INDEX_PROMPT = (
        "You are the CEO of a Python AI agency. "
        "Given this file index, pick the 5-8 most important files to review "
        "for the current focus area.\n"
        "Reply ONLY with a JSON array of relative file paths.\n"
        "No markdown fences. No other text."
    )

    def __init__(self, api_key, worker, db=None):
        super().__init__()
        self.api_key = api_key
        self.worker  = worker     # base WorkerAgent (Coder by default)
        self.db      = db
        self.running = True
        self.paused  = False

        self.task_queue  = queue.Queue()
        self.human_queue = queue.Queue()

        self._heartbeat_interval = int(os.getenv("HEARTBEAT_INTERVAL", 60))
        self._research_cache_ttl = int(os.getenv("RESEARCH_CACHE_TTL", 120))
        self._focus_index    = 0
        self._reviewed_files = set()
        self._last_actions: List[str] = []
        self._last_llm_time  = 0.0
        self._min_llm_gap    = 2.0

        self._last_research      = None
        self._last_research_time = 0.0

        self._chat_history: List[dict] = []
        self._chat_queue = queue.Queue()  # For routing chat to Summarizer
        self._summarizer = None  # Set via set_summarizer()

        # ── Worker roster ──────────────────────────────────────────────────────
        # name → {"worker": WorkerAgent, "busy": bool, "current_task": str}
        self._roster: Dict[str, dict] = {
            "Coder": {"worker": worker, "busy": False, "current_task": ""}
        }
        # Coordinator handles chat+main model collaboration
        self._log = SimpleLogger()  # Temporary logger
        self._coordinator = CoordinatorWorker(None, self._log.emit, db=self.db)
        self._roster["Coordinator"] = {
            "worker": self._coordinator,
            "busy": False,
            "current_task": "",
            "specialty": "cross-model communication"
        }
        self._job_queue: List[dict] = []   # queued gigs from JobSearchWorker
        self._shared_ctx: Optional[object] = None  # Lazy-loaded

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def HEARTBEAT_INTERVAL(self):
        return self._heartbeat_interval

    @HEARTBEAT_INTERVAL.setter
    def HEARTBEAT_INTERVAL(self, v):
        self._heartbeat_interval = max(10, int(v))

    # ── Shared Context ────────────────────────────────────────────────────────

    @property
    def shared_context(self):
        """Access to SharedContext for cross-model communication."""
        if self._shared_ctx is None:
            from agents.shared_context import get_shared_context
            self._shared_ctx = get_shared_context()
        return self._shared_ctx

    def update_shared_state(self, key: str, value, model_name: str = "Manager"):
        """Update shared context with a decision or result."""
        ctx = self.shared_context
        ctx.update_model_context(
            model_name,
            current_task=key,
            reasoning_chain=[f"Update: {key} = {value}"],
            key_decisions=[{"decision": key, "value": value, "at": time.time()}]
        )
        self._m_think(f"Shared state: {key} = {value}")

    # ── Roster management ─────────────────────────────────────────────────────

    def register_worker(self, name: str, worker_obj, specialty: str = ""):
        self._roster[name] = {
            "worker": worker_obj,
            "busy":   False,
            "current_task": "",
            "specialty": specialty,
        }
        self._m_think(f"Worker registered: {name} ({specialty})")

    def get_free_worker(self, preferred: str = None) -> Optional[str]:
        if preferred and preferred in self._roster:
            if not self._roster[preferred]["busy"]:
                return preferred
        for name, info in self._roster.items():
            if not info["busy"]:
                return name
        return None

    def _set_worker_busy(self, name: str, task: str):
        if name in self._roster:
            self._roster[name]["busy"] = True
            self._roster[name]["current_task"] = task
            self.worker_assigned.emit(name, task[:60])
            self.agent_status.emit(f"{name}: Working", task[:50])

    def _set_worker_free(self, name: str):
        if name in self._roster:
            self._roster[name]["busy"] = False
            self._roster[name]["current_task"] = ""
            self.agent_status.emit(f"{name}: Ready", "")

    def on_new_jobs(self, jobs: list):
        for jr in jobs:
            self._job_queue.append(jr.to_dict())
        if jobs:
            self.job_found.emit(json.dumps([j.to_dict() for j in jobs]))
            self._m_think(
                f"Job queue updated: {len(self._job_queue)} gig(s) waiting")

    # ── Public interface ──────────────────────────────────────────────────────

    def send_human_message(self, text: str):
        self.human_queue.put(text)

    def queue_task(self, task: str):
        self.task_queue.put(task)

    def invalidate_cache(self):
        self._last_research      = None
        self._last_research_time = 0.0

    def set_paused(self, paused: bool):
        self.paused = paused

    # ── Logging helpers ───────────────────────────────────────────────────────

    def _m_think(self, text: str):
        self.manager_thought.emit(text)
        self.thought.emit("Manager", text)
        if self.db:
            try: self.db.log_thought("Manager", text)
            except Exception: pass

    def _a_think(self, text: str):
        self.agent_thought.emit(text)
        self.thought.emit("Agent", text)
        if self.db:
            try: self.db.log_thought("Agent", text)
            except Exception: pass

    def _communicate(self, direction: str, text: str):
        self.comms.emit(direction, text)
        self.thought.emit("Comms", f"[{direction}] {text}")
        if self.db:
            try: self.db.log_thought("Comms", f"[{direction}] {text}")
            except Exception: pass

    def _sys(self, text: str):
        self.thought.emit("System", text)
        if self.db:
            try: self.db.log_thought("System", text)
            except Exception: pass

    # ── Research helpers ──────────────────────────────────────────────────────

    def _get_research(self, force: bool = False) -> dict:
        now = time.time()
        if (force or self._last_research is None
                or (now - self._last_research_time) > self._research_cache_ttl):
            self._m_think("Scanning files for research context…")
            self._last_research      = self.worker.research_all()
            self._last_research_time = now
            r = self._last_research
            if self.db:
                try: self.db.save_research_cache(r)
                except Exception: pass
            self._m_think(
                f"Scan complete — root: {len(r['root']):,} chars | "
                f"research ({r['research_path'] or 'not set'}): "
                f"{len(r['research']):,} chars | "
                f"{r.get('research_file_count', 0)} file(s)"
            )
        return self._last_research

    def _pick_files_via_llm(self, task_hint: str = "") -> list:
        index = self.worker.file_index()
        if index.startswith("("):
            return []
        if self._reviewed_files:
            lines = [l for l in index.splitlines()
                     if not any(f in l for f in self._reviewed_files)]
            if len(lines) > 5:
                index = "\n".join(lines[:60])
        hint = f"Task context: {task_hint}\n\n" if task_hint else ""
        self._m_think(f"Phase 1 — file selection ({len(index)} chars)")
        raw = self._llm_call(self.INDEX_PROMPT, f"{hint}{index}", "file_selection")
        if raw.startswith("ERROR:"):
            return []
        self._m_think(f"Phase 1 selected: {raw[:200]}")
        try:
            clean = re.sub(r"```[a-z]*|```", "", raw).strip()
            files = json.loads(clean)
            if isinstance(files, list):
                result = [str(f) for f in files]
                self._reviewed_files.update(result)
                return result
        except Exception as e:
            self._m_think(f"JSON parse error: {e}, trying regex…")
            matches = re.findall(r'"([^"]+)"', raw)
            if matches:
                self._reviewed_files.update(matches)
                return matches
        return []

    def _build_context(self, research: dict, task_hint: str = "") -> str:
        DIRECT_LIMIT = 40_000
        root_text     = research.get("root", "")
        research_text = research.get("research", "")
        research_path = research.get("research_path") or "not set"
        root_part     = root_text[:4000]

        # Roster context with job queue
        roster_lines = []
        for name, info in self._roster.items():
            status = "BUSY" if info["busy"] else "free"
            task   = f" → {info['current_task'][:40]}" if info["busy"] else ""
            roster_lines.append(f"  {name}: {status}{task}")
        roster_str = "\n".join(roster_lines)

        job_queue_str = ""
        if self._job_queue:
            job_queue_str = (
                "\n=== QUEUED GIGS ===\n" +
                "\n".join(
                    f"  [{j.get('platform','')}] {j.get('title','')[:60]} "
                    f"${j.get('budget',0):.0f} score={j.get('score',0):.2f}"
                    for j in self._job_queue[:5]
                )
            )

        if len(research_text) <= DIRECT_LIMIT:
            self._m_think(f"Direct context: {len(research_text):,} chars")
            return "\n".join([
                "=== TEAM ROSTER ===",
                roster_str, "",
                "=== AGENT SOURCE FILES (root) ===",
                root_part or "(none)", "",
                f"=== RESEARCH FOLDER: {research_path} ===",
                research_text,
                job_queue_str,
            ])

        self._m_think(
            f"Large context ({len(research_text):,} chars) — two-phase")
        selected = self._pick_files_via_llm(task_hint)
        if selected:
            self._m_think(f"Phase 2 reading {len(selected)} files")
            file_content = self.worker.read_specific_files(selected)
        else:
            file_content = research_text[:DIRECT_LIMIT] + "\n…[truncated]"

        return "\n".join([
            "=== TEAM ROSTER ===",
            roster_str, "",
            "=== AGENT SOURCE FILES (root) ===",
            root_part or "(none)", "",
            f"=== RESEARCH FOLDER: {research_path} ===",
            f"(Showing {len(selected) if selected else 'truncated'} "
            f"of {research.get('research_file_count','?')} files)", "",
            file_content,
            job_queue_str,
        ])

    # ── LLM helpers ───────────────────────────────────────────────────────────

    def _rate_limit(self):
        elapsed = time.time() - self._last_llm_time
        if elapsed < self._min_llm_gap:
            time.sleep(self._min_llm_gap - elapsed)

    def _llm_call(self, system: str, user: str, trigger: str) -> str:
        self._rate_limit()
        t0 = time.time()
        result = self.worker.llm(system=system, user=user)
        self._last_llm_time = time.time()
        latency = int((time.time() - t0) * 1000)
        if self.db:
            try:
                self.db.log_llm_call(
                    model=getattr(self.worker, "last_model", "unknown"),
                    provider=getattr(self.worker, "last_provider", "unknown"),
                    trigger=trigger,
                    prompt_chars=len(system) + len(user),
                    response_chars=len(result),
                    latency_ms=latency,
                    error=result if result.startswith("ERROR:") else None
                )
            except Exception:
                pass
        return result

    def _history_suffix(self) -> str:
        if not self._last_actions:
            return ""
        lines = ["\nRecent actions (avoid repeating):"]
        for a in self._last_actions[-6:]:
            lines.append(f"  • {a}")
        return "\n".join(lines)

    # ── Decision + execution cycle ────────────────────────────────────────────

    def _ceo_decide(self, trigger_label: str, prompt: str) -> str:
        full = prompt + self._history_suffix()
        self._m_think(f"Forming decision for: {trigger_label}")
        self.agent_status.emit("Thinking", trigger_label)
        decision = self._llm_call(self.CEO_SYSTEM, full, trigger_label)
        self._m_think(f"Decision: {decision}")
        return decision

    def _parse_decision(self, decision: str):
        lower = decision.lower()
        # ACTION[Worker]: task
        m = re.search(r"action\[(\w+)\]:\s*(.+)", decision, re.IGNORECASE)
        if m:
            return "action", m.group(1).strip(), m.group(2).strip()
        if "no_action" in lower:
            reason = re.sub(r"no_action:?\s*", "", decision, flags=re.IGNORECASE).strip()
            return "no_action", "", reason
        if "escalate:" in lower:
            reason = decision.split("ESCALATE:")[-1].strip()
            return "escalate", "", reason
        # Legacy ACTION: format
        if "action:" in lower:
            action = decision.split("ACTION:")[-1].strip()
            worker = _route_to_worker(action)
            return "action", worker, action
        return "unclear", "", decision

    def _execute_with_worker(self, worker_name: str, action: str, context: str) -> str:
        # Get the actual worker object
        info = self._roster.get(worker_name, self._roster.get("Coder"))
        w = info["worker"] if isinstance(info, dict) else self.worker

        self._set_worker_busy(worker_name, action)
        self._communicate("M→A",
                          f"[To {worker_name}] Execute: {action[:80]}")
        self._a_think(f"[{worker_name}] Received task: {action}")
        self._a_think(f"[{worker_name}] Analysing relevant files…")

        agent_prompt = (
            f"You are the {worker_name} worker.\n"
            f"Manager directive: {action}\n\n"
            f"Available file context:\n{context[:8000]}"
        )
        result = w.llm(system=self.WORKER_SYSTEM, user=agent_prompt, chat=True)

        self._a_think(f"[{worker_name}] Result:\n{result}")
        result_short = (result.split("RESULT:")[-1].strip()
                         if "RESULT:" in result else result)
        return result_short

    def stop(self):
        """Signal the manager to stop running."""
        self.running = False
        self._m_think("Manager stop requested")

    def set_summarizer(self, summarizer):
        """Set the summarizer instance (called from MainWindow)."""
        self._summarizer = summarizer
        
    def route_chat(self, text: str):
        """Route a chat message to the summarizer (thread-safe)."""
        if self._summarizer:
            self._summarizer.send_human_message(text)
        else:
            self.log.emit("Summarizer not ready")

    # ── Main execution ──────────────────────────────────────────────────────

    def run(self):
        self.log.emit("ManagerThread started")
        self.agent_status.emit("Ready", "Waiting for work")

        while self.running:
            if self.paused:
                time.sleep(1)
                continue

            # Check human queue first
            try:
                human_msg = self.human_queue.get_nowait()
                self._handle_human_message(human_msg)
            except queue.Empty:
                pass

            # Check task queue
            try:
                task = self.task_queue.get_nowait()
                self._handle_task(task)
            except queue.Empty:
                pass

            # Periodic heartbeat
            self._heartbeat()

            # Process any new jobs from JobSearchWorker
            for job in self._job_queue[:3]:
                self._process_job(job)

            time.sleep(0.5)

        self.log.emit("ManagerThread stopped")

    def _handle_human_message(self, text: str):
        self._m_think(f"Human: {text[:80]}")
        intent = _classify_intent(text)
        self._m_think(f"Intent classified: {intent}")

        if intent == "task":
            # Direct task - route to appropriate worker
            worker = _route_to_worker(text)
            self._execute_workflow(text, worker)
        elif intent in ("conversation", "question"):
            # Chat - route to Summarizer's chat interface (uses chat model)
            self.route_chat(text)
        else:
            # Other - use CEO to decide (might route to Coder, JobSearch, etc.)
            decision = self._ceo_decide("human_chat", text)
            self._process_decision(decision, "human_chat")

    def _handle_task(self, task: str):
        self._m_think(f"Task from queue: {task[:50]}")
        worker = _route_to_worker(task)
        self._execute_workflow(task, worker)

    def _execute_workflow(self, task: str, worker_name: str, skip_research: bool = False):
        # For chat-only tasks (Summarizer), skip the expensive research step
        if not skip_research:
            context = self._get_research()
        else:
            context = {"mode": "chat_only"}
        full_context = self._build_context(context, task)

        result = self._execute_with_worker(worker_name, task, full_context)
        self._last_actions.append(f"{worker_name}: {task[:40]}")

        # Update shared state
        self.update_shared_state(f"action_{int(time.time())}", task[:60])

    def _process_decision(self, decision: str, trigger: str):
        dtype, worker, payload = self._parse_decision(decision)

        if dtype == "action":
            self._execute_workflow(payload, worker)
        elif dtype == "no_action":
            self._m_think(f"No action: {payload}")
        elif dtype == "escalate":
            self._communicate("ESCALATE", payload)

    def _process_job(self, job: dict):
        if job.get("status") != "queued":
            return
        # Job is ready for assignment
        self._m_think(f"Processing job: {job.get('title', '')[:40]}")

    def _heartbeat(self):
        self._focus_index = (self._focus_index + 1) % len(_FOCUS_AREAS)
        if int(time.time()) % self.HEARTBEAT_INTERVAL == 0:
            focus = _FOCUS_AREAS[self._focus_index]
            self.agent_status.emit("Heartbeat", focus)