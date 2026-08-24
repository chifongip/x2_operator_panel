(() => {
  const state = { map: null, mapImage: null, presets: [], status: null, poseTrail: [], socket: null, authenticated: false };
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
  function finiteField(id) {
    const value = Number(byId(id).value);
    if (!Number.isFinite(value)) throw new Error(`Enter a valid value for ${id.replace("place-", "")}`);
    return value;
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

  function drawMap() {
    if (!state.map || !state.mapImage) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(state.mapImage, 0, 0);
    const activeNavigation = state.status?.operations?.find((operation) => operation.kind === "navigate" && ["SUBMITTING", "ACTIVE", "CANCEL_REQUESTED"].includes(operation.status));
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
    byId("pick-server").textContent = status.servers.pick ? "Ready" : "Unavailable";
    byId("place-server").textContent = status.servers.place ? "Ready" : "Unavailable";
    byId("navigate-server").textContent = status.servers.navigate ? "Ready" : "Unavailable";
    byId("map-pose-status").textContent = pose.fresh ? "Live map-frame position" : (pose.detail || "Localization unavailable");
    byId("map-coordinates").textContent = pose.available ? `x ${pose.x.toFixed(2)}  y ${pose.y.toFixed(2)}  yaw ${pose.yaw.toFixed(2)}` : "--";
    const unlock = status.execution_unlock_remaining_sec || 0;
    const badge = byId("execution-state");
    badge.textContent = unlock > 0 ? `Unlocked ${Math.ceil(unlock)}s` : "Plan only";
    badge.classList.toggle("unlocked", unlock > 0);
    renderDiagnostics(status.diagnostics);
    renderOperations(status.operations);
    renderAudit(status.audit);
  }

  function renderDiagnostics(diagnostics) {
    byId("diagnostics").textContent = diagnostics?.length ? diagnostics.map((item) => `${item.name}: ${item.message}`).join("\n") : "No diagnostics received";
  }
  function renderOperations(operations) {
    byId("operations").innerHTML = (operations || []).slice(0, 15).map((operation) => `<tr><td>${escapeHtml(operation.kind)}</td><td>${escapeHtml(operation.status)}</td><td>${escapeHtml(operation.stage)}</td><td>${operation.progress == null ? "--" : `${Math.round(operation.progress * 100)}%`}</td><td>${escapeHtml(operation.result?.message || operation.result?.error_msg || operation.detail || "--")}</td></tr>`).join("") || '<tr><td colspan="5">No panel operations</td></tr>';
  }
  function renderAudit(entries) {
    byId("audit-log").innerHTML = (entries || []).slice(0, 20).map((entry) => `<li><time>${escapeHtml(new Date(entry.timestamp).toLocaleTimeString())}</time><strong>${escapeHtml(entry.action)}</strong> ${escapeHtml(entry.outcome)} ${escapeHtml(entry.detail)}</li>`).join("") || "<li>No audit events</li>";
  }
  function renderPresets() {
    const list = byId("preset-list"); list.textContent = "";
    if (!state.presets.length) { list.textContent = "No configured destinations"; return; }
    state.presets.forEach((preset) => { const button = document.createElement("button"); button.type = "button"; button.textContent = preset.label; button.addEventListener("click", () => navigate(preset)); list.appendChild(button); });
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
    socket.onmessage = (event) => { try { const message = JSON.parse(event.data); if (message.type === "status") applyStatus(message.payload); } catch (_) { setError("Received an invalid status update"); } };
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

  function placePose() { return { frame_id: byId("place-frame").value, x: finiteField("place-x"), y: finiteField("place-y"), z: finiteField("place-z"), yaw: finiteField("place-yaw") }; }
  async function submitManipulation(kind, extra = {}) {
    try {
      if (["place", "pick_place"].includes(kind) && !extra.place_pose) {
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
    try { await api("/api/actions", { method: "POST", body: JSON.stringify({ kind: "navigate", preset_id: preset.id, confirmed: true }) }); setError(""); } catch (error) { setError(error.message); }
  }
  async function recoverState(requestedState) {
    if (!window.confirm(`Confirm manipulation state: ${requestedState}?`)) return;
    try { await api("/api/recover-state", { method: "POST", body: JSON.stringify({ requested_state: requestedState, confirmed: true }) }); setError(""); } catch (error) { setError(error.message); }
  }
  async function unlockExecution() {
    if (!window.confirm("Temporarily unlock one physical manipulation command?")) return;
    try { await api("/api/unlock/execution", { method: "POST", body: JSON.stringify({ confirmed: true }) }); setError(""); } catch (error) { setError(error.message); }
  }
  async function cancelActive() {
    if (!window.confirm("Request cancellation for all active panel goals?")) return;
    try { await api("/api/cancel", { method: "POST", body: "{}" }); setError(""); } catch (error) { setError(error.message); }
  }

  byId("login-form").addEventListener("submit", login);
  document.querySelectorAll("[data-command]").forEach((button) => button.addEventListener("click", () => submitManipulation(button.dataset.command)));
  byId("place-form").addEventListener("submit", (event) => { event.preventDefault(); submitManipulation("place"); });
  byId("reset-manipulation").addEventListener("click", () => submitManipulation("reset", { confirm_empty: true }));
  byId("recover-empty").addEventListener("click", () => recoverState("empty"));
  byId("recover-holding").addEventListener("click", () => recoverState("holding"));
  byId("unlock-execution").addEventListener("click", unlockExecution);
  byId("cancel-active").addEventListener("click", cancelActive);
})();
