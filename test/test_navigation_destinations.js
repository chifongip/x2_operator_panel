const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(process.argv[2], "utf8");
const clone = (value) => JSON.parse(JSON.stringify(value));
const initial = { available: true, presets: [
  { id: "bay", label: "Loading bay", revision: 1, pose: { x: 1, y: -2, yaw: 0.5 } },
] };

function fixture() {
  const fields = {}, calls = [];
  function element() {
    let text = "", value = "";
    return { get value() { return value; }, set value(next) { value = String(next); },
      dataset: {}, children: [], listeners: {}, hidden: false,
      get textContent() { return text; }, set textContent(value) { text = value; this.children = []; },
      replaceChildren(...children) { this.children = children; },
      appendChild(child) { this.children.push(child); },
      addEventListener(event, callback) { this.listeners[event] = callback; },
    };
  }
  const byId = (id) => fields[id] ||= element();
  const state = { presets: [], destinationCatalog: {}, destinationDraft: null, destinationSaving: false,
    destinationLoadSequence: 0, shortcutDraft: {}, statusConnected: true,
    status: { map_pose: { available: true, fresh: true, x: 3, y: 4, yaw: 1 }, navigation: { goal_status: { available: true } } },
  };
  let savedCatalog = clone(initial), revision = 1;
  const context = vm.createContext({ state, byId, document: { createElement: element },
    window: { confirm: () => true },
    setError: (message) => { context.error = message; },
    drawMap: () => { context.mapRendered = true; },
    renderShortcutChoices: () => { context.choicesRendered = true; },
    renderTaskShortcuts: () => { context.shortcutsRendered = true; context.renderShortcutChoices(); },
    confirmNav2IdleWithoutStatus: () => false,
    api: async (path, options = {}) => {
      const payload = JSON.parse(options.body || "{}"); calls.push({ path, payload });
      if (path === "/api/presets") return clone(savedCatalog);
      if (path === "/api/presets/save") {
        const preset = { ...payload, revision: ++revision, id: payload.id || `generated-${revision}` };
        savedCatalog.presets = [...savedCatalog.presets.filter((item) => item.id !== preset.id), preset];
        return { ...clone(savedCatalog), preset };
      }
      if (path === "/api/presets/delete") {
        savedCatalog.presets = savedCatalog.presets.filter((item) => item.id !== payload.id);
        return clone(savedCatalog);
      }
      if (path === "/api/actions") return {};
      throw new Error(`Unexpected API ${path}`);
    },
  });
  vm.runInContext(source.slice(source.indexOf("  function finiteField("), source.indexOf("  function placePose(")), context);
  vm.runInContext(source.slice(source.indexOf("  function renderPresets("), source.indexOf("  function setMapMode(")), context);
  vm.runInContext(source.slice(source.indexOf("  async function navigate("), source.indexOf("  async function fineAlign(")), context);
  vm.runInContext(source.slice(source.indexOf('  byId("destination-select").addEventListener'),
    source.indexOf('  byId("task-shortcut-select").addEventListener')), context);
  context.applyDestinations(clone(initial));
  return { context, state, byId, calls };
}

