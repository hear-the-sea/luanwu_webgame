const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const template = fs.readFileSync(
  path.resolve(__dirname, "../../../gameplay/templates/gameplay/work.html"),
  "utf8"
);
const styles = fs.readFileSync(path.resolve(__dirname, "../../../src/input.css"), "utf8");

test("work location headings do not repeat the required level as a badge", () => {
  assert.doesNotMatch(template, /<span class="tw-level-badge">等级 \{\{ work\.required_level \}\}<\/span>/);
  assert.match(template, /requirement\.key == "level"/);
});

test("work requirements align with card content and use compact typography and spacing", () => {
  const match = template.match(/<div class="([^"]*)">\s*<span[^>]*>要求：<\/span>/);

  assert.ok(match, "missing work requirements row");
  const classes = new Set(match[1].split(/\s+/));

  assert.ok(classes.has("px-4"), "requirements should align with the card's horizontal padding");
  assert.ok(classes.has("text-xs"), "requirements should use the smaller supporting text size");
  assert.ok(classes.has("py-0.5"), "requirements should use minimal vertical padding");
  assert.ok(classes.has("mb-0.5"), "requirements should keep a minimal gap below");
});

test("work tier tabs use page-specific compact vertical spacing", () => {
  const header = template.match(/<div class="([^"]*tw-section-header[^"]*)">/);
  const tabs = template.match(/<nav class="([^"]*tw-troop-subtabs[^"]*)">/);

  assert.ok(header, "missing work section header");
  assert.ok(tabs, "missing work tier tabs");
  assert.ok(header[1].split(/\s+/).includes("tw-work-section-header"));

  const tabClasses = new Set(tabs[1].split(/\s+/));
  assert.ok(tabClasses.has("tw-work-subtabs"));
  assert.ok(tabClasses.has("mb-3"));
  assert.match(styles, /\.tw-work-section-header\s*\{[^}]*@apply\s+mb-3;/);
  assert.match(styles, /\.tw-work-subtabs\s*\{[^}]*@apply\s+py-1;/);
  assert.match(styles, /\.tw-work-subtabs\s+\.tw-troop-subtab\s*\{[^}]*@apply\s+py-1;/);
});

test("work cards show the tier action point cost", () => {
  assert.match(template, /<dt[^>]*>行动力<\/dt>/);
  assert.match(template, /\{\{ work\.action_point_cost \}\} 点/);
  assert.match(template, /sm:grid-cols-3/);
});
