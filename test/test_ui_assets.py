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
        self.assertIn("function mergeStatus", script)
        self.assertIn('message.type === "status_delta"', script)

    def test_performance_launch_parameters_are_exposed(self):
        package_root = Path(__file__).parents[1]
        launch_file = (package_root / "launch" / "operator_panel.launch.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('"navigation_lifecycle_poll_period_sec"', launch_file)
        self.assertIn('"websocket_compression"', launch_file)
        self.assertIn('default_value="false"', launch_file)

    def test_camera_previews_use_configurable_conditional_refreshes(self):
        package_root = Path(__file__).parents[1]
        page = (package_root / "x2_operator_panel" / "static" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (package_root / "x2_operator_panel" / "static" / "app.js").read_text(
            encoding="utf-8"
        )
        server = (package_root / "x2_operator_panel" / "panel_server.py").read_text(
            encoding="utf-8"
        )
        launch_file = (package_root / "launch" / "operator_panel.launch.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="front-center-image"', page)
        self.assertIn('id="throttled-image"', page)
        self.assertIn('id="show-camera-previews"', page)
        self.assertIn("function startCameraStreams", script)
        self.assertIn("function toggleCameraPreviews", script)
        self.assertIn('"If-None-Match"', script)
        self.assertIn('"/api/cameras/front-center"', server)
        self.assertIn('"camera_display_rate_hz"', launch_file)
        self.assertIn('"front_center_camera_topic"', launch_file)
        self.assertIn('"throttled_camera_topic"', launch_file)
        self.assertIn('"camera_jpeg_quality"', launch_file)
        self.assertNotIn('package="image_transport"', launch_file)
        self.assertNotIn('package="topic_tools"', launch_file)

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

    def test_manual_carry_pose_transition_controls_are_available(self):
        package_root = Path(__file__).parents[1]
        page = (package_root / "x2_operator_panel" / "static" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (package_root / "x2_operator_panel" / "static" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="carry-pose-server"', page)
        self.assertIn('id="move-carry-a"', page)
        self.assertIn('id="move-carry-b"', page)
        self.assertIn('submitManipulation("move_carry_pose", { target_pose: 0 })', script)
        self.assertIn('submitManipulation("move_carry_pose", { target_pose: 1 })', script)
        self.assertIn("const carryPoseReady", script)
        self.assertIn("button.disabled = !carryPoseReady", script)

    def test_box_profile_reload_control_uses_the_public_coordinator(self):
        package_root = Path(__file__).parents[1]
        page = (package_root / "x2_operator_panel" / "static" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (package_root / "x2_operator_panel" / "static" / "app.js").read_text(
            encoding="utf-8"
        )
        server = (package_root / "x2_operator_panel" / "panel_server.py").read_text(
            encoding="utf-8"
        )
        gateway = (package_root / "x2_operator_panel" / "ros_gateway.py").read_text(
            encoding="utf-8"
        )
        launch_file = (package_root / "launch" / "operator_panel.launch.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="reload-box-profiles"', page)
        self.assertIn('id="box-profiles-file"', page)
        self.assertIn("function reloadBoxProfiles", script)
        self.assertIn('"/api/box-profiles/reload"', script)
        self.assertIn('request("reload_box_profiles", payload)', server)
        self.assertIn('ReloadBoxProfiles, "/reload_box_profiles"', gateway)
        self.assertIn('"box_profiles_file"', launch_file)

    def test_locomanipulation_posture_control_uses_the_public_service(self):
        package_root = Path(__file__).parents[1]
        page = (package_root / "x2_operator_panel" / "static" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (package_root / "x2_operator_panel" / "static" / "app.js").read_text(
            encoding="utf-8"
        )
        server = (package_root / "x2_operator_panel" / "panel_server.py").read_text(
            encoding="utf-8"
        )
        gateway = (package_root / "x2_operator_panel" / "ros_gateway.py").read_text(
            encoding="utf-8"
        )
        launch_file = (package_root / "launch" / "operator_panel.launch.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="posture-form"', page)
        self.assertIn('id="posture-height"', page)
        self.assertIn('id="posture-waist-yaw"', page)
        self.assertIn('id="posture-wait-for-settle"', page)
        self.assertIn('id="set-posture"', page)
        self.assertIn('id="reset-posture"', page)
        self.assertIn('id="release-posture"', page)
        self.assertIn('value="0.0" step="any"', page)
        self.assertIn("function setLocomanipulationPosture", script)
        self.assertIn("function resetLocomanipulationPosture", script)
        self.assertIn("function releaseLocomanipulationPosture", script)
        self.assertIn('"/api/posture"', script)
        self.assertIn('"/api/posture/release"', script)
        self.assertIn('"/api/posture"', server)
        self.assertIn('"/api/posture/release"', server)
        self.assertIn('"set_locomanipulation_posture"', server)
        self.assertIn('"release_locomanipulation_posture"', server)
        self.assertIn(
            'SetLocomanipulationPosture, "/set_locomanipulation_posture"', gateway
        )
        self.assertIn("ClearLocomanipulationPostureTarget", gateway)
        self.assertIn('"/clear_locomanipulation_posture_target"', gateway)
        self.assertIn('"posture_service_timeout_sec"', launch_file)
        self.assertIn('"posture_status_freshness_sec"', launch_file)
        self.assertIn('LocomanipulationPostureStatus', gateway)
        self.assertIn('"/locomanipulation_posture_status"', gateway)

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
        self.assertIn("visibleBoxPoses.forEach", script)
        self.assertIn("drawBoxMarker(visibleBox", script)
        self.assertIn("drawBoxMarker(boxPose);", script)

    def test_visible_box_picker_selects_an_instance_for_pick_goals(self):
        package_root = Path(__file__).parents[1]
        page = (package_root / "x2_operator_panel" / "static" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (package_root / "x2_operator_panel" / "static" / "app.js").read_text(
            encoding="utf-8"
        )
        launch_file = (package_root / "launch" / "operator_panel.launch.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="visible-box-select"', page)
        self.assertIn('id="visible-box-status"', page)
        self.assertIn('id="visible-box-count"', page)
        self.assertIn("function renderVisibleBoxes", script)
        self.assertIn("instance_id: state.selectedBoxId", script)
        self.assertIn("Select a fresh visible box before picking", script)
        self.assertIn('"box_states_topic"', launch_file)
        self.assertIn('"box_states_freshness_sec"', launch_file)

    def test_map_commands_and_scan_overlay_are_available(self):
        package_root = Path(__file__).parents[1]
        page = (package_root / "x2_operator_panel" / "static" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (package_root / "x2_operator_panel" / "static" / "app.js").read_text(
            encoding="utf-8"
        )
        server = (package_root / "x2_operator_panel" / "panel_server.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="select-initial-pose"', page)
        self.assertIn('id="select-navigation-goal"', page)
        self.assertIn('id="check-fine-align"', page)
        self.assertIn('id="execute-fine-align"', page)
        self.assertIn('id="execute-undock"', page)
        self.assertIn('id="cancel-docking-motion"', page)
        self.assertIn('id="clear-costmaps"', page)
        self.assertIn('id="show-scan"', page)
        self.assertIn("function submitMapSelection", script)
        self.assertIn('"/api/initial-pose"', script)
        self.assertIn("confirm_nav2_idle", script)
        self.assertIn("function drawLaserScan", script)
        self.assertIn("function clearCostmaps", script)
        self.assertIn('"/api/costmaps/clear"', script)
        self.assertIn("function undock", script)
        self.assertIn("function cancelDockingMotion", script)
        self.assertIn('"/api/docking/cancel"', script)
        self.assertIn('request("clear_costmaps", payload)', server)
        self.assertIn('request("cancel_docking_motion", {})', server)
        self.assertIn("function formatPlanarError", script)
        self.assertIn("function formatUndockDistance", script)
        self.assertIn("operation.result?.final_error", script)
        self.assertIn("operation.result?.distance_traveled", script)
        self.assertIn("operation.feedback?.current_error", script)
        self.assertIn("error.yaw", script)
        self.assertNotIn("error.theta", script)

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
