from pathlib import Path
import shutil
import subprocess
import unittest


class GuidedWorkflowTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for browser tests")
    def test_panel_layout_switch_and_responsive_preference(self):
        root = Path(__file__).parents[1]
        result = subprocess.run(
            ["node", str(root / "test/test_panel_layout.js"),
             str(root / "x2_operator_panel/static/app.js")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for browser tests")
    def test_rotation_controls(self):
        root = Path(__file__).parents[1]
        subprocess.run(
            ["node", str(root / "test/test_rotation_controls.js"),
             str(root / "x2_operator_panel/static/app.js")],
            check=True,
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for browser tests")
    def test_navigation_destination_editor_and_persistence_requests(self):
        root = Path(__file__).parents[1]
        result = subprocess.run(
            ["node", str(root / "test/test_navigation_destinations.js"),
             str(root / "x2_operator_panel/static/app.js")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for browser tests")
    def test_task_shortcut_editor_sequence_and_disconnects(self):
        root = Path(__file__).parents[1]
        result = subprocess.run(
            ["node", str(root / "test/test_task_shortcuts.js"),
             str(root / "x2_operator_panel/static/app.js")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for docking selector tests")
    def test_docking_profile_selectors_and_action_payloads(self):
        root = Path(__file__).parents[1]
        result = subprocess.run(
            ["node", str(root / "test/test_docking_profiles.js"),
             str(root / "x2_operator_panel/static/app.js")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for browser tests")
    def test_saved_plan_execution_and_invalidation(self):
        root = Path(__file__).parents[1]
        result = subprocess.run(
            ["node", str(root / "test/test_saved_plan.js"),
             str(root / "x2_operator_panel/static/app.js")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for browser timer tests")
    def test_execution_badge_countdown_and_connection_lifecycle(self):
        package_root = Path(__file__).parents[1]
        result = subprocess.run(
            [
                "node",
                str(package_root / "test" / "test_execution_badge.js"),
                str(package_root / "x2_operator_panel" / "static" / "app.js"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
