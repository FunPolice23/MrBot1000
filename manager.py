"""
manager.py — CEO ManagerThread  (v4 — Opportunity Lifecycle Edition)

The Manager now acts as a CEO managing a team of specialized workers:
  • Maintains a worker roster (name → WorkerAgent subclass)
  • Routes tasks to the most appropriate worker based on specialty
  • Monitors the job search queue and assigns gigs to workers
  • Coordinates multi-worker tasks
  • Separate chat prompt that stays management-focused
  • New signals: worker_assigned, job_found, task_summary
  • Opportunity lifecycle integration with automated transitions
"""

import json
import queue
import re
import time
import os
import threading
from typing import Dict, List, Optional, Any
from pathlib import Path
from PySide6.QtCore import QThread, Signal

# ── Opportunity Lifecycle Configuration ──────────────────────────────────────
from agents.opportunity_lifecycle import (
    OpportunityLifecycleTracker,
    get_lifecycle_tracker,
    AUTO_APPLY_THRESHOLD,
    OPPORTUNITY_DISCOVERY_INTERVAL
)
# Project root (shared constant from base_worker) — used by the real execution engine
from agents.base_worker import ROOT_FOLDER
# Per-task deliverable workspaces (work/<platform>/<job_id>/)
from agents.task_workspace import TaskWorkspace, workspace_for

# ── Opportunity scheduling ────────────────────────────────────────────────────
OPPORTUNITY_DISCOVERY_INTERVAL = int(os.getenv("OPPORTUNITY_DISCOVERY_INTERVAL", "5"))  # Every 5 heartbeats

# ── Focus-to-Worker explicit mapping (rule-based fallback) ───────────────────
_FOCUS_WORKER_MAP = {
    "job search": "JobSearch",
    "proposal quality": "Analyst",
    "code quality": "Coder",
    "worker coordination": "Manager",
    "error handling": "Analyst",
    "agent speed": "Manager",
    "security": "Analyst",
    "revenue": "JobSearch",
}

# ── Action cooldown - minimum heartbeats between same action types ──────────
_ACTION_COOLDOWN = {
    "JobSearch": 3,
    "Analyst": 5,
    "Coder": 4,
    "Manager": 6,
}

# ─────────────────────────────────────────────────────────────────────────────
#  Focus areas rotate through per heartbeat
# ─────────────────────────────────────────────────────────────────────────────
_FOCUS_AREAS = [
    "job search — find high-value gigs on Fiverr, Upwork, and web search",
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
    "JobSearch":  ["job", "gig", "find work", "search", "fiverr", "upwork",
                   "freelance", "earn", "apply", "listing"],
    "Analyst":    ["analyse", "analyze", "metric", "report", "complexity",
                   "duplicate", "quality", "debt", "chart", "stats"],
    "Summarizer": ["summary", "explain", "simplify", "translate", "describe",
                   "what happened", "tldr", "recap"],
    "Coder":      ["code", "bug", "fix", "refactor", "implement", "write",
                   "function", "class", "module", "file", "python"],
}


