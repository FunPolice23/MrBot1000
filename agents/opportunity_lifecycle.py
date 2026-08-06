"""
agents/opportunity_lifecycle.py — Opportunity lifecycle automation

Automated state machine for tracking and progressing opportunities through:
  discovered → researched → queued → applied → in_progress → submitted → paid/failed

Includes:
  - Automated transitions based on scoring and availability
  - Scheduler for time-sensitive opportunities
  - Value/effort ranking for prioritization
  - Automatic queued→applied transitions
"""
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable
from pathlib import Path
import json
import os

# ── Job Status Constants ────────────────────────────────────────────────────
JOB_STATUSES = ("new", "evaluating", "queued", "assigned", "applied", 
                "in_progress", "submitted", "paid", "failed")

# ── Automatic Transition Thresholds ────────────────────────────────────────
AUTO_APPLY_THRESHOLD = float(os.getenv("AUTO_APPLY_THRESHOLD", "0.65"))
AUTO_SUBMIT_THRESHOLD = float(os.getenv("AUTO_SUBMIT_THRESHOLD", "0.70"))
MIN_PAYOUT_USD = float(os.getenv("MIN_PPLY_PAYOUT_USD", "50"))

# ── Timing Configuration ───────────────────────────────────────────────────
SCHEDULER_CHECK_INTERVAL = int(os.getenv("SCHEDULER_INTERVAL", "300"))  # 5 min default
OPPORTUNITY_DISCOVERY_INTERVAL = int(os.getenv("OPPORTUNITY_DISCOVERY_INTERVAL", "5"))  # Heartbeats between checks
OPPORTUNITY_EXPIRY_HOURS = int(os.getenv("OPPORTUNITY_EXPIRY_HOURS", "72"))  # 72 hrs default


@dataclass
class LifecycleEvent:
    stage: str
    timestamp: float
    note: str = ""
    amount: float = 0.0
    source: str = "system"
    transition_ok: bool = True
    context: Dict = field(default_factory=dict)


@dataclass
class OpportunityState:
    opportunity_id: str
    current_stage: str = "discovered"
    status: str = "active"
    history: List[LifecycleEvent] = field(default_factory=list)
    last_amount: float = 0.0
    last_transition_error: str = ""
    score: float = 0.0  # Value/effort score
    deadline: float = 0.0  # Unix timestamp if applicable
    budget: float = 0.0  # Expected payout
    team_available: bool = True  # Is team ready to work?


