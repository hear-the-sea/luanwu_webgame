const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

function readScript(relativePath) {
  return fs.readFileSync(path.resolve(__dirname, "..", relativePath), "utf8");
}

test("warehouse page disables pointer tracking for item tooltips", () => {
  const source = readScript("warehouse-page.js");

  assert.match(
    source,
    /initItemTooltip\(\{\s*key:\s*"warehouse"[\s\S]*trackPointer:\s*false[\s\S]*\}\)/
  );
});

test("trade page disables pointer tracking for item tooltips", () => {
  const source = readScript("trade.js");

  assert.match(
    source,
    /initItemTooltip\(\{\s*key:\s*"trade_market"[\s\S]*trackPointer:\s*false[\s\S]*\}\)/
  );
});
