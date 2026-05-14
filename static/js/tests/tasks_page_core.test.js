const test = require("node:test");
const assert = require("node:assert/strict");

const { buildTaskTabUrl } = require("../tasks_page_core.js");

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
