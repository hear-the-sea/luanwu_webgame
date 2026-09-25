const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function element({ dataset = {}, value = "", checked = false } = {}) {
  const classes = new Set();
  return {
    dataset: { ...dataset },
    value,
    checked,
    disabled: false,
    textContent: "",
    classList: {
      toggle(name, active) {
        if (active) classes.add(name);
        else classes.delete(name);
      },
      contains(name) {
        return classes.has(name);
      },
    },
    addEventListener(type, handler) {
      this[`on_${type}`] = handler;
    },
    setAttribute(name, value) {
      this[name] = value;
    },
  };
}

function createLoadoutHarness() {
  const source = fs.readFileSync(path.resolve(__dirname, "..", "tasks-page.js"), "utf8");
  const alerts = [];
  const guestInputs = [100, 150, 200].map((capacity) => element({
    dataset: { troopCapacity: String(capacity) },
  }));
  const troopInput = element({ dataset: { troopKey: "guard", max: "500" }, value: "0" });
  const selectedCount = element();
  const troopTotal = element();
  const troopCapacity = element();
  const capacityStatus = element();
  const submitButton = element();
  const selectAllButton = element();
  const clearGuestsButton = element();
  const clearTroopsButton = element();
  const fillButton = element({ dataset: { taskFill: "guard" } });
  const plusTenButton = element({ dataset: { taskAdjust: "10", troopKey: "guard" } });
  const minusTenButton = element({ dataset: { taskAdjust: "-10", troopKey: "guard" } });
  const singleSelectors = new Map([
    ["#selected-guest-count", selectedCount],
    ["[data-task-troop-total]", troopTotal],
    ["[data-task-troop-capacity]", troopCapacity],
    ["[data-task-capacity-status]", capacityStatus],
    ["[data-task-submit]", submitButton],
    ["[data-task-select-all]", selectAllButton],
    ["[data-task-clear-guests]", clearGuestsButton],
    ["[data-task-clear-troops]", clearTroopsButton],
  ]);
  const multiSelectors = new Map([
    ["[data-task-guest]", guestInputs],
    ["[data-task-troop]", [troopInput]],
    ["[data-task-adjust]", [plusTenButton, minusTenButton]],
    ["[data-task-fill]", [fillButton]],
  ]);
  const form = element({ dataset: { maxSquadSize: "2" } });
  form.querySelector = (selector) => singleSelectors.get(selector) || null;
  form.querySelectorAll = (selector) => multiSelectors.get(selector) || [];

  const documentListeners = new Map();
  const document = {
    addEventListener(type, handler) {
      documentListeners.set(type, handler);
    },
    querySelector(selector) {
      if (selector === ".tw-mission-tabs") return {};
      if (selector === "[data-task-loadout]") return form;
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
  const window = {
    gameAlert(message) {
      alerts.push(message);
    },
  };
  vm.runInNewContext(source, { document, window }, { filename: "tasks-page.js" });
  documentListeners.get("DOMContentLoaded")();

  return {
    alerts,
    capacityStatus,
    clearGuestsButton,
    clearTroopsButton,
    fillButton,
    form,
    guestInputs,
    minusTenButton,
    plusTenButton,
    selectAllButton,
    selectedCount,
    submitButton,
    troopCapacity,
    troopInput,
    troopTotal,
  };
}

test("mission quick selection respects the squad cap and updates troop capacity", () => {
  const harness = createLoadoutHarness();
  assert.equal(harness.submitButton.disabled, true);

  harness.selectAllButton.on_click();
  assert.deepEqual(harness.guestInputs.map((input) => input.checked), [true, true, false]);
  assert.equal(harness.selectedCount.textContent, "2");
  assert.equal(harness.troopCapacity.textContent, "250");
  assert.equal(harness.submitButton.disabled, false);

  harness.guestInputs[2].checked = true;
  harness.guestInputs[2].on_change();
  assert.equal(harness.guestInputs[2].checked, false);
  assert.equal(harness.alerts.length, 1);

  harness.clearGuestsButton.on_click();
  assert.equal(harness.selectedCount.textContent, "0");
  assert.equal(harness.submitButton.disabled, true);
  assert.equal(harness.capacityStatus.textContent, "可先配置护院；选择门客后计算带兵上限");
});

test("mission troop buttons respond before selecting guests and flag over-capacity loadouts", () => {
  const harness = createLoadoutHarness();
  harness.plusTenButton.on_click();
  assert.equal(harness.troopInput.value, "10");
  assert.equal(harness.troopTotal.textContent, "10");
  assert.equal(harness.capacityStatus.textContent, "已携带护院，请选择门客或全部清零");
  assert.equal(harness.submitButton.disabled, true);

  harness.fillButton.on_click();
  assert.equal(harness.troopInput.value, "500");
  assert.equal(harness.troopTotal.textContent, "500");

  harness.selectAllButton.on_click();
  assert.equal(harness.troopCapacity.textContent, "250");
  assert.equal(harness.capacityStatus.textContent, "已超出带兵上限 250 人");
  assert.equal(harness.submitButton.disabled, true);

  harness.plusTenButton.on_click();
  assert.equal(harness.troopInput.value, "500");

  harness.troopInput.value = "200";
  harness.troopInput.on_input();
  assert.equal(harness.submitButton.disabled, false);
  harness.troopInput.on_change();
  assert.equal(harness.troopInput.value, "200");
  assert.equal(harness.submitButton.disabled, false);

  harness.guestInputs[1].checked = false;
  harness.guestInputs[1].on_change();
  assert.equal(harness.capacityStatus.classList.contains("is-over-limit"), true);
  assert.equal(harness.capacityStatus.textContent, "已超出带兵上限 100 人");
  assert.equal(harness.submitButton.disabled, true);

  harness.minusTenButton.on_click();
  assert.equal(harness.troopInput.value, "190");
  harness.clearTroopsButton.on_click();
  assert.equal(harness.troopInput.value, "0");
  assert.equal(harness.submitButton.disabled, false);
});
