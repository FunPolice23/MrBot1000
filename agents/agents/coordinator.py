"""
agents/coordinator.py — Cross-model coordination hub

The Coordinator agent sits between the chat interface and main model workers,
enabling seamless collaboration where:
- Chat messages can query main model's state
- Main model decisions can notify chat model
- Tasks can be dynamically split between models
- Both models operate independently but share context
"""

import os
import time
import json
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

from agents.base_worker import WorkerAgent, ROOT_FOLDER
from agents.shared_context import get_shared_context
from library import AgentLogger, PromptBuilder, ResponseParser, fingerprint, ts_now


@dataclass
class CoordinationTask:
    """A task that requires both models to collaborate"""
    task_id: str
    description: str
    requested_by: str  # "human" or model name
    status: str  # "pending", "assigned_to_main", "assigned_to_chat", "in_progress", "completed"
    main_model_action: str = ""
    chat_model_action: str = ""
    shared_state_path: str = ""
    created_at: float = 0
    completed_at: float = 0


class CoordinatorWorker(WorkerAgent):
    """
    Specialized worker for cross-model coordination.
    Handles chat⇄main model communication and task distribution.
    """

    # System prompt for coordination tasks
    COORDINATOR_SYSTEM = (
        "You are the Coordinator in MrBot1000, managing cross-model communication "
        "between the fast chat model and accurate main model. "
        "Your job is to route tasks appropriately, coordinate responses, "
        "and maintain shared state between models. "
        "Be concise and action-oriented. Max 200 words."
    )

    def __init__(self, api_key: str, log_signal, db=None):
        super().__init__(api_key, log_signal, db=db)
        self._logger = AgentLogger(db=db, source="Coordinator", signal=log_signal)
        self._coordination_tasks: Dict[str, CoordinationTask] = {}
        self._shared_ctx = get_shared_context()

    # ── Coordination Hub ──────────────────────────────────────────────────────

    def coordinate_chat_to_main(self, chat_message: str) -> str:
        """
        When chat model needs main model's help, or vice versa.
        Returns coordinated response.
        """
        self._logger.info(f"Coordinating: {chat_message[:60]}")

        # Add message to shared context
        self._shared_ctx.add_event(
            event_type="chat_to_main",
            source="ChatModel",
            data={"message": chat_message, "timestamp": time.time()}
        )

        # Let main model handle it
        prompt = (PromptBuilder()
                  .context(f"Coordination request from chat model:\n{chat_message}")
                  .context(f"Shared context has recent decisions and tasks")
                  .instruction("Provide a coordinated response addressing the request.")
                  .build())

        response = self.llm(system=self.COORDINATOR_SYSTEM, user=prompt, chat=True)
        self._update_shared_state("last_coordinated_response", response)

        return response

    def route_task(self, task_description: str, requester: str = "human") -> CoordinationTask:
        """
        Dynamically route a task to the appropriate model(s).
        """
        task_id = f"coord_{int(time.time()*1000)}"
        task = CoordinationTask(
            task_id=task_id,
            description=task_description,
            requested_by=requester,
            status="pending"
        )

        # Determine which model(s) should handle this
        routing = self._analyze_task_routing(task_description)
        task.main_model_action = routing.get("main", "")
        task.chat_model_action = routing.get("chat", "")
        task.status = "assigned_to_main" if task.main_model_action else "pending"

        self._coordination_tasks[task_id] = task
        self._shared_ctx.add_signal(
            from_model="Coordinator",
            to_model="Main",
            message=f"task_routed_{task_id}",
            data=asdict(task)
        )

        self._logger.info(f"Task routed: {task_id} -> {task.status}")
        return task

    def _analyze_task_routing(self, task: str) -> dict:
        """Analyze which model should handle each part of a complex task."""
        # Use LLM to split complex tasks
        prompt = (PromptBuilder()
                  .context(f"Task: {task}")
                  .instruction("Split into main_model action and/or chat_model action. "
                              "Main model handles analysis/code/workflow. "
                              "Chat model handles conversation/state. "
                              "Return JSON: {'main': '...', 'chat': '...'}")
                  .build())

        response = self.llm(system=self.COORDINATOR_SYSTEM, user=prompt, chat=True)
        parser = ResponseParser(response)
        data = parser.json_object()

        return {
            "main": str(data.get("main", "")) if data else "",
            "chat": str(data.get("chat", "")) if data else ""
        }

    def sync_model_states(self) -> dict:
        """
        Get current state of both models from shared context.
        Used when coordination is needed.
        """
        states = {}
        for model in ["Main", "Chat", "JobSearch", "Coder", "Analyst", "Summarizer"]:
            ctx = self._shared_ctx.get_model_context(model)
            if ctx:
                states[model] = {
                    "current_task": ctx.current_task,
                    "last_decision": ctx.key_decisions[-1] if ctx.key_decisions else None,
                    "results": ctx.results
                }
        return states

    # ── Shared State Management ───────────────────────────────────────────────

    def _update_shared_state(self, key: str, value: str, model_name: str = "Coordinator"):
        """Update shared context with coordination info"""
        ctx = self._shared_ctx
        ctx.update_model_context(
            model_name,
            reasoning_chain=[f"Coordination: {key} = {value[:60]}"]
        )

    def get_pending_tasks(self, model_name: str = None) -> List[CoordinationTask]:
        """Get tasks that need attention"""
        pending = [t for t in self._coordination_tasks.values()
                   if t.status in ("pending", "assigned_to_main", "assigned_to_chat")]
        if model_name:
            pending = [t for t in pending if t.requested_by == model_name]
        return pending

    def complete_task(self, task_id: str, result: str = ""):
        """Mark a coordination task as complete"""
        if task_id in self._coordination_tasks:
            task = self._coordination_tasks[task_id]
            task.status = "completed"
            task.completed_at = time.time()
            self._update_shared_state(f"task_{task_id}", result)

            # Notify via shared signals
            self._shared_ctx.add_signal(
                from_model="Coordinator",
                to_model="all",
                message=f"task_completed_{task_id}",
                data={"result": result}
            )
            self._logger.info(f"Completed task: {task_id}")

    # ── Helper: Chat Interaction ────────────────────────────────────────────

    def process_human_chat(self, message: str) -> str:
        """
        Entry point for human chat messages.
        Decides whether to route to main model, handle locally, or coordinate.
        """
        # Quick check: does this need coordination?
        needs_coordination = self._detect_coordination_need(message)

        if needs_coordination:
            return self.coordinate_chat_to_main(message)

        # Otherwise, let main model handle directly
        return self.execute_task(message, "Coder")

    def _detect_coordination_need(self, message: str) -> bool:
        """Detect if message needs both models working together"""
        coordination_keywords = [
            "coordinate", "together", "both models", "chat and", "main model",
            "shared context", "cross-model", "together with", "combine"
        ]
        lower = message.lower()
        return any(kw in lower for kw in coordination_keywords)

    def execute_task(self, task: str, worker_name: str = "Coder") -> str:
        """Execute a task with the specified worker"""
        # Get worker from roster or use default Coder
        from manager import ManagerThread

        # This would be called via manager's roster
        self._logger.info(f"Executing: {task[:40]} via {worker_name}")

        # Update shared state
        self._update_shared_state(f"executing_{worker_name}", task)

        return f"Task sent to {worker_name}: {task[:50]}..."

    # ── Public Interface ─────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Return coordinator status for UI display"""
        return {
            "pending_tasks": len(self.get_pending_tasks()),
            "coordination_tasks": len(self._coordination_tasks),
            "last_action": self._last_action,
            "model_states": self.sync_model_states()
        }