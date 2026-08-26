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

    def test_place_pose_defaults_to_tag_placement_with_manual_override(self):
        package_root = Path(__file__).parents[1]
        script = (package_root / "x2_operator_panel" / "static" / "app.js").read_text(
            encoding="utf-8"
        )
        page = (package_root / "x2_operator_panel" / "static" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("function manualPlacePoseEnabled", script)
        self.assertIn("function placePose", script)
        self.assertIn("manualPlacePoseEnabled() && !extra.place_pose", script)
        self.assertIn("syncManualPlacePoseFields", script)
        self.assertIn('id="place-button"', page)
        self.assertIn('id="place-form"', page)
        self.assertIn('id="use-manual-place-pose"', page)
        self.assertIn('id="manual-place-fields" class="place-pose-fields" disabled', page)

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

    def test_map_commands_and_scan_overlay_are_available(self):
        package_root = Path(__file__).parents[1]
        page = (package_root / "x2_operator_panel" / "static" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (package_root / "x2_operator_panel" / "static" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="select-initial-pose"', page)
        self.assertIn('id="select-navigation-goal"', page)
        self.assertIn('id="show-scan"', page)
        self.assertIn("function submitMapSelection", script)
        self.assertIn('"/api/initial-pose"', script)
        self.assertIn("confirm_nav2_idle", script)
        self.assertIn("function drawLaserScan", script)

    def test_global_path_has_a_map_overlay_and_health_state(self):
        package_root = Path(__file__).parents[1]
        page = (package_root / "x2_operator_panel" / "static" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (package_root / "x2_operator_panel" / "static" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="global-path-state"', page)
        self.assertIn("function drawGlobalPath", script)
        self.assertIn("navigation?.global_path", script)
