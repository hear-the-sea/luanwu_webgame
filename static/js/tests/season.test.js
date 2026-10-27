const assert = require("node:assert/strict");
const test = require("node:test");
const { alphaContains, hitNode, selectionError } = require("../season.js");

function mask(alpha) {
  return { width: alpha.length, height: 1, data: new Uint8ClampedArray(alpha.flatMap((value) => [0, 0, 0, value])) };
}

test("透明边缘不作为命中区域，建筑实心部分可以点击", () => {
  const pixels = mask([0, 255, 16, 250]);
  assert.equal(alphaContains(pixels, 0.1, 0.5), false);
  assert.equal(alphaContains(pixels, 0.3, 0.5), true);
  assert.equal(alphaContains(pixels, 0.6, 0.5), false);
  assert.equal(alphaContains(pixels, 0.9, 0.5), true);
  assert.equal(alphaContains(pixels, 1, 0.5), false);
  assert.equal(alphaContains(pixels, -0.01, 0.5), false);
});

test("上层建筑的透明留白允许命中下层建筑", () => {
  const lower = { node: { frame: { x: 10, y: 20, width: 100, height: 50 } }, mask: mask([255, 255]) };
  const upper = { node: { frame: { x: 10, y: 20, width: 100, height: 50 } }, mask: mask([0, 255]) };
  assert.equal(hitNode([lower, upper], 35, 30), lower);
  assert.equal(hitNode([lower, upper], 85, 30), upper);
  assert.equal(hitNode([lower, upper], 3, 30), null);
  assert.equal(hitNode([{ ...upper, mask: null }], 85, 30), null);
});

test("召唤必须选足候选数，阵容可以在上下限内选择", () => {
  assert.equal(selectionError(3, 3, 3), "");
  assert.match(selectionError(2, 3, 3), /3/);
  assert.equal(selectionError(1, 1, 3), "");
  assert.match(selectionError(0, 1, 3), /至少/);
  assert.match(selectionError(4, 1, 3), /最多/);
});
