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

Create surveyed, collision-reviewed `map`-frame navigation destinations in
**Navigation → Manage destinations**. The panel starts with an empty list when
`navigation_destinations_file` does not exist and creates that file on the first
save. This single file is used for both loading and saving destinations.

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

## Camera previews

After sign-in, the panel shows the rectified front-center AprilTag image from
`/aima/hal/sensor/rgb_head_front_center/rgb_image_rect` and the throttled
detector input from `/x2/rgb_image_throttled`. Camera frames are delivered as
authenticated JPEG responses, not through the status WebSocket. By default,
the panel encodes and polls each preview at no more than 1 Hz with JPEG quality
70; unchanged frames use HTTP conditional requests and do not resend the JPEG.
Use the checked-by-default **Show previews** control to pause both browser image
requests entirely while leaving the rest of the panel active. Raw camera
subscriptions and JPEG encoding are demand-driven: they start when an
authenticated browser requests each preview and stop after requests cease for
three refresh periods (at least three seconds). This also stops camera work
when no browser is connected. On first opening or resuming previews, the browser
may show **Waiting for image** until the next refresh.

For a lower-bandwidth remote view, for example 0.5 Hz at JPEG quality 60:

```bash
ros2 launch x2_operator_panel operator_panel.launch.py \
  camera_display_rate_hz:=0.5 \
  camera_jpeg_quality:=60
```

The panel subscribes directly to the raw `Image` topics, drops frames before
conversion, and uses Pillow to encode only the selected preview frame as a
JPEG. It does not use OpenCV or NumPy, so it avoids the Jetson OpenCV/NumPy ABI
mismatch. Install the prebuilt `python3-pil` package on a robot image that does
not already provide Pillow:

```bash
sudo apt install python3-pil
```

While previews are active, these parameters limit Pillow encoding and browser
traffic only. They do not alter AprilTag detection or reduce the raw DDS frame
rate from a camera publisher to a panel running on another machine. Paused
previews unsubscribe from those streams after the demand timeout. Run the panel
on the robot with the camera publishers, or rate-limit the source image pipeline,
when that DDS link must also be reduced.

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

Button colors indicate the action's role. A text legend appears below the
panel header, and disabled controls use a muted gray appearance. Command labels
and confirmations describe the action independently of its color.

| Appearance | Meaning | Examples |
| --- | --- | --- |
| Solid amber | Requires an unlock for physical execution | Pick/Place, posture, Fine align, Undock, Start/Continue sequence |
| Outlined amber | Enable the timed motion unlock | Unlock physical motion |
| Blue | Submit navigation | Named destinations, confirmed map navigation goal |
| Teal | Measure or continue an existing task | Check fine alignment, Continue task |
| Red | Stop or cancel | Stop sequence, Cancel task, Cancel active goals |
| Neutral outline | Setup and maintenance | Initial pose, Clear Costmap, Reload profiles, Confirm empty/holding |

Map mode selection uses a filled neutral button and `aria-pressed` to identify
the selected mode. Keyboard focus has a visible outline. Colors do not change
execution permissions or ROS safety interlocks; plan-only manipulation still
requires no physical unlock.

The execution badge counts down the timed unlock locally between server status
messages. The server remains authoritative and consumes the unlock when one
physical command is submitted. The badge shows **Locked** when no unlock remains
and **Plan only** is unchecked; it shows **Plan only** when that toggle is checked
and no unlock remains. It displays **Status unavailable** on connection loss and
waits for a fresh status snapshot before showing an unlocked state again.
Unrelated status deltas do not renew the countdown. Start/Continue sequence gates
use the same elapsed-time calculation, so an expired displayed unlock cannot
leave those controls enabled while waiting for the next status push.

Manipulation starts in plan-only mode. A physical manipulation request needs a
timed unlock and a command-specific confirmation, and the existing action
server must still accept the goal. `NavigateToPose` has no plan-only mode, so a
selected named destination always needs a confirmation before the panel sends
the real Nav2 goal. Navigation is rejected unless the manipulation state is
known and the `map -> base_link` transform is current.

