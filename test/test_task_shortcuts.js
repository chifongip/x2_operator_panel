const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(process.argv[2], "utf8");
const fragment = source.slice(source.indexOf("  const guidedSteps ="), source.indexOf("  async function navigate("));
const clone = (value) => JSON.parse(JSON.stringify(value));
const flush = () => new Promise((resolve) => setImmediate(resolve));

function shortcut(action = "pick") {
  return { id: "shortcut-1", revision: 1, name: "Grey box task", action,
    box: { profile_id: "grey_box", instance_id: "tag:180" },
    dock: { enabled: true, profile_id: "grey_box_dock" },
    posture: { enabled: true, height: 0.48, waist_yaw: 0.2 },
    return_posture: { enabled: true, height: 0.61, waist_yaw: -0.1 },
    undock: { enabled: true, profile_id: "independent_retreat" },
    place: action === "place" ? { mode: "automatic", table_profile_id: "other_table" } : null };
}

function fixture(item = shortcut(), fast = false) {
  const fields = {}, calls = [], confirmations = [];
  function element() {
    let value = "";
    return { get value() { return value; }, set value(next) { value = String(next); },
      checked: false, hidden: false, dataset: {}, options: [], listeners: {},
      get children() { return this.options; },
      setAttribute(name, value) { this[name] = value; },
      addEventListener(name, callback) { this.listeners[name] = callback; },
      replaceChildren(...children) { this.options = children; } };
  }
  const byId = (id) => fields[id] ||= element();
  byId("task-shortcut-select").value = item.id;
  byId("task-shortcut-editor").hidden = true;
  const state = { authenticated: true, statusConnected: true, guidedWorkflow: null, guidedSubmitting: false,
    presets: [{ id: "loading-bay", label: "Loading bay", pose: { x: 1.0, y: 2.0, yaw: 0.5 } },
      { id: "dropoff", label: "Drop off", pose: { x: -1.0, y: -2.0, yaw: -0.5 } }],
    selectedBoxId: "tag:180", shortcutSaving: false, taskShortcuts: { available: true, shortcuts: [clone(item)] },
    status: { operations: [], manipulation_state: { state: item.action === "pick" ? "EMPTY" : "HOLDING" },
      navigation: { goal_status: { available: true, active: false } }, execution_unlock_remaining_sec: 30,
      locomanipulation_posture: { ready: true },
      servers: { navigate: true, move_carry_pose: true }, map_pose: { available: true, fresh: true },
      docking_profiles: { available: true, default_profile: "grey_box_dock", profiles: [
        { id: "grey_box_dock", target_source: "box", undock_mode: "timed_reverse", standoff: 0.4 },
        { id: "independent_retreat", target_source: "fixed", tag_id: 10, tag_frame: "tag10" },
      ] },
      table_profiles: { available: true, default_profile: "other_table", profiles: [
        { id: "other_table", tag_id: 20, tag_frame: "tag20", dimensions: [0.6, 0.4, 0.6] },
      ] },
      box_profiles: { available: true, profile_version: 0, profiles: [{ id: "grey_box" }] },
      visible_boxes: { fresh: true, boxes: [
        { instance_id: "tag:180", profile_id: "grey_box", default_docking_profile: "grey_box_dock", docking_profile_ids: ["grey_box_dock"] },
        { instance_id: "tag:181", profile_id: "grey_box", default_docking_profile: "grey_box_dock", docking_profile_ids: ["grey_box_dock"] },
      ] } },
  };
  function finishOperation(operation, status = "SUCCEEDED") {
    operation.status = status;
    operation.result = { success: status === "SUCCEEDED", profile_id: operation.profile_id,
      instance_id: operation.instance_id };
    if (status === "SUCCEEDED" && ["pick", "place"].includes(operation.kind)) {
      state.status.manipulation_state.state = operation.kind === "pick" ? "HOLDING" : "EMPTY";
    }
  }
  const context = vm.createContext({ state, byId, document: { createElement: element },
    window: { confirm(message) { confirmations.push(message); return true; } },
    executionUnlockRemaining: () => state.status.execution_unlock_remaining_sec,
    manualPlacePoseEnabled: () => false,
    finiteField: (id) => Number(byId(id).value),
    setError: (message) => { context.error = message; },
    applyStatus: (status) => { state.status = status; context.updateGuidedWorkflow(); },
    api: async (path, options = {}) => {
      const payload = JSON.parse(options.body || "{}");
      calls.push({ path, payload });
      if (path === "/api/status") return state.status;
      if (path === "/api/task-shortcuts") return state.taskShortcuts;
      if (path === "/api/presets") return { presets: state.presets };
      if (path === "/api/task-shortcuts/save") {
        const saved = { ...payload, id: payload.id || "new-shortcut", revision: (payload.revision || 0) + 1 };
        return { available: true, shortcut: saved, shortcuts: [saved] };
      }
      if (path === "/api/task-shortcuts/delete") return { available: true, shortcuts: [] };
      if (path === "/api/unlock/execution") { state.status.execution_unlock_remaining_sec = 30; return {}; }
      assert.ok(state.status.execution_unlock_remaining_sec > 0);
      if (payload.kind !== "navigate") state.status.execution_unlock_remaining_sec = 0;
      const operation = { ...payload, id: `operation-${calls.length}`, kind: payload.kind || "posture", status: "ACTIVE" };
      state.status.operations.unshift(operation);
      if (fast) { finishOperation(operation); context.updateGuidedWorkflow(); }
      return { operation };
    },
  });
  vm.runInContext(fragment, context);
  vm.runInContext(source.slice(source.indexOf("  async function unlockExecution()"),
    source.indexOf("  async function cancelActive()")), context);
  vm.runInContext(source.slice(source.indexOf('  byId("task-shortcut-select").addEventListener'),
    source.indexOf('  document.querySelectorAll("[data-command]")')), context);
  const commands = () => calls.filter((call) => ["/api/actions", "/api/posture"].includes(call.path));
  async function finish(status) {
    finishOperation(state.status.operations[0], status);
    context.updateGuidedWorkflow();
    context.updateGuidedWorkflow();
    await flush();
  }
  return { context, state, byId, calls, commands, confirmations, finish };
}

