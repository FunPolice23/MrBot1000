"""
Test suite for MrBot1000 Earning Pipeline.

Run with: python test_earning_pipeline.py
"""

import os
import sys
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestUpworkClient(unittest.TestCase):
    """Tests for Upwork API client."""

    def test_import(self):
        from agents.upwork_client import UpworkClient, UpworkGig
        self.assertIsNotNone(UpworkClient)
        self.assertIsNotNone(UpworkGig)

    def test_gig_dataclass(self):
        from agents.upwork_client import UpworkGig
        gig = UpworkGig(
            id="gig-456",
            title="Python Developer",
            description="Build a fast API",
            budget_usd=500.0,
            skills=["Python", "FastAPI"],
            url="https://www.upwork.com/jobs/gig-456",
        )
        d = gig.to_dict()
        self.assertEqual(d["job_id"], "gig-456")
        self.assertEqual(d["budget"], 500.0)


class TestFiverrClient(unittest.TestCase):
    """Tests for Fiverr client."""

    def test_import(self):
        from agents.fiverr_client import FiverrClient, FiverrGig
        self.assertIsNotNone(FiverrClient)
        self.assertIsNotNone(FiverrGig)


class TestAirdropScanner(unittest.TestCase):
    """Tests for airdrop scanner."""

    def test_import(self):
        from agents.airdrop_scanner import AirdropScanner, AirdropOpportunity
        self.assertIsNotNone(AirdropScanner)
        self.assertIsNotNone(AirdropOpportunity)

    def test_evaluate_risk_low(self):
        from agents.airdrop_scanner import AirdropOpportunity
        scanner = AirdropScanner()

        # Safe airdrop - no red flags
        result = scanner._assess_risk(
            "Free token airdrop for community members",
            "https://safe-airdrop.com",
        )
        self.assertEqual(result, "low")

    def test_evaluate_risk_high(self):
        from agents.airdrop_scanner import AirdropOpportunity
        scanner = AirdropScanner()

        # High-risk airdrop - red flags
        result = scanner._assess_risk(
            "Send ETH to claim free tokens",
            "https://sketchy-airdrop.xyz",
        )
        self.assertEqual(result, "high")


class TestDefiScanner(unittest.TestCase):
    """Tests for DeFi scanner."""

    def test_import(self):
        from agents.defi_scanner import DeFiScanner, DeFiOpportunity
        self.assertIsNotNone(DeFiScanner)
        self.assertIsNotNone(DeFiOpportunity)


