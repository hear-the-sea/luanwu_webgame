const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const { initGuildPvpPage, summarizeGuildPvpConfig } = require("../guild-pvp-page.js");

function createClassList() {
  const classes = new Set();
  return {
    add(name) {
      classes.add(name);
    },
    remove(name) {
      classes.delete(name);
    },
    toggle(name, force) {
      if (force === undefined) {
        if (classes.has(name)) {
          classes.delete(name);
        } else {
          classes.add(name);
        }
        return;
      }
      if (force) {
        classes.add(name);
      } else {
        classes.delete(name);
      }
    },
    contains(name) {
      return classes.has(name);
    },
  };
}

function createRadio({ value, checked = false, disabled = false }) {
  return {
    value,
    checked,
    disabled,
    addEventListener(type, listener) {
      this[`on_${type}`] = listener;
    },
  };
}

function createTargetOption({
  id,
  status,
  region = "",
  search,
  checked = false,
  disabled = false,
  includeLegacyTargetStatus = true,
}) {
  const radio = createRadio({ value: id, checked, disabled });
  const dataset = {
    targetId: id,
    displayStatus: status,
    targetRegion: region,
    targetSearch: search,
  };
  if (includeLegacyTargetStatus) {
    dataset.targetStatus = status;
  }
  return {
    hidden: false,
    dataset,
    classList: createClassList(),
    querySelector(selector) {
      return selector === "[data-target-radio]" ? radio : null;
    },
    addEventListener(type, listener) {
      this[`on_${type}`] = listener;
    },
    radio,
  };
}

function createInput({ value = "", checked = false, disabled = false, dataset = {}, max = "" } = {}) {
  return {
    value,
    checked,
    disabled,
    dataset: { ...dataset },
    max,
    classList: createClassList(),
    addEventListener(type, listener) {
      this[`on_${type}`] = listener;
    },
  };
}

function createFilterButton(filter) {
  return {
    dataset: { targetFilter: filter },
    classList: createClassList(),
    addEventListener(type, listener) {
      this[`on_${type}`] = listener;
    },
  };
}

function createRoot({
  targetOptions,
  targetSearchValue = "",
  regionValue = "",
  guestOptions = [],
  troopInputs = [],
  filterButtons = [],
  baseTravelSeconds = "28800",
  marchFactor = "1",
  timeScale = "1",
}) {
  const targetSearch = createInput({ value: targetSearchValue });
  const targetRegionFilter = createInput({ value: regionValue });
  const targetEmpty = { hidden: true };
  const guestCountNode = { textContent: "" };
  const capacityStatusNode = { textContent: "", classList: createClassList() };
  const troopSummaryNode = { textContent: "" };
  const travelArrivalNode = { textContent: "" };
  const travelReturnNode = { textContent: "" };
  const submitButton = { disabled: false };

  return {
    dataset: {
      dispatchLimit: "2",
      defaultTargetId: targetOptions[0]?.dataset.targetId || "",
      pvpBaseSeconds: baseTravelSeconds,
      pvpMarchFactor: marchFactor,
      pvpTimeScale: timeScale,
    },
    querySelector(selector) {
      if (selector === "[data-target-search]") return targetSearch;
      if (selector === "[data-target-region-filter]") return targetRegionFilter;
      if (selector === "[data-target-empty]") return targetEmpty;
      if (selector === "[data-selected-guest-count]") return guestCountNode;
      if (selector === "[data-guild-capacity-status]") return capacityStatusNode;
      if (selector === "[data-guild-troop-summary]") return troopSummaryNode;
      if (selector === "[data-guild-travel-arrival]") return travelArrivalNode;
      if (selector === "[data-guild-travel-return]") return travelReturnNode;
      if (selector === "[data-launch-submit]") return submitButton;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === "[data-target-option]") return targetOptions;
      if (selector === "[data-target-radio]") return targetOptions.map((option) => option.radio);
      if (selector === "[data-guest-option]") return guestOptions;
      if (selector === "[data-troop-input]") return troopInputs;
      if (selector === "[data-target-filter]") return filterButtons;
      return [];
    },
    _submitButton: submitButton,
    _targetEmpty: targetEmpty,
    _guestCountNode: guestCountNode,
    _capacityStatusNode: capacityStatusNode,
    _troopSummaryNode: troopSummaryNode,
    _travelArrivalNode: travelArrivalNode,
    _travelReturnNode: travelReturnNode,
    _targetSearch: targetSearch,
  };
}

function createDocument(root) {
  return {
    querySelector(selector) {
      return selector === "[data-guild-pvp-page]" ? root : null;
    },
  };
}

test("falls back to the first visible enabled target when the current selection is filtered out", () => {
  const hiddenSelected = createTargetOption({
    id: "1",
    status: "attackable",
    search: "alpha",
    checked: true,
  });
  const visibleEnabled = createTargetOption({
    id: "2",
    status: "attackable",
    search: "beta",
  });
  const root = createRoot({
    targetOptions: [hiddenSelected, visibleEnabled],
    targetSearchValue: "beta",
    guestOptions: [createInput({ checked: true })],
  });

  initGuildPvpPage(createDocument(root));

  assert.equal(hiddenSelected.hidden, true);
  assert.equal(hiddenSelected.radio.checked, false);
  assert.equal(visibleEnabled.hidden, false);
  assert.equal(visibleEnabled.radio.checked, true);
  assert.equal(visibleEnabled.classList.contains("is-selected-target"), true);
  assert.equal(root._submitButton.disabled, false);
});