async function sequenceChecks() {
  for (const action of ["pick", "place"]) {
    for (const fast of [false, true]) {
      const f = fixture(shortcut(action), fast);
      await f.context.runTaskShortcut();
      if (fast) await flush();
      else {
        // Editing the definition and selectors cannot alter the active snapshot.
        f.state.taskShortcuts.shortcuts[0].undock.profile_id = "changed_retreat";
        f.state.taskShortcuts.shortcuts[0].posture.height = 0.60;
        f.state.selectedBoxId = "tag:181";
        f.byId("docking-profile").value = "independent_retreat";
        for (let index = 0; index < 5; index++) {
          assert.equal(f.commands().length, index + 1, f.context.error);
          await f.context.runTaskShortcut(); // Double clicking must not dispatch twice.
          await f.finish();
        }
      }
      assert.deepEqual(f.commands().map((call) => call.payload.kind || "posture"),
        ["fine_align", "posture", action, "posture", "undock"]);
      assert.equal(f.commands()[0].payload.instance_id, "tag:180");
      assert.equal(f.commands()[1].payload.height, 0.48);
      assert.equal(f.commands()[3].payload.height, 0.61);
      assert.equal(f.commands()[4].payload.profile_id, "independent_retreat");
      assert.equal(f.commands()[2].payload.docking_profile_id, "grey_box_dock");
      if (action === "pick") assert.equal(f.commands()[2].payload.instance_id, "tag:180");
      else assert.equal(f.commands()[2].payload.table_profile_id, "other_table",
        "Explicit automatic placement can use a different tag from docking");
      assert.equal(f.confirmations.length, 1);
      assert.equal(f.state.guidedWorkflow.completed, true);
      assert.equal(f.calls.filter((call) => call.path === "/api/unlock/execution").length, 4);
    }
  }
  const item = shortcut("place");
  for (const stage of ["dock", "posture", "return_posture", "undock"]) item[stage].enabled = false;
  item.box = null;
  item.place = { mode: "manual", pose: { frame_id: "base_link", x: 0.35, y: 0, z: 0.2, yaw: 0 } };
  const manual = fixture(item);
  manual.state.status.table_profiles.available = false;
  manual.state.status.docking_profiles.available = false;
  await manual.context.runTaskShortcut();
  item.place.pose.x = 99;
  manual.state.taskShortcuts.shortcuts[0].place.pose.x = 99;
  assert.equal(manual.commands().length, 1);
  assert.equal(manual.commands()[0].payload.place_pose.x, 0.35);
  assert.ok(!("docking_profile_id" in manual.commands()[0].payload));
  await manual.finish();
  assert.equal(manual.state.guidedWorkflow.completed, true);
}

async function failureChecks() {
  const lostTarget = fixture();
  await lostTarget.context.runTaskShortcut();
  await lostTarget.finish();
  lostTarget.state.status.visible_boxes.boxes.shift();
  await lostTarget.finish();
  assert.equal(lostTarget.commands().length, 2, "Another tag sharing a profile must never replace the fixed instance");
  assert.match(lostTarget.context.guidedWaitReason(lostTarget.state.guidedWorkflow), /tag:180/);

  const drift = fixture();
  await drift.context.runTaskShortcut();
  drift.state.status.docking_profiles.profiles[1].tag_id = 99;
  await drift.finish();
  assert.equal(drift.commands().length, 1, "An independently selected retreat calibration is also pinned");

  const disconnected = fixture();
  await disconnected.context.runTaskShortcut();
  disconnected.context.pauseShortcutConnection();
  await disconnected.finish();
  assert.equal(disconnected.commands().length, 1);
  disconnected.state.statusConnected = true;
  disconnected.context.updateGuidedWorkflow();
  await flush();
  assert.equal(disconnected.commands().length, 1, "Reconnect must never resume automatically");
  await disconnected.context.continueGuidedWorkflow();
  assert.match(disconnected.context.error, /Unlock/);
  disconnected.state.status.execution_unlock_remaining_sec = 30;
  await disconnected.context.continueGuidedWorkflow();
  assert.equal(disconnected.commands().length, 1, "A still-valid server unlock does not satisfy reconnect review");
  await disconnected.context.unlockExecution();
  await disconnected.context.continueGuidedWorkflow();
  await flush();
  assert.equal(disconnected.commands()[1].path, "/api/posture", "Continue does not replay the completed Dock");

  const race = fixture();
  await race.context.runTaskShortcut();
  const originalApi = race.context.api;
  let release;
  race.context.api = (path, options) => path === "/api/unlock/execution"
    ? new Promise((resolve) => { release = () => resolve(originalApi(path, options)); })
    : originalApi(path, options);
  await race.finish();
  assert.ok(release);
  race.context.pauseShortcutConnection();
  race.state.statusConnected = true;
  release();
  await flush();
  assert.equal(race.commands().length, 1, "Disconnect while awaiting unlock must prevent later submission");

  const unlockRace = fixture();
  await unlockRace.context.runTaskShortcut();
  unlockRace.context.pauseShortcutConnection();
  await unlockRace.finish();
  unlockRace.state.statusConnected = true;
  const unlockApi = unlockRace.context.api;
  let finishUnlock;
  unlockRace.context.api = (path, options) => path === "/api/unlock/execution"
    ? new Promise((resolve) => { finishUnlock = () => resolve(unlockApi(path, options)); })
    : unlockApi(path, options);
  const pendingUnlock = unlockRace.context.unlockExecution();
  unlockRace.context.pauseShortcutConnection();
  unlockRace.state.statusConnected = true;
  finishUnlock();
  await pendingUnlock;
  assert.equal(unlockRace.state.guidedWorkflow.reconnectUnlockRequired, true,
    "A second disconnect invalidates an in-flight manual unlock");

  for (const condition of ["locked", "plan_only", "disconnected", "admission", "failed"]) {
    const f = fixture();
    if (condition === "locked") f.state.status.execution_unlock_remaining_sec = 0;
    if (condition === "plan_only") f.byId("plan-only").checked = true;
    if (condition === "disconnected") f.state.statusConnected = false;
    if (condition === "admission") f.state.status.task_admission = { blocked: true };
    await f.context.runTaskShortcut();
    if (condition === "failed") {
      await f.finish("ABORTED");
      assert.equal(f.state.guidedWorkflow.failed, true);
      assert.equal(f.commands().length, 1);
    } else assert.equal(f.commands().length, 0, condition);
  }
}

