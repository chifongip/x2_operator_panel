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

The panel expects the navigation stack to provide `/scan_nav/laser`. Start
`x2_navigation` with its normal launch before starting the panel. It uses the
standard `pointcloud_to_laserscan` package to convert the existing downsampled
`/scan_nav/cloud` stream; it does not replace the self-filtered PointCloud2
that Nav2 uses for obstacle avoidance. Verify the package is present on the
robot image with `ros2 pkg prefix pointcloud_to_laserscan`. For a stock Humble
image that lacks it, install `ros-humble-pointcloud-to-laserscan` and rebuild
the workspace.

The default address is `http://127.0.0.1:8080`. The server keeps this loopback
default so credentials and session cookies do not cross a LAN over cleartext
HTTP.

For temporary remote access, tunnel both local ports over SSH and open
`http://127.0.0.1:8080` on the operator workstation:

```bash
ssh -L 8080:127.0.0.1:8080 -L 8081:127.0.0.1:8081 robot-host
```

The SSH host may be the robot's Wi-Fi address, for example:

```bash
ssh -L 8080:127.0.0.1:8080 -L 8081:127.0.0.1:8081 ubuntu@192.168.252.14
```

Then open `http://127.0.0.1:8080` on the personal computer. This works through
the same Wi-Fi as long as the computer can SSH to the robot and the access
point does not isolate Wi-Fi clients.

For direct, same-Wi-Fi access without an SSH tunnel, explicitly bind the panel
to the robot's private IPv4 address with TLS and a Wi-Fi source subnet
allowlist. The certificate must include the robot IP as an IP subject
alternative name, and the operator computer must trust its issuing CA:

```bash
ros2 launch x2_operator_panel operator_panel.launch.py \
  bind_address:=192.168.252.14 \
  allow_lan_access:=true \
  lan_allowed_subnet:=192.168.252.0/24 \
  tls_cert_file:=/etc/x2_operator_panel/robot-cert.pem \
  tls_key_file:=/etc/x2_operator_panel/robot-key.pem
```

To generate a self-signed certificate on the robot, create a protected
directory and include the robot IP as a certificate subject alternative name:

```bash
sudo install -d -m 700 /etc/x2_operator_panel
sudo openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days 365 \
  -keyout /etc/x2_operator_panel/robot-key.pem \
  -out /etc/x2_operator_panel/robot-cert.pem \
  -subj "/CN=192.168.252.14" \
  -addext "subjectAltName=IP:192.168.252.14" \
  -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth"
sudo chmod 600 /etc/x2_operator_panel/robot-key.pem
sudo chmod 644 /etc/x2_operator_panel/robot-cert.pem
sudo chown "$(id -un):$(id -gn)" /etc/x2_operator_panel
sudo chown "$(id -un):$(id -gn)" /etc/x2_operator_panel/robot-key.pem \
  /etc/x2_operator_panel/robot-cert.pem
```

Import only `robot-cert.pem` into the personal computer's trusted certificate
store before opening the panel. From the personal computer, retrieve the public
certificate and add it to the Ubuntu/Debian system CA bundle:

```bash
scp ubuntu@192.168.252.14:/etc/x2_operator_panel/robot-cert.pem \
  ~/Downloads/robot-cert.crt
sudo install -m 644 ~/Downloads/robot-cert.crt \
  /usr/local/share/ca-certificates/robot-cert.crt
sudo update-ca-certificates
```

Never transfer `robot-key.pem` off the robot. Restart the browser after
importing (`chrome://restart` in Chrome).

The panel process runs as the account that invokes
`ros2 launch`, so that account must own the key while the file remains mode
`0600`. Keep `robot-key.pem` only on the robot. Verify the certificate includes
the required IP before launching:

```bash
openssl x509 -in /etc/x2_operator_panel/robot-cert.pem -noout -text | \
  rg 'Subject:|IP Address'
```

Open `https://192.168.252.14:8080` from the personal computer. The browser
also connects securely to `192.168.252.14:8081` for live panel updates. LAN
mode accepts only the exact RFC1918 IPv4 address supplied as `bind_address`,
rejects wildcard addresses such as `0.0.0.0`, and rejects HTTP/WebSocket
clients outside `lan_allowed_subnet`. Configure the robot firewall to allow
TCP 8080 and 8081 only from the same subnet. LAN mode cannot be combined with
the reverse-proxy `websocket_url` setting below; leave `allowed_origin` empty
to use the exact robot TLS origin automatically.

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