The panel admits one task at a time across navigation, Fine Align, Undock,
and every manipulation action, including plan-only goals, saved-plan execution,
carry poses, and reset. A goal reserves the slot while submitting and keeps it
until a terminal result; a cancellation request or acceptance timeout does not
release it. Running, retrying, or paused `/manipulation_task_status` reports and
active goals observed on either Nav2 action or any panel action's status topic
also block new tasks, including tasks started by another ROS client. Manual
Nav2-idle confirmation cannot override a known active task of any kind.
Recovery, profile reload, posture commands, initial-pose changes, and costmap
clearing use the same admission check. A timed-out service's unknown outcome
keeps the slot reserved until its late response establishes completion.
Action-result transport errors retain the task slot and retry result retrieval.
An unknown goal-acceptance outcome also retains the slot; if it cannot be
resolved, verify and stop the outstanding task before restarting the panel.
`task_admission` in the status snapshot exposes the blocker to the browser.
Continue and Cancel operate on the existing task and remain available.

This admission policy governs commands sent through the panel. The navigation,
docking, and manipulation servers do not share a global task arbiter; concurrent
requests sent directly by other ROS clients can race across those servers before
their status reaches the panel. System-wide exclusivity requires a shared
admission authority in the underlying stacks.

The **Move to Carry A** and **Move to Carry B** controls submit the manual
`/move_carry_pose` manipulation action. They are available only while the
reported manipulation state is `HOLDING`; both use the same plan-only toggle,
execution unlock, and physical-motion confirmation as pick/place. Carry B is a
calibrated payload pose, not a base-navigation command.

The **Dock → Set Height → Pick → Default Height → Undock** control uses **Place** while
the manipulation state is `HOLDING`. It runs independently from navigation;
no previous successful navigation goal is required, and a completed sequence
can be followed by another sequence from the current location. Active motion,
including active Nav2 navigation, must finish before starting. Turn off
**Plan only**, use **Unlock physical motion**, then press the combo control and
confirm the complete physical sequence. Start and **Continue sequence** are
disabled while the timed execution unlock is locked or expired. Amber buttons
identify commands that require an unlock for physical execution, including the
combo, Pick/Place, carry poses, manipulation reset, posture targets, Fine align,
and Undock. Plan-only manipulation remains available without physical unlock.
No visible object or box selection is required to start docking. Before Pick,
the sequence waits for a fresh visible detection. A single detected box is
selected automatically; when several boxes are visible, choose the intended
physical object from the existing **Visible box** list. Selecting a box resumes
the waiting sequence. The target instance ID is bound when Pick is ready and
must still be fresh when the command is submitted; it is not replaced during
an in-flight Pick. Place uses the held object and needs no visible pickup target.
All five stages run automatically, advancing only after the preceding command
succeeds. Dock uses
physical `/fine_align`; the middle stage uses `/pick_box` or `/place_box` (with
the same optional manual place target as the separate Place control); Undock
uses `/undock`. **Set Height** uses the operator's current **Height** and
**Waist yaw** fields, which should be set for the object being handled; object
profiles do not automatically choose a posture. **Default Height** restores the
policy default (`height=0.64 m`, `waist_yaw=0.0 rad`) after manipulation and before
undocking. Both posture stages call `/set_locomanipulation_posture` with
`wait_for_settle=true` and wait for a successful service result. This confirms
the direct feedback window, not that the one-way ZMQ policy has reached the
target. The guided stages leave the operator's input fields unchanged.
The manual unlock is consumed by the first physical command after Start or
Continue. The sequence confirmation authorizes the remaining stages, for which
the panel automatically renews the one-shot unlock immediately before each
command. No additional unlocks are needed while that sequence is running.
The posture target and optional
manual place pose are captured at startup. Later posture or place field edits do
not change the running sequence. Box selection is resolved before Pick after
docking. A failed or canceled stage stops the sequence,
including a failed height reset, which prevents guided undocking. Use navigation
separately when moving between pickup and drop-off locations.
**Continue sequence** requires a fresh manual unlock, refreshes the robot status,
asks for confirmation, and
retries the failed stage while preserving the completed stages and captured
posture/place targets. After a stale-selection rejection at Pick, select a fresh
box and continue; Dock and Set Height are not repeated. Continuing requires no
active motion and the manipulation state expected before that stage. Unknown
command outcomes or lost operation history block continuation. If a stopped
command subsequently reports success, Continue advances past that completed
stage instead of replaying it. **Continue task** remains the control for a
manipulation action that is still paused rather than terminally failed.
**Stop sequence** prevents further steps and requests cancellation of the active
action. A posture service already in progress cannot be canceled; it finishes
without starting the following step. A paused Pick/Place action holds the
sequence; **Continue task** resumes that action, and success resumes the
remaining steps automatically. The guided sequence is held in the browser tab;
keep it open while running. Reloading or signing out prevents further automatic
steps, while an already submitted ROS command remains visible in operation
history and may still complete.

