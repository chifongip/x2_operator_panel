const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// Exercise the real browser scheduler with authenticated HTTP and status pushes.
const source = fs.readFileSync(process.argv[2], "utf8");
const fragment = source.slice(source.indexOf("  const guidedSteps ="), source.indexOf("  async function navigate("));
const flush = () => new Promise((resolve) => setImmediate(resolve));

function fixture(kind, fast = false, completionStatus = "SUCCEEDED") {
  const calls = [], fields = {}, confirmations = [];
  let now = 0, timerId = 0;
  const timers = new Map();
  const state = {
    authenticated: true, guidedWorkflow: null, guidedSubmitting: false,
    selectedBoxId: "box-1",
    status: {
      operations: [{ id: "navigation-1", kind: "navigate", status: "SUCCEEDED" }],
      manipulation_state: { state: kind === "pick" ? "EMPTY" : "HOLDING" },
      navigation: { goal_status: { available: true, active: false, actions: {
        navigate_to_pose: { available: true, active: false },
        navigate_through_poses: { available: false, active: null, connected: true },
      } } },
      docking_profiles: { available: true, default_profile: "default", profiles: [
        { id: "default", tag_id: 9, tag_frame: "tag9" }, { id: "offset", tag_id: 9, tag_frame: "tag9" },
      ] },
      table_profiles: { available: true, default_profile: "default", profiles: [
        { id: "default", tag_id: 9, tag_frame: "tag9", dimensions: [0.6, 0.4, 0.6] },
        { id: "second", tag_id: 10, tag_frame: "tag10", dimensions: [0.8, 0.5, 0.7] },
      ] },
      locomanipulation_posture: { ready: true }, execution_unlock_remaining_sec: 30,
      visible_boxes: { fresh: true, boxes: [{ instance_id: "box-1" }] },
    },
  };
  const context = vm.createContext({
    state,
    byId: (id) => fields[id] ||= { checked: false },
    performance: { now: () => now },
    finiteField: (id) => Number(fields[id]?.value ?? 0),
    window: { setTimeout(callback, ms) { const id = ++timerId; timers.set(id, { callback, at: now + ms }); return id; },
      clearTimeout(id) { timers.delete(id); }, confirm: (message) => { confirmations.push(message); return true; } },
    setError: (message) => { context.error = message; },
    applyStatus: (status) => { state.status = status; context.updateGuidedWorkflow(); },
    executionUnlockRemaining: () => state.status.execution_unlock_remaining_sec,
    postureTarget: () => ({ height: 0.48, waist_yaw: 0.2 }),
    manualPlacePoseEnabled: () => false,
    api: async (path, options = {}) => {
      const payload = JSON.parse(options.body || "{}");
      calls.push({ path, payload });
      if (path === "/api/status") return state.status;
      if (path === "/api/unlock/execution") {
        state.status.execution_unlock_remaining_sec = 30;
        return {};
      }
      if (path === "/api/cancel") return { operation_ids: ["active"] };
      const operation = { id: `operation-${calls.length}`, kind: payload.kind || "set_locomanipulation_posture", status: "ACTIVE" };
      assert.ok(state.status.execution_unlock_remaining_sec > 0 || context.administratorModeActive(), "A physical command must have authorization");
      state.status.execution_unlock_remaining_sec = 0;
      state.status.operations.unshift(operation);
      if (fast) {
        finishOperation(operation, completionStatus, completionStatus === "SUCCEEDED");
        // WebSocket result arrives before the HTTP submission response.
        context.updateGuidedWorkflow();
      }
      return { operation };
    },
  });
  vm.runInContext(source.slice(source.indexOf("  function administratorModeActive("),
    source.indexOf("  function renderExecutionState(")), context);
  vm.runInContext(fragment, context);
  function finishOperation(operation, status, success, publishState = true) {
    operation.status = status;
    operation.result = { success, ...(operation.kind === "fine_align" ? { profile_id: "offset" } : {}) };
    if (publishState && operation.kind === kind && success && status === "SUCCEEDED") {
      state.status.manipulation_state.state = kind === "pick" ? "HOLDING" : "EMPTY";
    }
  }
  async function finish(status = "SUCCEEDED", success = true, publishState = true) {
    finishOperation(state.status.operations[0], status, success, publishState);
    context.updateGuidedWorkflow();
    context.updateGuidedWorkflow(); // Duplicate snapshots must not dispatch twice.
    await flush();
  }
  const commands = () => calls.filter((call) => ["/api/actions", "/api/posture"].includes(call.path));
  async function advance(ms) {
    const end = now + ms;
    while (true) {
      const next = [...timers.entries()].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      now = next[1].at; timers.delete(next[0]); next[1].callback(); await flush();
    }
    now = end; await flush();
  }
  return { context, state, calls, fields, confirmations, commands, finish, advance };
}

