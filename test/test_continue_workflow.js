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
  console.log("Continue/Cancel browser behavior passed");
})().catch((error) => { console.error(error); process.exitCode = 1; });
