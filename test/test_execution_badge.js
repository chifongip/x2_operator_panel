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
const administratorToggle = { setAttribute() {} };
const unlockButton = {};
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
  byId: (id) => id === "execution-state" ? badge : id === "administrator-mode" ? administratorToggle : id === "unlock-execution" ? unlockButton : planOnly,
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
context.applyStatus({ execution_unlock_remaining_sec: 0, administrator_mode_enabled: true });
assert.equal(badge.textContent, "Administrator mode active");
assert.equal(context.physicalExecutionAuthorized(), true);
assert.equal(unlockButton.disabled, true);
assert.equal(administratorToggle.textContent, "Disable administrator mode");
context.syncExecutionUnlock(0);
assert.equal(context.physicalExecutionAuthorized(), true, "Physical submission must not consume administrator mode");
context.invalidateExecutionUnlock();
assert.equal(badge.textContent, "Status unavailable");
assert.equal(context.physicalExecutionAuthorized(), false, "Disconnect must invalidate cached administrator authority");
context.applyStatus({ execution_unlock_remaining_sec: 0, administrator_mode_enabled: true });
assert.equal(context.physicalExecutionAuthorized(), true, "Fresh reconnect status restores the session mode");
context.applyStatus({ execution_unlock_remaining_sec: 0, administrator_mode_enabled: false }, false);
assert.equal(context.physicalExecutionAuthorized(), false);
assert.equal(administratorToggle.textContent, "Enable administrator mode");
assert.equal(unlockButton.disabled, false);
context.applyStatus({ administrator_mode_enabled: true });
assert.equal(context.physicalExecutionAuthorized(), false, "Invalid unlock telemetry must fail closed even in administrator mode");
context.invalidateExecutionUnlock();
state.authenticated = false;
context.syncExecutionUnlock(0);
assert.equal(timers.size, 0, "A signed-out browser must not start an update timer");

// A successful HTTP response may arrive after the WebSocket has disconnected.
vm.runInContext(source.slice(source.indexOf("  async function api("),
  source.indexOf("  function setError(")), context);
(async () => {
  state.authenticated = true;
  context.applyStatus({ execution_unlock_remaining_sec: 0, administrator_mode_enabled: true });
  let finishResponse;
  context.fetch = () => new Promise((resolve) => { finishResponse = resolve; });
  const pending = context.api("/api/actions", { method: "POST", body: "{}" });
  context.invalidateExecutionUnlock();
  finishResponse({ ok: true, status: 202, headers: { get: () => "application/json" },
    json: async () => ({ operation: { plan_only: false } }) });
  await pending;
  assert.equal(context.physicalExecutionAuthorized(), false,
    "A late successful submission must not restore administrator authority after disconnect");
  assert.equal(badge.textContent, "Status unavailable");
})().catch((error) => { console.error(error); process.exitCode = 1; });
