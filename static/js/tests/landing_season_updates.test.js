const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const source = fs.readFileSync(path.join(__dirname, "../landing-page.js"), "utf8");

async function runUpdate({ deadline, revision, updated }) {
  let replaced = false;
  let removed = false;
  let requests = 0;
  const row = {
    dataset: { seasonDeadline: deadline, seasonHomeEvent: "42", seasonRevision: revision },
    isConnected: true,
    contains: () => false,
    querySelector: () => null,
    replaceWith: () => { replaced = true; },
    remove: () => { removed = true; },
    parentElement: { querySelector: () => ({}) },
  };
  const document = {
    visibilityState: "visible", cookie: "", activeElement: null,
    querySelector: () => null,
    querySelectorAll: (selector) => selector === "[data-season-home-event]" ? [row] : [],
  };
  const window = {
    localStorage: { getItem: () => null }, location: { href: "https://example.test/" },
    setInterval: () => 1, setTimeout: () => 1, clearTimeout: () => {},
  };
  const context = {
    document, window, console, AbortController, Date,
    DOMParser: class { parseFromString() { return { querySelector: () => updated }; } },
    fetch: async () => { requests += 1; return { ok: true, redirected: false, text: async () => "" }; },
  };
  vm.runInNewContext(source, context);
  await new Promise((resolve) => setImmediate(resolve));
  return { requests, replaced, removed };
}

test("due season rows update when the server advances them", async () => {
  const result = await runUpdate({ deadline: "2020-01-01T00:00:00Z", revision: "old", updated: { dataset: { seasonRevision: "new" } } });
  assert.deepEqual(result, { requests: 1, replaced: true, removed: false });
});

test("a completed season row disappears and future rows do not poll", async () => {
  assert.deepEqual(await runUpdate({ deadline: "2020-01-01T00:00:00Z", revision: "old", updated: null }), { requests: 1, replaced: false, removed: true });
  assert.deepEqual(await runUpdate({ deadline: "2999-01-01T00:00:00Z", revision: "old", updated: null }), { requests: 0, replaced: false, removed: false });
});

test("an unchanged waiting row is left in place", async () => {
  const result = await runUpdate({ deadline: "2020-01-01T00:00:00Z", revision: "same", updated: { dataset: { seasonRevision: "same" } } });
  assert.deepEqual(result, { requests: 1, replaced: false, removed: false });
});
