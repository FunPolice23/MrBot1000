import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from action_pipeline import ActionPipeline, ProposedAction


class TestSafeModePipeline(unittest.TestCase):
    def test_safe_mode_skips_writing_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = ActionPipeline(root_folder=tmpdir, log_fn=lambda _: None, safe_mode=True)
            action = ProposedAction(
                action_type="create_file",
                proposer="Tester",
                description="Create a dry-run file",
                target_path="example.txt",
                code="print('safe mode')",
            )

            result = pipeline.process(action)

            self.assertTrue(result.success)
            self.assertIn("safe mode", result.message.lower())
            self.assertFalse(Path(tmpdir, "example.txt").exists())


if __name__ == "__main__":
    unittest.main()
