import time
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class LifecycleEvent:
    stage: str
    timestamp: float
    note: str = ""
    amount: float = 0.0


@dataclass
class OpportunityState:
    opportunity_id: str
    current_stage: str = "discovered"
    status: str = "active"
    history: List[LifecycleEvent] = field(default_factory=list)
    last_amount: float = 0.0


class OpportunityLifecycleTracker:
    """Track an opportunity through discovery, research, application, submission, and payout."""

    def __init__(self):
        self._states: Dict[str, OpportunityState] = {}

    def start(self, opportunity: Any) -> OpportunityState:
        state = self._states.get(opportunity.id)
        if state is None:
            state = OpportunityState(opportunity_id=opportunity.id)
            self._states[opportunity.id] = state
        self._append(state, "discovered", "Opportunity discovered")
        return state

    def mark_researched(self, opportunity_id: str, note: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        self._append(state, "researched", note or "Opportunity researched")
        return state

    def mark_applied(self, opportunity_id: str, note: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        self._append(state, "applied", note or "Application sent")
        return state

    def mark_in_progress(self, opportunity_id: str, note: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        self._append(state, "in_progress", note or "Work started")
        return state

    def mark_submitted(self, opportunity_id: str, note: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        self._append(state, "submitted", note or "Submission delivered")
        return state

    def mark_paid(self, opportunity_id: str, amount: float = 0.0, note: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        state.last_amount = amount
        self._append(state, "paid", note or "Payment received", amount=amount)
        state.status = "paid"
        return state

    def mark_failed(self, opportunity_id: str, note: str = "") -> OpportunityState:
        state = self._ensure(opportunity_id)
        self._append(state, "failed", note or "Opportunity failed")
        state.status = "failed"
        return state

    def get_state(self, opportunity_id: str) -> Dict:
        state = self._ensure(opportunity_id)
        return {
            "opportunity_id": state.opportunity_id,
            "current_stage": state.current_stage,
            "status": state.status,
            "last_amount": state.last_amount,
            "history": [
                {"stage": event.stage, "timestamp": event.timestamp, "note": event.note, "amount": event.amount}
                for event in state.history
            ],
        }

    def _ensure(self, opportunity_id: str) -> OpportunityState:
        if opportunity_id not in self._states:
            self._states[opportunity_id] = OpportunityState(opportunity_id=opportunity_id)
        return self._states[opportunity_id]

    def _append(self, state: OpportunityState, stage: str, note: str, amount: float = 0.0) -> None:
        state.current_stage = stage
        state.history.append(LifecycleEvent(stage=stage, timestamp=time.time(), note=note, amount=amount))
