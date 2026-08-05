"""
manager.py — CEO ManagerThread  (v3 — Worker Roster Edition)

The Manager now acts as a CEO managing a team of specialized workers:
  • Maintains a worker roster (name → WorkerAgent subclass)
  • Routes tasks to the most appropriate worker based on specialty
  • Monitors the job search queue and assigns gigs to workers
  • Coordinates multi-worker tasks
  • Separate chat prompt that stays management-focused
  • New signals: worker_assigned, job_found
"""

import json
import queue
import re
import time
import os
from typing import Dict, List, Optional
from PySide6.QtCore import QThread, Signal

# ─────────────────────────────────────────────────────────────────────────────
#  Focus areas rotate through per heartbeat
# ─────────────────────────────────────────────────────────────────────────────
_FOCUS_AREAS = [
    "job search — find high-value gigs on ClawGig/uGig/Moltbook",
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

# Worker specialty routing keywords
_WORKER_ROUTING = {
    "JobSearch":  ["job", "gig", "find work", "search", "clawgig", "ugig",
                   "moltbook", "freelance", "earn", "apply", "listing"],
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
        "  • JobSearch    — finds gigs on ClawGig, uGig, Moltbook\n"
        "  • Summarizer   — explains agent activity in plain language\n"
        "Core goals: 1) Win profitable gigs  2) Improve team code  "
        "3) Maximize USDC earnings  4) Self-upgrade workers.\n"
        "Given the context, decide the single most impactful next action.\n"
        "Respond in EXACTLY one of these formats:\n"
        "  ACTION[Coder]: <specific coding task>\n"
        "  ACTION[Analyst]: <analysis task>\n"
        "  ACTION[JobSearch]: <search/apply task>\n"
        "  ACTION[Manager]: <direct management task>\n"
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

        # ── Worker roster ──────────────────────────────────────────────────────
        # name → {"worker": WorkerAgent, "busy": bool, "current_task": str}
        self._roster: Dict[str, dict] = {
            "Coder": {"worker": worker, "busy": False, "current_task": ""}
        }
        self._job_queue: List[dict] = []   # queued gigs from JobSearchWorker

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def HEARTBEAT_INTERVAL(self):
        return self._heartbeat_interval

    @HEARTBEAT_INTERVAL.setter
    def HEARTBEAT_INTERVAL(self, v):
        self._heartbeat_interval = max(10, int(v))

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


    def set_summarizer(self, summarizer):
        """Store reference to summarizer for chat result routing."""
        self._summarizer = summarizer
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

        # Roster context
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
                        if "RESULT:" in result else result[:200])
        self._communicate("A→M", f"[{worker_name}] {result_short}")
        self._set_worker_free(worker_name)
        return result

    def _full_cycle(self, trigger_label: str, manager_prompt: str, context: str):
        decision = self._ceo_decide(trigger_label, manager_prompt)
        self.chat_reply.emit(trigger_label, decision)

        if decision.startswith("ERROR:"):
            self.log.emit(f"CEO LLM error: {decision}")
            return

        dtype, worker_name, content = self._parse_decision(decision)

        if dtype == "no_action":
            self.log.emit(f"CEO: No action — {content[:80]}")
            self._m_think(f"No action needed: {content}")
            return

        if dtype == "escalate":
            self.log.emit(f"CEO: Escalating — {content[:80]}")
            self._m_think(f"Escalated: {content}")
            return

        if dtype in ("action",):
            action = content
            self.log.emit(f"CEO → [{worker_name}] {action[:80]}")
            self._last_actions.append(f"[{worker_name}] {action}")
            if len(self._last_actions) > 20:
                self._last_actions.pop(0)

            if self.db:
                try:
                    self.db.log_decision(trigger_label, decision, action)
                    self.db.log_action(trigger_label, f"[{worker_name}] {action}")
                except Exception:
                    pass

            exec_result = self._execute_with_worker(worker_name, action, context)
            if self.db:
                try:
                    snippet = (exec_result.split("RESULT:")[-1].strip()[:300]
                               if "RESULT:" in exec_result else exec_result[:300])
                    self.db.log_action(f"{trigger_label}/{worker_name}", snippet)
                except Exception:
                    pass
        else:
            self.log.emit(f"CEO: unclear response: {decision[:100]}")
            self._m_think("CEO response lacked ACTION/NO_ACTION/ESCALATE.")

    # ── Chat handler ──────────────────────────────────────────────────────────

    def _handle_chat(self, human_text: str, research: dict):
        intent = _classify_intent(human_text)
        self._m_think(f"Chat intent: {intent}")
        context = self._build_context(research, human_text)

        if intent == "task":
            self._m_think("Routing as task — running full CEO cycle")
            prompt = (
                f"Human operator task: {human_text}\n\n"
                f"Plan which team member handles this and what they should do.\n\n"
                f"{context}"
            )
            self._full_cycle(f"Chat-task: {human_text[:40]}", prompt, context)

        elif intent == "command":
            lower = human_text.lower()
            if "pause" in lower:
                self.set_paused(True)
                self.chat_reply.emit("System", "⏸ Heartbeat paused.")
            elif "resume" in lower or "start" in lower:
                self.set_paused(False)
                self.chat_reply.emit("System", "▶ Heartbeat resumed.")
            elif "clear" in lower and "cache" in lower:
                self.invalidate_cache()
                if self.db:
                    try: self.db.clear_file_cache()
                    except Exception: pass
                self.chat_reply.emit("System", "🗑 Cache cleared.")
            elif "scan jobs" in lower or "search" in lower:
                self.task_queue.put("run job search cycle now")
                self.chat_reply.emit("System", "🔍 Job search queued.")
            elif "roster" in lower or "team" in lower:
                lines = ["**Team roster:**"]
                for name, info in self._roster.items():
                    status = "🔴 BUSY" if info.get("busy") else "🟢 Free"
                    task = f" — {info.get('current_task','')[:40]}" if info.get("busy") else ""
                    lines.append(f"  {name}: {status}{task}")
                self.chat_reply.emit("Roster", "\n".join(lines))
            else:
                self.chat_reply.emit(
                    "System", f"Unknown command: {human_text}")

        else:
            # Conversational question — CEO answers
            self._chat_history.append({"role": "user", "content": human_text})
            if len(self._chat_history) > 20:
                self._chat_history = self._chat_history[-20:]

            history_str = "\n".join(
                f"{m['role'].upper()}: {m['content']}"
                for m in self._chat_history[-6:]
            )
            chat_prompt = (
                f"Conversation:\n{history_str}\n\n"
                f"Context:\n{context[:5000]}"
            )
            self._m_think(f"CEO answering: {human_text[:80]}")
            answer = self._llm_call(self.CHAT_SYSTEM, chat_prompt, "chat")
            
            # Handle error or empty responses
            if not answer or not answer.strip():
                answer = "I'm having trouble reaching the language model right now. " \
                        "Please check your Ollama connection and try again."
            elif answer.startswith("ERROR:"):
                answer = "I'm still thinking about that. Could you rephrase or ask another question?"
            
            self._chat_history.append({"role": "assistant", "content": answer})
            self.chat_reply.emit("Answer", answer)
            self._m_think(f"CEO answer:\n{answer}")

    # ── Job queue processing ──────────────────────────────────────────────────

    def _process_job_queue(self):
        if not self._job_queue:
            return
        job = self._job_queue[0]
        free_w = self.get_free_worker(job.get("assigned_to", "Coder"))
        if not free_w:
            return
        self._m_think(
            f"Assigning gig to {free_w}: {job.get('title','')[:60]}")
        task = (
            f"Prepare a proposal for this gig:\n"
            f"Title: {job.get('title','')}\n"
            f"Budget: ${job.get('budget',0):.0f}\n"
            f"Description: {job.get('description','')[:300]}\n"
            f"Skills: {', '.join(job.get('skills',[]))}"
        )
        self._job_queue.pop(0)
        self._execute_with_worker(free_w, task, "")

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        self._sys("CEO ManagerThread started — autonomous heartbeat active")
        self.log.emit("CEO ManagerThread started — autonomous heartbeat active")

        startup_delay = int(os.getenv("STARTUP_DELAY_SECS", 5))
        if startup_delay > 0:
            self._m_think(f"Startup delay: {startup_delay}s")
            time.sleep(startup_delay)

        last_heartbeat = 0.0

        while self.running:
            now = time.time()

            # 1. Explicit queued tasks (highest priority)
            try:
                task = self.task_queue.get_nowait()
                self._m_think(f"Task received: {task}")
                research = self._get_research()
                context  = self._build_context(research, task)
                prompt   = (
                    f"Task assigned by operator: {task}\n\n"
                    f"Assign to the right team member and execute.\n\n"
                    f"{context}"
                )
                self._full_cycle(f"Task: {task[:40]}", prompt, context)
                self.agent_status.emit("Idle", "Ready")
                last_heartbeat = now
                time.sleep(2)
                continue
            except queue.Empty:
                pass

            # 2. Human chat (unaffected by pause)
            try:
                human_text = self.human_queue.get_nowait()
                self._m_think(f"Human message: {human_text}")
                research = self._get_research()
                self._handle_chat(human_text, research)
                self.agent_status.emit("Idle", "Ready")
                last_heartbeat = now
                time.sleep(2)
                continue
            except queue.Empty:
                pass

            # 3. Process job queue if jobs are waiting and workers are free
            if self._job_queue and not self.paused:
                self._process_job_queue()
                time.sleep(1)
                continue

            # 4. Autonomous heartbeat
            if not self.paused and (now - last_heartbeat) >= self._heartbeat_interval:
                focus = _FOCUS_AREAS[self._focus_index % len(_FOCUS_AREAS)]
                self._focus_index += 1
                self._m_think(f"Heartbeat — focus: {focus}")

                research = self._get_research(force=True)
                context  = self._build_context(research, focus)
                prompt   = (
                    f"Heartbeat focus: {focus}\n"
                    "Review the team and research files. "
                    "Identify the single most impactful action right now. "
                    "Assign it to the right team member.\n\n"
                    f"{context}"
                )
                self._full_cycle("Heartbeat", prompt, context)
                self.agent_status.emit("Idle", "Ready")
                last_heartbeat = now
                time.sleep(2)
                continue

            time.sleep(1)

    def stop(self):
        self.running = False