const test = require("node:test");
const assert = require("node:assert/strict");

const playerFacingCopy = require("../player_facing_copy.js");

test("browserErrorMessage hides native English browser errors", () => {
  assert.equal(
    playerFacingCopy.browserErrorMessage(new Error("Failed to fetch"), "请求失败，请重试"),
    "请求失败，请重试"
  );
});

test("browserErrorMessage keeps Chinese business errors and player names", () => {
  assert.equal(
    playerFacingCopy.browserErrorMessage(new Error("蜡笔小新 当前状态异常"), "请求失败，请重试"),
    "蜡笔小新 当前状态异常"
  );
});

test("browserErrorMessage uses a Chinese default for empty errors", () => {
  assert.equal(playerFacingCopy.browserErrorMessage(null), "操作失败，请稍后重试");
});
