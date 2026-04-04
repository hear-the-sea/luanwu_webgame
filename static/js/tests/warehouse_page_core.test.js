const test = require("node:test");
const assert = require("node:assert/strict");

const warehousePageCore = require("../warehouse_page_core.js");

test("computeStackedItemCount normalizes invalid quantities", () => {
  assert.equal(
    warehousePageCore.computeStackedItemCount([
      { quantity: "3" },
      { quantity: "bad" },
      { quantity: 0 },
      { quantity: "-2" },
    ]),
    3
  );
});

test("buildWarehouseFilterState normalizes soul fusion defaults", () => {
  assert.deepEqual(warehousePageCore.buildWarehouseFilterState({}), {
    minLevel: 30,
    allowedRarities: ["green", "blue", "purple"],
  });
  assert.deepEqual(
    warehousePageCore.buildWarehouseFilterState({
      soulFusionMinLevel: "45",
      soulFusionRarities: "green, blue ,purple",
    }),
    {
      minLevel: 45,
      allowedRarities: ["green", "blue", "purple"],
    }
  );
});

test("formatSoulFusionRequirementHint uses normalized filter state", () => {
  const filterState = warehousePageCore.buildWarehouseFilterState({
    soulFusionMinLevel: "40",
    soulFusionRarities: "green,purple",
  });

  assert.equal(
    warehousePageCore.formatSoulFusionRequirementHint(filterState, {
      green: "绿色",
      purple: "紫色",
    }),
    "当前容器要求：40级以上，且为绿色 / 紫色门客"
  );
});

test("shouldGuestMatchWarehouseFilter applies level and rarity gates", () => {
  const filterState = warehousePageCore.buildWarehouseFilterState({
    soulFusionMinLevel: "35",
    soulFusionRarities: "blue,purple",
  });

  assert.equal(
    warehousePageCore.shouldGuestMatchWarehouseFilter({ level: "50", rarity: "blue" }, filterState),
    true
  );
  assert.equal(
    warehousePageCore.shouldGuestMatchWarehouseFilter({ level: "20", rarity: "blue" }, filterState),
    false
  );
  assert.equal(
    warehousePageCore.shouldGuestMatchWarehouseFilter({ level: "50", rarity: "green" }, filterState),
    false
  );
});