The **Locomanipulation posture** control sends `height` and `waist_yaw` to
`/set_locomanipulation_posture`. It is available in confirmed `EMPTY` and
`HOLDING` states, so an operator can set a carrying posture after pick and a
release posture after place. Like every physical panel motion, it consumes one
timed execution unlock and requires a per-command confirmation. Keep **Wait
for direct feedback window** selected for workflow transitions; this confirms
fresh leg and waist feedback after publication, not that the one-way RoboJuDo
ZMQ policy has reached the requested target. The control stays disabled until
the manipulation server publishes a current status showing that posture
execution is enabled; when it is active, the panel displays the locally
published target. Its request timeout is automatically extended to cover the
server's advertised feedback-window timeout. The operator must use a
collision-reviewed posture sequence before undocking, navigating, or moving a
held object.

The waist-yaw field accepts exact decimal values, including `0.0`. **Reset
posture** sends the RoboJuDo policy default target (`height=0.64 m`,
`waist_yaw=0.0 rad`) and therefore requires the same execution unlock and
confirmation as any other physical posture command. **Release posture control**
only releases this panel publisher; it does not move the robot or restore the
default posture, because RoboJuDo retains the last accepted posture until
another source overrides it.

**Reload profiles** calls `/reload_box_profiles` with the launch-configured
`box_profiles_file`. The configured catalog must be an absolute path and must
match the file supplied to the manipulation launch. The button is enabled only
when the service is available, no panel operation is active, and manipulation
is `EMPTY`; the manipulation server enforces the same condition atomically.
Edit the catalog, then reload and wait for a fresh box state before a
`plan_only` pick/place verification. To use a non-default catalog, pass it to
both launches:

```bash
ros2 launch x2_operator_panel operator_panel.launch.py \
  box_profiles_file:=/absolute/path/to/box_profiles.yaml
```

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
initial pose is supplied. Nav2 action status is event-driven and does not expire
during silence. The panel monitors both `/navigate_to_pose/_action/status` and
`/navigate_through_poses/_action/status`, retaining each state while its action
server is ready and its DDS status-publisher identity is unchanged. A server
disconnect or publisher replacement clears that action's cached state. A known
active goal on either action blocks all new tasks, even with operator idle
confirmation. The second action is optional
until discovered; once discovered, missing status from it prevents a combined
Idle indication. Before the first status, readiness alone does not establish
Idle: the existing operator-idle confirmation remains required. Hover over the
goal state to see each action's state and the age of its last message.
`nav_goal_status_freshness_sec` remains accepted for launch compatibility but is
deprecated and no longer expires action state. A
map goal sends one confirmed `NavigateToPose`
action; named preset buttons remain available for surveyed locations. The same
additional idle confirmation is required for either kind of navigation goal
when Nav2 has not emitted an action-status message.
Panel-submitted navigation also requires the command mux action server and
Collision Monitor lifecycle node to be available.