async function overlappingSaveChecks() {
  for (const mode of ["new", "edit"]) {
    const f = fixture();
    f.context.editTaskShortcut(mode);
    f.byId("shortcut-name").value = "Save once";
    const api = f.context.api;
    const pending = [];
    f.context.api = (path, options) => new Promise((resolve, reject) => {
      pending.push({ path, options, resolve, reject });
    });
    const first = f.context.saveTaskShortcut({ preventDefault() {} });
    const second = f.context.saveTaskShortcut({ preventDefault() {} });
    assert.equal(pending.length, 1, `${mode}: repeated submit must send only one request`);
    f.context.renderTaskShortcuts();
    assert.equal(f.byId("task-shortcut-save").disabled, true);
    pending[0].resolve(await api(pending[0].path, pending[0].options));
    await Promise.all([first, second]);
    assert.equal(f.state.shortcutSaving, false);
    assert.equal(f.byId("task-shortcut-editor").hidden, true);
    await f.context.saveTaskShortcut({ preventDefault() {} });
    assert.equal(pending.length, 1, "Submitting a closed editor must not resend the saved draft");
    f.context.editTaskShortcut("new");
    assert.equal(f.byId("task-shortcut-save").disabled, false);
  }

  const failed = fixture();
  failed.context.editTaskShortcut("new");
  failed.byId("shortcut-name").value = "Retry save";
  const draft = failed.state.shortcutDraft;
  const api = failed.context.api;
  failed.context.api = async () => { throw new Error("Storage unavailable"); };
  await failed.context.saveTaskShortcut({ preventDefault() {} });
  assert.match(failed.context.error, /Storage unavailable/);
  assert.equal(failed.state.shortcutDraft, draft);
  assert.equal(failed.byId("task-shortcut-editor").hidden, false);
  assert.equal(failed.byId("task-shortcut-save").disabled, false);
  failed.context.api = api;
  await failed.context.saveTaskShortcut({ preventDefault() {} });
  assert.equal(failed.state.shortcutDraft, null, "A failed save must allow retrying");

  const replaced = fixture();
  replaced.context.editTaskShortcut("edit");
  const replacedApi = replaced.context.api;
  let finishSave;
  replaced.context.api = (path, options) => new Promise((resolve) => {
    finishSave = async () => resolve(await replacedApi(path, options));
  });
  const saving = replaced.context.saveTaskShortcut({ preventDefault() {} });
  replaced.context.editTaskShortcut("new");
  const newDraft = replaced.state.shortcutDraft;
  replaced.byId("shortcut-name").value = "Next shortcut";
  await finishSave();
  await saving;
  assert.equal(replaced.state.shortcutDraft, newDraft, "An older save must preserve a newly opened draft");
  assert.equal(replaced.byId("task-shortcut-editor").hidden, false);
  assert.equal(replaced.byId("shortcut-name").value, "Next shortcut");
  assert.equal(replaced.byId("task-shortcut-save").disabled, false);
}

async function shortcutButtonChecks() {
  const f = fixture();
  const second = { ...shortcut(), id: "shortcut-2", name: "Pick second box",
    box: { profile_id: "grey_box", instance_id: "tag:181" } };
  const place = { ...shortcut("place"), id: "place-1", name: "Place grey box" };
  f.state.taskShortcuts.shortcuts.push(place, second);
  f.context.renderTaskShortcuts();
  const list = f.byId("task-shortcut-pick-buttons");
  const placeList = f.byId("task-shortcut-place-buttons");
  assert.deepEqual(list.children.map((button) => button.textContent), ["Grey box task", "Pick second box"]);
  assert.deepEqual(placeList.children.map((button) => button.textContent), ["Place grey box"]);
  assert.equal(placeList.children[0].disabled, false, "Place can be selected before Pick");
  const originalButton = list.children[0];
  f.state.taskShortcuts.shortcuts[0].name = "<img src=x onerror=alert(1)>";
  f.context.renderTaskShortcuts();
  assert.equal(list.children[0], originalButton, "Status updates and renames must preserve button focus");
  assert.equal(originalButton.textContent, "<img src=x onerror=alert(1)>");
  assert.equal(originalButton.className, "shortcut-selection");
  assert.equal(originalButton.disabled, false);
  const requested = [];
  const runner = f.context.runTaskShortcut;
  f.context.runTaskShortcut = (id) => requested.push(id);
  placeList.children[0].listeners.click();
  list.children[1].listeners.click();
  assert.deepEqual(requested, [], "Selection never commands motion");
  assert.equal(f.state.selectedShortcuts.pick, "shortcut-2");
  assert.equal(f.state.selectedShortcuts.place, "place-1");
  assert.equal(f.byId("task-shortcut-select").value, "shortcut-1", "Management selection is independent");
  list.children[1].listeners.click();
  assert.equal(f.state.selectedShortcuts.pick, null);
  list.children[0].listeners.click();
  assert.equal(list.children[0]["aria-pressed"], "true");
  f.context.runTaskShortcut = runner;
  f.byId("task-shortcut-select").value = "shortcut-1";
  await Promise.all([runner("shortcut-2"), runner("shortcut-2")]);
  assert.equal(f.commands().length, 1, "Repeated shortcut clicks must not start concurrent tasks");
  assert.equal(f.commands()[0].payload.instance_id, "tag:181", "Button ID must override the management selection");

  for (const [reason, change] of [
    [/live status/, (f) => { f.state.statusConnected = false; }],
    [/live status/, (f) => { f.state.authenticated = false; }],
    [/Plan only/, (f) => { f.byId("plan-only").checked = true; }],
    [/Unlock/, (f) => { f.state.status.execution_unlock_remaining_sec = 0; }],
    [/EMPTY/, (f) => { f.state.status.manipulation_state.state = "HOLDING"; }],
    [/EMPTY/, (f) => { f.state.status.manipulation_state.state = "UNKNOWN"; }],
    [/active operation/, (f) => { f.state.status.task_admission = { blocked: true }; }],
    [/active operation/, (f) => { f.state.status.operations = [{ status: "CANCEL_REQUESTED" }]; }],
    [/active operation/, (f) => { f.state.status.navigation.goal_status.active = true; }],
    [/active operation/, (f) => { f.state.status.manipulation_task = { status: "paused" }; }],
    [/unavailable/, (f) => { f.state.taskShortcuts.available = false; }],
  ]) {
    const gated = fixture(); change(gated); gated.context.renderTaskShortcuts();
    gated.state.selectedShortcuts.pick = "shortcut-1";
    gated.context.renderTaskShortcuts();
    const button = gated.byId("run-selected-shortcuts");
    assert.equal(button.disabled, true);
    assert.match(button.title, reason);
  }
  const holding = fixture(shortcut("place"));
  holding.context.renderTaskShortcuts();
  assert.equal(holding.byId("task-shortcut-place-buttons").children[0].disabled, false);
  assert.equal(holding.byId("task-shortcut-pick-buttons").children.length, 0);
  assert.match(holding.byId("task-shortcut-pick-buttons").textContent, /No saved Pick shortcuts/);
  holding.context.window.confirm = () => false;
  await holding.context.runTaskShortcut("shortcut-1");
  assert.equal(holding.commands().length, 0);

  const stale = fixture();
  const api = stale.context.api;
  stale.context.api = (path, options) => path === "/api/task-shortcuts"
    ? Promise.resolve({ available: true, shortcuts: [] }) : api(path, options);
  await stale.context.runTaskShortcut("shortcut-1");
  assert.equal(stale.commands().length, 0);
  assert.match(stale.context.error, /unavailable/);
  assert.equal(stale.byId("task-shortcut-pick-buttons").children.length, 0);
  assert.equal(stale.byId("task-shortcut-place-buttons").children.length, 0);

  const removed = fixture();
  removed.context.renderTaskShortcuts();
  removed.state.taskShortcuts.shortcuts = [];
  removed.context.renderTaskShortcuts();
  assert.equal(removed.byId("task-shortcut-pick-buttons").children.length, 0);
  removed.state.taskShortcuts.shortcuts = [second];
  removed.context.renderTaskShortcuts();
  assert.equal(removed.byId("task-shortcut-pick-buttons").children[0].textContent, second.name);
  removed.state.taskShortcuts.shortcuts[0].action = "place";
  removed.state.taskShortcuts.shortcuts[0].place = shortcut("place").place;
  removed.context.renderTaskShortcuts();
  assert.equal(removed.byId("task-shortcut-pick-buttons").children.length, 0,
    "Editing an action must remove the shortcut from its old group");
  assert.equal(removed.byId("task-shortcut-place-buttons").children[0].textContent, second.name);
}

