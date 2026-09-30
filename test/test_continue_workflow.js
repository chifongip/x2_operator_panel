const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(process.argv[2], "utf8");
const fragment = source.slice(source.indexOf("  function renderManipulationTask("), source.indexOf("  async function recoverState("));
const fields = {};
const calls = [];
const task = { task_id: "task", pause_id: 3, status: "paused", can_continue: true, continue_service_ready: true, phase: "carry", last_completed_phase: "attach", object_disposition: "attached", attempt: 3, maximum_attempts: 3, failure: "no route" };
const state = { status: { manipulation_task: task } };
const context = vm.createContext({
  state,
  byId: (id) => fields[id] ||= {},
  setError: (message) => { context.error = message; },
  api: async (path, options) => { calls.push({ path, payload: JSON.parse(options.body) }); },
});
vm.runInContext(fragment, context);
(async () => {
  context.renderManipulationTask(task);
  assert.equal(fields["continue-manipulation"].disabled, false);
  assert.equal(fields["cancel-manipulation"].disabled, false);
  assert.equal(fields["manipulation-task-warning"].textContent, "no route");
  assert.match(fields["manipulation-task-summary"].textContent, /object attached/);
  context.renderManipulationTask({ ...task, task_elapsed_sec: 12.35,
    controller_execution_sec: 4.24, controller_goal_count: 2, timing_partial: true });
  assert.equal(fields["manipulation-task-timing"].textContent,
    "Task elapsed: 12.3 s · Controller execution: 4.2 s (2 trajectories) · Partial: panel joined mid-task");
  context.renderManipulationTask({ ...task, task_elapsed_sec: 25,
    controller_execution_sec: 7, controller_goal_count: 2, status: "completed" });
  assert.equal(fields["manipulation-task-timing"].textContent,
    "Task elapsed: 25.0 s · Controller execution: 7.0 s (2 trajectories)");
  context.renderManipulationTask({ status: "idle" });
  assert.equal(fields["manipulation-task-timing"].textContent,
    "Task elapsed: — · Controller execution: —");
  await context.continueManipulation();
  assert.equal(calls[0].path, "/api/manipulation/continue");
  assert.deepEqual(calls[0].payload, { task_id: "task", pause_id: 3 });
  task.continue_pending = true;
  context.renderManipulationTask(task);
  assert.equal(fields["continue-manipulation"].disabled, true);
  await context.continueManipulation();
  assert.equal(calls.length, 1);
  await context.cancelManipulation();
  assert.equal(calls[1].path, "/api/manipulation/cancel");
  assert.deepEqual(calls[1].payload, { task_id: "task" });
  context.renderManipulationTask({ ...task, status: "retrying", can_continue: false });
  assert.equal(fields["continue-manipulation"].disabled, true);
  assert.equal(fields["cancel-manipulation"].disabled, false);
  context.renderManipulationTask({ ...task, status: "completed", can_continue: false });
  assert.equal(fields["cancel-manipulation"].disabled, true);

  task.continue_pending = false;
  context.api = async () => { throw new Error("HTTP failure"); };
  await context.continueManipulation();
  assert.equal(fields["continue-manipulation"].disabled, false,
    "A failed HTTP request must restore Continue without waiting for a status push");
  assert.equal(context.error, "HTTP failure");

  let rejectRequest;
  context.api = () => new Promise((resolve, reject) => { rejectRequest = reject; });
  const pending = context.continueManipulation();
  context.renderManipulationTask(task); // Status can arrive while HTTP is pending.
  assert.equal(fields["continue-manipulation"].disabled, true);
  const currentRequest = state.continueRequest;
  await context.continueManipulation();
  assert.equal(state.continueRequest, currentRequest, "Double click must not dispatch another Continue");
  state.status.manipulation_task = { ...task, pause_id: 4, status: "retrying", can_continue: false };
  context.error = "new task warning";
  rejectRequest(new Error("stale HTTP failure"));
  await pending;
  assert.equal(context.error, "new task warning", "Old responses must not overwrite current warnings");
  assert.equal(fields["continue-manipulation"].disabled, true);

  state.status.manipulation_task = { ...task, pause_id: 4 };
  const oldRequest = context.continueManipulation();
  const rejectOld = rejectRequest;
  state.status.manipulation_task = { ...task, pause_id: 5 };
  context.renderManipulationTask(state.status.manipulation_task);
  const newRequest = context.continueManipulation();
  const rejectNew = rejectRequest;
  assert.equal(state.continueRequest.pause_id, 5, "A new pause must not be blocked by an old HTTP request");
  rejectOld(new Error("old failure"));
  await oldRequest;
  assert.equal(state.continueRequest.pause_id, 5, "An old request must not clear the new pending request");
  rejectNew(new Error("current failure"));
  await newRequest;
  assert.equal(fields["continue-manipulation"].disabled, false);

  const cancel = context.cancelManipulation();
  state.status.manipulation_task = { ...task, task_id: "next-task" };
  context.error = "next task warning";
  rejectRequest(new Error("old cancel failure"));
  await cancel;
  assert.equal(context.error, "next task warning");
  console.log("Continue/Cancel browser behavior passed");
})().catch((error) => { console.error(error); process.exitCode = 1; });