After coarse navigation, **Check fine alignment** sends a measurement-only
`/fine_align` goal. **Fine align** requires the timed, one-shot physical-motion
unlock plus a separate confirmation and permits coupled forward, lateral, and
yaw correction toward the selected tag-derived docking pose. Both require Nav2 idle and
manipulation state `EMPTY` or `HOLDING`; physical alignment additionally requires
an active Collision Monitor lifecycle node. Reverse x is controlled by the
navigation server's `allow_reverse_x` parameter and remains disabled by default.
Feedback and final planar error appear in operation history and native action
cancellation remains available. **Undock** submits the selected-profile `/undock`
action, which moves backward while correcting lateral and yaw drift using the
distance and speed limits configured by `x2_navigation`; it requires the same physical-motion unlock, confirmation,
Nav2-idle check, manipulation-state gate, and active Collision Monitor as physical
fine alignment. **Cancel docking motion** cancels the active fine-alignment or
undocking goal; **Cancel active goals** still cancels every cancelable panel
operation. `/api/fine-align/cancel` remains a compatibility alias for the shared
`/api/docking/cancel` endpoint.

### Docking profiles

The **Docking profile** selector applies to Check fine alignment, Fine align,
and the guided Pick/Place sequence. The panel reads profile names, tag IDs and
frames, stand-off distances, lateral offsets, and yaw offsets from
`/fine_align_server/get_parameters`; configure them in the navigation server,
not in a separate panel file. Profile discovery refreshes every five seconds,
with the existing `service_timeout_sec` bounding each asynchronous request.
The default profile retains the navigation server's legacy tag/offset settings.
The panel shows the selected geometry in meters and radians. Lateral offset is
measured along tag `+X`; yaw offset is relative to facing the tag.

**Server default** sends an empty `profile_id` to `/fine_align`, allowing the
navigation server to choose its configured default. The separate **Undocking
profile** selector defaults to **Last successful dock / server default**, which
sends an empty selection to `/undock`. The navigation server then tracks the
last successfully executed dock profile, falling back to its configured default
after restart. An explicit undocking selection overrides that choice for one
retreat. Measuring, failed/canceled docking, and undocking do not change the
server's remembered successful dock.

Guided sequences require profile discovery before starting and capture the
docking profile when started, show it in the
physical-motion confirmation, and retain it when retried or continued. After
Dock succeeds, the sequence uses the profile reported by the action result for
Undock, including when the server selected the default. Later selector edits
and the separate manual Undocking profile choice do not change a running
sequence. Dock/Undock stages wait while profile discovery is unavailable. Operation history displays the requested profile before acquisition
and the resolved profile from feedback/results afterward.

The catalog also shows each profile's detection source and undock mode.
The supplied box approaches use the selected visible box's vertical tag and a
timed retreat at 0.1 m/s for 3.0 s. Box types reference their own named docking
profiles in the manipulation catalog. The selector offers the selected box's
supported approaches and uses that box's default when no approach is selected. Timed feedback shows elapsed seconds and estimated travel, rather
than measured distance. The undock mode follows the resolved docking profile;
it is not an automatic fallback when a table tag disappears.

For a `target_source: box` docking profile, select a fresh visible box before
starting. The guided workflow captures its `instance_id` before Dock and checks
that Dock reports the same instance. Pick reuses the resolved docking profile
and that box ID, even if the selection changes or another box becomes visible.
If the bound box is lost, the sequence waits for that box rather than switching.
Standalone Fine align also accepts the selected visible box. Standalone Pick of
the same box records the last successful box docking profile in operation
history; gateway requests that explicitly reuse that profile reject a different
box instance before consuming the execution unlock. Manipulation geometry
continues to come from the box profile; navigation offsets are not grasp offsets.

Guided Pick and manual-pose Place do not require a matching table profile or a
visible table tag. The original guided combo selects the automatic Place table
by matching the docking tag ID and frame. Task shortcuts select an explicit
table independently of the docking profile; automatic Place still requires a
fresh stable table-tag observation. Guided workflows retain the docking
calibration and mode; changes during a sequence require operator review.
Existing execution unlock, confirmations, and server motion authority apply.

### Command layout

A full-width **Execution controls** section directly above Commands contains
the physical-motion unlock, its status badge, and **Cancel active goals**.

The Commands header contains the shared **Plan only** toggle and **Visible box**
selection. The selected box applies to standalone actions and the quick combo.
Profile-based saved shortcuts use it when it matches their configured box profile;
fixed-instance shortcuts retain their saved tag.

- **Tasks** presents saved shortcut buttons and the quick combo side by side,
  with shared sequence status, Stop, and Continue controls below.