async function editorChecks() {
  const f = fixture();
  f.context.editTaskShortcut("edit");
  assert.equal(f.byId("shortcut-box-instance").value, "180", "Legacy saved instances are displayed as numeric IDs");
  assert.match(f.context.shortcutDescription(f.state.taskShortcuts.shortcuts[0]), /ID 180/);
  assert.equal(f.byId("shortcut-undock-profile").value, "independent_retreat");
  f.byId("shortcut-name").value = "New name";
  f.byId("shortcut-undock-profile").value = "offline_retreat";
  await f.context.saveTaskShortcut({ preventDefault() {} });
  const save = f.calls.find((call) => call.path === "/api/task-shortcuts/save").payload;
  assert.equal(save.id, "shortcut-1");
  assert.equal(save.revision, 1);
  assert.equal(save.undock.profile_id, "offline_retreat");
  assert.equal(save.box.instance_id, "tag:180");
  f.context.editTaskShortcut("duplicate");
  await f.context.saveTaskShortcut({ preventDefault() {} });
  assert.ok(!("id" in f.calls.filter((call) => call.path.endsWith("/save"))[1].payload));
  await f.context.deleteTaskShortcut();
  assert.equal(f.state.taskShortcuts.shortcuts.length, 0);
  assert.equal(f.commands().length, 0, "Editing and saving must not command motion");

  f.context.editTaskShortcut("new");
  assert.equal(f.byId("shortcut-undock-profile").value, "grey_box_dock");
  f.byId("shortcut-dock-profile").value = "initial_retreat";
  f.byId("shortcut-dock-profile").listeners.change({target: f.byId("shortcut-dock-profile")});
  assert.equal(f.byId("shortcut-undock-profile").value, "initial_retreat");
  f.byId("shortcut-undock-profile").value = "explicit_retreat";
  f.byId("shortcut-dock-profile").value = "second_dock";
  f.byId("shortcut-dock-profile").listeners.change();
  assert.equal(f.byId("shortcut-dock-profile").value, "second_dock");
  assert.equal(f.byId("shortcut-undock-profile").value, "explicit_retreat",
    "Changing Dock must preserve an independently edited retreat");
}

async function profileTargetChecks() {
  const item = shortcut();
  item.box.instance_id = null;
  for (const tag of ["tag:180", "tag:181"]) {
    const f = fixture(item);
    f.state.status.visible_boxes.boxes = f.state.status.visible_boxes.boxes.filter((box) => box.instance_id === tag);
    f.state.selectedBoxId = null;
    await f.context.runTaskShortcut();
    assert.equal(f.commands()[0].payload.instance_id, tag, "The same shortcut can dock using either profile tag");
    assert.equal(f.state.taskShortcuts.shortcuts[0].box.instance_id, null, "Runtime binding must not edit the saved shortcut");
    await f.finish();
    await f.finish();
    assert.equal(f.commands()[2].payload.instance_id, tag, "Pick must reuse the box chosen for Dock");
    assert.equal(f.commands()[2].payload.docking_profile_id, "grey_box_dock");
  }

  const multiple = fixture(item);
  multiple.state.selectedBoxId = null;
  await multiple.context.runTaskShortcut();
  assert.equal(multiple.commands().length, 0, "Ambiguous profile tags must require a selection");
  assert.match(multiple.context.guidedWaitReason(multiple.state.guidedWorkflow), /Select.*grey_box/);
  multiple.state.selectedBoxId = "tag:181";
  multiple.context.scheduleGuidedStep();
  await flush();
  assert.equal(multiple.commands()[0].payload.instance_id, "tag:181");
  await multiple.finish();
  multiple.state.status.visible_boxes.boxes = multiple.state.status.visible_boxes.boxes.filter((box) => box.instance_id !== "tag:181");
  multiple.state.selectedBoxId = "tag:180";
  await multiple.finish();
  assert.equal(multiple.commands().length, 2, "Losing a bound tag must not redirect Pick to another tag");
  assert.match(multiple.context.guidedWaitReason(multiple.state.guidedWorkflow), /tag:181/);

  const filtered = fixture(item);
  filtered.state.status.visible_boxes.boxes[0].profile_id = "another_box";
  filtered.state.selectedBoxId = "tag:180";
  await filtered.context.runTaskShortcut();
  assert.equal(filtered.commands()[0].payload.instance_id, "tag:181", "Only the saved box profile can match");
  const unsupported = fixture(item);
  unsupported.state.status.visible_boxes.boxes[0].docking_profile_ids = ["another_dock"];
  await unsupported.context.runTaskShortcut();
  assert.equal(unsupported.commands()[0].payload.instance_id, "tag:181", "Only compatible docking targets can match");

  const pickOnly = clone(item);
  for (const stage of ["dock", "posture", "return_posture", "undock"]) pickOnly[stage].enabled = false;
  const pick = fixture(pickOnly);
  pick.state.selectedBoxId = "tag:181";
  await pick.context.runTaskShortcut();
  assert.equal(pick.commands()[0].payload.kind, "pick");
  assert.equal(pick.commands()[0].payload.instance_id, "tag:181");

  const placeItem = shortcut("place");
  placeItem.box.instance_id = null;
  const place = fixture(placeItem);
  place.state.selectedBoxId = "tag:181";
  await place.context.runTaskShortcut();
  assert.equal(place.commands()[0].payload.instance_id, "tag:181", "Place can choose a docking reference by box profile");

  const navigateItem = clone(item);
  navigateItem.navigate_start = { enabled: true, preset_id: "loading-bay" };
  const navigate = fixture(navigateItem);
  navigate.state.status.visible_boxes.fresh = false;
  await navigate.context.runTaskShortcut();
  assert.equal(navigate.commands()[0].payload.kind, "navigate");
  await navigate.finish();
  assert.equal(navigate.commands().length, 1, "Resolve the box after arrival, rather than before navigation");
  navigate.state.status.visible_boxes.fresh = true;
  navigate.state.selectedBoxId = "tag:181";
  navigate.context.scheduleGuidedStep();
  await flush();
  assert.equal(navigate.commands()[1].payload.instance_id, "tag:181");

  const editor = fixture(item);
  editor.context.editTaskShortcut("edit");
  assert.equal(editor.byId("shortcut-box-selection").value, "profile");
  assert.equal(editor.byId("shortcut-box-instance").disabled, true);
  assert.match(editor.context.shortcutDescription(item), /visible tag at run time/);
  await editor.context.saveTaskShortcut({ preventDefault() {} });
  assert.equal(editor.calls.find((call) => call.path.endsWith("/save")).payload.box.instance_id, null);
  editor.context.editTaskShortcut("new");
  assert.equal(editor.byId("shortcut-box-selection").value, "profile", "New shortcuts should reuse their box profile by default");
  editor.byId("shortcut-box-selection").value = "fixed";
  editor.byId("shortcut-box-selection").listeners.change();
  assert.equal(editor.byId("shortcut-box-instance").disabled, false);
}

