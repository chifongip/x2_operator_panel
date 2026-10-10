const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(process.argv[2], "utf8");
const fragment = source.slice(source.indexOf("  function executionUnlockRemaining("),
  source.indexOf("  function addPoseToTrail("));
let now = 0;
let nextTimer = 1;
const timers = new Map();
const classes = new Set();
const badge = { classList: { toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name) } };
const planOnly = { checked: true };
const state = {
  savedPlans: new Map(),
  authenticated: true, status: null, executionUnlockDeadline: null,
  executionUnlockKnown: false, executionTimer: null,
};
let guidedRenders = 0;
const context = vm.createContext({
  state,
  performance: { now: () => now },
  window: {
    setInterval: (callback) => { const id = nextTimer++; timers.set(id, callback); return id; },
    clearInterval: (id) => timers.delete(id),
  },
  byId: (id) => id === "execution-state" ? badge : planOnly,
  renderSavedPlans: () => {},
  renderRotation: () => {},
  renderGuidedWorkflow: () => { guidedRenders += 1; },
  updateGuidedWorkflow: () => {}, addPoseToTrail: () => {}, drawMap: () => {},
  renderStatus: () => context.renderExecutionState(),
});
vm.runInContext(fragment, context);
const tick = (milliseconds) => {
  now += milliseconds;
  for (const callback of timers.values()) callback();
};

context.applyStatus({ execution_unlock_remaining_sec: 30 });
assert.equal(badge.textContent, "Unlocked 30s");
assert.equal(timers.size, 1);
assert.equal(classes.has("unlocked"), true);
tick(1250);
assert.equal(badge.textContent, "Unlocked 29s", "Countdown must advance without server messages");

// A delta affecting telemetry only carries the old unlock value in merged status.
context.applyStatus({ execution_unlock_remaining_sec: 30, diagnostics: [] }, false);
tick(1000);
assert.equal(badge.textContent, "Unlocked 28s", "An unrelated delta must not restart the countdown");
assert.equal(timers.size, 1, "Status pushes must not create duplicate timers");
assert.equal(state.status.execution_unlock_remaining_sec, 30, "Interpolation must not mutate server telemetry");

// A renewed unlock is a real deadline change, rather than a repeated render.
context.applyStatus({ execution_unlock_remaining_sec: 10 });
assert.equal(badge.textContent, "Unlocked 10s");
planOnly.checked = false;
tick(25000); // Browser was suspended; elapsed time must still be accounted for.
assert.equal(badge.textContent, "Locked");
assert.equal(context.executionUnlockRemaining(), 0);
assert.equal(classes.has("unlocked"), false);
assert.ok(guidedRenders > 0, "Expiry must refresh the combo button gate as well as the badge");
planOnly.checked = true;
context.renderExecutionState();
assert.equal(badge.textContent, "Plan only");

context.syncExecutionUnlock(30);
context.syncExecutionUnlock(0); // Successful physical submission consumes the unlock.
assert.equal(badge.textContent, "Plan only");
assert.equal(classes.has("unlocked"), false);

context.syncExecutionUnlock(30);
context.invalidateExecutionUnlock(); // Lost WebSocket or signed-out session.
assert.equal(badge.textContent, "Status unavailable");
assert.equal(context.executionUnlockRemaining(), 0);
assert.equal(timers.size, 0);
tick(1000);
assert.equal(badge.textContent, "Status unavailable");
context.applyStatus({ execution_unlock_remaining_sec: 7 }); // Reconnection's full snapshot.
assert.equal(badge.textContent, "Unlocked 7s");
assert.equal(timers.size, 1);
context.applyStatus({}); // Missing/invalid unlock telemetry must fail closed.
assert.equal(badge.textContent, "Status unavailable");
context.invalidateExecutionUnlock();
state.authenticated = false;
context.syncExecutionUnlock(0);
assert.equal(timers.size, 0, "A signed-out browser must not start an update timer");