- **Manipulation** groups Pick/Place and their target settings. Manual pose
  fields appear when **Use manual target** is selected. Current task controls
  stay visible; **Saved plans** and **Recovery and profiles** start open and can
  be collapsed when not needed.
- **Posture** contains height, waist yaw, posture actions, and Carry A/B.
  **Posture options** exposes the feedback-window setting.
- **Navigation** contains destination buttons and **Clear costmaps**. Interactive
  initial-pose and navigation-goal controls remain beside the map.
- **Docking** contains profile selectors, alignment commands, Undock, and
  Cancel docking motion.

Tasks spans the full width above both columns. Manipulation occupies the left
column below Tasks. The right column stacks Posture at the top, Docking in the
middle, and Navigation at the bottom. Columns stack
on smaller screens. Shortcut management and posture options start collapsed;
task warnings and cancellation controls remain visible.

### Editable navigation destinations

In **Navigation → Manage destinations**, use **New**, **Edit**, **Duplicate**,
**Delete**, or **Refresh**. Enter a name and X/Y in metres and yaw in radians,
all in the `map` frame. Destination IDs are generated automatically on creation
or duplication, like shortcut IDs. Renaming or editing preserves the ID and
existing shortcut references, including destinations saved with older named IDs.
**Use current robot pose** copies a fresh, connected map-frame robot pose.
**Use selected map goal** copies the position and heading selected in the map's
navigation mode. Both buttons only fill the form; **Save destination** persists it.

Saving updates destination buttons, map markers, and the open shortcut editor
without commanding motion. A destination referenced by a saved shortcut cannot
be deleted; edit or remove those references first. Conflicting edits from other
browser sessions are rejected; refresh and reopen the destination before retrying.
An already submitted navigation goal retains its captured pose. Standalone
navigation and shortcut navigation reject a destination pose changed after
selection or confirmation, requiring the operator to review the destination.

Destinations persist in `navigation_destinations_file`, a ROS parameter and launch
argument defaulting to `~/.local/share/x2_operator_panel/navigation_destinations.json`.
An absent file starts an empty catalog. Existing saved destinations retain their
IDs, poses, and revisions. Use **Refresh** to read edits from another browser.
A malformed or unreadable saved catalog reports an error and is not overwritten.

Authenticated destination endpoints are `GET /api/presets`,
`POST /api/presets/save`, and `POST /api/presets/delete`. Saves contain
`label` and `pose: {x, y, yaw}`. New destinations omit `id` and `revision`;
the server generates a UUID. Edits include the record's `id` and `revision`;
deletes require both as well.

### Timed rotation controls

**Rotate in place** accepts signed angular speed (rad/s) and duration (seconds).
Positive speed turns counterclockwise; negative turns clockwise. The displayed
angle is speed × duration and is approximate: rotation does not use odometry or
tag feedback. **Run** requires physical execution unlock, confirmation, a known
`EMPTY` or `HOLDING` state, active Collision Monitor, and idle Nav2. When Nav2
status is unavailable, explicitly confirm it is idle; Nav2 processes need not run.
Plan only disables rotation. **Cancel rotation** cancels the active timed turn;
the global cancellation controls also include rotation.

`POST /api/actions` accepts `kind: "rotate_in_place"`, `angular_speed`, `duration`,
`confirmed: true`, and, when required, `confirm_nav2_idle: true`. The gateway reads
limits from `/fine_align_server/get_parameters`; `/api/status.rotation_limits`
reports `available`, `max_angular_speed`, and `max_duration`. The server remains
authoritative and rejects invalid or excessive values without clamping. Operation
feedback shows elapsed time, commanded speed, and progress; results include elapsed
time and the reason for completion or interruption. See the navigation README for
server limit parameters and ROS action usage.

### Editable task shortcuts

In **Tasks → Saved shortcuts → Manage shortcuts**, choose **New**, name the shortcut, select **Pick** or
**Place held box**, and configure the optional stages around the combo:

**Navigation → Carry Pose → Rotate → Combo Task → Rotate → Carry Pose → Navigation**

