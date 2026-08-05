import time
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class LifecycleEvent:
    stage: str
    timestamp: float
    note: str = ""
    amount: float = 0.0
    source: str = "system"
    transition_ok: bool = True


@dataclass
class OpportunityState:
    opportunity_id: str
    current_stage: str = "discovered"
    status: str = "active"
    history: List[LifecycleEvent] = field(default_factory=list)
    last_amount: float = 0.0
    last_transition_error: str = ""


class OpportunityLifecycleTracker:
    """Track an opportunity through explicit, auditable lifecycle stages."""

    _ALLOWED_TRANSITIONS = {
        "discovered": {"researched", "failed"},
        "researched": {"applied", "failed"},
        "applied": {"in_progress", "failed"},
        "in_progress": {"submitted", "failed"},
        "submitted": {"paid", "failed"},
        "paid": set(),
        "failed": set(),
    }

    def __init__(self):
        self._states: Dict[str, OpportunityState] = {}

    def start(self, opportunity: Any) -> OpportunityState:
        state = self._states.get(opportunity.id)
        if state is None:
            state = OpportunityState(opportunity_id=opportunity.id)
            self._states[opportunity.id] = state
        self._append(state, "discovered", "Opportunity discovered", source="system")
        return state

    def mark_researched(self, opportunity_id: str, note: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        self._transition(state, "researched", note or "Opportunity researched", source="system")
        return state

    def mark_applied(self, opportunity_id: str, note: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        self._transition(state, "applied", note or "Application sent", source="system")
        return state

    def mark_in_progress(self, opportunity_id: str, note: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        self._transition(state, "in_progress", note or "Work started", source="system")
        return state

    def mark_submitted(self, opportunity_id: str, note: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        self._transition(state, "submitted", note or "Submission delivered", source="system")
        return state

    def mark_paid(self, opportunity_id: str, amount: float = 0.0, note: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        state.last_amount = amount
        self._transition(state, "paid", note or "Payment received", amount=amount, source="system")
        state.status = "paid"
        return state

    def mark_failed(self, opportunity_id: str, note: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        self._transition(state, "failed", note or "Opportunity failed", source="system")
        state.status = "failed"
        return state

    def get_state(self, opportunity_id: str) -> Dict:
        state = self._ensure(opportunity_id)
        return {
            "opportunity_id": state.opportunity_id,
            "current_stage": state.current_stage,
            "status": state.status,
            "last_amount": state.last_amount,
            "last_transition_error": state.last_transition_error,
            "history": [
                {
                    "stage": event.stage,
                    "timestamp": event.timestamp,
                    "note": event.note,
                    "amount": event.amount,
                    "source": event.source,
                    "transition_ok": event.transition_ok,
                }
                for event in state.history
            ],
        }

    def _ensure(self, opportunity_id: str) -> OpportunityState:
        if opportunity_id not in self._states:
            self._states[opportunity_id] = OpportunityState(opportunity_id=opportunity_id)
        return self._states[opportunity_id]

    def _transition(self, state: OpportunityState, stage: str, note: str, amount: float = 0.0, source: str = "system") -> None:
        if stage not in self._ALLOWED_TRANSITIONS.get(state.current_stage, set()):
            state.last_transition_error = f"Invalid transition from {state.current_stage} to {stage}"
            state.history.append(LifecycleEvent(stage=stage, timestamp=time.time(), note=note, amount=amount, source=source, transition_ok=False))
            return

        state.last_transition_error = ""
        self._append(state, stage, note, amount=amount, source=source)

    def _append(self, state: OpportunityState, stage: str, note: str, amount: float = 0.0, source: str = "system") -> None:
        state.current_stage = stage
        state.history.append(LifecycleEvent(stage=stage, timestamp=time.time(), note=note, amount=amount, source=source, transition_ok=True))
