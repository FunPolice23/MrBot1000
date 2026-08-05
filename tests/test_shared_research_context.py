import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.chat_router import ChatRouter
from agents.shared_context import SharedContext


class TestSharedResearchContext(unittest.TestCase):
    def test_chat_router_includes_shared_research_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            context_path = str(Path(tmpdir) / "shared_context.json")
            os.environ["SHARED_CONTEXT_PATH"] = context_path
            try:
                shared_context = SharedContext(context_path)
                shared_context.update_research_snapshot({
                    "research_path": tmpdir,
                    "research_file_count": 3,
                    "research": "Key finding: use this context for better replies.",
                    "root": "root context",
                })

                router = ChatRouter()
                runtime_context = router.build_runtime_context(tmpdir, "What do you know?")

                self.assertIn("SHARED RESEARCH SNAPSHOT", runtime_context)
                self.assertIn("use this context", runtime_context)
            finally:
                os.environ.pop("SHARED_CONTEXT_PATH", None)


if __name__ == "__main__":
    unittest.main()
