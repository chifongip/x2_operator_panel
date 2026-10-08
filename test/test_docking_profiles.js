const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(process.argv[2], "utf8");
const profiles = [
  { id: "default", tag_id: 9, tag_frame: "tag9", standoff: 0.5, lateral_offset: 0, yaw_offset: 0 },
  { id: "offset", tag_id: 10, tag_frame: "tag10", standoff: 0.7, lateral_offset: -0.1, yaw_offset: 0.2 },
];
const fields = {}, calls = [], confirmations = [];
const state = {
  status: { docking_profiles: { available: true, default_profile: "default", profiles },
    navigation: { goal_status: { available: true } } },
  savedPlans: new Map(), savedOperationEvents: new Set(),
};
const field = (id) => fields[id] ||= {
  value: "", dataset: {}, children: [], textContent: "", innerHTML: "",
  replaceChildren(...children) { this.children = children; },
};
const context = vm.createContext({
  state, byId: field, guidedPickId: () => state.selectedBoxId || null,
  document: { createElement: () => ({ value: "", textContent: "" }) },
  window: { confirm: (message) => { confirmations.push(message); return true; } },
  confirmNav2IdleWithoutStatus: () => false,
  setError: (message) => { context.error = message; },
  api: async (path, options) => { calls.push({ path, payload: JSON.parse(options.body) }); return {}; },
  renderSavedPlans: () => {}, formatPlanarError: () => "", formatUndockDistance: () => "",
});
vm.runInContext(source.slice(source.indexOf("  const guidedSteps ="),
  source.indexOf("  function guidedStepLabel(")), context);
vm.runInContext(source.slice(source.indexOf("  async function fineAlign("),
  source.indexOf("  function renderManipulationTask(")), context);
vm.runInContext(source.slice(source.indexOf("  function escapeHtml("),
  source.indexOf("  function escapeHtml(") + source.slice(source.indexOf("  function escapeHtml(")).indexOf("\n  }\n") + 5), context);
vm.runInContext(source.slice(source.indexOf("  function renderOperations("),
  source.indexOf("  function renderAudit(")), context);
vm.runInContext(source.slice(source.indexOf("  function formatPlanarError("),
  source.indexOf("  function formatUndockDistance(")), context);

(async () => {
  state.status.table_profiles = { available: true, default_profile: "default", profiles: [
    { id: "default", tag_id: 9, tag_frame: "tag9", dimensions: [0.6, 0.4, 0.6] },
    { id: "second", tag_id: 10, tag_frame: "tag10", dimensions: [0.8, 0.5, 0.7] },
  ] };
  context.renderTableProfiles();
  assert.equal(field("table-profile").children.length, 3);
  field("table-profile").value = "second";
  context.renderTableProfiles();
  assert.match(field("table-profile-detail").textContent, /tag 10.*0.800/);
  assert.equal(context.tableProfileSelection(), "second");
  state.status.table_profiles.available = false;
  context.renderTableProfiles();
  assert.equal(field("table-profile").value, "second");
  assert.match(field("table-profile").children.at(-1).textContent, /unavailable/);
  assert.throws(() => context.tableProfileSelection(), /unavailable/);
  state.status.table_profiles.available = true;
  context.renderDockingProfiles();
  assert.equal(field("docking-profile").children.length, 3);
  assert.equal(field("docking-profile").children[0].textContent, "Server default (default)");
  assert.match(field("docking-profile-detail").textContent, /stand-off 0.500 m/);
  field("docking-profile").value = "offset";
  context.renderDockingProfiles();
  assert.match(field("docking-profile-detail").textContent, /tag 10 \(tag10\).*lateral offset -0.100 m/);
  await context.fineAlign(false);
  assert.equal(calls.at(-1).payload.profile_id, "offset");
  assert.equal(calls.at(-1).payload.execute, false);
  assert.equal(confirmations.length, 0, "Measurement-only checking needs no motion confirmation");
  await context.fineAlign(true);
  assert.equal(calls.at(-1).payload.profile_id, "offset");
  assert.match(confirmations.at(-1), /profile offset/);
  // Manual retreat has an independent automatic/explicit selector.
  await context.undock();
  assert.equal(calls.at(-1).payload.profile_id, "");
  assert.match(confirmations.at(-1), /last successful dock/);
  field("undocking-profile").value = "default";
  await context.undock();
  assert.equal(calls.at(-1).payload.profile_id, "default");

  // Refreshes and disconnections preserve selection instead of silently reverting.
  state.status.docking_profiles = { available: false, profiles: [], detail: "Service unavailable" };
  context.renderDockingProfiles();
  assert.equal(field("docking-profile").value, "offset");
  assert.equal(field("docking-profile").disabled, false, "Allow switching to automatic while disconnected");
  const count = calls.length;
  await context.fineAlign(true);
  assert.equal(calls.length, count);
  assert.match(context.error, /selected docking profile is unavailable/);
  field("docking-profile").value = "";
  await context.fineAlign(false);
  assert.equal(calls.at(-1).payload.profile_id, "", "Automatic selection still reaches the server");
  state.status.docking_profiles = { available: true, profiles, default_profile: "offset" };
  context.renderDockingProfiles();
  assert.equal(field("docking-profile").disabled, false);
  assert.equal(field("docking-profile").children[0].textContent, "Server default (offset)");
  field("docking-profile").value = "removed";
  context.renderDockingProfiles();
  assert.equal(field("docking-profile").value, "removed");
  assert.match(field("docking-profile-detail").textContent, /no longer configured/);
  assert.throws(() => context.dockingProfileSelection(), /unavailable/);

  context.renderOperations([{ id: "dock", kind: "fine_align", status: "SUCCEEDED",
    profile_id: "default", result: { profile_id: "offset", success: true } }]);
  assert.match(field("operations").innerHTML, /Profile: offset/);
  context.renderOperations([{ id: "retreat", kind: "undock", status: "ACTIVE",
    feedback: { profile_id: "offset" } }]);
  assert.match(field("operations").innerHTML, /Profile: offset/);
  context.renderOperations([{ id: "place", kind: "place", status: "SUCCEEDED",
    table_profile_id: "default", result: { table_profile_id: "second", success: true } }]);
  assert.match(field("operations").innerHTML, /Table: second/);
  context.renderOperations([{ id: "missing-target", kind: "fine_align", status: "ACTIVE",
    stage: "Reacquiring target", feedback: {
      tag_visible: false, current_error: { x: 0, y: 0, yaw: 0 },
    } }]);
  assert.match(field("operations").innerHTML, /error —/,
    "An invisible tag's default zero error must not look like successful alignment");
  context.renderOperations([{ id: "stopped-target", kind: "fine_align", status: "CANCELED",
    result: { final_error: { x: null, y: null, yaw: null } } }]);
  assert.match(field("operations").innerHTML, /CANCELED.*error —/);
  assert.equal(context.formatPlanarError({ x: 0.1, y: -0.2, yaw: 0.3 }),
    "error x 0.100 m, y -0.200 m, yaw 0.300 rad");
})().catch((error) => { console.error(error); process.exitCode = 1; });


vm.runInContext(source.slice(source.indexOf("  function formatUndockDistance("),
  source.indexOf("  function formatUndockDistance(") + source.slice(source.indexOf("  function formatUndockDistance(")).indexOf("\n  }\n") + 5), context);
assert.match(context.formatUndockDistance({result: {
  undock_mode: "timed_reverse", elapsed_time: 3, distance_traveled: 0.3,
}}), /estimated travel 0.300 m.*timed reverse.*3.0 s elapsed/);
