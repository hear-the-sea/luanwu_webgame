const test = require("node:test");
const assert = require("node:assert/strict");

const core = require("../jail_page_core.js");

test("speaker outcome classifications are not exposed to the page", () => {
  assert.equal(core.classifySpeakerRatio, undefined);
  assert.equal(core.buildSpeakerWarning, undefined);
});

test("formatDeltaSummary omits zero speaker change and keeps signs", () => {
  assert.equal(
    core.formatDeltaSummary({ heart_delta: -6, affinity_delta: 10, speaker_loyalty_delta: 0 }),
    "心防 -6｜归心 +10"
  );
  assert.equal(
    core.formatDeltaSummary({ heart_delta: 2, affinity_delta: -4, speaker_loyalty_delta: -1 }, "生涩辩士"),
    "心防 +2｜归心 -4｜生涩辩士 忠诚 -1"
  );
});

test("buildInteractionPayload includes speaker only for speaker methods", () => {
  assert.deepEqual(core.buildInteractionPayload("kindness", "12"), { method: "kindness" });
  assert.deepEqual(core.buildInteractionPayload("reason", "12"), { method: "reason", speaker_id: 12 });
  assert.deepEqual(core.buildInteractionPayload("might", ""), { method: "might" });
});

test("formatRecruitmentSummary reports the generated level and loyalty", () => {
  assert.equal(
    core.formatRecruitmentSummary({ recruited: true, initial_loyalty: 75 }),
    "已成为 1 级门客｜初始忠诚 75"
  );
  assert.equal(core.formatRecruitmentSummary({ recruited: false, initial_loyalty: 75 }), "");
  assert.equal(core.formatRecruitmentSummary({ recruited: false, initial_loyalty: null }), "");
  assert.equal(core.formatRecruitmentSummary({}), "");
});

test("formatHistoryEntries prepares readable text for the shared dialog", () => {
  assert.equal(
    core.formatHistoryEntries(["第一次交涉  心防 -6", "第二次交涉\n归心 +10"]),
    "1. 第一次交涉 心防 -6\n\n2. 第二次交涉 归心 +10"
  );
  assert.equal(core.formatHistoryEntries([]), "尚无招降记录。");
});
