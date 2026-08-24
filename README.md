# X2 Operator Panel

`x2_operator_panel` is a local web operator interface for the existing X2
ROS 2 stacks. It owns browser-to-ROS translation only; the manipulation server,
Nav2, controllers, localization, and hardware safety systems remain the motion
authorities.

The panel serves the configured Nav2 PGM map from local disk, converts it to an
in-memory browser PNG, and draws the live `map -> base_link` TF pose. It does
not forward the `/map` raster over DDS and it never publishes `/cmd_vel`.

## Configure

Generate a password hash without placing credentials in the repository:

```bash
ros2 run x2_operator_panel operator_panel_hash_password
export X2_OPERATOR_PANEL_PASSWORD_HASH='pbkdf2_sha256$...'
```

Add only surveyed and collision-reviewed `map`-frame navigation destinations to
`config/navigation_presets.yaml` before exposing navigation controls:

```yaml
presets:
  - id: loading_bay
    label: Loading bay
    pose: {x: 1.20, y: -2.40, yaw: 1.57}
```

The shipped preset file is intentionally empty; this repository has no
verified destinations to preconfigure.

## Run

Start shared state, localization, Nav2, and manipulation separately. Do not
start another state publisher or controller manager for the panel.

```bash
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 launch x2_operator_panel operator_panel.launch.py
```

The default address is `http://127.0.0.1:8080`. The server deliberately refuses
non-loopback bind addresses because credentials and session cookies must not
cross a LAN over cleartext HTTP.

For temporary remote access, tunnel both local ports over SSH and open
`http://127.0.0.1:8080` on the operator workstation:

```bash
ssh -L 8080:127.0.0.1:8080 -L 8081:127.0.0.1:8081 robot-host
```

For persistent LAN access, terminate TLS in an authenticated reverse proxy and
proxy the HTTP and WebSocket ports separately. Keep this node bound to loopback,
set `allowed_origin` to the exact external HTTPS origin, and set
`websocket_url` to its external WSS endpoint, for example:

```bash
ros2 launch x2_operator_panel operator_panel.launch.py \
  allowed_origin:=https://robot.example \
  websocket_url:=wss://robot.example/status-stream
```

The proxy must route `/` to `127.0.0.1:8080` and the configured status-stream
endpoint to `127.0.0.1:8081`, preserving the browser `Host` and `Origin`
headers. HTTPS origins automatically enable the `Secure` session-cookie flag.
Use network access controls in addition to the panel password.

## Safety behavior

Manipulation starts in plan-only mode. A physical manipulation request needs a
timed unlock and a command-specific confirmation, and the existing action
server must still accept the goal. `NavigateToPose` has no plan-only mode, so a
selected named destination always needs a confirmation before the panel sends
the real Nav2 goal. Navigation is rejected unless the manipulation state is
known and the `map -> base_link` transform is current.

Cancel sends native ROS action cancellation requests for goals created by this
panel. It is cooperative and is not a hardware emergency stop. Use the
independent robot safety system for emergency stopping.

On shutdown, the panel stops accepting requests and asks ROS to cancel every
active action goal, then waits up to `shutdown_cancel_grace_sec` for terminal
results. A cancellation request or process exit does not prove that the robot
stopped. If cancellation is unconfirmed, verify robot state before restarting
the panel or submitting another command.

Request-handler, login, WebSocket-client, operation-history, action-admission,
and service-response limits are bounded by launch parameters. Keep the shipped
defaults unless deployment testing justifies changing them.

The map marker is unavailable when the `map -> base_link` TF chain cannot be
resolved. An amber marker means the last transform is retained but has not been
observed updating within `tf_freshness_sec`; navigation remains disabled in
that state. `/odom` is never used as a replacement because it is not globally
map-aligned.

The panel also displays the `/box_pose` detection. The standard manipulation
localizer publishes this pose in `base_link`, which the panel projects into the
map using the current robot pose. A stale box detection is amber after
`box_pose_freshness_sec`; an unsupported source frame is reported in System
status instead of being plotted incorrectly.

## Future improvement

Replace the hand-written HTTP and WebSocket servers with FastAPI and Uvicorn,
using Pydantic request models, Pillow for PGM-to-PNG conversion, and HTTPX for
in-process API tests. Keep the ROS command queue, action-goal tracking,
authentication policy, and safety interlocks unchanged. Evaluate this only in a
ROS-compatible virtual environment; do not install newer Python dependencies
globally into the system ROS environment.

## Test

```bash
source /opt/ros/humble/setup.bash
colcon build --packages-select x2_operator_panel
source install/setup.bash
colcon test --packages-select x2_operator_panel --event-handlers console_direct+
colcon test-result --verbose
```