class OpportunityLifecycleTracker:
    """Automated lifecycle tracker with smart transitions and scheduling."""

    _ALLOWED_TRANSITIONS = {
            "discovered": {"researched", "queued", "failed"},
            "researched": {"queued", "failed"},
            "queued": {"applied", "rejected", "failed"},
            "applied": {"in_progress", "rejected", "failed"},
            "in_progress": {"submitted", "failed"},
            "submitted": {"paid", "failed"},
            "paid": set(),
            "failed": set(),
            "rejected": set(),
        }

    def __init__(self, callback: Callable = None):
        self._states: Dict[str, OpportunityState] = {}
        self._callback = callback  # Called on lifecycle events
        self._last_scheduler_check = 0.0
        self._auto_transition_enabled = os.getenv("AUTO_LIFECYCLE_TRANSITIONS", "true").lower() == "true"

    # ── State Access ──────────────────────────────────────────────────────────

    def start(self, opportunity: Any) -> OpportunityState:
        """Start tracking a new opportunity."""
        state = self._ensure(opportunity.id)
        state.budget = getattr(opportunity, 'estimated_usd_value', 0.0) or 0.0
        state.deadline = getattr(opportunity, 'deadline', 0.0) or 0.0
        state.score = getattr(opportunity, 'score', 0.5)
        # Also check for budget attribute
        if hasattr(opportunity, 'budget'):
            state.budget = opportunity.budget
        self._append(state, "discovered", "Opportunity discovered", source="system")
        self._notify("start", opportunity.id, state)
        return state

    def get_state(self, opportunity_id: str) -> Dict:
        """Get current state as dict for serialization."""
        state = self._ensure(opportunity_id)
        return {
            "opportunity_id": state.opportunity_id,
            "current_stage": state.current_stage,
            "status": state.status,
            "last_amount": state.last_amount,
            "score": state.score,
            "budget": state.budget,
            "deadline": state.deadline,
            "team_available": state.team_available,
            "history": [self._event_to_dict(e) for e in state.history],
        }

    def get_all_states(self) -> List[Dict]:
        """Get all opportunity states."""
        return [self.get_state(oid) for oid in self._states]

    def get_queued_opportunities(self) -> List[OpportunityState]:
        """Get opportunities ready for auto-application."""
        return [
            s for s in self._states.values()
            if s.current_stage == "queued" 
            and s.score >= AUTO_APPLY_THRESHOLD
            and s.budget >= MIN_PAYOUT_USD
            and s.team_available
        ]

    def get_active_opportunities(self) -> List[OpportunityState]:
        """Get opportunities that need attention."""
        return [
            s for s in self._states.values()
            if s.status == "active" and s.current_stage not in {"paid", "failed", "rejected"}
        ]

    # ── Automated Transitions ───────────────────────────────────────────────

    def promote_to_applied(self, opportunity_id: str, reason: str = "") -> OpportunityState:
        """Auto-transition from queued to applied."""
        state = self._ensure(opportunity_id)
        if state.current_stage != "queued":
            return state
        self._transition(state, "applied", reason or "Auto-promotion: score threshold met", source="scheduler")
        self._notify("applied", opportunity_id, state)
        return state

    def promote_to_submitted(self, opportunity_id: str, work_complete: bool = True) -> OpportunityState:
        """Auto-transition from applied/in_progress to submitted."""
        state = self._ensure(opportunity_id)
        if state.current_stage in ("applied", "in_progress"):
            self._transition(
                state, "submitted", 
                reason=f"Auto-promotion: work complete", 
                source="scheduler"
            )
            self._notify("submitted", opportunity_id, state)
        return state

    # ── Scheduler ───────────────────────────────────────────────────────────

    def scheduler_check(self, current_time: float = None) -> List[Dict]:
        """Run scheduler check - returns list of actions to take."""
        now = current_time or time.time()
        if now - self._last_scheduler_check < SCHEDULER_CHECK_INTERVAL:
            return []
        self._last_scheduler_check = now

        actions = []

        # 1. Auto-promote qualified queued opportunities
        queued = self.get_queued_opportunities()
        for state in queued:
            actions.append({
                "type": "promote_applied",
                "opportunity_id": state.opportunity_id,
                "reason": f"Auto-promotion: score={state.score:.2f} >= {AUTO_APPLY_THRESHOLD}, budget=${state.budget:.0f}"
            })

        # 2. Check for expired opportunities
        for oid, state in self._states.items():
            if state.deadline and state.deadline < now:
                if state.current_stage not in ("submitted", "paid", "failed", "rejected"):
                    actions.append({
                        "type": "expire",
                        "opportunity_id": oid,
                        "reason": "Opportunity past deadline"
                    })

        # 3. Check for stalled opportunities (in_progress > 24h)
        for oid, state in self._states.items():
            if state.current_stage == "in_progress":
                last_ts = state.history[-1].timestamp if state.history else 0
                if now - last_ts > 24 * 3600:  # 24 hours
                    actions.append({
                        "type": "follow_up",
                        "opportunity_id": oid,
                        "reason": "Stalled in_progress for 24h+"
                    })

        return actions

    def process_scheduled_actions(self, actions: List[Dict]) -> List[Dict]:
        """Execute scheduled actions and return results."""
        results = []
        for action in actions:
            if action["type"] == "promote_applied":
                state = self.promote_to_applied(action["opportunity_id"], action.get("reason", ""))
                results.append({"action": "applied", "id": action["opportunity_id"], "success": True})

            elif action["type"] == "expire":
                state = self._ensure(action["opportunity_id"])
                if state.current_stage not in ("submitted", "paid", "failed", "rejected"):
                    self._transition(state, "failed", "Opportunity expired", source="scheduler")
                    results.append({"action": "expire", "id": action["opportunity_id"], "success": True})

            elif action["type"] == "follow_up":
                # Log follow-up needed
                results.append({"action": "follow_up", "id": action["opportunity_id"], "success": True})

        return results

    # ── Ranking & Prioritization ────────────────────────────────────────────

    def rank_by_value_effort(self, limit: int = 10) -> List[tuple]:
        """Rank opportunities by value/effort ratio. Returns [(state, score), ...]."""
        scored = []
        for state in self._states.values():
            if state.status != "active":
                continue
            if state.current_stage in ("paid", "failed", "rejected"):
                continue

            # Calculate value/effort score
            # Higher is better
            value_score = state.score  # 0-1 fit score
            effort_factor = min(1.0, state.budget / 1000)  # Normalize to 0-1
            
            # Urgency bonus
            urgency_bonus = 0
            if state.deadline:
                hours_left = (state.deadline - time.time()) / 3600
                if hours_left < 24:
                    urgency_bonus = 0.3
                elif hours_left < 72:
                    urgency_bonus = 0.1

            total_score = (value_score * 0.6 + effort_factor * 0.3 + urgency_bonus * 0.1)
            scored.append((state, total_score))

        return sorted(scored, key=lambda x: x[1], reverse=True)[:limit]

    def get_top_opportunities(self, k: int = 3) -> List[OpportunityState]:
        """Get top K opportunities by value/effort ratio."""
        ranked = self.rank_by_value_effort(limit=k)
        return [state for state, score in ranked]

    # ── State Transitions ───────────────────────────────────────────────────

    def mark_researched(self, opportunity_id: str, note: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        self._transition(state, "researched", note or "Opportunity researched", source="user")
        return state

    def mark_queued(self, opportunity_id: str, score: float = 0.5, note: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        state.score = score
        state.team_available = True  # Mark as ready for auto-promotion
        self._transition(state, "queued", note or "Opportunity queued for action", source="user")
        return state

    def mark_applied(self, opportunity_id: str, note: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        state.team_available = False
        self._transition(state, "applied", note or "Application submitted", source="user")
        return state

    def mark_in_progress(self, opportunity_id: str, note: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        self._transition(state, "in_progress", note or "Work started", source="user")
        return state

    def mark_submitted(self, opportunity_id: str, note: str = "", amount: float = 0.0) -> OpportunityState:
        state = self._ensure(opportunity_id)
        state.last_amount = amount
        self._transition(state, "submitted", note or "Delivery submitted", amount=amount, source="user")
        return state

    def mark_paid(self, opportunity_id: str, amount: float = 0.0, note: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        state.last_amount = max(state.last_amount, amount)
        state.status = "paid"
        self._transition(state, "paid", note or "Payment received", amount=amount, source="user")
        return state

    def mark_failed(self, opportunity_id: str, note: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        state.status = "failed"
        self._transition(state, "failed", note or "Opportunity failed", source="user")
        return state

    def mark_rejected(self, opportunity_id: str, reason: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        state.status = "rejected"
        self._transition(state, "rejected", reason or "Opportunity rejected", source="user")
        return state

    # ── Internal Helpers ────────────────────────────────────────────────────

    def _ensure(self, opportunity_id: str) -> OpportunityState:
        if opportunity_id not in self._states:
            self._states[opportunity_id] = OpportunityState(opportunity_id=opportunity_id)
        return self._states[opportunity_id]

    def _transition(self, state: OpportunityState, stage: str, note: str, 
                    amount: float = 0.0, source: str = "system") -> None:
        if stage not in self._ALLOWED_TRANSITIONS.get(state.current_stage, set()):
            state.last_transition_error = f"Invalid transition from {state.current_stage} to {stage}"
            self._append(state, stage, note, amount=amount, source=source, transition_ok=False)
            return

        state.last_transition_error = ""
        self._append(state, stage, note, amount=amount, source=source, transition_ok=True)
        if source != "system":
            self._notify("transition", state.opportunity_id, state)

    def _append(self, state: OpportunityState, stage: str, note: str, 
                amount: float = 0.0, source: str = "system", transition_ok: bool = True) -> None:
        state.current_stage = stage
        state.history.append(LifecycleEvent(
            stage=stage, timestamp=time.time(), note=note, 
            amount=amount, source=source, transition_ok=transition_ok
        ))

    def _event_to_dict(self, event: LifecycleEvent) -> Dict:
        return {
            "stage": event.stage,
            "timestamp": event.timestamp,
            "note": event.note,
            "amount": event.amount,
            "source": event.source,
            "transition_ok": event.transition_ok
        }

    def _notify(self, event_type: str, opportunity_id: str, state: OpportunityState):
        """Notify callback if one was registered."""
        if self._callback:
            try:
                self._callback({
                    "type": event_type,
                    "opportunity_id": opportunity_id,
                    "stage": state.current_stage,
                    "score": state.score,
                    "budget": state.budget
                })
            except Exception:
                pass

    # ── Export Functions ────────────────────────────────────────────────────

    def export_queued_jobs(self, path: str = None) -> str:
        """Export queued jobs to JSON file for research folder integration."""
        queued = self.get_queued_opportunities()
        data = {
            "exported_at": time.time(),
            "count": len(queued),
            "opportunities": [
                {
                    "opportunity_id": s.opportunity_id,
                    "current_stage": s.current_stage,
                    "score": s.score,
                    "budget": s.budget,
                    "last_amount": s.last_amount,
                    "history_count": len(s.history)
                }
                for s in queued
            ]
        }
        if path:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
            return str(path)
        return json.dumps(data, indent=2)

    def export_analytics_report(self, path: str = None) -> str:
        """Export analytics summary to JSON file."""
        states = list(self._states.values())
        by_stage = {}
        by_status = {}
        total_value = 0.0
        
        for s in states:
            by_stage[s.current_stage] = by_stage.get(s.current_stage, 0) + 1
            by_status[s.status] = by_status.get(s.status, 0) + 1
            if s.last_amount:
                total_value += s.last_amount

        data = {
            "generated_at": time.time(),
            "summary": {
                "total_opportunities": len(states),
                "by_stage": by_stage,
                "by_status": by_status,
                "total_value_usd": round(total_value, 2),
                "active_opportunities": len(self.get_active_opportunities()),
                "queued_ready": len(self.get_queued_opportunities()),
            },
            "top_opportunities": [
                {k: getattr(s, k) for k in ['opportunity_id', 'current_stage', 'score', 'budget'] 
                 if hasattr(s, k)}
                for s, _ in self.rank_by_value_effort(limit=5)
            ]
        }
        
        if path:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
            return str(path)
        return json.dumps(data, indent=2)


# ── Module-level convenience functions ────────────────────────────────────

_lifecycle_tracker: Optional[OpportunityLifecycleTracker] = None

def get_lifecycle_tracker() -> OpportunityLifecycleTracker:
    """Get or create the global lifecycle tracker."""
    global _lifecycle_tracker
    if _lifecycle_tracker is None:
        _lifecycle_tracker = OpportunityLifecycleTracker()
    return _lifecycle_tracker


def initialize_lifecycle_export(research_folder: str = None):
    """Initialize export paths for research folder integration."""
    if research_folder:
        research_path = Path(research_folder)
        research_path.mkdir(parents=True, exist_ok=True)
        
        tracker = get_lifecycle_tracker()
        tracker.export_queued_jobs(str(research_path / "queued_jobs.json"))
        tracker.export_analytics_report(str(research_path / "analytics_report.json"))
        
        return str(research_path)