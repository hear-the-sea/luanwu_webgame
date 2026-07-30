const test = require("node:test");
const assert = require("node:assert/strict");

const { buildMissionCardConfirmation, buildTaskTabUrl } = require("../tasks_page_core.js");

test("buildTaskTabUrl records selected tab and clears selected mission", () => {
  assert.equal(
    buildTaskTabUrl("https://example.test/manor/tasks/?mission=wanxian&foo=1", "advanced"),
    "https://example.test/manor/tasks/?foo=1&tab=advanced"
  );
});

test("buildTaskTabUrl clears tab when no tab is selected", () => {
  assert.equal(
    buildTaskTabUrl("https://example.test/manor/tasks/?tab=intermediate", ""),
    "https://example.test/manor/tasks/"
  );
});

test("buildMissionCardConfirmation describes the cost, target, inventory, and daily usage", () => {
  assert.equal(
    buildMissionCardConfirmation({
      missionName: "春日部大作战",
      cardCount: "3",
      usedCount: "2",
      dailyLimit: "5",
    }),
    "确定消耗 1 张任务卡，为「春日部大作战」增加 1 次今日挑战次数吗？当前持有 3 张，该任务今日已使用 2 / 5 张。"
  );
});