The map supports two confirmed commands. Select **Initial pose** or
**Navigation goal**, then click and drag on the map to set the map-frame
position and heading. Initial pose publishes `geometry_msgs/msg/PoseWithCovarianceStamped`
to `/initialpose`, which the installed Open3D localizer subscribes to. When a
Nav2 action status is available, the panel requires it to be idle. Some idle
Nav2 deployments do not emit an action-status message; in that case the panel
requires an additional confirmation that the operator verified Nav2 is idle
and that the `NavigateToPose` action server is ready. After publishing, it
holds new navigation requests until a `map -> base_link` transform is stamped
after the publication and matches the requested pose within 0.5 m and 0.35 rad
(both configurable launch parameters). A 10-second settle timeout is reported
and remains a navigation interlock until localization is checked and a new
initial pose is supplied. Nav2 action status expires after three seconds; the
operator-idle confirmation is required again until a fresh status arrives. A
map goal sends one confirmed `NavigateToPose`
action; named preset buttons remain available for surveyed locations. The same
additional idle confirmation is required for either kind of navigation goal
when Nav2 has not emitted an action-status message.
Panel-submitted navigation also requires the command mux action server and
Collision Monitor lifecycle node to be available.

After coarse navigation, **Check fine alignment** sends a measurement-only
`/fine_align` goal. **Fine align** requires the timed, one-shot physical-motion
unlock plus a separate confirmation and permits coupled forward, lateral, and
yaw correction toward the tag9-derived table pose. Both require Nav2 idle and
manipulation state `EMPTY` or `HOLDING`; physical alignment additionally requires
an active Collision Monitor lifecycle node. Reverse x is controlled by the
navigation server's `allow_reverse_x` parameter and remains disabled by default.
Feedback and final planar error appear in operation history and native action
cancellation remains available. **Undock** submits the fixed-profile `/undock`
action, which moves backward while correcting lateral and yaw drift using the
distance and speed limits configured by `x2_navigation`; it requires the same physical-motion unlock, confirmation,
Nav2-idle check, manipulation-state gate, and active Collision Monitor as physical
fine alignment. **Cancel docking motion** cancels the active fine-alignment or
undocking goal; **Cancel active goals** still cancels every cancelable panel
operation. `/api/fine-align/cancel` remains a compatibility alias for the shared
`/api/docking/cancel` endpoint.

The map's optional laser layer renders at most 360 finite ranges from
`/scan_nav/laser`, transformed into `map` at the scan timestamp. It is a
localization-alignment aid only and is hidden from command decisions. The
layer reports stale data or a missing scan transform rather than drawing it at
an incorrect pose.

Navigation health shows all six Nav2 lifecycle node states, including collision
monitoring, plus action status, `/odom` freshness, and the newest global path
from `/plan`. The map draws that
map-frame path beneath the robot marker and removes it after three seconds
without an update. `global_path_topic` must publish `nav_msgs/Path` in the
`map` frame; other frames are reported but not drawn.

**Clear Costmap** calls both Nav2 `ClearEntireCostmap` services for the global
and local costmaps after operator confirmation. The button is enabled only when
both services are ready, and the combined result or any partial failure appears
in operation history and the audit log.

MoveIt health shows the configured `move_group` action,
`/get_planning_scene` service, and `/joint_states` freshness. Localization
fitness and delay come from `/localization_3d_confidence` and
`/localization_3d_delay_ms`. These are monitoring signals only; the panel does
not issue MoveIt actions, lifecycle transitions, or velocity commands.

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

## Performance tuning

The panel publishes browser status updates once per second by default. This
only affects display freshness; it does not change command processing, action
handling, TF polling, or navigation and manipulation safety checks. Increase
`status_publish_period_sec` to reduce the browser update rate further:

```bash
ros2 launch x2_operator_panel operator_panel.launch.py \
  status_publish_period_sec:=2.0
```

Display-only ROS telemetry uses best-effort QoS with a depth of one, so the
panel shows the newest sample rather than spending CPU catching up on stale
visual data. Command-interlock topics retain reliable QoS. Nav2 lifecycle
health checks run every five seconds by default; configure
`navigation_lifecycle_poll_period_sec` when a different cadence is needed.

WebSocket compression is disabled by default to avoid CPU spent compressing
small status updates over loopback or SSH. Enable it only when a LAN deployment
needs the bandwidth reduction:

```bash
ros2 launch x2_operator_panel operator_panel.launch.py \
  websocket_compression:=true
```

New WebSocket clients receive a full status snapshot. Later updates contain
only changed fields, while `/api/status` continues to return the complete
snapshot.

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
