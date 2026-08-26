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
            DeclareLaunchArgument(
                "allow_lan_access",
                default_value="false",
                description=(
                    "Allow TLS-protected HTTP and WebSocket access on the specified "
                    "private robot IPv4 address."
                ),
            ),
            DeclareLaunchArgument(
                "lan_allowed_subnet",
                default_value="",
                description="CIDR source allowlist required when allow_lan_access is true.",
            ),
            DeclareLaunchArgument(
                "tls_cert_file",
                default_value="",
                description="PEM certificate required when allow_lan_access is true.",
            ),
            DeclareLaunchArgument(
                "tls_key_file",
                default_value="",
                description="PEM private key required when allow_lan_access is true.",
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
                description=(
                    "Exact browser origin. LAN access defaults to the TLS origin "
                    "matching bind_address."
                ),
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
            DeclareLaunchArgument("scan_topic", default_value="/scan_nav/laser"),
            DeclareLaunchArgument("scan_freshness_sec", default_value="1.0"),
            DeclareLaunchArgument("scan_max_points", default_value="360"),
            DeclareLaunchArgument(
                "global_path_topic",
                default_value="/plan",
                description="Nav2 nav_msgs/Path topic in the map frame.",
            ),
            DeclareLaunchArgument("global_path_freshness_sec", default_value="3.0"),
            DeclareLaunchArgument("global_path_max_points", default_value="500"),
            DeclareLaunchArgument("nav_goal_status_freshness_sec", default_value="3.0"),
            DeclareLaunchArgument(
                "initial_pose_settle_timeout_sec", default_value="10.0"
            ),
            DeclareLaunchArgument(
                "initial_pose_position_tolerance_m", default_value="0.5"
            ),
            DeclareLaunchArgument(
                "initial_pose_yaw_tolerance_rad", default_value="0.35"
            ),
            DeclareLaunchArgument(
                "localization_confidence_topic",
                default_value="/localization_3d_confidence",
            ),
            DeclareLaunchArgument(
                "localization_delay_topic",
                default_value="/localization_3d_delay_ms",
            ),
            DeclareLaunchArgument("move_group_action_name", default_value="/move_action"),
            DeclareLaunchArgument(
                "planning_scene_service_name", default_value="/get_planning_scene"
            ),
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
                        "allow_lan_access": ParameterValue(
                            LaunchConfiguration("allow_lan_access"),
                            value_type=bool,
                        ),
                        "lan_allowed_subnet": LaunchConfiguration("lan_allowed_subnet"),
                        "tls_cert_file": LaunchConfiguration("tls_cert_file"),
                        "tls_key_file": LaunchConfiguration("tls_key_file"),
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
                        "scan_topic": LaunchConfiguration("scan_topic"),
                        "scan_freshness_sec": LaunchConfiguration("scan_freshness_sec"),
                        "scan_max_points": LaunchConfiguration("scan_max_points"),
                        "global_path_topic": LaunchConfiguration("global_path_topic"),
                        "global_path_freshness_sec": LaunchConfiguration(
                            "global_path_freshness_sec"
                        ),
                        "global_path_max_points": LaunchConfiguration(
                            "global_path_max_points"
                        ),
                        "nav_goal_status_freshness_sec": LaunchConfiguration(
                            "nav_goal_status_freshness_sec"
                        ),
                        "initial_pose_settle_timeout_sec": LaunchConfiguration(
                            "initial_pose_settle_timeout_sec"
                        ),
                        "initial_pose_position_tolerance_m": LaunchConfiguration(
                            "initial_pose_position_tolerance_m"
                        ),
                        "initial_pose_yaw_tolerance_rad": LaunchConfiguration(
                            "initial_pose_yaw_tolerance_rad"
                        ),
                        "localization_confidence_topic": LaunchConfiguration(
                            "localization_confidence_topic"
                        ),
                        "localization_delay_topic": LaunchConfiguration(
                            "localization_delay_topic"
                        ),
                        "move_group_action_name": LaunchConfiguration(
                            "move_group_action_name"
                        ),
                        "planning_scene_service_name": LaunchConfiguration(
                            "planning_scene_service_name"
                        ),
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
