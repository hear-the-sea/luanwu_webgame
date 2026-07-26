const test = require("node:test");
const assert = require("node:assert/strict");

const pvpTravel = require("../pvp-travel.js");

test("agility uses 160 baseline with two percent per ten points and bounded factors", () => {
  assert.equal(pvpTravel.calculateAgilityFactor(60), 1.2);
  assert.equal(pvpTravel.calculateAgilityFactor(160), 1);
  assert.equal(pvpTravel.calculateAgilityFactor(310), 0.7);
  assert.equal(pvpTravel.calculateAgilityFactor(-9999), 1.2);
  assert.equal(pvpTravel.calculateAgilityFactor(9999), 0.7);
});

test("size modifier is continuous and treats every troop as one person", () => {
  assert.deepEqual(pvpTravel.calculateSizeFactor(1, 0), {
    sizeScore: 0,
    sizeFactor: 1,
  });

  const estimate = pvpTravel.calculateSizeFactor(2, 200);
  assert.equal(estimate.sizeScore, 2);
  assert.ok(Math.abs(estimate.sizeFactor - (1 + (0.5 * 2) / 22)) < 1e-12);
});

test("travel rounds game time up to a minute before scaling and has no final cap", () => {
  const rounded = pvpTravel.calculatePvpTravelTime({
    routeSeconds: 61,
    guestAgilities: [160],
    troopCounts: [],
    timeScale: 10,
  });
  assert.equal(rounded.gameSeconds, 120);
  assert.equal(rounded.scaledSeconds, 12);

  const uncapped = pvpTravel.calculatePvpTravelTime({
    routeSeconds: 100000,
    guestAgilities: [60],
    troopCounts: [2000000],
  });
  assert.ok(uncapped.scaledSeconds > 8 * 60 * 60);
});

test("duration formatting supports long one-way and return estimates", () => {
  assert.equal(pvpTravel.formatDuration(29520), "8小时12分钟");
  assert.equal(pvpTravel.formatDuration(59040), "16小时24分钟");
});
