const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// Exercise the real browser scheduler with authenticated HTTP and status pushes.
const source = fs.readFileSync(process.argv[2], "utf8");
const fragment = source.slice(source.indexOf("  const guidedSteps ="), source.indexOf("  async function navigate("));
const flush = () => new Promise((resolve) => setImmediate(resolve));

function fixture(kind, fast = false, completionStatus = "SUCCEEDED") {
  const calls = [], fields = {}, confirmations = [];
  const state = {
    authenticated: true, guidedWorkflow: null, guidedSubmitting: false,
    selectedBoxId: "box-1",
    status: {
      operations: [{ id: "navigation-1", kind: "navigate", status: "SUCCEEDED" }],
      manipulation_state: { state: kind === "pick" ? "EMPTY" : "HOLDING" },
      navigation: { goal_status: { available: true, active: false } },
      locomanipulation_posture: { ready: true }, execution_unlock_remaining_sec: 0,
      visible_boxes: { fresh: true, boxes: [{ instance_id: "box-1" }] },
    },
  };
  const context = vm.createContext({
    state,
    byId: (id) => fields[id] ||= { checked: false },
    window: { confirm: (message) => { confirmations.push(message); return true; } },
    setError: (message) => { context.error = message; },
    postureTarget: () => ({ height: 0.48, waist_yaw: 0.2 }),
    manualPlacePoseEnabled: () => false,
    api: async (path, options) => {
      const payload = JSON.parse(options.body);
      calls.push({ path, payload });
      if (path === "/api/unlock/execution") return {};
      if (path === "/api/cancel") return { operation_ids: ["active"] };
      const operation = { id: `operation-${calls.length}`, kind: payload.kind || "set_locomanipulation_posture", status: "ACTIVE" };
      state.status.operations.unshift(operation);
      if (fast) {
        finishOperation(operation, completionStatus, completionStatus === "SUCCEEDED");
        // WebSocket result arrives before the HTTP submission response.
        context.updateGuidedWorkflow();
      }
      return { operation };
    },
  });
  vm.runInContext(fragment, context);
  function finishOperation(operation, status, success, publishState = true) {
    operation.status = status;
    operation.result = { success };
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
  return { context, state, calls, fields, confirmations, commands, finish };
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
  assert.deepEqual(f.commands().map((call) => call.payload.kind || "posture"),
    ["fine_align", "posture", kind, "posture", "undock"]);
  assert.equal(f.calls.filter((call) => call.path === "/api/unlock/execution").length, 5);
  assert.deepEqual(f.commands()[1].payload,
    { height: 0.48, waist_yaw: 0.2, wait_for_settle: true, confirmed: true });
  assert.deepEqual(f.commands()[3].payload,
    { height: 0.64, waist_yaw: 0.0, wait_for_settle: true, confirmed: true });
  assert.equal(f.state.guidedWorkflow.completed, true);
  f.context.renderGuidedWorkflow();
  assert.equal(f.fields["dock-manipulate-undock"].disabled, false,
    "Completion must allow a new sequence without another navigation goal");
}

(async () => {
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

  const paused = fixture("pick");
  await paused.context.advanceGuidedWorkflow();
  await paused.finish();
  await paused.finish();
  paused.state.status.manipulation_task = { status: "paused" };
  paused.context.updateGuidedWorkflow();
  await flush();
  assert.equal(paused.commands().length, 3, "A paused manipulation action must hold the sequence");
  await paused.finish(); // Continued action eventually succeeds.
  assert.equal(paused.commands().length, 4);

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

  const newest = fixture("pick");
  newest.state.status.operations.unshift({ id: "navigation-2", kind: "navigate", status: "ACTIVE" });
  await newest.context.advanceGuidedWorkflow();
  assert.equal(newest.calls.length, 0);

  const externalNavigation = fixture("pick");
  externalNavigation.state.status.operations = [];
  externalNavigation.state.status.navigation.goal_status.active = true;
  externalNavigation.context.renderGuidedWorkflow();
  assert.equal(externalNavigation.fields["dock-manipulate-undock"].disabled, true);
  await externalNavigation.context.advanceGuidedWorkflow();
  assert.equal(externalNavigation.calls.length, 0, "Active external navigation still blocks overlapping motion");

  const declined = fixture("pick");
  declined.context.window.confirm = () => false;
  await declined.context.advanceGuidedWorkflow();
  assert.equal(declined.calls.length, 0);

  const unlockError = fixture("pick");
  unlockError.context.api = async () => { throw new Error("Unlock failed"); };
  await unlockError.context.advanceGuidedWorkflow();
  await flush();
  assert.equal(unlockError.state.guidedWorkflow.failed, true);
  assert.equal(unlockError.commands().length, 0);
})().catch((error) => { console.error(error); process.exitCode = 1; });
