from pathlib import Path
import unittest


class UiAssetsTest(unittest.TestCase):
    def test_hidden_attribute_overrides_login_layout_display(self):
        package_root = Path(__file__).parents[1]
        stylesheet = (package_root / "x2_operator_panel" / "static" / "style.css").read_text(
            encoding="utf-8"
        )

        self.assertIn("[hidden] { display: none !important; }", stylesheet)

    def test_runtime_websocket_configuration_is_external(self):
        package_root = Path(__file__).parents[1]
        page = (package_root / "x2_operator_panel" / "static" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (package_root / "x2_operator_panel" / "static" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('src="/assets/config.js"', page)
        self.assertNotIn("__WS_PORT__", page)
        self.assertIn("websocketUrl || fallbackUrl", script)
        self.assertIn("event.code === 1008", script)

    def test_place_pose_validation_runs_inside_error_boundary(self):
        package_root = Path(__file__).parents[1]
        script = (package_root / "x2_operator_panel" / "static" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('extra = { ...extra, place_pose: placePose() };', script)
        self.assertNotIn('submitManipulation("place", { place_pose: placePose() })', script)

    def test_available_pose_always_draws_a_robot_marker(self):
        package_root = Path(__file__).parents[1]
        script = (package_root / "x2_operator_panel" / "static" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("if (pose?.available) drawRobotMarker(pose);", script)
        self.assertIn("function clampPointToMap", script)

    def test_available_box_pose_draws_a_distinct_marker(self):
        package_root = Path(__file__).parents[1]
        page = (package_root / "x2_operator_panel" / "static" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (package_root / "x2_operator_panel" / "static" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="box-pose-state"', page)
        self.assertIn("function drawBoxMarker", script)
        self.assertIn("if (boxPose?.available) drawBoxMarker(boxPose);", script)
