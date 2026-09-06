const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

function cssRuleBody(css, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, "m"));
  assert.ok(match, `Missing CSS rule for ${selector}`);
  return match[1];
}

function assertDeclaration(ruleBody, declaration) {
  assert.match(ruleBody, new RegExp(`${declaration}\\s*;`));
}

test("guest detail equipment chips keep equipped gear on one line", () => {
  const css = fs.readFileSync(path.join(__dirname, "../../css/guest-detail.css"), "utf8");
  const chipRule = cssRuleBody(css, ".guest-detail .equip-chip");

  assertDeclaration(chipRule, "gap:\\s*0");
  assertDeclaration(chipRule, "white-space:\\s*nowrap");
  assertDeclaration(chipRule, "overflow:\\s*hidden");
  assertDeclaration(chipRule, "text-overflow:\\s*ellipsis");
});

test("guest detail equipment slot titles stay on one line", () => {
  const css = fs.readFileSync(path.join(__dirname, "../../css/guest-detail.css"), "utf8");
  const titleRule = cssRuleBody(css, ".guest-detail .equip-line-title");

  assertDeclaration(titleRule, "white-space:\\s*nowrap");
  assertDeclaration(titleRule, "flex:\\s*0\\s+0\\s+auto");
});

test("guest detail equipment rows keep title and item spacing compact", () => {
  const css = fs.readFileSync(path.join(__dirname, "../../css/guest-detail.css"), "utf8");
  const lineRule = cssRuleBody(css, ".guest-detail .equip-line");

  assertDeclaration(lineRule, "gap:\\s*0");
  assertDeclaration(lineRule, "padding:\\s*0");
});

test("guest detail equipment tooltip wraps long set content inside the viewport", () => {
  const css = fs.readFileSync(path.join(__dirname, "../../css/guest-detail.css"), "utf8");
  const tooltipRule = cssRuleBody(css, ".guest-detail .guest-equip-tooltip-bubble");

  assertDeclaration(tooltipRule, "box-sizing:\\s*border-box");
  assertDeclaration(tooltipRule, "white-space:\\s*normal");
  assertDeclaration(tooltipRule, "overflow-wrap:\\s*anywhere");
  assert.match(tooltipRule, /min-width:\s*min\(240px,\s*calc\(100vw\s*-\s*40px\)\)\s*;/);
  assert.match(tooltipRule, /max-width:\s*min\(320px,\s*calc\(100vw\s*-\s*40px\)\)\s*;/);
});

test("guest detail stat labels stay on one line", () => {
  const css = fs.readFileSync(path.join(__dirname, "../../css/guest-detail.css"), "utf8");
  const labelRule = cssRuleBody(css, ".guest-detail .stat-row > span:first-child");

  assertDeclaration(labelRule, "flex:\\s*0\\s+0\\s+auto");
  assertDeclaration(labelRule, "white-space:\\s*nowrap");
});

test("guest detail attribute rows align label and value to the left", () => {
  const css = fs.readFileSync(path.join(__dirname, "../../css/guest-detail.css"), "utf8");
  const attributeRule = cssRuleBody(css, ".guest-detail .stat-row:has(.guest-attribute-tooltip-trigger)");

  assertDeclaration(attributeRule, "justify-content:\\s*flex-start");
  assertDeclaration(attributeRule, "gap:\\s*0\\.85rem");
});

test("guild guest equipment entries stay borderless", () => {
  const css = fs.readFileSync(path.join(__dirname, "../../css/guild-hero-pool.css"), "utf8");
  const lineRule = cssRuleBody(css, ".ghp-guest-detail .ghp-detail-equipment-card .equip-line");
  const chipRule = cssRuleBody(css, ".ghp-guest-detail .ghp-detail-equipment-card .equip-chip");

  assertDeclaration(lineRule, "border:\\s*0");
  assertDeclaration(chipRule, "border:\\s*0");
  assertDeclaration(chipRule, "background:\\s*transparent");
});

test("guild guest equipment collapses to one contained column on mobile", () => {
  const css = fs.readFileSync(path.join(__dirname, "../../css/guild-hero-pool.css"), "utf8");

  assert.match(
    css,
    /@media\s*\(max-width:\s*640px\)[\s\S]*?\.ghp-guest-detail \.ghp-detail-equipment-card \.equip-lines\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0,\s*1fr\)\s*;/,
  );
  assert.match(
    css,
    /@media\s*\(max-width:\s*640px\)[\s\S]*?\.ghp-guest-detail \.ghp-detail-equipment-card \.equip-chip\s*\{[\s\S]*?text-overflow:\s*ellipsis\s*;/,
  );
});
