(() => {
  const state = {
    map: null,
    mapImage: null,
    presets: [],
    status: null,
    poseTrail: [],
    socket: null,
    authenticated: false,
    mapMode: "initial_pose",
    mapSelection: null,
    mapPointer: null,
  };
  const byId = (id) => document.getElementById(id);
  const canvas = byId("map-canvas");
  const context = canvas.getContext("2d");

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const contentType = response.headers.get("Content-Type") || "";
    const body = contentType.includes("application/json") ? await response.json() : null;
    if (response.status === 401 && path !== "/api/login") {
      returnToLogin("Your operator session expired. Sign in again.");
    }
    if (!response.ok) throw new Error(body?.error || `Request failed (${response.status})`);
    return body;
  }

  function setError(message) { byId("panel-error").textContent = message || ""; }
  function setLoginError(message) { byId("login-error").textContent = message || ""; }
  function mergeStatus(current, delta) {
    const mergeObject = (target, changes) => {
      const merged = target && typeof target === "object" && !Array.isArray(target) ? { ...target } : {};
      Object.entries(changes || {}).forEach(([key, value]) => {
        merged[key] = value && typeof value === "object" && !Array.isArray(value)
          ? mergeObject(merged[key], value)
          : value;
      });
      return merged;
    };
    const merged = mergeObject(current, delta?.set);
    (delta?.remove || []).forEach((path) => {
      let parent = merged;
      for (const key of path.slice(0, -1)) {
        if (!parent || typeof parent !== "object") return;
        parent = parent[key];
      }
      if (parent && typeof parent === "object") delete parent[path[path.length - 1]];
    });
    return merged;
  }
  function returnToLogin(message) {
    state.authenticated = false;
    if (state.socket) {
      state.socket.onclose = null;
      state.socket.close();
      state.socket = null;
    }
    byId("panel-view").hidden = true;
    byId("login-view").hidden = false;
    setLoginError(message);
  }
  function manualPlacePoseEnabled() { return byId("use-manual-place-pose").checked; }
  function finiteField(id) {
    const rawValue = byId(id).value.trim();
    if (!rawValue) throw new Error(`Enter a valid value for ${id.replace("place-", "")}`);
    const value = Number(rawValue);
    if (!Number.isFinite(value)) throw new Error(`Enter a valid value for ${id.replace("place-", "")}`);
    return value;
  }
  function placePose() {
    return {
      frame_id: byId("place-frame").value,
      x: finiteField("place-x"),
      y: finiteField("place-y"),
      z: finiteField("place-z"),
      yaw: finiteField("place-yaw"),
    };
  }
  function syncManualPlacePoseFields() {
    byId("manual-place-fields").disabled = !manualPlacePoseEnabled();
  }
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));
  }

  async function login(event) {
    event.preventDefault();
    setLoginError("");
    try {
      await api("/api/login", { method: "POST", body: JSON.stringify({ password: byId("password").value }) });
      byId("password").value = "";
      state.authenticated = true;
      byId("login-view").hidden = true;
      byId("panel-view").hidden = false;
      await loadPanel();
    } catch (error) { setLoginError(error.message); }
  }

  async function loadPanel() {
    try {
      const [map, presets, status] = await Promise.all([api("/api/map"), api("/api/presets"), api("/api/status")]);
      state.map = map;
      state.presets = presets.presets;
      await loadMapImage(map.image_url);
      renderPresets();
      applyStatus(status);
      connectStatusStream();
    } catch (error) { setError(error.message); }
  }

  function loadMapImage(url) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => { state.mapImage = image; canvas.width = state.map.width; canvas.height = state.map.height; drawMap(); resolve(); };
      image.onerror = () => reject(new Error("Could not load the local navigation map"));
      image.src = url;
    });
  }

  function mapPoint(x, y) {
    const dx = x - state.map.origin.x;
    const dy = y - state.map.origin.y;
    const cosine = Math.cos(state.map.origin.yaw);
    const sine = Math.sin(state.map.origin.yaw);
    const mapX = cosine * dx + sine * dy;
    const mapY = -sine * dx + cosine * dy;
    return { x: mapX / state.map.resolution, y: state.map.height - mapY / state.map.resolution };
  }

  function mapCoordinates(point) {
    const mapX = point.x * state.map.resolution;
    const mapY = (state.map.height - point.y) * state.map.resolution;
    const cosine = Math.cos(state.map.origin.yaw);
    const sine = Math.sin(state.map.origin.yaw);
    return {
      x: state.map.origin.x + cosine * mapX - sine * mapY,
      y: state.map.origin.y + sine * mapX + cosine * mapY,
    };
  }

  function canvasPoint(event) {
    const rectangle = canvas.getBoundingClientRect();
    return {
      x: (event.clientX - rectangle.left) * canvas.width / rectangle.width,
      y: (event.clientY - rectangle.top) * canvas.height / rectangle.height,
    };
  }

  function clampPointToMap(point, margin = 13) {
    return {
      x: Math.min(Math.max(point.x, margin), canvas.width - margin),
      y: Math.min(Math.max(point.y, margin), canvas.height - margin),
    };
  }

  function pointIsOnMap(point) {
    return point.x >= 0 && point.x <= canvas.width && point.y >= 0 && point.y <= canvas.height;
  }

  function drawRobotMarker(pose) {
    const actualPoint = mapPoint(pose.x, pose.y);
    const onMap = pointIsOnMap(actualPoint);
    const point = onMap ? actualPoint : clampPointToMap(actualPoint);
    context.save();
    context.translate(point.x, point.y);
    context.rotate(-(pose.yaw - state.map.origin.yaw));
    context.fillStyle = pose.fresh ? "#116b83" : "#b67316";
    context.beginPath();
    context.moveTo(13, 0);
    context.lineTo(-9, -8);
    context.lineTo(-5, 0);
    context.lineTo(-9, 8);
    context.closePath();
    context.fill();
    context.strokeStyle = "#fff";
    context.lineWidth = 2;
    context.stroke();
    if (!onMap) {
      context.strokeStyle = "#7b4b08";
      context.lineWidth = 2;
      context.strokeRect(-11, -11, 22, 22);
    }
    context.restore();
  }

  function drawBoxMarker(boxPose) {
    const actualPoint = mapPoint(boxPose.x, boxPose.y);
    const onMap = pointIsOnMap(actualPoint);
    const point = onMap ? actualPoint : clampPointToMap(actualPoint, 9);
    context.save();
    context.translate(point.x, point.y);
    context.rotate(Math.PI / 4);
    context.fillStyle = boxPose.fresh ? "#a63e50" : "#b67316";
    context.fillRect(-6, -6, 12, 12);
    context.strokeStyle = "#fff";
    context.lineWidth = 2;
    context.strokeRect(-6, -6, 12, 12);
    if (!onMap) {
      context.strokeStyle = "#7b4b08";
      context.lineWidth = 2;
      context.strokeRect(-9, -9, 18, 18);
    }
    context.restore();
  }

  function drawTargetMarker(target, color, fill = false) {
    const point = mapPoint(target.x, target.y);
    if (!pointIsOnMap(point)) return;
    context.save();
    context.translate(point.x, point.y);
    context.rotate(-(target.yaw - state.map.origin.yaw));
    context.strokeStyle = color;
    context.fillStyle = color;
    context.lineWidth = 2;
    context.beginPath();
    context.moveTo(14, 0);
    context.lineTo(-8, -8);
    context.lineTo(-4, 0);
    context.lineTo(-8, 8);
    context.closePath();
    if (fill) context.fill(); else context.stroke();
    context.beginPath();
    context.arc(0, 0, 5, 0, Math.PI * 2);
    if (fill) context.fill(); else context.stroke();
    context.restore();
  }

  function drawLaserScan(scan) {
    if (!byId("show-scan").checked || !scan?.available || !Array.isArray(scan.points)) return;
    context.fillStyle = scan.fresh ? "rgba(57, 138, 184, .68)" : "rgba(182, 115, 22, .42)";
    scan.points.forEach(([x, y]) => {
      const point = mapPoint(x, y);
      if (pointIsOnMap(point)) context.fillRect(point.x - 1, point.y - 1, 2, 2);
    });
  }

  function drawGlobalPath(globalPath) {
    if (!globalPath?.available || !Array.isArray(globalPath.points) || globalPath.points.length < 2) return;
    context.save();
    context.strokeStyle = globalPath.fresh ? "rgba(49, 95, 142, .88)" : "rgba(182, 115, 22, .52)";
    context.lineWidth = 3;
    context.lineJoin = "round";
    context.lineCap = "round";
    let drawing = false;
    context.beginPath();
    globalPath.points.forEach(([x, y]) => {
      const point = mapPoint(x, y);
      if (!pointIsOnMap(point)) { drawing = false; return; }
      if (drawing) context.lineTo(point.x, point.y); else context.moveTo(point.x, point.y);
      drawing = true;
    });
    context.stroke();
    context.restore();
  }

  function currentMapSelection() {
    if (!state.mapPointer) return state.mapSelection;
    const start = mapCoordinates(state.mapPointer.start);
    const end = mapCoordinates(state.mapPointer.current);
    const distance = Math.hypot(end.x - start.x, end.y - start.y);
    return {
      kind: state.mapMode,
      x: start.x,
      y: start.y,
      yaw: distance > 0.03 ? Math.atan2(end.y - start.y, end.x - start.x) : 0,
    };
  }

  function drawMap() {
    if (!state.map || !state.mapImage) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(state.mapImage, 0, 0);
    const activeNavigation = state.status?.operations?.find((operation) => operation.kind === "navigate" && ["SUBMITTING", "ACTIVE", "CANCEL_REQUESTED"].includes(operation.status));
    drawGlobalPath(state.status?.navigation?.global_path);
    drawLaserScan(state.status?.scan);
    state.presets.forEach((preset) => {
      const point = mapPoint(preset.pose.x, preset.pose.y);
      context.fillStyle = activeNavigation?.preset_id === preset.id ? "#b67316" : "#2a8b51";
      context.beginPath(); context.arc(point.x, point.y, 6, 0, Math.PI * 2); context.fill();
      context.fillStyle = "#18383a"; context.font = "14px system-ui"; context.fillText(preset.label, point.x + 9, point.y - 8);
    });
    if (state.poseTrail.length > 1) {
      context.strokeStyle = "rgba(17, 107, 131, .48)"; context.lineWidth = 2; context.beginPath();
      state.poseTrail.forEach((pose, index) => { const point = mapPoint(pose.x, pose.y); if (index === 0) context.moveTo(point.x, point.y); else context.lineTo(point.x, point.y); });
      context.stroke();
    }
    const pose = state.status?.map_pose;
    const boxPose = state.status?.box_map_pose;
    if (activeNavigation?.target_pose) drawTargetMarker(activeNavigation.target_pose, "#b67316", true);
    const selection = currentMapSelection();
    if (selection) drawTargetMarker(selection, selection.kind === "initial_pose" ? "#75529a" : "#2a8b51");
    if (pose?.available) drawRobotMarker(pose);
    if (boxPose?.available) drawBoxMarker(boxPose);
  }

  function applyStatus(status) {
    state.status = status;
    addPoseToTrail(status.map_pose);
    renderStatus();
    drawMap();
  }

  function addPoseToTrail(pose) {
    if (!pose?.available || !pose.fresh) return;
    const previous = state.poseTrail[state.poseTrail.length - 1];
    if (!previous || Math.hypot(previous.x - pose.x, previous.y - pose.y) >= 0.02) {
      state.poseTrail.push({ x: pose.x, y: pose.y });
      if (state.poseTrail.length > 250) state.poseTrail.shift();
    }
  }

  function renderStatus() {
    const status = state.status;
    if (!status) return;
    const pose = status.map_pose;
    const boxPose = status.box_map_pose;
    byId("connection-status").textContent = "ROS gateway connected";
    byId("manipulation-state").textContent = status.manipulation_state.state;
    byId("localization-state").textContent = pose.fresh ? "Map pose current" : (pose.detail || "Unavailable");
    byId("box-pose-state").textContent = boxPose?.available ? (boxPose.fresh ? "Map position current" : boxPose.detail) : (boxPose?.detail || "Unavailable");
    const metrics = status.localization_metrics || {};
    const confidence = metrics.confidence;
    const delay = metrics.delay_ms;
    byId("localization-confidence").textContent = confidence?.fresh ? confidence.value.toFixed(3) : (confidence?.detail || "Waiting");
    byId("localization-delay").textContent = delay?.fresh ? `${delay.value.toFixed(1)} ms` : (delay?.detail || "Waiting");
    byId("pick-server").textContent = status.servers.pick ? "Ready" : "Unavailable";
    byId("place-server").textContent = status.servers.place ? "Ready" : "Unavailable";
    byId("navigate-server").textContent = status.servers.navigate ? "Ready" : "Unavailable";
    byId("fine-align-server").textContent = status.servers.fine_align ? "Ready" : "Unavailable";
    byId("undock-server").textContent = status.servers.undock ? "Ready" : "Unavailable";
    const navigation = status.navigation || {};
    const lifecycle = Object.values(navigation.lifecycle || {});
    const activeNodes = lifecycle.filter((node) => node.state_id === 3).length;
    byId("nav2-lifecycle").textContent = lifecycle.length ? `${activeNodes}/${lifecycle.length} active` : "Waiting";
    const goalStatus = navigation.goal_status;
    byId("nav2-goal-state").textContent = goalStatus?.available ? (goalStatus.active ? "Active" : "Idle") : (goalStatus?.detail || "Waiting");
    byId("nav2-odom-state").textContent = navigation.odom?.fresh ? "Current" : (navigation.odom?.detail || "Waiting");
    const costmapServices = navigation.costmap_clear_services || {};
    const clearCostmapsButton = byId("clear-costmaps");
    clearCostmapsButton.disabled = !(costmapServices.global && costmapServices.local);
    clearCostmapsButton.title = clearCostmapsButton.disabled ? "Costmap clear services are unavailable" : "Clear both Nav2 costmaps";
    const dockingMotionActive = (status.operations || []).some((operation) =>
      ["fine_align", "undock"].includes(operation.kind) && ["SUBMITTING", "ACTIVE"].includes(operation.status));
    const cancelDockingMotionButton = byId("cancel-docking-motion");
    cancelDockingMotionButton.disabled = !dockingMotionActive;
    cancelDockingMotionButton.title = dockingMotionActive ? "Cancel the active docking motion" : "No active docking motion";
    const globalPath = navigation.global_path;
    byId("global-path-state").textContent = globalPath?.fresh ? (globalPath.point_count ? `${globalPath.point_count} poses` : "No path") : (globalPath?.detail || "Waiting");
    const scan = status.scan;
    byId("scan-state").textContent = scan?.fresh ? `${scan.point_count} points` : (scan?.detail || "Waiting");
    const moveit = status.moveit || {};
    byId("move-group-state").textContent = moveit.move_group_action_ready ? "Ready" : "Unavailable";
    byId("planning-scene-state").textContent = moveit.planning_scene_service_ready ? "Ready" : "Unavailable";
    byId("joint-states-state").textContent = moveit.joint_states?.fresh ? "Current" : (moveit.joint_states?.detail || "Waiting");
    byId("map-pose-status").textContent = pose.fresh ? "Live map-frame position" : (pose.detail || "Localization unavailable");
    byId("map-coordinates").textContent = pose.available ? `x ${pose.x.toFixed(2)}  y ${pose.y.toFixed(2)}  yaw ${pose.yaw.toFixed(2)}` : "--";
    const unlock = status.execution_unlock_remaining_sec || 0;
    const badge = byId("execution-state");
    badge.textContent = unlock > 0 ? `Unlocked ${Math.ceil(unlock)}s` : "Plan only";
    badge.classList.toggle("unlocked", unlock > 0);
    renderDiagnostics(status.diagnostics);
    renderOperations(status.operations);
    renderAudit(status.audit);
    renderMapCommand();
  }

  function renderMapCommand() {
    const selection = currentMapSelection();
    const status = state.status?.initial_pose;
    let text = "No map command selected";
    if (selection) {
      const label = selection.kind === "initial_pose" ? "Initial pose" : "Navigation goal";
      text = `${label}: x ${selection.x.toFixed(2)}  y ${selection.y.toFixed(2)}  yaw ${selection.yaw.toFixed(2)}`;
    } else if (status?.state === "PENDING" || status?.state === "TIMEOUT") {
      text = status.detail;
    }
    byId("map-command-status").textContent = text;
    byId("submit-map-command").disabled = !selection;
  }

  function renderDiagnostics(diagnostics) {
    byId("diagnostics").textContent = diagnostics?.length ? diagnostics.map((item) => `${item.name}: ${item.message}`).join("\n") : "No diagnostics received";
  }
  function formatPlanarError(error) {
    if (!error || ![error.x, error.y, error.yaw].every(Number.isFinite)) return "";
    return `error x ${error.x.toFixed(3)} m, y ${error.y.toFixed(3)} m, yaw ${error.yaw.toFixed(3)} rad`;
  }
  function formatUndockDistance(operation) {
    const traveled = operation.result?.distance_traveled ?? operation.feedback?.distance_traveled;
    if (!Number.isFinite(traveled)) return "";
    const parts = [`traveled ${traveled.toFixed(3)} m`];
    const remaining = operation.feedback?.distance_remaining;
    const commandedSpeed = operation.feedback?.commanded_speed;
    const commandedLateralSpeed = operation.feedback?.commanded_lateral_speed;
    const commandedYawSpeed = operation.feedback?.commanded_yaw_speed;
    if (Number.isFinite(remaining)) parts.push(`remaining ${remaining.toFixed(3)} m`);
    if (Number.isFinite(commandedSpeed)) parts.push(`command x ${commandedSpeed.toFixed(3)} m/s`);
    if (Number.isFinite(commandedLateralSpeed)) parts.push(`y ${commandedLateralSpeed.toFixed(3)} m/s`);
    if (Number.isFinite(commandedYawSpeed)) parts.push(`yaw ${commandedYawSpeed.toFixed(3)} rad/s`);
    return parts.join(", ");
  }
  function renderOperations(operations) {
    byId("operations").innerHTML = (operations || []).slice(0, 15).map((operation) => {
      const message = operation.result?.message || operation.result?.error_msg || operation.detail || "--";
      const planarError = formatPlanarError(operation.result?.final_error || operation.feedback?.current_error);
      const motionDetail = planarError || formatUndockDistance(operation);
      const detail = motionDetail ? `${message}; ${motionDetail}` : message;
      return `<tr><td>${escapeHtml(operation.kind)}</td><td>${escapeHtml(operation.status)}</td><td>${escapeHtml(operation.stage)}</td><td>${operation.progress == null ? "--" : `${Math.round(operation.progress * 100)}%`}</td><td>${escapeHtml(detail)}</td></tr>`;
    }).join("") || '<tr><td colspan="5">No panel operations</td></tr>';
  }
  function renderAudit(entries) {
    byId("audit-log").innerHTML = (entries || []).slice(0, 20).map((entry) => `<li><time>${escapeHtml(new Date(entry.timestamp).toLocaleTimeString())}</time><strong>${escapeHtml(entry.action)}</strong> ${escapeHtml(entry.outcome)} ${escapeHtml(entry.detail)}</li>`).join("") || "<li>No audit events</li>";
  }
  function renderPresets() {
    const list = byId("preset-list"); list.textContent = "";
    if (!state.presets.length) { list.textContent = "No configured destinations"; return; }
    state.presets.forEach((preset) => { const button = document.createElement("button"); button.type = "button"; button.textContent = preset.label; button.addEventListener("click", () => navigate(preset)); list.appendChild(button); });
  }

  function setMapMode(mode) {
    state.mapMode = mode;
    state.mapSelection = null;
    byId("select-initial-pose").classList.toggle("active", mode === "initial_pose");
    byId("select-navigation-goal").classList.toggle("active", mode === "navigate");
    renderMapCommand();
    drawMap();
  }

  function startMapSelection(event) {
    if (!state.map) return;
    canvas.focus();
    const point = canvasPoint(event);
    state.mapPointer = { id: event.pointerId, start: point, current: point };
    canvas.setPointerCapture(event.pointerId);
    event.preventDefault();
    renderMapCommand();
    drawMap();
  }

  function updateMapSelection(event) {
    if (!state.mapPointer || event.pointerId !== state.mapPointer.id) return;
    state.mapPointer.current = canvasPoint(event);
    renderMapCommand();
    drawMap();
  }

  function finishMapSelection(event) {
    if (!state.mapPointer || event.pointerId !== state.mapPointer.id) return;
    state.mapPointer.current = canvasPoint(event);
    state.mapSelection = currentMapSelection();
    state.mapPointer = null;
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    renderMapCommand();
    drawMap();
  }

  function clearMapSelection() {
    state.mapPointer = null;
    state.mapSelection = null;
    renderMapCommand();
    drawMap();
  }

  function confirmNav2IdleWithoutStatus() {
    if (state.status?.navigation?.goal_status?.available) return false;
    return window.confirm("Nav2 action status is unavailable. Verify Nav2 is idle before continuing.");
  }

  async function submitMapSelection() {
    const selection = state.mapSelection;
    if (!selection) return;
    const label = selection.kind === "initial_pose" ? "Set this initial pose?" : "Navigate to this map goal?";
    if (!window.confirm(label)) return;
    try {
      if (selection.kind === "initial_pose") {
        const confirmNav2Idle = confirmNav2IdleWithoutStatus();
        if (!state.status?.navigation?.goal_status?.available && !confirmNav2Idle) return;
        await api("/api/initial-pose", {
          method: "POST",
          body: JSON.stringify({
            x: selection.x,
            y: selection.y,
            yaw: selection.yaw,
            confirmed: true,
            confirm_nav2_idle: confirmNav2Idle,
          }),
        });
      } else {
        const confirmNav2Idle = confirmNav2IdleWithoutStatus();
        if (!state.status?.navigation?.goal_status?.available && !confirmNav2Idle) return;
        await api("/api/actions", {
          method: "POST",
          body: JSON.stringify({
            kind: "navigate",
            goal: { x: selection.x, y: selection.y, yaw: selection.yaw },
            confirmed: true,
            confirm_nav2_idle: confirmNav2Idle,
          }),
        });
      }
      clearMapSelection();
      setError("");
    } catch (error) { setError(error.message); }
  }

  function connectStatusStream() {
    if (!state.authenticated) return;
    if (state.socket) {
      state.socket.onclose = null;
      state.socket.close();
    }
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const fallbackUrl = `${scheme}://${window.location.hostname}:${window.X2_PANEL_CONFIG.websocketPort}`;
    const socket = new WebSocket(window.X2_PANEL_CONFIG.websocketUrl || fallbackUrl);
    state.socket = socket;
    socket.onopen = () => { byId("connection-status").textContent = "Live status connected"; };
    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === "status") applyStatus(message.payload);
        if (message.type === "status_delta") applyStatus(mergeStatus(state.status, message.payload));
      } catch (_) { setError("Received an invalid status update"); }
    };
    socket.onclose = (event) => {
      if (state.socket !== socket) return;
      state.socket = null;
      if (event.code === 1008) {
        returnToLogin("Your operator session expired. Sign in again.");
      } else if (state.authenticated) {
        byId("connection-status").textContent = "Reconnecting live status";
        window.setTimeout(connectStatusStream, 2000);
      }
    };
    socket.onerror = () => { socket.close(); };
  }

  async function submitManipulation(kind, extra = {}) {
    try {
      if (["place", "pick_place"].includes(kind) && manualPlacePoseEnabled() && !extra.place_pose) {
        extra = { ...extra, place_pose: placePose() };
      }
      const planOnly = kind === "reset" ? null : byId("plan-only").checked;
      const physical = kind === "reset" || !planOnly;
      if (physical && !window.confirm("Submit a physical manipulation command?")) return;
      const payload = { kind, ...extra };
      if (planOnly !== null) payload.plan_only = planOnly;
      if (physical) payload.confirmed = true;
      await api("/api/actions", { method: "POST", body: JSON.stringify(payload) });
      setError("");
    } catch (error) { setError(error.message); }
  }
  async function navigate(preset) {
    if (!window.confirm(`Navigate to ${preset.label}?`)) return;
    const confirmNav2Idle = confirmNav2IdleWithoutStatus();
    if (!state.status?.navigation?.goal_status?.available && !confirmNav2Idle) return;
    try { await api("/api/actions", { method: "POST", body: JSON.stringify({ kind: "navigate", preset_id: preset.id, confirmed: true, confirm_nav2_idle: confirmNav2Idle }) }); setError(""); } catch (error) { setError(error.message); }
  }
  async function fineAlign(execute) {
    if (execute && !window.confirm("Move the robot in x, y, and yaw to fine-align with the table?")) return;
    const confirmNav2Idle = confirmNav2IdleWithoutStatus();
    if (!state.status?.navigation?.goal_status?.available && !confirmNav2Idle) return;
    try {
      await api("/api/actions", {
        method: "POST",
        body: JSON.stringify({
          kind: "fine_align",
          execute,
          confirmed: execute,
          confirm_nav2_idle: confirmNav2Idle,
        }),
      });
      setError("");
    } catch (error) { setError(error.message); }
  }
  async function undock() {
    if (!window.confirm("Move the robot backward using the configured undocking profile?")) return;
    const confirmNav2Idle = confirmNav2IdleWithoutStatus();
    if (!state.status?.navigation?.goal_status?.available && !confirmNav2Idle) return;
    try {
      await api("/api/actions", {
        method: "POST",
        body: JSON.stringify({
          kind: "undock",
          confirmed: true,
          confirm_nav2_idle: confirmNav2Idle,
        }),
      });
      setError("");
    } catch (error) { setError(error.message); }
  }
  async function recoverState(requestedState) {
    if (!window.confirm(`Confirm manipulation state: ${requestedState}?`)) return;
    try { await api("/api/recover-state", { method: "POST", body: JSON.stringify({ requested_state: requestedState, confirmed: true }) }); setError(""); } catch (error) { setError(error.message); }
  }
  async function unlockExecution() {
    if (!window.confirm("Temporarily unlock one physical motion command?")) return;
    try { await api("/api/unlock/execution", { method: "POST", body: JSON.stringify({ confirmed: true }) }); setError(""); } catch (error) { setError(error.message); }
  }
  async function cancelActive() {
    if (!window.confirm("Request cancellation for all active panel goals?")) return;
    try { await api("/api/cancel", { method: "POST", body: "{}" }); setError(""); } catch (error) { setError(error.message); }
  }
  async function cancelDockingMotion() {
    if (!window.confirm("Cancel the active docking motion?")) return;
    try {
      await api("/api/docking/cancel", { method: "POST", body: "{}" });
      setError("");
    } catch (error) { setError(error.message); }
  }
  async function clearCostmaps() {
    if (!window.confirm("Clear both the global and local Nav2 costmaps?")) return;
    try {
      await api("/api/costmaps/clear", { method: "POST", body: JSON.stringify({ confirmed: true }) });
      setError("");
    } catch (error) { setError(error.message); }
  }

  byId("login-form").addEventListener("submit", login);
  document.querySelectorAll("[data-command]").forEach((button) => button.addEventListener("click", () => submitManipulation(button.dataset.command)));
  byId("use-manual-place-pose").addEventListener("change", syncManualPlacePoseFields);
  syncManualPlacePoseFields();
  byId("place-form").addEventListener("submit", (event) => { event.preventDefault(); submitManipulation("place"); });
  byId("reset-manipulation").addEventListener("click", () => submitManipulation("reset", { confirm_empty: true }));
  byId("recover-empty").addEventListener("click", () => recoverState("empty"));
  byId("recover-holding").addEventListener("click", () => recoverState("holding"));
  byId("unlock-execution").addEventListener("click", unlockExecution);
  byId("cancel-active").addEventListener("click", cancelActive);
  byId("cancel-docking-motion").addEventListener("click", cancelDockingMotion);
  byId("clear-costmaps").addEventListener("click", clearCostmaps);
  byId("check-fine-align").addEventListener("click", () => fineAlign(false));
  byId("execute-fine-align").addEventListener("click", () => fineAlign(true));
  byId("execute-undock").addEventListener("click", undock);
  byId("select-initial-pose").addEventListener("click", () => setMapMode("initial_pose"));
  byId("select-navigation-goal").addEventListener("click", () => setMapMode("navigate"));
  byId("show-scan").addEventListener("change", drawMap);
  byId("clear-map-command").addEventListener("click", clearMapSelection);
  byId("submit-map-command").addEventListener("click", submitMapSelection);
  canvas.addEventListener("pointerdown", startMapSelection);
  canvas.addEventListener("pointermove", updateMapSelection);
  canvas.addEventListener("pointerup", finishMapSelection);
  canvas.addEventListener("pointercancel", clearMapSelection);
})();