async function fullSequence(kind, fast = false) {
  const f = fixture(kind, fast);
  await f.context.advanceGuidedWorkflow(); // The only combo-button click.
  if (fast) await flush();
  else {
    for (let step = 0; step < 5; step++) {
      assert.equal(f.commands().length, step + 1, f.context.error);
      await f.context.advanceGuidedWorkflow(); // Double click must be ignored.
      assert.equal(f.commands().length, step + 1);
      await f.finish();
    }
  }
  assert.equal(f.confirmations.length, 1, "The entire sequence needs one confirmation");
  assert.equal(f.confirmations[0].includes("Nav2 status is unavailable"), false,
    "Unknown secondary status must not warn when aggregate single-pose status is available");
  assert.equal(f.commands()[0].payload.confirm_nav2_idle, false);
  assert.deepEqual(f.commands().map((call) => call.payload.kind || "posture"),
    ["fine_align", "posture", kind, "posture", "undock"]);
  assert.equal(f.calls.filter((call) => call.path === "/api/unlock/execution").length, 4,
    "Dock must use the manual unlock; only later stages renew automatically");
  assert.deepEqual(f.commands()[1].payload,
    { height: 0.48, waist_yaw: 0.2, wait_for_settle: true, confirmed: true });
  assert.deepEqual(f.commands()[3].payload,
    { height: 0.64, waist_yaw: 0.0, wait_for_settle: true, confirmed: true });
  assert.equal(f.commands()[0].payload.profile_id, "default");
  assert.equal(f.commands()[4].payload.profile_id, "offset", "Undock uses the resolved successful Dock profile");
  assert.equal(f.state.guidedWorkflow.completed, true);
  f.context.renderGuidedWorkflow();
  assert.equal(f.fields["dock-manipulate-undock"].disabled, true,
    "Completion must require a fresh manual unlock for another sequence");
  f.state.status.execution_unlock_remaining_sec = 30;
  f.context.renderGuidedWorkflow();
  assert.equal(f.fields["dock-manipulate-undock"].disabled, false);
}

