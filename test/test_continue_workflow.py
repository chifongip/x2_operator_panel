from pathlib import Path
import shutil
import subprocess
import unittest


class ContinueWorkflowTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for browser workflow tests")
    def test_continue_cancel_and_retry_controls(self):
        root = Path(__file__).parents[1]
        result = subprocess.run(
            ["node", str(root / "test" / "test_continue_workflow.js"),
             str(root / "x2_operator_panel" / "static" / "app.js")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
