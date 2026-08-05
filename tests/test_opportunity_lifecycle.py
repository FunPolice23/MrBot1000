import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from earning_pipeline import Opportunity
from agents.opportunity_lifecycle import OpportunityLifecycleTracker


class TestOpportunityLifecycle(unittest.TestCase):
    def test_chat_router_includes_lifecycle_snapshot(self):
        import os
        import tempfile

        from agents.chat_router import ChatRouter
        from agents.shared_context import SharedContext

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = SharedContext(path=os.path.join(tmpdir, "shared.json"))
            shared.update_opportunity_lifecycle(
                "opp_999",
                current_stage="paid",
                status="paid",
                last_amount=250.0,
                note="Received payment",
            )

            router = ChatRouter()
            context = router.build_runtime_context(tmpdir, user_message="What opportunities are in progress?")
            self.assertIn("opp_999", context)
            self.assertIn("paid", context)
            self.assertIn("Status Report", context)
            self.assertIn("Board-ready snapshot:", context)
            self.assertIn("Primary action:", context)
            self.assertIn("Overall:", context)
            self.assertIn("Stage: paid", context)
            self.assertIn("Amount: $250.00", context)
            self.assertIn("Recommendation:", context)
            self.assertIn("Priority:", context)
            self.assertNotIn('"current_stage": "paid"', context)

    def test_tracker_moves_opportunity_through_stages(self):
        tracker = OpportunityLifecycleTracker()
        opportunity = Opportunity(
            id="opp_123",
            source="upwork",
            platform="Upwork",
            title="Build automation",
            description="Need Python automation",
            url="https://example.com/job",
        )

        tracker.start(opportunity)
        tracker.mark_researched(opportunity.id)
        tracker.mark_applied(opportunity.id)
        tracker.mark_in_progress(opportunity.id)
        tracker.mark_submitted(opportunity.id)
        tracker.mark_paid(opportunity.id, amount=250.0)

        state = tracker.get_state(opportunity.id)
        self.assertEqual(state["current_stage"], "paid")
        self.assertEqual(state["last_amount"], 250.0)
        self.assertEqual(state["history"][-1]["stage"], "paid")

    def test_tracker_marks_failed_state(self):
        tracker = OpportunityLifecycleTracker()
        opportunity = Opportunity(id="opp_456", source="fiverr", platform="Fiverr")

        tracker.start(opportunity)
        tracker.mark_failed(opportunity.id, "Client declined")

        state = tracker.get_state(opportunity.id)
        self.assertEqual(state["current_stage"], "failed")
        self.assertEqual(state["status"], "failed")


if __name__ == "__main__":
    unittest.main()
