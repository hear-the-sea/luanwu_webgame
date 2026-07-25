const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const template = fs.readFileSync(
  path.resolve(__dirname, "../../../gameplay/templates/gameplay/raid_config.html"),
  "utf8"
);
const styles = fs.readFileSync(path.resolve(__dirname, "../../../src/input.css"), "utf8");

global.document = {
  getElementById() {
    return null;
  },
};
const raidConfigPage = require("../raid-config-page.js");
delete global.document;

function createClassList() {
  const classes = new Set();
  return {
    toggle(name, force) {
      if (force === undefined ? !classes.has(name) : force) {
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

function createInteractiveElement({ checked = false, dataset = {}, value = "" } = {}) {
  return {
    checked,
    classList: createClassList(),
    dataset: { ...dataset },
    disabled: false,
    textContent: "",
    value,
    addEventListener(type, listener) {
      this[`on_${type}`] = listener;
    },
    setAttribute(name, nextValue) {
      this[name] = nextValue;
    },
  };
}

function createRaidHarness() {
  const guestInputs = [
    createInteractiveElement({ dataset: { troopCapacity: "120" }, value: "1" }),
    createInteractiveElement({ dataset: { troopCapacity: "180" }, value: "2" }),
    createInteractiveElement({ dataset: { troopCapacity: "300" }, value: "3" }),
  ];
  const troopInput = createInteractiveElement({
    dataset: { troopKey: "guard", previousValue: "0", max: "500" },
    value: "0",
  });
  troopInput.max = "500";

  const selectedCount = createInteractiveElement();
  const summaryGuests = createInteractiveElement();
  const summaryTroops = createInteractiveElement();
  const troopCapacity = createInteractiveElement();
  const capacityStatus = createInteractiveElement();
  const submitButton = createInteractiveElement();
  const selectMaxButton = createInteractiveElement();
  const clearGuestsButton = createInteractiveElement();
  const clearTroopsButton = createInteractiveElement();
  const fillTroopButton = createInteractiveElement({ dataset: { raidFill: "guard" } });

  const singleSelectors = new Map([
    ["[data-raid-selected-count]", selectedCount],
    ["[data-raid-summary-guests]", summaryGuests],
    ["[data-raid-summary-troops]", summaryTroops],
    ["[data-raid-troop-capacity]", troopCapacity],
    ["[data-raid-capacity-status]", capacityStatus],
    ["[data-raid-submit]", submitButton],
    ["[data-raid-select-max]", selectMaxButton],
    ["[data-raid-clear-guests]", clearGuestsButton],
    ["[data-raid-clear-troops]", clearTroopsButton],
  ]);
  const multiSelectors = new Map([
    ["[data-raid-guest]", guestInputs],
    ["[data-raid-troop]", [troopInput]],
    ["[data-raid-adjust]", []],
    ["[data-raid-fill]", [fillTroopButton]],
  ]);
  const root = createInteractiveElement({
    dataset: {
      mapUrl: "/manor/map/",
      maxSquadSize: "2",
      raidApiUrl: "/manor/api/map/raid/",
      targetId: "9",
    },
  });
  root.querySelector = (selector) => singleSelectors.get(selector) || null;
  root.querySelectorAll = (selector) => multiSelectors.get(selector) || [];

  const documentObj = {
    querySelector(selector) {
      return selector === "[data-raid-config-page]" ? root : null;
    },
  };

  return {
    capacityStatus,
    clearTroopsButton,
    documentObj,
    fillTroopButton,
    guestInputs,
    root,
    selectMaxButton,
    selectedCount,
    submitButton,
    summaryGuests,
    summaryTroops,
    troopCapacity,
    troopInput,
  };
}

test("raid config page exports testable state helpers", () => {
  assert.equal(typeof raidConfigPage.summarizeRaidConfig, "function");
  assert.equal(typeof raidConfigPage.resolveTroopValue, "function");
  assert.equal(typeof raidConfigPage.initRaidConfigPage, "function");
});

test("summarizeRaidConfig reports selected capacity and over-limit state", () => {
  assert.deepEqual(raidConfigPage.summarizeRaidConfig([120, 180], [200, 110]), {
    selectedGuests: 2,
    troopCapacity: 300,
    totalTroops: 310,
    isOverCapacity: true,
    canSubmit: false,
  });
});

test("resolveTroopValue caps increases but permits reductions after capacity shrinks", () => {
  assert.equal(
    raidConfigPage.resolveTroopValue({
      current: 40,
      requested: 100,
      inventoryMax: 500,
      otherTroops: 240,
      capacity: 300,
    }),
    60
  );
  assert.equal(
    raidConfigPage.resolveTroopValue({
      current: 100,
      requested: 90,
      inventoryMax: 500,
      otherTroops: 240,
      capacity: 300,
    }),
    90
  );
  assert.equal(
    raidConfigPage.resolveTroopValue({
      current: 100,
      requested: 110,
      inventoryMax: 500,
      otherTroops: 240,
      capacity: 300,
    }),
    100
  );
});

test("quick actions select the squad, fill remaining capacity, and clear troops", () => {
  const harness = createRaidHarness();

  raidConfigPage.initRaidConfigPage(harness.documentObj, {});
  harness.selectMaxButton.on_click();

  assert.deepEqual(
    harness.guestInputs.map((input) => input.checked),
    [true, true, false]
  );
  assert.equal(harness.selectedCount.textContent, "2");
  assert.equal(harness.summaryGuests.textContent, "2 人");
  assert.equal(harness.troopCapacity.textContent, "300");

  harness.fillTroopButton.on_click();
  assert.equal(harness.troopInput.value, "300");
  assert.equal(harness.summaryTroops.textContent, "300");
  assert.equal(harness.submitButton.disabled, false);

  harness.clearTroopsButton.on_click();
  assert.equal(harness.troopInput.value, "0");
  assert.equal(harness.summaryTroops.textContent, "0");
});

test("raid config submission exposes a typographic loading state", async () => {
  const harness = createRaidHarness();
  let resolveFetch;
  const fetchResult = new Promise((resolve) => {
    resolveFetch = resolve;
  });
  const runtime = {
    fetch() {
      return fetchResult;
    },
    gameDialog: {
      async error() {},
    },
  };

  raidConfigPage.initRaidConfigPage(harness.documentObj, runtime);
  harness.selectMaxButton.on_click();
  const submission = harness.root.on_submit({ preventDefault() {} });

  assert.equal(harness.submitButton.textContent, "发起进攻中…");

  resolveFetch({
    async json() {
      return { success: false, error: "测试失败" };
    },
  });
  await submission;
  assert.equal(harness.submitButton.textContent, "发起进攻");
});

test("raid config template keeps controls and images accessible and layout-stable", () => {
  assert.match(template, /<h2 class="tw-raid-eyebrow" id="raid-target-heading">目标庄园<\/h2>/);
  assert.match(template, /<img[^>]+width="44"[^>]+height="44"[^>]+alt="{{ guest\.display_name }}"/);
  assert.match(template, /<img[^>]+width="40"[^>]+height="40"[^>]+alt="{{ troop\.name }}"/);
  assert.match(template, /<input type="number"[\s\S]*?autocomplete="off"[\s\S]*?data-raid-troop/);
});

test("raid config motion scopes transitions and honors reduced-motion preferences", () => {
  assert.doesNotMatch(
    styles,
    /\.tw-raid-guest-card\s*\{[^}]*transition-all[^}]*\}/
  );
  assert.match(
    styles,
    /\.tw-raid-guest-card\s*\{[^}]*transition:\s*background-color[^;]+border-color[^;]+box-shadow[^;]+;/
  );
  assert.match(
    styles,
    /@media \(prefers-reduced-motion:\s*reduce\)[\s\S]*?\.tw-raid-guest-card[\s\S]*?\.tw-raid-guest-check[\s\S]*?transition:\s*none;/
  );
});

test("raid config layout uses page-scoped desktop and mobile rules", () => {
  assert.match(template, /class="tw-raid-loadout-grid"/);
  assert.match(template, /class="tw-panel tw-raid-summary-bar"/);
  assert.match(styles, /\.tw-raid-loadout-grid\s*\{/);
  assert.match(
    styles,
    /grid-template-columns:\s*minmax\(0,\s*1\.15fr\)\s+minmax\(0,\s*0\.85fr\)/
  );
  assert.match(styles, /@media \(max-width:\s*899px\)/);
  assert.match(styles, /@media \(max-width:\s*640px\)/);
});

test("mobile raid loadout keeps long lists bounded and troop actions clear", () => {
  assert.doesNotMatch(
    styles,
    /@media \(max-width:\s*899px\)\s*\{[\s\S]*?\.tw-raid-scroll-region\s*\{[\s\S]*?max-height:\s*none/
  );
  assert.match(
    styles,
    /\.tw-raid-troops-panel\s+\.tw-raid-panel-actions\s*\{\s*@apply\s+w-full\s+justify-start;\s*\}/
  );
});