async function provisionalTargetChecks() {
  const item = shortcut();
  item.box.instance_id = null;
  for (const stage of ["dock", "posture", "return_posture", "undock"]) item[stage].enabled = false;
  for (const fixed of [false, true]) {
    const pickItem = clone(item);
    if (fixed) pickItem.box.instance_id = "tag:180";
    const f = fixture(pickItem);
    const api = f.context.api;
    let statusReads = 0;
    f.context.api = async (path, options) => {
      if (path === "/api/status" && ++statusReads === 2) {
        f.state.status.visible_boxes.boxes.shift();
        f.state.selectedBoxId = "tag:181";
      }
      return api(path, options);
    };
    await f.context.runTaskShortcut();
    await flush();
    if (fixed) {
      assert.equal(f.commands().length, 0, "Preflight must never replace an explicitly fixed tag");
      assert.equal(f.state.guidedWorkflow.instanceId, "tag:180");
    } else {
      assert.equal(f.commands().length, 1, "A profile target lost during preflight must allow a new detection");
      assert.equal(f.commands()[0].payload.instance_id, "tag:181");
      assert.equal(f.state.taskShortcuts.shortcuts[0].box.instance_id, null);
    }
  }

  const dockItem = shortcut();
  dockItem.box.instance_id = null;
  dockItem.navigate_start = { enabled: true, preset_id: "loading-bay" };
  const dock = fixture(dockItem);
  await dock.context.runTaskShortcut();
  const dockApi = dock.context.api;
  let releaseUnlock;
  dock.context.api = (path, options) => {
    if (path === "/api/unlock/execution" && !releaseUnlock) {
      return new Promise((resolve) => { releaseUnlock = () => resolve(dockApi(path, options)); });
    }
    return dockApi(path, options);
  };
  await dock.finish();
  assert.ok(releaseUnlock, "Dock must be awaiting automatic unlock after navigation");
  dock.state.status.visible_boxes.boxes.shift();
  dock.state.selectedBoxId = "tag:181";
  releaseUnlock();
  await flush();
  assert.deepEqual(dock.commands().map((call) => call.payload.kind), ["navigate", "fine_align"]);
  assert.equal(dock.commands()[1].payload.instance_id, "tag:181", "A lost provisional Dock target must also be released");
  await dock.finish();
  dock.state.status.visible_boxes.boxes = [];
  await dock.finish();
  assert.equal(dock.commands().length, 3, "After Dock is issued, losing its tag must pause Pick");
  assert.match(dock.context.guidedWaitReason(dock.state.guidedWorkflow), /tag:181/);

  for (const submitted of [false, true]) {
    const f = fixture(item);
    const api = f.context.api;
    let statusReads = 0;
    f.context.api = async (path, options) => {
      if (path === "/api/status" && ++statusReads === 2 && !submitted) throw new Error("Status unavailable");
      if (path === "/api/actions" && submitted) throw new Error("Command response unavailable");
      return api(path, options);
    };
    await f.context.runTaskShortcut();
    const workflow = f.state.guidedWorkflow;
    assert.equal(workflow.failed, true);
    assert.equal(!!workflow.resumeBlocked, submitted, "Only a submitted command can have an unknown motion outcome");
    assert.equal(workflow.instanceId, submitted ? "tag:180" : null,
      "Keep the tag if submission might have moved the robot; release it if preflight failed");
    assert.equal(workflow.fixedInstance, submitted);
    if (!submitted) {
      f.context.api = api;
      f.state.status.visible_boxes.boxes.shift();
      f.state.selectedBoxId = "tag:181";
      await f.context.continueGuidedWorkflow();
      await flush();
      assert.equal(f.commands()[0].payload.instance_id, "tag:181", "Continue can resolve another tag after a preflight error");
    }
  }
}

async function numericBoxIdChecks() {
  for (const [id, expected] of [["0", "tag:0"], ["181", "tag:181"], ["00180", "tag:180"], ["2147483647", "tag:2147483647"]]) {
    const f = fixture();
    f.context.editTaskShortcut("edit");
    f.byId("shortcut-box-instance").value = id;
    await f.context.saveTaskShortcut({ preventDefault() {} });
    assert.equal(f.calls.find((call) => call.path.endsWith("/save")).payload.box.instance_id, expected);
    if (id === "181") {
      await f.context.runTaskShortcut();
      assert.equal(f.commands()[0].payload.instance_id, "tag:181", "Dock still uses the ROS instance format");
    }
  }
  for (const id of ["", "-1", "1.5", "1e2", "2147483648", "tag:180", "abc"]) {
    const f = fixture();
    f.context.editTaskShortcut("edit");
    f.byId("shortcut-box-instance").value = id;
    await f.context.saveTaskShortcut({ preventDefault() {} });
    assert.match(f.context.error, /Fixed box ID must be a whole number/);
    assert.equal(f.calls.filter((call) => call.path.endsWith("/save")).length, 0, "Reject invalid IDs before saving");
    assert.equal(f.commands().length, 0);
  }
}