test("never selects a disabled target during fallback", () => {
  const hiddenSelected = createTargetOption({
    id: "1",
    status: "attackable",
    search: "alpha",
    checked: true,
  });
  const visibleDisabled = createTargetOption({
    id: "2",
    status: "blocked",
    search: "beta",
    disabled: true,
  });
  const root = createRoot({
    targetOptions: [hiddenSelected, visibleDisabled],
    targetSearchValue: "beta",
    guestOptions: [createInput({ checked: true })],
  });

  initGuildPvpPage(createDocument(root));

  assert.equal(hiddenSelected.hidden, true);
  assert.equal(hiddenSelected.radio.checked, false);
  assert.equal(visibleDisabled.hidden, false);
  assert.equal(visibleDisabled.radio.checked, false);
  assert.equal(visibleDisabled.classList.contains("is-selected-target"), false);
  assert.equal(root._submitButton.disabled, true);
});

test("works with projector rows that expose explicit data-display-status without reading text content", () => {
  const attackable = createTargetOption({
    id: "11",
    status: "attackable",
    search: "projected",
    includeLegacyTargetStatus: false,
  });
  const blocked = createTargetOption({
    id: "12",
    status: "blocked",
    search: "projected blocked",
    disabled: true,
    includeLegacyTargetStatus: false,
  });
  const attackableFilter = createFilterButton("attackable");
  const blockedFilter = createFilterButton("blocked");
  const root = createRoot({
    targetOptions: [attackable, blocked],
    guestOptions: [createInput({ checked: true })],
    filterButtons: [attackableFilter, blockedFilter],
  });

  initGuildPvpPage(createDocument(root));

  assert.equal(attackable.dataset.displayStatus, "attackable");
  assert.equal(attackable.radio.checked, true);
  assert.equal(blocked.hidden, false);

  blockedFilter.on_click();

  assert.equal(attackable.hidden, true);
  assert.equal(blocked.hidden, false);
  assert.equal(blocked.radio.checked, false);
  assert.equal(root._targetEmpty.hidden, true);
});

test("auto-initializes in browser-like environments when the script is loaded", () => {
  const scriptPath = path.resolve(__dirname, "..", "guild-pvp-page.js");
  const scriptSource = fs.readFileSync(scriptPath, "utf8");
  const hiddenSelected = createTargetOption({
    id: "31",
    status: "attackable",
    search: "alpha",
    checked: true,
  });
  const visibleEnabled = createTargetOption({
    id: "32",
    status: "attackable",
    search: "beta",
  });
  const root = createRoot({
    targetOptions: [hiddenSelected, visibleEnabled],
    targetSearchValue: "beta",
    guestOptions: [createInput({ checked: true })],
  });
  const browserDocument = createDocument(root);
  const context = {
    console,
    document: browserDocument,
  };

  vm.createContext(context);
  vm.runInContext(scriptSource, context, { filename: scriptPath });

  assert.equal(hiddenSelected.hidden, true);
  assert.equal(hiddenSelected.radio.checked, false);
  assert.equal(visibleEnabled.hidden, false);
  assert.equal(visibleEnabled.radio.checked, true);
  assert.equal(root._submitButton.disabled, false);
});

test("submit button stays disabled when no guest is selected", () => {
  const attackable = createTargetOption({
    id: "21",
    status: "attackable",
    search: "solo",
    checked: true,
  });
  const root = createRoot({
    targetOptions: [attackable],
    guestOptions: [createInput({ checked: false })],
  });

  initGuildPvpPage(createDocument(root));

  assert.equal(attackable.radio.checked, true);
  assert.equal(root._submitButton.disabled, true);
  assert.equal(root._guestCountNode.textContent, "0");
});

test("guild PVP summary enforces capacity and previews one-way and return time", () => {
  const attackable = createTargetOption({
    id: "41",
    status: "attackable",
    search: "capacity",
    checked: true,
  });
  const guest = createInput({
    checked: true,
    dataset: { agility: "160", troopCapacity: "200" },
  });
  const troop = createInput({
    value: "200",
    max: "201",
    dataset: { troopKey: "guard" },
  });
  const root = createRoot({
    targetOptions: [attackable],
    guestOptions: [guest],
    troopInputs: [troop],
  });

  const page = initGuildPvpPage(createDocument(root));

  assert.deepEqual(page.readConfigurationState(), {
    selectedGuests: 1,
    troopCapacity: 200,
    totalTroops: 200,
    isOverCapacity: false,
  });
  assert.equal(root._troopSummaryNode.textContent, "200 / 200");
  assert.equal(root._travelArrivalNode.textContent, "8小时12分钟");
  assert.equal(root._travelReturnNode.textContent, "16小时24分钟");
  assert.equal(root._submitButton.disabled, false);

  troop.value = "201";
  troop.on_input();

  assert.equal(root._capacityStatusNode.textContent, "已超出带兵上限 1 人。");
  assert.equal(root._capacityStatusNode.classList.contains("is-over-limit"), true);
  assert.equal(root._submitButton.disabled, true);
});

test("guild PVP exports a capacity summary helper", () => {
  assert.deepEqual(summarizeGuildPvpConfig([200, 250], [451]), {
    selectedGuests: 2,
    troopCapacity: 450,
    totalTroops: 451,
    isOverCapacity: true,
  });
});
