const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(process.argv[2], "utf8");
const fragment = source.slice(source.indexOf("  function initializePanelLayout("),
  source.indexOf("  async function api("));

function fixture(stored = null, wide = true, storageBlocked = false) {
  const nodes = {}, classes = new Set(), mediaListeners = [], storageWrites = [];
  let document;
  class Element {
    constructor(id) {
      this.id = id; this.children = []; this.parentNode = null;
      this.hidden = false; this.attributes = {}; this.listeners = {};
    }
    get nextSibling() {
      return this.parentNode?.children[this.parentNode.children.indexOf(this) + 1] || null;
    }
    appendChild(child) { return this.insertBefore(child, null); }
    insertBefore(child, reference) {
      if (child.parentNode) child.parentNode.children.splice(child.parentNode.children.indexOf(child), 1);
      const index = reference === null ? this.children.length : this.children.indexOf(reference);
      assert.ok(index >= 0);
      this.children.splice(index, 0, child); child.parentNode = this;
      return child;
    }
    contains(child) { return child === this || this.children.some((node) => node.contains(child)); }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
    focus() { document.activeElement = this; }
    click() { for (const callback of this.listeners.click || []) callback(); }
  }
  const byId = (id) => nodes[id] ||= new Element(id);
  document = { activeElement: null, createComment: () => new Element(null) };
  const panel = byId("panel-view"), layout = byId("panel-layout");
  panel.classList = { toggle: (name, on) => on ? classes.add(name) : classes.delete(name) };
  panel.appendChild(byId("toggle-layout")); panel.appendChild(layout);
  const order = ["navigation-map", "robot-status", "camera-previews", "execution-controls",
    "panel-commands", "operation-history", "audit-history"];
  for (const id of [...order, "monitor-column", "controls-column"]) layout.appendChild(byId(id));
  byId("monitor-column").hidden = byId("controls-column").hidden = true;
  const input = byId("draft-input"), details = byId("open-details");
  input.value = "0.52"; details.open = true;
  byId("panel-commands").appendChild(input); byId("panel-commands").appendChild(details);
  let submissions = 0;
  byId("execution-controls").addEventListener("click", () => submissions++);
  const media = { matches: wide, addEventListener: (_, callback) => mediaListeners.push(callback) };
  const window = { matchMedia: (query) => {
    assert.equal(query, "(min-width: 981px)"); return media;
  }, localStorage: {
    getItem: () => { if (storageBlocked) throw new Error("Storage blocked"); return stored; },
    setItem: (key, value) => { if (storageBlocked) throw new Error("Storage blocked"); storageWrites.push([key, value]); },
  } };
  vm.runInNewContext(`${fragment}\ninitializePanelLayout();`, { window, document, byId });
  return { byId, classes, layout, order, input, details, storageWrites, document,
    submissions: () => submissions,
    resize: (wide) => { media.matches = wide; mediaListeners.forEach((callback) => callback()); } };
}

const ids = (node) => node.children.map((child) => child.id).filter(Boolean);
const f = fixture();
assert.equal(f.classes.has("split-layout"), false);
const originalOrder = ids(f.layout);
const originalInput = f.input;
f.input.focus();
f.byId("toggle-layout").click();
assert.equal(f.classes.has("split-layout"), true);
assert.deepEqual(ids(f.byId("monitor-column")), ["navigation-map", "robot-status", "camera-previews", "operation-history", "audit-history"]);
assert.deepEqual(ids(f.byId("controls-column")), ["execution-controls", "panel-commands"]);
assert.equal(f.byId("toggle-layout").attributes["aria-pressed"], "true");
assert.equal(f.document.activeElement, originalInput);
assert.equal(f.byId("draft-input"), originalInput);
assert.equal(f.input.value, "0.52");
assert.equal(f.details.open, true);
assert.equal(f.submissions(), 0, "Layout switching must not submit any command");
f.byId("execution-controls").click();
assert.equal(f.submissions(), 1);
f.byId("toggle-layout").click();
assert.deepEqual(ids(f.layout), originalOrder, "Restore exact classic section order");
assert.equal(f.byId("monitor-column").hidden, true);
assert.equal(f.input.value, "0.52");
for (let i = 0; i < 4; i++) f.byId("toggle-layout").click();
f.byId("execution-controls").click();
assert.equal(f.submissions(), 2, "Repeated switches must not duplicate action listeners");
assert.deepEqual(f.storageWrites[0], ["x2_operator_panel_layout", "split"]);
assert.deepEqual(f.storageWrites[1], ["x2_operator_panel_layout", "classic"]);

const restored = fixture("split");
assert.equal(restored.classes.has("split-layout"), true);
restored.resize(false);
assert.equal(restored.classes.has("split-layout"), false);
assert.deepEqual(ids(restored.layout), [...restored.order, "monitor-column", "controls-column"]);
assert.equal(restored.byId("toggle-layout").attributes["aria-pressed"], "true");
assert.equal(restored.byId("layout-hint").textContent, "Split layout on larger screens");
restored.resize(true);
assert.equal(restored.classes.has("split-layout"), true);
assert.equal(restored.storageWrites.length, 0, "Responsive fallback must preserve the saved preference");
const mobile = fixture("split", false);
assert.equal(mobile.classes.has("split-layout"), false);
mobile.resize(true);
assert.equal(mobile.classes.has("split-layout"), true);
assert.equal(fixture("invalid").classes.has("split-layout"), false);
const blocked = fixture("split", true, true);
assert.equal(blocked.classes.has("split-layout"), false);
blocked.byId("toggle-layout").click();
assert.equal(blocked.classes.has("split-layout"), true, "Blocked storage must not prevent switching");
