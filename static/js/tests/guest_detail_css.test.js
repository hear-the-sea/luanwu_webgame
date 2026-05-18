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
