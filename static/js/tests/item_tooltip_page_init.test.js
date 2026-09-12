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

test("mission drop icons provide reusable tooltip markup and load its controller", () => {
  const projectRoot = path.resolve(__dirname, "../../..");
  const dropTemplate = fs.readFileSync(
    path.join(projectRoot, "gameplay/templates/gameplay/includes/mission_drop_item.html"),
    "utf8"
  );
  const taskTemplate = fs.readFileSync(
    path.join(projectRoot, "gameplay/templates/gameplay/tasks.html"),
    "utf8"
  );

  assert.match(dropTemplate, /<button type="button" class="tw-drop-entry tw-item-cell"/);
  assert.match(dropTemplate, /<span class="tw-item-tooltip">/);
  assert.match(taskTemplate, /static 'js\/tooltip\.js'/);

  const css = fs.readFileSync(path.join(projectRoot, "static/css/tailwind.css"), "utf8");
  const dropEntryRule = css.match(/\.tw-drop-entry\s*\{([^}]*)\}/);
  assert.ok(dropEntryRule, "Missing styles for task drop buttons");
  assert.match(dropEntryRule[1], /appearance:\s*none\s*;/);
  assert.match(dropEntryRule[1], /border:\s*0\s*;/);
  assert.match(dropEntryRule[1], /padding:\s*0\s*;/);
  assert.match(dropEntryRule[1], /background:\s*transparent\s*;/);
  assert.match(dropEntryRule[1], /font:\s*inherit\s*;/);
});
