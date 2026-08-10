const test = require("node:test");
const assert = require("node:assert/strict");

const recruitmentHallCore = require("../recruitment_hall_core.js");

test("shouldUseChunkedRender only enables chunk mode above the threshold", () => {
  assert.equal(recruitmentHallCore.shouldUseChunkedRender(160, 160), false);
  assert.equal(recruitmentHallCore.shouldUseChunkedRender(161, 160), true);
});

test("buildRenderProgressText reports chunked and full render states", () => {
  assert.equal(
    recruitmentHallCore.buildRenderProgressText({
      total: 200,
      rendered: 96,
      selected: 3,
      chunked: true,
    }),
    "已加载 96/200，已勾选 3"
  );
  assert.equal(
    recruitmentHallCore.buildRenderProgressText({
      total: 12,
      rendered: 12,
      selected: 2,
      chunked: false,
    }),
    "共 12 名候选，已勾选 2"
  );
  assert.equal(
    recruitmentHallCore.buildRenderProgressText({
      total: 0,
      rendered: 0,
      selected: 0,
      chunked: false,
    }),
    ""
  );
});

test("normalizeSelectedIds deduplicates and sorts positive candidate ids", () => {
  assert.deepEqual(
    recruitmentHallCore.normalizeSelectedIds(["3", 2, "bad", 3, 0, -1, "08"]),
    [2, 3, 8]
  );
});

test("buildRecruitmentCardConfirmation explains the target pool and card stock", () => {
  assert.equal(
    recruitmentHallCore.buildRecruitmentCardConfirmation({
      poolName: "殿试",
      cardCount: "4",
    }),
    "确定消耗 1 张招募卡，为「殿试」增加 1 次今日招募额度吗？\n当前持有：4 张"
  );
});