Each rotation stage has its own enable checkbox, signed `angular_speed`, and
`duration`. The initial rotation runs after initial navigation/carry and before
Dock; the final rotation runs after Undock and before final carry/navigation.
Disabled docking or undocking does not disable an enabled rotation. Rotation
stages default to disabled in new and existing shortcuts; their initial editable
values are 0.2 rad/s and 1 second. Saved version-1 records without `rotate_start`
or `rotate_end` load with these stages disabled. Saving never commands motion.

A failed or canceled rotation stops the shortcut. After reviewing the current
orientation, unlocking, and choosing **Continue**, the confirmation explicitly
states that retrying commands the **full duration again**. Remaining duration is
never inferred, and interrupted turns are never retried automatically. Unknown
outcomes retain the existing workflow block until resolved.

Each navigation and carry stage has its own enable checkbox and target.
Navigation destinations come from the editable destination catalog; Carry Pose offers
Carry A or Carry B. Navigation before the combo runs before its carry pose;
navigation after the combo runs after its carry pose. The combo itself is:

1. Dock with a named docking profile, such as `grey_box_dock`.
2. Set posture with the required height and waist yaw.
3. Pick the resolved box instance, or Place the currently held box.
4. Return posture (initially 0.64 m and 0.0 rad, both editable).
5. Undock with a named profile (initially copied from Dock, then independently editable).

Dock, both posture stages, and Undock can each be disabled. Pick/Place is always
required. Carry poses require a held box, so the editor enables the initial
carry stage for Place and the final carry stage for Pick. Invalid combinations
are rejected when saving. The navigation stages work for either action.
Existing saved shortcuts load with all four added stages disabled.

Choose a box profile from the dropdown, or enter a profile name such as
`grey_box`. **Box selection → Visible tag matching profile** is the default:
the saved shortcut does not require one particular tag. When the run first
needs a box for Dock or Pick, it uses the selected matching **Visible box**, or
the sole matching detection if there is only one. Box docking also requires
that the detection supports the chosen docking profile. Multiple matching tags
require an explicit Visible box selection; no match leaves the sequence waiting.
This resolution happens after initial navigation, so the box need not be visible
before navigating to its location. Until the first box command is submitted,
a tag lost during preflight can be replaced by another matching detection.
After submission, each run keeps its resolved tag through Dock and Pick, even
if another tag of the same profile becomes visible later.
The shortcut itself remains reusable with other tags on subsequent runs.

**Box selection → Fixed tag instance** uses a numeric **Fixed box ID**, such
as `180` for `grey_box`; the panel adds the internal `tag:` prefix automatically.
Existing saved shortcuts display their numeric IDs and retain their fixed instances
until edited. For Place, the box fields only identify a box-based docking reference;
Place always operates on the held object. An automatic Place target names its
table profile explicitly; a manual target specifies frame, XYZ, and yaw without
requiring a table tag. Docking and table profiles may reference different tags.

**Save shortcut**, **Edit**, **Duplicate**, **Delete**, and **Refresh** manage
the server's saved definitions without commanding motion. Profiles can also
be entered while ROS discovery is offline using **Enter a profile name…**.
The Dock and Undock dropdowns list every discovered docking profile and preserve
unavailable saved selections. Box Profile lists every profile loaded by the
manipulation server from its box-profile configuration, even without detections.
Box profile IDs are preserved exactly, including hyphens, as single components
in `box_profiles.<id>.<field>` parameters.
The panel reads `/get_box_profiles` and refreshes the catalog every five seconds,
including after a successful profile reload. Table Profile lists the discovered
table profiles. If box-profile discovery is unavailable, detected and saved box
names remain available as a fallback. Both preserve saved selections and support
manual profile entry when the profile is not listed. Execution validates the
configured profiles and resolved target. Saving edits uses revisions and rejects stale edits
from another browser instead of overwriting them. Edits to saved definitions,
selectors, or manual poses do not change an active sequence.
The runner captures each navigation destination's map pose before confirmation
and rejects a changed preset at submission. A box need not be visible
before the initial navigation; the sequence waits for its detection before
box-based Dock or Pick. Navigation uses the existing localization, Nav2-idle,
Collision Monitor, and task-admission checks. A failed or canceled navigation or
carry move stops progression just like a failed combo stage.

