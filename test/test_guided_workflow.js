const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// Run the actual browser workflow with mock DOM and authenticated HTTP transport.
const source = fs.readFileSync(process.argv[2], "utf8");
const workflowSource = source.slice(source.indexOf("  const guidedSteps ="), source.indexOf("  async function navigate("));

function fixture(kind, completeBeforeResponse = false, completionStatus = "SUCCEEDED") {
  const calls = [];
  const fields = {};
  const state = {
    guidedWorkflow: null, guidedSubmitting: false, lastGuidedNavigationId: null,
    selectedBoxId: "box-1",
    status: {
      operations: [{ id: "navigation-1", kind: "navigate", status: "SUCCEEDED" }],
      manipulation_state: { state: kind === "pick" ? "EMPTY" : "HOLDING" },
      navigation: { goal_status: { available: true, active: false } },
      locomanipulation_posture: { ready: true },
      execution_unlock_remaining_sec: 30,
    },
  };
  const context = vm.createContext({
    state,
    byId: (id) => fields[id] ||= { checked: false },
    window: { confirm: () => true },
    setError: (message) => { context.error = message; },
    postureTarget: () => ({ height: 0.48, waist_yaw: 0.2, wait_for_settle: false }),
    manualPlacePoseEnabled: () => false,
    confirmNav2IdleWithoutStatus: () => true,
    api: async (path, options) => {
      const payload = JSON.parse(options.body);
      calls.push({ path, payload });
      const operation = { id: `operation-${calls.length}`, kind: payload.kind || "set_locomanipulation_posture", status: "ACTIVE" };
      state.status.operations.push(operation);
      state.status.execution_unlock_remaining_sec = 0;
      if (completeBeforeResponse) {
        operation.status = completionStatus;
        operation.result = { success: completionStatus === "SUCCEEDED" };
        if (operation.kind === kind && completionStatus === "SUCCEEDED") {
          state.status.manipulation_state.state = kind === "pick" ? "HOLDING" : "EMPTY";
        }
        // A status push is processed while the HTTP request is still pending.
        context.updateGuidedWorkflow();
      }
      return { operation };
    },
  });
  vm.runInContext(workflowSource, context);
  function finish(status = "SUCCEEDED", success = true) {
    const operation = state.status.operations.at(-1);
    operation.status = status;
    operation.result = { success };
    if (operation.kind === kind && success && status === "SUCCEEDED") {
      state.status.manipulation_state.state = kind === "pick" ? "HOLDING" : "EMPTY";
    }
    context.updateGuidedWorkflow();
  }
  return { context, state, calls, fields, finish };
}

async function checkFullSequence(kind) {
  const f = fixture(kind);
  for (let step = 0; step < 5; step++) {
    f.state.status.execution_unlock_remaining_sec = 30;
    await f.context.advanceGuidedWorkflow();
    assert.equal(f.calls.length, step + 1, f.context.error);
    await f.context.advanceGuidedWorkflow();
    assert.equal(f.calls.length, step + 1, "An active stage must prevent another submission");
    f.finish();
    f.context.updateGuidedWorkflow();
    assert.equal(f.state.guidedWorkflow.step, step + 1, "Repeated snapshots must not skip stages");
  }
  assert.deepEqual(f.calls.map((call) => call.payload.kind || "posture"),
    ["fine_align", "posture", kind, "posture", "undock"]);
  assert.equal(f.calls[1].path, "/api/posture");
  assert.deepEqual(f.calls[1].payload,
    { height: 0.48, waist_yaw: 0.2, wait_for_settle: true, confirmed: true });
  assert.deepEqual(f.calls[3].payload,
    { height: 0.64, waist_yaw: 0.0, wait_for_settle: true, confirmed: true });
  assert.equal(f.state.guidedWorkflow.completed, true);
  assert.equal(f.state.lastGuidedNavigationId, "navigation-1");
  await f.context.advanceGuidedWorkflow(); // Clear the completed sequence.
  await f.context.advanceGuidedWorkflow();
  assert.equal(f.calls.length, 5, "A new navigation goal is required for another sequence");
}

