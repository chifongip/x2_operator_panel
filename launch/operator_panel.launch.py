from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    panel_share = Path(get_package_share_directory("x2_operator_panel"))
    navigation_share = Path(get_package_share_directory("x2_navigation"))
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "bind_address",
                default_value="127.0.0.1",
                description="Loopback HTTP and WebSocket bind address.",
            ),
            DeclareLaunchArgument("http_port", default_value="8080"),
            DeclareLaunchArgument("websocket_port", default_value="8081"),
            DeclareLaunchArgument(
                "websocket_url",
                default_value="",
                description=(
                    "Optional browser-facing ws:// or wss:// URL used through a "
                    "reverse proxy."
                ),
            ),
            DeclareLaunchArgument(
                "allowed_origin",
                default_value="",
                description="Exact browser origin. Non-loopback origins must use HTTPS.",
            ),
            DeclareLaunchArgument(
                "map_yaml",
                default_value=str(
                    navigation_share / "map" / "2026-08-18-Lab_voxel_0_05m.yaml"
                ),
                description="Nav2 static map YAML served locally by the panel.",
            ),
            DeclareLaunchArgument(
                "navigation_presets_file",
                default_value=str(panel_share / "config" / "navigation_presets.yaml"),
                description="Surveyed, map-frame navigation presets available to operators.",
            ),
            DeclareLaunchArgument("session_ttl_sec", default_value="1800.0"),
            DeclareLaunchArgument("execution_unlock_sec", default_value="30.0"),
            DeclareLaunchArgument("box_pose_freshness_sec", default_value="0.5"),
            DeclareLaunchArgument("tf_freshness_sec", default_value="1.0"),
            DeclareLaunchArgument("goal_admission_timeout_sec", default_value="5.0"),
            DeclareLaunchArgument("service_timeout_sec", default_value="5.0"),
            DeclareLaunchArgument("shutdown_cancel_grace_sec", default_value="5.0"),
            DeclareLaunchArgument("http_worker_limit", default_value="16"),
            DeclareLaunchArgument("http_request_timeout_sec", default_value="5.0"),
            DeclareLaunchArgument("websocket_client_limit", default_value="4"),
            DeclareLaunchArgument("websocket_send_timeout_sec", default_value="1.0"),
            DeclareLaunchArgument("login_per_source_limit", default_value="5"),
            DeclareLaunchArgument("login_global_limit", default_value="30"),
            DeclareLaunchArgument("login_window_sec", default_value="60.0"),
            DeclareLaunchArgument("operation_history_limit", default_value="100"),
            DeclareLaunchArgument("use_sim_time", default_value="false"),
            Node(
                package="x2_operator_panel",
                executable="operator_panel",
                name="x2_operator_panel",
                output="screen",
                parameters=[
                    {
                        "bind_address": LaunchConfiguration("bind_address"),
                        "http_port": LaunchConfiguration("http_port"),
                        "websocket_port": LaunchConfiguration("websocket_port"),
                        "websocket_url": LaunchConfiguration("websocket_url"),
                        "allowed_origin": LaunchConfiguration("allowed_origin"),
                        "map_yaml": LaunchConfiguration("map_yaml"),
                        "navigation_presets_file": LaunchConfiguration(
                            "navigation_presets_file"
                        ),
                        "session_ttl_sec": LaunchConfiguration("session_ttl_sec"),
                        "execution_unlock_sec": LaunchConfiguration("execution_unlock_sec"),
                        "box_pose_freshness_sec": LaunchConfiguration(
                            "box_pose_freshness_sec"
                        ),
                        "tf_freshness_sec": LaunchConfiguration("tf_freshness_sec"),
                        "goal_admission_timeout_sec": LaunchConfiguration(
                            "goal_admission_timeout_sec"
                        ),
                        "service_timeout_sec": LaunchConfiguration("service_timeout_sec"),
                        "shutdown_cancel_grace_sec": LaunchConfiguration(
                            "shutdown_cancel_grace_sec"
                        ),
                        "http_worker_limit": LaunchConfiguration("http_worker_limit"),
                        "http_request_timeout_sec": LaunchConfiguration(
                            "http_request_timeout_sec"
                        ),
                        "websocket_client_limit": LaunchConfiguration(
                            "websocket_client_limit"
                        ),
                        "websocket_send_timeout_sec": LaunchConfiguration(
                            "websocket_send_timeout_sec"
                        ),
                        "login_per_source_limit": LaunchConfiguration(
                            "login_per_source_limit"
                        ),
                        "login_global_limit": LaunchConfiguration(
                            "login_global_limit"
                        ),
                        "login_window_sec": LaunchConfiguration("login_window_sec"),
                        "operation_history_limit": LaunchConfiguration(
                            "operation_history_limit"
                        ),
                        "use_sim_time": ParameterValue(
                            LaunchConfiguration("use_sim_time"), value_type=bool
                        ),
                    }
                ],
            ),
        ]
    )
