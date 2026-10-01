const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(process.argv[2], "utf8");
const fragment = source.slice(source.indexOf("  async function executeSavedPlan("), source.indexOf("  function renderAudit("));
const state = {savedPlans: new Map(), savedOperationEvents: new Set()};
const elements = {operations: {}, "saved-plan-select": {value: "plan-1"}};
let submitted;
const context = vm.createContext({state, byId: (id) => elements[id],
  window: {confirm: () => true}, api: async (path, request) => {submitted = JSON.parse(request.body);},
  renderSavedPlans: () => {}, setError: () => {}, escapeHtml: (text) => text,
  formatPlanarError: () => "", formatUndockDistance: () => ""});
vm.runInContext(fragment, context);
const plan = {id: "operation-1", kind: "pick_place", plan_only: true, status: "SUCCEEDED",
  result: {success: true, plan_id: "plan-1", planning_mode: "pose_to_pose"}};
context.renderOperations([plan]);
assert.equal(state.savedPlans.size, 1);
(async () => {
  await context.executeSavedPlan();
  assert.equal(submitted.plan_id, "plan-1");
  assert.equal(submitted.kind, "pick_place");
  assert.equal(submitted.plan_only, false);
  assert.equal(submitted.confirmed, true);
  assert.equal("place_pose" in submitted, false, "Execution must not read edited target forms");
  assert.equal(state.savedPlans.size, 0);
  context.renderOperations([plan]);
  assert.equal(state.savedPlans.size, 0, "Old result must not resurrect a consumed plan");
  const newer = {...plan, id: "operation-2", result: {...plan.result, plan_id: "plan-2"}};
  context.renderOperations([newer, plan]);
  assert.equal(state.savedPlans.size, 1);
  context.renderOperations([{id: "operation-3", kind: "pick", plan_only: false, status: "ACTIVE"}, newer, plan]);
  assert.equal(state.savedPlans.size, 0, "Physical execution invalidates available plans");
  state.status = {manipulation_state: {state: "EMPTY"}};
  const fresh = {...plan, id: "operation-4", result: {...plan.result, plan_id: "plan-4"}};
  context.renderOperations([fresh, newer, plan]);
  assert.equal(state.savedPlans.size, 1);
  state.status.manipulation_state.state = "HOLDING";
  context.renderOperations([fresh, newer, plan]);
  assert.equal(state.savedPlans.size, 0, "External manipulation state changes invalidate availability");
})().catch((error) => {console.error(error); process.exitCode = 1;});
