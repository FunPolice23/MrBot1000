import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from earning_pipeline import Opportunity
from agents.opportunity_lifecycle import OpportunityLifecycleTracker


class TestOpportunityStateMachine(unittest.TestCase):
    def test_invalid_transition_is_recorded(self):
        tracker = OpportunityLifecycleTracker()
        opportunity = Opportunity(id="opp_state_1", source="upwork", platform="Upwork")
        tracker.start(opportunity)

        tracker.mark_failed(opportunity.id, note="stopped")
        tracker.mark_paid(opportunity.id, amount=25.0, note="after fail")

        state = tracker.get_state(opportunity.id)
        last_event = state["history"][-1]
        self.assertFalse(last_event["transition_ok"])
        self.assertIn("Invalid transition", state["last_transition_error"])
        self.assertEqual(state["current_stage"], "failed")

    def test_valid_transition_updates_stage(self):
        tracker = OpportunityLifecycleTracker()
        opportunity = Opportunity(id="opp_state_2", source="fiverr", platform="Fiverr")
        tracker.start(opportunity)
        tracker.mark_researched(opportunity.id)
        tracker.mark_applied(opportunity.id)
        tracker.mark_in_progress(opportunity.id)
        tracker.mark_submitted(opportunity.id)
        tracker.mark_paid(opportunity.id, amount=120.0, note="paid")

        state = tracker.get_state(opportunity.id)
        self.assertEqual(state["current_stage"], "paid")
        self.assertEqual(state["status"], "paid")
        self.assertEqual(state["last_amount"], 120.0)
        self.assertTrue(state["history"][-1]["transition_ok"])


if __name__ == "__main__":
    unittest.main()