async function tableBindingChecks() {
  const changedBeforeRetreat = fixture("pick");
  await changedBeforeRetreat.context.advanceGuidedWorkflow();
  await changedBeforeRetreat.finish();
  await changedBeforeRetreat.finish();
  await changedBeforeRetreat.finish();
  changedBeforeRetreat.state.status.docking_profiles.profiles[1].tag_id = 10;
  changedBeforeRetreat.state.status.docking_profiles.profiles[1].tag_frame = "tag10";
  await changedBeforeRetreat.finish();
  assert.equal(changedBeforeRetreat.commands().length, 4,
    "A restarted docking server must not redirect the retreat to another table");

  const missing = fixture("pick");
  missing.state.status.table_profiles.profiles = [];
  missing.state.status.table_profiles.available = false;
  await missing.context.advanceGuidedWorkflow();
  assert.equal(missing.commands().length, 1, "Pick can dock without a table profile");
  await missing.finish();
  await missing.finish();
  assert.equal(missing.commands()[2].payload.kind, "pick");

  const distinct = fixture("place");
  distinct.state.status.docking_profiles.profiles[1].tag_id = 10;
  distinct.state.status.docking_profiles.profiles[1].tag_frame = "tag10";
  distinct.fields["docking-profile"] = { value: "offset" };
  distinct.context.manualPlacePoseEnabled = () => true;
  distinct.context.placePose = () => ({ frame_id: "base_link", x: 0.35, y: 0, z: 0.17, yaw: 0 });
  await distinct.context.advanceGuidedWorkflow();
  assert.equal(distinct.state.guidedWorkflow.tableId, "");
  assert.match(distinct.confirmations[0], /without a table tag/);
  await distinct.finish();
  distinct.fields["table-profile"] = { value: "default" };
  await distinct.finish();
  const payload = distinct.commands()[2].payload;
  assert.equal(payload.table_profile_id, "");
  assert.equal(payload.docking_profile_id, "offset");
  assert.equal(payload.place_pose.x, 0.35);

  const mismatch = fixture("pick");
  mismatch.state.status.docking_profiles.profiles[1].tag_id = 10;
  mismatch.state.status.docking_profiles.profiles[1].tag_frame = "tag10";
  await mismatch.context.advanceGuidedWorkflow();
  await mismatch.finish(); // The fake Dock resolves offset, which now targets another table.
  assert.equal(mismatch.commands().length, 1);
  assert.equal(mismatch.state.guidedWorkflow.resumeBlocked, true);

  const dockDisconnected = fixture("place");
  await dockDisconnected.context.advanceGuidedWorkflow();
  dockDisconnected.state.status.table_profiles.available = false;
  await dockDisconnected.finish();
  assert.equal(dockDisconnected.commands().length, 1);
  assert.equal(!!dockDisconnected.state.guidedWorkflow.failed, false);
  dockDisconnected.state.status.table_profiles.available = true;
  dockDisconnected.context.updateGuidedWorkflow();
  await flush();
  assert.equal(dockDisconnected.commands().length, 2);

  const changed = fixture("place");
  await changed.context.advanceGuidedWorkflow();
  changed.state.status.table_profiles.profiles[0].dimensions[0] = 1.0;
  await changed.finish();
  assert.equal(changed.commands().length, 1);
  assert.equal(changed.state.guidedWorkflow.resumeBlocked, true);

  const disconnected = fixture("place");
  await disconnected.context.advanceGuidedWorkflow();
  await disconnected.finish();
  disconnected.state.status.table_profiles.available = false;
  await disconnected.finish();
  assert.equal(disconnected.commands().length, 2);
  disconnected.state.status.table_profiles.available = true;
  disconnected.context.updateGuidedWorkflow();
  await flush();
  assert.equal(disconnected.commands()[2].payload.table_profile_id, "default");
}

async function boxBindingChecks() {
  for (const race of ["refresh", "retry"]) {
    const bound = fixture("pick");
    bound.state.status.docking_profiles.profiles.forEach((p) => p.target_source = "box");
    const targets = ["tag:17", "tag:42"].map((instance_id) => ({
      instance_id, docking_profile_ids: ["default", "offset"], default_docking_profile: "default",
    }));
    bound.state.status.visible_boxes.boxes = targets;
    bound.state.selectedBoxId = "tag:17";
    await bound.context.advanceGuidedWorkflow();
    bound.state.status.operations[0].status = "SUCCEEDED";
    bound.state.status.operations[0].result = { success: true, profile_id: "offset", instance_id: "tag:17" };
    bound.context.updateGuidedWorkflow();
    await flush();
    const api = bound.context.api;
    let interrupt = true;
    bound.context.api = async (path, options) => {
      if (interrupt && bound.state.guidedWorkflow.step === 2) {
        if (race === "refresh" && path === "/api/status") {
          bound.state.status.visible_boxes.boxes = [targets[1]];
        } else if (race === "retry" && path === "/api/actions") {
          throw new Error("The selected box is no longer a fresh visible detection; select it again");
        }
      }
      return api(path, options);
    };
    await bound.finish();
    assert.equal(bound.commands().length, 2);
    assert.equal(bound.state.guidedWorkflow.instanceId, "tag:17",
      "Losing a detection during refresh must retain the docked box identity");
    interrupt = false;
    bound.state.selectedBoxId = "tag:42";
    bound.state.status.visible_boxes.boxes = targets;
    if (race === "retry") {
      bound.state.status.execution_unlock_remaining_sec = 30;
      await bound.context.continueGuidedWorkflow();
    } else {
      bound.context.updateGuidedWorkflow();
    }
    await flush();
    assert.equal(bound.commands()[2].payload.instance_id, "tag:17",
      "Refresh and Continue must pick the same box used for docking");
  }
  const f = fixture("pick");
  f.state.status.table_profiles.available = false;
  f.state.status.docking_profiles.profiles[0].target_source = "box";
  f.state.status.docking_profiles.profiles[1].target_source = "box";
  f.state.status.visible_boxes.boxes = [
    { instance_id: "tag:17", docking_profile_ids: ["default", "offset"], default_docking_profile: "default" },
    { instance_id: "tag:42", docking_profile_ids: ["offset"], default_docking_profile: "offset" },
  ];
  f.state.selectedBoxId = "tag:17";
  await f.context.advanceGuidedWorkflow();
  assert.equal(f.commands()[0].payload.instance_id, "tag:17");
  f.state.selectedBoxId = "tag:42";
  f.state.status.operations[0].status = "SUCCEEDED";
  f.state.status.operations[0].result = { success: true, profile_id: "offset", instance_id: "tag:17" };
  f.context.updateGuidedWorkflow();
  await flush();
  await f.finish();
  assert.equal(f.commands()[2].payload.instance_id, "tag:17");
  assert.equal(f.commands()[2].payload.docking_profile_id, "offset");
  const other = fixture("pick");
  other.state.status.docking_profiles.profiles.forEach((p) => p.target_source = "box");
  other.state.status.visible_boxes.boxes = [{ instance_id: "tag:42", docking_profile_ids: ["offset"], default_docking_profile: "offset" }];
  other.state.selectedBoxId = "tag:42";
  await other.context.advanceGuidedWorkflow();
  assert.equal(other.commands()[0].payload.profile_id, "offset");
  assert.equal(other.commands()[0].payload.instance_id, "tag:42");
  const mismatch = fixture("pick");
  mismatch.state.status.docking_profiles.profiles.forEach((p) => p.target_source = "box");
  mismatch.state.status.visible_boxes.boxes[0].docking_profile_ids = ["default", "offset"];
  mismatch.state.status.visible_boxes.boxes[0].default_docking_profile = "default";
  await mismatch.context.advanceGuidedWorkflow();
  await mismatch.finish();
  assert.equal(mismatch.commands().length, 1, "A dock result must identify the same box");
  assert.equal(mismatch.state.guidedWorkflow.resumeBlocked, true);
}

