(() => {
  const state = {
    map: null,
    mapImage: null,
    presets: [],
    destinationCatalog: { available: false, detail: "Loading destinations…" },
    destinationDraft: null,
    destinationSaving: false,
    destinationLoadSequence: 0,
    status: null,
    poseTrail: [],
    socket: null,
    authenticated: false,
    mapMode: "initial_pose",
    mapSelection: null,
    mapPointer: null,
    cameraPollTimer: null,
    selectedBoxId: null,
    guidedWorkflow: null,
    guidedSubmitting: false,
    taskShortcuts: { available: false, shortcuts: [], detail: "Loading shortcuts…" },
    selectedShortcuts: { pick: null, place: null },
    shortcutDraft: null,
    shortcutSaving: false,
    statusConnected: false,
    connectionGeneration: 0,
    continueRequest: null,
    executionUnlockDeadline: null,
    executionUnlockKnown: false,
    executionTimer: null,
    savedPlans: new Map(),
    savedOperationEvents: new Set(),
    savedContext: null,
  };
  const cameraStreams = [
    { endpoint: "/api/cameras/front-center", imageId: "front-center-image", statusId: "front-center-camera-status", etag: null, objectUrl: null, inFlight: false },
    { endpoint: "/api/cameras/throttled", imageId: "throttled-image", statusId: "throttled-camera-status", etag: null, objectUrl: null, inFlight: false },
  ];
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
    if (["/api/actions", "/api/posture"].includes(path) &&
        (body?.operation?.plan_only === false || body?.operation?.kind === "reset")) {
      syncExecutionUnlock(0);
    }
    if (options.method === "POST" && ["/api/recover-state", "/api/posture", "/api/box-profiles/reload"].includes(path)) {
      const dryRun = path === "/api/box-profiles/reload" && JSON.parse(options.body || "{}").dry_run;
      if (!dryRun) {state.savedPlans.clear(); renderSavedPlans();}
    }
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
    if (state.guidedWorkflow?.shortcut) pauseShortcutConnection();
    state.statusConnected = false;
    if (state.guidedWorkflow) state.guidedWorkflow.cancelRequested = true;
    state.authenticated = false;
    invalidateExecutionUnlock();
    stopCameraStreams();
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
    byId("manual-place-fields").hidden = !manualPlacePoseEnabled();
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
      state.destinationCatalog = presets;
      await loadMapImage(map.image_url);
      renderPresets();
      applyStatus(status);
      connectStatusStream();
      await loadTaskShortcuts();
      startCameraStreams();
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

  function stopCameraStreams() {
    if (state.cameraPollTimer !== null) {
      window.clearInterval(state.cameraPollTimer);
      state.cameraPollTimer = null;
    }
    cameraStreams.forEach((stream) => { stream.inFlight = false; stream.etag = null; });
  }

  function cameraPreviewsEnabled() {
    return byId("show-camera-previews").checked;
  }

  async function refreshCameraStream(stream) {
    if (!state.authenticated || stream.inFlight) return;
    stream.inFlight = true;
    try {
      const headers = stream.etag ? { "If-None-Match": stream.etag } : {};
      const response = await fetch(stream.endpoint, { credentials: "same-origin", headers });
      if (response.status === 401) {
        returnToLogin("Your operator session expired. Sign in again.");
        return;
      }
      if (response.status === 304) return;
      if (response.status === 204) {
        byId(stream.statusId).textContent = "Waiting for image";
        return;
      }
      if (!response.ok) throw new Error(`Preview request failed (${response.status})`);
      const image = byId(stream.imageId);
      const previousUrl = stream.objectUrl;
      stream.objectUrl = URL.createObjectURL(await response.blob());
      stream.etag = response.headers.get("ETag");
      image.onload = () => {
        if (previousUrl) URL.revokeObjectURL(previousUrl);
        byId(stream.statusId).textContent = "Live";
      };
      image.onerror = () => {
        URL.revokeObjectURL(stream.objectUrl);
        stream.objectUrl = null;
        byId(stream.statusId).textContent = "Could not display image";
      };
      image.src = stream.objectUrl;
    } catch (_) {
      if (state.authenticated) byId(stream.statusId).textContent = "Preview unavailable";
    } finally {
      stream.inFlight = false;
    }
  }

  function startCameraStreams() {
    stopCameraStreams();
    if (!cameraPreviewsEnabled()) {
      byId("camera-refresh-rate").textContent = "Previews paused";
      return;
    }
    const refreshPeriodMs = Math.max(1, Number(window.X2_PANEL_CONFIG.cameraRefreshPeriodMs) || 1000);
    byId("camera-refresh-rate").textContent = `At most ${(1000 / refreshPeriodMs).toFixed(1)} frame/s per preview`;
    cameraStreams.forEach(refreshCameraStream);
    state.cameraPollTimer = window.setInterval(
      () => cameraStreams.forEach(refreshCameraStream), refreshPeriodMs
    );
  }

  function toggleCameraPreviews() {
    const enabled = cameraPreviewsEnabled();
    byId("camera-grid").hidden = !enabled;
    if (enabled) {
      startCameraStreams();
    } else {
      stopCameraStreams();
      byId("camera-refresh-rate").textContent = "Previews paused";
    }
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

  function drawBoxMarker(boxPose, selected = false) {
    const actualPoint = mapPoint(boxPose.x, boxPose.y);
    const onMap = pointIsOnMap(actualPoint);
    const point = onMap ? actualPoint : clampPointToMap(actualPoint, 9);
    context.save();
    context.translate(point.x, point.y);
    context.rotate(Math.PI / 4);
    context.fillStyle = selected ? "#75529a" : (boxPose.fresh ? "#a63e50" : "#b67316");
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
    const visibleBoxPoses = state.status?.box_map_poses;
    if (activeNavigation?.target_pose) drawTargetMarker(activeNavigation.target_pose, "#b67316", true);
    const selection = currentMapSelection();
    if (selection) drawTargetMarker(selection, selection.kind === "initial_pose" ? "#75529a" : "#2a8b51");
    if (pose?.available) drawRobotMarker(pose);
    if (Array.isArray(visibleBoxPoses) && visibleBoxPoses.length) {
      visibleBoxPoses.forEach((visibleBox) => {
        if (visibleBox.available) drawBoxMarker(visibleBox, visibleBox.instance_id === state.selectedBoxId);
      });
    } else if (boxPose?.available) {
      drawBoxMarker(boxPose);
    }
  }

  function executionUnlockRemaining() {
    return state.executionUnlockKnown && state.executionUnlockDeadline !== null
      ? Math.max(0, (state.executionUnlockDeadline - performance.now()) / 1000) : 0;
  }

  function renderExecutionState() {
    const remaining = executionUnlockRemaining();
    const badge = byId("execution-state");
    badge.textContent = !state.executionUnlockKnown ? "Status unavailable"
      : remaining > 0 ? `Unlocked ${Math.ceil(remaining)}s`
        : byId("plan-only").checked ? "Plan only" : "Locked";
    badge.classList.toggle("unlocked", remaining > 0);
    badge.title = remaining > 0 ? "One physical command may consume this timed unlock."
      : "Physical commands require an execution unlock.";
  }

  function syncExecutionUnlock(remaining) {
    state.executionUnlockKnown = Number.isFinite(remaining) && remaining >= 0;
    state.executionUnlockDeadline = state.executionUnlockKnown ? performance.now() + remaining * 1000 : null;
    renderExecutionState();
    renderSavedPlans();
    renderGuidedWorkflow();
    if (!(executionUnlockRemaining() > 0) && state.executionTimer !== null) {
      window.clearInterval(state.executionTimer);
      state.executionTimer = null;
    }
    if (state.authenticated && executionUnlockRemaining() > 0 && state.executionTimer === null) {
      state.executionTimer = window.setInterval(() => {
        renderExecutionState();
        renderSavedPlans();
        renderGuidedWorkflow();
        if (executionUnlockRemaining() <= 0) {
          window.clearInterval(state.executionTimer);
          state.executionTimer = null;
        }
      }, 200);
    }
  }

  function invalidateExecutionUnlock() {
    if (state.executionTimer !== null) window.clearInterval(state.executionTimer);
    state.executionTimer = null;
    state.executionUnlockKnown = false;
    state.executionUnlockDeadline = null;
    state.savedPlans.clear();
    renderExecutionState();
    renderSavedPlans();
    renderGuidedWorkflow();
  }

  function applyStatus(status, unlockChanged = true) {
    state.status = status;
    if (unlockChanged) syncExecutionUnlock(status.execution_unlock_remaining_sec);
    updateGuidedWorkflow();
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
    renderManipulationTask(status.manipulation_task || {});
    byId("localization-state").textContent = pose.fresh ? "Map pose current" : (pose.detail || "Unavailable");
    byId("box-pose-state").textContent = boxPose?.available ? (boxPose.fresh ? "Map position current" : boxPose.detail) : (boxPose?.detail || "Unavailable");
    const visibleBoxes = status.visible_boxes;
    byId("visible-box-count").textContent = visibleBoxes?.fresh
      ? `${visibleBoxes.box_count} fresh`
      : (visibleBoxes?.detail || "Waiting");
    renderVisibleBoxes(visibleBoxes);
    renderDockingProfiles();
    renderTableProfiles();
    renderGuidedWorkflow();
    const metrics = status.localization_metrics || {};
    const confidence = metrics.confidence;
    const delay = metrics.delay_ms;
    byId("localization-confidence").textContent = confidence?.fresh ? confidence.value.toFixed(3) : (confidence?.detail || "Waiting");
    byId("localization-delay").textContent = delay?.fresh ? `${delay.value.toFixed(1)} ms` : (delay?.detail || "Waiting");
    byId("pick-server").textContent = status.servers.pick ? "Ready" : "Unavailable";
    byId("place-server").textContent = status.servers.place ? "Ready" : "Unavailable";
    byId("carry-pose-server").textContent = status.servers.move_carry_pose ? "Ready" : "Unavailable";
    const carryPoseReady = status.servers.move_carry_pose && status.manipulation_state.state === "HOLDING";
    const carryPoseDetail = !status.servers.move_carry_pose
      ? "Carry-pose action server is unavailable"
      : "Carry-pose transitions require manipulation state HOLDING";
    [byId("move-carry-a"), byId("move-carry-b")].forEach((button) => {
      button.disabled = !carryPoseReady;
      button.title = carryPoseReady ? "Plan or move the held box to this carry pose" : carryPoseDetail;
    });
    const posture = status.locomanipulation_posture || {};
    const postureButton = byId("set-posture");
    postureButton.disabled = !posture.ready;
    postureButton.title = posture.detail || "Locomanipulation posture service unavailable";
    const resetPostureButton = byId("reset-posture");
    resetPostureButton.disabled = !posture.ready;
    resetPostureButton.title = posture.detail || "Locomanipulation posture service unavailable";
    const releasePostureButton = byId("release-posture");
    releasePostureButton.disabled = !posture.release_ready;
    releasePostureButton.title = posture.release_detail || "Locomanipulation posture release unavailable";
    byId("posture-status").textContent = posture.detail || "Waiting for posture service";
    const profileReload = status.box_profiles_reload || {};
    const reloadProfilesButton = byId("reload-box-profiles");
    reloadProfilesButton.disabled = !profileReload.ready;
    reloadProfilesButton.title = profileReload.detail || "Box-profile reload unavailable";
    byId("box-profiles-file").textContent = profileReload.profiles_file || "No catalog configured";
    byId("box-profiles-file").title = profileReload.profiles_file || "";
    byId("box-profiles-reload-status").textContent = profileReload.detail || "Waiting for reload service";
    byId("navigate-server").textContent = status.servers.navigate ? "Ready" : "Unavailable";
    byId("fine-align-server").textContent = status.servers.fine_align ? "Ready" : "Unavailable";
    byId("undock-server").textContent = status.servers.undock ? "Ready" : "Unavailable";
    const navigation = status.navigation || {};
    const lifecycle = Object.values(navigation.lifecycle || {});
    const activeNodes = lifecycle.filter((node) => node.state_id === 3).length;
    byId("nav2-lifecycle").textContent = lifecycle.length ? `${activeNodes}/${lifecycle.length} active` : "Waiting";
    const goalStatus = navigation.goal_status;
    byId("nav2-goal-state").textContent = goalStatus?.available ? (goalStatus.active ? "Active" : "Idle") : (goalStatus?.detail || "Waiting");
    byId("nav2-goal-state").title = Object.entries(goalStatus?.actions || {}).map(([name, action]) => {
      const value = action.available ? (action.active ? "Active" : "Idle") : (action.connected ? "Unknown" : "Unavailable");
      const age = action.age_sec == null ? "no status received" : `last status ${Math.floor(action.age_sec)}s ago`;
      return `${name}: ${value}; ${age}`;
    }).join("\n");
    byId("nav2-odom-state").textContent = navigation.odom?.fresh ? "Current" : (navigation.odom?.detail || "Waiting");
    const costmapServices = navigation.costmap_clear_services || {};
    const clearCostmapsButton = byId("clear-costmaps");
    clearCostmapsButton.disabled = status.task_admission?.blocked || !(costmapServices.global && costmapServices.local);
    clearCostmapsButton.title = status.task_admission?.blocked ? status.task_admission.detail :
      clearCostmapsButton.disabled ? "Costmap clear services are unavailable" : "Clear both Nav2 costmaps";
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
    renderExecutionState();
    renderDiagnostics(status.diagnostics);
    renderOperations(status.operations);
    renderAudit(status.audit);
    renderMapCommand();
  }

  function renderVisibleBoxes(visibleBoxes) {
    const boxes = Array.isArray(visibleBoxes?.boxes) ? visibleBoxes.boxes : [];
    const selector = byId("visible-box-select");
    const previousSelection = state.selectedBoxId;
    const selectedStillVisible = boxes.some((box) => box.instance_id === previousSelection);
    if (!selectedStillVisible) {
      state.selectedBoxId = boxes.length === 1 ? boxes[0].instance_id : null;
    }
    selector.textContent = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = boxes.length ? "Select a visible box" : "No fresh visible boxes";
    selector.appendChild(placeholder);
    boxes.forEach((box) => {
      const option = document.createElement("option");
      option.value = box.instance_id;
      const age = Number.isFinite(box.age_sec) ? `, ${box.age_sec.toFixed(1)} s` : "";
      option.textContent = `${box.instance_id} — ${box.profile_id}${age}`;
      selector.appendChild(option);
    });
    selector.disabled = boxes.length === 0;
    selector.value = state.selectedBoxId || "";
    const selected = boxes.find((box) => box.instance_id === state.selectedBoxId);
    byId("visible-box-status").textContent = selected
      ? `Selected ${selected.instance_id} (${selected.profile_id})`
      : (visibleBoxes?.detail || "Select a fresh visible box before picking");
    const pickReady = Boolean(selected);
    document.querySelectorAll('[data-command="pick"], [data-command="pick_place"]').forEach((button) => {
      button.disabled = !pickReady;
      button.title = pickReady ? `Pick ${selected.instance_id}` : "Select a fresh visible box first";
    });
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
    byId("submit-map-command").classList.toggle("navigation", selection?.kind === "navigate");
    byId("submit-map-command").classList.toggle("secondary", selection?.kind !== "navigate");
  }

  function renderDiagnostics(diagnostics) {
    byId("diagnostics").textContent = diagnostics?.length ? diagnostics.map((item) => `${item.name}: ${item.message}`).join("\n") : "No diagnostics received";
  }
  function formatPlanarError(error, available = true) {
    if (!error) return "";
    if (!available || ![error.x, error.y, error.yaw].every(Number.isFinite)) return "error —";
    return `error x ${error.x.toFixed(3)} m, y ${error.y.toFixed(3)} m, yaw ${error.yaw.toFixed(3)} rad`;
  }
  function formatUndockDistance(operation) {
    const traveled = operation.result?.distance_traveled ?? operation.feedback?.distance_traveled;
    if (!Number.isFinite(traveled)) return "";
    const mode = operation.result?.undock_mode || operation.feedback?.undock_mode;
    const elapsed = operation.result?.elapsed_time ?? operation.feedback?.elapsed_time;
    const parts = [`${mode === "timed_reverse" ? "estimated travel" : "traveled"} ${traveled.toFixed(3)} m`];
    if (mode) parts.push(mode === "timed_reverse" ? "timed reverse" : "tag relative");
    if (Number.isFinite(elapsed)) parts.push(`${elapsed.toFixed(1)} s elapsed`);
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
  function renderSavedPlans() {
    const select = byId("saved-plan-select");
    const button = byId("execute-saved-plan");
    if (!select || !button) return;
    const previous = select.value;
    select.innerHTML = Array.from(state.savedPlans.values()).map((plan) => {
      const p = plan.result.achieved_pose;
      const target = p ? ` → (${p.x.toFixed(3)}, ${p.y.toFixed(3)}, ${p.z.toFixed(3)})` : "";
      return `<option value="${escapeHtml(plan.result.plan_id)}">${escapeHtml(plan.kind + " / " + plan.result.planning_mode + (plan.result.table_profile_id ? " / table " + plan.result.table_profile_id : "") + (plan.detail ? " / " + plan.detail : "") + target)}</option>`;
    }).join("") || '<option value="">Run Plan only to save a complete action</option>';
    if (Array.from(select.options).some((option) => option.value === previous)) select.value = previous;
    const busy = state.status?.task_admission?.blocked || state.status?.navigation?.goal_status?.active ||
      ["running", "retrying", "paused"].includes(state.status?.manipulation_task?.status) ||
      (state.status?.operations || []).some((operation) => ["SUBMITTING", "ACTIVE", "CANCEL_REQUESTED"].includes(operation.status));
    button.disabled = !select.value || busy || !(executionUnlockRemaining() > 0);
  }

  async function executeSavedPlan() {
    const id = byId("saved-plan-select").value;
    const plan = Array.from(state.savedPlans.values()).find((item) => item.result.plan_id === id);
    if (!plan) return;
    if (!window.confirm(`Execute saved ${plan.kind} plan (${plan.result.planning_mode}) with its stored targets?`)) return;
    try {
      await api("/api/actions", {method: "POST", body: JSON.stringify({kind: plan.kind,
        plan_only: false, plan_id: id, table_profile_id: plan.result.table_profile_id || "", confirmed: true})});
      state.savedPlans.clear();
      renderSavedPlans();
      setError("");
    } catch (error) {setError(error.message);}
  }

  function renderOperations(operations) {
    const posture = state.status?.locomanipulation_posture?.status;
    const context = JSON.stringify([state.status?.manipulation_state?.state,
      posture?.target_height, posture?.target_waist_yaw]);
    if (state.savedContext != null && state.savedContext !== context) state.savedPlans.clear();
    state.savedContext = context;
    for (const kind of state.savedPlans.keys()) {
      if (state.status?.servers?.[kind] === false) state.savedPlans.delete(kind);
    }
    [...(operations || [])].reverse().forEach((operation) => {
      const event = `${operation.id}/${operation.status}`;
      if (state.savedOperationEvents.has(event)) return;
      state.savedOperationEvents.add(event);
      if (operation.plan_only === false || ["reset", "recover_state", "reload_box_profiles", "set_locomanipulation_posture", "navigate", "fine_align", "undock"].includes(operation.kind)) {
        state.savedPlans.clear();
      }
      if (operation.plan_only === true && operation.status === "SUCCEEDED" &&
          operation.result?.success && operation.result?.plan_id) {
        state.savedPlans.set(operation.kind, operation);
      }
    });
    renderSavedPlans();
    byId("operations").innerHTML = (operations || []).slice(0, 15).map((operation) => {
      const message = operation.result?.message || operation.result?.error_msg || operation.detail || "--";
      const planarError = formatPlanarError(
        operation.result?.final_error || operation.feedback?.current_error,
        operation.result?.final_error != null || operation.feedback?.tag_visible !== false);
      const motionDetail = planarError || formatUndockDistance(operation);
      const profileId = operation.result?.profile_id || operation.feedback?.profile_id || operation.profile_id;
      const profileDetail = ["fine_align", "undock"].includes(operation.kind)
        ? `Profile: ${profileId || (operation.kind === "undock" ? "last successful dock / server default" : "server default")}` : "";
      const tableId = operation.result?.table_profile_id || operation.feedback?.table_profile_id || operation.table_profile_id;
      const tableDetail = ["pick", "place", "pick_place"].includes(operation.kind)
        ? `Table: ${tableId || "server default"}` : "";
      const detail = [message, profileDetail, tableDetail, motionDetail].filter(Boolean).join("; ");
      return `<tr><td>${escapeHtml(operation.kind)}</td><td>${escapeHtml(operation.status)}</td><td>${escapeHtml(operation.stage)}</td><td>${operation.progress == null ? "--" : `${Math.round(operation.progress * 100)}%`}</td><td>${escapeHtml(detail)}</td></tr>`;
    }).join("") || '<tr><td colspan="5">No panel operations</td></tr>';
  }
  function renderAudit(entries) {
    byId("audit-log").innerHTML = (entries || []).slice(0, 20).map((entry) => `<li><time>${escapeHtml(new Date(entry.timestamp).toLocaleTimeString())}</time><strong>${escapeHtml(entry.action)}</strong> ${escapeHtml(entry.outcome)} ${escapeHtml(entry.detail)}</li>`).join("") || "<li>No audit events</li>";
  }
  function renderPresets() {
    const list = byId("preset-list"); list.textContent = "";
    if (!state.presets.length) list.textContent = state.destinationCatalog.available ? "No configured destinations" : state.destinationCatalog.detail;
    state.presets.forEach((preset) => { const button = document.createElement("button"); button.type = "button"; button.className = "navigation"; button.textContent = preset.label; button.addEventListener("click", () => navigate(preset)); list.appendChild(button); });
    const select = byId("destination-select"), selected = select.value;
    const prompt = document.createElement("option"); prompt.value = ""; prompt.textContent = "Choose a destination";
    select.replaceChildren(prompt, ...state.presets.map((preset) => {
      const option = document.createElement("option"); option.value = preset.id;
      option.textContent = preset.label; return option;
    }));
    select.value = state.presets.some((item) => item.id === selected) ? selected : "";
    renderDestinationControls();
  }

  function renderDestinationControls() {
    const available = state.destinationCatalog.available && !state.destinationSaving;
    const selected = state.presets.some((item) => item.id === byId("destination-select").value);
    for (const action of ["edit", "duplicate", "delete"]) byId(`destination-${action}`).disabled = !available || !selected;
    byId("destination-new").disabled = !available;
    byId("destination-refresh").disabled = state.destinationSaving;
    byId("destination-select").disabled = state.destinationSaving;
    byId("destination-fields").disabled = state.destinationSaving;
    byId("destination-save").disabled = !available || !state.destinationDraft;
    byId("destination-status").textContent = state.destinationCatalog.available
      ? `${state.presets.length} saved destination(s).` : state.destinationCatalog.detail;
  }

  function applyDestinations(catalog) {
    state.destinationCatalog = catalog;
    state.presets = catalog.presets;
    renderPresets();
    renderTaskShortcuts();
    drawMap();
  }

  async function loadDestinations() {
    const sequence = ++state.destinationLoadSequence;
    try {
      const catalog = await api("/api/presets");
      if (sequence === state.destinationLoadSequence && !state.destinationSaving) applyDestinations(catalog);
    } catch (error) {
      if (sequence === state.destinationLoadSequence && !state.destinationSaving) {
        applyDestinations({ available: false, presets: [], detail: error.message });
        setError(error.message);
      }
    }
  }

  function editDestination(mode) {
    if (state.destinationSaving || !state.destinationCatalog.available) return;
    const selected = state.presets.find((item) => item.id === byId("destination-select").value);
    if (mode !== "new" && !selected) return;
    const draft = mode === "new" ? { label: "", pose: { x: 0, y: 0, yaw: 0 } }
      : JSON.parse(JSON.stringify(selected));
    if (mode === "duplicate") { delete draft.id; draft.label = `${draft.label} copy`.slice(0, 80); delete draft.revision; }
    state.destinationDraft = draft;
    byId("destination-name").value = draft.label;
    for (const key of ["x", "y", "yaw"]) byId(`destination-${key}`).value = draft.pose[key];
    byId("destination-editor").hidden = false;
    renderDestinationControls();
  }

  function copyDestinationPose(source) {
    if (!state.destinationDraft || state.destinationSaving) return;
    const pose = source === "robot" ? state.status?.map_pose : state.mapSelection;
    const valid = source === "robot" ? state.statusConnected && pose?.available && pose.fresh : pose?.kind === "navigate";
    if (!valid || !["x", "y", "yaw"].every((key) => Number.isFinite(pose[key]))) {
      setError(source === "robot" ? "A fresh robot pose in the map frame is required." : "Select a navigation goal on the map first."); return;
    }
    for (const key of ["x", "y", "yaw"]) byId(`destination-${key}`).value = pose[key];
    setError("");
  }

  async function saveDestination(event) {
    event.preventDefault();
    const draft = state.destinationDraft;
    if (!draft || state.destinationSaving) return;
    try {
      const payload = {
        label: byId("destination-name").value.trim(),
        pose: Object.fromEntries(["x", "y", "yaw"].map((key) => [key, finiteField(`destination-${key}`)])),
        ...(draft.id ? { id: draft.id, revision: draft.revision } : {}),
      };
      state.destinationSaving = true; ++state.destinationLoadSequence; renderDestinationControls();
      const catalog = await api("/api/presets/save", { method: "POST", body: JSON.stringify(payload) });
      // Discard refreshes started while the save was in flight.
      ++state.destinationLoadSequence;
      applyDestinations(catalog);
      byId("destination-select").value = catalog.preset.id;
      state.destinationDraft = null; byId("destination-editor").hidden = true;
      setError("");
    } catch (error) { setError(error.message); }
    finally { state.destinationSaving = false; renderDestinationControls(); }
  }

  async function deleteDestination() {
    const selected = state.presets.find((item) => item.id === byId("destination-select").value);
    if (!selected || state.destinationSaving || !window.confirm(`Delete destination ${selected.label}?`)) return;
    state.destinationSaving = true; ++state.destinationLoadSequence; renderDestinationControls();
    try {
      const catalog = await api("/api/presets/delete", { method: "POST", body: JSON.stringify({ id: selected.id, revision: selected.revision }) });
      ++state.destinationLoadSequence;
      applyDestinations(catalog);
      if (state.destinationDraft?.id === selected.id) {
        state.destinationDraft = null; byId("destination-editor").hidden = true;
      }
      setError("");
    } catch (error) { setError(error.message); }
    finally { state.destinationSaving = false; renderDestinationControls(); }
  }

  function setMapMode(mode) {
    state.mapMode = mode;
    state.mapSelection = null;
    byId("select-initial-pose").classList.toggle("active", mode === "initial_pose");
    byId("select-navigation-goal").classList.toggle("active", mode === "navigate");
    byId("select-initial-pose").setAttribute("aria-pressed", String(mode === "initial_pose"));
    byId("select-navigation-goal").setAttribute("aria-pressed", String(mode === "navigate"));
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
        if (message.type === "status_delta") {
          const delta = message.payload;
          const unlockChanged = Object.hasOwn(delta.set || {}, "execution_unlock_remaining_sec") ||
            (delta.remove || []).some((path) => path[0] === "execution_unlock_remaining_sec");
          applyStatus(mergeStatus(state.status, delta), unlockChanged);
        }
        if (["status", "status_delta"].includes(message.type)) {
          state.statusConnected = true;
          renderTaskShortcuts();
        }
      } catch (_) { setError("Received an invalid status update"); }
    };
    socket.onclose = (event) => {
      if (state.socket !== socket) return;
      state.socket = null;
      pauseShortcutConnection();
      invalidateExecutionUnlock();
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
      if (["pick", "pick_place"].includes(kind)) {
        if (!state.selectedBoxId) throw new Error("Select a fresh visible box before picking");
        extra = { ...extra, instance_id: state.selectedBoxId };
      }
      if (["place", "pick_place"].includes(kind) && manualPlacePoseEnabled() && !extra.place_pose) {
        extra = { ...extra, place_pose: placePose() };
      }
      const planOnly = kind === "reset" ? null : byId("plan-only").checked;
      const physical = kind === "reset" || !planOnly;
      const payload = { kind, ...extra, ...(["pick", "place", "pick_place"].includes(kind)
        ? { table_profile_id: tableProfileSelection() } : {}) };
      if (physical && !window.confirm(`Submit a physical manipulation command${Object.hasOwn(payload, "table_profile_id") ? ` using table ${payload.table_profile_id || "server default"}` : ""}?`)) return;
      if (planOnly !== null) payload.plan_only = planOnly;
      if (physical) payload.confirmed = true;
      await api("/api/actions", { method: "POST", body: JSON.stringify(payload) });
      setError("");
    } catch (error) { setError(error.message); }
  }
  byId("execute-saved-plan")?.addEventListener("click", executeSavedPlan);
  const guidedSteps = ["fine_align", "set_height", "manipulate", "default_height", "undock"];

  function workflowSteps(workflow) { return workflow.steps || guidedSteps; }

  function shortcutSteps(item) {
    const settings = { fine_align: item.dock, set_height: item.posture,
      default_height: item.return_posture, undock: item.undock };
    return ["navigate_start", "carry_start", ...guidedSteps, "carry_end", "navigate_end"]
      .filter((step) => step === "manipulate" || (settings[step] || item[step])?.enabled);
  }

  function shortcutSelected() {
    return state.taskShortcuts?.shortcuts?.find((item) => item.id === byId("task-shortcut-select").value);
  }

  function shortcutDescription(item) {
    const steps = [];
    const destinationName = (id) => state.presets?.find((preset) => preset.id === id)?.label || "unavailable destination";
    if (item.navigate_start?.enabled) steps.push(`Navigate to ${destinationName(item.navigate_start.preset_id)}`);
    if (item.carry_start?.enabled) steps.push(`Carry ${item.carry_start.pose.toUpperCase()}`);
    const boxTarget = item.box ? `${item.box.profile_id} (${item.box.instance_id ? `ID ${item.box.instance_id.replace(/^tag:/, "")}` : "visible tag at run time"})` : "";
    if (item.dock.enabled) steps.push(`Dock ${item.dock.profile_id}${item.action === "place" && item.box ? `; box reference ${boxTarget}` : ""}`);
    if (item.posture.enabled) steps.push(`Posture ${item.posture.height} m / ${item.posture.waist_yaw} rad`);
    steps.push(item.action === "pick" ? `Pick ${boxTarget}`
      : item.place.mode === "automatic" ? `Place on ${item.place.table_profile_id}`
        : `Place at ${item.place.pose.frame_id} (${item.place.pose.x}, ${item.place.pose.y}, ${item.place.pose.z}), yaw ${item.place.pose.yaw}`);
    if (item.return_posture.enabled) steps.push(`Return posture ${item.return_posture.height} m / ${item.return_posture.waist_yaw} rad`);
    if (item.undock.enabled) steps.push(`Undock ${item.undock.profile_id}`);
    if (item.carry_end?.enabled) steps.push(`Carry ${item.carry_end.pose.toUpperCase()}`);
    if (item.navigate_end?.enabled) steps.push(`Navigate to ${destinationName(item.navigate_end.preset_id)}`);
    return steps.join(" → ");
  }

  async function loadTaskShortcuts() {
    try { state.taskShortcuts = await api("/api/task-shortcuts"); }
    catch (error) { state.taskShortcuts = { available: false, shortcuts: [], detail: error.message }; }
    renderTaskShortcuts();
  }

  function shortcutOptions(id, choices, prompt, preferredValue = null) {
    const select = byId(id), selected = preferredValue ?? (select.value || "");
    const values = [["", prompt], ...choices];
    if (selected && !values.some(([value]) => value === selected)) values.push([selected, `${selected} (unavailable)`]);
    const signature = JSON.stringify(values);
    if (select.dataset.choices !== signature) {
      select.replaceChildren(...values.map(([value, label]) => {
        const option = document.createElement("option");
        option.value = value; option.textContent = label; return option;
      }));
      select.dataset.choices = signature;
    }
    select.value = selected;
  }

  function shortcutProfileOptions(field, preferredValue = null) {
    const label = field === "box" ? "box" : field === "table" ? "table" : "docking";
    const catalog = field === "box" ? state.status?.box_profiles
      : field === "table" ? state.status?.table_profiles : state.status?.docking_profiles;
    const knownBoxes = [...new Set([
      ...(state.status?.visible_boxes?.boxes || []).map((box) => box.profile_id),
      ...(state.taskShortcuts?.shortcuts || []).map((item) => item.box?.profile_id),
    ].filter(Boolean))].sort();
    const choices = catalog?.available ? catalog.profiles.map((item) => [item.id, item.id])
      : field === "box" ? knownBoxes.map((id) => [id, id]) : [];
    // A dot cannot occur in a box profile ID (one ROS parameter component).
    choices.push([".manual", "Enter a profile name…"]);
    shortcutOptions(`shortcut-${field}-profile`, choices, `Choose a ${label} profile`, preferredValue);
    const select = byId(`shortcut-${field}-profile`);
    if (select.value !== ".manual") select.dataset.profileValue = select.value;
  }

  function shortcutProfileSelection(field) {
    const select = byId(`shortcut-${field}-profile`);
    if (select.value === ".manual") {
      const label = field === "box" ? "box" : field === "table" ? "table" : "docking";
      const name = window.prompt(`Enter a named ${label} profile (also available while discovery is offline):`, "");
      const value = field === "box" ? name : name?.trim();
      const valid = value && (field === "box" ? !value.includes(".") : /^[A-Za-z0-9_]{1,128}$/.test(value));
      if (value && !valid) setError(`Enter a valid ${label} profile name.`);
      shortcutProfileOptions(field, valid ? value : (select.dataset.profileValue || ""));
    }
    select.dataset.profileValue = select.value;
    return select.value;
  }

  function selectedTaskShortcuts() {
    const selected = state.selectedShortcuts ||= { pick: null, place: null };
    return ["pick", "place"].flatMap((action) => {
      const item = state.taskShortcuts.shortcuts.find((entry) => entry.id === selected[action] && entry.action === action);
      if (!item) selected[action] = null;
      return item ? [item] : [];
    });
  }

  function shortcutSelectionLocked() {
    const workflow = state.guidedWorkflow;
    return state.guidedSubmitting || !!(workflow && !workflow.failed && !workflow.completed);
  }

  function renderTaskShortcuts() {
    if (!state.taskShortcuts) return;
    const catalog = state.taskShortcuts;
    shortcutOptions("task-shortcut-select", catalog.shortcuts.map((item) => [item.id, item.name]), "Choose a shortcut");
    const item = shortcutSelected();
    const selected = selectedTaskShortcuts();
    byId("task-shortcut-preview").textContent = selected.length
      ? selected.map((entry) => `${entry.name}: ${shortcutDescription(entry)}`).join(" → ")
      : "Select one Pick and/or one Place shortcut, then Run selected.";
    const run = byId("run-selected-shortcuts");
    const runReason = selected.length ? shortcutUnavailableReason(selected[0]) : "Select a shortcut first.";
    run.disabled = !!runReason;
    run.title = runReason || "Run the selected shortcuts in Pick → Place order";
    byId("task-shortcut-status").textContent = catalog.available ? `${catalog.shortcuts.length} saved shortcut(s).` : catalog.detail;
    for (const action of ["pick", "place"]) {
      const entries = catalog.shortcuts.filter((entry) => entry.action === action);
      const list = byId(`task-shortcut-${action}-buttons`);
      const signature = JSON.stringify(entries.map((entry) => entry.id));
      if (list.dataset.shortcuts !== signature) {
        list.replaceChildren(...entries.map((entry) => {
          const button = document.createElement("button");
          button.type = "button"; button.className = "shortcut-selection";
          button.addEventListener("click", () => {
            if (shortcutSelectionLocked()) return;
            const selected = state.selectedShortcuts;
            selected[action] = selected[action] === entry.id ? null : entry.id;
            renderTaskShortcuts();
          });
          return button;
        }));
        list.dataset.shortcuts = signature;
      }
      if (!entries.length) list.textContent = catalog.available
        ? `No saved ${action === "pick" ? "Pick" : "Place"} shortcuts. Open Manage shortcuts to create one.` : "Shortcuts unavailable.";
      entries.forEach((entry, index) => {
        const button = list.children[index];
        button.textContent = entry.name;
        button.disabled = !catalog.available || shortcutSelectionLocked();
        button.setAttribute("aria-pressed", String(state.selectedShortcuts[action] === entry.id));
        button.title = shortcutDescription(entry);
      });
    }
    for (const id of ["edit", "duplicate", "delete"]) byId(`task-shortcut-${id}`).disabled = !item || !catalog.available;
    byId("task-shortcut-new").disabled = !catalog.available;
    byId("task-shortcut-save").disabled = state.shortcutSaving || !state.shortcutDraft;
    if (!byId("task-shortcut-editor").hidden) renderShortcutChoices();
  }

  function shortcutUnavailableReason(item) {
    if (!state.taskShortcuts.available) return "Shortcut storage is unavailable.";
    if (!state.statusConnected || state.authenticated === false) return "Connect live status before starting a shortcut.";
    const workflow = state.guidedWorkflow;
    if (state.guidedSubmitting || (workflow && !workflow.failed && !workflow.completed) ||
        state.status?.task_admission?.blocked || state.status?.navigation?.goal_status?.active ||
        ["running", "retrying", "paused"].includes(state.status?.manipulation_task?.status) ||
        (state.status?.operations || []).some((operation) => activeStatuses.includes(operation.status))) {
      return "Wait for the active operation to finish.";
    }
    if (byId("plan-only").checked) return "Turn off Plan only to run a physical sequence.";
    if (!(executionUnlockRemaining() > 0)) return "Unlock physical motion before starting a shortcut.";
    const expected = item.action === "pick" ? "EMPTY" : "HOLDING";
    if (state.status?.manipulation_state?.state !== expected) return `This shortcut requires manipulation state ${expected}.`;
    return "";
  }

  function renderShortcutChoices() {
    for (const field of ["box", "table", "dock", "undock"]) shortcutProfileOptions(field);
    const place = byId("shortcut-action").value === "place";
    const fixed = byId("shortcut-box-selection").value === "fixed";
    byId("shortcut-box-instance").disabled = !fixed;
    byId("shortcut-box-instance").required = fixed;
    for (const stage of ["start", "end"]) {
      shortcutOptions(`shortcut-navigate-${stage}-preset`, (state.presets || []).map((item) => [item.id, item.label]), "Choose a destination");
      const incompatible = stage === "start" ? !place : place;
      const enabled = byId(`shortcut-carry-${stage}-enabled`);
      enabled.disabled = incompatible;
      if (incompatible) enabled.checked = false;
      byId(`shortcut-carry-${stage}-pose`).disabled = incompatible;
    }
    const manual = byId("shortcut-place-mode").value === "manual";
    byId("shortcut-place-fields").hidden = !place;
    byId("shortcut-table-field").hidden = manual;
    byId("shortcut-manual-fields").hidden = !manual;
  }

  function editTaskShortcut(mode) {
    const selected = shortcutSelected();
    if (mode !== "new" && !selected) return;
    const box = state.status?.visible_boxes?.boxes?.find((item) => item.instance_id === guidedPickId({}));
    const profile = box?.default_docking_profile || state.status?.docking_profiles?.default_profile || "";
    const defaults = { name: "", action: "pick", box: box ? { profile_id: box.profile_id, instance_id: null } : null,
      dock: { enabled: true, profile_id: profile }, undock: { enabled: true, profile_id: profile },
      posture: { enabled: true, height: 0.64, waist_yaw: 0 }, return_posture: { enabled: true, height: 0.64, waist_yaw: 0 },
      navigate_start: { enabled: false, preset_id: "" }, navigate_end: { enabled: false, preset_id: "" },
      carry_start: { enabled: false, pose: "a" }, carry_end: { enabled: false, pose: "a" },
      place: { mode: "automatic", table_profile_id: state.status?.table_profiles?.default_profile || "" } };
    const draft = JSON.parse(JSON.stringify(mode === "new" ? defaults : selected));
    if (mode === "duplicate") { delete draft.id; delete draft.revision; draft.name = `${draft.name} copy`; }
    state.shortcutDraft = draft;
    byId("task-shortcut-management").open = true;
    byId("task-shortcut-save").disabled = state.shortcutSaving;
    byId("task-shortcut-editor").hidden = false;
    byId("shortcut-name").value = draft.name;
    byId("shortcut-action").value = draft.action;
    byId("shortcut-box-profile").value = "";
    byId("shortcut-box-selection").value = draft.box?.instance_id ? "fixed" : "profile";
    byId("shortcut-box-instance").value = draft.box?.instance_id?.replace(/^tag:/, "") || "";
    for (const stage of ["start", "end"]) {
      byId(`shortcut-navigate-${stage}-enabled`).checked = !!draft[`navigate_${stage}`]?.enabled;
      byId(`shortcut-navigate-${stage}-preset`).value = "";
      byId(`shortcut-carry-${stage}-enabled`).checked = !!draft[`carry_${stage}`]?.enabled;
      byId(`shortcut-carry-${stage}-pose`).value = draft[`carry_${stage}`]?.pose || "a";
    }
    for (const [field, key] of [["dock", "dock"], ["posture", "posture"], ["return", "return_posture"], ["undock", "undock"]]) {
      byId(`shortcut-${field}-enabled`).checked = draft[key].enabled;
      if (["dock", "undock"].includes(field)) {
        byId(`shortcut-${field}-profile`).value = "";
      } else {
        byId(`shortcut-${field}-height`).value = draft[key].height;
        byId(`shortcut-${field}-yaw`).value = draft[key].waist_yaw;
      }
    }
    byId("shortcut-place-mode").value = draft.place?.mode || "automatic";
    renderShortcutChoices();
    for (const stage of ["start", "end"]) {
      const select = byId(`shortcut-navigate-${stage}-preset`);
      select.value = draft[`navigate_${stage}`]?.preset_id || "";
      // Retain a saved destination even when it has been removed from the catalog.
      if (draft[`navigate_${stage}`]?.preset_id && !select.value) {
        const option = document.createElement("option"); option.value = draft[`navigate_${stage}`].preset_id;
        option.textContent = `${option.value} (unavailable)`; select.appendChild(option); select.value = option.value;
      }
    }
    for (const field of ["dock", "undock"]) shortcutProfileOptions(field, draft[field].profile_id);
    shortcutProfileOptions("box", draft.box?.profile_id || "");
    shortcutProfileOptions("table", draft.place?.table_profile_id || "");
    const pose = draft.place?.pose || { frame_id: "base_link", x: 0.35, y: 0, z: 0.29, yaw: 0 };
    byId("shortcut-place-frame").value = pose.frame_id;
    for (const key of ["x", "y", "z", "yaw"]) byId(`shortcut-place-${key}`).value = pose[key];
  }

  async function saveTaskShortcut(event) {
    event.preventDefault();
    const draft = state.shortcutDraft;
    if (state.shortcutSaving || !draft) return;
    state.shortcutSaving = true;
    renderTaskShortcuts();
    try {
      const item = { ...(draft.id ? { id: draft.id, revision: draft.revision } : {}),
        name: byId("shortcut-name").value.trim(), action: byId("shortcut-action").value };
      const profile = byId("shortcut-box-profile").value;
      let instance = null;
      if (byId("shortcut-box-selection").value === "fixed") {
        const id = byId("shortcut-box-instance").value.trim();
        const number = Number(id);
        if (!/^\d+$/.test(id) || !Number.isSafeInteger(number) || number > 2147483647) {
          throw new Error("Fixed box ID must be a whole number from 0 to 2147483647.");
        }
        instance = `tag:${number}`;
      }
      item.box = profile || instance ? { profile_id: profile, instance_id: instance } : null;
      for (const stage of ["start", "end"]) {
        item[`navigate_${stage}`] = { enabled: byId(`shortcut-navigate-${stage}-enabled`).checked,
          preset_id: byId(`shortcut-navigate-${stage}-preset`).value };
        item[`carry_${stage}`] = { enabled: byId(`shortcut-carry-${stage}-enabled`).checked,
          pose: byId(`shortcut-carry-${stage}-pose`).value };
      }
      for (const field of ["dock", "undock"]) item[field] = {
        enabled: byId(`shortcut-${field}-enabled`).checked, profile_id: byId(`shortcut-${field}-profile`).value.trim() };
      for (const [field, key] of [["posture", "posture"], ["return", "return_posture"]]) item[key] = {
        enabled: byId(`shortcut-${field}-enabled`).checked, height: finiteField(`shortcut-${field}-height`), waist_yaw: finiteField(`shortcut-${field}-yaw`) };
      item.place = item.action === "place" ? (byId("shortcut-place-mode").value === "automatic"
        ? { mode: "automatic", table_profile_id: byId("shortcut-table-profile").value.trim() }
        : { mode: "manual", pose: { frame_id: byId("shortcut-place-frame").value,
          ...Object.fromEntries(["x", "y", "z", "yaw"].map((key) => [key, finiteField(`shortcut-place-${key}`)])) } }) : null;
      const response = await api("/api/task-shortcuts/save", { method: "POST", body: JSON.stringify(item) });
      state.taskShortcuts = response;
      if (state.shortcutDraft === draft) {
        state.shortcutDraft = null;
        byId("task-shortcut-editor").hidden = true;
      }
      renderTaskShortcuts(); byId("task-shortcut-select").value = response.shortcut.id;
      renderTaskShortcuts(); setError("");
    } catch (error) { setError(error.message); }
    finally { state.shortcutSaving = false; renderTaskShortcuts(); }
  }

  async function deleteTaskShortcut() {
    const item = shortcutSelected();
    if (!item || !window.confirm(`Delete shortcut ${item.name}?`)) return;
    try {
      state.taskShortcuts = await api("/api/task-shortcuts/delete", { method: "POST", body: JSON.stringify({ id: item.id, revision: item.revision }) });
      byId("task-shortcut-select").value = "";
      renderTaskShortcuts(); setError("");
    } catch (error) { setError(error.message); }
  }

  function validateShortcutReferences(workflow) {
    const item = workflow.shortcut;
    for (const stage of ["navigate_start", "navigate_end"]) {
      if (!item[stage]?.enabled) continue;
      const preset = state.presets?.find((entry) => entry.id === item[stage].preset_id);
      if (!preset) throw new Error(`Navigation destination ${item[stage].preset_id} is unavailable.`);
      const expected = workflow.navigationTargets?.[stage];
      if (expected && ["x", "y", "yaw"].some((key) => preset.pose[key] !== expected.pose[key])) {
        throw new Error("The shortcut navigation destination changed; review the route before restarting.");
      }
    }
    for (const [key, signature] of [["dock", "dockSignature"], ["undock", "undockSignature"]]) {
      if (item[key].enabled) comboDock(item[key].profile_id, workflow[signature]);
    }
    if (workflow.requiresTable) {
      const catalog = state.status?.table_profiles;
      const table = catalog?.available && catalog.profiles.find((entry) => entry.id === workflow.tableId);
      if (!table) throw new Error("The shortcut table profile is unavailable.");
      if (workflow.tableSignature && JSON.stringify(table) !== workflow.tableSignature) throw new Error("The shortcut table calibration changed; verify robot state before restarting.");
    }
    const visible = state.status?.visible_boxes;
    const box = visible?.fresh && visible.boxes.find((entry) => entry.instance_id === workflow.instanceId);
    if (box && item.box && box.profile_id !== item.box.profile_id) throw new Error("The selected box now has a different profile; verify the shortcut target.");
    if (box && workflow.boxTarget && !box.docking_profile_ids?.includes(workflow.profileId)) throw new Error("The selected box no longer supports the shortcut docking profile.");
  }

  function pauseShortcutConnection() {
    state.statusConnected = false;
    state.connectionGeneration = (state.connectionGeneration || 0) + 1;
    const workflow = state.guidedWorkflow;
    if (workflow?.shortcut && !workflow.completed) {
      workflow.reconnectUnlockRequired = true;
      failGuidedWorkflow(workflow, "Shortcut paused: live status disconnected. Reconnect, verify the current operation, unlock and Continue.", workflow.resumeBlocked);
    }
    renderGuidedWorkflow();
  }

  function createShortcutWorkflow(item, status) {
    const snapshot = JSON.parse(JSON.stringify(item));
    if ((item.action === "pick" && item.carry_start?.enabled) ||
        (item.action === "place" && item.carry_end?.enabled)) throw new Error("Carry poses require a held box: enable before Place or after Pick.");
    const dock = item.dock.enabled && status.docking_profiles?.profiles?.find((entry) => entry.id === item.dock.profile_id);
    const workflow = { shortcut: snapshot, kind: item.action, label: `${item.name} (${item.action === "pick" ? "Pick" : "Place"})`,
      instanceId: item.box?.instance_id || null, fixedInstance: !!item.box?.instance_id, boxTarget: dock?.target_source === "box",
      profileId: item.dock.enabled ? item.dock.profile_id : "", undockProfileId: item.undock.enabled ? item.undock.profile_id : "",
      posture: { height: item.posture.height, waist_yaw: item.posture.waist_yaw, wait_for_settle: true },
      returnPosture: { height: item.return_posture.height, waist_yaw: item.return_posture.waist_yaw, wait_for_settle: true },
      placeTarget: snapshot.place?.mode === "manual" ? snapshot.place.pose : null,
      requiresTable: item.action === "place" && item.place.mode === "automatic", tableId: item.place?.table_profile_id || "",
      steps: shortcutSteps(snapshot), navigationTargets: {},
      step: 0, operationId: null, useManualUnlock: true, confirmNav2Idle: !status.navigation?.goal_status?.available };
    if (workflow.boxTarget && !item.box) throw new Error("Box docking requires a box profile.");
    validateShortcutReferences(workflow);
    for (const stage of ["navigate_start", "navigate_end"]) {
      if (snapshot[stage]?.enabled) workflow.navigationTargets[stage] = JSON.parse(JSON.stringify(
        state.presets.find((entry) => entry.id === snapshot[stage].preset_id)));
    }
    workflow.dockSignature = item.dock.enabled ? comboDock(item.dock.profile_id) : null;
    workflow.undockSignature = item.undock.enabled ? comboDock(item.undock.profile_id) : null;
    workflow.tableSignature = workflow.requiresTable ? JSON.stringify(status.table_profiles.profiles.find((table) => table.id === workflow.tableId)) : null;
    return workflow;
  }

  function completeShortcutWorkflow(workflow) {
    if (workflow.cancelRequested) return;
    const remaining = workflow.remainingWorkflows || [];
    if (remaining.length) {
      const next = remaining[0];
      Object.assign(workflow, next, {
        remainingWorkflows: remaining.slice(1), sequencePosition: workflow.sequencePosition + 1,
        useManualUnlock: false, operationSeen: false, completed: false, message: "",
      });
    } else {
      workflow.message = workflow.sequenceLength > 1 ? "Pick → Place sequence completed." : `${workflow.label} sequence completed.`;
      workflow.completed = true;
    }
  }

  async function runSelectedShortcuts() {
    const selected = selectedTaskShortcuts();
    if (selected.length) await runTaskShortcut(selected[0].id, selected);
  }

  async function runTaskShortcut(shortcutId = null, selections = null) {
    if (state.guidedSubmitting || (state.guidedWorkflow && !state.guidedWorkflow.failed && !state.guidedWorkflow.completed)) return;
    const selected = shortcutId === null ? shortcutSelected()
      : state.taskShortcuts?.shortcuts.find((item) => item.id === shortcutId);
    if (!selected) return;
    state.guidedSubmitting = true;
    try {
      const [catalog, status, destinations] = await Promise.all([api("/api/task-shortcuts"), api("/api/status"), api("/api/presets")]);
      state.presets = destinations.presets;
      state.taskShortcuts = catalog; applyStatus(status);
      const items = (selections || [selected]).map((selection) => {
        const item = catalog.available && catalog.shortcuts.find((entry) => entry.id === selection.id && entry.action === selection.action);
        if (!item) throw new Error("The shortcut is unavailable; refresh the list.");
        return item;
      });
      const item = items[0];
      if (!state.statusConnected || state.authenticated === false) throw new Error("Connect live status before starting a shortcut.");
      if (byId("plan-only").checked || !(executionUnlockRemaining() > 0)) throw new Error("Turn off Plan only and unlock physical motion before running a shortcut.");
      const expected = item.action === "pick" ? "EMPTY" : "HOLDING";
      if (status.manipulation_state?.state !== expected) throw new Error(`This shortcut requires manipulation state ${expected}.`);
      if (status.task_admission?.blocked || status.navigation?.goal_status?.active ||
          ["running", "retrying", "paused"].includes(status.manipulation_task?.status) ||
          (status.operations || []).some((operation) => activeStatuses.includes(operation.status))) throw new Error("Wait for the active operation to finish.");
      const workflows = items.map((entry) => createShortcutWorkflow(entry, status));
      const workflow = workflows[0];
      workflow.remainingWorkflows = workflows.slice(1);
      workflow.sequencePosition = 1;
      workflow.sequenceLength = workflows.length;
      if (!window.confirm(`Run physical shortcut sequence: ${items.map((entry) => `${entry.name}: ${shortcutDescription(entry)}`).join(" → ")}? Remaining stages run automatically.${workflow.confirmNav2Idle ? " Confirm Nav2 is idle." : ""}`)) return;
      state.guidedWorkflow = workflow; setError("");
    } catch (error) { setError(error.message); }
    finally { state.guidedSubmitting = false; renderGuidedWorkflow(); }
    if (state.guidedWorkflow?.shortcut && !state.guidedWorkflow.failed) await runGuidedStep();
  }

  function tableProfileSelection() {
    const id = byId("table-profile").value || "";
    const catalog = state.status?.table_profiles;
    if (id && (!catalog?.available || !catalog.profiles.some((table) => table.id === id))) {
      throw new Error("The selected table profile is unavailable; choose again");
    }
    return id;
  }

  function comboTable(profileId, expectedId = null, expectedSignature = null) {
    const docking = state.status?.docking_profiles;
    const tables = state.status?.table_profiles;
    if (!docking?.available || !tables?.available) {
      throw new Error("Waiting for docking and table profile configuration.");
    }
    const dock = docking.profiles.find((profile) => profile.id === profileId);
    if (!dock) throw new Error("The combo docking profile is unavailable.");
    const matches = tables.profiles.filter((table) => table.tag_id === dock.tag_id && table.tag_frame === dock.tag_frame);
    if (matches.length !== 1) throw new Error("Docking must match exactly one table profile by tag ID and frame.");
    if (expectedId && matches[0].id !== expectedId) throw new Error("The docking result does not match the combo table.");
    if (expectedSignature && JSON.stringify(matches[0]) !== expectedSignature) {
      throw new Error("The combo table calibration changed; verify robot state before restarting.");
    }
    return matches[0];
  }

  function comboDock(profileId, expectedSignature = null) {
    const catalog = state.status?.docking_profiles;
    if (!catalog?.available) throw new Error("Waiting for docking profile configuration.");
    const dock = catalog.profiles.find((profile) => profile.id === profileId);
    if (!dock) throw new Error("The combo docking profile is unavailable.");
    const signature = JSON.stringify([dock.tag_id, dock.tag_frame, dock.standoff,
      dock.lateral_offset, dock.yaw_offset, dock.detections_topic, dock.undock_mode,
      dock.timed_reverse_speed, dock.timed_reverse_duration, dock.target_source]);
    if (expectedSignature && signature !== expectedSignature) {
      throw new Error("The combo docking calibration changed; verify robot state before restarting.");
    }
    return signature;
  }

  function bindCompletedDock(workflow, operation) {
    const resolved = operation.result?.profile_id;
    if (!resolved) throw new Error("Dock did not report its resolved profile; verify robot state before restarting.");
    comboDock(resolved, workflow.dockSignature);
    if (workflow.boxTarget && operation.result?.instance_id !== workflow.instanceId) {
      throw new Error("Dock reported a different box instance; verify robot state before restarting.");
    }
    if (workflow.shortcut && resolved !== workflow.profileId) {
      throw new Error("Dock reported a different shortcut profile; verify robot state before restarting.");
    }
    if (workflow.requiresTable && !workflow.shortcut) comboTable(resolved, workflow.tableId, workflow.tableSignature);
    workflow.profileId = resolved;
  }

  function renderTableProfiles() {
    const catalog = state.status?.table_profiles;
    const select = byId("table-profile");
    const selected = select.value || "";
    const choices = [["", catalog?.available ? `Server default (${catalog.default_profile})` : "Server default"],
      ...(catalog?.available ? catalog.profiles.map((table) => [table.id, table.id]) : [])];
    if (selected && !choices.some(([value]) => value === selected)) choices.push([selected, `${selected} (unavailable)`]);
    const signature = JSON.stringify(choices);
    if (select.dataset.choices !== signature) {
      select.replaceChildren(...choices.map(([value, label]) => {
        const option = document.createElement("option");
        option.value = value; option.textContent = label; return option;
      }));
      select.value = selected; select.dataset.choices = signature;
    }
    const table = catalog?.profiles?.find((item) => item.id === (selected || catalog.default_profile));
    byId("table-profile-detail").textContent = table
      ? `Table ${table.id}: tag ${table.tag_id} (${table.tag_frame}), dimensions ${table.dimensions.map((value) => value.toFixed(3)).join(" × ")} m. Combo selection follows the docking tag and frame.`
      : catalog?.available ? "The selected table profile is unavailable; choose again."
        : catalog?.detail || "Waiting for table profile configuration.";
  }

  function dockingProfileSelection(undocking = false) {
    const profileId = byId(undocking ? "undocking-profile" : "docking-profile").value || "";
    const catalog = state.status?.docking_profiles;
    if (profileId && (!catalog?.available || !catalog.profiles.some((profile) => profile.id === profileId))) {
      throw new Error("The selected docking profile is unavailable; choose a configured profile again");
    }
    return profileId;
  }

  function boxDockChoice(preferBox = true) {
    const catalog = state.status?.docking_profiles;
    const id = guidedPickId({});
    const box = state.status?.visible_boxes?.boxes?.find((item) => item.instance_id === id);
    let profileId = dockingProfileSelection();
    if (!profileId) profileId = (preferBox && box?.default_docking_profile) || catalog?.default_profile;
    const profile = catalog?.profiles?.find((item) => item.id === profileId);
    const boxTarget = profile?.target_source === "box";
    if (boxTarget) {
      if (!box) throw new Error("Select a fresh visible box before box docking.");
      if (!box.docking_profile_ids?.includes(profileId)) {
        throw new Error("This docking profile is not supported by the selected box.");
      }
    }
    return { profileId, box, boxTarget };
  }

  function renderDockingProfiles() {
    const catalog = state.status?.docking_profiles;
    const running = state.guidedWorkflow && !state.guidedWorkflow.failed && !state.guidedWorkflow.completed;
    const boxId = guidedPickId({});
    const box = state.status?.visible_boxes?.boxes?.find((item) => item.instance_id === boxId);
    for (const [id, automatic] of [["docking-profile", "Server default"],
        ["undocking-profile", "Last successful dock / server default"]]) {
      const select = byId(id);
      const selected = select.value || "";
      const choices = [["", id === "docking-profile" && catalog?.available
        ? (box?.default_docking_profile ? `Box default (${box.default_docking_profile})` : `Server default (${catalog.default_profile})`) : automatic],
        ...(catalog?.available ? catalog.profiles.filter((profile) => id === "undocking-profile" ||
          profile.target_source !== "box" || box?.docking_profile_ids?.includes(profile.id))
          .map((profile) => [profile.id, profile.id]) : [])];
      if (selected && !choices.some(([value]) => value === selected)) {
        choices.push([selected, `${selected} (unavailable)`]);
      }
      const signature = JSON.stringify(choices);
      if (select.dataset.choices !== signature) {
        select.replaceChildren(...choices.map(([value, label]) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = label;
          return option;
        }));
        select.value = selected;
        select.dataset.choices = signature;
      }
      select.disabled = !!running;
    }
    const selected = byId("docking-profile").value || box?.default_docking_profile || catalog?.default_profile;
    const profile = catalog?.profiles?.find((item) => item.id === selected);
    byId("docking-profile-detail").textContent = !catalog?.available
      ? `${catalog?.detail || "Waiting for docking profile configuration"}. Automatic manual selection remains available.`
      : !profile ? "The selected docking profile is no longer configured; choose again."
        : `Dock ${profile.id}: ${profile.target_source === "box" ? "selected box tag" : `tag ${profile.tag_id} (${profile.tag_frame})`}, stand-off ${profile.standoff.toFixed(3)} m, lateral offset ${profile.lateral_offset.toFixed(3)} m, yaw offset ${profile.yaw_offset.toFixed(3)} rad. Detection source ${profile.detections_topic || "/front_center_rectify/detections"}. ${profile.undock_mode === "timed_reverse" ? `Undock: timed reverse at ${profile.timed_reverse_speed.toFixed(2)} m/s for ${profile.timed_reverse_duration.toFixed(1)} s (estimated travel).` : "Undock: tag relative."} Undocking uses its own selection above.`;
    const retreatId = byId("undocking-profile").value;
    const retreat = catalog?.profiles?.find((item) => item.id === retreatId);
    byId("undocking-profile-detail").textContent = !retreatId
      ? "Undock uses the last successful dock profile, or the server default after restart; feedback reports the resolved mode."
      : !retreat ? "The selected undocking profile is unavailable."
        : retreat.undock_mode === "timed_reverse"
          ? `Undock ${retreat.id}: reverse at ${retreat.timed_reverse_speed.toFixed(2)} m/s for ${retreat.timed_reverse_duration.toFixed(1)} s; distance is estimated.`
          : `Undock ${retreat.id}: track ${retreat.target_source === "box" ? "the last docked box tag" : `stationary tag ${retreat.tag_id} (${retreat.tag_frame})`} throughout retreat.`;

  }

  const activeStatuses = ["SUBMITTING", "ACTIVE", "CANCEL_REQUESTED"];

  function guidedStepLabel(step, label) {
    const shortcut = state.guidedWorkflow?.shortcut;
    return ({ fine_align: "Dock", set_height: shortcut ? "Set posture" : "Set Height", manipulate: label,
      default_height: shortcut ? "Return posture" : "Default Height", undock: "Undock",
      navigate_start: "Navigate before combo", navigate_end: "Navigate after combo",
      carry_start: "Carry pose before combo", carry_end: "Carry pose after combo" })[step];
  }

  function failGuidedWorkflow(workflow, message, resumeBlocked = false) {
    workflow.failed = true;
    workflow.message = message;
    workflow.resumeBlocked = resumeBlocked;
  }

  function updateGuidedWorkflow() {
    const workflow = state.guidedWorkflow;
    if (!workflow || workflow.failed || workflow.completed) return;
    if (workflow.operationId) {
      const operation = state.status?.operations?.find((item) => item.id === workflow.operationId);
      if (!operation) {
        if (workflow.operationSeen) {
          workflow.operationId = null;
          failGuidedWorkflow(workflow, `${workflow.label} stopped: operation history was lost. Verify the robot state before starting again.`, true);
        }
        return;
      }
      workflow.operationSeen = true;
      if (activeStatuses.includes(operation.status)) return;
      if (workflowSteps(workflow)[workflow.step] === "fine_align" && operation.status === "SUCCEEDED" &&
          operation.result?.success !== false && (!state.status?.docking_profiles?.available ||
          (workflow.requiresTable && !state.status?.table_profiles?.available))) return;
      workflow.operationId = null;
      if (operation.status !== "SUCCEEDED" || operation.result?.success === false) {
        failGuidedWorkflow(workflow, `${workflow.label} stopped at ${guidedStepLabel(workflowSteps(workflow)[workflow.step], workflow.label)}: ${operation.result?.message || operation.detail || operation.status}`,
          ["OUTCOME_UNKNOWN", "ERROR"].includes(operation.status));
        return;
      }
      if (workflowSteps(workflow)[workflow.step] === "fine_align") {
        try { bindCompletedDock(workflow, operation); }
        catch (error) { failGuidedWorkflow(workflow, error.message, true); return; }
      }
      workflow.step += 1;
      if (workflow.step === workflowSteps(workflow).length) {
        completeShortcutWorkflow(workflow);
      }
    }
    scheduleGuidedStep();
  }

  function scheduleGuidedStep() {
    const workflow = state.guidedWorkflow;
    if (!workflow || workflow.failed || workflow.completed || workflow.cancelRequested ||
        workflow.operationId || state.guidedSubmitting || workflow.scheduled) return;
    workflow.scheduled = true;
    Promise.resolve().then(() => {
      workflow.scheduled = false;
      if (state.guidedWorkflow === workflow) return runGuidedStep();
    });
  }

  function guidedPickId(workflow) {
    const visible = state.status?.visible_boxes;
    const profile = workflow.shortcut?.box?.profile_id;
    const boxes = visible?.fresh && Array.isArray(visible.boxes) ? visible.boxes.filter((box) =>
      (!profile || box.profile_id === profile) &&
      (!workflow.boxTarget || box.docking_profile_ids?.includes(workflow.profileId))) : [];
    const selected = boxes.find((box) => box.instance_id === state.selectedBoxId);
    const identifier = workflow.instanceId || selected?.instance_id ||
      (boxes.length === 1 ? boxes[0].instance_id : null);
    return boxes.some((box) => box.instance_id === identifier) ? identifier : null;
  }

  function guidedWaitReason(workflow) {
    const step = workflowSteps(workflow)[workflow.step];
    if (workflow.shortcut && !state.statusConnected) return "Reconnect live status before continuing the shortcut.";
    if (state.status?.task_admission?.blocked) return state.status.task_admission.detail;
    if (["running", "retrying", "paused"].includes(state.status?.manipulation_task?.status)) {
      return "Waiting for the active manipulation task to finish.";
    }
    if (["fine_align", "undock"].includes(step) && !state.status?.docking_profiles?.available) {
      return "Waiting for docking profile configuration.";
    }
    if (workflow.shortcut) {
      try { validateShortcutReferences(workflow); }
      catch (error) { return error.message; }
    } else if (["fine_align", "manipulate", "undock"].includes(step)) {
      try { comboDock(workflow.profileId, workflow.dockSignature); }
      catch (error) { return error.message; }
    }
    if (!workflow.shortcut && workflow.requiresTable && ["fine_align", "manipulate", "undock"].includes(step)) {
      try { comboTable(workflow.profileId, workflow.tableId, workflow.tableSignature); }
      catch (error) { return error.message; }
    }
    if (state.status?.navigation?.goal_status?.active) return "Waiting for active navigation to finish.";
    if ((state.status?.operations || []).some((operation) => activeStatuses.includes(operation.status))) {
      return "Waiting for the active operation to finish.";
    }
    const expectedState = workflow.kind === "pick"
      ? (workflow.step <= workflowSteps(workflow).indexOf("manipulate") ? "EMPTY" : "HOLDING")
      : (workflow.step <= workflowSteps(workflow).indexOf("manipulate") ? "HOLDING" : "EMPTY");
    if (state.status?.manipulation_state?.state !== expectedState) {
      return `Waiting for manipulation state ${expectedState}.`;
    }
    if (["set_height", "default_height"].includes(step) && !state.status?.locomanipulation_posture?.ready) {
      return state.status?.locomanipulation_posture?.detail || "Waiting for posture service.";
    }
    if (["carry_start", "carry_end"].includes(step)) {
      if (state.status?.manipulation_state?.state !== "HOLDING") return "Carry poses require a held box.";
      if (!state.status?.servers?.move_carry_pose) return "Waiting for carry-pose action server.";
    }
    if (["navigate_start", "navigate_end"].includes(step)) {
      if (!state.status?.servers?.navigate) return "Waiting for navigation action server.";
      if (!state.status?.map_pose?.available || !state.status.map_pose.fresh) return "Waiting for fresh map localization before navigation.";
    }
    if (((step === "manipulate" && workflow.kind === "pick") ||
        (workflow.shortcut && step === "fine_align" && workflow.boxTarget)) && !guidedPickId(workflow)) {
      if (workflow.instanceId) return `Waiting for a fresh detection of ${workflow.instanceId}.`;
      if (workflow.shortcut?.box) return `Select a fresh visible ${workflow.shortcut.box.profile_id} tag${workflow.boxTarget ? ` supporting ${workflow.profileId}` : ""} from Visible box.`;
      const visible = state.status?.visible_boxes;
      return visible?.fresh && visible.boxes?.length > 1
        ? "Multiple objects detected. Select the object to pick from Visible box."
        : "Waiting for a fresh object detection before Pick. Docking does not require a visible object.";
    }
    return "";
  }

  function renderGuidedWorkflow() {
    renderTaskShortcuts();
    const button = byId("dock-manipulate-undock");
    const message = byId("guided-workflow-status");
    const workflow = state.guidedWorkflow;
    const manipulationState = state.status?.manipulation_state?.state;
    const label = manipulationState === "HOLDING" ? "Place" : "Pick";
    const active = state.status?.task_admission?.blocked ||
      ["running", "retrying", "paused"].includes(state.status?.manipulation_task?.status) ||
      state.status?.navigation?.goal_status?.active ||
      (state.status?.operations || []).some((operation) => activeStatuses.includes(operation.status));
    const running = workflow && !workflow.failed && !workflow.completed;
    const unlocked = executionUnlockRemaining() > 0;
    byId("stop-guided-workflow").disabled = !running || workflow.cancelRequested;
    const continueButton = byId("continue-guided-workflow");
    continueButton.disabled = !workflow?.failed || workflow.resumeBlocked || workflow.reconnectUnlockRequired ||
      (workflow.shortcut && !state.statusConnected) || active || state.guidedSubmitting || byId("plan-only").checked || !unlocked;
    continueButton.title = workflow?.resumeBlocked ? "Command outcome is unknown; verify robot state before restarting."
      : "Resume at the failed stage after verifying robot state";
    if (running) {
      const stepLabel = guidedStepLabel(workflowSteps(workflow)[workflow.step], workflow.label);
      button.textContent = `Running ${workflow.label}: ${stepLabel} (${workflow.profileId || (workflow.shortcut ? "Dock disabled" : "server default")}${workflow.requiresTable ? `, table ${workflow.tableId}` : ", no table tag required"})`;
      button.disabled = true;
      const sequenceProgress = workflow.sequenceLength > 1 ? `${workflow.label} (${workflow.sequencePosition}/${workflow.sequenceLength}): ` : "";
      message.textContent = sequenceProgress + (workflow.cancelRequested ? "Stopping sequence; no further steps will start."
        : workflow.operationId && workflowSteps(workflow)[workflow.step] === "fine_align" &&
          (!state.status?.docking_profiles?.available || (workflow.requiresTable && !state.status?.table_profiles?.available))
          ? "Waiting for docking and table profile configuration before advancing."
        : workflow.operationId ? `Waiting for ${stepLabel} to finish.`
          : guidedWaitReason(workflow) || `Starting ${stepLabel} automatically (${workflow.step + 1}/${workflowSteps(workflow).length}).`);
    } else {
      button.textContent = `Dock → Set Height → ${label} → Default Height → Undock`;
      let tableProblem = "";
      try {
        if (manipulationState === "HOLDING" && !manualPlacePoseEnabled()) {
          comboTable(dockingProfileSelection() || state.status?.docking_profiles?.default_profile);
        }
      }
      catch (error) { tableProblem = error.message; }
      button.disabled = !!tableProblem || active || state.guidedSubmitting || !state.status?.docking_profiles?.available || !["EMPTY", "HOLDING"].includes(manipulationState) || byId("plan-only").checked || !unlocked;
      message.textContent = workflow?.message || (byId("plan-only").checked
        ? "Turn off Plan only to run the physical sequence."
        : active ? "Wait for the active operation to finish."
          : tableProblem ? tableProblem
          : !unlocked ? "Unlock physical motion before starting the combo sequence."
          : `Ready for ${label.toLowerCase()}. One confirmation runs all five steps automatically.`);
    }
  }

  async function advanceGuidedWorkflow() {
    if (state.guidedSubmitting || (state.guidedWorkflow && !state.guidedWorkflow.failed && !state.guidedWorkflow.completed)) return;
    state.guidedSubmitting = true;
    renderGuidedWorkflow();
    let starting = true;
    try {
      applyStatus(await api("/api/status"));
      if (state.authenticated === false) return;
      if (!(executionUnlockRemaining() > 0)) {
        throw new Error("Unlock physical motion before starting the combo sequence");
      }
      if (byId("plan-only").checked) throw new Error("Turn off Plan only for this physical sequence");
      if (state.status?.task_admission?.blocked) throw new Error(state.status.task_admission.detail);
      if (["running", "retrying", "paused"].includes(state.status?.manipulation_task?.status)) {
        throw new Error("Wait for the active manipulation task to finish");
      }
      if (state.status?.navigation?.goal_status?.active) throw new Error("Wait for active navigation to finish");
      if ((state.status?.operations || []).some((operation) => activeStatuses.includes(operation.status))) {
        throw new Error("Wait for the active operation to finish");
      }
      const manipulationState = state.status?.manipulation_state?.state;
      if (!["EMPTY", "HOLDING"].includes(manipulationState)) throw new Error("Verify the manipulation state before starting");
      const kind = manipulationState === "HOLDING" ? "place" : "pick";
      const posture = { ...postureTarget(), wait_for_settle: true };
      const placeTarget = kind === "place" && manualPlacePoseEnabled() ? placePose() : null;
      const label = kind === "pick" ? "Pick" : "Place";
      if (!state.status?.docking_profiles?.available) throw new Error("Docking profile configuration is unavailable");
      const { profileId, box, boxTarget } = boxDockChoice(kind === "pick" || !!placeTarget);
      const instanceId = boxTarget ? box.instance_id : null;
      if (boxTarget && !instanceId) throw new Error("Select a fresh visible box before box docking.");
      const requiresTable = kind !== "pick" && !placeTarget;
      const table = requiresTable ? comboTable(profileId) : null;
      const dockSignature = comboDock(profileId);
      const missingNavStatus = !state.status?.navigation?.goal_status?.available;
      if (!window.confirm(`Run the complete physical sequence using docking profile ${profileId || "server default"}${boxTarget ? ` for box ${instanceId}` : ""} ${table ? `and table ${table.id}` : "without a table tag"}: Dock → Set Height (${posture.height.toFixed(3)} m, waist yaw ${posture.waist_yaw.toFixed(4)} rad) → ${label} → Default Height → Undock? All five steps will run automatically.${missingNavStatus ? " Nav2 status is unavailable: confirm Nav2 is idle before starting." : ""}`)) return;
      state.guidedWorkflow = { kind, label, posture, placeTarget, profileId, dockSignature, requiresTable, boxTarget, tableId: table?.id || "", tableSignature: table ? JSON.stringify(table) : null, instanceId,
        step: 0, operationId: null, confirmNav2Idle: missingNavStatus, useManualUnlock: true };
      setError("");
      starting = false;
      state.guidedSubmitting = false;
      await runGuidedStep();
    } catch (error) { setError(error.message); }
    finally { if (starting) state.guidedSubmitting = false; renderGuidedWorkflow(); }
  }

  async function runGuidedStep() {
    const workflow = state.guidedWorkflow;
    if (!workflow || workflow.failed || workflow.completed || workflow.cancelRequested || workflow.operationId || state.guidedSubmitting) return;
    if (state.authenticated === false) return;
    if (guidedWaitReason(workflow)) return;
    const step = workflowSteps(workflow)[workflow.step];
    const resolvingBox = !workflow.instanceId && ((step === "manipulate" && workflow.kind === "pick") ||
      (workflow.shortcut && step === "fine_align" && workflow.boxTarget));
    if (resolvingBox) {
      // Keep this target provisional until its first box command is submitted.
      workflow.instanceId = guidedPickId(workflow);
    }
    const postureStep = ["set_height", "default_height"].includes(step);
    const posture = step === "set_height" ? workflow.posture :
      workflow.returnPosture || { height: 0.64, waist_yaw: 0.0, wait_for_settle: true };
    const payload = postureStep ? { ...posture, confirmed: true }
      : ["navigate_start", "navigate_end"].includes(step) ? { kind: "navigate",
        preset_id: workflow.navigationTargets[step].id, expected_preset_pose: workflow.navigationTargets[step].pose,
        confirmed: true, confirm_nav2_idle: workflow.confirmNav2Idle }
      : ["carry_start", "carry_end"].includes(step) ? { kind: "move_carry_pose",
        target_pose: workflow.shortcut[step].pose === "a" ? 0 : 1, plan_only: false, confirmed: true }
      : step === "fine_align" ? { kind: "fine_align", profile_id: workflow.profileId || "", ...(workflow.boxTarget ? { instance_id: workflow.instanceId } : {}), execute: true, confirmed: true, confirm_nav2_idle: workflow.confirmNav2Idle }
      : step === "undock" ? { kind: "undock", profile_id: workflow.undockProfileId || workflow.profileId || "", confirmed: true, confirm_nav2_idle: workflow.confirmNav2Idle }
      : { kind: workflow.kind, table_profile_id: workflow.tableId, ...(workflow.profileId ? { docking_profile_id: workflow.profileId } : {}), plan_only: false, confirmed: true,
          ...(workflow.kind === "pick" ? { instance_id: workflow.instanceId } : {}),
          ...(workflow.placeTarget ? { place_pose: workflow.placeTarget } : {}) };
    state.guidedSubmitting = true;
    renderGuidedWorkflow();
    let submittingCommand = false;
    try {
      // Start/Continue uses the operator's one-shot unlock for its first command.
      // The confirmation authorizes renewal for the remaining automatic stages.
      if (!workflow.useManualUnlock) {
        await api("/api/unlock/execution", { method: "POST", body: JSON.stringify({ confirmed: true }) });
      }
      if (workflow.failed || workflow.cancelRequested || state.guidedWorkflow !== workflow || state.authenticated === false) return;
      if (step === "manipulate" && workflow.kind === "pick") {
        // The browser snapshot can outlive the detection freshness window.
        applyStatus(await api("/api/status"));
        if (workflow.failed || workflow.cancelRequested || state.guidedWorkflow !== workflow || state.authenticated === false) return;
      }
      if (guidedWaitReason(workflow)) return;
      submittingCommand = true;
      if (workflow.shortcut && resolvingBox) workflow.fixedInstance = true;
      const response = await api(postureStep ? "/api/posture" : "/api/actions", {
        method: "POST", body: JSON.stringify(payload),
      });
      workflow.operationId = response.operation.id;
      workflow.useManualUnlock = false;
      workflow.operationSeen = false;
      if (workflow.cancelRequested) {
        await api("/api/cancel", { method: "POST", body: "{}" });
      } else {
        // A status push may complete the command before its HTTP response.
        updateGuidedWorkflow();
      }
    } catch (error) {
      const staleSelection = error.message === "The selected box is no longer a fresh visible detection; select it again";
      const profileRejected = error.message === "Docking profile configuration is unavailable" ||
        error.message === "Navigation preset changed; review the shortcut destination" ||
        error.message === "Choose a navigation preset or map goal" ||
        error.message.startsWith("Unknown docking profile:") ||
        error.message === "The selected table profile is unavailable" ||
        error.message === "Combo docking profile is unavailable" ||
        error.message === "Table profile configuration is unavailable" ||
        error.message === "Combo table profile does not match the docking tag and frame" ||
        error.message === "Docking profile must match exactly one table tag ID and frame";
      failGuidedWorkflow(workflow, `${workflow.label} stopped at ${guidedStepLabel(step, workflow.label)}: ${error.message}`,
        submittingCommand && !staleSelection && !profileRejected);
      setError(error.message);
    } finally {
      // Preflight may outlive a detection. No box command was sent, so resolve again.
      if (resolvingBox && !submittingCommand) workflow.instanceId = null;
      state.guidedSubmitting = false;
      if (workflow.cancelRequested) failGuidedWorkflow(workflow, `${workflow.label} sequence stopped. Verify the active command outcome before restarting.`, workflow.resumeBlocked);
      renderGuidedWorkflow();
      scheduleGuidedStep();
    }
  }

  async function continueGuidedWorkflow() {
    const workflow = state.guidedWorkflow;
    if (!workflow?.failed || workflow.resumeBlocked || state.guidedSubmitting) return;
    state.guidedSubmitting = true;
    try {
      if (workflow.shortcut && !state.statusConnected) throw new Error("Reconnect live status before continuing the shortcut.");
      if (workflow.reconnectUnlockRequired) throw new Error("Unlock physical motion again after reconnecting before continuing the shortcut.");
      applyStatus(await api("/api/status"));
      if (state.guidedWorkflow !== workflow || state.authenticated === false) return;
      if (workflow.shortcut && (!state.statusConnected || workflow.reconnectUnlockRequired)) {
        throw new Error("Reconnect live status and unlock physical motion again before continuing the shortcut.");
      }
      if (!(executionUnlockRemaining() > 0)) {
        throw new Error("Unlock physical motion before continuing the combo sequence");
      }
      if (byId("plan-only").checked) throw new Error("Turn off Plan only to continue the physical sequence");
      if (state.status?.task_admission?.blocked ||
          ["running", "retrying", "paused"].includes(state.status?.manipulation_task?.status) ||
          state.status?.navigation?.goal_status?.active ||
          (state.status?.operations || []).some((operation) => activeStatuses.includes(operation.status))) {
        throw new Error("Wait for active motion to finish before continuing");
      }
      let nextStep = workflow.step;
      if (workflow.operationId) {
        const operation = state.status?.operations?.find((item) => item.id === workflow.operationId);
        if (!operation || ["ERROR", "OUTCOME_UNKNOWN"].includes(operation.status)) {
          workflow.resumeBlocked = true;
          throw new Error("Command outcome is unknown; verify robot state before restarting");
        }
        if (operation.status === "SUCCEEDED" && operation.result?.success !== false) {
          if (workflowSteps(workflow)[nextStep] === "fine_align") {
            try { bindCompletedDock(workflow, operation); }
            catch (error) {
              workflow.resumeBlocked = !!(state.status?.docking_profiles?.available && state.status?.table_profiles?.available);
              throw error;
            }
          }
          nextStep += 1;
        }
      }
      const expectedState = workflow.kind === "pick"
        ? (nextStep <= workflowSteps(workflow).indexOf("manipulate") ? "EMPTY" : "HOLDING")
        : (nextStep <= workflowSteps(workflow).indexOf("manipulate") ? "HOLDING" : "EMPTY");
      if (state.status?.manipulation_state?.state !== expectedState) {
        throw new Error(`Cannot retry this stage in the current manipulation state; expected ${expectedState}`);
      }
      const missingNavStatus = !state.status?.navigation?.goal_status?.available;
      const label = guidedStepLabel(workflowSteps(workflow)[nextStep], workflow.label) || "completion";
      const configuration = workflow.shortcut ? shortcutDescription(workflow.shortcut)
        : `docking profile ${workflow.profileId || "server default"} and table ${workflow.tableId}`;
      if (!window.confirm(`Continue ${workflow.label} from ${label} using ${configuration}? Verify robot state before retrying. Remaining steps will run automatically.${missingNavStatus ? " Confirm Nav2 is idle." : ""}`)) return;
      workflow.step = nextStep;
      workflow.failed = false;
      workflow.cancelRequested = false;
      workflow.operationId = null;
      workflow.operationSeen = false;
      workflow.message = "";
      workflow.confirmNav2Idle = missingNavStatus;
      workflow.useManualUnlock = true;
      workflow.completed = false;
      if (nextStep === workflowSteps(workflow).length) completeShortcutWorkflow(workflow);
      if (workflowSteps(workflow)[nextStep] === "manipulate" && workflow.kind === "pick" && !workflow.boxTarget && !workflow.fixedInstance) workflow.instanceId = null;
      setError("");
    } catch (error) { setError(error.message); }
    finally {
      state.guidedSubmitting = false;
      renderGuidedWorkflow();
      scheduleGuidedStep();
    }
  }

  async function stopGuidedWorkflow() {
    const workflow = state.guidedWorkflow;
    if (!workflow || workflow.failed || workflow.completed || workflow.cancelRequested) return;
    workflow.cancelRequested = true;
    renderGuidedWorkflow();
    try {
      if (workflow.operationId) await api("/api/cancel", { method: "POST", body: "{}" });
      if (!state.guidedSubmitting) failGuidedWorkflow(workflow, `${workflow.label} sequence stopped. Verify the active command outcome before restarting.`);
    } catch (error) {
      failGuidedWorkflow(workflow, `Sequence stopped; cancellation failed: ${error.message}`);
      setError(error.message);
    } finally { renderGuidedWorkflow(); }
  }
  async function navigate(preset) {
    if (!window.confirm(`Navigate to ${preset.label}?`)) return;
    const confirmNav2Idle = confirmNav2IdleWithoutStatus();
    if (!state.status?.navigation?.goal_status?.available && !confirmNav2Idle) return;
    try { await api("/api/actions", { method: "POST", body: JSON.stringify({ kind: "navigate", preset_id: preset.id, expected_preset_pose: preset.pose, confirmed: true, confirm_nav2_idle: confirmNav2Idle }) }); setError(""); }
    catch (error) { await loadDestinations(); setError(error.message); }
  }
  async function fineAlign(execute) {
    let profileId, instanceId;
    try {
      const choice = boxDockChoice();
      profileId = choice.profileId || "";
      if (choice.boxTarget) instanceId = choice.box.instance_id;
    }
    catch (error) { setError(error.message); return; }
    const label = profileId || `server default (${state.status?.docking_profiles?.default_profile || "automatic"})`;
    if (execute && !window.confirm(`Move the robot in x, y, and yaw to fine-align using docking profile ${label}?`)) return;
    const confirmNav2Idle = confirmNav2IdleWithoutStatus();
    if (!state.status?.navigation?.goal_status?.available && !confirmNav2Idle) return;
    try {
      await api("/api/actions", {
        method: "POST",
        body: JSON.stringify({
          kind: "fine_align",
          profile_id: profileId,
          ...(instanceId ? { instance_id: instanceId } : {}),
          execute,
          confirmed: execute,
          confirm_nav2_idle: confirmNav2Idle,
        }),
      });
      setError("");
    } catch (error) { setError(error.message); }
  }
  async function undock() {
    let profileId;
    try { profileId = dockingProfileSelection(true); }
    catch (error) { setError(error.message); return; }
    if (!window.confirm(`Move the robot backward using ${profileId ? `docking profile ${profileId}` : "the last successful dock profile (or server default after restart)"}?`)) return;
    const confirmNav2Idle = confirmNav2IdleWithoutStatus();
    if (!state.status?.navigation?.goal_status?.available && !confirmNav2Idle) return;
    try {
      await api("/api/actions", {
        method: "POST",
        body: JSON.stringify({
          kind: "undock",
          profile_id: profileId,
          confirmed: true,
          confirm_nav2_idle: confirmNav2Idle,
        }),
      });
      setError("");
    } catch (error) { setError(error.message); }
  }
  function renderManipulationTask(task) {
    const taskActive = ["running", "retrying", "paused"].includes(task.status);
    if (state.continueRequest && (state.continueRequest.task_id !== task.task_id ||
        state.continueRequest.pause_id !== task.pause_id || task.status !== "paused")) {
      state.continueRequest = null;
    }
    byId("manipulation-task-summary").textContent = task.task_id
      ? `${task.status}: ${task.phase || task.action}; attempt ${task.attempt || 0}/${task.maximum_attempts || 0}; object ${task.object_disposition || "unknown"}; completed ${task.last_completed_phase || "none"}`
      : "No active task";
    const elapsed = Number.isFinite(task.task_elapsed_sec)
      ? `${task.task_elapsed_sec.toFixed(1)} s` : "—";
    const controller = Number.isFinite(task.controller_execution_sec)
      ? `${task.controller_execution_sec.toFixed(1)} s (${task.controller_goal_count || 0} trajectories)`
      : "—";
    byId("manipulation-task-timing").textContent =
      `Task elapsed: ${elapsed} · Controller execution: ${controller}` +
      (task.timing_partial ? " · Partial: panel joined mid-task" : "");
    byId("manipulation-task-warning").textContent = task.continue_error || task.failure || "";
    const submitting = state.continueRequest?.task_id === task.task_id &&
      state.continueRequest?.pause_id === task.pause_id;
    byId("continue-manipulation").disabled = !(task.status === "paused" && task.can_continue && task.continue_service_ready && !task.continue_pending && !submitting);
    byId("cancel-manipulation").disabled = !taskActive;
  }
  async function continueManipulation() {
    const task = state.status?.manipulation_task;
    if (task?.status !== "paused" || !task.can_continue || !task.continue_service_ready || task.continue_pending || state.continueRequest) return;
    const request = { task_id: task.task_id, pause_id: task.pause_id };
    const isCurrent = () => state.status?.manipulation_task?.task_id === request.task_id &&
      state.status?.manipulation_task?.pause_id === request.pause_id &&
      state.status?.manipulation_task?.status === "paused";
    state.continueRequest = request;
    renderManipulationTask(task);
    try {
      await api("/api/manipulation/continue", {
        method: "POST", body: JSON.stringify(request),
      });
      if (isCurrent()) setError("");
    } catch (error) { if (isCurrent()) setError(error.message); }
    finally {
      if (state.continueRequest === request) state.continueRequest = null;
      renderManipulationTask(state.status?.manipulation_task || {});
    }
  }
  async function cancelManipulation() {
    const task = state.status?.manipulation_task;
    if (!task?.task_id || !["running", "retrying", "paused"].includes(task.status)) return;
    const workflow = state.guidedWorkflow;
    if (workflow && !workflow.failed && !workflow.completed && workflowSteps(workflow)[workflow.step] === "manipulate") {
      await stopGuidedWorkflow();
      return;
    }
    const taskId = task.task_id;
    const isCurrent = () => state.status?.manipulation_task?.task_id === taskId &&
      ["running", "retrying", "paused"].includes(state.status?.manipulation_task?.status);
    try {
      await api("/api/manipulation/cancel", {
        method: "POST", body: JSON.stringify({ task_id: taskId }),
      });
      if (isCurrent()) setError("");
    } catch (error) { if (isCurrent()) setError(error.message); }
  }
  async function recoverState(requestedState) {
    if (!window.confirm(`Confirm manipulation state: ${requestedState}?`)) return;
    try { await api("/api/recover-state", { method: "POST", body: JSON.stringify({ requested_state: requestedState, confirmed: true }) }); setError(""); } catch (error) { setError(error.message); }
  }
  async function reloadBoxProfiles() {
    const catalog = state.status?.box_profiles_reload?.profiles_file;
    if (!catalog) {
      setError("No box-profile catalog is configured");
      return;
    }
    if (!window.confirm(`Reload box profiles from ${catalog}?`)) return;
    try {
      await api("/api/box-profiles/reload", {
        method: "POST",
        body: JSON.stringify({ confirmed: true }),
      });
      setError("");
    } catch (error) { setError(error.message); }
  }
  function postureTarget() {
    const height = Number(byId("posture-height").value);
    const waistYaw = Number(byId("posture-waist-yaw").value);
    if (!Number.isFinite(height) || height < 0.30 || height > 0.64) {
      throw new Error("Posture height must be within 0.30 to 0.64 m");
    }
    if (!Number.isFinite(waistYaw) || waistYaw < -1.5708 || waistYaw > 1.5708) {
      throw new Error("Posture waist yaw must be within -1.5708 to 1.5708 rad");
    }
    return {
      height,
      waist_yaw: waistYaw,
      wait_for_settle: byId("posture-wait-for-settle").checked,
    };
  }
  async function submitLocomanipulationPosture(target, actionLabel) {
    if (!(executionUnlockRemaining() > 0)) {
      throw new Error("Temporarily unlock one physical motion command first");
    }
    const waitDetail = target.wait_for_settle
      ? " Wait for the direct lower-body feedback window?"
      : " Do not wait for the direct lower-body feedback window?";
    if (!window.confirm(
      `${actionLabel}: height ${target.height.toFixed(3)} m and waist yaw ${target.waist_yaw.toFixed(4)} rad?${waitDetail}`
    )) return false;
    await api("/api/posture", {
      method: "POST",
      body: JSON.stringify({ ...target, confirmed: true }),
    });
    setError("");
    return true;
  }
  async function setLocomanipulationPosture() {
    try {
      await submitLocomanipulationPosture(postureTarget(), "Set posture");
    } catch (error) { setError(error.message); }
  }
  async function resetLocomanipulationPosture() {
    try {
      const submitted = await submitLocomanipulationPosture({
        height: 0.64,
        waist_yaw: 0.0,
        wait_for_settle: byId("posture-wait-for-settle").checked,
      }, "Reset posture to the policy default");
      if (submitted) {
        byId("posture-height").value = "0.64";
        byId("posture-waist-yaw").value = "0.0";
      }
    } catch (error) { setError(error.message); }
  }
  async function releaseLocomanipulationPosture() {
    if (!window.confirm(
      "Release this publisher's posture control? This does not move the robot or restore a pose; RoboJuDo retains its last accepted posture until another source overrides it."
    )) return;
    try {
      await api("/api/posture/release", {
        method: "POST",
        body: JSON.stringify({ confirmed: true }),
      });
      setError("");
    } catch (error) { setError(error.message); }
  }
  async function unlockExecution() {
    if (!window.confirm("Temporarily unlock one physical motion command?")) return;
    const reconnectWorkflow = state.statusConnected ? state.guidedWorkflow : null;
    const connectionGeneration = state.connectionGeneration;
    try {
      await api("/api/unlock/execution", { method: "POST", body: JSON.stringify({ confirmed: true }) });
      applyStatus(await api("/api/status"));
      if (state.statusConnected && state.guidedWorkflow === reconnectWorkflow &&
          state.connectionGeneration === connectionGeneration && reconnectWorkflow?.shortcut) {
        state.guidedWorkflow.reconnectUnlockRequired = false;
        renderGuidedWorkflow();
      }
      setError("");
    } catch (error) { setError(error.message); }
  }
  async function cancelActive() {
    if (!window.confirm("Request cancellation for all active panel goals?")) return;
    if (state.guidedWorkflow && !state.guidedWorkflow.failed && !state.guidedWorkflow.completed) {
      await stopGuidedWorkflow();
      return;
    }
    try { await api("/api/cancel", { method: "POST", body: "{}" }); setError(""); } catch (error) { setError(error.message); }
  }
  async function cancelDockingMotion() {
    if (!window.confirm("Cancel the active docking motion?")) return;
    const workflow = state.guidedWorkflow;
    if (workflow && !workflow.failed && !workflow.completed && ["fine_align", "undock"].includes(workflowSteps(workflow)[workflow.step])) {
      await stopGuidedWorkflow();
      return;
    }
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
  byId("destination-select").addEventListener("change", renderDestinationControls);
  for (const mode of ["new", "edit", "duplicate"]) byId(`destination-${mode}`).addEventListener("click", () => editDestination(mode));
  byId("destination-delete").addEventListener("click", deleteDestination);
  byId("destination-refresh").addEventListener("click", loadDestinations);
  byId("destination-editor").addEventListener("submit", saveDestination);
  byId("destination-editor-close").addEventListener("click", () => {
    if (state.destinationSaving) return;
    state.destinationDraft = null; byId("destination-editor").hidden = true; renderDestinationControls();
  });
  byId("destination-use-robot").addEventListener("click", () => copyDestinationPose("robot"));
  byId("destination-use-map").addEventListener("click", () => copyDestinationPose("map"));
  byId("task-shortcut-select").addEventListener("change", renderTaskShortcuts);
  byId("run-selected-shortcuts").addEventListener("click", runSelectedShortcuts);
  byId("task-shortcut-new").addEventListener("click", () => editTaskShortcut("new"));
  byId("task-shortcut-edit").addEventListener("click", () => editTaskShortcut("edit"));
  byId("task-shortcut-duplicate").addEventListener("click", () => editTaskShortcut("duplicate"));
  byId("task-shortcut-delete").addEventListener("click", deleteTaskShortcut);
  byId("task-shortcut-refresh").addEventListener("click", loadTaskShortcuts);
  byId("task-shortcut-editor").addEventListener("submit", saveTaskShortcut);
  byId("task-shortcut-editor-close").addEventListener("click", () => {
    byId("task-shortcut-editor").hidden = true; state.shortcutDraft = null;
  });
  for (const id of ["shortcut-action", "shortcut-place-mode"]) byId(id).addEventListener("change", renderShortcutChoices);
  byId("shortcut-dock-profile").addEventListener("change", () => {
    const profileId = shortcutProfileSelection("dock");
    if (state.shortcutDraft && !state.shortcutDraft.id &&
        byId("shortcut-undock-profile").value === state.shortcutDraft.dock.profile_id) {
      shortcutProfileOptions("undock", profileId);
    }
    if (state.shortcutDraft) state.shortcutDraft.dock.profile_id = profileId;
  });
  for (const field of ["box", "table", "undock"]) {
    byId(`shortcut-${field}-profile`).addEventListener("change", () => shortcutProfileSelection(field));
  }
  byId("shortcut-box-selection").addEventListener("change", renderShortcutChoices);
  document.querySelectorAll("[data-command]").forEach((button) => button.addEventListener("click", () => submitManipulation(button.dataset.command)));
  byId("visible-box-select").addEventListener("change", (event) => {
    state.selectedBoxId = event.target.value || null;
    const box = state.status?.visible_boxes?.boxes?.find((item) => item.instance_id === state.selectedBoxId);
    const selectedApproach = state.status?.docking_profiles?.profiles?.find(
      (item) => item.id === byId("docking-profile").value);
    if (selectedApproach?.target_source === "box" && !box?.docking_profile_ids?.includes(selectedApproach.id)) {
      byId("docking-profile").value = "";
    }
    renderVisibleBoxes(state.status?.visible_boxes);
    renderDockingProfiles();
    renderGuidedWorkflow();
    scheduleGuidedStep();
    drawMap();
  });
  byId("use-manual-place-pose").addEventListener("change", () => {
    syncManualPlacePoseFields(); renderGuidedWorkflow();
  });
  syncManualPlacePoseFields();
  byId("place-form").addEventListener("submit", (event) => { event.preventDefault(); submitManipulation("place"); });
  byId("dock-manipulate-undock").addEventListener("click", advanceGuidedWorkflow);
  byId("stop-guided-workflow").addEventListener("click", stopGuidedWorkflow);
  byId("continue-guided-workflow").addEventListener("click", continueGuidedWorkflow);
  byId("plan-only").addEventListener("change", () => { renderExecutionState(); renderGuidedWorkflow(); });
  byId("move-carry-a").addEventListener("click", () => submitManipulation("move_carry_pose", { target_pose: 0 }));
  byId("move-carry-b").addEventListener("click", () => submitManipulation("move_carry_pose", { target_pose: 1 }));
  byId("reset-manipulation").addEventListener("click", () => submitManipulation("reset", { confirm_empty: true }));
  byId("continue-manipulation").addEventListener("click", continueManipulation);
  byId("cancel-manipulation").addEventListener("click", cancelManipulation);
  byId("recover-empty").addEventListener("click", () => recoverState("empty"));
  byId("recover-holding").addEventListener("click", () => recoverState("holding"));
  byId("reload-box-profiles").addEventListener("click", reloadBoxProfiles);
  byId("posture-form").addEventListener("submit", (event) => {
    event.preventDefault();
    setLocomanipulationPosture();
  });
  byId("reset-posture").addEventListener("click", resetLocomanipulationPosture);
  byId("release-posture").addEventListener("click", releaseLocomanipulationPosture);
  byId("unlock-execution").addEventListener("click", unlockExecution);
  byId("cancel-active").addEventListener("click", cancelActive);
  byId("cancel-docking-motion").addEventListener("click", cancelDockingMotion);
  byId("clear-costmaps").addEventListener("click", clearCostmaps);
  byId("check-fine-align").addEventListener("click", () => fineAlign(false));
  byId("execute-fine-align").addEventListener("click", () => fineAlign(true));
  byId("execute-undock").addEventListener("click", undock);
  byId("table-profile").addEventListener("change", renderTableProfiles);
  byId("docking-profile").addEventListener("change", () => { renderDockingProfiles(); renderGuidedWorkflow(); });
  byId("undocking-profile").addEventListener("change", renderDockingProfiles);
  byId("select-initial-pose").addEventListener("click", () => setMapMode("initial_pose"));
  byId("select-navigation-goal").addEventListener("click", () => setMapMode("navigate"));
  byId("show-scan").addEventListener("change", drawMap);
  byId("show-camera-previews").addEventListener("change", toggleCameraPreviews);
  byId("clear-map-command").addEventListener("click", clearMapSelection);
  byId("submit-map-command").addEventListener("click", submitMapSelection);
  canvas.addEventListener("pointerdown", startMapSelection);
  canvas.addEventListener("pointermove", updateMapSelection);
  canvas.addEventListener("pointerup", finishMapSelection);
  canvas.addEventListener("pointercancel", clearMapSelection);
})();