async function dockingDropdownChecks() {
  const f = fixture();
  f.context.editTaskShortcut("edit");
  const profiles = f.state.status.docking_profiles.profiles;
  profiles.push({ id: "box_b_dock", target_source: "box", standoff: 0.5 });
  f.context.renderShortcutChoices();
  for (const field of ["dock", "undock"]) {
    const select = f.byId(`shortcut-${field}-profile`);
    assert.deepEqual(select.options.filter((option) => profiles.some((profile) => profile.id === option.value))
      .map((option) => option.value), profiles.map((profile) => profile.id),
    "Both dropdowns must include every profile, regardless of the selected box or current value");
  }
  assert.equal(f.byId("shortcut-dock-profile").value, "grey_box_dock");
  f.state.status.docking_profiles.available = false;
  f.context.renderShortcutChoices();
  assert.equal(f.byId("shortcut-undock-profile").value, "independent_retreat");
  assert.match(f.byId("shortcut-undock-profile").options.at(-1).textContent, /unavailable/);
  f.context.window.prompt = () => "offline_retreat";
  f.byId("shortcut-undock-profile").value = ".manual";
  f.byId("shortcut-undock-profile").listeners.change();
  assert.equal(f.byId("shortcut-undock-profile").value, "offline_retreat");
  await f.context.saveTaskShortcut({ preventDefault() {} });
  assert.equal(f.calls.find((call) => call.path.endsWith("/save")).payload.undock.profile_id, "offline_retreat");
}

async function boxAndTableDropdownChecks() {
  const f = fixture(shortcut("place"));
  f.state.status.visible_boxes.boxes.push({ profile_id: "blue_box", instance_id: "tag:190" });
  f.state.taskShortcuts.shortcuts.push({ ...shortcut(), id: "saved-red", box: { profile_id: "red_box", instance_id: null } });
  f.state.status.table_profiles.profiles.push({ id: "large_table" });
  f.state.status.box_profiles.profiles.push({ id: "unseen_box" });
  f.context.editTaskShortcut("edit");
  const boxes = f.byId("shortcut-box-profile");
  const tables = f.byId("shortcut-table-profile");
  assert.deepEqual(boxes.options.filter((option) => option.value && option.value !== ".manual").map((option) => option.value),
    ["grey_box", "unseen_box"], "Box choices come from the loaded configuration, including unseen boxes");
  assert.deepEqual(tables.options.filter((option) => option.value && option.value !== ".manual").map((option) => option.value),
    ["other_table", "large_table"], "All discovered tables are offered");
  assert.equal(boxes.value, "grey_box");
  assert.equal(tables.value, "other_table");
  f.state.status.box_profiles = { available: true, profile_version: 1, profiles: [{ id: "replacement_box" }] };
  f.context.renderShortcutChoices();
  assert.equal(boxes.value, "grey_box", "Reload must preserve an open editor's removed selection");
  assert.deepEqual(boxes.options.map((option) => option.value), ["", "replacement_box", ".manual", "grey_box"]);
  assert.match(boxes.options.at(-1).textContent, /unavailable/);
  f.state.status.box_profiles.available = false;
  f.context.renderShortcutChoices();
  assert.deepEqual(boxes.options.filter((option) => option.value && option.value !== ".manual").map((option) => option.value),
    ["blue_box", "grey_box", "red_box"], "Detected and saved names are a fallback while catalog discovery is offline");
  f.state.status.visible_boxes = { fresh: false, boxes: [] };
  f.state.status.table_profiles.available = false;
  f.context.renderShortcutChoices();
  assert.equal(boxes.value, "grey_box", "Losing a detection must not clear the saved box profile");
  assert.equal(tables.value, "other_table", "Losing table discovery must preserve the selected table");
  assert.match(tables.options.at(-1).textContent, /unavailable/);
  for (const [field, expected] of [["box", "offline_box"], ["table", "offline_table"]]) {
    f.context.window.prompt = () => expected;
    f.byId(`shortcut-${field}-profile`).value = ".manual";
    f.byId(`shortcut-${field}-profile`).listeners.change();
    assert.equal(f.byId(`shortcut-${field}-profile`).value, expected);
    f.context.window.prompt = () => null;
    f.byId(`shortcut-${field}-profile`).value = ".manual";
    f.byId(`shortcut-${field}-profile`).listeners.change();
    assert.equal(f.byId(`shortcut-${field}-profile`).value, expected, "Canceling manual entry preserves the selection");
  }
  await f.context.saveTaskShortcut({ preventDefault() {} });
  const saved = f.calls.find((call) => call.path.endsWith("/save")).payload;
  assert.equal(saved.box.profile_id, "offline_box");
  assert.equal(saved.place.table_profile_id, "offline_table");
  assert.equal(f.commands().length, 0, "Selecting or saving profiles must never command motion");

  f.context.editTaskShortcut("edit");
  assert.equal(f.byId("shortcut-box-profile").value, "offline_box");
  assert.equal(f.byId("shortcut-table-profile").value, "offline_table");
}

async function boxProfileNameChecks() {
  for (const name of ["box-a", "Box B", "箱A", ":manual", " box ", "x".repeat(129)]) {
    const item = shortcut();
    item.box = { profile_id: name, instance_id: null };
    const f = fixture(item);
    f.state.status.box_profiles.profiles.push({ id: name });
    f.state.status.visible_boxes.boxes.forEach((box) => { box.profile_id = name; });
    f.context.editTaskShortcut("edit");
    const select = f.byId("shortcut-box-profile");
    assert.equal(select.value, name, "Editing must preserve the server's exact box ID");
    assert.ok(select.options.some((option) => option.value === name));
    f.context.window.prompt = () => { throw new Error("Selecting a configured box must not prompt for manual entry"); };
    select.listeners.change();
    assert.equal(select.value, name, "The previous :manual sentinel can be a real box ID");
    await f.context.saveTaskShortcut({ preventDefault() {} });
    assert.equal(f.calls.find((call) => call.path.endsWith("/save")).payload.box.profile_id, name);
    await f.context.runTaskShortcut();
    await f.finish();
    await f.finish();
    assert.equal(f.commands()[2].payload.kind, "pick");
    assert.equal(f.commands()[2].payload.instance_id, "tag:180", "Profile matching uses the preserved ID");
    assert.equal(f.state.taskShortcuts.shortcuts[0].box.profile_id, name);

    const manual = fixture();
    manual.context.editTaskShortcut("new");
    manual.context.window.prompt = () => name;
    manual.byId("shortcut-box-profile").value = ".manual";
    manual.byId("shortcut-box-profile").listeners.change();
    assert.equal(manual.byId("shortcut-box-profile").value, name, "Manual box names follow the server's naming rules");
    await manual.context.saveTaskShortcut({ preventDefault() {} });
    assert.equal(manual.calls.find((call) => call.path.endsWith("/save")).payload.box.profile_id, name);
  }
  for (const field of ["box", "dock", "undock", "table"]) {
    const f = fixture();
    f.context.editTaskShortcut("new");
    const select = f.byId(`shortcut-${field}-profile`), previous = select.value;
    f.context.window.prompt = () => field === "box" ? "nested.box" : "unsupported-profile";
    select.value = ".manual";
    select.listeners.change();
    assert.equal(select.value, previous);
    assert.match(f.context.error, /Enter a valid .* profile name/);
  }
}

