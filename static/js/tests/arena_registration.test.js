const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function createPanel(maxSelected, checkboxCount) {
  const checkboxes = Array.from({ length: checkboxCount }, () => {
    const listeners = new Map();
    return {
      checked: false,
      addEventListener(type, handler) {
        listeners.set(type, handler);
      },
      change() {
        return listeners.get("change")({ target: this });
      },
    };
  });

  return {
    dataset: { maxSelected: String(maxSelected) },
    checkboxes,
    querySelectorAll(selector) {
      assert.equal(selector, ".arena-guest-checkbox");
      return checkboxes;
    },
  };
}

function createHarness(panels) {
  const source = fs.readFileSync(path.resolve(__dirname, "..", "arena-registration.js"), "utf8");
  const calls = [];
  const document = {
    querySelectorAll(selector) {
      assert.equal(selector, ".tw-arena-signup-panel[data-max-selected]");
      return panels;
    },
  };
  const windowObject = {
    gameDialog: {
      error(message) {
        calls.push(message);
        return Promise.resolve();
      },
    },
  };

  vm.runInNewContext(source, { window: windowObject, document });

  return { calls };
}

test("coop registration enforces its own three-guest limit", async () => {
  const regularPanel = createPanel(10, 4);
  const coopPanel = createPanel(3, 4);
  const harness = createHarness([regularPanel, coopPanel]);

  regularPanel.checkboxes.forEach((checkbox) => {
    checkbox.checked = true;
  });
  await regularPanel.checkboxes[3].change();
  assert.deepEqual(harness.calls, []);

  coopPanel.checkboxes.forEach((checkbox) => {
    checkbox.checked = true;
  });
  await coopPanel.checkboxes[3].change();

  assert.deepEqual(harness.calls, ["最多只能选择 3 名门客"]);
  assert.equal(coopPanel.checkboxes[3].checked, false);
});