Each saved shortcut appears as a named selection button in a separate **Pick**
or **Place** row, preserving saved order within each action. Select at most one
shortcut in each row; clicking the selected button clears it. Selection commands
no motion and is available while physical motion is locked. The selector inside
**Manage shortcuts** remains independent and is used for editing, duplicating,
or deleting definitions.

To execute, connect live status, turn off **Plan only**, unlock physical motion,
and click **Run selected**. A single selection runs that shortcut; selecting both
runs every enabled Pick stage followed automatically by every enabled Place stage.
A pair requires manipulation state `EMPTY`; a single Place requires `HOLDING`.
One confirmation lists both shortcuts and their stages. Both definitions and
referenced configurations are captured before execution; editing them does not
change the running sequence. Place waits for Pick to finish successfully and for
manipulation state `HOLDING`. Progress identifies the active shortcut and stage.
**Stop sequence** prevents further stages, and **Continue sequence** resumes the
current stage after review without replaying completed shortcuts. Selections
remain available for another run but are not saved across page reloads.
Each stage uses the existing ROS action or posture service and advances only after
success. A status disconnect pauses progression. An already submitted command
may finish; after reconnecting, verify its outcome, unlock again, and use
**Continue sequence**. Unknown outcomes block continuation. Reloading the page
does not resume a task automatically. The browser must remain open to advance
the sequence; this is not a server-side job queue.

Shortcuts persist across panel restarts in the JSON file configured by the
`task_shortcuts_file` ROS parameter and launch argument, defaulting to
`~/.local/share/x2_operator_panel/task_shortcuts.json` for the panel user. Use an
absolute writable path when overriding it. A missing file starts an empty
catalog. An invalid file is reported in the panel and preserved until repaired;
saves use atomic file replacement. Keep this operator data outside the source
repository.

Authenticated shortcut endpoints are `GET /api/task-shortcuts`,
`POST /api/task-shortcuts/save`, and `POST /api/task-shortcuts/delete`. Save
accepts the edited definition plus `id` and `revision` when updating; Delete
requires both identifiers. Stale revisions return HTTP 409. Mutation endpoints
use the panel's existing same-origin checks.
For shortcut navigation, `POST /api/actions` uses `kind: navigate`, `preset_id`,
and `expected_preset_pose: {x, y, yaw}`. The gateway compares that pose to the
configured preset before submitting a Nav2 goal.

If discovery is unavailable, automatic manual selections remain usable; explicit
selections are blocked until the catalog is available. A missing or disconnected
profile remains visibly selected rather than silently switching to the default.
Choose a currently configured profile again. Profile validation precedes
consuming the physical-motion unlock. Nav2-idle checks, manipulation-state gates,
Collision Monitor requirements, unlocks, confirmations, and cancellation remain
in effect for every docking command.

Authenticated API additions:

- `GET /api/status` includes `docking_profiles`, with `available`,
  `default_profile`, `profiles` (each containing `id`, `tag_id`, `tag_frame`,
  `standoff`, `lateral_offset`, `yaw_offset`), and `detail`.
- `POST /api/actions` accepts optional `profile_id` for `fine_align` and `undock`.
  Omit it or use an empty string for automatic server selection. Non-string,
  malformed, unknown, or unavailable explicit selections are rejected before
  goal submission; the ROS server also validates selections.
- Operation records include the requested `profile_id`; docking feedback and
  results include the resolved `profile_id` and preserve `INVALID_PROFILE`
  errors from the navigation server.

Rebuild and restart the panel alongside the navigation server when upgrading
these action interfaces. New profiles still require detector support and
commissioned offsets before physical execution.

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

