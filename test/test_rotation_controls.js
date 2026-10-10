const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(process.argv[2], "utf8");
const fields = {}, calls = [], confirmations = [];
const state = { authenticated: true, statusConnected: true, status: {
  rotation_limits: { available: true, max_angular_speed: 0.5, max_duration: 60 },
  servers: { rotate_in_place: true }, operations: [], manipulation_state: { state: "EMPTY" },
  navigation: { goal_status: { available: false }, lifecycle: { collision_monitor: { state_id: 3 } } },
} };
let unlocked = 30;
const byId = (id) => fields[id] ||= { value: "", checked: false };
byId("rotation-speed").value = "-0.2";
byId("rotation-duration").value = "2";
const context = vm.createContext({ state, byId, executionUnlockRemaining: () => unlocked,
  finiteField: (id) => Number(byId(id).value),
  window: { confirm: (message) => { confirmations.push(message); return true; } },
  confirmNav2IdleWithoutStatus: () => true,
  api: async (path, options) => calls.push({ path, payload: JSON.parse(options.body) }),
  setError: (message) => { context.error = message; },
});
vm.runInContext(source.slice(source.indexOf("  function rotationSettings("), source.indexOf("  function shortcutSteps(")), context);
vm.runInContext(source.slice(source.indexOf("  function renderRotation("), source.indexOf("  async function undock(")), context);

(async () => {
  context.renderRotation();
  assert.equal(byId("execute-rotation").disabled, false);
  assert.match(byId("rotation-detail").textContent, /clockwise/);
  assert.match(byId("rotation-detail").textContent, /approximate angle -0.400 rad/);
  await context.rotateInPlace();
  assert.equal(calls[0].payload.kind, "rotate_in_place");
  assert.equal(calls[0].payload.angular_speed, -0.2);
  assert.equal(calls[0].payload.duration, 2);
  assert.equal(calls[0].payload.confirm_nav2_idle, true);
  assert.match(confirmations[0], /approximate/);
  byId("plan-only").checked = true;
  context.renderRotation();
  assert.equal(byId("execute-rotation").disabled, true);
  await context.rotateInPlace();
  assert.equal(calls.length, 1);
  byId("plan-only").checked = false;
  byId("rotation-speed").value = "0.6";
  await context.rotateInPlace();
  assert.match(context.error, /exceeds server limits/);
  byId("rotation-speed").value = "0.2";
  unlocked = 0;
  context.renderRotation();
  assert.equal(byId("execute-rotation").disabled, true);
  unlocked = 30;
  state.status.operations = [{ kind: "rotate_in_place", status: "ACTIVE", progress: 0.5,
    feedback: { elapsed_time: 1 } }];
  context.renderRotation();
  assert.equal(byId("cancel-rotation").disabled, false);
  assert.equal(byId("execute-rotation").disabled, true);
  assert.match(byId("rotation-detail").textContent, /Elapsed: 1.00 s; 50%/);
  state.status.operations[0].status = "CANCEL_REQUESTED";
  context.renderRotation();
  assert.equal(byId("cancel-rotation").disabled, true);
})().catch((error) => { console.error(error); process.exitCode = 1; });