def _classify_intent(text: str) -> str:
    lower = text.lower().strip()
    has_question = "?" in text
    scores = {intent: sum(1 for kw in kws if kw in lower)
              for intent, kws in _INTENT_KEYWORDS.items()}
    # A standalone command verb ("pause", "resume", "stop", "start", "reset",
    # "clear") is a command even without other keywords — but only when it is
    # the first word. A question that merely *mentions* a command word
    # (e.g. "should we pause?") must stay a question, so gate on first_word.
    _CMD_VERBS = ("pause", "resume", "stop", "start", "reset", "clear")
    first_word = lower.split()[0] if lower.split() else ""
    if first_word in _CMD_VERBS:
        return "command"
    if has_question:
        return "question"
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
    task_summary = Signal(str)           # Summary metrics for logging

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
        "  • JobSearch    — finds gigs on Fiverr, Upwork, and via web search\n"
        "  • Summarizer   — explains agent activity in plain language\n"
        "Core goals: 1) Win profitable gigs  2) Improve team code  "
        "3) Maximize USDC earnings  4) Self-upgrade workers.\n"
        "Given the FOCUS AREA, decide the single most impactful action.\n"
        "DISABLED platforms (never target): ClawGig, ClerkGig, uGig, Moltbook. "
        "Only use Fiverr, Upwork, or web search for job discovery.\n"
        "Respond in EXACTLY one of these formats:\n"
        "  ACTION[Coder]: <specific coding task>\n"
        "  ACTION[Analyst]: <analysis task>\n"
        "  ACTION[JobSearch]: <search/apply task>\n"
        "  ACTION[Manager]: <direct management task>\n"
        "  NO_ACTION: <brief reason>\n"
        "  ESCALATE: <reason needing human input>\n"
        "Keep response under 150 words. Reference filenames when possible."
    )

    WORKER_SYSTEM = (
        "You are an autonomous worker in the MrBot1000 AI agency. "
        "Your manager has given you a task. Execute it precisely:\n"
        "1. Identify exactly which file(s) need changing\n"
        "2. Describe the specific line-level changes\n"
        "3. Explain the expected improvement\n"
        "Always cite filenames. Keep response under 150 words.\n"
        "End with: RESULT: [what was done or found]"
    )

    CHAT_SYSTEM = (
        "You are the CEO of MrBot1000, an autonomous AI freelance agency, "
        "speaking directly to the human operator who is running this software.\n"
        "Answer the operator's MOST RECENT message using the conversation history. "
        "Address exactly what they asked. Be direct and honest. "
        "If you do not know something, say so plainly — do NOT invent status, "
        "files, workers, or plans. Do not greet them or give a generic status "
        "update unless they explicitly asked for one. Never repeat a prior answer "
        "unchanged. Keep it under 120 words and concis.\n"
        "The conversation history is provided so you can follow what they are "
        "referring to; use it to stay on topic."
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

        self._heartbeat_interval = int(os.getenv("HEARTBEAT_INTERVAL", 120))  # Default 120s
        self._research_cache_ttl = int(os.getenv("RESEARCH_CACHE_TTL", 120))
        self._focus_index    = 0
        self._reviewed_files = set()
        self._last_actions: List[str] = []
        self._last_llm_time  = 0.0
        self._min_llm_gap    = 2.0

        # Per-focus consecutive-failure tracking (2.0.20g). When a heartbeat focus
        # area repeatedly produces no usable result (e.g. JobSearch 0 gigs,
        # Analyst 0 proposals), the CEO is told to PIVOT instead of re-issuing the
        # identical task forever. Keyed by focus-area string.
        self._focus_failures: Dict[str, int] = {}

        self._last_research      = None
        self._last_research_time = 0.0

        self._chat_history: List[dict] = []

        # Task execution lock to prevent overlapping
        self._task_lock = threading.Lock()
        self._task_in_progress = False

        # ── Worker roster ──────────────────────────────────────────────────────
        # name → {"worker": WorkerAgent, "busy": bool, "current_task": str}
        self._roster: Dict[str, dict] = {
            "Coder": {"worker": worker, "busy": False, "current_task": ""}
        }
        self._job_queue: List[dict] = []   # queued gigs from JobSearchWorker

        # ── Opportunity lifecycle integration (D.1, D.2, D.3) ───────────────
        self._lifecycle: OpportunityLifecycleTracker = get_lifecycle_tracker()
        self._heartbeat_count: int = 0
        self._heartbeat_metrics = {
            "analysis": 0, "job_search": 0, "manager": 0, "coder": 0,
            "total_tasks": 0, "successful": 0, "errors": 0,
            "opportunities_discovered": 0, "opportunities_applied": 0,
            "opportunities_submitted": 0, "opportunities_paid": 0,
        }
        self._last_opportunity_check: float = 0.0

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
            self.worker_assigned.emit(name, task)
            self.agent_status.emit(f"{name}: Working", task)

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

    def _a_think(self, text: str):
        self.agent_thought.emit(text)
        self.thought.emit("Agent", text)

    def _communicate(self, direction: str, text: str):
        self.comms.emit(direction, text)
        self.thought.emit("Comms", f"[{direction}] {text}")

    def _sys(self, text: str):
        self.thought.emit("System", text)

    # ── Research helpers ──────────────────────────────────────────────────────

    def _get_research(self, force: bool = False) -> dict:
        now = time.time()
        if (force or self._last_research is None
                or (now - self._last_research_time) > self._research_cache_ttl):
            self._m_think("Scanning files for research context…")
            self._last_research      = self.worker.research_all()
            self._last_research_time = now
            r = self._last_research
        return self._last_research

    # ── LLM helpers ───────────────────────────────────────────────────────────

    def _llm_call(self, system: str, user: str, trigger: str, chat: bool = False) -> str:
        t0 = time.time()
        result = self.worker.llm(system=system, user=user, chat=chat)
        self._last_llm_time = time.time()
        return result

    def _history_suffix(self) -> str:
        if not self._last_actions:
            return ""
        lines = ["\nRecent actions (avoid repeating):"]
        for a in self._last_actions[-5:]:
            lines.append(f"  • {a}")
        return "\n".join(lines)

    # ── Decision + execution cycle ────────────────────────────────────────────

    def _ceo_decide(self, trigger_label: str, prompt: str, focus: str = "") -> str:
        full = prompt + self._history_suffix()
        self._m_think(f"Forming decision for: {trigger_label}")
        self.agent_status.emit("Thinking", trigger_label)
        decision = self._llm_call(self.CEO_SYSTEM, full, trigger_label)
        self._m_think(f"Decision: {decision}")
        return decision

    def _parse_decision(self, decision: str, focus: str = ""):
        lower = decision.lower()
        m = re.search(r"action\[(\w+)\]:\s*(.+)", decision, re.IGNORECASE)
        if m:
            return "action", m.group(1).strip(), m.group(2).strip()
        if "no_action" in lower:
            return "no_action", "", re.sub(r"no_action:?\s*", "", decision, flags=re.IGNORECASE).strip()
        if "escalate:" in lower:
            reason = decision.split("ESCALATE:")[-1].strip()
            return "escalate", "", reason
        if "action:" in lower:
            action = decision.split("ACTION:")[-1].strip()
            worker = _route_to_worker(action)
            return "action", worker, action
        # No explicit ACTION[Worker]: — let the heartbeat focus pick the worker
        # so e.g. "code quality" reliably routes to Coder (not left to LLM whim).
        focus_key = (focus or "").split("—")[0].strip().lower()
        focus_worker = _FOCUS_WORKER_MAP.get(focus_key)
        if focus_worker:
            return "action", focus_worker, f"Focus-area task for {focus_worker}: {focus}"
        return "unclear", "", decision

    # ── Real execution engine ─────────────────────────────────────────────────
    # Stages: THINK → PLAN → TOOL-CALL (real file/network ops) → CHECK → PROOFREAD
    # The worker's LLM is used for reasoning/planning/proofreading only — never as
    # a substitute for actually reading/writing/creating files or fetching gigs.

    PLANNER_SYSTEM = (
        "You are the planning module of MrBot1000. Given a manager directive and the "
        "REAL project file tree, extract a structured execution plan as STRICT JSON "
        "(no markdown fences). Schema:\n"
        "{\n"
        '  "file": "<relative path from the tree, or null if none>",\n'
        '  "operation": "fix" | "refactor" | "create" | "search" | "analyze" | "audit" | "other",\n'
        '  "platform": "fiverr" | "upwork" | "web" | null,\n'
        '  "issue": "<concise description of what to change/check>",\n'
        '  "rationale": "<one line>"\n'
        "}\n"
        "Rules: ONLY reference files that appear in the tree. If the directive names a "
        "file NOT in the tree, set 'file' to null and operation to 'other'. "
        "Return ONLY the JSON object."
    )

    def _plan_task(self, worker_name: str, action: str) -> dict:
        """THINK + PLAN: ask the chat model to structure the directive into a plan.

        Returns a dict; always includes 'operation' and 'issue'. Falls back to a
        heuristic parse if the LLM returns garbage.
        """
        from agents.base_worker import project_file_tree
        tree = project_file_tree()
        user = (
            f"Worker: {worker_name}\n"
            f"Directive: {action}\n\n"
            f"REAL PROJECT FILE TREE (only these files exist):\n{tree}\n\n"
            "Return the JSON plan now."
        )
        try:
            raw = self.worker.llm(system=self.PLANNER_SYSTEM, user=user, chat=True,
                                  max_tokens=600)
        except Exception:
            raw = ""
        plan = self._extract_json_plan(raw)
        if plan is None:
            # Heuristic fallback
            plan = {
                "file": None,
                "operation": "other",
                "platform": None,
                "issue": action[:200],
                "rationale": "heuristic fallback (LLM planner failed)",
            }
        return plan

    @staticmethod
    def _extract_json_plan(raw: str) -> dict | None:
        if not raw:
            return None
        # Strip markdown fences if present
        s = raw.strip()
        if s.startswith("```"):
            s = s.split("```", 2)[1]
            if s and s[0] in "json\n":
                s = s[4:] if s.startswith("json") else s
        try:
            obj = json.loads(s)
            if isinstance(obj, dict) and "operation" in obj:
                obj.setdefault("file", None)
                obj.setdefault("platform", None)
                obj.setdefault("issue", "")
                return obj
        except Exception:
            pass
        # Try to salvage a JSON object substring
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict) and "operation" in obj:
                    obj.setdefault("file", None)
                    obj.setdefault("platform", None)
                    obj.setdefault("issue", "")
                    return obj
            except Exception:
                pass
        return None

    def _execute_with_worker(self, worker_name: str, action: str, context: str) -> str:
        info = self._roster.get(worker_name, self._roster.get("Coder"))
        w = info["worker"] if isinstance(info, dict) else self.worker

        self._set_worker_busy(worker_name, action)
        self._communicate("M→A", f"[To {worker_name}] Execute: {action}")
        self._a_think(f"[{worker_name}] Received task: {action}")

        # ── STAGE 1+2: THINK + PLAN ───────────────────────────────────────────
        self._a_think(f"[{worker_name}] PLANNING: structuring directive…")
        plan = self._plan_task(worker_name, action)
        self._a_think(f"[{worker_name}] PLAN: {json.dumps(plan)[:300]}")
        operation = (plan.get("operation") or "other").lower()
        target_file = plan.get("file")
        issue = plan.get("issue") or action
        platform = (plan.get("platform") or "").lower()

        # ── STAGE 3: TOOL-CALL — real ability dispatch ────────────────────────
        evidence = ""           # concrete proof of what actually happened
        ok = False
        try:
            if worker_name == "JobSearch" or operation == "search":
                # Real gig discovery (Fiverr/Upwork/web) via the worker's clients
                plat = platform or "fiverr"
                if plat not in ("fiverr", "upwork", "web"):
                    plat = "fiverr"
                self._a_think(f"[JobSearch] TOOL-CALL: search('{plat}') — real client")
                gigs = w.search(plat, skill_tags=["ai agent", "automation", "python"])
                if gigs:
                    evidence = (f"FOUND {len(gigs)} real gig(s) on {plat}. "
                                f"Top: " + "; ".join(
                                    f"{g.title} (${g.budget})" for g in gigs[:3]))
                    ok = True
                else:
                    evidence = f"search('{plat}') returned 0 gigs (no matches / client error)"
                    ok = True  # executed successfully, just no results

            elif worker_name == "Analyst" or operation in ("analyze", "audit"):
                # Real metrics / evaluation
                self._a_think(f"[Analyst] TOOL-CALL: generate_metrics_report() — real analysis")
                report = w.generate_metrics_report()
                if isinstance(report, dict):
                    evidence = (f"REPORT generated: "
                                f"proposals={report.get('total_proposals', 0)}, "
                                f"avg_quality={report.get('average_quality', 0)}, "
                                f"submissions={report.get('submissions_recommended', 0)}")
                    ok = True
                else:
                    evidence = f"analysis returned: {str(report)[:200]}"

            elif worker_name == "Coder" or operation in ("fix", "refactor", "create", "audit"):
                # Real file read → LLM fix → safe_write_file
                if not target_file:
                    evidence = ("No valid file in directive; cannot execute a file op "
                                "safely. Skipped (no hallucinated edits).")
                    ok = False
                else:
                    full = os.path.join(ROOT_FOLDER, target_file)
                    if not os.path.exists(full):
                        evidence = f"FILE NOT FOUND: {target_file} — cannot edit a non-existent file. Skipped."
                        ok = False
                    else:
                        before = open(full, encoding="utf-8", errors="ignore").read()
                        self._a_think(f"[Coder] TOOL-CALL: analyze_and_fix('{target_file}') — real read+write")
                        res = w.analyze_and_fix(full, issue)
                        if isinstance(res, dict) and res.get("success"):
                            after = open(full, encoding="utf-8", errors="ignore").read()
                            # ── STAGE 4: CHECK WORK — verify the file actually changed ──
                            changed = before != after
                            lines = after.count("\n") + 1
                            evidence = (f"EDITED {target_file}: success=True, "
                                        f"changed={changed}, lines={lines}, "
                                        f"notes={res.get('notes','')}")
                            ok = changed or res.get("success")
                            # ── STAGE 5: PROOFREAD ──
                            if changed:
                                self._a_think(f"[Coder] PROOFREAD: verifying diff for {target_file}")
                                proof = self._proofread_change(target_file, res.get("changes", []))
                                evidence += f" | PROOFREAD: {proof}"
                        else:
                            evidence = (f"EDIT of {target_file} FAILED: "
                                        f"{res.get('notes','unknown error') if isinstance(res,dict) else res}")
                            ok = False
            else:
                # Unknown operation — do NOT pretend. Report honestly.
                if operation in ("fulfill", "complete_job", "deliver"):
                    evidence = self._fulfill_job(plan, action, worker_name)
                    ok = "FAILED" not in evidence and "Skipped" not in evidence
                else:
                    evidence = (f"Operation '{operation}' for {worker_name} has no real "
                                f"tool implementation; no simulated result returned.")
                    ok = False

        except Exception as e:
            evidence = f"EXECUTION ERROR ({type(e).__name__}): {e}"
            ok = False

        # ── Emit the REAL result (verified evidence), not LLM self-narrative ──
        status = "DONE" if ok else "NO-OP/FAILED"
        result_text = f"[{status}] {worker_name}: {evidence}"
        self._a_think(f"[{worker_name}] RESULT: {evidence}")
        self._communicate("A→M", f"[{worker_name}] {evidence}")
        self._set_worker_free(worker_name)
        # Persist the action so the DB Stats "Recent Actions" tab can show it
        # (2.0.20e). Guarded: logging must never break the result emission.
        try:
            if self.db is not None:
                self.db.log_action(trigger=action, action_text=result_text)
        except Exception as _log_err:
            self.log.emit(f"Action stats log skipped: {_log_err}")
        return result_text

    def _fulfill_job(self, plan: dict, action: str, worker_name: str) -> str:
        """Create work/<platform>/<job_id>/ and complete the gig's deliverable.

        Reads a job spec from the plan/action: platform, job_id, requirements,
        and deliverable filename + content. Saves into the workspace, verifies
        against requirements via document_scanner, archives on pass.
        """
        platform = (plan.get("platform") or "").lower() or "unknown"
        job_id   = str(plan.get("job_id") or plan.get("id") or "").strip() or "unknown"
        reqs     = plan.get("requirements") or None
        deliverable_name = plan.get("deliverable") or plan.get("file") or f"{platform}_deliverable.md"
        content   = plan.get("content") or plan.get("deliverable_content") or action

        try:
            ws = workspace_for({"platform": platform, "job_id": job_id},
                               root_folder=ROOT_FOLDER, log_signal=self.log)
            self._a_think(f"[Fulfill] created workspace {ws.path}")
            saved = ws.save(deliverable_name, content)
            if not saved:
                return "FULFILL FAILED: could not write deliverable to workspace"
            res = ws.complete(requirements=reqs,
                              job={"platform": platform, "job_id": job_id,
                                   "skills": reqs or []})
            if res.get("completed"):
                sub = ws.submit()
                return (f"FULFILLED {platform}:{job_id} — deliverable saved, "
                        f"requirements met (q={res['quality_score']:.2f}), "
                        f"submitted (archive: {sub['archive']})")
            return (f"FULFILL incomplete {platform}:{job_id} — requirements not met: "
                    f"{res.get('missing_items')} {res.get('issues')}. "
                    f"Deliverable saved; retry with better content.")
        except Exception as e:
            return f"FULFILL FAILED: {type(e).__name__}: {e}"

    def _proofread_change(self, file_path: str, diff) -> str:
        """PROOFREAD: ask the chat model to sanity-check the applied diff."""
        try:
            diff_txt = "\n".join(diff) if isinstance(diff, (list, tuple)) else str(diff)
            if not diff_txt.strip():
                return "no diff produced"
            user = (f"Review this unified diff for {file_path}. Confirm it is valid, "
                    f"safe, and addresses the issue. Reply in ONE short sentence.\n\n"
                    f"{diff_txt[:2500]}")
            out = self.worker.llm(system=self.WORKER_SYSTEM, user=user, chat=True,
                                   max_tokens=200)
            return (out or "proofread skipped").strip().split("\n")[0][:200]
        except Exception as e:
            return f"proofread error: {e}"


    def _record_focus_outcome(self, focus: str, result_text: str):
        """Track consecutive failures per focus area so the heartbeat can ADAPT
        (skip a dead path) instead of re-issuing the same doomed task forever."""
        if not focus:
            return
        _neg = ("[NO-OP/FAILED]", "no matches", "0 gigs", "returned 0",
                "no real tool", "REFUSED", "Skipped", "FAILED", "no_metrics_available")
        failed = any(sig in (result_text or "") for sig in _neg)
        if failed:
            self._focus_failures[focus] = self._focus_failures.get(focus, 0) + 1
        else:
            # Any non-failed outcome resets the streak for this focus.
            self._focus_failures[focus] = 0

    def _full_cycle(self, trigger_label: str, manager_prompt: str, context: str, focus: str = ""):
        decision = self._ceo_decide(trigger_label, manager_prompt, focus)
        self.chat_reply.emit(trigger_label, decision)

        if decision.startswith("ERROR:"):
            self.log.emit(f"CEO LLM error: {decision}")
            return

        dtype, worker_name, content = self._parse_decision(decision, focus)

        if dtype == "no_action":
            self.log.emit(f"CEO: No action — {content[:80]}")
            return

        if dtype == "escalate":
            self.log.emit(f"CEO: Escalating — {content[:80]}")
            return

        if dtype in ("action",):
            action = content
            self.log.emit(f"CEO → [{worker_name}] {action[:80]}")
            self._last_actions.append(f"[{worker_name}] {action}")
            if len(self._last_actions) > 5:
                self._last_actions.pop(0)

            self._heartbeat_metrics["successful"] += 1
            result_text = self._execute_with_worker(worker_name, action, context)
            # Track per-focus consecutive failures so the CEO can pivot instead
            # of re-issuing a task that yields nothing (2.0.20g).
            self._record_focus_outcome(focus, result_text)
            return result_text

    def _record_focus_outcome(self, focus: str, result_text: str):
        """Update consecutive-failure counter for a heartbeat focus area.

        A 'failure' = the worker executed but produced no usable result
        (0 gigs found, 0 proposals analyzed, a refused/NO-OP/FAILED op). This
        is used by the heartbeat to tell the CEO to change strategy.
        """
        if not focus:
            return
        rt = (result_text or "").lower()
        failed = (
            "0 gigs" in rt or "no matches" in rt or "no_metrics" in rt
            or "proposals=0" in rt or "no proposals" in rt
            or "no-op/failed" in rt or "failed" in rt and "success" not in rt
            or "refused" in rt or "skipped" in rt or "not found" in rt
        )
        if failed:
            self._focus_failures[focus] = self._focus_failures.get(focus, 0) + 1
            if self._focus_failures[focus] >= 2:
                self._m_think(
                    f"ADAPT: focus '{focus}' produced no results "
                    f"{self._focus_failures[focus]}x — CEO should PIVOT to a "
                    f"different action, not repeat the same task.")
        else:
            self._focus_failures[focus] = 0

    # ── Chat handler ──────────────────────────────────────────────────────────

    def _handle_chat(self, human_text: str, research: dict, focus: str = ""):
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
            self._full_cycle(f"Chat-task: {human_text[:40]}", prompt, context, focus)

        elif intent == "command":
            lower = human_text.lower()
            if "pause" in lower:
                self.set_paused(True)
                self.chat_reply.emit("System", "⏸ Heartbeat paused.")
            elif "resume" in lower or "start" in lower:
                self.set_paused(False)
                self.chat_reply.emit("System", "▶ Heartbeat resumed.")
            elif "roster" in lower or "team" in lower:
                lines = ["**Team roster:**"]
                for name, info in self._roster.items():
                    status = "🔴 BUSY" if info.get("busy") else "🟢 Free"
                    task = f" — {info.get('current_task','')[:40]}" if info.get("busy") else ""
                    lines.append(f"  {name}: {status}{task}")
                self.chat_reply.emit("Roster", "\n".join(lines))

        else:
            self._chat_history.append({"role": "user", "content": human_text})
            if len(self._chat_history) > 20:
                self._chat_history = self._chat_history[-20:]

            history_str = "\n".join(
                f"{m['role'].upper()}: {m['content']}"
                for m in self._chat_history[-6:]
            )
            chat_prompt = (
                f"Conversation:\n{history_str}\n\n"
                f"Context:\n{context[:3000]}"
            )
            self._m_think(f"CEO answering: {human_text[:80]}")
            answer = self._llm_call(self.CHAT_SYSTEM, chat_prompt, "chat", chat=True)
            self._chat_history.append({"role": "assistant", "content": answer})
            self.chat_reply.emit("Answer", answer)



    # ── Summarizer connection ──────────────────────────────────────────────────

    def set_summarizer(self, summarizer):
        """Connect summarizer to manager for chat routing."""
        self._summarizer = summarizer

    def _on_summarizer_chat_reply(self, label: str, text: str):
        """Route summarizer chat replies through manager."""
        self.chat_reply.emit(label, text)

    # ── Research folder property ────────────────────────────────────────────

    @property
    def research_folder(self) -> Optional[str]:
        """Get the research folder path."""
        return self.worker.research_folder

    @research_folder.setter
    def research_folder(self, path: str):
        """Set the research folder for file scanning."""
        self.worker.research_folder = path

    # ── Job queue processing ──────────────────────────────────────────────────

    def _process_job_queue(self):
        if not self._job_queue:
            return
        job = self._job_queue[0]
        free_w = self.get_free_worker(job.get("assigned_to", "Coder"))
        if not free_w:
            return
        self._m_think(f"Assigning gig to {free_w}: {job.get('title','')[:60]}")
        task = (
            f"Prepare a proposal for this gig:\n"
            f"Title: {job.get('title','')}\n"
            f"Budget: ${job.get('budget',0):.0f}\n"
            f"Description: {job.get('description','')}\n"
            f"Skills: {', '.join(job.get('skills',[]))}"
        )
        self._job_queue.pop(0)
        self._execute_with_worker(free_w, task, "")

    # ── Opportunity Lifecycle Integration ────────────────────────────────────

    def _process_opportunities(self) -> List[Dict]:
        """Check for opportunities ready for automatic promotion (D.1, D.3)."""
        now = time.time()
        if self._heartbeat_count > 0 and self._heartbeat_count % OPPORTUNITY_DISCOVERY_INTERVAL == 0:
            actions = self._lifecycle.scheduler_check(now)
            results = self._lifecycle.process_scheduled_actions(actions)
            for r in results:
                self.log.emit(f"Lifecycle: {r['action']} - {r.get('opportunity_id', 'all')}")
            return results
        return []

    def _update_opportunity_metrics(self) -> None:
        """Update heartbeat metrics from lifecycle tracker (D.4, E.2)."""
        states = self._lifecycle.get_all_states()
        for state in states:
            stage = state['current_stage']
            if stage == 'queued':
                self._heartbeat_metrics['total_tasks'] += 1
            elif stage == 'applied':
                self._heartbeat_metrics['opportunities_applied'] += 1
            elif stage == 'submitted':
                self._heartbeat_metrics['opportunities_submitted'] += 1
            elif stage == 'paid':
                self._heartbeat_metrics['opportunities_paid'] += 1
                self._heartbeat_metrics['successful'] += 1
            elif stage == 'failed':
                self._heartbeat_metrics['errors'] += 1

    def get_top_opportunities(self, k: int = 3) -> List[Dict]:
        """Get top K opportunities by value/effort ratio (D.4)."""
        ranked = self._lifecycle.rank_by_value_effort(limit=k)
        return [{"id": s.opportunity_id, "score": s.score, "budget": s.budget,
                 "stage": s.current_stage} for s, _ in ranked]

    def _log_heartbeat_summary(self):
        """Log periodic summary metrics (E.1)."""
        metrics = self._heartbeat_metrics
        summary = (f"Heartbeat #{self._heartbeat_count} summary: "
                   f"{metrics['analysis']} analysis, {metrics['job_search']} job search, "
                   f"{metrics['coder']} coder, {metrics['manager']} manager tasks, "
                   f"queued={metrics['total_tasks']}, successful={metrics['successful']}, "
                   f"errors={metrics['errors']}")
        self.task_summary.emit(summary)

    def export_queued_jobs(self, path: str = None) -> str:
        """Export queued jobs to JSON (B.4)."""
        return self._lifecycle.export_queued_jobs(path)

    def export_analytics_report(self, path: str = None) -> str:
        """Export analytics report to JSON (B.4)."""
        return self._lifecycle.export_analytics_report(path)

    # ── Build context helper ──────────────────────────────────────────────────

    def _build_context(self, research: dict, task_hint: str = "") -> str:
        DIRECT_LIMIT = 40000
        root_text = research.get("root", "")
        research_text = research.get("research", "")
        research_path = research.get("research_path") or "not set"
        root_part = root_text[:4000]

        roster_lines = []
        for name, info in self._roster.items():
            status = "BUSY" if info["busy"] else "free"
            task = f" -> {info['current_task'][:40]}" if info["busy"] else ""
            roster_lines.append(f"  {name}: {status}{task}")
        roster_str = "\n".join(roster_lines)

        job_queue_str = ""
        if self._job_queue:
            job_queue_str = (
                "\n=== QUEUED GIGS ===" +
                "\n".join(
                    f"  [{j.get('platform','')}] {j.get('title','')[:60]} "
                    f"${j.get('budget',0):.0f} score={j.get('score',0):.2f}"
                    for j in self._job_queue[:5]
                )
            )

        return "\n".join([
            "=== TEAM ROSTER ===",
            roster_str, "",
            "=== AGENT SOURCE FILES (root) ===",
            root_part or "(none)", "",
            f"=== RESEARCH FOLDER: {research_path} ===",
            research_text,
            job_queue_str,
        ])

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
                context = self._build_context(research, task)
                prompt = (
                    f"Task assigned by operator: {task}\n\n"
                    f"Assign to the right team member and execute.\n\n"
                    f"{context}"
                )
                self._full_cycle(f"Task: {task[:40]}", prompt, context)
                self.agent_status.emit("Idle", "Ready")
                last_heartbeat = now
                time.sleep(1)
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
                time.sleep(1)
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
                self._heartbeat_count += 1
                # ADAPT (2.0.20g): skip focus areas that have failed repeatedly so
                # the CEO stops re-issuing a doomed task (e.g. job search -> 0 gigs
                # forever). Advance past any focus with >=3 consecutive failures.
                focus = _FOCUS_AREAS[self._focus_index % len(_FOCUS_AREAS)]
                _skipped = 0
                while (self._focus_failures.get(focus, 0) >= 3
                       and _skipped < len(_FOCUS_AREAS)):
                    self._m_think(
                        f"ADAPT: focus '{focus[:40]}' failed "
                        f"{self._focus_failures.get(focus,0)}x consecutively — "
                        f"skipping to avoid looping on a dead path")
                    self._focus_index += 1
                    focus = _FOCUS_AREAS[self._focus_index % len(_FOCUS_AREAS)]
                    _skipped += 1
                self._focus_index += 1

                self._m_think(f"Heartbeat #{self._heartbeat_count} — focus: {focus}")

                research = self._get_research(force=True)
                context = self._build_context(research, focus)
                prompt = (
                    f"HEARTBEAT FOCUS AREA: {focus}\n\n"
                    f"Review the team and research files. "
                    f"Identify the single most impactful action for THIS FOCUS AREA. "
                    f"Assign it to the right team member.\n\n"
                )
                # Adaptation signal (2.0.20g): if this focus has repeatedly
                # produced no usable result, force the CEO to PIVOT rather than
                # re-issue the identical task.
                fails = self._focus_failures.get(focus, 0)
                if fails >= 2:
                    prompt += (
                        f"ADAPTION NOTICE: This focus area has produced NO usable "
                        f"result {fails} times in a row (e.g. 0 gigs found / 0 "
                        f"proposals analyzed / operations refused). DO NOT repeat the "
                        f"same action. Choose a DIFFERENT, concrete action this cycle "
                        f"-- or, if the focus is genuinely unworkable in this "
                        f"environment, pick a different focus area to make progress.\n\n"
                    )
                prompt += f"{context}"
                self._full_cycle(f"Heartbeat: {focus[:30]}", prompt, context, focus)
                self.agent_status.emit("Idle", "Ready")
                last_heartbeat = now

                # Process opportunity lifecycle
                self._process_opportunities()
                self._update_opportunity_metrics()

                # Log summary every 5 heartbeats
                if self._heartbeat_count % 5 == 0:
                    self._log_heartbeat_summary()

                time.sleep(1)
                continue

            time.sleep(0.5)

    def stop(self):
        self.running = False