class TestWalletManager(unittest.TestCase):
    """Tests for wallet manager."""

    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.mkdtemp(prefix="hermes-test-")
        from agents.wallet_manager import WalletManager
        self.wm = WalletManager(root_folder=self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_add_solana_wallet(self):
        self.wm.add_solana_wallet("test_sol", "SolAddress123")
        wallets = self.wm.list_wallets()
        self.assertEqual(len(wallets), 1)
        self.assertEqual(wallets[0]["type"], "solana")
        self.assertEqual(wallets[0]["name"], "test_sol")

    def test_add_ethereum_wallet(self):
        self.wm.add_ethereum_wallet("test_eth", "EthAddress456")
        wallets = self.wm.list_wallets()
        self.assertEqual(len(wallets), 1)
        self.assertEqual(wallets[0]["type"], "ethereum")

    def test_get_balance_unknown_wallet(self):
        bal = self.wm.get_balance("nonexistent")
        self.assertIsNone(bal)


class TestContentGenerator(unittest.TestCase):
    """Tests for content generator."""

    def test_import(self):
        from agents.content_generator import ContentGenerator
        self.assertIsNotNone(ContentGenerator)

    def test_find_opportunities(self):
        from agents.content_generator import ContentGenerator
        gen = ContentGenerator(worker=None)
        opps = gen.find_content_opportunities()
        self.assertGreater(len(opps), 0)
        platforms = [o["platform"] for o in opps]
        self.assertIn("Mirror.xyz", platforms)
        self.assertIn("Hive", platforms)
        self.assertIn("Gitcoin", platforms)


class TestMicrotaskClient(unittest.TestCase):
    """Tests for microtask client."""

    def test_import(self):
        from agents.microtask_client import MicrotaskClient, MicrotaskGig
        self.assertIsNotNone(MicrotaskClient)
        self.assertIsNotNone(MicrotaskGig)


class TestEarningPipeline(unittest.TestCase):
    """Tests for the core earning pipeline engine."""

    def setUp(self):
        self.tmp_db = tempfile.mktemp(suffix=".db", prefix="hermes-test-")

    def tearDown(self):
        if os.path.exists(self.tmp_db):
            os.unlink(self.tmp_db)

    def test_instantiation(self):
        from earning_pipeline import EarningPipeline
        pipe = EarningPipeline(db_path=self.tmp_db)
        self.assertIsNotNone(pipe)

    def test_discover_empty_sources(self):
        from earning_pipeline import EarningPipeline
        pipe = EarningPipeline(db_path=self.tmp_db)
        opps = pipe.discover(sources=[])
        self.assertIsInstance(opps, list)

    def test_discover_airdrops(self):
        from earning_pipeline import EarningPipeline
        pipe = EarningPipeline(db_path=self.tmp_db)
        opps = pipe.discover(sources=["airdrop"])
        # Should work even without network (may be empty)
        self.assertIsInstance(opps, list)

    def test_filter_by_risk(self):
        from earning_pipeline import EarningPipeline, Opportunity
        pipe = EarningPipeline(db_path=self.tmp_db)

        opps = [
            Opportunity(id=f"test-{i}", source="test", type="test",
                          estimated_usd_value=100.0, risk_level=["low", "medium", "high"][i % 3])
            for i in range(3)
        ]

        filtered_low = pipe.filter(opps, max_risk="low")
        self.assertEqual(len(filtered_low), 1)  # Only one low risk

        filtered_med = pipe.filter(opps, max_risk="medium")
        self.assertEqual(len(filtered_med), 2)  # low + medium

        filtered_high = pipe.filter(opps, max_risk="high")
        self.assertEqual(len(filtered_high), 3)  # all

    def test_get_revenue_report(self):
        from earning_pipeline import EarningPipeline
        pipe = EarningPipeline(db_path=self.tmp_db)
        report = pipe.get_revenue_report(days=30)
        self.assertIsInstance(report, dict)
        self.assertIn("total_revenue_usd", report)
        self.assertIn("total_outcomes", report)
        self.assertIn("avg_revenue_per_outcome", report)

    def test_run_full_cycle_no_error(self):
        from earning_pipeline import EarningPipeline
        pipe = EarningPipeline(db_path=self.tmp_db)
        result = pipe.run_full_cycle(sources=[], max_risk="medium")
        self.assertIsInstance(result.success, bool)
        self.assertGreater(len(result.message), 0)


class TestIntegration(unittest.TestCase):
    """Integration tests for earning pipeline workflow."""

    def setUp(self):
        self.tmp_db = tempfile.mktemp(suffix=".db", prefix="hermes-int-")
        os.environ["CLAWGIG_API_KEY"] = "test-key"  # Dummy key for testing

    def tearDown(self):
        if os.path.exists(self.tmp_db):
            os.unlink(self.tmp_db)
        os.environ.pop("CLAWGIG_API_KEY", None)

    def test_full_workflow(self):
        from earning_pipeline import EarningPipeline
        pipe = EarningPipeline(db_path=self.tmp_db)

        # 1. Discover - use valid sources only
        opps = pipe.discover(sources=["social", "dynamic"])
        self.assertGreater(len(opps), 0, "Should find at least some opportunities")

        # 2. Get report
        report = pipe.get_revenue_report()
        self.assertIsInstance(report["total_revenue_usd"], float)


def run_tests():
    """Run all tests and print results."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes (ClawGig tests removed - service discontinued)
    suite.addTests(loader.loadTestsFromTestCase(TestUpworkClient))
    suite.addTests(loader.loadTestsFromTestCase(TestFiverrClient))
    suite.addTests(loader.loadTestsFromTestCase(TestAirdropScanner))
    suite.addTests(loader.loadTestsFromTestCase(TestDefiScanner))
    suite.addTests(loader.loadTestsFromTestCase(TestWalletManager))
    suite.addTests(loader.loadTestsFromTestCase(TestContentGenerator))
    suite.addTests(loader.loadTestsFromTestCase(TestMicrotaskClient))
    suite.addTests(loader.loadTestsFromTestCase(TestEarningPipeline))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegration))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_tests())