async function quickDelayChecks() {
  for (const kind of ["pick", "place"]) {
    const f = fixture(kind);
    f.fields["combo-delay"] = { value: "1.5" };
    await f.context.advanceGuidedWorkflow();
    assert.match(f.confirmations[0], /Wait 1.5 s/);
    f.fields["combo-delay"].value = "0";
    await f.finish(); await f.finish();
    await f.advance(1499); assert.equal(f.commands().length, 2);
    await f.advance(1); assert.equal(f.commands()[2].payload.kind, kind);
  }
  const f = fixture("place");
  f.fields["combo-delay"] = { value: "1" };
  await f.context.advanceGuidedWorkflow(); await f.finish(); await f.finish();
  f.state.status.manipulation_state.state = "UNKNOWN";
  await f.advance(1000);
  assert.equal(f.commands().length, 2, "Admission checks must still block after delay");
}

(async () => { await quickDelayChecks();
  await tableBindingChecks();
  await boxBindingChecks();
  for (const kind of ["pick", "place"]) {
    await fullSequence(kind);
    await fullSequence(kind, true);
  }
  for (const kind of ["pick", "place"]) {
    for (const navigationStatus of [null, "FAILED", "CANCELED"]) {
      const standalone = fixture(kind, true);
      standalone.state.status.operations = navigationStatus
        ? [{ id: "old-navigation", kind: "navigate", status: navigationStatus }] : [];
      standalone.context.renderGuidedWorkflow();
      assert.equal(standalone.fields["dock-manipulate-undock"].disabled, false);
      await standalone.context.advanceGuidedWorkflow();
      await flush();
      assert.equal(standalone.commands().length, 5, "Navigation history must not gate the sequence");
      assert.equal(standalone.state.guidedWorkflow.completed, true);
    }
  }
  const repeat = fixture("pick");
  repeat.state.status.operations = [];
  await repeat.context.advanceGuidedWorkflow();
  for (let step = 0; step < 5; step++) await repeat.finish();
  repeat.state.status.execution_unlock_remaining_sec = 30;
  await repeat.context.advanceGuidedWorkflow();
  assert.equal(repeat.commands().length, 6, "A new sequence can start at the same location");
  assert.equal(repeat.state.guidedWorkflow.kind, "place");
  for (const status of ["ABORTED", "CANCELED", "FAILED", "OUTCOME_UNKNOWN"]) {
    const failure = fixture("pick", true, status);
    await failure.context.advanceGuidedWorkflow();
    await flush();
    assert.equal(failure.state.guidedWorkflow.failed, true);
    assert.equal(failure.commands().length, 1);
  }
  const heightFailure = fixture("pick");
  await heightFailure.context.advanceGuidedWorkflow();
  for (let i = 0; i < 3; i++) await heightFailure.finish();
  await heightFailure.finish("SUCCEEDED", false);
  assert.equal(heightFailure.state.guidedWorkflow.failed, true);
  assert.equal(heightFailure.commands().length, 4, "A failed height reset must prevent Undock");
  await heightFailure.context.continueGuidedWorkflow();
  assert.equal(heightFailure.commands().length, 4, "Locked Continue must not submit motion");
  heightFailure.state.status.execution_unlock_remaining_sec = 30;
  await heightFailure.context.continueGuidedWorkflow();
  await flush();
  assert.equal(heightFailure.commands().length, 5);
  assert.equal(heightFailure.commands()[4].path, "/api/posture",
    "Continue must retry Default Height without repeating Dock or Pick");
  await heightFailure.finish();
  assert.equal(heightFailure.commands()[5].payload.kind, "undock");

  const paused = fixture("pick");
  await paused.context.advanceGuidedWorkflow();
  await paused.finish();
  await paused.finish();
  paused.state.status.manipulation_task = { status: "paused" };
  paused.context.updateGuidedWorkflow();
  await flush();
  assert.equal(paused.commands().length, 3, "A paused manipulation action must hold the sequence");
  await paused.finish(); // Continued action eventually succeeds.
  assert.equal(paused.commands().length, 3, "A still-paused task report must block the next task");
  paused.state.status.manipulation_task = { status: "completed" };
  paused.context.updateGuidedWorkflow();
  await flush();
  assert.equal(paused.commands().length, 4);

  const externalTask = fixture("pick");
  externalTask.state.status.task_admission = { blocked: true, detail: "Finish or cancel the active fine_align task first" };
  externalTask.context.renderGuidedWorkflow();
  assert.equal(externalTask.fields["dock-manipulate-undock"].disabled, true);
  await externalTask.context.advanceGuidedWorkflow();
  assert.equal(externalTask.commands().length, 0, "An external task blocks a new sequence");

  const delayedState = fixture("pick");
  await delayedState.context.advanceGuidedWorkflow();
  await delayedState.finish();
  await delayedState.finish();
  await delayedState.finish("SUCCEEDED", true, false);
  assert.equal(delayedState.commands().length, 3, "Wait for the HOLDING state after Pick result");
  delayedState.state.status.manipulation_state.state = "HOLDING";
  delayedState.context.updateGuidedWorkflow();
  await flush();
  assert.equal(delayedState.commands().length, 4);

  const named = fixture("pick");
  named.fields["docking-profile"] = { value: "offset" };
  await named.context.advanceGuidedWorkflow();
  assert.equal(named.commands()[0].payload.profile_id, "offset");
  assert.match(named.confirmations[0], /docking profile offset/);
  named.fields["docking-profile"].value = "default";
  named.state.status.docking_profiles.default_profile = "default";
  for (let step = 0; step < 4; step++) await named.finish();
  assert.equal(named.commands()[4].payload.profile_id, "offset", "Form changes cannot change retreat selection");

  const profileRetry = fixture("pick");
  profileRetry.fields["docking-profile"] = { value: "offset" };
  await profileRetry.context.advanceGuidedWorkflow();
  await profileRetry.finish("ABORTED", false);
  profileRetry.fields["docking-profile"].value = "default";
  profileRetry.state.status.execution_unlock_remaining_sec = 30;
  await profileRetry.context.continueGuidedWorkflow();
  assert.equal(profileRetry.commands()[1].payload.profile_id, "offset", "Retry preserves the captured profile");

  const noCatalog = fixture("pick");
  noCatalog.state.status.docking_profiles.available = false;
  noCatalog.context.renderGuidedWorkflow();
  assert.equal(noCatalog.fields["dock-manipulate-undock"].disabled, true);
  await noCatalog.context.advanceGuidedWorkflow();
  assert.equal(noCatalog.commands().length, 0);
  assert.match(noCatalog.context.error, /Docking profile configuration is unavailable/);

  const disconnectedRetreat = fixture("place");
  await disconnectedRetreat.context.advanceGuidedWorkflow();
  for (let step = 0; step < 3; step++) await disconnectedRetreat.finish();
  disconnectedRetreat.state.status.docking_profiles.available = false;
  await disconnectedRetreat.finish();
  assert.equal(disconnectedRetreat.commands().length, 4, "Wait for catalog before retreat");
  disconnectedRetreat.state.status.docking_profiles.available = true;
  disconnectedRetreat.context.updateGuidedWorkflow();
  await flush();
  assert.equal(disconnectedRetreat.commands()[4].payload.profile_id, "offset");

  const staleProfile = fixture("pick");
  staleProfile.fields["docking-profile"] = { value: "removed" };
  await staleProfile.context.advanceGuidedWorkflow();
  assert.equal(staleProfile.commands().length, 0);
  assert.match(staleProfile.context.error, /selected docking profile is unavailable/);

  const captured = fixture("pick");
  await captured.context.advanceGuidedWorkflow();
  captured.context.postureTarget = () => ({ height: 0.6, waist_yaw: 0 });
  captured.state.selectedBoxId = "different-box";
  captured.state.status.visible_boxes.boxes = [{ instance_id: "different-box" }];
  await captured.finish();
  await captured.finish();
  assert.equal(captured.commands()[1].payload.height, 0.48);
  assert.equal(captured.commands()[2].payload.instance_id, "different-box",
    "Box selection must be resolved after docking, when Pick becomes ready");

  const unseen = fixture("pick");
  unseen.state.selectedBoxId = null;
  unseen.state.status.visible_boxes = { fresh: false, boxes: [] };
  unseen.context.renderGuidedWorkflow();
  assert.equal(unseen.fields["dock-manipulate-undock"].disabled, false);
  await unseen.context.advanceGuidedWorkflow();
  assert.equal(unseen.commands()[0].payload.kind, "fine_align");
  await unseen.finish();
  assert.equal(unseen.commands().length, 2, "Set Height must run without a visible object");
  await unseen.finish();
  assert.equal(unseen.commands().length, 2, "Pick must wait for a fresh object");
  assert.match(unseen.context.guidedWaitReason(unseen.state.guidedWorkflow), /fresh object/);
  const unlocksBeforeDetection = unseen.calls.filter((call) => call.path === "/api/unlock/execution").length;
  unseen.state.status.visible_boxes = { fresh: true, boxes: [{ instance_id: "new-box" }] };
  unseen.context.updateGuidedWorkflow();
  await flush();
  assert.equal(unseen.commands()[2].payload.instance_id, "new-box",
    "A single object detected after docking must be selected automatically");
  assert.equal(unseen.calls.filter((call) => call.path === "/api/unlock/execution").length,
    unlocksBeforeDetection + 1, "Waiting for detection must not repeatedly unlock motion");

  const multiple = fixture("pick");
  multiple.state.selectedBoxId = null;
  multiple.state.status.visible_boxes = { fresh: true,
    boxes: [{ instance_id: "box-a" }, { instance_id: "box-b" }] };
  await multiple.context.advanceGuidedWorkflow();
  await multiple.finish();
  await multiple.finish();
  assert.equal(multiple.commands().length, 2);
  assert.match(multiple.context.guidedWaitReason(multiple.state.guidedWorkflow), /Multiple objects/);
  multiple.state.selectedBoxId = "box-b";
  multiple.context.scheduleGuidedStep();
  await flush();
  assert.equal(multiple.commands()[2].payload.instance_id, "box-b");

  const stale = fixture("pick");
  await stale.context.advanceGuidedWorkflow();
  await stale.finish();
  stale.state.status.visible_boxes.fresh = false;
  await stale.finish();
  assert.equal(stale.commands().length, 2, "An expired detection cannot authorize Pick");

  const rejectedPick = fixture("pick");
  const rejectedApi = rejectedPick.context.api;
  let rejectPick = true;
  rejectedPick.context.api = async (path, options) => {
    if (path === "/api/actions" && JSON.parse(options.body).kind === "pick" && rejectPick) {
      throw new Error("The selected box is no longer a fresh visible detection; select it again");
    }
    return rejectedApi(path, options);
  };
  await rejectedPick.context.advanceGuidedWorkflow();
  await rejectedPick.finish();
  await rejectedPick.finish();
  assert.equal(rejectedPick.state.guidedWorkflow.failed, true);
  assert.equal(rejectedPick.state.guidedWorkflow.resumeBlocked, false);
  rejectPick = false;
  rejectedPick.state.selectedBoxId = "replacement-box";
  rejectedPick.state.status.visible_boxes.boxes = [{ instance_id: "replacement-box" }];
  rejectedPick.state.status.execution_unlock_remaining_sec = 30;
  await rejectedPick.context.continueGuidedWorkflow();
  await flush();
  assert.deepEqual(rejectedPick.commands().map((call) => call.payload.kind || "posture"),
    ["fine_align", "posture", "pick"]);
  assert.equal(rejectedPick.commands()[2].payload.instance_id, "replacement-box");

  const refreshed = fixture("pick");
  const refreshApi = refreshed.context.api;
  refreshed.context.api = async (path, options) => {
    if (path === "/api/status") refreshed.state.status.visible_boxes.fresh = false;
    return refreshApi(path, options);
  };
  await refreshed.context.advanceGuidedWorkflow();
  await refreshed.finish();
  await refreshed.finish();
  assert.equal(refreshed.commands().length, 2,
    "A detection that expired since the browser snapshot must block Pick before submission");

  const retryState = fixture("pick", true, "ABORTED");
  await retryState.context.advanceGuidedWorkflow();
  await flush();
  retryState.state.status.manipulation_state.state = "UNKNOWN";
  retryState.state.status.execution_unlock_remaining_sec = 30;
  await retryState.context.continueGuidedWorkflow();
  assert.equal(retryState.commands().length, 1);
  assert.match(retryState.context.error, /expected EMPTY/);

  const invisiblePlace = fixture("place", true);
  invisiblePlace.state.selectedBoxId = null;
  invisiblePlace.state.status.visible_boxes = { fresh: false, boxes: [] };
  await invisiblePlace.context.advanceGuidedWorkflow();
  await flush();
  assert.equal(invisiblePlace.commands().length, 5, "Place must not require a visible pickup object");

  const place = fixture("place");
  place.context.manualPlacePoseEnabled = () => true;
  place.context.placePose = () => ({ frame_id: "base_link", x: 0.35, y: 0, z: 0.2, yaw: 0 });
  await place.context.advanceGuidedWorkflow();
  place.context.placePose = () => { throw new Error("Must not reread edited fields"); };
  await place.finish();
  await place.finish();
  assert.equal(place.commands()[2].payload.place_pose.x, 0.35);

  const unavailable = fixture("pick");
  await unavailable.context.advanceGuidedWorkflow();
  unavailable.state.status.locomanipulation_posture.ready = false;
  await unavailable.finish();
  assert.equal(unavailable.commands().length, 1);
  unavailable.state.status.locomanipulation_posture.ready = true;
  unavailable.context.updateGuidedWorkflow();
  await flush();
  assert.equal(unavailable.commands().length, 2, "A service becoming ready resumes automatic progression");

  const cancel = fixture("pick");
  await cancel.context.advanceGuidedWorkflow();
  await cancel.context.stopGuidedWorkflow();
  await cancel.finish();
  assert.equal(cancel.commands().length, 1, "Stop must prevent all subsequent commands");
  assert.equal(cancel.calls.at(-1).path, "/api/cancel");
  cancel.state.status.execution_unlock_remaining_sec = 30;
  await cancel.context.continueGuidedWorkflow();
  await flush();
  assert.equal(cancel.commands().length, 2);
  assert.equal(cancel.commands()[1].path, "/api/posture",
    "A stopped command that subsequently succeeded must not be replayed");

  const invisible = fixture("pick");
  await invisible.context.advanceGuidedWorkflow();
  const docking = invisible.state.status.operations[0];
  docking.stage = "Reacquiring target";
  docking.feedback = { tag_visible: false, current_error: { x: null, y: null, yaw: null } };
  invisible.state.status.task_admission = { blocked: true, detail: "Active docking task" };
  await invisible.context.stopGuidedWorkflow();
  assert.equal(invisible.calls.at(-1).path, "/api/cancel");
  invisible.context.renderGuidedWorkflow();
  assert.equal(invisible.fields["dock-manipulate-undock"].disabled, true,
    "Keep new tasks blocked until cancellation is confirmed");
  await invisible.finish("CANCELED", false);
  docking.result.final_error = { x: null, y: null, yaw: null };
  invisible.state.status.task_admission = { blocked: false };
  invisible.state.status.execution_unlock_remaining_sec = 30;
  invisible.context.renderGuidedWorkflow();
  assert.equal(invisible.commands().length, 1, "A stopped Pick combo must not proceed to posture or Pick");
  assert.equal(invisible.fields["dock-manipulate-undock"].disabled, false,
    "Confirmed cancellation and a fresh unlock must permit a new combo");
  await invisible.context.advanceGuidedWorkflow();
  await flush();
  assert.equal(invisible.commands().length, 2);
  assert.equal(invisible.commands()[1].payload.kind, "fine_align",
    "A new combo starts from docking after the previous docking action was canceled");

  const inFlight = fixture("pick");
  let release;
  const originalApi = inFlight.context.api;
  inFlight.context.api = (path, options) => path === "/api/actions"
    ? new Promise((resolve) => { release = () => resolve(originalApi(path, options)); })
    : originalApi(path, options);
  const submission = inFlight.context.advanceGuidedWorkflow();
  await flush();
  await inFlight.context.stopGuidedWorkflow();
  release();
  await submission;
  await flush();
  assert.equal(inFlight.calls.at(-1).path, "/api/cancel", "Stop during submission must cancel the eventual goal");
  assert.equal(inFlight.commands().length, 1);

  const lost = fixture("pick");
  await lost.context.advanceGuidedWorkflow();
  lost.state.status.operations = [];
  lost.context.updateGuidedWorkflow();
  assert.equal(lost.state.guidedWorkflow.failed, true);
  await lost.context.continueGuidedWorkflow();
  assert.equal(lost.commands().length, 1, "Lost operation history must block continuation");

  const newest = fixture("pick");
  newest.state.status.operations.unshift({ id: "navigation-2", kind: "navigate", status: "ACTIVE" });
  await newest.context.advanceGuidedWorkflow();
  assert.equal(newest.commands().length, 0);

  const externalNavigation = fixture("pick");
  externalNavigation.state.status.operations = [];
  externalNavigation.state.status.navigation.goal_status.active = true;
  externalNavigation.context.renderGuidedWorkflow();
  assert.equal(externalNavigation.fields["dock-manipulate-undock"].disabled, true);
  await externalNavigation.context.advanceGuidedWorkflow();
  assert.equal(externalNavigation.commands().length, 0, "Active external navigation still blocks overlapping motion");

  const declined = fixture("pick");
  declined.context.window.confirm = () => false;
  await declined.context.advanceGuidedWorkflow();
  assert.equal(declined.commands().length, 0);

  const unlockError = fixture("pick");
  await unlockError.context.advanceGuidedWorkflow();
  const unlockApi = unlockError.context.api;
  unlockError.context.api = async (path, options) => {
    if (path === "/api/unlock/execution") throw new Error("Unlock failed");
    return unlockApi(path, options);
  };
  await unlockError.finish();
  assert.equal(unlockError.state.guidedWorkflow.failed, true);
  assert.equal(unlockError.commands().length, 1);

  const administrator = fixture("pick");
  administrator.state.authenticated = true;
  administrator.state.executionUnlockKnown = true;
  administrator.state.status.administrator_mode_enabled = true;
  administrator.state.status.execution_unlock_remaining_sec = 0;
  await administrator.context.advanceGuidedWorkflow();
  for (let i = 0; i < 5; i += 1) await administrator.finish();
  assert.equal(administrator.commands().length, 5);
  assert.equal(administrator.calls.filter((call) => call.path === "/api/unlock/execution").length, 0);
  assert.ok(administrator.confirmations.length > 0, "Administrator sequences still require confirmation");

  const unknownPrimary = fixture("pick");
  unknownPrimary.state.status.navigation.goal_status.available = false;
  unknownPrimary.state.status.navigation.goal_status.active = null;
  await unknownPrimary.context.advanceGuidedWorkflow();
  assert.match(unknownPrimary.confirmations[0], /Nav2 status is unavailable: confirm Nav2 is idle/);
  assert.equal(unknownPrimary.commands()[0].payload.confirm_nav2_idle, true);

  const locked = fixture("pick");
  locked.state.status.execution_unlock_remaining_sec = 0;
  locked.context.renderGuidedWorkflow();
  assert.equal(locked.fields["dock-manipulate-undock"].disabled, true);
  await locked.context.advanceGuidedWorkflow();
  assert.equal(locked.commands().length, 0);
  assert.equal(locked.calls.filter((call) => call.path === "/api/unlock/execution").length, 0,
    "A locked combo must not unlock itself at startup");
  assert.match(locked.context.error, /Unlock physical motion/);
})().catch((error) => { console.error(error); process.exitCode = 1; });
