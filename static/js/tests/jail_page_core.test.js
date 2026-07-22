const test = require("node:test");
const assert = require("node:assert/strict");

const core = require("../jail_page_core.js");

test("classifySpeakerRatio uses gap-free design thresholds", () => {
  assert.equal(core.classifySpeakerRatio(0.6999).kind, "backfire");
  assert.equal(core.classifySpeakerRatio(0.7).kind, "failed");
  assert.equal(core.classifySpeakerRatio(0.8499).kind, "failed");
  assert.equal(core.classifySpeakerRatio(0.85).kind, "even");
  assert.equal(core.classifySpeakerRatio(1.15).kind, "advantage");
  assert.equal(core.classifySpeakerRatio(1.5).kind, "dominant");
});

test("buildSpeakerWarning explains deterministic failure and backfire", () => {
  const failed = core.buildSpeakerWarning("reason", 0.8, "年轻辩士");
  assert.equal(failed.requiresConfirmation, true);
  assert.match(failed.message, /无法奏效/);
  assert.match(failed.message, /今日次数/);

  const backfire = core.buildSpeakerWarning("might", 0.5, "新卒");
  assert.equal(backfire.requiresConfirmation, true);
  assert.match(backfire.message, /基础武力/);
  assert.match(backfire.message, /忠诚下降 1 点/);

  assert.equal(core.buildSpeakerWarning("reason", 1.0, "纵横客").requiresConfirmation, false);
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
    core.formatRecruitmentSummary({ initial_loyalty: 75 }),
    "已成为 1 级门客｜初始忠诚 75"
  );
  assert.equal(core.formatRecruitmentSummary({}), "");
});