(async () => {
  await checkFullSequence("pick");
  await checkFullSequence("place");

  const fastDock = fixture("pick", true);
  await fastDock.context.advanceGuidedWorkflow();
  assert.equal(fastDock.state.guidedWorkflow.step, 1,
    "Dock success received before the HTTP response must enable Set Height");
  assert.equal(fastDock.state.guidedWorkflow.operationId, null);
  assert.equal(fastDock.fields["dock-manipulate-undock"].textContent, "Set Height");
  assert.doesNotMatch(fastDock.fields["guided-workflow-status"].textContent, /Waiting for Dock/);
  fastDock.context.updateGuidedWorkflow();
  assert.equal(fastDock.state.guidedWorkflow.step, 1, "Completion must be consumed only once");

  for (const kind of ["pick", "place"]) {
    const fastSequence = fixture(kind, true);
    for (let step = 0; step < 5; step++) {
      fastSequence.state.status.execution_unlock_remaining_sec = 30;
      await fastSequence.context.advanceGuidedWorkflow();
      assert.equal(fastSequence.state.guidedWorkflow.step, step + 1,
        "Every stage must consume completion received before its HTTP response");
      fastSequence.context.updateGuidedWorkflow();
      assert.equal(fastSequence.state.guidedWorkflow.step, step + 1);
    }
    assert.equal(fastSequence.state.guidedWorkflow.completed, true);
  }

  const newestNavigation = fixture("pick");
  newestNavigation.state.status.operations.unshift({ id: "navigation-2", kind: "navigate", status: "ACTIVE" });
  await newestNavigation.context.advanceGuidedWorkflow();
  assert.equal(newestNavigation.calls.length, 0, "Old navigation success must not authorize a new sequence");
  newestNavigation.state.status.operations[0].status = "SUCCEEDED";
  await newestNavigation.context.advanceGuidedWorkflow();
  assert.equal(newestNavigation.state.guidedWorkflow.navigationId, "navigation-2");

  for (const status of ["ABORTED", "CANCELED", "FAILED", "OUTCOME_UNKNOWN"]) {
    const fastFailure = fixture("pick", true, status);
    await fastFailure.context.advanceGuidedWorkflow();
    assert.equal(fastFailure.state.guidedWorkflow.failed, true);
    assert.equal(fastFailure.state.guidedWorkflow.operationId, null);
    assert.equal(fastFailure.state.guidedWorkflow.step, 0);
  }

  const lostHistory = fixture("pick");
  await lostHistory.context.advanceGuidedWorkflow();
  lostHistory.state.status.operations = [];
  lostHistory.context.updateGuidedWorkflow();
  assert.equal(lostHistory.state.guidedWorkflow.failed, true,
    "A previously observed operation lost on reconnect must not wait forever");
  assert.match(lostHistory.state.guidedWorkflow.message, /history was lost/);

  const failure = fixture("pick");
  for (let step = 0; step < 4; step++) {
    failure.state.status.execution_unlock_remaining_sec = 30;
    await failure.context.advanceGuidedWorkflow();
    failure.finish("SUCCEEDED", step !== 3);
  }
  assert.equal(failure.state.guidedWorkflow.failed, true);
  assert.equal(failure.state.guidedWorkflow.step, 3);
  await failure.context.advanceGuidedWorkflow();
  assert.equal(failure.calls.length, 4, "A failed default-height step must not submit Undock");

  const canceled = fixture("place");
  await canceled.context.advanceGuidedWorkflow();
  canceled.finish("CANCELED", false);
  assert.equal(canceled.state.guidedWorkflow.failed, true);
  assert.equal(canceled.state.guidedWorkflow.step, 0);

  const locked = fixture("pick");
  locked.state.status.execution_unlock_remaining_sec = 0;
  await locked.context.advanceGuidedWorkflow();
  assert.equal(locked.calls.length, 0);
  assert.match(locked.context.error, /unlock/);

  const unavailable = fixture("pick");
  await unavailable.context.advanceGuidedWorkflow();
  unavailable.finish();
  unavailable.state.status.execution_unlock_remaining_sec = 30;
  unavailable.state.status.locomanipulation_posture.ready = false;
  await unavailable.context.advanceGuidedWorkflow();
  assert.equal(unavailable.calls.length, 1, "An unavailable posture service must block Set Height");
})().catch((error) => { console.error(error); process.exitCode = 1; });