The panel subscribes to `/box_states` and lists every fresh localized box by
its stable `instance_id` and profile. Select a box before using **Pick box** or
**Pick and place**; the panel passes that ID in the action goal and rejects a
selection that is no longer fresh. With one visible box, the panel selects it
automatically; with multiple boxes, the operator must choose one explicitly.
With the default manipulation-server configuration, fresh non-selected boxes
remain collision obstacles.
The panel expires visible detections after `box_states_freshness_sec` (default
0.5 s) and projects `base_link` detections onto the map. Its legacy
`/box_pose` marker remains available for older single-box localizers.
This freshness window measures time since receipt of each box detection; it
does not start at docking completion. The combo has no detection wait timeout:
it waits before Pick until a fresh target is available or the operator stops it.
Browser status is published at 1 Hz by default, so the combo requests a fresh
status snapshot immediately before submitting Pick. The ROS gateway still
checks freshness at submission and can reject a detection that expires during
the HTTP round trip. The freshness setting can be supplied at launch, for example
`ros2 launch x2_operator_panel operator_panel.launch.py box_states_freshness_sec:=0.5`.
Choose any larger window using the measured detector update interval and
acceptable pose age; increasing it also allows older object poses. The existing
configured freshness values are unchanged by this workflow update.

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

### Retrying and continuing manipulation tasks

The **Current task** section displays the server's phase, retry count, last
completed phase, object disposition, and detailed planning warning. Recoverable
failures retry automatically while the original action remains active. The
section also displays two passive monotonic timings: **Task elapsed** runs from
the first observed task status to its terminal status (including planning and
pauses), while **Controller execution** sums observed `EXECUTING` to terminal
intervals for the dual-arm trajectory controller. The panel refreshes them on
its existing status cadence, creates no timing log, and labels a task partial
when the panel joined after it started. Status-message delivery adds small
measurement uncertainty; controller time is not the full task duration. If the
server pauses after exhausting retries, **Continue task** requests replanning of
the unfinished phase. **Cancel task** uses ROS action cancellation for the exact
task UUID, including tasks started outside this panel.

Continue preserves the original goal and completed attachment/release phases.
It does not consume a new execution unlock or submit another manipulation goal.
The button is enabled only for a resumable pause with the Continue service
available, and is disabled while its request is pending. A service-response
timeout displays a warning without aborting the retained action. New action
submissions remain blocked while the manipulation task is running, retrying, or
paused.

ROS interfaces:

- `/manipulation_task_status`: `agibot_x2_manipulation_msgs/msg/ManipulationTaskStatus`
- `/dual_arm_controller/follow_joint_trajectory/_action/status`: `action_msgs/msg/GoalStatusArray`
- `/continue_manipulation`: `agibot_x2_manipulation_msgs/srv/ContinueManipulation`

Authenticated HTTP endpoints:

- `POST /api/manipulation/continue`: `{"task_id": "<action UUID hex>", "pause_id": 1}`
- `POST /api/manipulation/cancel`: `{"task_id": "<action UUID hex>"}`

After a manipulation-server restart, Continue is unavailable; verify the
physical object state using the existing recovery controls. Restart the panel
and manipulation server after rebuilding the new ROS interfaces.

### Table profiles and guided docking

The panel discovers immutable physical table profiles from
`/pick_place_server/get_parameters` and includes them as `table_profiles` in
status. The standalone manipulation table selector sends `table_profile_id` to
Pick, Place, and PickPlace. Empty selection uses the manipulation server's
configured default. Saved execution retains the table reported by its plan,
independently of the current selector. Operation history shows resolved tables.

For automatic Place, the combo derives its table from the selected docking
profile's **tag ID and frame**, requiring exactly one match. Multiple docking approaches may
share the same physical table. Catalogs must be available and a match must exist
before startup. The confirmation captures both names; retries and Continue keep
them. After Dock, the panel checks its resolved profile against the captured
table before advancing. Manipulation requests carry `table_profile_id` and
`docking_profile_id`; the gateway checks the match again before consuming the
motion unlock or submitting the ROS goal. Docking and manipulation select named
profiles; matching by tag does not introduce visibility-based selection.

Manual placement overrides remain available in the Place combo and standalone
Place/PickPlace. They override the destination pose without requiring a matching
table. Fresh table observations can still supply optional collision geometry. Without an override, placement uses the
selected table's calibration. The combo ignores the standalone table dropdown.

After updating the action definitions, rebuild and restart manipulation and the
panel together; clients using the previous Pick/Place/PickPlace definitions must
also be rebuilt. Add measured table profiles and corresponding detector tags
before using new docking approaches for automatic placement.