async function checks() {
  const f = fixture(), { context: c, state: s, byId } = f;
  assert.equal(byId("preset-list").children[0].textContent, "Loading bay");
  assert.equal(byId("destination-select").children[1].textContent, "Loading bay");
  assert.equal(byId("destination-edit").disabled, true);
  byId("destination-select").value = "bay";
  byId("destination-select").listeners.change();
  assert.equal(byId("destination-edit").disabled, false);
  byId("destination-edit").listeners.click();
  assert.equal(s.destinationDraft.id, "bay");
  byId("destination-name").value = "<img src=x>";
  byId("destination-use-robot").listeners.click();
  assert.equal(byId("destination-x").value, "3");
  s.status.map_pose.fresh = false;
  c.copyDestinationPose("robot"); assert.match(c.error, /fresh robot pose/);
  s.status.map_pose.fresh = true; s.statusConnected = false;
  c.copyDestinationPose("robot"); assert.match(c.error, /fresh robot pose/);
  s.mapSelection = { kind: "initial_pose", x: 99, y: 99, yaw: 99 };
  c.copyDestinationPose("map"); assert.match(c.error, /navigation goal/);
  s.mapSelection = { kind: "navigate", x: -3, y: -4, yaw: -1 };
  byId("destination-use-map").listeners.click();
  assert.equal(byId("destination-y").value, "-4");
  await c.saveDestination({ preventDefault() {} });
  const save = f.calls.find((call) => call.path === "/api/presets/save");
  assert.deepEqual(save.payload, { id: "bay", label: "<img src=x>", revision: 1, pose: { x: -3, y: -4, yaw: -1 } });
  assert.equal(byId("preset-list").children[0].textContent, "<img src=x>");
  assert.ok(c.mapRendered && c.choicesRendered && c.shortcutsRendered);
  assert.equal(s.destinationDraft, null);
  assert.equal(f.calls.some((call) => call.path === "/api/actions"), false, "Editing must not navigate");

  byId("destination-duplicate").listeners.click();
  assert.equal(s.destinationDraft.id, undefined);
  assert.equal(s.destinationDraft.revision, undefined);
  byId("destination-x").value = "";
  await c.saveDestination({ preventDefault() {} });
  assert.match(c.error, /valid value/);
  byId("destination-x").value = "0";
  const api = c.api;
  c.api = async () => { throw new Error("Destination changed; refresh"); };
  await c.saveDestination({ preventDefault() {} });
  assert.match(c.error, /Destination changed/);
  assert.equal(byId("destination-editor").hidden, false);
  assert.equal(byId("destination-save").disabled, false);
  c.api = api;
  await c.saveDestination({ preventDefault() {} });
  assert.equal(f.calls.at(-1).payload.id, undefined);
  assert.equal(f.calls.at(-1).payload.revision, undefined);
  assert.equal(byId("destination-select").value, "generated-3");
  await c.deleteDestination();
  assert.equal(s.presets.length, 1);

  c.editDestination("new");
  byId("destination-name").value = "Third";
  let resolveRefresh;
  c.api = (path, options) => path === "/api/presets"
    ? new Promise((resolve) => { resolveRefresh = resolve; }) : api(path, options);
  const refresh = c.loadDestinations();
  await c.saveDestination({ preventDefault() {} });
  resolveRefresh(clone(initial)); await refresh;
  assert.equal(s.presets.length, 2, "A stale refresh must not replace a newer save");
  c.api = api;
  await c.navigate(s.presets[0]);
  assert.deepEqual(f.calls.at(-1).payload.expected_preset_pose, clone(s.presets[0].pose));

  c.applyDestinations({ available: false, presets: [], detail: "Storage unavailable" });
  assert.equal(byId("destination-new").disabled, true);
  assert.equal(byId("destination-delete").disabled, true);
  assert.equal(byId("destination-status").textContent, "Storage unavailable");
}
async function mutationRefreshChecks() {
  for (const action of ["save", "delete"]) {
    for (const refreshFirst of [false, true]) {
      for (const refreshFails of [false, true]) {
        const f = fixture(), { context: c, state: s, byId } = f;
        const added = { id: "new-bay", label: "New bay", revision: 2, pose: { x: 0, y: 0, yaw: 0 } };
        const updated = { available: true, presets: action === "save" ? [...clone(initial.presets), added] : [],
          ...(action === "save" ? { preset: added } : {}) };
        let finishMutation, finishRefresh, failRefresh;
        c.api = (path) => {
          if (path === "/api/actions") return Promise.reject(new Error("Nav2 unavailable"));
          return new Promise((resolve, reject) => {
            if (path === `/api/presets/${action}`) finishMutation = resolve;
            else if (path === "/api/presets") { finishRefresh = resolve; failRefresh = reject; }
            else reject(new Error(`Unexpected API ${path}`));
          });
        };
        byId("destination-select").value = "bay";
        if (action === "save") {
          c.editDestination("new");
          byId("destination-name").value = added.label;
        }
        const mutation = action === "save" ? c.saveDestination({ preventDefault() {} }) : c.deleteDestination();
        // A failed navigation can automatically refresh even with Refresh disabled.
        const navigation = c.navigate(clone(initial.presets[0]));
        await new Promise((resolve) => setImmediate(resolve));
        assert.equal(typeof finishRefresh, "function");
        const completeRefresh = () => refreshFails ? failRefresh(new Error("Refresh failed"))
          : finishRefresh(clone(initial));
        if (refreshFirst) {
          completeRefresh(); await navigation;
          assert.deepEqual(clone(s.presets), initial.presets, "Refreshes must not change the catalog during a write");
          assert.equal(s.destinationCatalog.available, true);
        }
        finishMutation(clone(updated)); await mutation;
        if (!refreshFirst) { completeRefresh(); await navigation; }
        assert.deepEqual(clone(s.presets), updated.presets, "A refresh during a write must not replace its result");
        assert.equal(s.destinationCatalog.available, true);
        assert.equal(byId("destination-new").disabled, false);
        assert.deepEqual(byId("preset-list").children.map((button) => button.textContent),
          updated.presets.map((preset) => preset.label));
        assert.equal(byId("destination-select").value, action === "save" ? added.id : "");
        c.api = async () => clone(initial);
        await c.loadDestinations();
        assert.deepEqual(clone(s.presets), initial.presets, "A fresh read after the write must still apply");
      }
    }
  }
}

(async () => { await checks(); await mutationRefreshChecks(); })()
  .catch((error) => { console.error(error); process.exitCode = 1; });
