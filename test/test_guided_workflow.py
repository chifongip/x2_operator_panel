from pathlib import Path
import shutil
import subprocess
import unittest


class GuidedWorkflowTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for browser workflow tests")
    def test_browser_sequence_and_failure_interlocks(self):
        package_root = Path(__file__).parents[1]
        result = subprocess.run(
            [
                "node",
                str(package_root / "test" / "test_guided_workflow.js"),
                str(package_root / "x2_operator_panel" / "static" / "app.js"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