async function navigationCarryChecks() {
  const named = shortcut();
  const destinationId = "a4ba4a18-2c77-4bcd-9e7c-c7d4d3bf9510";
  named.navigate_start = { enabled: true, preset_id: destinationId };
  named.navigate_end = { enabled: true, preset_id: "dropoff" };
  const preview = fixture(named);
  preview.state.presets[0].id = destinationId;
  preview.state.selectedShortcuts = { pick: named.id, place: null };
  preview.context.renderTaskShortcuts();
  assert.match(preview.byId("task-shortcut-preview").textContent, /^Grey box task: Navigate to Loading bay →/);
  assert.match(preview.byId("task-shortcut-preview").textContent, /→ Navigate to Drop off$/);
  assert.ok(!preview.byId("task-shortcut-preview").textContent.includes(destinationId));
  preview.state.presets[0].label = "Renamed loading bay";
  preview.context.renderTaskShortcuts();
  assert.match(preview.byId("task-shortcut-preview").textContent, /^Grey box task: Navigate to Renamed loading bay →/);
  assert.equal(preview.state.taskShortcuts.shortcuts[0].navigate_start.preset_id, destinationId);
  preview.state.presets = [];
  preview.context.renderTaskShortcuts();
  assert.match(preview.byId("task-shortcut-preview").textContent, /^Grey box task: Navigate to unavailable destination →/);

  for (const action of ["pick", "place"]) {
    for (const before of [false, true]) for (const after of [false, true]) for (const carry of [false, true]) {
      const item = shortcut(action);
      item.navigate_start = { enabled: before, preset_id: "loading-bay" };
      item.navigate_end = { enabled: after, preset_id: "dropoff" };
      item[action === "pick" ? "carry_end" : "carry_start"] = { enabled: carry, pose: "b" };
      const f = fixture(item);
      await f.context.runTaskShortcut();
      const expected = [...(before ? ["navigate"] : []), ...(carry && action === "place" ? ["move_carry_pose"] : []),
        "fine_align", "posture", action, "posture", "undock",
        ...(carry && action === "pick" ? ["move_carry_pose"] : []), ...(after ? ["navigate"] : [])];
      for (let index = 0; index < expected.length; index++) {
        assert.equal(f.commands().length, index + 1, f.context.error);
        assert.equal(f.commands()[index].payload.kind || "posture", expected[index]);
        await f.finish();
      }
      const navigation = f.commands().filter((call) => call.payload.kind === "navigate");
      assert.deepEqual(navigation.map((call) => call.payload.preset_id),
        [...(before ? ["loading-bay"] : []), ...(after ? ["dropoff"] : [])]);
      for (const call of navigation) {
        const preset = f.state.presets.find((entry) => entry.id === call.payload.preset_id);
        assert.deepEqual(call.payload.expected_preset_pose, preset.pose);
      }
      for (const call of f.commands().filter((call) => call.payload.kind === "move_carry_pose")) {
        assert.equal(call.payload.target_pose, 1);
        assert.equal(call.payload.plan_only, false);
      }
      assert.equal(f.state.guidedWorkflow.completed, true);
      assert.equal(f.confirmations.length, 1);
    }
  }

  const item = shortcut();
  item.navigate_start = { enabled: true, preset_id: "loading-bay" };
  item.navigate_end = { enabled: true, preset_id: "dropoff" };
  item.carry_end = { enabled: true, pose: "a" };
  const fast = fixture(item, true);
  await fast.context.runTaskShortcut();
  await flush();
  assert.equal(fast.commands().length, 8, "Fast results must not skip or duplicate navigation and carry stages");
  assert.equal(fast.state.guidedWorkflow.completed, true);

  const absentBox = fixture(item);
  absentBox.state.status.visible_boxes.fresh = false;
  await absentBox.context.runTaskShortcut();
  assert.equal(absentBox.commands()[0].payload.kind, "navigate", "The box need not be visible before navigating to it");
  await absentBox.finish();
  assert.equal(absentBox.commands().length, 1, "Dock waits for the fixed box after arrival");
  absentBox.state.status.visible_boxes.fresh = true;
  absentBox.context.updateGuidedWorkflow();
  await flush();
  assert.equal(absentBox.commands()[1].payload.kind, "fine_align");

  for (const status of ["ABORTED", "CANCELED", "OUTCOME_UNKNOWN"]) {
    const failed = fixture(item);
    await failed.context.runTaskShortcut();
    await failed.finish(status);
    assert.equal(failed.commands().length, 1, "A failed navigation cannot advance to Dock");
    assert.equal(failed.state.guidedWorkflow.failed, true);
    if (status === "OUTCOME_UNKNOWN") assert.equal(failed.state.guidedWorkflow.resumeBlocked, true);
  }
  const disconnected = fixture(item);
  await disconnected.context.runTaskShortcut();
  disconnected.context.pauseShortcutConnection();
  await disconnected.finish();
  disconnected.state.statusConnected = true;
  await disconnected.context.unlockExecution();
  await disconnected.context.continueGuidedWorkflow();
  await flush();
  assert.equal(disconnected.commands()[1].payload.kind, "fine_align", "Continue must not replay completed navigation");

  const drift = fixture(item);
  await drift.context.runTaskShortcut();
  drift.state.presets[1].pose.x = 99;
  await drift.finish();
  assert.equal(drift.commands().length, 1, "A changed end destination must not silently replace the confirmed route");

  const missing = fixture(item);
  missing.state.presets = [];
  await missing.context.runTaskShortcut();
  assert.equal(missing.commands().length, 0);
  assert.match(missing.context.error, /unavailable/);

  const editor = fixture(item);
  editor.context.editTaskShortcut("edit");
  assert.equal(editor.byId("shortcut-navigate-start-preset").value, "loading-bay");
  assert.equal(editor.byId("shortcut-carry-start-enabled").disabled, true);
  assert.equal(editor.byId("shortcut-carry-end-enabled").checked, true);
  await editor.context.saveTaskShortcut({preventDefault() {}});
  const saved = editor.calls.find((call) => call.path.endsWith("/save")).payload;
  assert.equal(saved.navigate_end.preset_id, "dropoff");
  assert.equal(saved.carry_end.pose, "a");
  editor.context.editTaskShortcut("edit");
  editor.byId("shortcut-action").value = "place";
  editor.context.renderShortcutChoices();
  assert.equal(editor.byId("shortcut-carry-end-enabled").checked, false);
  assert.equal(editor.byId("shortcut-carry-start-enabled").disabled, false);
}

async function pairedShortcutChecks() {
  function pair(fast = false) {
    const f = fixture(shortcut(), fast);
    const place = shortcut("place");
    place.id = "place-1"; place.name = "Place box";
    f.state.taskShortcuts.shortcuts.push(place);
    f.state.selectedShortcuts = { pick: "shortcut-1", place: "place-1" };
    return f;
  }
  for (const fast of [false, true]) {
    const f = pair(fast);
    await f.context.runSelectedShortcuts();
    if (fast) await flush();
    else {
      f.state.taskShortcuts.shortcuts[1].posture.height = 0.60;
      for (let index = 0; index < 10; index++) {
        assert.equal(f.commands().length, index + 1);
        await f.finish();
      }
    }
    assert.deepEqual(f.commands().map((call) => call.payload.kind || "posture"),
      ["fine_align", "posture", "pick", "posture", "undock", "fine_align", "posture", "place", "posture", "undock"]);
    assert.equal(f.commands()[6].payload.height, 0.48, "Place uses its initial snapshot");
    assert.equal(f.confirmations.length, 1);
    assert.equal(f.state.guidedWorkflow.completed, true);
    assert.equal(f.state.status.manipulation_state.state, "EMPTY");
  }
  const extended = pair();
  extended.state.taskShortcuts.shortcuts[0].carry_end = { enabled: true, pose: "a" };
  extended.state.taskShortcuts.shortcuts[0].navigate_end = { enabled: true, preset_id: "loading-bay" };
  extended.state.taskShortcuts.shortcuts[1].navigate_start = { enabled: true, preset_id: "dropoff" };
  extended.state.taskShortcuts.shortcuts[1].carry_start = { enabled: true, pose: "b" };
  await extended.context.runSelectedShortcuts();
  for (let index = 0; index < 14; index++) await extended.finish();
  assert.deepEqual(extended.commands().map((call) => call.payload.kind || "posture"),
    ["fine_align", "posture", "pick", "posture", "undock", "move_carry_pose", "navigate",
      "navigate", "move_carry_pose", "fine_align", "posture", "place", "posture", "undock"]);
  assert.equal(extended.state.guidedWorkflow.completed, true);

  const waiting = pair();
  await waiting.context.runSelectedShortcuts();
  for (let index = 0; index < 4; index++) await waiting.finish();
  waiting.state.status.manipulation_state.state = "EMPTY";
  await waiting.finish();
  assert.equal(waiting.commands().length, 5, "Place waits for HOLDING even after successful Pick stages");
  waiting.state.status.manipulation_state.state = "HOLDING";
  waiting.context.updateGuidedWorkflow();
  await flush();
  assert.equal(waiting.commands().length, 6);
  waiting.state.status.docking_profiles.profiles[1].tag_id = 99;
  await waiting.finish();
  assert.equal(waiting.commands().length, 6, "Place rejects changed captured calibration");

  const unknown = pair();
  await unknown.context.runSelectedShortcuts();
  for (let index = 0; index < 5; index++) await unknown.finish();
  await unknown.finish("OUTCOME_UNKNOWN");
  assert.equal(unknown.state.guidedWorkflow.resumeBlocked, true);
  await unknown.context.continueGuidedWorkflow();
  assert.equal(unknown.commands().length, 6);

  const invalid = pair();
  invalid.state.taskShortcuts.shortcuts[1].place.table_profile_id = "missing";
  await invalid.context.runSelectedShortcuts();
  assert.equal(invalid.commands().length, 0, "Validate Place before starting Pick");

  const failed = pair();
  await failed.context.runSelectedShortcuts();
  await failed.finish("ABORTED");
  assert.equal(failed.commands().length, 1);
  failed.state.status.execution_unlock_remaining_sec = 30;
  await failed.context.continueGuidedWorkflow();
  for (let index = 0; index < 10; index++) await failed.finish();
  assert.equal(failed.state.guidedWorkflow.completed, true);
  assert.equal(failed.commands().filter((call) => call.payload.kind === "pick").length, 1);

  for (const pause of ["stop", "disconnect"]) {
    const f = pair();
    await f.context.runSelectedShortcuts();
    for (let index = 0; index < 4; index++) await f.finish();
    if (pause === "stop") await f.context.stopGuidedWorkflow();
    else f.context.pauseShortcutConnection();
    await f.finish();
    assert.equal(f.commands().length, 5, "No automatic handoff after interruption");
    if (pause === "disconnect") {
      f.state.statusConnected = true;
      await f.context.unlockExecution();
    } else f.state.status.execution_unlock_remaining_sec = 30;
    await f.context.continueGuidedWorkflow();
    await flush();
    assert.equal(f.commands().length, 6, "Continue advances past completed Pick");
    assert.equal(f.state.guidedWorkflow.kind, "place");
    for (let index = 0; index < 5; index++) await f.finish();
    assert.equal(f.state.guidedWorkflow.completed, true);
  }
  const removed = pair();
  removed.state.taskShortcuts.shortcuts[0].action = "place";
  removed.state.taskShortcuts.shortcuts[0].place = shortcut("place").place;
  removed.context.renderTaskShortcuts();
  assert.equal(removed.state.selectedShortcuts.pick, null);
  removed.state.taskShortcuts.shortcuts = [];
  removed.context.renderTaskShortcuts();
  assert.equal(removed.state.selectedShortcuts.place, null);
}

(async () => { await pairedShortcutChecks(); await sequenceChecks(); await failureChecks(); await overlappingSaveChecks(); await shortcutButtonChecks(); await editorChecks(); await profileTargetChecks(); await provisionalTargetChecks(); await numericBoxIdChecks(); await dockingDropdownChecks(); await boxAndTableDropdownChecks(); await boxProfileNameChecks(); await navigationCarryChecks(); })()
  .catch((error) => { console.error(error); process.exitCode = 1; });
